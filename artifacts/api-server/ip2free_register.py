#!/usr/bin/env python3
"""
ip2free.com 注册脚本 v2.0 — 自适应多池代理重试
使用 proxy_chain.ProxyChain 自动跨 Webshare HTTP / 本地 SOCKS5 / 直连 降级重试。

用法:
    python3 ip2free_register.py \
        --email user@outlook.com \
        --outlook-password OutlookPwd \
        [--ip2free-password Ip2freePwd123] \
        [--proxy http://user:pass@host:port]   # 手动指定单代理
        [--proxies p1,p2,p3]                   # 手动多代理（优先于 DB 自适应）
        [--auto-proxy]                         # 从 DB 自适应选取（默认开启）
        [--invite-code 7pdC4VeeYw]
        [--access-token OAUTH_TOKEN]
        [--headless true]
"""

import argparse, json, os, re, sys, time

REGISTER_URL   = "https://www.ip2free.com/cn/register"
DEFAULT_INVITE = "7pdC4VeeYw"
CODE_WAIT_SEC  = 120


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


def fetch_verification_code(email, password, access_token="", timeout_s=CODE_WAIT_SEC):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from outlook_imap import fetch_inbox_xoauth2, fetch_inbox_basic
    except ImportError:
        print("[imap] \u26a0 outlook_imap \u6a21\u5757\u672a\u627e\u5230", flush=True)
        return None

    deadline = time.time() + timeout_s
    attempt  = 0
    print(f"[imap] \u7b49\u5f85 ip2free \u9a8c\u8bc1\u90ae\u4ef6\uff08\u6700\u591a {timeout_s}s\uff09\u2026", flush=True)

    while time.time() < deadline:
        attempt += 1
        try:
            if access_token:
                result = fetch_inbox_xoauth2(email, access_token, limit=10, search="ip2free")
            else:
                result = fetch_inbox_basic(email, password, limit=10)
            if not result.get("success"):
                print(f"[imap] \u7b2c{attempt}\u6b21: \u8bfb\u53d6\u5931\u8d25 \u2014 {result.get('error','')}", flush=True)
            else:
                msgs = result.get("messages", [])
                print(f"[imap] \u7b2c{attempt}\u6b21: {len(msgs)} \u5c01\u90ae\u4ef6", flush=True)
                for msg in msgs:
                    text = (msg.get("subject","") + " " +
                            msg.get("body_plain","") + " " +
                            msg.get("preview",""))
                    codes = re.findall(r"\b(\d{6})\b", text)
                    if codes:
                        print(f"[imap] \u2705 \u9a8c\u8bc1\u7801: {codes[0]}", flush=True)
                        return codes[0]
        except Exception as e:
            print(f"[imap] \u5f02\u5e38: {e}", flush=True)
        time.sleep(10)

    print("[imap] \u26a0 \u8d85\u65f6\u672a\u6536\u5230\u9a8c\u8bc1\u7801", flush=True)
    return None


