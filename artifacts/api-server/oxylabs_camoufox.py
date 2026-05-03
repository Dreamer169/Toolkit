#!/usr/bin/env python3
"""
Oxylabs.io 注册 - camoufox 版 (Firefox fingerprint, stronger CF bypass)
"""
import asyncio, json, time, random, argparse

def log(msg):
    print(msg, flush=True)

FIRST_NAMES = ["James","John","Robert","Michael","William","David","Richard","Joseph",
               "Thomas","Charles","Christopher","Daniel","Matthew","Anthony","Mark"]
LAST_NAMES  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
               "Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson"]

async def register_oxylabs(email, password, first_name="", last_name="", proxy="", headless=True):
    t0 = time.time()
    result = {
        "success": False, "email": email, "password": password,
        "first_name": first_name or random.choice(FIRST_NAMES),
        "last_name":  last_name  or random.choice(LAST_NAMES),
        "username": "", "error": "", "elapsed": ""
    }
    if not first_name: first_name = result["first_name"]
    if not last_name:  last_name  = result["last_name"]

    from camoufox.async_api import AsyncCamoufox

    log("✅ camoufox 已加载 (Firefox)")
    log(f"📧 {email} | 👤 {first_name} {last_name}")
    if proxy: log(f"🌐 代理: {proxy[:60]}")
    log("🚀 启动 Firefox (camoufox)...")

    launch_kwargs: dict = {
        "headless": headless,
        "os": "windows",
        "locale": ["en-US", "en"],
        "screen": {"width": 1366, "height": 768},
    }
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}

    api_response: dict = {}

    async with AsyncCamoufox(**launch_kwargs) as browser:
        page = await browser.new_page()

        async def on_response(resp):
            if "/api/v1/users" in resp.url and resp.request.method == "POST":
                status = resp.status
                api_response["status"] = status
                try:
                    body = await resp.text()
                    api_response["body"] = body
                except Exception:
                    api_response["body"] = ""
                log(f"📡 /api/v1/users → HTTP {status}: {api_response['body'][:200]}")

        page.on("response", on_response)

        # Navigate
        log("🌐 打开注册页...")
        await page.goto("https://dashboard.oxylabs.io/registration",
                        wait_until="domcontentloaded", timeout=60000)

        # Wait for form
        log("⏳ 等待表单...")
        for i in range(25):
            await asyncio.sleep(1)
            inputs = await page.query_selector_all("input")
            if len(inputs) >= 3:
                log(f"✅ 表单就绪 t={i+1}s, inputs={len(inputs)}")
                break
            if i % 5 == 4:
                log(f"  等待... t={i+1}s inputs={len(inputs)}")
        else:
            result["error"] = "表单未渲染"
            result["elapsed"] = f"{time.time()-t0:.1f}s"
            return result

        # Fill fields
        log("✏️ 填写表单...")
        for name, val, label in [
            ("name", first_name, "名"), ("surname", last_name, "姓"),
            ("email", email, "邮箱"), ("password", password, "密码")
        ]:
            el = await page.query_selector(f"input[name='{name}']")
            if el and await el.is_visible():
                await el.click()
                await asyncio.sleep(0.15)
                await el.fill("")
                await page.keyboard.type(val, delay=random.randint(50, 90))
                await asyncio.sleep(random.uniform(0.2, 0.4))
                log(f"  ✓ {label}: {val[:40]}")

        # Mouse movement
        await page.mouse.move(400 + random.randint(0,80), 300 + random.randint(0,60))
        await asyncio.sleep(0.5)

        # Wait for SEON
        log("⏳ SEON 指纹 (5s)...")
        await asyncio.sleep(5)

        try:
            await page.screenshot(path="/tmp/oxylabs_camoufox_before.png")
            log("📸 提交前截图")
        except Exception:
            pass

        # Submit
        log("🖱️ 提交...")
        btn = await page.query_selector("button[type='submit']")
        if btn and await btn.is_visible():
            await btn.click()
            log("  ✓ clicked submit")
        else:
            await page.keyboard.press("Enter")

        # Wait for result (CF Managed Challenge should auto-solve with Firefox)
        log("⏳ 等待 CF 验证 + API 响应 (最多 90s)...")
        for i in range(45):
            await asyncio.sleep(2)

            if api_response:
                status = api_response.get("status", 0)
                body   = api_response.get("body", "")
                bl     = body.lower()
                if status in (200, 201):
                    result["success"] = True
                    result["username"] = email.split("@")[0]
                    log("✅ 注册成功!")
                    break
                elif status in (400, 422):
                    result["error"] = "邮箱已注册" if "already" in bl or "exist" in bl else f"API {status}: {body[:150]}"
                    break
                elif status == 403:
                    log(f"  CF 403 t={i*2+2}s，等待 CF 自动绕过...")
                    api_response.clear()  # reset to wait for retry
                    continue
                elif status == 429:
                    result["error"] = "频率限制 429"; break
                else:
                    result["error"] = f"API {status}: {body[:100]}"; break

            # Check page
            cur_url = page.url
            try:
                txt = (await page.locator("body").inner_text())[:500]
            except Exception:
                txt = ""
            tl = txt.lower()

            if any(s in tl for s in ["performing security","just a moment","checking your browser"]):
                if i % 8 == 7:
                    log(f"  🔄 CF 验证 t={i*2+2}s...")
                continue

            if "/registration" not in cur_url and "/register" not in cur_url:
                result["success"] = True
                result["username"] = email.split("@")[0]
                log(f"✅ URL 跳转: {cur_url[-60:]}")
                break

            if any(s in tl for s in ["check your email","verify","sent","confirm","successfully"]):
                result["success"] = True
                result["username"] = email.split("@")[0]
                log("✅ 成功消息")
                break

            if "already" in tl and "email" in tl:
                result["error"] = "邮箱已被注册"; break

        else:
            if not result["error"]:
                try:
                    result["error"] = f"超时 | URL:{page.url[-50:]}"
                except Exception:
                    result["error"] = "超时"

        try:
            result["final_url"] = page.url
        except Exception:
            result["final_url"] = ""

        if result["success"]:
            log(f"✅ 完成!")
        else:
            log(f"❌ {result.get('error','')[:100]}")

    result["elapsed"] = f"{time.time()-t0:.1f}s"
    return result

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--email",    required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--first",    default="")
    ap.add_argument("--last",     default="")
    ap.add_argument("--proxy",    default="")
    ap.add_argument("--headless", default="true")
    args = ap.parse_args()
    headless = args.headless.lower() not in ("false","0","no")
    r = asyncio.run(register_oxylabs(
        args.email, args.password, args.first, args.last, args.proxy, headless
    ))
    print("\n── JSON 结果 ──")
    print(json.dumps(r, ensure_ascii=False, indent=2))
