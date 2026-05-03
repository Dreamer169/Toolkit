import { useState, useRef, useEffect } from "react";

const API = "/api";

type Phase = "idle" | "gen-outlook" | "register-webshare" | "done" | "error";

interface LogEntry { type: string; message: string; }

interface OutlookResult {
  success: boolean;
  email: string;
  password: string;
  error?: string;
}

interface WebshareResult {
  success: boolean;
  email: string;
  password?: string;
  api_key?: string;
  plan?: string;
  error?: string;
  elapsed?: string;
}

function colorClass(type: string) {
  if (type === "ok" || type === "start") return "text-emerald-400";
  if (type === "error") return "text-red-400";
  if (type === "warn") return "text-amber-400";
  return "text-gray-300";
}

function Step({ n, label, desc, active, done, error }: {
  n: number; label: string; desc: string;
  active: boolean; done: boolean; error?: boolean;
}) {
  return (
    <div className={`flex items-start gap-3 p-3 rounded-lg border transition-all ${
      active ? "bg-blue-500/10 border-blue-500/30" :
      done ? "bg-emerald-500/5 border-emerald-500/20" :
      error ? "bg-red-500/10 border-red-500/30" :
      "bg-[#0d1117] border-[#21262d]"
    }`}>
      <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 mt-0.5 ${
        active ? "bg-blue-600 text-white animate-pulse" :
        done ? "bg-emerald-700 border border-emerald-600 text-emerald-300" :
        error ? "bg-red-700 border border-red-600 text-red-300" :
        "bg-[#21262d] text-gray-600"
      }`}>
        {done ? "✓" : error ? "✗" : n}
      </div>
      <div className="flex-1 min-w-0">
        <div className={`text-sm font-semibold ${active ? "text-white" : done ? "text-emerald-300" : error ? "text-red-300" : "text-gray-500"}`}>
          {label}
        </div>
        <div className="text-[10px] text-gray-600 mt-0.5">{desc}</div>
      </div>
    </div>
  );
}

function LogPanel({ logs, logRef }: { logs: LogEntry[]; logRef: React.RefObject<HTMLDivElement | null> }) {
  return (
    <div ref={logRef} className="bg-[#0d1117] rounded-xl border border-[#21262d] overflow-y-auto font-mono text-[11px] p-3 space-y-0.5" style={{ height: 280 }}>
      {logs.length === 0 ? (
        <div className="text-gray-700 text-center py-8">等待开始...</div>
      ) : logs.map((l, i) => (
        <div key={i} className={`leading-relaxed ${colorClass(l.type)}`}>
          <span className="text-gray-700 select-none">{String(i + 1).padStart(3, "0")} </span>
          {l.message}
        </div>
      ))}
    </div>
  );
}

export default function WebshareRegister() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [outlookResult, setOutlookResult] = useState<OutlookResult | null>(null);
  const [webshareResult, setWebshareResult] = useState<WebshareResult | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);

  // Config
  const [proxy, setProxy] = useState("");
  const [headless, setHeadless] = useState(true);
  const [outlookEngine, setOutlookEngine] = useState("patchright");
  const [capsolverKey, setCapsolverKey] = useState("");
  const [showCapsolverKey, setShowCapsolverKey] = useState(false);

  // Manual modes
  const [useManualEmail, setUseManualEmail] = useState(false);
  const [manualEmail, setManualEmail] = useState("");
  const [manualPassword, setManualPassword] = useState("");

  const [manualApiKey, setManualApiKey] = useState("");
  const [showManualApiKey, setShowManualApiKey] = useState(false);

  const [elapsed, setElapsed] = useState("0.0");
  const [wsJobId, setWsJobId] = useState<string | null>(null);
  const [olJobId, setOlJobId] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const elapsedRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const sinceRef = useRef(0);
  const t0Ref = useRef(0);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (elapsedRef.current) clearInterval(elapsedRef.current);
  }, []);

  const addLog = (type: string, message: string) =>
    setLogs(prev => [...prev, { type, message }]);

  const startElapsedTimer = () => {
    t0Ref.current = Date.now();
    if (elapsedRef.current) clearInterval(elapsedRef.current);
    elapsedRef.current = setInterval(() =>
      setElapsed(((Date.now() - t0Ref.current) / 1000).toFixed(1)), 500);
  };

  const stopElapsed = () => {
    if (elapsedRef.current) { clearInterval(elapsedRef.current); elapsedRef.current = null; }
  };

  const stopPoll = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  const resetAll = () => {
    stopPoll(); stopElapsed();
    setPhase("idle"); setLogs([]);
    setOutlookResult(null); setWebshareResult(null);
    setWsJobId(null); setOlJobId(null);
    sinceRef.current = 0;
  };

  // ── Manual API Key Save ────────────────────────────────────────────────
  function saveManualApiKey() {
    if (!manualApiKey.trim()) return;
    const email = manualEmail || outlookResult?.email || "手动输入";
    const password = manualPassword || outlookResult?.password || "";
    setWebshareResult({
      success: true,
      email,
      password,
      api_key: manualApiKey.trim(),
      plan: "free",
    });
    setPhase("done");
    addLog("ok", `✅ 已保存手动输入的 API Key: ${manualApiKey.slice(0, 20)}...`);
  }

  // ── Step 2: Webshare Register ──────────────────────────────────────────
  async function startWebshareRegister(email: string, password: string) {
    setPhase("register-webshare");
    sinceRef.current = 0;
    addLog("log", "");
    addLog("start", "🌐 步骤 2/2：注册 Webshare.io...");
    addLog("log", `📧 邮箱: ${email}`);
    if (capsolverKey) addLog("log", "🔑 使用 Capsolver 自动解决 reCAPTCHA");
    startElapsedTimer();

    try {
      const body: Record<string, unknown> = { email, password, headless };
      if (proxy) body.proxy = proxy;
      if (capsolverKey.trim()) body.capsolverKey = capsolverKey.trim();

      const r = await fetch(`${API}/tools/webshare/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!d.success) throw new Error(d.error || "Webshare 启动失败");

      const jid = d.jobId;
      setWsJobId(jid);
      addLog("log", `📋 任务 ID: ${jid}`);

      pollRef.current = setInterval(async () => {
        try {
          const pr = await fetch(`${API}/tools/webshare/register/${jid}?since=${sinceRef.current}`);
          const pd = await pr.json();
          if (pd.logs) {
            pd.logs.forEach((l: LogEntry) => addLog(l.type, l.message));
            sinceRef.current += pd.logs.length;
          }
          if (pd.status === "done" || pd.status === "error" || pd.status === "failed") {
            stopPoll(); stopElapsed();
            const result: WebshareResult = pd.result ?? {};
            result.password = password;
            setWebshareResult(result);
            if (result.success) {
              addLog("ok", "✅ Webshare 注册完成！");
              setPhase("done");
            } else {
              addLog("error", `❌ Webshare 失败: ${result.error ?? "未知错误"}`);
              setPhase("error");
            }
          }
        } catch { /* keep polling */ }
      }, 2000);

    } catch (e) {
      stopElapsed();
      addLog("error", `❌ ${String(e)}`);
      setPhase("error");
    }
  }

  // ── Step 1: Outlook Register ────────────────────────────────────────────
  async function startOutlookRegister() {
    setPhase("gen-outlook");
    setLogs([]); setOutlookResult(null); setWebshareResult(null);
    sinceRef.current = 0;
    startElapsedTimer();

    addLog("start", "🚀 启动完整注册工作流...");
    addLog("log", "📧 步骤 1/2：注册新 Outlook 账号...");

    try {
      const body: Record<string, unknown> = {
        count: 1, engine: outlookEngine, headless, wait: 11, retries: 2,
        proxyMode: proxy ? "" : "cf",
      };
      if (proxy) body.proxy = proxy;

      const r = await fetch(`${API}/tools/outlook/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!d.success) throw new Error(d.error || "启动失败");

      const jid = d.jobId;
      setOlJobId(jid);
      addLog("log", `📋 Outlook 任务: ${jid}`);

      pollRef.current = setInterval(async () => {
        try {
          const pr = await fetch(`${API}/tools/outlook/register/${jid}?since=${sinceRef.current}`);
          const pd = await pr.json();
          if (pd.logs) {
            pd.logs.forEach((l: LogEntry) => addLog(l.type, l.message));
            sinceRef.current += pd.logs.length;
          }
          if (pd.status === "done" || pd.status === "error") {
            stopPoll(); stopElapsed();
            const results = pd.result?.results ?? [];
            const ok = results.find((r: OutlookResult) => r.success);
            if (ok) {
              setOutlookResult(ok);
              addLog("ok", `✅ Outlook 注册成功: ${ok.email}`);
              sinceRef.current = 0;
              await startWebshareRegister(ok.email, ok.password);
            } else {
              const errMsg = results[0]?.error || pd.result?.error || "Outlook 注册失败";
              addLog("error", `❌ ${errMsg}`);
              setPhase("error");
            }
          }
        } catch { /* keep polling */ }
      }, 2000);

    } catch (e) {
      stopElapsed();
      addLog("error", `❌ ${String(e)}`);
      setPhase("error");
    }
  }

  // ── Manual email → webshare only ──────────────────────────────────────
  async function startManualWebshare() {
    if (!manualEmail || !manualPassword) return;
    setLogs([]); setWebshareResult(null);
    setOutlookResult({ success: true, email: manualEmail, password: manualPassword });
    sinceRef.current = 0;
    await startWebshareRegister(manualEmail, manualPassword);
  }

  const canStart = phase === "idle" || phase === "done" || phase === "error";
  const isBusy = phase === "gen-outlook" || phase === "register-webshare";

  const copyAll = () => {
    if (!webshareResult) return;
    const lines = [
      "Webshare 注册结果",
      `邮箱: ${webshareResult.email}`,
      webshareResult.password ? `密码: ${webshareResult.password}` : "",
      webshareResult.api_key ? `API Key: ${webshareResult.api_key}` : "",
    ].filter(Boolean).join("\n");
    navigator.clipboard.writeText(lines);
  };

  return (
    <div className="space-y-4 max-w-4xl mx-auto">
      {/* Header */}
      <div className="bg-gradient-to-r from-[#161b22] to-[#1c2128] border border-[#21262d] rounded-xl p-5">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-cyan-600 flex items-center justify-center text-xl shadow-lg">
            🌐
          </div>
          <div>
            <h2 className="text-base font-bold text-white">Webshare 注册工作流</h2>
            <p className="text-[11px] text-gray-500">全自动：生成 Outlook → 注册 webshare.io → 获取 API Key</p>
          </div>
          {isBusy && (
            <div className="ml-auto flex items-center gap-2 text-[11px] text-blue-400">
              <div className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
              运行中 {elapsed}s
            </div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-2">
          <Step n={1} label="生成 Outlook 账号" desc="patchright 自动注册 outlook.com 邮箱"
            active={phase === "gen-outlook"} done={!!outlookResult?.success}
            error={phase === "error" && !outlookResult?.success} />
          <Step n={2} label="注册 webshare.io" desc="自动填表，处理 reCAPTCHA，获取 API Key"
            active={phase === "register-webshare"}
            done={phase === "done"}
            error={phase === "error" && !!outlookResult?.success} />
        </div>
      </div>

      <div className="grid grid-cols-5 gap-4">
        {/* Left: config */}
        <div className="col-span-2 space-y-3">

          {/* 邮箱模式 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="text-[11px] text-gray-400 font-semibold uppercase tracking-wide">邮箱来源</div>
            <div className="flex gap-2">
              <button onClick={() => setUseManualEmail(false)}
                className={`flex-1 text-xs py-1.5 rounded-lg border transition-all ${!useManualEmail ? "bg-blue-600/20 border-blue-500/40 text-blue-400" : "bg-transparent border-[#21262d] text-gray-600 hover:border-[#30363d]"}`}>
                🤖 自动生成
              </button>
              <button onClick={() => setUseManualEmail(true)}
                className={`flex-1 text-xs py-1.5 rounded-lg border transition-all ${useManualEmail ? "bg-purple-600/20 border-purple-500/40 text-purple-400" : "bg-transparent border-[#21262d] text-gray-600 hover:border-[#30363d]"}`}>
                ✉️ 手动输入
              </button>
            </div>

            {useManualEmail ? (
              <>
                <div>
                  <label className="text-[10px] text-gray-500 mb-1 block">Outlook 邮箱</label>
                  <input value={manualEmail} onChange={e => setManualEmail(e.target.value)}
                    placeholder="user@outlook.com" type="email"
                    className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs text-gray-200 outline-none focus:border-blue-500/50 placeholder-gray-700" />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 mb-1 block">Outlook 密码</label>
                  <input value={manualPassword} onChange={e => setManualPassword(e.target.value)}
                    placeholder="密码" type="password"
                    className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs text-gray-200 outline-none focus:border-blue-500/50 placeholder-gray-700" />
                </div>
              </>
            ) : (
              <div>
                <label className="text-[10px] text-gray-500 mb-1 block">注册引擎</label>
                <select value={outlookEngine} onChange={e => setOutlookEngine(e.target.value)}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs text-gray-300 outline-none">
                  <option value="patchright">patchright（推荐）</option>
                  <option value="playwright">playwright</option>
                  <option value="camoufox">camoufox</option>
                </select>
              </div>
            )}
          </div>

          {/* CAPTCHA 配置 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-[11px] text-gray-400 font-semibold uppercase tracking-wide">CAPTCHA 解决方案</div>
              {capsolverKey && (
                <span className="text-[9px] px-2 py-0.5 bg-emerald-500/15 border border-emerald-500/25 rounded-full text-emerald-400">已配置</span>
              )}
            </div>

            {/* Capsolver Key */}
            <div>
              <label className="text-[10px] text-gray-500 mb-1 block flex items-center gap-1">
                Capsolver API Key
                <span className="text-gray-700 font-normal">（可选，付费）</span>
              </label>
              <div className="flex gap-1.5">
                <input
                  value={capsolverKey}
                  onChange={e => setCapsolverKey(e.target.value)}
                  type={showCapsolverKey ? "text" : "password"}
                  placeholder="CAP-xxxxxxxxxxxx"
                  className="flex-1 bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs font-mono text-gray-300 outline-none focus:border-blue-500/50 placeholder-gray-700"
                />
                <button onClick={() => setShowCapsolverKey(v => !v)}
                  className="px-2 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg text-gray-600 hover:text-gray-400 text-xs">
                  {showCapsolverKey ? "🙈" : "👁"}
                </button>
              </div>
              <div className="text-[9px] text-gray-700 mt-1 leading-relaxed">
                可选付费加速。无此 Key 时默认使用 <strong className="text-green-400">免费方案</strong>：Xvfb + Tor 浏览器（新鲜出口 IP，绕开音频限速）。
                <a href="https://capsolver.com" target="_blank" rel="noopener noreferrer"
                  className="text-blue-600 hover:underline ml-1">capsolver.com</a>
              </div>
            </div>

            {/* Proxy */}
            <div>
              <label className="text-[10px] text-gray-500 mb-1 block">代理（可选）</label>
              <input value={proxy} onChange={e => setProxy(e.target.value)}
                placeholder="socks5://user:pass@host:port"
                className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs font-mono text-gray-300 outline-none focus:border-blue-500/50 placeholder-gray-700" />
            </div>

            {/* Headless toggle */}
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <div onClick={() => setHeadless(v => !v)}
                className={`w-9 h-5 rounded-full relative transition-colors cursor-pointer ${headless ? "bg-blue-600" : "bg-gray-700"}`}>
                <div className="w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all"
                  style={{ left: headless ? "calc(100% - 18px)" : "2px" }} />
              </div>
              <span className="text-[11px] text-gray-400">无界面模式</span>
            </label>
          </div>

          {/* Action buttons */}
          <div className="space-y-2">
            <button
              onClick={useManualEmail ? startManualWebshare : startOutlookRegister}
              disabled={isBusy || (useManualEmail && (!manualEmail || !manualPassword))}
              className="w-full py-2.5 rounded-xl text-sm font-semibold transition-all disabled:opacity-40 disabled:cursor-not-allowed bg-gradient-to-r from-blue-600 to-cyan-600 hover:from-blue-500 hover:to-cyan-500 text-white shadow-lg"
            >
              {isBusy ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  运行中...
                </span>
              ) : useManualEmail ? "🌐 注册 Webshare（手动邮箱）" : "🚀 全自动注册"}
            </button>

            {isBusy && (
              <button
                onClick={() => {
                  stopPoll(); stopElapsed();
                  if (wsJobId) fetch(`${API}/tools/webshare/register/${wsJobId}`, { method: "DELETE" }).catch(() => {});
                  if (olJobId) fetch(`${API}/tools/outlook/register/${olJobId}`, { method: "DELETE" }).catch(() => {});
                  setPhase("error");
                  addLog("warn", "⚠️ 用户手动停止");
                }}
                className="w-full py-1.5 rounded-lg text-xs border border-red-500/30 text-red-400 hover:bg-red-500/10 transition-all"
              >
                ⏹ 停止
              </button>
            )}

            {(phase === "done" || phase === "error") && (
              <button onClick={resetAll}
                className="w-full py-1.5 rounded-lg text-xs border border-[#30363d] text-gray-500 hover:text-gray-300 hover:border-[#484f58] transition-all">
                🔄 重置
              </button>
            )}
          </div>

          {/* Webshare link */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-3 text-[10px] text-gray-600 space-y-1">
            <a href="https://dashboard.webshare.io/register" target="_blank" rel="noopener noreferrer"
              className="text-blue-500 hover:underline block">🔗 dashboard.webshare.io/register</a>
            <a href="https://dashboard.webshare.io/userapi/config" target="_blank" rel="noopener noreferrer"
              className="text-blue-500 hover:underline block">🔑 userapi/config（API Key 页面）</a>
          </div>
        </div>

        {/* Right: logs + result */}
        <div className="col-span-3 space-y-3">
          {/* Log panel */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[11px] text-gray-500 font-mono">实时日志</span>
              {logs.length > 0 && (
                <button onClick={() => setLogs([])} className="text-[10px] text-gray-700 hover:text-gray-500">清空</button>
              )}
            </div>
            <LogPanel logs={logs} logRef={logRef} />
          </div>

          {/* Manual API Key fallback */}
          <div className={`bg-[#161b22] border border-[#21262d] rounded-xl p-4 transition-all ${showManualApiKey ? "" : ""}`}>
            <button
              onClick={() => setShowManualApiKey(v => !v)}
              className="w-full flex items-center justify-between text-left"
            >
              <span className="text-[11px] text-gray-500 font-semibold">
                🔑 手动输入 API Key（兜底方案）
              </span>
              <span className="text-gray-700 text-xs">{showManualApiKey ? "▲" : "▼"}</span>
            </button>

            {showManualApiKey && (
              <div className="mt-3 space-y-3">
                <div className="text-[10px] text-gray-600 leading-relaxed bg-amber-500/5 border border-amber-500/15 rounded-lg px-3 py-2">
                  如果自动注册遇到 reCAPTCHA 频率限制，可手动在{" "}
                  <a href="https://dashboard.webshare.io/register" target="_blank" rel="noopener noreferrer"
                    className="text-blue-500 hover:underline">webshare.io</a>{" "}
                  注册后，从{" "}
                  <a href="https://dashboard.webshare.io/userapi/config" target="_blank" rel="noopener noreferrer"
                    className="text-blue-500 hover:underline">API 设置页</a>{" "}
                  复制 API Key 填入此处。
                </div>
                <div className="flex gap-2">
                  <input
                    value={manualApiKey}
                    onChange={e => setManualApiKey(e.target.value)}
                    placeholder="粘贴 Webshare API Key..."
                    className="flex-1 bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs font-mono text-gray-200 outline-none focus:border-blue-500/50 placeholder-gray-700"
                  />
                  <button
                    onClick={saveManualApiKey}
                    disabled={!manualApiKey.trim()}
                    className="px-4 py-2 bg-emerald-600/20 border border-emerald-500/30 rounded-lg text-xs text-emerald-400 hover:bg-emerald-600/30 disabled:opacity-40 disabled:cursor-not-allowed transition-all whitespace-nowrap"
                  >
                    保存
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Result card */}
          {phase === "done" && webshareResult?.success && (
            <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-emerald-400 text-lg">✅</span>
                  <span className="text-sm font-bold text-emerald-300">注册成功</span>
                  {webshareResult.elapsed && (
                    <span className="text-[10px] text-gray-600">耗时 {webshareResult.elapsed}</span>
                  )}
                </div>
                <button onClick={copyAll}
                  className="text-[10px] px-2.5 py-1 bg-emerald-500/15 border border-emerald-500/25 rounded-lg text-emerald-400 hover:bg-emerald-500/25 transition-all">
                  复制全部
                </button>
              </div>

              <div className="grid grid-cols-1 gap-2">
                {[
                  { label: "📧 邮箱", value: webshareResult.email },
                  webshareResult.password ? { label: "🔒 密码", value: webshareResult.password } : null,
                  webshareResult.api_key ? { label: "🔑 Webshare API Key", value: webshareResult.api_key } : null,
                  { label: "📦 计划", value: webshareResult.plan ?? "free" },
                ].filter(Boolean).map((item, i) => item && (
                  <div key={i} className="bg-[#0d1117] rounded-lg px-3 py-2 flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-[9px] text-gray-600">{item.label}</div>
                      <div className="text-xs font-mono text-gray-200 mt-0.5 break-all">{item.value}</div>
                    </div>
                    <button onClick={() => navigator.clipboard.writeText(item.value)}
                      className="shrink-0 text-[10px] px-2 py-0.5 bg-[#21262d] border border-[#30363d] rounded text-gray-400 hover:text-white transition-all">
                      复制
                    </button>
                  </div>
                ))}
              </div>

              {!webshareResult.api_key && (
                <div className="text-[10px] text-amber-400/80 bg-amber-500/5 border border-amber-500/15 rounded-lg px-3 py-2">
                  ⚠️ 注册成功但未获取到 API Key。请前往{" "}
                  <a href="https://dashboard.webshare.io/userapi/config" target="_blank" rel="noopener noreferrer"
                    className="text-blue-400 hover:underline">userapi/config</a>{" "}
                  手动复制，或使用下方手动输入框。
                </div>
              )}

              <div className="pt-1 border-t border-emerald-500/20">
                <a href="https://dashboard.webshare.io" target="_blank" rel="noopener noreferrer"
                  className="text-[11px] text-blue-400 hover:text-blue-300 hover:underline flex items-center gap-1.5">
                  🚀 打开 Webshare Dashboard →
                </a>
              </div>
            </div>
          )}

          {phase === "error" && (
            <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-xl">❌</span>
                <span className="text-sm text-red-400 font-semibold">注册失败</span>
              </div>
              <div className="text-[11px] text-red-400/70">
                {webshareResult?.error || "请查看日志了解详情"}
              </div>
              {!capsolverKey && (
                <div className="text-[10px] text-amber-400/80 bg-amber-500/5 border border-amber-500/15 rounded-lg px-3 py-2 mt-2">
                  💡 提示：使用「手动输入 API Key」兜底，或等待 Google 速率限制解除后重试。
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Info banner */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 text-[10px] text-gray-600 space-y-1.5">
        <div className="font-semibold text-gray-500 mb-1">⚙️ 工作流说明</div>
        <div>• <span className="text-green-400/80">默认免费方案</span>：Xvfb + Tor 浏览器（新鲜出口 IP，Google 未限速）→ 图片验证码 → 切换音频 → 直连/Tor 下载 → Whisper 转写</div>
        <div>• <span className="text-gray-400">Capsolver（可选付费）</span>：提供 Key 可跳过浏览器，直接调 webshare API，最快</div>
        <div>• <span className="text-gray-400">手动兜底</span>：自动流程遇到 Google 频率限制时，可手动注册 webshare.io 后填入 API Key</div>
        <div>• <span className="text-gray-400">为何 Tor</span>：服务器直连 IP 和 CF IP 经多次测试已被 Google 临时限速；Tor 每次换新出口 IP，绕过限速</div>
      </div>
    </div>
  );
}
