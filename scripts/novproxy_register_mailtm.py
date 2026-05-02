#!/usr/bin/env python3
"""
novproxy_register_mailtm.py
────────────────────────────
Register N novproxy accounts using mail.tm disposable emails.
mail.tm receives novproxy verification codes; MS Graph / Outlook NOT needed.

Usage:
  python3 novproxy_register_mailtm.py --count 3 [--proxy socks5://...]
  python3 novproxy_register_mailtm.py --count 3 --save-db
"""
import asyncio, sys, re, json, time, secrets, string, argparse
import urllib.request, urllib.parse, urllib.error
sys.path.insert(0, '/root/Toolkit/scripts')

from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions
import ddddocr

CHROME = '/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome'
MAILTM_BASE   = 'https://api.mail.tm'
MAILTM_DOMAIN = 'deltajohnsons.com'
_ocr = ddddocr.DdddOcr(show_ad=False)

# ─── logging ────────────────────────────────────────────────────────────────
def log(msg):         print(f'[LOG]  {msg}', flush=True)
def ok(e, p, ip=''):  print(f'[OK]   {e}|{p}|{ip}', flush=True)
def fail(e, r):       print(f'[FAIL] {e}|{r}', flush=True)
def done(n, t):       print(f'[DONE] {n}/{t}', flush=True)


# ─── mail.tm helpers ─────────────────────────────────────────────────────────
def _mailtm_req(method, path, data=None, token=None, timeout=20):
    url = MAILTM_BASE + path
    body = json.dumps(data).encode() if data else None
    h = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as exc:
        return 0, {'error': str(exc)}


def mailtm_create():
    """Returns (address, password, account_id)"""
    chars = string.ascii_lowercase + string.digits
    login = ''.join(secrets.choice(chars) for _ in range(16))
    address = f'{login}@{MAILTM_DOMAIN}'
    password = 'P@' + secrets.token_hex(12)
    code, body = _mailtm_req('POST', '/accounts', {'address': address, 'password': password})
    if code not in (200, 201):
        raise RuntimeError(f'mail.tm create failed {code}: {body}')
    return address, password, body.get('id', '')


def mailtm_token(address, password):
    code, body = _mailtm_req('POST', '/token', {'address': address, 'password': password})
    if code != 200:
        raise RuntimeError(f'mail.tm token failed {code}: {body}')
    return body['token']


def mailtm_poll_code(token, timeout=120):
    """Poll inbox for a novproxy verification code. Returns code string or ''."""
    deadline = time.time() + timeout
    log('  [mail.tm] Polling inbox for novproxy code ...')
    while time.time() < deadline:
        code, body = _mailtm_req('GET', '/messages', token=token)
        if code == 200:
            for msg in body.get('hydra:member', []):
                subj = msg.get('subject', '').lower()
                intro = msg.get('intro', '').lower()
                if 'novproxy' in subj or 'verify' in subj or 'code' in subj or re.search(r'\d{4,8}', intro):
                    mid = msg['id']
                    c2, full = _mailtm_req('GET', f'/messages/{mid}', token=token)
                    if c2 == 200:
                        text = full.get('text', '') or ''
                        html = full.get('html', [''])[0] if isinstance(full.get('html'), list) else full.get('html', '')
                        combined = text + ' ' + (html or '')
                        codes = re.findall(r'\b(\d{4,8})\b', combined)
                        if codes:
                            log(f'  [mail.tm] Code found in "{msg.get("subject", "")[:50]}": {codes[0]}')
                            return codes[0]
        time.sleep(6)
    log('  [mail.tm] Timeout: no code received')
    return ''


# ─── browser helpers ─────────────────────────────────────────────────────────
def _make_opts(proxy_url=''):
    o = ChromiumOptions()
    o.headless = True
    o.binary_location = CHROME
    o.add_argument('--no-sandbox')
    o.add_argument('--disable-dev-shm-usage')
    o.add_argument('--disable-gpu')
    o.add_argument('--window-size=1920,1080')
    o.start_timeout = 30
    o.webrtc_leak_protection = True
    if proxy_url:
        p = proxy_url.replace('socks5h://', 'socks5://')
        o.add_argument(f'--proxy-server={p}')
    return o


