#!/usr/bin/env python3
"""
oauth_reauth_authcode.py v2 — authorization_code 流补授权
用同步 patchright (同 outlook_register.py), proxy=10808
"""

import sys, os, re, json
import urllib.parse as _up
import urllib.request as _ur
import psycopg2

sys.path.insert(0, '/root/Toolkit/artifacts/api-server')

CLIENT_ID    = '9e5f94bc-e8a4-4e73-b8be-63364c29d753'
REDIRECT_URI = 'https://login.microsoftonline.com/common/oauth2/nativeclient'
SCOPES = [
    'offline_access',
    'https://graph.microsoft.com/Mail.Read',
    'https://graph.microsoft.com/Mail.ReadWrite',
    'https://graph.microsoft.com/User.Read',
]
SCOPE = ' '.join(SCOPES)
PROXY = 'socks5://127.0.0.1:10808'

DB_DSN = "host=localhost dbname=toolkit user=postgres password=postgres"

def get_accounts(limit=3):
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, email, password FROM accounts
        WHERE platform='outlook'
          AND status IN ('needs_oauth', 'needs_oauth_pending')
          AND COALESCE(tags,'') LIKE '%%needs_oauth_manual%%'
          AND password IS NOT NULL AND password != ''
          AND COALESCE(tags,'') NOT LIKE '%%token_invalid%%'
          AND COALESCE(tags,'') NOT LIKE '%%abuse_mode%%'
        ORDER BY created_at ASC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

def save_token(account_id, email, access_token, refresh_token):
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("""
        UPDATE accounts
        SET status='active',
            access_token=%s,
            refresh_token=%s,
            tags=regexp_replace(COALESCE(tags,''), 'needs_oauth_manual,?', '', 'g'),
            updated_at=NOW()
        WHERE id=%s
    """, (access_token, refresh_token, account_id))
    conn.commit()
    conn.close()
    print(f"[db] ✅ {email} → active + tokens 已写入", flush=True)

def mark_failed(account_id, email, reason):
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("""
        UPDATE accounts
        SET status='needs_oauth',
            tags=COALESCE(tags,'') || ',authcode_failed',
            updated_at=NOW()
        WHERE id=%s
    """, (account_id,))
    conn.commit()
    conn.close()
    print(f"[db] ❌ {email} 标记失败: {reason}", flush=True)

