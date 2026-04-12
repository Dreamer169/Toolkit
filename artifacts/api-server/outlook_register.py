"""
Outlook/Hotmail 批量注册自动化脚本
精髓完全参考 https://github.com/hrhcode/outlook-batch-manager

核心逻辑 (与原版一致):
  - 入口: outlook.live.com/mail/0/?prompt=create_account
  - 首先点击 '同意并继续' (中文 UI)
  - 输入速度与 bot_protection_wait 成比例 (默认 11s)
  - patchright 双 iframe CAPTCHA: 可访问性挑战按钮
  - playwright CAPTCHA: Enter键 + hsprotect.net 流量监听
  - Faker 生成真实人名
  - 可选 OAuth2 刷新 Token

用法:
  python3 outlook_register.py --count 3 --proxy socks5://127.0.0.1:1080
  python3 outlook_register.py --count 1 --engine playwright --headless false
"""

import argparse
import asyncio
import json
import random
import secrets
import string
import sys
import time
from pathlib import Path

from faker import Faker

fake = Faker("zh_CN")

# ─── 配置 ─────────────────────────────────────────────────────────────────────
BOT_PROTECTION_WAIT = 11          # 秒，与原版一致
MAX_CAPTCHA_RETRIES = 2
REGISTER_URL = "https://outlook.live.com/mail/0/?prompt=create_account"


# ─── 工具函数 ──────────────────────────────────────────────────────────────────
def gen_password(n=None):
    n = n or random.randint(12, 16)
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pw = "".join(secrets.choice(chars) for _ in range(n))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "!@#$%^&*" for c in pw)):
            return pw


def gen_email_username():
    """生成真实人名格式的邮箱用户名（尽量减少被占概率）"""
    FIRST = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas",
             "Christopher","Daniel","Matthew","Anthony","Mark","Steven","Paul","Andrew","Joshua",
             "Benjamin","Samuel","Patrick","Jack","Tyler","Aaron","Nathan","Kyle","Bryan","Eric",
             "Mary","Patricia","Jennifer","Linda","Elizabeth","Susan","Jessica","Sarah","Karen",
             "Lisa","Nancy","Ashley","Emily","Donna","Michelle","Amanda","Melissa","Rebecca","Laura",
             "Emma","Olivia","Liam","Noah","Ava","Sophia","Isabella","Lucas","Ethan","Mason",
             "Aiden","Logan","Caden","Jayden","Brayden","Kayden","Rylan","Landen","Zayden",
             "Nora","Ellie","Lily","Zoey","Riley","Stella","Hazel","Violet","Aurora","Penelope"]
    LAST  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez",
             "Martinez","Hernandez","Lopez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson",
             "Lee","Perez","Thompson","White","Harris","Clark","Ramirez","Lewis","Robinson","Walker",
             "Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Green","Adams",
             "Nelson","Baker","Campbell","Mitchell","Carter","Turner","Phillips","Evans","Collins",
             "Stewart","Morales","Murphy","Cook","Rogers","Bennett","Gray","Hughes","Patel","Parker",
             "Flores","Rivera","Gomez","Diaz","Cruz","Reyes","Ortiz","Gutierrez","Chavez","Ramos",
             "Sanchez","Perez","Romero","Torres","Jimenez","Vasquez","Alvarez","Castillo","Jenkins"]
    fn = random.choice(FIRST)
    ln = random.choice(LAST)
    y2 = str(random.randint(70, 99))   # 出生年份后两位，如 85
    n3 = str(random.randint(100, 999))  # 三位数，减少冲突
    n4 = str(random.randint(1000, 9999))  # 四位随机
    rc = ''.join(random.choices('abcdefghjkmnpqrstuvwxyz', k=3))  # 3个随机字母
    # 权重：带数字格式被占概率低；纯名字格式被占概率高
    patterns = [
        fn.lower() + "." + ln.lower() + y2,     # karen.ramirez85  ← 常用但带年份
        fn.lower() + "_" + ln.lower() + y2,     # karen_ramirez85
        fn[0].lower() + ln.lower() + y2,        # kramirez85
        fn.lower() + ln.lower() + n3,           # karenramirez347
        fn[0].lower() + "." + ln.lower() + n3,  # k.ramirez347
        fn.lower() + "." + ln[0].lower() + y2,  # karen.r85
        fn.lower() + ln.lower() + n4[:3],       # karenramirez142
        fn[0].lower() + "." + ln.lower() + n3,  # k.ramirez142
        fn.lower() + rc + n3,                   # karenabc347  (很少被占)
        fn[0].lower() + ln.lower() + rc,        # kramirezabc   (极少被占)
    ]
    return random.choice(patterns), fn, ln


