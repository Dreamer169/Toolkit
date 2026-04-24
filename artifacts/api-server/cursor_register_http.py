#!/usr/bin/env python3
"""
Cursor.sh HTTP 注册器 — 无浏览器、纯 HTTP 协议

流程 (5步):
  1. 获取 session / state  ← 可选，CF 403 时自动跳过
  2. 提交邮箱 (Next.js Server-Action)
  3. 提交密码 + Turnstile token  ← Outlook/MailTM 快速通道时跳过；否则先试空 token
  4. 轮询邮件取 OTP -> 提交 OTP 换 auth_code
  5. 回调 cursor.com 拿 WorkosCursorSessionToken

Turnstile 绕过策略（免费优先）:
  1. Outlook/Hotmail/Live 域名  → email-verification 快速通道，Step3 完全跳过 ✅
  2. MailTM 域名注册            → Step2 后检测服务器是否也触发快速通道
  3. Step3 空 token 探测        → 部分场景后端不校验，直接过 ✅

OTP 获取策略（按优先级）:
  1. MailTM API 轮询（当用临时邮箱时）
  2. MS Graph API（DB 中有 refresh_token 的真实 Outlook 账号）
  3. IMAP（--imap-host/user/pass）

用法:
  # 全自动（自动创建 MailTM 邮箱，试空 token）:
  python cursor_register_http.py --use-xray

  # 指定邮箱（Outlook 快速通道）:
  python cursor_register_http.py --use-xray --email user@outlook.com

  # 带打码服务:

  # IMAP 接 OTP:
  python cursor_register_http.py --email user@example.com \
    --imap-host imap.example.com --imap-user user --imap-pass pass
"""

import argparse
import base64
import hashlib
import imaplib
import email as email_lib
import json
import os
import random
import re
import secrets
import string
import sys
import time
import urllib.request

import httpx
try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None  # fallback to httpx

CURSOR_AUTH_BASE  = "https://authenticator.cursor.sh"
CURSOR_BASE       = "https://cursor.com"
CURSOR_REDIRECT_URI = "https://cursor.com/api/auth/callback"  # CRITICAL: must include /api/

ACTION_SUBMIT_EMAIL    = "d0b05a2a36fbe69091c2f49016138171d5c1e4cd"  # RSC $h25 = real signInAction
ACTION_SUBMIT_PASSWORD = "fef846a39073c935bea71b63308b177b113269b7"
ACTION_MAGIC_CODE      = "f9e8ae3d58a7cd11cccbcdbf210e6f2a6a2550dd"

MAILTM_BASE        = "https://api.mail.tm"

# Microsoft / Outlook domains that trigger email-verification fast path
OUTLOOK_DOMAINS = frozenset({
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "outlook.co.uk", "hotmail.co.uk", "live.co.uk",
    "outlook.de", "hotmail.de", "live.de",
    "outlook.fr", "hotmail.fr", "live.fr",
    "outlook.jp", "hotmail.jp",
    "outlook.cn", "hotmail.cn",
})

# MailTM 域名列表（缓存，运行时动态获取）
_MAILTM_DOMAINS: list[str] = []

NEXT_ROUTER_STATE = (
    "%5B%22%22%2C%7B%22children%22%3A%5B%22(main)%22%2C%7B%22children%22%3A%5B"
    "%22(root)%22%2C%7B%22children%22%3A%5B%22(sign-in)%22%2C%7B%22children%22"
    "%3A%5B%22__PAGE__%22%2C%7B%7D%5D%7D%5D%7D%5D%7D%5D%7D%5D"
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")


# ─── 工具函数 ────────────────────────────────────────────────────────────────

def rand_str(n: int) -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=n))


def rand_password(n: int = 16) -> str:
    lower = random.choice(string.ascii_lowercase)
    upper = random.choice(string.ascii_uppercase)
    digit = random.choice(string.digits)
    sym   = '.'
    rest  = ''.join(random.choices(string.ascii_letters + string.digits, k=n - 4))
    chars = list(lower + upper + digit + sym + rest)
    random.shuffle(chars)
    return ''.join(chars)


def build_pkce() -> tuple:
    verifier  = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b'=').decode()
    return verifier, challenge


