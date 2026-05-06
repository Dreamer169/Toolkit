#!/usr/bin/env python3
"""
unitool_register v2 — 全面修复版
修复记录:
  - 启动即锁定账号(unitool_processing)，防止OOM崩溃后重复选同账号
  - atexit 兜底标记 unitool_fail，避免账号永久占锁
  - 启动前检测内存 + Chrome进程数，资源不足时优雅退出(exit 0)
  - socks5 住宅代理绕过Turnstile
  - Graph API 轮询收件箱 + 垃圾箱找验证邮件并点击
  - pydoll login 回退获取 ssid
  - ORDER BY RANDOM() 防止固定选同一账号
"""
import asyncio, atexit, glob, json, os, re, subprocess, sys, time
import urllib.parse, urllib.request
import psycopg2

LOG       = "/tmp/unitool_reg_v2.log"
DB_URL    = "postgresql://postgres:postgres@localhost/toolkit"
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
SIGNUP_NA = "602b5c42ffedec9865ca902b033d188b22c575dfd5"

# 住宅代理端口列表（轮选）
RESI_PORTS = [10851, 10853, 10854, 10857, 10859, 10870, 10872, 10878, 10879]

CHROME = None
for p in ["/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
          "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome"]:
    if os.path.exists(p): CHROME = p; break

# ── 全局状态（atexit 用）─────────────────────────────────────────────────────
_account_id   = None
_success_flag = False

def log(msg):
    ts   = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f: f.write(line + "\n")

# ── atexit 兜底 ───────────────────────────────────────────────────────────────
def _atexit_handler():
    if _account_id and not _success_flag:
        try:
            conn = psycopg2.connect(DB_URL)
            cur  = conn.cursor()
            cur.execute("SELECT tags FROM accounts WHERE id=%s", (_account_id,))
            row  = cur.fetchone()
            tags = row[0] if row and row[0] else ""
            # 只有没registered才标fail
            if "unitool_registered" not in tags:
                # 移除processing，加fail
                new_tags = re.sub(r",?unitool_processing", "", tags).strip(",")
                if "unitool_fail" not in new_tags:
                    new_tags = (new_tags + ",unitool_fail").strip(",")
                cur.execute(
                    "UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s",
                    (new_tags, _account_id)
                )
                conn.commit()
                log(f"[atexit] account {_account_id} → unitool_fail")
            conn.close()
        except Exception as e:
            log(f"[atexit] err: {e}")

atexit.register(_atexit_handler)

# ── DB 操作 ───────────────────────────────────────────────────────────────────
def db_connect():
    return psycopg2.connect(DB_URL)

def get_account():
    conn = db_connect()
    cur  = conn.cursor()
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
        ORDER BY RANDOM()
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    return row  # (id, email, password, refresh_token) or None

def mark_tag(account_id, tag):
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("SELECT tags FROM accounts WHERE id=%s", (account_id,))
    r    = cur.fetchone()
    tags = r[0] if r and r[0] else ""
    if tag not in tags:
        new_tags = (tags + "," + tag).strip(",")
        cur.execute(
            "UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s",
            (new_tags, account_id)
        )
        conn.commit()
        log(f"[DB] id={account_id} tags→{new_tags}")
    conn.close()

def save_ssid(account_id, email, ssid):
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("""
        UPDATE accounts SET
          tags       = CASE WHEN COALESCE(tags,'')=''
                            THEN 'unitool_registered'
                            ELSE regexp_replace(tags,',?unitool_processing','','g')
                                 || ',unitool_registered' END,
          notes      = COALESCE(notes,'') || E'\nunitool_ssid=' || %s || E'\nat=' || %s,
          updated_at = NOW()
        WHERE id=%s
    """, (ssid[:200], time.strftime("%Y-%m-%d %H:%M:%S"), account_id))
    conn.commit()
    conn.close()
    log(f"[DB] saved ssid for {email} id={account_id} ssid_len={len(ssid)}")
    # 写 ssid 文件让 proxy 自动热加载
    try:
        existing = sorted(glob.glob("/tmp/unitool_ssid*.txt"))
        idxs     = []
        for fp in existing:
            m = re.search(r"unitool_ssid(\d*)\.txt", fp)
            idxs.append(int(m.group(1)) if m and m.group(1) else 1)
        n    = (max(idxs) + 1) if idxs else 1
        fname = f"/tmp/unitool_ssid{n}.txt"
        with open(fname, "w") as fh: fh.write(ssid)
        log(f"[proxy-file] wrote {fname}")
    except Exception as fe:
        log(f"[proxy-file] warn: {fe}")
    print(f"[OK] {email} | {ssid}", flush=True)

