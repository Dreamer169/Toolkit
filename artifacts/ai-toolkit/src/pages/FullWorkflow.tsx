import { useState, useRef, useEffect } from "react";

const API = import.meta.env.BASE_URL.replace(/\/$/, "") + "/api";

type Phase = "idle" | "preparing" | "ready" | "registering" | "done" | "error";

interface Identity {
  name: string; firstName: string; lastName: string; gender: string;
  birthday: string; age: number; phone: string; email: string;
  username: string; password: string; address: string;
  city: string; state: string; zip: string; country: string;
}
interface Fingerprint {
  userAgent: string; platform: string; language: string;
  timezone: string; screen: { width: number; height: number };
  devicePixelRatio: number; hardwareConcurrency: number; deviceMemory: number;
  canvas: { hash: string }; audio: { hash: string };
  webgl: { vendor: string; renderer: string };
}
interface PrepareData {
  identity: Identity | null;
  fingerprint: Fingerprint;
  outlook: { email: string; username: string; password: string };
}

interface LogLine { ts: number; text: string; level: "info"|"ok"|"warn"|"error" }

function colorClass(level: LogLine["level"]) {
  return { info:"text-gray-300", ok:"text-emerald-400", warn:"text-amber-400", error:"text-red-400" }[level];
}

function classifyLine(t: string): LogLine["level"] {
  const l = t.toLowerCase();
  if (l.includes("✅") || l.includes("success") || l.includes("注册成功") || l.includes("ok")) return "ok";
  if (l.includes("❌") || l.includes("error") || l.includes("fail") || l.includes("失败")) return "error";
  if (l.includes("⚠️") || l.includes("warn") || l.includes("captcha") || l.includes("重试")) return "warn";
  return "info";
}

