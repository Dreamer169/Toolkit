#!/usr/bin/env python3
"""
talordata /reg 注册 — 拦截hcaptcha render，强制visible模式 + 音频挑战
"""
import asyncio, json, re, secrets, string, subprocess, tempfile
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
    except: return 0, {}

# hcaptcha intercept script: changes from invisible to visible checkbox mode
HCAP_INTERCEPT = """
(function() {
    'use strict';
    // Wait for hcaptcha to load, then intercept render/execute
    let intercepted = false;
    const origInterval = setInterval(() => {
        if (window.hcaptcha && !intercepted) {
            intercepted = true;
            clearInterval(origInterval);
            console.log('[HC-INTERCEPT] hcaptcha loaded, intercepting...');
            
            const origRender = window.hcaptcha.render.bind(window.hcaptcha);
            const origExecute = window.hcaptcha.execute.bind(window.hcaptcha);
            
            // Override render to force normal/checkbox mode
            window.hcaptcha.render = function(container, params) {
                const newParams = Object.assign({}, params, {
                    size: 'normal',
                    theme: 'light',
                });
                delete newParams.execution;  // remove invisible execution trigger
                console.log('[HC-INTERCEPT] render intercepted, forcing normal mode');
                return origRender(container, newParams);
            };
            
            // Override execute to be a no-op (prevent invisible execution)
            window.hcaptcha.execute = function() {
                console.log('[HC-INTERCEPT] execute() blocked');
                // Do nothing - let the user/automation click the checkbox
            };
        }
    }, 50);
    
    // Also intercept if hcaptcha loads after our script
    const origDefine = window.define;
    Object.defineProperty(window, 'hcaptcha', {
        set: function(v) {
            window._hcaptcha = v;
            if (v && !intercepted) {
                intercepted = true;
                clearInterval(origInterval);
                console.log('[HC-INTERCEPT] hcaptcha set intercepted');
                const origR = v.render.bind(v);
                const origE = v.execute.bind(v);
                v.render = function(c, p) {
                    return origR(c, Object.assign({}, p, {size:'normal', theme:'light'}));
                };
                v.execute = function() { console.log('[HC-INTERCEPT] execute blocked'); };
            }
        },
        get: function() { return window._hcaptcha; },
        configurable: true,
    });
})();
"""

