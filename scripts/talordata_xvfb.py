#!/usr/bin/env python3
"""
talordata /reg 注册 — Xvfb非headless + 标准playwright + Stealth
用虚拟显示器骗过hcaptcha的bot检测
"""
import asyncio, json, os, re, secrets, string, subprocess, time
import requests as rq
import urllib.request, urllib.error

API = 'https://api.talordata.com'
H = {'User-Agent':'Mozilla/5.0','Origin':'https://dashboard.talordata.com','Content-Type':'application/json'}

def mr(method, path, data=None, token=None):
    url = 'https://api.mail.tm' + path
    body = json.dumps(data).encode() if data else None
    h = {'Content-Type':'application/json','Accept':'application/json'}
    if token: h['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(); return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as ex: return 0, {'error':str(ex)}

async def main():
    # Create mail.tm inbox
    chars = string.ascii_lowercase + string.digits
    login = ''.join(secrets.choice(chars) for _ in range(14))
    email = f'{login}@deltajohnsons.com'
    mt_pwd = 'P@' + secrets.token_hex(10)
    nv_pwd = 'Aa1!' + ''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(12))
    mr('POST', '/accounts', {'address':email,'password':mt_pwd})
    _, tb = mr('POST', '/token', {'address':email,'password':mt_pwd})
    mt_tok = tb.get('token','')
    print(f'[*] email={email}')
    
    api_calls = []
    hcap_token = {'v': None}
    
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        # Launch NON-headless (display=:99 from Xvfb)
        browser = await p.chromium.launch(
            headless=False,
            args=[
                '--no-sandbox','--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--window-size=1280,900',
            ]
        )
        ctx = await browser.new_context(
            viewport={'width':1280,'height':900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='en-US',
        )
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
        """)
        page = await ctx.new_page()
        
        async def on_req(req):
            if 'api.talordata' in req.url:
                body = req.post_data or ''
                api_calls.append({'url':req.url,'method':req.method,'body':body})
                print(f'  [REQ] {req.method} {req.url[-60:]} | {body[:80]}')
        async def on_resp(resp):
            if 'api.talordata' in resp.url:
                try:
                    d = await resp.json()
                    print(f'  [RESP] {resp.status} {resp.url[-50:]}: code={d.get("code")} msg={d.get("message")}')
                except: pass
        page.on('request', on_req)
        page.on('response', on_resp)
        
        print('[*] loading /reg page (non-headless with Xvfb)...')
        await page.goto('https://dashboard.talordata.com/reg', wait_until='networkidle', timeout=35000)
        await asyncio.sleep(4)
        print(f'[*] title: {await page.title()}')
        
        # Fill form with realistic typing
        await page.click('input[placeholder*="email" i]')
        await asyncio.sleep(0.3)
        await page.type('input[placeholder*="email" i]', email, delay=50)
        await asyncio.sleep(0.5)
        
        await page.click('input[type="password"]')
        await asyncio.sleep(0.2)
        await page.type('input[type="password"]', nv_pwd, delay=60)
        await asyncio.sleep(0.4)
        
        try:
            inv_visible = await page.is_visible('input[placeholder*="invitation" i]', timeout=1000)
            if inv_visible:
                await page.click('input[placeholder*="invitation" i]')
                await page.type('input[placeholder*="invitation" i]', 'z46vzbz4', delay=80)
        except: pass
        await asyncio.sleep(0.5)
        
        # Click checkbox with real mouse
        cb_inner = await page.query_selector('.el-checkbox__inner')
        if cb_inner:
            box = await cb_inner.bounding_box()
            if box:
                cx, cy = box['x']+box['width']//2, box['y']+box['height']//2
                await page.mouse.move(cx-10, cy, steps=5)
                await asyncio.sleep(0.2)
                await page.mouse.move(cx, cy, steps=3)
                await asyncio.sleep(0.1)
                await page.mouse.click(cx, cy)
                await asyncio.sleep(0.5)
                print('[*] checkbox clicked via mouse')
        
        # Verify checkbox via Vue reactivity (click the outer label if inner didn't work)
        checked = await page.evaluate("() => document.querySelector('input[type=\"checkbox\"]')?.checked")
        if not checked:
            label = await page.query_selector('.el-checkbox')
            if label:
                await label.click()
                await asyncio.sleep(0.3)
                checked = await page.evaluate("() => document.querySelector('input[type=\"checkbox\"]')?.checked")
        print(f'[*] checkbox checked: {checked}')
        
        await asyncio.sleep(1)
        
        # Check button state
        btn_info = await page.evaluate("""() => {
            const btn = document.querySelector('.el-button--primary');
            return {disabled: btn?.disabled, class: btn?.className, text: btn?.textContent?.trim()};
        }""")
        print(f'[*] btn info: {btn_info}')
        
        # Click Sign Up with realistic mouse movement
        btn = await page.query_selector('.el-button--primary')
        if btn:
            box = await btn.bounding_box()
            if box:
                cx, cy = box['x']+box['width']//2, box['y']+box['height']//2
                await page.mouse.move(cx+20, cy-10, steps=8)
                await asyncio.sleep(0.3)
                await page.mouse.move(cx, cy, steps=5)
                await asyncio.sleep(0.2)
                await page.mouse.click(cx, cy)
                print('[*] Sign Up clicked via mouse')
        
        # Wait and monitor hcaptcha
        print('[*] waiting for hcaptcha/API response...')
        for i in range(30):
            await asyncio.sleep(1)
            hcap_frames = [f for f in page.frames if 'hcaptcha' in f.url]
            if hcap_frames or api_calls:
                print(f'[{i}s] hcap_frames={len(hcap_frames)} api_calls={len(api_calls)}')
                for f in hcap_frames:
                    print(f'  frame: {f.url[:80]}')
                break
            if i % 5 == 0:
                print(f'[{i}s] waiting...')
        
        # If hcaptcha appeared, try audio challenge
        hcap_frames = [f for f in page.frames if 'hcaptcha' in f.url]
        if hcap_frames:
            print('[*] hcaptcha appeared! trying audio challenge...')
            import whisper, tempfile
            
            # Find challenge frame
            challenge_frame = next((f for f in page.frames if 'challenge' in f.url and 'hcaptcha' in f.url), None)
            if not challenge_frame:
                challenge_frame = hcap_frames[0]
            
            await asyncio.sleep(2)
            
            # Click audio button
            for sel in ['button[aria-label*="audio" i]', '.challenge-switchtype', '[class*="audio"]']:
                try:
                    el = await challenge_frame.query_selector(sel)
                    if el:
                        await el.click()
                        print(f'[hcap] clicked audio: {sel}')
                        await asyncio.sleep(2)
                        break
                except: pass
            
            # Find audio URL
            audio_url = None
            for _ in range(10):
                await asyncio.sleep(1)
                for frame in page.frames:
                    if 'hcaptcha' not in frame.url: continue
                    try:
                        u = await frame.evaluate('() => document.querySelector("audio")?.src || null')
                        if u and u.startswith('http'):
                            audio_url = u; break
                    except: pass
                if audio_url: break
            
            if audio_url:
                with tempfile.NamedTemporaryFile(suffix='.mp3',delete=False) as f: ap=f.name
                resp = rq.get(audio_url, timeout=20)
                open(ap,'wb').write(resp.content)
                wp = ap.replace('.mp3','.wav')
                subprocess.run(['ffmpeg','-i',ap,'-ar','16000','-ac','1',wp,'-y'],capture_output=True)
                model = whisper.load_model('base')
                result = model.transcribe(wp, language='en')
                text = re.sub(r'[^a-z0-9\s]','',result['text'].strip().lower()).strip()
                print(f'[hcap] whisper: "{text}"')
                
                for frame in page.frames:
                    if 'hcaptcha' not in frame.url: continue
                    try:
                        inp = await frame.query_selector('input[type="text"]')
                        if inp:
                            await inp.fill(text)
                            await asyncio.sleep(0.3)
                            vbtn = await frame.query_selector('button[type="submit"]')
                            if vbtn: await vbtn.click()
                            await asyncio.sleep(3)
                            break
                    except: pass
                
                tok = await page.evaluate('() => document.querySelector("[name=h-captcha-response]")?.value || null')
                if tok:
                    hcap_token['v'] = tok
                    print(f'[hcap] token: {tok[:30]}')
        
        # If we got a token, call send_activation
        if hcap_token['v']:
            r = rq.post(f'{API}/users/v1/auth/send_activation',
                       json={'email':email,'hcaptcha':hcap_token['v']}, headers=H, timeout=10)
            print(f'[*] send_activation: {r.status_code} {r.text[:150]}')
        
        # Check what happened on the page
        page_body = (await page.inner_text('body'))[:400]
        print(f'[*] page body: {page_body}')
        
        # Poll for email code
        email_code = None
        for i in range(18):
            await asyncio.sleep(5)
            _, msgs = mr('GET', '/messages', token=mt_tok)
            items = (msgs or {}).get('hydra:member', [])
            if items:
                for msg in items:
                    _, full = mr('GET', f'/messages/{msg["id"]}', token=mt_tok)
                    codes = re.findall(r'\b(\d{6})\b', str(full.get('text','')))
                    if codes: email_code = codes[0]; break
                if email_code: break
            print(f'  [{(i+1)*5}s] waiting for email...')
        
        if email_code:
            print(f'[*] email code: {email_code}')
            r2 = rq.post(f'{API}/users/v1/auth/register',
                        json={'email':email,'password':nv_pwd,'code':email_code},
                        headers=H, timeout=10)
            d2 = r2.json()
            print(f'[*] register: {d2.get("code")} {d2.get("message")}')
            jwt = d2.get('data','') if isinstance(d2.get('data'),str) else (d2.get('data') or {}).get('token','')
            if jwt:
                r3 = rq.get(f'{API}/users/v1/auth/activation', params={'token':jwt},
                           headers={k:v for k,v in H.items() if k!='Content-Type'}, timeout=10)
                d3 = r3.json()
                print(f'[*] activation: {d3.get("code")} {d3.get("message")}')
                if d3.get('code') == 0:
                    print(f'\n✅ SUCCESS: {email} | {nv_pwd}')
                    try:
                        import psycopg2
                        conn = psycopg2.connect('postgresql://postgres:postgres@localhost/toolkit')
                        cur = conn.cursor()
                        cur.execute('CREATE TABLE IF NOT EXISTS accounts (id SERIAL PRIMARY KEY, platform TEXT, email TEXT, password TEXT, notes TEXT, created_at TIMESTAMP DEFAULT NOW())')
                        cur.execute('INSERT INTO accounts (platform,email,password,notes) VALUES (%s,%s,%s,%s)',
                                   ('talordata',email,nv_pwd,'activated'))
                        conn.commit(); conn.close()
                        print('[*] saved to DB')
                    except Exception as e: print(f'[*] DB error: {e}')
        
        print(f'\n[*] API calls: {len(api_calls)}')
        await browser.close()

asyncio.run(main())
