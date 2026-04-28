#!/usr/bin/env python3
"""
replit_register.py — Replit 注册表单自动化 v7.29
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
import sys, json, asyncio, os, re, subprocess, urllib.request, random as _random

params   = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
EMAIL    = params.get("email", "")
USERNAME = params.get("username", "")
PASSWORD = params.get("password", "")
PROXY    = params.get("proxy", "")
UA       = params.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADLESS = params.get("headless", True)
USE_CDP  = params.get("use_cdp", False)
CDP_WS   = params.get("cdp_ws", "http://127.0.0.1:9222")

# v7.80: precheck-only mode. When True, fill_step1 will navigate + fill email +
# run the inline availability probe, then short-circuit BEFORE password fill /
# captcha solve / verification. Used by /api/admin/email-prescan to batch-mark
# the outlook pool as replit_used / replit_avail / replit_unknown so the actual
# registration picker hits known-good emails on the first try.
# Returns: {"ok": True, "phase": "precheck", "decision": "available|taken|unknown"}
PRECHECK_ONLY    = bool(params.get("precheck_only", False))
PRECHECK_MAX_S   = float(params.get("precheck_max_s", 6.0))
_PRECHECK_RESULT = None  # set inside fill_step1 by the probe block

# ── CDP shim ────────────────────────────────────────────────────────────────
# Lets attempt_register() reuse the headed-Chromium owned by browser-model
# (port 9222) instead of launching its own. Bypasses CF + Camoufox detection
# combo that fails on datacenter SOCKS5 from this VPS.
class _CDPBrowserShim:
    def __init__(self, real_browser):
        self._b = real_browser
        self._owned = []
        # v8.16: track pages opened on the *reused* broker default ctx so we can
        # close them on shim.close() — otherwise every registration leaves its
        # /signup tab open in the broker chromium, accumulating across runs and
        # eventually making connect_over_cdp time out at 180s while enumerating
        # 50+ targets. Root cause of all "Attempt N: timeout" cascades.
        self._owned_pages = []
        self._page_listeners = {}  # ctx_id -> handler (for cleanup safety)
    def __getattr__(self, name):
        return getattr(self._b, name)
    @property
    def contexts(self):
        return self._b.contexts
    async def new_context(self, **kw):
        # WORKAROUND: chromium contexts created via CDP do NOT inherit
        # --proxy-server flag. Reuse the broker default context (which has
        # proxy applied) when proxy isn't explicitly set.
        kw.pop("proxy", None)
        if self._b.contexts:
            ctx = self._b.contexts[0]
            try:
                ua = kw.get("user_agent")
                if ua:
                    await ctx.set_extra_http_headers({"User-Agent": ua})
            except Exception:
                pass
            # v8.16: record any page opened on the reused ctx, so close() can
            # close them — they are *our* pages even though the ctx is shared.
            cid = id(ctx)
            if cid not in self._page_listeners:
                def _track(p, _self=self):
                    try: _self._owned_pages.append(p)
                    except Exception: pass
                try:
                    ctx.on("page", _track)
                    self._page_listeners[cid] = _track
                except Exception:
                    pass
            return ctx
        ctx = await self._b.new_context(**kw)
        self._owned.append(ctx)
        return ctx
    async def new_page(self, **kw):
        ctx = await self.new_context(**kw)
        page = await ctx.new_page()
        # v8.16: also explicitly track pages created via shim.new_page on
        # the reused default ctx (on("page") handler covers ctx.new_page,
        # but be defensive against double-registration race).
        try:
            if page not in self._owned_pages:
                self._owned_pages.append(page)
        except Exception:
            pass
        return page
    async def close(self):
        # v8.16: first close pages opened on the reused broker default ctx —
        # these would otherwise leak forever (broker default ctx is never closed).
        for pg in list(self._owned_pages):
            try:
                if not pg.is_closed():
                    await pg.close()
            except Exception: pass
        self._owned_pages.clear()
        # detach listeners (broker ctx outlives us)
        for cid, h in list(self._page_listeners.items()):
            try:
                # find the matching ctx if still around
                for c in self._b.contexts:
                    if id(c) == cid:
                        try: c.remove_listener("page", h)
                        except Exception: pass
                        break
            except Exception: pass
        self._page_listeners.clear()
        for ctx in list(self._owned):
            try: await ctx.close()
            except Exception: pass
        self._owned.clear()
        # NEVER call self._b.close() — that would kill the broker chromium.

class _GoogleRouteSkip(Exception):
    """v8.21 sentinel — raised inside google-route try-block to indicate intentional
    SKIP based on broker exit family (e.g. direct → *.google must use chromium main
    proxy for IP-family alignment with submit). Caught explicitly, not logged as error."""
    pass

class _CDPChromiumShim:
    def __init__(self, real_chromium, ws_endpoint):
        self._c = real_chromium
        self._ws = ws_endpoint
        self._cached = None
    async def _attach(self):
        if self._cached is not None:
            try:
                _ = self._cached.contexts
                return self._cached
            except Exception:
                self._cached = None
        # v7.42: HTTP /json/version 3s 探针 + 30s WS timeout 避免 180s 死锁
        import urllib.request as _ureq
        def _probe_cdp_http(url: str, timeout: float = 3.0) -> bool:
            try:
                _u = url.rstrip("/") + "/json/version"
                with _ureq.urlopen(_u, timeout=timeout) as _r:
                    return _r.status == 200
            except Exception:
                return False

        # Try direct connect first; on failure, spawn broker chromium via warmup then retry
        # v8.16: belt-and-suspenders — prune stale leaked broker pages BEFORE attach.
        # Even with shim.close() page-tracking (above), a crashed/killed registration
        # process leaves its tab open. Over hours of jobs the broker accumulates
        # dozens of replit.com/signup tabs + reCAPTCHA iframes/workers, and Playwright
        # connect_over_cdp() times out at 180s while enumerating all targets.
        # Close everything except about:blank/chrome:// before each attach.
        try:
            import urllib.request as _ureq2, json as _json2
            with _ureq2.urlopen(self._ws.rstrip("/") + "/json/list", timeout=5) as _r:
                _tgts = _json2.loads(_r.read())
            _killed = 0
            for _t in _tgts:
                if _t.get("type") != "page":
                    continue
                _u = _t.get("url","") or ""
                if ("replit.com" in _u or "google.com/recaptcha" in _u or
                        "example.com" in _u or "challenges.cloudflare" in _u):
                    try:
                        _ureq2.urlopen(self._ws.rstrip("/") + "/json/close/" + _t["id"], timeout=3)
                        _killed += 1
                    except Exception: pass
            if _killed:
                log(f"[CDP] prune stale broker pages: closed {_killed} (replit/recaptcha/example tabs)")
        except Exception as _pe:
            log(f"[CDP] prune skipped: {_pe}")

        last_exc = None
        for tri in range(3):
            try:
                b = await self._c.connect_over_cdp(self._ws)
                self._cached = _CDPBrowserShim(b)
                return self._cached
            except Exception as e:
                last_exc = e
                log(f"[CDP] attach try {tri+1}/3 failed: {e}; pinging broker /api/cf-warmup to spawn chromium…")
                try:
                    subprocess.run(
                        ["curl", "-s", "--max-time", "35",
                         "http://localhost:8092/api/cf-warmup?url=https%3A%2F%2Fexample.com&timeoutMs=25000"],
                        capture_output=True, timeout=40,
                    )
                except Exception as _we:
                    log(f"[CDP] warmup ping err: {_we}")
                await asyncio.sleep(2)
        raise last_exc
    async def launch(self, **_kw):
        return await self._attach()
    async def connect_over_cdp(self, ws_endpoint, **_kw):
        return await self._attach()

class _CDPPwShim:
    def __init__(self, real_pw, ws_endpoint):
        self._pw = real_pw
        self._ws = ws_endpoint
        self.chromium = _CDPChromiumShim(real_pw.chromium, ws_endpoint)
    def __getattr__(self, name):
        return getattr(self._pw, name)

OUTLOOK_REFRESH_TOKEN = params.get("outlook_refresh_token", "")

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

def is_replit_offline_block(title: str, body: str) -> bool:
    # v8.60: replit edge static offline.html (5644B) sham-offline rejection
    t, b = title.lower(), body.lower()
    return (
        ("offline - replit" in t) or
        ("you're offline" in b and "access replit" in b) or
        ("check your connection" in b and "refresh to access replit" in b)
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
    var cfEl  = document.querySelector('[name="cf-turnstile-response"]');
    // v2 checkbox token (g-recaptcha-response) vs Replit's Enterprise field (recaptchaToken)
    var rcV2El = document.querySelector('#g-recaptcha-response') ||
                 document.querySelector('[name="g-recaptcha-response"]');
    var rcEntEl = document.querySelector('[name="recaptchaToken"]');
    // prefer recaptchaToken (Replit's field), fallback to v2
    var rcPrimary = rcEntEl || rcV2El;
    return {
        iframes,
        cfToken:       cfEl     ? cfEl.value      : null,
        rcToken:       rcPrimary ? rcPrimary.value : null,
        rcV2Token:     rcV2El   ? rcV2El.value.slice(0,20)   : null,
        rcEntToken:    rcEntEl  ? rcEntEl.value.slice(0,20)  : null,
    };
}"""

def is_rate_limited(t: str) -> bool:
    t = t.lower()
    return any(p in t for p in ("too quickly","doing this too quickly","please wait a bit","rate limit","too many requests","wait a bit"))
# ── v8.09 — dynamic sitekey defense (auto-track LIVE drift) ─────────────────
# Empirical: 2026-04-25 8 cleared sessions all returned 6LfyLYUsAAAAAP0Xmu-hJvZOYJLSL7E410qvKyII
# (no drift today). But hardcoding the key in execute() means a future rotation
# breaks signup silently. v8.09 caches the LIVE sitekey from each cleared
# iframe scan and uses it (not the hardcoded constant) at every execute() call.
_LIVE_SITEKEY: str = "6LfyLYUsAAAAAP0Xmu-hJvZOYJLSL7E410qvKyII"  # default fallback
def _update_live_sitekey(k: str) -> None:
    global _LIVE_SITEKEY
    if not k or not k.startswith("6L") or len(k) < 30:
        return
    if k != _LIVE_SITEKEY:
        log(f"[v8.09] LIVE sitekey CHANGED: {_LIVE_SITEKEY} → {k} (drift detected, cache updated)")
        _LIVE_SITEKEY = k
async def _live_sitekey_from_page(page) -> str:
    # Pull k= from any visible recaptcha iframe in the LIVE DOM, fall back to cache.
    try:
        k = await page.evaluate("""() => {
            const ifs = Array.from(document.querySelectorAll("iframe"));
            for (const f of ifs) {
                const s = f.src || "";
                if (s.includes("google.com/recaptcha") || s.includes("recaptcha/api") || s.includes("recaptcha/enterprise")) {
                    try { const u = new URL(s); const k = u.searchParams.get("k"); if (k) return k; } catch(e) {}
                }
            }
            return null;
        }""")
        if k and isinstance(k, str) and k.startswith("6L"):
            _update_live_sitekey(k)
            return k
    except Exception as e:
        log(f"[v8.09] _live_sitekey_from_page failed: {e}")
    return _LIVE_SITEKEY

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
                _update_live_sitekey(k)
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

# ── 环境初始化：DISPLAY + PulseAudio ─────────────────────────────────────────
def _setup_display_audio():
    """确保 Xvfb :99 和 PulseAudio 已注入到环境变量"""
    import os
    # Xvfb :99 — 服务器上已由 PM2/systemd 预启动
    if not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":99"
        log("[env] DISPLAY=:99 (Xvfb)")
    # PulseAudio — 防止 headful 音频静音（音频挑战需要）
    if not os.environ.get("PULSE_SERVER"):
        for _sock in ["/tmp/pulse/native", "/run/user/0/pulse/native"]:
            if os.path.exists(_sock):
                os.environ["PULSE_SERVER"] = f"unix:{_sock}"
                log(f"[env] PULSE_SERVER={os.environ['PULSE_SERVER']}")
                break
    return os.environ.get("DISPLAY", ":99")

import socket as _socket

async def ensure_tunnel(proxy_cfg: dict | None, timeout_s: float = 8.0) -> bool:
    """
    探针：SOCKS5 握手验证代理真正可用（端口开放 + 协议正确）。
    poll-bridge 1092-1095 / xray 10820-10845 使用前先探针检查。
    """
    if not proxy_cfg:
        return True
    server = proxy_cfg.get("server", "")
    _m = re.search(r"(?:socks5://)?([^:]+):(\d+)", server)
    if not _m:
        return True
    host, port = _m.group(1), int(_m.group(2))
    try:
        # 真 SOCKS5 握手：VER=5, NMETHODS=1, METHOD=0(no-auth)
        _sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        _sock.settimeout(timeout_s)
        _sock.connect((host, port))
        _sock.sendall(b"\x05\x01\x00")
        _resp = _sock.recv(2)
        _sock.close()
        if len(_resp) >= 2 and _resp[0] == 5:
            log(f"[ensure_tunnel] ✅ SOCKS5:{port} 握手OK")
            return True
        log(f"[ensure_tunnel] ⚠ SOCKS5:{port} 端口开放但非SOCKS5(resp={_resp.hex()})")
        return False
    except Exception as _te:
        log(f"[ensure_tunnel] ❌ SOCKS5:{port} 不可达: {_te}")
        return False

async def solve_recaptcha_audio(page, force_bframe: bool = False) -> str | None:
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
            "() => { var ent=document.querySelector('[name=\"recaptchaToken\"]'); if(ent&&ent.value&&ent.value.length>50) return ent.value; return ''; }",
        ]:
            try:
                t = await page.evaluate(js)
                if t: return t
            except Exception: pass
        return ""

    # 等待 checkbox 通过（无挑战情况，最多 4s）
    # force_bframe=True 时跳过早期返回，强制触发 bframe 音频挑战
    _cb_token = ""
    for _ in range(8):
        await page.wait_for_timeout(500)
        t = await _read_token()
        if t:
            if not force_bframe:
                log(f"[audio] ✅ checkbox 通过 token={len(t)}chars")
                return t
            _cb_token = t
            log(f"[audio] checkbox 通过 force_bframe=True → 继续触发音频 bframe")
            break

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
        if _cb_token:
            log(f"[audio] ✅ force_bframe 但无 bframe，fallback cb_token={len(_cb_token)}chars")
            return _cb_token
        log("[audio] 未找到 bframe")
        return None

    # 点击音频按钮
    try:
        audio_btn = challenge_frame.locator("#recaptcha-audio-button, .rc-button-audio")
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
            audio_src_el = challenge_frame.locator(".rc-audiochallenge-download-link, audio source, #audio-source")
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
    # 35s max (0.5s*70) — give CF JS challenge time to self-resolve
    for _w in range(70):
        await page.wait_for_timeout(500)
        try:
            title = await page.title()
            body  = (await page.locator("body").inner_text())[:300]
        except Exception:
            continue
        tl = title.lower()
        if "just a moment" in tl:
            if _w % 6 == 0:
                log(f"[cf] CF challenge... ({_w//2}s)")
            continue
        return None
    title2 = await page.title()
    if "just a moment" in title2.lower():
        log("[cf] 10s 超时，换端口+轮换IP")
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
            for lnk in _random.sample(links, min(6, len(links))):
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

        # ── v8.10 阶段 5.5：reCAPTCHA iframe 邻近行为（最高权重信号源）──────
        # Enterprise 评分对 iframe 邻近的鼠标轨迹权重最高 (Google 公开论文 2019).
        # 在 execute() 之前 0-2s 在 reCAPTCHA iframe 区域做 5-10 次贝塞尔移动 + hover,
        # 让 score 模型把这些动作归到 "user just clicked the captcha" 高置信类别.
        try:
            _rc_iframes = await page.evaluate("""() => {
                const out = [];
                for (const f of document.querySelectorAll('iframe')) {
                    const s = f.src || '';
                    if (s.includes('recaptcha/enterprise') || s.includes('recaptcha/api') || s.includes('google.com/recaptcha')) {
                        const r = f.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            out.push({ x: r.x, y: r.y, w: r.width, h: r.height, src: s.slice(0, 80) });
                        }
                    }
                }
                return out;
            }""")
            if _rc_iframes and isinstance(_rc_iframes, list):
                _rc = _rc_iframes[0]
                _cx, _cy = _rc['x'] + _rc['w']/2, _rc['y'] + _rc['h']/2
                log(f"[warmup] v8.10 iframe-near hover @ ({int(_cx)},{int(_cy)}) box={int(_rc['w'])}x{int(_rc['h'])}")
                # First slow approach to iframe center
                _ax, _ay = _random.randint(200, w - 200), _random.randint(150, h - 200)
                await _bezier(_ax, _ay, _cx, _cy, steps=_random.randint(20, 30))
                await page.wait_for_timeout(_random.randint(400, 800))
                # 6-10 small bezier moves around the iframe rectangle
                _hx, _hy = _cx, _cy
                for _i in range(_random.randint(6, 10)):
                    _nx = _rc['x'] + _random.uniform(-30, _rc['w'] + 30)
                    _ny = _rc['y'] + _random.uniform(-30, _rc['h'] + 30)
                    await _bezier(_hx, _hy, _nx, _ny, steps=_random.randint(10, 18))
                    await page.wait_for_timeout(_random.randint(120, 350))
                    _hx, _hy = _nx, _ny
                # v8.13 Final hover in iframe center (3-5s) — extended dwell
                await page.mouse.move(_cx, _cy)
                # micro-jitter at hover anchor (Enterprise sees realistic hand tremor)
                _jx, _jy = _cx, _cy
                for _ji in range(_random.randint(8, 14)):
                    _dx = _random.uniform(-3.5, 3.5); _dy = _random.uniform(-2.5, 2.5)
                    _jx += _dx; _jy += _dy
                    await page.mouse.move(_jx, _jy)
                    await page.wait_for_timeout(_random.randint(120, 300))
                await page.wait_for_timeout(_random.randint(800, 1600))
                log("[warmup] v8.13 iframe-near phase ✓ (extended dwell)")
            else:
                log("[warmup] v8.10 no recaptcha iframe yet (skip iframe-near phase)")
        except Exception as _ife:
            log(f"[warmup] v8.10 iframe-near phase err (忽略): {_ife}")

        # v8.13 阶段 6：focus body + Tab 试探（模拟用户准备填表）
        try:
            await page.evaluate("() => document.body && document.body.focus && document.body.focus()")
            await page.wait_for_timeout(_random.randint(400, 900))
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(_random.randint(300, 700))
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(_random.randint(300, 700))
        except Exception:
            pass
        # 最终停顿（用户"决定"开始填表）
        await page.wait_for_timeout(_random.randint(2500, 4500))

        log("[warmup] ✓ v8.13 完成（约 50-70s 行为 + iframe 长 dwell + tab 试探）")
    except Exception as e:
        log(f"[warmup] 异常(忽略): {e}")

