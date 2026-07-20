#!/usr/bin/env python3
"""
talordata v5: 正确流程 - 点击hcaptcha checkbox -> 等待图像挑战 -> 切换音频 -> 解决
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
    except Exception as ex: return 0, {}

async def click_hcaptcha_checkbox(page):
    """Click the hcaptcha widget checkbox to trigger the challenge"""
    for frame in page.frames:
        if 'hcaptcha' not in frame.url: continue
        try:
            # Look for the initial checkbox widget
            state = await frame.evaluate("""() => ({
                buttons: Array.from(document.querySelectorAll('button')).map(b=>({
                    aria: b.getAttribute('aria-label'),
                    text: b.textContent?.trim()?.substring(0,30),
                    id: b.id, class: b.className?.substring(0,50)
                })),
                checkboxes: Array.from(document.querySelectorAll('#checkbox, [role="checkbox"], .checkbox')).map(c=>({
                    id: c.id, class: c.className?.substring(0,30), role: c.getAttribute('role')
                })),
            })""")
            print(f'  [hcap widget state]: {json.dumps(state)[:300]}')
            
            # Click the main hcaptcha checkbox
            for sel in ['#checkbox', '[role="checkbox"]', '.checkbox-container',
                       'div[tabindex="0"]', '.hcaptcha-box']:
                try:
                    el = await frame.query_selector(sel)
                    if el:
                        await el.click()
                        print(f'  [hcap] clicked checkbox: {sel}')
                        return True
                except: pass
            
            # Try clicking the "About hCaptcha" button area (it's the widget frame)
            # The checkbox might be the entire frame area
            await frame.evaluate("() => { const c = document.querySelector('#checkbox'); if(c) c.click(); }")
        except Exception as e:
            pass
    return False

async def solve_hcaptcha_audio(page, max_retries=3):
    """Click checkbox -> wait for challenge -> switch to audio -> solve"""
    import whisper
    
    for retry in range(max_retries):
        print(f'\n[hcap] solve attempt {retry+1}/{max_retries}')
        
        # Wait for all frames to load
        await asyncio.sleep(3)
        
        frames = [f for f in page.frames if 'hcaptcha' in f.url]
        print(f'[hcap] frames: {len(frames)}')
        for f in frames:
            print(f'  {f.url[:70]}')
        
        # Step 1: Click the hcaptcha checkbox (widget frame)
        checkbox_clicked = False
        for frame in frames:
            try:
                content = await frame.content()
                # The widget frame has the checkbox
                if 'checkbox' in content.lower() or len(content) < 100000:
                    # Try clicking checkbox elements
                    for sel in ['#checkbox', '[role="checkbox"]', 'div.checkbox',
                               'div[tabindex="0"]', '.task-widget']:
                        try:
                            el = await frame.query_selector(sel)
                            if el:
                                await el.click()
                                print(f'  [hcap] clicked: {sel} in frame {frame.url[:40]}')
                                checkbox_clicked = True
                                await asyncio.sleep(3)
                                break
                        except: pass
                    if checkbox_clicked: break
            except: pass
        
        if not checkbox_clicked:
            print('  [hcap] no checkbox found, trying page-level click')
            # The hcaptcha widget might be in an iframe embedded in the page
            # Try finding it via page evaluate
            try:
                await page.evaluate("""() => {
                    const iframes = document.querySelectorAll('iframe');
                    for(const f of iframes) {
                        if(f.src?.includes('hcaptcha')) {
                            f.contentDocument?.querySelector('#checkbox')?.click();
                        }
                    }
                }""")
                await asyncio.sleep(3)
            except: pass
        
        # Step 2: Check for new frames (challenge appeared after checkbox click)
        await asyncio.sleep(2)
        all_frames = [f for f in page.frames if 'hcaptcha' in f.url]
        print(f'[hcap] frames after checkbox: {len(all_frames)}')
        
        # Step 3: Find the challenge frame and click audio button
        audio_url = None
        for frame in all_frames:
            try:
                content = await frame.content()
                # Challenge frame has images and tasks
                has_challenge = any(kw in content.lower() for kw in ['task', 'challenge', 'image', 'verify'])
                print(f'  frame len={len(content)} has_challenge={has_challenge}')
                
                if len(content) > 10000:  # Challenge frames are usually large
                    # Try to switch to audio mode
                    for audio_sel in [
                        'button[aria-label*="audio" i]',
                        'button[class*="audio"]',
                        'button[id*="audio"]',
                        '.challenge-switchtype',
                        '[data-action="switch-audio"]',
                        'a.audio-btn',
                    ]:
                        try:
                            el = await frame.query_selector(audio_sel)
                            if el:
                                await el.click()
                                print(f'  [hcap] switched to audio: {audio_sel}')
                                await asyncio.sleep(3)
                                break
                        except: pass
                    
                    # Also try pressing keyboard shortcut (some versions use Tab+Enter)
                    try:
                        await frame.keyboard.press('Tab')
                        await asyncio.sleep(0.5)
                        await frame.keyboard.press('Tab')
                        await asyncio.sleep(0.5)
                    except: pass
                    
                    # Look for audio element
                    for _ in range(15):
                        await asyncio.sleep(1)
                        try:
                            u = await frame.evaluate("""() => {
                                const a = document.querySelector('audio');
                                if (!a) return null;
                                return a.src || a.currentSrc || null;
                            }""")
                            if u:
                                print(f'  [hcap] audio src: {u[:70]}')
                                audio_url = u
                                break
                        except: pass
                    
                    if audio_url: break
            except Exception as e:
                print(f'  frame error: {e}')
        
        if not audio_url:
            print('[hcap] no audio found, check all buttons again...')
            for frame in all_frames:
                try:
                    btns = await frame.evaluate("""() =>
                        Array.from(document.querySelectorAll('button,a,[role=button]')).map(b=>({
                            aria: b.getAttribute('aria-label'),
                            text: b.textContent?.trim()?.substring(0,25),
                            class: b.className?.substring(0,40),
                            visible: b.offsetParent !== null,
                        }))""")
                    visible_btns = [b for b in btns if b.get('visible')]
                    if visible_btns:
                        print(f'  visible buttons: {visible_btns[:5]}')
                        # Click any audio-related button
                        for btn_info in visible_btns:
                            if btn_info.get('aria') and 'audio' in str(btn_info.get('aria','')).lower():
                                # Click it
                                await frame.evaluate(f"""() => {{
                                    const btns = document.querySelectorAll('button,a');
                                    for(const b of btns) {{
                                        if(b.getAttribute('aria-label')?.toLowerCase().includes('audio')) {{
                                            b.click();
                                        }}
                                    }}
                                }}""")
                                print('[hcap] clicked audio btn via evaluate')
                                await asyncio.sleep(3)
                                break
                except: pass
            
            # Last chance: check audio
            for frame in all_frames:
                try:
                    u = await frame.evaluate("() => { const a=document.querySelector('audio'); return a?.src||a?.currentSrc||null; }")
                    if u: audio_url = u; break
                except: pass
        
        if audio_url:
            break
        
        print(f'[hcap] retry {retry+1}: no audio, will retry...')
        await asyncio.sleep(2)
    
    if not audio_url:
        return None
    
    # Download audio
    if audio_url.startswith('blob:'):
        # Handle blob URL
        for frame in all_frames:
            try:
                audio_data = await frame.evaluate(
                    '(url) => fetch(url).then(r=>r.arrayBuffer()).then(b=>Array.from(new Uint8Array(b)))',
                    audio_url
                )
                if audio_data:
                    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                        f.write(bytes(audio_data))
                        mp3_path = f.name
                    print(f'[hcap] blob audio saved: {len(audio_data)} bytes')
                    break
            except Exception as e:
                print(f'blob error: {e}')
                continue
    else:
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            mp3_path = f.name
        resp = rq.get(audio_url, timeout=30, headers={'User-Agent':'Mozilla/5.0'})
        open(mp3_path, 'wb').write(resp.content)
        print(f'[hcap] downloaded {len(resp.content)} bytes')
    
    # Transcribe
    wav_path = mp3_path.replace('.mp3', '.wav')
    subprocess.run(['ffmpeg', '-i', mp3_path, '-ar', '16000', '-ac', '1', wav_path, '-y'],
                  capture_output=True, timeout=30)
    
    model = whisper.load_model('base')
    result = model.transcribe(wav_path, language='en')
    text = re.sub(r'[^a-z0-9\s]', '', result['text'].strip().lower()).strip()
    print(f'[hcap] whisper: "{text}"')
    
    # Submit
    for frame in all_frames:
        try:
            inp = await frame.query_selector('input[type="text"]')
            if inp:
                await inp.fill(text)
                await asyncio.sleep(0.3)
                for btn_sel in ['button[type="submit"]', '#submit-btn', 'button:has-text("Submit")']:
                    try:
                        btn = await frame.query_selector(btn_sel)
                        if btn:
                            await btn.click()
                            print(f'[hcap] submitted: {btn_sel}')
                            await asyncio.sleep(4)
                            break
                    except: pass
                break
        except: pass
    
    # Get token
    for i in range(8):
        await asyncio.sleep(1)
        try:
            tok = await page.evaluate("""() => {
                const el = document.querySelector('[name="h-captcha-response"]');
                if (el && el.value && el.value.length > 20) return el.value;
                if (typeof hcaptcha !== 'undefined') {
                    const r = hcaptcha.getResponse();
                    if (r && r.length > 20) return r;
                }
                return null;
            }""")
            if tok:
                print(f'[hcap] GOT TOKEN: {tok[:40]}')
                return tok
        except: pass
    
    print('[hcap] no token after answer submit')
    return None

async def main():
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
                print(f'  [API] {req.method} {req.url[-60:]} | {body[:80]}')
            elif 'hcaptcha' in req.url and not any(x in req.url for x in ['.js','.css','.png','.woff','logo']):
                print(f'  [HCAP] {req.method} {req.url[:80]}')
        async def on_resp(resp):
            if 'api.talordata' in resp.url:
                try: d=await resp.json(); print(f'  [RESP] code={d.get("code")} {d.get("message")}')
                except: pass
            elif 'getcaptcha' in resp.url or 'checksiteconfig' in resp.url:
                try: t=await resp.text(); print(f'  [HCAP-RESP] {resp.status}: {t[:120]}')
                except: pass
        page.on('request', on_req)
        page.on('response', on_resp)

        print('[*] Loading /reg...')
        await page.goto('https://dashboard.talordata.com/reg', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(5)

        await page.click('input[placeholder*="email" i]')
        await page.type('input[placeholder*="email" i]', email, delay=40)
        await asyncio.sleep(0.5)
        await page.click('input[type="password"]')
        await page.type('input[type="password"]', nv_pwd, delay=55)
        try:
            await page.click('input[placeholder*="invitation" i]')
            await page.type('input[placeholder*="invitation" i]', 'z46vzbz4', delay=70)
        except: pass
        await asyncio.sleep(0.3)

        cb = await page.query_selector('.el-checkbox__inner')
        if cb:
            box = await cb.bounding_box()
            if box:
                await page.mouse.move(box['x']+3, box['y']+3, steps=4)
                await page.mouse.click(box['x']+3, box['y']+3)
                await asyncio.sleep(0.5)

        checked = await page.evaluate("() => document.querySelector('input[type=checkbox]')?.checked")
        print(f'[*] checkbox: {checked}')
        await asyncio.sleep(1)

        # Click Sign Up via text selector
        signup = page.get_by_text('Sign Up').last
        box = await signup.bounding_box()
        if box:
            await page.mouse.move(box['x']+box['width']//2, box['y']+box['height']//2, steps=8)
            await asyncio.sleep(0.4)
            await page.mouse.click(box['x']+box['width']//2, box['y']+box['height']//2)
            print('[*] Sign Up clicked')

        # Wait for hcaptcha
        print('[*] waiting for hcaptcha...')
        for i in range(15):
            await asyncio.sleep(1)
            frames = [f for f in page.frames if 'hcaptcha' in f.url]
            if frames: print(f'[{i}s] {len(frames)} hcaptcha frames'); break
        
        frames = [f for f in page.frames if 'hcaptcha' in f.url]
        
        # Now solve the audio challenge
        token = await solve_hcaptcha_audio(page)
        
        if token:
            # Use token to call send_activation
            print(f'[*] calling send_activation...')
            r = rq.post(f'{API}/users/v1/auth/send_activation',
                       json={'email': email, 'hcaptcha': token}, headers=H, timeout=10)
            d = r.json()
            print(f'[*] send_activation: code={d.get("code")} msg={d.get("message")}')
            
            if d.get('code') == 0:
                # Poll for email code
                email_code = None
                for i in range(18):
                    await asyncio.sleep(5)
                    _, msgs = mail_req('GET', '/messages', token=mt_tok)
                    items = (msgs or {}).get('hydra:member', [])
                    if items:
                        for msg in items:
                            _, full = mail_req('GET', f'/messages/{msg["id"]}', token=mt_tok)
                            full_text = str(full.get('text','')) + str(full.get('html',''))
                            codes = re.findall(r'\b(\d{6})\b', full_text)
                            jwts = re.findall(r'token[=:]([A-Za-z0-9_.-]{50,})', full_text)
                            if codes: email_code = codes[0]; break
                            if jwts: email_code = f'JWT:{jwts[0]}'; break
                        if email_code: break
                    print(f'  [{(i+1)*5}s] waiting email...')
                
                if email_code:
                    if email_code.startswith('JWT:'):
                        jwt = email_code[4:]
                        r3 = rq.get(f'{API}/users/v1/auth/activation', params={'token':jwt},
                                   headers={k:v for k,v in H.items() if k!='Content-Type'}, timeout=10)
                        d3 = r3.json()
                        print(f'[*] activation(jwt): code={d3.get("code")} msg={d3.get("message")}')
                        success = d3.get('code') == 0
                    else:
                        r2 = rq.post(f'{API}/users/v1/auth/register',
                                    json={'email':email,'password':nv_pwd,'code':email_code},
                                    headers=H, timeout=10)
                        d2 = r2.json()
                        jwt = d2.get('data','') if isinstance(d2.get('data'),str) else (d2.get('data') or {}).get('token','')
                        print(f'[*] register: code={d2.get("code")} msg={d2.get("message")}')
                        if jwt:
                            r3 = rq.get(f'{API}/users/v1/auth/activation', params={'token':jwt},
                                       headers={k:v for k,v in H.items() if k!='Content-Type'}, timeout=10)
                            d3 = r3.json()
                            print(f'[*] activation: code={d3.get("code")} msg={d3.get("message")}')
                            success = d3.get('code') == 0
                        else:
                            success = False
                    
                    if success:
                        print(f'\n✅ SUCCESS: {email} | {nv_pwd}')
                        import psycopg2
                        try:
                            conn = psycopg2.connect('postgresql://postgres:postgres@localhost/toolkit')
                            cur = conn.cursor()
                            cur.execute('CREATE TABLE IF NOT EXISTS accounts (id SERIAL PRIMARY KEY, platform TEXT, email TEXT, password TEXT, notes TEXT, created_at TIMESTAMP DEFAULT NOW())')
                            cur.execute('INSERT INTO accounts (platform,email,password,notes) VALUES (%s,%s,%s,%s)',
                                       ('talordata', email, nv_pwd, 'activated'))
                            conn.commit(); conn.close()
                            print('[*] saved to DB')
                        except Exception as e: print(f'DB: {e}')
        else:
            print('[!] no hcaptcha token')

        await browser.close()

asyncio.run(main())
