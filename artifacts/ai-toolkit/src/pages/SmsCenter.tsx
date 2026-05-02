import { useState, useEffect, useCallback, useRef } from "react";

// ── Types ──────────────────────────────────────────────────────────────────
interface PhoneNumber {
  id: number | string;
  number: string;
  source: "jiemahao" | "smsreceivefree";
}

interface SmsMessage {
  info: string;
  body: string;
}

interface MessagesResult {
  phoneNumber?: string;
  messages?: SmsMessage[];
  count?: number;
  error?: string;
}

// ── Helpers ────────────────────────────────────────────────────────────────
function extractCode(text: string): string {
  const m = text.match(/\b(\d{4,8})\b/);
  return m ? m[1] : "";
}

function sendDesktopNotification(title: string, body: string) {
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification(title, { body, icon: "/favicon.ico" });
  }
}

// ── Source config ──────────────────────────────────────────────────────────
const SOURCES = [
  {
    id: "jiemahao" as const,
    label: "jiemahao.com",
    desc: "83 个美国号码 · pydoll CF bypass",
    badge: "稳定",
    badgeColor: "emerald",
  },
  {
    id: "smsreceivefree" as const,
    label: "smsreceivefree.xyz",
    desc: "直接输入号码查看 · stealth Chrome",
    badge: "实验",
    badgeColor: "amber",
  },
];

// ── Sub-components ─────────────────────────────────────────────────────────

