#!/usr/bin/env python3
"""
novproxy_register_worker.py — 批量注册 novproxy.com 账号
Args:
  --accounts  JSON: [["email","pwd"], ...]
  --delay     秒 (default 3)
Output (每行 flush):
  [LOG]  message
  [OK]   email|password
  [FAIL] email|reason
  [DONE] ok/total
"""
import asyncio, sys, time, re, json, base64, argparse
from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions
from pydoll.protocol.fetch.events import FetchEvent, RequestPausedEvent
import ddddocr

CHROME = '/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome'
_ocr = ddddocr.DdddOcr(show_ad=False)

def log(msg):  print(f'[LOG]  {msg}', flush=True)
def ok(e, p):  print(f'[OK]   {e}|{p}', flush=True)
def fail(e,r): print(f'[FAIL] {e}|{r}', flush=True)
def done(n,t): print(f'[DONE] {n}/{t}', flush=True)

def _make_options():
    o = ChromiumOptions()
    o.headless = True
    o.binary_location = CHROME
    o.add_argument('--no-sandbox')
    o.add_argument('--disable-dev-shm-usage')
    o.add_argument('--disable-gpu')
    o.add_argument('--window-size=1920,1080')
    o.start_timeout = 30
    o.browser_preferences = {
        'profile': {'last_engagement_time': int(time.time())-7*86400,
                    'exit_type': 'Normal', 'exited_cleanly': True},
        'intl': {'accept_languages': 'en-US,en'}
    }
    o.webrtc_leak_protection = True
    return o

async def _get_captcha(tab):
    r = await tab.execute_script('''(function(){
        var imgs=document.querySelectorAll("img.code_img");
        if(imgs.length>0) return imgs[0].src;
        var all=document.querySelectorAll("img[src^='data:image']");
        return all.length>0?all[0].src:"";
    })()''')
    src = r.get('result', {}).get('result', {}).get('value', '')
    for pfx in ['data:image/png;base64,', 'data:image/jpeg;base64,']:
        if src.startswith(pfx):
            return base64.b64decode(src[len(pfx):])
    return b''

async def _refresh_captcha(tab):
    await tab.execute_script('''(function(){
        if(typeof check_verificat==="function"){check_verificat();return;}
        var b=document.querySelector("img[onclick*=verificat]");if(b)b.click();
    })()''')
    await asyncio.sleep(1.0)

async def _fill(tab, eid, value):
    el = await tab.find(id=eid, raise_exc=False)
    if not el: return False
    await el.click(); await asyncio.sleep(0.2)
    await el.clear(); await asyncio.sleep(0.1)
    await el.type_text(value, humanize=False); await asyncio.sleep(0.3)
    return True

async def register_one(email, password):
    """每个账号使用独立 Chrome 实例（干净 session）"""
    async with Chrome(options=_make_options()) as browser:
        tab = await browser.start()
        log(f'[{email}] 打开注册页...')
        try:
            async with tab.expect_and_bypass_cloudflare_captcha():
                await tab.go_to('https://novproxy.com/register/')
        except Exception as e:
            log(f'[{email}] CF: {str(e)[:60]}')
            await tab.go_to('https://novproxy.com/register/')
        await asyncio.sleep(2)

        await _fill(tab, 'mailbox', email)
        await _fill(tab, 'password', password)

        # 识别验证码（最多5次）
        captcha_ok = False
        for attempt in range(5):
            img = await _get_captcha(tab)
            if not img:
                await _refresh_captcha(tab); continue
            try:
                code = _ocr.classification(img).strip()
                if len(code) >= 3:
                    await _fill(tab, 'verification', code)
                    captcha_ok = True
                    log(f'[{email}] 验证码: "{code}"')
                    break
            except Exception:
                pass
            await _refresh_captcha(tab)

        if not captcha_ok:
            return False, 'captcha_failed'

        # 验证 DOM 值（确保填写成功）
        chk = await tab.execute_script('''(function(){
            var m=document.getElementById("mailbox"),p=document.getElementById("password"),v=document.getElementById("verification");
            return JSON.stringify({m:m?m.value:"",pl:(p?p.value:"").length,v:v?v.value:""});
        })()''')
        log(f'[{email}] DOM: {chk.get("result",{}).get("result",{}).get("value","")}')

        # 点击注册
        await tab.execute_script('''(function(){
            var b=document.querySelector("button.login_btn"); if(b)b.click();
        })()''')

        # 等待跳转（最长 15s）
        for _ in range(15):
            await asyncio.sleep(1)
            url = await tab.current_url
            if '/register' not in url:
                return True, 'registered'

        html = await tab.page_source
        errs = re.findall(r'errorTips[^>]*>([^<]{3,80})', html)
        return False, errs[0] if errs else 'timeout_no_redirect'

async def main(accounts, delay):
    n_ok, total = 0, len(accounts)
    for i, (email, password) in enumerate(accounts):
        log(f'--- [{i+1}/{total}] 注册 {email} ---')
        try:
            success, info = await register_one(email, password)
            if success:
                ok(email, password)
                n_ok += 1
            else:
                fail(email, info)
        except Exception as e:
            fail(email, str(e)[:120])
        if i < total - 1:
            await asyncio.sleep(delay)
    done(n_ok, total)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--accounts', required=True)
    ap.add_argument('--delay', type=float, default=3.0)
    args = ap.parse_args()
    asyncio.run(main(json.loads(args.accounts), args.delay))
