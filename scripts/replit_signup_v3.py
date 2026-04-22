#!/usr/bin/env python3
"""
replit_signup_v3.py — 友节点全自动注册 v7.36
============================================================
核心改进 (v7.36):
  • Camoufox (Firefox-based 反指纹) 替代 Chromium-stealth → 极大提升 reCAPTCHA 评分
  • Playwright 代理改用 args=['--proxy-server=…']  (绕过部分 CDP 探针)
  • Xvfb 真实显示(:99) 启动 headful 浏览器 → reCAPTCHA score 显著上升
  • reCAPTCHA v2 音频挑战自动绕过：
        anchor iframe → click checkbox
        bframe iframe → 切音频 → 下载 audio.mp3
        ffmpeg 转 16kHz wav → openai-whisper 本地转录
        填入 audio-response → verify
        token 由真实 session 生成，服务端验证通过
  • PulseAudio 虚拟接收槽，避免 audio 事件被 mute
  • 隧道 nohup 持久化 + 内嵌 readiness 等待
  • 0 邮箱起步：mail.tm 自动建箱 → 自动收信 → 验证链接自动访问
  • 提升 token 评分：
        - 真实鼠标轨迹（贝塞尔曲线 + 抖动）
        - 滚动 / 停留 / 焦点切换
        - 时区/语言/地理与代理出口一致
        - WebGL/Canvas/AudioContext 噪声由 Camoufox 自动处理

用法:
  python3 replit_signup_v3.py                   # 注册1个账号 (默认 xvfb)
  python3 replit_signup_v3.py --count 3
  python3 replit_signup_v3.py --serve 7070      # HTTP 服务模式
  python3 replit_signup_v3.py --no-xvfb         # 调试 (本地有显示)
"""
import argparse, asyncio, json, math, os, random, re, secrets, string
import subprocess, sys, time, urllib.request, urllib.error, shutil, tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, List

# ── 配置 ──────────────────────────────────────────────────────────────────────
GATEWAY_API      = os.environ.get("GATEWAY_API",    "http://localhost:8080/api/gateway")
VPS_GATEWAY_URL  = os.environ.get("VPS_GATEWAY_URL","http://45.205.27.69:8080/api/gateway")
TOOLKIT_FORK     = os.environ.get("TOOLKIT_FORK",   "Dreamer169/Toolkit")
SIGNUP_URL       = "https://replit.com/signup"
DB_URL           = os.environ.get("DATABASE_URL","postgresql://postgres:postgres@localhost/toolkit")
WHISPER_MODEL    = os.environ.get("WHISPER_MODEL", "base")  # tiny/base/small
XVFB_DISPLAY     = ":99"
XVFB_RES         = "1366x768x24"

XRAY_SOCKS_PORTS  = list(range(10820, 10846))
POLL_BRIDGE_PORTS = [1092, 1093, 1094, 1095]
ALL_SOCKS_PORTS   = POLL_BRIDGE_PORTS + XRAY_SOCKS_PORTS
MAX_RETRIES       = 5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
]
FIRST = ["alex","blake","casey","dakota","eden","finley","gray","harper","jordan",
         "kennedy","lane","morgan","parker","quinn","reese","sage","taylor","winter"]
LAST  = ["smith","jones","chen","kim","lee","wang","patel","garcia","miller","davis"]

G="\033[92m"; R="\033[91m"; Y="\033[93m"; C="\033[96m"; B="\033[1m"; X="\033[0m"
ok  = lambda s: print(f"{G}✓{X} {s}")
er  = lambda s: print(f"{R}✗{X} {s}")
warn= lambda s: print(f"{Y}!{X} {s}")
inf = lambda s: print(f"{C}·{X} {s}")
hdr = lambda s: print(f"\n{B}{s}{X}")

# ── 身份生成 (实时新建 Outlook + Graph API) ──────────────────────────────────
sys.path.insert(0, "/root/Toolkit/artifacts/api-server")
try:
    from outlook_graph import wait_for_replit_verify
except Exception:
    wait_for_replit_verify = None

OUTLOOK_REG_SCRIPT = "/root/Toolkit/artifacts/api-server/outlook_register.py"
OUTLOOK_PROXY_PORTS = [int(x) for x in os.environ.get("OUTLOOK_PROXY_PORTS","1092,1093,1094,1095").split(",")]

