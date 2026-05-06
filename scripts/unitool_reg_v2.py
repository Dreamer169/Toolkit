#!/usr/bin/env python3
"""
unitool_reg_v2.py — unitool 注册 v2（持续循环版）
流程: 选账号 → 锁定 → Graph token → Chrome注册 → JunkEmail轮询(60s) → curl点击 → pydoll登录
PM2持续运行，无账号时sleep 120s，资源不足时sleep 60s
"""
import asyncio, atexit, glob, json, os, re, subprocess, time
import urllib.parse, urllib.request
import psycopg2

LOG       = "/tmp/unitool_reg_v2.log"
DB_URL    = "postgresql://postgres:postgres@localhost/toolkit"
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
SIGNUP_NA = "602b5c42ffedec9865ca902b033d188b22c575dfd5"
LOGIN_SCRIPT = "/root/Toolkit/scripts/unitool_login.py"
RESI_PORTS   = [10851, 10853, 10854, 10857, 10859, 10870, 10872, 10878, 10879]

CHROME = None
for _p in ["/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
           "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome"]:
    if os.path.exists(_p): CHROME = _p; break

_account_id   = None
_success_flag = False

# ── 日志 ──────────────────────────────────────────────────────────────────────
def log(msg):
    ts   = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f: f.write(line + "\n")

# ── atexit 兜底：崩溃时自动标 unitool_fail，解锁账号 ──────────────────────────
def _atexit_handler():
    if not _account_id or _success_flag:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("SELECT tags FROM accounts WHERE id=%s", (_account_id,))
        row  = cur.fetchone()
        tags = row[0] if row and row[0] else ""
        if "unitool_registered" not in tags:
            new_tags = re.sub(r",?unitool_processing", "", tags).strip(",")
            if "unitool_fail" not in new_tags:
                new_tags = (new_tags + ",unitool_fail").strip(",")
            cur.execute("UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s",
                        (new_tags, _account_id))
            conn.commit()
            log(f"[atexit] id={_account_id} → {new_tags}")
        conn.close()
    except Exception as e:
        log(f"[atexit] err: {e}")

atexit.register(_atexit_handler)

# ── DB ────────────────────────────────────────────────────────────────────────
def db_connect():
    return psycopg2.connect(DB_URL)

def get_account():
    """选一个outlook账号：有refresh_token、无unitool相关tag、随机轮选"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("""
        SELECT id, email, password, refresh_token FROM accounts
        WHERE platform='outlook' AND status='active'
          AND refresh_token IS NOT NULL AND refresh_token != ''
          AND LENGTH(COALESCE(password,'')) >= 8
          AND (tags IS NULL OR (
               tags NOT LIKE '%unitool_registered%'
           AND tags NOT LIKE '%unitool_fail%'
           AND tags NOT LIKE '%unitool_processing%'
           AND tags NOT LIKE '%unitool_already%'
          ))
        ORDER BY RANDOM() LIMIT 1
    """)
    row = cur.fetchone(); conn.close()
    return row  # (id, email, password, refresh_token) or None

def mark_tag(account_id, tag):
    """追加一个tag（已存在则跳过）"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("SELECT tags FROM accounts WHERE id=%s", (account_id,))
    r = cur.fetchone(); tags = r[0] if r and r[0] else ""
    if tag not in tags:
        new_tags = (tags + "," + tag).strip(",")
        cur.execute("UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s",
                    (new_tags, account_id))
        conn.commit()
        log(f"[DB] id={account_id} tags→{new_tags}")
    conn.close()

