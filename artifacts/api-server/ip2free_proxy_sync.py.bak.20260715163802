#!/usr/bin/env python3
"""
ip2free_proxy_sync.py v4.0
-------------------------
Toolkit-integrated sync daemon for ip2free.com proxy pools.

Responsibilities:
  1. Read ip2free credentials from a local env file or CLI args.
  2. For every account, login to api.ip2free.com.
  3. Fetch free SOCKS5 proxies from /api/ip/freeList and add them to the
     VPS resi_pool (free proxy pool).
  4. Claim the monthly invite-3-people event reward (task_code=register_three)
     by registering 3 new ip2free accounts under the account's invite code,
     then finish the task and fetch activity proxies from /api/ip/taskIpList.
  5. Persist activity proxies separately (JSON) with metadata and expiration.
  6. Run once or loop every 8 hours.

Run once:
    python3 ip2free_proxy_sync.py --probe

Run as 8-hour cron:
    python3 ip2free_proxy_sync.py --probe --loop
"""

import argparse
import json
import os
import subprocess
import sys
import fcntl
import atexit
import time
import traceback
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")

# ── resi_pool integration (VPS paths) ───────────────────────────────────────
RESI_POOL_PATHS = ["/data/Toolkit/scripts", "/root/Toolkit/scripts"]
for _rp in RESI_POOL_PATHS:
    if _rp not in sys.path and os.path.isdir(_rp):
        sys.path.insert(0, _rp)

try:
    import resi_pool
except Exception as _exc:
    print(f"[warn] resi_pool import failed: {_exc}", file=sys.stderr)
    resi_pool = None  # type: ignore

import requests
import urllib3

urllib3.disable_warnings()

