#!/usr/bin/env python3
"""
ip2free.com 注册脚本 v3.0 — Graph API 读取验证码（替代已死的 IMAP Basic Auth）
使用 proxy_chain.ProxyChain 自动跨 Webshare HTTP / 本地 SOCKS5 / 直连 降级重试。

用法:
    python3 ip2free_register.py \
        --email user@outlook.com \
        --account-id 592 \
        [--outlook-password OutlookPwd] \
        [--ip2free-password Ip2freePwd123] \
        [--proxy http://user:pass@host:port]
        [--proxies p1,p2,p3]
        [--auto-proxy]
        [--invite-code 7pdC4VeeYw]
        [--headless true]
"""

import argparse, json, os, re, sys, time, urllib.request

REGISTER_URL   = "https://www.ip2free.com/cn/register"
DEFAULT_INVITE = "7pdC4VeeYw"
CODE_WAIT_SEC  = 120
LOCAL_API      = "http://localhost:8081"


def gen_ip2free_password(base: str) -> str:
    import random, string
    pwd = re.sub(r"[^a-zA-Z0-9]", "", base)
    if not pwd:
        pwd = "Aa123456x"
    if len(pwd) < 8:
        pwd += "".join(random.choices(string.ascii_lowercase + string.digits, k=8 - len(pwd)))
    if not any(c.isdigit() for c in pwd):
        pwd = pwd[:-1] + "1"
    if not any(c.isalpha() for c in pwd):
        pwd = "a" + pwd[1:]
    return pwd[:20]


