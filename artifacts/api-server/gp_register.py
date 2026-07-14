#!/usr/bin/env python3
"""
Gp-Register-Tool v3
====================
GPTree 邀请注册工具 — pydoll 版，含完整 Cloudflare Turnstile bypass

turnstile 方案完全移植自 unitool_login.py：
  Phase 1: postMessage 拦截器等待 Invisible 自动 token（30s）
  Phase 2: _bypass_cloudflare() shadow-root checkbox click（3次重试）

流程:
  1. urllib 登录 inviter 账号（不需要真实 turnstile）→ 取 session cookie
  2. POST /api/invites 发送邀请给临时邮箱（session cookie auth）
  3. guerrillamail 等待邀请邮件，提取 invite_token
  4. pydoll 注册浏览器（CF SOCKS 代理轮换）:
       a. 打开邀请链接（设置 referral cookie / invite_token）
       b. 导航到 /register，填写 name/email/password
       c. _inject_pm_hook → _bypass_turnstile (unitool 同款)
       d. 提交注册表单
  5. guerrillamail 等待验证邮件，urllib 点击验证链接
  6. urllib POST /api/auth/login → 提取 Bearer token（API 返回 token 字段）
  7. 保存到 /data/api-server/data.db ai_accounts 表
  8. 更新 /opt/gptree-sillytavern-proxy/.env → pm2 restart gptree-proxy
"""

import argparse
import asyncio
import http.cookiejar
import json
import os
import random
import re
import socket
import sqlite3
import string
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

# ─────────────────────────── 配置 ────────────────────────────────
BASE_URL          = "https://gp-tree.com"
TURNSTILE_SITEKEY = "0x4AAAAAAD0xhfVDgxnJcbfL"

INVITER_EMAIL     = "btaininyby@yanemail.com"
INVITER_PASSWORD  = "@gjI!zR5dSkzak"

PROXY_ENV_PATH    = "/opt/gptree-sillytavern-proxy/.env"
PM2_NAME          = "gptree-proxy"
DB_PATH           = "/data/api-server/data.db"

CHROME = None
for _p in [
    "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
    "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
]:
    if os.path.exists(_p):
        CHROME = _p
        break

# CF SOCKS 代理（经测试 10822/10824/10826 可达 gp-tree.com）
CF_SOCKS_PORTS = [10822, 10824, 10826]
# RESI 代理端口（住宅 IP，Turnstile 友好）
RESI_PORTS = [10851, 10853, 10854, 10857, 10859]

GMAIL_API = "https://www.guerrillamail.com/ajax.php"

LOG_FILE = "/tmp/gp_register.log"


# ─────────────────────────── 日志 ────────────────────────────────
def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ─────────────────────────── 工具：端口 ──────────────────────────
def _free_port(lo: int = 13000, hi: int = 28999) -> int:
    tried: set = set()
    while len(tried) < (hi - lo):
        p = random.randint(lo, hi)
        if p in tried:
            continue
        tried.add(p)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("no free port")


