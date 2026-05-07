#!/usr/bin/env python3
"""
obvious_airforce_factory_code.py — Sandbox-B: api.airforce registrar
Critical fixes (2026-05-03):
  - URL: api.airforce/signup (not panel.) — pydoll verified this works
  - Invisible Turnstile: poll 90s from page-load (GCP IPs get silent token ~20-45s)
  - Shadow bypass: kept as fallback (for residential proxy IPs that show checkbox)
  - window.__turnstile_token interceptor active from page init
  - Form fields: input#username/email/password/confirmPassword (id-based)
"""
import asyncio, json, os, random, re, string, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path

def _pip(pkg):
    r = subprocess.run([sys.executable,"-m","pip","install","-q",pkg],
        capture_output=True, text=True, timeout=60)
    return r.returncode == 0

for _p in ["playwright","nest_asyncio"]:
    try: __import__(_p)
    except ImportError: _pip(_p)

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("[af-factory] FATAL: playwright not importable"); sys.exit(0)

def _ensure_chromium():
    from pathlib import Path as _P
    cache_dirs = list(_P.home().glob(".cache/ms-playwright/chromium*"))
    shell_dirs = list(_P.home().glob(".cache/ms-playwright/chromium_headless_shell*"))
    if cache_dirs or shell_dirs:
        return True
    print("[af-factory] Installing chromium browser (~2min)...", flush=True)
    r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True, timeout=300)
    print(f"[af-factory] chromium install RC={r.returncode}", flush=True)
    return r.returncode == 0

_ensure_chromium()
import nest_asyncio; nest_asyncio.apply()

VPS_API       = os.environ.get("VPS_API",       "http://45.205.27.69:8084")
SANDBOX_LABEL = os.environ.get("SANDBOX_LABEL", "unknown")
SOCKS5_RELAY  = os.environ.get("SOCKS5_RELAY",  "")
SIGNUP_URL    = os.environ.get("SIGNUP_URL",     "https://api.airforce/signup")
SHOT_DIR      = Path("/home/user/work/shots"); SHOT_DIR.mkdir(parents=True, exist_ok=True)

RESULT = {"success":False,"username":None,"email":None,"password":None,
          "api_key":None,"error":None,"elapsed":None,"sandbox":SANDBOX_LABEL}

