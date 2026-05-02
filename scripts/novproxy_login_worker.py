#!/usr/bin/env python3
"""
novproxy_login_worker.py v2 — 批量登录 + 代理凭据提取 + IP白名单
学自 Outlook 工作流隐蔽思路：
  1. 纯 HTTP 直调 API（无浏览器）
  2. 登录后自动拉取子账号代理凭据
  3. 自动写入 IP 白名单（本机IP）
  4. 拉取流量服务器列表

Args:
  --accounts  JSON: [["email","pwd"], ...]
  --proxy     可选 SOCKS5/HTTP 代理 (socks5h://host:port or http://host:port)
  --delay     秒 (default 0.5)

Output:
  [LOG]    message
  [OK]     email|password|token|access_key|proxy_user|proxy_pass|alltraffic
  [PROXY]  proxy_user|proxy_pass|us.novproxy.io|1000|alltraffic|whitelist_ip
  [FAIL]   email|reason
  [DONE]   ok/total
"""
import sys, json, time, argparse, socket
import urllib.request, urllib.parse, urllib.error

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
BASE_HEADERS = {
    'User-Agent': UA,
    'Origin': 'https://novproxy.com',
    'Referer': 'https://novproxy.com/login/',
    'Content-Type': 'application/x-www-form-urlencoded',
}
API_BASE = 'https://api.novproxy.com'

def log(msg):        print(f'[LOG]  {msg}', flush=True)
def ok(e,p,t,ak,pu,pp,at): print(f'[OK]   {e}|{p}|{t}|{ak}|{pu}|{pp}|{at}', flush=True)
def proxy_line(pu,pp,sv,pt,at,wl): print(f'[PROXY] {pu}|{pp}|{sv}|{pt}|{at}|{wl}', flush=True)
def fail(e, r):      print(f'[FAIL] {e}|{r}', flush=True)
def done(n, total):  print(f'[DONE] {n}/{total}', flush=True)

def http_post(url, data: dict, proxy_url: str = '') -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=BASE_HEADERS, method='POST')
    if proxy_url:
        # 设置代理处理器
        if proxy_url.startswith('socks'):
            import socks, socket as _sock
            try:
                parts = proxy_url.replace('socks5h://', '').replace('socks5://', '')
                h, p = parts.rsplit(':', 1)
                socks.set_default_proxy(socks.SOCKS5, h, int(p))
                _sock.socket = socks.socksocket
            except Exception:
                pass
        else:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
            )
            urllib.request.install_opener(opener)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

def get_my_ip() -> str:
    try:
        with urllib.request.urlopen('https://api.ipify.org?format=json', timeout=8) as r:
            return json.loads(r.read()).get('ip', '')
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ''

def login_one(email: str, password: str, proxy_url: str = ''):
    """
    隐蔽思路：
    Step1: sign_auth  (浏览器指纹检测)
    Step2: signin     (获取 token + access_key)
    Step3: subUsers   (获取代理子账号凭据)
    Step4: traffic/server (获取代理服务器列表)
    Step5: addTrafficWhite (IP 白名单)
    """
    # Step 1: fingerprint check
    try:
        d1 = http_post(f'{API_BASE}/v1/sign_auth', {'email': email})
        if d1.get('code') != 0:
            return None, f"sign_auth: {d1.get('msg','failed')}"
    except Exception as e:
        return None, f"sign_auth_err: {str(e)[:80]}"
    time.sleep(0.3)

    # Step 2: signin
    try:
        d2 = http_post(f'{API_BASE}/v1/signin', {'lang': 'en', 'email': email, 'pwd': password})
        if d2.get('code') != 0:
            return None, d2.get('msg', f'code={d2.get("code")}')
        data = d2.get('data', {})
        token      = data.get('token', '')
        access_key = data.get('access_key', '')
        alltraffic = data.get('alltraffic', 0)
        if not token:
            return None, 'empty_token'
    except Exception as e:
        return None, f"signin_err: {str(e)[:80]}"

    # Step 3: 获取子账号代理凭据
    proxy_user, proxy_pass = '', ''
    try:
        d3 = http_post(f'{API_BASE}/v1/subUsers', {'token': token})
        users = d3.get('data', [])
        if users:
            proxy_user = users[0].get('username', '') or users[0].get('user', '')
            proxy_pass = users[0].get('pass', '') or users[0].get('password', '')
            # alltraffic 从 subUser 可能更准确
            traffic_str = str(users[0].get('alltraffic', alltraffic))
            try: alltraffic = float(traffic_str.replace('MB','').replace('GB',''))
            except: pass
            log(f'[{email}] 代理凭据: {proxy_user}:{proxy_pass} (流量={users[0].get("traffic","0")})')
    except Exception as e:
        log(f'[{email}] subUsers 失败: {str(e)[:60]}')

    # Step 4: 获取代理服务器列表
    proxy_server = 'us.novproxy.io'
    proxy_port = '1000'
    try:
        d4 = http_post(f'{API_BASE}/v1/traffic/server', {'token': token})
        servers = d4.get('data', [])
        if servers:
            # 优先选 US 区 1000 端口
            for sv in servers:
                if sv.get('area') == 'US' and ':1000' in sv.get('hostname',''):
                    host_full = sv['hostname']
                    proxy_server, proxy_port = host_full.split(':')
                    break
            else:
                host_full = servers[0].get('hostname', 'us.novproxy.io:1000')
                proxy_server, proxy_port = host_full.split(':')
    except Exception as e:
        log(f'[{email}] traffic/server 失败: {str(e)[:60]}')

    # Step 5: IP 白名单（添加本机 IP）
    whitelist_ip = ''
    try:
        my_ip = get_my_ip()
        if my_ip:
            d5 = http_post(f'{API_BASE}/v1/addTrafficWhite', {'token': token, 'ip': my_ip})
            msg5 = d5.get('msg', '')
            whitelist_ip = my_ip
            log(f'[{email}] IP白名单: {my_ip} → {msg5}')
    except Exception as e:
        log(f'[{email}] addTrafficWhite 失败: {str(e)[:60]}')

    return {
        'token': token, 'access_key': access_key,
        'proxy_user': proxy_user, 'proxy_pass': proxy_pass,
        'proxy_server': proxy_server, 'proxy_port': proxy_port,
        'alltraffic': alltraffic, 'whitelist_ip': whitelist_ip,
    }, 'ok'


def main(accounts, delay, proxy_url):
    n_ok = 0
    total = len(accounts)
    for i, item in enumerate(accounts):
        email, password = item[0], item[1]
        log(f'[{i+1}/{total}] 登录 {email}...')
        result, info = login_one(email, password, proxy_url)
        if result:
            t = result['token']
            ak = result['access_key']
            pu = result['proxy_user']
            pp = result['proxy_pass']
            sv = result['proxy_server']
            pt = result['proxy_port']
            at = result['alltraffic']
            wl = result['whitelist_ip']
            ok(email, password, t, ak, pu, pp, at)
            proxy_line(pu, pp, sv, pt, at, wl)
            n_ok += 1
            log(f'✅ {email} | token={t[:16]}... | 代理={pu}:{pp}@{sv}:{pt} | 流量={at}MB')
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
    ap.add_argument('--proxy', default='', help='socks5h://host:port or http://user:pass@host:port')
    args = ap.parse_args()
    main(json.loads(args.accounts), args.delay, args.proxy)
