#!/usr/bin/env python3
"""
proxy_chain.py — 统一自适应代理链路模块 v1.0

所有注册/操作脚本通过此模块选取代理，无需各自维护代理逻辑。

代理池分层（按可信度排序）:
  Pool-A  local_socks5    127.0.0.1:10820-10845  本地 xray/CF 出口 (无凭据)
  Pool-B  subnode_bridge  127.0.0.1:1089-1199    子节点桥接 SOCKS5 (无凭据)
  Pool-C  webshare_http   http://user:pass@host   Webshare 商用 HTTP 代理
  Pool-D  external_socks5 socks5://user:pass@host 外部 SOCKS5 代理 (有凭据)

按目标选池优先级:
  outlook / microsoft  → Pool-A → Pool-B → Pool-C → direct
  ip2free / http_site  → Pool-C → Pool-B → Pool-A → direct
  cursor               → Pool-C → Pool-B → Pool-A → direct
  generic              → Pool-B → Pool-C → Pool-A → direct

使用:
    from proxy_chain import build_proxy_cfg, pick_adaptive, ProxyChain

    # 按目标用途自适应选取代理列表
    proxies = pick_adaptive("ip2free", count=3, db_url="postgresql://...")
    # → ["http://u:p@1.2.3.4:6754", "http://u:p@5.6.7.8:1234", ...]

    # 构建 Playwright 代理配置（scheme-aware，修复了 HTTP 代理走 Socks5Relay 的 Bug）
    cfg = build_proxy_cfg(proxies[0])
    # SOCKS5: {"server": "socks5://127.0.0.1:<relay_port>"}
    # HTTP:   {"server": "http://host:port", "username": "u", "password": "p"}
    # 无凭据: {"server": "socks5://127.0.0.1:10820"}
"""

from __future__ import annotations
import re, sys, os

__all__ = ["build_proxy_cfg", "pick_adaptive", "ProxyChain"]

# ── Pool 分类 ────────────────────────────────────────────────────────────────

POOL_TYPE_SQL = """
CASE
  WHEN formatted ILIKE 'http://%%'            THEN 'webshare_http'
  WHEN (host='127.0.0.1' AND port BETWEEN 10820 AND 10845)
                                             THEN 'local_socks5'
  WHEN (host='127.0.0.1' AND port BETWEEN 1089 AND 1199)
                                             THEN 'subnode_bridge'
  WHEN formatted ~ '^socks5h?://[^@]+@'     THEN 'external_socks5'
  ELSE 'other'
END
"""

# 按用途排列每个池的优先级（越靠前越优先）
PURPOSE_POOL_ORDER: dict[str, list[str]] = {
    "outlook":   ["local_socks5", "subnode_bridge", "webshare_http", "external_socks5"],
    "microsoft": ["local_socks5", "subnode_bridge", "webshare_http", "external_socks5"],
    "ip2free":   ["webshare_http", "subnode_bridge", "local_socks5", "external_socks5"],
    "cursor":    ["webshare_http", "subnode_bridge", "local_socks5", "external_socks5"],
    "generic":   ["subnode_bridge", "webshare_http", "local_socks5", "external_socks5"],
}

# ── build_proxy_cfg ───────────────────────────────────────────────────────────

_relay_refs: list = []  # 防止 GC 回收正在用的 Relay

def build_proxy_cfg(proxy: str) -> dict | None:
    """
    将代理 URL 转为 Playwright browser.launch(proxy=...) 接受的 dict。

    路由规则:
      socks5://user:pass@host:port  → Socks5Relay 本地中转（Chromium 不支持带认证 SOCKS5）
      socks5://127.0.0.1:PORT       → 直接传（本地无认证端口，Pool-A / Pool-B）
      http://user:pass@host:port    → Playwright 原生 username/password（Chromium 支持）
      http://host:port              → 直接传
      ""                            → None（不使用代理）
    """
    if not proxy:
        return None

    m = re.match(r"(socks5h?|http|https)://([^:]+):([^@]+)@([^:]+):(\d+)", proxy)
    if m:
        scheme, user, password, host, port = m.groups()
        if scheme in ("socks5", "socks5h"):
            # SOCKS5 有凭据 → 本地中转（Chromium 不支持带认证 SOCKS5）
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from socks5_relay import Socks5Relay
            relay = Socks5Relay(host, int(port), user, password)
            local_port = relay.start()
            _relay_refs.append(relay)
            print(f"[proxy_chain] SOCKS5 中转: 127.0.0.1:{local_port} → {host}:{port}", flush=True)
            return {"server": f"socks5://127.0.0.1:{local_port}", "bypass": "localhost"}
        else:
            # HTTP/HTTPS 有凭据 → Playwright 原生认证（无需中转）
            print(f"[proxy_chain] HTTP代理（原生认证）: {host}:{port}", flush=True)
            return {
                "server":   f"http://{host}:{port}",
                "username": user,
                "password": password,
                "bypass":   "localhost",
            }

    # 无凭据（本地 SOCKS5 端口 / 裸 HTTP）→ 直接传给 Chromium
    print(f"[proxy_chain] 无凭据代理（直接）: {proxy}", flush=True)
    return {"server": proxy, "bypass": "localhost"}


