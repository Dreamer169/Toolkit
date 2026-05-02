#!/usr/bin/env python3
"""
novproxy_register_mailtm.py  v4
────────────────────────────────
Register N novproxy accounts using mail.tm disposable emails.
Key fixes vs v3:
 - Uses CDP type_text (not JS value setter) so Vue v-model fires → span.get-code appears
 - Retry loop waits for span.get-code after email is typed (up to 15s)
 - go_to uses 120s timeout, no asyncio.wait_for wrapper (lets CF load naturally)
 - DB save uses correct 'platform' column
 - mail.tm 429 retry with back-off

Usage:
  python3 novproxy_register_mailtm.py --count 3 [--proxy socks5://...] [--save-db]
"""
import asyncio, sys, re, json, time, secrets, string, argparse, traceback
import urllib.request, urllib.parse, urllib.error
sys.path.insert(0, '/root/Toolkit/scripts')

from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions
import ddddocr

CHROME        = '/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome'
MAILTM_BASE   = 'https://api.mail.tm'
MAILTM_DOMAIN = 'deltajohnsons.com'
_ocr = ddddocr.DdddOcr(show_ad=False)

# ─── logging ─────────────────────────────────────────────────────────────────
def log(msg):         print(f'[LOG]  {msg}', flush=True)
def ok(e, p, ip=''):  print(f'[OK]   {e}|{p}|{ip}', flush=True)
def fail(e, r):       print(f'[FAIL] {e}|{r}', flush=True)
def done(n, t):       print(f'[DONE] {n}/{t}', flush=True)


# ─── mail.tm helpers ──────────────────────────────────────────────────────────
def _mailtm_req(method, path, data=None, token=None, timeout=20):
    url  = MAILTM_BASE + path
    body = json.dumps(data).encode() if data else None
    h = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as exc:
        return 0, {'error': str(exc)}


def mailtm_create(retries=6):
    chars = string.ascii_lowercase + string.digits
    for attempt in range(retries):
        login    = ''.join(secrets.choice(chars) for _ in range(16))
        address  = f'{login}@{MAILTM_DOMAIN}'
        password = 'P@' + secrets.token_hex(12)
        code, body = _mailtm_req('POST', '/accounts', {'address': address, 'password': password})
        if code in (200, 201):
            return address, password, body.get('id', '') if isinstance(body, dict) else ''
        if code == 429:
            wait = 15 * (attempt + 1)
            log(f'  [mail.tm] Rate limited (429), waiting {wait}s ...')
            time.sleep(wait)
            continue
        raise RuntimeError(f'mail.tm create failed {code}: {body}')
    raise RuntimeError('mail.tm create failed after all retries')


def mailtm_token(address, password):
    code, body = _mailtm_req('POST', '/token', {'address': address, 'password': password})
    if code != 200:
        raise RuntimeError(f'mail.tm token failed {code}: {body}')
    return body['token'] if isinstance(body, dict) else ''


def _body_msgs(body):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        return body.get('hydra:member', [])
    return []


def mailtm_poll_code(token, timeout=180):
    """Block-poll inbox for novproxy verification code. Returns code str or ''."""
    deadline = time.time() + timeout
    log(f'  [mail.tm] Polling inbox for code (max {timeout}s)...')
    while time.time() < deadline:
        try:
            code, body = _mailtm_req('GET', '/messages', token=token)
            if code == 200:
                for msg in _body_msgs(body):
                    if not isinstance(msg, dict):
                        continue
                    subj  = msg.get('subject', '').lower()
                    intro = msg.get('intro',   '').lower()
                    if ('novproxy' in subj or 'verify' in subj or 'code' in subj
                            or 'confirm' in subj or re.search(r'\d{4,8}', intro)):
                        mid = msg.get('id', '')
                        if not mid:
                            continue
                        c2, full = _mailtm_req('GET', f'/messages/{mid}', token=token)
                        if c2 == 200 and isinstance(full, dict):
                            text = str(full.get('text', '') or '')
                            hr   = full.get('html', '')
                            html = ' '.join(str(h) for h in hr) if isinstance(hr, list) else str(hr or '')
                            codes = re.findall(r'\b(\d{4,8})\b', text + ' ' + html)
                            if codes:
                                log(f'  [mail.tm] Code found [{msg.get("subject","")[:40]}]: {codes[0]}')
                                return codes[0]
        except Exception as ex:
            log(f'  [mail.tm] Poll error: {ex}')
        time.sleep(7)
    log('  [mail.tm] Timeout — no code')
    return ''


