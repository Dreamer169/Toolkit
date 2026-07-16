import { useState, useRef, useEffect } from "react";

interface RegisteredAccount {
  email: string;
  password: string;
  username?: string; // uid
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

export default function YnRegister() {
  const [count, setCount] = useState(10);
  const [updateProxy, setUpdateProxy] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<JobStatus>("idle");
  const [logs, setLogs] = useState<Array<{ type: string; message: string }>>([]);
  const [accounts, setAccounts] = useState<RegisteredAccount[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [copied, setCopied] = useState(false);
  const [poolStatus, setPoolStatus] = useState<Record<string, number> | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const scrollToBottom = () => logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  useEffect(scrollToBottom, [logs]);

  // 加载proxy pool状态
  const fetchPoolStatus = async () => {
    try {
      const r = await fetch("/api/tools/yonoo-pool-status");
      if (r.ok) setPoolStatus(await r.json());
    } catch {}
  };
  useEffect(() => { fetchPoolStatus(); }, []);

  const stopPoll = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  const startPoll = (jid: string) => {
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`/api/tools/jobs/${jid}`);
        const d = await r.json() as Job;
        setLogs(d.logs ?? []);
        setElapsed(d.finishedAt
          ? Math.round((d.finishedAt - d.startedAt) / 1000)
          : Math.round((Date.now() - d.startedAt) / 1000));
        if (d.accounts?.length) setAccounts(d.accounts);
        if (d.status === "done" || d.status === "failed" || d.status === "stopped") {
          setStatus(d.status === "done" ? "done" : "error");
          stopPoll();
          fetchPoolStatus();
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
      const r = await fetch("/api/tools/yn-register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count, updateProxy }),
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
      `email=${a.email} | password=${a.password}${a.username ? ` | uid=${a.username}` : ""}`
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
    if (status === "running") return (
      <span className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full bg-sky-500/15 text-sky-400">
        <span className="w-1.5 h-1.5 rounded-full bg-sky-400 animate-pulse" />运行中 {elapsed}s
      </span>
    );
    if (status === "done") return (
      <span className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full bg-emerald-500/15 text-emerald-400">
        ✓ 完成 {elapsed}s
      </span>
    );
    if (status === "error") return (
      <span className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full bg-red-500/15 text-red-400">
        ✗ 失败
      </span>
    );
    return null;
  };

  return (
    <div className="space-y-4 max-w-2xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-white font-bold text-xl flex items-center gap-2">
            <span className="text-2xl">🌀</span> Yonoo 注册工具
          </h2>
          <p className="text-gray-500 text-sm mt-0.5">
            Yn-Register-Tool — 纯 HTTP 直连，无需浏览器
          </p>
        </div>
        {statusBadge()}
      </div>

      {/* Pool 状态 */}
      {poolStatus && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4">
          <div className="text-xs font-semibold text-gray-500 mb-3 uppercase tracking-wide">
            Yonoo Proxy 账号池
          </div>
          <div className="grid grid-cols-4 gap-3">
            {[
              { label: "活跃", val: poolStatus.active, color: "text-emerald-400" },
              { label: "隔离", val: poolStatus.isolated, color: "text-amber-400" },
              { label: "禁用", val: poolStatus.disabled, color: "text-red-400" },
              { label: "磁盘存档", val: poolStatus.saved_on_disk, color: "text-gray-300" },
            ].map(({ label, val, color }) => (
              <div key={label} className="text-center">
                <div className={"text-2xl font-bold " + color}>{val ?? "—"}</div>
                <div className="text-xs text-gray-500 mt-0.5">{label}</div>
              </div>
            ))}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-[#21262d] rounded-full overflow-hidden">
              <div
                className="h-full bg-emerald-500 rounded-full transition-all"
                style={{ width: Math.min(100, ((poolStatus.active ?? 0) / (poolStatus.target ?? 500)) * 100) + "%" }}
              />
            </div>
            <span className="text-xs text-gray-500">
              {poolStatus.active}/{poolStatus.target} ({Math.round(((poolStatus.active ?? 0) / (poolStatus.target ?? 500)) * 100)}%)
            </span>
          </div>
        </div>
      )}

      {/* 配置区 */}
      <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-5 space-y-4">
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide">注册配置</div>

        <div>
          <label className="text-sm text-gray-300 mb-1.5 block">
            注册数量 <span className="text-sky-400 font-bold">{count}</span>
          </label>
          <input
            type="range" min={1} max={200} step={1} value={count}
            onChange={e => setCount(Number(e.target.value))}
            className="w-full accent-sky-500"
          />
          <div className="flex justify-between text-xs text-gray-600 mt-0.5">
            <span>1</span><span>50</span><span>100</span><span>200</span>
          </div>
        </div>

        <label className="flex items-center gap-3 cursor-pointer select-none">
          <div
            onClick={() => setUpdateProxy(v => !v)}
            className={"relative inline-flex h-5 w-9 items-center rounded-full transition-colors " +
              (updateProxy ? "bg-emerald-500" : "bg-[#30363d]")}
          >
            <span className={"inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform " +
              (updateProxy ? "translate-x-4" : "translate-x-1")} />
          </div>
          <span className="text-sm text-gray-300">
            注册成功后写入 accounts.json 并重启 yonoo-proxy
          </span>
        </label>

        <div className="bg-[#0d1117] rounded-lg p-3 text-xs text-gray-500 space-y-1">
          <div>• 端点：<code className="text-sky-400">https://yonoo.ai/api/auth/register</code>（无验证码/无邮箱验证）</div>
          <div>• 连接：VPS 直连（不走 SOCKS 代理）</div>
          <div>• 账号密码统一：<code className="text-amber-400">Pool@Pass2026!x</code></div>
          <div>• 专属 Key：<code className="text-emerald-400 break-all">ynnnuPqlLQN8CRV5qLfuTr3zVyCscnKFSzjECDmDV3Z3hv1sm</code></div>
        </div>
      </div>

      {/* 操作按钮 */}
      <div className="flex gap-3">
        {status !== "running" ? (
          <button
            onClick={start}
            className="flex-1 py-3 bg-sky-600 hover:bg-sky-500 rounded-xl text-white text-sm font-semibold transition-colors"
          >
            🚀 开始注册
          </button>
        ) : (
          <button
            onClick={stop}
            className="flex-1 py-3 bg-red-600/80 hover:bg-red-600 rounded-xl text-white text-sm font-semibold transition-colors"
          >
            ⏹ 停止
          </button>
        )}
        <button
          onClick={fetchPoolStatus}
          className="px-4 py-3 bg-[#21262d] hover:bg-[#30363d] rounded-xl text-gray-300 text-sm transition-colors"
        >
          刷新状态
        </button>
      </div>

      {/* 账号结果 */}
      {accounts.length > 0 && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-semibold text-white">
              注册账号 <span className="text-emerald-400">({accounts.length})</span>
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
                  <th className="text-left py-2">UID</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a, i) => (
                  <tr key={i} className="border-b border-[#21262d]/50">
                    <td className="py-1.5 pr-4 text-gray-200 font-mono">{a.email}</td>
                    <td className="py-1.5 pr-4 text-gray-400 font-mono">{a.password}</td>
                    <td className="py-1.5 text-sky-400 font-mono">{a.username ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 日志 */}
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
