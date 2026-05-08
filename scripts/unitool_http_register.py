#!/usr/bin/env python3
"""
unitool_http_register.py — 纯 HTTP 协议注册 unitool.ai（无浏览器）
====================================================================
分析来源:
  ① chatgpt2api (basketikun): curl_cffi TLS指纹, solve_turnstile_token, 浏览器header
  ② spring-ai-mcp-demo (tdsay-cn): Java Spring AI + MCP协议演示
      → 与unitool注册无关（MCP = Model Context Protocol，AI工具调用协议）

可行性结论:
  ● chatgpt2api 的 curl_cffi TLS指纹 + solve_turnstile_token ★ 可直接移植
  ● chatgpt2api 的 OpenAI auth0 PKCE 流程 ✗ 不适用（unitool用NextAuth.js）
  ● chatgpt2api 的 SentinelTokenGenerator PoW ✗ 不适用（unitool无PoW）
  ● spring-ai-mcp-demo ✗ 完全不适用（Java/Spring AI协议示例）

unitool注册流程（逆向工程）:
  1. GET /api/auth/csrf  → csrfToken
  2. Cloudflare Turnstile 求解 → cf_token (本文件核心难点)
  3. POST /api/auth/callback/credentials {email,password,csrfToken,cf_token}
     或 POST /api/auth/signin/email
  4. 等邮件 verify 链接
  5. curl verify URL → ssid cookie

Turnstile 求解三种方案:
  A) 纯HTTP solve_turnstile_token(dx,p): 需要Cloudflare challenge frame中的dx/p参数
     → dx/p仅在浏览器执行Turnstile JS时可获取，纯HTTP难以获取
     → 可行性: 低（除非Cloudflare challenge endpoint暴露这些参数）
  B) 轻量级CDP (Chrome DevTools Protocol) 仅用于Turnstile，其余HTTP
     → 可行性: 高（复用现有pydoll+Chrome，但只用一次browser call）
  C) 外部Turnstile求解服务 (2captcha/capsolver/nocaptchaai)
     → 可行性: 高但需付费

本文件实现: 方案A探测 + 方案B回退（使用现有RESI池）
"""

import asyncio, base64, hashlib, json, os, random, re, socket, subprocess, sys, time
import urllib.request, urllib.parse
from typing import Optional

sys.path.insert(0, "/data/Toolkit/scripts")

# ─── 复用现有模块 ──────────────────────────────────────────────────────────────
try:
    import resi_pool as _rpool
    RESI_AVAILABLE = True
except ImportError:
    RESI_AVAILABLE = False
    print("[WARN] resi_pool not available", flush=True)

# ─── curl_cffi TLS指纹（来自 chatgpt2api）────────────────────────────────────
try:
    from curl_cffi import requests as _cffi_req
    CFFI_AVAILABLE = True
except ImportError:
    import requests as _cffi_req
    CFFI_AVAILABLE = False
    print("[WARN] curl_cffi not available, falling back to requests", flush=True)

UNITOOL_BASE = "https://unitool.ai"

# chatgpt2api 移植: Chrome 145 浏览器指纹 headers
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
SEC_CH_UA = '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"'
SEC_CH_UA_FULL = '"Chromium";v="145.0.0.0", "Not:A-Brand";v="99.0.0.0", "Google Chrome";v="145.0.0.0"'

COMMON_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": UNITOOL_BASE,
    "referer": f"{UNITOOL_BASE}/en/entry",
    "user-agent": UA,
    "sec-ch-ua": SEC_CH_UA,
    "sec-ch-ua-arch": '"x86_64"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-full-version-list": SEC_CH_UA_FULL,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"10.0.0"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "priority": "u=1, i",
}

NAV_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": UA,
    "sec-ch-ua": SEC_CH_UA,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─── Turnstile 纯Python求解器（移植自 chatgpt2api/utils/turnstile.py）────────

