#!/usr/bin/env python3
"""
click_verify_link.py — 读取邮件正文，提取 Reseek 验证链接，用 patchright 访问。
用法: python3 click_verify_link.py '<json>'
JSON: { "token": "...", "message_id": "...", "verify_url": "" }
"""
import sys, json, re, os, urllib.request, urllib.parse, html as html_lib

if len(sys.argv) < 2:
    print(json.dumps({"success": False, "error": "缺少参数"}))
    sys.exit(1)

data = json.loads(sys.argv[1])
token      = data.get("token", "")
message_id = data.get("message_id", "")
verify_url = data.get("verify_url", "")
verify_url = html_lib.unescape(verify_url) if verify_url else verify_url
# v7.78j — 去掉 "http://127.0.0.1:8091" 默认 fallback。该端口长期无 listener,
# 而 Graph API 从 VPS 直连 graph.microsoft.com 完全可达 (无需 IP-anchoring)。
# 仅当上层显式传入或 env 设置时才用代理。
proxy_url = data.get("proxy") or os.environ.get("MICROSOFT_BROWSER_PROXY") or os.environ.get("OUTLOOK_BROWSER_PROXY") or os.environ.get("MICROSOFT_HTTP_PROXY") or os.environ.get("OUTLOOK_HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""

# v7.85 — chromium 访问 verify link (replit.com / reseek.com) 时必须走 WARP,
# 否则注册时 chromium 走 socks5://127.0.0.1:40000 (CF WARP, 出口 104.28.x.x),
# 但 verify 点击却走 VPS 公网 (45.205.27.69), 让 Replit 反爬看到
# "注册 IP ≠ 验证 IP" 这个极强异常信号 → 账号被风控 + VPS IP 被标黑.
# 上面的 proxy_url 是给 Graph API (open_url) 读邮件用的 connect-proxy /
# outlook 通道, chromium 不能复用. 这里独立一个 replit_browser_proxy.
replit_browser_proxy = (
    data.get("replit_browser_proxy")
    or os.environ.get("REPLIT_BROWSER_PROXY")
    or os.environ.get("BROWSER_PROXY")
    or "socks5://127.0.0.1:40000"
)

VERIFY_KWS   = ("verify", "confirm", "activate", "validation", "email-action",
                "verificationToken", "emailVerification", "signup_success",
                "token=", "confirmation")
RESEEK_HOSTS = ("replit.com", "reseek.com", "replit.dev")


def _read_connect_proxy_token_from_proc():
    """从 /proc 中读取 http_connect_proxy.py 实际使用的 token"""
    import glob
    for cmdline_path in glob.glob("/proc/*/cmdline"):
        try:
            cmd = open(cmdline_path, "rb").read().decode("utf-8", errors="replace")
            if "http_connect_proxy" not in cmd:
                continue
            pid_dir = cmdline_path.rsplit("/", 1)[0]
            env_raw = open(f"{pid_dir}/environ", "rb").read().decode("utf-8", errors="replace")
            env = dict(e.split("=", 1) for e in env_raw.split("\x00") if "=" in e)
            tok = env.get("CONNECT_PROXY_TOKEN", "") or env.get("SESSION_SECRET", "")
            if tok:
                return tok
        except Exception:
            pass
    return ""


def _with_local_proxy_auth(proxy):
    if not proxy:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", proxy):
        proxy = "http://" + proxy
    parsed = urllib.parse.urlparse(proxy)
    if parsed.scheme.startswith("socks"):
        return proxy
    if parsed.username or parsed.password:
        return proxy
    if parsed.hostname in ("127.0.0.1", "localhost") and str(parsed.port or "") == "8091":
        token = (os.environ.get("CONNECT_PROXY_TOKEN") or
                 os.environ.get("SESSION_SECRET") or
                 _read_connect_proxy_token_from_proc() or
                 "replproxy2024")
        host = parsed.hostname or "127.0.0.1"
        netloc = ":" + urllib.parse.quote(token, safe="") + "@" + host
        if parsed.port:
            netloc += f":{parsed.port}"
        return urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return proxy

proxy_url = _with_local_proxy_auth(proxy_url)

# 把 HTTP(S)_PROXY 从环境中清除：
#   - Graph API 调用走自定义 open_url()，已显式带 Proxy-Authorization
#   - Firebase REST / patchright 直连即可（VPS 出口可达 googleapis.com 与 replit.com）
#   - 留着 env 反而让 urllib/Chromium 走代理但不会带 auth → 407
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)


