"""
captcha_solver.py — hCaptcha 多级求解器

优先级（全部免费 → 付费兜底）：
  1. 音频挑战 + 本地 Whisper STT  (免费, 完全离线)
  2. YesCaptcha API                (付费, 需 YESCAPTCHA_API_KEY)

用法（在 Playwright page 上下文中）：
    from captcha_solver import solve_hcaptcha
    solved = await solve_hcaptcha(page, log_fn=log)
"""
import asyncio
import base64
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx
from playwright.async_api import Page

# ── 配置 ─────────────────────────────────────────────────────────────────────
YESCAPTCHA_API_KEY = os.environ.get("YESCAPTCHA_API_KEY", "")
YESCAPTCHA_API_URL = "https://api.yescaptcha.com"
FFMPEG             = "/usr/bin/ffmpeg"
WHISPER_MODEL      = os.environ.get("WHISPER_MODEL", "base")   # tiny/base/small

# 延迟加载 Whisper 避免拖慢启动
_whisper_model = None


def _load_whisper():
    global _whisper_model
    if _whisper_model is None:
        import whisper as _w
        _whisper_model = _w.load_model(WHISPER_MODEL)
    return _whisper_model


def log(msg, level="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level.upper():5s}] {msg}", flush=True)


# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════

async def _get_sitekey(page: Page) -> str | None:
    sitekey = await page.evaluate("""() => {
        const el = document.querySelector('[data-sitekey]');
        if (el) return el.getAttribute('data-sitekey');
        const iframes = document.querySelectorAll('iframe');
        for (const f of iframes) {
            const src = f.src || '';
            const match = src.match(/sitekey=([a-f0-9-]{36,})/);
            if (match) return match[1];
        }
        return null;
    }""")
    if not sitekey:
        for frame in page.frames:
            m = re.search(r"sitekey=([a-f0-9-]{36,})", frame.url)
            if m:
                sitekey = m.group(1)
                break
    if not sitekey:
        sitekey = await page.evaluate("""() => {
            if (window.hcaptcha && window.hcaptcha._psts) {
                for (const k of Object.keys(window.hcaptcha._psts)) return k;
            }
            const scripts = document.querySelectorAll('script');
            for (const s of scripts) {
                const m = s.textContent.match(/sitekey['":\\s]+['"]([a-f0-9-]{36,})['"]/);
                if (m) return m[1];
            }
            return null;
        }""")
    return sitekey


def _hcap_frames(page: Page):
    return [f for f in page.frames if "hcaptcha.com" in f.url]


async def _inject_token(page: Page, token: str, log_fn=log) -> bool:
    success = await page.evaluate("""(token) => {
        let ok = false;
        const textareas = document.querySelectorAll(
            'textarea[name="h-captcha-response"],textarea[name="g-recaptcha-response"]');
        for (const ta of textareas) { ta.value = token; ta.innerHTML = token; ok = true; }
        const inputs = document.querySelectorAll('input[name="h-captcha-response"]');
        for (const inp of inputs) { inp.value = token; ok = true; }
        if (window.hcaptcha) {
            try {
                const wids = Object.keys(window.hcaptcha._psts || {});
                for (const wid of wids) window.hcaptcha.setResponse(token, wid);
                ok = true;
            } catch(e) {}
        }
        if (window.onHCaptchaSuccess) { window.onHCaptchaSuccess(token); return true; }
        const el = document.querySelector('[data-callback]');
        if (el) {
            const cb = el.getAttribute('data-callback');
            if (window[cb]) { window[cb](token); return true; }
        }
        return ok;
    }""", token)
    if not success:
        for frame in page.frames:
            if "hcaptcha.com" in frame.url:
                try:
                    await frame.evaluate("""(token) => {
                        const ta = document.querySelector('textarea[name="h-captcha-response"]');
                        if (ta) ta.value = token;
                        window.parent.postMessage(JSON.stringify({
                            source: 'hcaptcha', label: 'challenge-closed',
                            contents: {event: 'challenge-passed',
                                       response: token, expiration: 120}
                        }), '*');
                    }""", token)
                    success = True
                except Exception:
                    pass
    if success:
        log_fn("Token 已注入页面", "ok")
    return success


async def _poll_hcap_token(page: Page, timeout: int = 10) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        token = await page.evaluate("""() => {
            if (typeof hcaptcha === 'undefined') return null;
            try { const r = hcaptcha.getResponse(); return r && r.length > 20 ? r : null; }
            catch(e) { return null; }
        }""")
        if token:
            return token
        await asyncio.sleep(0.5)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 方案 1：音频挑战 + Whisper STT（完全免费）
# ══════════════════════════════════════════════════════════════════════════════

