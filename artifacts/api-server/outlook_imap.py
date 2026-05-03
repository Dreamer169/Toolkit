#!/usr/bin/env python3
"""
Outlook IMAP 收件箱读取器
优先路径：XOAUTH2（access_token）→ imapclient.oauth2_login()  [与 hrhcode 相同]
备用路径：Basic Auth（password）    → imaplib.login()          [微软已对大多数账号封锁]
"""
import base64, email as email_lib, imaplib, json, re, sys, socket, ssl
from email.header import decode_header, make_header


# ── 代理支持：PySocks (SOCKS5 / HTTP CONNECT) ──────────────────────────────

def _make_proxy_socket(proxy_url: str):
    """
    按 proxy_url 创建代理 socket，供 IMAP4 子类使用。
    支持:
      socks5://[user:pass@]host:port   — SOCKS5（有/无认证）
      socks5://127.0.0.1:PORT          — 本地无认证 SOCKS5
      http://[user:pass@]host:port     — HTTP CONNECT 隧道
    """
    import re as _re
    m = _re.match(r'(socks5h?|http|https)://(?:([^:]+):([^@]*)@)?([^:]+):(\d+)', proxy_url)
    if not m:
        raise ValueError(f"无法解析代理URL: {proxy_url}")
    scheme, user, pw, phost, pport = m.groups()
    pport = int(pport)
    try:
        import socks as _socks
    except ImportError:
        raise ImportError("PySocks 未安装，请 pip install PySocks")
    sock = _socks.socksocket()
    if 'socks5' in scheme:
        sock.set_proxy(_socks.SOCKS5, phost, pport,
                       username=user or None, password=pw or None)
    else:
        sock.set_proxy(_socks.HTTP, phost, pport,
                       username=user or None, password=pw or None)
    return sock


class _ProxiedIMAP4SSL(imaplib.IMAP4):
    """imaplib.IMAP4_SSL 的代理版本：通过 PySocks 代理连接 IMAP over SSL。"""
    def __init__(self, host, port, proxy_url):
        self._imap_host   = host
        self._imap_port   = port
        self._proxy_url   = proxy_url
        self._ssl_ctx     = ssl.create_default_context()
        imaplib.IMAP4.__init__(self, host, port)

    def open(self, host="", port=None):
        raw = _make_proxy_socket(self._proxy_url)
        raw.connect((self._imap_host, self._imap_port))
        self.sock = self._ssl_ctx.wrap_socket(raw, server_hostname=self._imap_host)
        self.file = self.sock.makefile("rb")

    def read(self, size):      return self.file.read(size)
    def readline(self):        return self.file.readline()
    def send(self, data):      return self.sock.sendall(data)
    def shutdown(self):
        try: self.sock.close()
        except Exception: pass

IMAP_HOST = "outlook.office365.com"
IMAP_PORT = 993


# ── 工具函数 ────────────────────────────────────────────────────────────────

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
            "reset", "link", "auth", "email", "account",
            "microsoft", "live.com", "outlook.com", "signup",
        ])
    ]
    return urls[:20], verify_urls[:10]


def get_body(msg) -> tuple:
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
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
        ct = msg.get_content_type()
        try:
            payload = msg.get_payload(decode=True)
            decoded = payload.decode(charset, errors="replace")
            if ct == "text/html":
                html = decoded
            else:
                plain = decoded
        except Exception:
            pass
    return plain, html


def parse_message(mid, mail) -> dict:
    _, flag_data = mail.fetch(mid, "(FLAGS)")
    flag_bytes = flag_data[0] if flag_data else b""
    is_read = b"\\Seen" in (
        flag_bytes if isinstance(flag_bytes, bytes) else str(flag_bytes).encode()
    )
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
        return {
            "subject": subject,
            "from": from_raw,
            "date": date,
            "preview": preview,
            "body_html": html,
            "body_plain": plain,
            "urls": all_urls,
            "verify_urls": verify_urls,
            "is_read": is_read,
        }
    return {"subject": "[空]", "from": "", "date": "", "preview": "",
            "body_html": "", "body_plain": "", "urls": [], "verify_urls": [], "is_read": False}


