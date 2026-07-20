#!/usr/bin/env python3
"""
v12: Click ≡ → Accessibility Cookie → bypass challenge
hcaptcha accessibility cookie bypasses visual challenge for registered users
"""
import asyncio, re, secrets, string, subprocess, tempfile, json, os
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

async def try_register(email, nv_pwd, token, mt_tok, loop_page=None):
    """Full registration flow given a hcaptcha token"""
    r = rq.post(f'{API}/users/v1/auth/send_activation', json={'email': email, 'hcaptcha': token}, headers=H, timeout=10)
    d = r.json()
    print(f'send_activation: code={d.get("code")} msg={d.get("message")}')
    if d.get('code') != 0:
        return False
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
        print(f'  [{(i+1)*5}s] waiting email...')
    if not email_code:
        print('no email code received')
        return False
    print(f'email code: {email_code}')
    r2 = rq.post(f'{API}/users/v1/auth/register', json={'email': email, 'password': nv_pwd, 'code': email_code}, headers=H, timeout=10)
    d2 = r2.json()
    print(f'register: code={d2.get("code")} msg={d2.get("message")}')
    jwt = d2.get('data', '') if isinstance(d2.get('data'), str) else (d2.get('data') or {}).get('token', '')
    if not jwt:
        return False
    r3 = rq.get(f'{API}/users/v1/auth/activation', params={'token': jwt}, headers={k: v for k, v in H.items() if k != 'Content-Type'}, timeout=10)
    d3 = r3.json()
    print(f'activation: code={d3.get("code")} msg={d3.get("message")}')
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
        except Exception as e: print(f'DB err: {e}')
        return True
    return False

async def get_token(page):
    """Try to get hcaptcha token from page"""
    for _ in range(2):
        try:
            tok = await page.evaluate(
                '() => { var e=document.querySelector("[name=\'h-captcha-response\']"); '
                'if(e&&e.value&&e.value.length>20) return e.value; '
                'if(typeof hcaptcha!="undefined"){var r=hcaptcha.getResponse(); if(r&&r.length>20) return r;} '
                'return null; }'
            )
            if tok: return tok
        except: pass
        await asyncio.sleep(1)
    return None