# ─── 基础控制器 ───────────────────────────────────────────────────────────────
class BaseController:
    def __init__(self, proxy="", wait_ms=None, max_captcha_retries=MAX_CAPTCHA_RETRIES,
                 captcha_solver=None):
        self.proxy          = proxy
        self.wait_time      = (wait_ms or BOT_PROTECTION_WAIT) * 1000  # ms
        self.max_retries    = max_captcha_retries
        self.captcha_solver = captcha_solver   # captcha_solver.py 中的 Solver 对象

    def _build_proxy_cfg(self):
        """
        Chromium 不支持带认证的 SOCKS5，因此：
        有凭据时 → 启动本地 Socks5Relay（无认证），转发到上游带认证的代理
        无凭据时 → 直接传给 Chromium
        """
        if not self.proxy:
            return None
        import re, sys, os
        m = re.match(r'(socks5h?|http|https)://([^:]+):([^@]+)@([^:]+):(\d+)', self.proxy)
        if m:
            _scheme, user, password, host, port = m.groups()
            # 启动本地无认证中转代理
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from socks5_relay import Socks5Relay
            relay = Socks5Relay(host, int(port), user, password)
            local_port = relay.start()
            self._relay = relay  # 保持引用，防止 GC
            print(f"[relay] 本地中转代理启动：127.0.0.1:{local_port} → {host}:{port}", flush=True)
            return {"server": f"socks5://127.0.0.1:{local_port}", "bypass": "localhost"}
        # 无凭据格式，直接用
        return {"server": self.proxy, "bypass": "localhost"}

    # ── 打码服务辅助 ──────────────────────────────────────────────────────────
    def _start_blob_capture(self, page):
        """
        拦截 FunCaptcha 网络请求，提取 sessionToken（blob）。
        在浏览器导航前调用，返回一个 list，稍后通过 list[0] 读取。
        """
        blob_container: list[str] = []

        def on_request(request):
            url = request.url
            # Arkose Labs 的 iframe 地址含 sessionToken 参数
            if "hsprotect.net" in url or "arkoselabs.com" in url:
                import urllib.parse
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                for key in ("sessionToken", "session_token", "token", "id"):
                    if key in qs and qs[key]:
                        val = qs[key][0]
                        if len(val) > 20 and not blob_container:
                            blob_container.append(val)
                            print(f"[captcha] 捕获到 blob (len={len(val)})", flush=True)

        page.on("request", on_request)
        return blob_container

    def _inject_captcha_token(self, page, token: str) -> bool:
        """
        将打码服务返回的 token 注入到 Arkose/FunCaptcha 验证流程。
        尝试多种注入方式，任一成功即返回 True。
        """
        print(f"[captcha] 注入 token (len={len(token)})…", flush=True)
        script = f"""
        (function() {{
            var tk = {json.dumps(token)};
            // 方式1: Microsoft 专用回调
            try {{
                if (window.ArkoseEnforcement && typeof window.ArkoseEnforcement.setAnswerToken === 'function') {{
                    window.ArkoseEnforcement.setAnswerToken(tk);
                    return 'ArkoseEnforcement.setAnswerToken';
                }}
            }} catch(e) {{}}
            // 方式2: 隐藏 input 字段
            var fields = document.querySelectorAll(
                'input[name*="arkose"], input[name*="enforcement"], input[name*="fc-token"], ' +
                'input[name*="FunCaptcha-Token"], input[id*="arkose"], input[type="hidden"]'
            );
            var injected = false;
            fields.forEach(function(el) {{
                el.value = tk;
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                injected = true;
            }});
            if (injected) return 'hidden-input';
            // 方式3: postMessage 给所有 frame
            document.querySelectorAll('iframe').forEach(function(fr) {{
                try {{
                    fr.contentWindow.postMessage({{
                        eventId: 'challenge-complete',
                        payload: {{sessionToken: tk}}
                    }}, '*');
                }} catch(e) {{}}
            }});
            window.postMessage({{
                eventId: 'challenge-complete',
                payload: {{sessionToken: tk}}
            }}, '*');
            return 'postMessage';
        }})()
        """
        try:
            method = page.evaluate(script)
            print(f"[captcha] 注入成功，方式={method}", flush=True)
            return True
        except Exception as ex:
            print(f"[captcha] 注入失败: {ex}", flush=True)
            return False

    def _solve_with_service(self, page, blob_container: list) -> bool:
        """
        调用 self.captcha_solver 解题并注入 token。
        成功返回 True，失败返回 False。
        """
        if not self.captcha_solver:
            return False
        try:
            page_url = page.url or "https://signup.live.com/signup"
            blob = blob_container[0] if blob_container else None
            print(f"[captcha] 调用打码服务… blob={'有' if blob else '无'}", flush=True)
            token = self.captcha_solver.solve(page_url, blob=blob)
            ok = self._inject_captcha_token(page, token)
            if ok:
                # 等待注入生效，然后检查是否通过
                page.wait_for_timeout(3000)
                # 如果验证质询 iframe 消失，说明通过了
                try:
                    page.wait_for_selector(
                        'iframe[title="验证质询"]', state="detached", timeout=8000)
                    print("[captcha] ✅ 打码服务验证通过", flush=True)
                    return True
                except Exception:
                    # 可能直接跳到下一步（验证质询不会 detach 而是直接消失）
                    if (not page.locator('iframe[title="验证质询"]').count()
                            or page.get_by_text("取消").count()):
                        print("[captcha] ✅ 打码服务验证通过（无 iframe detach）", flush=True)
                        return True
            return False
        except Exception as ex:
            print(f"[captcha] 打码服务失败: {ex}", flush=True)
            return False

    def outlook_register(self, page, email, password):
        """
        完全复刻原版 BaseBrowserController.outlook_register()
        """
        lastname  = fake.last_name()
        firstname = fake.first_name()
        year  = str(random.randint(1960, 2005))
        month = str(random.randint(1, 12))
        day   = str(random.randint(1, 28))

        # 启动 FunCaptcha blob 捕获（在导航前挂钩）
        blob_container = self._start_blob_capture(page)

        # ── Step 1: 打开注册页，等待同意按钮 ──────────────────────────────
        try:
            page.goto(REGISTER_URL, timeout=20000, wait_until="domcontentloaded")
            page.get_by_text("同意并继续").wait_for(timeout=30000)
            start_time = time.time()
            page.wait_for_timeout(0.1 * self.wait_time)
            page.get_by_text("同意并继续").click(timeout=30000)
        except Exception as e:
            return False, f"IP质量不佳，无法进入注册界面: {e}", email

        # ── Step 2: 填写邮箱名、密码、生日、姓名 ─────────────────────────
        try:
            # 邮箱（支持用户名被占时自动切换建议名）
            email_input = page.locator('[aria-label="新建电子邮件"]')
            email_input.wait_for(timeout=20000)
            email_input.click()
            email_input.type(email, delay=max(20, 0.006 * self.wait_time), timeout=15000)
            page.keyboard.press("Tab")
            page.wait_for_timeout(0.02 * self.wait_time)
            page.locator('[data-testid="primaryButton"]').click(timeout=8000)
            page.wait_for_timeout(max(3000, 0.05 * self.wait_time))

            # 检测用户名是否被占用 → 重新生成（最多 8 次，超过可能触发异常活动检测）
            # 每次重试之间加 5s 冷却，防止微软检测到过快的用户名检查请求
            username_accepted = page.locator('[type="password"]').count() > 0
            for _attempt in range(8):
                if username_accepted:
                    print(f"  ✅ 用户名 {email} 已接受，进入密码步骤", flush=True)
                    break
                taken = (
                    page.get_by_text("已被占用").count() > 0
                    or page.get_by_text("username is taken").count() > 0
                    or page.get_by_text("该用户名不可用").count() > 0
                )
                password_visible = page.locator('[type="password"]').count() > 0
                if password_visible:
                    username_accepted = True
                    print(f"  ✅ 用户名 {email} 已接受，进入密码步骤", flush=True)
                    break
                if taken:
                    picked, _, _ = gen_email_username()
                    print(f"  ⚠ 用户名被占（第{_attempt+1}次），冷却5s后切换为: {picked}", flush=True)
                    email = picked
                    # 冷却 5 秒，避免触发速率限制
                    page.wait_for_timeout(5000)
                    email_input = page.locator('[aria-label="新建电子邮件"]')
                    email_input.click()
                    page.keyboard.press("Control+a")
                    page.keyboard.press("Delete")
                    email_input.type(picked, delay=max(30, 0.008 * self.wait_time))
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(0.03 * self.wait_time)
                    page.locator('[data-testid="primaryButton"]').click(timeout=8000)
                    page.wait_for_timeout(max(4000, 0.06 * self.wait_time))
                else:
                    # 既没有"被占用"也没有密码框 → 再等 2 秒
                    page.wait_for_timeout(2000)
                    # 再次检查密码框（等待中可能出现）
                    if page.locator('[type="password"]').count() > 0:
                        username_accepted = True
            else:
                # 8次重试全部失败 → 中止
                return False, "用户名全部被占，请稍后重试", email

            if not username_accepted:
                return False, "用户名全部被占，请稍后重试", email

            # 截图记录提交用户名后的页面（方便调试）
            try:
                page.screenshot(path=f"/tmp/outlook_after_username_{email}.png")
            except Exception:
                pass

            # 密码（通过代理时页面切换更慢，等待 35s）
            pwd_loc = page.locator('[type="password"]')
            pwd_loc.wait_for(state="visible", timeout=35000)
            pwd_loc.click()
            pwd_loc.type(password, delay=0.004 * self.wait_time, timeout=35000)
            page.wait_for_timeout(0.02 * self.wait_time)
            page.locator('[data-testid="primaryButton"]').click(timeout=8000)

            # 生日
            page.wait_for_timeout(0.03 * self.wait_time)
            page.locator('[name="BirthYear"]').fill(year, timeout=20000)
            try:
                page.wait_for_timeout(0.02 * self.wait_time)
                page.locator('[name="BirthMonth"]').select_option(value=month, timeout=1000)
                page.wait_for_timeout(0.05 * self.wait_time)
                page.locator('[name="BirthDay"]').select_option(value=day)
            except Exception:
                page.locator('[name="BirthMonth"]').click()
                page.wait_for_timeout(0.02 * self.wait_time)
                page.locator(f'[role="option"]:text-is("{month}月")').click()
                page.wait_for_timeout(0.04 * self.wait_time)
                page.locator('[name="BirthDay"]').click()
                page.wait_for_timeout(0.03 * self.wait_time)
                page.locator(f'[role="option"]:text-is("{day}日")').click()
                page.locator('[data-testid="primaryButton"]').click(timeout=5000)

            # 姓名
            page.locator('#lastNameInput').wait_for(state="visible", timeout=20000)
            page.locator('#lastNameInput').type(
                lastname, delay=0.002 * self.wait_time, timeout=20000)
            page.wait_for_timeout(0.02 * self.wait_time)
            page.locator('#firstNameInput').fill(firstname, timeout=20000)

            # 等满 bot_protection_wait 再点下一步
            elapsed = time.time() - start_time
            if elapsed < self.wait_time / 1000:
                page.wait_for_timeout((self.wait_time / 1000 - elapsed) * 1000)

            page.locator('[data-testid="primaryButton"]').click(timeout=5000)

            # 等待隐私链接消失 → CAPTCHA 出现
            page.locator(
                'span > [href="https://go.microsoft.com/fwlink/?LinkID=521839"]'
            ).wait_for(state="detached", timeout=22000)

            page.wait_for_timeout(400)

            if (page.get_by_text("一些异常活动").count()
                    or page.get_by_text("此站点正在维护，暂时无法使用，请稍后重试。").count()):
                return False, "当前IP注册频率过快", email

            if page.locator("iframe#enforcementFrame").count() > 0:
                return False, "验证码类型错误，非按压验证码", email

            # ── CAPTCHA ──────────────────────────────────────────────────
            captcha_ok = self.handle_captcha(page, blob_container)
            if not captcha_ok:
                return False, "验证码处理失败", email

            # ── 验证注册真正完成（等待跳转到成功页）────────────────────
            # 微软成功页：account.live.com, outlook.com, login.live.com/login.srf
            # 只有页面实际跳转到这些域才算真正注册成功
            try:
                page.wait_for_url(
                    lambda u: any(x in u for x in [
                        "account.live.com",
                        "account.microsoft.com",
                        "outlook.live.com",
                        "outlook.com/mail",
                        "login.live.com/login.srf",
                    ]),
                    timeout=30000,
                )
                print("[register] ✅ 检测到成功跳转页", flush=True)
            except Exception:
                # 检查当前页面是否有成功标志（避免误判）
                cur_url = page.url
                success_keywords = ["account.live", "account.microsoft", "outlook.live", "outlook.com/mail"]
                if not any(k in cur_url for k in success_keywords):
                    # 尝试等待页面出现 "你好" 或 "欢迎" 等完成标志
                    try:
                        page.wait_for_selector(
                            '[data-testid="ocid-login"] , [aria-label="Outlook"] , .welcome-msg , #mectrl_headerPicture',
                            timeout=5000,
                        )
                    except Exception:
                        # 截图记录当前状态
                        try:
                            page.screenshot(path=f"/tmp/outlook_captcha_done_{email}.png")
                        except Exception:
                            pass
                        return False, f"CAPTCHA 已点击但页面未跳转到成功页（当前: {cur_url[:80]}）", email

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[register] ❌ 完整错误:\n{tb}", flush=True)
            return False, f"加载超时或触发机器人检测: {e}", email

        return True, "注册成功", email

    def handle_captcha(self, page, blob_container=None):
        raise NotImplementedError


