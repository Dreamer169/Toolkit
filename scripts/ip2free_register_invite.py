#!/usr/bin/env python3
"""
ip2free_register_invite.py
==========================
Automatically register new ip2free accounts using existing accounts' invite codes.

Rules:
  - Each invite link registers exactly 3 new accounts
  - Use NON-ip2free proxies for registration (local_xray or proxyscrape)
  - Maintain IP consistency throughout each registration session
  - Uses mail.tm (wshu.net domain) for disposable email inboxes
  - After registration, pulls proxies from new account and adds to proxy_manager DB

Usage:
  python3 ip2free_register_invite.py --accounts 3    # register 3 new accounts
  python3 ip2free_register_invite.py --invite I3qD20OQyg --accounts 3
  python3 ip2free_register_invite.py --status        # show invite usage status
"""

import sys, os, json, time, re, random, string, argparse, logging, datetime
import requests, urllib3
urllib3.disable_warnings()

sys.path.insert(0, os.path.dirname(__file__))
from proxy_manager import ProxyManager, ProxyEntry, EXCLUSION_RULES

# ── Config ──────────────────────────────────────────────────────────────────
BASE_IP2FREE  = 'https://api.ip2free.com'
BASE_MAILTM   = 'https://api.mail.tm'
MAILTM_DOMAIN = 'wshu.net'   # mail.tm managed domain
REG_PASSWORD  = 'Reg2026@Secure!'

INVITE_STATE_FILE = '/data/ip2free_invite_state.json'  # tracks usage per invite code
NEW_ACCOUNTS_FILE = '/data/ip2free_new_accounts.json'  # registered account credentials

IP2FREE_HEADERS = {
    'User-Agent':   'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.6778.85 Safari/537.36',
    'Content-Type': 'text/plain;charset=UTF-8',
    'Origin':       'https://www.ip2free.com',
    'Referer':      'https://www.ip2free.com/',
    'lang':         'cn', 'domain': 'www.ip2free.com', 'webname': 'IP2FREE',
    'affid': '', 'invitecode': '', 'serviceid': '',
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-7s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger('ip2free_reg')

# ── Invite state ─────────────────────────────────────────────────────────────
def load_state() -> dict:
    try:
        return json.loads(open(INVITE_STATE_FILE).read())
    except Exception:
        return {}

def save_state(state: dict):
    open(INVITE_STATE_FILE, 'w').write(json.dumps(state, indent=2, ensure_ascii=False))

def load_new_accounts() -> list:
    try:
        return json.loads(open(NEW_ACCOUNTS_FILE).read())
    except Exception:
        return []

def save_new_accounts(accounts: list):
    open(NEW_ACCOUNTS_FILE, 'w').write(json.dumps(accounts, indent=2, ensure_ascii=False))

# ── Proxy picker (non-ip2free) ────────────────────────────────────────────────
def get_registration_proxy(pm: ProxyManager) -> dict:
    """Pick a non-ip2free proxy for registration. Returns requests proxies dict."""
    entry = pm.pick(not_for='ip2free', probe_if_unknown=True)
    if entry:
        url = entry.socks5h_url
        log.info(f'  Registration proxy: {url.split("@")[-1]} (source={entry.source})')
        return {'http': url, 'https': url}, entry
    log.warning('  No non-ip2free proxy available; using direct connection')
    return {}, None

# ── mail.tm helpers ───────────────────────────────────────────────────────────
def create_mailtm_inbox(prefix: str = None) -> tuple[str, str]:
    """Create a new mail.tm inbox. Returns (address, token)."""
    if not prefix:
        prefix = 'ip2r_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    address = f'{prefix}@{MAILTM_DOMAIN}'
    password = 'MailTm2026!' + ''.join(random.choices(string.digits, k=4))
    r = requests.post(f'{BASE_MAILTM}/accounts',
                      json={'address': address, 'password': password}, timeout=12)
    if r.status_code not in (200, 201):
        raise RuntimeError(f'mail.tm create failed: {r.status_code} {r.text[:100]}')
    rt = requests.post(f'{BASE_MAILTM}/token',
                       json={'address': address, 'password': password}, timeout=12)
    token = rt.json().get('token')
    if not token:
        raise RuntimeError(f'mail.tm token failed: {rt.text[:100]}')
    return address, token

def wait_for_code(mt_token: str, timeout: int = 90, poll: int = 5) -> str:
    """Poll mail.tm inbox for a 6-digit verification code. Returns the code."""
    headers = {'Authorization': f'Bearer {mt_token}'}
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f'{BASE_MAILTM}/messages', headers=headers, timeout=10)
        msgs = r.json().get('hydra:member', [])
        for m in msgs:
            rid = m.get('id', '')
            rb = requests.get(f'{BASE_MAILTM}/messages/{rid}', headers=headers, timeout=10)
            body = rb.json().get('text', '') or rb.json().get('html', '')
            codes = re.findall(r'\b(\d{6})\b', body)
            if codes:
                return codes[0]
        log.debug(f'  Waiting for email... ({int(deadline - time.time())}s left)')
        time.sleep(poll)
    raise TimeoutError('Verification code not received within timeout')

