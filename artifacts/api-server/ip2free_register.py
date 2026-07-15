#!/usr/bin/env python3
"""
ip2free.com 注册脚本 v3.1 — 与前端邮件中心联动，自动从 AI Account Toolkit 的
Outlook 账号池里解析 account_id / 选取可用账号，并用 Graph API 读取验证码。

用法:
    python3 ip2free_register.py
        [--email user@outlook.com]          # 可选；不传则自动从邮件中心挑可用账号
        [--account-id 592]                   # 可选；不传则按 email 自动查找
        [--outlook-password OutlookPwd]      # 可选
        [--ip2free-password Ip2freePwd123]   # 可选
        [--api-base http://localhost:8081]   # Toolkit API 地址
        [--proxy http://user:pass@host:port]
        [--proxies p1,p2,p3]
        [--no-auto-proxy]
        [--invite-code 7pdC4VeeYw]
        [--headless true]
"""

import argparse, json, os, re, sqlite3, subprocess, sys, time, urllib.parse, urllib.request

REGISTER_URL   = "https://www.ip2free.com/register"
DEFAULT_INVITE = "7pdC4VeeYw"
CODE_WAIT_SEC  = 120
# 默认连本地 Toolkit API（nginx 反代到 8081），可通过环境变量或 --api-base 覆盖
LOCAL_API      = os.environ.get("TOOLKIT_API", "http://localhost:8081")
DEFAULT_ENV_FILE = "/data/Toolkit/.ip2free_proxy.env"
DEFAULT_DB_PATH  = "/data/api-server/data.db"


def gen_ip2free_password(base: str) -> str:
    """生成符合格式的随机密码（不使用固定前缀，避免批量特征）"""
    import random, string
    upper  = random.choices(string.ascii_uppercase, k=2)
    digits = random.choices(string.digits, k=2)
    lower  = random.choices(string.ascii_lowercase, k=6)
    parts  = upper + digits + lower
    random.shuffle(parts)
    return "".join(parts)


_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.85 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.116 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.6668.89 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.108 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.91 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.137 Safari/537.36",
]

_VIEWPORT_POOL = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
    {"width": 1600, "height": 900},
]


def _rand_ua():
    import random
    return random.choice(_UA_POOL)


def _rand_viewport():
    import random
    vp = random.choice(_VIEWPORT_POOL).copy()
    vp["width"]  += random.randint(-3, 3)
    vp["height"] += random.randint(-2, 2)
    return vp


def _human_type(page, selector: str, text: str, min_ms: int = 40, max_ms: int = 120):
    """模拟人工逐字输入，带随机延迟"""
    import random
    loc = page.locator(selector)
    loc.click()
    page.wait_for_timeout(random.randint(100, 300))
    for ch in text:
        loc.press_sequentially(ch, delay=random.randint(min_ms, max_ms))


def toolkit_api(method: str, path: str, payload=None, timeout: int = 30):
    """调用 AI Account Toolkit API（与前端 MailCenter 同源）"""
    base = LOCAL_API.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    url = f"{base}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if method == "GET" and payload is not None:
        raise ValueError("GET 请求不能带 payload")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    r = urllib.request.urlopen(req, timeout=timeout)
    raw = r.read()
    if not raw:
        return {}
    return json.loads(raw)


def find_account_by_email(email: str) -> tuple[int, str, str] | None:
    """按邮箱在邮件中心查找 Outlook 账号，返回 (id, email, password)。"""
    q = urllib.parse.quote(email.lower())
    d = toolkit_api("GET", f"/api/tools/outlook/accounts?search={q}&limit=10&status=active")
    if not d.get("success"):
        return None
    for acc in d.get("accounts", []):
        if acc.get("email", "").lower() == email.lower():
            return acc["id"], acc["email"], acc.get("password", "")
    return None


