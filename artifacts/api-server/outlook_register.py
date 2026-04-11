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
    """生成真实人名格式的邮箱用户名"""
    FIRST = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas",
             "Christopher","Daniel","Matthew","Anthony","Mark","Steven","Paul","Andrew","Joshua",
             "Benjamin","Samuel","Patrick","Jack","Tyler","Aaron","Nathan","Kyle","Bryan","Eric",
             "Mary","Patricia","Jennifer","Linda","Elizabeth","Susan","Jessica","Sarah","Karen",
             "Lisa","Nancy","Ashley","Emily","Donna","Michelle","Amanda","Melissa","Rebecca","Laura",
             "Emma","Olivia","Liam","Noah","Ava","Sophia","Isabella","Lucas","Ethan","Mason"]
    LAST  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez",
             "Martinez","Hernandez","Lopez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson",
             "Lee","Perez","Thompson","White","Harris","Clark","Ramirez","Lewis","Robinson","Walker",
             "Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Green","Adams",
             "Nelson","Baker","Campbell","Mitchell","Carter","Turner","Phillips","Evans","Collins",
             "Stewart","Morales","Murphy","Cook","Rogers","Bennett","Gray","Hughes","Patel","Parker"]
    fn = random.choice(FIRST)
    ln = random.choice(LAST)
    y2 = str(random.randint(70, 99))   # 出生年份后两位，如 85
    n2 = str(random.randint(10, 99))   # 两位数，如 42
    # 权重：更像真实人名的格式权重高；纯名字最佳（被占概率高），带年份次之
    patterns = [
        fn.lower() + "." + ln.lower(),          # karen.ramirez  ← 最真实
        fn.lower() + "_" + ln.lower(),          # karen_ramirez
        fn[0].lower() + "." + ln.lower(),       # k.ramirez
        fn + "." + ln,                          # Karen.Ramirez
        fn + ln,                                # KarenRamirez
        fn.lower() + "." + ln.lower() + y2,     # karen.ramirez85
        fn.lower() + "_" + ln.lower() + y2,     # karen_ramirez85
        fn + ln + y2,                           # KarenRamirez85
        fn[0].lower() + ln.lower() + y2,        # kramirez85
        fn.lower() + ln.lower() + n2,           # karenramirez42
        fn[0].lower() + "." + ln.lower() + n2,  # k.ramirez42
        fn.lower() + "." + ln[0].lower() + y2,  # karen.r85
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
            email_input.wait_for(timeout=10000)
            email_input.click()
            email_input.type(email, delay=max(20, 0.006 * self.wait_time), timeout=10000)
            page.keyboard.press("Tab")
            page.wait_for_timeout(0.02 * self.wait_time)
            page.locator('[data-testid="primaryButton"]').click(timeout=5000)
            page.wait_for_timeout(max(2000, 0.04 * self.wait_time))

            # 检测用户名是否被占用 → 始终用我们自己的人名格式重新生成
            # 不采用微软推荐名（karene34618 风格），保持真实人名外观
            for _attempt in range(3):
                if page.get_by_text("已被占用").count() or page.get_by_text("username is taken").count():
                    # 重新生成一个人名格式用户名（gen_email_username 已含带数字的模式）
                    picked, _, _ = gen_email_username()
                    print(f"  ⚠ 用户名被占，切换为: {picked}")
                    email = picked
                    email_input = page.locator('[aria-label="新建电子邮件"]')
                    email_input.click()
                    email_input.select_all() if hasattr(email_input, 'select_all') else None
                    page.keyboard.press("Control+a")
                    page.keyboard.press("Delete")
                    email_input.type(picked, delay=max(20, 0.006 * self.wait_time))
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(0.02 * self.wait_time)
                    page.locator('[data-testid="primaryButton"]').click(timeout=5000)
                    page.wait_for_timeout(max(2000, 0.04 * self.wait_time))
                else:
                    break

            # 密码
            page.locator('[type="password"]').type(
                password, delay=0.004 * self.wait_time, timeout=10000)
            page.wait_for_timeout(0.02 * self.wait_time)
            page.locator('[data-testid="primaryButton"]').click(timeout=5000)

            # 生日
            page.wait_for_timeout(0.03 * self.wait_time)
            page.locator('[name="BirthYear"]').fill(year, timeout=10000)
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
            page.locator('#lastNameInput').type(
                lastname, delay=0.002 * self.wait_time, timeout=10000)
            page.wait_for_timeout(0.02 * self.wait_time)
            page.locator('#firstNameInput').fill(firstname, timeout=10000)

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

        except Exception as e:
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
            args=["--lang=zh-CN", "--no-sandbox", "--disable-dev-shm-usage"],
            proxy=self._build_proxy_cfg(),
        )
        return p, b

    def handle_captcha(self, page, blob_container=None):
        """
        优先使用无障碍挑战（免费）。
        如果失败且配置了打码服务，则自动降级到 2captcha/CapMonster。
        """
        # ── 方式1：无障碍挑战（双 iframe 按钮点击）────────────────────────
        accessibility_ok = self._try_accessibility_challenge(page)
        if accessibility_ok:
            return True

        # ── 方式2：打码服务降级 ──────────────────────────────────────────
        print("[captcha] 无障碍挑战失败，尝试打码服务…", flush=True)
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

        # 内层 iframe 候选选择器（微软可能改过 style 格式）
        INNER_SELECTORS = [
            'iframe[style*="display: block"]',
            'iframe[style*="display:block"]',
            'iframe[tabindex="0"]',
            'iframe:first-child',
        ]

        def _find_frame2():
            """尝试多个内层 iframe 选择器"""
            for sel in INNER_SELECTORS:
                try:
                    f2 = frame1.frame_locator(sel)
                    # 检查无障碍按钮是否存在
                    cnt = f2.locator('[aria-label="可访问性挑战"]').count()
                    if cnt > 0:
                        return f2
                except Exception:
                    pass
            # 最后兜底：直接从 page.frames() 里找
            for fr in page.frames():
                try:
                    if fr.locator('[aria-label="可访问性挑战"]').count() > 0:
                        return fr
                except Exception:
                    pass
            return None

        def _click_by_locator_or_js(frame_or_locator, aria_label) -> bool:
            """尝试 locator.click() → JS dispatch_event"""
            try:
                if hasattr(frame_or_locator, 'locator'):
                    loc = frame_or_locator.locator(f'[aria-label="{aria_label}"]')
                else:
                    loc = frame_or_locator
                loc.wait_for(state="attached", timeout=3000)
                loc.scroll_into_view_if_needed(timeout=3000)
                loc.click(timeout=5000, force=True)
                return True
            except Exception as e:
                print(f"[captcha] click() 失败({e})，尝试 dispatch_event…", flush=True)
                try:
                    loc.dispatch_event("click", timeout=3000)
                    return True
                except Exception as e2:
                    print(f"[captcha] dispatch_event 也失败({e2})", flush=True)
                    return False

        for attempt in range(self.max_retries + 1):
            page.wait_for_timeout(800)
            print(f"[captcha] 无障碍挑战第 {attempt+1} 次尝试…", flush=True)

            # 定位内层 frame
            frame2 = _find_frame2()
            if frame2 is None:
                # 直接在 frame1 里找
                frame2 = frame1

            # ── 点击无障碍按钮（轮椅图标）────────────────────────────────────
            clicked_accessibility = _click_by_locator_or_js(frame2, "可访问性挑战")
            if not clicked_accessibility:
                print("[captcha] 无障碍按钮点击失败，放弃本次", flush=True)
                return False

            print("[captcha] ✅ 无障碍按钮点击成功", flush=True)
            page.wait_for_timeout(800)

            # ── 点击「再次按下」按钮 ──────────────────────────────────────────
            _click_by_locator_or_js(frame2, "再次按下")
            print("[captcha] 已点击再次按下（忽略失败）", flush=True)

            # 等待 .draw 动画消失，判断是否通过
            try:
                page.locator(".draw").wait_for(state="detached", timeout=10000)
                try:
                    page.locator('[role="status"][aria-label="正在加载..."]').wait_for(timeout=5000)
                    page.wait_for_timeout(8000)
                    if (page.get_by_text("一些异常活动").count()
                            or page.get_by_text("此站点正在维护，暂时无法使用，请稍后重试。").count()):
                        return False
                    if frame2.locator('[aria-label="可访问性挑战"]').count() > 0:
                        print("[captcha] ⚠️ 无障碍挑战需要重试", flush=True)
                        continue
                    break
                except Exception:
                    if page.get_by_text("取消").count() > 0:
                        print("[captcha] ✅ 出现取消按钮，认为已通过", flush=True)
                        break
                    try:
                        frame1.get_by_text("请再试一次").wait_for(timeout=15000)
                        print("[captcha] ⚠️ 请再试一次，重试中…", flush=True)
                        continue
                    except Exception:
                        break
            except Exception:
                if page.get_by_text("取消").count() > 0:
                    print("[captcha] ✅ 出现取消按钮，认为已通过", flush=True)
                    break
                return False
        else:
            return False

        return True


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

    context = b.new_context(
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        viewport={"width": random.randint(1280, 1920), "height": random.randint(768, 1080)},
    )
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
    parser.add_argument("--engine",          type=str,   default="patchright", choices=["patchright","playwright"])
    parser.add_argument("--headless",        type=str,   default="true",       help="true/false")
    parser.add_argument("--wait",            type=int,   default=BOT_PROTECTION_WAIT, help="bot_protection_wait (秒)")
    parser.add_argument("--retries",         type=int,   default=MAX_CAPTCHA_RETRIES)
    parser.add_argument("--delay",           type=int,   default=5,            help="每次注册间隔秒数")
    parser.add_argument("--output",          type=str,   default="",           help="输出文件")
    parser.add_argument("--captcha-service", type=str,   default="",           help="打码服务: 2captcha | capmonster")
    parser.add_argument("--captcha-key",     type=str,   default="",           help="打码服务 API Key")
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

    ctrl = CtrlCls(
        proxy=args.proxy or "",
        wait_ms=args.wait,
        max_captcha_retries=args.retries,
        captcha_solver=solver,
    )

    svc_hint = f"  打码服务={captcha_service}" if solver else ""
    print(f"\n🚀 Outlook 批量注册  引擎={args.engine}  headless={headless}  count={args.count}{svc_hint}")
    print(f"   bot_protection_wait={args.wait}s  max_captcha_retries={args.retries}")
    print(f"   入口URL: {REGISTER_URL}\n{'─'*60}")

    results = []
    for i in range(args.count):
        print(f"\n[{i+1}/{args.count}] 开始注册...")
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
