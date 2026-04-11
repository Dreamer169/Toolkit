import { useState } from "react";

type Platform = "openai" | "claude";

interface CheckResult {
  token?: string;
  valid: boolean;
  error?: string;
}

interface Summary {
  total: number;
  valid: number;
  invalid: number;
}

export default function TokenBatch() {
  const [platform, setPlatform] = useState<Platform>("openai");
  const [text, setText] = useState("");
  const [results, setResults] = useState<CheckResult[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(false);

  const check = async () => {
    const tokens = text
      .split("\n")
      .map((t) => t.trim())
      .filter(Boolean);
    if (tokens.length === 0) return;
    setLoading(true);
    setResults([]);
    setSummary(null);
    try {
      const r = await fetch("/api/tools/token-batch-check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tokens, platform }),
      });
      const d = await r.json();
      if (d.success) {
        setResults(d.results);
        setSummary(d.summary);
      }
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  const tokenCount = text.split("\n").filter((t) => t.trim()).length;

  const exportValid = () => {
    const valid = results.filter((r) => r.valid).map((r) => r.token ?? "");
    const blob = new Blob([valid.join("\n")], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "valid_tokens.txt";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">Token 批量检测</h2>
        <p className="text-sm text-gray-400">
          批量验证 API Key 或 Token 是否有效，每次最多 20 个，结果可导出
        </p>
      </div>

      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-6 space-y-5">
        <div>
          <label className="block text-sm text-gray-400 mb-2">平台</label>
          <div className="flex gap-2">
            {(["openai", "claude"] as Platform[]).map((p) => (
              <button
                key={p}
                onClick={() => setPlatform(p)}
                className={`px-4 py-2 rounded-lg text-sm border transition-all ${
                  platform === p
                    ? "bg-blue-600/20 border-blue-500/40 text-blue-400"
                    : "bg-[#0d1117] border-[#30363d] text-gray-400 hover:text-gray-200"
                }`}
              >
                {p === "openai" ? "OpenAI" : "Claude"}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm text-gray-400 mb-2">
            Token 列表（每行一个，最多 20 个）
          </label>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={"sk-proj-abc123...\nsk-proj-xyz456...\n..."}
            rows={8}
            className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 font-mono resize-y"
          />
          <p className="text-xs text-gray-600 mt-1">已输入 {tokenCount} 个 Token</p>
        </div>

        <button
          onClick={check}
          disabled={loading || tokenCount === 0}
          className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-white font-medium text-sm transition-all"
        >
          {loading ? `检测中 (${tokenCount} 个)...` : `开始检测 (${tokenCount} 个)`}
        </button>

        {summary && (
          <div className="grid grid-cols-3 gap-3">
            <div className="bg-[#0d1117] rounded-xl p-4 text-center border border-[#21262d]">
              <div className="text-2xl font-bold text-white">{summary.total}</div>
              <div className="text-xs text-gray-500 mt-1">总计</div>
            </div>
            <div className="bg-emerald-500/10 rounded-xl p-4 text-center border border-emerald-500/20">
              <div className="text-2xl font-bold text-emerald-400">{summary.valid}</div>
              <div className="text-xs text-gray-500 mt-1">有效</div>
            </div>
            <div className="bg-red-500/10 rounded-xl p-4 text-center border border-red-500/20">
              <div className="text-2xl font-bold text-red-400">{summary.invalid}</div>
              <div className="text-xs text-gray-500 mt-1">无效</div>
            </div>
          </div>
        )}

        {results.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-gray-300">检测结果</h3>
              {summary && summary.valid > 0 && (
                <button
                  onClick={exportValid}
                  className="text-xs px-3 py-1.5 bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 rounded-lg hover:bg-emerald-500/25 transition-all"
                >
                  导出有效 Token ({summary.valid})
                </button>
              )}
            </div>
            <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
              {results.map((r, i) => (
                <div
                  key={i}
                  className={`flex items-center justify-between gap-3 px-4 py-2.5 rounded-lg border text-sm ${
                    r.valid
                      ? "bg-emerald-500/10 border-emerald-500/20"
                      : "bg-red-500/10 border-red-500/20"
                  }`}
                >
                  <span className="font-mono text-gray-300 truncate">{r.token}</span>
                  <span className={`shrink-0 text-xs font-medium ${r.valid ? "text-emerald-400" : "text-red-400"}`}>
                    {r.valid ? "✓ 有效" : `✗ ${r.error ?? "无效"}`}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
