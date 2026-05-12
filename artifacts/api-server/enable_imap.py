#!/usr/bin/env python3
"""
enable_imap.py — 为 Outlook 账号开启 IMAP 访问
================================================
v1.0 — 处理微软完整安全验证流程:
  Step 0: 用 cookies_json 复用已登录 session（或从头 email/password 登录）
  Step 1: 导航到 IMAP 设置页 outlook.live.com/mail/0/options/mail/popimap
  Step 2: 处理密码重新验证弹窗 (Microsoft Security Check)
  Step 3: 处理"添加备用邮箱"提示 → 创建 mail.tm 临时邮箱，填入提交
  Step 4: 从 mail.tm 收件箱获取验证码 → 填入确认
  Step 5: 开启 IMAP radio button → 保存设置
  Step 6: 可选 — 更新 DB tags = imap_enabled

用法:
  python3 enable_imap.py --email foo@outlook.com --password secret
  python3 enable_imap.py --account-id 42
  python3 enable_imap.py --account-id 42 --proxy socks5://127.0.0.1:10851
  python3 enable_imap.py --account-id 42 --headless false
"""

import argparse, json, os, re, sys, time
from pathlib import Path

# ─── MailTM helper (inline, no external import) ──────────────────────────────
import secrets, string, urllib.request, urllib.error

_MAILTM_BASE   = "https://api.mail.tm"
_MAILTM_DOMAIN = "wshu.net"

def _mt_req(method, path, data=None, token=None, timeout=20):
    url = _MAILTM_BASE + path
    body = json.dumps(data).encode() if data else None
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as exc:
        return 0, {"error": str(exc)}

def mailtm_create():
    """创建 mail.tm 账号，返回 (address, password, token)"""
    chars = string.ascii_lowercase + string.digits
    for attempt in range(4):
        login   = "".join(secrets.choice(chars) for _ in range(14))
        address = f"{login}@{_MAILTM_DOMAIN}"
        pw      = "Mt@" + secrets.token_hex(9)
        code, body = _mt_req("POST", "/accounts", {"address": address, "password": pw})
        if code in (200, 201):
            _, tbody = _mt_req("POST", "/token", {"address": address, "password": pw})
            return address, pw, tbody.get("token", "")
        if code == 429 and attempt < 3:
            wait = 25 * (attempt + 1)
            log(f"  [mail.tm] 429 rate-limit, {wait}s 后重试 (attempt {attempt+1}/4)")
            time.sleep(wait)
        else:
            raise RuntimeError(f"mail.tm 创建失败 {code}: {body}")

def mailtm_poll_code(token: str, timeout=240):
    """
    轮询 mail.tm 收件箱，提取第一个 4~8 位数字验证码。
    关键词: 'microsoft', 'security', 'code', 'verify', 'account'
    """
    deadline = time.time() + timeout
    seen: set = set()
    log(f"  [mail.tm] 轮询验证码 (最多 {timeout}s)…")
    while time.time() < deadline:
        code, body = _mt_req("GET", "/messages", token=token)
        if code == 200:
            for msg in body.get("hydra:member", []):
                mid = msg["id"]
                if mid in seen:
                    continue
                subj  = msg.get("subject", "").lower()
                intro = msg.get("intro", "").lower()
                kws   = ("microsoft", "security", "code", "verify", "account", "confirmation", "备用")
                if not any(k in subj or k in intro for k in kws):
                    seen.add(mid)
                    continue
                c2, full = _mt_req("GET", f"/messages/{mid}", token=token)
                if c2 == 200:
                    html = full.get("html", full.get("text", "")) or ""
                    # 优先匹配单行数字块（不含更长序列，避免匹配电话号）
                    m = re.search(r'\b(\d{4,8})\b', re.sub(r'\s+', ' ', html))
                    if m:
                        log(f"  [mail.tm] ✅ 收到邮件 «{msg.get('subject','')[:50]}» 验证码={m.group(1)}")
                        return m.group(1)
                seen.add(mid)
        time.sleep(8)
    log("  [mail.tm] ❌ 超时，未收到验证码")
    return None


# ─── 日志 ─────────────────────────────────────────────────────────────────────
def log(msg: str):
    print(msg, flush=True)




# ─── RESI proxy helper ────────────────────────────────────────────────────────
_RESI_PORTS = [10851, 10853, 10854, 10855, 10857, 10859]