# ─── browser helpers ──────────────────────────────────────────────────────────
def _make_opts(proxy_url=''):
    o = ChromiumOptions()
    o.headless         = True
    o.binary_location  = CHROME
    o.add_argument('--no-sandbox')
    o.add_argument('--disable-dev-shm-usage')
    o.add_argument('--disable-gpu')
    o.add_argument('--window-size=1920,1080')
    o.start_timeout    = 30
    o.webrtc_leak_protection = True
    if proxy_url:
        p = proxy_url.replace('socks5h://', 'socks5://')
        o.add_argument(f'--proxy-server={p}')
    return o


def _sv(r):
    """Extract string value from pydoll execute_script result dict."""
    if not isinstance(r, dict):
        return ''
    return r.get('result', {}).get('result', {}).get('value', '') or ''


async def _type_into(tab, elem_id, value):
    """Type into a form field using CDP keyboard events (triggers Vue v-model)."""
    try:
        el = await tab.find(id=elem_id, raise_exc=False)
        if not el:
            # JS fallback with keyboard event simulation
            r = await tab.execute_script(f'''(function(){{
                var el=document.getElementById("{elem_id}");
                if(!el) return "NOT_FOUND";
                el.focus();
                el.click();
                el.value="";
                for(var c of {json.dumps(value)}){{
                    el.dispatchEvent(new KeyboardEvent("keydown",{{key:c,bubbles:true}}));
                    el.dispatchEvent(new KeyboardEvent("keypress",{{key:c,bubbles:true}}));
                    el.value+=c;
                    el.dispatchEvent(new Event("input",{{bubbles:true}}));
                    el.dispatchEvent(new KeyboardEvent("keyup",{{key:c,bubbles:true}}));
                }}
                el.dispatchEvent(new Event("change",{{bubbles:true}}));
                el.dispatchEvent(new FocusEvent("blur",{{bubbles:true}}));
                return "JS_OK:"+el.value.length;
            }})()''')
            v = _sv(r)
            log(f'  _type_into({elem_id}) JS fallback: {v}')
            return v.startswith('JS_OK')
        await el.click()
        await asyncio.sleep(0.3)
        # clear via select-all + delete via JS
        await tab.execute_script(f'var e=document.getElementById("{elem_id}");if(e){{e.value="";e.dispatchEvent(new Event("input",{{bubbles:true}}));}};')
        await asyncio.sleep(0.15)
        await el.type_text(value, humanize=False)
        await asyncio.sleep(0.4)
        # fire blur so Vue sees the change
        await tab.execute_script(
            f'var e=document.getElementById("{elem_id}");'
            f'if(e){{e.dispatchEvent(new FocusEvent("blur",{{bubbles:true}}))}}'
        )
        return True
    except Exception as ex:
        log(f'  _type_into({elem_id}) error: {ex}')
        return False


