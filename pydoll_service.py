#!/usr/bin/env python3
"""
Pydoll CF WAF Bypass HTTP Service  v2.0
端口: 8766
改进 (pydoll-antibot-bypasser + capsolver skills):
  - 全套 28 个 anti-detection Chrome flags（对齐 cdp-broker.ts）
  - 启动时 enable_auto_solve_cloudflare_captcha(time_to_wait_captcha=30) 持久化监听
  - stealth JS 注入（消除 webdriver/cdc 全局变量 + chrome.runtime 伪装）
  - CapSolver Turnstile API 兜底（CAPSOLVER_API_KEY 环境变量）
  - DoH + QUIC 禁用 + locale en_US + WebGL angle/swiftshader
  - 共享异步事件循环（单进程内复用，减少 Chrome 启动开销）
"""
import asyncio
import json
import os
import time
import random
import traceback
import threading
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions

# ── Chrome & Display 自动检测 ─────────────────────────────────────────────
_CHROME_CANDIDATES = [
    "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
    "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
    "/data/cache/ms-playwright/chromium-1169/chrome-linux64/chrome",
]
CHROME_PATH = next((p for p in _CHROME_CANDIDATES if os.path.exists(p)), _CHROME_CANDIDATES[0])

if not os.environ.get("DISPLAY"):
    import glob as _glob
    for _lock in _glob.glob("/tmp/.X*-lock"):
        _num = _lock.replace("/tmp/.X", "").replace("-lock", "")
        if _num.isdigit():
            os.environ["DISPLAY"] = f":{_num}"
            break
    else:
        os.environ.setdefault("DISPLAY", ":99")

PORT = int(os.environ.get("PYDOLL_PORT", 8766))
CAPSOLVER_KEY = os.environ.get("CAPSOLVER_API_KEY", "")


# ── Xvfb 探针 ────────────────────────────────────────────────────────────
def _probe_x(n) -> bool:
    try:
        os.stat(f"/tmp/.X11-unix/X{n}")
        return True
    except OSError:
        return False


def _best_display():
    raw = os.environ.get("DISPLAY", "").lstrip(":").split(".")[0]
    for n in ([raw] if raw.isdigit() else []) + ["99", "100", "77"]:
        if _probe_x(n):
            return f":{n}"
    return None


DISPLAY = _best_display()
USE_HEADED = bool(DISPLAY)

# ── Stealth JS — 消除 webdriver 痕迹 + chrome.runtime 伪装 ──────────────
_STEALTH_JS = r"""
(function(){
  try { delete Navigator.prototype.webdriver; } catch(_) {}
  try { delete navigator.__proto__.webdriver; } catch(_) {}
  try { Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true }); } catch(_) {}
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; } catch(_) {}
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; } catch(_) {}
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol; } catch(_) {}
  try { Object.defineProperty(Navigator.prototype, 'languages', { get: () => ['en-US','en'], configurable: true }); } catch(_) {}
  try { Object.defineProperty(Navigator.prototype, 'language',  { get: () => 'en-US', configurable: true }); } catch(_) {}
  try { Object.defineProperty(Navigator.prototype, 'platform', { get: () => 'Linux x86_64', configurable: true }); } catch(_) {}
  try { Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', { get: () => 8, configurable: true }); } catch(_) {}
  try { Object.defineProperty(Navigator.prototype, 'deviceMemory', { get: () => 8, configurable: true }); } catch(_) {}
  try { Object.defineProperty(Navigator.prototype, 'maxTouchPoints', { get: () => 0, configurable: true }); } catch(_) {}
  try {
    if (!window.chrome) window.chrome = {};
    var _mk = function() { return { addListener: function(){}, removeListener: function(){}, hasListener: function(){ return false; }, hasListeners: function(){ return false; } }; };
    window.chrome.runtime = {
      id: undefined, lastError: null,
      onConnect: _mk(), onMessage: _mk(), onInstalled: _mk(), onStartup: _mk(),
      getManifest: function(){ return undefined; },
      getPlatformInfo: function(cb){ var i={os:'linux',arch:'x86-64',nacl_arch:'x86-64'}; if(cb)cb(i); return Promise.resolve(i); }
    };
    var _t0 = Date.now()/1000 - (Math.random()*0.3+0.1);
    window.chrome.loadTimes = function(){ return { requestTime:_t0, startLoadTime:_t0, commitLoadTime:_t0+0.05, finishDocumentLoadTime:_t0+0.4, finishLoadTime:_t0+0.5, firstPaintTime:_t0+0.15, firstPaintAfterLoadTime:0, navigationType:'Other', wasFetchedViaSpdy:true, wasNpnNegotiated:true, npnNegotiatedProtocol:'h2', wasAlternateProtocolAvailable:false, connectionInfo:'h2' }; };
    window.chrome.csi = function(){ return { startE:Date.now(), onloadT:Date.now(), pageT:Math.random()*800+200, tran:15 }; };
  } catch(_) {}
  try {
    if (typeof WebGLRenderingContext !== 'undefined') {
      var _gp = WebGLRenderingContext.prototype.getParameter;
      WebGLRenderingContext.prototype.getParameter = function(p){
        if(p===37445) return 'Google Inc. (Intel)';
        if(p===37446) return 'ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)';
        return _gp.apply(this,arguments);
      };
    }
  } catch(_) {}
})();
"""


