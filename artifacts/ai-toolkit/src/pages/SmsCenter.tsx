import { useState, useEffect, useCallback, useRef } from "react";

interface PhoneNumber { id: string; number: string; }
interface SmsMessage  { info: string; body: string; }
interface MessagesResult {
  phoneNumber?: string;
  messages?: SmsMessage[];
  count?: number;
  cached?: boolean;
  error?: string;
  message?: string;
}

function extractCode(text: string) { const m = text.match(/\b(\d{4,8})\b/); return m ? m[1] : ""; }
function notify(title: string, body: string) {
  if ("Notification" in window && Notification.permission === "granted")
    new Notification(title, { body, icon: "/favicon.ico" });
}

const KNOWN_NUMBERS: PhoneNumber[] = [
  { id: "7437695823", number: "+1 7437695823" },
  { id: "5183535766", number: "+1 5183535766" },
  { id: "3397875789", number: "+1 3397875789" },
  { id: "8053479366", number: "+1 8053479366" },
  { id: "7739934816", number: "+1 7739934816" },
  { id: "4157773804", number: "+1 4157773804" },
  { id: "9258986521", number: "+1 9258986521" },
  { id: "6467093288", number: "+1 6467093288" },
  { id: "2134933093", number: "+1 2134933093" },
  { id: "3238120560", number: "+1 3238120560" },
  { id: "4702464466", number: "+1 4702464466" },
  { id: "3106000251", number: "+1 3106000251" },
  { id: "5593218207", number: "+1 5593218207" },
  { id: "7608607580", number: "+1 7608607580" },
  { id: "3524337128", number: "+1 3524337128" },
];

