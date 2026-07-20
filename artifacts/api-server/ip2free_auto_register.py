#!/usr/bin/env python3
"""
ip2free_auto_register.py v2.1 — bug-fixed
------------------------------------------
Changes vs v2.0:
  - After an inviter account accumulates 3 successful invites this month,
    immediately call finishTask on that account's register_three task
    (instead of waiting for the next 8-hour sync cycle).
  - trigger_sync() logs clearly when the sync lock is held (not a silent failure).
  - Stale lock auto-released on startup.
  - Log directory created gracefully.
"""

import argparse
import json
import os
import re
import random
import sqlite3
import string
import sys
import fcntl
import atexit
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import urllib3
urllib3.disable_warnings()

# ──────────────────────── 配置 ─────────────────────────────────────
BASE_API       = "https://api.ip2free.com"
ENV_FILE       = "/data/Toolkit/.ip2free_proxy.env"
DB_PATH        = "/data/api-server/data.db"
STATE_FILE     = "/data/Toolkit/.ip2free_reg_state.json"
LOG_DIR        = "/var/log"
LOG_FILE       = f"{LOG_DIR}/ip2free_auto_register.log"
LOCK_FILE      = "/tmp/ip2free_auto_register.lock"
SYNC_LOCK_FILE = "/tmp/ip2free_proxy_sync.lock"
LOCAL_API      = os.environ.get("TOOLKIT_API", "http://localhost:8081")

MAX_ACCOUNTS   = 200
MAX_INVITE_PM  = 3       # each account may invite 3 per month
CODE_WAIT_SEC  = 120

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.85 Safari/537.36"


# ──────────────────────── 日志 ──────────────────────────────────────
def log(msg: str) -> None:
    ts   = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ──────────────────────── 月度状态 ──────────────────────────────────
def _load_state() -> Dict[str, Any]:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"[state] 保存失败: {e}")


def _this_month() -> str:
    return time.strftime("%Y%m")


def _get_month_invites(state: Dict[str, Any], email: str) -> int:
    month = _this_month()
    if state.get("month") != month:
        state.clear()
        state["month"] = month
        state["invites"] = {}
        state["used_outlook"] = []
    return state.get("invites", {}).get(email, 0)


def _inc_invite(state: Dict[str, Any], email: str) -> None:
    state.setdefault("month", _this_month())
    state.setdefault("invites", {})
    state["invites"][email] = state["invites"].get(email, 0) + 1


def _mark_outlook_used(state: Dict[str, Any], outlook_email: str) -> None:
    state.setdefault("month", _this_month())
    state.setdefault("used_outlook", [])
    if outlook_email not in state["used_outlook"]:
        state["used_outlook"].append(outlook_email)


def _is_outlook_used(state: Dict[str, Any], outlook_email: str) -> bool:
    return outlook_email in state.get("used_outlook", [])


# ──────────────────────── Env 文件 ──────────────────────────────────
def load_env_accounts(path: str = ENV_FILE) -> List[Dict[str, str]]:
    accounts: List[Dict[str, str]] = []
    if not os.path.exists(path):
        return accounts
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k == "IP2FREE_EMAIL":
                accounts.append({"email": v, "password": ""})
            elif k == "IP2FREE_PASSWORD" and accounts:
                accounts[-1]["password"] = v
    return [a for a in accounts if a["email"] and a["password"]]


def append_account_to_env(email: str, password: str, path: str = ENV_FILE) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\nIP2FREE_EMAIL={email}\nIP2FREE_PASSWORD={password}\n")
    log(f"[env] 追加: {email}")


# ──────────────────────── DB 持久化 ─────────────────────────────────
def save_to_db(email: str, password: str, invite_code: str = "") -> bool:
    try:
        db = sqlite3.connect(DB_PATH)
        db.execute("""
            CREATE TABLE IF NOT EXISTS ai_accounts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                service    TEXT NOT NULL DEFAULT 'ip2free',
                email      TEXT NOT NULL,
                api_key    TEXT,
                status     TEXT NOT NULL DEFAULT 'active',
                notes      TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(service, email)
            )
        """)
        notes = json.dumps({"password": password, "invite_code": invite_code}, ensure_ascii=False)
        db.execute("""
            INSERT INTO ai_accounts (service, email, api_key, status, notes)
            VALUES ('ip2free', ?, ?, 'active', ?)
            ON CONFLICT(service, email) DO UPDATE SET
                api_key=excluded.api_key, status='active',
                notes=excluded.notes, updated_at=datetime('now')
        """, (email, invite_code or "", notes))
        db.commit()
        db.close()
        log(f"[db] 已保存: {email}")
        return True
    except Exception as e:
        log(f"[db] 保存失败: {e}")
        return False


