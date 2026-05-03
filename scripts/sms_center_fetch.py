#!/usr/bin/env python3
"""sms_center_fetch.py -- jiemahao.com SMS Center fetcher"""
import asyncio, sys, re, json, argparse
import urllib.request, urllib.error

COUNTRIES = {
    'us':('美国','US'), 'gb':('英国','GB'), 'ca':('加拿大','CA'),
    'de':('德国','DE'), 'th':('泰国','TH'), 'my':('马来西亚','MY'), 'ph':('菲律宾','PH'),
}
HEADERS = {
    'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language':'zh-CN,zh;q=0.9,en;q=0.8',
}
CHROME = '/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome'


def fetch_url(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=15) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception:
        return ''


def fetch_numbers(country='all'):
    results = []
    countries = list(COUNTRIES.keys()) if country == 'all' else [country]
    for c in countries:
        if c not in COUNTRIES: continue
        html = fetch_url(f'https://jiemahao.com/{c}/')
        if not html: continue
        matches = re.findall(r'sms/\?phone=(\d+)"[^>]*>([+\d\s()]+)<', html)
        if not matches:
            matches = re.findall(r'sms/\?phone=(\d+)[^>]*>([^<]{5,30})<', html)
        seen = set()
        for pid, pnum in matches:
            pnum = pnum.strip()
            if not pnum or not re.search(r'\d{7,}', pnum) or pid in seen: continue
            seen.add(pid)
            results.append({'id':int(pid), 'number':pnum, 'country':c, 'countryName':COUNTRIES[c][0], 'countryCode':COUNTRIES[c][1]})
    return results


def make_opts():
    from pydoll.browser.options import ChromiumOptions
    o = ChromiumOptions()
    o.headless = True
    o.binary_location = CHROME
    o.add_argument('--no-sandbox')
    o.add_argument('--disable-dev-shm-usage')
    o.add_argument('--disable-gpu')
    o.add_argument('--window-size=1920,1080')
    o.add_argument('--disable-blink-features=AutomationControlled')
    o.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    o.start_timeout = 30
    return o


JS_HIDE_WD   = 'Object.defineProperty(navigator,"webdriver",{get:()=>undefined})'
JS_GET_MSGS  = '(function(){var items=document.querySelectorAll(".direct-chat-msg");var out=[];items.forEach(function(item){var info=item.querySelector(".direct-chat-info");var text=item.querySelector(".direct-chat-text");if(text)out.push({info:info?info.textContent.trim():"",body:text.textContent.trim()});});return JSON.stringify(out);})()'
JS_CLICK_BTN = '(function(){var b=document.querySelector("button[name=submit]")||document.querySelector(".sms-submit")||document.querySelector("#submit");if(b){b.click();return "clicked";}return "no-btn";})()'
JS_MSG_CNT   = 'return document.querySelectorAll(".direct-chat-msg").length'
JS_TITLE     = '(function(){var h=document.querySelector("h1.article-title,h1");return h?h.textContent.trim():""})()'


async def _get(tab, js):
    r = await tab.execute_script(js)
    return r.get('result',{}).get('result',{}).get('value','')


async def fetch_messages_pydoll(phone_id: int):
    try:
        from pydoll.browser import Chrome
    except ImportError:
        return {'error':'pydoll_not_installed','messages':[],'phoneId':phone_id}

    url = f'https://jiemahao.com/sms/?phone={phone_id}'

    async def try_get_messages(tab):
        # Click submit button (Turnstile should be auto-solved by bypass)
        await _get(tab, JS_CLICK_BTN)
        # Wait up to 30s for messages
        for _ in range(20):
            await asyncio.sleep(1.5)
            cnt_r = await tab.execute_script(JS_MSG_CNT)
            cnt = cnt_r.get('result',{}).get('result',{}).get('value',0) or 0
            if cnt > 0:
                return cnt
        return 0

    try:
        async with Chrome(options=make_opts()) as browser:
            tab = await browser.start()
            await tab.execute_script(JS_HIDE_WD)

            # Approach 1: single bypass on direct SMS URL
            try:
                async with tab.expect_and_bypass_cloudflare_captcha(time_to_wait_captcha=40):
                    await tab.go_to(url)
            except Exception:
                await tab.go_to(url)

            await asyncio.sleep(4)

            phone_number = await _get(tab, JS_TITLE)

            # Wait for any Turnstile to settle, then submit
            await asyncio.sleep(3)
            cnt = await try_get_messages(tab)

            # Approach 2: if no messages, try running bypass_cloudflare_captcha again on current page
            if cnt == 0:
                try:
                    async with tab.expect_and_bypass_cloudflare_captcha(time_to_wait_captcha=20):
                        # Don't navigate — just wait for the embedded Turnstile to solve
                        await asyncio.sleep(15)
                except Exception:
                    pass
                # Click submit again and wait
                await _get(tab, JS_CLICK_BTN)
                for _ in range(15):
                    await asyncio.sleep(1.5)
                    cnt_r = await tab.execute_script(JS_MSG_CNT)
                    cnt = cnt_r.get('result',{}).get('result',{}).get('value',0) or 0
                    if cnt > 0:
                        break

            raw_val = await _get(tab, JS_GET_MSGS)
            try:
                messages = json.loads(raw_val) if raw_val else []
            except Exception:
                messages = []

            if not messages and cnt == 0:
                return {
                    'error': 'turnstile_verification_required',
                    'message': 'jiemahao.com 需要 Cloudflare Turnstile 人机验证，无头浏览器暂时无法通过。请稍后重试。',
                    'phoneId': phone_id,
                    'messages': [],
                    'count': 0,
                }

            return {'phoneNumber': phone_number, 'phoneId': phone_id, 'messages': messages, 'count': len(messages)}

    except Exception as e:
        return {'error': str(e), 'phoneId': phone_id, 'messages': []}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--action', choices=['numbers','messages'], required=True)
    ap.add_argument('--country', default='all')
    ap.add_argument('--phone-id', type=int, default=0)
    args = ap.parse_args()
    if args.action == 'numbers':
        print(json.dumps(fetch_numbers(args.country)))
    else:
        print(json.dumps(asyncio.run(fetch_messages_pydoll(args.phone_id))))
