#!/usr/bin/env python3
"""
replit_register.py — Replit 注册表单自动化 v7.27
策略（全免费，无付费服务）：
  Layer 1: playwright + stealth (chrome_runtime=True, webgl_vendor=True)
           + Canvas 2D 噪声注入 → reCAPTCHA Enterprise 自动评分 token（无需挑战）
  Layer 2: reCAPTCHA v2 音频挑战（仅当 Enterprise 未自动通过时）
           ffmpeg MP3→WAV + Google 免费 STT / faster-whisper 离线
关键修复 v7.24：
  - 删除全部 CapSolver 付费代码
  - 删除 patchright（不通过 integrity check）
  - 修复 if any_rc 覆盖有效 token 的 Bug（应为 if any_rc and not rc_token）
  - warmup 在页面加载期间并发执行，表单填写用快速 type()（30-50ms/char）
  - token 在提交前 re-check，确保不过期
"""
import sys, json, asyncio, os, subprocess, urllib.request, random as _random

params   = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
EMAIL    = params.get("email", "")
USERNAME = params.get("username", "")
PASSWORD = params.get("password", "")
PROXY    = params.get("proxy", "")
UA       = params.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADLESS = params.get("headless", True)

def log(msg): print(f"[replit_reg] {msg}", flush=True)

# ── CF / 错误检测 ─────────────────────────────────────────────────────────────
def is_cf_blocked(title: str, body: str) -> bool:
    t, b = title.lower(), body.lower()
    return (
        "attention required" in t or "attention required" in b or
        "have been blocked" in b or "sorry, you have been blocked" in b or
        "you are unable to access" in b or
        "error 1020" in b or "error 1010" in b or
        ("cloudflare" in t and "block" in b)
    )

def is_integrity_error(body: str) -> bool:
    b = body.lower()
    return "failed to evaluate" in b or "browser integrity" in b or "integrity check" in b

def is_captcha_invalid(text: str) -> bool:
    t = text.lower()
    return (
        "captcha token is invalid" in t or "invalid captcha" in t or
        "captcha validation failed" in t or "captcha expired" in t or
        ("recaptcha" in t and ("invalid" in t or "expired" in t or "failed" in t))
    )

# ── DOM 探针 ──────────────────────────────────────────────────────────────────
_JS_FULL_PROBE = """() => {
    var iframes = Array.from(document.querySelectorAll('iframe')).map(e=>({
        src: e.src, id: e.id, cls: e.className
    }));
    var cfEl = document.querySelector('[name="cf-turnstile-response"]');
    var rcEl = document.querySelector('[name="g-recaptcha-response"], #g-recaptcha-response, [name="recaptchaToken"]');
    return {
        iframes,
        cfToken: cfEl ? cfEl.value : null,
        rcToken: rcEl ? rcEl.value : null,
    };
}"""

def extract_recaptcha_sitekey(iframes: list) -> str | None:
    import urllib.parse
    for fr in iframes:
        src = fr.get("src", "")
        if "google.com/recaptcha" in src or "recaptcha/api" in src:
            qs = urllib.parse.urlparse(src).query
            p = dict(x.split("=", 1) for x in qs.split("&") if "=" in x)
            k = p.get("k") or p.get("sitekey")
            if k:
                log(f"[reCAPTCHA] sitekey={k}")
                return k
    return None

def extract_turnstile_sitekey(iframes: list) -> str | None:
    import urllib.parse
    for fr in iframes:
        src = fr.get("src", "")
        if "challenges.cloudflare.com" in src or "cf-turnstile" in src:
            qs = urllib.parse.urlparse(src).query
            p = dict(x.split("=", 1) for x in qs.split("&") if "=" in x)
            k = p.get("sitekey") or p.get("k")
            if k:
                log(f"[Turnstile] sitekey={k}")
                return k
    return None

# ══════════════════════════════════════════════════════════════════════════════
# 音频挑战（Layer 2 — 完全免费）
# ══════════════════════════════════════════════════════════════════════════════

def _mp3_to_wav(mp3_bytes: bytes) -> bytes | None:
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
            input=mp3_bytes, capture_output=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout
        log(f"[audio] ffmpeg err: {r.stderr[:200].decode(errors='replace')}")
        return None
    except FileNotFoundError:
        log("[audio] ffmpeg 未找到")
        return None
    except Exception as e:
        log(f"[audio] ffmpeg 异常: {e}"); return None