def count_db_accounts() -> int:
    try:
        db = sqlite3.connect(DB_PATH)
        n = db.execute(
            "SELECT COUNT(*) FROM ai_accounts WHERE service='ip2free' AND status='active'"
        ).fetchone()[0]
        db.close()
        return n
    except Exception:
        return 0


# ──────────────────────── ip2free API ────────────────────────────────
def _ip2s(invite_code: str = "") -> requests.Session:
    s = requests.Session(); s.verify = False
    s.headers.update({
        "User-Agent": _UA,
        "Origin": "https://www.ip2free.com",
        "Referer": f"https://www.ip2free.com/zh-CN/register?inviteCode={invite_code}",
        "domain": "www.ip2free.com", "lang": "cn", "webname": "IP2FREE",
        "affid": "", "invitecode": invite_code or "", "serviceid": "",
        "Content-Type": "text/plain;charset=UTF-8",
    })
    return s


def login_get_invite_code(email: str, pw: str) -> Optional[str]:
    s = _ip2s()
    try:
        r = s.post(f"{BASE_API}/api/account/login?",
                   data=json.dumps({"email": email, "password": pw}), timeout=15)
        d = r.json()
        if d.get("code") != 0:
            log(f"  [login] {email} 失败: {d.get('msg','?')}")
            return None
        code = d.get("data", {}).get("profile", {}).get("invite_code", "")
        log(f"  [login] {email} OK, invite_code={code}")
        return code or None
    except Exception as e:
        log(f"  [login] {email} 异常: {e}")
        return None


def send_register_code(session: requests.Session, email: str) -> Tuple[bool, str]:
    try:
        r = session.post(f"{BASE_API}/api/account/getRegisterCode?",
                         data=json.dumps({"email": email}), timeout=15)
        d = r.json()
        ok  = d.get("code") == 0
        msg = d.get("msg", "")
        log(f"  [sendCode] {email}: code={d.get('code')} msg={msg}")
        return ok, msg
    except Exception as e:
        log(f"  [sendCode] {email} 异常: {e}")
        return False, str(e)


def register_account(session: requests.Session, email: str, password: str,
                     code: str) -> bool:
    try:
        r = session.post(f"{BASE_API}/api/account/register?",
                         data=json.dumps({"email": email, "password": password,
                                          "code": code, "ga_client_id": "",
                                          "url_query_raw": ""}),
                         timeout=15)
        d = r.json()
        ok = d.get("code") == 0
        log(f"  [register] {email}: code={d.get('code')} msg={d.get('msg','')}")
        return ok
    except Exception as e:
        log(f"  [register] {email} 异常: {e}")
        return False


def verify_login(email: str, password: str) -> Optional[str]:
    s = _ip2s()
    for attempt in range(4):
        try:
            r = s.post(f"{BASE_API}/api/account/login?",
                       data=json.dumps({"email": email, "password": password}), timeout=15)
            d = r.json()
            if d.get("code") == 0:
                inv = d.get("data", {}).get("profile", {}).get("invite_code", "")
                log(f"  [verify] {email} 登录 OK, invite_code={inv}")
                return inv
            log(f"  [verify] {email} ({attempt+1}/4): {d.get('msg','?')}")
        except Exception as e:
            log(f"  [verify] {email} 异常 ({attempt+1}/4): {e}")
        time.sleep(8)
    return None


