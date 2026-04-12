#!/usr/bin/env python3
"""
Outlook IMAP 收件箱读取器
连接 outlook.office365.com:993，读取近期邮件，提取验证链接和正文
"""
import imaplib, email as email_lib, json, sys, re
from email.header import decode_header, make_header

IMAP_HOST = "outlook.office365.com"
IMAP_PORT = 993


def decode_subject(raw):
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return raw or ""


def extract_urls(text: str):
    raw = re.findall(r'https?://[^\s"<>\]\)\']+', text)
    urls = [u.rstrip(".,;:") for u in raw]
    verify_urls = [
        u for u in urls
        if any(k in u.lower() for k in [
            "verify", "confirm", "activate", "click", "token",
            "reset", "link", "auth", "oauth", "email", "account",
            "microsoft", "live.com", "outlook.com", "signup",
        ])
    ]
    return urls[:20], verify_urls[:10]


def get_body(msg) -> tuple[str, str]:
    """Return (plain_text, html_text)"""
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                charset = part.get_content_charset("utf-8") or "utf-8"
                try:
                    payload = part.get_payload(decode=True)
                    decoded = payload.decode(charset, errors="replace")
                    if ct == "text/plain" and not plain:
                        plain = decoded
                    elif ct == "text/html" and not html:
                        html = decoded
                except Exception:
                    pass
    else:
        charset = msg.get_content_charset("utf-8") or "utf-8"
        try:
            payload = msg.get_payload(decode=True)
            plain = payload.decode(charset, errors="replace")
        except Exception:
            pass
    return plain, html


def parse_flags(flag_bytes) -> bool:
    """Return True if \\Seen flag is present."""
    try:
        return b"\\Seen" in flag_bytes
    except Exception:
        return False


def fetch_inbox(address: str, password: str, limit: int = 25, folder: str = "INBOX", search: str = ""):
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(address, password)

        # 选择文件夹（不区分大小写尝试）
        status, _ = mail.select(folder)
        if status != "OK":
            # 尝试列出可用文件夹找到最接近的
            _, folder_list = mail.list()
            folder_names = []
            for f in (folder_list or []):
                if isinstance(f, bytes):
                    parts = f.decode("utf-8", errors="replace").split('"')
                    if parts:
                        folder_names.append(parts[-1].strip())
            mail.logout()
            return {"success": False, "error": f"文件夹 '{folder}' 不存在，可用: {folder_names[:10]}"}

        # 搜索条件
        if search:
            search_criteria = f'(OR SUBJECT "{search}" FROM "{search}")'
        else:
            search_criteria = "ALL"

        status, data = mail.search(None, search_criteria)
        if status != "OK":
            # 搜索语法不支持时回退 ALL
            status, data = mail.search(None, "ALL")

        if status != "OK":
            mail.logout()
            return {"success": False, "error": "邮件搜索失败"}

        message_ids = data[0].split()
        recent_ids = list(reversed(message_ids))[:limit]

        results = []
        for mid in recent_ids:
            try:
                # 获取 FLAGS + RFC822
                _, flag_data = mail.fetch(mid, "(FLAGS)")
                flag_bytes = flag_data[0] if flag_data else b""
                is_read = b"\\Seen" in (flag_bytes if isinstance(flag_bytes, bytes) else str(flag_bytes).encode())

                _, msg_data = mail.fetch(mid, "(RFC822)")
                for response in msg_data:
                    if not isinstance(response, tuple):
                        continue
                    msg = email_lib.message_from_bytes(response[1])
                    subject = decode_subject(msg.get("Subject", ""))
                    from_raw = msg.get("From", "")
                    date = msg.get("Date", "")

                    plain, html = get_body(msg)
                    body_for_urls = html or plain
                    preview = re.sub(r"<[^>]+>", " ", plain or html or "")
                    preview = re.sub(r"\s+", " ", preview).strip()[:400]
                    all_urls, verify_urls = extract_urls(body_for_urls)

                    results.append({
                        "subject": subject,
                        "from": from_raw,
                        "date": date,
                        "preview": preview,
                        "body_html": html,
                        "body_plain": plain,
                        "urls": all_urls,
                        "verify_urls": verify_urls,
                        "is_read": is_read,
                    })
            except Exception as e:
                results.append({
                    "subject": f"[读取失败] {e}", "from": "", "date": "", "preview": "",
                    "body_html": "", "body_plain": "", "urls": [], "verify_urls": [], "is_read": False,
                })

        mail.logout()
        return {"success": True, "messages": results, "total": len(message_ids)}

    except imaplib.IMAP4.error as e:
        err = str(e)
        msg = f"IMAP 登录失败：{err}"
        if "AUTHENTICATIONFAILED" in err.upper():
            msg = (
                "IMAP 认证失败：微软已禁用基础密码认证。\n"
                "解决方法：登录 outlook.com → 设置 → 邮件 → 同步邮件 → 启用 IMAP，"
                "或使用「手动 OAuth 授权」。"
            )
        return {"success": False, "error": msg, "imap_error": True}
    except ConnectionRefusedError:
        return {"success": False, "error": f"连接 {IMAP_HOST}:{IMAP_PORT} 被拒绝", "imap_error": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "缺少参数"}))
        sys.exit(1)
    try:
        params = json.loads(sys.argv[1])
        result = fetch_inbox(
            params["email"],
            params["password"],
            params.get("limit", 25),
            params.get("folder", "INBOX"),
            params.get("search", ""),
        )
    except Exception as e:
        result = {"success": False, "error": str(e)}
    print(json.dumps(result, ensure_ascii=False))
