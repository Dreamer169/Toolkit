#!/usr/bin/env python3
"""
obvious_factory_code.py - api.airforce account registration factory
Runs INSIDE an obvious.ai e2b sandbox (Python 3.13 + Playwright 1.59)
Each sandbox has its own IP - fully distributed.
Output: last line is RESULT:{json}
"""
import asyncio, json, os, random, string, time
from playwright.async_api import async_playwright

def _gen_username(n=8):
    return "af_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

def _gen_password():
    chars = string.ascii_lowercase + string.ascii_uppercase + string.digits
    pw = (random.choice(string.ascii_uppercase)
          + random.choice(string.ascii_lowercase)
          + random.choice(string.digits)
          + random.choice("!@#$%^&*")
          + "".join(random.choices(chars, k=8)))
    lst = list(pw)
    random.shuffle(lst)
    return "".join(lst)

USERNAME = os.environ.get("AF_USERNAME") or _gen_username()
PASSWORD = os.environ.get("AF_PASSWORD") or _gen_password()
EMAIL    = USERNAME + "@proton.me"

RESULT = {
    "success": False, "username": USERNAME, "email": EMAIL,
    "password": PASSWORD, "api_key": None, "error": None, "elapsed": None,
}

async def register():
    t0 = time.time()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled",
                  "--disable-setuid-sandbox"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        await ctx.add_init_script("""
            Object.defineProperty(navigator, "webdriver", {get: () => undefined});
            window.chrome = {runtime: {}};
            Object.defineProperty(navigator, "plugins", {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, "languages", {get: () => ["en-US","en"]});
        """)
        page = await ctx.new_page()
        try:
            print(f"[factory:{USERNAME}] navigating...", flush=True)
            await page.goto("https://api.airforce/signup/", timeout=40000,
                            wait_until="domcontentloaded")
            await asyncio.sleep(2)
            try:
                await page.wait_for_selector('input[name="username"]', timeout=15000)
                print(f"[factory:{USERNAME}] form ready t={time.time()-t0:.1f}s", flush=True)
            except Exception:
                RESULT["error"] = "form not found title=" + (await page.title())[:60]
                return

            # Step 1: probe invisible Turnstile token (5s fast probe)
            token = None
            for _ in range(5):
                await asyncio.sleep(1)
                token = await page.evaluate("""() => {
                    for (const inp of document.querySelectorAll('input[name="cf-turnstile-response"]'))
                        if (inp.value) return inp.value;
                    return null;
                }""")
                if token:
                    break

            # Step 2: shadow DOM checkbox bypass
            if not token:
                print(f"[factory:{USERNAME}] shadow bypass t={time.time()-t0:.1f}s", flush=True)
                try:
                    await page.wait_for_selector(
                        "iframe[src*='challenges.cloudflare.com']", timeout=8000)
                    frame_loc = page.frame_locator(
                        "iframe[src*='challenges.cloudflare.com']").first
                    cb = frame_loc.locator("input[type='checkbox']")
                    if await cb.count() == 0:
                        cb = frame_loc.locator(".ctp-checkbox-label, label")
                    await cb.first.click(timeout=6000)
                    print(f"[factory:{USERNAME}] CF clicked t={time.time()-t0:.1f}s", flush=True)
                except Exception as e:
                    print(f"[factory:{USERNAME}] iframe click: {e}", flush=True)

                for _ in range(30):
                    await asyncio.sleep(1)
                    token = await page.evaluate("""() => {
                        for (const inp of document.querySelectorAll('input[name="cf-turnstile-response"]'))
                            if (inp.value) return inp.value;
                        return null;
                    }""")
                    if token:
                        print(f"[factory:{USERNAME}] TOKEN len={len(token)} t={time.time()-t0:.1f}s", flush=True)
                        break
            else:
                print(f"[factory:{USERNAME}] invisible TOKEN t={time.time()-t0:.1f}s", flush=True)

            if not token:
                RESULT["error"] = "no Turnstile token after 35s"
                return

            # Step 3: fill form
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="email"]', EMAIL)
            await page.fill('input[name="password"]', PASSWORD)
            try:
                await page.fill('input[name="confirmPassword"]', PASSWORD)
            except Exception:
                pass
            print(f"[factory:{USERNAME}] form filled t={time.time()-t0:.1f}s", flush=True)

            # Step 4: submit
            for sel in ['button[type="submit"]', 'button:has-text("Create")',
                        'button:has-text("Sign")', 'button:has-text("Register")']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        print(f"[factory:{USERNAME}] submitted t={time.time()-t0:.1f}s", flush=True)
                        break
                except Exception:
                    pass

            # Step 5: wait for dashboard
            dashboard = False
            for _ in range(80):
                await asyncio.sleep(1)
                url = page.url
                if "dashboard" in url.lower():
                    print(f"[factory:{USERNAME}] DASHBOARD t={time.time()-t0:.1f}s", flush=True)
                    dashboard = True
                    break
                err_el = page.locator('[class*="error"], [role="alert"], .text-red, .text-destructive')
                if await err_el.count() > 0:
                    try:
                        err_txt = await err_el.first.text_content(timeout=500)
                        if err_txt and len(err_txt.strip()) > 2:
                            RESULT["error"] = "signup error: " + err_txt.strip()[:100]
                            return
                    except Exception:
                        pass

            if not dashboard:
                RESULT["error"] = "timeout waiting for dashboard (80s)"
                return

            # Step 6: get API key
            await asyncio.sleep(1)
            api_key = await page.evaluate("""async () => {
                try {
                    const r = await fetch("/api/me", {credentials: "include"});
                    const d = await r.json();
                    return d.api_key || null;
                } catch(e) { return null; }
            }""")

            if api_key:
                RESULT["success"] = True
                RESULT["api_key"] = api_key
                RESULT["elapsed"] = round(time.time() - t0, 1)
                print(f"[factory:{USERNAME}] SUCCESS key={api_key[:28]}... t={RESULT['elapsed']}s", flush=True)
            else:
                RESULT["error"] = "dashboard reached but /api/me returned no api_key"

        except Exception as e:
            RESULT["error"] = type(e).__name__ + ": " + str(e)[:200]
            print(f"[factory:{USERNAME}] EXCEPTION: {e}", flush=True)
        finally:
            await browser.close()

asyncio.run(register())
print("RESULT:" + json.dumps(RESULT))
