#!/usr/bin/env python3
"""
replit_deploy_agent.py
Fork @skingsbp/gh-cli-install → Deploy → 返回 URL，自动注册为 friend node。
用法: python3 replit_deploy_agent.py '<json>'
JSON: {
  "email": "...",
  "password": "...",
  "outlook_token": "",        // 可选，用于邮箱验证
  "gateway_url": "http://45.205.27.69:8080",
  "source_project": "https://replit.com/@skingsbp/gh-cli-install",
  "proxy": "socks5://...",    // 可选
  "headless": true,
  "deploy": true              // 是否执行 Deploy（需付费账号）
}
"""
import sys, json, re, time

if len(sys.argv) < 2:
    print(json.dumps({"ok": False, "error": "缺少参数"}))
    sys.exit(1)

args = json.loads(sys.argv[1])
email          = args["email"]
password       = args["password"]
outlook_tok    = args.get("outlook_token", "")
gateway_url    = args.get("gateway_url", "http://45.205.27.69:8080").rstrip("/")
proxy_str      = args.get("proxy", "")
headless       = args.get("headless", True)
source_project = args.get("source_project", "https://replit.com/@skingsbp/gh-cli-install")
do_deploy      = args.get("deploy", True)

def log(msg):
    print(f"[deploy] {msg}", flush=True)

