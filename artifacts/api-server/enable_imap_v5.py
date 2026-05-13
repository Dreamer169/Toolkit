#!/usr/bin/env python3
"""
enable_imap.py v5

Root-cause fix vs v4:
  - v4 tried to detect session validity via gear icon before navigating to IMAP.
    Gear only appears if MSAL localStorage token is valid → always fails when cookies are stale.
  - v5: Always do fresh login via login.live.com (clear ALL context, fill email+password).
    After confirmed inbox load (gear visible), go directly to IMAP URL.
    Cycle: detect & handle reauth (password re-challenge) → backup email → security code.

Real Microsoft IMAP flow (2026):
  1. Fresh login → inbox loads (gear visible)
  2. goto /mail/options/mail/popimap → Microsoft intercepts with password re-challenge
  3. Fill password → Microsoft may show proofs page (add backup email)
  4. Add backup mail.tm email → Microsoft sends security code
  5. Fill security code → IMAP settings page appears
  6. Toggle IMAP on → Save
"""

import argparse, json, os, re, sys, time
import secrets, string, urllib.request, urllib.error

_MAILTM_BASE   = "https://api.mail.tm"
_MAILTM_DOMAIN = "wshu.net"

def _mt_req(method, path, data=None, token=None, timeout=20):
    url = _MAILTM_BASE + path
    body = json.dumps(data).encode() if data else None
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as exc:
        return 0, {"error": str(exc)}

def mailtm_create():
    chars = string.ascii_lowercase + string.digits
    for attempt in range(4):
        login   = "".join(secrets.choice(chars) for _ in range(14))
        address = f"{login}@{_MAILTM_DOMAIN}"
        pw      = "Mt@" + secrets.token_hex(9)
        code, body = _mt_req("POST", "/accounts", {"address": address, "password": pw})
        if code in (200, 201):
            _, tbody = _mt_req("POST", "/token", {"address": address, "password": pw})
            return address, pw, tbody.get("token", "")
        if code == 429 and attempt < 3:
            wait = 25 * (attempt + 1)
            log(f"  [mail.tm] 429, retry in {wait}s")
            time.sleep(wait)
        else:
            raise RuntimeError(f"mail.tm create failed {code}: {body}")

def mailtm_poll_code(token: str, timeout=240):
    deadline = time.time() + timeout
    seen: set = set()
    log(f"  [mail.tm] polling code (max {timeout}s)...")
    while time.time() < deadline:
        code, body = _mt_req("GET", "/messages", token=token)
        if code == 200:
            for msg in body.get("hydra:member", []):
                mid = msg["id"]
                if mid in seen:
                    continue
                subj  = msg.get("subject", "").lower()
                intro = msg.get("intro", "").lower()
                kws   = ("microsoft", "security", "code", "verify", "account", "confirmation")
                if not any(k in subj or k in intro for k in kws):
                    seen.add(mid)
                    continue
                c2, full = _mt_req("GET", f"/messages/{mid}", token=token)
                if c2 == 200:
                    html = full.get("html", full.get("text", "")) or ""
                    m = re.search(r'\b(\d{4,8})\b', re.sub(r'\s+', ' ', html))
                    if m:
                        log(f"  [mail.tm] code={m.group(1)} from: {msg.get('subject','')[:50]}")
                        return m.group(1)
                seen.add(mid)
        time.sleep(8)
    log("  [mail.tm] timeout - no code")
    return None


def log(msg: str):
    print(msg, flush=True)


_CF_INSOCKS_PORTS = set(range(10820, 10830))

def _is_isp_port(port: int) -> bool:
    return port not in _CF_INSOCKS_PORTS


def _probe_socks5(port: int, timeout: float = 2.5) -> bool:
    """Real SOCKS5 handshake probe: verifies port actually proxies, not just listens.
    Mirrors xray_relay._is_port_alive(). TCP-connect alone misses dead xray routes.
    """
    import socket as _sk
    try:
        s = _sk.socket()
        s.settimeout(timeout)
        s.connect(("127.0.0.1", port))
        s.sendall(b"\x05\x01\x00")  # SOCKS5 no-auth greeting
        if s.recv(2) != b"\x05\x00":  # expect "version=5, method=no-auth"
            s.close(); return False
        # CONNECT to 1.1.1.1:443 to verify outbound routing
        s.sendall(b"\x05\x01\x00\x01\x01\x01\x01\x01\x01\xbb")
        r = s.recv(10)
        s.close()
        return len(r) >= 2 and r[1] == 0x00
    except Exception:
        return False


def _setup_proxy(exit_ip: str = "", manual_proxy: str = "") -> tuple:
    if manual_proxy:
        log(f"  [proxy] manual: {manual_proxy}")
        return manual_proxy, None

    import socket
    xray_inst = None

    # Port priority for Microsoft login (React SPA needs fast US IP for JS bundle loading):
    #   1. tp-in ports 10910-10914: US datacenter IPs (~0.4s), proven to render MS login form
    #   2. ss-in ISP ports: Italy/Turkey/Russia/HK (real ISP exit, proxy:false, slower CDN)
    #   10857=ss-in-7 is CF-proxied → excluded; 10820-10829=in-socks CF → excluded
    ISP_STATIC_PORTS = [10910, 10911, 10912, 10914, 10851, 10853, 10855, 10859]

    if exit_ip:
        try:
            from xray_relay import XrayRelay as _XR
            xray_inst = _XR(exit_ip, force_dynamic=True)
            if xray_inst.start(timeout=15.0):
                url = xray_inst.socks5_url
                log(f"  [proxy] XrayRelay force_dynamic exit_ip={exit_ip} → {url}")
                return url, xray_inst
            else:
                log(f"  [proxy] XrayRelay force_dynamic({exit_ip}) failed, trying ISP ports")
                xray_inst = None
        except Exception as e:
            log(f"  [proxy] XrayRelay error: {e}, trying ISP ports")
            xray_inst = None

    for port in ISP_STATIC_PORTS:
        if _probe_socks5(port):
            log(f"  [proxy] ISP static port {port}")
            return f"socks5://127.0.0.1:{port}", None

    try:
        import random as _rand
        _ps = json.load(open('/tmp/cf_pool_state.json'))
        _avail = [x['ip'] for x in _ps.get('available', []) if isinstance(x, dict) and x.get('ip')]
        if _avail:
            for _ip in _rand.sample(_avail[:30], min(5, len(_avail[:30]))):
                from xray_relay import XrayRelay as _XR2
                xray_inst = _XR2(_ip, force_dynamic=True)
                if xray_inst.start(timeout=12.0):
                    url = xray_inst.socks5_url
                    log(f"  [proxy] CF pool IP={_ip} → {url}")
                    return url, xray_inst
                else:
                    xray_inst = None
    except Exception as e:
        log(f"  [proxy] CF pool error: {e}")

    raise RuntimeError("❌ 所有代理均失败，严禁直连，中止。")


_DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")

def db_get_account(account_id: int) -> dict:
    import psycopg2, psycopg2.extras
    conn = psycopg2.connect(_DB_URL)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT id, email, password, cookies_json, fingerprint_json, user_agent, exit_ip, proxy_port "
            "FROM accounts WHERE platform='outlook' AND id=%s",
            (account_id,)
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"account id={account_id} not found")
        return dict(row)
    finally:
        conn.close()

def db_tag_imap_enabled(account_id: int):
    """Tag account with imap_enabled AND pop_enabled (both enabled together).
    v9.37 Fix10: use string-based tag update (not array_cat which may fail on text-type tags).
    """
    import psycopg2
    try:
        conn = psycopg2.connect(_DB_URL)
        cur  = conn.cursor()
        # Read current tags (stored as comma-separated TEXT, not array)
        cur.execute("SELECT tags FROM accounts WHERE id=%s", (account_id,))
        row = cur.fetchone()
        existing_tags = set(t.strip() for t in (row[0] or "").split(",") if t.strip()) if row else set()
        existing_tags.add("imap_enabled")
        existing_tags.add("pop_enabled")
        new_tags_str = ",".join(sorted(existing_tags))
        cur.execute(
            "UPDATE accounts SET tags=%s, updated_at=now() WHERE id=%s",
            (new_tags_str, account_id),
        )
        conn.commit()
        conn.close()
        log(f"  [db] account {account_id} tagged: {new_tags_str}")
    except Exception as e:
        log(f"  [db] tag write failed: {e}")


def _find_isp_proxy() -> str:
    """Find first available ISP static port (real IP, not CF). Used for login.live.com."""
    import socket
    # tp-in ports (US IP, ~0.4s) first — proven to render Microsoft login React SPA
    # then ss-in ISP direct (Italy/Turkey/Russia/HK, proxy:false)
    ISP_PORTS = [10910, 10911, 10912, 10914, 10851, 10853, 10855, 10859]
    for port in ISP_PORTS:
        if _probe_socks5(port):
            return f"socks5://127.0.0.1:{port}"
    return ""


def launch_browser(proxy: str = "", headless: bool = True):
    from patchright.sync_api import sync_playwright
    import subprocess as _sp
    try:
        _sp.run(["sync"], check=False, timeout=3)
        with open("/proc/sys/vm/drop_caches", "w") as _d:
            _d.write("1\n")
    except Exception:
        pass

    p = sync_playwright().start()
    # Prefer headless-shell: faster JS execution, less memory, better for CI
    _SHELL = "/root/.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell"
    _FULL  = "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome"
    _exec  = _SHELL if os.path.exists(_SHELL) else (_FULL if os.path.exists(_FULL) else None)
    if _exec:
        log(f"  [browser] using: {os.path.basename(_exec)}")
    launch_args = dict(
        headless=headless,
        executable_path=_exec,
        args=[
            "--lang=en-US,en",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            "--ignore-certificate-errors",
            "--allow-running-insecure-content",
            "--disable-sync",
            "--metrics-recording-only",
            "--mute-audio",
            "--disable-renderer-backgrounding",
            "--disable-features=Translate,BackForwardCache,OptimizationHints",
            "--js-flags=--max-old-space-size=512",
        ],
    )
    if proxy:
        launch_args["proxy"] = {"server": proxy}
    b = p.chromium.launch(**launch_args)
    return p, b


