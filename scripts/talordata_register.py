#!/usr/bin/env python3
"""
talordata.com 全自动注册脚本
方案: playwright + hcaptcha音频挑战 + Whisper本地语音识别 (完全免费)
"""
import asyncio, json, re, secrets, string, sys, time, urllib.request, urllib.error
from pathlib import Path
import requests as rq

# ── 配置 ──────────────────────────────────────────────────────────
API = 'https://api.talordata.com'
HCAP_SITEKEY = 'aef235d8-778c-4db6-ba04-dddef1fa9916'
H = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Origin': 'https://dashboard.talordata.com',
    'Content-Type': 'application/json',
}
DB_URL = 'postgresql://postgres:postgres@localhost/toolkit'

def gen_email_addr():
    chars = string.ascii_lowercase + string.digits
    login = ''.join(secrets.choice(chars) for _ in range(14))
    return f'{login}@deltajohnsons.com'

def gen_password():
    return 'Aa1!' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))

# ── mail.tm 工具 ─────────────────────────────────────────────────
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
    except Exception as ex:
        return 0, {'error': str(ex)}

def create_inbox():
    addr = gen_email_addr()
    mt_pwd = 'P@' + secrets.token_hex(10)
    mr('POST', '/accounts', {'address': addr, 'password': mt_pwd})
    _, tb = mr('POST', '/token', {'address': addr, 'password': mt_pwd})
    tok = tb.get('token', '')
    print(f'[mail.tm] inbox: {addr}')
    return addr, mt_pwd, tok

