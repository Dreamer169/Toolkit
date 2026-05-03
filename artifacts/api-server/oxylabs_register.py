#!/usr/bin/env python3
"""
Oxylabs.io 全自动注册脚本 v6
根因修复:
  1. CF inline wait无效 — React SPA fetch()拿403响应体，不执行CF JS
     修复: page.goto(CF challenge URL) 让完整页面运行CF JS
  2. CapSolver proxy bug — 127.0.0.1被过滤后caps_proxy=""，CF Managed Challenge需要代理
     修复: 使用服务器公网IP + socat暴露的居民IP代理
  3. Xvfb GLX可用 — DISPLAY=:99 有完整OpenGL加速，headed模式更好绕过CF
  4. 多居民IP轮换 — HKBN/HKT都可用于CF导航+CapSolver

策略优先级:
  A. CapSolver AntiCloudflareTask (提供CAPSOLVER_API_KEY时)
     proxy: socks5://45.205.27.69:20854 (HKT 112.120.48.16)
  B. 导航到CF挑战URL + Xvfb headed模式 (DISPLAY=:99)
     多居民IP轮换: 10839(HKBN), 10844(HKBN), 10854(HKT)
  C. 错误报告 + 操作指引
"""
import argparse, asyncio, json, os, re, time, random
import aiohttp

SERVER_PUBLIC_IP = "45.205.27.69"
# Public socat ports → residential exit IPs
PUBLIC_PROXIES = [
    f"socks5://{SERVER_PUBLIC_IP}:20854",   # HKT  112.120.48.16
    f"socks5://{SERVER_PUBLIC_IP}:20839",   # HKBN 219.76.13.x
    f"socks5://{SERVER_PUBLIC_IP}:20838",   # HKBN 219.76.13.177
]
# Local residential xray ports for browser
LOCAL_RESIDENTIAL = [
    "socks5://127.0.0.1:10854",  # HKT
    "socks5://127.0.0.1:10839",  # HKBN
    "socks5://127.0.0.1:10844",  # HKBN
    "socks5://127.0.0.1:10838",  # HKBN
]

def log(msg: str):
    print(msg, flush=True)

FIRST_NAMES = ["James","John","Robert","Michael","William","David","Richard","Joseph",
               "Thomas","Charles","Christopher","Daniel","Matthew","Anthony","Mark",
               "Steven","Paul","Andrew","Kenneth","Joshua","Kevin","Brian","George"]
LAST_NAMES  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
               "Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson",
               "Thomas","Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson"]

CF_STRINGS = [
    "performing security","just a moment","checking your browser",
    "正在进行安全验证","本网站使用安全服务","验证您不是自动程序",
    "cloudflare ray id","由 cloudflare 提供",
]
def _is_cf(text: str) -> bool:
    tl = text.lower()
    return any(s in tl for s in CF_STRINGS)

# ── CapSolver REST API ────────────────────────────────────────────────────────
async def _capsolver_solve_cf(api_key: str, website_url: str, proxy: str = "") -> dict:
    """
    AntiCloudflareTask: Returns solution with cf_clearance cookie.
    proxy must be publicly accessible (not localhost).
    """
    task: dict = {"type": "AntiCloudflareTask", "websiteURL": website_url}
    if proxy:
        task["proxy"] = proxy
    log(f"  [CapSolver] Creating task for {website_url} (proxy={proxy or 'none'})...")
    async with aiohttp.ClientSession() as session:
        r = await session.post("https://api.capsolver.com/createTask",
            json={"clientKey": api_key, "task": task},
            timeout=aiohttp.ClientTimeout(total=30))
        data = await r.json()
        if data.get("errorId") or data.get("errorCode"):
            raise RuntimeError(f"CapSolver createTask: {data.get('errorDescription', data)}")
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(f"CapSolver: no taskId: {data}")
        log(f"  [CapSolver] taskId={task_id}, polling...")
        for attempt in range(40):
            await asyncio.sleep(3)
            rr = await session.post("https://api.capsolver.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
                timeout=aiohttp.ClientTimeout(total=20))
            result = await rr.json()
            status = result.get("status")
            if status == "ready":
                solution = result.get("solution", {})
                log(f"  [CapSolver] Solved! cf_clearance: {'yes' if solution.get('cf_clearance') else 'no'}")
                return solution
            elif status == "failed":
                raise RuntimeError(f"CapSolver failed: {result.get('errorDescription', result)}")
            if attempt % 5 == 4:
                log(f"  [CapSolver] polling... t={attempt*3+3}s")
    raise RuntimeError("CapSolver timeout (120s)")


