#!/usr/bin/env python3
"""
replit_register.py — Replit 注册表单自动化 v7.3
核心升级：音频挑战绕过 reCAPTCHA（完全免费，无需任何付费 API key）
策略：
  Layer 1: patchright 指纹伪装 + checkbox 自动通过（无挑战最优情况）
  Layer 2: 浏览器内部音频挑战（进 iframe → 点音频 → ffmpeg MP3→WAV → Google 免费 STT → 填答案）
           token 在真实 browser session 内生成 → Replit 服务端验证通过
  Layer 3: CapSolver（仅当 CAPSOLVER_KEY 已设置且前两层均失败时作后备）
删除：一切付费/外部 token 注入方案作为主路径
"""
import sys, json, asyncio, os, time, subprocess, urllib.request

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
        n: e.name, v: e.value
    }));
    var iframes = Array.from(document.querySelectorAll('iframe')).map(e=>({
        src: e.src,
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
    import urllib.parse
    for fr in iframes:
        src = fr.get("src", "")
        if "google.com/recaptcha" in src or "recaptcha/api" in src:
            qs = urllib.parse.urlparse(src).query
            params_qs = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
            key = params_qs.get("k") or params_qs.get("sitekey")
            if key:
                log(f"[reCAPTCHA] siteKey 提取: {key}")
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

# ══════════════════════════════════════════════════════════════════════════════
# 音频挑战绕过核心（Layer 2 — 完全免费）
# ══════════════════════════════════════════════════════════════════════════════

def _mp3_to_wav(mp3_bytes: bytes) -> bytes | None:
    """ffmpeg: MP3 → WAV (16kHz 单声道)，用于 Google 免费 STT。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
            input=mp3_bytes, capture_output=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
        log(f"[audio] ffmpeg 错误: {result.stderr[:200].decode(errors='replace')}")
        return None
    except FileNotFoundError:
        log("[audio] ffmpeg 未找到，尝试安装…")
        try:
            subprocess.run(["apt-get", "install", "-y", "ffmpeg"],
                           capture_output=True, timeout=90)
            result = subprocess.run(
                ["ffmpeg", "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
                input=mp3_bytes, capture_output=True, timeout=30,
            )
            return result.stdout if result.returncode == 0 else None
        except Exception as e:
            log(f"[audio] ffmpeg 安装失败: {e}"); return None
    except Exception as e:
        log(f"[audio] ffmpeg 异常: {e}"); return None


def _google_stt_free(wav_bytes: bytes) -> str | None:
    """
    调用 Google 免费语音识别（此 key 内嵌于 Android/Chrome 源码，无配额限制，无需注册）。
    输入: 16kHz 单声道 WAV PCM；返回识别文本（全小写），失败返回 None。
    """
    # 内置于 Chrome 的公开 key，SpeechRecognition 库同样使用此 key
    URL = (
        "https://www.google.com/speech-api/v2/recognize"
        "?output=json&lang=en-US&key=AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw"
    )
    try:
        req = urllib.request.Request(
            URL, data=wav_bytes,
            headers={"Content-Type": "audio/l16; rate=16000"},
        )
        resp = urllib.request.urlopen(req, timeout=30)
        text = resp.read().decode()
        log(f"[audio] Google STT 原始响应: {text[:300]}")
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                results = data.get("result", [])
                if results:
                    transcript = results[0]["alternative"][0]["transcript"]
                    return transcript.strip().lower()
            except Exception:
                pass
        log("[audio] Google STT 无识别结果")
        return None
    except Exception as e:
        log(f"[audio] Google STT 异常: {e}"); return None


def _whisper_stt_fallback(mp3_bytes: bytes) -> str | None:
    """
    Whisper tiny 本地推理（Google STT 失败时的后备，完全离线免费）。
    首次调用会自动下载 ~75MB tiny 模型。
    """
    import tempfile, os as _os
    try:
        import whisper as _whisper
    except ImportError:
        try:
            log("[audio] 安装 openai-whisper…")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "openai-whisper"],
                timeout=120,
            )
            import whisper as _whisper
        except Exception as e:
            log(f"[audio] whisper 安装失败: {e}"); return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(mp3_bytes); fname = f.name
        model = _whisper.load_model("tiny")
        result = model.transcribe(fname, language="en")
        _os.unlink(fname)
        text = result.get("text", "").strip().lower()
        log(f"[audio] Whisper 识别结果: '{text}'")
        return text if text else None
    except Exception as e:
        log(f"[audio] Whisper 推理异常: {e}"); return None


async def solve_recaptcha_audio(page) -> str | None:
    """
    在真实浏览器 session 内部通过音频挑战解算 reCAPTCHA v2。
    完全免费，无需外部 API key：
      ffmpeg（MP3→WAV） + Google 免费 STT / Whisper 离线
    token 在真实 browser session 里生成 → Replit 服务端验证必通过。
    """
    log("[audio] ▶ 开始音频挑战解算")

    # ── 1. 找 checkbox iframe (anchor) ─────────────────────────────────────────
    checkbox_frame = None
    for _w in range(15):
        for f in page.frames:
            if "recaptcha" in f.url and "anchor" in f.url:
                checkbox_frame = f; break
        if checkbox_frame: break
        await page.wait_for_timeout(500)

    if not checkbox_frame:
        log("[audio] 未找到 reCAPTCHA checkbox iframe (anchor)")
        return None

    # ── 2. 点击 checkbox ───────────────────────────────────────────────────────
    try:
        cb = checkbox_frame.locator("#recaptcha-anchor")
        if await cb.count():
            await cb.click()
            log("[audio] 点击 checkbox")
            await page.wait_for_timeout(2500)
    except Exception as e:
        log(f"[audio] 点击 checkbox 异常: {e}")

    # ── 3. 检查是否直接通过（无需挑战）─────────────────────────────────────────
    async def _read_token() -> str:
        for js in [
            "() => { var el = document.querySelector('#g-recaptcha-response,[name=\"g-recaptcha-response\"],[name=\"recaptchaToken\"]'); return el?el.value:''; }",
        ]:
            try:
                t = await page.evaluate(js)
                if t: return t
            except Exception: pass
        # 尝试从 checkbox iframe 内读取
        try:
            t = await checkbox_frame.evaluate(
                "() => { var el = document.querySelector('#g-recaptcha-response'); return el?el.value:''; }"
            )
            if t: return t
        except Exception: pass
        return ""

    token = await _read_token()
    if token:
        log(f"[audio] ✅ checkbox 直接通过，token 长度={len(token)}")
        return token

    # ── 4. 等待 challenge iframe (bframe) ──────────────────────────────────────
    challenge_frame = None
    for _w in range(20):
        for f in page.frames:
            if "recaptcha" in f.url and "bframe" in f.url:
                challenge_frame = f; break
        if challenge_frame: break
        await page.wait_for_timeout(500)

    if not challenge_frame:
        token = await _read_token()
        if token:
            log(f"[audio] ✅ 无 bframe，已有 token 长度={len(token)}")
            return token
        log("[audio] 未找到 challenge iframe (bframe)")
        return None

    # ── 5. 点击音频按钮 ────────────────────────────────────────────────────────
    audio_btn = challenge_frame.locator("#recaptcha-audio-button, .rc-button-audio")
    if not await audio_btn.count():
        log("[audio] 未找到音频按钮，截图诊断")
        await page.screenshot(path=f"/tmp/replit_audio_nobtn_{USERNAME}.png")
        return None
    await audio_btn.first.click()
    log("[audio] 点击音频按钮")
    await page.wait_for_timeout(2500)

    # ── 6. 获取音频 URL ────────────────────────────────────────────────────────
    audio_url: str | None = None
    for _w in range(12):
        # 方式 A: #audio-source[src]
        try:
            el = challenge_frame.locator("#audio-source")
            if await el.count():
                u = await el.get_attribute("src")
                if u: audio_url = u; break
        except Exception: pass
        # 方式 B: .rc-audiochallenge-download-link[href]
        try:
            el = challenge_frame.locator(".rc-audiochallenge-download-link")
            if await el.count():
                u = await el.get_attribute("href")
                if u: audio_url = u; break
        except Exception: pass
        await page.wait_for_timeout(500)

    if not audio_url:
        log("[audio] 未获取到音频 URL，截图诊断")
        await page.screenshot(path=f"/tmp/replit_audio_nourl_{USERNAME}.png")
        return None
    log(f"[audio] 音频 URL: {audio_url[:100]}")

    # ── 7. 下载 MP3 ────────────────────────────────────────────────────────────
    try:
        req = urllib.request.Request(audio_url, headers={"User-Agent": UA})
        mp3_bytes = urllib.request.urlopen(req, timeout=30).read()
        log(f"[audio] 下载 MP3 {len(mp3_bytes)} bytes")
    except Exception as e:
        log(f"[audio] 下载 MP3 失败: {e}"); return None

    # ── 8. 语音识别（Google 免费 STT 优先，Whisper 离线备用）───────────────────
    wav_bytes = _mp3_to_wav(mp3_bytes)
    transcript: str | None = None
    if wav_bytes:
        transcript = _google_stt_free(wav_bytes)
    if not transcript:
        log("[audio] Google STT 失败，尝试 Whisper 离线推理")
        transcript = _whisper_stt_fallback(mp3_bytes)
    if not transcript:
        log("[audio] 所有 STT 方案均失败"); return None
    log(f"[audio] 最终识别结果: '{transcript}'")

    # ── 9. 填写答案 ────────────────────────────────────────────────────────────
    answer_input = challenge_frame.locator("#audio-response")
    if not await answer_input.count():
        log("[audio] 未找到 #audio-response 输入框"); return None
    await answer_input.click()
    await answer_input.fill(transcript)
    await page.wait_for_timeout(400)

    # ── 10. 点击 Verify ────────────────────────────────────────────────────────
    verify_btn = challenge_frame.locator("#recaptcha-verify-button, .rc-audio-verify-button")
    if await verify_btn.count():
        await verify_btn.first.click()
        log("[audio] 点击 Verify")
    else:
        await answer_input.press("Enter")
        log("[audio] Enter 提交")
    await page.wait_for_timeout(3500)

    # ── 11. 读取 token ──────────────────────────────────────────────────────────
    token = await _read_token()
    if token:
        log(f"[audio] 🎉 解算成功！token 长度={len(token)}")
        return token

    log("[audio] Verify 后未读到 token（答案可能错误），截图诊断")
    await page.screenshot(path=f"/tmp/replit_audio_fail_{USERNAME}.png")
    return None

# ══════════════════════════════════════════════════════════════════════════════
# CapSolver 后备（仅当 CAPSOLVER_KEY 存在且音频失败时）
# ══════════════════════════════════════════════════════════════════════════════

def capsolver_solve_recaptcha_v2(api_key: str, site_key: str, page_url: str) -> str | None:
    """CapSolver 外部解算（后备，需要付费 key）。"""
    try:
        payload = json.dumps({
            "clientKey": api_key,
            "task": {
                "type": "ReCaptchaV2TaskProxyless",
                "websiteURL": page_url,
                "websiteKey": site_key,
                "isInvisible": False,
            }
        }).encode()
        req = urllib.request.Request(
            "https://api.capsolver.com/createTask", data=payload,
            headers={"Content-Type": "application/json"},
        )
        r = urllib.request.urlopen(req, timeout=15)
        resp = json.loads(r.read())
        task_id = resp.get("taskId")
        if not task_id:
            log(f"CapSolver createTask 失败: {resp.get('errorDescription','unknown')}")
            return None
        log(f"CapSolver taskId={task_id}，轮询…")
        for _ in range(40):
            time.sleep(3)
            req2 = urllib.request.Request(
                "https://api.capsolver.com/getTaskResult",
                data=json.dumps({"clientKey": api_key, "taskId": task_id}).encode(),
                headers={"Content-Type": "application/json"},
            )
            r2 = urllib.request.urlopen(req2, timeout=10)
            resp2 = json.loads(r2.read())
            if resp2.get("status") == "ready":
                token = resp2.get("solution", {}).get("gRecaptchaResponse", "")
                log(f"CapSolver 解算完成 token 长度={len(token)}")
                return token
            if resp2.get("status") == "failed":
                log(f"CapSolver 失败: {resp2.get('errorDescription','')}")
                return None
        log("CapSolver 轮询超时"); return None
    except Exception as e:
        log(f"CapSolver 异常: {e}"); return None

# ── 出口 IP ───────────────────────────────────────────────────────────────────
async def get_exit_ip(pw_module, proxy_cfg) -> str:
    try:
        browser = await pw_module.chromium.launch(headless=True, proxy=proxy_cfg,
            args=["--no-sandbox"])
        page = await (await browser.new_context()).new_page()
        await page.goto("https://api.ipify.org?format=json", timeout=15000)
        data = json.loads(await page.locator("body").inner_text())
        await browser.close()
        return data.get("ip", "")
    except Exception:
        return ""

# ── wait_cf ───────────────────────────────────────────────────────────────────
async def wait_cf(page, use_patchright: bool) -> str | None:
    """等待 Cloudflare JS challenge 自动通过（最多 90s）。"""
    log("检测 CF challenge…")
    for _r in range(15):   # 2s × 15 = 30s，超时则报错让外层换代理端口
        title = await page.title()
        body  = (await page.locator("body").inner_text())[:400]
        if is_cf_blocked(title, body):
            return "signup_cf_ip_banned"
        if "just a moment" in title.lower() or "checking your browser" in body.lower()                 or "enable javascript" in body.lower():
            if _r % 5 == 0:
                log(f"  CF JS challenge 等待 {(_r+1)*2}s…")
            await page.wait_for_timeout(2000)
            continue
        break
    else:
        # 90s 后仍在 CF 页面 → 该代理 IP 被 CF 硬封
        title2 = await page.title()
        if "just a moment" in title2.lower():
            log("CF JS challenge 30s 超时，代理 IP 可能被 CF 硬封")
            return "signup_cf_js_challenge_timeout"
    return None

# ── Step 1：填写 email + password + 解 captcha ────────────────────────────────
_last_token: dict = {"rc": None, "cf": None}

async def fill_step1(page) -> str | None:
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

    # 探针：检测验证码类型
    probe = {}
    rc_sitekey = None
    try:
        probe = await page.evaluate(_JS_FULL_PROBE)
        iframes = probe.get("iframes", [])
        cf_token = probe.get("cfToken") or ""
        rc_token = probe.get("rcToken") or ""
        log(f"[探针] iframes={len(iframes)} cfToken={len(cf_token)}chars rcToken={len(rc_token)}chars")
        for fr in iframes:
            log(f"  iframe: {fr.get('src','')[:120]}")

        rc_sitekey = extract_recaptcha_sitekey(iframes)
        any_rc = bool(rc_sitekey) or any("recaptcha" in fr.get("src","") for fr in iframes)
        any_ts = any("challenges.cloudflare.com" in fr.get("src","") for fr in iframes)
        log(f"[探针] reCAPTCHA={any_rc} Turnstile={any_ts}")
    except Exception as e:
        log(f"DOM探针异常: {e}")
        any_rc = False

    rc_token = probe.get("rcToken") or ""

    # ── Layer 2: 音频挑战（免费，IN-browser，token 100% 有效）────────────────
    if not rc_token:
        log("[captcha] 尝试音频挑战（Layer 2 — 免费）")
        rc_token = await solve_recaptcha_audio(page) or ""
        if rc_token:
            log(f"[captcha] ✅ 音频挑战成功，token 长度={len(rc_token)}")
        else:
            log("[captcha] 音频挑战未获得 token")

    # ── Layer 3: CapSolver 后备（仅当有 key 且前两层失败）───────────────────
    if not rc_token and rc_sitekey and CAPSOLVER_KEY:
        log("[captcha] Layer 3: CapSolver 后备解算")
        solved = capsolver_solve_recaptcha_v2(CAPSOLVER_KEY, rc_sitekey, "https://replit.com/signup")
        if solved:
            # 注入 token（外部生成，成功率较低）
            _JS_INJECT = f"""() => {{
                var els = document.querySelectorAll('[name="g-recaptcha-response"], #g-recaptcha-response, [name="recaptchaToken"]');
                els.forEach(el => {{ el.value = {json.dumps(solved)}; }});
                try {{
                    var cb = window.__recaptchaCallback || window.__onCaptchaToken;
                    if (typeof cb === 'function') cb({json.dumps(solved)});
                }} catch(e) {{}}
                return els.length;
            }}"""
            n = await page.evaluate(_JS_INJECT)
            log(f"[captcha] CapSolver token 注入 {n} 个字段")
            rc_token = solved

    # token 对比日志
    prev_rc = _last_token["rc"]
    if rc_token:
        log(f"[token] 本次={rc_token[:40]}… 上次={prev_rc[:40] if prev_rc else 'None'} 相同={rc_token==prev_rc}")
        _last_token["rc"] = rc_token

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

    # 等待 React 渲染 captcha 错误元素（固定 5s 可能不够）
    try:
        await page.wait_for_selector(
            '[class*="error" i],[data-cy*="error"],[role="alert"]',
            timeout=3000
        )
    except Exception:
        pass

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

    # ── Step2 reCAPTCHA 检测与音频解算 ───────────────────────────────────────
    try:
        probe2 = await page.evaluate(_JS_FULL_PROBE)
        rc_token2 = probe2.get("rcToken") or ""
        iframes2  = probe2.get("iframes", [])
        any_rc2   = bool(extract_recaptcha_sitekey(iframes2)) or any(
            "recaptcha" in fr.get("src", "") for fr in iframes2
        )
        if any_rc2 and not rc_token2:
            log("[step2] 检测到 reCAPTCHA → 尝试音频解算")
            rc_token2 = await solve_recaptcha_audio(page) or ""
            if rc_token2:
                log(f"[step2] ✅ reCAPTCHA 音频解算成功 token={len(rc_token2)}chars")
            else:
                log("[step2] 音频解算失败，继续尝试提交")
    except Exception as e2rc:
        log(f"[step2] reCAPTCHA 探针异常: {e2rc}")

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

    # 额外等 React 渲染验证码错误
    try:
        await page.wait_for_selector(
            '[class*="error" i],[data-cy*="error"],[role="alert"]',
            timeout=3000
        )
    except Exception:
        pass

    body = (await page.locator("body").inner_text())[:500]
    log(f"Step2_body[0:200]: {body[:200].replace(chr(10),' ')}")
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

    # Canvas 2D 指纹噪声注入（独立于 stealth，防止 Replit integrity canvas 探针）
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
            const ctx = this.getContext('2d');
            if (ctx && this.width && this.height) {
                try {
                    const img = ctx.getImageData(0, 0, this.width, this.height);
                    for (let i = 0; i < img.data.length; i += 4)
                        img.data[i] = Math.min(255, Math.max(0, img.data[i] + (Math.random() > .5 ? 1 : -1)));
                    ctx.putImageData(img, 0, 0);
                } catch(e) {}
            }
            return _oToDataURL.call(this, type, q);
        };
        const _oToBlob = HTMLCanvasElement.prototype.toBlob;
        HTMLCanvasElement.prototype.toBlob = function(cb, type, q) {
            _oToBlob.call(this, cb, type, q);
        };
    })();
    """
    try:
        await page.add_init_script(_CANVAS_NOISE_JS)
        log("Canvas 2D 噪声脚本注入完成")
    except Exception as e:
        log(f"Canvas 噪声注入失败: {e}")

    _captured_reqs: list = []
    def _on_request(req):
        try:
            if "replit.com" in req.url and req.method == "POST":
                body = req.post_data or ""
                if any(k in body for k in ("email","captcha","turnstile","token","recaptcha")):
                    _captured_reqs.append(f"POST {req.url} body={body[:600]}")
        except Exception:
            pass
    page.on("request", _on_request)

    try:
        result["phase"] = "navigate"
        log("打开 replit.com/signup …")
        await page.goto("https://replit.com/signup", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1500)

        t0 = await page.title()
        b0 = (await page.locator("body").inner_text())[:400]
        log(f"初始页面标题: {t0!r}")
        if is_cf_blocked(t0, b0):
            result["error"] = "signup_cf_ip_banned"
            await browser.close(); return result
        if is_integrity_error(b0):
            result["error"] = "integrity_check_failed_on_load"
            await browser.close(); return result

        cf_err = await wait_cf(page, use_patchright)
        if cf_err:
            result["error"] = cf_err
            await browser.close(); return result

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

        result["phase"] = "step1_fill"
        step1_err = await fill_step1(page)
        for _req in _captured_reqs[-5:]:
            log(f"[intercept] {_req}")
        _captured_reqs.clear()

        if step1_err == "captcha_token_invalid":
            log("captcha_token_invalid → reload，重新音频解算…")
            try:
                await page.reload(wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(3000)
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
                    log("✅ 无 Step2，body 检测邮件已发送")
                    await browser.close(); return result
            except Exception:
                pass
            # signup_username_field_missing 前检测 captcha 错误，避免误判为成功
            try:
                body_cap = (await page.locator("body").inner_text())[:500]
                if is_captcha_invalid(body_cap):
                    log("Step2-wait body 检测到 captcha 错误 → captcha_token_invalid")
                    result["error"] = "captcha_token_invalid"
                    await browser.close(); return result
                await page.wait_for_timeout(3000)
                body_cap2 = (await page.locator("body").inner_text())[:500]
                if is_captcha_invalid(body_cap2):
                    log("Step2-wait 延迟检测到 captcha 错误 → captcha_token_invalid")
                    result["error"] = "captcha_token_invalid"
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

    log("v7.3 — 音频挑战 + playwright/stealth 优先（通过 integrity check）（完全免费：ffmpeg + Google STT / Whisper）")
    log(f"CapSolver 后备: {'已配置（仅在音频失败时使用）' if CAPSOLVER_KEY else '未配置（不影响音频方案）'}")

    stealth_fn = None
    use_patchright = False
    pw_ctx_fn = None

    # playwright+stealth 优先（通过 integrity check）；patchright 作后备
    try:
        from playwright.async_api import async_playwright as _apw
        pw_ctx_fn = _apw
        try:
            from playwright_stealth import Stealth
            stealth_fn = Stealth(
                chrome_runtime=True,   # 必须开启，否则 Replit 检测到缺失 chrome.runtime
                webgl_vendor=True,     # WebGL 指纹伪装
            ).apply_stealth_async
            log("使用 playwright + stealth（chrome_runtime=True / WebGL — 通过 integrity check）")
        except ImportError:
            stealth_fn = None
            log("playwright（无 stealth，integrity check 可能失败）")
    except ImportError:
        try:
            from patchright.async_api import async_playwright as _apw
            pw_ctx_fn = _apw
            use_patchright = True
            log("后备: patchright（注意 integrity check 可能不过）")
        except ImportError:
            final["error"] = "playwright/patchright 均未安装"
            return final

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
    # 注意：CF JS challenge 超时时直接返回错误，由外层（accounts.ts）换端口重试
    # 不在脚本内降级为直连（避免 VPS 主 IP 被 Replit rate-limit）
    for attempt in range(1, 4):
        proxy_tag = f"proxy={proxy_cfg['server']}" if proxy_cfg else "直连"
        log(f"browser attempt {attempt}/3 [{proxy_tag}]")
        async with pw_ctx_fn() as pw:
            res = await attempt_register(pw, proxy_cfg, use_patchright, stealth_fn, final["exit_ip"])
        res["exit_ip"] = final["exit_ip"]
        final = res
        if res["ok"]:
            break
        # CF 超时 / IP 封禁：立即返回，让外层换代理端口
        if res["error"] in ("signup_cf_js_challenge_timeout", "signup_cf_ip_banned"):
            log(f"CF 失败({res['error']}) → 返回外层换端口，不在本脚本内重试")
            break
        if res["error"] not in INTEGRITY_ERRORS:
            break
        log(f"integrity 失败({res['error']}) → 新 browser 重试（同代理）")
        await asyncio.sleep(2)

    return final

if __name__ == "__main__":
    res = asyncio.run(run())
    print(json.dumps(res))
