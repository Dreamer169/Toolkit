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
import urllib.parse
import subprocess
import os

OAUTH_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

try:
    from captcha_solver import TURNSTILE_SITE_KEY, solve_with_fallback
except Exception:
    TURNSTILE_SITE_KEY = "0x4AAAAAAAMNIvC45A4Wjjln"
    solve_with_fallback = None


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

def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _psql_json(sql: str):
    try:
        r = subprocess.run(
            ["psql", "postgresql://postgres:postgres@localhost/toolkit", "-t", "-A", "-c", sql],
            capture_output=True, text=True, timeout=8,
        )
        if r.returncode != 0:
            emit("warn", f"DB 查询失败: {r.stderr.strip()[:160]}")
            return None
        out = r.stdout.strip()
        if not out:
            return None
        return json.loads(out)
    except Exception as e:
        emit("warn", f"DB 查询异常: {e}")
        return None


def pick_outlook_accounts(limit: int) -> list[dict]:
    """
    选取可用于 Replit 注册的 Outlook 账号。
    过滤条件：
      1. 未标记 replit_used（已知 Replit 侧邮箱已存在）
      2. 未标记 token_invalid / inbox_error / abuse_mode
      3. 数据库中不存在同邮箱的 replit 账号（NOT EXISTS）
      4. 有 refresh_token（用于邮箱验证读取）
    """
    sql = f"""
        SELECT COALESCE(json_agg(row_to_json(t)), '[]'::json)
        FROM (
            SELECT id, email, password, refresh_token
            FROM accounts
            WHERE platform='outlook'
              AND status='active'
              AND COALESCE(email,'') <> ''
              AND COALESCE(password,'') <> ''
              AND refresh_token IS NOT NULL
              AND COALESCE(refresh_token,'') <> ''
              AND COALESCE(tags,'') NOT LIKE '%replit_used%'
              AND COALESCE(tags,'') NOT LIKE '%token_invalid%'
              AND COALESCE(tags,'') NOT LIKE '%inbox_error%'
              AND COALESCE(tags,'') NOT LIKE '%abuse_mode%'
              AND NOT EXISTS (
                SELECT 1 FROM accounts r
                WHERE r.platform = 'replit'
                  AND r.email = accounts.email
              )
            ORDER BY
              CASE WHEN COALESCE(tags,'') LIKE '%inbox_verified%' THEN 0 ELSE 1 END,
              updated_at DESC NULLS LAST, id DESC
            LIMIT {max(1, int(limit))}
        ) t;
    """
    rows = _psql_json(sql)
    return rows if isinstance(rows, list) else []


def _get_outlook_tokens(email: str) -> dict:
    # 优先取有 refresh_token 的行，其次有 token 的行，最后取最新行
    sql = f"""
        SELECT row_to_json(t)
        FROM (
            SELECT id, email, token, refresh_token
            FROM accounts
            WHERE platform='outlook' AND lower(email)=lower({_sql_quote(email)})
            ORDER BY
                CASE WHEN refresh_token IS NOT NULL AND refresh_token != \'\' THEN 0
                     WHEN token IS NOT NULL AND token != \'\' THEN 1
                     ELSE 2 END ASC,
                id DESC
            LIMIT 1
        ) t;
    """
    row = _psql_json(sql)
    return row if isinstance(row, dict) else {}


def _update_outlook_tokens(account_id: int, access_token: str, refresh_token: str):
    sql = (
        "UPDATE accounts SET token=" + _sql_quote(access_token) +
        ", refresh_token=" + _sql_quote(refresh_token) +
        ", updated_at=NOW() WHERE id=" + str(int(account_id)) + ";"
    )
    subprocess.run(["psql", "postgresql://postgres:postgres@localhost/toolkit", "-q", "-c", sql], capture_output=True, text=True, timeout=8)


