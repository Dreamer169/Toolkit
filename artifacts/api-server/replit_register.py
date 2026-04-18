#!/usr/bin/env python3
"""
replit_register.py — Replit 注册表单浏览器自动化（仅负责填表提交）
邮件验证由上层 click-verify-link 接口处理，不在此脚本内。

用法: python3 replit_register.py '<json>'
JSON 入参:
  email, username, password, proxy (socks5://...), user_agent?, headless?

输出 (最后一行 JSON):
  { "ok": bool, "phase": str, "error": str, "exit_ip": str }

BUG-FIX v3 (实测验证):
  - patchright 优先（比 playwright-stealth 更稳定通过 CF）
  - CF IP 封禁（Attention Required）在 Turnstile 循环内快速识别
  - page.content() 检测 cf-turnstile（inner_text 只含文本，不含 HTML attr）
  - patchright 12s 内未解 Turnstile → 判断为 IP 封禁，返回 signup_cf_ip_banned
    (patchright 对正常 IP < 5s 自动解题；卡住 12s 说明 IP 被 CF WAF 封禁)
  - 先点击 "Email & password" 按钮，再等待输入框出现
    (Replit 注册页先显示 OAuth 按钮，不直接展示邮箱/密码表单)
  - IP 封禁立即返回 signup_cf_ip_banned（accounts.ts 遇此错误换端口重试）
  - 每次失败耗时: ~15s (v1: 35s, v2: 24s, v3: 15s)
"""
import sys, json, asyncio

params    = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
EMAIL     = params.get("email", "")
USERNAME  = params.get("username", "")
PASSWORD  = params.get("password", "")
PROXY     = params.get("proxy", "")
UA        = params.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADLESS  = params.get("headless", True)
MAX_WAIT  = params.get("max_wait", 90)

def log(msg): print(f"[replit_reg] {msg}", flush=True)

def is_cf_blocked(title: str, body_text: str) -> bool:
    t = title.lower()
    b = body_text.lower()
    return (
        "attention required" in t or
        "have been blocked" in b or
        "sorry, you have been blocked" in b or
        "you are unable to access" in b or
        "error 1020" in b or
        "error 1010" in b
    )

