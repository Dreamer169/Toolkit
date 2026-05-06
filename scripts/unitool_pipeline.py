#!/usr/bin/env python3
"""
unitool_pipeline.py — unitool 注册完整流水线（下游脚本）
=========================================================
⚠️  上游依赖（必须先跑这一步生成 Outlook 账号入库）:
    POST /api/tools/outlook/register
      → outlook_register.py (patchright + 随机指纹 + CF IP 代理)
      → 成功后 OAuth 拿 refresh_token，一起写入 PostgreSQL accounts 表
    可通过前端一键触发:
      GET  /api/tools/workflow/prepare   → 生成身份+密码
      POST /api/tools/outlook/register  → 注册 Outlook + 入库（含 refresh_token）

本脚本流程（在 Outlook 账号已入库后执行）:
1. 从 PostgreSQL 拉全新 outlook 账号（有 refresh_token, 无 unitool_registered 标记）
2. pydoll 打开 unitool.ai/en/entry，Turnstile bypass，填写邮件/密码，提交注册
3. Graph API 同时轮询 Inbox + JunkEmail 找验证邮件（unitool 邮件常落入垃圾箱！）
4. curl 点击 verify 链接捕获 __Secure-unitool-ssid cookie
5. 若 verify 不含 ssid → pydoll login 获取 ssid
6. 写入 DB（notes 存 ssid，tags 加 unitool_registered）
"""

import asyncio, glob, json, os, re, subprocess, sys, time, urllib.parse, urllib.request
import psycopg2

DB_URL = "postgresql://postgres:postgres@localhost/toolkit"
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
DISPLAY = ":99"
CHROME = None
for p in ["/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
          "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
          "/data/cache/ms-playwright/chromium-1169/chrome-linux64/chrome"]:
    if os.path.exists(p): CHROME = p; break

LOG_FILE = "/tmp/unitool_pipeline.log"
SIGNUP_NA = "602b5c42ffedec9865ca902b033d188b22c575dfd5"
LOGIN_NA  = "60e02e33f743e14f5dab1dc42181ba1e746fd4d925"

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f: f.write(line + "\n")

def db_connect(): return psycopg2.connect(DB_URL)

def get_next_account():
    """拉一个有refresh_token、未做unitool的全新outlook账号"""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, email, password, refresh_token FROM accounts
        WHERE platform='outlook' AND status='active'
          AND refresh_token IS NOT NULL AND refresh_token != ''
          AND (tags IS NULL OR (
               tags NOT LIKE '%unitool_registered%'
            AND tags NOT LIKE '%unitool_fail%'
            AND tags NOT LIKE '%token_invalid%'
          ))
          AND LENGTH(COALESCE(password,'')) >= 12
        ORDER BY created_at DESC NULLS LAST
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    return row  # (id, email, password, refresh_token) or None

def get_pending_account():
    """拉一个unitool_verify_pending账号重试"""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, email, password, refresh_token FROM accounts
        WHERE platform='outlook' AND status='active'
          AND refresh_token IS NOT NULL AND refresh_token != ''
          AND tags LIKE '%unitool_verify_pending%'
          AND tags NOT LIKE '%unitool_registered%'
          AND LENGTH(COALESCE(password,'')) >= 8
        ORDER BY updated_at ASC NULLS LAST
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    return row