# ── CapSolver Turnstile fallback ──────────────────────────────────────────
def _capsolver_turnstile(page_url: str, site_key: str = "0x4AAAAAAABuyoRxYB4wU7TU"):
    """Call CapSolver API to solve Cloudflare Turnstile. Returns token or None."""
    if not CAPSOLVER_KEY or not _HAS_REQUESTS:
        return None
    try:
        r = _requests.post("https://api.capsolver.com/createTask", json={
            "clientKey": CAPSOLVER_KEY,
            "task": {
                "type": "AntiCloudflareTask",
                "websiteURL": page_url,
                "websiteKey": site_key,
                "proxy": "",
            }
        }, timeout=10)
        data = r.json()
        task_id = data.get("taskId")
        if not task_id:
            print(f"[capsolver] createTask failed: {data}", flush=True)
            return None
        for _ in range(60):
            time.sleep(1)
            gr = _requests.post("https://api.capsolver.com/getTaskResult", json={
                "clientKey": CAPSOLVER_KEY,
                "taskId": task_id,
            }, timeout=10)
            gd = gr.json()
            if gd.get("status") == "ready":
                token = gd.get("solution", {}).get("token")
                print(f"[capsolver] token OK len={len(token or '')}", flush=True)
                return token
        print("[capsolver] timeout 60s", flush=True)
        return None
    except Exception as e:
        print(f"[capsolver] error: {e}", flush=True)
        return None


