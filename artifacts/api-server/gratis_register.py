#!/usr/bin/env python3
"""
gratis_register.py v2 — 自动注册 ia.gratis 账号并提取 API Token
使用 guerrillamail 接收验证邮件
"""
import re, time, sys, json, random, string, argparse, subprocess
import urllib.request, urllib.parse, urllib.error, http.cookiejar

REF      = "T82BX0NH"
BASE     = "https://ia.gratis"
GMAIL    = "https://www.guerrillamail.com/ajax.php"
PASSWORD = "Gratis2026!Secure"

# ── guerrillamail helpers ─────────────────────────────────────────────────────
def gml_session():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    url = GMAIL + "?f=get_email_address&lang=en&v=1.6.2"
    with opener.open(url, timeout=15) as r:
        d = json.loads(r.read())
    return opener, d["email_addr"], d["sid_token"]

def gml_check(opener, sid, retries=30, interval=6):
    for i in range(retries):
        url = f"{GMAIL}?f=check_email&seq=0&sid_token={sid}"
        with opener.open(url, timeout=15) as r:
            d = json.loads(r.read())
        msgs = d.get("list", [])
        # filter only ia.gratis mails
        gratis_msgs = [m for m in msgs if "ia.gratis" in m.get("mail_from", "")]
        if gratis_msgs:
            return gratis_msgs[0]["mail_id"]
        print(f"  等待邮件... ({i+1}/{retries})", flush=True)
        time.sleep(interval)
    return None

def gml_fetch(opener, sid, mail_id):
    url = f"{GMAIL}?f=fetch_email&email_id={mail_id}&sid_token={sid}"
    with opener.open(url, timeout=15) as r:
        return json.loads(r.read())

# ── ia.gratis helpers ─────────────────────────────────────────────────────────
def make_gratis_opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def get_csrf_cookie(opener, url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=15) as r:
        html = r.read().decode("utf-8", errors="replace")
    m = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', html)
    if m: return m.group(1)
    m = re.search(r'csrfmiddlewaretoken.*?value="([^"]+)"', html)
    if m: return m.group(1)
    raise RuntimeError("No CSRF token")