async def _wait_for_get_code_btn(tab, max_wait=20):
    """Wait for span.get-code to appear after email is typed (Vue renders it)."""
    for _ in range(max_wait):
        r = await tab.execute_script('''(function(){
            var b=document.querySelector("span.get-code,.get-code,[class*=get-code],[class*=getcode],.send-code");
            if(b){
                var st=window.getComputedStyle(b);
                if(st.display!=="none"&&st.visibility!=="hidden")return "FOUND:"+b.textContent.trim().slice(0,20);
            }
            return "NOT_YET";
        })()''')
        v = _sv(r)
        if v.startswith('FOUND'):
            log(f'  span.get-code ready: {v}')
            return True
        await asyncio.sleep(1)
    log('  span.get-code not found after wait')
    return False


async def _click_get_code(tab, email):
    r = await tab.execute_script('''(function(){
        var sels=["span.get-code",".get-code","[class*=get-code]","[class*=getcode]",
                  ".send-code","[class*=send-code]","[class*=sendcode]"];
        for(var s of sels){
            var b=document.querySelector(s);
            if(b){b.click();return "clicked:"+s;}
        }
        var all=document.querySelectorAll("span,button,a,div");
        for(var e of all){
            var t=(e.textContent||"").trim();
            if(/^(get code|send code|获取验证码|发送验证码|get verification code)$/i.test(t)){
                e.click();return "text:"+t.slice(0,20);
            }
        }
        var dbg=[];
        var cc=document.querySelectorAll("span,button,a");
        for(var e of cc){var t=(e.textContent||"").trim();if(t&&t.length<40)dbg.push(t.slice(0,18));}
        return "NOT_FOUND|elems:"+dbg.slice(0,12).join(",");
    })()''')
    v = _sv(r)
    log(f'[{email}] Get-code click: {v[:100]}')
    return not v.startswith('NOT_FOUND')


async def _get_captcha_img(tab):
    import base64
    r = await tab.execute_script('''(function(){
        var imgs=document.querySelectorAll("img.code_img,img[id*=captcha],img[src^='data:image']");
        for(var i of imgs){if(i.src&&i.src.startsWith("data:image"))return i.src;}
        return "";
    })()''')
    src = _sv(r)
    for pfx in ['data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/gif;base64,']:
        if src.startswith(pfx):
            return base64.b64decode(src[len(pfx):])
    return b''


async def _refresh_captcha(tab):
    await tab.execute_script('''(function(){
        if(typeof check_verificat==="function"){check_verificat();return;}
        var btn=document.querySelector("img[onclick*=verificat],.refresh-captcha");
        if(btn)btn.click();
    })()''')
    await asyncio.sleep(1.5)


