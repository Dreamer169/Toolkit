import { useState } from "react";

interface MachineIds {
  machineId: string;
  macMachineId: string;
  devDeviceId: string;
  sqmId: string;
}

interface ResetData {
  ids: MachineIds;
  paths: { windows: string; mac: string; linux: string };
  scripts: { windows: string; mac: string; linux: string };
  json_patch: Record<string, string>;
}

type OS = "windows" | "mac" | "linux";

export default function MachineReset() {
  const [data, setData] = useState<ResetData | null>(null);
  const [loading, setLoading] = useState(false);
  const [os, setOs] = useState<OS>("windows");
  const [copied, setCopied] = useState<string | null>(null);
  const [showScript, setShowScript] = useState(false);

  const generate = async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/tools/machine-id/generate");
      const d = await r.json();
      if (d.success) { setData(d); setShowScript(false); }
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  const copy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 1200);
  };

  const downloadScript = () => {
    if (!data) return;
    const ext = os === "windows" ? ".bat" : ".sh";
    const content = data.scripts[os];
    const blob = new Blob([content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `cursor_reset${ext}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const Btn = ({ id, label, icon }: { id: OS; label: string; icon: string }) => (
    <button
      onClick={() => setOs(id)}
      className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
        os === id
          ? "bg-blue-600 text-white"
          : "bg-[#21262d] text-gray-400 hover:text-white border border-[#30363d]"
      }`}
    >
      <span>{icon}</span>{label}
    </button>
  );

  const F = ({ label, value, k }: { label: string; value: string; k: string }) => (
    <div className="flex items-center gap-3 py-2 border-b border-[#21262d] last:border-0">
      <span className="text-xs text-gray-500 w-28 shrink-0">{label}</span>
      <span className="text-xs font-mono text-emerald-300 flex-1 break-all">{value}</span>
      <button
        onClick={() => copy(value, k)}
        className={`text-xs px-2 py-0.5 rounded border shrink-0 transition-all ${
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
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">Cursor 机器ID重置</h2>
        <p className="text-sm text-gray-400">
          参考 <span className="text-blue-400">cursor-free-vip</span> 实现，生成新机器ID并提供一键重置脚本，解决"Too many free trial accounts used on this machine"限制
        </p>
      </div>

      {/* 原理说明 */}
      <div className="bg-[#161b22] border border-yellow-500/20 rounded-xl p-4">
        <p className="text-xs font-semibold text-yellow-400 mb-2">工作原理</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs text-gray-400">
          <div>Cursor 将机器标识存储在 <code className="text-blue-300">storage.json</code> 中的 4 个字段</div>
          <div>重置这 4 个字段即可绕过试用账号的"同一机器限制"</div>
          <div><span className="text-gray-300">machineId / macMachineId</span>：SHA256 哈希（64位十六进制）</div>
          <div><span className="text-gray-300">devDeviceId</span>：UUID v4 &nbsp;|&nbsp; <span className="text-gray-300">sqmId</span>：花括号包裹的 UUID</div>
        </div>
      </div>

      {/* 生成按钮 */}
      <button
        onClick={generate}
        disabled={loading}
        className="w-full py-3 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-xl text-white font-medium text-sm transition-all"
      >
        {loading ? "生成中..." : "🔄 生成新机器ID"}
      </button>

      {data && (
        <>
          {/* ID 展示 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-gray-300">新机器ID</h3>
              <button
                onClick={() => copy(JSON.stringify(data.json_patch, null, 2), "json")}
                className={`text-xs px-3 py-1 rounded border transition-all ${
                  copied === "json"
                    ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400"
                    : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"
                }`}
              >
                {copied === "json" ? "✓ 已复制" : "复制 JSON"}
              </button>
            </div>
            <F label="telemetry.machineId"    value={data.ids.machineId}    k="mid" />
            <F label="telemetry.macMachineId" value={data.ids.macMachineId} k="mmid" />
            <F label="telemetry.devDeviceId"  value={data.ids.devDeviceId}  k="did" />
            <F label="telemetry.sqmId"        value={data.ids.sqmId}        k="sqm" />
          </div>

          {/* 配置文件路径 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
            <h3 className="text-xs font-semibold text-gray-400 mb-3">storage.json 路径</h3>
            <div className="space-y-2">
              {(["windows", "mac", "linux"] as OS[]).map((k) => (
                <div key={k} className="flex items-center gap-3">
                  <span className="text-xs text-gray-500 w-16 capitalize">{k}</span>
                  <code className="text-xs font-mono text-blue-300 flex-1 break-all">{data.paths[k]}</code>
                  <button
                    onClick={() => copy(data.paths[k], `path-${k}`)}
                    className={`text-xs px-2 py-0.5 rounded border shrink-0 transition-all ${
                      copied === `path-${k}`
                        ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400"
                        : "bg-[#21262d] border-[#30363d] text-gray-500 hover:text-white"
                    }`}
                  >
                    {copied === `path-${k}` ? "✓" : "复制"}
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* 一键脚本 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-gray-300">一键重置脚本</h3>
              <button
                onClick={() => setShowScript(!showScript)}
                className="text-xs text-gray-400 hover:text-white transition-colors"
              >
                {showScript ? "收起 ▲" : "展开查看 ▼"}
              </button>
            </div>

            <div className="flex flex-wrap gap-2 mb-4">
              <Btn id="windows" label="Windows (.bat)" icon="🪟" />
              <Btn id="mac"     label="macOS (.sh)"    icon="🍎" />
              <Btn id="linux"   label="Linux (.sh)"    icon="🐧" />
            </div>

            {showScript && (
              <pre className="bg-[#0d1117] rounded-lg p-4 text-xs font-mono text-gray-300 overflow-x-auto whitespace-pre max-h-64 overflow-y-auto mb-4">
                {data.scripts[os]}
              </pre>
            )}

            <div className="flex gap-3">
              <button
                onClick={downloadScript}
                className="flex-1 py-2.5 bg-emerald-600 hover:bg-emerald-700 rounded-xl text-white text-sm font-medium transition-all"
              >
                ⬇️ 下载 {os === "windows" ? "cursor_reset.bat" : os === "mac" ? "cursor_reset_mac.sh" : "cursor_reset_linux.sh"}
              </button>
              <button
                onClick={() => copy(data.scripts[os], "script")}
                className={`px-4 py-2.5 rounded-xl border text-sm transition-all ${
                  copied === "script"
                    ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400"
                    : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"
                }`}
              >
                {copied === "script" ? "✓ 已复制" : "复制脚本"}
              </button>
            </div>
          </div>

          {/* 使用步骤 */}
          <div className="bg-[#0d1117] border border-[#30363d] rounded-xl p-4">
            <p className="text-xs font-semibold text-gray-400 mb-3">使用步骤</p>
            <ol className="space-y-2 text-xs text-gray-400">
              <li className="flex gap-2"><span className="text-blue-400 font-bold">1.</span><span>选择你的操作系统，下载对应的重置脚本</span></li>
              <li className="flex gap-2"><span className="text-blue-400 font-bold">2.</span><span><strong className="text-gray-300">关闭 Cursor</strong>（脚本会自动强制终止进程）</span></li>
              <li className="flex gap-2"><span className="text-blue-400 font-bold">3.</span><span>以<strong className="text-gray-300">管理员权限</strong>运行脚本（Windows 右键 → 以管理员身份运行；Mac/Linux 执行 <code className="text-blue-300">chmod +x cursor_reset.sh && ./cursor_reset.sh</code>）</span></li>
              <li className="flex gap-2"><span className="text-blue-400 font-bold">4.</span><span>脚本会自动备份原 storage.json，写入新ID，重启 Cursor 即可使用新的试用账号</span></li>
            </ol>
          </div>
        </>
      )}
    </div>
  );
}
