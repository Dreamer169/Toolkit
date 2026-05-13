#!/usr/bin/env python3
"""
unitool_verify_rescue.py — 专门处理 unitool_verify_pending 账号
流程: 选pending账号 → 锁定 → 刷新Graph token → 查JunkEmail+Inbox+$search(360s) → curl点击验证 → unitool_login.py登录拿ssid
持续循环运行，无账号时sleep 60s
"""
import atexit, glob, json, os, re, signal, subprocess, sys, time
import urllib.parse, urllib.request
import imaplib, email as _email_lib
import psycopg2

LOG      = "/tmp/unitool_verify_rescue.log"
DB_URL   = "postgresql://postgres:postgres@localhost/toolkit"
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
LOGIN_SCRIPT = "/data/Toolkit/scripts/unitool_login.py"

_account_id   = None
_success_flag = False

def log(msg):
    ts   = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f: f.write(line + "\n")

def _atexit_handler():
    if not _account_id or _success_flag:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("SELECT tags FROM accounts WHERE id=%s", (_account_id,))
        row  = cur.fetchone(); tags = row[0] if row and row[0] else ""
        if "unitool_registered" not in tags:
            new_tags = re.sub(r",?unitool_processing", "", tags).strip(",")
            cur.execute("UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s",
                        (new_tags, _account_id))
            conn.commit()
            log(f"[atexit] id={_account_id} unlocked → {new_tags}")
        conn.close()
    except Exception as e:
        log(f"[atexit] err: {e}")

atexit.register(_atexit_handler)

# SIGTERM → sys.exit(0) so atexit fires cleanly when pm2 stops/restarts
def _sigterm_handler(signum, frame):
    log("[signal] SIGTERM received — exiting cleanly")
    sys.exit(0)
signal.signal(signal.SIGTERM, _sigterm_handler)
signal.signal(signal.SIGINT,  _sigterm_handler)  # PM2默认发 SIGINT

# ── DB ────────────────────────────────────────────────────────────────────────
def db_connect():
    return psycopg2.connect(DB_URL)

def get_pending_account():
    """选一个 unitool_verify_pending 且未locked/registered 的账号"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("""
        SELECT id, email, password, refresh_token FROM accounts
        WHERE platform='outlook' AND status='active'
          AND refresh_token IS NOT NULL AND refresh_token != ''
          AND LENGTH(COALESCE(password,'')) >= 8
          AND tags LIKE '%unitool_verify_pending%'
          AND tags NOT LIKE '%unitool_registered%'
          AND tags NOT LIKE '%unitool_processing%'
          AND tags NOT LIKE '%unitool_rescue_dead%'
          AND tags NOT LIKE '%abuse_mode%'
          AND updated_at < NOW() - INTERVAL '2 minutes'
          AND (
            notes IS NULL
            OR notes NOT LIKE '%rescue_fail_at=%'
            OR updated_at < NOW() - INTERVAL '5 minutes'
          )
        ORDER BY updated_at ASC NULLS LAST
        LIMIT 1
    """)
    row = cur.fetchone(); conn.close()
    return row

def get_relogin_account():
    """v5.14: 选一个 ref_activated 但 SSID 失效/缺失的账号直接重登
    跳过邮件验证流程，直接调用 unitool_login.py 拿新 SSID。"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("""
        SELECT a.id, a.email, a.password
        FROM accounts a
        WHERE a.platform = 'outlook'
          AND a.tags LIKE '%unitool_registered%'
          AND LENGTH(COALESCE(a.password,'')) >= 8
          AND a.tags NOT LIKE '%unitool_processing%'
          AND a.tags NOT LIKE '%balance_exhausted%'
          AND a.tags NOT LIKE '%unitool_rescue_dead%'
          AND a.tags NOT LIKE '%abuse_mode%'
          AND a.tags NOT LIKE '%unitool_already%'
          AND (a.updated_at IS NULL OR a.updated_at < NOW() - INTERVAL '5 minutes')
          AND NOT EXISTS (
            SELECT 1 FROM unitool_ssids s
            WHERE s.source_email = a.email
              AND s.is_valid = true
              AND s.ssid IS NOT NULL
              AND s.ssid != ''
          )
        ORDER BY a.updated_at ASC NULLS LAST
        LIMIT 1
    """)
    row = cur.fetchone(); conn.close()
    return row  # (id, email, password) or None


def mark_tag(account_id, tag):
    conn = db_connect(); cur = conn.cursor()
    cur.execute("SELECT tags FROM accounts WHERE id=%s", (account_id,))
    r = cur.fetchone(); tags = r[0] if r and r[0] else ""
    if tag not in tags:
        new_tags = (tags + "," + tag).strip(",")
        cur.execute("UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s",
                    (new_tags, account_id))
        conn.commit()
        log(f"[DB] id={account_id} → {new_tags}")
    conn.close()

def mark_rescue_fail(account_id):
    """rescue失败：移除processing，保留verify_pending（下次继续重试）；3次失败后才标rescue_dead"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("SELECT tags, notes FROM accounts WHERE id=%s", (account_id,))
    row = cur.fetchone()
    tags  = row[0] if row and row[0] else ""
    notes = row[1] if row and row[1] else ""
    new_tags = re.sub(r",?unitool_processing", "", tags).strip(",")
    # Always clean transient noise tags (they have no place in rescue flow)
    for _noise in ("unitool_fail", "unitool_reg_retry"):
        new_tags = re.sub(r",?" + _noise, "", new_tags).strip(",")
    # Count previous rescue attempts
    rescue_attempts = notes.count('rescue_fail_at=')
    note_line = '\nrescue_fail_at=' + time.strftime('%Y-%m-%d %H:%M:%S')
    if rescue_attempts >= 2:
        new_tags = re.sub(r",?unitool_verify_pending", "", new_tags).strip(",")
        if "unitool_rescue_dead" not in new_tags:
            new_tags = (new_tags + ",unitool_rescue_dead").strip(",")
        log(f"[DB] id={account_id} {rescue_attempts+1} attempts → rescue_dead")
    else:
        log(f"[DB] id={account_id} rescue attempt {rescue_attempts+1}/3, will retry")
    cur.execute("UPDATE accounts SET tags=%s, notes=COALESCE(notes,'') || %s, updated_at=NOW() WHERE id=%s",
                (new_tags, note_line, account_id))
    conn.commit(); conn.close()
    log(f"[DB] id={account_id} rescue_fail → {new_tags}")

