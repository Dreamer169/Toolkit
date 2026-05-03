#!/usr/bin/env python3
"""
Oxylabs.io 全自动注册脚本 v5
策略:
  1. camoufox (Firefox) + geoip + proxy 填写表单，捕获 SEON deviceFingerprint
  2. POST /api/v1/users — 若得到 CF 403 Managed Challenge:
     a. 有 CAPSOLVER_API_KEY → 调用 CapSolver AntiCloudflareTask 解决
     b. 无 API Key → 等待浏览器内联 CF 自动解决 (最多 3 分钟)
  3. 取得 cf_clearance cookie 后重发 POST
  4. 返回结果 dict

环境变量:
  CAPSOLVER_API_KEY  — CapSolver API Key (推荐)
  OXY_PROXY          — SOCKS5/HTTP 代理 (可选, 格式: socks5://host:port)
"""
import argparse, asyncio, json, os, re, time, random
import aiohttp

def log(msg: str):
    print(msg, flush=True)

FIRST_NAMES = ["James","John","Robert","Michael","William","David","Richard","Joseph",
               "Thomas","Charles","Christopher","Daniel","Matthew","Anthony","Mark",
               "Steven","Paul","Andrew","Kenneth","Joshua","Kevin","Brian","George"]
LAST_NAMES  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
               "Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson",
               "Thomas","Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson"]

# CF challenge detection strings (EN + ZH)
CF_STRINGS = [
    "performing security", "just a moment", "checking your browser",
    "正在进行安全验证", "本网站使用安全服务", "验证您不是自动程序",
    "cloudflare ray id", "由 cloudflare 提供",
]

def _is_cf_page(text: str) -> bool:
    tl = text.lower()
    return any(s in tl for s in CF_STRINGS)


# ── CapSolver REST API ────────────────────────────────────────────────────────

async def _capsolver_solve_cf(api_key: str, website_url: str, proxy: str = "") -> dict:
    """
    Call CapSolver AntiCloudflareTask to obtain cf_clearance cookie.
    Returns solution dict with keys: cf_clearance, user_agent, cookies
    Raises on failure.
    """
    task: dict = {
        "type": "AntiCloudflareTask",
        "websiteURL": website_url,
    }
    if proxy:
        task["proxy"] = proxy  # format: "socks5://host:port" or "http://user:pass@host:port"

    log(f"  [CapSolver] Creating AntiCloudflareTask for {website_url}...")
    async with aiohttp.ClientSession() as session:
        # Step 1: Create task
        create_resp = await session.post(
            "https://api.capsolver.com/createTask",
            json={"clientKey": api_key, "task": task},
            timeout=aiohttp.ClientTimeout(total=30),
        )
        create_data = await create_resp.json()

        if create_data.get("errorId") or create_data.get("errorCode"):
            raise RuntimeError(f"CapSolver createTask error: {create_data.get('errorDescription', create_data)}")

        task_id = create_data.get("taskId")
        if not task_id:
            raise RuntimeError(f"CapSolver: no taskId in response: {create_data}")
        log(f"  [CapSolver] taskId={task_id}, polling...")

        # Step 2: Poll for result (max 120s)
        for attempt in range(40):
            await asyncio.sleep(3)
            result_resp = await session.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
                timeout=aiohttp.ClientTimeout(total=20),
            )
            result_data = await result_resp.json()
            status = result_data.get("status")

            if status == "ready":
                solution = result_data.get("solution", {})
                log(f"  [CapSolver] ✅ Solved! cf_clearance={'present' if solution.get('cf_clearance') else 'missing'}")
                return solution
            elif status == "failed":
                raise RuntimeError(f"CapSolver task failed: {result_data.get('errorDescription', result_data)}")
            else:
                if attempt % 5 == 4:
                    log(f"  [CapSolver] still processing... t={attempt*3+3}s status={status}")

    raise RuntimeError("CapSolver: timed out after 120s")


# ── Main registration function ────────────────────────────────────────────────

