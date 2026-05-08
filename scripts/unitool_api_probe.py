#!/usr/bin/env python3
"""
unitool_api_probe.py — 直接探测 unitool.ai API v1.2
=======================================================
跳过 /api/user (IP屏蔽), 直接用SSID测 /api/chats + messages + paginatedMessages
v1.2: 先探活RESI端口，重试时换端口

用法:
  python3 unitool_api_probe.py               # quick test (5 services)
  python3 unitool_api_probe.py --all         # 全部服务
  python3 unitool_api_probe.py --model gpt-5
  python3 unitool_api_probe.py --list-services
"""
import argparse, json, os, random, subprocess, sys, time
import requests

SSID_DIR   = "/data/unitool_ssids"
BASE       = "https://unitool.ai"
ALL_RESI   = list(range(10851, 10860)) + list(range(10870, 10890))

ALL_CHAT_SERVICES = [
    "gpt-5", "gpt-5.5", "gpt-5.4", "gpt-4-1", "gpt-4o", "gpt-4o-mini", "gpt-4-5",
    "claude-sonnet", "claude-sonnet-4-5", "claude-sonnet-4-6",
    "claude-opus", "claude-opus-4-6", "claude-haiku",
    "gemini-3.1-pro", "gemini-3-pro", "grok",
    "gpt-o1", "gpt-o1-mini", "gpt-o3", "gpt-o3-mini", "gpt-o4-mini", "gpt-5-nano",
]
QUICK_SERVICES = ["gpt-5", "gpt-4o-mini", "gpt-4-5", "claude-sonnet-4-5", "gpt-5.5", "claude-sonnet-4-6", "grok"]
TEST_PROMPT = "Reply with exactly one word: PROBE_OK"

_alive_ports: list[int] = []

def get_alive_ports(force=False) -> list[int]:
    """Probe RESI ports and return working ones. Cache result."""
    global _alive_ports
    if _alive_ports and not force:
        return _alive_ports
    sys.stdout.write("Probing RESI ports... ")
    sys.stdout.flush()
    from concurrent.futures import ThreadPoolExecutor, as_completed
    def check(p):
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "5", "--socks5-hostname", f"127.0.0.1:{p}",
                 "-o", "/dev/null", "-w", "%{http_code}", "https://unitool.ai/en/entry"],
                capture_output=True, text=True, timeout=7)
            return p, r.stdout.strip() not in ("", "000")
        except Exception:
            return p, False
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(as_completed([ex.submit(check, p) for p in ALL_RESI]))
    alive = [r.result()[0] for r in results if r.result()[1]]
    if not alive:
        alive = ALL_RESI[:6]  # fallback
    _alive_ports = sorted(alive)
    print(f"alive={_alive_ports}")
    return _alive_ports

def pick_port(avoid: int = 0) -> int:
    ports = [p for p in get_alive_ports() if p != avoid]
    return random.choice(ports) if ports else random.choice(get_alive_ports())

def first_ssid(min_balance=5.0):
    """Return (label, ssid) for first SSID with enough balance."""
    # Try to read balance from proxy log to pick a good SSID
    high_bal = []
    try:
        log = open("/root/.pm2/logs/unitool-proxy-out.log").read()
        import re
        for m in re.finditer(r"\[BAL\] ✓ ([^:]+): balance=(\d+\.\d+)", log):
            email, bal = m.group(1).strip(), float(m.group(2))
            if bal >= min_balance:
                high_bal.append(email)
    except Exception:
        pass

    # First try high-balance SSIDs
    for email in high_bal:
        safe = email.replace("@", "@").replace(".", "_").replace("@", "_")
        for fn in [f"{email}.txt", f"{safe}.txt"]:
            path = os.path.join(SSID_DIR, fn)
            if os.path.exists(path):
                ssid = open(path).read().strip()
                if ssid and len(ssid) > 50:
                    return email, ssid
    # Fallback: any SSID file
    for fn in sorted(os.listdir(SSID_DIR)):
        if fn.endswith(".txt"):
            ssid = open(os.path.join(SSID_DIR, fn)).read().strip()
            if ssid and len(ssid) > 50:
                return fn[:-4], ssid
    raise RuntimeError(f"No SSID files in {SSID_DIR}")

