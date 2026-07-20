import { useState, useEffect, useRef } from "react";

interface Account {
  address: string;
  password: string;
  token: string;
  id: string;
}

type ExportFormat = "address" | "credential" | "token" | "json" | "csv";

export default function BulkEmail() {
  const [domain, setDomain]     = useState("");
  const [domains, setDomains]   = useState<string[]>([]);
  const [count, setCount]       = useState(5);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading]   = useState(false);
  const [progress, setProgress] = useState(0);
  const [copied, setCopied]     = useState<string | null>(null);
  const [inboxes, setInboxes]   = useState<Record<string, Array<{ subject: string; intro: string }>>>({});
  const [pollActive, setPollActive] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    fetch("/api/tools/email/domains")
      .then((r) => r.json())
      .then((d) => { if (d.domains?.length) { setDomains(d.domains); setDomain(d.domains[0]); } });
  }, []);

  const [useHumanName, setUseHumanName] = useState(true);
  const [preview, setPreview] = useState<Array<{username:string;firstName:string;lastName:string;pattern:string}>>([]);

  const loadPreview = async () => {
    const r = await fetch(`/api/tools/email/gen-username?count=${count}`);
    const d = await r.json();
    if (d.success) setPreview(d.usernames);
  };

  const generate = async () => {
    setLoading(true);
    setAccounts([]);
    setInboxes({});
    setProgress(0);
    if (pollRef.current) clearInterval(pollRef.current);

    const results: Account[] = [];

    // 预先获取所有人名用户名
    let humanNames: Array<{username:string;password:string}> = [];
    if (useHumanName) {
      const r = await fetch(`/api/tools/email/gen-username?count=${count}`);
      const d = await r.json();
      if (d.success) humanNames = d.usernames;
    }

    for (let i = 0; i < count; i++) {
      let username: string, password: string;
      if (useHumanName && humanNames[i]) {
        username = humanNames[i].username;
        password = humanNames[i].password;
      } else {
        username = Math.random().toString(36).slice(2, 12) + Math.floor(Math.random() * 999);
        password = Math.random().toString(36).slice(2, 14);
      }
      const address  = `${username}@${domain}`;
      try {
        const r = await fetch("/api/tools/email/create", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ address, password }),
        });
        const d = await r.json();
        if (d.success) results.push({ address, password, token: d.token ?? "", id: d.account?.id ?? "" });
      } catch { /* skip */ }
      setProgress(i + 1);
      setAccounts([...results]);
      await new Promise((res) => setTimeout(res, 400));
    }
    setLoading(false);
    if (results.length > 0) startPolling(results);
  };

  const startPolling = (accs: Account[]) => {
    setPollActive(true);
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const newInboxes: typeof inboxes = {};
      await Promise.allSettled(accs.slice(0, 10).map(async (acc) => {
        if (!acc.token) return;
        try {
          const r = await fetch("/api/tools/email/messages", { headers: { "x-mail-token": acc.token } });
          const d = await r.json();
          if (d.success) newInboxes[acc.address] = d.messages ?? [];
        } catch {}
      }));
      setInboxes((prev) => ({ ...prev, ...newInboxes }));
    }, 6000);
  };

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const copy = (text: string, k: string) => {
    navigator.clipboard.writeText(text);
    setCopied(k);
    setTimeout(() => setCopied(null), 1200);
  };

  const exportData = (fmt: ExportFormat) => {
    let content = "";
    let filename = "emails";
    if (fmt === "address")    { content = accounts.map((a) => a.address).join("\n"); filename = "addresses.txt"; }
    else if (fmt === "credential") { content = accounts.map((a) => `${a.address}----${a.password}`).join("\n"); filename = "credentials.txt"; }
    else if (fmt === "token")  { content = accounts.map((a) => a.token).join("\n"); filename = "tokens.txt"; }
    else if (fmt === "json")   { content = JSON.stringify(accounts, null, 2); filename = "accounts.json"; }
    else if (fmt === "csv")   {
      content = "address,password,token,id\n" + accounts.map((a) => `${a.address},${a.password},${a.token},${a.id}`).join("\n");
      filename = "accounts.csv";
    }
    const blob = new Blob([content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const el = document.createElement("a");
    el.href = url; el.download = filename; el.click();
    URL.revokeObjectURL(url);
  };

  const extractCode = (text: string) => text?.match(/\b(\d{6})\b/)?.[1] ?? null;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">批量 MailTM 邮箱生成</h2>
        <p className="text-sm text-gray-400">
          使用 <span className="text-blue-400">MailTM 免费 API</span> 批量创建临时邮箱，实时监听收件箱，导出地址 / 密码 / Token / CSV / JSON
        </p>
      </div>

      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-gray-400 mb-1.5">邮箱域名</label>
            <select value={domain} onChange={(e) => setDomain(e.target.value)}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-300 focus:outline-none focus:border-blue-500">
              {domains.map((d) => <option key={d}>{d}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1.5">生成数量（最多 20）</label>
            <input type="number" min={1} max={20} value={count}
              onChange={(e) => setCount(Math.min(20, Math.max(1, Number(e.target.value))))}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500" />
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => { setUseHumanName(!useHumanName); setPreview([]); }}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm transition-all ${useHumanName ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}
          >
            <span className={`w-2 h-2 rounded-full ${useHumanName ? "bg-emerald-400" : "bg-gray-600"}`} />
            👤 真实人名
          </button>
          {useHumanName && (
            <button onClick={loadPreview} className="text-xs px-3 py-2 rounded-lg border border-[#30363d] bg-[#21262d] text-gray-400 hover:text-white transition-all">
              预览名字
            </button>
          )}
          <span className="text-xs text-gray-500 flex-1">
            {useHumanName ? "生成 JohnSmith88 / j.davis92 等真实人名格式，大幅提升注册成功率" : "使用随机字符串"}
          </span>
        </div>

        {useHumanName && preview.length > 0 && (
          <div className="bg-[#0d1117] rounded-lg p-3 space-y-1 max-h-32 overflow-y-auto">
            <p className="text-xs text-gray-500 mb-2">名字预览（实际生成时刷新）：</p>
            {preview.slice(0, 6).map((p, i) => (
              <div key={i} className="flex items-center gap-3 text-xs">
                <span className="font-mono text-blue-300 w-36 truncate">{p.username}@<span className="text-gray-500">{domain}</span></span>
                <span className="text-gray-500">{p.firstName} {p.lastName}</span>
                <span className="text-gray-600 text-[10px]">{p.pattern}</span>
              </div>
            ))}
          </div>
        )}

        <button onClick={generate} disabled={loading || !domain}
          className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-white font-medium text-sm transition-all">
          {loading ? `生成中... (${progress}/${count})` : "⚡ 开始批量生成"}
        </button>

        {loading && (
          <div className="w-full bg-[#21262d] rounded-full h-1.5">
            <div className="bg-blue-500 h-1.5 rounded-full transition-all" style={{ width: `${(progress / count) * 100}%` }} />
          </div>
        )}
      </div>

      {accounts.length > 0 && (
        <div className="space-y-4">
          {/* 导出工具栏 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-gray-300">已创建 {accounts.length} 个邮箱</span>
                {pollActive && (
                  <span className="flex items-center gap-1 text-xs text-emerald-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />实时监听中
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5">
                <button onClick={() => copy(accounts.map((a) => a.address).join("\n"), "cp-addr")}
                  className={`text-xs px-2.5 py-1 rounded border transition-all ${copied === "cp-addr" ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"}`}>
                  {copied === "cp-addr" ? "✓" : "复制地址"}
                </button>
                <button onClick={() => copy(accounts.map((a) => `${a.address}----${a.password}`).join("\n"), "cp-cred")}
                  className={`text-xs px-2.5 py-1 rounded border transition-all ${copied === "cp-cred" ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"}`}>
                  {copied === "cp-cred" ? "✓" : "复制账密"}
                </button>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {([
                ["address",    "📄 地址.txt"],
                ["credential", "🔐 账密.txt"],
                ["token",      "🎟 Token.txt"],
                ["csv",        "📊 全量.csv"],
                ["json",       "🗂 全量.json"],
              ] as [ExportFormat, string][]).map(([fmt, label]) => (
                <button key={fmt} onClick={() => exportData(fmt)}
                  className="text-xs px-3 py-1 rounded border border-[#30363d] bg-[#21262d] text-gray-400 hover:text-white hover:border-blue-500/50 transition-all">
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* 账号表格 */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
            <div className="grid grid-cols-[2fr_1.2fr_1fr_auto] px-4 py-2 bg-[#0d1117] text-xs text-gray-500 border-b border-[#21262d]">
              <span>邮箱地址</span>
              <span>密码</span>
              <span>Token</span>
              <span>状态</span>
            </div>
            <div className="max-h-64 overflow-y-auto divide-y divide-[#21262d]">
              {accounts.map((acc, i) => {
                const msgs = inboxes[acc.address] ?? [];
                const code = msgs.length > 0 ? extractCode(msgs[0]?.intro ?? msgs[0]?.subject ?? "") : null;
                return (
                  <div key={i}>
                    <div className="grid grid-cols-[2fr_1.2fr_1fr_auto] px-4 py-2.5 text-xs hover:bg-[#1c2128] group">
                      <span className="font-mono text-gray-300 truncate pr-2 flex items-center gap-1">
                        {acc.address}
                        <button onClick={() => copy(acc.address, `addr-${i}`)} className={`opacity-0 group-hover:opacity-100 px-1 py-0.5 rounded text-[10px] border transition-all ${copied === `addr-${i}` ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}>
                          {copied === `addr-${i}` ? "✓" : "复制"}
                        </button>
                      </span>
                      <span className="font-mono text-gray-400 truncate pr-2 flex items-center gap-1">
                        {acc.password}
                        <button onClick={() => copy(acc.password, `pass-${i}`)} className={`opacity-0 group-hover:opacity-100 px-1 py-0.5 rounded text-[10px] border transition-all ${copied === `pass-${i}` ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}>
                          {copied === `pass-${i}` ? "✓" : "复制"}
                        </button>
                      </span>
                      <span className="font-mono text-gray-500 truncate pr-2 flex items-center gap-1 text-[10px]">
                        {acc.token.slice(0, 16)}…
                        <button onClick={() => copy(acc.token, `tok-${i}`)} className={`opacity-0 group-hover:opacity-100 px-1 py-0.5 rounded text-[10px] border transition-all ${copied === `tok-${i}` ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500"}`}>
                          {copied === `tok-${i}` ? "✓" : "复制"}
                        </button>
                      </span>
                      <div className="flex items-center gap-2">
                        <span className="text-emerald-400">✓ 可用</span>
                        {code && (
                          <button onClick={() => copy(code, `code-${i}`)} className={`text-[10px] px-1.5 py-0.5 rounded border font-mono font-bold ${copied === `code-${i}` ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-yellow-500/10 border-yellow-500/30 text-yellow-400"}`}>
                            {copied === `code-${i}` ? "✓" : `验证码:${code}`}
                          </button>
                        )}
                        {msgs.length > 0 && !code && (
                          <span className="text-[10px] text-blue-400">{msgs.length}封新邮件</span>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
