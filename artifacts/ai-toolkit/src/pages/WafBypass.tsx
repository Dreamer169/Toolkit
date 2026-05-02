import { useState, useRef, useEffect } from "react";

const API = import.meta.env.BASE_URL.replace(/\/$/, "") + "/api";

interface BypassResult {
  success: boolean;
  title?: string;
  url?: string;
  html_length?: number;
  elapsed?: number;
  html?: string;
  data?: Record<string, string | null>;
  error?: string;
  trace?: string;
}

const MODES = [
  { id: "bypass", label: "绕过访问", desc: "Cloudflare WAF 绕过，返回页面标题/URL/HTML" },
  { id: "scrape", label: "数据提取", desc: "隐蔽爬取 + CSS 选择器提取指定字段" },
] as const;

type Mode = typeof MODES[number]["id"];

export default function WafBypass() {
  const [health, setHealth] = useState<{ ok: boolean; chrome: string } | null>(null);
  const [mode, setMode] = useState<Mode>("bypass");
  const [url, setUrl] = useState("https://nowsecure.nl");
  const [headless, setHeadless] = useState(true);
  const [screenshot, setScreenshot] = useState(false);
  const [selectorKey, setSelectorKey] = useState("h1");
  const [selectorVal, setSelectorVal] = useState("h1");
  const [extraSelectors, setExtraSelectors] = useState<{ k: string; v: string }[]>([]);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BypassResult | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API}/tools/waf/healthz`)
      .then(r => r.json())
      .then(d => setHealth(d))
      .catch(() => setHealth({ ok: false, chrome: "" }));
  }, []);

  useEffect(() => {
    logRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log]);

  const addLog = (msg: string) => setLog(prev => [...prev, msg]);

  const run = async () => {
    if (!url.trim()) return;
    setRunning(true);
    setResult(null);
    setLog([]);
    addLog(`[${new Date().toLocaleTimeString()}] 开始 ${mode === "bypass" ? "绕过访问" : "数据提取"}…`);
    addLog(`URL: ${url}`);
    addLog(`headless: ${headless}${screenshot ? " | 截图: 是" : ""}`);

    try {
      const endpoint = mode === "bypass" ? "/tools/waf/bypass" : "/tools/waf/scrape";
      const body: Record<string, unknown> = { url, headless };
      if (mode === "bypass") {
        body.screenshot = screenshot;
      } else {
        const selectors: Record<string, string> = { [selectorKey]: selectorVal };
        for (const { k, v } of extraSelectors) {
          if (k && v) selectors[k] = v;
        }
        body.selectors = selectors;
        addLog(`选择器: ${JSON.stringify(selectors)}`);
      }

      addLog("正在绕过 Cloudflare，请等待…");
      const r = await fetch(`${API}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json() as BypassResult;
      setResult(d);

      if (d.success) {
        addLog(`✅ 成功！耗时 ${d.elapsed}s`);
        addLog(`标题: ${d.title}`);
        addLog(`最终 URL: ${d.url}`);
        addLog(`HTML: ${d.html_length?.toLocaleString()} 字符`);
        if (d.data) {
          for (const [k, v] of Object.entries(d.data)) {
            addLog(`${k}: ${v ?? "(null)"}`);
          }
        }
      } else {
        addLog(`❌ 失败: ${d.error}`);
      }
    } catch (e) {
      addLog(`❌ 网络错误: ${String(e)}`);
      setResult({ success: false, error: String(e) });
    }
    setRunning(false);
  };

  return (
    <div className="space-y-6">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <span>🛡️</span> CF WAF 绕过工具
          </h2>
          <p className="text-sm text-gray-400 mt-1">
            基于 pydoll-python，零 WebDriver 绕过 Cloudflare WAF / Turnstile / Managed Challenge
          </p>
        </div>
        <button
          onClick={() =>
            fetch(`${API}/tools/waf/healthz`).then(r => r.json()).then(d => setHealth(d)).catch(() => setHealth({ ok: false, chrome: "" }))
          }
          className="px-3 py-1.5 text-xs rounded-lg bg-[#21262d] hover:bg-[#30363d] text-gray-300 border border-[#30363d] transition-colors"
        >
          刷新状态
        </button>
      </div>

      {/* 状态卡 */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4">
          <div className="text-xs text-gray-500 mb-1">pydoll 服务</div>
          <div className={`text-sm font-semibold flex items-center gap-2 ${health?.ok ? "text-emerald-400" : "text-red-400"}`}>
            <span className={`w-2 h-2 rounded-full ${health?.ok ? "bg-emerald-400 animate-pulse" : "bg-red-400"}`} />
            {health === null ? "检测中…" : health?.ok ? "运行中 :8766" : "不可达"}
          </div>
        </div>
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4">
          <div className="text-xs text-gray-500 mb-1">Chrome 引擎</div>
          <div className="text-sm font-mono text-gray-300 truncate">
            {health?.chrome ? health.chrome.split("/").slice(-3).join("/") : "–"}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 左：输入面板 */}
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-5 space-y-4">
          {/* 模式切换 */}
          <div className="flex gap-2">
            {MODES.map(m => (
              <button
                key={m.id}
                onClick={() => setMode(m.id)}
                className={`flex-1 py-2 rounded-lg text-sm font-medium transition-all ${
                  mode === m.id
                    ? "bg-violet-600/30 border border-violet-500/50 text-violet-300"
                    : "bg-[#21262d] border border-[#30363d] text-gray-400 hover:text-gray-200"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-gray-500">{MODES.find(m => m.id === mode)?.desc}</p>

          {/* URL 输入 */}
          <div>
            <label className="text-xs text-gray-400 mb-1 block">目标 URL</label>
            <input
              value={url}
              onChange={e => setUrl(e.target.value)}
              placeholder="https://example.com"
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-600 outline-none focus:border-violet-500 transition-colors font-mono"
            />
          </div>

          {/* 选项 */}
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <div
                onClick={() => setHeadless(v => !v)}
                className={`relative w-9 h-5 rounded-full transition-colors ${headless ? "bg-blue-600" : "bg-gray-700"}`}
              >
                <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${headless ? "translate-x-4" : "translate-x-0.5"}`} />
              </div>
              <span className="text-xs text-gray-300">无头模式</span>
            </label>
            {mode === "bypass" && (
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <div
                  onClick={() => setScreenshot(v => !v)}
                  className={`relative w-9 h-5 rounded-full transition-colors ${screenshot ? "bg-emerald-600" : "bg-gray-700"}`}
                >
                  <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${screenshot ? "translate-x-4" : "translate-x-0.5"}`} />
                </div>
                <span className="text-xs text-gray-300">保存截图</span>
              </label>
            )}
          </div>

          {/* 选择器（scrape 模式） */}
          {mode === "scrape" && (
            <div className="space-y-2">
              <label className="text-xs text-gray-400 block">CSS 选择器</label>
              <div className="flex gap-2">
                <input
                  value={selectorKey}
                  onChange={e => setSelectorKey(e.target.value)}
                  placeholder="字段名"
                  className="w-28 bg-[#0d1117] border border-[#30363d] rounded-lg px-2 py-2 text-xs text-white outline-none focus:border-violet-500 font-mono"
                />
                <input
                  value={selectorVal}
                  onChange={e => setSelectorVal(e.target.value)}
                  placeholder="CSS selector"
                  className="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-2 py-2 text-xs text-white outline-none focus:border-violet-500 font-mono"
                />
              </div>
              {extraSelectors.map((s, i) => (
                <div key={i} className="flex gap-2">
                  <input
                    value={s.k}
                    onChange={e => setExtraSelectors(prev => prev.map((x, j) => j === i ? { ...x, k: e.target.value } : x))}
                    placeholder="字段名"
                    className="w-28 bg-[#0d1117] border border-[#30363d] rounded-lg px-2 py-2 text-xs text-white outline-none focus:border-violet-500 font-mono"
                  />
                  <input
                    value={s.v}
                    onChange={e => setExtraSelectors(prev => prev.map((x, j) => j === i ? { ...x, v: e.target.value } : x))}
                    placeholder="CSS selector"
                    className="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-2 py-2 text-xs text-white outline-none focus:border-violet-500 font-mono"
                  />
                  <button
                    onClick={() => setExtraSelectors(prev => prev.filter((_, j) => j !== i))}
                    className="px-2 text-gray-500 hover:text-red-400 transition-colors"
                  >×</button>
                </div>
              ))}
              <button
                onClick={() => setExtraSelectors(prev => [...prev, { k: "", v: "" }])}
                className="text-xs text-violet-400 hover:text-violet-300 transition-colors"
              >
                + 添加选择器
              </button>
            </div>
          )}

          {/* 运行按钮 */}
          <button
            onClick={run}
            disabled={running || !url.trim() || !health?.ok}
            className={`w-full py-3 rounded-xl text-sm font-semibold transition-all ${
              running
                ? "bg-violet-600/40 text-violet-300 cursor-not-allowed"
                : !health?.ok
                ? "bg-gray-700 text-gray-500 cursor-not-allowed"
                : "bg-violet-600 hover:bg-violet-500 text-white shadow-lg shadow-violet-900/30"
            }`}
          >
            {running ? (
              <span className="flex items-center justify-center gap-2">
                <span className="w-4 h-4 border-2 border-violet-300 border-t-transparent rounded-full animate-spin" />
                绕过中，请稍候…
              </span>
            ) : "🚀 开始绕过"}
          </button>
        </div>

        {/* 右：结果面板 */}
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-5 space-y-4">
          <h3 className="text-sm font-semibold text-white">执行日志 / 结果</h3>

          {/* 日志流 */}
          <div className="bg-[#0d1117] rounded-lg p-3 h-56 overflow-y-auto font-mono text-xs space-y-0.5">
            {log.length === 0 ? (
              <div className="text-gray-600 text-center mt-16">等待执行…</div>
            ) : (
              log.map((line, i) => (
                <div
                  key={i}
                  className={
                    line.startsWith("✅") ? "text-emerald-400" :
                    line.startsWith("❌") ? "text-red-400" :
                    line.startsWith("URL") || line.startsWith("最终") ? "text-blue-400" :
                    line.includes("耗时") ? "text-violet-400 font-semibold" :
                    "text-gray-400"
                  }
                >
                  {line}
                </div>
              ))
            )}
            <div ref={logRef} />
          </div>

          {/* 结果卡片 */}
          {result && (
            <div className={`rounded-xl p-4 space-y-3 ${result.success ? "bg-emerald-500/10 border border-emerald-500/30" : "bg-red-500/10 border border-red-500/30"}`}>
              {result.success ? (
                <>
                  <div className="flex items-center gap-2">
                    <span className="text-emerald-400 font-semibold text-sm">✅ 绕过成功</span>
                    <span className="text-xs text-gray-500 ml-auto">{result.elapsed}s</span>
                  </div>
                  <div className="space-y-1.5 text-xs">
                    <div className="flex gap-2">
                      <span className="text-gray-500 w-16 shrink-0">标题</span>
                      <span className="text-white font-medium">{result.title}</span>
                    </div>
                    <div className="flex gap-2">
                      <span className="text-gray-500 w-16 shrink-0">URL</span>
                      <a href={result.url} target="_blank" rel="noopener noreferrer"
                        className="text-blue-400 hover:text-blue-300 truncate">{result.url}</a>
                    </div>
                    <div className="flex gap-2">
                      <span className="text-gray-500 w-16 shrink-0">HTML</span>
                      <span className="text-gray-300">{result.html_length?.toLocaleString()} 字符</span>
                    </div>
                    {result.data && Object.entries(result.data).length > 0 && (
                      <div className="mt-2 pt-2 border-t border-emerald-500/20 space-y-1">
                        <span className="text-gray-500">提取数据</span>
                        {Object.entries(result.data).map(([k, v]) => (
                          <div key={k} className="flex gap-2 ml-2">
                            <span className="text-violet-400 w-20 shrink-0">{k}</span>
                            <span className="text-gray-200 break-all">{v ?? <em className="text-gray-600">null</em>}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <div>
                  <div className="text-red-400 font-semibold text-sm mb-2">❌ 失败</div>
                  <pre className="text-red-300 text-xs whitespace-pre-wrap break-all">{result.error}</pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* API 说明 */}
      <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-5">
        <h3 className="text-sm font-semibold text-white mb-3">API 接口（端口 8766）</h3>
        <div className="space-y-2 font-mono text-xs">
          {[
            { m: "GET",  p: "/api/tools/waf/healthz",   d: "服务健康检查" },
            { m: "POST", p: "/api/tools/waf/bypass",     d: '{ url, headless?, screenshot? }  — CF WAF 绕过' },
            { m: "POST", p: "/api/tools/waf/scrape",     d: '{ url, selectors, headless? }  — 隐蔽爬取' },
          ].map(({ m, p, d }) => (
            <div key={p} className="flex items-start gap-3 py-1.5 border-b border-[#21262d] last:border-0">
              <span className={`shrink-0 px-2 py-0.5 rounded text-xs font-bold ${m === "GET" ? "bg-blue-500/20 text-blue-400" : "bg-violet-500/20 text-violet-400"}`}>{m}</span>
              <span className="text-gray-300 flex-1">{p}</span>
              <span className="text-gray-600 hidden sm:block">{d}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
