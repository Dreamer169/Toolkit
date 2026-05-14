#!/usr/bin/env python3
"""
unitool_token_stats.py v2.0
===========================
- 20线程并发，全量扫描约 2 分钟（原串行 42 分钟）
- 仅扫 pool 中 is_valid=true 的账号（JOIN unitool_ssids）
- 扫描后自动踢出 bonus < HB_THRESHOLD 的高余额池账号
"""
import json, subprocess, time, threading
import psycopg2
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_URL       = "postgresql://postgres:postgres@localhost/toolkit"
CACHE_FILE   = "/tmp/unitool_token_cache.json"
CACHE_TTL    = 14400
AUTH_COOKIE  = "__Secure-unitool-ssid"
UA           = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
WORKERS      = 20
HB_THRESHOLD = 10.1

RESI_PORTS  = [10851, 10853, 10854, 10857, 10859, 10870, 10872, 10878, 10879]
_port_idx   = 0
_port_lock  = threading.Lock()
_cache_lock = threading.Lock()

def _next_resi_port():
    global _port_idx
    with _port_lock:
        port = RESI_PORTS[_port_idx % len(RESI_PORTS)]
        _port_idx += 1
    return port

def load_cache():
    try:
        return json.loads(open(CACHE_FILE).read())
    except Exception:
        return {}

def save_cache(c):
    try:
        with _cache_lock:
            open(CACHE_FILE, "w").write(json.dumps(c))
    except Exception:
        pass

def api_billing(ssid):
    port = _next_resi_port()
    url  = "https://unitool.ai/api/user/billing-accounts"
    base = ["-b", AUTH_COOKIE + "=" + ssid,
            "-H", "Accept: application/json",
            "-H", "User-Agent: " + UA]
    attempts = [
        ["curl", "-s", "--socks5-hostname", "127.0.0.1:" + str(port),
         "--max-time", "12"] + base + [url],
        ["curl", "-s", "--max-time", "10"] + base + [url],
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

def scan_one(row, cache, now):
    acc_id, email, ssid = row
    key   = str(acc_id)
    entry = cache.get(key, {})
    if entry and (now - entry.get("ts", 0)) < CACHE_TTL:
        return key, entry, True
    accounts        = api_billing(ssid)
    regular         = 0
    bonus           = 0
    expires_regular = ""
    expires_bonus   = ""
    if accounts:
        for acct in accounts:
            pid = acct.get("product_id", "")
            val = round(float(acct.get("value") or 0), 4)
            exp = acct.get("expires_at", "")
            if pid == "regular":
                regular = val; expires_regular = exp
            elif pid == "bonus":
                bonus = val; expires_bonus = exp
    return key, {
        "regular": regular, "bonus": bonus,
        "expires_regular": expires_regular,
        "expires_bonus":   expires_bonus,
        "ts":     now,
        "api_ok": accounts is not None,
    }, False

def kick_high_balance(kick_ids):
    if not kick_ids:
        return
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    for aid in kick_ids:
        cur.execute(
            "UPDATE accounts SET tags = TRIM(BOTH ',' FROM REPLACE("
            "REGEXP_REPLACE(tags, '(,|^)unitool_high_balance(,|$)', ',', 'g'),"
            "',\,',',')) WHERE id = %s",
            (aid,)
        )
    conn.commit()
    conn.close()
    print(f"[HB] 已踢出 {len(kick_ids)} 个 bonus<{HB_THRESHOLD} 账号", flush=True)

def main():
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (a.id) a.id, a.email, us.ssid
        FROM accounts a
        JOIN unitool_ssids us
          ON LOWER(TRIM(a.email)) = LOWER(TRIM(us.source_email))
        WHERE us.is_valid = true
          AND us.ssid IS NOT NULL AND LENGTH(us.ssid) > 50
          AND a.platform = 'outlook'
        ORDER BY a.id DESC
    """)
    rows  = cur.fetchall()
    conn.close()
    total = len(rows)
    print(f"[SCAN] pool 有效账号: {total} 个，启动 {WORKERS} 线程...", flush=True)

    cache    = load_cache()
    now      = time.time()
    results  = {}
    done_cnt = [0]
    cnt_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(scan_one, row, cache, now): row for row in rows}
        for fut in as_completed(futures):
            try:
                key, entry, was_cached = fut.result()
                results[key] = entry
                if not was_cached:
                    with _cache_lock:
                        cache[key] = entry
                    save_cache(cache)
            except Exception as e:
                print(f"[SCAN] err: {e}", flush=True)
            with cnt_lock:
                done_cnt[0] += 1
                done = done_cnt[0]
            if done % 100 == 0:
                bp = sum(1 for v in results.values() if float(v.get("bonus") or 0) > 0)
                print(f"  {done}/{total}  bonus>0: {bp}  elapsed: {int(time.time()-now)}s",
                      flush=True)

    bonus_pos   = [v for v in results.values() if float(v.get("bonus") or 0) > 0]
    bonus_zero  = [v for v in results.values() if float(v.get("bonus") or 0) <= 0]
    api_fail    = sum(1 for v in results.values() if not v.get("api_ok", True))
    total_bonus = round(sum(float(v.get("bonus") or 0) for v in bonus_pos), 4)
    print(f"\n完成! {total} 个账号，耗时 {int(time.time()-now)}s", flush=True)
    print(f"bonus>0:   {len(bonus_pos)} 个", flush=True)
    print(f"bonus=0:   {len(bonus_zero)} 个", flush=True)
    print(f"bonus合计:  {total_bonus}", flush=True)
    print(f"API失败:    {api_fail}", flush=True)

    # 踢出 bonus < HB_THRESHOLD 的高余额池账号
    conn2 = psycopg2.connect(DB_URL)
    cur2  = conn2.cursor()
    cur2.execute(
        "SELECT a.id, a.email FROM accounts a "
        "WHERE a.tags LIKE '%unitool_high_balance%' AND a.platform = 'outlook'"
    )
    hb_accounts = cur2.fetchall()
    conn2.close()

    kick_ids = []
    for acc_id, email in hb_accounts:
        entry = results.get(str(acc_id)) or cache.get(str(acc_id))
        if entry:
            bon = float(entry.get("bonus") or 0)
            if bon < HB_THRESHOLD:
                kick_ids.append(acc_id)
                print(f"[HB] kick: id={acc_id}  {email}  bonus={bon:.4f}", flush=True)

    if kick_ids:
        kick_high_balance(kick_ids)
    else:
        print(f"[HB] 所有高余额池账号 bonus >= {HB_THRESHOLD}，无需踢出", flush=True)

if __name__ == "__main__":
    main()