// ── Force-refresh countdown bar ──────────────────────────────────────────
function ForceProgress({ onDone }: { onDone: () => void }) {
  const [elapsed, setElapsed] = useState(0);
  const TOTAL = 75; // estimated seconds

  useEffect(() => {
    const t = setInterval(() => setElapsed(e => {
      if (e >= TOTAL) { clearInterval(t); return TOTAL; }
      return e + 1;
    }), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => { if (elapsed >= TOTAL) onDone(); }, [elapsed, onDone]);

  const pct = Math.min((elapsed / TOTAL) * 100, 100);
  const remaining = Math.max(TOTAL - elapsed, 0);

  const phases = [
    { label: "启动 Chrome", from: 0, to: 15 },
    { label: "绕过 Cloudflare", from: 15, to: 50 },
    { label: "加载短信页面", from: 50, to: 65 },
    { label: "提取消息", from: 65, to: 75 },
  ];
  const currentPhase = phases.find(p => elapsed >= p.from && elapsed < p.to)
    ?? phases[phases.length - 1];

  return (
    <div className="py-10 px-6 text-center space-y-5">
      {/* Spinner + icon */}
      <div className="relative inline-block">
        <div className="w-16 h-16 rounded-full border-2 border-blue-500/20 border-t-blue-500 animate-spin" />
        <div className="absolute inset-0 flex items-center justify-center text-2xl">🔄</div>
      </div>

      {/* Phase label */}
      <div>
        <p className="text-gray-200 text-sm font-semibold">{currentPhase.label}…</p>
        <p className="text-gray-500 text-[11px] mt-1">
          约还需 <span className="text-blue-400 font-mono">{remaining}s</span> · 强制刷新跳过缓存
        </p>
      </div>

      {/* Progress bar */}
      <div className="w-full bg-[#21262d] rounded-full h-1.5 overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-blue-600 to-blue-400 rounded-full transition-all duration-1000"
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Phase steps */}
      <div className="flex justify-between text-[9px] text-gray-700">
        {phases.map((p, i) => (
          <span key={i} className={elapsed >= p.from ? "text-blue-400/70" : ""}>
            {p.label}
          </span>
        ))}
      </div>

      {/* Bouncing dots */}
      <div className="flex justify-center gap-1.5">
        {[0, 1, 2, 3].map(i => (
          <div key={i} className="w-1.5 h-1.5 bg-blue-500/60 rounded-full animate-bounce"
            style={{ animationDelay: `${i * 0.15}s` }} />
        ))}
      </div>
    </div>
  );
}

// ── Message pane ─────────────────────────────────────────────────────────
function MessagePane({ phone, messages, loading, forceLoading, newCount, lastRefresh,
  autoRefresh, interval_, notifPerm,
  onRefresh, onForceRefresh, onToggleAuto, onIntervalChange, onRequestNotif }: {
  phone: PhoneNumber | null; messages: MessagesResult | null;
  loading: boolean; forceLoading: boolean; newCount: number;
  lastRefresh: string; autoRefresh: boolean; interval_: number;
  notifPerm: NotificationPermission;
  onRefresh: () => void; onForceRefresh: () => void;
  onToggleAuto: () => void; onIntervalChange: (v: number) => void; onRequestNotif: () => void;
}) {
  if (!phone) return (
    <div className="bg-[#161b22] border border-[#21262d] rounded-xl flex items-center justify-center" style={{ minHeight: 360 }}>
      <div className="text-center px-8">
        <div className="text-5xl mb-4">📱</div>
        <p className="text-gray-300 text-sm font-semibold">从左侧选择或输入号码</p>
        <p className="text-gray-500 text-[11px] mt-2 leading-relaxed">
          已缓存号码 ⚡ 即时返回<br/>
          首次或强制刷新约需 60~90 秒
        </p>
      </div>
    </div>
  );

  const busy = loading || forceLoading;

  return (
    <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden flex flex-col" style={{ minHeight: 360 }}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d] flex-wrap gap-2">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-mono font-bold text-white">🇺🇸 {phone.number}</span>
            {messages?.cached && !busy && (
              <span className="text-[9px] bg-emerald-500/20 border border-emerald-500/30 text-emerald-400 px-1.5 py-0.5 rounded-full">⚡ 缓存</span>
            )}
            {newCount > 0 && (
              <span className="text-[9px] bg-blue-500/20 border border-blue-500/30 text-blue-400 px-1.5 py-0.5 rounded-full animate-pulse">+{newCount} 新消息</span>
            )}
          </div>
          {lastRefresh && <div className="text-[10px] text-gray-600 mt-0.5">更新于 {lastRefresh}</div>}
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          {notifPerm !== "granted" && (
            <button onClick={onRequestNotif} className="text-[10px] px-2 py-1 bg-amber-500/15 border border-amber-500/25 rounded text-amber-400 hover:bg-amber-500/25">🔔</button>
          )}
          {/* Force refresh button */}
          <button
            onClick={onForceRefresh}
            disabled={busy}
            title="跳过缓存，重新抓取最新短信（约 60~90 秒）"
            className="text-[10px] px-2.5 py-1.5 bg-orange-500/15 border border-orange-500/30 rounded-lg text-orange-400 hover:bg-orange-500/25 disabled:opacity-40 transition-all flex items-center gap-1"
          >
            <span>🔃</span>
            <span>强制刷新</span>
          </button>
          <label className="flex items-center gap-1.5 cursor-pointer select-none">
            <div onClick={onToggleAuto} className={`w-8 h-4 rounded-full relative transition-colors cursor-pointer ${autoRefresh ? "bg-blue-600" : "bg-gray-700"}`}>
              <div className={`w-3 h-3 bg-white rounded-full absolute top-0.5 transition-all ${autoRefresh ? "left-4" : "left-0.5"}`} />
            </div>
            <span className="text-[10px] text-gray-400">自动</span>
          </label>
          <select value={interval_} onChange={e => onIntervalChange(Number(e.target.value))}
            className="text-[10px] bg-[#0d1117] border border-[#21262d] rounded px-1.5 py-0.5 text-gray-400">
            <option value={15}>15s</option><option value={30}>30s</option>
            <option value={60}>60s</option><option value={120}>2min</option>
          </select>
          <button onClick={onRefresh} disabled={busy}
            className="text-[10px] px-2.5 py-1.5 bg-blue-600/20 border border-blue-500/30 rounded-lg text-blue-400 hover:bg-blue-600/30 disabled:opacity-40 transition-all">
            {loading ? "读取…" : "刷新"}
          </button>
        </div>
      </div>

      {/* Auto-refresh progress */}
      {autoRefresh && !busy && (
        <div className="h-0.5 bg-[#21262d]">
          <div key={lastRefresh} className="h-full bg-blue-500/40" style={{ animation: `smsProg ${interval_}s linear` }} />
        </div>
      )}

      {/* Body */}
      <div className="flex-1 overflow-y-auto" style={{ maxHeight: 520 }}>
        {/* Force-refresh countdown */}
        {forceLoading ? (
          <ForceProgress onDone={() => {}} />
        ) : loading ? (
          <div className="py-16 text-center">
            <div className="relative inline-block mb-6">
              <div className="w-16 h-16 rounded-full border-2 border-blue-500/20 border-t-blue-500 animate-spin" />
              <div className="absolute inset-0 flex items-center justify-center text-2xl">📡</div>
            </div>
            <p className="text-gray-200 text-sm font-semibold mb-1">绕过 Cloudflare…</p>
            <p className="text-gray-500 text-[11px] mb-4">首次约需 60~90 秒，之后缓存秒回</p>
            <div className="flex justify-center gap-1.5">
              {[0,1,2,3].map(i => (
                <div key={i} className="w-2 h-2 bg-blue-500/60 rounded-full animate-bounce" style={{ animationDelay: `${i*0.15}s` }} />
              ))}
            </div>
          </div>
        ) : messages?.error ? (
          <div className="p-6 text-center">
            <div className="text-3xl mb-3">⚠️</div>
            <p className="text-red-400 text-xs font-mono break-all px-4 max-w-sm mx-auto">{messages.error}</p>
            <div className="flex justify-center gap-2 mt-4">
              <button onClick={onRefresh} className="text-[10px] px-3 py-1.5 bg-blue-600/15 border border-blue-500/25 rounded-lg text-blue-400 hover:bg-blue-600/25">重试（缓存）</button>
              <button onClick={onForceRefresh} className="text-[10px] px-3 py-1.5 bg-orange-500/15 border border-orange-500/25 rounded-lg text-orange-400 hover:bg-orange-500/25">强制刷新</button>
            </div>
          </div>
        ) : !messages ? (
          <div className="p-8 text-center text-gray-700 text-xs">选择号码后开始读取</div>
        ) : (messages.messages?.length ?? 0) === 0 ? (
          <div className="p-10 text-center">
            <div className="text-3xl mb-2">📭</div>
            <p className="text-gray-500 text-xs mb-3">该号码暂无短信</p>
            <button onClick={onForceRefresh} className="text-[10px] px-3 py-1.5 bg-orange-500/15 border border-orange-500/25 rounded-lg text-orange-400 hover:bg-orange-500/25">🔃 强制刷新</button>
          </div>
        ) : (
          <div className="p-3 space-y-2">
            {messages.messages?.map((msg, i) => {
              const code  = extractCode(msg.body);
              const isNew = i < newCount;
              return (
                <div key={i} className={`border rounded-lg p-3 space-y-1.5 transition-all ${isNew ? "bg-emerald-500/5 border-emerald-500/30" : "bg-[#0d1117] border-[#21262d] hover:border-[#30363d]"}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      {isNew && <span className="inline-block text-[9px] bg-emerald-500/20 text-emerald-400 px-1.5 py-0.5 rounded-full mb-1">NEW</span>}
                      {msg.info && <div className="text-[10px] text-gray-500 font-mono mb-1 truncate">{msg.info}</div>}
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
            })}
          </div>
        )}
      </div>

      {/* Footer */}
      {messages && !busy && (messages.messages?.length ?? 0) > 0 && (
        <div className="px-4 py-2 border-t border-[#21262d] flex justify-between items-center">
          <span className="text-[10px] text-gray-600">共 {messages.count ?? messages.messages?.length} 条</span>
          <span className="text-[10px] text-gray-700">{messages.cached ? "⚡ 缓存数据" : "🌐 实时抓取"}</span>
        </div>
      )}
    </div>
  );
}

// ── Shared hook ─────────────────────────────────────────────────────────
function useMsgFetcher() {
  const [selected, setSelected]       = useState<PhoneNumber | null>(null);
  const [messages, setMessages]       = useState<MessagesResult | null>(null);
  const [loading, setLoading]         = useState(false);
  const [forceLoading, setForceLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [interval_, setInterval_]     = useState(30);
  const [lastRefresh, setLastRefresh] = useState("");
  const [newCount, setNewCount]       = useState(0);
  const [notifPerm, setNotifPerm]     = useState<NotificationPermission>(
    "Notification" in window ? Notification.permission : "denied"
  );
  const prevBodies = useRef<Set<string>>(new Set());
  const timerRef   = useRef<ReturnType<typeof setInterval> | null>(null);

  const doFetch = useCallback(async (phone: PhoneNumber, opts: { polling?: boolean; force?: boolean } = {}) => {
    const { polling = false, force = false } = opts;
    if (!polling && !force) { setLoading(true); setMessages(null); setNewCount(0); prevBodies.current = new Set(); }
    if (force) { setForceLoading(true); }

    try {
      const raw = phone.id.replace(/\D/g, "");
      const res = await fetch("/api/tools/smsrf/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone: raw, force: force || undefined }),
      });
      const r: MessagesResult = await res.json();

      if (polling && r.messages) {
        const incoming = r.messages.filter(m => !prevBodies.current.has(m.body));
        if (incoming.length) {
          setNewCount(n => n + incoming.length);
          incoming.forEach(m => notify(`📱 新短信 ${phone.number}`, m.body.slice(0, 100)));
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

    if (!polling && !force) setLoading(false);
    if (force) setForceLoading(false);
  }, []);

  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (autoRefresh && selected)
      timerRef.current = setInterval(() => doFetch(selected, { polling: true }), interval_ * 1000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [autoRefresh, selected, interval_, doFetch]);

  const select       = (p: PhoneNumber) => { setSelected(p); setNewCount(0); prevBodies.current = new Set(); doFetch(p); };
  const refresh      = () => { if (selected) doFetch(selected); };
  const forceRefresh = () => { if (selected) doFetch(selected, { force: true }); };

  return { selected, messages, loading, forceLoading, autoRefresh, interval_, lastRefresh, newCount, notifPerm,
    select, refresh, forceRefresh, setAutoRefresh, setInterval_, setNotifPerm };
}

// ── Main page ─────────────────────────────────────────────────────────────
export default function SmsCenter() {
  const mf = useMsgFetcher();
  const [customInput, setCustomInput] = useState("");
  const [numbers, setNumbers]         = useState<PhoneNumber[]>(KNOWN_NUMBERS);
  const [loadingNums, setLoadingNums] = useState(false);
  const [search, setSearch]           = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoadingNums(true);
    fetch("/api/tools/smsrf/numbers")
      .then(r => r.json())
      .then((data: unknown) => {
        if (cancelled) return;
        if (Array.isArray(data) && data.length > 0) {
          const scraped: PhoneNumber[] = (data as { id: string | number; number: string }[]).map(n => ({
            id: String(n.id).replace(/\D/g, ""),
            number: n.number,
          }));
          const scrapedIds = new Set(scraped.map(n => n.id));
          const extras = KNOWN_NUMBERS.filter(k => !scrapedIds.has(k.id));
          setNumbers([...scraped, ...extras]);
        }
        setLoadingNums(false);
      })
      .catch(() => { if (!cancelled) setLoadingNums(false); });
    return () => { cancelled = true; };
  }, []);

  const addCustom = () => {
    const raw = customInput.replace(/\D/g, "");
    if (raw.length < 10) return;
    const digits = raw.length > 10 ? raw.slice(-10) : raw;
    const np: PhoneNumber = { id: digits, number: "+1 " + digits };
    setNumbers(prev => prev.find(n => n.id === digits) ? prev : [np, ...prev]);
    setCustomInput("");
    mf.select(np);
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
            <h2 className="text-sm font-bold text-white">📱 短信接收中心</h2>
            <p className="text-[11px] text-gray-500 mt-0.5">smsreceivefree.xyz · 美国虚拟号码 · 缓存 5 分钟</p>
          </div>
          <div className="flex items-center gap-2">
            {loadingNums && (
              <span className="text-[10px] text-blue-400/60 flex items-center gap-1">
                <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-pulse inline-block" />
                后台更新号码列表…
              </span>
            )}
            <span className="text-[10px] bg-emerald-500/10 border border-emerald-500/25 text-emerald-400 px-2 py-0.5 rounded-full">在线</span>
          </div>
        </div>
        {/* Legend */}
        <div className="mt-3 flex gap-4 text-[10px] text-gray-600 flex-wrap">
          <span>⚡ 缓存命中 — 即时返回</span>
          <span>🔃 强制刷新 — 跳过缓存，重新抓取（约 60~90 秒）</span>
          <span>🔄 自动刷新 — 按间隔读取缓存</span>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-4">
        {/* Left */}
        <div className="col-span-5 space-y-2">
          <div className="bg-[#161b22] border border-[#21262d] rounded-lg px-3 py-2.5 space-y-2">
            <div className="text-[10px] text-gray-500 font-medium">输入任意美国号码</div>
            <div className="flex gap-2">
              <input type="text" value={customInput} onChange={e => setCustomInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && addCustom()} placeholder="10 位号码"
                className="flex-1 bg-[#0d1117] border border-[#21262d] rounded px-2.5 py-1.5 text-xs font-mono text-gray-300 focus:outline-none focus:border-blue-500/50 placeholder-gray-700" />
              <button onClick={addCustom} className="text-[10px] px-3 py-1.5 bg-blue-600/20 border border-blue-500/30 rounded text-blue-400 hover:bg-blue-600/30 shrink-0">查看</button>
            </div>
          </div>
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
            <div className="px-3 py-2 border-b border-[#21262d] flex items-center gap-2">
              <input type="text" value={search} onChange={e => setSearch(e.target.value)}
                placeholder="搜索…" className="flex-1 bg-transparent text-[10px] text-gray-400 outline-none placeholder-gray-700" />
              <span className="text-[10px] text-gray-600 shrink-0">{filtered.length} 个</span>
            </div>
            <div className="overflow-y-auto" style={{ maxHeight: 500 }}>
              {filtered.map(p => (
                <button key={p.id} onClick={() => mf.select(p)}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 hover:bg-[#21262d] border-b border-[#21262d]/40 transition-all text-left ${mf.selected?.id === p.id ? "bg-blue-600/10 border-l-2 border-l-blue-500" : ""}`}>
                  <span className="text-base shrink-0">🇺🇸</span>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-mono text-white">{p.number}</div>
                    <div className="text-[9px] text-gray-600 mt-0.5">smsreceivefree.xyz</div>
                  </div>
                  {mf.selected?.id === p.id && <span className="text-[10px] text-blue-400 shrink-0">●</span>}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Right */}
        <div className="col-span-7">
          <MessagePane
            phone={mf.selected} messages={mf.messages}
            loading={mf.loading} forceLoading={mf.forceLoading}
            newCount={mf.newCount} lastRefresh={mf.lastRefresh}
            autoRefresh={mf.autoRefresh} interval_={mf.interval_} notifPerm={mf.notifPerm}
            onRefresh={mf.refresh} onForceRefresh={mf.forceRefresh}
            onToggleAuto={() => mf.setAutoRefresh(v => !v)}
            onIntervalChange={mf.setInterval_}
            onRequestNotif={async () => { const p = await Notification.requestPermission(); mf.setNotifPerm(p); }}
          />
        </div>
      </div>

      <style>{`@keyframes smsProg { from { width:100%; } to { width:0%; } }`}</style>
    </div>
  );
}