# ── Chrome options builder v2 — full anti-detection flag set ──────────────
def get_options(headless=None, proxy=None) -> ChromiumOptions:
    options = ChromiumOptions()
    _headed = USE_HEADED if headless is None else (not headless)
    options.headless = not _headed
    options.binary_location = CHROME_PATH
    options.start_timeout = 40

    # Anti-detection core
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-features=IsolateOrigins,site-per-process,AutomationControlled,Translate")
    # NOTE: pydoll auto-adds --no-first-run and --no-default-browser-check — do NOT add again
    options.add_argument("--mute-audio")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions-except")
    options.add_argument("--disable-component-extensions-with-background-pages")
    # Locale — match en-US machine fingerprint
    options.add_argument("--lang=en-US")
    options.add_argument("--accept-lang=en-US,en;q=0.9")
    # Keychain / password store (Linux)
    options.add_argument("--password-store=basic")
    options.add_argument("--use-mock-keychain")
    # Fake media devices (getUserMedia + enumerateDevices consistency)
    options.add_argument("--use-fake-ui-for-media-stream")
    options.add_argument("--use-fake-device-for-media-stream")
    # Network — DoH + DNS anti-pollution
    options.add_argument("--enable-features=AsyncDns,DnsOverHttps")
    options.add_argument("--dns-over-https-mode=secure")
    options.add_argument("--dns-over-https-templates=https://1.1.1.1/dns-query,https://dns.google/dns-query")
    # Background / breakpad / telemetry noise
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-breakpad")
    options.add_argument("--disable-sync")
    options.add_argument("--metrics-recording-only")
    options.add_argument("--disable-features=GlobalMediaControls,MediaRouter,DialMediaRouteProvider,OptimizationHints,InterestFeedContentSuggestions")
    # Window
    options.add_argument("--window-size=1920,1080")
    if _headed:
        options.add_argument("--window-position=0,0")
        options.add_argument("--start-maximized")
        options.add_argument("--use-gl=angle")
        options.add_argument("--use-angle=swiftshader")
        options.add_argument("--enable-webgl")
    else:
        options.add_argument("--disable-gpu")
    # Proxy
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")
        options.add_argument("--disable-quic")
        options.add_argument("--proxy-resolves-dns-locally")
    # Browser preferences (fake profile age)
    fake_time = int(time.time()) - random.randint(7, 21) * 86400
    options.browser_preferences = {
        "profile": {
            "last_engagement_time": fake_time,
            "exit_type": "Normal",
            "exited_cleanly": True,
            "default_content_setting_values": {"notifications": 2, "geolocation": 2},
            "password_manager_enabled": False,
        },
        "session": {"restore_on_startup": 1},
        "intl": {"accept_languages": "en-US,en"},
    }
    options.webrtc_leak_protection = bool(proxy)
    return options


# ── Shared async event loop ───────────────────────────────────────────────
_loop = None
_loop_lock = threading.Lock()


def _get_loop():
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            t = threading.Thread(target=_loop.run_forever, daemon=True)
            t.start()
    return _loop


def run_async(coro, timeout=180):
    """Run coroutine in shared background loop, block until done."""
    loop = _get_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


# ── Core bypass logic v2 ──────────────────────────────────────────────────
async def do_bypass(url: str, headless=None, proxy=None,
                    screenshot: bool = False, timeout: int = 30) -> dict:
    """
    v2: persistent enable_auto_solve_cloudflare_captcha(30s) + stealth JS.
    CapSolver fallback when token not found.
    """
    options = get_options(headless=headless, proxy=proxy)
    start = time.time()
    async with Chrome(options=options) as browser:
        tab = await browser.start()

        # Inject stealth JS immediately
        try:
            await tab.execute_script(_STEALTH_JS)
        except Exception as e:
            print(f"[bypass] stealth_js warn: {e}", flush=True)

        # Persistent auto-solve listener (fires on every page load event)
        await tab.enable_auto_solve_cloudflare_captcha(time_to_wait_captcha=30)
        print(f"[bypass] auto_solve enabled (30s). navigating to {url}", flush=True)

        await tab.go_to(url)

        # Wait up to 45s for CF challenge to clear
        for _w in range(45):
            await asyncio.sleep(1)
            try:
                title = (await tab.title).lower()
                if "just a moment" not in title and "cloudflare" not in title:
                    break
            except Exception:
                break
        else:
            print("[bypass] CF title still present after 45s", flush=True)

        await asyncio.sleep(2)

        # Read Turnstile token length
        token_len = 0
        try:
            res = await tab.execute_script(
                "(document.querySelector('[name=\"cf-turnstile-response\"]')||{value:''}).value.length",
                return_by_value=True
            )
            if isinstance(res, dict):
                inner = res.get("result", res)
                token_len = int(inner.get("value", 0) if isinstance(inner, dict) else 0)
        except Exception:
            pass

        result = {
            "success": True,
            "title": await tab.title,
            "url": await tab.current_url,
            "html_length": len(await tab.page_source),
            "token_len": token_len,
            "elapsed": round(time.time() - start, 2),
            "method": "pydoll_auto_solve",
            "headed": USE_HEADED,
        }

        # CapSolver fallback if pydoll bypass could not get token
        if token_len == 0 and CAPSOLVER_KEY:
            print("[bypass] token=0, trying CapSolver fallback...", flush=True)
            cs_token = await asyncio.to_thread(_capsolver_turnstile, url)
            if cs_token:
                inject = f"var el=document.querySelector('[name=\"cf-turnstile-response\"]'); if(el) el.value={json.dumps(cs_token)};"
                await tab.execute_script(inject)
                result["capsolver_token_len"] = len(cs_token)
                result["method"] = "capsolver_fallback"
                result["token_len"] = len(cs_token)

        if screenshot:
            ss_path = f"/tmp/pydoll_ss_{int(time.time())}.png"
            await tab.take_screenshot(ss_path)
            result["screenshot"] = ss_path

        try:
            await tab.disable_auto_solve_cloudflare_captcha()
        except Exception:
            pass
        return result


