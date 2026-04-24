"""Per-host Google routing for the python-side CDP context.

When the page loads reCAPTCHA Enterprise (www.google.com / www.gstatic.com /
www.recaptcha.net), the request normally exits via the browser's network
stack — which under WARP is GCP IPs that reCAPTCHA Enterprise scores low.

This helper attaches a ``page.route`` /``context.route`` handler that pipes
those requests through a pool of vetted non-GCP SOCKS5 exits (xray subnodes
on 10820+), reproduces the request server-side via httpx[socks], and
fulfills the route with the upstream response. The browser still appears to
load the script normally; only the IP changes for that one origin.
"""
import asyncio
import os
import random
import re
from typing import Optional, Iterable

import httpx
try:
    from httpx_socks import AsyncProxyTransport  # type: ignore
except Exception:  # pragma: no cover
    AsyncProxyTransport = None  # type: ignore
try:
    import h2  # noqa: F401
    _HAS_H2 = True
except Exception:
    _HAS_H2 = False

DEFAULT_POOL = [
    # v7.78b: WARP-only — chromium 主代理走 datacenter SOCKS (POST signup 通) 同时 *.google
    # 流量全走 WARP (104.28.x = CF backbone, reCAPTCHA Enterprise 评分高). 不对称代理.
    "socks5://127.0.0.1:40000",
]
GOOGLE_HOST_RE = re.compile(
    r"(^|\.)("
    r"google\.com|gstatic\.com|recaptcha\.net|youtube\.com|googleapis\.com|"
    r"googleusercontent\.com|googletagmanager\.com|googleadservices\.com|"
    r"google-analytics\.com|doubleclick\.net|ytimg\.com"
    r")$",
    re.I,
)


def _load_pool() -> list[str]:
    raw = os.environ.get("GOOGLE_PROXY_POOL") or ",".join(DEFAULT_POOL)
    items = [s.strip() for s in raw.split(",") if s.strip()]
    return items or list(DEFAULT_POOL)


_POOL = _load_pool()
import sys as _gpr_sys
_gpr_sys.stderr.write(f"[gpr-DIAG] _POOL_len={len(_POOL)} first={_POOL[0] if _POOL else None} env_GPP={os.environ.get('GOOGLE_PROXY_POOL','UNSET')[:120]}\n")
_gpr_sys.stderr.flush()
_client_cache: dict[str, httpx.AsyncClient] = {}


def _get_client(proxy: str) -> Optional[httpx.AsyncClient]:
    if not AsyncProxyTransport:
        return None
    c = _client_cache.get(proxy)
    if c is not None:
        return c
    transport = AsyncProxyTransport.from_url(
        proxy,
        verify=False,
        retries=1,
    )
    c = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(20.0, connect=8.0),
        follow_redirects=False,
        verify=False,
        http2=_HAS_H2,
    )
    _client_cache[proxy] = c
    return c


_STRIP_REQ_HDRS = {
    "host", "connection", "content-length", "accept-encoding",
    "transfer-encoding", "expect", "upgrade",
}
_STRIP_RESP_HDRS = {
    "content-encoding", "content-length", "transfer-encoding",
    "connection", "keep-alive",
}


async def attach_google_proxy_routing(target, log=None) -> None:
    """Attach the per-host google route to a Playwright Page or BrowserContext.

    ``target`` may be a ``Page`` or ``BrowserContext``. We prefer attaching at
    the context level so reCAPTCHA iframes (separate Page instances) are
    covered too.
    """
    if AsyncProxyTransport is None:
        if log:
            log("[google-route-py] httpx_socks not installed; skipping")
        return

    pool = list(_POOL)
    if not pool:
        if log:
            log("[google-route-py] empty pool; skipping")
        return
    # v7.76 sticky-per-context: 一次 attach (= 一个 BrowserContext 生命周期 = 一次
    # signup attempt) 内, 所有 *.google / gstatic / recaptcha.net 子请求共用同一个
    # SOCKS5 出口 IP。旧逻辑用纯轮询 cursor +1, 导致一次提交内 page-load 走 IP-A、
    # _GRECAPTCHA cookie 写入走 IP-B、grecaptcha.execute() POST 走 IP-C ... Google
    # reCAPTCHA Enterprise 看到同一 client 的 NID/_GRECAPTCHA cookie 从 N 个不同 IP
    # 发出 → 评分降到 ~0 → token invalid → Replit 返回 code:1。
    # 修复后整个 ctx 内 IP 只一个, 评分能上来。不同 ctx 之间通过 random 起点
    # 分散, 避免所有 ctx 都撞到 pool[0]。
    _pinned_proxy = pool[random.randrange(len(pool))]
    if log:
        log(f"[google-route-py] ctx pinned to {_pinned_proxy} (sticky, pool={len(pool)})")

    def _pick() -> str:
        return _pinned_proxy

    async def handler(route, request):
        try:
            from urllib.parse import urlsplit
            host = urlsplit(request.url).hostname or ""
            if not GOOGLE_HOST_RE.search(host):
                await route.fallback()
                return
        except Exception:
            await route.fallback()
            return

        last_err: Optional[Exception] = None
        for attempt in range(2):
            proxy = _pick()
            client = _get_client(proxy)
            if client is None:
                break
            try:
                hdrs = {}
                for k, v in (request.headers or {}).items():
                    lk = k.lower()
                    if lk in _STRIP_REQ_HDRS or lk.startswith(":"):
                        continue
                    hdrs[k] = v
                body = request.post_data_buffer
                req = client.build_request(
                    request.method,
                    request.url,
                    headers=hdrs,
                    content=body,
                )
                resp = await client.send(req, stream=False)
                resp_headers = []
                for k, v in resp.headers.multi_items():
                    if k.lower() in _STRIP_RESP_HDRS:
                        continue
                    resp_headers.append((k, v))
                await route.fulfill(
                    status=resp.status_code,
                    headers=dict(resp_headers),
                    body=resp.content,
                )
                if log and attempt == 0 and request.resource_type in ("document", "xhr", "fetch"):
                    log(f"[google-route-py] {host} -> {proxy} {resp.status_code}")
                return
            except Exception as e:
                last_err = e
                if log:
                    log(f"[google-route-py] {host} via {proxy} attempt{attempt+1} err: {e}")
        if log:
            log(f"[google-route-py] FALLBACK {host} after retries: {last_err}")
        try:
            await route.fallback()
        except Exception:
            pass

    await target.route("**/*", handler)


async def aclose_all() -> None:
    for c in list(_client_cache.values()):
        try:
            await c.aclose()
        except Exception:
            pass
    _client_cache.clear()
