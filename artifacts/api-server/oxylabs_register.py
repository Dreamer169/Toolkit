#!/usr/bin/env python3
"""
Oxylabs.io 全自动注册脚本 v7 — 终极修复版
======================================
CF Managed Challenge (cType='managed', cFPWv='g') 完整解决方案:

优先级:
  A. FlareSolverr (本地HTTP服务) — 用于GET/POST, DrissionPage内核
  B. CapSolver AntiCloudflareTask — 需要API key + 公网代理  
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









# ─── Main registration ──────────────────────────────────────────────────────
async def register_oxylabs(
    email: str, password: str,
    first_name: str = "", last_name: str = "",
    proxy: str = "", headless: bool = True
    flaresolverr: bool = True,
) -> dict:
    t0 = time.time()
    if not first_name: first_name = random.choice(FIRST_NAMES)
    if not last_name:  last_name  = random.choice(LAST_NAMES)
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


            # ─ Give up if no clearance ──────────────────────────────
            if not cf_clearance_value:
                msg = (
                    "CF Managed Challenge not solved. "
                    f"cType={cf_token_data.get('cType','?')}, cFPWv={cf_token_data.get('cFPWv','?')}. "
                    "Try: Start FlareSolverr (port 8191), or provide cf_clearance cookie manually."
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
    ap.add_argument("--company", default="")  # accepted but unused
    ap.add_argument("--phone",   default="")  # accepted but unused
    ap.add_argument("--no-flaresolverr", action="store_true")
    args = ap.parse_args()
    r = asyncio.run(register_oxylabs(
        args.email, args.password,
        first_name=args.first, last_name=args.last,
        proxy=args.proxy,
        headless=args.headless.lower() not in ("false","0","no")
        two_captcha_key=args.two_captcha_key
        flaresolverr=not args.no_flaresolverr,
    ))
    print(json.dumps(r, indent=2))