def mark_account(account_id, tag, extra_notes=""):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        UPDATE accounts SET
            tags = CASE WHEN COALESCE(tags,'')='' THEN %s
                        ELSE tags || ',' || %s END,
            notes = COALESCE(notes,'') || E'\n' || %s,
            updated_at = NOW()
        WHERE id = %s
    """, (tag, tag, f"{tag} at={time.strftime('%Y-%m-%d %H:%M:%S')} {extra_notes}", account_id))
    conn.commit()
    conn.close()

def save_ssid(account_id, email, ssid, all_cookies_json=""):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        UPDATE accounts SET
            tags = CASE WHEN COALESCE(tags,'')='' THEN 'unitool_registered'
                        ELSE tags || ',unitool_registered' END,
            notes = COALESCE(notes,'') || E'\nunitool_ssid=' || %s || E'\nat=' || %s,
            updated_at = NOW()
        WHERE id = %s
    """, (ssid[:200], time.strftime("%Y-%m-%d %H:%M:%S"), account_id))
    conn.commit()
    conn.close()
    log(f"[DB] saved ssid for {email} id={account_id} ssid_len={len(ssid)}")
    # AUTO-LINK: write ssid to /tmp/unitool_ssidN.txt so proxy auto-picks up within 5s
    try:
        _existing = sorted(glob.glob("/tmp/unitool_ssid*.txt"))
        _idxs = []
        for _f in _existing:
            _m = re.search(r"unitool_ssid(\d*)\.txt", _f)
            _idxs.append(int(_m.group(1)) if _m and _m.group(1) else 1)
        _next_n = (max(_idxs) + 1) if _idxs else 1
        _fname = f"/tmp/unitool_ssid{_next_n}.txt"
        with open(_fname, "w") as _fh:
            _fh.write(ssid)
        log(f"[proxy-file] wrote {_fname}")
    except Exception as _fe:
        log(f"[proxy-file] warn: {_fe}")
    # AUTO-LINK: tools.ts reads this line to push ssid -> proxy pool
    print(f"[OK] {email} | {ssid}", flush=True)

# ── Step 1: Graph API token refresh ──────────────────────────────────────────

def refresh_ms_token(refresh_token):
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
        "scope": "https://graph.microsoft.com/Mail.Read offline_access",
    }).encode()
    req = urllib.request.Request(
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        data=data, method="POST"
    )
    r = urllib.request.urlopen(req, timeout=20)
    return json.loads(r.read())

