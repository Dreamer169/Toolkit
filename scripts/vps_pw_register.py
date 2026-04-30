#!/usr/bin/env python3
"""
vps_pw_register.py v3 — 修复: 先点"Or Email & password"展开表单
"""
import argparse, asyncio, json, random, string, sys
from pathlib import Path

XRAY_PORTS = list(range(10851, 10860))

def rand_str(n): return ''.join(random.choices(string.ascii_lowercase, k=n))
def rand_digits(n): return ''.join(random.choices(string.digits, k=n))
def gen_email(): return f'usr_{rand_str(7)}{rand_digits(4)}@deltajohnsons.com'
def gen_password(): return f'M@{rand_str(4)}{rand_digits(6)}{rand_str(3)}'
def gen_username(): return f'{rand_str(6)}{rand_digits(4)}'

async def do_register(email, password, username, proxy_port=10857):
    from playwright.async_api import async_playwright

    proxy_url = f'socks5://127.0.0.1:{proxy_port}'
    print(f'[VPS_REG] email={email} proxy={proxy_url}', flush=True)

    result = {'status': 'init', 'email': email, 'replit_token': None,
              'error': None, 'proxy_port': proxy_port}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox', '--disable-dev-shm-usage',
                '--disable-setuid-sandbox',
                '--disable-blink-features=AutomationControlled',
            ],
        )
        ctx = await browser.new_context(
            viewport={'width': 1366, 'height': 768},
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            locale='en-US', timezone_id='America/New_York',
            proxy={'server': proxy_url},
        )

        # warmup
        page = await ctx.new_page()
        try:
            await page.goto('https://www.google.com', timeout=20000,
                            wait_until='domcontentloaded')
            print(f'WARMUP_OK title={await page.title()!r}', flush=True)
            await asyncio.sleep(2)
        except Exception as e:
            print(f'WARMUP_SKIP: {e}', flush=True)
        await page.close()

        page = await ctx.new_page()
        try:
            await page.goto('https://replit.com/signup', timeout=45000,
                            wait_until='domcontentloaded')
            print(f'PAGE_LOADED title={await page.title()!r}', flush=True)

            # 等待社交登录按钮出现 (说明React已渲染)
            await page.wait_for_selector('text="Or Email & password"', timeout=20000)
            print('SOCIAL_BUTTONS_RENDERED', flush=True)
            await asyncio.sleep(1)

            # 点击 "Or Email & password" 展开邮箱表单
            await page.click('text="Or Email & password"')
            print('CLICKED_EMAIL_OPTION', flush=True)
            await asyncio.sleep(1.5)

            # 等待email输入框出现
            email_sel = None
            for sel in [
                'input[name="email"]', 'input[type="email"]',
                'input[placeholder*="email" i]',
            ]:
                try:
                    await page.wait_for_selector(sel, timeout=10000)
                    email_sel = sel
                    print(f'EMAIL_FIELD={sel}', flush=True)
                    break
                except Exception:
                    pass

            if not email_sel:
                await page.screenshot(path=f'/tmp/noform_{email.split("@")[0]}.png')
                result['status'] = 'no_email_field'
                result['error'] = 'Email input still not found after clicking email option'
                await browser.close()
                return result

            # 填写表单
            await page.fill(email_sel, email)
            await asyncio.sleep(0.5)

            # username (可能先出现)
            for sel in ['input[name="username"]', 'input[placeholder*="sername" i]']:
                if await page.locator(sel).count() > 0:
                    await page.fill(sel, username)
                    await asyncio.sleep(0.5)
                    break

            # password
            for sel in ['input[name="password"]', 'input[type="password"]']:
                if await page.locator(sel).count() > 0:
                    await page.fill(sel, password)
                    await asyncio.sleep(0.5)
                    break

            # submit
            submitted = False
            for sel in [
                'button[type="submit"]',
                'button:has-text("Sign up")',
                'button:has-text("Create account")',
                'button:has-text("Continue")',
                'button:has-text("Next")',
            ]:
                if await page.locator(sel).count() > 0:
                    await page.click(sel)
                    print(f'SUBMITTED via {sel}', flush=True)
                    submitted = True
                    break

            if not submitted:
                print('NO_SUBMIT_BUTTON - pressing Enter', flush=True)
                await page.keyboard.press('Enter')

            # 等跳转
            try:
                await page.wait_for_url(lambda url: 'signup' not in url, timeout=20000)
            except Exception:
                pass
            await asyncio.sleep(4)

            url = page.url
            print(f'FINAL_URL={url}', flush=True)

            if any(x in url for x in ['/home', '/repls', '/@', '/dashboard', '/account']):
                result['status'] = 'success'
                print('SIGNUP_SUCCESS', flush=True)
            elif 'verify' in url or 'confirm' in url:
                result['status'] = 'verify_email'
                print('VERIFY_EMAIL_REQUIRED', flush=True)
            else:
                body = await page.content()
                lower = body.lower()
                if 'already' in lower or 'in use' in lower:
                    result['status'] = 'email_taken'
                elif 'verify' in lower or 'check your email' in lower:
                    result['status'] = 'verify_email'
                elif 'captcha' in lower or 'hcaptcha' in lower:
                    result['status'] = 'captcha_required'
                    result['error'] = 'CAPTCHA detected'
                else:
                    result['status'] = 'unknown'
                    result['error'] = f'at {url}'
                    # 打印页面文字帮助调试
                    try:
                        txt = await page.locator('body').inner_text()
                        print(f'BODY[:400]={txt[:400].replace(chr(10)," ")}', flush=True)
                    except Exception:
                        pass

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
