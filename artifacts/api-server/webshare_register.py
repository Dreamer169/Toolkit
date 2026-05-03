#!/usr/bin/env python3
"""
Webshare.io 自动注册脚本 v9
关键改进:
  - 默认使用服务器直连 IP（不走 CF 代理）→ Google 音频挑战不会被频率限制
  - Xvfb 自动搜索空闲 display 编号（避免 :99/:100 冲突）
  - 音频下载优先级: route拦截 → Python直连 → Tor SOCKS5 → 浏览器fetch
  - Capsolver 仍作为可选付费加速路径
"""
import argparse, asyncio, json, time, re, os, sys, subprocess, urllib.request, urllib.error

try:
    sys.path.insert(0, "/root/Toolkit/artifacts/api-server")
    from google_proxy_route import attach_google_proxy_routing as _attach_google_proxy_routing
    _HAS_GOOGLE_ROUTE = True
except Exception as _gpr_err:
    _HAS_GOOGLE_ROUTE = False
    _attach_google_proxy_routing = None

def log(msg: str):
    print(msg, flush=True)

WEBSHARE_SITE_KEY     = "6LeHZ6UUAAAAAKat_YS--O2tj_by3gv3r_l03j9d"
WEBSHARE_REGISTER_URL = "https://dashboard.webshare.io/register"
TOR_SOCKS             = "socks5h://127.0.0.1:9050"


# ─────────────────────────── 路径 A: Capsolver ───────────────────────────────