def _check_socks_port(port: int, test_url: str = "https://gp-tree.com/sanctum/csrf-cookie") -> bool:
    try:
        proc = subprocess.Popen(
            ["curl", "-s", "--max-time", "5",
             "--proxy", f"socks5h://127.0.0.1:{port}",
             "-o", "/dev/null", "-w", "%{http_code}", test_url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate(timeout=7)
        code = out.decode().strip()
        return code not in ("", "000")
    except Exception:
        return False


def _pick_socks_port(preferred_ports: list) -> int:
    """选一个可用的 SOCKS5 代理端口"""
    random.shuffle(preferred_ports)
    for port in preferred_ports:
        if _check_socks_port(port):
            log(f"    [proxy] socks5://127.0.0.1:{port} OK")
            return port
    # fallback: 扫描更多 CF SOCKS 端口
    for port in range(10820, 10917):
        if _check_socks_port(port):
            log(f"    [proxy] fallback socks5://127.0.0.1:{port} OK")
            return port
    raise RuntimeError("no reachable SOCKS5 proxy found for gp-tree.com")


# ─────────────────────────── 工具：HTTP ──────────────────────────
def _make_opener(proxies: Optional[dict] = None) -> urllib.request.OpenerDirector:
    cj = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(cj)]
    if proxies:
        handlers.append(urllib.request.ProxyHandler(proxies))
    return urllib.request.build_opener(*handlers)


def _opener_xsrf(opener: urllib.request.OpenerDirector) -> str:
    """GET /sanctum/csrf-cookie → 返回 XSRF-TOKEN 明文"""
    req = urllib.request.Request(
        f"{BASE_URL}/sanctum/csrf-cookie",
        headers={"Accept": "application/json"},
    )
    try:
        with opener.open(req, timeout=15) as r:
            r.read()
    except Exception:
        pass
    for c in opener.handlers:
        if hasattr(c, "cookiejar"):
            for cookie in c.cookiejar:
                if cookie.name == "XSRF-TOKEN":
                    return urllib.parse.unquote(cookie.value)
    return ""


def _api_post(opener: urllib.request.OpenerDirector, path: str, data: dict,
              xsrf: str = "", bearer: str = "") -> dict:
    """通用 JSON POST，返回解析后的 dict（失败返回 {"_error": ...}）"""
    body = json.dumps(data).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if xsrf:
        headers["X-XSRF-TOKEN"] = xsrf
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"_error": f"HTTP {e.code}", "_code": e.code}
    except Exception as ex:
        return {"_error": str(ex)}


# ─────────────────────────── Step 1: Inviter 登录 ─────────────────
def inviter_login() -> tuple[urllib.request.OpenerDirector, str, str]:
    """
    登录 inviter 账号，返回 (opener, xsrf, bearer_token)
    opener 带 session cookie，可用于后续 /api/invites 调用
    """
    log("[inviter] 登录 inviter 账号...")
    opener = _make_opener()
    xsrf = _opener_xsrf(opener)
    resp = _api_post(opener, "/api/auth/login", {
        "email": INVITER_EMAIL,
        "password": INVITER_PASSWORD,
        "cf_turnstile_response": "1",   # GPTree login 服务端不强验证 turnstile
    }, xsrf=xsrf)
    if "_error" in resp and "Invalid credentials" in str(resp.get("message", "")):
        raise RuntimeError(f"Inviter 登录失败: {resp}")
    token = resp.get("token", "")
    if not token and "_error" in resp:
        raise RuntimeError(f"Inviter 登录失败: {resp}")
    log(f"[inviter] 登录成功, token={token[:20]}...")
    return opener, xsrf, token


# ─────────────────────────── Step 2: 发邀请 ──────────────────────
def send_invite(opener: urllib.request.OpenerDirector, xsrf: str,
                invite_email: str) -> bool:
    """
    POST /api/invites 发送邀请
    session cookie 已在 opener 里，Bearer 不一定需要
    """
    log(f"[invite] 发送邀请到 {invite_email} ...")
    resp = _api_post(opener, "/api/invites", {"email": invite_email}, xsrf=xsrf)
    log(f"[invite] 响应: {json.dumps(resp)[:200]}")
    if resp.get("_error") or resp.get("message", "").lower().startswith(("error", "unauthenticated")):
        return False
    return True


