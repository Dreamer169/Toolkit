#!/usr/bin/env python3
"""smsreceivefree_fetch.py -- smsreceivefree.xyz SMS fetcher
Numbers: index.php?t=us  ->  Bootstrap card list (sms.php?p=NUMBER links)
Messages: sms.php?p=NUMBER  ->  .card-header + .card-body HTML regex extraction
"""
import asyncio, re, json, argparse

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

async def _bypass_tab():
    from pydoll.browser import Chrome
    browser = Chrome(options=make_opts())
    await browser.__aenter__()
    tab = await browser.start()
    await tab.execute_script('Object.defineProperty(navigator,"webdriver",{get:()=>undefined})')
    async with tab.expect_and_bypass_cloudflare_captcha(time_to_wait_captcha=30):
        await tab.go_to(BASE + '/')
    await asyncio.sleep(2)
    return browser, tab

async def _get_html(tab):
    r = await tab.execute_script('return document.body.innerHTML')
    return r.get('result',{}).get('result',{}).get('value','')

def _parse_messages(html):
    """Parse SMS messages from sms.php?p=NUMBER page HTML.
    Structure: .card-header (sender) + .card-body (text + footer timestamp).
    """
    messages = []
    # Match each card-header + card-body pair
    pairs = re.findall(
        r'<div[^>]*class="card-header[^"]*"[^>]*>(.*?)</div>'
        r'.*?'
        r'<div[^>]*class="card-body[^"]*"[^>]*>(.*?)</div>',
        html, re.DOTALL | re.IGNORECASE
    )
    for header_raw, body_raw in pairs:
        sender = re.sub(r'<[^>]+>', '', header_raw).strip()
        footer_m = re.search(r'<footer[^>]*>(.*?)</footer>', body_raw, re.DOTALL | re.IGNORECASE)
        ts = re.sub(r'<[^>]+>', '', footer_m.group(1)).strip() if footer_m else ''
        body = re.sub(r'<footer.*?</footer>', '', body_raw, flags=re.DOTALL | re.IGNORECASE)
        body = re.sub(r'<div[^>]*class="clear[^"]*"[^>]*/?>', '', body, flags=re.IGNORECASE)
        body = re.sub(r'<[^>]+>', '', body).strip()
        if body and len(body) > 2:
            messages.append({'info': (sender + '  ' + ts).strip(), 'body': body})
    return messages

async def fetch_numbers_async():
    browser, tab = await _bypass_tab()
    results = []
    try:
        await tab.go_to(BASE + '/index.php?t=us')
        await asyncio.sleep(5)
        html = await _get_html(tab)
        nums_links = re.findall(r'sms\.php\?p=(\d{10,11})', html)
        nums_text  = re.findall(r'\+1\s+(\d{10})', html)
        seen = set()
        all_nums = []
        for n in nums_links + nums_text:
            if n and len(n) == 10 and n not in seen:
                seen.add(n)
                all_nums.append(n)
        ts_pairs = re.findall(r'\+1\s+(\d{10})(?:[^<]*)</p>\s*<p[^>]*>([0-9 :-]+)</p>', html)
        ts_map = {n: ts.strip() for n, ts in ts_pairs}
        for n in all_nums:
            results.append({'id': n, 'number': '+1 ' + n, 'lastSeen': ts_map.get(n,''), 'source': 'smsreceivefree'})
    finally:
        await browser.__aexit__(None, None, None)
    return results

async def fetch_messages_async(phone: str):
    browser, tab = await _bypass_tab()
    try:
        await tab.go_to(f'{BASE}/sms.php?p={phone}')
        await asyncio.sleep(6)
        html = await _get_html(tab)
        messages = _parse_messages(html)
        return {'phoneNumber': '+1 ' + phone, 'source': 'smsreceivefree', 'messages': messages, 'count': len(messages)}
    except Exception as e:
        return {'error': str(e), 'phoneNumber': '+1 ' + phone, 'source': 'smsreceivefree', 'messages': []}
    finally:
        await browser.__aexit__(None, None, None)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--action', choices=['numbers','messages'], required=True)
    ap.add_argument('--phone', default='')
    args = ap.parse_args()
    if args.action == 'numbers':
        print(json.dumps(asyncio.run(fetch_numbers_async())))
    else:
        print(json.dumps(asyncio.run(fetch_messages_async(args.phone))))