def clear_tag(account_id, tag_pattern):
    """用正则移除tag（如 unitool_processing）"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("UPDATE accounts SET tags=regexp_replace(tags, %s, '', 'g'), updated_at=NOW() WHERE id=%s",
                (f",?{tag_pattern}", account_id))
    conn.commit(); conn.close()

def save_ssid(account_id, email, ssid):
    """写入ssid，标注unitool_registered，同步写ssid文件供proxy热加载"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("""
        UPDATE accounts SET
          tags       = regexp_replace(
                         CASE WHEN COALESCE(tags,'')='' THEN 'unitool_registered'
                              ELSE tags || ',unitool_registered' END,
                         ',?unitool_(processing|fail|verify_pending|already)', '', 'g'),
          notes      = COALESCE(notes,'') || E'\nunitool_ssid=' || %s || E'\nat=' || %s,
          updated_at = NOW()
        WHERE id=%s
    """, (ssid[:200], time.strftime("%Y-%m-%d %H:%M:%S"), account_id))
    conn.commit(); conn.close()
    log(f"[DB] ssid saved for {email} id={account_id} len={len(ssid)}")
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

def find_unitool_verify_link(access_token, max_msgs=30):
    """在 JunkEmail+Inbox 找unitool验证邮件，优先垃圾箱（unitool邮件常落入垃圾箱）"""
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
                log(f"[graph] link in {folder}: subj={m.get('subject','')} from={from_addr}")
                return links[0]
    return ""

def click_verify_and_get_ssid(verify_url):
    """curl点击验证链接（完成邮箱验证），返回(ssid, to_entry)"""
    ck_file  = "/tmp/unitool_verify_ck.txt"
    hdr_file = "/tmp/unitool_verify_hdr.txt"
    for f in [ck_file, hdr_file]:
        try: os.remove(f)
        except: pass
    result = subprocess.run([
        "curl", "-sS", "-L", "--max-redirs", "8",
        "-c", ck_file, "-b", ck_file,
        "-D", hdr_file,
        "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0",
        "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.9",
        "--max-time", "30",
        verify_url
    ], capture_output=True, text=True, timeout=35)
    ssid      = ""
    to_entry  = False
    raw_hdrs  = result.stdout
    # 从响应头找 Set-Cookie: __Secure-unitool-ssid
    for line in raw_hdrs.splitlines():
        if "unitool-ssid" in line.lower() and "set-cookie" in line.lower():
            m = re.search(r"unitool-ssid=([^;\s]+)", line, re.I)
            if m: ssid = m.group(1)
        if "/entry" in line and ("location:" in line.lower() or "< location:" in line.lower()):
            to_entry = True
    # 从cookie jar找
    if not ssid and os.path.exists(ck_file):
        for line in open(ck_file):
            if "unitool-ssid" in line.lower():
                parts = line.strip().split("\t")
                ssid  = parts[-1] if parts else ""
                break
    log(f"[curl] verify done ssid={'len='+str(len(ssid)) if ssid else 'NONE'} to_entry={to_entry}")
    return ssid, to_entry

def login_via_script(email, password):
    """调用 unitool_login.py 子进程登录，返回 ssid（脚本已验证稳定）"""
    if not os.path.exists(LOGIN_SCRIPT):
        log(f"[login] script not found: {LOGIN_SCRIPT}"); return ""
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
                log(f"[login] FAIL: {line}")
        if r.stderr: log(f"[login] stderr: {r.stderr[-300:]}")
    except subprocess.TimeoutExpired:
        log("[login] timeout 180s")
    except Exception as e:
        log(f"[login] err: {e}")
    return ""

# ── 资源检查 ──────────────────────────────────────────────────────────────────
def check_resources():
    """MemAvailable≥600MB 且 chrome-linux64 进程数≤5"""
    try:
        for line in open("/proc/meminfo"):
            if "MemAvailable" in line:
                mb = int(line.split()[1]) // 1024
                log(f"[res] mem={mb}MB")
                if mb < 600:
                    log(f"[res] SKIP mem<600MB"); return False
                break
    except: pass
    try:
        r = subprocess.run(
            ["bash", "-c",
             "ps aux | grep chrome-linux64/chrome | grep -v 'crashpad\\|grep' | wc -l"],
            capture_output=True, text=True)
        n = max(0, int(r.stdout.strip() or 0))
        log(f"[res] chrome={n}")
        if n > 5:
            log(f"[res] SKIP chrome>{n}"); return False
    except: pass
    return True

# ── pydoll JS辅助 ─────────────────────────────────────────────────────────────
def _s(r):
    if not isinstance(r, dict): return str(r) if r else ""
    inner = r.get("result", r)
    if isinstance(inner, dict): inner = inner.get("result", inner)
    return str(inner.get("value", "")) if isinstance(inner, dict) else str(inner)

# ── 注册主流程 ────────────────────────────────────────────────────────────────
async def do_register(tab, email, password, proxy_port):
    """pydoll注册流程，返回 (email_sent:bool, already_reg:bool)"""
    posts = []
    async def on_req(ev):
        req = ev.get("params", {}).get("request", {})
        if "unitool.ai/en/entry" not in req.get("url", ""): return
        hd  = req.get("headers", {})
        na  = hd.get("next-action") or hd.get("Next-Action", "")
        if req.get("method") == "POST" and na and na != "00c396975d301f79a8208d4a593c756fdb31e4f356":
            log(f"[POST] NA={na[:24]} body_len={len(req.get('postData',''))}")
            posts.append(na)

    await tab.on("Network.requestWillBeSent", on_req)

    log(f"[nav] goto unitool.ai/en/entry proxy:{proxy_port}")
    await tab.go_to("https://unitool.ai/en/entry")
    await asyncio.sleep(4)

    body_txt = _s(await tab.execute_script(
        "document.body ? document.body.innerText.slice(0,200) : 'NO_BODY'",
        return_by_value=True))
    log(f"[page] {body_txt[:120].replace(chr(10),' | ')}")

    # Turnstile bypass
    log("[cf] bypass...")
    try:
        await asyncio.wait_for(tab._bypass_cloudflare({}, time_to_wait_captcha=30), timeout=50)
        log("[cf] OK")
    except asyncio.TimeoutError:
        log("[cf] timeout — continue")
    except Exception as e:
        log(f"[cf] err: {e}")

    # 等token（最多20s）
    tok_len = 0
    for i in range(20):
        await asyncio.sleep(1)
        tok_len = int(_s(await tab.execute_script(
            "(document.querySelector('[name=\"cf-turnstile-response\"]')||{value:''}).value.length",
            return_by_value=True)) or 0)
        if tok_len > 20:
            log(f"[cf] token ready {i+1}s len={tok_len}"); break
        if i % 5 == 4: log(f"[cf] [{i+1}s] token_len={tok_len}")
    log(f"[cf] final token_len={tok_len}")

    # 填邮箱
    r = _s(await tab.execute_script(f"""(function(){{
        var el=document.querySelector('input[name="email"]')||document.querySelector('input[type="email"]');
        if(!el) return 'NOT_FOUND';
        el.focus(); document.execCommand('selectAll'); document.execCommand('delete');
        document.execCommand('insertText',false,{json.dumps(email)});
        return el.value;
    }})()""", return_by_value=True))
    log(f"[form] email={r}")

    # 填密码
    r2 = _s(await tab.execute_script(f"""(function(){{
        var el=document.querySelector('input[type="password"]');
        if(!el) return 'NOT_FOUND';
        el.focus(); document.execCommand('selectAll'); document.execCommand('delete');
        document.execCommand('insertText',false,{json.dumps(password)});
        return 'len='+el.value.length;
    }})()""", return_by_value=True))
    log(f"[form] pwd={r2}")
    await asyncio.sleep(0.5)

    # 等按钮enabled（最多15s）
    for i in range(15):
        await asyncio.sleep(1)
        dl = _s(await tab.execute_script("""JSON.stringify(
            Array.from(document.querySelectorAll('button'))
            .filter(b=>b.innerText.trim()==='Join Unitool').map(b=>b.disabled)
        )""", return_by_value=True))
        try:
            disabled = json.loads(dl)
            if disabled and not any(disabled):
                log(f"[form] btn enabled {i+1}s"); break
            if i % 5 == 4: log(f"[form] [{i+1}s] disabled={dl}")
        except: pass

    snap = _s(await tab.execute_script("""JSON.stringify({
        cfLen:(document.querySelector('[name="cf-turnstile-response"]')||{value:''}).value.length,
        pwLen:(document.querySelector('input[type="password"]')||{value:''}).value.length,
        body:document.body.innerText.slice(0,300)
    })""", return_by_value=True))
    log(f"[snap] {snap[:300]}")

    # 提交
    sub = _s(await tab.execute_script("""(function(){
        var btns=Array.from(document.querySelectorAll('button'));
        for(var b of btns){if(b.innerText.trim()==='Join Unitool'&&!b.disabled){b.click();return 'NATURAL';}}
        for(var b of btns){if(b.innerText.trim()==='Join Unitool'){b.disabled=false;b.click();return 'FORCE';}}
        return 'NO_BTN';
    })()""", return_by_value=True))
    log(f"[submit] {sub}")

    # 等结果（最多30s）
    email_sent = already_reg = False
    for t in range(15):
        await asyncio.sleep(2)
        cur_url = _s(await tab.execute_script("location.href", return_by_value=True))
        pg      = _s(await tab.execute_script("document.body.innerText.slice(0,500)", return_by_value=True))
        low     = pg.lower()
        log(f"[wait] {(t+1)*2}s url={cur_url} body={pg[:120].replace(chr(10),' | ')}")
        if "entry" not in cur_url and "unitool" in cur_url:
            log("[wait] redirect → already logged in?"); already_reg = True; break
        if any(kw in low for kw in ("sent", "check your email", "verify your email", "link to your email", "follow the link")):
            log("[wait] EMAIL SENT ✓"); email_sent = True; break
        if any(kw in low for kw in ("already", "email address is already", "user with like email existed")):
            log("[wait] ALREADY REGISTERED"); already_reg = True; break
        if "something went wrong" in low:
            log("[wait] SOMETHING WENT WRONG"); break

    log(f"[submit] posts={[p[:16] for p in posts]}")
    return email_sent, already_reg

# ── 主循环 ────────────────────────────────────────────────────────────────────
async def main():
    global _account_id, _success_flag
    open(LOG, "w").write("")
    log("=== unitool_reg_v2 start ===")

    if not check_resources():
        log("[main] resources low → sleep 60s")
        await asyncio.sleep(60); return

    row = get_account()
    if not row:
        log("[main] no account → sleep 120s")
        await asyncio.sleep(120); return

    account_id, email, password, refresh_token = row
    _account_id = account_id
    log(f"[main] account: {email} id={account_id}")
    mark_tag(account_id, "unitool_processing")       # 立即锁定，防OOM重复选

    proxy_port = RESI_PORTS[account_id % len(RESI_PORTS)]
    log(f"[main] proxy: socks5://127.0.0.1:{proxy_port}")

    # Graph token（注册提交后立即开始轮询用）
    access_token = ""
    try:
        access_token = refresh_ms_token(refresh_token).get("access_token", "")
        log(f"[graph] token len={len(access_token)}")
    except Exception as e:
        log(f"[graph] token fail: {e}")

    # pydoll 注册
    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions
    opt = ChromiumOptions()
    opt.headless = False
    if CHROME: opt.binary_location = CHROME
    for a in ["--no-sandbox", "--disable-dev-shm-usage", "--window-size=1440,900",
               "--disable-gpu", "--lang=en-US",
               f"--proxy-server=socks5://127.0.0.1:{proxy_port}"]:
        opt.add_argument(a)

    email_sent = already_reg = False
    try:
        async with Chrome(options=opt) as browser:
            tab = await browser.start()
            await tab.enable_network_events()
            email_sent, already_reg = await do_register(tab, email, password, proxy_port)
    except BaseException as e:
        log(f"[pydoll] fatal: {type(e).__name__}: {e}")

    reg_submitted = email_sent or already_reg
    log(f"[main] reg_submitted={reg_submitted} (email_sent={email_sent} already={already_reg})")

    if not reg_submitted:
        log("[main] not submitted → atexit will mark fail")
        return

    # Graph API 轮询验证邮件（60s，6×10s）
    verify_url = ""
    if access_token:
        log("[graph] polling JunkEmail+Inbox (max 60s)...")
        for attempt in range(6):
            await asyncio.sleep(10)
            verify_url = find_unitool_verify_link(access_token)
            if verify_url:
                log(f"[graph] found at {(attempt+1)*10}s: {verify_url[:80]}"); break
            log(f"[graph] [{(attempt+1)*10}s] not found")
    else:
        log("[graph] no token, skip poll")

    # curl点击验证链接（完成邮箱验证；ssid可能拿不到，但email已verified）
    ssid = ""
    if verify_url:
        ssid, to_entry = click_verify_and_get_ssid(verify_url)
        if to_entry and not ssid:
            log("[verify] redirected to /entry — email verified, need login for ssid")

    # 登录获取ssid（调用稳定的 unitool_login.py 子进程）
    if not ssid:
        log("[login] calling unitool_login.py for ssid...")
        if check_resources():
            ssid = login_via_script(email, password)
        else:
            log("[login] resources low, skip")

    if ssid:
        log(f"[done] SUCCESS len={len(ssid)}")
        save_ssid(account_id, email, ssid)
        _success_flag = True
    else:
        log("[done] FAIL — no ssid, marking verify_pending")
        mark_tag(account_id, "unitool_verify_pending")
        # atexit 会清 processing 并加 fail

    log("=== unitool_reg_v2 done ===")

asyncio.run(main())
