#!/usr/bin/env python3
"""
代理池管理器
- 轮询 (Round-Robin)，支持按组分组
- 403/429 自动加入黑名单 (可设冷却时间)
- 从数据库加载 / 支持直接传列表
- 线程安全
- 动态维护：定期检测有效性，清除无效，补充新有效，解封到期
"""

import json
import os
import threading
import time
import urllib.request
import urllib.error

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")

# 动态维护间隔（秒）
MAINTAIN_INTERVAL   = int(os.environ.get("PROXY_MAINTAIN_INTERVAL",   "120"))   # 维护周期
VALIDATE_TIMEOUT    = int(os.environ.get("PROXY_VALIDATE_TIMEOUT",    "8"))     # 单个代理检测超时
VALIDATE_TARGET_URL = os.environ.get("PROXY_VALIDATE_URL", "https://www.google.com")  # 检测目标


class ProxyPool:
    """线程安全的代理池，支持分组轮询 + 自动 ban"""

    def __init__(self, proxies=None, ban_seconds: int = 300):
        self._lock        = threading.RLock()
        self._ban_seconds = ban_seconds
        self._proxies     = []
        self._index       = 0
        self._group_index = {}

        if proxies:
            self._load(proxies)

    def _load(self, proxies):
        with self._lock:
            self._proxies     = [{**p, "banned_until": 0} for p in proxies]
            self._index       = 0
            self._group_index = {}

    def load_from_db(self):
        """从数据库加载 active 代理（增量合并，不清空已有状态）"""
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL)
            cur  = conn.cursor()
            cur.execute("""
                SELECT formatted, host, port, status
                FROM proxies
                WHERE (status IN ('active','idle') OR ((host = '127.0.0.1' AND port BETWEEN 1089 AND 1199) OR formatted = 'socks5://127.0.0.1:1089' OR formatted LIKE 'socks5://127.0.0.1:109%' OR formatted LIKE 'socks5://127.0.0.1:11%'))
                  AND NOT (host = '127.0.0.1' AND port BETWEEN 10820 AND 10845)
                ORDER BY
                  CASE WHEN ((host = '127.0.0.1' AND port BETWEEN 1089 AND 1199) OR formatted = 'socks5://127.0.0.1:1089' OR formatted LIKE 'socks5://127.0.0.1:109%' OR formatted LIKE 'socks5://127.0.0.1:11%') THEN 0 ELSE 1 END,
                  id
            """)
            rows = cur.fetchall()
            conn.close()
            new_urls = {r[0] for r in rows}

            with self._lock:
                existing_urls = {p["url"] for p in self._proxies}
                # 加入新增的
                for r in rows:
                    if r[0] not in existing_urls:
                        group = "subnode_bridge" if ((r[1] == "127.0.0.1" and 1089 <= int(r[2] or 0) <= 1199) or str(r[0]) == "socks5://127.0.0.1:1089" or str(r[0]).startswith("socks5://127.0.0.1:109") or str(r[0]).startswith("socks5://127.0.0.1:11")) else "default"
                        self._proxies.append({"url": r[0], "group": group, "banned_until": 0})
                # 移除数据库里已不存在的（非 active/idle 或已知死亡端口）
                self._proxies = [p for p in self._proxies if p["url"] in new_urls]

            print(f"[ProxyPool] 同步数据库完成，当前池 {len(self._proxies)} 个代理，其中有效 {len(self._active())} 个", flush=True)
        except Exception as e:
            print(f"[ProxyPool] 加载代理失败: {e}", flush=True)

    def load_from_list(self, proxy_urls, group="default"):
        proxies = [{"url": u, "group": group} for u in proxy_urls]
        self._load(proxies)

    def _active(self):
        now = time.time()
        return [p for p in self._proxies if p["banned_until"] <= now]

    def next(self):
        with self._lock:
            active = self._active()
            if not active:
                return None
            idx = self._index % len(active)
            self._index += 1
            return active[idx]["url"]

    def next_by_group(self, group: str):
        with self._lock:
            now     = time.time()
            matched = [p for p in self._proxies
                       if p["group"] == group and p["banned_until"] <= now]
            if not matched:
                return None
            idx = self._group_index.get(group, 0) % len(matched)
            self._group_index[group] = idx + 1
            return matched[idx]["url"]

    def ban(self, proxy_url: str, reason: str = ""):
        with self._lock:
            banned_until = time.time() + self._ban_seconds
            for p in self._proxies:
                if p["url"] == proxy_url:
                    p["banned_until"] = banned_until
                    print(f"[ProxyPool] ban {proxy_url} ({reason}), cooldown {self._ban_seconds}s", flush=True)
                    break
            try:
                import psycopg2
                from datetime import datetime, timedelta
                conn = psycopg2.connect(DATABASE_URL)
                cur  = conn.cursor()
                cur.execute("""
                    UPDATE proxies
                    SET status='banned', last_used=NOW()
                    WHERE formatted=%s
                """, (proxy_url,))
                conn.commit()
                conn.close()
            except Exception:
                pass

    def ban_on_http_error(self, proxy_url: str, status_code: int):
        if status_code in (403, 429):
            self.ban(proxy_url, reason=f"HTTP {status_code}")

    def unban_expired(self):
        """将已超过冷却时间的代理在数据库中恢复为 active"""
        try:
            import psycopg2
            from datetime import datetime
            conn = psycopg2.connect(DATABASE_URL)
            cur  = conn.cursor()
            cur.execute("""
                UPDATE proxies
                SET status='idle'
                WHERE status='banned' AND last_used < %s
            """, (datetime.utcnow(),))
            recovered = cur.rowcount
            conn.commit()
            conn.close()
            if recovered:
                print(f"[ProxyPool] 解封 {recovered} 个到期代理", flush=True)
        except Exception as e:
            print(f"[ProxyPool] unban_expired 失败: {e}", flush=True)

    # ── 有效性检测 ───────────────────────────────────────────────

    def _validate_proxy(self, proxy_url: str, timeout: int = VALIDATE_TIMEOUT) -> bool:
        """测试代理是否能通 VALIDATE_TARGET_URL，返回 True/False"""
        try:
            proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            opener = urllib.request.build_opener(proxy_handler)
            opener.addheaders = [("User-Agent", "ProxyValidator/1.0")]
            resp = opener.open(VALIDATE_TARGET_URL, timeout=timeout)
            return resp.status < 500
        except Exception:
            return False

    def validate_all(self, workers: int = 10):
        """并行检测所有代理，无效的标记 invalid 并从数据库移除"""
        with self._lock:
            snapshot = list(self._proxies)

        invalid_urls = []
        lock = threading.Lock()

        def check(p):
            ok = self._validate_proxy(p["url"])
            if not ok:
                with lock:
                    invalid_urls.append(p["url"])

        threads = []
        for p in snapshot:
            t = threading.Thread(target=check, args=(p,), daemon=True)
            threads.append(t)
            t.start()
            if len(threads) >= workers:
                for t2 in threads:
                    t2.join()
                threads = []
        for t in threads:
            t.join()

        if invalid_urls:
            print(f"[ProxyPool] 检测到 {len(invalid_urls)} 个失效代理，清除中...", flush=True)
            self._remove_invalid(invalid_urls)

        with self._lock:
            active_count = len(self._active())
        print(f"[ProxyPool] 有效性检测完成，当前有效: {active_count}/{len(snapshot)}", flush=True)
        return {"checked": len(snapshot), "invalid": len(invalid_urls), "removed": len(invalid_urls)}

    def _remove_invalid(self, invalid_urls: list):
        """内存 + 数据库同步删除失效代理"""
        invalid_set = set(invalid_urls)
        with self._lock:
            self._proxies = [p for p in self._proxies if p["url"] not in invalid_set]
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL)
            cur  = conn.cursor()
            cur.execute(
                "UPDATE proxies SET status='banned', last_used=NOW() WHERE formatted = ANY(%s)",
                (list(invalid_set),)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[ProxyPool] 数据库标记失效失败: {e}", flush=True)

    def stats(self) -> dict:
        with self._lock:
            now    = time.time()
            total  = len(self._proxies)
            banned = sum(1 for p in self._proxies if p["banned_until"] > now)
            return {
                "total":  total,
                "active": total - banned,
                "banned": banned,
            }

    def list(self):
        with self._lock:
            now = time.time()
            return [
                {
                    "url":    p["url"],
                    "group":  p["group"],
                    "status": "banned" if p["banned_until"] > now else "active",
                }
                for p in self._proxies
            ]


# ── 动态维护器 ─────────────────────────────────────────────────

class ProxyMaintainer:
    """
    后台线程：定期
      1. unban_expired()  — 解封冷却到期的代理
      2. load_from_db()   — 从数据库补充新有效代理
      3. validate_all()   — 检测并清除无效代理
    实时监控可通过 get_pool().stats() 查询。
    """

    def __init__(self, pool: "ProxyPool", interval: int = MAINTAIN_INTERVAL):
        self._pool     = pool
        self._interval = interval
        self._stop_evt = threading.Event()
        self._thread   = threading.Thread(target=self._run, daemon=True, name="ProxyMaintainer")

    def start(self):
        self._thread.start()
        print(f"[ProxyMaintainer] 启动，维护间隔 {self._interval}s", flush=True)

    def stop(self):
        self._stop_evt.set()

    def _run(self):
        while not self._stop_evt.wait(self._interval):
            try:
                ts = time.strftime("%H:%M:%S")
                print(f"[ProxyMaintainer] [{ts}] 开始维护周期 ...", flush=True)

                # Step 1: 解封到期
                self._pool.unban_expired()

                # Step 2: 同步数据库（补充新代理，移除已删除的）
                self._pool.load_from_db()

                # Step 3: 并行有效性检测 + 清除无效
                result = self._pool.validate_all(workers=10)

                stats = self._pool.stats()
                print(
                    f"[ProxyMaintainer] [{ts}] 维护完成 | "
                    f"检测={result['checked']} 清除={result['invalid']} "
                    f"| 池状态: 总={stats['total']} 有效={stats['active']} 封禁={stats['banned']}",
                    flush=True
                )
            except Exception as e:
                print(f"[ProxyMaintainer] 维护异常: {e}", flush=True)


# ── 全局单例 ───────────────────────────────────────────────────
_pool        = None
_maintainer  = None
_pool_lock   = threading.Lock()


def get_pool() -> ProxyPool:
    global _pool, _maintainer
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ProxyPool(ban_seconds=300)
                _pool.load_from_db()
                _maintainer = ProxyMaintainer(_pool, interval=MAINTAIN_INTERVAL)
                _maintainer.start()
    return _pool


if __name__ == "__main__":
    pool = get_pool()
    print(json.dumps(pool.stats(), indent=2))
    for p in pool.list():
        print(f"  {p['status']:6}  {p['url']}  [{p['group']}]")
    # 手动触发一次维护演示
    print("\n--- 手动触发有效性检测 ---")
    pool.validate_all()
    print(json.dumps(pool.stats(), indent=2))
