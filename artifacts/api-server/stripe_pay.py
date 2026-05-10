"""
stripe_pay.py — Stripe Checkout 自动支付模块
流程: EFunCard 兑换虚拟信用卡 → 填写 Stripe 表单 → 处理 hCaptcha + 3DS

外部依赖:
  - EFUNCARD_TOKEN: 硬编码 (可通过环境变量覆盖)
  - YESCAPTCHA_API_KEY: 环境变量, 缺失则跳过 hCaptcha 自动求解
"""
import asyncio
import json
import os
import random
import time
import urllib.request
import urllib.error
from datetime import datetime
from playwright.async_api import async_playwright

EFUNCARD_API   = "https://card.efuncard.com/api/external"
EFUNCARD_TOKEN = os.environ.get(
    "EFUNCARD_TOKEN",
    "b352d13f20462ed46cff0aa417065496bd811eb8396b2e2fee11aeacb796fc00",
)


# ── 指纹工具 (自包含, 无需外部依赖) ──────────────────────────────────────────
import secrets as _secrets, hashlib as _hashlib

_CHROME_VERSIONS = ["131.0.0.0", "130.0.0.0", "129.0.0.0", "128.0.0.0"]
_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 800},
]
_TIMEZONES = ["America/New_York", "America/Chicago", "America/Los_Angeles",
              "America/Denver", "Europe/London", "Europe/Berlin"]


def _random_fingerprint_config():
    cv = random.choice(_CHROME_VERSIONS)
    vp = random.choice(_VIEWPORTS)
    tz = random.choice(_TIMEZONES)
    ua = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          f"(KHTML, like Gecko) Chrome/{cv} Safari/537.36")
    return {
        "user_agent": ua,
        "viewport":  {"width": vp["width"], "height": vp["height"]},
        "screen":    {"width": vp["width"], "height": vp["height"]},
        "locale": "en-US",
        "timezone": tz,
        "pixel_ratio": random.choice([1.0, 1.5, 2.0]),
    }


# ── EFunCard API ──────────────────────────────────────────────────────────────
def _efun_request(method, path, body=None, log=print):
    url = f"{EFUNCARD_API}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bearer {EFUNCARD_TOKEN}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        log(f"EFunCard HTTP {e.code}: {e.read()[:200].decode(errors='replace')}", "warn")
        return None
    except Exception as exc:
        log(f"EFunCard 请求异常: {exc}", "warn")
        return None


def efun_redeem(code, log=print):
    data = _efun_request("POST", "/redeem", {"code": code}, log)
    if data and data.get("success"):
        card = data["data"]
        log(f"卡片兑换成功: *{card['lastFour']} ({card['status']})", "ok")
        return card
    log(f"兑换响应: {data}", "warn")
    return None


def efun_query(code, log=print):
    data = _efun_request("GET", f"/cards/query/{code}", log=log)
    if data and data.get("success"):
        return data["data"]
    return None


def efun_3ds_verify(code, minutes=5, log=print):
    data = _efun_request("POST", "/3ds/verify", {"code": code, "minutes": minutes}, log)
    if data and data.get("success"):
        verifications = data["data"].get("verifications", [])
        if verifications:
            latest = verifications[0]
            log(f"3DS 验证码: {latest['otp']}", "ok")
            return latest
        log("暂无 3DS 验证码", "info")
        return None
    log(f"3DS 查询失败: {data}", "error")
    return None


