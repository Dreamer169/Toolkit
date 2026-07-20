#!/usr/bin/env python3
"""
unitool_token_stats.py v2.1
===========================
- 20线程并发，全量扫描约 2 分钟（原串行 42 分钟）
- 仅扫 pool 中 is_valid=true 的账号（JOIN unitool_ssids）
- 扫描后自动踢出 bonus < HB_THRESHOLD 的高余额池账号
- v2.1: 自动升格 bonus >= HB_THRESHOLD 且有有效 SSID 的账号为 unitool_high_balance
- v2.1: 踢出或升格后触发 proxy /reload-ssids 立即生效
"""
import json, subprocess, time, threading, urllib.request
import psycopg2
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_URL       = "postgresql://postgres:postgres@localhost/toolkit"
CACHE_FILE   = "/tmp/unitool_token_cache.json"
CACHE_TTL    = 14400
AUTH_COOKIE  = "__Secure-unitool-ssid"
UA           = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
WORKERS      = 20
HB_THRESHOLD = 10.1
PROXY_PORT   = 8089

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
    # FIX: API 失败时保留上次缓存值，不能静默归零（否则删号/SSID 过期会把 $82 变成 $0）
    if accounts is None:
        prev = entry.copy() if entry else {}
        prev["ts"]     = now
        prev["api_ok"] = False
        return key, prev, False
    regular         = 0
    bonus           = 0
    expires_regular = ""
    expires_bonus   = ""
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
        "api_ok": True,
    }, False

def kick_high_balance(kick_ids, conn=None):
    if not kick_ids:
        return
    _own = conn is None
    if _own:
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
    if _own:
        conn.close()
    print(f"[HB] 已踢出 {len(kick_ids)} 个 bonus<{HB_THRESHOLD} 账号", flush=True)

def promote_high_balance(results, cache, conn=None):
    """
    v2.1: 把 bonus >= HB_THRESHOLD 且有有效 SSID 但未打标的账号升格为 unitool_high_balance。
    返回升格的账号数量。
    """
    # 找出 results+cache 中 bonus >= HB_THRESHOLD 的账号 id
    all_data = {**cache, **results}  # results 覆盖 cache（更新）
    candidate_ids = [
        int(k) for k, v in all_data.items()
        if float(v.get("bonus") or 0) >= HB_THRESHOLD
    ]
    if not candidate_ids:
        print(f"[HB] promote: 无 bonus>={HB_THRESHOLD} 候选", flush=True)
        return 0

    _own = conn is None
    if _own:
        conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()

    # 查哪些候选账号：有有效 SSID + 未打 HB 标 + platform=outlook
    cur.execute("""
        SELECT DISTINCT ON (a.id) a.id, a.email, a.tags
        FROM accounts a
        JOIN unitool_ssids us
            ON LOWER(TRIM(a.email)) = LOWER(TRIM(us.source_email))
            AND us.is_valid = true
            AND LENGTH(us.ssid) > 50
        WHERE a.id = ANY(%s)
          AND a.platform = 'outlook'
          AND (a.tags IS NULL OR a.tags NOT LIKE '%%unitool_high_balance%%')
        ORDER BY a.id
    """, (candidate_ids,))
    to_promote = cur.fetchall()

    if not to_promote:
        print(f"[HB] promote: 所有 bonus>={HB_THRESHOLD} 账号已是 HB 或无有效 SSID", flush=True)
        if _own:
            conn.close()
        return 0

    promoted = 0
    for acc_id, email, tags in to_promote:
        new_tags = ((tags or "").rstrip(",") + ",unitool_high_balance").lstrip(",")
        cur.execute("UPDATE accounts SET tags=%s WHERE id=%s", (new_tags, acc_id))
        bonus_val = float((all_data.get(str(acc_id)) or {}).get("bonus") or 0)
        print(f"[HB] promote: id={acc_id}  {email}  bonus={bonus_val:.4f}", flush=True)
        promoted += 1

    conn.commit()
    if _own:
        conn.close()
    print(f"[HB] 已升格 {promoted} 个账号为 unitool_high_balance", flush=True)
    return promoted

