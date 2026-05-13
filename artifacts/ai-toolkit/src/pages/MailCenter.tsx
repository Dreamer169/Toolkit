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
  inbox_verified:    "收件箱✓",
  replit_used:       "已用Replit",
  needs_oauth_manual:"需重新授权",
  inbox_error:       "收件箱异常",
  authcode_failed:   "授权码失败",
  replit_avail:      "可用Replit",
};
// 对用户隐藏的内部运维标签（abuse_mode/token_invalid 已由 status 标签体现）
const HIDDEN_TAGS = new Set(["abuse_mode","token_invalid","ssid_ok","unitool_registered","needs_oauth_manual"]);
const TAG_CLASSES: Record<string, string> = {
  inbox_verified:    "text-emerald-300 bg-emerald-950/40 border-emerald-800/40",
  replit_used:       "text-blue-300 bg-blue-950/40 border-blue-800/40",
  needs_oauth_manual:"text-violet-300 bg-violet-950/40 border-violet-800/40",
  abuse_mode:        "text-red-400 bg-red-950/60 border-red-800/60",
  token_invalid:     "text-orange-300 bg-orange-950/40 border-orange-800/40",
  inbox_error:       "text-amber-300 bg-amber-950/40 border-amber-800/40",
  authcode_failed:   "text-orange-300 bg-orange-950/40 border-orange-800/40",
  replit_avail:      "text-cyan-300 bg-cyan-950/40 border-cyan-800/40",
};
function tagsOf(acc: Account): string[] {
  return Array.from(new Set((acc.tags ?? "").split(",").map(t => t.trim()).filter(Boolean)));
}
function hasTag(acc: Account, tag: string): boolean {
  return tagsOf(acc).includes(tag);
}

