import asyncio,re,secrets,string,subprocess,tempfile,json
import requests as rq,urllib.request,urllib.error

API='https://api.talordata.com'
H={'User-Agent':'Mozilla/5.0','Origin':'https://dashboard.talordata.com','Content-Type':'application/json'}

def mr(method,path,data=None,token=None):
    url='https://api.mail.tm'+path
    body=json.dumps(data).encode() if data else None
    h={'Content-Type':'application/json','Accept':'application/json'}
    if token: h['Authorization']=f'Bearer {token}'
    req=urllib.request.Request(url,data=body,headers=h,method=method)
    try:
        with urllib.request.urlopen(req,timeout=20) as r:
            raw=r.read(); return r.status,(json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try: return e.code,json.loads(e.read())
        except: return e.code,{}
    except: return 0,{}

async def get_audio_from_frame(frames):
    for frame in frames:
        try:
            u = await frame.evaluate('() => { const a=document.querySelector("audio"); return a ? (a.src||a.currentSrc) : null; }')
            if u: return u
        except: pass
    return None

async def main():
    chars=string.ascii_lowercase+string.digits
    login=''.join(secrets.choice(chars) for _ in range(14))
    email=f'{login}@deltajohnsons.com'
    mt_pwd='P@'+secrets.token_hex(10)
    nv_pwd='Aa1!'+''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(12))
    mr('POST','/accounts',{'address':email,'password':mt_pwd})
    _,tb=mr('POST','/token',{'address':email,'password':mt_pwd})
    mt_tok=tb.get('token','')
    print(f'email={email}')

    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    stealth=Stealth()
    getcap_done=[False]
    audio_from_net=[None]

    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
        ctx=await browser.new_context(viewport={'width':1280,'height':900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',locale='en-US')
        page=await ctx.new_page()
        await stealth.apply_stealth_async(page)

        async def on_req(r):
            url=r.url
            try: body=r.post_data or ''
            except: body='<b>'
            if 'api.talordata' in url: print(f'  [API] {r.method} {url[-50:]}')
            elif 'getcaptcha' in url: print(f'  [GETCAP-REQ]')
            elif 'audio' in url.lower() and 'hcaptcha' in url:
                print(f'  [AUDIO-REQ] {url[:80]}')
                audio_from_net[0]=url
        async def on_resp(r):
            url=r.url
            if 'api.talordata' in url:
                try: d=await r.json(); print(f'  [RESP] {d.get("code")} {d.get("message")}')
                except: pass
            elif 'getcaptcha' in url: getcap_done[0]=True
        page.on('request',on_req); page.on('response',on_resp)

        await page.goto('https://dashboard.talordata.com/reg',wait_until='networkidle',timeout=30000)
        await asyncio.sleep(5)
        await page.fill('input[placeholder*="email" i]',email)
        await page.fill('input[type="password"]',nv_pwd)
        try: await page.fill('input[placeholder*="invitation" i]','z46vzbz4')
        except: pass
        cb=await page.query_selector('.el-checkbox__inner')
        if cb:
            box=await cb.bounding_box()
            if box: await page.mouse.click(box['x']+3,box['y']+3); await asyncio.sleep(0.4)
        signup=page.get_by_text('Sign Up').last
        box=await signup.bounding_box()
        if box: await page.mouse.click(box['x']+box['width']//2,box['y']+box['height']//2)
        print('clicked Sign Up')

        for i in range(20):
            await asyncio.sleep(1)
            if getcap_done[0]: print(f'[{i}s] challenge loaded'); break
        await asyncio.sleep(5)

        frames=[f for f in page.frames if 'hcaptcha' in f.url]
        frame_boxes=[]
        for i,frame in enumerate(frames):
            try:
                handle=await frame.frame_element()
                box=await handle.bounding_box()
                frame_boxes.append((frame,box))
                print(f'frame[{i}]: {box}')
                await handle.screenshot(path=f'/tmp/frame_{i}.png')
            except Exception as e:
                print(f'frame[{i}] err: {e}')
                frame_boxes.append((frame,None))

        # Identify challenge frame (larger one)
        challenge_frame=None; challenge_box=None
        for frame,box in frame_boxes:
            if box and box.get('width',0) > 300:
                challenge_frame=frame; challenge_box=box; break

        if not challenge_box:
            print('no challenge frame found')
            await page.screenshot(path='/tmp/debug.png')
            await browser.close(); return

        print(f'challenge box: {challenge_box}')

        # Click at various positions to find ≡ audio button
        # hcaptcha challenge: image grid fills ~70% height, controls at ~85-95% height
        # ≡ button is leftmost in controls row
        audio_url=None
        for ypct in [0.85, 0.87, 0.89, 0.91, 0.93, 0.95]:
            for xpct in [0.04, 0.07, 0.10, 0.13, 0.16]:
                if audio_url: break
                cx=challenge_box['x']+challenge_box['width']*xpct
                cy=challenge_box['y']+challenge_box['height']*ypct
                await page.mouse.click(cx,cy)
                await asyncio.sleep(0.4)
                audio_url=await get_audio_from_frame(page.frames)
                if audio_url: print(f'AUDIO at ({cx:.0f},{cy:.0f}) xpct={xpct} ypct={ypct}')

        await page.screenshot(path='/tmp/after_clicks.png')

        if not audio_url and audio_from_net[0]:
            audio_url=audio_from_net[0]
            print(f'Using network audio: {audio_url[:60]}')

        token=None
        if audio_url:
            import whisper
            print(f'Solving audio: {audio_url[:60]}')
            with tempfile.NamedTemporaryFile(suffix='.mp3',delete=False) as f: mp3=f.name
            if audio_url.startswith('blob:'):
                for frame in page.frames:
                    try:
                        data=await frame.evaluate('(u)=>fetch(u).then(r=>r.arrayBuffer()).then(b=>Array.from(new Uint8Array(b)))',audio_url)
                        if data: open(mp3,'wb').write(bytes(data)); break
                    except: pass
            else:
                resp=rq.get(audio_url,timeout=30)
                open(mp3,'wb').write(resp.content)
            wav=mp3.replace('.mp3','.wav')
            subprocess.run(['ffmpeg','-i',mp3,'-ar','16000','-ac','1',wav,'-y'],capture_output=True,timeout=30)
            model=whisper.load_model('base')
            result=model.transcribe(wav,language='en')
            text=re.sub(r'[^a-z0-9\s]','',result['text'].strip().lower()).strip()
            print(f'whisper: "{text}"')
            for frame in page.frames:
                if 'hcaptcha' not in frame.url: continue
                try:
                    inp=await frame.query_selector('input[type="text"]')
                    if inp:
                        await inp.fill(text)
                        btn=await frame.query_selector('button[type="submit"]')
                        if btn: await btn.click(); await asyncio.sleep(4)
                        break
                except: pass
            for _ in range(6):
                await asyncio.sleep(1)
                try:
                    tok=await page.evaluate('() => { const e=document.querySelector("[name=\'h-captcha-response\']"); if(e&&e.value.length>20) return e.value; if(typeof hcaptcha!="undefined"){const r=hcaptcha.getResponse(); if(r) return r;} return null; }')
                    if tok: token=tok; print(f'TOKEN: {tok[:40]}'); break
                except: pass
        
        if token:
            r=rq.post(f'{API}/users/v1/auth/send_activation',json={'email':email,'hcaptcha':token},headers=H,timeout=10)
            d=r.json(); print(f'send_activation: {d.get("code")} {d.get("message")}')
            if d.get('code')==0:
                email_code=None
                for i in range(18):
                    await asyncio.sleep(5)
                    _,msgs=mr('GET','/messages',token=mt_tok)
                    items=(msgs or {}).get('hydra:member',[])
                    if items:
                        for msg in items:
                            _,full=mr('GET',f'/messages/{msg["id"]}',token=mt_tok)
                            ft=str(full.get('text',''))+str(full.get('html',''))
                            codes=re.findall(r'\b(\d{6})\b',ft)
                            if codes: email_code=codes[0]; break
                        if email_code: break
                    print(f'  [{(i+1)*5}s] waiting...')
                if email_code:
                    r2=rq.post(f'{API}/users/v1/auth/register',json={'email':email,'password':nv_pwd,'code':email_code},headers=H,timeout=10)
                    d2=r2.json(); jwt=d2.get('data','') if isinstance(d2.get('data'),str) else (d2.get('data') or {}).get('token','')
                    if jwt:
                        r3=rq.get(f'{API}/users/v1/auth/activation',params={'token':jwt},headers={k:v for k,v in H.items() if k!='Content-Type'},timeout=10)
                        d3=r3.json(); print(f'activation: {d3.get("code")} {d3.get("message")}')
                        if d3.get('code')==0:
                            print(f'\nSUCCESS: {email} | {nv_pwd}')
                            import psycopg2
                            try:
                                conn=psycopg2.connect('postgresql://postgres:postgres@localhost/toolkit')
                                cur=conn.cursor()
                                cur.execute('CREATE TABLE IF NOT EXISTS accounts (id SERIAL PRIMARY KEY, platform TEXT, email TEXT, password TEXT, notes TEXT, created_at TIMESTAMP DEFAULT NOW())')
                                cur.execute('INSERT INTO accounts (platform,email,password,notes) VALUES (%s,%s,%s,%s)',('talordata',email,nv_pwd,'activated'))
                                conn.commit(); conn.close(); print('saved to DB')
                            except Exception as e: print(f'DB: {e}')
        else:
            print('[!] no token')
            await page.screenshot(path='/tmp/final_state.png')
        await browser.close()

asyncio.run(main())