def _google_stt(wav_bytes: bytes) -> str | None:
    URL = (
        "https://www.google.com/speech-api/v2/recognize"
        "?output=json&lang=en-US&key=AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw"
    )
    try:
        req = urllib.request.Request(URL, data=wav_bytes,
                                      headers={"Content-Type": "audio/l16; rate=16000"})
        resp = urllib.request.urlopen(req, timeout=30)
        text = resp.read().decode()
        log(f"[audio] STT raw: {text[:200]}")
        for line in text.strip().split("\n"):
            if not line.strip(): continue
            try:
                data = json.loads(line)
                results = data.get("result", [])
                if results:
                    return results[0]["alternative"][0]["transcript"].strip().lower()
            except Exception:
                pass
        return None
    except Exception as e:
        log(f"[audio] Google STT 异常: {e}"); return None

def _whisper_stt(mp3_bytes: bytes) -> str | None:
    import tempfile, os as _os
    try:
        from faster_whisper import WhisperModel
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(mp3_bytes); fname = f.name
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segs, _ = model.transcribe(fname, language="en", beam_size=1)
        _os.unlink(fname)
        text = " ".join(s.text for s in segs).strip().lower()
        log(f"[audio] Whisper: '{text}'")
        return text or None
    except Exception as e:
        log(f"[audio] Whisper 异常: {e}"); return None

async def solve_recaptcha_audio(page) -> str | None:
    log("[audio] ▶ 音频挑战开始")
    checkbox_frame = None
    for _w in range(15):
        for f in page.frames:
            if "recaptcha" in f.url and "anchor" in f.url:
                checkbox_frame = f; break
        if checkbox_frame: break
        await page.wait_for_timeout(500)

    if not checkbox_frame:
        log("[audio] 未找到 reCAPTCHA anchor iframe")
        return None

    try:
        cb = checkbox_frame.locator("#recaptcha-anchor")
        if await cb.count():
            await cb.click()
            log("[audio] 点击 checkbox")
            await page.wait_for_timeout(2500)
    except Exception as e:
        log(f"[audio] checkbox 点击异常: {e}")

    async def _read_token() -> str:
        for js in [
            "() => { var el=document.querySelector('#g-recaptcha-response,[name=\"g-recaptcha-response\"],[name=\"recaptchaToken\"]'); return el?el.value:''; }",
        ]:
            try:
                t = await page.evaluate(js)
                if t: return t
            except Exception: pass
        return ""

    # 等待 checkbox 通过（无挑战情况，最多 4s）
    for _ in range(8):
        await page.wait_for_timeout(500)
        t = await _read_token()
        if t:
            log(f"[audio] ✅ checkbox 通过 token={len(t)}chars")
            return t

    # 找 challenge iframe (bframe)
    challenge_frame = None
    for _w in range(20):
        for f in page.frames:
            if "recaptcha" in f.url and "bframe" in f.url:
                challenge_frame = f; break
        if challenge_frame: break
        await page.wait_for_timeout(500)

    if not challenge_frame:
        t = await _read_token()
        if t:
            log(f"[audio] ✅ 无 bframe，token={len(t)}chars")
            return t
        log("[audio] 未找到 bframe")
        return None

    # 点击音频按钮
    try:
        audio_btn = challenge_frame.locator("#recaptcha-audio-button")
        if await audio_btn.count():
            await audio_btn.click()
            log("[audio] 点击音频按钮")
            await page.wait_for_timeout(2500)
        else:
            log("[audio] 未找到音频按钮，可能已是音频模式或图片模式")
    except Exception as e:
        log(f"[audio] 音频按钮异常: {e}")

    # 下载音频 + 识别
    for attempt in range(3):
        try:
            audio_src_el = challenge_frame.locator(".rc-audiochallenge-tdownload-link, audio source, #audio-source")
            if not await audio_src_el.count():
                log(f"[audio] 第{attempt+1}次：未找到音频链接")
                await page.wait_for_timeout(2000)
                continue
            audio_url = await audio_src_el.first.get_attribute("href") or await audio_src_el.first.get_attribute("src")
            if not audio_url:
                log(f"[audio] 第{attempt+1}次：音频 URL 为空")
                await page.wait_for_timeout(2000)
                continue
            log(f"[audio] 下载音频: {audio_url[:80]}")
            req = urllib.request.Request(audio_url, headers={"User-Agent": UA})
            mp3_bytes = urllib.request.urlopen(req, timeout=30).read()
            log(f"[audio] 下载 {len(mp3_bytes)} bytes")
            wav = _mp3_to_wav(mp3_bytes)
            transcript = None
            if wav:
                transcript = _google_stt(wav)
            if not transcript:
                transcript = _whisper_stt(mp3_bytes)
            if not transcript:
                log("[audio] STT 失败，尝试刷新音频")
                try:
                    reload_btn = challenge_frame.locator("#recaptcha-reload-button")
                    if await reload_btn.count():
                        await reload_btn.click()
                        await page.wait_for_timeout(2000)
                except Exception: pass
                continue

            log(f"[audio] 识别结果: '{transcript}'")
            answer_el = challenge_frame.locator("#audio-response")
            if await answer_el.count():
                await answer_el.fill(transcript)
                await page.wait_for_timeout(500)
                verify_btn = challenge_frame.locator("#recaptcha-verify-button")
                if await verify_btn.count():
                    await verify_btn.click()
                    log("[audio] 提交答案")
                    await page.wait_for_timeout(3000)
                    token = await _read_token()
                    if token:
                        log(f"[audio] ✅ 音频通过 token={len(token)}chars")
                        return token
                    log("[audio] 提交后无 token，继续尝试")
                else:
                    log("[audio] 未找到 verify 按钮")
            else:
                log("[audio] 未找到答案输入框")
        except Exception as e:
            log(f"[audio] 第{attempt+1}次异常: {e}")
            await page.wait_for_timeout(2000)

    log("[audio] ✗ 音频挑战失败")
    return None