# ─────────────────────────── Step 3: 临时邮箱 ────────────────────
class GuerrillaMail:
    def __init__(self):
        self.opener = _make_opener()
        self.email: str = ""
        self.sid: str = ""
        self.last_seq: str = "0"

    def create(self) -> str:
        url = f"{GMAIL_API}?f=get_email_address&lang=en&v=1.6.2"
        with self.opener.open(url, timeout=30) as r:
            d = json.loads(r.read())
        self.email = d["email_addr"]
        self.sid = d["sid_token"]
        log(f"[邮箱] 临时邮箱: {self.email}")
        return self.email

    def _req(self, params: dict) -> dict:
        url = f"{GMAIL_API}?{urllib.parse.urlencode(params)}"
        with self.opener.open(url, timeout=30) as r:
            return json.loads(r.read())

    def wait_for_email(self, from_domain: str = "gp-tree.com",
                       retries: int = 30, interval: int = 6) -> Optional[dict]:
        for i in range(retries):
            d = self._req({"f": "check_email", "seq": self.last_seq, "sid_token": self.sid})
            msgs = [m for m in d.get("list", [])
                    if from_domain in m.get("mail_from", "").lower()]
            if msgs:
                log(f"[邮箱] 收到邮件 (尝试 {i+1})")
                self.last_seq = str(msgs[0]["mail_id"])
                return msgs[0]
            log(f"  [邮箱] 等待... ({i+1}/{retries})")
            time.sleep(interval)
        return None

    def fetch(self, mail_id: str) -> dict:
        return self._req({"f": "fetch_email", "email_id": mail_id, "sid_token": self.sid})

    def find_link(self, mail: dict, keywords: tuple = ()) -> Optional[str]:
        body = mail.get("mail_body", "")
        links = re.findall(r'https?://[^\s<>\'"]+', body)
        for kw in keywords:
            for lnk in links:
                if kw in lnk.lower():
                    return lnk
        for lnk in links:
            if "gp-tree.com" in lnk.lower():
                return lnk
        return None


# ─────────── Turnstile bypass（移植自 unitool_login.py v5.0）──────

# postMessage 拦截器：捕获 CF Turnstile iframe→parent token
_PM_HOOK_JS = (
    "(function(){"
    "if(window.__cf_pm_hooked)return;"
    "window.__cf_pm_hooked=true;"
    "window.__cf_captured_token='';"
    "window.addEventListener('message',function(ev){"
    "try{"
    "var d=ev.data;"
    "if(typeof d==='string'){try{d=JSON.parse(d);}catch(e){}}"
    "var tok='';"
    "if(d&&typeof d==='object'){"
    "tok=d.token||d.cf_token||d.turnstileToken||d.response||'';}"
    "if(!tok&&typeof ev.data==='string'&&ev.data.length>80)tok=ev.data;"
    "if(tok&&tok.length>20&&window.__cf_captured_token.length<20){"
    "window.__cf_captured_token=tok;"
    "var inp=document.querySelector('[name=\\\"cf-turnstile-response\\\"]');"
    "if(inp&&(!inp.value||inp.value.length<20)){"
    "try{"
    "var s=Object.getOwnPropertyDescriptor("
    "window.HTMLInputElement.prototype,'value').set;"
    "s.call(inp,tok);"
    "inp.dispatchEvent(new Event('input',{bubbles:true}));"
    "inp.dispatchEvent(new Event('change',{bubbles:true}));"
    "}catch(e){inp.value=tok;}}}"
    "}catch(e){}},true);})();"
)


def _s(r) -> str:
    if not isinstance(r, dict):
        return str(r) if r else ""
    inner = r.get("result", r)
    if isinstance(inner, dict):
        inner = inner.get("result", inner)
    return str(inner.get("value", "")) if isinstance(inner, dict) else str(inner)


async def _inject_pm_hook(tab):
    """注入 postMessage 拦截器（幂等，多次调用安全）"""
    try:
        await tab.execute_script(_PM_HOOK_JS, return_by_value=True)
    except Exception:
        pass


async def _tok_len(tab, field: str = "cf-turnstile-response") -> int:
    try:
        v = _s(await tab.execute_script(
            f"(document.querySelector('[name=\"{field}\"]')||{{value:''}}).value.length",
            return_by_value=True))
        return int(v)
    except Exception:
        return 0


