"""
core/registrar.py -- AirForce API auto-registration (pure open-source, browser-based)

Proven flow (2026-05-02):
  1. pydoll Chrome via residential proxy (round-robin port rotation)
  2. go_to signup page, wait 10s for React + Turnstile shadow DOM
  3. Fill 4 fields: username, email, password, confirmPassword
  4. Shadow bypass: outer shadow -> CF iframe -> body -> inner shadow -> span.cb-i -> click
  5. Wait for Turnstile token in [name="cf-turnstile-response"]
  6. Click "Create Free Account" submit button
  7. Wait for SPA redirect to /dashboard/
  8. Fetch /api/me in-browser (same cookies/session) -> extract api_key

Key insight: CF Turnstile tokens are session-bound (IP + browser cookies).
curl POST with extracted token fails. Browser must submit the form directly.
Port rotation avoids CF rate-limiting any single residential IP.
"""
import asyncio
import threading
import json
import os
import random
import string
import time
from dataclasses import dataclass
from typing import Optional

from .turnstile import RESIDENTIAL_PORTS, DC_PORTS_SKIP

CHROME_PATH = "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome"
CF_DOM      = "challenges.cloudflare.com"

_port_idx = 0            # module-level round-robin counter
_port_idx_lock = threading.Lock()  # parallel batch safety
_port_blacklist: dict = {}   # port -> epoch-time when cooldown expires (30 min)
# Pre-populate permanent skip for DC/cloud IPs (they will never pass Cloudflare)
_PERMANENT = 365 * 86400  # 1 year TTL = permanent
import time as _t0
for _dc in DC_PORTS_SKIP:
    _port_blacklist[_dc] = _t0.time() + _PERMANENT
_COOLDOWN = 1800             # seconds before a rate-limited port is retried


def _blacklist_port(port: int) -> None:
    """Mark port as rate-limited for COOLDOWN seconds."""
    _port_blacklist[port] = time.time() + _COOLDOWN
    print(f"[registrar] port {port} blacklisted for {_COOLDOWN//60}min", flush=True)


_ps_refresh_lock = threading.Lock()
_ps_last_refresh = 0.0
_PS_RANGE = range(10870, 10900)
_PS_REFRESH_COOLDOWN = 7200  # don't re-refresh within 2h

def _check_ps_ports_alive() -> list:
    """Quick check which ps-* ports (10870-10899) currently respond."""
    import subprocess as _sp
    alive = []
    ps_ports = [p for p in RESIDENTIAL_PORTS if p in _PS_RANGE]
    if not ps_ports:
        return []
    def _chk(p):
        try:
            r = _sp.run(['curl','-s','--max-time','4','--socks5',f'127.0.0.1:{p}',
                         'https://api.ipify.org'], capture_output=True, text=True, timeout=6)
            return p if (r.stdout.strip() and '.' in r.stdout.strip()) else None
        except:
            return None
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=10) as ex:
        for result in ex.map(_chk, ps_ports):
            if result:
                alive.append(result)
    return alive

def _maybe_refresh_ps():
    """Trigger proxyscrape manager if ps-* pool is empty and cooldown elapsed."""
    global _ps_last_refresh
    import threading as _thr, subprocess as _sp, time as _ti
    with _ps_refresh_lock:
        if _ti.time() - _ps_last_refresh < _PS_REFRESH_COOLDOWN:
            return
        _ps_last_refresh = _ti.time()
    def _bg():
        print("[registrar] ps-* pool depleted → triggering proxyscrape refresh", flush=True)
        try:
            r = _sp.run(['python3', '/root/AirForce/core/proxyscrape_manager.py'],
                       capture_output=True, text=True, timeout=300)
            print(f"[registrar] proxyscrape refresh done: {r.stdout[-300:]}", flush=True)
        except Exception as e:
            print(f"[registrar] proxyscrape refresh failed: {e}", flush=True)
    _thr.Thread(target=_bg, daemon=True).start()

