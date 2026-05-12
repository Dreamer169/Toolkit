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
import asyncio, random, json, os, sys, time, socket, argparse

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
# 住宅代理端口列表（与 chain_v3 保持一致）
RESI_PORTS = [10851, 10853, 10854, 10855, 10857, 10859]  # Fix-6a+6d: 10852,10856,10858 dead

# v5.14: RESI port health check cache (valid 5 min)
_resi_healthy_ports: list = []
_resi_health_ts: float = 0.0

def _check_resi_port(port: int) -> bool:
    """Quick test: can this SOCKS5 port actually proxy HTTPS to unitool.ai?"""
    try:
        proc = subprocess.Popen(
            ["curl", "-s", "--max-time", "5",
             "--proxy", f"socks5h://127.0.0.1:{port}",
             "-o", "/dev/null", "-w", "%{{http_code}}",
             "https://unitool.ai/en/entry"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, _ = proc.communicate(timeout=7)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.communicate(); return False
        return out.decode().strip() not in ("", "000")
    except Exception:
        return False

def _get_healthy_resi_ports() -> list:
    """All ports checked in parallel; max wall-time ~7s instead of 32s."""
    global _resi_healthy_ports, _resi_health_ts
    import time as _t, concurrent.futures
    if _resi_healthy_ports and _t.time() - _resi_health_ts < 300:
        return _resi_healthy_ports
    t0 = _t.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(RESI_PORTS)) as _ex:
        _ok = list(_ex.map(_check_resi_port, RESI_PORTS))
    healthy = [p for p, ok in zip(RESI_PORTS, _ok) if ok]
    if not healthy:
        healthy = [10851, 10853, 10854, 10855, 10857, 10859]  # Fix-6d: exclude dead 10852,10856,10858
    _resi_healthy_ports = healthy
    _resi_health_ts = _t.time()
    print(f"[RESI] healthy={healthy} ({len(healthy)}/{len(RESI_PORTS)}) in {_t.time()-t0:.1f}s", flush=True)
    return healthy

def _pick_resi_port(email: str) -> int:
    """Pick RESI port from healthy set; falls back to full list if none pass check."""
    ports = _get_healthy_resi_ports()
    return ports[hash(email) % len(ports)]


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


def _free_port(lo: int = 12000, hi: int = 29999) -> int:
    tried = set()
    while len(tried) < (hi - lo):
        p = random.randint(lo, hi)
        if p in tried:
            continue
        tried.add(p)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if s.connect_ex(('127.0.0.1', p)) != 0:
                return p
    raise RuntimeError('no free port found')

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

# ── Turnstile postMessage interceptor JS (module-level) ──────────────────────
# Injected into every tab before navigation. Captures CF Turnstile token from
# iframe→parent postMessage and populates the hidden input field.
_PM_HOOK_JS = (
    "(function(){"
    "if(window.__cf_pm_hooked)return;"
    "window.__cf_pm_hooked=true;"
    "window.__cf_captured_token='';"
    "window.addEventListener('message',function(ev){"
    "try{"
    "var d=ev.data;"
    "if(typeof d==='string'){try{d=JSON.parse(d);}catch(e){}}"
    "var tok='';"
    "if(d&&typeof d==='object'){"
    "tok=d.token||d.cf_token||d.turnstileToken||d.response||'';}"
    "if(!tok&&typeof ev.data==='string'&&ev.data.length>80)tok=ev.data;"
    "if(tok&&tok.length>20&&window.__cf_captured_token.length<20){"
    "window.__cf_captured_token=tok;"
    "var inp=document.querySelector('[name=\\\"cf-turnstile-response\\\"]');"
    "if(inp&&(!inp.value||inp.value.length<20)){"
    "try{"
    "var s=Object.getOwnPropertyDescriptor("
    "window.HTMLInputElement.prototype,'value').set;"
    "s.call(inp,tok);"
    "inp.dispatchEvent(new Event('input',{bubbles:true}));"
    "inp.dispatchEvent(new Event('change',{bubbles:true}));"
    "}catch(e){inp.value=tok;}}}"
    "}catch(e){}},true);})();"
)


async def _inject_pm_hook(tab):
    """注入 postMessage 拦截器（幂等，多次调用安全）"""
    try:
        await tab.execute_script(_PM_HOOK_JS, return_by_value=True)
    except Exception:
        pass


async def _wait_natural_token(tab, label="", field="cf-turnstile-response",
                               captcha_action="", max_wait=30) -> str:
    """
    等待 Turnstile token 自然出现（Invisible 自动求解 / postMessage 回传）。
    使用已有的 _tok_len/_get_full_token helpers（避免内联 JS 引号问题）。
    captcha_action 非空时校验 input[name=captcha_action] 值匹配。
    返回 token 字符串（空串 = 超时）。
    """
    for i in range(max_wait):
        await asyncio.sleep(1)
        n = await _tok_len(tab, field)          # uses existing helper with correct escaping
        if n > 20:
            if captcha_action:
                ca = _s(await tab.execute_script(
                    "(document.querySelector('[name=\"captcha_action\"]')||{value:'?'}).value",
                    return_by_value=True))
                if ca != captcha_action:
                    if i % 8 == 7:
                        _log(f"    [{label}] [{i+1}s] token len={n} action={ca} (want {captcha_action})")
                    continue
            tok = await _get_full_token(tab, field)
            _log(f"    [{label}] natural token at {i+1}s len={len(tok)}")
            return tok
        try:
            pm_tok = _s(await tab.execute_script(
                "window.__cf_captured_token||''", return_by_value=True))
        except Exception:
            pm_tok = ""
        if len(pm_tok) > 20:
            _log(f"    [{label}] postMessage token at {i+1}s len={len(pm_tok)}")
            return pm_tok
        if i % 10 == 9:
            _log(f"    [{label}] [{i+1}s] waiting token (len={n})...")
    return ""


async def _bypass_turnstile(tab, label="", timeout=35) -> bool:
    """
    Turnstile bypass v4 — Invisible mode first, Managed checkbox as fallback.

    Invisible Turnstile (unitool current mode):
      CF auto-solves based on browser fingerprint → token appears in hidden input.
      We just wait for it; no user interaction or span.cb-i click needed.

    Managed Turnstile (old checkbox mode):
      Requires pydoll._bypass_cloudflare to click span.cb-i in shadow DOM.
      Used only as fallback if token doesn't appear within ~22s.

    Steps:
      1. Inject postMessage listener (idempotent)
      2. Wait for natural token (invisible mode, up to 22s)
      3. If no token: try pydoll managed bypass (checkbox)
      4. Final wait 8s
    """
    await _inject_pm_hook(tab)

    # Phase 1: wait for natural/invisible token
    tok = await _wait_natural_token(tab, label=label, max_wait=22)
    if tok:
        return True

    # Phase 2: managed bypass fallback (checkbox / span.cb-i)
    for attempt in range(2):
        try:
            await tab._bypass_cloudflare({}, time_to_wait_captcha=8)
            _log(f"    [{label}] managed bypass OK (attempt {attempt+1})")
        except Exception as e:
            em = str(e)
            if any(k in em for k in ("cb-i", "shadow root", "Timed out")):
                _log(f"    [{label}] managed N/A (invisible mode): {em[:60]}")
                break
            _log(f"    [{label}] managed err {attempt+1}: {em[:80]}")
            await asyncio.sleep(2)

    # Phase 3: final wait using existing helper
    for i in range(8):
        await asyncio.sleep(1)
        n = await _tok_len(tab)                 # uses existing helper
        if n > 20:
            _log(f"    [{label}] late token at +{i+1}s len={n}")
            return True
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
    # Auto-detect Xvfb and force non-headless (Turnstile needs real browser)
    if headless:
        display = _os.environ.get('DISPLAY', '')
        if not display:
            # detect Xvfb on :99
            import glob as _glob
            if _glob.glob('/tmp/.X99-lock') or _glob.glob('/tmp/.X[0-9]-lock'):
                display = ':99'
                _os.environ['DISPLAY'] = display
                _log(f"  [display] auto-set DISPLAY={display}")
        if display:
            headless = False
            _log(f"  [display] headless=False (DISPLAY={display})")
    opt.headless = headless
    if CHROME: opt.binary_location = CHROME
    _resi_port = _pick_resi_port(email)
    _log(f"  [{email}] 住宅代理端口: {_resi_port}")
    for a in ["--no-sandbox", "--disable-dev-shm-usage", "--window-size=1440,900",
               "--disable-gpu", "--lang=en-US",
               f"--proxy-server=socks5://127.0.0.1:{_resi_port}"]:
        opt.add_argument(a)

    t0 = time.time()

    async with Chrome(options=opt, connection_port=_free_port()) as browser:
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
        # Inject postMessage interceptor before page navigates (catches CF token early)
        await _inject_pm_hook(tab)
        try:
            await tab.go_to(TARGET)
        except Exception as _nav_e:
            _log(f"  [{email}] go_to FAILED: {_nav_e}")
            return {"ok": False, "email": email, "reason": "navigation_timeout"}
        await asyncio.sleep(4)
        # Re-inject after navigation (page reload clears window state)
        await _inject_pm_hook(tab)

        # ── 2. bypass signup Turnstile (page load) ───────────────────────────
        _log(f"  [{email}] bypassing signup Turnstile...")
        await _bypass_turnstile(tab, "signup", timeout=35)

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

        # 等 signin token 出现 (captcha_action=login) — 带重试 bypass（最多3轮）
        signin_tok = ""
        for _bypass_round in range(3):
            if _bypass_round > 0:
                _log(f"  [{email}] [round {_bypass_round+1}] retry bypass signin Turnstile...")
                await _bypass_turnstile(tab, f"signin-r{_bypass_round+1}", timeout=12)
            await asyncio.sleep(3)
            for i in range(15):
                await asyncio.sleep(1)
                n  = await _tok_len(tab)
                ca = _s(await tab.execute_script(
                    "(document.querySelector('[name=\"captcha_action\"]')||{value:'?'}).value",
                    return_by_value=True))
                if n > 20 and ca == "login":
                    signin_tok = await _get_full_token(tab)
                    _log(f"  [{email}] signin token at round={_bypass_round+1} i={i+1}s len={n} action={ca}")
                    break
                if i % 5 == 4:
                    _log(f"  [{email}]   [r{_bypass_round+1} {i+1}s] waiting signin tok len={n} action={ca}")
            if signin_tok:
                break

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
                _log(f"  [{email}]   pg_body: {pg_body[:150]!r}")
                if "captcha" in pg_body.lower():
                    _log(f"  [{email}]   still captcha error")
                if "wrong" in pg_body.lower() or "incorrect" in pg_body.lower():
                    _log(f"  [{email}]   credentials rejected")
                    return {"ok": False, "email": email, "reason": "invalid_credentials"}
                if any(kw in pg_body.lower() for kw in (
                        "verify your email", "confirm your", "check your email",
                        "email not verified", "please verify", "activation link",
                        "we have just sent", "sent link to your email")):
                    _log(f"  [{email}]   email verification required (early detect)")
                    return {"ok": False, "email": email, "reason": "email_not_verified"}
                # Fix-6b: home page content at /en/entry = SPA redirected silently (email unverified)
                if any(kw in pg_body.lower() for kw in (
                        "get 10 tokens", "tokens for registration",
                        "inviting friends", "join thousands", "chatgpt")):
                    _log(f"  [{email}]   home page at entry = email not verified (SPA redirect)")
                    return {"ok": False, "email": email, "reason": "email_not_verified"}

        if not logged_in:
            # 再检查一次 body 内容
            pg_body = _s(await tab.execute_script(
                "(document.body||{}).innerText?.slice(0,300)", return_by_value=True))
            _log(f"  [{email}] final pg_body: {pg_body[:200]!r}")
            if "captcha" in pg_body.lower():
                return {"ok": False, "email": email, "reason": "captcha_rejected"}
            if any(kw in pg_body.lower() for kw in (
                    "verify your email", "confirm your", "check your email",
                    "email not verified", "please verify", "activation link",
                    "we have just sent", "sent link to your email")):
                return {"ok": False, "email": email, "reason": "email_not_verified"}
            # Fix-6b: home page shown at /en/entry = email not verified (SPA silent redirect)
            if any(kw in pg_body.lower() for kw in (
                    "get 10 tokens", "tokens for registration",
                    "inviting friends", "join thousands", "chatgpt")):
                return {"ok": False, "email": email, "reason": "email_not_verified"}
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
                # Also write numbered file + push to proxy /add-ssid
                try:
                    import urllib.request as _ur
                    _numbered = f"/tmp/unitool_ssid{ok_count}.txt" if ok_count > 1 else "/tmp/unitool_ssid.txt"
                    open(_numbered, "w").write(result["ssid"])
                    _jdata = __import__("json").dumps({"ssid": result["ssid"], "label": email}).encode()
                    _req = _ur.Request("http://localhost:8089/add-ssid", data=_jdata,
                                       headers={"Content-Type": "application/json"})
                    with _ur.urlopen(_req, timeout=3) as _r:
                        _resp = __import__("json").loads(_r.read())
                    print(f"[POOL] pushed to proxy: {_resp}", flush=True)
                except Exception as _pe:
                    print(f"[POOL] warn push: {_pe}", flush=True)
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
