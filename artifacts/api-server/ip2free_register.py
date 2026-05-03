#!/usr/bin/env python3
"""
ip2free.com 注册脚本 v1.0
使用 Webshare HTTP 代理 + 新注册的 Outlook 邮箱在 ip2free.com 注册账号。

流程:
  1. 打开注册页并填写邮箱 / 密码 / 邀请码
  2. 点击"获取验证码"（触发 ip2free 向 Outlook 邮箱发验证码）
  3. 通过 IMAP 轮询 Outlook 收件箱读取 6 位验证码
  4. 填入验证码并提交，验证注册成功

用法:
    python3 ip2free_register.py \
        --email user@outlook.com \
        --outlook-password OutlookPwd \
        --ip2free-password Ip2freePwd123 \
        --proxy http://nnhginhn:ib02dddzfpev@31.59.20.176:6754 \
        [--invite-code 7pdC4VeeYw] \
        [--access-token OAUTH_TOKEN] \
        [--headless true]
"""

import argparse, json, re, sys, time

REGISTER_URL    = "https://www.ip2free.com/cn/register"
DEFAULT_INVITE  = "7pdC4VeeYw"
CODE_WAIT_SEC   = 120   # max seconds to wait for email code


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def gen_ip2free_password(base: str) -> str:
    """从 Outlook 密码派生出符合 ip2free 要求的密码（8-20 位，含字母和数字）。"""
    import random, string
    pwd = re.sub(r"[^a-zA-Z0-9]", "", base)  # 只保留字母数字
    if not pwd:
        pwd = "Aa123456x"
    if len(pwd) < 8:
        pwd += "".join(random.choices(string.ascii_lowercase + string.digits, k=8 - len(pwd)))
    if not any(c.isdigit() for c in pwd):
        pwd = pwd[:-1] + "1"
    if not any(c.isalpha() for c in pwd):
        pwd = "a" + pwd[1:]
    return pwd[:20]


_relay_refs: list = []


def build_proxy_cfg(proxy: str) -> dict | None:
    """
    构建 Playwright 代理配置。
    - SOCKS5 有凭据 → Socks5Relay 中转（Chromium 不支持 SOCKS5 带认证）
    - HTTP  有凭据 → Playwright 原生 username/password（Chromium 原生支持 HTTP 代理认证）
    - 无凭据       → 直接传给 Chromium
    """
    if not proxy:
        return None
    m = re.match(r"(socks5h?|http|https)://([^:]+):([^@]+)@([^:]+):(\d+)", proxy)
    if m:
        scheme, user, password, host, port = m.groups()
        if scheme in ("socks5", "socks5h"):
            import os
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from socks5_relay import Socks5Relay
            relay = Socks5Relay(host, int(port), user, password)
            local_port = relay.start()
            _relay_refs.append(relay)
            print(f"[relay] SOCKS5 中转：127.0.0.1:{local_port} → {host}:{port}", flush=True)
            return {"server": f"socks5://127.0.0.1:{local_port}", "bypass": "localhost"}
        else:
            print(f"[proxy] HTTP代理（原生认证）：{host}:{port}", flush=True)
            return {
                "server":   f"http://{host}:{port}",
                "username": user,
                "password": password,
                "bypass":   "localhost",
            }
    return {"server": proxy, "bypass": "localhost"}


def fetch_verification_code(email: str, password: str, access_token: str = "",
                             timeout_s: int = CODE_WAIT_SEC) -> str | None:
    """
    轮询 Outlook IMAP 收件箱，提取 ip2free 发来的 6 位验证码。
    优先用 OAuth2 access_token；无 token 则 Basic Auth。
    """
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from outlook_imap import fetch_inbox_xoauth2, fetch_inbox_basic
    except ImportError:
        print("[imap] ⚠ outlook_imap 模块未找到，无法读取验证码", flush=True)
        return None

    deadline = time.time() + timeout_s
    attempt  = 0
    print(f"[imap] 等待 ip2free 验证邮件（最多 {timeout_s}s）…", flush=True)

    while time.time() < deadline:
        attempt += 1
        try:
            if access_token:
                result = fetch_inbox_xoauth2(email, access_token, limit=10, search="ip2free")
            else:
                result = fetch_inbox_basic(email, password, limit=10)
            if not result.get("success"):
                print(f"[imap] 第{attempt}次: 读取失败 — {result.get('error','')}", flush=True)
            else:
                msgs = result.get("messages", [])
                print(f"[imap] 第{attempt}次: 共 {len(msgs)} 封邮件", flush=True)
                for msg in msgs:
                    text = (msg.get("subject","") + " " +
                            msg.get("body_plain","") + " " +
                            msg.get("preview",""))
                    # ip2free 邮件通常包含 6 位验证码
                    codes = re.findall(r"\b(\d{6})\b", text)
                    if codes:
                        print(f"[imap] ✅ 找到验证码: {codes[0]}", flush=True)
                        return codes[0]
        except Exception as e:
            print(f"[imap] 第{attempt}次异常: {e}", flush=True)
        time.sleep(10)

    print("[imap] ⚠ 超时未收到验证码", flush=True)
    return None