async def _fast_fill(page, sel_list: list[str], value: str, label: str,
                     min_ms: int = 90, max_ms: int = 220) -> bool:
    """v8.13 人类速度填充：focus dwell → click → type → post-fill thinking pause."""
    for sel in sel_list:
        f = page.locator(sel)
        if await f.count():
            # v8.13: hover-then-click + pre-click dwell (~300-700ms)
            try:
                _bb = await f.first.bounding_box()
                if _bb:
                    _hx = _bb["x"] + _bb["width"]/2 + _random.uniform(-12,12)
                    _hy = _bb["y"] + _bb["height"]/2 + _random.uniform(-4,4)
                    await page.mouse.move(_hx, _hy)
                    await page.wait_for_timeout(_random.randint(180, 420))
            except Exception:
                pass
            await f.first.click()
            await page.wait_for_timeout(_random.randint(180, 380))
            await f.first.fill("")
            await page.wait_for_timeout(_random.randint(120, 260))
            for ch in value:
                await f.first.type(ch)
                delay = _random.randint(min_ms, max_ms)
                # 偶尔有短暂停顿（模拟换手/思考）
                if _random.random() < 0.08:
                    delay += _random.randint(200, 500)
                # v8.13: 5% 概率长思考（500-1100ms）
                if _random.random() < 0.05:
                    delay += _random.randint(500, 1100)
                await page.wait_for_timeout(delay)
            # v8.13: post-fill thinking pause (用户检查输入)
            await page.wait_for_timeout(_random.randint(450, 1100))
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
    # v7.31b: 先点 Continue with email 按钮（超时3s，避免挂死）
    _email_btn_found = False
    for _ebs in [
        "button:has-text(" + chr(34) + "Continue with email" + chr(34) + ")",
        "button:has-text(" + chr(34) + "Email" + chr(34) + ")",
        "[data-cy=" + chr(34) + "email-signup" + chr(34) + "]",
    ]:
        _eb = page.locator(_ebs)
        if await _eb.count():
            try:
                await _eb.first.click(timeout=3000)
                log("[step1] email btn: " + _ebs)
                await page.wait_for_timeout(800)
                _email_btn_found = True
            except Exception as _e:
                log("[step1] email btn click fail: " + str(_e)[:60])
            break
    if not _email_btn_found:
        log("[step1] no email btn, direct fill")

    # 填 email
    ok = await _fast_fill(
        page,
        ['input[name="email"]', 'input[type="email"]', 'input[placeholder*="email" i]'],
        EMAIL, "email"
    )
    if not ok:
        return "signup_email_field_not_found"

    # ──────────────────────────────────────────────────────────────────────
    # v7.79: Email 预检 (pre-flight availability probe)
    # ──────────────────────────────────────────────────────────────────────
    # 背景: 用户 outlook 池里大量邮箱已经在 Replit 站外被注册过 (NOT EXISTS DB
    #   过滤抓不到的情况). 现状走完 captcha solve + submit 才知道 "Email already
    #   in use" → 每个死候选浪费 ~3 分钟. cf-warmup 9 分钟跑了 3 个候选全废.
    #
    # 修法: 填完 email 后, 把 Replit 自家的 inline 异步验证结果作为 fast-fail
    #   信号: success("available"/"looks good") → 继续走完整 flow;
    #         negative("already in use"/"taken"/"already exists"/"already
    #         registered") → 直接返回 "Email already in use on Replit", 跳过
    #         captcha solve, 由上游 accounts.ts 标 replit_used 后立即换下一个
    #         outlook (省 ~2.5 分钟/候选).
    #
    # [LEGACY-v7.78r 备份] 旧的 5 行只等 "available" 不识别 negative:
    #   await page.wait_for_timeout(_random.randint(1800, 2800))
    #   try:
    #       await page.wait_for_selector(
    #           '[role="alert"]:has-text("available"), .success, [class*="success"]',
    #           timeout=3000)
    #       log("[email] 邮箱可用提示出现")
    #   except Exception: pass
    # ──────────────────────────────────────────────────────────────────────
    await page.wait_for_timeout(_random.randint(1800, 2800))
    _NEG_KW = (
        "already in use", "already registered", "already exists",
        "already been used", "email is taken", "is already taken",
        "use a different email", "email taken",
    )
    _POS_KW = ("available", "looks good", "valid email")
    _precheck_decision = None
    try:
        # PRECHECK_MAX_S 内轮询: 任一明确判断成立就 break, 优先识别 negative
        # v7.80: 改为可配 (default 6s for actual register, 12s for precheck-only)
        _probe_budget = PRECHECK_MAX_S if PRECHECK_ONLY else 5.0
        _t_start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - _t_start < _probe_budget:
            try:
                # v8.55 FIX placeholder 误报: 收紧 selector
                #   旧版 [class*="success" i],[class*="valid" i] 容易抓到表单库
                #   隐藏的模板组件,模板里 placeholder 文字 + literal "Email already
                #   in use" 拼一起污染 _alert_blob → 误判 taken.
                #   新版只信 [role=alert]/[aria-live=*] 这种语义化 live region,
                #   并要求 元素可见 (filter visible) + outerHTML dump 到 /tmp.
                _alert_handles = await page.locator(
                    '[role="alert"], [aria-live="polite"], [aria-live="assertive"], '
                    '[data-cy*="error"], [data-testid*="error"]'
                ).element_handles()
                _alerts = []
                _alert_diag = []
                for _h in _alert_handles:
                    try:
                        _vis = await _h.is_visible()
                    except Exception:
                        _vis = False
                    if not _vis:
                        continue
                    try:
                        _txt = (await _h.text_content()) or ""
                    except Exception:
                        _txt = ""
                    _txt = _txt.strip()
                    if not _txt:
                        continue
                    _alerts.append(_txt)
                    try:
                        _oh = await _h.evaluate('e=>e.outerHTML')
                    except Exception:
                        _oh = ""
                    _alert_diag.append(f"text={_txt[:120]!r} html={_oh[:200]}")
                _alert_blob = " | ".join(_alerts).lower()
                if _alert_blob:
                    if any(kw in _alert_blob for kw in _NEG_KW):
                        _precheck_decision = "taken"
                        log(f"[email-precheck] ❌ negative 信号: {_alert_blob[:160]}")
                        try:
                            with open('/tmp/precheck_taken_dump.txt','w') as _df:
                                _df.write("BLOB:\n"+_alert_blob+"\n\nELEMENTS:\n"+"\n---\n".join(_alert_diag))
                        except Exception: pass
                        break
                    if any(kw in _alert_blob for kw in _POS_KW):
                        _precheck_decision = "available"
                        log(f"[email-precheck] ✅ 邮箱可用: {_alert_blob[:80]}")
                        break
            except Exception:
                pass
            await page.wait_for_timeout(250)
        if _precheck_decision is None:
            log("[email-precheck] timeout: 未拿到明确信号 → 继续走完整 flow")
    except Exception as _ev:
        log(f"[email-precheck] 异常 (忽略, fallback 走完整 flow): {_ev}")

    # v7.80: 把决策回写 module-global, 让 attempt_register 在 PRECHECK_ONLY 模式
    # 下能拿到 "available" vs "unknown(timeout)" 的区分 (fill_step1 单纯 return None
    # 不够区分这两个).
    global _PRECHECK_RESULT
    _PRECHECK_RESULT = _precheck_decision

    if _precheck_decision == "taken":
        # 短路: 直接告诉上游 "Email already in use", 让它标 replit_used 换下一个
        # outlook. 避免后续 captcha solve + submit 浪费 ~2.5 分钟.
        return "Email already in use on Replit"

    # v7.80: precheck-only 模式 → 跳过后续 password/captcha/submit, 直接告诉
    # attempt_register 用 _PRECHECK_RESULT 短路返回。
    if PRECHECK_ONLY:
        return "_precheck_done"

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
        rcV2  = probe.get("rcV2Token")  or ""
        rcEnt = probe.get("rcEntToken") or ""
        log(f"[probe] iframes={len(iframes)} rc={bool(rc_token)} cf={bool(cf_token)} any_rc={any_rc} any_ts={any_ts}")
        log(f"[probe] rcV2(g-recaptcha-response) prefix={rcV2!r}  rcEnt(recaptchaToken) prefix={rcEnt!r}")
        for fr in iframes:
            log(f"  iframe: {fr.get('src','')[:100]}")
    except Exception as e:
        log(f"[probe] 异常: {e}")
        rc_token, cf_token, any_rc, any_ts = "", "", False, False

    # ── 核心策略 v7.34：有预置 enterprise token 直接用（触发 RC callback 解锁按钮）
    # 若无预置 token → 尝试音频/checkbox solve
    async def _inject_and_trigger(token: str):
        """
        注入 token 到所有 reCAPTCHA 相关 DOM 字段，使用 Object.defineProperty 强制覆盖 value getter。
        目的：阻止 Replit click handler 读到空 recaptchaToken 后再调用 grecaptcha.enterprise.execute()
             （execute() 生成的 Enterprise score token 因 GCP IP 低分被 server code:2 拒绝）。
        """
        try:
            n = await page.evaluate("""
                (token) => {
                    // 1. 覆盖所有 reCAPTCHA token 字段的 value getter（强制劫持）
                    var sels = ['[name="g-recaptcha-response"]','#g-recaptcha-response','[name="recaptchaToken"]'];
                    var count = 0;
                    sels.forEach(function(sel) {
                        document.querySelectorAll(sel).forEach(function(el) {
                            try {
                                // 先用 native setter 设置（触发 React onChange）
                                var desc = Object.getOwnPropertyDescriptor(
                                    el.tagName === 'TEXTAREA'
                                        ? window.HTMLTextAreaElement.prototype
                                        : window.HTMLInputElement.prototype,
                                    'value');
                                if (desc && desc.set) desc.set.call(el, token);
                            } catch(e) {}
                            // 再用 Object.defineProperty 锁死 value 的返回值
                            // 这样即使 click handler 直接读 el.value 也得到我们的 token
                            Object.defineProperty(el, 'value', {
                                get: function() { return token; },
                                configurable: true
                            });
                            try {
                                el.dispatchEvent(new Event('input',  { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            } catch(e) {}
                            count++;
                        });
                    });
                    return count;
                }
            """, token)
            log(f"[captcha] token 注入字段数={n} (Object.defineProperty + React事件)")
        except Exception as e:
            log(f"[captcha] token 注入/callback 异常(忽略): {e}")

    # v7.40: Enterprise token 策略修复
    # rcV2 = g-recaptcha-response (checkbox token, 0cAFcWeA 前缀) → Replit server 拒绝 code:1
    # rcEnt = recaptchaToken (Enterprise score/challenge token) → 正确格式
    # 关键修复: 若 rcEnt 为空，即使 rcV2 存在也必须等待真正的 Enterprise token
    if any_rc and not rcEnt:
        # Enterprise token 还未生成，rc_token 可能是 v2，不能直接提交
        if rc_token and not rcEnt:
            log(f"[captcha] ⚠ 只有 v2 token({rc_token[:20]}), 非 Enterprise → 重置等待")
            rc_token = ""

    # v7.43 (2026-04-23): 恢复 7d89395+0391f15+9536bc3 的 execute() Enterprise 评分链
    # 用户确认: execute() 是正确路径 (非 audio fallback)。
    # 7d89395 时段 score token 2425chars one-shot pass，依赖：
    #   1) cf-warmup 阶段 broker 加载 replit.com/signup → reCAPTCHA Enterprise 自动初始化，
    #      .google.com 域 NID/SID/HSID/SAPISID/OTZ/__Secure-* trust cookies 落到 broker jar
    #   2) 9536bc3 cookie 清理只针对 replit.com auth blacklist，保留 Google trust cookies
    #   3) 0391f15 的 google_proxy_route 池子已剔除 10827/10829 GCP 端口，纯非 GCP 出口
    #   4) execute() 在 broker chrome JS 内调用 → ctx.route 拦截 reCAPTCHA POST → 经 xray 干净出口
    # ── v8.12 ─────────────────────────────────────────────────────────────
    # ROOT CAUSE confirmed by v7.92 mech still alive: pre-mint here fires
    # execute() once. Then Replit's submit click handler fires execute() AGAIN
    # within ~10s. reCAPTCHA Enterprise scores this rapid double-execute pattern
    # near zero → backend returns code:1 "captcha token is invalid".
    #
    # FIX: skip our pre-mint entirely. Let Replit's submit click be the sole
    # execute() caller → single-execute = clean score profile. The 'rcEnt'
    # input is auto-populated by Replit's own submit JS; we just need to fill
    # email+pwd+click. DIAG-RC probe (line ~2090) lets us verify ONE-AND-ONLY
    # execute call appears before POST.
    #
    # Set V812_PREMINT=1 env to revert to old behavior for A/B comparison.
    _v812_premint = os.environ.get("V812_PREMINT","").strip() in ("1","true","yes")
    if any_rc and not rc_token and _v812_premint:
        log("[captcha] v8.12 V812_PREMINT=1 → run legacy pre-mint execute() (DIAG only, expect code:1)")
        try:
            _sk_live = await _live_sitekey_from_page(page)
            log(f"[v8.09] mint sitekey={_sk_live} (LIVE)")
            ent_result = await page.evaluate("""(SK) => {
                return new Promise((res) => {
                    if (typeof grecaptcha === 'undefined') { res({ok:false,err:'grecaptcha undefined'}); return; }
                    if (!grecaptcha.enterprise) { res({ok:false,err:'enterprise undefined'}); return; }
                    try {
                        grecaptcha.enterprise.ready(() => {
                            grecaptcha.enterprise.execute(SK, {action:"signUpPassword"})
                                .then(t => {
                                    var el = document.querySelector('[name="recaptchaToken"]');
                                    if (el) {
                                        var desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                                        if (desc && desc.set) desc.set.call(el, t);
                                        el.dispatchEvent(new Event('input',{bubbles:true}));
                                        el.dispatchEvent(new Event('change',{bubbles:true}));
                                    }
                                    // v7.57 撤销 v7.45 monkey-patch：改写 grecaptcha.enterprise.execute
                                    // 会被 reCAPTCHA Enterprise 内部完整性自检命中，进一步压低评分。
                                    // 7d89395 时段不做 monkey-patch，让 page 自己 auto-execute 也能拿高分。
                                    res({ok:true, token:t, len:t?t.length:0});
                                })
                                .catch(e => res({ok:false, err:'execute_rejected:'+(e&&e.message?e.message:String(e))}));
                        });
                    } catch(e) { res({ok:false, err:'try_catch:'+(e&&e.message?e.message:String(e))}); }
                    setTimeout(() => res({ok:false, err:'timeout_22s'}), 22000);
                });
            }""", _sk_live)
            if isinstance(ent_result, dict):
                if ent_result.get("ok") and ent_result.get("token") and ent_result.get("len", 0) > 50:
                    rc_token = ent_result["token"]
                    log(f"[captcha] ✅ execute() Enterprise score token len={ent_result['len']} prefix={rc_token[:20]}")
                    # v7.57 撤销 v7.44 _inject_and_trigger：Object.defineProperty 锁死 value
                    # 是 Enterprise 完整性检测的强信号。execute() 内部 .then 里已通过 native
                    # setter 写入 React state，无需再做 DOM 强写。
                else:
                    log(f"[captcha] ⚠ execute() 失败: {ent_result.get('err','unknown')[:200]}")
            else:
                log(f"[captcha] ⚠ execute() 返回非dict: {str(ent_result)[:100]}")
        except Exception as _ee:
            log(f"[captcha] execute() Python 侧异常: {_ee}")

    if any_rc and not rc_token:
        log("[captcha] execute() 无 token → 等待页面自动注入 (max 15s)")
        rc_token, cf_token = await _wait_for_token(page, max_s=15)

    if any_rc and not rc_token:
        log("[captcha] 仍无 Enterprise token → 音频挑战兜底")
        audio_token = await solve_recaptcha_audio(page) or ""
        if audio_token:
            log(f"[captcha] ✅ 音频兜底 token len={len(audio_token)} prefix={audio_token[:20]}")
            rc_token = audio_token
        else:
            log("[captcha] 音频兜底失败，空提交")
            rc_token, cf_token = await _wait_for_token(page, max_s=8)
    elif not rc_token and not cf_token:
        log("[captcha] 等待 Enterprise 自动 token (max 20s)...")
        rc_token, cf_token = await _wait_for_token(page, max_s=20)

    # ── Turnstile 额外等待（无付费服务）
    if any_ts and not cf_token:
        log("[Turnstile] 等待额外 10s...")
        rc_token, cf_token = await _wait_for_token(page, max_s=10, label="_ts")

    # 提交前：5-8s 等待（给 Enterprise 最终行为评分时间，v7.28 proven strategy）
    _pre_wait = _random.randint(5000, 8000)
    log(f"[submit] 等待 {_pre_wait}ms → Enterprise 最终评分")
    await page.wait_for_timeout(_pre_wait)
    # v7.57 撤销 v7.44 提交前 re-lock：再次跑 _inject_and_trigger 会留下
    # Object.defineProperty 痕迹 + 多次触发 React onChange，被 Enterprise 视为脚本行为。
    # 顺便检查按钮状态
    try:
        btn_check = page.locator('[data-cy="signup-create-account"]')
        if await btn_check.count():
            dis = await btn_check.first.get_attribute("disabled")
            aria_dis = await btn_check.first.get_attribute("aria-disabled")
            if dis is None and aria_dis != "true":
                log("[submit-wait] 按钮已解锁 ✅")
            else:
                log("[submit-wait] 按钮仍 disabled → 将 force=True 点击")
    except Exception:
        pass

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

    # v7.75 — route-force 智能化（拓扑修正后）
    # 拓扑修正：broker chromium 走 WARP (CF backbone) 拿 cf_clearance；
    # google_proxy_route 把 *.google/recaptcha.net 转走非 GCP SOCKS5 池。
    # 在这套同 IP 一致 context 下，page auto-re-execute 在 submit click 时
    # 调 grecaptcha.enterprise.execute() 拿到的 LIVE token 跟我们手动 execute()
    # 拿到的 LOCKED token 评分一致（同 sitekey/同 action/同出口/同 NID cookie），
    # 但 LIVE token 更新鲜（sub-second），LOCKED token 已老 5-15s 且 action 可能
    # 跟 Replit 后端 expected_action 不匹配 → Google verify 返 code:2。
    #
    # 因此：只在 LIVE token 缺失/异常时才回填 locked，正常情况完全放行。
    # v7.94 — REVERT v7.93f. Restore v7.75 always-on route-force (LIVE-token-first,
    # LOCKED-fallback) when proxy is non-empty + non-Tor. The v7.93f reasoning that
    # page.route leaves runtime instrumentation was unfounded — v7.75 logic is purely
    # a body-rewriter and only fires when LIVE token is missing/malformed (<1000 chars
    # or wrong prefix). LIVE token short-circuits before any rewrite, so no overhead
    # under the happy path. Keeping FORCE_ROUTE_FORCE=0 as opt-out.
    _brox_rf = PROXY.strip()
    if rc_token and len(rc_token) > 50 and bool(_brox_rf) and ":9050" not in _brox_rf:
        _locked_rc = rc_token
        async def _signup_force_token(route, request):
            # v8.27 — IP-consistency root-fix: forward POST sign-up via the SAME socks5
            # IP that minted the recaptcha token. Previous v7.75 route.continue_() sent
            # the POST out the broker chromium default exit (CF AS13335 datacenter) ->
            # reCAPTCHA Enterprise IP-mismatch -> code:1 invalid. Empirical 2026-04-27
            # rpl_mogy1gc8: token mint via socks5:10826 (DigitalOcean), POST exit
            # 104.28.195.186 (CF AS13335 datacenter) -> 400 code:1.
            try:
                import json as _jft
                bd = _jft.loads(request.post_data or "{}")
                _orig_t = bd.get("recaptchaToken", "") or ""
                if len(_orig_t) >= 1000 and _orig_t.startswith("0c"):
                    log(f"[route-force] LIVE token OK ({len(_orig_t)}chars, 0c... prefix) → 放行不覆盖, 走 socks5")
                    _body = request.post_data_buffer
                else:
                    bd["recaptchaToken"] = _locked_rc
                    log(f"[route-force] LIVE token 异常 (len={len(_orig_t)}, prefix={_orig_t[:4]!r}) → 回填 locked={len(_locked_rc)}chars, 走 socks5")
                    _body = _jft.dumps(bd).encode("utf-8")
                _proxy = None
                try:
                    from google_proxy_route import get_pinned_proxy_for_page
                    _proxy = get_pinned_proxy_for_page(page)
                except Exception as _ie:
                    log(f"[route-force v8.27] get_pinned_proxy import err: {_ie}")
                if not _proxy:
                    log("[route-force v8.27] no pinned proxy → fallback to route.continue_ (IP mismatch risk)")
                    await route.continue_(post_data=_body)
                    return
                try:
                    from httpx_socks import AsyncProxyTransport
                    import httpx as _hx
                    _hdrs = {}
                    _STRIP = {"host","connection","content-length","accept-encoding","transfer-encoding","expect","upgrade"}
                    for _k, _v in (request.headers or {}).items():
                        if _k.lower() in _STRIP or _k.startswith(":"):
                            continue
                        _hdrs[_k] = _v
                    _tr = AsyncProxyTransport.from_url(_proxy)
                    async with _hx.AsyncClient(transport=_tr, http2=False, timeout=20.0, follow_redirects=False, verify=True) as _cl:
                        _r = await _cl.request(request.method, request.url, headers=_hdrs, content=_body)
                    _resp_h = []
                    _STRIP_R = {"content-encoding","content-length","transfer-encoding","connection","keep-alive"}
                    for _k, _v in _r.headers.multi_items():
                        if _k.lower() in _STRIP_R: continue
                        _resp_h.append((_k, _v))
                    log(f"[route-force v8.27] sign-up POST -> {_proxy} -> {_r.status_code} ({len(_r.content)}B)")
                    await route.fulfill(status=_r.status_code, headers=dict(_resp_h), body=_r.content)
                    return
                except Exception as _fe:
                    log(f"[route-force v8.27] socks5 forward FAIL: {_fe} -> fallback route.continue_")
                    await route.continue_(post_data=_body)
            except Exception as _re:
                log(f"[route-force] err: {_re}")
                try:
                    await route.continue_()
                except Exception:
                    pass
        try:
            await page.route("**/api/v1/auth/sign-up**", _signup_force_token)
            log(f"[route-force] v7.75 sign-up POST 拦截器已挂载（LIVE-token 优先, LOCKED 回填）")
        except Exception as _ro:
            log(f"[route-force] 挂载失败: {_ro}")

    # 先注册 response+request 拦截器（在 click 前）避免漏掉快速响应
    _api_resp: dict = {}
    _api_req: dict = {}
    async def _on_response(resp):
        try:
            url = resp.url
            if ("replit.com" in url and "sp.replit.com" not in url
                    and resp.request.method == "POST"):
                rb = await resp.body()
                _api_resp["url"]    = url
                _api_resp["status"] = resp.status
                _api_resp["body"]   = rb[:600].decode("utf-8", errors="replace")
        except Exception:
            pass
    async def _on_request(req):
        try:
            url = req.url
            if ("replit.com" in url and "sign-up" in url and req.method == "POST"):
                pb = req.post_data or ""
                _api_req["url"]  = url
                _api_req["body"] = pb[:8000]
                # v7.92 DIAG: log all top-level field names + turnstile-related field presence
                try:
                    import json as _jdiag
                    _bd = _jdiag.loads(pb) if pb else {}
                    _keys = list(_bd.keys()) if isinstance(_bd, dict) else []
                    _ts_fields = {k: (len(str(_bd[k])) if _bd.get(k) is not None else 0)
                                  for k in _keys if any(t in k.lower() for t in ("turnstile","cf-","captcha","token","challenge"))}
                    log(f"[step1-wait] DIAG POST keys={_keys}")
                    log(f"[step1-wait] DIAG captcha-like fields lens={_ts_fields}")
                except Exception as _de:
                    log(f"[step1-wait] DIAG parse fail: {_de}")
        except Exception:
            pass
    page.on("response", _on_response)
    page.on("request",  _on_request)
    # v7.77 DIAG: 记录 36s 内 ALL replit.com 流量 (任何方法/状态), 用于定位 submit 后
    # 为什么 _on_response 不触发: (a) POST 根本没发, (b) cf 把 POST 改写成 GET html
    # challenge, (c) net error / requestfailed, (d) 走了 sub-domain (sp./api.) 被过滤
    _diag_resp: list = []
    _diag_failed: list = []
    _diag_req_sent: list = []
    _diag_req_finished: list = []
    from urllib.parse import urlparse as _diag_urlparse
    def _diag_is_replit(u: str) -> bool:
        try:
            h = (_diag_urlparse(u).hostname or "").lower()
            return h == "replit.com" or h.endswith(".replit.com")
        except Exception:
            return False
    async def _diag_on_response(resp):
        try:
            url = resp.url
            if _diag_is_replit(url):
                _diag_resp.append(f"{resp.request.method} {resp.status} {url[:120]}")
        except Exception:
            pass
    def _diag_on_requestfailed(req):
        try:
            url = req.url
            if _diag_is_replit(url):
                _diag_failed.append(f"{req.method} {url[:120]} fail={req.failure}")
        except Exception:
            pass
    def _diag_on_request(req):
        try:
            url = req.url
            if _diag_is_replit(url) and req.method == "POST":
                _diag_req_sent.append(f"POST {url[:120]}")
        except Exception:
            pass
    def _diag_on_request_finished(req):
        try:
            url = req.url
            if _diag_is_replit(url) and req.method == "POST":
                _diag_req_finished.append(f"POST {url[:120]}")
        except Exception:
            pass
    page.on("response", _diag_on_response)
    page.on("requestfailed", _diag_on_requestfailed)
    page.on("request", _diag_on_request)
    page.on("requestfinished", _diag_on_request_finished)

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

    # v7.34: 修复 "already" 误匹配 "Already have an account? Log in"（正常页面文本）
    # 改为更精确的错误词组
    _err_keywords = ("invalid","incorrect","taken",
                     "already in use","already exists","already registered","already been used",
                     "email is taken","username is taken",
                     "unavailable","something went wrong",
                     "too many","rate limit",
                     "captcha token","captcha validation","captcha failed",
                     "recaptcha failed","recaptcha invalid","recaptcha expired")
    for _w in range(18):
        await page.wait_for_timeout(2000)
        cur_url = page.url
        if "signup" not in cur_url.lower():
            log(f"[step1-wait] 跳转: {cur_url[:80]}")
            break
        if _api_resp.get("status"):
            log(f"[step1-wait] API={_api_resp['status']} url={_api_resp.get('url','')[:80]}")
            log(f"[step1-wait] API body: {_api_resp.get('body','')[:300]}")
            if _api_req.get("body"):
                req_body = _api_req["body"]
                # 只显示 recaptchaToken 部分，截断不泄露完整 token
                import re as _re
                m = _re.search(r'"recaptchaToken"\s*:\s*"([^"]{0,60})', req_body)
                rtok = m.group(1) if m else "(not found)"
                log(f"[step1-wait] REQ recaptchaToken prefix: {rtok}... (total req {len(req_body)}chars)")
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
        # v8.14 DIAG: req-sent vs req-finished delta tells us if POST went out at all
        log(f"[diag-v8.14] replit.com POST sent={len(_diag_req_sent)} finished={len(_diag_req_finished)} (delta={len(_diag_req_sent)-len(_diag_req_finished)} = inflight/dropped)")
        for _l in _diag_req_sent[-6:]:
            log(f"[diag-req-sent] {_l}")
        for _l in _diag_req_finished[-6:]:
            log(f"[diag-req-fin]  {_l}")
        # v7.77 DIAG dump
        log(f"[diag] 36s 内 replit.com response 总数={len(_diag_resp)}")
        for _l in _diag_resp[-15:]:
            log(f"[diag-resp] {_l}")
        log(f"[diag] 36s 内 replit.com requestfailed 总数={len(_diag_failed)}")
        for _l in _diag_failed[-10:]:
            log(f"[diag-fail] {_l}")
        try: cur_url2 = page.url
        except Exception: cur_url2 = "(unknown)"
        log(f"[diag] 当前 page.url={cur_url2[:120]}")

    page.remove_listener("response", _on_response)
    try: page.remove_listener("response", _diag_on_response)
    except: pass
    try: page.remove_listener("requestfailed", _diag_on_requestfailed)
    except: pass
    try: page.remove_listener("request", _diag_on_request)
    except: pass
    try: page.remove_listener("requestfinished", _diag_on_request_finished)
    except: pass

    await page.screenshot(path=f"/tmp/replit_after_step1_{USERNAME}.png")

    # CF 403 API 拦截检测
    _api_status = _api_resp.get("status", 0)
    _api_body_r = _api_resp.get("body", "")
    log(f"[cf-check] status={_api_status} body_has_moment={'just a moment' in _api_body_r.lower()} body50={repr(_api_body_r[:50])}")
    _api_url_r = _api_resp.get("url", "")
    _is_signup_post = "sign-up" in _api_url_r or "/signup" in _api_url_r
    if _api_status in (403, 429, 503) and ("just a moment" in _api_body_r.lower() or "challenge" in _api_body_r.lower() or "cloudflare" in _api_body_r.lower()):
        if not _is_signup_post:
            log(f"[cf-check] {_api_status} on non-signup ({_api_url_r[:80]}) → 忽略, 等待 sign-up POST 真结果")
        else:
            log(f"[step1] CF API 拦截 ({_api_status}) on sign-up → cf_api_blocked")
            return "cf_api_blocked"
    if _api_status == 400 and "captcha" in _api_body_r.lower():
        log(f"[step1] API captcha 400 → v7.54 fast-retry (同 ctx 内 port-rotate + 重做 execute)")
        # re-attach response listener (was removed above) so we can read new responses
        page.on("response", _on_response)
        try:
            _fr_extra_s = 0  # v8.49: extra wait accumulates when 429 received
            for _fr in range(1):  # v8.56: 砍掉 fast-retry, 单次 captcha 失败立即返回, 让 outer outlook-rotate 接管 (CLIProxyAPI v6.9.38 同思路)
                try:
                    # replit per-(IP,email) rate-limit window ~30-90s. Sleep BEFORE retry submit.
                    _bw = 35 + (_fr * 5) + _fr_extra_s
                    _fr_extra_s = 0  # consume
                    log(f"[fast-retry {_fr+1}/1] 等 {_bw}s 避开 replit 429 速率窗口…")
                    await page.wait_for_timeout(_bw * 1000)
                    _api_resp.clear()
                    _sk_live2 = await _live_sitekey_from_page(page)
                    log(f"[v8.09] fast-retry sitekey={_sk_live2} (LIVE)")
                    _new = await page.evaluate("""
                        (SK) => new Promise((res) => {
                            try {
                                var fn = window.__origExecute;
                                if (!fn) {
                                    if (window.grecaptcha && grecaptcha.enterprise && grecaptcha.enterprise.execute) {
                                        fn = grecaptcha.enterprise.execute.bind(grecaptcha.enterprise);
                                    }
                                }
                                if (!fn) { res({ok:false, err:'no_execute'}); return; }
                                fn(SK, {action:"signUpPassword"})
                                    .then(t => {
                                        window.__lockedRcToken = t;
                                        var el = document.querySelector('[name="recaptchaToken"]');
                                        if (el) {
                                            var d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                                            if (d && d.set) d.set.call(el, t);
                                            el.dispatchEvent(new Event('input',{bubbles:true}));
                                            el.dispatchEvent(new Event('change',{bubbles:true}));
                                        }
                                        res({ok:true, token:t, len:t?t.length:0});
                                    })
                                    .catch(e => res({ok:false, err:String(e&&e.message||e)}));
                                setTimeout(() => res({ok:false, err:'execute_timeout_20s'}), 20000);
                            } catch(e) { res({ok:false, err:String(e&&e.message||e)}); }
                        })
                    """, _sk_live2)
                    if not (isinstance(_new, dict) and _new.get("ok")):
                        log(f"[fast-retry {_fr+1}/1] execute 失败: {(_new or {}).get('err','?')[:120]}")
                        break
                    log(f"[fast-retry {_fr+1}/1] 新 token len={_new.get('len')} prefix={_new.get('token','')[:20]}")
                    # v7.95 — REVERT v7.92/v7.93 直 fetch. 直 fetch 缺 Replit 真实 XHR 的
                    # CSRF / origin / 完整 header 链, 必然 403 "Expected X-Requested-With".
                    # 回到 v7.78r click-submit: Replit JS 调用 execute() 拿 fresh LIVE token,
                    # 经 route-force LIVE-OK 放行不覆盖, IP 一致前提下 (v7.95 broker-aware
                    # google-route 已修复 IP 串台) 直接通过.
                    _api_resp.clear()
                    _clicked = False
                    for _sel in ('[data-cy="signup-create-account"]', 'button[type="submit"]'):
                        try:
                            _btn = page.locator(_sel)
                            if await _btn.count():
                                await _btn.first.click(force=True)
                                _clicked = True
                                break
                        except Exception:
                            continue
                    if not _clicked:
                        log(f"[fast-retry {_fr+1}/1] submit click 失败")
                        continue
                    # 等 Replit POST 响应进入 _on_response (max 30s)
                    for _w in range(15):
                        await page.wait_for_timeout(2000)
                        if _api_resp.get("status"):
                            break
                    _s2 = _api_resp.get("status", 0)
                    _b2 = _api_resp.get("body", "")
                    log(f"[fast-retry {_fr+1}/1] CLICK-API={_s2} body={_b2[:140]}")
                    if _s2 in (200, 201, 204):
                        log(f"[fast-retry {_fr+1}/1] ✅ 成功")
                        return None
                    _b2l = _b2.lower()
                    if _s2 == 400 and "captcha" in _b2l:
                        continue
                    if is_integrity_error(_b2):
                        return "integrity_check_failed_after_step1"
                    if _s2 in (400, 422) and any(kw in _b2l for kw in (
                        "already in use","already registered","already exists","already been used","email is taken"
                    )):
                        return "Email already in use on Replit"
                    if _s2 in (403, 503) and ("just a moment" in _b2l or "challenge" in _b2l or "cloudflare" in _b2l):
                        return "cf_api_blocked"
                    if _s2 == 429:
                        if _fr < 2:
                            log(f"[fast-retry {_fr+1}/1] 429 速率限制 → 额外 60s 冷却后继续 (round {_fr+2}/3)")
                            _fr_extra_s = 60
                            continue
                        else:
                            log(f"[fast-retry {_fr+1}/1] 429 速率限制 (第3轮已到) → 放弃")
                            break
                    # v7.93: 403 CSRF / 'expected x-requested-with' = 网络层噪声, 不是 captcha 终态, 继续重试
                    if _s2 == 403 and ('x-requested-with' in _b2l or 'csrf' in _b2l or 'expected' in _b2l):
                        log(f"[fast-retry {_fr+1}/1] 403 CSRF/header 噪声 → 继续下一轮")
                        continue
                    log(f"[fast-retry {_fr+1}/1] 非 captcha 错误 ({_s2}) → 退出循环 body={_b2[:80]}")
                    break
                except Exception as _fre:
                    log(f"[fast-retry {_fr+1}/1] 异常: {_fre}")
                    break
        finally:
            try: page.remove_listener("response", _on_response)
            except Exception: pass
        log("[step1] fast-retry 3 轮全失败 → captcha_token_invalid")
        return "captcha_token_invalid"
    if is_integrity_error(_api_body_r):
        log(f"[step1] API body integrity error: {_api_body_r[:120]}")
        return "integrity_check_failed_after_step1"
    _abr_low = _api_body_r.lower()
    # v8.31 ROOT-FIX 2026-04-27 — sign-up POST 200 + isNewUser:false detection
    # When the email exists on Replit, sign-up endpoint returns 200 with
    # {"userId":...,"isNewUser":false,"cookieExpiresAt":...} (it auto-logs in).
    # Without this short-circuit, code would fall through to step2 (username field
    # not present in login flow) and burn 30s on a wait_for_selector timeout, then
    # the same outlook account is reused for attempt 2 hitting "Email already in use".
    # Treat isNewUser:false as taken so upstream marks replit_used + rotates outlook.
    if _api_status == 200 and ('"isnewuser":false' in _abr_low.replace(' ', '') or '"isnewuser": false' in _abr_low):
        log(f"[step1] v8.31 isNewUser=false → existing Replit account auto-login → Email already in use on Replit")
        return "Email already in use on Replit"
    if _api_status in (400, 422) and any(kw in _abr_low for kw in (
        "already in use", "already registered", "already exists", "already been used",
        "email is taken", "email.*already"
    )):
        log(f"[step1] API body: email already on Replit → {_api_body_r[:120]}")
        return "Email already in use on Replit"

    body = (await page.locator("body").inner_text())[:1000]
    url  = page.url
    log(f"[step1] body[0:400]: {body[:400].replace(chr(10),' ')}")
    log(f"[step1] url: {url[:100]}")

    if is_integrity_error(body):
        return "integrity_check_failed_after_step1"
    if is_captcha_invalid(body):
        return "captcha_token_invalid"
    if is_rate_limited(body):
        log("[step1] ⏳ rate limited → account_rate_limited")
        return "account_rate_limited"

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


