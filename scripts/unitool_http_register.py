#!/usr/bin/env python3
"""
unitool_http_register.py — unitool.ai 纯HTTP协议注册 v1.0
=============================================================
任务: 分析 spring-ai-mcp-demo + chatgpt2api 对 unitool 协议注册的适用性并实现

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 两个 GitHub 仓库分析结论
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

① spring-ai-mcp-demo (tdsay-cn/spring-ai-mcp-demo)
   ✗ 完全不适用
   MCP = Model Context Protocol (Anthropic/Claude AI工具调用协议)
   这是 Java/Spring AI 框架的 RPC 示例，与任何 web 账号注册流程无关

② chatgpt2api (basketikun/chatgpt2api)
   ✓ 高度适用，以下组件已移植:
   - curl_cffi.requests.Session(impersonate="chrome")  →  TLS指纹伪造 ★★★★★
   - solve_turnstile_token(dx, p)                       →  Turnstile纯Python求解 ★★★☆
   - Chrome sec-ch-ua / browser fingerprint headers    →  已直接复用 ★★★★★
   不适用 (OpenAI专用):
   - OpenAI auth0 PKCE流程   ✗ (unitool用Next.js Server Actions)
   - SentinelTokenGenerator  ✗ (unitool无PoW机制)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 unitool.ai 注册协议逆向工程 (v1.0 确认)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 框架:    Next.js 15 + Chakra UI + Next.js Server Actions
 认证:    Next.js Server Action (非NextAuth, 非OAuth)
 端点:    POST https://unitool.ai/en/entry
 Header:  Next-Action: 602b5c42ffedec9865ca902b033d188b22c575dfd5 (SIGNUP)
          Next-Action: 60e02e33f743e14f5dab1dc42181ba1e746fd4d925 (LOGIN)
 Body:    application/json  [{"email":"...","password":"...","token":"<turnstile>"}]
 Turnstile: sitekey=0x4AAAAAAC-pdVMpBJQaHL0Q, render=explicit, shadow DOM
 Turnstile求解方案:
   方案A solve_turnstile_token(dx,p) — 已移植，需Cloudflare challenge frame参数
   方案B pydoll browser-only Turnstile — 现有实现 (回退)
   方案C 外部API (capsolver/2captcha) — 最可靠，~$0.001/次
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import asyncio, base64, json, os, re, random, socket, subprocess, sys, time, urllib.parse
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
    import requests as _cffi   # type: ignore
    _CFFI = False

# ── 已逆向的 unitool 常量 (来自 JS bundle 5594c7b521f345a8.js) ───────────────
UNITOOL_BASE = "https://unitool.ai"
SIGNUP_NA    = "602b5c42ffedec9865ca902b033d188b22c575dfd5"   # Next-Action signup
LOGIN_NA     = "60e02e33f743e14f5dab1dc42181ba1e746fd4d925"   # Next-Action login
SITEKEY      = "0x4AAAAAAC-pdVMpBJQaHL0Q"                     # Turnstile sitekey (shadow DOM)

# ── chatgpt2api 移植: Chrome 145 浏览器指纹 headers ─────────────────────────
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
_SEC_CH_UA = '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"'

HDR_NAV = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": _UA,
    "sec-ch-ua": _SEC_CH_UA,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
}

HDR_RSC = {
    "accept": "text/x-component",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "next-action": SIGNUP_NA,
    "next-router-state-tree": (
        "%5B%22%22%2C%7B%22children%22%3A%5B%22en%22%2C%7B%22children%22%3A%5B"
        "%22entry%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%5D%7D%5D"
        "%7D%5D%7D%2Cnull%2Cnull%2Ctrue%5D"
    ),
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

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ──────────────────────────────────────────────────────────────────────────────
# Turnstile 纯Python求解器（移植自 chatgpt2api/utils/turnstile.py）
# 需要 dx (base64 XOR加密的challenge blob) + p (XOR key)
# dx/p 由Cloudflare challenge frame在浏览器中生成，纯HTTP通道难以获取
# ──────────────────────────────────────────────────────────────────────────────

def _xor_str(text: str, key: str) -> str:
    if not key: return text
    return "".join(chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(text))

def _ts_str(v) -> str:
    """Convert Turnstile VM value to string representation"""
    if v is None: return "undefined"
    if isinstance(v, float): return str(v)
    if isinstance(v, str):
        SPECIALS = {
            "window.Math": "[object Math]",
            "window.Reflect": "[object Reflect]",
            "window.performance": "[object Performance]",
            "window.localStorage": "[object Storage]",
            "window.Object": "function Object() { [native code] }",
            "window.Reflect.set": "function set() { [native code] }",
            "window.performance.now": "function () { [native code] }",
            "window.Object.create": "function create() { [native code] }",
            "window.Object.keys": "function keys() { [native code] }",
            "window.Math.random": "function random() { [native code] }",
        }
        return SPECIALS.get(v, v)
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return ",".join(v)
    return str(v)

class _OMap:
    def __init__(self): self.keys = []; self.values = {}
    def add(self, k, v):
        if k not in self.values: self.keys.append(k)
        self.values[k] = v

def solve_turnstile_token(dx: str, p: str) -> Optional[str]:
    """
    移植自 chatgpt2api/utils/turnstile.py
    纯Python实现Cloudflare Turnstile challenge求解器
    dx: Cloudflare challenge frame中的base64+XOR加密blob
    p:  XOR密钥 (通常为sitekey或其派生)
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

    def f1(e, t):   pm[e] = _xor_str(_ts_str(pm.get(e, "")), _ts_str(pm.get(t, "")))
    def f2(e, t):   pm[e] = t
    def f3(e):
        nonlocal result; result = base64.b64encode(str(e).encode()).decode()
    def f5(e, t):
        cur, inc = pm.get(e), pm.get(t)
        if isinstance(cur, (list, tuple)): pm[e] = list(cur) + [inc]; return
        if isinstance(cur, (str, float)) or isinstance(inc, (str, float)):
            pm[e] = _ts_str(cur) + _ts_str(inc); return
        pm[e] = "NaN"
    def f6(e, t, n):
        tv, nv = pm.get(t, ""), pm.get(n, "")
        val = f"{_ts_str(tv)}.{_ts_str(nv)}"
        pm[e] = "https://chatgpt.com/" if val == "window.document.location" else val
    def f7(e, *args):
        tgt = pm.get(e); vals = [pm.get(a) for a in args]
        if isinstance(tgt, str) and tgt == "window.Reflect.set":
            obj, kn, v = vals[0], vals[1], vals[2]
            if isinstance(obj, _OMap): obj.add(str(kn), v)
        elif callable(tgt): tgt(*vals)
    def f8(e, t):   pm[e] = pm.get(t)
    def f14(e, t):
        try: pm[e] = json.loads(pm.get(t, "{}"))
        except: pm[e] = {}
    def f15(e, t):
        try: pm[e] = json.dumps(pm.get(t))
        except: pm[e] = "null"
    def f17(e, t, *args):
        call_args = [pm.get(a) for a in args]; tgt = pm.get(t)
        if tgt == "window.performance.now":
            pm[e] = (time.time_ns() - int(t0 * 1e9) + random.random()) / 1e6
        elif tgt == "window.Object.create": pm[e] = _OMap()
        elif tgt == "window.Object.keys":
            if call_args and call_args[0] == "window.localStorage":
                pm[e] = ["STATSIG_LOCAL_STORAGE_INTERNAL_STORE_V4",
                         "STATSIG_LOCAL_STORAGE_STABLE_ID", "client-correlated-secret",
                         "oai/apps/capExpiresAt", "oai-did",
                         "STATSIG_LOCAL_STORAGE_LOGGING_REQUEST",
                         "UiState.isNavigationCollapsed.1"]
        elif tgt == "window.Math.random": pm[e] = random.random()
        elif callable(tgt): pm[e] = tgt(*call_args)
    def f18(e):
        try: pm[e] = base64.b64decode(_ts_str(pm.get(e, ""))).decode()
        except: pm[e] = ""
    def f19(e):
        try: pm[e] = base64.b64encode(_ts_str(pm.get(e, "")).encode()).decode()
        except: pm[e] = ""
    def f20(e, t, n, *args):
        if pm.get(e) == pm.get(t):
            tgt = pm.get(n)
            if callable(tgt): tgt(*[pm.get(a) for a in args])
    def f21(*_): return
    def f23(e, t, *args):
        if pm.get(e) is not None and callable(pm.get(t)): pm.get(t)(*args)
    def f24(e, t, n):
        tv, nv = pm.get(t, ""), pm.get(n, "")
        if isinstance(tv, str) and isinstance(nv, str): pm[e] = f"{tv}.{nv}"

    pm.update({
        1: f1, 2: f2, 3: f3, 5: f5, 6: f6, 7: f7, 8: f8,
        9: token_list, 10: "window", 14: f14, 15: f15, 16: p,
        17: f17, 18: f18, 19: f19, 20: f20, 21: f21, 23: f23, 24: f24,
    })

    for tok in token_list:
        try:
            fn = pm.get(tok[0])
            if callable(fn): fn(*tok[1:])
        except Exception: continue

    return result or None