async def _solve_audio_whisper(page: Page, log_fn=log) -> bool:
    log_fn("【音频法】启动", "info")

    frames = _hcap_frames(page)
    if not frames:
        await asyncio.sleep(3)
        frames = _hcap_frames(page)
    if not frames:
        log_fn("【音频法】未找到 hCaptcha frames", "warn")
        return False

    # 点击音频按钮（在 challenge frame 里）
    AUDIO_SELECTORS = [
        'button[aria-label*="audio" i]',
        'button[class*="audio"]',
        '.challenge-switchtype',
        '[data-type="audio"]',
        'button[aria-label="switch to audio"]',
    ]
    clicked = False
    for frame in frames:
        for sel in AUDIO_SELECTORS:
            try:
                el = await frame.query_selector(sel)
                if el:
                    await el.click()
                    log_fn(f"【音频法】点击音频按钮: {sel}", "info")
                    clicked = True
                    await asyncio.sleep(3)
                    break
            except Exception:
                pass
        if clicked:
            break

    # 等待并提取音频 URL（含 blob）
    audio_path = None
    for frame in _hcap_frames(page):
        for attempt in range(15):
            await asyncio.sleep(1)
            try:
                u = await frame.evaluate(
                    "() => { const a = document.querySelector('audio'); "
                    "return a ? (a.src || a.currentSrc || null) : null; }"
                )
                if u and (u.startswith("http") or u.startswith("blob:")):
                    if u.startswith("blob:"):
                        data = await frame.evaluate(
                            "(url) => fetch(url).then(r=>r.arrayBuffer())"
                            ".then(b=>Array.from(new Uint8Array(b)))", u
                        )
                        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                        tmp.write(bytes(data))
                        tmp.close()
                        audio_path = tmp.name
                        log_fn(f"【音频法】blob 音频已保存 ({len(data)} bytes)", "ok")
                    else:
                        import urllib.request
                        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=20) as resp:
                            tmp.write(resp.read())
                        tmp.close()
                        audio_path = tmp.name
                        log_fn(f"【音频法】HTTP 音频已下载", "ok")
                    break
            except Exception as e:
                if attempt == 14:
                    log_fn(f"【音频法】提取音频失败: {e}", "warn")
        if audio_path:
            break

    if not audio_path:
        log_fn("【音频法】未找到音频元素", "warn")
        return False

    # ffmpeg 转 wav
    wav_path = audio_path + ".wav"
    try:
        subprocess.run(
            [FFMPEG, "-y", "-i", audio_path, "-ar", "16000", "-ac", "1",
             "-acodec", "pcm_s16le", wav_path],
            capture_output=True, timeout=20
        )
    except Exception as e:
        log_fn(f"【音频法】ffmpeg 错误: {e}", "warn")
        wav_path = audio_path

    # Whisper 转录
    try:
        model = await asyncio.get_event_loop().run_in_executor(None, _load_whisper)
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: model.transcribe(wav_path, language="en")
        )
        text = re.sub(r"[^a-z0-9\s]", "", result["text"].strip().lower()).strip()
        log_fn(f"【音频法】Whisper 识别: '{text}'", "ok")
    except Exception as e:
        log_fn(f"【音频法】Whisper 失败: {e}", "error")
        return False
    finally:
        for p in [audio_path, wav_path]:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass

    if not text:
        log_fn("【音频法】转录结果为空", "warn")
        return False

    # 提交答案
    submitted = False
    for frame in _hcap_frames(page):
        try:
            inp = await frame.query_selector('input[type="text"]')
            if inp:
                await inp.fill(text)
                await asyncio.sleep(0.3)
                for btn_sel in [
                    'button[type="submit"]', 'button:has-text("Submit")',
                    'button[class*="submit"]', '#submit-btn',
                ]:
                    btn = await frame.query_selector(btn_sel)
                    if btn:
                        await btn.click()
                        submitted = True
                        log_fn("【音频法】答案已提交", "ok")
                        break
                if not submitted:
                    await inp.press("Enter")
                    submitted = True
                break
        except Exception as e:
            log_fn(f"【音频法】提交异常: {e}", "warn")

    if not submitted:
        log_fn("【音频法】未找到提交入口", "warn")
        return False

    # 等待 token
    await asyncio.sleep(3)
    token = await _poll_hcap_token(page, timeout=8)
    if token:
        log_fn(f"【音频法】获取 token 成功 ({len(token)} chars)", "ok")
        await _inject_token(page, token, log_fn)
        return True

    # challenge 消失也视为成功
    remaining = _hcap_frames(page)
    challenge_frames = [f for f in remaining if "frame=challenge" in f.url]
    if not challenge_frames:
        log_fn("【音频法】challenge 已消失，视为通过", "ok")
        return True

    log_fn("【音频法】提交后 challenge 仍在", "warn")
    return False


# ══════════════════════════════════════════════════════════════════════════════
# 方案 2：YesCaptcha API（付费兜底）
# ══════════════════════════════════════════════════════════════════════════════

