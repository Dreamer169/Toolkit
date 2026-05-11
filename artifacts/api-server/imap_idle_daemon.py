#!/usr/bin/env python3
"""
IMAP IDLE 守护进程 — 为有 token 的 Outlook 账号实时监听新邮件。

当前状态:
  IMAP XOAUTH2 对所有账号均返回 AUTHENTICATE failed。
  原因: client_id (9e5f94bc-..., Thunderbird 公开应用) 在 Azure AD 中
       未注册 IMAP.AccessAsUser.All delegated permission，Microsoft
       即使接受 token 换取请求也不会在 token 里附带 IMAP scope。
  解决方案: 注册自有 Azure AD 应用并申请 IMAP.AccessAsUser.All 权限，
            或改用 Graph API (Mail.Read) 读邮件。

用法:
  DATABASE_URL=postgresql://... python3 imap_idle_daemon.py
"""
import base64, email as email_lib, json, os, signal, sys, threading, time
from datetime import datetime
from email.header import decode_header, make_header
from pathlib import Path

EVENTS_FILE  = Path("/tmp/imap_idle_events.json")
STATUS_FILE  = Path("/tmp/imap_idle_status.json")
PID_FILE     = Path("/tmp/imap_idle_daemon.pid")

IMAP_HOST_PRIMARY  = "outlook.live.com"
IMAP_HOST_FALLBACK = "outlook.office365.com"
IMAP_PORT          = 993
IDLE_CYCLE_SECS    = 25 * 60   # re-issue IDLE every 25 min
MAX_ACCOUNTS       = 60        # cap concurrent IMAP connections
REFRESH_INTERVAL   = 300       # reload DB account list every 5 min

CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

# IMAP-specific scope for personal Outlook.com accounts
IMAP_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"

_stop_event = threading.Event()
_ev_lock    = threading.Lock()
_st_lock    = threading.Lock()
_threads: dict[int, threading.Thread] = {}

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")


# ── persistence helpers ───────────────────────────────────────────────────────

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
        evts.append(evt)
        _save_json(EVENTS_FILE, evts[-500:])

def _set_status(acct_id: int, email: str, status: str, error: str = ""):
    with _st_lock:
        st = _load_json(STATUS_FILE, {})
        st[str(acct_id)] = {
            "email": email, "status": status, "error": error,
            "updated_at": datetime.utcnow().isoformat(),
        }
        _save_json(STATUS_FILE, st)


