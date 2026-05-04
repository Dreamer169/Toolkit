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
import re
import secrets
import string
import sys
import time
import os
import subprocess
from pathlib import Path

from faker import Faker
from browser_fingerprint import gen_profile, context_kwargs, apply_fingerprint_sync, profile_summary

fake = Faker("en_US")  # v8.19: 双语 selector 完成后切回 en_US (与 browser locale 一致, 名字-locale 不矛盾)

# ═══════════════════════════════════════════════════════════════════════════
# v8.19: 双语 selector 常量 (zh-CN + en-US 并存, locale 切换不再失配)
# ═══════════════════════════════════════════════════════════════════════════
# 文本匹配 — 用 re.compile 实现 OR (Playwright get_by_text 接受 Pattern 对象)
TXT_AGREE_CONTINUE = re.compile(r"^\s*(同意并继续|Agree and continue|Continue|Next)\s*$")
TXT_USERNAME_TAKEN = re.compile(r"已被占用|该用户名不可用|username is taken|already (taken|exists|in use)|is not available|cannot be used|Someone already has", re.IGNORECASE)
TXT_UNUSUAL_ACTIVITY = re.compile(r"一些异常活动|unusual activity|something went wrong", re.IGNORECASE)
TXT_SITE_MAINTENANCE = re.compile(r"此站点正在维护|currently (down|unavailable)|under maintenance|service is unavailable", re.IGNORECASE)
TXT_CANCEL_BTN = re.compile(r"^\s*(取消|Cancel)\s*$")

# CSS attribute selector union (comma-separated, Playwright/CSS 原生支持 OR)
SEL_EMAIL_INPUT = '[aria-label="新建电子邮件"], [aria-label="New email"], [aria-label="New email address"]'
SEL_IFRAME_CHALLENGE = 'iframe[title="验证质询"], iframe[title="Verification challenge"], iframe[title*="challenge" i]'
SEL_A11Y_CHALLENGE = '[aria-label="可访问性挑战"], [aria-label="Accessible challenge"], [aria-label="Accessibility challenge"], [aria-label="Audio challenge"]'
SEL_PRESS_AGAIN = '[aria-label="再次按下"], [aria-label="Press again"], [aria-label="Press and hold"]'

