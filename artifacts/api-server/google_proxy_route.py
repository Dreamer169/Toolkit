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

# v7.87 — POOL CURATION (audited 2026-04-25)
# 原则继承自 0391f15 (e2e: tylerreyes307@outlook.com -> userId=58078470 score-token
# 2425chars one-shot pass): "google_proxy_route 池子已剔除 GCP 端口，纯非 GCP 出口"。
# 0391f15 当时移除 10827 (34.132.50.119 GOOGLE-CLOUD-PLATFORM) + 10829 (34.53.117.84
# Google LLC), 加入 10824 (Cogent/Kirino) + 10826 (DigitalOcean) + 10830
# (Cogent/MULTACOM)。
#
# 2026-04-25 重新探测 (curl --socks5 + ipinfo.io ASN), 发现 xray 上游订阅轮换后:
#   10820 → 159.89.91.17    AS14061 DigitalOcean       ✓ 通用 DC, 历史可用
#   10822 → 107.174.42.185  AS36352 HostPapa           ✓ 小型 colo
#   10823 → 128.14.66.101   AS21859 Zenlayer           ✓ CDN 性质, 边缘
#   10824 → 38.244.31.27    AS174   Cogent             ✓ 0391f15 原班 (telecom)
# ★ 10825 → 77.110.126.244  AS210644 AEZA GROUP        ★ 唯一近期实测真新签成功的端口
#                                                        (e2e: userId=58169318
#                                                         SignupNewUserResponse)
#   10826 → 165.232.148.158 AS14061 DigitalOcean       ✓ 0391f15 原班
#   10828 → 147.182.229.237 AS14061 DigitalOcean       ✓ 通用 DC
#   10830 → 38.146.28.146   AS174   Cogent             ✓ 0391f15 原班
# ✗ 10831 → 20.106.211.232  AS8075  Microsoft Azure    ✗ 超大规模云 (跟 GCP 同性质,
#                                                        reCAPTCHA Enterprise 评 0 分,
#                                                        违反 0391f15 "non-GCP exits" 原则)
#   10836 → 137.184.228.85  AS14061 DigitalOcean       ✓ 通用 DC
#   10837 → 23.95.88.103    AS36352 HostPapa           ✓ 小型 colo
#   10845 → 107.173.15.46   AS36352 HostPapa           ✓ 小型 colo
#
# 排序: ★10825 已知好 → 0391f15 原班 (10824/10826/10830) → 其余通用 DC。
# sticky-per-context (random.randrange) 选中后整 ctx 共用, 故池子干净度比顺序更重要。
# CLOUD_ASN_BLOCKLIST: 任何 hyperscale 云 ASN 都按 0391f15 原则剔除。后续 xray 节点
# 轮换重新打分时, 用 "curl --socks5 :PORT https://ipinfo.io/json" 复检 ASN, 命中
# Google/Microsoft/Amazon/Oracle/Tencent/Alibaba/Huawei 云 ASN 即从池中剔除。
DEFAULT_POOL = [
    "socks5://127.0.0.1:10825",  # ★ AEZA — 实测成功 (userId=58169318)
    "socks5://127.0.0.1:10824",  # 0391f15 原班 — Cogent
    "socks5://127.0.0.1:10826",  # 0391f15 原班 — DigitalOcean
    "socks5://127.0.0.1:10830",  # 0391f15 原班 — Cogent
    "socks5://127.0.0.1:10820",  # DigitalOcean
    "socks5://127.0.0.1:10822",  # HostPapa
    "socks5://127.0.0.1:10823",  # Zenlayer
    "socks5://127.0.0.1:10828",  # DigitalOcean
    # "socks5://127.0.0.1:10831",  # ✗ DROPPED — Microsoft Azure (AS8075) hyperscale cloud
    "socks5://127.0.0.1:10836",  # DigitalOcean
    "socks5://127.0.0.1:10837",  # HostPapa
    "socks5://127.0.0.1:10845",  # HostPapa
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

    # v7.78c: 在 broker reused-ctx (cf-warmup 已加载 /signup + reCAPTCHA cross-origin
    # iframes) 上裸调 ctx.route("**/*",h) 会让 playwright 把 handler back-fill 到所有
    # iframe targets, 与 reCAPTCHA enterprise 的 anchor/bframe 跨 origin frame 死锁,
    # 导致 await 永远不返回 → 上层 240s Node 超时杀进程。先 unroute_all 清空残留,
    # 再用 8s wait_for 包裹 ctx.route, 超时则 swallow 继续后续流程, 不让单步 hang。
    try:
        if hasattr(target, "unroute_all"):
            await asyncio.wait_for(target.unroute_all(behavior="ignoreErrors"), timeout=3.0)
    except Exception as _ue:
        if log:
            log(f"[google-route-py] unroute_all 跳过: {_ue}")
    try:
        await asyncio.wait_for(target.route("**/*", handler), timeout=8.0)
        if log:
            log(f"[google-route-py] route handler 安装完成 (target={type(target).__name__})")
    except asyncio.TimeoutError:
        if log:
            log("[google-route-py] ⚠ route 安装超时 >8s（broker reused-ctx 已有 cross-origin iframe），跳过 *.google 截流，chromium 主代理直出")
        return
    except Exception as _re:
        if log:
            log(f"[google-route-py] ⚠ route 安装异常: {_re}; 跳过 *.google 截流")
        return


async def aclose_all() -> None:
    for c in list(_client_cache.values()):
        try:
            await c.aclose()
        except Exception:
            pass
    _client_cache.clear()
