#!/usr/bin/env python3
"""
v17: Improved visual solver using interior dark-pixel count
Key insight: the outlier icon has dramatically different dark pixel count
"""
import asyncio, re, secrets, string, json, io
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

def bfs_blobs(mask, min_area=300):
    """Find connected regions in binary mask using BFS"""
    h, w = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    blobs = []
    step = 2  # Skip pixels for speed
    for y in range(0, h, step):
        for x in range(0, w, step):
            if mask[y, x] and not visited[y, x]:
                queue = [(y, x)]
                pixels = []
                visited[y, x] = True
                while queue:
                    py, px = queue.pop()
                    pixels.append((py, px))
                    for dy, dx in [(-1,0),(1,0),(0,-1),(0,1)]:
                        ny, nx = py+dy, px+dx
                        if 0<=ny<h and 0<=nx<w and mask[ny,nx] and not visited[ny,nx]:
                            visited[ny,nx] = True
                            queue.append((ny, nx))
                if len(pixels) >= min_area:
                    blobs.append(pixels)
    return blobs

def analyze_challenge(frame_bytes, attempt=0):
    """
    Returns (cx, cy) of the outlier icon in frame-local coordinates.
    Uses dark-pixel count and orange-pixel count as discriminating features.
    """
    img = Image.open(io.BytesIO(frame_bytes)).convert('RGB')
    arr = np.array(img)
    h, w = arr.shape[:2]
    
    # Image area (exclude title bar ~115px, controls ~65px)
    y1, y2 = 115, h - 65
    x1, x2 = 10, w - 10
    img_area = arr[y1:y2, x1:x2]
    
    # Find white circular regions (icon borders)
    white = (img_area[:,:,0]>210) & (img_area[:,:,1]>210) & (img_area[:,:,2]>210)
    blobs = bfs_blobs(white, min_area=300)
    print(f'  found {len(blobs)} white blobs')
    
    if len(blobs) < 3:
        return None
    
    # Find centers and extract features
    icons = []
    for blob in blobs:
        ys = [p[0] for p in blob]
        xs = [p[1] for p in blob]
        miny, maxy = min(ys), max(ys)
        minx, maxx = min(xs), max(xs)
        bh = maxy - miny
        bw = maxx - minx
        # Filter: icon circles should be roughly square
        if bh < 30 or bw < 30 or bh > 200 or bw > 200 or abs(bh - bw) > 50:
            continue
        cy = (miny + maxy) / 2 + y1
        cx = (minx + maxx) / 2 + x1
        icons.append({'cx': cx, 'cy': cy, 'h': bh, 'w': bw, 'area': len(blob)})
    
    if len(icons) < 3:
        print(f'  only {len(icons)} valid icons after filter')
        return None
    
    print(f'  {len(icons)} valid icons')
    
    # Extract features for each icon
    r = 32  # extraction radius
    features = []
    for icon in icons:
        cy, cx = int(icon['cy']), int(icon['cx'])
        py1 = max(0, cy - r)
        py2 = min(h, cy + r)
        px1 = max(0, cx - r)
        px2 = min(w, cx + r)
        patch = arr[py1:py2, px1:px2]
        
        # Non-white pixels (interior content)
        is_white = (patch[:,:,0]>200) & (patch[:,:,1]>200) & (patch[:,:,2]>200)
        non_white_px = patch[~is_white]
        
        # Dark pixels (black dots/circles inside icon)
        dark_mask = (patch[:,:,0]<100) & (patch[:,:,1]<100) & (patch[:,:,2]<100)
        dark_count = int(dark_mask.sum())
        
        # Orange/red colored pixels
        orange_mask = (patch[:,:,0]>150) & (patch[:,:,0] > patch[:,:,1]+30) & (patch[:,:,0] > patch[:,:,2]+30)
        orange_count = int(orange_mask.sum())
        
        # Non-white non-dark pixels (colored blobs)
        other_colored = len(non_white_px) - dark_count - int(((patch[:,:,0]>200)&(patch[:,:,1]>200)&(patch[:,:,2]>200)).sum())
        
        icon['dark'] = dark_count
        icon['orange'] = orange_count
        icon['non_white'] = len(non_white_px)
        features.append(np.array([dark_count, orange_count, len(non_white_px)], dtype=float))
        print(f'    icon({cx:.0f},{cy:.0f}): dark={dark_count} orange={orange_count} non_white={len(non_white_px)}')
    
    # Find outlier: use dark pixel count as primary feature
    # (works when one icon has dramatically more or fewer dark pixels)
    feat_arr = np.array(features)
    
    # Method 1: Score = distance from median
    median = np.median(feat_arr, axis=0)
    scores = [np.linalg.norm(f - median) for f in feat_arr]
    
    best_idx = int(np.argmax(scores))
    print(f'  OUTLIER: icon {best_idx} ({icons[best_idx]["cx"]:.0f},{icons[best_idx]["cy"]:.0f}) score={scores[best_idx]:.1f}')
    
    # Also log the dark counts to verify
    dark_counts = [ic['dark'] for ic in icons]
    orange_counts = [ic['orange'] for ic in icons]
    dark_mean = np.mean(dark_counts)
    dark_std = np.std(dark_counts)
    print(f'  dark counts: mean={dark_mean:.0f} std={dark_std:.0f} min={min(dark_counts)} max={max(dark_counts)}')
    print(f'  orange counts: mean={np.mean(orange_counts):.0f}')
    
    return icons[best_idx]

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