def _pick_resi_proxy() -> str:
    """Pick first reachable RESI SOCKS5 proxy port."""
    import socket
    for port in _RESI_PORTS:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
            s.close()
            return f"socks5://127.0.0.1:{port}"
        except Exception:
            continue
    log("  [proxy] ⚠ 所有 RESI 端口不通，使用直连")
    return ""

def _is_error_page(page) -> bool:
    """Check if page is an error/no-internet page due to proxy failure."""
    try:
        txt = page.evaluate("()=>document.body?.innerText?.slice(0,300)||''") or ""
        url = page.url or ""
        return ("ERR_PROXY" in txt or "No internet" in txt or
                "ERR_NETWORK" in txt or "ERR_CONNECTION" in txt or
                "chrome-error://" in url)
    except Exception:
        return False

# ─── DB helpers ───────────────────────────────────────────────────────────────
_DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")

def db_get_account(account_id: int) -> dict:
    import psycopg2, psycopg2.extras
    conn = psycopg2.connect(_DB_URL)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT id, email, password, cookies_json, fingerprint_json, user_agent, exit_ip, proxy_port "
            "FROM accounts WHERE platform='outlook' AND id=%s",
            (account_id,)
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"账号 id={account_id} 不存在或不是 outlook 平台")
        return dict(row)
    finally:
        conn.close()

def db_tag_imap_enabled(account_id: int):
    """将 imap_enabled 写入 tags 字段"""
    import psycopg2
    try:
        conn = psycopg2.connect(_DB_URL)
        cur  = conn.cursor()
        # tags 是 text[]；用 array_append+distinct 幂等添加
        cur.execute("""
            UPDATE accounts
               SET tags = (
                     SELECT ARRAY(SELECT DISTINCT unnest(array_append(COALESCE(tags,'{}'), 'imap_enabled')))
                   ),
                   updated_at = now()
             WHERE id = %s
        """, (account_id,))
        conn.commit()
        conn.close()
        log(f"  [db] ✅ 账号 {account_id} 已写入 tag: imap_enabled")
    except Exception as e:
        log(f"  [db] ⚠ 写 tag 失败: {e}")


# ─── 浏览器 launch (同步 patchright) ─────────────────────────────────────────
def launch_browser(proxy: str = "", headless: bool = True):
    from patchright.sync_api import sync_playwright
    import subprocess as _sp

    # 释放 page cache（同 PatchrightController.launch）
    try:
        _sp.run(["sync"], check=False, timeout=3)
        with open("/proc/sys/vm/drop_caches", "w") as _d:
            _d.write("1\n")
    except Exception:
        pass

    p = sync_playwright().start()
    _FULL_CHROME = "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome"
    _exec = _FULL_CHROME if os.path.exists(_FULL_CHROME) else None
    launch_args = dict(
        headless=headless,
        executable_path=_exec,
        args=[
            "--lang=en-US,en",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-extensions",
            "--disable-web-security",
            "--no-first-run",
            "--no-default-browser-check",
            "--ignore-certificate-errors",
            "--allow-running-insecure-content",
            "--disable-background-networking",
            "--disable-sync",
            "--metrics-recording-only",
            "--mute-audio",
            "--disable-renderer-backgrounding",
            "--disable-features=Translate,BackForwardCache,OptimizationHints",
            "--js-flags=--max-old-space-size=512",
        ],
    )
    if proxy:
        launch_args["proxy"] = {"server": proxy}
    b = p.chromium.launch(**launch_args)
    return p, b


# ─── 主流程 ───────────────────────────────────────────────────────────────────
IMAP_URL  = "https://outlook.live.com/mail/0/options/mail/popimap"
INBOX_URL = "https://outlook.live.com/mail/0/inbox"

def _screenshot(page, tag: str, email: str):
    safe = email.split("@")[0].replace("/", "_")
    path = f"/tmp/enable_imap_{tag}_{safe}.png"
    try:
        page.screenshot(path=path)
        log(f"  [screenshot] {path}")
    except Exception:
        pass


def _react_fill(page, selector: str, value: str):
    """React-safe 填值：native setter + input/change events"""
    page.evaluate("""([sel, val]) => {
        const inp = document.querySelector(sel);
        if (!inp) return;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
        setter.call(inp, val);
        inp.dispatchEvent(new Event('input',  {bubbles:true}));
        inp.dispatchEvent(new Event('change', {bubbles:true}));
    }""", [selector, value])


