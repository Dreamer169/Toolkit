import { useState, useEffect, useCallback, useRef } from "react";

interface PhoneNumber {
  id: number;
  number: string;
}

interface SmsMessage {
  info: string;
  body: string;
}

interface MessagesResult {
  phoneNumber?: string;
  phoneId?: number;
  messages?: SmsMessage[];
  count?: number;
  error?: string;
}

function extractCode(text: string): string {
  const m = text.match(/\b(\d{4,8})\b/);
  return m ? m[1] : "";
}

function sendNotification(title: string, body: string) {
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification(title, { body, icon: "/favicon.ico" });
  }
}

export default function SmsCenter() {
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [numbersLoading, setNumbersLoading] = useState(false);
  const [selectedPhone, setSelectedPhone] = useState<PhoneNumber | null>(null);
  const [messages, setMessages] = useState<MessagesResult | null>(null);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [refreshInterval, setRefreshInterval] = useState(30);
  const [lastRefresh, setLastRefresh] = useState<string>("");
  const [newCount, setNewCount] = useState(0);
  const [notifPerm, setNotifPerm] = useState<NotificationPermission>(
    "Notification" in window ? Notification.permission : "denied"
  );
  const prevBodiesRef = useRef<Set<string>>(new Set());
  const autoRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadNumbers = useCallback(async () => {
    setNumbersLoading(true);
    try {
      const r = await fetch("/api/tools/sms/numbers?country=us");
      const data = await r.json() as { id: number; number: string }[] | { error: string };
      if (Array.isArray(data)) setNumbers(data);
      else setNumbers([]);
    } catch {
      setNumbers([]);
    }
    setNumbersLoading(false);
  }, []);

  useEffect(() => { loadNumbers(); }, [loadNumbers]);

  const fetchMessages = useCallback(async (phone: PhoneNumber, isPolling = false) => {
    if (!isPolling) {
      setMessagesLoading(true);
      setMessages(null);
      setNewCount(0);
      prevBodiesRef.current = new Set();
    }
    try {
      const r = await fetch("/api/tools/sms/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phoneId: phone.id }),
      });
      const data = await r.json() as MessagesResult;

      if (isPolling && data.messages) {
        const prev = prevBodiesRef.current;
        const incoming = data.messages.filter(m => !prev.has(m.body));
        if (incoming.length > 0) {
          setNewCount(n => n + incoming.length);
          for (const msg of incoming) {
            const code = extractCode(msg.body);
            sendNotification(
              `📱 新短信 — ${phone.number}`,
              code ? `验证码: ${code}\n${msg.body.slice(0, 80)}` : msg.body.slice(0, 100)
            );
          }
        }
        prevBodiesRef.current = new Set(data.messages.map(m => m.body));
      } else if (data.messages) {
        prevBodiesRef.current = new Set(data.messages.map(m => m.body));
      }

      setMessages(data);
      setLastRefresh(new Date().toLocaleTimeString());
    } catch (e) {
      if (!isPolling) setMessages({ error: String(e), messages: [] });
    }
    if (!isPolling) setMessagesLoading(false);
  }, []);

  // Auto-refresh polling
  useEffect(() => {
    if (autoRefreshRef.current) { clearInterval(autoRefreshRef.current); autoRefreshRef.current = null; }
    if (autoRefresh && selectedPhone) {
      autoRefreshRef.current = setInterval(
        () => fetchMessages(selectedPhone, true),
        refreshInterval * 1000
      );
    }
    return () => { if (autoRefreshRef.current) clearInterval(autoRefreshRef.current); };
  }, [autoRefresh, selectedPhone, refreshInterval, fetchMessages]);

  const requestPerms = async () => {
    if ("Notification" in window) {
      const p = await Notification.requestPermission();
      setNotifPerm(p);
    }
  };

  const selectPhone = (phone: PhoneNumber) => {
    setSelectedPhone(phone);
    setNewCount(0);
    prevBodiesRef.current = new Set();
    fetchMessages(phone, false);
  };

  const filtered = numbers.filter(n =>
    !search || n.number.replace(/\D/g, "").includes(search.replace(/\D/g, ""))
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <h2 className="text-sm font-bold text-white">🇺🇸 美国短信接收中心</h2>
            <p className="text-[10px] text-gray-500 mt-0.5">
              来源：jiemahao.com — {numbers.length} 个美国临时手机号 · pydoll 自动绕过 CF Turnstile
            </p>
          </div>
          <div className="flex items-center gap-2">
            {notifPerm !== "granted" ? (
              <button onClick={requestPerms}
                className="text-[10px] px-2 py-1 bg-amber-500/20 border border-amber-500/30 rounded text-amber-400 hover:bg-amber-500/30 transition-all">
                🔔 开启桌面通知
              </button>
            ) : (
              <span className="text-[10px] text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 rounded-full">
                🔔 通知已开启
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-4">
        {/* ── Left: Number List ── */}
        <div className="col-span-5 space-y-3">
          <div className="flex gap-2">
            <input type="text" value={search} onChange={e => setSearch(e.target.value)}
              placeholder="搜索号码... 如 3023165706"
              className="flex-1 bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-1.5 text-xs font-mono text-gray-300 focus:outline-none focus:border-blue-500/50" />
            <button onClick={loadNumbers} disabled={numbersLoading}
              className="text-[10px] px-2.5 py-1.5 bg-[#21262d] border border-[#30363d] rounded-lg text-gray-400 hover:text-gray-200 disabled:opacity-40 shrink-0">
              ↻
            </button>
          </div>

          <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
            <div className="px-3 py-2 border-b border-[#21262d] flex items-center justify-between">
              <span className="text-[10px] text-gray-500">
                {numbersLoading ? "加载中..." : `${filtered.length} / ${numbers.length} 个号码`}
              </span>
              <span className="text-[10px] text-gray-600">点击号码查看短信</span>
            </div>
            <div className="max-h-[560px] overflow-y-auto">
              {numbersLoading ? (
                <div className="py-10 text-center text-gray-600 text-xs">加载号码列表...</div>
              ) : filtered.length === 0 ? (
                <div className="py-10 text-center text-gray-600 text-xs">无匹配号码</div>
              ) : (
                filtered.map(phone => (
                  <button key={phone.id} onClick={() => selectPhone(phone)}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 hover:bg-[#21262d] border-b border-[#21262d]/40 transition-all text-left ${
                      selectedPhone?.id === phone.id
                        ? "bg-blue-600/10 border-l-2 border-l-blue-500"
                        : ""
                    }`}>
                    <span className="text-base shrink-0">🇺🇸</span>
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-mono text-white">{phone.number}</div>
                      <div className="text-[10px] text-gray-600">+1 美国</div>
                    </div>
                    {selectedPhone?.id === phone.id && (
                      <span className="text-[10px] text-blue-400 shrink-0">●</span>
                    )}
                  </button>
                ))
              )}
            </div>
          </div>
        </div>

        {/* ── Right: SMS Messages ── */}
        <div className="col-span-7 space-y-3">
          {!selectedPhone ? (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl flex items-center justify-center" style={{ minHeight: 320 }}>
              <div className="text-center px-8">
                <div className="text-5xl mb-4">📨</div>
                <p className="text-gray-400 text-sm font-semibold">从左侧选择号码查看短信</p>
                <p className="text-gray-600 text-[10px] mt-2 leading-relaxed">
                  所有 83 个美国号码均可查看<br />
                  首次读取约需 30~60 秒（绕过 Turnstile）<br />
                  开启自动刷新后新短信自动推送桌面通知
                </p>
              </div>
            </div>
          ) : (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
              {/* Header */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d] flex-wrap gap-2">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-mono font-bold text-white">
                      🇺🇸 {selectedPhone.number}
                    </span>
                    {newCount > 0 && (
                      <span className="text-[10px] bg-emerald-500/20 border border-emerald-500/40 text-emerald-400 px-1.5 py-0.5 rounded-full animate-pulse">
                        +{newCount} 新消息
                      </span>
                    )}
                  </div>
                  {lastRefresh && (
                    <div className="text-[10px] text-gray-600 mt-0.5">更新于 {lastRefresh}</div>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <label className="flex items-center gap-1.5 cursor-pointer select-none">
                    <div onClick={() => setAutoRefresh(v => !v)}
                      className={`w-8 h-4 rounded-full relative transition-colors cursor-pointer ${autoRefresh ? "bg-blue-600" : "bg-gray-700"}`}>
                      <div className={`w-3 h-3 bg-white rounded-full absolute top-0.5 transition-all ${autoRefresh ? "left-4" : "left-0.5"}`} />
                    </div>
                    <span className="text-[10px] text-gray-400">自动刷新</span>
                  </label>
                  <select value={refreshInterval}
                    onChange={e => setRefreshInterval(Number(e.target.value))}
                    className="text-[10px] bg-[#0d1117] border border-[#21262d] rounded px-1.5 py-0.5 text-gray-400 cursor-pointer">
                    <option value={15}>15s</option>
                    <option value={30}>30s</option>
                    <option value={60}>60s</option>
                    <option value={120}>2min</option>
                  </select>
                  <button onClick={() => fetchMessages(selectedPhone, false)}
                    disabled={messagesLoading}
                    className="text-[10px] px-3 py-1.5 bg-blue-600/20 border border-blue-500/30 rounded-lg text-blue-400 hover:bg-blue-600/30 disabled:opacity-40 transition-all font-medium">
                    {messagesLoading ? "读取中..." : "刷新短信"}
                  </button>
                </div>
              </div>

              {/* Progress bar for auto-refresh */}
              {autoRefresh && !messagesLoading && (
                <div className="h-0.5 bg-[#21262d]">
                  <div key={lastRefresh}
                    className="h-full bg-blue-500/60"
                    style={{ animation: `sms-progress ${refreshInterval}s linear` }} />
                </div>
              )}

              {/* Messages */}
              <div className="max-h-[500px] overflow-y-auto p-3 space-y-2">
                {messagesLoading ? (
                  <div className="py-14 text-center">
                    <div className="text-4xl mb-4">⏳</div>
                    <p className="text-gray-300 text-sm font-medium">正在绕过 Cloudflare Turnstile</p>
                    <p className="text-gray-600 text-[10px] mt-1">约需 30~60 秒，请耐心等待</p>
                    <div className="mt-5 flex justify-center gap-1.5">
                      {[0,1,2,3].map(i => (
                        <div key={i} className="w-2 h-2 bg-blue-500/70 rounded-full animate-bounce"
                          style={{ animationDelay: `${i * 0.12}s` }} />
                      ))}
                    </div>
                  </div>
                ) : messages?.error ? (
                  <div className="py-10 text-center">
                    <div className="text-3xl mb-2">⚠️</div>
                    <p className="text-red-400 text-xs font-mono break-all px-4">{messages.error}</p>
                  </div>
                ) : !messages ? null
                : (messages.messages?.length ?? 0) === 0 ? (
                  <div className="py-10 text-center">
                    <div className="text-3xl mb-2">📭</div>
                    <p className="text-gray-500 text-xs">该号码暂无短信</p>
                  </div>
                ) : (
                  messages.messages?.map((msg, i) => {
                    const code = extractCode(msg.body);
                    const isNew = i < newCount;
                    return (
                      <div key={i}
                        className={`border rounded-lg p-3 space-y-2 transition-all ${
                          isNew
                            ? "bg-emerald-500/5 border-emerald-500/30"
                            : "bg-[#0d1117] border-[#21262d] hover:border-[#30363d]"
                        }`}>
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex-1 min-w-0">
                            {isNew && (
                              <span className="inline-block text-[9px] bg-emerald-500/20 text-emerald-400 px-1.5 py-0.5 rounded-full mb-1">NEW</span>
                            )}
                            {msg.info && (
                              <div className="text-[10px] text-gray-500 font-mono mb-1">{msg.info}</div>
                            )}
                            <p className="text-xs text-gray-200 leading-relaxed break-words">{msg.body}</p>
                          </div>
                          {code && (
                            <button
                              onClick={() => navigator.clipboard.writeText(code)}
                              className="shrink-0 font-mono text-sm font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 px-2.5 py-1 rounded-lg hover:bg-emerald-500/20 active:scale-95 transition-all"
                              title="点击复制验证码">
                              {code}
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })
                )}
              </div>

              {messages && !messagesLoading && (messages.messages?.length ?? 0) > 0 && (
                <div className="px-4 py-2 border-t border-[#21262d] flex items-center justify-between">
                  <span className="text-[10px] text-gray-600">共 {messages.count} 条短信</span>
                  {autoRefresh && (
                    <span className="text-[10px] text-blue-400/50">每 {refreshInterval}s 自动刷新</span>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <style>{`
        @keyframes sms-progress {
          from { width: 100%; }
          to   { width: 0%; }
        }
      `}</style>
    </div>
  );
}
