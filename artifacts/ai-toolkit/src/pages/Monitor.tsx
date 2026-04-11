import { useState, useEffect, useRef, useCallback } from "react";

const API = import.meta.env.BASE_URL.replace(/\/$/, "") + "/api";

// ── 类型 ──────────────────────────────────────────────────────────────────────
interface JobSummary {
  id: string;
  status: "running" | "done" | "stopped" | "error";
  startedAt: number;
  logCount: number;
  accountCount: number;
  exitCode: number | null;
  lastLog: { type: string; message: string } | null;
}
interface LogEntry { type: string; message: string }
interface ProxyStats { total: number; idle: number; active: number; banned: number }
interface DbStats { accounts: number; identities: number; temp_emails: number; proxies: number }
interface RecentAccount { id: number; platform: string; email: string; status: string; created_at: string }
interface ApiHealth { ok: boolean; latency: number }

// ── 工具函数 ──────────────────────────────────────────────────────────────────
function elapsed(ms: number) {
  const s = Math.floor((Date.now() - ms) / 1000);
  if (s < 60) return `${s}s 前`;
  if (s < 3600) return `${Math.floor(s / 60)}m${s % 60}s 前`;
  return `${Math.floor(s / 3600)}h 前`;
}
function statusColor(s: string) {
  if (s === "running") return "text-blue-400";
  if (s === "done")    return "text-emerald-400";
  if (s === "stopped") return "text-amber-400";
  return "text-red-400";
}
function statusDot(s: string) {
  if (s === "running") return "bg-blue-400 animate-pulse";
  if (s === "done")    return "bg-emerald-400";
  if (s === "stopped") return "bg-amber-400";
  return "bg-red-400";
}
function logColor(type: string) {
  if (type === "ok" || type === "done") return "text-emerald-400";
  if (type === "error") return "text-red-400";
  if (type === "warn")  return "text-amber-400";
  if (type === "start") return "text-blue-400";
  return "text-gray-300";
}
function platformBadge(p: string) {
  const m: Record<string, string> = {
    outlook: "bg-blue-900/40 text-blue-300",
    gmail:   "bg-red-900/40 text-red-300",
    openai:  "bg-emerald-900/40 text-emerald-300",
  };
  return m[p.toLowerCase()] ?? "bg-gray-800 text-gray-400";
}