# ── Stripe 自动填表 ───────────────────────────────────────────────────────────
async def fill_stripe_checkout(payment_url, card_info, cdk_code, log=print, headless=True):
    """自动填写 Stripe Checkout 表单并提交。"""
    card_number   = card_info["cardNumber"]
    cvv           = card_info["cvv"]
    expiry_month  = str(card_info["expiryMonth"]).zfill(2)
    expiry_year   = str(card_info["expiryYear"])[-2:]
    name_on_card  = card_info.get("nameOnCard", "Amy Allen")
    billing_addr  = card_info.get("billingAddress", "") or card_info.get("nodeInstructions", "")
    addr_parts    = [p.strip() for p in billing_addr.split(",")]
    address_line1 = addr_parts[0] if len(addr_parts) > 0 else ""
    city          = addr_parts[1] if len(addr_parts) > 1 else ""
    state_field   = addr_parts[2] if len(addr_parts) > 2 else ""
    postal_code   = addr_parts[3] if len(addr_parts) > 3 else ""

    log(f"卡号: *{card_number[-4:]}, 到期: {expiry_month}/{expiry_year}")
    log(f"地址: {address_line1}, {city}, {state_field} {postal_code}")

    fp = _random_fingerprint_config()

    browser = None
    try:
        async with async_playwright() as p:
            try:
                from playwright_stealth import Stealth
                _has_stealth = True
            except ImportError:
                _has_stealth = False

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-first-run",
                f"--window-size={fp['screen']['width']},{fp['screen']['height']}",
            ]
            if headless:
                launch_args += ["--no-sandbox", "--disable-gpu",
                                "--disable-setuid-sandbox", "--disable-dev-shm-usage"]

            browser = await p.chromium.launch(headless=headless, args=launch_args)
            context = await browser.new_context(
                viewport=fp["viewport"], screen=fp["screen"],
                locale=fp["locale"], timezone_id=fp["timezone"],
                user_agent=fp["user_agent"], color_scheme="light",
                device_scale_factor=fp["pixel_ratio"],
            )
            page = await context.new_page()
            # ── 注入 hCaptcha Accessibility Cookie (免费绕过 hCaptcha) ─────
            try:
                await apply_accessibility_cookie(context)
            except Exception as _e:
                log(f"Accessibility Cookie 注入跳过: {_e}", "warn")
            if _has_stealth:
                await Stealth().apply_stealth_async(page)

            log("加载 Stripe 支付页面...")
            try:
                await page.goto(payment_url, timeout=60000, wait_until="domcontentloaded")
            except Exception:
                log("页面加载失败，重试...", "warn")
                await asyncio.sleep(3)
                await page.goto(payment_url, timeout=60000, wait_until="commit")

            try:
                await page.wait_for_selector("#cardNumber", timeout=30000)
            except Exception:
                log("支付表单未加载，链接可能已失效", "error")
                return {"ok": False, "status": "error", "message": "支付表单未出现"}

            await asyncio.sleep(2)

            async def _move(loc):
                try:
                    box = await loc.bounding_box()
                    if box:
                        await page.mouse.move(
                            box["x"] + box["width"] * random.uniform(0.3, 0.7),
                            box["y"] + box["height"] * random.uniform(0.3, 0.7),
                            steps=random.randint(5, 12),
                        )
                        await asyncio.sleep(random.uniform(0.1, 0.3))
                except Exception:
                    pass

            async def _type(loc, text, delay_range=(40, 110)):
                await _move(loc)
                await loc.click()
                await asyncio.sleep(random.uniform(0.2, 0.5))
                await loc.fill("")
                for i, ch in enumerate(text):
                    await page.keyboard.type(ch, delay=0)
                    d = random.uniform(delay_range[0], delay_range[1]) / 1000
                    if random.random() < 0.06:
                        d += random.uniform(0.15, 0.4)
                    await asyncio.sleep(d)
                await asyncio.sleep(random.uniform(0.4, 0.9))

            # 选国家
            try:
                cs = page.locator("#billingCountry")
                if await cs.count() > 0:
                    await cs.select_option("US")
                    await asyncio.sleep(random.uniform(0.8, 1.5))
            except Exception:
                pass

            log("填写卡号...")
            await _type(page.locator("#cardNumber"), card_number, (45, 100))

            log("填写有效期...")
            await _type(page.locator("#cardExpiry"), f"{expiry_month}{expiry_year}", (50, 120))

            log("填写 CVV...")
            await _type(page.locator("#cardCvc"), cvv, (60, 140))

            log("填写持卡人姓名...")
            await _type(page.locator("#billingName"), name_on_card, (35, 90))

            for sel, val in [
                ("#billingAddressLine1", address_line1),
                ("#billingPostalCode",   postal_code),
                ("#billingLocality",     city),
            ]:
                try:
                    loc = page.locator(sel)
                    if val and await loc.count() > 0 and await loc.first.is_visible():
                        await _type(loc.first, val, (30, 80))
                except Exception:
                    pass

            try:
                ss = page.locator("#billingAdministrativeArea")
                if state_field and await ss.count() > 0 and await ss.first.is_visible():
                    try:
                        await ss.first.select_option(state_field.strip())
                    except Exception:
                        await ss.first.fill(state_field.strip())
                    await asyncio.sleep(0.2)
            except Exception:
                pass

            log("表单填写完成，准备提交...", "ok")
            await asyncio.sleep(random.uniform(1.5, 3.0))

            log("点击 Subscribe...")
            submit_btn = page.locator('button[type="submit"]')
            if await submit_btn.count() > 0:
                await _move(submit_btn.first)
                await asyncio.sleep(random.uniform(0.3, 0.8))
                await submit_btn.first.click()
            else:
                log("未找到提交按钮", "error")
                return {"ok": False, "status": "error", "message": "未找到提交按钮"}

            log("等待支付处理...")
            result = await _wait_for_payment_result(page, cdk_code, log)

            await browser.close()
            browser = None
            return result

    except Exception as e:
        log(f"支付流程异常: {str(e)[:100]}", "error")
        return {"ok": False, "status": "error", "message": str(e)[:100]}
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


