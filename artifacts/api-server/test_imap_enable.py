#!/usr/bin/env python3
"""
test_imap_enable.py — 测试修复后的 enable_imap_in_browser (v9.62)
直接从 outlook_register.py import 函数，确保测的是生产代码。

用法:
    python3 test_imap_enable.py
    python3 test_imap_enable.py --email sophia.k82@outlook.com
    python3 test_imap_enable.py --all   (测所有有 cookies 的账号，最多5个)
"""
import argparse, os, sys, json, time
import psycopg2

DB_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost/toolkit')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 直接 import 生产函数
from outlook_register import enable_imap_in_browser

def get_accounts(email=None, limit=1):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    if email:
        cur.execute(
            "SELECT email, password, cookies_json FROM accounts WHERE platform='outlook' AND email=%s",
            (email,)
        )
    else:
        cur.execute(
            """SELECT email, password, cookies_json FROM accounts
               WHERE platform='outlook' AND cookies_json IS NOT NULL
                 AND length(cookies_json::text) > 100
               ORDER BY updated_at DESC LIMIT %s""",
            (limit,)
        )
    rows = cur.fetchall()
    conn.close()
    if not rows:
        print('ERROR: no accounts found in DB')
        sys.exit(1)
    return [{'email': r[0], 'password': r[1], 'cookies': r[2]} for r in rows]

def test_one(acc, headless=True):
    email = acc['email']
    password = acc['password']
    print(f'\n{"-"*60}', flush=True)
    print(f'Testing: {email}', flush=True)

    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            storage = json.loads(acc['cookies']) if isinstance(acc['cookies'], str) else acc['cookies']
            ctx = browser.new_context(storage_state=storage)
            print(f'  cookies loaded (len={len(str(acc["cookies"]))})', flush=True)
        except Exception as e:
            print(f'  cookies load failed: {e}, using fresh context', flush=True)
            ctx = browser.new_context()

        page = ctx.new_page()
        t0 = time.time()
        try:
            ok = enable_imap_in_browser(page, email, password=password)
        except Exception as ex:
            import traceback
            print(f'  EXCEPTION: {ex}\n{traceback.format_exc()[:600]}', flush=True)
            ok = False
        elapsed = time.time() - t0

        print(f'  Result: {"✅ ENABLED" if ok else "❌ FAILED"}  elapsed={elapsed:.1f}s', flush=True)
        ctx.close()
        browser.close()
        return ok

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--email', default=None)
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--headless', default='true')
    args = parser.parse_args()
    headless = args.headless.lower() not in ('false','0','no')

    if args.all:
        accs = get_accounts(limit=5)
    elif args.email:
        accs = get_accounts(email=args.email)
    else:
        accs = get_accounts(limit=2)

    results = []
    for acc in accs:
        ok = test_one(acc, headless=headless)
        results.append((acc['email'], ok))
        time.sleep(2)

    print(f'\n{"="*60}', flush=True)
    passed = sum(1 for _, ok in results if ok)
    print(f'SUMMARY: {passed}/{len(results)} passed', flush=True)
    for email, ok in results:
        print(f'  {"✅" if ok else "❌"} {email}', flush=True)
    sys.exit(0 if passed == len(results) else 1)

if __name__ == '__main__':
    main()