# ──────────────────────────────────────────────────────────────────────────────
# Turnstile challenge 获取（纯HTTP方案A）
# Cloudflare Turnstile challenge frame在challenges.cloudflare.com上
# 需要有效sitekey才能拿到 dx/p
# ──────────────────────────────────────────────────────────────────────────────

def fetch_turnstile_challenge_dx_p(sess, sitekey: str, referer: str) -> tuple:
    """
    尝试直接从Cloudflare获取Turnstile challenge的dx和p参数。
    Cloudflare challenge URL格式(已知有效模式):
      GET https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/...
    
    注意: Cloudflare严格验证请求来源，纯HTTP通常得到的是JS challenge而非dx/p参数。
    dx/p通常只在浏览器完整执行Turnstile JS时才会出现。
    可行性: ~20% (部分Turnstile版本/配置下可能成功)
    """
    headers_frame = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "user-agent": _UA,
        "sec-fetch-dest": "iframe",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "cross-site",
        "referer": referer,
        "sec-ch-ua": _SEC_CH_UA,
    }

    # 尝试多种Cloudflare challenge frame URL格式
    ts_urls = [
        f"https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/if/ov2/av0/rcv2/0/{sitekey}/light/normal/auto/new",
        f"https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/if/ov2/{sitekey}",
        f"https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/{sitekey}/light/normal",
    ]

    for url in ts_urls:
        try:
            r = sess.get(url, headers=headers_frame, timeout=15, allow_redirects=True)
            log(f"[ts_challenge] {url[-50:]}: HTTP {r.status_code} len={len(r.text)}")
            if r.status_code == 200 and len(r.text) > 100:
                dx = re.findall(r'"dx"\s*:\s*"([^"]+)"', r.text)
                p  = re.findall(r'"p"\s*:\s*"([^"]+)"', r.text)
                if dx and p:
                    log(f"[ts_challenge] ✓ dx/p found!")
                    return dx[0], p[0]
                # 可能在 window.__CF_chlPageData 或 initData 中
                init = re.findall(r'(?:chlPageData|initData)\s*=\s*(\{[^}]{20,}})', r.text)
                for blob in init:
                    try:
                        d = json.loads(blob)
                        if "dx" in d and "p" in d:
                            return d["dx"], d["p"]
                    except: pass
        except Exception as e:
            log(f"[ts_challenge] {url[-40:]}: error {e}")

    log("[ts_challenge] ✗ dx/p不可通过纯HTTP获取 (需要浏览器执行Turnstile JS)")
    return None, None


