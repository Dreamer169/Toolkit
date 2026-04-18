#!/usr/bin/env python3
"""
replit_register.py — Replit 注册表单浏览器自动化
BUG-FIX v5:
  - playwright+stealth 优先（可绕过 integrity check，CF Turnstile 也能通过）
  - patchright 备用（能解 Turnstile，12s 判断 IP 封禁）
  - 修复两步表单：Step1 email(type=text)+password → Continue → Step2 username → Create Account
  - 修复 exit_ip 在 fill_and_submit 中被清空的 bug（全程保留）
  - 内部最多 3 次 integrity 重试（每次新 browser 实例）
  - CF 封禁立即返回 signup_cf_ip_banned
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
        "attention required" in t or
        "have been blocked" in b or
        "sorry, you have been blocked" in b or
        "you are unable to access" in b or
        "error 1020" in b or
        "error 1010" in b
    )

def is_integrity_error(body: str) -> bool:
    b = body.lower()
    return (
        "failed to evaluate" in b or
        "browser integrity" in b or
        "integrity check" in b
    )

async def get_ip_via_page(page) -> str:
    """获取出口 IP，失败返回空字符串（不影响主流程）"""
    try:
        await page.goto("https://api.ipify.org?format=json",
                        wait_until="domcontentloaded", timeout=10000)
        data = json.loads(await page.locator("body").inner_text())
        return data.get("ip", "")
    except Exception:
        return ""

async def wait_cf(page, use_patchright: bool) -> str | None:
    """
    等待 CF Turnstile 解除。
    返回 None 表示已通过，返回错误字符串表示失败。
    """
    MAX_ITERS = 6 if use_patchright else 12  # 12s / 24s
    for i in range(MAX_ITERS):
        title = await page.title()
        html  = await page.content()
        body  = (await page.locator("body").inner_text())[:500]

        if is_cf_blocked(title, body):
            log(f"CF IP 封禁 (iter {i+1}): {title}")
            return "signup_cf_ip_banned"

        still = (
            "just a moment" in title.lower() or
            "cf-turnstile" in html or
            "challenge" in title.lower()
        )
        if not still:
            log(f"CF Turnstile 已通过，标题: {title}")
            return None

        log(f"CF waiting ({i+1}/{MAX_ITERS}) title={title!r}")
        await page.wait_for_timeout(2000)

    # 超时
    title = await page.title()
    body  = (await page.locator("body").inner_text())[:300]
    if is_cf_blocked(title, body):
        return "signup_cf_ip_banned"
    if use_patchright:
        log(f"patchright 12s 未解 Turnstile → IP 封禁 (title={title!r})")
        return "signup_cf_ip_banned"
    return "signup_turnstile_unsolved"

async def attempt_register(pw_module, proxy_cfg, use_patchright: bool, stealth_fn, exit_ip: str) -> dict:
    """
    单次注册尝试。exit_ip 由外层传入（保证不被覆盖）。
    """
    result = {"ok": False, "phase": "init", "error": "", "exit_ip": exit_ip}

    launch_args = [
        "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-blink-features=AutomationControlled",
        "--disable-web-security",
    ]
    browser = await pw_module.chromium.launch(
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
        # ── 1. 打开注册页 ────────────────────────────────────────────────────
        result["phase"] = "navigate"
        log("打开 replit.com/signup …")
        await page.goto("https://replit.com/signup",
                        wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1500)

        # ── 2. 立即检测 CF 封禁 ──────────────────────────────────────────────
        title0 = await page.title()
        body0  = (await page.locator("body").inner_text())[:500]
        if is_cf_blocked(title0, body0):
            result["error"] = "signup_cf_ip_banned"
            await browser.close()
            return result
        if is_integrity_error(body0):
            result["error"] = "integrity_check_failed_on_load"
            await browser.close()
            return result

        # ── 3. 等待 CF Turnstile ─────────────────────────────────────────────
        cf_err = await wait_cf(page, use_patchright)
        if cf_err:
            result["error"] = cf_err
            await browser.close()
            return result

        # ── 4. 点击 "Email & password" 按钮展开表单 ─────────────────────────
        result["phase"] = "click_email_btn"
        await page.wait_for_timeout(1000)
        EMAIL_BTN_SELS = [
            'button:has-text("Email & password")',
            'button:has-text("Continue with email")',
            'button:has-text("Use email")',
            'button:has-text("Email")',
            '[data-cy="email-signup"]',
            'a:has-text("Email")',
        ]
        clicked_btn = False
        for sel in EMAIL_BTN_SELS:
            btn = page.locator(sel)
            if await btn.count():
                await btn.first.click()
                log(f"已点击 Email 按钮: {sel}")
                await page.wait_for_timeout(1500)
                clicked_btn = True
                break
        if not clicked_btn:
            log("未找到 Email 按钮 → 表单可能已直接展示")

        # ── 5. 等待 Step1 表单出现 ───────────────────────────────────────────
        result["phase"] = "step1_fill"
        try:
            # 等待 email 字段（type=text name=email 或 type=email）
            await page.wait_for_selector(
                'input[name="email"], input[type="email"], input[placeholder*="email" i]',
                timeout=8000
            )
            log("Step1 表单已出现")
        except Exception:
            title2 = await page.title()
            body2  = (await page.locator("body").inner_text())[:300]
            if is_cf_blocked(title2, body2):
                result["error"] = "signup_cf_ip_banned"
            else:
                result["error"] = "signup_form_input_missing"
                log(f"Step1 表单未出现，标题: {title2!r}")
                await page.screenshot(path=f"/tmp/replit_no_form_{USERNAME}.png")
            await browser.close()
            return result

        # integrity 再检查
        body2 = (await page.locator("body").inner_text())[:300]
        if is_integrity_error(body2):
            result["error"] = "integrity_check_failed_after_click"
            await browser.close()
            return result

        # ── 6. 填写 Step1: email + password ─────────────────────────────────
        # Replit 实际表单: input[type="text"][name="email"]（不是 type=email！）
        EMAIL_SELS = [
            'input[name="email"]',           # 实测正确（type=text）
            'input[type="email"]',           # 备用
            'input[placeholder*="email" i]',
        ]
        for sel in EMAIL_SELS:
            f = page.locator(sel)
            if await f.count():
                await f.first.click()
                await f.first.fill(EMAIL)
                await page.wait_for_timeout(300)
                log(f"已填 email via {sel}")
                break

        PW_SELS = ['input[type="password"]', 'input[name="password"]']
        for sel in PW_SELS:
            f = page.locator(sel)
            if await f.count():
                await f.first.click()
                await f.first.fill(PASSWORD)
                await page.wait_for_timeout(300)
                log("已填 password")
                break

        await page.wait_for_timeout(800)
        await page.screenshot(path=f"/tmp/replit_step1_{USERNAME}.png")

        # ── 7. 提交 Step1 → 跳转到 Step2 ────────────────────────────────────
        result["phase"] = "step1_submit"
        step1_submitted = False
        for sel in [
            'button[type="submit"]',
            'button:has-text("Continue")',
            'button:has-text("Next")',
            'button:has-text("Sign up")',
            'button:has-text("Create")',
        ]:
            btn = page.locator(sel)
            if await btn.count():
                await btn.first.click()
                log(f"Step1 提交: {sel}")
                step1_submitted = True
                break
        if not step1_submitted:
            await page.keyboard.press("Enter")
            log("Step1 回车提交")

        await page.wait_for_timeout(3000)

        # integrity 检查
        body3 = (await page.locator("body").inner_text())[:300]
        if is_integrity_error(body3):
            result["error"] = "integrity_check_failed_after_step1"
            await browser.close()
            return result

        # 检查 Step1 是否有表单错误（email invalid 等）
        step1_url = page.url
        if "signup" in step1_url.lower():
            err_els = await page.locator(
                '[class*="error" i],[class*="Error"],[data-cy*="error"],[role="alert"]'
            ).all_text_contents()
            errs = [e.strip() for e in err_els if e.strip()]
            if errs:
                result["error"] = "; ".join(errs[:3])
                log(f"Step1 表单错误: {result['error']}")
                await page.screenshot(path=f"/tmp/replit_step1_err_{USERNAME}.png")
                await browser.close()
                return result

        # ── 8. 等待 Step2: username 字段出现 ────────────────────────────────
        result["phase"] = "step2_fill"
        try:
            await page.wait_for_selector(
                'input[name="username"], input[placeholder*="username" i], #username',
                timeout=8000
            )
            log("Step2 username 字段已出现")
        except Exception:
            # 可能直接成功跳转了（无需 username 步骤）
            cur_url = page.url
            log(f"Step2 等待超时，当前 URL: {cur_url[:80]}")
            if any(x in cur_url.lower() for x in ("verify", "confirm", "dashboard", "home", "@")):
                result["ok"]    = True
                result["phase"] = "email_verify_pending"
                log(f"✅ 直接成功（无 Step2）: {cur_url[:60]}")
                await browser.close()
                return result
            # 仍在 signup，截图
            await page.screenshot(path=f"/tmp/replit_step2_missing_{USERNAME}.png")
            result["error"] = "signup_username_field_missing"
            await browser.close()
            return result

        # integrity 检查
        body4 = (await page.locator("body").inner_text())[:300]
        if is_integrity_error(body4):
            result["error"] = "integrity_check_failed_at_step2"
            await browser.close()
            return result

        # ── 9. 填写 Step2: username ──────────────────────────────────────────
        for sel in ['input[name="username"]', 'input[placeholder*="username" i]', '#username']:
            f = page.locator(sel)
            if await f.count():
                await f.first.click()
                await f.first.fill(USERNAME)
                await page.wait_for_timeout(400)
                log(f"已填 username via {sel}")
                break

        await page.wait_for_timeout(800)
        await page.screenshot(path=f"/tmp/replit_step2_{USERNAME}.png")

        # ── 10. 提交 Step2 (Create Account) ─────────────────────────────────
        result["phase"] = "step2_submit"
        step2_submitted = False
        for sel in [
            'button:has-text("Create Account")',
            'button:has-text("Create account")',
            'button[type="submit"]',
            'button:has-text("Sign up")',
            'button:has-text("Finish")',
            'button:has-text("Continue")',
        ]:
            btn = page.locator(sel)
            if await btn.count():
                await btn.first.click()
                log(f"Step2 提交: {sel}")
                step2_submitted = True
                break
        if not step2_submitted:
            await page.keyboard.press("Enter")
            log("Step2 回车提交")

        await page.wait_for_timeout(6000)
        cur_url = page.url
        log(f"提交后 URL: {cur_url[:80]}")

        # integrity 再检查
        body5 = (await page.locator("body").inner_text())[:300]
        if is_integrity_error(body5):
            result["error"] = "integrity_check_failed_after_step2"
            await browser.close()
            return result

        await page.screenshot(path=f"/tmp/replit_after_{USERNAME}.png")

        # ── 11. 判断注册结果 ─────────────────────────────────────────────────
        SUCCESS_URLS = ("verify", "confirm", "check-email", "dashboard", "home", "@")
        if any(x in cur_url.lower() for x in SUCCESS_URLS):
            log(f"✅ 注册成功: {cur_url[:60]}")
            result["ok"]    = True
            result["phase"] = "email_verify_pending"
        elif "signup" in cur_url.lower():
            err_els = await page.locator(
                '[class*="error" i],[class*="Error"],[data-cy*="error"],[role="alert"]'
            ).all_text_contents()
            errs = [e.strip() for e in err_els if e.strip()]
            if errs:
                result["error"] = "; ".join(errs[:3])
                log(f"Step2 表单错误: {result['error']}")
            else:
                # 延迟跳转
                await page.wait_for_timeout(4000)
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


async def run() -> dict:
    """
    外层：获取 exit_ip，然后最多 3 次内部重试（integrity 失败时换新 browser 实例）。
    exit_ip 在第一次成功获取后全程保留，不被后续 attempt 覆盖。
    """
    final = {"ok": False, "phase": "init", "error": "", "exit_ip": ""}

    proxy_cfg = {"server": PROXY} if PROXY else None

    # ── 加载库（playwright+stealth 优先，patchright 备用）──────────────────
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
            log("playwright 已加载（无 stealth）")
    except ImportError:
        try:
            from patchright.async_api import async_playwright as _apw
            pw_ctx_fn = _apw
            use_patchright = True
            log("fallback: patchright")
        except ImportError:
            final["error"] = "playwright/patchright 未安装"
            return final

    # ── 先用一次 playwright 获取出口 IP（exit_ip 全程保留） ────────────────
    try:
        async with pw_ctx_fn() as pw:
            brow_ip = await pw.chromium.launch(
                headless=HEADLESS,
                proxy=proxy_cfg,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            ctx_ip = await brow_ip.new_context(viewport={"width": 800, "height": 600})
            pg_ip  = await ctx_ip.new_page()
            ip = await get_ip_via_page(pg_ip)
            await brow_ip.close()
            if ip:
                final["exit_ip"] = ip
                log(f"出口 IP: {ip}")
    except Exception as e:
        log(f"获取出口 IP 失败（继续）: {e}")

    # ── 最多 3 次注册尝试（integrity 失败时重试） ──────────────────────────
    INTEGRITY_ERRORS = {
        "integrity_check_failed_on_load",
        "integrity_check_failed_after_click",
        "integrity_check_failed_after_step1",
        "integrity_check_failed_at_step2",
        "integrity_check_failed_after_step2",
    }
    MAX_INTEGRITY_RETRY = 3

    for attempt in range(1, MAX_INTEGRITY_RETRY + 1):
        log(f"注册 attempt {attempt}/{MAX_INTEGRITY_RETRY}")
        async with pw_ctx_fn() as pw:
            res = await attempt_register(pw, proxy_cfg, use_patchright, stealth_fn, final["exit_ip"])

        # 保留 exit_ip（不被 attempt_register 覆盖）
        res["exit_ip"] = final["exit_ip"]
        final = res

        if res["ok"]:
            break
        # CF 封禁 / 非 integrity 错误 → 直接返回（不重试）
        if res["error"] not in INTEGRITY_ERRORS:
            break
        log(f"integrity 失败（{res['error']}），换新 browser 实例重试…")
        await asyncio.sleep(1)

    return final


if __name__ == "__main__":
    res = asyncio.run(run())
    print(json.dumps(res))
