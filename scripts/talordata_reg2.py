#!/usr/bin/env python3
"""
talordata.com /reg 页面注册 — 修复版
用 playwright 填表，JS点checkbox，触发hcaptcha音频挑战+whisper
"""
import asyncio, json, re, secrets, string, time, urllib.request, urllib.error
import requests as rq

API = 'https://api.talordata.com'
INVITE_CODE = 'z46vzbz4'
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

async def get_hcaptcha_token_audio(page):
    """Click audio challenge in hcaptcha and transcribe with whisper"""
    import whisper, tempfile, subprocess
    
    # Wait for challenge frame (not the checkbox frame)
    challenge_frame = None
    for attempt in range(20):
        await asyncio.sleep(1)
        for frame in page.frames:
            if 'hcaptcha' in frame.url and 'challenge' in frame.url:
                challenge_frame = frame
                break
        if challenge_frame:
            print(f'[hcap] challenge frame found (attempt {attempt+1})')
            break
    
    if not challenge_frame:
        # Look for any hcaptcha frame
        hcap_frames = [f for f in page.frames if 'hcaptcha' in f.url]
        print(f'[hcap] frames: {[f.url[:60] for f in hcap_frames]}')
        if hcap_frames:
            challenge_frame = hcap_frames[-1]
    
    if not challenge_frame:
        print('[hcap] no challenge frame found')
        return None

    await asyncio.sleep(2)
    
    # Click audio accessibility button
    audio_clicked = False
    for sel in [
        'button[aria-label*="audio" i]',
        '[class*="audio"]',
        '.challenge-switchtype',
        'button:has-text("Audio")',
    ]:
        try:
            el = await challenge_frame.query_selector(sel)
            if el:
                await el.click()
                audio_clicked = True
                print(f'[hcap] clicked audio btn: {sel}')
                await asyncio.sleep(2)
                break
        except:
            pass
    
    if not audio_clicked:
        # Print frame HTML to debug
        try:
            content = await challenge_frame.content()
            print(f'[hcap] frame content (400): {content[:400]}')
        except: pass
    
    # Find audio src
    audio_url = None
    for attempt in range(10):
        await asyncio.sleep(1)
        for frame in page.frames:
            if 'hcaptcha' not in frame.url:
                continue
            try:
                content = await frame.content()
                # Look for audio element
                m = re.search(r'<audio[^>]*src=["\']([^"\']+)["\']|src=["\']([^"\']+\.mp3[^"\']*)["\']', content, re.I)
                if m:
                    audio_url = m.group(1) or m.group(2)
                    print(f'[hcap] audio url found: {audio_url[:80]}')
                    break
            except:
                pass
        if audio_url:
            break
    
    if not audio_url:
        # Try evaluating JS to get audio
        for frame in page.frames:
            if 'hcaptcha' not in frame.url:
                continue
            try:
                audio_url = await frame.evaluate('''() => {
                    const a = document.querySelector("audio");
                    return a ? (a.src || a.querySelector("source")?.src) : null;
                }''')
                if audio_url:
                    print(f'[hcap] audio via JS: {audio_url[:80]}')
                    break
            except:
                pass
    
    if not audio_url:
        print('[hcap] no audio URL found')
        return None
    
    # Download audio
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        audio_path = f.name
    wav_path = audio_path.replace('.mp3', '.wav')
    
    try:
        resp = rq.get(audio_url, timeout=30)
        open(audio_path, 'wb').write(resp.content)
        print(f'[hcap] downloaded {len(resp.content)}B')
    except Exception as e:
        print(f'[hcap] download fail: {e}'); return None
    
    subprocess.run(['ffmpeg','-i',audio_path,'-ar','16000','-ac','1',wav_path,'-y'], 
                   capture_output=True)
    
    model = whisper.load_model('base')
    result = model.transcribe(wav_path, language='en')
    text = re.sub(r'[^a-z0-9\s]', '', result['text'].strip().lower()).strip()
    print(f'[hcap] whisper: "{text}"')
    
    # Enter into audio input field
    for frame in page.frames:
        if 'hcaptcha' not in frame.url: continue
        try:
            inp = await frame.query_selector('input[type="text"], .audio-input input, [placeholder*="answer" i]')
            if inp:
                await inp.fill(text)
                await asyncio.sleep(0.3)
                # Submit
                verify_btn = await frame.query_selector('button[type="submit"], .verify-btn, button:has-text("Verify")')
                if verify_btn:
                    await verify_btn.click()
                    await asyncio.sleep(3)
                    print('[hcap] submitted')
                break
        except Exception as e:
            print(f'[hcap] input/submit error: {e}')
    
    await asyncio.sleep(3)
    # Get resulting hcaptcha token
    for frame in page.frames:
        try:
            tok = await frame.evaluate('() => document.querySelector("[name=h-captcha-response]")?.value || null')
            if tok:
                print(f'[hcap] token: {tok[:40]}')
                return tok
        except: pass
    # Also check main page
    try:
        tok = await page.evaluate('''() => document.querySelector('[name="h-captcha-response"]')?.value || 
                                     window.hcaptchaToken || null''')
        if tok:
            print(f'[hcap] page token: {tok[:40]}')
            return tok
    except: pass
    return None