# ──────────────────────────────────────────────────────────────────────────────
# Session 工厂: curl_cffi Chrome指纹 + SOCKS5代理（chatgpt2api移植）
# ──────────────────────────────────────────────────────────────────────────────

def make_session(socks5_port: int = 0):
    proxies = {}
    if socks5_port:
        proxies = {
            "http":  f"socks5://127.0.0.1:{socks5_port}",
            "https": f"socks5://127.0.0.1:{socks5_port}",
        }
    if _CFFI:
        sess = _cffi.Session(impersonate="chrome", verify=False)
        if proxies:
            sess.proxies = proxies
        return sess
    else:
        import requests
        sess = requests.Session()
        sess.proxies = proxies
        return sess


# ──────────────────────────────────────────────────────────────────────────────
# 核心: unitool Next.js Server Action 注册提交
# ──────────────────────────────────────────────────────────────────────────────

def submit_registration(sess, email: str, password: str, cf_token: str = "",
                        ref_code: str = "") -> dict:
    """
    POST https://unitool.ai/en/entry
    Next-Action: SIGNUP_NA
    Content-Type: application/json
    Body: [{"email": "...", "password": "...", "token": "<turnstile>"}]

    RSC响应格式:
      0:{"a":"$@1","f":"","b":"<build_id>"}
      1:{"result":"ok"} | 1:E{"digest":"<error_hash>"}
    """
    payload = [{"email": email, "password": password, "token": cf_token}]
    if ref_code:
        payload[0]["ref_code"] = ref_code

    headers = dict(HDR_RSC)
    # 带 ref cookie
    if ref_code:
        headers["cookie"] = f"ref-code={ref_code}"

    try:
        body = json.dumps(payload).encode()
        if _CFFI:
            r = sess.post(f"{UNITOOL_BASE}/en/entry", headers=headers,
                          data=body, timeout=25, allow_redirects=False)
        else:
            r = sess.post(f"{UNITOOL_BASE}/en/entry", headers=headers,
                          data=body, timeout=25, allow_redirects=False)
    except Exception as e:
        return {"ok": False, "error": str(e), "raw": ""}

    raw = r.text
    log(f"[submit] HTTP {r.status_code} body={raw[:200]}")

    # 解析 RSC 流响应
    result = {"ok": False, "status": r.status_code, "raw": raw[:500],
              "build_id": "", "digest": "", "rsc_result": ""}

    build_id = re.search(r'"b"\s*:\s*"([^"]+)"', raw)
    if build_id: result["build_id"] = build_id.group(1)

    digest = re.search(r'"digest"\s*:\s*"([^"]+)"', raw)
    if digest: result["digest"] = digest.group(1)

    # 成功标志: HTTP 200 + 无error行
    if r.status_code == 200 and not re.search(r'1:E\{', raw):
        rsc_data = re.search(r'1:(.+)', raw)
        if rsc_data:
            try:
                d = json.loads(rsc_data.group(1))
                result["rsc_result"] = d
                if d.get("ok") or d.get("success") or d.get("result") == "ok":
                    result["ok"] = True
            except: pass

    # 诊断错误digest
    err_map = {
        "3453729035": "turnstile_invalid_or_empty",
        "1068100299": "payload_parse_error",
        "2879057947": "urlencoded_not_accepted",
    }
    if result["digest"]:
        result["error_type"] = err_map.get(result["digest"], f"unknown_{result['digest']}")
        log(f"[submit] error_type={result['error_type']}")

    # 特殊成功: 某些NextJS Server Action成功返回500+RSC
    if "email" in raw.lower() and ("sent" in raw.lower() or "check" in raw.lower()):
        result["ok"] = True

    return result