# ──────────────────────── finishTask (新增) ──────────────────────────
def try_finish_invite_task(email: str, pw: str) -> bool:
    """
    登录 ip2free，查找未完成的 register_three 任务，尝试调用 finishTask 领取奖励。
    在注册满 3 个邀请人后立即调用，无需等待下次 8 小时 sync。
    返回是否成功领取。
    """
    import urllib3 as _u3; _u3.disable_warnings()
    h = {
        "User-Agent": _UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN",
        "Origin": "https://www.ip2free.com",
        "Referer": "https://www.ip2free.com/",
        "domain": "www.ip2free.com",
        "lang": "cn",
        "webname": "IP2FREE",
        "affid": "",
        "invitecode": "",
        "serviceid": "",
    }
    try:
        s = requests.Session(); s.verify = False; s.headers.update(h)
        r = s.post(
            f"{BASE_API}/api/account/login?",
            data=json.dumps({"email": email, "password": pw}),
            headers={"Content-Type": "text/plain;charset=UTF-8"},
            timeout=15,
        )
        d = r.json()
        if d.get("code") != 0:
            log(f"  [finishTask] login failed for {email}: {d.get('msg','')}")
            return False
        tok = d.get("data", {}).get("token", "")
        s.headers["x-token"] = tok

        r2 = s.post(
            f"{BASE_API}/api/account/taskList?",
            data="{}",
            headers={"Content-Type": "text/plain;charset=UTF-8"},
            timeout=12,
        )
        tasks = r2.json().get("data", {}).get("list", []) or []

        for t in tasks:
            if t.get("task_code") == "register_three" and not t.get("is_finished", 1):
                rec_id = t.get("id")
                if rec_id is None:
                    continue
                r3 = s.post(
                    f"{BASE_API}/api/account/finishTask?",
                    data=json.dumps({"id": rec_id}),
                    headers={"Content-Type": "text/plain;charset=UTF-8"},
                    timeout=12,
                )
                res = r3.json()
                rc  = res.get("code", -1)
                msg = res.get("msg", "")
                log(f"  [finishTask] {email} rec_id={rec_id} code={rc} msg={msg}")
                if rc in (0, 200) or "success" in msg.lower() or "成功" in msg:
                    log(f"  [finishTask] ✓ 活动代理已领取: {email}")
                    return True
                elif "invalid" in msg.lower():
                    log(f"  [finishTask] 任务条件未满足（邀请人数不足？）: {email}")
                    return False
                else:
                    log(f"  [finishTask] 领取失败: {msg}")
                    return False

        log(f"  [finishTask] {email} 无未完成的 register_three 任务（可能已领取）")
        return True  # already finished = success

    except Exception as e:
        log(f"  [finishTask] {email} 异常: {e}")
        return False


# ──────────────────────── Toolkit API ────────────────────────────────
def _toolkit_get(path: str, base: str = "") -> dict:
    url = (base or LOCAL_API).rstrip("/") + path
    try:
        r = urllib.request.urlopen(url, timeout=15)
        return json.loads(r.read())
    except Exception as e:
        log(f"  [toolkit] GET {path} 失败: {e}")
        return {}


def _toolkit_post(path: str, payload: dict, base: str = "") -> dict:
    url = (base or LOCAL_API).rstrip("/") + path
    try:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(url, data=data,
                                      headers={"Content-Type": "application/json"}, method="POST")
        r = urllib.request.urlopen(req, timeout=20)
        return json.loads(r.read())
    except Exception as e:
        log(f"  [toolkit] POST {path} 失败: {e}")
        return {}


def pick_outlook_account(state: Dict[str, Any], existing_ip2free_emails: set,
                        skip: Optional[set] = None) -> Optional[Tuple[int, str, str]]:
    d = _toolkit_get("/api/tools/outlook/accounts?status=active&limit=200")
    accounts = d.get("accounts", [])
    if not accounts:
        log("  [outlook] 没有可用 Outlook 账号")
        return None

    candidates = []
    for acc in accounts:
        em = acc.get("email", "").lower()
        if em in existing_ip2free_emails:
            continue
        if _is_outlook_used(state, em):
            continue
        if skip and em in skip:
            continue
        candidates.append(acc)

    if not candidates:
        log("  [outlook] 所有可用 Outlook 账号已耗尽（或本月已用完）")
        return None

    verified = [a for a in candidates if "inbox_verified" in (a.get("tags") or "")]
    pick = (verified or candidates)[0]
    return pick.get("id", 0), pick.get("email", ""), pick.get("password", "")


def poll_register_code(account_id: int, timeout_s: int = CODE_WAIT_SEC) -> Optional[str]:
    deadline = time.time() + timeout_s
    attempt  = 0
    log(f"  [graph] 等待 ip2free 验证码 (account_id={account_id}, timeout={timeout_s}s)…")
    while time.time() < deadline:
        attempt += 1
        for folder in ["inbox", "junkemail"]:
            d = _toolkit_post("/api/tools/outlook/fetch-messages-by-id",
                              {"accountId": account_id, "folder": folder, "top": 15})
            msgs = d.get("messages", [])
            for msg in msgs:
                subj = msg.get("subject", "")
                prev = msg.get("preview", "")
                body = msg.get("body", "")
                text = f"{subj} {prev} {body}"
                if not ("ip2free" in text.lower() or "验证码" in subj):
                    continue
                noise = ["replit", "microsoft account", "security code", "新应用"]
                if any(n in text.lower() for n in noise):
                    continue
                codes = re.findall(r"\b(\d{6})\b", text)
                if codes:
                    log(f"  [graph] 验证码={codes[0]} folder={folder} attempt={attempt}")
                    return codes[0]
        log(f"  [graph] 等待中... ({attempt})")
        time.sleep(10)
    log("  [graph] 超时未收到验证码")
    return None