def poll_email_code(mt_tok, timeout=120):
    """Poll mail.tm for email verification code"""
    print(f'[mail] polling for code (max {timeout}s)...')
    for i in range(timeout // 5):
        time.sleep(5)
        _, msgs = mr('GET', '/messages', token=mt_tok)
        items = (msgs or {}).get('hydra:member', [])
        if items:
            for msg in items:
                mid = msg.get('id', '')
                _, full = mr('GET', f'/messages/{mid}', token=mt_tok)
                text = str(full.get('text', ''))
                html_list = full.get('html', [])
                html = ' '.join(str(x) for x in (html_list if isinstance(html_list, list) else [html_list]))
                all_text = text + ' ' + html
                codes = re.findall(r'\b(\d{6})\b', all_text)
                if codes:
                    print(f'[mail] got code: {codes[0]}')
                    return codes[0]
        print(f'  [{(i+1)*5}s] no email yet...')
    return None

# ── hcaptcha 音频绕过 ─────────────────────────────────────────────
async def solve_hcaptcha_audio(page):
    """在 hcaptcha iframe 中切换到音频挑战，用 Whisper 识别"""
    import whisper, tempfile, subprocess
    
    print('[hcap] waiting for hcaptcha iframe...')
    # Wait for hcaptcha iframe to appear
    try:
        await page.wait_for_selector('iframe[src*="hcaptcha"]', timeout=30000)
    except Exception as e:
        print(f'[hcap] no hcaptcha iframe found: {e}')
        return None
    
    # Get the hcaptcha iframe
    hcap_frame = None
    for frame in page.frames:
        if 'hcaptcha' in frame.url:
            hcap_frame = frame
            break
    
    if not hcap_frame:
        print('[hcap] cannot find hcaptcha frame')
        return None
    
    print(f'[hcap] frame found: {hcap_frame.url[:60]}')
    
    # Click the hcaptcha checkbox first (to trigger it)
    try:
        checkbox = await hcap_frame.query_selector('.challenge-checkbox, #checkbox')
        if checkbox:
            await checkbox.click()
            await asyncio.sleep(2)
            print('[hcap] checkbox clicked')
    except Exception as e:
        print(f'[hcap] checkbox click: {e}')
    
    # Find the challenge frame (may be different from the checkbox frame)
    challenge_frame = None
    for attempt in range(10):
        await asyncio.sleep(1)
        for frame in page.frames:
            if 'hcaptcha' in frame.url and 'challenge' in frame.url:
                challenge_frame = frame
                break
        if challenge_frame:
            break
    
    if not challenge_frame:
        # Try using the same frame
        challenge_frame = hcap_frame
    
    print(f'[hcap] challenge frame: {challenge_frame.url[:60]}')
    
    # Click audio button (accessibility)
    audio_btn_selectors = [
        'button[aria-label*="audio"]',
        '.challenge-switchtype',
        'button.audio',
        '[aria-label="Get an audio challenge"]',
        '.challenge-help',
    ]
    for sel in audio_btn_selectors:
        try:
            btn = await challenge_frame.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                print(f'[hcap] clicked audio btn: {sel}')
                await asyncio.sleep(2)
                break
        except Exception as e:
            pass
    
    # Find audio element
    audio_url = None
    for attempt in range(10):
        await asyncio.sleep(1)
        for frame in page.frames:
            if 'hcaptcha' not in frame.url:
                continue
            try:
                audio = await frame.query_selector('audio, audio source')
                if audio:
                    src = await audio.get_attribute('src')
                    if src:
                        audio_url = src
                        break
            except:
                pass
        if audio_url:
            break
    
    if not audio_url:
        # Try getting from page source
        for frame in page.frames:
            if 'hcaptcha' not in frame.url:
                continue
            try:
                content = await frame.content()
                m = re.search(r'href=["\']([^"\']+\.mp3[^"\']*)["\']|src=["\']([^"\']+\.mp3[^"\']*)["\']', content)
                if m:
                    audio_url = m.group(1) or m.group(2)
                    break
            except:
                pass
    
    if not audio_url:
        print('[hcap] could not find audio URL')
        return None
    
    print(f'[hcap] audio URL: {audio_url[:80]}')
    
    # Download audio
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        audio_path = f.name
    
    try:
        resp = rq.get(audio_url, timeout=30)
        with open(audio_path, 'wb') as f:
            f.write(resp.content)
        print(f'[hcap] audio downloaded: {len(resp.content)} bytes')
    except Exception as e:
        print(f'[hcap] audio download failed: {e}')
        return None
    
    # Convert to wav if needed
    wav_path = audio_path.replace('.mp3', '.wav')
    subprocess.run(['ffmpeg', '-i', audio_path, '-ar', '16000', '-ac', '1', wav_path, '-y'],
                   capture_output=True)
    
    # Transcribe with Whisper
    print('[hcap] transcribing with whisper...')
    try:
        model = whisper.load_model('base')
        result = model.transcribe(wav_path, language='en')
        text = result['text'].strip().lower()
        # Clean: only keep alphanumeric
        text = re.sub(r'[^a-z0-9\s]', '', text).strip()
        print(f'[hcap] whisper result: "{text}"')
    except Exception as e:
        print(f'[hcap] whisper failed: {e}')
        return None
    
    # Enter the transcription
    for frame in page.frames:
        if 'hcaptcha' not in frame.url:
            continue
        try:
            inp = await frame.query_selector('input[type="text"], .audio-input input')
            if inp:
                await inp.fill(text)
                await asyncio.sleep(0.5)
                print(f'[hcap] entered text: {text}')
                
                # Submit
                submit_btn = await frame.query_selector('button[type="submit"], .button-submit, .verify-btn')
                if submit_btn:
                    await submit_btn.click()
                    await asyncio.sleep(2)
                    print('[hcap] submitted audio answer')
        except Exception as e:
            print(f'[hcap] input error: {e}')
    
    # Wait and check for hcaptcha token
    await asyncio.sleep(3)
    
    # Get token from page
    hcap_token = await page.evaluate('''() => {
        const el = document.querySelector('[name="h-captcha-response"]');
        return el ? el.value : null;
    }''')
    
    print(f'[hcap] token: {hcap_token[:30] if hcap_token else "NONE"}')
    return hcap_token


# ── 主注册流程 ────────────────────────────────────────────────────
async def register_one(email, mt_pwd, mt_tok):
    from playwright.async_api import async_playwright
    
    nv_pwd = gen_password()
    print(f'\n[reg] email={email} pwd={nv_pwd}')
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox', '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars', '--lang=en-US',
            ]
        )
        ctx = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='en-US',
        )
        # Stealth init script
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
            window.chrome = {runtime: {}};
        """)
        page = await ctx.new_page()
        
        # Intercept to capture hcaptcha token
        hcap_token_captured = {'token': None}
        
        async def intercept_request(route):
            req = route.request
            if 'api.talordata' in req.url and 'send_activation' in req.url:
                body = req.post_data or ''
                print(f'[intercept] send_activation: {body[:100]}')
            await route.continue_()
        
        await page.route('**/*', intercept_request)
        
        print('[reg] loading register page...')
        await page.goto('https://dashboard.talordata.com/register',
                        wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(3)
        
        title = await page.title()
        print(f'[reg] title: {title}')
        await page.screenshot(path='/tmp/reg_step1.png')
        
        # Find email input
        print('[reg] filling email field...')
        email_sel = None
        for sel in ['input[type="email"]', 'input[name="email"]',
                    'input[placeholder*="email" i]', 'input[placeholder*="邮" ]',
                    'input.el-input__inner']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    email_sel = sel
                    await el.fill(email)
                    print(f'[reg] filled email with sel: {sel}')
                    break
            except:
                pass
        
        if not email_sel:
            body = await page.inner_text('body')
            print(f'[reg] WARN: no email input found. body[:300]: {body[:300]}')
        
        await asyncio.sleep(1)
        
        # Look for "Get Code" / "发送验证码" button
        print('[reg] looking for Get Code button...')
        code_btn = None
        for txt in ['Get Code', '发送验证码', 'Send Code', '获取验证码', 'Verify', 'Send']:
            try:
                btn = page.get_by_text(txt, exact=False).first
                if await btn.is_visible(timeout=2000):
                    code_btn = btn
                    print(f'[reg] found button: {txt}')
                    break
            except:
                pass
        
        if code_btn:
            await code_btn.click()
            print('[reg] clicked Get Code button')
            await asyncio.sleep(2)
        
        await page.screenshot(path='/tmp/reg_step2.png')
        
        # Check if hcaptcha appeared
        hcap_visible = False
        try:
            await page.wait_for_selector('iframe[src*="hcaptcha"]', timeout=8000)
            hcap_visible = True
            print('[reg] hcaptcha appeared!')
        except:
            print('[reg] no hcaptcha iframe (might auto-execute or already solved)')
        
        hcap_token = None
        if hcap_visible:
            hcap_token = await solve_hcaptcha_audio(page)
        
        # If we got hcaptcha token, call send_activation directly via API
        if hcap_token:
            print(f'[reg] calling send_activation with token...')
            r = rq.post(f'{API}/users/v1/auth/send_activation',
                       json={'email': email, 'hcaptcha': hcap_token},
                       headers=H, timeout=10)
            print(f'[reg] send_activation: {r.status_code} {r.text[:150]}')
        
        # Poll for email code
        email_code = poll_email_code(mt_tok, timeout=90)
        
        if not email_code:
            print('[reg] no code received, trying register without code...')
            # Try the register endpoint directly
            r1 = rq.post(f'{API}/users/v1/auth/register',
                        json={'email': email, 'password': nv_pwd},
                        headers=H, timeout=10)
            d1 = r1.json()
            print(f'[reg] register(no code): code={d1.get("code")} msg={d1.get("message")}')
            
            jwt = ''
            if isinstance(d1.get('data'), dict):
                jwt = d1['data'].get('token', '')
            elif isinstance(d1.get('data'), str):
                jwt = d1['data']
            
            if jwt:
                # Try activation
                r2 = rq.get(f'{API}/users/v1/auth/activation', params={'token': jwt},
                           headers={k: v for k, v in H.items() if k != 'Content-Type'},
                           timeout=10)
                d2 = r2.json()
                print(f'[reg] activation: code={d2.get("code")} msg={d2.get("message")}')
                if d2.get('code') == 0:
                    print('[reg] ✅ ACCOUNT ACTIVATED!')
                    await browser.close()
                    return email, nv_pwd, jwt
        
        # If we have email code, fill it and register
        if email_code:
            # Fill code in form
            for sel in ['input.verification-code', 'input[maxlength="6"]',
                       'input[placeholder*="code" i]', 'input[placeholder*="验证码"]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.fill(email_code)
                        print(f'[reg] filled code: {email_code}')
                        break
                except:
                    pass
            
            # Fill password
            for sel in ['input[type="password"]', 'input[name="password"]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.fill(nv_pwd)
                        print(f'[reg] filled password')
                        break
                except:
                    pass
            
            # Also call register API directly
            r3 = rq.post(f'{API}/users/v1/auth/register',
                        json={'email': email, 'password': nv_pwd, 'code': email_code},
                        headers=H, timeout=10)
            d3 = r3.json()
            print(f'[reg] register(code): code={d3.get("code")} msg={d3.get("message")}')
            
            jwt = ''
            if isinstance(d3.get('data'), dict):
                jwt = d3['data'].get('token', '')
            elif isinstance(d3.get('data'), str):
                jwt = d3['data']
            
            if jwt:
                r4 = rq.get(f'{API}/users/v1/auth/activation', params={'token': jwt},
                           headers={k: v for k, v in H.items() if k != 'Content-Type'},
                           timeout=10)
                d4 = r4.json()
                print(f'[reg] activation: code={d4.get("code")} {d4.get("message")}')
                if d4.get('code') == 0:
                    print('[reg] ✅ ACCOUNT ACTIVATED!')
                    await browser.close()
                    return email, nv_pwd, jwt
        
        await page.screenshot(path='/tmp/reg_final.png')
        await browser.close()
        return None


def save_to_db(email, password, notes=''):
    try:
        import psycopg2
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS accounts
            (id SERIAL PRIMARY KEY, platform TEXT, email TEXT, password TEXT, notes TEXT, created_at TIMESTAMP DEFAULT NOW())''')
        cur.execute('INSERT INTO accounts (platform, email, password, notes) VALUES (%s, %s, %s, %s)',
                   ('talordata', email, password, notes))
        conn.commit()
        conn.close()
        print(f'[db] saved: {email}')
    except Exception as e:
        print(f'[db] error: {e}')


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f'[main] registering {n} talordata accounts...')
    
    success = 0
    for i in range(n):
        print(f'\n{"="*50}')
        print(f'[main] Account {i+1}/{n}')
        email, mt_pwd, mt_tok = create_inbox()
        
        result = await register_one(email, mt_pwd, mt_tok)
        if result:
            email, pwd, jwt = result
            save_to_db(email, pwd, f'activated jwt={jwt[:30]}')
            success += 1
            print(f'[main] ✅ SUCCESS {i+1}: {email}')
        else:
            print(f'[main] ❌ FAILED {i+1}: {email}')
        
        await asyncio.sleep(3)
    
    print(f'\n[main] Done: {success}/{n} accounts created')

if __name__ == '__main__':
    asyncio.run(main())
