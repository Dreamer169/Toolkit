#!/usr/bin/env python3
"""
v18: Use Guerrilla Mail for hcaptcha accessibility registration
1. Get temp email from guerrillamail API
2. Register at dashboard.hcaptcha.com/signup?type=accessibility with guerrilla email
3. Get verification email via guerrilla API → click link
4. cookie set (hmt_id linked to real registered account)
5. Use cookie on talordata to bypass visual challenge
6. Register talordata account
"""
import asyncio, re, secrets, string, json, time
import requests as rq, urllib.request, urllib.error

API = 'https://api.talordata.com'
H = {'User-Agent': 'Mozilla/5.0', 'Origin': 'https://dashboard.talordata.com', 'Content-Type': 'application/json'}

# ---- Guerrilla Mail API ----
GML_BASE = 'https://api.guerrillamail.com/ajax.php'
GML_SESS = {}

def gml_get_addr():
    """Get a guerrilla mail address"""
    r = rq.get(GML_BASE, params={'f': 'get_email_address'}, timeout=15)
    d = r.json()
    GML_SESS['sid_token'] = d.get('sid_token', '')
    email = d.get('email_addr', '')
    print(f'GML email: {email}')
    return email

def gml_check(sid=None, seq=0):
    """Check for new emails"""
    sid = sid or GML_SESS.get('sid_token', '')
    r = rq.get(GML_BASE, params={'f': 'check_email', 'seq': seq, 'sid_token': sid}, timeout=15)
    try:
        d = r.json()
        return d.get('list', [])
    except: return []

def gml_fetch(email_id, sid=None):
    """Fetch email content"""
    sid = sid or GML_SESS.get('sid_token', '')
    r = rq.get(GML_BASE, params={'f': 'fetch_email', 'email_id': email_id, 'sid_token': sid}, timeout=15)
    try: return r.json()
    except: return {}

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

async def get_hcap_token(page):
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
        print(f'  [{(i+1)*5}s] waiting activation code...')
    if not email_code: return False
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

