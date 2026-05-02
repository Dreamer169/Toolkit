#!/usr/bin/env python3
"""
novproxy_register_worker.py v3 - email verification code (mailbox-captcha) support
Args:
  --accounts  JSON: [["email","pwd"], ...]
  --proxy     single proxy URL
  --proxies   comma-separated proxy list
  --delay     seconds (default 3)
  --graph-token-json  JSON: {"email": "ms_refresh_token", ...}

Output:
  [LOG]    message
  [OK]     email|password|exit_ip
  [FAIL]   email|reason
  [DONE]   ok/total
"""
import asyncio, sys, time, re, json, base64, argparse, urllib.request, urllib.parse
from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions
import ddddocr

CHROME = '/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome'
_ocr = ddddocr.DdddOcr(show_ad=False)
GRAPH_CLIENT_ID = '9e5f94bc-e8a4-4e73-b8be-63364c29d753'

def log(msg):        print(f'[LOG]  {msg}', flush=True)
def ok(e, p, ip=''): print(f'[OK]   {e}|{p}|{ip}', flush=True)
def fail(e, r):      print(f'[FAIL] {e}|{r}', flush=True)
def done(n, t):      print(f'[DONE] {n}/{t}', flush=True)


def _make_options(proxy_url=''):
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


def _refresh_graph_token(refresh_token):
    try:
        body = urllib.parse.urlencode({
            'grant_type': 'refresh_token',
            'client_id': GRAPH_CLIENT_ID,
            'refresh_token': refresh_token,
            'scope': 'https://graph.microsoft.com/Mail.Read offline_access',
        }).encode()
        req = urllib.request.Request(
            'https://login.microsoftonline.com/common/oauth2/v2.0/token',
            data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
        with urllib.request.urlopen(req, timeout=15) as r:
            tok = json.loads(r.read())
        return tok.get('access_token', '')
    except Exception as e:
        log(f'Graph token refresh failed: {e}')
        return ''


def _read_novproxy_code_graph(access_token):
    """Search Outlook for novproxy verification code - checks Inbox + JunkEmail"""
    try:
        req = urllib.request.Request(
            'https://graph.microsoft.com/v1.0/me/messages?%24search=%22novproxy%22&%24top=5&%24select=subject%2CbodyPreview%2CreceivedDateTime',
            headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as r:
            msgs = json.loads(r.read())
        for m in msgs.get('value', []):
            codes = re.findall(r'\b(\d{4,8})\b', m.get('bodyPreview', ''))
            if codes:
                return codes[0]
        for folder in ['JunkEmail', 'Inbox']:
            req2 = urllib.request.Request(
                f'https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages?%24top=10&%24select=subject%2CbodyPreview%2Cfrom&%24orderby=receivedDateTime+desc',
                headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'})
            with urllib.request.urlopen(req2, timeout=10) as r:
                fmsgs = json.loads(r.read())
            for m in fmsgs.get('value', []):
                frm = m.get('from', {}).get('emailAddress', {}).get('address', '').lower()
                subj = m.get('subject', '').lower()
                preview = m.get('bodyPreview', '')
                if 'novproxy' in frm or 'novproxy' in subj or ('verify' in subj and re.search(r'\d{4,8}', preview)):
                    codes = re.findall(r'\b(\d{4,8})\b', preview)
                    if codes:
                        return codes[0]
    except Exception as e:
        log(f'Graph read failed: {e}')
    return ''


async def _wait_novproxy_code(email, graph_token_map, timeout=90):
    """Wait for novproxy email verification code (polls MS Graph every 6s)"""
    refresh_token = graph_token_map.get(email, '')
    if not refresh_token:
        log(f'[{email}] No MS Graph credentials - skipping email verification wait')
        log(f'[{email}] NOTE: Without email verification, account may not receive free trial traffic')
        return ''

    log(f'[{email}] Refreshing Outlook Graph token...')
    access_token = _refresh_graph_token(refresh_token)
    if not access_token:
        log(f'[{email}] Graph token refresh failed - cannot read email')
        return ''

    log(f'[{email}] Polling inbox for novproxy code (max {timeout}s)...')
    deadline = time.time() + timeout
    while time.time() < deadline:
        code = _read_novproxy_code_graph(access_token)
        if code:
            log(f'[{email}] Email verification code received: {code}')
            return code
        await asyncio.sleep(6)

    log(f'[{email}] WARNING: Timed out waiting for email code ({timeout}s) - emails may not be delivered to Outlook from novproxy')
    return ''


async def _get_captcha(tab):
    r = await tab.execute_script('''(function(){
        var imgs=document.querySelectorAll("img.code_img");
        if(imgs.length>0) return imgs[0].src;
        var all=document.querySelectorAll("img[src^='data:image']");
        return all.length>0?all[0].src:"";
    })()''')
    src = r.get('result', {}).get('result', {}).get('value', '')
    for pfx in ['data:image/png;base64,', 'data:image/jpeg;base64,']:
        if src.startswith(pfx):
            return base64.b64decode(src[len(pfx):])
    return b''


async def _refresh_captcha(tab):
    await tab.execute_script('''(function(){
        if(typeof check_verificat==="function"){check_verificat();return;}
        var b=document.querySelector("img[onclick*=verificat]");if(b)b.click();
    })()''')
    await asyncio.sleep(1.2)


async def _fill(tab, eid, value):
    el = await tab.find(id=eid, raise_exc=False)
    if not el:
        return False
    await el.click()
    await asyncio.sleep(0.2)
    await el.clear()
    await asyncio.sleep(0.1)
    await el.type_text(value, humanize=False)
    await asyncio.sleep(0.3)
    return True


async def register_one(email, password, proxy_url='', graph_token_map=None):
    if graph_token_map is None:
        graph_token_map = {}

    async with Chrome(options=_make_options(proxy_url)) as browser:
        tab = await browser.start()
        if proxy_url:
            log(f'[{email}] proxy: {proxy_url[:40]}')
        log(f'[{email}] Opening register page...')
        try:
            async with tab.expect_and_bypass_cloudflare_captcha():
                await tab.go_to('https://novproxy.com/register/')
        except Exception as e:
            log(f'[{email}] CF bypass: {str(e)[:50]}')
            await tab.go_to('https://novproxy.com/register/')
        await asyncio.sleep(2.5)

        # 1. Fill email address
        await _fill(tab, 'mailbox', email)
        await asyncio.sleep(0.5)

        # 2. Click "Get code" to send email verification code
        click_res = await tab.execute_script('''(function(){
            var selectors = ["span.get-code", ".get-code", "[class*=get-code]", "[class*=getcode]", ".send-code"];
            for(var i=0;i<selectors.length;i++){
                var btn=document.querySelector(selectors[i]);
                if(btn){btn.click();return "clicked:"+selectors[i];}
            }
            return "button_not_found";
        })()''')
        click_val = click_res.get('result', {}).get('result', {}).get('value', '')
        log(f'[{email}] Get-code click: {click_val}')

        # 3. Start background email code polling
        email_code_task = asyncio.create_task(
            _wait_novproxy_code(email, graph_token_map, timeout=90)
        )

        # 4. Fill password
        await _fill(tab, 'password', password)

        # 5. Solve image CAPTCHA
        captcha_ok = False
        for attempt in range(5):
            img = await _get_captcha(tab)
            if not img:
                await _refresh_captcha(tab)
                continue
            try:
                code = _ocr.classification(img).strip()
                if len(code) >= 3:
                    await _fill(tab, 'verification', code)
                    captcha_ok = True
                    log(f'[{email}] Image CAPTCHA #{attempt+1}: "{code}"')
                    break
            except Exception:
                pass
            await _refresh_captcha(tab)

        if not captcha_ok:
            email_code_task.cancel()
            return False, 'captcha_failed', ''

        # 6. Wait for email verification code (runs concurrently with above)
        email_code = await email_code_task

        if email_code:
            filled = await _fill(tab, 'mailbox-captcha', email_code)
            log(f'[{email}] Email code filled (mailbox-captcha): {"OK" if filled else "FIELD_NOT_FOUND"} -> {email_code}')
        else:
            log(f'[{email}] Submitting without email verification code (may register as unverified account)')

        # 7. Log DOM state for debugging
        chk = await tab.execute_script('''(function(){
            var m=document.getElementById("mailbox");
            var v=document.getElementById("verification");
            var mc=document.getElementById("mailbox-captcha");
            return (m?m.value:"N/A")+"|"+(v?v.value:"N/A")+"|"+(mc?mc.value:"NO_FIELD");
        })()''')
        log(f'[{email}] DOM [mailbox|captcha|emailcode]: {chk.get("result",{}).get("result",{}).get("value","")}')

        # 8. Submit
        await tab.execute_script('(function(){var b=document.querySelector("button.login_btn");if(b)b.click();})()')

        # 9. Wait for redirect away from /register (success indicator)
        for _ in range(18):
            await asyncio.sleep(1)
            url = await tab.current_url
            if '/register' not in url:
                verified_note = ' [email_verified]' if email_code else ' [no_email_verify]'
                return True, 'registered' + verified_note, ''

        # Read error messages from page
        html = await tab.page_source
        errs = re.findall(r'errorTips[^>]*>([^<]{3,80})', html)
        reason = errs[0] if errs else 'timeout_no_redirect'
        return False, reason, ''


async def main(accounts, proxy_list, delay, graph_token_map):
    n_ok, total = 0, len(accounts)
    for i, (email, password) in enumerate(accounts):
        proxy = ''
        if proxy_list:
            proxy = proxy_list[i % len(proxy_list)]
        log(f'--- [{i+1}/{total}] Registering {email}{" via proxy" if proxy else ""} ---')
        try:
            success, info, exit_ip = await register_one(email, password, proxy, graph_token_map)
            if success:
                ok(email, password, exit_ip)
                n_ok += 1
            else:
                fail(email, info)
        except Exception as e:
            fail(email, str(e)[:120])
        if i < total - 1:
            await asyncio.sleep(delay)
    done(n_ok, total)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--accounts', required=True)
    ap.add_argument('--proxy', default='')
    ap.add_argument('--proxies', default='')
    ap.add_argument('--delay', type=float, default=3.0)
    ap.add_argument('--graph-token-json', default='{}',
                    help='JSON: {"email@domain.com": "ms_refresh_token"} for Outlook email reading')
    args = ap.parse_args()

    proxy_list = []
    if args.proxies:
        proxy_list = [p.strip() for p in args.proxies.split(',') if p.strip()]
    elif args.proxy:
        proxy_list = [args.proxy]

    graph_token_map = {}
    try:
        graph_token_map = json.loads(args.graph_token_json)
    except Exception:
        pass

    asyncio.run(main(json.loads(args.accounts), proxy_list, args.delay, graph_token_map))
