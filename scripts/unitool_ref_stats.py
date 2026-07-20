#!/usr/bin/env python3
"""
unitool_ref_stats.py — 实时查询所有 unitool ref 账号的真实状态
=============================================================
用途:
  python3 unitool_ref_stats.py [--refresh]
    --refresh: 强制忽略缓存，重新查 API

输出: JSON 数组
  [{"id":909,"email":"c.murphy483@outlook.com","code":"QqWu3",
    "conversions":0,"earnings":0,"clicks":0,"remaining":10,
    "status":"active","cached":false}, ...]
"""
import argparse, json, os, re, subprocess, sys, time
import psycopg2

DB_URL             = "postgresql://postgres:postgres@localhost/toolkit"
CACHE_FILE         = "/tmp/unitool_ref_code_cache.json"
CACHE_TTL          = 14400  # 4 hours（防封：降低调用频率）
AUTH_COOKIE        = "__Secure-unitool-ssid"
MAX_CONVERSIONS    = 10

def load_cache() -> dict:
    try:
        return json.loads(open(CACHE_FILE).read())
    except Exception:
        return {}

def save_cache(c: dict) -> None:
    try:
        open(CACHE_FILE, "w").write(json.dumps(c))
    except Exception:
        pass

# 本地 Xray SOCKS5 代理端口（Xray 管理，每个 port 是不同住宅 IP 出口）
# 与 chain_v3 / unitool_batch_refcode 保持一致
RESI_PORTS = [10851, 10853, 10854, 10857, 10859, 10870, 10872, 10878, 10879]

_resi_idx = 0   # 全局轮转索引

def _next_resi_port() -> int:
    global _resi_idx
    port = RESI_PORTS[_resi_idx % len(RESI_PORTS)]
    _resi_idx += 1
    return port

def api_fetch(ssid: str) -> dict | None:
    """通过本地 Xray SOCKS5 代理调用 Unitool API，防止 VPS IP 被封。
    代理格式: --socks5-hostname 127.0.0.1:{port}（与 chain_v3/batch 一致）
    失败时降级为直连（GET 请求封号风险较低）。
    """
    port = _next_resi_port()
    for use_proxy in (True, False):   # 先走代理，失败降级直连
        try:
            cmd = ["curl", "-s",
                   "-b", f"{AUTH_COOKIE}={ssid}",
                   "-H", "Accept: application/json",
                   "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                   "--max-time", "10",
                   "https://unitool.ai/api/user/ref-code"]
            if use_proxy:
                cmd = ["curl", "-s",
                       "--socks5-hostname", f"127.0.0.1:{port}",
                       "-b", f"{AUTH_COOKIE}={ssid}",
                       "-H", "Accept: application/json",
                       "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                       "--max-time", "12",
                       "https://unitool.ai/api/user/ref-code"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=18)
            raw = r.stdout.strip()
            if raw == "null" or not raw:
                if use_proxy:
                    continue   # 代理失败 → 降级直连
                return None
            if raw.startswith("{"):
                return json.loads(raw)
            if use_proxy:
                continue
            return None
        except Exception:
            if use_proxy:
                continue
            return None
    return None

def api_session(ssid: str) -> dict:
    try:
        r = subprocess.run(
            ["curl", "-s", "-b", f"{AUTH_COOKIE}={ssid}",
             "-H", "Accept: application/json", "--max-time", "8",
             "https://unitool.ai/api/auth/session"],
            capture_output=True, text=True, timeout=12)
        d = json.loads(r.stdout)
        return d.get("auth", {}).get("user", {})
    except Exception:
        return {}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="强制忽略缓存")
    args = ap.parse_args()

    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    # 查所有已注册 unitool 且有 ssid 的账号
    cur.execute("""
        SELECT id, email, notes, tags FROM accounts
        WHERE platform='outlook'
          AND tags LIKE '%unitool_registered%'
          AND notes LIKE '%unitool_ssid=%'
        ORDER BY id DESC
    """)
    rows = cur.fetchall()
    conn.close()

    cache = {} if args.refresh else load_cache()
    results = []
    now = time.time()

    for acc_id, email, notes, tags in rows:
        ssid_m = re.search(r"unitool_ssid=([0-9a-f]{40,})", notes or "")
        if not ssid_m:
            continue
        ssid = ssid_m.group(1)
        key  = str(acc_id)

        # 判断角色
        role = "registered"
        if "unitool_ref_master"    in (tags or ""): role = "master"
        elif "unitool_ref_activated" in (tags or ""): role = "activated"

        # 读缓存
        entry   = cache.get(key, {})
        cached  = bool(entry) and (now - entry.get("ts", 0)) < CACHE_TTL
        if not cached:
            data = api_fetch(ssid)
            time.sleep(2.0)   # 限速：每个账号间隔 2s，避免 RESI 被封
            if data:
                entry = {
                    "code":        data.get("code", ""),
                    "conversions": int(data.get("conversions", 0)),
                    "earnings":    data.get("earnings", 0),
                    "clicks":      data.get("clicks", 0),
                    "ts":          now,
                }
            else:
                # API 返回 null：无自己的 ref_code
                entry = {"code": "", "conversions": 0, "earnings": 0,
                         "clicks": 0, "ts": now}
            cache[key] = entry

        code       = entry.get("code", "")
        conversions= entry.get("conversions", 0)
        remaining  = max(0, MAX_CONVERSIONS - conversions) if code else 0

        results.append({
            "id":          acc_id,
            "email":       email,
            "role":        role,
            "code":        code or None,
            "ref_url":     f"https://unitool.ai/ref/{code}" if code else None,
            "conversions": conversions,
            "remaining":   remaining,
            "earnings":    entry.get("earnings", 0),
            "clicks":      entry.get("clicks", 0),
            "has_code":    bool(code),
            "exhausted":   bool(code) and conversions >= MAX_CONVERSIONS,
            "cached":      cached,
        })

    save_cache(cache)

    # 汇总
    total_with_code  = sum(1 for r in results if r["has_code"])
    total_exhausted  = sum(1 for r in results if r["exhausted"])
    total_remaining  = sum(r["remaining"] for r in results)
    total_earnings   = sum(r["earnings"]  for r in results)

    output = {
        "generated_at": int(now),
        "summary": {
            "total_accounts":    len(results),
            "with_own_code":     total_with_code,
            "exhausted":         total_exhausted,
            "available_slots":   total_remaining,
            "total_earnings":    total_earnings,
        },
        "accounts": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