def open_url(req, timeout):
    """发起 HTTP/HTTPS 请求，HTTPS 通过手动 CONNECT 隧道（正确携带 Proxy-Authorization）"""
    import http.client, base64, ssl as _ssl
    url = req.full_url
    headers = dict(req.headers)
    parsed_url = urllib.parse.urlparse(url)
    is_https = parsed_url.scheme == "https"
    host = parsed_url.hostname
    port = parsed_url.port or (443 if is_https else 80)
    path = parsed_url.path or "/"
    if parsed_url.query:
        path += "?" + parsed_url.query

    if proxy_url and not proxy_url.startswith("socks"):
        parsed_proxy = urllib.parse.urlparse(proxy_url)
        proxy_host = parsed_proxy.hostname or "127.0.0.1"
        proxy_port = parsed_proxy.port or 80
        proxy_user = urllib.parse.unquote(parsed_proxy.username or "")
        proxy_pass = urllib.parse.unquote(parsed_proxy.password or "")
        if is_https:
            conn = http.client.HTTPConnection(proxy_host, proxy_port, timeout=timeout)
            tunnel_headers = {}
            if proxy_pass or proxy_user:
                cred = base64.b64encode(f"{proxy_user}:{proxy_pass}".encode()).decode()
                tunnel_headers["Proxy-Authorization"] = f"Basic {cred}"
            conn.set_tunnel(host, port, headers=tunnel_headers)
            conn.connect()
            ssl_ctx = _ssl.create_default_context()
            conn.sock = ssl_ctx.wrap_socket(conn.sock, server_hostname=host)
        else:
            conn = http.client.HTTPConnection(proxy_host, proxy_port, timeout=timeout)
            path = url
            if proxy_pass or proxy_user:
                cred = base64.b64encode(f"{proxy_user}:{proxy_pass}".encode()).decode()
                headers["Proxy-Authorization"] = f"Basic {cred}"
    else:
        if is_https:
            conn = http.client.HTTPSConnection(host, port, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)

    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()

    class _FakeResp:
        def __init__(self, status, body):
            self.status = status
            self._body = body
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            conn.close()

    return _FakeResp(resp.status, body)

def patchright_proxy(proxy):
    if not proxy:
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", proxy):
        proxy = "http://" + proxy
    parsed = urllib.parse.urlparse(proxy)
    if parsed.scheme.startswith("socks"):
        return {"server": proxy}
    username = urllib.parse.unquote(parsed.username or "")
    password = urllib.parse.unquote(parsed.password or "")
    server_netloc = parsed.hostname or "127.0.0.1"
    if parsed.port:
        server_netloc += f":{parsed.port}"
    conf = {"server": urllib.parse.urlunparse((parsed.scheme, server_netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))}
    if username or password:
        conf["username"] = username
        conf["password"] = password
    return conf


def extract_verify_url(html_content):
    cands = re.findall(r'href=["\']?(https://[^\s"\'<>]+)', html_content, re.IGNORECASE)
    if not cands:
        cands = re.findall(r'https://\S+', html_content)
    verify = [u for u in cands if any(kw.lower() in u.lower() for kw in VERIFY_KWS)]
    if verify:
        reseek_v = [u for u in verify if any(h in u.lower() for h in RESEEK_HOSTS)]
        return html_lib.unescape(reseek_v[0] if reseek_v else verify[0])
    fallback = [u for u in cands if any(h in u.lower() for h in RESEEK_HOSTS)]
    return html_lib.unescape(fallback[0]) if fallback else ""


