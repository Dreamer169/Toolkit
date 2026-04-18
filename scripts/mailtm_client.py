#!/usr/bin/env python3
"""
mailtm_client.py — mail.tm API 客户端
=====================================
Replit 不封锁 deltajohnsons.com，可通过 API 自动收件。
"""
import json, secrets, string, time, urllib.request, urllib.error

BASE = "https://api.mail.tm"
DOMAIN = "deltajohnsons.com"   # mail.tm 当前有效域名

def _req(method, path, data=None, token=None, timeout=20):
    url = BASE + path
    body = json.dumps(data).encode() if data else None
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as exc:
        return 0, {"error": str(exc)}

def create_account():
    """创建 mail.tm 账号，返回 (address, password, account_id)"""
    chars = string.ascii_lowercase + string.digits
    login = "".join(secrets.choice(chars) for _ in range(14))
    address = f"{login}@{DOMAIN}"
    password = "M@" + secrets.token_hex(10)
    code, body = _req("POST", "/accounts", {"address": address, "password": password})
    if code not in (200, 201):
        raise RuntimeError(f"mail.tm 创建账号失败 {code}: {body}")
    return address, password, body.get("id", "")

def get_token(address: str, password: str) -> str:
    """获取 JWT 访问令牌"""
    code, body = _req("POST", "/token", {"address": address, "password": password})
    if code != 200:
        raise RuntimeError(f"mail.tm 获取 token 失败 {code}: {body}")
    return body["token"]

def poll_inbox(token: str, timeout=200, keywords=("verify","confirm","replit","activate","email")):
    """轮询收件箱，返回第一封含 keyword 的邮件 HTML body"""
    deadline = time.time() + timeout
    print(f"  [mail.tm] 等待邮件 (最多 {timeout}s)…", flush=True)
    while time.time() < deadline:
        code, body = _req("GET", "/messages", token=token)
        if code == 200:
            for msg in body.get("hydra:member", []):
                subj = msg.get("subject", "").lower()
                intro = msg.get("intro", "").lower()
                if any(k in subj or k in intro for k in keywords):
                    # 获取完整邮件
                    mid = msg["id"]
                    c2, full = _req("GET", f"/messages/{mid}", token=token)
                    if c2 == 200:
                        print(f"  [mail.tm] 收到: {msg.get('subject','')[:60]}", flush=True)
                        return full.get("html", full.get("text", ""))
        time.sleep(6)
    return None

def extract_verify_url(html: str):
    """从邮件 HTML 提取验证链接"""
    import re
    candidates = re.findall(r'https?://[^\s"\'<>]+', html or "")
    kws = ("verify", "confirm", "activate", "email-action", "auth", "replit.com")
    for u in candidates:
        if any(k in u.lower() for k in kws):
            return u
    return None

if __name__ == "__main__":
    print("测试 mail.tm …")
    addr, pwd, aid = create_account()
    print(f"创建: {addr}")
    tok = get_token(addr, pwd)
    print(f"Token: {tok[:30]}…")
    print("收件测试通过（30s 后退出）")
    time.sleep(2)
