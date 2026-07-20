#!/usr/bin/env python3
"""
talordata.com 注册脚本 v4
流程: /reg页面 -> 触发hcaptcha挑战 -> 音频绕过 -> 获取token -> 
      POST /register{email,password,hcaptcha} -> 从邮件获取激活链接JWT -> GET /activation
"""
import asyncio, json, re, secrets, string, subprocess, tempfile
import requests as rq
import urllib.request, urllib.error

API = 'https://api.talordata.com'
H = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
     'Origin':'https://dashboard.talordata.com','Content-Type':'application/json'}

def mail_req(method, path, data=None, token=None):
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
    except Exception as ex: return 0, {'err': str(ex)}

async def wait_for_hcaptcha_frames(page, timeout=30):
    for i in range(timeout):
        await asyncio.sleep(1)
        frames = [f for f in page.frames if 'hcaptcha' in f.url]
        if frames: return frames
        if i % 5 == 0: print(f'  [{i}s] waiting for hcaptcha frames...')
    return []

async def solve_audio(page, frames):
    import whisper
    print('[hcap] attempting audio challenge...')
    await asyncio.sleep(4)  # Let frames fully load

    audio_url = None

    for frame in frames:
        try:
            # Get frame content to understand its state
            content = await frame.content()
            frame_type = 'challenge' if 'task' in content.lower() or 'image' in content.lower() else 'widget'
            print(f'  [hcap frame] type={frame_type} len={len(content)} url={frame.url[:60]}')

            # Try clicking audio button in any frame
            for sel in [
                'button[aria-label*="audio" i]',
                'button[class*="audio"]',
                '.challenge-switchtype',
                '[data-type="audio"]',
                'button[aria-label="switch to audio"]',
                'a[href*="audio"]',
            ]:
                try:
                    el = await frame.query_selector(sel)
                    if el:
                        await el.click()
                        print(f'  [hcap] audio btn clicked: {sel}')
                        await asyncio.sleep(3)
                        break
                except: pass

            # Look for audio
            for attempt in range(12):
                await asyncio.sleep(1)
                try:
                    u = await frame.evaluate('() => { const a=document.querySelector("audio"); return a ? (a.src||a.currentSrc) : null; }')
                    if u and (u.startswith('http') or u.startswith('blob:')):
                        # For blob URLs, we need to get the actual data
                        if u.startswith('blob:'):
                            print(f'  [hcap] blob audio detected: {u[:50]}')
                            # Convert blob to arrayBuffer
                            audio_data = await frame.evaluate(
                                '(url) => fetch(url).then(r=>r.arrayBuffer()).then(b=>Array.from(new Uint8Array(b)))',
                                u
                            )
                            if audio_data:
                                with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                                    f.write(bytes(audio_data))
                                    audio_url = f.name
                                print(f'  [hcap] blob saved to {audio_url}')
                        else:
                            audio_url = u
                        print(f'  [hcap] audio url: {str(audio_url)[:60]}')
                        break
                except Exception as e:
                    pass
            
            if audio_url: break
        except Exception as e:
            print(f'  [hcap] frame error: {e}')

    if not audio_url:
        # Dump frame states for debugging
        print('[hcap] no audio found, dumping frames...')
        for frame in frames:
            try:
                state = await frame.evaluate("""() => ({
                    title: document.title,
                    url: location.href.substring(0,60),
                    buttons: Array.from(document.querySelectorAll('button')).map(b=>
                        b.getAttribute('aria-label')||b.textContent?.trim()?.substring(0,20)).filter(Boolean),
                    audios: Array.from(document.querySelectorAll('audio')).map(a=>a.src?.substring(0,40)),
                    inputs: Array.from(document.querySelectorAll('input')).map(i=>i.type),
                })""")
                print(f'  state: {json.dumps(state)[:300]}')
            except Exception as e:
                print(f'  dump error: {e}')
        return None

    # Download/use audio file
    if audio_url.startswith('/'):  # Already saved path
        mp3_path = audio_url
    else:
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            mp3_path = f.name
        try:
            resp = rq.get(audio_url, timeout=30, headers={'User-Agent':'Mozilla/5.0'})
            open(mp3_path, 'wb').write(resp.content)
            print(f'  [hcap] downloaded {len(resp.content)} bytes')
        except Exception as e:
            print(f'  [hcap] download error: {e}')
            return None

    # Convert and transcribe
    wav_path = mp3_path.replace('.mp3', '.wav')
    res = subprocess.run(['ffmpeg', '-i', mp3_path, '-ar', '16000', '-ac', '1', wav_path, '-y'],
                        capture_output=True, timeout=30)
    if not res.returncode == 0:
        print(f'  [hcap] ffmpeg error: {res.stderr[:100]}')
        # Try anyway
    
    try:
        model = whisper.load_model('base')
        result = model.transcribe(wav_path if res.returncode == 0 else mp3_path, language='en')
        text = re.sub(r'[^a-z0-9\s]', '', result['text'].strip().lower()).strip()
        print(f'  [hcap] whisper: "{text}"')
    except Exception as e:
        print(f'  [hcap] whisper error: {e}')
        return None

    # Submit answer
    submitted = False
    for frame in frames:
        try:
            inp = await frame.query_selector('input[type="text"]')
            if inp:
                await inp.fill(text)
                await asyncio.sleep(0.3)
                for btn_sel in ['button[type="submit"]', 'button:has-text("Submit")',
                                'button[class*="submit"]', '#submit-btn']:
                    try:
                        btn = await frame.query_selector(btn_sel)
                        if btn:
                            await btn.click()
                            print(f'  [hcap] submitted: {btn_sel}')
                            submitted = True
                            await asyncio.sleep(4)
                            break
                    except: pass
                if submitted: break
        except: pass

    # Get token
    for i in range(8):
        await asyncio.sleep(1)
        try:
            tok = await page.evaluate("""() => {
                const el = document.querySelector('[name="h-captcha-response"]');
                if (el && el.value) return el.value;
                if (typeof hcaptcha !== 'undefined') {
                    const r = hcaptcha.getResponse();
                    if (r) return r;
                }
                return null;
            }""")
            if tok and len(tok) > 20:
                print(f'  [hcap] GOT TOKEN len={len(tok)}: {tok[:40]}...')
                return tok
        except: pass

    print('  [hcap] no token after submit')
    return None