def search_verify_email(token):
    # v7.78i — fast path: list recent inbox by receivedDateTime desc, match
    # sender=verify@replit.com or noreply@replit.com or subject contains replit/verify.
    # KQL $search excludes brand-new emails (~1-5 min indexing delay) so we MUST
    # use $orderby+top + Python-side filter for verify emails sent <5min ago.
    for folder in ("Inbox", "JunkEmail"):
        try:
            url = (f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
                   f"?$top=20&$orderby=receivedDateTime+desc"
                   f"&$select=id,subject,isRead,receivedDateTime,from")
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with open_url(req, timeout=12) as r:
                d = json.loads(r.read())
            for m in d.get("value", []):
                s   = (m.get("subject", "") or "").lower()
                snd = (m.get("from", {}).get("emailAddress", {}).get("address", "") or "").lower()
                if ("replit.com" in snd) or ("verify" in s) or ("confirm" in s) or ("replit" in s):
                    print(f"[click_verify] 找到验证邮件 (recent-list/{folder}): {m.get('subject','')} from={snd}", flush=True)
                    return m["id"]
        except Exception as e:
            print(f"[click_verify] recent-list {folder} 失败: {e}", flush=True)
    # 回退：旧 $search 路径 (索引化邮件 >5min)
    for subj_kw in ("verify", "confirm", "replit", "reseek", "activate"):
        try:
            url = (f"https://graph.microsoft.com/v1.0/me/messages"
                   f"?$search=%22subject:{subj_kw}%22"
                   f"&$select=id,subject,isRead,receivedDateTime&$top=10")
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with open_url(req, timeout=12) as r:
                d = json.loads(r.read())
            for m in d.get("value", []):
                s = m.get("subject", "").lower()
                if any(k in s for k in ("verify", "confirm", "activat", "replit", "reseek")):
                    print(f"[click_verify] 找到验证邮件 ($search): {m['subject']}", flush=True)
                    return m["id"]
        except Exception as e:
            print(f"[click_verify] 搜索 {subj_kw} 失败: {e}", flush=True)
    return ""


if not verify_url:
    if not message_id and token:
        message_id = search_verify_email(token)
        if not message_id:
            print(json.dumps({"success": False, "error": "收件箱未找到验证邮件"}))
            sys.exit(0)
    if message_id and token:
        try:
            safe_id = urllib.parse.quote(message_id, safe="")
            req = urllib.request.Request(
                f"https://graph.microsoft.com/v1.0/me/messages/{safe_id}?$select=body",
                headers={"Authorization": f"Bearer {token}"}
            )
            with open_url(req, timeout=15) as r:
                body_data = json.loads(r.read())
            html = body_data.get("body", {}).get("content", "")
            verify_url = extract_verify_url(html)
            print(f"[click_verify] URL: {verify_url[:120]}", flush=True)
        except Exception as e:
            print(json.dumps({"success": False, "error": f"Graph API 拉取失败: {e}"}))
            sys.exit(0)

if not verify_url:
    print(json.dumps({"success": False, "error": "未找到验证链接，请手动检查邮件正文"}))
    sys.exit(0)


def _try_firebase_verify(verify_url: str) -> dict:
    """直接调 Firebase Identity Toolkit REST API 验证邮件，无需浏览器和代理。"""
    try:
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(verify_url).query)
        oob_code = qs.get("oobCode", [""])[0]
        api_key  = qs.get("apiKey",  [""])[0]
        if not (oob_code and api_key):
            return {"ok": False, "error": "URL 缺少 oobCode/apiKey"}
        url  = f"https://identitytoolkit.googleapis.com/v1/accounts:update?key={api_key}"
        body = json.dumps({"oobCode": oob_code}).encode()
        # 直连 Google，不经 connect-proxy
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        if data.get("emailVerified"):
            return {"ok": True, "email": data.get("email", "")}
        return {"ok": False, "error": f"emailVerified={data.get('emailVerified')} — 响应不含验证成功标志"}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return {"ok": False, "error": f"Firebase HTTPError: {msg}"}
    except Exception as e:
        return {"ok": False, "error": f"Firebase exception: {e}"}

# --- Firebase REST 仅适用于纯 Firebase 托管 action 页 ---------------
# Replit 等使用自定义 action handler（如 replit.com/action-code）的服务，
# 必须走浏览器：Replit 前端在收到 oobCode 后还会调 Replit 后端同步用户表，
# 仅调 Firebase REST 会消费 oobCode 但 Replit DB 不更新 → 显示未验证。
_is_custom_action_handler = bool(re.search(r"replit\.com|reseek\.com", verify_url, re.I))
if not _is_custom_action_handler:
    print("[click_verify] 尝试 Firebase REST API 验证...", flush=True)
    _fb = _try_firebase_verify(verify_url)
    if _fb["ok"]:
        print(f"[click_verify] ✅ Firebase 验证成功: {_fb.get('email', '')}", flush=True)
        print(json.dumps({"success": True, "verify_url": verify_url,
                          "final_url": verify_url,
                          "title": "Email Verified via Firebase",
                          "http_status": 200}))
        sys.exit(0)
    print(f"[click_verify] Firebase 未成功({_fb['error']}), 降级到浏览器直连", flush=True)
else:
    print("[click_verify] 检测到自定义 action handler（Replit 等），强制走浏览器以触发后端同步", flush=True)
