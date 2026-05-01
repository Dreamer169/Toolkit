#!/usr/bin/env python3
"""
repair_account.py — 为 projectId/threadId/sandboxId 为 null 的账号
                    用已有 session 重新开一个 project+thread，写回 manifest。
Usage:
  python3 repair_account.py --label cz-test1
  python3 repair_account.py --label cz-test1 --headless
"""
import asyncio, argparse, json, re, time
from pathlib import Path
from datetime import datetime, timezone

ACC_DIR = Path('/root/obvious-accounts')

async def repair(label: str, headless: bool):
    from playwright.async_api import async_playwright

    mf_path  = ACC_DIR / label / 'manifest.json'
    ss_path  = ACC_DIR / label / 'storage_state.json'
    shots_dir = ACC_DIR / label / 'shots'
    shots_dir.mkdir(parents=True, exist_ok=True)

    if not ss_path.exists():
        print(f'[repair] ERROR: no storage_state for {label}'); return

    mf = json.loads(mf_path.read_text()) if mf_path.exists() else {}
    proxy_url = mf.get('proxy')
    print(f'[repair] {label}  proxy={proxy_url}  headless={headless}')

    pw_proxy = None
    if proxy_url:
        # socks5://127.0.0.1:10821 → playwright format
        pw_proxy = {'server': proxy_url.replace('socks5://', 'socks5://')}

    api_calls: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=['--no-sandbox','--disable-setuid-sandbox',
                  '--disable-blink-features=AutomationControlled'],
            proxy=pw_proxy,
        )
        ctx = await browser.new_context(
            storage_state=str(ss_path),
            viewport={'width':1280,'height':800},
        )
        page = await ctx.new_page()

        # intercept API to capture thread/project IDs
        async def on_request(req):
            api_calls.append({'u': req.url, 'm': req.method})
        page.on('request', on_request)

        # ── 1. 打开 app，session 自动带入 ──
        print('[repair] navigating to app.obvious.ai ...')
        await page.goto('https://app.obvious.ai', wait_until='load', timeout=60000)
        await page.screenshot(path=str(shots_dir/'repair_01_home.png'))

        # ── 2. 找或点击「New project / + 」按钮 ──
        new_btn = None
        for sel in ['button:has-text("New project")',
                    'button:has-text("New Project")',
                    '[data-testid="new-project"]',
                    'a:has-text("New project")',
                    'button[aria-label*="new"]',
                    'button:has-text("+")']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    new_btn = el; break
            except Exception:
                pass

        if new_btn:
            print('[repair] clicking New Project button')
            await new_btn.click()
            await page.wait_for_load_state('networkidle', timeout=15000)
        else:
            # 直接导航到 /new 路径
            print('[repair] no button found, trying /new route')
            await page.goto('https://app.obvious.ai/new', wait_until='load', timeout=30000)

        await page.screenshot(path=str(shots_dir/'repair_02_newproject.png'))
        await asyncio.sleep(2)

        # ── 3. 找 chat 输入框，发一条消息 ──
        ci = None
        for sel in ['[contenteditable="true"]', 'textarea', 'input[type="text"]']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    ci = el; break
            except Exception:
                pass

        if not ci:
            print('[repair] ERROR: cannot find chat input')
            await browser.close(); return

        await ci.click()
        await asyncio.sleep(0.5)
        msg = 'Sandbox health check — please run: echo alive && uname -n && date -u'
        await page.keyboard.type(msg, delay=15)
        await asyncio.sleep(0.5)
        await page.keyboard.press('Enter')
        print('[repair] message sent, waiting for thread/project IDs ...')
        await page.screenshot(path=str(shots_dir/'repair_03_sent.png'))

        # ── 4. 轮询 URL + api_calls 拿到 projectId / threadId ──
        project_id = None; thread_id = None
        for _ in range(40):
            await asyncio.sleep(1.5)
            # URL 里找 project slug
            url_now = page.url
            m = re.search(r'/p/([a-z0-9-]+-)?([A-Za-z0-9]{6,})', url_now)
            if m:
                project_id = 'prj_' + m.group(2)
            # api_calls 里找 thread_id
            for c in api_calls:
                m2 = (re.search(r'/threads/(th_[A-Za-z0-9]+)/', c['u'])
                      or re.search(r'/agent/chat/(th_[A-Za-z0-9]+)', c['u']))
                if m2: thread_id = m2.group(1)
            if project_id and thread_id:
                break

        print(f'[repair] projectId={project_id}  threadId={thread_id}')
        await page.screenshot(path=str(shots_dir/'repair_04_ids.png'))

        # ── 5. 轮询 messages API 拿 sandboxId ──
        sandbox_id = None
        if thread_id:
            for _ in range(25):
                await asyncio.sleep(3)
                try:
                    msgs = await page.evaluate("""async (tid) => {
                        const r = await fetch(
                            'https://api.app.obvious.ai/prepare/threads/'+tid+'/messages',
                            {credentials:'include'});
                        return {s: r.status, b: await r.text()};
                    }""", thread_id)
                    if msgs.get('s') == 200:
                        m3 = re.search(r'"sandboxId"\s*:\s*"([a-z0-9]+)"', msgs['b'])
                        if m3: sandbox_id = m3.group(1); break
                except Exception as e:
                    print(f'  messages poll err: {e}')

        print(f'[repair] sandboxId={sandbox_id}')

        # ── 6. 保存更新后的 storage_state ──
        state = await ctx.storage_state()
        ss_path.write_text(json.dumps(state))

        await browser.close()

    # ── 7. 写回 manifest ──
    if project_id and thread_id:
        mf['projectId'] = project_id
        mf['threadId']  = thread_id
        if sandbox_id:
            mf['sandboxId'] = sandbox_id
        mf['repairedAt'] = datetime.now(timezone.utc).isoformat()
        if 'deadReason' in mf and mf.get('deadReason') == 'credit_depleted':
            pass  # don't clear dead if genuinely depleted
        elif mf.get('status') == 'dead' and mf.get('deadReason') not in ('credit_depleted',):
            mf['status'] = 'active'
            mf['deadReason'] = None
        mf_path.write_text(json.dumps(mf, indent=2))
        print(f'[repair] ✅ manifest updated: pid={project_id} tid={thread_id} sb={sandbox_id}')

        # 同步 index.json
        idx_path = ACC_DIR / 'index.json'
        if idx_path.exists():
            accs = json.loads(idx_path.read_text())
            for a in (accs if isinstance(accs, list) else accs.values()):
                if a.get('label') == label:
                    a['projectId'] = project_id
                    a['threadId']  = thread_id
                    if sandbox_id: a['sandboxId'] = sandbox_id
            idx_path.write_text(json.dumps(accs, indent=2))
            print('[repair] index.json updated')
    else:
        print('[repair] ❌ could not get projectId/threadId')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--label', required=True)
    ap.add_argument('--headless', action='store_true', default=False)
    args = ap.parse_args()
    asyncio.run(repair(args.label, args.headless))