def _refresh_outlook_access_token(account: dict) -> str | None:
    refresh_token = account.get("refresh_token") or ""
    if not refresh_token:
        return account.get("token") or None
    try:
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": OAUTH_CLIENT_ID,
            "refresh_token": refresh_token,
            "scope": "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read offline_access",
        }).encode()
        req = urllib.request.Request(
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        access_token = resp.get("access_token")
        if access_token:
            _update_outlook_tokens(int(account["id"]), access_token, resp.get("refresh_token") or refresh_token)
            emit("info", "🔄 Outlook token 已刷新")
            return access_token
    except Exception as e:
        emit("warn", f"Outlook token 刷新失败，尝试旧 token: {e}")
    return account.get("token") or None


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
    import json as _json, re as _re, time as _time

    graph_token = None
    account = _get_outlook_tokens(email)
    if account:
        graph_token = _refresh_outlook_access_token(account)
        if graph_token:
            emit('info', '🔑 使用 Outlook Graph API 读取 OTP')

    if graph_token:
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            try:
                headers = {'Authorization': f'Bearer {graph_token}', 'Content-Type': 'application/json'}
                url = 'https://graph.microsoft.com/v1.0/me/messages?$top=25&$orderby=receivedDateTime+desc&$select=id,subject,body,bodyPreview,from,receivedDateTime'
                req = urllib.request.Request(url, headers=headers)
                resp = urllib.request.urlopen(req, timeout=10)
                data = _json.loads(resp.read())
                for msg in data.get('value', []):
                    subj = (msg.get('subject') or '').lower()
                    frm = (msg.get('from') or {}).get('emailAddress', {}).get('address', '').lower()
                    body = ((msg.get('body') or {}).get('content') or '') + ' ' + (msg.get('bodyPreview') or '')
                    if 'cursor' not in subj and 'cursor' not in frm and 'cursor' not in body.lower():
                        continue
                    m = _re.search(r'\b(\d{6})\b', body)
                    if m:
                        otp = m.group(1)
                        emit('info', f'✅ Outlook Graph 收到验证码: {otp}')
                        return otp
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors='ignore')[:200]
                emit('warn', f'Graph API 查询失败: HTTP {e.code} {detail}')
            except Exception as e:
                emit('warn', f'Graph API 查询失败: {e}')
            _time.sleep(5)
        emit('warn', 'Graph API 超时，回退到浏览器登录...')

    return outlook_web_wait_otp(email, password, timeout=min(120, timeout))

# ─── Outlook.com 浏览器 OTP 读取（替代 IMAP） ──────────────────────────────
async def outlook_web_wait_otp_async(email: str, password: str, timeout: int = 120) -> str | None:
    """通过 Playwright 登录 Outlook.com，轮询收件箱获取 Cursor OTP 验证码。"""
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        from playwright.async_api import async_playwright

    deadline = time.time() + timeout
    import os as _os
    _outlook_headless = _os.environ.get("DISPLAY", "") == ""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=_outlook_headless)
        fp2 = gen_profile(locale="en-US")
        ctx = await browser.new_context(**context_kwargs(fp2))
        await apply_fingerprint(ctx, fp2)
        page = await ctx.new_page()
        try:
            emit("info", f"🔐 登录 Outlook.com (headless={_outlook_headless})...")
            # 尝试多个 Outlook 登录 URL（live.com 可能重定向到 account.microsoft.com）
            for _login_url in [
                "https://login.live.com/login.srf?wa=wsignin1.0",
                "https://account.microsoft.com/",
                "https://outlook.live.com/owa/",
            ]:
                try:
                    await page.goto(_login_url, timeout=40000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(3000)
                    _login_found = await page.locator(
                        "input[type='email'], input[name='loginfmt'], input[name='email'], "                        "input[placeholder*='email' i], input[placeholder*='mail' i]"
                    ).count()
                    if _login_found:
                        break
                except Exception:
                    continue

            await page.wait_for_timeout(2000)
            # 填邮箱 - 等待 input 出现再填（更宽松选择器 + 更长超时）
            _email_loc = page.locator(
                "input[type='email'], input[name='loginfmt'], input[name='email'], "                "input[placeholder*='email' i], input[placeholder*='mail' i]"
            )
            await _email_loc.first.wait_for(state="visible", timeout=30000)
            await _email_loc.first.fill(email)
            await page.click("input[type='submit'], button[type='submit'], button:has-text('Next'), button:has-text('Sign in')")
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
                        m = re.search(r'\b(\d{6})\b', body)
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



async def inject_turnstile_token(page, token: str) -> bool:
    if not token:
        return False
    try:
        result = await page.evaluate(r"""(token) => {
            const setValue = (el, value) => {
                const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) desc.set.call(el, value);
                else el.value = value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            };
            const ensureField = (name) => {
                let el = document.querySelector('input[name="' + name + '"], textarea[name="' + name + '"]');
                if (!el) {
                    el = document.createElement('input');
                    el.type = 'hidden';
                    el.name = name;
                    const form = document.querySelector('form');
                    (form || document.body || document.documentElement).appendChild(el);
                }
                setValue(el, token);
                return !!el.value;
            };
            return {
                signals: ensureField('signals'),
                turnstile: ensureField('cf-turnstile-response'),
            };
        }""", token)
        emit('info', f"✅ 已注入 Turnstile token 到 signals/cf-turnstile-response: {result}")
        return bool(result and (result.get('signals') or result.get('turnstile')))
    except Exception as e:
        emit('warn', f"Turnstile token 注入失败: {e}")
        return False


async def is_otp_stage(page) -> bool:
    try:
        return bool(await page.evaluate(r"""() => {
            const url = new URL(window.location.href);
            const path = url.pathname.toLowerCase();
            if (path.includes('email-verification') || path.includes('one-time') || path.includes('/otp') || path.includes('/code')) return true;
            const hasOtpInput = !!(
                document.querySelector('input[autocomplete="one-time-code"]') ||
                document.querySelector('input[name="code"]') ||
                document.querySelector('input[maxlength="6"]') ||
                document.querySelector('input[inputmode="numeric"]')
            );
            if (hasOtpInput) return true;
            const text = (document.body?.innerText || '').toLowerCase();
            return /verification code|one-time code|check your email|enter the code|sent.*email|6-digit/.test(text);
        }"""))
    except Exception:
        return False


# ── [Firefox ETP 修复] Turnstile token 等待器 ─────────────────────────────────
async def wait_for_turnstile_token(page, timeout_ms: int = 35000, captcha_providers: list | None = None):
    emit('info', f'ETP已禁用，等待 Turnstile token (最多 {timeout_ms}ms)...')
    # 方法1: 等待 signals 或 cf-turnstile-response 被填入（WorkOS 用 signals）
    try:
        await page.wait_for_function(
            r"""() => {
                const sig = document.querySelector('input[name="signals"]');
                if (sig && sig.value && sig.value.length > 10) return true;
                const inp = document.querySelector('input[name="cf-turnstile-response"]');
                return inp && inp.value && inp.value.length > 10;
            }""",
            timeout=timeout_ms,
        )
        token = await page.evaluate("""() => {
            const s = document.querySelector('[name="signals"]');
            if (s && s.value && s.value.length > 10) return s.value;
            const c = document.querySelector('[name="cf-turnstile-response"]');
            return c ? c.value : '';
        }""")
        if token and len(token) > 10:
            emit('info', f'✅ Turnstile/signals token OK ({len(token)} chars)')
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


    if captcha_providers:
        if solve_with_fallback is None:
            emit('warn', '打码模块不可用，无法调用 Turnstile 外部解题')
        else:
            try:
                emit('info', f'🔑 调用打码服务解 Turnstile sitekey={TURNSTILE_SITE_KEY}')
                solved = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: solve_with_fallback(
                        captcha_providers,
                        'turnstile',
                        page_url=page.url,
                        site_key=TURNSTILE_SITE_KEY,
                    )
                )
                if solved and len(solved) > 10:
                    ok = await inject_turnstile_token(page, solved)
                    if ok:
                        return solved
            except Exception as e:
                emit('warn', f'Turnstile 打码失败: {e}')

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