async def _get_full_token(tab, field: str = "cf-turnstile-response") -> str:
    try:
        return _s(await tab.execute_script(
            f"(document.querySelector('[name=\"{field}\"]')||{{value:''}}).value",
            return_by_value=True))
    except Exception:
        return ""


async def _bypass_turnstile(tab, label: str = "", timeout: int = 55) -> bool:
    """
    Turnstile bypass v6.0 — unitool bypass_until_token pattern
    Phase 1: wait CF iframe (up to 10s)
    Phase 2: 4 rounds _bypass_cloudflare + poll token (15s each)
    Phase 3: reload + final bypass (25s)
    """
    await _inject_pm_hook(tab)

    async def _wait_token(secs: int, tag: str = "") -> bool:
        for i in range(secs):
            await asyncio.sleep(1)
            n = await _tok_len(tab)
            if n > 20:
                log(f"    [{label}] [{tag}] token ready at {i+1}s len={n}")
                return True
            try:
                pm = _s(await tab.execute_script(
                    "window.__cf_captured_token||''", return_by_value=True))
                if len(pm) > 20:
                    return True
            except Exception:
                pass
            if i % 5 == 4:
                log(f"    [{label}] [{tag}] [{i+1}s] waiting token...")
        return False

    # Phase 1: wait for CF Turnstile iframe (up to 10s)
    for i in range(10):
        await asyncio.sleep(1)
        try:
            n_iframe = int(_s(await tab.execute_script(
                "document.querySelectorAll('iframe[src*=\"challenges.cloudflare\"]').length",
                return_by_value=True)) or 0)
            if n_iframe > 0:
                log(f"    [{label}] CF iframe ready at {i+1}s (count={n_iframe})")
                break
            if i % 3 == 2:
                log(f"    [{label}] [{i+1}s] waiting CF iframe...")
        except Exception:
            pass

    # Phase 2: up to 4 rounds bypass + poll
    for rnd in range(4):
        try:
            await tab._bypass_cloudflare({}, time_to_wait_captcha=20)
            log(f"    [{label}] bypass OK (round {rnd+1})")
        except Exception as e:
            log(f"    [{label}] bypass round {rnd+1} err: {e}")
        if await _wait_token(15, f"rnd{rnd+1}"):
            return True
        log(f"    [{label}] round {rnd+1} token still 0, retrying bypass...")
        await asyncio.sleep(2)

    # Phase 3: reload + final bypass
    log(f"    [{label}] all rounds failed — reloading page...")
    try:
        cur_url = await tab.current_url
        await tab.go_to(cur_url)
    except Exception as e:
        log(f"    [{label}] reload err: {e}")
    await asyncio.sleep(6)
    await _inject_pm_hook(tab)
    for i in range(12):
        await asyncio.sleep(1)
        try:
            n_iframe = int(_s(await tab.execute_script(
                "document.querySelectorAll('iframe[src*=\"challenges.cloudflare\"]').length",
                return_by_value=True)) or 0)
            if n_iframe > 0:
                log(f"    [{label}] [reload] CF iframe ready at {i+1}s")
                break
            if i % 3 == 2:
                log(f"    [{label}] [reload {i+1}s] waiting iframe...")
        except Exception:
            pass
    try:
        await tab._bypass_cloudflare({}, time_to_wait_captcha=25)
        log(f"    [{label}] final bypass OK")
    except Exception as e:
        log(f"    [{label}] final bypass err: {e}")
    if await _wait_token(15, "final"):
        return True

    log(f"    [{label}] all phases failed (incl. reload)")
    return False

