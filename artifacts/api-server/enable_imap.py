#!/usr/bin/env python3
"""
enable_imap.py v4
Fixes vs v3:
  1. _setup_proxy: XrayRelay result port in CF in-socks range (10820-10829) → skip, use ISP ports
     ISP-only static ports: 10851,10853,10855,10857,10859,10870-10889,10910+
     严禁直连 — raise RuntimeError if all proxies fail.
  2. Fresh login re-enabled: login via Pool B ISP proxy (non-CF exit) is feasible.
     Login detection timeout: 12s per URL (was 3s — too short through proxy).
     Cookies injected as backup if fresh login page not found.
  3. Cycle: detect chrome-error://chromewebdata/ (proxy/IP failure) → try inbox warmup first.
  4. Error 440: try inbox warmup → MSAL re-complete → re-nav IMAP (not just classic OWA).
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


# ── CF in-socks port range (exit via CF VLESS — causes HTTP 417 on IMAP URL) ──
_CF_INSOCKS_PORTS = set(range(10820, 10830))  # 10820-10829 inclusive

def _is_isp_port(port: int) -> bool:
    """Returns True if port exits via real ISP IP (not CF VLESS in-socks)."""
    return port not in _CF_INSOCKS_PORTS


def _setup_proxy(exit_ip: str = "", manual_proxy: str = "") -> tuple:
    """
    Pick a working SOCKS5 proxy with ISP exit (not CF in-socks).
    Priority: manual_proxy > XrayRelay(exit_ip) if ISP port > ISP static ports > CF pool XrayRelay
    严禁直连 — raise RuntimeError if all proxies fail.
    """
    if manual_proxy:
        log(f"  [proxy] manual: {manual_proxy}")
        return manual_proxy, None

    import socket
    xray_inst = None

    # ISP-only static ports (exit via real ISP IP, not CF VLESS)
    # 10851-10859: ss-in (ISP: Italy/Turkey/Russia/HK/Fourplex/M247/Datacamp/Greenhost)
    # 10870-10889: additional ss-in ISP exits
    # 10910-10916: tinyproxy pool
    ISP_STATIC_PORTS = [
        10857, 10859, 10853, 10855, 10851,
        10870, 10871, 10872, 10873, 10874, 10875,
        10876, 10877, 10878, 10879, 10880,
        10910, 10911, 10912, 10913, 10914, 10915, 10916,
    ]

    # Try XrayRelay with force_dynamic=True — creates dedicated VLESS tunnel through CF exit_ip.
    # CRITICAL: force_dynamic=True ensures exit IP matches registration IP (avoids MSAL Error 440).
    # force_dynamic=False falls back to random static port and IGNORES exit_ip entirely.
    if exit_ip:
        try:
            from xray_relay import XrayRelay as _XR
            xray_inst = _XR(exit_ip, force_dynamic=True)
            if xray_inst.start(timeout=15.0):
                url = xray_inst.socks5_url
                log(f"  [proxy] XrayRelay force_dynamic exit_ip={exit_ip} → port {xray_inst.socks_port} → {url}")
                return url, xray_inst
            else:
                log(f"  [proxy] XrayRelay force_dynamic({exit_ip}) start failed, trying static ISP ports")
                xray_inst = None
        except Exception as e:
            log(f"  [proxy] XrayRelay force_dynamic error: {e}, trying static ISP ports")
            xray_inst = None

    # Fallback: ISP-only static ports (real ISP exit, no CF VLESS — only when no exit_ip)
    for port in ISP_STATIC_PORTS:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
            s.close()
            log(f"  [proxy] ISP static port {port}")
            return f"socks5://127.0.0.1:{port}", None
        except Exception:
            continue

    # CF pool: force_dynamic=True with pool IPs (maintains CF exit for accounts without exit_ip)
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
                    log(f"  [proxy] CF pool force_dynamic IP={_ip} → port {xray_inst.socks_port} → {url}")
                    return url, xray_inst
                else:
                    xray_inst = None
    except Exception as e:
        log(f"  [proxy] CF pool error: {e}")

    raise RuntimeError("❌ 所有代理均失败，严禁直连，中止。请检查 XrayRelay / CF pool / ISP 端口状态。")


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
    _FULL_CHROME = "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome"
    _exec = _FULL_CHROME if os.path.exists(_FULL_CHROME) else None
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


IMAP_URL         = "https://outlook.live.com/mail/options/mail/popimap"
INBOX_URL        = "https://outlook.live.com/mail/inbox"
CLASSIC_IMAP_URL = "https://outlook.live.com/mail/options/mail/popimap"  # same as IMAP_URL (new Outlook has no classic OWA)
LOGIN_URL        = "https://login.live.com/login.srf?wa=wsignin1.0"


def _ss(page, tag: str, label: str):
    safe = label.replace("/","_").replace("@","_at_")
    path = f"/tmp/imap4_{tag}_{safe}.png"
    try:
        page.screenshot(path=path)
        log(f"  [ss] {path}")
    except Exception:
        pass

def _txt(page) -> str:
    try:
        return (page.evaluate("()=>document.body?.innerText?.slice(0,1000)||''") or "").lower()
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
    for sel in [
        'button:has-text("Skip for now")', 'button:has-text("Maybe later")',
        'button:has-text("Not now")',       'button:has-text("Skip")',
        'a:has-text("Skip for now")',       'a:has-text("Maybe later")',
        'input[type="submit"][value="No"]', 'button:has-text("No")',
        '[data-testid="secondaryButton"]',  '#idBtn_Back',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1000):
                loc.click()
                page.wait_for_timeout(1500)
                log(f"  [skip] {sel}")
                return True
        except Exception:
            continue
    return False


def _wait_for_msal_complete(page, timeout_s=90) -> bool:
    """
    Wait for new Outlook SPA to fully authenticate + load.
    New Outlook (no /0/) does NOT use msalAuthRedirect= in URL.
    We wait for: gear icon OR popimap/inbox URL OR SPA content loaded.
    Abort early only if "something went wrong" persists for >10s (debounced).
    """
    _gear_sel = '#owaSettingsBtn_container,[aria-label="Settings"],[aria-label="Settings, try new Outlook"]'
    deadline = time.time() + timeout_s
    _error_since = None
    log(f"  [msal] waiting up to {timeout_s}s for MSAL to complete...")
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        u = _url(page)
        # Gear icon = SPA fully loaded
        try:
            if page.locator(_gear_sel).first.is_visible(timeout=500):
                log(f"  [msal] gear visible, SPA loaded OK")
                return True
        except Exception:
            pass
        # New Outlook: URL settled on popimap or inbox (no auth redirect)
        if "popimap" in u or ("outlook.live.com/mail" in u and "msalAuthRedirect" not in u
                               and "bO=" not in u and "login" not in u):
            log(f"  [msal] SPA URL settled (no redirect): {u[:80]}")
            return True
        # Old Outlook: msalAuthRedirect gone = auth done (success or failure)
        if "msalAuthRedirect" in u:
            txt = _txt(page)
            if "something went wrong" in txt:
                # Debounce: only fail after error persists 10s
                if _error_since is None:
                    _error_since = time.time()
                    log(f"  [msal] error page detected, debouncing 10s...")
                elif time.time() - _error_since > 10:
                    log(f"  [msal] error page persisted >10s, MSAL failed. url={u[:80]}")
                    return False
            else:
                _error_since = None  # reset debounce if error cleared
        elif "outlook.live.com" in u:
            # URL changed away from msalAuthRedirect - SPA is navigating
            log(f"  [msal] URL changed: {u[:80]}")
            return False
    log(f"  [msal] timeout after {timeout_s}s")
    return False


def _do_fresh_login(page, ctx, email: str, password: str) -> bool:
    """
    Attempt fresh login via outlook.live.com?prompt=login (through proxy).
    Returns True if successfully redirected to outlook.live.com/mail with MSAL complete.
    v4.1: 60s timeout for form (proxy latency), prompt=login forces login UI.
    """
    if not password:
        log("  [login] no password, skipping fresh login")
        return False

    log("  [login] clearing cookies for fresh login...")
    try:
        ctx.clear_cookies()
    except Exception as e:
        log(f"  [login] clear cookies: {e}")

    # prompt=login forces Microsoft to show login UI even with existing session cookies
    # Use /mail/0/?prompt=login — redirects to login.microsoftonline.com with Outlook SPA
    # client_id (9199bf20) which reliably shows the email input form via proxy.
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
    _login_ok = False
    for _lu in _login_urls:
        try:
            page.goto(_lu, timeout=45000, wait_until="domcontentloaded")
        except Exception:
            pass
        page.wait_for_timeout(3000)
        _cur = _url(page)
        log(f"  [login] navigated to: {_cur[:90]}")
        # v4.1: wait 60s for email input (proxy latency is high through VLESS)
        try:
            _e = page.locator(_email_input_sel).first
            if _e.is_visible(timeout=60000):
                _login_ok = True
                log(f"  [login] email input found at: {_cur[:80]}")
                break
        except Exception:
            pass

    if not _login_ok:
        log("  [login] ⚠ email input not found on any login URL")

    # Fill email
    try:
        _ei = page.locator(_email_input_sel).first
        _ei.wait_for(timeout=25000)
        _react_fill(page, 'input[name="loginfmt"]', email)
        page.wait_for_timeout(700)
        _click_primary(page)
        page.wait_for_timeout(4000)
        log("  [login] ✅ email submitted")
    except Exception as e:
        log(f"  [login] ⚠ email fill: {e}")

    # Fill password
    try:
        _pw = page.locator('input[type="password"],input[name="passwd"]').first
        _pw.wait_for(timeout=20000)
        _pw.fill(password)
        page.wait_for_timeout(700)
        _click_primary(page)
        page.wait_for_timeout(6000)
        log("  [login] ✅ password submitted")
    except Exception as e:
        log(f"  [login] ⚠ password fill: {e}")

    # Skip interrupts (Stay signed in? / MFA prompts)
    for _ in range(8):
        if not _skip_interrupts(page):
            break
        page.wait_for_timeout(2000)

    # Wait for inbox redirect
    try:
        page.wait_for_url("**/mail/**", timeout=40000)
    except Exception:
        pass
    page.wait_for_timeout(4000)

    # v4.1: if landed on msalAuthRedirect, wait for MSAL to fully complete
    # This ensures MSAL stores account+tokens before we navigate to IMAP
    for _mw in range(3):
        _fu = _url(page)
        if "msalAuthRedirect" in _fu:
            log(f"  [login] msalAuthRedirect detected (attempt {_mw+1}), waiting for MSAL...")
            _wait_for_msal_complete(page, timeout_s=90)
            page.wait_for_timeout(3000)
        else:
            break

    # v4.1: also wait for gear icon to confirm SPA fully loaded + MSAL complete
    _gear_sel = '#owaSettingsBtn_container,[aria-label="Settings"],[aria-label="Settings, try new Outlook"]'
    try:
        if page.locator(_gear_sel).first.is_visible(timeout=30000):
            log("  [login] ✅ gear icon visible — MSAL + SPA fully initialized")
    except Exception:
        pass

    final_url = _url(page)
    log(f"  [login] post-login url: {final_url[:100]}")
    success = "outlook.live.com" in final_url and "mail" in final_url
    log(f"  [login] {'✅ success' if success else '⚠ uncertain — proceeding anyway'}")
    return success


def _handle_reauth(page, email: str, password: str) -> bool:
    u = _url(page)
    txt = _txt(page)
    is_reauth = any(x in u for x in (
        "account.live.com/reauth", "account.microsoft.com/reauth",
        "login.live.com", "login.microsoftonline.com"))
    has_pw_hint = any(k in txt for k in (
        "password", "sign in again", "verify your", "confirm your", "enter your"))

    if not is_reauth and not has_pw_hint:
        return True

    log(f"  [reauth] password re-challenge (url={u[:60]})")
    _pw_sel = 'input[type="password"], input[name="passwd"]'
    _pw_visible = False
    try:
        page.wait_for_selector(_pw_sel, timeout=8000)
        _pw_visible = True
    except Exception:
        pass

    if not _pw_visible:
        _em_sel = 'input[name="loginfmt"], input[type="email"]'
        try:
            if page.locator(_em_sel).first.is_visible(timeout=3000):
                _react_fill(page, 'input[name="loginfmt"]', email)
                page.wait_for_timeout(500)
                _click_primary(page)
                page.wait_for_timeout(3000)
        except Exception:
            pass
        try:
            page.wait_for_selector(_pw_sel, timeout=10000)
            _pw_visible = True
        except Exception:
            pass

    if not _pw_visible:
        log(f"  [reauth] pw box never appeared, continuing")
        return True

    try:
        page.locator(_pw_sel).first.fill(password)
        page.wait_for_timeout(500)
        _click_primary(page)
        page.wait_for_timeout(5000)
        log("  [reauth] password submitted")
    except Exception as e:
        log(f"  [reauth] error: {e}")

    for _ in range(3):
        if not _skip_interrupts(page):
            break
        page.wait_for_timeout(1500)

    log(f"  [reauth] done, url={_url(page)[:80]}")
    return True


def _handle_backup_email(page) -> tuple:
    u = _url(page)
    txt = _txt(page)

    BACKUP_KWS = (
        "alternate email", "backup email", "recovery email",
        "security info", "add email", "add a backup",
        "add another email", "keep your account secure",
        "protect your account", "add a way to", "proof up",
        "add your email",
    )
    is_proof_page = any(x in u for x in ("account.live.com/proofs", "account.microsoft.com/proofs"))
    has_kw = any(k in txt for k in BACKUP_KWS)

    if not is_proof_page and not has_kw:
        return False, "", ""

    log(f"  [backup] creating mail.tm...")
    try:
        mt_addr, mt_pw, mt_token = mailtm_create()
    except Exception as e:
        log(f"  [backup] mail.tm failed: {e}")
        return False, "", ""
    log(f"  [backup] addr: {mt_addr}")

    _em_filled = False
    for sel in [
        'input[name="EmailAddress"]', 'input[name="ProofConfirmation"]',
        'input[name*="Email"][type="email"]', 'input[name*="email"][type="email"]',
        'input[placeholder*="email" i]', 'input[type="email"]',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                loc.fill(mt_addr)
                page.wait_for_timeout(400)
                _em_filled = True
                log(f"  [backup] filled → {sel}")
                break
        except Exception:
            continue

    if not _em_filled:
        _em_filled = page.evaluate("""(addr) => {
            for (var inp of document.querySelectorAll('input[type="email"],input[type="text"]')) {
                var st = window.getComputedStyle(inp);
                if (st.display==='none'||st.visibility==='hidden') continue;
                if (!inp.value) {
                    var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                    s.call(inp,addr);
                    inp.dispatchEvent(new Event('input',{bubbles:true}));
                    inp.dispatchEvent(new Event('change',{bubbles:true}));
                    return true;
                }
            }
            return false;
        }""", mt_addr)

    page.wait_for_timeout(600)
    _click_primary(page, [
        'button:has-text("Send code")', 'button:has-text("Add")',
        'button:has-text("Continue")', 'button:has-text("Next")',
        'input[value*="Send"]', 'input[value*="Add"]',
    ])
    page.wait_for_timeout(3000)
    return True, mt_addr, mt_token


def _handle_security_code(page, mt_token: str) -> bool:
    if not mt_token:
        return False
    code = mailtm_poll_code(mt_token, timeout=240)
    if not code:
        log("  [code] no code received")
        return False

    log(f"  [code] code={code}")
    _code_filled = False
    for sel in [
        'input[name*="Code" i]', 'input[name*="code" i]',
        'input[name*="otp" i]', 'input[placeholder*="code" i]',
        'input[type="number"]',
        'input[type="text"][maxlength="6"]',
        'input[type="text"][maxlength="7"]',
        'input[type="text"][maxlength="8"]',
        'input[type="text"]',
    ]:
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
                var st=window.getComputedStyle(inp);
                if(st.display==='none'||st.visibility==='hidden') continue;
                var ml=parseInt(inp.getAttribute('maxlength')||'99');
                if(ml>=4&&ml<=9&&!inp.value){
                    var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                    s.call(inp,c);
                    inp.dispatchEvent(new Event('input',{bubbles:true}));
                    inp.dispatchEvent(new Event('change',{bubbles:true}));
                    return;
                }
            }
        }""", code)

    page.wait_for_timeout(500)
    _click_primary(page, [
        'button:has-text("Verify")', 'button:has-text("Confirm")',
        'button:has-text("Submit")', 'button:has-text("Next")',
    ])
    page.wait_for_timeout(5000)
    log(f"  [code] submitted, url={_url(page)[:80]}")
    return True


