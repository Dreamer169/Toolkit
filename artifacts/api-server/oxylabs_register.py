#!/usr/bin/env python3
"""
Oxylabs.io 全自动注册脚本 v7 — 终极修复版
======================================
CF Managed Challenge (cType='managed', cFPWv='g') 完整解决方案:

优先级:
  A. FlareSolverr (本地HTTP服务) — 用于GET/POST, DrissionPage内核
  B. CapSolver AntiCloudflareTask — 需要API key + 公网代理  
  C. camoufox headed — 人机交互方式绕CF
  D. 手动cf_clearance注入

关键修复 (vs v6):
  1. FlareSolverr集成 (port 8191, DrissionPage Chrome)
  2. CapSolver使用CF挑战URL (fa token URL), 不是注册页URL
  3. patchright静默403已确认是CF (cf-mitigated:challenge, body=empty)
  4. 代理注入改为session级别
"""
import argparse, asyncio, json, os, re, time, random
import aiohttp

SERVER_PUBLIC_IP   = "45.205.27.69"
FLARESOLVERR_URL   = "http://127.0.0.1:8191/v1"  # FlareSolverr service
PUBLIC_PROXIES = [
    f"socks5://{SERVER_PUBLIC_IP}:20854",
    f"socks5://{SERVER_PUBLIC_IP}:20839",
]
LOCAL_RESIDENTIAL = [
    "socks5://127.0.0.1:10854",
    "socks5://127.0.0.1:10839",
    "socks5://127.0.0.1:10844",
]

FIRST_NAMES = ["James","John","Robert","Michael","William","David","Richard","Joseph",
               "Thomas","Charles","Christopher","Daniel","Matthew","Anthony","Mark",
               "Steven","Paul","Andrew","Kenneth","Joshua","Kevin","Brian","George"]
LAST_NAMES  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
               "Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson"]

def log(msg): print(msg, flush=True)


# ─── FlareSolverr solver ────────────────────────────────────────────────────
async def _flaresolverr_solve(cf_url: str, proxy: str = "") -> dict:
    """Use FlareSolverr to bypass CF challenge and get cf_clearance"""
    payload = {
        "cmd": "request.get",
        "url": cf_url,
        "maxTimeout": 30000,
    }
    if proxy:
        payload["proxy"] = {"url": proxy}
    log(f"  [FlareSolverr] GET {cf_url[:70]}...")
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.post(FLARESOLVERR_URL, json=payload,
                            timeout=aiohttp.ClientTimeout(total=120))
            d = await r.json()
        status = d.get("status","?")
        solution = d.get("solution",{})
        cookies = solution.get("cookies",[])
        cf_clearance = next((c["value"] for c in cookies if c.get("name")=="cf_clearance"), "")
        ua = solution.get("userAgent","")
        log(f"  [FlareSolverr] status={status} cf_clearance={'YES' if cf_clearance else 'NO'}")
        return {"cf_clearance": cf_clearance, "cookies": cookies, "ua": ua, "status": status}
    except Exception as e:
        log(f"  [FlareSolverr] Error: {e}")
        return {}


# ─── CapSolver CF Managed Challenge ────────────────────────────────────────
async def _capsolver_solve(api_key: str, cf_challenge_url: str, proxy: str = "") -> dict:
    """
    AntiCloudflareTask: Navigate to CF challenge URL, return cf_clearance.
    IMPORTANT: Use the fa token URL (specific challenge), not the domain root.
    """
    task: dict = {"type": "AntiCloudflareTask", "websiteURL": cf_challenge_url}
    if proxy:
        task["proxy"] = proxy
    log(f"  [CapSolver] Creating task URL={cf_challenge_url[:70]} proxy={proxy or 'none'}...")
    async with aiohttp.ClientSession() as s:
        r = await s.post("https://api.capsolver.com/createTask",
            json={"clientKey": api_key, "task": task},
            timeout=aiohttp.ClientTimeout(total=30))
        data = await r.json()
        if data.get("errorId") or data.get("errorCode"):
            raise RuntimeError(f"CapSolver createTask: {data.get('errorDescription', data)}")
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(f"CapSolver: no taskId: {data}")
        log(f"  [CapSolver] taskId={task_id}")
        for attempt in range(50):
            await asyncio.sleep(3)
            rr = await s.post("https://api.capsolver.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
                timeout=aiohttp.ClientTimeout(total=20))
            result = await rr.json()
            status = result.get("status","")
            if status == "ready":
                sol = result.get("solution",{})
                cf_clearance = sol.get("cf_clearance","")
                log(f"  [CapSolver] Solved! cf_clearance={'YES' if cf_clearance else 'NO'}")
                return sol
            elif status == "failed":
                raise RuntimeError(f"CapSolver failed: {result.get('errorDescription', result)}")
            if attempt % 5 == 4:
                log(f"  [CapSolver] polling t={attempt*3+3}s")
    raise RuntimeError("CapSolver timeout (150s)")



