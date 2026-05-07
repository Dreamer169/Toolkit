#!/usr/bin/env python3
"""
obvious_email_factory_code.py — 基于 outlook_factory_sandbox.py v3 + EASI 修复
关键修复:
  1. click_submit() — 单次点击: 不双发 (submit + role/Next)
  2. input[name="email"] — EASI 新流程邮箱字段
  3. birthday: JS click #BirthMonthDropdown/#BirthDayDropdown (FluentUI)
  4. name 步骤: 可在生日后出现 (EASI 流程)
  5. nest_asyncio — sandbox uvicorn 事件循环兼容
  6. 输出 RESULT: JSON 给 pipeline 解析
  7. VPS push 到 8084/emails/push
"""
import asyncio, json, os, random, re, string, subprocess, sys, time
from pathlib import Path
from datetime import datetime, timezone

for _p in ["playwright", "nest_asyncio", "faker"]:
    try: __import__(_p)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", _p],
                       capture_output=True, timeout=90)

from playwright.async_api import async_playwright
from faker import Faker
import nest_asyncio; nest_asyncio.apply()

VPS_API       = os.environ.get("VPS_API",   "http://45.205.27.69:8084")
VPS_PROXY     = os.environ.get("VPS_PROXY", "socks5://45.205.27.69:19080")
SANDBOX_LABEL = os.environ.get("SANDBOX_LABEL", "unknown")

WORK_DIR  = Path("/home/user/work");   WORK_DIR.mkdir(parents=True, exist_ok=True)
ACC_DIR   = WORK_DIR / "accounts";    ACC_DIR.mkdir(parents=True, exist_ok=True)
SHOTS_DIR = WORK_DIR / "shots";       SHOTS_DIR.mkdir(parents=True, exist_ok=True)

DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

EN_MONTHS = ["","January","February","March","April","May","June",
             "July","August","September","October","November","December"]

fake = Faker("en_US")
RESULT = {"success":False,"email":None,"password":None,"username":None,
          "error":None,"elapsed":None,"sandbox":SANDBOX_LABEL}


def gen_account():
    first = fake.first_name(); last = fake.last_name()
    num   = random.randint(10, 9999)
    patterns = [
        f"{first.lower()}{last.lower()}{num}",
        f"{first.lower()}.{last.lower()}{random.randint(10,99)}",
        f"{first.lower()}{last[0].lower()}{num}",
        f"{first[0].lower()}{last.lower()}{num}",
    ]
    username = random.choice(patterns)
    password = (random.choice(string.ascii_uppercase)
                + "".join(random.choices(string.ascii_lowercase, k=6))
                + "".join(random.choices(string.digits, k=3))
                + random.choice("!@#$%"))
    bday = {"day":str(random.randint(1,28)),"month":str(random.randint(1,12)),
            "year":str(random.randint(1985,2001))}
    return {"firstName":first,"lastName":last,"username":username,"password":password,
            "email":f"{username}@outlook.com","birthday":bday}


async def shot(page, name):
    try: await page.screenshot(path=str(SHOTS_DIR/f"{name}.png"), full_page=False)
    except: pass


async def click_submit(page):
    """单次点击 Next/Submit — 优先 type=submit，否则 role=button/Next，绝不双发"""
    for sel in ['button[type="submit"]']:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await el.click(); return True
        except: pass
    for txt in ["Next", "下一步"]:
        try:
            btn = page.get_by_role("button", name=re.compile(txt, re.I)).first
            if await btn.is_visible(timeout=1000):
                await btn.click(); return True
        except: pass
    return False


