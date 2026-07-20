#!/usr/bin/env python3
"""
codex_headless_login.py
用 curl_cffi 模拟浏览器，走 auth.openai.com PKCE 流程，
全程无头登录拿 codex 专用 access_token + refresh_token，
写入 ~/.codex/auth.json (chatgptAuthTokens 格式)。

用法:
  python3 codex_headless_login.py [email] [password]
"""
import sys, os, json, base64, hashlib, secrets, re, time
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

# curl_cffi 路径
sys.path.insert(0, "/data/Toolkit/reference-tools/chatgpt2api")

from curl_cffi import requests as cffi_requests

EMAIL    = sys.argv[1] if len(sys.argv) > 1 else "enriquetaidzik515218@outlook.com"
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else "dokows263493"

# codex CLI 专用 client_id
CODEX_CLIENT_ID   = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_REDIRECT    = "http://localhost:1455/auth/callback"
CODEX_SCOPE       = "openid profile email offline_access"

AUTH_BASE   = "https://auth.openai.com"
AUTH_PATH   = Path.home() / ".codex" / "auth.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def generate_pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge

def make_session():
    sess = cffi_requests.Session(impersonate="chrome136", verify=False)
    return sess

def do_login():
    sess = make_session()
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)

    # ── Step 1: GET /api/accounts/authorize (起始 authorize 页面) ──
    auth_params = {
        "issuer": AUTH_BASE,
        "client_id": CODEX_CLIENT_ID,
        "redirect_uri": CODEX_REDIRECT,
        "scope": CODEX_SCOPE,
        "response_type": "code",
        "response_mode": "query",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
    }
    authorize_url = f"{AUTH_BASE}/api/accounts/authorize?{urlencode(auth_params)}"
    log(f"Step 1: GET authorize → {authorize_url[:80]}...")

    r = sess.get(authorize_url, headers={"User-Agent": UA}, allow_redirects=True, timeout=30)
    log(f"  status={r.status_code} final_url={r.url[:80]}")

    # 从最终 URL 里拿 state (auth0 会替换 state)
    final_url = str(r.url)
    qs = parse_qs(urlparse(final_url).query)
    actual_state = (qs.get("state") or [state])[0]
    log(f"  state={actual_state[:30]}...")

    # 从 HTML 中提取 csrf token / action
    html = r.text
    csrf_match = re.search(r'name="_csrf"\s+value="([^"]+)"', html)
    if not csrf_match:
        csrf_match = re.search(r'"csrf"\s*:\s*"([^"]+)"', html)
    csrf = csrf_match.group(1) if csrf_match else ""
    log(f"  csrf={'found' if csrf else 'NOT FOUND'}")

    # ── Step 2: POST email (identifier) ──
    log(f"Step 2: POST email={EMAIL}")
    id_url = f"{AUTH_BASE}/u/login/identifier"
    # Extract form action if present
    action_match = re.search(r'action="(/[^"]*identifier[^"]*)"', html)
    if action_match:
        id_url = AUTH_BASE + action_match.group(1)
    log(f"  POST → {id_url[:80]}")

    r2 = sess.post(
        id_url,
        data={
            "state": actual_state,
            "username": EMAIL,
            "js-available": "true",
            "webauthn-available": "false",
            "is-brave": "false",
            "webauthn-platform-available": "false",
            "action": "default",
        },
        headers={
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": authorize_url,
        },
        allow_redirects=True,
        timeout=30,
    )
    log(f"  status={r2.status_code} url={str(r2.url)[:80]}")

    html2 = r2.text
    if "password" not in html2.lower() and r2.status_code not in (200, 302):
        log(f"  WARN: password form not detected. Body snippet: {html2[:300]}")

    # ── Step 3: POST password ──
    log("Step 3: POST password")
    pwd_url = f"{AUTH_BASE}/u/login/password"
    action_match2 = re.search(r'action="(/[^"]*password[^"]*)"', html2)
    if action_match2:
        pwd_url = AUTH_BASE + action_match2.group(1)
    log(f"  POST → {pwd_url[:80]}")

    r3 = sess.post(
        pwd_url,
        data={
            "state": actual_state,
            "username": EMAIL,
            "password": PASSWORD,
            "action": "default",
        },
        headers={
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": str(r2.url),
        },
        allow_redirects=False,
        timeout=30,
    )
    log(f"  status={r3.status_code}")

    # 跟踪重定向，找 callback?code=...
    location = r3.headers.get("location", "")
    log(f"  Location: {location[:120]}")

    max_redirects = 10
    for _ in range(max_redirects):
        if not location:
            break
        if "code=" in location or CODEX_REDIRECT in location or "localhost:1455" in location:
            log(f"  Got callback! {location[:120]}")
            break
        # 跟重定向
        if location.startswith("/"):
            location = AUTH_BASE + location
        r_next = sess.get(
            location,
            headers={"User-Agent": UA, "Referer": str(r3.url)},
            allow_redirects=False,
            timeout=30,
        )
        log(f"  Redirect → {r_next.status_code} {str(r_next.url)[:80]}")
        location = r_next.headers.get("location", str(r_next.url) if "code=" in str(r_next.url) else "")

    # 从 location 里提取 code
    parsed_cb = urlparse(location)
    qs_cb = parse_qs(parsed_cb.query)
    code = (qs_cb.get("code") or [""])[0]
    if not code:
        log(f"FAIL: no code in callback. location={location[:200]}")
        log(f"Last HTML snippet: {r3.text[:500]}")
        sys.exit(1)

    log(f"Step 4: Got auth code={code[:20]}...")

    # ── Step 4: POST /oauth/token (code exchange) ──
    log("Step 4: Exchange code for tokens")
    token_r = sess.post(
        f"{AUTH_BASE}/oauth/token",
        json={
            "client_id": CODEX_CLIENT_ID,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CODEX_REDIRECT,
            "code_verifier": verifier,
        },
        headers={
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Origin": AUTH_BASE,
        },
        timeout=30,
    )
    log(f"  token status={token_r.status_code}")

    try:
        tok_data = token_r.json()
    except Exception:
        tok_data = {}

    if not tok_data.get("access_token"):
        log(f"FAIL: no access_token. Response: {token_r.text[:400]}")
        sys.exit(1)

    # ── 写入 auth.json ──
    auth_data = {
        "auth_mode": "chatgptAuthTokens",
        "access_token": tok_data["access_token"],
        "refresh_token": tok_data.get("refresh_token", ""),
        "id_token": tok_data.get("id_token", ""),
        "client_id": CODEX_CLIENT_ID,
    }
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_PATH.write_text(json.dumps(auth_data, indent=2) + "\n", encoding="utf-8")

    log(f"SUCCESS! Wrote to {AUTH_PATH}")
    log(f"  access_token: {auth_data['access_token'][:40]}...")
    log(f"  refresh_token: {auth_data['refresh_token'][:30]}...")
    print(json.dumps({"ok": True, "email": EMAIL, "expires_in": tok_data.get("expires_in")}))

if __name__ == "__main__":
    do_login()