async def _create_yescaptcha_task(sitekey: str, page_url: str, api_key: str,
                                   log_fn=log) -> str | None:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{YESCAPTCHA_API_URL}/createTask",
            json={
                "clientKey": api_key,
                "task": {
                    "type": "HCaptchaTaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": sitekey,
                },
            },
        )
        data = resp.json()
        if data.get("errorId", 1) != 0:
            log_fn(f"YesCaptcha 创建任务失败: {data.get('errorDescription', data)}", "error")
            return None
        task_id = data.get("taskId")
        log_fn(f"YesCaptcha 任务: {task_id}", "info")
        return task_id


async def _get_yescaptcha_result(task_id: str, api_key: str, log_fn=log,
                                  timeout: int = 120) -> str | None:
    start = time.time()
    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() - start < timeout:
            await asyncio.sleep(5)
            resp = await client.post(
                f"{YESCAPTCHA_API_URL}/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
            )
            data = resp.json()
            if data.get("errorId", 1) != 0:
                log_fn(f"YesCaptcha 查询失败: {data.get('errorDescription', data)}", "error")
                return None
            if data.get("status") == "ready":
                token = data.get("solution", {}).get("gRecaptchaResponse")
                log_fn(f"YesCaptcha token 成功 ({len(token) if token else 0} chars)", "ok")
                return token
            log_fn(f"YesCaptcha 等待... ({int(time.time()-start)}s)", "dbg")
    log_fn("YesCaptcha 超时", "error")
    return None


async def _solve_yescaptcha(page: Page, log_fn=log) -> bool:
    api_key = os.environ.get("YESCAPTCHA_API_KEY", YESCAPTCHA_API_KEY)
    if not api_key:
        log_fn("未设置 YESCAPTCHA_API_KEY，跳过", "warn")
        return False

    log_fn("【YesCaptcha】启动", "info")
    sitekey = await _get_sitekey(page)
    if not sitekey:
        await asyncio.sleep(3)
        sitekey = await _get_sitekey(page)
    if not sitekey:
        log_fn("【YesCaptcha】无法提取 sitekey", "error")
        return False

    page_url = page.url
    log_fn(f"【YesCaptcha】sitekey={sitekey}", "info")

    for attempt in range(1, 3):
        log_fn(f"--- 尝试 {attempt}/2 ---", "info")
        task_id = await _create_yescaptcha_task(sitekey, page_url, api_key, log_fn)
        if not task_id:
            await asyncio.sleep(3)
            continue
        token = await _get_yescaptcha_result(task_id, api_key, log_fn)
        if not token:
            continue
        injected = await _inject_token(page, token, log_fn)
        if injected:
            await asyncio.sleep(2)
            challenge_frames = [
                f for f in page.frames
                if "hcaptcha.com" in f.url and "frame=challenge" in f.url
            ]
            if not challenge_frames:
                log_fn("【YesCaptcha】验证通过!", "ok")
                return True
            log_fn("token 注入后 challenge 仍在，重试...", "warn")

    log_fn("【YesCaptcha】求解失败", "error")
    return False


# ══════════════════════════════════════════════════════════════════════════════
# 统一入口
# ══════════════════════════════════════════════════════════════════════════════

async def solve_hcaptcha(page: Page, log_fn=log, use_audio: bool = True) -> bool:
    """
    多级 hCaptcha 求解器：
      1. 音频 + 本地 Whisper STT（免费）
      2. YesCaptcha API（付费，需 YESCAPTCHA_API_KEY）
    返回 True 表示验证已通过。
    """
    if use_audio:
        try:
            ok = await _solve_audio_whisper(page, log_fn)
            if ok:
                return True
            log_fn("音频法失败，尝试付费 API...", "warn")
        except Exception as e:
            log_fn(f"音频法异常: {e}", "warn")

    return await _solve_yescaptcha(page, log_fn)


# ══════════════════════════════════════════════════════════════════════════════
# Accessibility Cookie 支持（配合 hcap_accessibility.py 使用）
# ══════════════════════════════════════════════════════════════════════════════
import json as _json
from pathlib import Path as _Path

_COOKIE_FILE = _Path(__file__).parent / "hcap_cookie.json"


def load_accessibility_cookies() -> list[dict] | None:
    """加载保存的 hCaptcha Accessibility Cookie，不存在返回 None。"""
    if not _COOKIE_FILE.exists():
        return None
    try:
        data = _json.loads(_COOKIE_FILE.read_text())
        cookies = data.get("cookies", [])
        if cookies:
            return cookies
    except Exception:
        pass
    return None


async def apply_accessibility_cookie(context) -> bool:
    """
    将 hCaptcha Accessibility Cookie 注入 Playwright BrowserContext。
    在 new_context() 之后、page.goto() 之前调用。
    返回 True 表示 Cookie 已成功注入。
    """
    cookies = load_accessibility_cookies()
    if not cookies:
        return False
    try:
        await context.add_cookies(cookies)
        log(f"[ACC-COOKIE] 已注入 {len(cookies)} 个无障碍 Cookie", "info")
        return True
    except Exception as e:
        log(f"[ACC-COOKIE] Cookie 注入失败: {e}", "warn")
        return False