# ── XOAUTH2 路径（imapclient）──────────────────────────────────────────────

def fetch_inbox_xoauth2(address: str, access_token: str,
                         limit: int = 25, folder: str = "INBOX", search: str = "", proxy: str = ""):
    """
    用 OAuth2 access_token 通过 XOAUTH2 登录 IMAP。
    与 hrhcode 相同：imapclient.oauth2_login(email, access_token)
    """
    try:
        from imapclient import IMAPClient
    except ImportError:
        return {"success": False, "error": "imapclient 未安装，请 pip install imapclient"}

    try:
        if proxy:
            # 代理模式：使用自定义 SOCKS5/HTTP 代理 socket (imaplib 路径)
            import base64 as _b64
            _mail = _ProxiedIMAP4SSL(IMAP_HOST, IMAP_PORT, proxy)
            _auth_str = f"user={address}\x01auth=Bearer {access_token}\x01\x01"
            _encoded  = _b64.b64encode(_auth_str.encode()).decode()
            _mail.authenticate("XOAUTH2", lambda x: _encoded)
            _mail.select(folder, readonly=True)
            typ, data = _mail.search(None, f'SUBJECT "{search}"' if search else "ALL")
            mids = (data[0] or b"").split()
            recent = list(reversed(mids))[:limit]
            results, total = [], len(mids)
            for mid in recent:
                try: results.append(parse_message(mid, _mail))
                except Exception: pass
            _mail.logout()
            return {"success": True, "messages": results, "total": total, "via": "xoauth2+proxy"}
        with IMAPClient(IMAP_HOST, IMAP_PORT, ssl=True, timeout=30.0) as client:
            client.oauth2_login(address, access_token)

            # 选择文件夹
            try:
                client.select_folder(folder, readonly=True)
            except Exception:
                client.select_folder("INBOX", readonly=True)
                folder = "INBOX"

            # 搜索
            if search:
                try:
                    uids = client.search(["OR", "SUBJECT", search, "FROM", search])
                except Exception:
                    uids = client.search(["ALL"])
            else:
                uids = client.search(["ALL"])

            recent_uids = list(reversed(uids))[:limit]
            total = len(uids)

            if not recent_uids:
                return {"success": True, "messages": [], "total": 0, "via": "xoauth2"}

            response = client.fetch(recent_uids, ["RFC822", "FLAGS", "INTERNALDATE"])
            results = []
            for uid in recent_uids:
                payload = response.get(uid)
                if not payload:
                    continue
                raw = payload.get(b"RFC822") or payload.get("RFC822")
                flags = payload.get(b"FLAGS") or payload.get("FLAGS", ())
                is_read = b"\\Seen" in flags

                if raw is None:
                    continue
                msg = email_lib.message_from_bytes(raw)
                subject = decode_subject(msg.get("Subject", ""))
                from_raw = msg.get("From", "")
                date = msg.get("Date", "")
                plain, html = get_body(msg)
                body_for_urls = html or plain
                preview = re.sub(r"<[^>]+>", " ", plain or html or "")
                preview = re.sub(r"\s+", " ", preview).strip()[:400]
                all_urls, verify_urls = extract_urls(body_for_urls)
                results.append({
                    "subject": subject, "from": from_raw, "date": date,
                    "preview": preview, "body_html": html, "body_plain": plain,
                    "urls": all_urls, "verify_urls": verify_urls, "is_read": is_read,
                })

        return {"success": True, "messages": results, "total": total, "via": "xoauth2"}

    except Exception as e:
        err = str(e)
        return {"success": False, "error": f"XOAUTH2 IMAP 失败：{err}", "via": "xoauth2"}


