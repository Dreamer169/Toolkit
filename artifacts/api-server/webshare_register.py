#!/usr/bin/env python3
"""
Webshare.io 自动注册脚本 - 用 patchright 浏览器填写注册表单
用法: python3 webshare_register.py --email user@outlook.com --password Pass123!
输出: JSON -> {success, email, password, api_key, plan, error, elapsed}
"""
import argparse, asyncio, json, time, re

def log(msg: str):
    print(msg, flush=True)


async def register_webshare(email: str, password: str, proxy: str = "", headless: bool = True) -> dict:
    t0 = time.time()
    result = {
        "success": False, "email": email, "password": password,
        "api_key": "", "plan": "free", "error": "", "elapsed": ""
    }
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            result["error"] = "patchright/playwright not installed"
            result["elapsed"] = f"{time.time()-t0:.1f}s"
            return result

    log(f"📧 目标邮箱: {email}")
    log(f"🔒 密码: {password[:3]}***")
    if proxy:
        log(f"🌐 代理: {proxy[:50]}")
    log("🚀 启动浏览器...")

    async with async_playwright() as p:
        browser = None
        try:
            launch_opts = {
                "headless": headless,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
                "executable_path": "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
            }
            browser = await p.chromium.launch(**launch_opts)

            ctx_opts: dict = {
                "viewport": {"width": 1366, "height": 768},
                "locale": "en-US",
                "timezone_id": "America/New_York",
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            }
            if proxy:
                ctx_opts["proxy"] = {"server": proxy}

            ctx = await browser.new_context(**ctx_opts)
            await ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                window.chrome = {runtime: {}};
            """)
            page = await ctx.new_page()

            log("🌐 打开 webshare.io 注册页...")
            await page.goto(
                "https://dashboard.webshare.io/register?source=home_hero_button_register",
                wait_until="networkidle",
                timeout=60_000,
            )
            log("✅ 页面已加载")

            # 等待邮箱输入框
            email_sel = "input[name='email'], input[type='email']"
            await page.wait_for_selector(email_sel, timeout=15_000)
            log("📝 填写注册表单...")

            # 填邮箱
            await page.fill(email_sel, "")
            await page.type(email_sel, email, delay=80)
            log(f"  ✓ 邮箱: {email}")

            # 填密码（可能有两个密码字段）
            pwd_inputs = await page.query_selector_all("input[type='password']")
            if len(pwd_inputs) >= 2:
                await pwd_inputs[0].fill("")
                await pwd_inputs[0].type(password, delay=70)
                await asyncio.sleep(0.3)
                await pwd_inputs[1].fill("")
                await pwd_inputs[1].type(password, delay=70)
                log("  ✓ 密码已填写（两次确认）")
            elif len(pwd_inputs) == 1:
                await pwd_inputs[0].fill("")
                await pwd_inputs[0].type(password, delay=70)
                log("  ✓ 密码已填写")
            else:
                log("  ⚠️ 未找到密码字段，尝试 name 选择器")
                await page.fill("input[name='password']", password)

            await asyncio.sleep(1)

            # ToS 复选框（如果存在）
            tos_box = await page.query_selector("input[type='checkbox']")
            if tos_box:
                checked = await tos_box.get_attribute("checked")
                if not checked:
                    await tos_box.click()
                    log("  ✓ 已勾选服务条款")

            log("⏳ 等待 reCAPTCHA 加载 (5s)...")
            await asyncio.sleep(5)

            # 尝试点击 reCAPTCHA 复选框（通过 iframe frames）
            try:
                for frame in page.frames:
                    if "recaptcha" in frame.url or "google.com/recaptcha" in frame.url:
                        chk = frame.locator("#recaptcha-anchor")
                        if await chk.is_visible(timeout=3_000):
                            await chk.click()
                            log("  ✓ 点击 reCAPTCHA 复选框")
                            await asyncio.sleep(4)
                        break
            except Exception as ce:
                log(f"  ℹ️ reCAPTCHA 自动处理: {ce}")

            await asyncio.sleep(2)

            log("🖱️ 点击注册按钮...")
            submit_btn = None
            for sel in [
                "button[type='submit']",
                "button:has-text('Register')",
                "button:has-text('Sign up')",
                "button:has-text('Create account')",
                "form button",
            ]:
                try:
                    b = await page.query_selector(sel)
                    if b:
                        submit_btn = b
                        break
                except Exception:
                    continue

            if not submit_btn:
                result["error"] = "未找到提交按钮"
                result["elapsed"] = f"{time.time()-t0:.1f}s"
                return result

            await submit_btn.click()
            log("⏳ 等待注册结果 (最多 35s)...")

            try:
                await page.wait_for_url(
                    re.compile(r"dashboard\.webshare\.io(?!/register)"),
                    timeout=35_000,
                )
                log("✅ 注册成功！已跳转到 dashboard")
                result["success"] = True
            except Exception:
                await asyncio.sleep(3)
                cur_url = page.url
                log(f"  当前URL: {cur_url}")

                if "/register" not in cur_url:
                    result["success"] = True
                    log("✅ 注册成功（URL 已变化）")
                else:
                    body_text = ""
                    try:
                        body_text = await page.inner_text("body")
                    except Exception:
                        pass
                    bl = body_text.lower()
                    if "already" in bl or "exists" in bl:
                        result["error"] = "邮箱已被注册"
                    elif "captcha" in bl or "robot" in bl:
                        result["error"] = "reCAPTCHA 未通过，建议使用代理或 headless=false 重试"
                    elif "suspicious" in bl:
                        result["error"] = "邮箱被 webshare 视为可疑，请重新生成 Outlook 账号"
                    else:
                        ss_path = f"/tmp/webshare_fail_{int(time.time())}.png"
                        try:
                            await page.screenshot(path=ss_path)
                        except Exception:
                            pass
                        result["error"] = f"注册超时或失败，截图已保存: {ss_path}"

            # 尝试获取 API Key（注册成功后）
            if result["success"]:
                await asyncio.sleep(2)
                log("🔑 获取 Webshare API Key...")
                try:
                    api_resp = await ctx.request.get(
                        "https://proxy.webshare.io/api/v2/api-key/",
                        headers={"Content-Type": "application/json"},
                    )
                    if api_resp.ok:
                        api_data = await api_resp.json()
                        if isinstance(api_data, dict) and api_data.get("api_key"):
                            result["api_key"] = api_data["api_key"]
                            log(f"  ✅ API Key 获取成功: {result['api_key'][:16]}...")
                except Exception as ae:
                    log(f"  ℹ️ API Key 直接获取失败: {ae}")

                # 备用：从 localStorage
                if not result["api_key"]:
                    try:
                        token = await page.evaluate(
                            "() => localStorage.getItem('token') || localStorage.getItem('api_key') || ''"
                        )
                        if token:
                            result["api_key"] = token
                            log(f"  ✅ API Key (localStorage): {token[:16]}...")
                    except Exception:
                        pass

                log(f"✅ 完成! 邮箱: {email} | 计划: free")

        except Exception as e:
            result["error"] = str(e)
            log(f"❌ 错误: {e}")
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass

    result["elapsed"] = f"{time.time()-t0:.1f}s"
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Webshare.io 自动注册")
    ap.add_argument("--email",    required=True,  help="Outlook 邮箱")
    ap.add_argument("--password", required=True,  help="Outlook 密码")
    ap.add_argument("--proxy",    default="",     help="SOCKS5/HTTP 代理")
    ap.add_argument("--headless", default="true", help="无头模式 true/false")
    args = ap.parse_args()

    headless = args.headless.lower() not in ("false", "0", "no")
    r = asyncio.run(register_webshare(args.email, args.password, args.proxy, headless))
    print("\n── JSON 结果 ──")
    print(json.dumps(r, ensure_ascii=False, indent=2))