def _next_port() -> int:
    """Round-robin over RESIDENTIAL_PORTS, skipping currently blacklisted ones.
    Auto-triggers proxyscrape refresh when ps-* slots are depleted."""
    global _port_idx
    now = time.time()
    # Expire old blacklist entries
    expired = [p for p, t in _port_blacklist.items() if t < now]
    for p in expired:
        del _port_blacklist[p]
        print(f"[registrar] port {p} blacklist expired", flush=True)

    available = [p for p in RESIDENTIAL_PORTS if p not in _port_blacklist]
    if not available:
        available = RESIDENTIAL_PORTS  # all banned → use all anyway
        print("[registrar] WARNING all ports blacklisted, ignoring cooldowns", flush=True)

    # Check if ps-* slots in RESIDENTIAL_PORTS are alive; if none, trigger refresh
    ps_in_pool = [p for p in available if p in _PS_RANGE]
    if not ps_in_pool:
        # No ps-* in pool at all (not even listed) - trigger refresh
        _maybe_refresh_ps()
    elif len(ps_in_pool) > 0:
        alive = _check_ps_ports_alive()
        if not alive:
            print("[registrar] all ps-* ports dead, triggering refresh", flush=True)
            _maybe_refresh_ps()

    with _port_idx_lock:
        port = available[_port_idx % len(available)]
        _port_idx += 1
    return port


@dataclass
class RegistrationResult:
    success: bool
    username: str
    email: str
    password: str
    token: Optional[str] = None
    api_key: Optional[str] = None
    user_id: Optional[str] = None
    error: Optional[str] = None
    identity_info: Optional[str] = None
    proxy_port: Optional[int] = None


def _cdp(v):
    if v is None: return None
    if isinstance(v, (bool, int, float)): return v
    if isinstance(v, str): return v
    if isinstance(v, dict):
        x = v.get("result", {})
        if isinstance(x, dict): x = x.get("result", x)
        if isinstance(x, dict): return x.get("value")
        return x
    return None


def _make_options(proxy_port: int):
    from pydoll.browser.options import ChromiumOptions
    opt = ChromiumOptions()
    opt.headless = False
    opt.binary_location = CHROME_PATH
    opt.add_argument("--no-sandbox")
    opt.add_argument("--disable-dev-shm-usage")
    opt.add_argument("--disable-gpu")
    opt.add_argument("--window-size=1280,900")
    opt.add_argument(f"--proxy-server=socks5://127.0.0.1:{proxy_port}")
    opt.add_argument("--proxy-bypass-list=<-loopback>")
    opt.start_timeout = 30
    return opt


