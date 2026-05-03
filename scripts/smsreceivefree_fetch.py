#!/usr/bin/env python3
"""smsreceivefree_fetch.py — with file-based cache (5-min TTL for messages, 10-min for numbers)"""
import asyncio, re, json, argparse, os, time, sys

CHROME    = '/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome'
BASE      = 'https://smsreceivefree.xyz'
CACHE_DIR = '/tmp/sms_cache_srf'
TTL_MSG   = 300   # 5 min
TTL_NUMS  = 600   # 10 min

# ── file cache ───────────────────────────────────────────────────────────
def _cache_get(key: str, ttl: int):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = f'{CACHE_DIR}/{key}.json'
    if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < ttl:
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return None

def _cache_set(key: str, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(f'{CACHE_DIR}/{key}.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass

# ── browser helpers ──────────────────────────────────────────────────────
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

async def _open_browser():
    from pydoll.browser import Chrome
    browser = Chrome(options=make_opts())
    await browser.__aenter__()
    tab = await browser.start()
    try:
        await tab.execute_script('Object.defineProperty(navigator,"webdriver",{get:()=>undefined})')
    except Exception:
        pass
    return browser, tab

async def _bypass_and_go(tab, url: str):
    async with tab.expect_and_bypass_cloudflare_captcha(time_to_wait_captcha=40):
        await tab.go_to(url)
    await asyncio.sleep(3)

async def _get_html(tab) -> str:
    try:
        r = await tab.execute_script('return document.documentElement.outerHTML')
        val = r.get('result', {}).get('result', {}).get('value', '')
        if val:
            return val
    except Exception:
        pass
    try:
        return await tab.page_source
    except Exception:
        return ''

def _parse_messages(html: str):
    messages = []
    pairs = re.findall(
        r'<div[^>]*class="[^"]*card-header[^"]*"[^>]*>(.*?)</div>'
        r'.*?'
        r'<div[^>]*class="[^"]*card-body[^"]*"[^>]*>(.*?)</div>',
        html, re.DOTALL | re.IGNORECASE
    )
    for header_raw, body_raw in pairs:
        sender = re.sub(r'<[^>]+>', '', header_raw).strip()
        footer_m = re.search(r'<footer[^>]*>(.*?)</footer>', body_raw, re.DOTALL | re.IGNORECASE)
        ts = re.sub(r'<[^>]+>', '', footer_m.group(1)).strip() if footer_m else ''
        body = re.sub(r'<footer.*?</footer>', '', body_raw, flags=re.DOTALL | re.IGNORECASE)
        body = re.sub(r'<div[^>]*class="[^"]*clear[^"]*"[^>]*/?>', '', body, flags=re.IGNORECASE)
        body = re.sub(r'<[^>]+>', '', body).strip()
        if body and len(body) > 2:
            info = (sender + ('  ' + ts if ts else '')).strip()
            messages.append({'info': info, 'body': body})
    return messages

# ── numbers scrape ───────────────────────────────────────────────────────
async def fetch_numbers_async():
    cached = _cache_get('numbers', TTL_NUMS)
    if cached:
        return cached
    browser, tab = await _open_browser()
    results = []
    try:
        await _bypass_and_go(tab, BASE + '/index.php?t=us')
        await asyncio.sleep(4)
        html = await _get_html(tab)
        seen = set()
        for n in re.findall(r'sms\.php\?p=(\d{8,11})', html) + re.findall(r'\+1[\s-]?(\d{10})', html):
            digits = re.sub(r'\D', '', n)
            if len(digits) == 10 and digits not in seen:
                seen.add(digits)
                results.append({'id': digits, 'number': '+1 ' + digits, 'source': 'smsreceivefree'})
    except Exception as e:
        results = [{'error': str(e)}]
    finally:
        try:
            await browser.__aexit__(None, None, None)
        except Exception:
            pass
    if results and 'error' not in results[0]:
        _cache_set('numbers', results)
    return results

# ── messages scrape ──────────────────────────────────────────────────────
async def fetch_messages_async(phone: str):
    ckey = f'msgs_{phone}'
    cached = _cache_get(ckey, TTL_MSG)
    if cached:
        cached['cached'] = True
        return cached

    browser, tab = await _open_browser()
    try:
        await _bypass_and_go(tab, f'{BASE}/sms.php?p={phone}')
        await asyncio.sleep(5)
        html = await _get_html(tab)
        messages = _parse_messages(html)
        # Retry if empty (sometimes needs extra wait)
        if not messages:
            await asyncio.sleep(4)
            html = await _get_html(tab)
            messages = _parse_messages(html)
        result = {
            'phoneNumber': '+1 ' + phone,
            'source': 'smsreceivefree',
            'messages': messages,
            'count': len(messages),
            'cached': False,
        }
        if messages:
            _cache_set(ckey, result)
        return result
    except Exception as e:
        return {'error': str(e), 'phoneNumber': '+1 ' + phone, 'source': 'smsreceivefree', 'messages': []}
    finally:
        try:
            await browser.__aexit__(None, None, None)
        except Exception:
            pass

# ── main ─────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--action', choices=['numbers', 'messages'], required=True)
    ap.add_argument('--phone', default='')
    ap.add_argument('--force', action='store_true', help='Delete cache and re-fetch')
    args = ap.parse_args()

    if args.action == 'numbers':
        if args.force:
            try: os.remove(f'{CACHE_DIR}/numbers.json')
            except FileNotFoundError: pass
        print(json.dumps(asyncio.run(fetch_numbers_async())))
    else:
        if not args.phone:
            print(json.dumps({'error': 'phone required', 'messages': []}))
            sys.exit(1)
        phone = args.phone.replace(' ', '')
        if args.force:
            try: os.remove(f'{CACHE_DIR}/msgs_{phone}.json')
            except FileNotFoundError: pass
        print(json.dumps(asyncio.run(fetch_messages_async(phone))))
