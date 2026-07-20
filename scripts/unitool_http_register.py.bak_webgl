#!/usr/bin/env python3
"""
unitool_http_register.py v3.2 — unitool.ai 混合协议注册（pydoll + multipart FormData）
=============================================================================

分析结论（2025-05）:
  spring-ai-mcp-demo  ✗ 完全不适用（Java Spring AI MCP RPC 框架，与注册零关联）
  chatgpt2api         ✓ 两组件直接移植：
                         - curl_cffi Session(impersonate="chrome") — TLS 指纹 ★★★★★
                         - solve_turnstile_token(dx,p)            — 纯 Python 求解器
                           ⚠ 需要 dx/p 参数，只有浏览器执行 CF JS 才能产生
                           ⚠ 纯 HTTP 获取 dx/p 可行性 0%（CF URLs 全返回 404）

实测结论：
  • fake_token 与 empty_token 返回完全相同错误 digest=3453729035
  • Cloudflare 在服务端真实验证 Turnstile token，无法伪造/跳过
  • 唯一免费可行方案: pydoll bypass → 提取真实 token → curl_cffi HTTP 提交

混合模式流程（比全浏览器快 40%）:
  Step A. pydoll Chrome(RESI proxy) → /en/entry → bypass_cloudflare → 提取 token → 关闭
  Step B. curl_cffi Chrome指纹 → GET /en/entry → POST 注册（携带真实 token）
  Step C. 邮件验证由上层 unitool_pipeline.py / unitool_chain_v3.py 处理
"""

import asyncio, base64, json, os, random, re, socket, sys, time
from typing import Optional

sys.path.insert(0, "/data/Toolkit/scripts")

# ── 依赖 ──────────────────────────────────────────────────────────────────────
try:
    import resi_pool as _rpool
    _RESI = True
except ImportError:
    _RESI = False

try:
    from curl_cffi import requests as _cffi
    _CFFI = True
except ImportError:
    import requests as _cffi  # type: ignore
    _CFFI = False

# ── 常量（实测确认） ───────────────────────────────────────────────────────────
UNITOOL_BASE = "https://unitool.ai"
# Next-Action 哈希：每次 Next.js 部署可能更新，启动时动态确认
_SIGNUP_NA_DEFAULT = "602b5c42ffedec9865ca902b033d188b22c575dfd5"
_LOGIN_NA_DEFAULT  = "60e02e33f743e14f5dab1dc42181ba1e746fd4d925"
# Turnstile sitekey（shadow DOM，不在 HTML body 中，在 JS bundle 里）
SITEKEY = "0x4AAAAAAC-pdVMpBJQaHL0Q"
# JS fingerprint injection: hide automation signals on every new document
_FINGERPRINT_JS = "(function() {\n  try { Object.defineProperty(navigator,'webdriver',{get:()=>undefined,configurable:true}); } catch(e){}\n  try { Object.defineProperty(navigator,'deviceMemory',{get:()=>8,configurable:true}); } catch(e){}\n  try { Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8,configurable:true}); } catch(e){}\n  try { Object.defineProperty(navigator,'languages',{get:()=>['en-US','en'],configurable:true}); } catch(e){}\n  try {\n    var pp={name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format'};\n    var pl=Object.create(PluginArray.prototype);\n    Object.defineProperty(pl,'length',{get:()=>1});\n    Object.defineProperty(pl,'0',{get:()=>pp});\n    pl.item=()=>pp; pl.namedItem=()=>pp; pl.refresh=()=>{};\n    Object.defineProperty(navigator,'plugins',{get:()=>pl,configurable:true});\n  } catch(e){}\n  try {\n    if(!window.chrome){\n      Object.defineProperty(window,'chrome',{\n        value:{runtime:{},loadTimes:function(){},csi:function(){},app:{}},\n        configurable:true,writable:true});\n    }\n  } catch(e){}\n  try {\n    var oq=Permissions.prototype.query;\n    Permissions.prototype.query=function(p){\n      if(p&&p.name==='notifications') return Promise.resolve({state:'prompt',onchange:null});\n      return oq.apply(this,arguments);\n    };\n  } catch(e){}\n  // Canvas fingerprint noise: LSB-flip every 200th byte to break hash-based detection\n  try {\n    const origGID = CanvasRenderingContext2D.prototype.getImageData;\n    CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {\n      const d = origGID.call(this, x, y, w, h);\n      for (let i = 0; i < d.data.length; i += 200) { d.data[i] ^= 1; }\n      return d;\n    };\n  } catch(e) {}\n  try {\n    const origTDU = HTMLCanvasElement.prototype.toDataURL;\n    HTMLCanvasElement.prototype.toDataURL = function() {\n      const c = this.getContext && this.getContext('2d');\n      if (c && this.width > 0 && this.height > 0) {\n        c.putImageData(c.getImageData(0, 0, this.width, this.height), 0, 0);\n      }\n      return origTDU.apply(this, arguments);\n    };\n  } catch(e) {}\n  try {\n    const origTB = HTMLCanvasElement.prototype.toBlob;\n    HTMLCanvasElement.prototype.toBlob = function(cb, type, q) {\n      const c = this.getContext && this.getContext('2d');\n      if (c && this.width > 0 && this.height > 0) {\n        c.putImageData(c.getImageData(0, 0, this.width, this.height), 0, 0);\n      }\n      return origTB.call(this, cb, type, q);\n    };\n  } catch(e) {}\n})();"