async def run() -> dict:
    result = {"ok": False, "phase": "init", "error": "", "exit_ip": ""}

    # ── 优先 patchright（CF bypass 最佳），playwright-stealth 为备选 ─────────
    stealth_fn = None
    use_patchright = False
    try:
        from patchright.async_api import async_playwright
        use_patchright = True
        log("使用 patchright（优先）")
    except ImportError:
        try:
            from playwright.async_api import async_playwright
            try:
                from playwright_stealth import Stealth
                stealth_fn = Stealth().apply_stealth_async
                log("fallback: playwright + stealth")
            except ImportError:
                log("fallback: playwright（无 stealth）")
        except ImportError:
            result["error"] = "playwright/patchright 未安装"
            return result

    proxy_cfg = {"server": PROXY} if PROXY else None

    async with async_playwright() as pw:
        launch_args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                       "--disable-blink-features=AutomationControlled"]
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            proxy=proxy_cfg,
            args=launch_args,
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
            # ── 0. 获取出口 IP ────────────────────────────────────────────────
            result["phase"] = "get_exit_ip"
            try:
                await page.goto("https://api.ipify.org?format=json",
                                wait_until="domcontentloaded", timeout=12000)
                ip_data = json.loads(await page.locator("body").inner_text())
                result["exit_ip"] = ip_data.get("ip", "")
                log(f"出口 IP: {result['exit_ip']}")
            except Exception:
                log("获取出口 IP 失败（继续）")

            # ── 1. 打开注册页 ─────────────────────────────────────────────────
            result["phase"] = "navigate"
            log("打开 replit.com/signup …")
            await page.goto("https://replit.com/signup",
                            wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2000)

            # ── 2. 快速 CF 封禁检测（在 Turnstile 等待之前）─────────────────
            _title_init = await page.title()
            _body_init  = (await page.locator("body").inner_text())[:500]
            if is_cf_blocked(_title_init, _body_init):
                log(f"CF IP 封禁（立即）: {_title_init}")
                result["error"] = "signup_cf_ip_banned"
                await browser.close()
                return result

            # integrity 检查
            if "failed to evaluate" in _body_init.lower() or "browser integrity" in _body_init.lower():
                result["error"] = "integrity_check_failed_on_load"
                await browser.close()
                return result

            log(f"初始标题: {_title_init} — 继续等待 CF 验证通过…")

            # ── 3. 等待 CF Turnstile 自动解除 ────────────────────────────────
            # patchright 正常 < 5s 自动解 Turnstile；卡住 >12s 说明 IP 被封
            # playwright-stealth 无自动解题能力，等满 20s 超时
            # page.content() 检测 cf-turnstile（inner_text 不含 HTML 属性）
            MAX_ITERS = 6 if use_patchright else 10   # 12s / 20s
            for _tw in range(MAX_ITERS):
                _t    = await page.title()
                _html = await page.content()
                _body = (await page.locator("body").inner_text())[:400]

                # CF 封禁可能在 Turnstile 循环中才出现（"Attention Required"）
                if is_cf_blocked(_t, _body):
                    log(f"CF IP 封禁（Turnstile 循环中）: {_t}")
                    result["error"] = "signup_cf_ip_banned"
                    await browser.close()
                    return result

                still_turnstile = (
                    "just a moment" in _t.lower() or
                    "cf-turnstile" in _html or
                    "challenge" in _t.lower()
                )
                if not still_turnstile:
                    log(f"Turnstile 已通过，标题: {_t}")
                    break
                log(f"CF Turnstile waiting ({_tw+1}/{MAX_ITERS}) title={_t!r}…")
                await page.wait_for_timeout(2000)
            else:
                # 超时：patchright 未能解题 → 判断为 IP 封禁
                #       playwright-stealth 未能解题 → 真正的 Turnstile 超时
                _final_t = await page.title()
                _final_b = (await page.locator("body").inner_text())[:400]
                if is_cf_blocked(_final_t, _final_b):
                    result["error"] = "signup_cf_ip_banned"
                elif use_patchright:
                    # patchright 12s 未解 = IP 被 CF 封锁（挑战永不通过）
                    result["error"] = "signup_cf_ip_banned"
                    log(f"patchright 12s 未解 Turnstile → 判断为 IP 封禁 (title={_final_t!r})")
                else:
                    result["error"] = "signup_turnstile_unsolved"
                log(f"Turnstile 超时: {result['error']}")
                await browser.close()
                return result

            # ── 4. 等待页面渲染完成（登录选项页/OAuth 按钮） ─────────────────
            # Replit 注册页先显示 OAuth 按钮，不直接显示输入框
            # 必须先点击 "Email & password" 按钮才会展示邮箱/密码表单
            await page.wait_for_timeout(2000)
            _t_page = await page.title()
            log(f"注册页已就绪，标题: {_t_page}")
            if is_cf_blocked(_t_page, (await page.locator("body").inner_text())[:200]):
                result["error"] = "signup_cf_ip_banned"
                await browser.close()
                return result

            # ── 5. 点 "Email & password" 按钮（展开邮箱表单） ───────────────
            result["phase"] = "click_email_btn"
            EMAIL_BTN_SELS = [
                'button:has-text("Email & password")',
                'button:has-text("Continue with email")',
                'button:has-text("Use email")',
                'button:has-text("Email")',
                '[data-cy="email-signup"]',
                'a:has-text("Email")',
                'button[type="button"]:has-text("email" i)',
            ]
            for sel in EMAIL_BTN_SELS:
                btn = page.locator(sel)
                if await btn.count():
                    await btn.first.click()
                    log(f"已点击 Email 按钮: {sel}")
                    await page.wait_for_timeout(1500)
                    break
            else:
                log("未找到独立 Email 按钮 → 表单可能已直接展示")

            # ── 等待表单输入框出现（点击 Email 按钮后） ─────────────────────
            try:
                await page.wait_for_selector("input:not([type=hidden])", timeout=8000)
                log("输入框已就绪")
            except Exception:
                _t2 = await page.title()
                _b2 = (await page.locator("body").inner_text())[:300]
                if is_cf_blocked(_t2, _b2):
                    result["error"] = "signup_cf_ip_banned"
                else:
                    result["error"] = "signup_form_input_missing"
                    log(f"输入框未找到，页面标题: {_t2!r}")
                    await page.screenshot(path=f"/tmp/replit_no_form_{USERNAME}.png")
                await browser.close()
                return result

            # integrity 再检查
            _body2 = (await page.locator("body").inner_text())[:300]
            if "failed to evaluate" in _body2.lower() or "browser integrity" in _body2.lower():
                result["error"] = "integrity_check_failed_after_click"
                await browser.close()
                return result

            # ── 6. 填写表单 ──────────────────────────────────────────────────
            result["phase"] = "fill_form"
            log(f"填表: user={USERNAME} email={EMAIL}")

            for sel in ['input[name="username"]', 'input[placeholder*="username" i]', '#username']:
                f = page.locator(sel)
                if await f.count():
                    await f.first.fill(USERNAME)
                    await page.wait_for_timeout(400)
                    break

            await page.wait_for_timeout(500)

            for sel in ['input[type="email"]', 'input[name="email"]', 'input[placeholder*="email" i]']:
                f = page.locator(sel)
                if await f.count():
                    await f.first.fill(EMAIL)
                    await page.wait_for_timeout(400)
                    break

            await page.wait_for_timeout(400)

            for sel in ['input[type="password"]', 'input[name="password"]']:
                f = page.locator(sel)
                if await f.count():
                    await f.first.fill(PASSWORD)
                    await page.wait_for_timeout(400)
                    break

            await page.wait_for_timeout(1200)
            await page.screenshot(path=f"/tmp/replit_form_{USERNAME}.png")
            log("表单截图已保存")

            # ── 7. 提交 ──────────────────────────────────────────────────────
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
            body3 = (await page.locator("body").inner_text())[:300]
            if "failed to evaluate" in body3.lower() or "browser integrity" in body3.lower():
                result["error"] = "integrity_check_failed_after_submit"
                await browser.close()
                return result

            await page.screenshot(path=f"/tmp/replit_after_{USERNAME}.png")

            # ── 8. 判断注册结果 ──────────────────────────────────────────────
            if any(x in cur_url.lower() for x in
                   ("verify", "confirm", "check-email", "dashboard", "home", "@")):
                log(f"✅ 注册成功，进入验证阶段: {cur_url[:60]}")
                result["ok"]    = True
                result["phase"] = "email_verify_pending"
            elif "signup" in cur_url.lower():
                err_els = await page.locator('[class*="error"],[class*="Error"],[data-cy*="error"]').all_text_contents()
                errs = [e.strip() for e in err_els if e.strip()]
                if errs:
                    result["error"] = "; ".join(errs[:3])
                    log(f"表单错误: {result['error']}")
                else:
                    await page.wait_for_timeout(5000)
                    cur_url2 = page.url
                    if "signup" not in cur_url2.lower():
                        result["ok"]    = True
                        result["phase"] = "email_verify_pending"
                        log(f"✅ 延迟跳转成功: {cur_url2[:60]}")
                    else:
                        result["error"] = "signup_still_on_form_no_redirect"
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