def _has_imap_content(page, timeout_ms=12000) -> bool:
    # v4 fix: must be on outlook.live.com to avoid false positive on microsoft.com marketing page
    u = _url(page)
    if "outlook.live.com" not in u:
        return False
    try:
        page.wait_for_selector(
            '[data-testid*="imap" i],[aria-label*="IMAP" i],'
            'input[type="radio"],[role="radio"],[role="switch"]',
            timeout=timeout_ms)
        return True
    except Exception:
        pass
    try:
        page.wait_for_function(
            "() => document.body && document.body.innerText.toLowerCase().indexOf('imap') >= 0",
            timeout=timeout_ms)
        return True
    except Exception:
        pass
    return False


def _nav_to_imap(page) -> bool:
    """Navigate to new Outlook IMAP settings page. Returns True if on IMAP page.

    New Outlook SPA auth cycle:
      goto /mail/options/mail/popimap
      → domcontentloaded fires (initial URL = popimap)
      → SPA JS starts: silent auth check → bO=4 (in-progress) → bO=1 (fail) OR stays popimap (success)
      
    Key: DO NOT check URL immediately after goto — the SPA auth takes ~2s.
    Wait 8s (matching standalone test) so the full auth cycle completes before reading URL.
    """
    log("  [nav] navigating to IMAP settings URL...")
    try:
        page.goto(IMAP_URL, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        log(f"  [nav] goto (ok): {str(e)[:80]}")

    # Wait 8s for the SPA auth cycle to complete (mirrors standalone test that succeeds)
    page.wait_for_timeout(8000)
    u = _url(page)
    log(f"  [nav] url after 8s settle: {u[:100]}")

    # If still in the SPA auth redirect loop, wait longer
    if "bO=" in u or "msalAuthRedirect" in u:
        log(f"  [nav] still in auth redirect, waiting 10s more...")
        page.wait_for_timeout(10000)
        u = _url(page)
        log(f"  [nav] url after 18s: {u[:80]}")

    # chrome-error → proxy/network failure, retry via inbox warmup
    if "chrome-error://" in u:
        log(f"  [nav] chrome-error, retrying via inbox warmup...")
        try:
            page.goto(INBOX_URL, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            page.goto(IMAP_URL, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(8000)
            u = _url(page)
            log(f"  [nav] after warmup: {u[:80]}")
        except Exception as e2:
            log(f"  [nav] warmup failed: {e2}")

    u = _url(page)
    log(f"  [nav] final url: {u[:100]}")

    # Check if on IMAP page
    if _has_imap_content(page, 8000):
        log("  [nav] on IMAP settings page")
        return True

    # Try gear-click path if gear is visible
    _gear_sel = '#owaSettingsBtn_container,[aria-label="Settings"],[aria-label="Settings, try new Outlook"]'
    _gear_visible = False
    try:
        _gear_visible = page.locator(_gear_sel).first.is_visible(timeout=5000)
    except Exception:
        pass

    if _gear_visible:
        log("  [nav] gear visible, SPA hot-navigate to IMAP URL")
        try:
            page.goto(IMAP_URL, timeout=25000, wait_until="domcontentloaded")
        except Exception as e:
            log(f"  [nav] SPA goto timeout: {e}")
        page.wait_for_timeout(3000)
        if _has_imap_content(page, 8000):
            log("  [nav] on IMAP page via SPA")
            return True

        for gs in ['#owaSettingsBtn_container','[aria-label="Settings"]',
                   '[aria-label="Settings, try new Outlook"]']:
            try:
                g = page.locator(gs).first
                if g.is_visible(timeout=2000):
                    g.click()
                    page.wait_for_timeout(2500)
                    break
            except Exception:
                continue
        page.evaluate("""() => {
            for(var el of document.querySelectorAll('button,[role="option"],[role="menuitem"],[role="treeitem"],li>a,a')){
                var t=el.textContent.trim();if(t==="Mail"||t==="Email"){el.click();return;}
            }
        }""")
        page.wait_for_timeout(2000)
        page.evaluate("""() => {
            for(var el of document.querySelectorAll('button,[role="option"],[role="menuitem"],[role="treeitem"],li>a,a,li')){
                var t=(el.textContent||"").toLowerCase();
                if(t.indexOf("forward")>=0||(t.indexOf("imap")>=0&&t.indexOf("pop")>=0)){
                    (el.tagName==="LI"?(el.querySelector("button,a")||el):el).click();return;
                }
            }
        }""")
        page.wait_for_timeout(3000)
        if _has_imap_content(page, 8000):
            log("  [nav] on IMAP page via gear-click")
            return True

    log(f"  [nav] NOT on IMAP page, url={_url(page)[:80]}, txt={_txt(page)[:150]}")
    return False


def _toggle_imap(page) -> str:
    return page.evaluate("""() => {
        var inputs=Array.from(document.querySelectorAll(
            'input[type="radio"],input[type="checkbox"],[role="radio"],[role="switch"],[role="checkbox"]'));
        for(var el of inputs){
            var par=el.closest('li')||el.closest('label')||el.closest('div')||el.parentElement;
            var txt=(par?par.textContent:"").toLowerCase();
            if(txt.indexOf("imap")<0) continue;
            var chk=el.getAttribute("aria-checked")||(el.checked!==undefined?String(el.checked):"false");
            if(chk==="true"||el.checked) return "already-on";
            el.click(); return "radio-clicked:"+(el.id||el.name||el.value||"?");
        }
        for(var lbl of Array.from(document.querySelectorAll("label"))){
            var lt=lbl.textContent.toLowerCase();
            if(lt.indexOf("enable imap")>=0){
                var inp=lbl.control||document.getElementById(lbl.htmlFor);
                if(inp){inp.click();return "label-inp";}
                lbl.click();return "label";
            }
        }
        var byAttr=Array.from(document.querySelectorAll('[aria-label*="IMAP" i],[data-testid*="imap" i]'));
        for(var el of byAttr){
            var chk=el.getAttribute("aria-checked")||(el.checked!==undefined?String(el.checked):"false");
            if(chk==="true"||el.checked) return "attr-already-on";
            el.click();return "attr-clicked:"+el.getAttribute("aria-label");
        }
        var sw=Array.from(document.querySelectorAll('[role="switch"]'));
        for(var s of sw){
            if(s.getAttribute("aria-checked")!=="true"){s.click();return "switch-clicked";}
            return "switch-already-on";
        }
        return "not-found";
    }""")

def _save_settings(page) -> bool:
    for sel in ['button:has-text("Save")','button[type="submit"]','input[type="submit"]','input[value="Save"]']:
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
            Array.from(document.querySelectorAll("button,input[type=submit]")).forEach(b=>{
                if(/^Save$/i.test(b.textContent.trim())||b.value==='Save') b.click();
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
    log(f"[enable-imap] v4 start: {email} (id={account_id})")

    p, browser = launch_browser(proxy=proxy, headless=headless)
    try:
        ctx_kw = dict(
            locale="en-US", timezone_id="America/Los_Angeles",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        if fingerprint_json:
            try:
                fp = json.loads(fingerprint_json)
                for k in ("user_agent","locale","timezone_id","viewport","screen"):
                    if fp.get(k):
                        ctx_kw[k] = fp[k]
            except Exception:
                pass

        # ── Step 0: Session setup — restore full storage_state (cookies + localStorage/MSAL tokens) ──
        # v4 fix: use new_context(storage_state=...) to restore BOTH cookies AND MSAL localStorage.
        #         Without localStorage, MSAL cannot silently refresh → Error 440.
        _cookies_injected = 0
        _storage_state = None

        if cookies_json and cookies_json.strip():
            try:
                _storage_state = json.loads(cookies_json)
                if isinstance(_storage_state, dict) and _storage_state.get("cookies"):
                    _cookies_injected = len(_storage_state["cookies"])
                    _origin_count = len(_storage_state.get("origins", []))
                    log(f"  [step0] storage_state: {_cookies_injected} cookies + {_origin_count} origins (localStorage)")
            except Exception as e:
                log(f"  [step0] cookies parse error: {e}")
                _storage_state = None

        if _storage_state:
            ctx_kw["storage_state"] = _storage_state

        ctx = browser.new_context(**ctx_kw)
        page = ctx.new_page()

        if not _cookies_injected and not password:
            log("  [step0] ❌ no cookies and no password — cannot proceed")
            return False

        if not _cookies_injected and password:
            log("  [step0] no cookies — attempting fresh login via proxy...")
            _do_fresh_login(page, ctx, email, password)
            _ss(page, "00_after_login", label)

        # DISABLED: add_init_script cleared old-Outlook MSAL telemetry cache, but new Outlook
        # (no /0/) does NOT use those keys and this hook may interfere with the SPA auth flow.
        # The new Outlook settles on popimap directly within ~5s when cookies are valid.
        # page.add_init_script(...)

        # ── Step 1: Navigate directly to IMAP settings URL ──────────────────
        # New Outlook (no /0/): outlook.live.com/mail/options/mail/popimap loads directly.
        # No inbox warmup needed — new Outlook handles auth in the SPA without msalAuthRedirect.
        log("  [step1] navigating to IMAP settings URL (new Outlook)...")
        _on_imap = _nav_to_imap(page)
        _ss(page, "01_nav", label)
        u = _url(page)
        log(f"  [step1] url={u[:100]}, on_imap={_on_imap}")

        # ── Step 2-4: Handle security cycles ───────────────────────────────
        _fresh_login_done = False  # track whether fresh login has been attempted

        for _cycle in range(6):
            u = _url(page)
            txt = _txt(page)
            log(f"  [cycle {_cycle}] url={u[:100]}")

            _is_chrome_error = "chrome-error://" in u
            _is_reauth = any(x in u for x in (
                "account.live.com/reauth", "account.microsoft.com/reauth",
                "login.live.com", "login.microsoftonline.com"))
            _is_proofs = any(x in u for x in (
                "account.live.com/proofs", "account.microsoft.com/proofs"))
            # New Outlook error: "something went wrong" in text AND NOT on any useful page
            _is_error440 = ("something went wrong" in txt and
                            "popimap" not in u and "options" not in u and
                            "outlook.live.com/mail" in u)
            # New Outlook IMAP URL: outlook.live.com/mail/options/mail/popimap (no /0/)
            _is_imap = "outlook.live.com" in u and ("popimap" in u or
                        ("imap" in txt and "options" in u and "outlook.live.com/mail" in u))

            if _is_imap and not _is_reauth and not _is_proofs:
                log(f"  [cycle {_cycle}] ✅ on IMAP page, proceeding to toggle")
                break

            # v4 fix: chrome-error → proxy failure on IMAP URL
            if _is_chrome_error:
                log(f"  [cycle {_cycle}] chrome-error — proxy blocked IMAP URL (HTTP 417/network)")
                log(f"  [cycle {_cycle}] trying inbox warmup then retry IMAP...")
                try:
                    page.goto(INBOX_URL, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(5000)
                    u2 = _url(page)
                    log(f"  [cycle {_cycle}] inbox url: {u2[:80]}")
                    if "chrome-error://" in u2:
                        log(f"  [cycle {_cycle}] inbox also blocked — proxy fully broken, aborting")
                        return False
                    if "msalAuthRedirect" in u2:
                        _wait_for_msal_complete(page, 60)
                    page.wait_for_timeout(3000)
                    _nav_to_imap(page)
                    _ss(page, f"c{_cycle}_chrome_err", label)
                    if _has_imap_content(page, 8000):
                        break
                except Exception as e:
                    log(f"  [cycle {_cycle}] chrome-error recovery failed: {e}")
                    return False
                continue

            if _is_error440:
                log(f"  [cycle {_cycle}] Error 440 — MSAL token cache empty (no account object stored at registration)")

                # v4.1 PRIMARY FIX: on FIRST 440 with password available, do full fresh login.
                # MSAL needs an account+token object in localStorage; the only way to get it is
                # a complete login that runs the MSAL SPA flow end-to-end.
                if not _fresh_login_done and password:
                    log(f"  [cycle {_cycle}] → fresh login fallback (clearing ctx, logging in with password)...")
                    _fresh_login_done = True
                    # Clear all auth state so fresh login starts truly clean
                    try:
                        ctx.clear_cookies()
                        page.evaluate("try { localStorage.clear(); sessionStorage.clear(); } catch(e) {}")
                    except Exception:
                        pass
                    _login_ok = _do_fresh_login(page, ctx, email, password)
                    _ss(page, f"c{_cycle}_freshlogin", label)
                    log(f"  [cycle {_cycle}] fresh login returned: {_login_ok}")
                    # Navigate to IMAP settings after fresh login
                    _nav_to_imap(page)
                    _ss(page, f"c{_cycle}_after_freshlogin", label)
                    if _has_imap_content(page, 10000):
                        break
                    continue

                # Secondary fallback: inbox warmup + wait longer
                log(f"  [cycle {_cycle}] inbox warmup + MSAL re-complete (fresh login {'already tried' if _fresh_login_done else 'no password'})...")
                try:
                    page.goto(INBOX_URL, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(5000)
                    u2 = _url(page)
                    log(f"  [cycle {_cycle}] inbox url after 440: {u2[:80]}")
                    if "msalAuthRedirect" in u2:
                        _wait_for_msal_complete(page, 90)
                    page.wait_for_timeout(3000)
                    _nav_to_imap(page)
                    _ss(page, f"c{_cycle}_after440", label)
                    if _has_imap_content(page, 8000):
                        break
                except Exception as e:
                    log(f"  [cycle {_cycle}] 440 recovery error: {e}")
                log(f"  [cycle {_cycle}] 440 unresolved, waiting 30s and retrying...")
                page.wait_for_timeout(30000)
                _nav_to_imap(page)
                _ss(page, f"c{_cycle}_after_wait", label)
                if _has_imap_content(page, 5000):
                    break
                continue

            if _is_reauth:
                log(f"  [cycle {_cycle}] reauth page")
                if not password:
                    log(f"  [cycle {_cycle}] no password for reauth, cannot proceed")
                    return False
                _handle_reauth(page, email, password)
                _ss(page, f"c{_cycle}_reauth", label)
                for _ in range(3):
                    if not _skip_interrupts(page):
                        break
                    page.wait_for_timeout(1500)
                log(f"  [cycle {_cycle}] re-navigating to IMAP after reauth...")
                _nav_to_imap(page)
                _ss(page, f"c{_cycle}_renav", label)
                continue

            if _is_proofs:
                log(f"  [cycle {_cycle}] proofs page")
                _added, mt_addr, mt_token = _handle_backup_email(page)
                _ss(page, f"c{_cycle}_backup", label)
                if _added:
                    _handle_security_code(page, mt_token)
                    _ss(page, f"c{_cycle}_code", label)
                for _ in range(3):
                    if not _skip_interrupts(page):
                        break
                    page.wait_for_timeout(1500)
                log(f"  [cycle {_cycle}] re-navigating to IMAP after proofs...")
                _nav_to_imap(page)
                _ss(page, f"c{_cycle}_renav", label)
                continue

            # Text-based detections
            if any(k in txt for k in ("password", "sign in again", "verify your identity", "re-enter")):
                log(f"  [cycle {_cycle}] pw hint in text")
                if not password:
                    log(f"  [cycle {_cycle}] no password, cannot proceed")
                    return False
                _handle_reauth(page, email, password)
                _ss(page, f"c{_cycle}_pw_txt", label)
                _nav_to_imap(page)
                _ss(page, f"c{_cycle}_renav_pw", label)
                continue

            BACKUP_KWS = (
                "alternate email","backup email","recovery email","security info",
                "add email","add a backup","keep your account secure",
                "protect your account","add a way to","proof up",
            )
            if any(k in txt for k in BACKUP_KWS):
                log(f"  [cycle {_cycle}] backup email hint")
                _added, mt_addr, mt_token = _handle_backup_email(page)
                _ss(page, f"c{_cycle}_backup_txt", label)
                if _added:
                    _handle_security_code(page, mt_token)
                    _ss(page, f"c{_cycle}_code_txt", label)
                for _ in range(3):
                    if not _skip_interrupts(page):
                        break
                    page.wait_for_timeout(1500)
                _nav_to_imap(page)
                _ss(page, f"c{_cycle}_renav_bkp", label)
                continue

            if "msalAuthRedirect" in u:
                log(f"  [cycle {_cycle}] still in MSAL redirect, waiting 30s more...")
                page.wait_for_timeout(30000)
                _nav_to_imap(page)
                _ss(page, f"c{_cycle}_msal_wait", label)
                continue

            log(f"  [cycle {_cycle}] no special page detected, breaking")
            break

        _ss(page, "05_pre_toggle", label)

        # ── Step 5: Toggle IMAP ─────────────────────────────────────────────
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
            log(f"  [toggle] NOT FOUND")
            log(f"  [toggle] url={_url(page)[:100]}")
            log(f"  [toggle] page text: {_txt(page)[:500]}")
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
    ap = argparse.ArgumentParser(description="Enable IMAP for Outlook v4")
    ap.add_argument("--email",      default="")
    ap.add_argument("--password",   default="")
    ap.add_argument("--account-id", type=int, default=0)
    ap.add_argument("--proxy",      default="")
    ap.add_argument("--headless",   default="true")
    args = ap.parse_args()

    headless = args.headless.lower() not in ("false","0","no")
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

    # 严禁直连 — 必须走代理 (ISP exit, not CF in-socks)
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