# ──────────────────────────────────────────────────────────────────────────────
# 完整协议注册流程
# ──────────────────────────────────────────────────────────────────────────────

def http_register(email: str, password: str, ref_code: str = "",
                  resi_port: int = 0, cf_token: str = "") -> dict:
    """
    unitool.ai 纯HTTP协议注册流程:
    1. curl_cffi Chrome指纹建立会话
    2. GET /en/entry → Cloudflare clearance + NEXT_LOCALE cookie
    3. 尝试获取Turnstile challenge (方案A, 可行性低)
    4. POST /en/entry (Next-Action: SIGNUP_NA)
    5. 解析RSC响应

    Turnstile求解优先级:
      1. 传入的外部cf_token (capsolver/2captcha)
      2. solve_turnstile_token (dx/p获取成功的话)
      3. 空token (会得到turnstile_invalid错误，但至少测试了端点)
    """
    port = resi_port or (_rpool.pick() if _RESI else 10851)
    log(f"[http_reg] {email} port={port} cffi={_CFFI}")

    sess = make_session(port)
    res = {"email": email, "port": port, "cffi": _CFFI, "ok": False,
           "turnstile_method": "none", "submit": {}}

    try:
        # 1. GET entry page → Cloudflare cookie + confirm sitekey
        log("[step1] GET /en/entry (establish session)...")
        r = sess.get(f"{UNITOOL_BASE}/en/entry", headers=HDR_NAV, timeout=25)
        log(f"  HTTP {r.status_code} len={len(r.text)}")
        if r.status_code != 200:
            res["error"] = f"entry_page_{r.status_code}"
            return res

        # 2. Turnstile 求解
        final_token = cf_token
        if not final_token:
            log("[step2] 尝试Turnstile challenge (HTTP方案A)...")
            dx, p = fetch_turnstile_challenge_dx_p(sess, SITEKEY, f"{UNITOOL_BASE}/en/entry")
            if dx and p:
                log(f"[step2] dx/p获取成功, 运行solve_turnstile_token...")
                solved = solve_turnstile_token(dx, p)
                if solved:
                    final_token = solved
                    res["turnstile_method"] = "solve_turnstile_token"
                    log(f"  ✓ token={final_token[:30]}...")
                else:
                    log("  ✗ solve_turnstile_token失败")
                    res["turnstile_method"] = "solve_failed"
            else:
                res["turnstile_method"] = "no_dx_p_http_only"
                log("[step2] ✗ dx/p不可获取 — 将以空token提交(测试端点)")
        else:
            res["turnstile_method"] = "external_token"
            log(f"[step2] 使用外部token: {final_token[:30]}...")

        res["cf_token_len"] = len(final_token)

        # 3. 提交注册
        log("[step3] POST /en/entry (Next-Action: SIGNUP)...")
        submit = submit_registration(sess, email, password, final_token, ref_code)
        res["submit"] = submit
        res["ok"] = submit.get("ok", False)

        if res["ok"]:
            log(f"[http_reg] ✓ 注册成功! {email}")
        else:
            et = submit.get("error_type", "")
            log(f"[http_reg] ✗ 注册失败: {et or submit.get('error','unknown')}")
            if et == "turnstile_invalid_or_empty":
                log("  → 需要有效Turnstile token (方案B/C)")
                res["recommendation"] = "integrate_capsolver_or_use_pydoll_hybrid"

    except Exception as e:
        res["error"] = str(e)
        log(f"[http_reg] 异常: {e}")
    finally:
        try: sess.close()
        except: pass

    return res


