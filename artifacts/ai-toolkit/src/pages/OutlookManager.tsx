import { useState, useRef, useEffect } from "react";

interface MailMsg { id: string; subject: string; from: string; fromName: string; receivedAt: string; preview: string; isRead: boolean; }
interface Profile { displayName?: string; mail?: string; userPrincipalName?: string; }
interface RegAccount { email: string; password: string; }
interface SseEvent { type: string; message: string; account?: RegAccount; }
type Step = 1 | 2 | 3 | 4;

const STEPS = [
  { n: 1, label: "创建临时邮箱",        icon: "📬" },
  { n: 2, label: "注册 Outlook 账号",   icon: "📝" },
  { n: 3, label: "获取 OAuth2 Token",   icon: "🔑" },
  { n: 4, label: "读取收件箱",          icon: "📥" },
];

export default function OutlookManager() {
  const [step, setStep]               = useState<Step>(1);
  const [copied, setCopied]           = useState<string | null>(null);

  // Step 1 – 临时邮箱
  const [tmpEmail, setTmpEmail]       = useState("");
  const [tmpPass,  setTmpPass]        = useState("");
  const [tmpToken, setTmpToken]       = useState("");
  const [tmpDomains, setTmpDomains]   = useState<string[]>([]);
  const [tmpDomain, setTmpDomain]     = useState("");
  const [step1Busy, setStep1Busy]     = useState(false);
  const [step1Msgs, setStep1Msgs]     = useState<Array<{ from: string; subject: string; intro: string }>>([]);
  const pollRef = useRef<ReturnType<typeof setInterval>|null>(null);

  // Step 2 – 自动化注册
  const [regEngine,  setRegEngine]   = useState("patchright");
  const [regCount,   setRegCount]    = useState(1);
  const [regProxy,   setRegProxy]    = useState("");   // 代理（多行/逗号分隔均支持）
  const [regWait,    setRegWait]     = useState(11);
  const [regDelay,   setRegDelay]    = useState(5);
  const [regRetries, setRegRetries]  = useState(2);
  const [regHeadless,setRegHeadless] = useState(true);
  const [regBusy,    setRegBusy]     = useState(false);
  const [regLogs,    setRegLogs]     = useState<SseEvent[]>([]);
  const [regAccounts,setRegAccounts] = useState<RegAccount[]>([]);
  const [regJobId,   setRegJobId]    = useState<string|null>(null);
  const [regStatus,  setRegStatus]   = useState<string>("idle");
  const logsEndRef  = useRef<HTMLDivElement>(null);
  const pollRef2    = useRef<ReturnType<typeof setInterval>|null>(null);
  const sinceRef    = useRef(0);

  // CF 代理池
  type CfPoolStatus = { available: number; used_total: number; pool: {ip:string;latency:number}[] };
  const [proxyMode,   setProxyMode]   = useState<"manual"|"cf">("manual");
  const [cfPool,      setCfPool]      = useState<CfPoolStatus|null>(null);
  const [cfPoolBusy,  setCfPoolBusy]  = useState(false);

  const loadCfPoolStatus = async () => {
    try {
      const r = await fetch("/api/tools/cf-pool/status");
      const d = await r.json() as CfPoolStatus & { success: boolean };
      if (d.success) setCfPool(d);
    } catch {}
  };

  const refreshCfPool = async () => {
    setCfPoolBusy(true);
    try {
      const r = await fetch("/api/tools/cf-pool/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count: 60, target: 20, threads: 8, port: 443, maxLatency: 1000 }),
      });
      const d = await r.json() as { success: boolean; new_ips: number; total_available: number; pool: {ip:string;latency:number}[] };
      if (d.success) {
        setCfPool({ available: d.total_available, used_total: 0, pool: d.pool });
      }
    } catch {}
    setCfPoolBusy(false);
  };

  useEffect(() => { if (proxyMode === "cf") loadCfPoolStatus(); }, [proxyMode]);

  useEffect(() => { logsEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [regLogs]);

  const stopPolling = () => {
    if (pollRef2.current) { clearInterval(pollRef2.current); pollRef2.current = null; }
  };

  const startPolling = (jobId: string) => {
    sinceRef.current = 0;
    stopPolling();
    pollRef2.current = setInterval(async () => {
      try {
        const r = await fetch(`/api/tools/outlook/register/${jobId}?since=${sinceRef.current}`);
        const d = await r.json() as {
          success: boolean; status: string; logs: SseEvent[];
          accounts: RegAccount[]; nextSince: number;
        };
        if (!d.success) return;

        if (d.logs?.length) {
          setRegLogs(prev => [...prev, ...d.logs]);
          sinceRef.current = d.nextSince;
        }
        if (d.accounts?.length) {
          setRegAccounts(d.accounts);
        }
        setRegStatus(d.status);

        if (d.status === "done" || d.status === "stopped") {
          stopPolling();
          setRegBusy(false);
        }
      } catch {}
    }, 2000);
  };

  const startRegister = async () => {
    if (regBusy) return;
    setRegBusy(true);
    setRegLogs([]);
    setRegAccounts([]);
    setRegJobId(null);
    setRegStatus("running");
    sinceRef.current = 0;

    try {
      const r = await fetch("/api/tools/outlook/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          count: regCount,
          proxies: proxyMode === "manual" ? regProxy : "",
          proxyMode: proxyMode === "cf" ? "cf" : "",
          cfPort: 443,
          headless: regHeadless,
          delay: regDelay, engine: regEngine, wait: regWait, retries: regRetries,
        }),
      });
      const d = await r.json() as { success: boolean; jobId?: string; message?: string };
      if (d.success && d.jobId) {
        setRegJobId(d.jobId);
        setRegLogs([{ type: "start", message: d.message ?? "任务已启动，正在轮询进度..." }]);
        startPolling(d.jobId);
      } else {
        setRegLogs([{ type: "error", message: "启动失败" }]);
        setRegBusy(false);
      }
    } catch (e) {
      setRegLogs([{ type: "error", message: String(e) }]);
      setRegBusy(false);
    }
  };

  const stopRegister = async () => {
    stopPolling();
    if (regJobId) {
      try { await fetch(`/api/tools/outlook/register/${regJobId}`, { method: "DELETE" }); } catch {}
    }
    setRegBusy(false);
    setRegStatus("stopped");
    setRegLogs(prev => [...prev, { type: "warn", message: "⚠ 用户手动停止" }]);
  };

  const exportRegAccounts = (fmt: "txt"|"csv"|"json") => {
    let content = "";
    if (fmt === "txt")  content = regAccounts.map(a => `${a.email}----${a.password}`).join("\n");
    if (fmt === "csv")  content = "email,password\n" + regAccounts.map(a => `${a.email},${a.password}`).join("\n");
    if (fmt === "json") content = JSON.stringify(regAccounts, null, 2);
    const blob = new Blob([content], { type: "text/plain" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `outlook_accounts_${Date.now()}.${fmt}`; a.click();
  };

  // Step 3 – OAuth2
  const [clientId,  setClientId]      = useState("9e5f94bc-e8a4-4e73-b8be-63364c29d753");
  const [refreshTok, setRefreshTok]   = useState("");
  const [tenantId,  setTenantId]      = useState("common");
  const [accessTok, setAccessTok]     = useState("");
  const [oauthBusy, setOauthBusy]     = useState(false);
  const [oauthErr,  setOauthErr]      = useState("");
  const [profile,   setProfile]       = useState<Profile|null>(null);

  // Device Code Flow 状态
  const [dcBusy,    setDcBusy]        = useState(false);
  const [dcCode,    setDcCode]        = useState("");      // user_code（显示给用户的短码）
  const [dcDevice,  setDcDevice]      = useState("");      // device_code（内部轮询用）
  const [dcUri,     setDcUri]         = useState("");      // verification_uri
  const [dcExpiry,  setDcExpiry]      = useState(0);       // 过期时间戳
  const [dcStatus,  setDcStatus]      = useState<"idle"|"waiting"|"done"|"error">("idle");
  const [dcErr,     setDcErr]         = useState("");
  const [dcInterval,setDcInterval]    = useState(5);
  const dcPollRef   = useRef<ReturnType<typeof setInterval>|null>(null);

  const stopDcPoll = () => { if (dcPollRef.current) { clearInterval(dcPollRef.current); dcPollRef.current = null; } };

  const startDeviceCodeFlow = async () => {
    setDcBusy(true); setDcErr(""); setDcCode(""); setDcDevice(""); setDcStatus("idle");
    stopDcPoll();
    try {
      const r = await fetch("/api/tools/outlook/device-code", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clientId, tenantId }),
      });
      const d = await r.json() as {
        success: boolean; deviceCode?: string; userCode?: string;
        verificationUri?: string; expiresIn?: number; interval?: number; error?: string;
      };
      if (!d.success || !d.deviceCode) { setDcErr(d.error ?? "获取设备码失败"); setDcBusy(false); return; }
      setDcCode(d.userCode ?? "");
      setDcDevice(d.deviceCode);
      setDcUri(d.verificationUri ?? "https://microsoft.com/devicelogin");
      setDcExpiry(Date.now() + (d.expiresIn ?? 900) * 1000);
      setDcInterval(d.interval ?? 5);
      setDcStatus("waiting");
      setDcBusy(false);

      // 开始轮询
      const pollMs = Math.max((d.interval ?? 5) * 1000, 5000);
      const savedDeviceCode = d.deviceCode;
      dcPollRef.current = setInterval(async () => {
        if (Date.now() > (d.expiresIn ?? 900) * 1000 + Date.now()) { stopDcPoll(); setDcStatus("error"); setDcErr("授权码已过期"); return; }
        try {
          const pr = await fetch("/api/tools/outlook/device-poll", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ deviceCode: savedDeviceCode, clientId, tenantId }),
          });
          const pd = await pr.json() as {
            success: boolean; pending?: boolean; slowDown?: boolean;
            accessToken?: string; refreshToken?: string; error?: string;
          };
          if (pd.success && pd.accessToken) {
            stopDcPoll();
            setAccessTok(pd.accessToken);
            if (pd.refreshToken) setRefreshTok(pd.refreshToken);
            setDcStatus("done");
            // 获取用户信息
            try {
              const profR = await fetch("/api/tools/outlook/profile", { headers: { "x-access-token": pd.accessToken } });
              const profD = await profR.json() as { success: boolean; profile?: Profile };
              if (profD.success && profD.profile) setProfile(profD.profile);
            } catch {}
          } else if (!pd.pending) {
            stopDcPoll(); setDcStatus("error"); setDcErr(pd.error ?? "授权失败");
          }
        } catch {}
      }, pollMs);
    } catch (e) { setDcErr(String(e)); setDcBusy(false); }
  };

  useEffect(() => () => stopDcPoll(), []);

  // Step 4 – 邮件
  const [messages,  setMessages]      = useState<MailMsg[]>([]);
  const [msgBusy,   setMsgBusy]       = useState(false);
  const [msgErr,    setMsgErr]        = useState("");
  const [folder,    setFolder]        = useState("inbox");
  const [search,    setSearch]        = useState("");
  const [selMsg,    setSelMsg]        = useState<string|null>(null);

  const copy = (text: string, k: string) => {
    navigator.clipboard.writeText(text);
    setCopied(k);
    setTimeout(() => setCopied(null), 1200);
  };

  // ── Step 1: 创建临时邮箱 ──────────────────────────────
  const loadDomains = async () => {
    if (tmpDomains.length) return;
    const r = await fetch("/api/tools/email/domains");
    const d = await r.json();
    if (d.domains?.length) { setTmpDomains(d.domains); setTmpDomain(d.domains[0]); }
  };

  const createTmpEmail = async () => {
    setStep1Busy(true);
    const user = Math.random().toString(36).slice(2, 12) + Math.floor(Math.random() * 999);
    const pass = Math.random().toString(36).slice(2, 14);
    const address = `${user}@${tmpDomain}`;
    try {
      const r = await fetch("/api/tools/email/create", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address, password: pass }),
      });
      const d = await r.json();
      if (d.success) {
        setTmpEmail(address); setTmpPass(pass); setTmpToken(d.token ?? "");
        startPollMailTM(d.token ?? "");
      }
    } catch {}
    setStep1Busy(false);
  };

  const startPollMailTM = (tok: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch("/api/tools/email/messages", { headers: { "x-mail-token": tok } });
        const d = await r.json();
        if (d.success) setStep1Msgs(d.messages ?? []);
      } catch {}
    }, 5000);
  };

  // ── Step 3: OAuth2 刷新 ──────────────────────────────
  const doRefresh = async () => {
    setOauthBusy(true); setOauthErr(""); setProfile(null);
    try {
      const r = await fetch("/api/tools/outlook/refresh-token", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clientId, refreshToken: refreshTok, tenantId }),
      });
      const d = await r.json();
      if (d.success) {
        setAccessTok(d.accessToken);
        if (d.refreshToken) setRefreshTok(d.refreshToken);
        const pr = await fetch("/api/tools/outlook/profile", { headers: { "x-access-token": d.accessToken } });
        const pd = await pr.json();
        if (pd.success) setProfile(pd.profile);
      } else { setOauthErr(d.error ?? "OAuth2 失败"); }
    } catch (e) { setOauthErr(String(e)); }
    setOauthBusy(false);
  };

  // ── Step 4: 读取邮件 ──────────────────────────────────
  const fetchMessages = async () => {
    if (!accessTok) return;
    setMsgBusy(true); setMsgErr("");
    try {
      const r = await fetch("/api/tools/outlook/messages", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accessToken: accessTok, folder, top: 30, search: search || undefined }),
      });
      const d = await r.json();
      if (d.success) setMessages(d.messages);
      else setMsgErr(d.error ?? "获取失败");
    } catch (e) { setMsgErr(String(e)); }
    setMsgBusy(false);
  };

  const extractCode = (text: string) => text?.match(/\b(\d{6})\b/)?.[1] ?? text?.match(/\b([A-Z0-9]{8,})\b/)?.[1] ?? null;

  const Pill = ({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) => (
    <button onClick={onClick} className={`px-3 py-1 rounded-full text-xs border transition-all ${active ? "bg-blue-600 text-white border-blue-500" : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"}`}>{children}</button>
  );

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">Outlook 邮箱管理工作流</h2>
        <p className="text-sm text-gray-400">
          参考 <span className="text-blue-400">outlook-batch-manager</span> 设计，整合 MailTM 临时邮箱注册 → 微软 OAuth2 取件的完整流程
        </p>
      </div>

      {/* 步骤导航 */}
      <div className="flex items-center gap-0">
        {STEPS.map((s, i) => (
          <div key={s.n} className="flex items-center flex-1">
            <button
              onClick={() => { setStep(s.n as Step); if (s.n === 1) loadDomains(); }}
              className={`flex flex-col items-center gap-1 flex-1 py-3 px-2 rounded-xl border transition-all ${
                step === s.n ? "bg-blue-500/10 border-blue-500/40 text-white" : "bg-[#161b22] border-[#21262d] text-gray-500 hover:text-gray-300"
              }`}
            >
              <span className="text-lg">{s.icon}</span>
              <span className="text-[11px] font-medium text-center leading-tight">{s.label}</span>
              <span className={`text-[10px] w-4 h-4 rounded-full flex items-center justify-center font-bold ${step === s.n ? "bg-blue-500 text-white" : "bg-[#30363d] text-gray-500"}`}>{s.n}</span>
            </button>
            {i < STEPS.length - 1 && <div className="w-4 h-px bg-[#30363d] shrink-0" />}
          </div>
        ))}
      </div>

      {/* ── Step 1: 临时邮箱 ── */}
      {step === 1 && (
        <div className="space-y-4">
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-4">
            <h3 className="text-sm font-semibold text-gray-300">创建 MailTM 临时邮箱</h3>
            <div className="flex gap-3">
              <select value={tmpDomain} onChange={(e) => setTmpDomain(e.target.value)} onClick={loadDomains}
                className="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-300 focus:outline-none focus:border-blue-500">
                {tmpDomains.length === 0 ? <option>加载域名中...</option> : tmpDomains.map((d) => <option key={d}>{d}</option>)}
              </select>
              <button onClick={createTmpEmail} disabled={step1Busy || !tmpDomain}
                className="px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-all">
                {step1Busy ? "创建中..." : "创建邮箱"}
              </button>
            </div>
            {tmpEmail && (
              <div className="space-y-2">
                {[
                  { label: "邮箱地址", value: tmpEmail, k: "te" },
                  { label: "密码",     value: tmpPass,  k: "tp" },
                  { label: "Token",    value: tmpToken, k: "tt" },
                ].map(({ label, value, k }) => (
                  <div key={k} className="flex items-center gap-3 bg-[#0d1117] rounded-lg px-3 py-2">
                    <span className="text-xs text-gray-500 w-16 shrink-0">{label}</span>
                    <span className="text-xs font-mono text-emerald-300 flex-1 truncate">{value}</span>
                    <button onClick={() => copy(value, k)} className={`text-xs px-2 py-0.5 rounded border shrink-0 transition-all ${copied === k ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500 hover:text-white"}`}>
                      {copied === k ? "✓" : "复制"}
                    </button>
                  </div>
                ))}
                <div className="flex items-center gap-2 pt-1">
                  <span className="flex items-center gap-1 text-xs text-emerald-400"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />实时监听中</span>
                  <span className="text-xs text-gray-500">每 5 秒刷新 · {step1Msgs.length} 封</span>
                </div>
                {step1Msgs.length > 0 && (
                  <div className="space-y-2 max-h-40 overflow-y-auto">
                    {step1Msgs.map((m, i) => {
                      const code = extractCode(m.intro || m.subject);
                      return (
                        <div key={i} className="bg-[#161b22] rounded-lg px-3 py-2 flex items-start justify-between gap-2">
                          <div className="flex-1 min-w-0">
                            <p className="text-xs font-medium text-gray-200 truncate">{m.subject}</p>
                            <p className="text-[11px] text-gray-500">{m.from}</p>
                          </div>
                          {code && <button onClick={() => copy(code, `vc-${i}`)} className={`text-xs px-2 py-0.5 rounded border font-mono font-bold ${copied === `vc-${i}` ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-yellow-500/10 border-yellow-500/30 text-yellow-400"}`}>{copied === `vc-${i}` ? "已复制" : `验证码: ${code}`}</button>}
                        </div>
                      );
                    })}
                  </div>
                )}
                <button onClick={() => setStep(2)}
                  className="w-full py-2 bg-emerald-600 hover:bg-emerald-700 rounded-lg text-white text-sm font-medium transition-all">
                  用此邮箱注册 Outlook → 下一步
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Step 2: 自动化注册控制台 ── */}
      {step === 2 && (
        <div className="space-y-4">
          {/* 配置面板 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-300">Outlook 批量注册控制台</h3>
              <span className="text-[10px] text-gray-500 bg-[#0d1117] border border-[#30363d] rounded px-2 py-0.5">
                基于 outlook-batch-manager 核心逻辑
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {/* 引擎 */}
              <div>
                <label className="text-[11px] text-gray-500 mb-1 block">浏览器引擎</label>
                <select value={regEngine} onChange={e => setRegEngine(e.target.value)} disabled={regBusy}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-blue-500 disabled:opacity-50">
                  <option value="patchright">patchright（推荐）</option>
                  <option value="playwright">playwright</option>
                </select>
              </div>
              {/* 数量 */}
              <div>
                <label className="text-[11px] text-gray-500 mb-1 block">注册数量</label>
                <input type="number" min={1} max={999} value={regCount} onChange={e => setRegCount(+e.target.value)} disabled={regBusy}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-blue-500 disabled:opacity-50" />
              </div>
              {/* bot_protection_wait */}
              <div>
                <label className="text-[11px] text-gray-500 mb-1 block">bot_protection_wait (秒)</label>
                <input type="number" min={3} max={30} value={regWait} onChange={e => setRegWait(+e.target.value)} disabled={regBusy}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-blue-500 disabled:opacity-50" />
              </div>
              {/* 间隔 */}
              <div>
                <label className="text-[11px] text-gray-500 mb-1 block">账号间隔 (秒)</label>
                <input type="number" min={2} max={30} value={regDelay} onChange={e => setRegDelay(+e.target.value)} disabled={regBusy}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-blue-500 disabled:opacity-50" />
              </div>
              {/* retries */}
              <div>
                <label className="text-[11px] text-gray-500 mb-1 block">CAPTCHA 重试次数</label>
                <input type="number" min={1} max={5} value={regRetries} onChange={e => setRegRetries(+e.target.value)} disabled={regBusy}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-blue-500 disabled:opacity-50" />
              </div>
              {/* headless */}
              <div className="flex flex-col">
                <label className="text-[11px] text-gray-500 mb-1 block">无头模式</label>
                <button onClick={() => setRegHeadless(h => !h)} disabled={regBusy}
                  className={`flex-1 text-xs rounded-lg border transition-all ${regHeadless ? "bg-emerald-500/15 border-emerald-500/30 text-emerald-400" : "bg-orange-500/10 border-orange-500/30 text-orange-400"} disabled:opacity-50`}>
                  {regHeadless ? "✓ headless=true" : "× headless=false"}
                </button>
              </div>
            </div>

            {/* 代理模式切换 */}
            <div>
              <label className="text-[11px] text-gray-500 mb-2 block">代理模式</label>
              <div className="flex gap-2 mb-3">
                <button
                  onClick={() => setProxyMode("manual")} disabled={regBusy}
                  className={`flex-1 py-1.5 text-xs rounded-lg border transition-all ${proxyMode === "manual" ? "bg-blue-500/20 border-blue-500/50 text-blue-300" : "bg-[#0d1117] border-[#30363d] text-gray-500 hover:text-gray-300"} disabled:opacity-50`}>
                  🔗 手动输入代理
                </button>
                <button
                  onClick={() => setProxyMode("cf")} disabled={regBusy}
                  className={`flex-1 py-1.5 text-xs rounded-lg border transition-all ${proxyMode === "cf" ? "bg-orange-500/20 border-orange-500/50 text-orange-300" : "bg-[#0d1117] border-[#30363d] text-gray-500 hover:text-gray-300"} disabled:opacity-50`}>
                  ☁️ CF IP 代理池
                </button>
              </div>

              {proxyMode === "manual" && (
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[10px] text-gray-600">每行一个，每账号自动轮换节点</span>
                  </div>
                  <textarea
                    value={regProxy}
                    onChange={e => setRegProxy(e.target.value)}
                    disabled={regBusy}
                    rows={regProxy.split('\n').filter(Boolean).length > 1 ? Math.min(5, regProxy.split('\n').length + 1) : 2}
                    placeholder={`socks5://user:pass@pool-us.quarkip.io:7777\nsocks5://user:pass@pool-us2.quarkip.io:7777`}
                    className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-xs font-mono text-gray-300 focus:outline-none focus:border-blue-500 disabled:opacity-50 placeholder-gray-600 resize-none"
                  />
                  {regProxy.split('\n').filter(l => l.trim()).length > 1 && (
                    <div className="mt-1 text-[10px] text-blue-400">
                      已输入 {regProxy.split('\n').filter(l => l.trim()).length} 个节点，将按账号顺序轮换
                    </div>
                  )}
                </div>
              )}

              {proxyMode === "cf" && (
                <div className="bg-[#0d1117] border border-orange-500/20 rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] text-orange-300 font-medium">☁️ CF IP 代理池</span>
                    <div className="flex items-center gap-2">
                      {cfPool && (
                        <span className={`text-[10px] px-2 py-0.5 rounded-full border ${cfPool.available > 0 ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400" : "bg-red-500/10 border-red-500/30 text-red-400"}`}>
                          {cfPool.available > 0 ? `✓ ${cfPool.available} 个可用` : "池空"}
                        </span>
                      )}
                      <button onClick={refreshCfPool} disabled={cfPoolBusy}
                        className="text-[10px] px-2 py-0.5 bg-orange-500/15 border border-orange-500/30 text-orange-400 rounded hover:bg-orange-500/25 transition-all disabled:opacity-50">
                        {cfPoolBusy ? (
                          <span className="flex items-center gap-1">
                            <span className="w-2 h-2 border border-orange-400/40 border-t-orange-400 rounded-full animate-spin" />
                            测速中…
                          </span>
                        ) : "🎲 生成 20 个 IP"}
                      </button>
                    </div>
                  </div>
                  <div className="text-[10px] text-gray-500">
                    每账号独占一个 CF IP + 独立指纹 + 独立机器ID，用后丢弃不重用
                  </div>
                  {cfPool && cfPool.pool.length > 0 && (
                    <div className="mt-1 max-h-24 overflow-y-auto space-y-0.5">
                      {cfPool.pool.slice(0, 8).map((item, i) => (
                        <div key={i} className="flex items-center justify-between text-[10px] font-mono">
                          <span className="text-gray-400">{item.ip}</span>
                          <span className="text-emerald-400">{item.latency}ms</span>
                        </div>
                      ))}
                      {cfPool.pool.length > 8 && (
                        <div className="text-[10px] text-gray-600">… 共 {cfPool.pool.length} 个 IP</div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* 指纹伪装说明 */}
            <div className="bg-blue-500/5 border border-blue-500/20 rounded-lg p-3 text-xs text-gray-400 space-y-1">
              <div className="text-blue-400 font-semibold mb-1">自动启用深度反检测技术：</div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11px]">
                <span>✓ Canvas 指纹噪点</span>
                <span>✓ WebGL 渲染器伪装</span>
                <span>✓ 音频指纹随机化</span>
                <span>✓ 唯一机器 ID 生成</span>
                <span>✓ 随机屏幕分辨率</span>
                <span>✓ 随机 User-Agent</span>
                <span>✓ 随机美国时区</span>
                <span>✓ 插件列表伪装</span>
              </div>
            </div>

            <div className="flex gap-3">
              <button onClick={startRegister} disabled={regBusy}
                className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-all">
                {regBusy ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    注册进行中...
                  </span>
                ) : "▶ 开始自动化注册"}
              </button>
              {regBusy && (
                <button onClick={stopRegister}
                  className="px-4 py-2.5 bg-red-600/80 hover:bg-red-700 rounded-lg text-white text-sm font-medium transition-all">
                  停止
                </button>
              )}
            </div>
          </div>

          {/* 实时日志流 */}
          {regLogs.length > 0 && (
            <div className={`bg-[#0d1117] border rounded-xl overflow-hidden transition-colors ${
              regStatus === "done" ? "border-purple-500/30" :
              regBusy ? "border-blue-500/30" : "border-[#21262d]"
            }`}>
              <div className="flex items-center justify-between px-4 py-2 border-b border-[#21262d]">
                <div className="flex items-center gap-2">
                  {regBusy && <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />}
                  {regStatus === "done" && <span className="w-2 h-2 rounded-full bg-purple-400" />}
                  <span className="text-xs text-gray-400 font-semibold">
                    {regBusy ? "注册中（每 2s 更新）" : regStatus === "done" ? "任务完成" : "实时日志"}
                  </span>
                  {regJobId && <span className="text-[10px] text-gray-600 font-mono">#{regJobId.slice(-8)}</span>}
                </div>
                <span className="text-[10px] text-gray-600">{regLogs.length} 条日志</span>
              </div>
              <div className="p-3 max-h-56 overflow-y-auto space-y-0.5 font-mono text-[11px]">
                {regLogs.map((ev, i) => (
                  <div key={i} className={`leading-relaxed ${
                    ev.type === "success" ? "text-emerald-400 font-bold" :
                    ev.type === "error"   ? "text-red-400" :
                    ev.type === "warn"    ? "text-yellow-400" :
                    ev.type === "start"   ? "text-blue-400" :
                    ev.type === "done"    ? "text-purple-300 font-semibold" :
                    "text-gray-500"
                  }`}>{ev.message}</div>
                ))}
                <div ref={logsEndRef} />
              </div>
            </div>
          )}

          {/* 已注册账号列表 */}
          {regAccounts.length > 0 && (
            <div className="bg-[#161b22] border border-emerald-500/20 rounded-xl overflow-hidden">
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#21262d]">
                <span className="text-xs text-emerald-400 font-semibold">✅ 注册成功 ({regAccounts.length} 个)</span>
                <div className="flex gap-1.5">
                  {(["txt","csv","json"] as const).map(fmt => (
                    <button key={fmt} onClick={() => exportRegAccounts(fmt)}
                      className="text-[10px] px-2 py-0.5 rounded border border-[#30363d] bg-[#21262d] text-gray-400 hover:text-white transition-all">
                      {fmt.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
              <div className="divide-y divide-[#21262d] max-h-56 overflow-y-auto">
                {regAccounts.map((acc, i) => (
                  <div key={i} className="flex items-center gap-3 px-4 py-2">
                    <span className="w-5 h-5 rounded-full bg-emerald-500/20 text-emerald-400 text-[10px] flex items-center justify-center font-bold shrink-0">{i+1}</span>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-mono text-blue-300 truncate">{acc.email}</p>
                      <p className="text-[10px] font-mono text-gray-500 truncate">{acc.password}</p>
                    </div>
                    <button onClick={() => copy(`${acc.email}----${acc.password}`, `ra-${i}`)}
                      className={`text-[10px] px-2 py-0.5 rounded border shrink-0 ${copied === `ra-${i}` ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500 hover:text-white"}`}>
                      {copied === `ra-${i}` ? "✓" : "复制"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <button onClick={() => setStep(3)} className="w-full py-2 bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] rounded-lg text-gray-300 text-sm font-medium transition-all">
            跳过 → 获取 OAuth2 Token
          </button>
        </div>
      )}

      {/* ── Step 3: OAuth2 ── */}
      {step === 3 && (
        <div className="space-y-4">

          {/* 成功状态 */}
          {dcStatus === "done" && profile && (
            <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-xl p-4 flex items-center gap-3">
              <span className="text-2xl">✅</span>
              <div className="flex-1">
                <p className="text-sm font-semibold text-emerald-300">授权成功！已获取 Access Token</p>
                <p className="text-xs text-gray-400 mt-0.5">{profile.displayName}  ·  {profile.mail ?? profile.userPrincipalName}</p>
              </div>
              <button onClick={() => setStep(4)} className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 rounded-lg text-white text-xs font-medium shrink-0">读取收件箱 →</button>
            </div>
          )}

          {/* ── 方法 A：设备码一键授权（推荐） ── */}
          <div className="bg-[#161b22] border border-blue-500/20 rounded-xl p-5 space-y-4">
            <div className="flex items-center gap-2">
              <span className="text-base">🔒</span>
              <h3 className="text-sm font-semibold text-white">方法一：设备码授权（推荐）</h3>
              <span className="ml-auto text-[10px] bg-blue-500/15 border border-blue-500/30 text-blue-400 px-2 py-0.5 rounded-full">无需 Redirect URI</span>
            </div>
            <p className="text-xs text-gray-400 leading-relaxed">
              点击"获取授权码"→ 在浏览器打开微软授权页面 → 输入短码登录你的 Outlook 账号 → 系统自动获取 Token，无需手动配置任何回调地址。
            </p>

            {/* ClientId / TenantId 配置 */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-500 mb-1 block">Client ID</label>
                <input value={clientId} onChange={(e) => setClientId(e.target.value)}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-2 py-1.5 text-xs font-mono text-gray-300 focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">Tenant ID</label>
                <input value={tenantId} onChange={(e) => setTenantId(e.target.value)}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-2 py-1.5 text-xs font-mono text-gray-300 focus:outline-none focus:border-blue-500" />
              </div>
            </div>

            {/* 获取设备码按钮 */}
            {dcStatus === "idle" || dcStatus === "error" ? (
              <button onClick={startDeviceCodeFlow} disabled={dcBusy}
                className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-all flex items-center justify-center gap-2">
                {dcBusy
                  ? <><span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />获取授权码中...</>
                  : "🔑 获取设备授权码"}
              </button>
            ) : null}

            {dcErr && dcStatus === "error" && (
              <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 flex items-center gap-2">
                <span className="text-red-400 text-xs flex-1">{dcErr}</span>
                <button onClick={startDeviceCodeFlow} className="text-xs px-2 py-0.5 bg-red-500/20 border border-red-500/30 rounded text-red-400 hover:bg-red-500/30">重试</button>
              </div>
            )}

            {/* 设备码显示区域 */}
            {dcStatus === "waiting" && dcCode && (
              <div className="space-y-3">
                {/* 步骤提示 */}
                <div className="bg-blue-500/5 border border-blue-500/20 rounded-lg p-3 space-y-2">
                  <p className="text-xs font-semibold text-blue-300">按以下步骤操作：</p>
                  <ol className="space-y-1.5 text-xs text-gray-400">
                    <li className="flex items-start gap-2">
                      <span className="w-4 h-4 rounded-full bg-blue-500/30 text-blue-300 flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5">1</span>
                      在浏览器中打开：
                      <a href={dcUri} target="_blank" rel="noreferrer"
                        className="text-blue-400 underline underline-offset-2 hover:text-blue-300">
                        {dcUri}
                      </a>
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="w-4 h-4 rounded-full bg-blue-500/30 text-blue-300 flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5">2</span>
                      输入以下授权码，然后登录你的 Outlook 账号
                    </li>
                    <li className="flex items-start gap-2">
                      <span className="w-4 h-4 rounded-full bg-blue-500/30 text-blue-300 flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5">3</span>
                      授权完成后，本页面自动获取 Token（每 {dcInterval}s 检测一次）
                    </li>
                  </ol>
                </div>

                {/* 授权码大字展示 */}
                <div className="bg-[#0d1117] border border-yellow-500/40 rounded-xl p-4 text-center relative">
                  <p className="text-xs text-gray-500 mb-2">授权码（在微软页面输入）</p>
                  <p className="text-3xl font-bold font-mono tracking-[0.3em] text-yellow-300">{dcCode}</p>
                  <button onClick={() => copy(dcCode, "dc")}
                    className={`mt-3 text-xs px-3 py-1 rounded border transition-all ${copied === "dc" ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"}`}>
                    {copied === "dc" ? "✓ 已复制" : "复制授权码"}
                  </button>
                  <div className="absolute top-2 right-2 flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                    <span className="text-[10px] text-gray-600">等待授权中…</span>
                  </div>
                </div>

                <button onClick={() => { stopDcPoll(); setDcStatus("idle"); setDcCode(""); }}
                  className="w-full py-1.5 text-xs text-gray-500 hover:text-gray-300 border border-[#30363d] rounded-lg transition-all">
                  取消，重新获取
                </button>
              </div>
            )}

            {/* 授权成功 Token 展示 */}
            {dcStatus === "done" && accessTok && (
              <div className="space-y-2">
                {[
                  { label: "Access Token", value: accessTok.slice(0, 60) + "...", full: accessTok, k: "at" },
                  { label: "Refresh Token", value: refreshTok.slice(0, 60) + (refreshTok.length > 60 ? "..." : ""), full: refreshTok, k: "rt" },
                ].map(({ label, value, full, k }) => full ? (
                  <div key={k} className="bg-[#0d1117] rounded-lg px-3 py-2 flex items-center gap-3">
                    <span className="text-xs text-gray-500 w-28 shrink-0">{label}</span>
                    <span className="text-xs font-mono text-blue-300 flex-1 truncate">{value}</span>
                    <button onClick={() => copy(full, k)} className={`text-xs px-2 py-0.5 rounded border shrink-0 ${copied === k ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500 hover:text-white"}`}>
                      {copied === k ? "✓" : "复制"}
                    </button>
                  </div>
                ) : null)}
              </div>
            )}
          </div>

          {/* ── 方法 B：手动粘贴 Refresh Token ── */}
          <details className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
            <summary className="px-5 py-3 text-sm font-semibold text-gray-400 cursor-pointer hover:text-gray-200 select-none flex items-center gap-2">
              <span>🔧</span> 方法二：手动粘贴 Refresh Token（已有 Token 时使用）
            </summary>
            <div className="px-5 pb-5 pt-3 space-y-3 border-t border-[#21262d]">
              <div>
                <label className="text-xs text-gray-400 mb-1 block">Refresh Token</label>
                <textarea value={refreshTok} onChange={(e) => setRefreshTok(e.target.value)} rows={3}
                  placeholder="粘贴从微软授权流程获取的 Refresh Token..."
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-xs font-mono text-gray-300 focus:outline-none focus:border-blue-500 resize-none" />
              </div>
              <button onClick={doRefresh} disabled={oauthBusy || !refreshTok || !clientId}
                className="w-full py-2.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-all">
                {oauthBusy ? "刷新中..." : "🔑 使用 Refresh Token 获取 Access Token"}
              </button>
              {oauthErr && <p className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{oauthErr}</p>}
            </div>
          </details>

        </div>
      )}

      {/* ── Step 4: 收件箱 ── */}
      {step === 4 && (
        <div className="space-y-4">
          {!accessTok && (
            <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-4 text-center">
              <p className="text-sm text-yellow-400">请先在第三步获取 Access Token</p>
              <button onClick={() => setStep(3)} className="mt-2 text-xs px-4 py-1.5 bg-yellow-500/20 border border-yellow-500/30 rounded-lg text-yellow-400">← 返回第三步</button>
            </div>
          )}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-4">
            {profile && (
              <div className="flex items-center gap-2 pb-3 border-b border-[#21262d]">
                <span className="text-base">📧</span>
                <span className="text-sm font-medium text-white">{profile.mail ?? profile.userPrincipalName}</span>
                <span className="text-xs text-gray-500 ml-auto">Microsoft Graph API</span>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              {["inbox","sentItems","drafts","junkemail","deleteditems"].map((f) => (
                <Pill key={f} active={folder === f} onClick={() => setFolder(f)}>
                  {f === "inbox" ? "收件箱" : f === "sentItems" ? "已发送" : f === "drafts" ? "草稿箱" : f === "junkemail" ? "垃圾邮件" : "已删除"}
                </Pill>
              ))}
            </div>
            <div className="flex gap-2">
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索关键词（可选）"
                className="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-xs text-gray-300 focus:outline-none focus:border-blue-500" />
              <button onClick={fetchMessages} disabled={msgBusy || !accessTok}
                className="px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg text-white text-xs font-medium transition-all">
                {msgBusy ? "加载中..." : "获取邮件"}
              </button>
            </div>
            {msgErr && <p className="text-xs text-red-400">{msgErr}</p>}
            {messages.length > 0 && (
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {messages.map((m) => {
                  const code = extractCode(m.preview || m.subject);
                  const time = new Date(m.receivedAt).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
                  return (
                    <div key={m.id} onClick={() => setSelMsg(selMsg === m.id ? null : m.id)}
                      className={`bg-[#0d1117] rounded-lg px-3 py-2.5 cursor-pointer hover:border-blue-500/30 border transition-all ${selMsg === m.id ? "border-blue-500/30" : "border-[#21262d]"}`}>
                      <div className="flex items-start gap-2">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            {!m.isRead && <span className="w-1.5 h-1.5 rounded-full bg-blue-400 shrink-0" />}
                            <p className="text-xs font-medium text-gray-200 truncate">{m.subject}</p>
                          </div>
                          <p className="text-[11px] text-gray-500 mt-0.5">{m.fromName || m.from} · {time}</p>
                        </div>
                        {code && (
                          <button onClick={(e) => { e.stopPropagation(); copy(code, `m-${m.id}`); }}
                            className={`text-[11px] px-2 py-0.5 rounded border font-mono font-bold shrink-0 ${copied === `m-${m.id}` ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-yellow-500/10 border-yellow-500/30 text-yellow-400"}`}>
                            {copied === `m-${m.id}` ? "已复制" : `验证码: ${code}`}
                          </button>
                        )}
                      </div>
                      {selMsg === m.id && (
                        <p className="mt-2 pt-2 border-t border-[#21262d] text-[11px] text-gray-400 leading-relaxed">{m.preview}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
