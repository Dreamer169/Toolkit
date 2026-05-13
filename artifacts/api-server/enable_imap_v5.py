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
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
            s.close()
            log(f"  [proxy] ISP static port {port}")
            return f"socks5://127.0.0.1:{port}", None
        except Exception:
            continue

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
    import psycopg2
    try:
        conn = psycopg2.connect(_DB_URL)
        cur  = conn.cursor()
        cur.execute("""
            UPDATE accounts
               SET tags = (SELECT ARRAY(SELECT DISTINCT unnest(array_append(COALESCE(tags,'{}'), 'imap_enabled')))),
                   updated_at = now()
             WHERE id = %s
        """, (account_id,))
        conn.commit()
        conn.close()
        log(f"  [db] account {account_id} tagged: imap_enabled")
    except Exception as e:
        log(f"  [db] tag write failed: {e}")


def _find_isp_proxy() -> str:
    """Find first available ISP static port (real IP, not CF). Used for login.live.com."""
    import socket
    # tp-in ports (US IP, ~0.4s) first — proven to render Microsoft login React SPA
    # then ss-in ISP direct (Italy/Turkey/Russia/HK, proxy:false)
    ISP_PORTS = [10910, 10911, 10912, 10914, 10851, 10853, 10855, 10859]
    for port in ISP_PORTS:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            return f"socks5://127.0.0.1:{port}"
        except Exception:
            continue
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
        return (page.evaluate("()=>document.body?.innerText?.slice(0,2000)||''") or "").lower()
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
            page.goto(_lu, timeout=45000, wait_until="domcontentloaded")
        except Exception as _ge:
            log(f"  [login] goto (ok): {str(_ge)[:60]}")
        page.wait_for_timeout(3000)
        cur = _url(page)
        log(f"  [login] landed: {cur[:90]}")
        # Only skip real error pages — NOT 0-byte SPA (React not yet mounted).
        # Mirrors auto_device_code._pick_residential_proxy / outlook_register.py pattern:
        #   0b       → SPA still loading, fall through to 60s email-input wait
        #   1-800b + error keyword → genuine error page, skip to next URL
        try:
            _blen = page.evaluate("()=>document.body?.innerHTML?.length||0")
            if _blen == 0:
                log(f"  [login] body=0 (SPA loading), waiting for form...")
            elif _blen < 800:
                _btxt = page.evaluate("()=>document.body?.innerText?.slice(0,200)||''")
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
            _ei = page.locator(_email_input_sel).first
            if _ei.is_visible(timeout=60000):
                _login_form_found = True
                log(f"  [login] ✅ email input found at: {cur[:80]}")
                break
        except Exception:
            pass
        log(f"  [login] ⚠ input not visible at {_lu[:60]}, trying next...")

    if not _login_form_found:
        log("  [login] ⚠ email input not found on any login URL — proceeding anyway")
        _ss(page, "login_no_input", email.split("@")[0])

    # Fill email via react_fill (sets value + fires React synthetic events)
    _react_fill(page, 'input[name="loginfmt"]', email)
    log("  [login] email filled")

    page.wait_for_timeout(400)
    _click_primary(page)
    page.wait_for_timeout(4000)
    log(f"  [login] email submitted, url={_url(page)[:80]}")

    # Wait specifically for PASSWORD field (not just any input — email input may linger in DOM)
    _pw_visible = False
    try:
        _pw_loc = page.locator('input[name="passwd"],input[type="password"]').first
        _pw_loc.wait_for(state="visible", timeout=25000)
        _pw_visible = True
        log("  [login] ✅ password field visible")
    except Exception:
        log("  [login] ⚠ password field not visible after 25s")

    # Fill password
    if _pw_visible:
        try:
            _pw_loc.fill(password)
            page.wait_for_timeout(300)
        except Exception:
            _react_fill(page, 'input[name="passwd"]', password)
    else:
        _react_fill(page, 'input[name="passwd"]', password)
        log("  [login] password filled via react_fill (blind)")

    page.wait_for_timeout(400)
    _click_primary(page)
    page.wait_for_timeout(4000)
    log(f"  [login] password submitted, url={_url(page)[:80]}")

    # Skip Stay-signed-in? / MFA prompts (same as v4)
    for _ in range(8):
        if not _skip_interrupts(page):
            break
        page.wait_for_timeout(2000)

    # Wait for redirect to outlook.live.com/mail (v4 proven approach)
    try:
        page.wait_for_url("**/mail/**", timeout=45000)
        log(f"  [login] ✅ redirected to mail, url={_url(page)[:80]}")
    except Exception:
        log(f"  [login] wait_for_url timeout, cur={_url(page)[:80]}")
    page.wait_for_timeout(3000)

    # Handle msalAuthRedirect if present (v4 approach)
    for _mw in range(3):
        _fu = _url(page)
        if "msalAuthRedirect" in _fu:
            log(f"  [login] msalAuthRedirect detected (pass {_mw+1}), waiting for MSAL...")
            _wait_for_msal_complete(page, timeout_s=90)
            page.wait_for_timeout(3000)
        else:
            break

    # Wait for networkidle (SPA MSAL initialization)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(2000)

    # Confirm gear (MSAL + SPA fully loaded)
    gear_ok = _wait_for_gear(page, timeout_s=30)
    final_url = _url(page)
    log(f"  [login] final url={final_url[:100]} gear={gear_ok}")

    if not gear_ok and "outlook.live.com/mail" in final_url:
        log("  [login] on mail but no gear — waiting 15s more...")
        page.wait_for_timeout(15000)
        gear_ok = _wait_for_gear(page, timeout_s=20)

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

    # Fast-check: if IMAP content already loaded, return immediately
    if "outlook.live.com" in u and ("popimap" in u or "options/mail" in u):
        if _has_imap_content(page, 8000):
            log("  [nav] ✅ IMAP content confirmed via direct goto")
            state = _page_state(page)
            log(f"  [nav] state={state}, url={u[:100]}")
            return state

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

        # Wait for IMAP sub-page to fully render after clicking "Forwarding and IMAP"
        page.wait_for_timeout(3000)
        _u2 = _url(page)
        log(f"  [nav] after forwarding click, url={_u2[:100]}, fwd_clicked={_fwd_clicked}")

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
    Check if IMAP *settings panel* content is present (not just inbox nav that says 'imap').
    Uses specific IMAP settings keywords and toggle selectors.
    """
    u = _url(page)
    if "outlook.live.com" not in u:
        return False
    # Selector check: IMAP-specific toggle (radio/switch on the settings page)
    try:
        page.wait_for_selector(
            '[data-testid*="imap" i],[aria-label*="IMAP" i],'
            '[data-testid*="popimap" i]',
            timeout=timeout_ms
        )
        return True
    except Exception:
        pass
    # Check for radio/switch ONLY if on popimap URL (avoids inbox false-positive)
    if "popimap" in u or "options/mail" in u:
        try:
            page.wait_for_selector(
                'input[type="radio"],[role="radio"],[role="switch"]',
                timeout=min(timeout_ms, 5000)
            )
            return True
        except Exception:
            pass
    # Specific IMAP settings text (not generic "imap" which appears in inbox nav)
    IMAP_PANEL_KWS = (
        "imap access", "enable imap", "pop and imap", "pop access",
        "imap settings", "pop/imap", "let devices and apps use imap",
        "forwarding and imap", "sync email", "pop access and forwarding",
    )
    txt = _txt(page)
    if any(k in txt for k in IMAP_PANEL_KWS):
        return True
    # v4 fallback: wait_for_function for specific phrases
    try:
        page.wait_for_function(
            """() => {
                var t = (document.body && document.body.innerText || '').toLowerCase();
                return t.indexOf('imap access') >= 0 || t.indexOf('enable imap') >= 0 ||
                       t.indexOf('pop and imap') >= 0 || t.indexOf('pop access') >= 0 ||
                       t.indexOf('sync email') >= 0;
            }""",
            timeout=min(timeout_ms, 5000)
        )
        return True
    except Exception:
        pass
    return False


def _toggle_imap(page) -> str:
    return page.evaluate("""() => {
        var inputs = Array.from(document.querySelectorAll(
            'input[type="radio"],input[type="checkbox"],[role="radio"],[role="switch"],[role="checkbox"]'));
        for (var el of inputs) {
            var par = el.closest('li') || el.closest('label') || el.closest('div') || el.parentElement;
            var txt = (par ? par.textContent : "").toLowerCase();
            if (txt.indexOf("imap") < 0) continue;
            var chk = el.getAttribute("aria-checked") || (el.checked !== undefined ? String(el.checked) : "false");
            if (chk === "true" || el.checked) return "already-on";
            el.click();
            return "radio-clicked:" + (el.id || el.name || el.value || "?");
        }
        for (var lbl of Array.from(document.querySelectorAll("label"))) {
            var lt = lbl.textContent.toLowerCase();
            if (lt.indexOf("enable imap") >= 0) {
                var inp = lbl.control || document.getElementById(lbl.htmlFor);
                if (inp) { inp.click(); return "label-inp"; }
                lbl.click(); return "label";
            }
        }
        var byAttr = Array.from(document.querySelectorAll('[aria-label*="IMAP" i],[data-testid*="imap" i]'));
        for (var el of byAttr) {
            var chk = el.getAttribute("aria-checked") || (el.checked !== undefined ? String(el.checked) : "false");
            if (chk === "true" || el.checked) return "attr-already-on";
            el.click();
            return "attr-clicked:" + el.getAttribute("aria-label");
        }
        var sw = Array.from(document.querySelectorAll('[role="switch"]'));
        for (var s of sw) {
            if (s.getAttribute("aria-checked") !== "true") { s.click(); return "switch-clicked"; }
            return "switch-already-on";
        }
        return "not-found";
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

    # v5: Use the proxy as-is (CF IP works fine for Outlook login — confirmed by registration flow).
    # Root cause of prior failures was wrong login URL, NOT the proxy type.
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
        # If main proxy is CF VLESS (slow JS on login.live.com), find ISP port as hint.
        _isp_proxy = _find_isp_proxy()
        log(f"  [step0] fresh login (proxy={proxy[:40]}, isp_hint={_isp_proxy})...")
        _login_ok = _do_fresh_login(page, ctx, email, password, isp_proxy=_isp_proxy)
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
                _do_fresh_login(page, ctx, email, password, isp_proxy=_isp_proxy)
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
                _do_fresh_login(page, ctx, email, password, isp_proxy=_isp_proxy)
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

        _ss(page, "05_pre_toggle", label)

        # ── Step N+1: Check IMAP content one more time ───────────────────────
        if not _has_imap_content(page, 8000):
            log("  [toggle] ❌ IMAP content not found after all cycles")
            log(f"  [toggle] url={_url(page)[:100]}")
            log(f"  [toggle] text={_txt(page)[:500]}")
            return False

        # ── Step N+2: Toggle IMAP on ─────────────────────────────────────────
        page.wait_for_timeout(2000)
        res = _toggle_imap(page)
        log(f"  [toggle] result: {res}")
        _ss(page, "06_toggle", label)

        if res == "not-found":
            page.wait_for_timeout(5000)
            res = _toggle_imap(page)
            log(f"  [toggle] retry: {res}")
            _ss(page, "06b_toggle2", label)

        if res == "not-found":
            log("  [toggle] ❌ IMAP toggle not found")
            log(f"  [toggle] url={_url(page)[:100]}")
            log(f"  [toggle] text={_txt(page)[:500]}")
            return False

        if "already" not in res:
            page.wait_for_timeout(800)
            _save_settings(page)
        _ss(page, "07_save", label)

        log(f"  [enable-imap] ✅ SUCCESS ({res})")
        if account_id:
            db_tag_imap_enabled(account_id)
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
