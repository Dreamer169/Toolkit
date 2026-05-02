#!/usr/bin/env python3
"""
smsreceivefree_fetch.py -- smsreceivefree.xyz SMS fetcher
Uses stealth Chrome + pydoll to bypass Cloudflare managed challenge.
Usage:
  python3 smsreceivefree_fetch.py --action numbers
  python3 smsreceivefree_fetch.py --action messages --phone 5183535766
"""
import asyncio, sys, re, json, argparse

CHROME = '/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome'
BASE   = 'https://smsreceivefree.xyz'

def make_opts():
    from pydoll.browser.options import ChromiumOptions
    o = ChromiumOptions()
    o.headless = True
    o.binary_location = CHROME
    o.add_argument('--no-sandbox')
    o.add_argument('--disable-dev-shm-usage')
    o.add_argument('--disable-gpu')
    o.add_argument('--window-size=1280,900')
    o.add_argument('--disable-blink-features=AutomationControlled')
    o.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    o.start_timeout = 30
    return o


async def _get_tab():
    """Returns (browser_ctx, tab) with CF bypass already done on homepage."""
    from pydoll.browser import Chrome
    browser = Chrome(options=make_opts())
    await browser.__aenter__()
    tab = await browser.start()
    await tab.execute_script(
        'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
    )
    async with tab.expect_and_bypass_cloudflare_captcha(time_to_wait_captcha=30):
        await tab.go_to(BASE + '/')
    await asyncio.sleep(2)
    return browser, tab


async def fetch_numbers_async():
    browser, tab = await _get_tab()
    results = []
    try:
        await tab.go_to(BASE + '/index.php?t=us')
        await asyncio.sleep(4)
        r = await tab.execute_script(
            '(function(){'
            '  var out=[];'
            '  document.querySelectorAll("a[href]").forEach(function(a){'
            '    var h=a.getAttribute("href")||"";'
            '    var t=a.textContent.trim();'
            '    var m=h.match(/^\\/?(\\d{10,11})\\/?$/);'
            '    if(m) out.push({number:m[1],href:h,text:t});'
            '  });'
            '  return JSON.stringify(out);'
            '})()'
        )
        links = json.loads(r.get('result',{}).get('result',{}).get('value','[]'))
        seen = set()
        for lk in links:
            n = lk['number']
            if n not in seen:
                seen.add(n)
                results.append({'id': int(n), 'number': '+1 ' + n, 'source': 'smsreceivefree'})

        # If no links found via href pattern, try scraping HTML with regex
        if not results:
            r2 = await tab.execute_script('return document.body.innerHTML')
            html = r2.get('result',{}).get('result',{}).get('value','')
            nums = re.findall(r'href=["\'][/]?(\d{10,11})[/"\'?]', html)
            seen2 = set()
            for n in nums:
                if n not in seen2:
                    seen2.add(n)
                    results.append({'id': int(n), 'number': '+1 ' + n, 'source': 'smsreceivefree'})
    finally:
        await browser.__aexit__(None, None, None)
    return results


async def fetch_messages_async(phone: str):
    browser, tab = await _get_tab()
    try:
        # Visit the phone-specific page: /{number}/
        url = f'{BASE}/{phone}/'
        await tab.go_to(url)
        await asyncio.sleep(5)

        title_r = await tab.execute_script('return document.title')
        title = title_r.get('result',{}).get('result',{}).get('value','')

        # Get full page HTML
        r = await tab.execute_script('return document.body.innerHTML')
        html = r.get('result',{}).get('result',{}).get('value','')

        # Extract SMS messages from table rows
        # Site uses Bootstrap tables with columns: From | Message | Date
        msgs_r = await tab.execute_script(
            '(function(){'
            '  var out=[];'
            '  var rows=document.querySelectorAll("table tbody tr, .table tr");'
            '  rows.forEach(function(tr){'
            '    var cells=tr.querySelectorAll("td");'
            '    if(cells.length>=2){'
            '      var from=(cells[0]||{}).textContent||"";'
            '      var body=(cells[1]||{}).textContent||"";'
            '      var time=(cells[2]||{}).textContent||"";'
            '      if(body.trim()) out.push({info:from.trim()+" "+time.trim(),body:body.trim()});'
            '    }'
            '  });'
            '  if(!out.length){'
            '    var cards=document.querySelectorAll(".card-body p, .sms-body, .message-body");'
            '    cards.forEach(function(el){ if(el.textContent.trim()) out.push({info:"",body:el.textContent.trim()}); });'
            '  }'
            '  return JSON.stringify(out);'
            '})()'
        )
        messages = json.loads(msgs_r.get('result',{}).get('result',{}).get('value','[]'))

        # Fallback: regex parse from HTML for common patterns
        if not messages:
            trs = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
            for tr in trs:
                tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.DOTALL)
                if len(tds) >= 2:
                    body = re.sub(r'<[^>]+>', '', tds[1]).strip()
                    sender = re.sub(r'<[^>]+>', '', tds[0]).strip()
                    ts = re.sub(r'<[^>]+>', '', tds[2]).strip() if len(tds) > 2 else ''
                    if body and len(body) > 2:
                        messages.append({'info': f'{sender} {ts}'.strip(), 'body': body})

        return {
            'phoneNumber': '+1 ' + phone,
            'source': 'smsreceivefree',
            'messages': messages,
            'count': len(messages),
        }
    except Exception as e:
        return {'error': str(e), 'phoneNumber': '+1 ' + phone, 'source': 'smsreceivefree', 'messages': []}
    finally:
        await browser.__aexit__(None, None, None)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--action', choices=['numbers', 'messages'], required=True)
    ap.add_argument('--phone', default='')
    args = ap.parse_args()

    if args.action == 'numbers':
        print(json.dumps(asyncio.run(fetch_numbers_async())))
    else:
        if not args.phone:
            print(json.dumps({'error': 'phone required'}))
        else:
            print(json.dumps(asyncio.run(fetch_messages_async(args.phone))))