async def register_account(inv_code='z46vzbz4'):
    # Setup email
    chars = string.ascii_lowercase + string.digits
    login = ''.join(secrets.choice(chars) for _ in range(14))
    email = f'{login}@deltajohnsons.com'
    mt_pwd = 'P@' + secrets.token_hex(10)
    nv_pwd = 'Aa1!' + ''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(12))
    
    mail_req('POST', '/accounts', {'address':email,'password':mt_pwd})
    _, tb = mail_req('POST', '/token', {'address':email,'password':mt_pwd})
    mt_tok = tb.get('token', '')
    print(f'[*] email={email}  pwd={nv_pwd}')

    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    stealth = Stealth()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            '--no-sandbox', '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
        ])
        ctx = await browser.new_context(
            viewport={'width':1280,'height':900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='en-US',
        )
        page = await ctx.new_page()
        await stealth.apply_stealth_async(page)

        async def on_req(req):
            try: body = req.post_data or ''
            except: body = '<binary>'
            if 'api.talordata' in req.url:
                print(f'  [API-REQ] {req.method} {req.url[-60:]} | {body[:80]}')
            elif 'hcaptcha' in req.url and not any(x in req.url for x in ['.js','.css','.woff']):
                print(f'  [HCAP-NET] {req.method} {req.url[:80]}')
        async def on_resp(resp):
            if 'api.talordata' in resp.url:
                try:
                    d = await resp.json()
                    print(f'  [API-RESP] code={d.get("code")} msg={d.get("message")} data_len={len(str(d.get("data","")))}')
                except: pass
        page.on('request', on_req)
        page.on('response', on_resp)

        print('[*] Loading /reg...')
        await page.goto('https://dashboard.talordata.com/reg', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(5)
        print(f'[*] title: {await page.title()}')

        # Fill form naturally
        await page.click('input[placeholder*="email" i]')
        await page.type('input[placeholder*="email" i]', email, delay=40)
        await asyncio.sleep(0.5)
        await page.click('input[type="password"]')
        await page.type('input[type="password"]', nv_pwd, delay=55)
        await asyncio.sleep(0.3)
        try:
            inv_inp = page.locator('input[placeholder*="invitation" i]').first
            if await inv_inp.is_visible(timeout=1000):
                await inv_inp.click()
                await page.type('input[placeholder*="invitation" i]', inv_code, delay=70)
        except: pass
        await asyncio.sleep(0.3)

        # Click checkbox
        cb = await page.query_selector('.el-checkbox__inner')
        if cb:
            box = await cb.bounding_box()
            if box:
                await page.mouse.move(box['x']+3, box['y']+3, steps=4)
                await asyncio.sleep(0.2)
                await page.mouse.click(box['x']+3, box['y']+3)
                await asyncio.sleep(0.5)
        
        checked = await page.evaluate("() => document.querySelector('input[type=checkbox]')?.checked")
        print(f'[*] checkbox checked: {checked}')
        await asyncio.sleep(1)

        # Click Sign Up via text-based selector (confirmed working)
        signup = page.get_by_text('Sign Up').last
        box = await signup.bounding_box()
        if box:
            await page.mouse.move(box['x']+box['width']//2, box['y']+box['height']//2, steps=8)
            await asyncio.sleep(0.4)
            await page.mouse.click(box['x']+box['width']//2, box['y']+box['height']//2)
            print('[*] Sign Up clicked')

        # Wait for hcaptcha challenge
        frames = await wait_for_hcaptcha_frames(page, timeout=20)
        print(f'[*] hcaptcha frames: {len(frames)}')

        hcap_token = None
        if frames:
            hcap_token = await solve_audio(page, frames)

        if hcap_token:
            print(f'[*] have hcaptcha token, calling /register...')
            # POST register with hcaptcha token
            r = rq.post(f'{API}/users/v1/auth/register',
                       json={'email': email, 'password': nv_pwd,
                             'inviter_code': inv_code, 'hcaptcha': hcap_token},
                       headers=H, timeout=15)
            d = r.json()
            print(f'[*] register response: code={d.get("code")} msg={d.get("message")}')
            
            # Also try send_activation approach
            if d.get('code') == 20015:
                print('[*] trying send_activation with hcaptcha token...')
                r2 = rq.post(f'{API}/users/v1/auth/send_activation',
                            json={'email': email, 'hcaptcha': hcap_token}, headers=H, timeout=10)
                d2 = r2.json()
                print(f'[*] send_activation: code={d2.get("code")} msg={d2.get("message")}')
            
            # Poll email for activation link or code
            print('[*] polling email for activation link/code...')
            for i in range(24):
                await asyncio.sleep(5)
                _, msgs = mail_req('GET', '/messages', token=mt_tok)
                items = (msgs or {}).get('hydra:member', [])
                if items:
                    for msg in items:
                        _, full = mail_req('GET', f'/messages/{msg["id"]}', token=mt_tok)
                        full_text = str(full.get('text', '')) + str(full.get('html', ''))
                        
                        # Look for JWT activation token in URL
                        jwt_matches = re.findall(r'token[=:]([A-Za-z0-9_.-]{50,})', full_text)
                        # Look for 6-digit code
                        codes = re.findall(r'\b(\d{6})\b', full_text)
                        # Look for activation link
                        links = re.findall(r'https?://[^\s"\'<>]+activ[^\s"\'<>]+', full_text)
                        
                        if jwt_matches:
                            jwt = jwt_matches[0]
                            print(f'[*] found JWT in email: {jwt[:40]}')
                            r3 = rq.get(f'{API}/users/v1/auth/activation', params={'token': jwt},
                                       headers={k:v for k,v in H.items() if k!='Content-Type'}, timeout=10)
                            d3 = r3.json()
                            print(f'[*] activation: code={d3.get("code")} msg={d3.get("message")}')
                            if d3.get('code') == 0:
                                print(f'\n✅ SUCCESS: {email} | {nv_pwd}')
                                try:
                                    import psycopg2
                                    conn = psycopg2.connect('postgresql://postgres:postgres@localhost/toolkit')
                                    cur = conn.cursor()
                                    cur.execute('CREATE TABLE IF NOT EXISTS accounts (id SERIAL PRIMARY KEY, platform TEXT, email TEXT, password TEXT, notes TEXT, created_at TIMESTAMP DEFAULT NOW())')
                                    cur.execute('INSERT INTO accounts (platform,email,password,notes) VALUES (%s,%s,%s,%s)',
                                               ('talordata', email, nv_pwd, f'activated jwt'))
                                    conn.commit(); conn.close()
                                    print('[*] saved to DB')
                                except Exception as e:
                                    print(f'[*] DB error: {e}')
                                return True
                        
                        if codes:
                            code = codes[0]
                            print(f'[*] found code: {code}')
                            r3 = rq.post(f'{API}/users/v1/auth/register',
                                        json={'email':email,'password':nv_pwd,'code':code},
                                        headers=H, timeout=10)
                            d3 = r3.json()
                            print(f'[*] register with code: code={d3.get("code")} msg={d3.get("message")}')
                            jwt = d3.get('data','') if isinstance(d3.get('data'),str) else (d3.get('data') or {}).get('token','')
                            if jwt:
                                r4 = rq.get(f'{API}/users/v1/auth/activation', params={'token':jwt},
                                           headers={k:v for k,v in H.items() if k!='Content-Type'}, timeout=10)
                                d4 = r4.json()
                                print(f'[*] activation: code={d4.get("code")} msg={d4.get("message")}')
                                if d4.get('code') == 0:
                                    print(f'\n✅ SUCCESS: {email} | {nv_pwd}')
                                    try:
                                        import psycopg2
                                        conn = psycopg2.connect('postgresql://postgres:postgres@localhost/toolkit')
                                        cur = conn.cursor()
                                        cur.execute('CREATE TABLE IF NOT EXISTS accounts (id SERIAL PRIMARY KEY, platform TEXT, email TEXT, password TEXT, notes TEXT, created_at TIMESTAMP DEFAULT NOW())')
                                        cur.execute('INSERT INTO accounts (platform,email,password,notes) VALUES (%s,%s,%s,%s)',
                                                   ('talordata', email, nv_pwd, 'activated'))
                                        conn.commit(); conn.close()
                                        print('[*] saved to DB')
                                    except Exception as e:
                                        print(f'[*] DB error: {e}')
                                    return True
                        
                        if links:
                            print(f'[*] found activation links: {links[:2]}')
                            for link in links[:2]:
                                # Extract JWT from link
                                m = re.search(r'[?&]token=([A-Za-z0-9_.-]+)', link)
                                if m:
                                    jwt = m.group(1)
                                    r3 = rq.get(f'{API}/users/v1/auth/activation', params={'token':jwt},
                                               headers={k:v for k,v in H.items() if k!='Content-Type'}, timeout=10)
                                    d3 = r3.json()
                                    print(f'[*] activation from link: code={d3.get("code")} msg={d3.get("message")}')
                                    if d3.get('code') == 0:
                                        print(f'\n✅ SUCCESS: {email} | {nv_pwd}')
                                        return True
                
                print(f'  [{(i+1)*5}s] waiting for email...')
            
            print('[!] Email timeout - no activation email received')
        else:
            print('[!] No hcaptcha token obtained')

        await page.screenshot(path='/tmp/talordata_final.png')
        await browser.close()
    return False

asyncio.run(register_account())