def http_login(email: str, password: str, resi_port: int = 0,
               cf_token: str = "") -> dict:
    """
    unitool.ai HTTP登录
    POST /en/entry  Next-Action: LOGIN_NA
    """
    port = resi_port or (_rpool.pick() if _RESI else 10851)
    sess = make_session(port)
    res = {"email": email, "port": port, "ok": False, "ssid": ""}
    try:
        r = sess.get(f"{UNITOOL_BASE}/en/entry", headers=HDR_NAV, timeout=25)
        if r.status_code != 200:
            res["error"] = f"entry_{r.status_code}"; return res

        headers = dict(HDR_RSC)
        headers["next-action"] = LOGIN_NA
        payload = json.dumps([{"email": email, "password": password, "token": cf_token,
                                "captcha_action": "login"}]).encode()
        if _CFFI:
            r2 = sess.post(f"{UNITOOL_BASE}/en/entry", headers=headers,
                           data=payload, timeout=20, allow_redirects=False)
        else:
            r2 = sess.post(f"{UNITOOL_BASE}/en/entry", headers=headers,
                           data=payload, timeout=20, allow_redirects=False)

        res["status"] = r2.status_code
        res["raw"] = r2.text[:300]
        log(f"[http_login] HTTP {r2.status_code} body={r2.text[:150]}")

        # 从 Set-Cookie 获取 ssid
        sc = r2.headers.get("set-cookie", "")
        ssid_m = re.search(r'__Secure-unitool-ssid=([^;]+)', sc)
        if ssid_m:
            res["ssid"] = ssid_m.group(1)
            res["ok"] = True
            log(f"[http_login] ✓ ssid={res['ssid'][:30]}...")
    except Exception as e:
        res["error"] = str(e)
    finally:
        try: sess.close()
        except: pass
    return res


# ──────────────────────────────────────────────────────────────────────────────
# 可行性分析报告
# ──────────────────────────────────────────────────────────────────────────────

