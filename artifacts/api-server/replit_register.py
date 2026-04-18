#!/usr/bin/env python3
"""
replit_register.py — Replit 注册表单浏览器自动化
BUG-FIX v5.1:
  - playwright+stealth 优先（绕 integrity check）; patchright 备用
  - Turnstile 等待统一 12s（6 iter × 2s），超时 → signup_cf_ip_banned（立即换端口）
  - 两步表单：Step1 email(type=text)+password → Step2 username
  - exit_ip 全程保留（外层获取，内层只读）
  - captcha_token_invalid：内部 reload 重试 1 次（不新建 browser）
  - integrity 失败：外层最多 3 次新 browser 重试
"""
import sys, json, asyncio

params   = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
EMAIL    = params.get("email", "")
USERNAME = params.get("username", "")
PASSWORD = params.get("password", "")
PROXY    = params.get("proxy", "")
UA       = params.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADLESS = params.get("headless", True)

def log(msg): print(f"[replit_reg] {msg}", flush=True)

def is_cf_blocked(title: str, body: str) -> bool:
    t, b = title.lower(), body.lower()
    return (
        "attention required" in t or "have been blocked" in b or
        "sorry, you have been blocked" in b or "you are unable to access" in b or
        "error 1020" in b or "error 1010" in b
    )

def is_integrity_error(body: str) -> bool:
    b = body.lower()
    return "failed to evaluate" in b or "browser integrity" in b or "integrity check" in b

def is_captcha_invalid(text: str) -> bool:
    t = text.lower()
    return "captcha token is invalid" in t or "captcha" in t and "invalid" in t