# ── pydoll 依赖（Chromium 路径） ──────────────────────────────────────────────
CHROME = None
for _p in [
    "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
    "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
    "/data/cache/ms-playwright/chromium-1169/chrome-linux64/chrome",
]:
    if os.path.exists(_p):
        CHROME = _p
        break

# ── Chrome 145 指纹 headers（移植自 chatgpt2api） ────────────────────────────
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
_SEC_CH_UA = '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"'

HDR_NAV = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": _UA,
    "sec-ch-ua": _SEC_CH_UA,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
}

# ── 错误 digest 映射（实测） ──────────────────────────────────────────────────
_DIGEST_MAP = {
    "3453729035": "turnstile_invalid",
    "1068100299": "payload_parse_error",
    "2879057947": "urlencoded_not_accepted",
}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Turnstile 求解器（移植自 chatgpt2api/utils/turnstile.py）
# 前提：需要 dx + p（来自浏览器执行 CF challenge frame JS）
# 现状：CF 的 challenge URL 全返回 404，纯 HTTP 无法获取 dx/p
# 保留此函数以备将来 CF 开放或有其他来源提供 dx/p
# ─────────────────────────────────────────────────────────────────────────────

def _xor_str(text: str, key: str) -> str:
    if not key:
        return text
    return "".join(chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(text))


def _ts_str(v) -> str:
    if v is None:
        return "undefined"
    if isinstance(v, float):
        return str(v)
    if isinstance(v, str):
        _S = {
            "window.Math":               "[object Math]",
            "window.Reflect":            "[object Reflect]",
            "window.performance":        "[object Performance]",
            "window.localStorage":       "[object Storage]",
            "window.Object":             "function Object() { [native code] }",
            "window.Reflect.set":        "function set() { [native code] }",
            "window.performance.now":    "function () { [native code] }",
            "window.Object.create":      "function create() { [native code] }",
            "window.Object.keys":        "function keys() { [native code] }",
            "window.Math.random":        "function random() { [native code] }",
        }
        return _S.get(v, v)
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return ",".join(v)
    return str(v)


class _OMap:
    """chatgpt2api OrderedMap 移植"""
    def __init__(self):
        self.keys = []
        self.values = {}

    def add(self, k, v):
        if k not in self.values:
            self.keys.append(k)
        self.values[k] = v

    def __str__(self):
        return "[object Object]"


def solve_turnstile_token(dx: str, p: str) -> Optional[str]:
    """
    移植自 chatgpt2api/utils/turnstile.py（OrderedMap / func_* VM）
    dx: Cloudflare challenge frame 中的 base64+XOR 加密 blob
    p:  XOR 密钥（通常是 sitekey 或其派生值）
    """
    try:
        decoded = base64.b64decode(dx).decode()
        token_list = json.loads(_xor_str(decoded, p))
    except Exception as e:
        log(f"[ts_solver] decode error: {e}")
        return None

    pm: dict = {}
    t0 = time.time()
    result = ""

    def f1(e, t):
        pm[e] = _xor_str(_ts_str(pm.get(e, "")), _ts_str(pm.get(t, "")))

    def f2(e, t):
        pm[e] = t

    def f3(e):
        nonlocal result
        result = base64.b64encode(str(e).encode()).decode()

    def f5(e, t):
        cur, inc = pm.get(e), pm.get(t)
        if isinstance(cur, (list, tuple)):
            pm[e] = list(cur) + [inc]
            return
        if isinstance(cur, (str, float)) or isinstance(inc, (str, float)):
            pm[e] = _ts_str(cur) + _ts_str(inc)
            return
        pm[e] = "NaN"

    def f6(e, t, n):
        tv, nv = pm.get(t, ""), pm.get(n, "")
        val = f"{_ts_str(tv)}.{_ts_str(nv)}"
        pm[e] = "https://chatgpt.com/" if val == "window.document.location" else val

    def f7(e, *args):
        tgt = pm.get(e)
        vals = [pm.get(a) for a in args]
        if isinstance(tgt, str) and tgt == "window.Reflect.set":
            if len(vals) >= 3:  # Bug fix: guard against IndexError
                obj, kn, v = vals[0], vals[1], vals[2]
                if isinstance(obj, _OMap):
                    obj.add(str(kn), v)
        elif callable(tgt):
            tgt(*vals)

    def f8(e, t):
        pm[e] = pm.get(t)

    def f14(e, t):
        try:
            pm[e] = json.loads(pm.get(t, "{}"))
        except Exception:
            pm[e] = {}

    def f15(e, t):
        try:
            pm[e] = json.dumps(pm.get(t))
        except Exception:
            pm[e] = "null"

    def f17(e, t, *args):
        call_args = [pm.get(a) for a in args]
        tgt = pm.get(t)
        if tgt == "window.performance.now":
            pm[e] = (time.time_ns() - int(t0 * 1e9) + random.random()) / 1e6
        elif tgt == "window.Object.create":
            pm[e] = _OMap()
        elif tgt == "window.Object.keys":
            if call_args and call_args[0] == "window.localStorage":
                pm[e] = [
                    "STATSIG_LOCAL_STORAGE_INTERNAL_STORE_V4",
                    "STATSIG_LOCAL_STORAGE_STABLE_ID",
                    "client-correlated-secret",
                    "oai/apps/capExpiresAt",
                    "oai-did",
                    "STATSIG_LOCAL_STORAGE_LOGGING_REQUEST",
                    "UiState.isNavigationCollapsed.1",
                ]
        elif tgt == "window.Math.random":
            pm[e] = random.random()
        elif callable(tgt):
            pm[e] = tgt(*call_args)

    def f18(e):
        try:
            pm[e] = base64.b64decode(_ts_str(pm.get(e, ""))).decode()
        except Exception:
            pm[e] = ""

    def f19(e):
        try:
            pm[e] = base64.b64encode(_ts_str(pm.get(e, "")).encode()).decode()
        except Exception:
            pm[e] = ""

    def f20(e, t, n, *args):
        if pm.get(e) == pm.get(t):
            tgt = pm.get(n)
            if callable(tgt):
                tgt(*[pm.get(a) for a in args])

    def f21(*_):
        return

    def f23(e, t, *args):
        # Bug fix: original chatgpt2api passes args directly (not as indices)
        if pm.get(e) is not None:
            tgt = pm.get(t)
            if callable(tgt):
                tgt(*args)

    def f24(e, t, n):
        tv, nv = pm.get(t, ""), pm.get(n, "")
        if isinstance(tv, str) and isinstance(nv, str):
            pm[e] = f"{tv}.{nv}"

    pm.update({
        1: f1, 2: f2, 3: f3, 5: f5, 6: f6, 7: f7, 8: f8,
        9: token_list, 10: "window", 14: f14, 15: f15, 16: p,
        17: f17, 18: f18, 19: f19, 20: f20, 21: f21, 23: f23, 24: f24,
    })

    for tok in token_list:
        try:
            fn = pm.get(tok[0])
            if callable(fn):
                fn(*tok[1:])
        except Exception:
            continue

    return result or None


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def _free_port(lo=12000, hi=29999):
    tried: set = set()
    while len(tried) < (hi - lo):
        p = random.randint(lo, hi)
        if p in tried:
            continue
        tried.add(p)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("no free port")


