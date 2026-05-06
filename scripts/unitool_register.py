#!/usr/bin/env python3
"""
unitool_register.py — unitool.ai 全流程注册 v1.0
==============================================
流程:
  1. 从 DB 取未注册 outlook 账号（有 refresh_token）
  2. pydoll 打开 unitool.ai/en/entry，Turnstile bypass，填写信息，提交注册
  3. Graph API 同时轮询 Inbox + JunkEmail 找验证邮件（修复：原只搜索 inbox）
  4. 点击验证链接，提取 ssid cookie，写入 DB tags

输出:
  [OK]   email|ssid
  [FAIL] email|reason
  [DONE] ok/total
"""
import asyncio, json, os, sys, time, argparse, re
import urllib.request, urllib.parse

# ── Chrome 路径 ────────────────────────────────────────────────────────────────
CHROME = None
for _p in [
    "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
    "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
]:
    if os.path.exists(_p):
        CHROME = _p; break

TARGET   = "https://unitool.ai/en/entry"
AUTH_COOKIE = "__Secure-unitool-ssid"
DB_URL   = "postgresql://postgres:postgres@localhost/toolkit"

def log(*a): print(*a, flush=True)

# ── DB helpers ────────────────────────────────────────────────────────────────
def db_get_account(email=None):
    import psycopg2
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    if email:
        cur.execute(
            "SELECT id,email,password,refresh_token FROM accounts WHERE email=%s", (email,))
    else:
        cur.execute("""
            SELECT id,email,password,refresh_token FROM accounts
            WHERE platform='outlook' AND status='active'
              AND (tags IS NULL OR tags NOT LIKE '%unitool%')
              AND refresh_token IS NOT NULL AND refresh_token != ''
            ORDER BY id LIMIT 1
        """)
    row = cur.fetchone(); conn.close(); return row

def db_tag_unitool(account_id: int, ssid: str):
    import psycopg2
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute("""
        UPDATE accounts SET
          tags = CASE WHEN tags IS NULL THEN 'unitool'
                      WHEN tags NOT LIKE '%unitool%' THEN tags || ',unitool'
                      ELSE tags END,
          notes = COALESCE(notes,'') || ' | unitool_ssid=' || %s
        WHERE id = %s
    """, (ssid[:80], account_id))
    conn.commit(); conn.close()

# ── Graph API helpers ─────────────────────────────────────────────────────────
CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"

