import { useState, useEffect } from "react";

interface IpInfo {
  ip: string;
  city?: string;
  region?: string;
  country_name?: string;
  org?: string;
  timezone?: string;
  latitude?: number;
  longitude?: number;
}

export default function IpChecker() {
  const [myIp, setMyIp] = useState<IpInfo | null>(null);
  const [proxyInput, setProxyInput] = useState("");
  const [proxyResult, setProxyResult] = useState<{ loading: boolean; success?: boolean; info?: IpInfo; error?: string }>({ loading: false });
  const [loadingMyIp, setLoadingMyIp] = useState(true);

  useEffect(() => {
    fetch("/api/tools/ip-check")
      .then((r) => r.json())
      .then((d) => {
        if (d.success) setMyIp(d.info);
        setLoadingMyIp(false);
      })
      .catch(() => setLoadingMyIp(false));
  }, []);

  const checkProxy = async () => {
    if (!proxyInput.trim()) return;
    setProxyResult({ loading: true });
    try {
      const r = await fetch("/api/tools/proxy-check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proxy: proxyInput.trim() }),
      });
      const d = await r.json();
      if (d.success) {
        setProxyResult({ loading: false, success: true, info: d.info });
      } else {
        setProxyResult({ loading: false, success: false, error: d.error });
      }
    } catch (e) {
      setProxyResult({ loading: false, success: false, error: String(e) });
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">IP 查询 / 代理检测</h2>
        <p className="text-sm text-gray-400">
          查询当前服务器 IP 归属地，或检测代理 IP 是否可用及其地理位置
        </p>
      </div>

      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-6">
        <h3 className="text-sm font-semibold text-gray-300 mb-4">当前服务器 IP</h3>
        {loadingMyIp ? (
          <div className="flex items-center gap-2 text-gray-500 text-sm">
            <div className="w-3 h-3 border-2 border-gray-600 border-t-blue-400 rounded-full animate-spin" />
            查询中...
          </div>
        ) : myIp ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {[
              { label: "IP 地址", value: myIp.ip },
              { label: "城市", value: myIp.city },
              { label: "地区", value: myIp.region },
              { label: "国家", value: myIp.country_name },
              { label: "运营商", value: myIp.org },
              { label: "时区", value: myIp.timezone },
            ].map((item) =>
              item.value ? (
                <div key={item.label} className="bg-[#0d1117] rounded-lg p-3 border border-[#21262d]">
                  <p className="text-xs text-gray-500 mb-1">{item.label}</p>
                  <p className="text-sm text-white font-mono truncate">{item.value}</p>
                </div>
              ) : null
            )}
          </div>
        ) : (
          <p className="text-gray-500 text-sm">查询失败</p>
        )}
      </div>

      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-6 space-y-4">
        <h3 className="text-sm font-semibold text-gray-300">代理 IP 检测</h3>
        <p className="text-xs text-gray-500">输入代理地址检测是否可用（格式：http://host:port 或 http://user:pass@host:port）</p>
        <div className="flex gap-2">
          <input
            value={proxyInput}
            onChange={(e) => {
              setProxyInput(e.target.value);
              setProxyResult({ loading: false });
            }}
            placeholder="http://127.0.0.1:7890"
            className="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 font-mono"
          />
          <button
            onClick={checkProxy}
            disabled={proxyResult.loading || !proxyInput.trim()}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-white text-sm font-medium transition-all whitespace-nowrap"
          >
            {proxyResult.loading ? "检测中..." : "检测"}
          </button>
        </div>

        {!proxyResult.loading && proxyResult.success !== undefined && (
          <div className={`rounded-xl p-4 border ${proxyResult.success ? "bg-emerald-500/10 border-emerald-500/30" : "bg-red-500/10 border-red-500/30"}`}>
            <div className="flex items-center gap-2 mb-2">
              <span>{proxyResult.success ? "✅" : "❌"}</span>
              <span className={`font-semibold text-sm ${proxyResult.success ? "text-emerald-400" : "text-red-400"}`}>
                {proxyResult.success ? "代理可用" : "代理不可用"}
              </span>
            </div>
            {proxyResult.success && proxyResult.info && (
              <div className="grid grid-cols-2 gap-2">
                {[
                  { label: "代理 IP", value: proxyResult.info.ip },
                  { label: "城市", value: proxyResult.info.city },
                  { label: "国家", value: proxyResult.info.country_name },
                  { label: "运营商", value: proxyResult.info.org },
                ].map((item) =>
                  item.value ? (
                    <div key={item.label} className="text-xs">
                      <span className="text-gray-500">{item.label}: </span>
                      <span className="text-gray-200 font-mono">{item.value}</span>
                    </div>
                  ) : null
                )}
              </div>
            )}
            {!proxyResult.success && proxyResult.error && (
              <p className="text-sm text-red-300">{proxyResult.error}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