def save_ssid(account_id, email, ssid):
    conn = db_connect(); cur = conn.cursor()
    # Fix-8a: add ssid_ok tag; strip all transient tags
    cur.execute("""
        UPDATE accounts SET
          tags  = TRIM(BOTH ',' FROM regexp_replace(
                    COALESCE(tags,'') || ',ssid_ok,unitool_registered',
                    ',?unitool_(processing|fail|verify_pending|reg_retry)', '', 'g')),
          notes = COALESCE(notes,'') || E'\nunitool_ssid=' || %s || E'\nat=' || %s,
          updated_at = NOW()
        WHERE id=%s
    """, (ssid, time.strftime("%Y-%m-%d %H:%M:%S"), account_id))
    # Fix-8b: insert into unitool_ssids with source_account_id
    try:
        cur.execute("""
            INSERT INTO unitool_ssids (source_account_id, source_email, ssid, collected_at, is_valid)
            VALUES (%s, %s, %s, NOW(), TRUE)
        """, (account_id, email, ssid))
    except Exception as _ei:
        log(f"[DB] unitool_ssids insert warn: {_ei}")
    conn.commit(); conn.close()
    log(f"[DB] ssid saved {email} id={account_id} len={len(ssid)}")
    # v5.15b: write to /data/unitool_ssids/<email>.txt
    SSID_DIR_VR = "/data/unitool_ssids"
    try:
        os.makedirs(SSID_DIR_VR, exist_ok=True)
        import re as _re_vr
        _safe_email = _re_vr.sub(r"[^a-zA-Z0-9@._-]", "_", email)
        fname = os.path.join(SSID_DIR_VR, _safe_email + ".txt")
        open(fname, "w").write(ssid)
        log(f"[proxy] wrote {fname}")
    except Exception as e:
        log(f"[proxy] warn: {e}")
    # Fix-8c: hotpush to proxy pool immediately (PROXY_PORT=8089)
    try:
        _data = json.dumps({"ssid": ssid, "label": email}).encode()
        _req  = urllib.request.Request(
            "http://localhost:8089/add-ssid", data=_data,
            headers={"Content-Type": "application/json"})
        _resp = json.loads(urllib.request.urlopen(_req, timeout=5).read())
        log(f"[proxy] hotpush OK pool_size={_resp.get('pool_size','?')}")
    except Exception as _ep:
        log(f"[proxy] hotpush warn: {_ep}")
    print(f"[OK] {email} | {ssid}", flush=True)

# ── Graph API ─────────────────────────────────────────────────────────────────
def refresh_ms_token(refresh_token):
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token", "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
        "scope": "https://graph.microsoft.com/Mail.Read offline_access",
    }).encode()
    r = urllib.request.urlopen(urllib.request.Request(
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        data=data, method="POST"), timeout=20)
    return json.loads(r.read())

