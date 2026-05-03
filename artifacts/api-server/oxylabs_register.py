#!/usr/bin/env python3
"""
Oxylabs.io 全自动注册脚本
用法: python3 oxylabs_register.py --email user@outlook.com --password Pass123! [--first John] [--last Doe] [--company Acme] [--proxy socks5://...]
输出: JSON -> {success, email, password, first_name, last_name, company, username, error, elapsed}
"""
import argparse, asyncio, json, time, re, random, string

def log(msg: str):
    print(msg, flush=True)

def rand_str(n=8):
    return ''.join(random.choices(string.ascii_lowercase, k=n))

FIRST_NAMES = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas","Charles","Christopher","Daniel","Matthew","Anthony","Mark","Donald","Steven","Paul","Andrew","Kenneth","Joshua","Kevin","Brian","George","Timothy","Ronald","Edward","Jason","Jeffrey","Ryan","Jacob","Gary","Nicholas","Eric","Jonathan","Stephen","Larry","Justin","Scott","Brandon","Benjamin","Samuel","Frank","Gregory","Raymond","Frank","Patrick","Jack","Dennis","Jerry"]
LAST_NAMES  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson","White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson","Walker","Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Flores","Green","Adams","Nelson","Baker","Hall","Rivera","Campbell","Mitchell","Carter","Roberts"]
COMPANIES   = ["Nexus Digital","Atlas Media","Vertex Solutions","Prime Analytics","Orbit Technologies","Summit Data","Apex Research","Echo Systems","Pulse Labs","Nova Intelligence","Zenith Analytics","Crest Technologies","Harbor Digital","Beacon Research","Cascade Systems","Meridian Data","Pinnacle Labs","Quantum Media","Stellar Analytics","Horizon Technologies"]