async def solve_challenge(page, challenge_box, max_attempts=12):
    """Visual + refresh loop to solve hcaptcha challenge"""
    token = None
    refresh_x = challenge_box['x'] + challenge_box['width'] * (73/520)
    refresh_y = challenge_box['y'] + challenge_box['height'] * (541/570)
    
    for attempt in range(max_attempts):
        print(f'\n[*] attempt {attempt+1}/{max_attempts}')
        
        # Wait for challenge to fully load
        await asyncio.sleep(2)
        
        # Screenshot the frame
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
            print('  no frame, refreshing...')
            await page.mouse.click(refresh_x, refresh_y)
            await asyncio.sleep(3)
            continue
        
        # Save for debugging
        with open(f'/tmp/v17_frame_{attempt}.png', 'wb') as f:
            f.write(frame_bytes)
        
        # Analyze
        outlier = analyze_challenge(frame_bytes, attempt)
        
        if not outlier:
            print('  analysis failed, refreshing...')
            await page.mouse.click(refresh_x, refresh_y)
            await asyncio.sleep(3)
            continue
        
        # Click outlier
        page_cx = challenge_box['x'] + outlier['cx']
        page_cy = challenge_box['y'] + outlier['cy']
        print(f'  clicking outlier at ({page_cx:.0f},{page_cy:.0f})')
        await page.mouse.move(page_cx, page_cy, steps=5)
        await asyncio.sleep(0.3)
        await page.mouse.click(page_cx, page_cy)
        await asyncio.sleep(3)
        
        # Check for token
        token = await get_token(page)
        if token:
            print(f'  TOKEN: {token[:40]}')
            return token
        
        # Check frame state
        frame_texts = []
        for frame in [f for f in page.frames if 'hcaptcha' in f.url]:
            try:
                texts = await frame.evaluate('''() => Array.from(document.querySelectorAll("*"))
                    .filter(e => e.offsetParent !== null && e.childElementCount === 0)
                    .map(e => e.textContent?.trim()).filter(t => t && t.length > 1 && t.length < 100)''')
                frame_texts.extend(texts)
            except: pass
        
        if any('try again' in t.lower() for t in frame_texts):
            print('  wrong answer, refreshing...')
            await page.mouse.click(refresh_x, refresh_y)
            await asyncio.sleep(3)
        else:
            print(f'  frame texts: {[t for t in frame_texts if t.strip()][:3]}')
            # Might be on next challenge
        
        token = await get_token(page)
        if token: return token
    
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
            elif 'getcaptcha' in url: getcap_done[0] = True; print('  [getcap]')
            elif 'checkcaptcha' in url:
                try: d = await r.json(); print(f'  [verify] {str(d)[:60]}')
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
        await asyncio.sleep(4)

        frames = [f for f in page.frames if 'hcaptcha' in f.url]
        challenge_box = {'x': 546, 'y': 163.9, 'width': 520, 'height': 570}
        for frame in frames:
            try:
                h = await frame.frame_element()
                b = await h.bounding_box()
                if b and b.get('width', 0) > 300: challenge_box = b; break
            except: pass
        print(f'challenge_box: {challenge_box}')

        token = await solve_challenge(page, challenge_box)
        
        if token:
            await do_register(email, nv_pwd, token, mt_tok)
        else:
            print('[!] all attempts exhausted')
            await page.screenshot(path='/tmp/v17_failed.png')
        
        await browser.close()

asyncio.run(main())
