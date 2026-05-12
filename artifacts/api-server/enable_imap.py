#!/usr/bin/env python3
"""
enable_imap.py v3
Root-cause fixes:
  1. VPS IP is blocked for fresh login (0x8004101A) - NEVER attempt fresh login
  2. cookies_json is valid but no MSAL localStorage - skip SPA inbox check
  3. Navigate DIRECTLY to IMAP settings URL with cookies injected
  4. Wait up to 90s for MSAL silent refresh to complete
  5. Handle reauth/proofs/security-code cycles on account.live.com
  6. Classic OWA (/owa/) fallback if SPA fails
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


def _setup_proxy(exit_ip: str = "", manual_proxy: str = "") -> tuple:
    """
    Pick a working SOCKS5 proxy.
    Priority: manual_proxy > XrayRelay(exit_ip) > static socks5 ports.
    NOTE: XrayRelay is used as first choice but static ports are tried on failure.
    """
    if manual_proxy:
        log(f"  [proxy] manual: {manual_proxy}")
        return manual_proxy, None

    import socket
    # Try static ss-in ports first (real ISP exit, best for auth pages)
    STATIC_PORTS = [10851, 10853, 10857, 10855, 10859]
    xray_inst = None

    # Try XrayRelay if exit_ip given
    if exit_ip:
        try:
            from xray_relay import XrayRelay as _XR
            xray_inst = _XR(exit_ip)
            if xray_inst.start(timeout=8.0):
                url = xray_inst.socks5_url
                log(f"  [proxy] XrayRelay exit_ip={exit_ip} -> {url}")
                return url, xray_inst
            else:
                log(f"  [proxy] XrayRelay({exit_ip}) start failed, trying static ports")
                xray_inst = None
        except Exception as e:
            log(f"  [proxy] XrayRelay error: {e}, trying static ports")
            xray_inst = None

    # Try static ports
    for port in STATIC_PORTS:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
            s.close()
            log(f"  [proxy] using static port {port}")
            return f"socks5://127.0.0.1:{port}", None
        except Exception:
            continue

    # CF pool fallback
    try:
        import random as _rand
        _ps = json.load(open('/tmp/cf_pool_state.json'))
        _avail = [x['ip'] for x in _ps.get('available', []) if isinstance(x, dict) and x.get('ip')]
        if _avail:
            _ip = _rand.choice(_avail[:20])
            from xray_relay import XrayRelay as _XR
            xray_inst = _XR(_ip)
            if xray_inst.start(timeout=8.0):
                url = xray_inst.socks5_url
                log(f"  [proxy] CF pool fallback IP={_ip} -> {url}")
                return url, xray_inst
    except Exception as e:
        log(f"  [proxy] CF pool error: {e}")

    raise RuntimeError("All proxies failed")


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
            "--disable-web-security",
            "--no-first-run",
            "--no-default-browser-check",
            "--ignore-certificate-errors",
            "--allow-running-insecure-content",
            "--disable-background-networking",
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


IMAP_URL   = "https://outlook.live.com/mail/0/options/mail/popimap"
INBOX_URL  = "https://outlook.live.com/mail/0/inbox"
CLASSIC_IMAP_URL = "https://outlook.live.com/owa/?path=/options/mail/popIMAPAccessEnabled"


def _ss(page, tag: str, label: str):
    safe = label.replace("/","_").replace("@","_at_")
    path = f"/tmp/imap3_{tag}_{safe}.png"
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
    Wait for MSAL silent-refresh redirect to complete.
    MSAL is done when:
    - URL no longer contains msalAuthRedirect
    - OR gear icon becomes visible
    Returns True if gear visible, False if just timeout/url-change
    """
    _gear_sel = '#owaSettingsBtn_container,[aria-label="Settings"],[aria-label="Settings, try new Outlook"]'
    deadline = time.time() + timeout_s
    log(f"  [msal] waiting up to {timeout_s}s for MSAL to complete...")
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        u = _url(page)
        # Check gear visible
        try:
            if page.locator(_gear_sel).first.is_visible(timeout=500):
                log(f"  [msal] gear visible, MSAL complete")
                return True
        except Exception:
            pass
        # Check MSAL redirect done
        if "msalAuthRedirect" not in u:
            log(f"  [msal] msalAuthRedirect gone, url={u[:80]}")
            return False
        # Check for error page
        txt = _txt(page)
        if "something went wrong" in txt and "440" in txt:
            log(f"  [msal] Error 440 detected, MSAL failed")
            return False
    log(f"  [msal] timeout after {timeout_s}s")
    return False


