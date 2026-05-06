#!/usr/bin/env python3
"""
unitool_verify_rescue.py — 专门处理 unitool_verify_pending 账号
流程: 选pending账号 → 锁定 → 刷新Graph token → 查JunkEmail(60s) → curl点击验证 → unitool_login.py登录拿ssid
持续循环运行，无账号时sleep 60s
"""
import atexit, glob, json, os, re, subprocess, time
import urllib.parse, urllib.request
import psycopg2

LOG      = "/tmp/unitool_verify_rescue.log"
DB_URL   = "postgresql://postgres:postgres@localhost/toolkit"
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
LOGIN_SCRIPT = "/root/Toolkit/scripts/unitool_login.py"

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
          AND (
            notes IS NULL
            OR notes NOT LIKE '%rescue_fail_at=%'
            OR updated_at < NOW() - INTERVAL '30 minutes'
          )
        ORDER BY updated_at ASC NULLS LAST
        LIMIT 1
    """)
    row = cur.fetchone(); conn.close()
    return row

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
    cur.execute("""
        UPDATE accounts SET
          tags  = regexp_replace(
                    CASE WHEN COALESCE(tags,'')='' THEN 'unitool_registered'
                         ELSE tags || ',unitool_registered' END,
                    ',?unitool_(processing|fail|verify_pending)', '', 'g'),
          notes = COALESCE(notes,'') || E'\nunitool_ssid=' || %s || E'\nat=' || %s,
          updated_at = NOW()
        WHERE id=%s
    """, (ssid, time.strftime("%Y-%m-%d %H:%M:%S"), account_id))
    conn.commit(); conn.close()
    log(f"[DB] ssid saved {email} id={account_id} len={len(ssid)}")
    try:
        existing = sorted(glob.glob("/tmp/unitool_ssid*.txt"))
        idxs = []
        for fp in existing:
            m = re.search(r"unitool_ssid(\d*)\.txt", fp)
            idxs.append(int(m.group(1)) if m and m.group(1) else 1)
        n     = (max(idxs) + 1) if idxs else 1
        fname = f"/tmp/unitool_ssid{n}.txt"
        open(fname, "w").write(ssid)
        log(f"[proxy] wrote {fname}")
    except Exception as e:
        log(f"[proxy] warn: {e}")
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
    """在 JunkEmail+Inbox 找unitool验证邮件，按sender域名优先匹配"""
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    pattern = re.compile(r"https://unitool\.ai/\S+", re.IGNORECASE)
    for folder in ["JunkEmail", "Inbox"]:
        url = (f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
               f"?$top={max_msgs}&$orderby=receivedDateTime+desc"
               f"&$select=subject,body,from,receivedDateTime")
        try:
            req  = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=15)
            msgs = json.loads(resp.read()).get("value", [])
        except Exception as e:
            log(f"[graph] {folder} err: {e}"); continue
        for m in msgs:
            subj      = m.get("subject", "").lower()
            from_addr = m.get("from", {}).get("emailAddress", {}).get("address", "").lower()
            if "unitool.ai" not in from_addr and "unitool" not in subj and "verify" not in subj:
                continue
            body  = m.get("body", {}).get("content", "")
            links = pattern.findall(body)
            if links:
                log(f"[graph] ✓ {folder}: subj='{m.get('subject','')}' from={from_addr}")
                return links[0]
    return ""

def click_verify_link(verify_url):
    """curl点击验证链接，完成邮箱验证。返回(ssid, to_entry)"""
    ck  = "/tmp/unitool_rescue_ck.txt"
    hdr = "/tmp/unitool_rescue_hdr.txt"
    for f in [ck, hdr]:
        try: os.remove(f)
        except: pass
    r = subprocess.run([
        "curl", "-sS", "-L", "--max-redirs", "8",
        "-c", ck, "-b", ck, "-D", hdr,
        "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0",
        "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.9",
        "--max-time", "30",
        verify_url
    ], capture_output=True, text=True, timeout=35)
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

def login_via_script(email, password):
    """调用 unitool_login.py 子进程登录，返回ssid"""
    if not os.path.exists(LOGIN_SCRIPT):
        log(f"[login] not found: {LOGIN_SCRIPT}"); return ""
    env = {**os.environ, "DISPLAY": ":99", "PYTHONUNBUFFERED": "1"}
    try:
        r = subprocess.run(
            ["python3", LOGIN_SCRIPT, "--email", email, "--password", password, "--no-headless"],
            capture_output=True, text=True, timeout=180, env=env
        )
        for line in r.stdout.splitlines():
            if line.startswith("[OK]"):
                parts = line.split("|")
                if len(parts) >= 3:
                    ssid = parts[2].strip()
                    log(f"[login] ssid len={len(ssid)}")
                    return ssid
            if line.startswith("[FAIL]"):
                log(f"[login] {line}")
        if r.stderr: log(f"[login] stderr: {r.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        log("[login] timeout")
    except Exception as e:
        log(f"[login] err: {e}")
    return ""

# ── 主逻辑 ────────────────────────────────────────────────────────────────────
def main():
    global _account_id, _success_flag
    open(LOG, "w").write("")
    log("=== unitool_verify_rescue start ===")

    row = get_pending_account()
    if not row:
        log("[main] no pending account → sleep 60s")
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

    # 查验证邮件（60s轮询，6×10s）
    verify_url = ""
    if access_token:
        log("[graph] polling JunkEmail+Inbox (max 60s)...")
        for attempt in range(6):
            import time as _t; _t.sleep(10)
            verify_url = find_verify_link(access_token)
            if verify_url:
                log(f"[graph] found at {(attempt+1)*10}s: {verify_url[:80]}"); break
            log(f"[graph] [{(attempt+1)*10}s] not found")
    else:
        log("[graph] no token")

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
        conn_chk = db_connect(); cur_chk = conn_chk.cursor()
        cur_chk.execute("SELECT notes FROM accounts WHERE id=%s", (account_id,))
        row_chk = cur_chk.fetchone(); conn_chk.close()
        notes_chk = row_chk[0] if row_chk and row_chk[0] else ""
        if "already_registered" in notes_chk and access_token:
            log("[rescue] already_registered in notes — extra 30s wait + rescan inbox")
            time.sleep(30)
            for _fld in ("JunkEmail", "Inbox"):
                try:
                    import urllib.request as _ur2
                    _filter = "from/emailAddress/address%20eq%20'no-reply@unitool.ai'"
                    _url2 = ("https://graph.microsoft.com/v1.0/me/mailFolders/"
                             + _fld + "/messages"
                             + "?$filter=" + _filter
                             + "&$orderby=receivedDateTime%20desc&$top=5"
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

    if ssid:
        log(f"[done] SUCCESS len={len(ssid)}")
        save_ssid(account_id, email, ssid)
        _success_flag = True
    else:
        log("[done] FAIL — no ssid, will retry next cycle")
        mark_rescue_fail(account_id)

    log("=== unitool_verify_rescue done ===")

main()