def vps_get(path):
    try:
        with urllib.request.urlopen(f"{VPS_API}{path}", timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

def vps_post(path, payload):
    try:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(f"{VPS_API}{path}", data=data,
               headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

def gen_af_username(email_username):
    base = re.sub(r"[^a-z0-9]","",email_username.lower())[:12]
    sfx  = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"af_{base}{sfx}"

def gen_password():
    import secrets
    chars = string.ascii_letters + string.digits + "!@#$%"
    while True:
        pw = "".join(secrets.choice(chars) for _ in range(13))
        if (any(c.isupper() for c in pw) and any(c.islower() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "!@#$%" for c in pw)):
            return pw

async def shot(page, name):
    try:
        await page.screenshot(path=str(SHOT_DIR / f"{name}.png"))
    except Exception:
        pass

# JS to probe all Turnstile token sources
TOKEN_PROBE_JS = """() => {
    // Source 1: hidden input (primary - set when CF widget completes)
    for (const inp of document.querySelectorAll('input[name="cf-turnstile-response"]')) {
        if (inp.value && inp.value.length > 20) return inp.value;
    }
    // Source 2: window.__turnstile_token (our interceptor)
    if (window.__turnstile_token && window.__turnstile_token.length > 20)
        return window.__turnstile_token;
    // Source 3: any input containing a Turnstile-like token pattern
    for (const inp of document.querySelectorAll('input[type="hidden"]')) {
        if (inp.value && inp.value.length > 200 && inp.name !== 'cf-turnstile-response')
            return inp.value;
    }
    return null;
}"""

async def register():
    t_start = time.time()

    print(f"[af-factory:{SANDBOX_LABEL}] claiming email from VPS queue...", flush=True)
    claim = vps_get("/emails/pop")
    if claim.get("error") or not claim.get("email"):
        RESULT["error"] = f"no email from queue: {claim}"; return

    email   = claim["email"]
    af_user = gen_af_username(claim.get("username", email.split("@")[0]))
    RESULT.update({"email":email,"username":af_user})
    print(f"[af-factory:{SANDBOX_LABEL}] claimed {email} → af_user={af_user}", flush=True)

    async with async_playwright() as pw:
        _proxy = {"server": SOCKS5_RELAY} if SOCKS5_RELAY else None
        _launch_kwargs = dict(
            headless=True,
            proxy=_proxy,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--lang=en-US",
                "--window-size=1366,768",
                "--disable-extensions",
                "--disable-default-apps",
            ],
        )
        try:
            browser = await pw.chromium.launch(channel="chrome", **_launch_kwargs)
            print(f"[af-factory:{SANDBOX_LABEL}] using Chrome", flush=True)
        except Exception as _e:
            print(f"[af-factory:{SANDBOX_LABEL}] Chrome unavailable, using Chromium", flush=True)
            browser = await pw.chromium.launch(**_launch_kwargs)
        try:
            ctx = await browser.new_context(
                locale="en-US",
                viewport={"width":1366,"height":768},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                proxy=_proxy,
            )
            await ctx.add_init_script("""
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
                Object.defineProperty(navigator,'platform',{get:()=>'Linux x86_64'});
                Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8});
                Object.defineProperty(navigator,'deviceMemory',{get:()=>8});
                window.chrome={runtime:{},app:{isInstalled:false},csi:function(){},loadTimes:function(){}};
                // Turnstile interceptor: capture token when widget fires callback
                (function(){
                    let _ts = null;
                    Object.defineProperty(window,'turnstile',{
                        get:()=>_ts,
                        set:(v)=>{
                            if(v && v.render){
                                const _origRender = v.render.bind(v);
                                v.render = function(el, opts){
                                    const _origCb = opts && opts.callback;
                                    if(opts) opts.callback = function(token){
                                        window.__turnstile_token = token;
                                        console.log('[turnstile-interceptor] token captured len=' + token.length);
                                        if(_origCb) _origCb(token);
                                    };
                                    return _origRender(el, opts);
                                };
                            }
                            _ts = v;
                        },
                        configurable:true
                    });
                })();
            """)
            page = await ctx.new_page()

            # Navigate to api.airforce/signup (confirmed working with pydoll)
            print(f"[af-factory:{SANDBOX_LABEL}] navigating to {SIGNUP_URL}...", flush=True)
            try:
                await page.goto(SIGNUP_URL, timeout=45000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"[af-factory:{SANDBOX_LABEL}] goto warn: {str(e)[:60]}", flush=True)

            t_nav = time.time() - t_start
            print(f"[af-factory:{SANDBOX_LABEL}] page loaded t={t_nav:.1f}s", flush=True)

            # Wait for form + poll for Turnstile token SIMULTANEOUSLY
            # GCP sandbox IPs get silent Turnstile token (managed mode) — takes 20-45s
            # Poll aggressively from t=0 to catch it as soon as it arrives
            print(f"[af-factory:{SANDBOX_LABEL}] polling for invisible Turnstile token (90s)...", flush=True)
            token = None
            form_ready = False
            poll_deadline = time.time() + 90

            while time.time() < poll_deadline:
                elapsed = time.time() - t_start

                # Check for token
                try:
                    tok = await page.evaluate(TOKEN_PROBE_JS)
                    if tok:
                        token = tok
                        print(f"[af-factory:{SANDBOX_LABEL}] INVISIBLE TOKEN len={len(token)} t={elapsed:.1f}s", flush=True)
                        break
                except Exception:
                    pass

                # Check form readiness (don't break poll, just note it)
                if not form_ready:
                    try:
                        n = await page.evaluate("document.querySelectorAll('input').length")
                        if n and int(n) >= 3:
                            form_ready = True
                            print(f"[af-factory:{SANDBOX_LABEL}] form ready: {n} inputs at t={elapsed:.1f}s", flush=True)
                    except Exception:
                        pass

                # Log progress every 10s
                if int(elapsed) % 10 == 0 and int(elapsed) > 0:
                    url_now = page.url
                    print(f"[af-factory:{SANDBOX_LABEL}] t={elapsed:.0f}s url={url_now[:60]} form={form_ready}", flush=True)

                await asyncio.sleep(1)

            if not form_ready:
                await shot(page, "form_missing")
                RESULT["error"] = "form not ready after 90s"; return

            # If no invisible token, attempt shadow bypass (for checkbox-mode IPs)
            if not token:
                print(f"[af-factory:{SANDBOX_LABEL}] no invisible token → shadow bypass...", flush=True)
                await shot(page, "pre_shadow")
                try:
                    iframe_loc = page.frame_locator("iframe[src*='challenges.cloudflare.com']").first
                    # Try multiple checkbox selectors inside the CF iframe
                    for cb_sel in ["input[type='checkbox']", ".ctp-checkbox-label", "label", "span.cb-i"]:
                        try:
                            cb = iframe_loc.locator(cb_sel)
                            if await cb.count() > 0:
                                await cb.first.click(timeout=5000)
                                print(f"[af-factory:{SANDBOX_LABEL}] Turnstile click: {cb_sel}", flush=True)
                                break
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[af-factory:{SANDBOX_LABEL}] shadow bypass err: {str(e)[:80]}", flush=True)

                # Wait up to 60s more for token after click attempt
                for i in range(60):
                    await asyncio.sleep(1)
                    try:
                        tok = await page.evaluate(TOKEN_PROBE_JS)
                        if tok:
                            token = tok
                            print(f"[af-factory:{SANDBOX_LABEL}] POST-CLICK TOKEN len={len(token)} t={round(time.time()-t_start,1)}s", flush=True)
                            break
                    except Exception:
                        pass

            if not token:
                await shot(page, "no_token")
                RESULT["error"] = "no Turnstile token after full timeout"; return

            # Fill form
            af_pass = gen_password()
            RESULT["password"] = af_pass

            print(f"[af-factory:{SANDBOX_LABEL}] filling form t={round(time.time()-t_start,1)}s...", flush=True)
            for field_id, field_val in [("username",af_user),("email",email),
                                         ("password",af_pass),("confirmPassword",af_pass)]:
                try:
                    await page.fill(f"input#{field_id}", field_val)
                except Exception:
                    # Fallback: React-compatible value setter
                    jid  = json.dumps(field_id)
                    jval = json.dumps(field_val)
                    await page.evaluate(f"""()=>{{
                        var el=document.getElementById({jid});
                        if(el){{var s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
                        s.call(el,{jval});el.dispatchEvent(new Event('input',{{bubbles:true}}));
                        el.dispatchEvent(new Event('change',{{bubbles:true}}));}}
                    }}""")

            await shot(page, "form_filled")
            print(f"[af-factory:{SANDBOX_LABEL}] form filled, submitting...", flush=True)

            # Submit
            submitted = False
            for sel in ["button[type='submit']","button:has-text('Create')","button:has-text('Sign')","button:has-text('Get')"]:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0:
                        await btn.wait_for(state="visible", timeout=4000)
                        await btn.click()
                        print(f"[af-factory:{SANDBOX_LABEL}] submitted via {sel}", flush=True)
                        submitted = True
                        break
                except Exception:
                    pass
            if not submitted:
                # Fallback: click first button
                try:
                    await page.locator("button").first.click()
                    print(f"[af-factory:{SANDBOX_LABEL}] submitted via first button", flush=True)
                except Exception as e:
                    print(f"[af-factory:{SANDBOX_LABEL}] submit error: {e}", flush=True)

            # Wait for dashboard
            dashboard = False
            for i in range(90):
                await asyncio.sleep(1)
                url = page.url
                if "dashboard" in url.lower():
                    dashboard = True
                    print(f"[af-factory:{SANDBOX_LABEL}] DASHBOARD t={round(time.time()-t_start,1)}s url={url}", flush=True)
                    break
                # Check for errors or rate limit
                if i in (5, 15, 30, 60):
                    try:
                        body_txt = await page.evaluate("document.body.innerText") or ""
                        if "Too many" in body_txt or "too many" in body_txt:
                            RESULT["error"] = "rate limited: Too many attempts"
                            print(f"[af-factory:{SANDBOX_LABEL}] RATE LIMITED at t={i}s", flush=True)
                            await shot(page, f"rate_limited_{i}")
                            return
                        if i in (15, 30):
                            print(f"[af-factory:{SANDBOX_LABEL}] t={i}s url={url[:60]}", flush=True)
                    except Exception:
                        pass

            if not dashboard:
                await shot(page, "timeout_no_dashboard")
                body_txt = ""
                try:
                    body_txt = await page.evaluate("document.body.innerText") or ""
                except Exception:
                    pass
                RESULT["error"] = f"timeout no dashboard url={page.url[:60]} text={body_txt[:100]}"
                return

            await shot(page, "dashboard")
            await asyncio.sleep(1)

            # Extract API key
            api_key = await page.evaluate("""async () => {
                try {
                    const r = await fetch('/api/me', {credentials:'include'});
                    const d = await r.json();
                    return d.api_key || d.apiKey || null;
                } catch(e) { return null; }
            }""")

            RESULT["elapsed"] = round(time.time()-t_start, 1)
            if api_key:
                push = vps_post("/accounts/push",{"username":af_user,"email":email,
                                                   "password":af_pass,"api_key":api_key,
                                                   "sandbox":SANDBOX_LABEL})
                RESULT.update({"success":True,"api_key":api_key})
                print(f"[af-factory:{SANDBOX_LABEL}] SUCCESS key={api_key[:28]}... push={push}", flush=True)
            else:
                RESULT["error"] = "dashboard reached but no api_key from /api/me"

        finally:
            await browser.close()

asyncio.get_event_loop().run_until_complete(register())
print("RESULT:"+json.dumps(RESULT), flush=True)