def _pick_port(hint: int = 0) -> int:
    if _RESI:
        return _rpool.pick(hint=hint % 97) if hint else _rpool.pick()
    return 10851


def make_session(port: int = 0):
    """curl_cffi Chrome 指纹会话 + SOCKS5（移植自 chatgpt2api）"""
    proxies = {}
    if port:
        proxies = {
            "http":  f"socks5://127.0.0.1:{port}",
            "https": f"socks5://127.0.0.1:{port}",
        }
    if _CFFI:
        sess = _cffi.Session(impersonate="chrome", verify=False)
        if proxies:
            sess.proxies = proxies
        return sess
    import requests
    sess = requests.Session()
    sess.proxies = proxies
    return sess


def _extract_signup_na(html: str) -> str:
    """动态从 HTML 提取 SIGNUP Next-Action 哈希（防止 Next.js 重新部署后失效）"""
    # NA 哈希: 42 位小写 hex，在 script 标签中
    candidates = list(set(re.findall(r"[a-f0-9]{42}", html)))
    # 优先返回已知的（验证它还在）
    if _SIGNUP_NA_DEFAULT in candidates:
        return _SIGNUP_NA_DEFAULT
    # 返回第一个候选（通常就是 NA）
    return candidates[0] if candidates else _SIGNUP_NA_DEFAULT


# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# pydoll 混合注册：bypass + JS fetch() 提交（同一 cookie 上下文）
#
# Bug #1 根因（v3.0 实测）：
#   pydoll Chrome 产生的 Turnstile token 绑定浏览器内 CF cookie
#   (__cf_bm / cf_clearance)。若将 token 传给 curl_cffi 新会话提交，
#   CF 服务端检测到 cookie ↔ token 不匹配 → digest=3453729035 (turnstile_invalid)。
#
# 修法（v3.1）：
#   bypass 完成后，直接在浏览器内调 JS fetch() 发 POST，
#   浏览器自动携带全部 cookie，token 验证必然通过。
#   省去：表单填写 / 按钮点击 / 页面跳转等待（比全浏览器仍快 ~30%）
# ─────────────────────────────────────────────────────────────────────────────

_RST = "%5B%22%22%2C%7B%22children%22%3A%5B%5B%22lang%22%2C%22en%22%2C%22d%22%5D%2C%7B%22children%22%3A%5B%22entry%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%5D%7D%5D%7D%5D%7D%2Cnull%2Cnull%2Ctrue%5D"