# ── ip2free account helpers ───────────────────────────────────────────────────
def ip2free_login(email: str, password: str, proxies: dict = None) -> tuple[requests.Session, str]:
    """Login to ip2free. Returns (session, token)."""
    s = requests.Session()
    s.verify = False
    s.headers.update(IP2FREE_HEADERS)
    if proxies:
        s.proxies.update(proxies)
    r = s.post(f'{BASE_IP2FREE}/api/account/login?',
               data=json.dumps({'email': email, 'password': password}), timeout=15)
    d = r.json()
    tok = (d.get('data') or {}).get('token')
    if not tok:
        raise RuntimeError(f'ip2free login failed: {d.get("msg","?")}')
    s.headers['x-token'] = tok
    return s, tok

def ip2free_get_invite_code(email: str, password: str) -> str:
    """Get invite code for an account."""
    s, _ = ip2free_login(email, password)
    r = s.post(f'{BASE_IP2FREE}/api/account/login?',
               data=json.dumps({'email': email, 'password': password}), timeout=15)
    d = r.json()
    return d.get('data', {}).get('profile', {}).get('invite_code', '')

def ip2free_send_register_code(email: str, proxies: dict = None) -> bool:
    """Send registration verification code to email. Returns True on success."""
    s = requests.Session()
    s.verify = False
    s.headers.update(IP2FREE_HEADERS)
    if proxies:
        s.proxies.update(proxies)
    r = s.post(f'{BASE_IP2FREE}/api/account/getRegisterCode?',
               data=json.dumps({'email': email}), timeout=15)
    d = r.json()
    ok = d.get('code') in (0, 200) or '成功' in d.get('msg', '')
    if not ok:
        log.warning(f'  sendCode failed: {d.get("msg","?")} (code={d.get("code")})')
    return ok

def ip2free_register(email: str, password: str, code: str, invite_code: str,
                     proxies: dict = None) -> dict:
    """Register new ip2free account. Returns account dict with token."""
    s = requests.Session()
    s.verify = False
    s.headers.update(IP2FREE_HEADERS)
    if proxies:
        s.proxies.update(proxies)
    payload = {
        'email': email, 'password': password, 'code': code,
        'affId': invite_code, 'ga_client_id': '', 'url_query_raw': ''
    }
    r = s.post(f'{BASE_IP2FREE}/api/account/register?',
               data=json.dumps(payload), timeout=15)
    d = r.json()
    if d.get('code') not in (0, 200) and 'success' not in d.get('msg', '').lower():
        raise RuntimeError(f'Registration failed: {d.get("msg","?")} code={d.get("code")}')
    token = (d.get('data') or {}).get('token', '')
    return {'email': email, 'password': password, 'token': token}

def ip2free_get_proxies(email: str, password: str, token: str = None) -> list:
    """Get proxy list for an account."""
    s = requests.Session()
    s.verify = False
    s.headers.update(IP2FREE_HEADERS)
    if not token:
        _, token = ip2free_login(email, password)
        s.headers['x-token'] = token
    else:
        s.headers['x-token'] = token
    r = s.post(f'{BASE_IP2FREE}/api/ip/freeList?',
               data=json.dumps({'size': 200}), timeout=15)
    return (r.json().get('data') or {}).get('free_ip_list') or []