class _OMap:
    """Ordered key-value map (Turnstile VM用)"""
    def __init__(self):
        self.keys = []
        self.values = {}
    def add(self, k, v):
        if k not in self.values: self.keys.append(k)
        self.values[k] = v

def _ts_str(v):
    if v is None: return "undefined"
    if isinstance(v, float): return str(v)
    if isinstance(v, str):
        SPECIAL = {
            "window.Math": "[object Math]", "window.Reflect": "[object Reflect]",
            "window.performance": "[object Performance]", "window.localStorage": "[object Storage]",
            "window.Object": "function Object() { [native code] }",
            "window.Reflect.set": "function set() { [native code] }",
            "window.performance.now": "function () { [native code] }",
            "window.Object.create": "function create() { [native code] }",
            "window.Object.keys": "function keys() { [native code] }",
            "window.Math.random": "function random() { [native code] }",
        }
        return SPECIAL.get(v, v)
    if isinstance(v, list) and all(isinstance(x, str) for x in v): return ",".join(v)
    return str(v)

def _xor_str(text, key):
    if not key: return text
    return "".join(chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(text))

def solve_turnstile_token(dx: str, p: str) -> Optional[str]:
    """
    移植自 chatgpt2api/utils/turnstile.py
    dx: base64编码的XOR加密token列表
    p: XOR key (通常是Turnstile sitekey或其派生)
    返回: base64编码的求解token, 或 None
    """
    try:
        decoded = base64.b64decode(dx).decode()
        token_list = json.loads(_xor_str(decoded, p))
    except Exception as e:
        log(f"[turnstile] decode error: {e}")
        return None

    pm = {}
    t0 = time.time()
    result = ""

    def f1(e, t): pm[e] = _xor_str(_ts_str(pm[e]), _ts_str(pm[t]))
    def f2(e, t): pm[e] = t
    def f3(e):
        nonlocal result; result = base64.b64encode(e.encode()).decode()
    def f5(e, t):
        cur, inc = pm[e], pm[t]
        if isinstance(cur, (list, tuple)): pm[e] = list(cur) + [inc]; return
        if isinstance(cur, (str, float)) or isinstance(inc, (str, float)):
            pm[e] = _ts_str(cur) + _ts_str(inc); return
        pm[e] = "NaN"
    def f6(e, t, n):
        tv, nv = pm[t], pm[n]
        if isinstance(tv, str) and isinstance(nv, str):
            val = f"{tv}.{nv}"
            pm[e] = "https://chatgpt.com/" if val == "window.document.location" else val
    def f7(e, *args):
        tgt = pm[e]; vals = [pm[a] for a in args]
        if isinstance(tgt, str) and tgt == "window.Reflect.set":
            obj, kn, v = vals; obj.add(str(kn), v)
        elif callable(tgt): tgt(*vals)
    def f8(e, t): pm[e] = pm[t]
    def f14(e, t): pm[e] = json.loads(pm[t])
    def f15(e, t): pm[e] = json.dumps(pm[t])
    def f17(e, t, *args):
        call_args = [pm[a] for a in args]; tgt = pm[t]
        if tgt == "window.performance.now":
            pm[e] = (time.time_ns() - int(t0 * 1e9) + random.random()) / 1e6
        elif tgt == "window.Object.create": pm[e] = _OMap()
        elif tgt == "window.Object.keys":
            if call_args and call_args[0] == "window.localStorage":
                pm[e] = ["STATSIG_LOCAL_STORAGE_INTERNAL_STORE_V4", "STATSIG_LOCAL_STORAGE_STABLE_ID",
                         "client-correlated-secret", "oai/apps/capExpiresAt", "oai-did",
                         "STATSIG_LOCAL_STORAGE_LOGGING_REQUEST", "UiState.isNavigationCollapsed.1"]
        elif tgt == "window.Math.random": pm[e] = random.random()
        elif callable(tgt): pm[e] = tgt(*call_args)
    def f18(e): pm[e] = base64.b64decode(_ts_str(pm[e])).decode()
    def f19(e): pm[e] = base64.b64encode(_ts_str(pm[e]).encode()).decode()
    def f20(e, t, n, *args):
        if pm[e] == pm[t]:
            tgt = pm[n]
            if callable(tgt): tgt(*[pm[a] for a in args])
    def f21(*_): return
    def f23(e, t, *args):
        if pm[e] is not None and callable(pm[t]): pm[t](*args)
    def f24(e, t, n):
        tv, nv = pm[t], pm[n]
        if isinstance(tv, str) and isinstance(nv, str): pm[e] = f"{tv}.{nv}"

    pm.update({1:f1, 2:f2, 3:f3, 5:f5, 6:f6, 7:f7, 8:f8,
               9:token_list, 10:"window", 14:f14, 15:f15, 16:p,
               17:f17, 18:f18, 19:f19, 20:f20, 21:f21, 23:f23, 24:f24})

    for tok in token_list:
        try:
            fn = pm.get(tok[0])
            if callable(fn): fn(*tok[1:])
        except Exception: continue

    return result or None


