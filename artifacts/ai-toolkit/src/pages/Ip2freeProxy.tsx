import { useState, useRef, useEffect } from "react";

interface RegisteredAccount {
  email: string;
  password: string;
  proxy?: string;
  inviteCode?: string;
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

export default function Ip2freeProxy() {
  const [count, setCount] = useState(1);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [proxy, setProxy] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [ip2freePassword, setIp2freePassword] = useState("");
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
        const r = await fetch(`/api/tools/ip2free/register/${jid}`);
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
      const r = await fetch("/api/tools/ip2free/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count, email, password, proxy, inviteCode, ip2freePassword }),
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
      `email=${a.email} | password=${a.password}${a.proxy ? ` | proxy=${a.proxy}` : ""}${a.inviteCode ? ` | invite=${a.inviteCode}` : ""}`
    ).join("\n");
    navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); });
  };

  const logColor = (type: string) => {
    if (type === "error") return "text-red-400";
    if (type === "warn") return "text-amber-400";
    if (type === "success") return "text-emerald-400";
    if (type === "start") return "text-sky-400";
    return "text-gray-300";
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-white font-bold text-xl flex items-center gap-2">
            <span className="text-sky-400">🌐</span> IP2FREE 代理注册
          </h2>
          <p className="text-gray-500 text-sm mt-0.5">使用 Outlook 邮箱 + 代理在 ip2free.com 注册账号</p>
        </div>
      </div>

      <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <div>
            <label className="text-xs text-gray-500 block mb-1.5">数量</label>
            <input type="number" min={1} max={50} value={count} onChange={(e) => setCount(Math.max(1, parseInt(e.target.value || "1", 10)))}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 focus:border-blue-500 outline-none" />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1.5">Outlook 邮箱</label>
            <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="留空自动选取"
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none font-mono" />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1.5">Outlook 密码</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="留空自动使用数据库"
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none" />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1.5">代理 (可选)</label>
            <input value={proxy} onChange={(e) => setProxy(e.target.value)} placeholder="http://host:port"
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none font-mono" />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1.5">邀请码</label>
            <input value={inviteCode} onChange={(e) => setInviteCode(e.target.value)} placeholder="可选"
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none font-mono" />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1.5">IP2FREE 密码</label>
            <input type="password" value={ip2freePassword} onChange={(e) => setIp2freePassword(e.target.value)} placeholder="默认使用 Outlook 密码"
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:border-blue-500 outline-none" />
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={start} disabled={status === "running"}
            className="px-4 py-2 bg-sky-600 hover:bg-sky-500 disabled:opacity-40 rounded-lg text-white text-sm font-medium transition-colors">
            {status === "running" ? "运行中..." : "启动注册"}
          </button>
          <button onClick={stop} disabled={status !== "running"}
            className="px-4 py-2 bg-red-600/20 hover:bg-red-600/30 text-red-400 border border-red-600/30 disabled:opacity-40 rounded-lg text-sm font-medium transition-colors">
            停止
          </button>
          {status === "running" && <span className="text-xs text-gray-500">已运行 {elapsed}s</span>}
        </div>
      </div>

      {accounts.length > 0 && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-semibold text-white">成功账号 <span className="text-emerald-400">({accounts.length})</span></span>
            <button onClick={copyAccounts} className="text-xs px-3 py-1 rounded-lg bg-sky-600/20 text-sky-400 hover:bg-sky-600/30 border border-sky-600/30 transition-colors">{copied ? "✓ 已复制" : "复制全部"}</button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="text-gray-500 border-b border-[#21262d]"><th className="text-left py-2 pr-4">邮箱</th><th className="text-left py-2 pr-4">密码</th><th className="text-left py-2 pr-4">代理</th><th className="text-left py-2">邀请码</th></tr></thead>
              <tbody>
                {accounts.map((a, i) => (
                  <tr key={i} className="border-b border-[#21262d]/50">
                    <td className="py-1.5 pr-4 text-gray-200 font-mono">{a.email}</td>
                    <td className="py-1.5 pr-4 text-gray-400 font-mono">{a.password}</td>
                    <td className="py-1.5 pr-4 text-gray-400 font-mono">{a.proxy ?? "—"}</td>
                    <td className="py-1.5 text-sky-400">{a.inviteCode ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {logs.length > 0 && (
        <div className="bg-[#0d1117] border border-[#21262d] rounded-xl p-4">
          <div className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">运行日志</div>
          <div className="h-72 overflow-y-auto font-mono text-xs space-y-0.5">
            {logs.map((l, i) => (<div key={i} className={logColor(l.type)}>{l.message}</div>))}
            <div ref={logEndRef} />
          </div>
        </div>
      )}
    </div>
  );
}
