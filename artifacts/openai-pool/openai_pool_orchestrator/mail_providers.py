"""
MailProvider 抽象层
支持 Mail.tm / MoeMail / DuckMail / 自定义兼容 API
"""

from __future__ import annotations

import itertools
import logging
import random
import re
import secrets
import string
import time
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Callable

import requests as _requests
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.exceptions import InsecureRequestWarning
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)
urllib3.disable_warnings(InsecureRequestWarning)


def _normalize_proxy_url(proxy: str) -> str:
    value = str(proxy or "").strip()
    if not value:
        return ""
    if "://" in value:
        return value
    if ":" in value:
        return f"http://{value}"
    return ""


class _ProxyAwareSession(_requests.Session):
    def __init__(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ):
        super().__init__()
        self._default_proxy = _normalize_proxy_url(proxy)
        self._proxy_selector = proxy_selector

    def request(self, method, url, **kwargs):
        selected_proxy = ""
        if self._proxy_selector:
            try:
                selected_proxy = _normalize_proxy_url(self._proxy_selector() or "")
            except Exception:
                selected_proxy = ""
        if not selected_proxy:
            selected_proxy = self._default_proxy
        base_kwargs = dict(kwargs)
        if selected_proxy and "proxies" not in base_kwargs:
            base_kwargs["proxies"] = {"http": selected_proxy, "https": selected_proxy}
        try:
            return super().request(method, url, **base_kwargs)
        except Exception:
            # 动态代理失败时，自动回退固定代理（若有）
            if (
                selected_proxy
                and self._default_proxy
                and selected_proxy != self._default_proxy
                and "proxies" not in kwargs
            ):
                fallback_kwargs = dict(kwargs)
                fallback_kwargs["proxies"] = {"http": self._default_proxy, "https": self._default_proxy}
                return super().request(method, url, **fallback_kwargs)
            raise