async def get_exit_ip(pw_module, proxy_cfg) -> str:
    try:
        browser = await pw_module.chromium.launch(
            headless=HEADLESS, proxy=proxy_cfg,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        ctx  = await browser.new_context(viewport={"width": 800, "height": 600})
        page = await ctx.new_page()
        await page.goto("https://api.ipify.org?format=json",
                        wait_until="domcontentloaded", timeout=10000)
        ip = json.loads(await page.locator("body").inner_text()).get("ip", "")
        await browser.close()
        return ip
    except Exception as e:
        log(f"获取出口 IP 失败: {e}")
        return ""

async def wait_cf(page, use_patchright: bool) -> str | None:
    """
    等待 CF Turnstile。统一 6 iter × 2s = 12s。
    超时 → 一律返回 signup_cf_ip_banned（立即换端口）。
    """
    for i in range(6):
        title = await page.title()
        html  = await page.content()
        body  = (await page.locator("body").inner_text())[:500]

        if is_cf_blocked(title, body):
            log(f"CF 封禁 (iter {i+1}): {title}")
            return "signup_cf_ip_banned"

        still = (
            "just a moment" in title.lower() or
            "cf-turnstile" in html or
            "challenge" in title.lower()
        )
        if not still:
            log(f"CF Turnstile 通过，标题: {title}")
            return None

        log(f"CF waiting ({i+1}/6) title={title!r}")
        await page.wait_for_timeout(2000)

    # 超时 → 封禁
    title = await page.title()
    body  = (await page.locator("body").inner_text())[:200]
    if is_cf_blocked(title, body):
        return "signup_cf_ip_banned"
    log(f"Turnstile 12s 超时 (use_patchright={use_patchright}) title={title!r} → signup_cf_ip_banned")
    return "signup_cf_ip_banned"

async def fill_step1(page) -> str | None:
    """填写 Step1（email+password）并提交。返回错误字符串或 None。"""
    # email 字段：Replit 实际为 type=text name=email
    for sel in ['input[name="email"]', 'input[type="email"]', 'input[placeholder*="email" i]']:
        f = page.locator(sel)
        if await f.count():
            await f.first.click()
            await f.first.fill(EMAIL)
            await page.wait_for_timeout(300)
            log(f"已填 email via {sel}")
            break
    else:
        return "signup_email_field_not_found"

    for sel in ['input[type="password"]', 'input[name="password"]']:
        f = page.locator(sel)
        if await f.count():
            await f.first.click()
            await f.first.fill(PASSWORD)
            await page.wait_for_timeout(300)
            log("已填 password")
            break

    await page.wait_for_timeout(600)
    await page.screenshot(path=f"/tmp/replit_step1_{USERNAME}.png")

    # 提交 Step1
    for sel in [
        'button[type="submit"]', 'button:has-text("Continue")',
        'button:has-text("Next")', 'button:has-text("Sign up")',
        'button:has-text("Create")',
    ]:
        btn = page.locator(sel)
        if await btn.count():
            await btn.first.click()
            log(f"Step1 提交: {sel}")
            break
    else:
        await page.keyboard.press("Enter")
        log("Step1 回车提交")

    await page.wait_for_timeout(3000)

    # 检查 Step1 错误
    body = (await page.locator("body").inner_text())[:300]
    if is_integrity_error(body):
        return "integrity_check_failed_after_step1"
    if is_captcha_invalid(body):
        return "captcha_token_invalid"

    if "signup" in page.url.lower():
        err_els = await page.locator(
            '[class*="error" i],[data-cy*="error"],[role="alert"]'
        ).all_text_contents()
        errs = [e.strip() for e in err_els if e.strip()]
        if errs:
            return "; ".join(errs[:3])

    return None

async def fill_step2(page) -> str | None:
    """填写 Step2（username）并提交。返回错误字符串或 None。"""
    for sel in ['input[name="username"]', 'input[placeholder*="username" i]', '#username']:
        f = page.locator(sel)
        if await f.count():
            await f.first.click()
            await f.first.fill(USERNAME)
            await page.wait_for_timeout(400)
            log(f"已填 username via {sel}")
            break
    else:
        return "signup_username_field_not_found"

    await page.wait_for_timeout(600)
    await page.screenshot(path=f"/tmp/replit_step2_{USERNAME}.png")

    for sel in [
        'button:has-text("Create Account")', 'button:has-text("Create account")',
        'button[type="submit"]', 'button:has-text("Sign up")',
        'button:has-text("Finish")', 'button:has-text("Continue")',
    ]:
        btn = page.locator(sel)
        if await btn.count():
            await btn.first.click()
            log(f"Step2 提交: {sel}")
            break
    else:
        await page.keyboard.press("Enter")
        log("Step2 回车提交")

    await page.wait_for_timeout(6000)

    body = (await page.locator("body").inner_text())[:300]
    if is_integrity_error(body):
        return "integrity_check_failed_after_step2"
    if is_captcha_invalid(body):
        return "captcha_token_invalid"

    cur_url = page.url
    log(f"提交后 URL: {cur_url[:80]}")
    await page.screenshot(path=f"/tmp/replit_after_{USERNAME}.png")

    SUCCESS_URLS = ("verify", "confirm", "check-email", "dashboard", "home", "@")
    if any(x in cur_url.lower() for x in SUCCESS_URLS):
        return None  # 成功

    if "signup" in cur_url.lower():
        err_els = await page.locator(
            '[class*="error" i],[data-cy*="error"],[role="alert"]'
        ).all_text_contents()
        errs = [e.strip() for e in err_els if e.strip()]
        if errs:
            return "; ".join(errs[:3])
        # 延迟跳转
        await page.wait_for_timeout(4000)
        if "signup" not in page.url.lower():
            return None  # 成功
        return "signup_still_on_form_no_redirect"

    return None  # 其他 URL → 成功

async def attempt_register(pw_module, proxy_cfg, use_patchright: bool, stealth_fn, exit_ip: str) -> dict:
    result = {"ok": False, "phase": "init", "error": "", "exit_ip": exit_ip}

    browser = await pw_module.chromium.launch(
        headless=HEADLESS, proxy=proxy_cfg,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
              "--disable-blink-features=AutomationControlled", "--disable-web-security"],
    )
    ctx  = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-US", user_agent=UA)
    page = await ctx.new_page()

    if stealth_fn:
        try:
            await stealth_fn(page)
        except Exception as e:
            log(f"stealth 注入失败: {e}")

    try:
        # 1. 导航
        result["phase"] = "navigate"
        log("打开 replit.com/signup …")
        await page.goto("https://replit.com/signup", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1200)

        # 2. 立即 CF 检测
        t0 = await page.title()
        b0 = (await page.locator("body").inner_text())[:400]
        if is_cf_blocked(t0, b0):
            result["error"] = "signup_cf_ip_banned"
            await browser.close(); return result
        if is_integrity_error(b0):
            result["error"] = "integrity_check_failed_on_load"
            await browser.close(); return result

        # 3. 等待 Turnstile（统一 12s）
        cf_err = await wait_cf(page, use_patchright)
        if cf_err:
            result["error"] = cf_err
            await browser.close(); return result

        # 4. 点击 "Email & password" 按钮
        result["phase"] = "click_email_btn"
        await page.wait_for_timeout(800)
        for sel in [
            'button:has-text("Email & password")', 'button:has-text("Continue with email")',
            'button:has-text("Use email")', 'button:has-text("Email")',
            '[data-cy="email-signup"]',
        ]:
            btn = page.locator(sel)
            if await btn.count():
                await btn.first.click()
                log(f"已点击 Email 按钮: {sel}")
                await page.wait_for_timeout(1200)
                break
        else:
            log("未找到 Email 按钮 → 表单可能已直接展示")

        # 5. 等待 Step1 表单
        result["phase"] = "step1_wait"
        try:
            await page.wait_for_selector(
                'input[name="email"], input[type="email"], input[placeholder*="email" i]',
                timeout=8000
            )
        except Exception:
            t2 = await page.title()
            b2 = (await page.locator("body").inner_text())[:300]
            if is_cf_blocked(t2, b2):
                result["error"] = "signup_cf_ip_banned"
            else:
                result["error"] = "signup_form_input_missing"
                await page.screenshot(path=f"/tmp/replit_no_form_{USERNAME}.png")
            await browser.close(); return result

        b1 = (await page.locator("body").inner_text())[:200]
        if is_integrity_error(b1):
            result["error"] = "integrity_check_failed_after_click"
            await browser.close(); return result

        # 6. 填写 + 提交 Step1（captcha_token_invalid → reload重试1次）
        result["phase"] = "step1_fill"
        step1_err = await fill_step1(page)
        if step1_err == "captcha_token_invalid":
            log("captcha token invalid → 立即返回换端口（无reload，快速切换）")
            result["error"] = "captcha_token_invalid"
            await browser.close(); return result

        if step1_err:
            result["error"] = step1_err
            await browser.close(); return result

        # 7. 等待 Step2 username 字段
        result["phase"] = "step2_wait"
        try:
            await page.wait_for_selector(
                'input[name="username"], input[placeholder*="username" i], #username',
                timeout=12000
            )
            log("Step2 username 字段已出现")
        except Exception:
            cur = page.url
            # 检查 URL 包含成功/验证关键词
            SUCCESS_URL_HINTS = ("verify", "confirm", "dashboard", "home", "@", "check-email", "check_email", "email", "account")
            if any(x in cur.lower() for x in SUCCESS_URL_HINTS) and "signup" not in cur.lower():
                result["ok"] = True; result["phase"] = "email_verify_pending"
                log(f"✅ 无 Step2，URL 跳转成功: {cur[:80]}")
                await browser.close(); return result
            # 检查 body 是否有邮件已发送提示（even if URL still has 'signup'）
            try:
                body_chk = (await page.locator("body").inner_text())[:500].lower()
                email_sent_hints = ("check your email", "we sent", "verify your email",
                                    "verification email", "sent you", "check for an email",
                                    "sent an email", "confirm your email")
                if any(hint in body_chk for hint in email_sent_hints):
                    result["ok"] = True; result["phase"] = "email_verify_pending"
                    log(f"✅ 无 Step2，body 检测邮件已发送: {cur[:60]}")
                    await browser.close(); return result
            except Exception:
                pass
            await page.screenshot(path=f"/tmp/replit_step2_missing_{USERNAME}.png")
            log(f"step2 缺失，URL={cur[:80]}")
            result["error"] = "signup_username_field_missing"
            await browser.close(); return result

        b4 = (await page.locator("body").inner_text())[:200]
        if is_integrity_error(b4):
            result["error"] = "integrity_check_failed_at_step2"
            await browser.close(); return result

        # 8. 填写 + 提交 Step2
        result["phase"] = "step2_fill"
        step2_err = await fill_step2(page)
        if step2_err:
            result["error"] = step2_err
            await browser.close(); return result

        result["ok"] = True
        result["phase"] = "email_verify_pending"
        log(f"✅ 注册完成，等待邮件验证")

    except Exception as exc:
        result["error"] = str(exc)
        log(f"异常: {exc}")
        try: await page.screenshot(path=f"/tmp/replit_error_{USERNAME}.png")
        except Exception: pass

    await browser.close()
    return result


