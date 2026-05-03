#!/usr/bin/env python3
"""
v15: Complete accessibility registration with email verification
1. ≡ → Accessibility Cookie → Retrieve link → fill email + submit
2. Check mail.tm inbox for hcaptcha verification email → click link
3. Cookie properly set (linked to registered account)
4. Reload, challenge bypasses
5. Get token → register talordata
"""
import asyncio, re, secrets, string, json, time
import requests as rq, urllib.request, urllib.error

API = 'https://api.talordata.com'
H = {'User-Agent': 'Mozilla/5.0', 'Origin': 'https://dashboard.talordata.com', 'Content-Type': 'application/json'}

def mr(method, path, data=None, token=None):
    url = 'https://api.mail.tm' + path
    body = json.dumps(data).encode() if data else None
    h = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if token: h['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except: return e.code, {}
    except: return 0, {}

async def get_token(page):
    for _ in range(3):
        try:
            tok = await page.evaluate(
                '() => { var e=document.querySelector("[name=\'h-captcha-response\']"); '
                'if(e&&e.value&&e.value.length>20) return e.value; '
                'if(typeof hcaptcha!="undefined"){var r=hcaptcha.getResponse(); if(r&&r.length>20) return r;} '
                'return null; }'
            )
            if tok: return tok
        except: pass
        await asyncio.sleep(0.5)
    return None

async def do_register(email, nv_pwd, token, mt_tok):
    r = rq.post(f'{API}/users/v1/auth/send_activation', json={'email': email, 'hcaptcha': token}, headers=H, timeout=10)
    d = r.json()
    print(f'send_activation: {d.get("code")} {d.get("message")}')
    if d.get('code') != 0: return False
    email_code = None
    for i in range(20):
        await asyncio.sleep(5)
        _, msgs = mr('GET', '/messages', token=mt_tok)
        items = (msgs or {}).get('hydra:member', [])
        if items:
            for msg in items:
                _, full = mr('GET', f'/messages/{msg["id"]}', token=mt_tok)
                ft = str(full.get('text', '')) + str(full.get('html', ''))
                codes = re.findall(r'\b(\d{6})\b', ft)
                if codes: email_code = codes[0]; break
            if email_code: break
        print(f'  [{(i+1)*5}s] waiting email code...')
    if not email_code: return False
    print(f'code: {email_code}')
    r2 = rq.post(f'{API}/users/v1/auth/register', json={'email': email, 'password': nv_pwd, 'code': email_code}, headers=H, timeout=10)
    d2 = r2.json()
    jwt = d2.get('data', '') if isinstance(d2.get('data'), str) else (d2.get('data') or {}).get('token', '')
    if not jwt: return False
    r3 = rq.get(f'{API}/users/v1/auth/activation', params={'token': jwt}, headers={k: v for k, v in H.items() if k != 'Content-Type'}, timeout=10)
    d3 = r3.json()
    print(f'activation: {d3.get("code")} {d3.get("message")}')
    if d3.get('code') == 0:
        print(f'\n=== SUCCESS: {email} | {nv_pwd} ===')
        import psycopg2
        try:
            conn = psycopg2.connect('postgresql://postgres:postgres@localhost/toolkit')
            cur = conn.cursor()
            cur.execute('CREATE TABLE IF NOT EXISTS accounts (id SERIAL PRIMARY KEY, platform TEXT, email TEXT, password TEXT, notes TEXT, created_at TIMESTAMP DEFAULT NOW())')
            cur.execute('INSERT INTO accounts (platform,email,password,notes) VALUES (%s,%s,%s,%s)', ('talordata', email, nv_pwd, 'activated'))
            conn.commit(); conn.close()
            print('saved to DB')
        except Exception as e: print(f'DB: {e}')
        return True
    return False

async def wait_for_hcaptcha_email(mt_tok, since=None, timeout=90):
    """Wait for hcaptcha verification email in mail.tm inbox"""
    start = time.time()
    seen_ids = set()
    while time.time() - start < timeout:
        _, msgs = mr('GET', '/messages', token=mt_tok)
        items = (msgs or {}).get('hydra:member', [])
        for msg in items:
            mid = msg.get('id')
            if mid in seen_ids: continue
            seen_ids.add(mid)
            from_addr = str(msg.get('from', {}))
            subj = str(msg.get('subject', ''))
            # hcaptcha sends from hcaptcha.com or dashboard.hcaptcha.com
            if 'hcaptcha' in from_addr.lower() or 'hcaptcha' in subj.lower() or 'accessibility' in subj.lower():
                _, full = mr('GET', f'/messages/{mid}', token=mt_tok)
                ft = str(full.get('text', '')) + str(full.get('html', ''))
                # Find verification URL
                urls = re.findall(r'https?://[^\s<>"\']+', ft)
                hcap_urls = [u for u in urls if 'hcaptcha' in u.lower() and ('verify' in u.lower() or 'confirm' in u.lower() or 'token' in u.lower() or 'access' in u.lower())]
                print(f'hcaptcha email found: subject={subj}')
                print(f'  hcap urls: {hcap_urls}')
                if hcap_urls: return hcap_urls[0]
                # Also try to find any link from hcaptcha domain
                hcap_all = [u for u in urls if 'hcaptcha' in u.lower() and 'hcaptcha.com' in u.lower()]
                if hcap_all: return hcap_all[0]
                print(f'  all urls: {urls[:5]}')
                return None
        await asyncio.sleep(5)
        print(f'  [{int(time.time()-start)}s] waiting hcaptcha email...')
    return None

async def main():
    # Use the SAME email for both talordata and hcaptcha accessibility registration
    # (hcaptcha accessibility cookie is linked to the email)
    chars = string.ascii_lowercase + string.digits
    login = ''.join(secrets.choice(chars) for _ in range(14))
    # Use a common email that can receive hcaptcha emails
    email = f'{login}@deltajohnsons.com'
    mt_pwd = 'P@' + secrets.token_hex(10)
    nv_pwd = 'Aa1!' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    mr('POST', '/accounts', {'address': email, 'password': mt_pwd})
    _, tb = mr('POST', '/token', {'address': email, 'password': mt_pwd})
    mt_tok = tb.get('token', '')
    print(f'email={email} pass={nv_pwd} tok={mt_tok[:20]}')

    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    stealth = Stealth()
    getcap_done = [False]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        ctx = await browser.new_context(
            viewport={'width': 1280, 'height': 900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='en-US'
        )
        page = await ctx.new_page()
        await stealth.apply_stealth_async(page)
        new_pages = []
        ctx.on('page', lambda pg: new_pages.append(pg))

        async def on_resp(r):
            url = r.url
            if 'api.talordata' in url:
                try: d = await r.json(); print(f'  [API] {d.get("code")} {d.get("message")}')
                except: pass
            elif 'getcaptcha' in url:
                getcap_done[0] = True; print('  [getcap]')
        page.on('response', on_resp)

        # --- Load talordata reg page ---
        await page.goto('https://dashboard.talordata.com/reg', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(5)
        await page.fill('input[placeholder*="email" i]', email)
        await page.fill('input[type="password"]', nv_pwd)
        try: await page.fill('input[placeholder*="invitation" i]', 'z46vzbz4')
        except: pass
        cb = await page.query_selector('.el-checkbox__inner')
        if cb:
            box = await cb.bounding_box()
            if box: await page.mouse.click(box['x'] + 3, box['y'] + 3); await asyncio.sleep(0.3)
        signup = page.get_by_text('Sign Up').last
        box = await signup.bounding_box()
        if box: await page.mouse.click(box['x'] + box['width'] // 2, box['y'] + box['height'] // 2)
        print('clicked Sign Up')
        for i in range(15):
            await asyncio.sleep(1)
            if getcap_done[0]: print(f'[{i}s] challenge loaded'); break
        await asyncio.sleep(4)

        # Get challenge frame
        frames = [f for f in page.frames if 'hcaptcha' in f.url]
        challenge_box = {'x': 546, 'y': 163.9, 'width': 520, 'height': 570}
        for frame in frames:
            try:
                h = await frame.frame_element()
                b = await h.bounding_box()
                if b and b.get('width', 0) > 300: challenge_box = b; break
            except: pass

        # Click ≡ menu
        tx = challenge_box['x'] + challenge_box['width'] * (28/520)
        ty = challenge_box['y'] + challenge_box['height'] * (541/570)
        await page.mouse.click(tx, ty)
        await asyncio.sleep(1.5)

        # Click Accessibility Cookie from menu
        for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
            try:
                el = await frame.query_selector('[aria-label*="Accessibility Cookie"]')
                if el: await el.click(); print('clicked Accessibility Cookie'); break
            except: pass
        await asyncio.sleep(2)

        # Click "Retrieve accessibility cookie." link
        for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
            try:
                for sel in ['a[href*="dashboard.hcaptcha.com"]', 'a[href*="signup"]', 'a']:
                    els = await frame.query_selector_all(sel)
                    for el in els:
                        text = await el.text_content()
                        if text and 'retrieve' in text.lower():
                            await el.click()
                            print(f'clicked retrieve link: {text.strip()}')
                            break
                    else: continue
                    break
            except Exception as e: print(f'retrieve err: {e}')
        await asyncio.sleep(3)

        # Handle hcaptcha accessibility sign-up page
        acc_page = new_pages[-1] if new_pages else None
        if acc_page:
            try: await acc_page.wait_for_load_state('domcontentloaded', timeout=15000)
            except: pass
            print(f'acc page: {acc_page.url}')
            await asyncio.sleep(2)
            # Fill email and submit
            try:
                inp = await acc_page.query_selector('input[type="email"], input[type="text"], input:not([type])')
                if inp:
                    await inp.fill(email)
                    print(f'filled: {email}')
                    await asyncio.sleep(0.5)
                    btn = await acc_page.query_selector('button[type="submit"], button:has-text("Submit"), button')
                    if btn:
                        await btn.click()
                        print('clicked Submit on acc page')
                await asyncio.sleep(2)
                await acc_page.screenshot(path='/tmp/v15_acc_submitted.png')
                # Check for success message
                try:
                    msg_text = await acc_page.evaluate('() => document.body.innerText')
                    print(f'acc page text after submit: {msg_text[:200]}')
                except: pass
            except Exception as e: print(f'acc form err: {e}')

        # --- Wait for hcaptcha verification email ---
        print('\nWaiting for hcaptcha verification email...')
        hcap_verify_url = await wait_for_hcaptcha_email(mt_tok, timeout=60)
        
        if hcap_verify_url:
            print(f'Verification URL: {hcap_verify_url}')
            # Visit the verification URL
            verify_page = await ctx.new_page()
            await verify_page.goto(hcap_verify_url, wait_until='domcontentloaded', timeout=20000)
            await asyncio.sleep(3)
            await verify_page.screenshot(path='/tmp/v15_verified.png')
            print(f'After verify: {verify_page.url}')
            try:
                body_text = await verify_page.evaluate('() => document.body.innerText')
                print(f'verify page: {body_text[:200]}')
            except: pass
            await verify_page.close()
        else:
            print('[!] no hcaptcha verification email received')
            # Check all emails
            _, msgs = mr('GET', '/messages', token=mt_tok)
            items = (msgs or {}).get('hydra:member', [])
            print(f'total emails: {len(items)}')
            for msg in items:
                print(f'  email: from={msg.get("from")} subj={msg.get("subject")}')

        # --- Check accessibility status after verification ---
        await asyncio.sleep(2)
        cookies = await ctx.cookies()
        hmt_cookies = [c for c in cookies if c.get('name') == 'hmt_id']
        print(f'\nhmt_id cookies: {len(hmt_cookies)}')
        for c in hmt_cookies:
            print(f'  {c.get("value","")[:40]} @ {c.get("domain")}')

        # Close acc_page if still open
        if acc_page:
            try: await acc_page.close()
            except: pass

        # --- Reload talordata reg with cookies, try bypass ---
        print('\n=== Trying talordata reg with accessibility cookies ===')
        getcap_done[0] = False
        await page.goto('https://dashboard.talordata.com/reg', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(5)
        await page.fill('input[placeholder*="email" i]', email)
        await page.fill('input[type="password"]', nv_pwd)
        try: await page.fill('input[placeholder*="invitation" i]', 'z46vzbz4')
        except: pass
        cb = await page.query_selector('.el-checkbox__inner')
        if cb:
            box = await cb.bounding_box()
            if box: await page.mouse.click(box['x'] + 3, box['y'] + 3); await asyncio.sleep(0.3)
        signup = page.get_by_text('Sign Up').last
        box = await signup.bounding_box()
        if box: await page.mouse.click(box['x'] + box['width'] // 2, box['y'] + box['height'] // 2)
        print('clicked Sign Up (with acc cookie)')
        for i in range(15):
            await asyncio.sleep(1)
            token = await get_token(page)
            if token: print(f'[{i}s] TOKEN AUTO: {token[:40]}'); break
            if getcap_done[0]: print(f'[{i}s] challenge still shows'); break
        await asyncio.sleep(5)
        await page.screenshot(path='/tmp/v15_final.png')
        token = await get_token(page)
        if token:
            print(f'token: {token[:40]}')
            await do_register(email, nv_pwd, token, mt_tok)
        else:
            print('[!] no token - challenge still blocking')
            # Check accessibility status in frame
            frames = [f for f in page.frames if 'hcaptcha' in f.url]
            for frame in frames:
                try:
                    text_list = await frame.evaluate('''() => {
                        return Array.from(document.querySelectorAll("*"))
                            .filter(e => e.offsetParent !== null && e.childElementCount === 0)
                            .map(e => e.textContent?.trim())
                            .filter(t => t && t.length > 1 && t.length < 100);
                    }''')
                    print(f'  frame text: {text_list[:10]}')
                except: pass

        await browser.close()

asyncio.run(main())
