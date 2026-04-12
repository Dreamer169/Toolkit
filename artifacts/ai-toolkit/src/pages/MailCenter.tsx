import { useState, useEffect, useRef, useCallback } from "react";

const API = "/api";
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
interface VerifyResult {
  id: number;
  email: string;
  status: string; // valid | not_exist | wrong_password | need_mfa | blocked_ca | error | no_password
  error?: string;
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
interface DeviceState {
  userCode: string;
  verificationUri: string;
  deviceCode: string;
}

function extractCode(text: string): string {
  const m6  = text.match(/\b(\d{6,8})\b/);
  const mAZ = text.match(/\b([A-Z0-9]{6,10})\b/);
  return m6 ? m6[1] : mAZ ? mAZ[1] : "";
}

function fmtDate(iso: string) {
  const d    = new Date(iso);
  const now  = new Date();
  const diff = Math.floor((now.getTime() - d.getTime()) / 86400000);
  if (diff === 0) return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  if (diff  < 7)  return d.toLocaleDateString("zh-CN", { weekday: "short", hour: "2-digit", minute: "2-digit" });
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
  const [authBusy, setAuthBusy]         = useState<number | "all" | null>(null);
  const [error, setError]               = useState("");
  const [needsAuth, setNeedsAuth]       = useState(false);
  const [authError, setAuthError]       = useState("");
  const [authOk, setAuthOk]             = useState("");
  const [showDevice, setShowDevice]     = useState(false);
  const [device, setDevice]             = useState<DeviceState | null>(null);
  const [polling, setPolling]           = useState(false);
  const [copied, setCopied]             = useState("");
  const [batchResults, setBatchResults] = useState<{ email: string; ok: boolean; error?: string }[]>([]);
  const [verifyResults, setVerifyResults] = useState<VerifyResult[]>([]);
  const [verifying, setVerifying]         = useState(false);
  const pollRef                           = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadAccounts = useCallback(async () => {
    const d = await fetch(`${API}/tools/outlook/accounts`).then(r => r.json()).catch(() => ({}));
    if (d.success) setAccounts(d.accounts ?? []);
  }, []);

  useEffect(() => { loadAccounts(); }, [loadAccounts]);

  const fetchMessages = useCallback(async (acc: Account, fld: string, q: string) => {
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
    resetAuthState();
    fetchMessages(acc, folder, search);
  };

  const changeFolder = (fld: string) => {
    setFolder(fld);
    if (selAccount) fetchMessages(selAccount, fld, search);
  };

  const doSearch = () => { if (selAccount) fetchMessages(selAccount, folder, search); };

  const copy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(""), 2000);
  };

