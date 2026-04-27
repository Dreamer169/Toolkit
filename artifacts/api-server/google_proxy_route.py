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
# v8.29 ROOT-FIX 2026-04-27 — xray.json deep-audit revealed two parallel
# outbound pools and DEFAULT_POOL was pointing at the WRONG one:
#   (A) in-socks-0..25  (port 10820-10845)  -> vless proxy-0..25 -> ALL flattened
#       to same CF Workers IP 172.64.159.138 (AS13335) by v8.26 hot-fix.
#   (B) ss-in-0..9      (port 10850-10859)  -> shadowsocks ss-out-0..9 -> 9
#       distinct REAL upstreams (US/KR/GB/TW/NL telecom + small DC).
# This is also why "previously solved" issue recurred: DEFAULT_POOL still points
# at pool (A), but xray subscription rotated pool (A) to single CF Worker.
# Fix: switch to pool (B) and add runtime ASN-probe self-healer below.
#
# Per-port ASN probe 2026-04-27 (curl --socks5 + ipinfo.io):
#   10850 DEAD
#   10851 US 156.146.38.169 AS60068 Datacamp Limited       (DC, acceptable)
#   10852 DEAD
#   10853 US 38.135.24.131  AS27284 Fourplex Telecom LLC   (small telecom ★★)
#   10854 KR 141.164.45.187 AS20473 The Constant Co (Vultr) (DC, acceptable)
#   10855 GB 141.98.101.182 AS9009  M247 Europe SRL        (DC, acceptable)
#   10856 DEAD
#   10857 TW 114.45.129.72  AS3462  Chunghwa Telecom       (NATIONAL ISP ★★★)
#   10858 DEAD
#   10859 NL 193.29.139.250 AS47172 Greenhost BV           (small hosting ★)
#
# Order = ASN-quality descending (residential-ISP first, hyperscale last).
# Sticky-per-context still applies: each ctx pins one and reuses for all
# google subrequests + sign-up POST (v8.27 IP-consistency).
DEFAULT_POOL = [
    "socks5://127.0.0.1:10857",  # ★★★ Chunghwa Telecom AS3462 (TW national ISP)
    "socks5://127.0.0.1:10853",  # ★★ Fourplex Telecom AS27284 (US small telecom)
    "socks5://127.0.0.1:10859",  # ★ Greenhost AS47172 (NL small hosting)
    "socks5://127.0.0.1:10854",  # Vultr AS20473 (DC, KR)
    "socks5://127.0.0.1:10855",  # M247 AS9009 (DC, GB)
    "socks5://127.0.0.1:10851",  # Datacamp AS60068 (DC, US)
    # 10850/10852/10856/10858 DEAD as of probe — self-healer drops at runtime.
    # Pool (A) 10820-10845 EXCLUDED — all map to CF AS13335 datacenter.
]

# v8.29 ASN-blocklist for runtime self-healing. Any port whose exit ASN matches
# is dropped from the active pool until next restart. Prevents "previously
# solved" regression when xray subscription rotates upstreams to a poisoned
# ASN (CF Workers, hyperscale cloud, etc).
CLOUD_ASN_BLOCKLIST = {
    "AS13335",  # Cloudflare (CF Workers/anycast — reCAPTCHA -> code:1)
    "AS15169",  # Google
    "AS16509",  # Amazon AWS
    "AS14618",  # Amazon AWS
    "AS8075",   # Microsoft Azure
    "AS396982", # Google Cloud
    "AS32934",  # Facebook
    "AS31898",  # Oracle Cloud
    "AS45102",  # Alibaba Cloud
    "AS132203", # Tencent Cloud
    "AS55990",  # Huawei Cloud
}
GOOGLE_HOST_RE = re.compile(
    r"(^|\.)("
    r"google\.com|gstatic\.com|recaptcha\.net|youtube\.com|googleapis\.com|"
    r"googleusercontent\.com|googletagmanager\.com|googleadservices\.com|"
    r"google-analytics\.com|doubleclick\.net|ytimg\.com"
    r")$",
    re.I,
)
# v8.26a — IP 一致性修复 (revised after v8.26 实测 page-closed bug 立修):
# v8.26 把 *.replit.com 全部走 socks5 → cdn.replit.com 8s timeout 累积 + reCAPTCHA
# cross-origin iframe deadlock → broker chromium page 被关闭 (复现 v7.78c 警告).
# v8.26a 修正: 只精准拦截 POST /api/v1/auth/sign-up — token verify 提交 IP 匹配
# token-mint 出口 IP, 其他 replit.com 请求 (cdn/static/sp/page nav/IDE) 继续走
# chromium direct, 避免 deadlock. 这是 architecturally minimal change: 唯一变
# 出口 IP 的请求 = 唯一被 google 验证 IP 一致性的请求.
SIGNUP_POST_PATH_RE = re.compile(
    r"^/api/v1/auth/sign-?up(/|$|\?)",
    re.I,
)


