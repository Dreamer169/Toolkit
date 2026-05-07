#!/usr/bin/env python3
"""
unitool_token_stats.py — 查询所有 unitool 账号的 AI chat token 余额
====================================================================
调用 /api/user/billing-accounts 获取:
  - product_id="regular"  → 主力 token 余额
  - product_id="bonus"    → 赠送 token 余额（同样可开新对话）

优先走 Xray SOCKS5 居民代理，失败后 fallback 直连。
"""
import argparse, json, re, subprocess, time
import psycopg2

DB_URL      = "postgresql://postgres:postgres@localhost/toolkit"
CACHE_FILE  = "/tmp/unitool_token_cache.json"
CACHE_TTL   = 14400   # 4 hours
AUTH_COOKIE = "__Secure-unitool-ssid"
UA          = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

RESI_PORTS = [10822, 10851, 10853, 10854, 10857, 10859, 10870, 10872, 10878, 10879]
_resi_idx  = 0

def _next_resi_port():
    global _resi_idx
    port = RESI_PORTS[_resi_idx % len(RESI_PORTS)]
    _resi_idx += 1
    return port

def load_cache():
    try:
        return json.loads(open(CACHE_FILE).read())
    except Exception:
        return {}

def save_cache(c):
    try:
        open(CACHE_FILE, "w").write(json.dumps(c))
    except Exception:
        pass

def api_billing(ssid):
    """GET /api/user/billing-accounts — 先走 SOCKS5，失败 fallback 直连。"""
    port = _next_resi_port()
    url  = "https://unitool.ai/api/user/billing-accounts"
    base_args = [
        "-b", AUTH_COOKIE + "=" + ssid,
        "-H", "Accept: application/json",
        "-H", "User-Agent: " + UA,
    ]
    attempts = [
        ["curl", "-s", "--socks5-hostname", "127.0.0.1:" + str(port),
         "--max-time", "12"] + base_args + [url],
        ["curl", "-s", "--max-time", "10"] + base_args + [url],
    ]
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=18)
            raw = r.stdout.strip()
            if not raw or raw == "null":
                continue
            d = json.loads(raw)
            if isinstance(d, dict) and "accounts" in d:
                return d["accounts"]
        except Exception:
            continue
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="忽略缓存强制刷新")
    ap.add_argument("--limit",   type=int, default=0, help="只处理前 N 个账号")
    args = ap.parse_args()

    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute("""
        SELECT id, email, notes, tags FROM accounts
        WHERE platform='outlook'
          AND tags LIKE '%unitool_registered%'
          AND notes LIKE '%unitool_ssid=%'
        ORDER BY id DESC
    """)
    rows = cur.fetchall()
    conn.close()

    if args.limit:
        rows = rows[:args.limit]

    cache   = {} if args.refresh else load_cache()
    results = []
    now     = time.time()

    for acc_id, email, notes, tags in rows:
        ssid_m = re.search(r"unitool_ssid=([0-9a-f]{40,})", notes or "")
        if not ssid_m:
            continue
        ssid = ssid_m.group(1)
        key  = str(acc_id)

        role = "registered"
        if "unitool_ref_master"      in (tags or ""): role = "master"
        elif "unitool_ref_activated" in (tags or ""): role = "activated"

        entry  = cache.get(key, {})
        cached = bool(entry) and (now - entry.get("ts", 0)) < CACHE_TTL

        if not cached:
            accounts        = api_billing(ssid)
            time.sleep(1.5)
            regular         = 0; bonus = 0
            expires_regular = ""; expires_bonus = ""
            if accounts:
                for acct in accounts:
                    pid = acct.get("product_id", "")
                    val = int(acct.get("value", 0))
                    exp = acct.get("expires_at", "")
                    if pid == "regular":
                        regular = val; expires_regular = exp
                    elif pid == "bonus":
                        bonus = val;   expires_bonus   = exp
            entry = {
                "regular":         regular,
                "bonus":           bonus,
                "expires_regular": expires_regular,
                "expires_bonus":   expires_bonus,
                "ts":              now,
                "api_ok":          accounts is not None,
            }
            cache[key] = entry
            save_cache(cache)   # 增量保存，中断不丢数据

        results.append({
            "id":              acc_id,
            "email":           email,
            "role":            role,
            "regular":         entry.get("regular", 0),
            "bonus":           entry.get("bonus",   0),
            "total":           entry.get("regular", 0) + entry.get("bonus", 0),
            "expires_regular": entry.get("expires_regular", ""),
            "expires_bonus":   entry.get("expires_bonus",   ""),
            "api_ok":          entry.get("api_ok",  False),
            "cached":          cached,
        })

    total_regular = sum(r["regular"] for r in results)
    total_bonus   = sum(r["bonus"]   for r in results)
    zero_count    = sum(1 for r in results if r["regular"] == 0)
    api_fail      = sum(1 for r in results if not r["api_ok"])

    output = {
        "generated_at": int(now),
        "summary": {
            "total_accounts": len(results),
            "total_regular":  total_regular,
            "total_bonus":    total_bonus,
            "total_all":      total_regular + total_bonus,
            "zero_regular":   zero_count,
            "api_fail":       api_fail,
        },
        "accounts": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
