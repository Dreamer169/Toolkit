#!/usr/bin/env python3
"""
IMAP IDLE 守护进程 v2 — 路B实现

优先: imapclient IDLE（XOAUTH2，需要 OUTLOOK_CLIENT_ID 注册了 IMAP.AccessAsUser.All）
回退: Microsoft Graph API 轮询（所有现有账号可用）

环境变量:
  DATABASE_URL        — PostgreSQL 连接串
  OUTLOOK_CLIENT_ID   — 自己注册的 Azure AD App client_id（支持 IMAP.AccessAsUser.All）
                        若未设置则仅用 Graph API 轮询模式

Azure App 注册步骤（一次性，免费）:
  1. 打开 https://portal.azure.com → Azure Active Directory → 应用注册 → 新注册
  2. 名称随意，受支持的账户类型选"任何组织目录中的账户和个人 Microsoft 账户"
  3. 注册后进入应用 → API 权限 → 添加权限 → Microsoft Graph → 委托权限 →
     勾选: IMAP.AccessAsUser.All, SMTP.Send, Mail.Read, Mail.ReadWrite, Mail.Send, User.Read, offline_access
  4. 继续添加 → Outlook / Exchange → IMAP.AccessAsUser.All, SMTP.Send
  5. 身份验证 → 添加平台 → 移动和桌面应用 → 勾选 https://login.microsoftonline.com/common/oauth2/nativeclient
     并启用"允许公共客户端流"
  6. 概述页面复制 client_id → 设置到环境变量 OUTLOOK_CLIENT_ID
  7. 执行: python3 reauth_imap.py   重新授权现有账号
"""
import base64, json, os, signal, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

# ── 常量 ─────────────────────────────────────────────────────────────────────
THUNDERBIRD_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
CLIENT_ID   = os.environ.get("OUTLOOK_CLIENT_ID", THUNDERBIRD_CLIENT_ID)
IMAP_CAPABLE = CLIENT_ID != THUNDERBIRD_CLIENT_ID   # 注册了正确权限的 client_id

IMAP_SCOPE  = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
GRAPH_SCOPE = (
    "https://graph.microsoft.com/Mail.Read "
    "https://graph.microsoft.com/User.Read "
    "offline_access"
)
TOKEN_URL   = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
GRAPH_BASE  = "https://graph.microsoft.com/v1.0"

IMAP_HOST   = "outlook.office365.com"
IMAP_PORT   = 993

POLL_INTERVAL    = 30       # Graph 轮询间隔（秒）
IDLE_TIMEOUT     = 25 * 60  # IMAP IDLE 最长等待（25min，微软30min断开）
MAX_ACCOUNTS     = 60
REFRESH_INTERVAL = 300      # 重新从 DB 加载账号列表的间隔

EVENTS_FILE = Path("/tmp/imap_idle_events.json")
STATUS_FILE = Path("/tmp/imap_idle_status.json")
PID_FILE    = Path("/tmp/imap_idle_daemon.pid")

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")

_stop_event = threading.Event()
_ev_lock    = threading.Lock()
_st_lock    = threading.Lock()
_threads: dict[int, threading.Thread] = {}


# ── 持久化 ────────────────────────────────────────────────────────────────────

def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default

def _save_json(path: Path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, default=str), "utf-8")
    tmp.replace(path)

def _append_event(evt: dict):
    with _ev_lock:
        evts = _load_json(EVENTS_FILE, [])
        seen_ids = {e.get("msg_id") for e in evts if e.get("msg_id")}
        if evt.get("msg_id") and evt["msg_id"] in seen_ids:
            return
        evts.append(evt)
        _save_json(EVENTS_FILE, evts[-500:])

def _set_status(acct_id: int, email: str, status: str, error: str = "", via: str = ""):
    with _st_lock:
        st = _load_json(STATUS_FILE, {})
        st[str(acct_id)] = {
            "email": email, "status": status, "error": error, "via": via,
            "updated_at": datetime.utcnow().isoformat(),
        }
        _save_json(STATUS_FILE, st)


# ── 数据库 ────────────────────────────────────────────────────────────────────

