#!/usr/bin/env python3
"""
outlook_factory_sandbox.py — obvious.ai 沙盒内 Outlook 批量注册器
运行环境: obvious.ai sandbox (Python 3.13, Playwright 1.59, Chromium 147)
持久化: /home/user/work/accounts/ (沙盒内)
上报: POST http://45.205.27.69:8081/api/accounts (VPS入库)

特性:
  - 移动端 UA (iPhone 15 Pro) 降低 CAPTCHA 触发率
  - 可选 SOCKS5 代理 (VPS socat relay 45.205.27.69:19080)
  - 无障碍 CAPTCHA 挑战自动点击
  - 截图全程记录 (shots/)
  - 账号持久化 JSON

用法:
  python3 outlook_factory_sandbox.py           # 直接注册
  python3 outlook_factory_sandbox.py --proxy   # 通过VPS代理注册
  python3 outlook_factory_sandbox.py --count 3 # 注册3个
"""
import asyncio, argparse, json, random, re, string, sys, time, uuid
from pathlib import Path
from datetime import datetime, timezone
from faker import Faker

WORK_DIR   = Path("/home/user/work")
ACC_DIR    = WORK_DIR / "accounts"
SHOTS_DIR  = WORK_DIR / "shots"
VPS_API    = "http://45.205.27.69:8081"
VPS_PROXY  = "socks5://45.205.27.69:19080"

# 移动端 UA (iPhone 15 Pro, iOS 17, Safari)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

fake = Faker("en_US")

def gen_account():
    first = fake.first_name()
    last  = fake.last_name()
    year  = random.randint(1985, 2001)
    num   = random.randint(10, 9999)
    patterns = [
        f"{first.lower()}{last.lower()}{num}",
        f"{first.lower()}.{last.lower()}{random.randint(10,99)}",
        f"{first.lower()}{last[0].lower()}{num}",
        f"{first[0].lower()}{last.lower()}{num}",
        f"{first.lower()}{year}",
    ]
    username = random.choice(patterns)
    password = (
        random.choice(string.ascii_uppercase) +
        ''.join(random.choices(string.ascii_lowercase, k=6)) +
        ''.join(random.choices(string.digits, k=3)) +
        random.choice("!@#$%")
    )
    bday = {
        "day": str(random.randint(1, 28)),
        "month": str(random.randint(1, 12)),
        "year": str(year),
    }
    return {
        "firstName": first, "lastName": last,
        "username": username, "password": password,
        "email": f"{username}@outlook.com", "birthday": bday,
    }

async def shot(page, name: str):
    p = SHOTS_DIR / f"{name}.png"
    try:
        await page.screenshot(path=str(p), full_page=False)
    except Exception:
        pass