# ── CF Turnstile 等待 ─────────────────────────────────────────────────────────
async def wait_cf(page) -> str | None:
    for _w in range(60):
        await page.wait_for_timeout(500)
        try:
            title = await page.title()
            body  = (await page.locator("body").inner_text())[:300]
        except Exception:
            continue
        tl = title.lower()
        if "just a moment" in tl:
            if _w % 10 == 0:
                log(f"[cf] CF challenge... ({_w//2}s)")
            continue
        return None
    title2 = await page.title()
    if "just a moment" in title2.lower():
        log("[cf] 30s 超时，代理 IP 被 CF 封")
        return "signup_cf_js_challenge_timeout"
    return None

# ── 人类行为（扩展版，40-55s，在页面加载后并发执行，提升 Enterprise score）───
async def _human_warmup(page):
    """长时间人类行为模拟：bezier 鼠标、滚动、停顿、hover 元素。
    目标：让 reCAPTCHA Enterprise 积累足够多的行为信号，提高 score（目标 ≥0.5）。
    """
    try:
        w, h = 1280, 800

        async def _bezier(x0, y0, x1, y1, steps=22):
            cx = x0 + (x1 - x0) * 0.3 + _random.randint(-80, 80)
            cy = y0 + (y1 - y0) * 0.3 + _random.randint(-60, 60)
            for i in range(steps + 1):
                t = i / steps
                bx = (1-t)**2*x0 + 2*(1-t)*t*cx + t**2*x1
                by = (1-t)**2*y0 + 2*(1-t)*t*cy + t**2*y1
                await page.mouse.move(bx, by)
                await page.wait_for_timeout(_random.randint(8, 22))

        # 阶段 1：初始停顿（用户"阅读"页面）
        await page.wait_for_timeout(_random.randint(2000, 3500))

        # 阶段 2：大量 bezier 鼠标移动（20次，覆盖全屏）
        x, y = _random.randint(300, 800), _random.randint(150, 500)
        for i in range(20):
            nx = _random.randint(60, w - 60)
            ny = _random.randint(60, h - 60)
            await _bezier(x, y, nx, ny, steps=_random.randint(18, 30))
            pause = _random.randint(60, 300)
            # 每5次随机长停顿（模拟阅读/思考）
            if i % 5 == 4:
                pause += _random.randint(500, 1200)
            await page.wait_for_timeout(pause)
            x, y = nx, ny

        # 阶段 3：滚动浏览页面（向下+向上）
        for sd in [80, 120, 60, 100, 40]:
            await page.evaluate(f"window.scrollBy(0, {sd})")
            await page.wait_for_timeout(_random.randint(300, 700))
        await page.wait_for_timeout(_random.randint(800, 1500))
        # 缓慢回到顶部
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, -80)")
            await page.wait_for_timeout(_random.randint(150, 350))

        # 阶段 4：再次鼠标移动（10次，专注于页面中央区域）
        x, y = _random.randint(400, 700), _random.randint(200, 500)
        for _ in range(10):
            nx = _random.randint(200, w - 200)
            ny = _random.randint(150, h - 200)
            await _bezier(x, y, nx, ny, steps=_random.randint(15, 25))
            await page.wait_for_timeout(_random.randint(80, 350))
            x, y = nx, ny

        # 阶段 5：尝试 hover 页面可见链接/按钮（不点击）
        try:
            links = await page.locator("a, button").all()
            for lnk in _random.sample(links, min(4, len(links))):
                try:
                    bbox = await lnk.bounding_box()
                    if bbox and bbox["x"] > 0 and bbox["y"] > 0:
                        await page.mouse.move(
                            bbox["x"] + bbox["width"] / 2,
                            bbox["y"] + bbox["height"] / 2
                        )
                        await page.wait_for_timeout(_random.randint(300, 800))
                except Exception:
                    pass
        except Exception:
            pass

        # 阶段 6：最终停顿（用户"决定"开始填表）
        await page.wait_for_timeout(_random.randint(2000, 4000))

        log("[warmup] ✓ 完成（约 40-55s 行为积累）")
    except Exception as e:
        log(f"[warmup] 异常(忽略): {e}")

