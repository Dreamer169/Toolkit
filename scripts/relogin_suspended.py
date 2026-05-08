#!/usr/bin/env python3
"""
relogin_suspended.py — 对 status=suspended 的 unitool 账号重新登录刷新 SSID
只刷新 unitool.ai 侧的 SSID，不改 Outlook 侧的 status（两个系统分开）
"""
import asyncio, re, json, sys, os, time
import psycopg2

sys.path.insert(0, "/root/Toolkit/scripts")
from unitool_login import login_one

DB_URL     = "postgresql://postgres:postgres@localhost/toolkit"
CACHE_FILE = "/tmp/unitool_token_cache.json"

ACCOUNTS = [
    (667, "kmitchellnvh@outlook.com",       "0lPaq%H*7!tM@V"),
    (669, "rperezkqa@outlook.com",           "b%UuE9*lJGD^RVV"),
    (670, "aurorahughes221@outlook.com",     "NH5WLbI%LIhkqwu"),
    (674, "rylancarter452@outlook.com",      "n2L#q0zQPf90f"),
    (685, "rileykrg224@outlook.com",         "0pVg%!xqR!RqJImp"),
    (686, "landenwkq610@outlook.com",        "!OaDtAeTXTT0"),
    (687, "joseph.ramirez81@outlook.com",    "&IvlHn!@2MLt"),
    (695, "penelopeweh408@outlook.com",      "8XoU$$GPk3kVtoRq"),
    (696, "ellieharris182@outlook.com",      "m06hqfXPeoz&KdWP"),
    (697, "aaron.m98@outlook.com",           "!X$kX1n1B%jwlUZ"),
    (702, "jackxxr207@outlook.com",          "PKHDMHI@6Pmtm"),
    (708, "cadenmartinez556@outlook.com",    "6HM!1*f3AMx@aT"),
    (718, "ericwilson473@outlook.com",       "F0b9O1OfzlG@H9E"),
    (724, "sarahgreen828@outlook.com",       "BWiFU!5%7sLz"),
    (726, "ellie.gomez72@outlook.com",       "I6xI&ggQKk!giA"),
    (756, "joseph_rodriguez74@outlook.com",  "P1a5rVQYTXd%#sy9"),
    (769, "kphillipseaf@outlook.com",        "A9thJoJGE1^1"),
    (770, "ethanmartinez921@outlook.com",    "5WiEx0efy0Og*x^"),
    (778, "mphillipsear@outlook.com",        "fptntAvO2H6%@^R"),
    (786, "mwrightdqt@outlook.com",          "9P#@*BguBP*XD"),
    (797, "lily_patel91@outlook.com",        "SD&7#lHSjR3qeFt7"),
    (858, "nparkerccg@outlook.com",          "7d*pCrab5CrH"),
]

def db_update_ssid(acc_id: int, email: str, new_ssid: str):
    """把 notes 里的旧 unitool_ssid=... 替换成新的，并补注时间戳。"""
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute("SELECT notes FROM accounts WHERE id=%s", (acc_id,))
    row = cur.fetchone()
    notes = row[0] or "" if row else ""

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    new_entry = f"unitool_ssid={new_ssid}\nat={ts}"

    # 替换已有 ssid 段；若没有则追加
    if re.search(r"unitool_ssid=[0-9a-f]+", notes):
        notes = re.sub(r"unitool_ssid=[0-9a-f]+(\nat=[^\n]+)?", new_entry, notes)
    else:
        notes = (notes.rstrip() + "\n" + new_entry).lstrip("\n")

    cur.execute(
        "UPDATE accounts SET notes=%s, updated_at=NOW() WHERE id=%s",
        (notes, acc_id)
    )
    conn.commit(); cur.close(); conn.close()

def cache_invalidate(acc_id: int):
    """从 token 缓存里删除这个账号，下次监控时强制重查。"""
    try:
        cache = json.loads(open(CACHE_FILE).read())
        cache.pop(str(acc_id), None)
        open(CACHE_FILE, "w").write(json.dumps(cache))
    except Exception as e:
        print(f"  [cache] warn: {e}", flush=True)

async def main():
    ok_ids   = []
    fail_ids = []
    total    = len(ACCOUNTS)

    for idx, (acc_id, email, password) in enumerate(ACCOUNTS, 1):
        print(f"\n[{idx}/{total}] {email}  id={acc_id}", flush=True)
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
            db_update_ssid(acc_id, email, new_ssid)
            cache_invalidate(acc_id)
            print(f"  ✓ DB+cache 已更新", flush=True)
            ok_ids.append((acc_id, email))
        else:
            reason = result.get("reason", "unknown")
            print(f"  ✗ 登录失败 {elapsed}s  reason={reason}", flush=True)
            fail_ids.append((acc_id, email, reason))

        # 各账号之间稍作间隔，避免 RESI IP 被 CF 连续命中
        if idx < total:
            await asyncio.sleep(3)

    print(f"\n{'='*55}", flush=True)
    print(f"完成: 成功={len(ok_ids)}  失败={len(fail_ids)}  共={total}", flush=True)
    if ok_ids:
        print("成功账号:", flush=True)
        for aid, em in ok_ids:
            print(f"  ✓ {aid} {em}", flush=True)
    if fail_ids:
        print("失败账号:", flush=True)
        for aid, em, reason in fail_ids:
            print(f"  ✗ {aid} {em}  reason={reason}", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
