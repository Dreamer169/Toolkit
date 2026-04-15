#!/usr/bin/env python3
"""
click_verify_link.py — 读取邮件正文，提取验证链接，用 patchright 浏览器访问。
用法: python3 click_verify_link.py '<json>'
JSON: { "token": "...", "message_id": "...", "verify_url": "" }
"""
import sys, json, re, urllib.request, urllib.parse, html as html_lib

if len(sys.argv) < 2:
    print(json.dumps({"success": False, "error": "缺少参数"}))
    sys.exit(1)

data = json.loads(sys.argv[1])
token       = data.get("token", "")
message_id  = data.get("message_id", "")
verify_url  = data.get("verify_url", "")   # 已知 URL 可直接传入
verify_url  = html_lib.unescape(verify_url) if verify_url else verify_url

# ── 若没有直接提供 URL，从 Graph API 拉取邮件正文提取 ───────────────────────
if not verify_url and message_id and token:
    try:
        safe_id = urllib.parse.quote(message_id, safe="")
        req = urllib.request.Request(
            f"https://graph.microsoft.com/v1.0/me/messages/{safe_id}?$select=body",
            headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            body_data = json.loads(r.read())
        html = body_data.get("body", {}).get("content", "")

        # 提取所有 href 里含 verify/confirm/activate 的 https URL
        candidates = re.findall(
            r'href=["\']?(https://[^\s"\'<>]+)',
            html, re.IGNORECASE
        )
        VERIFY_KWS = ("verify", "confirm", "activate", "validation", "email-action")
        urls = [u for u in candidates if any(kw in u.lower() for kw in VERIFY_KWS)]
        if not urls:
            # 退而求其次：取所有 replit.com 链接
            urls = [u for u in candidates if "replit.com" in u.lower()]
        verify_url = urls[0] if urls else ""
        verify_url = html_lib.unescape(verify_url)  # decode &amp; → &
        print(f"[click_verify] 提取到 {len(urls)} 个候选URL，使用: {verify_url[:120]}", flush=True)
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Graph API 拉取失败: {e}"}))
        sys.exit(0)

if not verify_url:
    print(json.dumps({"success": False, "error": "未找到验证链接，请手动检查邮件正文"}))
    sys.exit(0)

# ── 用 patchright 打开验证链接 ──────────────────────────────────────────────
try:
    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-extensions", "--mute-audio"]
        )
        ctx  = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = ctx.new_page()
        print(f"[click_verify] 正在访问: {verify_url[:120]}", flush=True)
        resp = page.goto(verify_url, timeout=30000, wait_until="networkidle")
        page.wait_for_timeout(6000)
        final_url = page.url
        title     = page.title()
        status    = resp.status if resp else 0
        print(f"[click_verify] 最终URL={final_url[:80]} 标题={title[:60]} HTTP={status}", flush=True)
        browser.close()
    print(json.dumps({
        "success": True,
        "verify_url": verify_url,
        "final_url": final_url,
        "title": title,
        "http_status": status,
    }))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "verify_url": verify_url}))
