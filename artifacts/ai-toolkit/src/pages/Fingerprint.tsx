import { useState } from "react";

interface FingerprintProfile {
  userAgent: string;
  platform: string;
  language: string;
  languages: string[];
  timezone: string;
  timezoneOffset: number;
  screen: { width: number; height: number; availWidth: number; availHeight: number; colorDepth: number; pixelDepth: number };
  viewport: { innerWidth: number; innerHeight: number; outerWidth: number; outerHeight: number };
  devicePixelRatio: number;
  webgl: { vendor: string; renderer: string };
  canvas: { hash: string; winding: boolean };
  audio: { hash: string; oscillator: string };
  fonts: string[];
  plugins: string[];
  doNotTrack: string | null;
  cookieEnabled: boolean;
  hardwareConcurrency: number;
  deviceMemory: number;
  maxTouchPoints: number;
  connectionType: string;
  generatedAt: string;
}

export default function Fingerprint() {
  const [profiles, setProfiles] = useState<FingerprintProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [count, setCount] = useState(3);
  const [selected, setSelected] = useState(0);
  const [copied, setCopied] = useState<string | null>(null);

  const generate = async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/tools/fingerprint/generate?count=${count}`);
      const d = await r.json();
      if (d.success) { setProfiles(d.profiles); setSelected(0); }
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  const copy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 1200);
  };

  const p = profiles[selected];

  const getOs = (ua: string) => {
    if (ua.includes("iPhone") || ua.includes("Android")) return "📱";
    if (ua.includes("Macintosh") || ua.includes("Mac OS X")) return "🍎";
    if (ua.includes("Windows")) return "🪟";
    return "🐧";
  };

  const getBrowser = (ua: string) => {
    if (ua.includes("Edg/")) return "Edge";
    if (ua.includes("Firefox/")) return "Firefox";
    if (ua.includes("Safari/") && ua.includes("Chrome/")) return "Chrome";
    if (ua.includes("Safari/") && !ua.includes("Chrome")) return "Safari";
    return "Chrome";
  };

  const Row = ({ label, value, k }: { label: string; value: string; k: string }) => (
    <div className="flex items-start gap-2 py-1.5 border-b border-[#21262d] last:border-0">
      <span className="text-[11px] text-gray-500 w-28 shrink-0 pt-0.5">{label}</span>
      <span className="text-[12px] font-mono text-gray-200 flex-1 break-all">{value}</span>
      <button
        onClick={() => copy(value, k)}
        className={`text-[11px] px-1.5 py-0.5 rounded border shrink-0 transition-all ${
          copied === k
            ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400"
            : "bg-[#21262d] border-[#30363d] text-gray-500 hover:text-white"
        }`}
      >
        {copied === k ? "✓" : "复制"}
      </button>
    </div>
  );

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">浏览器指纹生成器</h2>
        <p className="text-sm text-gray-400">
          基于 <span className="text-blue-400">codex-register</span> / <span className="text-blue-400">outlook-batch-manager</span> 的指纹规避方案，生成真实可信的浏览器环境档案，用于自动化注册时绕过反爬检测
        </p>
      </div>

      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 bg-[#161b22] border border-[#21262d] rounded-xl px-4 py-2">
          <span className="text-xs text-gray-400">生成数量</span>
          <input
            type="number"
            min={1} max={10} value={count}
            onChange={(e) => setCount(Math.min(10, Math.max(1, Number(e.target.value))))}
            className="w-12 bg-transparent text-white text-sm text-center outline-none"
          />
          <span className="text-xs text-gray-500">（最多10）</span>
        </div>
        <button
          onClick={generate}
          disabled={loading}
          className="flex-1 py-2.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 rounded-xl text-white font-medium text-sm transition-all"
        >
          {loading ? "生成中..." : "🎭 生成指纹档案"}
        </button>
        {profiles.length > 0 && (
          <button
            onClick={() => copy(JSON.stringify(profiles, null, 2), "all")}
            className={`px-4 py-2.5 rounded-xl border text-sm transition-all ${
              copied === "all"
                ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400"
                : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"
            }`}
          >
            {copied === "all" ? "✓ 已复制" : "导出 JSON"}
          </button>
        )}
      </div>

      {profiles.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          {/* 左侧列表 */}
          <div className="lg:col-span-1 space-y-2">
            {profiles.map((fp, i) => (
              <button
                key={i}
                onClick={() => setSelected(i)}
                className={`w-full text-left p-3 rounded-xl border transition-all ${
                  selected === i
                    ? "bg-purple-500/10 border-purple-500/40 text-white"
                    : "bg-[#161b22] border-[#21262d] text-gray-400 hover:border-purple-500/20"
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-base">{getOs(fp.userAgent)}</span>
                  <span className="text-xs font-medium">{getBrowser(fp.userAgent)}</span>
                </div>
                <p className="text-[11px] text-gray-500">{fp.screen.width}×{fp.screen.height} {fp.devicePixelRatio}x</p>
                <p className="text-[11px] text-gray-500 truncate">{fp.timezone}</p>
              </button>
            ))}
          </div>

          {/* 右侧详情 */}
          {p && (
            <div className="lg:col-span-3 space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-lg">{getOs(p.userAgent)}</span>
                  <span className="text-sm font-semibold text-white">指纹档案 #{selected + 1}</span>
                </div>
                <button
                  onClick={() => copy(JSON.stringify(p, null, 2), "single")}
                  className={`text-xs px-3 py-1 rounded border transition-all ${
                    copied === "single"
                      ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400"
                      : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"
                  }`}
                >
                  {copied === "single" ? "✓" : "复制此档案"}
                </button>
              </div>

              {/* 基础环境 */}
              <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
                <h3 className="text-xs font-semibold text-gray-400 mb-3">🌐 基础环境</h3>
                <Row label="User-Agent"        value={p.userAgent}        k="ua" />
                <Row label="平台 Platform"     value={p.platform}         k="plt" />
                <Row label="语言 Language"     value={p.language}         k="lang" />
                <Row label="语言列表"          value={p.languages.join(", ")} k="langs" />
                <Row label="时区"              value={`${p.timezone} (UTC${p.timezoneOffset >= 0 ? "+" : ""}${-p.timezoneOffset / 60})`} k="tz" />
                <Row label="连接类型"          value={p.connectionType}   k="conn" />
              </div>

              {/* 屏幕 & 视口 */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
                  <h3 className="text-xs font-semibold text-gray-400 mb-3">🖥️ 屏幕信息</h3>
                  <Row label="分辨率"      value={`${p.screen.width}×${p.screen.height}`} k="res" />
                  <Row label="可用区域"    value={`${p.screen.availWidth}×${p.screen.availHeight}`} k="avail" />
                  <Row label="色彩深度"    value={`${p.screen.colorDepth}bit`} k="color" />
                  <Row label="DPR"         value={String(p.devicePixelRatio)} k="dpr" />
                </div>
                <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
                  <h3 className="text-xs font-semibold text-gray-400 mb-3">📐 视口信息</h3>
                  <Row label="innerWidth"  value={`${p.viewport.innerWidth}×${p.viewport.innerHeight}`} k="inner" />
                  <Row label="outerWidth"  value={`${p.viewport.outerWidth}×${p.viewport.outerHeight}`} k="outer" />
                  <Row label="触摸点"      value={String(p.maxTouchPoints)} k="touch" />
                </div>
              </div>

              {/* 硬件 & 指纹哈希 */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
                  <h3 className="text-xs font-semibold text-gray-400 mb-3">⚙️ 硬件信息</h3>
                  <Row label="CPU 核心数"  value={String(p.hardwareConcurrency)} k="cpu" />
                  <Row label="内存"        value={`${p.deviceMemory}GB`} k="mem" />
                  <Row label="WebGL 厂商"  value={p.webgl.vendor}   k="wv" />
                  <Row label="WebGL 渲染器" value={p.webgl.renderer} k="wr" />
                </div>
                <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
                  <h3 className="text-xs font-semibold text-gray-400 mb-3">🔏 指纹哈希</h3>
                  <Row label="Canvas Hash" value={p.canvas.hash} k="ch" />
                  <Row label="Audio Hash"  value={p.audio.hash} k="ah" />
                  <Row label="Audio Osc"   value={p.audio.oscillator} k="ao" />
                  <Row label="DNT"         value={p.doNotTrack ?? "null"} k="dnt" />
                </div>
              </div>

              {/* 字体 & 插件 */}
              <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
                <h3 className="text-xs font-semibold text-gray-400 mb-3">🔤 字体列表 ({p.fonts.length} 个)</h3>
                <div className="flex flex-wrap gap-1.5">
                  {p.fonts.map((f, i) => (
                    <span key={i} className="text-[11px] bg-[#21262d] text-gray-300 px-2 py-0.5 rounded">{f}</span>
                  ))}
                </div>
                {p.plugins.length > 0 && (
                  <>
                    <h3 className="text-xs font-semibold text-gray-400 mt-3 mb-2">🧩 插件列表</h3>
                    <div className="flex flex-wrap gap-1.5">
                      {p.plugins.map((pl, i) => (
                        <span key={i} className="text-[11px] bg-[#21262d] text-gray-300 px-2 py-0.5 rounded">{pl}</span>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {profiles.length === 0 && (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-10 text-center">
          <div className="text-4xl mb-3">🎭</div>
          <p className="text-gray-400 text-sm">点击"生成指纹档案"按钮开始</p>
          <p className="text-gray-600 text-xs mt-1 max-w-lg mx-auto">
            每个档案包含：UserAgent、平台、时区、语言、屏幕分辨率、WebGL、Canvas 哈希、Audio 哈希、字体列表、CPU/内存、插件、触摸点等 20+ 个指纹维度
          </p>
        </div>
      )}

      <div className="bg-[#0d1117] border border-[#30363d] rounded-xl p-4">
        <p className="text-xs font-semibold text-gray-400 mb-2">指纹规避使用场景</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-[11px] text-gray-500">
          <div className="flex gap-2"><span className="text-purple-400 shrink-0">①</span><span>配合 Playwright/patchright 等框架注入到自动化浏览器，让每次注册的指纹不同</span></div>
          <div className="flex gap-2"><span className="text-purple-400 shrink-0">②</span><span>将 UserAgent 字段配置到请求头，绕过服务端的 Bot 检测</span></div>
          <div className="flex gap-2"><span className="text-purple-400 shrink-0">③</span><span>与代理 IP 组合使用，形成"IP + 设备指纹"双维度隔离的账号注册环境</span></div>
        </div>
      </div>
    </div>
  );
}