def _report_to_vps(acc):
    import urllib.request
    summary_file = WORK_DIR / "all_accounts.jsonl"
    with open(summary_file, "a") as f:
        f.write(json.dumps({"email":acc["email"],"password":acc["password"],
            "username":acc["username"],"source":"obvious-sandbox-factory-v4",
            "savedAt":datetime.now(timezone.utc).isoformat()},ensure_ascii=False)+"\n")
    try:
        payload = json.dumps({"email":acc["email"],"password":acc["password"],
            "username":acc["username"],"platform":"outlook","sandbox":SANDBOX_LABEL}).encode()
        req = urllib.request.Request(f"{VPS_API}/emails/push", data=payload,
            headers={"Content-Type":"application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            print(f"[factory:{SANDBOX_LABEL}] VPS push: {r.status}", flush=True)
    except Exception as e:
        print(f"[factory:{SANDBOX_LABEL}] VPS push fail: {e}", flush=True)


async def register_one():
    t0  = time.time()
    acc = gen_account()
    RESULT.update({"email":acc["email"],"password":acc["password"],"username":acc["username"]})
    print(f"[factory:{SANDBOX_LABEL}] → {acc['email']} bday={acc['birthday']}", flush=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, proxy={"server":VPS_PROXY},
            args=["--no-sandbox","--disable-setuid-sandbox",
                  "--disable-blink-features=AutomationControlled","--disable-dev-shm-usage",
                  "--disable-features=IsolateOrigins,site-per-process"])
        ctx = await browser.new_context(user_agent=DESKTOP_UA,
            viewport={"width":1280,"height":800},locale="en-US",timezone_id="America/New_York")
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = await ctx.new_page()

        try:
            # ── 1. 注册首页 ──
            await page.goto("https://outlook.live.com/mail/0/?prompt=create_account",
                            wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(4)
            await shot(page, f"{acc['username']}_01")

            for txt in ["Agree and continue","同意并继续","Continue"]:
                try:
                    btn = page.get_by_text(re.compile(txt,re.I)).first
                    if await btn.is_visible(timeout=2000): await btn.click(); await asyncio.sleep(2); break
                except: pass

            # ── 2. 邮箱 (EASI=email / 旧流程=MemberName) ──
            for sel in ['input[name="MemberName"]','input[name="email"]',
                        '[aria-label="New email address"]','input[type="email"]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=3000):
                        await el.fill(acc["username"]); await asyncio.sleep(1); break
                except: pass
            await click_submit(page); await asyncio.sleep(4)
            await shot(page, f"{acc['username']}_02")
            print(f"[step2] title={await page.title()}", flush=True)

            pt = await page.inner_text("body")
            if re.search(r"already taken|unavailable|not available|已被占用|someone else", pt, re.I):
                RESULT["error"] = "username_taken"; return False

            # ── 3. 密码 ──
            for sel in ['input[type="password"]','[name="Password"]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=4000):
                        await el.fill(acc["password"]); await asyncio.sleep(1); break
                except: pass
            await click_submit(page); await asyncio.sleep(5)
            await shot(page, f"{acc['username']}_03")
            print(f"[step3] title={await page.title()}", flush=True)

            # ── 4. 名字 (可能在密码后，也可能在生日后) ──
            async def fill_name_step():
                pt_now = await page.inner_text("body")
                if not re.search(r"Add your name|First name|your name", pt_now, re.I):
                    return False
                for sel,val in [('[name="FirstName"]',acc["firstName"]),
                                ('[name="LastName"]', acc["lastName"])]:
                    try:
                        el = page.locator(sel).first
                        if await el.is_visible(timeout=2000): await el.fill(val)
                    except: pass
                # 兜底: 直接填 text inputs
                inps = page.locator('input[type="text"]')
                cnt  = await inps.count()
                if cnt >= 2:
                    await inps.nth(0).fill(acc["firstName"])
                    await inps.nth(1).fill(acc["lastName"])
                elif cnt == 1:
                    await inps.nth(0).fill(acc["firstName"])
                await click_submit(page); await asyncio.sleep(4)
                print(f"[name-step] filled and submitted", flush=True)
                return True

            await fill_name_step()
            await shot(page, f"{acc['username']}_04")
            print(f"[step4] title={await page.title()}", flush=True)

            # ── 5. 生日 (FluentUI: #BirthMonthDropdown / #BirthDayDropdown) ──
            await asyncio.sleep(2)
            bd = acc["birthday"]
            month_name = EN_MONTHS[int(bd["month"])]
            print(f"[bday] month={month_name} day={bd['day']} year={bd['year']}", flush=True)

            # 诊断
            selects = await page.evaluate(
                "() => Array.from(document.querySelectorAll('select')).map(s=>({name:s.name,id:s.id}))")
            print(f"[bday] native selects={selects}", flush=True)

            month_ok = day_ok = False

            # 策略1: 原生 <select> (旧流程)
            try:
                await page.locator('[name="BirthMonth"]').select_option(value=bd["month"],timeout=2000)
                month_ok = True; print("[bday] month select_option OK", flush=True)
            except: pass
            try:
                await page.locator('[name="BirthDay"]').select_option(value=bd["day"],timeout=2000)
                day_ok = True; print("[bday] day select_option OK", flush=True)
            except: pass

            # 策略2: FluentUI JS click (EASI 新流程)
            if not month_ok:
                try:
                    await page.evaluate("document.querySelector('#BirthMonthDropdown').click()")
                    await asyncio.sleep(1)
                    opt = page.get_by_role("option", name=re.compile(f"^{month_name}$", re.I))
                    if await opt.first.is_visible(timeout=2000):
                        await opt.first.click(); month_ok = True
                        print(f"[bday] month JS click OK: {month_name}", flush=True)
                except Exception as e:
                    print(f"[bday] month JS click fail: {e}", flush=True)

            if not day_ok:
                try:
                    await page.evaluate("document.querySelector('#BirthDayDropdown').click()")
                    await asyncio.sleep(1)
                    opt = page.get_by_role("option", name=re.compile(f"^{bd['day']}$"))
                    if await opt.first.is_visible(timeout=2000):
                        await opt.first.click(); day_ok = True
                        print(f"[bday] day JS click OK: {bd['day']}", flush=True)
                except Exception as e:
                    print(f"[bday] day JS click fail: {e}", flush=True)

            # Year
            for sel in ['input[name="BirthYear"]','[aria-label="Birth year"]','[aria-label*="year" i]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.fill(bd["year"]); print(f"[bday] year filled OK", flush=True); break
                except: pass

            await asyncio.sleep(1)
            await shot(page, f"{acc['username']}_05")
            await click_submit(page); await asyncio.sleep(5)
            await shot(page, f"{acc['username']}_06")
            print(f"[step5] title={await page.title()}", flush=True)

            pt2 = await page.inner_text("body")
            if re.search(r"Enter your birthdate|enter.*birth|出生日期", pt2, re.I):
                RESULT["error"] = f"birthday_fail month_ok={month_ok} day_ok={day_ok}"; return False

            # ── 6. 名字 (EASI: 出现在生日后) ──
            await asyncio.sleep(1)
            await fill_name_step()
            await shot(page, f"{acc['username']}_07")
            print(f"[step6] title={await page.title()}", flush=True)

            # ── 7. CAPTCHA (Arkose/FunCaptcha Lets prove youre human) ──
            await asyncio.sleep(3)
            for attempt in range(4):
                pt  = await page.inner_text("body")
                ttl = await page.title()
                url = page.url
                print(f"[captcha-check] {attempt} title={ttl[:50]} url={url[:60]}", flush=True)
                # 检测: captcha / challenge / prove / human
                is_captcha = any(w in pt.lower() for w in ["captcha","challenge","prove","robot","human"])
                             or any(w in ttl.lower() for w in ["prove","captcha","challenge"])
                if is_captcha:
                    print(f"[factory:{SANDBOX_LABEL}] CAPTCHA detected: {ttl}", flush=True)
                    # 1. 主页面 accessibility 按钮
                    for a11y in ['[aria-label="Accessible challenge"]',
                                 '[aria-label="Audio challenge"]',
                                 '[aria-label="Accessibility challenge"]',
                                 'a:has-text("Audio challenge")',
                                 'a:has-text("Accessibility")']:
                        try:
                            el = page.locator(a11y).first
                            if await el.is_visible(timeout=1500): await el.click(); await asyncio.sleep(3); break
                        except: pass
                    # 2. iframe 内 accessibility 按钮 (Arkose)
                    for fr in page.frames:
                        try:
                            furl = fr.url
                            if not furl or furl == "about:blank": continue
                            for a11y in ['[aria-label="Accessibility challenge"]',
                                         '[aria-label="Audio"]',
                                         'button:has-text("Accessibility")',
                                         'button:has-text("Audio")']:
                                try:
                                    el = fr.locator(a11y).first
                                    if await el.is_visible(timeout=1000):
                                        await el.click(); await asyncio.sleep(3); break
                                except: pass
                        except: pass
                    # 3. frame_locator fallback
                    for frame_sel in ['iframe[title*="challenge" i]',
                                      'iframe[src*="arkose"]',
                                      'iframe[src*="funcaptcha"]',
                                      'iframe[src*="enforcement"]']:
                        try:
                            frame = page.frame_locator(frame_sel)
                            for a11y in ['[aria-label="Accessibility challenge"]',
                                         '[aria-label="Audio challenge"]',
                                         'button:has-text("Audio")']:
                                try:
                                    el = frame.locator(a11y).first
                                    if await el.is_visible(timeout=1000): await el.click(); await asyncio.sleep(3); break
                                except: pass
                        except: pass
                    await asyncio.sleep(5)
                else:
                    break
            await shot(page, f"{acc['username']}_08")

            # ── 8. 等待成功 ──
            SUCCESS_COOKIES = {"RPSAuth","MSPAuth","ESTSAUTH","ESTSAUTHPERSISTENT","OIDCAuth"}
            SUCCESS_URLS    = ["outlook.live.com/mail/0/","office.com","/SkypeProvisioning","welcome.microsoft"]
            success = False
            for tick in range(25):
                await asyncio.sleep(2)
                cur_url = page.url
                print(f"[wait] tick={tick} url={cur_url[:80]}", flush=True)
                cookies = await ctx.cookies()
                auth_ck = [c for c in cookies if c.get("name","") in SUCCESS_COOKIES]
                if auth_ck:
                    print(f"[factory:{SANDBOX_LABEL}] ✅ cookies={[c['name'] for c in auth_ck]}", flush=True)
                    success = True; break
                if any(x in cur_url for x in SUCCESS_URLS):
                    print(f"[factory:{SANDBOX_LABEL}] ✅ URL={cur_url[:60]}", flush=True)
                    success = True; break
                pt = await page.inner_text("body")
                if re.search(r"something went wrong|error|错误", pt, re.I) and \
                   not re.search(r"inbox|mail|outlook\.com", cur_url, re.I):
                    print(f"[factory:{SANDBOX_LABEL}] error page, stopping", flush=True)
                    break

            await shot(page, f"{acc['username']}_09_final")

            if success:
                state = await ctx.storage_state()
                (ACC_DIR/f"{acc['username']}.json").write_text(
                    json.dumps({**acc,"status":"success","storage_state":state},indent=2))
                _report_to_vps(acc)
                RESULT.update({"success":True,"elapsed":round(time.time()-t0,1)})
                print(f"[factory:{SANDBOX_LABEL}] ✅ SUCCESS {acc['email']} t={RESULT['elapsed']}s", flush=True)
                return True
            else:
                RESULT["error"] = f"auth_timeout url={cur_url[:80]}"
                print(f"[factory:{SANDBOX_LABEL}] ❌ {acc['email']}", flush=True)
                return False

        except Exception as e:
            import traceback
            RESULT["error"] = str(e)
            print(f"[factory:{SANDBOX_LABEL}] EXCEPTION: {e}", flush=True)
            print(traceback.format_exc()[-400:], flush=True)
            return False
        finally:
            await browser.close()


async def _main():
    for attempt in range(3):
        ok = await register_one()
        if ok: break
        if RESULT.get("error") == "username_taken":
            print(f"[factory:{SANDBOX_LABEL}] retry {attempt+1}/3 (username taken)", flush=True)
            RESULT["error"] = None; continue
        break

asyncio.get_event_loop().run_until_complete(_main())
print("RESULT:" + json.dumps(RESULT))