def _build_session(proxy: str = "", proxy_selector: Optional[Callable[[], str]] = None) -> _requests.Session:
    s = _ProxyAwareSession(proxy, proxy_selector)
    retry_total = 0 if proxy_selector else 2
    retry = Retry(
        total=retry_total,
        connect=retry_total,
        read=retry_total,
        status=retry_total,
        backoff_factor=0.2,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    fixed_proxy = _normalize_proxy_url(proxy)
    if fixed_proxy and not proxy_selector:
        s.proxies = {"http": fixed_proxy, "https": fixed_proxy}
    return s


def _strip_html(html: str) -> str:
    """Strip HTML tags and unescape entities to get plain text."""
    import html as _html_mod
    text = _html_mod.unescape(html)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_code(content: str) -> Optional[str]:
    if not content:
        return None
    # 1. Strip HTML and try contextual patterns on plain text
    plain = _strip_html(content) if "<" in content else content
    contextual_pats = [
        r"(?:verification|confirm|your|enter|use|code)[^\d]{0,30}(\d{6})",
        r"(\d{6})(?:[^\d]{0,30}(?:verification|confirm|code|otp))",
        r"Verification code:?\s*(\d{6})",
        r"code is\s*(\d{6})",
        r"\b(\d{6})\b",
    ]
    for pat in contextual_pats:
        m = re.search(pat, plain, re.IGNORECASE)
        if m:
            return m.group(1)
    # 2. Fallback: raw HTML, strip CSS hex colors to avoid false matches
    raw_stripped = re.sub(r"#[0-9A-Fa-f]{6}\b", "", content)
    m = re.search(r">\s*(\d{6})\s*<", raw_stripped)
    if m:
        return m.group(1)
    m = re.search(r"(?<![\d#&])\b(\d{6})\b", raw_stripped)
    if m:
        return m.group(1)
    return None

# ==================== 抽象基类 ====================

class MailProvider(ABC):
    @abstractmethod
    def create_mailbox(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ) -> Tuple[str, str]:
        """返回 (email, auth_credential)，auth_credential 是 bearer token 或 email_id"""

    @abstractmethod
    def wait_for_otp(
        self,
        auth_credential: str,
        email: str,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
        timeout: int = 120,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        """轮询获取6位验证码，超时返回空字符串"""

    def test_connection(self, proxy: str = "") -> Tuple[bool, str]:
        """测试 API 连通性，返回 (success, message)"""
        try:
            email, cred = self.create_mailbox(proxy)
            if email and cred:
                return True, f"成功创建测试邮箱: {email}"
            return False, "创建邮箱失败，请检查配置"
        except Exception as e:
            return False, f"连接失败: {e}"

    def close(self):
        pass


# ==================== Mail.tm ====================

class MailTmProvider(MailProvider):
    def __init__(self, api_base: str = "https://api.mail.tm"):
        self.api_base = api_base.rstrip("/")
        self._global_seen: Dict[str, set] = {}

    def _headers(self, token: str = "", use_json: bool = False) -> Dict[str, str]:
        h: Dict[str, str] = {"Accept": "application/json"}
        if use_json:
            h["Content-Type"] = "application/json"
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _get_domains(self, session: _requests.Session) -> List[str]:
        resp = session.get(f"{self.api_base}/domains", headers=self._headers(), timeout=15, verify=False)
        if resp.status_code != 200:
            return []
        data = resp.json()
        items = data if isinstance(data, list) else (data.get("hydra:member") or data.get("items") or [])
        domains = []
        for item in items:
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain") or "").strip()
            if domain and item.get("isActive", True) and not item.get("isPrivate", False):
                domains.append(domain)
        return domains

    def create_mailbox(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ) -> Tuple[str, str]:
        with _build_session(proxy, proxy_selector) as session:
            domains = self._get_domains(session)
            if not domains:
                return "", ""
            # 不优先 duckmail.sbs（OpenAI 会拒绝该域名）— 随机选一个
            domain = random.choice(domains)

            for _ in range(5):
                local = f"oc{secrets.token_hex(5)}"
                email = f"{local}@{domain}"
                password = secrets.token_urlsafe(18)

                resp = session.post(
                    f"{self.api_base}/accounts",
                    headers=self._headers(use_json=True),
                    json={"address": email, "password": password},
                    timeout=15, verify=False,
                )
                if resp.status_code not in (200, 201):
                    continue

                token_resp = session.post(
                    f"{self.api_base}/token",
                    headers=self._headers(use_json=True),
                    json={"address": email, "password": password},
                    timeout=15, verify=False,
                )
                if token_resp.status_code == 200:
                    token = str(token_resp.json().get("token") or "").strip()
                    if token:
                        return email, token
        return "", ""

    def wait_for_otp(
        self,
        auth_credential: str,
        email: str,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
        timeout: int = 120,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        with _build_session(proxy, proxy_selector) as session:
            if email not in self._global_seen:
                self._global_seen[email] = set()
            seen_ids = self._global_seen[email]
            start = time.time()

            while time.time() - start < timeout:
                if stop_event and stop_event.is_set():
                    return ""
                try:
                    resp = session.get(
                        f"{self.api_base}/messages",
                        headers=self._headers(token=auth_credential),
                        timeout=15, verify=False,
                    )
                    if resp.status_code != 200:
                        time.sleep(3)
                        continue

                    data = resp.json()
                    messages = data if isinstance(data, list) else (
                        data.get("hydra:member") or data.get("messages") or []
                    )

                    for msg in messages:
                        if not isinstance(msg, dict):
                            continue
                        msg_id = str(msg.get("id") or msg.get("@id") or "").strip()
                        if not msg_id or msg_id in seen_ids:
                            continue

                        if msg_id.startswith("/messages/"):
                            msg_id = msg_id.split("/")[-1]

                        detail_resp = session.get(
                            f"{self.api_base}/messages/{msg_id}",
                            headers=self._headers(token=auth_credential),
                            timeout=15, verify=False,
                        )
                        if detail_resp.status_code != 200:
                            continue
                        seen_ids.add(msg_id)

                        mail_data = detail_resp.json()
                        sender = str(((mail_data.get("from") or {}).get("address") or "")).lower()
                        subject = str(mail_data.get("subject") or "")
                        intro = str(mail_data.get("intro") or "")
                        text = str(mail_data.get("text") or "")
                        html = mail_data.get("html") or ""
                        if isinstance(html, list):
                            html = "\n".join(str(x) for x in html)
                        content = "\n".join([subject, intro, text, str(html)])

                        if "openai" not in sender and "openai" not in content.lower():
                            continue

                        code = _extract_code(content)
                        if code:
                            return code
                except Exception as exc:
                    logger.warning("Mail.tm 轮询验证码失败: %s", exc)
                time.sleep(3)
        return ""


# ==================== MoeMail ====================

class MoeMailProvider(MailProvider):
    def __init__(self, api_base: str, api_key: str):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self._global_seen: Dict[str, set] = {}

    def _headers(self) -> Dict[str, str]:
        return {"X-API-Key": self.api_key}

    def _get_domain(self, session: _requests.Session) -> Optional[str]:
        try:
            resp = session.get(
                f"{self.api_base}/api/config",
                headers=self._headers(), timeout=10, verify=False,
            )
            if resp.status_code == 200:
                data = resp.json()
                domains_str = data.get("emailDomains", "")
                if domains_str:
                    domains = [d.strip() for d in domains_str.split(",") if d.strip()]
                    if domains:
                        return random.choice(domains)
        except Exception as exc:
            logger.warning("MoeMail 读取域名配置失败: %s", exc)
        return None

    def create_mailbox(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ) -> Tuple[str, str]:
        with _build_session(proxy, proxy_selector) as session:
            domain = self._get_domain(session)
            if not domain:
                return "", ""

            chars = string.ascii_lowercase + string.digits
            prefix = "".join(random.choice(chars) for _ in range(random.randint(8, 13)))

            try:
                resp = session.post(
                    f"{self.api_base}/api/emails/generate",
                    json={"name": prefix, "domain": domain, "expiryTime": 0},
                    headers=self._headers(), timeout=15, verify=False,
                )
                if resp.status_code not in (200, 201):
                    return "", ""
                data = resp.json()
                email_id = data.get("id")
                email = data.get("email")
                if email_id and email:
                    return email, str(email_id)
            except Exception as exc:
                logger.warning("MoeMail 创建邮箱失败: %s", exc)
        return "", ""

    def wait_for_otp(
        self,
        auth_credential: str,
        email: str,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
        timeout: int = 120,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        with _build_session(proxy, proxy_selector) as session:
            email_id = auth_credential
            start = time.time()

            while time.time() - start < timeout:
                if stop_event and stop_event.is_set():
                    return ""
                try:
                    resp = session.get(
                        f"{self.api_base}/api/emails/{email_id}",
                        headers=self._headers(), timeout=15, verify=False,
                    )
                    if resp.status_code == 200:
                        messages = resp.json().get("messages") or []
                        for msg in messages:
                            if not isinstance(msg, dict):
                                continue
                            msg_id = msg.get("id")
                            if not msg_id:
                                continue
                            detail_resp = session.get(
                                f"{self.api_base}/api/emails/{email_id}/{msg_id}",
                                headers=self._headers(), timeout=15, verify=False,
                            )
                            if detail_resp.status_code == 200:
                                detail = detail_resp.json()
                                msg_obj = detail.get("message") or {}
                                content = msg_obj.get("content") or msg_obj.get("html") or ""
                                if not content:
                                    content = detail.get("text") or detail.get("html") or ""
                                code = _extract_code(content)
                                if code:
                                    return code
                except Exception as exc:
                    logger.warning("MoeMail 轮询验证码失败: %s", exc)
                time.sleep(3)
        return ""


# ==================== DuckMail ====================

class DuckMailProvider(MailProvider):
    def __init__(self, api_base: str = "https://api.duckmail.sbs", bearer_token: str = ""):
        self.api_base = api_base.rstrip("/")
        self.bearer_token = bearer_token
        self._global_seen: Dict[str, set] = {}

    def _auth_headers(self, token: str = "") -> Dict[str, str]:
        h: Dict[str, str] = {"Accept": "application/json"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def create_mailbox(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ) -> Tuple[str, str]:
        with _build_session(proxy, proxy_selector) as session:
            headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
            if self.bearer_token:
                headers["Authorization"] = f"Bearer {self.bearer_token}"

            try:
                domains_resp = session.get(f"{self.api_base}/domains", headers={"Accept": "application/json"}, timeout=15, verify=False)
                if domains_resp.status_code != 200:
                    return "", ""
                data = domains_resp.json()
                items = data if isinstance(data, list) else (data.get("hydra:member") or [])
                domains = [str(i.get("domain") or "") for i in items if isinstance(i, dict) and i.get("domain") and i.get("isActive", True)]
                if not domains:
                    return "", ""
                # 不优先 duckmail.sbs（OpenAI 会拒绝该域名）— 随机选一个
                domain = random.choice(domains)

                local = f"oc{secrets.token_hex(5)}"
                email = f"{local}@{domain}"
                password = secrets.token_urlsafe(18)

                resp = session.post(
                    f"{self.api_base}/accounts",
                    json={"address": email, "password": password},
                    headers=headers, timeout=30, verify=False,
                )
                if resp.status_code not in (200, 201):
                    return "", ""

                time.sleep(0.5)
                token_resp = session.post(
                    f"{self.api_base}/token",
                    json={"address": email, "password": password},
                    headers=headers, timeout=30, verify=False,
                )
                if token_resp.status_code == 200:
                    mail_token = token_resp.json().get("token")
                    if mail_token:
                        return email, str(mail_token)
            except Exception as exc:
                logger.warning("DuckMail 创建邮箱失败: %s", exc)
        return "", ""

    def wait_for_otp(
        self,
        auth_credential: str,
        email: str,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
        timeout: int = 120,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        with _build_session(proxy, proxy_selector) as session:
            if email not in self._global_seen:
                self._global_seen[email] = set()
            seen_ids = self._global_seen[email]
            start = time.time()

            while time.time() - start < timeout:
                if stop_event and stop_event.is_set():
                    return ""
                try:
                    resp = session.get(
                        f"{self.api_base}/messages",
                        headers=self._auth_headers(auth_credential),
                        timeout=30, verify=False,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        messages = data.get("hydra:member") or data.get("member") or data.get("data") or []
                        for msg in (messages if isinstance(messages, list) else []):
                            if not isinstance(msg, dict):
                                continue
                            msg_id = msg.get("id") or msg.get("@id")
                            if not msg_id or msg_id in seen_ids:
                                continue
                            raw_id = str(msg_id).split("/")[-1] if str(msg_id).startswith("/") else str(msg_id)

                            detail_resp = session.get(
                                f"{self.api_base}/messages/{raw_id}",
                                headers=self._auth_headers(auth_credential),
                                timeout=30, verify=False,
                            )
                            if detail_resp.status_code == 200:
                                seen_ids.add(msg_id)
                                detail = detail_resp.json()
                                content = detail.get("text") or detail.get("html") or ""
                                code = _extract_code(content)
                                if code:
                                    return code
                except Exception as exc:
                    logger.warning("DuckMail 轮询验证码失败: %s", exc)
                time.sleep(3)
        return ""


# ==================== Cloudflare Temp Email ====================

class CloudflareTempEmailProvider(MailProvider):
    def __init__(self, api_base: str = "", admin_password: str = "", domain: str = ""):
        self.api_base = api_base.rstrip("/")
        self.admin_password = admin_password
        self.domain = str(domain).strip()
        # 使用线程本地 token，避免多线程下邮箱 token 串用。
        self._tls = threading.local()
        self._global_seen: Dict[str, set] = {}

    def _get_random_domain(self) -> str:
        if not self.domain:
            return ""
        # 尝试按照 JSON 数组解析
        if self.domain.startswith("[") and self.domain.endswith("]"):
            try:
                import json
                domain_list = json.loads(self.domain)
                if isinstance(domain_list, list) and domain_list:
                    return random.choice([str(d).strip() for d in domain_list if str(d).strip()])
            except Exception:
                pass
        # 按照逗号分隔解析
        if "," in self.domain:
            parts = [d.strip() for d in self.domain.split(",") if d.strip()]
            if parts:
                return random.choice(parts)
        return self.domain

    @staticmethod
    def _message_matches_email(msg: Dict[str, Any], target_email: str) -> bool:
        target = str(target_email or "").strip().lower()
        if not target:
            return True

        def _extract_text_candidates(value: Any) -> List[str]:
            out: List[str] = []
            if isinstance(value, str):
                out.append(value)
            elif isinstance(value, dict):
                for k in ("address", "email", "name", "value"):
                    if value.get(k):
                        out.extend(_extract_text_candidates(value.get(k)))
            elif isinstance(value, list):
                for item in value:
                    out.extend(_extract_text_candidates(item))
            return out

        candidates: List[str] = []
        for key in ("to", "mailTo", "receiver", "receivers", "address", "email", "envelope_to"):
            if key in msg:
                candidates.extend(_extract_text_candidates(msg.get(key)))
        if not candidates:
            return True
        target_lower = target.lower()
        for raw in candidates:
            text = str(raw or "").strip().lower()
            if not text:
                continue
            if target_lower in text:
                return True
        return False

    def create_mailbox(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ) -> Tuple[str, str]:
        if not self.api_base or not self.admin_password or not self.domain:
            return "", ""

        with _build_session(proxy, proxy_selector) as session:
            try:
                # 生成5位字母 + 1-3位数字 + 1-3位字母的随机名
                letters1 = ''.join(random.choices(string.ascii_lowercase, k=5))
                numbers = ''.join(random.choices(string.digits, k=random.randint(1, 3)))
                letters2 = ''.join(random.choices(string.ascii_lowercase, k=random.randint(1, 3)))
                name = letters1 + numbers + letters2

                target_domain = self._get_random_domain()
                if not target_domain:
                    return "", ""

                resp = session.post(
                    f"{self.api_base}/admin/new_address",
                    json={
                        "enablePrefix": True,
                        "name": name,
                        "domain": target_domain,
                    },
                    headers={
                        "x-admin-auth": self.admin_password,
                        "Content-Type": "application/json"
                    },
                    timeout=30, verify=False,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    email = data.get("address")
                    jwt_token = data.get("jwt")
                    if email and jwt_token:
                        self._tls.jwt_token = jwt_token
                        return email, jwt_token
            except Exception as exc:
                logger.warning("Cloudflare 临时邮箱创建失败: %s", exc)
        return "", ""

    def wait_for_otp(
        self,
        auth_credential: str,
        email: str,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
        timeout: int = 120,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        token = str(auth_credential or "").strip() or str(getattr(self._tls, "jwt_token", "") or "").strip()
        if not token:
            return ""
        print(f"[CFMail] wait_for_otp 进入! email={email}, api_base={self.api_base}, jwt前16={token[:16] if token else 'EMPTY'}", flush=True)
        with _build_session(proxy, proxy_selector) as session:
            if email not in self._global_seen:
                self._global_seen[email] = set()
            seen_ids = self._global_seen[email]
            start = time.time()
            poll_count = 0

            while time.time() - start < timeout:
                if stop_event and stop_event.is_set():
                    print("[CFMail] stop_event 已触发，退出", flush=True)
                    return ""
                try:
                    poll_count += 1
                    url = f"{self.api_base}/api/mails?limit=10&offset=0"
                    resp = session.get(
                        url,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json"
                        },
                        timeout=30, verify=False,
                    )
                    print(f"[CFMail] 轮询#{poll_count} status={resp.status_code}, body前200={str(resp.text or '')[:200]}", flush=True)
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                        except Exception as je:
                            print(f"[CFMail] JSON解析失败: {je}", flush=True)
                            time.sleep(3)
                            continue
                        # API 返回字典 {"results": [...], "count": 0}，需正确提取
                        if isinstance(data, dict):
                            messages = data.get("results") or []
                        elif isinstance(data, list):
                            messages = data
                        else:
                            messages = []
                        print(f"[CFMail] 解析到 {len(messages)} 条邮件", flush=True)
                        for msg in messages:
                            if not isinstance(msg, dict):
                                continue
                            if not self._message_matches_email(msg, email):
                                continue
                            msg_id = msg.get("id")
                            if not msg_id or msg_id in seen_ids:
                                continue
                            seen_ids.add(msg_id)

                            content = msg.get("text") or msg.get("html") or ""
                            # Cloudflare Temp Email 将邮件原文放在 raw 字段（MIME 格式）
                            if not content and msg.get("raw"):
                                try:
                                    import email as _email_mod
                                    from email import policy
                                    parsed = _email_mod.message_from_string(msg["raw"], policy=policy.default)
                                    # 优先取纯文本
                                    body = parsed.get_body(preferencelist=('plain', 'html'))
                                    if body:
                                        content = body.get_content() or ""
                                    if not content:
                                        # 回退：遍历所有 part
                                        for part in parsed.walk():
                                            ctype = part.get_content_type()
                                            if ctype in ("text/plain", "text/html"):
                                                payload = part.get_content()
                                                if payload:
                                                    content = str(payload)
                                                    break
                                except Exception as parse_err:
                                    print(f"[CFMail] MIME解析失败，回退raw: {parse_err}", flush=True)
                                    content = msg.get("raw", "")
                            print(f"[CFMail] 邮件id={msg_id}, 内容前200={content[:200]}", flush=True)
                            code = _extract_code(content)
                            if code:
                                print(f"[CFMail] 成功提取验证码: {code}", flush=True)
                                return code
                except Exception as e:
                    print(f"[CFMail] 轮询异常: {e}", flush=True)
                time.sleep(3)
        print("[CFMail] wait_for_otp 超时, 未获取到验证码", flush=True)
        return ""


# ==================== FreeMail ====================

class GuerrillaMailProvider(MailProvider):
    """GuerrillaMail provider - uses guerrillamailblock.com domain which OpenAI accepts."""
    BASE = "https://api.guerrillamail.com/ajax.php"

    def __init__(self) -> None:
        self._sid_cookies: Dict[str, Dict[str, str]] = {}
        self._global_seen: Dict[str, set] = {}

    def _save_session_cookies(self, sid: str, session: _requests.Session) -> None:
        if not sid:
            return
        self._sid_cookies[sid] = _requests.utils.dict_from_cookiejar(session.cookies)

    def _restore_session_cookies(self, sid: str, session: _requests.Session) -> None:
        cookies = self._sid_cookies.get(sid)
        if not cookies:
            return
        session.cookies.update(_requests.utils.cookiejar_from_dict(cookies))

    def create_mailbox(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ) -> Tuple[str, str]:
        with _build_session(proxy, proxy_selector) as session:
            try:
                resp = session.get(self.BASE, params={"f": "get_email_address"}, timeout=15, verify=False)
                if resp.status_code == 200:
                    data = resp.json()
                    email = data.get("email_addr", "").replace("\\040", " ")
                    sid = data.get("sid_token", "")
                    if email and sid:
                        self._save_session_cookies(sid, session)
                        return email, sid
            except Exception as exc:
                logger.warning("GuerrillaMail create_mailbox failed: %s", exc)
        return "", ""

    def wait_for_otp(
        self,
        auth_credential: str,
        email: str,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
        timeout: int = 120,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        sid = auth_credential
        with _build_session(proxy, proxy_selector) as session:
            self._restore_session_cookies(sid, session)
            start = time.time()
            if email not in self._global_seen:
                self._global_seen[email] = set()
            seen_ids = self._global_seen[email]
            seq = 0
            while time.time() - start < timeout:
                if stop_event and stop_event.is_set():
                    return ""
                try:
                    resp = session.get(
                        self.BASE,
                        params={"f": "check_email", "seq": seq, "sid_token": sid},
                        timeout=15, verify=False,
                    )
                    if resp.status_code == 200:
                        self._save_session_cookies(sid, session)
                        data = resp.json()
                        messages = data.get("list", [])
                        for msg in messages:
                            msg_id = str(msg.get("mail_id", ""))
                            if not msg_id or msg_id in seen_ids:
                                continue
                            seen_ids.add(msg_id)
                            detail_resp = session.get(
                                self.BASE,
                                params={"f": "fetch_email", "email_id": msg_id, "sid_token": sid},
                                timeout=15, verify=False,
                            )
                            if detail_resp.status_code == 200:
                                self._save_session_cookies(sid, session)
                                detail = detail_resp.json()
                                # Filter: only process OpenAI verification emails
                                sender = str(
                                    detail.get("mail_from", "")
                                    or detail.get("mail_sender", "")
                                    or msg.get("mail_from", "")
                                    or ""
                                ).lower()
                                subject = str(detail.get("mail_subject", "") or msg.get("mail_subject", "") or "").lower()
                                body = str(detail.get("mail_body", "") or "")
                                # Only check emails from OpenAI
                                if ("openai" not in sender and "openai" not in subject
                                        and "openai" not in body.lower()[:800]):
                                    continue
                                # Use robust code extraction (handles HTML, CSS noise)
                                code = _extract_code(body)
                                if code:
                                    return code
                        if messages:
                            seq = int(data.get("count", seq))
                except Exception as exc:
                    logger.warning("GuerrillaMail wait_for_otp error: %s", exc)
                time.sleep(5)
        return ""
class FreeMailProvider(MailProvider):
    def __init__(self, api_base: str, api_key: str):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _get_domains(self, session: _requests.Session) -> List[str]:
        try:
            resp = session.get(f"{self.api_base}/api/domains", headers=self._headers(), timeout=15, verify=False)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return []

    def create_mailbox(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ) -> Tuple[str, str]:
        with _build_session(proxy, proxy_selector) as session:
            domains = self._get_domains(session)
            if not domains:
                return "", ""
            
            # 使用 /api/generate 随机生成
            try:
                resp = session.get(f"{self.api_base}/api/generate", headers=self._headers(), timeout=15, verify=False)
                if resp.status_code == 200:
                    data = resp.json()
                    email = data.get("email")
                    if email:
                        return email, self.api_key
            except Exception as exc:
                logger.warning("FreeMail 创建邮箱失败: %s", exc)
        return "", ""

    def wait_for_otp(
        self,
        auth_credential: str,
        email: str,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
        timeout: int = 120,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        with _build_session(proxy, proxy_selector) as session:
            start = time.time()
            if email not in self._global_seen:
                self._global_seen[email] = set()
            seen_ids = self._global_seen[email]

            while time.time() - start < timeout:
                if stop_event and stop_event.is_set():
                    return ""
                try:
                    # 获取邮件列表
                    resp = session.get(
                        f"{self.api_base}/api/emails",
                        params={"mailbox": email, "limit": 20},
                        headers=self._headers(),
                        timeout=15, verify=False,
                    )
                    if resp.status_code == 200:
                        messages = resp.json()
                        for msg in messages:
                            msg_id = msg.get("id")
                            if not msg_id or msg_id in seen_ids:
                                continue
                            seen_ids.add(msg_id)

                            # 获取邮件详情以获取完整内容
                            detail_resp = session.get(
                                f"{self.api_base}/api/email/{msg_id}",
                                headers=self._headers(),
                                timeout=15, verify=False,
                            )
                            if detail_resp.status_code == 200:
                                detail = detail_resp.json()
                                sender = str(detail.get("sender") or "").lower()
                                subject = str(detail.get("subject") or "")
                                content = detail.get("content") or detail.get("html_content") or ""
                                
                                combined = f"{subject}\n{content}"
                                if "openai" in sender or "openai" in combined.lower():
                                    code = _extract_code(combined)
                                    if code:
                                        return code
                except Exception as exc:
                    logger.warning("FreeMail 轮询验证码失败: %s", exc)
                time.sleep(3)
        return ""


# ==================== 多提供商路由 ====================


class MultiMailRouter:
    """线程安全的多邮箱提供商路由器，支持轮询/随机/容错策略"""

    def __init__(self, config: Dict[str, Any]):
        providers_list: List[str] = config.get("mail_providers") or []
        provider_configs: Dict[str, Dict] = config.get("mail_provider_configs") or {}
        self.strategy: str = config.get("mail_strategy", "round_robin")

        if not providers_list:
            legacy = config.get("mail_provider", "mailtm")
            providers_list = [legacy]
            provider_configs = {legacy: config.get("mail_config") or {}}

        self._provider_names: List[str] = []
        self._providers: Dict[str, MailProvider] = {}
        self._failures: Dict[str, int] = {}
        self._lock = threading.RLock()
        self._counter = itertools.count()

        for name in providers_list:
            try:
                p = create_provider_by_name(name, provider_configs.get(name, {}))
                self._provider_names.append(name)
                self._providers[name] = p
                self._failures[name] = 0
            except Exception as e:
                logger.warning("创建邮箱提供商 %s 失败: %s", name, e)

        # DuckMail auto-add removed — OpenAI blocks duckmail domains at signup

        if not self._providers:
            if providers_list:
                raise RuntimeError(f"邮箱提供商配置无效: {', '.join(str(n) for n in providers_list)}")
            fallback = create_provider_by_name("mailtm", {})
            self._provider_names = ["mailtm"]
            self._providers = {"mailtm": fallback}
            self._failures = {"mailtm": 0}
            # DuckMail auto-add removed — OpenAI blocks duckmail domains at signup

    def next_provider(self) -> Tuple[str, MailProvider]:
        with self._lock:
            names = self._provider_names
            if not names:
                raise RuntimeError("无可用邮箱提供商")

            # Skip providers with too many consecutive failures if alternatives exist
            FAILURE_SKIP_THRESHOLD = 5
            healthy = [n for n in names if self._failures.get(n, 0) < FAILURE_SKIP_THRESHOLD]
            candidate_names = healthy if healthy else names  # fall back to all when all failing

            if self.strategy == "random":
                name = random.choice(candidate_names)
            elif self.strategy == "failover":
                name = min(names, key=lambda n: self._failures.get(n, 0))
            else:
                idx = next(self._counter) % len(candidate_names)
                name = candidate_names[idx]
            return name, self._providers[name]

    def providers(self) -> List[Tuple[str, MailProvider]]:
        with self._lock:
            return [(n, self._providers[n]) for n in self._provider_names]

    def report_success(self, provider_name: str) -> None:
        with self._lock:
            self._failures[provider_name] = max(0, self._failures.get(provider_name, 0) - 1)

    def report_failure(self, provider_name: str) -> None:
        with self._lock:
            self._failures[provider_name] = self._failures.get(provider_name, 0) + 1



class TempMailPlusProvider(MailProvider):
    BASE = "https://tempmail.plus/api"

    def __init__(self):
        self._global_seen: Dict[str, set] = {}

    def create_mailbox(self, proxy="", proxy_selector=None):
        import secrets as _sec
        local = "oa" + _sec.token_hex(6)
        email = local + "@tempmail.plus"
        return email, local

    def wait_for_otp(self, auth_credential, email, proxy="", proxy_selector=None, timeout=120, stop_event=None):
        local = auth_credential
        if "@" in email:
            local = email.split("@")[0]
        with _build_session(proxy, proxy_selector) as session:
            start = time.time()
            if email not in self._global_seen:
                self._global_seen[email] = set()
            seen_ids = self._global_seen[email]
            while time.time() - start < timeout:
                if stop_event and stop_event.is_set():
                    return ""
                try:
                    resp = session.get(self.BASE + "/mails", params={"email": local, "epin": "", "limit": 10}, timeout=15, verify=False)
                    if resp.status_code == 200:
                        data = resp.json()
                        for msg in data.get("mail_list", []):
                            mid = str(msg.get("id", ""))
                            if not mid or mid in seen_ids:
                                continue
                            seen_ids.add(mid)
                            sender = str(msg.get("from_mail", "") or "").lower()
                            subj = str(msg.get("subject", "") or "").lower()
                            if "openai" not in sender and "openai" not in subj:
                                continue
                            dr = session.get(self.BASE + "/mail", params={"email": local, "epin": "", "id": mid}, timeout=15, verify=False)
                            if dr.status_code == 200:
                                d = dr.json()
                                body = str(d.get("html", "") or d.get("text", "") or "")
                                code = _extract_code(body)
                                if code:
                                    return code
                except Exception as exc:
                    logger.warning("TempMailPlus wait_for_otp error: %s", exc)
                time.sleep(5)
        return ""


# ==================== Outlook Graph (已有账号) ====================

class OutlookGraphProvider(MailProvider):
    """
    使用数据库中已有的 Outlook 账号，通过 Microsoft Graph API 读取 OTP 验证码。
    配置项：
      api_server  - 本地 API 服务地址，默认 http://localhost:8080
      accounts    - Outlook 邮箱列表（逗号分隔字符串或 list）
    """

    GRAPH_BASE = "https://graph.microsoft.com/v1.0"

    def __init__(
        self,
        api_server: str = "http://localhost:8080",
        accounts=None,
    ):
        self._api_server = api_server.rstrip("/")
        if isinstance(accounts, str):
            self._accounts: List[str] = [a.strip() for a in accounts.split(",") if a.strip()]
        else:
            self._accounts = list(accounts or [])
        self._idx = 0
        self._lock = threading.Lock()
        # 跨 wait_for_otp 调用共享已读邮件ID（key=email）
        self._global_seen: Dict[str, set] = {}

    def _next_email(self) -> str:
        with self._lock:
            if not self._accounts:
                raise RuntimeError("OutlookGraphProvider: 未配置 accounts")
            email = self._accounts[self._idx % len(self._accounts)]
            self._idx += 1
            return email

    def _get_token(self, email: str) -> str:
        try:
            resp = _requests.get(
                f"{self._api_server}/api/data/accounts",
                params={"limit": 100, "platform": "outlook"},
                timeout=10,
            )
            if resp.status_code == 200:
                for acc in resp.json().get("data", []):
                    if acc.get("email", "").lower() == email.lower():
                        return acc.get("token", "") or ""
        except Exception as exc:
            logger.warning("OutlookGraphProvider._get_token: %s", exc)
        return ""

    def _refresh_token(self, email: str) -> str:
        try:
            resp = _requests.post(
                f"{self._api_server}/api/tools/outlook/refresh-token",
                json={"email": email},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return data.get("access_token", "") or data.get("token", "") or ""
        except Exception as exc:
            logger.warning("OutlookGraphProvider._refresh_token: %s", exc)
        return ""

    def _read_inbox(self, access_token: str) -> List[Dict]:
        try:
            resp = _requests.get(
                f"{self.GRAPH_BASE}/me/mailFolders/inbox/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "": 20,
                    "": "receivedDateTime desc",
                    "": "id,subject,from,body,receivedDateTime",
                },
                timeout=20,
            )
            if resp.status_code == 200:
                return resp.json().get("value", [])
        except Exception as exc:
            logger.warning("OutlookGraphProvider._read_inbox: %s", exc)
        return []

    def create_mailbox(self, proxy: str = "", proxy_selector=None) -> Tuple[str, str]:
        email = self._next_email()
        token = self._get_token(email)
        if not token:
            token = self._refresh_token(email)
        if not token:
            raise RuntimeError(f"OutlookGraphProvider: 无法获取 {email} 的 token")
        return email, token

    def wait_for_otp(
        self,
        auth_credential: str,
        email: str,
        proxy: str = "",
        proxy_selector=None,
        timeout: int = 120,
        stop_event=None,
    ) -> str:
        import datetime
        access_token = auth_credential
        start = time.time()
        # 使用实例级共享 seen_ids，避免多次调用时重复使用同一封 OTP 邮件
        with self._lock:
            if email not in self._global_seen:
                self._global_seen[email] = set()
            seen_ids = self._global_seen[email]
        # 只看15分钟内收到的邮件，不预加载历史邮件
        cutoff_ts = start - 900  # 15分钟前
        while True:
            if stop_event and stop_event.is_set():
                return ""
            elapsed = time.time() - start
            if elapsed > 0:
                time.sleep(min(6, max(0, timeout - elapsed)))
            if time.time() - start > timeout:
                break
            msgs = self._read_inbox(access_token)
            if not msgs:
                new_tok = self._refresh_token(email)
                if new_tok:
                    access_token = new_tok
                    msgs = self._read_inbox(access_token)
            for msg in msgs:
                mid = msg.get("id", "")
                if not mid or mid in seen_ids:
                    continue
                # 时间过滤：忽略15分钟前的邮件
                recv_str = msg.get("receivedDateTime", "") or ""
                if recv_str:
                    try:
                        recv_str_clean = recv_str.replace("Z", "+00:00")
                        recv_dt = datetime.datetime.fromisoformat(recv_str_clean)
                        recv_ts = recv_dt.timestamp()
                        if recv_ts < cutoff_ts:
                            seen_ids.add(mid)  # 太老的直接跳过
                            continue
                    except Exception:
                        pass
                seen_ids.add(mid)
                sender = str((msg.get("from") or {}).get("emailAddress", {}).get("address", "") or "").lower()
                subject = str(msg.get("subject", "") or "").lower()
                if not any(k in sender or k in subject for k in ("openai", "noreply@tm", "verification", "verify", "code")):
                    continue
                body = str((msg.get("body") or {}).get("content", "") or "")
                code = _extract_code(body)
                if code:
                    return code
        return ""


# ==================== 工厂函数 ====================


def create_provider_by_name(provider_type: str, mail_cfg: Dict[str, Any]) -> MailProvider:
    """根据提供商名称和单独配置创建实例"""
    provider_type = provider_type.lower().strip()
    api_base = str(mail_cfg.get("api_base", "")).strip()

    if provider_type == "moemail":
        return MoeMailProvider(
            api_base=api_base or "https://your-moemail-api.example.com",
            api_key=str(mail_cfg.get("api_key", "")).strip(),
        )
    elif provider_type == "duckmail":
        return DuckMailProvider(
            api_base=api_base or "https://api.duckmail.sbs",
            bearer_token=str(mail_cfg.get("bearer_token", "")).strip(),
        )
    elif provider_type == "cloudflare_temp_email":
        return CloudflareTempEmailProvider(
            api_base=api_base,
            admin_password=str(mail_cfg.get("admin_password", "")).strip(),
            domain=str(mail_cfg.get("domain", "")).strip(),
        )
    elif provider_type == "guerrillamail":
        return GuerrillaMailProvider()
    elif provider_type == "tempmail":
        return TempMailPlusProvider()
    elif provider_type == "freemail":
        return FreeMailProvider(
            api_base=api_base,
            api_key=str(mail_cfg.get("api_key", "")).strip(),
        )
    elif provider_type == "mailtm":
        return MailTmProvider(api_base=api_base or "https://api.mail.tm")
    elif provider_type in ("outlook_graph", "outlook"):
        return OutlookGraphProvider(
            api_server=mail_cfg.get("api_server", "http://localhost:8080"),
            accounts=mail_cfg.get("accounts", []),
        )
    raise ValueError(f"未知邮箱提供商: {provider_type}")


def create_provider(config: Dict[str, Any]) -> MailProvider:
    """兼容旧配置格式的工厂函数"""
    provider_type = str(config.get("mail_provider", "mailtm")).lower()
    mail_cfg = config.get("mail_config") or {}
    return create_provider_by_name(provider_type, mail_cfg)