# ─── Session 创建（curl_cffi Chrome指纹）────────────────────────────────────

def make_session(socks5_port: int = 0):
    """curl_cffi Session with Chrome impersonation + optional SOCKS5 proxy"""
    proxies = {}
    if socks5_port:
        proxies = {"http": f"socks5://127.0.0.1:{socks5_port}",
                   "https": f"socks5://127.0.0.1:{socks5_port}"}
    if CFFI_AVAILABLE:
        return _cffi_req.Session(impersonate="chrome", verify=False, proxies=proxies)
    else:
        import requests
        sess = requests.Session()
        if proxies: sess.proxies.update(proxies)
        sess.verify = False
        return sess


# ─── Step 1: Turnstile sitekey 提取（从 JS bundle）─────────────────────────

def extract_turnstile_sitekey(sess, entry_html: str = "") -> str:
    """从unitool.ai JS bundle 提取 Cloudflare Turnstile sitekey"""
    if not entry_html:
        try:
            r = sess.get(f"{UNITOOL_BASE}/en/entry", headers=NAV_HEADERS, timeout=15)
            entry_html = r.text
        except Exception as e:
            log(f"[sitekey] entry page fetch error: {e}")
            return ""

    # Turnstile sitekey = 0x4AAAAAAA... 格式
    # 从 JS bundles 中搜索
    js_urls = re.findall(r'/_next/static/chunks/([a-f0-9]+)\.js', entry_html)
    log(f"[sitekey] checking {len(js_urls)} JS bundles for sitekey")

    for js_id in js_urls[:30]:  # 限制30个避免超时
        try:
            url = f"{UNITOOL_BASE}/_next/static/chunks/{js_id}.js"
            r = sess.get(url, headers=NAV_HEADERS, timeout=15)
            txt = r.text
            # 查找 0x4 开头的hex (Turnstile sitekey格式)
            m = re.findall(r'0x4[A-Fa-f0-9]{14,}', txt)
            for sk in m:
                if len(sk) >= 16:
                    log(f"[sitekey] FOUND in {js_id}.js: {sk}")
                    return sk
            # 也查找字符串格式的sitekey
            m2 = re.findall(r'["\']sitekey["\']\s*:\s*["\']([^"\']+)["\']', txt)
            for sk in m2:
                log(f"[sitekey] FOUND (string) in {js_id}.js: {sk}")
                return sk
        except Exception:
            continue

    log("[sitekey] NOT FOUND in JS bundles")
    return ""


# ─── Step 2: Turnstile challenge 获取（方案A: 直接HTTP）─────────────────────

