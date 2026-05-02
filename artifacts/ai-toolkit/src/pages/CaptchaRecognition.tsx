import { useState, useRef, useEffect, useCallback } from "react";

const API = import.meta.env.BASE_URL.replace(/\/$/, "") + "/api";

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

export default function CaptchaRecognition() {
  const [health, setHealth] = useState<{ ok: boolean; model_ready: boolean } | null>(null);
  const [trainStatus, setTrainStatus] = useState<TrainStatus | null>(null);
  const [trainPolling, setTrainPolling] = useState(false);
  const [skipGen, setSkipGen] = useState(false);
  const [startingTrain, setStartingTrain] = useState(false);

  const [imageB64, setImageB64] = useState("");
  const [imagePreview, setImagePreview] = useState("");
  const [recognizing, setRecognizing] = useState(false);
  const [result, setResult] = useState<RecognizeResult | null>(null);

  const trainPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  const fetchHealth = useCallback(async () => {
    try {
      const r = await fetch(`${API}/tools/captcha/health`);
      const d = await r.json() as { ok: boolean; model_ready: boolean };
      setHealth(d);
    } catch {
      setHealth({ ok: false, model_ready: false });
    }
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

  useEffect(() => {
    fetchHealth();
    fetchTrainStatus();
  }, [fetchHealth, fetchTrainStatus]);

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

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [trainStatus?.logs]);

  const startTrain = async () => {
    setStartingTrain(true);
    try {
      const r = await fetch(`${API}/tools/captcha/train/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skip_gen: skipGen }),
      });
      const d = await r.json() as { started?: boolean; error?: string };
      if (d.started) {
        setTrainPolling(true);
        await fetchTrainStatus();
      }
    } catch {}
    setStartingTrain(false);
  };

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const url = ev.target?.result as string;
      setImagePreview(url);
      const b64 = url.split(",")[1] ?? "";
      setImageB64(b64);
      setResult(null);
    };
    reader.readAsDataURL(file);
  };

  const recognize = async () => {
    if (!imageB64) return;
    setRecognizing(true);
    setResult(null);
    try {
      const r = await fetch(`${API}/tools/captcha/recognize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base64: imageB64 }),
      });
      const d = await r.json() as RecognizeResult;
      setResult(d);
    } catch (e) {
      setResult({ error: String(e) });
    }
    setRecognizing(false);
  };

  const modelReady = health?.model_ready ?? false;
  const isRunning = trainStatus?.running ?? false;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white">验证码识别</h2>
          <p className="text-sm text-gray-400 mt-1">
            基于 CNN + PyTorch 的数字验证码识别模型 &nbsp;·&nbsp;
            <a
              href="https://github.com/Leonhaoran/captcha_recognition"
              target="_blank" rel="noopener noreferrer"
              className="text-blue-400 hover:text-blue-300"
            >
              源码
            </a>
          </p>
        </div>
        <button
          onClick={fetchHealth}
          className="px-3 py-1.5 text-xs rounded-lg bg-[#21262d] hover:bg-[#30363d] text-gray-300 border border-[#30363d] transition-colors"
        >
          刷新状态
        </button>
      </div>

      {/* 状态栏 */}
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
        {/* 训练面板 */}
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-5 space-y-4">
          <h3 className="text-sm font-semibold text-white">模型训练</h3>
          <p className="text-xs text-gray-500">
            自动生成 2000 张训练 / 1000 张测试数字验证码图片，训练 CNN 模型（200 epoch）。
            首次约需 10–20 分钟（CPU），训练完成后模型自动保存到
            <code className="text-blue-400 ml-1">scripts/captcha_recognition/model/</code>
          </p>

          <label className="flex items-center gap-2 cursor-pointer select-none">
            <div
              onClick={() => setSkipGen(v => !v)}
              className={`relative w-10 h-5 rounded-full transition-colors ${skipGen ? "bg-blue-600" : "bg-gray-700"}`}
            >
              <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${skipGen ? "translate-x-5" : "translate-x-0.5"}`} />
            </div>
            <span className="text-sm text-gray-300">跳过数据生成（数据已存在时开启）</span>
          </label>

          <button
            onClick={startTrain}
            disabled={isRunning || startingTrain || !health?.ok}
            className={`w-full py-2.5 rounded-xl text-sm font-semibold transition-all ${
              isRunning || startingTrain
                ? "bg-blue-600/40 text-blue-300 cursor-not-allowed"
                : !health?.ok
                ? "bg-gray-700 text-gray-500 cursor-not-allowed"
                : "bg-blue-600 hover:bg-blue-500 text-white"
            }`}
          >
            {isRunning ? "⏳ 训练进行中…" : startingTrain ? "启动中…" : "🚀 启动训练"}
          </button>

          {/* 训练日志 */}
          {(trainStatus?.logs?.length ?? 0) > 0 && (
            <div className="bg-[#0d1117] rounded-lg p-3 h-48 overflow-y-auto font-mono text-xs space-y-0.5">
              {trainStatus!.logs.map((line, i) => (
                <div
                  key={i}
                  className={
                    line.includes("acc=") ? "text-emerald-400" :
                    line.includes("保存") ? "text-purple-400" :
                    line.includes("完成") ? "text-emerald-300 font-semibold" :
                    line.includes("Error") || line.includes("error") ? "text-red-400" :
                    "text-gray-400"
                  }
                >
                  {line}
                </div>
              ))}
              <div ref={logEndRef} />
            </div>
          )}
        </div>

        {/* 识别面板 */}
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-5 space-y-4">
          <h3 className="text-sm font-semibold text-white">验证码识别</h3>
          <p className="text-xs text-gray-500">上传验证码图片，返回识别文字和置信度。</p>

          {/* 上传区 */}
          <label className={`block w-full rounded-xl border-2 border-dashed transition-colors cursor-pointer ${
            modelReady ? "border-[#30363d] hover:border-blue-500" : "border-gray-700 cursor-not-allowed opacity-50"
          }`}>
            <input
              type="file"
              accept="image/*"
              className="hidden"
              onChange={onFileChange}
              disabled={!modelReady}
            />
            {imagePreview ? (
              <div className="p-4 flex flex-col items-center gap-3">
                <img
                  src={imagePreview}
                  alt="captcha preview"
                  className="max-h-24 rounded-lg border border-[#30363d] object-contain"
                />
                <span className="text-xs text-gray-500">点击更换图片</span>
              </div>
            ) : (
              <div className="py-10 flex flex-col items-center gap-2 text-gray-500">
                <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                    d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                </svg>
                <span className="text-sm">{modelReady ? "点击上传验证码图片" : "请先训练模型"}</span>
              </div>
            )}
          </label>

          <button
            onClick={recognize}
            disabled={!imageB64 || !modelReady || recognizing}
            className={`w-full py-2.5 rounded-xl text-sm font-semibold transition-all ${
              !imageB64 || !modelReady || recognizing
                ? "bg-emerald-600/30 text-emerald-400/50 cursor-not-allowed"
                : "bg-emerald-600 hover:bg-emerald-500 text-white"
            }`}
          >
            {recognizing ? "识别中…" : "🔍 开始识别"}
          </button>

          {/* 识别结果 */}
          {result && (
            <div className={`rounded-xl p-4 ${result.error ? "bg-red-500/10 border border-red-500/30" : "bg-emerald-500/10 border border-emerald-500/30"}`}>
              {result.error ? (
                <p className="text-red-400 text-sm">{result.error}</p>
              ) : (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-500">识别结果</span>
                    <span className="text-xs text-gray-500">
                      置信度 {((result.confidence ?? 0) * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div className="text-4xl font-mono font-bold text-emerald-400 text-center tracking-widest py-2">
                    {result.text}
                  </div>
                  <div className="w-full h-2 bg-gray-700 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-emerald-500 rounded-full transition-all"
                      style={{ width: `${(result.confidence ?? 0) * 100}%` }}
                    />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 接口说明 */}
      <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-5">
        <h3 className="text-sm font-semibold text-white mb-3">API 接口</h3>
        <div className="space-y-2 font-mono text-xs">
          {[
            { method: "GET",  path: "/api/tools/captcha/health",       desc: "服务状态 + 模型是否就绪" },
            { method: "POST", path: "/api/tools/captcha/train/start",   desc: 'body: { "skip_gen": false }  — 启动训练' },
            { method: "GET",  path: "/api/tools/captcha/train/status",  desc: "训练进度 + 日志流" },
            { method: "POST", path: "/api/tools/captcha/recognize",     desc: 'body: { "base64": "<base64_png>" }' },
          ].map(({ method, path, desc }) => (
            <div key={path} className="flex items-start gap-3 py-1.5 border-b border-[#21262d] last:border-0">
              <span className={`shrink-0 px-2 py-0.5 rounded text-xs font-bold ${method === "GET" ? "bg-blue-500/20 text-blue-400" : "bg-emerald-500/20 text-emerald-400"}`}>
                {method}
              </span>
              <span className="text-gray-300 flex-1">{path}</span>
              <span className="text-gray-600 hidden sm:block">{desc}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
