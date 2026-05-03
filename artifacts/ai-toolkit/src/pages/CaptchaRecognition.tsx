import { useState, useRef, useEffect, useCallback } from "react";

const API = import.meta.env.BASE_URL.replace(/\/$/, "") + "/api";

// ─── CNN 数字验证码 types ────────────────────────────────────────────
interface TrainStatus {
  running: boolean;
  returncode: number | null;
  logs: string[];
}
interface RecognizeResult {
  text?: string;
  confidence?: number;
  error?: string;
}

// ─── 文字点选验证码 types ────────────────────────────────────────────
interface PointResult {
  x_rel: number;
  y_rel: number;
}
interface CorpResult {
  x1: number; y1: number; x2: number; y2: number;
}
interface TextSelectRes {
  imgW: number;
  imgH: number;
  point: PointResult[];
  corp: CorpResult[];
}
interface TextSelectResult {
  code?: number;
  msg?: string;
  data?: { imageID: string; res: TextSelectRes };
  error?: string;
}

type MainTab = "cnn" | "text-select";

export default function CaptchaRecognition() {
  const [mainTab, setMainTab] = useState<MainTab>("text-select");

  // ─── CNN tab state ───────────────────────────────────────────────
  const [health, setHealth] = useState<{ ok: boolean; model_ready: boolean } | null>(null);
  const [trainStatus, setTrainStatus] = useState<TrainStatus | null>(null);
  const [trainPolling, setTrainPolling] = useState(false);
  const [skipGen, setSkipGen] = useState(false);
  const [startingTrain, setStartingTrain] = useState(false);
  const [cnnImageB64, setCnnImageB64] = useState("");
  const [cnnImagePreview, setCnnImagePreview] = useState("");
  const [recognizing, setRecognizing] = useState(false);
  const [cnnResult, setCnnResult] = useState<RecognizeResult | null>(null);
  const trainPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  // ─── Text-select tab state ───────────────────────────────────────
  const [tsHealth, setTsHealth] = useState<{ ok: boolean } | null>(null);
  const [tsInputMode, setTsInputMode] = useState<"upload" | "url">("upload");
  const [tsImageB64, setTsImageB64] = useState("");
  const [tsImageUrl, setTsImageUrl] = useState("");
  const [tsPreview, setTsPreview] = useState("");
  const [tsRecognizing, setTsRecognizing] = useState(false);
  const [tsResult, setTsResult] = useState<TextSelectResult | null>(null);
  const [tsShowImg, setTsShowImg] = useState<string | null>(null);
  const [tsLoadingShow, setTsLoadingShow] = useState(false);

  // ─── CNN: health + train status ─────────────────────────────────
  const fetchHealth = useCallback(async () => {
    try {
      const r = await fetch(`${API}/tools/captcha/health`);
      const d = await r.json() as { ok: boolean; model_ready: boolean };
      setHealth(d);
    } catch { setHealth({ ok: false, model_ready: false }); }
  }, []);

  const fetchTrainStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/tools/captcha/train/status`);
      const d = await r.json() as TrainStatus;
      setTrainStatus(d);
      if (!d.running) {
        setTrainPolling(false);
        if (trainPollRef.current) { clearInterval(trainPollRef.current); trainPollRef.current = null; }
        await fetchHealth();
      }
    } catch {}
  }, [fetchHealth]);

  // ─── Text-select: health ─────────────────────────────────────────
  const fetchTsHealth = useCallback(async () => {
    try {
      const r = await fetch(`${API}/tools/text-captcha/health`);
      const d = await r.json() as { ok: boolean };
      setTsHealth(d);
    } catch { setTsHealth({ ok: false }); }
  }, []);

  useEffect(() => {
    fetchHealth();
    fetchTrainStatus();
    fetchTsHealth();
  }, [fetchHealth, fetchTrainStatus, fetchTsHealth]);

  useEffect(() => {
    if (trainPolling && !trainPollRef.current) {
      trainPollRef.current = setInterval(fetchTrainStatus, 3000);
    }
    return () => {
      if (!trainPolling && trainPollRef.current) {
        clearInterval(trainPollRef.current); trainPollRef.current = null;
      }
    };
  }, [trainPolling, fetchTrainStatus]);

  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [trainStatus?.logs]);

  // ─── CNN handlers ────────────────────────────────────────────────
  const startTrain = async () => {
    setStartingTrain(true);
    try {
      const r = await fetch(`${API}/tools/captcha/train/start`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skip_gen: skipGen }),
      });
      const d = await r.json() as { started?: boolean };
      if (d.started) { setTrainPolling(true); await fetchTrainStatus(); }
    } catch {}
    setStartingTrain(false);
  };

  const onCnnFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const url = ev.target?.result as string;
      setCnnImagePreview(url);
      setCnnImageB64(url.split(",")[1] ?? "");
      setCnnResult(null);
    };
    reader.readAsDataURL(file);
  };

  const recognize = async () => {
    if (!cnnImageB64) return;
    setRecognizing(true); setCnnResult(null);
    try {
      const r = await fetch(`${API}/tools/captcha/recognize`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base64: cnnImageB64 }),
      });
      setCnnResult(await r.json() as RecognizeResult);
    } catch (e) { setCnnResult({ error: String(e) }); }
    setRecognizing(false);
  };

  // ─── Text-select handlers ────────────────────────────────────────
  const onTsFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const url = ev.target?.result as string;
      setTsPreview(url);
      setTsImageB64(url.split(",")[1] ?? "");
      setTsResult(null); setTsShowImg(null);
    };
    reader.readAsDataURL(file);
  };

  const tsRecognize = async () => {
    const hasInput = tsInputMode === "upload" ? !!tsImageB64 : !!tsImageUrl.trim();
    if (!hasInput) return;
    setTsRecognizing(true); setTsResult(null); setTsShowImg(null);
    try {
      const body = tsInputMode === "upload"
        ? { dataType: 2, imageSource: tsImageB64 }
        : { dataType: 1, imageSource: tsImageUrl.trim() };
      const r = await fetch(`${API}/tools/text-captcha/identify`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setTsResult(await r.json() as TextSelectResult);
    } catch (e) { setTsResult({ error: String(e) }); }
    setTsRecognizing(false);
  };

  const tsGetShowImg = async () => {
    const hasInput = tsInputMode === "upload" ? !!tsImageB64 : !!tsImageUrl.trim();
    if (!hasInput) return;
    setTsLoadingShow(true);
    try {
      const body = tsInputMode === "upload"
        ? { dataType: 2, imageSource: tsImageB64 }
        : { dataType: 1, imageSource: tsImageUrl.trim() };
      const r = await fetch(`${API}/tools/text-captcha/show`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const blob = await r.blob();
      setTsShowImg(URL.createObjectURL(blob));
    } catch {}
    setTsLoadingShow(false);
  };

  const modelReady = health?.model_ready ?? false;
  const isRunning = trainStatus?.running ?? false;
  const tsOk = tsHealth?.ok ?? false;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white">验证码识别</h2>
          <p className="text-sm text-gray-400 mt-1">CNN 数字验证码 · 文字点选验证码 (YOLO + ONNX)</p>
        </div>
        <button
          onClick={() => { fetchHealth(); fetchTsHealth(); fetchTrainStatus(); }}
          className="px-3 py-1.5 text-xs rounded-lg bg-[#21262d] hover:bg-[#30363d] text-gray-300 border border-[#30363d] transition-colors"
        >
          刷新状态
        </button>
      </div>

      {/* Tab switcher */}
      <div className="flex gap-2 bg-[#161b22] border border-[#30363d] rounded-xl p-1 w-fit">
        {([
          { id: "text-select", label: "🖱️ 文字点选验证码", badge: "YOLO+ONNX" },
          { id: "cnn",         label: "🔢 CNN 数字验证码", badge: "PyTorch" },
        ] as { id: MainTab; label: string; badge: string }[]).map(t => (
          <button
            key={t.id}
            onClick={() => setMainTab(t.id)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              mainTab === t.id
                ? "bg-blue-600 text-white shadow"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            {t.label}
            <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${
              mainTab === t.id ? "bg-white/20" : "bg-[#21262d] text-gray-500"
            }`}>{t.badge}</span>
          </button>
        ))}
      </div>

      {/* ═══════════════ TEXT-SELECT TAB ═══════════════ */}
      {mainTab === "text-select" && (
        <div className="space-y-5">
          {/* Status bar */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4">
              <div className="text-xs text-gray-500 mb-1">文字点选服务</div>
              <div className={`text-sm font-semibold flex items-center gap-2 ${tsOk ? "text-emerald-400" : "text-red-400"}`}>
                <span className={`w-2 h-2 rounded-full ${tsOk ? "bg-emerald-400 animate-pulse" : "bg-red-400"}`} />
                {tsHealth === null ? "检测中…" : tsOk ? "运行中 :8767" : "不可达 — 服务未启动"}
              </div>
            </div>
            <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4">
              <div className="text-xs text-gray-500 mb-1">模型</div>
              <div className="text-sm font-semibold text-emerald-400 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-400" />
                best_v3.onnx · pre_model_v7.onnx
              </div>
            </div>
          </div>

          {/* Input mode switcher */}
          <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-5 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-white">输入图片</h3>
              <div className="flex gap-1 bg-[#0d1117] rounded-lg p-1">
                {(["upload", "url"] as const).map(m => (
                  <button key={m} onClick={() => { setTsInputMode(m); setTsResult(null); setTsShowImg(null); }}
                    className={`px-3 py-1 rounded text-xs font-medium transition-all ${tsInputMode === m ? "bg-blue-600 text-white" : "text-gray-400 hover:text-gray-200"}`}>
                    {m === "upload" ? "📁 上传图片" : "🔗 图片 URL"}
                  </button>
                ))}
              </div>
            </div>

            {tsInputMode === "upload" ? (
              <label className="block w-full rounded-xl border-2 border-dashed border-[#30363d] hover:border-blue-500 transition-colors cursor-pointer">
                <input type="file" accept="image/*" className="hidden" onChange={onTsFileChange} />
                {tsPreview ? (
                  <div className="p-4 flex flex-col items-center gap-3">
                    <img src={tsPreview} alt="preview" className="max-h-32 rounded-lg border border-[#30363d] object-contain" />
                    <span className="text-xs text-gray-500">点击更换图片</span>
                  </div>
                ) : (
                  <div className="py-10 flex flex-col items-center gap-2 text-gray-500">
                    <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                    </svg>
                    <span className="text-sm">点击上传验证码截图</span>
                    <span className="text-xs">支持 geetest 文字点选类型</span>
                  </div>
                )}
              </label>
            ) : (
              <div className="space-y-3">
                <input
                  type="url"
                  value={tsImageUrl}
                  onChange={e => { setTsImageUrl(e.target.value); setTsResult(null); setTsShowImg(null); }}
                  placeholder="https://static.geetest.com/captcha_v3/batch/... .jpg"
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500"
                />
                {tsImageUrl && (
                  <img src={tsImageUrl} alt="url preview" className="max-h-32 rounded-lg border border-[#30363d] object-contain" onError={() => {}} />
                )}
              </div>
            )}

            <div className="flex gap-3">
              <button
                onClick={tsRecognize}
                disabled={tsRecognizing || (tsInputMode === "upload" ? !tsImageB64 : !tsImageUrl.trim())}
                className={`flex-1 py-2.5 rounded-xl text-sm font-semibold transition-all ${
                  tsRecognizing || (tsInputMode === "upload" ? !tsImageB64 : !tsImageUrl.trim())
                    ? "bg-blue-600/30 text-blue-400/50 cursor-not-allowed"
                    : "bg-blue-600 hover:bg-blue-500 text-white"
                }`}
              >
                {tsRecognizing ? "识别中…" : "🔍 识别坐标"}
              </button>
              <button
                onClick={tsGetShowImg}
                disabled={tsLoadingShow || (tsInputMode === "upload" ? !tsImageB64 : !tsImageUrl.trim())}
                className={`px-4 py-2.5 rounded-xl text-sm font-semibold transition-all border ${
                  tsLoadingShow || (tsInputMode === "upload" ? !tsImageB64 : !tsImageUrl.trim())
                    ? "border-[#30363d] text-gray-600 cursor-not-allowed"
                    : "border-[#30363d] hover:border-blue-500 text-gray-300 hover:text-white"
                }`}
              >
                {tsLoadingShow ? "生成中…" : "🖼️ 预览标注"}
              </button>
            </div>
          </div>

          {/* Results */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            {/* Coordinate result */}
            {tsResult && (
              <div className={`bg-[#161b22] border rounded-xl p-5 space-y-3 ${
                tsResult.error || tsResult.code !== 200
                  ? "border-red-500/30"
                  : "border-emerald-500/30"
              }`}>
                <h3 className="text-sm font-semibold text-white">识别结果</h3>
                {(tsResult.error || tsResult.code !== 200) ? (
                  <div className="text-red-400 text-sm bg-red-500/10 rounded-lg p-3">
                    {tsResult.error || tsResult.msg || "识别失败"}
                  </div>
                ) : tsResult.data?.res ? (
                  <div className="space-y-3">
                    <div className="flex gap-4 text-xs text-gray-500">
                      <span>图片尺寸: {tsResult.data.res.imgW} × {tsResult.data.res.imgH}</span>
                      <span>找到 {tsResult.data.res.point.length} 个点击目标</span>
                    </div>
                    <div className="space-y-2">
                      {tsResult.data.res.point.map((pt, i) => (
                        <div key={i} className="flex items-center gap-3 bg-[#0d1117] rounded-lg px-3 py-2">
                          <span className="w-6 h-6 rounded-full bg-blue-600 text-white text-xs flex items-center justify-center font-bold shrink-0">{i + 1}</span>
                          <div className="flex-1">
                            <div className="text-xs text-gray-300 font-mono">
                              中心: ({Math.round(pt.x_rel)}, {Math.round(pt.y_rel)})
                            </div>
                            <div className="text-xs text-gray-600 font-mono">
                              框: ({Math.round(tsResult.data!.res.corp[i].x1)},{Math.round(tsResult.data!.res.corp[i].y1)}) → ({Math.round(tsResult.data!.res.corp[i].x2)},{Math.round(tsResult.data!.res.corp[i].y2)})
                            </div>
                          </div>
                          <span className="text-xs text-blue-400 font-mono shrink-0">
                            click({Math.round(pt.x_rel)}, {Math.round(pt.y_rel)})
                          </span>
                        </div>
                      ))}
                    </div>
                    {/* Copy JSON */}
                    <button
                      onClick={() => navigator.clipboard.writeText(JSON.stringify(tsResult.data?.res.point, null, 2))}
                      className="w-full py-1.5 rounded-lg text-xs text-gray-400 hover:text-gray-200 border border-[#30363d] hover:border-[#444] transition-colors"
                    >
                      📋 复制坐标 JSON
                    </button>
                  </div>
                ) : null}
              </div>
            )}

            {/* Annotated image */}
            {tsShowImg && (
              <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-5 space-y-3">
                <h3 className="text-sm font-semibold text-white">标注效果图</h3>
                <img src={tsShowImg} alt="annotated" className="w-full rounded-lg border border-[#30363d] object-contain" />
                <a
                  href={tsShowImg}
                  download="captcha_result.jpg"
                  className="block w-full py-1.5 rounded-lg text-xs text-center text-gray-400 hover:text-gray-200 border border-[#30363d] hover:border-[#444] transition-colors"
                >
                  ⬇️ 下载标注图
                </a>
              </div>
            )}
          </div>

          {/* API reference */}
          <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-5">
            <h3 className="text-sm font-semibold text-white mb-3">API 接口（文字点选）</h3>
            <div className="space-y-2 font-mono text-xs">
              {[
                { method: "GET",  path: "/api/tools/text-captcha/health",   desc: "服务健康状态" },
                { method: "POST", path: "/api/tools/text-captcha/identify",  desc: '{ dataType:1(url)|2(base64), imageSource, imageID? } → 点击坐标数组' },
                { method: "POST", path: "/api/tools/text-captcha/show",      desc: '同上 → JPEG 标注图（bbox 可视化）' },
              ].map(({ method, path, desc }) => (
                <div key={path} className="flex items-start gap-3 py-1.5 border-b border-[#21262d] last:border-0">
                  <span className={`shrink-0 px-2 py-0.5 rounded text-xs font-bold ${method === "GET" ? "bg-blue-500/20 text-blue-400" : "bg-emerald-500/20 text-emerald-400"}`}>{method}</span>
                  <span className="text-gray-300 flex-1">{path}</span>
                  <span className="text-gray-600 hidden sm:block text-right max-w-xs">{desc}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ═══════════════ CNN TAB ═══════════════ */}
      {mainTab === "cnn" && (
        <div className="space-y-5">
          {/* Status */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4">
              <div className="text-xs text-gray-500 mb-1">API 服务</div>
              <div className={`text-sm font-semibold flex items-center gap-2 ${health?.ok ? "text-emerald-400" : "text-red-400"}`}>
                <span className={`w-2 h-2 rounded-full ${health?.ok ? "bg-emerald-400 animate-pulse" : "bg-red-400"}`} />
                {health === null ? "检测中…" : health.ok ? "运行中 :8765" : "不可达"}
              </div>
            </div>
            <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4">
              <div className="text-xs text-gray-500 mb-1">模型状态</div>
              <div className={`text-sm font-semibold flex items-center gap-2 ${modelReady ? "text-emerald-400" : "text-amber-400"}`}>
                <span className={`w-2 h-2 rounded-full ${modelReady ? "bg-emerald-400" : "bg-amber-400"}`} />
                {modelReady ? "已就绪" : "未训练"}
              </div>
            </div>
            <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4">
              <div className="text-xs text-gray-500 mb-1">训练任务</div>
              <div className={`text-sm font-semibold flex items-center gap-2 ${isRunning ? "text-blue-400" : "text-gray-400"}`}>
                <span className={`w-2 h-2 rounded-full ${isRunning ? "bg-blue-400 animate-pulse" : "bg-gray-600"}`} />
                {isRunning ? "训练中…" : trainStatus?.returncode === 0 ? "已完成" : "空闲"}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Train panel */}
            <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-5 space-y-4">
              <h3 className="text-sm font-semibold text-white">模型训练</h3>
              <p className="text-xs text-gray-500">自动生成 2000 张训练 / 1000 张测试数字验证码，训练 CNN 模型（200 epoch）。首次约 10–20 分钟。</p>
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <div onClick={() => setSkipGen(v => !v)}
                  className={`relative w-10 h-5 rounded-full transition-colors ${skipGen ? "bg-blue-600" : "bg-gray-700"}`}>
                  <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${skipGen ? "translate-x-5" : "translate-x-0.5"}`} />
                </div>
                <span className="text-sm text-gray-300">跳过数据生成</span>
              </label>
              <button
                onClick={startTrain}
                disabled={isRunning || startingTrain || !health?.ok}
                className={`w-full py-2.5 rounded-xl text-sm font-semibold transition-all ${
                  isRunning || startingTrain ? "bg-blue-600/40 text-blue-300 cursor-not-allowed"
                  : !health?.ok ? "bg-gray-700 text-gray-500 cursor-not-allowed"
                  : "bg-blue-600 hover:bg-blue-500 text-white"
                }`}
              >
                {isRunning ? "⏳ 训练进行中…" : startingTrain ? "启动中…" : "🚀 启动训练"}
              </button>
              {(trainStatus?.logs?.length ?? 0) > 0 && (
                <div className="bg-[#0d1117] rounded-lg p-3 h-48 overflow-y-auto font-mono text-xs space-y-0.5">
                  {trainStatus!.logs.map((line, i) => (
                    <div key={i} className={
                      line.includes("acc=") ? "text-emerald-400" :
                      line.includes("保存") ? "text-purple-400" :
                      line.includes("完成") ? "text-emerald-300 font-semibold" :
                      line.includes("Error") || line.includes("error") ? "text-red-400" :
                      "text-gray-400"
                    }>{line}</div>
                  ))}
                  <div ref={logEndRef} />
                </div>
              )}
            </div>

            {/* Recognize panel */}
            <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-5 space-y-4">
              <h3 className="text-sm font-semibold text-white">数字验证码识别</h3>
              <label className={`block w-full rounded-xl border-2 border-dashed transition-colors cursor-pointer ${
                modelReady ? "border-[#30363d] hover:border-blue-500" : "border-gray-700 cursor-not-allowed opacity-50"
              }`}>
                <input type="file" accept="image/*" className="hidden" onChange={onCnnFileChange} disabled={!modelReady} />
                {cnnImagePreview ? (
                  <div className="p-4 flex flex-col items-center gap-3">
                    <img src={cnnImagePreview} alt="captcha preview" className="max-h-24 rounded-lg border border-[#30363d] object-contain" />
                    <span className="text-xs text-gray-500">点击更换</span>
                  </div>
                ) : (
                  <div className="py-10 flex flex-col items-center gap-2 text-gray-500">
                    <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                    </svg>
                    <span className="text-sm">{modelReady ? "点击上传验证码图片" : "请先训练模型"}</span>
                  </div>
                )}
              </label>
              <button
                onClick={recognize}
                disabled={!cnnImageB64 || !modelReady || recognizing}
                className={`w-full py-2.5 rounded-xl text-sm font-semibold transition-all ${
                  !cnnImageB64 || !modelReady || recognizing
                    ? "bg-emerald-600/30 text-emerald-400/50 cursor-not-allowed"
                    : "bg-emerald-600 hover:bg-emerald-500 text-white"
                }`}
              >
                {recognizing ? "识别中…" : "🔍 开始识别"}
              </button>
              {cnnResult && (
                <div className={`rounded-xl p-4 ${cnnResult.error ? "bg-red-500/10 border border-red-500/30" : "bg-emerald-500/10 border border-emerald-500/30"}`}>
                  {cnnResult.error ? (
                    <p className="text-red-400 text-sm">{cnnResult.error}</p>
                  ) : (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-500">识别结果</span>
                        <span className="text-xs text-gray-500">置信度 {((cnnResult.confidence ?? 0) * 100).toFixed(1)}%</span>
                      </div>
                      <div className="text-4xl font-mono font-bold text-emerald-400 text-center tracking-widest py-2">{cnnResult.text}</div>
                      <div className="w-full h-2 bg-gray-700 rounded-full overflow-hidden">
                        <div className="h-full bg-emerald-500 rounded-full transition-all" style={{ width: `${(cnnResult.confidence ?? 0) * 100}%` }} />
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
