#!/usr/bin/env python3
"""
novproxy_register_worker.py v2 — 批量注册 + IP一致性（Outlook CF池隐蔽思路）
每个账号独立 Chrome 实例 + 可选代理（不同 IP 注册 → 防批量检测）

Args:
  --accounts  JSON: [["email","pwd"], ...]
  --proxy     单个代理 URL（socks5h:// 或 http://）
  --proxies   逗号分隔的多代理列表（每账号轮换）
  --delay     秒 (default 3)

Output:
  [LOG]    message
  [OK]     email|password|exit_ip
  [FAIL]   email|reason
  [DONE]   ok/total
"""
import asyncio, sys, time, re, json, base64, argparse
from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions
import ddddocr

CHROME = '/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome'
_ocr = ddddocr.DdddOcr(show_ad=False)

def log(msg):         print(f'[LOG]  {msg}', flush=True)
def ok(e, p, ip=''):  print(f'[OK]   {e}|{p}|{ip}', flush=True)
def fail(e, r):       print(f'[FAIL] {e}|{r}', flush=True)
def done(n, t):       print(f'[DONE] {n}/{t}', flush=True)

def _make_options(proxy_url: str = ''):
    o = ChromiumOptions()
    o.headless = True
    o.binary_location = CHROME
    o.add_argument('--no-sandbox')
    o.add_argument('--disable-dev-shm-usage')
    o.add_argument('--disable-gpu')
    o.add_argument('--window-size=1920,1080')
    o.start_timeout = 30
    o.webrtc_leak_protection = True
    if proxy_url:
        # 统一格式: socks5/http
        # Chrome 支持: --proxy-server=socks5://host:port 或 http://host:port
        proxy_str = proxy_url
        if proxy_str.startswith('socks5h://'):
            proxy_str = proxy_str.replace('socks5h://', 'socks5://')
        o.add_argument(f'--proxy-server={proxy_str}')
        o.add_argument('--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost')
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

async def _get_exit_ip(tab):
    """登录后获取实际出口 IP"""
    try:
        r = await tab.execute_script('''(function(){
            return fetch("https://api.ipify.org?format=json")
              .then(r=>r.json())
              .then(d=>d.ip||"")
              .catch(()=>"");
        })()''')
        # 这是 Promise，需要等待
        return ''
    except Exception:
        return ''

async def register_one(email: str, password: str, proxy_url: str = ''):
    """每个账号独立 Chrome + 独立代理 = 不同 IP"""
    async with Chrome(options=_make_options(proxy_url)) as browser:
        tab = await browser.start()
        if proxy_url:
            log(f'[{email}] 代理: {proxy_url[:40]}...')
        log(f'[{email}] 打开注册页...')
        try:
            async with tab.expect_and_bypass_cloudflare_captcha():
                await tab.go_to('https://novproxy.com/register/')
        except Exception as e:
            log(f'[{email}] CF 绕过: {str(e)[:50]}')
            await tab.go_to('https://novproxy.com/register/')
        await asyncio.sleep(2)

        await _fill(tab, 'mailbox', email)
        await _fill(tab, 'password', password)

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
                    log(f'[{email}] 验证码 #{attempt+1}: "{code}"')
                    break
            except Exception:
                pass
            await _refresh_captcha(tab)

        if not captcha_ok:
            return False, 'captcha_failed', ''

        # 验证 DOM
        chk = await tab.execute_script('''(function(){
            var m=document.getElementById("mailbox"),v=document.getElementById("verification");
            return (m?m.value:"")+"|"+(v?v.value:"");
        })()''')
        log(f'[{email}] DOM 验证: {chk.get("result",{}).get("result",{}).get("value","")}')

        await tab.execute_script('(function(){var b=document.querySelector("button.login_btn");if(b)b.click();})()')

        # 等待跳转
        for _ in range(15):
            await asyncio.sleep(1)
            url = await tab.current_url
            if '/register' not in url:
                # 尝试获取出口 IP（通过页面 JS fetch）
                return True, 'registered', ''

        html = await tab.page_source
        errs = re.findall(r'errorTips[^>]*>([^<]{3,80})', html)
        return False, errs[0] if errs else 'timeout_no_redirect', ''


async def main(accounts, proxy_list, delay):
    n_ok, total = 0, len(accounts)
    for i, (email, password) in enumerate(accounts):
        # 每账号轮换代理（如果提供了列表）
        proxy = ''
        if proxy_list:
            proxy = proxy_list[i % len(proxy_list)]
        log(f'--- [{i+1}/{total}] 注册 {email}{" via proxy" if proxy else ""} ---')
        try:
            success, info, exit_ip = await register_one(email, password, proxy)
            if success:
                ok(email, password, exit_ip or proxy.split('@')[-1].split(':')[0] if proxy else '')
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
    ap.add_argument('--proxy', default='', help='单个代理 URL')
    ap.add_argument('--proxies', default='', help='逗号分隔多代理列表')
    ap.add_argument('--delay', type=float, default=3.0)
    args = ap.parse_args()

    proxy_list = []
    if args.proxies:
        proxy_list = [p.strip() for p in args.proxies.split(',') if p.strip()]
    elif args.proxy:
        proxy_list = [args.proxy]

    asyncio.run(main(json.loads(args.accounts), proxy_list, args.delay))
