import { useState, useRef, useEffect } from "react";

const API = "/api";
type Phase = "idle" | "gen-outlook" | "register-oxylabs" | "done" | "error";
interface LogEntry { type: string; message: string; }
interface OutlookResult { success: boolean; email: string; password: string; error?: string; }
interface OxylabsResult {
  success: boolean; email: string; password: string;
  first_name?: string; last_name?: string;
  username?: string; final_url?: string; error?: string; elapsed?: string;
}
function colorClass(t: string) {
  if (t === "ok" || t === "start") return "text-emerald-400";
  if (t === "error") return "text-red-400";
  if (t === "warn") return "text-amber-400";
  return "text-gray-300";
}
function Step({ n, label, desc, active, done, error }: {
  n:number; label:string; desc:string; active:boolean; done:boolean; error?:boolean;
}) {
  return (
    <div className={`flex items-start gap-3 p-3 rounded-lg border transition-all ${active?"bg-green-500/10 border-green-500/30":done?"bg-emerald-500/5 border-emerald-500/20":error?"bg-red-500/10 border-red-500/30":"bg-[#0d1117] border-[#21262d]"}`}>
      <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 mt-0.5 ${active?"bg-green-600 text-white animate-pulse":done?"bg-emerald-700 border border-emerald-600 text-emerald-300":error?"bg-red-700 border border-red-600 text-red-300":"bg-[#21262d] text-gray-600"}`}>
        {done?"✓":error?"✗":n}
      </div>
      <div className="flex-1 min-w-0">
        <div className={`text-sm font-semibold ${active?"text-white":done?"text-emerald-300":error?"text-red-300":"text-gray-500"}`}>{label}</div>
        <div className="text-[10px] text-gray-600 mt-0.5">{desc}</div>
      </div>
    </div>
  );
}

export default function OxylabsRegister() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [outlookResult, setOutlookResult] = useState<OutlookResult|null>(null);
  const [oxylabsResult, setOxylabsResult] = useState<OxylabsResult|null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [proxy, setProxy] = useState("");
  const [headless, setHeadless] = useState(true);
  const [outlookEngine, setOutlookEngine] = useState("patchright");
  const [useManual, setUseManual] = useState(false);
  const [manualEmail, setManualEmail] = useState("");
  const [manualPassword, setManualPassword] = useState("");
  const [manualFirst, setManualFirst] = useState("");
  const [manualLast, setManualLast] = useState("");
  const [capsolverKey, setCapsolverKey] = useState("");
  const [showCapsolverInfo, setShowCapsolverInfo] = useState(false);
  const [cfClearance, setCfClearance] = useState("");
  const [showCfClearanceInfo, setShowCfClearanceInfo] = useState(false);
  const [elapsed, setElapsed] = useState("0.0");
  const [olJobId, setOlJobId] = useState<string|null>(null);
  const [wsJobId, setWsJobId] = useState<string|null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval>|null>(null);
  const elRef   = useRef<ReturnType<typeof setInterval>|null>(null);
  const logRef  = useRef<HTMLDivElement>(null);
  const since   = useRef(0);
  const t0Ref   = useRef(0);

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [logs]);
  useEffect(() => () => { stopPoll(); stopEl(); }, []);

  const addLog = (type: string, message: string) => setLogs(p => [...p, {type, message}]);
  const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  const stopEl   = () => { if (elRef.current)   { clearInterval(elRef.current);   elRef.current   = null; } };
  const startEl  = () => { t0Ref.current = Date.now(); stopEl(); elRef.current = setInterval(() => setElapsed(((Date.now()-t0Ref.current)/1000).toFixed(1)), 500); };

  async function doOutlookRegister(): Promise<{email:string;password:string}|null> {
    setPhase("gen-outlook"); since.current = 0; startEl();
    addLog("start","🚀 步骤 1/2：注册新 Outlook 账号...");
    const body: Record<string,unknown> = { count:1, engine:outlookEngine, headless, wait:11, retries:2, proxyMode: proxy?"":"cf" };
    if (proxy) body.proxy = proxy;
    const r = await fetch(`${API}/tools/outlook/register`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) });
    const d = await r.json();
    if (!d.success) throw new Error(d.error||"Outlook 启动失败");
    const jid = d.jobId; setOlJobId(jid);
    addLog("log",`📋 Outlook 任务: ${jid}`);
    return new Promise((resolve, reject) => {
      pollRef.current = setInterval(async () => {
        try {
          const pr = await fetch(`${API}/tools/outlook/register/${jid}?since=${since.current}`);
          const pd = await pr.json();
          if (pd.logs) { pd.logs.forEach((l:LogEntry) => addLog(l.type, l.message)); since.current += pd.logs.length; }
          if (pd.status === "done" || pd.status === "error") {
            stopPoll();
            const ok = (pd.result?.results??[]).find((x:OutlookResult) => x.success);
            if (ok) { setOutlookResult(ok); addLog("ok",`✅ Outlook: ${ok.email}`); resolve(ok); }
            else reject(new Error((pd.result?.results?.[0]?.error)||"Outlook 注册失败"));
          }
        } catch {}
      }, 2000);
    });
  }

  async function doOxylabsRegister(email:string, password:string, first:string="", last:string="") {
    setPhase("register-oxylabs"); since.current = 0; startEl();
    addLog("log",""); addLog("start","🌱 步骤 2/2：注册 Oxylabs 账号...");
    addLog("log",`📧 邮箱: ${email}`);
    if (capsolverKey) addLog("log","🔑 CapSolver API Key 已提供 → 自动解决 CF Managed Challenge");
    if (cfClearance)  addLog("log","🍪 手动 cf_clearance 已提供 → 跳过 CF 挑战");
    const body: Record<string,unknown> = { email, password, headless };
    if (proxy)        body.proxy        = proxy;
    if (first)        body.first_name   = first;
    if (last)         body.last_name    = last;
    if (capsolverKey) body.capsolverKey = capsolverKey;
    if (cfClearance)  body.cfClearance  = cfClearance;
    const r = await fetch(`${API}/tools/oxylabs/register`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) });
    const d = await r.json();
    if (!d.success) throw new Error(d.error||"Oxylabs 启动失败");
    const jid = d.jobId; setWsJobId(jid);
    addLog("log",`📋 Oxylabs 任务: ${jid}`);
    pollRef.current = setInterval(async () => {
      try {
        const pr = await fetch(`${API}/tools/oxylabs/register/${jid}?since=${since.current}`);
        const pd = await pr.json();
        if (pd.logs) { pd.logs.forEach((l:LogEntry) => addLog(l.type, l.message)); since.current += pd.logs.length; }
        if (pd.status === "done" || pd.status === "error") {
          stopPoll(); stopEl();
          const res: OxylabsResult = pd.result?.result ?? pd.result ?? {};
          setOxylabsResult(res);
          if (res.success) { addLog("ok","✅ Oxylabs 注册成功！"); setPhase("done"); }
          else { addLog("error",`❌ 失败: ${res.error||"未知"}`); setPhase("error"); }
        }
      } catch {}
    }, 2000);
  }

  async function start() {
    setPhase("gen-outlook"); setLogs([]); setOutlookResult(null); setOxylabsResult(null);
    try {
      if (useManual) {
        setOutlookResult({success:true, email:manualEmail, password:manualPassword});
        await doOxylabsRegister(manualEmail, manualPassword, manualFirst, manualLast);
      } else {
        const ol = await doOutlookRegister();
        if (ol) await doOxylabsRegister(ol.email, ol.password);
      }
    } catch (e) { stopPoll(); stopEl(); addLog("error",`❌ ${String(e)}`); setPhase("error"); }
  }

  function stop() {
    stopPoll(); stopEl();
    if (wsJobId) fetch(`${API}/tools/oxylabs/register/${wsJobId}`, {method:"DELETE"}).catch(()=>{});
    if (olJobId) fetch(`${API}/tools/outlook/register/${olJobId}`, {method:"DELETE"}).catch(()=>{});
    setPhase("error"); addLog("warn","⚠️ 用户手动停止");
  }

  const isBusy = phase === "gen-outlook" || phase === "register-oxylabs";

  const copyAll = () => {
    if (!oxylabsResult) return;
    const lines = [
      "Oxylabs 账号",
      `邮箱: ${oxylabsResult.email}`,
      `密码: ${oxylabsResult.password}`,
      oxylabsResult.first_name ? `姓名: ${oxylabsResult.first_name} ${oxylabsResult.last_name}` : "",
      oxylabsResult.username   ? `用户名: ${oxylabsResult.username}` : "",
    ].filter(Boolean).join("\n");
    navigator.clipboard.writeText(lines);
  };

  return (
    <div className="space-y-4 max-w-4xl mx-auto">
      {/* Header */}
      <div className="bg-gradient-to-r from-[#161b22] to-[#1c2128] border border-[#21262d] rounded-xl p-5">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-green-500 to-emerald-600 flex items-center justify-center text-xl shadow-lg">🌱</div>
          <div>
            <h2 className="text-base font-bold text-white">Oxylabs 注册工作流</h2>
            <p className="text-[11px] text-gray-500">全自动：生成 Outlook → 注册 dashboard.oxylabs.io</p>
          </div>
          {isBusy && (
            <div className="ml-auto flex items-center gap-2 text-[11px] text-green-400">
              <div className="w-1.5 h-1.5 bg-green-400 rounded-full animate-pulse"/>运行中 {elapsed}s
            </div>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Step n={1} label="生成 Outlook 账号" desc="patchright 自动注册 outlook.com 邮箱"
            active={phase==="gen-outlook"} done={!!outlookResult?.success}
            error={phase==="error" && !outlookResult?.success}/>
          <Step n={2} label="注册 Oxylabs" desc="camoufox Firefox + SEON 指纹 + CF bypass → dashboard.oxylabs.io"
            active={phase==="register-oxylabs"} done={phase==="done"}
            error={phase==="error" && !!outlookResult?.success}/>
        </div>
      </div>

      <div className="grid grid-cols-5 gap-4">
        {/* Config panel */}
        <div className="col-span-2 space-y-3">
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="text-[11px] text-gray-400 font-semibold uppercase tracking-wide">模式</div>
            <div className="flex gap-2">
              <button onClick={()=>setUseManual(false)} className={`flex-1 text-xs py-1.5 rounded-lg border transition-all ${!useManual?"bg-green-600/20 border-green-500/40 text-green-400":"bg-transparent border-[#21262d] text-gray-600 hover:border-[#30363d]"}`}>🔁 全自动</button>
              <button onClick={()=>setUseManual(true)}  className={`flex-1 text-xs py-1.5 rounded-lg border transition-all ${useManual?"bg-purple-600/20 border-purple-500/40 text-purple-400":"bg-transparent border-[#21262d] text-gray-600 hover:border-[#30363d]"}`}>✉️ 手动邮箱</button>
            </div>
          </div>

          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 space-y-3">
            <div className="text-[11px] text-gray-400 font-semibold uppercase tracking-wide">配置</div>
            {useManual ? (
              <>
                {([
                  ["Outlook 邮箱",   "email",    "text",     manualEmail,    setManualEmail],
                  ["Outlook 密码",   "password", "password", manualPassword, setManualPassword],
                  ["名（可选）",      "first",    "text",     manualFirst,    setManualFirst],
                  ["姓（可选）",      "last",     "text",     manualLast,     setManualLast],
                ] as const).map(([label, , type, val, set]) => (
                  <div key={label}>
                    <label className="text-[10px] text-gray-500 mb-1 block">{label}</label>
                    <input value={val} onChange={e => set(e.target.value)} type={type}
                      className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs text-gray-200 outline-none focus:border-green-500/50 placeholder-gray-700"/>
                  </div>
                ))}
              </>
            ) : (
              <div>
                <label className="text-[10px] text-gray-500 mb-1 block">Outlook 引擎</label>
                <select value={outlookEngine} onChange={e=>setOutlookEngine(e.target.value)}
                  className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs text-gray-300 outline-none">
                  <option value="patchright">patchright（推荐）</option>
                  <option value="playwright">playwright</option>
                  <option value="camoufox">camoufox</option>
                </select>
              </div>
            )}
            <div>
              <label className="text-[10px] text-gray-500 mb-1 block">代理（可选）</label>
              <input value={proxy} onChange={e=>setProxy(e.target.value)} placeholder="socks5://user:pass@host:port"
                className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-xs font-mono text-gray-300 outline-none focus:border-green-500/50 placeholder-gray-700"/>
            </div>

            {/* CapSolver Key field */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-[10px] text-gray-500">CapSolver Key（CF 解决必填）</label>
                <button onClick={()=>setShowCapsolverInfo(v=>!v)} className="text-[9px] text-blue-500 hover:text-blue-400">
                  {showCapsolverInfo?"▲ 收起":"▼ 说明"}
                </button>
              </div>
              <input
                value={capsolverKey}
                onChange={e=>setCapsolverKey(e.target.value)}
                type="password"
                placeholder="CAP-xxxxxxxxxxxxxxxxxxxxxxxx"
                className={`w-full bg-[#0d1117] border rounded-lg px-3 py-2 text-xs font-mono text-gray-300 outline-none transition-colors placeholder-gray-700 ${capsolverKey?"border-yellow-500/50 focus:border-yellow-400":"border-[#21262d] focus:border-yellow-500/50"}`}
              />
              {showCapsolverInfo && (
                <div className="mt-2 p-2 bg-yellow-500/5 border border-yellow-500/20 rounded-lg text-[10px] text-yellow-400/80 space-y-1">
                  <div className="font-semibold text-yellow-400">⚡ 为什么需要 CapSolver？</div>
                  <div>Oxylabs 注册 API 使用 CF Managed Challenge（非交互式指纹验证）</div>
                  <div>自动化浏览器在VPS上无法通过此挑战，需要 CapSolver 的真实硬件解决</div>
                  <div className="pt-1 border-t border-yellow-500/20">
                    <span className="text-gray-500">获取：</span>
                    <a href="https://capsolver.com" target="_blank" rel="noopener" className="text-yellow-400 hover:underline ml-1">capsolver.com</a>
                    <span className="text-gray-600 ml-2">~$0.001/次，注册有免费额度</span>
                  </div>
                </div>
              )}
              {!capsolverKey && (
                <div className="mt-1 text-[9px] text-amber-500/70">
                  ⚠ 无 Key 时将尝试 CF 页面导航绕过（可能失败）
                </div>
              )}
            </div>

            {/* Manual cf_clearance fallback */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-[10px] text-gray-500">手动 cf_clearance（可选备用）</label>
                <button onClick={()=>setShowCfClearanceInfo(v=>!v)} className="text-[9px] text-blue-500 hover:text-blue-400">
                  {showCfClearanceInfo?"▲ 收起":"▼ 说明"}
                </button>
              </div>
              <input
                value={cfClearance}
                onChange={e=>setCfClearance(e.target.value)}
                type="password"
                placeholder="从浏览器粘贴 cf_clearance cookie 值..."
                className={`w-full bg-[#0d1117] border rounded-lg px-3 py-2 text-xs font-mono text-gray-300 outline-none transition-colors placeholder-gray-700 ${cfClearance?"border-blue-500/50 focus:border-blue-400":"border-[#21262d] focus:border-blue-500/30"}`}
              />
              {showCfClearanceInfo && (
                <div className="mt-2 p-2 bg-blue-500/5 border border-blue-500/20 rounded-lg text-[10px] text-blue-400/80 space-y-1">
                  <div className="font-semibold text-blue-400">🍪 手动 cf_clearance 用法</div>
                  <div>在真实浏览器中访问 <a href="https://dashboard.oxylabs.io" target="_blank" rel="noopener" className="underline">dashboard.oxylabs.io</a></div>
                  <div>打开 DevTools → Application → Cookies → 找到 <code className="bg-blue-900/30 px-1 rounded">cf_clearance</code></div>
                  <div>复制其 Value 粘贴到此处，有效期约 30 分钟</div>
                  <div className="text-gray-600 pt-1 border-t border-blue-500/20">无需 CapSolver Key，免费绕过 CF</div>
                </div>
              )}
              {cfClearance && (
                <div className="mt-1 text-[9px] text-blue-400/70">✓ cf_clearance 长度: {cfClearance.length}</div>
              )}
            </div>

            <label className="flex items-center gap-2 cursor-pointer select-none">
              <div onClick={()=>setHeadless(v=>!v)} className={`w-9 h-5 rounded-full relative transition-colors cursor-pointer ${headless?"bg-green-600":"bg-gray-700"}`}>
                <div className="w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all" style={{left:headless?"calc(100% - 18px)":"2px"}}/>
              </div>
              <span className="text-[11px] text-gray-400">无界面模式</span>
            </label>
            <button onClick={start}
              disabled={isBusy||(useManual&&(!manualEmail||!manualPassword))}
              className="w-full py-2.5 rounded-xl text-sm font-semibold transition-all disabled:opacity-40 disabled:cursor-not-allowed bg-gradient-to-r from-green-600 to-emerald-600 hover:from-green-500 hover:to-emerald-500 text-white shadow-lg">
              {isBusy ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin"/>运行中...
                </span>
              ) : useManual ? "🌱 注册 Oxylabs" : "🚀 全自动注册"}
            </button>
            {isBusy && (
              <button onClick={stop} className="w-full py-1.5 rounded-lg text-xs border border-red-500/30 text-red-400 hover:bg-red-500/10 transition-all">
                ⏹ 停止
              </button>
            )}
          </div>

          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-3 space-y-1">
            <div className="text-[10px] text-gray-600">目标站点</div>
            <a href="https://dashboard.oxylabs.io/registration" target="_blank" rel="noopener noreferrer"
              className="text-[11px] text-green-400 hover:text-green-300 flex items-center gap-1.5 group">
              <span>🔗</span><span className="group-hover:underline">dashboard.oxylabs.io/registration</span>
            </a>
            <div className="text-[10px] text-gray-700 mt-1 leading-relaxed">
              表单字段：Name · Surname · Email · Password<br/>
              CF 保护：Managed Challenge (cType=managed)<br/>
              注册后需邮件激活账号
            </div>
          </div>
        </div>

        {/* Log + Result panel */}
        <div className="col-span-3 space-y-3">
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[11px] text-gray-500 font-mono">实时日志</span>
              {logs.length > 0 && (
                <button onClick={()=>setLogs([])} className="text-[10px] text-gray-700 hover:text-gray-500">清空</button>
              )}
            </div>
            <div ref={logRef}
              className="bg-[#0d1117] rounded-xl border border-[#21262d] overflow-y-auto font-mono text-[11px] p-3 space-y-0.5"
              style={{height:300}}>
              {logs.length === 0
                ? <div className="text-gray-700 text-center py-8">等待开始...</div>
                : logs.map((l,i) => (
                  <div key={i} className={`leading-relaxed ${colorClass(l.type)}`}>
                    <span className="text-gray-700 select-none">{String(i+1).padStart(3,"0")} </span>
                    {l.message}
                  </div>
                ))
              }
            </div>
          </div>

          {phase === "done" && oxylabsResult?.success && (
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
                {([
                  { label:"📧 邮箱（Outlook）", value: oxylabsResult.email },
                  { label:"🔒 密码",            value: oxylabsResult.password },
                  oxylabsResult.username ? { label:"👤 用户名", value: oxylabsResult.username } : null,
                  (oxylabsResult.first_name||oxylabsResult.last_name)
                    ? { label:"🙍 姓名", value: `${oxylabsResult.first_name??""} ${oxylabsResult.last_name??""}`.trim() }
                    : null,
                  oxylabsResult.elapsed ? { label:"⏱ 耗时", value: oxylabsResult.elapsed } : null,
                ].filter(Boolean) as {label:string;value:string}[]).map((item,i) => (
                  <div key={i} className="bg-[#0d1117] rounded-lg px-3 py-2 flex items-center justify-between gap-2">
                    <div>
                      <div className="text-[9px] text-gray-600">{item.label}</div>
                      <div className="text-xs font-mono text-gray-200 mt-0.5 break-all">{item.value}</div>
                    </div>
                    <button onClick={()=>navigator.clipboard.writeText(item.value)}
                      className="shrink-0 text-[10px] px-2 py-0.5 bg-[#21262d] border border-[#30363d] rounded text-gray-400 hover:text-white transition-all">
                      复制
                    </button>
                  </div>
                ))}
              </div>
              <div className="pt-1 border-t border-emerald-500/20">
                <a href="https://dashboard.oxylabs.io" target="_blank" rel="noopener noreferrer"
                  className="text-[11px] text-green-400 hover:underline">
                  🚀 打开 Oxylabs Dashboard →
                </a>
              </div>
            </div>
          )}

          {phase === "error" && !oxylabsResult?.success && (
            <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4">
              <div className="flex items-start gap-3">
                <div className="text-2xl">❌</div>
                <div className="flex-1">
                  <div className="text-sm text-red-400 font-semibold">注册失败</div>
                  <div className="text-[11px] text-red-400/70 mt-1 break-words">{oxylabsResult?.error||"请查看日志"}</div>
                  {(oxylabsResult?.error||"").includes("CAPSOLVER_API_KEY") && (
                    <div className="mt-2 p-2 bg-yellow-500/10 border border-yellow-500/20 rounded-lg text-[10px] text-yellow-400">
                      💡 在左侧配置面板填写 CapSolver Key 后重试
                      （获取：<a href="https://capsolver.com" target="_blank" className="underline">capsolver.com</a>）
                    </div>
                  )}
                </div>
              </div>
              <button onClick={()=>{setPhase("idle");setLogs([]);setOutlookResult(null);setOxylabsResult(null);}}
                className="mt-3 text-[11px] px-4 py-1.5 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 hover:bg-red-500/20 w-full">
                重置
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
        <div className="text-[11px] text-gray-600 space-y-1">
          <div className="font-semibold text-gray-500 mb-2">⚙️ 工作流说明</div>
          <div>• <span className="text-gray-400">步骤 1</span>：patchright 自动注册 outlook.com 邮箱（CF 代理池绕过 CAPTCHA）</div>
          <div>• <span className="text-gray-400">步骤 2</span>：camoufox Firefox 打开注册页，SEON 指纹采集，提交后遇到 CF Managed Challenge</div>
          <div>• <span className="text-gray-400">CF 绕过策略</span>：① CapSolver AntiCloudflareTask（推荐，需 Key）→ 注入 cf_clearance 重发 POST</div>
          <div>• <span className="text-gray-200 ml-16">② 导航到 CF 挑战 URL（Xvfb DISPLAY=:99），多居民 IP 轮换（HKT/HKBN）</span></div>
          <div>• <span className="text-gray-400">注意</span>：注册后 Oxylabs 发送激活邮件，进入 Outlook 收件箱点击激活链接</div>
        </div>
      </div>
    </div>
  );
}