# ── Firefox 端口扫描：找 CF 直接放行的 socks5 端口 ──────────────────────────
async def scan_working_proxy_port(ports=None):
    """Quick-scan xray socks5 ports; return first where Firefox loads cursor.sh title='Sign up'."""
    if ports is None:
        ports = [10808, 10820, 10821, 10822, 10823, 10824, 10825, 10826, 10827, 10828, 10829, 10830]
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None
    emit("info", f"🔍 扫描可用代理端口 (Firefox)...")
    ff_prefs = _firefox_etp_prefs()
    async with async_playwright() as pw:
        for port in ports:
            try:
                proxy_cfg = {"server": f"socks5://127.0.0.1:{port}"}
                browser = await pw.firefox.launch(
                    headless=True,
                    proxy=proxy_cfg,
                    firefox_user_prefs=ff_prefs,
                    timeout=8000,
                )
                page = await browser.new_page()
                try:
                    await page.goto("https://authenticator.cursor.sh/sign-up",
                                    timeout=12000, wait_until="domcontentloaded")
                    title = await page.evaluate("document.title")
                    if "Just a moment" not in title and "Attention" not in title:
                        emit("info", f"✅ 端口 {port} 畅通 (title={title!r})")
                        await browser.close()
                        return port
                    emit("info", f"  Port {port}: CF 拦截 ({title!r})")
                except Exception as e:
                    emit("info", f"  Port {port}: 超时/错误 ({e})")
                finally:
                    await browser.close()
            except Exception:
                pass
    emit("warn", "⚠️ 未找到畅通端口，使用默认 10820")
    return 10820