IMAP_URL  = "https://outlook.live.com/mail/options/mail/popimap"
INBOX_URL = "https://outlook.live.com/mail/inbox"
LOGIN_URL = "https://login.live.com/login.srf?wa=wsignin1.0"

_GEAR_SEL = (
    '#owaSettingsBtn_container,'
    '[aria-label="Settings"],'
    '[aria-label="Settings, try new Outlook"]'
)
_EMAIL_SEL = 'input[name="loginfmt"],input[type="email"],input[name="username"]'
_PW_SEL    = 'input[type="password"],input[name="passwd"]'


def _ss(page, tag: str, label: str):
    safe = label.replace("/", "_").replace("@", "_at_")
    path = f"/tmp/imap5_{tag}_{safe}.png"
    try:
        page.screenshot(path=path)
        log(f"  [ss] {path}")
    except Exception:
        pass


def _txt(page) -> str:
    try:
        # Try dialog/modal first (Settings panel uses a modal overlay)
        result = page.evaluate("""
            () => {
                // Outlook Settings dialog is a modal overlay
                var selectors = [
                    '[role="dialog"]', '[class*="ms-Modal"]',
                    '[data-testid*="settings"]', '[class*="SettingsPanel"]',
                    '[class*="setting"]'
                ];
                for (var s of selectors) {
                    var el = document.querySelector(s);
                    if (el && el.innerText && el.innerText.length > 50)
                        return el.innerText.slice(0,3000);
                }
                return document.body ? document.body.innerText.slice(0,2000) : '';
            }
        """) or ""
        return result.lower()
    except Exception:
        return ""


def _url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


def _react_fill(page, selector: str, value: str):
    page.evaluate("""([sel, val]) => {
        const inp = document.querySelector(sel);
        if (!inp) return;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
        setter.call(inp, val);
        inp.dispatchEvent(new Event('input',  {bubbles:true}));
        inp.dispatchEvent(new Event('change', {bubbles:true}));
    }""", [selector, value])


def _click_primary(page, extra_sels=None, timeout=3000) -> bool:
    sels = [
        'button[data-testid="primaryButton"]',
        'input[id="idSIButton9"]',
        'input[type="submit"]',
        'button[type="submit"]',
        'input[value="Next"]',
        'input[value="Submit"]',
        'button:has-text("Next")',
        'button:has-text("Continue")',
    ] + (extra_sels or [])
    for s in sels:
        try:
            loc = page.locator(s).first
            if loc.is_visible(timeout=timeout):
                loc.click()
                page.wait_for_timeout(1800)
                return True
        except Exception:
            continue
    return False


def _skip_interrupts(page) -> bool:
    """Click through Stay-signed-in / MFA skip prompts. Returns True if something was clicked."""
    for sel in [
        'button:has-text("Skip for now")', 'button:has-text("Maybe later")',
        'button:has-text("Not now")',       'button:has-text("Skip")',
        'a:has-text("Skip for now")',       'a:has-text("Maybe later")',
        'input[type="submit"][value="No"]', 'button:has-text("No")',
        '[data-testid="secondaryButton"]',  '#idBtn_Back',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=800):
                loc.click()
                page.wait_for_timeout(1500)
                log(f"  [skip] clicked: {sel}")
                return True
        except Exception:
            continue
    return False


def _wait_for_gear(page, timeout_s=45) -> bool:
    """Wait for the Outlook SPA gear/settings icon to appear (= inbox fully loaded)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if page.locator(_GEAR_SEL).first.is_visible(timeout=2000):
                log("  [gear] ✅ settings icon visible")
                return True
        except Exception:
            pass
        page.wait_for_timeout(2000)
    log(f"  [gear] ❌ not found after {timeout_s}s, url={_url(page)[:80]}")
    return False


def _wait_for_input_js(page, timeout_s=60) -> bool:
    """
    Poll via JS for email/password input element — faster than Playwright locator
    because it doesn't wait for element to be in layout, just in DOM.
    Returns True once an input is detected.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            found = page.evaluate("""() => {
                var sels = ['input[name="loginfmt"]','input[type="email"]',
                            'input[name="username"]','input[type="password"]',
                            'input[name="passwd"]'];
                for (var s of sels) {
                    var el = document.querySelector(s);
                    if (el) return s;
                }
                return null;
            }""")
            if found:
                log(f"  [login] JS detected input: {found}")
                return True
        except Exception:
            pass
        page.wait_for_timeout(1500)
    return False


