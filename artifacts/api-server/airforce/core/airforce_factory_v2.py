#!/usr/bin/env python3
"""
airforce_factory_v2.py  -- Sandbox-B: api.airforce account generator
======================================================================
* Runs on VPS (patchright installed), routes browser via sandbox socks5 proxy
* Bypasses Turnstile shadow DOM -> waits for cf-turnstile-response token
* Pushes api_key to VPS accounts DB via /accounts/push
* No Jupyter kernel conflict; no nest_asyncio needed

Environment:
  PROXY_PORT     local socks5 port (sandbox egress, e.g. 10837) REQUIRED
  VPS_API        default http://45.205.27.69:8084
  SANDBOX_LABEL  label for logging
  EMAIL          pre-specified email (skip queue pop if set)
"""
import json, os, random, re, string, time, urllib.request

PROXY_PORT    = os.environ.get("PROXY_PORT", "")
VPS_API       = os.environ.get("VPS_API", "http://45.205.27.69:8084")
SANDBOX_LABEL = os.environ.get("SANDBOX_LABEL", f"proxy:{PROXY_PORT}")
PRE_EMAIL     = os.environ.get("EMAIL", "")

def vps_get(path):
    try:
        with urllib.request.urlopen(f"{VPS_API}{path}", timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

def vps_post(path, payload):
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(f"{VPS_API}{path}", data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

def gen_af_username(email_username):
    base = re.sub(r"[^a-z0-9]", "", email_username.lower())[:12]
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

def run_factory():
    if not PROXY_PORT:
        return {"status": "fail", "reason": "PROXY_PORT_missing"}

    from patchright.sync_api import sync_playwright

    # Claim email from queue (or use pre-specified)
    if PRE_EMAIL:
        email = PRE_EMAIL
        username_hint = email.split("@")[0]
    else:
        claim = vps_get("/emails/pop")
        if claim.get("error") or not claim.get("email"):
            return {"status": "fail", "reason": f"no_email_from_queue: {claim}"}
        email = claim["email"]
        username_hint = claim.get("username", email.split("@")[0])

    af_user = gen_af_username(username_hint)
    af_pass = gen_password()
    proxy_url = f"socks5://127.0.0.1:{PROXY_PORT}"
    result = {"status": "fail", "email": email, "username": af_user,
              "reason": "unknown", "sandbox": SANDBOX_LABEL}

    print(f"[af:{SANDBOX_LABEL}] email={email} user={af_user} proxy={proxy_url}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            proxy={"server": proxy_url},
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled",
                  "--lang=en-US"],
        )
        try:
            ctx = browser.new_context(
                locale="en-US",
                viewport={"width": 1366, "height": 768},
                proxy={"server": proxy_url},
                user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
            )
            ctx.add_init_script("""
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                window.chrome={runtime:{}};
            """)
            page = ctx.new_page()

            print(f"[af:{SANDBOX_LABEL}] loading signup page...", flush=True)
            page.goto("https://api.airforce/signup/?ref=avjyFSUY9UzdqrRb",
                      timeout=45000, wait_until="domcontentloaded")
            time.sleep(20)  # wait for React hydration + Turnstile load

            page.screenshot(path=f"/tmp/af_loaded_{SANDBOX_LABEL}.png")
            print(f"[af:{SANDBOX_LABEL}] page loaded: {page.title()}", flush=True)

            # Check form is present
            try:
                page.locator("input#username").wait_for(state="visible", timeout=15000)
                print(f"[af:{SANDBOX_LABEL}] form found", flush=True)
            except Exception:
                result["reason"] = "form_not_found: " + page.title()[:60]
                return result

            # Probe for silent Turnstile token (some sandbox IPs get it automatically)
            token = None
            for _ in range(5):
                token = page.evaluate("""() => {
                    for (const inp of document.querySelectorAll('input[name="cf-turnstile-response"]'))
                        if (inp.value) return inp.value;
                    if (window.__turnstile_token) return window.__turnstile_token;
                    return null;
                }""")
                if token:
                    print(f"[af:{SANDBOX_LABEL}] token found silently len={len(token)}", flush=True)
                    break
                time.sleep(1)

            if not token:
                print(f"[af:{SANDBOX_LABEL}] shadow bypass attempt...", flush=True)
                try:
                    page.wait_for_selector("iframe[src*='challenges.cloudflare.com']",
                                           timeout=10000)
                    frame = page.frame_locator("iframe[src*='challenges.cloudflare.com']").first
                    cb = frame.locator("input[type='checkbox']")
                    if cb.count() == 0:
                        cb = frame.locator(".ctp-checkbox-label,label")
                    cb.first.click(timeout=6000)
                    print(f"[af:{SANDBOX_LABEL}] Turnstile checkbox clicked", flush=True)
                except Exception as e:
                    print(f"[af:{SANDBOX_LABEL}] iframe click: {str(e)[:60]}", flush=True)

                for _ in range(35):
                    time.sleep(1)
                    token = page.evaluate("""() => {
                        for (const inp of document.querySelectorAll('input[name="cf-turnstile-response"]'))
                            if (inp.value) return inp.value;
                        return null;
                    }""")
                    if token:
                        print(f"[af:{SANDBOX_LABEL}] TOKEN len={len(token)}", flush=True)
                        break

            if not token:
                result["reason"] = "no_turnstile_token_after_40s"
                return result

            # Fill form
            page.locator("input#username").fill(af_user)
            time.sleep(0.5)
            page.locator("input#email").fill(email)
            time.sleep(0.5)
            page.locator("input#password").fill(af_pass)
            time.sleep(0.5)
            try:
                page.locator("input#confirmPassword").fill(af_pass)
            except Exception:
                pass
            page.screenshot(path=f"/tmp/af_filled_{SANDBOX_LABEL}.png")
            print(f"[af:{SANDBOX_LABEL}] form filled t=?", flush=True)

            # Submit
            for sel in ["button[type='submit']", "button:has-text('Create')", "button:has-text('Sign')", "button:has-text('Get Started')"]:
                try:
                    btn = page.locator(sel).first
                    if btn.count() > 0:
                        btn.wait_for(state="visible", timeout=3000)
                        btn.click()
                        print(f"[af:{SANDBOX_LABEL}] submitted via {sel}", flush=True)
                        break
                except Exception:
                    pass

            # Wait for dashboard redirect
            dashboard = False
            for i in range(80):
                time.sleep(1)
                url = page.url
                if "dashboard" in url.lower():
                    dashboard = True
                    print(f"[af:{SANDBOX_LABEL}] DASHBOARD at {i}s URL={url}", flush=True)
                    break
                # Check for error messages
                for err_sel in ["[class*='error']", "[role='alert']", ".text-red-500", ".text-destructive"]:
                    try:
                        el = page.locator(err_sel).first
                        if el.count() > 0:
                            txt = el.text_content(timeout=500) or ""
                            if len(txt.strip()) > 2:
                                result["reason"] = f"signup_error: {txt.strip()[:100]}"
                                return result
                    except Exception:
                        pass

            if not dashboard:
                result["reason"] = f"timeout_no_dashboard url={page.url[:60]}"
                page.screenshot(path=f"/tmp/af_timeout_{SANDBOX_LABEL}.png")
                return result

            page.screenshot(path=f"/tmp/af_dashboard_{SANDBOX_LABEL}.png")
            time.sleep(1)

            api_key = page.evaluate("""async () => {
                try {
                    const r = await fetch('/api/me', {credentials: 'include'});
                    const d = await r.json();
                    return d.api_key || d.apiKey || null;
                } catch(e) { return null; }
            }""")

            if api_key:
                push = vps_post("/accounts/push", {
                    "username": af_user, "email": email,
                    "password": af_pass, "api_key": api_key,
                    "sandbox": SANDBOX_LABEL
                })
                result.update({"status": "ok", "api_key": api_key, "password": af_pass,
                               "reason": "success", "vps": push})
                print(f"[af:{SANDBOX_LABEL}] SUCCESS key={api_key[:28]}...", flush=True)
            else:
                result["reason"] = "dashboard_reached_but_no_api_key"

        finally:
            browser.close()

    return result

if __name__ == "__main__":
    import sys
    res = run_factory()
    print("RESULT:", json.dumps(res), flush=True)