def _firefox_etp_prefs() -> dict:
    """Return Firefox user prefs that disable ETP AND enable WebGL (Turnstile needs WebGL)."""
    return {
        # ── 禁用增强跟踪保护，允许 challenges.cloudflare.com ───────────────────
        "privacy.trackingprotection.enabled": False,
        "privacy.trackingprotection.pbmode.enabled": False,
        "privacy.trackingprotection.cryptomining.enabled": False,
        "privacy.trackingprotection.fingerprinting.enabled": False,
        "network.cookie.cookieBehavior": 0,
        "browser.contentblocking.category": "standard",
        "privacy.annotate_channels.strict_list.enabled": False,
        "privacy.partition.network_state": False,
        "privacy.resistFingerprinting": False,
        "dom.security.https_only_mode": False,
        # ── 强制启用 WebGL（Turnstile 用 WebGL 完成挑战，headless 默认关闭）──
        "webgl.disabled": False,
        "webgl.force-enabled": True,
        "webgl.enable-webgl2": True,
        "webgl.dxgl.enabled": False,
        "layers.acceleration.force-enabled": True,
        "layers.offmainthreadcomposition.enabled": True,
        "gfx.canvas.azure.backends": "skia",
        "media.hardware-video-decoding.force-enabled": False,
        # ── Bounce Tracker 保护（Firefox 会把 cursor.com 列为 bounce tracker 并阻止重定向）
        "privacy.bounceTrackingProtection.enabled": False,
        "privacy.bounceTrackingProtection.mode": 0,
        "privacy.purge_trackers.enabled": False,
        "network.http.tailing.enabled": False,
        # ── 允许 iframe 跨域通信（Turnstile iframe 需要 postMessage）──────────
        "security.fileuri.strict_origin_policy": False,
        "dom.ipc.processCount": 1,
        # ── 减少噪音警告 ─────────────────────────────────────────────────────
        "browser.send_pings": True,
        "media.autoplay.default": 0,
    }