def _do_fresh_login(page, ctx, email: str, password: str,
                    isp_proxy: str = "") -> bool:
    """
    Full fresh login via login.live.com.
    v5 fixes:
    - Clears ALL cookies + localStorage before login.
    - wait_until="load" + networkidle wait → ensures React form is rendered.
    - JS polling for input detection (faster, proxy-friendly).
    - If current proxy is CF VLESS, the isp_proxy param provides ISP fallback.
      CF VLESS slows JS execution on login.live.com → forms take >60s to render.
      Solution: navigate login page through ISP port if isp_proxy is provided.
    """
    if not password:
        log("  [login] no password, cannot do fresh login")
        return False

    log("  [login] v5: clearing context for fresh login...")
    try:
        ctx.clear_cookies()
    except Exception as e:
        log(f"  [login] clear_cookies: {e}")
    try:
        page.evaluate("try{localStorage.clear();sessionStorage.clear();}catch(e){}")
    except Exception:
        pass

    # ── ISP proxy context switch (mirrors outlook_register.py residential fallback) ──
    # When main browser proxy is CF VLESS (exit_ip flow), the Microsoft React SPA
    # at login.microsoftonline.com cannot render (JS bundles stall through CF).
    # Fix: open a NEW context with ISP/tp-in proxy for the login step,
    # then restore cookies into the main context for subsequent Outlook navigation.
    _login_ctx  = ctx    # will be replaced if isp_proxy is different
    _login_page = page
    _isp_ctx_created = False
    if isp_proxy and isp_proxy != getattr(ctx, "_proxy_server", isp_proxy):
        try:
            _isp_browser = page.context.browser
            _login_ctx = _isp_browser.new_context(
                proxy={"server": isp_proxy},
                locale="en-US",
                timezone_id="America/Los_Angeles",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            _login_page = _login_ctx.new_page()
            _isp_ctx_created = True
            log(f"  [login] ISP context created: {isp_proxy} (CF VLESS fallback)")
        except Exception as _ice:
            log(f"  [login] ISP ctx failed ({_ice}), using main ctx")
            _login_ctx = ctx
            _login_page = page
            _isp_ctx_created = False

    # Login URL priority (same as v4 which confirmed working):
    #   1. outlook.live.com/mail/0/?prompt=login → OAuth redirect with Outlook client_id
    #      This is the CORRECT entry point; login.live.com/login.srf?wa=wsignin1.0
    #      without proper OAuth context returns a 386-byte "technical problems" error.
    #   2. login.live.com/login.srf?wa=wsignin1.0 (classic fallback)
    #   3. login.microsoftonline.com with full OAuth params + login_hint
    _login_urls = [
        "https://outlook.live.com/mail/0/?prompt=login",
        LOGIN_URL,
        "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
        "?client_id=9ba1a5c7-f17a-4de9-a1f1-6178c8d51223"
        "&scope=openid+profile+email&response_type=code"
        "&redirect_uri=https%3A%2F%2Foutlook.live.com%2F&prompt=login"
        f"&login_hint={email}",
    ]
    _email_input_sel = 'input[name="loginfmt"],input[type="email"],input[name="username"]'

    _login_form_found = False
    for _lu in _login_urls:
        log(f"  [login] trying: {_lu[:80]}")
        try:
            _login_page.goto(_lu, timeout=45000, wait_until="domcontentloaded")
        except Exception as _ge:
            log(f"  [login] goto (ok): {str(_ge)[:60]}")
        _login_page.wait_for_timeout(3000)
        cur = _url(_login_page)
        log(f"  [login] landed: {cur[:90]}")
        # Only skip real error pages — NOT 0-byte SPA (React not yet mounted).
        # Mirrors auto_device_code._pick_residential_proxy / outlook_register.py pattern:
        #   0b       → SPA still loading, fall through to 60s email-input wait
        #   1-800b + error keyword → genuine error page, skip to next URL
        try:
            _blen = _login_page.evaluate("()=>document.body?.innerHTML?.length||0")
            if _blen == 0:
                log(f"  [login] body=0 (SPA loading), waiting for form...")
            elif _blen < 800:
                _btxt = _login_page.evaluate("()=>document.body?.innerText?.slice(0,200)||''")
                _ERR_KW = ("technical problem", "unauthorized_client", "unable to complete",
                           "does not exist", "not enabled")
                if any(kw in _btxt.lower() for kw in _ERR_KW):
                    log(f"  [login] ⚠ error page ({_blen}b): {_btxt[:150]}")
                    continue  # skip to next URL
                log(f"  [login] small body ({_blen}b), falling through to form wait...")
        except Exception:
            pass
        # v4.1: wait up to 60s for email input (proxy latency through VLESS)
        try:
            _ei = _login_page.locator(_email_input_sel).first
            if _ei.is_visible(timeout=60000):
                _login_form_found = True
                log(f"  [login] ✅ email input found at: {cur[:80]}")
                break
        except Exception:
            pass
        log(f"  [login] ⚠ input not visible at {_lu[:60]}, trying next...")

    if not _login_form_found:
        log("  [login] ⚠ email input not found on any login URL — proceeding anyway")
        _ss(_login_page, "login_no_input", email.split("@")[0])

    # Fill email via react_fill (sets value + fires React synthetic events)
    _react_fill(_login_page, 'input[name="loginfmt"]', email)
    log("  [login] email filled")

    _login_page.wait_for_timeout(400)
    _click_primary(_login_page)
    _login_page.wait_for_timeout(4000)
    log(f"  [login] email submitted, url={_url(_login_page)[:80]}")

    # Wait specifically for PASSWORD field (not just any input — email input may linger in DOM)
    _pw_visible = False
    try:
        _pw_loc = _login_page.locator('input[name="passwd"],input[type="password"]').first
        _pw_loc.wait_for(state="visible", timeout=25000)
        _pw_visible = True
        log("  [login] ✅ password field visible")
    except Exception:
        log("  [login] ⚠ password field not visible after 25s")

    # Fill password
    if _pw_visible:
        try:
            _pw_loc.fill(password)
            _login_page.wait_for_timeout(300)
        except Exception:
            _react_fill(_login_page, 'input[name="passwd"]', password)
    else:
        _react_fill(_login_page, 'input[name="passwd"]', password)
        log("  [login] password filled via react_fill (blind)")

    _login_page.wait_for_timeout(400)
    _click_primary(_login_page)
    _login_page.wait_for_timeout(4000)
    log(f"  [login] password submitted, url={_url(_login_page)[:80]}")

    # Skip Stay-signed-in? / MFA prompts (same as v4)
    for _ in range(8):
        if not _skip_interrupts(_login_page):
            break
        _login_page.wait_for_timeout(2000)

    # Wait for redirect to outlook.live.com/mail (v4 proven approach)
    try:
        _login_page.wait_for_url("**/mail/**", timeout=45000)
        log(f"  [login] ✅ redirected to mail, url={_url(_login_page)[:80]}")
    except Exception:
        log(f"  [login] wait_for_url timeout, cur={_url(_login_page)[:80]}")
    _login_page.wait_for_timeout(3000)

    # Handle msalAuthRedirect if present (v4 approach)
    for _mw in range(3):
        _fu = _url(_login_page)
        if "msalAuthRedirect" in _fu:
            log(f"  [login] msalAuthRedirect detected (pass {_mw+1}), waiting for MSAL...")
            _wait_for_msal_complete(_login_page, timeout_s=90)
            _login_page.wait_for_timeout(3000)
        else:
            break

    # Wait for networkidle (SPA MSAL initialization)
    try:
        _login_page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    _login_page.wait_for_timeout(2000)

    # ── Cookie restore: if ISP context was used, copy cookies to main context ──
    # Mirrors outlook_register.py: _saved_state = page.context.storage_state() +
    # _res_ctx.add_cookies(...) pattern — ensures Outlook SPA nav uses CF exit_ip
    # while login was done through ISP proxy for MS React SPA rendering.
    if _isp_ctx_created:
        try:
            _state = _login_ctx.storage_state()
            _cookies = _state.get("cookies", [])
            if _cookies:
                ctx.add_cookies(_cookies)
                log(f"  [login] ISP ctx: restored {len(_cookies)} cookies to main ctx")
            # Navigate the main page to outlook mail (it has the ISP session cookies now)
            try:
                page.goto("https://outlook.live.com/mail/0/", timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                log(f"  [login] main ctx navigated to mail: {_url(page)[:80]}")
            except Exception as _ne:
                log(f"  [login] main ctx nav: {str(_ne)[:60]}")
        except Exception as _cre:
            log(f"  [login] cookie restore failed: {_cre}")
        finally:
            try: _login_ctx.close()
            except Exception: pass
        # Use main page for gear check
        _check_page = page
    else:
        _check_page = _login_page

    # Confirm gear (MSAL + SPA fully loaded)
    gear_ok = _wait_for_gear(_check_page, timeout_s=30)
    final_url = _url(_check_page)
    log(f"  [login] final url={final_url[:100]} gear={gear_ok}")

    if not gear_ok and "outlook.live.com/mail" in final_url:
        log("  [login] on mail but no gear — waiting 15s more...")
        _check_page.wait_for_timeout(15000)
        gear_ok = _wait_for_gear(_check_page, timeout_s=20)

    # CRITICAL: check startswith to avoid matching "outlook.live.com" in login.live.com scope params
    success = final_url.startswith("https://outlook.live.com/mail") or (
              "outlook.live.com/mail" in final_url and gear_ok)
    log(f"  [login] {'✅ success' if success else '⚠ uncertain — proceeding anyway'}")
    return success


def _page_state(page) -> str:
    """
    Classify what page the browser is currently on.
    Returns one of: 'imap', 'reauth', 'proofs', 'login', 'inbox', 'error', 'unknown'
    """
    u = _url(page)
    txt = _txt(page)

    # IMAP settings page
    if "outlook.live.com" in u and ("popimap" in u or "options/mail" in u):
        # Also check for IMAP content in text
        if any(k in txt for k in ("imap", "pop", "forwarding")):
            return "imap"
        # On the URL but maybe still loading
        return "imap_loading"

    # Login / reauth pages
    if any(x in u for x in ("login.live.com", "login.microsoftonline.com",
                              "account.live.com/reauth", "account.microsoft.com/reauth")):
        if "password" in txt or page.locator(_PW_SEL).is_visible():
            return "reauth"
        if page.locator(_EMAIL_SEL).is_visible():
            return "login"
        return "reauth"

    # Proofs / backup email page
    if any(x in u for x in ("account.live.com/proofs", "account.microsoft.com/proofs",
                              "account.live.com/proof", "account.microsoft.com/security")):
        return "proofs"

    # Text-based proofs detection (page may not have changed URL yet)
    PROOFS_KWS = (
        "alternate email", "backup email", "recovery email",
        "security info", "add email", "add a backup",
        "add another email", "keep your account secure",
        "protect your account", "add a way to", "proof up",
        "add your email", "verify your email",
    )
    if any(k in txt for k in PROOFS_KWS):
        return "proofs"

    # Password re-challenge on a non-login URL (e.g., inside Outlook SPA)
    REAUTH_KWS = ("sign in again", "verify your identity", "re-enter your password",
                  "confirm your password", "enter your password")
    if any(k in txt for k in REAUTH_KWS):
        return "reauth"
    try:
        if page.locator(_PW_SEL).first.is_visible(timeout=500):
            return "reauth"
    except Exception:
        pass

    # Inbox (logged in but not on IMAP page)
    if "outlook.live.com/mail" in u and "options" not in u:
        return "inbox"

    # Error page
    if "chrome-error://" in u or ("something went wrong" in txt and "outlook.live.com" in u):
        return "error"

    # Generic Microsoft marketing page (not logged in)
    if "microsoft.com/en-us/microsoft-365/outlook" in u:
        return "login"

    return "unknown"



def _handle_fwd_signin(page, email: str, password: str) -> bool:
    """
    v9.37 FIX: Handle the 'Sign in' button inside the 'Forwarding and IMAP' settings panel.

    Microsoft requires re-verification before showing IMAP/POP toggles.
    The panel shows: "Sign in and verify your account to forward your email or sync to your devices."

    Key fix: clicking "Sign in" may open a POPUP window (new browser page) for OAuth.
    The old code only waited 8s and never detected popups. This version:
    1. Sets up a popup listener BEFORE clicking Sign in
    2. If popup appears: handles auth in the popup (fills email+password)
    3. If no popup: waits up to 15s for in-page auth to complete
    4. Verifies the IMAP panel is now unlocked (Sign-in wall gone)

    Returns True if Sign in was clicked and auth was attempted.
    """
    log("  [fwd-signin] checking for Sign in button in IMAP panel...")

    # Quick check: is Sign-in wall actually present?
    txt_check = _txt(page).lower()
    has_signin_wall = "sign in and verify" in txt_check or "sign in to verify" in txt_check
    if not has_signin_wall:
        # Check if toggles already visible (panel already unlocked)
        try:
            page.wait_for_selector(
                'input[type="radio"],[role="radio"],[role="switch"]',
                timeout=1500
            )
            wall_txt = _txt(page).lower()
            if ("enable imap" in wall_txt or "imap access" in wall_txt or
                    "pop access" in wall_txt or "disable imap" in wall_txt):
                log("  [fwd-signin] panel already unlocked (no Sign-in wall + toggles visible)")
                return False
        except Exception:
            pass

    # ── Set up popup listener BEFORE clicking Sign in ─────────────────────
    _popup_pages = []
    def _on_new_page(new_page):
        _popup_pages.append(new_page)
        log(f"  [fwd-signin] popup detected: {_url(new_page)[:80]}")

    page.context.on("page", _on_new_page)

    # ── Click the Sign in button ──────────────────────────────────────────
    _signin_clicked = False
    for sel in [
        'button:has-text("Sign in")',
        '[role="button"]:has-text("Sign in")',
        'a:has-text("Sign in")',
        'button:has-text("sign in")',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                loc.click()
                log(f"  [fwd-signin] ✅ clicked Sign in via {sel}")
                _signin_clicked = True
                break
        except Exception:
            continue

    if not _signin_clicked:
        _js = page.evaluate("""() => {
            var kws = ['sign in', 'signin'];
            for (var el of document.querySelectorAll('button,a,[role="button"]')) {
                var t = el.textContent.trim().toLowerCase();
                for (var k of kws) {
                    if (t === k) { el.click(); return 'js:' + el.tagName + '/' + el.textContent.trim(); }
                }
            }
            return 'not-found';
        }""")
        log(f"  [fwd-signin] JS: {_js}")
        _signin_clicked = _js != "not-found"

    if not _signin_clicked:
        page.context.remove_listener("page", _on_new_page)
        log("  [fwd-signin] no Sign in button found — panel may already be unlocked")
        return False

    # ── Wait and handle popup (if one opened) ────────────────────────────
    # Wait up to 5s for a popup to appear
    import time as _time
    _popup_deadline = _time.time() + 5.0
    while _time.time() < _popup_deadline and not _popup_pages:
        page.wait_for_timeout(500)

    if _popup_pages:
        popup = _popup_pages[0]
        log(f"  [fwd-signin] popup detected, waiting for navigation from about:blank...")
        # v9.37 Fix8: wait for popup to navigate AWAY from about:blank
        # The popup starts at about:blank and then navigates to the real auth URL.
        # Old code handled it at about:blank before it loaded, so no auth was done.
        _nav_deadline = _time.time() + 12.0
        while _time.time() < _nav_deadline:
            popup_url_now = _url(popup)
            if popup_url_now and popup_url_now != "about:blank":
                break
            page.wait_for_timeout(500)
        popup_url = _url(popup)
        log(f"  [fwd-signin] popup url after wait: {popup_url[:100]}")
        # Try to load state
        try:
            popup.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        popup_url = _url(popup)
        log(f"  [fwd-signin] popup url (loaded): {popup_url[:100]}")
        # Handle auth in popup
        _POPUP_AUTH_DOMAINS = (
            "login.live.com", "login.microsoftonline.com",
            "account.live.com", "outlook.live.com",
            "login.windows.net", "microsoftonline.com",
        )
        if any(x in popup_url for x in _POPUP_AUTH_DOMAINS):
            log("  [fwd-signin] auth popup — handling reauth...")
            _handle_reauth(popup, email, password)
            popup.wait_for_timeout(2000)
            # ── v9.37 Fix11: skip proofs/Add + wait for OAuth callback ────────
            # After password submit, Microsoft may show proofs/Add (backup verify).
            # Must skip it (click "Not now"/"Skip") so the popup completes the
            # OAuth flow and fires the callback to the main page.
            # Also handle account.live.com/proofs/* and other interrupt pages.
            _PROOFS_SKIP_SELS = [
                'a:has-text("Not now")',            # proofs/Add "Not now" link
                'a:has-text("Skip for now")',
                'button:has-text("Not now")',
                'button:has-text("Skip for now")',
                'button:has-text("Maybe later")',
                'button:has-text("Skip")',
                'input[type="submit"][value="No"]',
                'button:has-text("No")',
                '#idBtn_Back',
                '[data-testid="secondaryButton"]',
                'a[href*="proofs/Skip"]',           # direct skip link
            ]
            _popup_skip_rounds = 0
            _PROOFS_DOMAINS = ("account.live.com/proofs", "account.microsoft.com/proofs",
                                "account.live.com/proof", "account.microsoft.com/security")
            while _popup_skip_rounds < 8:
                _popup_skip_rounds += 1
                popup_u = _url(popup)
                log(f"  [fwd-signin] popup skip round {_popup_skip_rounds}: {popup_u[:80]}")
                # Check if popup is on Outlook callback URL (OAuth done)
                if "outlook.live.com" in popup_u or popup_u == "" or popup_u == "about:blank":
                    log("  [fwd-signin] popup navigated to Outlook/blank — OAuth done")
                    break
                # Check if popup closed itself
                try:
                    if popup.is_closed():
                        log("  [fwd-signin] popup closed itself — OAuth done")
                        break
                except Exception:
                    break
                # Try skipping interrupt pages
                _skipped = False
                for sel in _PROOFS_SKIP_SELS:
                    try:
                        loc = popup.locator(sel).first
                        if loc.is_visible(timeout=800):
                            loc.click()
                            log(f"  [fwd-signin] popup: skipped via {sel}")
                            popup.wait_for_timeout(2000)
                            _skipped = True
                            break
                    except Exception:
                        continue
                if not _skipped:
                    # No skip button found — check if we're on a known non-skip page
                    if any(x in popup_u for x in ("login.live.com", "login.microsoftonline.com")):
                        # Might need another auth step
                        log("  [fwd-signin] popup back on login page — re-reauthing...")
                        _handle_reauth(popup, email, password)
                        popup.wait_for_timeout(2000)
                    else:
                        # Wait and check again
                        popup.wait_for_timeout(2000)
            _popup_final_url = _url(popup)
            log(f"  [fwd-signin] popup after auth+skip: {_popup_final_url[:100]}")
        elif popup_url == "about:blank" or not popup_url:
            log("  [fwd-signin] popup still at about:blank — may be blocked or already closed")
        else:
            log(f"  [fwd-signin] popup at unexpected URL: {popup_url[:80]}")
        # Wait for main page to update after popup completes OAuth
        log("  [fwd-signin] waiting 8s for main page to receive OAuth callback...")
        page.wait_for_timeout(8000)
        try:
            popup.close()
            log("  [fwd-signin] popup closed")
        except Exception:
            pass
        page.wait_for_timeout(3000)
    else:
        # No popup — URL-based handling
        page.wait_for_timeout(3000)
        u = _url(page)
        log(f"  [fwd-signin] after click (no popup), url={u[:100]}")

        if any(x in u for x in ("login.live.com", "login.microsoftonline.com", "account.live.com")):
            log("  [fwd-signin] redirected to login — handling reauth...")
            _handle_reauth(page, email, password)
            page.wait_for_timeout(3000)
            for _ in range(5):
                if not _skip_interrupts(page):
                    break
                page.wait_for_timeout(1500)
            log(f"  [fwd-signin] after login, url={_url(page)[:100]}")
        elif "outlook.live.com" in u:
            # In-page auth — wait longer for it to resolve
            log("  [fwd-signin] stayed on Outlook — waiting up to 15s for in-page auth...")
            for wait_chunk in range(5):
                page.wait_for_timeout(3000)
                txt_after = _txt(page).lower()
                # Check if Sign-in wall is gone (IMAP toggles appeared)
                if ("enable imap" in txt_after or "imap access" in txt_after or
                        "pop access" in txt_after or "disable imap" in txt_after):
                    log(f"  [fwd-signin] ✅ IMAP panel unlocked after {(wait_chunk+1)*3}s")
                    break
                # Check if wall is still there
                if "sign in and verify" in txt_after:
                    log(f"  [fwd-signin] wall still present after {(wait_chunk+1)*3}s...")
                else:
                    log(f"  [fwd-signin] page changed (wall gone) after {(wait_chunk+1)*3}s")
                    break
            for _ in range(3):
                if not _skip_interrupts(page):
                    break
                page.wait_for_timeout(1000)

    page.context.remove_listener("page", _on_new_page)

    # ── Final state check ─────────────────────────────────────────────────
    txt_final = _txt(page).lower()
    wall_still_present = "sign in and verify" in txt_final
    imap_unlocked = ("enable imap" in txt_final or "imap access" in txt_final or
                     "pop access" in txt_final or "disable imap" in txt_final)
    log(f"  [fwd-signin] final: wall_present={wall_still_present} imap_unlocked={imap_unlocked}")
    return True


def _handle_reauth(page, email: str, password: str) -> bool:
    """
    Handle password re-challenge. Microsoft requires re-auth when navigating to IMAP settings.
    Returns True when password is submitted.
    """
    log("  [reauth] handling password re-challenge...")
    if not password:
        log("  [reauth] ❌ no password available")
        return False

    # May need to fill email first if on a full login page
    try:
        em_loc = page.locator(_EMAIL_SEL).first
        if em_loc.is_visible(timeout=3000):
            log("  [reauth] email input visible — filling email first")
            _react_fill(page, 'input[name="loginfmt"]', email)
            try:
                em_loc.fill(email)
            except Exception:
                pass
            page.wait_for_timeout(400)
            _click_primary(page)
            page.wait_for_timeout(3000)
    except Exception:
        pass

    # Wait for password field
    _pw_visible = False
    try:
        page.locator(_PW_SEL).first.wait_for(timeout=15000)
        _pw_visible = True
        log("  [reauth] password field visible")
    except Exception as e:
        log(f"  [reauth] password wait: {str(e)[:80]}")

    if not _pw_visible:
        log("  [reauth] ⚠ password field not found, continuing anyway")
        return True

    try:
        pw_loc = page.locator(_PW_SEL).first
        pw_loc.fill(password)
        page.wait_for_timeout(500)
        _click_primary(page)
        page.wait_for_timeout(5000)
        log(f"  [reauth] ✅ password submitted, url={_url(page)[:80]}")
    except Exception as e:
        log(f"  [reauth] ⚠ submit error: {str(e)[:100]}")

    # Skip Stay-signed-in etc.
    for _ in range(4):
        if not _skip_interrupts(page):
            break
        page.wait_for_timeout(1500)

    log(f"  [reauth] done, url={_url(page)[:80]}")
    return True


def _handle_proofs(page) -> tuple:
    """
    Handle Microsoft 'add backup email' / proofs page.
    Creates a mail.tm temp address, fills it, clicks Send Code.
    Returns (added: bool, mt_addr: str, mt_token: str)
    """
    log("  [proofs] handling backup email page...")

    try:
        mt_addr, mt_pw, mt_token = mailtm_create()
    except Exception as e:
        log(f"  [proofs] ❌ mail.tm create failed: {e}")
        return False, "", ""
    log(f"  [proofs] mail.tm addr: {mt_addr}")

    # Try to fill the email field
    _filled = False
    EMAIL_INPUT_SELS = [
        'input[name="EmailAddress"]',
        'input[name="ProofConfirmation"]',
        'input[name*="Email"][type="email"]',
        'input[name*="email"][type="email"]',
        'input[placeholder*="email" i]',
        'input[type="email"]',
    ]
    for sel in EMAIL_INPUT_SELS:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                loc.fill(mt_addr)
                page.wait_for_timeout(400)
                _filled = True
                log(f"  [proofs] filled email → {sel}")
                break
        except Exception:
            continue

    if not _filled:
        # JS fallback
        _filled = page.evaluate("""(addr) => {
            for (var inp of document.querySelectorAll('input[type="email"],input[type="text"]')) {
                var st = window.getComputedStyle(inp);
                if (st.display==='none' || st.visibility==='hidden') continue;
                if (!inp.value) {
                    var s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                    s.call(inp, addr);
                    inp.dispatchEvent(new Event('input', {bubbles:true}));
                    inp.dispatchEvent(new Event('change', {bubbles:true}));
                    return true;
                }
            }
            return false;
        }""", mt_addr)
        if _filled:
            log("  [proofs] filled email via JS fallback")

    if not _filled:
        log("  [proofs] ⚠ could not fill email input")
        return False, "", ""

    page.wait_for_timeout(600)

    # Click Send Code / Add / Continue
    _click_primary(page, [
        'button:has-text("Send code")',
        'button:has-text("Send Code")',
        'button:has-text("Add")',
        'button:has-text("Continue")',
        'button:has-text("Next")',
        'input[value*="Send"]',
        'input[value*="Add"]',
        'input[value*="Continue"]',
    ])
    page.wait_for_timeout(3000)
    log(f"  [proofs] code sent, url={_url(page)[:80]}")
    return True, mt_addr, mt_token


def _handle_security_code(page, mt_token: str) -> bool:
    """
    Poll mail.tm for the security code, fill it in, submit.
    Returns True if code was submitted.
    """
    if not mt_token:
        return False
    code = mailtm_poll_code(mt_token, timeout=240)
    if not code:
        log("  [code] ❌ no code received")
        return False

    log(f"  [code] received code={code}")

    _code_filled = False
    CODE_SELS = [
        'input[name*="Code" i]',
        'input[name*="code" i]',
        'input[name*="otp" i]',
        'input[placeholder*="code" i]',
        'input[type="number"]',
        'input[type="text"][maxlength="6"]',
        'input[type="text"][maxlength="7"]',
        'input[type="text"][maxlength="8"]',
        'input[type="text"]',
    ]
    for sel in CODE_SELS:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                loc.fill(code)
                page.wait_for_timeout(400)
                _code_filled = True
                log(f"  [code] filled → {sel}")
                break
        except Exception:
            continue

    if not _code_filled:
        page.evaluate("""(c) => {
            for (var inp of document.querySelectorAll('input')) {
                var st = window.getComputedStyle(inp);
                if (st.display==='none' || st.visibility==='hidden') continue;
                var ml = parseInt(inp.getAttribute('maxlength') || '99');
                if (ml >= 4 && ml <= 9 && !inp.value) {
                    var s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                    s.call(inp, c);
                    inp.dispatchEvent(new Event('input', {bubbles:true}));
                    inp.dispatchEvent(new Event('change', {bubbles:true}));
                    return;
                }
            }
        }""", code)
        log("  [code] filled via JS fallback")

    page.wait_for_timeout(500)
    _click_primary(page, [
        'button:has-text("Verify")',
        'button:has-text("Confirm")',
        'button:has-text("Submit")',
        'button:has-text("Next")',
        'button:has-text("Done")',
        'input[value*="Verify"]',
        'input[value*="Confirm"]',
    ])
    page.wait_for_timeout(5000)
    log(f"  [code] submitted, url={_url(page)[:80]}")
    return True


def _nav_to_imap_direct(page) -> str:
    """
    Navigate to IMAP settings page.
    Strategy (matches v4 proven approach):
      1. Direct goto IMAP_URL + 10s settle
      2. If URL settled but settings panel empty → gear-click SPA navigation
      3. chrome-error → inbox warmup + retry
    """
    log(f"  [nav] goto IMAP URL: {IMAP_URL}")
    try:
        page.goto(IMAP_URL, timeout=35000, wait_until="domcontentloaded")
    except Exception as e:
        log(f"  [nav] goto timeout (ok): {str(e)[:80]}")

    # Wait 10s for SPA to settle (auth redirect cycle takes ~5-8s)
    page.wait_for_timeout(10000)
    u = _url(page)
    log(f"  [nav] url after 10s: {u[:100]}")

    # If still in auth redirect loop, wait 10s more
    if "bO=" in u or "msalAuthRedirect" in u:
        log("  [nav] still in auth redirect, waiting 10s more...")
        page.wait_for_timeout(10000)
        u = _url(page)
        log(f"  [nav] url after 20s: {u[:80]}")

    # chrome-error → proxy blocked IMAP URL (HTTP 417)
    if "chrome-error://" in u:
        log("  [nav] ⚠ chrome-error — inbox warmup + retry...")
        try:
            page.goto(INBOX_URL, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            _wait_for_gear(page, timeout_s=20)
            page.goto(IMAP_URL, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(10000)
            u = _url(page)
        except Exception as e2:
            log(f"  [nav] warmup failed: {str(e2)[:60]}")

    # v9.37 FIX: Fast-check with short timeout (3s) to avoid mid-wait page navigation.
    # Long timeouts here allowed MSAL session refresh to redirect the page away.
    if "outlook.live.com" in u and ("popimap" in u or "options/mail" in u):
        if _has_imap_content(page, 3000):
            # Re-confirm URL hasn't drifted during the check
            u2 = _url(page)
            if "outlook.live.com" in u2 and ("popimap" in u2 or "options/mail" in u2):
                log("  [nav] ✅ IMAP content confirmed via direct goto")
                state = _page_state(page)
                log(f"  [nav] state={state}, url={u2[:100]}")
                return state
            else:
                log(f"  [nav] ⚠ URL drifted during fast-check to {u2[:60]} — continuing gear-click path")
                u = u2

    # Gear-click SPA path (v4-proven): new Outlook SPA may not render settings panel
    # from cold goto — need gear → Mail settings → POP/IMAP navigation.
    _gear_sel = '#owaSettingsBtn_container,[aria-label="Settings"],[aria-label="Settings, try new Outlook"]'
    _gear_visible = False
    try:
        _gear_visible = page.locator(_gear_sel).first.is_visible(timeout=5000)
    except Exception:
        pass

    if _gear_visible:
        log("  [nav] gear visible — using SPA navigation to IMAP settings")
        # First try: hot-navigate via SPA (gear visible = Outlook SPA active)
        try:
            page.goto(IMAP_URL, timeout=25000, wait_until="domcontentloaded")
        except Exception as ge:
            log(f"  [nav] SPA goto: {str(ge)[:60]}")
        page.wait_for_timeout(4000)
        if _has_imap_content(page, 8000):
            log("  [nav] ✅ IMAP content via SPA hot-goto")
            state = _page_state(page)
            log(f"  [nav] state={state}, url={_url(page)[:100]}")
            return state

        # Gear click → Mail → Forwarding and IMAP
        log("  [nav] gear-click menu navigation...")
        for gs in ['#owaSettingsBtn_container', '[aria-label="Settings"]',
                   '[aria-label="Settings, try new Outlook"]']:
            try:
                g = page.locator(gs).first
                if g.is_visible(timeout=2000):
                    g.click()
                    page.wait_for_timeout(2500)
                    log(f"  [nav] gear clicked: {gs}")
                    break
            except Exception:
                continue

        # Click "Mail" in settings menu — use inner_text() not textContent to avoid
        # matching parent containers whose nested children include "Mail"
        _mail_clicked = False
        for _ml_sel in [
            'button:has-text("Mail")', '[role="menuitem"]:has-text("Mail")',
            '[role="option"]:has-text("Mail")', '[role="treeitem"]:has-text("Mail")',
        ]:
            try:
                _ml = page.locator(_ml_sel).first
                if _ml.is_visible(timeout=2000):
                    _ml_txt = _ml.inner_text().strip().lower()
                    if _ml_txt in ("mail", "email"):
                        _ml.click()
                        _mail_clicked = True
                        log(f"  [nav] Mail clicked via locator: {_ml_sel}")
                        page.wait_for_timeout(2000)
                        break
            except Exception:
                continue

        if not _mail_clicked:
            # JS fallback: match direct text nodes only (not nested textContent)
            page.evaluate("""() => {
                function directText(el) {
                    return Array.from(el.childNodes)
                        .filter(function(n) { return n.nodeType === 3; })
                        .map(function(n) { return n.textContent.trim(); })
                        .join('').trim();
                }
                var sels = 'button,[role="option"],[role="menuitem"],[role="treeitem"],li>a,a';
                for (var el of document.querySelectorAll(sels)) {
                    var t = directText(el);
                    if (t === 'Mail' || t === 'Email') { el.click(); return; }
                }
            }""")
            page.wait_for_timeout(2000)
            log("  [nav] Mail clicked via JS fallback")

        # ── BUG FIX: Click "Forwarding and IMAP" to enter the sub-page ──────
        # Root cause: Settings panel opens, Mail section expands, and
        # "Forwarding and IMAP" appears in the sidebar — but the old code never
        # clicked it. Old JS used el.textContent (which includes ALL nested
        # children), so parent containers matched before the actual menu item.
        # Fix: use Playwright get_by_text(exact=True) then JS with direct text nodes.
        _fwd_clicked = False
        _fwd_labels = [
            "Forwarding and IMAP", "POP and IMAP", "Sync Email",
            "Forwarding", "POP / IMAP", "Email sync",
        ]
        for _lbl in _fwd_labels:
            try:
                _loc = page.get_by_text(_lbl, exact=True).first
                if _loc.is_visible(timeout=3000):
                    _loc.click()
                    _fwd_clicked = True
                    log(f"  [nav] ✅ clicked '{_lbl}' via get_by_text(exact)")
                    break
            except Exception:
                continue

        if not _fwd_clicked:
            # JS fallback: compare direct text nodes only (not nested textContent)
            _fwd_result = page.evaluate("""() => {
                function directText(el) {
                    return Array.from(el.childNodes)
                        .filter(function(n) { return n.nodeType === 3; })
                        .map(function(n) { return n.textContent.trim(); })
                        .join('').trim().toLowerCase();
                }
                var sels = 'button,[role="option"],[role="menuitem"],[role="treeitem"],li>a,a,span';
                var kws = ['forwarding and imap', 'pop and imap', 'sync email',
                           'forwarding', 'pop / imap', 'email sync'];
                for (var el of document.querySelectorAll(sels)) {
                    var t = directText(el);
                    if (!t) continue;
                    for (var i = 0; i < kws.length; i++) {
                        if (t === kws[i] || t.indexOf(kws[i]) === 0) {
                            el.click();
                            return 'clicked:' + t;
                        }
                    }
                }
                return 'not-found';
            }""")
            log(f"  [nav] JS fallback forwarding click: {_fwd_result}")
            _fwd_clicked = (_fwd_result != 'not-found')

        # Wait for IMAP sub-page to render after clicking "Forwarding and IMAP"
        page.wait_for_timeout(3000)
        _u2 = _url(page)
        log(f"  [nav] after forwarding click, url={_u2[:100]}, fwd_clicked={_fwd_clicked}")

        # ✅ KEY FIX: If we landed on /forwarding or /popimap after clicking the
        # menu item, that IS success — even if the panel shows "Sign in" button.
        # The Sign in prompt is handled later in the toggle section.
        if _fwd_clicked and ("options/mail" in _u2 or "popimap" in _u2):
            log("  [nav] ✅ on IMAP settings page after menu click (may show Sign in)")
            state = _page_state(page)
            log(f"  [nav] state={state}, url={_u2[:100]}")
            return state

        if _has_imap_content(page, 8000):
            log("  [nav] ✅ IMAP content via gear-click menu")
            state = _page_state(page)
            log(f"  [nav] state={state}, url={_url(page)[:100]}")
            return state
        log(f"  [nav] gear-click: IMAP content still not found, txt={_txt(page)[:200]}")

    state = _page_state(page)
    log(f"  [nav] state={state}, url={_url(page)[:100]}")
    return state


def _has_imap_content(page, timeout_ms=10000) -> bool:
    """
    v9.37 FIX: redesigned to prevent false-positives caused by page navigation
    during long wait_for_selector() calls.

    Root cause of previous bug:
      wait_for_selector(timeout=12000) blocked for 12s. During those 12s, the
      MSAL session refresh redirected the page. Then the fallback radio/switch
      check found inputs on the NEW (wrong) page → returned True (false positive).

    Fix strategy:
      1. Use short (3s max) wait intervals, re-check URL between each check.
      2. radio/switch fallback now requires IMAP-specific text to be present.
      3. If URL drifts away from outlook.live.com during any check, return False.
    """
    import time as _t

    def _still_on_outlook() -> bool:
        return "outlook.live.com" in _url(page)

    if not _still_on_outlook():
        return False

    # ── Level 1: IMAP-specific aria-label / data-testid (most reliable) ──
    try:
        page.wait_for_selector(
            '[data-testid*="imap" i],[data-testid*="popimap" i],'
            '[aria-label="Enable IMAP"],[aria-label="Disable IMAP"],'
            '[aria-label="Enable POP"],[aria-label="Disable POP"]',
            timeout=min(timeout_ms, 3000)
        )
        if _still_on_outlook():
            return True
    except Exception:
        pass

    # ── Level 2: Specific IMAP settings text (instant, no wait) ──────────
    IMAP_PANEL_KWS = (
        "imap access", "enable imap", "pop and imap", "pop access",
        "imap settings", "pop/imap", "let devices and apps use imap",
        "pop access and forwarding",
        "sign in and verify", "verify your account to forward",
        "sync to your devices",
    )
    txt = _txt(page).lower()
    if _still_on_outlook() and any(k in txt for k in IMAP_PANEL_KWS):
        return True

    # ── Level 3: radio/switch with IMAP/POP context ────────────────────────
    # CRITICAL: only count radio/switch if IMAP or POP specific text is nearby.
    # Generic radio buttons exist in the Outlook inbox toolbar — they must NOT match.
    u = _url(page)
    if _still_on_outlook() and ("popimap" in u or "options/mail" in u):
        try:
            page.wait_for_selector(
                'input[type="radio"],[role="radio"],[role="switch"]',
                timeout=min(timeout_ms, 3000)
            )
            # Re-check URL (page may have navigated during wait)
            if not _still_on_outlook():
                return False
            # Require IMAP/POP specific text in page context
            txt2 = _txt(page).lower()
            has_imap_ctx = (
                ("imap" in txt2 and ("enable" in txt2 or "disable" in txt2 or "access" in txt2)) or
                ("pop" in txt2 and ("enable" in txt2 or "disable" in txt2 or "access" in txt2))
            )
            if has_imap_ctx:
                return True
        except Exception:
            pass

    # ── Level 4: wait_for_function (short poll, re-check URL after) ───────
    if _still_on_outlook() and timeout_ms > 3000:
        try:
            page.wait_for_function(
                """() => {
                    var t = (document.body && document.body.innerText || '').toLowerCase();
                    return t.indexOf('imap access') >= 0 || t.indexOf('enable imap') >= 0 ||
                           t.indexOf('pop and imap') >= 0 || t.indexOf('pop access') >= 0;
                }""",
                timeout=min(timeout_ms - 3000, 4000)
            )
            if _still_on_outlook():
                return True
        except Exception:
            pass

    return False



def _click_fwd_imap_menu(page) -> bool:
    """
    Click the "Forwarding and IMAP" menu item inside the Outlook Settings dialog.
    When navigating to /mail/options/mail/popimap, Outlook opens the Settings
    dialog to the Mail settings MENU (showing items like Layout, Compose, etc.).
    The actual IMAP/POP toggle panel only appears AFTER clicking "Forwarding and IMAP".
    Returns True if a click was sent, False if panel already open.
    """
    # v9.37 FIX: Only skip fwd-menu click if IMAP/POP-SPECIFIC radio inputs are present.
    # Generic radio/checkbox elements exist in the Outlook inbox toolbar and must NOT
    # be mistaken for the IMAP/POP toggle panel being open.
    txt_now = _txt(page).lower()
    _imap_pop_specific = (
        ("imap" in txt_now and ("enable" in txt_now or "disable" in txt_now or "access" in txt_now)) or
        ("pop" in txt_now and ("enable" in txt_now or "disable" in txt_now or "access" in txt_now))
    )
    if _imap_pop_specific:
        try:
            page.wait_for_selector(
                'input[type="radio"],[role="radio"],[role="switch"],'
                'input[type="checkbox"]',
                timeout=1500
            )
            log("  [fwd-menu] IMAP/POP toggle inputs visible — panel already open")
            return False
        except Exception:
            pass

    # Try Playwright exact-text match (most reliable)
    _labels = [
        "Forwarding and IMAP", "POP and IMAP", "Sync Email",
        "Forwarding", "POP / IMAP", "Email sync",
    ]
    for _lbl in _labels:
        try:
            _loc = page.get_by_text(_lbl, exact=True).first
            if _loc.is_visible(timeout=2000):
                _loc.click()
                log(f"  [fwd-menu] clicked '{_lbl}' via get_by_text(exact)")
                return True
        except Exception:
            continue

    # JS fallback: match on direct text nodes only (avoids parent-container mismatch)
    _js = page.evaluate("""
        () => {
            function dt(el) {
                return Array.from(el.childNodes)
                    .filter(function(n){return n.nodeType===3;})
                    .map(function(n){return n.textContent.trim();})
                    .join('').trim().toLowerCase();
            }
            var sels = 'button,[role=option],[role=menuitem],[role=treeitem],li>a,a,li';
            var kws = ['forwarding and imap','pop and imap','sync email','forwarding','pop / imap'];
            for (var el of document.querySelectorAll(sels)) {
                var t = dt(el);
                if (!t) continue;
                for (var i=0;i<kws.length;i++) {
                    if (t===kws[i] || t.indexOf(kws[i])===0) {
                        el.click();
                        return 'js:'+el.textContent.trim().slice(0,40);
                    }
                }
            }
            return 'not-found';
        }
    """)
    log(f"  [fwd-menu] JS result: {_js}")
    return _js != "not-found"

def _toggle_imap(page) -> str:
    """
    v9.37 FIX: Enable IMAP toggle on the Outlook settings page.

    Key fix: Filter out LEFT PANEL navigation menu items.
    Previously [aria-label*="IMAP" i] matched the left panel "Forwarding and IMAP"
    menu item (a BUTTON element) and clicked it instead of the actual IMAP radio.
    Now navigation roles (button/link/option/treeitem) are excluded from attr search.
    """
    return page.evaluate("""() => {
        // ── Strategy 1: radio/checkbox with IMAP text in close parent ──────
        var inputs = Array.from(document.querySelectorAll(
            'input[type="radio"],input[type="checkbox"],[role="radio"],[role="switch"],[role="checkbox"]'));
        for (var el of inputs) {
            var par = el.closest('li') || el.closest('label') || el.closest('div') || el.parentElement;
            var txt = (par ? par.textContent : "").toLowerCase();
            if (txt.indexOf("imap") < 0) continue;
            // Exclude elements where the parent ONLY mentions imap in nav context
            var parRole = par ? (par.getAttribute("role") || par.tagName.toLowerCase()) : "";
            if (parRole === "navigation" || parRole === "menu" || parRole === "menubar") continue;
            var chk = el.getAttribute("aria-checked") || (el.checked !== undefined ? String(el.checked) : "false");
            if (chk === "true" || el.checked) return "already-on";
            el.click();
            return "radio-clicked:" + (el.id || el.name || el.value || "?");
        }
        // ── Strategy 2: label containing "enable imap" ─────────────────────
        for (var lbl of Array.from(document.querySelectorAll("label"))) {
            var lt = lbl.textContent.toLowerCase();
            if (lt.indexOf("enable imap") >= 0) {
                var inp = lbl.control || document.getElementById(lbl.htmlFor);
                if (inp) { inp.click(); return "label-inp"; }
                lbl.click(); return "label";
            }
        }
        // ── Strategy 3: aria-label with IMAP — STRICT: only real toggle elements ────
        // CRITICAL: [aria-label*="IMAP" i] can match container divs like
        // "MailForwarding and IMAPis loaded" which are NOT toggle controls.
        // Fix: only accept elements that are actual toggle inputs:
        //   - input[type="radio"|"checkbox"] — native toggle
        //   - [role="radio"|"switch"|"checkbox"] — ARIA toggle
        //   - elements with aria-checked attribute — toggle state indicator
        // This EXCLUDES: buttons, divs, sections, spans, nav items, containers.
        var TOGGLE_TAGS = ["INPUT", "SELECT"];
        var TOGGLE_ROLES = ["radio", "switch", "checkbox"];
        var byAttr = Array.from(document.querySelectorAll('[aria-label*="IMAP" i],[data-testid*="imap" i]'));
        for (var el of byAttr) {
            var role = (el.getAttribute("role") || "").toLowerCase();
            var tag = el.tagName;
            var hasAriaChecked = el.hasAttribute("aria-checked");
            // ONLY accept if it's a genuine toggle control
            var isInput = (tag === "INPUT" && (el.type === "radio" || el.type === "checkbox" || el.type === "range"));
            var isAriaToggle = TOGGLE_ROLES.indexOf(role) >= 0;
            var hasToggleState = hasAriaChecked;
            if (!isInput && !isAriaToggle && !hasToggleState) continue;
            var chk = el.getAttribute("aria-checked") || (el.checked !== undefined ? String(el.checked) : "false");
            if (chk === "true" || el.checked) return "attr-already-on";
            el.click();
            return "attr-clicked-strict:" + el.getAttribute("aria-label");
        }
        // ── Strategy 4: any visible [role="switch"] ──────────────────────────
        var sw = Array.from(document.querySelectorAll('[role="switch"]'));
        for (var s of sw) {
            if (s.getAttribute("aria-checked") !== "true") { s.click(); return "switch-clicked"; }
            return "switch-already-on";
        }
        return "not-found";
    }""")


def _toggle_pop(page) -> str:
    """Enable POP access on the Outlook POP/IMAP settings page.
    Returns: 'already-on' | 'radio-clicked:X' | 'attr-clicked:X' | 'not-found'
    Outlook POP settings sit on the same /popimap page as IMAP.
    Radio layout: 'Enable POP for all messages' / 'Disable POP'
    """
    return page.evaluate("""() => {
        // Try radio/checkbox inputs whose parent mentions 'pop'
        var inputs = Array.from(document.querySelectorAll(
            'input[type="radio"],input[type="checkbox"],[role="radio"],[role="switch"],[role="checkbox"]'));
        for (var el of inputs) {
            var par = el.closest('li') || el.closest('label') || el.closest('div') || el.parentElement;
            var txt = (par ? par.textContent : '').toLowerCase();
            // must mention POP but NOT be the 'disable pop' option
            if (txt.indexOf('pop') < 0) continue;
            if (txt.indexOf('disable pop') >= 0 || txt.indexOf('disable') >= 0) continue;
            var chk = el.getAttribute('aria-checked') || (el.checked !== undefined ? String(el.checked) : 'false');
            if (chk === 'true' || el.checked) return 'pop-already-on';
            el.click();
            return 'pop-radio:' + (el.id || el.name || el.value || '?');
        }
        // Try aria-label with POP
        var byAttr = Array.from(document.querySelectorAll('[aria-label*="POP" i],[data-testid*="pop" i]'));
        for (var el of byAttr) {
            if ((el.getAttribute('aria-label') || '').toLowerCase().indexOf('disable') >= 0) continue;
            var chk = el.getAttribute('aria-checked') || (el.checked !== undefined ? String(el.checked) : 'false');
            if (chk === 'true' || el.checked) return 'pop-attr-already-on';
            el.click();
            return 'pop-attr:' + el.getAttribute('aria-label');
        }
        // Label containing 'enable pop'
        for (var lbl of Array.from(document.querySelectorAll('label'))) {
            var lt = lbl.textContent.toLowerCase();
            if (lt.indexOf('enable pop') >= 0 || (lt.indexOf('pop') >= 0 && lt.indexOf('disable') < 0)) {
                var inp = lbl.control || document.getElementById(lbl.htmlFor);
                if (inp && !inp.checked) { inp.click(); return 'pop-label'; }
                if (inp && inp.checked) return 'pop-already-on';
            }
        }
        return 'not-found';
    }""")


def _save_settings(page) -> bool:
    for sel in [
        'button:has-text("Save")',
        'button[type="submit"]',
        'input[type="submit"]',
        'input[value="Save"]',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500):
                loc.click()
                page.wait_for_timeout(2000)
                log(f"  [save] via {sel}")
                return True
        except Exception:
            continue
    try:
        page.evaluate("""() => {
            Array.from(document.querySelectorAll("button,input[type=submit]")).forEach(b => {
                if (/^Save$/i.test(b.textContent.trim()) || b.value === 'Save') b.click();
            });
        }""")
        page.wait_for_timeout(1500)
        return True
    except Exception:
        pass
    return False


def enable_imap(email, password, account_id=None,
                cookies_json="", fingerprint_json="",
                proxy="", headless=True, xray_relay_inst=None) -> bool:
    label = email.split("@")[0]
    log(f"\n{'='*60}")
    log(f"[enable-imap] v5 start: {email} (id={account_id})")

    # v5.1: Prefer ISP/tp-in proxy for the ENTIRE session (login + settings navigation).
    # CF VLESS (XrayRelay force_dynamic) works for login.live.com but stalls the
    # Outlook SPA lazy-loads that render the IMAP/POP settings panel content.
    # ISP/tp-in ports (10910-10914, 10851+) fully render all SPA resources.
    # XrayRelay is only needed for registration IP consistency — not for IMAP/POP enabling.
    _session_isp = _find_isp_proxy()
    if _session_isp and _session_isp != proxy:
        _effective_proxy = _session_isp
        log(f"  [proxy] ISP override (CF→ISP): {_effective_proxy} (main={proxy[:40]})")
    else:
        _effective_proxy = proxy

    log(f"  [proxy] effective proxy: {_effective_proxy}")
    p, browser = launch_browser(proxy=_effective_proxy, headless=headless)
    try:
        ctx_kw = dict(
            locale="en-US",
            timezone_id="America/Los_Angeles",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        if fingerprint_json:
            try:
                fp = json.loads(fingerprint_json)
                for k in ("user_agent", "locale", "timezone_id", "viewport", "screen"):
                    if fp.get(k):
                        ctx_kw[k] = fp[k]
            except Exception:
                pass

        # v5: create context WITHOUT storage_state first.
        # We always do a fresh login so restored MSAL tokens are irrelevant —
        # the SPA will re-initialize from a clean state after login.
        ctx = browser.new_context(**ctx_kw)
        page = ctx.new_page()

        if not password:
            log("  [step0] ❌ no password — cannot enable IMAP (requires fresh login)")
            return False

        # ── Step 0: Fresh login ──────────────────────────────────────────────
        # v5 KEY: Always fresh login. Cookies alone cannot pass IMAP security checks.
        # Microsoft enforces re-authentication when navigating to IMAP settings.
        # v5.1: Browser already uses ISP proxy (_effective_proxy above), so
        # isp_proxy hint = _effective_proxy (no separate context switch needed).
        log(f"  [step0] fresh login (proxy={_effective_proxy[:40]})...")
        _login_ok = _do_fresh_login(page, ctx, email, password, isp_proxy=_effective_proxy)
        _ss(page, "00_after_login", label)
        log(f"  [step0] login_ok={_login_ok}, url={_url(page)[:100]}")

        if not _login_ok and "outlook.live.com" not in _url(page):
            log("  [step0] ❌ login failed completely")
            return False

        # ── Step 1: Navigate to IMAP settings ───────────────────────────────
        log("  [step1] navigating to IMAP settings...")
        state = _nav_to_imap_direct(page)
        _ss(page, "01_nav", label)

        # ── Step 2-N: Handle security cycle ─────────────────────────────────
        # Microsoft IMAP path: reauth → proofs (backup email) → security code → IMAP page
        # Each handled state re-navigates to IMAP URL to continue the cycle.
        _reauth_done   = False
        _proofs_done   = False
        _fresh_retried = False
        _mt_addr       = ""
        _mt_token      = ""

        for _cycle in range(8):
            u = _url(page)
            log(f"  [cycle {_cycle}] state={state}, url={u[:100]}")
            _ss(page, f"c{_cycle}_state", label)

            # ── IMAP page found ──────────────────────────────────────────────
            if state in ("imap", "imap_loading"):
                if _has_imap_content(page, 12000):
                    log(f"  [cycle {_cycle}] ✅ IMAP page confirmed")
                    break
                # Still loading — wait and re-check
                log(f"  [cycle {_cycle}] imap_loading — waiting 8s more...")
                page.wait_for_timeout(8000)
                if _has_imap_content(page, 8000):
                    log(f"  [cycle {_cycle}] ✅ IMAP page loaded")
                    break
                # Not found — re-navigate
                log(f"  [cycle {_cycle}] IMAP content not found despite URL match, re-navigating...")
                state = _nav_to_imap_direct(page)
                continue

            # ── Password re-challenge ────────────────────────────────────────
            if state == "reauth":
                if _reauth_done:
                    log(f"  [cycle {_cycle}] ⚠ reauth already done once — may be wrong password or MFA block")
                    # Try anyway
                _handle_reauth(page, email, password)
                _reauth_done = True
                _ss(page, f"c{_cycle}_reauth_done", label)
                # After reauth, Microsoft may show proofs or redirect to IMAP
                page.wait_for_timeout(3000)
                # Handle possible skip prompts
                for _ in range(4):
                    if not _skip_interrupts(page):
                        break
                    page.wait_for_timeout(1500)
                state = _page_state(page)
                if state == "proofs":
                    log(f"  [cycle {_cycle}] proofs page appeared after reauth — handling in same cycle")
                    # Fall through to proofs handling below by continuing loop
                    continue
                if state not in ("imap", "imap_loading"):
                    log(f"  [cycle {_cycle}] post-reauth state={state}, re-navigating to IMAP...")
                    state = _nav_to_imap_direct(page)
                continue

            # ── Backup email / proofs page ───────────────────────────────────
            if state == "proofs":
                if _proofs_done and _mt_token:
                    # Already added email, may need to fill code on this page
                    log(f"  [cycle {_cycle}] proofs reappeared — trying code fill again")
                    _handle_security_code(page, _mt_token)
                    _ss(page, f"c{_cycle}_code_retry", label)
                else:
                    _added, _mt_addr, _mt_token = _handle_proofs(page)
                    _ss(page, f"c{_cycle}_proofs", label)
                    if _added:
                        log(f"  [cycle {_cycle}] proofs: email added, waiting for code page...")
                        # After adding backup email, Microsoft shows code input page
                        page.wait_for_timeout(3000)
                        _handle_security_code(page, _mt_token)
                        _ss(page, f"c{_cycle}_code", label)
                        _proofs_done = True
                    else:
                        log(f"  [cycle {_cycle}] ⚠ proofs: could not add backup email")
                # Skip any post-proofs prompts
                for _ in range(4):
                    if not _skip_interrupts(page):
                        break
                    page.wait_for_timeout(1500)
                state = _page_state(page)
                if state not in ("imap", "imap_loading"):
                    log(f"  [cycle {_cycle}] post-proofs state={state}, re-navigating to IMAP...")
                    state = _nav_to_imap_direct(page)
                continue

            # ── Login page (session expired / not logged in) ─────────────────
            if state == "login":
                if _fresh_retried:
                    log(f"  [cycle {_cycle}] ❌ login page reappeared after retry — giving up")
                    return False
                log(f"  [cycle {_cycle}] login page — retrying fresh login...")
                _do_fresh_login(page, ctx, email, password, isp_proxy=_effective_proxy)
                _fresh_retried = True
                _ss(page, f"c{_cycle}_relogin", label)
                state = _nav_to_imap_direct(page)
                continue

            # ── Error page ───────────────────────────────────────────────────
            if state == "error":
                if _fresh_retried:
                    log(f"  [cycle {_cycle}] ❌ error page after retry — aborting")
                    return False
                log(f"  [cycle {_cycle}] error/440 — retrying fresh login + IMAP nav...")
                _do_fresh_login(page, ctx, email, password, isp_proxy=_effective_proxy)
                _fresh_retried = True
                _ss(page, f"c{_cycle}_error_relogin", label)
                state = _nav_to_imap_direct(page)
                continue

            # ── Inbox (logged in but not on IMAP page) ───────────────────────
            if state == "inbox":
                log(f"  [cycle {_cycle}] on inbox — re-navigating to IMAP...")
                state = _nav_to_imap_direct(page)
                continue

            # ── Unknown state ─────────────────────────────────────────────────
            log(f"  [cycle {_cycle}] unknown state, txt={_txt(page)[:200]}")
            if _cycle < 3:
                page.wait_for_timeout(5000)
                state = _page_state(page)
                if state == "unknown":
                    state = _nav_to_imap_direct(page)
                continue
            log(f"  [cycle {_cycle}] breaking after repeated unknown state")
            break

        # ── v9.37 FIX: Re-validate URL after cycle exit (page may have navigated) ──
        _post_cycle_url = _url(page)
        log(f"  [post-cycle] url={_post_cycle_url[:100]}")
        if "outlook.live.com" not in _post_cycle_url:
            log("  [post-cycle] ❌ page navigated away during cycle — re-doing full login+nav")
            _login_ok2 = _do_fresh_login(page, ctx, email, password, isp_proxy=_effective_proxy)
            if not _login_ok2:
                log("  [post-cycle] ❌ re-login failed — aborting")
                return False
            state2 = _nav_to_imap_direct(page)
            if state2 not in ("imap", "imap_loading"):
                log(f"  [post-cycle] ❌ still not on IMAP page after re-login: state={state2}")
                return False
            page.wait_for_timeout(3000)
        elif "popimap" not in _post_cycle_url and "options/mail" not in _post_cycle_url:
            log("  [post-cycle] not on IMAP URL — re-navigating...")
            state2 = _nav_to_imap_direct(page)
            page.wait_for_timeout(3000)

        _ss(page, "05_pre_toggle", label)

        # ── Step N+0.5: Click "Forwarding and IMAP" menu item ──────────────
        # Settings dialog opens to MENU (URL=/popimap, "Forwarding and IMAP" visible
        # as a link). Must click it to open the actual IMAP/POP toggle panel.
        log("  [fwd-panel] clicking Forwarding-and-IMAP menu item if needed...")
        _fwd_panel_clicked = _click_fwd_imap_menu(page)
        if _fwd_panel_clicked:
            log("  [fwd-panel] clicked — waiting 4s for panel to render...")
            page.wait_for_timeout(4000)
            _ss(page, "05b_after_fwd_click", label)
        else:
            log("  [fwd-panel] panel already open or not needed")

        # ── Handle "Sign in" button in IMAP panel ───────────────────────
        # After clicking "Forwarding and IMAP", Outlook may show a Sign-in prompt:
        # "Sign in and verify your account to forward your email or sync to your devices."
        # Must click Sign in + handle login before IMAP/POP toggles appear.
        log("  [fwd-panel] checking for Sign in prompt...")
        _signin_handled = _handle_fwd_signin(page, email, password)
        if _signin_handled:
            log("  [fwd-panel] Sign in handled — waiting 8s for page to settle...")
            page.wait_for_timeout(8000)
            _ss(page, "05c_after_signin", label)
            u_after = _url(page)
            log(f"  [fwd-panel] post-signin url={u_after[:100]}")
            # Skip any post-login prompts
            for _ in range(4):
                if not _skip_interrupts(page):
                    break
                page.wait_for_timeout(1500)
            # Navigate back to IMAP settings if redirected away
            if "options/mail" not in _url(page) and "popimap" not in _url(page):
                log("  [fwd-panel] not on IMAP page after signin — re-navigating...")
                state = _nav_to_imap_direct(page)
                page.wait_for_timeout(3000)
            # Try to open the IMAP panel (now unlocked)
            _click_fwd_imap_menu(page)
            page.wait_for_timeout(5000)
            _ss(page, "05d_imap_unlocked", label)

        # ── Step N+1: Check IMAP content one more time ───────────────────────
        if not _has_imap_content(page, 8000):
            log("  [toggle] ❌ IMAP content not found after all cycles")
            log(f"  [toggle] url={_url(page)[:100]}")
            log(f"  [toggle] text={_txt(page)[:500]}")
            return False

        # ── Step N+2: Toggle IMAP on ─────────────────────────────────────────
        page.wait_for_timeout(2000)
        res = _toggle_imap(page)
        log(f"  [imap-toggle] result: {res}")
        _ss(page, "06_toggle_imap", label)

        if res == "not-found":
            page.wait_for_timeout(5000)
            res = _toggle_imap(page)
            log(f"  [imap-toggle] retry: {res}")
            _ss(page, "06b_toggle_imap2", label)

        if res == "not-found":
            log("  [imap-toggle] ❌ IMAP toggle not found")
            log(f"  [imap-toggle] url={_url(page)[:100]}")
            log(f"  [imap-toggle] text={_txt(page)[:500]}")
            return False

        # ── Step N+3: Toggle POP on (same /popimap page) ─────────────────────
        # POP and IMAP settings coexist on the same Outlook settings page.
        # Enable POP for all messages alongside IMAP.
        page.wait_for_timeout(600)
        pop_res = _toggle_pop(page)
        log(f"  [pop-toggle] result: {pop_res}")
        _ss(page, "06c_toggle_pop", label)

        if pop_res == "not-found":
            page.wait_for_timeout(3000)
            pop_res = _toggle_pop(page)
            log(f"  [pop-toggle] retry: {pop_res}")

        # ── Step N+4: Save once for both IMAP + POP ──────────────────────────
        _imap_changed = "already" not in res
        _pop_changed  = "already" not in pop_res
        if _imap_changed or _pop_changed:
            page.wait_for_timeout(800)
            _save_settings(page)
        _ss(page, "07_save", label)

        log(f"  [enable-imap+pop] ✅ SUCCESS imap={res} pop={pop_res}")
        if account_id:
            db_tag_imap_enabled(account_id)  # tags both imap_enabled + pop_enabled
        return True

    except Exception as e:
        import traceback
        log(f"  [enable-imap] EXCEPTION: {e}")
        log(traceback.format_exc())
        return False
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
        if xray_relay_inst:
            try:
                xray_relay_inst.stop()
                log("  [proxy] XrayRelay stopped")
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description="Enable IMAP for Outlook v5")
    ap.add_argument("--email",      default="")
    ap.add_argument("--password",   default="")
    ap.add_argument("--account-id", type=int, default=0)
    ap.add_argument("--proxy",      default="")
    ap.add_argument("--headless",   default="true")
    args = ap.parse_args()

    headless = args.headless.lower() not in ("false", "0", "no")
    email, password, acc_id = args.email, args.password, args.account_id
    cookies_json = fingerprint_json = ""
    acc: dict = {}

    if acc_id:
        acc = db_get_account(acc_id)
        email            = acc["email"]
        password         = acc.get("password") or ""
        cookies_json     = (acc.get("cookies_json") or "").strip()
        fingerprint_json = (acc.get("fingerprint_json") or "").strip()
        log(f"[cli] DB account: id={acc_id} email={email}")

    if not email:
        ap.error("must provide --email or --account-id")

    xray_relay_inst = None
    exit_ip = (acc.get("exit_ip") or "") if acc_id else ""
    try:
        proxy_url, xray_relay_inst = _setup_proxy(
            exit_ip=exit_ip, manual_proxy=args.proxy)
        if not args.proxy:
            args.proxy = proxy_url
        log(f"[cli] proxy: {args.proxy}")
    except RuntimeError as e:
        log(f"[cli] ❌ {e}")
        sys.exit(2)

    ok = enable_imap(
        email=email, password=password, account_id=acc_id or None,
        cookies_json=cookies_json, fingerprint_json=fingerprint_json,
        proxy=args.proxy, headless=headless, xray_relay_inst=xray_relay_inst)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