def _should_proxy(method: str, host: str, path: str) -> bool:
    """v8.26a: route via sticky socks5 if:
       - host ∈ google-family (token mint origin), OR
       - method=POST AND host=replit.com AND path=/api/v1/auth/sign-up
         (token submit must match mint IP → bypass code:1 invalid).
    """
    if not host:
        return False
    if GOOGLE_HOST_RE.search(host):
        return True
    if os.environ.get("DISABLE_REPLIT_ROUTE", "").strip() in ("1", "true", "yes"):
        return False
    if (method or "").upper() == "POST" and host.lower() in ("replit.com", "www.replit.com"):
        if SIGNUP_POST_PATH_RE.search(path or ""):
            return True
    return False


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



# v8.27 — sticky-pin registry for cross-module IP consistency.
# replit_register.py v7.75 route-force handler reads this to forward POST sign-up
# via the SAME socks5 IP that minted the recaptcha token (IP consistency root-fix).
_PINNED_BY_CTX: dict = {}

def get_pinned_proxy(target):
    try:
        return _PINNED_BY_CTX.get(id(target))
    except Exception:
        return None

def get_pinned_proxy_for_page(page):
    try:
        v = _PINNED_BY_CTX.get(id(page))
        if v: return v
        ctx = getattr(page, "context", None)
        if ctx is not None:
            return _PINNED_BY_CTX.get(id(ctx))
    except Exception:
        pass
    return None


_PROBED_ASN_CACHE: dict = {}  # proxy_url -> (ok: bool, asn: str, ip: str)

async def _probe_proxy_asn(proxy_url: str, timeout: float = 8.0):
    """v8.29 — probe a socks5 proxy via ipinfo.io. Returns (ok, asn, ip, org)."""
    if AsyncProxyTransport is None:
        return (True, "?", "?", "?")  # cannot probe, assume ok
    try:
        tr = AsyncProxyTransport.from_url(proxy_url)
        async with httpx.AsyncClient(transport=tr, timeout=timeout, http2=False, verify=True) as c:
            r = await c.get("https://ipinfo.io/json")
            j = r.json()
        org = (j.get("org") or "").strip()
        asn = ""
        if org.upper().startswith("AS"):
            parts = org.split(None, 1)
            asn = parts[0].upper()
        ip = j.get("ip", "?")
        return (True, asn, ip, org)
    except Exception:
        return (False, "", "", "")

async def _filter_pool_by_asn(pool, log=None):
    """v8.29 — keep only proxies whose ASN is NOT in CLOUD_ASN_BLOCKLIST and
    that respond to the probe within the timeout. Cached per-process to avoid
    re-probing on every attach. Self-healing: if xray rotates upstreams and a
    previously-good port becomes CF/cloud, this drops it on next process
    restart (or after cache invalidation).
    """
    keep = []
    for prx in pool:
        cached = _PROBED_ASN_CACHE.get(prx)
        if cached is None:
            ok, asn, ip, org = await _probe_proxy_asn(prx)
            _PROBED_ASN_CACHE[prx] = (ok, asn, ip, org)
            if log:
                tag = "OK" if ok and asn not in CLOUD_ASN_BLOCKLIST else ("DEAD" if not ok else "BLOCKED")
                log(f"[google-route-py][asn-probe] {prx} -> {tag} ip={ip} {org}")
        else:
            ok, asn, ip, org = cached
        if not ok:
            continue
        if asn in CLOUD_ASN_BLOCKLIST:
            continue
        keep.append(prx)
    return keep


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
    # v8.29 self-healer: drop any port whose exit ASN is on the cloud blocklist
    # OR fails the liveness check. Cached per-process (so we probe at most once
    # per port per process lifetime).
    pool = await _filter_pool_by_asn(pool, log=log)
    if not pool:
        if log:
            log("[google-route-py] empty pool after ASN filter; skipping (CHECK xray.json subscription rotation!)")
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
    try:
        _PINNED_BY_CTX[id(target)] = _pinned_proxy
    except Exception:
        pass
    if log:
        log(f"[google-route-py] ctx pinned to {_pinned_proxy} (sticky, pool={len(pool)})")

    def _pick() -> str:
        return _pinned_proxy

    async def handler(route, request):
        try:
            from urllib.parse import urlsplit
            parts = urlsplit(request.url)
            host = parts.hostname or ""
            path = parts.path or ""
            # v8.26b DEBUG — log every request for replit.com / sign-up so we can
            # confirm whether the handler is actually receiving the POST (root-cause
            # the "no [google-route-py] sign-up log" diagnostic miss).
            if log and (host.endswith("replit.com") or "sign-up" in path or "signup" in path):
                log(f"[google-route-py] DBG seen {request.method} {host}{path} rt={request.resource_type}")
            # v8.26a — _should_proxy(method, host, path) — replit narrowed to
            # POST /api/v1/auth/sign-up only (avoid v7.78c iframe-deadlock)
            if not _should_proxy(request.method, host, path):
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