def find_verify_link(access_token, max_msgs=30):
    """在 JunkEmail+Inbox+Clutter+DeletedItems 找unitool验证邮件，$search 兜底"""
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    # v8b: also match replit.com/action-code (verify@replit.com sends this format)
    pattern = re.compile(
        r"https://(?:(?:[a-z0-9-]+\.)?unitool\.ai|replit\.com/action-code)[^\s\"\'<>]+",
        re.IGNORECASE)
    # also match html-unescaped body
    import html as _html_mod
    for folder in ["JunkEmail", "Inbox", "Clutter", "DeletedItems"]:
        url = (f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
               f"?$top={max_msgs}&$orderby=receivedDateTime+desc"
               f"&$select=subject,body,from,receivedDateTime")
        try:
            req  = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=15)
            msgs = json.loads(resp.read()).get("value", [])
        except Exception as e:
            log(f"[graph] {folder} err: {e}"); continue
        log(f"[graph] {folder}: {len(msgs)} messages")
        for i, m in enumerate(msgs):
            subj      = m.get("subject", "")
            from_addr = m.get("from", {}).get("emailAddress", {}).get("address", "")
            recv_dt   = m.get("receivedDateTime", "")[:16]
            if i < 3:
                log(f"[graph]   msg{i}: from={from_addr} subj={subj!r:.60} dt={recv_dt}")
            body  = _html_mod.unescape(m.get("body", {}).get("content", ""))
            links = pattern.findall(body)
            if links:
                # v10: for replit action-code links, reject emails older than 55 min
                _link = links[0]
                if "replit.com/action-code" in _link:
                    _recv_full = m.get("receivedDateTime", "")
                    _age_ok = False
                    try:
                        import datetime as _dt
                        _recv = _dt.datetime.fromisoformat(_recv_full.replace("Z", "+00:00"))
                        _now  = _dt.datetime.now(_dt.timezone.utc)
                        _age_min = (_now - _recv).total_seconds() / 60
                        _age_ok = _age_min <= 55
                        if not _age_ok:
                            log(f"[graph] skip expired replit link age={_age_min:.0f}min (>55min) subj={subj!r}")
                            continue
                    except Exception as _te:
                        log(f"[graph] time parse err: {_te}"); _age_ok = True
                log(f"[graph] v8b ✓ {folder}: subj={subj!r} from={from_addr} url={_link[:80]}")
                return _link
    # $search 跨全部文件夹（Focused/Other/ClutteredLow 等 folder 扫描可能遗漏）
    try:
        _search_url = (
            "https://graph.microsoft.com/v1.0/me/messages"
            "?$search=%22unitool%22&$top=10"
            "&$select=subject,body,from,receivedDateTime"
        )
        req2  = urllib.request.Request(_search_url, headers=headers)
        resp2 = urllib.request.urlopen(req2, timeout=15)
        msgs2 = json.loads(resp2.read()).get("value", [])
        for m in msgs2:
            body  = m.get("body", {}).get("content", "")
            links = pattern.findall(body)
            if links:
                _subj = m.get("subject", "")
                log(f"[graph] \u2713 $search: subj='{_subj}' url={links[0][:60]}")
                return links[0]
    except Exception as e:
        log(f"[graph] $search err: {e}")
    return ""



def refresh_ms_token_imap(refresh_token):
    data = urllib.parse.urlencode({
        'grant_type': 'refresh_token', 'client_id': CLIENT_ID,
        'refresh_token': refresh_token,
        'scope': 'https://outlook.office.com/IMAP.AccessAsUser.All offline_access',
    }).encode()
    r = urllib.request.urlopen(urllib.request.Request(
        'https://login.microsoftonline.com/common/oauth2/v2.0/token',
        data=data, method='POST'), timeout=20)
    return json.loads(r.read())


def find_verify_link_imap(email_addr, imap_access_token, max_msgs=30):
    import datetime as _dt
    SOH = chr(1)
    auth_str = ('user=' + email_addr + SOH + 'auth=Bearer ' + imap_access_token + SOH + SOH).encode()
    pattern  = re.compile(
        r'https://(?:(?:[a-z0-9-]+[.])?unitool[.]ai|replit[.]com/action-code)[^\s"<>]+',
        re.IGNORECASE)
    try:
        conn = imaplib.IMAP4_SSL('outlook.office365.com', 993, timeout=20)
    except Exception as e:
        log('[imap] connect err: ' + str(e)); return ''
    try:
        conn.authenticate('XOAUTH2', lambda _: auth_str)
        log('[imap] auth OK')
        for folder in ('INBOX', 'Junk'):
            try:
                st, _ = conn.select(folder, readonly=True)
                if st != 'OK':
                    log('[imap] ' + folder + ' select fail'); continue
                _, data = conn.search(None, 'ALL')
                ids = data[0].split()
                log('[imap] ' + folder + ': ' + str(len(ids)) + ' msgs')
                for mid in reversed(ids[-max_msgs:]):
                    _, raw = conn.fetch(mid, '(RFC822)')
                    if not raw or not raw[0]: continue
                    msg      = _email_lib.message_from_bytes(raw[0][1])
                    subj     = str(msg.get('Subject', ''))
                    frm      = str(msg.get('From',    ''))
                    date_str = str(msg.get('Date',    ''))
                    body = ''
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() in ('text/plain', 'text/html'):
                                try: body += part.get_payload(decode=True).decode('utf-8', 'replace')
                                except: pass
                    else:
                        try: body = msg.get_payload(decode=True).decode('utf-8', 'replace')
                        except: body = str(msg.get_payload())
                    links = pattern.findall(body)
                    if links:
                        _link = links[0]
                        if 'replit.com/action-code' in _link:
                            try:
                                from email.utils import parsedate_to_datetime as _p2d
                                recv    = _p2d(date_str)
                                age_min = (_dt.datetime.now(_dt.timezone.utc) - recv).total_seconds() / 60
                                if age_min > 55:
                                    log('[imap] skip expired age=' + str(int(age_min)) + 'min'); continue
                            except Exception: pass
                        log('[imap] FOUND ' + folder + ' from=' + frm[:40] + ' url=' + _link[:80])
                        return _link
            except Exception as e:
                log('[imap] ' + folder + ' err: ' + str(e))
    except Exception as e:
        log('[imap] auth err: ' + str(e))
    finally:
        try: conn.logout()
        except: pass
    return ''