def fetch_turnstile_challenge(sess, sitekey: str) -> tuple:
    """
    尝试从 Cloudflare Turnstile challenge infrastructure 获取 dx/p 参数。
    这是 chatgpt2api solve_turnstile_token 所需的输入。

    Cloudflare Turnstile challenge frame URL 格式:
    GET https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/if/ov2/av0/rcv2/0/{sitekey}/{lang}/{theme}/new
    """
    if not sitekey:
        return None, None

    # 尝试获取challenge frame
    challenge_url = (
        f"https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/if/ov2/av0/rcv2/"
        f"0/{sitekey}/light/fbE/new"
    )
    log(f"[turnstile] fetching challenge frame: {sitekey[:20]}...")
    try:
        r = sess.get(challenge_url, headers={
            **NAV_HEADERS,
            "referer": f"{UNITOOL_BASE}/en/entry",
        }, timeout=20)

        html = r.text
        log(f"[turnstile] challenge frame: HTTP {r.status_code} len={len(html)}")

        # 查找 dx 和 p 参数
        dx_match = re.search(r'"dx"\s*:\s*"([^"]+)"', html)
        p_match = re.search(r'"p"\s*:\s*"([^"]+)"', html)

        if dx_match and p_match:
            log(f"[turnstile] ✓ dx/p found!")
            return dx_match.group(1), p_match.group(1)

        # 也查找 initData 格式
        init_match = re.search(r'initData\s*=\s*(\{[^}]+\})', html)
        if init_match:
            try:
                d = json.loads(init_match.group(1))
                if "dx" in d and "p" in d:
                    return d["dx"], d["p"]
            except: pass

        log(f"[turnstile] dx/p not in response (first 500): {html[:500]}")
        return None, None
    except Exception as e:
        log(f"[turnstile] challenge fetch error: {e}")
        return None, None


# ─── Step 3: NextAuth CSRF token ────────────────────────────────────────────

def fetch_csrf_token(sess) -> str:
    """GET /api/auth/csrf → csrfToken"""
    try:
        r = sess.get(f"{UNITOOL_BASE}/api/auth/csrf",
                     headers={**COMMON_HEADERS, "accept": "application/json"}, timeout=10)
        data = r.json()
        token = data.get("csrfToken", "")
        log(f"[csrf] token: {token[:20]}... (len={len(token)})")
        return token
    except Exception as e:
        log(f"[csrf] error: {e}")
        return ""


def fetch_providers(sess) -> dict:
    """GET /api/auth/providers → available auth providers"""
    try:
        r = sess.get(f"{UNITOOL_BASE}/api/auth/providers",
                     headers={**COMMON_HEADERS, "accept": "application/json"}, timeout=10)
        return r.json()
    except Exception as e:
        log(f"[providers] error: {e}")
        return {}


# ─── Step 4: 注册表单提交（HTTP直接 POST）───────────────────────────────────