async def _fast_fill(page, sel_list: list[str], value: str, label: str,
                     min_ms: int = 90, max_ms: int = 220) -> bool:
    """人类速度填充：click → type（默认 90-220ms/char，模拟真实打字节奏）。"""
    for sel in sel_list:
        f = page.locator(sel)
        if await f.count():
            await f.first.click()
            await page.wait_for_timeout(_random.randint(100, 200))
            await f.first.fill("")
            for ch in value:
                await f.first.type(ch)
                delay = _random.randint(min_ms, max_ms)
                # 偶尔有短暂停顿（模拟换手/思考）
                if _random.random() < 0.08:
                    delay += _random.randint(200, 500)
                await page.wait_for_timeout(delay)
            log(f"填 {label} via {sel}")
            return True
    return False

# ── token 等待（公共函数）──────────────────────────────────────────────────────
async def _wait_for_token(page, max_s: int = 15, label: str = "") -> tuple[str, str]:
    """等待 rc_token 或 cf_token 自动出现，返回 (rc_token, cf_token)。"""
    rc, cf = "", ""
    for i in range(max_s):
        await page.wait_for_timeout(1000)
        try:
            p = await page.evaluate(_JS_FULL_PROBE)
            rc = p.get("rcToken") or ""
            cf = p.get("cfToken") or ""
            if rc:
                log(f"[token{label}] ✅ reCAPTCHA token at {i+1}s len={len(rc)}")
                return rc, cf
            if cf:
                log(f"[token{label}] ✅ Turnstile token at {i+1}s len={len(cf)}")
                return rc, cf
        except Exception:
            pass
    log(f"[token{label}] {max_s}s 内无 token (rc={bool(rc)} cf={bool(cf)})")
    return rc, cf