async def register_oxylabs(
    email: str, password: str,
    first_name: str = "", last_name: str = "",
    company: str = "", phone: str = "",
    proxy: str = "", headless: bool = True
) -> dict:
    t0 = time.time()
    result = {
        "success": False, "email": email, "password": password,
        "first_name": first_name, "last_name": last_name,
        "company": company, "username": "", "error": "", "elapsed": ""
    }

    # auto-generate identity if not provided
    if not first_name:
        first_name = random.choice(FIRST_NAMES)
        result["first_name"] = first_name
    if not last_name:
        last_name = random.choice(LAST_NAMES)
        result["last_name"] = last_name
    if not company:
        company = random.choice(COMPANIES)
        result["company"] = company
    if not phone:
        phone = f"+1{random.randint(200,999)}{random.randint(100,999)}{random.randint(1000,9999)}"

    try:
        from patchright.async_api import async_playwright
        log("✅ patchright 可用")
    except ImportError:
        try:
            from playwright.async_api import async_playwright
            log("⚠ patchright 不可用，使用 playwright 回退")
        except ImportError:
            result["error"] = "patchright/playwright not installed"
            result["elapsed"] = f"{time.time()-t0:.1f}s"
            return result

    log(f"📧 邮箱: {email}")
    log(f"👤 姓名: {first_name} {last_name}")
    log(f"🏢 公司: {company}")
    log(f"📱 电话: {phone}")
    if proxy: log(f"🌐 代理: {proxy[:60]}")
    log("🚀 启动浏览器...")

    async with async_playwright() as p:
        browser = None
        try:
            launch_opts = {
                "headless": headless,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--window-size=1366,768",
                ],
            }
            # prefer chromium-1208 path if available (matches patchright)
            import os
            chrome_path = "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome"
            if os.path.exists(chrome_path):
                launch_opts["executable_path"] = chrome_path

            browser = await p.chromium.launch(**launch_opts)

            ctx_opts: dict = {
                "viewport": {"width": 1366, "height": 768},
                "locale": "en-US",
                "timezone_id": "America/New_York",
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            }
            if proxy:
                ctx_opts["proxy"] = {"server": proxy}

            ctx = await browser.new_context(**ctx_opts)
            await ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                window.chrome = {runtime: {}};
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            """)
            page = await ctx.new_page()

            log("🌐 打开 Oxylabs 注册页...")
            await page.goto(
                "https://dashboard.oxylabs.io/en/register",
                wait_until="networkidle",
                timeout=60_000,
            )
            await asyncio.sleep(3)
            log(f"✅ 页面加载完成，URL: {page.url[:80]}")

            # ── 等待表单渲染（React SPA 需要时间） ──────────────────────────
            email_sel = "input[name='email'], input[type='email'], input[placeholder*='email' i], input[id*='email' i]"
            try:
                await page.wait_for_selector(email_sel, timeout=20_000, state="visible")
                log("✅ 表单已渲染")
            except Exception:
                log("⚠ email 字段等待超时，继续尝试...")

            # ── helper: fill field by multiple selectors ──────────────────
            async def fill_field(selectors: list, value: str, label: str):
                for sel in selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el and await el.is_visible():
                            await el.click()
                            await asyncio.sleep(0.2)
                            await el.fill("")
                            await page.keyboard.type(value, delay=random.randint(40, 90))
                            log(f"  ✓ {label}: {value[:40]}")
                            return True
                    except Exception:
                        continue
                log(f"  ⚠ 未找到 {label} 字段")
                return False

            await asyncio.sleep(1)

            # ── 填写表单字段 ──────────────────────────────────────────────
            await fill_field(
                ["input[name='firstName']","input[id='firstName']","input[placeholder*='first' i]","input[autocomplete='given-name']"],
                first_name, "firstName"
            )
            await asyncio.sleep(0.3)

            await fill_field(
                ["input[name='lastName']","input[id='lastName']","input[placeholder*='last' i]","input[autocomplete='family-name']"],
                last_name, "lastName"
            )
            await asyncio.sleep(0.3)

            await fill_field(
                ["input[name='email']","input[type='email']","input[id='email']","input[placeholder*='email' i]"],
                email, "email"
            )
            await asyncio.sleep(0.3)

            await fill_field(
                ["input[name='password']","input[type='password']","input[id='password']"],
                password, "password"
            )
            await asyncio.sleep(0.3)

            # companyName (可能是文本输入或 combobox)
            await fill_field(
                ["input[name='companyName']","input[id='companyName']","input[placeholder*='company' i]","input[autocomplete='organization']"],
                company, "companyName"
            )
            await asyncio.sleep(0.3)

            # phone (optional, try to fill if visible)
            phone_filled = await fill_field(
                ["input[name='phone']","input[type='tel']","input[id='phone']","input[placeholder*='phone' i]"],
                phone, "phone"
            )
            await asyncio.sleep(0.5)

            # ── ToS / Privacy checkbox ─────────────────────────────────────
            for chk_sel in ["input[type='checkbox']", "[data-testid*='tos']", "[data-testid*='terms']"]:
                try:
                    boxes = await page.query_selector_all(chk_sel)
                    for box in boxes:
                        if await box.is_visible():
                            checked = await box.get_attribute("checked")
                            aria_checked = await box.get_attribute("aria-checked")
                            if not checked and aria_checked != "true":
                                await box.click()
                                log("  ✓ 已勾选条款复选框")
                                await asyncio.sleep(0.3)
                except Exception:
                    pass

            # ── 等待 SEON 加载 ─────────────────────────────────────────────
            log("⏳ 等待 SEON 指纹采集 (4s)...")
            await asyncio.sleep(4)

            # ── 截图记录表单状态 ──────────────────────────────────────────
            try:
                await page.screenshot(path="/tmp/oxylabs_before_submit.png")
                log("📸 提交前截图已保存: /tmp/oxylabs_before_submit.png")
            except Exception:
                pass

            # ── 提交表单 ──────────────────────────────────────────────────
            log("🖱️ 点击注册按钮...")
            submit_clicked = False
            for sel in [
                "button[type='submit']",
                "button[data-testid*='register']",
                "button[data-testid*='submit']",
                "button:has-text('Create account')",
                "button:has-text('Register')",
                "button:has-text('Sign up')",
                "button:has-text('Get started')",
                "button:has-text('Start free')",
                "form button",
            ]:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        log(f"  ✓ 点击按钮: {sel}")
                        submit_clicked = True
                        break
                except Exception:
                    continue

            if not submit_clicked:
                log("  ⚠ 未找到提交按钮，尝试 Enter 键")
                await page.keyboard.press("Enter")

            # ── 等待结果 ──────────────────────────────────────────────────
            log("⏳ 等待注册结果 (最多 40s)...")
            final_url = page.url
            success = False
            error_msg = ""

            for _ in range(20):
                await asyncio.sleep(2)
                cur_url = page.url
                body_text = ""
                try:
                    body_text = (await page.locator("body").inner_text())[:800]
                except Exception:
                    pass
                bl = body_text.lower()

                # success: URL changed away from register, or success message
                if "/register" not in cur_url:
                    log(f"✅ URL 已跳转: {cur_url[:80]}")
                    success = True
                    final_url = cur_url
                    break

                # success signals in page
                if any(s in bl for s in [
                    "check your email", "verify your email", "confirmation email",
                    "we sent", "sent you", "almost there", "one more step",
                    "activate your account", "please confirm"
                ]):
                    log(f"✅ 注册成功（邮件验证提示）: {body_text[:120]}")
                    success = True
                    break

                # error signals
                if "already" in bl and ("email" in bl or "exist" in bl):
                    error_msg = "邮箱已被注册"
                    break
                if "invalid email" in bl:
                    error_msg = "邮箱格式无效"
                    break
                if "password" in bl and ("weak" in bl or "short" in bl or "least" in bl):
                    error_msg = "密码强度不足"
                    break

            if not success and not error_msg:
                # final check
                try:
                    cur_url = page.url
                    body_text = (await page.locator("body").inner_text())[:800]
                    bl = body_text.lower()
                    if "/register" not in cur_url:
                        success = True
                        final_url = cur_url
                    elif any(s in bl for s in ["check your email","verify","sent","confirmation"]):
                        success = True
                    else:
                        try:
                            await page.screenshot(path="/tmp/oxylabs_fail.png")
                        except Exception:
                            pass
                        error_msg = f"注册超时，当前URL: {cur_url[:80]}"
                except Exception as e:
                    error_msg = str(e)

            result["success"] = success
            result["error"] = error_msg
            result["final_url"] = page.url[:120]

            if success:
                log(f"✅ 注册成功！邮箱: {email}")
                # extract username from email
                result["username"] = email.split("@")[0]

        except Exception as e:
            result["error"] = str(e)
            log(f"❌ 异常: {e}")
            try:
                await page.screenshot(path="/tmp/oxylabs_error.png")
            except Exception:
                pass
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass

    result["elapsed"] = f"{time.time()-t0:.1f}s"
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Oxylabs.io 自动注册")
    ap.add_argument("--email",    required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--first",    default="",    help="名（留空自动生成）")
    ap.add_argument("--last",     default="",    help="姓（留空自动生成）")
    ap.add_argument("--company",  default="",    help="公司名（留空自动生成）")
    ap.add_argument("--phone",    default="",    help="电话（留空自动生成）")
    ap.add_argument("--proxy",    default="",    help="socks5://... 代理")
    ap.add_argument("--headless", default="true")
    args = ap.parse_args()

    headless = args.headless.lower() not in ("false", "0", "no")
    r = asyncio.run(register_oxylabs(
        args.email, args.password,
        args.first, args.last, args.company, args.phone,
        args.proxy, headless
    ))
    print("\n── JSON 结果 ──")
    print(json.dumps(r, ensure_ascii=False, indent=2))