async def register_oxylabs(
    email: str,
    password: str,
    first_name: str = "",
    last_name: str = "",
    proxy: str = "",
    headless: bool = True,
    capsolver_key: str = "",
) -> dict:
    t0 = time.time()

    if not first_name:
        first_name = random.choice(FIRST_NAMES)
    if not last_name:
        last_name = random.choice(LAST_NAMES)

    # Merge env var with arg
    capsolver_key = capsolver_key or os.environ.get("CAPSOLVER_API_KEY", "")
    proxy = proxy or os.environ.get("OXY_PROXY", "socks5://127.0.0.1:10821")

    result: dict = {
        "success": False, "email": email, "password": password,
        "first_name": first_name, "last_name": last_name,
        "username": "", "error": "", "elapsed": "",
    }

    log(f"📧 {email} | 👤 {first_name} {last_name}")
    log(f"🌐 Proxy: {proxy or 'none'} | CapSolver: {'yes' if capsolver_key else 'no key'}")

    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        result["error"] = "camoufox not installed. Run: pip install camoufox"
        result["elapsed"] = f"{time.time()-t0:.1f}s"
        return result

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
                    log(f"  [SEON] captured: {d['deviceFingerprint'][:60]}...")
            except Exception:
                pass

    async def _on_response(r):
        if "/api/v1/users" in r.url and r.request.method == "POST":
            s = r.status
            try:
                body = await r.text()
            except Exception:
                body = ""
            api_responses.append({"status": s, "body": body, "t": time.time() - t0})
            log(f"  [API] HTTP {s}: {body[:120]}")
            if s == 403:
                m = re.search(r'fa:"(/api/v1/users\?__cf_chl_f_tk=[^"]+)"', body)
                if m:
                    cf_token_data["fa"] = m.group(1)
                    log(f"  [CF token] {cf_token_data['fa'][:80]}")

    async with AsyncCamoufox(
        headless=headless, os="windows",
        geoip=bool(proxy_cfg), proxy=proxy_cfg
    ) as browser:
        page = await browser.new_page()
        ua = await page.evaluate("navigator.userAgent")
        log(f"🦊 Firefox UA: {ua}")

        page.on("request", _on_request)
        page.on("response", _on_response)

        # ── 1. Load registration page ──────────────────────────────────────
        log("🌐 Loading registration page...")
        await page.goto(
            "https://dashboard.oxylabs.io/registration",
            wait_until="domcontentloaded", timeout=90_000,
        )
        for i in range(25):
            await asyncio.sleep(1)
            inputs = await page.query_selector_all("input")
            if len(inputs) >= 3:
                log(f"  ✅ Form ready t={i+1}s"); break
        else:
            result["error"] = "Form did not render (possible CF block on page load)"
            result["elapsed"] = f"{time.time()-t0:.1f}s"
            return result

        # ── 2. Fill form ───────────────────────────────────────────────────
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

        # ── 3. Wait for SEON + Submit ──────────────────────────────────────
        log("⏳ SEON collection (7s)...")
        await asyncio.sleep(7)

        btn = await page.query_selector("button[type='submit']")
        if not btn:
            result["error"] = "Submit button not found"
            result["elapsed"] = f"{time.time()-t0:.1f}s"
            return result

        await btn.click()
        log("🖱️ Submitted!")

        # ── 4. Wait for initial API response ──────────────────────────────
        for i in range(15):
            await asyncio.sleep(1)
            if api_responses or cf_token_data:
                break

        # ── 5. Handle success / CF / error ────────────────────────────────
        # Check if direct success (no CF)
        for resp in api_responses:
            if resp["status"] in (200, 201):
                log("✅ Direct registration success!")
                result["success"] = True
                result["username"] = email.split("@")[0]
                result["elapsed"] = f"{time.time()-t0:.1f}s"
                return result
            if resp["status"] in (400, 422):
                body = resp["body"]
                if "already" in body.lower() or "exist" in body.lower():
                    result["error"] = "Email already registered"
                elif "password" in body.lower():
                    result["error"] = f"Password validation: {body[:120]}"
                else:
                    result["error"] = f"Validation error HTTP {resp['status']}: {body[:150]}"
                result["elapsed"] = f"{time.time()-t0:.1f}s"
                return result

        # CF 403 detected
        if cf_token_data.get("fa") or any(r["status"] == 403 for r in api_responses):
            log("⚠️  CF Managed Challenge detected on POST /api/v1/users")

            cf_solved = False
            cf_clearance_value = ""

            # ── 5a. CapSolver path ─────────────────────────────────────────
            if capsolver_key:
                log("🔧 Solving CF via CapSolver AntiCloudflareTask...")
                try:
                    # Use our proxy for CapSolver (must be publicly accessible)
                    # For local socks5 proxies, CapSolver cannot reach them.
                    # We pass proxy only if it's a non-localhost address.
                    caps_proxy = ""
                    if proxy and "127.0.0.1" not in proxy and "localhost" not in proxy:
                        caps_proxy = proxy
                    
                    solution = await _capsolver_solve_cf(
                        capsolver_key,
                        "https://dashboard.oxylabs.io/en/registration",
                        proxy=caps_proxy,
                    )

                    cf_clearance_value = solution.get("cf_clearance", "")
                    cookies_from_solution = solution.get("cookies", [])
                    ua_from_solution = solution.get("user_agent", "")

                    if cf_clearance_value:
                        # Inject cf_clearance into browser context
                        await page.context.add_cookies([{
                            "name": "cf_clearance",
                            "value": cf_clearance_value,
                            "domain": "dashboard.oxylabs.io",
                            "path": "/",
                            "httpOnly": False,
                            "secure": True,
                        }])
                        for ck in cookies_from_solution:
                            if isinstance(ck, dict) and ck.get("name") and ck.get("value"):
                                try:
                                    await page.context.add_cookies([{
                                        "name": ck["name"],
                                        "value": ck["value"],
                                        "domain": ck.get("domain", "dashboard.oxylabs.io"),
                                        "path": ck.get("path", "/"),
                                    }])
                                except Exception:
                                    pass
                        log(f"  ✅ cf_clearance injected (len={len(cf_clearance_value)})")
                        cf_solved = True
                    else:
                        log("  ⚠ CapSolver returned no cf_clearance, falling back...")

                except Exception as e:
                    log(f"  ❌ CapSolver failed: {e} — falling back to browser wait")

            # ── 5b. Browser inline wait (fallback) ────────────────────────
            if not cf_solved:
                log("⏳ Waiting for browser to inline-solve CF challenge (max 3 min)...")
                for i in range(90):
                    await asyncio.sleep(2)

                    cookies = await page.context.cookies()
                    cf_ck = next((c for c in cookies if c["name"] == "cf_clearance"), None)
                    if cf_ck:
                        cf_clearance_value = cf_ck["value"]
                        log(f"  ✅ cf_clearance appeared at t={i*2+2}s!")
                        cf_solved = True
                        break

                    # Check any new successful POST in background
                    for resp in api_responses:
                        if resp["status"] in (200, 201):
                            log(f"  ✅ POST succeeded (CF auto-solved) at t={resp['t']:.1f}s!")
                            result["success"] = True
                            result["username"] = email.split("@")[0]
                            result["elapsed"] = f"{time.time()-t0:.1f}s"
                            return result

                    if i % 15 == 14:
                        log(f"  Still waiting... t={i*2+2}s")

                if not cf_solved:
                    log("  ❌ CF challenge never resolved (browser wait exhausted)")
                    result["error"] = (
                        "CF Managed Challenge not bypassed. "
                        "Set CAPSOLVER_API_KEY env var for reliable bypass. "
                        "Get key at https://capsolver.com"
                    )
                    result["elapsed"] = f"{time.time()-t0:.1f}s"
                    return result

            # ── 6. Navigate back and retry POST ───────────────────────────
            if "registration" not in page.url:
                log("🔄 Navigate to registration for POST retry...")
                await page.goto(
                    "https://dashboard.oxylabs.io/registration",
                    wait_until="domcontentloaded", timeout=60_000,
                )
                await asyncio.sleep(3)

            seon_val = seon_fp.get("value", "Web;Browser;1366x768;en-US;24;Firefox;135.0;Windows")
            ga_id = await page.evaluate("""
() => {
    try {
        const ga = document.cookie.match(/_ga=GA\\d\\.\\d\\.([\\d.]+)/);
        return ga ? ga[1] : '';
    } catch(e) { return ''; }
}
""")

            log("📡 Retrying POST /api/v1/users with CF clearance...")
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
                "email": email,
                "password": password,
                "name": first_name,
                "surname": last_name,
                "websiteTrackingId": "oxylabs-registration",
                "gaClientId": ga_id or "",
                "region": "Global",
                "deviceFingerprint": seon_val,
            })

            status = retry_result.get("status", 0)
            body = retry_result.get("body", "")
            log(f"  [Retry] HTTP {status}: {body[:200]}")

            if status in (200, 201):
                log("✅ Registration SUCCESS after CF bypass!")
                result["success"] = True
                result["username"] = email.split("@")[0]
            elif status == 403:
                result["error"] = "CF still blocking after bypass attempt"
            elif status in (400, 422):
                bl = body.lower()
                if "already" in bl or "exist" in bl:
                    result["error"] = "Email already registered"
                elif "password" in bl:
                    result["error"] = f"Password issue: {body[:120]}"
                else:
                    result["error"] = f"Validation HTTP {status}: {body[:150]}"
            elif status == 429:
                result["error"] = "Rate limited (429) — try again later"
            else:
                result["error"] = f"Unexpected HTTP {status}: {body[:150]}"

        elif not api_responses:
            # No response at all — form submit likely failed
            log("⚠️  No API response — checking page state...")
            try:
                page_txt = await page.locator("body").inner_text(timeout=5000)
                log(f"  Page: {page_txt[:200]}")
                # Try fetching SEON FP directly and making manual POST
                result["error"] = f"Form submission produced no API response. Page: {page_txt[:200]}"
            except Exception as e:
                result["error"] = f"No API response and page error: {e}"

        result["elapsed"] = f"{time.time()-t0:.1f}s"
        if result["success"]:
            log(f"✅ Done: {email} registered in {result['elapsed']}")
        else:
            log(f"❌ Failed: {result['error'][:100]} in {result['elapsed']}")
        return result


# ── CLI entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Oxylabs.io auto-registration v5")
    ap.add_argument("--email",    required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--first",    default="")
    ap.add_argument("--last",     default="")
    ap.add_argument("--proxy",    default="")
    ap.add_argument("--headless", default="true")
    ap.add_argument("--capsolver-key", default="", dest="capsolver_key")
    args = ap.parse_args()

    r = asyncio.run(register_oxylabs(
        args.email, args.password,
        first_name=args.first, last_name=args.last,
        proxy=args.proxy,
        headless=args.headless.lower() not in ("false", "0", "no"),
        capsolver_key=args.capsolver_key,
    ))
    print("\n── JSON Result ──")
    print(json.dumps(r, ensure_ascii=False, indent=2))
