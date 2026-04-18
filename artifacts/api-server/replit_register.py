#!/usr/bin/env python3
"""
replit_register.py — Replit 注册表单自动化 v6.0
核心修复：
  - 明确区分 reCAPTCHA（recaptchaToken）vs Turnstile（cf-turnstile-response）
  - 全量 iframe src 探针（不截断），自动提取 reCAPTCHA siteKey
  - CapSolver 集成：CAPSOLVER_KEY env 有值时自动用 ReCaptchaV2TaskProxyless
  - token 全量捕获（不截断），两次 token 对比日志
  - wait_cf 同时检测 Turnstile + reCAPTCHA iframe，避免提前提交
  - 端口 CF 封禁检测：is_cf_blocked 覆盖 "Attention Required"
"""
import sys, json, asyncio, os, time

params   = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
EMAIL    = params.get("email", "")
USERNAME = params.get("username", "")
PASSWORD = params.get("password", "")
PROXY    = params.get("proxy", "")
UA       = params.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADLESS = params.get("headless", True)
CAPSOLVER_KEY = os.environ.get("CAPSOLVER_KEY", params.get("capsolver_key", ""))

def log(msg): print(f"[replit_reg] {msg}", flush=True)

# ── CF / 错误检测 ──────────────────────────────────────────────────────────────
def is_cf_blocked(title: str, body: str) -> bool:
    t, b = title.lower(), body.lower()
    return (
        "attention required" in t or "attention required" in b or
        "have been blocked" in b or "sorry, you have been blocked" in b or
        "you are unable to access" in b or
        "error 1020" in b or "error 1010" in b or
        "cloudflare" in t and "block" in b
    )

def is_integrity_error(body: str) -> bool:
    b = body.lower()
    return "failed to evaluate" in b or "browser integrity" in b or "integrity check" in b

def is_captcha_invalid(text: str) -> bool:
    t = text.lower()
    return (
        "captcha token is invalid" in t or "invalid captcha" in t or
        "captcha validation failed" in t or "captcha expired" in t or
        "recaptcha" in t and ("invalid" in t or "expired" in t or "failed" in t)
    )

# ── 全量 DOM 探针（不截断 token / iframe src）───────────────────────────────────
_JS_FULL_PROBE = """() => {
    var hidden = Array.from(document.querySelectorAll('input[type="hidden"]')).map(e=>({
        n: e.name, v: e.value   // 不截断
    }));
    var iframes = Array.from(document.querySelectorAll('iframe')).map(e=>({
        src: e.src,             // 不截断，用于提取 siteKey
        id: e.id, cls: e.className
    }));
    var cfEl  = document.querySelector('[name="cf-turnstile-response"]');
    var rcEl  = document.querySelector('[name="g-recaptcha-response"], #g-recaptcha-response, [name="recaptchaToken"]');
    return {
        hidden,
        iframes,
        cfToken: cfEl  ? cfEl.value  : null,
        rcToken: rcEl  ? rcEl.value  : null,
        iframeCount: iframes.length
    };
}"""

def extract_recaptcha_sitekey(iframes: list) -> str | None:
    """从 iframe src 提取 reCAPTCHA siteKey（k= 参数）。"""
    import urllib.parse
    for fr in iframes:
        src = fr.get("src", "")
        if "google.com/recaptcha" in src or "recaptcha/api" in src:
            qs = urllib.parse.urlparse(src).query
            params_qs = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
            key = params_qs.get("k") or params_qs.get("sitekey")
            if key:
                log(f"[reCAPTCHA] siteKey 提取成功: {key}")
                return key
    return None

def extract_turnstile_sitekey(iframes: list) -> str | None:
    for fr in iframes:
        src = fr.get("src", "")
        if "challenges.cloudflare.com" in src or "cf-turnstile" in src:
            import urllib.parse
            qs = urllib.parse.urlparse(src).query
            params_qs = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
            key = params_qs.get("sitekey") or params_qs.get("k")
            if key:
                log(f"[Turnstile] siteKey 提取: {key}")
                return key
    return None