def _click_primary(page, extra_sels: list[str] | None = None, timeout=3000) -> bool:
    """点击 MS 表单主按钮（Next / Continue / Submit）"""
    sels = [
        'button[data-testid="primaryButton"]',
        'input[id="idSIButton9"]',
        'input[type="submit"]',
        'button[type="submit"]',
        'input[value="Next"]',
        'input[value="Submit"]',
        'button:has-text("Next")',
        'button:has-text("Continue")',
    ] + (extra_sels or [])
    for s in sels:
        try:
            loc = page.locator(s).first
            if loc.is_visible(timeout=timeout):
                loc.click()
                page.wait_for_timeout(2000)
                return True
        except Exception:
            continue
    return False


def _handle_password_rechallenge(page, email: str, password: str) -> bool:
    """
    Step 2: 微软要求在进入安全设置前重新验证密码。
    可能形态:
      A. 弹窗 popup（新页面）
      B. 内联 dialog（同页面中 password input 变可见）
      C. 重定向到 login.live.com（密码页）
    返回 True 表示已处理（或无需处理）。
    """
    log("  [reauth] 检查密码重验证提示…")
    page.wait_for_timeout(2500)

    # 等待最多 12s，看有无 password input 出现
    _pw_sel = 'input[type="password"], input[name="passwd"]'
    _pw_visible = False
    try:
        page.wait_for_selector(_pw_sel, timeout=12000)
        _pw_visible = True
    except Exception:
        pass

    if not _pw_visible:
        # 检查页面是否含 "sign in" / "verify" / "password" 字样（可能是静态文字提示）
        txt = ""
        try:
            txt = page.evaluate("()=>document.body.innerText.toLowerCase()") or ""
        except Exception:
            pass
        if not any(k in txt for k in ("password", "sign in", "verify your", "confirm your")):
            log("  [reauth] 无密码重验证，跳过")
            return True
        # 尝试点击 "Sign in" button 触发密码框
        for btn_sel in ['button:has-text("Sign in")', 'a:has-text("Sign in")',
                        'button:has-text("Verify")', 'button:has-text("Confirm")']:
            try:
                b = page.locator(btn_sel).first
                if b.is_visible(timeout=2000):
                    b.click()
                    page.wait_for_timeout(3000)
                    break
            except Exception:
                continue
        try:
            page.wait_for_selector(_pw_sel, timeout=10000)
            _pw_visible = True
        except Exception:
            pass

    if not _pw_visible:
        log("  [reauth] 密码框始终未出现，继续")
        return True

    # 填写 email（有些流程会先要求 email）
    _em_sel = 'input[type="email"], input[name="loginfmt"]'
    try:
        em = page.locator(_em_sel).first
        if em.is_visible(timeout=2000):
            _react_fill(page, 'input[name="loginfmt"],input[type="email"]', email)
            page.wait_for_timeout(500)
            _click_primary(page)
            page.wait_for_timeout(2000)
            # 等密码框
            try:
                page.wait_for_selector(_pw_sel, timeout=8000)
            except Exception:
                pass
    except Exception:
        pass

    # 填密码
    try:
        pw = page.locator(_pw_sel).first
        if pw.is_visible(timeout=5000):
            pw.fill(password)
            page.wait_for_timeout(500)
            _click_primary(page)
            page.wait_for_timeout(4000)
            log("  [reauth] ✅ 密码已提交")
            return True
    except Exception as e:
        log(f"  [reauth] ⚠ 填密码异常: {e}")

    return False


def _handle_skip_interrupts(page) -> bool:
    """跳过 passkey/stay-signed-in/recovery 打断页"""
    clicked = False
    for sel in [
        'button:has-text("Skip for now")', 'button:has-text("Maybe later")',
        'button:has-text("Not now")',       'button:has-text("Skip")',
        'a:has-text("Skip for now")',       'a:has-text("Maybe later")',
        'button:has-text("跳过")',           'button:has-text("稍后")',
        'a:has-text("跳过")',
        'input[type="submit"][value="No"]', 'button:has-text("No")',
        '[data-testid="secondaryButton"]',  '#idBtn_Back',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1200):
                loc.click()
                page.wait_for_timeout(1500)
                clicked = True
                log(f"  [skip-interrupt] clicked: {sel}")
                break
        except Exception:
            continue
    return clicked