def register_via_http(sess, email: str, password: str, csrf_token: str,
                      cf_token: str = "", ref_code: str = "") -> dict:
    """
    直接 POST 注册请求到 NextAuth 端点。

    unitool 使用 NextAuth.js credentials provider:
      POST /api/auth/callback/credentials
    或 custom signup endpoint。

    关键发现: unitool_register.py 的 SIGNUP_NA 常量
    = "602b5c42ffedec9865ca902b033d188b22c575dfd5"
    来自 JS bundle 5594c7b521f345a8.js，这是 NextAuth action nonce。
    """
    SIGNUP_NA = "602b5c42ffedec9865ca902b033d188b22c575dfd5"

    results = {}

    # 方案1: NextAuth credentials callback
    endpoints = [
        ("POST", f"{UNITOOL_BASE}/api/auth/callback/credentials",
         {"email": email, "password": password, "csrfToken": csrf_token,
          "cf-turnstile-response": cf_token, "callbackUrl": f"{UNITOOL_BASE}/en/entry"}),

        # 方案2: 自定义signup endpoint（如果存在）
        ("POST", f"{UNITOOL_BASE}/api/auth/signup",
         {"email": email, "password": password, "token": cf_token}),

        # 方案3: NextAuth email signIn
        ("POST", f"{UNITOOL_BASE}/api/auth/signin/email",
         {"email": email, "csrfToken": csrf_token, "callbackUrl": f"{UNITOOL_BASE}/en/entry",
          "cf-turnstile-response": cf_token}),

        # 方案4: 带 nonce 的注册
        ("POST", f"{UNITOOL_BASE}/api/auth/callback/credentials",
         {"email": email, "password": password, "csrfToken": csrf_token,
          "cf-turnstile-response": cf_token, "na": SIGNUP_NA,
          "callbackUrl": f"{UNITOOL_BASE}/en/entry",
          "redirect": "false"}),
    ]

    for method, url, payload in endpoints:
        try:
            hdrs = {**COMMON_HEADERS,
                    "content-type": "application/x-www-form-urlencoded",
                    "accept": "application/json, */*",
                    "referer": f"{UNITOOL_BASE}/en/entry"}
            # NextAuth 用 form-urlencoded
            body = urllib.parse.urlencode(payload)
            log(f"[register] POST {url.replace(UNITOOL_BASE, '')} payload={list(payload.keys())}")

            if CFFI_AVAILABLE:
                r = sess.post(url, content=body.encode(), headers=hdrs, timeout=20,
                              allow_redirects=False)
            else:
                r = sess.post(url, data=body, headers=hdrs, timeout=20,
                              allow_redirects=False)

            loc = r.headers.get("location", "")
            log(f"  → HTTP {r.status_code} location={loc[:80]} body={r.text[:200]}")

            results[url] = {"status": r.status_code, "location": loc, "body": r.text[:300]}

            # 成功指标: 302跳转到非error URL, 或JSON包含成功标志
            if r.status_code in (200, 302, 307):
                if "error" not in loc.lower() and "email" not in loc.lower().replace("email",""):
                    if r.status_code == 302 and loc:
                        results["success_hint"] = f"redirect:{loc}"
                try:
                    d = r.json()
                    if isinstance(d, dict):
                        results[url]["json"] = d
                except: pass
        except Exception as e:
            log(f"  → error: {e}")
            results[url] = {"error": str(e)}

    return results


# ─── Step 5: 完整协议注册流程（HTTP模式）────────────────────────────────────