def _get_accounts():
    import psycopg2
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, email, token, refresh_token
            FROM accounts
            WHERE platform='outlook'
              AND status NOT IN ('suspended')
              AND (token IS NOT NULL AND token <> ''
                   OR refresh_token IS NOT NULL AND refresh_token <> '')
            ORDER BY updated_at DESC
            LIMIT %s
        """, (MAX_ACCOUNTS,))
        return [
            {"id": r[0], "email": r[1], "token": r[2] or "", "refresh_token": r[3] or ""}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()

def _update_db_tokens(acct_id: int, access_token: str, refresh_token: str):
    import psycopg2
    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE accounts SET token=%s, refresh_token=%s, updated_at=NOW() WHERE id=%s",
                (access_token, refresh_token, acct_id)
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"[daemon] ⚠ DB token 更新失败 id={acct_id}: {e}", flush=True)


# ── Token 刷新 ────────────────────────────────────────────────────────────────

def _exchange_token(refresh_tok: str, scope: str, client_id: str = CLIENT_ID) -> tuple[str, str]:
    body = urlencode({
        "grant_type":    "refresh_token",
        "client_id":     client_id,
        "refresh_token": refresh_tok,
        "scope":         scope,
    }).encode()
    req = Request(TOKEN_URL, data=body,
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        resp = json.loads(urlopen(req, timeout=20).read())
    except HTTPError as e:
        body_bytes = e.read()
        try:
            err_body = json.loads(body_bytes)
            raise RuntimeError(f"{err_body.get('error','HTTP'+str(e.code))}: {err_body.get('error_description','')[:120]}")
        except (json.JSONDecodeError, KeyError):
            raise RuntimeError(f"HTTP {e.code}: {body_bytes[:80]}")
    at = resp.get("access_token", "")
    rt = resp.get("refresh_token", refresh_tok)
    if not at:
        raise RuntimeError(f"{resp.get('error','?')}: {resp.get('error_description','')[:120]}")
    return at, rt

def _get_graph_token(refresh_tok: str) -> tuple[str, str]:
    return _exchange_token(refresh_tok, GRAPH_SCOPE)

def _get_imap_token(refresh_tok: str) -> tuple[str, str]:
    """获取 IMAP XOAUTH2 专用 token（需要 CLIENT_ID 有 IMAP 权限）。"""
    return _exchange_token(refresh_tok, IMAP_SCOPE)


# ── Graph API 读邮件 ──────────────────────────────────────────────────────────

def _graph_request(path: str, token: str) -> dict:
    url = GRAPH_BASE + path
    req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        return json.loads(urlopen(req, timeout=20).read())
    except HTTPError as e:
        raise RuntimeError(f"Graph {e.code}: {e.read()[:80]}")

def _fetch_graph_new(token: str, since_dt: str | None, top: int = 10) -> list[dict]:
    params = {
        "$top": str(top),
        "$select": "id,subject,from,receivedDateTime,isRead",
        "$orderby": "receivedDateTime desc",
    }
    if since_dt:
        params["$filter"] = f"receivedDateTime gt {since_dt}"
    qs = "&".join(f"{k}={quote(str(v), safe='$@')}" for k, v in params.items())
    data = _graph_request(f"/me/mailFolders/inbox/messages?{qs}", token)
    return list(reversed(data.get("value", [])))


# ── IMAP IDLE 工作线程 ────────────────────────────────────────────────────────

def _emit_imap_message(acct: dict, msg_data: dict):
    evt = {
        "account_id": acct["id"],
        "email":      acct["email"],
        "msg_id":     str(msg_data.get("uid", msg_data.get("seq", ""))),
        "subject":    msg_data.get("subject", ""),
        "from":       msg_data.get("from", ""),
        "date":       msg_data.get("date", ""),
        "is_unread":  not msg_data.get("is_read", False),
        "via":        "imap_idle",
        "ts":         datetime.utcnow().isoformat(),
    }
    _append_event(evt)
    print(f"[idle] 📬 {acct['email']}: {msg_data.get('subject','')[:60]}", flush=True)


def _imap_idle_worker(acct: dict, imap_token: str, refresh_tok: str):
    """真正的 IMAP IDLE 循环，有新邮件时立即触发。"""
    from imapclient import IMAPClient
    import email as email_lib
    from email.header import decode_header, make_header

    def _decode_subj(raw):
        try:
            return str(make_header(decode_header(raw or "")))
        except Exception:
            return raw or ""

    acct_id = acct["id"]
    email   = acct["email"]
    consecutive_errors = 0

    while not _stop_event.is_set():
        try:
            with IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True, timeout=60) as client:
                client.oauth2_login(email, imap_token)
                client.select_folder("INBOX")

                # 获取当前最大 UID 作为基线，避免对历史邮件触发事件
                all_uids = client.search(["ALL"])
                last_uid = max(all_uids) if all_uids else 0
                _set_status(acct_id, email, "idle", via="imap_idle")
                print(f"[idle] ✅ {email}: IMAP IDLE 就绪，共 {len(all_uids)} 封，last_uid={last_uid}", flush=True)
                consecutive_errors = 0

                client.idle()
                while not _stop_event.is_set():
                    try:
                        responses = client.idle_check(timeout=IDLE_TIMEOUT)
                    except Exception:
                        break

                    if not responses:
                        # 接近30分钟超时，重置 IDLE
                        client.idle_done()
                        if _stop_event.is_set():
                            return
                        client.idle()
                        continue

                    # 检查是否有新邮件通知
                    has_new = any(
                        len(r) >= 2 and r[1] == b"EXISTS"
                        for r in responses
                    )
                    if not has_new:
                        continue

                    client.idle_done()

                    # 拉取 uid > last_uid 的新邮件
                    new_uids = [u for u in client.search(["ALL"]) if u > last_uid]
                    if new_uids:
                        fetch_data = client.fetch(new_uids, ["RFC822", "FLAGS", "ENVELOPE"])
                        for uid, data in fetch_data.items():
                            raw = data.get(b"RFC822")
                            flags = data.get(b"FLAGS", ())
                            is_read = b"\\Seen" in flags
                            if raw:
                                msg = email_lib.message_from_bytes(raw)
                                subj = _decode_subj(msg.get("Subject", ""))
                                frm  = msg.get("From", "")
                                dt   = msg.get("Date", "")
                            else:
                                env = data.get(b"ENVELOPE")
                                subj = str(env.subject or b"") if env else ""
                                frm  = ""
                                dt   = ""
                            _emit_imap_message(acct, {
                                "uid": uid, "subject": subj,
                                "from": frm, "date": dt, "is_read": is_read,
                            })
                            if uid > last_uid:
                                last_uid = uid

                    client.idle()

        except Exception as e:
            err = str(e)
            consecutive_errors += 1
            print(f"[idle] ⚠ {email}: IMAP 错误({consecutive_errors}): {err[:100]}", flush=True)
            _set_status(acct_id, email, "error", err, via="imap_idle")

            # Token 过期 → 刷新
            if any(k in err.lower() for k in ["401", "authenticationfailed", "token", "expired",
                                               "invalid", "credentials", "authenticate"]):
                if refresh_tok:
                    try:
                        new_at, new_rt = _get_imap_token(refresh_tok)
                        _update_db_tokens(acct_id, new_at, new_rt)
                        imap_token  = new_at
                        refresh_tok = new_rt
                        print(f"[idle] 🔄 {email}: IMAP token 已刷新", flush=True)
                        consecutive_errors = 0
                        continue
                    except RuntimeError as re2:
                        print(f"[idle] ❌ {email}: IMAP token 刷新失败: {re2}", flush=True)

            if consecutive_errors >= 5:
                print(f"[idle] 停止 IMAP IDLE {email} (5次连续错误)", flush=True)
                _set_status(acct_id, email, "disabled", err, via="imap_idle")
                return

            for _ in range(min(10 * consecutive_errors, 120)):
                if _stop_event.is_set():
                    return
                time.sleep(1)


# ── Graph 轮询工作线程 ────────────────────────────────────────────────────────

def _graph_poll_worker(acct: dict, graph_token: str, refresh_tok: str):
    acct_id = acct["id"]
    email   = acct["email"]
    consecutive_errors = 0

    # 基线
    try:
        msgs = _fetch_graph_new(graph_token, since_dt=None, top=1)
        last_dt = msgs[-1]["receivedDateTime"] if msgs else None
        _set_status(acct_id, email, "polling", via="graph")
        print(f"[poll] ✅ {email}: Graph OK, last_dt={last_dt or 'none'}", flush=True)
    except RuntimeError as e:
        last_dt = None
        print(f"[poll] ⚠ {email}: 初始 Graph 失败: {e}", flush=True)
        _set_status(acct_id, email, "error", str(e), via="graph")

    while not _stop_event.is_set():
        for _ in range(POLL_INTERVAL):
            if _stop_event.is_set():
                return
            time.sleep(1)

        try:
            new_msgs = _fetch_graph_new(graph_token, since_dt=last_dt, top=10)
            for m in new_msgs:
                from_ = m.get("from", {}).get("emailAddress", {})
                sender = from_.get("address", "")
                name   = from_.get("name", "")
                recv_dt = m.get("receivedDateTime", "")
                evt = {
                    "account_id": acct_id,
                    "email":      email,
                    "msg_id":     m.get("id", ""),
                    "subject":    m.get("subject", ""),
                    "from":       f"{name} <{sender}>" if name else sender,
                    "date":       recv_dt,
                    "is_unread":  not m.get("isRead", True),
                    "via":        "graph_poll",
                    "ts":         datetime.utcnow().isoformat(),
                }
                _append_event(evt)
                print(f"[poll] 📬 {email}: {m.get('subject','')[:60]}", flush=True)
                if recv_dt and recv_dt > (last_dt or ""):
                    last_dt = recv_dt

            consecutive_errors = 0
            _set_status(acct_id, email, "polling", via="graph")

        except RuntimeError as e:
            err = str(e)
            consecutive_errors += 1
            _set_status(acct_id, email, "error", err, via="graph")

            if any(k in err for k in ["401", "InvalidAuthentication", "token"]):
                if refresh_tok:
                    try:
                        new_at, new_rt = _get_graph_token(refresh_tok)
                        _update_db_tokens(acct_id, new_at, new_rt)
                        graph_token = new_at
                        refresh_tok = new_rt
                        print(f"[poll] 🔄 {email}: Graph token 已刷新", flush=True)
                        consecutive_errors = 0
                    except RuntimeError as re2:
                        print(f"[poll] ❌ {email}: Graph token 刷新失败: {re2}", flush=True)

            if consecutive_errors >= 10:
                _set_status(acct_id, email, "disabled", err, via="graph")
                return


# ── 账号工作线程入口 ──────────────────────────────────────────────────────────

def _account_worker(acct: dict):
    acct_id = acct["id"]
    email   = acct["email"]
    rt      = acct["refresh_token"]
    token   = acct["token"]

    _set_status(acct_id, email, "starting")

    # ── 先刷新 Graph token（两条路都需要）
    if rt:
        try:
            new_at, new_rt = _get_graph_token(rt)
            if new_at != token or new_rt != rt:
                _update_db_tokens(acct_id, new_at, new_rt)
            token = new_at
            rt    = new_rt
        except RuntimeError as e:
            if not token:
                print(f"[daemon] {email}: Graph token 刷新失败，跳过: {e}", flush=True)
                _set_status(acct_id, email, "needs_oauth", str(e))
                return
            # 有旧 token 继续尝试
            print(f"[daemon] ⚠ {email}: Graph token 刷新失败，用旧 token: {e}", flush=True)
    elif not token:
        _set_status(acct_id, email, "needs_oauth", "no token")
        return

    # ── 路B: 尝试 IMAP IDLE（仅当 OUTLOOK_CLIENT_ID 是自己注册的 app）
    if IMAP_CAPABLE:
        print(f"[daemon] 🔑 {email}: 尝试 IMAP token (client_id={CLIENT_ID[:8]}...)", flush=True)
        try:
            imap_at, imap_rt = _get_imap_token(rt)
            print(f"[daemon] 🔒 {email}: IMAP token OK → 启动 IMAP IDLE", flush=True)
            _imap_idle_worker(acct, imap_at, imap_rt)
            return
        except RuntimeError as e:
            print(f"[daemon] ↓ {email}: IMAP token 失败({e}), 回退 Graph 轮询", flush=True)
    else:
        print(f"[daemon] ℹ {email}: 未设置 OUTLOOK_CLIENT_ID → Graph 轮询模式", flush=True)

    # ── 路A 兜底: Graph API 轮询
    _graph_poll_worker(acct, token, rt)


# ── 主进程 ────────────────────────────────────────────────────────────────────

def main():
    PID_FILE.write_text(str(os.getpid()))

    if IMAP_CAPABLE:
        print(f"[daemon] PID={os.getpid()} 启动 — 模式: IMAP IDLE (client_id={CLIENT_ID[:8]}...)", flush=True)
    else:
        print(f"[daemon] PID={os.getpid()} 启动 — 模式: Graph 轮询 (未设置 OUTLOOK_CLIENT_ID)", flush=True)
        print(f"[daemon] ℹ 设置 OUTLOOK_CLIENT_ID 环境变量后将启用真正的 IMAP IDLE", flush=True)

    def _sig(sig, frame):
        print(f"[daemon] 收到信号 {sig}，停止...", flush=True)
        _stop_event.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)

    try:
        accounts = _get_accounts()
    except Exception as e:
        print(f"[daemon] 读取账号失败: {e}", flush=True)
        sys.exit(1)

    print(f"[daemon] 加载 {len(accounts)} 个账号", flush=True)
    for acc in accounts:
        t = threading.Thread(
            target=_account_worker, args=(acc,),
            daemon=True, name=f"acct-{acc['email']}"
        )
        t.start()
        _threads[acc["id"]] = t
        time.sleep(0.1)

    last_refresh = time.time()
    while not _stop_event.is_set():
        time.sleep(5)
        if time.time() - last_refresh >= REFRESH_INTERVAL:
            last_refresh = time.time()
            try:
                new_accs = _get_accounts()
                existing = set(_threads.keys())
                for acc in new_accs:
                    if acc["id"] not in existing:
                        print(f"[daemon] 新账号 {acc['email']} 加入监听", flush=True)
                        t = threading.Thread(
                            target=_account_worker, args=(acc,),
                            daemon=True, name=f"acct-{acc['email']}"
                        )
                        t.start()
                        _threads[acc["id"]] = t
            except Exception as e:
                print(f"[daemon] 刷新账号失败: {e}", flush=True)

    PID_FILE.unlink(missing_ok=True)
    print("[daemon] 已退出", flush=True)


if __name__ == "__main__":
    main()
