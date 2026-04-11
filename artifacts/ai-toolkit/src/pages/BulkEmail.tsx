import { useState, useEffect } from "react";

interface Account {
  address: string;
  password: string;
  token: string;
  id: string;
}

export default function BulkEmail() {
  const [domain, setDomain] = useState("");
  const [domains, setDomains] = useState<string[]>([]);
  const [count, setCount] = useState(5);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/tools/email/domains")
      .then((r) => r.json())
      .then((d) => {
        if (d.domains?.length) {
          setDomains(d.domains);
          setDomain(d.domains[0]);
        }
      });
  }, []);

  const generate = async () => {
    setLoading(true);
    setAccounts([]);
    setProgress(0);
    const results: Account[] = [];
    const pass = () => Math.random().toString(36).slice(2, 14);
    const user = () => Math.random().toString(36).slice(2, 12) + Math.floor(Math.random() * 999);

    for (let i = 0; i < count; i++) {
      const address = `${user()}@${domain}`;
      const password = pass();
      try {
        const r = await fetch("/api/tools/email/create", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ address, password }),
        });
        const d = await r.json();
        if (d.success) {
          results.push({ address, password, token: d.token, id: d.account?.id ?? "" });
        }
      } catch {
        // skip failed
      }
      setProgress(i + 1);
      setAccounts([...results]);
      // small delay to avoid rate limiting
      await new Promise((res) => setTimeout(res, 300));
    }
    setLoading(false);
  };

  const exportTxt = (type: "addresses" | "credentials" | "tokens") => {
    let content = "";
    if (type === "addresses") content = accounts.map((a) => a.address).join("\n");
    else if (type === "credentials") content = accounts.map((a) => `${a.address}----${a.password}`).join("\n");
    else content = accounts.map((a) => a.token).join("\n");
    const blob = new Blob([content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `emails_${type}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const copyAll = (type: "addresses" | "credentials") => {
    let content = "";
    if (type === "addresses") content = accounts.map((a) => a.address).join("\n");
    else content = accounts.map((a) => `${a.address}----${a.password}`).join("\n");
    navigator.clipboard.writeText(content);
    setCopied(type);
    setTimeout(() => setCopied(null), 1500);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">批量邮箱生成</h2>
        <p className="text-sm text-gray-400">
          批量创建可接收邮件的真实临时邮箱账号，可导出地址、密码、Token
        </p>
      </div>

      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-6 space-y-5">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm text-gray-400 mb-2">邮箱域名</label>
            <select
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-300 focus:outline-none focus:border-blue-500"
            >
              {domains.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-2">生成数量（最多 20）</label>
            <input
              type="number"
              min={1}
              max={20}
              value={count}
              onChange={(e) => setCount(Math.min(20, Math.max(1, Number(e.target.value))))}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>

        <button
          onClick={generate}
          disabled={loading || !domain}
          className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-white font-medium text-sm transition-all"
        >
          {loading ? `生成中... (${progress}/${count})` : "开始批量生成"}
        </button>

        {loading && (
          <div className="w-full bg-[#21262d] rounded-full h-1.5">
            <div
              className="bg-blue-500 h-1.5 rounded-full transition-all"
              style={{ width: `${(progress / count) * 100}%` }}
            />
          </div>
        )}

        {accounts.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-300">
                已生成 {accounts.length} 个邮箱
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => copyAll("addresses")}
                  className={`text-xs px-2.5 py-1.5 rounded-lg border transition-all ${copied === "addresses" ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"}`}
                >
                  {copied === "addresses" ? "已复制" : "复制地址"}
                </button>
                <button
                  onClick={() => copyAll("credentials")}
                  className={`text-xs px-2.5 py-1.5 rounded-lg border transition-all ${copied === "credentials" ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"}`}
                >
                  {copied === "credentials" ? "已复制" : "复制账密"}
                </button>
                <button
                  onClick={() => exportTxt("credentials")}
                  className="text-xs px-2.5 py-1.5 rounded-lg border border-[#30363d] bg-[#21262d] text-gray-400 hover:text-white transition-all"
                >
                  导出
                </button>
              </div>
            </div>
            <div className="rounded-xl border border-[#21262d] overflow-hidden">
              <div className="grid grid-cols-3 px-4 py-2 bg-[#0d1117] text-xs text-gray-500 border-b border-[#21262d]">
                <span>邮箱地址</span>
                <span>密码</span>
                <span>状态</span>
              </div>
              <div className="max-h-72 overflow-y-auto divide-y divide-[#21262d]">
                {accounts.map((acc, i) => (
                  <div key={i} className="grid grid-cols-3 px-4 py-2.5 text-xs hover:bg-[#1c2128]">
                    <span className="font-mono text-gray-300 truncate pr-2">{acc.address}</span>
                    <span className="font-mono text-gray-400 truncate pr-2">{acc.password}</span>
                    <span className="text-emerald-400">✓ 可用</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="flex gap-2">
              <button onClick={() => exportTxt("addresses")} className="text-xs px-3 py-1.5 rounded-lg border border-[#30363d] bg-[#21262d] text-gray-400 hover:text-white transition-all">
                导出地址.txt
              </button>
              <button onClick={() => exportTxt("tokens")} className="text-xs px-3 py-1.5 rounded-lg border border-[#30363d] bg-[#21262d] text-gray-400 hover:text-white transition-all">
                导出 Token.txt
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