async def register_one(proxy: str, headless: bool = True, provided_email: str = "", provided_email_password: str = "", captcha_service: str = "", captcha_key: str = "", cdp_url: str = "", user_data_dir: str = "") -> dict | None:
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
    captcha_providers = []
    if captcha_service and captcha_key:
        captcha_providers.append((captcha_service, captcha_key))
        emit("info", f"🔑 Cursor Turnstile 打码服务已启用: {captcha_service}")
    emit("info", "🌐 启动浏览器 → cursor.sh signup...")

    # Firefox路径用普通playwright（patchright Firefox有_client bug）
    # 通过 add_init_script 手动 patch navigator.webdriver 让 WorkOS 信任
    if cdp_url or user_data_dir:
        try:
            from patchright.async_api import async_playwright
        except ImportError:
            from playwright.async_api import async_playwright
    else:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            emit("error", "❌ 未安装 playwright")
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

        # ── Firefox + ETP disabled → Turnstile 正常运行 → signals 自动填充 ────────
        # 结论：Chromium 在 CF WAF 永久卡住；Firefox 直接通过 CF + 禁用 ETP 后
        # challenges.cloudflare.com 不再被拦截，Turnstile 自动解题填充 signals 字段。
        ff_prefs = _firefox_etp_prefs()
        if cdp_url:
            emit("info", f"🔌 使用外部真实浏览器 CDP: {cdp_url}")
            browser = await pw.chromium.connect_over_cdp(cdp_url)
            if browser.contexts:
                ctx = browser.contexts[0]
            else:
                fp = gen_profile(locale="en-US")
                ctx = await browser.new_context(**context_kwargs(fp))
            await apply_fingerprint(ctx, gen_profile(locale="en-US"))
        elif user_data_dir:
            emit("info", f"🗂️ 使用持久化 Chrome Profile: {user_data_dir}")
            fp = gen_profile(locale="en-US")
            ctx = await pw.chromium.launch_persistent_context(user_data_dir, **launch_opts, **context_kwargs(fp))
            browser = ctx
            await apply_fingerprint(ctx, fp)
        else:
            # 自动扫描可用端口（如果 proxy 由 use_xray 参数传入）
            if not proxy and not cdp_url and not user_data_dir:
                _best_port = await scan_working_proxy_port()
                if _best_port:
                    emit("info", f"🔌 自动选择代理端口: {_best_port}")
                    launch_opts["proxy"] = {"server": f"socks5://127.0.0.1:{_best_port}"}
            elif proxy and "10808" in proxy:
                # 如果用户传了默认端口，尝试找更好的
                _best_port = await scan_working_proxy_port()
                if _best_port and _best_port != 10808:
                    emit("info", f"🔌 升级代理端口 10808→{_best_port}")
                    launch_opts["proxy"] = {"server": f"socks5://127.0.0.1:{_best_port}"}
            launch_opts["firefox_user_prefs"] = ff_prefs
            # Mesa 软件渲染 = Turnstile WebGL 可以在无 GPU 的 headless 环境中运行
            launch_opts["env"] = {
                "LIBGL_ALWAYS_SOFTWARE": "1",
                "GALLIUM_DRIVER": "softpipe",
                "MOZ_WEBRENDER": "0",
                "MOZ_ACCELERATED": "0",
            }
            emit("info", "🦊 启动 Firefox (ETP禁用+软件WebGL+Bounce Tracker禁用)...")
            browser = await pw.firefox.launch(**launch_opts)
            ctx = await browser.new_context(
                locale="en-US",
                timezone_id="America/New_York",
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
            )
            # 注入 webdriver patch：让 WorkOS/Turnstile 不认为这是机器人
            await ctx.add_init_script("""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined, configurable: true});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5], configurable: true});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en'], configurable: true});
window.chrome = {runtime: {}};
""")
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
                _title = await page.evaluate("document.title")
                if "Just a moment" not in _title and "challenge" not in _url.lower():
                    _cf_passed = True
                    break
                await page.wait_for_timeout(2000)
            if not _cf_passed:
                emit("warn", "⚠️ CF挑战90s未通过，尝试刷新页面...")
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(8000)
            await page.wait_for_timeout(3000)
            _final_title = await page.evaluate("document.title")
            emit("info", f"📄 页面加载完成（title: {_final_title}）")
            if "Just a moment" in _final_title:
                emit("error", "❌ CF挑战未通过，跳过此任务")
                await browser.close()
                return None

            # ── ⚡ email-verification 快速通道检测 ──────────────────────────────
            # Cursor 有时直接把已填邮箱账号送到 OTP 页，跳过 name/email 填写步骤
            _cur_url_pre = page.url
            if "email-verification" in _cur_url_pre:
                emit("info", f"⚡ 检测到 email-verification 快速通道，跳过表单填写直接等 OTP...")
                # 直接跳到 OTP 收取
                if provided_email and provided_email_password:
                    otp = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: _get_otp_via_graph_or_browser(provided_email, provided_email_password, timeout=120)
                    )
                else:
                    otp = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: mailtm_wait_otp(mail_token, timeout=90)
                    )
                if not otp:
                    emit("error", "❌ [快速通道] 超时未收到验证码邮件")
                    await browser.close()
                    return None
                emit("info", f"🔢 [快速通道] 填写验证码: {otp}")
                otp_sel = await find_input_smart(page, "code", "otp", "verification", "one-time")
                if otp_sel:
                    await page.fill(otp_sel, otp)
                    await page.wait_for_timeout(400)
                    await page.keyboard.press("Enter")
                else:
                    await page.keyboard.type(otp, delay=80)
                    await page.keyboard.press("Enter")
                await page.wait_for_timeout(4000)
                # 等待 token 捕获
                for _ in range(20):
                    if session_state["token"]:
                        break
                    await page.wait_for_timeout(500)
                if not session_state["token"]:
                    session_state["token"] = await extract_token_from_cookies(page)
                elapsed = round(__import__("time").time() - start, 1)
                tok_display = f"token({len(session_state['token'])}chars)" if session_state["token"] else "无token"
                emit("success", f"✅ [快速通道] 注册成功 | {email} | {tok_display} | 耗时 {elapsed}s")
                await browser.close()
                return {
                    "email": email,
                    "password": password,
                    "name": f"{first_name} {last_name}",
                    "token": session_state["token"],
                }
            # ── 快速通道检测结束 ────────────────────────────────────────────────

            # Step 2: 填写姓名（快照方式 + CSS 降级）
            # Firefox+React: pressSequentially 触发 onChange 事件（fill 不触发 React）
            async def _fill_react(selector_or_loc, value, label):
                """Click → clear → pressSequentially → blur → tab，确保 React onChange 触发。"""
                try:
                    if isinstance(selector_or_loc, str):
                        loc = page.locator(selector_or_loc)
                    else:
                        loc = selector_or_loc
                    await loc.first.wait_for(state="visible", timeout=8000)
                    await loc.first.click()
                    await page.keyboard.press("Control+a")
                    await page.keyboard.press("Delete")
                    await loc.first.press_sequentially(value, delay=55)
                    await page.wait_for_timeout(150)
                    emit("info", f"⌨️  {label}: {value}")
                except Exception as _fe:
                    emit("warn", f"pressSequentially fallback fill ({label}): {_fe}")
                    try:
                        await page.fill(selector_or_loc if isinstance(selector_or_loc, str) else "input", value)
                    except Exception:
                        pass

            first_sel = await find_input_smart(page, "first", "name")
            if first_sel:
                await _fill_react(first_sel, first_name, "名字")
            else:
                await _fill_react(
                    page.locator("input[name='first_name'],input[placeholder*='first' i],input[name='name']"),
                    first_name, "名字"
                )

            last_sel = await find_input_smart(page, "last")
            if last_sel:
                await _fill_react(last_sel, last_name, "姓氏")
            else:
                await _fill_react(
                    page.locator("input[name='last_name'],input[placeholder*='last' i]"),
                    last_name, "姓氏"
                )

            # Step 3: 填写邮箱
            email_sel = await find_input_smart(page, "email", "mail")
            if email_sel:
                await _fill_react(email_sel, email, "邮箱")
            else:
                email_loc = page.locator("input[type='email'],input[name='email']")
                await email_loc.first.wait_for(state="visible", timeout=30000)
                await _fill_react(email_loc, email, "邮箱")
            await page.wait_for_timeout(500)

            # 鼠标随机移动帮助 WorkOS signals 积累更多信号
            import random as _rand
            for _mi in range(4):
                await page.mouse.move(_rand.randint(200, 900), _rand.randint(100, 600))
                await page.wait_for_timeout(_rand.randint(100, 250))
            await page.wait_for_timeout(_rand.randint(400, 800))

            # Step 4: 提交前等待 Turnstile 自动加载并解题
            # 诊断发现：iframe src= 是 Turnstile 占位，JS 延迟填充真实 src
            # 必须等 iframe.src 变成 challenges.cloudflare.com 后 patchright 才能自动解题
            emit("info", "⏳ 等待 Turnstile 加载（最多 45s）...")
            _ts_loaded = False
            for _tl in range(45):
                _ts_state = await page.evaluate("""() => {
                    // 检查 signals/cf-turnstile-response 已填充
                    const sig = document.querySelector('[name="signals"]');
                    if (sig && sig.value && sig.value.length > 10) return 'ALREADY_SOLVED';
                    const resp = document.querySelector('[name="cf-turnstile-response"]');
                    if (resp && resp.value && resp.value.length > 10) return 'ALREADY_SOLVED';
                    // 检查 Turnstile widget（多种检测方式）
                    if (document.querySelector('[id^="cf-chl-widget"],[class*="cf-turnstile"],[data-sitekey]')) return 'WIDGET_PRESENT';
                    const iframes = document.querySelectorAll('iframe');
                    for (const f of iframes) {
                        if (f.src && (f.src.includes('challenges.cloudflare.com') || f.src.includes('turnstile'))) return 'WIDGET_PRESENT';
                    }
                    return '';
                }""")
                if _ts_state == 'ALREADY_SOLVED':
                    emit("info", "✅ Turnstile/signals 已自动解题")
                    _ts_loaded = True
                    break
                if _ts_state == 'WIDGET_PRESENT':
                    emit("info", "🔐 Turnstile widget 已就绪，等待自动解题...")
                    await wait_for_turnstile_token(page, timeout_ms=60000, captcha_providers=captcha_providers)
                    emit("info", "✅ Turnstile 解题完成")
                    _ts_loaded = True
                    await page.wait_for_timeout(500)
                    break
                await page.wait_for_timeout(1000)
            if not _ts_loaded:
                emit("warn", "⚠️ Turnstile 45s 未出现，尝试直接提交（可能无 Turnstile 保护）")

            _url_before_submit = page.url
            continue_btn = page.locator("button[type='submit'], button:has-text('Continue'), button:has-text('Sign up')")
            if await continue_btn.count() > 0:
                await continue_btn.first.click()
                emit("info", "🖱️ Continue 已点击，等待页面响应...")
            else:
                await page.keyboard.press("Enter")
                emit("info", "⌨️ Enter 已按下，等待页面响应...")

            # 等待最多 60s：URL 变化 OR Turnstile 出现 OR password/OTP form 出现
            _submit_ok = False
            for _si in range(60):
                await page.wait_for_timeout(1000)
                _url_now = page.url
                # URL 变了就是成功推进
                if _url_now != _url_before_submit:
                    emit("info", f"✅ URL 已推进: {_url_now[:80]}")
                    _submit_ok = True
                    break
                # 检测所有形式的 Turnstile（包括不可见的 0px iframe）
                _ts4_late = await page.evaluate("""() => {
                    const iframes = document.querySelectorAll('iframe');
                    for (const f of iframes) {
                        if ((f.src || '').includes('challenges.cloudflare.com') ||
                            (f.src || '').includes('turnstile')) return true;
                    }
                    return !!document.querySelector('[id^="cf-chl-widget"],[class*="cf-turnstile"],[name="cf-turnstile-response"]');
                }""")
                if _ts4_late:
                    emit("info", "🔐 [延迟] 检测到 Turnstile（点击后出现），等待 patchright 自动解题...")
                    await wait_for_turnstile_token(page, timeout_ms=60000, captcha_providers=captcha_providers)
                    await page.wait_for_timeout(800)
                    # Turnstile 解完后再 click
                    _btn2 = page.locator("button[type='submit']")
                    if await _btn2.count() > 0:
                        await _btn2.first.click()
                    for _si2 in range(15):
                        await page.wait_for_timeout(1000)
                        if page.url != _url_before_submit:
                            emit("info", f"✅ Turnstile 解题后 URL 推进: {page.url[:80]}")
                            _submit_ok = True
                            break
                    break
                # 检测 password 或 OTP 字段出现（URL 可能没变但表单已换）
                _pw_or_otp = await page.evaluate(
                    """() => !!document.querySelector('input[type="password"],input[autocomplete="one-time-code"],input[maxlength="6"]')"""
                )
                if _pw_or_otp:
                    emit("info", "✅ 检测到密码/OTP 字段，表单已推进")
                    _submit_ok = True
                    break
                # 检测错误消息（该邮箱已注册等）
                _err_txt = await page.evaluate("""() => {
                    const alerts = document.querySelectorAll('[role="alert"],[class*="error"],[class*="Error"]');
                    for (const a of alerts) {
                        if (a.textContent && a.textContent.trim()) return a.textContent.trim().slice(0, 100);
                    }
                    return '';
                }""")
                if _err_txt and _si > 3:
                    emit("warn", f"⚠️ 页面错误消息: {_err_txt}")
                    _submit_ok = True  # 有错误也继续，后面步骤会检测
                    break
            if not _submit_ok:
                emit("warn", "⚠️ 提交后 60s 内页面未推进，强制继续...")

            # ── Step 4.5: 检测密码表单（Cursor 可能先要求设密码再发 OTP）──────────
            _pw_pre_sel = await find_input_smart(page, "password")
            if _pw_pre_sel:
                emit("info", "🔑 [Step 4.5] 检测到密码表单（在 OTP 前），先提交密码...")
                await page.fill(_pw_pre_sel, password)
                _confirm_pre = await find_input_smart(page, "confirm", "repeat")
                if _confirm_pre and _confirm_pre != _pw_pre_sel:
                    await page.fill(_confirm_pre, password)
                _ts_pw_pre = await page.evaluate(
                    """() => !!document.querySelector('[id^="cf-chl-widget"],iframe[src*="challenges.cloudflare.com"],[name="signals"],[name="cf-turnstile-response"]')"""
                )
                if _ts_pw_pre:
                    emit("info", "🔐 [Step 4.5] 密码表单 Turnstile，等待解题...")
                    await wait_for_turnstile_token(page, timeout_ms=40000, captcha_providers=captcha_providers)
                    await page.wait_for_timeout(600)
                _pw_submit = page.locator("button[type='submit']")
                if await _pw_submit.count() > 0:
                    await _pw_submit.first.click()
                    emit("info", "✅ [Step 4.5] 密码已提交，等待 Cursor 发送 OTP...")
                for _otp_wait in range(25):
                    await page.wait_for_timeout(1000)
                    if await is_otp_stage(page):
                        break
            _url_post_submit = page.url
            _is_otp_stage = await is_otp_stage(page)
            if not _is_otp_stage:
                _sig_len = 0
                try:
                    _sig_len = await page.evaluate("""() => (document.querySelector('[name="signals"]')?.value || '').length""")
                except Exception:
                    pass
                emit("error", f"❌ 未进入 OTP 阶段，停止等待邮件。URL: {_url_post_submit[:120]} | signals_len={_sig_len}")
                if not captcha_providers and not (cdp_url or user_data_dir):
                    emit("warn", "💡 免费方案提示: 当前运行在数据中心浏览器且没有外部真实浏览器/CDP或持久化本地 Profile，WorkOS 可能不会发 OTP")
                await browser.close()
                return None
            emit("info", f"⏳ 已确认进入 OTP 阶段，等待验证码邮件（URL: {_url_post_submit[:120]}）...")
            # ── 结束 Step 4.5 ───────────────────────────────────────────────────

            emit("info", "⏳ 等待验证码邮件...")
            await page.wait_for_timeout(1000)

            # 并行等待 OTP
            _otp_email = provided_email
            _otp_pwd = provided_email_password
            if _otp_email and not _otp_pwd:
                # 从 DB 查密码
                try:
                    _acct_data = _get_outlook_tokens(_otp_email)
                    if _acct_data:
                        _otp_pwd = _acct_data.get('password', '') or ''
                        if _otp_pwd:
                            emit('info', f'🔑 从 DB 获取邮箱密码用于 OTP 读取')
                except Exception as _pe:
                    emit('warn', f'DB 密码查询失败: {_pe}')
            if _otp_email and (_otp_pwd or True):  # Graph API 可无密码（仅需 refresh_token）
                otp = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _get_otp_via_graph_or_browser(_otp_email, _otp_pwd or '', timeout=120)
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
                    await wait_for_turnstile_token(page, timeout_ms=40000, captcha_providers=captcha_providers)
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

            def _is_success_url(u: str) -> bool:
                """
                成功 URL 判断：
                  - 已离开 sign-up 域 → 成功
                  - 还在 sign-up/email-verification 且有 token → 视为成功（OTP 已验证但页面重定向慢）
                  - 纯 sign-up（非 email-verification 子路径）且无 error → 视为成功
                """;
                if "error" in u.lower():
                    return False
                if "sign-up" not in u:
                    return True
                # sign-up/email-verification 子路径 + 已捕获 token → 成功
                if "email-verification" in u and session_state["token"]:
                    return True
                return False

            if _is_success_url(current_url):
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
                # 等候最多 5s，看页面是否会离开 email-verification/sign-up
                for _wait_i in range(5):
                    await page.wait_for_timeout(1000)
                    current_url = page.url
                    if not session_state["token"]:
                        session_state["token"] = await extract_token_from_cookies(page)
                    if _is_success_url(current_url):
                        account = {
                            "email": email,
                            "password": password,
                            "name": f"{first_name} {last_name}",
                            "token": session_state["token"],
                        }
                        emit("success", f"✅ 注册成功 | {email} | 耗时 {round(__import__('time').time()-start,1)}s")
                        await browser.close()
                        return account
                # 最终判断 — 如果仍在 sign-up 且无 token，才真正失败
                if session_state["token"] and "error" not in current_url.lower():
                    account = {
                        "email": email,
                        "password": password,
                        "name": f"{first_name} {last_name}",
                        "token": session_state["token"],
                    }
                    emit("success", f"✅ 注册成功(token兜底) | {email} | 耗时 {round(__import__('time').time()-start,1)}s")
                    await browser.close()
                    return account
                emit("error", f"❌ 注册失败，URL: {current_url} | token: {'有' if session_state['token'] else '无'}")
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
    parser.add_argument("--captcha-service", type=str, default=os.environ.get("CAPTCHA_SERVICE", ""))
    parser.add_argument("--captcha-key", type=str, default=os.environ.get("CAPTCHA_API_KEY", ""))
    parser.add_argument("--cdp-url", type=str, default=os.environ.get("CURSOR_CDP_URL", ""), help="连接外部真实 Chrome，例如 http://127.0.0.1:9222")
    parser.add_argument("--user-data-dir", type=str, default=os.environ.get("CURSOR_USER_DATA_DIR", ""), help="使用持久化 Chrome Profile，复用真实浏览器会话/信任状态")
    args = parser.parse_args()

    headless = args.headless.lower() != "false"
    count = args.count
    success_count = 0
    accounts = []

    # 非无头模式：启动 Xvfb 虚拟显示器（绕过 CF headless 检测）
    _xvfb_proc = None
    if not headless:
        import subprocess, time as _t
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
    if args.cdp_url:
        emit("info", "🆓 免费真实浏览器模式: 连接外部 Chrome CDP，复用本机 IP/设备信任")
    if args.user_data_dir:
        emit("info", "🆓 免费持久 Profile 模式: 复用 Chrome 用户数据目录和历史信任信号")

    outlook_accounts = []
    if not args.email:
        outlook_accounts = pick_outlook_accounts(count)
        if outlook_accounts:
            emit("info", f"📮 将复用已授权 Outlook 邮箱: {len(outlook_accounts)} 个")
        else:
            emit("warn", "未找到已授权 Outlook 邮箱，回退到临时邮箱")

    sem = asyncio.Semaphore(args.concurrency)

    async def limited_register(idx: int):
        async with sem:
            account = outlook_accounts[idx] if idx < len(outlook_accounts) else None
            email = args.email or (account.get("email") if account else "")
            email_password = args.email_password or (account.get("password") if account else "")
            return await register_one(proxy=args.proxy, headless=headless, provided_email=email, provided_email_password=email_password, captcha_service=args.captcha_service, captcha_key=args.captcha_key, cdp_url=args.cdp_url, user_data_dir=args.user_data_dir)

    tasks = [limited_register(i) for i in range(count)]
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
