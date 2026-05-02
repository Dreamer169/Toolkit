#!/usr/bin/env python3
"""
sms_center_fetch.py -- jiemahao.com SMS Center fetcher
Usage:
  python3 sms_center_fetch.py --action numbers [--country us|gb|ca|de|th|my|ph|all]
  python3 sms_center_fetch.py --action messages --phone-id 102
"""
import asyncio, sys, re, json, argparse
import urllib.request, urllib.error

COUNTRIES = {
    'us': ('美国', 'US'),
    'gb': ('英国', 'GB'),
    'ca': ('加拿大', 'CA'),
    'de': ('德国', 'DE'),
    'th': ('泰国', 'TH'),
    'my': ('马来西亚', 'MY'),
    'ph': ('菲律宾', 'PH'),
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

CHROME = '/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome'


def fetch_url(url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception:
        return ''


def fetch_numbers(country='all'):
    results = []
    countries = list(COUNTRIES.keys()) if country == 'all' else [country]
    for c in countries:
        if c not in COUNTRIES:
            continue
        html = fetch_url(f'https://jiemahao.com/{c}/')
        if not html:
            continue
        # Primary pattern: explicit class="article-title center"
        matches = re.findall(
            r'sms/\?phone=(\d+)"[^>]*>([+\d\s()]+)<',
            html
        )
        # Fallback: any link containing a phone-like number
        if not matches:
            matches = re.findall(
                r'sms/\?phone=(\d+)[^>]*>([^<]{5,30})<',
                html
            )
        seen = set()
        for phone_id, phone_num in matches:
            phone_num = phone_num.strip()
            if not phone_num or not re.search(r'\d{7,}', phone_num):
                continue
            if phone_id in seen:
                continue
            seen.add(phone_id)
            results.append({
                'id': int(phone_id),
                'number': phone_num,
                'country': c,
                'countryName': COUNTRIES[c][0],
                'countryCode': COUNTRIES[c][1],
            })
    return results


async def fetch_messages_pydoll(phone_id: int):
    try:
        from pydoll.browser import Chrome
        from pydoll.browser.options import ChromiumOptions
    except ImportError:
        return {'error': 'pydoll_not_installed', 'messages': [], 'phoneId': phone_id}

    def make_opts():
        o = ChromiumOptions()
        o.headless = True
        o.binary_location = CHROME
        o.add_argument('--no-sandbox')
        o.add_argument('--disable-dev-shm-usage')
        o.add_argument('--disable-gpu')
        o.add_argument('--window-size=1920,1080')
        o.start_timeout = 30
        return o

    url = f'https://jiemahao.com/sms/?phone={phone_id}'

    try:
        async with Chrome(options=make_opts()) as browser:
            tab = await browser.start()

            try:
                async with tab.expect_and_bypass_cloudflare_captcha():
                    await tab.go_to(url)
            except Exception:
                await tab.go_to(url)

            await asyncio.sleep(3)

            phone_number = ''
            try:
                h1r = await tab.execute_script(
                    '(function(){ var h=document.querySelector("h1.article-title"); return h?h.textContent.trim():""; })()'
                )
                phone_number = h1r.get('result', {}).get('result', {}).get('value', '')
            except Exception:
                pass

            # Wait for Turnstile auto-solve (up to 25s)
            for _ in range(25):
                await asyncio.sleep(1)
                solved = await tab.execute_script(
                    '(function(){ var r=document.querySelector("[name=\'cf-turnstile-response\']"); return r&&r.value?r.value.substring(0,10):""; })()'
                )
                val = solved.get('result', {}).get('result', {}).get('value', '')
                if val:
                    break

            # Click submit
            await tab.execute_script(
                '(function(){ var b=document.querySelector("button[name=submit]#submit")||document.querySelector(".sms-submit"); if(b)b.click(); })()'
            )

            # Wait for messages to appear
            for _ in range(15):
                await asyncio.sleep(1.5)
                cnt = await tab.execute_script(
                    '(function(){ return document.querySelectorAll(".direct-chat-msg").length; })()'
                )
                if (cnt.get('result', {}).get('result', {}).get('value', 0) or 0) > 0:
                    break

            # Extract messages with sender info
            raw = await tab.execute_script(
                '(function(){'
                '  var items=document.querySelectorAll(".direct-chat-msg");'
                '  var out=[];'
                '  items.forEach(function(item){'
                '    var info=item.querySelector(".direct-chat-info");'
                '    var text=item.querySelector(".direct-chat-text");'
                '    if(text){'
                '      out.push({'
                '        info: info?info.textContent.trim():"",'
                '        body: text.textContent.trim()'
                '      });'
                '    }'
                '  });'
                '  return JSON.stringify(out);'
                '})()'
            )

            raw_val = raw.get('result', {}).get('result', {}).get('value', '[]')
            try:
                messages = json.loads(raw_val)
            except Exception:
                messages = []

            return {
                'phoneNumber': phone_number,
                'phoneId': phone_id,
                'messages': messages,
                'count': len(messages),
            }

    except Exception as e:
        return {'error': str(e), 'phoneId': phone_id, 'messages': []}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--action', choices=['numbers', 'messages'], required=True)
    ap.add_argument('--country', default='all')
    ap.add_argument('--phone-id', type=int, default=0)
    args = ap.parse_args()

    if args.action == 'numbers':
        print(json.dumps(fetch_numbers(args.country)))
    else:
        print(json.dumps(asyncio.run(fetch_messages_pydoll(args.phone_id))))
