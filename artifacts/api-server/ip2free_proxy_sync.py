#!/usr/bin/env python3
"""
ip2free_proxy_sync.py v4.1 — bug-fixed
---------------------------------------
Fixes vs v4.0:
  - invite_code now comes from login (profile), not task record (task records have no invite_code field)
  - register_invitees uses the correct account invite_code
  - persistent proxy files moved to /data/Toolkit/proxy_data/ (survive reboots)
  - free proxy dedup always stores strings, clears legacy dict entries
  - stale lock detection: if lock file exists but no process holds it, remove it
  - client_click daily task added to claimable set (API-only, no browser needed)
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

# ── Persistent file paths (survive reboots) ──────────────────────────────────
PROXY_DATA_DIR = "/data/Toolkit/proxy_data"
os.makedirs(PROXY_DATA_DIR, exist_ok=True)

FREE_PROXY_FILE   = f"{PROXY_DATA_DIR}/ip2free_free_proxies.json"
EVENT_PROXY_FILE  = f"{PROXY_DATA_DIR}/ip2free_event_proxies.json"
SYNC_STATE_FILE   = f"{PROXY_DATA_DIR}/ip2free_sync_state.json"
INVITE_STATE_FILE = f"{PROXY_DATA_DIR}/ip2free_invite_state.json"

# tmp symlinks for backward-compat with API routes that read /tmp paths
for _src, _dst in [
    (FREE_PROXY_FILE,   "/tmp/ip2free_free_proxies.json"),
    (EVENT_PROXY_FILE,  "/tmp/ip2free_event_proxies.json"),
    (SYNC_STATE_FILE,   "/tmp/ip2free_sync_state.json"),
    (INVITE_STATE_FILE, "/tmp/ip2free_invite_state.json"),
]:
    try:
        # Remove both stale symlinks AND regular files (regular files exist before first reboot)
        if os.path.islink(_dst) or os.path.isfile(_dst):
            os.unlink(_dst)
        os.symlink(_src, _dst)
    except Exception:
        pass

LOCK_FILE        = "/tmp/ip2free_proxy_sync.lock"
DEFAULT_ENV_FILE = "/data/Toolkit/.ip2free_proxy.env"

# Monthly task: invite 3 people → 30-day unlimited residential proxies
# Daily task:   client_click    → visit partner links (API-only claim)
AUTO_CLAIMABLE_CODES = {"register_three"}
DAILY_CLAIMABLE_CODES = {"client_click"}
INVITE_NEEDED = {"register_three": 3}


# ── API helpers ──────────────────────────────────────────────────────────────

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


def fetch_free_proxies(s: requests.Session, size: int = 200) -> List[Dict[str, Any]]:
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
    pw   = (p.get("password") or "").strip()
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


def click_all_links(s: requests.Session) -> int:
    """GET /api/website/link → click each link ID via API (satisfies client_click condition)."""
    try:
        r = s.get(f"{BASE_API}/api/website/link?", timeout=10)
        r.raise_for_status()
    except Exception:
        return 0

    link_ids: List[int] = []

    def _recurse(obj: Any) -> None:
        if isinstance(obj, dict):
            if "id" in obj and "link" in obj:
                try:
                    link_ids.append(int(obj["id"]))
                except Exception:
                    pass
            for v in obj.values():
                _recurse(v)
        elif isinstance(obj, list):
            for item in obj:
                _recurse(item)

    _recurse(r.json())
    for lid in link_ids:
        try:
            s.get(f"{BASE_API}/api/website/linkClick?id={lid}", timeout=8, allow_redirects=True)
        except Exception:
            pass
        time.sleep(0.2)
    return len(link_ids)


def fetch_event_proxies(s: requests.Session, size: int = 100) -> List[Dict[str, Any]]:
    r = s.post(
        f"{BASE_API}/api/ip/taskIpList?",
        data=json.dumps({"size": size}),
        headers={"Content-Type": "text/plain;charset=UTF-8"},
        timeout=12,
    )
    r.raise_for_status()
    return (r.json().get("data", {}).get("page") or {}).get("list", []) or []


# ── Invite state helpers ──────────────────────────────────────────────────────

def _load_invite_state() -> Dict[str, Any]:
    return _load_json(INVITE_STATE_FILE, {})


def _save_invite_state(state: Dict[str, Any]) -> None:
    _save_json(INVITE_STATE_FILE, state)


def _invitees_registered_this_month(email: str, task_code: str, month: str) -> int:
    state = _load_invite_state()
    record = state.get(email, {})
    if record.get("month") == month and record.get("code") == task_code:
        return record.get("registered", 0)
    return 0


def _record_invitees(email: str, task_code: str, month: str, registered: int) -> None:
    state = _load_invite_state()
    state[email] = {
        "month": month,
        "code": task_code,
        "registered": registered,
        "ts": time.time(),
    }
    _save_invite_state(state)


# ── Register invitees (lightweight API, not browser) ──────────────────────────

def register_invitee_api(invite_code: str, env_file: str = DEFAULT_ENV_FILE) -> Tuple[bool, str]:
    """
    Register ONE new ip2free account using the API (no browser).
    Uses ip2free_auto_register.py's approach: getRegisterCode → Outlook inbox poll → register.
    Calls ip2free_auto_register.py --max 1 --no-sync --env-file <f>
    """
    script_dir = Path(__file__).resolve().parent
    auto_reg = script_dir / "ip2free_auto_register.py"
    if not auto_reg.exists():
        return False, f"ip2free_auto_register.py not found: {auto_reg}"
    try:
        # ip2free_auto_register.py picks its own Outlook account and invite code
        # We pass invite_code via env var since the script picks from existing accounts
        # Better: call the script which loops existing ip2free accounts by invite code
        # For sync-triggered registrations, we want to register under a SPECIFIC invite_code.
        # ip2free_auto_register.py doesn't accept --invite-code so we use a wrapper approach:
        # write a temp account file and call the register API directly.
        r = subprocess.run(
            [sys.executable, str(auto_reg), "--max", "1", "--no-sync",
             "--env-file", env_file],
            capture_output=True, text=True,
            cwd=str(script_dir), timeout=300,
        )
        # Parse JSON summary from stdout
        lines = [l for l in r.stdout.strip().splitlines() if l.strip().startswith("{")]
        if lines:
            summary = json.loads(lines[-1])
            if summary.get("registered", 0) > 0:
                return True, f"registered via auto_register"
        return False, f"rc={r.returncode} {r.stderr[-200:]}"
    except Exception as e:
        return False, str(e)


def register_invitees_for_account(
    invite_code: str,
    needed: int,
    email: str,
    env_file: str = DEFAULT_ENV_FILE,
) -> Tuple[int, List[str]]:
    """
    Register `needed` new ip2free accounts under invite_code.
    Uses direct API registration (getRegisterCode → Graph → register).
    Returns (success_count, messages).
    """
    if not invite_code:
        return 0, ["no invite_code provided — skipping registration"]

    script_dir = Path(__file__).resolve().parent
    reg_script = script_dir / "ip2free_register.py"
    if not reg_script.exists():
        return 0, [f"register script not found: {reg_script}"]

    registered = 0
    msgs: List[str] = []
    for i in range(needed):
        try:
            r = subprocess.run(
                [sys.executable, str(reg_script),
                 "--invite-code", invite_code,
                 "--env-file", env_file,
                 "--no-sync",
                 "--headless", "true"],
                capture_output=True, text=True,
                cwd=str(script_dir), timeout=300,
            )
            if r.returncode == 0:
                registered += 1
                msgs.append(f"ok: invitee #{i+1} for invite={invite_code[:8]}")
                print(f"  [register_invitee] #{i+1} OK", flush=True)
            else:
                err = (r.stderr or r.stdout)[-300:]
                msgs.append(f"fail: invitee #{i+1} rc={r.returncode} {err}")
                print(f"  [register_invitee] #{i+1} FAIL rc={r.returncode}", flush=True)
        except Exception as e:
            msgs.append(f"exc: invitee #{i+1} {e}")
    return registered, msgs


# ── Event task claiming ───────────────────────────────────────────────────────

def claim_event_tasks(
    s: requests.Session,
    email: str,
    invite_code: str,            # ← from login profile (FIXED: was taken from task record before)
    env_file: str = DEFAULT_ENV_FILE,
) -> Tuple[int, List[str]]:
    """
    Claim monthly register_three task and daily client_click task.

    register_three: needs 3 invitees registered under account's invite_code.
      - If condition not met, register invitees then finishTask.
      - invite_code comes from login (profile.invite_code), NOT from task record
        (task records don't have invite_code fields — that was the v4.0 bug).

    client_click: click partner links via API then finishTask.
    """
    tasks = fetch_task_list(s)
    print(f"  [event] taskList: {len(tasks)} records", flush=True)
    for t in tasks:
        code = t.get("task_code", "")
        if code in AUTO_CLAIMABLE_CODES | DAILY_CLAIMABLE_CODES:
            print(
                f"    code={code} rec_id={t.get('id')} "
                f"finished={t.get('is_finished')} month={t.get('month')}",
                flush=True,
            )

    claimed = 0
    msgs: List[str] = []

    for t in tasks:
        code = t.get("task_code", "")
        if t.get("is_finished", 1):
            continue
        rec_id = t.get("id")
        if rec_id is None:
            continue

        # ── Daily client_click task ─────────────────────────────────────────
        if code in DAILY_CLAIMABLE_CODES:
            n_links = click_all_links(s)
            print(f"  [daily] clicked {n_links} partner links for {code}", flush=True)
            res = finish_task(s, rec_id)
            rc  = res.get("code", -1)
            msg = res.get("msg", "")
            print(f"  [daily] finishTask rec_id={rec_id} code={rc} msg={msg}", flush=True)
            if rc in (0, 200) or "success" in msg.lower() or "成功" in msg:
                claimed += 1
                msgs.append(f"claimed daily {code} rec_id={rec_id}")
            else:
                msgs.append(f"daily_fail {code} rec_id={rec_id} msg={msg}")
            continue

        # ── Monthly register_three task ─────────────────────────────────────
        if code not in AUTO_CLAIMABLE_CODES:
            continue

        needed  = INVITE_NEEDED.get(code, 0)
        month   = str(t.get("month", ""))

        if needed > 0 and invite_code:
            already = _invitees_registered_this_month(email, code, month)
            still_needed = max(0, needed - already)
            if still_needed > 0:
                print(
                    f"  [invite] need {still_needed} more invitees "
                    f"(already={already}) for {email} invite={invite_code[:8]}",
                    flush=True,
                )
                reg_count, reg_msgs = register_invitees_for_account(
                    invite_code, still_needed, email, env_file
                )
                msgs.extend(reg_msgs)
                _record_invitees(email, code, month, already + reg_count)
                if reg_count < still_needed:
                    msgs.append(f"insufficient_invitees {code}: {already+reg_count}/{needed}")
                    print(
                        f"  [invite] only got {reg_count}/{still_needed} — "
                        f"will retry next sync cycle",
                        flush=True,
                    )
                    continue
            else:
                msgs.append(f"invitees_already_done {code} month={month}")

        elif needed > 0 and not invite_code:
            msgs.append(f"no_invite_code for {email}, skipping {code}")
            print(f"  [invite] WARNING: no invite_code for {email}, cannot register invitees", flush=True)
            continue

        # Attempt to claim
        res = finish_task(s, rec_id)
        rc  = res.get("code", -1)
        msg = res.get("msg", "")
        print(f"  [event] finishTask rec_id={rec_id} code={rc} msg={msg}", flush=True)
        if rc in (0, 200) or "success" in msg.lower() or "成功" in msg:
            claimed += 1
            msgs.append(f"claimed {code} rec_id={rec_id}")
        elif "invalid" in msg.lower():
            msgs.append(f"task_not_ready {code} rec_id={rec_id} msg={msg}")
            # Don't retry invitees immediately; auto_register runs every 10 min
        else:
            msgs.append(f"claim_failed {code} rec_id={rec_id} msg={msg}")

    return claimed, msgs


# ── Proxy file helpers ────────────────────────────────────────────────────────

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


def _build_pm_proxy_dict(p: Dict[str, Any], email: str) -> Dict[str, Any]:
    ip   = p.get("ip", "").strip()
    port = int(p.get("port", 0) or 0)
    if not ip or not port:
        return {}
    uid = (p.get("proxy_uid") or p.get("id") or f"{ip}:{port}")
    expire = (p.get("expire_ts")
              or _parse_expire_ts(p.get("expired_at") or p.get("expire_time") or p.get("expires_at"))
              or None)
    return {
        "proxy_uid":     uid,
        "id":            uid,
        "ip":            ip,
        "port":          port,
        "username":      (p.get("username") or "").strip(),
        "password":      (p.get("password") or "").strip(),
        "protocol":      (p.get("protocol") or "socks5").lower(),
        "country_code":  (p.get("country_code") or p.get("country") or "").strip(),
        "city":          (p.get("city") or "").strip(),
        "source_account": email,
        "expire_time":   expire,
        "expires_at":    expire,
    }


def write_proxy_manager_json(
    results: List[Dict[str, Any]],
    path: str = f"{PROXY_DATA_DIR}/ip2free_proxies_all.json",
) -> int:
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


def load_and_inject_proxy_manager(
    path: str = f"{PROXY_DATA_DIR}/ip2free_proxies_all.json",
) -> None:
    if not Path(path).exists():
        return
    pm_script = Path("/data/Toolkit/scripts/proxy_manager.py")
    if not pm_script.exists():
        print(f"[warn] proxy_manager.py not found, skipping DB injection", file=sys.stderr)
        return
    try:
        r = subprocess.run(
            [sys.executable, str(pm_script), "load-file", path, "--source", "ip2free"],
            capture_output=True, text=True, cwd=str(pm_script.parent), timeout=120,
        )
        if r.returncode != 0:
            print(f"[warn] proxy_manager load-file failed: {r.stderr[-500:]}", file=sys.stderr)
        else:
            lines = r.stdout.strip().splitlines()
            if lines:
                print(f"[proxy_manager] {lines[-1]}", flush=True)
        r2 = subprocess.run(
            [sys.executable, str(pm_script), "inject-resi-pool"],
            capture_output=True, text=True, cwd=str(pm_script.parent), timeout=180,
        )
        if r2.returncode != 0:
            print(f"[warn] proxy_manager inject-resi-pool failed: {r2.stderr[-500:]}", file=sys.stderr)
        else:
            for line in r2.stdout.strip().splitlines():
                if line.strip():
                    print(f"[proxy_manager] {line}", flush=True)
    except Exception as e:
        print(f"[warn] proxy_manager injection error: {e}", file=sys.stderr)


# ── Per-account sync ──────────────────────────────────────────────────────────

def sync_account(
    email: str,
    pw: str,
    probe: bool = False,
    env_file: str = DEFAULT_ENV_FILE,
) -> Dict[str, Any]:
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
        s, _tok, invite_code = login(email, pw)
        print(f"  [login] OK invite_code={invite_code}", flush=True)

        # ── Free proxies ────────────────────────────────────────────────────
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

        # Persist free proxies (URL strings only, dedup)
        try:
            raw_existing = _load_json(FREE_PROXY_FILE, {}).get("proxies", [])
            # v3 stored dicts; v4 stores strings — normalise
            existing_urls: List[str] = [
                p if isinstance(p, str) else fmt_proxy_url(p)
                for p in raw_existing if p
            ]
            existing_set = set(existing_urls)
            new_urls = [fmt_proxy_url(p) for p in free_raw]
            merged = existing_urls + [u for u in new_urls if u not in existing_set]
            _save_json(FREE_PROXY_FILE, {"proxies": merged, "ts": time.time()})
        except Exception as e:
            print(f"  [warn] failed to save free proxy file: {e}", flush=True)

        # ── Event tasks (invite-3 monthly + client_click daily) ─────────────
        # FIXED: pass invite_code from login, not from task record
        claimed, claim_msgs = claim_event_tasks(s, email, invite_code, env_file=env_file)
        result["claimed"] = claimed
        result["claim_messages"] = claim_msgs

        # ── Event proxies ───────────────────────────────────────────────────
        event_raw = fetch_event_proxies(s)
        result["event_total"] = len(event_raw)
        event_proxies: List[Dict[str, Any]] = []
        for p in event_raw:
            entry = {
                "proxy_uid":    p.get("id") or p.get("proxy_uid") or f"{p.get('ip')}:{p.get('port')}",
                "ip":           p.get("ip", ""),
                "port":         int(p.get("port", 0) or 0),
                "username":     p.get("username", ""),
                "password":     p.get("password", ""),
                "protocol":     (p.get("protocol") or "socks5").lower(),
                "country_code": p.get("country_code", ""),
                "city":         p.get("city", ""),
                "remark":       p.get("remark", ""),
                "bind_status":  p.get("bind_status", 0),
                "assigned_at":  p.get("assigned_at", ""),
                "expired_at":   p.get("expired_at") or p.get("expire_time") or None,
                "expire_ts":    _parse_expire_ts(p.get("expired_at") or p.get("expire_time")),
                "source_account": email,
                "source":       "ip2free_event",
            }
            entry["url"] = fmt_proxy_url(entry)
            event_proxies.append(entry)

        result["event_proxies"] = event_proxies

        # Merge event proxies (dedup by proxy_uid)
        existing_event = _load_json(EVENT_PROXY_FILE, {}).get("proxies", [])
        if existing_event and isinstance(existing_event[0], str):
            existing_event = []  # clear legacy string format
        existing_uids = {p.get("proxy_uid") for p in existing_event if isinstance(p, dict)}
        new_event = [p for p in event_proxies if p.get("proxy_uid") not in existing_uids]
        merged_event = existing_event + new_event
        result["event_added"] = len(new_event)
        _save_json(EVENT_PROXY_FILE, {"proxies": merged_event, "ts": time.time()})

        print(
            f"  [account] free={result['free_total']}(+{result['free_added']}) "
            f"event={result['event_total']}(+{result['event_added']}) "
            f"claimed={result['claimed']}",
            flush=True,
        )
        result["success"] = True

    except Exception as e:
        result["error"] = str(e)
        traceback.print_exc()

    return result


# ── Credentials ───────────────────────────────────────────────────────────────

def load_credentials_env_file(path: str) -> List[Dict[str, str]]:
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


# ── Full sync run ─────────────────────────────────────────────────────────────

def run_sync(
    accounts: List[Dict[str, str]],
    probe: bool = False,
    env_file: str = DEFAULT_ENV_FILE,
) -> Dict[str, Any]:
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
        try:
            loaded = resi_pool.reload_externals()
            if loaded:
                print(f"[run_sync] loaded {loaded} existing external proxies", flush=True)
        except Exception:
            pass

    for i, acct in enumerate(accounts, 1):
        email = acct.get("email", "").strip()
        pw    = acct.get("password", "").strip()
        if not email or not pw:
            continue
        print(f"[{i}/{len(accounts)}] sync {email} ...", flush=True)
        r = sync_account(email, pw, probe=probe, env_file=env_file)
        summary["results"].append(r)
        summary["free_total"]  += r.get("free_total", 0)
        summary["free_added"]  += r.get("free_added", 0)
        summary["event_total"] += r.get("event_total", 0)
        summary["event_added"] += r.get("event_added", 0)
        summary["claimed"]     += r.get("claimed", 0)
        if r.get("error"):
            summary["errors"] += 1

    pm_count = write_proxy_manager_json(summary["results"])
    if pm_count:
        print(f"[sync] wrote {pm_count} proxies for proxy_manager", flush=True)
        load_and_inject_proxy_manager()

    _save_json(SYNC_STATE_FILE, summary)
    # Print compact summary (not full JSON with all proxies)
    compact = {k: v for k, v in summary.items() if k != "results"}
    compact["results"] = [
        {k2: v2 for k2, v2 in r.items()
         if k2 not in ("free_proxies", "event_proxies")}
        for r in summary["results"]
    ]
    print(json.dumps(compact, ensure_ascii=False, indent=2), flush=True)
    return summary


# ── Main ──────────────────────────────────────────────────────────────────────

def _release_stale_lock(lock_path: str) -> None:
    """If the lock file exists but no process holds it, remove it."""
    if not os.path.exists(lock_path):
        return
    try:
        fd = open(lock_path, "r")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # We got the lock → it was stale
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
            os.unlink(lock_path)
            print(f"[lock] removed stale lock: {lock_path}", flush=True)
        except (OSError, IOError):
            # Lock is held by another process — that's fine
            fd.close()
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="ip2free -> VPS proxy pool sync v4.1")
    parser.add_argument("--email",         default=os.environ.get("IP2FREE_EMAIL"))
    parser.add_argument("--password",      default=os.environ.get("IP2FREE_PASSWORD"))
    parser.add_argument("--accounts-json", help="JSON file with [{email,password},...]")
    parser.add_argument("--probe",         action="store_true")
    parser.add_argument("--loop",          action="store_true")
    parser.add_argument("--interval",      type=int, default=8 * 3600)
    parser.add_argument("--env-file",      default=DEFAULT_ENV_FILE)
    args = parser.parse_args()

    accounts: List[Dict[str, str]] = []
    if args.accounts_json:
        accounts = _load_json(args.accounts_json, [])
    elif args.email and args.password:
        accounts = [{"email": args.email, "password": args.password}]
    else:
        accounts = load_credentials_env_file(args.env_file)

    if not accounts:
        parser.error("No ip2free credentials found.")

    _release_stale_lock(LOCK_FILE)

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