// ── 메인 컴포넌트 ─────────────────────────────────────────────────────────────
export default function Monitor() {
  const [jobs, setJobs]               = useState<JobSummary[]>([]);
  const [selectedJob, setSelectedJob] = useState<string | null>(null);
  const [jobLogs, setJobLogs]         = useState<LogEntry[]>([]);
  const [sinceIdx, setSinceIdx]       = useState(0);
  const [proxyStats, setProxyStats]   = useState<ProxyStats | null>(null);
  const [dbStats, setDbStats]         = useState<DbStats | null>(null);
  const [recentAcc, setRecentAcc]     = useState<RecentAccount[]>([]);
  const [health, setHealth]           = useState<ApiHealth | null>(null);
  const [lastRefresh, setLastRefresh] = useState(Date.now());
  const [paused, setPaused]           = useState(false);

  const logRef   = useRef<HTMLDivElement>(null);
  const sinceRef = useRef(0);

  // 自动滚动日志
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [jobLogs]);

  // ── 拉取 API 健康 ──────────────────────────────────────────────────────────
  const checkHealth = useCallback(async () => {
    const t0 = Date.now();
    try {
      const r = await fetch(`${API}/data/stats`);
      setHealth({ ok: r.ok, latency: Date.now() - t0 });
    } catch {
      setHealth({ ok: false, latency: -1 });
    }
  }, []);

  // ── 拉取代理池统计 ─────────────────────────────────────────────────────────
  const fetchProxy = useCallback(async () => {
    try {
      const r = await fetch(`${API}/data/proxies?limit=1`).then(r => r.json());
      if (r.success) {
        // 从 proxies 列表计算状态分布
        const all = await fetch(`${API}/data/proxies?limit=9999`).then(r => r.json());
        const list: { status: string }[] = all.proxies ?? [];
        setProxyStats({
          total:  r.total ?? 0,
          idle:   list.filter(p => p.status === "idle").length,
          active: list.filter(p => p.status === "active").length,
          banned: list.filter(p => p.status === "banned").length,
        });
      }
    } catch {}
  }, []);

  // ── 拉取 DB 统计 & 最近账号 ───────────────────────────────────────────────
  const fetchStats = useCallback(async () => {
    try {
      const r = await fetch(`${API}/data/stats`).then(r => r.json());
      if (r.success) {
        setDbStats({
          accounts:   r.counts.accounts   ?? 0,
          identities: r.counts.identities ?? 0,
          temp_emails:r.counts.temp_emails ?? 0,
          proxies:    r.counts.proxies     ?? 0,
        });
      }
    } catch {}
    try {
      const r = await fetch(`${API}/data/accounts?limit=6`).then(r => r.json());
      if (r.success) setRecentAcc(r.accounts ?? []);
    } catch {}
  }, []);

  // ── 拉取任务列表 ───────────────────────────────────────────────────────────
  const fetchJobs = useCallback(async () => {
    try {
      const r = await fetch(`${API}/tools/jobs`).then(r => r.json());
      if (r.success) {
        setJobs(r.jobs);
        setLastRefresh(Date.now());
        // 如果有 running 任务且没有选中，自动选中最新的
        setSelectedJob(prev => {
          if (!prev && r.jobs.find((j: JobSummary) => j.status === "running")) {
            return r.jobs.find((j: JobSummary) => j.status === "running")?.id ?? null;
          }
          return prev;
        });
      }
    } catch {}
  }, []);

  // ── 拉取选中任务的日志 ────────────────────────────────────────────────────
  const fetchLogs = useCallback(async (jobId: string) => {
    try {
      const r = await fetch(`${API}/tools/outlook/register/${jobId}?since=${sinceRef.current}`);
      if (r.status === 404) { setSinceIdx(0); sinceRef.current = 0; return; }
      const d = await r.json();
      if (!d.success) return;
      const newLines: LogEntry[] = d.logs ?? [];
      if (newLines.length > 0) {
        setJobLogs(prev => [...prev, ...newLines]);
      }
      if (d.nextSince != null) { sinceRef.current = d.nextSince; setSinceIdx(d.nextSince); }
    } catch {}
  }, []);

  // ── 切换选中任务 ──────────────────────────────────────────────────────────
  useEffect(() => {
    setJobLogs([]);
    sinceRef.current = 0;
    setSinceIdx(0);
  }, [selectedJob]);

  // ── 主轮询循环 ─────────────────────────────────────────────────────────────
  useEffect(() => {
    checkHealth();
    fetchProxy();
    fetchStats();
    fetchJobs();
  }, [checkHealth, fetchProxy, fetchStats, fetchJobs]);

  useEffect(() => {
    if (paused) return;
    const t = setInterval(() => {
      fetchJobs();
      if (selectedJob) fetchLogs(selectedJob);
    }, 2000);
    return () => clearInterval(t);
  }, [paused, fetchJobs, fetchLogs, selectedJob]);

  useEffect(() => {
    if (paused) return;
    const t = setInterval(() => {
      fetchStats();
      fetchProxy();
      checkHealth();
    }, 8000);
    return () => clearInterval(t);
  }, [paused, fetchStats, fetchProxy, checkHealth]);

  // ── 停止任务 ──────────────────────────────────────────────────────────────
  async function stopJob(id: string) {
    await fetch(`${API}/tools/outlook/register/${id}`, { method: "DELETE" }).catch(() => {});
    fetchJobs();
  }

  const runningJobs = jobs.filter(j => j.status === "running");
  const otherJobs   = jobs.filter(j => j.status !== "running");
  const selectedJobObj = jobs.find(j => j.id === selectedJob);

  return (
    <div className="space-y-4">
      {/* ── 顶部标题栏 ──────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <span className="text-lg">📡</span> 实时监控中心
          </h1>
          <p className="text-xs text-gray-500 mt-0.5">每 2s 自动刷新 · 无需手动操作</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-600">上次刷新 {elapsed(lastRefresh)}</span>
          <button
            onClick={() => setPaused(p => !p)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${paused ? "bg-blue-700 text-white" : "bg-[#21262d] text-gray-400 hover:text-white"}`}
          >
            {paused ? "▶ 恢复" : "⏸ 暂停"}
          </button>
          <button
            onClick={() => { fetchJobs(); fetchStats(); fetchProxy(); checkHealth(); }}
            className="px-3 py-1.5 rounded-lg text-xs bg-[#21262d] text-gray-400 hover:text-white transition-colors"
          >
            🔄 立即刷新
          </button>
        </div>
      </div>

      {/* ── 状态卡片行 ──────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {/* API 健康 */}
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
          <div className="text-xs text-gray-500 mb-1">API 服务器</div>
          {health ? (
            <>
              <div className={`text-lg font-bold ${health.ok ? "text-emerald-400" : "text-red-400"}`}>
                {health.ok ? "● 正常" : "● 离线"}
              </div>
              <div className="text-xs text-gray-600 mt-1">
                延迟 {health.latency >= 0 ? `${health.latency}ms` : "—"}
              </div>
            </>
          ) : (
            <div className="text-gray-600 text-sm animate-pulse">检测中…</div>
          )}
        </div>

        {/* 活跃任务 */}
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
          <div className="text-xs text-gray-500 mb-1">注册任务</div>
          <div className="text-lg font-bold text-white">
            {runningJobs.length > 0 ? (
              <span className="text-blue-400">⚙ {runningJobs.length} 运行中</span>
            ) : (
              <span className="text-gray-500">— 空闲</span>
            )}
          </div>
          <div className="text-xs text-gray-600 mt-1">历史共 {jobs.length} 次</div>
        </div>

        {/* 代理池 */}
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
          <div className="text-xs text-gray-500 mb-1">代理池</div>
          {proxyStats ? (
            <>
              <div className="text-lg font-bold text-emerald-400">{proxyStats.total} 个</div>
              <div className="text-xs text-gray-600 mt-1 space-x-2">
                <span className="text-gray-400">空闲 {proxyStats.idle}</span>
                <span className="text-blue-400">活跃 {proxyStats.active}</span>
                <span className="text-red-400">封禁 {proxyStats.banned}</span>
              </div>
            </>
          ) : (
            <div className="text-gray-600 text-sm animate-pulse">加载中…</div>
          )}
        </div>

        {/* 账号库 */}
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
          <div className="text-xs text-gray-500 mb-1">数据库</div>
          {dbStats ? (
            <>
              <div className="text-lg font-bold text-white">{dbStats.accounts} 账号</div>
              <div className="text-xs text-gray-600 mt-1">
                身份 {dbStats.identities} · 邮箱 {dbStats.temp_emails}
              </div>
            </>
          ) : (
            <div className="text-gray-600 text-sm animate-pulse">加载中…</div>
          )}
        </div>
      </div>

      {/* ── 主体：任务列表 + 实时日志 ──────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">

        {/* 左：任务列表 */}
        <div className="lg:col-span-2 space-y-2">
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-sm font-semibold text-gray-300">注册任务队列</h2>
            <span className="text-xs text-gray-600">{jobs.length} 条记录</span>
          </div>

          {jobs.length === 0 ? (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-8 text-center text-gray-600 text-sm">
              暂无任务<br/>
              <span className="text-xs">在「完整工作流」页面启动注册后会显示在这里</span>
            </div>
          ) : (
            <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
              {/* 运行中 */}
              {runningJobs.length > 0 && (
                <div className="text-xs text-blue-400/70 px-1 font-medium">▶ 运行中</div>
              )}
              {[...runningJobs, ...otherJobs].map(job => (
                <button
                  key={job.id}
                  onClick={() => setSelectedJob(prev => prev === job.id ? null : job.id)}
                  className={`w-full text-left rounded-xl border p-3 transition-all ${
                    selectedJob === job.id
                      ? "bg-blue-900/20 border-blue-700/60"
                      : "bg-[#161b22] border-[#21262d] hover:border-[#30363d]"
                  }`}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-2">
                      <div className={`w-2 h-2 rounded-full flex-shrink-0 ${statusDot(job.status)}`} />
                      <span className={`text-xs font-semibold ${statusColor(job.status)}`}>
                        {job.status === "running" ? "运行中" : job.status === "done" ? "完成" : job.status === "stopped" ? "已停止" : "出错"}
                      </span>
                    </div>
                    <span className="text-xs text-gray-600">{elapsed(job.startedAt)}</span>
                  </div>
                  <div className="text-xs text-gray-500 font-mono truncate">{job.id}</div>
                  {job.lastLog && (
                    <div className={`text-xs mt-1.5 truncate ${logColor(job.lastLog.type)}`}>
                      {job.lastLog.message}
                    </div>
                  )}
                  <div className="flex items-center gap-3 mt-2 text-xs text-gray-600">
                    <span>📝 {job.logCount} 条日志</span>
                    {job.accountCount > 0 && (
                      <span className="text-emerald-500">✅ {job.accountCount} 个账号</span>
                    )}
                    {job.status === "running" && (
                      <button
                        onClick={e => { e.stopPropagation(); stopJob(job.id); }}
                        className="ml-auto text-red-400 hover:text-red-300 transition-colors"
                      >
                        ⏹ 停止
                      </button>
                    )}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* 右：实时日志 */}
        <div className="lg:col-span-3 flex flex-col bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d]">
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${selectedJobObj?.status === "running" ? "bg-blue-400 animate-pulse" : selectedJobObj ? "bg-emerald-400" : "bg-gray-700"}`} />
              <span className="text-sm font-semibold text-gray-300">
                {selectedJobObj ? `任务日志 · ${sinceIdx} 条` : "选中左侧任务查看日志"}
              </span>
            </div>
            {selectedJobObj && (
              <button
                onClick={() => { setJobLogs([]); sinceRef.current = 0; setSinceIdx(0); if (selectedJob) fetchLogs(selectedJob); }}
                className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
              >
                清空
              </button>
            )}
          </div>

          <div
            ref={logRef}
            className="flex-1 overflow-y-auto p-4 font-mono text-xs space-y-0.5 min-h-[300px] max-h-[420px]"
          >
            {!selectedJobObj ? (
              <div className="h-full flex items-center justify-center text-gray-700">
                从左侧选择一个任务以查看实时日志
              </div>
            ) : jobLogs.length === 0 ? (
              <div className="text-gray-700 animate-pulse">等待日志…</div>
            ) : (
              jobLogs.map((l, i) => (
                <div key={i} className={`leading-5 ${logColor(l.type)}`}>
                  <span className="text-gray-700 select-none mr-2">{String(i + 1).padStart(3, "0")}</span>
                  {l.message}
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* ── 最近账号 ──────────────────────────────────────────────────────── */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-[#21262d] flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-300">最近入库账号</h2>
          <span className="text-xs text-gray-600">共 {dbStats?.accounts ?? "—"} 条</span>
        </div>
        {recentAcc.length === 0 ? (
          <div className="px-4 py-6 text-center text-gray-600 text-sm">暂无账号记录</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[#21262d]">
                  {["平台", "邮箱", "状态", "入库时间"].map(h => (
                    <th key={h} className="text-left px-4 py-2 text-gray-600 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {recentAcc.map((acc, i) => (
                  <tr key={acc.id} className={`border-b border-[#1c2128] ${i % 2 === 0 ? "" : "bg-[#0d1117]/30"}`}>
                    <td className="px-4 py-2.5">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${platformBadge(acc.platform)}`}>
                        {acc.platform}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 font-mono text-gray-300">{acc.email}</td>
                    <td className="px-4 py-2.5">
                      <span className={`${acc.status === "active" ? "text-emerald-400" : acc.status === "inactive" ? "text-red-400" : "text-amber-400"}`}>
                        {acc.status === "active" ? "✅ 正常" : acc.status === "inactive" ? "❌ 失效" : acc.status}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-gray-600">
                      {acc.created_at ? new Date(acc.created_at).toLocaleString("zh-CN") : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── 代理池明细 ────────────────────────────────────────────────────── */}
      {proxyStats && (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
          <h2 className="text-sm font-semibold text-gray-300 mb-3">代理池状态</h2>
          <div className="flex items-center gap-4">
            <div className="flex-1 h-3 bg-[#21262d] rounded-full overflow-hidden flex">
              {proxyStats.total > 0 && (
                <>
                  <div
                    className="h-full bg-gray-500/60 transition-all"
                    style={{ width: `${(proxyStats.idle / proxyStats.total) * 100}%` }}
                  />
                  <div
                    className="h-full bg-blue-500/80 transition-all"
                    style={{ width: `${(proxyStats.active / proxyStats.total) * 100}%` }}
                  />
                  <div
                    className="h-full bg-red-500/80 transition-all"
                    style={{ width: `${(proxyStats.banned / proxyStats.total) * 100}%` }}
                  />
                </>
              )}
            </div>
            <div className="flex items-center gap-4 text-xs shrink-0">
              <span className="text-gray-400">空闲 <strong className="text-white">{proxyStats.idle}</strong></span>
              <span className="text-blue-400">活跃 <strong className="text-white">{proxyStats.active}</strong></span>
              <span className="text-red-400">封禁 <strong className="text-white">{proxyStats.banned}</strong></span>
              <span className="text-gray-600">共 {proxyStats.total}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