# ---------------------------------------------------------------------

try:
    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        # v7.85 — chromium 必须走 WARP, 跟 register/replay 路径出口 IP 一致.
        # 删除 --no-proxy-server (那个 arg 会强制 chromium 忽略所有代理设置, 包括
        # 我们下面 ctx 上挂的 proxy, 让浏览器从 VPS 公网直连 replit.com).
        browser = p.chromium.launch(headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-extensions", "--mute-audio",
                  "--disable-quic"])  # 防 QUIC over UDP 绕过 socks5
        replit_proxy_conf = patchright_proxy(replit_browser_proxy)
        ctx_opts = {"user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")}
        # v7.85 — 强制走 WARP 代理 (默认 socks5://127.0.0.1:40000), 让 chromium
        # 出口 IP 落在 CF WARP CIDR (104.28.x.x), 跟注册时一致, 避开 Replit
        # 反爬 "注册 IP ≠ 验证 IP" 关联检测.
        if replit_proxy_conf:
            ctx_opts["proxy"] = replit_proxy_conf
            print(f"[click_verify] chromium proxy = {replit_proxy_conf.get('server')} (v7.85 WARP)", flush=True)
        else:
            print("[click_verify] WARNING: 无 chromium proxy, 将走 VPS 公网直连 (注册-验证 IP 不匹配)", flush=True)
        ctx  = browser.new_context(**ctx_opts)
        page = ctx.new_page()
        # 抓取所有 XHR/fetch，便于诊断 Replit 后端是否被调用
        _xhrs = []
        def _on_resp(r):
            try:
                u = r.url
                if any(k in u for k in ("identitytoolkit","replit.com/api","replit.com/graphql","replit.com/auth","replit.com/internal","replit.com/data")):
                    _xhrs.append(f"{r.status} {r.request.method} {u[:160]}")
            except Exception: pass
        page.on("response", _on_resp)
        print(f"[click_verify] 访问: {verify_url[:120]}", flush=True)
        resp = page.goto(verify_url, timeout=60000, wait_until="domcontentloaded")
        # Replit SPA：等到页面文字出现验证结果（成功/失败），最多 25s
        body_text = ""
        verified_marker = None
        # 收紧 success 关键词：避免与 "verifying..."、"please verify your email" 等加载/请求页冲突
        # v7.78j — 加 Replit action-code 成功页实际短语:
        # "Verifying email Success!" / "this window will close automatically" /
        # "return to replit here". 这些只在 Firebase oobCode 验证通过后才出现。
        SUCCESS_KWS = (
            "email verified", "email has been verified", "successfully verified",
            "verification successful", "email confirmed", "已验证邮箱", "邮箱已验证",
            "your email is now verified", "you have verified",
            "verifying email success", "this window will close automatically",
            "return to replit here",
        )
        # 收紧 failure 关键词：去掉过于通用的 "error"，加上明确的 oobCode 已用/过期措辞
        FAILURE_KWS = (
            "invalid or has been used", "link is invalid", "link has expired",
            "code is invalid", "code has expired", "already been used",
            "expired", "已过期", "无效", "已使用", "try again",
        )
        for _ in range(25):
            page.wait_for_timeout(1000)
            try:
                body_text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            except Exception:
                body_text = ""
            low = body_text.lower()
            if any(k in low for k in SUCCESS_KWS):
                verified_marker = "success"; break
            if any(k in low for k in FAILURE_KWS):
                verified_marker = "failure"; break
        final_url = page.url
        title     = page.title()
        status    = resp.status if resp else 0
        body_snip = body_text.replace("\n", " ")[:300]
        print(f"[click_verify] marker={verified_marker} url={final_url[:80]} title={title[:60]}", flush=True)
        print(f"[click_verify] body={body_snip}", flush=True)
        for x in _xhrs[:30]:
            print(f"[click_verify] xhr: {x}", flush=True)
        browser.close()
    # 严格：仅 marker == "success" 才视为成功；status==200+marker==None 不再算成功
    # （多数失败页面 HTTP 状态也是 200，旧逻辑会把"未匹配任何关键词"误判成功）
    is_ok = (verified_marker == "success")
    print(json.dumps({"success": is_ok, "verify_url": verify_url,
                      "final_url": final_url, "title": title, "http_status": status,
                      "verified_marker": verified_marker, "body_snippet": body_snip}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "verify_url": verify_url}))
