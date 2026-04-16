import { useState, useEffect, useCallback } from "react";

const API = import.meta.env.BASE_URL.replace(/\/$/, "") + "/api";

type Platform = "outlook" | "chatgpt" | "claude" | "gemini" | "cursor" | "grok" | "codex" | "other";
type Tab = "accounts" | "archives" | "emails" | "configs" | "stats" | "guide";

interface Account {
  id: number; platform: string; email: string; password: string;
  username?: string; token?: string; refresh_token?: string; status: string; notes?: string;
  created_at: string;
}
interface Identity {
  id: number; full_name: string; first_name: string; last_name: string;
  gender: string; birthday?: string; phone?: string; email?: string;
  address?: string; city?: string; state?: string; zip?: string;
  country?: string; username?: string; password?: string; created_at: string;
}
interface Archive {
  id: number; platform: string; email: string; password?: string;
  username?: string; token?: string; refresh_token?: string;
  machine_id?: string; fingerprint?: Record<string, unknown>;
  proxy_used?: string; identity_data?: Record<string, unknown>;
  cookies?: Record<string, unknown>; registration_email?: string;
  status: string; notes?: string; created_at: string; updated_at: string;
}
interface TempEmail {
  id: number; address: string; password: string; provider: string;
  token?: string; status: string; notes?: string; created_at: string;
}
interface Config { id: number; key: string; value: string; description?: string; }
interface Stats {
  accounts: { total: number; active: number };
  archives: { total: number };
  emails: { total: number };
  long_term: { total: number };
  proxies: { idle: number; active: number; banned: number };
  byPlatform: { platform: string; count: number }[];
}

const PLATFORM_COLORS: Record<string, string> = {
  outlook: "text-blue-400", chatgpt: "text-emerald-400", claude: "text-amber-400",
  gemini: "text-purple-400", cursor: "text-cyan-400", grok: "text-pink-400",
  codex: "text-orange-400", other: "text-gray-400",
};
const PLATFORMS: Platform[] = ["chatgpt","claude","gemini","cursor","grok","codex","other"];
const AI_PLATFORMS: Platform[] = ["chatgpt","claude","gemini","cursor","grok","codex","other"];

