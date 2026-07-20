#!/usr/bin/env python3
"""
v16: Visual challenge solver using PIL
Strategy: Screenshot challenge frame → detect white circle icons →
compare color features → click the outlier (most different icon)
"""
import asyncio, re, secrets, string, json, io, base64
import requests as rq, urllib.request, urllib.error
from PIL import Image
import numpy as np

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

def find_icon_centers(frame_img_bytes):
    """Find centers of white circular icons in hcaptcha challenge.
    Returns list of (cx, cy) in frame-local pixel coords."""
    img = Image.open(io.BytesIO(frame_img_bytes)).convert('RGB')
    arr = np.array(img)
    h, w = arr.shape[:2]
    
    # The image area excludes the title bar (~100px at top) and controls (~65px at bottom)
    y1, y2 = 115, h - 65
    x1, x2 = 10, w - 10
    
    # Find white pixels (icon borders/backgrounds are white)
    img_area = arr[y1:y2, x1:x2]
    white = (img_area[:,:,0] > 210) & (img_area[:,:,1] > 210) & (img_area[:,:,2] > 210)
    
    # Simple connected components using numpy (no scipy)
    # Scan for white blobs
    visited = np.zeros_like(white, dtype=bool)
    blobs = []
    
    def bfs(sy, sx):
        """BFS to find connected white region"""
        queue = [(sy, sx)]
        pixels = []
        visited[sy, sx] = True
        while queue:
            y, x = queue.pop()
            pixels.append((y, x))
            for dy, dx in [(-1,0),(1,0),(0,-1),(0,1)]:
                ny, nx = y+dy, x+dx
                if 0 <= ny < white.shape[0] and 0 <= nx < white.shape[1]:
                    if white[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((ny, nx))
        return pixels
    
    # Downsample for speed
    step = 3
    for y in range(0, white.shape[0], step):
        for x in range(0, white.shape[1], step):
            if white[y, x] and not visited[y, x]:
                pixels = bfs(y, x)
                if len(pixels) > 300:  # min blob size
                    blobs.append(pixels)
    
    print(f'  found {len(blobs)} white blobs (min 300px)')
    
    centers = []
    for blob in blobs:
        ys = [p[0] for p in blob]
        xs = [p[1] for p in blob]
        cy = (min(ys) + max(ys)) / 2 + y1
        cx = (min(xs) + max(xs)) / 2 + x1
        bh = max(ys) - min(ys)
        bw = max(xs) - min(xs)
        # Filter: icon circles should be roughly square (h ≈ w) and reasonable size
        if bh > 30 and bw > 30 and bh < 200 and bw < 200 and abs(bh - bw) < 50:
            centers.append({'cx': cx, 'cy': cy, 'h': bh, 'w': bw, 'area': len(blob)})
    
    return centers

def compute_icon_feature(frame_img_bytes, cx, cy, radius=35):
    """Extract color histogram from icon center region"""
    img = Image.open(io.BytesIO(frame_img_bytes)).convert('RGB')
    arr = np.array(img)
    h, w = arr.shape[:2]
    y1 = max(0, int(cy - radius))
    y2 = min(h, int(cy + radius))
    x1 = max(0, int(cx - radius))
    x2 = min(w, int(cx + radius))
    patch = arr[y1:y2, x1:x2]
    # Compute color histogram (R, G, B means)
    if patch.size == 0:
        return np.array([0., 0., 0.])
    return np.array([patch[:,:,0].mean(), patch[:,:,1].mean(), patch[:,:,2].mean()])

def find_outlier_icon(frame_img_bytes):
    """Find the icon that doesn't fit the pattern"""
    centers = find_icon_centers(frame_img_bytes)
    print(f'  icon centers: {len(centers)}')
    for c in centers:
        print(f'    cx={c["cx"]:.0f} cy={c["cy"]:.0f} area={c["area"]}')
    
    if len(centers) < 3:
        return None
    
    # Compute features for each icon
    features = []
    for c in centers:
        feat = compute_icon_feature(frame_img_bytes, c['cx'], c['cy'])
        features.append(feat)
        print(f'    feature [{c["cx"]:.0f},{c["cy"]:.0f}]: R={feat[0]:.1f} G={feat[1]:.1f} B={feat[2]:.1f}')
    
    # Find outlier: the one whose feature is most different from the mean of others
    features_arr = np.array(features)
    scores = []
    for i, feat in enumerate(features_arr):
        others = np.delete(features_arr, i, axis=0)
        mean_others = others.mean(axis=0)
        dist = np.linalg.norm(feat - mean_others)
        scores.append(dist)
        print(f'    icon {i} dist from others: {dist:.2f}')
    
    best_idx = int(np.argmax(scores))
    print(f'  OUTLIER: icon {best_idx} at cx={centers[best_idx]["cx"]:.0f} cy={centers[best_idx]["cy"]:.0f}')
    return centers[best_idx]

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

async def solve_visual_challenge(page, challenge_box, max_attempts=5):
    """Take screenshot of challenge frame, analyze, click outlier icon"""
    token = None
    for attempt in range(max_attempts):
        print(f'\n[*] Visual solve attempt {attempt+1}/{max_attempts}')
        
        # Screenshot the challenge frame
        frame_bytes = None
        for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
            try:
                handle = await frame.frame_element()
                b = await handle.bounding_box()
                if b and b.get('width', 0) > 300:
                    frame_bytes = await handle.screenshot()
                    break
            except: pass
        
        if not frame_bytes:
            print('  no frame screenshot')
            break
        
        # Save for debugging
        with open(f'/tmp/v16_frame_{attempt}.png', 'wb') as f:
            f.write(frame_bytes)
        
        # Find outlier icon
        outlier = find_outlier_icon(frame_bytes)
        if not outlier:
            print('  no outlier found, clicking ↻ to refresh')
            # Click refresh button
            rx = challenge_box['x'] + challenge_box['width'] * (73/520)
            ry = challenge_box['y'] + challenge_box['height'] * (541/570)
            await page.mouse.click(rx, ry)
            await asyncio.sleep(3)
            continue
        
        # Convert frame-local coords to page coords
        # The frame position matches challenge_box
        page_cx = challenge_box['x'] + outlier['cx']
        page_cy = challenge_box['y'] + outlier['cy']
        
        print(f'  clicking outlier at page ({page_cx:.0f},{page_cy:.0f})')
        await page.mouse.click(page_cx, page_cy)
        await asyncio.sleep(2)
        
        # Screenshot after click
        await page.screenshot(path=f'/tmp/v16_after_click_{attempt}.png')
        
        # Check for token (challenge solved)
        token = await get_token(page)
        if token:
            print(f'  TOKEN: {token[:40]}')
            return token
        
        # Check if challenge changed (might have loaded next question)
        # Check for "Please try again" in frame text
        challenge_failed = False
        for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
            try:
                texts = await frame.evaluate('''() => Array.from(document.querySelectorAll("*"))
                    .filter(e => e.offsetParent !== null && e.childElementCount === 0)
                    .map(e => e.textContent?.trim()).filter(t => t && t.length > 1 && t.length < 100)''')
                if any('try again' in t.lower() for t in texts):
                    challenge_failed = True
                    print(f'  challenge failed, refreshing...')
                    break
                print(f'  frame texts: {[t for t in texts if t.strip()][:5]}')
            except: pass
        
        # Check for multiple challenges (progress bar moved = partial solve)
        await asyncio.sleep(2)
        token = await get_token(page)
        if token: return token
        
        # Continue to next attempt (challenge refreshes automatically or we refresh)
        if challenge_failed:
            # Click ↻ to refresh
            rx = challenge_box['x'] + challenge_box['width'] * (73/520)
            ry = challenge_box['y'] + challenge_box['height'] * (541/570)
            await page.mouse.click(rx, ry)
            await asyncio.sleep(3)
    
    return token

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

        async def on_resp(r):
            url = r.url
            if 'api.talordata' in url:
                try: d = await r.json(); print(f'  [API] {d.get("code")} {d.get("message")}')
                except: pass
            elif 'getcaptcha' in url:
                getcap_done[0] = True; print('  [getcap]')
            elif 'checkcaptcha' in url:
                try: d = await r.json(); print(f'  [verify] success={d.get("generated_pass_UUID","")[:20]}')
                except: pass
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
            if box: await page.mouse.click(box['x'] + 3, box['y'] + 3); await asyncio.sleep(0.3)
        signup = page.get_by_text('Sign Up').last
        box = await signup.bounding_box()
        if box: await page.mouse.click(box['x'] + box['width'] // 2, box['y'] + box['height'] // 2)
        print('clicked Sign Up')
        for i in range(15):
            await asyncio.sleep(1)
            if getcap_done[0]: print(f'[{i}s] challenge loaded'); break
        await asyncio.sleep(5)

        frames = [f for f in page.frames if 'hcaptcha' in f.url]
        challenge_box = {'x': 546, 'y': 163.9, 'width': 520, 'height': 570}
        for frame in frames:
            try:
                h = await frame.frame_element()
                b = await h.bounding_box()
                if b and b.get('width', 0) > 300: challenge_box = b; break
            except: pass
        print(f'challenge_box: {challenge_box}')

        token = await solve_visual_challenge(page, challenge_box, max_attempts=8)
        
        if token:
            await do_register(email, nv_pwd, token, mt_tok)
        else:
            print('[!] no token obtained via visual solve')
            await page.screenshot(path='/tmp/v16_final.png')
        
        await browser.close()

asyncio.run(main())
