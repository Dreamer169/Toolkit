import { useState, useRef, useEffect } from "react";

interface LogEntry   { type: string; message: string }
interface NvAccount  { email: string; password: string; token?: string; access_key?: string; proxy_user?: string }
type Step      = 1 | 2 | 3;
type JobStatus = "idle" | "running" | "done" | "stopped";

const STEPS = [
  { n: 1, label: "账号配置",   icon: "📋" },
  { n: 2, label: "批量注册",   icon: "🤖", badge: "pydoll" },
  { n: 3, label: "批量登录",   icon: "⚡", badge: "HTTP直调" },
] as const;

const LOG_CLS: Record<string, string> = {
  start:   "text-blue-400",
  success: "text-emerald-400",
  error:   "text-red-400",
  warn:    "text-yellow-400",
  done:    "text-purple-400",
  log:     "text-gray-500",
};

function parseAccountList(raw: string): [string, string][] {
  const pairs: [string, string][] = [];
  for (const line of raw.split(/\n/).map(l => l.trim()).filter(Boolean)) {
    const sep = line.includes("----") ? "----" : line.includes("|") ? "|" : ":";
    const idx = line.indexOf(sep);
    if (idx < 0) continue;
    const email = line.slice(0, idx).trim();
    const pwd   = line.slice(idx + sep.length).trim();
    if (email.includes("@") && pwd) pairs.push([email, pwd]);
  }
  return pairs;
}

