#!/usr/bin/env python3
"""unitool_gpt55_scan.py v1.1 - scan all valid-SSID accounts for gpt-5.5 balance"""
import argparse, http.client, json, ssl, time, re, threading
import psycopg2
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_URL  = "postgresql://postgres:postgres@localhost/toolkit"
BASE    = "unitool.ai"
CTX     = ssl.create_default_context()
UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
AUTH_CK = "__Secure-unitool-ssid"
_lock   = threading.Lock()

def log(msg):
    with _lock:
        print("[" + time.strftime("%H:%M:%S") + "] " + str(msg), flush=True)

def db_connect():
    return psycopg2.connect(DB_URL)

def get_accounts(limit):
    conn = db_connect(); cur = conn.cursor()
    sql = (
        "SELECT DISTINCT ON (a.id) a.id, a.email, us.ssid "
        "FROM accounts a "
        "JOIN unitool_ssids us ON LOWER(TRIM(a.email)) = LOWER(TRIM(us.source_email)) "
        "WHERE us.is_valid = true "
        "AND LENGTH(us.ssid) > 50 "
        "AND a.platform = 'outlook' "
        "AND STRPOS(COALESCE(a.tags,''), 'unitool_deleted') = 0 "
        "ORDER BY a.id DESC "
        "LIMIT %s"
    )
    cur.execute(sql, (limit,))
    rows = cur.fetchall(); conn.close()
    return [(i,e,s) for i,e,s in rows if re.match(r'^[0-9a-f]{50,}$', s or '')]

def call_api(path, ssid, timeout=12):
    try:
        c = http.client.HTTPSConnection(BASE, timeout=timeout, context=CTX)
        c.request("GET", path, headers={
            "Cookie": AUTH_CK + "=" + ssid,
            "Accept": "application/json",
            "User-Agent": UA,
        })
        r = c.getresponse(); body = r.read(8000).decode("utf-8","ignore"); c.close()
        return r.status, body
    except Exception as e:
        return 0, str(e)

def ssid_valid(ssid):
    st, body = call_api("/api/auth/session", ssid, timeout=10)
    if st != 200: return False
    try:
        return bool(json.loads(body).get("auth",{}).get("user",{}).get("id"))
    except Exception:
        return False

def get_billing(ssid):
    st, body = call_api("/api/user/billing-accounts", ssid)
    if st != 200: return None, None
    try:
        data = json.loads(body)
        if not isinstance(data, dict) or "accounts" not in data: return None, None
        regular = bonus = 0.0
        for acct in data["accounts"]:
            pid = acct.get("product_id",""); val = float(acct.get("value") or 0)
            if pid == "regular": regular = val
            elif pid == "bonus": bonus = val
        return regular, bonus
    except Exception:
        return None, None

def mark_ssid_invalid(email):
    try:
        conn = db_connect(); cur = conn.cursor()
        cur.execute("UPDATE unitool_ssids SET is_valid=FALSE WHERE LOWER(TRIM(source_email))=LOWER(TRIM(%s))", (email,))
        conn.commit(); conn.close()
    except Exception as e:
        log("warn mark_ssid_invalid " + email + ": " + str(e))

def update_tags(conn, acc_id, add_tag, remove_tags=None):
    cur = conn.cursor()
    cur.execute("SELECT tags FROM accounts WHERE id=%s", (acc_id,))
    row = cur.fetchone(); tags = (row[0] or "") if row else ""
    for rt in (remove_tags or []):
        tags = re.sub(r'(^|,)' + re.escape(rt) + r'(,|$)', ',', tags).strip(",")
    tags = re.sub(r',+', ',', tags).strip(",")
    if add_tag and add_tag not in tags.split(","):
        tags = (tags.rstrip(",") + "," + add_tag).lstrip(",")
    cur.execute("UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s", (tags, acc_id))
    conn.commit()

def scan_one(row):
    acc_id, email, ssid = row
    if not ssid_valid(ssid):
        log("SSID_DEAD  id=" + str(acc_id) + " " + email)
        mark_ssid_invalid(email)
        return acc_id, email, "ssid_dead", 0.0, 0.0
    regular, bonus = get_billing(ssid)
    if regular is None:
        log("API_FAIL   id=" + str(acc_id) + " " + email)
        return acc_id, email, "api_fail", 0.0, 0.0
    total = regular + bonus
    if total > 0:
        log("gpt55_ok   id=" + str(acc_id) + " " + email + "  reg=" + str(round(regular,4)) + " bonus=" + str(round(bonus,4)) + " total=" + str(round(total,4)))
        return acc_id, email, "gpt55_ok", regular, bonus
    else:
        log("low_bal    id=" + str(acc_id) + " " + email + "  reg=" + str(round(regular,4)) + " bonus=" + str(round(bonus,4)) + " total=" + str(round(total,4)))
        return acc_id, email, "gpt55_no_balance", regular, bonus

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int, default=9999)
    parser.add_argument("--workers", type=int, default=15)
    args = parser.parse_args()

    rows = get_accounts(args.limit)
    log("扫描 " + str(len(rows)) + " 个账号 (WORKERS=" + str(args.workers) + ")")

    ok = low = dead = fail = 0
    conn = db_connect()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(scan_one, row): row for row in rows}
        for fut in as_completed(futures):
            try:
                acc_id, email, status, regular, bonus = fut.result()
            except Exception as e:
                log("ERR future: " + str(e)); fail += 1; continue
            if status == "gpt55_ok":
                update_tags(conn, acc_id, "gpt55_ok", remove_tags=["gpt55_no_balance"]); ok += 1
            elif status == "gpt55_no_balance":
                update_tags(conn, acc_id, "gpt55_no_balance", remove_tags=["gpt55_ok"]); low += 1
            elif status == "ssid_dead":
                dead += 1
            else:
                fail += 1
    conn.close()
    log("=== 完成 === gpt55_ok=" + str(ok) + " gpt55_no_balance=" + str(low) + " ssid_dead=" + str(dead) + " api_fail=" + str(fail))

if __name__ == "__main__":
    main()