def pick_available_account() -> tuple[int, str, str] | None:
    """从邮件中心挑选一个可用 Outlook 账号。优先 inbox_verified 且未被 replit_used 的。"""
    d = toolkit_api("GET", "/api/tools/outlook/accounts?status=active&limit=50")
    if not d.get("success"):
        return None
    accounts = d.get("accounts", [])
    for acc in accounts:
        tags = acc.get("tags", "") or ""
        if "inbox_verified" in tags and "replit_used" not in tags:
            return acc["id"], acc["email"], acc.get("password", "")
    if accounts:
        acc = accounts[0]
        return acc["id"], acc["email"], acc.get("password", "")
    return None


def resolve_account(email: str | None, account_id: int) -> tuple[int, str, str]:
    """
    解析最终要使用的 (account_id, email, password)。
    规则：
      1. 如果提供了 account_id，优先信任它；若同时提供了 email 会校验是否匹配。
      2. 否则如果提供了 email，按 email 查找。
      3. 否则自动从邮件中心挑一个可用账号。
    """
    if account_id > 0:
        # 按 id 反查，确保存在且有效
        d = toolkit_api("GET", f"/api/tools/outlook/accounts?limit=500")
        if d.get("success"):
            for acc in d.get("accounts", []):
                if acc["id"] == account_id:
                    found_email = acc["email"]
                    if email and email.lower() != found_email.lower():
                        print(f"[mail-center] 警告：传入 email({email}) 与 account_id({account_id}) 对应邮箱({found_email}) 不一致，已自动对齐为账号邮箱",
                              flush=True)
                    return account_id, found_email, acc.get("password", "")
        # 反查失败但还有 email，按 email 兜底
        if email:
            found = find_account_by_email(email)
            if found:
                print(f"[mail-center] account_id={account_id} 查无此账号，已按 email={email} 对齐为 id={found[0]}", flush=True)
                return found
        raise RuntimeError(f"account_id={account_id} 在邮件中心不存在")

    if email:
        found = find_account_by_email(email)
        if found:
            print(f"[mail-center] 已按 email={email} 联动到 account_id={found[0]}", flush=True)
            return found
        raise RuntimeError(f"邮件中心找不到可用账号: {email}")

    found = pick_available_account()
    if found:
        print(f"[mail-center] 已自动选取可用账号: {found[1]} (id={found[0]})", flush=True)
        return found

    raise RuntimeError("邮件中心没有可用 Outlook 账号")


def fetch_verification_code_graph(account_id: int, timeout_s: int = CODE_WAIT_SEC) -> str | None:
    """
    通过 Toolkit 的 Graph API 代理读取 ip2free 验证邮件中的 6 位数字验证码。
    同时轮询 inbox 和 junkemail，防止验证码被过滤。
    """
    deadline = time.time() + timeout_s
    attempt  = 0
    print(f"[graph] 等待 ip2free 验证邮件（最多 {timeout_s}s，account_id={account_id}）…", flush=True)

    while time.time() < deadline:
        attempt += 1
        for folder in ["inbox", "junkemail"]:
            try:
                payload = {
                    "accountId": account_id,
                    "folder":    folder,
                    "top":       15,
                }
                d = toolkit_api("POST", "/api/tools/outlook/fetch-messages-by-id", payload)
                msgs = d.get("messages", [])
                via  = d.get("via", "?")
                if msgs:
                    print(f"[graph] 第{attempt}次 [{folder}]: {len(msgs)} 封邮件 via={via}", flush=True)
                # 优先识别 ip2free / 注册验证码相关邮件；忽略微软服务连接通知等杂信
                # 只认 ip2free 注册验证码邮件，防止 Replit / Microsoft 等杂信干扰
                def _is_ip2free_email(text: str, subj: str) -> bool:
                    t = (text + " " + subj).lower()
                    if "ip2free" in t:
                        return True
                    if "注册验证码" in subj:
                        return True
                    if "注册" in subj and "验证码" in subj:
                        return True
                    if "ip2free" in t and "验证码" in t:
                        return True
                    return False

                def _is_noise_email(text: str, subj: str) -> bool:
                    t = (text + " " + subj).lower()
                    noise = [
                        "replit", "microsoft account", "new app(s) connected", "security code",
                        "安全代码", "verify your email", "confirm your email", "device code",
                    ]
                    return any(k in t for k in noise)

                ip2free_msgs = []
                for msg in msgs:
                    subj = msg.get("subject", "")
                    prev = msg.get("preview", "")
                    body = msg.get("body", "")
                    text = f"{subj} {prev} {body}"
                    if _is_noise_email(text, subj):
                        continue
                    if _is_ip2free_email(text, subj):
                        ip2free_msgs.append(msg)

                if ip2free_msgs:
                    # 取最新一封 ip2free 邮件，提取 6 位验证码
                    msg = ip2free_msgs[0]
                    subj = msg.get("subject", "")
                    prev = msg.get("preview", "")
                    body = msg.get("body", "")
                    text = f"{subj} {prev} {body}"
                    codes = re.findall(r"\b(\d{6})\b", text)
                    if codes:
                        print(f"[graph] 验证码: {codes[0]} [{folder}] (来自: {subj[:60]})", flush=True)
                        return codes[0]
                    else:
                        print(f"[graph] 已找到 ip2free 邮件但无 6 位验证码 [{folder}]: {subj[:60]}", flush=True)
                else:
                    # 如果本轮都是杂信，打印出来便于排查
                    noise_names = [m.get("subject", "")[:40] for m in msgs]
                    if noise_names:
                        print(f"[graph] 第{attempt}次 [{folder}]: 未找到 ip2free 邮件，当前 {len(msgs)} 封: {noise_names}", flush=True)
            except Exception as e:
                print(f"[graph] 异常 [{folder}]: {e}", flush=True)
        time.sleep(10)

    print("[graph] 超时未收到验证码", flush=True)
    return None