async def _browser_register(username: str, email: str, password: str,
                             proxy_port: int,
                             _kill_chrome: bool = True) -> RegistrationResult:
    """
    Full browser-based registration - v5 (2026-05-02).

    Fixes:
      - Kill leftover Chrome/chromium processes before each attempt
      - go_to timeout 65s; after timeout retry navigation and poll for input
      - NO IIFEs anywhere (pydoll returns None for all IIFE results)
      - Fill form with if-statement JS (no function wrapping)
      - Poll token with simple expressions
      - fetch('/api/me') via await_promise=True
    """
    import subprocess as _sp
    os.environ.setdefault("DISPLAY", ":99")
    from pydoll.browser import Chrome

    label = f"port:{proxy_port}"
    t0 = time.time()
    result = RegistrationResult(
        success=False, username=username, email=email, password=password,
        proxy_port=proxy_port,
        identity_info=f"proxy:{proxy_port}",
    )

    def _ex(raw):
        """Extract value from pydoll execute_script response (no-IIFE safe)."""
        if raw is None:
            return None
        if isinstance(raw, (bool, int, float)):
            return raw
        if isinstance(raw, str):
            return None if raw.strip() in ('null', 'undefined', '') else raw
        if isinstance(raw, dict):
            x = raw.get("result", {})
            if isinstance(x, dict):
                x = x.get("result", x)
            if isinstance(x, dict):
                if x.get("subtype") in ("null", "undefined") or x.get("type") in ("undefined", "object") and x.get("subtype") == "null":
                    return None
                v = x.get("value")
                return v  # may be None for object types (that's ok)
            return x
        return None

    class _CDPDead(Exception):
        pass

    _cdp_fail = 0  # consecutive CDP connection failures

    async def _js(tab, script):
        nonlocal _cdp_fail
        try:
            r = _ex(await tab.execute_script(script))
            _cdp_fail = 0
            return r
        except Exception as _e:
            _e_s = str(_e)
            if ("Connect call failed" in _e_s
                    or "Connection refused" in _e_s
                    or "ConnectionRefusedError" in _e_s):
                _cdp_fail += 1
                if _cdp_fail >= 3:
                    raise _CDPDead(
                        "CDP dead after %d consecutive failures: %s" % (_cdp_fail, _e_s[:80])
                    )
            else:
                _cdp_fail = 0
            print(f"[registrar:{label}] _js err: {_e}", flush=True)
            return None

    # Reap zombie Chrome children (safe: WNOHANG only collects already-exited)
    import os as _os2
    try:
        while True:
            _zpid, _ = _os2.waitpid(-1, _os2.WNOHANG)
            if _zpid <= 0:
                break
    except ChildProcessError:
        pass

    # Kill live Chrome before starting (skip in parallel mode to avoid killing siblings)
    if _kill_chrome:
        try:
            _sp.run(["pkill", "-9", "chrome"], capture_output=True)
            await asyncio.sleep(1)
        except Exception:
            pass

    print(f"[registrar:{label}] Chrome starting...", flush=True)
    try:
      async with Chrome(options=_make_options(proxy_port)) as browser:
        tab = await browser.start()

        # --- Navigate with long timeout, retry once ---
        print(f"[registrar:{label}] navigating...", flush=True)
        nav_ok = False
        for nav_attempt in range(2):
            try:
                await tab.go_to("https://api.airforce/signup/?ref=avjyFSUY9UzdqrRb", timeout=65)
                nav_ok = True
                print(f"[registrar:{label}] nav OK t={time.time()-t0:.1f}s", flush=True)
                break
            except Exception as e:
                print(f"[registrar:{label}] go_to warn #{nav_attempt}: {e}", flush=True)
                await asyncio.sleep(5)

        # Check if page at all loaded by polling for URL
        for _w in range(20):
            _url = await _js(tab, "location.href")
            if _url and "airforce" in str(_url):
                nav_ok = True
                print(f"[registrar:{label}] page confirmed: {_url}", flush=True)
                break
            await asyncio.sleep(1)

        if not nav_ok:
            result.error = "Page failed to load after 2 attempts"
            return result

        # --- Wait for React to render signup form (poll for username input) ---
        print(f"[registrar:{label}] waiting for form inputs...", flush=True)
        form_ready = False
        for _i in range(30):
            _n = await _js(tab, "document.querySelectorAll('input').length")
            if _n is not None and int(_n) >= 3:
                form_ready = True
                print(f"[registrar:{label}] form ready: {_n} inputs at {_i}s", flush=True)
                break
            await asyncio.sleep(1)
        if not form_ready:
            result.error = "Form never rendered (inputs < 3)"
            return result

        # --- Poll for invisible Turnstile token (up to 30s) ---
        print(f"[registrar:{label}] polling invisible Turnstile token (5s fast probe)...", flush=True)
        token = None
        for _i in range(6):  # fast probe 5s
            _t = await _js(tab, "var e=document.querySelector('[name=\"cf-turnstile-response\"]'); e?e.value:''")
            if _t and len(str(_t)) > 20:
                token = str(_t)
                print(f"[registrar:{label}] INVISIBLE TOKEN len={len(token)} at t={time.time()-t0:.1f}s", flush=True)
                break
            if _i == 0:
                print(f"[registrar:{label}] invisible probe (5s)...", flush=True)
            await asyncio.sleep(0.8)

        # --- If no token: shadow bypass + checkbox click ---
        if not token:
            print(f"[registrar:{label}] no invisible token → shadow bypass...", flush=True)
            cf_sr = None
            for _attempt in range(12):
                try:
                    roots = await tab.find_shadow_roots(deep=False)
                    for sr in roots:
                        html = await sr.inner_html
                        if CF_DOM in (html or ""):
                            cf_sr = sr
                            break
                    if cf_sr:
                        print(f"[registrar:{label}] CF shadow root at attempt={_attempt}", flush=True)
                        break
                except Exception:
                    pass
                await asyncio.sleep(2)

            if cf_sr:
                try:
                    ifr  = await cf_sr.query('iframe[src*="' + CF_DOM + '"]', timeout=10)
                    body = await ifr.find(tag_name="body", timeout=15)
                    isr  = await body.get_shadow_root(timeout=15)
                    cb   = await isr.query("span.cb-i", timeout=8)
                    await cb.click()
                    print(f"[registrar:{label}] checkbox clicked", flush=True)
                except Exception as _ex2:
                    print(f"[registrar:{label}] checkbox err: {_ex2}", flush=True)

            # Poll 45s for token after checkbox click
            for _i in range(57):
                _t = await _js(tab, "var e=document.querySelector('[name=\"cf-turnstile-response\"]'); e?e.value:''")
                if _t and len(str(_t)) > 20:
                    token = str(_t)
                    print(f"[registrar:{label}] INTERACTIVE TOKEN len={len(token)} at t={time.time()-t0:.1f}s", flush=True)
                    break
                if _i % 15 == 0:
                    print(f"[registrar:{label}] post-click poll {_i}: no token", flush=True)
                await asyncio.sleep(0.8)

        # --- Fill form fields (no IIFE, use if-statements) ---
        print(f"[registrar:{label}] filling form fields...", flush=True)
        fields = [
            ('username', username),
            ('email', email),
            ('password', password),
            ('confirmPassword', password),
        ]
        for fid, fval in fields:
            # Use if-statement (not IIFE) to conditionally set
            jfid = json.dumps(fid)
            jfval = json.dumps(fval)
            set_js = (
                "var _el=document.getElementById(" + jfid + ");"
                "var _r='notfound';"
                "if(_el){"
                "  try{"
                "    var _s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
                "    _s.call(_el," + jfval + ");"
                "  }catch(_e2){_el.value=" + jfval + ";}"
                "  _el.dispatchEvent(new Event('input',{bubbles:true}));"
                "  _el.dispatchEvent(new Event('change',{bubbles:true}));"
                "  _r='ok';"
                "}"
                "_r"
            )
            fill_r = await _js(tab, set_js)
            print(f"[registrar:{label}] field {fid}: {fill_r}", flush=True)

        await asyncio.sleep(1.5)

        if not token:
            print(f"[registrar:{label}] WARNING: no token before submit, trying anyway...", flush=True)

        # --- Click submit button (no IIFE) ---
        print(f"[registrar:{label}] clicking submit...", flush=True)
        btn_count_raw = await _js(tab, "document.querySelectorAll('button').length")
        btn_count = int(btn_count_raw) if btn_count_raw is not None else 0
        sub_r = "no button"
        for _bi in range(btn_count):
            _bt = await _js(tab, f"document.querySelectorAll('button')[{_bi}].textContent")
            if _bt and ('create' in str(_bt).lower() or 'sign up' in str(_bt).lower()):
                await _js(tab, f"document.querySelectorAll('button')[{_bi}].click()")
                sub_r = f"clicked:{str(_bt).strip()}"
                break
        if sub_r == "no button" and btn_count > 0:
            await _js(tab, "document.querySelectorAll('button')[0].click()")
            sub_r = "fallback:btn[0]"
        print(f"[registrar:{label}] submit: {sub_r}", flush=True)

        # --- Monitor for /dashboard/ redirect ---
        print(f"[registrar:{label}] monitoring redirect (80s)...", flush=True)
        dashboard = False
        for _i in range(80):
            _url = await _js(tab, "location.href")
            if "dashboard" in str(_url or ""):
                print(f"[registrar:{label}] DASHBOARD at t={time.time()-t0:.1f}s URL={_url}", flush=True)
                dashboard = True
                break
            if _i in (5, 15, 30, 50):
                _txt = await _js(tab, "document.body.innerText")
                _txt_s = str(_txt or '')
                print(f"[registrar:{label}] t={_i}s url={str(_url or '')[:60]} text={_txt_s[:200]}", flush=True)
                # Detect early failure conditions
                if "Too many attempts" in _txt_s or "too many" in _txt_s.lower():
                    result.error = f"Rate limited: Too many attempts (port {proxy_port})"
                    print(f"[registrar:{label}] RATE LIMITED - early exit", flush=True)
                    return result
                if "already registered" in _txt_s.lower() or "already exist" in _txt_s.lower():
                    result.error = f"Username/email already registered"
                    print(f"[registrar:{label}] DUPLICATE - early exit", flush=True)
                    return result
            await asyncio.sleep(1)

        if not dashboard:
            _txt = await _js(tab, "document.body.innerText")
            _url = await _js(tab, "location.href")
            result.error = f"No dashboard: url={_url} text={str(_txt or '')[:200]}"
            try:
                await tab.take_screenshot(f"/tmp/reg_fail_{proxy_port}.png")
            except Exception:
                pass
            return result

        # --- Get API key via synchronous XHR (await_promise doesn't work in pydoll) ---
        await asyncio.sleep(1)  # reduced from 6s; cookies are set on redirect
        api_key = None
        user_id = None

        # Try synchronous XHR to several endpoints
        _xhr_js = (
            "var _res=''; var _xhr=new XMLHttpRequest();"
            "try{"
            "  _xhr.open('GET','/api/me',false);"
            "  _xhr.withCredentials=true;"
            "  _xhr.send();"
            "  _res=_xhr.responseText;"
            "}catch(_e){_res='err:'+_e;}"
            "_res"
        )
        me_str = await _js(tab, _xhr_js)
        print(f"[registrar:{label}] /api/me XHR: {str(me_str or '')[:300]}", flush=True)
        if me_str and not str(me_str).startswith("err:"):
            try:
                me = json.loads(me_str)
                api_key = me.get("api_key") or me.get("apiKey") or me.get("key")
                user_id = str(me.get("id") or me.get("user_id") or "")
            except Exception:
                pass

        if not api_key:
            # Try other likely endpoints
            for _ep in ['/api/user', '/api/v1/me', '/api/keys', '/api/account']:
                _ep_js = (
                    f"var _r2=''; var _x2=new XMLHttpRequest();"
                    f"try{{_x2.open('GET','{_ep}',false);_x2.withCredentials=true;_x2.send();_r2=_x2.responseText;}}catch(_e2){{_r2='err:'+_e2;}}"
                    f"_r2"
                )
                _ep_r = await _js(tab, _ep_js)
                print(f"[registrar:{label}] {_ep}: {str(_ep_r or '')[:150]}", flush=True)
                if _ep_r and not str(_ep_r).startswith("err:"):
                    try:
                        _d = json.loads(_ep_r)
                        api_key = _d.get("api_key") or _d.get("apiKey") or _d.get("key")
                        if api_key:
                            break
                    except Exception:
                        pass

        if not api_key:
            # Scrape page text for key-like patterns
            import re as _re
            _txt2 = await _js(tab, "document.body.innerText") or ""
            print(f"[registrar:{label}] dashboard text: {str(_txt2)[:400]}", flush=True)
            _keys = _re.findall(r"[A-Za-z0-9\-_]{32,}", str(_txt2))
            if _keys:
                api_key = _keys[0]
                print(f"[registrar:{label}] scraped key: {api_key[:20]}...", flush=True)

        elapsed = time.time() - t0
        result.success = True
        result.api_key = api_key
        result.user_id = user_id
        print(f"[registrar:{label}] SUCCESS api_key={api_key} t={elapsed:.1f}s", flush=True)
        return result
    except _CDPDead as _cdp_dead:
        result.error = f'CDP disconnected: {_cdp_dead}'
        print(f'[registrar:{label}] CDP DEAD - aborting early', flush=True)
    return result



