#!/usr/bin/env python3
"""
replit_login.py — 用 patchright 登录 Reseek 账号，提取 session cookie + API key。
用法: python3 replit_login.py '<json>'
JSON: { "email": "...", "password": "...", "proxy": "socks5://..." }
返回: { "ok": true, "cookie": "connect.sid=...", "api_key": "...", "username": "..." }
"""
import sys, json, time, re

if len(sys.argv) < 2:
    print(json.dumps({"ok": False, "error": "缺少参数"}))
    sys.exit(1)

args = json.loads(sys.argv[1])
email    = args.get("email", "")
password = args.get("password", "")
proxy    = args.get("proxy", "")

if not email or not password:
    print(json.dumps({"ok": False, "error": "email/password 不能为空"}))
    sys.exit(1)

try:
    from patchright.sync_api import sync_playwright

    launch_args = [
        "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-extensions", "--mute-audio",
    ]
    proxy_cfg = None
    if proxy:
        # socks5://user:pass@host:port or socks5://host:port
        proxy_cfg = {"server": proxy}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=launch_args,
            proxy=proxy_cfg if proxy_cfg else None
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )
        page = ctx.new_page()

        # ── 1. 打开登录页 ──────────────────────────────────────────────────────
        print("[replit_login] 打开登录页...", flush=True)
        page.goto("https://replit.com/login", timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # ── 2. 填写邮箱 ────────────────────────────────────────────────────────
        try:
            page.locator('input[name="username"], input[type="email"], input[placeholder*="email" i], input[placeholder*="username" i]').first.fill(email, timeout=8000)
        except Exception:
            page.keyboard.type(email)

        # ── 3. 填写密码 ────────────────────────────────────────────────────────
        try:
            page.locator('input[name="password"], input[type="password"]').first.fill(password, timeout=8000)
        except Exception:
            print("[replit_login] 未找到密码框", flush=True)

        # ── 4. 提交 ────────────────────────────────────────────────────────────
        try:
            page.locator('button[type="submit"], button:text("Log in"), button:text("Sign in")').first.click(timeout=5000)
        except Exception:
            page.keyboard.press("Enter")

        print("[replit_login] 等待登录完成...", flush=True)
        page.wait_for_timeout(8000)

        # ── 5. 检查登录结果 ────────────────────────────────────────────────────
        cur_url = page.url
        print(f"[replit_login] 当前URL: {cur_url[:80]}", flush=True)

        if "login" in cur_url.lower():
            # 可能需要验证码，或者用户名/密码错误
            title = page.title()
            print(json.dumps({"ok": False, "error": f"登录失败，仍在登录页: {title[:60]}"}))
            browser.close()
            sys.exit(0)

        # ── 6. 获取 session cookie ──────────────────────────────────────────────
        cookies = ctx.cookies()
        session_cookie = ""
        for c in cookies:
            if "connect.sid" in c["name"] or "replit_session" in c["name"] or c["name"].startswith("__Secure"):
                session_cookie = f"{c['name']}={c['value']}"
                break
        if not session_cookie and cookies:
            session_cookie = "; ".join(f"{c['name']}={c['value']}" for c in cookies[:5])

        # ── 7. 获取用户名 ──────────────────────────────────────────────────────
        username = ""
        try:
            username = re.search(r"replit\.com/@([^/?]+)", page.url)
            if username:
                username = username.group(1)
            else:
                # 尝试从页面内容提取
                username = page.evaluate("() => window.__REPLIT_NEXT_DATA__?.user?.username || ''") or ""
        except Exception:
            pass

        # ── 8. 访问 API key 页面 ────────────────────────────────────────────────
        api_key = ""
        try:
            page.goto("https://replit.com/account#api-keys", timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            # 寻找现有 API key 或创建新的
            key_el = page.locator('[data-testid="api-key"], input[value^="r8_"], code:text("r8_")').first
            if key_el.is_visible(timeout=3000):
                api_key = key_el.input_value() if key_el.evaluate("el => el.tagName") == "INPUT" else key_el.text_content() or ""
            if not api_key:
                # 点击创建新 API key
                create_btn = page.locator('button:text("Create"), button:text("Generate"), button:text("New API key")').first
                if create_btn.is_visible(timeout=3000):
                    create_btn.click()
                    page.wait_for_timeout(2000)
                    key_el2 = page.locator('[data-testid="api-key"], input[value^="r8_"]').first
                    if key_el2.is_visible(timeout=3000):
                        api_key = key_el2.input_value() or ""
        except Exception as e:
            print(f"[replit_login] 获取 API key 失败: {e}", flush=True)

        browser.close()

    print(json.dumps({
        "ok": True,
        "cookie": session_cookie,
        "api_key": api_key,
        "username": username,
        "email": email,
    }))

except Exception as e:
    print(json.dumps({"ok": False, "error": str(e)}))