async def register_with_browser(invite_link: str, invite_token: str,
                                 reg_email: str, password: str, full_name: str,
                                 socks_port: int) -> bool:
    """
    pydoll 注册浏览器：
    1. 打开邀请链接 → 设置 referral/invite cookie
    2. 导航到 /register，填表
    3. 注入 PM hook → _bypass_turnstile
    4. 提交，检查成功
    """
    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions

    opt = ChromiumOptions()
    opt.headless = True
    opt.start_timeout = 90
    if CHROME:
        opt.binary_location = CHROME

    # Xvfb 检测：有 DISPLAY 则 non-headless（Turnstile 在无头下成功率低）
    display = os.environ.get("DISPLAY", "")
    if not display:
        import glob as _glob
        if _glob.glob("/tmp/.X99-lock") or _glob.glob("/tmp/.X[0-9]-lock"):
            display = ":99"
            os.environ["DISPLAY"] = display
    if display:
        opt.headless = False
        log(f"    [browser] headless=False (DISPLAY={display})")

    for arg in ["--no-sandbox", "--disable-dev-shm-usage",
                "--window-size=1440,900", "--disable-gpu", "--lang=en-US",
                f"--proxy-server=socks5://127.0.0.1:{socks_port}"]:
        opt.add_argument(arg)

    log(f"[register] 启动浏览器 proxy=socks5://127.0.0.1:{socks_port}")

    async with Chrome(options=opt, connection_port=_free_port()) as browser:
        tab = await browser.start()

        # 1. 注入 PM hook，打开邀请链接（设置 referral cookie）
        await _inject_pm_hook(tab)
        log(f"[register] 打开邀请链接: {invite_link[:80]}")
        try:
            await tab.go_to(invite_link)
        except Exception as e:
            log(f"[register] 邀请链接导航异常（忽略）: {e}")
        await asyncio.sleep(4)
        await _inject_pm_hook(tab)

        # 2. 导航到 /register（如果邀请链接没有自动重定向）
        current_url = ""
        try:
            current_url = _s(await tab.execute_script("location.href", return_by_value=True))
        except Exception:
            pass
        if "/register" not in current_url:
            log("[register] 导航到 /register...")
            try:
                await tab.go_to(f"{BASE_URL}/register" +
                                (f"?invite_token={invite_token}" if invite_token else ""))
            except Exception as e:
                log(f"[register] /register 导航异常: {e}")
            await asyncio.sleep(5)
            await _inject_pm_hook(tab)

        log(f"[register] 当前 URL: {current_url[:80]}")

        # 3. 填写注册表单
        log("[register] 填写表单...")
        for sel, val in [
            ("input#name",     full_name),
            ("input#email",    reg_email),
            ("input#password", password),
        ]:
            try:
                el = await tab.query(sel, timeout=8)
                await el.clear()
                await el.type_text(val)
                await asyncio.sleep(0.3)
            except Exception as e:
                log(f"[register] 填写 {sel} 失败: {e}")
                # JS fallback
                escaped = val.replace("'", "\\'")
                await tab.execute_script(
                    f"(function(){{var el=document.querySelector('{sel}');"
                    f"if(el){{var s=Object.getOwnPropertyDescriptor("
                    f"window.HTMLInputElement.prototype,'value').set;"
                    f"s.call(el,'{escaped}');"
                    f"el.dispatchEvent(new Event('input',{{bubbles:true}}));"
                    f"el.dispatchEvent(new Event('change',{{bubbles:true}}));}}}})();",
                    return_by_value=True)

        # 如果 invite_token 存在但未自动注入，尝试填到隐藏字段
        if invite_token:
            await tab.execute_script(
                f"(function(){{"
                f"var f=document.querySelector('[name=\"invite_token\"]');"
                f"if(f){{f.value='{invite_token}';}}"
                f"window.__gp_invite_token='{invite_token}';"
                f"}})();",
                return_by_value=True)

        # 4. 等待 Turnstile（unitool 同款 bypass）
        await _bypass_turnstile(tab, "register", timeout=55)

        # 确认 token 已填入
        tok_len = await _tok_len(tab)
        if tok_len < 20:
            # 最后兜底：从 postMessage 缓存填入
            pm_tok = _s(await tab.execute_script(
                "window.__cf_captured_token||''", return_by_value=True))
            if len(pm_tok) > 20:
                await tab.execute_script(
                    f"(function(){{"
                    f"var inp=document.querySelector('[name=\"cf-turnstile-response\"]');"
                    f"if(inp){{var s=Object.getOwnPropertyDescriptor("
                    f"window.HTMLInputElement.prototype,'value').set;"
                    f"s.call(inp,'{pm_tok}');"
                    f"inp.dispatchEvent(new Event('input',{{bubbles:true}}));}}"
                    f"}})();",
                    return_by_value=True)
                log(f"[register] postMessage fallback 填入 token len={len(pm_tok)}")
                tok_len = len(pm_tok)
            else:
                log("[register] ⚠ Turnstile token 为空，继续提交（可能失败）")

        log(f"[register] Turnstile token len={tok_len}, 提交表单...")

        # 5. 提交
        try:
            btn = await tab.query("button[type='submit']", timeout=5)
            await btn.click()
        except Exception:
            await tab.execute_script(
                "(function(){var b=document.querySelector('button[type=submit]');if(b)b.click();})();",
                return_by_value=True)

        await asyncio.sleep(6)

        # 6. 检查结果
        html = ""
        try:
            html = await tab.page_source
        except Exception:
            pass
        cur_url = _s(await tab.execute_script("location.href", return_by_value=True))
        log(f"[register] 提交后 URL: {cur_url[:80]}")

        if "captcha verification failed" in html.lower():
            log("[register] ✗ Turnstile 验证失败")
            return False
        if "suspended" in html.lower() or "banned" in html.lower():
            log("[register] ✗ 账号被封禁")
            return False
        if ("/login" in cur_url or "verify" in html.lower()
                or "verification" in html.lower() or "check your email" in html.lower()
                or "/register" not in cur_url):
            log("[register] ✓ 注册提交成功")
            return True

        # 如果仍在 /register，再等一会儿
        await asyncio.sleep(4)
        cur_url = _s(await tab.execute_script("location.href", return_by_value=True))
        if "/register" not in cur_url:
            log(f"[register] ✓ 跳转到 {cur_url[:60]}")
            return True

        log(f"[register] ? 仍在 /register，视为提交成功（等邮件确认）")
        return True