# ── Canvas 2D + WebGL 完整指纹注入 ───────────────────────────────────────────
_CANVAS_WEBGL_NOISE_JS = """
(() => {
    /* Canvas 2D 像素噪声 */
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
    /* WebGL 完整指纹伪装 */
    function patchWebGL(ctx) {
        if (!ctx) return;
        const _getParam = ctx.getParameter.bind(ctx);
        ctx.getParameter = function(p) {
            if (p === 37445) return 'Google Inc. (Intel)';
            if (p === 37446) return 'ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (TGL GT2), OpenGL 4.6)';
            if (p === 7936)  return 'WebKit';
            if (p === 7937)  return 'WebKit WebGL';
            if (p === 7938)  return 'WebGL 1.0 (OpenGL ES 2.0 Chromium)';
            return _getParam(p);
        };
        const _getSupportedExt = ctx.getSupportedExtensions.bind(ctx);
        ctx.getSupportedExtensions = function() {
            const exts = _getSupportedExt() || [];
            if (!exts.includes('WEBGL_debug_renderer_info')) exts.push('WEBGL_debug_renderer_info');
            return exts;
        };
        const _getExt = ctx.getExtension.bind(ctx);
        ctx.getExtension = function(name) {
            if (name === 'WEBGL_debug_renderer_info') return {
                UNMASKED_VENDOR_WEBGL: 37445, UNMASKED_RENDERER_WEBGL: 37446
            };
            return _getExt(name);
        };
        const _readPx = ctx.readPixels.bind(ctx);
        ctx.readPixels = function(x, y, w, h, fmt, type, pixels) {
            _readPx(x, y, w, h, fmt, type, pixels);
            if (pixels && pixels.length > 0)
                for (let i = 0; i < Math.min(pixels.length, 4); i++)
                    pixels[i] = Math.min(255, Math.max(0, pixels[i] + (Math.random() > .5 ? 1 : -1)));
        };
    }
    const _origGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, attrs) {
        const ctx = _origGetContext.call(this, type, attrs);
        if (ctx && (type === 'webgl' || type === 'experimental-webgl' || type === 'webgl2'))
            patchWebGL(ctx);
        return ctx;
    };
    /* AudioContext 噪声 */
    if (typeof OfflineAudioContext !== 'undefined') {
        const _origStart = OfflineAudioContext.prototype.startRendering;
        OfflineAudioContext.prototype.startRendering = function() {
            return _origStart.call(this).then(buf => {
                const ch = buf.getChannelData(0);
                for (let i = 0; i < Math.min(ch.length, 100); i++)
                    ch[i] += (Math.random() - 0.5) * 1e-7;
                return buf;
            });
        };
    }
})();
"""

# ── 单次 browser attempt ──────────────────────────────────────────────────────