def _refresh_token(refresh_token: str) -> str:
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "grant_type": "refresh_token",
        "refresh_token": refresh_token, "scope": "offline_access Mail.Read",
    }).encode()
    req = urllib.request.Request(
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
    if "access_token" not in resp:
        raise ValueError(f"token refresh failed: {resp}")
    return resp["access_token"]

def _graph_get(path: str, token: str) -> dict:
    if "?" in path:
        base, qs = path.split("?", 1)
        qs = urllib.parse.quote(qs, safe="=&$/'")
        url = f"https://graph.microsoft.com/v1.0{base}?{qs}"
    else:
        url = f"https://graph.microsoft.com/v1.0{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())

UNITOOL_KWS     = ("unitool", "verify", "confirm", "activate", "email")
UNITOOL_SENDERS = ("unitool.ai", "noreply", "no-reply", "support")

def _extract_verify_url(html: str) -> str | None:
    candidates = re.findall(r'https?://[^\s"\'<>\)]+', html)
    priority = ("unitool.ai/en/verify", "unitool.ai/verify", "unitool.ai/confirm",
                "unitool.ai/activate", "unitool.ai/email", "unitool.ai/en/entry?token",
                "unitool.ai/en/entry?verify")
    fallback = ("unitool", "verify", "confirm", "activate", "token")
    for url in candidates:
        u = url.lower()
        if any(k in u for k in priority):
            return url.rstrip(".,)")
    for url in candidates:
        u = url.lower()
        if "unitool.ai" in u and any(k in u for k in fallback):
            return url.rstrip(".,)")
    for url in candidates:
        if any(k in url.lower() for k in fallback) and len(url) > 60:
            return url.rstrip(".,)")
    return None

def wait_for_unitool_verify(refresh_token: str, timeout: int = 300,
                             after_ts: float | None = None) -> str | None:
    """
    同时轮询 Inbox + JunkEmail（修复：unitool 验证邮件落入垃圾邮件）
    返回验证 URL 或 None
    """
    start_ts = after_ts or (time.time() - 30)
    deadline  = time.time() + timeout
    seen_ids: set = set()

    log("[graph] 获取 access_token…")
    try:
        token = _refresh_token(refresh_token)
    except Exception as e:
        log(f"[graph] ❌ token 刷新失败: {e}"); return None

    log(f"[graph] 开始轮询 Inbox + JunkEmail (最多 {timeout}s)…")
    poll = 0
    while time.time() < deadline:
        poll += 1
        for folder in ("Inbox", "JunkEmail"):
            try:
                msgs = _graph_get(
                    f"/me/mailFolders/{folder}/messages"
                    "?$top=20&$orderby=receivedDateTime+desc"
                    "&$select=id,subject,bodyPreview,receivedDateTime,from",
                    token)
                for msg in msgs.get("value", []):
                    mid = msg["id"]
                    if mid in seen_ids: continue

                    recv_str = msg.get("receivedDateTime", "")
                    if recv_str:
                        try:
                            from datetime import datetime
                            recv_ts = datetime.fromisoformat(
                                recv_str.replace("Z", "+00:00")).timestamp()
                            if recv_ts < start_ts - 60:
                                seen_ids.add(mid); continue
                        except Exception: pass

                    subj   = msg.get("subject", "").lower()
                    prev   = msg.get("bodyPreview", "").lower()
                    sender = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()

                    hit = (any(k in subj for k in UNITOOL_KWS)
                           or any(k in prev for k in UNITOOL_KWS)
                           or any(s in sender for s in UNITOOL_SENDERS)
                           or "unitool" in sender)
                    if not hit:
                        seen_ids.add(mid); continue

                    log(f"[graph] 📧 [{folder}] {msg.get('subject','')} from={sender}")
                    detail = _graph_get(f"/me/messages/{mid}?$select=body", token)
                    body   = detail.get("body", {}).get("content", "") or ""
                    url    = _extract_verify_url(body)
                    if url:
                        log(f"[graph] ✅ [{folder}] 验证链接: {url[:100]}")
                        return url
                    seen_ids.add(mid)
            except Exception as e:
                log(f"[graph] {folder} 查询失败: {e}")

        log(f"[graph] poll#{poll} 未找到，等 10s…")
        time.sleep(10)
        try:
            token = _refresh_token(refresh_token)
        except Exception: pass

    log("[graph] ⏰ 超时"); return None

# ── pydoll helpers ─────────────────────────────────────────────────────────────
def _s(r):
    if not isinstance(r, dict): return str(r) if r else ""
    inner = r.get("result", r)
    if isinstance(inner, dict): inner = inner.get("result", inner)
    return str(inner.get("value", "")) if isinstance(inner, dict) else str(inner)

async def _fill(tab, sel: str, val: str) -> bool:
    r = _s(await tab.execute_script(f"""(function(){{
        var el=document.querySelector({json.dumps(sel)});
        if(!el) return 'NOT_FOUND';
        el.focus(); document.execCommand('selectAll'); document.execCommand('delete');
        var ok=document.execCommand('insertText',false,{json.dumps(val)});
        return 'ok='+ok+' len='+el.value.length;
    }})()""", return_by_value=True))
    log(f"  fill {sel}: {r}"); return "ok=true" in r

async def _bypass(tab, label="", timeout=18):
    for att in range(3):
        try:
            await tab._bypass_cloudflare({}, time_to_wait_captcha=timeout)
            log(f"  [{label}] bypass OK"); return True
        except Exception as e:
            log(f"  [{label}] bypass att{att+1}: {e}")
            await asyncio.sleep(2)
    return False

async def _tok_len(tab) -> int:
    try:
        return int(_s(await tab.execute_script(
            "(document.querySelector('[name=\"cf-turnstile-response\"]')||{value:''}).value.length",
            return_by_value=True)))
    except: return 0

# ── 点击验证链接 ───────────────────────────────────────────────────────────────
async def click_verify(tab, url: str) -> bool:
    log(f"  [verify] 打开: {url[:100]}")
    try:
        await tab.go_to(url)
        await asyncio.sleep(5)
        page_txt = _s(await tab.execute_script(
            "document.body ? document.body.innerText.slice(0,400) : ''", return_by_value=True))
        log(f"  [verify] body: {page_txt[:200]}")
        low = page_txt.lower()
        if any(k in low for k in ("verified", "success", "confirmed", "welcome", "dashboard", "activated")):
            return True
        # 已验证也算成功
        if any(k in low for k in ("already verified", "already confirmed", "already activated")):
            return True
        return False
    except Exception as e:
        log(f"  [verify] err: {e}"); return False

# ── 单账号注册 ────────────────────────────────────────────────────────────────
async def register_one(email: str, password: str, refresh_token: str,
                        headless: bool = False) -> dict:
    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions

    opt = ChromiumOptions()
    opt.headless = headless
    if CHROME: opt.binary_location = CHROME
    for a in ["--no-sandbox","--disable-dev-shm-usage","--window-size=1440,900",
               "--disable-gpu","--lang=en-US","--disable-blink-features=AutomationControlled"]:
        opt.add_argument(a)

    reg_start = time.time()

    async with Chrome(options=opt) as browser:
        tab = await browser.start()
        await tab.enable_network_events()

        # Track submission result
        submitted_na = None
        async def on_req(ev):
            nonlocal submitted_na
            try:
                req = ev.get("params",{}).get("request",{})
                if "unitool.ai/en/entry" not in req.get("url",""): return
                hd = req.get("headers",{})
                na = hd.get("next-action") or hd.get("Next-Action","")
                # Known signup NA patterns (not login NA)
                if na and na != "60e02e33f743e14f5dab1dc42181ba1e746fd4d925":
                    submitted_na = na
                    log(f"  [net] signup POST NA={na[:20]}")
            except: pass
        await tab.on("Network.requestWillBeSent", on_req)

        # ── 1. 加载页面 ──────────────────────────────────────────────────────
        log(f"[{email}] 打开 {TARGET}")
        await tab.go_to(TARGET)
        await asyncio.sleep(4)

        # ── 2. bypass 初始 Turnstile（signup tab） ───────────────────────────
        log(f"[{email}] bypass signup Turnstile…")
        await _bypass(tab, "signup", timeout=20)
        for _ in range(15):
            await asyncio.sleep(1)
            if await _tok_len(tab) > 20:
                log(f"[{email}] signup token ready len={await _tok_len(tab)}")
                break

        # ── 3. 确认在 New account tab ───────────────────────────────────────
        # 检查是否需要点 "New account" tab
        tab_clicked = _s(await tab.execute_script("""(function(){
            for(var b of document.querySelectorAll('button,[role="tab"]')){
                var t=b.innerText.trim().toLowerCase();
                if(t==='new account'){b.click();return 'clicked:'+t;}
            }
            return 'no-tab-needed';
        })()""", return_by_value=True))
        log(f"[{email}] tab: {tab_clicked}")
        if "clicked" in tab_clicked:
            await asyncio.sleep(2)
            await _bypass(tab, "new-account", timeout=15)

        await asyncio.sleep(1)

        # ── 4. 填写 email / password ─────────────────────────────────────────
        log(f"[{email}] 填写注册信息…")
        ok_e = await _fill(tab, 'input[name="email"]', email)
        if not ok_e: ok_e = await _fill(tab, 'input[type="email"]', email)

        ok_p = await _fill(tab, 'input[type="password"]', password)
        await asyncio.sleep(0.5)

        if not (ok_e and ok_p):
            return {"ok": False, "email": email, "reason": "fill_failed"}

        # ── 5. 等 Join Unitool 按钮 enabled ──────────────────────────────────
        for i in range(20):
            await asyncio.sleep(1)
            r = _s(await tab.execute_script("""JSON.stringify(
                Array.from(document.querySelectorAll('button'))
                .filter(b=>['join unitool','sign up','create account'].includes(b.innerText.trim().toLowerCase()))
                .map(b=>b.disabled)
            )""", return_by_value=True))
            try:
                dl = json.loads(r)
                if dl and not any(dl):
                    log(f"[{email}] button enabled at {i+1}s"); break
            except: pass

        # ── 6. 截图 page state ───────────────────────────────────────────────
        pg = _s(await tab.execute_script("""JSON.stringify({
            cfLen:(document.querySelector('[name="cf-turnstile-response"]')||{value:''}).value.length,
            ca:(document.querySelector('[name="captcha_action"]')||{value:'?'}).value,
            body:document.body.innerText.slice(0,300)
        })""", return_by_value=True))
        log(f"[{email}] page state: {pg[:400]}")

        # ── 7. 提交 ──────────────────────────────────────────────────────────
        sub = _s(await tab.execute_script("""(function(){
            var kws=['join unitool','sign up','create account','register'];
            var btns=Array.from(document.querySelectorAll('button'));
            for(var b of btns){if(kws.includes(b.innerText.trim().toLowerCase())&&!b.disabled){b.click();return 'NATURAL';}}
            for(var b of btns){if(kws.includes(b.innerText.trim().toLowerCase())){b.disabled=false;b.click();return 'FORCE';}}
            var form=document.querySelector('form');
            if(form){form.requestSubmit();return 'FORM_SUBMIT';}
            return 'NO_BTN';
        })()""", return_by_value=True))
        log(f"[{email}] submit: {sub}")

        # ── 8. 等待结果（检查 URL 变化 or "verify email" 提示） ──────────────
        needs_verify = False
        reg_success  = False
        already_reg  = False
        for t in range(30):
            await asyncio.sleep(2)
            cur_url = _s(await tab.execute_script("location.href", return_by_value=True))
            body_txt = _s(await tab.execute_script(
                "document.body ? document.body.innerText.slice(0,600) : ''",
                return_by_value=True))
            low = body_txt.lower()
            log(f"[{email}] [{(t+1)*2}s] url={cur_url[:80]}")

            if "entry" not in cur_url and "unitool.ai" in cur_url:
                log(f"[{email}] ✅ 重定向成功: {cur_url}"); reg_success = True; break
            if any(k in low for k in ("verify your email","check your email","verification email","sent you an email")):
                log(f"[{email}] 📧 需要邮件验证"); needs_verify = True; break
            if "already" in low and ("registered" in low or "exists" in low or "email" in low):
                log(f"[{email}] ⚠ 邮箱已注册"); already_reg = True; break
            if "something went wrong" in low or "error" in low:
                log(f"[{email}] ❌ 错误: {body_txt[:200]}"); break

        if already_reg:
            return {"ok": False, "email": email, "reason": "already_registered"}

        # ── 9. 获取 cookies ──────────────────────────────────────────────────
        all_ck = await tab.get_cookies()
        ut_ck  = [c for c in all_ck if "unitool" in c.get("domain","")]
        ssid   = next((c["value"] for c in ut_ck if c.get("name") == AUTH_COOKIE), "")

        if ssid:
            log(f"[{email}] ✅ 注册成功，ssid_len={len(ssid)}")
            return {"ok": True, "email": email, "ssid": ssid, "needs_verify": needs_verify}

        if not needs_verify and not reg_success:
            return {"ok": False, "email": email, "reason": "no_redirect_no_ssid"}

        # ── 10. 邮件验证流程（Inbox + JunkEmail） ────────────────────────────
        log(f"[{email}] 🔍 等待验证邮件（同时搜索 Inbox + JunkEmail）…")
        verify_url = wait_for_unitool_verify(
            refresh_token, timeout=300, after_ts=reg_start - 10)

        if not verify_url:
            return {"ok": False, "email": email, "reason": "verify_email_not_found"}

        log(f"[{email}] 点击验证链接: {verify_url[:100]}")
        ok_v = await click_verify(tab, verify_url)
        log(f"[{email}] verify result: {ok_v}")

        # 再次获取 cookies
        all_ck2 = await tab.get_cookies()
        ut_ck2  = [c for c in all_ck2 if "unitool" in c.get("domain","")]
        ssid2   = next((c["value"] for c in ut_ck2 if c.get("name") == AUTH_COOKIE), "")

        if ssid2:
            return {"ok": True, "email": email, "ssid": ssid2, "verified": True}
        if ok_v:
            return {"ok": True, "email": email, "ssid": "", "verified": True, "note": "no_ssid_after_verify"}
        return {"ok": False, "email": email, "reason": "verify_click_failed"}

# ── CLI ────────────────────────────────────────────────────────────────────────
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email",    default="")
    ap.add_argument("--count",    type=int, default=1)
    ap.add_argument("--headless", action="store_true", default=False)
    args = ap.parse_args()

    ok_count = 0
    for _ in range(args.count):
        row = db_get_account(args.email if args.email else None)
        if not row:
            log("[main] ❌ 没有可用账号"); break
        acc_id, email, password, refresh_token = row

        log(f"\n{'='*60}")
        log(f"[main] 使用账号: {email}")
        result = await register_one(email, password, refresh_token,
                                    headless=args.headless)
        if result["ok"]:
            ok_count += 1
            ssid = result.get("ssid","")
            if ssid:
                try: db_tag_unitool(acc_id, ssid)
                except Exception as e: log(f"[DB] tag err: {e}")
                try: open('/tmp/unitool_ssid2.txt','w').write(ssid)
                except Exception: pass
            print(f"[OK]   {email}|{ssid}", flush=True)
        else:
            print(f"[FAIL] {email}|{result.get('reason','?')}", flush=True)
        await asyncio.sleep(2)

    print(f"[DONE] {ok_count}/{args.count}", flush=True)

asyncio.run(main())
