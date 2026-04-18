#!/usr/bin/env python3
"""
replit_signup.py — Replit 账号全自动注册 + 子节点接入
======================================================
整合: playwright + fakemail_bridge + captcha_solver + DB 持久化

流程:
  1. 生成随机身份 (用户名/密码/邮箱)
  2. 用 fakemail_bridge 开始监听临时邮箱收件箱
  3. Playwright 打开 replit.com/signup 填表注册
  4. 等待验证邮件 → 提取验证链接 → 访问完成验证
  5. 登录后通过 API 导入 Toolkit fork
  6. 设置 SELF_REGISTER_URL secret
  7. 触发 api-server workflow 启动（5s后自动自注册到主节点）
  8. 将账号信息存入 PostgreSQL

用法:
  # 单个 (有打码key):
  python3 replit_signup.py --count 1 --captcha-key YOUR_KEY

  # 批量 (无打码key，半自动):
  python3 replit_signup.py --count 3 --no-headless

  # 仅探测现有子节点:
  python3 replit_signup.py --probe-only
"""

import argparse
import asyncio
import json
import os
import random
import re
import secrets
import string
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

# ── 配置 ──────────────────────────────────────────────────────────────────────
GATEWAY_API       = os.environ.get("GATEWAY_API",       "http://localhost:8080/api/gateway")
FAKEMAIL_API      = os.environ.get("FAKEMAIL_API",      "http://localhost:6100")
VPS_GATEWAY_URL   = os.environ.get("VPS_GATEWAY_URL",   "http://45.205.27.69:8080/api/gateway")
TOOLKIT_FORK      = os.environ.get("TOOLKIT_FORK",      "Dreamer169/Toolkit")
REPLIT_SIGNUP_URL = "https://replit.com/signup"
DB_URL            = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")

# FakeMail 可用域名 (同 fakemail_bridge.py)
FMG_DOMAINS = [
    "armyspy.com", "dayrep.com", "einrot.com",
    "fleckens.hu", "jourrapide.com", "superrito.com", "teleworm.us"
]