def reload_proxy_pool():
    """通知 unitool-proxy 重建 SSID 池，使踢出/升格立即生效。"""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PROXY_PORT}/reload-ssids",
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            print(f"[HB] proxy reload: {body}", flush=True)
    except Exception as e:
        print(f"[HB] proxy reload failed (non-fatal): {e}", flush=True)

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
          AND (a.tags IS NULL OR a.tags NOT LIKE '%unitool_deleted%')
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

    # ── 1. 踢出 bonus < HB_THRESHOLD 的高余额池账号 ──────────────────────────
    # 复用单个连接，避免并发时"too many clients"
    _post_conn = psycopg2.connect(DB_URL)
    _post_cur  = _post_conn.cursor()
    _post_cur.execute(
        "SELECT a.id, a.email FROM accounts a "
        "WHERE a.tags LIKE '%unitool_high_balance%' AND a.platform = 'outlook'"
    )
    hb_accounts = _post_cur.fetchall()

    # v2.2: 查出所有 HB 账号的 ref_registered 转化次数，保护有 10+ 次的账号
    _post_cur.execute(
        "SELECT a.id, LENGTH(a.notes) - LENGTH(REPLACE(a.notes, \ref_registered=\, \\)) "
        "FROM accounts a "
        "WHERE a.tags LIKE \%unitool_high_balance%\ AND a.platform = \outlook"
    )
    _ref_conv_map = {r[0]: (r[1] // 16 if r[1] else 0) for r in _post_cur.fetchall()}

    kick_ids = []
    for acc_id, email in hb_accounts:
        ref_conv = _ref_conv_map.get(acc_id, 0)
        if ref_conv >= 10:
            print(f"[HB] keep: id={acc_id}  {email}  ref_conv={ref_conv}>=10 (protected)", flush=True)
            continue
        entry = results.get(str(acc_id)) or cache.get(str(acc_id))
        if entry:
            bon = float(entry.get("bonus") or 0)
            if bon < HB_THRESHOLD:
                kick_ids.append(acc_id)
                print(f"[HB] kick: id={acc_id}  {email}  bonus={bon:.4f} ref_conv={ref_conv}", flush=True)

    if kick_ids:
        kick_high_balance(kick_ids, conn=_post_conn)
    else:
        print(f"[HB] 所有高余额池账号 bonus >= {HB_THRESHOLD}，无需踢出", flush=True)

    # ── 2. v2.1: 升格 bonus >= HB_THRESHOLD 且有 SSID 的新账号 ──────────────
    promoted = promote_high_balance(results, cache, conn=_post_conn)

    # ── 3. 如有踢出或升格，通知 proxy 重建池 ────────────────────────────────
    if kick_ids or promoted > 0:
        reload_proxy_pool()

    # ── 4. 打印最终 HB 池摘要 ────────────────────────────────────────────────
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{PROXY_PORT}/high-balance-status", timeout=6
        ) as resp:
            hbs = json.loads(resp.read())
            print(f"\n[HB] 最终池状态: total={hbs['high_balance_total']}  "
                  f"live={hbs['high_balance_live']}  dead={hbs['high_balance_dead']}", flush=True)
    except Exception:
        pass

    # ── 5. 清理 token_cache：移除 unitool_deleted 账号的僵尸条目 ─────────────
    try:
        _del_cur  = _post_conn.cursor()
        _del_cur.execute(
            "SELECT id FROM accounts WHERE tags LIKE %s AND platform='outlook'",
            ("%unitool_deleted%",)
        )
        deleted_ids = {str(r[0]) for r in _del_cur.fetchall()}
        _post_conn.close()

        if deleted_ids:
            with _cache_lock:
                c = load_cache()
                before = len(c)
                for did in deleted_ids:
                    c.pop(did, None)
                after = len(c)
                save_cache(c)
            removed = before - after
            if removed:
                print(f"[CACHE] 清理 {removed} 个 unitool_deleted 僵尸缓存条目", flush=True)
    except Exception as _ce:
        print(f"[CACHE] 清理失败(非致命): {_ce}", flush=True)

if __name__ == "__main__":
    main()