async def _fill(tab, elem_id, value):
    el = await tab.find(id=elem_id, raise_exc=False)
    if not el:
        return False
    await el.click()
    await asyncio.sleep(0.2)
    await el.clear()
    await asyncio.sleep(0.1)
    await el.type_text(value, humanize=False)
    await asyncio.sleep(0.3)
    return True


async def _get_captcha(tab):
    result = await tab.execute_script('''(function(){
        var imgs=document.querySelectorAll("img.code_img");
        if(imgs.length>0)return imgs[0].src;
        var all=document.querySelectorAll("img[src^='data:image']");
        if(all.length>0)return all[0].src;
        return "";
    })()''')
    import base64
    src = result.get('result', {}).get('result', {}).get('value', '')
    for prefix in ['data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/gif;base64,']:
        if src.startswith(prefix):
            return base64.b64decode(src[len(prefix):])
    return b''


async def _refresh_captcha(tab):
    await tab.execute_script('''(function(){
        if(typeof check_verificat==="function"){check_verificat();return;}
        var btn=document.querySelector("img[onclick*=verificat]");
        if(btn)btn.click();
    })()''')
    await asyncio.sleep(1.2)


# ─── core registration ────────────────────────────────────────────────────────
async def register_one(novproxy_email, novproxy_pwd, mailtm_token_str, proxy_url=''):
    """Register one novproxy account. mailtm_token_str is used to read verification code."""
    async with Chrome(options=_make_opts(proxy_url)) as browser:
        tab = await browser.start()
        log(f'[{novproxy_email}] Opening register page ...')
        try:
            async with tab.expect_and_bypass_cloudflare_captcha():
                await tab.go_to('https://novproxy.com/register/')
        except Exception as e:
            log(f'[{novproxy_email}] CF bypass note: {str(e)[:60]}')
            await tab.go_to('https://novproxy.com/register/')
        await asyncio.sleep(3)

        # 1. Fill email
        await _fill(tab, 'mailbox', novproxy_email)
        await asyncio.sleep(0.5)

        # 2. Click "Get code" button → triggers novproxy to send email
        click_res = await tab.execute_script('''(function(){
            var sels=["span.get-code",".get-code","[class*=get-code]","[class*=getcode]",".send-code",".sendCode"];
            for(var s of sels){var b=document.querySelector(s);if(b){b.click();return "clicked:"+s;}}
            var all=document.querySelectorAll("span,button,a");
            for(var e of all){if(e.textContent&&e.textContent.trim().toLowerCase().includes("get code")){e.click();return "text_match:"+e.textContent.trim().slice(0,30);}}
            return "not_found";
        })()''')
        click_val = click_res.get('result', {}).get('result', {}).get('value', '')
        log(f'[{novproxy_email}] Get-code click: {click_val}')
        await asyncio.sleep(1)

        # 3. Start polling mail.tm for the code (background)
        code_task = asyncio.create_task(
            asyncio.to_thread(mailtm_poll_code, mailtm_token_str, 120)
        )

        # 4. Fill password
        await _fill(tab, 'password', novproxy_pwd)

        # 5. Solve image CAPTCHA
        captcha_ok = False
        for attempt in range(6):
            img = await _get_captcha(tab)
            if not img:
                await _refresh_captcha(tab)
                await asyncio.sleep(1)
                continue
            try:
                c = _ocr.classification(img).strip()
                if len(c) >= 3:
                    await _fill(tab, 'verification', c)
                    captcha_ok = True
                    log(f'[{novproxy_email}] CAPTCHA #{attempt+1}: "{c}"')
                    break
            except Exception:
                pass
            await _refresh_captcha(tab)

        if not captcha_ok:
            code_task.cancel()
            return False, 'captcha_failed', ''

        # 6. Wait for email verification code
        email_code = await code_task

        if email_code:
            filled = await _fill(tab, 'mailbox-captcha', email_code)
            log(f'[{novproxy_email}] Email code ({email_code}) filled: {"OK" if filled else "FIELD_NOT_FOUND"}')
        else:
            log(f'[{novproxy_email}] WARNING: No email code — submitting without (will be unverified)')

        # 7. DOM check
        chk = await tab.execute_script('''(function(){
            var m=document.getElementById("mailbox");
            var v=document.getElementById("verification");
            var mc=document.getElementById("mailbox-captcha");
            return (m?m.value:"N/A")+"|"+(v?v.value:"N/A")+"|"+(mc?mc.value:"NO_FIELD");
        })()''')
        log(f'[{novproxy_email}] DOM [mailbox|captcha|emailcode]: {chk.get("result",{}).get("result",{}).get("value","")}')

        # 8. Submit
        await tab.execute_script('(function(){var b=document.querySelector("button.login_btn");if(b)b.click();})()')

        # 9. Wait for redirect (success)
        for _ in range(20):
            await asyncio.sleep(1)
            url = await tab.current_url
            if '/register' not in url:
                note = ' [email_verified]' if email_code else ' [no_email_verify]'
                return True, 'registered' + note, ''

        # Read error text
        html = await tab.page_source
        errs = re.findall(r'errorTips[^>]*>([^<]{3,80})', html)
        reason = errs[0] if errs else 'timeout_no_redirect'
        return False, reason, ''