# ──────────────────────── 触发 proxy sync ────────────────────────────
def trigger_sync(env_file: str) -> Dict[str, Any]:
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ip2free_proxy_sync.py")

    # Check if sync lock is held — if so, skip to avoid deadlock
    if os.path.exists(SYNC_LOCK_FILE):
        try:
            fd = open(SYNC_LOCK_FILE, "r")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                fd.close()
                # Lock was stale — proceed normally
            except (OSError, IOError):
                fd.close()
                log("[sync] proxy_sync is already running, skip trigger (sync will pick up new accounts on next cycle)")
                return {"skipped": True, "reason": "sync already running"}
        except Exception:
            pass

    try:
        r = subprocess.run(
            [sys.executable, script, "--env-file", env_file],
            capture_output=True, text=True, timeout=600, cwd=os.path.dirname(script),
        )
        # Extract last JSON summary line
        lines = [l for l in r.stdout.strip().splitlines() if l.strip().startswith("{")]
        if lines:
            return json.loads(lines[-1])
        log(f"[sync] no JSON summary, stdout tail: {r.stdout[-300:]}")
        return {"error": (r.stderr or r.stdout)[-500:]}
    except Exception as e:
        log(f"[sync] trigger error: {e}")
        return {"error": str(e)}


# ──────────────────────── 随机密码 ──────────────────────────────────
def _rand_password() -> str:
    uppers = random.choices(string.ascii_uppercase, k=2)
    digits = random.choices(string.digits, k=2)
    lowers = random.choices(string.ascii_lowercase, k=6)
    parts  = uppers + digits + lowers
    random.shuffle(parts)
    return "".join(parts)