function formatDate(s: string) {
  return new Date(s).toLocaleString("zh-CN", { year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit" });
}

// ─── Stats ──────────────────────────────────────────────────────────────────
function StatsPanel() {
  const [stats, setStats] = useState<Stats | null>(null);
  useEffect(() => {
    fetch(`${API}/data/stats`).then(r => r.json()).then(d => d.success && setStats(d)).catch(() => {});
  }, []);
  if (!stats) return <p className="text-gray-500 text-center py-12">加载中…</p>;
  const proxyTotal = (stats.proxies?.idle ?? 0) + (stats.proxies?.active ?? 0) + (stats.proxies?.banned ?? 0);
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label:"AI服务池", value: stats.accounts.total, sub:`${stats.accounts.active} 个有效`, color:"text-blue-400" },
          { label:"有效账号", value: stats.accounts.active, sub:`共 ${stats.accounts.total} 个`, color:"text-emerald-400" },
          { label:"档案库", value: stats.archives?.total ?? 0, sub:"条完整记录", color:"text-amber-400" },
          { label:"邮箱库", value: stats.emails.total, sub:`${stats.long_term.total} 长期邮箱`, color:"text-purple-400" },
          { label:"代理池", value: proxyTotal, sub:`${stats.proxies?.idle ?? 0} 可用 · ${stats.proxies?.banned ?? 0} 封禁`, color:"text-cyan-400" },
        ].map(({ label, value, sub, color }) => (
          <div key={label} className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 text-center">
            <div className={`text-3xl font-bold ${color}`}>{value}</div>
            <div className="text-sm text-gray-400 mt-1">{label}</div>
            <div className="text-xs text-gray-600 mt-0.5">{sub}</div>
          </div>
        ))}
      </div>
      {stats.byPlatform.length > 0 && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-400 mb-3">按平台分布</h3>
          <div className="space-y-2">
            {stats.byPlatform.map(({ platform, count }) => {
              const pct = stats.accounts.total ? Math.round(count / stats.accounts.total * 100) : 0;
              return (
                <div key={platform} className="flex items-center gap-3">
                  <span className={`text-xs w-16 ${PLATFORM_COLORS[platform] ?? "text-gray-400"}`}>{platform}</span>
                  <div className="flex-1 h-2 bg-[#0d1117] rounded-full overflow-hidden">
                    <div className="h-full bg-emerald-600 rounded-full" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="text-xs text-gray-400 w-10 text-right">{count}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Accounts ───────────────────────────────────────────────────────────────
function AccountsPanel() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [filter, setFilter] = useState<{ platform: string; status: string; search: string }>({ platform: "", status: "", search: "" });
  const [showAdd, setShowAdd] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [importText, setImportText] = useState("");
  const [importPlatform, setImportPlatform] = useState<Platform>("chatgpt");
  const [importDelimiter, setImportDelimiter] = useState("----");
  const [form, setForm] = useState({ platform: "chatgpt", email: "", password: "", username: "", token: "", status: "active", notes: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [copied, setCopied] = useState<string>("");
  const [archiveDetail, setArchiveDetail] = useState<{ email: string; platform: string } | null>(null);

  function copy(text: string, key: string) {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(key);
      setTimeout(() => setCopied(k => k === key ? "" : k), 1500);
    });
  }

  const load = useCallback(async () => {
    const q = new URLSearchParams();
    if (filter.platform) q.set("platform", filter.platform);
    else q.set("exclude_platform", "outlook");
    if (filter.status)   q.set("status",   filter.status);
    if (filter.search)   q.set("search",   filter.search);
    const d = await fetch(`${API}/data/accounts?${q}`).then(r => r.json()).catch(() => ({}));
    if (d.success) setAccounts(d.data);
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  async function addAccount() {
    if (!form.email || !form.password) { setMsg("email 和 password 必填"); return; }
    setBusy(true); setMsg("");
    const d = await fetch(`${API}/data/accounts`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(form) }).then(r=>r.json()).catch(()=>({}));
    setBusy(false);
    if (d.success) { setMsg("✅ 添加成功"); setShowAdd(false); setForm({...form,email:"",password:"",username:"",token:"",notes:""}); load(); }
    else setMsg("❌ " + (d.error || "失败"));
  }

  async function deleteAccount(id: number) {
    if (!confirm("确认删除？")) return;
    await fetch(`${API}/data/accounts/${id}`, { method:"DELETE" }).then(r=>r.json()).catch(()=>{});
    load();
  }

  async function doImport() {
    if (!importText.trim()) return;
    setBusy(true); setMsg("");
    const d = await fetch(`${API}/data/accounts/import`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ text: importText, platform: importPlatform, delimiter: importDelimiter }) }).then(r=>r.json()).catch(()=>({}));
    setBusy(false);
    if (d.success) { setMsg(`✅ 导入 ${d.inserted}/${d.total} 条`); setShowImport(false); setImportText(""); load(); }
    else setMsg("❌ " + (d.error || "失败"));
  }

  function exportAccounts(format: string) {
    const q = new URLSearchParams({ format });
    if (filter.platform) q.set("platform", filter.platform);
    window.open(`${API}/data/accounts/export?${q}`);
  }


  return (
    <div className="space-y-4">
      {/* 工具栏 */}
      <div className="flex flex-wrap gap-2 items-center">
        <select value={filter.platform} onChange={e => setFilter(f => ({...f,platform:e.target.value}))} className="bg-[#161b22] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white">
          <option value="">全部平台</option>
          {AI_PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={filter.status} onChange={e => setFilter(f => ({...f,status:e.target.value}))} className="bg-[#161b22] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white">
          <option value="">全部状态</option>
          <option value="active">有效</option>
          <option value="inactive">已失效</option>
          <option value="banned">已封禁</option>
        </select>
        <input value={filter.search} onChange={e => setFilter(f => ({...f,search:e.target.value}))} placeholder="搜索 email/备注…" className="bg-[#161b22] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white placeholder-gray-600 flex-1 min-w-32" />
        <div className="flex gap-1 ml-auto flex-wrap">
          <button onClick={load} title="刷新数据" className="px-3 py-1.5 bg-[#21262d] border border-[#30363d] rounded text-xs text-gray-300 hover:bg-[#30363d] hover:text-white">🔄 刷新</button>
          <button onClick={() => exportAccounts("txt")} className="px-3 py-1.5 bg-[#21262d] border border-[#30363d] rounded text-xs text-gray-300 hover:bg-[#30363d]">导出 TXT</button>
          <button onClick={() => exportAccounts("csv")} className="px-3 py-1.5 bg-[#21262d] border border-[#30363d] rounded text-xs text-gray-300 hover:bg-[#30363d]">导出 CSV</button>
          <button onClick={() => exportAccounts("json")} className="px-3 py-1.5 bg-[#21262d] border border-[#30363d] rounded text-xs text-gray-300 hover:bg-[#30363d]">导出 JSON</button>
          <button onClick={() => setShowImport(true)} className="px-3 py-1.5 bg-[#1f6feb] rounded text-xs text-white hover:bg-blue-600">批量导入</button>
          <button onClick={() => setShowAdd(true)} className="px-3 py-1.5 bg-emerald-700 rounded text-xs text-white hover:bg-emerald-600">+ 添加账号</button>
        </div>
      </div>

      {msg && <p className={`text-sm px-3 py-2 rounded ${msg.startsWith("✅") ? "bg-emerald-900/40 text-emerald-300" : msg.startsWith("⚠") ? "bg-yellow-900/30 text-yellow-300" : "bg-red-900/40 text-red-300"}`}>{msg}</p>}

      {/* 添加弹窗 */}
      {showAdd && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 space-y-3">
          <h3 className="text-sm font-semibold text-white">添加账号</h3>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-gray-400">平台</label>
              <select value={form.platform} onChange={e => setForm(f=>({...f,platform:e.target.value}))} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1">
                {AI_PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-400">状态</label>
              <select value={form.status} onChange={e => setForm(f=>({...f,status:e.target.value}))} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1">
                <option value="active">有效</option>
                <option value="inactive">已失效</option>
                <option value="banned">已封禁</option>
              </select>
            </div>
            {(["email","password","username","token","notes"] as const).map(k => (
              <div key={k} className={k === "notes" || k === "token" ? "col-span-2" : ""}>
                <label className="text-xs text-gray-400">{k === "notes" ? "备注" : k}</label>
                <input value={form[k]} onChange={e => setForm(f=>({...f,[k]:e.target.value}))} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1" placeholder={k === "token" ? "可选" : ""} />
              </div>
            ))}
          </div>
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowAdd(false)} className="px-3 py-1.5 text-xs text-gray-400 hover:text-white">取消</button>
            <button onClick={addAccount} disabled={busy} className="px-4 py-1.5 bg-emerald-700 rounded text-xs text-white hover:bg-emerald-600 disabled:opacity-50">保存</button>
          </div>
        </div>
      )}

      {/* 导入弹窗 */}
      {showImport && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 space-y-3">
          <h3 className="text-sm font-semibold text-white">批量导入账号</h3>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-gray-400">平台</label>
              <select value={importPlatform} onChange={e => setImportPlatform(e.target.value as Platform)} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1">
                {PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-400">分隔符</label>
              <input value={importDelimiter} onChange={e => setImportDelimiter(e.target.value)} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1" placeholder="默认 ----" />
            </div>
          </div>
          <div>
            <label className="text-xs text-gray-400">账号列表（每行一个：email{importDelimiter}password{importDelimiter}token可选）</label>
            <textarea value={importText} onChange={e => setImportText(e.target.value)} rows={8} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1 font-mono resize-none" placeholder={`user@outlook.com${importDelimiter}password123\nanother@outlook.com${importDelimiter}pass456`} />
          </div>
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowImport(false)} className="px-3 py-1.5 text-xs text-gray-400 hover:text-white">取消</button>
            <button onClick={doImport} disabled={busy} className="px-4 py-1.5 bg-blue-700 rounded text-xs text-white hover:bg-blue-600 disabled:opacity-50">导入</button>
          </div>
        </div>
      )}

      {/* 账号列表 */}
      <div className="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
        <div className="grid grid-cols-[60px_1fr_1fr_80px_50px_80px_44px] gap-2 px-3 py-2 bg-[#21262d] text-xs text-gray-500 font-medium">
          <span>平台</span><span>邮箱</span><span>密码</span><span>Token</span><span>状态</span><span>创建</span><span></span>
        </div>
        {accounts.length === 0 && (
          <p className="text-center text-gray-600 text-sm py-8">暂无账号，点击「添加账号」或「批量导入」</p>
        )}
        {accounts.map(a => {
          const emailKey = `email-${a.id}`;
          const pwKey    = `pw-${a.id}`;
          const tokKey   = `tok-${a.id}`;
          const emailCopied = copied === emailKey;
          const pwCopied    = copied === pwKey;
          const tokCopied   = copied === tokKey;
          const pw  = a.password || "";
          const tok = a.token || "";
          return (
            <div key={a.id} className="border-t border-[#21262d] text-xs hover:bg-[#21262d]/50 group">
              <div className="grid grid-cols-[60px_1fr_1fr_80px_50px_80px_44px] gap-2 px-3 py-2 items-center">
                <span className={`font-medium ${PLATFORM_COLORS[a.platform] ?? "text-gray-400"}`}>{a.platform}</span>
                <button onClick={() => copy(a.email, emailKey)} title={a.email}
                  className={`text-left font-mono truncate transition-colors ${emailCopied ? "text-emerald-400" : "text-white hover:text-blue-300"}`}>
                  {emailCopied ? "✓ 已复制" : a.email}
                </button>
                <button onClick={() => copy(pw, pwKey)} title={pw}
                  className={`text-left font-mono truncate transition-colors ${pwCopied ? "text-emerald-400" : "text-gray-300 hover:text-blue-300"}`}>
                  {pwCopied ? "✓ 已复制" : (pw || <span className="text-gray-600 italic">—</span>)}
                </button>
                {tok ? (
                  <button onClick={() => copy(tok, tokKey)} title={tok}
                    className={`text-left font-mono truncate transition-colors ${tokCopied ? "text-emerald-400" : "text-blue-400 hover:text-blue-300"}`}>
                    {tokCopied ? "✓ 已复制" : tok.slice(0, 10) + "…"}
                  </button>
                ) : <span className="text-gray-700">—</span>}
                <span className={`px-1.5 py-0.5 rounded-full text-center w-fit ${a.status === "active" ? "bg-emerald-900/40 text-emerald-400" : a.status === "banned" ? "bg-red-900/40 text-red-400" : "bg-gray-800 text-gray-500"}`}>
                  {a.status === "active" ? "有效" : a.status === "banned" ? "封禁" : "失效"}
                </span>
                <span className="text-gray-600">{formatDate(a.created_at).split(" ")[0]}</span>
                <div className="flex items-center gap-1.5">
                  <button onClick={() => setArchiveDetail({ email: a.email, platform: a.platform })}
                    className="text-blue-500 hover:text-blue-300 text-xs opacity-0 group-hover:opacity-100 transition-opacity">详细</button>
                  <button onClick={() => deleteAccount(a.id)} className="text-red-600 hover:text-red-400 text-xs opacity-0 group-hover:opacity-100 transition-opacity">删</button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-gray-600 text-right">共 {accounts.length} 条</p>
      {archiveDetail && <ArchiveDetailModal email={archiveDetail.email} platform={archiveDetail.platform} onClose={() => setArchiveDetail(null)} />}
    </div>
  );
}

// ─── Identities ─────────────────────────────────────────────────────────────
function IdentitiesPanel() {
  const [identities, setIdentities] = useState<Identity[]>([]);
  const [search, setSearch] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ first_name:"", last_name:"", gender:"Male", birthday:"", phone:"", email:"", address:"", city:"", state:"", zip:"", country:"United States", username:"", password:"" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    const q = search ? `?search=${encodeURIComponent(search)}` : "";
    const d = await fetch(`${API}/data/identities${q}`).then(r => r.json()).catch(() => ({}));
    if (d.success) setIdentities(d.data);
  }, [search]);

  useEffect(() => { load(); }, [load]);

  async function addIdentity() {
    setBusy(true); setMsg("");
    const d = await fetch(`${API}/data/identities`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(form) }).then(r=>r.json()).catch(()=>({}));
    setBusy(false);
    if (d.success) { setMsg("✅ 已保存"); setShowAdd(false); setForm({...form,first_name:"",last_name:"",phone:"",email:"",address:"",city:"",state:"",zip:"",username:"",password:"",birthday:""}); load(); }
    else setMsg("❌ " + (d.error || "失败"));
  }

  async function deleteIdentity(id: number) {
    if (!confirm("确认删除？")) return;
    await fetch(`${API}/data/identities/${id}`, { method:"DELETE" }).then(r=>r.json()).catch(()=>{});
    load();
  }

  function exportIdentities() {
    const text = identities.map(i =>
      [i.full_name, i.gender, i.birthday, i.phone, i.email, i.address, i.city, i.state, i.zip, i.country, i.username, i.password].join(",")
    ).join("\n");
    const blob = new Blob(["full_name,gender,birthday,phone,email,address,city,state,zip,country,username,password\n" + text], { type:"text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "identities.csv"; a.click();
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索姓名/邮箱/用户名…" className="flex-1 bg-[#161b22] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white placeholder-gray-600" />
        <button onClick={exportIdentities} className="px-3 py-1.5 bg-[#21262d] border border-[#30363d] rounded text-xs text-gray-300 hover:bg-[#30363d]">导出 CSV</button>
        <button onClick={() => setShowAdd(true)} className="px-3 py-1.5 bg-emerald-700 rounded text-xs text-white hover:bg-emerald-600">+ 添加身份</button>
      </div>

      {msg && <p className={`text-sm px-3 py-2 rounded ${msg.startsWith("✅") ? "bg-emerald-900/40 text-emerald-300" : msg.startsWith("⚠") ? "bg-yellow-900/30 text-yellow-300" : "bg-red-900/40 text-red-300"}`}>{msg}</p>}

      {showAdd && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 space-y-3">
          <h3 className="text-sm font-semibold text-white">添加身份信息</h3>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
            {(["first_name","last_name","gender","birthday","phone","email","address","city","state","zip","country","username","password"] as const).map(k => (
              <div key={k} className={k === "address" ? "col-span-2 md:col-span-3" : ""}>
                <label className="text-xs text-gray-400">{({ first_name:"名",last_name:"姓",gender:"性别",birthday:"生日",phone:"手机",email:"邮箱",address:"地址",city:"城市",state:"州",zip:"邮编",country:"国家",username:"用户名",password:"密码" })[k]}</label>
                {k === "gender" ? (
                  <select value={form.gender} onChange={e => setForm(f=>({...f,gender:e.target.value}))} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1">
                    <option>Male</option><option>Female</option>
                  </select>
                ) : (
                  <input value={form[k]} onChange={e => setForm(f=>({...f,[k]:e.target.value}))} type={k === "birthday" ? "date" : "text"} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1" />
                )}
              </div>
            ))}
          </div>
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowAdd(false)} className="px-3 py-1.5 text-xs text-gray-400 hover:text-white">取消</button>
            <button onClick={addIdentity} disabled={busy} className="px-4 py-1.5 bg-emerald-700 rounded text-xs text-white hover:bg-emerald-600 disabled:opacity-50">保存</button>
          </div>
        </div>
      )}

      <div className="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
        <div className="grid grid-cols-[1fr_60px_1fr_1fr_100px_60px] gap-2 px-3 py-2 bg-[#21262d] text-xs text-gray-500 font-medium">
          <span>姓名</span><span>性别</span><span>手机 / 邮箱</span><span>地址</span><span>用户名/密码</span><span></span>
        </div>
        {identities.length === 0 && <p className="text-center text-gray-600 text-sm py-8">暂无身份信息</p>}
        {identities.map(i => (
          <div key={i.id} className="grid grid-cols-[1fr_60px_1fr_1fr_100px_60px] gap-2 px-3 py-2 border-t border-[#21262d] text-xs hover:bg-[#21262d]/50 group items-center">
            <span className="text-white">{i.full_name}</span>
            <span className="text-gray-500">{i.gender === "Male" ? "男" : "女"}</span>
            <div><div className="text-gray-300">{i.phone}</div><div className="text-gray-500 truncate">{i.email}</div></div>
            <span className="text-gray-400 truncate">{[i.city, i.state, i.country].filter(Boolean).join(", ")}</span>
            <div><div className="text-gray-300 font-mono">{i.username}</div><div className="text-gray-500 font-mono">{i.password}</div></div>
            <button onClick={() => deleteIdentity(i.id)} className="text-red-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity text-xs">删除</button>
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-600 text-right">共 {identities.length} 条</p>
    </div>
  );
}


// ─── 邮箱库（临时邮箱）────────────────────────────────────────────────────────
function EmailsPanel() {
  const [emails, setEmails] = useState<TempEmail[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ address:"", password:"", provider:"mailtm", token:"", notes:"" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [copied, setCopied] = useState<string>("");
  const [archiveDetail, setArchiveDetail] = useState<{ email: string; platform: string } | null>(null);

  function copy(text: string, key: string) {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(key);
      setTimeout(() => setCopied(k => k === key ? "" : k), 1500);
    });
  }

  const load = useCallback(async () => {
    const d = await fetch(`${API}/data/emails`).then(r => r.json()).catch(() => ({}));
    if (d.success) setEmails(d.data);
  }, []);

  useEffect(() => { load(); }, [load]);

  async function addEmail() {
    setBusy(true); setMsg("");
    const d = await fetch(`${API}/data/emails`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(form) }).then(r=>r.json()).catch(()=>({}));
    setBusy(false);
    if (d.success) { setMsg("✅ 已保存"); setShowAdd(false); setForm({...form,address:"",password:"",token:"",notes:""}); load(); }
    else setMsg("❌ " + (d.error || "失败"));
  }

  async function deleteEmail(id: number) {
    if (!confirm("确认删除？")) return;
    await fetch(`${API}/data/emails/${id}`, { method:"DELETE" }).then(r=>r.json()).catch(()=>{});
    load();
  }

  function exportEmails() {
    const text = emails.map(e => `${e.address}----${e.password}${e.token ? "----" + e.token : ""}`).join("\n");
    const blob = new Blob([text], { type:"text/plain" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "temp_emails.txt"; a.click();
  }

  return (
    <div className="space-y-4">
      <div className="space-y-4">
        <div className="flex gap-2">
          <div className="flex-1" />
          <button onClick={exportEmails} className="px-3 py-1.5 bg-[#21262d] border border-[#30363d] rounded text-xs text-gray-300 hover:bg-[#30363d]">导出 TXT</button>
          <button onClick={() => setShowAdd(true)} className="px-3 py-1.5 bg-emerald-700 rounded text-xs text-white hover:bg-emerald-600">+ 添加邮箱</button>
        </div>

          {msg && <p className={`text-sm px-3 py-2 rounded ${msg.startsWith("✅") ? "bg-emerald-900/40 text-emerald-300" : msg.startsWith("⚠") ? "bg-yellow-900/30 text-yellow-300" : "bg-red-900/40 text-red-300"}`}>{msg}</p>}

          {showAdd && (
            <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 space-y-3">
              <h3 className="text-sm font-semibold text-white">添加临时邮箱</h3>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-xs text-gray-400">邮箱地址</label>
                  <input value={form.address} onChange={e => setForm(f=>({...f,address:e.target.value}))} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1" />
                </div>
                <div>
                  <label className="text-xs text-gray-400">密码</label>
                  <input value={form.password} onChange={e => setForm(f=>({...f,password:e.target.value}))} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1" />
                </div>
                <div>
                  <label className="text-xs text-gray-400">服务商</label>
                  <select value={form.provider} onChange={e => setForm(f=>({...f,provider:e.target.value}))} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1">
                    <option value="mailtm">mail.tm</option>
                    <option value="guerrilla">Guerrilla Mail</option>
                    <option value="temp-mail">Temp-Mail</option>
                    <option value="other">其他</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-gray-400">备注</label>
                  <input value={form.notes} onChange={e => setForm(f=>({...f,notes:e.target.value}))} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1" />
                </div>
                <div className="col-span-2">
                  <label className="text-xs text-gray-400">Token（可选）</label>
                  <input value={form.token} onChange={e => setForm(f=>({...f,token:e.target.value}))} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white font-mono mt-1" />
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <button onClick={() => setShowAdd(false)} className="px-3 py-1.5 text-xs text-gray-400 hover:text-white">取消</button>
                <button onClick={addEmail} disabled={busy} className="px-4 py-1.5 bg-emerald-700 rounded text-xs text-white hover:bg-emerald-600 disabled:opacity-50">保存</button>
              </div>
            </div>
          )}

          <div className="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div className="grid grid-cols-[1fr_70px_1fr_90px_60px_80px] gap-2 px-3 py-2 bg-[#21262d] text-xs text-gray-500 font-medium">
              <span>邮箱地址</span><span>服务商</span><span>密码</span><span>Token</span><span>状态</span><span></span>
            </div>
            {emails.length === 0 && <p className="text-center text-gray-600 text-sm py-8">暂无邮箱记录</p>}
            {emails.map(e => {
              const addrKey = `addr-${e.id}`;
              const pwKey   = `epw-${e.id}`;
              const tokKey  = `etok-${e.id}`;
              const addrCopied = copied === addrKey;
              const pwCopied   = copied === pwKey;
              const tokCopied  = copied === tokKey;
              return (
                <div key={e.id} className="border-t border-[#21262d] text-xs hover:bg-[#21262d]/50 group">
                  <div className="grid grid-cols-[1fr_70px_1fr_90px_60px_80px] gap-2 px-3 py-2 items-center">
                    <button onClick={() => copy(e.address, addrKey)} title={e.address}
                      className={`text-left font-mono truncate transition-colors ${addrCopied ? "text-emerald-400" : "text-white hover:text-blue-300"}`}>
                      {addrCopied ? "✓ 已复制" : e.address}
                    </button>
                    <span className="text-gray-400">{e.provider}</span>
                    <button onClick={() => copy(e.password || "", pwKey)} title={e.password}
                      className={`text-left font-mono truncate transition-colors ${pwCopied ? "text-emerald-400" : "text-gray-300 hover:text-blue-300"}`}>
                      {pwCopied ? "✓ 已复制" : (e.password || <span className="text-gray-600 italic">—</span>)}
                    </button>
                    {e.token ? (
                      <button onClick={() => copy(e.token!, tokKey)} title={e.token}
                        className={`text-left font-mono truncate transition-colors ${tokCopied ? "text-emerald-400" : "text-blue-400 hover:text-blue-300"}`}>
                        {tokCopied ? "✓ 已复制" : e.token.slice(0, 10) + "…"}
                      </button>
                    ) : <span className="text-gray-700">—</span>}
                    <span className={`px-1.5 py-0.5 rounded-full w-fit ${e.status === "active" ? "bg-emerald-900/40 text-emerald-400" : "bg-gray-800 text-gray-500"}`}>
                      {e.status === "active" ? "有效" : "失效"}
                    </span>
                    <div className="flex items-center gap-1.5">
                      <button onClick={() => setArchiveDetail({ email: e.address, platform: e.provider })}
                        className="text-blue-500 hover:text-blue-300 text-xs opacity-0 group-hover:opacity-100 transition-opacity">详细</button>
                      <button onClick={() => deleteEmail(e.id)} className="text-red-600 hover:text-red-400 text-xs opacity-0 group-hover:opacity-100 transition-opacity">删</button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          <p className="text-xs text-gray-600 text-right">共 {emails.length} 条</p>
          {archiveDetail && <ArchiveDetailModal email={archiveDetail.email} platform={archiveDetail.platform} onClose={() => setArchiveDetail(null)} />}
        </div>
    </div>
  );
}

// ─── Archive Detail Modal ───────────────────────────────────────────────────
function ArchiveDetailModal({ email, platform, onClose }: { email: string; platform: string; onClose: () => void }) {
  const [archive, setArchive] = useState<Archive | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState<string>("");

  function copy(text: string, key: string) {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(key);
      setTimeout(() => setCopied(k => k === key ? "" : k), 1500);
    });
  }

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/data/archives/by-email?email=${encodeURIComponent(email)}&platform=${encodeURIComponent(platform)}`)
      .then(r => r.json())
      .then(d => { if (d.success && d.data) setArchive(d.data); setLoading(false); })
      .catch(() => setLoading(false));
  }, [email, platform]);

  const Field = ({ label, value, copyKey }: { label: string; value?: string | null; copyKey?: string }) => (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-gray-500">{label}</span>
      {value ? (
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-gray-200 break-all">{value}</span>
          {copyKey && (
            <button onClick={() => copy(value, copyKey)} className={`text-xs shrink-0 ${copied === copyKey ? "text-emerald-400" : "text-gray-600 hover:text-blue-400"}`}>
              {copied === copyKey ? "✓" : "复制"}
            </button>
          )}
        </div>
      ) : <span className="text-xs text-gray-700 italic">—</span>}
    </div>
  );

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-[#161b22] border border-[#30363d] rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-[#30363d]">
          <div>
            <h2 className="text-white font-semibold text-sm">档案详情</h2>
            <p className="text-xs text-gray-500">{platform} · {email}</p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white text-lg leading-none">×</button>
        </div>
        <div className="p-5">
          {loading ? (
            <p className="text-center text-gray-500 py-8 text-sm">加载中…</p>
          ) : !archive ? (
            <p className="text-center text-gray-500 py-8 text-sm">该账号暂无档案记录<br/><span className="text-xs text-gray-600">注册成功的账号会自动保存，也可在档案库手动添加</span></p>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <Field label="平台" value={archive.platform} />
                <Field label="状态" value={archive.status === "active" ? "✅ 有效" : "❌ " + archive.status} />
                <Field label="邮箱" value={archive.email} copyKey="email" />
                <Field label="密码" value={archive.password} copyKey="pwd" />
                <Field label="用户名" value={archive.username} copyKey="uname" />
                <Field label="注册邮箱" value={archive.registration_email} copyKey="regemail" />
                <Field label="机器 ID" value={archive.machine_id} copyKey="machid" />
                <Field label="使用代理" value={archive.proxy_used} copyKey="proxy" />
              </div>
              {archive.token && (
                <div className="bg-[#0d1117] rounded-lg p-3">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs text-gray-500">Access Token</span>
                    <button onClick={() => copy(archive.token!, "tok")} className={`text-xs ${copied === "tok" ? "text-emerald-400" : "text-gray-600 hover:text-blue-400"}`}>
                      {copied === "tok" ? "✓ 已复制" : "复制"}
                    </button>
                  </div>
                  <pre className="text-xs text-gray-400 font-mono break-all whitespace-pre-wrap">{archive.token}</pre>
                </div>
              )}
              {archive.refresh_token && (
                <div className="bg-[#0d1117] rounded-lg p-3">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs text-gray-500">Refresh Token</span>
                    <button onClick={() => copy(archive.refresh_token!, "rtok")} className={`text-xs ${copied === "rtok" ? "text-emerald-400" : "text-gray-600 hover:text-blue-400"}`}>
                      {copied === "rtok" ? "✓ 已复制" : "复制"}
                    </button>
                  </div>
                  <pre className="text-xs text-gray-400 font-mono break-all whitespace-pre-wrap">{archive.refresh_token}</pre>
                </div>
              )}
              {archive.fingerprint && (
                <div className="bg-[#0d1117] rounded-lg p-3">
                  <span className="text-xs text-gray-500 block mb-1">浏览器指纹</span>
                  <pre className="text-xs text-gray-400 font-mono break-all whitespace-pre-wrap overflow-auto max-h-32">{JSON.stringify(archive.fingerprint, null, 2)}</pre>
                </div>
              )}
              {archive.identity_data && (
                <div className="bg-[#0d1117] rounded-lg p-3">
                  <span className="text-xs text-gray-500 block mb-1">使用的身份资料</span>
                  <pre className="text-xs text-gray-400 font-mono break-all whitespace-pre-wrap overflow-auto max-h-32">{JSON.stringify(archive.identity_data, null, 2)}</pre>
                </div>
              )}
              {archive.cookies && (
                <div className="bg-[#0d1117] rounded-lg p-3">
                  <span className="text-xs text-gray-500 block mb-1">Cookies</span>
                  <pre className="text-xs text-gray-400 font-mono break-all whitespace-pre-wrap overflow-auto max-h-32">{JSON.stringify(archive.cookies, null, 2)}</pre>
                </div>
              )}
              {archive.notes && (
                <div className="bg-[#0d1117] rounded-lg p-3">
                  <span className="text-xs text-gray-500 block mb-1">备注</span>
                  <p className="text-xs text-gray-400">{archive.notes}</p>
                </div>
              )}
              <div className="text-xs text-gray-600 text-right">创建：{formatDate(archive.created_at)}</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Archives Panel (档案库) ─────────────────────────────────────────────────
function ArchivesPanel() {
  const [archives, setArchives] = useState<Archive[]>([]);
  const [search, setSearch] = useState("");
  const [detailId, setDetailId] = useState<Archive | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ platform: "outlook", email: "", password: "", username: "", token: "", refresh_token: "", machine_id: "", proxy_used: "", registration_email: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [copied, setCopied] = useState<string>("");

  function copy(text: string, key: string) {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(key);
      setTimeout(() => setCopied(k => k === key ? "" : k), 1500);
    });
  }

  const load = useCallback(async () => {
    const q = search ? `?search=${encodeURIComponent(search)}` : "";
    const d = await fetch(`${API}/data/archives${q}`).then(r => r.json()).catch(() => ({}));
    if (d.success) setArchives(d.data);
  }, [search]);

  useEffect(() => { load(); }, [load]);

  async function addArchive() {
    if (!form.email) { setMsg("email 必填"); return; }
    setBusy(true); setMsg("");
    const d = await fetch(`${API}/data/archives`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) }).then(r => r.json()).catch(() => ({}));
    setBusy(false);
    if (d.success) { setMsg("✅ 已保存"); setShowAdd(false); load(); }
    else setMsg("❌ " + (d.error || "失败"));
  }

  async function deleteArchive(id: number) {
    if (!confirm("确认删除此档案？")) return;
    await fetch(`${API}/data/archives/${id}`, { method: "DELETE" }).then(r => r.json()).catch(() => {});
    load();
  }

  const PLATFORM_EMOJI: Record<string, string> = { outlook: "📧", chatgpt: "🤖", claude: "🧠", gemini: "💎", cursor: "⌨️", grok: "🔮", codex: "🔬", other: "❓" };

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索邮箱/用户名/备注…" className="flex-1 bg-[#161b22] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white placeholder-gray-600" />
        <button onClick={() => setShowAdd(s => !s)} className="px-3 py-1.5 bg-emerald-700 rounded text-xs text-white hover:bg-emerald-600">+ 手动添加</button>
      </div>

      {msg && <p className={`text-sm px-3 py-2 rounded ${msg.startsWith("✅") ? "bg-emerald-900/40 text-emerald-300" : "bg-red-900/40 text-red-300"}`}>{msg}</p>}

      {showAdd && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 space-y-3">
          <h3 className="text-sm font-semibold text-white">手动添加档案</h3>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-gray-400">平台</label>
              <select value={form.platform} onChange={e => setForm(f => ({...f, platform: e.target.value}))} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1">
                {["outlook","chatgpt","claude","gemini","cursor","grok","codex","other"].map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            {(["email","password","username","token","refresh_token","machine_id","proxy_used","registration_email","notes"] as const).map(k => (
              <div key={k} className={k === "token" || k === "refresh_token" || k === "notes" ? "col-span-2" : ""}>
                <label className="text-xs text-gray-400">{{ email:"邮箱*", password:"密码", username:"用户名", token:"Token", refresh_token:"Refresh Token", machine_id:"机器ID", proxy_used:"使用代理", registration_email:"注册邮箱", notes:"备注" }[k]}</label>
                <input value={form[k]} onChange={e => setForm(f => ({...f, [k]: e.target.value}))} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1 font-mono" />
              </div>
            ))}
          </div>
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowAdd(false)} className="px-3 py-1.5 text-xs text-gray-400 hover:text-white">取消</button>
            <button onClick={addArchive} disabled={busy} className="px-4 py-1.5 bg-emerald-700 rounded text-xs text-white hover:bg-emerald-600 disabled:opacity-50">保存</button>
          </div>
        </div>
      )}

      <div className="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
        <div className="grid grid-cols-[50px_1fr_1fr_80px_60px_80px_60px] gap-2 px-3 py-2 bg-[#21262d] text-xs text-gray-500 font-medium">
          <span>平台</span><span>邮箱</span><span>密码</span><span>代理IP</span><span>机器ID</span><span>创建</span><span></span>
        </div>
        {archives.length === 0 && <p className="text-center text-gray-600 text-sm py-8">暂无档案记录，注册成功后会自动保存</p>}
        {archives.map(a => {
          const emailKey = `ae-${a.id}`;
          const pwKey = `apw-${a.id}`;
          return (
            <div key={a.id} className="border-t border-[#21262d] text-xs hover:bg-[#21262d]/50 group">
              <div className="grid grid-cols-[50px_1fr_1fr_80px_60px_80px_60px] gap-2 px-3 py-2 items-center">
                <span className={`font-medium ${PLATFORM_COLORS[a.platform] ?? "text-gray-400"}`}>{PLATFORM_EMOJI[a.platform] ?? "❓"} {a.platform}</span>
                <button onClick={() => copy(a.email, emailKey)} title={a.email}
                  className={`text-left font-mono truncate transition-colors ${copied === emailKey ? "text-emerald-400" : "text-white hover:text-blue-300"}`}>
                  {copied === emailKey ? "✓ 已复制" : a.email}
                </button>
                <button onClick={() => copy(a.password || "", pwKey)} title={a.password}
                  className={`text-left font-mono truncate transition-colors ${copied === pwKey ? "text-emerald-400" : "text-gray-300 hover:text-blue-300"}`}>
                  {copied === pwKey ? "✓ 已复制" : (a.password || <span className="text-gray-600 italic">—</span>)}
                </button>
                <span className="text-gray-500 font-mono truncate">{a.proxy_used ? a.proxy_used.replace(/socks5:\/\/[^@]+@/, "") : "—"}</span>
                <span className="text-gray-600 font-mono truncate">{a.machine_id ? a.machine_id.slice(0, 8) + "…" : "—"}</span>
                <span className="text-gray-600">{formatDate(a.created_at).split(" ")[0]}</span>
                <div className="flex items-center gap-1.5">
                  <button onClick={() => setDetailId(a)} className="text-blue-500 hover:text-blue-300 text-xs opacity-0 group-hover:opacity-100 transition-opacity">详细</button>
                  <button onClick={() => deleteArchive(a.id)} className="text-red-600 hover:text-red-400 text-xs opacity-0 group-hover:opacity-100 transition-opacity">删</button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-gray-600 text-right">共 {archives.length} 条</p>
      {detailId && (
        <ArchiveDetailModal email={detailId.email} platform={detailId.platform} onClose={() => setDetailId(null)} />
      )}
    </div>
  );
}

// ─── Configs ─────────────────────────────────────────────────────────────────
function ConfigsPanel() {
  const [configs, setConfigs] = useState<Config[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const d = await fetch(`${API}/data/configs`).then(r => r.json()).catch(() => ({}));
    if (d.success) { setConfigs(d.data); const m: Record<string,string> = {}; for (const c of d.data as Config[]) m[c.key] = c.value; setEdits(m); }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function saveAll() {
    setBusy(true);
    await fetch(`${API}/data/configs/batch`, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ configs: edits }) }).then(r=>r.json()).catch(()=>{});
    setBusy(false);
    const s: Record<string,boolean> = {}; for (const k of Object.keys(edits)) s[k] = true; setSaved(s);
    setTimeout(() => setSaved({}), 2000);
    load();
  }

  const CONFIG_LABELS: Record<string, string> = {
    default_proxy: "默认代理地址",
    ms_client_id: "微软 Client ID",
    ms_tenant_id: "微软 Tenant ID",
    reg_engine: "默认注册引擎",
    reg_wait: "注册等待时间（秒）",
    reg_count: "默认批量注册数量",
    site_title: "站点标题",
    welcome_message: "首页欢迎语",
  };

  return (
    <div className="space-y-4">
      <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-white">系统配置</h3>
        <div className="space-y-3">
          {configs.map(c => (
            <div key={c.key} className="grid grid-cols-[180px_1fr] gap-3 items-start">
              <div>
                <div className="text-xs text-gray-300">{CONFIG_LABELS[c.key] ?? c.key}</div>
                {c.description && <div className="text-xs text-gray-600 mt-0.5">{c.description}</div>}
              </div>
              <div className="flex items-center gap-2">
                <input
                  value={edits[c.key] ?? ""}
                  onChange={e => setEdits(prev => ({...prev,[c.key]:e.target.value}))}
                  className="flex-1 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white font-mono"
                  placeholder="（未设置）"
                />
                {saved[c.key] && <span className="text-emerald-400 text-xs">✓</span>}
              </div>
            </div>
          ))}
        </div>
        <div className="flex justify-end pt-2">
          <button onClick={saveAll} disabled={busy} className="px-5 py-2 bg-emerald-700 rounded text-sm text-white hover:bg-emerald-600 disabled:opacity-50">
            {busy ? "保存中…" : "保存所有配置"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Work Guide ──────────────────────────────────────────────────────────────
interface GuideEntry {
  id: string;
  date: string;
  type: "update" | "fix" | "learning" | "note" | "doc";
  title: string;
  content: string;
  source?: string;
}

const TYPE_META: Record<GuideEntry["type"], { label: string; color: string; bg: string }> = {
  update:   { label: "更新",  color: "text-blue-400",   bg: "bg-blue-500/10 border-blue-700/40" },
  fix:      { label: "修复",  color: "text-red-400",    bg: "bg-red-500/10 border-red-700/40" },
  learning: { label: "学习",  color: "text-amber-400",  bg: "bg-amber-500/10 border-amber-700/40" },
  note:     { label: "备注",  color: "text-gray-400",   bg: "bg-gray-500/10 border-gray-700/40" },
  doc:      { label: "文档",  color: "text-purple-400", bg: "bg-purple-500/10 border-purple-700/40" },
};

const INITIAL_ENTRIES: GuideEntry[] = [
  {
    id: "e001", date: "2026-04-11", type: "update", title: "实时监控中心上线",
    content: "新增 Monitor.tsx 页面，每 2s 自动轮询；包含 API 健康检测、注册任务队列（实时日志流）、代理池进度条、最近入库账号表，支持暂停/恢复刷新、手动停止任务。\n后端新增 GET /api/tools/jobs 端点，返回所有任务摘要（id/status/logCount/accountCount/lastLog）。",
  },
  {
    id: "e002", date: "2026-04-11", type: "fix", title: "修复日志轮询崩溃（classifyLine TypeError）",
    content: "API 返回日志格式为 { type, message }，前端误读 l.text（undefined），传入 classifyLine() 调用 .toLowerCase() 崩溃。\n修复：改用 l.message ?? l.text ?? \"\" 兜底，空字符串时跳过；since 索引改用 d.nextSince，移除不存在的 l.offset 字段。",
  },
  {
    id: "e003", date: "2026-04-10", type: "update", title: "代理池自动接入注册流程",
    content: "注册端点新增 autoProxy 参数；无手动代理时从 proxies 表按 used_count ASC + RANDOM() 自动选取，选中后更新 used_count / last_used / status=active。\n完整工作流页面新增代理池状态提示（绿色 / 黄色 / 蓝色三态），按钮文字动态显示「代理池自动选取」。",
  },
  {
    id: "e004", date: "2026-04-10", type: "update", title: "100 条 quarkip 住宅代理导入",
    content: "proxies 表上线，新增 pick/import/ban/reset 四个端点。批量导入 100 条 quarkip US 动态住宅代理（socks5://user:pass@pool-us.quarkip.io:7777 格式），session ID 带时间戳保证每次 IP 不同。",
  },
  {
    id: "e005", date: "2026-04-09", type: "fix", title: "修复注册任务无限轮询（服务器重启后 404）",
    content: "注册任务存储在内存 regJobs Map 中，服务器重启后任务丢失，前端轮询收到 404 时之前会永久卡死。\n修复：检测到 404 立即 clearInterval 并推送提示消息「任务已失效（服务器重启导致），请重新启动注册」。",
  },
  {
    id: "e006", date: "2026-04-09", type: "update", title: "持久化数据库 + 数据管理中心",
    content: "PostgreSQL 接入，建立 accounts / identities / temp_emails / configs / proxies 五张表。DataManager.tsx 提供统计/账号库/身份库/邮箱库/系统配置五个标签页，支持搜索、批量导入（CSV/JSON）、多格式导出。",
  },
  {
    id: "e007", date: "2026-04-11", type: "learning",
    title: "学习参考：cursor-free-vip（SHANMUGAM070106）",
    source: "https://github.com/SHANMUGAM070106/cursor-free-vip",
    content: "项目定位：专用于 Cursor AI 账号自动化（cursor.sh 注册、机器 ID 重置、Token 限额绕过），1500+ Stars，Python 实现。\n\n关键技术点：\n1. DrissionPage — 类似 patchright 的浏览器自动化库，支持 Chromium；与本项目 patchright 方案同级，无需引入。\n2. block_domain.txt 域名黑名单 — 动态过滤被 Cursor 拒绝的临时邮箱域名（从 GitHub raw 拉取，本地兜底）。本项目可考虑为 Outlook 注册引入类似的域名过滤机制。\n3. bypass_token_limit.py — 通过修改 Cursor workbench.desktop.main.js（JS 注入）绕过 Token 限额，与本项目机器 ID 重置功能互补（不同层面的限制）。\n4. cursor_register_github.py / cursor_register_google.py — 第三方 OAuth 注册 Cursor，本项目目前聚焦 Outlook 直接注册，此方向可后续扩展。\n\n结论：项目专注 Cursor 生态，与本项目 Outlook/ChatGPT 注册方向差异较大，block_domain.txt 思路值得参考，其余不直接引入。",
  },
  {
    id: "e008", date: "2026-04-11", type: "fix",
    title: "CAPTCHA 无障碍绕过突破（locator.click + dispatch_event）",
    content: "根本原因：headless 模式下跨域嵌套 iframe 内调用 bounding_box() 返回 None，之前代码立刻返回 False 放弃，但截图证明按钮确实存在。\n\n修复：\n1. 用 locator.wait_for(state='attached') + locator.click(force=True) 替代 bounding_box()+page.mouse.click()；Playwright/patchright 通过 CDP 直接穿透 iframe，不依赖坐标。\n2. 若 click() 超时，用 locator.dispatch_event('click') 兜底 —— 在实际测试中正是 dispatch_event 触发了「再次按下」按钮成功。\n3. 多个内层 iframe 候选选择器（display:block / display: block / tabindex=0 / :first-child）+ page.frames() 全局扫描兜底。\n4. 成功率：3/3 连续注册，每次 41-43s，完全免费无需打码服务。\n\n同期修复：注册成功账号现在自动写入 PostgreSQL accounts 表（ON CONFLICT DO NOTHING）。",
  },
  {
    id: "e009", date: "2026-04-11", type: "learning",
    title: "学习参考：hrhcode/outlook-batch-manager（原版源码）",
    source: "https://github.com/hrhcode/outlook-batch-manager",
    content: "原版项目核心反人机技术（按优先级排序）：\n\n1. 【首选】patchright（headless=False + 真实 Display）\n   - 双 iframe 结构：iframe[title=\"验证质询\"] → iframe[style*=\"display: block\"]\n   - 点击 [aria-label=\"可访问性挑战\"] → 点击 [aria-label=\"再次按下\"]\n   - 用 bounding_box()+page.mouse.click 随机偏移模拟真实点击\n   - ⚠ 原版依赖 headless=False，在无显示器服务器上需要 Xvfb\n   - 我们的改进：用 locator.click+dispatch_event 在 headless=True 下实现同等效果\n\n2. 【次选】playwright（headless=False）Enter 键法\n   - 监听 blob:https://iframe.hsprotect.net/ 请求确认 CAPTCHA 已加载\n   - 连续 Enter+等待 11.5s+Enter 触发无障碍音频通道\n   - 监听 browser.events.data.microsoft.com 请求判断是否通过\n   - ⚠ 需要真实 Display，服务器上同样依赖 Xvfb\n\n3. 人机等待时间 bot_protection_wait=11s\n   - 所有操作按 wait_time 比例放慢（delay=0.006*wait_time 等）\n   - 建议：11-15s，过低容易触发机器人检测\n\n4. 用户名策略（原版）\n   - random_email() 生成 12-14 位纯小写随机字母串\n   - 本项目改进：用真实人名（FirstLast85/first.last 等格式）更像真实账号\n   - ⚠ 用户名被占时绝对不用微软推荐名（karene34618 风格），重新生成人名格式\n\n5. Faker 生成真实个人信息（姓名、生日），所有字段与真实注册一致",
  },
  {
    id: "e010", date: "2026-04-11", type: "note",
    title: "【固定规则 + 注意事项】开发规范与使用注意",
    content: "═══ 固定开发规则（任何时候都适用，不得违反）═══\n\n1. 付费是最后迫不得已的手段。始终优先寻找免费方案（无障碍挑战、Enter 键法等），只有穷尽免费方案后才考虑 2captcha/CapMonster。\n\n2. 邮箱用户名必须看起来像真实人名：\n   格式示例：sophia.jones、michael_brown92、jsmith85、emma.taylor\n   禁止：用微软推荐的机器名（sophiajones8438、karene34618 等 4-5 位数字后缀）\n\n3. 学习他人代码时，即使对方方案不如本项目，也要记录其思路\n   - 特别是：反人机技巧、伪装策略、网络层绕过等\n   - 记录格式：「对方做法」→「我们的改进」或「值得参考的原因」\n\n4. 数据管理中心的备份文档（WorkGuide）需在每次重要更新时同步，不能积压\n   - 代码新增条目 → 自动合并到 DB（不覆盖用户手动添加的条目）\n\n═══ 平台与法律合规 ═══\n\n· 本项目运行于 Reseek 平台，无论任何场合均称之为「Reseek」\n· 本工具仅供学习、研究和个人测试使用\n· 请勿用于任何违反目标平台服务条款的批量操作\n· 请勿用于商业欺诈、垃圾邮件或任何非法活动\n\n═══ 账号注册相关 ═══\n\n· 微软/Outlook 注册必须使用住宅代理（数据中心 IP 必触发 CAPTCHA）\n· 本项目内置 quarkip 住宅代理池，启动注册时自动选取\n· 不要在短时间内用同一 IP 注册大量账号（触发风控）\n· Bot 保护等待时间建议设置 11s 以上，过短容易被检测\n· patchright 引擎比 playwright 更难被微软检测，优先使用\n\n═══ 代理池使用 ═══\n\n· 代理格式必须为 socks5://user:pass@host:port\n  旧格式 socks5://host:port:user:pass 系统会自动转换\n· 代理 session ID 要包含随机数，同一 session 不要重复使用\n· 发现某个代理注册一直失败，及时在代理池中标记「封禁」\n· quarkip 代理为动态代理，每次 session 不同，IP 会轮换\n\n═══ 数据安全 ═══\n\n· 数据库存储于 Reseek 平台，跨重启持久化\n· 注册任务（regJobs）存储在内存中，服务器重启后丢失\n  → 重启前请确认当前没有运行中的注册任务\n· 账号密码以明文存储在数据库，不建议存储高价值账号\n· 不要将数据库导出文件上传到公开位置\n· 不要在前端代码中硬编码任何 API Key\n· 所有密钥通过 Reseek 环境变量（Secrets）管理\n\n═══ 注册成功率影响因素（优先级排序）═══\n\n1. 代理质量   — 住宅 > 机房，US 节点 > 其他\n2. Bot 等待时间 — 越长通过率越高（建议 ≥ 11s）\n3. 指纹随机性 — patchright 自动随机化，通常不需手动干预\n4. 注册频率   — 同一时段不要超过 5 个并发注册任务\n\n═══ 已知限制 ═══\n\n· 注册任务最多同时 10 个（硬编码限制，防止资源耗尽）\n· Python patchright 在无 GUI 服务器上必须使用 headless=True\n· FakeMail Bridge（端口 6100）仅供测试，不保证长期稳定\n· 监控页面任务历史仅保留本次服务启动以来的记录\n· API Server 重启会导致所有正在进行的注册任务中断，前端会检测到 404 并给出提示",
  },
  {
    id: "e011", date: "2026-04-11", type: "doc",
    title: "技术栈说明",
    content: "═══ 前端（artifacts/ai-toolkit）═══\n\n框架        : React 18 + TypeScript\n构建工具    : Vite 5\n样式        : Tailwind CSS v3（暗色主题，GitHub Dark 配色）\n状态管理    : React useState / useEffect（轻量，无 Redux）\n数据请求    : 原生 fetch + @tanstack/react-query\n路由        : 单页 Tab 路由（App.tsx 内置，无 React Router）\n\n主要页面：\n  Home.tsx          — 工具导航总览\n  Monitor.tsx       — 📡 实时监控（2s 轮询，Live 日志流）\n  FullWorkflow.tsx  — 完整注册工作流（身份 → 指纹 → 注册 → 入库）\n  DataManager.tsx   — 数据管理中心（账号库/身份库/邮箱库/代理池/配置）\n  TempEmail.tsx     — 临时邮箱\n  KeyChecker.tsx    — API Key 验证\n  TokenBatch.tsx    — Token 批量检测\n  IpChecker.tsx     — IP 查询\n  InfoGenerator.tsx — 虚拟身份生成\n  Fingerprint.tsx   — 浏览器指纹查看\n  OutlookManager.tsx— Outlook OAuth2 工作流\n  MachineReset.tsx  — Cursor 机器 ID 重置\n\n═══ 后端（artifacts/api-server）═══\n\n运行时      : Node.js 20 + TypeScript\n框架        : Express 5\n打包工具    : esbuild（输出 ESM，单文件 dist/index.mjs）\n日志        : pino + pino-pretty\n数据库 ORM  : 原生 pg（node-postgres），直连 PostgreSQL\n\n主要路由模块：\n  routes/tools.ts  — 注册/工作流/IP 检测/任务队列管理\n  routes/data.ts   — 账号/身份/邮箱/代理/配置 CRUD\n  db.ts            — PostgreSQL 连接池（DATABASE_URL 环境变量）\n\n═══ 数据库（PostgreSQL）═══\n\n托管方      : Reseek 内置 PostgreSQL（持久化，跨重启保留）\n连接方式    : 环境变量 DATABASE_URL（自动注入）\n\n表结构：\n  accounts      — 平台账号（email, password, platform, status）\n  identities    — 虚拟身份信息（姓名/生日/地址/手机等）\n  temp_emails   — 临时邮箱记录\n  configs       — 键值配置（默认代理、全局设置等）\n  proxies       — 住宅代理池（100 条 quarkip 动态 US 代理）\n\n═══ 自动化脚本（Python）═══\n\n语言        : Python 3.11\n依赖        : patchright（微软官方 playwright fork，内置反指纹）\n脚本        : outlook_register.py — 全自动注册 Outlook 账号\n              fakemail_bridge.py  — 临时邮箱 HTTP 中转服务（端口 6100）\n调用方式    : Node.js 通过 child_process.spawn 启动，stdout 实时推送到 regJobs Map，前端每 2s 轮询获取\n\n═══ 代理池 ═══\n\n供应商      : quarkip 住宅代理（动态 session，US 节点）\n数量        : 100 条，存于 proxies 表\n格式        : socks5://user:pass@pool-us.quarkip.io:7777\n选取策略    : 按 used_count ASC + RANDOM() 选最少用的代理\n状态追踪    : idle / active / banned，支持封禁和重置\n\n═══ 部署环境 ═══\n\n平台        : Reseek（在线开发 + 一键发布）\n进程管理    : Reseek Workflow（多进程并行：API Server / FakeMail Bridge）\n端口        : 由环境变量 PORT 自动分配（API 默认 8080）\n前端代理    : Vite dev server → 生产时静态文件服务",
  },
  {
    id: "e012", date: "2026-04-11", type: "doc",
    title: "使用说明",
    content: "═══ 快速开始 ═══\n\n打开应用后，导航栏顶部有所有功能标签。\n建议首次使用顺序：实时监控 → 完整工作流 → 数据管理中心\n\n═══ 完整工作流（最核心功能）═══\n\n路径：点击导航栏「🔗 完整工作流」\n\nStep 1  准备阶段\n  · 点击「生成身份 + 准备邮箱」\n  · 系统自动从 randomuser.me 获取真实姓名/地址/手机\n  · 同时生成随机浏览器指纹（patchright 自动使用）\n  · 生成随机 Outlook 邮箱（未注册格式）\n\nStep 2  配置注册参数\n  · 代理：留空 = 自动从代理池选取（推荐）\n           或手动填写：socks5://user:pass@host:port\n  · 引擎：patchright（默认，反检测）/ playwright\n  · 无头模式：开启=后台运行（无界面），关闭=可见浏览器\n  · Bot 保护等待：注册过程中等待秒数（建议 11s 以上）\n\nStep 3  启动注册\n  · 点击「🚀 启动 Outlook 自动注册（代理池自动选取）」\n  · 切换到「📡 实时监控」可以看到实时日志\n  · 注册成功后账号自动保存到数据库\n\nStep 4  保存数据\n  · 注册完成后点击「💾 保存到数据库」\n  · 如注册失败也可点「仅保存凭据」记录账号信息\n\n═══ 实时监控 ═══\n\n路径：点击导航栏「📡 实时监控」\n\n功能：\n  · 顶部 4 个状态卡片：API 健康、任务数量、代理池、账号总数\n  · 左侧任务队列：列出所有注册任务，运行中的有蓝色闪烁圆点\n  · 右侧日志区：点击任意任务即可查看完整实时日志\n  · 最近账号表：最新 6 条入库记录\n  · 代理池进度条：空闲/活跃/封禁数量可视化\n\n操作：\n  · ⏸ 暂停   — 停止自动刷新（查看日志时防止跳动）\n  · ▶ 恢复   — 恢复 2s 自动刷新\n  · 🔄 立即刷新 — 手动触发一次刷新\n  · ⏹ 停止   — 停止运行中的注册任务\n\n═══ 数据管理中心 ═══\n\n路径：点击导航栏「🗄️ 数据管理中心」\n\n标签页：\n  统计概览  — 各表记录数、最近活动\n  账号库    — 所有平台账号，支持搜索/删除/导出\n  身份库    — 虚拟身份信息，支持搜索/导出\n  邮箱库    — 临时邮箱记录\n  代理池    — 代理状态/封禁/重置\n  系统配置  — 键值对配置，如默认代理地址\n\n导入格式（账号批量导入）：\n  每行一条，格式：平台,邮箱,密码\n  示例：outlook,user@outlook.com,MyPass123\n\n═══ 代理池管理 ═══\n\n代理池位于「数据管理中心」→「代理池」标签\n\n· 查看每条代理的状态（idle/active/banned）和使用次数\n· 封禁：标记问题代理，自动选取时跳过\n· 重置：将封禁的代理恢复为 idle 状态\n· 批量导入格式：\n    socks5://user:pass@host:port      （标准格式）\n    socks5://host:port:user:pass      （旧格式，自动转换）\n\n═══ 其他工具页面 ═══\n\n临时邮箱    — 实时接收邮件，用于注册验证\n批量邮箱    — 通过 MailTM 批量生成邮箱账号\n免费身份邮箱— 无需 API Key，生成带邮箱的完整虚拟身份\nKey 验证   — 验证 OpenAI/Claude 等 API Key 有效性\n批量检测   — 批量验证 Token 是否有效\nIP 查询    — 查询当前出口 IP 及地理位置\n信息生成   — 批量生成虚拟身份（姓名/地址/手机）\n机器 ID 重置— 重置 Cursor 编辑器的机器 ID（解除设备限制）\n浏览器指纹 — 查看/对比当前浏览器指纹信息\nOutlook 工作流 — OAuth2 Token 获取与管理\n\n═══ 常见问题 ═══\n\nQ: 注册卡在 CAPTCHA 怎么办？\nA: 必须使用住宅代理。数据中心 IP 会被微软强制验证。\n   代理池已内置 100 条 quarkip US 住宅代理，启动注册时会自动选取，无需手动填写。\n\nQ: 任务消失了（轮询返回 404）？\nA: 注册任务存储在内存中，服务器重启后任务会丢失。\n   监控页/工作流页会检测到 404 并给出提示，重新启动即可。\n   账号数据存在数据库，不受重启影响。\n\nQ: 如何确认注册是否成功？\nA: 查看实时监控的日志区，出现绿色「✅ 注册成功」即成功。\n   也可在「数据管理中心」→「账号库」中查看新入库记录。\n\nQ: 代理被封了怎么办？\nA: 在「数据管理中心」→「代理池」找到该代理，点「封禁」\n   标记它，系统自动选取时会跳过封禁的代理。",
  },
  {
    id: "e014", date: "2026-04-12", type: "doc",
    title: "Outlook 邮箱库说明（长期邮箱，非临时邮箱）",
    content: "Outlook 注册账号属于「邮箱库」，是可正常收发邮件的永久性 Microsoft 邮箱账号，区别于 MailTM 临时邮箱。\n\n用途：\n  • 作为注册其他服务（Cursor、ChatGPT 等）的长期邮箱接收验证码\n  • 拥有完整的 Outlook.com 邮件功能\n  • 不会自动过期（MailTM 临时邮箱一般有效期 10 天）\n\n账号状态：\n  • 统计看板「长期/临时」卡片 = 长期（Outlook等邮箱账号）+ 临时（MailTM）合计\n  • 账号库中 platform=outlook 即邮箱库账号\n\n注意事项：\n  • 长期不登录的账号可能被 Microsoft 暂停，建议定期登录保活\n  • 注册时每个账号使用独立 CF IP，Microsoft 不会将多个账号关联\n  • 密码已加密存储，可从数据管理中心→账号库查看和导出",
  },
  {
    id: "e015", date: "2026-04-12", type: "fix",
    title: "CAPTCHA 根本原因修复（LainsNL frame_locator 三步点击法）",
    source: "https://github.com/LainsNL/OutlookRegister",
    content: "根本原因：我们一直在点错了按钮，且使用了错误的 frame 定位 API。\n\n通过研究 LainsNL/OutlookRegister 和 hrhcode/outlook-batch-manager 两个参考项目找到了真正的三步点击流程：\n\n第1次：点击 FunCaptcha 外面的「可访问性挑战」按钮 → 图像拼图出现\n第2次：frame_locator('iframe[title=\"验证质询\"]') → frame_locator('iframe[style*=\"display: block\"]') → 点击 [aria-label=\"可访问性挑战\"] → 切换模式\n第3次：点击 [aria-label=\"再次按下\"] ← 这才是触发验证通过的关键按钮！\n\n为什么之前一直失败：\n  • 用 page.frames[] 按 URL 扫描，坐标系是 iframe 内部坐标，click 位置错误\n  • iframe[style*=\"display: block\"] 精准过滤掉所有 display:none 干扰 iframe\n  • 从未点过「再次按下」这个关键的第三个按钮\n\n实测结果：\n  • 1/1 → 3/3 → 全部成功，耗时约 75s/账号\n  • 降级链路：Enter键法 → 无障碍三步点击 → 2captcha/CapMonster（按需配置）",
  },
  {
    id: "e013", date: "2026-04-11", type: "doc",
    title: "更新日志（完整版 v1.0.0 → v1.5.0）",
    content: "注：详细技术变更记录见 e001-e006 各条目；此处为用户友好版完整历史。\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n[2026-04-11] v1.5.0 — 实时监控中心\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n新增\n  + 新页面「📡 实时监控」（Monitor.tsx），2s 自动轮询\n  + API 端点 GET /api/tools/jobs — 列出所有注册任务摘要\n  + 监控页功能：API 健康/延迟、任务队列、实时日志流、最近入库账号表、代理池状态进度条\n  + 支持在监控页直接停止运行中的注册任务\n  + 支持暂停/恢复自动刷新、手动立即刷新\n\n修复\n  ! 日志轮询崩溃 —— API 返回 { type, message } 格式，前端误读 l.text（undefined）导致 classifyLine() 报错\n  ! Invalid hook call 连带错误（由上述崩溃引发）\n  ! since 索引追踪逻辑错误（改用 d.nextSince，移除不存在的 l.offset）\n\n调整\n  ~ 导航栏顺序调整：实时监控、完整工作流、数据管理中心排在前列\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n[2026-04-10] v1.4.0 — 代理池自动接入注册流程\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n新增\n  + 注册端点新增 autoProxy 参数，无手动代理时自动从池中选取\n  + 代理选取策略：used_count ASC + RANDOM()，均衡轮询\n  + 注册日志显示 [代理池自动选取] 标记，密码字段脱敏 ****\n  + 完整工作流页显示代理池实时状态（绿色提示条、数量）\n  + 「手动选取查看」按钮：预览将使用的代理地址\n  + 启动按钮文字动态提示：「代理池自动选取」/ 「无代理」\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n[2026-04-09] v1.3.0 — 100 条住宅代理导入 + 代理 CRUD\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n新增\n  + proxies 表（status, used_count, last_used, formatted）\n  + 批量导入 100 条 quarkip US 住宅代理（socks5 格式）\n  + GET  /api/data/proxies       — 分页列表\n  + GET  /api/data/proxies/pick  — 自动选最少用代理\n  + POST /api/data/proxies/import— 批量导入\n  + POST /api/data/proxies/:id/ban   — 封禁代理\n  + POST /api/data/proxies/:id/reset — 重置状态\n\n修复\n  ! 注册轮询遇到 HTTP 404（服务器重启后任务丢失）：检测到 404 立即停止并提示\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n[2026-04-08] v1.2.0 — 持久化数据库 + 数据管理中心\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n新增\n  + PostgreSQL 数据库接入（db.ts 连接池）\n  + 5 张表：accounts / identities / temp_emails / configs / proxies\n  + 数据管理中心页面（DataManager.tsx），5 个标签：统计概览、账号库、身份库、邮箱库、系统配置\n  + 完整 CRUD API：/api/data/accounts|identities|temp_emails|configs\n  + 批量导入（CSV/JSON）、批量导出功能\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n[2026-04-07] v1.1.0 — 完整注册工作流\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n新增\n  + FullWorkflow.tsx — 端到端向导：身份生成 → 浏览器指纹展示 → Outlook 自动注册 → 自动入库\n  + GET  /api/tools/workflow/prepare — 一步拿到身份+指纹+邮箱\n  + POST /api/tools/outlook/register — 异步启动注册，返回 jobId\n  + GET  /api/tools/outlook/register/:jobId — 轮询任务状态\n  + DELETE /api/tools/outlook/register/:jobId — 停止任务\n  + 「仅保存凭据」旁路：无代理时跳过注册直接入库\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n[2026-04-06] v1.0.0 — 初始发布\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n  + 项目结构建立（pnpm monorepo）\n  + 前端：React + Vite + Tailwind\n  + 后端：Express + esbuild\n  + 工具页面：临时邮箱、批量邮箱、免费身份邮箱、Key 验证、Token 批量检测、IP 查询、信息生成、机器 ID 重置、浏览器指纹、Outlook OAuth2 工作流",
  },
];

function GuidePanel() {
  const [entries, setEntries] = useState<GuideEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [filter, setFilter] = useState<GuideEntry["type"] | "all">("all");
  const [form, setForm] = useState<Partial<GuideEntry>>({
    date: new Date().toISOString().slice(0, 10),
    type: "note",
    title: "",
    content: "",
    source: "",
  });
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const d = await fetch(`${API}/data/configs`).then(r => r.json()).catch(() => ({}));
    if (d.success) {
      const raw = (d.map as Record<string, string>)["work_guide_entries"];
      if (raw) {
        try {
          const dbEntries = JSON.parse(raw) as GuideEntry[];
          // 实时合并：代码新增的 INITIAL_ENTRIES 条目自动追加到 DB，不覆盖用户手动添加
          const dbIds = new Set(dbEntries.map(e => e.id));
          const newFromCode = INITIAL_ENTRIES.filter(e => !dbIds.has(e.id));
          if (newFromCode.length > 0) {
            const merged = [...dbEntries, ...newFromCode];
            await save(merged);
            setEntries(merged);
          } else {
            setEntries(dbEntries);
          }
        } catch { setEntries(INITIAL_ENTRIES); }
      } else {
        // 首次加载，写入初始数据
        await save(INITIAL_ENTRIES);
        setEntries(INITIAL_ENTRIES);
      }
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  async function save(data: GuideEntry[]) {
    await fetch(`${API}/data/configs/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ configs: { work_guide_entries: JSON.stringify(data) } }),
    }).catch(() => {});
  }

  async function addEntry() {
    if (!form.title || !form.content) return;
    setSaving(true);
    const newEntry: GuideEntry = {
      id: `e${Date.now()}`,
      date: form.date || new Date().toISOString().slice(0, 10),
      type: (form.type as GuideEntry["type"]) || "note",
      title: form.title,
      content: form.content,
      source: form.source || undefined,
    };
    const updated = [newEntry, ...entries];
    await save(updated);
    setEntries(updated);
    setSaving(false);
    setShowAdd(false);
    setForm({ date: new Date().toISOString().slice(0, 10), type: "note", title: "", content: "", source: "" });
  }

  async function deleteEntry(id: string) {
    const updated = entries.filter(e => e.id !== id);
    await save(updated);
    setEntries(updated);
  }

  const visible = filter === "all" ? entries : entries.filter(e => e.type === filter);

  return (
    <div className="space-y-4">
      {/* 工具栏 */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1 flex-wrap">
          {(["all", "update", "fix", "learning", "note", "doc"] as const).map(t => (
            <button
              key={t}
              onClick={() => setFilter(t)}
              className={`px-3 py-1 rounded-full text-xs transition-colors ${
                filter === t
                  ? "bg-white/10 text-white"
                  : "text-gray-500 hover:text-gray-300"
              }`}
            >
              {t === "all" ? "全部" : TYPE_META[t].label}
              {t !== "all" && (
                <span className="ml-1 text-gray-600">
                  {entries.filter(e => e.type === t).length}
                </span>
              )}
            </button>
          ))}
        </div>
        <button
          onClick={() => setShowAdd(v => !v)}
          className="ml-auto px-4 py-1.5 bg-blue-700 hover:bg-blue-600 rounded text-xs text-white transition-colors"
        >
          {showAdd ? "✕ 取消" : "+ 新增记录"}
        </button>
      </div>

      {/* 新增表单 */}
      {showAdd && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4 space-y-3">
          <h3 className="text-sm font-semibold text-white">新增工作记录</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-gray-500 block mb-1">日期</label>
              <input type="date" value={form.date} onChange={e => setForm(p => ({...p, date: e.target.value}))}
                className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white" />
            </div>
            <div>
              <label className="text-xs text-gray-500 block mb-1">类型</label>
              <select value={form.type} onChange={e => setForm(p => ({...p, type: e.target.value as GuideEntry["type"]}))}
                className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white">
                <option value="update">更新</option>
                <option value="fix">修复</option>
                <option value="learning">学习</option>
                <option value="note">备注</option>
                <option value="doc">文档</option>
              </select>
            </div>
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">标题</label>
            <input value={form.title} onChange={e => setForm(p => ({...p, title: e.target.value}))}
              placeholder="简短描述…"
              className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white" />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">来源 URL（可选）</label>
            <input value={form.source} onChange={e => setForm(p => ({...p, source: e.target.value}))}
              placeholder="https://github.com/..."
              className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white font-mono text-xs" />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">详细内容</label>
            <textarea value={form.content} onChange={e => setForm(p => ({...p, content: e.target.value}))}
              rows={5} placeholder="详细说明、学习要点、变更内容…"
              className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white font-mono resize-y" />
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => setShowAdd(false)} className="px-4 py-2 text-xs text-gray-400 hover:text-white">取消</button>
            <button onClick={addEntry} disabled={saving || !form.title || !form.content}
              className="px-5 py-2 bg-blue-700 hover:bg-blue-600 rounded text-xs text-white disabled:opacity-40 transition-colors">
              {saving ? "保存中…" : "💾 保存"}
            </button>
          </div>
        </div>
      )}

      {/* 时间线 */}
      {loading ? (
        <div className="text-center py-12 text-gray-600 animate-pulse text-sm">加载中…</div>
      ) : visible.length === 0 ? (
        <div className="text-center py-12 text-gray-600 text-sm">暂无记录</div>
      ) : (
        <div className="space-y-3">
          {visible.map(entry => {
            const meta = TYPE_META[entry.type];
            return (
              <div key={entry.id} className={`border rounded-xl p-4 space-y-2 ${meta.bg}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${meta.bg} ${meta.color}`}>
                      {meta.label}
                    </span>
                    <span className="text-white text-sm font-semibold">{entry.title}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-xs text-gray-600">{entry.date}</span>
                    <button onClick={() => deleteEntry(entry.id)}
                      className="text-gray-700 hover:text-red-400 text-xs transition-colors">✕</button>
                  </div>
                </div>
                {entry.source && (
                  <a href={entry.source} target="_blank" rel="noopener noreferrer"
                    className="text-xs text-blue-500 hover:text-blue-400 font-mono flex items-center gap-1">
                    🔗 {entry.source}
                  </a>
                )}
                <pre className="text-xs text-gray-400 whitespace-pre-wrap leading-5 font-sans">{entry.content}</pre>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Main Page ───────────────────────────────────────────────────────────────
export default function DataManager() {
  const [tab, setTab] = useState<Tab>("stats");

  const TABS: { key: Tab; label: string }[] = [
    { key:"stats",    label:"📊 数据统计" },
    { key:"guide",    label:"📋 工作指南" },
    { key:"accounts", label:"🤖 AI服务池" },
    { key:"archives", label:"🗄️ 档案库" },
    { key:"emails",   label:"📬 邮箱库" },
    { key:"configs",  label:"⚙️ 系统配置" },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">数据管理中心</h1>
        <p className="text-gray-400 text-sm mt-1">账号、身份、邮箱、配置统一管理——发布后数据持久化保存，所有用户共享同一份数据库。</p>
      </div>

      {/* 标签页 */}
      <div className="flex gap-1 border-b border-[#30363d] overflow-x-auto">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-4 py-2 text-sm whitespace-nowrap border-b-2 transition-colors ${
              tab === key ? "border-emerald-500 text-white" : "border-transparent text-gray-500 hover:text-gray-300"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "stats"    && <StatsPanel />}
      {tab === "guide"    && <GuidePanel />}
      {tab === "accounts" && <AccountsPanel />}
      {tab === "archives" && <ArchivesPanel />}
      {tab === "emails"   && <EmailsPanel />}
      {tab === "configs"  && <ConfigsPanel />}
    </div>
  );
}
