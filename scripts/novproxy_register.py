import asyncio, time, re, json, base64
from pydoll.browser import Chrome
from pydoll.browser.options import ChromiumOptions
from pydoll.protocol.fetch.events import FetchEvent, RequestPausedEvent
import ddddocr

CHROME = '/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome'
EMAIL    = 'a.diaz356@outlook.com'
PASSWORD = '@t*Z2$0bI#o%Dz*n'
INVITE   = ''
MAX_CAPTCHA_TRIES = 5

ocr = ddddocr.DdddOcr(show_ad=False)


def recognize_captcha_bytes(img_bytes: bytes) -> str:
    return ocr.classification(img_bytes).strip()


async def get_captcha_bytes(tab) -> bytes:
    """从页面提取验证码 base64 图片并返回 bytes"""
    result = await tab.execute_script('''(function(){
        var imgs = document.querySelectorAll("img.code_img");
        if(imgs.length > 0) return imgs[0].src;
        var all = document.querySelectorAll("img[src^='data:image']");
        if(all.length > 0) return all[0].src;
        return "";
    })()''')
    src = result.get('result', {}).get('result', {}).get('value', '')
    for prefix in ['data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/gif;base64,']:
        if src.startswith(prefix):
            return base64.b64decode(src[len(prefix):])
    return b''


async def refresh_captcha(tab):
    await tab.execute_script('''(function(){
        if(typeof check_verificat === "function"){ check_verificat(); return "fn"; }
        var btn = document.querySelector("img[onclick*=verificat]");
        if(btn){ btn.click(); return "click"; }
        return "nf";
    })()''')
    await asyncio.sleep(1.0)


async def fill_field(tab, elem_id: str, value: str):
    """先 click 聚焦，再 clear，再 type_text（pydoll 原生 CDP）"""
    el = await tab.find(id=elem_id, raise_exc=False)
    if not el:
        print(f'    ⚠️  find({elem_id}) 未找到')
        return False
    await el.click()
    await asyncio.sleep(0.2)
    # clear via Ctrl+A + Delete
    await el.clear()
    await asyncio.sleep(0.1)
    await el.type_text(value, humanize=False)
    await asyncio.sleep(0.3)
    # 验证 DOM 值
    chk = await tab.execute_script(
        '(function(eid){var e=document.getElementById(eid);return e?e.value:null;})("' + elem_id + '")'
    )
    val = chk.get('result', {}).get('result', {}).get('value', '')
    print(f'    {elem_id}: DOM="{val[:30]}" len={len(val)}')
    return True