def fetch_verification_code_graph(account_id: int, timeout_s: int = CODE_WAIT_SEC) -> str | None:
    """
    通过 Graph API（本地 HTTP 代理接口）读取 ip2free 验证邮件中的 6 位数字验证码。
    同时轮询 inbox 和 junkemail（垃圾邮件），防止验证码被过滤。
    """
    deadline = time.time() + timeout_s
    attempt  = 0
    print(f"[graph] 等待 ip2free 验证邮件（最多 {timeout_s}s，account_id={account_id}）…", flush=True)

    while time.time() < deadline:
        attempt += 1
        # 同时检查收件箱和垃圾邮件夹
        for folder in ["inbox", "junkemail"]:
            try:
                payload = json.dumps({
                    "accountId": account_id,
                    "folder":    folder,
                    "top":       15,
                }).encode()
                req = urllib.request.Request(
                    f"{LOCAL_API}/api/tools/outlook/fetch-messages-by-id",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                r = urllib.request.urlopen(req, timeout=30)
                d = json.loads(r.read())
                msgs = d.get("messages", [])
                via  = d.get("via", "?")
                if msgs:
                    print(f"[graph] 第{attempt}次 [{folder}]: {len(msgs)} 封邮件 via={via}", flush=True)
                for msg in msgs:
                    subj = msg.get("subject", "")
                    prev = msg.get("preview", "")
                    body = msg.get("body", "")
                    text = f"{subj} {prev} {body}"
                    # ip2free 验证码固定 6 位数字
                    codes = re.findall(r"\b(\d{6})\b", text)
                    if codes:
                        print(f"[graph] ✅ 验证码: {codes[0]} [{folder}] (来自: {subj[:60]})", flush=True)
                        return codes[0]
            except Exception as e:
                print(f"[graph] 异常 [{folder}]: {e}", flush=True)
        time.sleep(10)

    print("[graph] ⚠ 超时未收到验证码", flush=True)
    return None


def try_register_with_proxy(
    outlook_email, ip2free_password,
    proxy, invite_code, headless, account_id
):
    """
    单次代理尝试注册。返回 (success, message, is_proxy_error)。
    is_proxy_error=True 表示代理问题，应切换下一个代理重试。
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from proxy_chain import build_proxy_cfg

    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        return False, "patchright 未安装", False

    proxy_cfg   = build_proxy_cfg(proxy)
    proxy_label = (proxy[:50] + "...") if proxy and len(proxy) > 50 else (proxy or "直连")
    print(f"[ip2free] 尝试代理: {proxy_label}", flush=True)

    is_proxy_error = False
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

            url = f"{REGISTER_URL}?inviteCode={invite_code}"
            print(f"[ip2free] 打开: {url}", flush=True)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
            except Exception as e:
                is_proxy_error = True
                return False, f"代理连接失败: {e}", is_proxy_error

            page.wait_for_timeout(3000)

            # 等待表单加载
            try:
                page.wait_for_selector("#email", timeout=12000)
            except Exception:
                is_proxy_error = True
                cur = page.url
                try:
                    page.screenshot(path=f"/tmp/ip2free_nopage_{int(time.time())}.png")
                except Exception:
                    pass
                return False, f"页面未加载 (#email 未出现), URL={cur}", is_proxy_error

            # 填写 email 和 password
            for fid, val in [("#email", outlook_email), ("#password", ip2free_password)]:
                loc = page.locator(fid)
                page.evaluate(f"el=document.querySelector(\"{fid}\");if(el){{el.removeAttribute('readonly');el.focus();}}")
                loc.fill(val)
                page.wait_for_timeout(300)

            # 填写邀请码
            try:
                aff = page.locator("#affId")
                if aff.count() > 0:
                    page.evaluate("el=document.querySelector(\"#affId\");if(el){el.removeAttribute('readonly');}")
                    aff.fill(invite_code)
                    page.wait_for_timeout(200)
            except Exception:
                pass

            # 点击「获取验证码」
            code_btn = None
            for sel in [
                'button:has-text("获取验证码")',
                'button:has-text("发送验证码")',
                'button:has-text("Send Code")',
                'button:has-text("Get Code")',
            ]:
                try:
                    btn = page.locator(sel)
                    if btn.count() > 0:
                        code_btn = btn.first
                        break
                except Exception:
                    pass

            if code_btn is None:
                try:
                    page.screenshot(path="/tmp/ip2free_no_code_btn.png")
                except Exception:
                    pass
                return False, "未找到「获取验证码」按钮", False

            code_btn.click()
            page.wait_for_timeout(2000)
            print("[ip2free] ✉ 已点击「获取验证码」，开始轮询 Graph API…", flush=True)

            # ── Graph API 读取验证码（替代已死的 IMAP）──────────────────────
            code = fetch_verification_code_graph(account_id, timeout_s=CODE_WAIT_SEC)
            if not code:
                try:
                    page.screenshot(path=f"/tmp/ip2free_nocode_{int(time.time())}.png")
                except Exception:
                    pass
                return False, "验证码等待超时（Graph API 未收到 ip2free 邮件）", False

            # 填验证码
            code_loc = page.locator("#code")
            page.evaluate("el=document.querySelector(\"#code\");if(el){el.removeAttribute('readonly');}")
            code_loc.fill(code)
            page.wait_for_timeout(400)

            # 勾选服务条款
            try:
                cb = page.locator('input[type="checkbox"]').first
                if cb.count() > 0 and not cb.is_checked():
                    cb.click()
                    page.wait_for_timeout(200)
            except Exception:
                pass

            # 提交 — MUI 按钮可能需要额外等待激活
            page.wait_for_timeout(800)

            submit_clicked = False
            # 优先尝试 sizeLarge（ip2free 注册主按钮）
            for sel in [
                'button.MuiButton-sizeLarge',
                'button[type="submit"]',
                'button:has-text("注册")',
                'button:has-text("立即注册")',
                'button:has-text("Register")',
            ]:
                try:
                    btn = page.locator(sel)
                    cnt = btn.count()
                    if cnt > 0:
                        # 对 sizeLarge 选最后一个，对其余选 first（避免多个匹配中拿到隐藏的）
                        target = btn.last if "sizeLarge" in sel or "submit" in sel else btn.first
                        target.scroll_into_view_if_needed(timeout=3000)
                        try:
                            target.click(timeout=8000, force=True)
                        except Exception:
                            # JS 强制点击兜底
                            page.evaluate(f"""
                                (function(){{
                                    var btns=document.querySelectorAll('{sel}');
                                    if(btns.length>0) btns[btns.length-1].click();
                                }})()
                            """)
                        submit_clicked = True
                        print(f"[ip2free] ✅ 点击提交 ({sel} × {cnt})", flush=True)
                        break
                except Exception as e2:
                    print(f"[ip2free] 提交选择器 {sel} 异常: {e2}", flush=True)

            if not submit_clicked:
                return False, "未找到提交按钮", False

            page.wait_for_timeout(5000)

            cur_url = page.url
            print(f"[ip2free] 当前 URL: {cur_url}", flush=True)

            # 判断是否跳转到成功页
            ok_kws = ["/dashboard", "/home", "/cn/home", "/user", "/cn/login", "/login"]
            if any(k in cur_url for k in ok_kws):
                print("[ip2free] ✅ 注册成功（URL 跳转）", flush=True)
                return True, f"注册成功 | email={outlook_email}", False

            # 检查 Alert
            try:
                page.wait_for_selector(".MuiAlert-standardSuccess,[role=\"alert\"]", timeout=5000)
                txt = page.locator('[role="alert"]').first.inner_text()
                if "success" in txt.lower() or "成功" in txt:
                    return True, f"注册成功 | email={outlook_email}", False
                return False, f"提示: {txt[:120]}", False
            except Exception:
                pass

            try:
                page.screenshot(path=f"/tmp/ip2free_result_{int(time.time())}.png")
            except Exception:
                pass
            return False, f"结果不确定 URL={cur_url}", False

    except Exception as e:
        import traceback
        print(f"[ip2free] 异常:\n{traceback.format_exc()}", flush=True)
        # 只有真正的代理网络错误才标记 is_proxy_error，Playwright 内部超时不算
        proxy_err = any(k in str(e).lower() for k in [
            "proxy", "err_tunnel", "err_proxy", "err_connection_refused",
            "net::err_connect", "connection refused", "connection reset",
        ])
        return False, f"异常: {e}", proxy_err
    finally:
        from proxy_chain import stop_relays
        stop_relays()


def register_ip2free_adaptive(
    outlook_email, ip2free_password,
    manual_proxies=None, invite_code=DEFAULT_INVITE,
    headless=True, auto_proxy=True, account_proxy="",
    account_id=0
):
    """
    自适应多池代理链路注册。
    自动按 ip2free 用途优先级选取：Pool-C (Webshare) → Pool-B → Pool-A → 直连
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from proxy_chain import ProxyChain

    extra_proxies = []
    if account_proxy:
        extra_proxies.append(account_proxy)
    if manual_proxies:
        extra_proxies.extend([p for p in manual_proxies if p != account_proxy])

    chain = ProxyChain(
        purpose="ip2free" if auto_proxy else "generic",
        count=5 if auto_proxy else 1,
        extra=extra_proxies,
    )

    print(f"[ip2free] 代理链路准备: {len(chain)} 个选项", flush=True)

    for idx, proxy in enumerate(chain):
        label = proxy[:50] if proxy else "直连（无代理）"
        print(f"[ip2free] 尝试 [{idx+1}/{len(chain)}]: {label}", flush=True)

        success, msg, is_proxy_err = try_register_with_proxy(
            outlook_email, ip2free_password,
            proxy, invite_code, headless, account_id
        )

        if success:
            return True, msg

        print(f"[ip2free] 失败: {msg}", flush=True)
        if is_proxy_err:
            chain.mark_failed(proxy)
            print("[ip2free] 代理钝败，切换下一个…", flush=True)
            continue
        else:
            return False, msg

    return False, "所有代理均失败，请检查 Webshare 账号状态"


def main():
    parser = argparse.ArgumentParser(description="ip2free.com 注册")
    parser.add_argument("--email",            required=True)
    parser.add_argument("--account-id",       type=int, default=0,
                        help="Outlook 账号在 DB 中的 id（用于 Graph API 读取验证码）")
    parser.add_argument("--outlook-password", default="")
    parser.add_argument("--ip2free-password", default="")
    parser.add_argument("--proxy",            default="",  help="单个手动代理")
    parser.add_argument("--proxies",          default="",  help="多代理逗号分隔")
    parser.add_argument("--account-proxy",    default="",  help="账号注册时绑定的代理URL（IP一致性）")
    parser.add_argument("--no-auto-proxy",    action="store_true", help="禁用 DB 自适应选取")
    parser.add_argument("--invite-code",      default=DEFAULT_INVITE)
    parser.add_argument("--headless",         default="true")
    args = parser.parse_args()

    ip2free_pwd = args.ip2free_password or gen_ip2free_password(args.outlook_password or "Aa123456")
    headless    = args.headless.lower() not in ("false", "0", "no")
    auto_proxy  = not args.no_auto_proxy

    manual = []
    if args.proxies:
        manual.extend([p.strip() for p in args.proxies.split(",") if p.strip()])
    if args.proxy:
        manual.insert(0, args.proxy)

    print(f"[ip2free] 开始 | email={args.email} account_id={args.account_id} | 手动={len(manual)} | auto={auto_proxy}", flush=True)

    success, msg = register_ip2free_adaptive(
        outlook_email=args.email,
        ip2free_password=ip2free_pwd,
        manual_proxies=manual,
        invite_code=args.invite_code,
        headless=headless,
        auto_proxy=auto_proxy,
        account_proxy=args.account_proxy,
        account_id=args.account_id,
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