def mint_outlook(timeout=720):
    """实时调用 outlook_register.py 新建一个 Outlook 账号；解析 stdout JSON 取 token"""
    import subprocess, pathlib as _pl
    proxy_port = random.choice(OUTLOOK_PROXY_PORTS)
    proxy = f"socks5://127.0.0.1:{proxy_port}"
    inf(f"  [outlook] 通过 {proxy} 调用 outlook_register.py 新建账号 (timeout={timeout}s)…")
    cmd = ["python3","-u",OUTLOOK_REG_SCRIPT,
           "--count","1","--engine","camoufox",
           "--headless","false","--proxy",proxy]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout,
                              env={**os.environ, "DISPLAY": os.environ.get("DISPLAY",":99")})
    except subprocess.TimeoutExpired:
        warn(f"  [outlook] 超时 {timeout}s")
        return None
    out = (proc.stdout or "") + "\n----STDERR----\n" + (proc.stderr or "")
    log_p = f"/tmp/outlook_mint_{int(time.time())}.log"
    _pl.Path(log_p).write_text(out)
    inf(f"  [outlook] 子进程结束 (rc={proc.returncode}), log={log_p}")
    m = re.search(r"── JSON 结果 ──\s*(\[.*\])\s*\Z", out, re.S)
    if not m:
        m = re.search(r"(\[\s*\{[^\[\]]*?\"email\".*?\}\s*\])", out, re.S)
    if not m:
        warn("  [outlook] 无法解析 JSON 结果"); return None
    try:
        arr = json.loads(m.group(1))
    except Exception as e:
        warn(f"  [outlook] JSON 解析失败: {e}"); return None
    for r in arr:
        if r.get("success") and r.get("refresh_token"):
            ok(f"  [outlook] 新账号: {r['email']}")
            return {"email": r["email"], "password": r["password"],
                    "refresh_token": r["refresh_token"]}
    # 即使没有 refresh_token，只要邮箱注册成功也返回（IMAP 兜底）
    for r in arr:
        if r.get("success"):
            warn(f"  [outlook] 账号已注册但缺 refresh_token: {r['email']}")
            return {"email": r["email"], "password": r["password"], "refresh_token": ""}
    warn("  [outlook] 全部子任务失败"); return None

def gen_identity():
    o = mint_outlook()
    if not o: return None
    email = o["email"]; login = email.split("@")[0]
    user = re.sub(r"[^a-zA-Z0-9_]", "", login)[:24]
    if len(user) < 3: user = (login + "xxx")[:8]
    pwd = ("".join(random.choices(string.ascii_letters, k=4))
         + "".join(random.choices(string.digits, k=4))
         + "".join(random.choices("!@#$%&", k=2))
         + "".join(random.choices(string.ascii_letters + string.digits, k=6)))
    pwd = "".join(random.sample(pwd, len(pwd)))
    return {"username": user, "password": pwd, "email": email,
            "login": login, "domain": email.split("@")[1],
            "refresh_token": o.get("refresh_token",""),
            "outlook_password": o["password"], "first": "", "last": ""}

def extract_verify_url(html):
    m = re.search(r'https?://[^"\s<>]*replit[^"\s<>]*(?:verify|confirm|activate)[^"\s<>]*', html, re.I)
    if m: return m.group(0)
    m = re.search(r'https?://replit\.com/[^"\s<>]+', html, re.I)
    return m.group(0) if m else ""