async def register_one(use_proxy: bool, headless: bool = True) -> dict | None:
    from playwright.async_api import async_playwright

    acc = gen_account()
    print(f"[factory] registering {acc['email']}")
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
            ],
            proxy=proxy,
        )
        ctx = await browser.new_context(
            user_agent=MOBILE_UA,
            viewport={"width": 390, "height": 844},
            locale="en-US",
            timezone_id="America/New_York",
            is_mobile=True,
            has_touch=True,
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await ctx.new_page()
        result = {"status": "unknown", **acc}

        try:
            # ── 1. 打开注册页 ──
            url = "https://outlook.live.com/mail/0/?prompt=create_account"
            print(f"[factory] goto {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(3)
            await shot(page, f"{acc['username']}_01_start")

            # ── 2. 同意并继续 (中英双语) ──
            for txt in ["Agree and continue", "同意并继续", "Continue", "Next"]:
                try:
                    btn = page.get_by_text(re.compile(txt, re.I)).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click(); await asyncio.sleep(2); break
                except Exception:
                    pass

            # ── 3. 填写邮箱前缀 ──
            for sel in [
                '[aria-label="New email address"]',
                '[aria-label="新建电子邮件"]',
                '[aria-label="New email"]',
                'input[name="MemberName"]',
                'input[type="email"]',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=3000):
                        await el.fill(acc["username"]); await asyncio.sleep(1); break
                except Exception:
                    pass

            # Next 按钮
            for txt in ["Next", "下一步"]:
                try:
                    btn = page.get_by_role("button", name=re.compile(txt, re.I)).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click(); await asyncio.sleep(2); break
                except Exception:
                    pass

            await shot(page, f"{acc['username']}_02_email")

            # ── 4. 用户名已占用检测 ──
            taken_re = re.compile(r"already taken|unavailable|not available|已被占用", re.I)
            page_text = await page.inner_text("body")
            if taken_re.search(page_text):
                print(f"[factory] username taken: {acc['username']}")
                await browser.close()
                return None

            # ── 5. 填写密码 ──
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
                        await btn.click(); await asyncio.sleep(2); break
                except Exception:
                    pass
            await shot(page, f"{acc['username']}_03_password")

            # ── 6. 填写名字 ──
            for fname_sel in ['[name="FirstName"]', '[aria-label*="First"]', 'input[id*="first" i]']:
                try:
                    el = page.locator(fname_sel).first
                    if await el.is_visible(timeout=3000):
                        await el.fill(acc["firstName"]); break
                except Exception:
                    pass
            for lname_sel in ['[name="LastName"]', '[aria-label*="Last"]', 'input[id*="last" i]']:
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
                        await btn.click(); await asyncio.sleep(2); break
                except Exception:
                    pass
            await shot(page, f"{acc['username']}_04_name")

            # ── 7. 生日 ──
            bd = acc["birthday"]
            for month_sel in ['[aria-label*="month" i]', '[data-name="BirthMonth"]',
                               'select[name="BirthMonth"]']:
                try:
                    el = page.locator(month_sel).first
                    if await el.is_visible(timeout=3000):
                        await el.select_option(value=bd["month"]); break
                except Exception:
                    pass
            for day_sel in ['[aria-label*="day" i]', 'select[name="BirthDay"]']:
                try:
                    el = page.locator(day_sel).first
                    if await el.is_visible(timeout=2000):
                        await el.select_option(value=bd["day"]); break
                except Exception:
                    pass
            for year_sel in ['[aria-label*="year" i]', 'input[name="BirthYear"]', '[name="BirthYear"]']:
                try:
                    el = page.locator(year_sel).first
                    if await el.is_visible(timeout=2000):
                        await el.fill(bd["year"]); break
                except Exception:
                    pass
            for txt in ["Next", "下一步"]:
                try:
                    btn = page.get_by_role("button", name=re.compile(txt, re.I)).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click(); await asyncio.sleep(3); break
                except Exception:
                    pass
            await shot(page, f"{acc['username']}_05_birthday")

            # ── 8. CAPTCHA 无障碍挑战 ──
            await asyncio.sleep(3)
            for _ in range(3):
                page_text = await page.inner_text("body")
                if "captcha" in page_text.lower() or "challenge" in page_text.lower():
                    print("[factory] CAPTCHA detected, trying accessibility challenge")
                    # 尝试点击无障碍挑战按钮
                    for a11y_sel in [
                        '[aria-label="Accessible challenge"]',
                        '[aria-label="可访问性挑战"]',
                        '[aria-label="Audio challenge"]',
                        '[aria-label="Accessibility challenge"]',
                    ]:
                        try:
                            el = page.locator(a11y_sel).first
                            if await el.is_visible(timeout=2000):
                                await el.click(); await asyncio.sleep(2); break
                        except Exception:
                            pass
                    # 在 iframe 里找挑战
                    for frame_sel in ['iframe[title*="challenge" i]', 'iframe[title*="验证"]']:
                        try:
                            frame = page.frame_locator(frame_sel)
                            for a11y in ['[aria-label="Accessibility challenge"]',
                                         '[aria-label="Audio challenge"]']:
                                try:
                                    el = frame.locator(a11y).first
                                    if await el.is_visible(timeout=2000):
                                        await el.click(); await asyncio.sleep(2); break
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    await asyncio.sleep(3)
                else:
                    break

            await shot(page, f"{acc['username']}_06_captcha")

            # ── 9. 等待注册完成，检测成功信号 ──
            success = False
            for _ in range(20):
                await asyncio.sleep(2)
                cur_url = page.url
                # 成功信号: outlook.live.com/mail 或 office.com 或 microsoft.com/welcome
                if any(x in cur_url for x in [
                    "outlook.live.com/mail/0/inbox",
                    "outlook.live.com/mail/0/",
                    "office.com",
                    "/SkypeProvisioning",
                    "microsoft.com",
                ]):
                    # 验证 cookie 存在
                    cookies = await ctx.cookies()
                    auth_cookies = [c for c in cookies
                                    if c.get("name", "") in ("RPSAuth","MSPAuth","ESTSAUTH","ESTSAUTHPERSISTENT")]
                    if auth_cookies:
                        success = True
                        print(f"[factory] ✅ registered {acc['email']}")
                        break
                page_text = await page.inner_text("body")
                if "inbox" in page_text.lower() or "outlook" in page_text.lower():
                    success = True; break

            await shot(page, f"{acc['username']}_07_final")

            if success:
                result["status"] = "success"
                result["registeredAt"] = datetime.now(timezone.utc).isoformat()
                # 保存 storage_state
                state = await ctx.storage_state()
                acc_file = ACC_DIR / f"{acc['username']}.json"
                acc_data = {**result, "storage_state": state}
                acc_file.write_text(json.dumps(acc_data, indent=2))
                print(f"[factory] saved to {acc_file}")
                # 上报到 VPS
                _report_to_vps(result)
            else:
                result["status"] = "failed"
                print(f"[factory] ❌ registration failed for {acc['email']}")

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            print(f"[factory] exception: {e}")
        finally:
            await browser.close()

    return result if result.get("status") == "success" else None


def _report_to_vps(acc: dict):
    """上报成功账号到 VPS API"""
    import urllib.request
    try:
        payload = json.dumps({
            "email": acc["email"],
            "password": acc["password"],
            "username": acc["username"],
            "source": "obvious-sandbox-factory",
            "tags": "outlook,sandbox-generated",
        }).encode()
        req = urllib.request.Request(
            f"{VPS_API}/api/accounts",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"[factory] VPS report: {r.status}")
    except Exception as e:
        print(f"[factory] VPS report failed: {e}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--proxy", action="store_true", help="通过VPS SOCKS5代理")
    ap.add_argument("--no-headless", action="store_true")
    args = ap.parse_args()

    success = 0
    for i in range(args.count):
        print(f"\n[factory] === 第 {i+1}/{args.count} 个账号 ===")
        result = await register_one(
            use_proxy=args.proxy,
            headless=not args.no_headless,
        )
        if result:
            success += 1
        await asyncio.sleep(5)

    print(f"\n[factory] 完成: {success}/{args.count} 成功")

if __name__ == "__main__":
    asyncio.run(main())
