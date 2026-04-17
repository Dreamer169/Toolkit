import { useState } from "react";

interface UploadResult {
  success: boolean;
  message: string;
  count?: number;
}

export default function Sub2ApiManager() {
  const [platform, setPlatform] = useState<"sub2api" | "cpa">("sub2api");
  const [apiUrl, setApiUrl]     = useState("/api/gateway");
  const [apiKey, setApiKey]     = useState("");
  const [tokens, setTokens]     = useState("");
  const [loading, setLoading]   = useState(false);
  const [result, setResult]     = useState<UploadResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [stats, setStats]       = useState<Record<string, unknown> | null>(null);
  const [gatewayStats, setGatewayStats] = useState<Record<string, unknown> | null>(null);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatResult, setChatResult] = useState<Record<string, unknown> | null>(null);
  const [friendNodes, setFriendNodes] = useState("");
  const [friendLoading, setFriendLoading] = useState(false);
  const [friendResult, setFriendResult] = useState<Record<string, unknown> | null>(null);

  const tokenLines = tokens.split("\n").filter((t) => t.trim());

  const bridgeRequest = async (
    endpoint: string,
    options: { method?: string; headers?: Record<string, string>; body?: unknown } = {},
  ): Promise<{ success: boolean; status?: number; data?: Record<string, unknown>; error?: string }> => {
    const method = options.method ?? "GET";
    if (endpoint.startsWith("/")) {
      const r = await fetch(endpoint, {
        method,
        headers: { "Content-Type": "application/json", ...(options.headers ?? {}) },
        body: method !== "GET" && options.body !== undefined
          ? (typeof options.body === "string" ? options.body : JSON.stringify(options.body))
          : undefined,
      });
      let data: Record<string, unknown> = {};
      try { data = await r.json() as Record<string, unknown>; } catch { data = { raw: await r.text() }; }
      return { success: r.ok, status: r.status, data };
    }

    const r = await fetch(`/api/tools/proxy-request`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: endpoint,
        method,
        headers: options.headers,
        body: typeof options.body === "string" ? options.body : options.body === undefined ? undefined : JSON.stringify(options.body),
      }),
    });
    return await r.json() as { success: boolean; status?: number; data?: Record<string, unknown>; error?: string };
  };

  const checkStats = async () => {
    if (!apiUrl || !apiKey) return;
    setChecking(true);
    setStats(null);
    try {
      const endpoint = platform === "sub2api"
        ? `${apiUrl.replace(/\/$/, "")}/api/v1/admin/dashboard/stats`
        : `${apiUrl.replace(/\/$/, "")}/api/stats`;
      const d = await bridgeRequest(endpoint, {
        headers: { Authorization: `Bearer ${apiKey}`, "x-api-key": apiKey },
      });
      if (d.success) setStats(d.data);
      else setStats({ error: d.error ?? "无法获取统计信息，请检查 API URL 和 Key", status: d.status });
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
        ? `${apiUrl.replace(/\/$/, "")}/api/v1/admin/accounts/batch`
        : `${apiUrl.replace(/\/$/, "")}/api/accounts/add`;

      const requestBody = platform === "sub2api"
        ? { tokens: tks }
        : { accounts: tks };

      const d = await bridgeRequest(endpoint, {
        method: "POST",
        headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
        body: requestBody,
      });
      if (d.success) {
        setResult({ success: true, message: "上传成功", count: tks.length });
      } else {
        setResult({ success: false, message: d.data?.error as string ?? d.error ?? "上传失败" });
      }
    } catch (e) {
      setResult({ success: false, message: String(e) });
    }
    setLoading(false);
  };

  const loadGatewayStats = async () => {
    setChecking(true);
    setGatewayStats(null);
    try {
      const r = await fetch("/api/gateway/v1/stats");
      setGatewayStats(await r.json() as Record<string, unknown>);
    } catch (e) {
      setGatewayStats({ success: false, error: String(e) });
    }
    setChecking(false);
  };

  const testGatewayChat = async () => {
    setChatLoading(true);
    setChatResult(null);
    try {
      const started = Date.now();
      const r = await fetch("/api/gateway/v1/chat/completions", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}) },
        body: JSON.stringify({
          model: "gpt-5-mini",
          messages: [
            { role: "system", content: "You are a gateway health checker. Reply with a short confirmation." },
            { role: "user", content: "Return gateway-ok and the current node is working." },
          ],
          stream: false,
          max_completion_tokens: 64,
        }),
      });
      const gatewayNode = r.headers.get("x-gateway-node");
      const data = await r.json() as Record<string, unknown>;
      setChatResult({ success: r.ok, status: r.status, gatewayNode, latencyMs: Date.now() - started, data });
      await loadGatewayStats();
    } catch (e) {
      setChatResult({ success: false, error: String(e) });
    }
    setChatLoading(false);
  };

  const registerFriendNodes = async () => {
    const nodes = friendNodes
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line, index) => {
        const [baseUrl, apiKey, model, name] = line.split("|").map((item) => item?.trim());
        return {
          baseUrl,
          apiKey: apiKey || undefined,
          model: model || "gpt-5-mini",
          name: name || `Friend 节点 ${index + 1}`,
        };
      });
    if (!nodes.length) return;
    setFriendLoading(true);
    setFriendResult(null);
    try {
      const r = await fetch("/api/gateway/nodes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nodes }),
      });
      const data = await r.json() as Record<string, unknown>;
      setFriendResult({ success: r.ok, status: r.status, data });
      await loadGatewayStats();
    } catch (e) {
      setFriendResult({ success: false, error: String(e) });
    }
    setFriendLoading(false);
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
          <button
            onClick={() => { setPlatform("sub2api"); setApiUrl("/api/gateway"); setResult(null); setStats(null); }}
            className="mt-3 w-full py-2 bg-emerald-600/15 hover:bg-emerald-600/25 border border-emerald-500/30 rounded-lg text-sm text-emerald-300 transition-all"
          >
            使用多节点网关（远端 Sub2API + Reseek AI 兜底）
          </button>
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

      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-4">
        <div>
          <h3 className="text-sm font-semibold text-gray-300">多节点网关检测</h3>
          <p className="text-xs text-gray-500 mt-1">
            /api/gateway/v1/chat/completions 会优先调用 45.205.27.69，远端无账号或 503 时自动切到 Reseek OpenAI / Anthropic / Gemini 以及 friend 节点轮询。
          </p>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={loadGatewayStats}
            disabled={checking}
            className="py-2 bg-[#21262d] hover:bg-[#30363d] disabled:opacity-50 border border-[#30363d] rounded-lg text-sm text-gray-300 transition-all"
          >
            {checking ? "检测中..." : "查看节点状态"}
          </button>
          <button
            onClick={testGatewayChat}
            disabled={chatLoading}
            className="py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 rounded-lg text-sm text-white transition-all"
          >
            {chatLoading ? "请求中..." : "真实聊天测试"}
          </button>
        </div>

        {gatewayStats && (
          <div className="bg-[#0d1117] rounded-lg p-4 border border-[#30363d]">
            <p className="text-xs text-gray-500 mb-2">网关节点池</p>
            <pre className="text-xs text-gray-300 overflow-auto max-h-56">
              {JSON.stringify(gatewayStats, null, 2)}
            </pre>
          </div>
        )}

        {chatResult && (
          <div className={`rounded-lg p-4 border ${
            chatResult.success
              ? "bg-emerald-500/10 border-emerald-500/30"
              : "bg-red-500/10 border-red-500/30"
          }`}>
            <p className={`text-sm font-medium mb-2 ${chatResult.success ? "text-emerald-400" : "text-red-400"}`}>
              {chatResult.success ? "聊天请求成功" : "聊天请求失败"}
            </p>
            <pre className="text-xs text-gray-300 overflow-auto max-h-48">
              {JSON.stringify(chatResult, null, 2)}
            </pre>
          </div>
        )}

        <div className="bg-[#0d1117] border border-[#30363d] rounded-lg p-4 space-y-3">
          <div>
            <h4 className="text-sm font-medium text-gray-300">添加 Friend 子节点</h4>
            <p className="text-xs text-gray-500 mt-1">
              每行一个 OpenAI-compatible 节点：baseUrl|apiKey|model|name。apiKey 可留空，运行时加入当前轮询池。
            </p>
          </div>
          <textarea
            value={friendNodes}
            onChange={(e) => { setFriendNodes(e.target.value); setFriendResult(null); }}
            placeholder={"https://friend-node.example.com|sk-xxx|gpt-4o|好友节点 1\nhttps://another-node.example.com|sk-xxx|gpt-5-mini|备用节点 2"}
            rows={4}
            className="w-full bg-[#010409] border border-[#30363d] rounded-lg px-3 py-2 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 font-mono resize-none"
          />
          <button
            onClick={registerFriendNodes}
            disabled={friendLoading || !friendNodes.trim()}
            className="w-full py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg text-sm text-white transition-all"
          >
            {friendLoading ? "添加中..." : "添加到轮询池"}
          </button>
          {friendResult && (
            <pre className="text-xs text-gray-300 overflow-auto max-h-40">
              {JSON.stringify(friendResult, null, 2)}
            </pre>
          )}
        </div>
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
            <span>默认通过 /api/gateway 调用 45.205.27.69:9090，上传操作走服务器中转，API Key 仅用于本次请求，不会被存储。</span>
          </div>
        </div>
      </div>
    </div>
  );
}