async def main():
    # talordata account email (mail.tm)
    chars = string.ascii_lowercase + string.digits
    login = ''.join(secrets.choice(chars) for _ in range(14))
    td_email = f'{login}@deltajohnsons.com'
    mt_pwd = 'P@' + secrets.token_hex(10)
    nv_pwd = 'Aa1!' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    mr('POST', '/accounts', {'address': td_email, 'password': mt_pwd})
    _, tb = mr('POST', '/token', {'address': td_email, 'password': mt_pwd})
    mt_tok = tb.get('token', '')
    print(f'talordata email={td_email}')

    # Guerrilla mail for hcaptcha accessibility
    gml_email = gml_get_addr()

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
            elif 'getcaptcha' in url: getcap_done[0] = True; print('  [getcap]')
        page.on('response', on_resp)

        # ===== PHASE 1: Register accessibility via Guerrilla Mail =====
        print('\n=== PHASE 1: hcaptcha accessibility registration ===')
        # Go directly to accessibility sign-up page
        acc_page = await ctx.new_page()
        await acc_page.goto('https://dashboard.hcaptcha.com/signup?type=accessibility&hl=en',
                            wait_until='domcontentloaded', timeout=20000)
        await asyncio.sleep(3)
        await acc_page.screenshot(path='/tmp/v18_acc_page.png')
        print(f'acc page: {acc_page.url}')

        # Fill email with guerrilla mail address
        try:
            inp = await acc_page.query_selector('input[type="email"], input[type="text"], input:not([type])')
            if inp:
                await inp.fill(gml_email)
                print(f'filled GML email: {gml_email}')
                await asyncio.sleep(0.5)
                # Click Submit
                btn = await acc_page.query_selector('button[type="submit"], button:has-text("Submit"), button')
                if btn:
                    text = await btn.text_content()
                    print(f'clicking: {text}')
                    await btn.click()
                    await asyncio.sleep(3)
                    await acc_page.screenshot(path='/tmp/v18_acc_submitted.png')
                    page_text = await acc_page.evaluate('() => document.body.innerText')
                    print(f'acc page after submit: {page_text[:300]}')
        except Exception as e:
            print(f'acc form err: {e}')
            await acc_page.screenshot(path='/tmp/v18_acc_err.png')

        # Wait for guerrilla mail verification email
        print('\nWaiting for hcaptcha verification email (Guerrilla Mail)...')
        hcap_link = None
        start = time.time()
        last_seen_ids = set()
        while time.time() - start < 90:
            emails = gml_check()
            for em in emails:
                eid = em.get('mail_id', '')
                if eid in last_seen_ids: continue
                last_seen_ids.add(eid)
                from_addr = em.get('mail_from', '')
                subj = em.get('mail_subject', '')
                print(f'  email: from={from_addr} subj={subj}')
                if 'hcaptcha' in from_addr.lower() or 'hcaptcha' in subj.lower() or 'access' in subj.lower():
                    full = gml_fetch(eid)
                    body = str(full.get('mail_body', '')) + str(full.get('mail_body_html', ''))
                    urls = re.findall(r'https?://[^\s<>"\']+', body)
                    hcap_urls = [u for u in urls if 'hcaptcha' in u.lower()]
                    print(f'  hcaptcha URLs: {hcap_urls}')
                    if hcap_urls:
                        hcap_link = hcap_urls[0]
                        break
            if hcap_link: break
            await asyncio.sleep(5)
            print(f'  [{int(time.time()-start)}s] waiting...')

        if hcap_link:
            print(f'Verification link: {hcap_link}')
            # Visit verification link in browser to set cookie
            verify_page = await ctx.new_page()
            await verify_page.goto(hcap_link, wait_until='domcontentloaded', timeout=20000)
            await asyncio.sleep(3)
            await verify_page.screenshot(path='/tmp/v18_verified.png')
            print(f'verify page: {verify_page.url}')
            page_text = await verify_page.evaluate('() => document.body.innerText')
            print(f'verify page text: {page_text[:200]}')
            await verify_page.close()
        else:
            print('[!] no hcaptcha verification email via GML')
            # Try direct cookies
            cookies = await ctx.cookies()
            hmt = [c for c in cookies if c.get('name') == 'hmt_id']
            print(f'hmt_id cookies: {len(hmt)}')

        await acc_page.close()

        # ===== PHASE 2: Check accessibility cookie status =====
        print('\n=== PHASE 2: Check cookie status ===')
        cookies = await ctx.cookies()
        hmt_cookies = [c for c in cookies if c.get('name') in ('hmt_id', 'hc_accessibility')]
        print(f'accessibility cookies: {len(hmt_cookies)}')
        for c in hmt_cookies:
            print(f'  {c.get("name")}: {c.get("value","")[:40]} @ {c.get("domain")}')

        # ===== PHASE 3: Try talordata registration with accessibility cookie =====
        print('\n=== PHASE 3: Talordata registration with acc cookie ===')
        await page.goto('https://dashboard.talordata.com/reg', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(5)
        await page.fill('input[placeholder*="email" i]', td_email)
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

        token = None
        for i in range(12):
            await asyncio.sleep(1)
            token = await get_hcap_token(page)
            if token: print(f'[{i}s] TOKEN AUTO: {token[:40]}'); break
            if getcap_done[0]: print(f'[{i}s] challenge triggered (cookie not working)'); break

        await asyncio.sleep(5)
        await page.screenshot(path='/tmp/v18_reg_state.png')
        token = await get_hcap_token(page)

        # If still no token and challenge appeared, try clicking ≡ → check status
        if not token and getcap_done[0]:
            frames = [f for f in page.frames if 'hcaptcha' in f.url]
            challenge_box = {'x': 546, 'y': 163.9, 'width': 520, 'height': 570}
            for frame in frames:
                try:
                    h = await frame.frame_element()
                    b = await h.bounding_box()
                    if b and b.get('width', 0) > 300: challenge_box = b; break
                except: pass
            tx = challenge_box['x'] + challenge_box['width'] * (28/520)
            ty = challenge_box['y'] + challenge_box['height'] * (541/570)
            await page.mouse.click(tx, ty); await asyncio.sleep(1.5)
            for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
                try:
                    el = await frame.query_selector('[aria-label*="Accessibility Cookie"]')
                    if el: await el.click(); print('clicked Accessibility Cookie'); await asyncio.sleep(2)
                    # Check status
                    texts = await frame.evaluate('''() => Array.from(document.querySelectorAll("*"))
                        .filter(e => e.offsetParent !== null && e.childElementCount === 0)
                        .map(e => e.textContent?.trim()).filter(t => t && t.length > 1 && t.length < 100)''')
                    print(f'  acc dialog texts: {[t for t in texts if t.strip()][:8]}')
                    break
                except: pass

        if token:
            await do_register(td_email, nv_pwd, token, mt_tok)
        else:
            print('[!] no token')

        await browser.close()

asyncio.run(main())
