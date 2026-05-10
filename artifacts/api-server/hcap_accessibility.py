#!/usr/bin/env python3
"""
hcap_accessibility.py — hCaptcha Accessibility Cookie 注册器

流程:
  1. Guerrilla Mail 获取临时邮箱
  2. Playwright 打开 dashboard.hcaptcha.com/signup?type=accessibility
  3. 填入邮箱提交 → 等待验证邮件
  4. 在浏览器内访问验证链接 → 让 hCaptcha 设置 hmt_id Cookie
  5. 提取并保存到 hcap_cookie.json（同目录）

使用方式:
  python3 hcap_accessibility.py               # 注册新 Cookie
  python3 hcap_accessibility.py --check       # 检查已保存 Cookie 是否有效
"""
import argparse
import asyncio
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

COOKIE_FILE = Path(__file__).parent / "hcap_cookie.json"

# ── Guerrilla Mail API ────────────────────────────────────────────────────────
GML_BASE = "https://api.guerrillamail.com/ajax.php"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_gml_sid = ""


def gml_get_addr():
    global _gml_sid
    req = urllib.request.Request(
        GML_BASE + "?f=get_email_address",
        headers={"User-Agent": _UA},
    )
    r = urllib.request.urlopen(req, timeout=15)
    d = json.loads(r.read())
    _gml_sid = d.get("sid_token", "")
    return d.get("email_addr", "")


def gml_check():
    try:
        req = urllib.request.Request(
            GML_BASE + f"?f=check_email&seq=0&sid_token={_gml_sid}",
            headers={"User-Agent": _UA},
        )
        r = urllib.request.urlopen(req, timeout=15)
        return json.loads(r.read()).get("list", [])
    except Exception:
        return []


def gml_fetch(email_id):
    try:
        req = urllib.request.Request(
            GML_BASE + f"?f=fetch_email&email_id={email_id}&sid_token={_gml_sid}",
            headers={"User-Agent": _UA},
        )
        r = urllib.request.urlopen(req, timeout=15)
        return json.loads(r.read())
    except Exception:
        return {}


def _find_hcap_link(body: str) -> str | None:
    urls = re.findall(r"https?://[^\s<>\"']+", body)
    for u in urls:
        if "hcaptcha.com" in u and any(
            k in u for k in ("verify", "confirm", "activate", "access", "email")
        ):
            return u
    return None