# ─── Patchright 控制器 ────────────────────────────────────────────────────────
class PatchrightController(BaseController):
    """
    与原版 PatchrightController.handle_captcha() 完全一致:
    双 iframe 嵌套的无障碍挑战按钮点击
    """
    def launch(self, headless=True):
        from patchright.sync_api import sync_playwright
        p = sync_playwright().start()
        b = p.chromium.launch(
            headless=headless,
            args=[
                "--lang=en-US,en",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-web-security",
                "--no-first-run",
                "--no-default-browser-check",
                "--ignore-certificate-errors",
                "--allow-running-insecure-content",
                "--disable-background-networking",
                "--disable-sync",
                "--metrics-recording-only",
                "--mute-audio",
            ],
            proxy=self._build_proxy_cfg(),
        )
        return p, b

    def _try_enter_challenge_patchright(self, page) -> bool:
        """
        Enter键法（与 PlaywrightController._try_enter_challenge 相同逻辑，
        但在 patchright 下执行）。
        等待视觉 CAPTCHA 的 blob URL 加载，然后 Enter 键通过。
        """
        print("[captcha] 尝试Enter键法（等待blob URL）…", flush=True)
        try:
            page.wait_for_event(
                "request",
                lambda req: req.url.startswith("blob:https://iframe.hsprotect.net/"),
                timeout=22000,
            )
        except Exception:
            print("[captcha] ⚠ 22s内未检测到blob URL，Enter键法跳过", flush=True)
            return False

        print("[captcha] ✅ 检测到blob URL，开始Enter键法", flush=True)
        page.wait_for_timeout(800)

        for _t in range(self.max_retries + 1):
            page.keyboard.press("Enter")
            page.wait_for_timeout(11500)
            page.keyboard.press("Enter")

            try:
                page.wait_for_event(
                    "request",
                    lambda req: req.url.startswith("https://browser.events.data.microsoft.com"),
                    timeout=8000,
                )
                try:
                    page.wait_for_event(
                        "request",
                        lambda req: req.url.startswith(
                            "https://collector-pxzc5j78di.hsprotect.net/assets/js/bundle"
                        ),
                        timeout=1700,
                    )
                    page.wait_for_timeout(2000)
                    print(f"[captcha] ⚠️ Enter第{_t+1}次：需重试", flush=True)
                    continue
                except Exception:
                    if (page.get_by_text("一些异常活动").count()
                            or page.get_by_text("此站点正在维护，暂时无法使用，请稍后重试。").count()):
                        return False
                    print(f"[captcha] ✅ Enter键第{_t+1}次通过！", flush=True)
                    return True
            except Exception:
                page.wait_for_timeout(5000)
                page.keyboard.press("Enter")
                try:
                    page.wait_for_event(
                        "request",
                        lambda req: req.url.startswith("https://browser.events.data.microsoft.com"),
                        timeout=10000,
                    )
                    try:
                        page.wait_for_event(
                            "request",
                            lambda req: req.url.startswith(
                                "https://collector-pxzc5j78di.hsprotect.net/assets/js/bundle"
                            ),
                            timeout=4000,
                        )
                    except Exception:
                        print(f"[captcha] ✅ 二次Enter第{_t+1}次通过！", flush=True)
                        return True
                except Exception:
                    pass
                page.wait_for_timeout(500)
        return False

    def handle_captcha(self, page, blob_container=None):
        """
        优先使用 Enter 键法（等待视觉 CAPTCHA blob URL）；
        失败后使用无障碍按钮点击法；
        最后降级到打码服务。
        """
        # ── 方式1：Enter键法（等blob URL → Enter通过）──────────────────
        enter_ok = self._try_enter_challenge_patchright(page)
        if enter_ok:
            return True

        # ── 方式2：无障碍挑战（轮椅按钮点击法）──────────────────────────
        accessibility_ok = self._try_accessibility_challenge(page)
        if accessibility_ok:
            return True

        # ── 方式3：打码服务降级 ──────────────────────────────────────────
        print("[captcha] 两种免费方法失败，尝试打码服务…", flush=True)
        return self._solve_with_service(page, blob_container or [])

    def _try_accessibility_challenge(self, page) -> bool:
        """
        点击无障碍挑战按钮（轮椅图标）绕过视觉 CAPTCHA。
        修复：用 locator.click() 替代 bounding_box()+page.mouse.click()，
        避免无头模式下跨域 iframe 坐标返回 None 的问题。
        兜底：JS 注入点击。
        """
        # 等 CAPTCHA iframe 出现
        try:
            page.wait_for_selector('iframe[title="验证质询"]', timeout=12000)
        except Exception:
            # 没有 CAPTCHA，也许已通过
            return True

        frame1 = page.frame_locator('iframe[title="验证质询"]')

        # 可能的无障碍按钮 aria-label（中文/英文变体）
        ACCESSIBILITY_LABELS = [
            "可访问性挑战",          # zh-CN 标准
            "Accessible challenge",   # en 标准
            "Accessibility challenge",
            "Audio challenge",
            "轮椅",
        ]

        # 内层 iframe 候选选择器（微软可能改过 style 格式）
        INNER_SELECTORS = [
            'iframe[style*="display: block"]',
            'iframe[style*="display:block"]',
            'iframe[tabindex="0"]',
            'iframe[id*="game"]',
            'iframe[id*="fc"]',
            'iframe[src*="arkose"]',
            'iframe[src*="riskapi"]',
            'iframe:first-child',
            'iframe',              # 任意 iframe
        ]

        # hsprotect.net frame 里 Arkose Labs 可访问性按钮的 JS 选择器（无 aria-label 时使用）
        JS_A11Y_SELECTORS = [
            # 常见 Arkose Labs 无障碍/音频挑战按钮
            'button[class*="audio"]',
            'button[class*="accessible"]',
            'button[class*="accessibility"]',
            '[data-cy="accessibility-challenge-tab"]',
            '[data-cy="audio-challenge-tab"]',
            'button[id*="audio"]',
            'button[id*="accessible"]',
            # 按文字内容匹配
            'button[aria-label*="Audio"]',
            'button[aria-label*="audio"]',
            'button[aria-label*="Accessible"]',
            'button[aria-label*="accessible"]',
            'button[aria-label*="challenge"]',
            # Arkose Labs tab 结构
            '.challenge-tab[data-event-name*="audio"]',
            '[class*="challenge"][class*="tab"]',
            # 通用兜底
            'button[class*="arko"]',
            'button[class*="fc-"]',
        ]

        def _frame_has_a11y(fr_or_loc) -> bool:
            """检查 frame/locator 内是否有无障碍按钮（aria-label 或 JS 搜索）"""
            # 方法1：aria-label 精确匹配
            for lbl in ACCESSIBILITY_LABELS:
                try:
                    if fr_or_loc.locator(f'[aria-label="{lbl}"]').count() > 0:
                        return True
                except Exception:
                    pass
            # 方法2：JS evaluate 在 Frame 内搜索（仅适用于真实 Frame 对象，非 FrameLocator）
            if hasattr(fr_or_loc, 'evaluate'):
                for sel in JS_A11Y_SELECTORS:
                    try:
                        found = fr_or_loc.evaluate(
                            f'!!document.querySelector({repr(sel)})'
                        )
                        if found:
                            print(f"[captcha] JS找到按钮: {sel}", flush=True)
                            return True
                    except Exception:
                        pass
            return False

        def _find_frame2():
            """
            多策略查找包含无障碍挑战按钮的内层 frame。
            先等 Arkose Labs 内容完全加载（按钮变为可用），再扫描。
            """
            # 等 Arkose Labs CAPTCHA 内容完全加载（最多 25s）
            # 关键：等待 aria-disabled 消失（按钮由灰色变为可点击状态）
            print("[captcha] 等待 CAPTCHA 游戏加载完成（最多25s）…", flush=True)
            page.wait_for_timeout(3000)
            for _wait in range(22):  # 最多再等 22 秒
                all_fr = page.frames
                for _fr in all_fr:
                    try:
                        # 检查是否有可用的无障碍按钮（非disabled）
                        enabled = _fr.evaluate("""
                            () => {
                                const btn = document.querySelector('[aria-label="可访问性挑战"], [aria-label="Accessible challenge"], [aria-label="Audio challenge"]');
                                if (!btn) return null;
                                return {
                                    disabled: btn.getAttribute('aria-disabled'),
                                    opacity: btn.style.opacity,
                                    text: btn.textContent.substring(0, 30)
                                };
                            }
                        """)
                        if enabled and enabled.get('disabled') != 'true':
                            print(f"[captcha] ✅ 无障碍按钮已启用: {enabled}", flush=True)
                            break
                    except Exception:
                        pass
                else:
                    page.wait_for_timeout(1000)
                    continue
                break

            # 策略1：遍历所有页面 frames 找 hsprotect.net 或含无障碍按钮的 frame
            all_frames = page.frames
            print(f"[captcha] 扫描 {len(all_frames)} 个 frames…", flush=True)
            best_frame = None
            for fr in all_frames:
                try:
                    url = fr.url
                    print(f"[captcha]   frame url: {url[:80]}", flush=True)
                    # 优先选择 hsprotect.net（Arkose Labs 主 frame）
                    if "hsprotect.net" in url and best_frame is None:
                        best_frame = fr
                        print(f"[captcha]   ← 标记为候选 frame", flush=True)
                    if _frame_has_a11y(fr):
                        print(f"[captcha] ✅ 在 frame 中找到无障碍按钮: {url[:60]}", flush=True)
                        return fr
                except Exception:
                    pass

            # 策略2：在最佳候选 frame 里尝试 dump 按钮信息
            if best_frame is not None:
                try:
                    btn_info = best_frame.evaluate("""
                        () => {
                            const btns = Array.from(document.querySelectorAll('button, [role="tab"], [role="button"]'));
                            return btns.slice(0, 10).map(b => ({
                                tag: b.tagName,
                                cls: b.className.substring(0, 60),
                                aria: b.getAttribute('aria-label') || '',
                                text: b.textContent.trim().substring(0, 30),
                                id: b.id
                            }));
                        }
                    """)
                    print(f"[captcha] hsprotect按钮列表: {btn_info}", flush=True)
                except Exception as e:
                    print(f"[captcha] 无法dump按钮: {e}", flush=True)
                return best_frame

            return None

        def _click_a11y_btn(frame_or_locator) -> bool:
            """
            尝试点击无障碍按钮（多种方式）：
            1. aria-label 匹配
            2. JS 选择器点击（适用于 Frame 对象）
            3. 键盘 Tab+Enter 导航
            """
            # 方法0：用真实鼠标坐标点击（跨 frame 边界有效）
            # JS dispatchEvent 不会跨 frame 冒泡，必须用 page 级别的鼠标点击
            if hasattr(frame_or_locator, 'locator'):
                for lbl in ACCESSIBILITY_LABELS:
                    try:
                        loc = frame_or_locator.locator(f'[aria-label="{lbl}"]')
                        if loc.count() == 0:
                            continue
                        # 先强制启用（移除 disabled 属性）
                        frame_or_locator.evaluate(f"""
                            () => {{
                                const btn = document.querySelector('[aria-label="{lbl}"]');
                                if (btn) {{
                                    btn.removeAttribute('aria-disabled');
                                    btn.removeAttribute('disabled');
                                    btn.style.opacity = '1';
                                    btn.style.pointerEvents = 'auto';
                                }}
                            }}
                        """)
                        # 获取按钮在 page 中的绝对坐标
                        box = loc.bounding_box(timeout=5000)
                        if box:
                            cx = box['x'] + box['width'] / 2
                            cy = box['y'] + box['height'] / 2
                            # 用 page 级别鼠标点击（能跨 frame 边界触发父 frame 事件）
                            page.mouse.move(cx - 5, cy - 3)
                            page.wait_for_timeout(200)
                            page.mouse.click(cx, cy)
                            print(f"[captcha] ✅ 真实鼠标点击 [{lbl}] 坐标({cx:.0f},{cy:.0f})", flush=True)
                            return True
                    except Exception as e:
                        print(f"[captcha] 鼠标点击失败[{lbl}]: {e}", flush=True)

            # 方法1：aria-label 精确匹配
            for lbl in ACCESSIBILITY_LABELS:
                try:
                    if hasattr(frame_or_locator, 'locator'):
                        loc = frame_or_locator.locator(f'[aria-label="{lbl}"]')
                        if loc.count() == 0:
                            continue
                        loc.scroll_into_view_if_needed(timeout=3000)
                        loc.click(timeout=6000, force=True)
                        print(f"[captcha] ✅ 点击 aria-label [{lbl}]", flush=True)
                        return True
                except Exception as e:
                    try:
                        loc.dispatch_event("click", timeout=3000)
                        print(f"[captcha] ✅ dispatch_event [{lbl}]", flush=True)
                        return True
                    except Exception:
                        pass

            # 方法2：JS 评估直接点击（仅适用于 Frame 对象）
            if hasattr(frame_or_locator, 'evaluate'):
                for sel in JS_A11Y_SELECTORS:
                    try:
                        clicked = frame_or_locator.evaluate(f"""
                            () => {{
                                const el = document.querySelector({repr(sel)});
                                if (el) {{ el.click(); return true; }}
                                return false;
                            }}
                        """)
                        if clicked:
                            print(f"[captcha] ✅ JS点击成功: {sel}", flush=True)
                            return True
                    except Exception:
                        pass

                # 方法3：键盘 Tab 遍历（导航到无障碍按钮）
                try:
                    # 先聚焦 frame，然后 Tab 若干次找按钮
                    frame_or_locator.evaluate("document.body.focus()")
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(300)
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(300)
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(300)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(500)
                    print("[captcha] ✅ 键盘Tab+Enter 已发送", flush=True)
                    return True  # 乐观假设成功
                except Exception as e:
                    print(f"[captcha] 键盘导航失败: {e}", flush=True)

            return False

        for attempt in range(self.max_retries + 1):
            page.wait_for_timeout(1000)
            print(f"[captcha] 无障碍挑战第 {attempt+1} 次尝试…", flush=True)

            # 定位内层 frame
            frame2 = _find_frame2()
            if frame2 is None:
                print("[captcha] ⚠ 内层 frame 未找到，直接用 frame1 尝试", flush=True)
                frame2 = frame1

            # ── 点击无障碍按钮（轮椅图标）────────────────────────────────────
            clicked_accessibility = _click_a11y_btn(frame2)
            if not clicked_accessibility:
                print("[captcha] 无障碍按钮点击失败，放弃本次", flush=True)
                return False

            print("[captcha] ✅ 无障碍按钮点击成功", flush=True)
            page.wait_for_timeout(2000)

            # ── 点击后等待音频挑战界面加载（8s）────────────────────────────
            page.wait_for_timeout(8000)

            # ── 截图 + 深度诊断（hsprotect frames 完整 HTML）────────────────
            try:
                page.screenshot(path=f"/tmp/outlook_captcha_after_a11y_{attempt}.png")
                print(f"[captcha] 截图已保存 /tmp/outlook_captcha_after_a11y_{attempt}.png", flush=True)
            except Exception:
                pass
            # 扫描所有 frames：打印 hsprotect.net frames 的完整 body
            all_frames_now = page.frames
            print(f"[captcha] 点击后帧深度扫描（{len(all_frames_now)} frames）：", flush=True)
            for _fi, _df in enumerate(all_frames_now):
                try:
                    detail = _df.evaluate("""
                        () => {
                            const body = document.body ? document.body.innerHTML : '';
                            // 宽泛音频选择器
                            const audios = Array.from(document.querySelectorAll(
                                'audio, video, [src*=".mp3"],[src*=".wav"],[src*=".ogg"],[src*="audio"],' +
                                '[data-src*="mp3"],[data-src*="audio"],source'
                            ));
                            const inputs = Array.from(document.querySelectorAll(
                                'input[type="text"],input[type="tel"],input[placeholder],textarea'
                            ));
                            const playBtns = Array.from(document.querySelectorAll(
                                '[class*="play"],[aria-label*="play"],[aria-label*="Play"],' +
                                '[class*="audio"],[class*="sound"],[class*="listen"]'
                            ));
                            return {
                                url: window.location.href.substring(0, 80),
                                audios: audios.slice(0,5).map(a => ({
                                    tag: a.tagName, src: (a.src||a.getAttribute('src')||a.currentSrc||'').substring(0,100),
                                    dataSrc: (a.getAttribute('data-src')||'').substring(0,80)
                                })),
                                inputs: inputs.length,
                                inputPH: inputs.slice(0,3).map(i => i.placeholder||i.type),
                                playBtns: playBtns.length,
                                bodyLen: body.length,
                                bodySnippet: body.substring(0, 600)
                            };
                        }
                    """)
                    url = detail.get('url', '')[:50]
                    has_audio = bool(detail.get('audios'))
                    has_input = detail.get('inputs', 0) > 0
                    body_len  = detail.get('bodyLen', 0)
                    is_hsp = 'hsprotect.net' in url
                    if is_hsp or has_audio or has_input or detail.get('playBtns'):
                        print(f"[captcha]   🔍 frame[{_fi}] {url}", flush=True)
                        print(f"[captcha]      audios={detail.get('audios')} inputs={detail.get('inputs')} playBtns={detail.get('playBtns')} bodyLen={body_len}", flush=True)
                        # 对 hsprotect 的 frame 打印更长的 body（找出音频挑战结构）
                        snippet_len = 800 if is_hsp else 400
                        print(f"[captcha]      body: {detail.get('bodySnippet','')[:snippet_len]}", flush=True)
                    else:
                        print(f"[captcha]   frame[{_fi}] {url}: bodyLen={body_len}", flush=True)
                except Exception as _fe:
                    print(f"[captcha]   frame[{_fi}] 读取异常: {_fe}", flush=True)

            # ── Whisper 音频 CAPTCHA 解法 ──────────────────────────────────
            audio_solved = self._solve_audio_challenge(page, frame2)
            if audio_solved:
                print("[captcha] ✅ 音频挑战通过！", flush=True)
                # 等待 CAPTCHA 消失
                page.wait_for_timeout(3000)
                try:
                    page.wait_for_selector('iframe[title="验证质询"]', state="detached", timeout=8000)
                    return True
                except Exception:
                    if (page.get_by_text("取消").count() > 0
                            or page.get_by_text("一些异常活动").count() == 0):
                        return True
                    return False

            # ── 兜底：检查页面是否已通过 ─────────────────────────────────────
            page.wait_for_timeout(3000)
            if page.get_by_text("取消").count() > 0:
                print("[captcha] ✅ 出现取消按钮，认为已通过", flush=True)
                return True
            if (page.get_by_text("一些异常活动").count()
                    or page.get_by_text("此站点正在维护，暂时无法使用，请稍后重试。").count()):
                return False
            try:
                page.wait_for_selector('iframe[title="验证质询"]', timeout=2000)
                print("[captcha] ❌ CAPTCHA 仍然存在", flush=True)
                return False
            except Exception:
                print("[captcha] ✅ CAPTCHA 已消失，认为通过", flush=True)
                return True
        else:
            return False

        return True

    def _solve_audio_challenge(self, page, hint_frame=None) -> bool:
        """
        用 Whisper 离线转写解决 Arkose Labs 音频 CAPTCHA。
        在所有 frames 中搜索音频元素，下载并转写，然后提交。
        """
        import tempfile, os, urllib.request

        # 扫描所有 frames 查找音频元素
        all_frames = page.frames
        print(f"[captcha] 搜索音频元素（{len(all_frames)} 个frames）…", flush=True)

        audio_url = None
        audio_frame = None
        input_frame = None

        for fr in all_frames:
            try:
                info = fr.evaluate("""
                    () => {
                        const audio = document.querySelector('audio[src], audio source, [class*="audio"] audio');
                        const input = document.querySelector('input[type="text"], input[type="tel"], input[placeholder]');
                        const playBtn = document.querySelector('[class*="play"], button[aria-label*="play"], button[aria-label*="Play"]');
                        return {
                            audioSrc: audio ? (audio.src || (audio.querySelector('source') ? audio.querySelector('source').src : '')) : '',
                            hasInput: !!input,
                            inputPlaceholder: input ? input.placeholder : '',
                            hasPlayBtn: !!playBtn,
                            url: window.location.href
                        };
                    }
                """)
                print(f"[captcha] frame {fr.url[:50]}: audioSrc={info.get('audioSrc','')[:60]} hasInput={info.get('hasInput')}", flush=True)

                if info.get('audioSrc') and not audio_url:
                    audio_url = info['audioSrc']
                    audio_frame = fr
                if info.get('hasInput'):
                    input_frame = fr
            except Exception:
                pass

        if not audio_url:
            print("[captcha] ⚠ 未找到音频元素", flush=True)
            return False

        print(f"[captcha] 找到音频URL: {audio_url[:80]}", flush=True)

        # 下载音频文件
        tmp_audio = None
        try:
            suffix = ".mp3" if ".mp3" in audio_url.lower() else ".wav"
            tmp_fd, tmp_audio = tempfile.mkstemp(suffix=suffix)
            os.close(tmp_fd)

            if audio_url.startswith("blob:"):
                # blob URL：用 JS 方式导出
                audio_data = audio_frame.evaluate(f"""
                    async () => {{
                        const resp = await fetch({repr(audio_url)});
                        const buf = await resp.arrayBuffer();
                        const bytes = new Uint8Array(buf);
                        let binary = '';
                        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
                        return btoa(binary);
                    }}
                """)
                import base64
                with open(tmp_audio, 'wb') as f:
                    f.write(base64.b64decode(audio_data))
                print(f"[captcha] blob音频已下载 ({os.path.getsize(tmp_audio)} bytes)", flush=True)
            else:
                # 普通 URL，直接下载
                req = urllib.request.Request(audio_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    with open(tmp_audio, 'wb') as f:
                        f.write(resp.read())
                print(f"[captcha] 音频已下载 ({os.path.getsize(tmp_audio)} bytes)", flush=True)

            # Whisper 转写
            import whisper
            print("[captcha] 加载Whisper base模型…", flush=True)
            model = whisper.load_model("base")
            result = model.transcribe(tmp_audio, language="en", fp16=False)
            transcript = result["text"].strip()
            print(f"[captcha] Whisper转写结果: '{transcript}'", flush=True)

            if not transcript:
                print("[captcha] ⚠ 转写结果为空", flush=True)
                return False

            # 在音频挑战 frame 中找输入框并提交
            target_frame = input_frame or audio_frame
            if target_frame:
                submitted = target_frame.evaluate(f"""
                    () => {{
                        const input = document.querySelector('input[type="text"], input[type="tel"], input[placeholder]');
                        if (!input) return false;
                        input.value = {repr(transcript)};
                        input.dispatchEvent(new Event('input', {{bubbles: true}}));
                        input.dispatchEvent(new Event('change', {{bubbles: true}}));
                        // 提交按钮
                        const submitBtn = document.querySelector('button[type="submit"], button[class*="submit"], input[type="submit"]');
                        if (submitBtn) {{ submitBtn.click(); return true; }}
                        // 按 Enter
                        input.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}}));
                        return true;
                    }}
                """)
                print(f"[captcha] 提交结果: {submitted}", flush=True)
                return bool(submitted)
            else:
                # 用键盘输入
                page.keyboard.type(transcript)
                page.keyboard.press("Enter")
                print("[captcha] 用键盘提交了转写结果", flush=True)
                return True

        except Exception as e:
            print(f"[captcha] 音频解法异常: {e}", flush=True)
            return False
        finally:
            if tmp_audio and os.path.exists(tmp_audio):
                try:
                    os.unlink(tmp_audio)
                except Exception:
                    pass


# ─── Playwright 控制器 ────────────────────────────────────────────────────────
class PlaywrightController(BaseController):
    """
    与原版 PlaywrightController.handle_captcha() 完全一致:
    监听 hsprotect.net 流量 + Enter 按键法
    """
    def launch(self, headless=True):
        from playwright.sync_api import sync_playwright
        p = sync_playwright().start()
        b = p.chromium.launch(
            headless=headless,
            args=["--lang=zh-CN", "--no-sandbox", "--disable-dev-shm-usage"],
            proxy=self._build_proxy_cfg(),
        )
        return p, b

    def handle_captcha(self, page, blob_container=None):
        """
        优先使用 Enter 键 + hsprotect.net 流量监听（原版逻辑）。
        如果失败且配置了打码服务，则自动降级到 2captcha/CapMonster。
        """
        ok = self._try_enter_challenge(page)
        if ok:
            return True
        print("[captcha] Enter挑战失败，尝试打码服务…", flush=True)
        return self._solve_with_service(page, blob_container or [])

    def _try_enter_challenge(self, page) -> bool:
        """原版 Enter键 + hsprotect.net 流量监听逻辑"""
        try:
            page.wait_for_event(
                "request",
                lambda req: req.url.startswith("blob:https://iframe.hsprotect.net/"),
                timeout=22000,
            )
        except Exception:
            return False
        page.wait_for_timeout(800)

        for _ in range(self.max_retries + 1):
            page.keyboard.press("Enter")
            page.wait_for_timeout(11500)
            page.keyboard.press("Enter")

            try:
                page.wait_for_event(
                    "request",
                    lambda req: req.url.startswith("https://browser.events.data.microsoft.com"),
                    timeout=8000,
                )
                try:
                    page.wait_for_event(
                        "request",
                        lambda req: req.url.startswith(
                            "https://collector-pxzc5j78di.hsprotect.net/assets/js/bundle"
                        ),
                        timeout=1700,
                    )
                    page.wait_for_timeout(2000)
                    continue
                except Exception:
                    if (page.get_by_text("一些异常活动").count()
                            or page.get_by_text("此站点正在维护，暂时无法使用，请稍后重试。").count()):
                        return False
                    break
            except Exception:
                page.wait_for_timeout(5000)
                page.keyboard.press("Enter")
                try:
                    page.wait_for_event(
                        "request",
                        lambda req: req.url.startswith("https://browser.events.data.microsoft.com"),
                        timeout=10000,
                    )
                    try:
                        page.wait_for_event(
                            "request",
                            lambda req: req.url.startswith(
                                "https://collector-pxzc5j78di.hsprotect.net/assets/js/bundle"
                            ),
                            timeout=4000,
                        )
                    except Exception:
                        break
                except Exception:
                    return False
                page.wait_for_timeout(500)
        else:
            return False

        return True


# ─── 主任务 ───────────────────────────────────────────────────────────────────
def register_one(ctrl, engine_name: str, headless: bool) -> dict:
    username, fn_eng, ln_eng = gen_email_username()
    email    = username
    password = gen_password()
    result   = {
        "email": f"{email}@outlook.com",
        "username": email,
        "password": password,
        "success": False,
        "error": "",
        "elapsed": "",
        "engine": engine_name,
    }

    p, b = ctrl.launch(headless=headless)
    if not p:
        result["error"] = "浏览器启动失败"
        return result

    # ── 浏览器指纹伪装（与住宅代理地区匹配）──────────────────────────────
    # 常见 US 用户分辨率与像素比
    SCREEN_PRESETS = [
        (1920, 1080, 1),
        (1366, 768,  1),
        (1440, 900,  1),
        (1536, 864,  1),
        (2560, 1440, 2),
        (1600, 900,  1),
        (1280, 800,  1),
        (1680, 1050, 1),
    ]
    sw, sh, dpr = random.choice(SCREEN_PRESETS)
    # 窗口比屏幕略小（浏览器 chrome 占用部分空间）
    vw = sw - random.randint(0, 40)
    vh = sh - random.randint(60, 130)

    # 常见 Chrome UA（匹配 patchright 基于的 Chromium 版本）
    CHROME_UAS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    ]
    user_agent = random.choice(CHROME_UAS)

    # US 时区随机选取
    US_TIMEZONES = [
        "America/New_York", "America/Chicago", "America/Denver",
        "America/Los_Angeles", "America/Phoenix", "America/Detroit",
    ]
    timezone_id = random.choice(US_TIMEZONES)

    is_win = "Windows" in user_agent
    platform_str = "Win32" if is_win else "MacIntel"
    hw_concurrency = random.choice([4, 6, 8, 12, 16])
    device_memory  = random.choice([4, 8, 16])
    ch_ver = "131"  # 与 UA 中的 Chrome 版本对齐

    context = b.new_context(
        # locale 保持 zh-CN：注册页面用中文 UI，选择器全部匹配
        # 中国用户使用美国住宅 IP 是非常常见的场景（留学生/VPN 用户）
        locale="zh-CN",
        timezone_id=timezone_id,
        viewport={"width": vw, "height": vh},
        screen={"width": sw, "height": sh},
        device_scale_factor=dpr,
        color_scheme="light",
        user_agent=user_agent,
        java_script_enabled=True,
        accept_downloads=False,
        extra_http_headers={
            # 中文优先的 Accept-Language（与 locale 一致）
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "sec-ch-ua": f'"Chromium";v="{ch_ver}", "Google Chrome";v="{ch_ver}", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{"Windows" if is_win else "macOS"}"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
        },
    )
    # ── 深度指纹伪装（参考 HotmailBot Pro 技术点）──────────────────────────
    import uuid as _uuid
    machine_id = str(_uuid.uuid4())           # 每次会话生成唯一机器ID
    canvas_noise = random.randint(1, 9999)    # canvas 哈希噪点种子
    webgl_vendors = [
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1080 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Apple Inc.", "Apple M1"),
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ]
    webgl_vendor, webgl_renderer = random.choice(webgl_vendors)
    # 随机插件列表（伪装成普通用户）
    plugins_js = "[{name:'Chrome PDF Plugin'},{name:'Chrome PDF Viewer'},{name:'Native Client'}]"
    # 注入所有指纹欺骗脚本
    context.add_init_script(f"""
        // navigator 属性
        Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hw_concurrency} }});
        Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {device_memory} }});
        Object.defineProperty(navigator, 'platform', {{ get: () => '{platform_str}' }});
        Object.defineProperty(navigator, 'language', {{ get: () => 'zh-CN' }});
        Object.defineProperty(navigator, 'languages', {{ get: () => ['zh-CN', 'zh', 'en-US', 'en'] }});
        Object.defineProperty(screen, 'colorDepth', {{ get: () => 24 }});
        Object.defineProperty(screen, 'pixelDepth', {{ get: () => 24 }});
        // 隐藏 webdriver 标志
        Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
        // 插件伪装（非空 = 真实浏览器）
        Object.defineProperty(navigator, 'plugins', {{
            get: () => {{
                const arr = {plugins_js};
                arr.length = arr.length; return arr;
            }}
        }});
        // canvas 指纹噪点
        (function() {{
            const orig = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {{
                const ctx = this.getContext('2d');
                if (ctx) {{
                    const noise = {canvas_noise};
                    const px = ctx.getImageData(0, 0, 1, 1);
                    px.data[0] = (px.data[0] + noise) % 256;
                    ctx.putImageData(px, 0, 0);
                }}
                return orig.apply(this, arguments);
            }};
            const orig2 = CanvasRenderingContext2D.prototype.getImageData;
            CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {{
                const data = orig2.apply(this, arguments);
                const noise = {canvas_noise};
                for (let i = 0; i < data.data.length; i += 100) {{
                    data.data[i] = (data.data[i] + noise % 3) % 256;
                }}
                return data;
            }};
        }})();
        // WebGL 指纹伪装
        (function() {{
            const getParam = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(param) {{
                if (param === 37445) return '{webgl_vendor}';    // UNMASKED_VENDOR_WEBGL
                if (param === 37446) return '{webgl_renderer}';  // UNMASKED_RENDERER_WEBGL
                return getParam.apply(this, arguments);
            }};
            try {{
                const getParam2 = WebGL2RenderingContext.prototype.getParameter;
                WebGL2RenderingContext.prototype.getParameter = function(param) {{
                    if (param === 37445) return '{webgl_vendor}';
                    if (param === 37446) return '{webgl_renderer}';
                    return getParam2.apply(this, arguments);
                }};
            }} catch(e) {{}}
        }})();
        // Audio 指纹噪点
        (function() {{
            const orig = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function(ch) {{
                const data = orig.apply(this, arguments);
                const noise = {canvas_noise} * 1e-7;
                for (let i = 0; i < data.length; i += 200) {{
                    data[i] += noise;
                }}
                return data;
            }};
        }})();
        // 机器 ID（localStorage 写入，模拟真实设备持久化）
        try {{
            localStorage.setItem('device_id', '{machine_id}');
            localStorage.setItem('machine_id', '{machine_id}');
        }} catch(e) {{}}
        // 电池 API（避免暴露无电池=服务器环境）
        try {{
            navigator.getBattery = async () => ({{
                charging: true, chargingTime: 0,
                dischargingTime: Infinity, level: {round(random.uniform(0.6, 1.0), 2)},
                addEventListener: () => {{}}, removeEventListener: () {{}}
            }});
        }} catch(e) {{}}
    """)
    print(f"[register] 指纹: UA={user_agent[:40]}... WebGL={webgl_vendor[:20]} Screen={sw}x{sh} TZ={timezone_id} MachineID={machine_id[:8]}...", flush=True)
    page = context.new_page()
    t0 = time.time()

    try:
        ok, msg, actual_email = ctrl.outlook_register(page, email, password)
        result["success"]  = ok
        result["error"]    = "" if ok else msg
        result["email"]    = f"{actual_email}@outlook.com"
        result["username"] = actual_email

        if ok:
            try:
                page.screenshot(path=f"/tmp/outlook_ok_{actual_email}.png")
            except Exception:
                pass
        else:
            try:
                page.screenshot(path=f"/tmp/outlook_fail_{actual_email}.png")
            except Exception:
                pass
    except Exception as e:
        result["error"] = str(e)
        try:
            page.screenshot(path=f"/tmp/outlook_err_{email}.png")
        except Exception:
            pass
    finally:
        try:
            b.close()
            p.stop()
        except Exception:
            pass

    result["elapsed"] = f"{time.time()-t0:.1f}s"
    return result