def read_inbox_for_unitool(access_token, max_msgs=20):
    """读 Inbox + JunkEmail 找unitool验证邮件，返回最新verify URL (或空串)
    修复: unitool验证邮件经常落入垃圾邮件文件夹，原代码只搜索Inbox会漏掉。"""
    params = urllib.parse.urlencode({
        "$top": str(max_msgs),
        "$select": "id,subject,from,receivedDateTime,bodyPreview",
        "$orderby": "receivedDateTime desc"
    })
    verify_urls = []
    # 同时搜索 Inbox 和 JunkEmail — 修复: unitool验证邮件常落入垃圾邮件
    for folder in ("inbox", "JunkEmail"):
        try:
            url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages?{params}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
            r = urllib.request.urlopen(req, timeout=15)
            msgs = json.loads(r.read()).get("value", [])
            log(f"  [{folder}] 共{len(msgs)}封邮件")
        except Exception as e:
            log(f"  [{folder}] 读取失败: {e}")
            continue

        for m in msgs:
            subj    = m.get("subject", "")
            preview = m.get("bodyPreview", "")
            sender  = m.get("from", {}).get("emailAddress", {}).get("address", "").lower()
            if ("unitool" not in subj.lower() and "unitool" not in preview.lower()
                    and "unitool" not in sender and "noreply" not in sender
                    and "verify" not in subj.lower()):
                continue
            mid = m["id"]
            try:
                req2 = urllib.request.Request(
                    f"https://graph.microsoft.com/v1.0/me/messages/{mid}?$select=body,receivedDateTime",
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                r2 = urllib.request.urlopen(req2, timeout=15)
                msg_data = json.loads(r2.read())
            except Exception as e:
                log(f"  [{folder}] 获取body失败: {e}")
                continue
            body    = msg_data.get("body", {}).get("content", "")
            recv_dt = msg_data.get("receivedDateTime", "")

            PAT1 = r"https://unitool[.]ai/api/auth/email[?]token=[^\s<>\"]+"
            PAT2 = r"https://unitool[.]ai/[^\s<>\"]*token=[^\s<>\"]+"
            links = re.findall(PAT1, body)
            if not links:
                links = re.findall(PAT2, body)
            for link in links:
                try:
                    payload_b64 = link.split("token=")[1].split(".")[1]
                    pad = 4 - len(payload_b64) % 4
                    import base64 as _b64
                    payload = json.loads(_b64.urlsafe_b64decode(payload_b64 + "=" * pad))
                    exp = payload.get("exp", 0)
                    now = int(time.time())
                    if exp > now:
                        verify_urls.append((exp, recv_dt, link))
                        log(f"  [{folder}] valid URL expires_in={exp-now}s")
                    else:
                        log(f"  [{folder}] expired URL")
                except Exception:
                    verify_urls.append((0, recv_dt, link))

    if not verify_urls:
        return ""
    verify_urls.sort(reverse=True)
    return verify_urls[0][2]

def poll_inbox_for_verify(refresh_token, timeout_sec=300, interval_sec=20):
    """轮询inbox直到找到有效verify URL，超时返回空串"""
    log(f"[inbox+junk] 开始轮询Inbox+JunkEmail验证邮件 (timeout={timeout_sec}s)...")
    deadline = time.time() + timeout_sec
    access_token = ""
    
    while time.time() < deadline:
        try:
            if not access_token:
                resp = refresh_ms_token(refresh_token)
                access_token = resp.get("access_token", "")
                if not access_token:
                    log(f"  [inbox] token刷新失败: {resp.get('error_description','?')[:80]}")
                    time.sleep(interval_sec)
                    continue
                log(f"  [inbox] token刷新成功 len={len(access_token)}")
            
            url = read_inbox_for_unitool(access_token)
            if url:
                log(f"  [inbox] 找到验证链接!")
                return url
            
            remaining = int(deadline - time.time())
            log(f"  [inbox+junk] 未找到，剩余{remaining}s，{interval_sec}s后重试...")
            time.sleep(interval_sec)
        except Exception as e:
            log(f"  [inbox] 读取异常: {e}")
            access_token = ""  # 强制刷新token
            time.sleep(interval_sec)
    
    log(f"  [inbox+junk] 超时，未找到验证邮件")
    return ""

def click_verify_link(verify_url):
    """用curl访问验证链接，捕获__Secure-unitool-ssid cookie"""
    cookie_file = "/tmp/unitool_verify_cookies.txt"
    log(f"[verify] 点击链接: {verify_url[:80]}...")
    
    result = subprocess.run([
        "curl", "-s", "-D", "-",
        "-c", cookie_file, "-b", cookie_file,
        "-L", "--max-redirs", "5",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H", "Accept-Language: en-US,en;q=0.5",
        "-o", "/tmp/unitool_verify_resp.html",
        verify_url
    ], capture_output=True, text=True, timeout=30)
    
    headers = result.stdout
    log(f"  [verify] curl status: {result.returncode}")
    
    # 从headers找Set-Cookie: __Secure-unitool-ssid
    ssid_from_header = ""
    for line in headers.split("\n"):
        if "__Secure-unitool-ssid" in line and "Set-Cookie" in line:
            m = re.search(r"__Secure-unitool-ssid=([^;]+)", line)
            if m:
                ssid_from_header = m.group(1)
                log(f"  [verify] ssid from header: {ssid_from_header[:40]}...")
    
    # 从cookie文件读取
    ssid_from_file = ""
    try:
        with open(cookie_file) as f:
            for line in f:
                if "__Secure-unitool-ssid" in line:
                    parts = line.strip().split("\t")
                    if parts:
                        ssid_from_file = parts[-1]
                        log(f"  [verify] ssid from cookie file: {ssid_from_file[:40]}...")
    except: pass
    
    ssid = ssid_from_header or ssid_from_file
    
    # 检查final URL（是否重定向到entry = 失败）
    final_redirected_to_entry = False
    if "/entry" in headers:
        final_redirected_to_entry = True
        log(f"  [verify] WARNING: redirected to /entry (token may be invalid)")
    
    return ssid, final_redirected_to_entry

# ── Step 2: pydoll注册unitool ────────────────────────────────────────────────

async def unitool_register(email, password):
    """用pydoll在unitool.ai注册账号，返回(success, error_msg)"""
    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions
    
    log(f"[unitool_reg] 开始注册: {email}")
    opt = ChromiumOptions()
    opt.headless = False
    if CHROME: opt.binary_location = CHROME
    for a in ["--no-sandbox","--disable-dev-shm-usage","--window-size=1440,900",
               "--disable-gpu","--lang=en-US","--disable-blink-features=AutomationControlled"]:
        opt.add_argument(a)
    
    env_backup = os.environ.get("DISPLAY", "")
    os.environ["DISPLAY"] = DISPLAY
    
    def s(r):
        if not isinstance(r, dict): return str(r) if r else ""
        inner = r.get("result", r)
        if isinstance(inner, dict): inner = inner.get("result", inner)
        return str(inner.get("value","")) if isinstance(inner, dict) else str(inner)
    
    async def tok_len(tab):
        return int(s(await tab.execute_script(
            "(document.querySelector('[name=\"cf-turnstile-response\"]')||{value:''}).value.length",
            return_by_value=True)) or 0)
    
    try:
        async with Chrome(options=opt) as browser:
            tab = await browser.start()
            await tab.enable_network_events()
            
            reg_success = False
            reg_error = ""
            
            log("[unitool_reg] goto https://unitool.ai/en/entry")
            await tab.go_to("https://unitool.ai/en/entry")
            await asyncio.sleep(4)
            
            # bypass Turnstile
            log("[unitool_reg] bypass Turnstile...")
            for attempt in range(3):
                try:
                    await tab._bypass_cloudflare({}, time_to_wait_captcha=20)
                    log(f"  bypass OK (attempt {attempt+1})")
                    break
                except Exception as e:
                    log(f"  bypass attempt {attempt+1}: {e}")
                    await asyncio.sleep(2)
            
            # 等token
            for i in range(25):
                await asyncio.sleep(1)
                n = await tok_len(tab)
                if n > 20:
                    log(f"  token ready at {i+1}s len={n}")
                    break
                if i % 5 == 4: log(f"  [{i+1}s] waiting token len={n}")
            
            n = await tok_len(tab)
            if n < 20:
                return False, f"Turnstile failed (len={n})"
            
            # 填email
            r = s(await tab.execute_script(f"""(function(){{
                var el=document.querySelector('input[name="email"]')||document.querySelector('input[type="email"]');
                if(!el) return 'NOT_FOUND';
                el.focus(); document.execCommand('selectAll'); document.execCommand('delete');
                document.execCommand('insertText',false,{json.dumps(email)});
                return 'val='+el.value;
            }})()""", return_by_value=True))
            log(f"  email: {r}")
            await asyncio.sleep(0.3)
            
            # 填password
            r2 = s(await tab.execute_script(f"""(function(){{
                var el=document.querySelector('input[type="password"]');
                if(!el) return 'NOT_FOUND';
                el.focus(); document.execCommand('selectAll'); document.execCommand('delete');
                document.execCommand('insertText',false,{json.dumps(password)});
                return 'len='+el.value.length;
            }})()""", return_by_value=True))
            log(f"  password: {r2}")
            await asyncio.sleep(0.5)
            
            # 等按钮enabled
            btn_ready = False
            for i in range(25):
                await asyncio.sleep(1)
                r3 = s(await tab.execute_script("""JSON.stringify(
                    Array.from(document.querySelectorAll('button'))
                    .filter(b=>b.innerText.trim()==='Join Unitool').map(b=>b.disabled)
                )""", return_by_value=True))
                try:
                    dl = json.loads(r3)
                    if dl and not any(dl):
                        btn_ready = True
                        log(f"  Join btn enabled at {i+1}s")
                        break
                except: pass
                if i % 5 == 4: log(f"  [{i+1}s] btn disabled={r3}")
            
            if not btn_ready:
                # 检查是否已存在
                pg = s(await tab.execute_script("document.body.innerText.slice(0,300)", return_by_value=True))
                if re.search(r'email.{0,30}already|already.{0,20}registered|already.{0,20}exist|user with like email existed', pg, re.I):
                    return False, "email_already_registered"
                log(f"  btn_never_enabled page={pg[:200]}")
                return False, "btn_never_enabled"
            
            # 提交
            sub = s(await tab.execute_script("""(function(){
                var btns=Array.from(document.querySelectorAll('button'));
                for(var b of btns){
                    if(b.innerText.trim()==='Join Unitool'&&!b.disabled){b.click();return 'CLICKED';}
                }
                return 'NO_BTN';
            })()""", return_by_value=True))
            log(f"  submit: {sub}")
            
            # 等待响应（email verification message）
            for t in range(30):
                await asyncio.sleep(1)
                pg = s(await tab.execute_script("document.body.innerText.slice(0,500)", return_by_value=True))
                cur_url = s(await tab.execute_script("location.href", return_by_value=True))
                
                if "sent link to your" in pg.lower() or "follow the link" in pg.lower():
                    log(f"  [✓] 'sent link' at {t+1}s → email verification pending")
                    reg_success = True
                    break
                if re.search(r'email.{0,30}already|already.{0,20}registered|already.{0,20}exist|user with like email existed', pg, re.I):
                    log(f"  [!!] email_already_registered page={pg[:200]}")
                    reg_error = "email_already_registered"
                    break
                log(f"  [{t+1}s] body={pg[:180].replace(chr(10), ' | ')}")
                if "entry" not in cur_url and "unitool.ai" in cur_url:
                    log(f"  [✓] redirect to {cur_url} at {t+1}s → registered+autologin?")
                    reg_success = True
                    break
                if t % 10 == 9: log(f"  [{t+1}s] url={cur_url}")
            
            if not reg_success and not reg_error:
                reg_error = "timeout_no_confirmation"
            
            return reg_success, reg_error
    except Exception as e:
        return False, str(e)[:200]
    finally:
        if env_backup:
            os.environ["DISPLAY"] = env_backup

async def unitool_login_for_ssid(email, password):
    """用pydoll登录unitool.ai，返回ssid cookie"""
    login_script = "/root/Toolkit/scripts/unitool_login.py"
    log(f"[unitool_login] 登录: {email}")
    
    env = {**os.environ, "DISPLAY": DISPLAY, "PYTHONUNBUFFERED": "1"}
    result = subprocess.run(
        ["python3", login_script, "--email", email, "--password", password, "--no-headless"],
        capture_output=True, text=True, timeout=180, env=env
    )
    for line in result.stdout.split("\n"):
        if line.startswith("[OK]"):
            parts = line.split("|")
            if len(parts) >= 3:
                ssid = parts[2]
                log(f"  [login] ssid: {ssid[:40]}...")
                return ssid
        if line.startswith("[FAIL]"):
            log(f"  [login] FAIL: {line}")
    
    if result.stderr:
        log(f"  [login] stderr: {result.stderr[-300:]}")
    return ""

# ── 完整pipeline ─────────────────────────────────────────────────────────────

async def process_one_account(acct_id, email, password, refresh_token):
    log(f"\n{'='*60}")
    log(f"[pipeline] 处理账号 id={acct_id} {email}")
    
    # Step 1: unitool注册
    log("[step1] unitool注册...")
    reg_ok, reg_err = await unitool_register(email, password)
    
    if not reg_ok:
        log(f"[step1] 注册失败: {reg_err}")
        mark_account(acct_id, "unitool_fail", f"reg_err={reg_err[:80]}")
        return False
    
    log("[step1] 注册成功（邮件已发送）")
    
    # Step 2: 轮询inbox找verify链接
    log("[step2] 轮询inbox...")
    verify_url = poll_inbox_for_verify(refresh_token, timeout_sec=300, interval_sec=20)
    
    if not verify_url:
        log("[step2] 未找到verify链接")
        mark_account(acct_id, "unitool_verify_pending", "no_verify_email")
        return False
    
    # Step 3: 点击verify链接
    log("[step3] 点击verify链接...")
    ssid, to_entry = click_verify_link(verify_url)
    
    if ssid:
        log(f"[step3] ✅ verify成功，ssid获取！ssid_len={len(ssid)}")
        save_ssid(acct_id, email, ssid)
        return True
    
    if to_entry:
        log("[step3] verify重定向到/entry，token可能已过期或需要另行登录")
    else:
        log("[step3] verify完成但无ssid cookie，尝试独立登录")
    
    # Step 4: pydoll登录获取ssid
    log("[step4] pydoll登录获取ssid...")
    await asyncio.sleep(3)
    ssid = await unitool_login_for_ssid(email, password)
    
    if ssid:
        log(f"[step4] ✅ 登录成功，ssid获取！ssid_len={len(ssid)}")
        save_ssid(acct_id, email, ssid)
        return True
    else:
        log("[step4] 登录失败，无ssid")
        mark_account(acct_id, "unitool_fail", "login_no_ssid")
        return False

async def retry_pending_account(acct_id, email, password, refresh_token):
    """重试pending账号：跳过step1直接轮询inbox获取verify链接"""
    log(f"\n{'='*60}")
    log(f"[retry] 重试 id={acct_id} {email}")
    conn = db_connect(); cur = conn.cursor()
    cur.execute(
        "UPDATE accounts SET tags=REGEXP_REPLACE(tags, E'(,unitool_verify_pending|unitool_verify_pending,|unitool_verify_pending)', '', 'g'), updated_at=NOW() WHERE id=%s",
        (acct_id,))
    conn.commit(); conn.close()
    mark_account(acct_id, "unitool_processing", "retry")
    log("[retry-step2] 轮询inbox (timeout=300s, 跳过step1)...")
    verify_url = poll_inbox_for_verify(refresh_token, timeout_sec=300, interval_sec=20)
    if not verify_url:
        log("[retry-step2] 仍未找到verify链接，重标pending")
        mark_account(acct_id, "unitool_verify_pending", "retry_no_email")
        return False
    log("[retry-step3] 点击verify链接...")
    ssid, to_entry = click_verify_link(verify_url)
    if ssid:
        log(f"[retry-step3] \u2705 ssid len={len(ssid)}")
        save_ssid(acct_id, email, ssid)
        return True
    log("[retry-step4] 无ssid，pydoll登录...")
    await asyncio.sleep(3)
    ssid = await unitool_login_for_ssid(email, password)
    if ssid:
        log(f"[retry-step4] \u2705 登录成功 ssid_len={len(ssid)}")
        save_ssid(acct_id, email, ssid)
        return True
    mark_account(acct_id, "unitool_fail", "retry_login_no_ssid")
    return False

async def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--email", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--account-id", type=int, default=0)
    ap.add_argument("--wait-for-accounts", type=int, default=0,
                    help="等待N秒让outlook注册完成再开始")
    ap.add_argument("--retry-pending", type=int, default=0,
                    help="重试N个unitool_verify_pending账号")
    args = ap.parse_args()
    
    open(LOG_FILE, "w").write("")
    log(f"[main] unitool pipeline启动 batch={args.batch}")
    
    if args.wait_for_accounts > 0:
        log(f"[main] 等待{args.wait_for_accounts}s让outlook账号注册完成...")
        time.sleep(args.wait_for_accounts)
    
    if args.email and args.account_id:
        # 手动指定
        row = (args.account_id, args.email, args.password, "")
        await process_one_account(*row)
        return

    if args.retry_pending > 0:
        log(f"[main] retry-pending 模式，最多重试{args.retry_pending}个账号")
        ok_count = 0
        for i in range(args.retry_pending):
            row = get_pending_account()
            if not row:
                log(f"[main] 没有更多pending账号 (已处理{i}个)")
                break
            acct_id, email, password, refresh_token = row
            log(f"\n[main] [{i+1}/{args.retry_pending}] 重试pending: {email}")
            success = await retry_pending_account(acct_id, email, password, refresh_token)
            if success:
                ok_count += 1
            if i < args.retry_pending - 1:
                await asyncio.sleep(5)
        log(f"\n[main] retry-pending完成 成功={ok_count}/{args.retry_pending}")
        return

    ok_count = 0
    for i in range(args.batch):
        row = get_next_account()
        if not row:
            log(f"[main] 没有更多可用账号 (已处理{i}个)")
            break
        acct_id, email, password, refresh_token = row
        log(f"\n[main] [{i+1}/{args.batch}] 账号: {email}")
        
        # 先标记"正在处理"防止重复拉取
        mark_account(acct_id, "unitool_processing", "")
        
        success = await process_one_account(acct_id, email, password, refresh_token)
        if success:
            ok_count += 1
        
        if i < args.batch - 1:
            log("[main] 间隔10s...")
            await asyncio.sleep(10)
    
    log(f"\n[main] 完成 成功={ok_count}/{args.batch}")

if __name__ == "__main__":
    asyncio.run(main())