async def main():
    options = ChromiumOptions()
    options.headless = True
    options.binary_location = CHROME
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.start_timeout = 30
    options.browser_preferences = {
        'profile': {'last_engagement_time': int(time.time()) - 7 * 86400,
                    'exit_type': 'Normal', 'exited_cleanly': True},
        'intl': {'accept_languages': 'en-US,en'}
    }
    options.webrtc_leak_protection = True

    async with Chrome(options=options) as browser:
        tab = await browser.start()

        # 拦截 novproxy API 请求
        api_responses = []
        async def intercept(event: RequestPausedEvent):
            p = event.get('params', {})
            req_id = p.get('requestId')
            url    = p.get('request', {}).get('url', '')
            method = p.get('request', {}).get('method', '')
            body   = p.get('request', {}).get('postData', '')
            if 'api.novproxy.com' in url and method == 'POST':
                entry = {'url': url, 'body': body}
                api_responses.append(entry)
                print(f'[API→] {method} {url}')
                if body:
                    print(f'       body: {body[:300]}')
            await tab.continue_request(req_id)

        await tab.enable_fetch_events()
        await tab.on(FetchEvent.REQUEST_PAUSED, intercept)

        # ── Step 1: 打开注册页 ─────────────────────────────────────────
        print('[1] 打开 novproxy.com/register/ ...')
        try:
            async with tab.expect_and_bypass_cloudflare_captcha():
                await tab.go_to('https://novproxy.com/register/')
        except Exception as e:
            print(f'    CF: {e}')
            await tab.go_to('https://novproxy.com/register/')
        await asyncio.sleep(2)
        print(f'    URL: {await tab.current_url}')

        # ── Step 2: 填写邮箱 ───────────────────────────────────────────
        print(f'[2] 填写邮箱: {EMAIL}')
        await fill_field(tab, 'mailbox', EMAIL)

        # ── Step 3: 填写密码 ───────────────────────────────────────────
        print(f'[3] 填写密码 (len={len(PASSWORD)})')
        await fill_field(tab, 'password', PASSWORD)

        # ── Step 4: 邀请码（可选）─────────────────────────────────────
        if INVITE:
            print(f'[4] 填写邀请码: {INVITE}')
            await fill_field(tab, 'invitecode', INVITE)

        # ── Step 5: 识别并填写图片验证码（失败则刷新重试）──────────────
        print('[5] 识别图片验证码...')
        captcha_ok = False
        captcha_text = ''
        for attempt in range(1, MAX_CAPTCHA_TRIES + 1):
            img_bytes = await get_captcha_bytes(tab)
            if not img_bytes:
                print(f'    [{attempt}] ⚠️  未能提取验证码，刷新...')
                await refresh_captcha(tab)
                continue

            # 保存样本
            with open(f'/root/pydoll-service/reg_captcha_{attempt}.png', 'wb') as f:
                f.write(img_bytes)

            try:
                code = recognize_captcha_bytes(img_bytes)
                print(f'    [{attempt}] ddddocr → "{code}"')
            except Exception as e:
                print(f'    [{attempt}] ddddocr 出错: {e}')
                await refresh_captcha(tab)
                continue

            if not code or len(code) < 3:
                print(f'    [{attempt}] 结果太短，刷新...')
                await refresh_captcha(tab)
                continue

            # 填入验证码
            await fill_field(tab, 'verification', code)
            captcha_text = code
            captcha_ok = True
            break

        if not captcha_ok:
            print('❌ 验证码多次识别失败，放弃')
            await tab.take_screenshot('/root/pydoll-service/reg_failed.png')
            return

        await asyncio.sleep(0.5)
        await tab.take_screenshot('/root/pydoll-service/reg_before_submit.png')

        # ── DOM 全量确认 ───────────────────────────────────────────────
        chk = await tab.execute_script('''(function(){
            function g(id){ var e=document.getElementById(id); return e?e.value:null; }
            return JSON.stringify({
                mail:     g("mailbox"),
                pwdLen:   (g("password")||"").length,
                captcha:  g("verification"),
                invite:   g("invitecode")
            });
        })()''')
        print(f'[DOM 确认] {chk}')

        # ── Step 6: 点击注册按钮 ───────────────────────────────────────
        api_responses.clear()
        print('[6] 点击注册按钮...')
        r = await tab.execute_script('''(function(){
            var btn = document.querySelector("button.login_btn");
            if(!btn) return "BTN_NOT_FOUND";
            btn.click();
            return "CLICKED";
        })()''')
        val = r.get('result', {}).get('result', {}).get('value', '')
        print(f'    {val}')

        # ── Step 7: 等待跳转 / 错误 ────────────────────────────────────
        print('[7] 等待结果...')
        for i in range(15):
            await asyncio.sleep(1)
            url = await tab.current_url
            if i < 8:
                print(f'    [{i+1}s] {url[:70]}')
            if '/register' not in url:
                print('🎉 注册成功！页面已跳转')
                break

        await tab.take_screenshot('/root/pydoll-service/reg_result.png')

        # ── 结果分析 ───────────────────────────────────────────────────
        html = await tab.page_source
        final_url = await tab.current_url
        errs = re.findall(r'errorTips[^>]*>([^<]{3,100})', html)

        print(f'\n=== 最终 URL: {final_url} ===')
        print(f'错误提示: {errs}')

        if api_responses:
            print(f'\n=== API 请求 ({len(api_responses)}) ===')
            for r in api_responses:
                print(f'  {r["url"]}')
                if r["body"]:
                    print(f'  body: {r["body"][:400]}')

        if '/register' not in final_url:
            print('\n✅ 注册完成！账号已创建')
        else:
            # 检查是不是验证码错误还是别的问题
            text = re.sub(r'<[^>]+>', ' ', html)
            text = ' '.join(text.split())
            print(f'页面文本: {text[:400]}')

asyncio.run(main())