def stop_relays():
    """脚本结束时调用，停止所有 Socks5Relay 中转。"""
    for relay in _relay_refs:
        try:
            relay.stop()
        except Exception:
            pass


# ── pick_adaptive ─────────────────────────────────────────────────────────────

def pick_adaptive(purpose: str, count: int = 3, db_url: str = "") -> list[str]:
    """
    从数据库按用途自适应选取最优代理列表。

    优先级按 PURPOSE_POOL_ORDER[purpose] 顺序；同池内按 used_count ASC + RANDOM() 排序。
    不依赖 DB 时（db_url=""）直接返回空列表，调用方应 fallback 到手动代理。

    返回: ["http://user:pass@host:port", "socks5://127.0.0.1:10820", ...]
    """
    if not db_url:
        db_url = (
            os.environ.get("DATABASE_URL")
            or "postgresql://postgres:postgres@localhost/toolkit"
        )

    pool_order = PURPOSE_POOL_ORDER.get(purpose, PURPOSE_POOL_ORDER["generic"])
    n = max(1, min(20, count))

    try:
        import psycopg2
    except ImportError:
        print("[proxy_chain] ⚠ psycopg2 未安装，无法自适应选取代理", flush=True)
        return []

    results: list[str] = []
    try:
        conn = psycopg2.connect(db_url)
        cur  = conn.cursor()

        # 每种池按优先级取，总数不超过 n
        remaining = n
        for pool_type in pool_order:
            if remaining <= 0:
                break
            # webshare_http 必须有凭据（formatted LIKE '%@%'），排除无认证的 CF 隧道 IP
            cred_filter = "AND formatted LIKE '%%@%%'" if pool_type == "webshare_http" else ""
            cur.execute(f"""
                SELECT formatted
                FROM proxies
                WHERE status != 'banned'
                  AND ({POOL_TYPE_SQL}) = %s
                  {cred_filter}
                ORDER BY used_count ASC, RANDOM()
                LIMIT %s
            """, (pool_type, remaining))
            rows = cur.fetchall()
            if rows:
                for (fmt,) in rows:
                    results.append(fmt)
                remaining -= len(rows)
                print(f"[proxy_chain] {pool_type}: +{len(rows)} 个代理", flush=True)

        # 更新 used_count
        if results:
            cur.execute(
                "UPDATE proxies SET used_count = used_count + 1, last_used = NOW() WHERE formatted = ANY(%s)",
                (results,)
            )
            conn.commit()

        cur.close()
        conn.close()
    except Exception as e:
        print(f"[proxy_chain] ⚠ DB 选取失败: {e}", flush=True)

    print(f"[proxy_chain] 自适应选取 {len(results)}/{n} 个代理 (purpose={purpose})", flush=True)
    return results


# ── ProxyChain（重试迭代器） ──────────────────────────────────────────────────

class ProxyChain:
    """
    自适应代理链迭代器。

    用法:
        chain = ProxyChain("ip2free", extra=["http://user:pass@1.2.3.4:6754"])
        for proxy in chain:
            success, msg = try_register(proxy=proxy)
            if success:
                break
            chain.mark_failed(proxy)
    """
    def __init__(
        self,
        purpose: str,
        count:   int  = 5,
        extra:   list[str] | None = None,
        db_url:  str  = "",
    ):
        self.purpose = purpose
        self._proxies: list[str] = []
        self._failed: set[str]  = set()

        # DB 自适应选取
        db_proxies = pick_adaptive(purpose, count=count, db_url=db_url)
        self._proxies.extend(db_proxies)

        # 追加手动指定代理（优先插到最前）
        if extra:
            for p in extra:
                if p and p not in self._proxies:
                    self._proxies.insert(0, p)

        # 最后加一个 "" 代表无代理直连（兜底）
        self._proxies.append("")

    def __iter__(self):
        for proxy in self._proxies:
            if proxy not in self._failed:
                yield proxy

    def mark_failed(self, proxy: str):
        """标记此代理失败，下次迭代跳过。"""
        self._failed.add(proxy)
        if proxy:
            print(f"[proxy_chain] ❌ 代理标记失败: {proxy[:60]}", flush=True)

    def available(self) -> list[str]:
        return [p for p in self._proxies if p not in self._failed]

    def __len__(self):
        return len(self.available())
