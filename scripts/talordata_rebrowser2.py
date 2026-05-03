#!/usr/bin/env python3
"""
talordata /reg 注册 — rebrowser_playwright + hcaptcha 音频绕过
"""
import asyncio, json, re, secrets, string, time
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
    chars = string.ascii_lowercase + string.digits
    login = ''.join(secrets.choice(chars) for _ in range(14))
    email = f'{login}@deltajohnsons.com'
    mt_pwd = 'P@' + secrets.token_hex(10)
    nv_pwd = 'Aa1!' + ''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(12))
    
    mr('POST', '/accounts', {'address':email,'password':mt_pwd})
    _, tb = mr('POST', '/token', {'address':email,'password':mt_pwd})
    mt_tok = tb.get('token','')
    print(f'[*] email={email}')
    
    captured = {'token': None}
    api_calls = []
    
    from rebrowser_playwright.async_api import async_playwright as rb_pw
    
    async with rb_pw() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox','--disable-dev-shm-usage',
                  '--disable-blink-features=AutomationControlled']
        )
        ctx = await browser.new_context(
            viewport={'width':1280,'height':800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='en-US',
        )
        page = await ctx.new_page()
        
        async def on_req(req):
            if 'api.talordata' in req.url or 'hcaptcha' in req.url:
                body = req.post_data or ''
                api_calls.append({'url':req.url,'method':req.method,'body':body})
                print(f'[REQ] {req.method} {req.url[:80]} | {body[:80]}')
        async def on_resp(resp):
            if 'api.talordata' in resp.url:
                try:
                    d = await resp.json()
                    print(f'[RESP] {resp.url[:70]}: code={d.get("code")} msg={d.get("message")} data={str(d.get("data",""))[:60]}')
                except: pass
        page.on('request', on_req)
        page.on('response', on_resp)
        
        print('[*] goto /reg')
        await page.goto('https://dashboard.talordata.com/reg', wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(4)
        print(f'[*] title: {await page.title()}')
        
        # Fill form
        await page.fill('input[placeholder*="email" i]', email)
        await asyncio.sleep(0.3)
        await page.fill('input[type="password"]', nv_pwd)
        await asyncio.sleep(0.3)
        # Optional invite code
        try:
            inv = page.locator('input[placeholder*="invitation" i]').first
            if await inv.is_visible(timeout=1500):
                await inv.fill('z46vzbz4')
        except: pass
        await asyncio.sleep(0.2)
        # Check the terms checkbox via JS
        await page.evaluate("""() => {
            const cb = document.querySelector('input[type="checkbox"]');
            if (cb) {
                cb.checked = true;
                ['change','input','click'].forEach(e => cb.dispatchEvent(new Event(e,{bubbles:true})));
            }
        }""")
        print('[*] checkbox set via JS')
        await asyncio.sleep(1)
        
        # Also try the el-checkbox wrapper click
        try:
            el_cb = await page.query_selector('.el-checkbox')
            if el_cb:
                await page.evaluate('(el) => el.click()', el_cb)
                await asyncio.sleep(0.3)
        except: pass
        
        await page.screenshot(path='/tmp/rb2_filled.png')
        
        # Click Sign Up
        for sel in ['button:has-text("Sign Up")', 'button[type="submit"]', '.el-button--primary']:
            try:
                btn = page.locator(sel).last
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    print(f'[*] clicked: {sel}')
                    break
            except: pass
        
        await asyncio.sleep(6)
        await page.screenshot(path='/tmp/rb2_submit.png')
        
        # Check hcaptcha
        hcap_frames = [f for f in page.frames if 'hcaptcha' in f.url]
        print(f'[*] hcaptcha frames after click: {len(hcap_frames)}')
        for f in hcap_frames:
            print(f'  url: {f.url[:80]}')
        
        # Check if invisible hcaptcha auto-passed
        tok = await page.evaluate("""() => {
            const el = document.querySelector('[name="h-captcha-response"]');
            return el ? el.value : null;
        }""")
        print(f'[*] hcaptcha token in page: {tok[:40] if tok else "NONE"}')
        
        if tok:
            captured['token'] = tok
        
        if not captured['token'] and hcap_frames:
            print('[*] solving hcaptcha audio challenge...')
            import whisper, tempfile, subprocess
            
            challenge_frame = next((f for f in page.frames if 'challenge' in f.url), None)
            if not challenge_frame:
                challenge_frame = hcap_frames[0] if hcap_frames else None
            
            if challenge_frame:
                content = await challenge_frame.content()
                print(f'[hcap] frame content (500): {content[:500]}')
                
                # Try audio button
                for sel in ['button[aria-label*="audio" i]','.challenge-switchtype','button:has-text("Audio")']:
                    try:
                        el = await challenge_frame.query_selector(sel)
                        if el:
                            await el.click()
                            print(f'[hcap] audio btn clicked: {sel}')
                            await asyncio.sleep(2)
                            break
                    except: pass
                
                # Find audio URL
                audio_url = None
                for _ in range(8):
                    await asyncio.sleep(1)
                    for frame in page.frames:
                        if 'hcaptcha' not in frame.url: continue
                        try:
                            u = await frame.evaluate('() => document.querySelector("audio")?.src || null')
                            if u and u.startswith('http'):
                                audio_url = u
                                break
                            content = await frame.content()
                            m = re.search(r'src=["\']([^"\']+\.mp3[^"\']*)["\']', content)
                            if m: audio_url = m.group(1); break
                        except: pass
                    if audio_url: break
                
                if audio_url:
                    print(f'[hcap] audio: {audio_url[:60]}')
                    with tempfile.NamedTemporaryFile(suffix='.mp3',delete=False) as f: ap=f.name
                    resp = rq.get(audio_url, timeout=20)
                    open(ap,'wb').write(resp.content)
                    wp = ap.replace('.mp3','.wav')
                    subprocess.run(['ffmpeg','-i',ap,'-ar','16000','-ac','1',wp,'-y'],capture_output=True)
                    mdl = whisper.load_model('base')
                    result = mdl.transcribe(wp, language='en')
                    text = re.sub(r'[^a-z0-9\s]','',result['text'].strip().lower()).strip()
                    print(f'[hcap] whisper: "{text}"')
                    
                    for frame in page.frames:
                        if 'hcaptcha' not in frame.url: continue
                        try:
                            inp = await frame.query_selector('input[type="text"]')
                            if inp:
                                await inp.fill(text)
                                btn = await frame.query_selector('button[type="submit"]')
                                if btn: await btn.click()
                                await asyncio.sleep(3)
                                break
                        except: pass
                    
                    tok2 = await page.evaluate('() => document.querySelector("[name=h-captcha-response]")?.value || null')
                    if tok2: captured['token'] = tok2; print(f'[hcap] got token after solve: {tok2[:30]}')
        
        # If we got hcaptcha token, call send_activation
        if captured['token']:
            r = rq.post(f'{API}/users/v1/auth/send_activation',
                       json={'email':email,'hcaptcha':captured['token']}, headers=H, timeout=10)
            print(f'[*] send_activation: {r.status_code} {r.text[:150]}')
        
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
                        json={'email':email,'password':nv_pwd,'code':email_code}, headers=H, timeout=10)
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
                    # Save to DB
                    try:
                        import psycopg2
                        conn = psycopg2.connect('postgresql://postgres:postgres@localhost/toolkit')
                        cur = conn.cursor()
                        cur.execute('CREATE TABLE IF NOT EXISTS accounts (id SERIAL PRIMARY KEY, platform TEXT, email TEXT, password TEXT, notes TEXT, created_at TIMESTAMP DEFAULT NOW())')
                        cur.execute('INSERT INTO accounts (platform,email,password,notes) VALUES (%s,%s,%s,%s)',
                                   ('talordata',email,nv_pwd,f'jwt={jwt[:30]}'))
                        conn.commit(); conn.close()
                        print('[*] saved to DB')
                    except Exception as e: print(f'[*] DB error: {e}')
        
        print(f'\n[*] API calls: {len(api_calls)}')
        await browser.close()

asyncio.run(main())