try:
    from playwright.sync_api import sync_playwright

    pw_proxy = None
    if proxy_str:
        pw_proxy = {"server": proxy_str}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            proxy=pw_proxy,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(ctx.new_page())
        except Exception:
            pass

        page = ctx.new_page()
        page.set_default_timeout(60000)

        # ── 1. 登录 ─────────────────────────────────────────────────────────────
        log("导航到登录页...")
        page.goto("https://replit.com/login", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        log(f"填写邮箱: {email}")
        for sel in ['input[name="username"]', 'input[placeholder*="email" i]',
                    'input[type="email"]', '#username']:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.fill(email)
                    break
            except Exception:
                pass

        for sel in ['input[name="password"]', 'input[type="password"]', '#password']:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.fill(password)
                    break
            except Exception:
                pass

        try:
            page.locator(
                'button[type="submit"], button:has-text("Log in"), button:has-text("Sign in")'
            ).first.click(timeout=5000)
        except Exception:
            page.keyboard.press("Enter")

        log("等待登录完成...")
        page.wait_for_timeout(8000)
        cur_url = page.url
        log(f"当前URL: {cur_url[:80]}")

        if "login" in cur_url.lower():
            log("登录失败，仍在登录页")
            browser.close()
            print(json.dumps({"ok": False, "error": "登录失败"}))
            sys.exit(0)

        # ── 2. 邮箱未验证处理 ────────────────────────────────────────────────────
        page.wait_for_timeout(2000)
        try:
            verify_notice = page.locator(
                'text=/verify your email/i, text=/confirm your email/i, '
                '[data-testid="email-verification"]'
            ).first
            if verify_notice.is_visible(timeout=3000):
                log("检测到邮箱未验证提示，尝试重发验证邮件...")
                resend_btn = page.locator(
                    'button:has-text("Resend"), a:has-text("Resend"), button:has-text("Send again")'
                ).first
                if resend_btn.is_visible(timeout=3000):
                    resend_btn.click()
                    page.wait_for_timeout(5000)
                    if outlook_tok:
                        import subprocess, os
                        script = os.path.join(os.path.dirname(__file__), "click_verify_link.py")
                        r = subprocess.run(
                            ["python3", script,
                             json.dumps({"token": outlook_tok, "message_id": ""})],
                            capture_output=True, text=True, timeout=180
                        )
                        log(f"验证结果: {r.stdout[-200:]}")
                        page.wait_for_timeout(5000)
                        page.reload()
                        page.wait_for_timeout(3000)
        except Exception:
            pass

        # ── 3. Fork 源项目 ───────────────────────────────────────────────────────
        log(f"导航到源项目: {source_project}")
        page.goto(source_project, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        fork_clicked = False

        # 方式 A: 找并点击 Fork / Remix 按钮
        for fork_sel in [
            'button:has-text("Fork")',
            'button:has-text("Remix")',
            '[data-cy="fork-btn"]',
            '[data-testid="fork-btn"]',
            'a:has-text("Fork")',
            'a:has-text("Remix")',
        ]:
            try:
                btn = page.locator(fork_sel).first
                if btn.is_visible(timeout=4000):
                    btn.click()
                    fork_clicked = True
                    log(f"Fork 按钮已点击 ({fork_sel})")
                    break
            except Exception:
                pass

        # 方式 B: 直接访问 /fork URL
        if not fork_clicked:
            log("按钮未找到，尝试 /fork 端点...")
            page.goto(source_project.rstrip("/") + "/fork", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            fork_clicked = True

        # 确认 Fork 弹窗（如有）
        for confirm_sel in [
            'button:has-text("Fork it")',
            'button:has-text("Confirm")',
            'button:has-text("Create Fork")',
            'button:has-text("Fork")',
        ]:
            try:
                btn = page.locator(confirm_sel).first
                if btn.is_visible(timeout=5000):
                    btn.click()
                    log(f"确认 Fork 弹窗: {confirm_sel}")
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                pass

        # 等待跳转到新 fork 的 URL（不包含 skingsbp 的路径）
        log("等待 fork 完成（最多 60s）...")
        fork_done = False
        for i in range(30):
            page.wait_for_timeout(2000)
            cur = page.url
            log(f"  [{i}] URL={cur[:80]}")
            if "@" in cur and "/repl/" not in cur:
                # 已经跳转到 @username/repl-name 页面
                fork_done = True
                break
            if "/repl/" in cur or (".replit" in cur and "new" not in cur):
                fork_done = True
                break

        repl_url = page.url
        log(f"Fork 完成: {repl_url[:100]}")

        # 提取用户名
        username = ""
        try:
            username = (
                page.evaluate(
                    "() => window.__REPLIT_NEXT_DATA__?.user?.username "
                    "|| window.__USER__?.username || ''"
                )
                or ""
            )
        except Exception:
            pass
        if not username:
            m = re.search(r"replit\.com/@([^/?#]+)", repl_url)
            if m:
                username = m.group(1)
        log(f"账号用户名: {username}")

        # ── 4. 获取 webview URL（fork 后自动运行，不需要手动 Run）───────────────
        webview_url = ""
        try:
            webview_url = (
                page.locator(
                    'iframe[src*="repl.co"], iframe[src*="replit.dev"]'
                ).first.get_attribute("src") or ""
            )
        except Exception:
            pass
        if not webview_url and username:
            # 从项目 URL 推导 slug
            m = re.search(r"@[^/]+/([^/?#]+)", repl_url)
            slug = m.group(1) if m else "gh-cli-install"
            webview_url = f"https://{slug}--{username}.replit.dev"
        log(f"Webview URL: {webview_url}")

        # ── 5. 自动 Deploy（需付费账号）─────────────────────────────────────────
        deployed_url = ""
        if do_deploy:
            log("尝试自动 Deploy...")
            page.wait_for_timeout(5000)

            deploy_btns = [
                'button:has-text("Deploy")',
                'button:has-text("Publish")',
                '[data-cy="deploy-btn"]',
                '[data-testid="deploy-btn"]',
                'button[title*="Deploy" i]',
            ]
            deploy_clicked = False
            for sel in deploy_btns:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=4000):
                        btn.click()
                        log(f"Deploy 按钮已点击 ({sel})")
                        deploy_clicked = True
                        page.wait_for_timeout(3000)
                        break
                except Exception:
                    pass

            if deploy_clicked:
                # 确认/向导
                for confirm_sel in [
                    'button:has-text("Confirm")',
                    'button:has-text("Deploy now")',
                    'button:has-text("Next")',
                    'button:has-text("Continue")',
                ]:
                    try:
                        btn = page.locator(confirm_sel).first
                        if btn.is_visible(timeout=6000):
                            btn.click()
                            log(f"确认部署: {confirm_sel}")
                            page.wait_for_timeout(8000)
                            break
                    except Exception:
                        pass

                # 检测 "nothing to publish" 错误
                try:
                    ntpub = page.locator(
                        'text=/nothing to publish/i, text=/no deployable/i'
                    ).first
                    if ntpub.is_visible(timeout=5000):
                        log("WARN: 检测到 'nothing to publish' — artifact.toml 可能未同步")
                        # 这不应发生（fork 已含 kind=web），记录页面内容用于调试
                        log(f"页面摘要: {page.content()[:300]}")
                except Exception:
                    pass

                # 抓取 .replit.app URL
                try:
                    link_el = page.locator('a[href*=".replit.app"]').first
                    if link_el.is_visible(timeout=20000):
                        deployed_url = link_el.get_attribute("href") or ""
                        log(f"部署完成: {deployed_url}")
                except Exception:
                    pass

                if not deployed_url:
                    # 从 URL 推导已部署地址
                    m2 = re.search(r"@([^/]+)/([^/?#]+)", repl_url)
                    if m2:
                        u2, slug2 = m2.group(1), m2.group(2)
                        deployed_url = f"https://{slug2}--{u2}.replit.app"
                        log(f"推导部署URL: {deployed_url}")
            else:
                log("未找到 Deploy 按钮（账号可能不支持部署，或页面结构变化）")

        browser.close()

    result = {
        "ok": True,
        "repl_url": repl_url,
        "webview_url": webview_url,
        "deployed_url": deployed_url,
        "username": username,
        "forked_from": source_project,
    }
    log(f"完成: {json.dumps(result)}")
    print(json.dumps(result))

except Exception as e:
    import traceback
    print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()[-800:]}))