async def _fetch_replit_verify_url(email_addr: str, password: str, timeout_s: int = 180,
                                    proxy_cfg=None,
                                    outlook_refresh_token: str = "") -> str | None:
    """
    读取 outlook inbox 中的 Replit 验证链接。
    方法1: Microsoft Graph API — refresh_token grant (需外部传入 token)
    方法2: Playwright headless 登录 Outlook Web (login.microsoftonline.com)
    注意: ROPC (password grant) 已被 Microsoft 全面封禁，不再尝试。
    """
    import urllib.request, urllib.parse, json as _json, time as _t, re as _re, os as _os, html as _html

    _domain = email_addr.lower().split("@")[-1] if "@" in email_addr else ""
    if _domain not in ("outlook.com", "hotmail.com", "live.com", "msn.com"):
        log(f"[mail] 非outlook邮箱({_domain}), 跳过"); return None

    _URL_PAT = r'https://replit\.com/action-code[^\s"\'<>]+'

    # ── 方法0: 共享缓存 (live-verify-poller 抢先拿到时落盘的 verify_url) ────
    # poller 和我们抢同一封邮件, 谁先消费谁拿走; 落盘共享让 python 同会话也能接管.
    import re as _re_pre
    _safe_key = _re_pre.sub(r'[^a-z0-9._@+-]', '_', email_addr.lower())
    _cache_path = f"/tmp/replit_verify_cache/{_safe_key}.json"
    _cache_deadline = _t.time() + min(60, timeout_s)  # 最多等60s共享缓存
    while _t.time() < _cache_deadline:
        try:
            if _os.path.exists(_cache_path):
                with open(_cache_path) as _cf:
                    _cd = _json.loads(_cf.read())
                _cu = (_cd.get("verify_url") or "").strip()
                _ct = int(_cd.get("ts") or 0)
                # 10 分钟内的缓存才算新鲜 (oobCode 通常 1h, 但宁可保守)
                if _cu and (_t.time() * 1000 - _ct) < 600_000:
                    log(f"[cache] ✅ 命中共享 verify_url (来源={_cd.get('source','?')}): {_cu[:80]}")
                    return _cu
        except Exception as _ce:
            log(f"[cache] 读取失败 (非致命): {str(_ce)[:80]}")
        await asyncio.sleep(2)
    log(f"[cache] 等待 {min(60,timeout_s)}s 未命中, 走自己的 Graph/Web fallback")

    # 全局 outlook_refresh_token 作为默认值
    _rt = outlook_refresh_token or OUTLOOK_REFRESH_TOKEN

    # ── 方法1: Graph API refresh_token grant ─────────────────────────────────
    if _rt:
        log("[graph] 尝试 refresh_token grant...")
        # 使用 accounts.ts 中相同的自定义 app client_id
        for _cid in ["9e5f94bc-e8a4-4e73-b8be-63364c29d753",
                     "d3590ed6-52b3-4102-aeff-aad2292ab01c"]:
            try:
                _tdata = urllib.parse.urlencode({
                    "client_id": _cid,
                    "grant_type": "refresh_token",
                    "refresh_token": _rt,
                    "scope": "https://graph.microsoft.com/Mail.Read offline_access",
                }).encode()
                _tr = urllib.request.Request(
                    "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
                    data=_tdata,
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
                _resp = urllib.request.urlopen(_tr, timeout=12)
                _tok_data = _json.loads(_resp.read())
                _tok = _tok_data.get("access_token", "")
                if not _tok:
                    log(f"[graph] cid={_cid[:8]} 无 token: {list(_tok_data.keys())}"); continue
                log(f"[graph] ✅ refresh_token OK cid={_cid[:8]}")

                _deadline = _t.time() + timeout_s
                # 真实发件人是 verify@replit.com (不是 noreply@), $filter 命中 0 条 + URL 编码后还 400.
                # 改成: 拉前 10 封, 代码侧按 sender/subject/body 匹配. Inbox + JunkEmail 双轮询.
                _qs = urllib.parse.quote(
                    "$select=subject,from,body&$orderby=receivedDateTime desc&$top=10",
                    safe="=&$/'")
                _folders = ("Inbox", "JunkEmail")
                while _t.time() < _deadline:
                    for _fld in _folders:
                        try:
                            _url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{_fld}/messages?{_qs}"
                            _gr = urllib.request.Request(_url, headers={
                                "Authorization": f"Bearer {_tok}", "Accept": "application/json"})
                            _msgs = _json.loads(urllib.request.urlopen(_gr, timeout=12).read()).get("value", [])
                            for _msg in _msgs:
                                _subj = (_msg.get("subject") or "").lower()
                                _frm  = ((_msg.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
                                if not ("replit" in _frm or "replit" in _subj or "verify" in _subj):
                                    continue
                                _body = (_msg.get("body") or {}).get("content", "")
                                _m = _re.search(_URL_PAT, _body)
                                if _m:
                                    log(f"[graph] ✅ {_fld} 验证链接: {_m.group(0)[:80]}")
                                    return _html.unescape(_m.group(0)).rstrip(".,)")
                        except Exception as _ge:
                            log(f"[graph] {_fld} poll err: {str(_ge)[:120]}")
                    log(f"[graph] 未找到, 剩余{int(_deadline - _t.time())}s (Inbox+Junk)")
                    await asyncio.sleep(6)
                return None
            except Exception as _re_e:
                log(f"[graph] refresh_token cid={_cid[:8]} err: {str(_re_e)[:100]}")
    else:
        log("[graph] 无 outlook_refresh_token → 跳过 Graph API")

    # ── 方法2: Playwright headless 登录 Outlook Web ──────────────────────────
    log("[mail] → Playwright Outlook Web 登录读取邮件...")
    try:
        try:
            from playwright.async_api import async_playwright as _ow_apw
        except ImportError:
            from rebrowser_playwright.async_api import async_playwright as _ow_apw

        _launch_args = ["--no-sandbox", "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled"]
        if proxy_cfg and proxy_cfg.get("server"):
            _srv = proxy_cfg["server"].replace("socks5://", "")
            _launch_args.append(f"--proxy-server=socks5://{_srv}")

        async with _ow_apw() as _pw2:
            _br2 = await _pw2.chromium.launch(headless=True, args=_launch_args)
            _ctx2 = await _br2.new_context(
                locale="en-US",
                timezone_id="America/Los_Angeles",  # v8.18: 与 locale 配套, 避免 navigator.language=en + Intl.tz=Asia 矛盾
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                            " AppleWebKit/537.36 (KHTML, like Gecko)"
                            " Chrome/131.0.0.0 Safari/537.36"))
            _pg2 = await _ctx2.new_page()
            try:
                # ── 步骤1: 登录 login.microsoftonline.com ────────────────────
                log("[outlook-web] 导航到登录页...")
                await _pg2.goto(
                    "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
                    "?client_id=d3590ed6-52b3-4102-aeff-aad2292ab01c"
                    "&response_type=token&scope=openid+profile"
                    "&redirect_uri=https://outlook.live.com&nonce=12345",
                    wait_until="domcontentloaded", timeout=30000)
                await _pg2.wait_for_timeout(1500)

                # 如果跳转失败，尝试直接 live.com
                if "login" not in _pg2.url and "microsoft" not in _pg2.url:
                    await _pg2.goto("https://login.live.com",
                                    wait_until="domcontentloaded", timeout=30000)
                    await _pg2.wait_for_timeout(1500)

                # 填邮箱
                for _es in ['input[type="email"]', 'input[name="loginfmt"]',
                             '#i0116', 'input[name="login"]']:
                    _ef = _pg2.locator(_es)
                    if await _ef.count():
                        await _ef.first.fill(email_addr)
                        await _pg2.wait_for_timeout(800)
                        # 点击 Next
                        for _ns in ['input[type="submit"]', '#idSIButton9',
                                    'button:has-text("Next")', '[data-testid="primaryButton"]']:
                            _nb = _pg2.locator(_ns)
                            if await _nb.count():
                                await _nb.first.click(); break
                        await _pg2.wait_for_timeout(2500)
                        break

                # 填密码
                for _ps in ['input[type="password"]', 'input[name="passwd"]',
                             '#i0118', 'input[name="password"]']:
                    _pf = _pg2.locator(_ps)
                    if await _pf.count():
                        await _pf.first.fill(password)
                        await _pg2.wait_for_timeout(800)
                        for _ss2 in ['input[type="submit"]', '#idSIButton9',
                                     'button:has-text("Sign in")', '[data-testid="primaryButton"]']:
                            _sb2 = _pg2.locator(_ss2)
                            if await _sb2.count(): await _sb2.first.click(); break
                        await _pg2.wait_for_timeout(3500)
                        break

                # 处理 "Stay signed in?" / "Keep me signed in?"
                for _no_sel in ['#idBtn_Back', 'button:has-text("No")',
                                 'input[value="No"]', '[data-testid="secondaryButton"]']:
                    try:
                        _no = _pg2.locator(_no_sel)
                        if await _no.count():
                            await _no.first.click(); await _pg2.wait_for_timeout(2000); break
                    except Exception: pass

                log(f"[outlook-web] 登录完成, 当前URL: {_pg2.url[:60]}")

                # ── 步骤2: 进入收件箱轮询 ────────────────────────────────────
                await _pg2.goto("https://outlook.live.com/mail/0/inbox",
                                wait_until="domcontentloaded", timeout=35000)
                await _pg2.wait_for_timeout(4000)
                log("[outlook-web] 进入收件箱, 开始轮询 (Inbox/Other/Junk + Search)...")

                # 全新 Outlook 账号: Replit 验证邮件常进 Junk Email 或 Other(Focused/Other 切换);
                # 用 search?q=replit 一次扫所有 folder, 再轮 inbox / junkemail 兜底.
                _SEARCH_URLS = [
                    "https://outlook.live.com/mail/0/?searchquery=replit",
                    "https://outlook.live.com/mail/0/junkemail",
                    "https://outlook.live.com/mail/0/inbox",
                ]
                _dl2 = _t.time() + timeout_s
                _folder_idx = 0
                while _t.time() < _dl2:
                    # 先扫全页 HTML
                    _c2 = await _pg2.content()
                    _m2 = _re.search(_URL_PAT, _c2)
                    if _m2:
                        _u2 = _m2.group(0).rstrip(".,)")
                        log(f"[outlook-web] ✅ 从页面HTML找到: {_u2[:80]}")
                        return _html.unescape(_u2)

                    # 点击含 "replit" / "verify" 的邮件行 (任何当前 folder)
                    try:
                        _rows = await _pg2.locator(
                            '[data-convid], [role="option"], .customScrollBar div[class*="row"]'
                        ).all()
                        for _row in _rows:
                            try:
                                _rtxt = (await _row.inner_text())[:200].lower()
                                if "replit" in _rtxt or "verify" in _rtxt or "verification" in _rtxt:
                                    await _row.click()
                                    await _pg2.wait_for_timeout(2500)
                                    _c3 = await _pg2.content()
                                    _m3 = _re.search(_URL_PAT, _c3)
                                    if _m3:
                                        _u3 = _m3.group(0).rstrip(".,)")
                                        log(f"[outlook-web] ✅ 点击邮件获取: {_u3[:80]}")
                                        return _html.unescape(_u3)
                            except Exception: pass
                    except Exception: pass

                    # 尝试切换 Focused/Other tab (如果有)
                    try:
                        _other = _pg2.locator('button[role="tab"]:has-text("Other"), button[role="tab"]:has-text("其他"), [aria-label*="Other"]').first
                        if await _other.count() > 0:
                            await _other.click(timeout=2000)
                            await _pg2.wait_for_timeout(1500)
                            log("[outlook-web] 切到 Other tab")
                    except Exception: pass

                    _remain = int(_dl2 - _t.time())
                    _next_folder = _SEARCH_URLS[_folder_idx % len(_SEARCH_URLS)]
                    _folder_idx += 1
                    log(f"[outlook-web] 未找到, 剩余{_remain}s → 轮换到 {_next_folder.split('/')[-1] or 'search'}")
                    await asyncio.sleep(6)
                    try:
                        await _pg2.goto(_next_folder, wait_until="domcontentloaded", timeout=20000)
                        await _pg2.wait_for_timeout(3500)
                    except Exception: pass

            finally:
                try: await _br2.close()
                except: pass
    except Exception as _owe:
        log(f"[outlook-web] 异常: {_owe}")

    log("[mail] 所有方法均失败，返回 None"); return None


async def _complete_via_verify_url(page, verify_url: str, close_fn=None) -> dict | None:
    """导航到Replit action-code URL，完成邮件验证+username填写。返回result dict或None。"""
    try:
        await page.goto(verify_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)
        try:
            await page.wait_for_selector(
                'input[name="username"], input[placeholder*="username" i], #username',
                timeout=30000)
            log("[verify] ✅ username字段出现 → fill_step2")
            err_v = await fill_step2(page)
            if err_v:
                return {"ok": False, "error": err_v, "phase": "verify_step2_err"}
            log("[verify] ✅ 注册+邮件验证完成")
            try:
                _u = USERNAME if 'USERNAME' in globals() else ""
                _e = EMAIL if 'EMAIL' in globals() else ""
                _sp = await _save_replit_state(page.context, _u, _e, {"path":"verify_done"})
                return {"ok": True, "phase": "done", "state_path": _sp or ""}
            except Exception as _se:
                log(f"[state] ⚠ {_se}")
                return {"ok": True, "phase": "done"}
        except Exception:
            url_v = page.url
            log(f"[verify] username字段未出现, url={url_v[:80]}")
            if any(x in url_v.lower() for x in ("dashboard","home","~","/@","replit.com/@")):
                try:
                    _u = USERNAME if 'USERNAME' in globals() else ""
                    _e = EMAIL if 'EMAIL' in globals() else ""
                    _sp = await _save_replit_state(page.context, _u, _e, {"path":"done_via_verify"})
                    return {"ok": True, "phase": "done_via_verify", "state_path": _sp or ""}
                except Exception as _se:
                    log(f"[state] ⚠ {_se}")
                    return {"ok": True, "phase": "done_via_verify"}
    except Exception as _gv:
        log(f"[verify] 导航失败: {_gv}")
    return None



# v7.78m — in-page exit-IP probe.
# register 流程之前用 curl/get_exit_ip(_camoufox) 测的 exit_ip 有 4 种来源歧义:
# (a) [CDP] curl --interface CloudflareWARP ipify  (broker WARP iface)
# (b) [CDP] fallback curl ipify                     (VPS 默认路由)
# (c) get_exit_ip_camoufox(proxy_cfg)               (camoufox + proxy)
# (d) get_exit_ip(pw, proxy_cfg)                    (playwright + proxy)
# 这 4 种测出来的 IP 不一定等于 signup HTTP 实际 source IP, 也不一定等于
# cf_clearance 发放时浏览器看到的 IP (因为 broker 出口 vs new_context 的
# proxy 出口可能不同, 且 xray pool outbound 在实时漂移).
#
# 唯一 ground truth: signup 完成后, 在已通过 CF challenge 的 page 上下文里
# fetch ipify - 此时的源 IP 一定等于 cf_clearance/connect.sid 绑定 IP, 也
# 是 replay 时必须严格复刻的 IP.
async def _probe_inpage_exit_ip(page) -> str:
    """在 page 内 fetch ipify, 拿浏览器 chromium 链路实际出口 IP."""
    try:
        ip = await page.evaluate("""async () => {
            try {
                const r = await fetch("https://api.ipify.org/?format=json", {cache: "no-store"});
                const j = await r.json();
                return j.ip || "";
            } catch(e) { return ""; }
        }""")
        if ip and isinstance(ip, str) and ip.count(".") == 3:
            return ip.strip()
    except Exception as e:
        log(f"[inpage-ip] err: {e}")
    return ""


async def _probe_inpage_fingerprint(page) -> dict:
    """v7.78o: signup 完成后, 在 page 内 evaluate 拿浏览器实际 navigator/screen
    指纹, 写到 result.fingerprint. CDP/playwright 路径下与 _CTX_FINGERPRINT 一致,
    camoufox 路径下拿到的是 firefox 真实指纹 (跟硬编码的 chromium 默认不同)."""
    try:
        fp = await page.evaluate("""async () => {
            const n = navigator, s = screen;
            const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
            return {
                user_agent: n.userAgent,
                viewport: {width: window.innerWidth, height: window.innerHeight},
                screen: {width: s.width, height: s.height},
                device_scale_factor: window.devicePixelRatio || 1,
                is_mobile: /Mobi|Android/i.test(n.userAgent),
                has_touch: ('ontouchstart' in window) || (n.maxTouchPoints||0)>0,
                locale: n.language || n.languages?.[0] || 'en-US',
                timezone_id: tz || 'America/Los_Angeles',
                color_scheme: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
                platform: n.platform || '',
                hardware_concurrency: n.hardwareConcurrency || 0,
                device_memory: n.deviceMemory || null,
                languages: Array.from(n.languages || []),
            };
        }""")
        if isinstance(fp, dict) and fp.get("user_agent"):
            return fp
    except Exception as e:
        log(f"[inpage-fp] err: {e}")
    return {}


_WARP_CIDR_PREFIXES = (
    "104.28.", "162.158.", "172.69.", "172.70.", "108.162.",
    "131.0.72.", "188.114.", "190.93.", "197.234.24",
    "199.27.128.", "104.16.", "104.17.",
)
_WARP_PROXY_PORT = 40000  # broker 用 socks5://127.0.0.1:40000 = warp-cli proxy

def _ip_is_warp(ip: str) -> bool:
    return bool(ip) and isinstance(ip, str) and any(ip.startswith(p) for p in _WARP_CIDR_PREFIXES)

async def _finalize_exit_ip(result: dict, page) -> None:
    """v7.78m+v7.78o: signup 成功后一次到位:
    1. in-page fetch ipify → ground truth exit_ip (覆写 preflight 值)
    2. in-page navigator/screen 探针 → ground truth fingerprint
    3. 写 result.user_agent / result.fingerprint, accounts.ts INSERT 拿得到
    4. v7.84: in-page IP 落 CF WARP CIDR 时, 写 result.actual_proxy_port=40000
       让 accounts.ts INSERT 拿真实 proxy_port (broker WARP 路径) 而不是 xray
       hint port. 这是 v7.82 修了 exit_ip 字段后, 对 proxy_port 字段的对称修复.
    全部用 try/except 包住, 避免 page 异常影响 signup-成功 状态."""
    try:
        real = await _probe_inpage_exit_ip(page)
        if real:
            result["exit_ip_original"] = result.get("exit_ip", "")
            result["exit_ip"] = real
            result["exit_ip_real"] = real
            result["exit_ip_source"] = "inpage_post_signup"
            log(f"[inpage-ip] ground truth = {real} (覆盖 preflight {result.get('exit_ip_original','')})")
            # v7.84: 推断真实 proxy_port — WARP CIDR 出口 ↔ broker 走的是 WARP socks
            if _ip_is_warp(real):
                result["actual_proxy_port"] = _WARP_PROXY_PORT
                result["actual_proxy_kind"] = "warp"
                log(f"[inpage-ip] real exit ∈ WARP CIDR → actual_proxy_port={_WARP_PROXY_PORT}")
        else:
            result["exit_ip_source"] = "preflight_only"
            log(f"[inpage-ip] probe 失败, 保留 preflight 值 {result.get('exit_ip','')}")
    except Exception as e:
        log(f"[inpage-ip] _finalize_exit_ip exit_ip block err (ignored): {e}")

    try:
        fp = await _probe_inpage_fingerprint(page)
        if fp:
            # 与 _CTX_FINGERPRINT 同 schema, replay_session.py 直接 load 用
            result["fingerprint"] = fp
            result["user_agent"] = fp.get("user_agent", "")
            result["fingerprint_source"] = "inpage_post_signup"
            log(f"[inpage-fp] captured ua={fp.get('user_agent','')[:60]}... viewport={fp.get('viewport')} tz={fp.get('timezone_id')}")
        else:
            # 兜底: 用 _CTX_FINGERPRINT (CDP 路径设过) 或保留空
            ctx_fp = globals().get("_CTX_FINGERPRINT") or {}
            if ctx_fp:
                result["fingerprint"] = ctx_fp
                result["user_agent"] = ctx_fp.get("user_agent", "")
                result["fingerprint_source"] = "ctx_fallback"
                log(f"[inpage-fp] probe 失败, 用 _CTX_FINGERPRINT 兜底")
            else:
                result["fingerprint_source"] = "missing"
                log(f"[inpage-fp] probe 失败 + 无 _CTX_FINGERPRINT, fingerprint 字段空")
    except Exception as e:
        log(f"[inpage-fp] _finalize_exit_ip fp block err (ignored): {e}")


# ── session 持久化 ─────────────────────────────────────────────────────────
# 注册/登录成功时把 cookies+localStorage 落到 .state/replit/<username>.json,
# 下次 replit_login.py 直接 load_state 进 context, 0 captcha 0 cf_challenge,
# 复用直到 connect.sid 过期 (Replit cookieExpiresAt ≈ 7 天).
_CTX_FINGERPRINT: dict = {}
STATE_DIR = "/root/Toolkit/.state/replit"

async def _save_replit_state(ctx, username: str, email: str = "", extra: dict | None = None):
    """Persist context.storage_state to disk keyed by username."""
    if not username:
        return None
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        path = os.path.join(STATE_DIR, f"{username}.json")
        st = await ctx.storage_state()
        # decorate with metadata for downstream tooling
        st["_meta"] = {
            "username": username,
            "email": email,
            "saved_at": int(__import__("time").time()),
            "fingerprint": _CTX_FINGERPRINT,
            **(extra or {}),
        }
        import json as _j
        with open(path, "w") as f:
            _j.dump(st, f, indent=2)
        log(f"[state] ✅ saved {len(st.get('cookies',[]))} cookies → {path}")
        return path
    except Exception as e:
        log(f"[state] ⚠ save failed: {e}")
        return None

async def attempt_register(pw_module, proxy_cfg, stealth_fn, exit_ip: str) -> dict:
    result = {"ok": False, "phase": "init", "error": "", "exit_ip": exit_ip}

    # ── v8.08 — TRUE same-IP fix ───────────────────────────────────────────
    # 上层 orchestrator (accounts.ts) 把 SOCKS hint 端口 (e.g. 10822) 作为
    # proxy_cfg 传进来; chromium 主 ctx 默认用它. 但是:
    #   * cf_clearance 由 broker chromium 在 WARP 出口 (104.28.x) 颁发
    #   * google-route (v8.07) 把 *.google PIN 到 WARP 40000 → token mint 在 WARP IP
    #   * 主 ctx 仍走 SOCKS hint → replit.com signup POST 从另一个 IP 出去
    # 三 IP 不一致 → reCAPTCHA siteverify 检测到 token-mint-IP ≠ submit-IP
    # → 评分极低 → Replit 返回 code:1 "Your captcha token is invalid".
    # 修复: broker=warp 时, 把主 ctx proxy 也强制 PIN 到 WARP 40000,
    # 保证 replit.com POST + recaptcha mint + cf_clearance 三件全在 WARP IP.
    try:
        import json as _jbf08
        with open("/tmp/replit-broker/exit.json","r") as _bf08:
            _bj08 = _jbf08.load(_bf08)
        _fam08 = (_bj08.get("family") or "").strip().lower()
        # v8.11 — opt-out switch to bypass WARP override (let chromium use orchestrator
        # SOCKS5 proxy = real residential/DC IP for higher reCAPTCHA Enterprise score).
        # Trade-off: cf_clearance from broker WARP IP may be invalidated → CF may issue
        # fresh challenge on signup nav. Use NO_WARP_OVERRIDE=1 env to enable.
        _no_warp = os.environ.get("NO_WARP_OVERRIDE","").strip() in ("1","true","yes")
        if _fam08 == "warp" and not _no_warp:
            log(f"[attempt-register] v8.08 broker=warp → main ctx proxy override: {proxy_cfg} → socks5://127.0.0.1:40000 (IP-同源 with cf_clearance + recaptcha mint)")
            proxy_cfg = {"server": "socks5://127.0.0.1:40000"}
        elif _fam08 == "warp" and _no_warp:
            log(f"[attempt-register] v8.11 NO_WARP_OVERRIDE=1 → keep orchestrator proxy_cfg={proxy_cfg} (better reCAPTCHA score, may CF challenge)")
        elif _fam08 == "socks":
            _bport08 = str(_bj08.get("port") or "").strip()
            if _bport08:
                log(f"[attempt-register] v8.08 broker=socks → main ctx proxy override: {proxy_cfg} → socks5://127.0.0.1:{_bport08} (IP-同源 with broker SOCKS exit)")
                proxy_cfg = {"server": f"socks5://127.0.0.1:{_bport08}"}
    except Exception as _e08:
        log(f"[attempt-register] v8.08 broker hint read failed (keep orchestrator proxy_cfg): {_e08}")

    browser = await pw_module.chromium.launch(
        headless=HEADLESS, proxy=proxy_cfg,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
              "--disable-blink-features=AutomationControlled", "--disable-web-security"],
    )
    if USE_CDP:
        # Mirror broker sticky context (browser-model renderer.ts) EXACTLY
        # so CF accepts the cf_clearance cookie issued there. Any drift in
        # UA / viewport / timezone / Accept-Language / sec-ch-ua hints
        # forces CF to re-challenge.
        # v7.57 revert v7.52: 不再强行 proxy=proxy_cfg。CDP 路径继承 broker WARP 出口，
        # 与 cf_clearance 来源 IP / Google NID trust cookies 起源 IP 保持一致，
        # 恢复 7d89395 时段 reCAPTCHA Enterprise 高分拓扑。
        # v7.78k — 把 context 指纹快照写到 _CTX_FINGERPRINT，便于 register 完成时
        # 一并回写 DB (accounts.user_agent + accounts.fingerprint_json)，replay 时
        # 重建一致上下文，避免 Cloudflare/Replit 风控因 UA/viewport/timezone 漂移
        # 把 cf_clearance + connect.sid 拉黑。
        global _CTX_FINGERPRINT
        _CTX_FINGERPRINT = {
            "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "viewport": {"width": 1920, "height": 1040},
            "screen": {"width": 1920, "height": 1080},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
            "locale": "en-US",
            "timezone_id": "America/Los_Angeles",
            "color_scheme": "light",
            "extra_http_headers": {
                "Accept-Language": "en-US,en;q=0.9",
                "sec-ch-ua": "\"Chromium\";v=\"145\", \"Not:A-Brand\";v=\"99\", \"Google Chrome\";v=\"145\"",
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": "\"Linux\"",
            },
            "platform": "Linux x86_64",
        }
        ctx = await browser.new_context(
            user_agent=_CTX_FINGERPRINT["user_agent"],
            viewport=_CTX_FINGERPRINT["viewport"],
            screen=_CTX_FINGERPRINT["screen"],
            device_scale_factor=_CTX_FINGERPRINT["device_scale_factor"],
            is_mobile=_CTX_FINGERPRINT["is_mobile"],
            has_touch=_CTX_FINGERPRINT["has_touch"],
            locale=_CTX_FINGERPRINT["locale"],
            timezone_id=_CTX_FINGERPRINT["timezone_id"],
            color_scheme=_CTX_FINGERPRINT["color_scheme"],
            ignore_https_errors=True,
            extra_http_headers=_CTX_FINGERPRINT["extra_http_headers"],
        )
    else:
        ctx  = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-US", user_agent=UA)
    page = await ctx.new_page()
    try:
        def _diag_console_handler(msg):
            try:
                t = msg.text or ""
                if "[DIAG-RC]" in t:
                    log(f"[DIAG-RC-CON] {t}")
            except Exception:
                pass
        page.on("console", _diag_console_handler)
        log("[DIAG-RC] console listener attached")
    except Exception as _ce:
        log(f"[DIAG-RC] console listener failed: {_ce}")

    # v8.00 — playwright_stealth DISABLED on CDP path:
    # it forcibly sets navigator.webdriver=false (real Chrome=undefined),
    # overriding broker's correct STEALTH_INIT and producing the strongest
    # reCAPTCHA Enterprise bot tell. Broker STEALTH_INIT + v8.00 main-world
    # inject cover all the same surface anyway.
    if stealth_fn and not USE_CDP:
        try:
            await stealth_fn(page)
        except Exception as e:
            log(f"stealth 注入失败: {e}")
    elif stealth_fn and USE_CDP:
        log('[stealth-v8.00] playwright_stealth SKIPPED on CDP (broker STEALTH_INIT owns webdriver)')

    # Canvas 2D 噪声（独立注入，stealth 不覆盖）
    try:
        await page.add_init_script(_CANVAS_WEBGL_NOISE_JS)
        log("Canvas 2D + WebGL 完整注入 ✓")
    except Exception as e:
        log(f"Canvas 噪声注入失败: {e}")
    try:
        _DIAG_RECAPTCHA_PROBE = r"""
        (() => {
          try {
            const tag = (msg) => {
              try { console.log("[DIAG-RC]", msg); } catch(e){}
              try { document.title = "[DIAG-RC] " + String(msg).slice(0,200); } catch(e){}
            };
            // v8.17 — bulletproof getter/setter wrapper that survives any later
            // reassignment of grecaptcha.enterprise.execute by Replit's bundle.
            const wrapExec = (entObj) => {
              if (!entObj || entObj.__diag_wrapped_v817) return false;
              let cur = entObj.execute;
              const wrap = (fn) => {
                if (!fn || fn.__diag_wrapped_fn) return fn;
                const w = function(sk, opts){
                  const action = (opts && opts.action) || "(no-action)";
                  const skP = String(sk||"").slice(0,18);
                  const t0 = Date.now();
                  tag("execute CALLED sk=" + skP + " action=" + action);
                  let p;
                  try { p = fn.call(this, sk, opts); }
                  catch(e){ tag("execute THREW " + String(e&&e.message||e).slice(0,160)); throw e; }
                  if (p && typeof p.then === "function") {
                    p.then((tok) => {
                      const tp = String(tok||"").slice(0,20);
                      tag("execute RESOLVED action=" + action + " len=" + (tok?tok.length:0) + " prefix=" + tp + " dt=" + (Date.now()-t0) + "ms");
                    }, (err) => {
                      tag("execute REJECTED action=" + action + " err=" + String(err&&err.message||err).slice(0,160) + " dt=" + (Date.now()-t0) + "ms");
                    });
                  } else {
                    tag("execute SYNC-RET action=" + action + " type=" + typeof p);
                  }
                  return p;
                };
                w.__diag_wrapped_fn = true;
                return w;
              };
              try {
                Object.defineProperty(entObj, "execute", {
                  configurable: true,
                  enumerable: true,
                  get(){ return wrap(cur); },
                  set(v){ cur = v; tag("execute REASSIGNED type=" + typeof v); },
                });
                entObj.__diag_wrapped_v817 = true;
                tag("wrapped grecaptcha.enterprise.execute (defineProperty v8.17)");
                return true;
              } catch(e){
                tag("defineProperty FAILED " + String(e&&e.message||e).slice(0,160));
                try {
                  entObj.execute = wrap(entObj.execute);
                  entObj.__diag_wrapped_v817 = true;
                  tag("wrapped grecaptcha.enterprise.execute (assign fallback)");
                  return true;
                } catch(e2){ return false; }
              }
            };
            const tryWrap = () => {
              try {
                const ge = window.grecaptcha;
                if (ge && ge.enterprise) return wrapExec(ge.enterprise);
              } catch(e){}
              return false;
            };
            if (!tryWrap()) {
              const t0 = Date.now();
              const iv = setInterval(() => { if (tryWrap() || Date.now()-t0>30000) clearInterval(iv); }, 200);
            }
            // v8.17 — also detect VISIBLE bframe challenge (image grid / audio).
            // If a bframe iframe becomes user-visible, score is too low and the
            // user must solve a challenge -> we will deadlock waiting forever.
            const checkChallenge = () => {
              try {
                const frames = document.querySelectorAll("iframe[src*='bframe'], iframe[title*='challenge' i], iframe[src*='recaptcha/api2/bframe']");
                for (const f of frames) {
                  const r = f.getBoundingClientRect();
                  const cs = window.getComputedStyle(f);
                  const visible = r.width > 50 && r.height > 50 && cs.visibility !== "hidden" && cs.display !== "none" && parseFloat(cs.opacity||"1") > 0.1;
                  if (visible && !f.__diag_seen) {
                    f.__diag_seen = true;
                    tag("VISIBLE-CHALLENGE bframe " + Math.round(r.width) + "x" + Math.round(r.height) + " src=" + String(f.src||"").slice(0,80));
                  }
                }
              } catch(e){}
            };
            setInterval(checkChallenge, 500);
          } catch(e){}
        })();
        """
        await page.add_init_script(_DIAG_RECAPTCHA_PROBE)
        log("[DIAG-RC] grecaptcha.execute probe v8.17 injected (defineProperty+challenge-watch) ✓")
    except Exception as _de:
        log(f"[DIAG-RC] inject failed: {_de}")


    try:
        result["phase"] = "navigate"
        # CDP path: pre-warm broker sticky context to obtain cf_clearance,
        # then inject cookies into our context so signup goto returns 200.
        if USE_CDP:
            try:
                import urllib.request as _ur, json as _json
                log("[CDP] cf-warmup → replit.com/signup …")
                with _ur.urlopen(
                    "http://localhost:8092/api/cf-warmup?url=https%3A%2F%2Freplit.com%2Fsignup&googleWarmup=1&timeoutMs=90000",
                    timeout=120,
                ) as _r:
                    _wd = _json.loads(_r.read())
                # Mirror broker's STEALTH_INIT into our CDP-attached context so
                # navigator.webdriver / canvas / WebGL fingerprint match the
                # context that originally got cf_clearance from CF.
                _stealth = _wd.get("stealthInit") or ""
                if _stealth:
                    try:
                        await ctx.add_init_script(_stealth)
                        log(f"[CDP] stealth init script applied ({len(_stealth)}B)")
                    except Exception as _se:
                        log(f"[CDP] stealth init apply failed: {_se}")
                _cks = _wd.get("cookies") or []
                # ⚠ broker chromium 可能有残留登录态 (e.g. connect.sid),
                # 注入会让 /signup 自动跳到该用户 dashboard, 导致 signup_email_field_timeout.
                # 只放行 CF / 反爬必要 cookies, 剔除一切 Replit 会话/认证 cookies.
                _AUTH_COOKIE_BLACKLIST = {
                    "connect.sid", "replit_authed", "replit-user", "replit_user",
                    "replit-session", "replit_session", "sid", "__session",
                    "REPL_AUTH", "ajs_anonymous_id", "ajs_user_id",
                }
                _inj = []
                _skipped_auth = []
                for _c in _cks:
                    if not _c or not _c.get("name"):
                        continue
                    _name = _c["name"]
                    if _name in _AUTH_COOKIE_BLACKLIST:
                        _skipped_auth.append(_name)
                        continue
                    _dom = _c.get("domain") or "replit.com"
                    _ck = {
                        "name": _name,
                        "value": _c.get("value", ""),
                        "domain": _dom,
                        "path": _c.get("path", "/"),
                        "secure": bool(_c.get("secure", True)),
                        "httpOnly": bool(_c.get("httpOnly", False)),
                    }
                    _ss = _c.get("sameSite")
                    if _ss in ("Lax", "Strict", "None"):
                        _ck["sameSite"] = _ss
                    _exp = _c.get("expires")
                    if isinstance(_exp, (int, float)) and _exp > 0:
                        _ck["expires"] = _exp
                    _inj.append(_ck)
                # ⚠ 关键：只清 replit.com 域的 cookies (移除任何残留 connect.sid/__session 等),
                # 绝不能清 .google.com / .gstatic.com / .recaptcha.net — broker 在 cf-warmup 时
                # 加载 reCAPTCHA Enterprise 积累的 NID/SID/HSID/SAPISID/OTZ/__Secure-* trust cookies
                # 是 execute() score 的关键证据，全清会让 score 跌到 0.1 → server code:2 拒绝。
                _domain_cleared = []
                try:
                    cur_cks = await ctx.cookies()
                    # 用 expires=0 让 replit 域的 auth blacklist cookies 立即过期
                    _to_expire = []
                    for _ck in cur_cks:
                        _ck_dom = (_ck.get("domain") or "").lstrip(".").lower()
                        _ck_name = _ck.get("name") or ""
                        if _ck_dom.endswith("replit.com") and _ck_name in _AUTH_COOKIE_BLACKLIST:
                            _to_expire.append({
                                "name": _ck_name, "value": "",
                                "domain": _ck.get("domain"), "path": _ck.get("path", "/"),
                                "expires": 0,
                            })
                            _domain_cleared.append(_ck_name)
                    if _to_expire:
                        await ctx.add_cookies(_to_expire)
                except Exception as _ce:
                    log(f"[CDP] cookie cleanup err (忽略): {_ce}")
                if _inj:
                    await ctx.add_cookies(_inj)
                # v7.49 — 注入 broker 回传的 .google/.gstatic/.youtube/.recaptcha 跨域信任 cookies
                # (NID/AEC/SOCS/LOGIN_INFO 等, harvest 自 warmupGoogleSession sticky context)
                _g_inj = []
                for _gc in (_wd.get("googleCookies") or []):
                    _gname = _gc.get("name", "")
                    if not _gname: continue
                    _gck = {
                        "name": _gname,
                        "value": _gc.get("value", ""),
                        "domain": _gc.get("domain") or ".google.com",
                        "path": _gc.get("path", "/"),
                        "secure": bool(_gc.get("secure", True)),
                        "httpOnly": bool(_gc.get("httpOnly", False)),
                    }
                    _gss = _gc.get("sameSite")
                    if _gss in ("Lax", "Strict", "None"):
                        _gck["sameSite"] = _gss
                    _gexp = _gc.get("expires")
                    if isinstance(_gexp, (int, float)) and _gexp > 0:
                        _gck["expires"] = _gexp
                    _g_inj.append(_gck)
                if _g_inj:
                    try:
                        await ctx.add_cookies(_g_inj)
                        _names = sorted({c["name"] for c in _g_inj})
                        log(f"[CDP] ✅ 注入 {len(_g_inj)} 个 google trust cookies: {_names}")
                    except Exception as _gie:
                        log(f"[CDP] google cookies 注入失败: {_gie}")
                else:
                    log("[CDP] ⚠ broker 没回传 googleCookies (warmupGoogleSession 可能失败)")
                # v7.56 real-human storage_state seed: prefer cookies captured from a real signup.
                # Falls back to v7.53 fake generated cookies if the seed file is absent or empty.
                _real_seed_loaded = False
                _real_seed_names = set()
                # v8.18 — kill-switch for Replit-domain seed cookies. Evidence (rpl_mogny0wr_xw7b
                # Apr 27 04:00) shows /signup SSR returns 88KB shell with zero form/input/recaptcha
                # script when these cookies are pre-injected. Suspected cause: replit_statsig_stable_id
                # buckets us into a feature-flag cohort that strips the form.
                _SKIP_REPLIT_SEED = os.environ.get("DISABLE_REPLIT_SEED_COOKIES", "1") == "1"
                if _SKIP_REPLIT_SEED:
                    log("[CDP] v8.18 SKIP all replit-domain seed cookies (DISABLE_REPLIT_SEED_COOKIES=1) — let replit assign fresh stable_id/gating_id/fbp")
                try:
                    import json as _js, os as _os
                    _seed_path = _os.environ.get("REPLIT_TRUST_SEED_PATH", "/root/.replit-trust-seed.json")
                    if not _SKIP_REPLIT_SEED and _os.path.exists(_seed_path):
                        with open(_seed_path) as _sf:
                            _seed_doc = _js.load(_sf)
                        _seed_cookies = []
                        for _sc in _seed_doc.get("cookies", []):
                            _name = _sc.get("name")
                            if not _name: continue
                            # Skip post-auth + short-lived CF cookies even if file has them (defensive)
                            if _name in ("connect.sid", "__Host-session-sig", "replit_authed",
                                         "ld_uid", "__Host-wr-tc", "__cf_bm", "cf_clearance",
                                         "__stripe_sid", "_dd_s"):
                                continue
                            _ck = {
                                "name": _name,
                                "value": str(_sc.get("value", "")),
                                "domain": _sc.get("domain", ".replit.com"),
                                "path": _sc.get("path", "/"),
                                "secure": bool(_sc.get("secure", True)),
                                "sameSite": _sc.get("sameSite", "Lax"),
                            }
                            _seed_cookies.append(_ck)
                            _real_seed_names.add(_name)
                        if _seed_cookies:
                            await ctx.add_cookies(_seed_cookies)
                            _real_seed_loaded = True
                            log(f"[CDP] ✅ 注入 {len(_seed_cookies)} real-human seed cookies (source={_seed_doc.get('source','?')}): {sorted(_real_seed_names)}")
                except Exception as _rse:
                    log(f"[CDP] real-human seed 注入失败 (fallback to fake): {_rse}")
                # v7.53 trust seed: pre-age cookies so replit treats us as returning visitor
                # Reference: successful signup cookie jar had _fbp 9d old + matching marketing_attribution
                # v7.56: only inject names NOT already covered by real seed (avoids overwriting real values)
                try:
                    if _SKIP_REPLIT_SEED:
                        raise RuntimeError("v8.18-skip-replit-seed")
                    import uuid as _u, time as _t, random as _r
                    _nm = int(_t.time() * 1000)
                    _am = _nm - _r.randint(7, 14) * 86400 * 1000
                    _fbp = f"fb.1.{_am}.{_r.randint(10**14, 10**15-1)}"
                    _stb = str(_u.uuid4())
                    _gat = str(_u.uuid4())
                    _smid = f"{_u.uuid4()}{_r.randbytes(4).hex()[:8]}"
                    _attr = '{"first_fbp":"' + _fbp + '","last_fbp":"' + _fbp + '"}'
                    _seed_all = [
                        ("_fbp", _fbp),
                        ("marketing_attribution", _attr),
                        # v7.56: always re-randomize statsig_stable_id and gating_id per-session so
                        # each new signup looks like a distinct returning visitor, not the same one.
                        ("replit_statsig_stable_id", _stb),
                        ("gating_id", _gat),
                        ("__stripe_mid", _smid),
                        ("gfa_ref", "https://outlook.live.com/"),
                    ]
                    _force_re = {"replit_statsig_stable_id", "gating_id"}
                    _seed = []
                    _skipped = []
                    for _sn, _sv in _seed_all:
                        if _real_seed_loaded and _sn in _real_seed_names and _sn not in _force_re:
                            _skipped.append(_sn)
                            continue
                        _seed.append({"name":_sn,"value":_sv,"domain":".replit.com","path":"/","secure":True,"sameSite":"Lax"})
                    if _seed:
                        await ctx.add_cookies(_seed)
                        log(f"[CDP] ✅ 注入 {len(_seed)} fake-seed cookies (_fbp aged {(_nm - _am)//86400000}d, stable_id={_stb[:8]}…); 跳过 real-seed 覆盖: {_skipped}")
                    else:
                        log(f"[CDP] fake-seed 全部被 real-seed 覆盖，跳过")
                except Exception as _se:
                    if "v8.18-skip-replit-seed" in str(_se):
                        log("[CDP] v8.18 fake-seed cookies skipped (kill-switch active)")
                    else:
                        log(f"[CDP] trust seed 注入失败: {_se}")
                if _skipped_auth:
                    log(f"[CDP] ⚠ 跳过 broker 给的 auth cookies: {_skipped_auth}")
                if _domain_cleared:
                    log(f"[CDP] ⚠ 已过期 broker 残留 replit auth cookies: {_domain_cleared}")
                log(f"[CDP] cf_clearance={_wd.get('cfClearance')} cookies_injected={len(_inj)} warmup_ms={_wd.get('ms')} attrs=full")
                # v7.47 dump .google cookies for score diagnostics
                try:
                    _all_cks = await ctx.cookies()
                    _g_cks = [c for c in _all_cks if "google" in (c.get("domain","").lower()) or "gstatic" in (c.get("domain","").lower()) or "recaptcha" in (c.get("domain","").lower())]
                    _g_names = sorted({c.get("name","") for c in _g_cks})
                    log(f"[CDP] google/gstatic/recaptcha cookies count={len(_g_cks)} names={_g_names}")
                except Exception as _gce:
                    log(f"[CDP] google cookie dump err: {_gce}")
                # Per-host route: divert *.google.com / *.gstatic.com / *.recaptcha.net /
                # *.youtube.com requests through clean non-GCP SOCKS5 exits so
                # reCAPTCHA Enterprise sees a fresh IP instead of WARP-via-GCP.
                # v7.72: 默认始终禁用 google_proxy_route — 让 chromium 原生出口同时处理
                # execute() 和 signup POST, 保证 IP 一致 → 不再 code:2 (mismatch).
                # v7.71 restored: enable google_proxy_route when BROWSER_PROXY non-empty (VLESS).
                # v7.78g — RESTORE 0391f15 working behavior: google_proxy_route ALWAYS attached.
                # 0391f15 (e2e verified: tylerreyes307@outlook.com -> userId=58078470) had NO gate
                # here — *.google traffic was unconditionally routed via clean non-GCP SOCKS pool.
                # The v7.71-v7.78f BROWSER_PROXY/Tor gate was a misdiagnosis of code:2 (which is
                # actually a LIVE/LOCKED token-action mismatch, not an IP mismatch — see route-force
                # at line ~825 which already handles that). DISABLE_GOOGLE_ROUTE=1 kept as escape hatch.
                # v7.94 — REVERT v7.93e back to v7.78g always-on. v7.78q-era e2e-verified
                # behavior (tylerreyes307@outlook.com → userId=58078470) had google_proxy_route
                # unconditionally attached. The httpx-TLS-mismatch concern was wrong: in v7.78q
                # topology broker chromium ALSO exits via clean datacenter SOCKS (matching
                # google_proxy_route's pool family), so token-gen IP segment == submit IP
                # segment. DISABLE_GOOGLE_ROUTE=1 kept as escape hatch.
                # v7.99 — broker-family-aware google-route, 适配当前退化基础设施.
                #
                # ★ 关键事实 (2026-04-25 实测, 必须读懂否则永远绕不出来):
                #   xray 上游订阅已全部迁移到 CF Workers, 13 个 SOCKS 子节点
                #   (10820/22/23/24/25/26/28/30/36/37/45) 出口 IP 全部在 AS13335
                #   Cloudflare 104.28.x 段. WARP (40000) 也在 AS13335 104.28.x.
                #   唯一非 CF 出口: DIRECT VPS 45.205.27.69 (AS8796 FASTNET DATA).
                #
                #   → v7.78q/r 时代仰仗的"清洁非 GCP datacenter SOCKS 池" (10824 Kirino,
                #     10826 DO, 10830 MULTACOM 等真小型 ISP datacenter) 整个不复存在.
                #     google_proxy_route.DEFAULT_POOL 现在 = 同样 13 个 CF 端口.
                #
                # ★ 历史误诊回顾 (避免下次再绕进去):
                #   v7.78q 时 "always attach DEFAULT_POOL" 是对的 (池子真清洁).
                #   v7.95 引入 broker-family-aware SKIP/PIN, 当时被骂"误诊 code:1",
                #     其实是误打误撞做对了 — 因为基础设施在 v7.79-v7.95 期间逐步退化,
                #     SKIP-on-warp/direct 反而保证了 broker 与 *.google 同家族.
                #   v7.97 commit message 错把 code:2 解释为 "IP consistency" — 而 v7.78r
                #     原始注释 (line ~2050) 明说 "code:2 is LIVE/LOCKED token-action
                #     mismatch, NOT IP mismatch — see route-force at line ~825". 但 v7.95
                #     的 family-aware SKIP/PIN 行为本身仍是适配当前退化设施的最优解.
                #   v7.98 又把 SKIP 撤掉, 强行 always-attach DEFAULT_POOL, 在当前 CF-only
                #     SOCKS 基础设施下变成: broker=DIRECT(AS8796) vs *.google=CF(AS13335)
                #     跨家族, reCAPTCHA Enterprise 看到 token-IP 与 submit-IP 完全不同
                #     ASN → 评低 / token invalid / code:1.
                #
                # ★ v7.99 决策矩阵 (适配当前退化基础设施, 双轨保护):
                #   broker=direct (45.205.27.69 AS8796) → SKIP google-route
                #     → *.google 也走 VPS 公网 → 与 broker 同 IP, 完全一致, 评分 OK.
                #   broker=warp (104.28.x AS13335) → SKIP google-route
                #     → *.google 也走 WARP → 与 broker 同段 (CF), 一致 (评分受 CF 段影响
                #     但不会 mismatch, code:1 概率显著低于跨段).
                #   broker=socks (理论上的非 CF SOCKS, 当前不会发生) → PIN GOOGLE_PROXY_POOL
                #     到 broker 同 port → 同 IP 一致. 留这个分支为日后 xray 恢复清洁 SOCKS
                #     上游时自动启用.
                #   xray 上游恢复清洁 datacenter SOCKS 池后, 把 google_proxy_route.DEFAULT_POOL
                #     更新为新清洁端口, 然后这里改回 always-attach 即可 (v7.78q 模型).
                #
                # FORCE_GOOGLE_ROUTE=1 保留为 escape hatch (强制走 DEFAULT_POOL,
                # 用于人工调试 / DEFAULT_POOL 恢复后的快速验证).
                # DISABLE_GOOGLE_ROUTE=1 保留为完全禁用 escape hatch.
                _disable_groute = os.environ.get("DISABLE_GOOGLE_ROUTE","").strip() in ("1","true","yes")
                _force_groute = os.environ.get("FORCE_GOOGLE_ROUTE","").strip() in ("1","true","yes")
                # v7.95b — broker 与 api-server 是不同 pm2 进程, env 不互通.
                # broker 启动时把 BROKER_EXIT_FAMILY 写到 /tmp/replit-broker/exit.json,
                # 这里读文件优先, env 兜底.
                _broker_fam = ""
                _broker_port = ""
                try:
                    import json as _jbf
                    with open("/tmp/replit-broker/exit.json","r") as _bf:
                        _bj = _jbf.load(_bf)
                    _broker_fam = (_bj.get("family") or "").strip().lower()
                    _broker_port = str(_bj.get("port") or "").strip()
                    log(f"[CDP] broker exit hint from /tmp/replit-broker/exit.json → family={_broker_fam} port={_broker_port or 'N/A'}")
                except Exception as _bfe:
                    log(f"[CDP] /tmp/replit-broker/exit.json 读取失败 → fall back env: {_bfe}")
                    _broker_fam = (os.environ.get("BROKER_EXIT_FAMILY","") or "").strip().lower()
                    _broker_port = (os.environ.get("BROKER_EXIT_SOCKS_PORT","") or "").strip()
                if _disable_groute and not _force_groute:
                    log("[CDP] google-route SKIPPED (DISABLE_GOOGLE_ROUTE=1 explicit opt-out)")
                else:
                    # v8.03 — eliminate ambiguity: ALWAYS attach google-route to clean DEFAULT_POOL,
                    # regardless of broker family. v7.78g empirical (v7.78g+h success log:
                    # "google-route attached (v7.78g restore — *.google ALWAYS via clean non-GCP SOCKS pool)")
                    # → reCAPTCHA Enterprise score OK + signup POST 200. The v7.99 SKIP-on-direct branch
                    # was a regression (job rpl_moeaeubb_mz0o failed code:1). The v8.02 SKIP-on-warp
                    # branch was speculative (no empirical backing). Both removed.
                    # Live test 2026-04-25: WARP :40000 → google.com 200 / recaptcha.net 200 (reachable),
                    # but clean SOCKS pool 10820-10845 is the only path with empirically-validated
                    # high reCAPTCHA Enterprise score. broker=socks PIN branch retained as future-proof
                    # for clean SOCKS broker recovery.
                    try:
                        import sys as _sys, os as _os
                        _here = _os.path.dirname(_os.path.abspath(__file__))
                        if _here not in _sys.path:
                            _sys.path.insert(0, _here)
                        # broker=socks 模式下 (当前 xray 退化到全 CF 段, 此分支当前不会
                        # 触发, 留作 xray 恢复清洁 SOCKS 上游后的自动正确路径) — 强制
                        # google-route 用同 port, 保证 token-IP == submit-IP 一致.
                        if _broker_fam == "direct":
                            # v8.24 — REVERT v8.21 SKIP. v8.21 假设 (xray 池全 CF AS13335) 已过期:
                            # 2026-04-27 实测 12 端口 (10820/21/22/23/24/25/26/28/30/36/37/45)
                            # 全部 alive, 出口分布 DigitalOcean / HostPapa / Zenlayer / Cogent /
                            # Tencent / 小型 EU ISP, 无一在 CF AS13335 段. 池子已恢复清洁.
                            #
                            # broker=direct 时若 SKIP, *.google 走 DIRECT VPS 45.205.27.69 AS8796
                            # FASTNET DATA (datacenter), reCAPTCHA Enterprise 看到 datacenter ASN
                            # 直接给 ~0.1 score → Replit step1 POST 400 "captcha token is invalid
                            # (code:1)" — 这正是 rpl_mogtbmo8_l5bu / rpl_mogtduzk_n82s 实测失败的
                            # 真实根因 (2026-04-27 实证).
                            #
                            # 修复: ALWAYS attach google-route 走 DEFAULT_POOL 清洁住宅/小 ISP 出口,
                            # 让 reCAPTCHA Enterprise 看到合法住宅/edge ISP IP, score 拉回 0.5+.
                            # (v7.78q + v7.98 历史正确模型, 当池子清洁时此为最优解, 见上方注释)
                            log("[CDP] v8.24 google-route ATTACH (broker=direct + xray pool 实测清洁 → *.google 走住宅/edge SOCKS, 抬 reCAPTCHA Enterprise score)")
                            _os.environ.pop("GOOGLE_PROXY_POOL", None)
                        elif _broker_fam == "socks" and _broker_port:
                            _os.environ["GOOGLE_PROXY_POOL"] = f"socks5://127.0.0.1:{_broker_port}"
                            log(f"[CDP] v7.99 google-route pool override → socks5://127.0.0.1:{_broker_port} (sync broker exit, IP 一致)")
                        elif _broker_fam == "warp":
                            _no_warp_g = os.environ.get("NO_WARP_OVERRIDE","").strip() in ("1","true","yes")
                            if _no_warp_g:
                                # v8.11 — main ctx already escaped WARP; let *.google share that
                                # SOCKS exit too so 三件 IP-同源 in clean DC IP (better reCAPTCHA score).
                                _os.environ.pop("GOOGLE_PROXY_POOL", None)
                                log("[CDP] v8.11 NO_WARP_OVERRIDE=1 → google-route SKIP WARP PIN, use chromium main proxy (SOCKS clean) for IP-同源")
                            else:
                                # v8.07 — broker=warp 时 chromium 经 WARP socks5://:40000 出口.
                                # 必须把 *.google 也 PIN 到同一 WARP 端口, 否则 reCAPTCHA Enterprise
                                # 检测到 token-mint IP (DEFAULT_POOL clean SOCKS) ≠ submit IP (WARP)
                                # 跨段信号 → 评分极低 → code:1 reject.
                                _os.environ["GOOGLE_PROXY_POOL"] = "socks5://127.0.0.1:40000"
                                log("[CDP] v8.07 google-route pool override → socks5://127.0.0.1:40000 (broker=warp, IP 一致 via WARP backbone)")
                        elif _force_groute:
                            log("[CDP] v7.99 FORCE_GOOGLE_ROUTE=1 — 强行用 DEFAULT_POOL (调试)")
                            _os.environ.pop("GOOGLE_PROXY_POOL", None)
                        from google_proxy_route import attach_google_proxy_routing as _agr
                        await _agr(ctx, log)
                        log(f"[CDP] google-route attached (v7.99 — broker={_broker_fam or 'unknown'} family-aligned)")
                    except _GoogleRouteSkip as _gs:
                        log(f"[CDP] google-route SKIP confirmed (reason={_gs})")
                    except Exception as _ge:
                        log(f"[CDP] google-route attach failed: {_ge}")
            except Exception as _we:
                log(f"[CDP] cf-warmup err (continuing): {_we}")
        else:
            # legacy SOCKS5 path: original pre-nav warmup
            try:
                log("[pre-nav] 访问 google.com 建立会话历史…")
                await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=65000)
                await page.wait_for_timeout(_random.randint(2500, 4000))
                log("[pre-nav] 访问 github.com…")
                await page.goto("https://github.com", wait_until="domcontentloaded", timeout=65000)
                await page.wait_for_timeout(_random.randint(2000, 3500))
            except Exception as e:
                log(f"[pre-nav] 异常(忽略): {e}")

        # v7.57 移除 v7.47 patchright 侧 google.com 预热：本地 goto 会触发 .google.com
        # 域 NID 重写，覆盖 broker WARP 时段累积的高信任 cookie，反而拉低 execute() 评分。
        # broker cf-warmup 内部已对 google reCAPTCHA 资源做过预热并把 cookie 同步过来。

        log("打开 replit.com/signup ...")
        log("=== v8.00-MARKER REACHED (stealth aligned) ===")
        await page.goto("https://replit.com/signup", wait_until="domcontentloaded", timeout=75000)

        # === v7.96 STEALTH FORCE INJECTION (main-world via <script> tag) ===
        # ROOT CAUSE: 04-24 broker rewrite changed chromium.launch -> child_process.spawn.
        # Result: playwright addInitScript via connectOverCDP runs in ISOLATED world,
        # so all Object.defineProperty(Navigator.prototype, ...) silently DON'T propagate
        # to the page main world that reCAPTCHA Enterprise reads from. Probe v6 confirmed
        # that <script>-tag injection successfully writes prototype mutations into main
        # world AND they propagate back to playwright page.evaluate (proving same realm).
        # reCAPTCHA execute() runs at submit time -> stealth must be in place by then.
        try:
            _STEALTH_FORCE_v796 = """
            (() => {
                try { Reflect.defineProperty(Navigator.prototype, 'hardwareConcurrency', {get:()=>8, configurable:true}); } catch(_){}
                try { Reflect.defineProperty(Navigator.prototype, 'deviceMemory', {get:()=>8, configurable:true}); } catch(_){}
                try { Reflect.defineProperty(Navigator.prototype, 'platform', {get:()=>'Linux x86_64', configurable:true}); } catch(_){}
                try { Reflect.defineProperty(Navigator.prototype, 'language', {get:()=>'en-US', configurable:true}); } catch(_){}
                try { Reflect.defineProperty(Navigator.prototype, 'languages', {get:()=>['en-US','en'], configurable:true}); } catch(_){}
                try { Reflect.defineProperty(Navigator.prototype, 'maxTouchPoints', {get:()=>0, configurable:true}); } catch(_){}
                // v8.01 FIX: addInitScript via connect_over_cdp = ISOLATED world only.
                // Main world webdriver must be fixed HERE in this script tag.
                try { delete Navigator.prototype.webdriver; } catch(_){}
                try { delete navigator.__proto__.webdriver; } catch(_){}
                try { Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true, enumerable: false }); } catch(_){}
                try {
                    const orig = Intl.DateTimeFormat.prototype.resolvedOptions;
                    Intl.DateTimeFormat.prototype.resolvedOptions = function(){
                        const r = orig.apply(this, arguments);
                        if (!r.timeZone || r.timeZone === 'UTC') r.timeZone = 'America/Los_Angeles';
                        if (!r.locale || /^(zh|en-GB|de|fr|ja|ru|ko)/.test(r.locale)) r.locale = 'en-US';
                        return r;
                    };
                } catch(_){}
                try {
                    Date.prototype.getTimezoneOffset = function(){
                        const m = this.getUTCMonth();
                        return (m >= 2 && m <= 10) ? 420 : 480;
                    };
                } catch(_){}
                try {
                    if (!window.chrome) window.chrome = {};
                    window.chrome.runtime = window.chrome.runtime || {
                        OnInstalledReason:{}, OnRestartRequiredReason:{},
                        PlatformArch:{}, PlatformOs:{}, RequestUpdateCheckStatus:{}
                    };
                    window.chrome.app = window.chrome.app || { isInstalled:false,
                        InstallState:{DISABLED:'disabled',INSTALLED:'installed',NOT_INSTALLED:'not_installed'},
                        RunningState:{CANNOT_RUN:'cannot_run',READY_TO_RUN:'ready_to_run',RUNNING:'running'}
                    };
                    window.chrome.csi = window.chrome.csi || function(){return{};};
                    window.chrome.loadTimes = window.chrome.loadTimes || function(){return{requestTime:Date.now()/1000,startLoadTime:Date.now()/1000,commitLoadTime:Date.now()/1000,finishDocumentLoadTime:0,finishLoadTime:0,firstPaintTime:0,firstPaintAfterLoadTime:0,navigationType:'Other',wasFetchedViaSpdy:false,wasNpnNegotiated:true,npnNegotiatedProtocol:'h2',wasAlternateProtocolAvailable:false,connectionInfo:'h2'};};
                } catch(_){}
                try {
                    const wgl = WebGLRenderingContext.prototype.getParameter;
                    WebGLRenderingContext.prototype.getParameter = function(p){
                        if (p === 37445) return 'Google Inc. (Intel)';
                        if (p === 37446) return 'ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (TGL GT2), OpenGL 4.6)';
                        return wgl.apply(this, arguments);
                    };
                    if (typeof WebGL2RenderingContext !== 'undefined') {
                        const wgl2 = WebGL2RenderingContext.prototype.getParameter;
                        WebGL2RenderingContext.prototype.getParameter = function(p){
                            if (p === 37445) return 'Google Inc. (Intel)';
                            if (p === 37446) return 'ANGLE (Intel, Mesa Intel(R) Iris(R) Xe Graphics (TGL GT2), OpenGL 4.6)';
                            return wgl2.apply(this, arguments);
                        };
                    }
                } catch(_){}
                // v8.00 — REMOVED: Chrome 145 has WebGPU; forcing null is feature-mismatch tell.
                try {
                    Reflect.defineProperty(screen, 'availWidth',  {get:()=>1920, configurable:true});
                    Reflect.defineProperty(screen, 'availHeight', {get:()=>1040, configurable:true});
                    Reflect.defineProperty(screen, 'width',  {get:()=>1920, configurable:true});
                    Reflect.defineProperty(screen, 'height', {get:()=>1080, configurable:true});
                    Reflect.defineProperty(screen, 'colorDepth', {get:()=>24, configurable:true});
                    Reflect.defineProperty(screen, 'pixelDepth', {get:()=>24, configurable:true});
                } catch(_){}
                // === v8.13 EXTRA STEALTH (fingerprint hardening) ===
                try {
                    if (!navigator.connection || navigator.connection.effectiveType === undefined) {
                        const _conn = { effectiveType:"4g", rtt:50, downlink:10, saveData:false,
                                        onchange:null, addEventListener:()=>{}, removeEventListener:()=>{} };
                        Reflect.defineProperty(Navigator.prototype, "connection", { get: () => _conn, configurable:true });
                    }
                } catch(_){}
                try {
                    if (window.Notification) {
                        Reflect.defineProperty(Notification, "permission", { get: () => "default", configurable:true });
                    }
                    if (navigator.permissions && navigator.permissions.query) {
                        const _origQ = navigator.permissions.query.bind(navigator.permissions);
                        navigator.permissions.query = (p) => {
                            if (p && p.name === "notifications") return Promise.resolve({ state:"prompt", onchange:null });
                            return _origQ(p);
                        };
                    }
                } catch(_){}
                try {
                    const _exts = ["ANGLE_instanced_arrays","EXT_blend_minmax","EXT_color_buffer_half_float",
                        "EXT_disjoint_timer_query","EXT_float_blend","EXT_frag_depth","EXT_shader_texture_lod",
                        "EXT_texture_compression_bptc","EXT_texture_compression_rgtc","EXT_texture_filter_anisotropic",
                        "EXT_sRGB","KHR_parallel_shader_compile","OES_element_index_uint","OES_fbo_render_mipmap",
                        "OES_standard_derivatives","OES_texture_float","OES_texture_float_linear","OES_texture_half_float",
                        "OES_texture_half_float_linear","OES_vertex_array_object","WEBGL_color_buffer_float",
                        "WEBGL_compressed_texture_s3tc","WEBGL_compressed_texture_s3tc_srgb","WEBGL_debug_renderer_info",
                        "WEBGL_debug_shaders","WEBGL_depth_texture","WEBGL_draw_buffers","WEBGL_lose_context",
                        "WEBGL_multi_draw"];
                    const _wrapExt = (proto) => {
                        proto.getSupportedExtensions = function(){ return _exts.slice(); };
                    };
                    if (typeof WebGLRenderingContext !== "undefined")  _wrapExt(WebGLRenderingContext.prototype);
                    if (typeof WebGL2RenderingContext !== "undefined") _wrapExt(WebGL2RenderingContext.prototype);

                    const _wrapPrec = (proto) => {
                        proto.getShaderPrecisionFormat = function(_st, _pt){
                            return { rangeMin:127, rangeMax:127, precision:23 };
                        };
                    };
                    if (typeof WebGLRenderingContext !== "undefined")  _wrapPrec(WebGLRenderingContext.prototype);
                    if (typeof WebGL2RenderingContext !== "undefined") _wrapPrec(WebGL2RenderingContext.prototype);
                } catch(_){}
                try {
                    Reflect.defineProperty(window, "outerWidth",  { get: () => 1920, configurable:true });
                    Reflect.defineProperty(window, "outerHeight", { get: () => 1040, configurable:true });
                    Reflect.defineProperty(window, "screenX",     { get: () => 0,    configurable:true });
                    Reflect.defineProperty(window, "screenY",     { get: () => 0,    configurable:true });
                } catch(_){}
                // === END v8.13 EXTRA STEALTH ===
                window.__STEALTH_v813_OK = {
                    hwc: navigator.hardwareConcurrency,
                    dm: navigator.deviceMemory,
                    wd_in_proto: ('webdriver' in Navigator.prototype),
                    wd_val: navigator.webdriver,
                    tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
                    tzoff: new Date().getTimezoneOffset(),
                    cr: !!(window.chrome && window.chrome.runtime),
                    plat: navigator.platform,
                };
                try {
                    const c = document.createElement('canvas').getContext('webgl');
                    window.__STEALTH_v813_OK.webgl_v = c ? c.getParameter(37445) : 'no-ctx';
                    window.__STEALTH_v813_OK.webgl_r = c ? c.getParameter(37446) : 'no-ctx';
                } catch(_){}
            })();
            """
            _r796 = await page.evaluate("""(s) => {
                const tag = document.createElement('script');
                tag.textContent = s;
                document.documentElement.appendChild(tag);
                tag.remove();
                return window.__STEALTH_v813_OK || null;
            }""", _STEALTH_FORCE_v796)
            log(f"[stealth-v8.13] main-world inject result: {_r796}")
        except Exception as _se796:
            log(f"[stealth-v8.13] inject failed (non-fatal): {_se796}")
        # === END v7.96 STEALTH FORCE INJECTION ===


        # 页面加载后立即做 warmup（并发，让 reCAPTCHA 采集行为）
        warmup_task = asyncio.create_task(_human_warmup(page))

        t0 = await page.title()
        b0 = (await page.locator("body").inner_text())[:400]
        log(f"页面标题: {t0!r}")
        try:
            wd_val = await page.evaluate("() => navigator.webdriver")
            log(f"[detect] navigator.webdriver={wd_val}")
        except Exception as _wd_e:
            log(f"[detect] webdriver check err: {_wd_e}")
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
            url2 = page.url
            log(f"[step1-miss] url={url2[:120]} title={t2[:80]!r} body={b2.replace(chr(10),' ')[:200]!r}")
            # 截屏到磁盘以供肉眼诊断 (覆盖式, 不会涨盘)
            try:
                await page.screenshot(path="/tmp/replit_signup_miss.png", full_page=False)
                log(f"[step1-miss] screenshot → /tmp/replit_signup_miss.png")
            except Exception as _se:
                log(f"[step1-miss] screenshot failed: {_se}")
            # v8.17 — rich DOM dump so we can see WHY form did not render
            try:
                _dom = await page.evaluate("""
                    () => {
                      const inputs = Array.from(document.querySelectorAll('input')).map(i => ({
                        name: i.name||'', type: i.type||'', placeholder: i.placeholder||'', id: i.id||'',
                        visible: i.offsetWidth>0 && i.offsetHeight>0
                      }));
                      const buttons = Array.from(document.querySelectorAll('button')).slice(0,20).map(b => ({
                        text: (b.innerText||'').slice(0,60), dataCy: b.getAttribute('data-cy')||'', disabled: b.disabled
                      }));
                      const forms = Array.from(document.querySelectorAll('form')).map(f => ({
                        action: f.action||'', dataCy: f.getAttribute('data-cy')||'', innerHTMLLen: (f.innerHTML||'').length
                      }));
                      const iframes = Array.from(document.querySelectorAll('iframe')).map(f => ({
                        src: (f.src||'').slice(0,100), title: f.title||'',
                        w: f.getBoundingClientRect().width, h: f.getBoundingClientRect().height
                      }));
                      const root = document.querySelector('#root, #__next, main, [data-cy=signup-form]');
                      return {
                        htmlLen: document.documentElement.outerHTML.length,
                        bodyChildren: document.body ? document.body.children.length : 0,
                        inputs, buttons, forms, iframes,
                        rootTag: root ? root.tagName : null,
                        rootHTMLLen: root ? root.innerHTML.length : 0,
                        hasRecaptchaScript: !!document.querySelector('script[src*=recaptcha]'),
                        hasGrecaptcha: typeof window.grecaptcha,
                        hasGrecaptchaEnt: !!(window.grecaptcha && window.grecaptcha.enterprise),
                        readyState: document.readyState,
                        bodyText: (document.body ? document.body.innerText : '').slice(0,500)
                      };
                    }
                """)
                log(f"[step1-miss-dom] htmlLen={_dom.get('htmlLen')} bodyChildren={_dom.get('bodyChildren')} rootTag={_dom.get('rootTag')} rootHTMLLen={_dom.get('rootHTMLLen')} ready={_dom.get('readyState')}")
                log(f"[step1-miss-dom] grecaptcha={_dom.get('hasGrecaptcha')} ent={_dom.get('hasGrecaptchaEnt')} rcScript={_dom.get('hasRecaptchaScript')}")
                log(f"[step1-miss-dom] forms({len(_dom.get('forms',[]))})={_dom.get('forms')}")
                log(f"[step1-miss-dom] inputs({len(_dom.get('inputs',[]))})={_dom.get('inputs')}")
                log(f"[step1-miss-dom] buttons={_dom.get('buttons')}")
                log(f"[step1-miss-dom] iframes={_dom.get('iframes')}")
                log(f"[step1-miss-dom] bodyText={_dom.get('bodyText')!r}")
                # save full HTML to /tmp for full inspection
                try:
                    _html = await page.content()
                    with open("/tmp/replit_signup_miss.html","w",encoding="utf-8") as _hf:
                        _hf.write(_html)
                    log(f"[step1-miss-dom] full HTML saved /tmp/replit_signup_miss.html ({len(_html)}B)")
                except Exception as _he:
                    log(f"[step1-miss-dom] HTML save fail: {_he}")
            except Exception as _de:
                log(f"[step1-miss-dom] dump failed: {_de}")
            if is_replit_offline_block(t2, b2):
                # v8.60: replit edge sham-offline.html — port deeply blacklisted
                result["error"] = "replit_edge_blocked_offline_page"
            elif is_cf_blocked(t2, b2):
                result["error"] = "signup_cf_ip_banned"
            else:
                result["error"] = "signup_email_field_timeout"
            await browser.close(); return result

        result["phase"] = "fill_step1"
        err1 = await fill_step1(page)
        # v7.80: precheck-only 短路 — 用 _PRECHECK_RESULT 拼装最终结果, 跳过 captcha/submit
        if PRECHECK_ONLY:
            _dec = _PRECHECK_RESULT or ("taken" if err1 == "Email already in use on Replit" else "unknown")
            result.update({
                "ok": True, "phase": "precheck", "decision": _dec,
                "precheck_raw_err": err1 or "",
            })
            log(f"[precheck-only] decision={_dec} raw_err={err1!r}")
            await browser.close(); return result
        # captcha_token_invalid → 在当前页面直接触发音频挑战（bypass auto-token）
        if err1 == "captcha_token_invalid":
            # v7.69: 早判 reCAPTCHA Enterprise v3 score-only —
            # 无 bframe 即无视觉/音频挑战 UI（v3 纯打分模式），
            # 当前 IP 出口分被判 0 (code:2)；retry/reload 不会改善分数，
            # 直接返回结构化错误，避免 280s 内 outer-reload-retry 无谓循环耗尽 timeout。
            _v3_score_only = True
            try:
                for _f_chk in page.frames:
                    _u_chk = (_f_chk.url or "")
                    if "recaptcha" in _u_chk and "bframe" in _u_chk:
                        _v3_score_only = False; break
            except Exception:
                _v3_score_only = False
            # v7.97 — REVERT v7.92 误导分支. v7.78q/r 模型: code:2 是 *IP 一致性*
            # 问题 (token 生成 IP ≠ submit IP), code:1 是 *stealth 指纹* 问题.
            # v3 score-only 无 bframe 不代表 score 低, 仅说明站点配置纯 v3.
            # WARP / datacenter SOCKS / DIRECT 三种出口都能注册 (v7.78q e2e 实证),
            # 只要 broker / google_proxy_route / signup POST 全程同 IP. 不再早退
            # 抛 captcha_low_score; 让 audio-challenge fallback 与外层 IP 校验决定.
            if _v3_score_only:
                log("[retry] reCAPTCHA Enterprise v3 score-only widget (no bframe) → 跳过音频, 直接报 token_invalid")
                log("[retry] code:1 = reCAPTCHA Enterprise token rejected. 常见原因: (a) token-issuance IP 落在 datacenter ASN 被评低分 (本 VPS 45.205.27.69 AS8796 实证); (b) cf_clearance 缺失或 stealth 指纹被识破; (c) v3 score-only 站点不可能音频升级. 修复方向: 让 google-route attach 到清洁住宅/CDN-friendly SOCKS 池, 不要 SKIP 让 *.google 走 broker datacenter 出口.")
                result["error"] = "captcha_token_invalid"
                result["detail"] = (
                    "reCAPTCHA Enterprise v3 token rejected (code:1). "
                    "Likely token-issuance IP is on a datacenter ASN that reCAPTCHA scores low, "
                    "OR cf_clearance is missing. Fix: ensure google-route attaches a clean SOCKS pool "
                    "(do NOT skip when broker=direct — that lets *.google exit via VPS datacenter IP)."
                )
                try: await browser.close()
                except Exception: pass
                return result
            log("[retry] captcha_token_invalid → 尝试音频挑战 (Layer 2)...")
            try:
                # 当前页面仍在 signup，直接触发音频解算
                audio_token = await solve_recaptcha_audio(page, force_bframe=True)
                if audio_token:
                    log(f"[retry] ✅ 音频 token={len(audio_token)}chars → route-intercept 提交")
                    # Route intercept: 拦截 sign-up POST，替换 recaptchaToken 为音频 token
                    _tok_r = audio_token
                    async def _signup_intercept_cfx(route, request):
                        try:
                            import json as _jcfx
                            bd = _jcfx.loads(request.post_data or "{}")
                            bd["recaptchaToken"] = _tok_r
                            log(f"[route-cfx] recaptchaToken→audio({len(_tok_r)}chars)")
                            await route.continue_(post_data=_jcfx.dumps(bd))
                        except Exception as _re:
                            log(f"[route-cfx] err: {_re}")
                            await route.continue_()
                    _intercept_fired_cfx = [False]
                    _orig_intercept_cfx = _signup_intercept_cfx
                    async def _signup_intercept_cfx2(route, request):
                        _intercept_fired_cfx[0] = True
                        await _orig_intercept_cfx(route, request)
                    await page.route("**/api/v1/auth/sign-up**", _signup_intercept_cfx2)
                    await page.wait_for_timeout(300)
                    # 先解锁按钮（code:1 后 Replit 可能重置为 disabled）
                    try:
                        await page.evaluate("""
                            () => {
                                var btn = document.querySelector('[data-cy="signup-create-account"],button[type="submit"]');
                                if (btn) { btn.removeAttribute("disabled"); btn.removeAttribute("aria-disabled"); }
                            }
                        """)
                    except Exception: pass
                    await page.wait_for_timeout(300)
                    # 自然点击提交按钮，Replit JS 调用 execute()，route intercept 替换 token
                    _submitted2 = False
                    for sel_s in ['[data-cy="signup-create-account"]', 'button:has-text("Create Account")', 'button[type="submit"]']:
                        btn_s = page.locator(sel_s)
                        if await btn_s.count():
                            try:
                                await btn_s.first.click(timeout=5000)
                            except Exception:
                                await btn_s.first.click(force=True, timeout=3000)
                            _submitted2 = True
                            log(f"[retry] 音频route提交: {sel_s}")
                            break
                    if not _submitted2:
                        await page.keyboard.press("Enter")
                    await page.wait_for_timeout(3000)
                    # 如果 route intercept 未触发（Replit JS 未调用 API）→ 直接 fetch
                    if not _intercept_fired_cfx[0]:
                        log("[retry] route未触发 → 直接fetch提交")
                        try:
                            fr = await page.evaluate(
                                """async ([e,p,t]) => {
                                    var r=await fetch('/api/v1/auth/sign-up',{method:'POST',
                                        headers:{'Content-Type':'application/json','Accept':'application/json','X-Requested-With':'XMLHttpRequest'},
                                        credentials:'include',
                                        body:JSON.stringify({email:e,password:p,recaptchaToken:t})});
                                    return {s:r.status,b:(await r.text()).slice(0,300)};
                                }""",
                                [EMAIL, PASSWORD, _tok_r])
                            log(f"[retry-fetch] e={EMAIL!r} s={fr.get('s')} b={str(fr.get('b',''))[:120]}")
                        except Exception as _fe:
                            log(f"[retry-fetch] err: {_fe}")
                    await page.wait_for_timeout(1500)
                    try: await page.unroute("**/api/v1/auth/sign-up**", _signup_intercept_cfx2)
                    except Exception: pass
                    body_r = (await page.locator("body").inner_text())[:400]
                    if is_captcha_invalid(body_r):
                        log("[retry] 音频route token 仍无效")
                        err1 = "captcha_token_invalid"
                    elif is_rate_limited(body_r):
                        log("[retry] ⏳ IP 速率限制 → account_rate_limited")
                        err1 = "account_rate_limited"
                    elif is_integrity_error(body_r):
                        err1 = "integrity_check_failed_after_step1"
                    else:
                        # 检查是否成功（步骤2出现 or URL跳转）
                        cur_url_r = page.url
                        if "signup" not in cur_url_r.lower():
                            log(f"[retry] 音频提交后跳转: {cur_url_r[:60]}")
                            err1 = None  # 成功
                        else:
                            # 看 step2 字段
                            try:
                                await page.wait_for_selector('input[name="username"]', timeout=5000)
                                err1 = None  # step2 appeared
                                log("[retry] 音频提交 → step2 出现 ✅")
                            except Exception:
                                # 没有 step2 字段，且不是成功跳转 → 检查 body 是否有错误
                                body_assume = (await page.locator("body").inner_text())[:400]
                                if is_rate_limited(body_assume):
                                    log("[retry] else假设: rate limited")
                                    err1 = "account_rate_limited"
                                elif is_captcha_invalid(body_assume):
                                    err1 = "captcha_token_invalid"
                                else:
                                    err1 = None  # 假设 step1 成功，等 step2_wait
                else:
                    log("[retry] 音频挑战失败 → reload 重试表单...")
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=25000)
                        await page.wait_for_timeout(4000)
                        for sel_r in ['button:has-text("Email & password")', '[data-cy="email-signup"]']:
                            b_r = page.locator(sel_r)
                            if await b_r.count():
                                await b_r.first.click()
                                await page.wait_for_timeout(3000)
                                break
                        await page.wait_for_selector('input[name="email"], input[type="email"]', timeout=10000)
                        err1 = await fill_step1(page)
                        log(f"[retry] reload重试结果: {err1 or 'ok'}")
                    except Exception as e_reload:
                        log(f"[retry] reload失败: {e_reload}")
            except Exception as e_retry:
                log(f"[retry] 音频挑战异常: {e_retry}")
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
                log("[step2-miss] 验证邮件已发送 → 交还 TS orchestrator (Graph API + click-verify-link)")
                result["ok"] = True
                result["phase"] = "verify_email_sent"
                # v7.78m: in-page ground-truth IP probe
                await _finalize_exit_ip(result, page)
                # 不再 in-python _fetch_replit_verify_url / _complete_via_verify_url
                # TS Graph API (accounts.ts L900-940) 全权负责拉链接 + 点击, 避免 replit_register 污染
                try:
                    _sp = await _save_replit_state(page.context, USERNAME, EMAIL, {"path":"verify_email_sent","exit_ip_real":result.get("exit_ip_real",""),"exit_ip_source":result.get("exit_ip_source","")})
                    if _sp: result["state_path"] = _sp
                except Exception as _se:
                    log(f"[state] ⚠ {_se}")
                await browser.close(); return result
            log(f"[step2-miss][rl-debug] body_check len={len(body_check)} has_tq={'too quickly' in body_check.lower()} has_wait={'please wait a bit' in body_check.lower()}")
            if is_rate_limited(body_check):
                log("[step2-miss] ⏳ IP 速率限制 → account_rate_limited")
                result["error"] = "account_rate_limited"
                await browser.close(); return result
            if is_captcha_invalid(body_check):
                log("[step2-miss] captcha token invalid → captcha_token_invalid")
                result["error"] = "captcha_token_invalid"
                await browser.close(); return result
            result["error"] = "signup_username_field_missing"
            await browser.close(); return result

        result["phase"] = "fill_step2"
        err2 = await fill_step2(page)
        if err2:
            result["error"] = err2
            await browser.close(); return result

        if is_integrity_error((await page.locator("body").inner_text())[:300]):
            result["error"] = "integrity_check_failed_at_step2"
            await browser.close(); return result

        result["ok"] = True
        result["phase"] = "done"
        log("✅ 注册完成（验证邮件发送阶段）")
        # v7.78m: in-page ground-truth IP probe
        await _finalize_exit_ip(result, page)
        try:
            _sp = await _save_replit_state(page.context, USERNAME, EMAIL, {"path": "step2_done","exit_ip_real":result.get("exit_ip_real",""),"exit_ip_source":result.get("exit_ip_source","")})
            if _sp: result["state_path"] = _sp
        except Exception as _se:
            log(f"[state] ⚠ step2-done save_state failed: {_se}")
        await browser.close(); return result

    except Exception as e:
        log(f"attempt 异常: {e}")
        result["error"] = str(e)
    finally:
        try:
            await browser.close()
        except Exception:
            pass

    return result