def try_register_with_proxy(
    outlook_email, outlook_password, ip2free_password,
    proxy, invite_code, headless, access_token
):
    """
    \u5355\u6b21\u4ee3\u7406\u5c1d\u8bd5\u6ce8\u518c\u3002\u8fd4\u56de (success, message, is_proxy_error)\u3002
    is_proxy_error=True \u8868\u793a\u662f\u4ee3\u7406\u9510\u8d25\uff0c\u5e94\u5207\u6362\u4e0b\u4e00\u4e2a\u4ee3\u7406\u91cd\u8bd5\u3002
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from proxy_chain import build_proxy_cfg

    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        return False, "patchright \u672a\u5b89\u88c5", False

    proxy_cfg = build_proxy_cfg(proxy)
    proxy_label = (proxy[:50] + "...") if proxy and len(proxy) > 50 else (proxy or "\u76f4\u8fde")
    print(f"[ip2free] \u5c1d\u8bd5\u4ee3\u7406: {proxy_label}", flush=True)

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
            print(f"[ip2free] \u6253\u5f00: {url}", flush=True)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
            except Exception as e:
                is_proxy_error = True
                return False, f"\u4ee3\u7406\u8fde\u63a5\u5931\u8d25: {e}", is_proxy_error

            page.wait_for_timeout(3000)

            # \u68c0\u67e5\u9875\u9762\u662f\u5426\u52a0\u8f7d\u6210\u529f
            try:
                page.wait_for_selector("#email", timeout=12000)
            except Exception:
                is_proxy_error = True
                cur = page.url
                try: page.screenshot(path=f"/tmp/ip2free_nopage_{int(time.time())}.png")
                except Exception: pass
                return False, f"\u9875\u9762\u672a\u52a0\u8f7d (#email \u672a\u51fa\u73b0), URL={cur}", is_proxy_error

            # \u586b\u5199\u8868\u5355
            for fid, val in [("#email", outlook_email), ("#password", ip2free_password)]:
                loc = page.locator(fid)
                page.evaluate(f"el=document.querySelector(\"{fid}\");if(el){{el.removeAttribute(\'readonly\');el.focus();}}")
                loc.fill(val)
                page.wait_for_timeout(300)

            # \u586b\u5199\u9080\u8bf7\u7801
            try:
                aff = page.locator("#affId")
                if aff.count() > 0:
                    page.evaluate("el=document.querySelector(\"#affId\");if(el){el.removeAttribute(\'readonly\');}")
                    aff.fill(invite_code)
                    page.wait_for_timeout(200)
            except Exception:
                pass

            # \u70b9\u51fb\u83b7\u53d6\u9a8c\u8bc1\u7801
            code_btn = None
            for sel in [
                'button:has-text("\u83b7\u53d6\u9a8c\u8bc1\u7801")',
                'button:has-text("\u53d1\u9001\u9a8c\u8bc1\u7801")',
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
                try: page.screenshot(path="/tmp/ip2free_no_code_btn.png")
                except Exception: pass
                return False, "\u672a\u627e\u5230\u83b7\u53d6\u9a8c\u8bc1\u7801\u6309\u9215", False

            code_btn.click()
            page.wait_for_timeout(2000)

            # IMAP \u8bfb\u53d6\u9a8c\u8bc1\u7801
            code = fetch_verification_code(
                outlook_email, outlook_password, access_token, timeout_s=CODE_WAIT_SEC
            )
            if not code:
                return False, "\u9a8c\u8bc1\u7801\u7b49\u5f85\u8d85\u65f6", False

            # \u586b\u5199\u9a8c\u8bc1\u7801
            code_loc = page.locator("#code")
            page.evaluate("el=document.querySelector(\"#code\");if(el){el.removeAttribute(\'readonly\');}")
            code_loc.fill(code)
            page.wait_for_timeout(400)

            # \u52fe\u9009\u670d\u52a1\u6761\u6b3e
            try:
                cb = page.locator('input[type="checkbox"]').first
                if cb.count() > 0 and not cb.is_checked():
                    cb.click()
                    page.wait_for_timeout(200)
            except Exception:
                pass

            # \u63d0\u4ea4
            submit = None
            for sel in ['button[type="submit"]', 'button:has-text("\u6ce8\u518c")',
                        'button:has-text("\u7acb\u5373\u6ce8\u518c")', 'button:has-text("Register")']:
                try:
                    btn = page.locator(sel)
                    if btn.count() > 0:
                        submit = btn.last
                        break
                except Exception:
                    pass

            if submit is None:
                return False, "\u672a\u627e\u5230\u63d0\u4ea4\u6309\u9215", False

            submit.click()
            page.wait_for_timeout(4000)

            cur_url = page.url
            print(f"[ip2free] \u5f53\u524d URL: {cur_url}", flush=True)

            ok_kws = ["/dashboard", "/home", "/cn/home", "/user", "/cn/login", "/login"]
            if any(k in cur_url for k in ok_kws):
                print("[ip2free] \u2705 \u6ce8\u518c\u6210\u529f\uff08URL \u8df3\u8f6c\uff09", flush=True)
                return True, f"\u6ce8\u518c\u6210\u529f | email={outlook_email}", False

            try:
                page.wait_for_selector(".MuiAlert-standardSuccess,[role=\"alert\"]", timeout=5000)
                txt = page.locator('[role="alert"]').first.inner_text()
                if "success" in txt.lower() or "\u6210\u529f" in txt:
                    return True, f"\u6ce8\u518c\u6210\u529f | email={outlook_email}", False
                return False, f"\u63d0\u793a: {txt[:100]}", False
            except Exception:
                pass

            try:
                page.screenshot(path=f"/tmp/ip2free_result_{int(time.time())}.png")
            except Exception:
                pass
            return False, f"\u7ed3\u679c\u4e0d\u786e\u5b9a URL={cur_url}", False

    except Exception as e:
        import traceback
        print(f"[ip2free] \u5f02\u5e38:\n{traceback.format_exc()}", flush=True)
        proxy_err = any(k in str(e).lower() for k in ["proxy","connect","timeout","refused","reset"])
        return False, f"\u5f02\u5e38: {e}", proxy_err
    finally:
        from proxy_chain import stop_relays
        stop_relays()


def register_ip2free_adaptive(
    outlook_email, outlook_password, ip2free_password,
    manual_proxies=None, invite_code=DEFAULT_INVITE,
    headless=True, access_token="", auto_proxy=True
):
    """
    \u81ea\u9002\u5e94\u591a\u6c60\u4ee3\u7406\u94fe\u8def\u6ce8\u518c\u3002
    \u81ea\u52a8\u6309 ip2free \u7528\u9014\u4f18\u5148\u7ea7\u9009\u53d6\uff1a
      Pool-C (Webshare HTTP) \u2192 Pool-B (subnode SOCKS5) \u2192 Pool-A (local SOCKS5) \u2192 \u76f4\u8fde
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from proxy_chain import ProxyChain

    chain = ProxyChain(
        purpose="ip2free" if auto_proxy else "generic",
        count=5 if auto_proxy else 1,
        extra=manual_proxies or [],
    )

    print(f"[ip2free] \u4ee3\u7406\u94fe\u8def\u51c6\u5907: {len(chain)} \u4e2a\u9009\u9879", flush=True)

    for idx, proxy in enumerate(chain):
        label = proxy[:50] if proxy else "\u76f4\u8fde\uff08\u65e0\u4ee3\u7406\uff09"
        print(f"[ip2free] \u5c1d\u8bd5 [{idx+1}/{len(chain)}]: {label}", flush=True)

        success, msg, is_proxy_err = try_register_with_proxy(
            outlook_email, outlook_password, ip2free_password,
            proxy, invite_code, headless, access_token
        )

        if success:
            return True, msg

        print(f"[ip2free] \u5931\u8d25: {msg}", flush=True)
        if is_proxy_err:
            chain.mark_failed(proxy)
            print(f"[ip2free] \u4ee3\u7406\u9510\u8d25\uff0c\u5207\u6362\u4e0b\u4e00\u4e2a\u2026", flush=True)
            continue
        else:
            # \u975e\u4ee3\u7406\u9519\u8bef\uff08\u9a8c\u8bc1\u7801/\u9875\u9762\u95ee\u9898\uff09\u2014 \u4e0d\u5fc5\u5207\u6362\u4ee3\u7406
            return False, msg

    return False, "\u6240\u6709\u4ee3\u7406\u5747\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5 Webshare \u8d26\u53f7\u72b6\u6001"


