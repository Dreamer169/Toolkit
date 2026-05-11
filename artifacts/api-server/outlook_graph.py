#!/usr/bin/env python3
"""
outlook_graph.py — Microsoft Graph API 邮件读取工具
支持:
  1. wait_for_cursor_otp(timeout)        — 从 /tmp/ms_tokens.json 读取 Cursor OTP
  2. wait_for_replit_verify(refresh_token, timeout) — 按账号 refresh_token 读取 Replit 验证链接
"""
import json, time, re, urllib.request, urllib.parse, urllib.error

import os as _os
CLIENT_ID  = _os.environ.get("OUTLOOK_CLIENT_ID", "9e5f94bc-e8a4-4e73-b8be-63364c29d753")
TENANT     = "consumers"
TOKEN_FILE = "/tmp/ms_tokens.json"


def _refresh_access_token_raw(refresh_token: str) -> dict:
    """刷新并返回完整响应 dict"""
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read https://graph.microsoft.com/IMAP.AccessAsUser.All https://graph.microsoft.com/SMTP.Send",
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except urllib.error.HTTPError as e:
        resp = json.loads(e.read())
    if "access_token" not in resp:
        raise ValueError(f"刷新 token 失败: {resp}")
    return resp


def _refresh_access_token(refresh_token: str) -> str:
    resp = _refresh_access_token_raw(refresh_token)
    # 保存到文件（向后兼容）
    saved = {}
    try:
        with open(TOKEN_FILE) as f:
            saved = json.load(f)
    except Exception:
        pass
    saved["access_token"]  = resp["access_token"]
    saved["expires_at"]    = time.time() + resp.get("expires_in", 3600)
    if "refresh_token" in resp:
        saved["refresh_token"] = resp["refresh_token"]
    with open(TOKEN_FILE, "w") as f:
        json.dump(saved, f, indent=2)
    return resp["access_token"]


def _load_token() -> str:
    with open(TOKEN_FILE) as f:
        saved = json.load(f)
    if time.time() < saved.get("expires_at", 0) - 60:
        return saved["access_token"]
    return _refresh_access_token(saved["refresh_token"])


def _graph_get(path: str, token: str) -> dict:
    if "?" in path:
        base, qs = path.split("?", 1)
        qs = urllib.parse.quote(qs, safe="=&$/'+")
        url = f"https://graph.microsoft.com/v1.0{base}?{qs}"
    else:
        url = f"https://graph.microsoft.com/v1.0{path}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def _graph_get_with_token(path: str, access_token: str) -> dict:
    """用已有 access_token 直接查询（不走文件）"""
    if "?" in path:
        base, qs = path.split("?", 1)
        qs = urllib.parse.quote(qs, safe="=&$/'+")
        url = f"https://graph.microsoft.com/v1.0{base}?{qs}"
    else:
        url = f"https://graph.microsoft.com/v1.0{path}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


# ─────────────────────────────────────────────────────────────
# 1. Cursor OTP（原有功能，保留不变）
# ─────────────────────────────────────────────────────────────
def wait_for_cursor_otp(timeout: int = 120) -> str | None:
    """
    轮询 Outlook 收件箱，返回 Cursor OTP 验证码（6位数字）。
    需要 /tmp/ms_tokens.json 存在（由 ms_device_auth.py 生成）。
    """
    import os
    if not os.path.exists(TOKEN_FILE):
        raise FileNotFoundError(
            f"{TOKEN_FILE} 不存在，请先运行 ms_device_auth.py 完成授权"
        )
    deadline = time.time() + timeout
    seen_ids: set = set()
    token = _load_token()
    print("[outlook_graph] 开始轮询 Graph API 收件箱 (Cursor OTP)...")
    while time.time() < deadline:
        try:
            msgs = _graph_get(
                "/me/mailFolders/Inbox/messages"
                "?$top=15&$orderby=receivedDateTime+desc"
                "&$select=id,subject,bodyPreview,receivedDateTime,from",
                token,
            )
            for msg in msgs.get("value", []):
                mid = msg["id"]
                if mid in seen_ids:
                    continue
                subj = msg.get("subject", "")
                preview = msg.get("bodyPreview", "")
                if not re.search(r"cursor|verification|code|verify", subj + preview, re.I):
                    seen_ids.add(mid)
                    continue
                detail = _graph_get(f"/me/messages/{mid}?$select=body", token)
                body   = detail.get("body", {}).get("content", "")
                m = re.search(r"(\d{6})", body)
                if m:
                    print(f"[outlook_graph] ✅ 找到 OTP: {m.group(1)}")
                    return m.group(1)
                seen_ids.add(mid)
        except Exception as e:
            print(f"[outlook_graph] 轮询异常: {e}")
            try:
                token = _load_token()
            except Exception:
                pass
        time.sleep(8)
    return None