# ── Core registration flow ────────────────────────────────────────────────────
def register_one_account(invite_code: str, pm: ProxyManager,
                          retry: int = 2) -> dict | None:
    """
    Register one new ip2free account using invite_code.
    Maintains IP consistency: same proxy for sendCode + register steps.
    Returns account dict {email, password, token, proxies} or None on failure.
    """
    proxies, proxy_entry = get_registration_proxy(pm)

    for attempt in range(1, retry + 2):
        try:
            # 1. Create temporary email inbox
            log.info(f'  Attempt {attempt}: creating temp inbox...')
            email, mt_token = create_mailtm_inbox()
            log.info(f'  Inbox: {email}')

            # 2. Send verification code (SAME proxy = IP consistency)
            log.info(f'  Sending verification code...')
            if not ip2free_send_register_code(email, proxies):
                raise RuntimeError('sendCode returned failure')

            # 3. Wait for code in inbox
            log.info(f'  Waiting for verification email...')
            code = wait_for_code(mt_token, timeout=90)
            log.info(f'  Code received: {code}')

            # 4. Register (SAME proxy session = same IP)
            log.info(f'  Registering with invite={invite_code}...')
            acct = ip2free_register(email, REG_PASSWORD, code, invite_code, proxies)
            log.info(f'  Registration successful: {email}')

            # 5. Immediately pull proxies for new account
            time.sleep(2)
            proxy_list = ip2free_get_proxies(email, REG_PASSWORD, acct.get('token'))
            log.info(f'  Got {len(proxy_list)} proxies for new account')

            # 6. Report proxy used for registration (success feedback)
            if proxy_entry:
                pm.report_success(proxy_entry.uid)

            return {
                'email': email, 'password': REG_PASSWORD,
                'token': acct.get('token', ''),
                'invite_code_used': invite_code,
                'registered_at': datetime.datetime.now().isoformat(),
                'proxies': proxy_list,
            }

        except Exception as e:
            log.warning(f'  Attempt {attempt} failed: {e}')
            if proxy_entry:
                pm.report_failure(proxy_entry.uid)
            if attempt <= retry:
                # Retry with a different proxy
                proxies, proxy_entry = get_registration_proxy(pm)
                time.sleep(3)
            else:
                log.error(f'  All {retry+1} attempts exhausted')
                return None

def ingest_new_account(acct: dict, pm: ProxyManager) -> int:
    """Add new account's proxies to the proxy_manager DB. Returns count added."""
    added = 0
    for p in acct.get('proxies', []):
        raw_uid = p.get('proxy_uid') or f"{p.get('ip')}:{p.get('port')}"
        uid = f"ip2free:{raw_uid}"
        if not pm.db.get(uid):
            pm.db.put(ProxyEntry(
                uid=uid,
                proto=p.get('protocol', 'socks5'),
                host=p.get('ip', ''), port=int(p.get('port', 0)),
                user=p.get('username', ''), passwd=p.get('password', ''),
                source='ip2free', source_account=acct['email'],
                country=p.get('country_code', ''), city=p.get('city', ''),
                proxy_type='residential',
                not_for=list(EXCLUSION_RULES['ip2free']),
                meta={'proxy_uid': str(raw_uid),
                      'is_new': p.get('is_new', 0),
                      'status': p.get('status', 1),
                      'last_checked_at': p.get('last_checked_at', '')},
            ), save=False)
            added += 1
    if added:
        pm.db._save()
    return added

# ── Main ──────────────────────────────────────────────────────────────────────
def get_invite_codes_from_all_accounts(pm: ProxyManager) -> dict:
    """Login to all known accounts and collect invite codes. Returns {invite_code: email}."""
    accounts = pm._ip2free_accounts
    result = {}
    for acct in accounts:
        try:
            s = requests.Session(); s.verify = False; s.headers.update(IP2FREE_HEADERS)
            r = s.post(f'{BASE_IP2FREE}/api/account/login?',
                       data=json.dumps({'email': acct['email'], 'password': acct['password']}),
                       timeout=12)
            d = r.json()
            if d.get('data') and d['data'].get('token'):
                ic = d['data'].get('profile', {}).get('invite_code', '')
                if ic:
                    result[ic] = acct['email']
        except Exception as e:
            log.debug(f'  {acct["email"]}: {e}')
    # also check new accounts
    for na in load_new_accounts():
        try:
            s = requests.Session(); s.verify = False; s.headers.update(IP2FREE_HEADERS)
            r = s.post(f'{BASE_IP2FREE}/api/account/login?',
                       data=json.dumps({'email': na['email'], 'password': na['password']}),
                       timeout=12)
            d = r.json()
            if d.get('data') and d['data'].get('token'):
                ic = d['data'].get('profile', {}).get('invite_code', '')
                if ic:
                    result[ic] = na['email']
        except Exception:
            pass
    return result

def cmd_status():
    state = load_state()
    new_accts = load_new_accounts()
    print(f'\nip2free invite registration status')
    print(f'  New accounts registered: {len(new_accts)}')
    print(f'  Invite codes tracked:    {len(state)}')
    print()
    if state:
        print(f'  {"Invite Code":<14} {"Owner Email":<40} {"Used":>4}')
        print('  ' + '-'*62)
        for ic, info in sorted(state.items(), key=lambda x: -x[1].get('used',0)):
            print(f'  {ic:<14} {info.get("owner","?"):<40} {info.get("used",0):>4}/3')
    if new_accts:
        print()
        print('  Recent registrations:')
        for a in new_accts[-5:]:
            ts = a.get('registered_at','?')[:16]
            n_proxies = len(a.get('proxies',[]))
            print(f'    {a["email"]:<40} inv={a.get("invite_code_used","?")}  '
                  f'proxies={n_proxies}  at={ts}')

