#!/usr/bin/env python3
"""
novproxy_register_api.py  v1
────────────────────────────────
Register N novproxy accounts using mail.tm disposable emails.
NO BROWSER — pure API calls (requests + ddddocr).

Flow per account:
  1. Create mail.tm inbox
  2. POST /novip  → captcha {id, img}
  3. Solve captcha with ddddocr
  4. POST /v1/mailCode {email, verificat_id, verificat, lang} → sends verification email
  5. Poll mail.tm inbox for 6-digit code
  6. POST /v1/signup {email, pwd, verificat_id, verificat, code, lang} → register

Usage:
  python3 novproxy_register_api.py --count 3 [--save-db]
"""
import sys, re, json, time, secrets, string, argparse, traceback, base64
import urllib.request, urllib.parse, urllib.error
sys.path.insert(0, '/root/Toolkit/scripts')

import requests
import ddddocr

NOVPROXY_API  = 'https://api.novproxy.com'
MAILTM_BASE   = 'https://api.mail.tm'
MAILTM_DOMAIN = 'deltajohnsons.com'
_ocr = ddddocr.DdddOcr(show_ad=False)

NOVPROXY_HEADERS = {
    'Content-Type':  'application/x-www-form-urlencoded',
    'Origin':        'https://novproxy.com',
    'Referer':       'https://novproxy.com/register/',
    'User-Agent':    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                     '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept':        'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
}


def log(msg):         print(f'[LOG]  {msg}', flush=True)
def ok(e, p, ip=''):  print(f'[OK]   {e}|{p}|{ip}', flush=True)
def fail(e, r):       print(f'[FAIL] {e}|{r}', flush=True)
def done(n, t):       print(f'[DONE] {n}/{t}', flush=True)


# ─── novproxy API helpers ─────────────────────────────────────────────────────
def novp_post(path, data, session=None):
    s = session or requests.Session()
    r = s.post(NOVPROXY_API + path, data=data, headers=NOVPROXY_HEADERS, timeout=20)
    try:
        return r.json()
    except Exception:
        return {'code': -1, 'msg': r.text[:200], 'data': {}}


def get_captcha(session):
    """POST /novip → {code, data:{id, img, ip, k}}"""
    d = novp_post('/novip', {}, session)
    if d.get('code') != 0:
        return None, None
    data = d.get('data', {})
    return data.get('id', ''), data.get('img', '')


def solve_captcha(img_b64):
    """Decode base64 image, run ddddocr, return text."""
    for pfx in ['data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/gif;base64,']:
        if img_b64.startswith(pfx):
            raw = base64.b64decode(img_b64[len(pfx):])
            return _ocr.classification(raw).strip()
    return ''


def send_mail_code(session, email):
    """POST /v1/mailCode → {code:0} on success.
    NOTE: only {email, lang} needed — captcha params cause 'Too many times' errors."""
    d = novp_post('/v1/mailCode', {
        'email': email,
        'lang':  'en',
    }, session)
    return d


def do_signup(session, email, pwd, cap_id, cap_text, mail_code):
    """POST /v1/signup → {code:0, data:{id, email, ...}} on success."""
    d = novp_post('/v1/signup', {
        'email':        email,
        'pwd':          pwd,
        'verificat_id': cap_id,
        'verificat':    cap_text,
        'code':         mail_code,
        'invitecode':   '',
        'lang':         'en',
    }, session)
    return d


# ─── mail.tm helpers ──────────────────────────────────────────────────────────
def _mailtm_req(method, path, data=None, token=None, timeout=20):
    url  = MAILTM_BASE + path
    body = json.dumps(data).encode() if data else None
    h = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as exc:
        return 0, {'error': str(exc)}


def mailtm_create(retries=6):
    chars = string.ascii_lowercase + string.digits
    for attempt in range(retries):
        login    = ''.join(secrets.choice(chars) for _ in range(16))
        address  = f'{login}@{MAILTM_DOMAIN}'
        password = 'P@' + secrets.token_hex(12)
        code, body = _mailtm_req('POST', '/accounts', {'address': address, 'password': password})
        if code in (200, 201):
            return address, password, body.get('id', '') if isinstance(body, dict) else ''
        if code == 429:
            wait = 15 * (attempt + 1)
            log(f'  [mail.tm] Rate limited (429), waiting {wait}s ...')
            time.sleep(wait)
            continue
        raise RuntimeError(f'mail.tm create failed {code}: {body}')
    raise RuntimeError('mail.tm create failed after all retries')