async def main():
    chars = string.ascii_lowercase + string.digits
    login = ''.join(secrets.choice(chars) for _ in range(14))
    email = f'{login}@deltajohnsons.com'
    mt_pwd = 'P@' + secrets.token_hex(10)
    nv_pwd = 'Aa1!' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    mr('POST', '/accounts', {'address': email, 'password': mt_pwd})
    _, tb = mr('POST', '/token', {'address': email, 'password': mt_pwd})
    mt_tok = tb.get('token', '')
    print(f'email={email}')

    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    stealth = Stealth()
    getcap_done = [False]
    token_captured = [None]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
        )
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
                    print(f'  [API] {d.get("code")} {d.get("message")}')
                except: pass
            elif 'getcaptcha' in url:
                getcap_done[0] = True
                print('  [challenge loaded]')
            elif 'checkcaptcha' in url or 'verify' in url.lower():
                try:
                    d = await r.json()
                    print(f'  [VERIFY] {str(d)[:100]}')
                except: pass
        page.on('response', on_resp)

        # Handle new pages (accessibility cookie page opens in new tab)
        new_pages = []
        ctx.on('page', lambda p: new_pages.append(p))

        await page.goto('https://dashboard.talordata.com/reg', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(5)
        await page.fill('input[placeholder*="email" i]', email)
        await page.fill('input[type="password"]', nv_pwd)
        try: await page.fill('input[placeholder*="invitation" i]', 'z46vzbz4')
        except: pass
        cb = await page.query_selector('.el-checkbox__inner')
        if cb:
            box = await cb.bounding_box()
            if box: await page.mouse.click(box['x'] + 3, box['y'] + 3); await asyncio.sleep(0.4)

        signup = page.get_by_text('Sign Up').last
        box = await signup.bounding_box()
        if box: await page.mouse.click(box['x'] + box['width'] // 2, box['y'] + box['height'] // 2)
        print('clicked Sign Up')

        for i in range(20):
            await asyncio.sleep(1)
            if getcap_done[0]: print(f'[{i}s] challenge loaded'); break
        await asyncio.sleep(5)

        # Step 2: Click ≡ button (confirmed at 574, 705)
        frames = [f for f in page.frames if 'hcaptcha' in f.url]
        challenge_box = None
        for frame in frames:
            try:
                handle = await frame.frame_element()
                box = await handle.bounding_box()
                if box and box.get('width', 0) > 300:
                    challenge_box = box; break
            except: pass
        if not challenge_box:
            challenge_box = {'x': 546, 'y': 163.9, 'width': 520, 'height': 570}

        three_x = challenge_box['x'] + challenge_box['width'] * (28/520)
        three_y = challenge_box['y'] + challenge_box['height'] * (541/570)
        print(f'[*] clicking ≡ at ({three_x:.1f},{three_y:.1f})')
        await page.mouse.move(three_x, three_y, steps=5)
        await asyncio.sleep(0.3)
        await page.mouse.click(three_x, three_y)
        await asyncio.sleep(1.5)

        # Step 3: Click "Accessibility Cookie" menu item
        # DOM shows: div[aria-label="Retrieve hCaptcha Accessibility Cookie, 1 of 4"]
        acc_clicked = False
        for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
            try:
                el = await frame.query_selector('[aria-label*="Accessibility Cookie"]')
                if el:
                    await el.click()
                    acc_clicked = True
                    print('[*] clicked Accessibility Cookie')
                    break
                # Also try by text
                el2 = await frame.get_by_text('Accessibility Cookie').first
                if el2:
                    await el2.click()
                    acc_clicked = True
                    print('[*] clicked Accessibility Cookie (by text)')
                    break
            except Exception as e:
                print(f'  acc click err: {e}')

        if not acc_clicked:
            print('[!] Accessibility Cookie not found in DOM, trying by position')
            # Menu item 1 is above the ≡ button; each item ~30px tall
            acc_x = three_x
            acc_y = three_y - 80  # menu items stack upward
            await page.mouse.click(acc_x, acc_y)
            print(f'  clicked at ({acc_x:.0f},{acc_y:.0f})')

        await asyncio.sleep(2)

        # Wait for new tab (accessibility page)
        for i in range(8):
            await asyncio.sleep(1)
            if new_pages: print(f'[{i}s] new page opened: {new_pages[-1].url}'); break

        await page.screenshot(path='/tmp/v12_after_acc.png')

        if new_pages:
            acc_page = new_pages[-1]
            await acc_page.wait_for_load_state('domcontentloaded', timeout=15000)
            print(f'acc page URL: {acc_page.url}')
            await acc_page.screenshot(path='/tmp/v12_acc_page.png')

            # The accessibility page should set a cookie
            # After it's done, close it and the main page should auto-pass
            await asyncio.sleep(3)
            # Check cookies
            cookies = await ctx.cookies()
            hcap_cookies = [c for c in cookies if 'hcaptcha' in c.get('domain', '').lower()]
            print(f'hcap cookies: {hcap_cookies}')

            # Look for a submit/confirm button on the accessibility page
            try:
                btns = await acc_page.query_selector_all('button, [role=button], input[type=submit]')
                for btn in btns:
                    text = await btn.text_content()
                    if text: print(f'  acc page btn: {text.strip()[:30]}')
            except: pass

            # Close accessibility page
            await acc_page.close()
            await asyncio.sleep(2)

        # Check if the main page challenge auto-passed
        await page.screenshot(path='/tmp/v12_after_acc_close.png')
        token = await get_token(page)
        print(f'token after acc: {token[:40] if token else None}')

        # If no token, try clicking Submit/Verify in the challenge frame
        if not token:
            # The challenge might auto-verify after getting accessibility cookie
            # Try refreshing challenge
            for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
                try:
                    # Check challenge state
                    state = await frame.evaluate('''() => {
                        const btns = Array.from(document.querySelectorAll("button")).map(b=>({text:b.textContent?.trim()?.substring(0,20), aria:b.getAttribute("aria-label")}));
                        const inputs = Array.from(document.querySelectorAll("input")).map(i=>({type:i.type,name:i.name,value:i.value?.substring(0,20)}));
                        return {btns, inputs, title: document.title};
                    }''')
                    print(f'  frame state: {state}')
                except: pass

            # Try to click verify/submit button in challenge frame
            for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
                try:
                    for sel in ['button[type="submit"]', '#submit-btn', '[data-cy="submit"]', 'button:last-of-type']:
                        btn = await frame.query_selector(sel)
                        if btn:
                            text = await btn.text_content()
                            print(f'  clicking submit: {text}')
                            await btn.click()
                            await asyncio.sleep(3)
                            break
                except: pass

            await asyncio.sleep(3)
            token = await get_token(page)
            print(f'token after submit: {token[:40] if token else None}')

        # If STILL no token - try the full audio approach via URL direct call
        if not token:
            print('[*] trying direct accessibility cookie URL approach...')
            # hcaptcha accessibility cookie URL
            acc_url = 'https://accounts.hcaptcha.com/demo?sitekey=aef235d8-778c-4db6-ba04-dddef1fa9916'
            acc_p2 = await ctx.new_page()
            try:
                await acc_p2.goto(acc_url, wait_until='domcontentloaded', timeout=15000)
                await asyncio.sleep(3)
                await acc_p2.screenshot(path='/tmp/v12_acc_direct.png')
                print(f'acc direct URL: {acc_p2.url}')
                # Check if cookie was set
                cookies = await ctx.cookies('https://hcaptcha.com')
                print(f'hcap cookies: {len(cookies)}')
                await acc_p2.close()
            except Exception as e:
                print(f'acc direct err: {e}')
                try: await acc_p2.close()
                except: pass

        if token:
            await try_register(email, nv_pwd, token, mt_tok)
        else:
            print('[!] no token obtained')
            await page.screenshot(path='/tmp/v12_final.png')

        await browser.close()

asyncio.run(main())