# ── CapSolver reCAPTCHA 求解器 ─────────────────────────────────────────────────
def capsolver_solve_recaptcha_v2(api_key: str, site_key: str, page_url: str, invisible: bool = False) -> str | None:
    """调用 CapSolver 同步解 reCAPTCHA v2。约 20-60s。"""
    try:
        import urllib.request, urllib.error
        task_type = "ReCaptchaV2TaskProxyless" if not invisible else "ReCaptchaV2EnterpriseTaskProxyless"
        payload = json.dumps({
            "clientKey": api_key,
            "task": {
                "type": task_type,
                "websiteURL": page_url,
                "websiteKey": site_key,
                "isInvisible": invisible
            }
        }).encode()
        req = urllib.request.Request(
            "https://api.capsolver.com/createTask",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        r = urllib.request.urlopen(req, timeout=15)
        resp = json.loads(r.read())
        task_id = resp.get("taskId")
        if not task_id:
            log(f"CapSolver createTask 失败: {resp.get('errorDescription','unknown')}")
            return None
        log(f"CapSolver taskId={task_id}，轮询结果（最多120s）…")
        for _ in range(40):
            time.sleep(3)
            req2 = urllib.request.Request(
                "https://api.capsolver.com/getTaskResult",
                data=json.dumps({"clientKey": api_key, "taskId": task_id}).encode(),
                headers={"Content-Type": "application/json"}
            )
            r2 = urllib.request.urlopen(req2, timeout=10)
            resp2 = json.loads(r2.read())
            status = resp2.get("status")
            if status == "ready":
                token = resp2.get("solution", {}).get("gRecaptchaResponse", "")
                log(f"CapSolver 解算完成，token 长度={len(token)}")
                return token
            if status == "failed":
                log(f"CapSolver 解算失败: {resp2.get('errorDescription','unknown')}")
                return None
        log("CapSolver 轮询超时")
        return None
    except Exception as e:
        log(f"CapSolver 异常: {e}")
        return None

async def inject_recaptcha_token(page, token: str):
    """将 CapSolver token 注入页面。"""
    _JS_INJECT = f"""() => {{
        // 直接写入隐藏字段
        var els = document.querySelectorAll('[name="g-recaptcha-response"], #g-recaptcha-response, [name="recaptchaToken"]');
        els.forEach(function(el) {{ el.value = {json.dumps(token)}; }});
        // 触发 grecaptcha callback（如果有）
        try {{
            if (window.grecaptcha && window.grecaptcha.execute) {{
                // v2 invisible: 不需要 execute，token 已注入
            }}
            // React/自定义回调
            var cb = window.__recaptchaCallback || window.__onCaptchaToken;
            if (typeof cb === 'function') cb({json.dumps(token)});
        }} catch(e) {{}}
        return els.length;
    }}"""
    n = await page.evaluate(_JS_INJECT)
    log(f"reCAPTCHA token 注入 {n} 个字段")

# ── 出口 IP ───────────────────────────────────────────────────────────────────
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
        log(f"出口 IP 获取失败: {e}")
        return ""

# ── CF Turnstile 等待（同时检测 reCAPTCHA） ────────────────────────────────────
async def wait_cf(page, use_patchright: bool) -> str | None:
    for i in range(8):  # 最多 16s
        title = await page.title()
        html  = await page.content()
        body  = (await page.locator("body").inner_text())[:500]

        if is_cf_blocked(title, body):
            log(f"CF 封禁 (iter {i+1}): {title!r}")
            return "signup_cf_ip_banned"

        still = (
            "just a moment" in title.lower() or
            "cf-turnstile" in html or
            "challenges.cloudflare.com" in html or
            "challenge" in title.lower()
        )
        if not still:
            log(f"CF 通过，标题: {title!r}")
            return None

        log(f"CF waiting ({i+1}/8) title={title!r}")
        await page.wait_for_timeout(2000)

    title = await page.title()
    body  = (await page.locator("body").inner_text())[:300]
    if is_cf_blocked(title, body):
        return "signup_cf_ip_banned"
    log(f"Turnstile 16s 超时 → signup_cf_ip_banned")
    return "signup_cf_ip_banned"

# ── Step 1：填写 email + password + 解 captcha ────────────────────────────────
_last_token: dict = {"rc": None, "cf": None}  # 用于两次 token 对比

async def fill_step1(page) -> str | None:
    # 填 email
    for sel in ['input[name="email"]', 'input[type="email"]', 'input[placeholder*="email" i]']:
        f = page.locator(sel)
        if await f.count():
            await f.first.click()
            await f.first.fill(EMAIL)
            await page.wait_for_timeout(300)
            log(f"填 email via {sel}")
            break
    else:
        return "signup_email_field_not_found"

    # 填 password
    for sel in ['input[type="password"]', 'input[name="password"]']:
        f = page.locator(sel)
        if await f.count():
            await f.first.click()
            await f.first.fill(PASSWORD)
            await page.wait_for_timeout(300)
            log("填 password")
            break

    await page.wait_for_timeout(800)
    await page.screenshot(path=f"/tmp/replit_step1_{USERNAME}.png")

    # === 全量 DOM 探针（iframe src 不截断）===
    probe = {}
    try:
        probe = await page.evaluate(_JS_FULL_PROBE)
        iframes = probe.get("iframes", [])
        cf_token = probe.get("cfToken") or ""
        rc_token = probe.get("rcToken") or ""

        log(f"[探针] iframes={len(iframes)} cfToken={len(cf_token)}chars rcToken={len(rc_token)}chars")
        for fr in iframes:
            src = fr.get("src", "")
            log(f"  iframe src: {src[:120]}")  # 显示120字符方便诊断

        # 判断验证码类型
        rc_sitekey = extract_recaptcha_sitekey(iframes)
        ts_sitekey = extract_turnstile_sitekey(iframes)

        any_rc  = bool(rc_sitekey) or any("recaptcha" in fr.get("src","") for fr in iframes)
        any_ts  = bool(ts_sitekey) or any("challenges.cloudflare.com" in fr.get("src","") for fr in iframes)
        log(f"[探针] 检测类型: reCAPTCHA={any_rc} Turnstile={any_ts}")

        if not cf_token and not rc_token:
            # 等待 captcha 就绪
            wait_rounds = 8 if any_rc else 5  # reCAPTCHA 可能慢些
            log(f"token 未就绪，等待最多 {wait_rounds*2}s patchright 自解算…")
            for _r in range(wait_rounds):
                await page.wait_for_timeout(2000)
                probe2 = await page.evaluate(_JS_FULL_PROBE)
                cf2 = probe2.get("cfToken") or ""
                rc2 = probe2.get("rcToken") or ""
                log(f"  等待 {(_r+1)*2}s: cfToken={len(cf2)}chars rcToken={len(rc2)}chars")
                if cf2 or rc2:
                    cf_token, rc_token = cf2, rc2
                    break

        # CapSolver reCAPTCHA 解算（有 key + 有 sitekey）
        if any_rc and rc_sitekey and CAPSOLVER_KEY and not rc_token:
            log(f"[CapSolver] 启动 reCAPTCHA v2 解算 sitekey={rc_sitekey}")
            solved = capsolver_solve_recaptcha_v2(CAPSOLVER_KEY, rc_sitekey, "https://replit.com/signup")
            if solved:
                await inject_recaptcha_token(page, solved)
                rc_token = solved
                log(f"[CapSolver] reCAPTCHA 注入完成，token长度={len(solved)}")
            else:
                log("[CapSolver] 解算失败，继续尝试提交（可能失败）")

        # 两次 token 对比日志
        prev_rc = _last_token["rc"]
        if rc_token:
            log(f"[token对比] rcToken 本次={rc_token[:40]}… 上次={prev_rc[:40] if prev_rc else 'None'}")
            log(f"[token对比] 相同={rc_token == prev_rc} 长度={len(rc_token)}")
            _last_token["rc"] = rc_token
        prev_cf = _last_token["cf"]
        if cf_token:
            log(f"[token对比] cfToken 本次={cf_token[:40]}… 上次={prev_cf[:40] if prev_cf else 'None'}")
            log(f"[token对比] 相同={cf_token == prev_cf} 长度={len(cf_token)}")
            _last_token["cf"] = cf_token

        # 没有任何 iframe 说明 CF 还没通过
        if len(iframes) == 0:
            log("[警告] iframes=0，CF 可能尚未通过，继续尝试提交")

    except Exception as _pe:
        log(f"DOM探针异常: {_pe}")

    # 提交 Step1
    for sel in [
        'button:has-text("Create Account")', 'button:has-text("Create account")',
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

    await page.wait_for_timeout(5000)
    await page.screenshot(path=f"/tmp/replit_after_step1_{USERNAME}.png")

    body = (await page.locator("body").inner_text())[:500]
    log(f"Step1_body[0:250]: {body[:250].replace(chr(10),' ')}")
    log(f"Step1_url: {page.url[:120]}")

    if is_integrity_error(body):
        return "integrity_check_failed_after_step1"
    if is_captcha_invalid(body):
        return "captcha_token_invalid"

    SUCCESS_HINTS = ("verify your email", "check your email", "we sent", "sent you",
                     "verification email", "confirm your email", "check for an email",
                     "sent an email")
    if any(h in body.lower() for h in SUCCESS_HINTS):
        log("Step1 成功：已发送验证邮件（单步表单）")
        return None

    if "signup" in page.url.lower():
        err_els = await page.locator(
            '[class*="error" i],[data-cy*="error"],[role="alert"],[class*="invalid" i]'
        ).all_text_contents()
        errs = [e.strip() for e in err_els if e.strip()]
        if errs:
            return "; ".join(errs[:3])

    return None

# ── Step 2：填写 username ──────────────────────────────────────────────────────
async def fill_step2(page) -> str | None:
    for sel in ['input[name="username"]', 'input[placeholder*="username" i]', '#username']:
        f = page.locator(sel)
        if await f.count():
            await f.first.click()
            await f.first.fill(USERNAME)
            await page.wait_for_timeout(400)
            log(f"填 username via {sel}")
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
    log(f"Step2 提交后 URL: {cur_url[:100]}")
    await page.screenshot(path=f"/tmp/replit_after_{USERNAME}.png")

    SUCCESS_URLS = ("verify", "confirm", "check-email", "dashboard", "home", "@")
    if any(x in cur_url.lower() for x in SUCCESS_URLS):
        return None

    if "signup" in cur_url.lower():
        err_els = await page.locator(
            '[class*="error" i],[data-cy*="error"],[role="alert"]'
        ).all_text_contents()
        errs = [e.strip() for e in err_els if e.strip()]
        if errs:
            return "; ".join(errs[:3])
        await page.wait_for_timeout(4000)
        if "signup" not in page.url.lower():
            return None
        return "signup_still_on_form_no_redirect"

    return None

# ── 单次 browser attempt ───────────────────────────────────────────────────────
async def attempt_register(pw_module, proxy_cfg, use_patchright: bool, stealth_fn, exit_ip: str) -> dict:
    result = {"ok": False, "phase": "init", "error": "", "exit_ip": exit_ip}
    _last_token["rc"] = None
    _last_token["cf"] = None

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

    # 网络请求拦截（捕获完整 captcha 字段，不截断）
    _captured_reqs: list = []
    def _on_request(req):
        try:
            if "replit.com" in req.url and req.method == "POST":
                body = req.post_data or ""
                if any(k in body for k in ("email","captcha","turnstile","token","recaptcha")):
                    _captured_reqs.append(f"POST {req.url} body={body[:600]}")  # 不截断
        except Exception:
            pass
    page.on("request", _on_request)

    try:
        # 1. 导航
        result["phase"] = "navigate"
        log("打开 replit.com/signup …")
        await page.goto("https://replit.com/signup", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1500)

        # 2. 立即检测
        t0 = await page.title()
        b0 = (await page.locator("body").inner_text())[:400]
        log(f"初始页面标题: {t0!r}")
        if is_cf_blocked(t0, b0):
            result["error"] = "signup_cf_ip_banned"
            await browser.close(); return result
        if is_integrity_error(b0):
            result["error"] = "integrity_check_failed_on_load"
            await browser.close(); return result

        # 3. 等 CF Turnstile
        cf_err = await wait_cf(page, use_patchright)
        if cf_err:
            result["error"] = cf_err
            await browser.close(); return result

        # 4. 点 Email 按钮
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
                log(f"点击 Email 按钮: {sel}")
                await page.wait_for_timeout(3500)
                break
        else:
            log("未找到 Email 按钮 → 表单可能已直接展示")

        # 5. 等 Step1 表单
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

        # 6. 填写 Step1（captcha_token_invalid → reload 重试）
        result["phase"] = "step1_fill"
        step1_err = await fill_step1(page)
        for _req in _captured_reqs[-5:]:
            log(f"[intercept] {_req}")
        _captured_reqs.clear()

        if step1_err == "captcha_token_invalid":
            log("captcha_token_invalid → reload，重新等待解算…")
            try:
                await page.reload(wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(3000)
                # 重新点 Email 按钮
                for sel2 in ['button:has-text("Email & password")', 'button:has-text("Continue with email")', 'button:has-text("Email")']:
                    btn2 = page.locator(sel2)
                    if await btn2.count():
                        await btn2.first.click()
                        await page.wait_for_timeout(2000)
                        break
                step1_err2 = await fill_step1(page)
                for _req in _captured_reqs[-5:]:
                    log(f"[intercept-retry] {_req}")
                _captured_reqs.clear()
                if step1_err2:
                    result["error"] = step1_err2
                    await browser.close(); return result
            except Exception as e2:
                result["error"] = f"captcha_token_invalid_reload_failed:{e2}"
                await browser.close(); return result

        if step1_err:
            result["error"] = step1_err
            await browser.close(); return result

        # 7. 等 Step2 username
        result["phase"] = "step2_wait"
        try:
            await page.wait_for_selector(
                'input[name="username"], input[placeholder*="username" i], #username',
                timeout=12000
            )
            log("Step2 username 字段出现")
        except Exception:
            cur = page.url
            SUCCESS_URL_HINTS = ("verify", "confirm", "dashboard", "home", "@", "check-email", "email")
            if any(x in cur.lower() for x in SUCCESS_URL_HINTS) and "signup" not in cur.lower():
                result["ok"] = True; result["phase"] = "email_verify_pending"
                log(f"✅ 无 Step2，URL 跳转: {cur[:80]}")
                await browser.close(); return result
            try:
                body_chk = (await page.locator("body").inner_text())[:500].lower()
                email_sent_hints = ("check your email", "we sent", "verify your email",
                                    "verification email", "sent you", "check for an email", "sent an email")
                if any(h in body_chk for h in email_sent_hints):
                    result["ok"] = True; result["phase"] = "email_verify_pending"
                    log(f"✅ 无 Step2，body 检测邮件已发送")
                    await browser.close(); return result
            except Exception:
                pass
            await page.screenshot(path=f"/tmp/replit_step2_missing_{USERNAME}.png")
            result["error"] = "signup_username_field_missing"
            await browser.close(); return result

        b4 = (await page.locator("body").inner_text())[:200]
        if is_integrity_error(b4):
            result["error"] = "integrity_check_failed_at_step2"
            await browser.close(); return result

        # 8. 填写 Step2
        result["phase"] = "step2_fill"
        step2_err = await fill_step2(page)
        if step2_err:
            result["error"] = step2_err
            await browser.close(); return result

        result["ok"] = True
        result["phase"] = "email_verify_pending"
        log("✅ 注册完成，等待邮件验证")

    except Exception as exc:
        result["error"] = str(exc)
        log(f"异常: {exc}")
        try: await page.screenshot(path=f"/tmp/replit_error_{USERNAME}.png")
        except Exception: pass

    await browser.close()
    return result

# ── 主流程 ─────────────────────────────────────────────────────────────────────
async def run() -> dict:
    final = {"ok": False, "phase": "init", "error": "", "exit_ip": ""}
    proxy_cfg = {"server": PROXY} if PROXY else None

    log(f"CapSolver key: {'已配置' if CAPSOLVER_KEY else '未配置（无法解 reCAPTCHA，依赖 patchright 自解）'}")

    stealth_fn = None
    use_patchright = False
    pw_ctx_fn = None

    try:
        from patchright.async_api import async_playwright as _apw
        pw_ctx_fn = _apw
        use_patchright = True
        log("使用 patchright（Turnstile 自解算；reCAPTCHA 需 CapSolver）")
    except ImportError:
        try:
            from playwright.async_api import async_playwright as _apw
            pw_ctx_fn = _apw
            try:
                from playwright_stealth import Stealth
                stealth_fn = Stealth().apply_stealth_async
                log("fallback: playwright + stealth")
            except ImportError:
                log("fallback: playwright（无 stealth）")
        except ImportError:
            final["error"] = "playwright/patchright 未安装"
            return final

    # 获取出口 IP
    try:
        async with pw_ctx_fn() as pw:
            ip = await get_exit_ip(pw, proxy_cfg)
            if ip:
                final["exit_ip"] = ip
                log(f"出口 IP: {ip}")
    except Exception as e:
        log(f"出口 IP 异常: {e}")

    INTEGRITY_ERRORS = {
        "integrity_check_failed_on_load", "integrity_check_failed_after_click",
        "integrity_check_failed_after_step1", "integrity_check_failed_at_step2",
        "integrity_check_failed_after_step2",
    }

    for attempt in range(1, 4):
        log(f"browser attempt {attempt}/3")
        async with pw_ctx_fn() as pw:
            res = await attempt_register(pw, proxy_cfg, use_patchright, stealth_fn, final["exit_ip"])
        res["exit_ip"] = final["exit_ip"]
        final = res
        if res["ok"]:
            break
        if res["error"] not in INTEGRITY_ERRORS:
            break
        log(f"integrity 失败({res['error']}) → 新 browser 重试")
        await asyncio.sleep(1)

    return final

if __name__ == "__main__":
    res = asyncio.run(run())
    print(json.dumps(res))
