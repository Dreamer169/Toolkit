import { useState, useRef, useEffect } from "react";

interface LogEntry { type: string; message: string; }
interface Account { email: string; password: string; username?: string; token?: string; }
type JobStatus = "idle" | "running" | "done" | "failed" | "stopped";

const LOG_COLORS: Record<string, string> = {
  start: "text-blue-400", success: "text-emerald-400", error: "text-red-400",
  warn: "text-yellow-400", done: "text-purple-400", log: "text-gray-300", info: "text-gray-300",
};

export default function CursorRegister() {
  const [mode, setMode] = useState<"browser" | "http">("http");
  const [proxy, setProxy] = useState("");
  const [autoProxy, setAutoProxy] = useState(false);
  const [count, setCount] = useState(1);
  const [headless, setHeadless] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [useXray, setUseXray] = useState(true);
  const [skipStep1, setSkipStep1] = useState(true);
  const [captchaService, setCaptchaService] = useState("yescaptcha");
  const [captchaKey, setCaptchaKey] = useState("");
  const [imapHost, setImapHost] = useState("");
  const [imapUser, setImapUser] = useState("");
  const [imapPass, setImapPass] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatus>("idle");
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [sinceIdx, setSinceIdx] = useState(0);
  const [copied, setCopied] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const scrollToBottom = () => logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  useEffect(scrollToBottom, [logs]);

  const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };

  const startPoll = (jid: string, endpoint: string, startSince = 0) => {
    let since = startSince;
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`/api/tools/cursor/${endpoint}/${jid}?since=${since}`);
        const d = await r.json() as { success: boolean; status: string; logs: LogEntry[]; accounts: Account[]; nextSince: number };
        if (!d.success) return;
        if (d.logs?.length) setLogs((prev) => [...prev, ...d.logs]);
        if (d.accounts?.length) setAccounts(d.accounts);
        since = d.nextSince ?? since;
        setSinceIdx(since);
        if (d.status === "done" || d.status === "failed" || d.status === "stopped") {
          setStatus(d.status as JobStatus); stopPoll();
        }
      } catch {}
    }, 1500);
  };

  const start = async () => {
    stopPoll(); setLogs([]); setAccounts([]); setStatus("running"); setSinceIdx(0);
    const endpoint = mode === "http" ? "register-http" : "register";
    const body = mode === "http"
      ? { email, password, proxy: autoProxy ? "" : proxy, useXray, skipStep1, autoProxy, captchaService, captchaKey, imapHost, imapUser, imapPass }
      : { count, proxy: autoProxy ? "" : proxy, headless, autoProxy };
    try {
      const r = await fetch(`/api/tools/cursor/${endpoint}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const d = await r.json() as { success: boolean; jobId?: string; error?: string };
      if (d.success && d.jobId) { setJobId(d.jobId); startPoll(d.jobId, endpoint, 0); }
      else { setStatus("failed"); setLogs([{ type: "error", message: d.error ?? "启动失败" }]); }
    } catch (e) { setStatus("failed"); setLogs([{ type: "error", message: String(e) }]); }
  };

  const stop = async () => {
    if (!jobId) return; stopPoll();
    const endpoint = mode === "http" ? "register-http" : "register";
    await fetch(`/api/tools/cursor/${endpoint}/${jobId}`, { method: "DELETE" }).catch(() => {});
    setStatus("stopped");
  };

  const copyAccounts = () => {
    const text = accounts.map((a) => `${a.email}  ${a.password}`).join("\n");
    navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); });
  };

  const exportTxt = () => {
    const text = accounts.map((a) => `${a.email}----${a.password}`).join("\n");
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url;
    a.download = `cursor_accounts_${Date.now()}.txt`; a.click(); URL.revokeObjectURL(url);
  };

  const isRunning = status === "running";

  return (
    <div className="space-y-6">
      {/* 模式选择 */}
      <div className="flex gap-2">
        {(["http", "browser"] as const).map((m) => (
          <button key={m} onClick={() => setMode(m)} disabled={isRunning}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all border ${
              mode === m ? "bg-blue-600 border-blue-500 text-white" : "bg-[#161b22] border-[#30363d] text-gray-400 hover:text-gray-200"
            }`}>
            {m === "http" ? "⚡ HTTP 模式（无浏览器）" : "🌐 浏览器模式（Playwright）"}
          </button>
        ))}
      </div>

      {/* HTTP 模式说明 */}
      {mode === "http" && (
        <div className="rounded-lg border border-blue-500/20 bg-blue-500/5 px-4 py-3 text-sm text-blue-300">
          <p className="font-medium mb-1">⚡ HTTP 模式原理</p>
          <ul className="text-xs text-blue-300/80 space-y-0.5 list-disc list-inside">
            <li>Step1 GET 被 CF 拦截 → 自动跳过</li>
            <li>Step2 POST Server Action → 绕过 CF，返回 200</li>
            <li>Outlook 邮箱触发 email-verification 快速通道 → 完全跳过 Turnstile</li>
          </ul>
        </div>
      )}

      {/* 配置区 */}
      <div className="rounded-xl border border-[#21262d] bg-[#0d1117] p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-300">配置</h3>

        {mode === "http" ? (
          <>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Outlook 邮箱（留空自动生成）</label>
                <input value={email} onChange={(e) => setEmail(e.target.value)}
                  placeholder="user@outlook.com" disabled={isRunning}
                  className="w-full px-3 py-2 rounded-lg bg-[#161b22] border border-[#30363d] text-gray-200 text-sm focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">密码（留空自动生成）</label>
                <input value={password} onChange={(e) => setPassword(e.target.value)}
                  placeholder="自动生成" disabled={isRunning}
                  className="w-full px-3 py-2 rounded-lg bg-[#161b22] border border-[#30363d] text-gray-200 text-sm focus:outline-none focus:border-blue-500" />
              </div>
            </div>

            <div className="space-y-2">
              <div className="flex items-center gap-6">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={useXray}
                    onChange={(e) => { setUseXray(e.target.checked); if (e.target.checked) setAutoProxy(false); }}
                    disabled={isRunning} className="w-4 h-4 rounded" />
                  <span className="text-sm text-gray-300">使用 xray 代理 (127.0.0.1:10808)</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={skipStep1}
                    onChange={(e) => setSkipStep1(e.target.checked)} disabled={isRunning} className="w-4 h-4 rounded" />
                  <span className="text-sm text-gray-300">跳过 Step1（直接 Step2）</span>
                </label>
              </div>
              {!useXray && (
                <input value={proxy} onChange={(e) => setProxy(e.target.value)}
                  placeholder="socks5://user:pass@host:port（留空无代理）" disabled={isRunning}
                  className="w-full px-3 py-2 rounded-lg bg-[#161b22] border border-[#30363d] text-gray-200 text-sm font-mono focus:outline-none focus:border-blue-500" />
              )}
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">
                IMAP 收件（Outlook 快速通道时自动用 Graph API，可留空）
              </label>
              <div className="grid grid-cols-3 gap-2">
                <input value={imapHost} onChange={(e) => setImapHost(e.target.value)}
                  placeholder="outlook.office365.com" disabled={isRunning}
                  className="px-3 py-2 rounded-lg bg-[#161b22] border border-[#30363d] text-gray-200 text-sm focus:outline-none focus:border-blue-500" />
                <input value={imapUser} onChange={(e) => setImapUser(e.target.value)}
                  placeholder="邮箱账号" disabled={isRunning}
                  className="px-3 py-2 rounded-lg bg-[#161b22] border border-[#30363d] text-gray-200 text-sm focus:outline-none focus:border-blue-500" />
                <input value={imapPass} onChange={(e) => setImapPass(e.target.value)}
                  placeholder="邮箱密码" type="password" disabled={isRunning}
                  className="px-3 py-2 rounded-lg bg-[#161b22] border border-[#30363d] text-gray-200 text-sm focus:outline-none focus:border-blue-500" />
              </div>
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">打码服务（Outlook 快速通道完全跳过 Turnstile，可不填）</label>
              <div className="flex gap-2">
                <select value={captchaService} onChange={(e) => setCaptchaService(e.target.value)}
                  disabled={isRunning}
                  className="px-3 py-2 rounded-lg bg-[#161b22] border border-[#30363d] text-gray-200 text-sm focus:outline-none">
                  <option value="yescaptcha">YesCaptcha</option>
                  <option value="capsolver">CapSolver</option>
                  <option value="2captcha">2Captcha</option>
                </select>
                <input value={captchaKey} onChange={(e) => setCaptchaKey(e.target.value)}
                  placeholder="API Key（快速通道可留空）" disabled={isRunning}
                  className="flex-1 px-3 py-2 rounded-lg bg-[#161b22] border border-[#30363d] text-gray-200 text-sm focus:outline-none focus:border-blue-500" />
              </div>
            </div>
          </>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">注册数量</label>
                <input type="number" min={1} max={5} value={count}
                  onChange={(e) => setCount(Number(e.target.value))} disabled={isRunning}
                  className="w-full px-3 py-2 rounded-lg bg-[#161b22] border border-[#30363d] text-gray-200 text-sm" />
              </div>
              <div className="col-span-2">
                <label className="block text-xs text-gray-500 mb-1">代理</label>
                <input value={proxy} onChange={(e) => setProxy(e.target.value)}
                  placeholder="socks5://user:pass@host:port" disabled={isRunning || autoProxy}
                  className="w-full px-3 py-2 rounded-lg bg-[#161b22] border border-[#30363d] text-gray-200 text-sm font-mono focus:outline-none focus:border-blue-500" />
              </div>
            </div>
            <div className="flex gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={autoProxy} onChange={(e) => setAutoProxy(e.target.checked)}
                  disabled={isRunning} className="w-4 h-4 rounded" />
                <span className="text-sm text-gray-300">自动选代理</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={headless} onChange={(e) => setHeadless(e.target.checked)}
                  disabled={isRunning} className="w-4 h-4 rounded" />
                <span className="text-sm text-gray-300">无头模式</span>
              </label>
            </div>
          </>
        )}

        <div className="flex gap-3 pt-1">
          {!isRunning ? (
            <button onClick={start}
              className="px-6 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors">
              {mode === "http" ? "⚡ 开始 HTTP 注册" : "🚀 开始注册"}
            </button>
          ) : (
            <button onClick={stop}
              className="px-6 py-2.5 rounded-lg bg-red-600 hover:bg-red-500 text-white text-sm font-medium transition-colors">
              ⏹ 停止
            </button>
          )}
          {status !== "idle" && !isRunning && (
            <button onClick={() => { setStatus("idle"); setLogs([]); setAccounts([]); }}
              className="px-4 py-2.5 rounded-lg bg-[#21262d] hover:bg-[#30363d] text-gray-400 text-sm transition-colors border border-[#30363d]">
              重置
            </button>
          )}
          {isRunning && (
            <div className="flex items-center gap-2 text-sm text-blue-400">
              <span className="animate-spin">⟳</span> 运行中...
            </div>
          )}
        </div>
      </div>

      {logs.length > 0 && (
        <div className="rounded-xl border border-[#21262d] bg-[#0d1117] overflow-hidden">
          <div className="px-4 py-2.5 border-b border-[#21262d] flex items-center justify-between">
            <span className="text-xs font-medium text-gray-500">运行日志</span>
            <span className={`text-xs px-2 py-0.5 rounded-full ${
              status === "done" ? "bg-emerald-500/10 text-emerald-400" :
              status === "failed" ? "bg-red-500/10 text-red-400" :
              status === "running" ? "bg-blue-500/10 text-blue-400" : "bg-gray-500/10 text-gray-400"
            }`}>
              {status === "done" ? "完成" : status === "failed" ? "失败" : status === "running" ? "运行中" : status}
            </span>
          </div>
          <div className="p-4 font-mono text-xs space-y-1 max-h-72 overflow-y-auto">
            {logs.map((log, i) => (
              <div key={i} className={LOG_COLORS[log.type] ?? "text-gray-300"}>{log.message}</div>
            ))}
            <div ref={logEndRef} />
          </div>
        </div>
      )}

      {accounts.length > 0 && (
        <div className="rounded-xl border border-emerald-500/20 bg-[#0d1117] overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d] bg-emerald-500/5">
            <div className="flex items-center gap-2">
              <span className="text-emerald-400">✅</span>
              <span className="text-sm font-medium text-gray-300">注册成功 {accounts.length} 个账号</span>
            </div>
            <div className="flex gap-2">
              <button onClick={copyAccounts}
                className="text-xs px-3 py-1.5 rounded-lg bg-[#21262d] text-gray-400 hover:text-gray-200 transition-all border border-[#30363d]">
                {copied ? "✅ 已复制" : "复制全部"}
              </button>
              <button onClick={exportTxt}
                className="text-xs px-3 py-1.5 rounded-lg bg-[#21262d] text-gray-400 hover:text-gray-200 transition-all border border-[#30363d]">
                导出 .txt
              </button>
            </div>
          </div>
          <div className="divide-y divide-[#21262d]">
            {accounts.map((acc, i) => (
              <div key={i} className="px-4 py-3 flex items-start gap-3">
                <span className="text-emerald-400 text-lg leading-none mt-0.5">✓</span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-mono text-gray-200">{acc.email}</span>
                    <span className="text-xs text-gray-600">·</span>
                    <span className="text-xs font-mono text-gray-400 bg-[#0d1117] px-2 py-0.5 rounded">{acc.password}</span>
                  </div>
                  {acc.token && <p className="text-xs text-emerald-400/60 mt-0.5 truncate">token: {acc.token.slice(0, 40)}...</p>}
                </div>
                <button onClick={() => navigator.clipboard.writeText(`${acc.email}  ${acc.password}`)}
                  className="text-xs text-gray-600 hover:text-gray-400 shrink-0">复制</button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
