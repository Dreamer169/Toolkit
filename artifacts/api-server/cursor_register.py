"""
Cursor.sh 账号自动注册脚本

注册流程:
  1. 打开 cursor.sh signup 页面
  2. 输入真实人名 + 生成的临时邮箱
  3. 等待 OTP 验证码 (6位数字)
  4. 通过 FakeMail bridge 或 MailTM API 读取验证码
  5. 输入验证码完成注册

用法:
  python3 cursor_register.py --count 1 --proxy socks5://127.0.0.1:1080
"""

import argparse
import asyncio
import json
import random
import re
import secrets
import string
import sys
import time

# ─── 复用 outlook 的工具函数 ───────────────────────────────────────────────────
def gen_password(n=None):
    n = n or random.randint(12, 16)
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pw = "".join(secrets.choice(chars) for _ in range(n))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "!@#$%^&*" for c in pw)):
            return pw


def gen_name():
    """生成真实英文姓名"""
    FIRST = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas",
             "Christopher","Daniel","Matthew","Anthony","Mark","Steven","Paul","Andrew","Joshua",
             "Benjamin","Samuel","Patrick","Jack","Tyler","Aaron","Nathan","Kyle","Bryan","Eric",
             "Mary","Patricia","Jennifer","Linda","Elizabeth","Susan","Jessica","Sarah","Karen",
             "Lisa","Nancy","Ashley","Emily","Donna","Michelle","Amanda","Melissa","Rebecca","Laura",
             "Emma","Olivia","Sophia","Isabella","Lucas","Ethan","Mason","Liam","Noah","Ava"]
    LAST  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez",
             "Martinez","Hernandez","Lopez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson",
             "Lee","Perez","Thompson","White","Harris","Clark","Ramirez","Lewis","Robinson","Walker",
             "Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Green","Adams",
             "Nelson","Baker","Campbell","Mitchell","Carter","Turner","Phillips","Evans","Collins"]
    return random.choice(FIRST), random.choice(LAST)


def emit(type_: str, msg: str):
    """输出 JSON 日志行，Node.js 父进程通过 stdout 读取"""
    line = json.dumps({"type": type_, "message": msg}, ensure_ascii=False)
    print(line, flush=True)


# ─── MailTM 临时邮箱 ──────────────────────────────────────────────────────────
import urllib.request
import urllib.error

MAILTM_BASE = "https://api.mail.tm"

