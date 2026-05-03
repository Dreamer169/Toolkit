#!/usr/bin/env python3
"""
v13: After accessibility cookie → challenge auto-verifies
Strategy:
1. Click Sign Up → challenge appears
2. Click ≡ → Accessibility Cookie → cookie set (challenge closes)  
3. Reload page, fill form again
4. Click Sign Up → widget auto-checks (no challenge)
5. Get token → register
Also try: visit accounts.hcaptcha.com to pre-register accessibility
"""
import asyncio, re, secrets, string, subprocess, tempfile, json
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

async def fill_and_submit(page, email, nv_pwd):
    """Fill registration form and click Sign Up"""
    await page.fill('input[placeholder*="email" i]', email)
    await page.fill('input[type="password"]', nv_pwd)
    try: await page.fill('input[placeholder*="invitation" i]', 'z46vzbz4')
    except: pass
    cb = await page.query_selector('.el-checkbox__inner')
    if cb:
        box = await cb.bounding_box()
        if box:
            await page.mouse.click(box['x'] + 3, box['y'] + 3)
            await asyncio.sleep(0.3)
    signup = page.get_by_text('Sign Up').last
    box = await signup.bounding_box()
    if box:
        await page.mouse.click(box['x'] + box['width'] // 2, box['y'] + box['height'] // 2)
        return True
    return False

async def try_register(email, nv_pwd, token, mt_tok):
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
        print(f'  [{(i+1)*5}s] waiting...')
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

async def main():
    chars = string.ascii_lowercase + string.digits
    login = ''.join(secrets.choice(chars) for _ in range(14))
    email = f'{login}@deltajohnsons.com'
    mt_pwd = 'P@' + secrets.token_hex(10)
    nv_pwd = 'Aa1!' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    mr('POST', '/accounts', {'address': email, 'password': mt_pwd})
    _, tb = mr('POST', '/token', {'address': email, 'password': mt_pwd})
    mt_tok = tb.get('token', '')
    print(f'email={email} pass={nv_pwd}')

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

        async def on_resp(r):
            url = r.url
            if 'api.talordata' in url:
                try:
                    d = await r.json()
                    if d.get('code'): print(f'  [API] {d.get("code")} {d.get("message")}')
                except: pass
            elif 'getcaptcha' in url:
                getcap_done[0] = True; print('  [getcap]')
            elif 'checkcaptcha' in url or 'siteverify' in url:
                try: d = await r.json(); print(f'  [verify] {str(d)[:80]}')
                except: pass
        page.on('response', on_resp)

        # ===== PHASE 1: Get accessibility cookie =====
        print('\n=== PHASE 1: Get accessibility cookie ===')
        await page.goto('https://dashboard.talordata.com/reg', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(5)
        await fill_and_submit(page, email, nv_pwd)
        print('clicked Sign Up (pass 1)')
        for i in range(15):
            await asyncio.sleep(1)
            if getcap_done[0]: print(f'[{i}s] challenge loaded'); break
        await asyncio.sleep(4)

        # Get challenge frame position
        frames = [f for f in page.frames if 'hcaptcha' in f.url]
        challenge_box = {'x': 546, 'y': 163.9, 'width': 520, 'height': 570}
        for frame in frames:
            try:
                h = await frame.frame_element()
                b = await h.bounding_box()
                if b and b.get('width', 0) > 300:
                    challenge_box = b; break
            except: pass

        # Click ≡ to open accessibility menu
        tx = challenge_box['x'] + challenge_box['width'] * (28/520)
        ty = challenge_box['y'] + challenge_box['height'] * (541/570)
        print(f'clicking ≡ at ({tx:.0f},{ty:.0f})')
        await page.mouse.click(tx, ty)
        await asyncio.sleep(1.5)

        # Click Accessibility Cookie option
        clicked_acc = False
        for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
            try:
                el = await frame.query_selector('[aria-label*="Accessibility Cookie"]')
                if not el:
                    el = await frame.query_selector('[aria-label*="accessibility" i]')
                if el:
                    await el.click()
                    clicked_acc = True
                    print('clicked Accessibility Cookie')
                    break
            except: pass

        if not clicked_acc:
            # Try position above ≡
            await page.mouse.click(tx, ty - 75)
            print(f'position click at ({tx:.0f},{ty-75:.0f})')

        await asyncio.sleep(3)
        await page.screenshot(path='/tmp/v13_after_acc.png')

        # Check cookies
        all_cookies = await ctx.cookies()
        hcap_cookies = [c for c in all_cookies if 'hcaptcha' in (c.get('domain','') + c.get('name','') + c.get('path','')).lower()]
        print(f'hcap cookies after acc: {len(hcap_cookies)}')
        for c in hcap_cookies:
            print(f'  {c.get("name")}: {str(c.get("value",""))[:40]}')

        # Check for token already
        token = await get_hcap_token(page)
        if token:
            print(f'TOKEN from phase1: {token[:40]}')
            success = await try_register(email, nv_pwd, token, mt_tok)
            if success: await browser.close(); return

        # ===== PHASE 2: Reload and try again with cookie =====
        print('\n=== PHASE 2: Reload with accessibility cookie ===')
        getcap_done[0] = False
        await page.goto('https://dashboard.talordata.com/reg', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(5)
        await page.screenshot(path='/tmp/v13_reload.png')

        # Click Sign Up with cookies already set
        await fill_and_submit(page, email, nv_pwd)
        print('clicked Sign Up (pass 2)')

        # Wait - with accessibility cookie, challenge might auto-pass
        for i in range(10):
            await asyncio.sleep(1)
            token = await get_hcap_token(page)
            if token: print(f'[{i}s] token auto: {token[:40]}'); break
            if getcap_done[0]: print(f'[{i}s] challenge triggered'); break

        await asyncio.sleep(3)
        await page.screenshot(path='/tmp/v13_pass2.png')

        token = await get_hcap_token(page)
        if token:
            print(f'TOKEN from phase2: {token[:40]}')
            success = await try_register(email, nv_pwd, token, mt_tok)
            if success: await browser.close(); return

        # ===== PHASE 3: Visit accounts.hcaptcha.com accessibility registration =====
        print('\n=== PHASE 3: hcaptcha accessibility registration ===')
        # accounts.hcaptcha.com provides accessibility cookies for screen reader users
        acc_page = await ctx.new_page()
        await acc_page.goto('https://accounts.hcaptcha.com/', wait_until='domcontentloaded', timeout=20000)
        await asyncio.sleep(3)
        await acc_page.screenshot(path='/tmp/v13_accounts_hcap.png')
        print(f'accounts.hcaptcha.com: {acc_page.url}')
        # List form elements
        try:
            forms = await acc_page.evaluate('''() => {
                const els = Array.from(document.querySelectorAll("input,button,a,[role=button]"));
                return els.filter(e=>e.offsetParent!==null).map(e=>({
                    tag:e.tagName, type:e.type, text:e.textContent?.trim()?.substring(0,30),
                    placeholder:e.placeholder, name:e.name
                }));
            }''')
            print(f'  form elements: {forms[:8]}')
        except Exception as e:
            print(f'  eval err: {e}')
        await acc_page.close()

        # Final check for hcaptcha cookies
        all_cookies = await ctx.cookies()
        hcap_cookies = [c for c in all_cookies if 'hcaptcha' in (c.get('domain','') + c.get('name','')).lower() or c.get('name','').startswith('hc_')]
        print(f'Final hcap cookies: {len(hcap_cookies)}')
        for c in hcap_cookies:
            print(f'  {c.get("name")}: {str(c.get("value",""))[:40]} domain={c.get("domain")}')

        # ===== PHASE 4: Try with pre-loaded hcaptcha cookies from accounts.hcaptcha.com =====
        # Try submitting form one more time
        print('\n=== PHASE 4: Final attempt ===')
        getcap_done[0] = False
        await page.goto('https://dashboard.talordata.com/reg', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(5)
        await fill_and_submit(page, email, nv_pwd)
        print('clicked Sign Up (pass 3)')

        for i in range(15):
            await asyncio.sleep(1)
            token = await get_hcap_token(page)
            if token: print(f'[{i}s] TOKEN: {token[:40]}'); break
            if getcap_done[0]:
                print(f'[{i}s] challenge loaded again')
                # Try to click widget checkbox directly
                frames = [f for f in page.frames if 'hcaptcha' in f.url]
                for frame in frames:
                    try:
                        # Click the widget's checkbox
                        div = await frame.query_selector('div[tabindex]')
                        if div:
                            await div.click()
                            print('  clicked widget div[tabindex]')
                            break
                    except: pass
                break

        await asyncio.sleep(5)
        await page.screenshot(path='/tmp/v13_final.png')
        token = await get_hcap_token(page)
        if token:
            print(f'TOKEN final: {token[:40]}')
            await try_register(email, nv_pwd, token, mt_tok)
        else:
            print('[!] no token')

        await browser.close()

asyncio.run(main())
