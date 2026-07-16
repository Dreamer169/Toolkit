#!/usr/bin/env python3
"""
yn_register.py — Yonoo.ai 账号批量注册工具
==========================================
Yonoo 无验证码、无邮箱验证，纯 HTTP 直连注册。
注册成功后可写入 /data/yonoo-proxy/accounts.json 并 pm2 restart yonoo-proxy。

用法:
  python3 yn_register.py --count 10
  python3 yn_register.py --count 50 --update-proxy
"""

import argparse, json, os, random, string, sys, time, uuid
import urllib.request, urllib.parse, urllib.error, http.cookiejar

YONOO_REGISTER = "https://yonoo.ai/api/auth/register"
YONOO_LOGIN    = "https://yonoo.ai/api/auth/login"
MAILTM_API     = "https://api.mail.tm"
ACCOUNTS_FILE  = "/data/yonoo-proxy/accounts.json"
PASSWORD       = "Pool@Pass2026!x"

# ── 工具函数 ──────────────────────────────────────────────────────────────────
def _rand_str(n=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

def _rand_name():
    first = random.choice(["Alex","Jordan","Taylor","Morgan","Casey","Riley","Avery","Quinn",
                            "Blake","Cameron","Drew","Finley","Hayden","Jamie","Kendall","Lee",
                            "Miko","Noel","Parker","Reese","Sam","Skyler","Tatum","Uma","Val"])
    last  = random.choice(["Smith","Brown","Chen","Liu","Park","Kim","Wang","Lin","Wu","Zhang",
                            "Yang","Huang","Lee","Garcia","Wilson","Davis","Moore","Johnson"])
    return first + " " + last

_MAILTM_DOMAINS = None
def _get_domain():
    global _MAILTM_DOMAINS
    if not _MAILTM_DOMAINS:
        try:
            req = urllib.request.Request(MAILTM_API + "/domains",
                headers={"Accept": "application/ld+json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            _MAILTM_DOMAINS = [x["domain"] for x in d.get("hydra:member", [])] or ["web-library.net"]
        except Exception:
            _MAILTM_DOMAINS = ["web-library.net"]
    return random.choice(_MAILTM_DOMAINS)

# ── yonoo 注册 & 登录 ────────────────────────────────────────────────────────
def _yonoo_register(email, password, name):
    payload = json.dumps({"email": email, "password": password, "name": name}).encode()
    req = urllib.request.Request(YONOO_REGISTER, data=payload,
        headers={"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
        if d.get("success") and d.get("user"):
            return True, str(d["user"].get("id", "?"))
        return False, str(d)[:100]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:150]
        return False, "HTTP " + str(e.code) + ": " + body
    except Exception as e:
        return False, str(e)[:100]

def _yonoo_login(email, password):
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    payload = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(YONOO_LOGIN, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        with opener.open(req, timeout=20) as r:
            d = json.loads(r.read())
        return bool(d.get("success") or d.get("user"))
    except Exception:
        return False

# ── 持久化 ────────────────────────────────────────────────────────────────────
def _load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    try:
        with open(ACCOUNTS_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def _save_account(email, password):
    accs = _load_accounts()
    if any(a["email"] == email for a in accs):
        return
    accs.append({"email": email, "password": password})
    os.makedirs(os.path.dirname(ACCOUNTS_FILE), exist_ok=True)
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(accs, f, indent=2)

# ── 主注册流程 ────────────────────────────────────────────────────────────────
def register_one(idx):
    ts = time.strftime("%H:%M:%S")
    email    = "yn_" + _rand_str(8) + "_" + uuid.uuid4().hex[:6] + "@" + _get_domain()
    name     = _rand_name()
    print("[" + ts + "] [" + str(idx) + "] " + email, flush=True)

    ok, uid = _yonoo_register(email, PASSWORD, name)
    if not ok:
        print("  ERROR: " + uid, flush=True)
        return None
    print("  OK uid=" + uid, flush=True)

    time.sleep(0.8)
    logged_in = _yonoo_login(email, PASSWORD)
    if logged_in:
        print("  LOGIN OK", flush=True)
    else:
        print("  LOGIN WARN (账号已创建)", flush=True)

    return {"email": email, "password": PASSWORD, "uid": uid}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count",        type=int,   default=5)
    parser.add_argument("--delay",        type=float, default=1.2)
    parser.add_argument("--update-proxy", action="store_true")
    args = parser.parse_args()

    results, failed = [], 0
    for i in range(1, args.count + 1):
        r = register_one(i)
        if r:
            results.append(r)
        else:
            failed += 1
        if i < args.count:
            time.sleep(args.delay)

    print("\n" + "="*60, flush=True)
    print("完成: 成功 " + str(len(results)) + " / 失败 " + str(failed) + " / 总计 " + str(args.count), flush=True)
    for r in results:
        print("  " + r["email"] + "  uid=" + r.get("uid","?"), flush=True)

    # job-queue 读取
    print("\n__ACCOUNTS_JSON__", flush=True)
    print(json.dumps(results), flush=True)

    if args.update_proxy and results:
        saved = 0
        for r in results:
            try:
                _save_account(r["email"], r["password"])
                saved += 1
            except Exception as e:
                print("保存失败 " + r["email"] + ": " + str(e), flush=True)
        print("写入 " + str(saved) + " 个账号 -> " + ACCOUNTS_FILE, flush=True)
        import subprocess
        ret = subprocess.run(["pm2", "restart", "yonoo-proxy"], capture_output=True, text=True)
        print("pm2 restart: " + ("OK" if ret.returncode == 0 else ret.stderr[:80]), flush=True)

if __name__ == "__main__":
    main()