def _probe_geoip_ok(proxy_cfg) -> bool:
    """用代理实际请求 ipify 一次，能拿到 IP 才允许 camoufox geoip=True (避免 InvalidIP 崩溃)"""
    try:
        import requests
        proxies = None
        if proxy_cfg and isinstance(proxy_cfg, dict):
            srv = proxy_cfg.get("server") or ""
            if srv:
                proxies = {"http": srv, "https": srv}
        r = requests.get("https://api.ipify.org/?format=json", proxies=proxies, timeout=8)
        ok = bool(r.ok and r.json().get("ip"))
        if not ok: log(f"[geoip-probe] no ip via proxy={proxy_cfg}")
        return ok
    except Exception as e:
        log(f"[geoip-probe] failed via proxy={proxy_cfg}: {e}")
        return False

async def get_exit_ip_camoufox(proxy_cfg) -> str:
    try:
        from camoufox.async_api import AsyncCamoufox
        async with AsyncCamoufox(headless=True, proxy=proxy_cfg or None, geoip=_probe_geoip_ok(proxy_cfg), os="windows") as browser:
            page = await browser.new_page()
            await page.goto("https://api.ipify.org/?format=json", timeout=65000)
            data = json.loads(await page.locator("body").inner_text())
            return data.get("ip", "")
    except Exception as e:
        log(f"get_exit_ip_camoufox err: {e}"); return ""


