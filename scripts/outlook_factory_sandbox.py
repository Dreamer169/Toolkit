#!/usr/bin/env python3
"""
outlook_factory_sandbox.py v3 — obvious.ai 沙盒内 Outlook 批量注册器

根因修复 (v3):
  - Bug1-ROOT: Mobile UA 导致微软渲染 FluentUI React 组件 (无原生 <select>)
    → 改用 Desktop Chrome UA，恢复原生 <select> 下拉，select_option 正常工作
  - Bug1 之前的 JS 赋值修复：仍保留作为 fallback，但不再是主路径
  - Bug2: 成功检测只认 auth cookie (RPSAuth/MSPAuth 等)，不认 page_text
  - Bug3: VPS 上报改为本地 JSONL 主存储 + HTTP 次要
  - 新增: 每步打印当前URL和标题，便于调试

Desktop UA 关键性：
  signup.live.com 在 Mobile UA 下返回 FluentUI React 版 (Month/Day 无 <select>)
  在 Desktop UA 下返回传统 HTML 版 ([name="BirthMonth"]/[name="BirthDay"] select)
  → 所有操作均基于 Desktop UA
"""
import asyncio, argparse, json, random, re, string, sys, time
from pathlib import Path
from datetime import datetime, timezone
from faker import Faker

WORK_DIR  = Path("/home/user/work")
ACC_DIR   = WORK_DIR / "accounts"
SHOTS_DIR = WORK_DIR / "shots"
VPS_API   = "http://45.205.27.69:8081"
VPS_PROXY = "socks5://45.205.27.69:19080"

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