def http_register_flow(email: str, password: str, ref_code: str = "",
                        resi_port: int = 0) -> dict:
    """
    完整协议注册流程（无浏览器）:
    1. 获取CSRF token
    2. 提取 Turnstile sitekey
    3. 尝试获取 Turnstile challenge (dx/p)
    4. 求解 Turnstile (或跳过token)
    5. 提交注册表单
    """
    log(f"\n{'='*60}")
    log(f"[http_reg] 开始: {email} port={resi_port}")

    port = resi_port or (_rpool.pick() if RESI_AVAILABLE else 10851)
    sess = make_session(port)

    result = {
        "email": email, "port": port, "cffi": CFFI_AVAILABLE,
        "csrf": "", "sitekey": "", "turnstile_method": "",
        "cf_token": "", "register_results": {}
    }

    try:
        # 1. 获取 CSRF token
        csrf = fetch_csrf_token(sess)
        result["csrf"] = csrf

        # 2. 获取 providers（了解unitool支持哪些认证方式）
        providers = fetch_providers(sess)
        log(f"[providers] {list(providers.keys())}")
        result["providers"] = list(providers.keys())

        # 3. 提取 Turnstile sitekey
        log("[step3] 提取 Turnstile sitekey...")
        entry_r = sess.get(f"{UNITOOL_BASE}/en/entry", headers=NAV_HEADERS, timeout=15)
        entry_html = entry_r.text
        sitekey = extract_turnstile_sitekey(sess, entry_html)
        result["sitekey"] = sitekey

        # 4. 尝试 Turnstile 求解
        cf_token = ""
        if sitekey:
            log(f"[step4] 尝试获取 Turnstile challenge (sitekey={sitekey[:20]}...)...")
            dx, p = fetch_turnstile_challenge(sess, sitekey)
            if dx and p:
                log(f"[step4] 求解 Turnstile dx={dx[:20]}... p={p[:20]}...")
                cf_token = solve_turnstile_token(dx, p) or ""
                if cf_token:
                    log(f"[step4] ✓ Turnstile solved: token={cf_token[:30]}...")
                    result["turnstile_method"] = "solve_turnstile_token"
                else:
                    log("[step4] ✗ solve_turnstile_token failed")
                    result["turnstile_method"] = "solve_failed"
            else:
                log("[step4] ✗ 无法获取 dx/p (需要浏览器执行Cloudflare JS)")
                result["turnstile_method"] = "no_dx_p"
        else:
            log("[step4] 跳过 (sitekey未找到)")
            result["turnstile_method"] = "no_sitekey"

        result["cf_token"] = cf_token[:30] if cf_token else ""

        # 5. 提交注册（即使没有valid Turnstile token也尝试，记录响应）
        log(f"[step5] 提交注册 cf_token={'YES' if cf_token else 'EMPTY'}...")
        reg_results = register_via_http(sess, email, password, csrf, cf_token, ref_code)
        result["register_results"] = reg_results

        # 分析注册结果
        for k, v in reg_results.items():
            if isinstance(v, dict):
                status = v.get("status", 0)
                loc = v.get("location", "")
                body = v.get("body", "")
                if status == 302 and loc and "error" not in loc:
                    log(f"[result] ✓ 可能成功! {k} → {loc}")
                    result["outcome"] = f"redirect_success:{loc}"
                elif "check your email" in body.lower() or "sent link" in body.lower():
                    log(f"[result] ✓ 邮件已发送!")
                    result["outcome"] = "email_sent"
                elif "captcha" in body.lower() or "turnstile" in body.lower():
                    log(f"[result] ✗ Turnstile验证失败 (需要有效token)")
                    result["outcome"] = "turnstile_required"
                elif "already" in body.lower():
                    log(f"[result] ✗ 邮箱已注册")
                    result["outcome"] = "already_registered"

    except Exception as e:
        log(f"[http_reg] 异常: {e}")
        result["error"] = str(e)
    finally:
        try: sess.close()
        except: pass

    return result


# ─── 可行性分析报告 ──────────────────────────────────────────────────────────