def find_verify_link_imap_idle(email_addr, imap_access_token, max_wait=300):
    import datetime as _dt
    from imapclient import IMAPClient
    pattern = re.compile(
        r'https://(?:(?:[a-z0-9-]+[.])?unitool[.]ai|replit[.]com/action-code)[^\s"<>]+',
        re.IGNORECASE)

    def _scan(server):
        for folder in ("INBOX", "Junk"):
            try:
                server.select_folder(folder, readonly=True)
                ids = server.search(["ALL"])
                if not ids:
                    continue
                latest = ids[-30:]
                msgs = server.fetch(latest, ["RFC822"])
                for uid, data in reversed(list(msgs.items())):
                    raw = data.get(b"RFC822", b"")
                    if not raw:
                        continue
                    msg  = _email_lib.message_from_bytes(raw)
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() in ("text/plain", "text/html"):
                                try: body += part.get_payload(decode=True).decode("utf-8", "replace")
                                except: pass
                    else:
                        try: body = msg.get_payload(decode=True).decode("utf-8", "replace")
                        except: body = str(msg.get_payload())
                    links = pattern.findall(body)
                    if links:
                        _link = links[0]
                        if "replit.com/action-code" in _link:
                            try:
                                from email.utils import parsedate_to_datetime as _p2d
                                recv    = _p2d(str(msg.get("Date", "")))
                                age_min = (_dt.datetime.now(_dt.timezone.utc) - recv).total_seconds() / 60
                                if age_min > 55:
                                    log("[idle] skip expired age=%dmin" % int(age_min)); continue
                            except Exception: pass
                        log("[idle] FOUND %s: %s" % (folder, _link[:80]))
                        return _link
            except Exception as e:
                log("[idle] scan %s err: %s" % (folder, e))
        return ""

    try:
        server = IMAPClient("outlook.office365.com", ssl=True)
        SOH = chr(1)
        auth_str = ("user=" + email_addr + SOH + "auth=Bearer " + imap_access_token + SOH + SOH).encode()
        server.authenticate("XOAUTH2", lambda _: auth_str)
        log("[idle] auth OK")
    except Exception as e:
        log("[idle] connect/auth err: %s" % e); return ""

    try:
        url = _scan(server)
        if url:
            return url

        deadline = time.time() + max_wait
        cycle    = 0
        server.select_folder("INBOX", readonly=True)
        while time.time() < deadline:
            remaining    = deadline - time.time()
            idle_timeout = min(240, remaining)
            if idle_timeout <= 5:
                break
            cycle += 1
            log("[idle] cycle %d: IDLE %.0fs" % (cycle, idle_timeout))
            server.idle()
            responses = server.idle_check(timeout=idle_timeout)
            server.idle_done()
            if responses:
                log("[idle] push received (%d events), scanning..." % len(responses))
                url = _scan(server)
                if url:
                    return url
            else:
                log("[idle] cycle %d timeout, rescanning..." % cycle)
                url = _scan(server)
                if url:
                    return url
        log("[idle] exhausted %ds — not found" % max_wait)
        return ""
    except Exception as e:
        log("[idle] outer err: %s" % e); return ""
    finally:
        try: server.logout()
        except: pass