# ─── 2captcha CF bypass ─────────────────────────────────────────────────────
async def _twocaptcha_solve(api_key: str, cf_challenge_url: str, proxy: str = "") -> dict:
    """
    2captcha CloudflareChallenge task.
    Returns cf_clearance cookie value.
    Docs: https://2captcha.com/api-docs/cloudflare-challenge
    """
    task: dict = {
        "type": "AntiCloudflareTask",
        "websiteURL": cf_challenge_url,
        "version": "Managed",
    }
    if proxy:
        task["proxy"] = proxy

    log(f"  [2captcha] Creating task URL={cf_challenge_url[:70]}...")
    async with aiohttp.ClientSession() as s:
        r = await s.post("https://api.2captcha.com/createTask",
            json={"clientKey": api_key, "task": task},
            timeout=aiohttp.ClientTimeout(total=30))
        data = await r.json()
        if data.get("errorCode") or data.get("errorId"):
            raise RuntimeError(f"2captcha create: {data.get('errorDescription', data)}")
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(f"2captcha: no taskId: {data}")
        log(f"  [2captcha] taskId={task_id}")
        for attempt in range(50):
            await asyncio.sleep(3)
            rr = await s.post("https://api.2captcha.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
                timeout=aiohttp.ClientTimeout(total=20))
            result = await rr.json()
            status = result.get("status","")
            if status == "ready":
                sol = result.get("solution", {})
                cf_clearance = sol.get("cf_clearance", "")
                log(f"  [2captcha] Solved! cf_clearance={'YES' if cf_clearance else 'NO'}")
                return sol
            elif status == "failed":
                raise RuntimeError(f"2captcha failed: {result.get('errorDescription', result)}")
            if attempt % 5 == 4:
                log(f"  [2captcha] polling t={attempt*3+3}s")
    raise RuntimeError("2captcha timeout (150s)")


# ─── Get CF challenge URL by triggering POST ───────────────────────────────
async def _get_cf_challenge_url(email: str) -> str:
    """Quickly trigger POST /api/v1/users, capture CF fa token URL from 403 body"""
    try:
        import aiohttp_socks
        connector = aiohttp_socks.ProxyConnector.from_url("socks5://127.0.0.1:10839")
    except ImportError:
        connector = None
    
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Origin": "https://dashboard.oxylabs.io",
        "Referer": "https://dashboard.oxylabs.io/en/registration",
        "Accept": "application/json,*/*",
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
    }
    payload = {"name":"James","surname":"Smith","email":email,"password":"Aa123456x!",
               "websiteTrackingId":None,"gaClientId":"","region":"Global"}
    
    try:
        async with aiohttp.ClientSession(connector=connector) as s:
            r = await s.post("https://dashboard.oxylabs.io/api/v1/users",
                json=payload, headers=hdrs,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=False)
            body = await r.text()
            m = re.search(r'"fa":"(/api/v1/users\?__cf_chl_f_tk=[^"]+)"', body)
            if m:
                return "https://dashboard.oxylabs.io" + m.group(1)
    except Exception as e:
        log(f"  [get_cf_url] {e}")
    return ""


