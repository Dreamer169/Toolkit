import asyncio,json,re,secrets,string,subprocess,tempfile
import requests as rq
import urllib.request,urllib.error

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

    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
        ctx=await browser.new_context(viewport={'width':1280,'height':900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',locale='en-US')
        page=await ctx.new_page()
        await stealth.apply_stealth_async(page)

        async def on_req(req):
            try: body=req.post_data or ''
            except: body='<bin>'
            if 'api.talordata' in req.url: print(f'  [API] {req.method} {req.url[-50:]}')
            elif 'hcaptcha' in req.url and '.js' not in req.url and '.css' not in req.url: print(f'  [HC] {req.method} {req.url[:80]}')
        async def on_resp(resp):
            if 'api.talordata' in resp.url:
                try: d=await resp.json(); print(f'  [RESP] code={d.get("code")} {d.get("message")}')
                except: pass
        page.on('request',on_req)
        page.on('response',on_resp)

        await page.goto('https://dashboard.talordata.com/reg',wait_until='networkidle',timeout=30000)
        await asyncio.sleep(5)

        await page.fill('input[placeholder*="email" i]',email)
        await asyncio.sleep(0.4)
        await page.fill('input[type="password"]',nv_pwd)
        try: await page.fill('input[placeholder*="invitation" i]','z46vzbz4')
        except: pass
        await asyncio.sleep(0.3)
        cb=await page.query_selector('.el-checkbox__inner')
        if cb:
            box=await cb.bounding_box()
            if box: await page.mouse.click(box['x']+3,box['y']+3); await asyncio.sleep(0.5)

        # Click Sign Up (text selector - confirmed working)
        signup=page.get_by_text('Sign Up').last
        box=await signup.bounding_box()
        if box: await page.mouse.click(box['x']+box['width']//2,box['y']+box['height']//2); print('clicked Sign Up')

        # Wait for hcaptcha frames
        for i in range(15):
            await asyncio.sleep(1)
            frames=[f for f in page.frames if 'hcaptcha' in f.url]
            if frames: print(f'[{i}s] {len(frames)} hcap frames'); break

        frames=[f for f in page.frames if 'hcaptcha' in f.url]
        print(f'Total hcap frames: {len(frames)}')

        # Click "About hCaptcha & Accessibility Options" to get audio mode
        token=None
        for frame in frames:
            try:
                state=await frame.evaluate("""() => ({
                    btns: Array.from(document.querySelectorAll('button,a')).map(b=>({
                        text: b.textContent?.trim()?.substring(0,40),
                        aria: b.getAttribute('aria-label'),
                        href: b.href?.substring(0,60),
                        class: b.className?.substring(0,40)
                    })),
                    inputs: Array.from(document.querySelectorAll('input')).map(i=>({type:i.type,pl:i.placeholder})),
                })""")
                print(f'  frame btns: {json.dumps(state["btns"])[:300]}')
                print(f'  frame inputs: {state["inputs"]}')

                # Click accessibility/audio button
                for btn in state['btns']:
                    aria=str(btn.get('aria','')).lower()
                    text=str(btn.get('text','')).lower()
                    if any(kw in aria+text for kw in ['access','audio','about']):
                        print(f'  clicking: {btn}')
                        await frame.evaluate(f"""() => {{
                            const btns=document.querySelectorAll('button,a');
                            for(const b of btns) {{
                                const t=(b.textContent||'').toLowerCase()+(b.getAttribute('aria-label')||'').toLowerCase();
                                if(t.includes('access')||t.includes('audio')||t.includes('about')) {{ b.click(); break; }}
                            }}
                        }}""")
                        await asyncio.sleep(3)
                        break
            except Exception as e: print(f'  frame err: {e}')

        # Check if new accessibility frame appeared or audio link
        await asyncio.sleep(3)
        all_frames=[f for f in page.frames if 'hcaptcha' in f.url]
        print(f'frames after accessibility click: {len(all_frames)}')

        audio_url=None
        for frame in all_frames:
            try:
                u=await frame.evaluate("() => { const a=document.querySelector('audio'); return a?(a.src||a.currentSrc):null; }")
                if u: audio_url=u; print(f'  audio: {u[:70]}'); break
                # Check all links
                links=await frame.evaluate("() => Array.from(document.querySelectorAll('a')).map(a=>a.href).filter(h=>h.includes('mp3')||h.includes('audio'))")
                if links: audio_url=links[0]; print(f'  audio link: {links[0][:70]}'); break
            except: pass

        if audio_url:
            print(f'downloading audio: {audio_url[:70]}')
            import whisper
            with tempfile.NamedTemporaryFile(suffix='.mp3',delete=False) as f: mp3=f.name
            open(mp3,'wb').write(rq.get(audio_url,timeout=30).content)
            wav=mp3.replace('.mp3','.wav')
            subprocess.run(['ffmpeg','-i',mp3,'-ar','16000','-ac','1',wav,'-y'],capture_output=True,timeout=30)
            model=whisper.load_model('base')
            result=model.transcribe(wav,language='en')
            text=re.sub(r'[^a-z0-9\s]','',result['text'].strip().lower()).strip()
            print(f'whisper: "{text}"')
            # Submit
            for frame in all_frames:
                try:
                    inp=await frame.query_selector('input[type="text"]')
                    if inp:
                        await inp.fill(text)
                        btn=await frame.query_selector('button[type="submit"],#submit-btn')
                        if btn: await btn.click(); await asyncio.sleep(4)
                        break
                except: pass
            # Get token
            for _ in range(6):
                await asyncio.sleep(1)
                try:
                    tok=await page.evaluate("""()=>{const e=document.querySelector('[name="h-captcha-response"]'); if(e&&e.value.length>20) return e.value; if(typeof hcaptcha!='undefined'){const r=hcaptcha.getResponse(); if(r) return r;} return null;}""")
                    if tok: token=tok; print(f'TOKEN: {tok[:40]}'); break
                except: pass

        if not token:
            print('no token - dumping page state')
            # Dump ALL frames' full state
            for i,frame in enumerate(all_frames):
                try:
                    content=await frame.content()
                    # Find all interactive elements
                    elms=await frame.evaluate("""() => ({
                        allBtns: Array.from(document.querySelectorAll('button')).map(b=>({
                            text:b.textContent?.trim()?.substring(0,30), aria:b.getAttribute('aria-label'),
                            visible:b.offsetParent!==null, class:b.className?.substring(0,50)
                        })),
                        allInputs: Array.from(document.querySelectorAll('input')).map(i=>({type:i.type,pl:i.placeholder})),
                        allAudio: Array.from(document.querySelectorAll('audio')).map(a=>a.src),
                        allIframes: Array.from(document.querySelectorAll('iframe')).map(f=>f.src?.substring(0,50)),
                        hcapResp: document.querySelector('[name="h-captcha-response"]')?.value?.substring(0,20),
                    })""")
                    print(f'  Frame[{i}]: {json.dumps(elms)[:400]}')
                except Exception as e: print(f'  Frame[{i}] err: {e}')

        if token:
            r=rq.post(f'{API}/users/v1/auth/send_activation',json={'email':email,'hcaptcha':token},headers=H,timeout=10)
            d=r.json()
            print(f'send_activation: code={d.get("code")} {d.get("message")}')
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
                    print(f'code: {email_code}')
                    r2=rq.post(f'{API}/users/v1/auth/register',json={'email':email,'password':nv_pwd,'code':email_code},headers=H,timeout=10)
                    d2=r2.json()
                    jwt=d2.get('data','') if isinstance(d2.get('data'),str) else (d2.get('data') or {}).get('token','')
                    print(f'register: code={d2.get("code")} {d2.get("message")}')
                    if jwt:
                        r3=rq.get(f'{API}/users/v1/auth/activation',params={'token':jwt},headers={k:v for k,v in H.items() if k!='Content-Type'},timeout=10)
                        d3=r3.json()
                        print(f'activation: code={d3.get("code")} {d3.get("message")}')
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

        await browser.close()

asyncio.run(main())