def _parallel_register_batch(items: list, max_concurrent: int = 3) -> list:
    """
    Thread-based parallel batch registration.

    Each thread gets its own asyncio event loop and Chrome instance.
    Chrome startups are staggered by CHROME_START_STAGGER seconds to avoid
    X11 / CDP port contention when multiple instances launch simultaneously.

    Parameters
    ----------
    items : list of (username, email, password, proxy_port) tuples
    max_concurrent : max simultaneous registrations (also controls thread count)

    Returns list of RegistrationResult in the same order as items.
    """
    import threading as _thr
    import subprocess as _sp2

    CHROME_START_STAGGER = 5  # seconds between successive Chrome launches

    # One-time global Chrome cleanup before the parallel launch
    _sp2.run(["pkill", "-9", "chrome"], capture_output=True)
    time.sleep(2)

    results: list = [None] * len(items)
    _start_lock = _thr.Lock()   # serialise Chrome start-up phase only

    def _worker(idx, username, email, password, port):
        import asyncio as _aio
        # Hold the start lock just long enough to launch Chrome, then release
        # so the next thread can begin its startup (staggered).
        with _start_lock:
            print(f"[parallel:{idx}] Chrome starting port={port} user={username}", flush=True)
            time.sleep(CHROME_START_STAGGER)   # give this Chrome time to bind its CDP port

        loop = _aio.new_event_loop()
        try:
            result = loop.run_until_complete(
                _browser_register(username, email, password, port, _kill_chrome=False)
            )
            results[idx] = result
            _key_p = (result.api_key or "")[:22]
            if result.success:
                print(f"[parallel:{idx}] SUCCESS key={_key_p}... port={port}", flush=True)
            else:
                print(f"[parallel:{idx}] FAIL {result.error}", flush=True)
        except Exception as ex:
            print(f"[parallel:{idx}] EXCEPTION {ex}", flush=True)
            results[idx] = RegistrationResult(
                success=False, username=username, email=email,
                password=password, proxy_port=port,
                error=f"thread exception: {ex}",
            )
        finally:
            loop.close()

    threads = [
        _thr.Thread(
            target=_worker,
            args=(i, u, e, p, pt),
            daemon=True,
        )
        for i, (u, e, p, pt) in enumerate(items)
    ]

    # Start threads sequentially; the _start_lock inside _worker
    # ensures each Chrome has CHROME_START_STAGGER seconds before the next starts.
    for t in threads:
        t.start()

    for t in threads:
        t.join(timeout=220)

    return [r for r in results if r is not None]