async def attempt_register_camoufox(proxy_cfg, exit_ip: str) -> dict:
    from camoufox.async_api import AsyncCamoufox
    result = {"ok": False, "phase": "init", "error": "", "exit_ip": exit_ip}
    async with AsyncCamoufox(headless=HEADLESS, proxy=proxy_cfg or None, geoip=_probe_geoip_ok(proxy_cfg), os="windows") as browser:
        page = await browser.new_page()
        try:
            await page.add_init_script(_CANVAS_WEBGL_NOISE_JS)
            log("[camoufox] Canvas 2D noise injected (WebGL/fingerprint natively handled)")
        except Exception as e:
            log(f"[camoufox] canvas noise err: {e}")
        try:
            result["phase"] = "navigate"
            try:
                await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=65000)
                await page.wait_for_timeout(_random.randint(2500, 4000))
                await page.goto("https://github.com", wait_until="domcontentloaded", timeout=65000)
                await page.wait_for_timeout(_random.randint(2000, 3500))
            except Exception as e:
                log(f"[pre-nav] err(ignored): {e}")
            log("[camoufox] opening replit.com/signup ...")
            # 监听主导航响应：Replit 直接 403 → 立即识别 CF 封 IP，避免硬等到 timeout
            _nav_status = {"code": 0}
            def _on_main_nav(resp):
                try:
                    if resp.url.startswith("https://replit.com/signup") and resp.request.resource_type == "document":
                        _nav_status["code"] = resp.status
                except Exception: pass
            page.on("response", _on_main_nav)
            try:
                await page.goto("https://replit.com/signup", wait_until="domcontentloaded", timeout=30000)
            except Exception as _ge:
                _code = _nav_status["code"]
                if _code in (403, 429, 503):
                    log("[camoufox] navigation status=" + str(_code) + " -> cf_ip_banned (fast-fail)")
                    result["error"] = "signup_cf_ip_banned"
                    return result
                raise
            _code = _nav_status["code"]
            if _code in (403, 429, 503):
                log("[camoufox] navigation status=" + str(_code) + " -> cf_ip_banned (fast-fail)")
                result["error"] = "signup_cf_ip_banned"
                return result
            warmup_task = asyncio.create_task(_human_warmup(page))
            t0 = await page.title()
            b0 = (await page.locator("body").inner_text())[:400]
            log(f"[camoufox] title: {t0!r}")
            try:
                wd_val = await page.evaluate("() => navigator.webdriver")
                log(f"[detect] navigator.webdriver={wd_val}")
            except Exception:
                pass
            if is_cf_blocked(t0, b0):
                result["error"] = "signup_cf_ip_banned"; warmup_task.cancel(); return result
            if is_integrity_error(b0):
                result["error"] = "integrity_check_failed_on_load"; warmup_task.cancel(); return result
            cf_err = await wait_cf(page)
            if cf_err:
                result["error"] = cf_err; warmup_task.cancel(); return result
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
                    log(f"[camoufox] clicked email btn: {sel}")
                    await page.wait_for_timeout(_random.randint(2800, 4200))
                    break
            else:
                log("[camoufox] email btn not found, form may be shown directly")
            result["phase"] = "step1_wait"
            try:
                await page.wait_for_selector(
                    'input[name="email"], input[type="email"], input[placeholder*="email" i]',
                    timeout=15000)
            except Exception:
                t2 = await page.title(); b2 = (await page.locator("body").inner_text())[:300]
                if is_replit_offline_block(t2, b2):
                    result["error"] = "replit_edge_blocked_offline_page"
                elif is_cf_blocked(t2, b2):
                    result["error"] = "signup_cf_ip_banned"
                elif is_spa_hydration_failed(t2, b2):
                    result["error"] = "signup_spa_not_hydrated"
                else:
                    result["error"] = "signup_email_field_timeout"
                return result
            result["phase"] = "fill_step1"
            err1 = await fill_step1(page)
            # v7.80: precheck-only 短路 (camoufox path 同样要支持)
            if PRECHECK_ONLY:
                _dec = _PRECHECK_RESULT or ("taken" if err1 == "Email already in use on Replit" else "unknown")
                result.update({
                    "ok": True, "phase": "precheck", "decision": _dec,
                    "precheck_raw_err": err1 or "",
                })
                log(f"[precheck-only][camoufox] decision={_dec} raw_err={err1!r}")
                return result
            if err1 == "captcha_token_invalid":
                log("[retry] captcha_token_invalid -> audio challenge...")
                try:
                    audio_token = await solve_recaptcha_audio(page)
                    if audio_token:
                        log(f"[retry] audio token → route-intercept resubmit ({len(audio_token)}chars)")
                        _tok_r2 = audio_token
                        async def _signup_intercept_pw(route, request):
                            try:
                                import json as _j2
                                bd = _j2.loads(request.post_data or "{}")
                                bd["recaptchaToken"] = _tok_r2
                                log(f"[route-pw] recaptchaToken→audio({len(_tok_r2)}chars)")
                                await route.continue_(post_data=_j2.dumps(bd))
                            except Exception as _re:
                                log(f"[route-pw] err: {_re}")
                                await route.continue_()
                        _intercept_fired_pw = [False]
                        _orig_pw = _signup_intercept_pw
                        async def _signup_intercept_pw2(route, request):
                            _intercept_fired_pw[0] = True
                            await _orig_pw(route, request)
                        await page.route("**/api/v1/auth/sign-up**", _signup_intercept_pw2)
                        await page.wait_for_timeout(300)
                        try:
                            await page.evaluate("""
                                () => {
                                    var btn = document.querySelector('[data-cy="signup-create-account"],button[type="submit"]');
                                    if (btn) { btn.removeAttribute("disabled"); btn.removeAttribute("aria-disabled"); }
                                }
                            """)
                        except Exception: pass
                        await page.wait_for_timeout(300)
                        _s2 = False
                        for ss in ['[data-cy="signup-create-account"]', 'button:has-text("Create Account")', 'button[type="submit"]']:
                            bs = page.locator(ss)
                            if await bs.count():
                                try: await bs.first.click(timeout=5000)
                                except: await bs.first.click(force=True, timeout=3000)
                                _s2 = True; break
                        if not _s2: await page.keyboard.press("Enter")
                        await page.wait_for_timeout(3000)
                        if not _intercept_fired_pw[0]:
                            log("[retry-pw] route未触发 → 直接fetch提交")
                            try:
                                fr2 = await page.evaluate(
                                    """async ([e,p,t]) => {
                                        var r=await fetch('/api/v1/auth/sign-up',{method:'POST',
                                            headers:{'Content-Type':'application/json','Accept':'application/json','X-Requested-With':'XMLHttpRequest'},
                                            credentials:'include',
                                            body:JSON.stringify({email:e,password:p,recaptchaToken:t})});
                                        return {s:r.status,b:(await r.text()).slice(0,300)};
                                    }""",
                                    [EMAIL, PASSWORD, _tok_r2])
                                log(f"[retry-fetch-pw] e={EMAIL!r} s={fr2.get('s')} b={str(fr2.get('b',''))[:120]}")
                            except Exception as _fe2:
                                log(f"[retry-fetch-pw] err: {_fe2}")
                        await page.wait_for_timeout(1500)
                        try: await page.unroute("**/api/v1/auth/sign-up**", _signup_intercept_pw2)
                        except Exception: pass
                        br = (await page.locator("body").inner_text())[:400]
                        if is_captcha_invalid(br): err1 = "captcha_token_invalid"
                        elif is_rate_limited(br): err1 = "account_rate_limited"
                        elif is_integrity_error(br): err1 = "integrity_check_failed_after_step1"
                        else:
                            if "signup" not in page.url.lower(): err1 = None
                            else:
                                try:
                                    await page.wait_for_selector('input[name="username"]', timeout=5000); err1 = None
                                except Exception:
                                    ba = (await page.locator("body").inner_text())[:400]
                                    if is_rate_limited(ba): err1 = "account_rate_limited"
                                    elif is_captcha_invalid(ba): err1 = "captcha_token_invalid"
                                    else: err1 = None
                    else:
                        log("[retry] audio failed -> reload...")
                        try:
                            await page.reload(wait_until="domcontentloaded", timeout=25000)
                            await page.wait_for_timeout(4000)
                            for sr in ['button:has-text("Email & password")', '[data-cy="email-signup"]']:
                                br2 = page.locator(sr)
                                if await br2.count(): await br2.first.click(); await page.wait_for_timeout(3000); break
                            await page.wait_for_selector('input[name="email"], input[type="email"]', timeout=10000)
                            err1 = await fill_step1(page)
                        except Exception as er: log(f"[retry] reload fail: {er}")
                except Exception as er: log(f"[retry] audio err: {er}")
            if err1: result["error"] = err1; return result
            if is_integrity_error((await page.locator("body").inner_text())[:300]):
                result["error"] = "integrity_check_failed_after_step1"; return result
            result["phase"] = "step2_wait"
            step2_appeared = False
            try:
                await page.wait_for_selector(
                    'input[name="username"], input[placeholder*="username" i], #username',
                    timeout=35000)
                log("[camoufox] step2 username field appeared"); step2_appeared = True
            except Exception: log("[camoufox] step2 username not appeared in 35s")
            if not step2_appeared:
                bck = (await page.locator("body").inner_text())[:500]
                url_ck = page.url
                log(f"[step2-miss] url={url_ck[:80]}")
                SUCCESS_HINTS = ("verify your email","check your email","we sent","sent you","verification email","confirm your email","sent an email")
                if any(h in bck.lower() for h in SUCCESS_HINTS):
                    log("[camoufox][step2-miss] 验证邮件已发送 → 交还 TS orchestrator (Graph API + click-verify-link)")
                    result["ok"] = True; result["phase"] = "verify_email_sent"
                    # v7.78m: in-page ground-truth IP probe
                    await _finalize_exit_ip(result, page)
                    # 不再 in-python _fetch / _complete, TS Graph API 全权负责
                    try:
                        _sp = await _save_replit_state(page.context, USERNAME, EMAIL, {"path":"verify_email_sent_camoufox","exit_ip_real":result.get("exit_ip_real",""),"exit_ip_source":result.get("exit_ip_source","")})
                        if _sp: result["state_path"] = _sp
                    except Exception as _se:
                        log(f"[camoufox][state] ⚠ {_se}")
                    return result
                if is_rate_limited(bck): result["error"] = "account_rate_limited"; return result
                if is_captcha_invalid(bck): result["error"] = "captcha_token_invalid"; return result
                result["error"] = "signup_username_field_missing"; return result
            result["phase"] = "fill_step2"
            err2 = await fill_step2(page)
            if err2: result["error"] = err2; return result
            if is_integrity_error((await page.locator("body").inner_text())[:300]):
                result["error"] = "integrity_check_failed_at_step2"; return result
            result["ok"] = True; result["phase"] = "done"
            log("[camoufox] registration complete (verify email phase)")
            # v7.78m: in-page ground-truth IP probe
            await _finalize_exit_ip(result, page)
            try:
                _sp = await _save_replit_state(page.context, USERNAME, EMAIL, {"path": "step2_done_camoufox","exit_ip_real":result.get("exit_ip_real",""),"exit_ip_source":result.get("exit_ip_source","")})
                if _sp: result["state_path"] = _sp
            except Exception as _se:
                log(f"[camoufox][state] ⚠ step2-done save_state failed: {_se}")
        except Exception as e:
            log(f"[camoufox] attempt err: {e}"); result["error"] = str(e)
        return result