def _click_replit_verify_firebase(verify_url):
    """用真实 Chrome (pydoll) 打开 replit action-code 页面执行 JS 验证。
    等待页面出现 'Success' / 'can now close' 文字才算成功。"""
    import asyncio as _asyncio

    async def _do_verify():
        from pydoll.browser import Chrome
        from pydoll.browser.options import ChromiumOptions
        _opt = ChromiumOptions()
        _opt.headless = False   # xvfb DISPLAY=:99 提供虚拟屏幕
        _chrome = None
        for _p in ["/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
                   "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
                   "/usr/local/bin/google-chrome", "/usr/bin/google-chrome"]:
            if os.path.exists(_p):
                _chrome = _p; break
        if _chrome:
            _opt.binary_location = _chrome
        for _a in ["--no-sandbox", "--disable-dev-shm-usage",
                   "--disable-gpu", "--window-size=1280,800",
                   "--lang=en-US", "--disable-blink-features=AutomationControlled"]:
            _opt.add_argument(_a)

        _found = False
        try:
            async with Chrome(options=_opt) as _browser:
                _tab = await _browser.start()
                log(f"[pydoll] goto {verify_url[:80]}")
                await _tab.go_to(verify_url)
                # 等待最多 30s，每 2s 检查一次页面文字
                for _i in range(15):
                    await _asyncio.sleep(2)
                    _txt = ""
                    try:
                        _r = await _tab.execute_script(
                            "document.body ? document.body.innerText.slice(0,500) : ''",
                            return_by_value=True)
                        if isinstance(_r, dict):
                            _inner = _r.get("result", _r)
                            if isinstance(_inner, dict):
                                _inner = _inner.get("result", _inner)
                            _txt = str(_inner.get("value", "")) if isinstance(_inner, dict) else str(_inner)
                        else:
                            _txt = str(_r or "")
                    except Exception as _e2:
                        log(f"[pydoll] js err: {_e2}")
                    _low = _txt.lower()
                    log(f"[pydoll] [{(_i+1)*2}s] body={_txt[:100].replace(chr(10),' | ')}")
                    if any(_kw in _low for _kw in ("success", "can now close", "return to replit", "email verified")):
                        log("[pydoll] ✓ SUCCESS text found")
                        _found = True
                        break
                    if any(_kw in _low for _kw in ("invalid", "expired", "error", "something went wrong")):
                        log("[pydoll] ✗ error text found"); break
        except Exception as _e:
            log(f"[pydoll] fatal: {type(_e).__name__}: {_e}")
        return _found

    try:
        return _asyncio.run(_do_verify())
    except Exception as _ex:
        log(f"[pydoll] asyncio.run err: {_ex}"); return False

