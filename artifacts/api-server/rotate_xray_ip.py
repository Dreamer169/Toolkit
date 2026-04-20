#!/usr/bin/env python3
import sys, json, os, subprocess, argparse, tempfile

XRAY_JSON = "/root/Toolkit/xray.json"
POOL_STATE = "/tmp/cf_pool_state.json"
API_DIR = os.path.dirname(os.path.abspath(__file__))
CF_POOL_API = os.path.join(API_DIR, "cf_pool_api.py")
REFILL_MIN = int(os.environ.get("CF_POOL_REFILL_MIN", "25"))
REFILL_TARGET = int(os.environ.get("CF_POOL_REFILL_TARGET", "80"))
REFILL_COUNT = int(os.environ.get("CF_POOL_REFILL_COUNT", "240"))


def _lock_file(f):
    try:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    except Exception:
        pass


def _unlock_file(f):
    try:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


def _default_state():
    return {"available": [], "used_history": [], "history": [], "banned": []}


def _clean_state(state):
    if not isinstance(state, dict):
        state = {}
    available = state.get("available") or []
    history = state.get("used_history") or state.get("history") or []
    banned = state.get("banned") or []
    history = [x for x in history if isinstance(x, str)]
    banned = [x for x in banned if isinstance(x, str)]
    blocked = set(history) | set(banned)
    clean = []
    seen = set()
    for item in available:
        if not isinstance(item, dict):
            continue
        ip = item.get("ip")
        lat = item.get("latency")
        if not isinstance(ip, str) or not isinstance(lat, (int, float)):
            continue
        if ip in blocked or ip in seen:
            continue
        seen.add(ip)
        clean.append({"ip": ip, "latency": lat, "proxy": item.get("proxy") or f"http://{ip}:443"})
    clean.sort(key=lambda x: x["latency"])
    hist = history[-2000:]
    return {"available": clean, "used_history": hist, "history": hist, "history_count": len(hist), "banned": banned[-2000:]}


def _atomic_write_state(state):
    d = os.path.dirname(POOL_STATE) or "."
    fd, tmp = tempfile.mkstemp(prefix="cf_pool_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(tmp, POOL_STATE)
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def _write_state_locked(mutator):
    os.makedirs(os.path.dirname(POOL_STATE) or ".", exist_ok=True)
    if not os.path.exists(POOL_STATE):
        _atomic_write_state(_default_state())
    with open(POOL_STATE, "r+") as f:
        _lock_file(f)
        try:
            try:
                state = json.load(f)
            except Exception:
                state = _default_state()
            state = _clean_state(state)
            result = mutator(state)
            state = _clean_state(state)
            _atomic_write_state(state)
            return result, state
        finally:
            _unlock_file(f)


def trigger_background_refill(reason: str) -> bool:
    if not os.path.exists(CF_POOL_API):
        return False
    try:
        with open(os.devnull, "wb") as devnull:
            subprocess.Popen([
                sys.executable, CF_POOL_API, "refresh",
                "--count", str(REFILL_COUNT),
                "--target", str(REFILL_TARGET),
                "--threads", "12",
                "--port", "443",
                "--max-latency", "800",
            ], stdout=devnull, stderr=devnull, stdin=devnull, close_fds=True, start_new_session=True,
               env={**os.environ, "PYTHONUNBUFFERED": "1", "CF_POOL_REFILL_REASON": reason})
        return True
    except Exception:
        return False


def pop_ip_from_pool(banned_ip: str):
    def mutate(state):
        banned = state.setdefault("banned", [])
        history = state.setdefault("used_history", state.get("history", []))
        if banned_ip and banned_ip not in banned:
            banned.append(banned_ip)
        state["available"] = [x for x in state.get("available", []) if x.get("ip") != banned_ip]
        if not state["available"]:
            return None
        entry = state["available"].pop(0)
        ip = entry.get("ip")
        if ip and ip not in history:
            history.append(ip)
        state["history"] = history
        state["history_count"] = len(history)
        return entry
    entry, state = _write_state_locked(mutate)
    remaining = len(state.get("available", []))
    refill_started = False
    if remaining < REFILL_MIN:
        refill_started = trigger_background_refill("low_watermark")
    if not entry:
        refill_started = trigger_background_refill("pool_empty") or refill_started
        return None, "pool_empty", remaining, refill_started
    return entry, None, remaining, refill_started


def push_ip_back(entry):
    if not entry or not isinstance(entry, dict):
        return
    ip = entry.get("ip")
    if not ip:
        return
    def mutate(state):
        history = state.setdefault("used_history", state.get("history", []))
        state["used_history"] = [x for x in history if x != ip]
        state["history"] = state["used_history"]
        present = {x.get("ip") for x in state.get("available", []) if isinstance(x, dict)}
        if ip not in present:
            state.setdefault("available", []).insert(0, entry)
        state["history_count"] = len(state["used_history"])
    _write_state_locked(mutate)


def patch_xray_json(banned_ip: str, new_ip: str) -> int:
    with open(XRAY_JSON) as f:
        cfg = json.load(f)
    changed = 0
    for ob in cfg.get("outbounds", []):
        vnext = ob.get("settings", {}).get("vnext", [])
        for v in vnext:
            if v.get("address") == banned_ip:
                v["address"] = new_ip
                changed += 1
    if changed:
        with open(XRAY_JSON, "w") as f:
            json.dump(cfg, f, indent=2)
    return changed


def reload_xray() -> str:
    try:
        r = subprocess.run(["pm2", "reload", "xray"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return "pm2_reload_ok"
    except Exception:
        pass
    try:
        r2 = subprocess.run(["pkill", "-HUP", "-x", "xray"], capture_output=True, text=True, timeout=5)
        return "sighup_ok" if r2.returncode == 0 else "reload_failed"
    except Exception:
        return "reload_failed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--banned-ip", default="")
    ap.add_argument("--pop-only", action="store_true", help="pop IP only, skip xray patch")
    args = ap.parse_args()
    banned = args.banned_ip.strip()

    if args.pop_only:
        entry, err, remaining, refill = pop_ip_from_pool(banned or "__none__")
        if err or not entry:
            print(__import__("json").dumps({"success":False,"error":err or "pool_empty","remaining":remaining}))
            return
        print(__import__("json").dumps({"success":True,"new_ip":entry["ip"],"latency":entry.get("latency"),"remaining":remaining}))
        return

    if not banned:
        print(__import__("json").dumps({"success":False,"error":"--banned-ip required unless --pop-only"}))
        return

    entry, err, remaining, refill_started = pop_ip_from_pool(banned)
    if err or not entry:
        print(json.dumps({"success": False, "error": err or "pool_empty", "refill_started": refill_started, "remaining": remaining}))
        return

    new_ip = entry["ip"]
    changed = patch_xray_json(banned, new_ip)
    if not changed:
        push_ip_back(entry)
        print(json.dumps({"success": False, "error": f"banned_ip {banned} not found in xray.json", "refill_started": refill_started, "remaining": remaining}))
        return

    reload_status = reload_xray()
    print(json.dumps({
        "success": True,
        "banned_ip": banned,
        "new_ip": new_ip,
        "changed_outbounds": changed,
        "reload": reload_status,
        "remaining": remaining,
        "refill_started": refill_started,
    }))


if __name__ == "__main__":
    main()