async def get_exit_ip(pw_module, proxy_cfg) -> str:
    try:
        br = await pw_module.chromium.launch(headless=True, proxy=proxy_cfg,
                args=["--no-sandbox","--disable-dev-shm-usage"])
        try:
            ctx  = await br.new_context()
            page = await ctx.new_page()
            await page.goto("https://api.ipify.org/?format=json", timeout=65000)
            data = json.loads(await page.locator("body").inner_text())
            return data.get("ip", "")
        finally:
            await br.close()
    except Exception as e:
        log(f"get_exit_ip 异常: {e}"); return ""

# ── 主流程 ────────────────────────────────────────────────────────────────────
async def run() -> dict:
    final = {"ok": False, "phase": "init", "error": "", "exit_ip": ""}
    # ── 初始化 Xvfb/PulseAudio 环境 ──────────────────────────────────────────
    _setup_display_audio()

    proxy_cfg = {"server": PROXY} if PROXY else None

    # ── 探针：验证代理是否存活 ──────────────────────────────────────────────────
    # ─── CDP attach path (v7.33) ───────────────────────────────────────────
    # Headed broker chromium passes CF on this VPS; reuse it via remote-debugging.
    if USE_CDP:
        log(f"[CDP] use_cdp=True → attaching to broker chromium @ {CDP_WS}")
        from playwright.async_api import async_playwright as _apw_cdp
        try:
            # v7.86: WARP 在本 VPS 暴露为 socks5://127.0.0.1:40000 (warp-cli proxy mode),
            # 不是 TUN 接口, 故 --interface CloudflareWARP 永远失败, 走 except 把 VPS 直连 IP
            # 当作 exit_ip 写入 DB, 污染下游 replay/audit. 改用 broker chromium 同款 socks5.
            _ip_cdp = ""
            try:
                _ip_cdp = subprocess.check_output(
                    ["curl", "-s", "--max-time", "8",
                     "--socks5", "127.0.0.1:40000",
                     "https://api.ipify.org"],
                    text=True,
                ).strip()
                if _ip_cdp:
                    log(f"[CDP] WARP exit_ip={_ip_cdp}")
                else:
                    raise RuntimeError("empty WARP ipify response")
            except Exception as _werr:
                # WARP 探不到就放弃, 不写 VPS 直连 IP, 让 in-page fetch 兜底
                log(f"[CDP] WARP exit_ip probe failed ({_werr}); leaving exit_ip empty until in-page fetch")
                _ip_cdp = ""
            final["exit_ip"] = _ip_cdp
        except Exception as _e:
            log(f"[CDP] exit_ip probe err: {_e}")
        INTEGRITY_ERRORS_CDP = {
            "integrity_check_failed_on_load", "integrity_check_failed_after_click",
            "integrity_check_failed_after_step1", "integrity_check_failed_at_step2",
            "integrity_check_failed_after_step2",
        }
        async with _apw_cdp() as _pw_cdp:
            _shim = _CDPPwShim(_pw_cdp, CDP_WS)
            for _att in range(1, 2):  # v8.56: 砍掉 CDP 3 次内层重试
                log(f"[CDP] attempt {_att}/1 (no proxy, no stealth — broker chromium handles it)")
                # v7.52: 真把 SOCKS proxy 传给 new_context — 之前注释 "no proxy, broker handles it"
                # 是 bug, 因为 broker chromium 默认走 :40000 = WARP 104.28.x, replit reCAPTCHA
                # Enterprise 给 WARP IP 低分 → code:2. 改为传入 proxy_cfg 让 new_context 创建
                # 带独立 SOCKS 出口的 ctx (Playwright connect_over_cdp 后 new_context 仍支持
                # per-context proxy via Target.createBrowserContext + proxyServer 参数)
                _res = await attempt_register(_shim, proxy_cfg, None, final["exit_ip"])
                _res["exit_ip"] = final["exit_ip"]
                final = _res
                if _res["ok"]:
                    break
                if _res["error"] in ("signup_cf_js_challenge_timeout", "signup_cf_ip_banned"):
                    log(f"[CDP] CF 失败({_res['error']}) → bail out (broker也过不了，需要IP轮换)")
                    break
                if _res["error"] not in INTEGRITY_ERRORS_CDP:
                    break
                log(f"[CDP] integrity 失败({_res['error']}) → 重试")
                await asyncio.sleep(2)
        return final

    if proxy_cfg and not await ensure_tunnel(proxy_cfg):
        log("[run] 代理不可达，直接返回 tunnel_dead")
        return {"ok": False, "phase": "ensure_tunnel", "error": "tunnel_dead", "exit_ip": ""}
    # v7.32: camoufox primary (Firefox native fingerprint, passes integrity check)
    camoufox_available = False
    try:
        from camoufox.async_api import AsyncCamoufox as _ACF  # noqa
        camoufox_available = True
        log("✅ camoufox available (Firefox native fingerprint, passes integrity check)")
    except ImportError:
        log("⚠ camoufox not installed -> playwright+stealth fallback")

    stealth_fn = None
    pw_ctx_fn  = None

    if not camoufox_available:
        try:
            from playwright.async_api import async_playwright as _apw
            pw_ctx_fn = _apw
            log("✅ playwright active")
        except ImportError:
            try:
                from rebrowser_playwright.async_api import async_playwright as _rapw
                pw_ctx_fn = _rapw
                log("⚠ playwright not installed -> rebrowser fallback")
            except ImportError:
                final["error"] = "camoufox/playwright/rebrowser none available"
                return final
        try:
            from playwright_stealth import Stealth
            stealth_fn = Stealth(chrome_runtime=True, webgl_vendor=True).apply_stealth_async
            log("playwright-stealth active (chrome_runtime=True, webgl_vendor=True)")
        except ImportError:
            stealth_fn = None

    # exit IP
    try:
        if camoufox_available:
            ip = await get_exit_ip_camoufox(proxy_cfg)
        else:
            async with pw_ctx_fn() as pw:
                ip = await get_exit_ip(pw, proxy_cfg)
        if ip:
            final["exit_ip"] = ip
            log(f"exit IP: {ip}")
    except Exception as e:
        log(f"exit IP error: {e}")
    INTEGRITY_ERRORS = {
        "integrity_check_failed_on_load", "integrity_check_failed_after_click",
        "integrity_check_failed_after_step1", "integrity_check_failed_at_step2",
        "integrity_check_failed_after_step2",
    }

    for attempt in range(1, 2):  # v8.56: 砍掉浏览器 3 次内层重试
        log(f"browser attempt {attempt}/1")
        if camoufox_available:
            res = await attempt_register_camoufox(proxy_cfg, final["exit_ip"])
        else:
            async with pw_ctx_fn() as pw:
                res = await attempt_register(pw, proxy_cfg, stealth_fn, final["exit_ip"])
        # v7.82: 不再用 preflight final["exit_ip"] 覆盖 attempt_register 已经
        # 用 _finalize_exit_ip 写入的 in-page ground truth IP. preflight 来自
        # python 进程 curl ipify 走 OS 默认路由 (45.205.x.x VPS 公网), 不是
        # chromium 实际走的 socks5/WARP 出口. 仅在 attempt_register 没填
        # exit_ip 时才回退到 preflight.
        res["exit_ip_preflight"] = final["exit_ip"]
        if not res.get("exit_ip"):
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