# ─── core registration ────────────────────────────────────────────────────────
async def register_one(novproxy_email, novproxy_pwd, mailtm_token_str, proxy_url=''):
    async with Chrome(options=_make_opts(proxy_url)) as browser:
        tab = await browser.start()

        # ── 1. Navigate to register page ────────────────────────────────────
        log(f'[{novproxy_email}] Navigating to register page ...')
        try:
            async with tab.expect_and_bypass_cloudflare_captcha():
                await tab.go_to('https://novproxy.com/register/', timeout=120)
        except Exception as e:
            log(f'[{novproxy_email}] CF note: {str(e)[:60]}')
            # CRITICAL: second go_to without timeout (default 300s) — lets the page
            # fully load including all JS bundles (register.encrypt.js etc.)
            try:
                await tab.go_to('https://novproxy.com/register/')
            except Exception as e2:
                log(f'[{novproxy_email}] 2nd go_to: {str(e2)[:60]}')
        await asyncio.sleep(4)

        # ── Wait for form to be ready (span.get-code must exist) ─────────
        form_ready = False
        for _chk in range(20):
            r2 = await tab.execute_script(
                '(function(){return !!document.querySelector("span.get-code,.get-code")+":"+!!document.getElementById("mailbox");})()'
            )
            v2 = _sv(r2)
            log(f'[{novproxy_email}] form-check {_chk}: {v2}')
            if v2.startswith('true:true'):
                form_ready = True
                break
            await asyncio.sleep(2)
        if not form_ready:
            log(f'[{novproxy_email}] WARNING: form not fully loaded — span.get-code missing')

        # ── 2. Fill email (CDP type_text → triggers Vue v-model) ──────────
        ok1 = await _type_into(tab, 'mailbox', novproxy_email)
        log(f'[{novproxy_email}] email fill: {"OK" if ok1 else "FAIL"}')
        await asyncio.sleep(1.5)   # let Vue update after blur

        # ── 3. Wait for span.get-code to appear, then click ───────────────
        btn_appeared = await _wait_for_get_code_btn(tab, max_wait=18)
        if btn_appeared:
            await _click_get_code(tab, novproxy_email)
            log(f'[{novproxy_email}] Get-code clicked; email should be sent now')
        else:
            log(f'[{novproxy_email}] WARNING: span.get-code never appeared — no email will arrive')
        await asyncio.sleep(1)

        # ── 4. Start mail.tm polling in background thread ─────────────────
        code_task = asyncio.create_task(
            asyncio.to_thread(mailtm_poll_code, mailtm_token_str, 180)
        )

        # ── 5. Fill password ───────────────────────────────────────────────
        ok2 = await _type_into(tab, 'password', novproxy_pwd)
        log(f'[{novproxy_email}] pwd fill: {"OK" if ok2 else "FAIL"}')

        # ── 6. Solve image CAPTCHA ─────────────────────────────────────────
        captcha_ok = False
        for attempt in range(8):
            img = await _get_captcha_img(tab)
            if not img:
                await _refresh_captcha(tab)
                continue
            try:
                c = _ocr.classification(img).strip()
                if len(c) >= 3:
                    ok3 = await _type_into(tab, 'verification', c)
                    captcha_ok = True
                    log(f'[{novproxy_email}] CAPTCHA #{attempt+1}: "{c}" fill={"OK" if ok3 else "FAIL"}')
                    break
            except Exception:
                pass
            await _refresh_captcha(tab)

        if not captcha_ok:
            code_task.cancel()
            return False, 'captcha_failed', ''

        # ── 7. Wait for email verification code ───────────────────────────
        email_code = await code_task

        if email_code:
            ok4 = await _type_into(tab, 'mailbox-captcha', email_code)
            log(f'[{novproxy_email}] email code ({email_code}) fill: {"OK" if ok4 else "FIELD_NOT_FOUND"}')
        else:
            log(f'[{novproxy_email}] WARNING: no email code — submitting without (may fail to get 500MB)')

        # ── 8. DOM snapshot ───────────────────────────────────────────────
        r = await tab.execute_script('''(function(){
            function g(id){var e=document.getElementById(id);return e?e.value:"N/A";}
            return g("mailbox")+"|"+g("verification")+"|"+g("mailbox-captcha");
        })()''')
        log(f'[{novproxy_email}] DOM snap [email|captcha|code]: {_sv(r)}')

        # ── 9. Submit ─────────────────────────────────────────────────────
        await tab.execute_script(
            '(function(){var b=document.querySelector("button.login_btn,button[type=submit]");if(b)b.click();})()'
        )

        # ── 10. Wait for redirect off /register ───────────────────────────
        for _ in range(25):
            await asyncio.sleep(1)
            url = str(await tab.current_url)
            if '/register' not in url:
                note = ' [email_verified]' if email_code else ' [no_email_verify]'
                return True, 'registered' + note, ''

        src = await tab.page_source
        errs = re.findall(r'errorTips[^>]*>([^<]{3,80})', src)
        reason = errs[0] if errs else 'timeout_no_redirect'
        return False, reason, ''