# ─── DB save ──────────────────────────────────────────────────────────────────
def save_to_db(email, password):
    try:
        import subprocess
        sql = f"INSERT INTO accounts (email, password, type, status, created_at) VALUES ('{email}', '{password}', 'novproxy', 'active', NOW()) ON CONFLICT (email) DO UPDATE SET password=EXCLUDED.password, status='active';"
        result = subprocess.run(
            ['psql', 'postgresql://postgres:postgres@localhost/toolkit', '-c', sql],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            log(f'[DB] Saved {email}')
        else:
            log(f'[DB] Save failed for {email}: {result.stderr[:100]}')
    except Exception as e:
        log(f'[DB] Exception saving {email}: {e}')


# ─── main ─────────────────────────────────────────────────────────────────────
async def main(count, proxy_url, save_db, delay):
    log(f'=== novproxy mail.tm registration: {count} accounts ===')

    # Pre-create all mail.tm accounts
    log('Creating mail.tm inboxes ...')
    inboxes = []
    for i in range(count):
        addr, mpwd, _ = mailtm_create()
        tok = mailtm_token(addr, mpwd)
        inboxes.append((addr, mpwd, tok))
        log(f'  Inbox {i+1}: {addr}')

    n_ok = 0
    results = []

    for i, (mt_addr, mt_pwd, mt_tok) in enumerate(inboxes):
        # Use mail.tm address as novproxy email
        novproxy_email = mt_addr
        novproxy_pwd   = 'Nx' + secrets.token_hex(8) + '!2'

        log(f'--- [{i+1}/{count}] Registering {novproxy_email} ---')
        try:
            success, info, exit_ip = await register_one(novproxy_email, novproxy_pwd, mt_tok, proxy_url)
            if success:
                ok(novproxy_email, novproxy_pwd, exit_ip)
                n_ok += 1
                results.append({'email': novproxy_email, 'password': novproxy_pwd, 'status': info})
                if save_db:
                    save_to_db(novproxy_email, novproxy_pwd)
            else:
                fail(novproxy_email, info)
                results.append({'email': novproxy_email, 'password': novproxy_pwd, 'status': f'FAIL:{info}'})
        except Exception as e:
            fail(novproxy_email, str(e)[:140])
            results.append({'email': novproxy_email, 'password': novproxy_pwd, 'status': f'EXCEPTION:{str(e)[:80]}'})

        if i < count - 1:
            await asyncio.sleep(delay)

    done(n_ok, count)

    print('\n=== RESULT SUMMARY ===', flush=True)
    for r in results:
        print(f"  {r['status']:30s}  {r['email']}  /  {r['password']}", flush=True)

    # Also dump as JSON for easy consumption
    print('\n[JSON]' + json.dumps(results), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--count',   type=int,   default=3)
    ap.add_argument('--proxy',   default='', help='e.g. socks5://127.0.0.1:1080')
    ap.add_argument('--delay',   type=float, default=5.0)
    ap.add_argument('--save-db', action='store_true', help='Save results to toolkit DB')
    args = ap.parse_args()

    asyncio.run(main(args.count, args.proxy, args.save_db, args.delay))