# ANSI 颜色
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"; B = "\033[1m"; X = "\033[0m"

def ok(m):  print(f"{G}✅{X}  {m}", flush=True)
def er(m):  print(f"{R}❌{X}  {m}", flush=True)
def inf(m): print(f"{C}..{X}  {m}", flush=True)
def warn(m):print(f"{Y}⚠️ {X}  {m}", flush=True)
def hdr(m): print(f"\n{B}{'─'*60}\n{m}\n{'─'*60}{X}", flush=True)

# ── 身份生成 ──────────────────────────────────────────────────────────────────
_FIRST = ["alex","blake","casey","dakota","eden","finley","gray","harper","indigo","jordan",
          "kennedy","lane","morgan","noel","parker","quinn","reese","sage","taylor","winter",
          "james","liam","noah","emma","olivia","sophia","lucas","ethan","ava","mia"]
_LAST  = ["smith","jones","chen","kim","lee","wang","patel","garcia","miller","davis",
          "taylor","wilson","moore","anderson","jackson","white","harris","clark","lewis","hall"]

def gen_identity():
    """生成随机用户名/密码/邮箱"""
    first = random.choice(_FIRST)
    last  = random.choice(_LAST)
    num   = random.randint(10, 9999)
    domain = random.choice(FMG_DOMAINS)
    # Replit 用户名规则: 3-20 字符，字母数字下划线
    username = f"{first}{last[:3]}{num}"[:20]
    login    = f"{first}{last[:4]}{num}"[:20].lower()
    email    = f"{login}@{domain}"
    # 密码: 大小写+数字+符号
    pw_chars = string.ascii_letters + string.digits + "!@#$%"
    password = (secrets.token_hex(4).upper()[:2] +
                secrets.token_hex(3).lower()[:2] +
                "".join(secrets.choice(pw_chars) for _ in range(8)))
    return {
        "username": username,
        "email": email,
        "login": login,
        "domain": domain,
        "password": password,
        "first_name": first.capitalize(),
        "last_name": last.capitalize(),
    }

# ── HTTP 工具 ──────────────────────────────────────────────────────────────────
def http_get(url, timeout=10, headers=None):
    h = {"Accept": "application/json", **(headers or {})}
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}

def http_post(url, data, timeout=15, headers=None):
    body = json.dumps(data).encode()
    h = {"Content-Type": "application/json", "Accept": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}

# ── Fakemail 工具 ──────────────────────────────────────────────────────────────
def fakemail_watch(login: str, domain: str) -> bool:
    s, b = http_get(f"{FAKEMAIL_API}/watch?login={login}&domain={domain}")
    return b.get("success", False)

def fakemail_poll(login: str, domain: str,
                  timeout=180, keywords=("verify","confirm","replit","activate")) -> Optional[str]:
    """轮询收件箱，返回第一封匹配的邮件 body"""
    deadline = time.time() + timeout
    inf(f"等待验证邮件 ({timeout}s) → {login}@{domain}")
    while time.time() < deadline:
        s, b = http_get(f"{FAKEMAIL_API}/messages?login={login}&domain={domain}")
        if b.get("success") and b.get("messages"):
            for msg in b["messages"]:
                body = msg.get("body", "") + msg.get("subject", "")
                if any(k in body.lower() for k in keywords):
                    ok(f"收到邮件: {msg.get('subject','')[:60]}")
                    return msg.get("body", "")
        time.sleep(5)
    return None

def extract_verify_url(body: str) -> Optional[str]:
    """从邮件正文提取验证链接"""
    candidates = re.findall(r'https?://[^\s"\'<>]+', body)
    VERIFY_KWS = ("verify", "confirm", "activate", "email-action", "auth")
    urls = [u for u in candidates if any(k in u.lower() for k in VERIFY_KWS)]
    if not urls:
        urls = [u for u in candidates if "replit.com" in u.lower()]
    return urls[0] if urls else None

# ── 数据库 ──────────────────────────────────────────────────────────────────
def db_save_account(identity: dict, token: str = "", status="registered", node_id: str = ""):
    sql = f"""
        INSERT INTO accounts (platform, email, password, username, token, status, notes, tags)
        VALUES (
            'replit',
            {_sql_q(identity['email'])},
            {_sql_q(identity['password'])},
            {_sql_q(identity['username'])},
            {_sql_q(token)},
            {_sql_q(status)},
            {_sql_q(f"node_id={node_id}")},
            'replit,subnode'
        )
        ON CONFLICT (email) DO UPDATE
          SET token=EXCLUDED.token, status=EXCLUDED.status, updated_at=now();
    """
    try:
        r = subprocess.run(
            ["psql", DB_URL, "-t", "-A", "-c", sql],
            capture_output=True, text=True, timeout=8
        )
        return r.returncode == 0
    except Exception as e:
        warn(f"DB 保存失败: {e}")
        return False

def _sql_q(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"

# ── 主注册流程 ─────────────────────────────────────────────────────────────────
async def signup_one(identity: dict, headless: bool, captcha_key: str,
                     captcha_provider: str) -> dict:
    """单个账号完整注册流程"""
    from playwright.async_api import async_playwright

    result = {
        "email": identity["email"],
        "username": identity["username"],
        "ok": False,
        "phase": "init",
        "node_id": "",
        "error": ""
    }

    # 1. 监听邮箱
    hdr(f"账号: {identity['username']} <{identity['email']}>")
    if not fakemail_watch(identity["login"], identity["domain"]):
        result["error"] = "fakemail watch 失败"
        er(result["error"])
        return result
    ok("邮箱监听已启动")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            user_agent=random.choice([
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            ])
        )
        page = await ctx.new_page()

        try:
            # 2. 打开 signup 页面
            result["phase"] = "navigate"
            inf("打开 replit.com/signup …")
            await page.goto(REPLIT_SIGNUP_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            # 3. 检查是否已有表单（用户名/邮箱/密码）
            result["phase"] = "fill_form"
            inf("填写注册表单 …")

            # Replit signup: 先输入 username
            username_input = page.locator('input[name="username"], input[placeholder*="username" i], input[id*="username" i]')
            if await username_input.count() > 0:
                await username_input.first.click()
                await page.wait_for_timeout(500)
                await username_input.first.type(identity["username"], delay=80)
                inf(f"  用户名: {identity['username']}")
            else:
                warn("未找到 username 字段，可能页面结构已变化")

            await page.wait_for_timeout(800)

            # email 字段
            email_input = page.locator('input[type="email"], input[name="email"], input[placeholder*="email" i]')
            if await email_input.count() > 0:
                await email_input.first.click()
                await page.wait_for_timeout(300)
                await email_input.first.type(identity["email"], delay=70)
                inf(f"  邮箱: {identity['email']}")

            await page.wait_for_timeout(500)

            # password 字段
            pwd_input = page.locator('input[type="password"], input[name="password"]')
            if await pwd_input.count() > 0:
                await pwd_input.first.click()
                await page.wait_for_timeout(300)
                await pwd_input.first.type(identity["password"], delay=60)
                inf(f"  密码: {identity['password']}")

            await page.wait_for_timeout(1000)

            # 截图存档
            await page.screenshot(path=f"/tmp/replit_signup_{identity['username']}_form.png")
            inf("  表单截图已保存")

            # 4. 提交（可能有 hCaptcha）
            result["phase"] = "submit"
            submit_btn = page.locator('button[type="submit"], button:has-text("Create Account"), button:has-text("Sign up")')
            if await submit_btn.count() > 0:
                await submit_btn.first.click()
                inf("  已点击提交按钮")
            else:
                await page.keyboard.press("Enter")
                inf("  已按 Enter 提交")

            # 等待跳转或 hCaptcha 弹出
            await page.wait_for_timeout(5000)
            current_url = page.url

            # 5. 检查是否出现 hCaptcha
            captcha_frame = page.frame_locator('iframe[title*="hCaptcha" i], iframe[src*="hcaptcha" i]')
            has_captcha = await page.locator('iframe[src*="hcaptcha"], .h-captcha').count() > 0

            if has_captcha:
                result["phase"] = "captcha"
                warn("检测到 hCaptcha")
                if captcha_key:
                    inf(f"  调用 {captcha_provider} 打码 …")
                    try:
                        sys.path.insert(0, "/root/Toolkit/artifacts/api-server")
                        from captcha_solver import solve_with_fallback
                        token = solve_with_fallback(
                            [(captcha_provider, captcha_key)],
                            "hcaptcha",
                            page_url=REPLIT_SIGNUP_URL,
                            site_key="a5f74b19-9e45-40e0-b45d-47ff91b7a6c2"  # Replit hCaptcha key
                        )
                        # 注入 hcaptcha token
                        await page.evaluate(f"""
                            document.querySelector('textarea[name="h-captcha-response"]').value = '{token}';
                            document.querySelector('textarea[name="g-recaptcha-response"]') &&
                              (document.querySelector('textarea[name="g-recaptcha-response"]').value = '{token}');
                        """)
                        await submit_btn.first.click()
                        await page.wait_for_timeout(4000)
                        ok("  打码完成，已重新提交")
                    except Exception as ce:
                        warn(f"  打码失败: {ce}")
                        if not headless:
                            warn("  请手动完成 CAPTCHA，脚本将等待 60s …")
                            await page.wait_for_timeout(60000)
                        else:
                            result["error"] = f"captcha 失败: {ce}"
                            return result
                else:
                    if not headless:
                        warn("  未提供打码 key，请手动完成 CAPTCHA (60s) …")
                        await page.wait_for_timeout(60000)
                    else:
                        warn("  无头模式下无法手动完成 CAPTCHA，跳过")
                        result["error"] = "需要 --captcha-key 或 --no-headless"
                        return result

            # 6. 等待跳转到 /home 或验证邮件提示
            await page.wait_for_timeout(3000)
            current_url = page.url
            inf(f"  当前 URL: {current_url[:80]}")

            # 截图
            await page.screenshot(path=f"/tmp/replit_signup_{identity['username']}_after.png")

        except Exception as e:
            er(f"浏览器操作出错: {e}")
            try:
                await page.screenshot(path=f"/tmp/replit_signup_{identity['username']}_error.png")
            except:
                pass
            result["error"] = str(e)
            await browser.close()
            return result

        # 7. 监听验证邮件
        result["phase"] = "email_verify"
        body = fakemail_poll(identity["login"], identity["domain"], timeout=180)
        if not body:
            warn("未收到验证邮件（可能已发送，请手动检查）")
        else:
            verify_url = extract_verify_url(body)
            if verify_url:
                inf(f"验证链接: {verify_url[:80]}…")
                try:
                    await page.goto(verify_url, wait_until="networkidle", timeout=30000)
                    await page.wait_for_timeout(5000)
                    ok(f"  邮件验证完成 → {page.url[:60]}")
                except Exception as e:
                    warn(f"  验证链接访问失败: {e}")
            else:
                warn("  无法提取验证链接")

        # 8. 通过 Replit API 获取 session cookies，导入 Toolkit fork
        result["phase"] = "import_fork"
        cookies = await ctx.cookies("https://replit.com")
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

        node_id = await import_toolkit_fork(page, ctx, identity, cookie_header)
        result["node_id"] = node_id or ""

        await browser.close()

    # 9. 等待子节点自注册
    result["phase"] = "wait_self_register"
    if node_id:
        ok(f"Fork 导入完成，节点 ID: {node_id}")
        inf("等待 30s 让子节点自注册 …")
        time.sleep(30)
        # 验证节点是否出现在网关
        s, b = http_get(f"{GATEWAY_API}/nodes", timeout=10)
        if b.get("nodes"):
            matched = [n for n in b["nodes"] if identity["username"].lower() in n.get("name","").lower()
                      or identity["email"].split("@")[0] in n.get("name","").lower()]
            if matched:
                ok(f"子节点已出现在网关: {matched[0]['name']} [{matched[0]['status']}]")
                result["node_id"] = matched[0]["id"]
            else:
                warn("子节点尚未出现在网关（可能还在启动）")

    # 10. 存库
    result["phase"] = "save_db"
    db_save_account(identity, status="registered", node_id=result["node_id"])
    ok(f"账号已保存到 DB: {identity['email']}")

    result["ok"] = True
    result["phase"] = "done"
    return result


async def import_toolkit_fork(page, ctx, identity: dict, cookie_header: str) -> Optional[str]:
    """
    在 Replit 中导入 Toolkit fork，设置 SELF_REGISTER_URL secret，
    触发 workflow 启动
    """
    inf("导入 Toolkit fork …")
    try:
        # 导航到 GitHub import 页面
        import_url = f"https://replit.com/new/github/{TOOLKIT_FORK}"
        await page.goto(import_url, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(5000)

        # 等待导入完成（URL 会变成 /replit/Toolkit-xxx 或类似）
        for _ in range(30):
            cur = page.url
            if "/replit/" in cur or "@" in cur or "repl.co" in cur:
                ok(f"  Fork 已导入: {cur[:70]}")
                break
            await page.wait_for_timeout(2000)

        # 尝试提取 repl slug 和 ID
        cur = page.url
        slug_match = re.search(r"replit\.com/@([^/]+)/([^?#]+)", cur)
        repl_slug = slug_match.group(2) if slug_match else ""
        repl_owner = slug_match.group(1) if slug_match else identity["username"]

        if repl_slug:
            inf(f"  Repl: @{repl_owner}/{repl_slug}")
            # 通过 GraphQL API 设置 SELF_REGISTER_URL secret
            await set_replit_secret(ctx, repl_owner, repl_slug, cookie_header)
        else:
            warn(f"  无法提取 repl slug，URL: {cur[:80]}")

        return f"{repl_owner}/{repl_slug}" if repl_slug else ""

    except Exception as e:
        warn(f"  Fork 导入失败: {e}")
        return None


async def set_replit_secret(ctx, owner: str, slug: str, cookie_header: str):
    """通过 Replit GraphQL 设置 SELF_REGISTER_URL 环境变量"""
    inf(f"  设置 SELF_REGISTER_URL secret …")
    # 尝试通过 Replit 的环境变量 API
    try:
        gql_url = "https://replit.com/graphql"
        # 获取 repl ID
        s, b = http_post(gql_url, {
            "query": """
              query GetRepl($url: String!) {
                repl(url: $url) { id slug }
              }
            """,
            "variables": {"url": f"/@{owner}/{slug}"}
        }, headers={"Cookie": cookie_header, "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"https://replit.com/@{owner}/{slug}"})

        repl_id = b.get("data", {}).get("repl", {}).get("id", "")
        if repl_id:
            ok(f"  Repl ID: {repl_id}")
            # 设置 secret
            s2, b2 = http_post(gql_url, {
                "query": """
                  mutation SetEnvVar($replId: String!, $key: String!, $value: String!) {
                    updateEnvVar(replId: $replId, key: $key, value: $value) { key }
                  }
                """,
                "variables": {
                    "replId": repl_id,
                    "key": "SELF_REGISTER_URL",
                    "value": VPS_GATEWAY_URL
                }
            }, headers={"Cookie": cookie_header, "X-Requested-With": "XMLHttpRequest"})
            if b2.get("data", {}).get("updateEnvVar"):
                ok(f"  SELF_REGISTER_URL 设置成功 → {VPS_GATEWAY_URL}")
            else:
                warn(f"  secret 设置响应: {str(b2)[:120]}")
        else:
            warn(f"  获取 Repl ID 失败: {str(b)[:120]}")
    except Exception as e:
        warn(f"  设置 secret 失败: {e}")


# ── 探测现有子节点 ──────────────────────────────────────────────────────────────
def probe_existing_nodes():
    hdr("探测现有 VPS 网关子节点")
    s, b = http_get(f"{GATEWAY_API}/nodes")
    if s != 200:
        er(f"无法访问 gateway API: HTTP {s}")
        return
    nodes = b.get("nodes", [])
    t = b.get("totals", {})
    print(f"  总计 {t.get('nodes',0)} 个节点，{t.get('ready',0)} 就绪，{t.get('disabled',0)} 禁用")
    print()
    for n in nodes:
        status_icon = "✅" if n["status"] == "ready" else ("⛔" if n["status"] == "disabled" else "🔴")
        url = n.get("baseUrl","")[:55]
        print(f"  {status_icon} [{n['status']:8}] {n['name'][:30]:30} {url}")
        print(f"     src:{n.get('source','?'):8} succ:{n.get('successes',0)} fail:{n.get('failures',0)}")
    print()


# ── 批量注册 ──────────────────────────────────────────────────────────────────
async def batch_signup(count: int, headless: bool, captcha_key: str,
                       captcha_provider: str, delay_between: int):
    hdr(f"Replit 子节点自动注册 — {count} 个账号")
    print(f"  模式: {'无头' if headless else '有界面'}  打码: {captcha_provider if captcha_key else '手动/无'}")
    print()

    results = []
    for i in range(count):
        identity = gen_identity()
        print(f"\n{B}[{i+1}/{count}]{X} 开始注册 {identity['username']}")
        r = await signup_one(identity, headless, captcha_key, captcha_provider)
        results.append(r)

        # 输出结果
        if r["ok"]:
            ok(f"注册成功! 邮箱={r['email']} 节点={r['node_id'] or '(待确认)'}")
        else:
            er(f"注册失败 (phase={r['phase']}): {r['error'][:80]}")

        if i < count - 1:
            inf(f"等待 {delay_between}s 后注册下一个 …")
            await asyncio.sleep(delay_between)

    # 汇总
    hdr("注册汇总")
    ok_count = sum(1 for r in results if r["ok"])
    print(f"  成功 {ok_count}/{count}")
    for r in results:
        icon = "✅" if r["ok"] else "❌"
        print(f"  {icon} {r['email']:40} 节点={r['node_id'] or '—'}")
    print()

    # 最终检查节点池
    probe_existing_nodes()


# ── 入口 ──────────────────────────────────────────────────────────────────────
async def main():
    ap = argparse.ArgumentParser(
        description="Replit 账号自动注册 + 子节点接入",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    ap.add_argument("--count",            type=int, default=1,          help="注册账号数量 (默认 1)")
    ap.add_argument("--headless",         action="store_true",          help="无头模式 (默认)")
    ap.add_argument("--no-headless",      dest="headless", action="store_false", help="显示浏览器窗口")
    ap.add_argument("--captcha-key",      default="",                   help="打码服务 API Key")
    ap.add_argument("--captcha-provider", default="capsolver",
                    choices=["2captcha","capmonster","yescaptcha","capsolver"],
                    help="打码服务 (默认: capsolver)")
    ap.add_argument("--delay",            type=int, default=30,         help="批量注册间隔秒 (默认 30)")
    ap.add_argument("--probe-only",       action="store_true",          help="只探测现有节点，不注册")
    ap.add_argument("--gateway",          default="",                   help="覆盖本地 gateway API URL")
    ap.add_argument("--vps-gateway",      default="",                   help="覆盖 VPS gateway URL (写入子节点 secret)")
    ap.set_defaults(headless=True)
    a = ap.parse_args()

    global GATEWAY_API, VPS_GATEWAY_URL
    if a.gateway:     GATEWAY_API    = a.gateway.rstrip("/")
    if a.vps_gateway: VPS_GATEWAY_URL = a.vps_gateway.rstrip("/")

    if a.probe_only:
        probe_existing_nodes()
        return

    await batch_signup(
        count=a.count,
        headless=a.headless,
        captcha_key=a.captcha_key,
        captcha_provider=a.captcha_provider,
        delay_between=a.delay,
    )

if __name__ == "__main__":
    asyncio.run(main())
