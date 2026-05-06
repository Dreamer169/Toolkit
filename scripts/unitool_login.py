#!/usr/bin/env python3
"""
unitool_login.py — unitool.ai 自动登录模块 v1.0
=================================================
技术要点（v20 确认）:
  - Next.js Server Action  POST /en/entry
  - Next-Action: 60e02e33f743e14f5dab1dc42181ba1e746fd4d925
  - Turnstile sitekey: 0x4AAAAAAC-pdVMpBJQaHL0Q (shadow DOM)
  - captcha_action 字段: "login"（signin tab）
  - 修复关键: SPA tab 切换后手动调 _bypass_cloudflare() 触发 shadow-root checkbox click
  - 认证 cookie: __Secure-unitool-ssid (httpOnly, Secure)

用法:
  python3 unitool_login.py --email a@b.com --password P@ssw0rd
  python3 unitool_login.py --accounts '[["a@b.com","pwd1"],["c@d.com","pwd2"]]'

输出:
  [OK]   email|password|ssid_cookie|all_cookies_json
  [FAIL] email|reason
  [DONE] ok/total
"""
import asyncio, json, os, sys, time, argparse

CHROME = None
for _p in [
    "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
    "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
]:
    if os.path.exists(_p):
        CHROME = _p
        break

TARGET       = "https://unitool.ai/en/entry"
LOGIN_NA     = "60e02e33f743e14f5dab1dc42181ba1e746fd4d925"
AUTH_COOKIE  = "__Secure-unitool-ssid"

# ── helpers ────────────────────────────────────────────────────────────────────
def _log(*a):  print(*a, flush=True, file=sys.stderr)

def _s(r):
    if not isinstance(r, dict): return str(r) if r else ""
    inner = r.get("result", r)
    if isinstance(inner, dict): inner = inner.get("result", inner)
    return str(inner.get("value", "")) if isinstance(inner, dict) else str(inner)

def _iv(r):
    try: return int(_s(r))
    except: return 0

async def _tok_len(tab, field="cf-turnstile-response") -> int:
    return _iv(await tab.execute_script(
        f"(document.querySelector('[name=\"{field}\"]')||{{value:''}}).value.length",
        return_by_value=True))

async def _get_full_token(tab, field="cf-turnstile-response") -> str:
    n = await _tok_len(tab, field)
    if n < 20: return ""
    parts = []
    for cs in range(0, n + 300, 300):
        c = _s(await tab.execute_script(
            f"(document.querySelector('[name=\"{field}\"]')||{{value:''}}).value.slice({cs},{cs+300})",
            return_by_value=True))
        if c: parts.append(c)
        if not c or cs + 300 >= n: break
    return "".join(parts)

async def _execmd_fill(tab, selector: str, value: str) -> bool:
    r = _s(await tab.execute_script(f"""
        (function(){{
            var el = document.querySelector({json.dumps(selector)});
            if (!el) return 'NOT_FOUND';
            el.focus(); el.select();
            document.execCommand('selectAll'); document.execCommand('delete');
            var ok = document.execCommand('insertText', false, {json.dumps(value)});
            return 'ok=' + ok + ' len=' + el.value.length;
        }})()
    """, return_by_value=True))
    _log(f"    fill {selector}: {r}")
    return "ok=true" in r

async def _bypass_turnstile(tab, label="", timeout=15) -> bool:
    """手动触发 shadow root 点击，返回是否成功"""
    for attempt in range(3):
        try:
            await tab._bypass_cloudflare({}, time_to_wait_captcha=timeout)
            _log(f"    [{label}] _bypass_cloudflare OK (attempt {attempt+1})")
            return True
        except Exception as e:
            _log(f"    [{label}] bypass attempt {attempt+1} err: {e}")
            await asyncio.sleep(2)
    return False