# ─── CF navigate (camoufox headed) ────────────────────────────────────────
async def _navigate_cf_challenge(cf_url: str, proxy_str: str, display: str) -> str:
    """
    Navigate to CF challenge URL in headed camoufox browser.
    Returns cf_clearance or empty string.
    """
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        return ""
    
    if display:
        os.environ["DISPLAY"] = display
    
    proxy_cfg = {"server": proxy_str} if proxy_str else None
    try:
        async with AsyncCamoufox(
            headless=False, os="windows",
            geoip=bool(proxy_cfg), proxy=proxy_cfg,
        ) as browser:
            page = await browser.new_page()
            try:
                await page.goto(cf_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                log(f"  [CF-nav] goto exc: {str(e)[:60]}")
            
            for i in range(15):
                await asyncio.sleep(2)
                cookies = await page.context.cookies()
                cf_ck = next((c for c in cookies if c["name"]=="cf_clearance"), None)
                if cf_ck:
                    log(f"  [CF-nav] ✅ cf_clearance t={i*2+2}s")
                    return cf_ck["value"]
                url = page.url
                if "__cf_chl_f_tk" not in url and "oxylabs.io" in url:
                    cookies = await page.context.cookies()
                    cf_ck = next((c for c in cookies if c["name"]=="cf_clearance"), None)
                    if cf_ck: return cf_ck["value"]
                    break
                if i % 15 == 14:
                    log(f"  [CF-nav] t={i*2+2}s {url[-40:]}")
    except Exception as e:
        log(f"  [CF-nav] Error: {e}")
    return ""


# ─── Main registration ──────────────────────────────────────────────────────
async def register_oxylabs(
    email: str, password: str,
    first_name: str = "", last_name: str = "",
    proxy: str = "", headless: bool = True,
    capsolver_key: str = "",
    two_captcha_key: str = "",
    cf_clearance_manual: str = "",
    flaresolverr: bool = True,
) -> dict:
    t0 = time.time()
    if not first_name: first_name = random.choice(FIRST_NAMES)
    if not last_name:  last_name  = random.choice(LAST_NAMES)
    capsolver_key = capsolver_key or os.environ.get("CAPSOLVER_API_KEY","")
    two_captcha_key = two_captcha_key or os.environ.get("TWOCAPTCHA_API_KEY","")
    proxy = proxy or os.environ.get("OXY_PROXY","socks5://127.0.0.1:10854")

    result: dict = {
        "success": False, "email": email, "password": password,
        "first_name": first_name, "last_name": last_name,
        "username": "", "error": "", "elapsed": "",
    }
    log(f"📧 {email} | 👤 {first_name} {last_name}")

    # Detect Xvfb
    display = None
    for d in [":99",":1",":0",":100"]:
        if os.path.exists(f"/tmp/.X11-unix/X{d.replace(':','')}") or \
           os.path.exists(f"/tmp/.X{d.replace(':','')}-lock"):
            display = d; break
    log(f"🖥️  Display: {display or 'none'} | proxy={proxy}")

    # ─ PHASE 1: try camoufox to fill form + get SEON fingerprint ─────────
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        result["error"] = "camoufox not installed"; return result

    proxy_cfg = {"server": proxy} if proxy else None
    seon_fp: dict = {}
    cf_token_data: dict = {}
    api_responses: list = []

    async def on_req(req):
        if "/api/v1/users" in req.url and req.method == "POST":
            try:
                d = json.loads(req.post_data or "{}")
                if d.get("deviceFingerprint"):
                    seon_fp["value"] = d["deviceFingerprint"]
            except: pass

    async def on_resp(r):
        if "/api/v1/users" in r.url and r.request.method == "POST":
            s = r.status
            try: body = await r.text()
            except: body = ""
            api_responses.append({"status": s, "body": body, "t": time.time()-t0})
            log(f"  [API t={time.time()-t0:.1f}s] HTTP {s}: {body[:80]}")
            if s == 403:
                # CF formats: fa:"/api..." OR "fa":"/api..."
                m = re.search(r'fa:"(/api/v1/users[^"]+)"', body)
                if not m: m = re.search(r'"fa":"(/api/v1/users[^"]+)"', body)
                if not m: m = re.search(r"fa:'(/api/v1/users[^']+)'", body)
                if not m:
                    tk = re.search(r'__cf_chl_f_tk=([^"\'\s&,)]+)', body)
                    if tk: m = type("M", (), {"group": lambda s,n: "/api/v1/users?__cf_chl_f_tk=" + tk.group(1)})()
                if m: cf_token_data["fa"] = m.group(1)
                for k in ["cType", "cFPWv", "cRay"]:
                    # cType: 'managed'  OR  cType:"managed"
                    mm = re.search(k + r":\s*[\"']?([A-Za-z0-9_.-]+)[\"']?", body)
                    if mm: cf_token_data[k] = mm.group(1)

    if display and not headless:
        os.environ["DISPLAY"] = display

    async with AsyncCamoufox(
        headless=headless, os="windows",
        geoip=bool(proxy_cfg), proxy=proxy_cfg,
    ) as browser:
        page = await browser.new_page()
        ua = await page.evaluate("navigator.userAgent")
        log(f"🦊 UA: {ua[:80]}")
        page.on("request", on_req)
        page.on("response", on_resp)

        log("🌐 Loading registration...")
        await page.goto("https://dashboard.oxylabs.io/registration",
                        wait_until="domcontentloaded", timeout=90000)
        for i in range(25):
            await asyncio.sleep(1)
            if len(await page.query_selector_all("input")) >= 3:
                log(f"  ✅ Form ready t={i+1}s"); break
        else:
            result["error"] = "Form not rendered (CF blocks GET or SPA failed)"
            result["elapsed"] = f"{time.time()-t0:.1f}s"; return result

        log("✏️  Filling form...")
        for field, val in [("name",first_name),("surname",last_name),
                            ("email",email),("password",password)]:
            el = await page.query_selector(f"input[name='{field}']")
            if el and await el.is_visible():
                await el.click(); await asyncio.sleep(0.15)
                await el.fill("")
                await page.keyboard.type(val, delay=random.randint(50,90))
                await asyncio.sleep(random.uniform(0.25,0.45))

        log("⏳ SEON (7s)..."); await asyncio.sleep(7)
        
        btn = await page.query_selector("button[type='submit']")
        if btn: await btn.click()
        log("🖱️  Submitted!")

        for _ in range(15):
            await asyncio.sleep(1)
            if api_responses or cf_token_data: break

        # ─ Success path ─────────────────────────────────────────────────
        for resp in api_responses:
            if resp["status"] in (200,201):
                log("✅ Direct success!")
                result.update({"success":True,"username":email.split("@")[0]})
                result["elapsed"] = f"{time.time()-t0:.1f}s"; return result
            if resp["status"] in (400,422):
                bl = resp["body"].lower()
                result["error"] = ("Email already registered" if "already" in bl or "exist" in bl
                                   else f"HTTP {resp['status']}: {resp['body'][:150]}")
                result["elapsed"] = f"{time.time()-t0:.1f}s"; return result
            if resp["status"] == 429:
                result["error"] = "Rate limited"
                result["elapsed"] = f"{time.time()-t0:.1f}s"; return result

        # ─ Manual cf_clearance injection ────────────────────────────────
        if cf_clearance_manual:
            log(f"🔑 Injecting manual cf_clearance...")
            await page.context.add_cookies([{
                "name":"cf_clearance","value":cf_clearance_manual,
                "domain":"dashboard.oxylabs.io","path":"/","secure":True,
            }])

        # ─ CF block detected ─────────────────────────────────────────────
        if cf_token_data.get("fa") or any(r["status"]==403 for r in api_responses):
            log(f"⚠️  CF Managed Challenge | cType={cf_token_data.get('cType','?')} cFPWv={cf_token_data.get('cFPWv','?')}")
            
            cf_clearance_value = cf_clearance_manual
            
            # ─ A. FlareSolverr ────────────────────────────────────────
            if not cf_clearance_value and flaresolverr:
                log("🔧 FlareSolverr...")
                # Get CF challenge URL if we have fa token, else use registration page
                if cf_token_data.get("fa"):
                    fa = cf_token_data["fa"]
                    cf_challenge_url = ("https://dashboard.oxylabs.io" + fa
                                       if fa.startswith("/") else fa)
                else:
                    cf_challenge_url = "https://dashboard.oxylabs.io/en/registration"
                    
                fs_result = await _flaresolverr_solve(cf_challenge_url)
                if not fs_result.get("cf_clearance"):
                    # Fallback: try with proxy
                    for pub_proxy in PUBLIC_PROXIES:
                        fs_result = await _flaresolverr_solve(cf_challenge_url, pub_proxy)
                        if fs_result.get("cf_clearance"): break
                
                cf_clearance_value = fs_result.get("cf_clearance","")
                if cf_clearance_value:
                    log(f"  ✅ FlareSolverr got cf_clearance!")
                    await page.context.add_cookies([{
                        "name":"cf_clearance","value":cf_clearance_value,
                        "domain":"dashboard.oxylabs.io","path":"/","secure":True,
                    }])
                    # Inject all cookies from FlareSolverr
                    for ck in (fs_result.get("cookies") or []):
                        if isinstance(ck,dict) and ck.get("name") and ck["name"] != "cf_clearance":
                            try:
                                await page.context.add_cookies([{
                                    "name":ck["name"],"value":ck.get("value",""),
                                    "domain":ck.get("domain","dashboard.oxylabs.io"),
                                    "path":ck.get("path","/"),
                                }])
                            except: pass

            # ─ B. CapSolver ───────────────────────────────────────────
            if not cf_clearance_value and capsolver_key:
                log("🔧 CapSolver AntiCloudflareTask...")
                # CRITICAL: Use CF challenge URL (fa token) not registration page
                if cf_token_data.get("fa"):
                    caps_url = "https://dashboard.oxylabs.io" + cf_token_data["fa"]
                else:
                    # Get a fresh CF challenge URL
                    caps_url = await _get_cf_challenge_url(email)
                    if not caps_url:
                        caps_url = "https://dashboard.oxylabs.io/en/registration"
                log(f"  CapSolver URL: {caps_url[:80]}")
                
                for pub_proxy in PUBLIC_PROXIES + [""]:
                    try:
                        sol = await _capsolver_solve(capsolver_key, caps_url, pub_proxy)
                        cf_clearance_value = sol.get("cf_clearance","")
                        if cf_clearance_value:
                            await page.context.add_cookies([{
                                "name":"cf_clearance","value":cf_clearance_value,
                                "domain":"dashboard.oxylabs.io","path":"/","secure":True,
                            }])
                            for ck in (sol.get("cookies") or []):
                                if isinstance(ck,dict) and ck.get("name"):
                                    try:
                                        await page.context.add_cookies([{
                                            "name":ck["name"],"value":ck.get("value",""),
                                            "domain":ck.get("domain","dashboard.oxylabs.io"),
                                            "path":ck.get("path","/"),
                                        }])
                                    except: pass
                            break
                    except Exception as e:
                        log(f"  CapSolver error ({pub_proxy}): {e}")

            # ─ B2. 2captcha (alternative to CapSolver) ────────────
            if not cf_clearance_value and two_captcha_key:
                log("🔧 2captcha AntiCloudflareTask...")
                if cf_token_data.get("fa"):
                    caps_url = "https://dashboard.oxylabs.io" + cf_token_data["fa"]
                else:
                    caps_url = await _get_cf_challenge_url(email)
                    if not caps_url: caps_url = "https://dashboard.oxylabs.io/en/registration"
                for pub_proxy in PUBLIC_PROXIES + [""]:
                    try:
                        sol = await _twocaptcha_solve(two_captcha_key, caps_url, pub_proxy)
                        cf_clearance_value = sol.get("cf_clearance","")
                        if cf_clearance_value:
                            await page.context.add_cookies([{
                                "name":"cf_clearance","value":cf_clearance_value,
                                "domain":"dashboard.oxylabs.io","path":"/","secure":True,
                            }])
                            break
                    except Exception as e:
                        log(f"  2captcha error: {e}")

            # ─ C. Headed CF navigation (camoufox WebGL) ──────────────
            if not cf_clearance_value and cf_token_data.get("fa") and display:
                log("🌍 Headed CF navigation (WebGL)...")
                cf_url = "https://dashboard.oxylabs.io" + cf_token_data["fa"]
                for res_proxy in LOCAL_RESIDENTIAL:
                    cf_clearance_value = await _navigate_cf_challenge(cf_url, res_proxy, display)
                    if cf_clearance_value:
                        await page.context.add_cookies([{
                            "name":"cf_clearance","value":cf_clearance_value,
                            "domain":"dashboard.oxylabs.io","path":"/","secure":True,
                        }])
                        break

            # ─ Give up if no clearance ──────────────────────────────
            if not cf_clearance_value:
                msg = (
                    "CF Managed Challenge not solved. "
                    f"cType={cf_token_data.get('cType','?')}, cFPWv={cf_token_data.get('cFPWv','?')}. "
                    "Solutions: (1) Provide CAPSOLVER_API_KEY (~$0.001/solve), "
                    "(2) Start FlareSolverr (port 8191), "
                    "(3) Provide cf_clearance cookie manually from your browser."
                )
                result["error"] = msg
                result["elapsed"] = f"{time.time()-t0:.1f}s"
                log(f"❌ {msg}")
                return result

            # ─ Retry POST with cf_clearance ──────────────────────────
            log("📡 Retrying POST /api/v1/users with cf_clearance...")
            if "registration" not in page.url:
                try:
                    await page.goto("https://dashboard.oxylabs.io/en/registration",
                                    wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(3)
                except: pass

            ga_id = await page.evaluate("""
() => { try { const m=document.cookie.match(/_ga=GA\d\.\d\.([\d.]+)/);return m?m[1]:''; }
        catch(e){return '';} }
""")
            seon_val = seon_fp.get("value") or f"Web;{email};v7;en-US"
            
            api_responses.clear()
            retry = await page.evaluate("""
async (p) => {
    try {
        const r = await fetch('/api/v1/users', {
            method:'POST',
            headers:{'Content-Type':'application/json','Accept':'application/json,*/*'},
            credentials:'include', body:JSON.stringify(p)
        });
        const t = await r.text();
        return {status:r.status, body:t.substring(0,600)};
    } catch(e) { return {status:0, body:'err:'+e}; }
}
""", {
                "email": email, "password": password,
                "name": first_name, "surname": last_name,
                "websiteTrackingId": None, "gaClientId": ga_id or "",
                "region": "Global", "deviceFingerprint": seon_val,
            })
            
            s, b = retry.get("status",0), retry.get("body","")
            log(f"  [Retry] HTTP {s}: {b[:200]}")
            
            if s in (200,201):
                log("✅ SUCCESS after CF bypass!")
                result.update({"success":True,"username":email.split("@")[0]})
            elif s == 403:
                result["error"] = "CF still blocking after bypass (cf_clearance expired or path-specific)"
            elif s in (400,422):
                bl = b.lower()
                result["error"] = ("Email already registered" if "already" in bl or "exist" in bl
                                   else f"Validation {s}: {b[:150]}")
            else:
                result["error"] = f"HTTP {s}: {b[:150]}"

        elif not api_responses:
            result["error"] = "No API response after form submit"

        result["elapsed"] = f"{time.time()-t0:.1f}s"
        log(f"{'✅ Done' if result['success'] else '❌ Failed'}: {result.get('error','')[:100]} [{result['elapsed']}]")
        return result


# ─── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Oxylabs registration v7")
    ap.add_argument("--email",    required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--first",  default="")
    ap.add_argument("--last",   default="")
    ap.add_argument("--proxy",  default="")
    ap.add_argument("--headless", default="true")
    ap.add_argument("--capsolver-key", default="", dest="capsolver_key")
    ap.add_argument("--cf-clearance", default="", dest="cf_clearance")
    ap.add_argument("--company", default="")  # accepted but unused
    ap.add_argument("--phone",   default="")  # accepted but unused
    ap.add_argument("--twocaptcha-key", default="", dest="two_captcha_key")
    ap.add_argument("--no-flaresolverr", action="store_true")
    args = ap.parse_args()
    r = asyncio.run(register_oxylabs(
        args.email, args.password,
        first_name=args.first, last_name=args.last,
        proxy=args.proxy,
        headless=args.headless.lower() not in ("false","0","no"),
        capsolver_key=args.capsolver_key,
        two_captcha_key=args.two_captcha_key,
        cf_clearance_manual=args.cf_clearance,
        flaresolverr=not args.no_flaresolverr,
    ))
    print(json.dumps(r, indent=2))