# ── 核心流程 ──────────────────────────────────────────────────────────────────
async def register_accessibility_cookie(headless: bool = True) -> dict | None:
    from playwright.async_api import async_playwright

    print("[hcap] 获取 Guerrilla Mail 临时邮箱...", flush=True)
    email = gml_get_addr()
    if not email:
        print("[hcap] ❌ Guerrilla Mail 获取失败", flush=True)
        return None
    print(f"[hcap] 临时邮箱: {email}", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            locale="en-US",
            timezone_id="America/New_York",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        # ── 步骤 1：打开注册页 ───────────────────────────────────────────────
        print("[hcap] 打开 hCaptcha 无障碍注册页...", flush=True)
        await page.goto(
            "https://dashboard.hcaptcha.com/signup?type=accessibility&hl=en",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await asyncio.sleep(3)
        await page.screenshot(path="/tmp/hcap_acc_step1.png")

        # ── 步骤 2：填邮箱并提交 ─────────────────────────────────────────────
        filled = False
        for sel in ['input[type="email"]', 'input[type="text"]', 'input:not([type])']:
            inp = await page.query_selector(sel)
            if inp:
                await inp.fill(email)
                print(f"[hcap] 填入邮箱: {email}", flush=True)
                filled = True
                break

        if not filled:
            print("[hcap] ❌ 未找到邮箱输入框", flush=True)
            await page.screenshot(path="/tmp/hcap_acc_no_input.png")
            await browser.close()
            return None

        await asyncio.sleep(0.5)
        for btn_sel in [
            'button[type="submit"]',
            'button:has-text("Submit")',
            'button:has-text("Sign up")',
            'button:has-text("Register")',
            'button',
        ]:
            btn = await page.query_selector(btn_sel)
            if btn:
                await btn.click()
                print(f"[hcap] 已点击提交: {btn_sel}", flush=True)
                break

        await asyncio.sleep(3)
        await page.screenshot(path="/tmp/hcap_acc_step2.png")
        page_text = await page.evaluate("() => document.body.innerText")
        print(f"[hcap] 提交后: {page_text[:200]!r}", flush=True)

        # ── 步骤 3：等待验证邮件（最多 90s）──────────────────────────────────
        print("[hcap] 等待验证邮件...", flush=True)
        verify_link = None
        seen_ids: set = set()
        start = time.time()
        while time.time() - start < 90 and not verify_link:
            await asyncio.sleep(5)
            elapsed = int(time.time() - start)
            for em in gml_check():
                eid = em.get("mail_id", "")
                if eid in seen_ids:
                    continue
                seen_ids.add(eid)
                subj = em.get("mail_subject", "")
                frm  = em.get("mail_from", "")
                print(f"[hcap]   邮件: from={frm!r} subj={subj!r}", flush=True)
                if (
                    "hcaptcha" in frm.lower()
                    or "hcaptcha" in subj.lower()
                    or "access" in subj.lower()
                    or "verif" in subj.lower()
                ):
                    full = gml_fetch(eid)
                    body = str(full.get("mail_body", "")) + str(full.get("mail_body_html", ""))
                    link = _find_hcap_link(body)
                    if link:
                        verify_link = link
                        print(f"[hcap] ✅ 验证链接: {link[:80]}", flush=True)
                        break
            if not verify_link:
                print(f"[hcap]   等待中... ({elapsed}s/90s)", flush=True)

        if not verify_link:
            print("[hcap] ❌ 未收到验证邮件", flush=True)
            # 检查有没有已有 cookie（某些情况提交后立即种 cookie）
            existing = [
                c for c in await context.cookies()
                if c.get("name") in ("hmt_id", "hc_accessibility")
            ]
            if not existing:
                await browser.close()
                return None
            print(f"[hcap] ⚠ 无验证邮件，但发现 {len(existing)} 个 Cookie，继续...", flush=True)
            verify_link = "__skip__"

        # ── 步骤 4：访问验证链接 ──────────────────────────────────────────────
        if verify_link != "__skip__":
            print("[hcap] 访问验证链接...", flush=True)
            verify_page = await context.new_page()
            await verify_page.goto(verify_link, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)
            await verify_page.screenshot(path="/tmp/hcap_acc_verified.png")
            vtext = await verify_page.evaluate("() => document.body.innerText")
            print(f"[hcap] 验证页: {vtext[:200]!r}", flush=True)
            await verify_page.close()

        # ── 步骤 5：提取 Cookie ───────────────────────────────────────────────
        all_cookies = await context.cookies()
        acc_cookies = [
            c for c in all_cookies
            if c.get("name") in ("hmt_id", "hc_accessibility")
        ]
        print(f"[hcap] 找到 {len(acc_cookies)} 个无障碍 Cookie", flush=True)
        for c in acc_cookies:
            print(f"  {c['name']} = {c['value'][:30]}... (domain={c.get('domain')})", flush=True)

        await browser.close()

        if not acc_cookies:
            print("[hcap] ❌ 未获取到无障碍 Cookie", flush=True)
            return None

        # ── 步骤 6：持久化保存 ────────────────────────────────────────────────
        payload = {
            "registered_at": datetime.utcnow().isoformat() + "Z",
            "email": email,
            "cookies": acc_cookies,
        }
        COOKIE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"\n[hcap] ✅ Cookie 已保存到 {COOKIE_FILE}", flush=True)
        return payload


# ── 检查 Cookie 有效性 ────────────────────────────────────────────────────────
async def check_cookie() -> bool:
    if not COOKIE_FILE.exists():
        print(f"[hcap] Cookie 文件不存在: {COOKIE_FILE}", flush=True)
        return False
    data = json.loads(COOKIE_FILE.read_text())
    cookies = data.get("cookies", [])
    hmt = next((c for c in cookies if c["name"] == "hmt_id"), None)
    if not hmt:
        print("[hcap] 未找到 hmt_id Cookie", flush=True)
        return False
    print(f"[hcap] hmt_id = {hmt['value'][:20]}...", flush=True)
    print(f"[hcap] 注册时间: {data.get('registered_at', '?')}", flush=True)
    print("[hcap] ✅ Cookie 文件存在且格式正常", flush=True)
    return True


# ── CLI 入口 ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="hCaptcha Accessibility Cookie 注册器")
    ap.add_argument("--check",    action="store_true", help="检查已保存 Cookie 有效性")
    ap.add_argument("--headless", default="true",      help="无头模式 (true/false)")
    args = ap.parse_args()

    headless = args.headless.lower() in ("true", "1", "yes")

    if args.check:
        ok = asyncio.run(check_cookie())
        sys.exit(0 if ok else 1)
    else:
        result = asyncio.run(register_accessibility_cookie(headless=headless))
        if result:
            print("\n✅ 注册成功！Cookie 已保存，stripe_pay.py 将自动使用。", flush=True)
            sys.exit(0)
        else:
            print("\n❌ 注册失败", flush=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