# ── Step 1 ────────────────────────────────────────────────────────────────────
async def fill_step1(page) -> str | None:
    # 填 email
    ok = await _fast_fill(
        page,
        ['input[name="email"]', 'input[type="email"]', 'input[placeholder*="email" i]'],
        EMAIL, "email"
    )
    if not ok:
        return "signup_email_field_not_found"

    # v7.27b：等待 email 异步验证完成（显示 "Email is available"），避免 button 被 disabled
    await page.wait_for_timeout(_random.randint(1800, 2800))
    try:
        await page.wait_for_selector('[role="alert"]:has-text("available"), .success, [class*="success"]',
                                     timeout=3000)
        log("[email] 邮箱可用提示出现")
    except Exception:
        pass

    # 填 password
    await _fast_fill(
        page,
        ['input[type="password"]', 'input[name="password"]'],
        PASSWORD, "password"
    )

    await page.wait_for_timeout(_random.randint(1500, 2500))
    await page.screenshot(path=f"/tmp/replit_step1_{USERNAME}.png")

    # 探针：检测验证码类型
    try:
        probe = await page.evaluate(_JS_FULL_PROBE)
        iframes = probe.get("iframes", [])
        rc_token = probe.get("rcToken") or ""
        cf_token = probe.get("cfToken") or ""
        any_rc = bool(extract_recaptcha_sitekey(iframes)) or any("recaptcha" in f.get("src","") for f in iframes)
        any_ts = bool(extract_turnstile_sitekey(iframes)) or any("challenges.cloudflare.com" in f.get("src","") for f in iframes)
        log(f"[probe] iframes={len(iframes)} rc={bool(rc_token)} cf={bool(cf_token)} any_rc={any_rc} any_ts={any_ts}")
        for fr in iframes:
            log(f"  iframe: {fr.get('src','')[:100]}")
    except Exception as e:
        log(f"[probe] 异常: {e}")
        rc_token, cf_token, any_rc, any_ts = "", "", False, False

    # ── 核心策略 v7.27：使用 auto-token（invisible Enterprise 不展示 bframe/音频）
    # Replit 使用 invisible Enterprise 模式，无 bframe/checkbox challenge。
    # 策略：让 Enterprise JS 在充分的行为积累后自动生成 token，尽量提高 score。
    # 音频挑战作为 fallback：仅当 auto-token 未出现时尝试。
    if any_rc and not rc_token:
        log("[captcha] Enterprise invisible: 等待 auto-token (max 20s)…")
        rc_token, cf_token = await _wait_for_token(page, max_s=20)

    if any_rc and not rc_token:
        # Fallback: 尝试音频挑战（针对 v2 widget 模式）
        log("[captcha] 无 auto-token，尝试 checkbox+音频作为 fallback")
        audio_token = await solve_recaptcha_audio(page) or ""
        if audio_token:
            log(f"[captcha] ✅ 音频通过 token={len(audio_token)}chars")
            rc_token = audio_token
            # React-aware token 注入
            try:
                await page.evaluate("""
                    (token) => {
                        const el = document.querySelector('[name="recaptchaToken"]');
                        if (!el) return;
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                        setter.call(el, token);
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                """, rc_token)
                log("[captcha] ✓ audio token 已注入 recaptchaToken 字段")
            except Exception as e:
                log(f"[captcha] token 注入异常(忽略): {e}")
        else:
            log("[captcha] 音频 fallback 失败，用现有 token 提交（或空提交）")
            rc_token, cf_token = await _wait_for_token(page, max_s=8)
    elif not rc_token and not cf_token:
        log("[captcha] 等待 Enterprise 自动 token (max 15s)...")
        rc_token, cf_token = await _wait_for_token(page, max_s=15)

    # ── Turnstile 额外等待（无付费服务）
    if any_ts and not cf_token:
        log("[Turnstile] 等待额外 10s...")
        rc_token, cf_token = await _wait_for_token(page, max_s=10, label="_ts")

    # 提交前：延长等待 5-8s（给 reCAPTCHA Enterprise 更多行为评分时间）
    await page.wait_for_timeout(_random.randint(5000, 8000))

    # 提交前 re-check（只更新 cf；rc 由音频挑战控制，不覆盖）
    try:
        final_probe = await page.evaluate(_JS_FULL_PROBE)
        cf_token_now = final_probe.get("cfToken") or ""
        if cf_token_now:
            cf_token = cf_token_now
        # rc 只在没有音频 token 时才从 DOM 读
        if not rc_token:
            rc_token_now = final_probe.get("rcToken") or ""
            if rc_token_now:
                rc_token = rc_token_now
        log(f"[submit] 最终 token: rc={len(rc_token)}chars cf={len(cf_token)}chars")
    except Exception:
        pass

    # 先注册 response 拦截器（在 click 前）避免漏掉快速响应
    _api_resp: dict = {}
    async def _on_response(resp):
        try:
            url = resp.url
            # 只捕获 Replit 主域名的 POST（排除 sp.replit.com Stripe analytics）
            if ("replit.com" in url and "sp.replit.com" not in url
                    and resp.request.method == "POST"):
                rb = await resp.body()
                _api_resp["url"]    = url
                _api_resp["status"] = resp.status
                _api_resp["body"]   = rb[:600].decode("utf-8", errors="replace")
        except Exception:
            pass
    page.on("response", _on_response)

    # 提交 Step1：先尝试正常 click（5s），若 disabled 则 force=True 强制点击
    submitted = False
    _submit_sels = [
        '[data-cy="signup-create-account"]',
        'button:has-text("Create Account")', 'button:has-text("Create account")',
        'button[type="submit"]', 'button:has-text("Continue")',
        'button:has-text("Next")', 'button:has-text("Sign up")',
        'button:has-text("Create")',
    ]
    for sel in _submit_sels:
        btn = page.locator(sel)
        if await btn.count():
            try:
                await btn.first.hover()
                await page.wait_for_timeout(_random.randint(300, 600))
            except Exception:
                pass
            try:
                await btn.first.click(timeout=6000)
                log(f"[step1] 提交(normal): {sel}")
                submitted = True
                break
            except Exception:
                # button disabled → force-click to bypass Enterprise front-end gating
                try:
                    await btn.first.click(force=True, timeout=3000)
                    log(f"[step1] 提交(force): {sel}")
                    submitted = True
                    break
                except Exception as fe:
                    log(f"[step1] force click 失败 ({sel}): {fe}")
    if not submitted:
        # 最后手段：JS 直接提交表单
        try:
            await page.evaluate("""
                () => {
                    const btn = document.querySelector('[data-cy="signup-create-account"]');
                    if (btn) { btn.removeAttribute('disabled'); btn.click(); return; }
                    const form = document.querySelector('form');
                    if (form) form.submit();
                }
            """)
            log("[step1] JS 强制提交")
            submitted = True
        except Exception as e:
            log(f"[step1] JS 提交失败: {e}")
    if not submitted:
        await page.keyboard.press("Enter")
        log("[step1] 回车提交")

    # 等待页面响应（最多 36s）— 用 response 拦截器看 Replit API 实际返回

    _err_keywords = ("invalid","incorrect","taken","already","unavailable","error","failed",
                     "too many","captcha","something went wrong","recaptcha")
    for _w in range(18):
        await page.wait_for_timeout(2000)
        cur_url = page.url
        if "signup" not in cur_url.lower():
            log(f"[step1-wait] 跳转: {cur_url[:80]}")
            break
        if _api_resp.get("status"):
            log(f"[step1-wait] API={_api_resp['status']} url={_api_resp.get('url','')[:80]}")
            log(f"[step1-wait] API body: {_api_resp.get('body','')[:300]}")
            break
        try:
            body_w = (await page.locator("body").inner_text())[:600].lower()
        except Exception:
            body_w = ""
        ok_hint = any(h in body_w for h in ("check your email","we sent","verify your email",
                                             "sent you","sent an email","verification email"))
        if ok_hint:
            log(f"[step1-wait] ✅ 成功提示 (round {_w+1})")
            break
        if any(k in body_w for k in _err_keywords):
            log(f"[step1-wait] ❌ 错误关键词 (round {_w+1}): {body_w[:100]}")
            break
        if _w % 5 == 4:
            log(f"[step1-wait] 等待 {(_w+1)*2}s...")
    else:
        log("[step1-wait] 36s 超时，仍在 signup")

    page.remove_listener("response", _on_response)

    await page.screenshot(path=f"/tmp/replit_after_step1_{USERNAME}.png")

    body = (await page.locator("body").inner_text())[:1000]
    url  = page.url
    log(f"[step1] body[0:400]: {body[:400].replace(chr(10),' ')}")
    log(f"[step1] url: {url[:100]}")

    if is_integrity_error(body):
        return "integrity_check_failed_after_step1"
    if is_captcha_invalid(body):
        return "captcha_token_invalid"

    SUCCESS_HINTS = ("verify your email","check your email","we sent","sent you",
                     "verification email","confirm your email","check for an email","sent an email")
    if any(h in body.lower() for h in SUCCESS_HINTS):
        log("[step1] ✅ 验证邮件已发送（单步表单）")
        return None

    if "signup" in url.lower():
        err_els = await page.locator(
            '[class*="error" i],[data-cy*="error"],[role="alert"],[class*="invalid" i]'
        ).all_text_contents()
        errs = [e.strip() for e in err_els if e.strip()]
        if errs:
            return "; ".join(errs[:3])

    return None