# ── DB helpers ────────────────────────────────────────────────────────────────

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
              AND (
                    (token IS NOT NULL AND token <> '')
                 OR (refresh_token IS NOT NULL AND refresh_token <> '')
              )
            ORDER BY updated_at DESC
            LIMIT %s
        """, (MAX_ACCOUNTS,))
        return [
            {"id": r[0], "email": r[1], "token": r[2] or "", "refresh_token": r[3] or ""}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def _refresh_imap_token(refresh_tok: str) -> tuple[str, str]:
    """
    Exchange refresh_token for an IMAP access_token.

    Uses two endpoints as fallback (luoianun pattern):
      1. consumers + outlook.office.com/IMAP scope
      2. login.live.com (legacy, no explicit scope)

    NOTE: With the current Thunderbird client_id, token exchange succeeds
    but the returned token lacks IMAP.AccessAsUser.All scope. AUTHENTICATE
    will fail regardless. This function is kept for when a properly-scoped
    client_id is configured.
    """
    import urllib.request, urllib.parse

    methods = [
        {
            "url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            "data": {
                "grant_type":    "refresh_token",
                "client_id":     CLIENT_ID,
                "refresh_token": refresh_tok,
                "scope":         IMAP_SCOPE,
            },
            "label": "consumers/IMAP",
        },
        {
            "url": "https://login.live.com/oauth20_token.srf",
            "data": {
                "grant_type":    "refresh_token",
                "client_id":     CLIENT_ID,
                "refresh_token": refresh_tok,
            },
            "label": "login.live.com",
        },
    ]

    last_error = "no attempt"
    for method in methods:
        try:
            body = urllib.parse.urlencode(method["data"]).encode()
            req  = urllib.request.Request(
                method["url"],
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
            at = resp.get("access_token", "")
            rt = resp.get("refresh_token", refresh_tok)
            if at:
                print(f"[idle] token refresh OK via {method['label']}", flush=True)
                return at, rt
            last_error = f"{resp.get('error', '?')}: {resp.get('error_description', '')[:80]}"
            print(f"[idle] ⚠ {method['label']} 失败: {last_error}", flush=True)
        except Exception as e:
            last_error = str(e)[:120]
            print(f"[idle] ⚠ {method['label']} 异常: {last_error}", flush=True)

    raise RuntimeError(f"token 刷新失败（所有端点）: {last_error}")


def _update_db_token(acct_id: int, access_token: str, refresh_token: str):
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
        print(f"[idle] ⚠ DB token update failed for id={acct_id}: {e}", flush=True)


# ── message helpers ───────────────────────────────────────────────────────────

def _decode_subject(raw):
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return raw or ""


# ── IMAP IDLE worker ──────────────────────────────────────────────────────────

def _idle_worker(acct: dict):
    from imapclient import IMAPClient
    from imapclient import exceptions as imap_exc

    acct_id = acct["id"]
    email   = acct["email"]
    token   = acct["token"] or ""
    rt      = acct["refresh_token"] or ""

    _set_status(acct_id, email, "starting")

    # ── Step 1: refresh token ─────────────────────────────────────────────────
    if rt:
        try:
            new_at, new_rt = _refresh_imap_token(rt)
            if new_at != token or new_rt != rt:
                _update_db_token(acct_id, new_at, new_rt)
            token = new_at
            rt    = new_rt
            print(f"[idle] 🔄 {email}: token 已刷新", flush=True)
        except RuntimeError as e:
            err_str = str(e)
            if not token:
                print(f"[idle] {email}: refresh 失败且无 token，needs_oauth: {err_str}", flush=True)
                _set_status(acct_id, email, "needs_oauth", err_str)
                return
            print(f"[idle] ⚠ {email}: refresh 失败，尝试旧 token: {err_str}", flush=True)
    elif not token:
        _set_status(acct_id, email, "needs_oauth", "no token and no refresh_token")
        return

    # ── Step 2: IMAP connection ───────────────────────────────────────────────
    imap_host = IMAP_HOST_PRIMARY
    consecutive_errors = 0

    while not _stop_event.is_set():
        client = None
        try:
            connected   = False
            auth_failed = False
            last_error  = ""

            for host in (imap_host, IMAP_HOST_FALLBACK):
                try:
                    client = IMAPClient(host, IMAP_PORT, ssl=True, timeout=30.0)
                    client.oauth2_login(email, token)
                    imap_host = host
                    connected = True
                    break
                except Exception as ce:
                    err_str = str(ce)
                    last_error = err_str
                    print(f"[idle] ⚠ {email}: {host} 连接失败: {err_str}", flush=True)
                    if "AUTHENTICATE failed" in err_str:
                        auth_failed = True
                    try:
                        client.logout()
                    except Exception:
                        pass
                    client = None

            if not connected:
                if auth_failed:
                    # XOAUTH2 auth failed — current client_id lacks IMAP.AccessAsUser.All scope.
                    # Marking imap_disabled; switch to Graph API for mail reading.
                    _set_status(acct_id, email, "imap_disabled",
                                "XOAUTH2 AUTHENTICATE failed: client_id lacks IMAP.AccessAsUser.All scope")
                    print(f"[idle] 🚫 {email}: IMAP AUTHENTICATE failed (client_id scope issue)", flush=True)
                    return
                raise ConnectionError(f"两个 IMAP host 均连接失败: {last_error}")

            # ── Connected ─────────────────────────────────────────────────────
            mailbox        = client.select_folder("INBOX", readonly=True)
            idle_supported = b"IDLE" in client.capabilities()
            all_uids       = client.search(["ALL"])
            last_uid       = max(all_uids) if all_uids else 0

            _set_status(acct_id, email, "idle" if idle_supported else "poll")
            consecutive_errors = 0
            print(
                f"[idle] ✅ {email}: 已连接 {imap_host}, "
                f"IDLE={'yes' if idle_supported else 'no'}, last_uid={last_uid}",
                flush=True,
            )

            # ── IDLE / poll loop ──────────────────────────────────────────────
            while not _stop_event.is_set():
                if idle_supported:
                    client.idle()
                    try:
                        responses = client.idle_check(timeout=IDLE_CYCLE_SECS)
                    except (imap_exc.IMAPClientAbortError, imap_exc.IllegalStateError, OSError):
                        responses = []
                    finally:
                        try:
                            client.idle_done()
                        except Exception:
                            pass

                    if not responses:
                        continue
                else:
                    for _ in range(60):
                        if _stop_event.is_set():
                            break
                        time.sleep(1)

                # ── fetch new messages ────────────────────────────────────────
                try:
                    new_uids = client.search(["UID", f"{last_uid + 1}:*"])
                    new_uids = [u for u in new_uids if u > last_uid]
                except Exception:
                    new_uids = []

                for uid in new_uids[:10]:
                    try:
                        fetched = client.fetch([uid], ["RFC822.HEADER", "FLAGS"])
                        payload = fetched.get(uid, {})
                        raw_hdr = payload.get(b"RFC822.HEADER") or payload.get("RFC822.HEADER")
                        flags   = payload.get(b"FLAGS") or payload.get("FLAGS", ())
                        if raw_hdr:
                            msg       = email_lib.message_from_bytes(raw_hdr)
                            subject   = _decode_subject(msg.get("Subject", ""))
                            from_     = msg.get("From", "")
                            date_     = msg.get("Date", "")
                            is_unread = b"\\Seen" not in flags
                            evt = {
                                "account_id": acct_id, "email": email,
                                "uid": uid, "subject": subject,
                                "from": from_, "date": date_,
                                "is_unread": is_unread,
                                "ts": datetime.utcnow().isoformat(),
                            }
                            _append_event(evt)
                            print(f"[idle] 📬 {email}: {subject[:60]}", flush=True)
                        last_uid = max(last_uid, uid)
                    except Exception as fe:
                        print(f"[idle] fetch err {email} uid={uid}: {fe}", flush=True)

        except Exception as e:
            consecutive_errors += 1
            err_str = str(e)[:160]
            _set_status(acct_id, email, "error", err_str)
            print(f"[idle] ❌ {email}: {err_str} (errors={consecutive_errors})", flush=True)

            if consecutive_errors >= 5:
                _set_status(acct_id, email, "disabled", "Too many network errors")
                print(f"[idle] 停止监听 {email}", flush=True)
                return

            wait = min(30 * (2 ** (consecutive_errors - 1)), 300)
            print(f"[idle] {email} 等待 {wait}s 后重连...", flush=True)
            for _ in range(wait):
                if _stop_event.is_set():
                    return
                time.sleep(1)

            if rt:
                try:
                    new_at, new_rt = _refresh_imap_token(rt)
                    if new_at != token or new_rt != rt:
                        _update_db_token(acct_id, new_at, new_rt)
                    token = new_at
                    rt    = new_rt
                    print(f"[idle] 🔄 {email}: 重连前刷新 token", flush=True)
                except Exception:
                    pass

        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    PID_FILE.write_text(str(os.getpid()))
    print(f"[idle-daemon] PID={os.getpid()} 启动...", flush=True)

    def _sig(sig, frame):
        print(f"[idle-daemon] 收到信号 {sig}，停止...", flush=True)
        _stop_event.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)

    try:
        accounts = _get_accounts()
    except Exception as e:
        print(f"[idle-daemon] 读取账号失败: {e}", flush=True)
        sys.exit(1)

    print(f"[idle-daemon] 加载 {len(accounts)} 个账号", flush=True)
    for acc in accounts:
        t = threading.Thread(
            target=_idle_worker, args=(acc,),
            daemon=True, name=f"idle-{acc['email']}"
        )
        t.start()
        _threads[acc["id"]] = t
        time.sleep(0.15)

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
                        print(f"[idle-daemon] 新账号 {acc['email']} 加入监听", flush=True)
                        t = threading.Thread(
                            target=_idle_worker, args=(acc,),
                            daemon=True, name=f"idle-{acc['email']}"
                        )
                        t.start()
                        _threads[acc["id"]] = t
            except Exception as e:
                print(f"[idle-daemon] 刷新账号失败: {e}", flush=True)

    PID_FILE.unlink(missing_ok=True)
    print("[idle-daemon] 已退出", flush=True)


if __name__ == "__main__":
    main()