async def do_register(email, mt_tok, nv_pwd):
    from playwright.async_api import async_playwright
    
    api_calls = []
    hcap_token_ref = {'token': None}
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            '--no-sandbox','--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
        ])
        ctx = await browser.new_context(
            viewport={'width':1280,'height':800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='en-US',
        )
        await ctx.add_init_script("""
            Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
            Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3]});
            window.chrome={runtime:{}};
        """)
        page = await ctx.new_page()
        
        # Intercept API calls to capture exact request format
        async def on_req(req):
            if 'api.talordata' in req.url:
                body = req.post_data or ''
                api_calls.append({'url':req.url,'method':req.method,'body':body})
                print(f'  [REQ] {req.method} {req.url} body={body[:120]}')
        async def on_resp(resp):
            if 'api.talordata' in resp.url:
                try:
                    d = await resp.json()
                    print(f'  [RESP] {resp.status} {resp.url}: {json.dumps(d)[:150]}')
                except: pass
        
        page.on('request', on_req)
        page.on('response', on_resp)
        
        print(f'[reg] loading /reg page...')
        await page.goto('https://dashboard.talordata.com/reg', wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(3)
        
        print(f'[reg] title: {await page.title()}')
        
        # Fill email
        await page.fill('input[placeholder*="email" i]', email)
        await asyncio.sleep(0.5)
        # Fill password
        await page.fill('input[type="password"]', nv_pwd)
        await asyncio.sleep(0.5)
        # Fill invitation code (optional but helpful)
        try:
            inv_inp = page.locator('input[placeholder*="invitation" i]').first
            if await inv_inp.is_visible(timeout=2000):
                await inv_inp.fill(INVITE_CODE)
        except: pass
        await asyncio.sleep(0.3)
        # Check agree checkbox via JS (it might be visually hidden)
        await page.evaluate("""() => {
            const cb = document.querySelector('input[type="checkbox"]');
            if (cb && !cb.checked) {
                cb.checked = true;
                cb.dispatchEvent(new Event('change', {bubbles: true}));
                cb.dispatchEvent(new Event('input', {bubbles: true}));
            }
        }""")
        print('[reg] checkbox checked via JS')
        await asyncio.sleep(0.5)
        
        await page.screenshot(path='/tmp/reg2_filled.png')
        
        # Click Sign Up
        for selector in ['button:has-text("Sign Up")', 'button[type="submit"]',
                         '.el-button--primary', 'button.register-btn']:
            try:
                btn = page.locator(selector).last
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    print(f'[reg] clicked: {selector}')
                    break
            except: pass
        
        await asyncio.sleep(3)
        await page.screenshot(path='/tmp/reg2_after_submit.png')
        
        # Check if hcaptcha appeared
        hcap_frames = [f for f in page.frames if 'hcaptcha' in f.url]
        print(f'[reg] hcaptcha frames: {len(hcap_frames)}')
        for f in hcap_frames:
            print(f'  frame: {f.url[:80]}')
        
        if hcap_frames:
            print('[reg] hcaptcha triggered! solving with audio+whisper...')
            hcap_token = await get_hcaptcha_token_audio(page)
            hcap_token_ref['token'] = hcap_token
            
            if hcap_token:
                # Now call send_activation with hcaptcha token
                r = rq.post(f'{API}/users/v1/auth/send_activation',
                           json={'email':email, 'hcaptcha':hcap_token},
                           headers=H, timeout=10)
                print(f'[reg] send_activation: {r.status_code} {r.text[:150]}')
        
        # Check API calls made so far
        print(f'\n[reg] API calls so far:')
        for c in api_calls:
            print(f'  {c["method"]} {c["url"]} | {c["body"][:80]}')
        
        # Poll for email code
        print('\n[reg] polling mail.tm for code...')
        email_code = None
        for i in range(24):
            await asyncio.sleep(5)
            _, msgs = mr('GET', '/messages', token=mt_tok)
            items = (msgs or {}).get('hydra:member', [])
            if items:
                for msg in items:
                    _, full = mr('GET', f'/messages/{msg["id"]}', token=mt_tok)
                    text = str(full.get('text',''))
                    codes = re.findall(r'\b(\d{6})\b', text)
                    if codes:
                        email_code = codes[0]
                        print(f'[reg] email code: {email_code}')
                        break
                if email_code:
                    break
            print(f'  [{(i+1)*5}s] waiting...')
        
        if email_code:
            # Submit registration with email code via API
            r2 = rq.post(f'{API}/users/v1/auth/register',
                        json={'email':email,'password':nv_pwd,'code':email_code},
                        headers=H, timeout=10)
            d2 = r2.json()
            print(f'[reg] register: code={d2.get("code")} msg={d2.get("message")}')
            
            jwt = d2.get('data','') if isinstance(d2.get('data'),str) else (d2.get('data') or {}).get('token','')
            if jwt:
                r3 = rq.get(f'{API}/users/v1/auth/activation', params={'token':jwt},
                           headers={k:v for k,v in H.items() if k!='Content-Type'}, timeout=10)
                d3 = r3.json()
                print(f'[reg] activation: code={d3.get("code")} {d3.get("message")}')
                if d3.get('code') == 0:
                    await browser.close()
                    return email, nv_pwd, jwt
        
        await browser.close()
    
    return None


async def main():
    chars = string.ascii_lowercase + string.digits
    login = ''.join(secrets.choice(chars) for _ in range(14))
    email = f'{login}@deltajohnsons.com'
    mt_pwd = 'P@' + secrets.token_hex(10)
    nv_pwd = 'Aa1!' + ''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(12))
    
    # Create mail.tm inbox
    mr('POST', '/accounts', {'address':email,'password':mt_pwd})
    _, tb = mr('POST', '/token', {'address':email,'password':mt_pwd})
    mt_tok = tb.get('token','')
    print(f'[main] email={email} mt_tok={mt_tok[:20]}')
    
    result = await do_register(email, mt_tok, nv_pwd)
    if result:
        print(f'\n[main] ✅ SUCCESS: {result[0]} | {result[1]}')
    else:
        print(f'\n[main] ❌ FAILED')

asyncio.run(main())
