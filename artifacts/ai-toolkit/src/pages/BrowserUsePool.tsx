import { useCallback, useEffect, useState } from "react";

const PROXY = "/api/bu-cl";

type KeyEntry = { key: string; status: "available" | "exhausted" | "failed" };
interface PoolCounts { total: number; available: number; exhausted: number; failed: number }
interface PoolStatus { ok?: boolean; pool: PoolCounts; target: number; models?: { id: string }[] }

function apiFetch(path: string, method = "GET", body?: object) {
  return fetch(`${PROXY}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }).then(async r => {
    const j = await r.json().catch(() => ({ error: "non-json response" }));
    return { ok: r.ok, status: r.status, data: j };
  });
}

function Stat({ label, value, tone, sub }: {
  label: string; value: string | number;
  tone?: "ok" | "warn" | "err" | "dim"; sub?: string;
}) {
  const color = tone === "ok" ? "text-emerald-400"
    : tone === "warn" ? "text-amber-400"
    : tone === "err" ? "text-red-400"
    : tone === "dim" ? "text-gray-500" : "text-white";
  return (
    <div className="bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-2">
      <div className="text-[10px] text-gray-500 uppercase tracking-wide">{label}</div>
      <div className={`text-lg font-bold ${color}`}>{value}</div>
      {sub && <div className="text-[10px] text-gray-600">{sub}</div>}
    </div>
  );
}

function Btn({ onClick, disabled, children, tone = "default", small }: {
  onClick: () => void; disabled?: boolean; children: React.ReactNode;
  tone?: "default" | "green" | "red" | "amber"; small?: boolean;
}) {
  const cls = tone === "green" ? "bg-emerald-600 hover:bg-emerald-500"
    : tone === "red" ? "bg-red-700 hover:bg-red-600"
    : tone === "amber" ? "bg-amber-600 hover:bg-amber-500"
    : "bg-[#21262d] hover:bg-[#30363d]";
  return (
    <button
      onClick={onClick} disabled={disabled}
      className={`${cls} ${small ? "text-xs px-2 py-1" : "text-sm px-3 py-1.5"} rounded-lg text-white font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed`}
    >
      {children}
    </button>
  );
}

export default function BrowserUsePool() {
  const [status, setStatus] = useState<PoolStatus | null>(null);
  const [keys, setKeys] = useState<KeyEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [addKey, setAddKey] = useState("");
  const [addResult, setAddResult] = useState<string | null>(null);
  const [testKey, setTestKey] = useState("");
  const [testResult, setTestResult] = useState<string | null>(null);
  const [solveText, setSolveText] = useState("");
  const [solveResult, setSolveResult] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [autoRefill, setAutoRefill] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  const flash = (text: string, ok = true) => {
    setMsg({ text, ok });
    setTimeout(() => setMsg(null), 3500);
  };

  const loadStatus = useCallback(async () => {
    const r = await apiFetch("/pool/status");
    if (r.ok) setStatus(r.data as PoolStatus);
    setLastRefresh(new Date());
  }, []);

  const loadKeys = useCallback(async () => {
    const r = await apiFetch("/pool/keys");
    if (r.ok && r.data.keys) setKeys(r.data.keys as KeyEntry[]);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    await Promise.all([loadStatus(), loadKeys()]);
    setLoading(false);
  }, [loadStatus, loadKeys]);

  useEffect(() => { void refresh(); }, [refresh]);

  // Auto-refresh every 30s
  useEffect(() => {
    const t = setInterval(() => void refresh(), 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  const doRefill = async () => {
    setLoading(true);
    const r = await apiFetch("/pool/refill", "POST");
    flash(r.ok ? `补充成功，新增: ${r.data.added ? "1个" : "0个"}` : `补充失败: ${r.data.error ?? "未知"}`, r.ok);
    await refresh();
    setLoading(false);
  };

  const doPurge = async () => {
    if (!confirm("确认清除所有已耗尽/失效的 Key？")) return;
    setLoading(true);
    const r = await apiFetch("/pool/purge", "POST");
    flash(r.ok ? `已清除 ${r.data.removed} 个无效 Key` : `清除失败`, r.ok);
    await refresh();
    setLoading(false);
  };

  const doAdd = async () => {
    const key = addKey.trim();
    if (!key) return;
    const r = await apiFetch("/pool/add", "POST", { key });
    setAddResult(r.ok && r.data.success ? `✓ 已添加 ${key.slice(0, 20)}...` : `✗ 添加失败: ${r.data.error ?? ""}`);
    if (r.ok && r.data.success) { setAddKey(""); await refresh(); }
  };

  const doTest = async () => {
    const key = testKey.trim();
    if (!key) return;
    const r = await apiFetch("/pool/test-key", "POST", { key });
    const d = r.data as { valid?: boolean; code?: number; error?: string };
    setTestResult(d.valid ? `✓ 有效 (Key 可用)` : `✗ 无效 (code=${d.code ?? "?"}, ${d.error ?? ""})`);
  };

  const doSolve = async () => {
    if (!solveText.trim()) return;
    const r = await apiFetch("/pool/solve-challenge", "POST", { text: solveText.trim() });
    if (r.ok && r.data.answer !== undefined) {
      setSolveResult(`答案: ${r.data.answer}  (格式: ${r.data.formatted})`);
    } else {
      setSolveResult(`解题失败: ${JSON.stringify(r.data)}`);
    }
  };

  const counts = status?.pool;
  const avail = counts?.available ?? 0;
  const total = counts?.total ?? 0;

  const statusTone = avail === 0 ? "err" : avail < 3 ? "warn" : "ok";

  return (
    <div className="space-y-4 text-sm">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h2 className="text-white font-bold text-base">Browser Use Key 池</h2>
          {lastRefresh && (
            <p className="text-gray-600 text-xs mt-0.5">
              最后刷新: {lastRefresh.toLocaleTimeString("zh-CN")}
            </p>
          )}
        </div>
        <div className="flex gap-2 flex-wrap">
          <Btn onClick={refresh} disabled={loading}>{loading ? "刷新中…" : "🔄 刷新"}</Btn>
          <Btn onClick={doRefill} disabled={loading} tone="green">⚡ 立即补充 Key</Btn>
          <Btn onClick={doPurge} disabled={loading} tone="red">🗑️ 清除无效</Btn>
        </div>
      </div>

      {/* Flash message */}
      {msg && (
        <div className={`px-3 py-2 rounded-lg text-sm font-medium ${msg.ok ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30" : "bg-red-500/15 text-red-400 border border-red-500/30"}`}>
          {msg.text}
        </div>
      )}

      {/* Stats */}
      {counts && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <Stat label="可用" value={avail} tone={statusTone} />
          <Stat label="总计" value={total} />
          <Stat label="已耗尽" value={counts.exhausted} tone={counts.exhausted > 0 ? "warn" : "dim"} />
          <Stat label="失败" value={counts.failed} tone={counts.failed > 0 ? "err" : "dim"} />
        </div>
      )}

      {/* Target hint */}
      {status && (
        <div className="text-xs text-gray-500">
          目标储备: <span className="text-gray-300">{status.target}</span> 个 &nbsp;·&nbsp;
          代理端口: <span className="text-gray-300">3096</span> &nbsp;·&nbsp;
          支持模型: <span className="text-gray-300">{status.models?.length ?? "?"}</span> 个
        </div>
      )}

      {/* Key list */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
        <div className="px-3 py-2 border-b border-[#21262d] flex items-center justify-between">
          <span className="text-gray-300 font-medium text-xs uppercase tracking-wide">Key 列表 ({keys.length})</span>
        </div>
        <div className="max-h-72 overflow-y-auto">
          {keys.length === 0 ? (
            <div className="px-4 py-6 text-center text-gray-600 text-xs">暂无 Key</div>
          ) : (
            <table className="w-full text-xs">
              <tbody>
                {keys.map((k, i) => (
                  <tr key={i} className="border-b border-[#21262d] last:border-0 hover:bg-[#21262d]/50">
                    <td className="px-3 py-1.5 font-mono text-gray-300">{k.key.slice(0, 26)}…</td>
                    <td className="px-3 py-1.5 text-right">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                        k.status === "available" ? "bg-emerald-500/15 text-emerald-400"
                        : k.status === "exhausted" ? "bg-amber-500/15 text-amber-400"
                        : "bg-red-500/15 text-red-400"
                      }`}>
                        {k.status === "available" ? "可用" : k.status === "exhausted" ? "已耗尽" : "失败"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Add Key */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-3 space-y-2">
        <div className="text-xs text-gray-400 font-medium">手动添加 Key</div>
        <div className="flex gap-2">
          <input
            value={addKey} onChange={e => { setAddKey(e.target.value); setAddResult(null); }}
            placeholder="bu_xxxxxxxxxxxxxx…"
            className="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-1.5 text-white text-xs font-mono placeholder-gray-700 outline-none focus:border-blue-500"
          />
          <Btn onClick={doAdd} tone="green" small>添加</Btn>
        </div>
        {addResult && <p className={`text-xs ${addResult.startsWith("✓") ? "text-emerald-400" : "text-red-400"}`}>{addResult}</p>}
      </div>

      {/* Test Key */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-3 space-y-2">
        <div className="text-xs text-gray-400 font-medium">验证 Key 有效性</div>
        <div className="flex gap-2">
          <input
            value={testKey} onChange={e => { setTestKey(e.target.value); setTestResult(null); }}
            placeholder="bu_xxxxxxxxxxxxxx…"
            className="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-1.5 text-white text-xs font-mono placeholder-gray-700 outline-none focus:border-blue-500"
          />
          <Btn onClick={doTest} small>验证</Btn>
        </div>
        {testResult && <p className={`text-xs ${testResult.startsWith("✓") ? "text-emerald-400" : "text-red-400"}`}>{testResult}</p>}
      </div>

      {/* Solve challenge (debug) */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-3 space-y-2">
        <div className="text-xs text-gray-400 font-medium">调试解题（粘贴 challenge_text）</div>
        <textarea
          value={solveText} onChange={e => { setSolveText(e.target.value); setSolveResult(null); }}
          placeholder="粘贴挑战文本，点击解题…"
          rows={3}
          className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-white text-xs font-mono placeholder-gray-700 outline-none focus:border-blue-500 resize-none"
        />
        <div className="flex items-center gap-2">
          <Btn onClick={doSolve} small>解题</Btn>
          {solveResult && <span className={`text-xs ${solveResult.startsWith("答案") ? "text-emerald-400" : "text-red-400"}`}>{solveResult}</span>}
        </div>
      </div>

      {/* Auto-refill toggle info */}
      <div className="text-xs text-gray-600">
        后台自动补充间隔: 45s &nbsp;·&nbsp; 目标储备: {status?.target ?? 12} 个 Key &nbsp;·&nbsp;
        解题支持: 英/西/韩/中/世/拉脱维亚语
      </div>
    </div>
  );
}
