#!/usr/bin/env python3
"""
用Playwright打开obvious.ai, 触发sandbox唤醒, 拦截WS请求头提取e2b鉴权token
"""
import asyncio, json, sys, os, re
from playwright.async_api import async_playwright

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'eu-test1'
ACC_DIR = f'/root/obvious-accounts/{ACCOUNT}'
manifest = json.load(open(f'{ACC_DIR}/manifest.json'))
PROJ_ID = manifest['projectId']
THREAD_ID = manifest['threadId']
SANDBOX_ID = manifest['sandboxId']

captured = {'ws_headers': [], 'requests': []}

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            '--no-sandbox', '--disable-dev-shm-usage',
            '--disable-setuid-sandbox',
        ])
        ctx = await browser.new_context(
            storage_state=f'{ACC_DIR}/storage_state.json',
            viewport={'width': 1280, 'height': 720},
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        )
        page = await ctx.new_page()

        # 拦截所有请求
        async def on_request(req):
            url = req.url
            headers = dict(req.headers)
            # 找e2b相关
            if 'e2b' in url or 'sandbox' in url:
                print(f'REQ: {req.method} {url}')
                for k, v in headers.items():
                    if any(x in k.lower() for x in ['auth','key','token','bearer']):
                        print(f'  {k}: {v[:80]}')
                captured['requests'].append({'url': url, 'headers': headers})
            # WS upgrade
            if req.resource_type == 'websocket':
                print(f'WS: {url}')
                for k, v in headers.items():
                    print(f'  {k}: {v[:80]}')

        page.on('request', on_request)

        # 拦截WS
        async def on_websocket(ws):
            print(f'WS OPENED: {ws.url}')
            captured['ws_headers'].append(ws.url)

        page.on('websocket', on_websocket)

        # 导航到项目页面
        url = f'https://app.obvious.ai/workspaces/{manifest["workspaceId"]}/projects/{PROJ_ID}'
        print(f'Navigating to {url}')
        await page.goto(url, timeout=30000)
        await asyncio.sleep(5)

        # 发送一个简单消息触发sandbox唤醒
        print('Sending wake message...')
        # 找输入框
        try:
            inp = page.get_by_placeholder('Message')
            await inp.fill('echo hello')
            await asyncio.sleep(1)
            await inp.press('Enter')
        except Exception as e:
            print(f'Input error: {e}')
            # 找任意textarea
            try:
                ta = page.locator('textarea').first
                await ta.fill('echo hello')
                await ta.press('Enter')
            except Exception as e2:
                print(f'Textarea error: {e2}')

        # 等待WS连接
        await asyncio.sleep(15)

        # 也直接看网络请求
        print('\n=== captured WS URLs ===')
        for u in captured['ws_headers']:
            print(u)
        print('\n=== e2b requests ===')
        for r in captured['requests']:
            print(r['url'])

        await browser.close()

asyncio.run(main())