def mailtm_create():
    """创建 MailTM 临时邮箱，返回 (email, password, token)"""
    try:
        r = urllib.request.urlopen(MAILTM_BASE + "/domains", timeout=10)
        domains = json.loads(r.read())
        domain = domains["hydra:member"][0]["domain"]
    except Exception:
        domain = "sharklasers.com"

    username = "cursor" + secrets.token_hex(6)
    email = f"{username}@{domain}"
    password = "CursorReg" + secrets.token_hex(4) + "!"

    data = json.dumps({"address": email, "password": password}).encode()
    req = urllib.request.Request(MAILTM_BASE + "/accounts", data=data,
                                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        acc = json.loads(r.read())
        emit("info", f"📧 临时邮箱创建: {email}")
    except Exception as e:
        # Account already exists - get token anyway
        emit("warn", f"邮箱创建异常: {e}，尝试登录...")

    # 获取 token
    data = json.dumps({"address": email, "password": password}).encode()
    req = urllib.request.Request(MAILTM_BASE + "/token", data=data,
                                  headers={"Content-Type": "application/json"}, method="POST")
    r = urllib.request.urlopen(req, timeout=10)
    token_data = json.loads(r.read())
    return email, password, token_data["token"]


def mailtm_wait_otp(token: str, timeout: int = 90) -> str | None:
    """轮询 MailTM 收件箱，找到含 6 位数字验证码的邮件"""
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout
    seen = set()

    while time.time() < deadline:
        try:
            req = urllib.request.Request(MAILTM_BASE + "/messages", headers=headers)
            r = urllib.request.urlopen(req, timeout=10)
            msgs = json.loads(r.read())["hydra:member"]
            for msg in msgs:
                mid = msg["id"]
                if mid in seen:
                    continue
                seen.add(mid)
                # 获取邮件详情
                req2 = urllib.request.Request(f"{MAILTM_BASE}/messages/{mid}", headers=headers)
                r2 = urllib.request.urlopen(req2, timeout=10)
                detail = json.loads(r2.read())
                body = detail.get("text", "") or detail.get("html", "")
                # 查找 6 位数字验证码
                otp_match = re.search(r'\b(\d{6})\b', body)
                if otp_match:
                    otp = otp_match.group(1)
                    emit("info", f"✅ 收到验证码: {otp}")
                    return otp
        except Exception:
            pass
        time.sleep(3)

    return None


# ─── patchright 注册控制器 ────────────────────────────────────────────────────
async def register_one(proxy: str, headless: bool = True) -> dict | None:
    """注册单个 Cursor 账号，成功返回账号信息字典，失败返回 None"""
    start = time.time()

    # 生成账号信息
    first_name, last_name = gen_name()
    password = gen_password()

    # 创建临时邮箱
    try:
        email, _epw, mail_token = mailtm_create()
    except Exception as e:
        emit("error", f"❌ 临时邮箱创建失败: {e}")
        return None

    emit("info", f"👤 {first_name} {last_name} | 📧 {email}")
    emit("info", "🌐 启动浏览器 → cursor.sh signup...")

    try:
        from patchright.async_api import async_playwright
    except ImportError:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            emit("error", "❌ 未安装 patchright/playwright，请运行: pip install patchright")
            return None

    proxy_cfg = None
    if proxy:
        # 解析代理
        from urllib.parse import urlparse
        p = urlparse(proxy)
        proxy_cfg = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
        if p.username:
            proxy_cfg["username"] = p.username
            proxy_cfg["password"] = p.password or ""

    async with async_playwright() as pw:
        browser_type = pw.chromium
        launch_opts = {
            "headless": headless,
            "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        }
        if proxy_cfg:
            launch_opts["proxy"] = proxy_cfg

        browser = await browser_type.launch(**launch_opts)
        ctx = await browser.new_context(
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()

        try:
            # Step 1: 打开 Cursor signup 页面
            await page.goto("https://authenticator.cursor.sh/sign-up", timeout=30000,
                           wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            emit("info", "📄 已打开注册页面")

            # Step 2: 填写姓名（如果有姓名字段）
            name_input = page.locator("input[name='first_name'], input[placeholder*='first' i], input[name='name'], input[placeholder*='name' i]")
            if await name_input.count() > 0:
                await name_input.first.fill(first_name)
                await page.wait_for_timeout(300)

            last_input = page.locator("input[name='last_name'], input[placeholder*='last' i]")
            if await last_input.count() > 0:
                await last_input.first.fill(last_name)
                await page.wait_for_timeout(300)

            # Step 3: 填写邮箱
            email_input = page.locator("input[type='email'], input[name='email'], input[placeholder*='email' i]")
            await email_input.first.wait_for(state="visible", timeout=10000)
            await email_input.first.fill(email)
            emit("info", f"📧 已填写邮箱: {email}")
            await page.wait_for_timeout(500)

            # Step 4: 提交 (回车 或 Continue 按钮)
            continue_btn = page.locator("button[type='submit'], button:has-text('Continue'), button:has-text('Sign up'), input[type='submit']")
            if await continue_btn.count() > 0:
                await continue_btn.first.click()
            else:
                await email_input.first.press("Enter")
            emit("info", "⏳ 等待验证码邮件...")
            await page.wait_for_timeout(2000)

            # 并行：等待 OTP 出现在邮箱
            otp = await asyncio.get_event_loop().run_in_executor(
                None, lambda: mailtm_wait_otp(mail_token, timeout=90)
            )
            if not otp:
                emit("error", "❌ 超时未收到验证码邮件")
                await browser.close()
                return None

            # Step 5: 输入 OTP (6位验证码)
            await page.wait_for_timeout(1000)
            otp_input = page.locator("input[autocomplete='one-time-code'], input[type='text'][maxlength='6'], input[name='code']")

            # 尝试各种 OTP 输入方式
            if await otp_input.count() > 0:
                await otp_input.first.fill(otp)
                emit("info", f"🔢 已填写验证码: {otp}")
                await page.wait_for_timeout(500)
                # 提交
                await otp_input.first.press("Enter")
            else:
                # 可能是分开的 6 个格子
                digit_inputs = page.locator("input[maxlength='1']")
                if await digit_inputs.count() >= 6:
                    for i, digit in enumerate(otp):
                        await digit_inputs.nth(i).fill(digit)
                        await page.wait_for_timeout(100)
                    emit("info", f"🔢 已填写验证码 (逐格): {otp}")
                else:
                    # 尝试直接 type
                    await page.keyboard.type(otp, delay=100)
                    emit("info", f"🔢 已键入验证码: {otp}")
                await page.wait_for_timeout(500)
                await page.keyboard.press("Enter")

            await page.wait_for_timeout(3000)

            # Step 6: 可能要填写密码
            pw_input = page.locator("input[type='password'], input[name='password']")
            if await pw_input.count() > 0:
                await pw_input.first.fill(password)
                confirm_pw = page.locator("input[name='confirm_password'], input[placeholder*='confirm' i]")
                if await confirm_pw.count() > 0:
                    await confirm_pw.first.fill(password)
                submit_btn = page.locator("button[type='submit']")
                if await submit_btn.count() > 0:
                    await submit_btn.first.click()
                    emit("info", "🔑 已设置密码")
                await page.wait_for_timeout(2000)

            # Step 7: 确认注册成功 (检查 URL 是否离开了 signup 页面)
            await page.wait_for_timeout(3000)
            current_url = page.url
            elapsed = round(time.time() - start, 1)

            # 成功标志：URL 不再是 signup，或包含 dashboard/settings
            if "sign-up" not in current_url and "error" not in current_url.lower():
                account = {"email": email, "password": password, "name": f"{first_name} {last_name}"}
                emit("success", f"✅ 注册成功  |  {email}  密码: {password}  耗时: {elapsed}s")
                await browser.close()
                return account
            else:
                # 再等等
                await page.wait_for_timeout(3000)
                current_url = page.url
                elapsed = round(time.time() - start, 1)
                if "sign-up" not in current_url:
                    account = {"email": email, "password": password, "name": f"{first_name} {last_name}"}
                    emit("success", f"✅ 注册成功  |  {email}  密码: {password}  耗时: {elapsed}s")
                    await browser.close()
                    return account
                else:
                    emit("error", f"❌ 注册失败，当前 URL: {current_url}")
                    await browser.close()
                    return None

        except Exception as e:
            emit("error", f"❌ 注册异常: {type(e).__name__}: {e}")
            try:
                await browser.close()
            except Exception:
                pass
            return None


# ─── 主入口 ───────────────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--proxy", type=str, default="")
    parser.add_argument("--headless", type=str, default="true")
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()

    headless = args.headless.lower() != "false"
    count = args.count
    success_count = 0
    accounts = []

    emit("info", f"🚀 开始 Cursor 批量注册: {count} 个 | 并发: {args.concurrency} | 代理: {args.proxy or '无'}")

    sem = asyncio.Semaphore(args.concurrency)

    async def limited_register():
        async with sem:
            return await register_one(proxy=args.proxy, headless=headless)

    tasks = [limited_register() for _ in range(count)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, dict) and r:
            accounts.append(r)
            success_count += 1
        elif isinstance(r, Exception):
            emit("error", f"任务异常: {r}")

    emit("done", f"✅ 成功: {success_count} / {count}")
    emit("done", f"注册任务完成 · 成功 {success_count} 个 / 共 {count} 个 {'✅' if success_count else '❌'}")
    # 输出账号列表供 Node.js 解析
    if accounts:
        emit("accounts", json.dumps(accounts, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