class Registrar:
    """AirForce API registrar. Uses full browser-based flow with proxy port rotation."""

    def __init__(self, timeout: float = 150.0, use_fake_ip: bool = False,
                 max_retries: int = 5):
        self.timeout = timeout
        self.max_retries = max_retries

    def register_and_get_key(self, username: str = None, password: str = None,
                             email: str = None, proxy_port: int = None) -> RegistrationResult:
        """Register new account and return API key via browser flow.
        
        Retries up to max_retries times with automatic port rotation:
        - Rate-limited ports are immediately blacklisted and a fresh port is picked.
        - A caller-specified proxy_port is used on the first attempt only; if it
          gets rate-limited, subsequent attempts still rotate via _next_port().
        - New username/email identity is generated for every retry to avoid
          duplicate-account errors.
        """
        from .generator import generate_password

        if password is None:
            password = generate_password()

        sfx = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        if username is None:
            username = f"af_{sfx}"
        if email is None:
            email = f"af_{sfx}@proton.me"

        errors = []
        cur_username = username
        cur_email    = email
        # After a rate-limit we force-rotate regardless of caller's proxy_port hint
        force_rotate = False

        for attempt in range(self.max_retries):
            # Use caller's port on first attempt only; rotate after any failure/rate-limit
            if attempt == 0 and proxy_port and not force_rotate:
                port = proxy_port
            else:
                port = _next_port()

            print(f"[registrar] attempt {attempt+1}/{self.max_retries} port={port}", flush=True)
            try:
                result = asyncio.run(
                    _browser_register(cur_username, cur_email, password, port)
                )
                if result.success:
                    return result

                errors.append(f"port:{port} -> {result.error}")
                print(f"[registrar] attempt {attempt+1} failed: {result.error}", flush=True)

                is_rate_limited = result.error and (
                    "Rate limited" in result.error or
                    "Too many" in result.error or
                    "429" in str(result.error)
                )
                if is_rate_limited:
                    _blacklist_port(port)
                    force_rotate = True  # always use _next_port() from now on
                    print(f"[registrar] port {port} blacklisted → rotating immediately", flush=True)

            except Exception as ex:
                errors.append(f"port:{port} exception: {ex}")
                print(f"[registrar] attempt {attempt+1} exception: {ex}", flush=True)
                force_rotate = True  # exception → also rotate

            # Fresh identity for next attempt (avoids duplicate username errors)
            if attempt < self.max_retries - 1:
                sfx2 = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
                cur_username = f"af_{sfx2}"
                cur_email    = f"af_{sfx2}@proton.me"
                # No sleep: Chrome startup (~5-10s) already provides natural pacing

        return RegistrationResult(
            success=False, username=cur_username, email=cur_email, password=password,
            error=f"All {self.max_retries} attempts failed: {'; '.join(errors[-3:])}",
        )

    def register(self, username: str, password: str,
                 proxy_port: int = None) -> RegistrationResult:
        """Compatibility shim for service.py."""
        sfx = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        email = f"af_{sfx}@proton.me"
        return self.register_and_get_key(username=username, password=password,
                                         email=email, proxy_port=proxy_port)

    def get_api_key(self, token: str, proxy_port: int = None):
        """Deprecated: API key is now obtained via /api/me in browser session."""
        return False, None, "Use register_and_get_key() - browser-based flow"