def feasibility_report():
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║        unitool 协议注册可行性分析报告                                        ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  ① spring-ai-mcp-demo (Java Spring AI + MCP协议)                            ║
║  ─────────────────────────────────────────────                               ║
║  ✗ 完全不适用                                                                ║
║    • MCP = Model Context Protocol (Anthropic AI工具调用协议)                 ║
║    • 是Java/Spring框架示例，演示AI Agent调用工具的RPC协议                    ║
║    • 与unitool.ai账号注册流程毫无关联                                        ║
║    • 无法借鉴任何技术到注册流程                                              ║
║                                                                              ║
║  ② chatgpt2api (Python FastAPI账号池)                                        ║
║  ─────────────────────────────────────                                       ║
║  ✓ 高度相关，以下组件可直接移植:                                            ║
║                                                                              ║
║  组件                          适用性   说明                                 ║
║  ─────────────────────────     ───────  ────────────────────                 ║
║  curl_cffi TLS指纹             ★★★★★   可直接用于unitool所有HTTP请求        ║
║  solve_turnstile_token(dx,p)   ★★★☆☆   已移植，但需要dx/p参数               ║
║  Chrome浏览器headers           ★★★★★   直接复用sec-ch-ua等指纹              ║
║  OpenAI auth0 PKCE流程         ✗        unitool用NextAuth.js，不是auth0      ║
║  SentinelTokenGenerator PoW    ✗        OpenAI专用，unitool无PoW需求         ║
║  PlatformRegistrar             ✗        OpenAI注册流程，结构不同             ║
║  mail_provider (OTP接收)       ★★★★☆   可改造用于接收验证邮件               ║
║                                                                              ║
║  ③ unitool.ai 注册流程逆向                                                   ║
║  ──────────────────────────                                                  ║
║  框架: Next.js + Chakra UI + NextAuth.js + Cloudflare Turnstile             ║
║  认证: NextAuth email magic link (非OTP, 非OAuth PKCE)                      ║
║  Turnstile: render=explicit, sitekey在JS bundle中                           ║
║                                                                              ║
║  ④ Turnstile求解方案对比                                                     ║
║  ──────────────────────────                                                  ║
║  方案A: solve_turnstile_token(dx,p)                                          ║
║    • 需要Cloudflare challenge frame返回dx/p                                  ║
║    • dx/p仅在浏览器执行Turnstile JS时生成                                   ║
║    • 纯HTTP无法获取 → 可行性: 低 (20%)                                      ║
║                                                                              ║
║  方案B: CDP混合模式 (仅Turnstile用browser, 其余HTTP)                         ║
║    • pydoll仅用于bypass Turnstile获取token                                  ║
║    • 获取token后切换到curl_cffi HTTP流程                                     ║
║    • 速度: 比全浏览器快30-50%                                                ║
║    • 可行性: 高 (85%)                                                        ║
║                                                                              ║
║  方案C: 外部Turnstile求解服务                                                ║
║    • 2captcha/capsolver/nocaptchaai API                                      ║
║    • 无需浏览器，纯HTTP                                                      ║
║    • 成本: ~$0.001/次 = 1000次约$1                                           ║
║    • 可行性: 高 (95%)，需付费                                                ║
║                                                                              ║
║  ⑤ 推荐实施方案                                                              ║
║  ──────────────                                                              ║
║  近期: 方案B - CDP混合模式                                                   ║
║    pydoll获取Turnstile token → curl_cffi提交注册 → Graph API收邮件          ║
║    预计速度提升: 40% (减少browser操作)                                       ║
║                                                                              ║
║  中期: 方案C - 集成capsolver API                                             ║
║    完全无浏览器，纯HTTP + RESI SOCKS5                                        ║
║    预计速度提升: 70% + 可扩展至多线程                                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")


# ─── 测试入口 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--analysis", action="store_true", help="仅输出分析报告")
    ap.add_argument("--probe", action="store_true", help="探测unitool端点（不注册）")
    args = ap.parse_args()

    feasibility_report()

    if args.analysis:
        sys.exit(0)

    if args.probe or not args.email:
        log("=== 探测模式: unitool.ai 端点分析 ===")
        port = args.port or ((_rpool.pick() if RESI_AVAILABLE else 10851))
        log(f"使用 RESI 端口: {port}")
        sess = make_session(port)
        try:
            # CSRF
            csrf = fetch_csrf_token(sess)
            # providers
            providers = fetch_providers(sess)
            log(f"providers: {json.dumps(providers, indent=2, ensure_ascii=False)[:500]}")
            # sitekey
            log("提取 Turnstile sitekey...")
            entry_r = sess.get(f"{UNITOOL_BASE}/en/entry", headers=NAV_HEADERS, timeout=15)
            sitekey = extract_turnstile_sitekey(sess, entry_r.text)
            log(f"sitekey: {sitekey or 'NOT FOUND'}")
            if sitekey:
                dx, p = fetch_turnstile_challenge(sess, sitekey)
                log(f"dx: {dx[:30] if dx else 'NOT OBTAINED'}")
                log(f"p:  {p[:30] if p else 'NOT OBTAINED'}")
        finally:
            try: sess.close()
            except: pass
        sys.exit(0)

    # 完整注册流程
    result = http_register_flow(args.email, args.password, port=args.port)
    print("\n=== RESULT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
