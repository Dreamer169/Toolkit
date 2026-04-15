"""
Cursor.sh 账号自动注册脚本 v2 — 整合 j-cli CDP 网络拦截 + SheepKing 并发会话思想

新增特性:
  1. [j-cli CDP] 网络请求拦截：自动捕获 Cursor 认证 API 返回的 session token
  2. [j-cli snapshot] 页面快照元素检测：动态找表单字段，不依赖硬编码 CSS
  3. [SheepKing AgentSessionPool] 每个注册任务有独立 session，状态互不干扰

注册流程:
  1. 打开 cursor.sh signup 页面
  2. 安装网络拦截器（捕获 token）
  3. 用快照方式检测表单字段并填写
  4. 等待 OTP 验证码并填入
  5. 从网络拦截中提取 session token
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
from urllib.parse import urlparse
from browser_fingerprint import gen_profile, context_kwargs, apply_fingerprint, profile_summary
import urllib.request
import urllib.error
from urllib.parse import urlparse


# ─── 工具函数 ────────────────────────────────────────────────────────────────
def gen_password(n=None):
    n = n or random.randint(12, 16)
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pw = "".join(secrets.choice(chars) for _ in range(n))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "!@#$%^&*" for c in pw)):
            return pw


def gen_name():
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
    line = json.dumps({"type": type_, "message": msg}, ensure_ascii=False)
    print(line, flush=True)


# ─── MailTM 临时邮箱 ─────────────────────────────────────────────────────────
MAILTM_BASE = "https://api.mail.tm"

def mailtm_create():
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
        urllib.request.urlopen(req, timeout=10)
        emit("info", f"📧 临时邮箱创建: {email}")
    except Exception as e:
        emit("warn", f"邮箱创建异常: {e}，尝试登录...")

    data = json.dumps({"address": email, "password": password}).encode()
    req = urllib.request.Request(MAILTM_BASE + "/token", data=data,
                                  headers={"Content-Type": "application/json"}, method="POST")
    r = urllib.request.urlopen(req, timeout=10)
    token_data = json.loads(r.read())
    return email, password, token_data["token"]


def mailtm_wait_otp(token: str, timeout: int = 90) -> str | None:
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
                req2 = urllib.request.Request(f"{MAILTM_BASE}/messages/{mid}", headers=headers)
                r2 = urllib.request.urlopen(req2, timeout=10)
                detail = json.loads(r2.read())
                body = detail.get("text", "") or detail.get("html", "")
                otp_match = re.search(r'\b(\d{6})\b', body)
                if otp_match:
                    otp = otp_match.group(1)
                    emit("info", f"✅ 收到验证码: {otp}")
                    return otp
        except Exception:
            pass
        time.sleep(3)

    return None





# ─── MS Graph OTP 读取 (with DB token fallback) ──────────────────────────────
def _get_otp_via_graph_or_browser(email: str, password: str, timeout: int = 120):
    """先尝试 MS Graph API 读取 OTP，失败则回退到浏览器登录。"""
    import urllib.request, json as _json, re as _re, time as _time, subprocess

    graph_token = None
    try:
        r = subprocess.run(
            ['psql', 'postgresql://postgres:postgres@localhost/toolkit',
             '-t', '-A', '-c',
             f"SELECT token FROM accounts WHERE email='{email}' AND token IS NOT NULL LIMIT 1;"],
            capture_output=True, text=True, timeout=5
        )
        t = r.stdout.strip()
        if t:
            graph_token = t
            emit('info', f'🔑 找到 Graph token，用 API 读取 OTP')
    except Exception as e:
        emit('warn', f'DB token 查询失败: {e}')

    if graph_token:
        headers = {'Authorization': f'Bearer {graph_token}', 'Content-Type': 'application/json'}
        deadline = _time.time() + timeout
        seen_ids: set = set()
        while _time.time() < deadline:
            try:
                url = 'https://graph.microsoft.com/v1.0/me/messages?$top=10&$orderby=receivedDateTime+desc&$select=id,subject,body,from,isRead'
                req = urllib.request.Request(url, headers=headers)
                resp = urllib.request.urlopen(req, timeout=10)
                data = _json.loads(resp.read())
                for msg in data.get('value', []):
                    mid = msg.get('id', '')
                    subj = msg.get('subject', '').lower()
                    frm  = msg.get('from', {}).get('emailAddress', {}).get('address', '').lower()
                    if mid in seen_ids:
                        continue
                    if 'cursor' not in subj and 'cursor' not in frm:
                        continue
                    seen_ids.add(mid)
                    body = msg.get('body', {}).get('content', '')
                    m = _re.search(r'\b(\d{6})\b', body)
                    if m:
                        otp = m.group(1)
                        emit('info', f'✅ Graph API 收到验证码: {otp}')
                        return otp
            except Exception as e:
                emit('warn', f'Graph API 查询失败: {e}')
            _time.sleep(5)
        emit('warn', 'Graph API 超时，回退到浏览器登录...')

    return outlook_web_wait_otp(email, password, timeout=min(120, timeout))

# ─── Outlook.com 浏览器 OTP 读取（替代 IMAP） ──────────────────────────────
async def outlook_web_wait_otp_async(email: str, password: str, timeout: int = 120) -> str | None:
    """通过 Playwright 登录 Outlook.com，轮询收件箱获取 Cursor OTP 验证码。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        from patchright.async_api import async_playwright

    deadline = time.time() + timeout
    import os as _os
    # 检查 DISPLAY（Xvfb 模式）决定是否无头
    _outlook_headless = _os.environ.get("DISPLAY", "") == ""
    async with async_playwright() as pw:
        browser = await pw.firefox.launch(
            headless=_outlook_headless,
        )
        ctx = await browser.new_context(locale="en-US", timezone_id="America/New_York")
        page = await ctx.new_page()
        try:
            emit("info", f"🔐 登录 Outlook.com (headless={_outlook_headless})...")
            await page.goto("https://login.live.com/login.srf?wa=wsignin1.0", timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # 填邮箱 - 等待 input 出现再填
            await page.wait_for_selector("input[type='email'], input[name='loginfmt']", timeout=20000, state="visible")
            await page.fill("input[type='email'], input[name='loginfmt']", email)
            await page.click("input[type='submit'], button[type='submit']")
            await page.wait_for_timeout(2000)

            # 填密码
            try:
                await page.wait_for_selector("input[type='password'], input[name='passwd']", timeout=10000, state="visible")
                await page.fill("input[type='password'], input[name='passwd']", password, timeout=10000)
                await page.click("input[type='submit'], button[type='submit']")
                await page.wait_for_timeout(3000)
            except Exception:
                pass

            # 处理「保持登录」弹窗
            try:
                btn = page.locator("input[value='Yes'],input[value='No'],button:has-text('Yes'),button:has-text('No')")
                if await btn.count() > 0:
                    await btn.first.click()
                    await page.wait_for_timeout(2000)
            except Exception:
                pass

            emit("info", "📬 进入 Outlook 收件箱，等待 Cursor 验证码邮件...")
            await page.goto("https://outlook.live.com/mail/0/inbox", timeout=30000)
            await page.wait_for_timeout(4000)

            seen = set()
            while time.time() < deadline:
                await page.reload()
                await page.wait_for_timeout(3000)
                try:
                    items = page.locator("div[role='option'], [data-convid]")
                    count = await items.count()
                    for i in range(min(count, 15)):
                        item = items.nth(i)
                        conv_id = await item.get_attribute("data-convid") or str(i)
                        item_text = await item.inner_text()
                        if conv_id in seen:
                            continue
                        if not re.search(r'cursor|verification|code|verify', item_text, re.I):
                            continue
                        seen.add(conv_id)
                        await item.click()
                        await page.wait_for_timeout(2000)
                        body = await page.locator("[role='main'], .ReadingPaneContent").inner_text()
                        m = re.search(r'(\d{6})', body)
                        if m:
                            emit("info", f"✅ Outlook 收到验证码: {m.group(1)}")
                            return m.group(1)
                except Exception:
                    pass
                await asyncio.sleep(8)
        finally:
            await browser.close()
    return None


def outlook_web_wait_otp(email: str, password: str, timeout: int = 120) -> str | None:
    """同步包装：在新事件循环中运行 Outlook 浏览器 OTP 轮询。"""
    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(outlook_web_wait_otp_async(email, password, timeout))
    except Exception as e:
        emit("error", f"Outlook web OTP 失败: {e}")
        return None
    finally:
        try:
            loop.close()
        except Exception:
            pass

# ─── [j-cli CDP] 网络拦截：捕获 session token ───────────────────────────────
# 来自 LingoJack/j 的 CDP 思想：拦截浏览器网络响应，直接从 API 响应中提取 token
# j-cli 用 CDP 协议拦截所有 HTTP 响应；这里用 Playwright 的 response 事件实现相同效果

TOKEN_PATTERNS = [
    # Cursor API 返回的 token 字段名
    r'"accessToken"\s*:\s*"([^"]{20,})"',
    r'"access_token"\s*:\s*"([^"]{20,})"',
    r'"token"\s*:\s*"([^"]{20,})"',
    r'"sessionToken"\s*:\s*"([^"]{20,})"',
    r'"session_token"\s*:\s*"([^"]{20,})"',
    r'"jwt"\s*:\s*"([^"]{20,})"',
    r'"idToken"\s*:\s*"([^"]{20,})"',
    r'"id_token"\s*:\s*"([^"]{20,})"',
    r'"WorkosCursorSessionToken"\s*:\s*"([^"]{20,})"',
    r'"cursor_session_token"\s*:\s*"([^"]{20,})"',
]

# Cookie 中的 token 名称
TOKEN_COOKIE_NAMES = [
    "WorkosCursorSessionToken", "cursor_session_token", "session_token",
    "access_token", "auth_token", "__Secure-next-auth.session-token",
]

# 拦截的目标 URL 关键词
AUTH_URL_KEYWORDS = [
    "cursor.sh/api", "cursor.sh/auth", "cursor.sh/token",
    "authenticator.cursor.sh", "api2.cursor.sh",
    "/api/auth", "/oauth/token", "/sign-in", "/sign-up/callback",
    "workos.com/user_management", "workos.com/oauth",
]


def setup_network_intercept(page, session_state: dict):
    """
    [j-cli CDP 思想] 安装网络响应拦截器
    Playwright 的 page.on('response') 等价于 j-cli 的 CDP Network.responseReceived 事件
    捕获认证相关 API 返回的 token
    """
    async def on_response(response):
        try:
            url = response.url
            # 只关注认证相关 URL
            if not any(kw in url for kw in AUTH_URL_KEYWORDS):
                return
            if response.status not in (200, 201):
                return

            # 尝试读取响应体（JSON）
            try:
                body = await response.text()
            except Exception:
                return

            if not body or len(body) < 10:
                return

            # 在响应体中搜索 token
            for pattern in TOKEN_PATTERNS:
                m = re.search(pattern, body)
                if m:
                    token = m.group(1)
                    if len(token) > 20:
                        session_state["token"] = token
                        emit("info", f"🔑 [网络拦截] 捕获到 session token ({len(token)} chars) from {url}")
                        return

            # 尝试解析 JSON，查找嵌套 token
            try:
                data = json.loads(body)
                token = _extract_token_from_json(data)
                if token:
                    session_state["token"] = token
                    emit("info", f"🔑 [网络拦截] JSON 解析捕获到 token ({len(token)} chars) from {url}")
            except Exception:
                pass

        except Exception:
            pass

    page.on("response", on_response)
    emit("info", "🕵️ 网络拦截器已安装（将自动捕获 session token）")


def _extract_token_from_json(data, depth=0) -> str | None:
    """递归在 JSON 中寻找 token 字段"""
    if depth > 5:
        return None
    if isinstance(data, dict):
        for key in ("accessToken", "access_token", "token", "sessionToken",
                    "session_token", "jwt", "idToken", "id_token",
                    "WorkosCursorSessionToken", "cursor_session_token"):
            val = data.get(key)
            if isinstance(val, str) and len(val) > 20:
                return val
        for val in data.values():
            result = _extract_token_from_json(val, depth + 1)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _extract_token_from_json(item, depth + 1)
            if result:
                return result
    return None


async def extract_token_from_cookies(page) -> str | None:
    """从浏览器 Cookie 中提取 token（最终兜底）"""
    try:
        cookies = await page.context.cookies()
        for cookie in cookies:
            if cookie.get("name") in TOKEN_COOKIE_NAMES:
                val = cookie.get("value", "")
                if len(val) > 20:
                    emit("info", f"🍪 从 Cookie 提取 token: {cookie['name']} ({len(val)} chars)")
                    return val
    except Exception:
        pass
    return None


# ─── [j-cli snapshot] 页面快照：动态发现表单字段 ────────────────────────────
# j-cli 的 snapshot() 函数扫描页面的所有可交互元素，为每个元素打上 data-jref 标记
# AI 读取快照后决定点击哪个元素。这里简化为：按语义关键字找输入框

async def page_snapshot(page) -> list[dict]:
    """
    [j-cli snapshot 思想] 获取页面所有可交互元素快照
    返回: [{ref, tag, type, placeholder, label, name, aria_label}]
    """
    try:
        elements = await page.evaluate("""
            () => Array.from(document.querySelectorAll(
                'input, button, [role="button"], textarea, select'
            )).slice(0, 60).map((el, i) => {
                const ref = 'r' + i;
                el.setAttribute('data-jref', ref);
                // 找关联 label
                let label = '';
                if (el.id) {
                    const lbl = document.querySelector(`label[for="${el.id}"]`);
                    if (lbl) label = lbl.textContent.trim();
                }
                if (!label) {
                    const parent = el.closest('label, [class*="field"], [class*="form"]');
                    if (parent) label = parent.textContent.replace(el.value || '', '').trim().slice(0, 60);
                }
                return {
                    ref,
                    selector: `[data-jref="${ref}"]`,
                    tag: el.tagName.toLowerCase(),
                    type: el.type || null,
                    name: el.name || null,
                    placeholder: el.placeholder || null,
                    aria_label: el.getAttribute('aria-label') || null,
                    label: label,
                    text: el.textContent?.trim().slice(0, 50) || null,
                    visible: el.offsetParent !== null,
                };
            })
        """)
        return elements
    except Exception:
        return []



# ── [Firefox ETP 修复] Turnstile token 等待器 ─────────────────────────────────
async def wait_for_turnstile_token(page, timeout_ms: int = 35000):
    emit('info', f'ETP已禁用，等待 Turnstile token (最多 {timeout_ms}ms)...')
    # 方法1: 等待 cf-turnstile-response 被填入
    try:
        await page.wait_for_function(
            r"""() => {
                const inp = document.querySelector('input[name="cf-turnstile-response"]');
                return inp && inp.value && inp.value.length > 10;
            }""",
            timeout=timeout_ms,
        )
        token = await page.evaluate(
            "document.querySelector('[name=\"cf-turnstile-response\"]').value"
        )
        if token and len(token) > 10:
            emit('info', f'Turnstile token OK ({len(token)} chars)')
            return token
    except Exception:
        pass

    # 方法2: 扫描所有 iframe 找 turnstile response
    try:
        for frame in page.frames:
            try:
                val = await frame.evaluate(
                    "document.querySelector('[name=\"cf-turnstile-response\"]')?.value || ''"
                )
                if val and len(val) > 10:
                    emit('info', f'Turnstile from iframe ({len(val)} chars)')
                    return val
            except Exception:
                pass
    except Exception:
        pass

    # 方法3: 用 window.turnstile.getResponse() API
    try:
        captured = await page.evaluate(r"""() => {
            if (typeof window.turnstile === 'undefined') return null;
            const widgets = document.querySelectorAll('[id^="cf-chl-widget"]');
            for (const w of widgets) {
                try {
                    const r = window.turnstile.getResponse(w.id);
                    if (r && r.length > 10) return r;
                } catch(e) {}
            }
            return null;
        }""")
        if captured and len(captured) > 10:
            emit('info', f'Turnstile via getResponse() ({len(captured)} chars)')
            return captured
    except Exception:
        pass

    emit('warn', 'Turnstile token 超时，将尝试直接提交')
    return None

async def find_input_smart(page, *keywords) -> str | None:
    """
    [j-cli snapshot 思想] 通过关键字语义搜索表单字段
    比硬编码 CSS 更鲁棒 — 即使 Cursor 改版也能找到
    返回 Playwright 兼容的 CSS selector
    """
    snapshot = await page_snapshot(page)
    kws = [k.lower() for k in keywords]

    for el in snapshot:
        if not el.get("visible"):
            continue
        # 把所有文本属性合并搜索
        haystack = " ".join(filter(None, [
            el.get("placeholder", ""), el.get("label", ""),
            el.get("aria_label", ""), el.get("name", ""), el.get("type", "")
        ])).lower()
        if any(kw in haystack for kw in kws):
            return el["selector"]

    return None


# ─── 主注册函数 ──────────────────────────────────────────────────────────────
async def register_one(proxy: str, headless: bool = True, provided_email: str = "", provided_email_password: str = "") -> dict | None:
    """
    注册单个 Cursor 账号
    [SheepKing AgentSessionPool] session_state 隔离每个注册任务的状态
    """
    start = time.time()

    # SheepKing 思想：每个任务有独立的 session 状态容器
    session_state = {
        "token": None,       # 网络拦截捕获的 session token
        "email": None,
        "password": None,
        "name": None,
    }

    first_name, last_name = gen_name()
    password = gen_password()

    mail_token = None
    if provided_email:
        email = provided_email
        emit("info", f"📧 使用 Outlook 邮箱: {email}")
    else:
        try:
            email, _epw, mail_token = mailtm_create()
        except Exception as e:
            emit("error", f"❌ 临时邮箱创建失败: {e}")
            return None

    session_state.update({"email": email, "password": password, "name": f"{first_name} {last_name}"})
    emit("info", f"👤 {first_name} {last_name} | 📧 {email}")
    emit("info", "🌐 启动浏览器 → cursor.sh signup...")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        try:
            from patchright.async_api import async_playwright
        except ImportError:
            emit("error", "❌ 未安装 playwright/patchright")
            return None

    proxy_cfg = None
    if proxy:
        p = urlparse(proxy)
        proxy_cfg = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
        if p.username:
            proxy_cfg["username"] = p.username
            proxy_cfg["password"] = p.password or ""

    async with async_playwright() as pw:
        launch_opts = {
            "headless": headless,
        }
        if proxy_cfg:
            launch_opts["proxy"] = proxy_cfg

        # ── 切换至 patchright Chromium（彻底解决 Turnstile 问题）────────────────
        # Firefox ETP 会拦截 challenges.cloudflare.com，导致 cf-turnstile-response 永远为空
        # patchright 内置反检测 + 与 Outlook 共享同一套指纹档案 → WorkOS Radar 信任度更高
        fp = gen_profile(locale="en-US")
        emit("info", f"🖥️  浏览器指纹: {profile_summary(fp)}")
        browser = await pw.chromium.launch(**launch_opts)
        ctx = await browser.new_context(**context_kwargs(fp))
        # 注入完整指纹伪装：canvas / WebGL1+2 / Audio / Battery / MachineID / navigator
        await apply_fingerprint(ctx, fp)
        page = await ctx.new_page()

        # ── [j-cli CDP] 在任何导航之前安装网络拦截器 ──
        setup_network_intercept(page, session_state)

        try:
            # Step 1: 打开注册页面
            await page.goto("https://authenticator.cursor.sh/sign-up", timeout=60000,
                           wait_until="domcontentloaded")
            # 等 CF 挑战通过（最多 30s 轮询，每秒检测一次）
            emit("info", "⏳ 等待 CF 挑战通过（最多90s）…")
            _cf_start = __import__("time").time()
            _cf_passed = False
            while __import__("time").time() - _cf_start < 90:
                _url = page.url
                _title = await page.title()
                if "Just a moment" not in _title and "challenge" not in _url.lower():
                    _cf_passed = True
                    break
                await page.wait_for_timeout(2000)
            if not _cf_passed:
                emit("warn", "⚠️ CF挑战90s未通过，尝试刷新页面...")
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(8000)
            await page.wait_for_timeout(3000)
            _final_title = await page.title()
            emit("info", f"📄 页面加载完成（title: {_final_title}）")
            if "Just a moment" in _final_title:
                emit("error", "❌ CF挑战未通过，跳过此任务")
                await browser.close()
                return None

            # Step 2: 填写姓名（快照方式 + CSS 降级）
            first_sel = await find_input_smart(page, "first", "name")
            if first_sel:
                await page.fill(first_sel, first_name)
                emit("info", f"👤 [快照] 填写名字: {first_name}")
                await page.wait_for_timeout(200)
            else:
                name_input = page.locator("input[name='first_name'], input[placeholder*='first' i], input[name='name']")
                if await name_input.count() > 0:
                    await name_input.first.fill(first_name)

            last_sel = await find_input_smart(page, "last")
            if last_sel:
                await page.fill(last_sel, last_name)
                emit("info", f"👤 [快照] 填写姓氏: {last_name}")
                await page.wait_for_timeout(200)
            else:
                last_input = page.locator("input[name='last_name'], input[placeholder*='last' i]")
                if await last_input.count() > 0:
                    await last_input.first.fill(last_name)

            # Step 3: 填写邮箱（快照方式）
            email_sel = await find_input_smart(page, "email", "mail")
            if email_sel:
                await page.fill(email_sel, email)
                emit("info", f"📧 [快照] 填写邮箱: {email}")
            else:
                email_input = page.locator("input[type='email'], input[name='email']")
                await email_input.first.wait_for(state="visible", timeout=30000)
                await email_input.first.fill(email)
                emit("info", f"📧 [CSS] 填写邮箱: {email}")
            await page.wait_for_timeout(500)

            # Step 4: 提交（先检查 Turnstile，ETP 已禁用故 Turnstile 可正常加载）
            # Turnstile 可能出现在 email step 或 password step
            _ts4_present = await page.evaluate(
                """() => !!document.querySelector('[id^="cf-chl-widget"],[class*="cf-turnstile"],iframe[src*="challenges.cloudflare.com"]')"""
            )
            if _ts4_present:
                emit("info", "🔐 [ETP已禁用] 检测到 Turnstile，等待 token 回调...")
                await wait_for_turnstile_token(page, timeout_ms=40000)
                await page.wait_for_timeout(600)

            continue_btn = page.locator("button[type='submit'], button:has-text('Continue'), button:has-text('Sign up')")
            if await continue_btn.count() > 0:
                await continue_btn.first.click()
            else:
                await page.keyboard.press("Enter")
            emit("info", "⏳ 等待验证码邮件...")
            await page.wait_for_timeout(2000)

            # 并行等待 OTP
            if provided_email and provided_email_password:
                otp = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _get_otp_via_graph_or_browser(provided_email, provided_email_password, timeout=120)
                )
            else:
                otp = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: mailtm_wait_otp(mail_token, timeout=90)
                )
            if not otp:
                emit("error", "❌ 超时未收到验证码邮件")
                await browser.close()
                return None

            # Step 5: 填入 OTP
            await page.wait_for_timeout(1000)
            otp_sel = await find_input_smart(page, "code", "otp", "verification", "one-time")
            if otp_sel:
                await page.fill(otp_sel, otp)
                emit("info", f"🔢 [快照] 填写验证码: {otp}")
                await page.wait_for_timeout(500)
                await page.keyboard.press("Enter")
            else:
                otp_input = page.locator(
                    "input[autocomplete='one-time-code'], input[type='text'][maxlength='6'], input[name='code']"
                )
                if await otp_input.count() > 0:
                    await otp_input.first.fill(otp)
                    await page.wait_for_timeout(500)
                    await otp_input.first.press("Enter")
                else:
                    digit_inputs = page.locator("input[maxlength='1']")
                    if await digit_inputs.count() >= 6:
                        for i, digit in enumerate(otp):
                            await digit_inputs.nth(i).fill(digit)
                            await page.wait_for_timeout(80)
                        await page.keyboard.press("Enter")
                    else:
                        await page.keyboard.type(otp, delay=100)
                        await page.keyboard.press("Enter")
                emit("info", f"🔢 [CSS] 填写验证码: {otp}")

            await page.wait_for_timeout(3000)

            # Step 6: 可能需要填写密码
            pw_sel = await find_input_smart(page, "password")
            if pw_sel:
                await page.fill(pw_sel, password)
                confirm_sel = await find_input_smart(page, "confirm", "repeat")
                if confirm_sel and confirm_sel != pw_sel:
                    await page.fill(confirm_sel, password)
                # ETP 已禁用，等待密码 step 上的 Turnstile（WorkOS password form）
                _pw_ts = await page.evaluate(
                    """() => !!document.querySelector('[id^="cf-chl-widget"],iframe[src*="challenges.cloudflare.com"]')"""
                )
                if _pw_ts:
                    emit("info", "🔐 [ETP已禁用] 密码表单 Turnstile，等待解题...")
                    await wait_for_turnstile_token(page, timeout_ms=40000)
                    await page.wait_for_timeout(600)
                submit_btn = page.locator("button[type='submit']")
                if await submit_btn.count() > 0:
                    await submit_btn.first.click()
                    emit("info", "🔑 [快照] 已设置密码（Turnstile 已通过）")
                await page.wait_for_timeout(2000)

            # Step 7: 等待网络拦截捕获 token（最多额外等 5s）
            for _ in range(10):
                if session_state["token"]:
                    break
                await page.wait_for_timeout(500)

            # 如果网络拦截没捕获到，从 Cookie 兜底
            if not session_state["token"]:
                session_state["token"] = await extract_token_from_cookies(page)

            # Step 8: 判断注册成功
            await page.wait_for_timeout(2000)
            current_url = page.url
            elapsed = round(time.time() - start, 1)

            if "sign-up" not in current_url and "error" not in current_url.lower():
                account = {
                    "email": email,
                    "password": password,
                    "name": f"{first_name} {last_name}",
                    "token": session_state["token"],
                }
                tok_display = f"token({len(session_state['token'])}chars)" if session_state["token"] else "无token"
                emit("success", f"✅ 注册成功 | {email} | {tok_display} | 耗时 {elapsed}s")
                await browser.close()
                return account
            else:
                await page.wait_for_timeout(3000)
                current_url = page.url
                if "sign-up" not in current_url:
                    account = {
                        "email": email,
                        "password": password,
                        "name": f"{first_name} {last_name}",
                        "token": session_state["token"],
                    }
                    emit("success", f"✅ 注册成功 | {email} | 耗时 {elapsed}s")
                    await browser.close()
                    return account
                else:
                    emit("error", f"❌ 注册失败，URL: {current_url}")
                    await browser.close()
                    return None

        except Exception as e:
            emit("error", f"❌ 注册异常: {type(e).__name__}: {e}")
            try:
                await browser.close()
            except Exception:
                pass
            return None


