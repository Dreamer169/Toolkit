import { useState } from "react";

interface UploadResult {
  success: boolean;
  message: string;
  count?: number;
}

export default function Sub2ApiManager() {
  const [platform, setPlatform] = useState<"sub2api" | "cpa">("sub2api");
  const [apiUrl, setApiUrl]     = useState("https://api.sub2api.com");
  const [apiKey, setApiKey]     = useState("");
  const [tokens, setTokens]     = useState("");
  const [loading, setLoading]   = useState(false);
  const [result, setResult]     = useState<UploadResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [stats, setStats]       = useState<Record<string, unknown> | null>(null);

  const tokenLines = tokens.split("\n").filter((t) => t.trim());

  const checkStats = async () => {
    if (!apiUrl || !apiKey) return;
    setChecking(true);
    setStats(null);
    try {
      const endpoint = platform === "sub2api"
        ? `${apiUrl.replace(/\/$/, "")}/api/v1/dashboard`
        : `${apiUrl.replace(/\/$/, "")}/api/stats`;
      const r = await fetch(`/api/tools/proxy-request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: endpoint, headers: { Authorization: `Bearer ${apiKey}`, "x-api-key": apiKey } }),
      });
      const d = await r.json() as { success: boolean; data: Record<string, unknown> };
      if (d.success) setStats(d.data);
      else setStats({ error: "无法获取统计信息，请检查 API URL 和 Key" });
    } catch (e) {
      setStats({ error: String(e) });
    }
    setChecking(false);
  };

  const uploadTokens = async () => {
    if (!apiUrl || !apiKey || tokenLines.length === 0) return;
    setLoading(true);
    setResult(null);
    try {
      const tks = tokenLines.map((t) => t.trim()).filter(Boolean);
      const endpoint = platform === "sub2api"
        ? `${apiUrl.replace(/\/$/, "")}/api/v1/tokens/batch`
        : `${apiUrl.replace(/\/$/, "")}/api/accounts/add`;

      const body = platform === "sub2api"
        ? { tokens: tks }
        : { accounts: tks };

      const r = await fetch(`/api/tools/proxy-request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: endpoint,
          method: "POST",
          headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
      });
      const d = await r.json() as { success: boolean; data: Record<string, unknown> };
      if (d.success) {
        setResult({ success: true, message: "上传成功", count: tks.length });
      } else {
        setResult({ success: false, message: d.data?.error as string ?? "上传失败" });
      }
    } catch (e) {
      setResult({ success: false, message: String(e) });
    }
    setLoading(false);
  };

  const SUB2API_FORMATS = [
    { label: "Sub2Api 格式", placeholder: "https://api.sub2api.com", help: "Bearer Token 认证，支持批量上传 Codex Token" },
    { label: "CPA 格式",     placeholder: "https://api.cpa.io",        help: "x-api-key 认证，用于上传 OpenAI Access Token" },
  ];

  const cur = SUB2API_FORMATS[platform === "sub2api" ? 0 : 1];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">Token 转发平台管理</h2>
        <p className="text-sm text-gray-400">
          将注册的账号 Token 上传到 Sub2Api / CPA 等聚合平台，支持批量操作
        </p>
      </div>

      {/* 平台选择 */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-4">
        <div>
          <label className="block text-sm text-gray-400 mb-2">选择平台</label>
          <div className="grid grid-cols-2 gap-2">
            {(["sub2api", "cpa"] as const).map((p) => (
              <button
                key={p}
                onClick={() => { setPlatform(p); setResult(null); setStats(null); }}
                className={`py-2.5 px-4 rounded-lg text-sm font-medium border transition-all ${
                  platform === p
                    ? "bg-blue-600/20 border-blue-500/40 text-blue-400"
                    : "bg-[#0d1117] border-[#30363d] text-gray-400 hover:border-[#4a5568] hover:text-gray-200"
                }`}
              >
                {p === "sub2api" ? "🚀 Sub2Api" : "🏢 CPA"}
              </button>
            ))}
          </div>
          <p className="text-xs text-gray-600 mt-2">{cur.help}</p>
        </div>

        {/* API 配置 */}
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-gray-400 mb-1.5">API 基础 URL</label>
            <input
              value={apiUrl}
              onChange={(e) => setApiUrl(e.target.value)}
              placeholder={cur.placeholder}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1.5">API Key / Token</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="你的 API Key..."
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>

        <button
          onClick={checkStats}
          disabled={checking || !apiUrl || !apiKey}
          className="w-full py-2 bg-[#21262d] hover:bg-[#30363d] disabled:opacity-50 border border-[#30363d] rounded-lg text-sm text-gray-300 transition-all"
        >
          {checking ? "查询中..." : "📊 查询平台状态"}
        </button>

        {/* 统计信息 */}
        {stats && (
          <div className="bg-[#0d1117] rounded-lg p-4 border border-[#30363d]">
            <p className="text-xs text-gray-500 mb-2">平台返回数据</p>
            <pre className="text-xs text-gray-300 overflow-auto max-h-32">
              {JSON.stringify(stats, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* 批量上传 */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-300">批量上传 Token</h3>

        <div>
          <label className="block text-xs text-gray-400 mb-1.5">
            Token 列表 <span className="text-gray-600">（每行一个）</span>
          </label>
          <textarea
            value={tokens}
            onChange={(e) => { setTokens(e.target.value); setResult(null); }}
            placeholder={"每行粘贴一个 Access Token 或 Refresh Token\n例：\neyJhbGciO...\nsk-ant-api..."}
            rows={8}
            className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 font-mono resize-none"
          />
          <p className="text-xs text-gray-600 mt-1">已输入 {tokenLines.length} 个</p>
        </div>

        <button
          onClick={uploadTokens}
          disabled={loading || tokenLines.length === 0 || !apiUrl || !apiKey}
          className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-white font-medium text-sm transition-all"
        >
          {loading ? "上传中..." : `⬆ 上传 ${tokenLines.length} 个 Token`}
        </button>

        {result && (
          <div className={`rounded-lg p-4 border ${
            result.success
              ? "bg-emerald-500/10 border-emerald-500/30"
              : "bg-red-500/10 border-red-500/30"
          }`}>
            <div className="flex items-center gap-2">
              <span>{result.success ? "✅" : "❌"}</span>
              <span className={`text-sm font-medium ${result.success ? "text-emerald-400" : "text-red-400"}`}>
                {result.message}
              </span>
              {result.count && (
                <span className="text-xs text-gray-500">（共 {result.count} 个）</span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 使用说明 */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
        <h3 className="text-sm font-semibold text-gray-300 mb-3">使用说明</h3>
        <div className="space-y-2 text-xs text-gray-400">
          <div className="flex gap-2">
            <span className="text-blue-400 shrink-0">Sub2Api</span>
            <span>主流 Codex / OpenAI Token 聚合平台。注册后获取 API Key，将此工具注册的 Token 批量上传，供其他工具消费。</span>
          </div>
          <div className="flex gap-2">
            <span className="text-purple-400 shrink-0">CPA</span>
            <span>CLIProxyAPI 平台，专注于 ChatGPT OAuth Token 聚合。支持 openai-pool 自动上传。</span>
          </div>
          <div className="flex gap-2">
            <span className="text-yellow-400 shrink-0">注意</span>
            <span>上传操作通过服务器中转请求，避免 CORS 问题。API Key 仅用于本次请求，不会被存储。</span>
          </div>
        </div>
      </div>
    </div>
  );
}