def click_verify_link(verify_url):
    """curl点击验证链接，完成邮箱验证。返回(ssid, to_entry)"""
    # v9: replit.com/action-code -> Firebase REST API direct verify
    if 'replit.com/action-code' in verify_url:
        ok = _click_replit_verify_firebase(verify_url)
        log('[firebase] replit verify ' + ('OK' if ok else 'FAIL'))
        return '', ok
    ck  = "/tmp/unitool_rescue_ck.txt"
    hdr = "/tmp/unitool_rescue_hdr.txt"
    for f in [ck, hdr]:
        try: os.remove(f)
        except: pass
    # v5.13: Popen+communicate to avoid KBI crash (mirrors ds2api SIGTERM handling)
    _vr_cmd = [
        "curl", "-sS", "-L", "--max-redirs", "8",
        "-c", ck, "-b", ck, "-D", hdr,
        "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0",
        "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.9",
        "--max-time", "30",
        verify_url,
    ]
    _vr_proc = subprocess.Popen(_vr_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        _vr_out, _ = _vr_proc.communicate(timeout=35)
    except KeyboardInterrupt:
        try: _vr_proc.kill(); _vr_proc.communicate()
        except Exception: pass
        raise
    except subprocess.TimeoutExpired:
        try: _vr_proc.kill(); _vr_proc.communicate()
        except Exception: pass
        return "", False
    ssid     = ""; to_entry = False
    raw_hdrs = open(hdr).read() if os.path.exists(hdr) else ""
    # headers in hdr file, NOT r.stdout (body)
    for line in raw_hdrs.splitlines():
        if "unitool-ssid" in line.lower() and "set-cookie" in line.lower():
            m2 = re.search(r"unitool-ssid=([^;\s]+)", line, re.I)
            if m2: ssid = m2.group(1)
        if "/entry" in line and "location" in line.lower():
            to_entry = True
    if not ssid and os.path.exists(ck):
        for line in open(ck):
            if "unitool-ssid" in line.lower():
                parts = line.strip().split("\t")
                ssid  = parts[-1] if parts else ""; break
    log(f"[curl] ssid={'len='+str(len(ssid)) if ssid else 'NONE'} to_entry={to_entry}")
    return ssid, to_entry

def _run_login_once(email, password):
    """unitool_login.py 单次尝试，返回 ssid 或 ''"""
    if not os.path.exists(LOGIN_SCRIPT):
        log(f"[login] not found: {LOGIN_SCRIPT}"); return ""
    env = {**os.environ, "DISPLAY": ":99", "PYTHONUNBUFFERED": "1"}
    proc = None
    try:
        proc = subprocess.Popen(
            ["python3", LOGIN_SCRIPT, "--email", email, "--password", password, "--no-headless"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
        )
        try:
            stdout, stderr = proc.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.communicate()
            log("[login] timeout"); return ""
        for line in stdout.splitlines():
            if line.startswith("[OK]"):
                parts = line.split("|")
                if len(parts) >= 3:
                    ssid = parts[2].strip()
                    if ssid:
                        log(f"[login] ssid len={len(ssid)}")
                        return ssid
            if line.startswith("[FAIL]"):
                log(f"[login] {line}")
                if "email_not_verified" in line:
                    return "EMAIL_NOT_VERIFIED"
                # Fix-6c: navigation_timeout = dead RESI port, not account issue
                if "navigation_timeout" in line:
                    return "NETWORK_TRANSIENT"
        if stderr: log(f"[login] stderr: {stderr[-300:]}")
    except KeyboardInterrupt:
        if proc:
            try: proc.kill(); proc.communicate()
            except Exception: pass
        log("[login] KeyboardInterrupt — killing child and re-raising")
        raise
    except Exception as e:
        log(f"[login] err: {e}")
    return ""


def login_via_script(email, password):
    """
    调用 unitool_login.py 登录，返回 ssid。
    修复: 第一次 ERR_TIMED_OUT 时清除 RESI 健康缓存再重试，最多 2 次。
    """
    _resi_caches = ["/tmp/unitool_resi_healthy.json", "/tmp/unitool_resi_cache.json"]
    for attempt in range(1, 3):
        if attempt > 1:
            for _cf in _resi_caches:
                try: os.remove(_cf)
                except Exception: pass
            log(f"[login] 已清除 RESI 健康缓存, 重试 #{attempt}...")
            time.sleep(5)
        ssid = _run_login_once(email, password)
        if ssid == "EMAIL_NOT_VERIFIED":
            log(f"[login] email_not_verified — skipping retry")
            return "EMAIL_NOT_VERIFIED"
        if ssid == "NETWORK_TRANSIENT":
            log("[login] network_transient (dead RESI port) attempt %d/2" % attempt)
            if attempt >= 2:
                return "NETWORK_TRANSIENT"
        if ssid:
            return ssid
        log(f"[login] 尝试 {attempt}/2 失败")
    return ""
def cleanup_stale_processing(max_age_min=30):
    """清理卡死超过 max_age_min 分钟的 unitool_processing 锁（防孤儿锁永久阻塞）"""
    try:
        conn = db_connect(); cur = conn.cursor()
        cur.execute("""
            UPDATE accounts SET
              tags = TRIM(BOTH ',' FROM regexp_replace(tags, ',?unitool_processing', '', 'g')),
              updated_at = NOW()
            WHERE platform='outlook'
              AND tags LIKE '%%unitool_processing%%'
              AND tags NOT LIKE '%%unitool_registered%%'
              AND updated_at < NOW() - INTERVAL '%s minutes'
            RETURNING id, email
        """ % max_age_min)
        cleaned = cur.fetchall()
        if cleaned:
            log(f"[stale] 🧹 {len(cleaned)} 个卡死 processing 已自动解锁: {[r[1] for r in cleaned]}")
        conn.commit(); conn.close()
    except Exception as e:
        log(f"[stale] 解锁异常(忽略): {e}")

def main():
    global _account_id, _success_flag
    global _account_id, _success_flag
    _account_id = None
    _success_flag = False
    open(LOG, "w").write("")
    log("=== unitool_verify_rescue start ===")

    # 清理孤儿 processing 锁（>30min 未释放 = 崩溃残留）
    try:
        cleanup_stale_processing(30)
    except Exception as e:
        log(f"[stale] 异常(忽略): {e}")

    row = get_pending_account()
    if not row:
        # v5.14: batch re-login for ref_activated accounts with expired/missing SSID
        relogin_row = get_relogin_account()
        if relogin_row:
            _rl_id, _rl_email, _rl_pw = relogin_row
            _account_id = _rl_id
            log(f"[relogin] {_rl_email} id={_rl_id}")
            mark_tag(_rl_id, "unitool_processing")
            _rl_ssid = login_via_script(_rl_email, _rl_pw)
            if _rl_ssid:
                log(f"[relogin] SUCCESS len={len(_rl_ssid)}")
                save_ssid(_rl_id, _rl_email, _rl_ssid)
                _success_flag = True
                # v5.15b: invalidate stale ref_code cache entry so chain_v3
                # uses fresh API result instead of old "expired" cached empty
                try:
                    import json as _jmod
                    _cf = "/tmp/unitool_ref_code_cache.json"
                    if os.path.exists(_cf):
                        _cache = _jmod.loads(open(_cf).read())
                        _removed = _cache.pop(str(_rl_id), None)
                        if _removed is not None:
                            open(_cf, "w").write(_jmod.dumps(_cache))
                            log(f"[relogin] cleared stale ref_cache for id={_rl_id}")
                except Exception as _ce:
                    log(f"[relogin] cache clear warn: {_ce}")
            else:
                log(f"[relogin] FAIL — unlock processing")
                try:
                    _conn_rl = db_connect(); _cur_rl = _conn_rl.cursor()
                    _cur_rl.execute("""
                        UPDATE accounts SET
                          tags = TRIM(BOTH ',' FROM
                            regexp_replace(tags, ',?unitool_processing', '', 'g')),
                          updated_at = NOW()
                        WHERE id = %s
                    """, (_rl_id,))
                    _conn_rl.commit(); _conn_rl.close()
                except Exception as _e_rl:
                    log(f"[relogin] unlock err: {_e_rl}")
            log("=== unitool_verify_rescue relogin done ===")
            return
        log("[main] no pending/relogin accounts → sleep 60s")
        import time as _t; _t.sleep(60); return

    account_id, email, password, refresh_token = row
    _account_id = account_id
    log(f"[main] account: {email} id={account_id}")
    mark_tag(account_id, "unitool_processing")

    # Graph token
    access_token = ""
    try:
        access_token = refresh_ms_token(refresh_token).get("access_token", "")
        log(f"[graph] token len={len(access_token)}")
    except Exception as e:
        log(f"[graph] token fail: {e}")
        _emsg = str(e)
        if "AADSTS70000" in _emsg or "service abuse mode" in _emsg:
            log(f"[graph] ⛔ AADSTS70000 — 账号永久封禁，标 abuse_mode 并跳过")
            try:
                _ac = db_connect(); _cur = _ac.cursor()
                _cur.execute("SELECT tags FROM accounts WHERE id=%s", (_account_id,))
                _r = _cur.fetchone(); _tg = _r[0] if _r and _r[0] else ""
                import re as _re
                _tg = _re.sub(r",?unitool_(processing|verify_pending|reg_retry)", "", _tg).strip(",")
                for _t in ("abuse_mode", "unitool_fail"):
                    if _t not in _tg:
                        _tg = (_tg + "," + _t).strip(",")
                _cur.execute("UPDATE accounts SET status='suspended', tags=%s, updated_at=NOW() WHERE id=%s",
                             (_tg, _account_id))
                _ac.commit(); _ac.close()
                log(f"[graph] DB 更新 → {_tg}")
            except Exception as _dbe:
                log(f"[graph] DB err: {_dbe}")
            _success_flag = True  # 防止 atexit 再次 unlock
            return

    # Fix-7a: smart polling — 0 vr_attempt→360s first try, 1+→100s repeat
    _notes_pre = ""
    try:
        _conn_pre = db_connect(); _cur_pre = _conn_pre.cursor()
        _cur_pre.execute("SELECT notes FROM accounts WHERE id=%s", (account_id,))
        _row_pre = _cur_pre.fetchone(); _conn_pre.close()
        _notes_pre = _row_pre[0] if _row_pre and _row_pre[0] else ""
    except Exception as _ep:
        log(f"[graph] notes pre-read err: {_ep}")
    _prior_vr = _notes_pre.count("vr_attempt=")
    _max_polls = 18 if _prior_vr == 0 else 5  # 360s first, 100s repeat
    log(f"[graph] prior_vr={_prior_vr} max_polls={_max_polls} ({_max_polls*20}s)")

    verify_url = ""
    # -- Method 0 v8: 读取 live-verify-poller/chain_v3 写入的共享缓存 --
    _safe_email = re.sub(r"[^a-z0-9._@+-]", "_", email.lower())
    _cache_path = f"/tmp/replit_verify_cache/{_safe_email}.json"
    try:
        if os.path.exists(_cache_path):
            import json as _jcache
            _cd = _jcache.loads(open(_cache_path).read())
            _cu = (_cd.get("verify_url") or "").strip()
            _ct = int(_cd.get("ts") or 0)
            if _cu and (time.time() * 1000 - _ct) < 600_000:
                log("[cache] v8 HIT src=" + _cd.get("source", "?") + ": " + _cu[:80])
                verify_url = _cu
            elif _cu:
                log("[cache] v8 EXPIRED ts=" + str(_ct))
    except Exception as _ce:
        log("[cache] v8 err(non-fatal): " + str(_ce)[:80])

    # IMAP IDLE first (low-latency push); degrade to Graph polling only if IMAP unavailable
    _imap_wait  = _max_polls * 20  # match original Graph poll window (360s / 100s)
    _imap_tried = False
    if not verify_url and refresh_token:
        try:
            _ir = refresh_ms_token_imap(refresh_token)
            _it = _ir.get("access_token", "")
            if _it:
                log("[idle] token OK len=%d max_wait=%ds" % (len(_it), _imap_wait))
                _imap_tried = True
                verify_url  = find_verify_link_imap_idle(email, _it, max_wait=_imap_wait)
                if verify_url:
                    log("[idle] FOUND: " + verify_url[:80])
                else:
                    log("[idle] not found within wait window")
            else:
                log("[idle] imap token fail: " + _ir.get("error_description", "")[:80])
        except Exception as _ie:
            log("[idle] err (non-fatal): " + str(_ie)[:80])

    # Graph API polling fallback — only when IMAP token unavailable
    if not verify_url and not _imap_tried:
        if access_token:
            log("[graph] IMAP unavailable — Graph polling max %ds..." % (_max_polls * 20))
            for attempt in range(_max_polls):
                import time as _t; _t.sleep(20)
                verify_url = find_verify_link(access_token)
                if verify_url:
                    log("[graph] found at %ds: %s" % ((attempt+1)*20, verify_url[:80])); break
                log("[graph] [%ds] not found" % ((attempt+1)*20))
        else:
            log("[graph] no Graph token and IMAP unavailable")

    ssid = ""
    if verify_url:
        # curl点击：完成邮箱验证（即使拿不到ssid，验证已完成）
        ssid, to_entry = click_verify_link(verify_url)
        if not ssid:
            log("[verify] email verified via curl, no ssid header → will login")
    else:
        log("[graph] no verify link found — try direct login anyway")

    # 如果 notes 含 already_registered → unitool 已有该账号但邮件未验证
    # 多睡 30s 再扫一次 inbox，给验证邮件多一点到达时间
    if not ssid:
        notes_chk = _notes_pre  # Fix-7c: reuse pre-fetched notes
        if "already_registered" in notes_chk and access_token:
            log("[rescue] already_registered in notes — extra 30s wait + rescan inbox")
            time.sleep(30)
            for _fld in ("JunkEmail", "Inbox"):
                try:
                    import urllib.request as _ur2
                    _filter = "from/emailAddress/address%20eq%20%27no-reply%40unitool.ai%27"
                    _url2 = ("https://graph.microsoft.com/v1.0/me/mailFolders/"
                             + _fld + "/messages"
                             + "?$filter=" + _filter
                             + "&$top=5"
                             + "&$select=subject,body,receivedDateTime")
                    _req2 = _ur2.Request(
                        _url2,
                        headers={"Authorization": "Bearer " + access_token}
                    )
                    _resp2 = _ur2.urlopen(_req2, timeout=15)
                    import json as _jj
                    _msgs2 = _jj.loads(_resp2.read()).get("value", [])
                    for _m2 in _msgs2:
                        _body2 = _m2.get("body", {}).get("content", "")
                        _urls2 = re.findall(r'https://[^\s"\'<>]+verify[^\s"\'<>]*', _body2)
                        if _urls2:
                            verify_url = _urls2[0]
                            log("[rescue] rescan found verify url: " + verify_url[:80])
                            ssid2, _ = click_verify_link(verify_url)
                            if ssid2:
                                ssid = ssid2
                            break
                    if ssid:
                        break
                except Exception as _e2:
                    log("[rescue] rescan " + _fld + " err: " + str(_e2))

    # 登录拿ssid（email验证完成后 / 或之前已验证过）
    if not ssid:
        log("[login] calling unitool_login.py...")
        ssid = login_via_script(email, password)

    if ssid and ssid != "EMAIL_NOT_VERIFIED":
        log(f"[done] SUCCESS len={len(ssid)}")
        save_ssid(account_id, email, ssid)
        _success_flag = True
    elif ssid in ("EMAIL_NOT_VERIFIED", "NETWORK_TRANSIENT"):
        _r = "email_not_verified" if ssid == "EMAIL_NOT_VERIFIED" else "network_transient"
        log("[done] %s — unlock, no rescue_fail counted" % _r)
        # Fix-7b: write vr_attempt= only for EMAIL_NOT_VERIFIED (not NETWORK_TRANSIENT)
        _note_vr = ("\nvr_attempt=" + time.strftime("%Y-%m-%d %H:%M:%S")
                    if ssid == "EMAIL_NOT_VERIFIED" else "")
        try:
            _conn_nv = db_connect(); _cur_nv = _conn_nv.cursor()
            _cur_nv.execute("""
                UPDATE accounts SET
                  tags = TRIM(BOTH ',' FROM regexp_replace(tags, ',?unitool_processing', '', 'g')),
                  notes = COALESCE(notes, '') || %s,
                  updated_at = NOW()
                WHERE id = %s
            """, (_note_vr, account_id))
            _conn_nv.commit(); _conn_nv.close()
        except Exception as _eu:
            log(f"[done] unlock err: {_eu}")
    else:
        log("[done] FAIL — no ssid, will retry next cycle")
        mark_rescue_fail(account_id)

    log("=== unitool_verify_rescue done ===")

if __name__ == "__main__":
    import time as _loop_time
    while True:
        try:
            main()
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as _loop_e:
            print(f"[loop] uncaught: {_loop_e}", flush=True)
            _loop_time.sleep(5)