def mailtm_token(address, password):
    code, body = _mailtm_req('POST', '/token', {'address': address, 'password': password})
    if code != 200:
        raise RuntimeError(f'mail.tm token failed {code}: {body}')
    return body['token'] if isinstance(body, dict) else ''


def _body_msgs(body):
    if isinstance(body, list):   return body
    if isinstance(body, dict):   return body.get('hydra:member', [])
    return []


def mailtm_poll_code(token, timeout=180):
    deadline = time.time() + timeout
    log(f'  [mail.tm] Polling inbox (max {timeout}s)...')
    while time.time() < deadline:
        try:
            code, body = _mailtm_req('GET', '/messages', token=token)
            if code == 200:
                for msg in _body_msgs(body):
                    if not isinstance(msg, dict): continue
                    subj  = msg.get('subject', '').lower()
                    intro = msg.get('intro',   '').lower()
                    if ('novproxy' in subj or 'verify' in subj or 'code' in subj
                            or 'confirm' in subj or re.search(r'\d{4,8}', intro)):
                        mid = msg.get('id', '')
                        if not mid: continue
                        c2, full = _mailtm_req('GET', f'/messages/{mid}', token=token)
                        if c2 == 200 and isinstance(full, dict):
                            text = str(full.get('text', '') or '')
                            hr   = full.get('html', '')
                            html = ' '.join(str(h) for h in hr) if isinstance(hr, list) else str(hr or '')
                            codes = re.findall(r'\b(\d{4,8})\b', text + ' ' + html)
                            if codes:
                                log(f'  [mail.tm] Code found [{msg.get("subject","")[:40]}]: {codes[0]}')
                                return codes[0]
        except Exception as ex:
            log(f'  [mail.tm] Poll error: {ex}')
        time.sleep(7)
    log('  [mail.tm] Timeout — no code')
    return ''


# ─── DB save ──────────────────────────────────────────────────────────────────
def save_to_db(email, pwd, platform='novproxy', notes=''):
    try:
        import psycopg2
        conn = psycopg2.connect('postgresql://postgres:postgres@localhost/toolkit')
        cur  = conn.cursor()
        cur.execute('''
            INSERT INTO accounts (platform, email, password, notes)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (platform, email) DO UPDATE
            SET password=%s, notes=%s
        ''', (platform, email, pwd, notes, pwd, notes))
        conn.commit(); cur.close(); conn.close()
        return True
    except Exception as e:
        log(f'  DB save error: {e}')
        return False