# ─── 主入口 ──────────────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--proxy", type=str, default="")
    parser.add_argument("--headless", type=str, default="true")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--email", type=str, default="", help="使用已有邮箱注册（留空则自动创建 mailtm 邮箱）")
    parser.add_argument("--email-password", type=str, default="", dest="email_password", help="邮箱密码（用于 Outlook.com 登录读取 OTP）")
    args = parser.parse_args()

    headless = args.headless.lower() != "false"
    count = args.count
    success_count = 0
    accounts = []

    # 非无头模式：启动 Xvfb 虚拟显示器（绕过 CF headless 检测）
    _xvfb_proc = None
    if not headless:
        import os, subprocess, time as _t
        display_num = 99
        try:
            _xvfb_proc = subprocess.Popen(
                ["Xvfb", f":{display_num}", "-screen", "0", "1920x1080x24", "-ac"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            _t.sleep(1.5)
            os.environ["DISPLAY"] = f":{display_num}"
            emit("info", f"🖥️  Xvfb 虚拟显示器已启动 (DISPLAY=:{display_num})")
        except FileNotFoundError:
            emit("warn", "⚠️  Xvfb 未找到，回退到无头模式")
            headless = True

    emit("info", f"🚀 Cursor 注册 v2 (CDP拦截+快照检测): {count}个 | 并发: {args.concurrency} | 代理: {args.proxy or '无'} | 无头: {headless}")

    sem = asyncio.Semaphore(args.concurrency)

    async def limited_register():
        async with sem:
            return await register_one(proxy=args.proxy, headless=headless, provided_email=args.email, provided_email_password=args.email_password)

    tasks = [limited_register() for _ in range(count)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, dict) and r:
            accounts.append(r)
            success_count += 1
        elif isinstance(r, Exception):
            emit("error", f"任务异常: {r}")

    # 清理 Xvfb
    if _xvfb_proc is not None:
        try: _xvfb_proc.terminate()
        except: pass

    emit("done", f"注册任务完成 · 成功 {success_count} 个 / 共 {count} 个 {'✅' if success_count else '❌'}")
    if accounts:
        emit("accounts", json.dumps(accounts, ensure_ascii=False))


if __name__ == "__main__":

    asyncio.run(main())