def _handle_add_backup_email(page, email: str) -> tuple[bool, str, str]:
    """
    Step 3: 微软要求添加备用邮箱。
    检测条件:
      - 页面含 "alternate email" / "backup email" / "recovery email" / "security info" 字样
      - 或有 input[type=email] 让用户填写备用邮箱
    流程:
      1. 创建 mail.tm 临时邮箱
      2. 填入 input 框 → 提交
      3. 返回 (已处理, mailtm_addr, mailtm_token)
    """
    log("  [backup-email] 检查是否需要添加备用邮箱…")
    page.wait_for_timeout(2000)

    txt = ""
    try:
        txt = page.evaluate("()=>document.body.innerText.toLowerCase()") or ""
    except Exception:
        pass

    BACKUP_KWS = (
        "alternate email", "backup email", "recovery email",
        "security info", "add email", "add a backup",
        "add another email", "备用邮箱", "恢复邮箱", "备用电子邮件",
        "keep your account secure", "protect your account",
        "add a way to", "proof up",
    )
    if not any(k in txt for k in BACKUP_KWS):
        log("  [backup-email] 无备用邮箱提示，跳过")
        return False, "", ""

    log("  [backup-email] 检测到备用邮箱要求，创建 mail.tm 临时邮箱…")
    try:
        mt_addr, mt_pw, mt_token = mailtm_create()
    except Exception as e:
        log(f"  [backup-email] ❌ mail.tm 创建失败: {e}")
        return False, "", ""
    log(f"  [backup-email] mail.tm: {mt_addr}")

    # 找 email input 并填入
    _em_filled = False
    # 先找明确的 alternate/backup email input
    for sel in [
        'input[name*="Email" i][type="email"]',
        'input[name*="email" i][type="email"]',
        'input[placeholder*="email" i]',
        'input[placeholder*="Email" i]',
        'input[type="email"]',
        'input[name="ProofConfirmation"]',
        'input[name="EmailAddress"]',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                loc.fill(mt_addr)
                page.wait_for_timeout(500)
                _em_filled = True
                log(f"  [backup-email] 填入 {mt_addr} → {sel}")
                break
        except Exception:
            continue

    if not _em_filled:
        # JS fallback: 找第一个可见 email-type input（排除已有值的）
        _em_filled = page.evaluate("""(addr) => {
            var inputs = Array.from(document.querySelectorAll('input[type="email"],input[type="text"]'));
            for (var inp of inputs) {
                var style = window.getComputedStyle(inp);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                var ph = (inp.placeholder || '').toLowerCase();
                var nm = (inp.name || '').toLowerCase();
                if (!inp.value) {
                    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                    setter.call(inp, addr);
                    inp.dispatchEvent(new Event('input',  {bubbles:true}));
                    inp.dispatchEvent(new Event('change', {bubbles:true}));
                    return true;
                }
            }
            return false;
        }""", mt_addr)
        if _em_filled:
            log(f"  [backup-email] JS 填入 {mt_addr}")

    if not _em_filled:
        log("  [backup-email] ⚠ 未找到 email input，尝试继续")

    page.wait_for_timeout(800)

    # 提交
    _submitted = _click_primary(page, [
        'button:has-text("Send code")',
        'button:has-text("Add")',
        'button:has-text("Save")',
        'button:has-text("Continue")',
        'input[value*="Send"]',
        'input[value*="Add"]',
    ])
    if _submitted:
        log("  [backup-email] ✅ 备用邮箱提交成功，等待验证码…")
    else:
        log("  [backup-email] ⚠ 未找到提交按钮，按 Enter 尝试")
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass

    page.wait_for_timeout(3000)
    return True, mt_addr, mt_token


def _handle_security_code(page, mt_token: str) -> bool:
    """
    Step 4: 从 mail.tm 收件箱获取验证码，填入表单并提交。
    """
    if not mt_token:
        log("  [security-code] 无 mail.tm token，跳过")
        return False

    log("  [security-code] 等待 mail.tm 收件…")
    code = mailtm_poll_code(mt_token, timeout=240)
    if not code:
        log("  [security-code] ❌ 未收到验证码")
        return False

    log(f"  [security-code] 获得验证码: {code}，填入表单…")

    # 找验证码 input
    _code_filled = False
    for sel in [
        'input[name*="Code" i]',
        'input[name*="code" i]',
        'input[name*="otp" i]',
        'input[name*="Otp" i]',
        'input[placeholder*="code" i]',
        'input[placeholder*="Code" i]',
        'input[type="number"]',
        'input[type="text"][maxlength="6"]',
        'input[type="text"][maxlength="7"]',
        'input[type="text"][maxlength="8"]',
        'input[type="text"]',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                loc.fill(code)
                page.wait_for_timeout(400)
                _code_filled = True
                log(f"  [security-code] 填入 {code} → {sel}")
                break
        except Exception:
            continue

    if not _code_filled:
        # JS fallback
        page.evaluate("""(code) => {
            var inputs = Array.from(document.querySelectorAll('input'));
            for (var inp of inputs) {
                var style = window.getComputedStyle(inp);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                var maxl = parseInt(inp.getAttribute('maxlength') || '99');
                if (maxl >= 4 && maxl <= 9 && !inp.value) {
                    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                    setter.call(inp, code);
                    inp.dispatchEvent(new Event('input',  {bubbles:true}));
                    inp.dispatchEvent(new Event('change', {bubbles:true}));
                    return;
                }
            }
        }""", code)
        log(f"  [security-code] JS 填入验证码")

    page.wait_for_timeout(500)

    # 提交
    _click_primary(page, [
        'button:has-text("Verify")',
        'button:has-text("Confirm")',
        'button:has-text("Submit")',
        'input[value*="Verify"]',
        'input[value*="Confirm"]',
    ])
    page.wait_for_timeout(4000)
    log("  [security-code] ✅ 验证码已提交")
    return True


def _navigate_to_imap_settings(page, email: str) -> bool:
    """
    Step 1 + SPA 热路由到 IMAP 设置页。
    Path A: inbox warmup → SPA-hot goto popimap URL
    Path B: gear-click → Mail → Forwarding/IMAP
    """
    _gear_sel = '#owaSettingsBtn_container, [aria-label="Settings"], [aria-label="设置"]'

    # Path A ─ SPA 热路由
    log("  [nav] Path A: inbox warmup → SPA-hot goto IMAP URL")
    try:
        if "outlook.live.com/mail" not in (page.url or ""):
            page.goto(INBOX_URL, timeout=40000, wait_until="domcontentloaded")
        # 等 gear 可见（SPA 完整加载）
        try:
            page.wait_for_selector(_gear_sel, timeout=30000)
            log("  [nav] SPA ready (gear visible)")
        except Exception:
            log(f"  [nav] ⚠ gear 未出现 30s, url={page.url[:80]}")
        # SPA 热路由到 IMAP 设置
        try:
            page.goto(IMAP_URL, timeout=25000, wait_until="domcontentloaded")
        except Exception as e:
            log(f"  [nav] goto 超时(ok): {e}")
        page.wait_for_timeout(2500)
    except Exception as e:
        log(f"  [nav] Path A 异常: {e}")

    # 检查是否到达 IMAP 设置页
    def _has_imap_content(timeout_ms=10000) -> bool:
        try:
            page.wait_for_selector(
                '[data-testid*="imap" i], [aria-label*="IMAP" i], '
                'input[type="radio"], [role="radio"], [role="switch"]',
                timeout=timeout_ms,
            )
            return True
        except Exception:
            pass
        try:
            page.wait_for_function(
                "() => document.body.innerText.toLowerCase().indexOf('imap') >= 0",
                timeout=timeout_ms,
            )
            return True
        except Exception:
            pass
        return False

    if _has_imap_content(8000):
        log("  [nav] ✅ Path A 已到 IMAP 设置页")
        return True

    # Path B ─ gear-click
    log("  [nav] Path B: gear-click → Mail → Forwarding+IMAP")
    if "outlook.live.com/mail" not in (page.url or ""):
        try:
            page.goto(INBOX_URL, timeout=40000, wait_until="domcontentloaded")
        except Exception:
            pass
    try:
        page.wait_for_selector(_gear_sel, timeout=25000)
    except Exception:
        page.wait_for_timeout(8000)

    # B2: click gear
    _gear_clicked = False
    for gs in ['#owaSettingsBtn_container', '[aria-label="Settings"]', '[aria-label="设置"]',
               '[title="Settings"]', 'button[aria-label*="etting"]']:
        try:
            g = page.locator(gs).first
            if g.is_visible(timeout=3000):
                g.click()
                page.wait_for_timeout(2500)
                _gear_clicked = True
                log(f"  [nav] gear clicked: {gs}")
                break
        except Exception:
            continue
    if not _gear_clicked:
        log("  [nav] ❌ gear 未找到")
        return False

    # B3: Mail nav
    page.evaluate("""() => {
        var byLabel = document.querySelector('[aria-label="Mail"][role="option"],[aria-label="Mail"][role="tab"],[aria-label="邮件"]');
        if (byLabel) { byLabel.click(); return; }
        var els = Array.from(document.querySelectorAll(
            'button,[role="option"],[role="menuitem"],[role="tab"],[role="treeitem"],li>a,nav a,a'));
        for (var el of els) {
            var t = el.textContent.trim();
            if (t === "Mail" || t === "邮件") { el.click(); return; }
        }
    }""")
    page.wait_for_timeout(2500)

    # B4: Forwarding/IMAP sub-item
    page.evaluate("""() => {
        var els = Array.from(document.querySelectorAll(
            'button,[role="option"],[role="menuitem"],[role="tab"],[role="treeitem"],li>a,nav a,a,li'));
        for (var el of els) {
            var t = (el.textContent || "").toLowerCase();
            if (t.indexOf("forward") >= 0 || t.indexOf("转发") >= 0 ||
                (t.indexOf("imap") >= 0 && t.indexOf("pop") >= 0)) {
                var ce = (el.tagName === "LI") ? (el.querySelector("button,a") || el) : el;
                ce.click(); return;
            }
        }
    }""")
    page.wait_for_timeout(3000)

    if _has_imap_content(8000):
        log("  [nav] ✅ Path B 已到 IMAP 设置页")
        return True

    log("  [nav] ⚠ Path B 后仍未检测到 IMAP 内容")
    return False


def _toggle_imap_enable(page) -> str:
    """
    Step 5a: 找 IMAP radio/switch 并开启。
    返回 'already-on' / 'clicked:...' / 'not-found'
    """
    res = page.evaluate("""() => {
        // S1: radio/checkbox/switch 附近含 imap 文字
        var inputs = Array.from(document.querySelectorAll(
            'input[type="radio"],input[type="checkbox"],[role="radio"],[role="switch"],[role="checkbox"]'));
        for (var el of inputs) {
            var par = el.closest('li') || el.closest('label') || el.closest('div') || el.parentElement;
            var txt = (par ? par.textContent : "").toLowerCase();
            if (txt.indexOf("imap") < 0) continue;
            var chk = el.getAttribute("aria-checked") || (el.checked !== undefined ? String(el.checked) : "false");
            if (chk === "true" || el.checked) return "already-on";
            el.click(); return "radio-clicked:" + (el.id || el.name || el.value || "?");
        }
        // S2: label 文字 "Enable IMAP"
        for (var lbl of Array.from(document.querySelectorAll("label"))) {
            var lt = lbl.textContent.toLowerCase();
            if (lt.indexOf("enable imap") >= 0 || lt.indexOf("启用 imap") >= 0) {
                var inp = lbl.control || document.getElementById(lbl.htmlFor);
                if (inp) { inp.click(); return "label-enable-inp-clicked"; }
                lbl.click(); return "label-clicked";
            }
        }
        // S3: aria-label 含 IMAP
        var byAttr = Array.from(document.querySelectorAll('[aria-label*="IMAP" i],[data-testid*="imap" i]'));
        for (var el of byAttr) {
            var chk = el.getAttribute("aria-checked") || (el.checked !== undefined ? String(el.checked) : "false");
            if (chk === "true" || el.checked) return "attr-already-on";
            el.click(); return "attr-clicked:" + el.getAttribute("aria-label");
        }
        // S4: 任意未选中 switch
        var switches = Array.from(document.querySelectorAll('[role="switch"]'));
        for (var sw of switches) {
            if (sw.getAttribute("aria-checked") !== "true") {
                sw.click(); return "switch-clicked";
            } else { return "switch-already-on"; }
        }
        return "not-found";
    }""")
    return res


def _save_settings(page) -> bool:
    for sel in [
        'button:has-text("Save")', 'button:has-text("保存")',
        'button[type="submit"]', 'input[type="submit"]',
        'button[aria-label*="Save"]', 'input[value="Save"]',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500):
                loc.click()
                page.wait_for_timeout(2000)
                log(f"  [save] saved via {sel}")
                return True
        except Exception:
            continue
    # JS fallback
    try:
        page.evaluate("""() => {
            Array.from(document.querySelectorAll("button,input[type=submit]")).forEach(b => {
                var t = b.textContent.trim();
                if (/^(Save|保存)$/i.test(t) || b.value === 'Save') b.click();
            });
        }""")
        page.wait_for_timeout(1500)
        return True
    except Exception:
        pass
    return False


# ─── 主函数 ───────────────────────────────────────────────────────────────────
def enable_imap(
    email: str,
    password: str,
    account_id: int | None = None,
    cookies_json: str = "",
    fingerprint_json: str = "",
    proxy: str = "",
    headless: bool = True,
) -> bool:
    safe = email.split("@")[0]
    log(f"\n{'='*60}")
    log(f"[enable-imap] 开始处理: {email} (id={account_id})")

    p, browser = launch_browser(proxy=proxy, headless=headless)
    try:
        # ── 准备 context ────────────────────────────────────────────────────
        ctx_kwargs = dict(locale="en-US", timezone_id="America/Los_Angeles",
                          user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        if fingerprint_json:
            try:
                fp = json.loads(fingerprint_json)
                for k in ("user_agent", "locale", "timezone_id", "viewport", "screen"):
                    if fp.get(k):
                        ctx_kwargs[k] = fp[k]
            except Exception:
                pass
        context = browser.new_context(**ctx_kwargs)
        if cookies_json:
            try:
                state = json.loads(cookies_json)
                if isinstance(state, dict) and state.get("cookies"):
                    context.add_cookies(state["cookies"])
                    log(f"  [ctx] 已注入 {len(state['cookies'])} 个 cookies")
            except Exception as e:
                log(f"  [ctx] ⚠ cookies 解析失败: {e}")
        page = context.new_page()

        # ── Step 0: 确保已登录 ──────────────────────────────────────────────
        log("  [step0] 检查 Outlook 登录状态…")
        try:
            page.goto(INBOX_URL, timeout=45000, wait_until="domcontentloaded")
        except Exception as e:
            log(f"  [step0] goto inbox 超时(ok): {e}")
        page.wait_for_timeout(3000)

        # 检查是否需要从头登录
        cur_url = page.url or ""
        need_login = ("login" in cur_url or "passport" in cur_url
                      or "microsoft.com/devicelogin" in cur_url
                      or "live.com/login" in cur_url
                      or "account.microsoft.com/account" in cur_url)
        if not need_login:
            # 看有无邮箱 input（表示 session 已过期跳转到登录页）
            try:
                _em_inp = page.locator('input[name="loginfmt"],input[type="email"]').first
                if _em_inp.is_visible(timeout=3000):
                    need_login = True
            except Exception:
                pass
        if not need_login:
            # 检查是否真的在 inbox（gear 可见）
            try:
                _gear = page.locator('#owaSettingsBtn_container,[aria-label="Settings"],[aria-label="设置"]').first
                if not _gear.is_visible(timeout=8000):
                    need_login = True
            except Exception:
                need_login = True

        if need_login:
            if not password:
                log("  [step0] ❌ 需要密码登录但未提供 password，中止")
                return False
            log("  [step0] Session 无效，执行 email/password 登录…")
            # 导航到 login 页
            try:
                page.goto("https://login.live.com/login.srf?wa=wsignin1.0", timeout=30000, wait_until="domcontentloaded")
            except Exception:
                pass
            page.wait_for_timeout(2000)
            # email
            try:
                _ei = page.locator('input[name="loginfmt"],input[type="email"]').first
                _ei.wait_for(timeout=10000)
                _react_fill(page, 'input[name="loginfmt"],input[type="email"]', email)
                page.wait_for_timeout(500)
                _click_primary(page)
                page.wait_for_timeout(3000)
            except Exception as e:
                log(f"  [step0] ⚠ 填 email 异常: {e}")
            # password
            try:
                _pw = page.locator('input[type="password"],input[name="passwd"]').first
                _pw.wait_for(timeout=10000)
                _pw.fill(password)
                page.wait_for_timeout(500)
                _click_primary(page)
                page.wait_for_timeout(5000)
            except Exception as e:
                log(f"  [step0] ⚠ 填 password 异常: {e}")
            # 跳过打断页
            for _ in range(3):
                if _handle_skip_interrupts(page):
                    page.wait_for_timeout(1500)
            log("  [step0] 登录流程完成")

        _screenshot(page, "00_after_login", email)

        # ── Step 1: 导航到 IMAP 设置页 ─────────────────────────────────────
        log("  [step1] 导航到 IMAP 设置…")
        _navigate_to_imap_settings(page, email)
        _screenshot(page, "01_after_nav", email)

        # ── Step 2: 密码重验证 ─────────────────────────────────────────────
        _handle_password_rechallenge(page, email, password)
        _screenshot(page, "02_after_reauth", email)

        # 跳过可能出现的打断页
        for _ in range(2):
            if _handle_skip_interrupts(page):
                page.wait_for_timeout(1500)

        # ── Step 3: 添加备用邮箱 ───────────────────────────────────────────
        _added_backup, mt_addr, mt_token = _handle_add_backup_email(page, email)
        _screenshot(page, "03_after_backup", email)

        # ── Step 4: 验证码 ─────────────────────────────────────────────────
        if _added_backup:
            _handle_security_code(page, mt_token)
            _screenshot(page, "04_after_code", email)
            # 跳过更多打断页（passkey 等）
            for _ in range(3):
                if _handle_skip_interrupts(page):
                    page.wait_for_timeout(1500)

        # ── Step 5: 再次导航到 IMAP 设置（可能被重定向走了）──────────────
        cur_url2 = page.url or ""
        _on_imap = ("popimap" in cur_url2 or "imap" in (page.evaluate("()=>document.body.innerText.toLowerCase()") or ""))
        if not _on_imap:
            log("  [step5] 重新导航到 IMAP 设置页…")
            _navigate_to_imap_settings(page, email)
            _screenshot(page, "05_re_nav", email)

        # ── Step 5a: 开启 IMAP toggle ──────────────────────────────────────
        page.wait_for_timeout(2000)
        res = _toggle_imap_enable(page)
        log(f"  [step5] IMAP toggle 结果: {res}")
        _screenshot(page, "05_after_toggle", email)

        if res == "not-found":
            # 再等 5s 看看内容是否延迟加载
            page.wait_for_timeout(5000)
            res = _toggle_imap_enable(page)
            log(f"  [step5] IMAP toggle 二次结果: {res}")
            _screenshot(page, "05b_after_toggle2", email)

        if res == "not-found":
            log("  [step5] ❌ 未找到 IMAP toggle")
            # dump page text for debug
            try:
                bt = page.evaluate("()=>document.body.innerText.slice(0,600)")
                log(f"  [step5] 页面文字: {bt[:400]}")
            except Exception:
                pass
            return False

        # ── Step 5b: Save ──────────────────────────────────────────────────
        if "already" not in res:
            page.wait_for_timeout(800)
            _save_settings(page)
        _screenshot(page, "06_after_save", email)

        log(f"  [enable-imap] ✅ 成功！IMAP 已开启 ({res})")

        # ── Step 6: 写 DB tag ──────────────────────────────────────────────
        if account_id:
            db_tag_imap_enabled(account_id)

        return True

    except Exception as e:
        import traceback
        log(f"  [enable-imap] ❌ 异常: {e}")
        log(traceback.format_exc())
        return False
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="为 Outlook 账号开启 IMAP")
    ap.add_argument("--email",      default="",  help="Outlook 邮箱")
    ap.add_argument("--password",   default="",  help="密码")
    ap.add_argument("--account-id", type=int, default=0, help="从 DB 读取账号 (优先)")
    ap.add_argument("--proxy",      default="",  help="代理 socks5://127.0.0.1:10851")
    ap.add_argument("--headless",   default="true", help="true/false")
    args = ap.parse_args()

    headless = args.headless.lower() not in ("false", "0", "no")

    email, password, acc_id = args.email, args.password, args.account_id
    cookies_json = ""
    fingerprint_json = ""

    if acc_id:
        acc = db_get_account(acc_id)
        email         = acc["email"]
        password      = acc.get("password") or ""
        cookies_json  = (acc.get("cookies_json") or "").strip()
        fingerprint_json = (acc.get("fingerprint_json") or "").strip()
        log(f"[cli] 从 DB 读取账号: id={acc_id} email={email} "
             f"(DB proxy_port={acc.get('proxy_port')} ignored – ephemeral)")

    # DB proxy_port 是临时 XrayRelay 端口，跨 session 必然失效；改用 RESI
    if not args.proxy:
        args.proxy = _pick_resi_proxy()
        if args.proxy:
            log(f"[cli] 自动选择 RESI 代理: {args.proxy}")

    if not email:
        ap.error("必须提供 --email 或 --account-id")

    ok = enable_imap(
        email=email, password=password, account_id=acc_id or None,
        cookies_json=cookies_json, fingerprint_json=fingerprint_json,
        proxy=args.proxy, headless=headless,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