# ─── core registration ────────────────────────────────────────────────────────
def register_one(novproxy_email, novproxy_pwd, mailtm_tok, save_db=False):
    """
    Full registration flow using pure HTTP API (no browser).
    Returns (success:bool, reason:str, token:str)
    """
    session = requests.Session()

    # ── Step 1: Get captcha ───────────────────────────────────────────────────
    cap_id, cap_img = None, None
    for attempt in range(6):
        cap_id, cap_img = get_captcha(session)
        if cap_id and cap_img:
            break
        log(f'  captcha attempt {attempt+1}: no data, retrying ...')
        time.sleep(2)
    if not cap_id:
        return False, 'captcha_get_failed', ''

    # ── Step 2: Solve captcha ────────────────────────────────────────────────
    cap_text = ''
    for attempt in range(6):
        cap_text = solve_captcha(cap_img)
        if len(cap_text) >= 3:
            log(f'  captcha solved: "{cap_text}"')
            break
        log(f'  captcha attempt {attempt+1}: too short "{cap_text}", refreshing ...')
        cap_id, cap_img = get_captcha(session)
        if not cap_id:
            return False, 'captcha_get_failed', ''
    if len(cap_text) < 3:
        return False, 'captcha_solve_failed', ''

    # ── Step 3: Send email verification code ─────────────────────────────────
    # NOTE: mailCode only accepts {email, lang} — captcha params trigger 'Too many times'
    mail_resp = send_mail_code(session, novproxy_email)
    log(f'  mailCode response: {json.dumps(mail_resp)[:120]}')
    if mail_resp.get('code') != 0:
        log(f'  WARNING: mailCode failed — code={mail_resp.get("code")} msg={mail_resp.get("msg","")}')
        # Retry a couple times with brief wait
        for retry in range(3):
            time.sleep(8)
            mail_resp = send_mail_code(session, novproxy_email)
            log(f'  mailCode retry {retry+1}: {json.dumps(mail_resp)[:80]}')
            if mail_resp.get('code') == 0:
                break
    if mail_resp.get('code') != 0:
        log(f'  WARNING: mailCode still failing — proceeding without email verification')

    # ── Step 4: Poll mail.tm for code ─────────────────────────────────────────
    email_code = mailtm_poll_code(mailtm_tok, timeout=180)
    if not email_code:
        log(f'  WARNING: no email code received — will try signup without it')

    # ── Step 5: Register ──────────────────────────────────────────────────────
    # Try with email code first; if that fails, try without (empty code)
    for code_attempt, code_val in enumerate([email_code, '']):
        if code_attempt == 1 and email_code:
            log(f'  Retrying signup without email code ...')
        signup_resp = do_signup(session, novproxy_email, novproxy_pwd, cap_id, cap_text, code_val)
        log(f'  signup[{code_attempt}] response: {json.dumps(signup_resp)[:200]}')
        sc = signup_resp.get('code')
        if sc == 0:
            # success
            data = signup_resp.get('data', {})
            user_id = data.get('id', '')
            novproxy_token = data.get('token', '')
            log(f'  REGISTERED: id={user_id} token={novproxy_token[:20] if novproxy_token else "N/A"}')
            if save_db:
                notes = f'mailtm_code={email_code or "NONE"} verified={"YES" if email_code else "NO"}'
                saved = save_to_db(novproxy_email, novproxy_pwd, notes=notes)
                log(f'  DB save: {"OK" if saved else "FAIL"}')
            return True, 'ok', novproxy_token
        msg = signup_resp.get('msg', '')
        log(f'  signup failed: code={sc} msg={msg}')
        # if captcha wrong, refresh and retry full flow is too complex; just report
        if 'captcha' in msg.lower() or 'verificat' in msg.lower():
            # captcha was wrong — refresh captcha and do one more full attempt
            log(f'  Captcha wrong on signup; refreshing captcha for final retry ...')
            cap_id, cap_img = get_captcha(session)
            if cap_id:
                cap_text = solve_captcha(cap_img)
                log(f'  New captcha: "{cap_text}"')
                signup_resp2 = do_signup(session, novproxy_email, novproxy_pwd, cap_id, cap_text, email_code or '')
                log(f'  signup retry: {json.dumps(signup_resp2)[:200]}')
                if signup_resp2.get('code') == 0:
                    data = signup_resp2.get('data', {})
                    if save_db:
                        save_to_db(novproxy_email, novproxy_pwd,
                                   notes=f'mailtm_code={email_code or "NONE"} verified={"YES" if email_code else "NO"}')
                    return True, 'ok', signup_resp2.get('data', {}).get('token', '')
            return False, f'signup_failed: {msg}', ''

    return False, f'signup_failed: {signup_resp.get("msg","")}', ''


# ─── main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--count',   type=int, default=3)
    parser.add_argument('--save-db', action='store_true')
    args = parser.parse_args()

    log(f'=== novproxy API registration (no browser): {args.count} accounts ===')

    # Create all mail.tm inboxes upfront
    log('Creating mail.tm inboxes ...')
    inboxes = []
    for i in range(args.count):
        addr, pwd, _ = mailtm_create()
        tok = mailtm_token(addr, pwd)
        inboxes.append((addr, pwd, tok))
        log(f'  Inbox {i+1}: {addr}')
        if i < args.count - 1:
            time.sleep(3)

    success_count = 0
    for i, (mt_addr, mt_pwd, mt_tok) in enumerate(inboxes):
        log(f'--- [{i+1}/{args.count}] {mt_addr} ---')
        # Generate secure novproxy password
        chars = string.ascii_letters + string.digits + '!@#$'
        nv_pwd = ''.join(secrets.choice(chars) for _ in range(14))
        nv_pwd = 'Aa1!' + nv_pwd  # ensure complexity
        try:
            succ, reason, token = register_one(mt_addr, nv_pwd, mt_tok, save_db=args.save_db)
            if succ:
                ok(mt_addr, nv_pwd, token[:20] if token else '')
                success_count += 1
            else:
                fail(mt_addr, reason)
        except Exception as ex:
            fail(mt_addr, str(ex)[:120])
            traceback.print_exc()
        # small gap between accounts
        if i < args.count - 1:
            time.sleep(5)

    done(success_count, args.count)


if __name__ == '__main__':
    main()
