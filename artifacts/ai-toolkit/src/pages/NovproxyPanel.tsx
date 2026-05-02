import { useState, useRef, useEffect } from "react";

interface LogEntry   { type: string; message: string }
interface NvAccount  { email: string; password: string; token?: string; access_key?: string; proxy_user?: string }
interface CdkRecord  { code: string; account_email: string | null; result: string; msg: string; attempted_at: string }
type Step      = 1 | 2 | 3 | 4;
type JobStatus = "idle" | "running" | "done" | "stopped";

const STEPS = [
  { n: 1, label: "账号配置",   icon: "📋" },
  { n: 2, label: "批量注册",   icon: "🤖", badge: "pydoll" },
  { n: 3, label: "批量登录",   icon: "⚡", badge: "HTTP直调" },
  { n: 4, label: "CDK 兑换",  icon: "🎟️", badge: "去重" },
] as const;

const LOG_CLS: Record<string, string> = {
  start:   "text-blue-400",
  success: "text-emerald-400",
  error:   "text-red-400",
  warn:    "text-yellow-400",
  done:    "text-purple-400",
  log:     "text-gray-500",
};

const CDK_RESULT_CLS: Record<string, string> = {
  success: "text-emerald-400",
  used:    "text-yellow-400",
  invalid: "text-red-400",
  error:   "text-red-500",
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
  const dbSavedCount = loginLogs.filter(l => l.message.includes('入库:')).length;

  // Step 4 — CDK
  const [cdkInput,    setCdkInput]    = useState("VWGSUUPFXDIX1AGF\n9EBCN6RNL5SMXWQI\nKIYAKHTTW5K5JVGM");
  const [cdkEmail,    setCdkEmail]    = useState("");
  const [cdkToken,    setCdkToken]    = useState("");
  const [cdkRunning,  setCdkRunning]  = useState(false);
  const [cdkResults,  setCdkResults]  = useState<{ code: string; result: string; msg: string; skipped: boolean }[]>([]);
  const [cdkRecords,  setCdkRecords]  = useState<CdkRecord[]>([]);
  const [cdkSummary,  setCdkSummary]  = useState<{ total: number; succeeded: number } | null>(null);
  const [cdkRecordsLoading, setCdkRecordsLoading] = useState(false);

  useEffect(() => { regLogEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [regLogs]);
  useEffect(() => { loginLogEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [loginLogs]);
  useEffect(() => () => { clearInterval(regPollRef.current!); clearInterval(loginPollRef.current!); }, []);

  // Auto-fill CDK email/token from login result
  useEffect(() => {
    if (loginAccounts.length > 0) {
      const first = loginAccounts[0];
      if (first.email)  setCdkEmail(first.email);
      if (first.token)  setCdkToken(first.token);
    }
  }, [loginAccounts]);

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

  const redeemCdks = async () => {
    const codes = cdkInput.split(/\n|,/).map(s => s.trim().toUpperCase()).filter(Boolean);
    if (codes.length === 0 || !cdkToken) return;
    setCdkRunning(true); setCdkResults([]); setCdkSummary(null);
    try {
      const d = await (await fetch("/api/tools/novproxy/redeem-cdk", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codes, email: cdkEmail, token: cdkToken }),
      })).json() as { success: boolean; total: number; succeeded: number; results: typeof cdkResults; error?: string };
      if (d.success) {
        setCdkResults(d.results);
        setCdkSummary({ total: d.total, succeeded: d.succeeded });
      } else {
        setCdkResults([{ code: "ERROR", result: "error", msg: d.error ?? "请求失败", skipped: false }]);
      }
    } catch (e) {
      setCdkResults([{ code: "ERROR", result: "error", msg: String(e), skipped: false }]);
    } finally {
      setCdkRunning(false);
    }
  };

  const loadCdkRecords = async () => {
    setCdkRecordsLoading(true);
    try {
      const d = await (await fetch("/api/tools/novproxy/cdk-records")).json() as { success: boolean; records: CdkRecord[] };
      if (d.success) setCdkRecords(d.records);
    } catch {}
    setCdkRecordsLoading(false);
  };

  useEffect(() => { if (step === 4) loadCdkRecords(); }, [step]);

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
    return <span className={`text-[10px] border px-2 py-0.5 rounded-full font-medium ${cls}`}>{label}</span>;
  };

  return (
    <div className="max-w-2xl mx-auto space-y-4 pb-8">
      {/* Header */}
      <div className="bg-gradient-to-r from-[#161b22] to-[#0d1117] border border-[#21262d] rounded-2xl p-5">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-lg">🌐</div>
          <div>
            <h1 className="text-lg font-bold text-white">NovProxy 自动化面板</h1>
            <p className="text-xs text-gray-500">批量注册 · 批量登录 · CDK 兑换 · 去重防消耗</p>
          </div>
        </div>
      </div>

      {/* Step Nav */}
      <div className="grid grid-cols-4 gap-2">
        {STEPS.map(s => (
          <button key={s.n} onClick={() => setStep(s.n as Step)}
            className={`flex flex-col items-center gap-1 py-3 px-2 rounded-xl border text-xs font-medium transition-all ${
              step === s.n
                ? "bg-violet-500/20 border-violet-500/40 text-violet-300"
                : "bg-[#161b22] border-[#21262d] text-gray-500 hover:border-gray-600"
            }`}>
            <span className="text-base">{s.icon}</span>
            <span>{s.label}</span>
            {'badge' in s && <span className="text-[9px] bg-[#21262d] px-1.5 rounded text-gray-500">{s.badge}</span>}
          </button>
        ))}
      </div>

      {/* ── Step 1: 账号配置 ─────────────────────────────────── */}
      {step === 1 && (
        <div className="space-y-4">
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-4">
            <h2 className="text-sm font-semibold text-gray-300">账号列表</h2>
            <textarea value={accountInput} onChange={e => setAccountInput(e.target.value)} rows={6}
              placeholder={"email:password\nemail----password"}
              className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs font-mono text-gray-300 focus:outline-none focus:border-violet-500/50 resize-none" />
            <p className="text-[10px] text-gray-600">支持 <code className="text-gray-500">email:password</code> 或 <code className="text-gray-500">email----password</code>，每行一条</p>
            {parsedAccounts.length > 0 && (
              <p className="text-[10px] text-emerald-500">✓ 已解析 {parsedAccounts.length} 个账号</p>
            )}
          </div>

          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <h2 className="text-sm font-semibold text-gray-300">注册参数</h2>
            <div className="flex items-center gap-3">
              <label className="text-xs text-gray-400 w-24">注册间隔 (秒)</label>
              <input type="number" min={1} max={60} value={regDelay} onChange={e => setRegDelay(+e.target.value)}
                className="w-20 bg-[#0d1117] border border-[#21262d] rounded px-2 py-1 text-xs text-gray-300 focus:outline-none focus:border-violet-500/50" />
            </div>
            <div className="flex items-center gap-3">
              <label className="text-xs text-gray-400 w-24">注册后自动登录</label>
              <button onClick={() => setAutoLogin(v => !v)}
                className={`w-10 h-5 rounded-full transition-colors ${autoLogin ? "bg-violet-500" : "bg-gray-700"}`}>
                <span className={`block w-4 h-4 rounded-full bg-white mx-0.5 transition-transform ${autoLogin ? "translate-x-5" : "translate-x-0"}`} />
              </button>
            </div>
          </div>

          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-300">IP 隔离配置</h2>
              <span className="text-[10px] bg-blue-500/20 text-blue-400 border border-blue-500/30 px-2 py-0.5 rounded-full">防关联</span>
            </div>
            <div className="flex items-center gap-3">
              <label className="text-xs text-gray-400 w-24">自动用CF代理池</label>
              <button onClick={() => setAutoProxy(v => !v)}
                className={`w-10 h-5 rounded-full transition-colors ${autoProxy ? "bg-violet-500" : "bg-gray-700"}`}>
                <span className={`block w-4 h-4 rounded-full bg-white mx-0.5 transition-transform ${autoProxy ? "translate-x-5" : "translate-x-0"}`} />
              </button>
            </div>
            {!autoProxy && (
              <textarea value={proxyInput} onChange={e => setProxyInput(e.target.value)} rows={3}
                placeholder={"http://user:pass@host:port\nsocks5://user:pass@host:port"}
                className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs font-mono text-gray-300 focus:outline-none focus:border-violet-500/50 resize-none" />
            )}
          </div>
        </div>
      )}

      {/* ── Step 2: 批量注册 ─────────────────────────────────── */}
      {step === 2 && (
        <div className="space-y-4">
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-300">批量注册</h2>
              <StatusBadge status={regStatus} />
            </div>
            <p className="text-xs text-gray-600">将注册 {parsedAccounts.length} 个账号，间隔 {regDelay}s</p>
            <div className="flex gap-2">
              <button onClick={startRegister} disabled={regStatus === "running" || parsedAccounts.length === 0}
                className="flex-1 py-2 rounded-lg text-xs font-medium bg-violet-600 hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed text-white transition-colors">
                {regStatus === "running" ? "注册中…" : "▶ 开始注册"}
              </button>
              {regStatus === "running" && (
                <button onClick={stopRegister}
                  className="px-4 py-2 rounded-lg text-xs font-medium bg-red-500/20 border border-red-500/30 text-red-400 hover:bg-red-500/30 transition-colors">停止</button>
              )}
            </div>
            <LogConsole logs={regLogs} endRef={regLogEnd} />
          </div>
          {regAccounts.length > 0 && (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-300">注册账号 ({regAccounts.length})</h3>
                <button onClick={() => exportAccounts(regAccounts, false)}
                  className="text-[10px] px-2 py-1 bg-[#21262d] border border-[#30363d] rounded text-gray-400">↓ 导出</button>
              </div>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {regAccounts.map((a, i) => (
                  <div key={i} className="bg-[#0d1117] border border-[#21262d] rounded px-3 py-1.5 flex items-center gap-2">
                    <span className="text-xs text-emerald-400 font-mono flex-1 truncate">{a.email}</span>
                    <button onClick={() => copy(a.password, `pw-${i}`)}
                      className={`text-[9px] px-1.5 py-0.5 border rounded ${copied===`pw-${i}` ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}>
                      {copied===`pw-${i}` ? "✓" : "密码"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Step 3: 批量登录 ─────────────────────────────────── */}
      {step === 3 && (
        <div className="space-y-4">
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-300">批量登录</h2>
              <div className="flex items-center gap-2">
                {dbSavedCount > 0 && (
                  <span className="text-[10px] bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded-full">💾 {dbSavedCount} 入库</span>
                )}
                <StatusBadge status={loginStatus} />
              </div>
            </div>
            <p className="text-xs text-gray-600">纯 HTTP，零浏览器 — 自动提取代理凭据 + 入库</p>
            <div className="flex gap-2">
              <button onClick={() => startLogin()} disabled={loginStatus === "running" || parsedAccounts.length === 0}
                className="flex-1 py-2 rounded-lg text-xs font-medium bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed text-white transition-colors">
                {loginStatus === "running" ? "登录中…" : "⚡ 开始登录"}
              </button>
              {loginStatus === "running" && (
                <button onClick={stopLogin}
                  className="px-4 py-2 rounded-lg text-xs font-medium bg-red-500/20 border border-red-500/30 text-red-400 hover:bg-red-500/30 transition-colors">停止</button>
              )}
            </div>
            <LogConsole logs={loginLogs} endRef={loginLogEnd} />
          </div>

          {loginStatus === "done" && loginAccounts.length > 0 && (
            <div className="bg-[#161b22] border border-emerald-500/20 rounded-xl p-4 space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-emerald-400 text-sm">🌐</span>
                <h3 className="text-sm font-semibold text-emerald-300">代理连接串</h3>
              </div>
              {loginAccounts.map((a, i) => a.proxy_user && (
                <div key={i} className="space-y-1">
                  {[`http://${a.proxy_user}:m1wjx5aa@us.novproxy.io:1000`, `socks5h://${a.proxy_user}:m1wjx5aa@us.novproxy.io:1000`].map((str, j) => (
                    <div key={j} className="flex items-center gap-2 bg-[#0d1117] rounded px-2 py-1.5">
                      <code className="text-[10px] font-mono text-violet-300 flex-1 truncate">{str}</code>
                      <button onClick={() => copy(str, `proxy-${i}-${j}`)}
                        className={`text-[9px] px-1.5 py-0.5 border rounded shrink-0 ${copied===`proxy-${i}-${j}` ? "bg-violet-500/20 border-violet-500/30 text-violet-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}>
                        {copied===`proxy-${i}-${j}` ? "✓" : "复制"}
                      </button>
                    </div>
                  ))}
                  <div className="text-[9px] text-gray-600 px-1 space-y-0.5">
                    <p>· 国家指定: <code className="text-orange-300">{a.proxy_user}-country-US:m1wjx5aa@us.novproxy.io:1000</code></p>
                    <p>· 城市指定: <code className="text-orange-300">{a.proxy_user}-country-US-city-NewYork:m1wjx5aa@...</code></p>
                    <p>· 会话保持: <code className="text-orange-300">{a.proxy_user}-session-abc123:m1wjx5aa@...</code></p>
                  </div>
                </div>
              ))}
            </div>
          )}

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

      {/* ── Step 4: CDK 兑换 ─────────────────────────────────── */}
      {step === 4 && (
        <div className="space-y-4">
          {/* CDK 输入区 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-300">CDK 批量兑换</h2>
              <span className="text-[10px] bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded-full">去重防重复消耗</span>
            </div>

            {/* CDK 码输入 */}
            <div className="space-y-1.5">
              <label className="text-xs text-gray-400">CDK 码列表（每行一个）</label>
              <textarea value={cdkInput} onChange={e => setCdkInput(e.target.value)} rows={5}
                placeholder={"XXXX-XXXX-XXXX\nXXXX-XXXX-XXXX"}
                className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs font-mono text-gray-300 focus:outline-none focus:border-violet-500/50 resize-none" />
              <p className="text-[10px] text-gray-600">已输入 {cdkInput.split(/\n|,/).map(s=>s.trim()).filter(Boolean).length} 个码 · 自动去重，相同码只兑换一次</p>
            </div>

            {/* 账号 Token */}
            <div className="space-y-2">
              <label className="text-xs text-gray-400">账号 Email（可选，仅用于记录）</label>
              <input type="text" value={cdkEmail} onChange={e => setCdkEmail(e.target.value)}
                placeholder="a.diaz356@outlook.com"
                className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-1.5 text-xs font-mono text-gray-300 focus:outline-none focus:border-violet-500/50" />
              <label className="text-xs text-gray-400">账号 Token <span className="text-red-400">*</span></label>
              <div className="flex gap-2">
                <input type="text" value={cdkToken} onChange={e => setCdkToken(e.target.value)}
                  placeholder="从 Step 3 登录后自动填入"
                  className="flex-1 bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-1.5 text-xs font-mono text-gray-300 focus:outline-none focus:border-violet-500/50" />
                {!cdkToken && loginAccounts.length > 0 && loginAccounts[0].token && (
                  <button onClick={() => { setCdkToken(loginAccounts[0].token!); setCdkEmail(loginAccounts[0].email); }}
                    className="text-[10px] px-2 py-1 bg-indigo-500/20 border border-indigo-500/30 rounded text-indigo-400 hover:bg-indigo-500/30">
                    使用登录结果
                  </button>
                )}
              </div>
              {!cdkToken && (
                <p className="text-[10px] text-yellow-600">⚠ 请先在 Step 3 登录账号，Token 会自动填入</p>
              )}
            </div>

            <button onClick={redeemCdks} disabled={cdkRunning || !cdkToken || !cdkInput.trim()}
              className="w-full py-2.5 rounded-lg text-xs font-semibold bg-gradient-to-r from-violet-600 to-indigo-600 hover:from-violet-500 hover:to-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed text-white transition-all">
              {cdkRunning ? "⏳ 兑换中…" : "🎟️ 开始批量兑换"}
            </button>
          </div>

          {/* 本次兑换结果 */}
          {cdkSummary && (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-300">本次兑换结果</h3>
                <span className={`text-[10px] border px-2 py-0.5 rounded-full font-medium ${
                  cdkSummary.succeeded > 0
                    ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
                    : "bg-yellow-500/20 text-yellow-400 border-yellow-500/30"
                }`}>
                  ✓ {cdkSummary.succeeded}/{cdkSummary.total} 成功
                </span>
              </div>
              <div className="space-y-1.5">
                {cdkResults.map((r, i) => (
                  <div key={i} className="bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 flex items-start gap-2">
                    <span className={`text-[10px] font-mono font-bold mt-0.5 ${CDK_RESULT_CLS[r.result] ?? "text-gray-400"}`}>
                      {r.result === "success" ? "✅" : r.result === "used" ? "⚠️" : r.skipped ? "⏭️" : "❌"}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <code className="text-[10px] font-mono text-gray-300">{r.code}</code>
                        <span className={`text-[9px] px-1.5 py-0.5 rounded font-medium ${
                          r.result === "success" ? "bg-emerald-500/20 text-emerald-400" :
                          r.result === "used"    ? "bg-yellow-500/20 text-yellow-400" :
                          r.skipped             ? "bg-gray-500/20 text-gray-500" :
                                                   "bg-red-500/20 text-red-400"
                        }`}>
                          {r.skipped ? "已跳过" : r.result === "success" ? "兑换成功" : r.result === "used" ? "已被使用" : r.result === "invalid" ? "码无效" : "请求失败"}
                        </span>
                      </div>
                      <p className="text-[9px] text-gray-600 mt-0.5 truncate">{r.msg}</p>
                    </div>
                  </div>
                ))}
              </div>
              {cdkSummary.succeeded === 0 && (
                <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-lg px-3 py-2">
                  <p className="text-[10px] text-yellow-400 font-medium">提示：这 {cdkSummary.total} 个 CDK 码均已被其他账号使用，无法重复兑换</p>
                  <p className="text-[9px] text-gray-600 mt-1">每个 CDK 码全局一次性使用，兑换记录已保存至本地数据库防止重复请求</p>
                </div>
              )}
            </div>
          )}

          {/* 历史兑换记录 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-300">兑换历史记录</h3>
              <button onClick={loadCdkRecords} disabled={cdkRecordsLoading}
                className="text-[10px] px-2 py-1 bg-[#21262d] border border-[#30363d] rounded text-gray-400 hover:text-gray-300">
                {cdkRecordsLoading ? "刷新中…" : "↻ 刷新"}
              </button>
            </div>
            {cdkRecords.length === 0 ? (
              <p className="text-[10px] text-gray-600 text-center py-4">暂无记录</p>
            ) : (
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {cdkRecords.map((r, i) => (
                  <div key={i} className="bg-[#0d1117] border border-[#21262d] rounded px-3 py-1.5 flex items-center gap-2">
                    <span className={`text-[9px] w-14 font-medium ${CDK_RESULT_CLS[r.result] ?? "text-gray-400"}`}>
                      {r.result === "success" ? "✅成功" : r.result === "used" ? "⚠️已用" : r.result === "invalid" ? "❌无效" : "❌失败"}
                    </span>
                    <code className="text-[10px] font-mono text-gray-300 flex-1">{r.code}</code>
                    <span className="text-[9px] text-gray-600 shrink-0">
                      {new Date(r.attempted_at).toLocaleDateString()}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