async def run() -> dict:
    final = {"ok": False, "phase": "init", "error": "", "exit_ip": ""}
    proxy_cfg = {"server": PROXY} if PROXY else None

    # 加载库：playwright+stealth 优先，patchright 备用
    stealth_fn = None
    use_patchright = False
    pw_ctx_fn = None

    try:
        from playwright.async_api import async_playwright as _apw
        pw_ctx_fn = _apw
        try:
            from playwright_stealth import Stealth
            stealth_fn = Stealth().apply_stealth_async
            log("使用 playwright + stealth（优先）")
        except ImportError:
            log("playwright（无 stealth）")
    except ImportError:
        try:
            from patchright.async_api import async_playwright as _apw
            pw_ctx_fn = _apw
            use_patchright = True
            log("fallback: patchright")
        except ImportError:
            final["error"] = "playwright/patchright 未安装"
            return final

    # 获取出口 IP（一次，全程保留）
    try:
        async with pw_ctx_fn() as pw:
            ip = await get_exit_ip(pw, proxy_cfg)
            if ip:
                final["exit_ip"] = ip
                log(f"出口 IP: {ip}")
    except Exception as e:
        log(f"出口 IP 获取异常: {e}")

    # integrity 失败 → 最多 3 次新 browser 重试
    INTEGRITY_ERRORS = {
        "integrity_check_failed_on_load", "integrity_check_failed_after_click",
        "integrity_check_failed_after_step1", "integrity_check_failed_at_step2",
        "integrity_check_failed_after_step2",
    }

    for attempt in range(1, 4):
        log(f"browser attempt {attempt}/3")
        async with pw_ctx_fn() as pw:
            res = await attempt_register(pw, proxy_cfg, use_patchright, stealth_fn, final["exit_ip"])
        res["exit_ip"] = final["exit_ip"]  # 强制保留
        final = res
        if res["ok"]:
            break
        if res["error"] not in INTEGRITY_ERRORS:
            break
        log(f"integrity 失败({res['error']}) → 新 browser 实例重试")
        await asyncio.sleep(1)

    return final


if __name__ == "__main__":
    res = asyncio.run(run())
    print(json.dumps(res))