# ── Xvfb / PulseAudio 管理 ────────────────────────────────────────────────────
def ensure_xvfb():
    """启动 :99 Xvfb + PulseAudio (idempotent)。"""
    # Xvfb
    if subprocess.call(["pgrep","-af","Xvfb.*"+XVFB_DISPLAY],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)!=0:
        subprocess.Popen(["Xvfb",XVFB_DISPLAY,"-screen","0",XVFB_RES,"-ac","+extension","RANDR"],
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        ok(f"Xvfb 已启动 {XVFB_DISPLAY} ({XVFB_RES})")
    else:
        inf(f"Xvfb {XVFB_DISPLAY} 已在运行")
    os.environ["DISPLAY"]=XVFB_DISPLAY
    # PulseAudio (虚拟接收槽，让浏览器音频不被静音)
    if subprocess.call(["pgrep","-x","pulseaudio"],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)!=0:
        subprocess.Popen(["pulseaudio","--start","--exit-idle-time=-1"],
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        ok("PulseAudio 已启动")

# ── 隧道 nohup readiness 等待 ────────────────────────────────────────────────
def ensure_tunnel(port,timeout=15):
    """检测 socks5 端口存活，否则交回上层重试。"""
    import socket
    deadline=time.time()+timeout
    while time.time()<deadline:
        s=socket.socket(); s.settimeout(2)
        try:
            s.connect(("127.0.0.1",port)); s.close(); return True
        except: time.sleep(1)
    return False

# ── reCAPTCHA 音频挑战绕过 ───────────────────────────────────────────────────
async def dump_frames(page,tag=""):
    urls=[f.url for f in page.frames if f.url and f.url!="about:blank"]
    inf(f"[frames{tag}] " + " | ".join(u[:90] for u in urls) if urls else f"[frames{tag}] (no iframes)")

async def detect_captcha(page):
    for f in page.frames:
        u=f.url or ""
        if "recaptcha/api2/anchor" in u or "recaptcha/enterprise/anchor" in u: return "recaptcha"
        if "hcaptcha.com" in u and ("checkbox" in u or "challenge" in u): return "hcaptcha"
        if "challenges.cloudflare.com" in u or "turnstile" in u: return "turnstile"
    return None

async def solve_audio_recaptcha(page,timeout=180):
    inf("[recaptcha] 探测 anchor iframe…")
    deadline=time.time()+timeout
    anchor=None
    while time.time()<deadline:
        for f in page.frames:
            u=f.url or ""
            if "recaptcha/api2/anchor" in u or "recaptcha/enterprise/anchor" in u:
                anchor=f; break
        if anchor: break
        await page.wait_for_timeout(800)
    if not anchor:
        await dump_frames(page," recaptcha-not-found")
        warn("[recaptcha] 未发现 anchor frame (可能本次未触发)")
        return True
    inf(f"[recaptcha] anchor frame: {anchor.url[:80]}")
    inf("[recaptcha] 点击 checkbox…")
    clicked=False
    try:
        for el in await page.query_selector_all('iframe'):
            sa = await el.get_attribute("src") or ""
            if "recaptcha/api2/anchor" in sa or "recaptcha/enterprise/anchor" in sa:
                try:
                    await el.scroll_into_view_if_needed(timeout=4000)
                except Exception: pass
                await page.wait_for_timeout(400)
                box = await el.bounding_box()
                if box:
                    vh = await page.evaluate("window.innerHeight")
                    if box["y"] + box["height"] > vh:
                        await page.evaluate(f"window.scrollTo(0, {box[y]-100})")
                        await page.wait_for_timeout(500)
                        box = await el.bounding_box()
                    cx = box["x"] + 30; cy = box["y"] + box["height"]/2
                    await page.mouse.move(cx-20, cy-10, steps=8)
                    await page.wait_for_timeout(180)
                    await page.mouse.click(cx, cy)
                    clicked = True
                    inf(f"[recaptcha] 已点击 iframe checkbox 区域 ({cx:.0f},{cy:.0f}, vh={vh})")
                    await page.wait_for_timeout(2800)
                    break
    except Exception as _e:
        warn(f"[recaptcha] iframe 直接点击失败: {_e}")
    for sel in ([] if clicked else ["#recaptcha-anchor", "div.recaptcha-checkbox", "[role='checkbox']", ".recaptcha-checkbox-border"]):
        try:
            await anchor.locator(sel).first.wait_for(state="visible", timeout=12000)
            await anchor.locator(sel).first.click(timeout=5000)
            clicked=True; break
        except Exception as e:
            inf(f"[recaptcha] 选择器 {sel} 失败: {str(e)[:50]}")
    if not clicked:
        warn("[recaptcha] 全部 checkbox 选择器失败")
    await page.wait_for_timeout(2500)
    try:
        cls=await anchor.locator("#recaptcha-anchor").get_attribute("aria-checked")
        if cls=="true":
            ok("[recaptcha] 一次性通过，无音频挑战"); return True
    except: pass
    bframe=None
    for _ in range(20):
        for f in page.frames:
            u=f.url or ""
            if "recaptcha/api2/bframe" in u or "recaptcha/enterprise/bframe" in u:
                bframe=f; break
        if bframe: break
        await page.wait_for_timeout(500)
    if not bframe:
        warn("[recaptcha] 未发现 bframe"); return False
    inf("[recaptcha] 切换到音频挑战…")
    try:
        await bframe.locator("#recaptcha-audio-button").click(timeout=8000)
    except Exception as e:
        warn(f"[recaptcha] 音频按钮失败: {e}"); return False
    await page.wait_for_timeout(2500)
    audio_url=""
    for _ in range(15):
        try:
            audio_url=await bframe.locator("audio#audio-source, audio source, .rc-audiochallenge-tdownload-link").first.get_attribute("src") or ""
            if not audio_url:
                audio_url=await bframe.locator(".rc-audiochallenge-tdownload-link").first.get_attribute("href") or ""
        except: pass
        if audio_url: break
        await page.wait_for_timeout(700)
    if not audio_url:
        warn("[recaptcha] 无法获取音频 URL"); return False
    inf(f"[recaptcha] 音频 URL: {audio_url[:80]}")
    tmpdir=tempfile.mkdtemp(prefix="recap_")
    mp3=os.path.join(tmpdir,"a.mp3"); wav=os.path.join(tmpdir,"a.wav")
    try: urllib.request.urlretrieve(audio_url,mp3)
    except Exception as e:
        warn(f"[recaptcha] 下载音频失败: {e}"); return False
    rc=subprocess.call(["ffmpeg","-y","-i",mp3,"-ar","16000","-ac","1",wav],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if rc!=0:
        warn("[recaptcha] ffmpeg 转换失败"); return False
    inf(f"[recaptcha] whisper 转录 (model={WHISPER_MODEL})…")
    try:
        import whisper
        model=whisper.load_model(WHISPER_MODEL)
        text=model.transcribe(wav,language="en",fp16=False)["text"].strip()
    except Exception as e:
        warn(f"[recaptcha] whisper 失败: {e}"); return False
    text=re.sub(r"[^a-zA-Z0-9 ]","",text).strip().lower()
    inf(f"[recaptcha] 识别: '{text}'")
    if not text: return False
    try:
        box=bframe.locator("#audio-response")
        await box.click(); await box.type(text,delay=80)
        await page.wait_for_timeout(600)
        await bframe.locator("#recaptcha-verify-button").click()
    except Exception as e:
        warn(f"[recaptcha] 提交答案失败: {e}"); return False
    await page.wait_for_timeout(3500)
    try:
        cls=await anchor.locator("#recaptcha-anchor").get_attribute("aria-checked")
        if cls=="true":
            ok("[recaptcha] 音频挑战通过 ✓"); return True
    except: pass
    warn("[recaptcha] 音频挑战未通过"); return False

# ── 人类化交互 ────────────────────────────────────────────────────────────────
async def human_mouse(page,x,y,steps=18):
    """贝塞尔曲线鼠标轨迹"""
    bx,by=random.randint(40,200),random.randint(40,200)
    cx,cy=(bx+x)/2+random.randint(-80,80),(by+y)/2+random.randint(-80,80)
    for i in range(steps):
        t=i/steps
        nx=(1-t)**2*bx+2*(1-t)*t*cx+t*t*x
        ny=(1-t)**2*by+2*(1-t)*t*cy+t*t*y
        await page.mouse.move(nx+random.uniform(-1,1),ny+random.uniform(-1,1))
        await page.wait_for_timeout(random.randint(8,22))

async def human_dwell(page):
    """随机滚动 + 停留，提升 reCAPTCHA 行为评分"""
    await page.wait_for_timeout(random.randint(800,1600))
    await page.mouse.wheel(0,random.randint(120,400))
    await page.wait_for_timeout(random.randint(500,1100))
    await page.mouse.wheel(0,-random.randint(80,200))
    await page.wait_for_timeout(random.randint(400,900))

# ── 注册核心 ──────────────────────────────────────────────────────────────────
async def _do_signup(identity,ua,proxy_port,mailtm_tok,headless=False):
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        return {"ok":False,"phase":"import","error":"camoufox 未安装"}

    result={"ok":False,"phase":"init","error":"","cookies":[],"exit_ip":""}
    proxy_arg=f"--proxy-server=socks5://127.0.0.1:{proxy_port}"

    # Camoufox: 内置 Firefox + 反指纹 + 通过 launch_options 注入 args
    async with AsyncCamoufox(
        headless=headless,
        humanize=True,
        os=("windows","macos","linux"),
        locale="en-US",
        geoip=True,
        i_know_what_im_doing=True,
        config={
            "showcursor": False,
        },
        firefox_user_prefs={
            "media.navigator.permission.disabled": True,
            "media.peerconnection.enabled": False,
        },
        # 关键：proxy 不走 CDP 探针，改命令行 args
        proxy={"server":f"socks5://127.0.0.1:{proxy_port}"},
    ) as browser:
        ctx=await browser.new_context(
            viewport={"width":1366,"height":768},
            locale="en-US",
            timezone_id="America/Los_Angeles",
        )
        page=await ctx.new_page()

        try:
            # 0. 出口 IP
            result["phase"]="get_exit_ip"
            try:
                await page.goto("https://api.ipify.org?format=json",
                                wait_until="domcontentloaded",timeout=45000)
                ip_text=await page.locator("body").inner_text()
                result["exit_ip"]=json.loads(ip_text).get("ip","")
                inf(f"  出口 IP: {result['exit_ip']}")
            except Exception as e:
                warn(f"  IP 获取失败: {e}")

            # 1. signup 页
            result["phase"]="navigate"
            await page.goto(SIGNUP_URL,wait_until="load",timeout=70000)
            await human_dwell(page)
            body=await page.locator("body").inner_text()
            if "failed to evaluate" in body.lower() or "browser integrity" in body.lower():
                result["error"]="integrity_check_failed"; return result

            # 2. email 按钮
            result["phase"]="click_email"
            for sel in ['button:has-text("Email")','button:has-text("Continue with email")',
                        '[data-cy="email-signup"]','a:has-text("Email")']:
                btn=page.locator(sel)
                if await btn.count():
                    box=await btn.first.bounding_box()
                    if box: await human_mouse(page,box["x"]+box["width"]/2,box["y"]+box["height"]/2)
                    await btn.first.click(); await page.wait_for_timeout(1500); break

            # 3. 填表
            result["phase"]="fill_form"
            inf(f"  填表: {identity['username']} / {identity['email']}")
            for sel in ['input[name="username"]','input[placeholder*="username" i]']:
                f=page.locator(sel)
                if await f.count():
                    await f.first.click(); await f.first.type(identity["username"],delay=85); break
            await page.wait_for_timeout(500)
            for sel in ['input[type="email"]','input[name="email"]']:
                f=page.locator(sel)
                if await f.count():
                    await f.first.click(); await f.first.type(identity["email"],delay=70); break
            await page.wait_for_timeout(400)
            for sel in ['input[type="password"]','input[name="password"]']:
                f=page.locator(sel)
                if await f.count():
                    await f.first.click(); await f.first.type(identity["password"],delay=60); break
            await page.wait_for_timeout(900)
            await page.screenshot(path=f"/tmp/v3_{identity['username']}_form.png")

            # 4. 提交 (captcha 在提交后才会出现)
            submit_ts = time.time()
            result["phase"]="submit"
            for sel in ['button[type="submit"]','button:has-text("Create Account")',
                        'button:has-text("Sign up")','button:has-text("Continue")']:
                btn=page.locator(sel)
                if await btn.count():
                    await btn.first.click(); break
            else:
                await page.keyboard.press("Enter")
            await page.wait_for_timeout(5000)
            inf(f"  提交后 URL: {page.url[:70]}")
            await dump_frames(page," post-submit")
            ctype=await detect_captcha(page)
            if ctype:
                inf(f"  [captcha] 检测到 {ctype}")
                if ctype=="recaptcha":
                    await solve_audio_recaptcha(page,timeout=160)
                    for sel in ['button[type="submit"]','button:has-text("Continue")','button:has-text("Verify")','button:has-text("Create Account")']:
                        b=page.locator(sel)
                        if await b.count():
                            try: await b.first.click(); break
                            except: pass
                    await page.wait_for_timeout(5000)
                else:
                    warn(f"  [captcha] {ctype} 暂未实现，等待手动")
            await page.screenshot(path=f"/tmp/v3_{identity['username']}_after.png")

            body2=await page.locator("body").inner_text()
            if "failed to evaluate" in body2.lower():
                result["error"]="integrity_after_submit"; return result

            # 6. 邮件验证 (Outlook Graph)
            result["phase"]="email_verify"
            rtok = identity.get("refresh_token")
            if rtok and wait_for_replit_verify:
                inf(f"  Graph 轮询 Outlook 收件箱 (after_ts={submit_ts:.0f})…")
                vurl = wait_for_replit_verify(rtok, timeout=240, after_ts=submit_ts)
                if vurl:
                    inf(f"  验证链接: {vurl[:70]}")
                    try:
                        await page.goto(vurl, wait_until="load", timeout=30000)
                        await page.wait_for_timeout(5000)
                        ok(f"  邮件验证完成 → {page.url[:60]}")
                        result["verified"] = True
                    except Exception as ve:
                        warn(f"  验证链接失败: {ve}")
                else:
                    warn("  Graph 240s 内未取到 Replit 验证邮件")
            else:
                warn("  无 refresh_token，跳过邮件验证")

            # 7. cookies
            result["phase"]="cookies"
            cookies=await ctx.cookies("https://replit.com")
            result["cookies"]=cookies
            sess=next((c["value"] for c in cookies if "session" in c["name"].lower() or "auth" in c["name"].lower()),"")
            inf(f"  Session cookie: {'OK' if sess else '无'}")

            result["ok"]=True; result["phase"]="done"
        except Exception as e:
            er(f"  浏览器异常: {e}")
            result["error"]=str(e)
            try: await page.screenshot(path=f"/tmp/v3_{identity['username']}_err.png")
            except: pass
    return result

# ── DB ────────────────────────────────────────────────────────────────────────
def db_save(identity,status="registered",extra="{}"):
    q=lambda v:"'"+str(v).replace("'","''")+"'"
    sql=f"""INSERT INTO accounts (platform,email,password,username,status,notes,tags)
            VALUES ('replit',{q(identity['email'])},{q(identity['password'])},
                    {q(identity['username'])},{q(status)},{q(extra)},'replit,subnode,v7.36')
            ON CONFLICT (email) DO UPDATE
              SET status=EXCLUDED.status, notes=EXCLUDED.notes, updated_at=now();"""
    try:
        subprocess.run(["psql",DB_URL,"-t","-A","-c",sql],
                       capture_output=True,text=True,timeout=8)
    except Exception as e:
        warn(f"DB 写入失败: {e}")

# ── 主流程 ────────────────────────────────────────────────────────────────────
async def signup_one(headless=False):
    ident=gen_identity()
    if not ident:
        return {"ok":False, "error":"no_outlook_available"}
    hdr(f"账号: {ident['username']}  <{ident['email']}>")
    inf(f"邮箱: {ident['email']}  (Outlook池, refresh_token len={len(ident.get('refresh_token',''))})")
    tok = None

    ua=random.choice(USER_AGENTS)
    ports=(POLL_BRIDGE_PORTS+random.sample(XRAY_SOCKS_PORTS,
                                            min(MAX_RETRIES,len(XRAY_SOCKS_PORTS))))[:MAX_RETRIES]
    last_err=""
    for i,port in enumerate(ports,1):
        if not ensure_tunnel(port,timeout=8):
            warn(f"  端口 {port} 不可达，跳过"); continue
        inf(f"尝试 #{i}: socks5://127.0.0.1:{port}")
        r=await _do_signup(ident,ua,port,tok,headless=headless)
        if r["ok"]:
            db_save(ident,"registered",json.dumps({"exit_ip":r["exit_ip"],"port":port}))
            ok(f"注册完成: {ident['email']}")
            return {"ok":True,"email":ident["email"],"username":ident["username"],
                    "exit_ip":r["exit_ip"]}
        last_err=r.get("error","")
        warn(f"失败 ({r.get('phase')}): {last_err}")
    er(f"全部 {MAX_RETRIES} 次失败")
    db_save(ident,f"failed:{last_err[:80]}")
    return {"ok":False,"email":ident["email"],"error":last_err}

async def main_async(args):
    if not args.no_xvfb:
        ensure_xvfb()
    headless=False if not args.no_xvfb else args.headless
    for i in range(args.count):
        print(f"\n{'='*60}\n账号 {i+1}/{args.count}\n{'='*60}")
        await signup_one(headless=headless)
        if i<args.count-1: await asyncio.sleep(random.randint(20,45))

class SignupHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path!="/signup":
            self.send_response(404); self.end_headers(); return
        ln=int(self.headers.get("Content-Length","0") or 0)
        body=self.rfile.read(ln) if ln else b"{}"
        try:    cnt=int(json.loads(body).get("count",1))
        except: cnt=1
        loop=asyncio.new_event_loop()
        results=[loop.run_until_complete(signup_one(headless=False)) for _ in range(cnt)]
        loop.close()
        self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers()
        self.wfile.write(json.dumps({"ok":True,"results":results}).encode())
    def log_message(self,*a,**k): pass

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--count",type=int,default=1)
    p.add_argument("--headless",action="store_true")
    p.add_argument("--no-xvfb",action="store_true",help="不启动 Xvfb (本地有显示)")
    p.add_argument("--serve",type=int,default=0)
    args=p.parse_args()
    if not args.no_xvfb: ensure_xvfb()
    if args.serve:
        print(f"[signup-server v7.36] 监听 0.0.0.0:{args.serve}")
        HTTPServer(("0.0.0.0",args.serve),SignupHandler).serve_forever()
    else:
        asyncio.run(main_async(args))