  const resetAuthState = () => {
    setAuthError(""); setAuthOk(""); setShowDevice(false);
    setDevice(null); setPolling(false);
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  // ── ROPC 一键自动授权 ────────────────────────────────────────────────────
  const autoAuth = async (acc: Account) => {
    setAuthBusy(acc.id); setAuthError(""); setAuthOk("");
    const d = await fetch(`${API}/tools/outlook/auto-auth`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accountId: acc.id }),
    }).then(r => r.json()).catch(() => ({ success: false, error: "网络错误" }));
    setAuthBusy(null);
    if (d.success) {
      setAuthOk("授权成功！正在加载邮件…");
      setNeedsAuth(false);
      await loadAccounts();
      const updated = { ...acc, token: "ok", refresh_token: null };
      setSelAccount(updated);
      fetchMessages(updated, folder, search);
    } else {
      setAuthError(d.error ?? "授权失败");
    }
  };

  // ── 批量一键授权全部未授权账号 ────────────────────────────────────────────
  const autoAuthAll = async () => {
    setAuthBusy("all"); setBatchResults([]);
    const d = await fetch(`${API}/tools/outlook/auto-auth-all`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }).then(r => r.json()).catch(() => ({ success: false, error: "网络错误" }));
    setAuthBusy(null);
    if (d.success) {
      setBatchResults(d.results ?? []);
      await loadAccounts();
    } else {
      setBatchResults([{ email: "全部", ok: false, error: d.error }]);
    }
  };

  // ── 批量验证账号 ────────────────────────────────────────────────────
  const verifyAll = async () => {
    setVerifying(true); setVerifyResults([]);
    const d = await fetch(`${API}/tools/outlook/verify-accounts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }).then(r => r.json()).catch(() => ({ success: false }));
    setVerifying(false);
    if (d.success) {
      setVerifyResults(d.results ?? []);
      await loadAccounts();
    }
  };

  const verifySingle = async (acc: Account) => {
    setVerifying(true);
    const d = await fetch(`${API}/tools/outlook/verify-accounts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: [acc.id] }),
    }).then(r => r.json()).catch(() => ({ success: false }));
    setVerifying(false);
    if (d.success && d.results?.length) {
      setVerifyResults(prev => {
        const next = prev.filter(v => v.id !== acc.id);
        return [...next, ...d.results];
      });
      await loadAccounts();
      if (d.results[0]?.status === "valid") {
        const updated = { ...acc, token: "ok" };
        setSelAccount(updated);
        fetchMessages(updated, folder, search);
      }
    }
  };

  const verifyStatus = (id: number) => verifyResults.find(v => v.id === id);
  const verifyBadge = (st: string) => {
    const map: Record<string, { label: string; cls: string }> = {
      valid:            { label: "IMAP✓",   cls: "text-emerald-400" },
      wrong_password:   { label: "密码错",  cls: "text-red-400" },
      imap_disabled:    { label: "IMAP关闭", cls: "text-amber-400" },
      connection_error: { label: "连接失败", cls: "text-red-400" },
      error:            { label: "错误",    cls: "text-gray-500" },
      no_password:      { label: "无密码",  cls: "text-gray-500" },
    };
    return map[st] ?? { label: st, cls: "text-gray-500" };
  };

  // ── 设备码手动授权（ROPC 失败时备用）────────────────────────────────────
  const startDevice = async (acc: Account) => {
    setAuthBusy(acc.id); setAuthError("");
    const d = await fetch(`${API}/tools/outlook/device-code`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clientId: "9e5f94bc-e8a4-4e73-b8be-63364c29d753" }),
    }).then(r => r.json()).catch(() => null);
    setAuthBusy(null);
    if (!d?.success) { setAuthError(d?.error ?? "获取设备码失败"); return; }
    setDevice({ userCode: d.userCode, verificationUri: d.verificationUri, deviceCode: d.deviceCode });
    setPolling(true);
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const p = await fetch(`${API}/tools/outlook/device-poll`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deviceCode: d.deviceCode, clientId: "9e5f94bc-e8a4-4e73-b8be-63364c29d753" }),
      }).then(r => r.json()).catch(() => null);
      if (!p) return;
      if (p.success && p.accessToken) {
        clearInterval(pollRef.current!); pollRef.current = null; setPolling(false);
        await fetch(`${API}/tools/outlook/save-token`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: acc.email, token: p.accessToken, refreshToken: p.refreshToken }),
        });
        setAuthOk("授权成功！正在加载邮件…"); setNeedsAuth(false);
        await loadAccounts();
        const updated = { ...acc, token: p.accessToken, refresh_token: p.refreshToken };
        setSelAccount(updated);
        fetchMessages(updated, folder, search);
      } else if (p.error && p.error !== "authorization_pending" && p.error !== "slow_down") {
        clearInterval(pollRef.current!); pollRef.current = null; setPolling(false);
        setAuthError(p.errorDescription ?? p.error);
      }
    }, 4000);
  };

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  // 有 OAuth token → Graph API（最快）
  const hasOAuth  = (acc: Account) => !!(acc.token || acc.refresh_token);
  // 有密码但无 token → IMAP 直连（自动，无需额外授权）
  const hasImap   = (acc: Account) => !hasOAuth(acc) && !!acc.password;
  // 既无 token 也无密码 → 需要手动授权
  const authorized = (acc: Account) => hasOAuth(acc) || hasImap(acc);
  const unAuthCount = accounts.filter(a => hasOAuth(a) === false && hasImap(a) === false).length;

  return (
    <div className="flex h-[calc(100vh-56px)] overflow-hidden text-sm text-gray-200">

      {/* ─── 左列：账号列表 ─────────────────────────────────────────── */}
      <aside className="w-60 shrink-0 border-r border-[#21262d] flex flex-col bg-[#0d1117]">
        <div className="px-3 py-2.5 border-b border-[#21262d] space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Outlook 账号</span>
            <span className="text-xs text-gray-600">{accounts.length} 个</span>
          </div>
          <div className="flex gap-1.5">
            <button
              onClick={verifyAll}
              disabled={verifying || accounts.length === 0}
              className="flex-1 py-1.5 bg-blue-600/60 hover:bg-blue-600/80 disabled:opacity-50 rounded text-xs text-white font-medium transition-colors"
            >
              {verifying ? "验证中…" : "🔍 批量验证"}
            </button>
            {verifyResults.length > 0 && (
              <button
                onClick={() => setVerifyResults([])}
                className="px-2 py-1.5 bg-[#21262d] hover:bg-[#30363d] rounded text-xs text-gray-400 transition-colors"
                title="清除验证结果"
              >✕</button>
            )}
          </div>
          {batchResults.length > 0 && (
            <div className="space-y-0.5 max-h-24 overflow-y-auto">
              {batchResults.map((r, i) => (
                <div key={i} className={`text-[10px] truncate px-1 py-0.5 rounded ${r.ok ? "text-emerald-400" : "text-red-400"}`}>
                  {r.ok ? "✓" : "✗"} {r.email}{!r.ok && r.error ? `: ${r.error.slice(0, 40)}` : ""}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {accounts.length === 0 && (
            <p className="text-xs text-gray-600 text-center mt-8 px-4">暂无 Outlook 账号<br/>去「Outlook 工作流」注册</p>
          )}
          {accounts.map((acc) => {
            const active = selAccount?.id === acc.id;
            const isOAuth  = hasOAuth(acc);
            const isImap   = hasImap(acc);
            const noAccess = !isOAuth && !isImap;
            const dot = isOAuth ? "bg-emerald-400" : isImap ? "bg-blue-400" : "bg-amber-400";
            const label = isOAuth ? "OAuth" : isImap ? "IMAP" : "需授权";
            const labelCls = isOAuth ? "text-emerald-500" : isImap ? "text-blue-400" : "text-amber-500";
            const vr = verifyStatus(acc.id);
            const vb = vr ? verifyBadge(vr.status) : null;
            const pwKey = `pw-${acc.id}`;
            const pwCopied = copied === pwKey;
            return (
              <div key={acc.id} className={`border-b border-[#161b22] ${active ? "bg-blue-600/15" : ""} ${noAccess ? "opacity-70" : ""}`}>
                <button onClick={() => selectAccount(acc)}
                  className={`w-full text-left px-3 pt-2.5 pb-1.5 transition-colors border-l-2 ${
                    active ? "border-l-blue-500" : "hover:bg-[#161b22] border-l-transparent"
                  }`}>
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
                    <span className="text-xs font-mono truncate text-gray-200">{acc.email}</span>
                  </div>
                  <div className="flex items-center gap-2 mt-0.5 ml-3">
                    <span className={`text-[10px] font-medium ${labelCls}`}>{label}</span>
                    {vb && <span className={`text-[10px] ${vb.cls}`}>· {vb.label}</span>}
                    <span className="text-[10px] text-gray-600 ml-auto">
                      {new Date(acc.created_at).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" })}
                    </span>
                  </div>
                </button>
                {acc.password && (
                  <button
                    onClick={e => {
                      e.stopPropagation();
                      navigator.clipboard.writeText(acc.password);
                      setCopied(pwKey);
                      setTimeout(() => setCopied(c => c === pwKey ? "" : c), 1500);
                    }}
                    className="w-full text-left px-3 pb-2 flex items-center gap-1.5 group/pw"
                  >
                    <span className="text-[10px] text-gray-600">🔑</span>
                    <span className={`text-[10px] font-mono truncate transition-colors ${pwCopied ? "text-emerald-400" : "text-gray-500 group-hover/pw:text-gray-300"}`}>
                      {pwCopied ? "已复制 ✓" : acc.password}
                    </span>
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </aside>

      {/* ─── 中列：邮件列表 ─────────────────────────────────────────── */}
      <section className="w-72 shrink-0 border-r border-[#21262d] flex flex-col bg-[#0d1117]">
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

        <div className="flex-1 overflow-y-auto">
          {!selAccount && (
            <p className="text-xs text-gray-600 text-center mt-10 px-4">← 选择左侧账号查看邮件</p>
          )}

          {/* ── 未授权面板 ── */}
          {selAccount && needsAuth && (
            <div className="p-3 space-y-2">
              {authOk  && <p className="text-xs text-emerald-400">✅ {authOk}</p>}
              {authError && (
                <div className="bg-red-900/20 border border-red-700/30 rounded p-2">
                  <p className="text-[11px] text-red-400 break-all">{authError}</p>
                </div>
              )}

              {!authOk && (
                <>
                  <p className="text-xs text-amber-400">该账号尚未授权，无法读取邮件。</p>

                  <div className="flex gap-1.5">
                    {/* 主按钮：ROPC 一键授权 */}
                    <button
                      onClick={() => autoAuth(selAccount)}
                      disabled={authBusy === selAccount.id || verifying}
                      className="flex-1 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 rounded text-xs text-white font-semibold transition-colors"
                    >
                      {authBusy === selAccount.id ? "授权中…" : "⚡ 一键授权"}
                    </button>
                    {/* 验证按钮 */}
                    <button
                      onClick={() => verifySingle(selAccount)}
                      disabled={verifying || authBusy === selAccount.id}
                      className="px-3 py-2 bg-blue-600/60 hover:bg-blue-600/80 disabled:opacity-50 rounded text-xs text-white transition-colors"
                    >
                      {verifying ? "…" : "🔍 验证"}
                    </button>
                  </div>

                  {/* 验证结果 */}
                  {verifyStatus(selAccount.id) && (() => {
                    const vr = verifyStatus(selAccount.id)!;
                    const vb = verifyBadge(vr.status);
                    return (
                      <div className={`text-[11px] px-2 py-1 rounded bg-[#161b22] border border-[#30363d] ${vb.cls}`}>
                        验证结果：{vb.label}{vr.error ? ` — ${vr.error.slice(0, 60)}` : ""}
                      </div>
                    );
                  })()}

                  {/* 展开手动设备码授权 */}
                  {!showDevice && (
                    <button onClick={() => { setShowDevice(true); setAuthError(""); }}
                      className="w-full py-1 text-[11px] text-gray-500 hover:text-gray-300 underline">
                      一键授权失败？点这里手动授权
                    </button>
                  )}

                  {showDevice && !device && (
                    <button
                      onClick={() => startDevice(selAccount)}
                      disabled={authBusy === selAccount.id}
                      className="w-full py-1.5 bg-blue-600/80 hover:bg-blue-600 disabled:opacity-50 rounded text-xs text-white transition-colors"
                    >
                      {authBusy === selAccount.id ? "获取中…" : "获取设备码"}
                    </button>
                  )}

                  {device && (
                    <div className="bg-[#161b22] border border-[#30363d] rounded p-2 space-y-2">
                      <p className="text-[10px] text-gray-400">1. 打开链接</p>
                      <a href={device.verificationUri} target="_blank" rel="noopener noreferrer"
                        className="text-[10px] text-blue-400 underline break-all">{device.verificationUri}</a>
                      <p className="text-[10px] text-gray-400">2. 输入设备码：</p>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-lg font-bold text-white tracking-widest">{device.userCode}</span>
                        <button onClick={() => copy(device.userCode, "dcode")}
                          className="text-[10px] px-1.5 py-0.5 bg-[#21262d] rounded text-gray-400 hover:text-white">
                          {copied === "dcode" ? "✓" : "复制"}
                        </button>
                      </div>
                      {polling && <p className="text-[10px] text-gray-500 animate-pulse">等待授权确认…</p>}
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {selAccount && !needsAuth && error && (
            <div className="p-3 space-y-2">
              {/BasicAuthBlocked|LOGIN failed|基础认证|basic auth/i.test(error) ? (
                <div className="bg-amber-900/20 border border-amber-700/40 rounded-lg p-3 space-y-2">
                  <p className="text-xs text-amber-400 font-medium">⚠ 微软已封锁此账号的 IMAP 基础认证</p>
                  <p className="text-[11px] text-gray-400 leading-5">
                    自 2023 年起，微软对 Outlook.com 个人账号强制要求现代身份验证。<br/>
                    解决方案：<br/>
                    1. 登录 <span className="text-blue-400">outlook.live.com</span><br/>
                    2. 设置 → 邮件 → 同步邮件 → 将「允许使用 IMAP 的设备和应用」设为开<br/>
                    3. 或使用「获取授权」完成 OAuth 登录后即可通过 Graph API 读取邮件
                  </p>
                </div>
              ) : (
                <p className="text-xs text-red-400">{error}</p>
              )}
            </div>
          )}

          {busy && (
            <div className="flex items-center justify-center mt-10">
              <span className="text-xs text-gray-500 animate-pulse">加载中…</span>
            </div>
          )}

          {!busy && messages.map((m) => {
            const code     = extractCode(m.preview + " " + m.subject);
            const isActive = selMsg?.id === m.id;
            return (
              <button key={m.id} onClick={() => setSelMsg(isActive ? null : m)}
                className={`w-full text-left px-3 py-2.5 border-b border-[#21262d] transition-colors ${
                  isActive ? "bg-blue-600/10 border-l-2 border-l-blue-500" : "hover:bg-[#161b22] border-l-2 border-l-transparent"
                }`}>
                <div className="flex items-start gap-1.5">
                  <span className={`w-1.5 h-1.5 rounded-full mt-1 shrink-0 ${m.isRead ? "opacity-0" : "bg-blue-400"}`} />
                  <div className="flex-1 min-w-0">
                    <p className={`text-xs truncate ${m.isRead ? "text-gray-400" : "text-gray-100 font-medium"}`}>
                      {m.subject}
                    </p>
                    <div className="flex items-center justify-between mt-0.5 gap-1">
                      <span className="text-[10px] text-gray-600 truncate">{m.fromName || m.from}</span>
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

      {/* ─── 右列：邮件详情 ─────────────────────────────────────────── */}
      <main className="flex-1 flex flex-col bg-[#0d1117] overflow-hidden">
        {!selMsg && (
          <div className="flex-1 flex items-center justify-center">
            <p className="text-xs text-gray-600">← 选择邮件查看内容</p>
          </div>
        )}
        {selMsg && (
          <>
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