# ── Graph API ─────────────────────────────────────────────────────────────────
def refresh_ms_token(refresh_token):
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token", "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
        "scope": "https://graph.microsoft.com/Mail.Read offline_access",
    }).encode()
    req = urllib.request.Request(
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        data=data, method="POST")
    r = urllib.request.urlopen(req, timeout=20)
    return json.loads(r.read())

def find_unitool_verify_link(access_token, max_msgs=30):
    """在 JunkEmail+Inbox 找unitool验证邮件（优先JunkEmail，按sender/subject匹配）"""
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    pattern = re.compile(r"https://unitool\.ai/\S+", re.IGNORECASE)
    for folder in ["JunkEmail", "Inbox"]:
        url = (f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
               f"?={max_msgs}&=receivedDateTime+desc"
               f"&=subject,bodyPreview,body,from,receivedDateTime")
        req = urllib.request.Request(url, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            msgs = json.loads(resp.read()).get("value", [])
        except Exception as e:
            log(f"[graph] {folder} err: {e}"); continue
        for m in msgs:
            subj   = m.get("subject", "").lower()
            from_addr = m.get("from", {}).get("emailAddress", {}).get("address", "").lower()
            is_unitool = "unitool.ai" in from_addr or "unitool" in subj or "verify" in subj
            if not is_unitool:
                continue
            body  = m.get("body", {}).get("content", "") or m.get("bodyPreview", "")
            links = pattern.findall(body)
            if links:
                log(f"[graph] found verify link in {folder}: subj={subj} from={from_addr}")
                return links[0]
    return ""

def click_verify_link(verify_url):
    """curl 点击验证链接，捕获 __Secure-unitool-ssid cookie"""
    try:
        result = subprocess.run(
            ["curl", "-sS", "-L", "--max-redirs", "10",
             "-c", "/tmp/unitool_cookies.txt",
             "-b", "/tmp/unitool_cookies.txt",
             "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
             "-D", "/tmp/unitool_resp_headers.txt",
             "--max-time", "30",
             verify_url],
            capture_output=True, text=True, timeout=35
        )
        # 从 cookie jar 提取 ssid
        ssid = ""
        if os.path.exists("/tmp/unitool_cookies.txt"):
            for line in open("/tmp/unitool_cookies.txt"):
                if "__Secure-unitool-ssid" in line or "unitool-ssid" in line:
                    ssid = line.strip().split("\t")[-1]
                    break
        # 也从响应头找
        if not ssid and os.path.exists("/tmp/unitool_resp_headers.txt"):
            for line in open("/tmp/unitool_resp_headers.txt"):
                if "unitool-ssid" in line.lower():
                    m = re.search(r"unitool-ssid=([^;]+)", line, re.IGNORECASE)
                    if m: ssid = m.group(1); break
        log(f"[curl] verify done, ssid={'yes len='+str(len(ssid)) if ssid else 'NOT FOUND'}")
        log(f"[curl] resp body (200)={result.stdout[:200].replace(chr(10),' | ')}")
        return ssid
    except Exception as e:
        log(f"[curl] err: {e}"); return ""

# ── 资源检查 ──────────────────────────────────────────────────────────────────
def check_resources():
    """检查内存 + Chrome进程数(仅 chrome-linux64 全功能版)，资源不足返回 False"""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if "MemAvailable" in line:
                    avail_kb = int(line.split()[1])
                    avail_mb = avail_kb // 1024
                    log(f"[res] MemAvailable={avail_mb}MB")
                    if avail_mb < 600:
                        log(f"[res] SKIP — memory too low ({avail_mb}MB < 600MB)")
                        return False
                    break
    except: pass
    # 只计 chrome-linux64/chrome（非headless-shell）进程数
    try:
        r = subprocess.run(
            ["bash","-c","ps aux | grep chrome-linux64/chrome | grep -v crashpad | grep -v grep | wc -l"],
            capture_output=True, text=True
        )
        n = max(0, int(r.stdout.strip() or 0))
        log(f"[res] full-Chrome procs={n}")
        if n > 5:
            log(f"[res] SKIP — too many full-Chrome procs ({n} > 5), wait for slots")
            return False
    except: pass
    return True

def _s(r):
    if not isinstance(r, dict): return str(r) if r else ""
    inner = r.get("result", r)
    if isinstance(inner, dict): inner = inner.get("result", inner)
    return str(inner.get("value","")) if isinstance(inner, dict) else str(inner)

async def pydoll_login_get_ssid(email, password, proxy_port):
    """pydoll 回退：登录获取 ssid（注册后邮件验证链接抓不到时用）"""
    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions
    LOGIN_NA = "60e02e33f743e14f5dab1dc42181ba1e746fd4d925"

    opt = ChromiumOptions()
    opt.headless = False
    if CHROME: opt.binary_location = CHROME
    for a in ["--no-sandbox","--disable-dev-shm-usage","--window-size=1280,800",
               "--disable-gpu","--lang=en-US",
               f"--proxy-server=socks5://127.0.0.1:{proxy_port}"]:
        opt.add_argument(a)

    ssid = ""
    try:
        async with Chrome(options=opt) as browser:
            tab = await browser.start()
            await tab.enable_network_events()

            ssids_seen = []
            async def on_req(ev):
                req = ev.get("params",{}).get("request",{})
                hd  = req.get("headers",{})
                ck  = hd.get("cookie","") or hd.get("Cookie","")
                m   = re.search(r"__Secure-unitool-ssid=([^;]+)", ck)
                if m: ssids_seen.append(m.group(1))

            await tab.on("Network.requestWillBeSent", on_req)
            await tab.go_to("https://unitool.ai/en/entry")
            await asyncio.sleep(3)
            try:
                await tab._bypass_cloudflare({}, time_to_wait_captcha=25)
                log("[login] bypass OK")
            except Exception as e:
                log(f"[login] bypass err: {e}")

            # 填登录表单
            for _ in range(3):
                await asyncio.sleep(1)
                re_email = _s(await tab.execute_script(f"""(function(){{
                    var el=document.querySelector('input[name="email"]')||document.querySelector('input[type="email"]');
                    if(!el) return 'NOT_FOUND';
                    el.focus(); document.execCommand('selectAll'); document.execCommand('delete');
                    document.execCommand('insertText',false,{json.dumps(email)});
                    return el.value;
                }})()""", return_by_value=True))
                if email[:5] in re_email: break
            log(f"[login] email filled: {re_email}")

            _s(await tab.execute_script(f"""(function(){{
                var el=document.querySelector('input[type="password"]');
                if(!el) return;
                el.focus(); document.execCommand('selectAll'); document.execCommand('delete');
                document.execCommand('insertText',false,{json.dumps(password)});
            }})()""", return_by_value=True))

            # 等按钮并点击（登录按钮可能与注册按钮同文本，找Tab切换）
            for i in range(15):
                await asyncio.sleep(1)
                clicked = _s(await tab.execute_script("""(function(){
                    var btns=Array.from(document.querySelectorAll('button'));
                    for(var b of btns){
                        var t=b.innerText.trim();
                        if((t==='Join Unitool'||t==='Sign In'||t==='Log in')&&!b.disabled){
                            b.click(); return t;
                        }
                    }
                    return 'waiting';
                })()""", return_by_value=True))
                if clicked not in ("waiting",""):
                    log(f"[login] clicked: {clicked}"); break

            # 等待 ssid cookie出现
            for t in range(20):
                await asyncio.sleep(2)
                cur_url = _s(await tab.execute_script("location.href", return_by_value=True))
                if "entry" not in cur_url and "unitool" in cur_url:
                    log(f"[login] redirected → {cur_url}"); break
                ck_raw = _s(await tab.execute_script(
                    "(document.cookie||'')", return_by_value=True))
                m = re.search(r"__Secure-unitool-ssid=([^;]+)", ck_raw)
                if m:
                    ssid = m.group(1); log(f"[login] got ssid from cookie at {t*2}s"); break
                if ssids_seen:
                    ssid = ssids_seen[-1]; log(f"[login] got ssid from request at {t*2}s"); break

            # 再从 cookies API 尝试
            if not ssid:
                try:
                    all_ck = await tab.get_cookies()
                    for c in all_ck:
                        if "unitool-ssid" in c.get("name",""):
                            ssid = c["value"]; break
                except: pass
    except BaseException as e:
        log(f"[login-pydoll] fatal: {e}")
    return ssid

# ── 主流程 ────────────────────────────────────────────────────────────────────
async def main():
    global _account_id, _success_flag
    open(LOG, "w").write("")
    log("=== unitool_reg_v2 start ===")

    # 1. 资源检查
    if not check_resources():
        log("[main] resource check fail → sleep 60s before exit")
        await asyncio.sleep(60)
        return

    # 2. 选账号
    row = get_account()
    if not row:
        log("[main] no account available → sleep 120s")
        await asyncio.sleep(120)
        return
    account_id, email, password, refresh_token = row
    _account_id = account_id
    log(f"[main] account: {email} id={account_id}")

    # 3. 立即锁定账号（防OOM崩溃后重复选）
    mark_tag(account_id, "unitool_processing")

    # 4. 选代理端口
    proxy_port = RESI_PORTS[account_id % len(RESI_PORTS)]
    log(f"[main] proxy port: {proxy_port}")

    # 5. 刷新 Graph token
    try:
        tok_resp    = refresh_ms_token(refresh_token)
        access_token = tok_resp.get("access_token","")
        log(f"[graph] token OK len={len(access_token)}")
    except Exception as e:
        log(f"[graph] token refresh fail: {e}")
        access_token = ""

    # 6. pydoll 注册流程
    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions

    opt = ChromiumOptions()
    opt.headless = False
    if CHROME: opt.binary_location = CHROME
    for a in ["--no-sandbox","--disable-dev-shm-usage","--window-size=1440,900",
               "--disable-gpu","--lang=en-US",
               f"--proxy-server=socks5://127.0.0.1:{proxy_port}"]:
        opt.add_argument(a)

    reg_submitted = False
    try:
        async with Chrome(options=opt) as browser:
            tab = await browser.start()
            await tab.enable_network_events()

            posts = []
            async def on_req(ev):
                req = ev.get("params",{}).get("request",{})
                url = req.get("url","")
                if "unitool.ai/en/entry" not in url: return
                hd  = req.get("headers",{})
                na  = hd.get("next-action") or hd.get("Next-Action","")
                body = req.get("postData","")
                if req.get("method") == "POST" and na and na != "00c396975d301f79a8208d4a593c756fdb31e4f356":
                    log(f"[POST] NA={na[:24]} body_len={len(body)}")
                    posts.append({"na": na, "body": body})

            await tab.on("Network.requestWillBeSent", on_req)

            # 6a. 加载页面
            log(f"[nav] goto https://unitool.ai/en/entry via proxy:{proxy_port}")
            await tab.go_to("https://unitool.ai/en/entry")
            await asyncio.sleep(4)

            # 6b. 检查页面是否正常加载
            body_check = _s(await tab.execute_script(
                "document.body ? document.body.innerText.slice(0,300) : 'NO_BODY'",
                return_by_value=True))
            log(f"[page] initial body: {body_check[:200].replace(chr(10),' | ')}")
            if "unitool" not in body_check.lower() and "join" not in body_check.lower():
                log("[page] WARN: page doesn't look like unitool entry page")

            # 6c. bypass Turnstile
            log("[cf] bypassing Turnstile...")
            try:
                await asyncio.wait_for(
                    tab._bypass_cloudflare({}, time_to_wait_captcha=30),
                    timeout=50
                )
                log("[cf] bypass OK")
            except asyncio.TimeoutError:
                log("[cf] bypass TIMEOUT — continuing anyway")
            except Exception as e:
                log(f"[cf] bypass err: {e}")

            # 等token
            tok_len = 0
            for i in range(25):
                await asyncio.sleep(1)
                tok_len = int(_s(await tab.execute_script(
                    "(document.querySelector('[name=\"cf-turnstile-response\"]')||{value:''}).value.length",
                    return_by_value=True)) or 0)
                if tok_len > 20:
                    log(f"[cf] token ready at {i+1}s len={tok_len}"); break
                if i % 5 == 4:
                    log(f"[cf] [{i+1}s] still waiting token (len={tok_len})")
            log(f"[cf] final token len={tok_len}")

            # 6d. 填邮箱
            r = _s(await tab.execute_script(f"""(function(){{
                var el=document.querySelector('input[name="email"]')||document.querySelector('input[type="email"]');
                if(!el) return 'NOT_FOUND';
                el.focus(); document.execCommand('selectAll'); document.execCommand('delete');
                document.execCommand('insertText',false,{json.dumps(email)});
                return el.value;
            }})()""", return_by_value=True))
            log(f"[form] email filled: {r}")

            # 6e. 填密码
            r2 = _s(await tab.execute_script(f"""(function(){{
                var el=document.querySelector('input[type="password"]');
                if(!el) return 'NOT_FOUND';
                el.focus(); document.execCommand('selectAll'); document.execCommand('delete');
                document.execCommand('insertText',false,{json.dumps(password)});
                return 'len='+el.value.length;
            }})()""", return_by_value=True))
            log(f"[form] password filled: {r2}")
            await asyncio.sleep(0.5)

            # 6f. 等按钮 enabled
            for i in range(20):
                await asyncio.sleep(1)
                btn_state = _s(await tab.execute_script("""JSON.stringify(
                    Array.from(document.querySelectorAll('button'))
                    .filter(b=>b.innerText.trim()==='Join Unitool').map(b=>b.disabled)
                )""", return_by_value=True))
                try:
                    dl = json.loads(btn_state)
                    if dl and not any(dl):
                        log(f"[form] [{i+1}s] button ENABLED"); break
                    if i % 5 == 4:
                        log(f"[form] [{i+1}s] disabled={btn_state}")
                except: pass

            # 页面当前状态快照
            snap = _s(await tab.execute_script("""JSON.stringify({
                email:(document.querySelector('input[name="email"]')||{value:''}).value,
                pwLen:(document.querySelector('input[type="password"]')||{value:''}).value.length,
                cfLen:(document.querySelector('[name="cf-turnstile-response"]')||{value:''}).value.length,
                body:document.body.innerText.slice(0,400)
            })""", return_by_value=True))
            log(f"[snap] pre-submit: {snap}")

            # 6g. 提交
            sub = _s(await tab.execute_script("""(function(){
                var btns=Array.from(document.querySelectorAll('button'));
                for(var b of btns){if(b.innerText.trim()==='Join Unitool'&&!b.disabled){b.click();return 'NATURAL';}}
                for(var b of btns){if(b.innerText.trim()==='Join Unitool'){b.disabled=false;b.click();return 'FORCE';}}
                return 'NO_BTN';
            })()""", return_by_value=True))
            log(f"[submit] result: {sub}")

            # 6h. 等待提交结果（30s）
            log("[wait] polling for post-submit state...")
            email_sent = False
            already_reg = False
            for t in range(15):
                await asyncio.sleep(2)
                cur_url = _s(await tab.execute_script("location.href", return_by_value=True))
                pg_txt  = _s(await tab.execute_script(
                    "document.body.innerText.slice(0,600)", return_by_value=True))
                low = pg_txt.lower()
                log(f"[wait] [{(t+1)*2}s] url={cur_url} body={pg_txt[:150].replace(chr(10),' | ')}")

                if "entry" not in cur_url and "unitool" in cur_url:
                    log("[!!!] immediate redirect — already registered?")
                    already_reg = True; break
                if "sent" in low or "check your email" in low or "verify your email" in low or "link to your email" in low:
                    log("[!!!] EMAIL VERIFICATION SENT"); email_sent = True; break
                if "already" in low or "email address is already" in low:
                    log("[!!] EMAIL ALREADY REGISTERED"); already_reg = True; break
                if "something went wrong" in low:
                    log("[!!] SOMETHING WENT WRONG"); break

            if posts:
                log(f"[posts] captured {len(posts)} POST(s): {[p['na'][:16] for p in posts]}")
            reg_submitted = email_sent or already_reg

        # Chrome context closed
    except BaseException as e:
        log(f"[pydoll] fatal error: {type(e).__name__}: {e}")

    log(f"[main] reg_submitted={reg_submitted}")

    if not reg_submitted:
        log("[main] registration did not complete — marking fail")
        # atexit handles marking
        return

    # 7. Graph API 轮询验证邮件（最多 180s）
    verify_url = ""
    if access_token:
        log("[graph] polling inbox for verify link (max 180s)...")
        for attempt in range(18):
            await asyncio.sleep(10)
            verify_url = find_unitool_verify_link(access_token)
            if verify_url:
                log(f"[graph] found at attempt {attempt+1}: {verify_url[:80]}"); break
            log(f"[graph] [{(attempt+1)*10}s] not yet found")
    else:
        log("[graph] no access_token, skip inbox poll")

    # 8. 点击验证链接
    ssid = ""
    if verify_url:
        log(f"[verify] clicking: {verify_url[:80]}")
        ssid = click_verify_link(verify_url)

    # 9. 回退：pydoll 登录获取 ssid
    if not ssid:
        log("[fallback] no ssid from verify link → try pydoll login")
        if check_resources():
            ssid = await pydoll_login_get_ssid(email, password, proxy_port)
        else:
            log("[fallback] resource check fail, skip pydoll login")

    # 10. 结果
    if ssid:
        log(f"[done] SUCCESS ssid_len={len(ssid)}")
        save_ssid(account_id, email, ssid)
        _success_flag = True
    else:
        log("[done] FAIL — got no ssid after all attempts")
        mark_tag(account_id, "unitool_verify_pending")
        # atexit 会清除 processing 并加 fail

    log("=== unitool_reg_v2 done ===")

asyncio.run(main())
