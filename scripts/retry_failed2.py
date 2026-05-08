#!/usr/bin/env python3
"""
retry_failed2.py — 重试9个失败账号，猴子补丁强制只走存活端口
死亡端口: 10852 10856 10858 (ERR_CONNECTION_CLOSED / ERR_SSL_PROTOCOL_ERROR)
"""
import asyncio, re, json, sys, time
import psycopg2

sys.path.insert(0, "/root/Toolkit/scripts")
import unitool_login as _ul

# ── 猴子补丁：强制 _resi_healthy_ports 只含存活端口，TTL设为1小时避免被重置 ──
GOOD_PORTS = [10851, 10853, 10854, 10855, 10857, 10859]
_ul._resi_healthy_ports = GOOD_PORTS
_ul._resi_health_ts = time.time() + 3600   # 1h TTL
print(f"[PATCH] 强制使用端口: {GOOD_PORTS}", flush=True)

from unitool_login import login_one

DB_URL     = "postgresql://postgres:postgres@localhost/toolkit"
CACHE_FILE = "/tmp/unitool_token_cache.json"

ACCOUNTS = [
    (669, "rperezkqa@outlook.com",          "b%UuE9*lJGD^RVV"),
    (685, "rileykrg224@outlook.com",         "0pVg%!xqR!RqJImp"),
    (695, "penelopeweh408@outlook.com",      "8XoU$$GPk3kVtoRq"),
    (697, "aaron.m98@outlook.com",           "!X$kX1n1B%jwlUZ"),
    (702, "jackxxr207@outlook.com",          "PKHDMHI@6Pmtm"),
    (708, "cadenmartinez556@outlook.com",    "6HM!1*f3AMx@aT"),
    (726, "ellie.gomez72@outlook.com",       "I6xI&ggQKk!giA"),
    (786, "mwrightdqt@outlook.com",          "9P#@*BguBP*XD"),
    (797, "lily_patel91@outlook.com",        "SD&7#lHSjR3qeFt7"),
]

def db_update_ssid(acc_id: int, new_ssid: str):
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute("SELECT notes FROM accounts WHERE id=%s", (acc_id,))
    row = cur.fetchone()
    notes = row[0] or "" if row else ""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    new_entry = f"unitool_ssid={new_ssid}\nat={ts}"
    if re.search(r"unitool_ssid=[0-9a-f]+", notes):
        notes = re.sub(r"unitool_ssid=[0-9a-f]+(\nat=[^\n]+)?", new_entry, notes)
    else:
        notes = (notes.rstrip() + "\n" + new_entry).lstrip("\n")
    cur.execute("UPDATE accounts SET notes=%s, updated_at=NOW() WHERE id=%s", (notes, acc_id))
    conn.commit(); cur.close(); conn.close()

def cache_invalidate(acc_id: int):
    try:
        cache = json.loads(open(CACHE_FILE).read())
        cache.pop(str(acc_id), None)
        open(CACHE_FILE, "w").write(json.dumps(cache))
    except Exception as e:
        print(f"  [cache] warn: {e}", flush=True)

async def main():
    ok_ids = []
    fail_ids = []
    total = len(ACCOUNTS)

    for idx, (acc_id, email, password) in enumerate(ACCOUNTS, 1):
        # 重新确认补丁还在（每次迭代前）
        _ul._resi_healthy_ports = GOOD_PORTS
        _ul._resi_health_ts = time.time() + 3600

        port = GOOD_PORTS[(idx - 1) % len(GOOD_PORTS)]
        print(f"\n[{idx}/{total}] {email}  id={acc_id}  预期端口≈{port}", flush=True)
        t0 = time.time()
        try:
            result = await login_one(email, password, headless=True)
        except Exception as e:
            print(f"  ✗ 异常: {e}", flush=True)
            fail_ids.append((acc_id, email, str(e)))
            continue

        elapsed = round(time.time() - t0, 1)
        if result.get("ok"):
            new_ssid = result.get("ssid", "")
            print(f"  ✓ 登录成功 {elapsed}s  新SSID长={len(new_ssid)}", flush=True)
            db_update_ssid(acc_id, new_ssid)
            cache_invalidate(acc_id)
            print(f"  ✓ DB+cache 已更新", flush=True)
            ok_ids.append((acc_id, email))
        else:
            reason = result.get("reason", "unknown")
            print(f"  ✗ 登录失败 {elapsed}s  reason={reason}", flush=True)
            fail_ids.append((acc_id, email, reason))

        if idx < total:
            await asyncio.sleep(3)

    print(f"\n{'='*55}", flush=True)
    print(f"重试完成: 成功={len(ok_ids)}  失败={len(fail_ids)}  共={total}", flush=True)
    if ok_ids:
        print("成功账号:")
        for aid, em in ok_ids:
            print(f"  ✓ {aid} {em}")
    if fail_ids:
        print("仍失败账号:")
        for aid, em, reason in fail_ids:
            print(f"  ✗ {aid} {em}  reason={reason}")

if __name__ == "__main__":
    asyncio.run(main())