def exchange_token(code):
    token_body = _up.urlencode({
        'grant_type':   'authorization_code',
        'client_id':    CLIENT_ID,
        'code':         code,
        'redirect_uri': REDIRECT_URI,
        'scope':        SCOPE,
    }).encode()
    req = _ur.Request(
        'https://login.microsoftonline.com/consumers/oauth2/v2.0/token',
        data=token_body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    return json.loads(_ur.urlopen(req, timeout=15).read())

def reauth_one(account_id, email, password):
    print(f"\n{'='*60}", flush=True)
    print(f"[reauth] {email}", flush=True)

    from patchright.sync_api import sync_playwright
    from browser_fingerprint import gen_profile, context_kwargs

    scope_encoded = '%20'.join(_up.quote(s, safe=':/') for s in SCOPES)
    auth_url = (
        'https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize'
        f'?client_id={CLIENT_ID}'
        '&response_type=code'
        f'&redirect_uri={_up.quote(REDIRECT_URI, safe="")}'
        f'&scope={scope_encoded}'
        '&prompt=login'
        f'&login_hint={_up.quote(email, safe="")}'
    )

    captured = {'code': None, 'error': None, 'error_description': None}

    def _intercept(route, request):
        url = request.url
        if 'code=' in url or 'error=' in url:
            params = _up.parse_qs(_up.urlparse(url).query)
            if 'code' in params and not captured['code']:
                captured['code'] = params['code'][0]
                print(f"[oauth] ✅ code={params['code'][0][:12]}...", flush=True)
            elif 'error' in params and not captured['error']:
                captured['error'] = params['error'][0]
                captured['error_description'] = params.get('error_description', [''])[0]
        try:
            route.abort()
        except Exception:
            pass

    p = sync_playwright().start()
    try:
        fp = gen_profile(locale="en-US")
        proxy_cfg = {"server": PROXY, "bypass": "localhost"}
        b = p.chromium.launch(
            headless=True,
            proxy=proxy_cfg,
            args=[
                "--no-sandbox", "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-web-security",
            ],
        )
        ctx = b.new_context(**context_kwargs(fp))
        page = ctx.new_page()

        _NC_PATH_RE = re.compile(r"^https?://[^/]+/[^?]*nativeclient", re.IGNORECASE)
        try:
            page.route(_NC_PATH_RE, _intercept)
        except Exception as e:
            print(f"[oauth] 路由安装失败: {e}", flush=True)

        try:
            page.goto(auth_url, timeout=25000, wait_until='domcontentloaded')
        except Exception as e:
            if 'nativeclient' not in str(e):
                print(f"[reauth] goto 异常: {e}", flush=True)

        page.wait_for_timeout(3000)
        page.screenshot(path=f'/tmp/reauth_{email}_01.png')

        SKIP_SELS = [
            'input[type="submit"][value="No"]', 'button:has-text("No")',
            'button:has-text("Skip for now")', 'button:has-text("Maybe later")',
            'button:has-text("Not now")', '[data-testid="secondaryButton"]',
            '#idBtn_Back', 'button:has-text("跳过")', 'button:has-text("稍后")',
        ]
        CONSENT_SELS = [
            '[data-testid="appConsentPrimaryButton"]',
            'input[type="submit"][value*="Accept"]',
            'button:has-text("Accept")', 'button:has-text("接受")',
            'button:has-text("Allow")', 'button:has-text("允许")',
            '[data-testid="primaryButton"]', 'input[value="Continue"]',
        ]

        for rnd in range(10):
            cur_url = page.url or ''
            print(f"[reauth] round={rnd+1} url={cur_url[:90]}", flush=True)
            page.screenshot(path=f'/tmp/reauth_{email}_{rnd+2:02d}.png')

            if captured['code']:
                break

            try:
                _path = _up.urlparse(cur_url).path or ''
            except Exception:
                _path = ''
            if 'nativeclient' in _path:
                # 从 URL 拿 code
                params = _up.parse_qs(_up.urlparse(cur_url).query)
                if 'code' in params:
                    captured['code'] = params['code'][0]
                break

            # error 检测
            if captured['error']:
                print(f"[oauth] error={captured['error']}: {(captured['error_description'] or '')[:80]}", flush=True)
                break

            # 邮箱输入框
            try:
                ei = page.locator('input[type="email"], input[name="loginfmt"]').first
                if ei.is_visible(timeout=1200):
                    ei.fill(email)
                    page.wait_for_timeout(400)
                    for s in ['#idSIButton9', 'input[type="submit"]', '[data-testid="primaryButton"]', 'button[type="submit"]']:
                        try:
                            b2 = page.locator(s).first
                            if b2.is_visible(timeout=600):
                                b2.click()
                                break
                        except Exception:
                            continue
                    page.wait_for_timeout(3000)
                    continue
            except Exception:
                pass

            # 密码输入框
            try:
                pi = page.locator('input[type="password"], input[name="passwd"]').first
                if pi.is_visible(timeout=1200):
                    print(f"[reauth] 输入密码...", flush=True)
                    pi.fill(password)
                    page.wait_for_timeout(400)
                    for s in ['#idSIButton9', 'input[type="submit"]', '[data-testid="primaryButton"]', 'button[type="submit"]']:
                        try:
                            b2 = page.locator(s).first
                            if b2.is_visible(timeout=600):
                                b2.click()
                                break
                        except Exception:
                            continue
                    page.wait_for_timeout(3000)
                    continue
            except Exception:
                pass

            # KMSI / Skip 打断
            _clicked = False
            for s in SKIP_SELS:
                try:
                    b2 = page.locator(s).first
                    if b2.is_visible(timeout=700):
                        b2.click()
                        print(f"[reauth] skip: {s}", flush=True)
                        page.wait_for_timeout(2000)
                        _clicked = True
                        break
                except Exception:
                    continue

            if not _clicked:
                # consent / accept
                for s in CONSENT_SELS:
                    try:
                        b2 = page.locator(s).first
                        if b2.is_visible(timeout=700):
                            b2.click()
                            print(f"[reauth] consent: {s}", flush=True)
                            page.wait_for_timeout(3000)
                            _clicked = True
                            break
                    except Exception:
                        continue
                if not _clicked:
                    page.wait_for_timeout(3000)

        # 最后从 URL 拿 code
        if not captured['code']:
            cur_url = page.url or ''
            if '?' in cur_url:
                params = _up.parse_qs(_up.urlparse(cur_url).query)
                if 'code' in params:
                    captured['code'] = params['code'][0]

        b.close()
    finally:
        p.stop()

    code = captured['code']
    if not code:
        err = captured.get('error') or 'no_code'
        desc = (captured.get('error_description') or '')[:100]
        print(f"[reauth] ❌ 未拿到 code: [{err}] {desc}", flush=True)
        mark_failed(account_id, email, f"{err}: {desc}")
        return False

    print(f"[reauth] 换取 token...", flush=True)
    try:
        resp = exchange_token(code)
        if resp.get('access_token'):
            rt = resp.get('refresh_token', '')
            print(f"[reauth] ✅ 成功! refresh_token={rt[:20]}...", flush=True)
            save_token(account_id, email, resp['access_token'], rt)
            return True
        else:
            err = resp.get('error', 'unknown')
            desc = (resp.get('error_description') or '')[:100]
            print(f"[reauth] ❌ token 交换失败: {err} - {desc}", flush=True)
            mark_failed(account_id, email, f"token_exchange: {err}")
            return False
    except Exception as e:
        print(f"[reauth] ❌ 异常: {e}", flush=True)
        mark_failed(account_id, email, str(e))
        return False

def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    accounts = get_accounts(limit=limit)
    if not accounts:
        print("没有需要处理的账号")
        return

    print(f"待处理 {len(accounts)} 个账号:")
    for aid, email, _ in accounts:
        print(f"  ID={aid} {email}")

    results = []
    for account_id, email, password in accounts:
        ok = reauth_one(account_id, email, password)
        results.append((email, ok))

    print(f"\n{'='*60}")
    print("结果:")
    ok_count = sum(1 for _, ok in results if ok)
    for email, ok in results:
        print(f"  {'✅' if ok else '❌'}  {email}")
    print(f"\n成功 {ok_count}/{len(results)}")

if __name__ == '__main__':
    main()
