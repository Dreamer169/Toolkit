import { useState } from "react";

type Platform = "openai" | "claude" | "gemini" | "grok" | "deepseek" | "cursor";

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

const platforms: { id: Platform; label: string; icon: string; color: string }[] = [
  { id: "openai",   label: "OpenAI",   icon: "🤖", color: "blue"    },
  { id: "claude",   label: "Claude",   icon: "✨", color: "orange"  },
  { id: "gemini",   label: "Gemini",   icon: "💎", color: "cyan"    },
  { id: "grok",     label: "Grok",     icon: "⚡", color: "yellow"  },
  { id: "deepseek", label: "DeepSeek", icon: "🔭", color: "indigo"  },
  { id: "cursor",   label: "Cursor",   icon: "🖱️", color: "purple"  },
];

const colorMap: Record<string, string> = {
  blue:   "bg-blue-600/20 border-blue-500/40 text-blue-400",
  orange: "bg-orange-600/20 border-orange-500/40 text-orange-400",
  cyan:   "bg-cyan-600/20 border-cyan-500/40 text-cyan-400",
  yellow: "bg-yellow-600/20 border-yellow-500/40 text-yellow-400",
  indigo: "bg-indigo-600/20 border-indigo-500/40 text-indigo-400",
  purple: "bg-purple-600/20 border-purple-500/40 text-purple-400",
};

export default function TokenBatch() {
  const [platform, setPlatform] = useState<Platform>("openai");
  const [text, setText]         = useState("");
  const [results, setResults]   = useState<CheckResult[]>([]);
  const [summary, setSummary]   = useState<Summary | null>(null);
  const [loading, setLoading]   = useState(false);
  const [copied, setCopied]     = useState(false);

  const tokenCount = text.split("\n").filter((t) => t.trim()).length;

  const check = async () => {
    const tokens = text.split("\n").map((t) => t.trim()).filter(Boolean);
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
      const d = await r.json() as { success: boolean; results: CheckResult[]; summary: Summary };
      if (d.success) {
        setResults(d.results);
        setSummary(d.summary);
      }
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  const validResults = results.filter((r) => r.valid);

  const copyValid = () => {
    const text = validResults.map((r) => r.token?.replace(/\.\.\.$/, "") ?? "").join("\n");
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const exportValid = () => {
    const content = validResults.map((r) => r.token?.replace(/\.\.\.$/, "") ?? "").join("\n");
    const blob = new Blob([content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${platform}_valid_keys_${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const cur = platforms.find((p) => p.id === platform)!;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">批量 Key / Token 检测</h2>
        <p className="text-sm text-gray-400">
          支持 OpenAI、Claude、Gemini、Grok、DeepSeek、Cursor 最多 50 个一次性批量检测
        </p>
      </div>

      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-6 space-y-5">
        {/* 平台选择 */}
        <div>
          <label className="block text-sm text-gray-400 mb-2">选择平台</label>
          <div className="grid grid-cols-3 gap-2">
            {platforms.map((p) => (
              <button
                key={p.id}
                onClick={() => { setPlatform(p.id); setResults([]); setSummary(null); }}
                className={`py-2 px-3 rounded-lg text-sm font-medium border transition-all flex items-center gap-1.5 ${
                  platform === p.id
                    ? colorMap[p.color]
                    : "bg-[#0d1117] border-[#30363d] text-gray-400 hover:text-gray-200 hover:border-[#4a5568]"
                }`}
              >
                <span>{p.icon}</span>
                <span>{p.label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* 输入 */}
        <div>
          <label className="block text-sm text-gray-400 mb-2">
            粘贴 Key 列表 <span className="text-gray-600">（每行一个，最多 50 个）</span>
          </label>
          <textarea
            value={text}
            onChange={(e) => { setText(e.target.value); setResults([]); setSummary(null); }}
            placeholder={`每行粘贴一个 ${cur.label} Key / Token\n例：\nsk-xxxxxxxxxxxxx\nsk-yyyyyyyyyyyyyy`}
            rows={8}
            className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 font-mono resize-none"
          />
          <p className="text-xs text-gray-600 mt-1">已输入 {tokenCount} 个</p>
        </div>

        <button
          onClick={check}
          disabled={loading || tokenCount === 0}
          className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-white font-medium text-sm transition-all"
        >
          {loading ? "检测中..." : `批量检测 ${tokenCount} 个`}
        </button>

        {/* 汇总 */}
        {summary && (
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: "总计",   value: summary.total,   color: "text-gray-300",  bg: "bg-[#0d1117]" },
              { label: "有效",   value: summary.valid,   color: "text-emerald-400", bg: "bg-emerald-500/5" },
              { label: "无效",   value: summary.invalid, color: "text-red-400",   bg: "bg-red-500/5" },
            ].map((s) => (
              <div key={s.label} className={`${s.bg} rounded-lg p-3 text-center border border-[#30363d]`}>
                <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
                <div className="text-xs text-gray-500 mt-0.5">{s.label}</div>
              </div>
            ))}
          </div>
        )}

        {/* 导出有效 */}
        {validResults.length > 0 && (
          <div className="flex gap-2">
            <button
              onClick={copyValid}
              className="flex-1 py-2 text-sm rounded-lg bg-emerald-600/10 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-600/20 transition-all"
            >
              {copied ? "✅ 已复制" : `复制 ${validResults.length} 个有效 Key`}
            </button>
            <button
              onClick={exportValid}
              className="px-4 py-2 text-sm rounded-lg bg-[#21262d] border border-[#30363d] text-gray-400 hover:text-gray-200 transition-all"
            >
              导出 .txt
            </button>
          </div>
        )}
      </div>

      {/* 结果列表 */}
      {results.length > 0 && (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
          <div className="px-4 py-2.5 border-b border-[#21262d] flex items-center justify-between">
            <span className="text-xs text-gray-500">检测结果</span>
            <span className="text-xs text-gray-600">{results.length} 条</span>
          </div>
          <div className="divide-y divide-[#21262d] max-h-96 overflow-y-auto">
            {results.map((r, i) => (
              <div key={i} className="flex items-center gap-3 px-4 py-2.5">
                <span className={`text-sm ${r.valid ? "text-emerald-400" : "text-red-400"}`}>
                  {r.valid ? "✅" : "❌"}
                </span>
                <span className="font-mono text-sm text-gray-300 flex-1 truncate">
                  {r.token ?? ""}
                </span>
                {!r.valid && r.error && (
                  <span className="text-xs text-red-400/70 truncate max-w-[200px]">{r.error}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
