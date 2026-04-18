#!/usr/bin/env python3
"""
outlook_graph.py — Microsoft Graph API 邮件读取工具
支持:
  1. wait_for_cursor_otp(timeout)        — 从 /tmp/ms_tokens.json 读取 Cursor OTP
  2. wait_for_replit_verify(refresh_token, timeout) — 按账号 refresh_token 读取 Replit 验证链接
"""
import json, time, re, urllib.request, urllib.parse, urllib.error

CLIENT_ID  = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
TENANT     = "common"
TOKEN_FILE = "/tmp/ms_tokens.json"


def _refresh_access_token_raw(refresh_token: str) -> dict:
    """刷新并返回完整响应 dict"""
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "offline_access Mail.Read",
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
    req = urllib.request.Request(
        f"https://graph.microsoft.com/v1.0{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def _graph_get_with_token(path: str, access_token: str) -> dict:
    """用已有 access_token 直接查询（不走文件）"""
    req = urllib.request.Request(
        f"https://graph.microsoft.com/v1.0{path}",
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


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # 测试：传入 refresh_token
        print(wait_for_replit_verify(sys.argv[1], timeout=60))
    else:
        print(wait_for_cursor_otp(timeout=60))
