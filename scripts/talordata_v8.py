#!/usr/bin/env python3
"""
talordata v8: 设置hcaptcha accessibility cookie -> 强制音频挑战 -> 音频bypass
"""
import asyncio,json,re,secrets,string,subprocess,tempfile
import requests as rq, urllib.request, urllib.error

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
    hcap_urls=[]
    token_found=[None]
    getcap_binary=[None]

    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
        ctx=await browser.new_context(viewport={'width':1280,'height':900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',locale='en-US')
        
        # Set hcaptcha accessibility cookies to force audio mode
        await ctx.add_cookies([
            {'name':'hc_accessibility','value':'1','domain':'.hcaptcha.com','path':'/'},
            {'name':'hc_accessibility','value':'1','domain':'newassets.hcaptcha.com','path':'/'},
            {'name':'hcaptcha_accessibility','value':'audio','domain':'.hcaptcha.com','path':'/'},
        ])
        
        page=await ctx.new_page()
        await stealth.apply_stealth_async(page)
        
        async def on_resp(resp):
            url=resp.url
            if 'api.talordata' in url:
                try: d=await resp.json(); print(f'  [API] {d.get("code")} {d.get("message")}')
                except: pass
            elif 'getcaptcha' in url:
                try:
                    b=await resp.body()
                    getcap_binary[0]=b
                    print(f'  [GETCAP] {resp.status} {len(b)}B (binary={not b.isascii() if b else "empty"})')
                    # Try to parse as JSON anyway
                    try:
                        t=b.decode('utf-8')
                        d=json.loads(t)
                        print(f'  [GETCAP JSON] keys={list(d.keys())}')
                        if 'audio' in t.lower() or 'listen' in t.lower():
                            print('  [GETCAP] HAS AUDIO!')
                        hcap_urls.append(('getcap_key',d.get('key','')))
                    except: print(f'  [GETCAP] binary/encrypted (not JSON)')
                except Exception as e: print(f'  [GETCAP] err: {e}')
            elif 'checkcaptcha' in url.lower():
                try:
                    t=await resp.text()
                    print(f'  [CHECK] {resp.status}: {t[:200]}')
                    # If successful, extract token
                    try:
                        d=json.loads(t)
                        tok=d.get('generated_pass_UUID','') or d.get('pass_uuid','')
                        if tok: token_found[0]=tok; print(f'  [CHECK TOKEN] {tok[:40]}')
                    except: pass
                except: pass
        async def on_req(req):
            url=req.url
            try: body=req.post_data or ''
            except: body='<bin>'
            if 'api.talordata' in url: print(f'  [API-REQ] {req.method} {url[-50:]}')
            elif 'getcaptcha' in url: print(f'  [GETCAP-REQ]')
            elif 'checkcaptcha' in url.lower(): print(f'  [CHECK-REQ] {body[:100]}')
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
        signup=page.get_by_text('Sign Up').last
        box=await signup.bounding_box()
        if box: await page.mouse.click(box['x']+box['width']//2,box['y']+box['height']//2)
        print('clicked Sign Up')

        # Wait for getcaptcha
        for i in range(20):
            await asyncio.sleep(1)
            if getcap_binary[0]: print(f'[{i}s] getcaptcha received'); break
            if i%5==0: print(f'[{i}s] waiting...')

        # Check hcaptcha frames for audio
        await asyncio.sleep(3)
        frames=[f for f in page.frames if 'hcaptcha' in f.url]
        print(f'hcap frames: {len(frames)}')

        audio_url=None
        for frame in frames:
            try:
                # Check with deep inspection
                result=await frame.evaluate("""() => {
                    const shadow_search = (root) => {
                        let results = [];
                        if (!root) return results;
                        // Check for audio
                        const audios = root.querySelectorAll ? root.querySelectorAll('audio') : [];
                        for(const a of audios) results.push({type:'audio', src:a.src||a.currentSrc});
                        // Check for inputs
                        const inputs = root.querySelectorAll ? root.querySelectorAll('input[type="text"]') : [];
                        for(const i of inputs) results.push({type:'input', pl:i.placeholder});
                        // Check buttons
                        const btns = root.querySelectorAll ? root.querySelectorAll('button') : [];
                        for(const b of btns) results.push({
                            type:'btn', text:b.textContent?.trim()?.substring(0,20),
                            aria:b.getAttribute('aria-label'), visible:b.offsetParent!==null
                        });
                        // Check shadow roots
                        const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                        for(const el of all) {
                            if(el.shadowRoot) results = results.concat(shadow_search(el.shadowRoot));
                        }
                        return results;
                    };
                    return shadow_search(document);
                }""")
                print(f'  frame deep: {json.dumps(result)[:400]}')
                audio_els=[r for r in result if r.get('type')=='audio' and r.get('src')]
                if audio_els: audio_url=audio_els[0]['src']; break
            except Exception as e: print(f'  frame err: {e}')

        if audio_url:
            print(f'AUDIO FOUND: {audio_url[:80]}')
            import whisper
            with tempfile.NamedTemporaryFile(suffix='.mp3',delete=False) as f: mp3=f.name
            if audio_url.startswith('blob:'):
                for frame in frames:
                    try:
                        data=await frame.evaluate('(url)=>fetch(url).then(r=>r.arrayBuffer()).then(b=>Array.from(new Uint8Array(b)))',audio_url)
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
                        btn=await frame.query_selector('button[type="submit"]')
                        if btn: await btn.click(); await asyncio.sleep(4)
                        break
                except: pass
            for _ in range(6):
                await asyncio.sleep(1)
                try:
                    tok=await page.evaluate('()=>{const e=document.querySelector(\'[name="h-captcha-response"]\'); if(e&&e.value.length>20) return e.value; if(typeof hcaptcha!="undefined"){const r=hcaptcha.getResponse(); if(r) return r;} return null;}')
                    if tok: token_found[0]=tok; print(f'TOKEN: {tok[:40]}'); break
                except: pass

        if token_found[0]:
            r=rq.post(f'{API}/users/v1/auth/send_activation',json={'email':email,'hcaptcha':token_found[0]},headers=H,timeout=10)
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
                    print(f'  [{(i+1)*5}s] waiting...')
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

        await browser.close()

asyncio.run(main())
