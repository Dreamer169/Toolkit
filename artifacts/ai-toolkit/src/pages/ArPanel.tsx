import { useEffect, useRef, useState, useCallback } from "react";

const API = "/api/ar/admin";
const ADMIN_PW = "yu123456";

interface ArtAccount {
  id: string;
  email: string;
  enabled: boolean;
  disabled_reason: string;
  create_time: string;
  usage: number;
  max_usage: number;
  user_id: number;
  quarantine_until: number;
  rate_limit_until: number;
}

interface ArtStatus {
  ok: boolean;
  version: string;
  service: string;
  pool: {
    active: number; enabled: number; occupied: number; cooling: number;
    quarantined: number; rate_limited: number; total: number;
    total_credits_left: number;
  };
  maintainer: {
    pool_active: number; pool_cap: number; pool_total: number;
    registered: number; target: number; today: string;
  };
  proxy: { resi_cooled: number; resi_total: number };
  settings: { admin_pw_set: boolean; chat_proxy: string; reg_proxy: string };
}

function apiFetch(path: string, method = "GET", body?: object) {
  return fetch(`${API}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${ADMIN_PW}`,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }).then(r => r.json());
}

function fmtTime(ts: number) {
  if (!ts || ts <= 0) return "—";
  return new Date(ts * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function Stat({ label, value, sub, tone }: { label: string; value: string | number; sub?: string; tone?: "ok" | "warn" | "err" | "dim" }) {
  const color = tone === "ok" ? "text-emerald-400" : tone === "warn" ? "text-amber-400" : tone === "err" ? "text-red-400" : tone === "dim" ? "text-gray-500" : "text-white";
  return (
    <div className="bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2 min-w-0">
      <div className="text-[10px] text-gray-500 uppercase tracking-wide truncate">{label}</div>
      <div className={`text-lg font-bold ${color} leading-tight`}>{value}</div>
      {sub && <div className="text-[10px] text-gray-600 mt-0.5">{sub}</div>}
    </div>
  );
}

function Pill({ ok, label }: { ok: boolean; label?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium ${ok ? "bg-emerald-500/15 text-emerald-400" : "bg-gray-500/15 text-gray-500"}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-gray-600"}`} />
      {label ?? (ok ? "活跃" : "禁用")}
    </span>
  );
}

function Section({ title, icon, badge, defaultOpen = true, action, children }: {
  title: string; icon: string; badge?: React.ReactNode; defaultOpen?: boolean;
  action?: React.ReactNode; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 cursor-pointer select-none hover:bg-[#1c2129]"
        onClick={() => setOpen(v => !v)}>
        <div className="flex items-center gap-2">
          <span>{icon}</span>
          <span className="text-sm font-semibold text-white">{title}</span>
          {badge}
          <svg className={`w-3.5 h-3.5 text-gray-600 transition-transform ml-0.5 ${open ? "rotate-180" : ""}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </div>
        <div onClick={e => e.stopPropagation()}>{action}</div>
      </div>
      {open && <div className="px-4 pb-4">{children}</div>}
    </div>
  );
}

export default function ArPanel() {
  const [status, setStatus] = useState<ArtStatus | null>(null);
  const [accounts, setAccounts] = useState<ArtAccount[]>([]);
  const [page, setPage] = useState(0);
  const [accountsTotal, setAccountsTotal] = useState(0);
  const [logs, setLogs] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);
  const [search, setSearch] = useState("");
  const [regCount, setRegCount] = useState(1);
  const [logsOpen, setLogsOpen] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const notify = (msg: string, ok = true) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 3000);
  };


  const refresh = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([
        apiFetch("/status"),
        apiFetch(`/accounts?limit=50&offset=${page * 50}`),
      ]);
      if (s.ok !== undefined) { setStatus(s); setErr(null); }
      else setErr(s.error ?? "status 接口异常");
      if (a.accounts) { setAccounts(a.accounts); setAccountsTotal(a.total ?? a.accounts.length); }
    } catch (e: any) {
      setErr("网络错误: " + e.message);
    } finally {
      setLoading(false);
    }
  }, [page]);

  const fetchLogs = async () => {
    try {
      const r = await apiFetch("/logs");
      const lines: string[] = r.lines ?? r.log?.split("\n") ?? [];
      setLogs(lines.filter(Boolean).slice(-200).reverse());
    } catch {}
  };

  useEffect(() => {
    refresh();
    timerRef.current = setInterval(refresh, 10000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [refresh]);

  useEffect(() => {
    if (logsOpen) fetchLogs();
  }, [logsOpen]);

  async function act(path: string, method: string, body?: object, msg?: string) {
    setBusy(path);
    try {
      const r = await apiFetch(path, method, body);
      if (r.ok !== false) notify(msg ?? "操作成功");
      else notify(r.error ?? "操作失败", false);
      await refresh();
    } catch (e: any) {
      notify("网络错误: " + e.message, false);
    } finally {
      setBusy(null);
    }
  }

  const filtered = accounts.filter(a =>
    !search || a.email.toLowerCase().includes(search.toLowerCase()) ||
    a.id.toLowerCase().includes(search.toLowerCase())
  );

  const p = status?.pool;
  const m = status?.maintainer;

  if (loading) return (
    <div className="flex items-center justify-center py-20 text-gray-500">
      <svg className="animate-spin w-6 h-6 mr-3 text-violet-500" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
      </svg>
      正在加载…
    </div>
  );

  if (err && !status) return (
    <div className="flex flex-col items-center justify-center py-20 gap-4">
      <div className="text-red-400 text-center">
        <div className="text-3xl mb-2">⚠️</div>
        <div className="font-semibold">Arting 账号池离线</div>
        <div className="text-sm text-gray-500 mt-1">{err}</div>
      </div>
      <button onClick={refresh} className="px-4 py-2 bg-violet-600 hover:bg-violet-500 rounded-lg text-sm text-white">重试</button>
    </div>
  );

  return (
    <div className="space-y-4 pb-8">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-xl shadow-xl text-sm font-medium transition-all ${toast.ok ? "bg-emerald-900/90 border border-emerald-500/40 text-emerald-300" : "bg-red-900/90 border border-red-500/40 text-red-300"}`}>
          {toast.ok ? "✓ " : "✗ "}{toast.msg}
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">🎨 Arting 账号池</h1>
          <p className="text-gray-500 text-sm mt-0.5">art-register-tool {status?.version ?? ""} · 自动注册 &amp; OpenAI 兼容反代</p>
        </div>
        <div className="flex items-center gap-2">
          {err && <span className="text-amber-400 text-xs">⚠ {err}</span>}
          <button onClick={refresh} disabled={!!busy}
            className="p-2 rounded-lg bg-[#21262d] hover:bg-[#30363d] text-gray-400 disabled:opacity-40">
            <svg className={`w-4 h-4 ${busy ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 md:grid-cols-8 gap-2">
        <Stat label="活跃账号" value={p?.active ?? "—"} tone={p && p.active > 0 ? "ok" : "err"} />
        <Stat label="总账号" value={p?.total ?? "—"} />
        <Stat label="占用中"
          value={(p?.occupied ?? 0) + (p?.cooling ?? 0)}
          sub={p && ((p.occupied ?? 0) > 0 || (p.cooling ?? 0) > 0)
            ? `${p.occupied ?? 0} 处理中 · ${p.cooling ?? 0} 冷却中`
            : undefined}
          tone={p && ((p.occupied ?? 0) + (p?.cooling ?? 0)) > 0 ? "warn" : "dim"} />
        <Stat label="隔离中" value={p?.quarantined ?? 0} tone={(p?.quarantined ?? 0) > 0 ? "warn" : "dim"} />
        <Stat label="剩余积分" value={p?.total_credits_left?.toLocaleString() ?? "—"} sub="活跃账号余额合计" />
        <Stat label="今日注册" value={m?.registered ?? 0} tone={(m?.registered ?? 0) > 0 ? "ok" : "dim"} sub={m?.today ?? ""} />
        <Stat label="池目标" value={m ? `${m.pool_active}/${m.pool_cap}` : "—"} />
        <Stat label="住宅代理" value={status?.proxy.resi_total ?? "—"} sub={`${status?.proxy.resi_cooled ?? 0} 冷却`} />
      </div>

      {/* Pool Status + Register */}
      <Section title="注册控制" icon="⚡" defaultOpen={true}
        action={
          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-500">数量</label>
            <input type="number" min={1} max={20} value={regCount} onChange={e => setRegCount(Number(e.target.value))}
              className="w-16 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 text-xs text-white" />
            <button
              disabled={!!busy}
              onClick={() => act("/register", "POST", { count: regCount }, `已触发注册 ${regCount} 个账号`)}
              className="px-3 py-1.5 bg-violet-600 hover:bg-violet-500 disabled:opacity-40 rounded-lg text-xs text-white font-medium">
              {busy === "/register" ? "注册中…" : "手动注册"}
            </button>
          </div>
        }>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-1">
          <div className="bg-[#0d1117] rounded-lg p-3 border border-[#21262d]">
            <div className="text-[10px] text-gray-500 uppercase mb-1">聊天代理</div>
            <div className="text-sm text-white font-mono">{status?.settings.chat_proxy ?? "—"}</div>
          </div>
          <div className="bg-[#0d1117] rounded-lg p-3 border border-[#21262d]">
            <div className="text-[10px] text-gray-500 uppercase mb-1">注册代理</div>
            <div className="text-sm text-white font-mono">{status?.settings.reg_proxy ?? "—"}</div>
          </div>
          <div className="bg-[#0d1117] rounded-lg p-3 border border-[#21262d]">
            <div className="text-[10px] text-gray-500 uppercase mb-1">速率限制</div>
            <div className="text-sm text-white font-mono">{p?.rate_limited ?? 0} 个账号</div>
          </div>
          <div className="bg-[#0d1117] rounded-lg p-3 border border-[#21262d]">
            <div className="text-[10px] text-gray-500 uppercase mb-1">API 端点</div>
            <div className="text-xs text-blue-400 font-mono break-all">/api/ar/v1/chat/completions</div>
          </div>
        </div>

        {/* 翻页控件 */}
        {!search && accountsTotal > 50 && (
          <div className="flex items-center justify-between px-1 pt-3">
            <span className="text-xs text-gray-500">
              第 {page + 1} / {Math.ceil(accountsTotal / 50)} 页 · 共 {accountsTotal} 个账号
            </span>
            <div className="flex gap-2">
              <button
                disabled={page === 0}
                onClick={() => setPage(p => p - 1)}
                className="px-3 py-1 rounded-lg bg-[#21262d] hover:bg-[#30363d] text-sm text-gray-300 disabled:opacity-30">
                ← 上一页
              </button>
              <button
                disabled={(page + 1) * 50 >= accountsTotal}
                onClick={() => setPage(p => p + 1)}
                className="px-3 py-1 rounded-lg bg-[#21262d] hover:bg-[#30363d] text-sm text-gray-300 disabled:opacity-30">
                下一页 →
              </button>
            </div>
          </div>
        )}
      </Section>

      {/* Account List */}
      <Section title="账号列表" icon="👤"
        badge={<span className="text-xs text-gray-500 bg-[#21262d] px-2 py-0.5 rounded-full">{filtered.length}</span>}
        defaultOpen={true}
        action={
          <input value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} placeholder="搜索邮箱 / ID…"
            className="bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-1 text-xs text-white placeholder-gray-600 w-52 outline-none focus:border-violet-500" />
        }>
        <div className="overflow-x-auto rounded-lg border border-[#21262d] mt-1">
          <table className="w-full text-xs">
            <thead className="bg-[#0d1117] text-gray-500 sticky top-0">
              <tr>
                <th className="text-left px-3 py-2 font-medium">邮箱</th>
                <th className="text-center px-3 py-2 font-medium">余额</th>
                <th className="text-center px-3 py-2 font-medium">状态</th>
                <th className="text-left px-3 py-2 font-medium">注册时间</th>
                <th className="text-center px-3 py-2 font-medium">UID</th>
                <th className="text-right px-3 py-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(a => {
                const now = Date.now() / 1000;
                const inQuarantine = a.quarantine_until > now;
                const inRateLimit  = !inQuarantine && a.rate_limit_until > now;
                return (
                  <tr key={a.id} className="border-t border-[#21262d] hover:bg-[#0d1117]/60">
                    <td className="px-3 py-2 text-gray-200 truncate max-w-[220px]" title={a.email}>{a.email}</td>
                    <td className="px-3 py-2 text-center">
                      <span className={`font-mono ${a.usage >= a.max_usage ? "text-amber-400" : "text-emerald-400"}`}>
                        {Math.max(0, a.max_usage - a.usage)}/{a.max_usage}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-center">
                      {inQuarantine
                        ? <Pill ok={false} label={`隔离至 ${fmtTime(a.quarantine_until)}`} />
                        : inRateLimit
                          ? <Pill ok={false} label={`限速至 ${fmtTime(a.rate_limit_until)}`} />
                          : <Pill ok={a.enabled} label={a.enabled ? "活跃" : (a.disabled_reason || "禁用")} />}
                    </td>
                    <td className="px-3 py-2 text-gray-500">{a.create_time?.slice(0, 10) ?? "—"}</td>
                    <td className="px-3 py-2 text-center text-gray-600 font-mono text-[10px]">{a.user_id}</td>
                    <td className="px-3 py-2 text-right space-x-2">
                      {a.enabled ? (
                        <button disabled={busy === `/accounts/${a.id}/disable`}
                          onClick={() => act(`/accounts/${a.id}/disable`, "POST", {}, `已禁用 ${a.email}`)}
                          className="text-amber-400 hover:text-amber-300 disabled:opacity-40">禁用</button>
                      ) : (
                        <button disabled={busy === `/accounts/${a.id}/enable`}
                          onClick={() => act(`/accounts/${a.id}/enable`, "POST", {}, `已启用 ${a.email}`)}
                          className="text-emerald-400 hover:text-emerald-300 disabled:opacity-40">启用</button>
                      )}
                      <button disabled={!!busy}
                        onClick={() => { if (confirm(`删除 ${a.email}？`)) act(`/accounts/${a.id}/delete`, "POST", {}, `已删除 ${a.email}`); }}
                        className="text-red-400 hover:text-red-300 disabled:opacity-40">删除</button>
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr><td colSpan={6} className="text-center text-gray-600 py-8">无匹配账号</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>

      {/* Logs */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-[#1c2129]"
          onClick={() => { setLogsOpen(v => !v); }}>
          <div className="flex items-center gap-2">
            <span>📋</span>
            <span className="text-sm font-semibold text-white">实时日志</span>
            <svg className={`w-3.5 h-3.5 text-gray-600 transition-transform ml-0.5 ${logsOpen ? "rotate-180" : ""}`}
              fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </div>
          <button onClick={e => { e.stopPropagation(); if (logsOpen) fetchLogs(); }}
            className="text-xs text-gray-500 hover:text-gray-300 px-2 py-1 rounded bg-[#21262d]">刷新</button>
        </div>
        {logsOpen && (
          <div className="px-4 pb-4">
            <div className="bg-[#0d1117] rounded-lg border border-[#21262d] p-3 font-mono text-[11px] text-gray-400 max-h-72 overflow-y-auto">
              {logs.length === 0
                ? <span className="text-gray-600">暂无日志</span>
                : logs.map((l, i) => <div key={i} className="leading-5">{l}</div>)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