def build_state() -> str:
    verifier, challenge = build_pkce()
    state_obj = {
        "returnTo": "/",
        "crypto": {
            "id":             rand_str(22),
            "code_challenge": challenge,
            "code_verifier":  verifier,
        },
        "createdAt": int(time.time() * 1000),
    }
    raw = json.dumps(state_obj, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def build_multipart(fields: dict) -> tuple:
    boundary = "----WebKitFormBoundary" + rand_str(16)
    parts = []
    for k, v in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
        )
    body = (''.join(parts) + f'--{boundary}--\r\n').encode()
    ct   = f'multipart/form-data; boundary={boundary}'
    return body, ct


def action_headers(action_hash: str, referer: str, content_type: str) -> dict:
    return {
        "User-Agent":             UA,
        "Accept":                 "text/x-component",
        "Content-Type":           content_type,
        "Origin":                 CURSOR_AUTH_BASE,
        "Referer":                referer,
        "Next-Action":            action_hash,
        "Next-Router-State-Tree": NEXT_ROUTER_STATE,
    }


# ─── MailTM 临时邮箱 ─────────────────────────────────────────────────────────

def mailtm_create() -> tuple[str, str, str]:
    """
    创建 MailTM 临时邮箱（使用 httpx，更可靠）。
    返回 (email, password, token)
    """
    global _MAILTM_DOMAINS
    try:
        r = httpx.get(MAILTM_BASE + "/domains", timeout=10)
        _MAILTM_DOMAINS = [d["domain"] for d in r.json().get("hydra:member", [])]
        domain = _MAILTM_DOMAINS[0] if _MAILTM_DOMAINS else "sharklasers.com"
    except Exception:
        domain = "sharklasers.com"

    username = "cur" + secrets.token_hex(5)
    email    = f"{username}@{domain}"
    password = "CursorReg" + secrets.token_hex(4) + "!"

    # 注册账号
    try:
        r2 = httpx.post(MAILTM_BASE + "/accounts",
                        json={"address": email, "password": password}, timeout=10)
        if r2.status_code not in (201, 200):
            print(f"[MailTM] 账号注册异常 ({r2.status_code})，尝试直接登录", flush=True)
    except Exception as e:
        print(f"[MailTM] 账号注册请求失败 ({e})", flush=True)

    # 登录取 token（稍等片刻账号生效）
    time.sleep(0.5)
    r3 = httpx.post(MAILTM_BASE + "/token",
                    json={"address": email, "password": password}, timeout=10)
    if r3.status_code != 200:
        raise RuntimeError(f"MailTM 登录失败: {r3.status_code} {r3.text[:200]}")
    token = r3.json()["token"]
    print(f"[MailTM] 临时邮箱创建: {email}", flush=True)
    return email, password, token


