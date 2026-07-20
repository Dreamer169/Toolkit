#!/usr/bin/env python3
"""
v9b: 知道challenge在frame[0] (x=546,y=163.9,w=520,h=570)
底部音频按钮约在: 等三线=576,738 | 蓝绿圆=806,740
"""
import asyncio,json,re,secrets,string,subprocess,tempfile
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

    hcap_reqs=[]
    hcap_audio_url=[None]

    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
        ctx=await browser.new_context(viewport={'width':1280,'height':900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',locale='en-US')
        page=await ctx.new_page()
        await stealth.apply_stealth_async(page)

        async def on_req(req):
            url=req.url
            try: body=req.post_data or ''
            except: body='<bin>'
            if 'api.talordata' in url: print(f'  [API] {req.method} {url[-50:]}')
            elif 'hcaptcha' in url and '.js' not in url and 'logo' not in url and '.css' not in url:
                print(f'  [HC] {req.method} {url[:80]}')
                hcap_reqs.append(url)
        async def on_resp(resp):
            url=resp.url
            if 'api.talordata' in url:
                try: d=await resp.json(); print(f'  [RESP] {d.get("code")} {d.get("message")}')
                except: pass
            elif 'audio' in url.lower() or url.endswith('.mp3') or url.endswith('.wav'):
                print(f'  [AUDIO-URL] {url[:80]}')
                hcap_audio_url[0]=url
        page.on('request',on_req)
        page.on('response',on_resp)

        await page.goto('https://dashboard.talordata.com/reg',wait_until='networkidle',timeout=30000)
        await asyncio.sleep(5)
        await page.fill('input[placeholder*="email" i]',email)
        await page.fill('input[type="password"]',nv_pwd)
        try: await page.fill('input[placeholder*="invitation" i]','z46vzbz4')
        except: pass
        await asyncio.sleep(0.2)
        cb=await page.query_selector('.el-checkbox__inner')
        if cb:
            box=await cb.bounding_box()
            if box: await page.mouse.click(box['x']+3,box['y']+3); await asyncio.sleep(0.4)

        # Click Sign Up
        signup=page.get_by_text('Sign Up').last
        box=await signup.bounding_box()
        if box: await page.mouse.click(box['x']+box['width']//2,box['y']+box['height']//2)
        print('clicked Sign Up')

        # Wait for hcaptcha frames
        for i in range(15):
            await asyncio.sleep(1)
            frames=[f for f in page.frames if 'hcaptcha' in f.url]
            if frames: print(f'[{i}s] {len(frames)} hcap frames'); break

        await asyncio.sleep(3)

        # Take screenshot to verify state
        await page.screenshot(path='/tmp/before_audio.png')

        # STEP: Click the audio/accessibility button
        # The ≡ button (3 horizontal lines) - hcaptcha accessibility menu 
        # Position based on screenshot analysis: ~x=460, y=560 in 1024px wide img
        # In 1280 viewport: 460/1024*1280=575, 560/684*900=737
        
        # Try the three-lines button (accessibility menu)
        print('[*] clicking ≡ accessibility button at (577, 738)')
        await page.mouse.move(577, 738, steps=5)
        await asyncio.sleep(0.3)
        await page.mouse.click(577, 738)
        await asyncio.sleep(2)
        await page.screenshot(path='/tmp/after_threelines.png')
        print('screenshot: after_threelines.png')

        # Check what happened - look for audio menu or audio challenge
        frames=[f for f in page.frames if 'hcaptcha' in f.url]
        for i,frame in enumerate(frames):
            try:
                elms=await frame.evaluate("""() => ({
                    btns: Array.from(document.querySelectorAll('button,a,[role=menuitem],[role=option]')).map(b=>({
                        text:b.textContent?.trim()?.substring(0,30), aria:b.getAttribute('aria-label'),
                        visible:b.offsetParent!==null, class:b.className?.substring(0,40)
                    })),
                    audios: Array.from(document.querySelectorAll('audio')).map(a=>a.src),
                    inputs: Array.from(document.querySelectorAll('input')).map(i=>({type:i.type,pl:i.placeholder})),
                })""")
                vis_btns=[b for b in elms['btns'] if b.get('visible')]
                print(f'  frame[{i}] visible btns: {vis_btns[:10]}')
                print(f'  frame[{i}] audios: {elms["audios"]}')
            except Exception as e: print(f'  frame[{i}] err: {e}')

        # Try the teal audio icon
        print('[*] clicking teal audio icon at (806, 740)')
        await page.mouse.move(806, 740, steps=5)
        await asyncio.sleep(0.3)
        await page.mouse.click(806, 740)
        await asyncio.sleep(2)
        await page.screenshot(path='/tmp/after_teal_icon.png')

        # Check again for audio
        frames=[f for f in page.frames if 'hcaptcha' in f.url]
        for i,frame in enumerate(frames):
            try:
                audios=await frame.evaluate("() => Array.from(document.querySelectorAll('audio')).map(a=>({src:a.src,currentSrc:a.currentSrc}))")
                if audios: print(f'  frame[{i}] AUDIO: {audios}')
                # Also check for menu items
                elms=await frame.evaluate("""() => 
                    Array.from(document.querySelectorAll('[role=menuitem],[role=option],li')).map(e=>({
                        text:e.textContent?.trim()?.substring(0,30),
                        visible:e.offsetParent!==null
                    })).filter(e=>e.visible)""")
                if elms: print(f'  frame[{i}] menu: {elms[:5]}')
            except Exception as e: print(f'  frame[{i}] err: {e}')
        
        # If audio appeared, transcribe and solve
        audio_url=hcap_audio_url[0]
        for frame in frames:
            try:
                u=await frame.evaluate("()=>{const a=document.querySelector('audio');return a?(a.src||a.currentSrc):null;}")
                if u: audio_url=u; print(f'AUDIO FOUND: {u[:70]}'); break
            except: pass

        token=None
        if audio_url:
            import whisper
            with tempfile.NamedTemporaryFile(suffix='.mp3',delete=False) as f: mp3=f.name
            if audio_url.startswith('blob:'):
                for frame in frames:
                    try:
                        data=await frame.evaluate('(u)=>fetch(u).then(r=>r.arrayBuffer()).then(b=>Array.from(new Uint8Array(b)))',audio_url)
                        if data: open(mp3,'wb').write(bytes(data)); break
                    except: pass
            else:
                open(mp3,'wb').write(rq.get(audio_url,timeout=30).content)
            wav=mp3.replace('.mp3','.wav')
            subprocess.run(['ffmpeg','-i',mp3,'-ar','16000','-ac','1',wav,'-y'],capture_output=True,timeout=30)
            model=whisper.load_model('base')
            result=model.transcribe(wav,language='en')
            text=re.sub(r'[^a-z0-9\s]','',result['text'].strip().lower()).strip()
            print(f'whisper: "{text}"')
            for frame in frames:
                try:
                    inp=await frame.query_selector('input[type="text"]')
                    if inp:
                        await inp.fill(text)
                        btn=await frame.query_selector('button[type="submit"],#submit-btn')
                        if btn: await btn.click(); await asyncio.sleep(4)
                        break
                except: pass
            for _ in range(6):
                await asyncio.sleep(1)
                try:
                    tok=await page.evaluate('()=>{const e=document.querySelector(\'[name="h-captcha-response"]\'); if(e&&e.value.length>20) return e.value; if(typeof hcaptcha!="undefined"){const r=hcaptcha.getResponse(); if(r) return r;} return null;}')
                    if tok: token=tok; print(f'TOKEN: {tok[:40]}'); break
                except: pass

        if token:
            r=rq.post(f'{API}/users/v1/auth/send_activation',json={'email':email,'hcaptcha':token},headers=H,timeout=10)
            d=r.json()
            print(f'send_activation: {d.get("code")} {d.get("message")}')
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
                    print(f'  [{(i+1)*5}s] waiting email...')
                if email_code:
                    r2=rq.post(f'{API}/users/v1/auth/register',json={'email':email,'password':nv_pwd,'code':email_code},headers=H,timeout=10)
                    d2=r2.json()
                    jwt=d2.get('data','') if isinstance(d2.get('data'),str) else (d2.get('data') or {}).get('token','')
                    if jwt:
                        r3=rq.get(f'{API}/users/v1/auth/activation',params={'token':jwt},headers={k:v for k,v in H.items() if k!='Content-Type'},timeout=10)
                        d3=r3.json()
                        print(f'activation: {d3.get("code")} {d3.get("message")}')
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
            print('[!] no token - checking what happened')
            await page.screenshot(path='/tmp/final_state.png')
            print('screenshot: final_state.png')

        await browser.close()

asyncio.run(main())