export default function NovproxyPanel() {
  const [step, setStep] = useState<Step>(1);
  const [copied, setCopied] = useState<string | null>(null);

  // Step 1
  const [accountInput, setAccountInput] = useState("a.diaz356@outlook.com:@t*Z2$0bI#o%Dz*n");
  const [regDelay,   setRegDelay]   = useState(3);
  const [autoLogin,  setAutoLogin]  = useState(true);
  const [proxyInput, setProxyInput] = useState("");
  const [autoProxy,  setAutoProxy]  = useState(false);
  const parsedAccounts = parseAccountList(accountInput);

  // Step 2 — register
  const [regJobId,    setRegJobId]    = useState<string | null>(null);
  const [regStatus,   setRegStatus]   = useState<JobStatus>("idle");
  const [regLogs,     setRegLogs]     = useState<LogEntry[]>([]);
  const [regAccounts, setRegAccounts] = useState<NvAccount[]>([]);
  const regPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const regSince   = useRef(0);
  const regLogEnd  = useRef<HTMLDivElement>(null);

  // Step 3 — login
  const [loginJobId,    setLoginJobId]    = useState<string | null>(null);
  const [loginStatus,   setLoginStatus]   = useState<JobStatus>("idle");
  const [loginLogs,     setLoginLogs]     = useState<LogEntry[]>([]);
  const [loginAccounts, setLoginAccounts] = useState<NvAccount[]>([]);
  const loginPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const loginSince   = useRef(0);
  const loginLogEnd  = useRef<HTMLDivElement>(null);

  // DB save stats (count from logs)
  const dbSavedCount = loginLogs.filter(l => l.message.includes('入库:')).length;

  useEffect(() => { regLogEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [regLogs]);
  useEffect(() => { loginLogEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [loginLogs]);
  useEffect(() => () => { clearInterval(regPollRef.current!); clearInterval(loginPollRef.current!); }, []);

  const stopRegPoll   = () => { if (regPollRef.current)   { clearInterval(regPollRef.current);   regPollRef.current   = null; } };
  const stopLoginPoll = () => { if (loginPollRef.current) { clearInterval(loginPollRef.current); loginPollRef.current = null; } };

  const startRegPoll = (jobId: string) => {
    regSince.current = 0; stopRegPoll();
    regPollRef.current = setInterval(async () => {
      try {
        const d = await (await fetch(`/api/tools/novproxy/register/${jobId}?since=${regSince.current}`)).json() as
          { success: boolean; status: string; logs: LogEntry[]; accounts: { email: string; password: string; username?: string }[]; nextSince: number };
        if (!d.success) return;
        if (d.logs?.length)     { setRegLogs(p => [...p, ...d.logs]); regSince.current = d.nextSince; }
        if (d.accounts?.length) setRegAccounts(d.accounts);
        setRegStatus(d.status as JobStatus);
        if (d.status === "done" || d.status === "stopped") {
          stopRegPoll();
          if (autoLogin && d.accounts.length > 0 && d.status === "done") {
            setStep(3);
            setTimeout(() => startLogin(d.accounts.map(a => [a.email, a.password])), 500);
          }
        }
      } catch {}
    }, 2000);
  };

  const startLoginPoll = (jobId: string) => {
    loginSince.current = 0; stopLoginPoll();
    loginPollRef.current = setInterval(async () => {
      try {
        const d = await (await fetch(`/api/tools/novproxy/login/${jobId}?since=${loginSince.current}`)).json() as
          { success: boolean; status: string; logs: LogEntry[]; accounts: { email: string; password: string; token?: string; username?: string }[]; nextSince: number };
        if (!d.success) return;
        if (d.logs?.length)     { setLoginLogs(p => [...p, ...d.logs]); loginSince.current = d.nextSince; }
        if (d.accounts?.length) setLoginAccounts(d.accounts.map(a => ({ ...a, proxy_user: a.username })));
        setLoginStatus(d.status as JobStatus);
        if (d.status === "done" || d.status === "stopped") stopLoginPoll();
      } catch {}
    }, 1500);
  };

  const startRegister = async () => {
    if (parsedAccounts.length === 0) return;
    setRegStatus("running"); setRegLogs([]); setRegAccounts([]); setRegJobId(null);
    try {
      const d = await (await fetch("/api/tools/novproxy/register", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accounts: parsedAccounts, delay: regDelay, proxies: proxyInput, autoProxy }),
      })).json() as { success: boolean; jobId?: string; error?: string };
      if (d.success && d.jobId) {
        setRegJobId(d.jobId);
        startRegPoll(d.jobId);
        setRegLogs([{ type: "start", message: "任务已启动..." }]);
      } else {
        setRegStatus("idle");
        setRegLogs([{ type: "error", message: d.error ?? "启动失败" }]);
      }
    } catch (e) { setRegStatus("idle"); setRegLogs([{ type: "error", message: String(e) }]); }
  };

  const stopRegister = async () => {
    stopRegPoll();
    if (regJobId) { try { await fetch(`/api/tools/novproxy/register/${regJobId}`, { method: "DELETE" }); } catch {} }
    setRegStatus("stopped");
    setRegLogs(p => [...p, { type: "warn", message: "⚠ 手动停止" }]);
  };

  const startLogin = async (accs?: [string, string][]) => {
    const accounts: [string, string][] = accs ?? parsedAccounts;
    if (accounts.length === 0) return;
    setLoginStatus("running"); setLoginLogs([]); setLoginAccounts([]); setLoginJobId(null);
    try {
      const d = await (await fetch("/api/tools/novproxy/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accounts, delay: 0.5 }),
      })).json() as { success: boolean; jobId?: string; error?: string };
      if (d.success && d.jobId) {
        setLoginJobId(d.jobId);
        startLoginPoll(d.jobId);
        setLoginLogs([{ type: "start", message: "HTTP 直调登录 + 提取代理凭据 + 入库..." }]);
      } else {
        setLoginStatus("idle");
        setLoginLogs([{ type: "error", message: d.error ?? "启动失败" }]);
      }
    } catch (e) { setLoginStatus("idle"); setLoginLogs([{ type: "error", message: String(e) }]); }
  };

  const stopLogin = async () => {
    stopLoginPoll();
    if (loginJobId) { try { await fetch(`/api/tools/novproxy/login/${loginJobId}`, { method: "DELETE" }); } catch {} }
    setLoginStatus("stopped");
    setLoginLogs(p => [...p, { type: "warn", message: "⚠ 手动停止" }]);
  };

  const copy = (text: string, k: string) => {
    navigator.clipboard.writeText(text);
    setCopied(k); setTimeout(() => setCopied(null), 1200);
  };

  const exportAccounts = (accounts: NvAccount[], withToken: boolean) => {
    const lines = accounts.map(a =>
      withToken
        ? `${a.email}----${a.password}----${a.token ?? ""}----${a.proxy_user ?? ""}`
        : `${a.email}----${a.password}`
    );
    const blob = new Blob([lines.join("\n")], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url;
    a.download = `novproxy_${withToken ? "full" : "accounts"}_${Date.now()}.txt`; a.click();
    URL.revokeObjectURL(url);
  };

  const LogConsole = ({ logs, endRef }: { logs: LogEntry[]; endRef: React.RefObject<HTMLDivElement | null> }) => (
    <div className="bg-[#0d1117] rounded-lg p-3 h-56 overflow-y-auto font-mono text-xs space-y-0.5 border border-[#21262d]">
      {logs.length === 0
        ? <div className="text-gray-600 text-center mt-16">等待执行…</div>
        : logs.map((l, i) => <div key={i} className={LOG_CLS[l.type] ?? "text-gray-400"}>{l.message}</div>)
      }
      <div ref={endRef} />
    </div>
  );

  const StatusBadge = ({ status }: { status: JobStatus }) => {
    const map: Record<JobStatus, [string, string]> = {
      idle:    ["bg-gray-500/20 text-gray-400 border-gray-500/30", "待机"],
      running: ["bg-blue-500/20 text-blue-400 border-blue-500/30", "运行中"],
      done:    ["bg-emerald-500/20 text-emerald-400 border-emerald-500/30", "完成"],
      stopped: ["bg-yellow-500/20 text-yellow-400 border-yellow-500/30", "已停止"],
    };
    const [cls, label] = map[status];
    return (
      <span className={`text-[10px] px-2 py-0.5 rounded-full border ${cls} flex items-center gap-1`}>
        {status === "running" && <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />}
        {label}
      </span>
    );
  };

  // 从 logs 里提取代理连接串
  const proxyStrings = loginLogs.filter(l => l.message.startsWith('📋')).map(l => l.message.slice(3).trim());

  return (
    <div className="space-y-5">
      {/* 标题 */}
      <div>
        <h2 className="text-xl font-bold text-white mb-1 flex items-center gap-2">🔐 Novproxy 自动化工作流</h2>
        <p className="text-xs text-gray-500 flex flex-wrap gap-3">
          <span className="text-violet-400">注册 → pydoll+ddddocr (绕CF/验证码)</span>
          <span>·</span>
          <span className="text-emerald-400">登录 → 纯HTTP (直调 api.novproxy.com)</span>
          <span>·</span>
          <span className="text-blue-400">代理 → us.novproxy.io:1000 (SOCKS5/HTTP)</span>
          <span>·</span>
          <span className="text-yellow-400">💾 自动入库</span>
        </p>
      </div>

      {/* 步骤导航 */}
      <div className="flex items-center gap-1">
        {STEPS.map((s, i) => (
          <div key={s.n} className="flex items-center flex-1">
            <button onClick={() => setStep(s.n as Step)}
              className={`flex flex-col items-center gap-0.5 flex-1 py-2.5 px-1 rounded-xl border transition-all ${
                step === s.n ? "bg-blue-500/10 border-blue-500/40 text-white" : "bg-[#161b22] border-[#21262d] text-gray-500 hover:text-gray-300"
              }`}>
              <span className="text-lg">{s.icon}</span>
              <span className="text-[10px] font-medium text-center leading-tight">{s.label}</span>
              <div className="flex items-center gap-1 mt-0.5">
                <span className={`text-[9px] w-4 h-4 rounded-full flex items-center justify-center font-bold ${step === s.n ? "bg-blue-500 text-white" : "bg-[#30363d] text-gray-500"}`}>{s.n}</span>
                {"badge" in s && s.badge && <span className="text-[8px] bg-emerald-500/20 text-emerald-400 px-1 rounded">{s.badge}</span>}
              </div>
            </button>
            {i < STEPS.length - 1 && <div className="w-3 h-px bg-[#30363d] shrink-0" />}
          </div>
        ))}
      </div>

      {/* ── Step 1 ── */}
      {step === 1 && (
        <div className="space-y-4">
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <h3 className="text-sm font-semibold text-gray-300">账号列表</h3>
            <p className="text-[10px] text-gray-600">格式: <code className="text-gray-500">email:pwd</code> / <code className="text-gray-500">email----pwd</code></p>
            <textarea value={accountInput} onChange={e => setAccountInput(e.target.value)} rows={6}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-xs font-mono text-gray-300 focus:outline-none focus:border-blue-500 resize-none" />
            {parsedAccounts.length > 0 && (
              <p className="text-xs text-emerald-400">✓ 已解析 {parsedAccounts.length} 个账号</p>
            )}
          </div>

          {/* 代理配置（IP一致性） */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-300">IP 隔离配置</h3>
              <span className="text-[10px] text-gray-500">Outlook 工作流同款思路</span>
            </div>
            <div className="bg-blue-500/5 border border-blue-500/20 rounded-lg p-3 text-[10px] text-blue-300/80 space-y-0.5">
              <p className="font-semibold">🎭 IP一致性原理</p>
              <p>每账号分配唯一代理 IP → 注册/登录看起来来自不同用户</p>
              <p>批量注册不会被平台识别为同一来源</p>
            </div>
            <div className="flex items-center gap-2 mb-2">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <div onClick={() => setAutoProxy(v => !v)} className={`relative w-8 h-4.5 rounded-full transition-colors ${autoProxy ? "bg-blue-600" : "bg-gray-700"}`}
                  style={{height:'18px'}}>
                  <span className={`absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white shadow transition-transform ${autoProxy ? "translate-x-4" : "translate-x-0.5"}`} style={{height:'14px',width:'14px'}} />
                </div>
                <span className="text-xs text-gray-300">自动用 CF 代理池</span>
              </label>
            </div>
            <textarea value={proxyInput} onChange={e => setProxyInput(e.target.value)} rows={3}
              placeholder={"每行一个代理（可选）:\nsocks5h://user:pass@host:1080\nhttp://user:pass@host:3128"}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-[10px] font-mono text-gray-400 focus:outline-none focus:border-blue-500 resize-none" />
            <p className="text-[10px] text-gray-600">不填则直连 (同一 IP 注册所有账号)</p>
          </div>

          {/* 其他配置 */}
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-3 space-y-2">
              <label className="text-xs text-gray-500">账号间隔 (秒)</label>
              <input type="number" value={regDelay} onChange={e => setRegDelay(Number(e.target.value))} min={1} max={30}
                className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500" />
            </div>
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-3 space-y-2 flex flex-col justify-center">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <div onClick={() => setAutoLogin(v => !v)} className={`relative w-8 rounded-full transition-colors ${autoLogin ? "bg-emerald-600" : "bg-gray-700"}`}
                  style={{height:'18px'}}>
                  <span className={`absolute top-0.5 rounded-full bg-white shadow transition-transform ${autoLogin ? "translate-x-4" : "translate-x-0.5"}`} style={{height:'14px',width:'14px'}} />
                </div>
                <span className="text-xs text-gray-300">注册后自动登录</span>
              </label>
            </div>
          </div>

          <div className="flex gap-2">
            <button onClick={() => { setStep(2); startRegister(); }}
              disabled={parsedAccounts.length === 0}
              className="flex-1 py-3 bg-violet-600 hover:bg-violet-500 disabled:opacity-40 rounded-xl text-white text-sm font-semibold transition-all">
              🤖 批量注册 ({parsedAccounts.length} 个)
            </button>
            <button onClick={() => { setStep(3); startLogin(); }}
              disabled={parsedAccounts.length === 0}
              className="flex-1 py-3 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 rounded-xl text-white text-sm font-semibold transition-all">
              ⚡ 直接登录
            </button>
          </div>
        </div>
      )}

      {/* ── Step 2 ── */}
      {step === 2 && (
        <div className="space-y-4">
          <div className="bg-violet-500/5 border border-violet-500/20 rounded-xl p-3 text-[10px] text-violet-300/80 space-y-0.5">
            <p className="font-semibold text-violet-300">🤖 pydoll + ddddocr 注册原理</p>
            <p>Chromium 无头浏览器 · ddddocr 自动解图片验证码 · 每账号独立 Chrome 实例</p>
            {proxyInput && <p>🎭 代理: {proxyInput.split('\n')[0].slice(0,40)}...</p>}
          </div>

          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-300">注册控制台</h3>
              <div className="flex items-center gap-2">
                <StatusBadge status={regStatus} />
                {regAccounts.length > 0 && <span className="text-xs text-emerald-400 font-bold">✅ {regAccounts.length}</span>}
              </div>
            </div>
            <LogConsole logs={regLogs} endRef={regLogEnd} />
            <div className="flex gap-2">
              {regStatus === "running"
                ? <button onClick={stopRegister} className="flex-1 py-2 bg-red-600/20 hover:bg-red-600/30 border border-red-500/30 rounded-lg text-red-400 text-sm">⏹ 停止</button>
                : <button onClick={startRegister} disabled={parsedAccounts.length === 0}
                    className="flex-1 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-40 rounded-lg text-white text-sm">
                    {regStatus === "idle" ? "▶ 开始" : "↺ 重试"}
                  </button>
              }
              <button onClick={() => setStep(3)} className="px-4 py-2 bg-[#21262d] border border-[#30363d] rounded-lg text-gray-300 text-sm">→ 登录</button>
            </div>
          </div>

          {regAccounts.length > 0 && (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-300">已注册 ({regAccounts.length})</h3>
                <button onClick={() => exportAccounts(regAccounts, false)} className="text-[10px] px-2 py-1 bg-[#21262d] border border-[#30363d] rounded text-gray-400">↓ 导出</button>
              </div>
              <div className="space-y-1 max-h-36 overflow-y-auto">
                {regAccounts.map((a, i) => (
                  <div key={i} className="bg-[#0d1117] border border-[#21262d] rounded px-2 py-1.5 flex items-center gap-2 text-xs">
                    <span className="text-emerald-400 w-4 font-bold text-center">{i+1}</span>
                    <span className="text-gray-300 font-mono flex-1 truncate">{a.email}</span>
                    {a.username && <span className="text-[9px] bg-blue-500/10 text-blue-400 px-1 rounded">IP:{a.username.slice(0,12)}</span>}
                    <button onClick={() => copy(a.password, `rp-${i}`)} className={`text-[9px] px-1.5 py-0.5 border rounded shrink-0 ${copied===`rp-${i}` ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}>
                      {copied===`rp-${i}` ? "✓" : "pwd"}
                    </button>
                  </div>
                ))}
              </div>
              <button onClick={() => { setStep(3); setTimeout(() => startLogin(regAccounts.map(a => [a.email, a.password])), 200); }}
                className="w-full py-2 bg-emerald-700 hover:bg-emerald-600 rounded-lg text-white text-sm font-medium transition-all">
                ⚡ 登录 {regAccounts.length} 个账号 → 获取代理凭据
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── Step 3 ── */}
      {step === 3 && (
        <div className="space-y-4">
          <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-xl p-3 text-[10px] text-emerald-300/80 space-y-0.5">
            <p className="font-semibold text-emerald-300">⚡ 登录 + 代理提取 + 自动入库</p>
            <p>纯HTTP调API → 获取代理子账号凭据 → IP白名单 → 写入 <code className="text-emerald-400">accounts</code> 表</p>
            <p>代理格式: <code className="text-yellow-300">socks5h://user:pass@us.novproxy.io:1000</code></p>
          </div>

          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-300">登录控制台</h3>
              <div className="flex items-center gap-2">
                <StatusBadge status={loginStatus} />
                {loginAccounts.length > 0 && <span className="text-xs text-emerald-400 font-bold">✅ {loginAccounts.length}</span>}
                {dbSavedCount > 0 && <span className="text-[10px] bg-yellow-500/20 text-yellow-400 px-2 py-0.5 rounded-full border border-yellow-500/30">💾 入库 {dbSavedCount}</span>}
              </div>
            </div>
            <LogConsole logs={loginLogs} endRef={loginLogEnd} />
            <div className="flex gap-2">
              {loginStatus === "running"
                ? <button onClick={stopLogin} className="flex-1 py-2 bg-red-600/20 hover:bg-red-600/30 border border-red-500/30 rounded-lg text-red-400 text-sm">⏹ 停止</button>
                : <button onClick={() => startLogin()} disabled={parsedAccounts.length === 0}
                    className="flex-1 py-2 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 rounded-lg text-white text-sm">
                    {loginStatus === "idle" ? "⚡ 开始登录" : "↺ 重新登录"}
                  </button>
              }
              <button onClick={() => setStep(1)} className="px-4 py-2 bg-[#21262d] border border-[#30363d] rounded-lg text-gray-300 text-sm">← 返回</button>
            </div>
          </div>

          {/* 代理连接串 */}
          {proxyStrings.length > 0 && (
            <div className="bg-[#161b22] border border-yellow-500/20 rounded-xl p-4 space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-yellow-300">🌐 代理连接串</h3>
                <button onClick={() => copy(proxyStrings.join('\n'), 'all-proxy')}
                  className={`text-[10px] px-2 py-1 border rounded ${copied==='all-proxy' ? "bg-yellow-500/20 border-yellow-500/30 text-yellow-400" : "bg-[#21262d] border-[#30363d] text-gray-400"}`}>
                  {copied==='all-proxy' ? "✓ 已复制" : "复制全部"}
                </button>
              </div>
              <div className="space-y-1">
                {proxyStrings.map((s, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <code className="text-[10px] font-mono text-yellow-300/80 flex-1 truncate bg-[#0d1117] rounded px-2 py-1">{s}</code>
                    <button onClick={() => copy(s.split(': ').slice(1).join(': '), `ps-${i}`)}
                      className={`text-[9px] px-2 py-0.5 border rounded shrink-0 ${copied===`ps-${i}` ? "bg-yellow-500/20 border-yellow-500/30 text-yellow-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}>
                      {copied===`ps-${i}` ? "✓" : "复制"}
                    </button>
                  </div>
                ))}
              </div>
              <div className="bg-orange-500/5 border border-orange-500/20 rounded-lg p-2 text-[10px] text-orange-300/70 space-y-0.5">
                <p className="font-semibold">💡 500MB 流量使用说明</p>
                <p>· 需先充值 OR 激活 CDK 码 → alltraffic 会 &gt; 0</p>
                <p>· 国家指定: <code className="text-orange-300">user-country-US:pass@us.novproxy.io:1000</code></p>
                <p>· 城市指定: <code className="text-orange-300">user-country-US-city-NewYork:pass@...</code></p>
                <p>· 会话保持: <code className="text-orange-300">user-session-abc123:pass@...</code></p>
              </div>
            </div>
          )}

          {/* 账号 + Token 表 */}
          {loginAccounts.length > 0 && (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-300">账号详情 ({loginAccounts.length})</h3>
                <div className="flex gap-2">
                  <button onClick={() => exportAccounts(loginAccounts, true)}
                    className="text-[10px] px-2 py-1 bg-[#21262d] border border-[#30363d] rounded text-gray-400">↓ 导出</button>
                  <button onClick={() => {
                    const t = loginAccounts.map(a => `${a.email}----${a.password}----${a.token??''}----${a.proxy_user??''}`).join('\n');
                    copy(t, 'all-acc');
                  }} className={`text-[10px] px-2 py-1 border rounded ${copied==='all-acc' ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-400"}`}>
                    {copied==='all-acc' ? "✓ 已复制" : "复制全部"}
                  </button>
                </div>
              </div>
              <div className="space-y-1.5 max-h-64 overflow-y-auto">
                {loginAccounts.map((a, i) => (
                  <div key={i} className="bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-emerald-400 font-mono flex-1 truncate">{a.email}</span>
                      <span className="text-[9px] bg-yellow-500/20 text-yellow-400 px-1.5 rounded">💾 入库</span>
                      <button onClick={() => copy(a.password, `pw3-${i}`)}
                        className={`text-[9px] px-1.5 py-0.5 border rounded ${copied===`pw3-${i}` ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}>
                        {copied===`pw3-${i}` ? "✓" : "密码"}
                      </button>
                    </div>
                    {a.token && (
                      <div className="flex items-center gap-1.5">
                        <span className="text-[9px] text-gray-600 w-12">token</span>
                        <span className="text-[9px] font-mono text-blue-300 flex-1 truncate">{a.token}</span>
                        <button onClick={() => copy(a.token!, `tk3-${i}`)}
                          className={`text-[9px] px-1.5 py-0.5 border rounded shrink-0 ${copied===`tk3-${i}` ? "bg-blue-500/20 border-blue-500/30 text-blue-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}>
                          {copied===`tk3-${i}` ? "✓" : "复制"}
                        </button>
                      </div>
                    )}
                    {a.proxy_user && (
                      <div className="flex items-center gap-1.5">
                        <span className="text-[9px] text-gray-600 w-12">代理用户</span>
                        <span className="text-[9px] font-mono text-violet-300">{a.proxy_user}</span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