# 月份英文名映射 (date picker text-is fallback)
EN_MONTHS = ["", "January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]

def date_option_selector(value: str, zh_suffix: str, is_month: bool = False) -> str:
    """生成日期 picker 的 zh+en 双语 :text-is union selector.
    is_month=True: zh '5月' / en 'May'
    is_month=False (day): zh '5日' / en '5'
    """
    if is_month:
        en_text = EN_MONTHS[int(value)]
    else:
        en_text = value  # day 在 en-US 是纯数字
    return f'[role="option"]:text-is("{value}{zh_suffix}"), [role="option"]:text-is("{en_text}")'


# ─── 配置 ─────────────────────────────────────────────────────────────────────
BOT_PROTECTION_WAIT = 11          # 秒，与原版一致
MAX_CAPTCHA_RETRIES = 2
REGISTER_URL = "https://outlook.live.com/mail/0/?prompt=create_account"

# v8.22: CAPTCHA 误判修复 — 注册成功后微软重定向链 (consent → terms → mail/init shell)
# 在 datacenter ASN 出口下经常 25-50s, 老代码 30s wait_for_url + 5s 选择器 fallback
# 一过期就把已成功账号写成失败. POST_NAV_TIMEOUT 拉到 60s, 并加 cookie 正向证据判定.
POST_NAV_TIMEOUT = int(os.environ.get("POST_NAV_TIMEOUT_MS", "60000"))
# 微软注册成功后必然下发的 auth/profile cookies (任意一个出现 → 注册已生效)
SUCCESS_COOKIE_NAMES = (
    "RPSAuth", "MSPAuth", "MSPProf", "ESTSAUTH", "ESTSAUTHPERSISTENT",
    "PPAuth", "WLSSC", "MSCC", "MUID", "ANON",
)
# 成功 URL 关键词 (扩展原列表 — 命中任一即视为成功)
SUCCESS_URL_KEYWORDS = (
    "account.live.com", "account.microsoft.com",
    "outlook.live.com", "outlook.com/mail",
    "login.live.com/login.srf", "login.live.com/ppsecure",
    "consent.live.com", "signup.live.com/CreateAccount.aspx?id=",
    "/owa/", "hotmail.com/mail",
)


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
    def __init__(self, proxy="", wait_ms=None, max_captcha_retries=MAX_CAPTCHA_RETRIES):
        self.proxy          = proxy
        self.wait_time      = (wait_ms or BOT_PROTECTION_WAIT) * 1000  # ms
        self.max_retries    = max_captcha_retries

    def _build_proxy_cfg(self):
        """
        代理配置修复 (Bug Fix: Webshare HTTP 代理不能走 Socks5Relay):
        SOCKS5 有凭据 → 启动本地 Socks5Relay（无认证），转发到上游带认证的 SOCKS5 代理
        HTTP  有凭据 → 使用 Playwright 原生 username/password 认证（Chromium 支持 HTTP 代理认证）
        无凭据       → 直接传给 Chromium
        注意: Webshare 格式为 http://user:pass@host:port，属 HTTP 代理，
              不能传给 Socks5Relay（SOCKS5 握手协议与 HTTP 代理协议不兼容，连接必失败）。
        """
        if not self.proxy:
            return None
        import re, sys, os
        m = re.match(r'(socks5h?|http|https)://([^:]+):([^@]+)@([^:]+):(\d+)', self.proxy)
        if m:
            _scheme, user, password, host, port = m.groups()
            if _scheme in ("socks5", "socks5h"):
                # SOCKS5：Chromium 不支持带认证 SOCKS5，需要本地无认证中转
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from socks5_relay import Socks5Relay
                relay = Socks5Relay(host, int(port), user, password)
                local_port = relay.start()
                self._relay = relay  # 保持引用，防止 GC
                print(f"[relay] SOCKS5 中转启动：127.0.0.1:{local_port} → {host}:{port}", flush=True)
                return {"server": f"socks5://127.0.0.1:{local_port}", "bypass": "localhost"}
            else:
                # HTTP/HTTPS：Playwright 原生支持用户名密码认证（无需中转）
                print(f"[proxy] HTTP代理（原生认证）：{host}:{port}", flush=True)
                return {
                    "server":   f"http://{host}:{port}",
                    "username": user,
                    "password": password,
                    "bypass":   "localhost",
                }
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
            page.get_by_text(TXT_AGREE_CONTINUE).wait_for(timeout=30000)
            start_time = time.time()
            page.wait_for_timeout(0.1 * self.wait_time)
            page.get_by_text(TXT_AGREE_CONTINUE).click(timeout=30000)
        except Exception as e:
            return False, f"等待同意按钮(zh:同意并继续/en:Continue)超时(可能是 IP 被风控或页面未加载): {e}", email

        # ── Step 2: 填写邮箱名、密码、生日、姓名 ─────────────────────────
        try:
            # 邮箱（支持用户名被占时自动切换建议名）
            email_input = page.locator(SEL_EMAIL_INPUT)
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
                # v8.19: 双语 regex 单次检测 (zh+en 全部 username taken 提示)
                taken = page.get_by_text(TXT_USERNAME_TAKEN).count() > 0
                password_visible = page.locator('[type="password"]').count() > 0
                if password_visible:
                    username_accepted = True
                    print(f"  ✅ 用户名 {email} 已接受，进入密码步骤", flush=True)
                    break
                if taken:
                    picked, _, _ = gen_email_username()
                    # v8.77 优化 J: 用户名被占冷却 5s → 1.5s (MS 用户名查询不需长冷却, 实测 [3/3] case 损失 10s)
                    print(f"  ⚠ 用户名被占（第{_attempt+1}次），冷却1.5s后切换为: {picked}", flush=True)
                    email = picked
                    page.wait_for_timeout(1500)
                    email_input = page.locator(SEL_EMAIL_INPUT)
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
                page.locator(date_option_selector(month, "月", is_month=True)).first.click()
                page.wait_for_timeout(0.04 * self.wait_time)
                page.locator('[name="BirthDay"]').click()
                page.wait_for_timeout(0.03 * self.wait_time)
                page.locator(date_option_selector(day, "日", is_month=False)).first.click()
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

            if (page.get_by_text(TXT_UNUSUAL_ACTIVITY).count()
                    or page.get_by_text(TXT_SITE_MAINTENANCE).count()):
                return False, "当前IP注册频率过快", email

            if page.locator("iframe#enforcementFrame").count() > 0:
                return False, "验证码类型错误，非按压验证码", email

            # ── CAPTCHA ──────────────────────────────────────────────────
            captcha_ok = self.handle_captcha(page, blob_container)
            if not captcha_ok:
                return False, "验证码处理失败", email

            # ── 验证注册真正完成 ── v8.22 CAPTCHA 误判修复 (POST_NAV_TIMEOUT) ──
            # 微软注册成功后链路: captcha-pass → consent/terms → account.live → outlook.live/mail
            # 在 datacenter ASN + xray/CF/SOCKS 加层下整链路常 25-50s, 30s 不够.
            # 三种正向证据任意命中即视为成功 (避免把已成功账号误判为失败):
            #   1. URL 命中 SUCCESS_URL_KEYWORDS
            #   2. context cookies 出现 SUCCESS_COOKIE_NAMES (RPSAuth/MSPAuth 等)
            #   3. DOM 出现登录/个人中心标记
            def _has_success_cookie() -> bool:
                try:
                    cks = page.context.cookies()
                    for c in cks:
                        nm = (c.get("name") or "").strip()
                        if nm in SUCCESS_COOKIE_NAMES:
                            return True
                except Exception:
                    pass
                return False

            def _is_success_now() -> bool:
                if any(k in page.url for k in SUCCESS_URL_KEYWORDS):
                    return True
                if _has_success_cookie():
                    return True
                return False

            ok_signal = False
            try:
                # 第一层: 等 URL 跳转 (POST_NAV_TIMEOUT)
                page.wait_for_url(
                    lambda u: any(x in u for x in SUCCESS_URL_KEYWORDS),
                    timeout=POST_NAV_TIMEOUT,
                )
                print(f"[register] ✅ 检测到成功跳转页: {page.url[:100]}", flush=True)
                ok_signal = True
            except Exception:
                # 第二层: cookie 正向证据 (微软已下发 auth cookie → 后端账号已建好, 仅是前端壳没渲染)
                if _has_success_cookie():
                    print(f"[register] ✅ URL 未跳转但已检出成功 cookie (后端注册已完成)", flush=True)
                    ok_signal = True
                else:
                    # 第三层: DOM 标记 (兼容老版终端 UI)
                    try:
                        page.wait_for_selector(
                            '[data-testid="ocid-login"] , [aria-label="Outlook"] , '
                            '.welcome-msg , #mectrl_headerPicture , '
                            '[data-testid="appConsentPrimaryButton"] , #idSIButton9 , '
                            '#KmsiCheckboxField , [data-task="consent"] , #appName',
                            timeout=8000,
                        )
                        print("[register] ✅ DOM 出现登录/同意标记", flush=True)
                        ok_signal = True
                    except Exception:
                        pass
                    # 第四层: 短暂再等一次 cookie/URL (重定向链可能在 wait_for_selector 期间继续)
                    if not ok_signal:
                        try:
                            page.wait_for_timeout(2500)
                            if _is_success_now():
                                print(f"[register] ✅ 二次轮询命中: {page.url[:100]}", flush=True)
                                ok_signal = True
                        except Exception:
                            pass

            if not ok_signal:
                cur_url = page.url
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
    # [付费打码已禁] """
    # [付费打码已禁] 与原版 PatchrightController.handle_captcha() 完全一致:
    # [付费打码已禁] 双 iframe 嵌套的无障碍挑战按钮点击
    # [付费打码已禁] """
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
        Enter键法（兜底方案）：等待视觉 CAPTCHA 的 blob URL 加载，然后 Enter 键通过。
        v7.63 起作为无障碍法失败后的回退，22s 超时保留作为关键 iframe 加载缓冲。
        历史教训：曾改成 5s "快速跳过" 反而把 captcha iframe 提前杀掉，
        恢复 22s；此处不再缩短，让兜底有充分时间。
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
                    if (page.get_by_text(TXT_UNUSUAL_ACTIVITY).count()
                            or page.get_by_text(TXT_SITE_MAINTENANCE).count()):
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
        v7.63 调换顺序：优先使用无障碍按钮点击法（v7.62 实测 100% 通过率）；
        失败后回退到 Enter 键法（等待视觉 CAPTCHA blob URL）作为兜底。
        历史教训保留：如果未来发现无障碍法被风控，可调回 Enter 键先序。
        """
        # ── [早期拦截] 在所有交互前安装网络请求拦截器 ──────────────────────
        # Arkose Labs 音频通过 XHR fetch，DOM 里 audio.src 始终为空
        # 必须在按住按钮前就开始监听，才能捕获到音频下载请求
        self._net_audio_urls: list = []
        _AUDIO_EXTS = ('.mp3', '.wav', '.ogg', '.m4a', '.aac')
        _AUDIO_KWS  = ('audio-challenge', '/audio/', '/sound/', 'speak',
                       'arkose', 'funcaptcha')
        # 已知遥测/信标端点，绝不是音频，排除之
        _AUDIO_EXCL = ('beacon', 'telemetry', 'metric', 'analytics',
                       'tracking', 'pixel', 'collector', 'stats', 'ping')
        # hsprotect.net /api/ 路径下只有包含音频关键词的才算音频
        _HSP_AUDIO_KWS = ('audio', 'sound', 'speech', 'voice', 'captcha', 'challenge')
        def _on_audio_req(request):
            url = request.url
            low = url.lower()
            if any(ex in low for ex in _AUDIO_EXCL):
                return  # 排除遥测/信标端点（如 /api/v2/msft/beacon）
            is_hsp_audio = (
                'hsprotect.net' in low
                and '/api/' in low
                and any(ak in low for ak in _HSP_AUDIO_KWS)
            )
            if (any(low.endswith(e) for e in _AUDIO_EXTS)
                    or any(kw in low for kw in _AUDIO_KWS)
                    or is_hsp_audio):
                if url not in self._net_audio_urls:
                    self._net_audio_urls.append(url)
                    print(f"[captcha] 🌐 [早期拦截] 音频URL: {url[:120]}", flush=True)
        page.on("request", _on_audio_req)
        print("[captcha] ✅ 音频URL拦截器已安装（在所有按钮点击前）", flush=True)

        # ── 方式1：无障碍挑战（轮椅按钮点击法，v7.62 实测主力，100% 通过率）──
        accessibility_ok = self._try_accessibility_challenge(page)
        if accessibility_ok:
            return True

        # ── 方式2：Enter键法（等blob URL → Enter通过，作为兜底）─────────
        print("[captcha] 无障碍法失败，回退 Enter 键法兜底…", flush=True)
        enter_ok = self._try_enter_challenge_patchright(page)
        if enter_ok:
            return True

        # 两种免费方法都失败（已不再支持付费打码服务）
        print("[captcha] ❌ 两种免费方法均失败", flush=True)
        return False

    @staticmethod
    def _human_press_hold(page, cx, cy, hold_ms_min=4400, hold_ms_max=5300):
        """v7.62 人类化 press-and-hold：平滑接近 + 按下期间随机 jitter + 随机时长。
        修复 Microsoft 升级 PerimeterX 行为打分后机械 mouse.move/down/up 被识别为 bot 的问题。"""
        import random as _r
        # 平滑接近：从 60-120px 外开始，分 8-14 步移动到目标
        _sx = cx + _r.uniform(-130, -60) * _r.choice([-1, 1])
        _sy = cy + _r.uniform(-90, -40) * _r.choice([-1, 1])
        page.mouse.move(_sx, _sy)
        _steps = _r.randint(8, 14)
        for _i in range(1, _steps + 1):
            _t = _i / _steps
            _ix = _sx + (cx - _sx) * _t + _r.uniform(-1.8, 1.8)
            _iy = _sy + (cy - _sy) * _t + _r.uniform(-1.8, 1.8)
            try:
                page.mouse.move(_ix, _iy)
            except Exception:
                pass
            page.wait_for_timeout(_r.randint(8, 24))
        # 抵达后短暂停顿（人类反应时间）
        page.wait_for_timeout(_r.randint(80, 220))
        page.mouse.down()
        # 按住期间小幅 jitter（每 180-360ms 一次微动）
        _hold_total = _r.randint(hold_ms_min, hold_ms_max)
        _elapsed = 0
        while _elapsed < _hold_total:
            _wait = _r.randint(180, 360)
            page.wait_for_timeout(_wait)
            _elapsed += _wait
            _jx = cx + _r.uniform(-2.2, 2.2)
            _jy = cy + _r.uniform(-2.2, 2.2)
            try:
                page.mouse.move(_jx, _jy)
            except Exception:
                pass
        page.mouse.up()
        return _hold_total

    def _try_accessibility_challenge(self, page) -> bool:
        """
        点击无障碍挑战按钮（轮椅图标）绕过视觉 CAPTCHA。
        修复：用 locator.click() 替代 bounding_box()+page.mouse.click()，
        避免无头模式下跨域 iframe 坐标返回 None 的问题。
        兜底：JS 注入点击。
        """
        # 等 CAPTCHA iframe 出现
        try:
            page.wait_for_selector(SEL_IFRAME_CHALLENGE, timeout=12000)
        except Exception:
            # 没有 CAPTCHA，也许已通过
            return True

        frame1 = page.frame_locator(SEL_IFRAME_CHALLENGE)

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
                print("[captcha] 无障碍按钮点击失败，continue 到下一次尝试", flush=True)
                continue

            print("[captcha] ✅ 无障碍按钮点击成功", flush=True)
            page.wait_for_timeout(2000)

            # ── 第二次点击：图像拼图加载后，点击其中的轮椅/音频按钮 ─────────
            # FunCaptcha 图像拼图加载后，左下角有音频(轮椅)图标需再次点击
            # 注意：必须用 Playwright loc.bounding_box() 获取 PAGE 绝对坐标
            #       frame.evaluate(getBoundingClientRect) 返回的是 frame 内部坐标，不能直接给 page.mouse.click()
            # v8.77 优化 G: 原固定 3s 等待 → 改 1.5s + 短轮询 frame 增加 (省 1.5s)
            print("[captcha] 等待图像拼图加载(轮询 1.5s)，寻找音频/轮椅按钮…", flush=True)
            _img_t0 = time.time()
            _fr_init = len(page.frames)
            while time.time() - _img_t0 < 1.5:
                if len(page.frames) > _fr_init:
                    break
                page.wait_for_timeout(150)
            _second_click_done = False
            # 先检查 frame 数量是否增加（说明拼图已加载）
            _fr_count_now = len(page.frames)
            print(f"[captcha] 当前 frame 数: {_fr_count_now}", flush=True)

            # 搜索 hsprotect.net frame 中的音频切换按钮
            # 关键：首次点击后 about:blank frames 需要约 10s 才转换为 hsprotect.net
            # 所以先轮询等待转换完成（每秒检查一次，最多等 12 秒）
            # 关键修正：frame.url 不反映 JS 动态写入的 iframe.src（about:blank→hsprotect.net）
            # 必须在每个 frame 内部执行 window.location.href 来获取真实 URL
            print("[captcha] 轮询等待 hsprotect.net frames 加载（最多22s，用内部href检测）…", flush=True)
            _poll_start = time.time()
            _hsp_with_len = []
            while time.time() - _poll_start < 22:
                _all_f = page.frames
                _tmp_hsp = []
                for _fi, _fr in enumerate(_all_f):
                    try:
                        _real_info = _fr.evaluate("""
                            () => ({
                                href: window.location.href,
                                bodyLen: document.body ? document.body.innerHTML.length : 0
                            })
                        """)
                        _real_url = _real_info.get('href', '')
                        _blen = _real_info.get('bodyLen', 0)
                        if 'hsprotect.net' in _real_url and _blen > 3000:
                            _tmp_hsp.append((_blen, _fi, _fr))
                    except Exception:
                        pass
                if len(_tmp_hsp) >= 1:  # 只要找到一个 bodyLen>3000 的 hsprotect frame 就够了
                    _hsp_with_len = _tmp_hsp
                    print(f"[captcha] ✅ 找到 {len(_hsp_with_len)} 个 hsprotect frames (bodyLen>3000, 等待{time.time()-_poll_start:.1f}s)", flush=True)
                    break
                page.wait_for_timeout(1000)

            if not _hsp_with_len:
                print(f"[captcha] ⚠ 22s内未找到 hsprotect frames (bodyLen>3000)，跳过第二次点击", flush=True)
            else:
                _hsp_with_len.sort(reverse=True)  # 最大 body 排前面
                for _blen, _fi, _sfr in _hsp_with_len:
                    print(f"[captcha]   候选 hsp frame[{_fi}] bodyLen={_blen}", flush=True)

            # ── 第二次点击：LainsNL/hrhcode 验证方法 ────────────────────────────
            # 参考: github.com/LainsNL/OutlookRegister, github.com/hrhcode/outlook-batch-manager
            # 方法：frame_locator 链式定位 + 点击 [aria-label="再次按下"]
            # ─────────────────────────────────────────────────────────────────

            # frame_locator 自动处理 iframe 坐标偏移，比 page.frames[] 更可靠
            import random as _rnd
            _TITLE_SELS = [SEL_IFRAME_CHALLENGE, 'iframe[title*="challenge"]',
                           'iframe[title*="Challenge"]', 'iframe[title*="Captcha"]',
                           'iframe[title*="captcha"]']
            _BLOCK_SELS = ['iframe[style*="display: block"]', 'iframe[style*="display:block"]',
                           'iframe:not([style*="display: none"])']

            _frame1 = None
            _frame2 = None
            _a11y_btn = None

            # 尝试所有 title 选择器找 frame1
            for _tsel in _TITLE_SELS:
                try:
                    _f1_try = page.frame_locator(_tsel)
                    # 验证 frame1 存在（尝试找子元素）
                    _f1_count = page.locator(_tsel).count()
                    if _f1_count > 0:
                        print(f"[captcha] ✅ frame_locator frame1: {_tsel} (count={_f1_count})", flush=True)
                        _frame1 = _f1_try
                        break
                    else:
                        print(f"[captcha]   frame1 {_tsel}: count=0", flush=True)
                except Exception as _e1:
                    print(f"[captcha]   frame1 {_tsel}: {_e1}", flush=True)

            if _frame1 is None:
                print("[captcha] ⚠ 未找到 验证质询 frame，使用全页面直接查找", flush=True)
                _frame1 = page  # 回退到页面直接搜索

            # 尝试所有 block 选择器找 frame2（visible iframe）
            for _bsel in _BLOCK_SELS:
                try:
                    _f2_try = _frame1.frame_locator(_bsel)
                    # 验证 frame2 可见
                    _a11y_try = _f2_try.locator(SEL_A11Y_CHALLENGE)
                    _cnt = _a11y_try.count()
                    if _cnt > 0:
                        print(f"[captcha] ✅ frame_locator frame2: {_bsel} (可访问性挑战 count={_cnt})", flush=True)
                        _frame2 = _f2_try
                        _a11y_btn = _a11y_try
                        break
                    else:
                        print(f"[captcha]   frame2 {_bsel}: 可访问性挑战 count=0", flush=True)
                except Exception as _e2:
                    print(f"[captcha]   frame2 {_bsel}: {_e2}", flush=True)

            if _a11y_btn is None:
                # 最后回退：直接在页面中找
                print("[captcha] ⚠ 回退：直接在页面中找 [aria-label='可访问性挑战']", flush=True)
                _a11y_btn = page.locator(SEL_A11Y_CHALLENGE)

            # ── 点击 [aria-label="可访问性挑战"]（轮椅图标，进入音频/按压模式）──
            try:
                _box1 = _a11y_btn.first.bounding_box(timeout=8000)
                if _box1 and _box1['width'] > 0 and _box1['y'] > 0 and _box1['x'] > 0:  # fix: removed hardcoded y<720
                    _cx1 = _box1['x'] + _box1['width'] / 2 + _rnd.randint(-5, 5)
                    _cy1 = _box1['y'] + _box1['height'] / 2 + _rnd.randint(-5, 5)
                    print(f"[captcha] 🖱 点击 可访问性挑战: PAGE=({_cx1:.0f},{_cy1:.0f})", flush=True)
                    page.mouse.click(_cx1, _cy1)
                    _second_click_done = True
                    print("[captcha] ✅ 可访问性挑战已点击（第二次）", flush=True)
                else:
                    print(f"[captcha]   可访问性挑战 bounding_box 无效: {_box1}", flush=True)
            except Exception as _e3:
                print(f"[captcha]   可访问性挑战 点击异常: {_e3}", flush=True)

            # ── 等待 [aria-label="再次按下"] 或 PerimeterX 等效按钮 ──────────
            if _second_click_done:
                page.wait_for_timeout(2000)  # 等待模式切换
                _press_clicked = False

                # 方法A：Arkose "再次按下"（frame2 + page 全局）
                try:
                    _press_again = None
                    if _frame2 is not None:
                        _press_again = _frame2.locator(SEL_PRESS_AGAIN)
                    if _press_again is None or _press_again.count() == 0:
                        _press_again = page.locator(SEL_PRESS_AGAIN)
                    _box2 = _press_again.first.bounding_box(timeout=2000)
                    if _box2 and _box2['width'] > 0:
                        _cx2 = _box2['x'] + _box2['width'] / 2 + _rnd.randint(-10, 10)
                        _cy2 = _box2['y'] + _box2['height'] / 2 + _rnd.randint(-5, 5)
                        print(f"[captcha] 🖱 按住 再次按下: PAGE=({_cx2:.0f},{_cy2:.0f})", flush=True)
                        _held = PatchrightController._human_press_hold(page, _cx2, _cy2, 4400, 5200)
                        print(f"[captcha] ✅ 再次按下已按住{_held/1000:.1f}s！（v7.62 人类化 Arkose press-and-hold）", flush=True)
                        _press_clicked = True
                    else:
                        print(f"[captcha]   再次按下 bounding_box 无效: {_box2}", flush=True)
                except Exception as _e4:
                    print(f"[captcha]   再次按下 异常（Arkose）: {_e4}", flush=True)

                # 方法A-JS：在frame内部用async JS模拟press-hold（规避跨域bounding_box限制）
                if not _press_clicked:
                    _js_frames = ([_frame2] if _frame2 is not None else []) + list(page.frames)
                    for _jfr in _js_frames:
                        try:
                            _js_r = _jfr.evaluate("""
                                async () => {
                                    const labels = ["再次按下","Press","Hold","押下","Нажмите"];
                                    let btn = null;
                                    for (const l of labels) {
                                        btn = document.querySelector(`[aria-label*="${l}"]`);
                                        if (btn) break;
                                    }
                                    if (!btn) btn = document.querySelector('a[role="button"]') ||
                                                    document.querySelector('[tabindex="0"][id]');
                                    if (!btn) return null;
                                    const lbl = btn.getAttribute("aria-label") || btn.id || btn.tagName;
                                    btn.dispatchEvent(new MouseEvent("mousedown", {bubbles:true, cancelable:true}));
                                    await new Promise(r => setTimeout(r, 4500));
                                    btn.dispatchEvent(new MouseEvent("mouseup", {bubbles:true, cancelable:true}));
                                    btn.dispatchEvent(new MouseEvent("click", {bubbles:true, cancelable:true}));
                                    return lbl;
                                }
                            """)
                            if _js_r:
                                print(f"[captcha] ✅ 方法A-JS: frame press-hold 4.5s (btn={_js_r})", flush=True)
                                _press_clicked = True
                                # 验证 press-hold 是否真正通过（检查无障碍按钮 aria-disabled）
                                page.wait_for_timeout(2000)
                                _a11y_still_disabled = False
                                for _chk_fr in page.frames:
                                    try:
                                        _disabled = _chk_fr.evaluate("""
                                            () => {
                                                const btn = document.querySelector('[aria-label="可访问性挑战"], [aria-label="Accessible challenge"], [aria-label="Accessibility challenge"], [aria-label="Audio challenge"]');
                                                return btn ? btn.getAttribute('aria-disabled') : null;
                                            }
                                        """)
                                        if _disabled == 'true':
                                            _a11y_still_disabled = True
                                            break
                                    except Exception:
                                        pass
                                if _a11y_still_disabled:
                                    print("[captcha] ⚠ JS press-hold 未通过验证（aria-disabled仍true）—— 尝试真实鼠标按住px-captcha", flush=True)
                                    # 尝试真实鼠标事件（在 px-captcha 的 page 坐标按住）
                                    _real_hold_done = False
                                    for _rhfr in page.frames:
                                        try:
                                            _px_box = _rhfr.locator('#px-captcha').first.bounding_box(timeout=2000)
                                            if _px_box and _px_box.get('width', 0) > 0:
                                                _rx = _px_box['x'] + _px_box['width'] / 2
                                                _ry = _px_box['y'] + _px_box['height'] / 2
                                                _held_px = PatchrightController._human_press_hold(page, _rx, _ry, 5200, 6300)
                                                print(f"[captcha] ✅ 真实鼠标 px-captcha 按住{_held_px/1000:.1f}s ({_rx:.0f},{_ry:.0f}) [v7.62 人类化]", flush=True)
                                                _real_hold_done = True
                                                break
                                        except Exception:
                                            pass
                                    if not _real_hold_done:
                                        print("[captcha] ⚠ 真实鼠标按住也失败，跳过音频（CAPTCHA将在retry再试）", flush=True)
                                        return False  # 快速退出，节省100+秒
                                else:
                                    print("[captcha] ✅ JS press-hold 已通过验证（aria-disabled 解除）", flush=True)
                                break
                        except Exception:
                            pass

                # 方法B：逐 frame 搜索（针对10-frame重型挑战中的跨域button）
                # page.locator() 无法穿透跨域iframe边界，必须对每个frame单独调用locator()
                if not _press_clicked:
                    print("[captcha] 方法B：逐frame扫描跨域按钮（10-frame重型挑战兜底）…", flush=True)
                    page.wait_for_timeout(2000)

                    # 第一轮：精确找 [aria-label="再次按下"] —— 10-frame变体中在frame[9]
                    for _hfr in page.frames:
                        try:
                            _pa_loc = _hfr.locator(SEL_PRESS_AGAIN)
                            if _pa_loc.count() > 0:
                                # Bug2修复: about:blank 跨域嵌套frame的bounding_box()
                                # 即使返回非None坐标也是frame内坐标而非page坐标，不可信
                                # 统一走dispatch_event，不依赖坐标系
                                _is_cross_origin = ('about:blank' in _hfr.url or
                                                    _hfr.url == '' or
                                                    'hsprotect.net' not in _hfr.url)
                                _pa_box = _pa_loc.first.bounding_box(timeout=2000)
                                _box_usable = (_pa_box and _pa_box.get('width', 0) > 0
                                               and not _is_cross_origin)
                                if _box_usable:
                                    _cx = _pa_box['x'] + _pa_box['width'] / 2
                                    _cy = _pa_box['y'] + _pa_box['height'] / 2
                                    page.mouse.move(_cx, _cy)
                                    page.mouse.down()
                                    page.wait_for_timeout(4500)
                                    page.mouse.up()
                                    print(f"[captcha] ✅ 方法B 再次按下(hold 4.5s, 坐标法) in frame {_hfr.url[:40]}", flush=True)
                                else:
                                    # 跨域/about:blank frame → JS async press-hold（dispatch_event会因frame detach失败）
                                    reason = 'cross-origin' if _is_cross_origin else 'box无效'
                                    print(f"[captcha] 方法B JS-hold({reason}) 4.5s (frame {_hfr.url[:40]})", flush=True)
                                    try:
                                        _js_r2 = _hfr.evaluate("""
                                            async () => {
                                                const labels = ["再次按下","Press","Hold","押下"];
                                                let btn = null;
                                                for (const l of labels) {
                                                    btn = document.querySelector(`[aria-label*="${l}"]`);
                                                    if (btn) break;
                                                }
                                                if (!btn) btn = document.querySelector('a[role="button"]');
                                                if (!btn) return null;
                                                btn.dispatchEvent(new MouseEvent("mousedown", {bubbles:true, cancelable:true}));
                                                await new Promise(r => setTimeout(r, 4500));
                                                btn.dispatchEvent(new MouseEvent("mouseup", {bubbles:true, cancelable:true}));
                                                btn.dispatchEvent(new MouseEvent("click", {bubbles:true, cancelable:true}));
                                                return btn.getAttribute("aria-label") || btn.id || btn.tagName;
                                            }
                                        """)
                                        if _js_r2:
                                            print(f"[captcha] ✅ 方法B JS-hold 4.5s 完成 (btn={_js_r2})", flush=True)
                                    except Exception as _de:
                                        print(f"[captcha]   方法B JS-hold异常: {_de}", flush=True)
                                        try:
                                            _pa_loc.first.click(force=True, timeout=3000)
                                        except Exception:
                                            pass
                                _press_clicked = True
                                page.wait_for_timeout(3000)
                                break
                        except Exception:
                            pass

                    # 第二轮（若仍未找到）：用JS从每个hsprotect.net frame里挖通用按钮
                    if not _press_clicked:
                        for _hfr in page.frames:
                            if "hsprotect.net" not in _hfr.url:
                                continue
                            try:
                                _hinfo = _hfr.evaluate("""() => {
                                    // 优先找带aria-label的交互按钮（PX音频/无障碍）
                                    const PRESS_LABELS = ['再次按下','Press','Hold','Audio','Sound','Listen',
                                                          '음성','Accessibility','accessible'];
                                    let el = null;
                                    // 按aria-label精确匹配
                                    for (const lbl of PRESS_LABELS) {
                                        el = document.querySelector(`[aria-label*="${lbl}"]`);
                                        if (el) break;
                                    }
                                    // 兜底：role=button 或 tabindex=0 的有ID元素
                                    if (!el) {
                                        el = document.querySelector('a[role="button"]') ||
                                             document.querySelector('[tabindex="0"][id]') ||
                                             document.querySelector('button');
                                    }
                                    if (!el) return null;
                                    return {
                                        tag: el.tagName,
                                        id: el.id || '',
                                        href: el.href || el.getAttribute('href') || '',
                                        label: el.getAttribute('aria-label') || '',
                                        text: (el.textContent||'').trim().substring(0, 60),
                                        bodyLen: document.body.innerHTML.length,
                                    };
                                }""")
                                if not _hinfo:
                                    continue
                                print(f"[captcha] 方法B PX frame: tag={_hinfo.get('tag')} id={_hinfo.get('id')} label='{_hinfo.get('label')[:60]}' href={_hinfo.get('href')[:50]} bodyLen={_hinfo.get('bodyLen')}", flush=True)
                                _hid   = _hinfo.get('id', '')
                                _hhref = _hinfo.get('href', '')
                                _clicked_this = False
                                if _hid:
                                    try:
                                        _hloc2 = _hfr.locator(f'#{_hid}')
                                        _hbox2 = _hloc2.first.bounding_box(timeout=2000)
                                        if _hbox2 and _hbox2.get('width', 0) > 0:
                                            _hcx = _hbox2['x'] + _hbox2['width'] / 2
                                            _hcy = _hbox2['y'] + _hbox2['height'] / 2
                                            page.mouse.move(_hcx, _hcy)
                                            page.mouse.down()
                                            page.wait_for_timeout(4500)
                                            page.mouse.up()
                                            _clicked_this = True
                                        else:
                                            raise Exception("bounding_box无效")
                                    except Exception:
                                        # JS async press-hold（规避跨域坐标问题）
                                        try:
                                            _eid = _hid.replace("'", "\'") 
                                            _js_r3 = _hfr.evaluate(f"""
                                                async () => {{
                                                    const el = document.getElementById('{_eid}') ||
                                                               document.querySelector('a[role="button"]') ||
                                                               document.querySelector('[tabindex="0"][id]');
                                                    if (!el) return null;
                                                    el.dispatchEvent(new MouseEvent("mousedown", {{bubbles:true, cancelable:true}}));
                                                    await new Promise(r => setTimeout(r, 4500));
                                                    el.dispatchEvent(new MouseEvent("mouseup", {{bubbles:true, cancelable:true}}));
                                                    el.dispatchEvent(new MouseEvent("click", {{bubbles:true, cancelable:true}}));
                                                    return el.id || el.tagName;
                                                }}
                                            """)
                                            if _js_r3:
                                                print(f"[captcha] ✅ 方法B-JS press-hold 4.5s (el={_js_r3})", flush=True)
                                                _clicked_this = True
                                        except Exception as _je3:
                                            print(f"[captcha]   方法B-JS 异常: {_je3}", flush=True)
                                elif _hhref and any(k in _hhref for k in ['.mp3', '.wav', 'audio', 'sound']):
                                    print(f"[captcha] 方法B 直接音频href: {_hhref[:80]}", flush=True)
                                    _clicked_this = True
                                if _clicked_this:
                                    print(f"[captcha] ✅ 方法B frame按钮已点击", flush=True)
                                    _press_clicked = True
                                    page.wait_for_timeout(4000)
                                    break
                            except Exception as _hpe:
                                print(f"[captcha]   方法B frame异常: {_hpe}", flush=True)

            if not _second_click_done:
                print("[captcha] ⚠ 可访问性挑战按钮未找到，跳过", flush=True)

            # v7.60 早期通过检测：press-hold 完成后若 CAPTCHA 已消失，跳过音频流程
            # 修复：PerimeterX (px-captcha) 路径下 press-hold 已通过但代码继续等音频导致超时失败
            page.wait_for_timeout(3500)
            try:
                _early_solved_reason = None
                # v8.19: 双语 Cancel 检测合并
                if page.get_by_text(TXT_CANCEL_BTN).count() > 0:
                    _early_solved_reason = "出现取消/Cancel 按钮"
                elif page.locator(SEL_IFRAME_CHALLENGE).count() == 0:
                    _early_solved_reason = "验证质询 iframe 已消失"
                else:
                    for _pf_chk in page.frames:
                        try:
                            _u_chk = getattr(_pf_chk, "url", "") or ""
                            if "interrupt" in _u_chk or "passkey" in _u_chk:
                                _early_solved_reason = f"页面已跳到 {_u_chk[:60]}"
                                break
                            # v7.61: 关键检测——所有 hsprotect.net frame 都已清空 = captcha 通过
                            # 注意：success state 是 ALL hsprotect frames bodyLen<1500，因为部分 frame 有外壳<1500 但活跃 frame 仍 >3000
                        except Exception:
                            pass
                    # v7.61: 全量 hsprotect frame 清空检测
                    if not _early_solved_reason:
                        try:
                            _hsp_lens = []
                            for _pf_chk2 in page.frames:
                                _u2 = getattr(_pf_chk2, "url", "") or ""
                                if "hsprotect.net" in _u2:
                                    try:
                                        _bl = _pf_chk2.evaluate("() => document.body ? document.body.innerHTML.length : -1")
                                        if _bl is not None and _bl >= 0:
                                            _hsp_lens.append(_bl)
                                    except Exception:
                                        pass
                            if _hsp_lens and max(_hsp_lens) < 1500:
                                _early_solved_reason = f"所有 hsprotect frame 已清空 (max bodyLen={max(_hsp_lens)}, n={len(_hsp_lens)})"
                        except Exception:
                            pass
                # v7.64 修复：PerimeterX press-hold 通过后，外层 px-captcha 容器仍在 DOM
                #         （bodyLen 还有 4000+），但内部 challenge iframe 会被设为 display:none
                #         或外层 div 内容清空。补上这两条强信号，避免无谓的 20s+5s 音频等待
                #         以及随后徒劳的 Enter键法兜底（blob URL 已消费）造成整轮 CF IP 重试。
                if not _early_solved_reason:
                    try:
                        for _pf_chk_px in page.frames:
                            _u_px = getattr(_pf_chk_px, "url", "") or ""
                            if "hsprotect.net" not in _u_px:
                                continue
                            _px_state = _pf_chk_px.evaluate("""() => {
                                const root = document.getElementById("px-captcha");
                                if (!root) return null;
                                if (root.children.length === 0) return "empty";
                                const inner = root.querySelector("iframe");
                                if (inner) {
                                    const cs = window.getComputedStyle(inner);
                                    if (cs.display === "none" || cs.visibility === "hidden") return "iframe_hidden";
                                }
                                return null;
                            }""")
                            if _px_state == "empty":
                                _early_solved_reason = "PerimeterX 外层 px-captcha 已清空"
                                break
                            if _px_state == "iframe_hidden":
                                _early_solved_reason = "PerimeterX 内部挑战 iframe 已隐藏 (display:none)"
                                break
                    except Exception:
                        pass

                if _early_solved_reason:
                    print(f"[captcha] ✅ press-hold 后早期检测：CAPTCHA 已通过（{_early_solved_reason}）→ 跳过音频流程", flush=True)
                    return True
            except Exception as _early_e:
                print(f"[captcha]   早期检测异常: {_early_e}", flush=True)

            # ── 按住后轮询等待音频加载（最多20s，每2s检查一次网络拦截URL）──
            print("[captcha] 轮询等待音频加载（最多20s）…", flush=True)
            _poll_audio_start = time.time()
            while time.time() - _poll_audio_start < 20:
                _net_now = getattr(self, '_net_audio_urls', [])
                if _net_now:
                    print(f"[captcha] ✅ 网络拦截到音频URL (等待{time.time()-_poll_audio_start:.1f}s): {_net_now[0][:80]}", flush=True)
                    break
                # 快速检测: passkey/enroll 或 interrupt 页面出现 → CAPTCHA 已通过，无需等音频
                _passkey_found = any(
                    'interrupt' in getattr(_pf2, 'url', '') or 'passkey' in getattr(_pf2, 'url', '')
                    for _pf2 in page.frames
                )
                if _passkey_found:
                    print(f"[captcha] ✅ 检测到 passkey/interrupt 页，CAPTCHA 已通过（等待{time.time()-_poll_audio_start:.1f}s），跳过音频等待", flush=True)
                    break
                # 检查 fetching-volume 是否消失（意味着音频已加载）
                _fetch_done = False
                for _pf2 in page.frames:
                    try:
                        _fv = _pf2.evaluate("() => !document.querySelector('.fetching-volume') && document.querySelector('audio')")
                        if _fv:
                            _fetch_done = True
                            break
                    except Exception:
                        pass
                if _fetch_done:
                    print(f"[captcha] ✅ fetching-volume 消失，音频元素已出现（等待{time.time()-_poll_audio_start:.1f}s）", flush=True)
                    break
                page.wait_for_timeout(1000)
            else:
                print("[captcha] ⚠ 20s内音频未加载完成（代理延迟或挑战仍在fetching）", flush=True)

            # v7.60 二次早期检测：20s 后若 CAPTCHA 已消失，跳过深度扫描+音频解析
            try:
                _late_solved_reason = None
                # v8.19: 双语 Cancel 检测合并
                if page.get_by_text(TXT_CANCEL_BTN).count() > 0:
                    _late_solved_reason = "出现取消/Cancel 按钮"
                elif page.locator(SEL_IFRAME_CHALLENGE).count() == 0:
                    _late_solved_reason = "验证质询 iframe 已消失"
                else:
                    # v7.61: 全量 hsprotect frame 清空检测
                    _hsp_lens_l = []
                    for _pf_late in page.frames:
                        try:
                            _u_late = getattr(_pf_late, "url", "") or ""
                            if "hsprotect.net" in _u_late:
                                _bl_l = _pf_late.evaluate("() => document.body ? document.body.innerHTML.length : -1")
                                if _bl_l is not None and _bl_l >= 0:
                                    _hsp_lens_l.append(_bl_l)
                            elif "interrupt" in _u_late or "passkey" in _u_late:
                                _late_solved_reason = f"页面已跳到 {_u_late[:60]}"
                                break
                        except Exception:
                            pass
                    if not _late_solved_reason and _hsp_lens_l and max(_hsp_lens_l) < 1500:
                        _late_solved_reason = f"所有 hsprotect frame 已清空 (max bodyLen={max(_hsp_lens_l)})"
                if _late_solved_reason:
                    print(f"[captcha] ✅ 20s 后二次检测：CAPTCHA 已通过（{_late_solved_reason}）→ 跳过深度扫描", flush=True)
                    return True
            except Exception:
                pass

            # 额外缓冲：若 fetching-volume 仍在则再等 5s
            _fv_still = False
            for _fvf in page.frames:
                try:
                    _fv_still = _fvf.evaluate("() => !!document.querySelector('.fetching-volume')")
                    if _fv_still:
                        break
                except Exception:
                    pass
            if _fv_still:
                print("[captcha] ⚠ fetching-volume 仍在加载，再等5s…", flush=True)
                page.wait_for_timeout(5000)

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
                page.wait_for_timeout(1500)
                try:
                    page.wait_for_selector(SEL_IFRAME_CHALLENGE, state="detached", timeout=8000)
                    return True
                except Exception:
                    if (page.get_by_text(TXT_CANCEL_BTN).count() > 0
                            or page.get_by_text(TXT_UNUSUAL_ACTIVITY).count() == 0):
                        return True
                    return False

            # ── 兜底：检查页面是否已通过 ─────────────────────────────────────
            page.wait_for_timeout(1500)
            try:
                if page.get_by_text(TXT_CANCEL_BTN).count() > 0:
                    print("[captcha] ✅ 出现取消按钮，认为已通过", flush=True)
                    return True
                if (page.get_by_text(TXT_UNUSUAL_ACTIVITY).count()
                        or page.get_by_text(TXT_SITE_MAINTENANCE).count()):
                    return False
            except Exception as nav_err:
                if "context was destroyed" in str(nav_err).lower() or "navigation" in str(nav_err).lower():
                    print("[captcha] ✅ 页面已导航（上下文销毁），认为 CAPTCHA 通过", flush=True)
                    return True
                raise
            try:
                page.wait_for_selector(SEL_IFRAME_CHALLENGE, timeout=2000)
                # v7.61 终极兜底：iframe 在 DOM 中但 hsprotect 内容已清空 → 视为通过
                try:
                    for _pf_final in page.frames:
                        _u_final = getattr(_pf_final, "url", "") or ""
                        if "hsprotect.net" in _u_final:
                            _blen_final = _pf_final.evaluate("() => document.body ? document.body.innerHTML.length : 0")
                            if _blen_final is not None and 0 < _blen_final < 1500:
                                print(f"[captcha] ✅ 终极兜底：hsprotect frame 已清空 (bodyLen={_blen_final}) → CAPTCHA 通过", flush=True)
                                return True
                except Exception:
                    pass
                # v7.64 终极兜底：press-hold 通过后 PerimeterX 可能保留 px-captcha 外壳但
                # （a）外层 div 内容清空，或（b）内部 challenge iframe 设为 display:none。
                # 这两种状态 bodyLen 仍 >1500（因为 <script> 体），v7.61 检测捕捉不到，
                # 但视觉上 captcha 已通过——这是 g4ljon 类型 job 浪费 CF IP 的根因。
                try:
                    for _pf_px in page.frames:
                        _u_px = getattr(_pf_px, "url", "") or ""
                        if "hsprotect.net" not in _u_px:
                            continue
                        _px_state = _pf_px.evaluate("""() => {
                            const root = document.getElementById("px-captcha");
                            if (!root) return null;
                            if (root.children.length === 0) return "empty";
                            const inner = root.querySelector("iframe");
                            if (inner) {
                                const cs = window.getComputedStyle(inner);
                                if (cs.display === "none" || cs.visibility === "hidden") return "iframe_hidden";
                            }
                            return null;
                        }""")
                        if _px_state == "empty":
                            print("[captcha] ✅ v7.64 终极兜底：PerimeterX 外层 px-captcha 已清空 → CAPTCHA 通过", flush=True)
                            return True
                        if _px_state == "iframe_hidden":
                            print("[captcha] ✅ v7.64 终极兜底：PerimeterX 内部挑战 iframe 已隐藏 (display:none) → CAPTCHA 通过", flush=True)
                            return True
                except Exception:
                    pass
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
        play_btn_frames = []  # frames where we found a play button (but no src yet)

        # 优先使用 handle_captcha 顶部拦截到的 URL
        _net_urls = getattr(self, '_net_audio_urls', [])
        if _net_urls:
            audio_url = _net_urls[0]
            print(f"[captcha] ✅ 使用早期网络拦截URL: {audio_url[:100]}", flush=True)

        def _scan_frames_for_audio(frames):
            nonlocal audio_url, audio_frame, input_frame
            if audio_url:  # 已有网络拦截到的URL，跳过DOM扫描
                return
            for fr in frames:
                try:
                    info = fr.evaluate("""
                        () => {
                            // FIX: use querySelector('audio') and read .src property (not attribute)
                            const audio = document.querySelector('audio');
                            let audioSrc = '';
                            if (audio) {
                                audioSrc = audio.src || '';
                                if (!audioSrc) {
                                    const srcEl = audio.querySelector('source');
                                    if (srcEl) audioSrc = srcEl.src || srcEl.getAttribute('src') || '';
                                }
                            }
                            if (!audioSrc) {
                                const aLinks = Array.from(document.querySelectorAll('a[href]')).filter(a =>
                                    a.href && (a.href.includes('.mp3') || a.href.includes('.wav') ||
                                               a.href.includes('.ogg') || a.href.includes('audio') ||
                                               a.href.includes('sound') || a.href.includes('speak')));
                                if (aLinks.length) audioSrc = aLinks[0].href;
                            }
                            // Performance API fallback: find audio URLs loaded by the browser
                            let perfAudioUrl = '';
                            try {
                                const entries = performance.getEntriesByType('resource');
                                const ae = entries.find(e =>
                                    e.initiatorType !== 'beacon' && (
                                    e.name.includes('.mp3') || e.name.includes('.wav') ||
                                    e.name.includes('.ogg') || e.name.includes('/audio/') ||
                                    e.name.includes('audio-challenge') || e.name.includes('funcaptcha') ||
                                    (e.name.includes('hsprotect.net') && e.name.includes('audio'))));
                                if (ae) perfAudioUrl = ae.name;
                            } catch(e) {}
                            const input = document.querySelector('input[type="text"], input[type="tel"], input[placeholder], textarea');
                            const playBtn = document.querySelector(
                                '[class*="play"],[aria-label*="play"],[aria-label*="Play"],' +
                                'a[role="button"],button[class*="audio"],button[class*="sound"],' +
                                '[data-cy*="audio"],[id*="audio"],[id*="sound"]');
                            return {
                                audioSrc: audioSrc || perfAudioUrl,
                                perfAudioUrl: perfAudioUrl,
                                hasInput: !!input,
                                inputPlaceholder: input ? input.placeholder : '',
                                hasPlayBtn: !!playBtn,
                                playBtnId: playBtn ? (playBtn.id || '') : '',
                                bodyLen: document.body ? document.body.innerHTML.length : 0,
                                url: window.location.href
                            };
                        }
                    """)
                    print(f"[captcha] frame {fr.url[:50]}: audioSrc={info.get('audioSrc','')[:60]} hasInput={info.get('hasInput')} hasPlayBtn={info.get('hasPlayBtn')}", flush=True)

                    if info.get('audioSrc') and not audio_url:
                        audio_url = info['audioSrc']
                        audio_frame = fr
                    if info.get('hasInput'):
                        input_frame = fr
                    # Bug3修复: bodyLen<1500的frame是无障碍轮椅图标(约652字节)，非音频播放器
                    _btn_body_len = info.get('bodyLen', 0) if hasattr(info, 'get') else 0
                    # 额外过滤：aria-label="可访问性挑战"的按钮是轮椅图标，不是音频播放器
                    _is_a11y_only = False
                    try:
                        _a11y_check = fr.evaluate("""
                            () => {
                                const btn = document.querySelector('[aria-label="可访问性挑战"], [aria-label="Accessible challenge"], [aria-label="Accessibility challenge"], [aria-label="Audio challenge"]');
                                const hasAudio = document.querySelector('[aria-label*="play"],[aria-label*="Play"],[class*="play"],[class*="audio"]');
                                return { isA11yOnly: !!btn && !hasAudio };
                            }
                        """)
                        _is_a11y_only = _a11y_check.get('isA11yOnly', False)
                    except Exception:
                        pass
                    if info.get('hasPlayBtn') and not audio_url and _btn_body_len > 1500 and not _is_a11y_only:
                        play_btn_frames.append((fr, info.get('playBtnId', '')))
                    elif info.get('hasPlayBtn') and (_btn_body_len <= 1500 or _is_a11y_only):
                        reason = f"bodyLen={_btn_body_len}<1500" if _btn_body_len <= 1500 else "是可访问性挑战按钮(非音频)"
                        print(f"[captcha] 跳过假阳性playBtn frame ({reason}, url={fr.url[:40]})", flush=True)
                except Exception:
                    pass

        _scan_frames_for_audio(all_frames)

        # 若未找到音频但找到了播放按钮 → 点击后重试
        if not audio_url and play_btn_frames:
            print(f"[captcha] 找到 {len(play_btn_frames)} 个播放按钮，点击触发音频加载…", flush=True)
            for _pf, _pid in play_btn_frames[:3]:
                try:
                    if _pid:
                        _pf.locator(f'#{_pid}').first.click(force=True, timeout=3000)
                    else:
                        _pf.locator('a[role="button"],button').first.click(force=True, timeout=3000)
                    print(f"[captcha] 播放按钮已点击 (frame: {_pf.url[:40]})", flush=True)
                except Exception as _pbe:
                    try:
                        _pf.evaluate("(document.querySelector('a[role=\"button\"],button') || {}).click && document.querySelector('a[role=\"button\"],button').click()")
                    except Exception:
                        pass
            page.wait_for_timeout(8000)  # 等音频加载（增加到8s）
            # FIX: check _net_audio_urls first - play click triggers audio XHR
            _fresh_net = getattr(self, '_net_audio_urls', [])
            if _fresh_net:
                print(f"[captcha] play后网络拦截到音频URL: {_fresh_net[0][:100]}", flush=True)
                audio_url = _fresh_net[0]
            else:
                print("[captcha] 点击播放按钮后重新扫描…", flush=True)
                audio_url = None; audio_frame = None; input_frame = None
                _scan_frames_for_audio(page.frames)

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

            # ── 音频转写：Google 免费 STT（speech_recognition + 系统 ffmpeg）────
            transcript = ""
            try:
                import speech_recognition as sr
                import subprocess as _sp

                # 找 ffmpeg（系统可能在 Nix store 里）
                _ffmpeg = None
                for _fp in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg",
                            "/nix/store/ynlnyy6rn70kvzamy3b40bp3qlz70mn0-ffmpeg-full-7.1.1-bin/bin/ffmpeg"]:
                    if os.path.isfile(_fp):
                        _ffmpeg = _fp
                        break
                if not _ffmpeg:
                    _result = _sp.run(["which", "ffmpeg"], capture_output=True, text=True, timeout=5)
                    _fp = _result.stdout.strip()
                    if _fp and os.path.isfile(_fp):
                        _ffmpeg = _fp

                # 将 mp3/任意格式 → wav（speech_recognition 只接受 wav）
                wav_file = tmp_audio.replace(".mp3", ".wav").replace(".ogg", ".wav")
                if not wav_file.endswith(".wav"):
                    wav_file = tmp_audio + ".wav"

                if _ffmpeg:
                    _sp.run([_ffmpeg, "-y", "-i", tmp_audio, "-ar", "16000", "-ac", "1",
                             "-acodec", "pcm_s16le", wav_file],
                            capture_output=True, timeout=20)
                    print(f"[captcha] ffmpeg 转换完成: {wav_file}", flush=True)
                else:
                    wav_file = tmp_audio  # 直接尝试（若本身是 wav）

                # Google 免费 STT（无需 API key）
                _recognizer = sr.Recognizer()
                with sr.AudioFile(wav_file) as _src:
                    _audio_data = _recognizer.record(_src)
                transcript = _recognizer.recognize_google(_audio_data, language="en-US")
                print(f"[captcha] ✅ Google STT 转写成功: '{transcript}'", flush=True)
            except Exception as _stt_err:
                print(f"[captcha] ⚠ Google STT 失败: {_stt_err}", flush=True)
                transcript = ""

            if not transcript:
                print("[captcha] ⚠ 无转写内容，音频路径降级失败", flush=True)
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
        """使用 Enter 键 + hsprotect.net 流量监听"""
        ok = self._try_enter_challenge(page)
        return ok

    def _try_enter_challenge(self, page) -> bool:
        """原版 Enter键 + hsprotect.net 流量监听逻辑"""
        try:
            page.wait_for_event(
                "request",
                lambda req: req.url.startswith("blob:https://iframe.hsprotect.net/"),
                timeout=5000,
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
                    if (page.get_by_text(TXT_UNUSUAL_ACTIVITY).count()
                            or page.get_by_text(TXT_SITE_MAINTENANCE).count()):
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



# ─── Camoufox 控制器 (Firefox native fingerprint) ────────────────────────────
class CamoufoxController(BaseController):
    """
    Firefox-based controller using camoufox.
    Native Canvas/WebGL fingerprinting — passes Cloudflare integrity checks
    that Chromium-based drivers cannot pass.
    Sync API: camoufox.sync_api.Camoufox
    """

    def launch(self, headless=True):
        from camoufox.sync_api import Camoufox
        # camoufox 需要 proxy 为 dict (server / username / password)
        proxy_arg = None
        if self.proxy:
            m = re.match(r"(socks5h?|http|https)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)", self.proxy)
            if m:
                scheme, u, pw, host, port = m.groups()
                proxy_arg = {"server": f"{scheme}://{host}:{port}"}
                if u and pw:
                    proxy_arg["username"] = u; proxy_arg["password"] = pw
            else:
                proxy_arg = {"server": self.proxy}

        class _PwCompat:
            """Wraps camoufox context manager to expose p.stop() like sync_playwright()."""
            def __init__(self_, cm):
                self_._cm = cm
            def stop(self_):
                try: self_._cm.__exit__(None, None, None)
                except Exception: pass

        cm = Camoufox(headless=headless, proxy=proxy_arg, os="windows", geoip=True)
        browser = cm.__enter__()
        return _PwCompat(cm), browser

    def handle_captcha(self, page, blob_container=None):
        return self._try_enter_challenge(page)

    def _try_enter_challenge(self, page) -> bool:
        """Enter key + hsprotect.net traffic monitoring (same as PlaywrightController)."""
        try:
            page.wait_for_event(
                "request",
                lambda req: req.url.startswith("blob:https://iframe.hsprotect.net/"),
                timeout=5000,
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
                    if (page.get_by_text(TXT_UNUSUAL_ACTIVITY).count()
                            or page.get_by_text(TXT_SITE_MAINTENANCE).count()):
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


def _skip_ms_interrupts(page, label="") -> bool:
    """
    Dismiss Microsoft interrupt/nag pages that appear after registration:
      - Passkey enroll  (account.live.com/interrupt/passkey/enroll)
      - Stay signed in? (login.live.com)
      - Recovery email / phone nag
      - "Don't show again" checkbox pages
    Returns True if any button was clicked.
    """
    clicked = False
    skip_selectors = [
        # Passkey: "Maybe later" / "Skip for now"
        'button:has-text("Skip for now")',
        'button:has-text("Maybe later")',
        'button:has-text("Not now")',
        'button:has-text("Skip")',
        'a:has-text("Skip for now")',
        'a:has-text("Maybe later")',
        # 中文变体
        'button:has-text("跳过")',
        'button:has-text("稍后")',
        'button:has-text("暂时跳过")',
        'button:has-text("以后再说")',
        'a:has-text("跳过")',
        # Stay signed in → No
        'input[type="submit"][value="No"]',
        'button:has-text("No")',
        # Secondary / cancel buttons (catch-all for interrupt pages)
        '[data-testid="secondaryButton"]',
        '#idBtn_Back',
    ]
    for sel in skip_selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500):
                loc.click()
                page.wait_for_timeout(1500)
                if label:
                    print(f"[skip-interrupt] {label} clicked: {sel}", flush=True)
                clicked = True
                break
        except Exception:
            continue
    return clicked


def get_oauth_token_in_browser(page, email: str, captcha_handler=None) -> dict:
    """
    在已登录的浏览器 session 中做 OAuth2 authorization_code 授权。
    使用 prompt=consent（新账号首次授权必须经过 consent，prompt=none 会返回 consent_required）。
    自动捕获同意页面并点击 Accept 按钮，无需人工介入。
    captcha_handler: 可选，接受 (page) 参数的函数，在检测到 CAPTCHA 时调用。
    """
    import urllib.parse as _up, urllib.request as _ur, json as _json

    CLIENT_ID    = '9e5f94bc-e8a4-4e73-b8be-63364c29d753'
    REDIRECT_URI = 'https://login.microsoftonline.com/common/oauth2/nativeclient'
    SCOPES = [
        'offline_access',
        'https://graph.microsoft.com/Mail.Read',
        'https://graph.microsoft.com/Mail.ReadWrite',
        'https://graph.microsoft.com/Mail.Send',
        'https://graph.microsoft.com/User.Read',
    ]
    SCOPE = ' '.join(SCOPES)

    captured = {'code': None, 'error': None, 'error_description': None}

    # ── 安装路由拦截器，捕获 nativeclient 重定向中的 code ──────────────────────
    def _intercept_nativeclient(route, request):
        url = request.url
        if 'code=' in url or 'error=' in url:
            params = _up.parse_qs(_up.urlparse(url).query)
            if 'code' in params and not captured['code']:
                captured['code'] = params['code'][0]
                print(f'[oauth] ✅ 路由拦截到授权码 (code={params["code"][0][:12]}...)', flush=True)
            elif 'error' in params and not captured['error']:
                captured['error'] = params['error'][0]
                captured['error_description'] = params.get('error_description', [''])[0]
        try:
            route.abort()
        except Exception:
            pass

    try:
        # v8.43 ROOT-FIX 2026-04-28: 歧义 bug — glob "**/nativeclient**" 同样会匹配 authorize 页
        # (URL 中 redirect_uri=https%3A%2F%2F...%2Fnativeclient 的 query value 含 nativeclient 子串)
        # → route.abort() 中止真正的 authorize 导航 → OAuth 流程基础不稳定.
        # 改用 path-only 正则: 只匹配 URL path 含 /nativeclient, 完全绕开 query value.
        _NC_PATH_RE = re.compile(r"^https?://[^/]+/[^?]*nativeclient", re.IGNORECASE)
        page.route(_NC_PATH_RE, _intercept_nativeclient)
    except Exception as _re:
        print(f'[oauth] ⚠ 路由拦截器安装失败: {_re}', flush=True)

    try:
        # Dismiss passkey/interrupt pages before navigating to OAuth consent
        _skip_ms_interrupts(page, label='pre-oauth')
        scope_encoded = '%20'.join(_up.quote(s, safe=':/') for s in SCOPES)
        auth_url = (
            'https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize'
            f'?client_id={CLIENT_ID}'
            '&response_type=code'
            f'&redirect_uri={_up.quote(REDIRECT_URI, safe="")}'
            f'&scope={scope_encoded}'
            '&prompt=consent'
            f'&login_hint={_up.quote(email, safe="")}'
        )
        print(f'[oauth] 导航到授权页（prompt=consent）...', flush=True)
        try:
            page.goto(auth_url, timeout=20000, wait_until='domcontentloaded')
        except Exception:
            pass  # nativeclient redirect 造成 navigation error 属正常

        # 等待页面加载
        page.wait_for_timeout(3000)

        # ── 统一 OAuth 页面处理循环 ──────────────────────────────────────────────
        # 轮询检测页面状态，动态决策：同意页/重CAPTCHA页/其他，最多 7 轮 × ~8s
        _CONSENT_ACCEPT_SELS = [
            '[data-testid="appConsentPrimaryButton"]',
            'input[type="submit"][value*="Accept"]',
            'input[type="submit"][value*="Approve"]',
            'button:has-text("Accept")',
            'button:has-text("Approve")',
            'button:has-text("接受")',
            'button:has-text("允许")',
            'button:has-text("继续")',
            'input[value="Continue"]',
        ]

        def _try_click_accept() -> bool:
            for _sel in _CONSENT_ACCEPT_SELS:
                try:
                    btn = page.locator(_sel).first
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        print(f'[oauth] ✅ 点击同意按钮: {_sel}', flush=True)
                        page.wait_for_timeout(3000)
                        return True
                except Exception:
                    continue
            return False

        # v8.73 Bug1: 卡 URL 早退 + 强力按钮兜底 (避免 7 轮空轮 65s 浪费)
        _stuck_same_url_count = 0
        _last_url = ''
        for _poll_round in range(5):
            _poll_url = page.url or ''
            _is_same = (_poll_url == _last_url)
            _last_url = _poll_url
            _stuck_same_url_count = (_stuck_same_url_count + 1) if _is_same else 0
            print(f'[oauth] 轮询 round={_poll_round+1} url={_poll_url[:80]} stuck={_stuck_same_url_count}', flush=True)
            # v8.78 Bug K: 检测到 sign-in/login 页 (注册 cookie 缺失 → OAuth 要求重新登录) 立即 bail
            # 实测 elizabethcollins675 case: 5 轮 Next click 在 sign-in 页死循环, 浪费 35s
            # bail 后 device-code fallback 接管 (~10s), 比死循环快 25s
            if 'oauth20_authorize.srf' in _poll_url and _poll_round >= 1:
                try:
                    _signin_marker = page.evaluate("""()=>{const t=document.body?.innerText||'';return (t.includes('Use your Microsoft account')||t.includes('使用 Microsoft 帐户'))&&!!document.querySelector('input[type=email],input[name=loginfmt]');}""")
                    if _signin_marker:
                        print(f'[oauth] ⛔ 检测到 sign-in 页 (注册 cookie 失效 / OAuth 要求重新登录) → 立即 bail, 由设备码 fallback 接管', flush=True)
                        break
                except Exception:
                    pass
            # v8.77 oauth 卡页探针: 每轮截图 + dump 可见按钮文本, 用于事后 visual debugging
            try:
                _shot_p = f"/tmp/oauth_round_{email}_{_poll_round+1}.png"
                page.screenshot(path=_shot_p)
                _btns = page.evaluate("""()=>{const els=[...document.querySelectorAll('button,input[type=submit],a[role=button],[data-testid]')]; return els.slice(0,10).map(e=>({tag:e.tagName,tid:e.getAttribute('data-testid')||'',txt:(e.innerText||e.value||'').slice(0,40),vis:!!(e.offsetParent)}));}""")
                _vis_btns = [b for b in (_btns or []) if b.get('vis')]
                print(f'[oauth] 📸 round{_poll_round+1} 可见按钮({len(_vis_btns)}): ' + ' | '.join(f"{b.get('tid') or b.get('tag')}:'{b.get('txt')}'" for b in _vis_btns[:6]), flush=True)
            except Exception as _shote:
                print(f'[oauth] ⚠ round{_poll_round+1} 探针失败: {_shote}', flush=True)

            # v8.41 ROOT-FIX 2026-04-28 — 删除"已到达终止页"误导判定
            # 原 v8.36/v8.37 判定 (基于 parse_qs 解析 query 名 + 排除 oauth20_authorize) 实证 reg_1777341248972 仍误命中 → break → wait_for_url 30s 超时 → no_redirect.
            # 真终止 = nativeclient redirect 已发生 (OAuth 标准 callback URI).
            # nativeclient 检测下移到 elif 链里 (精确, 不参与终止页"判定"); 其他真终止靠循环外 wait_for_url 兜底.

            # nativeclient redirect 已捕获 → OAuth code 拿到, 退出循环
            # v8.42 ROOT-FIX 2026-04-28: 'nativeclient' 子串会被 redirect_uri query value 误命中
            # (REDIRECT_URI='https://login.microsoftonline.com/common/oauth2/nativeclient' URL-encoded 进 query → page.url 含 nativeclient 子串)
            # 必须用 urlparse 提取 path 单独判定.
            try:
                from urllib.parse import urlparse as _urpX
                _path_X = _urpX(_poll_url).path or ''
            except Exception:
                _path_X = ''
            if 'nativeclient' in _path_X:
                print(f'[oauth] ✅ nativeclient redirect 捕获 (path={_path_X}) → break', flush=True)
                break

            # 同意页（account.live.com/Consent）：原地等待并尝试 Accept
            if 'account.live.com/Consent' in _poll_url:
                if _try_click_accept():
                    break
                if captcha_handler:
                    print(f'[oauth] ⚠ consent页Accept未出现，原地调用CAPTCHA处理器...', flush=True)
                    try:
                        captcha_handler(page)
                        page.wait_for_timeout(2000)
                    except Exception as _ce:
                        print(f'[oauth] consent CAPTCHA处理异常: {_ce}', flush=True)
                else:
                    page.wait_for_timeout(5000)

            # 重 CAPTCHA 页（仅 signup.live.com）：handler 后重导航
            # v8.41 ROOT-FIX: 移除 'login.live.com/oauth' 子串 — 它会匹配 oauth20_authorize.srf 正常 OAuth 中间页 → 误判为重 CAPTCHA → 调 handler 失败 → 失去 round 浪费.
            elif 'signup.live.com' in _poll_url:
                print(f'[oauth] ⚠ 重CAPTCHA页 (signup.live.com)，调用处理器 (round={_poll_round+1})...', flush=True)
                if captcha_handler:
                    try:
                        captcha_handler(page)
                        page.wait_for_timeout(3000)
                    except Exception as _ce:
                        print(f'[oauth] CAPTCHA处理异常: {_ce}', flush=True)
                _after_url = page.url or ''
                # v8.42 ROOT-FIX 2026-04-28: 子串匹配会被 query value 误命中 (nativeclient/oauth20_authorize/authorize 都在 redirect_uri query 里) → 全用 path 判定
                try:
                    from urllib.parse import urlparse as _urp2, parse_qs as _pqs2
                    _parsed2 = _urp2(_after_url) if _after_url else None
                    _path2 = (_parsed2.path or '') if _parsed2 else ''
                    _qs2   = _pqs2(_parsed2.query) if _parsed2 else {}
                except Exception:
                    _path2 = ''
                    _qs2 = {}
                _is_authz2 = ('oauth20_authorize' in _path2) or _path2.endswith('/authorize') or _path2.endswith('/authorize/')
                if ('nativeclient' in _path2
                        or ('code' in _qs2 and not _is_authz2)
                        or ('error' in _qs2 and not _is_authz2)):
                    break
                print(f'[oauth] 重新导航授权页...', flush=True)
                try:
                    page.goto(auth_url, timeout=20000, wait_until='domcontentloaded')
                except Exception:
                    pass
                page.wait_for_timeout(3000)

            # 已落入 Outlook 收件箱（OAuth code 未被捕获），重新导航授权页
            elif ('outlook.live.com/mail' in _poll_url or
                  ('live.com' in _poll_url and '/mail/' in _poll_url) or
                  'msalAuthRedirect' in _poll_url):
                print(f'[oauth] ⚠ 已落入收件箱(URL={_poll_url[:60]})，重新导航授权页 (round={_poll_round+1})...', flush=True)
                try:
                    page.goto(auth_url, timeout=20000, wait_until='domcontentloaded')
                except Exception:
                    pass
                page.wait_for_timeout(3000)

            # 其他页（login、interrupt、未知）：驱散中断 → 强力按钮 → 早退
            else:
                _clicked = _skip_ms_interrupts(page, label=f'oauth-poll-{_poll_round}')
                # v8.73 Bug1: 卡在 oauth20_authorize.srf / login.live.com 时, 尝试常见 forward 按钮
                # v8.76 Bug D: 加 [data-testid="primaryButton"] (MS React UI 标准按钮, Next/Continue/Accept 都用同一 testid)
                #              加 [data-testid="secondaryButton"] 在 stuck>=1 时启用 (跳过 passkey/recovery 等可选步)
                if not _clicked:
                    _primary_sels = (
                        '[data-testid="primaryButton"]',  # v8.76 Bug D: MS React UI primary
                        'input[type="submit"][value="Yes"]',
                        'button:has-text("Yes")', 'button:has-text("是")',
                        'input[type="submit"][value="Continue"]',
                        'button:has-text("Continue")', 'button:has-text("继续")',
                        'input[type="submit"][value="Next"]',
                        'button:has-text("Next")', 'button:has-text("下一步")',
                        'input[type="submit"][value="Accept"]',
                        'button:has-text("Accept")', 'button:has-text("接受")',
                        'button:has-text("Approve")', 'button:has-text("允许")',
                        '#idSIButton9', '[data-report-event="Signin_Submit"]',
                    )
                    # v8.76 Bug D: stuck>=1 → primary 已点过没用 → 启用 secondary 跳过 (Skip/Maybe later/Cancel)
                    _secondary_sels = (
                        '[data-testid="secondaryButton"]',
                        'button:has-text("Skip for now")',
                        'button:has-text("Maybe later")',
                        'button:has-text("Not now")',
                        'button:has-text("Skip")',
                        'button:has-text("跳过")',
                        'button:has-text("稍后")',
                        '#idBtn_Back',
                    ) if _stuck_same_url_count >= 1 else ()
                    for _bsel in (_secondary_sels + _primary_sels):
                        try:
                            _b = page.locator(_bsel).first
                            if _b.is_visible(timeout=800):
                                _b.click()
                                _tag = 'secondary' if _bsel in _secondary_sels else 'primary'
                                print(f'[oauth] ✅ 强力按钮点击 [{_tag}]: {_bsel}', flush=True)
                                page.wait_for_timeout(2500)
                                _clicked = True
                                break
                        except Exception:
                            continue
                # v8.76 Bug D: 成功 click 但 URL 不变 ≠ 真 stuck (MS React UI 是 SPA 内部翻页, URL 不动)
                # 重置 stuck 计数, 给 SPA 多轮翻页机会; 只有"既无按钮可点 又 URL 不动"才算真 stuck
                if _clicked and _stuck_same_url_count > 0:
                    print(f'[oauth] ↻ click 成功 → 重置 stuck 计数 (SPA 内部翻页, URL 不变)', flush=True)
                    _stuck_same_url_count = 0
                    _last_url = ''  # 下轮不会被判同 URL
                # v8.73 Bug1 / v8.76 Bug D: 真 stuck (无按钮 + URL 不变 2 轮) → 早退
                if (not _clicked) and _stuck_same_url_count >= 2:
                    print(f'[oauth] ⏭ URL 连续 {_stuck_same_url_count+1} 轮未变且无可点按钮 → 早退', flush=True)
                    break
                page.wait_for_timeout(2000)
        # ────────────────────────────────────────────────────────────────────────

        # 使用 wait_for_url 等待 nativeclient 重定向（最多30s）
        # v8.37 ROOT-FIX: 与 v8.36 同源 — 子串 'code=' 会被 'response_type=code' 误命中
        if not captured['code'] and not captured['error']:
            def _is_terminal(u: str) -> bool:
                # v8.42 ROOT-FIX 2026-04-28: 子串匹配会被 query value 误命中 → 全用 path
                if not u:
                    return False
                try:
                    from urllib.parse import urlparse as _urp3, parse_qs as _pqs3
                    _parsed3 = _urp3(u)
                    _path3   = _parsed3.path or ''
                    _qs3     = _pqs3(_parsed3.query)
                except Exception:
                    return False
                if 'nativeclient' in _path3:
                    return True
                _is_authorize = ('oauth20_authorize' in _path3) or _path3.endswith('/authorize') or _path3.endswith('/authorize/')
                if _is_authorize:
                    return False
                return ('code' in _qs3) or ('error' in _qs3)
            try:
                page.wait_for_url(_is_terminal, timeout=8000)  # v8.73 Bug1: 30s→8s
            except Exception:
                pass

        # 从当前 URL 捕捉 code / error
        cur = page.url or ''
        if '?' in cur:
            params = _up.parse_qs(_up.urlparse(cur).query)
            if 'code' in params:
                captured['code'] = params['code'][0]
            elif 'error' in params:
                captured['error']             = params['error'][0]
                captured['error_description'] = params.get('error_description', [''])[0]
    except Exception as e:
        print(f'[oauth] 授权导航异常: {e}', flush=True)
        return {}

    code = captured['code']
    if not code:
        err  = captured.get('error') or 'no_redirect'
        desc = (captured.get('error_description') or '未捕获到重定向')[:150]
        print(f'[oauth] ❌ 授权失败 [{err}]: {desc}', flush=True)
        return {}

    print('[oauth] ✅ 获取到授权码，正在换取 token...', flush=True)
    try:
        token_body = _up.urlencode({
            'grant_type':   'authorization_code',
            'client_id':    CLIENT_ID,
            'code':         code,
            'redirect_uri': REDIRECT_URI,
            'scope':        SCOPE,
        }).encode()
        req = _ur.Request(
            'https://login.microsoftonline.com/consumers/oauth2/v2.0/token',
            data=token_body,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )
        resp = _json.loads(_ur.urlopen(req, timeout=15).read())
        if resp.get('access_token'):
            rt = resp.get('refresh_token', '')
            ei = resp.get('expires_in', 3600)
            print(f'[oauth] ✅ {email} 授权成功！expires_in={ei}s, refresh_token={rt[:20]}...', flush=True)
            return {'access_token': resp['access_token'], 'refresh_token': rt, 'expires_in': ei}
        else:
            print(f'[oauth] ❌ token 交换失败: {resp.get("error")} - {resp.get("error_description","")[:80]}', flush=True)
            return {}
    except Exception as e:
        print(f'[oauth] ❌ token 请求异常: {e}', flush=True)
        return {}


def register_one(ctrl, engine_name: str, headless: bool, planned_username: str = "", planned_password: str = "", exit_ip: str = "", proxy_port: int = 0, proxy_formatted: str = "") -> dict:
    if planned_username:
        email = planned_username.split("@")[0].strip()
    else:
        username, fn_eng, ln_eng = gen_email_username()
        email = username
    password = planned_password.strip() if planned_password else gen_password()
    result   = {
        "email": f"{email}@outlook.com",
        "username": email,
        "password": password,
        "success": False,
        "error": "",
        "elapsed": "",
        "engine": engine_name,
        # v8.20: identity bundle (注册成功后填充, retoken 复用避免风控 abuse)
        "cookies_json": "",
        "fingerprint_json": "",
        "user_agent": "",
        "exit_ip": exit_ip,
        "proxy_port": int(proxy_port) if proxy_port else 0,
        "proxy_formatted": proxy_formatted or "",
    }

    # v8.77 timing instrumentation: 给 register_one 全部 print 加 [T+s] 前缀, 用于精准时间分布分析
    import builtins as _b
    _t_start = time.time()
    _orig_print = _b.print
    def _timed_print(*a, **kw):
        try:
            _orig_print(f"[T+{time.time()-_t_start:6.1f}s]", *a, **kw)
        except Exception:
            _orig_print(*a, **kw)
    _b.print = _timed_print

    p, b = ctrl.launch(headless=headless)
    if not p:
        result["error"] = "浏览器启动失败"
        _b.print = _orig_print
        return result

    # ── 共享浏览器指纹档案（与 Cursor 注册完全一致）──────────────────────────
    # gen_profile 生成与 Cursor 相同的 UA/WebGL/canvas/Audio/machine_id/battery
    # v8.19: 全部 hard-code zh selector 已重构为 zh|en 双语 regex/union
    # (TXT_* / SEL_* / date_option_selector), 现可安全使用 locale="en-US",
    # 时区从 LOCALE_TIMEZONES["en-US"] (US_TIMEZONES) 池中随机, navigator.language
    # 与 Intl.tz 内部一致, 与 Cursor/Replit 完整工作流统一.
    fp = gen_profile(locale="en-US")
    print(f"[register] 指纹: {profile_summary(fp)}", flush=True)

    # 创建 browser context（统一参数：UA / 时区 / 屏幕 / sec-ch-ua headers）
    context = b.new_context(**context_kwargs(fp))
    # 注入完整指纹伪装脚本（canvas / WebGL / Audio / Battery / MachineID / navigator）
    apply_fingerprint_sync(context, fp)
    page = context.new_page()
    t0 = time.time()

    try:
        ok, msg, actual_email = ctrl.outlook_register(page, email, password)
        result["success"]  = ok
        result["error"]    = "" if ok else msg
        result["email"]    = f"{actual_email}@outlook.com"
        result["username"] = actual_email

        if ok:
            # 跳过微软注册后中断页（passkey / 保持登录 / 恢复邮箱等）
            _skip_ms_interrupts(page, label='post-register')
            print("[register] v9.24: wait 15s for MS session propagation (avoid OAuth re-login)...", flush=True)
            time.sleep(15)  # v9.24: post-reg propagation wait
            # ── in-browser OAuth2 authorization_code 授权 ──────────────
            try:
                _tokens = get_oauth_token_in_browser(
                    page, f"{actual_email}@outlook.com",
                    captcha_handler=ctrl.handle_captcha,
                )
                result["access_token"]  = _tokens.get("access_token", "")
                result["refresh_token"] = _tokens.get("refresh_token", "")
            except Exception as _oe:
                print(f"[oauth] 捕获异常: {_oe}", flush=True)
                result["access_token"]  = ""
                result["refresh_token"] = ""
            # ── v8.90 post-reg login verify: 无 token 时确认账号真实存在 ──
            if not result.get("access_token") and not result.get("refresh_token"):
                try:
                    import time as _time
                    # v9.00: MS账号扩散最多需要 30s，最多重试 3次×10s
                    _MAX_PROP = 5
                    for _pa in range(_MAX_PROP):
                        _vpage = context.new_page()
                        _vpage.goto("https://login.live.com", timeout=20000, wait_until="domcontentloaded")
                        _time.sleep(1.5)
                        _vei = _vpage.query_selector("input[type=email],input[name=loginfmt]")
                        _not_found = False
                        if _vei:
                            _vei.fill(f"{actual_email}@outlook.com")
                            _vbtn = _vpage.query_selector("input[type=submit],#idSIButton9,button[type=submit]")
                            if _vbtn:
                                _vbtn.click()
                            _time.sleep(3)
                            _vbody = _vpage.inner_text("body")
                            _not_found = (
                                "We couldn\x27t find a Microsoft account" in _vbody
                                or "couldn\x27t find" in _vbody
                                or "no account found" in _vbody
                                or "找不到此 Microsoft" in _vbody
                            )
                        try:
                            _vpage.close()
                        except Exception:
                            pass
                        if _not_found:
                            if _pa < _MAX_PROP - 1:
                                print(f"[register] ⚠ 账号尚未扩散, 等待15s再次验证 ({_pa+1}/{_MAX_PROP})…", flush=True)
                                _time.sleep(15)
                            else:
                                print(f"[register] ⚠ 登录验证: 账号 {actual_email}@outlook.com 在微软系统中不存在，扩散超时保留成功标记", flush=True)
                                result["propagation_pending"] = True  # v9.23: cookie confirmed
                                # v9.23: do not set error, account was created
                                # v9.23: keep ok=True (success cookie confirmed)
                        else:
                            print(f"[register] ✅ 登录验证通过: 账号存在，等待设备码授权", flush=True)
                            break
                except Exception as _ve:
                    print(f"[register] ⚠ 登录验证异常(忽略): {_ve}", flush=True)
            # ───────────────────────────────────────────────────────────
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
        # v8.20: 注册成功时收集 identity bundle (cookies + fingerprint + UA)
        # 后续 retoken 复用相同 context 参数, 避免 Microsoft 风控判 abuse
        if result.get("success"):
            try:
                _state = context.storage_state()
                # fp 是 BrowserProfile dataclass, 用 asdict 转 dict 后再序列化
                try:
                    import dataclasses as _dc
                    _fp_dict = _dc.asdict(fp) if _dc.is_dataclass(fp) else (dict(fp) if hasattr(fp,"items") else vars(fp))
                except Exception:
                    _fp_dict = vars(fp) if hasattr(fp,"__dict__") else {}
                result["cookies_json"]     = json.dumps(_state, ensure_ascii=False, default=str)
                result["fingerprint_json"] = json.dumps(_fp_dict, ensure_ascii=False, default=str)
                result["user_agent"]       = getattr(fp, "user_agent", "") or _fp_dict.get("user_agent", "")
                print(f"[register] 📦 identity bundle: cookies={len(result['cookies_json'])}B fp={len(result['fingerprint_json'])}B ua={len(result['user_agent'])}B exit_ip={result['exit_ip']} port={result['proxy_port']}", flush=True)
            except Exception as _ce:
                print(f"[register] ⚠ identity bundle 收集失败: {_ce}", flush=True)
        try:
            b.close()
            p.stop()
        except Exception:
            pass
        # v8.77 timing instrumentation: 还原 print
        try:
            _b.print = _orig_print
        except Exception:
            pass

    result["elapsed"] = f"{time.time()-t0:.1f}s"
    return result


def start_cf_pool_refill(reason: str, count: int = 240, target: int = 80, port: int = 443):
    script = Path(__file__).with_name("cf_pool_api.py")
    if not script.exists():
        return False
    try:
        with open(os.devnull, "wb") as devnull:
            subprocess.Popen([
                sys.executable, str(script), "refresh",
                "--count", str(count),
                "--target", str(target),
                "--threads", "12",
                "--port", str(port),
                "--max-latency", "900",
            ], stdout=devnull, stderr=devnull, stdin=devnull, close_fds=True, start_new_session=True,
               env={**os.environ, "PYTHONUNBUFFERED": "1", "CF_POOL_REFILL_REASON": reason})
        return True
    except Exception:
        return False


def make_pool_skip_result(i: int, args, engine_name: str, error: str) -> dict:
    username = args.username.split("@")[0].strip() if i == 0 and args.username else gen_email_username()[0]
    password = args.password.strip() if i == 0 and args.password else gen_password()
    return {
        "email": f"{username}@outlook.com",
        "username": username,
        "password": password,
        "success": False,
        "error": error,
        "elapsed": "0.0s",
        "engine": engine_name,
    }


# ─── 入口 ─────────────────────────────────────────────────────────────────────
def main():
    # —— 磁盘健康校验（exit 2 阻止启动；warn 仅打印不阻塞）——
    try:
        _hc = subprocess.run(
            ["bash", "/root/Toolkit/scripts/disk_health_check.sh"],
            capture_output=True, text=True, timeout=5,
        )
        if _hc.stdout:
            print(_hc.stdout, end="", flush=True)
        if _hc.returncode == 2:
            print("[DISK-CHECK] ❌ 磁盘致命异常，拒绝启动注册任务。请运维介入。", flush=True)
            sys.exit(2)
    except Exception as _e:
        print(f"[DISK-CHECK] ⚠ 健康校验执行失败（{_e!r}），继续启动。", flush=True)
    parser = argparse.ArgumentParser(description="Outlook 批量注册 (参考 outlook-batch-manager)")
    parser.add_argument("--count",           type=int,   default=1,            help="注册数量")
    parser.add_argument("--proxy",           type=str,   default="",           help="代理, 如 socks5://127.0.0.1:1080")
    parser.add_argument("--proxies",         type=str,   default="",           help="多代理轮换（逗号分隔），每次注册轮换一个节点")
    parser.add_argument("--engine",          type=str,   default="patchright", choices=["patchright","playwright","camoufox"])
    parser.add_argument("--headless",        type=str,   default="true",       help="true/false")
    parser.add_argument("--wait",            type=int,   default=BOT_PROTECTION_WAIT, help="bot_protection_wait (秒)")
    parser.add_argument("--retries",         type=int,   default=MAX_CAPTCHA_RETRIES)
    parser.add_argument("--delay",           type=int,   default=2,            help="每次注册间隔秒数 (v8.77: 5→2, CF 池每账号独立 IP 无需冷却)")
    parser.add_argument("--output",          type=str,   default="",           help="输出文件")
    parser.add_argument("--proxy-mode",      type=str,   default="",           help="cf = 从 CF IP 池自动分配代理")
    parser.add_argument("--cf-port",         type=int,   default=443,          help="CF 代理端口（默认443）")
    parser.add_argument("--username",        type=str,   default="",           help="指定首个 Outlook 用户名（可带 @outlook.com）")
    parser.add_argument("--password",        type=str,   default="",           help="指定首个 Outlook 密码")
    args = parser.parse_args()

    headless = args.headless.lower() != "false"
    CtrlCls  = (PatchrightController if args.engine == "patchright" else
             CamoufoxController if args.engine == "camoufox" else
             PlaywrightController)

    solver = None

    # 解析代理列表（--proxies 优先于 --proxy）
    proxy_list = []
    if args.proxies:
        proxy_list = [p.strip() for p in args.proxies.split(",") if p.strip()]
    if not proxy_list and args.proxy:
        proxy_list = [args.proxy.strip()]

    # CF 代理池模式
    use_cf_pool = getattr(args, 'proxy_mode', '') == 'cf'
    _cf_pool = None
    if use_cf_pool:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import cf_ip_pool as _cf_pool
        print(f"   CF代理池模式已启用 (port={args.cf_port})")
        _pool_status = _cf_pool.get_pool_status()
        _need_ips = max(5, min(40, args.count + 5))
        if _pool_status['available'] < _need_ips:
            started = start_cf_pool_refill("outlook_low_watermark", count=240, target=80, port=args.cf_port)
            state = "启动" if started else "尝试启动"
            print(f"   CF池可用 {_pool_status['available']} 个，低于 {_need_ips}，已{state}后台补池；本轮只使用现有已验证节点", flush=True)
        else:
            print(f"   CF池可用 {_pool_status['available']} 个，满足本轮需求", flush=True)

    print(f"\n🚀 Outlook 批量注册  引擎={args.engine}  headless={headless}  count={args.count}")
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
    _used_proxy_indices = set()  # 已使用代理下标，避免重复（严格 1IP1账号）
    for i in range(args.count):
        xray_relay_inst = None
        ip_info = None
        job_id = ""
        if use_cf_pool:
            job_id = f"reg_{i}_{int(time.time())}"
            ip_info = _cf_pool.acquire_ip(job_id, auto_refresh=False,
                                          log_cb=lambda m: print(f"   {m}", flush=True))
            if not ip_info:
                start_cf_pool_refill("outlook_pool_empty", count=240, target=80, port=args.cf_port)
                r = make_pool_skip_result(i, args, args.engine, "CF池无可用预验证IP，已后台补池，请稍后重试")
                results.append(r)
                print(f"\n[{i+1}/{args.count}] 跳过注册… ⚠ CF池无可用预验证IP（已后台补池，禁止无代理裸连）", flush=True)
                continue
            from xray_relay import XrayRelay as _XrayRelay
            xray_relay_inst = _XrayRelay(ip_info['ip'])
            if xray_relay_inst.start(timeout=8.0):
                cur_proxy = xray_relay_inst.socks5_url
                print(f"\n[{i+1}/{args.count}] 开始注册… CF节点: {ip_info['ip']} 延迟{ip_info['latency']}ms → SOCKS5:{xray_relay_inst.socks_port}", flush=True)
            else:
                _cf_pool.ban_ip(ip_info['ip'])
                _cf_pool.release_ip(job_id)
                r = make_pool_skip_result(i, args, args.engine, f"xray中继启动超时，已丢弃CF节点 {ip_info['ip']}")
                results.append(r)
                print(f"\n[{i+1}/{args.count}] 跳过注册… ⚠ xray 启动超时，已丢弃节点 {ip_info['ip']}（禁止无代理裸连）", flush=True)
                continue
        elif proxy_list:
            # 严格 1IP1账号：每个账号独占一个代理节点，不允许复用
            if i >= len(proxy_list):
                print(
                    f"\n⚠ 代理不足：共 {len(proxy_list)} 个代理，但需要注册 {args.count} 个账号。"
                    f"\n  已完成 {i} 个，剩余 {args.count - i} 个因无可用独立IP而跳过。"
                    f"\n  请补充更多代理后重试（规则：1IP ↔ 1账号，禁止复用）。",
                    flush=True,
                )
                break
            cur_proxy = proxy_list[i]
            _used_proxy_indices.add(i)
            if len(proxy_list) > 1:
                print(f"\n[{i+1}/{args.count}] 开始注册… 节点 [{i+1}/{len(proxy_list)}]: {cur_proxy[:40]}...")
            else:
                print(f"\n[{i+1}/{args.count}] 开始注册...")
        else:
            cur_proxy = ""
            print(f"\n[{i+1}/{args.count}] 开始注册...")

        planned_username = args.username if i == 0 else ""
        planned_password = args.password if i == 0 else ""
        MAX_CF_IP_RETRIES = 3
        cf_ip_retry = 0
        r = None
        _last_ip_info = ip_info
        _retry_email = ""    # 记录上次失败时已生成的邮箱（换IP重试时复用，避免重新注册全流程）
        _retry_password = "" # 记录上次失败时已生成的密码
        _ws_proxy_idx = i        # v9.00 webshare限速换代理：当前代理下标
        _ws_proxy_retries = 0    # v9.00 webshare代理切换次数
        MAX_WS_PROXY_RETRIES = 2 # v9.00 最多换2次备用代理

        while True:
            # CF模式换IP重试：CAPTCHA/Timeout失败时 ban 当前IP，重新获取新IP
            # 复用已生成的 email+password 省去表单重填时间
            if use_cf_pool and cf_ip_retry > 0:
                job_id = f"reg_{i}_{int(time.time())}"
                _new_ip = _cf_pool.acquire_ip(job_id, auto_refresh=False,
                                              log_cb=lambda m: print(f"   {m}", flush=True))
                if not _new_ip:
                    start_cf_pool_refill("outlook_pool_empty_retry", count=240, target=80, port=args.cf_port)
                    r = make_pool_skip_result(i, args, args.engine, "CF池无可用IP（CAPTCHA重试时耗尽），已后台补池")
                    break
                from xray_relay import XrayRelay as _XrayRelay
                xray_relay_inst = _XrayRelay(_new_ip["ip"])
                if xray_relay_inst.start(timeout=8.0):
                    cur_proxy = xray_relay_inst.socks5_url
                    ip_info = _new_ip
                    print(f"   ↺ CF换IP重试 ({cf_ip_retry}/{MAX_CF_IP_RETRIES}): {_new_ip['ip']} 延迟{_new_ip['latency']}ms → SOCKS5:{xray_relay_inst.socks_port}", flush=True)
                else:
                    _cf_pool.ban_ip(_new_ip["ip"])
                    _cf_pool.release_ip(job_id)
                    r = make_pool_skip_result(i, args, args.engine, f"xray中继启动超时（CAPTCHA重试），已丢弃CF节点 {_new_ip['ip']}")
                    break

            ctrl = CtrlCls(
                proxy=cur_proxy,
                wait_ms=args.wait,
                max_captcha_retries=args.retries,
            )
            if planned_username:
                print(f"   使用完整工作流预生成账号: {planned_username.split('@')[0]}@outlook.com", flush=True)
            # 若第一次attempt无指定用户名，传入已记录的重试邮箱（换IP时复用）
            _effective_user = planned_username or _retry_email
            _effective_pass = planned_password or _retry_password
            if cf_ip_retry > 0 and _effective_user:
                print(f"   ↳ 复用邮箱 {_effective_user.split('@')[0]}@outlook.com 继续注册（节省表单重填）", flush=True)
            _eip = ""
            _epp = 0
            try:
                if 'ip_info' in dir() and ip_info:
                    _eip = ip_info.get("ip", "")
                if 'xray_relay_inst' in dir() and xray_relay_inst:
                    _epp = int(getattr(xray_relay_inst, "socks_port", 0) or 0)
            except Exception:
                pass
            r = register_one(ctrl, args.engine, headless, _effective_user, _effective_pass, _eip, _epp, proxy_formatted=cur_proxy)

            # 保存本次生成的 email/password 供下次重试复用
            if not _retry_email and r.get("email"):
                _retry_email    = r["email"]
                _retry_password = r.get("password", "")

            # 注册完成后清理 xray 实例
            if xray_relay_inst:
                xray_relay_inst.stop()
                xray_relay_inst = None
            if use_cf_pool and ip_info and _cf_pool:
                _cf_pool.release_ip(job_id)
                if _cf_pool.get_pool_status().get("available", 0) < 25:
                    start_cf_pool_refill("outlook_after_consume", count=240, target=80, port=args.cf_port)

            # CAPTCHA/IP质量不佳/Timeout 失败时 ban 当前 CF IP，换新 IP 重试
            _err_str = r.get("error", "")
            _should_retry = (
                "验证码" in _err_str
                or "CAPTCHA" in _err_str.upper()
                or "IP质量不佳" in _err_str
                or "频率过快" in _err_str
                or "ERR_TUNNEL_CONNECTION_FAILED" in _err_str
                or ("Timeout" in _err_str and "注册界面" in _err_str)
                # v8.77 Bug F: "等待同意按钮超时" 实证 = IP 被风控 (CF 节点 104.24.22.21 案例)
                # 之前未触发 retry 直接 fail, 损失账号. 加入 retry 触发器.
                or "等待同意按钮" in _err_str
                or "Consent" in _err_str
                or "ERR_CONNECTION" in _err_str
                or "net::ERR" in _err_str
            )
            if (not r["success"] and use_cf_pool and ip_info and _cf_pool
                    and _should_retry
                    and cf_ip_retry < MAX_CF_IP_RETRIES):
                cf_ip_retry += 1
                if "验证码" in _err_str or "CAPTCHA" in _err_str.upper():
                    _reason = "CAPTCHA"
                elif "频率过快" in _err_str:
                    _reason = "IP频率限制"
                elif "ERR_TUNNEL_CONNECTION_FAILED" in _err_str:
                    _reason = "代理隧道断开"
                else:
                    _reason = "IP质量/Timeout"
                print(f"  ⚠ {_reason} 失败（IP={ip_info['ip']}），换新CF IP重试 ({cf_ip_retry}/{MAX_CF_IP_RETRIES})…", flush=True)
                _cf_pool.ban_ip(ip_info["ip"])
                continue
            # v9.00 Webshare代理限速 — 换下一个未使用的备用代理重试
            if (not r["success"] and not use_cf_pool and proxy_list
                    and "频率过快" in _err_str
                    and _ws_proxy_retries < MAX_WS_PROXY_RETRIES):
                _next_ws = _ws_proxy_idx + 1
                while _next_ws < len(proxy_list) and _next_ws in _used_proxy_indices:
                    _next_ws += 1
                if _next_ws < len(proxy_list):
                    _bannable = proxy_list[_ws_proxy_idx][:35]
                    print(f"  ⚠ IP频率限制 ({_bannable}…), 换备用代理重试 ({_ws_proxy_retries+1}/{MAX_WS_PROXY_RETRIES})…", flush=True)
                    _used_proxy_indices.add(_next_ws)
                    _ws_proxy_idx = _next_ws
                    cur_proxy = proxy_list[_next_ws]
                    _ws_proxy_retries += 1
                    continue
                else:
                    print(f"  ⚠ IP频率限制, 无备用代理 (已用{len(_used_proxy_indices)}/{len(proxy_list)}), 放弃此账号", flush=True)
            break

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