def try_register_with_proxy(
    outlook_email, ip2free_password,
    proxy, invite_code, headless, account_id
):
    """
    单次代理尝试注册。返回 (success, message, is_proxy_error)。
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
            import random as _rnd
            _ua = _rand_ua()
            _vp = _rand_viewport()
            launch_kwargs = dict(
                headless=headless,
                args=[
                    "--lang=zh-CN,zh,en-US,en",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-first-run",
                    "--ignore-certificate-errors",
                    "--enforce-webrtc-ip-permission-check",
                    "--disable-webrtc-encryption",
                    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-extensions",
                    "--disable-hang-monitor",
                    "--disable-popup-blocking",
                    "--disable-prompt-on-repost",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--password-store=basic",
                    "--use-mock-keychain",
                ],
            )
            if proxy_cfg:
                launch_kwargs["proxy"] = proxy_cfg
            browser = p.chromium.launch(**launch_kwargs)
            ctx  = browser.new_context(
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport=_vp,
                user_agent=_ua,
                java_script_enabled=True,
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

            import random as _rnd2
            for fid, val in [("#email", outlook_email), ("#password", ip2free_password)]:
                page.evaluate(f"el=document.querySelector(\"{fid}\");if(el){{el.removeAttribute('readonly');}}")
                _human_type(page, fid, val)
                page.wait_for_timeout(_rnd2.randint(200, 500))

            try:
                aff = page.locator("#affId")
                if aff.count() > 0:
                    page.evaluate("el=document.querySelector(\"#affId\");if(el){el.removeAttribute('readonly');}")
                    aff.fill(invite_code)
                    page.wait_for_timeout(200)
            except Exception:
                pass

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
            print("[ip2free] 已点击「获取验证码」，开始轮询 Graph API…", flush=True)

            code = fetch_verification_code_graph(account_id, timeout_s=CODE_WAIT_SEC)
            if not code:
                try:
                    page.screenshot(path=f"/tmp/ip2free_nocode_{int(time.time())}.png")
                except Exception:
                    pass
                return False, "验证码等待超时（Graph API 未收到 ip2free 邮件）", False

            # 填验证码（兼容 Ant Design / 原生 input 多种实现）
            def _fill_code_input(p, code):
                selectors = [
                    'input[id="code"]',
                    '#code input',
                    '#code .ant-input',
                    '.ant-input-otp input',
                    '.ant-otp-input',
                    'input[placeholder*="验证码"]',
                    'input[placeholder*="code" i]',
                    'input[name="code"]',
                    'input[aria-label*="code" i]',
                    'input[autocomplete="one-time-code"]',
                ]
                for sel in selectors:
                    try:
                        loc = p.locator(sel).first
                        if loc.count() > 0 and loc.is_visible(timeout=2000):
                            loc.fill(code)
                            return True
                    except Exception:
                        pass
                # Ant Design OTP / 可聚焦 div：点击后键盘输入
                try:
                    div = p.locator("#code")
                    if div.count() > 0 and div.is_visible(timeout=2000):
                        div.click()
                        p.keyboard.type(code, delay=30)
                        return True
                except Exception:
                    pass
                # 兜底：JS 直接赋值并触发 input 事件
                try:
                    filled = p.evaluate(f"""
                        (function(){{
                            var inputs = document.querySelectorAll('#code input, .ant-input-otp input, .ant-otp-input, input[name=\"code\"]');
                            var ev = new Event('input', {{ bubbles: true }});
                            var kev = new Event('keyup', {{ bubbles: true }});
                            inputs.forEach(function(el){{
                                el.value = '{code}'; el.dispatchEvent(ev); el.dispatchEvent(kev);
                            }});
                            var div = document.querySelector('#code');
                            if (div && div.isContentEditable) {{
                                div.innerText = '{code}'; div.dispatchEvent(ev);
                            }}
                            return inputs.length;
                        }})()
                    """)
                    return filled > 0
                except Exception:
                    return False

            if not _fill_code_input(page, code):
                raise RuntimeError("无法填写验证码输入框")
            page.wait_for_timeout(400)

            try:
                cb = page.locator('input[type="checkbox"]').first
                if cb.count() > 0 and not cb.is_checked():
                    cb.click()
                    page.wait_for_timeout(200)
            except Exception:
                pass

            page.wait_for_timeout(800)

            submit_clicked = False
            submit_selectors = [
                'button[type="submit"]',
                'button.MuiButton-sizeLarge',
                'button:has-text("注册")',
                'button:has-text("立即注册")',
                'button:has-text("Register")',
                'button:has-text("Sign up")',
                'button.ant-btn-primary',
                'button:has-text("创建账号")',
            ]
            for sel in submit_selectors:
                try:
                    btn = page.locator(sel)
                    cnt = btn.count()
                    if cnt > 0:
                        # 先尝试 JS 点击，避免 scroll/visibility 限制
                        try:
                            page.evaluate(f"""
                                (function(){{
                                    var btns = document.querySelectorAll('{sel}');
                                    var t = btns.length > 1 ? btns[btns.length - 1] : (btns[0] || null);
                                    if (t) {{
                                        t.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                                        t.focus();
                                        t.click();
                                        return 1;
                                    }}
                                    return 0;
                                }})()
                            """)
                            submit_clicked = True
                            print(f"[ip2free] JS 点击提交 ({sel} × {cnt})", flush=True)
                            break
                        except Exception as e_click:
                            print(f"[ip2free] JS 点击失败 {sel}: {e_click}", flush=True)
                        # JS 失败再用 Playwright 点击兜底
                        try:
                            target = btn.last if "sizeLarge" in sel or "submit" in sel else btn.first
                            target.scroll_into_view_if_needed(timeout=3000)
                            target.click(timeout=8000, force=True)
                            submit_clicked = True
                            print(f"[ip2free] 点击提交 ({sel} × {cnt})", flush=True)
                            break
                        except Exception as e2:
                            print(f"[ip2free] 提交选择器 {sel} Playwright 点击异常: {e2}", flush=True)
                except Exception as e2:
                    print(f"[ip2free] 提交选择器 {sel} 异常: {e2}", flush=True)

            if not submit_clicked:
                return False, "未找到提交按钮", False

            page.wait_for_timeout(5000)

            cur_url = page.url
            print(f"[ip2free] 当前 URL: {cur_url}", flush=True)

            ok_kws = ["/dashboard", "/home", "/cn/home", "/user", "/cn/login", "/login", "/success"]
            if any(k in cur_url for k in ok_kws):
                print("[ip2free] 注册成功（URL 跳转）", flush=True)
                return True, f"注册成功 | email={outlook_email}", False

            # 收集页面可见文本 / 提示，优先 Ant Design / MUI 组件
            error_texts = []
            try:
                page.wait_for_timeout(2000)
                selectors = [
                    '.ant-alert-message',
                    '.ant-alert-description',
                    '.ant-message-notice-content',
                    '.ant-form-item-explain-error',
                    '.ant-notification-notice-description',
                    '.MuiAlert-message',
                    '.MuiAlert-standardError',
                    '.MuiAlert-standardWarning',
                    '[role="alert"]',
                    '.error-message',
                    '.text-error',
                    '.text-danger',
                ]
                for sel in selectors:
                    try:
                        els = page.locator(sel).all()
                        for el in els:
                            txt = el.inner_text().strip()
                            if txt and len(txt) > 1:
                                error_texts.append(txt)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[ip2free] 提取提示文本异常: {e}", flush=True)

            if error_texts:
                joined = " | ".join(error_texts[:3])
                print(f"[ip2free] 页面提示: {joined}", flush=True)
                return False, f"提示: {joined}", False

            # 兜底：检查是否出现成功提示文字
            try:
                page_text = page.content().lower()
                if "success" in page_text or "成功" in page_text or "注册成功" in page_text:
                    return True, f"注册成功 | email={outlook_email}", False
            except Exception:
                pass

    except Exception as e:
        import traceback
        print(f"[ip2free] 异常:\n{traceback.format_exc()}", flush=True)
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



def is_in_env_file(email: str, path: str = DEFAULT_ENV_FILE) -> bool:
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().lower().startswith(f"ip2free_email={email.lower()}"):
                    return True
    except Exception:
        pass
    return False


def append_to_env_file(email: str, password: str, path: str = DEFAULT_ENV_FILE) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\nIP2FREE_EMAIL={email}\nIP2FREE_PASSWORD={password}\n")
        print(f"[persist] appended to env file: {email}", flush=True)
    except Exception as e:
        print(f"[persist] env append error: {e}", flush=True)


def save_to_accounts_db(email: str, password: str, invite_code: str = "",
                        db_path: str = DEFAULT_DB_PATH) -> None:
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        db = sqlite3.connect(db_path)
        db.execute("""
            CREATE TABLE IF NOT EXISTS ai_accounts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                service    TEXT NOT NULL DEFAULT 'ip2free',
                email      TEXT NOT NULL,
                api_key    TEXT,
                status     TEXT NOT NULL DEFAULT 'active',
                notes      TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(service, email)
            )
        """)
        notes = json.dumps({"password": password, "invite_code": invite_code}, ensure_ascii=False)
        db.execute("""
            INSERT INTO ai_accounts (service, email, api_key, status, notes)
            VALUES ('ip2free', ?, ?, 'active', ?)
            ON CONFLICT(service, email) DO UPDATE SET
                api_key=excluded.api_key, status='active',
                notes=excluded.notes, updated_at=datetime('now')
        """, (email, invite_code or "", notes))
        db.commit()
        db.close()
        print(f"[persist] saved to db: {email}", flush=True)
    except Exception as e:
        print(f"[persist] db save error: {e}", flush=True)


def trigger_proxy_sync(env_file: str = DEFAULT_ENV_FILE) -> None:
    sync_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ip2free_proxy_sync.py")
    try:
        r = subprocess.run(
            [sys.executable, sync_script, "--probe", "--env-file", env_file],
            capture_output=True, text=True, timeout=300, cwd=os.path.dirname(sync_script)
        )
        lines = [l for l in r.stdout.strip().splitlines() if l.strip().startswith("{")]
        if lines:
            print(f"[sync] triggered, summary={lines[-1]}", flush=True)
        else:
            print(f"[sync] triggered, stdout={r.stdout[-500:]}", flush=True)
    except Exception as e:
        print(f"[sync] trigger error: {e}", flush=True)


def mark_outlook_used(account_id: int, api_base: str = LOCAL_API) -> None:
    """将 Outlook 账号标记为 replit_used，避免下次注册再次选中同一邮箱。"""
    if account_id <= 0:
        return
    try:
        toolkit_api("POST", f"/api/tools/outlook/accounts/{account_id}/tag", {"tags": ["replit_used"]})
        print(f"[mail-center] 账号 {account_id} 已标记为 replit_used", flush=True)
    except Exception as e:
        print(f"[mail-center] 标记 replit_used 失败: {e}", flush=True)


def main():
    global LOCAL_API

    parser = argparse.ArgumentParser(description="ip2free.com 注册 — 与 AI Account Toolkit 邮件中心联动")
    parser.add_argument("--email",            default="", help="Outlook 邮箱；不传则自动从邮件中心挑选")
    parser.add_argument("--account-id",       type=int, default=0,
                        help="Outlook 账号在 DB 中的 id（用于 Graph API 读取验证码）；不传则自动解析")
    parser.add_argument("--outlook-password", default="")
    parser.add_argument("--ip2free-password", default="")
    parser.add_argument("--api-base",         default=LOCAL_API,
                        help="Toolkit API 基地址，默认 http://localhost:8081")
    parser.add_argument("--proxy",            default="",  help="单个手动代理")
    parser.add_argument("--proxies",          default="",  help="多代理逗号分隔")
    parser.add_argument("--account-proxy",    default="",  help="账号注册时绑定的代理URL（IP一致性）")
    parser.add_argument("--no-auto-proxy",    action="store_true", help="禁用 DB 自适应选取")
    parser.add_argument("--invite-code",      default=DEFAULT_INVITE)
    parser.add_argument("--headless",         default="true")
    parser.add_argument("--env-file",         default=DEFAULT_ENV_FILE, help="Path to .ip2free_proxy.env")
    parser.add_argument("--no-sync",          action="store_true", help="Do not trigger ip2free_proxy_sync after success")
    parser.add_argument("--no-save",          action="store_true", help="Do not save credentials to env/DB")
    args = parser.parse_args()

    LOCAL_API = args.api_base or LOCAL_API
    print(f"[mail-center] API 基地址: {LOCAL_API}", flush=True)

    # 与前端邮件中心联动：解析账号
    account_id, outlook_email, outlook_password = resolve_account(args.email or None, args.account_id)
    print(f"[mail-center] 最终使用账号: {outlook_email} (id={account_id})", flush=True)

    ip2free_pwd = args.ip2free_password or gen_ip2free_password(args.outlook_password or outlook_password or "Aa123456")
    headless    = args.headless.lower() not in ("false", "0", "no")
    auto_proxy  = not args.no_auto_proxy

    manual = []
    if args.proxies:
        manual.extend([p.strip() for p in args.proxies.split(",") if p.strip()])
    if args.proxy:
        manual.insert(0, args.proxy)

    print(f"[ip2free] 开始 | email={outlook_email} account_id={account_id} | 手动={len(manual)} | auto={auto_proxy}", flush=True)

    success, msg = register_ip2free_adaptive(
        outlook_email=outlook_email,
        ip2free_password=ip2free_pwd,
        manual_proxies=manual,
        invite_code=args.invite_code,
        headless=headless,
        auto_proxy=auto_proxy,
        account_proxy=args.account_proxy,
        account_id=account_id,
    )

    if success and not args.no_save:
        if not is_in_env_file(outlook_email, args.env_file):
            append_to_env_file(outlook_email, ip2free_pwd, args.env_file)
        save_to_accounts_db(outlook_email, ip2free_pwd, args.invite_code)
        mark_outlook_used(account_id, args.api_base)

    if success and not args.no_sync and not args.no_save:
        trigger_proxy_sync(args.env_file)

    result = {
        "success":          success,
        "email":            outlook_email,
        "account_id":       account_id,
        "ip2free_password": ip2free_pwd if success else "",
        "message":          msg,
    }
    print("\n── JSON 结果 ──")
    print(json.dumps([result], ensure_ascii=False, indent=2))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
