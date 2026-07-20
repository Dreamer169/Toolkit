#!/usr/bin/env python3
"""
talordata v7: 拦截getcaptcha响应 + 获取音频URL + whisper解码
"""
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
    _,tb=mr('POST','/token',{'address':email,'password':mt_tok_var:=''})
    mt_tok=tb.get('token','')
    print(f'email={email}')

    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    stealth=Stealth()

    # Store intercepted data
    getcaptcha_resp=[None]
    checkcaptcha_key=[None]

    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
        ctx=await browser.new_context(viewport={'width':1280,'height':900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',locale='en-US')
        page=await ctx.new_page()
        await stealth.apply_stealth_async(page)

        async def on_resp(resp):
            url=resp.url
            if 'api.talordata' in url:
                try: d=await resp.json(); print(f'  [API-RESP] code={d.get("code")} {d.get("message")}')
                except: pass
            elif 'getcaptcha' in url:
                try:
                    t=await resp.text()
                    print(f'  [GETCAPTCHA] {resp.status} {len(t)} bytes')
                    print(f'    preview: {t[:500]}')
                    getcaptcha_resp[0]=t
                    # Try to parse
                    d=json.loads(t)
                    # Look for audio
                    if 'audio' in t.lower() or 'tasklist' in t.lower():
                        print(f'  [GETCAPTCHA] has audio/tasklist!')
                    print(f'  [GETCAPTCHA] keys: {list(d.keys()) if isinstance(d,dict) else type(d)}')
                except Exception as e:
                    print(f'  [GETCAPTCHA] parse error: {e}')
            elif 'checkcaptcha' in url or 'checkCaptcha' in url:
                try: t=await resp.text(); print(f'  [CHECK] {t[:200]}')
                except: pass
        async def on_req(req):
            url=req.url
            try: body=req.post_data or ''
            except: body='<bin>'
            if 'api.talordata' in url: print(f'  [API] {req.method} {url[-50:]}')
            elif 'getcaptcha' in url:
                print(f'  [GETCAPTCHA-REQ] {req.method} {url[:80]} body={body[:100]}')
                checkcaptcha_key[0]=url.split('/')[-1]  # the key from URL
            elif 'checkcaptcha' in url.lower():
                print(f'  [CHECK-REQ] {req.method} {url[:80]} body={body[:100]}')
        page.on('request',on_req)
        page.on('response',on_resp)

        await page.goto('https://dashboard.talordata.com/reg',wait_until='networkidle',timeout=30000)
        await asyncio.sleep(5)

        await page.fill('input[placeholder*="email" i]',email)
        await asyncio.sleep(0.3)
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

        # Wait for getcaptcha to be called
        for i in range(25):
            await asyncio.sleep(1)
            if getcaptcha_resp[0]: print(f'[{i}s] getcaptcha received'); break
            if i%5==0: print(f'[{i}s] waiting getcaptcha...')

        if getcaptcha_resp[0]:
            print(f'\n=== FULL GETCAPTCHA RESPONSE ===')
            print(getcaptcha_resp[0][:2000])
            
            # Parse challenge
            try:
                data=json.loads(getcaptcha_resp[0])
                print(f'\nKeys: {list(data.keys())}')
                
                # Look for audio in challenge
                if 'tasklist' in data:
                    tasks=data['tasklist']
                    print(f'tasklist has {len(tasks)} tasks')
                    if tasks:
                        print(f'First task keys: {list(tasks[0].keys()) if isinstance(tasks[0],dict) else tasks[0]}')
                
                # Look for audio URL directly
                resp_str=json.dumps(data)
                audio_matches=re.findall(r'https?://[^\s"\'<>]+(?:mp3|wav|audio)[^\s"\'<>]*',resp_str,re.I)
                print(f'Audio URLs in response: {audio_matches}')
                
                # Look for key/token to use for next call
                key=data.get('key','')
                print(f'key: {key[:40] if key else None}')
                
            except Exception as e:
                print(f'parse error: {e}')

        await browser.close()

asyncio.run(main())