# ── 主注册函数 ────────────────────────────────────────────────────────────────

def register_ip2free(
    outlook_email:    str,
    outlook_password: str,
    ip2free_password: str,
    proxy:            str  = "",
    invite_code:      str  = DEFAULT_INVITE,
    headless:         bool = True,
    access_token:     str  = "",
) -> tuple[bool, str]:
    """
    在 ip2free.com 注册账号。
    返回 (success, message)。
    """
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        return False, "patchright 未安装，请 pip install patchright"

    proxy_cfg = build_proxy_cfg(proxy)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                args=[
                    "--lang=zh-CN,zh,en-US,en",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--disable-gpu",
                    "--no-first-run",
                    "--ignore-certificate-errors",
                ],
                proxy=proxy_cfg,
            )
            ctx  = browser.new_context(
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()

            # ── 1. 打开注册页 ────────────────────────────────────────────
            url = f"{REGISTER_URL}?inviteCode={invite_code}"
            print(f"[ip2free] 打开注册页: {url}", flush=True)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # ── 2. 填写邮箱 ──────────────────────────────────────────────
            email_loc = page.locator("#email")
            email_loc.wait_for(state="visible", timeout=15000)
            # MUI 组件带 readOnly；用 JS 解除后再 fill
            page.evaluate("""
                const el = document.querySelector('#email');
                if (el) { el.removeAttribute('readonly'); el.focus(); }
            """)
            page.wait_for_timeout(200)
            email_loc.fill(outlook_email)
            print(f"[ip2free] 已填写邮箱: {outlook_email}", flush=True)
            page.wait_for_timeout(400)

            # ── 3. 填写密码 ──────────────────────────────────────────────
            pwd_loc = page.locator("#password")
            page.evaluate("""
                const el = document.querySelector('#password');
                if (el) { el.removeAttribute('readonly'); el.focus(); }
            """)
            page.wait_for_timeout(200)
            pwd_loc.fill(ip2free_password)
            print("[ip2free] 已填写密码", flush=True)
            page.wait_for_timeout(400)

            # ── 4. 填写邀请码（affId 字段） ───────────────────────────────
            try:
                aff_loc = page.locator("#affId")
                if aff_loc.count() > 0:
                    page.evaluate("""
                        const el = document.querySelector('#affId');
                        if (el) { el.removeAttribute('readonly'); el.focus(); }
                    """)
                    aff_loc.fill(invite_code)
                    print(f"[ip2free] 已填写邀请码: {invite_code}", flush=True)
                    page.wait_for_timeout(300)
            except Exception:
                pass

            # ── 5. 点击"获取验证码"按钮 ────────────────────────────────
            code_btn = None
            for sel in [
                'button:has-text("获取验证码")',
                'button:has-text("发送验证码")',
                'button:has-text("获取")',
                'button:has-text("Send Code")',
                'button:has-text("Get Code")',
            ]:
                try:
                    btn = page.locator(sel)
                    if btn.count() > 0:
                        code_btn = btn.first
                        print(f"[ip2free] 找到验证码按钮: {sel}", flush=True)
                        break
                except Exception:
                    pass

            if code_btn is None:
                try:
                    page.screenshot(path="/tmp/ip2free_no_code_btn.png")
                except Exception:
                    pass
                return False, "未找到获取验证码按钮，ip2free 页面结构可能已变化"

            code_btn.click()
            print("[ip2free] 已点击获取验证码，等待邮件…", flush=True)
            page.wait_for_timeout(2000)

            # ── 6. 从 Outlook IMAP 读取验证码 ────────────────────────────
            code = fetch_verification_code(
                outlook_email, outlook_password, access_token,
                timeout_s=CODE_WAIT_SEC
            )
            if not code:
                return False, "等待 ip2free 验证码超时，请检查 Outlook 是否已收到邮件"

            # ── 7. 填写验证码 ─────────────────────────────────────────────
            code_loc = page.locator("#code")
            page.evaluate("""
                const el = document.querySelector('#code');
                if (el) { el.removeAttribute('readonly'); el.focus(); }
            """)
            code_loc.fill(code)
            print(f"[ip2free] 已填写验证码: {code}", flush=True)
            page.wait_for_timeout(400)

            # ── 8. 勾选服务条款（如有）────────────────────────────────────
            try:
                cb = page.locator('input[type="checkbox"]').first
                if cb.count() > 0 and not cb.is_checked():
                    cb.click()
                    print("[ip2free] 已勾选服务条款", flush=True)
                    page.wait_for_timeout(300)
            except Exception:
                pass

            # ── 9. 点击注册提交按钮 ───────────────────────────────────────
            submit_btn = None
            for sel in [
                'button[type="submit"]',
                'button:has-text("注册")',
                'button:has-text("立即注册")',
                'button:has-text("Register")',
                'button:has-text("Sign Up")',
            ]:
                try:
                    btn = page.locator(sel)
                    if btn.count() > 0:
                        submit_btn = btn.last
                        break
                except Exception:
                    pass

            if submit_btn is None:
                return False, "未找到注册提交按钮"

            print("[ip2free] 提交注册…", flush=True)
            submit_btn.click()
            page.wait_for_timeout(4000)

            # ── 10. 判断注册结果 ──────────────────────────────────────────
            cur_url = page.url
            print(f"[ip2free] 当前 URL: {cur_url}", flush=True)

            success_url_kws = ["/dashboard", "/home", "/cn/home", "/user", "/cn/login", "/login"]
            if any(k in cur_url for k in success_url_kws):
                print("[ip2free] ✅ 注册成功（URL 跳转至主页/登录页）", flush=True)
                return True, f"注册成功 | email={outlook_email} | ip2free_password={ip2free_password}"

            # 等待成功提示 toast
            try:
                page.wait_for_selector(
                    '.MuiAlert-standardSuccess, [class*="success"], [role="alert"]',
                    timeout=6000,
                )
                alert_text = page.locator('[role="alert"]').first.inner_text()
                if "success" in alert_text.lower() or "成功" in alert_text:
                    print(f"[ip2free] ✅ 注册成功（Toast: {alert_text[:60]}）", flush=True)
                    return True, f"注册成功 | email={outlook_email}"
            except Exception:
                pass

            # 检查错误提示
            try:
                err_loc = page.locator('[role="alert"], .MuiAlert-message').first
                if err_loc.count() > 0:
                    err_text = err_loc.inner_text()
                    if err_text.strip():
                        return False, f"注册失败: {err_text[:120]}"
            except Exception:
                pass

            try:
                page.screenshot(path=f"/tmp/ip2free_result_{int(time.time())}.png")
            except Exception:
                pass

            return False, f"注册结果不确定，当前 URL: {cur_url}"

    except Exception as e:
        import traceback
        print(f"[ip2free] ❌ 异常:\n{traceback.format_exc()}", flush=True)
        return False, f"异常: {e}"
    finally:
        for relay in _relay_refs:
            try:
                relay.stop()
            except Exception:
                pass


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ip2free.com 注册脚本")
    parser.add_argument("--email",            required=True,  help="Outlook 邮箱地址")
    parser.add_argument("--outlook-password", default="",     help="Outlook 密码（IMAP 读信用）")
    parser.add_argument("--access-token",     default="",     help="Outlook OAuth access token（优先于密码）")
    parser.add_argument("--ip2free-password", default="",     help="ip2free 账号密码（不填则从 Outlook 密码派生）")
    parser.add_argument("--proxy",            default="",     help="代理格式: http://user:pass@host:port")
    parser.add_argument("--invite-code",      default=DEFAULT_INVITE, help="邀请码")
    parser.add_argument("--headless",         default="true", help="无头模式 (true/false)")
    args = parser.parse_args()

    ip2free_pwd = args.ip2free_password or gen_ip2free_password(args.outlook_password or "Aa123456")
    headless    = args.headless.lower() not in ("false", "0", "no")

    print(f"[ip2free] 开始注册 | email={args.email} | proxy={'有' if args.proxy else '无'}", flush=True)

    success, msg = register_ip2free(
        outlook_email=args.email,
        outlook_password=args.outlook_password,
        ip2free_password=ip2free_pwd,
        proxy=args.proxy,
        invite_code=args.invite_code,
        headless=headless,
        access_token=args.access_token,
    )

    result = {
        "success":          success,
        "email":            args.email,
        "ip2free_password": ip2free_pwd if success else "",
        "message":          msg,
    }
    print("\n── JSON 结果 ──")
    print(json.dumps([result], ensure_ascii=False, indent=2))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