async def do_scrape(url: str, selectors: dict, headless=None, proxy=None) -> dict:
    options = get_options(headless=headless, proxy=proxy)
    start = time.time()
    async with Chrome(options=options) as browser:
        tab = await browser.start()
        try:
            await tab.execute_script(_STEALTH_JS)
        except Exception:
            pass
        await tab.enable_auto_solve_cloudflare_captcha(time_to_wait_captcha=30)
        await tab.go_to(url)
        await asyncio.sleep(random.uniform(2, 3.5))
        try:
            await tab.disable_auto_solve_cloudflare_captcha()
        except Exception:
            pass

        data = {}
        for key, selector in selectors.items():
            try:
                el = await tab.query(selector, raise_exc=False)
                if el:
                    data[key] = await el.get_attribute("content") if "meta" in selector else await el.text
                else:
                    data[key] = None
            except Exception:
                data[key] = None
        html = await tab.page_source
        return {
            "success": True,
            "title": await tab.title,
            "url": await tab.current_url,
            "html_length": len(html),
            "html": html[:5000],
            "data": data,
            "elapsed": round(time.time() - start, 2),
        }


async def do_register(email: str, password: str, username: str,
                      proxy=None, headless=None) -> dict:
    """v2: stealth JS + persistent auto_solve(30s) + CapSolver fallback."""
    options = get_options(headless=headless, proxy=proxy)
    start = time.time()
    result = {"status": "init", "email": email, "cookies": {}, "url": "", "error": None}

    async def _safe_click(el):
        try:
            await el.scroll_into_view()
            await asyncio.sleep(0.3)
        except Exception:
            pass
        try:
            await el.click()
        except Exception:
            await el.click_using_js()

    async def _type_into(tab, selector: str, text: str):
        el = None
        for sel in [s.strip() for s in selector.split(",")]:
            el = await tab.query(sel, raise_exc=False)
            if el:
                break
        if el is None:
            raise RuntimeError(f"selector not found: {selector}")
        await _safe_click(el)
        await asyncio.sleep(0.2)
        for ch in text:
            await el.type(ch)
            await asyncio.sleep(random.uniform(0.03, 0.08))

    async def _wait_past_cf(tab, timeout_s: int = 30) -> str:
        cf_titles = {"attention required", "just a moment", "cloudflare"}
        for _ in range(timeout_s * 2):
            title = (await tab.title).lower()
            if not any(t in title for t in cf_titles):
                return await tab.title
            await asyncio.sleep(0.5)
        return await tab.title

    async with Chrome(options=options) as browser:
        tab = await browser.start()
        try:
            try:
                await tab.execute_script(_STEALTH_JS)
            except Exception:
                pass

            await tab.enable_auto_solve_cloudflare_captcha(time_to_wait_captcha=30)
            print("[pydoll-reg] auto_solve enabled (30s), navigating to signup", flush=True)

            await tab.go_to("https://replit.com/signup")
            title_after = await _wait_past_cf(tab, timeout_s=30)
            print(f"[pydoll-reg] title after CF: {title_after!r}", flush=True)

            if "cloudflare" in title_after.lower() or "attention" in title_after.lower():
                if CAPSOLVER_KEY:
                    print("[pydoll-reg] CF persists, trying CapSolver...", flush=True)
                    cs_token = await asyncio.to_thread(_capsolver_turnstile, "https://replit.com/signup")
                    if cs_token:
                        inject = f"var el=document.querySelector('[name=\"cf-turnstile-response\"]'); if(el) el.value={json.dumps(cs_token)};"
                        await tab.execute_script(inject)
                        await asyncio.sleep(2)
                        title_after = await _wait_past_cf(tab, timeout_s=10)

            if "cloudflare" in title_after.lower() or "attention" in title_after.lower():
                result["status"] = "cf_not_bypassed"
                result["error"] = f"CF not resolved after 30s+capsolver: {title_after!r}"
                try:
                    await tab.take_screenshot(f"/tmp/pydoll_cf_fail_{int(time.time())}.png")
                except Exception:
                    pass
                return result

            await asyncio.sleep(1.5)

            email_btn = None
            for txt in ["Email & password", "Email", "Continue with email"]:
                email_btn = await tab.query(f'button:has-text("{txt}")', raise_exc=False)
                if email_btn:
                    print(f"[pydoll-reg] found email btn: {txt!r}", flush=True)
                    break
            if email_btn:
                await _safe_click(email_btn)
                await asyncio.sleep(1.5)

            try:
                await _type_into(tab, 'input[name="email"], input[type="email"]', email)
            except Exception as e:
                print(f"[pydoll-reg] email err: {e}", flush=True)

            try:
                await _type_into(tab, 'input[name="password"], input[type="password"]', password)
            except Exception as e:
                print(f"[pydoll-reg] password err: {e}", flush=True)

            try:
                un_el = await tab.query('input[name="username"], input[placeholder*="sername" i]', raise_exc=False)
                if un_el:
                    await _safe_click(un_el)
                    for ch in username:
                        await un_el.type(ch)
                        await asyncio.sleep(random.uniform(0.03, 0.07))
            except Exception as e:
                print(f"[pydoll-reg] username err: {e}", flush=True)

            await asyncio.sleep(random.uniform(0.5, 1))

            submitted = False
            for sel in ['button[type="submit"]', 'button:has-text("Create Account")',
                        'button:has-text("Sign up")', 'button:has-text("Continue")']:
                btn = await tab.query(sel, raise_exc=False)
                if btn:
                    await _safe_click(btn)
                    submitted = True
                    break
            if not submitted:
                await tab.execute_script("var f=document.querySelector('form'); if(f) f.submit();")

            await asyncio.sleep(6)

            url_now = await tab.current_url
            page_title = await tab.title
            try:
                body_text = await tab.execute_script(
                    "return document.body ? document.body.innerText.slice(0,500) : ''") or ""
            except Exception:
                body_text = ""

            print(f"[pydoll-reg] final url={url_now} title={page_title!r}", flush=True)

            try:
                ss_path = f"/tmp/pydoll_reg_{int(time.time())}.png"
                await tab.take_screenshot(ss_path)
                result["screenshot"] = ss_path
            except Exception:
                pass

            try:
                raw_cookies = await tab.get_cookies()
                cookie_dict = {c["name"]: c["value"] for c in (raw_cookies or [])
                               if c.get("domain", "").endswith("replit.com")}
                result["cookies"] = cookie_dict
            except Exception as e:
                print(f"[pydoll-reg] cookie err: {e}", flush=True)

            result["url"] = url_now
            result["elapsed"] = round(time.time() - start, 2)

            body_lower = body_text.lower()
            if any(x in url_now for x in ["/home", "/repls", "/@", "/dashboard", "/account"]):
                result["status"] = "success"
            elif "verify" in url_now or "confirm" in url_now:
                result["status"] = "verify_email"
            elif "integrity" in body_lower or "failed to evaluate" in body_lower:
                result["status"] = "bot_detected"
                result["error"] = "Browser integrity check failed"
            elif "already" in body_lower or "in use" in body_lower:
                result["status"] = "email_taken"
            elif "verify" in body_lower or "check your email" in body_lower:
                result["status"] = "verify_email"
            elif "captcha" in body_lower:
                result["status"] = "captcha_required"
                result["error"] = "CAPTCHA still present"
            elif "just a moment" in page_title.lower() or "cloudflare" in page_title.lower():
                result["status"] = "cf_not_bypassed"
                result["error"] = f"CF still showing: {page_title!r}"
            else:
                result["status"] = "unknown"
                result["error"] = f"at {url_now} body={body_text[:120]!r}"

            try:
                await tab.disable_auto_solve_cloudflare_captcha()
            except Exception:
                pass

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            result["trace"] = traceback.format_exc()
            print(f"[pydoll-reg] exception: {e}", flush=True)
            try:
                await tab.take_screenshot(f"/tmp/pydoll_reg_err_{int(time.time())}.png")
            except Exception:
                pass

    result["success"] = result["status"] in ("success", "verify_email")
    return result