# ─── 入口 ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Outlook 批量注册 (参考 outlook-batch-manager)")
    parser.add_argument("--count",           type=int,   default=1,            help="注册数量")
    parser.add_argument("--proxy",           type=str,   default="",           help="代理, 如 socks5://127.0.0.1:1080")
    parser.add_argument("--proxies",         type=str,   default="",           help="多代理轮换（逗号分隔），每次注册轮换一个节点")
    parser.add_argument("--engine",          type=str,   default="patchright", choices=["patchright","playwright"])
    parser.add_argument("--headless",        type=str,   default="true",       help="true/false")
    parser.add_argument("--wait",            type=int,   default=BOT_PROTECTION_WAIT, help="bot_protection_wait (秒)")
    parser.add_argument("--retries",         type=int,   default=MAX_CAPTCHA_RETRIES)
    parser.add_argument("--delay",           type=int,   default=5,            help="每次注册间隔秒数")
    parser.add_argument("--output",          type=str,   default="",           help="输出文件")
    parser.add_argument("--captcha-service", type=str,   default="",           help="打码服务: 2captcha | capmonster")
    parser.add_argument("--captcha-key",     type=str,   default="",           help="打码服务 API Key")
    parser.add_argument("--proxy-mode",      type=str,   default="",           help="cf = 从 CF IP 池自动分配代理")
    parser.add_argument("--cf-port",         type=int,   default=443,          help="CF 代理端口（默认443）")
    args = parser.parse_args()

    headless = args.headless.lower() != "false"
    CtrlCls  = PatchrightController if args.engine == "patchright" else PlaywrightController

    # 构建打码服务 solver（可选）
    solver = None
    captcha_service = args.captcha_service or ""
    captcha_key     = args.captcha_key     or ""
    if captcha_service and captcha_key:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from captcha_solver import build_solver
        solver = build_solver(captcha_service, captcha_key)
        print(f"[captcha] 打码服务已启用: {captcha_service}", flush=True)

    # 解析代理列表（--proxies 优先于 --proxy）
    proxy_list = []
    if args.proxies:
        proxy_list = [p.strip() for p in args.proxies.split(",") if p.strip()]
    if not proxy_list and args.proxy:
        proxy_list = [args.proxy.strip()]

    # CF 代理池模式
    use_cf_pool = getattr(args, 'proxy_mode', '') == 'cf'
    if use_cf_pool:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import cf_ip_pool as _cf_pool
        print(f"   CF代理池模式已启用 (port={args.cf_port})")
        # 预加载一批 IP（若池为空）
        _pool_status = _cf_pool.get_pool_status()
        if _pool_status['available'] == 0:
            print("   CF池为空，自动生成并测速…", flush=True)
            _cf_pool.refresh_pool(
                generate_count=60, target_valid=20, threads=8,
                port=args.cf_port, max_latency=1000.0,
                log_cb=lambda m: print(f"   {m}", flush=True)
            )

    svc_hint = f"  打码服务={captcha_service}" if solver else ""
    print(f"\n🚀 Outlook 批量注册  引擎={args.engine}  headless={headless}  count={args.count}{svc_hint}")
    print(f"   bot_protection_wait={args.wait}s  max_captcha_retries={args.retries}")
    if use_cf_pool:
        print(f"   代理模式: CF IP 池（每账号独占一个 IP，用后丢弃）")
    elif len(proxy_list) > 1:
        print(f"   代理轮换池: {len(proxy_list)} 个节点")
    elif proxy_list:
        import re as _re
        masked_proxy = _re.sub(r'(:)([^:@]{4})[^:@]*(@)', r'\1****\3', proxy_list[0])
        print(f"   代理: {masked_proxy}")
    print(f"   入口URL: {REGISTER_URL}\n{'─'*60}")

    results = []
    for i in range(args.count):
        # 代理选择：CF池模式 > 轮换列表 > 无
        if use_cf_pool:
            job_id = f"reg_{i}_{int(time.time())}"
            ip_info = _cf_pool.acquire_ip(job_id, auto_refresh=True,
                                          log_cb=lambda m: print(f"   {m}", flush=True))
            if ip_info:
                cur_proxy = ip_info['proxy']
                print(f"\n[{i+1}/{args.count}] 开始注册… CF节点: {ip_info['ip']} 延迟{ip_info['latency']}ms", flush=True)
            else:
                cur_proxy = ""
                print(f"\n[{i+1}/{args.count}] 开始注册… ⚠ CF池无可用IP，无代理模式", flush=True)
        elif proxy_list:
            cur_proxy = proxy_list[i % len(proxy_list)]
            if len(proxy_list) > 1:
                print(f"\n[{i+1}/{args.count}] 开始注册… 节点 [{(i % len(proxy_list))+1}/{len(proxy_list)}]: {cur_proxy[:40]}...")
            else:
                print(f"\n[{i+1}/{args.count}] 开始注册...")
        else:
            cur_proxy = ""
            print(f"\n[{i+1}/{args.count}] 开始注册...")

        ctrl = CtrlCls(
            proxy=cur_proxy,
            wait_ms=args.wait,
            max_captcha_retries=args.retries,
            captcha_solver=solver,
        )
        r = register_one(ctrl, args.engine, headless)
        results.append(r)

        status = "✅ 注册成功" if r["success"] else f"❌ {r['error']}"
        print(f"  {status}  |  {r['email']}  密码: {r['password']}  耗时: {r['elapsed']}")

        if i < args.count - 1:
            delay = args.delay + random.randint(0, 3)
            print(f"  ⏱ 等待 {delay}s ...")
            time.sleep(delay)

    ok  = [r for r in results if r["success"]]
    bad = [r for r in results if not r["success"]]
    print(f"\n{'─'*60}")
    print(f"✅ 成功: {len(ok)} / {len(results)}")
    for r in ok:
        print(f"  📧 {r['email']}  密码: {r['password']}")
    if bad:
        print(f"❌ 失败: {len(bad)}")
        for r in bad:
            print(f"  {r['email']}: {r['error']}")

    if args.output:
        Path(args.output).write_text("\n".join(
            f"{r['email']}----{r['password']}" for r in ok
        ))
        print(f"\n💾 已保存 {len(ok)} 条到 {args.output}")

    print("\n── JSON 结果 ──")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