# ──────────────────────── 主逻辑 ─────────────────────────────────────
def _release_stale_lock(lock_path: str) -> None:
    if not os.path.exists(lock_path):
        return
    try:
        fd = open(lock_path, "r")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
            os.unlink(lock_path)
            log(f"[lock] removed stale lock: {lock_path}")
        except (OSError, IOError):
            fd.close()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="ip2free 自动邀请注册 v2.1")
    parser.add_argument("--max",       type=int, default=MAX_ACCOUNTS)
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--no-sync",   action="store_true")
    parser.add_argument("--status",    action="store_true")
    parser.add_argument("--env-file",  default=ENV_FILE)
    args = parser.parse_args()

    _release_stale_lock(LOCK_FILE)

    try:
        lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        log("[main] 已有实例正在运行，退出")
        sys.exit(0)

    def _release_lock():
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            lock_fd.close()
        except Exception:
            pass

    atexit.register(_release_lock)

    env_path = args.env_file
    existing = load_env_accounts(env_path)
    total    = len(existing)
    existing_emails = {a["email"].lower() for a in existing}
    tried_outlook: set = set()

    db_count    = count_db_accounts()
    actual_total = max(total, db_count)

    log(f"[main] ip2free 账号: env={total} db={db_count} (上限={args.max})")

    state = _load_state()
    _get_month_invites(state, "__init__")

    if args.status:
        log(f"[status] 本月 ({_this_month()}) 各邀请账号使用情况:")
        for a in existing:
            n = state.get("invites", {}).get(a["email"], 0)
            log(f"  {a['email']}: {n}/{MAX_INVITE_PM}")
        log(f"[status] 已用 Outlook: {len(state.get('used_outlook', []))}")
        log(f"[status] 总 ip2free 账号: {actual_total}/{args.max}")
        return

    if actual_total >= args.max:
        log(f"[main] 已达上限 {actual_total}/{args.max}，退出")
        return

    registered_this_run = 0
    # Track per-inviter how many new invitees added this run (for finishTask trigger)
    inviter_new_this_run: Dict[str, int] = {}

    for acct in existing:
        if actual_total + registered_this_run >= args.max:
            log(f"[main] 达到总上限 {args.max}，停止")
            break

        inv_email = acct["email"]
        inv_pw    = acct["password"]

        used_pm = _get_month_invites(state, inv_email)
        if used_pm >= MAX_INVITE_PM:
            log(f"[skip] {inv_email} 本月已邀请 {used_pm}/{MAX_INVITE_PM}")
            continue

        invite_code = login_get_invite_code(inv_email, inv_pw)
        if not invite_code:
            log(f"[skip] {inv_email} 无法获取 invite_code")
            continue

        slots = MAX_INVITE_PM - used_pm
        log(f"[main] {inv_email} invite_code={invite_code} 剩余 {slots} 个名额")

        for slot in range(slots):
            if actual_total + registered_this_run >= args.max:
                break

            log(f"\n{'='*60}")
            log(f"[register] 邀请方={inv_email}  本月第 {used_pm+slot+1}/{MAX_INVITE_PM} 次")

            pick = pick_outlook_account(state, existing_emails, tried_outlook)
            if not pick:
                log("[register] 没有可用 Outlook 账号，跳出")
                break
            oa_id, oa_email, oa_pw = pick
            log(f"[register] Outlook 账号: {oa_email} (id={oa_id})")

            ip2free_pw = _rand_password()

            if args.dry_run:
                log(f"[dry-run] 跳过注册 {oa_email} → ip2free pw={ip2free_pw}")
                _mark_outlook_used(state, oa_email.lower())
                _inc_invite(state, inv_email)
                _save_state(state)
                tried_outlook.add(oa_email.lower())
                registered_this_run += 1
                inviter_new_this_run[inv_email] = inviter_new_this_run.get(inv_email, 0) + 1
                continue

            # 1. Build session with invite code in headers
            sess = _ip2s(invite_code)

            # 2. Send verification code
            ok, send_msg = send_register_code(sess, oa_email)
            if not ok:
                tried_outlook.add(oa_email.lower())
                if "验证码已发送" in send_msg:
                    log(f"[register] {oa_email} 限频（验证码已发送），跳过")
                    time.sleep(10)
                    continue
                log(f"[register] 发验证码失败，标记已用: {oa_email}")
                _mark_outlook_used(state, oa_email.lower())
                _save_state(state)
                time.sleep(3)
                continue

            # 3. Wait for verification code via Graph API
            code = poll_register_code(oa_id, CODE_WAIT_SEC)
            if not code:
                log(f"[register] 验证码超时，跳过 {oa_email}")
                tried_outlook.add(oa_email.lower())
                _mark_outlook_used(state, oa_email.lower())
                _save_state(state)
                continue

            # 4. Register
            ok2 = register_account(sess, oa_email, ip2free_pw, code)
            if not ok2:
                log(f"[register] 注册失败 {oa_email}")
                tried_outlook.add(oa_email.lower())
                _mark_outlook_used(state, oa_email.lower())
                _save_state(state)
                time.sleep(5)
                continue

            # 5. Verify login
            time.sleep(5)
            new_invite = verify_login(oa_email, ip2free_pw)
            if new_invite is None:
                log(f"[register] 注册后无法登录 {oa_email}，放弃")
                tried_outlook.add(oa_email.lower())
                _mark_outlook_used(state, oa_email.lower())
                _save_state(state)
                continue

            # 6. Save
            append_account_to_env(oa_email, ip2free_pw, env_path)
            save_to_db(oa_email, ip2free_pw, new_invite)
            _mark_outlook_used(state, oa_email.lower())
            _inc_invite(state, inv_email)
            _save_state(state)
            existing_emails.add(oa_email.lower())
            tried_outlook.add(oa_email.lower())
            registered_this_run += 1
            inviter_new_this_run[inv_email] = inviter_new_this_run.get(inv_email, 0) + 1

            log(f"[register] ✓ 注册完成: {oa_email}  新invite={new_invite}  本轮共注册={registered_this_run}")

            # 7. If inviter now has 3 total invites this month, immediately try finishTask
            total_invites_for_inviter = _get_month_invites(state, inv_email)
            if total_invites_for_inviter >= MAX_INVITE_PM:
                log(f"[finishTask] {inv_email} 已满 {total_invites_for_inviter} 个邀请，立即尝试领取活动代理…")
                finish_ok = try_finish_invite_task(inv_email, inv_pw)
                log(f"[finishTask] {inv_email} → {'✓ 成功' if finish_ok else '✗ 失败（可能已领取或任务条件未满足）'}")

            time.sleep(15)

    # ── 结果汇总 ────────────────────────────────────────────────────
    log(f"\n{'='*60}")
    log(f"[done] 本次新增 {registered_this_run} 个账号，总计 {actual_total + registered_this_run}/{args.max}")

    # ── 触发 proxy sync ─────────────────────────────────────────────
    if registered_this_run > 0 and not args.no_sync and not args.dry_run:
        log("[sync] 触发 proxy sync 拉取最新代理…")
        result = trigger_sync(env_path)
        if result.get("skipped"):
            log(f"[sync] 跳过（{result.get('reason','')}）")
        else:
            log(f"[sync] free_added={result.get('free_added',0)} "
                f"event_added={result.get('event_added',0)} "
                f"claimed={result.get('claimed',0)}")

    summary = {
        "ts":         time.time(),
        "registered": registered_this_run,
        "total":      actual_total + registered_this_run,
        "max":        args.max,
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
