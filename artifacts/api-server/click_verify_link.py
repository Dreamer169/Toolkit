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
proxy_url = data.get("proxy") or os.environ.get("MICROSOFT_BROWSER_PROXY") or os.environ.get("OUTLOOK_BROWSER_PROXY") or os.environ.get("MICROSOFT_HTTP_PROXY") or os.environ.get("OUTLOOK_HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or "http://127.0.0.1:8091"

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
                    print(f"[click_verify] 找到验证邮件: {m['subject']}", flush=True)
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

# --- 先尝试 Firebase REST API（不需要浏览器，不受代理影响）-----------
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
# ---------------------------------------------------------------------

try:
    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-extensions", "--mute-audio",
                  "--no-proxy-server"])  # 阻止 Chromium 读取 HTTPS_PROXY 环境变量
        proxy_conf = patchright_proxy(proxy_url)
        ctx_opts = {"user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")}
        # 直连 replit.com（VPS 有出口，connect-proxy 仅用于 Graph API）
        # if proxy_conf:
        #     ctx_opts["proxy"] = proxy_conf
        ctx  = browser.new_context(**ctx_opts)
        page = ctx.new_page()
        print(f"[click_verify] 访问: {verify_url[:120]}", flush=True)
        resp = page.goto(verify_url, timeout=30000, wait_until="networkidle")
        page.wait_for_timeout(6000)
        final_url = page.url
        title     = page.title()
        status    = resp.status if resp else 0
        print(f"[click_verify] 结果={final_url[:80]} 标题={title[:60]}", flush=True)
        browser.close()
    print(json.dumps({"success": True, "verify_url": verify_url,
                      "final_url": final_url, "title": title, "http_status": status}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "verify_url": verify_url}))
