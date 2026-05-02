#!/usr/bin/env python3
"""
novproxy_register_final.py  v1
────────────────────────────────
Register N novproxy accounts with 500MB trial activated.

Correct activation flow (discovered via API research):
  1. Create mail.tm inbox (SAVE password to DB)
  2. POST /novip              → captcha {id, img}
  3. Solve captcha with ddddocr
  4. POST /v1/signup {email, pwd, verificat_id, verificat, code:'', lang}  → account created
  5. POST /v1/mailCode {email, lang}  → sends verification email
  6. Poll mail.tm inbox for 6-digit code
  7. POST /v1/secureMail {token, email, code}  → email verified + 500MB ACTIVATED

Usage:
  python3 novproxy_register_final.py --count 3
"""
import sys, re, json, time, secrets, string, traceback, base64, argparse
import urllib.request, urllib.parse, urllib.error
sys.path.insert(0, '/root/Toolkit/scripts')

import requests
import ddddocr

NOVPROXY_API  = 'https://api.novproxy.com'
MAILTM_BASE   = 'https://api.mail.tm'
MAILTM_DOMAIN = 'deltajohnsons.com'
_ocr = ddddocr.DdddOcr(show_ad=False)

NP_HEADERS = {
    'Content-Type':    'application/x-www-form-urlencoded',
    'Origin':          'https://novproxy.com',
    'Referer':         'https://novproxy.com/register/',
    'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept':          'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
}
DB_HEADERS = {**NP_HEADERS, 'Origin': 'https://dash.novproxy.com', 'Referer': 'https://dash.novproxy.com/'}


def log(msg):         print(f'[LOG]  {msg}', flush=True)
def ok(e, p, t=''):   print(f'[OK]   {e}|{p}|traffic={t}', flush=True)
def fail(e, r):        print(f'[FAIL] {e}|{r}', flush=True)
def done(n, t):        print(f'[DONE] {n}/{t}', flush=True)


# ─── novproxy API ─────────────────────────────────────────────────────────────
def np_post(path, data, session=None, hdrs=None):
    s = session or requests.Session()
    h = hdrs or NP_HEADERS
    try:
        r = s.post(NOVPROXY_API + path, data=data, headers=h, timeout=20)
        return r.json()
    except Exception as e:
        return {'code': -1, 'msg': str(e), 'data': {}}


def get_captcha(session):
    d = np_post('/novip', {}, session)
    if d.get('code') != 0:
        return None, None
    data = d.get('data', {})
    return data.get('id', ''), data.get('img', '')


def solve_captcha_b64(img_b64):
    for pfx in ['data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/gif;base64,']:
        if img_b64.startswith(pfx):
            return _ocr.classification(base64.b64decode(img_b64[len(pfx):])).strip()
    return ''


def get_token(email, pwd):
    d = np_post('/v1/signin', {'email': email, 'pwd': pwd, 'lang': 'en'}, hdrs=DB_HEADERS)
    return (d.get('data') or {}).get('token', '')


def check_traffic(token):
    d = np_post('/v1/trafficInfo', {'token': token}, hdrs=DB_HEADERS)
    data = d.get('data', {})
    return data.get('alltraffic', 0), data.get('traffic', 0)


# ─── mail.tm ──────────────────────────────────────────────────────────────────
def _mr(method, path, data=None, token=None):
    url  = MAILTM_BASE + path
    body = json.dumps(data).encode() if data else None
    h = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as ex:
        return 0, {'error': str(ex)}


def mailtm_create(retries=8):
    chars = string.ascii_lowercase + string.digits
    for attempt in range(retries):
        login    = ''.join(secrets.choice(chars) for _ in range(16))
        address  = f'{login}@{MAILTM_DOMAIN}'
        password = 'P@' + secrets.token_hex(12)
        code, body = _mr('POST', '/accounts', {'address': address, 'password': password})
        if code in (200, 201):
            return address, password
        if code == 429:
            wait = 20 * (attempt + 1)
            log(f'  [mail.tm] 429, waiting {wait}s ...')
            time.sleep(wait)
            continue
        log(f'  [mail.tm] create error {code}: {body}')
        time.sleep(5)
    raise RuntimeError('mail.tm create failed after all retries')


