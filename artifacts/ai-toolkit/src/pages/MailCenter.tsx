import { useState, useEffect, useRef, useCallback } from "react";

const API = "/api";
const CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";
const FOLDERS = [
  { id: "inbox",        label: "收件箱" },
  { id: "sentItems",    label: "已发送" },
  { id: "junkemail",    label: "垃圾邮件" },
  { id: "drafts",       label: "草稿" },
  { id: "deleteditems", label: "已删除" },
];

interface Account {
  id: number;
  email: string;
  password: string;
  token: string | null;
  refresh_token: string | null;
  status: string;
  created_at: string;
}
interface MailMsg {
  id: string;
  subject: string;
  from: string;
  fromName: string;
  receivedAt: string;
  preview: string;
  body: string;
  bodyType: string;
  isRead: boolean;
}
interface AuthState {
  step: "idle" | "polling" | "done" | "error";
  userCode?: string;
  verificationUri?: string;
  deviceCode?: string;
  msg?: string;
}

function extractCode(text: string): string {
  const m6  = text.match(/\b(\d{6,8})\b/);
  const mAZ = text.match(/\b([A-Z0-9]{6,10})\b/);
  return (m6 ? m6[1] : mAZ ? mAZ[1] : "");
}

function fmtDate(iso: string) {
  const d = new Date(iso);
  const now = new Date();
  const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
  if (diffDays === 0)
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  if (diffDays < 7)
    return d.toLocaleDateString("zh-CN", { weekday: "short", hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

export default function MailCenter() {
  const [accounts, setAccounts]         = useState<Account[]>([]);
  const [selAccount, setSelAccount]     = useState<Account | null>(null);
  const [folder, setFolder]             = useState("inbox");
  const [messages, setMessages]         = useState<MailMsg[]>([]);
  const [selMsg, setSelMsg]             = useState<MailMsg | null>(null);
  const [search, setSearch]             = useState("");
  const [busy, setBusy]                 = useState(false);
  const [error, setError]               = useState("");
  const [needsAuth, setNeedsAuth]       = useState(false);
  const [auth, setAuth]                 = useState<AuthState>({ step: "idle" });
  const [copied, setCopied]             = useState("");
  const pollRef                         = useRef<ReturnType<typeof setInterval> | null>(null);

  // 加载账号列表
  const loadAccounts = useCallback(async () => {
    const d = await fetch(`${API}/tools/outlook/accounts`).then(r => r.json()).catch(() => ({}));
    if (d.success) setAccounts(d.accounts ?? []);
  }, []);

  useEffect(() => { loadAccounts(); }, [loadAccounts]);

  // 切账号/切文件夹/搜索 → 拉邮件
  const fetchMessages = useCallback(async (acc: Account, fld: string, q: string) => {
    if (!acc) return;
    setBusy(true); setError(""); setNeedsAuth(false); setMessages([]); setSelMsg(null);
    const d = await fetch(`${API}/tools/outlook/fetch-messages-by-id`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accountId: acc.id, folder: fld, top: 50, search: q || undefined }),
    }).then(r => r.json()).catch(() => ({ success: false, error: "网络错误" }));
    setBusy(false);
    if (d.success) {
      setMessages(d.messages ?? []);
    } else {
      setError(d.error ?? "获取失败");
      if (d.needsAuth) setNeedsAuth(true);
    }
  }, []);

  const selectAccount = (acc: Account) => {
    setSelAccount(acc);
    setSelMsg(null);
    setAuth({ step: "idle" });
    setNeedsAuth(false);
    fetchMessages(acc, folder, search);
  };

  const changeFolder = (fld: string) => {
    setFolder(fld);
    if (selAccount) fetchMessages(selAccount, fld, search);
  };

  const doSearch = () => {
    if (selAccount) fetchMessages(selAccount, folder, search);
  };

  const copy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(""), 2000);
  };

  // ── OAuth device code 流程 ─────────────────────────────────────────────
  const startAuth = async () => {
    if (!selAccount) return;
    setAuth({ step: "polling", msg: "请求设备码…" });
    const d = await fetch(`${API}/tools/outlook/device-code`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clientId: CLIENT_ID }),
    }).then(r => r.json()).catch(() => null);
    if (!d?.success) { setAuth({ step: "error", msg: d?.error ?? "请求失败" }); return; }
    setAuth({ step: "polling", userCode: d.userCode, verificationUri: d.verificationUri, deviceCode: d.deviceCode });
    // 开始轮询
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const p = await fetch(`${API}/tools/outlook/device-poll`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deviceCode: d.deviceCode, clientId: CLIENT_ID }),
      }).then(r => r.json()).catch(() => null);
      if (!p) return;
      if (p.success && p.accessToken) {
        clearInterval(pollRef.current!);
        // 保存 token
        await fetch(`${API}/tools/outlook/save-token`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: selAccount.email, token: p.accessToken, refreshToken: p.refreshToken }),
        });
        setAuth({ step: "done", msg: "授权成功！" });
        await loadAccounts();
        const updated = { ...selAccount, token: p.accessToken, refresh_token: p.refreshToken };
        setSelAccount(updated);
        fetchMessages(updated, folder, search);
      } else if (p.error && p.error !== "authorization_pending" && p.error !== "slow_down") {
        clearInterval(pollRef.current!);
        setAuth({ step: "error", msg: p.errorDescription ?? p.error });
      }
    }, 4000);
  };

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const authorized = (acc: Account) => !!(acc.token || acc.refresh_token);

  return (
    <div className="flex h-[calc(100vh-56px)] overflow-hidden text-sm text-gray-200">

      {/* ── 左列：账号列表 ─────────────────────────────────────────── */}
      <aside className="w-60 shrink-0 border-r border-[#21262d] flex flex-col bg-[#0d1117]">
        <div className="px-3 py-3 border-b border-[#21262d] flex items-center justify-between">
          <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Outlook 账号</span>
          <span className="text-xs text-gray-600">{accounts.length}</span>
        </div>
        <div className="flex-1 overflow-y-auto">
          {accounts.length === 0 && (
            <p className="text-xs text-gray-600 text-center mt-8 px-4">暂无 Outlook 账号<br/>去「批量注册」先注册几个</p>
          )}
          {accounts.map((acc) => {
            const active = selAccount?.id === acc.id;
            const ok     = authorized(acc);
            return (
              <button
                key={acc.id}
                onClick={() => selectAccount(acc)}
                className={`w-full text-left px-3 py-2.5 border-b border-[#161b22] transition-colors ${
                  active ? "bg-blue-600/15 border-l-2 border-l-blue-500" : "hover:bg-[#161b22] border-l-2 border-l-transparent"
                }`}
              >
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${ok ? "bg-emerald-400" : "bg-amber-400"}`} />
                  <span className="text-xs font-mono truncate text-gray-200">{acc.email}</span>
                </div>
                <div className="flex items-center gap-2 mt-0.5 ml-3">
                  <span className={`text-[10px] ${ok ? "text-emerald-500" : "text-amber-500"}`}>
                    {ok ? "已授权" : "未授权"}
                  </span>
                  <span className="text-[10px] text-gray-600 ml-auto">
                    {new Date(acc.created_at).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" })}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      </aside>

      {/* ── 中列：邮件列表 ─────────────────────────────────────────── */}
      <section className="w-72 shrink-0 border-r border-[#21262d] flex flex-col bg-[#0d1117]">
        {/* 文件夹 tabs */}
        <div className="px-2 pt-2 pb-1 border-b border-[#21262d] flex gap-1 flex-wrap">
          {FOLDERS.map(f => (
            <button key={f.id} onClick={() => changeFolder(f.id)}
              className={`text-[10px] px-2 py-0.5 rounded-full border transition-colors ${
                folder === f.id
                  ? "bg-blue-600/20 border-blue-500/50 text-blue-300"
                  : "border-transparent text-gray-500 hover:text-gray-300 hover:border-[#30363d]"
              }`}>
              {f.label}
            </button>
          ))}
        </div>
        {/* 搜索栏 */}
        <div className="px-2 py-2 border-b border-[#21262d] flex gap-1">
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            onKeyDown={e => e.key === "Enter" && doSearch()}
            placeholder="搜索主题/发件人…"
            className="flex-1 bg-[#161b22] border border-[#30363d] rounded px-2 py-1 text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-blue-500"
          />
          <button onClick={doSearch} disabled={busy || !selAccount}
            className="px-2 py-1 bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] rounded text-gray-400 text-xs disabled:opacity-40 transition-colors">
            {busy ? "…" : "搜"}
          </button>
        </div>
        {/* 邮件条目 */}
        <div className="flex-1 overflow-y-auto">
          {!selAccount && (
            <p className="text-xs text-gray-600 text-center mt-10 px-4">← 选择左侧账号查看邮件</p>
          )}

          {/* 未授权提示 + 一键授权 */}
          {selAccount && needsAuth && (
            <div className="p-3 space-y-2">
              <p className="text-xs text-amber-400">该账号尚未授权，无法读取邮件。</p>
              {auth.step === "idle" && (
                <button onClick={startAuth}
                  className="w-full py-1.5 bg-blue-600 hover:bg-blue-700 rounded text-xs text-white font-medium transition-colors">
                  开始 OAuth 授权
                </button>
              )}
              {auth.step === "polling" && auth.userCode && (
                <div className="bg-[#161b22] border border-[#30363d] rounded p-2 space-y-1.5">
                  <p className="text-[10px] text-gray-400">1. 点击链接打开授权页</p>
                  <a href={auth.verificationUri} target="_blank" rel="noopener noreferrer"
                    className="text-[10px] text-blue-400 underline break-all">{auth.verificationUri}</a>
                  <p className="text-[10px] text-gray-400">2. 输入设备码：</p>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-base font-bold text-white tracking-widest">{auth.userCode}</span>
                    <button onClick={() => copy(auth.userCode!, "code")}
                      className="text-[10px] px-1.5 py-0.5 bg-[#21262d] rounded text-gray-400 hover:text-white">
                      {copied === "code" ? "✓" : "复制"}
                    </button>
                  </div>
                  <p className="text-[10px] text-gray-500 animate-pulse">等待授权确认…</p>
                </div>
              )}
              {auth.step === "done" && <p className="text-xs text-emerald-400">✅ {auth.msg}</p>}
              {auth.step === "error" && <p className="text-xs text-red-400">❌ {auth.msg}</p>}
            </div>
          )}

          {selAccount && !needsAuth && error && (
            <div className="p-3">
              <p className="text-xs text-red-400">{error}</p>
              {!authorized(selAccount) && (
                <button onClick={startAuth} className="mt-2 w-full py-1 bg-blue-600 hover:bg-blue-700 rounded text-xs text-white">
                  去授权
                </button>
              )}
            </div>
          )}

          {busy && (
            <div className="flex items-center justify-center mt-10">
              <span className="text-xs text-gray-500 animate-pulse">加载中…</span>
            </div>
          )}

          {!busy && messages.map((m) => {
            const code  = extractCode(m.preview + " " + m.subject);
            const isActive = selMsg?.id === m.id;
            return (
              <button key={m.id} onClick={() => setSelMsg(isActive ? null : m)}
                className={`w-full text-left px-3 py-2.5 border-b border-[#21262d] transition-colors ${
                  isActive ? "bg-blue-600/10 border-l-2 border-l-blue-500" : "hover:bg-[#161b22] border-l-2 border-l-transparent"
                }`}>
                <div className="flex items-start gap-1.5">
                  {!m.isRead && <span className="w-1.5 h-1.5 rounded-full bg-blue-400 mt-1 shrink-0" />}
                  {m.isRead  && <span className="w-1.5 h-1.5 shrink-0" />}
                  <div className="flex-1 min-w-0">
                    <p className={`text-xs truncate ${m.isRead ? "text-gray-400" : "text-gray-100 font-medium"}`}>
                      {m.subject}
                    </p>
                    <div className="flex items-center justify-between mt-0.5 gap-1">
                      <span className="text-[10px] text-gray-600 truncate">
                        {m.fromName || m.from}
                      </span>
                      <span className="text-[10px] text-gray-600 shrink-0">{fmtDate(m.receivedAt)}</span>
                    </div>
                    {code && (
                      <button onClick={e => { e.stopPropagation(); copy(code, `c-${m.id}`); }}
                        className={`mt-1 text-[10px] px-1.5 py-0.5 rounded border font-mono font-bold ${
                          copied === `c-${m.id}`
                            ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400"
                            : "bg-yellow-500/10 border-yellow-500/20 text-yellow-400"
                        }`}>
                        {copied === `c-${m.id}` ? "✓ 已复制" : `验证码 ${code}`}
                      </button>
                    )}
                  </div>
                </div>
              </button>
            );
          })}

          {!busy && selAccount && !needsAuth && !error && messages.length === 0 && (
            <p className="text-xs text-gray-600 text-center mt-10 px-4">该文件夹暂无邮件</p>
          )}
        </div>
      </section>

      {/* ── 右列：邮件详情 ─────────────────────────────────────────── */}
      <main className="flex-1 flex flex-col bg-[#0d1117] overflow-hidden">
        {!selMsg && (
          <div className="flex-1 flex items-center justify-center">
            <p className="text-xs text-gray-600">← 选择左侧邮件查看内容</p>
          </div>
        )}
        {selMsg && (
          <>
            {/* 邮件头 */}
            <div className="px-5 py-4 border-b border-[#21262d] space-y-1.5 shrink-0">
              <div className="flex items-start justify-between gap-3">
                <h2 className="text-sm font-semibold text-white leading-snug">{selMsg.subject}</h2>
                <button onClick={() => setSelMsg(null)}
                  className="shrink-0 text-gray-500 hover:text-gray-300 text-xs px-2 py-0.5 rounded bg-[#21262d] hover:bg-[#30363d]">
                  关闭
                </button>
              </div>
              <div className="text-xs text-gray-500 space-y-0.5">
                <div><span className="text-gray-600">发件人：</span>{selMsg.fromName ? `${selMsg.fromName} <${selMsg.from}>` : selMsg.from}</div>
                <div className="flex items-center gap-3">
                  <span><span className="text-gray-600">时间：</span>{new Date(selMsg.receivedAt).toLocaleString("zh-CN")}</span>
                  {(() => {
                    const code = extractCode(selMsg.preview + " " + selMsg.subject + " " + (selMsg.body ?? ""));
                    if (!code) return null;
                    return (
                      <button onClick={() => copy(code, `h-${selMsg.id}`)}
                        className={`text-[11px] px-2 py-0.5 rounded border font-mono font-bold ${
                          copied === `h-${selMsg.id}`
                            ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400"
                            : "bg-yellow-500/10 border-yellow-500/20 text-yellow-400"
                        }`}>
                        {copied === `h-${selMsg.id}` ? "✓ 已复制" : `验证码 ${code}`}
                      </button>
                    );
                  })()}
                </div>
              </div>
            </div>
            {/* 邮件正文 */}
            <div className="flex-1 overflow-y-auto px-5 py-4">
              {selMsg.body ? (
                selMsg.bodyType === "html" ? (
                  <iframe
                    srcDoc={selMsg.body}
                    sandbox="allow-same-origin"
                    className="w-full rounded border border-[#21262d] bg-white"
                    style={{ minHeight: "400px", height: "100%" }}
                    title="邮件内容"
                  />
                ) : (
                  <pre className="text-xs text-gray-300 whitespace-pre-wrap leading-relaxed font-sans">
                    {selMsg.body}
                  </pre>
                )
              ) : (
                <p className="text-xs text-gray-500 leading-relaxed">{selMsg.preview}</p>
              )}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
