import { useState, useRef, useEffect } from "react";

interface LogEntry {
  type: string;
  message: string;
}

interface Account {
  email: string;
  password: string;
  username?: string;
}

type JobStatus = "idle" | "running" | "done" | "failed" | "stopped";

const LOG_COLORS: Record<string, string> = {
  start:   "text-blue-400",
  success: "text-emerald-400",
  error:   "text-red-400",
  warn:    "text-yellow-400",
  done:    "text-purple-400",
  log:     "text-gray-300",
  info:    "text-gray-300",
};

export default function CursorRegister() {
  const [count, setCount]       = useState(1);
  const [proxy, setProxy]       = useState("");
  const [autoProxy, setAutoProxy] = useState(false);
  const [headless, setHeadless] = useState(true);

  const [jobId, setJobId]       = useState<string | null>(null);
  const [status, setStatus]     = useState<JobStatus>("idle");
  const [logs, setLogs]         = useState<LogEntry[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [sinceIdx, setSinceIdx] = useState(0);
  const [copied, setCopied]     = useState(false);

  const logEndRef   = useRef<HTMLDivElement>(null);
  const pollRef     = useRef<ReturnType<typeof setInterval> | null>(null);

  const scrollToBottom = () =>
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  useEffect(scrollToBottom, [logs]);

  const stopPoll = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  const startPoll = (jid: string, startSince = 0) => {
    let since = startSince;
    pollRef.current = setInterval(async () => {
      try {
        const r   = await fetch(`/api/tools/cursor/register/${jid}?since=${since}`);
        const d   = await r.json() as { success: boolean; status: string; logs: LogEntry[]; accounts: Account[]; nextSince: number };
        if (!d.success) return;
        if (d.logs?.length) setLogs((prev) => [...prev, ...d.logs]);
        if (d.accounts?.length) setAccounts(d.accounts);
        since = d.nextSince ?? since;
        setSinceIdx(since);
        if (d.status === "done" || d.status === "failed" || d.status === "stopped") {
          setStatus(d.status as JobStatus);
          stopPoll();
        }
      } catch {}
    }, 1500);
  };

  const start = async () => {
    stopPoll();
    setLogs([]);
    setAccounts([]);
    setStatus("running");
    setSinceIdx(0);
    try {
      const r = await fetch("/api/tools/cursor/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count, proxy: autoProxy ? "" : proxy, headless, autoProxy }),
      });
      const d = await r.json() as { success: boolean; jobId?: string; error?: string };
      if (d.success && d.jobId) {
        setJobId(d.jobId);
        startPoll(d.jobId, 0);
      } else {
        setStatus("failed");
        setLogs([{ type: "error", message: d.error ?? "启动失败" }]);
      }
    } catch (e) {
      setStatus("failed");
      setLogs([{ type: "error", message: String(e) }]);
    }
  };

  const stop = async () => {
    if (!jobId) return;
    stopPoll();
    await fetch(`/api/tools/cursor/register/${jobId}`, { method: "DELETE" }).catch(() => {});
    setStatus("stopped");
  };

  const copyAccounts = () => {
    const text = accounts.map((a) => `${a.email}  ${a.password}  ${a.username ?? ""}`).join("\n");
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const exportTxt = () => {
    const text = accounts.map((a) => `${a.email}----${a.password}`).join("\n");
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `cursor_accounts_${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const isRunning = status === "running";

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">Cursor 账号自动注册</h2>
        <p className="text-sm text-gray-400">
          通过 MailTM 临时邮箱自动注册 cursor.sh 账号，支持代理池和批量并发
        </p>
      </div>

      {/* 配置区 */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-300 border-b border-[#21262d] pb-2">注册配置</h3>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-gray-400 mb-1.5">注册数量 (1–5)</label>
            <input
              type="number" min={1} max={5} value={count}
              onChange={(e) => setCount(Math.min(5, Math.max(1, Number(e.target.value))))}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1.5">运行模式</label>
            <button
              onClick={() => setHeadless(!headless)}
              className={`w-full py-2 px-3 rounded-lg text-sm border transition-all ${
                headless
                  ? "bg-blue-600/20 border-blue-500/40 text-blue-400"
                  : "bg-[#0d1117] border-[#30363d] text-gray-400"
              }`}
            >
              {headless ? "无头模式 (Headless)" : "有界面模式"}
            </button>
          </div>
        </div>

        {/* 代理设置 */}
        <div className="space-y-2">
          <label className="block text-xs text-gray-400">代理设置</label>
          <div className="flex gap-2">
            <button
              onClick={() => setAutoProxy(false)}
              className={`flex-1 py-2 rounded-lg text-xs border transition-all ${
                !autoProxy
                  ? "bg-purple-600/20 border-purple-500/40 text-purple-300"
                  : "bg-[#0d1117] border-[#30363d] text-gray-500"
              }`}
            >
              手动输入代理
            </button>
            <button
              onClick={() => setAutoProxy(true)}
              className={`flex-1 py-2 rounded-lg text-xs border transition-all ${
                autoProxy
                  ? "bg-emerald-600/20 border-emerald-500/40 text-emerald-300"
                  : "bg-[#0d1117] border-[#30363d] text-gray-500"
              }`}
            >
              自动从代理池选取
            </button>
          </div>

          {!autoProxy && (
            <input
              value={proxy}
              onChange={(e) => setProxy(e.target.value)}
              placeholder="socks5://user:pass@host:port  或  http://host:port"
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-blue-500"
            />
          )}
        </div>

        {/* 注意事项 */}
        <div className="bg-blue-500/5 border border-blue-500/20 rounded-lg p-3 text-xs text-blue-300/80 space-y-1">
          <p>⚡ 注册流程：MailTM 临时邮箱 → cursor.sh signup → OTP 验证 → 完成</p>
          <p>📧 无需自备邮箱，自动申请一次性邮箱接收验证码</p>
          <p>🌐 建议使用非中国大陆代理以提高成功率</p>
          <p>⏱ 每个账号约需 60–120 秒（含等待验证码）</p>
        </div>

        <div className="flex gap-3">
          <button
            onClick={start}
            disabled={isRunning}
            className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-white font-medium text-sm transition-all"
          >
            {isRunning ? "注册中..." : "🚀 开始注册"}
          </button>
          {isRunning && (
            <button
              onClick={stop}
              className="px-5 py-2.5 bg-red-600/20 hover:bg-red-600/30 border border-red-500/30 rounded-lg text-red-400 text-sm transition-all"
            >
              停止
            </button>
          )}
        </div>
      </div>

      {/* 状态 */}
      {status !== "idle" && (
        <div className="flex items-center gap-3">
          <div className={`w-2 h-2 rounded-full ${
            isRunning ? "bg-yellow-400 animate-pulse" : status === "done" ? "bg-emerald-400" : "bg-red-400"
          }`} />
          <span className="text-sm text-gray-400">
            {isRunning ? "注册进行中..." : status === "done" ? `完成  ✅ ${accounts.length} 个账号` : status === "stopped" ? "已停止" : "注册失败"}
          </span>
        </div>
      )}

      {/* 日志 */}
      {logs.length > 0 && (
        <div className="bg-[#0d1117] border border-[#21262d] rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2 border-b border-[#21262d]">
            <span className="text-xs text-gray-500">实时日志</span>
            <span className="text-xs text-gray-600">{logs.length} 条</span>
          </div>
          <div className="p-4 font-mono text-xs space-y-0.5 max-h-72 overflow-y-auto">
            {logs.map((l, i) => (
              <div key={i} className={LOG_COLORS[l.type] ?? "text-gray-400"}>
                {l.message}
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        </div>
      )}

      {/* 账号列表 */}
      {accounts.length > 0 && (
        <div className="bg-[#161b22] border border-emerald-500/30 rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d] bg-emerald-500/5">
            <div className="flex items-center gap-2">
              <span className="text-emerald-400">✅</span>
              <span className="text-sm font-medium text-gray-300">注册成功 {accounts.length} 个账号</span>
            </div>
            <div className="flex gap-2">
              <button
                onClick={copyAccounts}
                className="text-xs px-3 py-1.5 rounded-lg bg-[#21262d] text-gray-400 hover:text-gray-200 transition-all border border-[#30363d]"
              >
                {copied ? "✅ 已复制" : "复制全部"}
              </button>
              <button
                onClick={exportTxt}
                className="text-xs px-3 py-1.5 rounded-lg bg-[#21262d] text-gray-400 hover:text-gray-200 transition-all border border-[#30363d]"
              >
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
                    <span className="text-xs font-mono text-gray-400 bg-[#0d1117] px-2 py-0.5 rounded">
                      {acc.password}
                    </span>
                  </div>
                  {acc.username && (
                    <p className="text-xs text-gray-600 mt-0.5">{acc.username}</p>
                  )}
                </div>
                <button
                  onClick={() => navigator.clipboard.writeText(`${acc.email}  ${acc.password}`)}
                  className="text-xs text-gray-600 hover:text-gray-400 shrink-0"
                >
                  复制
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