# ── 单账号登录 ─────────────────────────────────────────────────────────────────
async def login_one(email: str, password: str, headless: bool = True,
                    timeout_total: int = 120) -> dict:
    """
    Returns:
        {"ok": True,  "email": ..., "ssid": ..., "cookies": [...]}
        {"ok": False, "email": ..., "reason": ...}
    """
    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions

    opt = ChromiumOptions()
    # auto no-headless when Xvfb DISPLAY set (fixes signin Turnstile in headless mode)
    import os as _os
    if headless and _os.environ.get('DISPLAY', ''):
        headless = False
    opt.headless = headless
    if CHROME: opt.binary_location = CHROME
    for a in ["--no-sandbox", "--disable-dev-shm-usage", "--window-size=1440,900",
               "--disable-gpu", "--lang=en-US"]:
        opt.add_argument(a)

    t0 = time.time()

    async with Chrome(options=opt) as browser:
        tab = await browser.start()
        await tab.enable_network_events()

        posts = []
        async def on_req(ev):
            try:
                req = ev.get("params", {}).get("request", {})
                if "unitool.ai/en/entry" not in req.get("url", ""): return
                hd  = req.get("headers", {})
                na  = hd.get("next-action") or hd.get("Next-Action", "")
                ct  = hd.get("content-type") or hd.get("Content-Type", "")
                body= req.get("postData", "")
                if na == LOGIN_NA:
                    _log(f"    [NET] Login POST captured body_len={len(body)}")
                    posts.append({"na": na, "ct": ct, "body": body})
            except: pass

        await tab.on("Network.requestWillBeSent", on_req)

        # ── 1. 加载页面 ──────────────────────────────────────────────────────
        _log(f"  [{email}] goto {TARGET}")
        await tab.go_to(TARGET)
        await asyncio.sleep(4)

        # ── 2. bypass signup Turnstile (page load) ───────────────────────────
        _log(f"  [{email}] bypassing signup Turnstile...")
        await _bypass_turnstile(tab, "signup", timeout=15)

        # 等 signup token 确认
        for _ in range(15):
            await asyncio.sleep(1)
            if await _tok_len(tab) > 20:
                _log(f"  [{email}] signup token OK len={await _tok_len(tab)}")
                break

        # ── 3. 点击 Sign In tab ──────────────────────────────────────────────
        _log(f"  [{email}] clicking Sign In tab...")
        await tab.execute_script("""
            for (var el of document.querySelectorAll('button,[role="tab"]')) {
                if (el.innerText.trim().toLowerCase() === 'sign in') { el.click(); break; }
            }
        """, return_by_value=True)
        await asyncio.sleep(2)   # 等 signin Turnstile iframe 渲染

        # ── 4. bypass signin Turnstile (manual, SPA tab 切换无 LOAD_EVENT) ──
        _log(f"  [{email}] bypassing signin Turnstile...")
        await _bypass_turnstile(tab, "signin", timeout=12)

        # 等 signin token 出现 (captcha_action=login)
        signin_tok = ""
        for i in range(25):
            await asyncio.sleep(1)
            n  = await _tok_len(tab)
            ca = _s(await tab.execute_script(
                "(document.querySelector('[name=\"captcha_action\"]')||{value:'?'}).value",
                return_by_value=True))
            if n > 20:
                signin_tok = await _get_full_token(tab)
                _log(f"  [{email}] signin token at {i+1}s len={n} action={ca}")
                break
            if i % 5 == 4:
                _log(f"  [{email}]   [{i+1}s] waiting signin tok len={n} action={ca}")

        if not signin_tok:
            # 兜底：用当前 form 里任何 token
            n = await _tok_len(tab)
            if n > 20:
                signin_tok = await _get_full_token(tab)
                ca = _s(await tab.execute_script(
                    "(document.querySelector('[name=\"captcha_action\"]')||{value:'?'}).value",
                    return_by_value=True))
                _log(f"  [{email}] fallback token len={n} action={ca}")

        if not signin_tok:
            return {"ok": False, "email": email, "reason": "turnstile_token_empty"}

        # ── 5. 填写 email / password ─────────────────────────────────────────
        _log(f"  [{email}] filling credentials...")
        email_sel = 'input[name="email"]'
        pw_sel    = 'input[type="password"]'

        ok_email = await _execmd_fill(tab, email_sel, email)
        if not ok_email:
            ok_email = await _execmd_fill(tab, 'input[type="email"]', email)
        await asyncio.sleep(0.3)

        ok_pw = await _execmd_fill(tab, pw_sel, password)
        await asyncio.sleep(0.3)

        if not (ok_email and ok_pw):
            return {"ok": False, "email": email, "reason": "fill_failed"}

        # ── 6. 等按钮自然 enabled，最多 12s ────────────────────────────────
        btn_natural = False
        for i in range(12):
            await asyncio.sleep(1)
            r = _s(await tab.execute_script("""JSON.stringify(
                Array.from(document.querySelectorAll('button'))
                .filter(b => b.innerText.trim().toLowerCase() === 'sign in')
                .map(b => b.disabled)
            )""", return_by_value=True))
            try:
                if not any(json.loads(r)):
                    btn_natural = True
                    _log(f"  [{email}] button naturally enabled at {i+1}s")
                    break
            except: pass

        # ── 7. 提交 ─────────────────────────────────────────────────────────
        _log(f"  [{email}] submitting (natural={btn_natural})...")
        sub = _s(await tab.execute_script("""(function() {
            var btns = Array.from(document.querySelectorAll('button'));
            for (var b of btns) {
                if (b.innerText.trim().toLowerCase() === 'sign in' && !b.disabled)
                    { b.click(); return 'NATURAL:' + b.type; }
            }
            for (var b of btns) {
                if (b.innerText.trim().toLowerCase() === 'sign in')
                    { b.disabled = false; b.click(); return 'FORCE:' + b.type; }
            }
            return 'NO_BTN';
        })()""", return_by_value=True))
        _log(f"  [{email}] submit: {sub}")

        # ── 8. 等待重定向（最多 45s） ────────────────────────────────────────
        logged_in = False
        for t in range(45):
            await asyncio.sleep(1)
            cur = _s(await tab.execute_script("location.href", return_by_value=True))
            if "entry" not in cur and "unitool.ai" in cur:
                _log(f"  [{email}] REDIRECT → {cur}")
                logged_in = True
                break
            if t % 10 == 9:
                pg_body = _s(await tab.execute_script(
                    "(document.body||{}).innerText?.slice(0,200)", return_by_value=True))
                _log(f"  [{email}]   [{t+1}s] url={cur}")
                if "captcha" in pg_body.lower():
                    _log(f"  [{email}]   still captcha error")
                if "wrong" in pg_body.lower() or "incorrect" in pg_body.lower():
                    _log(f"  [{email}]   credentials rejected")
                    return {"ok": False, "email": email, "reason": "invalid_credentials"}

        if not logged_in:
            # 再检查一次 body 内容
            pg_body = _s(await tab.execute_script(
                "(document.body||{}).innerText?.slice(0,300)", return_by_value=True))
            if "captcha" in pg_body.lower():
                return {"ok": False, "email": email, "reason": "captcha_rejected"}
            return {"ok": False, "email": email, "reason": "login_timeout"}

        # ── 9. 提取 cookies ──────────────────────────────────────────────────
        all_ck = await tab.get_cookies()
        ut_ck  = [c for c in all_ck if "unitool" in c.get("domain", "")]
        ssid   = next((c["value"] for c in ut_ck if c.get("name") == AUTH_COOKIE), "")

        elapsed = round(time.time() - t0, 1)
        _log(f"  [{email}] SUCCESS in {elapsed}s ssid_len={len(ssid)}")
        return {
            "ok":      True,
            "email":   email,
            "ssid":    ssid,
            "cookies": ut_ck,
            "elapsed": elapsed,
        }

# ── 批量入口 ────────────────────────────────────────────────────────────────────
async def run_batch(accounts: list, headless: bool = True):
    ok_count = 0
    for email, password in accounts:
        result = await login_one(email, password, headless=headless)
        if result["ok"]:
            ok_count += 1
            ck_json = json.dumps(result["cookies"])
            print(f"[OK]   {email}|{password}|{result['ssid']}|{ck_json}", flush=True)
            try:
                open('/tmp/unitool_ssid.txt', 'w').write(result['ssid'])
            except Exception: pass
        else:
            print(f"[FAIL] {email}|{result['reason']}", flush=True)
        await asyncio.sleep(1)
    print(f"[DONE] {ok_count}/{len(accounts)}", flush=True)

# ── CLI ─────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="unitool.ai 自动登录")
    ap.add_argument("--email",    default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--accounts", default="",
                    help='JSON: [["email","pwd"], ...]')
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--no-headless", dest="headless", action="store_false")
    args = ap.parse_args()

    if args.accounts:
        accounts = json.loads(args.accounts)
    elif args.email:
        accounts = [[args.email, args.password]]
    else:
        ap.print_help(); sys.exit(1)

    asyncio.run(run_batch(accounts, headless=args.headless))