def register(gratis_opener, email, password):
    reg_url = f"{BASE}/registro/?ref={REF}"
    csrf = get_csrf_cookie(gratis_opener, reg_url)
    data = urllib.parse.urlencode({
        "csrfmiddlewaretoken": csrf,
        "lang": "es",
        "ref": REF,
        "email": email,
        "password": password,
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/registro/",
        data=data,
        headers={
            "Referer": reg_url,
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        }
    )
    with gratis_opener.open(req, timeout=20) as r:
        return r.geturl()

def verify_link(gratis_opener, link):
    req = urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"})
    with gratis_opener.open(req, timeout=20) as r:
        return r.geturl(), r.read().decode("utf-8", errors="replace")

def get_token_from_cuenta(gratis_opener):
    req = urllib.request.Request(f"{BASE}/cuenta/", headers={"User-Agent": "Mozilla/5.0"})
    with gratis_opener.open(req, timeout=20) as r:
        html = r.read().decode("utf-8", errors="replace")
        final = r.geturl()
    if "/entrar/" in final or "/verificar/" in final:
        return None, final, html

    # Try many patterns
    patterns = [
        r'data-token="([a-zA-Z0-9_\-]{20,})"',
        r'id="api[_-]token"[^>]*>([a-zA-Z0-9_\-]{20,})<',
        r'"api_token"\s*:\s*"([a-zA-Z0-9_\-]{20,})"',
        r'"token"\s*:\s*"([a-zA-Z0-9_\-]{20,})"',
        r'<code[^>]*>([a-zA-Z0-9_\-]{32,})</code>',
        r'value="([a-zA-Z0-9]{32,})"',
        # 32-char hex-like string (common API key format)
        r'\b([0-9a-f]{32})\b',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html):
            tok = m.group(1)
            if len(tok) >= 20 and tok not in ("csrfmiddlewaretoken",):
                return tok, final, html
    return None, final, html

def update_proxy(token, proxy_path):
    import os
    with open(proxy_path) as f:
        src = f.read()
    new_src = re.sub(r'API_TOKEN\s*=\s*"[^"]*"', f'API_TOKEN = "{token}"', src)
    if new_src == src:
        print(f"  WARNING: pattern not found in {proxy_path}")
        return False
    bak = proxy_path + f".bak.{int(time.time())}"
    with open(bak, "w") as f: f.write(src)
    with open(proxy_path, "w") as f: f.write(new_src)
    print(f"  Updated {proxy_path} → backup {bak}")
    return True

# ── single registration ───────────────────────────────────────────────────────
def register_one(args, idx):
    print(f"\n{'='*60}")
    print(f"[{idx}] 注册 ia.gratis (ref={REF})")

    # Step 1: get guerrillamail temp email
    print("  [1] 获取临时邮箱 (guerrillamail)...")
    try:
        gml_opener, email, sid = gml_session()
        print(f"  邮箱: {email}  sid: {sid}")
    except Exception as e:
        print(f"  ERROR: {e}"); return None

    # Step 2: register
    print("  [2] 注册 ia.gratis...")
    gratis_opener = make_gratis_opener()
    try:
        final_url = register(gratis_opener, email, PASSWORD)
        print(f"  → {final_url}")
    except Exception as e:
        print(f"  ERROR: {e}"); return None

    # Step 3: wait for email
    print("  [3] 等待验证邮件...")
    try:
        mail_id = gml_check(gml_opener, sid, retries=30, interval=6)
        if not mail_id:
            print("  ERROR: 超时未收到邮件"); return None
        msg = gml_fetch(gml_opener, sid, mail_id)
        body = msg.get("mail_body", "")
        print(f"  主题: {msg.get('mail_subject','?')}")
        links = re.findall(r'https://ia\.gratis[^\s<>\'"]+', body)
        verify_url = next((l for l in links if any(k in l for k in ["verif","activ","confirm","token","uid","key"])), None)
        if not verify_url and links:
            verify_url = links[0]
        if not verify_url:
            print(f"  ERROR: 未找到验证链接 body={body[:300]}"); return None
        print(f"  验证链接: {verify_url[:80]}")
    except Exception as e:
        print(f"  ERROR: {e}"); return None

    # Step 4: click verify link
    print("  [4] 验证...")
    try:
        final2, html2 = verify_link(gratis_opener, verify_url)
        print(f"  → {final2}")
    except Exception as e:
        print(f"  ERROR: {e}"); return None

    # Step 5: get token
    print("  [5] 获取 API Token...")
    time.sleep(2)
    try:
        token, final3, html3 = get_token_from_cuenta(gratis_opener)
        if token:
            print(f"  ✓ Token: {token}")
            return {"email": email, "token": token}
        else:
            print(f"  未从 /cuenta/ 提取到 token (url={final3})")
            print(f"  HTML 片段:\n{html3[:1500]}")
            return {"email": email, "token": None, "debug": html3[:2000]}
    except Exception as e:
        print(f"  ERROR: {e}"); return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--update-proxy", action="store_true")
    parser.add_argument("--proxy-path", default="/data/Toolkit/artifacts/api-server/gratis_proxy.py")
    args = parser.parse_args()

    results = []
    for i in range(1, args.count + 1):
        r = register_one(args, i)
        if r: results.append(r)
        if i < args.count: time.sleep(5)

    print(f"\n{'='*60}")
    print(f"结果: {len(results)}/{args.count}")
    for r in results:
        print(f"  {r['email']}  token={r.get('token','N/A')}")

    if args.update_proxy:
        valid = [r for r in results if r.get("token")]
        if valid:
            tok = valid[0]["token"]
            if update_proxy(tok, args.proxy_path):
                subprocess.run(["pm2", "restart", "gratis-proxy"], check=False)
                print("pm2 gratis-proxy 重启完成")

if __name__ == "__main__":
    main()
