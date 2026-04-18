#!/usr/bin/env python3
"""
replit_register.py — Replit 注册表单浏览器自动化（仅负责填表提交）
邮件验证由上层 click-verify-link 接口处理，不在此脚本内。

用法: python3 replit_register.py '<json>'
JSON 入参:
  email, username, password, proxy (socks5://...), user_agent?, headless?

输出 (最后一行 JSON):
  { "ok": bool, "phase": str, "error": str, "exit_ip": str }
"""
import sys, json, re, time, asyncio

params    = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
EMAIL     = params.get("email", "")
USERNAME  = params.get("username", "")
PASSWORD  = params.get("password", "")
PROXY     = params.get("proxy", "")        # socks5://127.0.0.1:10820
UA        = params.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADLESS  = params.get("headless", True)
MAX_WAIT  = params.get("max_wait", 90)     # 表单超时秒数

def log(msg): print(f"[replit_reg] {msg}", flush=True)

async def run() -> dict:
    result = {"ok": False, "phase": "init", "error": "", "exit_ip": ""}

    # playwright + stealth（经验证可通过 CF Turnstile）
    stealth_fn = None
    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
        stealth_fn = Stealth().apply_stealth_async
        log("使用 playwright + stealth")
    except ImportError:
        try:
            from patchright.async_api import async_playwright
            log("fallback: patchright")
        except ImportError:
            result["error"] = "playwright/patchright 未安装"
            return result

    proxy_cfg = {"server": PROXY} if PROXY else None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            proxy=proxy_cfg,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            user_agent=UA,
        )
        page = await ctx.new_page()

        if stealth_fn:
            try:
                await stealth_fn(page)
            except Exception as e:
                log(f"stealth 注入失败（忽略）: {e}")

        try:
            # ── 0. 获取出口 IP ───────────────────────────────────────────────
            result["phase"] = "get_exit_ip"
            try:
                await page.goto("https://api.ipify.org?format=json",
                                wait_until="domcontentloaded", timeout=15000)
                ip_data = json.loads(await page.locator("body").inner_text())
                result["exit_ip"] = ip_data.get("ip", "")
                log(f"出口 IP: {result['exit_ip']}")
            except Exception:
                log("获取出口 IP 失败（继续）")

            # ── 1. 打开注册页 ────────────────────────────────────────────────
            result["phase"] = "navigate"
            log("打开 replit.com/signup …")
            await page.goto("https://replit.com/signup", wait_until="load", timeout=60000)
            await page.wait_for_timeout(3000)

            body = await page.locator("body").inner_text()
            if "failed to evaluate" in body.lower() or "browser integrity" in body.lower():
                result["error"] = "integrity_check_failed_on_load"
                await browser.close()
                return result
            log("页面加载完成，无 integrity 错误")
            # CF hard IP ban → 立即放弃换端口
            _title_init = await page.title()
            _body_init  = (await page.locator("body").inner_text())[:300]
            if "attention required" in _title_init.lower() or "have been blocked" in _body_init.lower():
                result["error"] = "signup_cf_ip_banned"
                await browser.close()
                return result

            # Wait for Cloudflare Turnstile to auto-solve (up to 30s)
            for _tw in range(15):
                _t = await page.title()
                _b = await page.locator("body").inner_text()
                if "just a moment" not in _t.lower() and "cf-turnstile" not in _b:
                    break
                log(f"CF Turnstile waiting ({_tw+1}/15)...")
                await page.wait_for_timeout(2000)
            else:
                result["error"] = "signup_turnstile_unsolved"
                await browser.close()
                return result
            # Wait for actual form inputs
            try:
                await page.wait_for_selector("input:not([type=hidden])", timeout=10000)
            except Exception:
                result["error"] = "signup_form_input_missing"
                await browser.close()
                return result
            log("Turnstile passed, form ready")


            # ── 2. 点 "Email & password" ─────────────────────────────────────
            result["phase"] = "click_email_btn"
            for sel in [
                'button:has-text("Email")',
                'button:has-text("Continue with email")',
                '[data-cy="email-signup"]',
                'a:has-text("Email")',
                'button:has-text("Email & password")',
            ]:
                btn = page.locator(sel)
                if await btn.count():
                    await btn.first.click()
                    log(f"已点击: {sel}")
                    await page.wait_for_timeout(2000)
                    break
            else:
                log("未找到 Email 按钮，继续尝试直接填表")

            # ── 3. 检查 integrity（按钮点击后）─────────────────────────────
            body2 = await page.locator("body").inner_text()
            if "failed to evaluate" in body2.lower() or "browser integrity" in body2.lower():
                result["error"] = "integrity_check_failed_after_click"
                await browser.close()
                return result

            # ── 4. 填写表单 ─────────────────────────────────────────────────
            result["phase"] = "fill_form"
            log(f"填表: user={USERNAME} email={EMAIL}")

            for sel in ['input[name="username"]', 'input[placeholder*="username" i]', '#username']:
                f = page.locator(sel)
                if await f.count():
                    await f.first.fill(USERNAME)
                    await page.wait_for_timeout(400)
                    break

            await page.wait_for_timeout(600)

            for sel in ['input[type="email"]', 'input[name="email"]', 'input[placeholder*="email" i]']:
                f = page.locator(sel)
                if await f.count():
                    await f.first.fill(EMAIL)
                    await page.wait_for_timeout(400)
                    break

            await page.wait_for_timeout(500)

            for sel in ['input[type="password"]', 'input[name="password"]']:
                f = page.locator(sel)
                if await f.count():
                    await f.first.fill(PASSWORD)
                    await page.wait_for_timeout(400)
                    break

            await page.wait_for_timeout(1500)
            await page.screenshot(path=f"/tmp/replit_form_{USERNAME}.png")
            log("表单截图已保存")

            # ── 5. 提交 ─────────────────────────────────────────────────────
            result["phase"] = "submit"
            clicked = False
            for sel in [
                'button[type="submit"]',
                'button:has-text("Create Account")',
                'button:has-text("Sign up")',
                'button:has-text("Continue")',
            ]:
                btn = page.locator(sel)
                if await btn.count():
                    await btn.first.click()
                    clicked = True
                    log(f"已点击提交: {sel}")
                    break
            if not clicked:
                await page.keyboard.press("Enter")
                log("回车提交")

            await page.wait_for_timeout(6000)
            cur_url = page.url
            log(f"提交后 URL: {cur_url[:80]}")

            # integrity 再检查
            body3 = await page.locator("body").inner_text()
            if "failed to evaluate" in body3.lower() or "browser integrity" in body3.lower():
                result["error"] = "integrity_check_failed_after_submit"
                await browser.close()
                return result

            await page.screenshot(path=f"/tmp/replit_after_{USERNAME}.png")

            # 判断是否跳转到验证等待页
            if any(x in cur_url.lower() for x in ("verify", "confirm", "check-email", "dashboard", "home", "@")):
                log(f"✅ 注册成功，进入验证阶段: {cur_url[:60]}")
                result["ok"]    = True
                result["phase"] = "email_verify_pending"
            elif "signup" in cur_url.lower():
                # 可能有 form 错误，截图检查
                err_els = await page.locator('[class*="error"],[class*="Error"],[data-cy*="error"]').all_text_contents()
                errs = [e.strip() for e in err_els if e.strip()]
                if errs:
                    result["error"] = "; ".join(errs[:3])
                    log(f"表单错误: {result['error']}")
                else:
                    # 仍在 signup 页但无错误 — 可能 JS 慢，等一下
                    await page.wait_for_timeout(5000)
                    cur_url2 = page.url
                    if "signup" not in cur_url2.lower():
                        result["ok"]    = True
                        result["phase"] = "email_verify_pending"
                        log(f"✅ 延迟跳转成功: {cur_url2[:60]}")
                    else:
                        result["error"] = f"仍在 signup 页 (可能提交失败或需人工验证)"
                        log(result["error"])
            else:
                result["ok"]    = True
                result["phase"] = "email_verify_pending"
                log(f"✅ 页面已跳转: {cur_url[:60]}")

        except Exception as exc:
            result["error"] = str(exc)
            log(f"异常: {exc}")
            try:
                await page.screenshot(path=f"/tmp/replit_error_{USERNAME}.png")
            except Exception:
                pass

        await browser.close()

    return result

if __name__ == "__main__":
    res = asyncio.run(run())
    print(json.dumps(res))