# ─────────────────────────────────────────────────────────────
# 2. Replit 验证邮件（新功能）
# ─────────────────────────────────────────────────────────────
REPLIT_KWS = ("replit", "verify", "confirm", "activate", "email", "welcome")

def wait_for_replit_verify(
    refresh_token: str,
    timeout: int = 240,
    after_ts: float | None = None,   # 只看这个时间戳之后收到的邮件
) -> str | None:
    """
    用指定账号的 refresh_token 轮询 Graph API 收件箱，
    返回 Replit 发送的邮件验证 URL（如 https://replit.com/verify?...）。
    
    after_ts: 开始注册前的 Unix 时间戳，只返回之后收到的邮件（避免读旧邮件）
    """
    start_ts = after_ts or (time.time() - 30)
    deadline  = time.time() + timeout
    seen_ids: set = set()

    print(f"[replit_graph] 获取 access_token…", flush=True)
    try:
        resp = _refresh_access_token_raw(refresh_token)
        token = resp["access_token"]
        # 保存新 refresh_token 以便外部更新 DB
        new_refresh = resp.get("refresh_token", refresh_token)
    except Exception as e:
        print(f"[replit_graph] ❌ 刷新 token 失败: {e}", flush=True)
        return None

    print(f"[replit_graph] 开始轮询收件箱 (最多 {timeout}s)…", flush=True)
    while time.time() < deadline:
        try:
            # NEW: also poll JunkEmail (Replit verify mail often lands there for fresh accounts)
            try:
                _junk = _graph_get_with_token(
                    "/me/mailFolders/JunkEmail/messages?$select=subject,from,id&$orderby=receivedDateTime desc&$top=10",
                    token,
                )
                for _jm in _junk.get("value", []):
                    _jsubj = (_jm.get("subject") or "").lower()
                    _jfrom = ((_jm.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
                    if any(k in _jsubj for k in REPLIT_KWS) or "replit" in _jfrom:
                        _jid = _jm.get("id")
                        _jdetail = _graph_get_with_token(f"/me/messages/{_jid}?$select=body", token)
                        _jurl = _extract_verify_url((_jdetail.get("body") or {}).get("content", ""))
                        if _jurl:
                            print(f"[replit_graph] ✅ (Junk) 验证链接: {_jurl[:80]}", flush=True)
                            return _jurl
            except Exception as _je:
                pass

            msgs = _graph_get_with_token(
                "/me/mailFolders/Inbox/messages"
                "?$top=20&$orderby=receivedDateTime+desc"
                "&$select=id,subject,bodyPreview,receivedDateTime,from",
                token,
            )
            for msg in msgs.get("value", []):
                mid  = msg["id"]
                if mid in seen_ids:
                    continue
                # 过滤旧邮件（按 receivedDateTime）
                recv_str = msg.get("receivedDateTime", "")
                if recv_str:
                    try:
                        from datetime import datetime, timezone
                        recv_dt = datetime.fromisoformat(recv_str.replace("Z", "+00:00"))
                        recv_ts = recv_dt.timestamp()
                        if recv_ts < start_ts - 60:   # 超过60s 的旧邮件跳过
                            seen_ids.add(mid)
                            continue
                    except Exception:
                        pass

                subj    = msg.get("subject", "").lower()
                preview = msg.get("bodyPreview", "").lower()
                sender  = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()

                is_replit = (
                    any(k in subj for k in REPLIT_KWS)
                    or any(k in preview for k in REPLIT_KWS)
                    or "replit" in sender
                    or "noreply" in sender
                )
                if not is_replit:
                    seen_ids.add(mid)
                    continue

                print(f"[replit_graph] 📧 找到候选邮件: {msg.get('subject','')} from={sender}", flush=True)
                detail = _graph_get_with_token(f"/me/messages/{mid}?$select=body", token)
                body   = detail.get("body", {}).get("content", "") or ""
                url    = _extract_verify_url(body)
                if url:
                    print(f"[replit_graph] ✅ 验证链接: {url[:80]}", flush=True)
                    return url
                seen_ids.add(mid)
        except Exception as e:
            print(f"[replit_graph] 轮询异常: {e}", flush=True)
            try:
                resp  = _refresh_access_token_raw(refresh_token)
                token = resp["access_token"]
            except Exception:
                pass
        time.sleep(10)

    print("[replit_graph] ⏰ 超时，未收到验证邮件", flush=True)
    return None


def _extract_verify_url(html: str) -> str | None:
    """从邮件 HTML 中提取 Replit 验证链接"""
    candidates = re.findall(r'https?://[^\s"\'<>]+', html)
    priority_kws = ("replit.com/verify", "replit.com/email", "replit.com/confirm",
                    "email-action", "/auth/")
    fallback_kws = ("replit.com", "verify", "confirm", "activate")
    for url in candidates:
        if any(k in url.lower() for k in priority_kws):
            return url.rstrip(".")
    for url in candidates:
        if any(k in url.lower() for k in fallback_kws):
            return url.rstrip(".")
    return None



# ─────────────────────────────────────────────────────────────
# 3. unitool.ai 验证邮件（Inbox + JunkEmail 双重搜索）
# ─────────────────────────────────────────────────────────────
UNITOOL_KWS     = ("unitool", "verify", "confirm", "activate", "email", "welcome")
UNITOOL_SENDERS = ("unitool.ai", "noreply", "no-reply", "support")


def wait_for_unitool_verify(
    refresh_token: str,
    timeout: int = 300,
    after_ts: float | None = None,
) -> str | None:
    """
    同时轮询 Inbox + JunkEmail，寻找 unitool.ai 的验证邮件。
    修复：unitool 验证邮件经常落入垃圾邮件(Junk Email)，原代码只搜索 Inbox 会漏掉。
    """
    start_ts = after_ts or (time.time() - 30)
    deadline  = time.time() + timeout
    seen_ids: set = set()

    print("[unitool_graph] 获取 access_token…", flush=True)
    try:
        resp  = _refresh_access_token_raw(refresh_token)
        token = resp["access_token"]
    except Exception as e:
        print(f"[unitool_graph] ❌ 刷新 token 失败: {e}", flush=True)
        return None

    print(f"[unitool_graph] 开始轮询 Inbox + JunkEmail (最多 {timeout}s)…", flush=True)
    poll = 0
    while time.time() < deadline:
        poll += 1
        # 同时搜索两个文件夹
        for folder_id in ("Inbox", "JunkEmail"):
            try:
                msgs = _graph_get_with_token(
                    f"/me/mailFolders/{folder_id}/messages"
                    "?$top=20&$orderby=receivedDateTime+desc"
                    "&$select=id,subject,bodyPreview,receivedDateTime,from",
                    token,
                )
                for msg in msgs.get("value", []):
                    mid = msg["id"]
                    if mid in seen_ids:
                        continue

                    recv_str = msg.get("receivedDateTime", "")
                    if recv_str:
                        try:
                            from datetime import datetime
                            recv_ts = datetime.fromisoformat(
                                recv_str.replace("Z", "+00:00")).timestamp()
                            if recv_ts < start_ts - 60:
                                seen_ids.add(mid)
                                continue
                        except Exception:
                            pass

                    subj   = msg.get("subject", "").lower()
                    prev   = msg.get("bodyPreview", "").lower()
                    sender = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()

                    hit = (
                        any(k in subj   for k in UNITOOL_KWS)
                        or any(k in prev   for k in UNITOOL_KWS)
                        or any(s in sender for s in UNITOOL_SENDERS)
                        or "unitool" in sender
                    )
                    if not hit:
                        seen_ids.add(mid)
                        continue

                    print(f"[unitool_graph] [{folder_id}] 候选: {msg.get('subject','')} from={sender}", flush=True)
                    detail = _graph_get_with_token(f"/me/messages/{mid}?$select=body", token)
                    body   = detail.get("body", {}).get("content", "") or ""
                    url    = _extract_unitool_verify_url(body)
                    if url:
                        print(f"[unitool_graph] ✅ [{folder_id}] 验证链接: {url[:100]}", flush=True)
                        return url
                    seen_ids.add(mid)
            except Exception as e:
                print(f"[unitool_graph] {folder_id} 查询异常: {e}", flush=True)

        print(f"[unitool_graph] poll#{poll} 未找到，等 10s…", flush=True)
        time.sleep(10)
        try:
            resp  = _refresh_access_token_raw(refresh_token)
            token = resp["access_token"]
        except Exception:
            pass

    print("[unitool_graph] ⏰ 超时，未收到 unitool 验证邮件", flush=True)
    return None


def _extract_unitool_verify_url(html: str) -> str | None:
    """从 unitool 邮件 HTML 中提取验证链接"""
    candidates = re.findall(r'https?://[^\s"\'<>\)]+', html)
    priority = (
        "unitool.ai/en/verify", "unitool.ai/verify", "unitool.ai/confirm",
        "unitool.ai/activate", "unitool.ai/email", "unitool.ai/en/entry?token",
        "unitool.ai/en/entry?verify",
    )
    fallback = ("unitool", "verify", "confirm", "activate", "token")
    for url in candidates:
        if any(k in url.lower() for k in priority):
            return url.rstrip(".,)")
    for url in candidates:
        if "unitool.ai" in url.lower() and any(k in url.lower() for k in fallback):
            return url.rstrip(".,)")
    for url in candidates:
        if any(k in url.lower() for k in fallback) and len(url) > 60:
            return url.rstrip(".,)")
    return None


# ─── MS account 6-digit OTP (suspicious login / new device / account recovery) ───
MS_OTP_SUBJ_KWS = (
    "microsoft account", "security code", "verification code",
    "verify your", "confirm your", "unusual sign",
    "microsoft 帐户", "安全代码", "验证码", "确认",
)
MS_OTP_BODY_KWS = (
    "security code", "verification code", "is your microsoft",
    "安全代码", "验证码",
)


def wait_for_ms_otp(
    refresh_token: str,
    timeout: int = 180,
    after_ts: float | None = None,
) -> str | None:
    """Poll Inbox + JunkEmail for a Microsoft 6-digit OTP email.

    Use cases: suspicious-login safety check, new-device verification,
    account-recovery email code.

    after_ts: Unix timestamp before which emails are ignored (prevents
              reading a stale OTP that arrived before registration started).
    Returns: 6-digit string, or None on timeout.
    """
    start_ts = after_ts or (time.time() - 60)
    deadline = time.time() + timeout
    seen_ids: set = set()

    print("[ms_otp] refreshing access_token...", flush=True)
    try:
        resp = _refresh_access_token_raw(refresh_token)
        token = resp["access_token"]
    except Exception as e:
        print(f"[ms_otp] ERROR refreshing token: {e}", flush=True)
        return None

    print(f"[ms_otp] polling Inbox + JunkEmail (max {timeout}s)...", flush=True)
    poll = 0
    while time.time() < deadline:
        poll += 1
        for folder_id in ("Inbox", "JunkEmail"):
            try:
                msgs = _graph_get_with_token(
                    f"/me/mailFolders/{folder_id}/messages"
                    "?$top=20&$orderby=receivedDateTime+desc"
                    "&$select=id,subject,bodyPreview,receivedDateTime,from",
                    token,
                )
                for msg in msgs.get("value", []):
                    mid = msg["id"]
                    if mid in seen_ids:
                        continue
                    # Skip emails that arrived before registration
                    recv_str = msg.get("receivedDateTime", "")
                    if recv_str:
                        try:
                            from datetime import datetime
                            recv_ts = datetime.fromisoformat(
                                recv_str.replace("Z", "+00:00")).timestamp()
                            if recv_ts < start_ts - 30:
                                seen_ids.add(mid)
                                continue
                        except Exception:
                            pass
                    subj = msg.get("subject", "").lower()
                    prev = msg.get("bodyPreview", "").lower()
                    sender = (
                        msg.get("from", {})
                        .get("emailAddress", {})
                        .get("address", "")
                        .lower()
                    )
                    # Accept: official Microsoft sender domains
                    is_ms = (
                        "microsoft.com" in sender
                        or "account.microsoft" in sender
                        or "accountprotection" in sender
                        or "msa" in sender
                    )
                    # Accept: subject/preview contains OTP keywords
                    has_kw = any(k in subj for k in MS_OTP_SUBJ_KWS) or any(
                        k in prev for k in MS_OTP_BODY_KWS
                    )
                    if not (is_ms or has_kw):
                        seen_ids.add(mid)
                        continue
                    print(
                        f"[ms_otp] [{folder_id}] candidate: "
                        f"{msg.get('subject','')} from={sender}",
                        flush=True,
                    )
                    detail = _graph_get_with_token(
                        f"/me/messages/{mid}?$select=body", token
                    )
                    body = (detail.get("body") or {}).get("content", "") or ""
                    otp = _extract_ms_otp(body or prev)
                    if otp:
                        print(f"[ms_otp] [{folder_id}] OTP: {otp}", flush=True)
                        return otp
                    seen_ids.add(mid)
            except Exception as e:
                print(f"[ms_otp] {folder_id} error: {e}", flush=True)
        remaining = int(deadline - time.time())
        print(f"[ms_otp] poll#{poll} not found, sleeping 10s (remaining {remaining}s)", flush=True)
        time.sleep(10)
        # Refresh token every ~60 s to prevent expiry during long waits
        if poll % 6 == 0:
            try:
                resp = _refresh_access_token_raw(refresh_token)
                token = resp["access_token"]
            except Exception:
                pass
    print("[ms_otp] timeout: no Microsoft OTP email received", flush=True)
    return None


def _extract_ms_otp(text: str) -> str | None:
    """Extract a 6-digit Microsoft OTP from email body (HTML or plain text).

    Prioritizes codes adjacent to known OTP keywords.
    Excludes year-like patterns (19xx/20xx).
    """
    import re as _re
    if not text:
        return None
    # Strip HTML tags to simplify matching
    plain = _re.sub(r"<[^>]+>", " ", text)
    # Priority: keyword immediately followed by / preceding a 6-digit number
    priority = [
        r"(?:security code|verification code|安全代码|验证码)[^\d]{0,30}(\d{6})(?!\d)",
        r"(\d{6})(?!\d)[^\d]{0,30}(?:is your|Microsoft)",
        r"(?:code is|code:)\s*(\d{6})(?!\d)",
    ]
    for pat in priority:
        m = _re.search(pat, plain, _re.IGNORECASE)
        if m:
            return m.group(1)
    # Fallback: any isolated 6-digit sequence; exclude year-like values
    for c in _re.findall(r"(?<!\d)(\d{6})(?!\d)", plain):
        if c[:2] in ("19", "20") and int(c[2:]) < 100:
            continue
        return c
    return None




# ─────────────────────────────────────────────────────────────
# 5. Graph API 发件（Mail.Send scope）
# ─────────────────────────────────────────────────────────────

def send_mail_graph(
    refresh_token: str,
    to_address: str,
    subject: str,
    body: str,
    body_type: str = "HTML",
) -> dict:
    """通过 Microsoft Graph API 发送邮件（POST /me/sendMail）。

    参数:
        refresh_token: 账号的 refresh_token（用于获取 access_token）
        to_address:    收件人地址
        subject:       邮件主题
        body:          邮件正文（HTML 或纯文本）
        body_type:     "HTML" 或 "Text"（默认 "HTML"）

    返回:
        {"success": True} 或 {"success": False, "error": "..."}

    说明:
        Mail.Send scope 已在 outlook_register.py 与 auto_device_code.py 中申请。
        Graph API POST /me/sendMail 成功时返回 202 Accepted，无响应体。
    """
    try:
        access_token = _refresh_access_token(refresh_token)
    except Exception as e:
        return {"success": False, "error": f"token 刷新失败: {e}"}

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": body_type, "content": body},
            "toRecipients": [{"emailAddress": {"address": to_address}}],
        },
        "saveToSentItems": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        data=data,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=20)
        return {"success": True}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return {"success": False, "error": f"HTTP {e.code}: {err_body[:300]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "unitool":
        print(wait_for_unitool_verify(sys.argv[2], timeout=60))
    elif len(sys.argv) > 2 and sys.argv[1] == "ms_otp":
        print(wait_for_ms_otp(sys.argv[2], timeout=120))
    elif len(sys.argv) > 1:
        print(wait_for_replit_verify(sys.argv[1], timeout=60))
    else:
        print(wait_for_cursor_otp(timeout=60))
