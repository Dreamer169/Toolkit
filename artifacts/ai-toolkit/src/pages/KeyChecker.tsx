import { useState } from "react";

type Platform = "openai" | "claude" | "gemini" | "openai-token";

interface Result {
  valid: boolean;
  info?: Record<string, unknown>;
  error?: string;
}

const platforms: { id: Platform; label: string; placeholder: string; desc: string }[] = [
  {
    id: "openai",
    label: "OpenAI API Key",
    placeholder: "sk-...",
    desc: "验证 OpenAI API Key 是否有效，并返回可用模型数量",
  },
  {
    id: "openai-token",
    label: "OpenAI Access Token",
    placeholder: "eyJhbGciO... 或 Bearer Token",
    desc: "验证 OpenAI 账号 Access Token，返回账号邮箱信息",
  },
  {
    id: "claude",
    label: "Claude API Key",
    placeholder: "sk-ant-...",
    desc: "验证 Anthropic Claude API Key 是否有效",
  },
  {
    id: "gemini",
    label: "Gemini API Key",
    placeholder: "AIza...",
    desc: "验证 Google Gemini API Key 是否有效",
  },
];

export default function KeyChecker() {
  const [platform, setPlatform] = useState<Platform>("openai");
  const [key, setKey] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [loading, setLoading] = useState(false);

  const check = async () => {
    if (!key.trim()) return;
    setLoading(true);
    setResult(null);
    try {
      const r = await fetch("/api/tools/key-check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform, key: key.trim() }),
      });
      const d = await r.json();
      setResult(d);
    } catch (e) {
      setResult({ valid: false, error: String(e) });
    }
    setLoading(false);
  };

  const cur = platforms.find((p) => p.id === platform)!;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">API Key 验证</h2>
        <p className="text-sm text-gray-400">
          检测 OpenAI、Claude、Gemini API Key 或 Access Token 是否有效
        </p>
      </div>

      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-6 space-y-5">
        <div>
          <label className="block text-sm text-gray-400 mb-2">选择平台</label>
          <div className="grid grid-cols-2 gap-2">
            {platforms.map((p) => (
              <button
                key={p.id}
                onClick={() => {
                  setPlatform(p.id);
                  setResult(null);
                }}
                className={`py-2 px-3 rounded-lg text-sm font-medium border transition-all text-left ${
                  platform === p.id
                    ? "bg-blue-600/20 border-blue-500/40 text-blue-400"
                    : "bg-[#0d1117] border-[#30363d] text-gray-400 hover:text-gray-200 hover:border-[#4a5568]"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-gray-600 mt-2">{cur.desc}</p>
        </div>

        <div>
          <label className="block text-sm text-gray-400 mb-2">{cur.label}</label>
          <textarea
            value={key}
            onChange={(e) => {
              setKey(e.target.value);
              setResult(null);
            }}
            placeholder={cur.placeholder}
            rows={3}
            className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 font-mono resize-none"
          />
        </div>

        <button
          onClick={check}
          disabled={loading || !key.trim()}
          className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-white font-medium text-sm transition-all"
        >
          {loading ? "验证中..." : "验证 Key"}
        </button>

        {result && (
          <div
            className={`rounded-xl p-5 border ${
              result.valid
                ? "bg-emerald-500/10 border-emerald-500/30"
                : "bg-red-500/10 border-red-500/30"
            }`}
          >
            <div className="flex items-center gap-2 mb-3">
              <span className="text-lg">{result.valid ? "✅" : "❌"}</span>
              <span
                className={`font-semibold ${result.valid ? "text-emerald-400" : "text-red-400"}`}
              >
                {result.valid ? "Key 有效" : "Key 无效"}
              </span>
            </div>
            {result.valid && result.info && (
              <div className="space-y-1">
                {Object.entries(result.info).map(([k, v]) => (
                  <div key={k} className="flex items-center gap-2 text-sm">
                    <span className="text-gray-500 w-24 shrink-0">{k}:</span>
                    <span className="text-gray-200 font-mono">{String(v)}</span>
                  </div>
                ))}
              </div>
            )}
            {!result.valid && result.error && (
              <p className="text-sm text-red-300">{result.error}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