async def register_one():
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
    console_log = []
    
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    stealth = Stealth()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox','--disable-dev-shm-usage',
                  '--disable-blink-features=AutomationControlled']
        )
        ctx = await browser.new_context(
            viewport={'width':1280,'height':900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='en-US',
        )
        # Inject hcaptcha intercept BEFORE page loads
        await ctx.add_init_script(HCAP_INTERCEPT)
        
        page = await ctx.new_page()
        await stealth.apply_stealth_async(page)
        
        async def on_req(req):
            if 'api.talordata' in req.url:
                body = req.post_data or ''
                api_calls.append({'url':req.url,'method':req.method,'body':body})
                print(f'  [API] {req.method} {req.url[-60:]} | {body[:100]}')
            elif 'hcaptcha' in req.url:
                print(f'  [HCAP-NET] {req.method} {req.url[:80]}')
        async def on_resp(resp):
            if 'api.talordata' in resp.url:
                try:
                    d = await resp.json()
                    print(f'  [API-RESP] code={d.get("code")} {d.get("message")}')
                except: pass
        page.on('request', on_req)
        page.on('response', on_resp)
        page.on('console', lambda m: (console_log.append(m.text), print(f'  [console] {m.text[:100]}') if 'HC-INTERCEPT' in m.text or 'hcap' in m.text.lower() else None))
        
        print('[*] loading /reg with hcaptcha intercept...')
        await page.goto('https://dashboard.talordata.com/reg', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(5)
        print(f'[*] title: {await page.title()}')
        
        # Check hcaptcha state
        hcap_state = await page.evaluate("""() => ({
            hcapLoaded: typeof hcaptcha !== 'undefined',
            hcapFuncs: typeof hcaptcha !== 'undefined' ? Object.keys(hcaptcha) : [],
            iframes: Array.from(document.querySelectorAll('iframe')).map(f=>f.src?.substring(0,60)),
        })""")
        print(f'[*] hcap state: {hcap_state}')
        
        # Fill form
        await page.fill('input[placeholder*="email" i]', email)
        await asyncio.sleep(0.5)
        await page.fill('input[type="password"]', nv_pwd)
        await asyncio.sleep(0.3)
        try: await page.fill('input[placeholder*="invitation" i]', 'z46vzbz4')
        except: pass
        await asyncio.sleep(0.3)
        
        # Click checkbox with mouse
        cb = await page.query_selector('.el-checkbox__inner')
        if cb:
            box = await cb.bounding_box()
            if box:
                await page.mouse.click(box['x']+3, box['y']+3)
                await asyncio.sleep(0.5)
        
        await asyncio.sleep(1)
        
        # Click Sign Up
        btn = await page.query_selector('.el-button--primary')
        if btn:
            box = await btn.bounding_box()
            if box:
                await page.mouse.click(box['x']+box['width']//2, box['y']+box['height']//2)
                print('[*] Sign Up clicked')
        
        # Wait for hcaptcha iframe to appear (visible mode)
        print('[*] waiting for hcaptcha iframe (visible mode)...')
        hcap_iframe = None
        for i in range(20):
            await asyncio.sleep(1)
            hcap_frames = [f for f in page.frames if 'hcaptcha' in f.url]
            # Also check for iframes in page
            iframes = await page.evaluate("""() => 
                Array.from(document.querySelectorAll('iframe')).map(f=>({src:f.src?.substring(0,70), id:f.id}))
            """)
            if hcap_frames:
                print(f'[{i}s] hcaptcha frames: {[f.url[:60] for f in hcap_frames]}')
                hcap_iframe = hcap_frames[0]
                break
            if i % 3 == 0:
                print(f'[{i}s] waiting... iframes: {[x["src"] for x in iframes if x["src"]]}')
        
        if hcap_iframe:
            print('[*] hcaptcha visible! solving via audio...')
            await asyncio.sleep(2)
            
            # Look for hcaptcha checkbox
            checkbox_frame = None
            for frame in page.frames:
                if 'hcaptcha' in frame.url:
                    try:
                        cb = await frame.query_selector('#checkbox, .challenge-checkbox, [role="checkbox"]')
                        if cb:
                            await cb.click()
                            print('[hcap] clicked hcaptcha checkbox')
                            await asyncio.sleep(2)
                            break
                    except: pass
            
            # Wait for challenge
            await asyncio.sleep(3)
            
            # Check if audio challenge appeared
            challenge_frame = next((f for f in page.frames if 'hcaptcha' in f.url and 'challenge' in f.url), None)
            print(f'[*] challenge frame: {challenge_frame.url[:60] if challenge_frame else None}')
            
            if challenge_frame:
                # Try audio button
                for sel in ['button[aria-label*="audio" i]', '.challenge-switchtype']:
                    try:
                        el = await challenge_frame.query_selector(sel)
                        if el:
                            await el.click()
                            await asyncio.sleep(2)
                            print(f'[hcap] audio btn: {sel}')
                            break
                    except: pass
                
                # Wait for audio
                audio_url = None
                for _ in range(10):
                    await asyncio.sleep(1)
                    for frame in page.frames:
                        if 'hcaptcha' not in frame.url: continue
                        try:
                            u = await frame.evaluate('() => document.querySelector("audio")?.src || null')
                            if u and u.startswith('http'): audio_url = u; break
                        except: pass
                    if audio_url: break
                
                if audio_url:
                    print(f'[hcap] audio: {audio_url[:60]}')
                    with tempfile.NamedTemporaryFile(suffix='.mp3',delete=False) as f: ap=f.name
                    open(ap,'wb').write(rq.get(audio_url,timeout=20).content)
                    wp = ap.replace('.mp3','.wav')
                    subprocess.run(['ffmpeg','-i',ap,'-ar','16000','-ac','1',wp,'-y'],capture_output=True)
                    import whisper
                    model = whisper.load_model('base')
                    result = model.transcribe(wp,language='en')
                    text = re.sub(r'[^a-z0-9\s]','',result['text'].strip().lower()).strip()
                    print(f'[hcap] whisper: "{text}"')
                    
                    for frame in page.frames:
                        if 'hcaptcha' not in frame.url: continue
                        try:
                            inp = await frame.query_selector('input[type="text"]')
                            if inp:
                                await inp.fill(text)
                                vbtn = await frame.query_selector('button[type="submit"]')
                                if vbtn: await vbtn.click()
                                await asyncio.sleep(3)
                                break
                        except: pass
                    
                    tok = await page.evaluate('() => document.querySelector("[name=h-captcha-response]")?.value || null')
                    if tok: hcap_token['v'] = tok; print(f'[hcap] token: {tok[:30]}')
        
        if hcap_token['v']:
            r = rq.post(f'{API}/users/v1/auth/send_activation',
                       json={'email':email,'hcaptcha':hcap_token['v']}, headers=H, timeout=10)
            print(f'[*] send_activation: {r.status_code} {r.text[:150]}')
        
        # Check what happened on page
        pg_body = (await page.inner_text('body'))[:300]
        print(f'[*] page body: {pg_body}')
        
        # Poll email
        email_code = None
        for i in range(12):
            await asyncio.sleep(5)
            _, msgs = mr('GET', '/messages', token=mt_tok)
            items = (msgs or {}).get('hydra:member', [])
            if items:
                for msg in items:
                    _, full = mr('GET', f'/messages/{msg["id"]}', token=mt_tok)
                    codes = re.findall(r'\b(\d{6})\b', str(full.get('text','')))
                    if codes: email_code = codes[0]; break
                if email_code: break
            print(f'  [{(i+1)*5}s] waiting email...')
        
        if email_code:
            print(f'[*] code: {email_code}')
            r2 = rq.post(f'{API}/users/v1/auth/register',
                        json={'email':email,'password':nv_pwd,'code':email_code},
                        headers=H, timeout=10)
            d2 = r2.json()
            jwt = d2.get('data','') if isinstance(d2.get('data'),str) else (d2.get('data') or {}).get('token','')
            print(f'register: {d2.get("code")} {d2.get("message")}')
            if jwt:
                r3 = rq.get(f'{API}/users/v1/auth/activation', params={'token':jwt},
                           headers={k:v for k,v in H.items() if k!='Content-Type'}, timeout=10)
                d3 = r3.json()
                print(f'activation: {d3.get("code")} {d3.get("message")}')
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
        
        print(f'\n[*] API calls total: {len(api_calls)}')
        await browser.close()

asyncio.run(register_one())