# ─── DB save ──────────────────────────────────────────────────────────────────
def save_to_db(email, password, notes='novproxy_mailtm'):
    try:
        import subprocess
        sql = (
            "INSERT INTO accounts (platform, email, password, status, notes, created_at) "
            f"VALUES ('novproxy', '{email}', '{password}', 'active', '{notes}', NOW()) "
            "ON CONFLICT (platform, email) DO UPDATE SET "
            "password=EXCLUDED.password, status='active', notes=EXCLUDED.notes;"
        )
        r = subprocess.run(
            ['psql', 'postgresql://postgres:postgres@localhost/toolkit', '-c', sql],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            log(f'[DB] Saved {email}')
        else:
            log(f'[DB] Save failed: {r.stderr[:120]}')
    except Exception as e:
        log(f'[DB] Exception: {e}')


# ─── quick post-registration diagnosis ───────────────────────────────────────
def quick_diag(email, password):
    try:
        import subprocess
        payload = json.dumps({'email': email, 'pwd': password})
        r = subprocess.run(
            ['python3', '/root/Toolkit/scripts/novproxy_diagnose.py', payload],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0:
            data = json.loads(r.stdout.strip())
            ti = data.get('trafficInfo', {})
            se = data.get('member', {}).get('secure_email', '?')
            log(f'[DIAG] {email}: flow_open={ti.get("flow_open")} '
                f'alltraffic={ti.get("alltraffic")} secure_email={"SET" if se else "EMPTY"}')
            return data
    except Exception as ex:
        log(f'[DIAG] Error: {ex}')
    return {}


# ─── main ─────────────────────────────────────────────────────────────────────
async def main(count, proxy_url, save_db, delay):
    log(f'=== novproxy mail.tm registration v4: {count} accounts ===')

    log('Creating mail.tm inboxes ...')
    inboxes = []
    for i in range(count):
        if i > 0:
            time.sleep(8)   # avoid 429
        addr, mpwd, _ = mailtm_create()
        tok = mailtm_token(addr, mpwd)
        inboxes.append((addr, mpwd, tok))
        log(f'  Inbox {i+1}: {addr}')

    n_ok    = 0
    results = []

    for i, (mt_addr, _mt_pwd, mt_tok) in enumerate(inboxes):
        novproxy_email = mt_addr
        novproxy_pwd   = 'Nv' + secrets.token_hex(8) + '!3'

        log(f'--- [{i+1}/{count}] {novproxy_email} ---')
        try:
            success, info, exit_ip = await register_one(
                novproxy_email, novproxy_pwd, mt_tok, proxy_url
            )
            if success:
                ok(novproxy_email, novproxy_pwd, exit_ip)
                n_ok += 1
                results.append({'email': novproxy_email, 'password': novproxy_pwd,
                                 'status': info, 'verified': '[email_verified]' in info})
                if save_db:
                    save_to_db(novproxy_email, novproxy_pwd)
                await asyncio.sleep(3)
                quick_diag(novproxy_email, novproxy_pwd)
            else:
                fail(novproxy_email, info)
                results.append({'email': novproxy_email, 'password': novproxy_pwd,
                                 'status': f'FAIL:{info}', 'verified': False})
        except Exception as e:
            tb_str = traceback.format_exc()
            fail(novproxy_email, str(e)[:120])
            log(f'[TB]\n{tb_str}')
            results.append({'email': novproxy_email, 'password': novproxy_pwd,
                             'status': f'EXC:{str(e)[:80]}', 'verified': False})

        if i < count - 1:
            await asyncio.sleep(delay)

    done(n_ok, count)
    print('\n=== SUMMARY ===', flush=True)
    for r in results:
        tag = '✓ VERIFIED' if r.get('verified') else '✗ unverified'
        print(f"  {tag:15s}  {r['status']:35s}  {r['email']} / {r['password']}", flush=True)
    print('\n[JSON]' + json.dumps(results), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--count',   type=int,   default=3)
    ap.add_argument('--proxy',   default='')
    ap.add_argument('--delay',   type=float, default=10.0)
    ap.add_argument('--save-db', action='store_true')
    args = ap.parse_args()
    asyncio.run(main(args.count, args.proxy, args.save_db, args.delay))