async def _wait_for_payment_result(page, cdk_code, log, timeout=120):
    """等待支付结果，处理 hCaptcha 和 3DS。"""
    from captcha_solver import solve_hcaptcha, apply_accessibility_cookie
    start = time.time()

    while time.time() - start < timeout:
        await asyncio.sleep(3)
        try:
            current_url = page.url
        except Exception:
            return {"ok": False, "status": "error", "message": "页面意外关闭"}

        if "success" in current_url or "return_url" in current_url:
            log("支付成功! 页面已跳转", "ok")
            return {"ok": True, "status": "success", "url": current_url}

        try:
            page_text = await page.evaluate("() => document.body.innerText")
        except Exception:
            page_text = ""

        if "thank you" in page_text.lower() or "subscription active" in page_text.lower():
            log("支付成功! 检测到确认信息", "ok")
            return {"ok": True, "status": "success"}

        # hCaptcha
        try:
            hcaptcha_visible = await page.evaluate("""() => {
                const iframe = document.querySelector('iframe[src*="hcaptcha.com/captcha"]');
                if (iframe && iframe.offsetWidth > 50 && iframe.offsetHeight > 50) return true;
                const ch = document.querySelector('[data-hcaptcha-widget-id]');
                return !!(ch && ch.offsetWidth > 50);
            }""")
        except Exception:
            continue

        if hcaptcha_visible:
            log("检测到 hCaptcha，启动求解 (音频法/API)...", "warn")
            try:
                solved = await solve_hcaptcha(page, log_fn=log)
                if not solved:
                    return {"ok": False, "status": "error", "message": "hCaptcha 求解失败"}
            except Exception:
                log("hCaptcha 处理异常", "error")
            continue

        # 3DS
        try:
            is_3ds = await page.evaluate("""() => {
                const iframes = Array.from(document.querySelectorAll('iframe'));
                for (const f of iframes) {
                    if (f.src && (f.src.includes('3ds') || f.src.includes('acs') ||
                        f.src.includes('authenticate') || f.src.includes('challenge')))
                        return f.offsetWidth > 50;
                }
                return false;
            }""")
        except Exception:
            continue

        if is_3ds:
            log("检测到 3DS 验证!", "warn")
            await _handle_3ds(page, cdk_code, log)
            continue

        # 错误检测
        try:
            error_msg = await page.evaluate("""() => {
                const el = document.querySelector('[class*="error"],[class*="Error"],[role="alert"]');
                return el ? el.innerText.trim() : '';
            }""")
            if error_msg and len(error_msg) > 5:
                log(f"支付错误: {error_msg}", "error")
                return {"ok": False, "status": "error", "message": error_msg}
        except Exception:
            pass

    log("支付超时", "error")
    return {"ok": False, "status": "timeout"}


