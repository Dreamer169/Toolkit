import { useState, useRef, useEffect } from "react";

const API = import.meta.env.BASE_URL.replace(/\/$/, "") + "/api";

interface LoginResult {
  ok: boolean;
  email: string;
  ssid?: string;
  reason?: string;
  cookies?: unknown[];
}

interface LogEntry { type: string; message: string; }

export default function UnitoolLogin() {
  const [mode, setMode] = useState<"single" | "batch">("single");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [batchText, setBatchText] = useState("");
  const [headless, setHeadless] = useState(true);
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [results, setResults] = useState<LoginResult[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const sinceRef = useRef(0);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const addLog = (msg: string) => setLogs(prev => [...prev, msg]);

  const stopPoll = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  const poll = (jid: string) => {
    sinceRef.current = 0;
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API}/tools/unitool/login/${jid}?since=${sinceRef.current}`);
        const d = await r.json();
        if (d.logs?.length) {
          sinceRef.current = d.nextSince;
          for (const l of d.logs as LogEntry[]) addLog(l.message);
        }
        if (d.results?.length) setResults(d.results);
        if (d.status === "done" || d.status === "failed") {
          stopPoll();
          setRunning(false);
        }
      } catch { /* ignore */ }
    }, 1000);
  };

  const run = async () => {
    setRunning(true);
    setLogs([]);
    setResults([]);
    stopPoll();

    let accounts: [string, string][] | undefined;
    if (mode === "batch") {
      accounts = batchText.trim().split("\n")
        .map(l => l.trim()).filter(Boolean)
        .map(l => {
          const [e, p] = l.split("|");
          return [e?.trim() ?? "", p?.trim() ?? ""] as [string, string];
        })
        .filter(([e]) => e.includes("@"));
      if (!accounts.length) { addLog("❌ 批量账号格式错误（每行 email|password）"); setRunning(false); return; }
    } else {
      if (!email || !password) { addLog("❌ 请填写邮箱和密码"); setRunning(false); return; }
    }

    addLog(`[${new Date().toLocaleTimeString()}] 启动 unitool.ai 登录...`);

    try {
      const body: Record<string, unknown> = { headless };
      if (accounts) body.accounts = accounts; else { body.email = email; body.password = password; }
      const r = await fetch(`${API}/tools/unitool/login`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!d.success) { addLog(`❌ 启动失败: ${d.error}`); setRunning(false); return; }
      setJobId(d.jobId);
      addLog(`任务ID: ${d.jobId}`);
      poll(d.jobId);
    } catch (e) { addLog(`❌ 网络错误: ${e}`); setRunning(false); }
  };

  const stop = async () => {
    stopPoll();
    if (jobId) await fetch(`${API}/tools/unitool/login/${jobId}`, { method: "DELETE" }).catch(() => {});
    setRunning(false);
    addLog("⏹ 已停止");
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">unitool.ai 自动登录</h2>
        <p className="text-gray-400 text-sm">Turnstile shadow-DOM bypass，支持单账号/批量登录，提取 ssid cookie</p>
      </div>

      {/* Mode */}
      <div className="flex gap-3">
        {(["single","batch"] as const).map(m => (
          <button key={m} onClick={() => setMode(m)}
            className={`px-4 py-1.5 rounded text-sm font-medium transition-colors ${mode===m ? "bg-blue-600 text-white" : "bg-[#21262d] text-gray-300 hover:bg-[#30363d]"}`}>
            {m === "single" ? "单账号" : "批量"}
          </button>
        ))}
      </div>

      {/* Inputs */}
      <div className="bg-[#161b22] rounded-lg p-4 space-y-3 border border-[#30363d]">
        {mode === "single" ? (
          <>
            <div>
              <label className="block text-xs text-gray-400 mb-1">邮箱</label>
              <input value={email} onChange={e => setEmail(e.target.value)}
                className="w-full bg-[#0d1117] border border-[#30363d] rounded px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
                placeholder="user@outlook.com" disabled={running} />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">密码</label>
              <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                className="w-full bg-[#0d1117] border border-[#30363d] rounded px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
                placeholder="••••••••" disabled={running} />
            </div>
          </>
        ) : (
          <div>
            <label className="block text-xs text-gray-400 mb-1">批量账号（每行 email|password）</label>
            <textarea value={batchText} onChange={e => setBatchText(e.target.value)}
              rows={6}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded px-3 py-2 text-sm text-white outline-none focus:border-blue-500 font-mono"
              placeholder={"user1@outlook.com|password1\nuser2@outlook.com|password2"}
              disabled={running} />
          </div>
        )}
        <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
          <input type="checkbox" checked={headless} onChange={e => setHeadless(e.target.checked)} className="w-4 h-4" disabled={running} />
          Headless 模式
        </label>
      </div>

      {/* Buttons */}
      <div className="flex gap-3">
        <button onClick={run} disabled={running}
          className="px-6 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded text-sm font-medium transition-colors">
          {running ? "登录中..." : "开始登录"}
        </button>
        {running && (
          <button onClick={stop} className="px-4 py-2 bg-red-700 hover:bg-red-800 text-white rounded text-sm transition-colors">停止</button>
        )}
      </div>

      {/* Results */}
      {results.length > 0 && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4">
          <h3 className="text-sm font-semibold text-white mb-3">登录结果 ({results.filter(r=>r.ok).length}/{results.length} 成功)</h3>
          <div className="space-y-2 max-h-48 overflow-y-auto">
            {results.map((r, i) => (
              <div key={i} className={`rounded p-2 text-xs font-mono ${r.ok ? "bg-green-900/30 border border-green-700/40" : "bg-red-900/30 border border-red-700/40"}`}>
                <div className="flex items-center gap-2">
                  <span>{r.ok ? "✅" : "❌"}</span>
                  <span className="text-gray-300">{r.email}</span>
                  {r.ok && r.ssid && <span className="text-green-400 ml-auto">ssid={r.ssid.slice(0,20)}...</span>}
                  {!r.ok && <span className="text-red-400 ml-auto">{r.reason}</span>}
                </div>
                {r.ok && r.ssid && (
                  <div className="mt-1 text-gray-500 break-all">
                    <button onClick={() => navigator.clipboard.writeText(r.ssid!)}
                      className="text-blue-400 hover:text-blue-300 mr-2">[复制 ssid]</button>
                    {r.ssid}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Logs */}
      {logs.length > 0 && (
        <div className="bg-[#0d1117] border border-[#30363d] rounded-lg p-4 max-h-80 overflow-y-auto font-mono text-xs text-gray-300 space-y-0.5">
          {logs.map((l, i) => <div key={i}>{l}</div>)}
          <div ref={logsEndRef} />
        </div>
      )}
    </div>
  );
}
