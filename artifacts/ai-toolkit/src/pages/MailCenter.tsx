import { useState, useEffect, useRef, useCallback } from "react";

const API = "/api";
const FOLDERS = [
  { id: "all",          label: "全部邮件" },
  { id: "inbox",        label: "收件箱" },
  { id: "sentItems",    label: "已发送" },
  { id: "junkemail",    label: "垃圾邮件" },
  { id: "archive",      label: "归档" },
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
  tags: string | null;
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
interface BatchOAuthAccount {
  accountId: number;
  email: string;
  userCode: string;
  verificationUri: string;
  deviceCode: string;   // stored client-side for direct polling — survives server restarts
  status: "pending" | "done" | "expired" | "error";
  errorMsg?: string;
}
interface BatchOAuthState {
  accounts: BatchOAuthAccount[];
  open: boolean;
}

function extractCode(text: string): string {
  const m6  = text.match(/\b(\d{6,8})\b/);
  const mAZ = text.match(/\b([A-Z0-9]{6,10})\b/);
  return m6 ? m6[1] : mAZ ? mAZ[1] : "";
}

// 给邮件 HTML 注入 <base target="_blank">，让所有链接强制在新标签页打开
function injectBaseTarget(html: string): string {
  const base = '<base target="_blank" rel="noopener noreferrer">';
  if (/<head[\s>]/i.test(html)) {
    return html.replace(/(<head[^>]*>)/i, `$1${base}`);
  }
  if (/<html[\s>]/i.test(html)) {
    return html.replace(/(<html[^>]*>)/i, `$1<head>${base}</head>`);
  }
  return base + html;
}

function fmtDate(iso: string) {
  const d    = new Date(iso);
  const now  = new Date();
  const diff = Math.floor((now.getTime() - d.getTime()) / 86400000);
  if (diff === 0) return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  if (diff  < 7)  return d.toLocaleDateString("zh-CN", { weekday: "short", hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}


const TAG_LABELS: Record<string, string> = {
  replit_used: "replit_used",
  abuse_mode: "API封禁",
  token_invalid: "token过期",
  inbox_error: "收件箱错误",
  needs_oauth_manual: "需手动授权",
  inbox_verified: "收件箱✓",
};
const TAG_CLASSES: Record<string, string> = {
  replit_used: "text-blue-300 bg-blue-950/40 border-blue-800/40",
  abuse_mode: "text-red-300 bg-red-950/40 border-red-800/40",
  token_invalid: "text-red-300 bg-red-950/40 border-red-800/40",
  inbox_error: "text-amber-300 bg-amber-950/40 border-amber-800/40",
  needs_oauth_manual: "text-violet-300 bg-violet-950/40 border-violet-800/40",
  inbox_verified: "text-emerald-300 bg-emerald-950/40 border-emerald-800/40",
};
function tagsOf(acc: Account): string[] {
  return Array.from(new Set((acc.tags ?? "").split(",").map(t => t.trim()).filter(Boolean)));
}
function hasTag(acc: Account, tag: string): boolean {
  return tagsOf(acc).includes(tag);
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
  const [batchResults, setBatchResults] = useState<{ email: string; ok: boolean; needsDeviceFlow?: boolean; error?: string; id?: number }[]>([]);
  const [verifyResults, setVerifyResults] = useState<VerifyResult[]>([]);
  const [verifying, setVerifying]         = useState(false);
  const [purging,   setPurging]           = useState(false);
  const [purgeStats, setPurgeStats]       = useState<{ valid: number; purged: number; kept: number } | null>(null);
  const [batchOAuth, setBatchOAuth]       = useState<BatchOAuthState | null>(null);
  const [batchOAuthBusy, setBatchOAuthBusy] = useState(false);
  const pollRef                           = useRef<ReturnType<typeof setInterval> | null>(null);
  const autoRefreshRef                    = useRef<ReturnType<typeof setInterval> | null>(null);
  const cdTimerRef                        = useRef<ReturnType<typeof setInterval> | null>(null);
  const [refreshCountdown, setRefreshCountdown] = useState<number>(6);
  const [retokenJobId,  setRetokenJobId]  = useState<string | null>(null);
  const [retokenBusy,   setRetokenBusy]   = useState(false);
  const [retokenLog,    setRetokenLog]     = useState<string[]>([]);
  const [retokenOpen,   setRetokenOpen]   = useState(false);

  const [markingRead,  setMarkingRead]  = useState(false);
  const [movingMsg,    setMovingMsg]    = useState(false);
  const [deletingMsg,  setDeletingMsg]  = useState(false);
  const [moveMenuOpen, setMoveMenuOpen] = useState(false);

  const batchPollRef                      = useRef<ReturnType<typeof setInterval> | null>(null);
  const [liveVerify, setLiveVerify]         = useState<{ enabled: boolean; lastRun: string | null; lastStats: { total: number; clicked: number; skipped: number; failed: number } } | null>(null);
  const searchRef = useRef(search);
  useEffect(() => { searchRef.current = search; }, [search]);
  const [autoRefreshError, setAutoRefreshError] = useState("");
  const [liveVerifyBusy, setLiveVerifyBusy] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [batchDelBusy, setBatchDelBusy]     = useState(false);
  const [batchDelKw, setBatchDelKw]         = useState("");
  const [batchDelResult, setBatchDelResult] = useState("");
  const [showManualAdd, setShowManualAdd]   = useState(false);
  const [manualForm, setManualForm]         = useState({ email: "", password: "", token: "" });
  const [manualBusy, setManualBusy]         = useState(false);
  const [manualMsg, setManualMsg]           = useState("");

  const loadAccounts = useCallback(async () => {
    const d = await fetch(`${API}/tools/outlook/accounts`).then(r => r.json()).catch(() => ({}));
    if (d.success) setAccounts(d.accounts ?? []);
  }, []);

  useEffect(() => { loadAccounts(); }, [loadAccounts]);

  // ── 实时验证轮询：加载状态 (v8.15 修复 toggle 覆盖) ─────────────────────
  // 用 ref 保存 toggle 后的"静默期"截止时间戳：toggle 后 5 秒内不接受
  // 来自 /status 的覆盖（避免轮询返回旧值反复盖住用户操作）。
  const liveVerifyMuteUntilRef = useRef<number>(0);
  const liveVerifyAbortRef     = useRef<AbortController | null>(null);

  const loadLiveVerifyStatus = useCallback(async () => {
    if (Date.now() < liveVerifyMuteUntilRef.current) return;  // 静默期内跳过
    liveVerifyAbortRef.current?.abort();
    const ctrl = new AbortController();
    liveVerifyAbortRef.current = ctrl;
    try {
      const d = await fetch(`${API}/tools/outlook/live-verify/status`, { signal: ctrl.signal }).then(r => r.json()).catch(() => null);
      if (Date.now() < liveVerifyMuteUntilRef.current) return; // 响应回来时已进入静默期，丢弃
      if (d?.success) setLiveVerify(d);
    } catch { /* aborted */ }
  }, []);

  const toggleLiveVerify = async () => {
    if (!liveVerify) return;
    setLiveVerifyBusy(true);
    // 立即 abort 在飞的 status 请求 + 设置 5s 静默期，防止旧响应覆盖
    liveVerifyAbortRef.current?.abort();
    liveVerifyMuteUntilRef.current = Date.now() + 5_000;
    const target = !liveVerify.enabled;
    // 乐观更新：UI 立刻翻转
    setLiveVerify({ ...liveVerify, enabled: target });
    const d = await fetch(`${API}/tools/outlook/live-verify/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: target }),
    }).then(r => r.json()).catch(() => null);
    setLiveVerifyBusy(false);
    if (d?.success) {
      setLiveVerify(d);
      // toggle 成功后保留 5s 静默期；之后 polling 自然恢复
    } else {
      // 失败回滚 UI
      setLiveVerify({ ...liveVerify, enabled: !target });
      liveVerifyMuteUntilRef.current = 0;
    }
  };

  useEffect(() => {
    loadLiveVerifyStatus();
    const iv = setInterval(loadLiveVerifyStatus, 3_000);
    return () => clearInterval(iv);
  }, [loadLiveVerifyStatus]);

  // 30s 自动轮询新邮件
  const silentRefresh = useCallback(async (acc: Account, fld: string, q: string) => {
    const d = await fetch(`${API}/tools/outlook/fetch-messages-by-id`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accountId: acc.id, folder: fld, top: 150, search: q || undefined }),
    }).then(r => r.json()).catch(() => null);
    if (d?.success) { setMessages(d.messages ?? []); setAutoRefreshError(""); }
    else if (d && !d.success) setAutoRefreshError(d.error ?? "刷新失败");
  }, []);

  useEffect(() => {
    if (autoRefreshRef.current) { clearInterval(autoRefreshRef.current); autoRefreshRef.current = null; }
    if (cdTimerRef.current)     { clearInterval(cdTimerRef.current);     cdTimerRef.current = null; }
    if (!selAccount) return;
    const ACC = selAccount;
    setRefreshCountdown(6);
    cdTimerRef.current = setInterval(() => setRefreshCountdown(c => (c <= 1 ? 6 : c - 1)), 1000);
    autoRefreshRef.current = setInterval(() => {
      setRefreshCountdown(6);
      silentRefresh(ACC, folder, searchRef.current);
    }, 6_000);
    return () => {
      if (autoRefreshRef.current) { clearInterval(autoRefreshRef.current); autoRefreshRef.current = null; }
      if (cdTimerRef.current)     { clearInterval(cdTimerRef.current);     cdTimerRef.current = null; }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selAccount, folder, silentRefresh]);

  const fetchMessages = useCallback(async (acc: Account, fld: string, q: string) => {
    setBusy(true); setError(""); setNeedsAuth(false); setMessages([]); setSelMsg(null);
    const d = await fetch(`${API}/tools/outlook/fetch-messages-by-id`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accountId: acc.id, folder: fld, top: 150, search: q || undefined }),
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
      const updated = { ...acc, token: "ok", refresh_token: acc.refresh_token };
      setSelAccount(updated);
      fetchMessages(updated, folder, search);
    } else if (d.needsDeviceFlow) {
      // ROPC 已被微软封锁，自动切换到设备码授权
      setAuthError("");
      setShowDevice(true);
      await startDevice(acc);
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
      // 对需要设备码授权的账号，不自动触发（批量时由用户手动点击）
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

  // ── 一键清洗风控账号（ROPC 验证 + 自动删除） ─────────────────────────────
  const purgeInvalid = async () => {
    if (!confirm('将对所有 Outlook 账号执行 ROPC 验证，不可逆删除确认已风控账号（密码错误/账号不存在/CA封禁），确定继续？')) return;
    setPurging(true); setPurgeStats(null);
    const d = await fetch(`${API}/tools/outlook/purge-invalid`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    }).then(r => r.json()).catch(() => ({ success: false, error: '网络错误' }));
    setPurging(false);
    if (d.success) {
      setPurgeStats({ valid: d.valid, purged: d.purged, kept: d.kept });
      await loadAccounts();
    } else {
      alert(d.error ?? '清洗失败');
    }
  };


  // 手动添加 Outlook 账号
  const addManualAccount = async () => {
    if (!manualForm.email || !manualForm.password) { setManualMsg("邮箱和密码必填"); return; }
    setManualBusy(true); setManualMsg("");
    const d = await fetch(`${API}/data/accounts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ platform: "outlook", email: manualForm.email, password: manualForm.password, token: manualForm.token || undefined }),
    }).then(r => r.json()).catch(() => ({ success: false, error: "网络错误" }));
    setManualBusy(false);
    if (d.success) {
      setManualMsg("✅ 账号已添加");
      setManualForm({ email: "", password: "", token: "" });
      setTimeout(() => { setShowManualAdd(false); setManualMsg(""); }, 1000);
      await loadAccounts();
    } else {
      setManualMsg("❌ " + (d.error || "保存失败"));
    }
  };

  // browser auto-retoken
  const startAutoRetoken = async () => {
    if (!confirm('将用浏览器自动登录所有 error 状态账号并重新授权，确定？')) return;
    setRetokenBusy(true); setRetokenLog([]); setRetokenOpen(true);
    const d = await fetch(`${API}/tools/outlook/auto-retoken`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ allError: true, headless: true }),
    }).then(r => r.json()).catch(() => ({ success: false, error: '网络错误' }));
    if (!d.success) { setRetokenBusy(false); alert(d.error ?? '启动失败'); return; }
    setRetokenJobId(d.jobId);
    const iv = setInterval(async () => {
      const st = await fetch(`${API}/tools/outlook/auto-retoken/${d.jobId}`).then(r => r.json()).catch(() => ({}));
      if (st.logs) setRetokenLog(st.logs.map((l: {message: string}) => l.message));
      if (st.status === 'done') { clearInterval(iv); setRetokenBusy(false); await loadAccounts(); }
    }, 3000);
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

  // ── 标记已读 / 未读 ────────────────────────────────────────────────────────
  const markRead = async (msg: MailMsg, isRead: boolean) => {
    if (!selAccount) return;
    setMarkingRead(true);
    const d = await fetch(`${API}/tools/outlook/message/${selAccount.id}/${encodeURIComponent(msg.id)}/read`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ isRead }),
    }).then(r => r.json()).catch(() => ({ success: false }));
    setMarkingRead(false);
    if (d.success) {
      setMessages(prev => prev.map(m => m.id === msg.id ? { ...m, isRead } : m));
      setSelMsg(prev => prev && prev.id === msg.id ? { ...prev, isRead } : prev);
    }
  };

  // ── 移动邮件到文件夹 ─────────────────────────────────────────────────────
  const moveMsg = async (msg: MailMsg, destinationId: string) => {
    if (!selAccount) return;
    setMovingMsg(true); setMoveMenuOpen(false);
    const d = await fetch(`${API}/tools/outlook/message/${selAccount.id}/${encodeURIComponent(msg.id)}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destinationId }),
    }).then(r => r.json()).catch(() => ({ success: false }));
    setMovingMsg(false);
    if (d.success) {
      setMessages(prev => prev.filter(m => m.id !== msg.id));
      setSelMsg(null);
    }
  };

  // ── 删除邮件 ─────────────────────────────────────────────────────────────
  const deleteMsg = async (msg: MailMsg) => {
    if (!selAccount || !confirm("确定删除此邮件？此操作不可撤销。")) return;
    setDeletingMsg(true);
    const d = await fetch(`${API}/tools/outlook/message/${selAccount.id}/${encodeURIComponent(msg.id)}`, {
      method: "DELETE",
    }).then(r => r.json()).catch(() => ({ success: false }));
    setDeletingMsg(false);
    if (d.success) {
      setMessages(prev => prev.filter(m => m.id !== msg.id));
      setSelMsg(null);
    }
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
      } else if (!p.pending && p.error) {
        clearInterval(pollRef.current!); pollRef.current = null; setPolling(false);
        setAuthError(p.errorDescription ?? p.error);
      }
    }, 4000);
  };

  useEffect(() => () => {
    if (pollRef.current)        clearInterval(pollRef.current);
    if (batchPollRef.current)   clearInterval(batchPollRef.current);
    if (autoRefreshRef.current) clearInterval(autoRefreshRef.current);
    if (cdTimerRef.current)     clearInterval(cdTimerRef.current);
  }, []);

  // ── 批量设备码 OAuth 授权 ─────────────────────────────────────────────────
  // 设计：deviceCode 存在 React state 中，直接轮询 /device-poll（已有接口），
  // 成功后调 /save-token（已有接口）。不依赖服务端 session，服务器重启不影响。
  const CLIENT_ID_BATCH = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";

  const startBatchOAuth = async (ids?: number[]) => {
    setBatchOAuthBusy(true);
    if (batchPollRef.current) { clearInterval(batchPollRef.current); batchPollRef.current = null; }

    const d = await fetch(`${API}/tools/outlook/batch-oauth/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ids?.length ? { accountIds: ids } : {}),
    }).then(r => r.json()).catch(() => ({ success: false, error: "网络错误" }));
    setBatchOAuthBusy(false);

    if (!d.success) { alert(d.error ?? "发起批量授权失败"); return; }

    // 把 deviceCode 也存在前端 state，不依赖服务端 session
    const accs: BatchOAuthAccount[] = (d.accounts ?? []).map((a: BatchOAuthAccount & { deviceCode?: string }) => ({
      accountId: a.accountId,
      email: a.email,
      userCode: a.userCode,
      verificationUri: a.verificationUri ?? "https://microsoft.com/devicelogin",
      deviceCode: a.deviceCode ?? "",
      status: (a.status === "error" ? "error" : "pending") as BatchOAuthAccount["status"],
      errorMsg: a.errorMsg,
    }));
    setBatchOAuth({ accounts: accs, open: true });

    // 直接轮询每个账号的 device-poll，不经过服务端 session
    batchPollRef.current = setInterval(async () => {
      setBatchOAuth(prev => {
        if (!prev) return prev;
        const stillPending = prev.accounts.filter(a => a.status === "pending");
        if (stillPending.length === 0) {
          clearInterval(batchPollRef.current!); batchPollRef.current = null;
        }
        return prev;
      });

      // 并发轮询所有 pending 账号
      const snapshot = await new Promise<BatchOAuthAccount[]>(resolve => {
        setBatchOAuth(prev => { resolve(prev?.accounts ?? []); return prev; });
      });
      const pending = snapshot.filter(a => a.status === "pending" && a.deviceCode);

      await Promise.allSettled(pending.map(async acc => {
        try {
          const p = await fetch(`${API}/tools/outlook/device-poll`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ deviceCode: acc.deviceCode, clientId: CLIENT_ID_BATCH }),
          }).then(r => r.json()).catch(() => null);
          if (!p) return;

          if (p.success && p.accessToken) {
            // 授权成功：存 token 到数据库
            await fetch(`${API}/tools/outlook/save-token`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ email: acc.email, token: p.accessToken, refreshToken: p.refreshToken ?? "" }),
            });
            setBatchOAuth(prev => {
              if (!prev) return prev;
              const updated = prev.accounts.map(a =>
                a.accountId === acc.accountId ? { ...a, status: "done" as const } : a
              );
              return { ...prev, accounts: updated };
            });
            loadAccounts();
          } else if (!p.pending && p.error) {
            // p.pending = true → authorization_pending or slow_down → keep waiting
            // p.pending = false/missing + p.error → real failure (expired, denied, etc.)
            const errMsg = p.errorDescription ?? p.error ?? "授权失败";
            const isExpired = /expired|code_expired|expired_token|过期/i.test(p.error ?? "");
            setBatchOAuth(prev => {
              if (!prev) return prev;
              const updated = prev.accounts.map(a =>
                a.accountId === acc.accountId
                  ? { ...a, status: (isExpired ? ("expired" as const) : ("error" as const)), errorMsg: errMsg }
                  : a
              );
              return { ...prev, accounts: updated };
            });
          }
          // authorization_pending / slow_down → continue waiting
        } catch { /* 网络错误，下次继续 */ }
      }));
    }, 4000);
  };

  const closeBatchOAuth = () => {
    if (batchPollRef.current) { clearInterval(batchPollRef.current); batchPollRef.current = null; }
    setBatchOAuth(null);
    loadAccounts();
  };

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
          {/* 一键清洗风控账号 */}
          <button
            onClick={purgeInvalid}
            disabled={purging || accounts.length === 0}
            className="w-full py-1.5 bg-red-700/50 hover:bg-red-700/70 disabled:opacity-50 rounded text-xs text-white font-medium transition-colors"
            title="ROPC 验证全部账号，自动删除密码错误/不存在/CA封禁的风控账号"
          >
            {purging ? '清洗中…' : '🗑️ 一键清洗风控'}
          </button>
          <button onClick={startAutoRetoken} disabled={retokenBusy || accounts.length === 0} className="px-3 py-1.5 text-xs rounded bg-violet-600 hover:bg-violet-500 disabled:opacity-40 transition-colors text-white font-medium">{retokenBusy ? "🔄 重授权中…" : "🤖 自动 retoken"}</button>
          <button onClick={() => { setShowManualAdd(s => !s); setManualMsg(""); }} className="w-full py-1.5 bg-emerald-700/70 hover:bg-emerald-700 rounded text-xs text-white font-medium transition-colors">
            {showManualAdd ? "取消添加" : "➕ 手动添加账号"}
          </button>
          {showManualAdd && (
            <div className="space-y-2 mt-1">
              <input value={manualForm.email} onChange={e => setManualForm(f => ({...f, email: e.target.value}))} placeholder="邮箱地址" className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-xs text-white placeholder-gray-600" />
              <input value={manualForm.password} onChange={e => setManualForm(f => ({...f, password: e.target.value}))} placeholder="密码" type="password" className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-xs text-white placeholder-gray-600" />
              <input value={manualForm.token} onChange={e => setManualForm(f => ({...f, token: e.target.value}))} placeholder="Token（可选）" className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-xs text-white placeholder-gray-600 font-mono" />
              {manualMsg && <p className={`text-xs px-1 ${manualMsg.startsWith("✅") ? "text-emerald-400" : "text-red-400"}`}>{manualMsg}</p>}
              <button onClick={addManualAccount} disabled={manualBusy} className="w-full py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded text-xs text-white font-medium transition-colors">
                {manualBusy ? "保存中…" : "确认添加"}
              </button>
            </div>
          )}
                {/* 实时验证状态栏 */}
      {liveVerify && (
        <div className="flex items-center gap-3 px-4 py-2 bg-[#0d1117] border-b border-[#21262d] text-xs">
          <span className={`font-semibold ${liveVerify.enabled ? 'text-emerald-400' : 'text-gray-500'}`}>
            {liveVerify.enabled ? '🟢 实时验证：开启' : '⚫ 实时验证：关闭'}
          </span>
          {liveVerify.lastRun && (
            <span className="text-gray-500">
              上次扫描：{new Date(liveVerify.lastRun).toLocaleTimeString('zh-CN')} &nbsp;
              ✅ {liveVerify.lastStats.clicked} 已点击 / 失败 {liveVerify.lastStats.failed} / 跳过 {liveVerify.lastStats.skipped}
            </span>
          )}
          <button
            onClick={toggleLiveVerify}
            disabled={liveVerifyBusy}
            className={`ml-auto px-3 py-1 rounded text-xs font-medium transition-colors ${
              liveVerify.enabled
                ? 'bg-red-900/40 hover:bg-red-800/60 text-red-300'
                : 'bg-emerald-900/40 hover:bg-emerald-800/60 text-emerald-300'
            } disabled:opacity-50`}
          >
            {liveVerifyBusy ? '处理中…' : liveVerify.enabled ? '暂停自动验证' : '开启自动验证'}
          </button>
        </div>
      )}
      {purgeStats && (
            <div className="text-[10px] px-1 py-0.5 rounded bg-[#21262d] text-gray-400 flex gap-2">
              <span className="text-emerald-400">✓ 有效 {purgeStats.valid}</span>
              <span className="text-red-400">✗ 删除 {purgeStats.purged}</span>
              <span className="text-amber-400">? 待查 {purgeStats.kept}</span>
            </div>
          )}
          {/* 批量 OAuth 授权按钮 */}
          {accounts.some(a => !hasOAuth(a)) && (
            <button
              onClick={() => startBatchOAuth()}
              disabled={batchOAuthBusy}
              className="w-full py-1.5 bg-emerald-700/60 hover:bg-emerald-700/80 disabled:opacity-50 rounded text-xs text-white font-medium transition-colors"
              title="为所有无 token 的账号批量发起设备码 OAuth 授权"
            >
              {batchOAuthBusy ? "发起中…" : "🔑 批量 OAuth 授权"}
            </button>
          )}
          {batchResults.length > 0 && (
            <div className="space-y-0.5 max-h-24 overflow-y-auto">
              {batchResults.map((r, i) => (
                <div key={i} className={`text-[10px] truncate px-1 py-0.5 rounded ${r.ok ? "text-emerald-400" : "text-red-400"}`}>
                  {r.ok ? "✓" : r.needsDeviceFlow ? "↗" : "✗"} {r.email}{!r.ok && r.needsDeviceFlow ? ": 请点击「设备码」授权" : (!r.ok && r.error ? ": " + r.error.slice(0, 40) : "")}
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
            const active       = selAccount?.id === acc.id;
            const isSuspended  = acc.status === "suspended";
            const isNeedsOAuth = acc.status === "needs_oauth" || acc.status === "needs_oauth_pending";
            const accTags      = tagsOf(acc);
            const isAbuse      = accTags.includes("abuse_mode");
            const isOAuth      = hasOAuth(acc) && !isSuspended;
            const isImap       = hasImap(acc) && !isSuspended;
            const noAccess     = !isOAuth && !isImap;
            // dot color: suspended=red, needs_oauth=amber, oauth=green, imap=blue, none=amber
            const dot      = isSuspended ? "bg-red-500" : isNeedsOAuth ? "bg-amber-400" : isOAuth ? "bg-emerald-400" : isImap ? "bg-blue-400" : "bg-amber-400";
            const label    = isSuspended ? (isAbuse ? "API封禁" : "已停用") : isNeedsOAuth ? "需授权" : isOAuth ? "OAuth" : isImap ? "IMAP" : "需授权";
            const labelCls = isSuspended ? "text-red-500" : isNeedsOAuth ? "text-amber-500" : isOAuth ? "text-emerald-500" : isImap ? "text-blue-400" : "text-amber-500";
            const vr = verifyStatus(acc.id);
            const vb = vr ? verifyBadge(vr.status) : null;
            const pwKey = `pw-${acc.id}`;
            const pwCopied = copied === pwKey;
            const isConfirmDel = confirmDeleteId === acc.id;
            return (
              <div key={acc.id} className={`border-b border-[#161b22] group/acc ${active ? "bg-blue-600/15" : ""} ${noAccess ? "opacity-70" : ""}`}>
                <div className="relative">
                  <button onClick={() => selectAccount(acc)}
                    className={`w-full text-left px-3 pt-2.5 pb-1.5 pr-7 transition-colors border-l-2 ${
                      active ? "border-l-blue-500" : "hover:bg-[#161b22] border-l-transparent"
                    }`}>
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
                      <button
                    onClick={e => {
                      e.stopPropagation();
                      navigator.clipboard.writeText(acc.email);
                      setCopied(`em-${acc.id}`);
                      setTimeout(() => setCopied(c => c === `em-${acc.id}` ? "" : c), 1500);
                    }}
                    title="点击复制邮箱"
                    className="text-xs font-mono truncate text-gray-200 hover:text-emerald-400 text-left"
                  >
                    {copied === `em-${acc.id}` ? "已复制 ✓" : acc.email}
                  </button>
                    </div>
                    <div className="flex items-center gap-2 mt-0.5 ml-3">
                      <span className={`text-[10px] font-medium ${labelCls}`}>{label}</span>
                      {vb && <span className={`text-[10px] ${vb.cls}`}>· {vb.label}</span>}
                      {accTags.map(tag => (
                        <span key={tag} className={`text-[10px] px-1 py-0.5 rounded border ${TAG_CLASSES[tag] ?? "text-gray-400 bg-[#21262d] border-[#30363d]"}`}>
                          {TAG_LABELS[tag] ?? tag}
                        </span>
                      ))}
                      <span className="text-[10px] text-gray-600 ml-auto">
                        {new Date(acc.created_at).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" })}
                      </span>
                    </div>
                  </button>
                  {/* 悬浮删除按钮 */}
                  <button
                    onClick={e => { e.stopPropagation(); setConfirmDeleteId(acc.id); }}
                    className="absolute right-1.5 top-2 opacity-0 group-hover/acc:opacity-100 transition-opacity p-1 hover:bg-red-900/30 rounded text-gray-600 hover:text-red-400"
                    title="删除账号"
                  >
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                    </svg>
                  </button>
                </div>
                {/* 删除确认条 */}
                {isConfirmDel && (
                  <div className="px-3 pb-2 flex items-center gap-2 bg-red-900/20 border-t border-red-800/30">
                    <span className="text-[10px] text-red-400 flex-1">确认删除此账号？</span>
                    <button
                      onClick={async e => {
                        e.stopPropagation();
                        await fetch(`${API}/tools/outlook/account/${acc.id}`, { method: "DELETE" });
                        setConfirmDeleteId(null);
                        if (selAccount?.id === acc.id) { setSelAccount(null); setMessages([]); }
                        loadAccounts();
                      }}
                      className="text-[10px] px-2 py-0.5 bg-red-600 hover:bg-red-700 text-white rounded transition-colors"
                    >确认删除</button>
                    <button
                      onClick={e => { e.stopPropagation(); setConfirmDeleteId(null); }}
                      className="text-[10px] px-2 py-0.5 bg-[#30363d] hover:bg-[#3d444d] text-gray-300 rounded transition-colors"
                    >取消</button>
                  </div>
                )}
                {acc.password && !isConfirmDel && (
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
        {/* ── 批量按主题删除栏 ── */}
        {selAccount && (
          <div className="px-2 py-1.5 border-b border-[#21262d] flex gap-1 items-center bg-[#0d1117]">
            <input
              value={batchDelKw}
              onChange={e => { setBatchDelKw(e.target.value); setBatchDelResult(""); }}
              placeholder="按主题关键词批量删除（Enter确认）…"
              className="flex-1 bg-[#161b22] border border-[#30363d] rounded px-2 py-1 text-[11px] text-gray-300 placeholder-gray-600 focus:outline-none focus:border-red-500/60"
              onKeyDown={async e => {
                if (e.key !== "Enter") return;
                const kw = batchDelKw.trim();
                if (!kw || !selAccount) return;
                setBatchDelBusy(true); setBatchDelResult("🗑 删除中…");
                const r = await fetch(`${API}/tools/outlook/account/${selAccount.id}/batch-delete-by-subject`, {
                  method: "POST", headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ keyword: kw }),
                }).then(x => x.json()).catch(() => ({ success: false, error: "网络错误" }));
                setBatchDelBusy(false);
                setBatchDelResult(r.success ? `✅ 已删除 ${r.deleted} 封（含"${kw}"）` : `❌ ${r.error}`);
                if (r.success) { setBatchDelKw(""); silentRefresh(selAccount, folder, search); }
              }}
            />
            <button
              disabled={batchDelBusy || !selAccount}
              onClick={async () => {
                const kw = batchDelKw.trim() || "verify";
                if (!batchDelKw.trim()) { setBatchDelKw("verify"); return; }
                setBatchDelBusy(true); setBatchDelResult("🗑 删除中…");
                const r = await fetch(`${API}/tools/outlook/account/${selAccount.id}/batch-delete-by-subject`, {
                  method: "POST", headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ keyword: kw }),
                }).then(x => x.json()).catch(() => ({ success: false, error: "网络错误" }));
                setBatchDelBusy(false);
                setBatchDelResult(r.success ? `✅ 已删除 ${r.deleted} 封（含"${kw}"）` : `❌ ${r.error}`);
                if (r.success) { setBatchDelKw(""); silentRefresh(selAccount, folder, search); }
              }}
              className="px-2 py-1 bg-red-900/40 hover:bg-red-900/70 border border-red-800/40 rounded text-red-400 text-[11px] disabled:opacity-40 transition-colors whitespace-nowrap"
            >{batchDelBusy ? "…" : "批删"}</button>
          </div>
        )}
        {batchDelResult && (
          <div className={`px-3 py-1 text-[10px] border-b border-[#21262d] ${batchDelResult.startsWith("✅") ? "text-emerald-400 bg-emerald-900/10" : batchDelResult.startsWith("❌") ? "text-red-400 bg-red-900/10" : "text-gray-500"}`}>
            {batchDelResult}
          </div>
        )}

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
              <button key={m.id} onClick={() => { if (isActive) { setSelMsg(null); } else { setSelMsg(m); if (!m.isRead) markRead(m, true); } }}
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
            <div className="flex flex-col items-center mt-10 px-4 gap-1">
              <p className="text-xs text-gray-600 text-center">该文件夹暂无邮件</p>
              {folder === "inbox" && <p className="text-xs text-gray-700 text-center">如果以前有邮件，可能已被移动到归档/垃圾邮件/已删除；后端会自动做全邮箱兜底查询。</p>}
              <p className="text-xs text-gray-700 text-center">{refreshCountdown}s 后自动刷新</p>
            </div>
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
                <div className="flex items-center gap-1.5 shrink-0">
                  <button onClick={() => markRead(selMsg, !selMsg.isRead)} disabled={markingRead}
                    className="text-xs px-2 py-0.5 rounded bg-[#21262d] hover:bg-[#30363d] text-gray-400 hover:text-white disabled:opacity-40 transition-colors">
                    {markingRead ? "…" : selMsg.isRead ? "标为未读" : "标为已读"}
                  </button>
                  <div className="relative">
                    <button onClick={() => setMoveMenuOpen(v => !v)} disabled={movingMsg}
                      className="text-xs px-2 py-0.5 rounded bg-[#21262d] hover:bg-[#30363d] text-gray-400 hover:text-white disabled:opacity-40 transition-colors">
                      {movingMsg ? "移动中…" : "移动 ▾"}
                    </button>
                    {moveMenuOpen && (
                      <div className="absolute right-0 top-full mt-1 bg-[#161b22] border border-[#30363d] rounded-lg shadow-xl z-20 min-w-[120px] py-1">
                        {FOLDERS.filter(f => f.id !== folder).map(f => (
                          <button key={f.id} onClick={() => moveMsg(selMsg, f.id)}
                            className="w-full text-left px-3 py-1.5 text-xs text-gray-300 hover:bg-[#21262d] hover:text-white transition-colors">
                            → {f.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  <button onClick={() => deleteMsg(selMsg)} disabled={deletingMsg}
                    className="text-xs px-2 py-0.5 rounded bg-red-700/50 hover:bg-red-700/70 text-red-300 disabled:opacity-40 transition-colors">
                    {deletingMsg ? "删除中…" : "🗑 删除"}
                  </button>
                  <button onClick={() => { setSelMsg(null); setMoveMenuOpen(false); }}
                    className="text-gray-500 hover:text-gray-300 text-xs px-2 py-0.5 rounded bg-[#21262d] hover:bg-[#30363d]">
                    关闭
                  </button>
                </div>
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
                    srcDoc={injectBaseTarget(selMsg.body)}
                    sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
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

      {/* ─── 批量 OAuth 授权弹窗 ─────────────────────────────────────────── */}
      {batchOAuth?.open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-[#0d1117] border border-[#30363d] rounded-xl shadow-2xl w-full max-w-lg mx-4 flex flex-col max-h-[80vh]">
            {/* 头部 */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-[#21262d]">
              <div>
                <h2 className="text-sm font-semibold text-white">🔑 批量 OAuth 授权</h2>
                <p className="text-[11px] text-gray-500 mt-0.5">
                  {batchOAuth.accounts.filter(a => a.status === "pending").length} 个账号待授权 ·
                  {" "}{batchOAuth.accounts.filter(a => a.status === "done").length} 个已完成
                </p>
              </div>
              <button onClick={closeBatchOAuth}
                className="text-gray-500 hover:text-white px-2 py-1 rounded hover:bg-[#21262d] text-xs">✕ 关闭</button>
            </div>

            {/* 说明 */}
            <div className="px-5 py-3 bg-blue-900/10 border-b border-[#21262d]">
              <p className="text-[11px] text-blue-300 leading-5">
                1. 点击下方按钮打开微软授权页面<br/>
                2. 逐个复制「用户码」粘贴到授权页，并用对应账号密码登录<br/>
                3. 后台每 4 秒自动检测，授权完成后自动存储 token 并显示 ✓
              </p>
              <a
                href={batchOAuth.accounts.find(a => a.status === "pending")?.verificationUri ?? "https://microsoft.com/devicelogin"}
                target="_blank" rel="noopener noreferrer"
                className="mt-2 inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded text-xs text-white font-medium transition-colors"
              >
                🌐 打开微软授权页面
              </a>
            </div>

            {/* 账号列表 */}
            <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
              {batchOAuth.accounts.map(acc => {
                const isDone    = acc.status === "done";
                const isPending = acc.status === "pending";
                const isError   = acc.status === "error" || acc.status === "expired";
                // 从账号列表查出密码
                const fullAcc   = accounts.find(a => a.id === acc.accountId);
                const pw        = fullAcc?.password ?? "";
                const codeKey   = `bo-${acc.accountId}`;
                const pwKey     = `bopw-${acc.accountId}`;
                return (
                  <div key={acc.accountId}
                    className={`rounded-lg border p-3 ${
                      isDone  ? "border-emerald-600/40 bg-emerald-900/10" :
                      isError ? "border-red-600/30 bg-red-900/10" :
                                "border-[#30363d] bg-[#161b22]"
                    }`}>

                    {/* 行一：状态 + 用户码（最重要，顶部） */}
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <span className="text-base shrink-0">
                        {isDone ? "✅" : isError ? "❌" : "⏳"}
                      </span>
                      {/* 用户码 + 复制 + 打开授权页 */}
                      {acc.userCode && !isDone && (
                        <div className="flex items-center gap-1.5 ml-auto">
                          <span className="font-mono text-base font-bold tracking-widest text-white">
                            {acc.userCode}
                          </span>
                          <button
                            onClick={() => { navigator.clipboard.writeText(acc.userCode); setCopied(codeKey); setTimeout(() => setCopied(""), 1500); }}
                            className="text-[10px] px-2 py-0.5 bg-blue-600/30 hover:bg-blue-600/60 border border-blue-500/30 rounded text-blue-300 hover:text-white transition-colors"
                          >
                            {copied === codeKey ? "✓ 已复制" : "复制码"}
                          </button>
                          <a href={acc.verificationUri} target="_blank" rel="noopener noreferrer"
                            className="text-[10px] px-2 py-0.5 bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] rounded text-gray-400 hover:text-white transition-colors">
                            授权页 ↗
                          </a>
                        </div>
                      )}
                      {isDone && <span className="text-[10px] text-emerald-400 ml-auto">token 已保存 ✓</span>}
                    </div>

                    {/* 行二：完整邮箱（不截断） */}
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-gray-500 shrink-0">账号</span>
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(acc.email);
                          setCopied(`boem-${acc.accountId}`);
                          setTimeout(() => setCopied(c => c === `boem-${acc.accountId}` ? "" : c), 1500);
                        }}
                        title="点击复制邮箱"
                        className="text-xs font-mono text-gray-200 select-all break-all hover:text-emerald-400 text-left"
                      >
                        {copied === `boem-${acc.accountId}` ? "已复制 ✓" : acc.email}
                      </button>
                    </div>

                    {/* 行三：密码（复制） */}
                    {pw && !isDone && (
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-[10px] text-gray-500 shrink-0">密码</span>
                        <button
                          onClick={() => { navigator.clipboard.writeText(pw); setCopied(pwKey); setTimeout(() => setCopied(""), 1500); }}
                          className="text-xs font-mono text-gray-300 hover:text-white text-left break-all"
                        >
                          {copied === pwKey ? <span className="text-emerald-400">✓ 已复制</span> : pw}
                        </button>
                      </div>
                    )}

                    {/* 状态文字 */}
                    {isError && <p className="text-[10px] text-red-400 mt-1.5">{acc.errorMsg}</p>}
                    {isPending && <p className="text-[10px] text-gray-600 mt-1 animate-pulse">⏳ 等待授权中…</p>}
                  </div>
                );
              })}
            </div>

            {/* 底部 */}
            <div className="px-5 py-3 border-t border-[#21262d] flex items-center gap-3">
              {batchOAuth.accounts.every(a => a.status === "done") ? (
                <p className="text-xs text-emerald-400 font-medium">✅ 所有授权已完成</p>
              ) : batchOAuth.accounts.some(a => a.status === "pending") ? (
                <p className="text-[11px] text-gray-500 animate-pulse">后台每 4 秒轮询，等待你在浏览器完成授权…</p>
              ) : (
                <p className="text-[11px] text-amber-400">有账号授权失败或码已过期，可重新点击「批量 OAuth 授权」按钮</p>
              )}
              <button onClick={closeBatchOAuth}
                className="ml-auto px-4 py-1.5 bg-[#21262d] hover:bg-[#30363d] rounded text-xs text-gray-300 transition-colors">
                完成
              </button>
            </div>
          </div>
        </div>
      )}

      {retokenOpen && (
        <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4">
          <div className="bg-[#161b22] border border-[#30363d] rounded-xl w-full max-w-2xl flex flex-col max-h-[80vh]">
            <div className="px-5 py-3 border-b border-[#30363d] flex items-center justify-between"><h3 className="text-sm font-semibold text-white">🤖 自动 retoken 进度</h3>{!retokenBusy && (<button onClick={() => setRetokenOpen(false)} className="text-gray-500 hover:text-gray-300 text-xs">关闭</button>)}</div>
            <div className="flex-1 overflow-y-auto p-4 font-mono text-xs space-y-1">{retokenLog.length === 0 ? <p className="text-gray-600 animate-pulse">等待输出…</p> : retokenLog.map((line, i) => (<p key={i} className={line.includes("✅") ? "text-emerald-400" : line.includes("❌") ? "text-red-400" : line.includes("⚠️") ? "text-amber-400" : "text-gray-300"}>{line}</p>))}</div>
            {retokenBusy && <p className="px-5 py-2 text-[10px] text-gray-600 animate-pulse border-t border-[#30363d]">处理中，每 3 秒刷新…</p>}
          </div>
        </div>
      )}
    </div>
  );
}