# ── Step 2 ────────────────────────────────────────────────────────────────────
async def fill_step2(page) -> str | None:
    ok = await _fast_fill(
        page,
        ['input[name="username"]', 'input[placeholder*="username" i]', '#username'],
        USERNAME, "username"
    )
    if not ok:
        return "signup_username_field_not_found"

    await page.wait_for_timeout(500)

    # Step2 reCAPTCHA 检测（如有，用音频解）
    try:
        probe2 = await page.evaluate(_JS_FULL_PROBE)
        rc2 = probe2.get("rcToken") or ""
        iframes2 = probe2.get("iframes", [])
        any_rc2 = bool(extract_recaptcha_sitekey(iframes2)) or any("recaptcha" in f.get("src","") for f in iframes2)
        if any_rc2 and not rc2:
            log("[step2] reCAPTCHA 检测 → 音频解算")
            rc2 = await solve_recaptcha_audio(page) or ""
            if rc2:
                log(f"[step2] ✅ 音频 token={len(rc2)}chars")
            else:
                log("[step2] 音频失败，继续提交")
    except Exception as e:
        log(f"[step2] reCAPTCHA 探针异常: {e}")

    await page.screenshot(path=f"/tmp/replit_step2_{USERNAME}.png")

    submitted = False
    for sel in [
        'button:has-text("Create Account")', 'button:has-text("Create account")',
        'button[type="submit"]', 'button:has-text("Sign up")',
        'button:has-text("Finish")', 'button:has-text("Continue")',
    ]:
        btn = page.locator(sel)
        if await btn.count():
            await btn.first.click()
            log(f"[step2] 提交: {sel}")
            submitted = True
            break
    if not submitted:
        await page.keyboard.press("Enter")
        log("[step2] 回车提交")

    await page.wait_for_timeout(6000)
    try:
        await page.wait_for_selector('[class*="error" i],[data-cy*="error"],[role="alert"]', timeout=3000)
    except Exception:
        pass

    body = (await page.locator("body").inner_text())[:500]
    log(f"[step2] body[0:200]: {body[:200].replace(chr(10),' ')}")
    if is_integrity_error(body): return "integrity_check_failed_after_step2"
    if is_captcha_invalid(body): return "captcha_token_invalid"

    cur_url = page.url
    log(f"[step2] url: {cur_url[:100]}")
    await page.screenshot(path=f"/tmp/replit_after_{USERNAME}.png")

    if any(x in cur_url.lower() for x in ("verify","confirm","check-email","dashboard","home","@")):
        return None
    if "signup" in cur_url.lower():
        err_els = await page.locator('[class*="error" i],[data-cy*="error"],[role="alert"]').all_text_contents()
        errs = [e.strip() for e in err_els if e.strip()]
        if errs:
            return "; ".join(errs[:3])
        await page.wait_for_timeout(4000)
        if "signup" not in page.url.lower():
            return None
        return "signup_still_on_form_no_redirect"
    return None