def hdrs(ssid):
    return {
        "Cookie":       f"__Secure-unitool-ssid={ssid}",
        "Content-Type": "application/json",
        "User-Agent":   "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/136",
        "Origin":       "https://unitool.ai",
        "Referer":      "https://unitool.ai/en/chatgpt",
        "Accept":       "application/json",
    }

def make_sess(port):
    s = requests.Session()
    s.proxies = {"http": f"socks5h://127.0.0.1:{port}", "https": f"socks5h://127.0.0.1:{port}"}
    return s

def test_session(ssid):
    port = pick_port()
    try:
        r = make_sess(port).get(f"{BASE}/api/auth/session", headers=hdrs(ssid), timeout=10)
        if r.status_code == 401:
            return False, "401 Unauthorized"
        d = r.json()
        if d.get("error"):
            return False, d["error"]
        user = (d.get("auth") or {}).get("user") or d.get("user")
        if user and user.get("id"):
            return True, f"valid user={user.get('email','?')}"
        return False, f"no user: {str(d)[:80]}"
    except Exception as e:
        return None, f"network: {e}"

def test_service(service_id, ssid, timeout=40):
    """Test service: create_chat → send_msg → poll. Retry with new port on ConnectionReset."""
    start  = time.time()
    port   = pick_port()
    s      = make_sess(port)
    chat_id = None
    res = {"service": service_id, "ok": False, "error": None,
           "response": None, "elapsed": 0, "port": port}
    try:
        # 1. create_chat — response {"id": ...} not {"chat": {"id": ...}}
        for attempt in range(2):
            try:
                r = s.post(f"{BASE}/api/chats",
                           json={"service_id": service_id, "title": ""},
                           headers=hdrs(ssid), timeout=15)
                break
            except requests.exceptions.ConnectionError:
                if attempt == 0:
                    port = pick_port(avoid=port)
                    s    = make_sess(port)
                    res["port"] = port
                else:
                    raise
        if r.status_code != 200:
            res["error"] = f"create_chat HTTP {r.status_code}: {r.text[:120]}"
            return res
        ch = r.json()
        chat_id = ch.get("id")   # FIXED: was ch.get("chat",{}).get("id")
        if not chat_id:
            res["error"] = f"create_chat no id in: {ch}"
            return res

        # 2. send message
        for attempt in range(2):
            try:
                r2 = s.post(f"{BASE}/api/chats/{chat_id}/messages",
                            json={"content": TEST_PROMPT, "attachments": [], "options": ""},
                            headers=hdrs(ssid), timeout=15)
                break
            except requests.exceptions.ConnectionError:
                if attempt == 0:
                    port = pick_port(avoid=port)
                    s    = make_sess(port)
                    res["port"] = port
                else:
                    raise
        if r2.status_code != 200:
            res["error"] = f"send_msg HTTP {r2.status_code}: {r2.text[:150]}"
            return res
        msg_resp = r2.json()
        if msg_resp.get("error"):
            res["error"] = f"send_msg: {msg_resp['error']}"
            return res
        if msg_resp.get("code") == 500:
            res["error"] = f"backend 500: {msg_resp.get('msg','')}"
            return res
        user_msg_id = msg_resp.get("message", {}).get("id")

        # 3. poll paginatedMessages until status=ended
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.5)
            try:
                r3 = s.get(f"{BASE}/api/chats/{chat_id}/paginatedMessages",
                           params={"limit": 10}, headers=hdrs(ssid), timeout=12)
            except requests.exceptions.ConnectionError:
                port = pick_port(avoid=port)
                s    = make_sess(port)
                continue
            if r3.status_code != 200:
                res["error"] = f"poll HTTP {r3.status_code}"
                return res
            msgs = r3.json().get("data", [])
            matched = False
            for m in msgs:
                if m.get("role") != "assistant" or m.get("reply_to") != user_msg_id:
                    continue
                matched = True
                status  = m.get("status", "")
                content = m.get("content", "") or ""
                if status == "error":
                    res["error"] = f"service_error: {content[:200]}"
                    return res
                if status == "ended":
                    res["ok"]       = True
                    res["response"] = content[:80] if content else "[attachments]"
                    return res
            # claude-opus fix: catch error msgs without reply_to match
            if not matched:
                for m in msgs:
                    if m.get("role") == "assistant" and m.get("status") == "error":
                        etxt = m.get("content", "") or ""
                        if etxt:
                            res["error"] = f"svc_err(no reply_to): {etxt[:200]}"
                            return res
        res["error"] = f"timeout {timeout}s"
    except requests.exceptions.ConnectionError as e:
        res["error"] = f"conn_reset: {e}"
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    finally:
        res["elapsed"] = round(time.time() - start, 1)
        if chat_id:
            try: make_sess(pick_port()).delete(f"{BASE}/api/chats/{chat_id}",
                                              headers=hdrs(ssid), timeout=5)
            except Exception: pass
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="Test single model")
    ap.add_argument("--ssid", help="Override SSID")
    ap.add_argument("--all",  action="store_true", help="All services")
    ap.add_argument("--list-services", action="store_true")
    ap.add_argument("--skip-session", action="store_true")
    ap.add_argument("--timeout", type=int, default=40)
    ap.add_argument("--min-balance", type=float, default=5.0, help="Min balance for SSID selection")
    args = ap.parse_args()

    get_alive_ports()  # probe ports upfront

    label, ssid = ("cli", args.ssid) if args.ssid else first_ssid(args.min_balance)
    print(f"SSID: {label} ({ssid[:20]}...)")

    if not args.skip_session:
        ok, msg = test_session(ssid)
        sym = "OK" if ok else ("WARN" if ok is None else "FAIL")
        print(f"[{sym}] /api/auth/session: {msg}")

    if args.list_services:
        try:
            port = pick_port()
            r = make_sess(port).get(f"{BASE}/api/services", headers=hdrs(ssid), timeout=10)
            svcs = r.json()
            lst  = svcs if isinstance(svcs, list) else svcs.get("data", [])
            print(f"\n/api/services ({len(lst)} entries):")
            for sv in lst:
                sid    = sv.get("id") or sv.get("name", "?")
                active = sv.get("active")
                minbal = sv.get("minimum_balance")
                parent = sv.get("parent_id", "")
                print(f"  {sid:<30} active={str(active):<6} min_bal={str(minbal):<10} parent={parent}")
        except Exception as e:
            print(f"list_services error: {e}")
        return

    services = ([args.model] if args.model
                else ALL_CHAT_SERVICES if args.all
                else QUICK_SERVICES)
    print(f"\nTesting {len(services)} services (timeout={args.timeout}s):\n")

    results = []
    for svc in services:
        sys.stdout.write(f"  {svc:<30}")
        sys.stdout.flush()
        r = test_service(svc, ssid, args.timeout)
        results.append(r)
        if r["ok"]:
            print(f" OK  {r['elapsed']:>5.1f}s  {r['response']}")
        else:
            print(f" FAIL {r['elapsed']:>5.1f}s  {r['error']}")

    ok_n = sum(1 for r in results if r["ok"])
    print(f"\n{'='*70}")
    print(f"RESULT: {ok_n}/{len(results)} working")
    failed = [r for r in results if not r["ok"]]
    if failed:
        print("\nFailed:")
        for r in failed:
            print(f"  {r['service']:<30} {r['error']}")

if __name__ == "__main__":
    main()