BASE_API = "https://api.ip2free.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.85 Safari/537.36"
)
COMMON_H = {
    "User-Agent": UA,
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

# Files written by the daemon
FREE_PROXY_FILE = "/tmp/ip2free_free_proxies.json"
EVENT_PROXY_FILE = "/tmp/ip2free_event_proxies.json"
SYNC_STATE_FILE = "/tmp/ip2free_sync_state.json"
INVITE_STATE_FILE = "/tmp/ip2free_invite_state.json"
LOCK_FILE = "/tmp/ip2free_proxy_sync.lock"
DEFAULT_ENV_FILE = "/data/Toolkit/.ip2free_proxy.env"

# Monthly event that gives 30-day unlimited residential proxies
AUTO_CLAIMABLE_CODES = {"register_three"}
INVITE_NEEDED = {"register_three": 3}


def _api_session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.headers.update(COMMON_H)
    return s


def login(email: str, pw: str) -> Tuple[requests.Session, str, str]:
    s = _api_session()
    r = s.post(
        f"{BASE_API}/api/account/login?",
        data=json.dumps({"email": email, "password": pw}),
        headers={"Content-Type": "text/plain;charset=UTF-8"},
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"login failed: {payload}")
    tok = payload.get("data", {}).get("token")
    if not tok:
        raise RuntimeError("login success but no token")
    inv = (payload.get("data", {}).get("profile") or {}).get("invite_code", "")
    s.headers["x-token"] = tok
    return s, tok, inv


def _build_pm_proxy_dict(p: Dict[str, Any], email: str) -> Dict[str, Any]:
    """Normalize a free or event proxy record into proxy_manager.load_from_json_file format."""
    ip = p.get("ip", "").strip()
    port = int(p.get("port", 0) or 0)
    if not ip or not port:
        return {}
    uid = (p.get("proxy_uid") or p.get("id") or f"{ip}:{port}")
    expire = (p.get("expire_ts")
              or _parse_expire_ts(p.get("expired_at") or p.get("expire_time") or p.get("expires_at"))
              or None)
    return {
        "proxy_uid": uid,
        "id": uid,
        "ip": ip,
        "port": port,
        "username": (p.get("username") or "").strip(),
        "password": (p.get("password") or "").strip(),
        "protocol": (p.get("protocol") or "socks5").lower(),
        "country_code": (p.get("country_code") or p.get("country") or "").strip(),
        "city": (p.get("city") or "").strip(),
        "source_account": email,
        "expire_time": expire,
        "expires_at": expire,
    }


def write_proxy_manager_json(
    results: List[Dict[str, Any]],
    path: str = "/tmp/ip2free_proxies_all.json",
) -> int:
    """Write all collected ip2free proxies into proxy_manager's auto-load format."""
    items: List[Dict[str, Any]] = []
    for r in results:
        email = r.get("email", "")
        for p in r.get("free_proxies", []):
            d = _build_pm_proxy_dict(p, email)
            if d:
                items.append(d)
        for p in r.get("event_proxies", []):
            d = _build_pm_proxy_dict(p, email)
            if d:
                items.append(d)
    if not items:
        return 0
    try:
        _save_json(path, {"proxies": items, "ts": time.time()})
        return len(items)
    except Exception as e:
        print(f"[warn] failed to write proxy_manager JSON: {e}", file=sys.stderr)
        return 0


def load_and_inject_proxy_manager(path: str = "/tmp/ip2free_proxies_all.json") -> None:
    """Load collected ip2free proxies into proxy_manager and inject into resi_pool."""
    if not Path(path).exists():
        return
    pm_script = Path("/data/Toolkit/scripts/proxy_manager.py")
    if not pm_script.exists():
        print(f"[warn] proxy_manager.py not found, skipping DB injection", file=sys.stderr)
        return
    try:
        r = subprocess.run(
            [sys.executable, str(pm_script), "load-file", path, "--source", "ip2free"],
            capture_output=True, text=True, cwd=str(pm_script.parent), timeout=120
        )
        if r.returncode != 0:
            print(f"[warn] proxy_manager load-file failed: {r.stderr[-500:]}", file=sys.stderr)
        else:
            print(f"[proxy_manager] {r.stdout.strip().splitlines()[-1]}", flush=True)
        r2 = subprocess.run(
            [sys.executable, str(pm_script), "inject-resi-pool"],
            capture_output=True, text=True, cwd=str(pm_script.parent), timeout=180
        )
        if r2.returncode != 0:
            print(f"[warn] proxy_manager inject-resi-pool failed: {r2.stderr[-500:]}", file=sys.stderr)
        else:
            for line in r2.stdout.strip().splitlines():
                if line.strip():
                    print(f"[proxy_manager] {line}", flush=True)
    except Exception as e:
        print(f"[warn] proxy_manager injection error: {e}", file=sys.stderr)


def fetch_free_proxies(s: requests.Session, size: int = 200) -> List[Dict[str, Any]]:
    """Return raw freeList proxy records (with ip/port/user/pass/status)."""
    r = s.post(
        f"{BASE_API}/api/ip/freeList?",
        data=json.dumps({"size": size}),
        headers={"Content-Type": "text/plain;charset=UTF-8"},
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"freeList failed: {payload}")
    data = payload.get("data", {})
    proxies = data.get("free_ip_list", []) if isinstance(data, dict) else []
    return [p for p in proxies if p.get("status") == 1 and p.get("ip") and p.get("port")]


def fmt_proxy_url(p: Dict[str, Any]) -> str:
    user = (p.get("username") or "").strip()
    pw = (p.get("password") or "").strip()
    host = p.get("ip", "").strip()
    port = int(p.get("port", 0))
    if user and pw:
        return f"{user}:{pw}@{host}:{port}"
    return f"{host}:{port}"


def fetch_task_list(s: requests.Session) -> List[Dict[str, Any]]:
    r = s.post(
        f"{BASE_API}/api/account/taskList?",
        data="{}",
        headers={"Content-Type": "text/plain;charset=UTF-8"},
        timeout=12,
    )
    r.raise_for_status()
    return r.json().get("data", {}).get("list", []) or []


def finish_task(s: requests.Session, record_id: int) -> Dict[str, Any]:
    r = s.post(
        f"{BASE_API}/api/account/finishTask?",
        data=json.dumps({"id": record_id}),
        headers={"Content-Type": "text/plain;charset=UTF-8"},
        timeout=12,
    )
    r.raise_for_status()
    return r.json()


def fetch_event_proxies(s: requests.Session, size: int = 100) -> List[Dict[str, Any]]:
    r = s.post(
        f"{BASE_API}/api/ip/taskIpList?",
        data=json.dumps({"size": size}),
        headers={"Content-Type": "text/plain;charset=UTF-8"},
        timeout=12,
    )
    r.raise_for_status()
    return (r.json().get("data", {}).get("page") or {}).get("list", []) or []


def _parse_expire_ts(exp_str: Optional[str]) -> Optional[float]:
    if not exp_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            dt = datetime.strptime(exp_str, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            continue
    return None


def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[warn] failed to write {path}: {e}", file=sys.stderr)


def _load_invite_state() -> Dict[str, Any]:
    return _load_json(INVITE_STATE_FILE, {})


def _save_invite_state(state: Dict[str, Any]) -> None:
    _save_json(INVITE_STATE_FILE, state)


def _invitees_still_needed(email: str, task_code: str, month: str) -> int:
    """How many invitees still need to be registered this month for the given task."""
    state = _load_invite_state()
    record = state.get(email, {})
    if record.get("month") == month and record.get("code") == task_code:
        registered = record.get("registered", 0)
        return max(0, INVITE_NEEDED.get(task_code, 0) - registered)
    return INVITE_NEEDED.get(task_code, 0)


def _record_invitees(email: str, task_code: str, month: str, registered: int) -> None:
    state = _load_invite_state()
    state[email] = {
        "month": month,
        "code": task_code,
        "registered": registered,
        "ts": time.time(),
    }
    _save_invite_state(state)


def register_invitees(
    invite_code: str,
    needed: int,
    env_file: str = DEFAULT_ENV_FILE,
) -> Tuple[int, List[str]]:
    """
    Register `needed` new ip2free accounts using the given invite code.
    Calls the local ip2free_register.py script for each invitee.
    Returns (success_count, messages).
    """
    script_dir = Path(__file__).resolve().parent
    reg_script = script_dir / "ip2free_register.py"
    if not reg_script.exists():
        return 0, [f"register script not found: {reg_script}"]

    registered = 0
    msgs = []
    for i in range(needed):
        args = [
            sys.executable,
            str(reg_script),
            "--invite-code", invite_code,
            "--env-file", env_file,
            "--no-sync",
            "--headless", "true",
        ]
        try:
            r = subprocess.run(
                args,
                capture_output=True,
                text=True,
                cwd=str(script_dir),
                timeout=300,
            )
            if r.returncode == 0:
                registered += 1
                msgs.append(f"registered invitee #{i+1} for invite_code={invite_code}")
            else:
                err_tail = r.stderr[-300:] if r.stderr else ""
                out_tail = r.stdout[-300:] if r.stdout else ""
                msgs.append(f"registration #{i+1} failed: rc={r.returncode} stderr={err_tail} stdout={out_tail}")
        except Exception as e:
            msgs.append(f"registration #{i+1} exception: {e}")
    return registered, msgs


def claim_event_tasks(
    s: requests.Session,
    email: str,
    env_file: str = DEFAULT_ENV_FILE,
) -> Tuple[int, List[str]]:
    """
    Claim the monthly invite-3-people event task for the session.
    If the task is not yet finished, register the required number of invitees
    under the account's invite code, then finish the task.
    Returns (claimed_count, list of messages).
    """
    tasks = fetch_task_list(s)
    print(f"  [event] taskList: {len(tasks)} records", flush=True)
    for t in tasks:
        if t.get("task_code", "") in AUTO_CLAIMABLE_CODES:
            print(
                f"    task_id={t.get('task_id')} code={t.get('task_code')} "
                f"rec_id={t.get('id')} finished={t.get('is_finished')} month={t.get('month')}",
                flush=True,
            )

    claimed = 0
    msgs: List[str] = []
    for t in tasks:
        code = t.get("task_code", "")
        if code not in AUTO_CLAIMABLE_CODES:
            continue
        if t.get("is_finished", 1):
            continue
        rec_id = t.get("id")
        if rec_id is None:
            continue

        invite_code = t.get("user_invite_code") or t.get("invite_code") or ""
        month = t.get("month", "")
        needed = INVITE_NEEDED.get(code, 0)

        if needed > 0 and invite_code:
            still_needed = _invitees_still_needed(email, code, month)
            if still_needed > 0:
                registered, reg_msgs = register_invitees(invite_code, still_needed, env_file)
                msgs.extend(reg_msgs)
                _record_invitees(email, code, month, needed - still_needed + registered)
                if registered < still_needed:
                    msgs.append(f"insufficient invitees for {code}: {registered}/{still_needed}")
                    continue
            else:
                msgs.append(f"invitees already registered this month for {code}")

        res = finish_task(s, rec_id)
        msg = res.get("msg", "")
        rc = res.get("code", -1)
        print(f"  [event] finishTask rec_id={rec_id} response: code={rc} msg={msg}", flush=True)
        if rc in (0, 200) or "success" in msg.lower() or "成功" in msg:
            claimed += 1
            msgs.append(f"claimed task_id={t.get('task_id')} rec_id={rec_id}")
        else:
            msgs.append(f"claim_failed task_id={t.get('task_id')} rec_id={rec_id} msg={msg}")
    return claimed, msgs


def sync_account(
    email: str,
    pw: str,
    probe: bool = False,
    env_file: str = DEFAULT_ENV_FILE,
) -> Dict[str, Any]:
    """
    Sync one ip2free account.
    Returns result dict with free_proxies, event_proxies, claim status, etc.
    """
    result: Dict[str, Any] = {
        "email": email,
        "success": False,
        "free_total": 0,
        "free_added": 0,
        "event_total": 0,
        "event_added": 0,
        "claimed": 0,
        "error": None,
    }

    try:
        s, _tok, _inv = login(email, pw)

        # ── Free proxies ───────────────────────────────────────────────────────
        free_raw = fetch_free_proxies(s)
        result["free_total"] = len(free_raw)
        result["free_proxies"] = free_raw
        free_added = 0
        if resi_pool:
            for _fp in free_raw:
                _fp_url = fmt_proxy_url(_fp)
                if _fp_url:
                    try:
                        if resi_pool.add_external_full(_fp_url, probe=False):
                            free_added += 1
                    except Exception:
                        pass
        result["free_added"] = free_added

        # Save free proxies to dedicated file (separate from event pool)
        try:
            existing_free = _load_json(FREE_PROXY_FILE, {}).get("proxies", [])
            free_urls = [fmt_proxy_url(p) for p in free_raw]
            merged_free = existing_free + [u for u in free_urls if u not in existing_free]
            _save_json(FREE_PROXY_FILE, {"proxies": merged_free, "ts": time.time()})
        except Exception as e:
            print(f"  [warn] failed to save free proxy file: {e}", flush=True)

        # ── Event task (invite 3 people) + activity proxies ────────────────────
        claimed, claim_msgs = claim_event_tasks(s, email, env_file=env_file)
        result["claimed"] = claimed
        result["claim_messages"] = claim_msgs

        event_raw = fetch_event_proxies(s)
        result["event_total"] = len(event_raw)
        event_proxies: List[Dict[str, Any]] = []
        for p in event_raw:
            entry = {
                "proxy_uid": p.get("id") or p.get("proxy_uid") or f"{p.get('ip')}:{p.get('port')}",
                "ip": p.get("ip", ""),
                "port": int(p.get("port", 0) or 0),
                "username": p.get("username", ""),
                "password": p.get("password", ""),
                "protocol": (p.get("protocol") or "socks5").lower(),
                "country_code": p.get("country_code", ""),
                "city": p.get("city", ""),
                "remark": p.get("remark", ""),
                "bind_status": p.get("bind_status", 0),
                "assigned_at": p.get("assigned_at", ""),
                "expired_at": p.get("expired_at") or p.get("expire_time") or None,
                "expire_ts": _parse_expire_ts(p.get("expired_at") or p.get("expire_time")),
                "source_account": email,
                "source": "ip2free_event",
            }
            entry["url"] = fmt_proxy_url(entry)
            event_proxies.append(entry)

        result["event_proxies"] = event_proxies

        # Merge into persistent event pool file (deduplicate by proxy_uid)
        existing_event = _load_json(EVENT_PROXY_FILE, {}).get("proxies", [])
        existing_uids = {p.get("proxy_uid") for p in existing_event}
        new_event = [p for p in event_proxies if p.get("proxy_uid") not in existing_uids]
        merged_event = existing_event + new_event
        result["event_added"] = len(new_event)
        _save_json(EVENT_PROXY_FILE, {"proxies": merged_event, "ts": time.time()})

        print(
            f"  [account] free_total={result['free_total']} free_added={result['free_added']} "
            f"event_total={result['event_total']} event_added={result['event_added']} claimed={result['claimed']}",
            flush=True,
        )
        result["success"] = True

    except Exception as e:
        result["error"] = str(e)
        traceback.print_exc()

    return result


def load_credentials_env_file(path: str) -> List[Dict[str, str]]:
    """Load email=password lines from env file. Format: IP2FREE_EMAIL=... IP2FREE_PASSWORD=..."""
    creds: List[Dict[str, str]] = []
    if not os.path.exists(path):
        return creds
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k == "IP2FREE_EMAIL":
                creds.append({"email": v, "password": ""})
            elif k == "IP2FREE_PASSWORD" and creds:
                creds[-1]["password"] = v
    return [c for c in creds if c["email"] and c["password"]]


def run_sync(
    accounts: List[Dict[str, str]],
    probe: bool = False,
    env_file: str = DEFAULT_ENV_FILE,
) -> Dict[str, Any]:
    """Run sync for all supplied accounts and return summary."""
    summary = {
        "ts": time.time(),
        "accounts_total": len(accounts),
        "results": [],
        "free_total": 0,
        "free_added": 0,
        "event_total": 0,
        "event_added": 0,
        "claimed": 0,
        "errors": 0,
    }

    if resi_pool:
        loaded = resi_pool.reload_externals()
        if loaded:
            print(f"[run_sync] loaded {loaded} existing external proxies into pool", flush=True)

    for i, acct in enumerate(accounts, 1):
        email = acct.get("email", "").strip()
        pw = acct.get("password", "").strip()
        if not email or not pw:
            continue
        print(f"[{i}/{len(accounts)}] sync {email} ...", flush=True)
        r = sync_account(email, pw, probe=probe, env_file=env_file)
        summary["results"].append(r)
        summary["free_total"] += r.get("free_total", 0)
        summary["free_added"] += r.get("free_added", 0)
        summary["event_total"] += r.get("event_total", 0)
        summary["event_added"] += r.get("event_added", 0)
        summary["claimed"] += r.get("claimed", 0)
        if r.get("error"):
            summary["errors"] += 1

    pm_count = write_proxy_manager_json(summary["results"])
    if pm_count:
        print(f"[sync] wrote {pm_count} proxies for proxy_manager", flush=True)
        load_and_inject_proxy_manager()

    _save_json(SYNC_STATE_FILE, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="ip2free -> VPS proxy pool sync v4")
    parser.add_argument("--email", default=os.environ.get("IP2FREE_EMAIL"), help="Single account email")
    parser.add_argument("--password", default=os.environ.get("IP2FREE_PASSWORD"), help="Single account password")
    parser.add_argument("--accounts-json", help="JSON file with [{email,password},...]")
    parser.add_argument("--probe", action="store_true", help="Probe proxy before adding to resi_pool")
    parser.add_argument("--loop", action="store_true", help="Loop forever")
    parser.add_argument("--interval", type=int, default=8 * 3600, help="Loop interval seconds")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="dotenv file with IP2FREE_EMAIL and IP2FREE_PASSWORD")
    args = parser.parse_args()

    accounts: List[Dict[str, str]] = []

    if args.accounts_json:
        accounts = _load_json(args.accounts_json, [])
    elif args.email and args.password:
        accounts = [{"email": args.email, "password": args.password}]
    else:
        accounts = load_credentials_env_file(args.env_file)

    if not accounts:
        parser.error("No ip2free credentials found. Provide --email/--password, --accounts-json, or an env file.")

    # single-instance lock
    try:
        lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        print("[main] another instance is running, exit", file=sys.stderr)
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

    while True:
        try:
            run_sync(accounts, probe=args.probe, env_file=args.env_file)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            traceback.print_exc()
        if not args.loop:
            break
        print(f"sleep {args.interval}s ...", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
