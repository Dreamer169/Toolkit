#!/usr/bin/env python3
"""
ip2free_get_proxies.py v2 -- Multi-account proxy fetcher
从所有已验证的 ip2free 账号拉取 freeList，去重后输出。

Outputs:
  /tmp/ip2free_proxies.json  -- full JSON with all fields
  /tmp/ip2free_proxies.txt   -- one line per proxy: socks5://user:pass@ip:port

Account status (2026-05-09):
  OK (9): sophiagray574, e.lewis904, rylan_rivera98,
          reg2026a1/b2/c3@guerrillamailblock.com,
          ip2r_ysrlrfeu/7vgq5rxn/lhs9p54x@wshu.net
  DEAD password: emily_gomez98 (password changed, login fails)
  DEAD no-account: 5pygn9r8bhlie7, fd46qce8g3fm5m, bjd6c2ayft0zr1 (wshu.net),
                   caseyjon2860@cuvox.de, jamesdav8027@dayrep.com, emilywan9588@teleworm.us
"""
import requests, json, urllib3, argparse
urllib3.disable_warnings()

BASE_API = 'https://api.ip2free.com'
H_BASE = {
    'User-Agent':   'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.6778.85 Safari/537.36',
    'Content-Type': 'text/plain;charset=UTF-8',
    'Origin':       'https://www.ip2free.com',
    'Referer':      'https://www.ip2free.com/',
    'lang': 'cn', 'domain': 'www.ip2free.com', 'webname': 'IP2FREE',
    'affid': '', 'invitecode': '', 'serviceid': '',
}

ACCOUNTS = [
    # Outlook accounts
    {'email': 'sophiagray574@outlook.com',        'password': '8nQDovHvbR@%mWL$'},
    {'email': 'e.lewis904@outlook.com',           'password': 'Aa123456'},
    {'email': 'rylan_rivera98@outlook.com',       'password': 'AWgpis7xb0'},
    # 2026-05 batch
    {'email': 'reg2026a1@guerrillamailblock.com', 'password': 'Reg2026@reg202X'},
    {'email': 'reg2026b2@guerrillamailblock.com', 'password': 'Reg2026@reg202X'},
    {'email': 'reg2026c3@guerrillamailblock.com', 'password': 'Reg2026@reg202X'},
    # Invite-registered wshu.net
    {'email': 'ip2r_ysrlrfeu@wshu.net',          'password': 'Reg2026@Secure!'},
    {'email': 'ip2r_7vgq5rxn@wshu.net',          'password': 'Reg2026@Secure!'},
    {'email': 'ip2r_lhs9p54x@wshu.net',          'password': 'Reg2026@Secure!'},
]


def login(email, password):
    s = requests.Session()
    s.verify = False
    s.headers.update(H_BASE)
    r = s.post(BASE_API + '/api/account/login?',
               data=json.dumps({'email': email, 'password': password}), timeout=15)
    d = r.json()
    tok = d.get('data', {}).get('token')
    if not tok:
        print(f'  [FAIL] {email}: {d.get("msg","?")}')
        return None, None
    inv = d.get('data', {}).get('profile', {}).get('invite_code', '?')
    s.headers['x-token'] = tok
    return s, inv


def fetch_free_list(s, size=200):
    r = s.post(BASE_API + '/api/ip/freeList?',
               data=json.dumps({'size': size}), timeout=15)
    return r.json().get('data', {}).get('free_ip_list', [])


def main():
    ap = argparse.ArgumentParser(description='Fetch ip2free proxies from all accounts')
    ap.add_argument('--size',     type=int, default=200)
    ap.add_argument('--out-json', default='/tmp/ip2free_proxies.json')
    ap.add_argument('--out-txt',  default='/tmp/ip2free_proxies.txt')
    args = ap.parse_args()

    all_proxies = []
    seen_uids = set()

    for acct in ACCOUNTS:
        email = acct['email']
        print(f'\n=== {email} ===')
        s, inv = login(email, acct['password'])
        if not s:
            continue
        print(f'  invite_code: {inv}')
        proxies = fetch_free_list(s, args.size)
        print(f'  got {len(proxies)} from freeList')
        added = 0
        for p in proxies:
            uid = p.get('proxy_uid') or p.get('id') or f"{p['ip']}:{p['port']}"
            if uid in seen_uids:
                continue
            seen_uids.add(uid)
            entry = {
                'proxy_uid':       str(uid),
                'ip':              p.get('ip', ''),
                'port':            p.get('port', 0),
                'username':        p.get('username', ''),
                'password':        p.get('password', ''),
                'protocol':        p.get('protocol', 'socks5'),
                'city':            p.get('city', ''),
                'country_code':    p.get('country_code', ''),
                'status':          p.get('status', 1),
                'is_new':          p.get('is_new', 0),
                'last_checked_at': p.get('last_checked_at', ''),
                'expires_at':      p.get('expires_at') or p.get('expire_time') or None,
                'source_account':  email,
            }
            all_proxies.append(entry)
            added += 1
            tag = 'NEW' if entry['is_new'] else '   '
            url = f"socks5://{entry['username']}:{entry['password']}@{entry['ip']}:{entry['port']}"
            print(f"  [{tag}] {url}  ({entry['city']},{entry['country_code']}) status={entry['status']}")
        print(f'  +{added} unique')

    print(f'\n=== Total: {len(all_proxies)} unique proxies ===')

    with open(args.out_json, 'w') as f:
        json.dump({'total': len(all_proxies), 'proxies': all_proxies}, f, indent=2, ensure_ascii=False)
    print(f'Saved: {args.out_json}')

    with open(args.out_txt, 'w') as f:
        for p in all_proxies:
            f.write(f"socks5://{p['username']}:{p['password']}@{p['ip']}:{p['port']}\n")
    print(f'Saved: {args.out_txt}')


if __name__ == '__main__':
    main()
