#!/usr/bin/env python3
"""
Playwright CDP sniff: 在obvious.ai首次创建sandbox时捕获e2b access token
使用cz-test1（sandboxId=null，触发首次sandbox创建）
"""
import asyncio, json, sys, os
from playwright.async_api import async_playwright

ACCOUNT = 'cz-test1'
ACC_DIR = f'/root/obvious-accounts/{ACCOUNT}'
manifest = json.load(open(f'{ACC_DIR}/manifest.json'))

all_requests = []
all_responses = []

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-setuid-sandbox',
                  '--enable-logging', '--log-level=0']
        )
        ctx = await browser.new_context(
            storage_state=f'{ACC_DIR}/storage_state.json',
            viewport={'width': 1280, 'height': 900},
        )
        
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        await cdp.send('Network.enable')
        
        # CDP完整请求头捕获（包括Authorization等敏感头）
        def on_request_extra(params):
            url = params.get('request', {}).get('url', '')
            headers = params.get('requestHeaders', params.get('headers', {}))
            if any(x in url.lower() for x in ['e2b', 'sandbox', 'envd', 'obvious.ai/api']):
                print(f'[REQ] {url[:100]}')
                for k, v in headers.items():
                    if any(x in k.lower() for x in ['auth', 'token', 'key', 'access', 'bearer']):
                        print(f'  {k}: {v[:100]}')
                all_requests.append({'url': url, 'headers': dict(headers)})
        
        def on_response_received(params):
            url = params.get('response', {}).get('url', '')
            if any(x in url.lower() for x in ['e2b', 'sandbox', 'envd']):
                print(f'[RESP] {url[:100]} status={params.get("response",{}).get("status")}')
                all_responses.append(params)
        
        cdp.on('Network.requestWillBeSentExtraInfo', on_request_extra)
        cdp.on('Network.responseReceived', on_response_received)
        
        # WS
        page.on('websocket', lambda ws: print(f'[WS] {ws.url[:120]}'))
        
        print('Navigating to obvious.ai project...')
        proj_url = f'https://app.obvious.ai/workspaces/{manifest["workspaceId"]}/projects/{manifest["projectId"]}'
        try:
            await page.goto(proj_url, wait_until='domcontentloaded', timeout=30000)
        except Exception as e:
            print(f'goto error (ok): {e}')
        
        await asyncio.sleep(5)
        print('Page loaded, looking for input...')
        
        # 找输入框并发送一个简单的bash命令
        inp = None
        for selector in ['textarea', '[contenteditable="true"]', 'input[type="text"]', '[placeholder]']:
            try:
                el = page.locator(selector).first
                if await el.is_visible(timeout=3000):
                    inp = el
                    print(f'Found input: {selector}')
                    break
            except:
                pass
        
        if inp:
            await inp.fill('run: echo sandbox_alive')
            await asyncio.sleep(1)
            await inp.press('Enter')
            print('Sent message, waiting 30s for sandbox creation...')
        else:
            print('No input found, waiting anyway...')
        
        await asyncio.sleep(30)
        
        # 输出捕获结果
        print(f'\n=== CAPTURED {len(all_requests)} e2b requests, {len(all_responses)} e2b responses ===')
        for r in all_requests:
            print(f'URL: {r["url"]}')
            for k, v in r['headers'].items():
                print(f'  {k}: {v[:100]}')
        
        # 检查cz-test1的manifest是否已更新sandboxId
        m2 = json.load(open(f'{ACC_DIR}/manifest.json'))
        print(f'sandboxId now: {m2.get("sandboxId")}')
        
        await browser.close()

asyncio.run(main())