def main():
    parser = argparse.ArgumentParser(description="ip2free.com \u6ce8\u518c")
    parser.add_argument("--email",            required=True)
    parser.add_argument("--outlook-password", default="")
    parser.add_argument("--access-token",     default="")
    parser.add_argument("--ip2free-password", default="")
    parser.add_argument("--proxy",            default="",  help="\u5355\u4e2a\u624b\u52a8\u4ee3\u7406")
    parser.add_argument("--proxies",          default="",  help="\u591a\u4ee3\u7406\u9017\u53f7\u5206\u9694")
    parser.add_argument("--no-auto-proxy",    action="store_true", help="\u7981\u7528 DB \u81ea\u9002\u5e94\u9009\u53d6")
    parser.add_argument("--invite-code",      default=DEFAULT_INVITE)
    parser.add_argument("--headless",         default="true")
    args = parser.parse_args()

    ip2free_pwd = args.ip2free_password or gen_ip2free_password(args.outlook_password or "Aa123456")
    headless    = args.headless.lower() not in ("false", "0", "no")
    auto_proxy  = not args.no_auto_proxy

    # \u6574\u5408\u624b\u52a8\u4ee3\u7406\u5217\u8868
    manual = []
    if args.proxies:
        manual.extend([p.strip() for p in args.proxies.split(",") if p.strip()])
    if args.proxy:
        manual.insert(0, args.proxy)

    print(f"[ip2free] \u5f00\u59cb | email={args.email} | \u624b\u52a8={len(manual)} | auto={auto_proxy}", flush=True)

    success, msg = register_ip2free_adaptive(
        outlook_email=args.email,
        outlook_password=args.outlook_password,
        ip2free_password=ip2free_pwd,
        manual_proxies=manual,
        invite_code=args.invite_code,
        headless=headless,
        access_token=args.access_token,
        auto_proxy=auto_proxy,
    )

    result = {
        "success":          success,
        "email":            args.email,
        "ip2free_password": ip2free_pwd if success else "",
        "message":          msg,
    }
    print("\n\u2500\u2500 JSON \u7ed3\u679c \u2500\u2500")
    print(json.dumps([result], ensure_ascii=False, indent=2))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