def cmd_register(n_accounts: int, invite_code: str = None, dry_run: bool = False):
    pm = ProxyManager()
    state = load_state()
    new_accts = load_new_accounts()

    # get available invite codes
    log.info('Fetching invite codes from all accounts...')
    invite_map = get_invite_codes_from_all_accounts(pm)
    log.info(f'  Found {len(invite_map)} invite codes: {list(invite_map.keys())}')

    # update state with owner info
    for ic, owner in invite_map.items():
        if ic not in state:
            state[ic] = {'owner': owner, 'used': 0, 'accounts': []}

    registered = 0
    for _ in range(n_accounts):
        # pick invite code: prefer specified, else least-used with < 3 uses
        if invite_code and invite_code in state and state[invite_code].get('used', 0) < 3:
            chosen_ic = invite_code
        else:
            available = [(ic, info) for ic, info in state.items() if info.get('used', 0) < 3]
            if not available:
                log.error('No invite codes with remaining capacity (< 3 uses each)')
                break
            chosen_ic = sorted(available, key=lambda x: x[1].get('used', 0))[0][0]

        owner = state[chosen_ic].get('owner', '?')
        log.info(f'Registering account {registered+1}/{n_accounts} '
                 f'using invite={chosen_ic} (from {owner}, '
                 f'used={state[chosen_ic].get("used",0)}/3)')

        if dry_run:
            log.info('  [DRY RUN] skipping actual registration')
            registered += 1
            continue

        acct = register_one_account(chosen_ic, pm)
        if acct:
            state[chosen_ic]['used'] = state[chosen_ic].get('used', 0) + 1
            state[chosen_ic].setdefault('accounts', []).append(acct['email'])
            new_accts.append(acct)
            # ingest proxies into PM DB
            n_added = ingest_new_account(acct, pm)
            log.info(f'  Ingested {n_added} new proxies into DB')
            registered += 1
            save_state(state)
            save_new_accounts(new_accts)
            # add new account to ip2free_accounts in proxy_manager for future refreshes
            _append_to_pm_accounts(acct['email'], acct['password'])
        else:
            log.error(f'  Registration failed for invite={chosen_ic}')
        time.sleep(5)  # polite delay between registrations

    log.info(f'Done: {registered}/{n_accounts} accounts registered')
    pm.print_status()

def _append_to_pm_accounts(email: str, password: str):
    """Add new account to proxy_manager.py IP2FREE_ACCOUNTS_DEFAULT list."""
    src = os.path.join(os.path.dirname(__file__), 'proxy_manager.py')
    code = open(src).read()
    new_entry = f'    {{"email": "{email}",  "password": "{password}"}},\n'
    # Insert before the closing bracket of IP2FREE_ACCOUNTS_DEFAULT
    marker = ']\n\nPROXYSCRAPE_URLS'
    if new_entry not in code and marker in code:
        code = code.replace(marker, new_entry + marker)
        open(src, 'w').write(code)
        log.info(f'  Added {email} to proxy_manager IP2FREE_ACCOUNTS_DEFAULT')

def main():
    ap = argparse.ArgumentParser(description='ip2free invite-based account registration')
    sub = ap.add_subparsers(dest='cmd')

    p_reg = sub.add_parser('register', help='Register new accounts')
    p_reg.add_argument('--accounts', type=int, default=3, help='Number of accounts to register (default 3)')
    p_reg.add_argument('--invite', type=str, default=None, help='Specific invite code to use')
    p_reg.add_argument('--dry-run', action='store_true', help='Simulate without actually registering')

    sub.add_parser('status', help='Show invite usage status')

    p_refresh = sub.add_parser('refresh-invites', help='Refresh invite code list from all accounts')

    args = ap.parse_args()
    if args.cmd == 'register':
        cmd_register(args.accounts, args.invite, args.dry_run)
    elif args.cmd == 'status':
        cmd_status()
    elif args.cmd == 'refresh-invites':
        pm = ProxyManager()
        inv = get_invite_codes_from_all_accounts(pm)
        print(f'Found {len(inv)} invite codes:')
        for ic, owner in inv.items():
            print(f'  {ic}  <- {owner}')
    else:
        ap.print_help()

if __name__ == '__main__':
    main()