def mailtm_wait_otp(token: str, timeout: int = 120):
    """Poll MailTM inbox for 6-digit OTP. Handles list and hydra-wrapped responses."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    deadline = time.time() + timeout
    seen = set()
    print(f"[MailTM] Waiting for OTP (max {timeout}s)...", flush=True)

    while time.time() < deadline:
        try:
            r = httpx.get(MAILTM_BASE + "/messages", headers=headers, timeout=10)
            body_data = r.json()
            if isinstance(body_data, list):
                msgs = body_data
            elif isinstance(body_data, dict):
                msgs = body_data.get("hydra:member", [])
            else:
                msgs = []
            for msg in msgs:
                mid = msg["id"]
                if mid in seen:
                    continue
                seen.add(mid)
                r2 = httpx.get(
                    f"{MAILTM_BASE}/messages/{mid}", headers=headers, timeout=10
                )
                detail = r2.json()
                body = detail.get("text", "") or detail.get("html", "")
                m = re.search(r"\b(\d{6})\b", body)
                if m:
                    otp = m.group(1)
                    print(f"[MailTM] OTP received: {otp}", flush=True)
                    return otp
        except Exception as e:
            print(f"[MailTM] Poll error: {e}", flush=True)
        time.sleep(4)
    return None

def wait_for_otp_imap(host: str, user: str, password: str,
                      timeout: int = 180, interval: int = 5) -> str:
    deadline = time.time() + timeout
    print(f"[IMAP] 等待 Cursor OTP 邮件 ({host})...", flush=True)
    while time.time() < deadline:
        try:
            m = imaplib.IMAP4_SSL(host, timeout=10)
            m.login(user, password)
            m.select("INBOX")
            _, ids = m.search(None, 'SUBJECT "Cursor" UNSEEN')
            for num in (ids[0].split() or []):
                _, data = m.fetch(num, "(RFC822)")
                msg  = email_lib.message_from_bytes(data[0][1])
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode(errors="ignore")
                            break
                else:
                    body = msg.get_payload(decode=True).decode(errors="ignore")
                match = re.search(r'\b(\d{6})\b', body)
                if match:
                    m.logout()
                    return match.group(1)
            m.logout()
        except Exception as e:
            print(f"[IMAP] 连接错误: {e}", flush=True)
        time.sleep(interval)
    raise TimeoutError("等待 OTP 超时")


def build_server_action(bound_hash: str) -> tuple[bytes, str]:
    """Legacy: kept for backward compat. Use build_urlencoded_email_body instead."""
    body = f'["{bound_hash}"]'.encode('utf-8')
    ct   = 'text/plain;charset=UTF-8'
    return body, ct


def build_urlencoded_email_body(
    email: str,
    redirect_uri: str,
    authorization_session_id: str,
    workos_state: str,
) -> tuple[bytes, str]:
    """
    Build Step2 email submit body.
    Confirmed working format (via HTML form inspection + cookie observation):
      Content-Type: application/x-www-form-urlencoded
      Fields: email, redirect_uri, authorization_session_id, state
    """
    from urllib.parse import urlencode
    fields = {
        "email":                   email,
        "redirect_uri":            redirect_uri,
        "authorization_session_id": authorization_session_id,
        "state":                   workos_state,
    }
    body = urlencode(fields).encode("utf-8")
    ct   = "application/x-www-form-urlencoded"
    return body, ct


def build_urlencoded_otp_body(
    otp: str,
    redirect_uri: str,
    authorization_session_id: str,
) -> tuple[bytes, str]:
    """
    Build Step4 OTP submit body for /magic-code POST.
    Fields: code, redirect_uri, authorization_session_id
    """
    from urllib.parse import urlencode
    fields = {
        "code":                     otp,
        "redirect_uri":             redirect_uri,
        "authorization_session_id": authorization_session_id,
    }
    body = urlencode(fields).encode("utf-8")
    ct   = "application/x-www-form-urlencoded"
    return body, ct


def fetch_bound_hash(state_encoded: str, proxy=None) -> str:
    """
    Fetch the sign-up page and extract the Server Action bound data hash from RSC stream.
    The hash is a 40-char hex string referenced as the closure argument in $h entries.
    Falls back to the last known good hash if extraction fails.
    FALLBACK = "a16d2c2e8934a2db21cbf1f00933c1a09affdc7c6977723256c4c27818985dd6"
    """
    FALLBACK = "a16d2c2e8934a2db21cbf1f00933c1a09affdc7c6977723256c4c27818985dd6"
    url = (f"{CURSOR_AUTH_BASE}/sign-up?client_id=cursor-editor"
           f"&redirect_uri={CURSOR_REDIRECT_URI}"
           f"&response_type=code&state={state_encoded}")
    try:
        if cffi_requests is not None:
            r = cffi_requests.get(
                url, impersonate="chrome124",
                proxies={"http": proxy, "https": proxy} if proxy else None,
                timeout=20, verify=False,
            )
            html = r.text
        else:
            r = httpx.get(url, follow_redirects=True,
                          proxy=proxy if proxy else None, timeout=20)
            html = r.text
        # RSC stream contains lines like: ["$","..."] with bound hashes
        # Pattern: 40-hex-char strings that appear in action references
        hashes = re.findall(r'[0-9a-f]{40}', html)
        if hashes:
            # Return the most common one (usually the submit-email bound hash)
            from collections import Counter
            most_common = Counter(hashes).most_common(1)[0][0]
            print(f"[fetch_bound_hash] found {len(hashes)} hash candidates, using {most_common[:12]}...", flush=True)
            return most_common
    except Exception as e:
        print(f"[fetch_bound_hash] error: {e}, using fallback", flush=True)
    return FALLBACK


def make_cffi_client(proxy=None):
    """Create a curl_cffi Session with Chrome TLS fingerprint."""
    if cffi_requests is None:
        return None
    sess = cffi_requests.Session(impersonate="chrome124")
    if proxy:
        sess.proxies = {"http": proxy, "https": proxy}
    return sess

# ─── HTTP 注册类 ─────────────────────────────────────────────────────────────

class CursorRegistrar:
    def __init__(self, proxy=None):
        self.proxy = proxy
        _proxy_arg = proxy if proxy else None
        # Prefer curl_cffi for TLS fingerprint impersonation (bypasses CF WAF)
        self._cffi = make_cffi_client(proxy)
        self.client = httpx.Client(
            proxy=_proxy_arg, follow_redirects=False, timeout=30,
            headers={"User-Agent": UA},
        )
        self.client_follow = httpx.Client(
            proxy=_proxy_arg, follow_redirects=True, timeout=60,
            headers={"User-Agent": UA},
        )

    def step1_get_session(self, state_encoded: str) -> tuple[bool, str, str, str]:
        """
        Step1: GET sign-up page + 提取关键隐藏字段。
        使用 curl_cffi Chrome TLS 指纹绕过 CF WAF (403问题修复)。

        返回 (ok, authorization_session_id, workos_state, redirect_uri)。
        - authorization_session_id: WorkOS session ULID (每次会话唯一)
        - workos_state: WorkOS nonce state (URL编码JSON, 包含nonce)
        - redirect_uri: 从表单读取 (应为 https://cursor.com/api/auth/callback)
        """
        url = (f"{CURSOR_AUTH_BASE}/sign-up?client_id=cursor-editor"
               f"&redirect_uri={CURSOR_REDIRECT_URI}"
               f"&response_type=code&state={state_encoded}")
        # Defaults
        auth_session_id = ""
        workos_state    = ""
        redirect_uri    = CURSOR_REDIRECT_URI

        def _extract_hidden_fields(html: str) -> tuple[str, str, str]:
            """Extract authorization_session_id, state, redirect_uri from form."""
            sid_m = re.search(r'name="authorization_session_id"\s+value="([^"]+)"', html)
            st_m  = re.search(r'name="state"\s+value="([^"]+)"', html)
            ru_m  = re.search(r'name="redirect_uri"\s+value="([^"]+)"', html)
            sid  = sid_m.group(1) if sid_m else ""
            st   = st_m.group(1) if st_m else ""
            ru   = ru_m.group(1) if ru_m else CURSOR_REDIRECT_URI
            return sid, st, ru

        # ── Try curl_cffi first (bypasses CF WAF via Chrome TLS fingerprint) ──
        if self._cffi is not None:
            try:
                r = self._cffi.get(url, allow_redirects=True, timeout=20, verify=False)
                print(f"[Step1] curl_cffi GET -> {r.status_code}", flush=True)
                if r.status_code == 403:
                    print("[Step1] ⚠️  curl_cffi 也被 CF WAF 拦截 (403)", flush=True)
                    return False, auth_session_id, workos_state, redirect_uri
                if r.status_code in (200, 302, 303):
                    auth_session_id, workos_state, redirect_uri = _extract_hidden_fields(r.text)
                    if auth_session_id:
                        print(f"[Step1] authorization_session_id: {auth_session_id}", flush=True)
                        print(f"[Step1] redirect_uri: {redirect_uri}", flush=True)
                    else:
                        print("[Step1] ⚠️  未找到 authorization_session_id，页面可能异常", flush=True)
                    return True, auth_session_id, workos_state, redirect_uri
                print(f"[Step1] ⚠️  curl_cffi 异常状态 {r.status_code}", flush=True)
            except Exception as e:
                print(f"[Step1] curl_cffi 失败: {e}，回退到 httpx", flush=True)

        # ── Fallback: httpx ──
        try:
            r = self.client_follow.get(url)
            print(f"[Step1] httpx GET -> {r.status_code}", flush=True)
            if r.status_code == 403:
                print("[Step1] ⚠️  CF WAF 拦截 (403)，跳过 Step1", flush=True)
                return False, auth_session_id, workos_state, redirect_uri
            if r.status_code not in (200, 302, 303):
                print(f"[Step1] ⚠️  异常状态 {r.status_code}", flush=True)
                return False, auth_session_id, workos_state, redirect_uri
            auth_session_id, workos_state, redirect_uri = _extract_hidden_fields(r.text)
            return True, auth_session_id, workos_state, redirect_uri
        except Exception as e:
            print(f"[Step1] ⚠️  连接异常: {e}，跳过 Step1", flush=True)
            return False, auth_session_id, workos_state, redirect_uri

    def step2_submit_email(
        self,
        email: str,
        state_encoded: str,
        authorization_session_id: str = "",
        workos_state: str = "",
        redirect_uri: str = "",
        bound_hash: str = "",  # legacy, ignored
    ) -> str:
        """
        提交邮箱 (已修复: urlencoded POST + 所有隐藏表单字段).

        确认的正确格式 (通过HTML表单检查 + Cookie观察):
          Content-Type: application/x-www-form-urlencoded
          Next-Action: d0b05a2a36fbe69091c2f49016138171d5c1e4cd  (RSC $h25 signInAction)
          Body: email=...&redirect_uri=https://cursor.com/api/auth/callback
                &authorization_session_id=<WorkOS ULID>&state=<WorkOS nonce state>

        返回 'EMAIL_VERIFICATION_FAST_PATH' 或响应文本。
        Bug修复:
          1. redirect_uri 改为 /api/auth/callback (有 /api/)
          2. authorization_session_id 必须从Step1 HTML表单提取并回传
          3. state 是 WorkOS nonce state (URL编码JSON)，非我们的 PKCE state
          4. body 格式改为 application/x-www-form-urlencoded
          5. Action hash 改为 d0b05a2a... (真实的 RSC $h25 signInAction)
        """
        _redirect_uri = redirect_uri or CURSOR_REDIRECT_URI
        body, ct = build_urlencoded_email_body(
            email=email,
            redirect_uri=_redirect_uri,
            authorization_session_id=authorization_session_id,
            workos_state=workos_state,
        )
        url  = f"{CURSOR_AUTH_BASE}/sign-up"
        hdrs = action_headers(ACTION_SUBMIT_EMAIL, url, ct)
        # Accept full HTML (urlencoded POST returns full page, not RSC stream)
        hdrs["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

        # ── Try curl_cffi first ──
        resp_text = ""
        resp_status = 0
        loc = ""
        if self._cffi is not None:
            try:
                r = self._cffi.post(url, data=body, headers=hdrs,
                                    allow_redirects=False, timeout=30, verify=False)
                resp_status = r.status_code
                resp_text   = r.text
                loc         = r.headers.get("location", "")
                print(f"[Step2] curl_cffi POST -> {resp_status} loc={loc[:80] if loc else '-'}", flush=True)
                if resp_status not in (200, 202, 302, 303):
                    print(f"[Step2] curl_cffi 异常 {resp_status}: {resp_text[:200]}", flush=True)
            except Exception as e:
                print(f"[Step2] curl_cffi 失败: {e}，回退到 httpx + multipart", flush=True)
                resp_status = 0

        # ── Fallback to httpx + urlencoded (same correct format, different HTTP client) ──
        if resp_status == 0:
            try:
                hdrs2 = dict(hdrs)
                r2 = self.client.post(url, content=body, headers=hdrs2)
                resp_status = r2.status_code
                resp_text   = r2.text
                loc         = r2.headers.get("location", "")
                print(f"[Step2] httpx urlencoded fallback -> {resp_status} loc={loc[:80] if loc else '-'}", flush=True)
            except Exception as e:
                raise RuntimeError(f"Step2 全部方式失败: {e}")

        body_preview = resp_text[:300].replace("\n", " ").replace("\r", "")
        print(f"[Step2] body={body_preview}", flush=True)
        if resp_status not in (200, 202, 303, 302, 422):
            raise RuntimeError(f"Step2 异常: {resp_status} {resp_text[:200]}")

        # 重定向到 email-verification → 快速通道（跳过 Step3）
        if loc and "email-verification" in loc:
            try:
                self.client_follow.get(loc)
            except Exception:
                pass
            return "EMAIL_VERIFICATION_FAST_PATH"
        combined = loc + resp_text
        if "email-verification" in combined or "email_verification" in combined:
            return "EMAIL_VERIFICATION_FAST_PATH"
        return resp_text

    def step3_submit_password(self, email: str, password: str,
                               state_encoded: str, captcha_token: str) -> bool:
        """
        提交密码 + Turnstile token。
        返回 True 表示成功，False 表示失败（token 被拒）。
        """
        body, ct = build_multipart({
            "1_state":      state_encoded,
            "email":        email,
            "password":     password,
            "captchaToken": captcha_token,
        })
        url  = f"{CURSOR_AUTH_BASE}/sign-up"
        hdrs = action_headers(ACTION_SUBMIT_PASSWORD, url, ct)
        r    = self.client.post(url, content=body, headers=hdrs)
        loc  = r.headers.get("location", "")
        print(f"[Step3] 提交密码 -> {r.status_code} loc={loc[:80] if loc else '-'}", flush=True)
        if r.status_code == 200:
            # 检查响应是否包含错误
            txt = r.text.lower()
            if "invalid" in txt or "captcha" in txt or "turnstile" in txt:
                print(f"[Step3] ⚠️  响应包含错误标志: {r.text[:200]}", flush=True)
                return False
            return True
        if r.status_code in (302, 303):
            # 重定向通常代表成功
            return True
        print(f"[Step3] ❌ 状态码 {r.status_code}: {r.text[:200]}", flush=True)
        return False

    def step4_submit_otp(
        self,
        email: str,
        otp: str,
        state_encoded: str,
        authorization_session_id: str = "",
        redirect_uri: str = "",
    ) -> str:
        """
        提交 OTP 验证码 (已修复).

        修复:
          1. POST 到 /magic-code (原为 /sign-up)
          2. 使用 application/x-www-form-urlencoded (原为 multipart)
          3. 字段: code, redirect_uri, authorization_session_id
          4. Action hash 与 Step2 相同 (d0b05a2a... signInAction 处理完整流程)
        """
        _redirect_uri = redirect_uri or CURSOR_REDIRECT_URI
        body, ct = build_urlencoded_otp_body(
            otp=otp,
            redirect_uri=_redirect_uri,
            authorization_session_id=authorization_session_id,
        )
        url  = f"{CURSOR_AUTH_BASE}/magic-code"
        hdrs = action_headers(ACTION_SUBMIT_EMAIL, url, ct)
        hdrs["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        hdrs["Referer"] = f"{CURSOR_AUTH_BASE}/magic-code?authorization_session_id={authorization_session_id}"

        # Try curl_cffi first
        if self._cffi is not None:
            try:
                r = self._cffi.post(url, data=body, headers=hdrs,
                                    allow_redirects=False, timeout=30, verify=False)
                print(f"[Step4] curl_cffi POST /magic-code -> {r.status_code}", flush=True)
                loc = r.headers.get("location", "")
                print(f"[Step4] location={loc[:100] if loc else '-'}", flush=True)
                if loc:
                    m = re.search(r'code=([A-Za-z0-9_\-]+)', loc)
                    if m:
                        return m.group(1)
                m = re.search(r'code=([A-Za-z0-9_\-]+)', r.text)
                if m:
                    return m.group(1)
                if r.status_code in (200, 302, 303):
                    print(f"[Step4] body 前500: {r.text[:500]}", flush=True)
            except Exception as e:
                print(f"[Step4] curl_cffi 失败: {e}", flush=True)

        # Fallback httpx
        r = self.client.post(url, content=body, headers=hdrs)
        print(f"[Step4] httpx POST /magic-code -> {r.status_code}", flush=True)
        loc = r.headers.get("location", "")
        if loc:
            m = re.search(r'code=([A-Za-z0-9_\-]+)', loc)
            if m:
                return m.group(1)
        m = re.search(r'code=([A-Za-z0-9_\-]+)', r.text)
        if m:
            return m.group(1)
        raise RuntimeError(f"Step4: 未找到 auth code, status={r.status_code}, body={r.text[:300]}")

    def step5_get_token(self, auth_code: str, state_encoded: str) -> str:
        cb = (f"{CURSOR_REDIRECT_URI}"  # https://cursor.com/api/auth/callback
              f"?code={auth_code}&state={state_encoded}")
        r = self.client_follow.get(cb)
        print(f"[Step5] 获取 token -> {r.status_code}", flush=True)
        for cookie in self.client_follow.cookies.jar:
            if cookie.name == "WorkosCursorSessionToken":
                return cookie.value
        raise RuntimeError("Step5: 未找到 WorkosCursorSessionToken cookie")

    def close(self):
        self.client.close()
        self.client_follow.close()


# ─── 主注册函数 ──────────────────────────────────────────────────────────────

def register(
    email: str = "",
    password: str = "",
    proxy=None,
    imap_host: str = "",
    imap_user: str = "",
    imap_pass: str = "",
    skip_step1: bool = False,
    use_mailtm: bool = False,
) -> dict:
    """
    完整注册流程。
    - email 为空时：优先 DB Outlook 账号，其次创建 MailTM 临时邮箱
    - Turnstile 绕过顺序：快速通道 > 空 token 探测（不再支持付费打码 API）
    """
    # ── 决定邮箱 ──────────────────────────────────────────────────────────────
    mailtm_token: str = ""
    is_mailtm = False

    if not email:
        # 1) 优先从 DB 取有 refresh_token 的 Outlook 账号
        if not use_mailtm:
            try:
                import subprocess as _sp
                _sql = (
                    "SELECT email, password FROM accounts "
                    "WHERE platform='outlook' AND status='active' "
                    "AND refresh_token IS NOT NULL AND refresh_token != '' "
                    "ORDER BY RANDOM() LIMIT 1;"
                )
                _res = _sp.run(
                    ["psql", "postgresql://postgres:postgres@localhost/toolkit",
                     "-t", "-A", "-F", "\t", "-c", _sql],
                    capture_output=True, text=True, timeout=5
                )
                _line = _res.stdout.strip().split("\n")[0]
                if _line and "\t" in _line:
                    _parts = _line.split("\t")
                    email = _parts[0].strip()
                    if not password and len(_parts) > 1 and _parts[1].strip():
                        password = _parts[1].strip()
                    print(f"[Cursor] 自动选取 DB Outlook 账号: {email}", flush=True)
                else:
                    raise ValueError("no db row")
            except Exception as _db_err:
                print(f"[Cursor] DB 取号失败 ({_db_err})，改用 MailTM", flush=True)

        # 2) 创建 MailTM 临时邮箱
        if not email:
            try:
                email, _, mailtm_token = mailtm_create()
                is_mailtm = True
                print(f"[Cursor] 使用 MailTM 邮箱: {email}", flush=True)
            except Exception as e:
                raise RuntimeError(f"无法创建 MailTM 邮箱: {e}")

    if not password:
        password = rand_password()

    email_domain = email.split("@")[-1].lower() if "@" in email else ""
    is_outlook   = email_domain in OUTLOOK_DOMAINS

    state_encoded = build_state()
    reg = CursorRegistrar(proxy=proxy)
    try:
        print(f"[Cursor] 开始注册 {email} ...", flush=True)

        # ── Step 1 ────────────────────────────────────────────────────────────
        # Step1 now returns (ok, authorization_session_id, workos_state, redirect_uri)
        authorization_session_id = ""
        workos_state             = ""
        step1_redirect_uri       = CURSOR_REDIRECT_URI

        if skip_step1:
            print("[Cursor] ⏭️  --skip-step1，直接 Step2", flush=True)
        else:
            step1_ok, authorization_session_id, workos_state, step1_redirect_uri = (
                reg.step1_get_session(state_encoded)
            )
            if not step1_ok:
                print("[Cursor] ℹ️  Step1 被拦截，直接尝试 Step2", flush=True)
            elif not authorization_session_id:
                print("[Cursor] ⚠️  Step1 未获取 authorization_session_id，请检查页面", flush=True)

        # ── Step 2 ────────────────────────────────────────────────────────────
        step2_result = reg.step2_submit_email(
            email, state_encoded,
            authorization_session_id=authorization_session_id,
            workos_state=workos_state,
            redirect_uri=step1_redirect_uri,
        )

        # ── 快速通道检测 ──────────────────────────────────────────────────────
        rsp_has_ev  = (
            step2_result == "EMAIL_VERIFICATION_FAST_PATH"
            or "email-verification" in str(step2_result)
            or "email_verification" in str(step2_result)
        )
        use_fast_path = is_outlook or rsp_has_ev

        if use_fast_path:
            reason = "Outlook 域名" if is_outlook else "服务器返回 email-verification"
            print(f"[Cursor] ⚡ 快速通道激活（{reason}）→ 完全跳过 Step3 Turnstile ✅", flush=True)
        else:
            # ── Step 3: 需要 Turnstile ────────────────────────────────────────
            # 策略 1: 先试空 token（免费，部分场景后端不校验）
            print("[Cursor] 🔍 Step3 空 token 探测...", flush=True)
            step3_ok = reg.step3_submit_password(email, password, state_encoded, "")
            if step3_ok:
                print("[Cursor] ✅ Step3 空 token 通过！Turnstile 绕过成功（后端未校验）", flush=True)
            else:
                raise RuntimeError(
                    "Step3 需要 Turnstile token，空 token 被拒。\n"
                    "解决方案：使用 Outlook/Hotmail 邮箱（自动触发快速通道）。\n"
                    "本工具已移除付费打码服务支持（坚持免费开源原则）。"
                )

        # ── Step 4: 获取 OTP ──────────────────────────────────────────────────
        print("[Cursor] ⏳ 等待 OTP...", flush=True)
        otp: str | None = None

        if is_mailtm and mailtm_token:
            # MailTM 轮询
            otp = mailtm_wait_otp(mailtm_token, timeout=120)
        elif email and not imap_host:
            # MS Graph API（DB 中有 refresh_token 的 Outlook 账号）
            try:
                import sys as _sys
                _sys.path.insert(0, '/workspaces/Toolkit/artifacts/api-server')
                from cursor_register import _get_otp_via_graph_or_browser
                otp = _get_otp_via_graph_or_browser(email, password, timeout=120)
            except Exception as _e:
                print(f"[OTP] Graph API 失败: {_e}", flush=True)

        if not otp and imap_host:
            otp = wait_for_otp_imap(imap_host, imap_user, imap_pass)

        if not otp:
            raise RuntimeError(
                "OTP 获取失败。\n"
                "  - MailTM: 邮件未到或超时\n"
                "  - Graph API: DB 中无有效 refresh_token\n"
                "  - IMAP: 未配置 --imap-host/user/pass"
            )

        print(f"[Cursor] OTP={otp}", flush=True)

        # ── Step 4: 提交 OTP ──────────────────────────────────────────────────
        auth_code     = reg.step4_submit_otp(
            email, otp, state_encoded,
            authorization_session_id=authorization_session_id,
            redirect_uri=step1_redirect_uri,
        )
        session_token = reg.step5_get_token(auth_code, state_encoded)

        print(f"[Cursor] 🎉 SUCCESS email={email} token_len={len(session_token)}", flush=True)
        return {
            "success":  True,
            "email":    email,
            "password": password,
            "token":    session_token,
        }
    finally:
        reg.close()


# ─── CLI 入口 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Cursor HTTP 注册器")
    p.add_argument("--email",          default="",  help="邮箱（留空自动选取 DB Outlook 账号或创建 MailTM）")
    p.add_argument("--password",       default="",  help="密码（留空随机生成）")
    p.add_argument("--proxy",          default="",  help="代理 URL，如 http://user:pass@host:port")
    p.add_argument("--use-xray",       action="store_true", help="使用本机 xray socks5://127.0.0.1:10808")
    p.add_argument("--use-mailtm",     action="store_true", help="强制使用 MailTM 临时邮箱（而非 DB 账号）")
    p.add_argument("--skip-step1",     action="store_true", help="跳过 Step1（CF 已知拦截时）")
    p.add_argument("--imap-host",      default="",  help="IMAP 服务器（可选）")
    p.add_argument("--imap-user",      default="",  help="IMAP 用户名（可选）")
    p.add_argument("--imap-pass",      default="",  help="IMAP 密码（可选）")
    args = p.parse_args()

    proxy = args.proxy
    if args.use_xray:
        proxy = "socks5://127.0.0.1:10808"
        print(f"[Cursor] 使用 xray 代理: {proxy}", flush=True)

    result = register(
        email           = args.email,
        password        = args.password,
        proxy           = proxy or None,
        imap_host       = args.imap_host,
        imap_user       = args.imap_user,
        imap_pass       = args.imap_pass,
        skip_step1      = args.skip_step1,
        use_mailtm      = args.use_mailtm,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