function MessagePane({
  phone,
  messages,
  loading,
  newCount,
  lastRefresh,
  autoRefresh,
  refreshInterval,
  notifPerm,
  onRefresh,
  onToggleAuto,
  onIntervalChange,
  onRequestNotif,
}: {
  phone: PhoneNumber | null;
  messages: MessagesResult | null;
  loading: boolean;
  newCount: number;
  lastRefresh: string;
  autoRefresh: boolean;
  refreshInterval: number;
  notifPerm: NotificationPermission;
  onRefresh: () => void;
  onToggleAuto: () => void;
  onIntervalChange: (v: number) => void;
  onRequestNotif: () => void;
}) {
  if (!phone) {
    return (
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl flex items-center justify-center" style={{ minHeight: 340 }}>
        <div className="text-center px-8">
          <div className="text-5xl mb-4">📨</div>
          <p className="text-gray-300 text-sm font-semibold">从左侧选择或输入号码</p>
          <p className="text-gray-600 text-[10px] mt-2 leading-relaxed">
            点击号码后自动读取短信<br />
            首次约需 30~60 秒（绕过 Cloudflare）
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d] flex-wrap gap-2">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-sm font-mono font-bold text-white">🇺🇸 {phone.number}</span>
            {newCount > 0 && (
              <span className="text-[10px] bg-emerald-500/20 border border-emerald-500/40 text-emerald-400 px-1.5 py-0.5 rounded-full animate-pulse">
                +{newCount} 新
              </span>
            )}
          </div>
          {lastRefresh && <div className="text-[10px] text-gray-600 mt-0.5">更新于 {lastRefresh}</div>}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {notifPerm !== "granted" && (
            <button onClick={onRequestNotif}
              className="text-[10px] px-2 py-1 bg-amber-500/20 border border-amber-500/30 rounded text-amber-400 hover:bg-amber-500/30">
              🔔 开启通知
            </button>
          )}
          <label className="flex items-center gap-1.5 cursor-pointer select-none">
            <div onClick={onToggleAuto}
              className={`w-8 h-4 rounded-full relative transition-colors cursor-pointer ${autoRefresh ? "bg-blue-600" : "bg-gray-700"}`}>
              <div className={`w-3 h-3 bg-white rounded-full absolute top-0.5 transition-all ${autoRefresh ? "left-4" : "left-0.5"}`} />
            </div>
            <span className="text-[10px] text-gray-400">自动刷新</span>
          </label>
          <select value={refreshInterval} onChange={e => onIntervalChange(Number(e.target.value))}
            className="text-[10px] bg-[#0d1117] border border-[#21262d] rounded px-1.5 py-0.5 text-gray-400 cursor-pointer">
            <option value={15}>15s</option>
            <option value={30}>30s</option>
            <option value={60}>60s</option>
            <option value={120}>2min</option>
          </select>
          <button onClick={onRefresh} disabled={loading}
            className="text-[10px] px-3 py-1.5 bg-blue-600/20 border border-blue-500/30 rounded-lg text-blue-400 hover:bg-blue-600/30 disabled:opacity-40 transition-all">
            {loading ? "读取中..." : "刷新"}
          </button>
        </div>
      </div>

      {/* Progress bar */}
      {autoRefresh && !loading && (
        <div className="h-0.5 bg-[#21262d]">
          <div key={lastRefresh} className="h-full bg-blue-500/50"
            style={{ animation: `sms-prog ${refreshInterval}s linear` }} />
        </div>
      )}

      {/* Body */}
      <div className="max-h-[500px] overflow-y-auto p-3 space-y-2">
        {loading ? (
          <div className="py-14 text-center">
            <div className="text-4xl mb-4">⏳</div>
            <p className="text-gray-300 text-sm font-medium">绕过 Cloudflare 中...</p>
            <p className="text-gray-600 text-[10px] mt-1">约需 30~60 秒</p>
            <div className="mt-5 flex justify-center gap-1.5">
              {[0,1,2,3].map(i => (
                <div key={i} className="w-2 h-2 bg-blue-500/70 rounded-full animate-bounce"
                  style={{ animationDelay: `${i * 0.12}s` }} />
              ))}
            </div>
          </div>
        ) : messages?.error ? (
          <div className="py-10 text-center">
            <div className="text-2xl mb-2">⚠️</div>
            <p className="text-red-400 text-xs font-mono break-all px-4">{messages.error}</p>
          </div>
        ) : !messages ? null
        : (messages.messages?.length ?? 0) === 0 ? (
          <div className="py-10 text-center">
            <div className="text-2xl mb-2">📭</div>
            <p className="text-gray-500 text-xs">该号码暂无短信</p>
          </div>
        ) : (
          messages.messages?.map((msg, i) => {
            const code = extractCode(msg.body);
            const isNew = i < newCount;
            return (
              <div key={i}
                className={`border rounded-lg p-3 space-y-1.5 transition-all ${
                  isNew ? "bg-emerald-500/5 border-emerald-500/30" : "bg-[#0d1117] border-[#21262d] hover:border-[#30363d]"
                }`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    {isNew && <span className="inline-block text-[9px] bg-emerald-500/20 text-emerald-400 px-1.5 py-0.5 rounded-full mb-1">NEW</span>}
                    {msg.info && <div className="text-[10px] text-gray-500 font-mono mb-1">{msg.info}</div>}
                    <p className="text-xs text-gray-200 leading-relaxed break-words">{msg.body}</p>
                  </div>
                  {code && (
                    <button onClick={() => navigator.clipboard.writeText(code)}
                      className="shrink-0 font-mono text-sm font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 px-2.5 py-1 rounded-lg hover:bg-emerald-500/20 active:scale-95 transition-all"
                      title="点击复制">
                      {code}
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {messages && !loading && (messages.messages?.length ?? 0) > 0 && (
        <div className="px-4 py-2 border-t border-[#21262d] flex justify-between items-center">
          <span className="text-[10px] text-gray-600">共 {messages.count} 条</span>
          {autoRefresh && <span className="text-[10px] text-blue-400/50">每 {refreshInterval}s 刷新</span>}
        </div>
      )}
    </div>
  );
}

// ── Jiemahao Source ────────────────────────────────────────────────────────
function JiemahaoSource() {
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [loadingNums, setLoadingNums] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<PhoneNumber | null>(null);
  const [messages, setMessages] = useState<MessagesResult | null>(null);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [interval_, setInterval_] = useState(30);
  const [lastRefresh, setLastRefresh] = useState("");
  const [newCount, setNewCount] = useState(0);
  const [notifPerm, setNotifPerm] = useState<NotificationPermission>(
    "Notification" in window ? Notification.permission : "denied"
  );
  const prevBodies = useRef<Set<string>>(new Set());
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadNumbers = useCallback(async () => {
    setLoadingNums(true);
    const r = await fetch("/api/tools/sms/numbers?country=us").then(r => r.json()).catch(() => []);
    setNumbers(Array.isArray(r) ? r.map((n: { id: number; number: string }) => ({ id: n.id, number: n.number, source: "jiemahao" as const })) : []);
    setLoadingNums(false);
  }, []);

  useEffect(() => { loadNumbers(); }, [loadNumbers]);

  const fetchMsgs = useCallback(async (phone: PhoneNumber, polling = false) => {
    if (!polling) { setLoadingMsgs(true); setMessages(null); setNewCount(0); prevBodies.current = new Set(); }
    try {
      const r = await fetch("/api/tools/sms/messages", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phoneId: Number(phone.id) }),
      }).then(r => r.json()) as MessagesResult;

      if (polling && r.messages) {
        const incoming = r.messages.filter(m => !prevBodies.current.has(m.body));
        if (incoming.length) {
          setNewCount(n => n + incoming.length);
          incoming.forEach(m => sendDesktopNotification(`📱 新短信 ${phone.number}`, m.body.slice(0, 100)));
        }
        prevBodies.current = new Set(r.messages.map(m => m.body));
      } else if (r.messages) {
        prevBodies.current = new Set(r.messages.map(m => m.body));
      }

      setMessages(r);
      setLastRefresh(new Date().toLocaleTimeString());
    } catch (e) {
      if (!polling) setMessages({ error: String(e), messages: [] });
    }
    if (!polling) setLoadingMsgs(false);
  }, []);

  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (autoRefresh && selected) {
      timerRef.current = setInterval(() => fetchMsgs(selected, true), interval_ * 1000);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [autoRefresh, selected, interval_, fetchMsgs]);

  const select = (p: PhoneNumber) => {
    setSelected(p); setNewCount(0); prevBodies.current = new Set(); fetchMsgs(p, false);
  };

  const filtered = numbers.filter(n =>
    !search || n.number.replace(/\D/g, "").includes(search.replace(/\D/g, ""))
  );

  return (
    <div className="grid grid-cols-12 gap-4">
      <div className="col-span-5 space-y-2">
        <div className="flex gap-2">
          <input type="text" value={search} onChange={e => setSearch(e.target.value)}
            placeholder="搜索号码..."
            className="flex-1 bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-1.5 text-xs font-mono text-gray-300 focus:outline-none focus:border-blue-500/50" />
          <button onClick={loadNumbers} disabled={loadingNums}
            className="text-xs px-2.5 py-1.5 bg-[#21262d] border border-[#30363d] rounded-lg text-gray-400 hover:text-white disabled:opacity-40">↻</button>
        </div>
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
          <div className="px-3 py-2 border-b border-[#21262d] text-[10px] text-gray-500">
            {loadingNums ? "加载..." : `${filtered.length} / ${numbers.length} 个号码`}
          </div>
          <div className="max-h-[540px] overflow-y-auto">
            {loadingNums ? (
              <div className="py-8 text-center text-gray-600 text-xs">加载中...</div>
            ) : filtered.length === 0 ? (
              <div className="py-8 text-center text-gray-600 text-xs">无匹配</div>
            ) : filtered.map(p => (
              <button key={p.id} onClick={() => select(p)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 hover:bg-[#21262d] border-b border-[#21262d]/40 transition-all text-left ${
                  selected?.id === p.id ? "bg-blue-600/10 border-l-2 border-l-blue-500" : ""
                }`}>
                <span className="text-base">🇺🇸</span>
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-mono text-white">{p.number}</div>
                  <div className="text-[10px] text-gray-600">+1 美国</div>
                </div>
                {selected?.id === p.id && <span className="text-[10px] text-blue-400">●</span>}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="col-span-7">
        <MessagePane
          phone={selected}
          messages={messages}
          loading={loadingMsgs}
          newCount={newCount}
          lastRefresh={lastRefresh}
          autoRefresh={autoRefresh}
          refreshInterval={interval_}
          notifPerm={notifPerm}
          onRefresh={() => selected && fetchMsgs(selected, false)}
          onToggleAuto={() => setAutoRefresh(v => !v)}
          onIntervalChange={setInterval_}
          onRequestNotif={async () => {
            const p = await Notification.requestPermission();
            setNotifPerm(p);
          }}
        />
      </div>
    </div>
  );
}

// ── SMSReceiveFree Source ──────────────────────────────────────────────────
function SMSReceiveFreeSource() {
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [loadingNums, setLoadingNums] = useState(false);
  const [customInput, setCustomInput] = useState("");
  const [selected, setSelected] = useState<PhoneNumber | null>(null);
  const [messages, setMessages] = useState<MessagesResult | null>(null);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [interval_, setInterval_] = useState(30);
  const [lastRefresh, setLastRefresh] = useState("");
  const [newCount, setNewCount] = useState(0);
  const [notifPerm, setNotifPerm] = useState<NotificationPermission>(
    "Notification" in window ? Notification.permission : "denied"
  );
  const prevBodies = useRef<Set<string>>(new Set());
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Hardcoded known numbers (including the user's requested one)
  const KNOWN: PhoneNumber[] = [
    { id: "5183535766", number: "+1 5183535766", source: "smsreceivefree" },
    { id: "3397875789", number: "+1 3397875789", source: "smsreceivefree" },
    { id: "9196070835", number: "+1 9196070835", source: "smsreceivefree" },
    { id: "2121215067", number: "+1 2121215067", source: "smsreceivefree" },
    { id: "5050469487", number: "+1 5050469487", source: "smsreceivefree" },
    { id: "7080562963", number: "+1 7080562963", source: "smsreceivefree" },
    { id: "5072589488", number: "+1 5072589488", source: "smsreceivefree" },
    { id: "3143489466", number: "+1 3143489466", source: "smsreceivefree" },
    { id: "8049245234", number: "+1 8049245234", source: "smsreceivefree" },
  ];

  const loadNumbers = useCallback(async () => {
    setLoadingNums(true);
    try {
      const r = await fetch("/api/tools/smsrf/numbers").then(r => r.json()).catch(() => []);
      if (Array.isArray(r) && r.length > 0) {
        const scraped: PhoneNumber[] = r.map((n: { id: string | number; number: string }) => ({
          id: String(n.id), number: n.number, source: "smsreceivefree" as const
        }));
        // Merge with known, dedup
        const seen = new Set<string>(scraped.map(n => String(n.id)));
        const extras = KNOWN.filter(k => !seen.has(String(k.id)));
        setNumbers([...scraped, ...extras]);
      } else {
        setNumbers(KNOWN);
      }
    } catch {
      setNumbers(KNOWN);
    }
    setLoadingNums(false);
  }, []);

  useEffect(() => { loadNumbers(); }, [loadNumbers]);

  const fetchMsgs = useCallback(async (phone: PhoneNumber, polling = false) => {
    if (!polling) { setLoadingMsgs(true); setMessages(null); setNewCount(0); prevBodies.current = new Set(); }
    try {
      const raw = String(phone.id).replace(/\D/g, "");
      const r = await fetch("/api/tools/smsrf/messages", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone: raw }),
      }).then(r => r.json()) as MessagesResult;

      if (polling && r.messages) {
        const incoming = r.messages.filter(m => !prevBodies.current.has(m.body));
        if (incoming.length) {
          setNewCount(n => n + incoming.length);
          incoming.forEach(m => sendDesktopNotification(`📱 新短信 ${phone.number}`, m.body.slice(0, 100)));
        }
        prevBodies.current = new Set(r.messages.map(m => m.body));
      } else if (r.messages) {
        prevBodies.current = new Set(r.messages.map(m => m.body));
      }

      setMessages(r);
      setLastRefresh(new Date().toLocaleTimeString());
    } catch (e) {
      if (!polling) setMessages({ error: String(e), messages: [] });
    }
    if (!polling) setLoadingMsgs(false);
  }, []);

  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (autoRefresh && selected) {
      timerRef.current = setInterval(() => fetchMsgs(selected, true), interval_ * 1000);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [autoRefresh, selected, interval_, fetchMsgs]);

  const addCustomNumber = () => {
    const raw = customInput.replace(/\D/g, "");
    if (raw.length < 10) return;
    const digits = raw.slice(-10);
    if (numbers.find(n => String(n.id) === digits)) return;
    const newPhone: PhoneNumber = { id: digits, number: "+1 " + digits, source: "smsreceivefree" };
    setNumbers(prev => [newPhone, ...prev]);
    setCustomInput("");
  };

  const select = (p: PhoneNumber) => {
    setSelected(p); setNewCount(0); prevBodies.current = new Set(); fetchMsgs(p, false);
  };

  return (
    <div className="grid grid-cols-12 gap-4">
      <div className="col-span-5 space-y-2">
        {/* Custom number input */}
        <div className="bg-[#161b22] border border-[#21262d] rounded-lg px-3 py-2 space-y-1.5">
          <div className="text-[10px] text-gray-500">输入美国号码直接查短信</div>
          <div className="flex gap-2">
            <input type="text" value={customInput} onChange={e => setCustomInput(e.target.value)}
              onKeyDown={e => e.key === "Enter" && addCustomNumber()}
              placeholder="5183535766"
              className="flex-1 bg-[#0d1117] border border-[#21262d] rounded px-2 py-1 text-xs font-mono text-gray-300 focus:outline-none focus:border-blue-500/50" />
            <button onClick={addCustomNumber}
              className="text-[10px] px-2.5 py-1 bg-blue-600/20 border border-blue-500/30 rounded text-blue-400 hover:bg-blue-600/30 shrink-0">
              查看
            </button>
          </div>
        </div>

        <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
          <div className="px-3 py-2 border-b border-[#21262d] flex items-center justify-between">
            <span className="text-[10px] text-gray-500">{loadingNums ? "加载..." : `${numbers.length} 个号码`}</span>
            <button onClick={loadNumbers} disabled={loadingNums}
              className="text-[10px] text-blue-400/60 hover:text-blue-400">↻ 刷新列表</button>
          </div>
          <div className="max-h-[500px] overflow-y-auto">
            {numbers.map(p => (
              <button key={String(p.id)} onClick={() => select(p)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 hover:bg-[#21262d] border-b border-[#21262d]/40 transition-all text-left ${
                  selected?.id === p.id ? "bg-blue-600/10 border-l-2 border-l-blue-500" : ""
                }`}>
                <span className="text-base">🇺🇸</span>
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-mono text-white">{p.number}</div>
                  <div className="text-[10px] text-gray-600">smsreceivefree.xyz</div>
                </div>
                {selected?.id === p.id && <span className="text-[10px] text-blue-400">●</span>}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="col-span-7">
        <MessagePane
          phone={selected}
          messages={messages}
          loading={loadingMsgs}
          newCount={newCount}
          lastRefresh={lastRefresh}
          autoRefresh={autoRefresh}
          refreshInterval={interval_}
          notifPerm={notifPerm}
          onRefresh={() => selected && fetchMsgs(selected, false)}
          onToggleAuto={() => setAutoRefresh(v => !v)}
          onIntervalChange={setInterval_}
          onRequestNotif={async () => {
            const p = await Notification.requestPermission();
            setNotifPerm(p);
          }}
        />
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────
export default function SmsCenter() {
  const [source, setSource] = useState<"jiemahao" | "smsreceivefree">("jiemahao");

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
        <h2 className="text-sm font-bold text-white mb-3">📱 短信接收中心</h2>

        {/* Source tabs */}
        <div className="flex gap-2 flex-wrap">
          {SOURCES.map(s => (
            <button key={s.id} onClick={() => setSource(s.id)}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-all text-left ${
                source === s.id
                  ? "bg-blue-600/15 border-blue-500/40 text-blue-300"
                  : "bg-[#0d1117] border-[#21262d] text-gray-400 hover:border-[#30363d] hover:text-gray-200"
              }`}>
              <div>
                <div className="text-xs font-semibold">{s.label}</div>
                <div className="text-[10px] text-gray-500">{s.desc}</div>
              </div>
              <span className={`text-[9px] px-1.5 py-0.5 rounded-full border shrink-0 ${
                s.badgeColor === "emerald"
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                  : "bg-amber-500/10 border-amber-500/30 text-amber-400"
              }`}>{s.badge}</span>
            </button>
          ))}
        </div>
      </div>

      {source === "jiemahao" ? <JiemahaoSource /> : <SMSReceiveFreeSource />}

      <style>{`
        @keyframes sms-prog {
          from { width: 100%; }
          to   { width: 0%; }
        }
      `}</style>
    </div>
  );
}
