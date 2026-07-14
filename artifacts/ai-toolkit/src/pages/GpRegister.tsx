import { useState, useRef, useEffect } from "react";

interface RegisteredAccount {
  email: string;
  password: string;
  username?: string;
  invite_code?: string;
  error?: string;
}

interface Job {
  jobId: string;
  status: "running" | "done" | "failed" | "stopped";
  startedAt: number;
  finishedAt?: number;
  logs: Array<{ type: string; message: string }>;
  accounts: RegisteredAccount[];
}

type JobStatus = "idle" | "running" | "done" | "error";

export default function GpRegister() {
  const [count, setCount] = useState(1);
  const [socksPort, setSocksPort] = useState(0);
  const [headless, setHeadless] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatus>("idle");
  const [logs, setLogs] = useState<Array<{ type: string; message: string }>>([]);
  const [accounts, setAccounts] = useState<RegisteredAccount[]>([]);
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
        const r = await fetch(`/api/tools/jobs/${jid}`);
        const d = await r.json() as Job;
        setLogs(d.logs ?? []);
        setElapsed(d.finishedAt ? Math.round((d.finishedAt - d.startedAt) / 1000) : Math.round((Date.now() - d.startedAt) / 1000));
        if (d.accounts?.length) setAccounts(d.accounts);
        if (d.status === "done" || d.status === "failed" || d.status === "stopped") {
          setStatus(d.status === "done" ? "done" : "error");
          stopPoll();
        }
      } catch {}
    }, 2000);
  };

  const start = async () => {
    stopPoll();
    setLogs([]);
    setAccounts([]);
    setElapsed(0);
    setStatus("running");
    try {
      const r = await fetch("/api/tools/gp-register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count, socksPort, headless }),
      });
      const d = await r.json() as { success: boolean; jobId?: string; error?: string };
      if (d.success && d.jobId) {
        setJobId(d.jobId);
        startPoll(d.jobId);
      } else {
        setStatus("error");
        setLogs([{ type: "error", message: d.error ?? "启动失败" }]);
      }
    } catch (e) {
      setStatus("error");
      setLogs([{ type: "error", message: String(e) }]);
    }
  };

  const stop = async () => {
    if (!jobId) return;
    stopPoll();
    try { await fetch(`/api/tools/jobs/${jobId}`, { method: "DELETE" }); } catch {}
    setStatus("idle");
  };

  const copyAccounts = () => {
    const text = accounts.map(a =>
      `email=${a.email} | password=${a.password}${a.username ? ` | username=${a.username}` : ""}${a.invite_code ? ` | invite=${a.invite_code}` : ""}`
    ).join("\n");
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const logColor = (type: string) => {
    if (type === "error") return "text-red-400";
    if (type === "warn") return "text-amber-400";
    if (type === "success") return "text-emerald-400";
    if (type === "start") return "text-sky-400";
    return "text-gray-300";
  };

  const statusBadge = () => {
    if (status === "running") return <span className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full bg-sky-500/15 text-sky-400"><span className="w-1.5 h-1.5 rounded-full bg-sky-400 animate-pulse" />运行中</span>;
    if (status === "done")    return <span className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full bg-emerald-500/15 text-emerald-400"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />完成</span>;
    if (status === "error")   return <span className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full bg-red-500/15 text-red-400"><span className="w-1.5 h-1.5 rounded-full bg-red-400" />失败</span>;
    return null;
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <span className="text-2xl">🌲</span>
        <div>
          <h1 className="text-xl font-bold text-white">GPTree 邀请注册</h1>
          <p className="text-sm text-gray-500">pydoll + Turnstile bypass · RESI 代理</p>
        </div>
      </div>

      {/* Config */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
        <div className="text-sm font-semibold text-white mb-3">注册参数</div>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-gray-500">注册数量</span>
            <input
              type="number" min={1} max={20} value={count}
              onChange={e => setCount(Math.max(1, Number(e.target.value)))}
              disabled={status === "running"}
              className="bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-white text-sm disabled:opacity-50"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-gray-500">SOCKS 端口 (0=自动)</span>
            <input
              type="number" min={0} value={socksPort}
              onChange={e => setSocksPort(Math.max(0, Number(e.target.value)))}
              disabled={status === "running"}
              className="bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 text-white text-sm disabled:opacity-50"
            />
          </label>
          <label className="flex items-center gap-2 mt-4">
            <input
              type="checkbox" checked={headless}
              onChange={e => setHeadless(e.target.checked)}
              disabled={status === "running"}
              className="w-4 h-4 rounded"
            />
            <span className="text-sm text-gray-300">无头模式</span>
          </label>
        </div>

        <div className="mt-4 flex gap-3">
          <button
            onClick={start}
            disabled={status === "running"}
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
          >
            ▶ 开始注册
          </button>
          {status === "running" && (
            <button
              onClick={stop}
              className="px-4 py-2 bg-red-600/30 hover:bg-red-600/50 text-red-400 text-sm font-medium rounded-lg border border-red-600/30 transition-colors"
            >
              ⏹ 停止
            </button>
          )}
          {(status === "done" || status === "error") && (
            <button
              onClick={() => { setStatus("idle"); setLogs([]); setAccounts([]); setJobId(null); }}
              className="px-4 py-2 bg-gray-700/50 hover:bg-gray-700 text-gray-300 text-sm font-medium rounded-lg border border-gray-600/30 transition-colors"
            >
              ↺ 重置
            </button>
          )}
          <div className="flex items-center gap-2 ml-auto">
            {statusBadge()}
            {elapsed > 0 && <span className="text-xs text-gray-500">{elapsed}s</span>}
          </div>
        </div>
      </div>

      {/* Results */}
      {accounts.length > 0 && (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-semibold text-white">
              成功账号 <span className="text-emerald-400">({accounts.length})</span>
            </span>
            <button
              onClick={copyAccounts}
              className="text-xs px-3 py-1 rounded-lg bg-sky-600/20 text-sky-400 hover:bg-sky-600/30 border border-sky-600/30 transition-colors"
            >
              {copied ? "✓ 已复制" : "复制全部"}
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 border-b border-[#21262d]">
                  <th className="text-left py-2 pr-4">邮箱</th>
                  <th className="text-left py-2 pr-4">密码</th>
                  <th className="text-left py-2 pr-4">用户名</th>
                  <th className="text-left py-2">邀请码</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a, i) => (
                  <tr key={i} className="border-b border-[#21262d]/50">
                    <td className="py-1.5 pr-4 text-gray-200 font-mono">{a.email}</td>
                    <td className="py-1.5 pr-4 text-gray-400 font-mono">{a.password}</td>
                    <td className="py-1.5 pr-4 text-gray-400">{a.username ?? "—"}</td>
                    <td className="py-1.5 text-sky-400">{a.invite_code ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Logs */}
      {logs.length > 0 && (
        <div className="bg-[#0d1117] border border-[#21262d] rounded-xl p-4">
          <div className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">运行日志</div>
          <div className="h-72 overflow-y-auto font-mono text-xs space-y-0.5">
            {logs.map((l, i) => (
              <div key={i} className={logColor(l.type)}>{l.message}</div>
            ))}
            <div ref={logEndRef} />
          </div>
        </div>
      )}
    </div>
  );
}
