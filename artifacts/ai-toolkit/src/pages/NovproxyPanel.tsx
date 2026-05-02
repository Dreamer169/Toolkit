import { useState, useRef, useEffect } from "react";

interface LogEntry  { type: string; message: string }
interface NvAccount { email: string; password: string; token?: string; access_key?: string }
type Step = 1 | 2 | 3;
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
  log:     "text-gray-400",
};

function parseAccountList(raw: string): [string, string][] {
  const lines = raw.split(/\n/).map(l => l.trim()).filter(Boolean);
  const pairs: [string, string][] = [];
  for (const line of lines) {
    const sep = line.includes("----") ? "----" : line.includes("|") ? "|" : line.includes(":") ? ":" : " ";
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

  // ── Step 1 状态 ────────────────────────────────────────
  const [accountInput, setAccountInput] = useState("a.diaz356@outlook.com:@t*Z2$0bI#o%Dz*n");
  const [regDelay, setRegDelay] = useState(3);
  const [autoLogin, setAutoLogin] = useState(true);

  const parsedAccounts = parseAccountList(accountInput);

  // ── Step 2 注册状态 ────────────────────────────────────
  const [regJobId,    setRegJobId]    = useState<string | null>(null);
  const [regStatus,   setRegStatus]   = useState<JobStatus>("idle");
  const [regLogs,     setRegLogs]     = useState<LogEntry[]>([]);
  const [regAccounts, setRegAccounts] = useState<NvAccount[]>([]);
  const regPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const regSince   = useRef(0);
  const regLogEnd  = useRef<HTMLDivElement>(null);

  // ── Step 3 登录状态 ────────────────────────────────────
  const [loginJobId,    setLoginJobId]    = useState<string | null>(null);
  const [loginStatus,   setLoginStatus]   = useState<JobStatus>("idle");
  const [loginLogs,     setLoginLogs]     = useState<LogEntry[]>([]);
  const [loginAccounts, setLoginAccounts] = useState<NvAccount[]>([]);
  const loginPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const loginSince   = useRef(0);
  const loginLogEnd  = useRef<HTMLDivElement>(null);

  useEffect(() => { regLogEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [regLogs]);
  useEffect(() => { loginLogEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [loginLogs]);
  useEffect(() => () => { stopRegPoll(); stopLoginPoll(); }, []);

  // ── 轮询工具 ───────────────────────────────────────────
  const stopRegPoll = () => { if (regPollRef.current) { clearInterval(regPollRef.current); regPollRef.current = null; } };
  const stopLoginPoll = () => { if (loginPollRef.current) { clearInterval(loginPollRef.current); loginPollRef.current = null; } };

  const startRegPoll = (jobId: string) => {
    regSince.current = 0;
    stopRegPoll();
    regPollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`/api/tools/novproxy/register/${jobId}?since=${regSince.current}`);
        const d = await r.json() as { success: boolean; status: string; logs: LogEntry[]; accounts: { email: string; password: string; token?: string; username?: string }[]; nextSince: number };
        if (!d.success) return;
        if (d.logs?.length) { setRegLogs(p => [...p, ...d.logs]); regSince.current = d.nextSince; }
        if (d.accounts?.length) setRegAccounts(d.accounts.map(a => ({ email: a.email, password: a.password, token: a.token, access_key: a.username })));
        setRegStatus(d.status as JobStatus);
        if (d.status === "done" || d.status === "stopped") {
          stopRegPoll();
          // 自动登录
          if (autoLogin && d.accounts.length > 0 && d.status === "done") {
            const accs: [string, string][] = d.accounts.map(a => [a.email, a.password]);
            setStep(3);
            setTimeout(() => startLogin(accs), 500);
          }
        }
      } catch {}
    }, 2000);
  };

  const startLoginPoll = (jobId: string) => {
    loginSince.current = 0;
    stopLoginPoll();
    loginPollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`/api/tools/novproxy/login/${jobId}?since=${loginSince.current}`);
        const d = await r.json() as { success: boolean; status: string; logs: LogEntry[]; accounts: { email: string; password: string; token?: string; username?: string }[]; nextSince: number };
        if (!d.success) return;
        if (d.logs?.length) { setLoginLogs(p => [...p, ...d.logs]); loginSince.current = d.nextSince; }
        if (d.accounts?.length) setLoginAccounts(d.accounts.map(a => ({ email: a.email, password: a.password, token: a.token, access_key: a.username })));
        setLoginStatus(d.status as JobStatus);
        if (d.status === "done" || d.status === "stopped") stopLoginPoll();
      } catch {}
    }, 1500);
  };

  // ── 启动注册 ───────────────────────────────────────────
  const startRegister = async () => {
    if (parsedAccounts.length === 0) return;
    setRegStatus("running"); setRegLogs([]); setRegAccounts([]); setRegJobId(null);
    try {
      const r = await fetch("/api/tools/novproxy/register", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accounts: parsedAccounts, delay: regDelay }),
      });
      const d = await r.json() as { success: boolean; jobId?: string; error?: string };
      if (d.success && d.jobId) {
        setRegJobId(d.jobId);
        startRegPoll(d.jobId);
        setRegLogs([{ type: "start", message: "任务已启动，pydoll 自动化注册中..." }]);
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
    setRegLogs(p => [...p, { type: "warn", message: "⚠ 用户手动停止" }]);
  };

  // ── 启动登录 ───────────────────────────────────────────
  const startLogin = async (accs?: [string, string][]) => {
    const accounts: [string, string][] = accs ?? parsedAccounts;
    if (accounts.length === 0) return;
    setLoginStatus("running"); setLoginLogs([]); setLoginAccounts([]); setLoginJobId(null);
    try {
      const r = await fetch("/api/tools/novproxy/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accounts, delay: 0.5 }),
      });
      const d = await r.json() as { success: boolean; jobId?: string; error?: string };
      if (d.success && d.jobId) {
        setLoginJobId(d.jobId);
        startLoginPoll(d.jobId);
        setLoginLogs([{ type: "start", message: "HTTP 直调登录中（无浏览器）..." }]);
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
    setLoginLogs(p => [...p, { type: "warn", message: "⚠ 用户手动停止" }]);
  };

  // ── 导出工具 ───────────────────────────────────────────
  const copy = (text: string, k: string) => {
    navigator.clipboard.writeText(text);
    setCopied(k); setTimeout(() => setCopied(null), 1200);
  };

  const exportAccounts = (accounts: NvAccount[], withToken: boolean) => {
    const lines = accounts.map(a =>
      withToken
        ? `${a.email}----${a.password}----${a.token ?? ""}----${a.access_key ?? ""}`
        : `${a.email}----${a.password}`
    );
    const blob = new Blob([lines.join("\n")], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url;
    a.download = `novproxy_${withToken ? "tokens" : "accounts"}_${Date.now()}.txt`; a.click();
    URL.revokeObjectURL(url);
  };

  // ── 日志控制台 ─────────────────────────────────────────
  const LogConsole = ({ logs, endRef }: { logs: LogEntry[]; endRef: React.RefObject<HTMLDivElement | null> }) => (
    <div className="bg-[#0d1117] rounded-lg p-3 h-52 overflow-y-auto font-mono text-xs space-y-0.5 border border-[#21262d]">
      {logs.length === 0 ? (
        <div className="text-gray-600 text-center mt-16">等待执行…</div>
      ) : (
        logs.map((l, i) => (
          <div key={i} className={LOG_CLS[l.type] ?? "text-gray-400"}>{l.message}</div>
        ))
      )}
      <div ref={endRef} />
    </div>
  );

  // ── 账号卡片 ────────────────────────────────────────────
  const AccountCard = ({ acc, idx, withToken }: { acc: NvAccount; idx: number; withToken?: boolean }) => (
    <div className="bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 flex items-center gap-2 text-xs">
      <span className="w-5 h-5 rounded-full bg-emerald-500/20 text-emerald-400 flex items-center justify-center text-[10px] font-bold shrink-0">{idx + 1}</span>
      <span className="text-gray-300 font-mono flex-1 truncate">{acc.email}</span>
      <button onClick={() => copy(acc.password, `pw-${idx}`)} className={`px-2 py-0.5 rounded border text-[10px] shrink-0 ${copied === `pw-${idx}` ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500 hover:text-white"}`}>
        {copied === `pw-${idx}` ? "✓" : "密码"}
      </button>
      {withToken && acc.token && (
        <button onClick={() => copy(acc.token!, `tk-${idx}`)} className={`px-2 py-0.5 rounded border text-[10px] shrink-0 ${copied === `tk-${idx}` ? "bg-blue-500/20 border-blue-500/30 text-blue-400" : "bg-[#21262d] border-[#30363d] text-gray-500 hover:text-white"}`}>
          {copied === `tk-${idx}` ? "✓ token" : "token"}
        </button>
      )}
    </div>
  );

  // ── 状态徽章 ────────────────────────────────────────────
  const StatusBadge = ({ status }: { status: JobStatus }) => {
    const cls = status === "running" ? "bg-blue-500/20 text-blue-400 border-blue-500/30"
              : status === "done"    ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
              : status === "stopped" ? "bg-yellow-500/20 text-yellow-400 border-yellow-500/30"
              : "bg-gray-500/20 text-gray-400 border-gray-500/30";
    const label = status === "running" ? "运行中" : status === "done" ? "完成" : status === "stopped" ? "已停止" : "待机";
    return (
      <span className={`text-[10px] px-2 py-0.5 rounded-full border ${cls} flex items-center gap-1`}>
        {status === "running" && <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />}
        {label}
      </span>
    );
  };

  return (
    <div className="space-y-5">
      {/* 标题 */}
      <div>
        <h2 className="text-xl font-bold text-white mb-1 flex items-center gap-2">
          <span>🔐</span> Novproxy 自动化工作流
        </h2>
        <p className="text-sm text-gray-400">
          注册: <span className="text-violet-400">pydoll + ddddocr</span> (自动绕 CF + 解图片验证码) · 
          登录: <span className="text-emerald-400">纯 HTTP</span> (直调 API，零浏览器)
        </p>
      </div>

      {/* 步骤导航 */}
      <div className="flex items-center gap-0">
        {STEPS.map((s, i) => (
          <div key={s.n} className="flex items-center flex-1">
            <button
              onClick={() => setStep(s.n as Step)}
              className={`flex flex-col items-center gap-1 flex-1 py-3 px-2 rounded-xl border transition-all ${
                step === s.n ? "bg-blue-500/10 border-blue-500/40 text-white" : "bg-[#161b22] border-[#21262d] text-gray-500 hover:text-gray-300"
              }`}
            >
              <span className="text-lg">{s.icon}</span>
              <span className="text-[11px] font-medium text-center leading-tight">{s.label}</span>
              <div className="flex items-center gap-1">
                <span className={`text-[10px] w-4 h-4 rounded-full flex items-center justify-center font-bold ${step === s.n ? "bg-blue-500 text-white" : "bg-[#30363d] text-gray-500"}`}>{s.n}</span>
                {"badge" in s && s.badge && <span className="text-[9px] bg-emerald-500/20 text-emerald-400 px-1 rounded">{s.badge}</span>}
              </div>
            </button>
            {i < STEPS.length - 1 && <div className="w-4 h-px bg-[#30363d] shrink-0" />}
          </div>
        ))}
      </div>

      {/* ── Step 1: 账号配置 ── */}
      {step === 1 && (
        <div className="space-y-4">
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-4">
            <h3 className="text-sm font-semibold text-gray-300">账号列表</h3>
            <p className="text-xs text-gray-500">每行一个账号，支持格式: <code className="text-gray-400">email:pwd</code> / <code className="text-gray-400">email----pwd</code> / <code className="text-gray-400">email|pwd</code></p>
            <textarea
              value={accountInput}
              onChange={e => setAccountInput(e.target.value)}
              rows={8}
              placeholder={"a.diaz356@outlook.com:@t*Z2$0bI#o%Dz*n\nuser2@gmail.com----password2"}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-xs font-mono text-gray-300 focus:outline-none focus:border-blue-500 resize-none"
            />
            <div className="flex items-center justify-between">
              <span className="text-xs text-gray-500">已解析: <span className="text-white font-bold">{parsedAccounts.length}</span> 个账号</span>
              {parsedAccounts.length > 0 && (
                <span className="text-xs text-emerald-400">✓ 格式正确</span>
              )}
            </div>
            {parsedAccounts.length > 0 && (
              <div className="space-y-1 max-h-32 overflow-y-auto">
                {parsedAccounts.map(([e, p], i) => (
                  <div key={i} className="flex items-center gap-2 text-xs bg-[#0d1117] rounded px-2 py-1">
                    <span className="text-emerald-400 w-4 text-center font-bold">{i+1}</span>
                    <span className="text-gray-300 flex-1 truncate font-mono">{e}</span>
                    <span className="text-gray-600">{"*".repeat(Math.min(p.length, 8))}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 注册配置 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-4">
            <h3 className="text-sm font-semibold text-gray-300">注册参数</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs text-gray-500 mb-1 block">账号间隔 (秒)</label>
                <input type="number" value={regDelay} onChange={e => setRegDelay(Number(e.target.value))} min={1} max={30}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
              </div>
              <div className="flex flex-col justify-end">
                <label className="flex items-center gap-2 cursor-pointer select-none mb-2">
                  <div onClick={() => setAutoLogin(v => !v)} className={`relative w-9 h-5 rounded-full transition-colors ${autoLogin ? "bg-emerald-600" : "bg-gray-700"}`}>
                    <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${autoLogin ? "translate-x-4" : "translate-x-0.5"}`} />
                  </div>
                  <span className="text-xs text-gray-300">注册后自动登录</span>
                </label>
              </div>
            </div>
          </div>

          <div className="flex gap-3">
            <button onClick={() => { setStep(2); startRegister(); }}
              disabled={parsedAccounts.length === 0}
              className="flex-1 py-3 bg-violet-600 hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-xl text-white text-sm font-semibold transition-all">
              🤖 开始批量注册 ({parsedAccounts.length} 个)
            </button>
            <button onClick={() => { setStep(3); startLogin(); }}
              disabled={parsedAccounts.length === 0}
              className="flex-1 py-3 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 disabled:cursor-not-allowed rounded-xl text-white text-sm font-semibold transition-all">
              ⚡ 直接批量登录
            </button>
          </div>
        </div>
      )}

      {/* ── Step 2: 批量注册 ── */}
      {step === 2 && (
        <div className="space-y-4">
          {/* 说明卡 */}
          <div className="bg-violet-500/5 border border-violet-500/20 rounded-xl p-4 text-xs text-violet-300 space-y-1">
            <p className="font-semibold">🤖 pydoll + ddddocr 注册原理</p>
            <ul className="text-violet-300/70 space-y-0.5 list-disc list-inside ml-1">
              <li>Chromium 无头浏览器访问 novproxy.com/register/</li>
              <li>ddddocr 自动识别页面图片验证码（识别率 &gt;90%）</li>
              <li>CDP 键盘事件触发 jQuery input 状态更新</li>
              <li>每个账号独立 Chrome 实例（隔离 cookie/fingerprint）</li>
            </ul>
          </div>

          {/* 控制台 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-300">注册控制台</h3>
              <div className="flex items-center gap-2">
                <StatusBadge status={regStatus} />
                {regAccounts.length > 0 && (
                  <span className="text-xs text-emerald-400 font-bold">✅ {regAccounts.length} 成功</span>
                )}
              </div>
            </div>
            <LogConsole logs={regLogs} endRef={regLogEnd} />
            <div className="flex gap-2">
              {regStatus === "running" ? (
                <button onClick={stopRegister} className="flex-1 py-2 bg-red-600/20 hover:bg-red-600/30 border border-red-500/30 rounded-lg text-red-400 text-sm font-medium transition-all">
                  ⏹ 停止注册
                </button>
              ) : (
                <button onClick={startRegister} disabled={parsedAccounts.length === 0}
                  className="flex-1 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-40 rounded-lg text-white text-sm font-medium transition-all">
                  {regStatus === "idle" ? "▶ 开始注册" : "↺ 重新注册"}
                </button>
              )}
              {regStatus !== "idle" && (
                <button onClick={() => setStep(3)} className="px-4 py-2 bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] rounded-lg text-gray-300 text-sm transition-all">
                  → 去登录
                </button>
              )}
            </div>
          </div>

          {/* 注册成功账号 */}
          {regAccounts.length > 0 && (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-300">已注册账号 ({regAccounts.length})</h3>
                <button onClick={() => exportAccounts(regAccounts, false)}
                  className="text-xs px-3 py-1 bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] rounded text-gray-300 transition-all">
                  ↓ 导出
                </button>
              </div>
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {regAccounts.map((a, i) => <AccountCard key={i} acc={a} idx={i} />)}
              </div>
              <button onClick={() => { setStep(3); setTimeout(() => startLogin(regAccounts.map(a => [a.email, a.password])), 200); }}
                className="w-full py-2 bg-emerald-700 hover:bg-emerald-600 rounded-lg text-white text-sm font-medium transition-all">
                ⚡ 立即登录这 {regAccounts.length} 个账号获取 Token →
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── Step 3: 批量登录 ── */}
      {step === 3 && (
        <div className="space-y-4">
          {/* 说明卡 */}
          <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-xl p-4 text-xs text-emerald-300 space-y-1">
            <p className="font-semibold">⚡ HTTP 直调登录原理（隐蔽思路）</p>
            <ul className="text-emerald-300/70 space-y-0.5 list-disc list-inside ml-1">
              <li>通过 pydoll 网络拦截发现真实 API 端点</li>
              <li>直接调用 api.novproxy.com/v1/sign_auth + /v1/signin</li>
              <li>完全绕过浏览器，纯 HTTP 请求</li>
              <li>速度极快：单账号 &lt;1s，批量 10 个 &lt;10s</li>
            </ul>
          </div>

          {/* 控制台 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-300">登录控制台</h3>
              <div className="flex items-center gap-2">
                <StatusBadge status={loginStatus} />
                {loginAccounts.length > 0 && (
                  <span className="text-xs text-emerald-400 font-bold">✅ {loginAccounts.length} 成功</span>
                )}
              </div>
            </div>
            <LogConsole logs={loginLogs} endRef={loginLogEnd} />
            <div className="flex gap-2">
              {loginStatus === "running" ? (
                <button onClick={stopLogin} className="flex-1 py-2 bg-red-600/20 hover:bg-red-600/30 border border-red-500/30 rounded-lg text-red-400 text-sm font-medium transition-all">
                  ⏹ 停止
                </button>
              ) : (
                <button onClick={() => startLogin()} disabled={parsedAccounts.length === 0}
                  className="flex-1 py-2 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 rounded-lg text-white text-sm font-medium transition-all">
                  {loginStatus === "idle" ? "⚡ 开始登录" : "↺ 重新登录"}
                </button>
              )}
              <button onClick={() => setStep(1)} className="px-4 py-2 bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] rounded-lg text-gray-300 text-sm transition-all">
                ← 返回配置
              </button>
            </div>
          </div>

          {/* 登录结果 + Token */}
          {loginAccounts.length > 0 && (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-300">账号 + Token ({loginAccounts.length})</h3>
                <div className="flex gap-2">
                  <button onClick={() => exportAccounts(loginAccounts, true)}
                    className="text-xs px-3 py-1 bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] rounded text-gray-300 transition-all">
                    ↓ 含Token导出
                  </button>
                  <button onClick={() => {
                    const text = loginAccounts.map(a => `${a.email}----${a.password}----${a.token ?? ""}----${a.access_key ?? ""}`).join("\n");
                    copy(text, "all-tokens");
                  }} className={`text-xs px-3 py-1 border rounded transition-all ${copied === "all-tokens" ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-300"}`}>
                    {copied === "all-tokens" ? "✓ 已复制" : "复制全部"}
                  </button>
                </div>
              </div>
              <div className="space-y-1.5 max-h-64 overflow-y-auto">
                {loginAccounts.map((a, i) => (
                  <div key={i} className="bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2.5 space-y-1.5">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-emerald-400 font-mono flex-1 truncate">{a.email}</span>
                      <button onClick={() => copy(a.password, `pw2-${i}`)} className={`text-[10px] px-2 py-0.5 border rounded ${copied === `pw2-${i}` ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}>
                        {copied === `pw2-${i}` ? "✓" : "密码"}
                      </button>
                    </div>
                    {a.token && (
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-gray-600 w-14 shrink-0">token</span>
                        <span className="text-[10px] font-mono text-blue-300 flex-1 truncate">{a.token}</span>
                        <button onClick={() => copy(a.token!, `tk2-${i}`)} className={`text-[10px] px-2 py-0.5 border rounded shrink-0 ${copied === `tk2-${i}` ? "bg-blue-500/20 border-blue-500/30 text-blue-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}>
                          {copied === `tk2-${i}` ? "✓" : "复制"}
                        </button>
                      </div>
                    )}
                    {a.access_key && (
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-gray-600 w-14 shrink-0">access_key</span>
                        <span className="text-[10px] font-mono text-violet-300 flex-1 truncate">{a.access_key}</span>
                        <button onClick={() => copy(a.access_key!, `ak-${i}`)} className={`text-[10px] px-2 py-0.5 border rounded shrink-0 ${copied === `ak-${i}` ? "bg-violet-500/20 border-violet-500/30 text-violet-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}>
                          {copied === `ak-${i}` ? "✓" : "复制"}
                        </button>
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
