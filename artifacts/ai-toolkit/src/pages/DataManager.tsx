import { useState, useEffect, useCallback } from "react";

const API = import.meta.env.BASE_URL.replace(/\/$/, "") + "/api";

type Platform = "outlook" | "chatgpt" | "claude" | "gemini" | "cursor" | "grok" | "codex" | "other";
type Tab = "accounts" | "identities" | "emails" | "configs" | "stats";

interface Account {
  id: number; platform: string; email: string; password: string;
  username?: string; token?: string; status: string; notes?: string;
  created_at: string;
}
interface Identity {
  id: number; full_name: string; first_name: string; last_name: string;
  gender: string; birthday?: string; phone?: string; email?: string;
  address?: string; city?: string; state?: string; zip?: string;
  country?: string; username?: string; password?: string; created_at: string;
}
interface TempEmail {
  id: number; address: string; password: string; provider: string;
  token?: string; status: string; notes?: string; created_at: string;
}
interface Config { id: number; key: string; value: string; description?: string; }
interface Stats {
  accounts: { total: number; active: number };
  identities: { total: number };
  emails: { total: number };
  byPlatform: { platform: string; count: number }[];
}

const PLATFORM_COLORS: Record<string, string> = {
  outlook: "text-blue-400", chatgpt: "text-emerald-400", claude: "text-amber-400",
  gemini: "text-purple-400", cursor: "text-cyan-400", grok: "text-pink-400",
  codex: "text-orange-400", other: "text-gray-400",
};
const PLATFORMS: Platform[] = ["outlook","chatgpt","claude","gemini","cursor","grok","codex","other"];

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
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label:"账号总数", value: stats.accounts.total, sub:`${stats.accounts.active} 个有效`, color:"text-blue-400" },
          { label:"有效账号", value: stats.accounts.active, sub:`共 ${stats.accounts.total} 个`, color:"text-emerald-400" },
          { label:"身份信息", value: stats.identities.total, sub:"条记录", color:"text-amber-400" },
          { label:"临时邮箱", value: stats.emails.total, sub:"个邮箱", color:"text-purple-400" },
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
  const [importPlatform, setImportPlatform] = useState<Platform>("outlook");
  const [importDelimiter, setImportDelimiter] = useState("----");
  const [form, setForm] = useState({ platform: "outlook", email: "", password: "", username: "", token: "", status: "active", notes: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    const q = new URLSearchParams();
    if (filter.platform) q.set("platform", filter.platform);
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
          {PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={filter.status} onChange={e => setFilter(f => ({...f,status:e.target.value}))} className="bg-[#161b22] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white">
          <option value="">全部状态</option>
          <option value="active">有效</option>
          <option value="inactive">已失效</option>
          <option value="banned">已封禁</option>
        </select>
        <input value={filter.search} onChange={e => setFilter(f => ({...f,search:e.target.value}))} placeholder="搜索 email/备注…" className="bg-[#161b22] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white placeholder-gray-600 flex-1 min-w-32" />
        <div className="flex gap-1 ml-auto">
          <button onClick={() => exportAccounts("txt")} className="px-3 py-1.5 bg-[#21262d] border border-[#30363d] rounded text-xs text-gray-300 hover:bg-[#30363d]">导出 TXT</button>
          <button onClick={() => exportAccounts("csv")} className="px-3 py-1.5 bg-[#21262d] border border-[#30363d] rounded text-xs text-gray-300 hover:bg-[#30363d]">导出 CSV</button>
          <button onClick={() => exportAccounts("json")} className="px-3 py-1.5 bg-[#21262d] border border-[#30363d] rounded text-xs text-gray-300 hover:bg-[#30363d]">导出 JSON</button>
          <button onClick={() => setShowImport(true)} className="px-3 py-1.5 bg-[#1f6feb] rounded text-xs text-white hover:bg-blue-600">批量导入</button>
          <button onClick={() => setShowAdd(true)} className="px-3 py-1.5 bg-emerald-700 rounded text-xs text-white hover:bg-emerald-600">+ 添加账号</button>
        </div>
      </div>

      {msg && <p className={`text-sm px-3 py-2 rounded ${msg.startsWith("✅") ? "bg-emerald-900/40 text-emerald-300" : "bg-red-900/40 text-red-300"}`}>{msg}</p>}

      {/* 添加弹窗 */}
      {showAdd && (
        <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-4 space-y-3">
          <h3 className="text-sm font-semibold text-white">添加账号</h3>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-gray-400">平台</label>
              <select value={form.platform} onChange={e => setForm(f=>({...f,platform:e.target.value}))} className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-white mt-1">
                {PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
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
        <div className="grid grid-cols-[80px_1fr_1fr_90px_70px_60px] gap-2 px-3 py-2 bg-[#21262d] text-xs text-gray-500 font-medium">
          <span>平台</span><span>邮箱</span><span>密码/备注</span><span>状态</span><span>创建时间</span><span></span>
        </div>
        {accounts.length === 0 && (
          <p className="text-center text-gray-600 text-sm py-8">暂无账号，点击「添加账号」或「批量导入」</p>
        )}
        {accounts.map(a => (
          <div key={a.id} className="grid grid-cols-[80px_1fr_1fr_90px_70px_60px] gap-2 px-3 py-2 border-t border-[#21262d] text-xs hover:bg-[#21262d]/50 group items-center">
            <span className={`font-medium ${PLATFORM_COLORS[a.platform] ?? "text-gray-400"}`}>{a.platform}</span>
            <span className="text-white font-mono truncate">{a.email}</span>
            <span className="text-gray-400 font-mono truncate">{a.notes || a.password}</span>
            <span className={`px-2 py-0.5 rounded-full text-center w-fit ${a.status === "active" ? "bg-emerald-900/40 text-emerald-400" : a.status === "banned" ? "bg-red-900/40 text-red-400" : "bg-gray-800 text-gray-500"}`}>{a.status === "active" ? "有效" : a.status === "banned" ? "封禁" : "失效"}</span>
            <span className="text-gray-600">{formatDate(a.created_at).split(" ")[0]}</span>
            <button onClick={() => deleteAccount(a.id)} className="text-red-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity text-xs">删除</button>
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-600 text-right">共 {accounts.length} 条</p>
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

      {msg && <p className={`text-sm px-3 py-2 rounded ${msg.startsWith("✅") ? "bg-emerald-900/40 text-emerald-300" : "bg-red-900/40 text-red-300"}`}>{msg}</p>}

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

// ─── Temp Emails ─────────────────────────────────────────────────────────────
function EmailsPanel() {
  const [emails, setEmails] = useState<TempEmail[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ address:"", password:"", provider:"mailtm", token:"", notes:"" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [showToken, setShowToken] = useState<number | null>(null);

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
      <div className="flex gap-2">
        <div className="flex-1" />
        <button onClick={exportEmails} className="px-3 py-1.5 bg-[#21262d] border border-[#30363d] rounded text-xs text-gray-300 hover:bg-[#30363d]">导出 TXT</button>
        <button onClick={() => setShowAdd(true)} className="px-3 py-1.5 bg-emerald-700 rounded text-xs text-white hover:bg-emerald-600">+ 添加邮箱</button>
      </div>

      {msg && <p className={`text-sm px-3 py-2 rounded ${msg.startsWith("✅") ? "bg-emerald-900/40 text-emerald-300" : "bg-red-900/40 text-red-300"}`}>{msg}</p>}

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
        <div className="grid grid-cols-[1fr_80px_1fr_80px_60px] gap-2 px-3 py-2 bg-[#21262d] text-xs text-gray-500 font-medium">
          <span>邮箱地址</span><span>服务商</span><span>密码</span><span>状态</span><span></span>
        </div>
        {emails.length === 0 && <p className="text-center text-gray-600 text-sm py-8">暂无邮箱记录</p>}
        {emails.map(e => (
          <div key={e.id} className="grid grid-cols-[1fr_80px_1fr_80px_60px] gap-2 px-3 py-2 border-t border-[#21262d] text-xs hover:bg-[#21262d]/50 group items-center">
            <div className="flex items-center gap-2">
              <span className="text-white font-mono">{e.address}</span>
              {e.token && <button onClick={() => setShowToken(showToken === e.id ? null : e.id)} className="text-gray-600 hover:text-gray-400 text-xs">Token</button>}
            </div>
            <span className="text-gray-400">{e.provider}</span>
            <span className="text-gray-400 font-mono">{e.password}</span>
            <span className={`px-2 py-0.5 rounded-full w-fit ${e.status === "active" ? "bg-emerald-900/40 text-emerald-400" : "bg-gray-800 text-gray-500"}`}>{e.status === "active" ? "有效" : "失效"}</span>
            <button onClick={() => deleteEmail(e.id)} className="text-red-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity text-xs">删除</button>
            {showToken === e.id && e.token && (
              <div className="col-span-5 bg-[#0d1117] rounded p-2 font-mono text-xs text-gray-400 break-all">{e.token}</div>
            )}
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-600 text-right">共 {emails.length} 条</p>
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

// ─── Main Page ───────────────────────────────────────────────────────────────
export default function DataManager() {
  const [tab, setTab] = useState<Tab>("stats");

  const TABS: { key: Tab; label: string }[] = [
    { key:"stats",      label:"📊 数据统计" },
    { key:"accounts",   label:"🔑 账号库" },
    { key:"identities", label:"🪪 身份库" },
    { key:"emails",     label:"📬 邮箱库" },
    { key:"configs",    label:"⚙️ 系统配置" },
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

      {tab === "stats"      && <StatsPanel />}
      {tab === "accounts"   && <AccountsPanel />}
      {tab === "identities" && <IdentitiesPanel />}
      {tab === "emails"     && <EmailsPanel />}
      {tab === "configs"    && <ConfigsPanel />}
    </div>
  );
}
