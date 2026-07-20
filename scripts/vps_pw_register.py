#!/usr/bin/env python3
"""
vps_pw_register.py v5 — 加stealth反检测，绕过Replit browser integrity check
"""
import argparse, asyncio, json, random, string, sys
from pathlib import Path

XRAY_PORTS = list(range(10851, 10860))

def rand_str(n): return ''.join(random.choices(string.ascii_lowercase, k=n))
def rand_digits(n): return ''.join(random.choices(string.digits, k=n))
def gen_email(): return f'usr_{rand_str(7)}{rand_digits(4)}@deltajohnsons.com'
def gen_password(): return f'M@{rand_str(4)}{rand_digits(6)}{rand_str(3)}'
def gen_username(): return f'{rand_str(6)}{rand_digits(4)}'

# JS stealth注入 — 覆盖webdriver检测
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
"""

async def do_register(email, password, username, proxy_port=10857):
    from playwright.async_api import async_playwright
    try:
        from playwright_stealth import stealth_async
        HAS_STEALTH = True
    except ImportError:
        HAS_STEALTH = False
        print('playwright_stealth not available, using manual stealth', flush=True)

    proxy_url = f'socks5://127.0.0.1:{proxy_port}'
    print(f'[VPS_REG] email={email} proxy={proxy_url} stealth={HAS_STEALTH}', flush=True)

    result = {'status': 'init', 'email': email, 'replit_token': None,
              'error': None, 'proxy_port': proxy_port}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox', '--disable-dev-shm-usage', '--disable-setuid-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--window-size=1366,768',
                '--disable-extensions',
                '--no-first-run',
                '--disable-default-apps',
                '--lang=en-US',
            ],
        )
        ctx = await browser.new_context(
            viewport={'width': 1366, 'height': 768},
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            locale='en-US',
            timezone_id='America/New_York',
            proxy={'server': proxy_url},
            java_script_enabled=True,
            bypass_csp=True,
            extra_http_headers={
                'Accept-Language': 'en-US,en;q=0.9',
            },
        )

        # 注入stealth script到所有页面
        await ctx.add_init_script(STEALTH_JS)

        # warmup
        page = await ctx.new_page()
        if HAS_STEALTH:
            await stealth_async(page)
        try:
            await page.goto('https://www.google.com', timeout=20000,
                            wait_until='domcontentloaded')
            print(f'WARMUP_OK title={await page.title()!r}', flush=True)
            await asyncio.sleep(random.uniform(2, 4))
        except Exception as e:
            print(f'WARMUP_SKIP: {e}', flush=True)
        await page.close()

        page = await ctx.new_page()
        if HAS_STEALTH:
            await stealth_async(page)

        try:
            # 先到首页建立cookie
            await page.goto('https://replit.com', timeout=30000, wait_until='domcontentloaded')
            await asyncio.sleep(random.uniform(1, 2))

            # 再到signup
            await page.goto('https://replit.com/signup', timeout=40000,
                            wait_until='domcontentloaded')
            print(f'PAGE_LOADED title={await page.title()!r}', flush=True)

            # 等"Email & password"按钮
            await page.wait_for_selector('button:has-text("Email & password")', timeout=20000)
            print('EMAIL_BTN_VISIBLE', flush=True)

            # 模拟真实用户行为：先移动鼠标
            await page.mouse.move(
                random.randint(300, 600), random.randint(300, 500))
            await asyncio.sleep(random.uniform(0.5, 1.2))

            # 点击展开
            await page.click('button:has-text("Email & password")')
            print('CLICKED_EMAIL_BTN', flush=True)
            await asyncio.sleep(random.uniform(1.5, 2.5))

            # 等email input
            email_sel = None
            for sel in ['input[name="email"]', 'input[type="email"]']:
                try:
                    await page.wait_for_selector(sel, timeout=10000)
                    email_sel = sel
                    print(f'EMAIL_INPUT={sel}', flush=True)
                    break
                except Exception:
                    pass

            if not email_sel:
                await page.screenshot(path=f'/tmp/noform_{email.split("@")[0]}.png')
                result['status'] = 'no_email_input'
                result['error'] = 'Email input not found after clicking'
                await browser.close()
                return result

            # 模拟真实打字（逐字符）
            await page.click(email_sel)
            await asyncio.sleep(0.3)
            for ch in email:
                await page.keyboard.type(ch, delay=random.randint(30, 80))
            print(f'TYPED_EMAIL', flush=True)
            await asyncio.sleep(0.5)

            # password
            pw_sel = None
            for sel in ['input[name="password"]', 'input[type="password"]']:
                try:
                    if await page.locator(sel).count() > 0:
                        pw_sel = sel
                        break
                except Exception:
                    pass
            if pw_sel:
                await page.click(pw_sel)
                await asyncio.sleep(0.3)
                for ch in password:
                    await page.keyboard.type(ch, delay=random.randint(30, 80))
                print('TYPED_PASSWORD', flush=True)
                await asyncio.sleep(0.5)

            # username (如有)
            for sel in ['input[name="username"]', 'input[placeholder*="sername" i]']:
                try:
                    if await page.locator(sel).count() > 0:
                        await page.click(sel)
                        for ch in username:
                            await page.keyboard.type(ch, delay=random.randint(30, 70))
                        print(f'TYPED_USERNAME', flush=True)
                        break
                except Exception:
                    pass
            await asyncio.sleep(random.uniform(0.8, 1.5))

            # 提交
            submitted = False
            for sel in ['button[type="submit"]', 'button:has-text("Create Account")',
                        'button:has-text("Sign up")', 'button:has-text("Continue")']:
                try:
                    if await page.locator(sel).count() > 0:
                        await page.click(sel)
                        print(f'SUBMITTED via {sel}', flush=True)
                        submitted = True
                        break
                except Exception:
                    pass
            if not submitted:
                await page.keyboard.press('Enter')
                print('SUBMITTED via Enter', flush=True)

            # 等跳转
            try:
                await page.wait_for_url(lambda u: 'signup' not in u, timeout=20000)
            except Exception:
                pass
            await asyncio.sleep(4)

            url = page.url
            txt = ''
            try:
                txt = (await page.locator('body').inner_text())[:400].replace('\n', ' ')
            except Exception:
                pass
            print(f'FINAL_URL={url}', flush=True)
            print(f'PAGE_TEXT: {txt}', flush=True)

            if any(x in url for x in ['/home', '/repls', '/@', '/dashboard', '/account']):
                result['status'] = 'success'
            elif 'verify' in url or 'confirm' in url:
                result['status'] = 'verify_email'
            elif 'integrity' in txt.lower() or 'failed to evaluate' in txt.lower():
                result['status'] = 'bot_detected'
                result['error'] = 'Browser integrity check failed'
            elif 'already' in txt.lower() or 'in use' in txt.lower():
                result['status'] = 'email_taken'
            elif 'verify' in txt.lower() or 'check your email' in txt.lower():
                result['status'] = 'verify_email'
            elif 'captcha' in txt.lower():
                result['status'] = 'captcha_required'
                result['error'] = 'CAPTCHA'
            else:
                result['status'] = 'unknown'
                result['error'] = f'at {url}'

            # session cookie
            try:
                for c in await ctx.cookies(['https://replit.com']):
                    if c['name'] in ('connect.sid', '__Host-replit-token'):
                        result['replit_token'] = c['value'][:40]
                        print(f'TOKEN cookie={c["name"]}', flush=True)
                        break
            except Exception:
                pass

        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)
            print(f'ERR: {e}', flush=True)
        finally:
            try:
                await page.screenshot(path=f'/tmp/reg_{email.split("@")[0]}.png')
                print('SCREENSHOT saved', flush=True)
            except Exception:
                pass
            await browser.close()

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--email', default=None)
    ap.add_argument('--password', default=None)
    ap.add_argument('--username', default=None)
    ap.add_argument('--port', type=int, default=None)
    args = ap.parse_args()

    email = args.email or gen_email()
    password = args.password or gen_password()
    username = args.username or gen_username()
    port = args.port or random.choice(XRAY_PORTS)

    result = asyncio.run(do_register(email, password, username, port))
    result.update({'email': email, 'password': password, 'username': username})
    print(f'RESULT_JSON: {json.dumps(result)}', flush=True)
    sys.exit(0 if result.get('status') in ('success', 'verify_email') else 1)

if __name__ == '__main__':
    main()
