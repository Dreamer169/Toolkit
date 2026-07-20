#!/usr/bin/env python3
"""
v11: Confirmed coordinates from frame screenshot
Frame[0]: x=546, y=163.9, w=520, h=570 (challenge frame)
≡ button in frame: ~x=28, y=541 → page: (574, 705)
Teal icon: ~x=262, y=541 → page: (808, 705)
Skip: ~x=468, y=541 → page: (1014, 705)
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

async def get_audio(frames):
    for frame in frames:
        try:
            u = await frame.evaluate('() => { var a=document.querySelector("audio"); return a ? (a.src||a.currentSrc||"") : ""; }')
            if u and len(u) > 10: return u
        except: pass
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
    audio_from_net = [None]

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
                    print(f'  [API-RESP] {d.get("code")} {d.get("message")}')
                except: pass
            elif 'getcaptcha' in url:
                getcap_done[0] = True
                print('  [GETCAP loaded]')
            elif ('audio' in url.lower() or url.endswith('.mp3') or url.endswith('.wav')) and 'hcaptcha' in url:
                print(f'  [AUDIO-NET] {url[:80]}')
                audio_from_net[0] = url
        page.on('response', on_resp)

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

        # Step 1: Click Sign Up → hcaptcha widget + challenge appear
        signup = page.get_by_text('Sign Up').last
        box = await signup.bounding_box()
        if box:
            await page.mouse.click(box['x'] + box['width'] // 2, box['y'] + box['height'] // 2)
        print('clicked Sign Up')

        for i in range(20):
            await asyncio.sleep(1)
            if getcap_done[0]:
                print(f'[{i}s] challenge loaded')
                break

        await asyncio.sleep(5)

        # Frame[0] is the challenge frame: x=546, y=163.9, w=520, h=570
        # Based on frame screenshot analysis:
        # ≡ (accessibility menu): x=28, y=541 in frame → page(574, 705)
        # ↻ (refresh): x=73, y=541 → page(619, 705)
        # Teal icon: x=262, y=541 → page(808, 705)

        frames = [f for f in page.frames if 'hcaptcha' in f.url]
        print(f'{len(frames)} hcap frames')

        # Get actual frame[0] position
        challenge_box = None
        for frame in frames:
            try:
                handle = await frame.frame_element()
                box = await handle.bounding_box()
                if box and box.get('width', 0) > 300:
                    challenge_box = box
                    print(f'challenge frame: {box}')
                    break
            except: pass

        if not challenge_box:
            challenge_box = {'x': 546, 'y': 163.9, 'width': 520, 'height': 570}
            print('using default challenge_box')

        # Calculate ≡ position based on frame dimensions
        # In frame screenshot: ≡ at x≈28, y≈541 out of 520x570
        three_lines_x = challenge_box['x'] + challenge_box['width'] * (28/520)
        three_lines_y = challenge_box['y'] + challenge_box['height'] * (541/570)
        print(f'[*] clicking ≡ at ({three_lines_x:.1f}, {three_lines_y:.1f})')

        await page.screenshot(path='/tmp/v11_before_3lines.png')
        await page.mouse.move(three_lines_x, three_lines_y, steps=5)
        await asyncio.sleep(0.5)
        await page.mouse.click(three_lines_x, three_lines_y)
        await asyncio.sleep(2)
        await page.screenshot(path='/tmp/v11_after_3lines.png')

        # Check what appeared - look for menu or audio
        hcap_frames = [f for f in page.frames if 'hcaptcha' in f.url]
        audio_url = await get_audio(hcap_frames)
        print(f'audio after ≡: {audio_url}')

        # Check for menu items in frames
        for i, frame in enumerate(hcap_frames):
            try:
                items = await frame.evaluate('''() => {
                    const els = Array.from(document.querySelectorAll("li, [role=menuitem], [role=option], button, a"));
                    return els.filter(e => e.offsetParent !== null).map(e => ({
                        tag: e.tagName, text: e.textContent?.trim()?.substring(0,40),
                        aria: e.getAttribute("aria-label"), class: e.className?.substring(0,30)
                    }));
                }''')
                vis = [it for it in items if it.get('text') or it.get('aria')]
                if vis: print(f'  frame[{i}] items: {vis[:8]}')
            except: pass

        # If menu appeared, try to click "Audio Challenge" type options
        if not audio_url:
            # Try various menu click positions (menu might appear above the ≡ button)
            menu_attempts = [
                (three_lines_x, three_lines_y - 40),   # above ≡
                (three_lines_x, three_lines_y - 80),   # further above
                (three_lines_x + 20, three_lines_y - 40),
            ]
            for mx, my in menu_attempts:
                await page.mouse.click(mx, my)
                await asyncio.sleep(1)
                audio_url = await get_audio([f for f in page.frames if 'hcaptcha' in f.url])
                if audio_url: print(f'audio after menu click at ({mx:.0f},{my:.0f}): {audio_url[:60]}'); break
            await page.screenshot(path='/tmp/v11_after_menu.png')

        # Try JavaScript to trigger audio challenge via hcaptcha API
        if not audio_url:
            print('[*] trying JS approach to get audio challenge...')
            # hcaptcha exposes an API: hcaptcha.challenge.switch('audio')
            for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
                try:
                    result = await frame.evaluate('''() => {
                        // Try to find and trigger audio
                        try {
                            // hcaptcha internal API attempt
                            if (window._hcaptcha) return "hcaptcha found: " + Object.keys(window._hcaptcha);
                            // Check for challenge object
                            const scripts = Array.from(document.querySelectorAll("script[src]")).map(s=>s.src);
                            return "scripts: " + scripts.join(", ").substring(0,200);
                        } catch(e) { return "err: "+e; }
                    }''')
                    print(f'  frame JS: {result}')
                except Exception as e:
                    print(f'  frame JS err: {e}')

        # Use network-captured audio if available
        if not audio_url and audio_from_net[0]:
            audio_url = audio_from_net[0]
            print(f'Using net-captured audio: {audio_url[:60]}')

        token = None
        if audio_url:
            print(f'Solving audio: {audio_url[:60]}')
            import whisper
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                mp3 = f.name
            if audio_url.startswith('blob:'):
                for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
                    try:
                        data = await frame.evaluate(
                            '(u) => fetch(u).then(r=>r.arrayBuffer()).then(b=>Array.from(new Uint8Array(b)))', audio_url
                        )
                        if data: open(mp3, 'wb').write(bytes(data)); break
                    except: pass
            else:
                open(mp3, 'wb').write(rq.get(audio_url, timeout=30).content)
            wav = mp3.replace('.mp3', '.wav')
            subprocess.run(['ffmpeg', '-i', mp3, '-ar', '16000', '-ac', '1', wav, '-y'], capture_output=True, timeout=30)
            model = whisper.load_model('base')
            result = model.transcribe(wav, language='en')
            text = re.sub(r'[^a-z0-9\s]', '', result['text'].strip().lower()).strip()
            print(f'whisper: "{text}"')
            for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
                try:
                    inp = await frame.query_selector('input[type="text"]')
                    if inp:
                        await inp.fill(text)
                        btn = await frame.query_selector('button[type="submit"], #submit-btn')
                        if btn: await btn.click(); await asyncio.sleep(4)
                        break
                except: pass
            for _ in range(8):
                await asyncio.sleep(1)
                try:
                    tok = await page.evaluate(
                        '() => { var e=document.querySelector("[name=\'h-captcha-response\']"); '
                        'if(e&&e.value.length>20) return e.value; '
                        'if(typeof hcaptcha!="undefined"){var r=hcaptcha.getResponse(); if(r) return r;} return null; }'
                    )
                    if tok: token = tok; print(f'TOKEN: {tok[:40]}...'); break
                except: pass
        else:
            print('[!] no audio URL found')
            await page.screenshot(path='/tmp/v11_no_audio.png')

        if token:
            r = rq.post(f'{API}/users/v1/auth/send_activation', json={'email': email, 'hcaptcha': token}, headers=H, timeout=10)
            d = r.json()
            print(f'send_activation: {d.get("code")} {d.get("message")}')
            if d.get('code') == 0:
                email_code = None
                for i in range(18):
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
                if email_code:
                    r2 = rq.post(f'{API}/users/v1/auth/register', json={'email': email, 'password': nv_pwd, 'code': email_code}, headers=H, timeout=10)
                    d2 = r2.json()
                    jwt = d2.get('data', '') if isinstance(d2.get('data'), str) else (d2.get('data') or {}).get('token', '')
                    if jwt:
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
                            except Exception as e: print(f'DB err: {e}')
        else:
            print('[!] no hcaptcha token')

        await browser.close()

asyncio.run(main())