def _handle_reauth(page, email: str, password: str) -> bool:
    """Handle password re-challenge on account.live.com/reauth or login pages."""
    u = _url(page)
    txt = _txt(page)
    is_reauth = any(x in u for x in (
        "account.live.com/reauth", "account.microsoft.com/reauth",
        "login.live.com", "login.microsoftonline.com"))
    has_pw_hint = any(k in txt for k in (
        "password", "sign in again", "verify your", "confirm your", "enter your"))

    if not is_reauth and not has_pw_hint:
        return True

    log(f"  [reauth] password re-challenge detected (url={u[:60]})")
    _pw_sel = 'input[type="password"], input[name="passwd"]'
    _pw_visible = False
    try:
        page.wait_for_selector(_pw_sel, timeout=8000)
        _pw_visible = True
    except Exception:
        pass

    if not _pw_visible:
        # Maybe needs email first
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
    """Handle add backup email on account.live.com/proofs/add"""
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
                log(f"  [backup] filled -> {sel}")
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
                log(f"  [code] filled -> {sel}")
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
    """Navigate to IMAP settings page. Returns True if on IMAP page."""
    log("  [nav] navigating to IMAP settings URL directly...")
    try:
        page.goto(IMAP_URL, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        log(f"  [nav] goto timeout (ok): {e}")
    page.wait_for_timeout(2000)

    u = _url(page)
    log(f"  [nav] url after goto: {u[:100]}")

    # If MSAL redirect appeared, wait it out
    if "msalAuthRedirect" in u:
        _wait_for_msal_complete(page, timeout_s=90)
        page.wait_for_timeout(2000)
        u = _url(page)
        log(f"  [nav] url after MSAL wait: {u[:100]}")

    # Check if on IMAP page
    if _has_imap_content(page, 8000):
        log("  [nav] on IMAP settings page")
        return True

    # If not on IMAP page but not on a redirect page, try gear-click path
    _gear_sel = '#owaSettingsBtn_container,[aria-label="Settings"],[aria-label="Settings, try new Outlook"]'
    _gear_visible = False
    try:
        _gear_visible = page.locator(_gear_sel).first.is_visible(timeout=5000)
    except Exception:
        pass

    if _gear_visible:
        log("  [nav] gear visible, trying SPA hot-navigate to IMAP URL")
        try:
            page.goto(IMAP_URL, timeout=25000, wait_until="domcontentloaded")
        except Exception as e:
            log(f"  [nav] SPA goto timeout: {e}")
        page.wait_for_timeout(3000)
        if _has_imap_content(page, 8000):
            log("  [nav] on IMAP page via SPA")
            return True

        # Gear click path
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
    log(f"[enable-imap] v3 start: {email} (id={account_id})")

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

        ctx = browser.new_context(**ctx_kw)
        page = ctx.new_page()

        # ── Step 0: Inject cookies ──────────────────────────────────────────
        _cookies_injected = 0
        if cookies_json and cookies_json.strip():
            try:
                state = json.loads(cookies_json)
                if isinstance(state, dict) and state.get("cookies"):
                    ctx.add_cookies(state["cookies"])
                    _cookies_injected = len(state["cookies"])
                    log(f"  [ctx] injected {_cookies_injected} cookies")
            except Exception as e:
                log(f"  [ctx] cookies parse error: {e}")

        if not _cookies_injected:
            log("  [ctx] NO cookies available - cannot proceed (fresh login blocked on VPS)")
            return False

        # ── Step 1: Navigate directly to IMAP settings URL ─────────────────
        # KEY FIX: Do NOT check inbox first. Go straight to IMAP settings.
        # Cookies make the auth work; MSAL will silently refresh in the SPA.
        log("  [step1] navigating DIRECTLY to IMAP settings URL (skip inbox check)...")

        _on_imap = _nav_to_imap(page)
        _ss(page, "01_nav", label)
        u = _url(page)
        log(f"  [step1] url={u[:100]}, on_imap={_on_imap}")

        # ── Step 2-4: Handle security cycles ───────────────────────────────
        for _cycle in range(5):
            u = _url(page)
            txt = _txt(page)
            log(f"  [cycle {_cycle}] url={u[:100]}")

            _is_reauth = any(x in u for x in (
                "account.live.com/reauth", "account.microsoft.com/reauth",
                "login.live.com", "login.microsoftonline.com"))
            _is_proofs = any(x in u for x in (
                "account.live.com/proofs", "account.microsoft.com/proofs"))
            _is_error440 = "something went wrong" in txt and ("440" in txt or "startupdata" in txt)
            _is_imap = "popimap" in u or ("imap" in txt and "settings" in u)

            if _is_imap and not _is_reauth and not _is_proofs:
                log(f"  [cycle {_cycle}] on IMAP page, proceeding to toggle")
                break

            if _is_error440:
                log(f"  [cycle {_cycle}] Error 440 SPA boot failure, trying classic OWA...")
                # Try classic OWA path
                try:
                    page.goto(CLASSIC_IMAP_URL, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(5000)
                    u2 = _url(page)
                    log(f"  [cycle {_cycle}] classic OWA url: {u2[:80]}")
                    if "msalAuthRedirect" in u2:
                        _wait_for_msal_complete(page, 60)
                    if _has_imap_content(page, 8000):
                        log(f"  [cycle {_cycle}] classic OWA: on IMAP page")
                        break
                    # Try direct IMAP URL one more time after some wait
                    page.wait_for_timeout(3000)
                    _nav_to_imap(page)
                    _ss(page, f"c{_cycle}_after440", label)
                    if _has_imap_content(page, 5000):
                        break
                except Exception as e:
                    log(f"  [cycle {_cycle}] classic OWA error: {e}")
                # If still error 440, try waiting longer for MSAL
                log(f"  [cycle {_cycle}] waiting additional 30s and retrying...")
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
                # Re-navigate to IMAP after reauth
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

            # msalAuthRedirect still present - wait more
            if "msalAuthRedirect" in u:
                log(f"  [cycle {_cycle}] still in MSAL redirect, waiting 30s more...")
                page.wait_for_timeout(30000)
                _nav_to_imap(page)
                _ss(page, f"c{_cycle}_msal_wait", label)
                continue

            # Nothing blocking, break
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
            txt_dump = _txt(page)
            log(f"  [toggle] page text: {txt_dump[:500]}")
            return False

        if "already" not in res:
            page.wait_for_timeout(800)
            _save_settings(page)
        _ss(page, "07_save", label)

        log(f"  [enable-imap] SUCCESS ({res})")
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
    ap = argparse.ArgumentParser(description="Enable IMAP for Outlook v3")
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
        email         = acc["email"]
        password      = acc.get("password") or ""
        cookies_json  = (acc.get("cookies_json") or "").strip()
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
    except RuntimeError as e:
        log(f"[cli] {e}")
        sys.exit(2)

    ok = enable_imap(
        email=email, password=password, account_id=acc_id or None,
        cookies_json=cookies_json, fingerprint_json=fingerprint_json,
        proxy=args.proxy, headless=headless, xray_relay_inst=xray_relay_inst)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