# ── Canvas 2D 噪声脚本 ────────────────────────────────────────────────────────
_CANVAS_NOISE_JS = """
(() => {
    const _oGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {
        const d = _oGetImageData.call(this, x, y, w, h);
        for (let i = 0; i < d.data.length; i += 4) {
            d.data[i]   = Math.min(255, Math.max(0, d.data[i]   + (Math.random() > .5 ? 1 : -1)));
            d.data[i+1] = Math.min(255, Math.max(0, d.data[i+1] + (Math.random() > .5 ? 1 : -1)));
            d.data[i+2] = Math.min(255, Math.max(0, d.data[i+2] + (Math.random() > .5 ? 1 : -1)));
        }
        return d;
    };
    const _oToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, q) {
        const ctx2 = this.getContext('2d');
        if (ctx2 && this.width && this.height) {
            try {
                const img = ctx2.getImageData(0, 0, this.width, this.height);
                for (let i = 0; i < img.data.length; i += 4)
                    img.data[i] = Math.min(255, Math.max(0, img.data[i] + (Math.random() > .5 ? 1 : -1)));
                ctx2.putImageData(img, 0, 0);
            } catch(e) {}
        }
        return _oToDataURL.call(this, type, q);
    };
})();
"""

# ── 单次 browser attempt ──────────────────────────────────────────────────────
async def attempt_register(pw_module, proxy_cfg, stealth_fn, exit_ip: str) -> dict:
    result = {"ok": False, "phase": "init", "error": "", "exit_ip": exit_ip}

    browser = await pw_module.chromium.launch(
        headless=HEADLESS, proxy=proxy_cfg,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
              "--disable-blink-features=AutomationControlled", "--disable-web-security"],
    )
    ctx  = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-US", user_agent=UA)
    page = await ctx.new_page()

    # playwright_stealth 注入（chrome_runtime=True + webgl_vendor=True）
    if stealth_fn:
        try:
            await stealth_fn(page)
        except Exception as e:
            log(f"stealth 注入失败: {e}")

    # Canvas 2D 噪声（独立注入，stealth 不覆盖）
    try:
        await page.add_init_script(_CANVAS_NOISE_JS)
        log("Canvas 2D 噪声注入 ✓")
    except Exception as e:
        log(f"Canvas 噪声注入失败: {e}")

    try:
        result["phase"] = "navigate"
        # v7.27：pre-navigation warmup —— 先访问可信站点建立浏览历史
        # Google→GitHub→replit.com 首页→signup，让 Enterprise JS 看到真实浏览历史
        try:
            log("[pre-nav] 访问 google.com 建立会话历史…")
            await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(_random.randint(2500, 4000))
            log("[pre-nav] 访问 github.com…")
            await page.goto("https://github.com", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(_random.randint(2000, 3500))
        except Exception as e:
            log(f"[pre-nav] 异常(忽略): {e}")

        log("打开 replit.com/signup ...")
        await page.goto("https://replit.com/signup", wait_until="domcontentloaded", timeout=45000)

        # 页面加载后立即做 warmup（并发，让 reCAPTCHA 采集行为）
        warmup_task = asyncio.create_task(_human_warmup(page))

        t0 = await page.title()
        b0 = (await page.locator("body").inner_text())[:400]
        log(f"页面标题: {t0!r}")
        if is_cf_blocked(t0, b0):
            result["error"] = "signup_cf_ip_banned"
            warmup_task.cancel()
            await browser.close(); return result
        if is_integrity_error(b0):
            result["error"] = "integrity_check_failed_on_load"
            warmup_task.cancel()
            await browser.close(); return result

        cf_err = await wait_cf(page)
        if cf_err:
            result["error"] = cf_err
            warmup_task.cancel()
            await browser.close(); return result

        # 等 warmup 完成后再继续
        await warmup_task

        result["phase"] = "click_email_btn"
        await page.wait_for_timeout(500)
        for sel in [
            'button:has-text("Email & password")', 'button:has-text("Continue with email")',
            'button:has-text("Use email")', 'button:has-text("Email")',
            '[data-cy="email-signup"]',
        ]:
            btn = page.locator(sel)
            if await btn.count():
                await btn.first.click()
                log(f"点击 Email 按钮: {sel}")
                await page.wait_for_timeout(_random.randint(2800, 4200))
                break
        else:
            log("未找到 Email 按钮 → 表单可能已直接展示")

        result["phase"] = "step1_wait"
        try:
            await page.wait_for_selector(
                'input[name="email"], input[type="email"], input[placeholder*="email" i]',
                timeout=15000
            )
        except Exception:
            t2 = await page.title(); b2 = (await page.locator("body").inner_text())[:300]
            if is_cf_blocked(t2, b2):
                result["error"] = "signup_cf_ip_banned"
            else:
                result["error"] = "signup_email_field_timeout"
            await browser.close(); return result

        result["phase"] = "fill_step1"
        err1 = await fill_step1(page)
        if err1:
            result["error"] = err1
            await browser.close(); return result

        if is_integrity_error((await page.locator("body").inner_text())[:300]):
            result["error"] = "integrity_check_failed_after_step1"
            await browser.close(); return result

        result["phase"] = "step2_wait"
        step2_appeared = False
        try:
            await page.wait_for_selector(
                'input[name="username"], input[placeholder*="username" i], #username',
                timeout=35000
            )
            log("Step2 username 字段出现")
            step2_appeared = True
        except Exception:
            log("Step2 username 字段 35s 未出现")

        if not step2_appeared:
            body_check = (await page.locator("body").inner_text())[:500]
            url_check = page.url
            log(f"[step2-miss] url={url_check[:80]} body={body_check[:150].replace(chr(10),' ')}")
            SUCCESS_HINTS = ("verify your email","check your email","we sent","sent you",
                             "verification email","confirm your email","sent an email")
            if any(h in body_check.lower() for h in SUCCESS_HINTS):
                log("[step2-miss] 实际是单步表单，已发送验证邮件 ✅")
                result["ok"] = True
                result["phase"] = "verify_email_sent"
                await browser.close(); return result
            result["error"] = "signup_username_field_missing"
            await browser.close(); return result

        result["phase"] = "fill_step2"
        err2 = await fill_step2(page)
        if err2:
            result["error"] = err2
            if err2 in ("signup_username_field_not_found",):
                pass
            await browser.close(); return result

        if is_integrity_error((await page.locator("body").inner_text())[:300]):
            result["error"] = "integrity_check_failed_at_step2"
            await browser.close(); return result

        result["ok"] = True
        result["phase"] = "done"
        log("✅ 注册完成（验证邮件发送阶段）")

    except Exception as e:
        log(f"attempt 异常: {e}")
        result["error"] = str(e)
    finally:
        try:
            await browser.close()
        except Exception:
            pass

    return result

async def get_exit_ip(pw_module, proxy_cfg) -> str:
    try:
        br = await pw_module.chromium.launch(headless=True, proxy=proxy_cfg,
                args=["--no-sandbox","--disable-dev-shm-usage"])
        try:
            ctx  = await br.new_context()
            page = await ctx.new_page()
            await page.goto("https://api.ipify.org/?format=json", timeout=20000)
            data = json.loads(await page.locator("body").inner_text())
            return data.get("ip", "")
        finally:
            await br.close()
    except Exception as e:
        log(f"get_exit_ip 异常: {e}"); return ""

# ── 主流程 ────────────────────────────────────────────────────────────────────
async def run() -> dict:
    final = {"ok": False, "phase": "init", "error": "", "exit_ip": ""}
    proxy_cfg = {"server": PROXY} if PROXY else None

    log("v7.27b — 恢复 auto-token | pre-nav 浏览历史 | 40-55s 行为 warmup | 排除Stripe | force提交 | email等待")

    stealth_fn = None
    pw_ctx_fn  = None

    # playwright + stealth ONLY（patchright 不通过 integrity check，已弃用）
    try:
        from playwright.async_api import async_playwright as _apw
        pw_ctx_fn = _apw
        try:
            from playwright_stealth import Stealth
            stealth_fn = Stealth(
                chrome_runtime=True,
                webgl_vendor=True,
            ).apply_stealth_async
            log("playwright + stealth (chrome_runtime=True, webgl_vendor=True)")
        except ImportError:
            stealth_fn = None
            log("playwright（无 stealth 库，integrity check 风险高）")
    except ImportError:
        final["error"] = "playwright 未安装"
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
            res = await attempt_register(pw, proxy_cfg, stealth_fn, final["exit_ip"])
        res["exit_ip"] = final["exit_ip"]
        final = res
        if res["ok"]:
            break
        if res["error"] in ("signup_cf_js_challenge_timeout", "signup_cf_ip_banned"):
            log(f"CF 失败({res['error']}) → 返回外层换端口")
            break
        if res["error"] not in INTEGRITY_ERRORS:
            break
        log(f"integrity 失败({res['error']}) → 新 browser 重试")
        await asyncio.sleep(2)

    return final

if __name__ == "__main__":
    import asyncio
    res = asyncio.run(run())
    print(json.dumps(res))
