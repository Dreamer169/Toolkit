#!/usr/bin/env python3
"""
rotate_xray_ip.py — 从 CF IP 池取新 IP 替换 xray.json 中被封禁的 CF IP，然后 reload xray
用法：python3 rotate_xray_ip.py --banned-ip 172.67.199.22
"""
import sys, json, os, subprocess, argparse, threading, time

XRAY_JSON   = "/root/Toolkit/xray.json"
POOL_STATE  = "/tmp/cf_pool_state.json"
POOL_LOCK   = threading.Lock()

# ── 从磁盘池取一个未用 IP ──────────────────────────────────────────────────
def pop_ip_from_pool():
    try:
        with open(POOL_STATE) as f:
            state = json.load(f)
        available = state.get("available", [])
        if not available:
            return None, "pool_empty"
        entry = available.pop(0)
        state["available"] = available
        with open(POOL_STATE, "w") as f:
            json.dump(state, f)
        return entry.get("ip"), None
    except Exception as e:
        return None, str(e)

# ── 修改 xray.json：把旧 IP 全部替换为新 IP ─────────────────────────────
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

# ── Reload xray（先试 pm2，失败则 SIGHUP pid）─────────────────────────────
def reload_xray() -> str:
    r = subprocess.run(["pm2", "reload", "xray"], capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        return "pm2_reload_ok"
    # fallback: SIGHUP
    r2 = subprocess.run(["pkill", "-HUP", "-x", "xray"], capture_output=True, text=True)
    return "sighup_ok" if r2.returncode == 0 else "reload_failed"

# ── main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--banned-ip", required=True)
    args = ap.parse_args()
    banned = args.banned_ip.strip()

    new_ip, err = pop_ip_from_pool()
    if err:
        print(json.dumps({"success": False, "error": err})); return

    changed = patch_xray_json(banned, new_ip)
    if not changed:
        # 把 IP 放回池（没有实际用到）
        try:
            with open(POOL_STATE) as f:
                s = json.load(f)
            s["available"].insert(0, {"ip": new_ip, "latency": 1.5, "proxy": f"http://{new_ip}:443"})
            with open(POOL_STATE, "w") as f:
                json.dump(s, f)
        except Exception:
            pass
        print(json.dumps({"success": False, "error": f"banned_ip {banned} not found in xray.json"})); return

    reload_status = reload_xray()
    print(json.dumps({
        "success": True,
        "banned_ip": banned,
        "new_ip": new_ip,
        "changed_outbounds": changed,
        "reload": reload_status,
    }))

if __name__ == "__main__":
    main()