async def _handle_3ds(page, cdk_code, log):
    for _ in range(10):
        await asyncio.sleep(5)
        verification = efun_3ds_verify(cdk_code, minutes=5, log=log)
        if not verification:
            continue
        otp = verification["otp"]
        log(f"获取到 3DS OTP: {otp}", "ok")
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                otp_input = frame.locator(
                    'input[type="text"],input[type="tel"],'
                    'input[name*="otp"],input[name*="code"],'
                    'input[placeholder*="code"]'
                )
                if await otp_input.count() > 0:
                    await otp_input.first.fill(otp)
                    submit = frame.locator(
                        'button[type="submit"],input[type="submit"],'
                        'button:has-text("Submit"),button:has-text("Verify")'
                    )
                    if await submit.count() > 0:
                        await submit.first.click()
                    log("3DS 验证码已提交", "ok")
                    return
            except Exception:
                continue
        try:
            otp_input = page.locator(
                'input[name*="otp"],input[name*="code"],'
                'input[autocomplete*="one-time"]'
            )
            if await otp_input.count() > 0:
                await otp_input.first.fill(otp)
                submit = page.locator('button[type="submit"]')
                if await submit.count() > 0:
                    await submit.first.click()
                log("3DS 验证码已在主页面填入并提交", "ok")
                return
        except Exception:
            pass
        log("未找到 3DS 输入框", "warn")
        return
    log("3DS 验证码获取超时", "error")


# ── 完整自动支付入口 ──────────────────────────────────────────────────────────
async def auto_pay(payment_url, cdk_code, headless=True, log=print):
    """
    完整自动支付流程:
      1. EFunCard 兑换/查询虚拟信用卡
      2. 填写 Stripe 表单 + 处理 hCaptcha/3DS

    返回: {"ok": True/False, "status": "success"/"error"/"timeout"/"captcha_required"}
    """
    log("=" * 50, "ok")
    log("开始自动支付流程", "info")
    log("=" * 50, "ok")

    # 获取卡片信息
    log("查询虚拟信用卡状态...")
    card_info = efun_query(cdk_code, log)

    if card_info and card_info.get("cardNumber") and card_info.get("status") == "ACTIVE":
        log(f"卡片已激活可用: *{card_info.get('lastFour', '????')}", "ok")
    else:
        log("卡片未就绪，尝试兑换...")
        card_info = None
        for retry in range(3):
            card_info = efun_redeem(cdk_code, log)
            if card_info and card_info.get("cardNumber"):
                break
            if retry < 2:
                log("等待 10s 后查询...", "info")
                time.sleep(10)
                card_info = efun_query(cdk_code, log)
                if card_info and card_info.get("cardNumber"):
                    break

        if not card_info or not card_info.get("cardNumber"):
            log("轮询等待开卡...", "info")
            for attempt in range(18):
                time.sleep(10)
                card_info = efun_query(cdk_code, log)
                if card_info and card_info.get("cardNumber"):
                    break
            if not card_info or not card_info.get("cardNumber"):
                log("开卡超时，无法获取卡片信息!", "error")
                return None

        if card_info.get("status") and card_info["status"] != "ACTIVE":
            for _ in range(12):
                time.sleep(5)
                card_info = efun_query(cdk_code, log)
                if card_info and card_info.get("status") == "ACTIVE":
                    log("卡片已激活!", "ok")
                    break
            else:
                log(f"卡片未能激活: {card_info.get('status') if card_info else 'None'}", "error")
                return None

    result = await fill_stripe_checkout(payment_url, card_info, cdk_code, log, headless=headless)

    log("=" * 50, "ok")
    log(f"支付流程结束: {'✅ 成功' if result and result.get('ok') else '❌ 失败'}", "ok" if result and result.get("ok") else "warn")
    log("=" * 50, "ok")
    return result


if __name__ == "__main__":
    import sys

    def _log(msg, level="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] [{level.upper():5s}] {msg}")

    if len(sys.argv) < 3:
        print("用法: python3 stripe_pay.py <payment_url> <cdk_code>")
        sys.exit(1)

    asyncio.run(auto_pay(sys.argv[1], sys.argv[2], headless=True, log=_log))