def _capsolver_request(endpoint: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://api.capsolver.com/{endpoint}",
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def solve_recaptcha_capsolver(api_key: str) -> str:
    log("  [Capsolver] 提交 reCAPTCHA v2 invisible 任务...")
    resp = _capsolver_request("createTask", {
        "clientKey": api_key,
        "task": {
            "type": "ReCaptchaV2TaskProxyless",
            "websiteURL": WEBSHARE_REGISTER_URL,
            "websiteKey": WEBSHARE_SITE_KEY,
            "isInvisible": True
        }
    })
    if resp.get("errorId"):
        raise RuntimeError(f"capsolver createTask 错误: {resp.get('errorDescription','?')}")
    task_id = resp.get("taskId")
    log(f"  [Capsolver] taskId={task_id}, 等待结果...")
    for i in range(40):
        time.sleep(3)
        r = _capsolver_request("getTaskResult", {"clientKey": api_key, "taskId": task_id})
        status = r.get("status")
        if status == "ready":
            token = r["solution"]["gRecaptchaResponse"]
            log(f"  [Capsolver] token 成功 ({i*3+3}s): {token[:40]}...")
            return token
        elif status == "failed" or r.get("errorId"):
            raise RuntimeError(f"capsolver 任务失败: {r.get('errorDescription', r)}")
        log(f"  [Capsolver] processing... ({i*3+3}s)")
    raise RuntimeError("capsolver 超时")


def register_via_capsolver(email: str, password: str, capsolver_key: str) -> dict:
    t0 = time.time()
    result = {"success": False, "email": email, "api_key": "", "error": "", "elapsed": ""}
    try:
        token = solve_recaptcha_capsolver(capsolver_key)
        log("  [Webshare API] 发送注册请求...")
        payload = {"email": email, "password": password, "recaptcha": token, "tos_accepted": True}
        req = urllib.request.Request(
            "https://proxy.webshare.io/api/v2/register/",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json", "Accept": "application/json",
                "Origin": "https://dashboard.webshare.io",
                "Referer": "https://dashboard.webshare.io/register",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status_code = r.getcode(); body = json.load(r)
        except urllib.error.HTTPError as e:
            status_code = e.code
            try: body = json.load(e)
            except Exception: body = {"error": e.reason}
        log(f"  [WS_API {status_code}] {json.dumps(body)[:300]}")
        if status_code in (200, 201):
            result["success"] = True
            result["api_key"] = body.get("token", "")
        else:
            parts = []
            for k, v in body.items():
                if isinstance(v, list) and v:
                    msg = v[0].get("message", str(v[0])) if isinstance(v[0], dict) else str(v[0])
                    parts.append(f"{k}: {msg}")
                else:
                    parts.append(f"{k}: {v}")
            result["error"] = "; ".join(parts) if parts else str(body)
    except Exception as e:
        result["error"] = str(e); log(f"  capsolver 路径失败: {e}")
        import traceback; traceback.print_exc()
    result["elapsed"] = f"{time.time()-t0:.1f}s"
    return result


# ─────────────────────── 路径 B: 浏览器自动化 ────────────────────────────────

class CfProxy:
    """可选的 CF IP 代理（默认不使用，直连服务器 IP 更好）"""
    def __init__(self):
        self.relay = None

    def start(self) -> str:
        try:
            sys.path.insert(0, "/root/Toolkit/artifacts/api-server")
            import cf_ip_pool
            from xray_relay import XrayRelay
            import random
            pool = cf_ip_pool.get_pool_status().get("pool", [])
            if not pool:
                return ""
            ip = random.choice(pool[:15])["ip"]
            self.relay = XrayRelay(ip)
            self.relay.start(timeout=10.0)
            url = f"socks5://127.0.0.1:{self.relay.socks_port}"
            log(f"  CF IP: {ip} -> {url}")
            return url
        except Exception as e:
            log(f"  CF 代理失败: {e}"); return ""

    def stop(self):
        if self.relay:
            try: self.relay.stop()
            except Exception: pass


def transcribe_audio(audio_path: str) -> str:
    try:
        r = subprocess.run(
            ["python3", "-c",
             'import sys; sys.path.insert(0,"/root")\n'
             'try:\n'
             '  from faster_whisper import WhisperModel\n'
             f'  model = WhisperModel("base", device="cpu", compute_type="int8")\n'
             f'  segs, _ = model.transcribe("{audio_path}", language="en", beam_size=5)\n'
             '  print(" ".join(s.text for s in segs).strip())\n'
             'except Exception:\n'
             '  import whisper\n'
             '  m = whisper.load_model("base")\n'
             f'  print(m.transcribe("{audio_path}", language="en")["text"].strip())\n'],
            capture_output=True, text=True, timeout=90
        )
        text = r.stdout.strip()
        log(f"  Whisper: {text[:80]}")
        return text
    except Exception as e:
        log(f"  Whisper 失败: {e}"); return ""


def download_audio_direct(audio_url: str) -> bytes | None:
    """服务器直连下载音频（IP 未被 Google 限制，首选方案）"""
    try:
        log(f"  [直连] 下载音频: {audio_url[:70]}...")
        req = urllib.request.Request(
            audio_url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Referer": "https://www.google.com/",
                "Accept": "audio/*,*/*",
            }
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            data = r.read()
        if len(data) > 500:
            log(f"  [直连] 下载成功: {len(data)} bytes")
            return data
        return None
    except Exception as e:
        log(f"  [直连] 下载异常: {e}")
        return None


def download_audio_via_tor(audio_url: str) -> bytes | None:
    """Tor SOCKS5 下载（备用，绕开可能的 IP 封锁）"""
    try:
        import requests
        log(f"  [Tor] 下载音频: {audio_url[:70]}...")
        s = requests.Session()
        s.proxies = {"http": TOR_SOCKS, "https": TOR_SOCKS}
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Referer": "https://www.google.com/",
            "Accept": "audio/*,*/*;q=0.9",
        })
        resp = s.get(audio_url, timeout=30)
        if resp.ok and len(resp.content) > 500:
            log(f"  [Tor] 下载成功: {len(resp.content)} bytes")
            return resp.content
        log(f"  [Tor] HTTP {resp.status_code}")
        return None
    except Exception as e:
        log(f"  [Tor] 下载异常: {e}")
        return None