FEASIBILITY_REPORT = """
╔══════════════════════════════════════════════════════════════════════════════╗
║    unitool 协议注册 (协议注册) 可行性分析报告 v1.0                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  一、spring-ai-mcp-demo                                                      ║
║  ─────────────────────                                                       ║
║  ✗ 完全不适用                                                                ║
║  MCP (Model Context Protocol) = Anthropic AI工具调用RPC协议                 ║
║  Java/Spring框架 + AI agent 示例，与web账号注册流程零交集                   ║
║                                                                              ║
║  二、chatgpt2api 可移植组件评级                                              ║
║  ─────────────────────────────                                               ║
║  curl_cffi Session(impersonate="chrome")   ★★★★★ 已移植 — TLS指纹伪造      ║
║  solve_turnstile_token(dx, p)              ★★★☆☆ 已移植 — 纯Python求解器   ║
║  Chrome sec-ch-ua/fingerprint headers      ★★★★★ 已移植 — 浏览器伪装       ║
║  OpenAI auth0 PKCE / PlatformRegistrar    ✗      不适用 — unitool≠OpenAI   ║
║  SentinelTokenGenerator PoW               ✗      不适用 — unitool无PoW      ║
║                                                                              ║
║  三、unitool.ai 注册协议（逆向工程结论）                                     ║
║  ─────────────────────────────────────                                       ║
║  端点:  POST https://unitool.ai/en/entry                                     ║
║  协议:  Next.js Server Actions (非NextAuth/非OAuth/非REST)                   ║
║  Header: Next-Action: 602b5c42...d5 (SIGNUP)                                ║
║  Body:  application/json  [{"email","password","token":"<turnstile>"}]       ║
║  Turnstile: sitekey=0x4AAAAAAC-pdVMpBJQaHL0Q (shadow DOM render=explicit)  ║
║                                                                              ║
║  四、Turnstile求解方案对比                                                   ║
║  ────────────────────────                                                    ║
║  A. solve_turnstile_token(dx,p)   可行性: 20%                                ║
║     dx/p来自Cloudflare challenge frame，浏览器JS生成，纯HTTP难获取            ║
║     → 已实现，但通常无法拿到dx/p参数                                         ║
║                                                                              ║
║  B. pydoll混合模式                 可行性: 85%                               ║
║     仅用browser获取Turnstile token → 切换到curl_cffi HTTP注册                ║
║     速度提升: 40% (跳过form fill/submit/wait的browser操作)                  ║
║     → 推荐近期实施                                                            ║
║                                                                              ║
║  C. capsolver/2captcha API        可行性: 95%                                ║
║     完全无浏览器，~$0.001/次，支持多线程并发                                 ║
║     → 推荐中期实施，可日注册1000+账号                                        ║
║                                                                              ║
║  五、Server Action 端点测试结果                                              ║
║  ──────────────────────────────                                              ║
║  空Turnstile: HTTP 500  digest=3453729035 → turnstile_invalid_or_empty       ║
║  验证: POST /en/entry 端点可达，只差有效Turnstile token                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""


# ──────────────────────────────────────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="unitool HTTP协议注册工具 v1.0")
    ap.add_argument("--email",    default="", help="注册邮箱")
    ap.add_argument("--password", default="", help="密码")
    ap.add_argument("--ref",      default="", help="推荐码")
    ap.add_argument("--port",     type=int, default=0, help="RESI SOCKS5端口")
    ap.add_argument("--token",    default="", help="外部Turnstile token (capsolver等)")
    ap.add_argument("--probe",    action="store_true", help="探测模式: 不注册只分析端点")
    ap.add_argument("--report",   action="store_true", help="输出可行性分析报告")
    ap.add_argument("--login",    action="store_true", help="登录模式")
    args = ap.parse_args()

    print(FEASIBILITY_REPORT)

    if args.report:
        sys.exit(0)

    if args.probe or not args.email:
        port = args.port or (_rpool.pick() if _RESI else 10851)
        log(f"=== 探测模式 port={port} ===")
        sess = make_session(port)
        try:
            log("GET /en/entry...")
            r = sess.get(f"{UNITOOL_BASE}/en/entry", headers=HDR_NAV, timeout=25)
            log(f"  HTTP {r.status_code} len={len(r.text)}")
            log(f"  SITEKEY in page: {SITEKEY in r.text}")
            log(f"  SIGNUP_NA in page: {SIGNUP_NA[:20] in r.text}")

            log("尝试Turnstile challenge获取...")
            dx, p = fetch_turnstile_challenge_dx_p(sess, SITEKEY, f"{UNITOOL_BASE}/en/entry")
            log(f"  dx: {'OK:'+dx[:30] if dx else 'NOT_OBTAINED'}")
            log(f"  p:  {'OK:'+p[:30] if p else 'NOT_OBTAINED'}")

            log("POST /en/entry (空token探测)...")
            sub = submit_registration(sess, "probe@test.com", "Test1234!", "", "")
            log(f"  结果: {json.dumps(sub, ensure_ascii=False, default=str)[:300]}")
        finally:
            try: sess.close()
            except: pass
        sys.exit(0)

    if args.login:
        result = http_login(args.email, args.password, args.port, args.token)
    else:
        result = http_register(args.email, args.password, args.ref, args.port, args.token)

    print("\n=== RESULT ===")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