# ── CF Challenge via page.goto() navigation ───────────────────────────────────
async def _try_cf_navigate(page, cf_url: str, t0: float) -> str:
    """
    Navigate to CF challenge URL as a full page.
    CF JS runs in full page context (not fetch context) → can auto-solve.
    Returns cf_clearance value or empty string.
    """
    log(f"  [CF-nav] goto: {cf_url[:80]}...")
    try:
        await page.goto(cf_url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        log(f"  [CF-nav] goto exception (ignored): {str(e)[:80]}")

    for i in range(150):  # 5 min max
        await asyncio.sleep(2)
        try:
            cookies = await page.context.cookies()
            cf_ck = next((c for c in cookies if c["name"] == "cf_clearance"), None)
            if cf_ck:
                log(f"  [CF-nav] ✅ cf_clearance at t={i*2+2}s (len={len(cf_ck['value'])})")
                return cf_ck["value"]

            cur_url = page.url
            # CF solved → redirected away from challenge URL
            if "__cf_chl_f_tk" not in cur_url and "oxylabs.io" in cur_url:
                log(f"  [CF-nav] redirect detected → {cur_url[-50:]}")
                # Recheck cookies
                cookies = await page.context.cookies()
                cf_ck = next((c for c in cookies if c["name"] == "cf_clearance"), None)
                if cf_ck:
                    return cf_ck["value"]
                break

            if i % 15 == 14:
                txt = ""
                try: txt = (await page.locator("body").inner_text(timeout=2000))[:60]
                except: pass
                log(f"  [CF-nav] t={i*2+2}s | url={cur_url[-40:]} | {txt}")
        except Exception as e:
            log(f"  [CF-nav] poll error: {str(e)[:60]}")

    return ""


# ── Main registration ─────────────────────────────────────────────────────────
async def register_oxylabs(
    email: str, password: str,
    first_name: str = "", last_name: str = "",
    proxy: str = "", headless: bool = True,
    capsolver_key: str = "",
) -> dict:
    t0 = time.time()
    if not first_name: first_name = random.choice(FIRST_NAMES)
    if not last_name:  last_name  = random.choice(LAST_NAMES)

    capsolver_key = capsolver_key or os.environ.get("CAPSOLVER_API_KEY", "")
    proxy = proxy or os.environ.get("OXY_PROXY", "socks5://127.0.0.1:10854")

    result: dict = {
        "success": False, "email": email, "password": password,
        "first_name": first_name, "last_name": last_name,
        "username": "", "error": "", "elapsed": "",
    }

    log(f"📧 {email} | 👤 {first_name} {last_name}")
    log(f"🌐 Proxy: {proxy} | CapSolver: {'KEY_PROVIDED' if capsolver_key else 'NO_KEY'}")

    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        result["error"] = "camoufox not installed"
        return result

    # Detect Xvfb display for headed mode
    display = None
    for d in [":99", ":1", ":0", ":100"]:
        lock = f"/tmp/.X{d.replace(':','')}-lock"
        sock = f"/tmp/.X11-unix/X{d.replace(':','')}"
        if os.path.exists(lock) or os.path.exists(sock):
            display = d
            break

    log(f"🖥️  Xvfb display: {display or 'not found, headless forced'}")

    proxy_cfg = {"server": proxy} if proxy else None
    seon_fp: dict = {}
    cf_token_data: dict = {}
    api_responses: list = []

    async def _on_request(req):
        if "/api/v1/users" in req.url and req.method == "POST":
            try:
                d = json.loads(req.post_data or "{}")
                if d.get("deviceFingerprint"):
                    seon_fp["value"] = d["deviceFingerprint"]
                    log(f"  [SEON] {d['deviceFingerprint'][:60]}...")
            except Exception: pass

    async def _on_response(r):
        if "/api/v1/users" in r.url and r.request.method == "POST":
            s = r.status
            try: body = await r.text()
            except: body = ""
            ts = time.time() - t0
            api_responses.append({"status": s, "body": body, "t": ts})
            log(f"  [API t={ts:.1f}s] HTTP {s}: {body[:100]}")
            if s == 403:
                # Extract CF challenge "fa" token URL
                m = re.search(r'"fa":"(/api/v1/users\?__cf_chl_f_tk=[^"]+)"', body)
                if not m:
                    m = re.search(r"fa:'(/api/v1/users\?__cf_chl_f_tk=[^']+)'", body)
                if not m:
                    m = re.search(r'fa:"?(/api/v1/users\?__cf_chl_f_tk=\S+?)["&,\s]', body)
                if m:
                    cf_token_data["fa"] = m.group(1)
                    log(f"  [CF-fa] {cf_token_data['fa'][:80]}")
                # Also try to get cray/cType
                for k in ["cType","cFPWv","cRay"]:
                    mm = re.search(f'{k}:"?([^,"}}]+)"?', body)
                    if mm: cf_token_data[k] = mm.group(1)
                if cf_token_data.get("cType"):
                    log(f"  [CF-info] cType={cf_token_data.get('cType')} cFPWv={cf_token_data.get('cFPWv')}")

    # ── Browser launch + form fill ─────────────────────────────────────────────
    env_override = {}
    if display and not headless:
        env_override["DISPLAY"] = display

    async with AsyncCamoufox(
        headless=headless, os="windows",
        geoip=bool(proxy_cfg), proxy=proxy_cfg,
    ) as browser:
        page = await browser.new_page()
        ua = await page.evaluate("navigator.userAgent")
        log(f"🦊 UA: {ua}")

        page.on("request", _on_request)
        page.on("response", _on_response)

        # ── 1. Load page ────────────────────────────────────────────────────
        log("🌐 Loading registration page...")
        await page.goto("https://dashboard.oxylabs.io/registration",
                        wait_until="domcontentloaded", timeout=90_000)
        for i in range(25):
            await asyncio.sleep(1)
            if len(await page.query_selector_all("input")) >= 3:
                log(f"  ✅ Form ready t={i+1}s"); break
        else:
            result["error"] = "Form did not render (CF may block page load)"
            result["elapsed"] = f"{time.time()-t0:.1f}s"
            return result

        # ── 2. Fill form ────────────────────────────────────────────────────
        log("✏️ Filling form...")
        for field, val in [("name", first_name), ("surname", last_name),
                           ("email", email), ("password", password)]:
            el = await page.query_selector(f"input[name='{field}']")
            if el and await el.is_visible():
                await el.click(); await asyncio.sleep(0.15)
                await el.fill(""); await asyncio.sleep(0.05)
                await page.keyboard.type(val, delay=random.randint(50, 90))
                await asyncio.sleep(random.uniform(0.25, 0.45))
                log(f"  ✓ {field}: {val}")

        # ── 3. SEON wait + Submit ───────────────────────────────────────────
        log("⏳ SEON (7s)..."); await asyncio.sleep(7)
        btn = await page.query_selector("button[type='submit']")
        if not btn:
            result["error"] = "Submit button not found"
            result["elapsed"] = f"{time.time()-t0:.1f}s"
            return result
        await btn.click()
        log("🖱️ Submitted!")

        # ── 4. Wait for initial API response (up to 15s) ───────────────────
        for _ in range(15):
            await asyncio.sleep(1)
            if api_responses or cf_token_data: break

        # ── 5. Handle outcome ──────────────────────────────────────────────
        # Success or soft error
        for resp in api_responses:
            if resp["status"] in (200, 201):
                log("✅ Direct success (no CF)!")
                result["success"] = True
                result["username"] = email.split("@")[0]
                result["elapsed"] = f"{time.time()-t0:.1f}s"
                return result
            if resp["status"] in (400, 422):
                bl = resp["body"].lower()
                if "already" in bl or "exist" in bl:
                    result["error"] = "Email already registered"
                elif "password" in bl:
                    result["error"] = f"Password validation: {resp['body'][:120]}"
                else:
                    result["error"] = f"HTTP {resp['status']}: {resp['body'][:150]}"
                result["elapsed"] = f"{time.time()-t0:.1f}s"
                return result
            if resp["status"] == 429:
                result["error"] = "Rate limited (429)"
                result["elapsed"] = f"{time.time()-t0:.1f}s"
                return result

        # ── 6. CF Managed Challenge detected ──────────────────────────────
        if cf_token_data.get("fa") or any(r["status"] == 403 for r in api_responses):
            log("⚠️  CF Managed Challenge on POST /api/v1/users")
            log(f"    cType={cf_token_data.get('cType','?')} cFPWv={cf_token_data.get('cFPWv','?')}")

            cf_clearance_value = ""

            # ── 6a. CapSolver (best option) ────────────────────────────────
            if capsolver_key:
                log("🔧 CapSolver AntiCloudflareTask...")
                for pub_proxy in PUBLIC_PROXIES:
                    try:
                        solution = await _capsolver_solve_cf(
                            capsolver_key,
                            "https://dashboard.oxylabs.io/en/registration",
                            proxy=pub_proxy,  # Public residential proxy for CapSolver
                        )
                        cf_clearance_value = solution.get("cf_clearance", "")
                        if cf_clearance_value:
                            log(f"  ✅ cf_clearance from CapSolver! (len={len(cf_clearance_value)})")
                            # Inject cf_clearance
                            await page.context.add_cookies([{
                                "name": "cf_clearance",
                                "value": cf_clearance_value,
                                "domain": "dashboard.oxylabs.io",
                                "path": "/", "secure": True,
                            }])
                            for ck in (solution.get("cookies") or []):
                                if isinstance(ck, dict) and ck.get("name"):
                                    try:
                                        await page.context.add_cookies([{
                                            "name": ck["name"], "value": ck.get("value",""),
                                            "domain": ck.get("domain","dashboard.oxylabs.io"),
                                            "path": ck.get("path","/"),
                                        }])
                                    except: pass
                            break
                        else:
                            log(f"  ⚠ No cf_clearance from CapSolver (proxy={pub_proxy}), trying next...")
                    except Exception as e:
                        log(f"  ❌ CapSolver error: {e} — trying next proxy...")
                        continue

            # ── 6b. Navigate to CF challenge URL (browser-based) ───────────
            if not cf_clearance_value and cf_token_data.get("fa"):
                cf_url = "https://dashboard.oxylabs.io" + cf_token_data["fa"]
                log("🌍 CF challenge page navigation (browser-based)...")
                log(f"  Proxies to try: {LOCAL_RESIDENTIAL}")
                
                # Try with multiple residential proxies
                for res_proxy in LOCAL_RESIDENTIAL:
                    log(f"  Trying proxy: {res_proxy}")
                    try:
                        # Recreate browser context with this proxy for CF challenge
                        new_ctx = await browser.new_context(
                            proxy={"server": res_proxy}
                        )
                        cf_page = await new_ctx.new_page()
                        
                        # Set Xvfb display for better rendering
                        if display:
                            os.environ["DISPLAY"] = display
                        
                        cf_clearance_value = await _try_cf_navigate(cf_page, cf_url, t0)
                        
                        if cf_clearance_value:
                            # Copy cf_clearance to main context
                            await page.context.add_cookies([{
                                "name": "cf_clearance",
                                "value": cf_clearance_value,
                                "domain": "dashboard.oxylabs.io",
                                "path": "/", "secure": True,
                            }])
                            await cf_page.close()
                            await new_ctx.close()
                            log(f"  ✅ Got cf_clearance via CF navigation!")
                            break
                        
                        await cf_page.close()
                        await new_ctx.close()
                    except Exception as e:
                        log(f"  CF-nav proxy {res_proxy} error: {str(e)[:80]}")
                        continue

            # ── 6c. Give up with clear message ────────────────────────────
            if not cf_clearance_value:
                msg = (
                    "CF Managed Challenge not solved. "
                    "To fix: provide CAPSOLVER_API_KEY (https://capsolver.com, ~$0.001/solve). "
                    f"CF params: cType={cf_token_data.get('cType','?')}, "
                    f"cFPWv={cf_token_data.get('cFPWv','?')}"
                )
                result["error"] = msg
                result["elapsed"] = f"{time.time()-t0:.1f}s"
                log(f"❌ {msg}")
                return result

            # ── 7. Retry POST with cf_clearance ────────────────────────────
            log("📡 Retrying POST /api/v1/users with cf_clearance...")

            # Navigate back to registration page
            if "registration" not in page.url:
                try:
                    await page.goto("https://dashboard.oxylabs.io/en/registration",
                                    wait_until="domcontentloaded", timeout=30_000)
                    await asyncio.sleep(3)
                except Exception as e:
                    log(f"  Navigate back: {str(e)[:80]}")

            ga_id = await page.evaluate("""
() => {
    try { const m=document.cookie.match(/_ga=GA\\d\\.\\d\\.([\\d.]+)/);return m?m[1]:''; }
    catch(e){return '';}
}
""")
            seon_val = seon_fp.get("value", "")
            if not seon_val:
                seon_val = f"Web;{email};camoufox-v6;en-US"

            api_responses.clear()
            retry_result = await page.evaluate("""
async (payload) => {
    try {
        const resp = await fetch('/api/v1/users', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/plain, */*',
            },
            credentials: 'include',
            body: JSON.stringify(payload),
        });
        const text = await resp.text();
        return { status: resp.status, body: text.substring(0, 600) };
    } catch(e) {
        return { status: 0, body: 'fetch error: ' + e.toString() };
    }
}
""", {
                "email": email, "password": password,
                "name": first_name, "surname": last_name,
                "websiteTrackingId": "oxylabs-registration",
                "gaClientId": ga_id or "",
                "region": "Global",
                "deviceFingerprint": seon_val,
            })

            status = retry_result.get("status", 0)
            body   = retry_result.get("body", "")
            log(f"  [Retry] HTTP {status}: {body[:200]}")

            if status in (200, 201):
                log("✅ Registration SUCCESS after CF bypass!")
                result["success"] = True
                result["username"] = email.split("@")[0]
            elif status == 403:
                result["error"] = "CF still blocking after bypass (cf_clearance may have expired)"
            elif status in (400, 422):
                bl = body.lower()
                if "already" in bl or "exist" in bl:
                    result["error"] = "Email already registered"
                elif "password" in bl:
                    result["error"] = f"Password issue: {body[:120]}"
                else:
                    result["error"] = f"Validation HTTP {status}: {body[:150]}"
            else:
                result["error"] = f"HTTP {status}: {body[:150]}"

        elif not api_responses:
            # No API response at all — form fill or submit failed
            log("⚠️  No API response after submit — checking form state...")
            try:
                vals = await page.evaluate("""
() => JSON.stringify(Object.fromEntries(
    [...document.querySelectorAll('input[name]')].map(i=>[i.name,i.value])
))
""")
                log(f"  Form values: {vals[:200]}")
                page_txt = (await page.locator("body").inner_text(timeout=5000))[:200]
                log(f"  Page: {page_txt[:150]}")
            except Exception as e:
                log(f"  State check error: {e}")
            result["error"] = "Form submission produced no API response (button click may have failed)"
        
        result["elapsed"] = f"{time.time()-t0:.1f}s"
        log(f"{'✅ Done' if result['success'] else '❌ Failed'}: {result.get('error','')[:100]} in {result['elapsed']}")
        return result


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Oxylabs registration v6")
    ap.add_argument("--email",    required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--first",  default="")
    ap.add_argument("--last",   default="")
    ap.add_argument("--proxy",  default="")
    ap.add_argument("--headless", default="true")
    ap.add_argument("--capsolver-key", default="", dest="capsolver_key")
    args = ap.parse_args()
    r = asyncio.run(register_oxylabs(
        args.email, args.password,
        first_name=args.first, last_name=args.last,
        proxy=args.proxy,
        headless=args.headless.lower() not in ("false","0","no"),
        capsolver_key=args.capsolver_key,
    ))
    print("\n── JSON Result ──")
    print(json.dumps(r, ensure_ascii=False, indent=2))