def download_audio_via_socks5(audio_url: str, proxy_url: str) -> bytes | None:
    """用 Pool B SOCKS5 代理下载音频 (与 reCAPTCHA token mint IP 一致，避免 HTTP 400)"""
    try:
        import requests
        log(f"  [Pool-B] 下载音频 via {proxy_url[:35]}...")
        s = requests.Session()
        s.proxies = {"http": proxy_url, "https": proxy_url}
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Referer": "https://www.google.com/",
            "Accept": "audio/*,*/*;q=0.9",
        })
        resp = s.get(audio_url, timeout=30)
        if resp.ok and len(resp.content) > 500:
            log(f"  [Pool-B] 下载成功: {len(resp.content)} bytes")
            return resp.content
        log(f"  [Pool-B] HTTP {resp.status_code}")
        return None
    except Exception as e:
        log(f"  [Pool-B] 下载异常: {e}")
        return None


class XvfbDisplay:
    """启动 Xvfb 虚拟显示器，自动搜索空闲 display 编号"""
    def __init__(self, width: int = 1280, height: int = 800):
        self.proc = None
        self.env_display = ""
        self.width = width
        self.height = height

    def _find_free_display(self) -> int:
        """找到没有 /tmp/.X{N}-lock 的编号"""
        for n in range(101, 130):
            if not os.path.exists(f"/tmp/.X{n}-lock"):
                return n
        return 130

    def start(self) -> bool:
        display_num = self._find_free_display()
        self.env_display = f":{display_num}"
        try:
            self.proc = subprocess.Popen(
                ["Xvfb", self.env_display, "-screen", "0",
                 f"{self.width}x{self.height}x24",
                 "-ac", "+extension", "GLX", "+render", "-noreset"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(2)
            if self.proc.poll() is None:
                log(f"  Xvfb 已启动 (DISPLAY={self.env_display})")
                return True
            log(f"  Xvfb 启动失败 (display={self.env_display})")
            return False
        except Exception as e:
            log(f"  Xvfb 异常: {e}"); return False

    def stop(self):
        if self.proc:
            try: self.proc.terminate(); self.proc.wait(timeout=3)
            except Exception: pass
        # 清理 lock 文件
        if self.env_display:
            n = self.env_display.lstrip(":")
            try: os.unlink(f"/tmp/.X{n}-lock")
            except Exception: pass


async def register_via_browser(email: str, password: str, proxy: str = "",
                                headless: bool = True, use_cf: bool = False,
                                use_xvfb: bool = True) -> dict:
    t0 = time.time()
    result = {"success": False, "email": email, "api_key": "", "error": "", "elapsed": ""}

    xvfb = None
    cf_proxy = None
    try:
        # Xvfb 模式：虚拟显示 → non-headless → 更好 reCAPTCHA 分数
        if use_xvfb:
            xvfb = XvfbDisplay()
            if xvfb.start():
                os.environ["DISPLAY"] = xvfb.env_display
                headless = False
                log("  Xvfb non-headless 模式已激活")
            else:
                xvfb = None
                log("  Xvfb 失败，回退 headless")

        # 代理：默认直连（服务器 IP），可选 CF IP
        proxy_url = proxy
        if not proxy_url and use_cf:
            cf_proxy = CfProxy()
            proxy_url = cf_proxy.start()

        if proxy_url and 'socks5' in proxy_url and '9050' in proxy_url:
            log(f"  Tor 代理模式: socks5://127.0.0.1:9050 (新鲜出口 IP，绕过 Google 音频限速)")
        elif use_cf and proxy_url:
            log(f"  代理模式: CF IP ({proxy_url[:30]}...)")
        else:
            log("  直连模式: 服务器 IP（不受 Google 音频频率限制）")

        from patchright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu",
                      "--window-size=1280,800",
                      "--disable-blink-features=AutomationControlled"],
                executable_path="/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome"
            )
            ctx_kw = dict(
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            if proxy_url:
                ctx_kw["proxy"] = {"server": proxy_url}
            ctx = await browser.new_context(**ctx_kw)
            await ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            # 关键: 把 *.google.com/gstatic.com/recaptcha.net 路由到 Pool B 住宅 IP
            # 这样 reCAPTCHA 看到的是住宅 IP → 评分高 → 音频挑战正常加载
            _pinned_poolb_proxy = ""
            if _HAS_GOOGLE_ROUTE and _attach_google_proxy_routing:
                try:
                    await _attach_google_proxy_routing(ctx, log=log)
                    # 获取固定的 Pool B 代理 URL (音频下载需用同一 IP)
                    try:
                        from google_proxy_route import get_pinned_proxy
                        _pinned_poolb_proxy = get_pinned_proxy(ctx) or ""
                        if _pinned_poolb_proxy:
                            log(f"  [google-route] 音频下载将用固定 Pool B: {_pinned_poolb_proxy[:40]}")
                    except Exception:
                        pass
                    log("  [google-route] Pool B 住宅 IP 路由已附加 reCAPTCHA 走住宅 IP")
                except Exception as gpr_e:
                    log(f"  [google-route] 附加失败 继续: {str(gpr_e)[:60]}")
            else:
                log("  [google-route] 模块不可用 httpx_socks 未安装")
            page = await ctx.new_page()

            ws_done = asyncio.Event()
            audio_bytes: list[bytes] = []
            audio_urls_seen: list[str] = []

            async def on_resp(resp):
                if "proxy.webshare.io" in resp.url and "register" in resp.url:
                    try:
                        b = await resp.text()
                        log(f"  [WS_API {resp.status}] {b[:300]}")
                        result["_ws_status"] = resp.status
                        result["_ws_body"] = b
                        ws_done.set()
                    except Exception: pass
                if ".mp3" in resp.url and ("google" in resp.url or "gstatic" in resp.url):
                    try:
                        b = await resp.body()
                        if len(b) > 500:
                            audio_bytes.append(b)
                            if resp.url not in audio_urls_seen:
                                audio_urls_seen.append(resp.url)
                            log(f"  [AUDIO_ROUTE] {len(b)} bytes")
                    except Exception: pass

            async def on_req(req):
                if ".mp3" in req.url and ("google" in req.url or "gstatic" in req.url):
                    if req.url not in audio_urls_seen:
                        audio_urls_seen.append(req.url)

            # ctx.on captures bframe audio (page.on misses iframes)
            ctx.on("response", on_resp)
            ctx.on("request", on_req)

            log("  -> 打开注册页面...")
            await page.goto("https://dashboard.webshare.io/register",
                            wait_until="load", timeout=90000)
            await asyncio.sleep(2)
            log(f"  页面加载完成 ({time.time()-t0:.1f}s)")

            email_el = (await page.query_selector("#email-input") or
                        await page.query_selector("input[type='email']"))
            if not email_el:
                raise Exception("找不到邮箱输入框")
            await email_el.click(); await email_el.fill("")
            await email_el.type(email, delay=60)
            log("  Email 已填写")
            await asyncio.sleep(0.4)

            for pw in await page.query_selector_all("input[type='password']"):
                if await pw.is_visible():
                    await pw.click(); await pw.fill("")
                    await pw.type(password, delay=55)
            log("  密码已填写")
            await asyncio.sleep(0.5)

            try:
                await page.locator(".MuiCheckbox-root").click(timeout=5000)
                cb = await page.evaluate(
                    "() => document.querySelector('input[type=checkbox]')?.checked"
                )
                log(f"  Terms 勾选: {cb}")
                if not cb:
                    await page.locator("input[type='checkbox']").first.check(
                        force=True, timeout=3000)
            except Exception as e:
                log(f"  checkbox 警告: {e}")
                try:
                    await page.locator("input[type='checkbox']").first.check(
                        force=True, timeout=3000)
                except Exception: pass

            await asyncio.sleep(0.5)
            await page.locator("button[type='submit']").click()
            log(f"  已提交 ({time.time()-t0:.1f}s)")

            audio_switch  = False
            doscaptcha    = False
            audio_solved  = False
            audio_retries = 0
            deadline      = time.time() + 160

            while time.time() < deadline:
                await asyncio.sleep(2)
                if ws_done.is_set() or "/register" not in page.url:
                    break

                bframe_html = ""
                bframe = None
                for f in page.frames:
                    if "bframe" in f.url and "recaptcha" in f.url:
                        bframe = f
                        try: bframe_html = await f.content()
                        except Exception: pass
                        break

                has_img   = "rc-imageselect"    in bframe_html
                has_audio = "rc-audiochallenge" in bframe_html
                has_dos   = ("doscaptcha" in bframe_html.lower() or
                             "try again later" in bframe_html.lower())

                if has_dos:
                    doscaptcha = True
                    log(f"  doscaptcha (t={time.time()-t0:.0f}s) — 换 IP 刷新")
                    # 如果在直连模式遇到 doscaptcha，尝试切回 CF 并重载
                    if bframe:
                        try:
                            rb = await bframe.query_selector("#recaptcha-reload-button")
                            if rb and not await rb.get_attribute("disabled"):
                                await rb.click(); await asyncio.sleep(5); continue
                        except Exception: pass
                    await asyncio.sleep(8); continue

                if has_img and not audio_switch:
                    audio_switch = True
                    log(f"  图片验证码 -> 切换音频 (t={time.time()-t0:.0f}s)")
                    if bframe:
                        try:
                            ab = await bframe.query_selector("#recaptcha-audio-button")
                            if ab: await ab.click(); await asyncio.sleep(4)
                        except Exception as e: log(f"  audio btn: {e}")
                    continue

                if has_audio and not audio_solved:
                    log(f"  音频验证码 (t={time.time()-t0:.0f}s)")
                    await asyncio.sleep(1)

                    audio_url = None
                    if bframe:
                        try:
                            html = await bframe.content()
                            for pat in [
                                r'href=["\']([^"\']*\.mp3[^"\']*)["\']',
                                r'"(https://[^"]*\.mp3[^"]*?)"',
                                r"(https://[^\s'\"]+\.mp3[^\s'\"]*)",
                            ]:
                                m = re.search(pat, html)
                                if m: audio_url = m.group(1); break
                        except Exception: pass

                    if not audio_url and audio_urls_seen:
                        audio_url = next(
                            (u for u in reversed(audio_urls_seen) if ".mp3" in u), None
                        )

                    audio_data = None
                    audio_path = None

                    # ① route 拦截字节（最优先，直接从 Playwright 路由中获取）
                    if audio_bytes:
                        audio_data = audio_bytes[-1]
                        audio_path = "/tmp/ws_route_audio.mp3"
                        with open(audio_path, "wb") as af: af.write(audio_data)
                        log(f"  ① route 拦截: {len(audio_data)} bytes")

                    # ② Pool B SOCKS5 下载（与 reCAPTCHA token mint IP 一致，避免 HTTP 400）
                    elif audio_url and _pinned_poolb_proxy:
                        audio_data = download_audio_via_socks5(audio_url, _pinned_poolb_proxy)
                        if audio_data:
                            audio_path = "/tmp/ws_poolb_audio.mp3"
                            with open(audio_path, "wb") as af: af.write(audio_data)

                    # ③ 服务器直连下载（备用）
                    elif audio_url:
                        audio_data = download_audio_direct(audio_url)
                        if audio_data:
                            audio_path = "/tmp/ws_direct_audio.mp3"
                            with open(audio_path, "wb") as af: af.write(audio_data)

                    # ④ Tor 下载（备用，换出口 IP）
                    if not audio_path and audio_url:
                        audio_data = download_audio_via_tor(audio_url)
                        if audio_data:
                            audio_path = "/tmp/ws_tor_audio.mp3"
                            with open(audio_path, "wb") as af: af.write(audio_data)

                    # ④ 浏览器 fetch（最后备用）
                    if not audio_path and audio_url:
                        log(f"  ④ 浏览器 fetch (备用): {audio_url[:60]}...")
                        try:
                            fr = await page.evaluate(f"""
                                async () => {{
                                    try {{
                                        const r = await fetch({json.dumps(audio_url)}, {{
                                            headers: {{"Accept": "audio/*,*/*",
                                                       "Referer": "https://www.google.com/"}}
                                        }});
                                        if (!r.ok) return {{ok: false, status: r.status}};
                                        const buf = await r.arrayBuffer();
                                        const arr = Array.from(new Uint8Array(buf));
                                        return {{ok: true, size: arr.length, data: arr}};
                                    }} catch(e) {{
                                        return {{ok: false, error: String(e)}};
                                    }}
                                }}
                            """)
                            if fr.get("ok") and fr.get("size", 0) > 500:
                                audio_path = "/tmp/ws_browser_audio.mp3"
                                with open(audio_path, "wb") as af:
                                    af.write(bytes(fr["data"]))
                                log(f"  ④ 浏览器 fetch 成功: {len(fr['data'])} bytes")
                            else:
                                log(f"  ④ 浏览器 fetch 失败: {fr}")
                        except Exception as e: log(f"  ④ 浏览器 fetch 异常: {e}")

                    if audio_path and os.path.exists(audio_path):
                        text = transcribe_audio(audio_path)
                        if text:
                            cleaned = re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()
                            log(f"  Whisper 清理: '{cleaned[:60]}'")
                            if bframe:
                                try:
                                    inp = await bframe.query_selector(
                                        "#audio-response, .rc-audiochallenge-response-field"
                                    )
                                    if inp:
                                        await inp.click()
                                        await inp.fill("")
                                        await inp.type(cleaned, delay=60)
                                        await asyncio.sleep(0.5)
                                        vb = await bframe.query_selector(
                                            "#recaptcha-verify-button"
                                        )
                                        if vb:
                                            await vb.click()
                                            log("  已提交音频答案")
                                            audio_solved = True
                                            audio_switch = False
                                            audio_bytes.clear()
                                            audio_urls_seen.clear()
                                            await asyncio.sleep(5)
                                except Exception as e: log(f"  提交音频: {e}")
                        else:
                            audio_retries += 1
                            log(f"  Whisper 无输出，刷新 (retry {audio_retries})")
                            audio_switch = False
                            if bframe:
                                try:
                                    rb = await bframe.query_selector("#recaptcha-reload-button")
                                    if rb: await rb.click(); await asyncio.sleep(4)
                                except Exception: pass
                    else:
                        log("  无音频文件，等待重试...")
                        audio_switch = False

            if not ws_done.is_set():
                try: await asyncio.wait_for(ws_done.wait(), timeout=15)
                except asyncio.TimeoutError: pass

            await page.screenshot(path="/tmp/ws_v9_final.png")

            ws_body   = result.get("_ws_body", "")
            ws_status = result.get("_ws_status", 0)

            if ws_status in (200, 201):
                result["success"] = True
                try:
                    data = json.loads(ws_body)
                    result["api_key"] = (data.get("token") or
                                         data.get("api_key") or
                                         data.get("key", ""))
                except Exception: pass
                log(f"  注册成功! API Key: {result['api_key'][:16] if result['api_key'] else '未获取'}...")
            elif ws_status >= 400:
                try:
                    data = json.loads(ws_body)
                    parts = []
                    for k, v in data.items():
                        if isinstance(v, list) and v:
                            msg = (v[0].get("message", str(v[0]))
                                   if isinstance(v[0], dict) else str(v[0]))
                            parts.append(f"{k}: {msg}")
                        else:
                            parts.append(f"{k}: {v}")
                    result["error"] = "; ".join(parts)
                except Exception:
                    result["error"] = ws_body[:200]
            else:
                cur_url = page.url
                if "/register" not in cur_url:
                    result["success"] = True
                    log(f"  URL 已跳转: {cur_url}")
                else:
                    result["error"] = (
                        f"超时/失败 (doscaptcha={doscaptcha}, "
                        f"audio_retries={audio_retries})"
                    )

            if result["success"] and not result["api_key"]:
                await asyncio.sleep(3)
                try:
                    tok = await page.evaluate(
                        "() => localStorage.getItem('token') || "
                        "localStorage.getItem('api_key') || ''"
                    )
                    if tok: result["api_key"] = tok
                except Exception: pass
                if not result["api_key"]:
                    try:
                        rd = await page.evaluate("""
                            async () => {
                                try {
                                    const r = await fetch(
                                        "https://proxy.webshare.io/api/v2/profile/"
                                    );
                                    return await r.json();
                                } catch(e) { return {}; }
                            }
                        """)
                        if rd.get("token"): result["api_key"] = rd["token"]
                    except Exception: pass

            await browser.close()

    except Exception as e:
        result["error"] = str(e)
        log(f"  浏览器路径异常: {e}")
        import traceback; traceback.print_exc()
    finally:
        if cf_proxy: cf_proxy.stop()
        if xvfb:     xvfb.stop()

    result.pop("_ws_status", None)
    result.pop("_ws_body", None)
    result["elapsed"] = f"{time.time()-t0:.1f}s"
    return result


# ─────────────────────────────── 入口 ────────────────────────────────────────

async def register_webshare(email: str, password: str, proxy: str = "",
                             headless: bool = True, use_cf: bool = False,
                             capsolver_key: str = "",
                             use_xvfb: bool = True,
                             use_tor_browser: bool = False) -> dict:
    if capsolver_key:
        log("  [模式] Capsolver 直接 API")
        return register_via_capsolver(email, password, capsolver_key)
    else:
        mode = "Xvfb+" if use_xvfb else ""
        if use_tor_browser:
            ip_mode = "Tor 浏览器（新鲜出口 IP）"
            proxy = proxy or "socks5://127.0.0.1:9050"
        elif use_cf:
            ip_mode = "CF IP"
        else:
            ip_mode = "服务器直连 IP"
        google_mode = "+ Pool B 住宅 IP 劫持 Google 请求" if _HAS_GOOGLE_ROUTE else "(google-route 不可用)"
        log(f"  [模式] {mode}浏览器 + {ip_mode} {google_mode} + 音频 CAPTCHA (免费)")
        return await register_via_browser(
            email, password, proxy, headless, use_cf, use_xvfb
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--email",         required=True)
    ap.add_argument("--password",      required=True)
    ap.add_argument("--proxy",         default="")
    ap.add_argument("--headless",      default="true")
    ap.add_argument("--use-cf",        action="store_true",
                    help="使用 CF IP 代理（默认关闭，直连 IP 对音频挑战更友好）")
    ap.add_argument("--no-tor",         action="store_true",
                    help="禁用 Tor 浏览器代理（默认启用，绕开 IP 频率限制）")
    ap.add_argument("--no-xvfb",       action="store_true",
                    help="禁用 Xvfb 虚拟显示（默认启用以获更好 reCAPTCHA 分数）")
    ap.add_argument("--capsolver-key", default=os.environ.get("CAPSOLVER_KEY", ""))
    args = ap.parse_args()

    headless = args.headless.lower() not in ("false", "0", "no")
    use_cf   = args.use_cf
    use_xvfb = not args.no_xvfb
    use_tor_browser = not args.no_tor

    r = asyncio.run(register_webshare(
        args.email, args.password, args.proxy,
        headless, use_cf, args.capsolver_key, use_xvfb, use_tor_browser
    ))
    print("\n-- 结果 --")
    print(json.dumps(r, ensure_ascii=False, indent=2))