function Step({ n, label, active, done }: { n: number; label: string; active: boolean; done: boolean }) {
  return (
    <div className={`flex items-center gap-2 text-sm transition-all ${active ? "text-white" : done ? "text-emerald-400" : "text-gray-600"}`}>
      <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 ${active ? "bg-emerald-600 text-white" : done ? "bg-emerald-900 text-emerald-400 border border-emerald-700" : "bg-[#21262d] text-gray-600"}`}>
        {done ? "✓" : n}
      </div>
      {label}
    </div>
  );
}

export default function FullWorkflow() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [data, setData] = useState<PrepareData | null>(null);
  const [proxy, setProxy] = useState("");
  const [autoProxy, setAutoProxy] = useState(true);
  const [poolCount, setPoolCount] = useState<number | null>(null);
  const [engine, setEngine] = useState("patchright");
  const [headless, setHeadless] = useState(true);
  const [wait, setWait] = useState(11);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [count, setCount] = useState("1");
  const [result, setResult] = useState<{ ok: boolean; email?: string; password?: string; msg?: string; accounts?: { email: string; password: string }[] } | null>(null);
  const [saved, setSaved] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");
  // 打码服务配置
  const [captchaService, setCaptchaService] = useState("2captcha");
  const [captchaKey, setCaptchaKey] = useState("");
  const [captchaHasKey, setCaptchaHasKey] = useState(false);
  const [captchaKeyMasked, setCaptchaKeyMasked] = useState("");
  const [captchaSaveMsg, setCaptchaSaveMsg] = useState("");
  const [captchaShowInput, setCaptchaShowInput] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sinceRef = useRef(0);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  // 恢复运行中的任务（切换页面后自动恢复轮询）
  useEffect(() => {
    const savedJobId = sessionStorage.getItem('fw_jobId');
    if (!savedJobId) return;
    fetch(API + '/tools/outlook/register/' + savedJobId + '?since=0')
      .then(r => r.json())
      .then(d => {
        if (!d.success || d.status === 'done' || d.status === 'stopped') {
          sessionStorage.removeItem('fw_jobId');
          return;
        }
        setJobId(savedJobId);
        setPhase('registering');
        if (d.logs && d.logs.length) {
          setLogs(d.logs.map(function(l: { message?: string }) { return { ts: Date.now(), text: l.message || '', level: 'info' }; }));
        }
        sinceRef.current = d.nextSince || 0;
        addLog('🔄 已自动恢复运行中的任务: ' + savedJobId);
        startPolling(savedJobId);
      })
      .catch(function() { sessionStorage.removeItem('fw_jobId'); });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 加载代理池数量 + 打码配置
  useEffect(() => {
    fetch(`${API}/data/proxies`).then(r => r.json()).then(d => {
      if (d.success) setPoolCount(d.total);
    }).catch(() => {});
    fetch(`${API}/tools/cf-pool/status`).then(r => r.json()).then(d => {
      if (d.success && typeof d.available === "number") setPoolCount(d.available);
    }).catch(() => {});
    fetch(`${API}/data/captcha-config`).then(r => r.json()).then(d => {
      if (d.success) {
        if (d.service) setCaptchaService(d.service);
        setCaptchaHasKey(d.hasKey);
        setCaptchaKeyMasked(d.apiKeyMasked || "");
      }
    }).catch(() => {});
  }, []);

  async function saveCaptchaConfig() {
    setCaptchaSaveMsg("保存中…");
    try {
      const r = await fetch(`${API}/data/captcha-config`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ service: captchaService, apiKey: captchaKey || captchaKeyMasked }),
      });
      const d = await r.json();
      if (d.success) {
        setCaptchaSaveMsg("✅ 已保存");
        setCaptchaHasKey(!!captchaKey || captchaHasKey);
        if (captchaKey) { setCaptchaKeyMasked(captchaKey.slice(0,6) + "****" + captchaKey.slice(-4)); setCaptchaKey(""); }
        setCaptchaShowInput(false);
      } else {
        setCaptchaSaveMsg("❌ 保存失败");
      }
    } catch { setCaptchaSaveMsg("❌ 网络错误"); }
    setTimeout(() => setCaptchaSaveMsg(""), 3000);
  }

  async function pickProxyFromPool() {
    const r = await fetch(`${API}/data/proxies/pick`).then(r => r.json()).catch(() => ({}));
    if (r.success && r.proxy) { setProxy(r.proxy); setAutoProxy(false); }
  }

  async function prepare() {
    setPhase("preparing");
    setData(null); setLogs([]); setResult(null); setSaved(false); setSaveMsg("");
    try {
      const r = await fetch(`${API}/tools/workflow/prepare`);
      const d = await r.json();
      if (!d.success) throw new Error(d.error);
      setData(d);
      setPhase("ready");
    } catch (e) {
      setPhase("error");
      setLogs([{ ts: Date.now(), text: `准备失败: ${e}`, level: "error" }]);
    }
  }

  function addLog(text: string) {
    setLogs(prev => [...prev, { ts: Date.now(), text, level: classifyLine(text) }]);
  }

  async function startRegistration() {
    if (!data) return;
    const submitCount = Math.min(999, Math.max(1, Math.floor(Number(count) || 1)));
    setCount(String(submitCount));
    setPhase("registering");
    setLogs([{ ts: Date.now(), text: "🚀 启动完整工作流...", level: "info" }]);
    sinceRef.current = 0;

    try {
      const r = await fetch(`${API}/tools/outlook/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          count: submitCount, proxy, engine, headless: headless ? "true" : "false",
          wait, retries: 2, delay: 0,
          autoProxy: !proxy && autoProxy,
          proxyMode: !proxy && autoProxy ? "cf" : "",
        }),
      });
      const d = await r.json();
      if (!d.success || !d.jobId) { addLog("❌ 启动失败: " + (d.error || "未知")); setPhase("error"); return; }
      setJobId(d.jobId);
      sessionStorage.setItem('fw_jobId', d.jobId);
      addLog(`📋 任务 ID: ${d.jobId}`);
      addLog(`🪪 身份: ${data.outlook.email} / ${data.outlook.password}`);
      addLog(`🎭 指纹: UA=${data.fingerprint.userAgent.slice(0,60)}…`);
      addLog(`🖥️  屏幕: ${data.fingerprint.screen.width}×${data.fingerprint.screen.height} | DPR: ${data.fingerprint.devicePixelRatio}`);
      addLog(`⏱️  Bot 保护等待: ${wait}s | 引擎: ${engine}`);
      startPolling(d.jobId);
    } catch (e) {
      addLog("❌ 请求失败: " + e); setPhase("error");
    }
  }

  function startPolling(id: string) {
    let staleTicks = 0;
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API}/tools/outlook/register/${id}?since=${sinceRef.current}`);
        if (r.status === 404) {
          clearInterval(pollRef.current!);
          addLog("⚠️ 任务已失效（服务器重启导致），请重新启动注册");
          setResult({ ok: false, msg: "任务丢失（服务器重启），请重试" });
          sessionStorage.removeItem('fw_jobId');
          setPhase("done");
          return;
        }
        const d = await r.json();
        if (!d.success) { staleTicks++; if (staleTicks > 10) { clearInterval(pollRef.current!); setPhase("error"); } return; }
        staleTicks = 0;
        const newLines: { type?: string; message?: string; text?: string }[] = d.logs ?? d.lines ?? [];
        newLines.forEach((l) => {
          const txt = l.message ?? l.text ?? "";
          if (txt) addLog(txt);
        });
        if (d.nextSince != null) sinceRef.current = d.nextSince;
        if (d.status === "done") {
          clearInterval(pollRef.current!);
          const accts: { email: string; password: string }[] = d.accounts ?? [];
          const ok = accts.length > 0;
          const email = accts[0]?.email ?? data?.outlook.email;
          const password = accts[0]?.password ?? data?.outlook.password;
          setResult({ ok, email, password, accounts: accts, msg: ok ? `✅ 注册成功！${accts.length > 1 ? `共 ${accts.length} 个账号` : "账号已激活"}，已自动保存到账号库和邮箱库；如日志出现设备码，请打开链接输入完成授权。` : "❌ 注册失败（CAPTCHA 无法通过——需要住宅代理）" });
          if (ok) {
            setSaved(true);
            setSaveMsg(`✅ ${accts.length} 个账号已自动入库`);
          }
          sessionStorage.removeItem('fw_jobId');
          setPhase("done");
        }
      } catch {}
    }, 2000);
  }

  async function saveToDb() {
    if (!data || !result) return;
    setSaveMsg("保存中…");
    const errors: string[] = [];

    // 保存账号（多账号时逐个保存）
    const accsToSave = result.accounts && result.accounts.length > 0
      ? result.accounts
      : [{ email: result.email ?? "", password: result.password ?? data.outlook.password ?? "" }];
    for (const acc of accsToSave) {
      const a = await fetch(`${API}/data/accounts`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: "outlook", email: acc.email, password: acc.password, status: result.ok ? "active" : "inactive", notes: result.ok ? "" : "注册失败" }),
      }).then(r => r.json()).catch(() => ({ success: false }));
      if (!a.success) errors.push(`${acc.email} 保存失败`);
    }

    // 保存身份信息
    if (data.identity) {
      const b = await fetch(`${API}/data/identities`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ first_name: data.identity.firstName, last_name: data.identity.lastName, gender: data.identity.gender === "female" ? "Female" : "Male", birthday: data.identity.birthday, phone: data.identity.phone, email: data.identity.email, address: data.identity.address, city: data.identity.city, state: data.identity.state, zip: data.identity.zip, country: data.identity.country, username: data.identity.username, password: data.identity.password }),
      }).then(r => r.json()).catch(() => ({ success: false }));
      if (!b.success) errors.push("身份保存失败");
    }

    if (errors.length === 0) { setSaved(true); setSaveMsg(`✅ ${accsToSave.length} 个账号 + 身份信息已保存到数据库`); }
    else setSaveMsg("⚠️ 部分保存失败: " + errors.join("，"));
  }

  const step1Done = phase !== "idle";
  const step2Done = ["ready","registering","done","error"].includes(phase);
  const step3Done = ["done","error"].includes(phase);
  const step4Done = phase === "done" && saved;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">完整工作流</h1>
        <p className="text-gray-400 text-sm mt-1">
          自动串联：身份生成 → 浏览器指纹配置 → Outlook 注册（patchright 随机指纹） → 自动入库
        </p>
      </div>

      {/* 打码服务配置面板（始终可见）*/}
      <div className={`rounded-lg border p-4 ${captchaHasKey ? "bg-emerald-950/30 border-emerald-700/40" : "bg-[#161b22] border-[#30363d]"}`}>
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-lg">{captchaHasKey ? "🔑" : "🛡️"}</span>
          <div className="flex-1 min-w-0">
            <p className={`text-sm font-medium ${captchaHasKey ? "text-emerald-300" : "text-gray-300"}`}>
              {captchaHasKey ? `打码服务已配置 (${captchaService})` : "无障碍挑战绕过（主要方式，成功率高）"}
            </p>
            <p className="text-xs text-gray-500 mt-0.5">
              {captchaHasKey ? `Key: ${captchaKeyMasked}` : "支持 2captcha / CapMonster 作为备用降级方案"}
            </p>
          </div>
          <button onClick={() => setCaptchaShowInput(v => !v)}
            className="px-3 py-1.5 rounded text-xs border border-[#30363d] bg-[#21262d] text-gray-300 hover:text-white whitespace-nowrap">
            {captchaShowInput ? "收起" : captchaHasKey ? "修改配置" : "配置打码服务"}
          </button>
        </div>
        {captchaShowInput && (
          <div className="mt-3 space-y-2 pt-3 border-t border-[#30363d]">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-gray-400">打码服务商</label>
                <select value={captchaService} onChange={e => setCaptchaService(e.target.value)}
                  className="w-full mt-1 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-white text-xs">
                  <option value="2captcha">2captcha.com</option>
                  <option value="capmonster">CapMonster Cloud</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-400">API Key</label>
                <input type="password" value={captchaKey} onChange={e => setCaptchaKey(e.target.value)}
                  placeholder={captchaHasKey ? captchaKeyMasked : "粘贴 API Key"}
                  className="w-full mt-1 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-white font-mono text-xs" />
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button onClick={saveCaptchaConfig}
                className="px-4 py-1.5 bg-emerald-700 hover:bg-emerald-600 rounded text-xs text-white">
                保存
              </button>
              {captchaHasKey && (
                <button onClick={async () => {
                  await fetch(`${API}/data/captcha-config`, {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ service: "", apiKey: "" }),
                  });
                  setCaptchaHasKey(false); setCaptchaKeyMasked(""); setCaptchaShowInput(false);
                  setCaptchaSaveMsg("✅ 已清除"); setTimeout(() => setCaptchaSaveMsg(""), 2000);
                }} className="px-4 py-1.5 bg-red-900/50 hover:bg-red-800 border border-red-700/50 rounded text-xs text-red-300">
                  清除 Key
                </button>
              )}
              {captchaSaveMsg && <span className="text-xs text-gray-400">{captchaSaveMsg}</span>}
            </div>
            <p className="text-xs text-gray-600">
              获取 API Key：
              <a href="https://2captcha.com" target="_blank" className="text-blue-500 hover:underline">2captcha.com</a>
              {" 或 "}
              <a href="https://capmonster.cloud" target="_blank" className="text-blue-500 hover:underline">capmonster.cloud</a>
              {" · 支持 FunCaptcha / Arkose Labs，注册成功率大幅提升"}
            </p>
          </div>
        )}
      </div>

      {/* 步骤指示器 */}
      <div className="flex gap-6 overflow-x-auto pb-1">
        <Step n={1} label="准备身份 + 指纹" active={phase==="preparing"} done={step1Done} />
        <div className="text-gray-700 self-center">→</div>
        <Step n={2} label="配置注册参数" active={phase==="ready"} done={step2Done} />
        <div className="text-gray-700 self-center">→</div>
        <Step n={3} label="Outlook 自动注册" active={phase==="registering"} done={step3Done} />
        <div className="text-gray-700 self-center">→</div>
        <Step n={4} label="自动入库" active={phase==="done" && !saved} done={step4Done} />
      </div>

      {/* Step 1：准备 */}
      {phase === "idle" && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-6 text-center space-y-4">
          <div className="text-4xl">🚀</div>
          <p className="text-gray-300">点击下方按钮，自动生成真实身份、浏览器指纹和 Outlook 账号信息</p>
          <button onClick={prepare} className="px-8 py-3 bg-emerald-700 rounded-lg text-white font-semibold hover:bg-emerald-600 transition-colors">
            开始准备
          </button>
        </div>
      )}

      {phase === "preparing" && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-8 text-center">
          <div className="animate-spin text-3xl mb-3">⚙️</div>
          <p className="text-gray-400">正在生成身份信息和浏览器指纹…</p>
        </div>
      )}

      {/* Step 2：展示准备结果 + 配置 */}
      {data && (phase === "ready" || phase === "registering" || phase === "done" || phase === "error") && (
        <div className="grid md:grid-cols-2 gap-4">
          {/* 身份信息 */}
          <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 space-y-3">
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">🪪 生成的身份信息</h3>
            {data.identity ? (
              <div className="grid grid-cols-2 gap-y-1.5 text-xs">
                {[
                  ["姓名", data.identity.name],
                  ["性别", data.identity.gender === "female" ? "女" : "男"],
                  ["生日", `${data.identity.birthday} (${data.identity.age}岁)`],
                  ["手机", data.identity.phone],
                  ["邮箱", data.identity.email],
                  ["用户名", data.identity.username],
                  ["城市", `${data.identity.city}, ${data.identity.state}`],
                  ["地址", data.identity.address],
                ].map(([k, v]) => (
                  <div key={k} className="contents">
                    <span className="text-gray-500">{k}</span>
                    <span className="text-gray-200 font-mono break-all">{v}</span>
                  </div>
                ))}
              </div>
            ) : <p className="text-gray-600 text-xs">身份信息获取失败（网络问题），注册仍可继续</p>}
          </div>

          {/* 浏览器指纹 */}
          <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 space-y-3">
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">🎭 浏览器指纹（patchright 随机化）</h3>
            <div className="grid grid-cols-2 gap-y-1.5 text-xs">
              {[
                ["引擎", "patchright (Chromium)"],
                ["User-Agent", data.fingerprint.userAgent.slice(0, 40) + "…"],
                ["平台", data.fingerprint.platform],
                ["语言", data.fingerprint.language],
                ["时区", data.fingerprint.timezone],
                ["分辨率", `${data.fingerprint.screen.width}×${data.fingerprint.screen.height}`],
                ["DPR", String(data.fingerprint.devicePixelRatio)],
                ["CPU核心", String(data.fingerprint.hardwareConcurrency)],
                ["内存", `${data.fingerprint.deviceMemory}GB`],
                ["Canvas 哈希", data.fingerprint.canvas.hash],
                ["音频哈希", data.fingerprint.audio.hash.slice(0, 12) + "…"],
                ["GPU", (data.fingerprint.webgl?.renderer ?? "").slice(0, 30) + "…"],
              ].map(([k, v]) => (
                <div key={k} className="contents">
                  <span className="text-gray-500">{k}</span>
                  <span className="text-gray-200 font-mono break-all">{v}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Outlook 账号计划 */}
          <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 space-y-2">
            <h3 className="text-sm font-semibold text-white">📧 待注册账号</h3>
            <div className="grid grid-cols-2 gap-y-2 text-xs">
              <span className="text-gray-500">邮箱地址</span>
              <span className="text-white font-mono">{data.outlook.email}</span>
              <span className="text-gray-500">密码</span>
              <span className="text-white font-mono">{data.outlook.password}</span>
            </div>
          </div>

          {/* 注册配置 */}
          <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 space-y-3">
            <h3 className="text-sm font-semibold text-white">⚙️ 注册配置</h3>
            <div className="space-y-2 text-xs">
              <div>
                <label className="text-gray-400">代理地址（无代理 = 服务器 IP，大概率在 CAPTCHA 卡住）</label>
                <input value={proxy} onChange={e => setProxy(e.target.value)} placeholder="socks5://user:pass@host:port" disabled={phase !== "ready"} className="w-full mt-1 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-white font-mono disabled:opacity-50" />
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="text-gray-400">引擎</label>
                  <select value={engine} onChange={e => setEngine(e.target.value)} disabled={phase !== "ready"} className="w-full mt-1 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-white disabled:opacity-50">
                    <option value="patchright">patchright</option>
                    <option value="playwright">playwright</option>
                  </select>
                </div>
                <div>
                  <label className="text-gray-400">Bot 等待(s)</label>
                  <input type="number" value={wait} onChange={e => setWait(Number(e.target.value))} min={5} max={60} disabled={phase !== "ready"} className="w-full mt-1 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-white disabled:opacity-50" />
                </div>
                <div className="flex flex-col">
                  <label className="text-gray-400">无头模式</label>
                  <button onClick={() => phase === "ready" && setHeadless(h => !h)} className={`mt-1 px-3 py-1.5 rounded text-xs border transition-colors ${headless ? "bg-emerald-900/40 border-emerald-700 text-emerald-300" : "bg-[#21262d] border-[#30363d] text-gray-400"} ${phase !== "ready" ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}>
                    {headless ? "开启" : "关闭"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 启动注册按钮 */}
      {phase === "ready" && (
        <div className="space-y-3">
          {/* 代理状态提示 */}
          {!proxy && poolCount !== null && poolCount > 0 ? (
            <div className="bg-emerald-900/20 border border-emerald-700/40 rounded-lg px-4 py-3 flex items-center gap-3">
              <span className="text-emerald-400 text-lg flex-shrink-0">🌐</span>
              <div className="flex-1 text-sm">
                <p className="text-emerald-300 font-medium">代理池已就绪：{poolCount} 个住宅代理可用</p>
                <p className="text-emerald-600 text-xs mt-0.5">启动注册时将自动从池中选取一个，每账号使用独立 session IP</p>
              </div>
              <button onClick={pickProxyFromPool} className="px-3 py-1.5 bg-emerald-800 hover:bg-emerald-700 rounded text-xs text-emerald-300 whitespace-nowrap">
                手动选取查看
              </button>
            </div>
          ) : !proxy ? (
            <div className="bg-amber-900/30 border border-amber-700/50 rounded-lg px-4 py-3 flex items-start gap-3">
              <span className="text-amber-400 text-lg flex-shrink-0">⚠️</span>
              <div className="text-sm">
                <p className="text-amber-300 font-medium">未填写代理——注册会在 CAPTCHA 卡住</p>
                <p className="text-amber-500 text-xs mt-0.5">微软对数据中心 IP 强制验证。填写住宅代理后再点「启动注册」，或直接点下方「仅保存凭据」跳过注册步骤。</p>
              </div>
            </div>
          ) : (
            <div className="bg-blue-900/20 border border-blue-700/40 rounded-lg px-4 py-2 flex items-center gap-2 text-sm text-blue-300">
              <span>🌐</span> 使用代理: <span className="font-mono text-xs text-blue-400">{proxy.replace(/:([^:@]{4})[^:@]*@/, ":****@")}</span>
            </div>
          )}
          <div className="flex gap-3 items-center">
            <div className="flex items-center gap-1.5 bg-[#21262d] border border-[#30363d] rounded-lg px-2 py-1.5 shrink-0">
              <span className="text-xs text-gray-400 whitespace-nowrap">数量</span>
              <input
                type="number" min={1} max={999} step={1} value={count}
                onChange={e => setCount(e.target.value.replace(/[^0-9]/g, ""))}
                onBlur={() => setCount(String(Math.min(999, Math.max(1, Math.floor(Number(count) || 1)))))}
                className="w-20 bg-transparent text-center text-sm text-white focus:outline-none"
              />
            </div>
            <button onClick={startRegistration} className={`flex-1 py-3 rounded-lg text-white font-semibold transition-colors ${(proxy || autoProxy) ? "bg-blue-700 hover:bg-blue-600" : "bg-blue-900/60 hover:bg-blue-800/60 border border-blue-700/50"}`}>
              🚀 启动注册{Number(count) > 1 ? `（顺序 ×${count}）` : ""}
              {!proxy && autoProxy ? "（代理池自动选取）" : !proxy ? "（无代理）" : ""}
            </button>
            <button onClick={prepare} className="px-4 py-3 bg-[#21262d] border border-[#30363d] rounded-lg text-gray-400 hover:text-white text-sm">
              重新生成
            </button>
          </div>
          <button
            onClick={async () => {
              if (!data) return;
              setSaved(false); setSaveMsg("");
              const a = await fetch(`${API}/data/accounts`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ platform:"outlook", email: data.outlook.email, password: data.outlook.password, status:"inactive", notes:"已生成待注册" }) }).then(r=>r.json()).catch(()=>({}));
              await fetch(`${API}/data/emails`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ address: data.outlook.email, password: data.outlook.password, provider:"outlook", status:"inactive", notes:"已生成待注册" }) }).then(r=>r.json()).catch(()=>({}));
              if (data.identity) {
                await fetch(`${API}/data/identities`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ first_name: data.identity.firstName, last_name: data.identity.lastName, gender: data.identity.gender === "female" ? "Female" : "Male", birthday: data.identity.birthday, phone: data.identity.phone, email: data.identity.email, address: data.identity.address, city: data.identity.city, state: data.identity.state, zip: data.identity.zip, country: data.identity.country, username: data.identity.username, password: data.identity.password }) }).then(r=>r.json()).catch(()=>{});
              }
              if (a.success) { setSaved(true); setSaveMsg("✅ 凭据已保存到账号库和邮箱库（状态：待注册）——有代理时可回来启动注册"); }
              else setSaveMsg("❌ 保存失败：" + (a.error || "未知"));
            }}
            className="w-full py-2 bg-[#21262d] border border-[#30363d] rounded-lg text-sm text-gray-400 hover:text-white hover:bg-[#30363d] transition-colors"
          >
            💾 仅保存凭据到数据库（跳过注册，稍后手动用代理注册）
          </button>
          {saveMsg && <p className={`text-sm ${saved ? "text-emerald-400" : "text-red-400"}`}>{saveMsg}</p>}
        </div>
      )}

      {/* 实时日志 */}
      {logs.length > 0 && (
        <div className="bg-[#0d1117] border border-[#30363d] rounded-lg overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2 bg-[#161b22] border-b border-[#30363d]">
            <span className="text-xs font-semibold text-gray-400">📋 实时注册日志</span>
            {phase === "registering" && (
              <span className="flex items-center gap-1.5 text-xs text-emerald-400">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                运行中…
              </span>
            )}
          </div>
          <div ref={logRef} className="h-48 overflow-y-auto p-3 font-mono text-xs space-y-0.5">
            {logs.map((l, i) => (
              <div key={i} className={colorClass(l.level)}>
                <span className="text-gray-600 mr-2">{new Date(l.ts).toLocaleTimeString("zh-CN", { hour12: false })}</span>
                {l.text}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 结果 + 入库 */}
      {phase === "done" && result && (
        <div className={`border rounded-lg p-5 space-y-4 ${result.ok ? "bg-emerald-900/20 border-emerald-700" : "bg-red-900/20 border-red-800"}`}>
          <div className="flex items-center gap-3">
            <span className="text-2xl">{result.ok ? "✅" : "❌"}</span>
            <div>
              <div className={`font-semibold ${result.ok ? "text-emerald-300" : "text-red-300"}`}>{result.msg}</div>
              {result.ok && (!result.accounts || result.accounts.length <= 1) && (
                <div className="text-sm text-gray-400 mt-0.5">{result.email} / {result.password}</div>
              )}
            </div>
          </div>
          {result.ok && result.accounts && result.accounts.length > 1 && (
            <div className="border border-emerald-700/40 rounded-lg overflow-hidden">
              <div className="px-3 py-2 bg-emerald-900/30 text-xs text-emerald-400 font-medium border-b border-emerald-700/30">
                已注册 {result.accounts.length} 个账号
              </div>
              {result.accounts.map((a, i) => (
                <div key={i} className="px-3 py-2 border-t border-[#21262d] first:border-0 text-xs font-mono flex gap-3">
                  <span className="text-emerald-400 shrink-0">#{i + 1}</span>
                  <span className="text-gray-300 truncate">{a.email}</span>
                  <span className="text-gray-500 shrink-0">/</span>
                  <span className="text-gray-400 truncate">{a.password}</span>
                </div>
              ))}
            </div>
          )}
          {!saved && (
            <button onClick={saveToDb} className="px-5 py-2 bg-emerald-700 hover:bg-emerald-600 rounded text-sm text-white transition-colors">
              💾 保存到数据库（账号 + 身份信息）
            </button>
          )}
          {saveMsg && <p className={`text-sm ${saved ? "text-emerald-400" : "text-amber-400"}`}>{saveMsg}</p>}
          <button onClick={() => { setPhase("idle"); setData(null); setLogs([]); setResult(null); setSaved(false); setSaveMsg(""); }} className="px-4 py-2 bg-[#21262d] border border-[#30363d] rounded text-xs text-gray-400 hover:text-white">
            重新开始
          </button>
        </div>
      )}

      {/* 错误重试 */}
      {phase === "error" && !result && (
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-4 flex items-center justify-between">
          <span className="text-red-300 text-sm">准备阶段出错</span>
          <button onClick={prepare} className="px-4 py-2 bg-[#21262d] border border-[#30363d] rounded text-xs text-gray-400 hover:text-white">重试</button>
        </div>
      )}

      {/* 说明 */}
      <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 text-xs text-gray-500 space-y-1.5">
        <p className="text-gray-400 font-medium">工作流说明</p>
        <p>• <span className="text-gray-300">身份信息</span>：从 randomuser.me 获取真实随机美国人身份（姓名/生日/手机/地址）</p>
        <p>• <span className="text-gray-300">浏览器指纹</span>：patchright 每次启动自动随机化 Canvas/WebGL/音频哈希、UA、时区、分辨率——无需手动配置</p>
        <p>• <span className="text-gray-300">机器 ID</span>：与 Cursor 机器 ID 重置无关，注册走的是浏览器自动化，每次都是全新浏览器实例</p>
        <p>• <span className="text-gray-300">CAPTCHA</span>：Microsoft 对数据中心 IP 强制要求，必须使用住宅代理才能通过</p>
        <p>• <span className="text-gray-300">自动入库</span>：注册完成后会自动写入账号库和邮箱库；没有内联 token 时自动生成设备码授权提示</p>
      </div>
    </div>
  );
}
