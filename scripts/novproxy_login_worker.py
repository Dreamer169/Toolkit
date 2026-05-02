#!/usr/bin/env python3
"""
novproxy_login_worker.py — 批量登录 novproxy.com (纯 HTTP, 无浏览器)
学自 Outlook 工作流隐蔽思路：通过网络拦截发现 API 端点后直接调用

Args:
  --accounts  JSON: [["email","pwd"], ...]
  --delay     秒 (default 0.5)
Output:
  [LOG]    message
  [OK]     email|password|token|access_key
  [FAIL]   email|reason
  [DONE]   ok/total
"""
import sys, json, time, argparse
import urllib.request, urllib.parse

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
HEADERS = {
    'User-Agent': UA,
    'Origin': 'https://novproxy.com',
    'Referer': 'https://novproxy.com/login/',
    'Content-Type': 'application/x-www-form-urlencoded',
}

def log(msg):        print(f'[LOG]  {msg}', flush=True)
def ok(e,p,t,ak):   print(f'[OK]   {e}|{p}|{t}|{ak}', flush=True)
def fail(e, r):      print(f'[FAIL] {e}|{r}', flush=True)
def done(n, total):  print(f'[DONE] {n}/{total}', flush=True)

def http_post(url, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=HEADERS, method='POST')
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

def login_one(email, password):
    """
    隐蔽思路：模拟浏览器真实 API 调用序列
    Step1: sign_auth — 指纹检测 (浏览器也会发)
    Step2: signin    — 实际登录
    """
    # Step 1: fingerprint check
    try:
        d1 = http_post('https://api.novproxy.com/v1/sign_auth', {'email': email})
        if d1.get('code') != 0:
            return None, None, f"sign_auth: {d1.get('msg','failed')}"
    except Exception as e:
        return None, None, f"sign_auth_err: {str(e)[:80]}"

    time.sleep(0.3)  # 模拟人工操作延迟

    # Step 2: actual login
    try:
        d2 = http_post('https://api.novproxy.com/v1/signin', {
            'lang': 'en',
            'email': email,
            'pwd': password,
        })
        if d2.get('code') == 0:
            data = d2.get('data', {})
            token      = data.get('token', '')
            access_key = data.get('access_key', '')
            return token, access_key, 'ok'
        return None, None, d2.get('msg', f'code={d2.get("code")}')
    except Exception as e:
        return None, None, f"signin_err: {str(e)[:80]}"

def main(accounts, delay):
    n_ok = 0
    total = len(accounts)
    for i, item in enumerate(accounts):
        email, password = item[0], item[1]
        log(f'[{i+1}/{total}] 登录 {email}...')
        token, access_key, info = login_one(email, password)
        if token:
            ok(email, password, token, access_key or '')
            n_ok += 1
            log(f'✅ {email} token={token[:16]}...')
        else:
            fail(email, info)
            log(f'❌ {email}: {info}')
        if i < total - 1:
            time.sleep(delay)
    done(n_ok, total)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--accounts', required=True)
    ap.add_argument('--delay', type=float, default=0.5)
    args = ap.parse_args()
    main(json.loads(args.accounts), args.delay)