export default function MailCenter() {
  const [accounts, setAccounts]         = useState<Account[]>([]);
  const [accTotal, setAccTotal]           = useState(0);
  const [selAccount, setSelAccount]     = useState<Account | null>(null);
  const [folder, setFolder]             = useState("inbox");
  const [messages, setMessages]         = useState<MailMsg[]>([]);
  const [selMsg, setSelMsg]             = useState<MailMsg | null>(null);
  const [search, setSearch]             = useState("");
  const [accSearch, setAccSearch]       = useState("");
  const [checkBusy, setCheckBusy]       = useState(false);
  const [checkStopBusy, setCheckStopBusy] = useState(false);

  const [checkResult, setCheckResult]   = useState<string | null>(null);
  const [checkProgress, setCheckProgress] = useState<{ checked: number; total: number; valid: number; needsAuth: number; banned: number; skipped: number } | null>(null);
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
  const [statusFilter, setStatusFilter]   = useState<"all"|"active"|"suspended"|"noauth"|"autofix">("all");
  const [bulkDelBusy, setBulkDelBusy]     = useState(false);
  const [bulkDelResult, setBulkDelResult] = useState("");
  const [batchOAuth, setBatchOAuth]       = useState<BatchOAuthState | null>(null);
  const [batchOAuthBusy, setBatchOAuthBusy] = useState(false);
  const [autoCompleteBusy, setAutoCompleteBusy] = useState(false);
  const [autoCompleteMsg, setAutoCompleteMsg] = useState("");
  const [autoCompleteLog, setAutoCompleteLog] = useState<string[]>([]);
  const [autoCompleteOpen, setAutoCompleteOpen] = useState(false);
  const [autoCompleteLogFile, setAutoCompleteLogFile] = useState("");
  const [autoCompleteDone, setAutoCompleteDone] = useState(false);
  const autoCompleteLogRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [reauthManualBusy, setReauthManualBusy] = useState(false);
  const [reauthManualMsg, setReauthManualMsg] = useState("");
  const [reauthManualLog, setReauthManualLog] = useState<string[]>([]);
  const [reauthManualOpen, setReauthManualOpen] = useState(false);
  const [reauthManualLogFile, setReauthManualLogFile] = useState("");
  const [reauthManualDone, setReauthManualDone] = useState(false);
  const reauthManualLogRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // IMAP IDLE daemon state
  const [idleDaemonRunning, setIdleDaemonRunning] = useState(false);
  const [idleDaemonBusy,    setIdleDaemonBusy]    = useState(false);
  const [idleEvents,        setIdleEvents]         = useState<Array<{ account_id: number; email: string; subject: string; from: string; ts: string }>>([]);
  const idleEventsPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const idleLastTs        = useRef<string>("");
  // compose state
  const [showCompose, setShowCompose] = useState(false);
  const [composeTo, setComposeTo] = useState("");
  const [composeSubject, setComposeSubject] = useState("");
  const [composeBody, setComposeBody] = useState("");
  const [sendBusy, setSendBusy] = useState(false);
  const [sendResult, setSendResult] = useState<{ ok: boolean; msg: string } | null>(null);
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
  const retokenPollRef                    = useRef<ReturnType<typeof setInterval> | null>(null);
  const accSearchTimerRef                   = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [liveVerify, setLiveVerify]         = useState<{ enabled: boolean; lastRun: string | null; lastStats: { total: number; clicked: number; skipped: number; failed: number } } | null>(null);
  // auto-fix 实时状态：从后端 /tools/accounts/auto-fix-status 5s 轮询
  const [autoFixIds, setAutoFixIds] = useState<{ pendingIds: Set<number>; waitingIds: Set<number> }>({ pendingIds: new Set(), waitingIds: new Set() });
  const autoFixPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
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


  const handleStopCheck = async () => {
    if (checkStopBusy) return;
    setCheckStopBusy(true);
    try {
      const r = await fetch("/api/tools/outlook/auto-check/stop", { method: "POST" });
      const d = await r.json() as { success: boolean; message?: string };
      if (d.success) {
        setCheckResult("🛑 停止信号已发送，正在等待当前账号处理完成…");
      } else {
        setCheckResult("⚠ " + (d.message ?? "停止失败"));
      }
    } catch {
      setCheckResult("❌ 停止请求失败");
    } finally {
      setCheckStopBusy(false);
    }
  };

  const handleAutoCheck = async () => {
    if (checkBusy) return;
    setCheckBusy(true); setCheckResult(null); setCheckProgress(null);
    // 将轮询逻辑提取出来，可复用于「已在运行」场景
    const startPolling = (totalN: number) => {
      const pollProgress = () => {
        fetch("/api/tools/outlook/auto-check/status")
          .then(r => r.json())
          .then((s: { running?: boolean; stopped?: boolean; stats?: { checked: number; valid: number; needsAuth: number; banned: number; skipped: number; stoppedEarly?: boolean; finishedAt: string | null } }) => {
            if (s.stats) {
              setCheckProgress({ checked: s.stats.checked, total: totalN, valid: s.stats.valid, needsAuth: s.stats.needsAuth, banned: s.stats.banned, skipped: s.stats.skipped });
            }
            if (s.running) {
              setTimeout(pollProgress, 3000);
            } else {
              // 检测结束（正常完成 or 手动停止）
              if (s.stats?.stoppedEarly) {
                setCheckResult(`🛑 已停止：已检 ${s.stats.checked}/${totalN} — ${s.stats.valid} 活跃 / ${s.stats.banned} 被封 / ${s.stats.needsAuth} 需授权`);
              } else if (s.stats) {
                setCheckResult(`✅ 检测完成：${s.stats.valid} 活跃 / ${s.stats.banned} 被封 / ${s.stats.needsAuth} 需授权 / ${s.stats.skipped} 跳过`);
              } else {
                setCheckResult("✅ 检测完成");
              }
              setCheckBusy(false);
              loadAccounts();
            }
          })
          .catch(() => { setCheckResult("⚠ 轮询出错"); setCheckBusy(false); });
      };
      setTimeout(pollProgress, 2000);
    };

    try {
      const res = await fetch("/api/tools/outlook/auto-check", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const d = await res.json() as { success: boolean; started?: boolean; running?: boolean; total?: number; message?: string };
      if (d.success) {
        if (d.running) {
          // 已在运行 — 获取当前进度并开始轮询
          setCheckResult("⏳ 检测已在后台运行，跟踪进度中…");
          const status = await fetch("/api/tools/outlook/auto-check/status").then(r => r.json()).catch(() => null) as { running?: boolean; stats?: { checked: number; valid: number; needsAuth: number; banned: number; skipped: number; total?: number; finishedAt: string | null } } | null;
          const totalN = status?.stats?.total ?? d.total ?? 0;
          if (status?.stats) {
            setCheckProgress({ checked: status.stats.checked, total: totalN, valid: status.stats.valid, needsAuth: status.stats.needsAuth, banned: status.stats.banned, skipped: status.stats.skipped });
          }
          startPolling(totalN);
          // 不调用 finally 里的 setCheckBusy(false)：通过 return 跳出，但 finally 仍会执行
          // 用 busyGuard 来阻止 finally 重置
        } else {
          const totalN = d.total ?? 0;
          setCheckResult("🚀 已在后台启动，共 " + totalN + " 个账号");
          startPolling(totalN);
        }
        return; // 两条路都走 polling，finally 里需跳过 setCheckBusy
      } else {
        setCheckResult("❌ 检测失败：" + (d.message ?? "未知错误"));
        setCheckBusy(false);
      }
    } catch {
      setCheckResult("❌ 请求出错，请检查网络");
      setCheckBusy(false);
    }
  };
  const loadAccounts = useCallback(async (opts?: { search?: string; status?: string }) => {
    const qs = new URLSearchParams({ limit: '300' });
    if (opts?.search) qs.set('search', opts.search);
    if (opts?.status && opts.status !== 'all') qs.set('status', opts.status);
    const d = await fetch(API + '/tools/outlook/accounts?' + qs).then(r => r.json()).catch(() => ({}));
    if (d.success) { setAccounts(d.accounts ?? []); setAccTotal(d.total ?? 0); }
  }, []);

  useEffect(() => { loadAccounts(); }, [loadAccounts]);

  // auto-fix 实时轮询：每 5s 从后端拉取 pending/waiting 账号 ID，覆盖本地状态展示
  useEffect(() => {
    const poll = async () => {
      try {
        const d = await fetch(API + "/tools/accounts/auto-fix-status").then(r => r.json()).catch(() => ({}));
        if (d.success) {
          setAutoFixIds({
            pendingIds: new Set<number>(d.pendingIds ?? []),
            waitingIds: new Set<number>(d.waitingIds ?? []),
          });
        }
      } catch {}
    };
    poll();
    autoFixPollRef.current = setInterval(poll, 5_000);
    return () => { if (autoFixPollRef.current) clearInterval(autoFixPollRef.current); };
  }, []);

  // Debounce accSearch + statusFilter -> server-side reload
  useEffect(() => {
    if (accSearchTimerRef.current) clearTimeout(accSearchTimerRef.current);
    accSearchTimerRef.current = setTimeout(() => {
      loadAccounts({ search: accSearch, status: statusFilter });
    }, 400);
    return () => { if (accSearchTimerRef.current) clearTimeout(accSearchTimerRef.current); };
  }, [accSearch, statusFilter, loadAccounts]);

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
    checkIdleStatus();
    const iv = setInterval(loadLiveVerifyStatus, 3_000);
    return () => clearInterval(iv);
  }, [loadLiveVerifyStatus]);

  // 30s 自动轮询新邮件
  // 30s 自动轮询新邮件（token 失效时顺手更新账号状态并刷新列表）
  const silentRefresh = useCallback(async (acc: Account, fld: string, q: string) => {
    const d = await fetch(`${API}/tools/outlook/fetch-messages-by-id`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accountId: acc.id, folder: fld, top: 150, search: q || undefined }),
    }).then(r => r.json()).catch(() => null);
    if (d?.success) { setMessages(d.messages ?? []); setAutoRefreshError(""); }
    else if (d && !d.success) {
      setAutoRefreshError(d.error ?? "刷新失败");
      // 后端已更新账号状态（token_invalid / abuse_mode） → 刷新左侧账号列表让用户看到变化
      if (d.statusUpdated || d.needsAuth) { try { await loadAccounts(); } catch {} }
    }
  }, [loadAccounts]);

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
      if (d.needsAuth) { setNeedsAuth(true); try { await loadAccounts(); } catch {} }
    }
  }, [loadAccounts]);

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
    retokenPollRef.current = setInterval(async () => {
      const st = await fetch(`${API}/tools/outlook/auto-retoken/${d.jobId}`).then(r => r.json()).catch(() => ({}));
      if (st.logs) setRetokenLog(st.logs.map((l: {message: string}) => l.message));
      if (st.status === 'done') { if (retokenPollRef.current) { clearInterval(retokenPollRef.current); retokenPollRef.current = null; } setRetokenBusy(false); await loadAccounts(); }
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
      not_exist:        { label: "账号不存在", cls: "text-red-400" },
      need_mfa:         { label: "需MFA",   cls: "text-orange-400" },
      blocked_ca:       { label: "CA封禁",  cls: "text-red-500" },
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
    if (pollRef.current)             clearInterval(pollRef.current);
    if (batchPollRef.current)        clearInterval(batchPollRef.current);
    if (autoRefreshRef.current)      clearInterval(autoRefreshRef.current);
    if (cdTimerRef.current)          clearInterval(cdTimerRef.current);
    if (retokenPollRef.current)      clearInterval(retokenPollRef.current);
    if (autoCompleteLogRef.current)  clearInterval(autoCompleteLogRef.current);
    if (reauthManualLogRef.current)  clearInterval(reauthManualLogRef.current);
    if (idleEventsPollRef.current)   clearInterval(idleEventsPollRef.current);
    if (autoFixPollRef.current)       clearInterval(autoFixPollRef.current);

  }, []);

  // ── 批量设备码 OAuth 授权 ─────────────────────────────────────────────────
  // 设计：deviceCode 存在 React state 中，直接轮询 /device-poll（已有接口），
  // 成功后调 /save-token（已有接口）。不依赖服务端 session，服务器重启不影响。
  const CLIENT_ID_BATCH = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";

  const bulkDeleteSuspended = async () => {
    const cnt = accounts.filter(a => a.status === "suspended").length;
    if (!confirm(`将永久删除 ${cnt} 个已封禁账号（suspended），不可恢复。确定继续？`)) return;
    setBulkDelBusy(true); setBulkDelResult("");
    let ok = 0, fail = 0;
    for (const a of accounts.filter(x => x.status === "suspended")) {
      const r = await fetch(`${API}/tools/outlook/account/${a.id}`, { method: "DELETE" })
        .then(res => res.json()).catch(() => ({ success: false }));
      if (r.success) ok++; else fail++;
    }
    setBulkDelBusy(false);
    setBulkDelResult(`✓ 已删除 ${ok} 个，失败 ${fail} 个`);
    loadAccounts();
  };

  const startAutoComplete = async (ids?: number[]) => {
    setAutoCompleteBusy(true); setAutoCompleteMsg("");
    const body: Record<string, unknown> = {};
    if (ids?.length) body.accountIds = ids;
    const d = await fetch(`${API}/tools/outlook/batch-oauth/auto-complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(r => r.json()).catch(() => ({ success: false, error: "网络错误" }));
    setAutoCompleteBusy(false);
    if (d.success) {
      const list = (d.accounts ?? []) as Array<{ email: string }>;
      setAutoCompleteMsg(`已为 ${list.length} 个账号启动自动授权`);
      setAutoCompleteLog([`🚀 启动 ${list.length} 个账号自动授权…`, ...list.map(a => `  • ${a.email}`)]);
      setAutoCompleteLogFile(d.logFile ?? "");
      setAutoCompleteDone(false);
      setAutoCompleteOpen(true);
      if (autoCompleteLogRef.current) clearInterval(autoCompleteLogRef.current);
      let _offset = 0;
      autoCompleteLogRef.current = setInterval(async () => {
        if (!d.logFile) return;
        const r = await fetch(`${API}/tools/outlook/batch-oauth/log?file=${encodeURIComponent(d.logFile)}&offset=${_offset}`)
          .then(x => x.json()).catch(() => null);
        if (!r?.success) return;
        if (r.lines?.length) {
          setAutoCompleteLog(prev => [...prev, ...r.lines]);
          _offset = r.nextOffset;
        }
        if (r.done) {
          setAutoCompleteDone(true);
          if (autoCompleteLogRef.current) { clearInterval(autoCompleteLogRef.current); autoCompleteLogRef.current = null; }
          await loadAccounts();
        }
      }, 3000);
    } else {
      setAutoCompleteMsg("失败：" + (d.error ?? "未知错误"));
    }
  };

  const sendMessage = async () => {
    if (!selAccount || !composeTo.trim() || !composeSubject.trim()) return;
    setSendBusy(true); setSendResult(null);
    const d = await fetch(`${API}/tools/outlook/send-message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        accountId: selAccount.id,
        to: composeTo.trim().split(",").map(s => s.trim()).filter(Boolean),
        subject: composeSubject.trim(),
        body: composeBody,
        bodyType: "Text",
      }),
    }).then(r => r.json()).catch(() => ({ success: false, error: "网络错误" }));
    setSendBusy(false);
    if (d.success) {
      setSendResult({ ok: true, msg: "✅ 发送成功！" });
      setComposeTo(""); setComposeSubject(""); setComposeBody("");
      setTimeout(() => { setShowCompose(false); setSendResult(null); }, 2500);
    } else {
      setSendResult({ ok: false, msg: "❌ " + (d.error ?? "发送失败") });
    }
  };

  const startReauthManual = async (ids?: number[]) => {
    setReauthManualBusy(true); setReauthManualMsg("");
    const body: Record<string, unknown> = {};
    if (ids?.length) body.accountIds = ids;
    const d = await fetch(`${API}/tools/outlook/batch-oauth/reauth-manual`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(r => r.json()).catch(() => ({ success: false, error: "网络错误" }));
    setReauthManualBusy(false);
    if (d.success) {
      const list = (d.accounts ?? []) as Array<{ email: string }>;
      setReauthManualMsg(`已为 ${list.length} 个账号启动重授权`);
      setReauthManualLog([`🔄 启动 ${list.length} 个账号重授权…`, ...list.map(a => `  • ${a.email}`)]);
      setReauthManualLogFile(d.logFile ?? "");
      setReauthManualDone(false);
      setReauthManualOpen(true);
      if (reauthManualLogRef.current) clearInterval(reauthManualLogRef.current);
      let _offset = 0;
      reauthManualLogRef.current = setInterval(async () => {
        if (!d.logFile) return;
        const r = await fetch(`${API}/tools/outlook/batch-oauth/log?file=${encodeURIComponent(d.logFile)}&offset=${_offset}`)
          .then(x => x.json()).catch(() => null);
        if (!r?.success) return;
        if (r.lines?.length) {
          setReauthManualLog(prev => [...prev, ...r.lines]);
          _offset = r.nextOffset;
        }
        if (r.done) {
          setReauthManualDone(true);
          if (reauthManualLogRef.current) { clearInterval(reauthManualLogRef.current); reauthManualLogRef.current = null; }
          await loadAccounts();
        }
      }, 3000);
    } else {
      setReauthManualMsg("失败：" + (d.error ?? "未知错误"));
    }
  };

  const startBatchOAuth = async (ids?: number[], filter?: "needs_oauth_manual") => {
    setBatchOAuthBusy(true);
    if (batchPollRef.current) { clearInterval(batchPollRef.current); batchPollRef.current = null; }

    const d = await fetch(`${API}/tools/outlook/batch-oauth/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ids?.length ? { accountIds: ids } : filter ? { filter } : {}),
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

  // ── IMAP IDLE daemon ──────────────────────────────────────────────────────
  const checkIdleStatus = async () => {
    try {
      const d = await fetch(`${API}/tools/outlook/imap-idle/status`).then(r => r.json()).catch(() => null);
      if (d?.success) setIdleDaemonRunning(!!d.running);
    } catch { /* ignore */ }
  };

  const pollIdleEvents = async () => {
    try {
      const since = idleLastTs.current ? `&since=${encodeURIComponent(idleLastTs.current)}` : "";
      const d = await fetch(`${API}/tools/outlook/imap-idle/events?limit=20${since}`).then(r => r.json()).catch(() => null);
      if (d?.success && d.events?.length) {
        setIdleEvents(prev => [...prev, ...d.events].slice(-100));
        idleLastTs.current = d.events[d.events.length - 1].ts;
      }
    } catch { /* ignore */ }
  };

  const toggleIdleDaemon = async () => {
    setIdleDaemonBusy(true);
    try {
      if (idleDaemonRunning) {
        await fetch(`${API}/tools/outlook/imap-idle/stop`, { method: "POST" }).then(r => r.json()).catch(() => null);
        setIdleDaemonRunning(false);
        if (idleEventsPollRef.current) { clearInterval(idleEventsPollRef.current); idleEventsPollRef.current = null; }
      } else {
        await fetch(`${API}/tools/outlook/imap-idle/start`, { method: "POST" }).then(r => r.json()).catch(() => null);
        setIdleDaemonRunning(true);
        idleLastTs.current = new Date().toISOString();
        if (idleEventsPollRef.current) clearInterval(idleEventsPollRef.current);
        idleEventsPollRef.current = setInterval(pollIdleEvents, 5000);
      }
    } finally {
      setIdleDaemonBusy(false);
    }
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
        <div className="flex items-center gap-3 px-3 py-1.5 text-xs">
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

          {/* 🤖 自动重授权 Manual 账号 */}
          {accounts.some(a => tagsOf(a).includes("needs_oauth_manual")) && (
            <button
              onClick={() => startReauthManual()}
              disabled={reauthManualBusy}
              className="w-full py-1.5 bg-violet-700/40 hover:bg-violet-700/60 disabled:opacity-50 rounded text-xs text-gray-300 font-medium transition-colors"
              title="用 Python 自动打开浏览器完成设备码 OAuth，无需手动输入验证码"
            >
              {reauthManualBusy ? "自动授权中…" : "🤖 自动重授权 Manual 账号"}
            </button>
          )}
          {reauthManualMsg && (
            <div className="text-[10px] px-1 py-0.5 rounded bg-[#21262d] text-violet-300 break-all">
              {reauthManualMsg}
            </div>
          )}
          {/* IMAP IDLE 守护进程 */}
          <div className="flex items-center gap-1.5">
            <button
              onClick={toggleIdleDaemon}
              disabled={idleDaemonBusy}
              className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors disabled:opacity-50 ${
                idleDaemonRunning
                  ? "bg-teal-800/50 hover:bg-teal-800/70 text-teal-300"
                  : "bg-[#21262d] hover:bg-[#30363d] text-gray-400"
              }`}
              title="启动/停止 IMAP IDLE 守护进程，实时监听所有账号新邮件"
            >
              {idleDaemonBusy ? "…" : idleDaemonRunning ? "🔔 IDLE 运行中" : "🔕 IDLE 已停止"}
            </button>
            {idleEvents.length > 0 && (
              <button onClick={() => setIdleEvents([])} className="px-1.5 py-1.5 bg-[#21262d] hover:bg-[#30363d] rounded text-xs text-gray-500 transition-colors" title="清除 IDLE 事件">✕</button>
            )}
          </div>
          {idleEvents.length > 0 && (
            <div className="space-y-0.5 max-h-20 overflow-y-auto">
              {idleEvents.slice(-8).map((e, i) => (
                <div key={i} className="text-[10px] truncate px-1 py-0.5 rounded bg-[#161b22] text-teal-300">
                  📬 {e.email.split("@")[0]}: {e.subject}
                </div>
              ))}
            </div>
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

        {/* 自动检测按钮 — 固定区域，不随账号列表滚动 */}
        <div className="px-2 py-1.5 border-b border-[#21262d] shrink-0">
          <div className="flex gap-1">
            <button
              onClick={handleAutoCheck}
              disabled={checkBusy}
              className="flex-1 flex items-center justify-center gap-1 py-1 rounded text-xs font-medium bg-[#1c2128] hover:bg-[#21262d] border border-[#30363d] text-gray-300 disabled:opacity-50 transition-colors"
            >
              {checkBusy
                ? <><svg className="w-3 h-3 animate-spin mr-1" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/></svg>检测中…</>
                : <><svg className="w-3 h-3 mr-0.5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>立即检测（全部）</>
              }
            </button>
            {checkBusy && (
              <button
                onClick={handleStopCheck}
                disabled={checkStopBusy}
                title="停止检测"
                className="flex items-center justify-center px-2 py-1 rounded text-xs font-medium bg-red-950/40 hover:bg-red-900/50 border border-red-800/50 text-red-400 disabled:opacity-40 transition-colors"
              >
                {checkStopBusy
                  ? <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/></svg>
                  : <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>
                }
              </button>
            )}
          </div>
          {checkResult && (
            <p className={`text-[10px] mt-0.5 leading-tight ${checkResult.startsWith("✅") ? "text-emerald-400" : checkResult.startsWith("🛑") ? "text-orange-400" : checkResult.startsWith("❌") || checkResult.startsWith("⚠") ? "text-red-400" : "text-blue-400"}`}>
              {checkResult}
            </p>
          )}
          {checkProgress && checkProgress.total > 0 && (() => {
            const pct = Math.min(100, Math.round(checkProgress.checked / checkProgress.total * 100));
            const done = !checkBusy;
            return (
              <div className="mt-1">
                <div className="flex justify-between items-center mb-0.5">
                  <span className="text-[9px] text-gray-500">{checkProgress.checked}/{checkProgress.total}</span>
                  <span className={`text-[9px] font-medium ${done ? "text-emerald-400" : "text-blue-400"}`}>{pct}%</span>
                </div>
                <div className="w-full bg-[#161b22] rounded-full h-1.5">
                  <div
                    className={`h-1.5 rounded-full transition-all duration-500 ${done ? "bg-emerald-500" : "bg-blue-500"}`}
                    style={{ width: pct + "%" }}
                  />
                </div>
                <div className="flex gap-2 mt-0.5 flex-wrap">
                  <span className="text-[9px] text-emerald-400">&#x2713; {checkProgress.valid} 活跃</span>
                  <span className="text-[9px] text-red-400">&#x2717; {checkProgress.banned} 被封</span>
                  <span className="text-[9px] text-amber-400">&#x26a0; {checkProgress.needsAuth} 需授权</span>
                  <span className="text-[9px] text-gray-500">&#x25a1; {checkProgress.skipped} 跳过</span>
                </div>
              </div>
            );
          })()}
          <p className="text-[10px] text-gray-600 mt-0.5 leading-tight">↻ 后台每 4 小时自动检测全部</p>
        </div>
        <div className="flex-1 overflow-y-auto flex flex-col">
          {/* 状态过滤标签栏 */}
          <div className="px-2 py-1 border-b border-[#21262d] shrink-0 flex gap-1 flex-wrap">
            {([
              { key: "all",       label: "全部",   count: accTotal || accounts.length },
              { key: "active",    label: "活跃",   count: accounts.filter(a => a.status === "active").length },
              { key: "suspended", label: "🔴 被封", count: accounts.filter(a => a.status === "suspended").length },
              { key: "noauth",    label: "⚠ 未授权", count: accounts.filter(a => !hasOAuth(a) && !hasImap(a)).length },
              { key: "autofix",   label: "⏳ 自动修复", count: accounts.filter(a => a.status === "needs_oauth" && !a.tags?.includes("needs_oauth_manual")).length },
            ] as const).map(t => (
              <button key={t.key}
                onClick={() => setStatusFilter(t.key)}
                className={`text-[10px] px-1.5 py-0.5 rounded border transition-colors ${
                  statusFilter === t.key
                    ? "bg-blue-600/20 border-blue-500/40 text-blue-300"
                    : "border-transparent text-gray-500 hover:text-gray-300"
                }`}>
                {t.label} {t.count > 0 && <span className="opacity-60">({t.count})</span>}
              </button>
            ))}
          </div>
          {/* 被封账号管理操作栏 */}
          {statusFilter === "suspended" && (
            <div className="px-2 py-1.5 border-b border-[#21262d] shrink-0 space-y-1">
              <button
                onClick={bulkDeleteSuspended}
                disabled={bulkDelBusy || accounts.filter(a => a.status === "suspended").length === 0}
                className="w-full py-1 bg-red-800/50 hover:bg-red-800/70 disabled:opacity-40 rounded text-[11px] text-red-300 font-medium transition-colors"
              >
                {bulkDelBusy ? "删除中…" : `🗑 批量删除 ${accounts.filter(a => a.status === "suspended").length} 个被封账号`}
              </button>
              {bulkDelResult && <p className="text-[10px] text-gray-400">{bulkDelResult}</p>}
            </div>
          )}
          {/* 账号搜索过滤框 */}
          <div className="px-2 py-1.5 border-b border-[#21262d] shrink-0">
            <div className="relative">
              <input
                value={accSearch}
                onChange={e => setAccSearch(e.target.value)}
                placeholder="搜邮箱…"
                className="w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 pl-6 text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-blue-500"
              />
              <svg className="absolute left-1.5 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-600" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
              {accSearch && (
                <button onClick={() => setAccSearch("")} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 text-xs">✕</button>
              )}
            </div>
            {accSearch ? (
              <p className="text-[10px] text-gray-600 mt-0.5 px-0.5">
                搜索到 {accounts.length} / {accTotal} 个
              </p>
            ) : accTotal > accounts.length ? (
              <p className="text-[10px] text-gray-500 mt-0.5 px-0.5">
                已加载 {accounts.length} / {accTotal} 个 · 搜索可精确查找
              </p>
            ) : null}
          </div>
          <div className="flex-1 overflow-y-auto">
          {accounts.length === 0 && (
            <p className="text-xs text-gray-600 text-center mt-8 px-4">暂无 Outlook 账号<br/>去「Outlook 工作流」注册</p>
          )}
          {(accounts
            .filter(a => {
              if (statusFilter === "active")    return a.status === "active";
              if (statusFilter === "suspended") return a.status === "suspended";
              if (statusFilter === "noauth")    return !hasOAuth(a) && !hasImap(a);
            if (statusFilter === "autofix")   return a.status === "needs_oauth" && !a.tags?.includes("needs_oauth_manual");
              return true;
            })
            .filter(a => !accSearch || a.email.toLowerCase().includes(accSearch.toLowerCase()))
          ).map((acc) => {
            const active       = selAccount?.id === acc.id;
            const isSuspended  = acc.status === "suspended";
            const isNeedsOAuth   = acc.status === "needs_oauth" || acc.status === "needs_oauth_pending";
            const accTags      = tagsOf(acc);
            const isAutoPending  = (acc.status === "needs_oauth" && !accTags.includes("needs_oauth_manual")) || autoFixIds.waitingIds.has(acc.id);  // healthcheck 将自动修复
            const isOAuthPending = acc.status === "needs_oauth_pending" || autoFixIds.pendingIds.has(acc.id);  // healthcheck 正在处理中
            const isAbuse      = accTags.includes("abuse_mode");
            const isOAuth      = hasOAuth(acc) && !isSuspended;
            const isImap       = hasImap(acc) && !isSuspended;
            const noAccess     = !isOAuth && !isImap;
            // dot color: suspended=red, auto-pending=sky(pulse), oauth-pending=amber(pulse), oauth=green, imap=blue, none=amber
            const dot      = isSuspended ? "bg-red-500" : isOAuthPending ? "bg-sky-400 animate-pulse" : isAutoPending ? "bg-sky-400 animate-pulse" : isNeedsOAuth ? "bg-amber-400" : isOAuth ? "bg-emerald-400" : isImap ? "bg-blue-400" : "bg-amber-400";
            const label    = isSuspended ? "已停用" : isOAuthPending ? "补授权中…" : isAutoPending ? "等待自动修复" : isNeedsOAuth ? "需授权" : isOAuth ? "OAuth" : isImap ? "IMAP" : "需授权";
            const labelCls = isSuspended ? "text-red-500" : isOAuthPending ? "text-sky-400" : isAutoPending ? "text-sky-400" : isNeedsOAuth ? "text-amber-500" : isOAuth ? "text-emerald-500" : isImap ? "text-blue-400" : "text-amber-500";
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
                      {accTags.filter(tag => !HIDDEN_TAGS.has(tag) && TAG_LABELS[tag]).map(tag => (
                        <span key={tag} className={`text-[10px] px-1 py-0.5 rounded border ${TAG_CLASSES[tag] ?? "text-gray-400 bg-[#21262d] border-[#30363d]"}`}>
                          {TAG_LABELS[tag]}
                        </span>
                      ))}
                      {isSuspended && !isAbuse && (
                        <span className="text-[10px] px-1 py-0.5 rounded border text-gray-500 bg-[#161b22] border-[#21262d]">待核查</span>
                      )}
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
                {isAutoPending && !isConfirmDel && (
                  <div className="px-3 pb-1.5 flex items-center gap-1.5">
                    <span className="text-[10px] text-sky-400 animate-pulse">⏳</span>
                    <span className="text-[10px] text-sky-300">healthcheck 将自动补授权（约 5 分钟内）</span>
                  </div>
                )}
                {isOAuthPending && !isConfirmDel && (
                  <div className="px-3 pb-1.5 flex items-center gap-1.5">
                    <span className="text-[10px] text-sky-400 animate-spin">🔄</span>
                    <span className="text-[10px] text-sky-300 animate-pulse">正在自动补授权中…</span>
                  </div>
                )}
                {accTags.includes("needs_oauth_manual") && !isConfirmDel && (
                  <button
                    onClick={e => { e.stopPropagation(); startReauthManual([acc.id]); }}
                    disabled={reauthManualBusy}
                    className="w-full text-left px-3 pb-1.5 flex items-center gap-1.5 hover:opacity-80 disabled:opacity-40"
                    title="清零旧 token 并重新发起设备码授权"
                  >
                    <span className="text-[10px] text-violet-400">🔄</span>
                    <span className="text-[10px] text-violet-300 font-medium">
                      {reauthManualBusy ? "重授权中…" : "一键重授权"}
                    </span>
                  </button>
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
          <button
            onClick={() => { setShowCompose(s => !s); setSendResult(null); }}
            disabled={!selAccount}
            className={`px-2 py-1 border rounded text-xs transition-colors disabled:opacity-40 ${
              showCompose
                ? "bg-emerald-600/30 border-emerald-500/50 text-emerald-300 hover:bg-emerald-600/50"
                : "bg-[#21262d] hover:bg-[#30363d] border-[#30363d] text-gray-400 hover:text-white"
            }`}
            title={showCompose ? "关闭写信" : "写新邮件"}
          >
            {showCompose ? "✕" : "✉"}
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
                    {/* 一键授权：优先用 refresh_token 刷新，无则提示手动设备码 */}
                    <button
                      onClick={() => autoAuth(selAccount)}
                      disabled={authBusy === selAccount.id}
                      className="flex-1 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 rounded text-xs text-white font-semibold transition-colors"
                    >
                      {authBusy === selAccount.id ? "授权中…" : "⚡ 一键授权"}
                    </button>
                  </div>


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
              {/BasicAuthBlocked|LOGIN failed|基础认证|basic auth|AUTHENTICATIONFAILED|AUTHENTICATE|搜索需要 OAuth/i.test(error) ? (
                <div className="bg-amber-900/20 border border-amber-700/40 rounded-lg p-3 space-y-2">
                  <p className="text-xs text-amber-400 font-medium">⚠ 微软已全面停用 IMAP 密码登录（非账号封禁）</p>
                  <p className="text-[11px] text-gray-400 leading-5">
                    自 2022 年起，微软对所有 Outlook.com 个人账号停用了 IMAP 基础密码认证，这不是你的账号被封禁，而是整个协议被禁用。<br/><br/>
                    <span className="text-emerald-400 font-medium">解决方案（推荐）：</span>点击下方「获取授权」完成 OAuth 授权，授权后可通过 Graph API 读取所有邮件和搜索。
                  </p>
                </div>
              ) : (
                <p className="text-xs text-red-400">{error}</p>
              )}
            </div>
          )}
          {selAccount && needsAuth && error && /搜索需要 OAuth/i.test(error) && (
            <div className="p-3">
              <div className="bg-blue-900/20 border border-blue-700/40 rounded-lg p-3">
                <p className="text-xs text-blue-300 font-medium">🔍 搜索需要 OAuth 授权</p>
                <p className="text-[11px] text-gray-400 mt-1">此账号尚未完成 OAuth 授权，无法使用邮件搜索。请点击「获取授权」后即可搜索。</p>
              </div>
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
        {showCompose && selAccount ? (
          <div className="flex-1 flex flex-col p-5 gap-3 overflow-y-auto">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-white">✉ 新建邮件</h2>
              <span className="text-[11px] text-gray-500">从 {selAccount.email}</span>
            </div>
            <div className="space-y-2">
              <div>
                <label className="text-[11px] text-gray-500 block mb-1">收件人</label>
                <input
                  value={composeTo}
                  onChange={e => setComposeTo(e.target.value)}
                  placeholder="example@outlook.com（多个用逗号分隔）"
                  className="w-full bg-[#161b22] border border-[#30363d] focus:border-blue-500 rounded px-3 py-2 text-xs text-gray-200 placeholder-gray-600 outline-none"
                />
              </div>
              <div>
                <label className="text-[11px] text-gray-500 block mb-1">主题</label>
                <input
                  value={composeSubject}
                  onChange={e => setComposeSubject(e.target.value)}
                  placeholder="邮件主题"
                  className="w-full bg-[#161b22] border border-[#30363d] focus:border-blue-500 rounded px-3 py-2 text-xs text-gray-200 placeholder-gray-600 outline-none"
                />
              </div>
              <div>
                <label className="text-[11px] text-gray-500 block mb-1">正文</label>
                <textarea
                  value={composeBody}
                  onChange={e => setComposeBody(e.target.value)}
                  placeholder="输入邮件内容…"
                  rows={10}
                  className="w-full bg-[#161b22] border border-[#30363d] focus:border-blue-500 rounded px-3 py-2 text-xs text-gray-200 placeholder-gray-600 outline-none resize-none"
                />
              </div>
            </div>
            {sendResult && (
              <p className={`text-[11px] ${sendResult.ok ? "text-emerald-400" : "text-red-400"}`}>
                {sendResult.msg}
              </p>
            )}
            <div className="flex gap-2">
              <button
                onClick={sendMessage}
                disabled={sendBusy || !composeTo.trim() || !composeSubject.trim()}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded text-xs text-white font-semibold transition-colors"
              >
                {sendBusy ? "发送中…" : "发送"}
              </button>
              <button
                onClick={() => { setShowCompose(false); setSendResult(null); }}
                className="px-3 py-2 bg-[#21262d] hover:bg-[#30363d] rounded text-xs text-gray-400 transition-colors"
              >
                取消
              </button>
            </div>
          </div>
        ) : !selMsg ? (
          <div className="flex-1 flex items-center justify-center">
            <p className="text-xs text-gray-600">
              {selAccount ? "← 选择邮件查看 · 或点击 ✉ 写邮件" : "← 选择左侧账号"}
            </p>
          </div>
        ) : null}
        {!showCompose && selMsg && (
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

      {autoCompleteOpen && (
        <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4">
          <div className="bg-[#161b22] border border-[#30363d] rounded-xl w-full max-w-2xl flex flex-col max-h-[80vh]">
            <div className="px-5 py-3 border-b border-[#30363d] flex items-center justify-between">
              <h3 className="text-sm font-semibold text-white">🤖 自动完成授权 — 实时日志</h3>
              <button onClick={() => { setAutoCompleteOpen(false); if (autoCompleteLogRef.current) { clearInterval(autoCompleteLogRef.current); autoCompleteLogRef.current = null; } }} className="text-gray-400 hover:text-white text-lg leading-none px-1" title="关闭">✕</button>
            </div>
            <div className="flex-1 overflow-y-auto p-4 font-mono text-xs space-y-0.5">
              {autoCompleteLog.length === 0
                ? <p className="text-gray-600 animate-pulse">等待输出…</p>
                : autoCompleteLog.map((line, i) => (
                  <p key={i} className={
                    line.includes("✅") ? "text-emerald-400" :
                    line.includes("❌") ? "text-red-400" :
                    line.includes("⚠") ? "text-amber-400" :
                    line.includes("[summary]") ? "text-blue-300 font-bold" :
                    line.startsWith("  •") ? "text-gray-500" :
                    "text-gray-300"
                  }>{line}</p>
                ))
              }
            </div>
            <div className="px-5 py-2 border-t border-[#30363d] flex items-center gap-2">
              {autoCompleteDone
                ? <p className="text-xs text-emerald-400 font-medium">✅ 授权任务已完成</p>
                : <p className="text-[10px] text-gray-600 animate-pulse">运行中，每 3 秒刷新… 日志：{autoCompleteLogFile}</p>
              }
            </div>
          </div>
        </div>
      )}

      {reauthManualOpen && (
        <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4">
          <div className="bg-[#161b22] border border-[#30363d] rounded-xl w-full max-w-2xl flex flex-col max-h-[80vh]">
            <div className="px-5 py-3 border-b border-[#30363d] flex items-center justify-between">
              <h3 className="text-sm font-semibold text-white">🔄 重授权 needs_oauth_manual — 实时日志</h3>
              <button onClick={() => { setReauthManualOpen(false); if (reauthManualLogRef.current) { clearInterval(reauthManualLogRef.current); reauthManualLogRef.current = null; } }} className="text-gray-400 hover:text-white text-lg leading-none px-1" title="关闭">✕</button>
            </div>
            <div className="flex-1 overflow-y-auto p-4 font-mono text-xs space-y-0.5">
              {reauthManualLog.length === 0
                ? <p className="text-gray-600 animate-pulse">等待输出…</p>
                : reauthManualLog.map((line, i) => (
                  <p key={i} className={
                    line.includes("✅") ? "text-emerald-400" :
                    line.includes("❌") ? "text-red-400" :
                    line.includes("⚠") ? "text-amber-400" :
                    line.includes("[summary]") ? "text-violet-300 font-bold" :
                    line.startsWith("  •") ? "text-gray-500" :
                    "text-gray-300"
                  }>{line}</p>
                ))
              }
            </div>
            <div className="px-5 py-2 border-t border-[#30363d] flex items-center gap-2">
              {reauthManualDone
                ? <p className="text-xs text-emerald-400 font-medium">✅ 重授权任务已完成</p>
                : <p className="text-[10px] text-gray-600 animate-pulse">运行中，每 3 秒刷新… 日志：{reauthManualLogFile}</p>
              }
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
