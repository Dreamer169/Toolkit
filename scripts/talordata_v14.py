#!/usr/bin/env python3
"""
v14: 
1. ≡ → Accessibility Cookie → dialog shows "Retrieve accessibility cookie." link
2. Click the link → new page opens at accounts.hcaptcha.com
3. Complete accessibility registration there
4. Cookie set (Status: ✅)
5. Close dialog → challenge auto-passes
6. Get token → register
"""
import asyncio, re, secrets, string, json
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
        print(f'  [{(i+1)*5}s] waiting...')
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
        new_pages = []
        ctx.on('page', lambda pg: new_pages.append(pg))

        async def on_resp(r):
            url = r.url
            if 'api.talordata' in url:
                try:
                    d = await r.json()
                    if d.get('code'): print(f'  [API] {d.get("code")} {d.get("message")}')
                except: pass
            elif 'getcaptcha' in url:
                getcap_done[0] = True
                print('  [getcap]')
            elif 'checkcaptcha' in url or 'siteverify' in url:
                try: d = await r.json(); print(f'  [verify] {str(d)[:80]}')
                except: pass
        page.on('response', on_resp)

        # ----- Load page, fill form, click Sign Up -----
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

        # ----- Get challenge frame position -----
        frames = [f for f in page.frames if 'hcaptcha' in f.url]
        challenge_box = {'x': 546, 'y': 163.9, 'width': 520, 'height': 570}
        for frame in frames:
            try:
                h = await frame.frame_element()
                b = await h.bounding_box()
                if b and b.get('width', 0) > 300:
                    challenge_box = b; break
            except: pass
        print(f'challenge_box: {challenge_box}')

        # ----- Step 1: Click ≡ to open accessibility menu -----
        tx = challenge_box['x'] + challenge_box['width'] * (28/520)
        ty = challenge_box['y'] + challenge_box['height'] * (541/570)
        print(f'clicking ≡ at ({tx:.0f},{ty:.0f})')
        await page.mouse.click(tx, ty)
        await asyncio.sleep(1.5)

        # ----- Step 2: Click "Accessibility Cookie" from ≡ menu -----
        for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
            try:
                el = await frame.query_selector('[aria-label*="Accessibility Cookie"]')
                if el: await el.click(); print('clicked Accessibility Cookie from menu'); break
            except: pass
        await asyncio.sleep(2)

        # ----- Step 3: Find "Retrieve accessibility cookie." link in dialog -----
        await page.screenshot(path='/tmp/v14_dialog.png')
        retrieve_clicked = False
        for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
            try:
                # Look for the "Retrieve accessibility cookie." link
                for sel in [
                    'a[href*="accounts.hcaptcha"]',
                    'a:has-text("Retrieve")',
                    '[data-cy*="retrieve"]',
                    'a',
                ]:
                    els = await frame.query_selector_all(sel)
                    for el in els:
                        text = await el.text_content()
                        if text and ('retrieve' in text.lower() or 'accessibility' in text.lower()):
                            href = await el.get_attribute('href')
                            print(f'found retrieve link: "{text.strip()}" href={href}')
                            await el.click()
                            retrieve_clicked = True
                            print('clicked Retrieve link!')
                            break
                    if retrieve_clicked: break
            except Exception as e:
                print(f'  frame err: {e}')

        if not retrieve_clicked:
            # Try clicking in the dialog area
            # Dialog appears at center of the challenge frame area
            dlg_x = challenge_box['x'] + challenge_box['width'] * 0.35
            dlg_y = challenge_box['y'] + challenge_box['height'] * 0.75
            await page.mouse.click(dlg_x, dlg_y)
            print(f'position click at ({dlg_x:.0f},{dlg_y:.0f})')

        # Wait for new page
        await asyncio.sleep(3)
        await page.screenshot(path='/tmp/v14_after_retrieve.png')

        if new_pages:
            acc_page = new_pages[-1]
            try:
                await acc_page.wait_for_load_state('domcontentloaded', timeout=15000)
                await asyncio.sleep(2)
            except: pass
            acc_url = acc_page.url
            print(f'accounts page: {acc_url}')
            await acc_page.screenshot(path='/tmp/v14_acc_page.png')

            # Inspect the accessibility registration page
            try:
                elems = await acc_page.evaluate('''() => {
                    const all = Array.from(document.querySelectorAll("*")).filter(e => e.offsetParent !== null);
                    return all.filter(e => e.childElementCount === 0 && e.textContent?.trim().length > 0)
                        .map(e => ({tag: e.tagName, text: e.textContent?.trim()?.substring(0,50), class: e.className?.substring(0,20)}))
                        .slice(0, 20);
                }''')
                print(f'acc page content: {elems}')
            except Exception as e:
                print(f'acc page eval err: {e}')

            # Check cookies before and after
            cookies_before = await ctx.cookies()
            hmt_before = [c for c in cookies_before if c.get('name') == 'hmt_id']
            print(f'hmt_id before: {len(hmt_before)} cookies')

            # The page might require email input - fill it
            try:
                inp = await acc_page.query_selector('input[type="email"], input[type="text"]')
                if inp:
                    await inp.fill(email)
                    print(f'filled email on acc page: {email}')
                    # Submit
                    btn = await acc_page.query_selector('button[type="submit"], button')
                    if btn:
                        text = await btn.text_content()
                        print(f'submit btn: {text}')
                        await btn.click()
                        await asyncio.sleep(3)
            except Exception as e:
                print(f'acc form err: {e}')

            await asyncio.sleep(3)
            await acc_page.screenshot(path='/tmp/v14_acc_after_submit.png')

            cookies_after = await ctx.cookies()
            hmt_after = [c for c in cookies_after if c.get('name') == 'hmt_id']
            print(f'hmt_id after: {len(hmt_after)} cookies')
            if hmt_after:
                print(f'  hmt_id value: {hmt_after[0].get("value","")[:40]}')

            await acc_page.close()
            await asyncio.sleep(1)

        # ----- Step 4: Check if accessibility dialog shows Status: ✅ -----
        await page.screenshot(path='/tmp/v14_main_after_acc.png')
        for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
            try:
                status = await frame.evaluate('''() => {
                    const els = Array.from(document.querySelectorAll("*")).filter(e=>e.offsetParent!==null);
                    return els.filter(e => e.childElementCount===0 && e.textContent?.trim().length > 0)
                        .map(e => e.textContent?.trim()).filter(t => t.length > 1 && t.length < 100);
                }''')
                print(f'  frame visible text: {status[:10]}')
            except: pass

        # ----- Step 5: Close dialog (click X or outside) -----
        # Look for X button to close the accessibility dialog
        for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
            try:
                close_btn = await frame.query_selector('[aria-label*="close" i], [aria-label*="Close" i], button.close, .close, [data-cy="close"]')
                if close_btn:
                    await close_btn.click()
                    print('closed accessibility dialog via button')
                    break
                # Try click the × text
                els = await frame.query_selector_all('button, [role=button]')
                for el in els:
                    t = await el.text_content()
                    if t and ('×' in t or 'x' in t.lower() or 'close' in t.lower()):
                        await el.click()
                        print(f'closed via: {t}')
                        break
            except: pass

        await asyncio.sleep(2)
        await page.screenshot(path='/tmp/v14_dialog_closed.png')

        # ----- Step 6: Wait for auto-verification with cookie -----
        token = None
        for i in range(10):
            await asyncio.sleep(1)
            token = await get_token(page)
            if token: print(f'[{i}s] TOKEN: {token[:40]}'); break

        # ----- Step 7: If no token, reload and retry -----
        if not token:
            print('[*] Reloading with accessibility cookie set...')
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
            print('clicked Sign Up (reload)')
            for i in range(12):
                await asyncio.sleep(1)
                token = await get_token(page)
                if token: print(f'[{i}s] token on reload: {token[:40]}'); break
                if getcap_done[0]: print(f'[{i}s] challenge again'); break
            await asyncio.sleep(3)
            await page.screenshot(path='/tmp/v14_reload_state.png')
            token = await get_token(page)

        if token:
            await do_register(email, nv_pwd, token, mt_tok)
        else:
            print('[!] no token')
            print('all cookies:')
            for c in await ctx.cookies():
                if 'hcaptcha' in (c.get('domain','') + c.get('name','')).lower() or c.get('name','').startswith('hmt'):
                    print(f'  {c.get("name")} @ {c.get("domain")}: {str(c.get("value",""))[:30]}')

        await browser.close()

asyncio.run(main())
