#!/usr/bin/env python3
"""
Graph API 邮件轮询守护进程 — 替代 IMAP IDLE，用 Microsoft Graph 读新邮件。

原因: 现有 refresh_token 含 Mail.Read (Graph) scope，不含 IMAP.AccessAsUser.All，
      IMAP XOAUTH2 必然失败。Graph API 对所有现有账号均可用。

用法:
  DATABASE_URL=postgresql://... python3 imap_idle_daemon.py
"""
import json, os, signal, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote

EVENTS_FILE     = Path("/tmp/imap_idle_events.json")
STATUS_FILE     = Path("/tmp/imap_idle_status.json")
PID_FILE        = Path("/tmp/imap_idle_daemon.pid")

POLL_INTERVAL   = 30           # seconds between Graph API checks per account
MAX_ACCOUNTS    = 60
REFRESH_INTERVAL = 300         # reload DB account list every 5 min

CLIENT_ID  = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
GRAPH_SCOPE = (
    "https://graph.microsoft.com/Mail.Read "
    "https://graph.microsoft.com/User.Read "
    "offline_access"
)
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_stop_event = threading.Event()
_ev_lock    = threading.Lock()
_st_lock    = threading.Lock()
_threads: dict[int, threading.Thread] = {}

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")


# ── persistence ───────────────────────────────────────────────────────────────

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
        # deduplicate by msg_id — safe across restarts
        seen_ids = {e.get("msg_id") for e in evts if e.get("msg_id")}
        if evt.get("msg_id") and evt["msg_id"] in seen_ids:
            return
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


# ── DB ────────────────────────────────────────────────────────────────────────

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


# ── token refresh ─────────────────────────────────────────────────────────────