# ── HTTP Handler ──────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {fmt % args}")

    def send_json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self.send_json(200, {
                "ok": True,
                "service": "pydoll-bypass",
                "version": "2.0",
                "chrome": CHROME_PATH,
                "display": DISPLAY,
                "headed": USE_HEADED,
                "capsolver": bool(CAPSOLVER_KEY),
            })
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        body = self.read_body()

        if parsed.path == "/bypass":
            url = body.get("url")
            if not url:
                return self.send_json(400, {"error": "url is required"})
            try:
                result = run_async(do_bypass(
                    url,
                    headless=body.get("headless"),
                    proxy=body.get("proxy"),
                    screenshot=body.get("screenshot", False),
                    timeout=body.get("timeout", 30),
                ))
                self.send_json(200, result)
            except Exception as e:
                self.send_json(500, {"success": False, "error": str(e),
                                     "trace": traceback.format_exc()})

        elif parsed.path == "/scrape":
            url = body.get("url")
            if not url:
                return self.send_json(400, {"error": "url is required"})
            try:
                result = run_async(do_scrape(
                    url,
                    selectors=body.get("selectors", {"title": "h1"}),
                    headless=body.get("headless"),
                    proxy=body.get("proxy"),
                ))
                self.send_json(200, result)
            except Exception as e:
                self.send_json(500, {"success": False, "error": str(e),
                                     "trace": traceback.format_exc()})

        elif parsed.path == "/register":
            email = body.get("email")
            password = body.get("password")
            username = body.get("username")
            if not all([email, password, username]):
                return self.send_json(400, {"error": "email, password, username required"})
            try:
                result = run_async(do_register(
                    email, password, username,
                    proxy=body.get("proxy"),
                    headless=body.get("headless"),
                ))
                self.send_json(200, result)
            except Exception as e:
                self.send_json(500, {"success": False, "error": str(e),
                                     "trace": traceback.format_exc()})

        else:
            self.send_json(404, {
                "error": "Not found",
                "endpoints": ["GET /healthz", "POST /bypass", "POST /scrape", "POST /register"],
            })


def main():
    import subprocess as _sp
    _sp.run(["fuser", "-k", f"{PORT}/tcp"], capture_output=True)
    time.sleep(0.5)
    socketserver.TCPServer.allow_reuse_address = True
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[pydoll-service v2.0] DISPLAY={DISPLAY} headed={USE_HEADED}")
    print(f"[pydoll-service v2.0] chrome={CHROME_PATH}")
    print(f"[pydoll-service v2.0] capsolver={'OK' if CAPSOLVER_KEY else 'off (set CAPSOLVER_API_KEY)'}")
    print(f"[pydoll-service v2.0] endpoints: GET /healthz  POST /bypass  POST /scrape  POST /register")
    print(f"[pydoll-service v2.0] listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
