#!/usr/bin/env python3
"""proxy_chain.py — 代理链路封装。
原文件被误删，现根据 ip2free_register.py / tools.ts 的调用方式重写。"""
import os
import re
import sys
import time
import urllib.parse
from typing import Iterator, List, Optional

# 让本文件能引用 /data/Toolkit/scripts/proxy_manager.py
_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

try:
    from proxy_manager import ProxyManager, PLATFORM_POLICIES
    _PM_AVAILABLE = True
except Exception as e:
    print(f"[proxy_chain] proxy_manager 不可用: {e}", file=sys.stderr)
    _PM_AVAILABLE = False


def build_proxy_cfg(proxy_url: str) -> Optional[dict]:
    """把代理 URL 转成 Playwright 的 proxy 参数字典。"""
    if not proxy_url:
        return None
    proxy_url = proxy_url.strip()
    if proxy_url.lower() in ("direct", "none", "null"):
        return None
    parsed = urllib.parse.urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    if scheme == "socks5h":
        scheme = "socks5"
    if scheme not in ("http", "https", "socks5", "socks4"):
        raise ValueError(f"[proxy_chain] 不支持的代理协议: {scheme}")
    return {
        "server": proxy_url,
        "bypass": "localhost,127.0.0.1",
    }


class ProxyChain:
    """可迭代的代理列表，支持 mark_failed 剔除失效节点。

    行为约定：
    - 若调用方传入 extra（手动/账号绑定代理），优先使用这些代理；
    - 当 extra 为空且 purpose 不是 generic 时，才从 proxy_manager 代理池补录；
    - 当 extra 为空且 purpose 为 generic 时，默认使用直连（空字符串）。
    """

    def __init__(self, purpose: str = "generic", count: int = 3, extra: Optional[List[str]] = None):
        self.purpose = purpose
        self.count = max(1, count)
        self.extra = [p.strip() for p in (extra or []) if p.strip()]
        self.failed: set = set()
        self._proxies = self._build_list()

    def _build_list(self) -> List[str]:
        proxies = list(self.extra)
        if len(proxies) >= self.count:
            return proxies[:self.count]

        # 非 generic 用途（ip2free/outlook/cursor 等）且未传手动代理时，才从 proxy_manager 补录
        if self.purpose != "generic" and _PM_AVAILABLE:
            need = self.count - len(proxies)
            try:
                pm = ProxyManager()
                policy = PLATFORM_POLICIES.get(self.purpose, PLATFORM_POLICIES["generic"])
                sources = policy.get("preferred_sources", ["local_xray", "ip2free", "webshare", "proxyscrape"])
                for source in sources:
                    if len(proxies) >= self.count:
                        break
                    candidates = [
                        e for e in pm.db.all()
                        if e.source == source
                        and e.alive is not False
                        and not e.is_blacklisted()
                        and not e.is_expired()
                        and self.purpose not in e.not_for
                    ]
                    for c in candidates:
                        if len(proxies) >= self.count:
                            break
                        url = c.url
                        if url not in proxies:
                            proxies.append(url)
            except Exception as e:
                print(f"[proxy_chain] 从 proxy_manager 取代理失败: {e}", file=sys.stderr)

        # 最终兜底：如果没有拿到任何代理，放入一个空字符串代表直连
        if not proxies:
            proxies.append("")
        return proxies

    def __iter__(self) -> Iterator[str]:
        for p in self._proxies:
            if p not in self.failed:
                yield p

    def __len__(self) -> int:
        return len([p for p in self._proxies if p not in self.failed])

    def mark_failed(self, proxy: str):
        if proxy:
            self.failed.add(proxy)


def stop_relays():
    """旧版 relay 清理；当前实现无 relay，故为空操作。"""
    pass


def pick_adaptive(purpose: str, count: int = 3) -> List[str]:
    """返回按 purpose 优先级选取的代理 URL 列表。"""
    return list(ProxyChain(purpose=purpose, count=count))
