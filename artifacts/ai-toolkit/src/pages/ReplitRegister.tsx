import { useState, useRef, useEffect } from "react";

interface RegisteredAccount {
  ok: boolean;
  email: string;
  username?: string;
  verified?: boolean;
  exit_ip?: string;
  error?: string;
}

interface PollResult {
  jobId: string;
  status: "running" | "done" | "error";
  elapsed: number;
  logs: string[];
  result: { results: RegisteredAccount[]; summary: string } | null;
}

type JobStatus = "idle" | "running" | "done" | "error";

export default function ReplitRegister() {
  const [count, setCount] = useState(1);
  const [headless, setHeadless] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatus>("idle");
  const [logs, setLogs] = useState<string[]>([]);
  const [accounts, setAccounts] = useState<RegisteredAccount[]>([]);
  const [summary, setSummary] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [copied, setCopied] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const scrollToBottom = () => logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  useEffect(scrollToBottom, [logs]);

  const stopPoll = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  const startPoll = (jid: string) => {
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`/api/replit/register/${jid}`);
        const d = await r.json() as PollResult;
        setLogs(d.logs ?? []);
        setElapsed(d.elapsed ?? 0);
        if (d.result) {
          const okAccs = (d.result.results ?? []).filter(a => a.ok);
          setAccounts(okAccs);
          setSummary(d.result.summary ?? "");
        }
        if (d.status === "done" || d.status === "error") {
          setStatus(d.status);
          stopPoll();
        }
      } catch {}
    }, 2000);
  };

  const start = async () => {
    stopPoll();
    setLogs([]);
    setAccounts([]);
    setSummary("");
    setElapsed(0);
    setStatus("running");
    try {
      const r = await fetch("/api/replit/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count, headless }),
      });
      const d = await r.json() as { success: boolean; jobId?: string; error?: string };
      if (d.success && d.jobId) {
        setJobId(d.jobId);
        startPoll(d.jobId);
      } else {
        setStatus("error");
        setLogs([`启动失败: ${d.error ?? "未知错误"}`]);
      }
    } catch (e) {
      setStatus("error");
      setLogs([`请求异常: ${String(e)}`]);
    }
  };

  const copyAccounts = () => {
    const text = accounts.map(a => `${a.email}  ${a.username ?? ""}`).join("\n");
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const exportTxt = () => {
    const text = accounts
      .map(a => `${a.email}  ${a.username ?? ""}  verified=${a.verified}  ip=${a.exit_ip ?? ""}`)
      .join("\n");
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `replit_accounts_${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const reset = () => {
    stopPoll();
    setStatus("idle");
    setLogs([]);
    setAccounts([]);
    setSummary("");
    setJobId(null);
    setElapsed(0);
  };

  const isRunning = status === "running";

  return (
    <div className="space-y-6">
      {/* 说明栏 */}
      <div className="rounded-lg border border-violet-500/20 bg-violet-500/5 px-4 py-3 text-sm text-violet-300">
        <p className="font-medium mb-1">🤖 Reseek 账号自动注册</p>
        <ul className="text-xs text-violet-300/80 space-y-0.5 list-disc list-inside">
          <li>从数据库取可用 Outlook 账号作为注册邮箱</li>
          <li>通过 Playwright + xray SOCKS5 代理填写注册表单</li>
          <li>自动调用 Outlook Graph API 完成邮件验证</li>
          <li>结果写入 DB（platform = replit）</li>
        </ul>
      </div>

      {/* 配置区 */}
      <div className="rounded-xl border border-[#21262d] bg-[#0d1117] p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-300">配置</h3>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">注册数量（最多 3 个）</label>
            <input
              type="number"
              min={1}
              max={3}
              value={count}
              onChange={e => setCount(Math.min(3, Math.max(1, Number(e.target.value))))}
              disabled={isRunning}
              className="w-full px-3 py-2 rounded-lg bg-[#161b22] border border-[#30363d] text-gray-200 text-sm focus:outline-none focus:border-blue-500"
            />
          </div>
          <div className="flex items-end pb-2">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={headless}
                onChange={e => setHeadless(e.target.checked)}
                disabled={isRunning}
                className="w-4 h-4 rounded"
              />
              <span className="text-sm text-gray-300">无头模式（Headless）</span>
            </label>
          </div>
        </div>

        <div className="rounded-lg border border-[#21262d] bg-[#161b22] px-4 py-3 text-xs text-gray-500">
          <p className="font-medium text-gray-400 mb-1">前置条件</p>
          <ul className="space-y-0.5 list-disc list-inside">
            <li>数据库中需有 <code className="text-yellow-400">platform=outlook, status=active</code> 且含 refresh_token 的账号</li>
            <li>xray 代理已运行（端口 10820–10845 任意可用）</li>
            <li>如账号不足，请先执行 Outlook 工作流生成</li>
          </ul>
        </div>

        <div className="flex gap-3 pt-1">
          {!isRunning ? (
            <button
              onClick={start}
              className="px-6 py-2.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-colors"
            >
              🚀 开始注册
            </button>
          ) : (
            <button
              onClick={reset}
              className="px-6 py-2.5 rounded-lg bg-red-600 hover:bg-red-500 text-white text-sm font-medium transition-colors"
            >
              ⏹ 中止轮询
            </button>
          )}
          {(status === "done" || status === "error") && (
            <button
              onClick={reset}
              className="px-4 py-2.5 rounded-lg bg-[#21262d] hover:bg-[#30363d] text-gray-400 text-sm transition-colors border border-[#30363d]"
            >
              重置
            </button>
          )}
          {isRunning && (
            <div className="flex items-center gap-2 text-sm text-violet-400">
              <span className="animate-spin inline-block">⟳</span>
              <span>运行中… {elapsed}s</span>
            </div>
          )}
          {!isRunning && status !== "idle" && elapsed > 0 && (
            <span className="self-center text-xs text-gray-600">耗时 {elapsed}s</span>
          )}
        </div>
      </div>

      {/* 日志区 */}
      {logs.length > 0 && (
        <div className="rounded-xl border border-[#21262d] bg-[#0d1117] overflow-hidden">
          <div className="px-4 py-2.5 border-b border-[#21262d] flex items-center justify-between">
            <span className="text-xs font-medium text-gray-500">运行日志</span>
            <span className={`text-xs px-2 py-0.5 rounded-full ${
              status === "done" ? "bg-emerald-500/10 text-emerald-400" :
              status === "error" ? "bg-red-500/10 text-red-400" :
              "bg-violet-500/10 text-violet-400"
            }`}>
              {status === "done" ? "完成" : status === "error" ? "出错" : "运行中"}
              {summary && ` · ${summary}`}
            </span>
          </div>
          <div className="p-4 font-mono text-xs space-y-0.5 max-h-80 overflow-y-auto">
            {logs.map((line, i) => {
              const isOk    = line.includes("✅");
              const isErr   = line.includes("✗") || line.toLowerCase().includes("error") || line.toLowerCase().includes("fail");
              const isWarn  = line.toLowerCase().includes("warn") || line.includes("timeout");
              const color = isOk ? "text-emerald-400" : isErr ? "text-red-400" : isWarn ? "text-yellow-400" : "text-gray-300";
              return <div key={i} className={color}>{line}</div>;
            })}
            <div ref={logEndRef} />
          </div>
        </div>
      )}

      {/* 账号结果 */}
      {accounts.length > 0 && (
        <div className="rounded-xl border border-emerald-500/20 bg-[#0d1117] overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d] bg-emerald-500/5">
            <div className="flex items-center gap-2">
              <span className="text-emerald-400">✅</span>
              <span className="text-sm font-medium text-gray-300">
                注册成功 {accounts.length} 个账号
              </span>
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
                    {acc.username && (
                      <>
                        <span className="text-xs text-gray-600">·</span>
                        <span className="text-xs font-mono text-violet-400 bg-[#161b22] px-2 py-0.5 rounded">
                          @{acc.username}
                        </span>
                      </>
                    )}
                    <span className={`text-xs px-2 py-0.5 rounded-full ${
                      acc.verified
                        ? "bg-emerald-500/10 text-emerald-400"
                        : "bg-yellow-500/10 text-yellow-400"
                    }`}>
                      {acc.verified ? "已验证" : "待验证"}
                    </span>
                  </div>
                  {acc.exit_ip && (
                    <p className="text-xs text-gray-600 mt-0.5">出口 IP: {acc.exit_ip}</p>
                  )}
                </div>
                <button
                  onClick={() => navigator.clipboard.writeText(`${acc.email}  ${acc.username ?? ""}`)}
                  className="text-xs text-gray-600 hover:text-gray-400 shrink-0 transition-colors"
                >
                  复制
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 无账号但任务完成 */}
      {status !== "idle" && status !== "running" && accounts.length === 0 && logs.length > 0 && (
        <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-4 py-4 text-sm text-red-300 text-center">
          未注册成功任何账号。请检查日志并确认 Outlook 账号可用、xray 代理正常。
        </div>
      )}
    </div>
  );
}