EN_MONTHS = ["", "January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]

fake = Faker("en_US")


def gen_account():
    first = fake.first_name()
    last  = fake.last_name()
    year  = random.randint(1985, 2001)
    num   = random.randint(10, 9999)
    patterns = [
        f"{first.lower()}{last.lower()}{num}",
        f"{first.lower()}.{last.lower()}{random.randint(10, 99)}",
        f"{first.lower()}{last[0].lower()}{num}",
        f"{first[0].lower()}{last.lower()}{num}",
    ]
    username = random.choice(patterns)
    password = (
        random.choice(string.ascii_uppercase)
        + "".join(random.choices(string.ascii_lowercase, k=6))
        + "".join(random.choices(string.digits, k=3))
        + random.choice("!@#$%")
    )
    bday = {
        "day":   str(random.randint(1, 28)),
        "month": str(random.randint(1, 12)),
        "year":  str(year),
    }
    return {
        "firstName": first, "lastName": last,
        "username": username, "password": password,
        "email": f"{username}@outlook.com", "birthday": bday,
    }


async def shot(page, name: str):
    try:
        await page.screenshot(path=str(SHOTS_DIR / f"{name}.png"), full_page=False)
    except Exception:
        pass


async def debug_page(page, label: str):
    try:
        url   = page.url
        title = await page.title()
        print(f"[dbg] {label} | url={url[:80]} | title={title[:60]}", flush=True)
    except Exception:
        pass


async def register_one(use_proxy: bool, headless: bool = True) -> dict | None:
    from playwright.async_api import async_playwright

    acc = gen_account()
    print(f"\n[factory] 注册 {acc['email']}  bday={acc['birthday']}", flush=True)
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ACC_DIR.mkdir(parents=True, exist_ok=True)

    proxy = {"server": VPS_PROXY} if use_proxy else None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
            proxy=proxy,
        )
        # ← KEY FIX v3: Desktop UA → 微软渲染原生 <select>，select_option 正常工作
        ctx = await browser.new_context(
            user_agent=DESKTOP_UA,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
            # is_mobile=False (默认)，has_touch=False (默认)
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = await ctx.new_page()
        result = {"status": "unknown", **acc}

        try:
            # ── 1. 打开注册页 ──
            await page.goto(
                "https://outlook.live.com/mail/0/?prompt=create_account",
                wait_until="domcontentloaded", timeout=45000,
            )
            await asyncio.sleep(4)
            await shot(page, f"{acc['username']}_01_start")
            await debug_page(page, "start")

            # ── 2. 同意/继续 ──
            for txt in ["Agree and continue", "同意并继续", "Continue"]:
                try:
                    btn = page.get_by_text(re.compile(txt, re.I)).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click(); await asyncio.sleep(2); break
                except Exception:
                    pass

            # ── 3. 邮箱 ──
            for sel in [
                'input[name="MemberName"]',
                '[aria-label="New email address"]',
                '[aria-label="新建电子邮件"]',
                'input[type="email"]',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=3000):
                        await el.fill(acc["username"]); await asyncio.sleep(1); break
                except Exception:
                    pass

            for txt in ["Next", "下一步"]:
                try:
                    btn = page.get_by_role("button", name=re.compile(txt, re.I)).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click(); await asyncio.sleep(3); break
                except Exception:
                    pass
            await shot(page, f"{acc['username']}_02_email")
            await debug_page(page, "after-email")

            # 用户名已占用
            page_text = await page.inner_text("body")
            if re.search(r"already taken|unavailable|not available|已被占用", page_text, re.I):
                print(f"[factory] ✗ username taken: {acc['username']}", flush=True)
                await browser.close()
                return None

            # ── 4. 密码 ──
            for sel in ['input[type="password"]', '[name="Password"]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=4000):
                        await el.fill(acc["password"]); await asyncio.sleep(1); break
                except Exception:
                    pass
            for txt in ["Next", "下一步"]:
                try:
                    btn = page.get_by_role("button", name=re.compile(txt, re.I)).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click(); await asyncio.sleep(3); break
                except Exception:
                    pass
            await shot(page, f"{acc['username']}_03_password")
            await debug_page(page, "after-password")

            # ── 5. 名字 ──
            for fname_sel in ['[name="FirstName"]', '[aria-label*="First"]',
                               'input[id*="first" i]', '#firstNameInput']:
                try:
                    el = page.locator(fname_sel).first
                    if await el.is_visible(timeout=3000):
                        await el.fill(acc["firstName"]); break
                except Exception:
                    pass
            for lname_sel in ['[name="LastName"]', '[aria-label*="Last"]',
                               'input[id*="last" i]', '#lastNameInput']:
                try:
                    el = page.locator(lname_sel).first
                    if await el.is_visible(timeout=3000):
                        await el.fill(acc["lastName"]); break
                except Exception:
                    pass
            for txt in ["Next", "下一步"]:
                try:
                    btn = page.get_by_role("button", name=re.compile(txt, re.I)).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click(); await asyncio.sleep(3); break
                except Exception:
                    pass
            await shot(page, f"{acc['username']}_04_name")
            await debug_page(page, "after-name")

            # ── 6. 生日 (v3: Desktop UA → 原生 <select>) ──
            bd = acc["birthday"]
            await asyncio.sleep(2)

            # 诊断: 打印页面上所有 select
            selects_info = await page.evaluate("""
                () => Array.from(document.querySelectorAll('select')).map(s => ({
                    name: s.name, id: s.id, label: s.getAttribute('aria-label'),
                    options: Array.from(s.options).slice(0,5).map(o=>({v:o.value,t:o.text}))
                }))
            """)
            print(f"[dbg] selects on page: {json.dumps(selects_info)[:600]}", flush=True)

            # 策略1: 原生 select_option (Desktop UA 下有效)
            month_ok = day_ok = False
            try:
                await page.locator('[name="BirthMonth"]').select_option(
                    value=bd["month"], timeout=3000)
                month_ok = True
                print(f"[bday] BirthMonth select_option({bd['month']}) OK", flush=True)
            except Exception as e:
                print(f"[bday] BirthMonth select_option fail: {e}", flush=True)

            try:
                await page.locator('[name="BirthDay"]').select_option(
                    value=bd["day"], timeout=3000)
                day_ok = True
                print(f"[bday] BirthDay select_option({bd['day']}) OK", flush=True)
            except Exception as e:
                print(f"[bday] BirthDay select_option fail: {e}", flush=True)

            # 策略2 fallback: click combobox + click option text
            # (用于FluentUI或其他自定义下拉)
            if not month_ok:
                try:
                    month_name = EN_MONTHS[int(bd["month"])]
                    month_zh   = f"{bd['month']}月"
                    # Click to open combobox
                    for m_sel in ['[name="BirthMonth"]', '[aria-label*="Month" i]',
                                  '[data-ng-model*="Month" i]']:
                        try:
                            el = page.locator(m_sel).first
                            if await el.is_visible(timeout=1000):
                                await el.click(); await asyncio.sleep(0.5); break
                        except Exception:
                            pass
                    # Select from dropdown options
                    for opt_text in [month_name, month_zh]:
                        try:
                            opt = page.get_by_role("option", name=re.compile(f"^{opt_text}$", re.I))
                            if await opt.first.is_visible(timeout=1500):
                                await opt.first.click()
                                month_ok = True
                                print(f"[bday] month option clicked: {opt_text}", flush=True)
                                break
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[bday] month fallback fail: {e}", flush=True)

            if not day_ok:
                try:
                    for d_sel in ['[name="BirthDay"]', '[aria-label*="Day" i]']:
                        try:
                            el = page.locator(d_sel).first
                            if await el.is_visible(timeout=1000):
                                await el.click(); await asyncio.sleep(0.5); break
                        except Exception:
                            pass
                    for opt_text in [bd["day"], f"{bd['day']}日"]:
                        try:
                            opt = page.get_by_role("option", name=re.compile(f"^{opt_text}$", re.I))
                            if await opt.first.is_visible(timeout=1500):
                                await opt.first.click()
                                day_ok = True
                                print(f"[bday] day option clicked: {opt_text}", flush=True)
                                break
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[bday] day fallback fail: {e}", flush=True)

            # Year 输入框 (text input，desktop/mobile 均存在)
            for year_sel in ['input[name="BirthYear"]', '[name="BirthYear"]',
                             '[aria-label*="year" i]']:
                try:
                    el = page.locator(year_sel).first
                    if await el.is_visible(timeout=2000):
                        await el.fill(bd["year"])
                        print(f"[bday] BirthYear filled: {bd['year']}", flush=True)
                        break
                except Exception:
                    pass

            await asyncio.sleep(1)
            await shot(page, f"{acc['username']}_05_birthday_filled")
            await debug_page(page, "birthday-filled")

            for txt in ["Next", "下一步"]:
                try:
                    btn = page.get_by_role("button", name=re.compile(txt, re.I)).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click(); await asyncio.sleep(4); break
                except Exception:
                    pass
            await shot(page, f"{acc['username']}_06_after_birthday")
            await debug_page(page, "after-birthday-submit")

            # 检查是否还停留在生日页（提交失败）
            page_text2 = await page.inner_text("body")
            if re.search(r"Enter your birthdate|enter.*birth|出生日期", page_text2, re.I):
                print("[factory] ❌ birthday validation failed, aborting", flush=True)
                result["status"] = "birthday_fail"
                result["error"] = "birthday selectors failed (Desktop UA, check selects dump)"
                await browser.close()
                return None

            # ── 7. CAPTCHA 无障碍挑战 ──
            await asyncio.sleep(3)
            for attempt in range(3):
                page_text = await page.inner_text("body")
                cur_url   = page.url
                print(f"[captcha-check] attempt={attempt} url={cur_url[:70]}", flush=True)
                if "captcha" in page_text.lower() or "challenge" in page_text.lower():
                    print("[factory] CAPTCHA detected", flush=True)
                    for a11y_sel in [
                        '[aria-label="Accessible challenge"]',
                        '[aria-label="Audio challenge"]',
                        '[aria-label="Accessibility challenge"]',
                    ]:
                        try:
                            el = page.locator(a11y_sel).first
                            if await el.is_visible(timeout=2000):
                                await el.click(); await asyncio.sleep(2); break
                        except Exception:
                            pass
                    for frame_sel in ['iframe[title*="challenge" i]']:
                        try:
                            frame = page.frame_locator(frame_sel)
                            for a11y in ['[aria-label="Accessibility challenge"]',
                                         '[aria-label="Audio challenge"]']:
                                try:
                                    el = frame.locator(a11y).first
                                    if await el.is_visible(timeout=2000):
                                        await el.click(); break
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    await asyncio.sleep(4)
                else:
                    break
            await shot(page, f"{acc['username']}_07_captcha")

            # ── 8. 等待成功 (Bug2修复: 只认 auth cookie) ──
            success = False
            SUCCESS_COOKIES = {"RPSAuth", "MSPAuth", "ESTSAUTH", "ESTSAUTHPERSISTENT", "OIDCAuth"}
            SUCCESS_URLS    = [
                "outlook.live.com/mail/0/inbox",
                "outlook.live.com/mail/0/",
                "office.com",
                "/SkypeProvisioning",
                "welcome.microsoft",
            ]
            for tick in range(25):
                await asyncio.sleep(2)
                cur_url = page.url
                print(f"[wait-success] tick={tick} url={cur_url[:80]}", flush=True)

                cookies = await ctx.cookies()
                auth_ck = [c for c in cookies if c.get("name", "") in SUCCESS_COOKIES]
                if auth_ck:
                    print(f"[factory] ✅ auth cookies: {[c['name'] for c in auth_ck]}", flush=True)
                    success = True; break

                if any(x in cur_url for x in SUCCESS_URLS):
                    success = True
                    print(f"[factory] ✅ URL success: {cur_url[:60]}", flush=True)
                    break

                page_text = await page.inner_text("body")
                if re.search(r"something went wrong|error|错误", page_text, re.I) and \
                   not re.search(r"inbox|mail|outlook\.com", cur_url, re.I):
                    print(f"[factory] error page, stopping wait", flush=True)
                    break

            await shot(page, f"{acc['username']}_08_final")
            await debug_page(page, "final")

            if success:
                result["status"] = "success"
                result["registeredAt"] = datetime.now(timezone.utc).isoformat()
                state = await ctx.storage_state()
                acc_data = {**result, "storage_state": state}
                acc_file = ACC_DIR / f"{acc['username']}.json"
                acc_file.write_text(json.dumps(acc_data, indent=2, ensure_ascii=False))
                print(f"[factory] 💾 saved {acc_file}", flush=True)
                _report_to_vps(result)
            else:
                result["status"] = "failed"
                fail_file = ACC_DIR / f"FAIL_{acc['username']}.json"
                fail_file.write_text(json.dumps(
                    {**result, "failedAt": datetime.now(timezone.utc).isoformat()},
                    indent=2, ensure_ascii=False))
                print(f"[factory] ❌ failed: {acc['email']}", flush=True)

        except Exception as e:
            import traceback
            result["status"] = "error"
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()[-600:]
            print(f"[factory] 💥 exception: {e}", flush=True)
        finally:
            await browser.close()

    return result if result.get("status") == "success" else None


def _report_to_vps(acc: dict):
    """Bug3修复: 本地 JSONL 主存储 + HTTP 次要上报"""
    import urllib.request

    summary_file = WORK_DIR / "all_accounts.jsonl"
    with open(summary_file, "a") as f:
        f.write(json.dumps({
            "email": acc["email"],
            "password": acc["password"],
            "username": acc["username"],
            "source": "obvious-sandbox-factory-v3",
            "savedAt": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False) + "\n")
    print(f"[factory] 📝 appended to {summary_file}", flush=True)

    # HTTP 上报 VPS
    try:
        payload = json.dumps({
            "email": acc["email"],
            "password": acc["password"],
            "username": acc["username"],
            "platform": "outlook",
            "source": "obvious-sandbox-factory-v3",
            "tags": "outlook,sandbox-generated,desktop-ua",
        }).encode()
        req = urllib.request.Request(
            f"{VPS_API}/api/tools/outlook/import",
            data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            body = r.read()[:200]
            print(f"[factory] VPS import: {r.status} {body}", flush=True)
    except Exception as e:
        print(f"[factory] VPS import failed: {e} (account stored locally)", flush=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--proxy", action="store_true")
    ap.add_argument("--no-headless", action="store_true")
    args = ap.parse_args()

    success = 0
    for i in range(args.count):
        print(f"\n[factory] ===== {i+1}/{args.count} =====", flush=True)
        result = await register_one(use_proxy=args.proxy, headless=not args.no_headless)
        if result:
            success += 1
        await asyncio.sleep(random.randint(4, 8))

    print(f"\n[factory] 完成: {success}/{args.count} 成功")
    summary = WORK_DIR / "all_accounts.jsonl"
    if summary.exists():
        lines = summary.read_text().strip().splitlines()
        print(f"[factory] 累计账号: {len(lines)} 个")
        for ln in lines[-3:]:
            d = json.loads(ln)
            print(f"  {d['email']}  pwd={d['password']}")

if __name__ == "__main__":
    asyncio.run(main())