def check_login_xoauth2(address: str, access_token: str) -> dict:
    """用 access_token 测试 XOAUTH2 IMAP 登录（不拉邮件）。"""
    try:
        from imapclient import IMAPClient
    except ImportError:
        return {"success": False, "error": "imapclient 未安装"}
    try:
        with IMAPClient(IMAP_HOST, IMAP_PORT, ssl=True, timeout=20.0) as client:
            client.oauth2_login(address, access_token)
            client.select_folder("INBOX", readonly=True)
        return {"success": True, "via": "xoauth2"}
    except Exception as e:
        return {"success": False, "error": f"XOAUTH2 登录失败：{e}", "via": "xoauth2"}


# ── Basic Auth 路径（imaplib）──────────────────────────────────────────────

def fetch_inbox(address: str, password: str, limit: int = 25,
                folder: str = "INBOX", search: str = "", proxy: str = ""):
    """Basic Auth IMAP（微软已对大多数 Outlook.com 个人账号封锁）。"""
    try:
        mail = _ProxiedIMAP4SSL(IMAP_HOST, IMAP_PORT, proxy) if proxy else imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(address, password)

        status, _ = mail.select(folder)
        if status != "OK":
            _, folder_list = mail.list()
            folder_names = []
            for f in (folder_list or []):
                if isinstance(f, bytes):
                    parts = f.decode("utf-8", errors="replace").split('"')
                    if parts:
                        folder_names.append(parts[-1].strip())
            mail.logout()
            return {"success": False, "error": f"文件夹 '{folder}' 不存在，可用: {folder_names[:10]}"}

        if search:
            search_criteria = f'(OR SUBJECT "{search}" FROM "{search}")'
        else:
            search_criteria = "ALL"

        status, data = mail.search(None, search_criteria)
        if status != "OK":
            status, data = mail.search(None, "ALL")
        if status != "OK":
            mail.logout()
            return {"success": False, "error": "邮件搜索失败"}

        message_ids = data[0].split()
        recent_ids = list(reversed(message_ids))[:limit]
        total = len(message_ids)

        results = []
        for mid in recent_ids:
            try:
                results.append(parse_message(mid, mail))
            except Exception as e:
                results.append({"subject": f"[读取失败] {e}", "from": "", "date": "", "preview": "",
                                 "body_html": "", "body_plain": "", "urls": [], "verify_urls": [], "is_read": False})
        mail.logout()
        return {"success": True, "messages": results, "total": total, "via": "basic_auth"}

    except imaplib.IMAP4.error as e:
        err = str(e)
        return {"success": False, "error": f"IMAP 登录失败：{err}", "imap_error": True, "via": "basic_auth"}
    except Exception as e:
        return {"success": False, "error": str(e), "via": "basic_auth"}


def check_login(address: str, password: str) -> dict:
    """Basic Auth IMAP 登录测试（check_only）。"""
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(address, password)
        mail.logout()
        return {"success": True, "via": "basic_auth"}
    except imaplib.IMAP4.error as e:
        return {"success": False, "error": f"IMAP 登录失败：{e}", "via": "basic_auth"}
    except Exception as e:
        return {"success": False, "error": str(e), "via": "basic_auth"}


# ── 入口 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "缺少参数"}))
        sys.exit(1)
    try:
        params = json.loads(sys.argv[1])
        email   = params["email"]
        pw      = params.get("password", "")
        token   = params.get("access_token", "")
        limit   = params.get("limit", 25)
        folder  = params.get("folder", "INBOX")
        search  = params.get("search", "")
        check   = params.get("check_only", False)

        if token:
            # XOAUTH2 路径（与 hrhcode 相同）
            result = check_login_xoauth2(email, token) if check else fetch_inbox_xoauth2(email, token, limit, folder, search)
        else:
            # Basic Auth 路径（可能被微软封锁）
            result = check_login(email, pw) if check else fetch_inbox(email, pw, limit, folder, search)
    except Exception as e:
        result = {"success": False, "error": str(e)}
    print(json.dumps(result, ensure_ascii=False))
