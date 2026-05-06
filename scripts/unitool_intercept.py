#!/usr/bin/env python3
"""
unitool.ai 登录拦截 v12
KEY FIX: After injecting signup token, also set captcha_action="signup"
to match the token's action (CF siteverify checks action field in token).

Full body format now known:
1_email, 1_password, 1_cf-turnstile-response, 1_captcha_token, 1_captcha_action, "0"=[state]
"""
import asyncio, json, os

EMAIL    = "penelopefwf303@outlook.com"
PASSWORD = "i0Rf*7E9HN1^"
TARGET   = "https://unitool.ai/en/entry"
LOGIN_NEXT_ACTION = "60e02e33f743e14f5dab1dc42181ba1e746fd4d925"

CHROME_BIN = None
for p in [
    "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
    "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
]:
    if os.path.exists(p):
        CHROME_BIN = p; break

print(f"[*] Chrome: {CHROME_BIN}", flush=True)

captured = []

async def main():
    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions

    options = ChromiumOptions()
    options.headless = False
    if CHROME_BIN:
        options.binary_location = CHROME_BIN
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1440,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-blink-features=AutomationControlled")

    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.enable_auto_solve_cloudflare_captcha()
        await tab.enable_network_events()
        print("[*] CF auto-solve + non-headless + network events", flush=True)

        async def on_req(event):
            try:
                req  = event.get("params",{}).get("request",{})
                url  = req.get("url","")
                body = req.get("postData","")
                hdrs = req.get("headers",{})
                if "unitool.ai" not in url: return
                na = hdrs.get("next-action", hdrs.get("Next-Action",""))
                ct = hdrs.get("content-type", hdrs.get("Content-Type",""))
                meth = req.get("method","")
                if meth in ("POST","PUT","PATCH"):
                    print(f"  [POST] {url[:80]}", flush=True)
                    if na: print(f"         NA: {na}", flush=True)
                    if ct: print(f"         CT: {ct[:70]}", flush=True)
                    if body: print(f"         body({len(body)}): {body[:600]}", flush=True)
                    captured.append({"url":url,"body":body,"na":na,"ct":ct})
            except: pass

        async def on_resp(event):
            try:
                resp = event.get("params",{}).get("response",{})
                if "unitool.ai" in resp.get("url",""):
                    print(f"  [RESP] {resp.get('status')} {resp.get('url','')[:80]}", flush=True)
            except: pass

        await tab.on("Network.requestWillBeSent", on_req)
        await tab.on("Network.responseReceived", on_resp)

        def cdp_str(r):
            if not isinstance(r, dict): return str(r) if r is not None else ""
            inner = r.get("result", r)
            if isinstance(inner, dict): inner = inner.get("result", inner)
            if isinstance(inner, dict): return str(inner.get("value",""))
            return str(inner)

        def cdp_int(r):
            try: return int(cdp_str(r))
            except: return 0

        print(f"[*] Opening {TARGET}", flush=True)
        await tab.go_to(TARGET)
        await asyncio.sleep(6)

        # STEP 1: Wait for SIGNUP Turnstile token
        print("[*] Waiting for SIGNUP Turnstile token...", flush=True)
        ts_token = None
        for i in range(25):
            await asyncio.sleep(1)
            tok_len = cdp_int(await tab.execute_script(
                "(document.querySelector('[name=\"cf-turnstile-response\"]')||{value:''}).value.length",
                return_by_value=True
            ))
            if tok_len > 20:
                print(f"[*] SIGNUP token at t={i+1}s, len={tok_len}", flush=True)
                parts = []
                for s in range(0, tok_len + 300, 300):
                    pv = cdp_str(await tab.execute_script(
                        f"(document.querySelector('[name=\"cf-turnstile-response\"]')||{{value:''}}).value.slice({s},{s+300})",
                        return_by_value=True
                    ))
                    if pv: parts.append(pv)
                    if not pv or s+300 >= tok_len: break
                ts_token = "".join(parts)
                print(f"[*] Token: len={len(ts_token)} [{ts_token[:50]}...]", flush=True)
                break
            if i % 8 == 7: print(f"  [{i+1}s] tok_len={tok_len}", flush=True)

        if not ts_token:
            print("[!] No SIGNUP token in 25s", flush=True)
            return

        # STEP 2: Click Sign-in tab
        print("\n[*] Clicking Sign-in tab...", flush=True)
        tab_r = cdp_str(await tab.execute_script("""
            (function(){
                var btns=document.querySelectorAll('button');
                for(var i=0;i<btns.length;i++){
                    if(btns[i].type==='button' && btns[i].innerText.toLowerCase().indexOf('sign')>=0){
                        btns[i].click();
                        return 'OK:'+btns[i].innerText.trim();
                    }
                }
                return 'NO';
            })()
        """, return_by_value=True))
        print(f"[*] Tab click: {tab_r}", flush=True)
        await asyncio.sleep(1.5)

        # STEP 3: Inject token back AND set captcha_action="signup" to match token
        print(f"\n[*] Injecting token + setting captcha_action='signup' to match token...", flush=True)
        tok_json = json.dumps(ts_token)

        inject_r = cdp_str(await tab.execute_script(f"""
            (function(){{
                var token = {tok_json};
                var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
                var results = [];

                // Inject into cf-turnstile-response
                var cfEl = document.querySelector('[name="cf-turnstile-response"]');
                if(cfEl){{
                    setter.call(cfEl, token);
                    cfEl.dispatchEvent(new Event('input',{{bubbles:true}}));
                    cfEl.dispatchEvent(new Event('change',{{bubbles:true}}));
                    results.push('cf-tr:'+cfEl.value.length);
                }} else results.push('cf-tr:NOT_FOUND');

                // Inject into captcha_token
                var ctEl = document.querySelector('[name="captcha_token"]');
                if(ctEl){{
                    setter.call(ctEl, token);
                    ctEl.dispatchEvent(new Event('input',{{bubbles:true}}));
                    results.push('ct:'+ctEl.value.length);
                }} else results.push('ct:NOT_FOUND');

                // CRITICAL: Set captcha_action to "signup" to match the token's action
                // CF siteverify will check: token.action == captcha_action
                var caEl = document.querySelector('[name="captcha_action"]');
                if(caEl){{
                    setter.call(caEl, 'signup');
                    caEl.dispatchEvent(new Event('input',{{bubbles:true}}));
                    results.push('ca:'+caEl.value);
                }} else results.push('ca:NOT_FOUND');

                return results.join(', ');
            }})()
        """, return_by_value=True))
        print(f"[*] Inject result: {inject_r}", flush=True)

        # STEP 4: Fill email + password
        print("[*] Filling email...", flush=True)
        try:
            em_el = await tab.query('input[name="email"]', raise_exc=False)
            if em_el is None:
                em_el = await tab.query('input[type="email"]', raise_exc=False)
            if em_el:
                await em_el.click()
                await asyncio.sleep(0.3)
                await em_el.type_text(EMAIL)
                v = cdp_str(await tab.execute_script(
                    "(document.querySelector('input[name=\"email\"]')||document.querySelector('input[type=\"email\"]')||{value:''}).value",
                    return_by_value=True
                ))
                print(f"[*] Email DOM: {v}", flush=True)
        except Exception as e:
            print(f"[!] Email error: {e}", flush=True)
            try:
                em2 = await tab.query('input[type="email"]', raise_exc=False)
                if em2:
                    await em2.click()
                    await em2.insert_text(EMAIL)
            except: pass

        await asyncio.sleep(0.3)
        print("[*] Filling password...", flush=True)
        try:
            pw_el = await tab.query('input[type="password"]', raise_exc=False)
            if pw_el:
                await pw_el.click()
                await asyncio.sleep(0.3)
                await pw_el.type_text(PASSWORD)
                pwlen = cdp_int(await tab.execute_script(
                    "(document.querySelector('input[type=\"password\"]')||{value:''}).value.length",
                    return_by_value=True
                ))
                print(f"[*] Password DOM len: {pwlen}", flush=True)
        except Exception as e:
            print(f"[!] Password error: {e}", flush=True)
            try:
                pw2 = await tab.query('input[name="password"]', raise_exc=False)
                if pw2:
                    await pw2.click()
                    await pw2.insert_text(PASSWORD)
            except: pass

        await asyncio.sleep(0.3)

        # Verify state before submit
        pre = cdp_str(await tab.execute_script("""
            JSON.stringify({
                email: (document.querySelector('input[name="email"]')||document.querySelector('input[type="email"]')||{value:''}).value,
                pwLen: (document.querySelector('input[type="password"]')||{value:''}).value.length,
                cfTokenLen: (document.querySelector('[name="cf-turnstile-response"]')||{value:''}).value.length,
                captchaAction: (document.querySelector('[name="captcha_action"]')||{value:'N/A'}).value
            })
        """, return_by_value=True))
        print(f"\n[*] Pre-submit state: {pre}", flush=True)

        # STEP 5: Submit via requestSubmit()
        print("\n[*] Submitting via requestSubmit()...", flush=True)
        sub_r = cdp_str(await tab.execute_script("""
            (function(){
                var form = document.querySelector('form');
                if(!form) return 'NO_FORM';
                try {
                    form.requestSubmit();
                    return 'requestSubmit-OK';
                } catch(e) {
                    var btn = form.querySelector('button[type="submit"]');
                    if(btn) { btn.click(); return 'btn-click'; }
                    return 'FAIL:'+e.message;
                }
            })()
        """, return_by_value=True))
        print(f"[*] Submit: {sub_r}", flush=True)

        print("[*] Waiting for login response (25s)...", flush=True)
        await asyncio.sleep(25)

        # GET COOKIES
        print("\n[*] Getting cookies...", flush=True)
        try:
            cookies = await tab.get_cookies()
            all_ck = [c for c in cookies if "unitool" in c.get("domain","")]
            print(f"[COOKIES] Total: {len(cookies)}, unitool.ai: {len(all_ck)}", flush=True)
            # Real auth cookies from unitool.ai - look for JWT-like long values or specific names
            auth_names = {"token","access_token","authtoken","auth_token","session","__session","jwt"}
            for c in all_ck:
                val  = c.get("value","")
                name = c.get("name","")
                flags = []
                if c.get("httpOnly"): flags.append("HttpOnly")
                if c.get("secure"):   flags.append("Secure")
                is_auth = name.lower() in auth_names or (len(val) > 50 and c.get("httpOnly"))
                print(f"  {'[AUTH] ' if is_auth else '       '}{name} = {val[:100]}{'...' if len(val)>100 else ''} [{','.join(flags)}]", flush=True)
        except Exception as e:
            print(f"[!] Cookies error: {e}", flush=True)

        # Check page state
        page = cdp_str(await tab.execute_script("""
            JSON.stringify({
                url: window.location.href,
                text: document.body ? document.body.innerText.slice(0,700) : ''
            })
        """, return_by_value=True))
        print(f"\n[*] Page: {page[:700]}", flush=True)

        # Check session API
        sess_script = """
(async () => {
    var r = await fetch('/api/auth/session', {credentials:'include'});
    var t = await r.text();
    window.__sess = JSON.stringify({status:r.status, body:t.slice(0,800)});
})()
"""
        try:
            await tab.execute_script(sess_script, return_by_value=True, await_promise=True)
            await asyncio.sleep(1)
            sess = cdp_str(await tab.execute_script("window.__sess||'null'", return_by_value=True))
            print(f"\n[SESSION API] {sess}", flush=True)
        except Exception as e:
            print(f"[!] Session check error: {e}", flush=True)

        print(f"\n{'='*60}", flush=True)
        login_posts = [p for p in captured if "unitool.ai/en/entry" in p.get("url","") and p.get("na") == LOGIN_NEXT_ACTION]
        print(f"[LOGIN POSTS: {len(login_posts)}]", flush=True)
        for p in login_posts:
            body = p.get("body","")
            print(f"  body({len(body)}):\n{body}", flush=True)

asyncio.run(main())