# ─────────────────────────── Step 5: 验证邮件 ────────────────────
def click_verify_link(url: str) -> bool:
    """urllib 直接点击验证链接（不需要浏览器）"""
    log(f"[verify] 点击验证链接: {url[:80]}")
    try:
        opener = _make_opener()
        opener.addheaders = [("User-Agent",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")]
        with opener.open(url, timeout=20) as r:
            r.read()
        log("[verify] ✓ 验证链接已点击")
        return True
    except Exception as e:
        log(f"[verify] 点击失败: {e}")
        return False


# ─────────────────────────── Step 6: 登录取 token ────────────────
def login_get_token(email: str, password: str) -> Optional[str]:
    """
    POST /api/auth/login → 提取 Bearer token
    GPTree login API 不强验证 turnstile（经测试 dummy 值即可）
    """
    log(f"[login] 登录 {email} 获取 Bearer token...")
    opener = _make_opener()
    xsrf = _opener_xsrf(opener)
    resp = _api_post(opener, "/api/auth/login", {
        "email": email,
        "password": password,
        "cf_turnstile_response": "1",
    }, xsrf=xsrf)
    token = resp.get("token", "")
    if token and len(token) > 10:
        log(f"[login] ✓ token={token[:20]}...")
        return token
    log(f"[login] ✗ 无 token，响应: {json.dumps(resp)[:200]}")
    return None


# ─────────────────────────── Step 7: 保存账号 ────────────────────
def save_to_db(email: str, password: str, token: str) -> bool:
    try:
        db = sqlite3.connect(DB_PATH)
        db.execute("""
            CREATE TABLE IF NOT EXISTS ai_accounts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                service    TEXT NOT NULL DEFAULT 'gptree',
                email      TEXT NOT NULL,
                api_key    TEXT,
                status     TEXT NOT NULL DEFAULT 'active',
                notes      TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(service, email)
            )
        """)
        db.execute("""
            INSERT INTO ai_accounts (service, email, api_key, status, notes)
            VALUES ('gptree', ?, ?, 'active', ?)
            ON CONFLICT(service, email) DO UPDATE SET
                api_key=excluded.api_key, status='active',
                notes=excluded.notes, updated_at=datetime('now')
        """, (email, token, f"password:{password}"))
        db.commit()
        db.close()
        log(f"[db] ✓ 已保存 {email} 到 {DB_PATH}")
        return True
    except Exception as e:
        log(f"[db] 保存失败: {e}")
        return False


def update_proxy_env(token: str) -> bool:
    env_path = Path(PROXY_ENV_PATH)
    if not env_path.exists():
        log(f"[env] 未找到 {env_path}")
        return False
    bak = env_path.with_suffix(f".bak.{int(time.time())}")
    text = env_path.read_text()
    env_path.replace(bak)
    text = re.sub(r"(?m)^GPTREE_AUTH_TOKEN=.*$",
                  f"GPTREE_AUTH_TOKEN={token}", text)
    env_path.write_text(text)
    log(f"[env] ✓ GPTREE_AUTH_TOKEN 已更新（备份: {bak.name}）")
    return True


def restart_proxy() -> bool:
    try:
        r = subprocess.run(["pm2", "restart", PM2_NAME],
                           capture_output=True, text=True, check=False)
        ok = r.returncode == 0
        log(f"[pm2] restart {PM2_NAME}: {'✓' if ok else '✗'} {r.stderr.strip()[:100]}")
        return ok
    except Exception as e:
        log(f"[pm2] restart 失败: {e}")
        return False


# ─────────────────────────── 主流程 ──────────────────────────────
async def register_one(dry_run: bool = False, socks_port: Optional[int] = None,
                       inviter_login_result=None) -> Optional[dict]:
    """单次注册流程，返回 {"email","password","token"} 或 None"""
    # 临时邮箱
    mail = GuerrillaMail()
    reg_email = mail.create()
    full_name = _random_full_name()
    password  = _random_password()

    # ── Step 1 & 2: 登录 inviter → 发邀请 ────────────────────────
    if inviter_login_result:
        opener, xsrf, _inv_token = inviter_login_result
    else:
        opener, xsrf, _inv_token = inviter_login()

    ok = send_invite(opener, xsrf, reg_email)
    if not ok:
        # 刷新 XSRF 再试一次
        log("[invite] 重试（刷新 XSRF）...")
        xsrf = _opener_xsrf(opener)
        ok = send_invite(opener, xsrf, reg_email)
    if not ok:
        log("[invite] ✗ 发送邀请失败，继续（可能已有 invite link 在邮箱）")

    # ── Step 3: 等待邀请邮件 ─────────────────────────────────────
    log("[mail] 等待邀请邮件...")
    invite_msg = mail.wait_for_email("gp-tree.com", retries=25, interval=6)
    if not invite_msg:
        log("[mail] ✗ 未收到邀请邮件")
        return None
    invite_mail = mail.fetch(invite_msg["mail_id"])
    invite_link = mail.find_link(invite_mail, ("invite", "refer", "register"))
    log(f"[mail] 邀请链接: {str(invite_link)[:80]}")
    if not invite_link:
        log("[mail] ✗ 未找到邀请链接")
        return None

    # 从链接提取 invite_token
    invite_token = ""
    m = re.search(r"[?&]invite_token=([^&\s]+)", invite_link or "")
    if m:
        invite_token = m.group(1)
        log(f"[mail] invite_token={invite_token[:20]}...")

    # ── Step 4: 注册（pydoll + Turnstile bypass）────────────────
    if not socks_port:
        ports_to_try = CF_SOCKS_PORTS + RESI_PORTS
        socks_port = _pick_socks_port(ports_to_try)

    ok = await register_with_browser(
        invite_link, invite_token, reg_email, password, full_name, socks_port)
    if not ok:
        log("[register] ✗ 注册失败")
        return None

    # ── Step 5: 验证邮件 ─────────────────────────────────────────
    log("[mail] 等待验证邮件...")
    mail.last_seq = "0"  # 重置，找下一封
    verify_msg = mail.wait_for_email("gp-tree.com", retries=25, interval=6)
    if not verify_msg:
        log("[mail] ✗ 未收到验证邮件")
        return None
    verify_mail = mail.fetch(verify_msg["mail_id"])
    verify_link = mail.find_link(verify_mail, ("verify", "confirm", "activate"))
    log(f"[mail] 验证链接: {str(verify_link)[:80]}")
    if verify_link:
        click_verify_link(verify_link)
        time.sleep(3)

    # ── Step 6: 登录取 token ─────────────────────────────────────
    token = None
    for attempt in range(3):
        time.sleep(2)
        token = login_get_token(reg_email, password)
        if token:
            break
        log(f"[login] 重试 {attempt+1}/3...")
    if not token:
        log("[login] ✗ 获取 token 失败")
        return None

    result = {"email": reg_email, "password": password,
              "full_name": full_name, "token": token}
    log(f"[成功] {reg_email} token={token[:20]}...")

    # ── Step 7: 保存 ─────────────────────────────────────────────
    save_to_db(reg_email, password, token)

    if not dry_run:
        update_proxy_env(token)
        restart_proxy()

    return result


# ─────────────────────────── CLI ─────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="GPTree 邀请注册工具 v3 (pydoll + Turnstile)")
    ap.add_argument("--count",    type=int, default=1,  help="注册数量")
    ap.add_argument("--dry-run",  action="store_true",  help="不更新 .env / 不重启 pm2")
    ap.add_argument("--socks-port", type=int, default=0, help="指定 SOCKS5 代理端口（0=自动）")
    ap.add_argument("--list-accounts", action="store_true", help="显示已注册账号")
    args = ap.parse_args()

    if args.list_accounts:
        db = sqlite3.connect(DB_PATH)
        rows = db.execute(
            "SELECT email, substr(api_key,1,25), status, created_at "
            "FROM ai_accounts WHERE service='gptree' ORDER BY created_at DESC"
        ).fetchall()
        db.close()
        print(f"{'Email':<35} {'Token(前25)':<27} {'Status':<10} {'Created'}")
        print("-" * 90)
        for r in rows:
            print(f"{r[0]:<35} {str(r[1]):<27} {r[2]:<10} {r[3]}")
        return

    socks_port = args.socks_port or None

    # 预先登录 inviter（复用同一 session 发多个邀请）
    inviter_session = None
    try:
        inviter_session = inviter_login()
    except Exception as e:
        log(f"[main] inviter 预登录失败: {e}")

    results = []
    for i in range(args.count):
        print(f"\n{'='*60}")
        print(f"[第 {i+1}/{args.count} 次注册]")
        try:
            r = asyncio.run(register_one(
                dry_run=args.dry_run,
                socks_port=socks_port,
                inviter_login_result=inviter_session,
            ))
        except Exception as e:
            import traceback
            log(f"[异常] {e}")
            traceback.print_exc()
            r = None
        if r:
            results.append(r)
        if i < args.count - 1:
            time.sleep(15)

    print(f"\n{'='*60}")
    print(f"完成: {len(results)}/{args.count}")
    for r in results:
        print(f"  {r['email']}  token={r['token'][:25]}...")

    if results:
        out = Path("/root/gptree_accounts.json")
        existing = []
        if out.exists():
            try:
                existing = json.loads(out.read_text())
            except Exception:
                existing = []
        existing.extend(results)
        out.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
        log(f"[记录] 已追加到 {out}")


if __name__ == "__main__":
    main()