def mailtm_token(address, password):
    code, body = _mr('POST', '/token', {'address': address, 'password': password})
    if code != 200:
        raise RuntimeError(f'mail.tm token failed {code}: {body}')
    return body.get('token', '') if isinstance(body, dict) else ''


def mailtm_poll_code(token, timeout=210):
    deadline = time.time() + timeout
    log(f'  [mail.tm] Polling inbox (max {timeout}s)...')
    seen = set()
    while time.time() < deadline:
        try:
            code, body = _mr('GET', '/messages', token=token)
            if code == 200:
                msgs = body.get('hydra:member', []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
                for msg in msgs:
                    if not isinstance(msg, dict): continue
                    mid = msg.get('id', '')
                    if not mid or mid in seen: continue
                    seen.add(mid)
                    c2, full = _mr('GET', f'/messages/{mid}', token=token)
                    if c2 == 200 and isinstance(full, dict):
                        text = str(full.get('text', '') or '')
                        hr   = full.get('html', '')
                        html = ' '.join(str(h) for h in hr) if isinstance(hr, list) else str(hr or '')
                        subj = msg.get('subject', '')
                        log(f'  [mail.tm] Email: "{subj[:40]}"')
                        codes = re.findall(r'\b(\d{4,8})\b', text + ' ' + html)
                        if codes:
                            log(f'  [mail.tm] Code found: {codes[0]}')
                            return codes[0]
        except Exception as ex:
            log(f'  [mail.tm] Poll error: {ex}')
        time.sleep(7)
    log('  [mail.tm] Timeout — no code')
    return ''


# ─── DB ───────────────────────────────────────────────────────────────────────
def save_to_db(novproxy_email, novproxy_pwd, mailtm_addr, mailtm_pwd, mailtm_code, traffic_mb):
    try:
        import psycopg2
        conn = psycopg2.connect('postgresql://postgres:postgres@localhost/toolkit')
        cur  = conn.cursor()
        notes = (f'mailtm_addr={mailtm_addr} mailtm_pwd={mailtm_pwd} '
                 f'code={mailtm_code or "NONE"} '
                 f'verified={"YES" if mailtm_code else "NO"} '
                 f'traffic_mb={traffic_mb}')
        cur.execute('''
            INSERT INTO accounts (platform, email, password, notes)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (platform, email) DO UPDATE
            SET password=%s, notes=%s
        ''', ('novproxy', novproxy_email, novproxy_pwd, notes, novproxy_pwd, notes))
        conn.commit(); cur.close(); conn.close()
        return True
    except Exception as e:
        log(f'  DB error: {e}')
        return False


# ─── core ─────────────────────────────────────────────────────────────────────
def register_one(idx, total, mailtm_addr, mailtm_pwd, mt_tok):
    log(f'--- [{idx}/{total}] {mailtm_addr} ---')
    s = requests.Session()

    # Generate novproxy password
    chars = string.ascii_letters + string.digits
    nv_pwd = 'Aa1!' + ''.join(secrets.choice(chars) for _ in range(14))

    # ── Captcha ───────────────────────────────────────────────────────────────
    cap_id, cap_text = '', ''
    for attempt in range(8):
        cid, cimg = get_captcha(s)
        if not cid:
            time.sleep(2); continue
        ctext = solve_captcha_b64(cimg)
        log(f'  captcha attempt {attempt+1}: "{ctext}"')
        if len(ctext) >= 3:
            cap_id, cap_text = cid, ctext
            break
        time.sleep(1)
    if not cap_id:
        return False, 'captcha_failed'

    # ── Signup (creates bare account, traffic=0) ───────────────────────────────
    signup_resp = np_post('/v1/signup', {
        'email':        mailtm_addr,
        'pwd':          nv_pwd,
        'verificat_id': cap_id,
        'verificat':    cap_text,
        'code':         '',
        'invitecode':   '',
        'lang':         'en',
    }, s)
    log(f'  signup: code={signup_resp.get("code")} msg={signup_resp.get("msg","")}')
    if signup_resp.get('code') not in (0, 2):  # code=2 might mean "already exists"
        # If FrequentOperations, wait and retry
        if signup_resp.get('code') == 500:
            log(f'  signup rate-limited, waiting 30s ...')
            time.sleep(30)
            signup_resp = np_post('/v1/signup', {
                'email': mailtm_addr, 'pwd': nv_pwd,
                'verificat_id': cap_id, 'verificat': cap_text,
                'code': '', 'invitecode': '', 'lang': 'en',
            }, s)
            log(f'  signup retry: code={signup_resp.get("code")} msg={signup_resp.get("msg","")}')
        if signup_resp.get('code') not in (0, 2):
            return False, f'signup_failed: {signup_resp.get("msg","")}'

    # Get auth token
    nv_token = get_token(mailtm_addr, nv_pwd)
    if not nv_token:
        return False, 'login_failed_after_signup'
    log(f'  novproxy token: {nv_token[:20]}')

    # ── Request email verification code ──────────────────────────────────────
    mc_resp = np_post('/v1/mailCode', {'email': mailtm_addr, 'lang': 'en'}, s)
    log(f'  mailCode: {json.dumps(mc_resp)[:80]}')
    if mc_resp.get('code') != 0:
        for retry in range(5):
            wait = 15 * (retry + 1)
            log(f'  mailCode rate-limited, waiting {wait}s ...')
            time.sleep(wait)
            mc_resp = np_post('/v1/mailCode', {'email': mailtm_addr, 'lang': 'en'}, s)
            log(f'  mailCode retry {retry+1}: {json.dumps(mc_resp)[:60]}')
            if mc_resp.get('code') == 0:
                break

    # ── Poll mail.tm for code ────────────────────────────────────────────────
    email_code = mailtm_poll_code(mt_tok, timeout=210)

    # ── Activate 500MB via secureMail ────────────────────────────────────────
    traffic_mb = 0
    if email_code:
        sm_resp = np_post('/v1/secureMail', {
            'token': nv_token,
            'email': mailtm_addr,
            'code':  email_code,
        }, s, hdrs=DB_HEADERS)
        log(f'  secureMail: {json.dumps(sm_resp)[:120]}')
        if sm_resp.get('code') == 0:
            log(f'  Email verified via secureMail!')
            # Check traffic now
            time.sleep(2)
            all_t, rem_t = check_traffic(nv_token)
            traffic_mb = (all_t or 0) // (1024 * 1024)
            log(f'  Traffic: alltraffic={all_t} ({traffic_mb}MB) remaining={rem_t}')
        else:
            log(f'  secureMail failed: {sm_resp.get("msg","")}')
    else:
        log(f'  WARNING: no email code — 500MB NOT activated')

    # Save to DB
    save_to_db(mailtm_addr, nv_pwd, mailtm_addr, mailtm_pwd, email_code, traffic_mb)
    log(f'  DB saved: {mailtm_addr}')

    ok(mailtm_addr, nv_pwd, f'{traffic_mb}MB')
    return True, f'traffic={traffic_mb}MB'


# ─── main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, default=3)
    args = parser.parse_args()

    log(f'=== novproxy FINAL registration (secureMail flow): {args.count} accounts ===')

    # Create all mail.tm inboxes upfront (with saved credentials)
    log('Creating mail.tm inboxes ...')
    inboxes = []
    for i in range(args.count):
        addr, mt_pwd = mailtm_create()
        mt_tok = mailtm_token(addr, mt_pwd)
        inboxes.append((addr, mt_pwd, mt_tok))
        log(f'  Inbox {i+1}: {addr} (pwd saved)')
        if i < args.count - 1:
            time.sleep(4)

    success_count = 0
    for i, (addr, mt_pwd, mt_tok) in enumerate(inboxes):
        try:
            succ, reason = register_one(i + 1, args.count, addr, mt_pwd, mt_tok)
            if succ:
                success_count += 1
        except Exception as ex:
            fail(addr, str(ex)[:120])
            traceback.print_exc()
        if i < args.count - 1:
            log(f'  Waiting 10s before next account ...')
            time.sleep(10)

    done(success_count, args.count)


if __name__ == '__main__':
    main()