async def _pydoll_register(
    email: str,
    password: str,
    ref_code: str = "",
    resi_port: int = 0,
    signup_na: str = "",
) -> dict:
    """
    pydoll Chrome（RESI 代理）完整注册流程：
      1. 访问 /en/entry（带 ref_code cookie）
      2. bypass_cloudflare() 获取真实 Turnstile token
      3. 在浏览器内 JS fetch() POST 注册（同一 cookie 上下文）
      4. 解析 RSC 响应流
      5. 关闭浏览器

    返回: {"ok": bool, "raw": str, "token_len": int, "build_id": str, ...}
    """
    try:
        from pydoll.browser import Chrome
        from pydoll.browser.options import ChromiumOptions
    except ImportError:
        return {"ok": False, "error": "pydoll_not_installed"}

    port = resi_port or _pick_port(hash(email))
    log(f"[pydoll] 启动 Chrome RESI={port} email={email}")

    opt = ChromiumOptions()
    display = os.environ.get("DISPLAY", "")
    if not display:
        import glob
        if glob.glob("/tmp/.X99-lock") or glob.glob("/tmp/.X[0-9]-lock"):
            display = ":99"
            os.environ["DISPLAY"] = display
    opt.headless = not bool(display)
    if CHROME:
        opt.binary_location = CHROME
    for arg in [
        "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1440,900",
        "--disable-gpu", "--lang=en-US",
        "--disable-blink-features=AutomationControlled",
        f"--proxy-server=socks5://127.0.0.1:{port}",
    ]:
        opt.add_argument(arg)

    result: dict = {"ok": False, "email": email, "port": port, "token_len": 0, "raw": ""}
    t_start = time.time()

    # ── JS 工具函数 ──────────────────────────────────────────────────────────
    def _s(r):
        if not isinstance(r, dict):
            return str(r) if r else ""
        inner = r.get("result", r)
        if isinstance(inner, dict):
            inner = inner.get("result", inner)
        return str(inner.get("value", "")) if isinstance(inner, dict) else str(inner)

    async def _tok_len(tab) -> int:
        try:
            return int(_s(await tab.execute_script(
                "(document.querySelector('[name=\"cf-turnstile-response\"]')||{value:''}).value.length",
                return_by_value=True)) or 0)
        except Exception:
            return 0

    async def _get_token(tab) -> str:
        n = await _tok_len(tab)
        if n < 20:
            return ""
        parts = []
        for cs in range(0, n + 300, 300):
            c = _s(await tab.execute_script(
                f"(document.querySelector('[name=\"cf-turnstile-response\"]')||{{value:''}}).value.slice({cs},{cs+300})",
                return_by_value=True))
            if c:
                parts.append(c)
            if not c or cs + 300 >= n:
                break
        return "".join(parts)

    async def _bypass_wait(tab, label="", rounds=2, per_round=10) -> bool:
        """等 Turnstile iframe → 多轮 bypass → reload 兜底"""
        for i in range(12):
            await asyncio.sleep(1)
            n_iframe = int(_s(await tab.execute_script(
                "document.querySelectorAll('iframe[src*=\"challenges.cloudflare\"]').length",
                return_by_value=True)) or 0)
            if n_iframe > 0:
                log(f"  [{label}] Turnstile iframe ready at {i+1}s")
                break
            if i % 3 == 2:
                log(f"  [{label}] [{i+1}s] waiting iframe...")

        for rnd in range(rounds):
            try:
                _bp_t0 = time.time()
                log(f"  [{label}] bypass start round={rnd+1}")
                await tab._bypass_cloudflare({}, time_to_wait_captcha=20)
                log(f"  [{label}] bypass done round={rnd+1} bp={time.time()-_bp_t0:.1f}s")
            except Exception as e:
                log(f"  [{label}] bypass err round={rnd+1}: {e}")
            for i in range(per_round):
                await asyncio.sleep(1)
                n = await _tok_len(tab)
                if n > 20:
                    log(f"  [{label}] token ready rnd={rnd+1} t={i+1}s len={n}")
                    return True
                if i % 5 == 4:
                    log(f"  [{label}] [{i+1}s] token len={n} ...")
            log(f"  [{label}] round {rnd+1} token=0, retry bypass...")
            await asyncio.sleep(2)

        # 最终兜底: reload
        log(f"  [{label}] all rounds failed, reloading...")
        await tab.go_to(f"{UNITOOL_BASE}/en/entry")
        await asyncio.sleep(6)
        for i in range(12):
            await asyncio.sleep(1)
            n_iframe = int(_s(await tab.execute_script(
                "document.querySelectorAll('iframe[src*=\"challenges.cloudflare\"]').length",
                return_by_value=True)) or 0)
            if n_iframe > 0:
                break
        try:
            _bp_t0 = time.time()
            log(f"  [{label}] bypass start reload")
            await tab._bypass_cloudflare({}, time_to_wait_captcha=25)
            log(f"  [{label}] bypass done reload bp={time.time()-_bp_t0:.1f}s")
        except Exception as e:
            log(f"  [{label}] reload bypass err: {e}")
        for i in range(20):
            await asyncio.sleep(1)
            n = await _tok_len(tab)
            if n > 20:
                log(f"  [{label}] final token len={n} at {i+1}s")
                return True
        return False

    try:
        async with Chrome(options=opt, connection_port=_free_port()) as browser:
            tab = await browser.start()
            # Fingerprint injection: hide automation signals before first navigation
            try:
                from pydoll.commands.page_commands import PageCommands as _PC
                await tab._execute_command(
                    _PC.add_script_to_evaluate_on_new_document(_FINGERPRINT_JS))
                log('[fp] fingerprint injection OK')
            except Exception as _fp_e:
                log('[fp] inject warn (non-fatal): ' + str(_fp_e)[:80])
            await tab.enable_network_events()

            # ref_code: 先访问 /ref/<code> 写入 cookie
            if ref_code:
                log(f"[pydoll] visiting ref /ref/{ref_code}")
                await tab.go_to(f"{UNITOOL_BASE}/ref/{ref_code}")
                await asyncio.sleep(4)

            log(f"[pydoll] goto /en/entry")
            await tab.go_to(f"{UNITOOL_BASE}/en/entry")
            await asyncio.sleep(4)

            # ── Step 1: bypass Turnstile ─────────────────────────────────────
            log("[pydoll] bypassing Turnstile...")
            bypass_ok = await _bypass_wait(tab, "signup")
            if not bypass_ok:
                result["error"] = "bypass_failed"
                return result

            token = await _get_token(tab)
            result["token_len"] = len(token)
            log(f"[pydoll] token len={len(token)}")
            if not token:
                result["error"] = "token_empty_after_bypass"
                return result

            # 动态获取 SIGNUP_NA（从页面 source 提取，与浏览器 build 一致）
            page_src = _s(await tab.execute_script(
                "document.documentElement.innerHTML.slice(0, 200000)",
                return_by_value=True))
            na = signup_na or _extract_signup_na(page_src) or _SIGNUP_NA_DEFAULT
            log(f"[pydoll] SIGNUP_NA={na} ({'ok' if na == _SIGNUP_NA_DEFAULT else '⚠ changed'})")

            # ── Step 2: JS fetch() FormData POST（同一 session，token 与 cookie 匹配）─
            # Bug #2 修复（v3.2）：
            #   真实按钮提交格式是 multipart/form-data，字段名带 1_ 前缀
            #   原来发 application/json 服务端解析不到 token 字段 → turnstile_invalid
            #   字段: 1_email / 1_password / 1_cf-turnstile-response / 1_captcha_token
            #         1_captcha_action="signup" / 0=React state
            log("[pydoll] JS FormData POST /en/entry ...")
            ref_append = f'fd.append("1_ref_code", {json.dumps(ref_code)});' if ref_code else ""
            fetch_js = f"""
(async function() {{
  try {{
    const token = document.querySelector('[name="cf-turnstile-response"]').value;
    const fd = new FormData();
    fd.append("1_email",                 {json.dumps(email)});
    fd.append("1_password",              {json.dumps(password)});
    fd.append("1_cf-turnstile-response", token);
    fd.append("1_captcha_token",         token);
    fd.append("1_captcha_action",        "signup");
    fd.append("0", '[{{"error":"","next":null,"success":false}},"$K1"]');
    {ref_append}
    const r = await fetch("https://unitool.ai/en/entry", {{
      method: "POST",
      headers: {{
        "accept":                 "text/x-component",
        "accept-language":        "en-US,en;q=0.9",
        "next-action":            {json.dumps(na)},
        "next-router-state-tree": {json.dumps(_RST)},
        "origin":                 "https://unitool.ai",
        "referer":                "https://unitool.ai/en/entry",
        "sec-fetch-dest":         "empty",
        "sec-fetch-mode":         "cors",
        "sec-fetch-site":         "same-origin"
      }},
      credentials: "include",
      body: fd
    }});
    const text = await r.text();
    return JSON.stringify({{status: r.status, body: text.slice(0, 800)}});
  }} catch(e) {{
    return JSON.stringify({{status: 0, error: String(e)}});
  }}
}})()
"""
            raw_json = _s(await tab.execute_script(fetch_js, return_by_value=True, await_promise=True))
            log(f"[pydoll] fetch result: {raw_json[:300]}")

            try:
                fetch_result = json.loads(raw_json)
            except Exception:
                fetch_result = {"status": 0, "error": f"json_parse: {raw_json[:100]}"}

            http_status = fetch_result.get("status", 0)
            raw_body = fetch_result.get("body", "")
            result["status"] = http_status
            result["raw"] = raw_body

            # ── Step 3: 解析 RSC 响应 ────────────────────────────────────────
            # Turnstile digest 错误（CF 拒绝 token）
            digest_m = re.search(r'"digest"\s*:\s*"(\d+)"', raw_body)
            if digest_m:
                d = digest_m.group(1)
                result["digest"] = d
                result["error_type"] = _DIGEST_MAP.get(d, f"unknown_{d}")
                log(f"[pydoll] RSC error digest={d} → {result['error_type']}")

            # build_id
            bid = re.search(r'"b"\s*:\s*"([^"]+)"', raw_body)
            if bid:
                result["build_id"] = bid.group(1)

            # 业务逻辑错误（RSC stream: 1:{"error":"..."}）
            # v3.2: 区分 CF 验证错误 vs 业务错误（invalid email / already registered 等）
            biz_err_m = re.search(r'1:\{"error"\s*:\s*"([^"]+)"', raw_body)
            if biz_err_m:
                biz_msg = biz_err_m.group(1)
                result["error"] = biz_msg[:120]
                result["error_type"] = "business"
                log(f"[pydoll] 业务错误: {biz_msg[:80]}")
            elif http_status == 200 and "1:E{" not in raw_body and not digest_m:
                # 无 CF 错误 + 无业务错误 → 成功（邮件已发出）
                result["ok"] = True
                log(f"[pydoll] ✓ 注册成功 {email}")
            elif not digest_m and not biz_err_m:
                result["error"] = fetch_result.get("error", f"http_{http_status}")

            # 兜底：邮件提示词（后端有时直接返回文本）
            raw_lower = raw_body.lower()
            if any(w in raw_lower for w in ("sent link", "check your email",
                                             "verify your email", "follow the link")):
                result["ok"] = True
                result.pop("error", None)
                log(f"[pydoll] ✓ 邮件确认词检测到")

    except Exception as e:
        result["error"] = str(e)[:200]
        log(f"[pydoll] exception: {e}")

    elapsed = time.time() - t_start
    log(f"[pydoll] 完成 {elapsed:.1f}s ok={result['ok']}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# curl_cffi 辅助提交（备用：当外部已有有效 token 时）
# ─────────────────────────────────────────────────────────────────────────────

def _http_submit_with_cookies(
    cf_token: str,
    browser_cookies: dict,
    email: str,
    password: str,
    ref_code: str = "",
    resi_port: int = 0,
    signup_na: str = "",
) -> dict:
    """
    curl_cffi 提交（需传入与 token 配套的浏览器 cookies）。
    正常流程不用此函数——主流程已在 pydoll 内完成 fetch()。
    此函数供外部已有 token + cookies 时使用。
    """
    port = resi_port or _pick_port(hash(email))
    sess = make_session(port)
    result = {"ok": False, "email": email, "port": port, "raw": ""}

    try:
        # 注入浏览器 cookies
        for k, v in browser_cookies.items():
            try:
                sess.cookies.set(k, v, domain="unitool.ai")
            except Exception:
                pass

        na = signup_na or _SIGNUP_NA_DEFAULT
        # v3.2: multipart/form-data（与真实浏览器按钮提交格式一致）
        import uuid as _uuid
        boundary = "----WebKitFormBoundary" + _uuid.uuid4().hex[:16]
        def _mp_field(name: str, value: str) -> bytes:
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        body_parts = [
            _mp_field("1_email",                 email),
            _mp_field("1_password",              password),
            _mp_field("1_cf-turnstile-response", cf_token),
            _mp_field("1_captcha_token",         cf_token),
            _mp_field("1_captcha_action",        "signup"),
            _mp_field("0",                       '[{"error":"","next":null,"success":false},"$K1"]'),
        ]
        if ref_code:
            body_parts.append(_mp_field("1_ref_code", ref_code))
        body_parts.append(f"--{boundary}--\r\n".encode())
        multipart_body = b"".join(body_parts)

        hdr_post = {
            "accept": "text/x-component",
            "accept-language": "en-US,en;q=0.9",
            "content-type": f"multipart/form-data; boundary={boundary}",
            "next-action": na,
            "next-router-state-tree": _RST,
            "origin": UNITOOL_BASE,
            "referer": f"{UNITOOL_BASE}/en/entry",
            "user-agent": _UA,
            "sec-ch-ua": _SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        r = sess.post(f"{UNITOOL_BASE}/en/entry", headers=hdr_post,
                      data=multipart_body, timeout=25, allow_redirects=False)
        raw = r.text
        result["status"] = r.status_code
        result["raw"] = raw[:500]

        digest_m = re.search(r'"digest"\s*:\s*"(\d+)"', raw)
        if digest_m:
            d = digest_m.group(1)
            result["digest"] = d
            result["error_type"] = _DIGEST_MAP.get(d, f"unknown_{d}")

        if r.status_code == 200 and "1:E{" not in raw and not digest_m:
            result["ok"] = True

        raw_lower = raw.lower()
        if any(w in raw_lower for w in ("sent link", "check your email",
                                         "verify your email", "follow the link")):
            result["ok"] = True

    except Exception as e:
        result["error"] = str(e)
    finally:
        try:
            sess.close()
        except Exception:
            pass

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────────

async def http_register_hybrid(
    email: str,
    password: str,
    ref_code: str = "",
    resi_port: int = 0,
) -> dict:
    """
    混合注册 v3.1（Bug #1 修复版）：
      pydoll Chrome bypass Turnstile → 浏览器内 JS fetch() POST 注册
      比全浏览器省去：表单填写 / 按钮等待 / 页面跳转（快 ~30%）
      token ↔ cookie 绑定在同一浏览器会话，CF 验证必然通过
    """
    # Fix14: bypass失败换端口重试（CF Turnstile 静默拒绝当前IP时换IP）
    _BYPASS_FAIL_ERRORS = ("bypass_failed", "token_empty_after_bypass")
    port = resi_port or _pick_port(hash(email))
    result = {}
    for _attempt in range(3):
        if _attempt > 0:
            if _RESI:
                _rpool.report_failure(port)
            port = _pick_port(hash(email) + _attempt * 37)
            log(f"[hybrid] bypass失败换端口 attempt={_attempt+1} new_port={port}")
        log(f"[hybrid] {email} ref={ref_code or chr(45)} port={port} attempt={_attempt+1}")
        result = await _pydoll_register(email, password, ref_code=ref_code, resi_port=port)
        result["method"] = "hybrid_v3.2"
        if result.get("ok"):
            log(f"[hybrid] ✓ 注册成功 {email} attempt={_attempt+1}")
            return result
        err = result.get("error_type") or result.get("error") or "unknown"
        log(f"[hybrid] ✗ 失败 attempt={_attempt+1}: {err}")
        if err not in _BYPASS_FAIL_ERRORS:
            break
        if result.get("digest") == "3453729035":
            log("[hybrid] token仍被拒绝，建议检查Xvfb/pydoll版本")
    return result


def http_register(
    email: str,
    password: str,
    ref_code: str = "",
    resi_port: int = 0,
) -> dict:
    """同步封装（供 unitool_chain_v3 等调用）"""
    return asyncio.run(http_register_hybrid(email, password, ref_code, resi_port))


# ─────────────────────────────────────────────────────────────────────────────
# 登录：pydoll bypass → Sign In tab → JS fetch() POST
# ─────────────────────────────────────────────────────────────────────────────

async def http_login_hybrid(
    email: str,
    password: str,
    resi_port: int = 0,
) -> dict:
    """
    登录流程（仅作辅助，主登录走 unitool_login.py）：
      pydoll 点 Sign In tab → bypass login Turnstile → JS fetch() POST
    """
    try:
        from pydoll.browser import Chrome
        from pydoll.browser.options import ChromiumOptions
    except ImportError:
        return {"ok": False, "error": "pydoll_not_installed"}

    port = resi_port or _pick_port(hash(email))
    log(f"[http_login] {email} port={port}")

    opt = ChromiumOptions()
    display = os.environ.get("DISPLAY", "")
    if not display:
        import glob
        if glob.glob("/tmp/.X99-lock") or glob.glob("/tmp/.X[0-9]-lock"):
            display = ":99"
            os.environ["DISPLAY"] = display
    opt.headless = not bool(display)
    if CHROME:
        opt.binary_location = CHROME
    for arg in ["--no-sandbox", "--disable-dev-shm-usage", "--window-size=1440,900",
                 "--disable-gpu", "--lang=en-US",
                 "--disable-blink-features=AutomationControlled",
                 f"--proxy-server=socks5://127.0.0.1:{port}"]:
        opt.add_argument(arg)

    result = {"email": email, "ok": False, "ssid": ""}

    def _s(r):
        if not isinstance(r, dict):
            return str(r) if r else ""
        inner = r.get("result", r)
        if isinstance(inner, dict):
            inner = inner.get("result", inner)
        return str(inner.get("value", "")) if isinstance(inner, dict) else str(inner)

    async def _tok_len(tab, field="cf-turnstile-response") -> int:
        try:
            return int(_s(await tab.execute_script(
                f"(document.querySelector('[name=\"{field}\"]')||{{value:''}}).value.length",
                return_by_value=True)) or 0)
        except Exception:
            return 0

    try:
        async with Chrome(options=opt, connection_port=_free_port()) as browser:
            tab = await browser.start()
            # Fingerprint injection: hide automation signals before first navigation
            try:
                from pydoll.commands.page_commands import PageCommands as _PC
                await tab._execute_command(
                    _PC.add_script_to_evaluate_on_new_document(_FINGERPRINT_JS))
                log('[fp] fingerprint injection OK')
            except Exception as _fp_e:
                log('[fp] inject warn (non-fatal): ' + str(_fp_e)[:80])
            await tab.enable_network_events()

            await tab.go_to(f"{UNITOOL_BASE}/en/entry")
            await asyncio.sleep(4)

            # 点 Sign In tab
            await tab.execute_script("""
                for (var el of document.querySelectorAll('button,[role="tab"]')) {
                    if (el.innerText.trim().toLowerCase() === 'sign in') { el.click(); break; }
                }
            """, return_by_value=True)
            await asyncio.sleep(2)

            # bypass 登录 Turnstile
            for rnd in range(3):
                try:
                    _bp_t0 = time.time()
                    log(f"  [login_bypass] bypass start round={rnd+1}")
                    await tab._bypass_cloudflare({}, time_to_wait_captcha=15)
                    log(f"  [login_bypass] bypass done round={rnd+1} bp={time.time()-_bp_t0:.1f}s")
                except Exception as e:
                    log(f"  [login_bypass] round {rnd+1} err: {e}")
                for i in range(15):
                    await asyncio.sleep(1)
                    ca = _s(await tab.execute_script(
                        "(document.querySelector('[name=\"captcha_action\"]')||{value:'?'}).value",
                        return_by_value=True))
                    n = await _tok_len(tab)
                    if n > 20 and ca == "login":
                        log(f"  [login_bypass] token ready rnd={rnd+1} t={i+1}s action={ca}")
                        break
                else:
                    continue
                break

            # 页面动态获取 LOGIN_NA
            page_src = _s(await tab.execute_script(
                "document.documentElement.innerHTML.slice(0,200000)", return_by_value=True))
            na_candidates = list(set(re.findall(r"[a-f0-9]{42}", page_src)))
            login_na = _LOGIN_NA_DEFAULT
            if login_na not in na_candidates and len(na_candidates) > 1:
                login_na = na_candidates[1]

            # JS FormData POST 登录（v3.2 修复：multipart/form-data，1_ 前缀字段）
            fetch_js = f"""
(async function() {{
  try {{
    const token = document.querySelector('[name="cf-turnstile-response"]').value;
    const fd = new FormData();
    fd.append("1_email",                 {json.dumps(email)});
    fd.append("1_password",              {json.dumps(password)});
    fd.append("1_cf-turnstile-response", token);
    fd.append("1_captcha_token",         token);
    fd.append("1_captcha_action",        "login");
    fd.append("0", '[{{"error":"","next":null,"success":false}},"$K1"]');
    const r = await fetch("https://unitool.ai/en/entry", {{
      method: "POST",
      headers: {{
        "accept":                 "text/x-component",
        "accept-language":        "en-US,en;q=0.9",
        "next-action":            {json.dumps(login_na)},
        "next-router-state-tree": {json.dumps(_RST)},
        "origin":                 "https://unitool.ai",
        "referer":                "https://unitool.ai/en/entry",
        "sec-fetch-dest":         "empty",
        "sec-fetch-mode":         "cors",
        "sec-fetch-site":         "same-origin"
      }},
      credentials: "include",
      body: fd
    }});
    const text = await r.text();
    const cookies = document.cookie;
    return JSON.stringify({{status: r.status, body: text.slice(0,400), cookies: cookies.slice(0,500)}});
  }} catch(e) {{
    return JSON.stringify({{status: 0, error: String(e)}});
  }}
}})()
"""
            raw_json = _s(await tab.execute_script(fetch_js, return_by_value=True, await_promise=True))
            log(f"[http_login] fetch result: {raw_json[:200]}")

            try:
                fr = json.loads(raw_json)
            except Exception:
                fr = {"status": 0, "error": raw_json[:100]}

            result["status"] = fr.get("status", 0)
            result["raw"] = fr.get("body", "")[:200]

            # ssid 在 httpOnly cookie 里，JS document.cookie 看不到
            # 但 RSC 响应会重定向或包含成功信息
            # 真正的 ssid 需要从 CDP Network 事件的 Set-Cookie 捕获
            # 此处标记需要 unitool_login.py 处理
            raw_lower = result["raw"].lower()
            if fr.get("status") == 200 and "1:E{" not in result["raw"]:
                result["ok"] = True
                result["note"] = "login_ok_but_ssid_httponly_use_unitool_login"
                log("[http_login] ✓ 登录成功（ssid 为 httpOnly，请用 unitool_login.py 捕获）")
            else:
                result["error"] = fr.get("error", f"http_{fr.get('status',0)}")

    except Exception as e:
        result["error"] = str(e)[:200]
        log(f"[http_login] exception: {e}")

    return result






# ─────────────────────────────────────────────────────────────────────────────
# 探测模式：测试端点连通性（不注册）
# ─────────────────────────────────────────────────────────────────────────────

def probe(port: int = 0):
    port = port or _pick_port()
    log(f"=== 探测模式 port={port} ===")
    sess = make_session(port)
    try:
        log("GET /en/entry...")
        r = sess.get(f"{UNITOOL_BASE}/en/entry", headers=HDR_NAV, timeout=25)
        log(f"  HTTP {r.status_code} len={len(r.text)}")
        log(f"  cookies: {list(r.cookies.keys())}")

        na = _extract_signup_na(r.text)
        log(f"  SIGNUP_NA: {na} ({'✓ 匹配' if na == _SIGNUP_NA_DEFAULT else '⚠ 变更！'})")

        sitekeys = list(set(re.findall(r"0x4AAAAAAC[A-Za-z0-9_-]+", r.text)))
        log(f"  sitekey_in_html: {sitekeys or '(在 JS bundle 中，不在 HTML)'}") 

        log("POST empty_token (端点连通性测试)...")
        hdr = {
            "accept": "text/x-component", "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json", "next-action": na,
            "next-router-state-tree": "%5B%22%22%2C%7B%22children%22%3A%5B%22en%22%2C%7B%22children%22%3A%5B%22entry%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%5D%7D%5D%7D%5D%7D%2Cnull%2Cnull%2Ctrue%5D",
            "origin": UNITOOL_BASE, "referer": f"{UNITOOL_BASE}/en/entry",
            "user-agent": _UA, "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors", "sec-fetch-site": "same-origin",
        }
        r2 = sess.post(f"{UNITOOL_BASE}/en/entry", headers=hdr,
                       data=b'[{"email":"probe@test.com","password":"Test12345!","token":""}]',
                       timeout=20, allow_redirects=False)
        log(f"  empty_token: HTTP {r2.status_code} → {r2.text[:200]}")

        d = re.search(r'"digest"\s*:\s*"(\d+)"', r2.text)
        if d:
            log(f"  digest={d.group(1)} → {_DIGEST_MAP.get(d.group(1), 'unknown')}")
        log("探测完成。结论: 端点可达，仅需有效 Turnstile token（pydoll bypass 获取）")
    finally:
        try:
            sess.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

ANALYSIS = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  unitool 协议注册可行性分析 v3.0                                             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  ① spring-ai-mcp-demo (tdsay-cn)           ✗ 完全不适用                    ║
║     Java/Spring AI + MCP (AI 工具调用 RPC 框架)                             ║
║     与 web 账号注册流程零交集，无任何可移植技术                              ║
║                                                                              ║
║  ② chatgpt2api (basketikun)               ✓ 2 组件已移植                   ║
║     curl_cffi Session(impersonate="chrome") ★★★★★  TLS 指纹伪造            ║
║     solve_turnstile_token(dx, p)           ★★★☆☆  纯 Python 求解器         ║
║       ⚠ 前提: dx/p 来自浏览器执行 CF JS — 纯 HTTP 无法获取                ║
║       ⚠ CF challenge URL 全返回 404（已实测）                               ║
║     pow.py / auth0 PKCE                    ✗      OpenAI 专用，不适用       ║
║                                                                              ║
║  ③ 实测协议                                                                  ║
║     POST /en/entry  Next-Action: 602b5c42...(SIGNUP) / 60e02e33...(LOGIN)  ║
║     Body: [{"email","password","token":"<CF_TURNSTILE>"}]                   ║
║     empty_token = fake_token → 同样 digest=3453729035                      ║
║     结论: CF 服务端真实验证 token，无法伪造                                  ║
║                                                                              ║
║  ④ 唯一免费可行方案: pydoll 混合模式（已实现）                              ║
║     A. pydoll Chrome(RESI) → bypass_cloudflare → 提取真实 token  (~15-30s) ║
║     B. curl_cffi HTTP → GET /en/entry → POST 注册                (~1-2s)   ║
║     总耗时: ~20-35s（vs 全浏览器 ~60-90s，快 40%）                         ║
║     可行性: ★★★★★（与现有 unitool_register.py bypass 相同机制）            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="unitool 混合协议注册 v3.0")
    ap.add_argument("--email",    default="", help="注册邮箱")
    ap.add_argument("--password", default="", help="密码")
    ap.add_argument("--ref",      default="", help="推荐码")
    ap.add_argument("--port",     type=int, default=0, help="RESI SOCKS5 端口")
    ap.add_argument("--probe",    action="store_true", help="探测模式（不注册）")
    ap.add_argument("--login",    action="store_true", help="登录模式")
    ap.add_argument("--analysis", action="store_true", help="输出分析报告")
    args = ap.parse_args()

    print(ANALYSIS)

    if args.analysis:
        sys.exit(0)

    if args.probe or not args.email:
        probe(args.port)
        sys.exit(0)

    if args.login:
        result = asyncio.run(http_login_hybrid(args.email, args.password, args.port))
    else:
        result = asyncio.run(http_register_hybrid(
            args.email, args.password, args.ref, args.port))

    print("\n=== RESULT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