def _refresh_graph_token(refresh_tok: str) -> tuple[str, str]:
    """Exchange refresh_token for Graph API access_token."""
    body = urlencode({
        "grant_type":    "refresh_token",
        "client_id":     CLIENT_ID,
        "refresh_token": refresh_tok,
        "scope":         GRAPH_SCOPE,
    }).encode()
    req = Request(TOKEN_URL, data=body,
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        resp = json.loads(urlopen(req, timeout=20).read())
    except HTTPError as e:
        raise RuntimeError(f"token refresh HTTP {e.code}: {e.read()[:120]}")
    at = resp.get("access_token", "")
    rt = resp.get("refresh_token", refresh_tok)
    if not at:
        raise RuntimeError(f"{resp.get('error','?')}: {resp.get('error_description','')[:80]}")
    return at, rt


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
        print(f"[poll] ⚠ DB token update failed id={acct_id}: {e}", flush=True)


# ── Graph API calls ───────────────────────────────────────────────────────────

def _graph_get(path: str, token: str) -> dict:
    url = GRAPH_BASE + path
    req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        return json.loads(urlopen(req, timeout=20).read())
    except HTTPError as e:
        body = e.read()
        raise RuntimeError(f"Graph {e.code}: {body[:120]}")


def _fetch_new_messages(token: str, since_dt: str | None, top: int = 10) -> list[dict]:
    """
    Fetch inbox messages newer than since_dt (ISO8601 string).
    Returns list sorted oldest-first.
    """
    params = {
        "$top": str(top),
        "$select": "id,subject,from,receivedDateTime,isRead",
        "$orderby": "receivedDateTime desc",
    }
    if since_dt:
        params["$filter"] = f"receivedDateTime gt {since_dt}"

    qs = "&".join(f"{k}={quote(str(v), safe='$@')}" for k, v in params.items())
    data = _graph_get(f"/me/mailFolders/inbox/messages?{qs}", token)
    msgs = data.get("value", [])
    return list(reversed(msgs))   # oldest first


# ── poll worker ───────────────────────────────────────────────────────────────

def _poll_worker(acct: dict):
    acct_id = acct["id"]
    email   = acct["email"]
    token   = acct["token"] or ""
    rt      = acct["refresh_token"] or ""

    _set_status(acct_id, email, "starting")

    # ── Initial token refresh ─────────────────────────────────────────────────
    if rt:
        try:
            new_at, new_rt = _refresh_graph_token(rt)
            if new_at != token or new_rt != rt:
                _update_db_token(acct_id, new_at, new_rt)
            token = new_at
            rt    = new_rt
            print(f"[poll] 🔄 {email}: token 已刷新", flush=True)
        except RuntimeError as e:
            err = str(e)
            if not token:
                print(f"[poll] {email}: refresh 失败且无 token: {err}", flush=True)
                _set_status(acct_id, email, "needs_oauth", err)
                return
            print(f"[poll] ⚠ {email}: refresh 失败，尝试旧 token: {err}", flush=True)
    elif not token:
        _set_status(acct_id, email, "needs_oauth", "no token and no refresh_token")
        return

    # ── Verify token works + get baseline ────────────────────────────────────
    consecutive_errors = 0
    last_received_dt: str | None = None   # ISO8601 of most recent message we've seen

    try:
        msgs = _fetch_new_messages(token, since_dt=None, top=1)
        if msgs:
            last_received_dt = msgs[-1]["receivedDateTime"]
        _set_status(acct_id, email, "polling")
        print(f"[poll] ✅ {email}: Graph OK, last_dt={last_received_dt or 'none'}", flush=True)
    except RuntimeError as e:
        err = str(e)
        print(f"[poll] ❌ {email}: initial Graph call failed: {err}", flush=True)
        _set_status(acct_id, email, "error", err)
        # fall through — will retry in loop

    # ── Poll loop ─────────────────────────────────────────────────────────────
    while not _stop_event.is_set():
        # sleep in 1-second increments so we can react to stop_event quickly
        for _ in range(POLL_INTERVAL):
            if _stop_event.is_set():
                return
            time.sleep(1)

        try:
            new_msgs = _fetch_new_messages(token, since_dt=last_received_dt, top=10)

            if new_msgs:
                for m in new_msgs:
                    subj    = m.get("subject", "")
                    from_   = m.get("from", {}).get("emailAddress", {})
                    sender  = from_.get("address", "")
                    name    = from_.get("name", "")
                    recv_dt = m.get("receivedDateTime", "")
                    msg_id  = m.get("id", "")
                    is_read = m.get("isRead", True)

                    evt = {
                        "account_id": acct_id,
                        "email":      email,
                        "msg_id":     msg_id,
                        "subject":    subj,
                        "from":       f"{name} <{sender}>" if name else sender,
                        "date":       recv_dt,
                        "is_unread":  not is_read,
                        "ts":         datetime.utcnow().isoformat(),
                    }
                    _append_event(evt)
                    print(f"[poll] 📬 {email}: {subj[:60]}", flush=True)

                    if recv_dt and recv_dt > (last_received_dt or ""):
                        last_received_dt = recv_dt

            consecutive_errors = 0
            _set_status(acct_id, email, "polling")

        except RuntimeError as e:
            err = str(e)
            consecutive_errors += 1
            print(f"[poll] ⚠ {email}: Graph error ({consecutive_errors}): {err}", flush=True)
            _set_status(acct_id, email, "error", err)

            if "401" in err or "InvalidAuthenticationToken" in err or "token" in err.lower():
                # Token expired — try to refresh
                if rt:
                    try:
                        new_at, new_rt = _refresh_graph_token(rt)
                        _update_db_token(acct_id, new_at, new_rt)
                        token = new_at
                        rt    = new_rt
                        print(f"[poll] 🔄 {email}: token 已重新刷新", flush=True)
                        consecutive_errors = 0
                    except RuntimeError as re2:
                        print(f"[poll] ❌ {email}: token 刷新失败: {re2}", flush=True)

            if consecutive_errors >= 10:
                print(f"[poll] 停止监听 {email} (10次连续错误)", flush=True)
                _set_status(acct_id, email, "disabled", err)
                return


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    PID_FILE.write_text(str(os.getpid()))
    print(f"[poll-daemon] PID={os.getpid()} 启动...", flush=True)

    def _sig(sig, frame):
        print(f"[poll-daemon] 收到信号 {sig}，停止...", flush=True)
        _stop_event.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)

    try:
        accounts = _get_accounts()
    except Exception as e:
        print(f"[poll-daemon] 读取账号失败: {e}", flush=True)
        sys.exit(1)

    print(f"[poll-daemon] 加载 {len(accounts)} 个账号", flush=True)
    for acc in accounts:
        t = threading.Thread(
            target=_poll_worker, args=(acc,),
            daemon=True, name=f"poll-{acc['email']}"
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
                        print(f"[poll-daemon] 新账号 {acc['email']} 加入监听", flush=True)
                        t = threading.Thread(
                            target=_poll_worker, args=(acc,),
                            daemon=True, name=f"poll-{acc['email']}"
                        )
                        t.start()
                        _threads[acc["id"]] = t
            except Exception as e:
                print(f"[poll-daemon] 刷新账号失败: {e}", flush=True)

    PID_FILE.unlink(missing_ok=True)
    print("[poll-daemon] 已退出", flush=True)


if __name__ == "__main__":
    main()
