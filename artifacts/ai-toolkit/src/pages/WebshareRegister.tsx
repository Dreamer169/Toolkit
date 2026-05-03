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
  password: string;
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
    <div ref={logRef} className="bg-[#0d1117] rounded-xl border border-[#21262d] overflow-y-auto font-mono text-[11px] p-3 space-y-0.5" style={{ height: 300 }}>
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
  const [proxy, setProxy] = useState("");
  const [headless, setHeadless] = useState(true);
  const [outlookEngine, setOutlookEngine] = useState("patchright");
  const [manualEmail, setManualEmail] = useState("");
  const [manualPassword, setManualPassword] = useState("");
  const [useManual, setUseManual] = useState(false);
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
    elapsedRef.current = setInterval(() => {
      setElapsed(((Date.now() - t0Ref.current) / 1000).toFixed(1));
    }, 500);
  };

  const stopElapsed = () => {
    if (elapsedRef.current) { clearInterval(elapsedRef.current); elapsedRef.current = null; }
  };

  const stopPoll = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  // ── Step 1: Outlook ────────────────────────────────────────────────────
  async function startOutlookRegister() {
    setPhase("gen-outlook");
    setLogs([]);
    setOutlookResult(null);
    setWebshareResult(null);
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
      addLog("log", `📋 Outlook 任务 ID: ${jid}`);

      // 轮询 Outlook 注册结果
      pollRef.current = setInterval(async () => {
        try {
          const pr = await fetch(`${API}/tools/outlook/register/${jid}?since=${sinceRef.current}`);
          const pd = await pr.json();
          if (pd.logs) {
            pd.logs.forEach((l: LogEntry) => addLog(l.type, l.message));
            sinceRef.current += pd.logs.length;
          }
          if (pd.status === "done" || pd.status === "error") {
            stopPoll();
            stopElapsed();
            const results = pd.result?.results ?? [];
            const ok = results.find((r: OutlookResult) => r.success);
            if (ok) {
              setOutlookResult(ok);
              addLog("ok", `✅ Outlook 注册成功: ${ok.email}`);
              await startWebshareRegister(ok.email, ok.password);
            } else {
              const errMsg = results[0]?.error || pd.result?.error || "Outlook 注册失败";
              addLog("error", `❌ ${errMsg}`);
              setPhase("error");
            }
          }
        } catch {}
      }, 2000);

    } catch (e) {
      stopElapsed();
      addLog("error", `❌ ${String(e)}`);
      setPhase("error");
    }
  }

  // ── Step 2: Webshare ───────────────────────────────────────────────────
  async function startWebshareRegister(email: string, password: string) {
    setPhase("register-webshare");
    sinceRef.current = 0;
    addLog("log", "");
    addLog("start", "🌐 步骤 2/2：注册 Webshare.io...");
    addLog("log", `📧 使用邮箱: ${email}`);
    startElapsedTimer();

    try {
      const body: Record<string, unknown> = { email, password, headless };
      if (proxy) body.proxy = proxy;

      const r = await fetch(`${API}/tools/webshare/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!d.success) throw new Error(d.error || "Webshare 启动失败");

      const jid = d.jobId;
      setWsJobId(jid);
      addLog("log", `📋 Webshare 任务 ID: ${jid}`);

      pollRef.current = setInterval(async () => {
        try {
          const pr = await fetch(`${API}/tools/webshare/register/${jid}?since=${sinceRef.current}`);
          const pd = await pr.json();
          if (pd.logs) {
            pd.logs.forEach((l: LogEntry) => addLog(l.type, l.message));
            sinceRef.current += pd.logs.length;
          }
          if (pd.status === "done" || pd.status === "error") {
            stopPoll();
            stopElapsed();
            const result: WebshareResult = pd.result?.result ?? pd.result ?? {};
            setWebshareResult(result);
            if (result.success) {
              addLog("ok", "✅ Webshare 注册完成！");
              setPhase("done");
            } else {
              addLog("error", `❌ Webshare 注册失败: ${result.error ?? "未知错误"}`);
              setPhase("error");
            }
          }
        } catch {}
      }, 2000);

    } catch (e) {
      stopElapsed();
      addLog("error", `❌ ${String(e)}`);
      setPhase("error");
    }
  }

  // ── Manual webshare-only mode ──────────────────────────────────────────
  async function startManualWebshare() {
    if (!manualEmail || !manualPassword) return;
    setPhase("register-webshare");
    setLogs([]);
    setOutlookResult({ success: true, email: manualEmail, password: manualPassword });
    setWebshareResult(null);
    sinceRef.current = 0;
    await startWebshareRegister(manualEmail, manualPassword);
  }

  const canStart = phase === "idle" || phase === "done" || phase === "error";
  const isBusy = phase === "gen-outlook" || phase === "register-webshare";

  const copyAll = () => {
    if (!webshareResult) return;
    const lines = [
      `Webshare 注册结果`,
      `邮箱: ${webshareResult.email}`,
      `密码: ${webshareResult.password}`,
      webshareResult.api_key ? `API Key: ${webshareResult.api_key}` : "",
      `计划: ${webshareResult.plan ?? "free"}`,
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

        {/* Steps */}
        <div className="grid grid-cols-2 gap-2">
          <Step n={1} label="生成 Outlook 账号" desc="patchright 自动注册 outlook.com 邮箱"
            active={phase === "gen-outlook"} done={!!outlookResult?.success}
            error={phase === "error" && !outlookResult?.success} />
          <Step n={2} label="注册 webshare.io" desc="浏览器自动填表，处理 reCAPTCHA，获取 API Key"
            active={phase === "register-webshare"}
            done={phase === "done"}
            error={phase === "error" && !!outlookResult?.success} />
        </div>
      </div>

      <div className="grid grid-cols-5 gap-4">
        {/* Left: config */}
        <div className="col-span-2 space-y-3">
          {/* Mode switch */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="text-[11px] text-gray-400 font-semibold uppercase tracking-wide">模式</div>
            <div className="flex gap-2">
              <button onClick={() => setUseManual(false)}
                className={`flex-1 text-xs py-1.5 rounded-lg border transition-all ${!useManual ? "bg-blue-600/20 border-blue-500/40 text-blue-400" : "bg-transparent border-[#21262d] text-gray-600 hover:border-[#30363d]"}`}>
                🔁 全自动
              </button>
              <button onClick={() => setUseManual(true)}
                className={`flex-1 text-xs py-1.5 rounded-lg border transition-all ${useManual ? "bg-purple-600/20 border-purple-500/40 text-purple-400" : "bg-transparent border-[#21262d] text-gray-600 hover:border-[#30363d]"}`}>
                ✉️ 手动邮箱
              </button>
            </div>
          </div>

          {/* Config */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="text-[11px] text-gray-400 font-semibold uppercase tracking-wide">配置</div>

            {useManual ? (
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
                <label className="text-[10px] text-gray-500 mb-1 block">Outlook 引擎</label>
                <select value={outlookEngine} onChange={e => setOutlookEngine(e.target.value)}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs text-gray-300 outline-none">
                  <option value="patchright">patchright（推荐）</option>
                  <option value="playwright">playwright</option>
                  <option value="camoufox">camoufox</option>
                </select>
              </div>
            )}

            <div>
              <label className="text-[10px] text-gray-500 mb-1 block">代理（可选）</label>
              <input value={proxy} onChange={e => setProxy(e.target.value)}
                placeholder="socks5://user:pass@host:port"
                className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs font-mono text-gray-300 outline-none focus:border-blue-500/50 placeholder-gray-700" />
            </div>

            <label className="flex items-center gap-2 cursor-pointer select-none">
              <div onClick={() => setHeadless(v => !v)}
                className={`w-9 h-5 rounded-full relative transition-colors cursor-pointer ${headless ? "bg-blue-600" : "bg-gray-700"}`}>
                <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all ${headless ? "left-4.5" : "left-0.5"}`} style={{ left: headless ? "calc(100% - 18px)" : "2px" }} />
              </div>
              <span className="text-[11px] text-gray-400">无界面模式（Headless）</span>
            </label>

            {/* Start button */}
            <button
              onClick={useManual ? startManualWebshare : startOutlookRegister}
              disabled={isBusy || (useManual && (!manualEmail || !manualPassword))}
              className="w-full py-2.5 rounded-xl text-sm font-semibold transition-all disabled:opacity-40 disabled:cursor-not-allowed bg-gradient-to-r from-blue-600 to-cyan-600 hover:from-blue-500 hover:to-cyan-500 text-white shadow-lg"
            >
              {isBusy ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  运行中...
                </span>
              ) : canStart ? (
                useManual ? "🌐 开始注册 Webshare" : "🚀 开始全自动注册"
              ) : "运行中..."}
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
          </div>

          {/* Webshare link */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-3 space-y-1">
            <div className="text-[10px] text-gray-600">目标站点</div>
            <a href="https://dashboard.webshare.io/register" target="_blank" rel="noopener noreferrer"
              className="text-[11px] text-blue-400 hover:text-blue-300 flex items-center gap-1.5 group">
              <span>🔗</span>
              <span className="group-hover:underline">dashboard.webshare.io/register</span>
            </a>
            <div className="text-[10px] text-gray-700 mt-1 leading-relaxed">
              注册完成后可在<br/>
              <a href="https://dashboard.webshare.io/userapi/config" target="_blank" rel="noopener noreferrer"
                className="text-blue-500 hover:underline">userapi/config</a> 查看 API Key
            </div>
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

          {/* Result card */}
          {(phase === "done" || webshareResult) && webshareResult?.success && (
            <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-emerald-400 text-lg">✅</span>
                  <span className="text-sm font-bold text-emerald-300">注册成功</span>
                </div>
                <button onClick={copyAll}
                  className="text-[10px] px-2.5 py-1 bg-emerald-500/15 border border-emerald-500/25 rounded-lg text-emerald-400 hover:bg-emerald-500/25 transition-all">
                  复制全部
                </button>
              </div>

              <div className="grid grid-cols-1 gap-2">
                {[
                  { label: "📧 邮箱（Outlook）", value: webshareResult.email },
                  { label: "🔒 Outlook 密码", value: webshareResult.password },
                  webshareResult.api_key ? { label: "🔑 Webshare API Key", value: webshareResult.api_key } : null,
                  { label: "📦 计划", value: webshareResult.plan ?? "free" },
                  webshareResult.elapsed ? { label: "⏱ 耗时", value: webshareResult.elapsed } : null,
                ].filter(Boolean).map((item, i) => item && (
                  <div key={i} className="bg-[#0d1117] rounded-lg px-3 py-2 flex items-center justify-between gap-2">
                    <div>
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

              {/* Webshare dashboard link */}
              <div className="pt-1 border-t border-emerald-500/20">
                <a href="https://dashboard.webshare.io" target="_blank" rel="noopener noreferrer"
                  className="text-[11px] text-blue-400 hover:text-blue-300 hover:underline flex items-center gap-1.5">
                  🚀 打开 Webshare Dashboard →
                </a>
              </div>
            </div>
          )}

          {phase === "error" && !webshareResult?.success && (
            <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-center">
              <div className="text-2xl mb-2">❌</div>
              <div className="text-sm text-red-400 font-semibold">注册失败</div>
              <div className="text-[11px] text-red-400/70 mt-1">
                {webshareResult?.error || "请查看日志了解详情"}
              </div>
              <button onClick={() => { setPhase("idle"); setLogs([]); setOutlookResult(null); setWebshareResult(null); }}
                className="mt-3 text-[11px] px-4 py-1.5 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 hover:bg-red-500/20">
                重置
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Info banner */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
        <div className="text-[11px] text-gray-600 space-y-1">
          <div className="font-semibold text-gray-500 mb-2">⚙️ 工作流说明</div>
          <div>• <span className="text-gray-400">步骤 1</span>：使用 patchright 自动注册 outlook.com 邮箱（绕过 CAPTCHA + CF 代理池）</div>
          <div>• <span className="text-gray-400">步骤 2</span>：用 Chrome 打开 webshare.io 注册页，自动填写邮箱密码，浏览器原生处理 reCAPTCHA</div>
          <div>• <span className="text-gray-400">注意</span>：Webshare 步骤约需 30~90 秒；reCAPTCHA 可能需要人机交互（无头模式下自动尝试）</div>
          <div>• <span className="text-gray-400">手动模式</span>：如已有 Outlook 账号，可直接填写跳过步骤 1，仅执行 Webshare 注册</div>
        </div>
      </div>
    </div>
  );
}
