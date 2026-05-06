#!/usr/bin/env python3
"""
unitool_reflink.py — 获取 unitool.ai 账号的 referral code
==========================================================
用法:
  python3 unitool_reflink.py --ssid <ssid>
  python3 unitool_reflink.py --email <email>
  python3 unitool_reflink.py --account-id <id>

输出:
  [OK] ref_code|https://unitool.ai/ref/{ref_code}|email|unitool_user_id
  [FAIL] reason
"""
import argparse, json, re, subprocess, sys
import psycopg2

DB_URL = "postgresql://postgres:postgres@localhost/toolkit"
AUTH_COOKIE = "__Secure-unitool-ssid"

def log(*a): print(*a, flush=True)

def db_get_ssid(email=None, account_id=None):
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    if account_id:
        cur.execute("SELECT id, email, notes FROM accounts WHERE id=%s", (account_id,))
    elif email:
        cur.execute("SELECT id, email, notes FROM accounts WHERE email=%s", (email,))
    else:
        cur.execute("""
            SELECT id, email, notes FROM accounts
            WHERE platform='outlook' AND status='active'
              AND tags LIKE '%unitool_registered%'
              AND notes LIKE '%unitool_ssid=%'
            ORDER BY id DESC LIMIT 1
        """)
    row = cur.fetchone()
    conn.close()
    if not row:
        return None, None, None
    acc_id, acc_email, notes = row
    ssid = ""
    if notes:
        m = re.search(r"unitool_ssid=([0-9a-f]{40,})", notes)
        if m:
            ssid = m.group(1)
    return acc_id, acc_email, ssid

def db_save_ref_code(account_id, ref_code):
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute("SELECT notes FROM accounts WHERE id=%s", (account_id,))
    row = cur.fetchone()
    notes = row[0] or "" if row else ""
    if f"unitool_ref_code={ref_code}" in notes:
        conn.close(); return
    cur.execute("""
        UPDATE accounts SET
          notes = COALESCE(notes,'') || E'\nunitool_ref_code=' || %s,
          tags  = CASE WHEN COALESCE(tags,'') NOT LIKE '%%unitool_ref_master%%'
                       THEN COALESCE(NULLIF(tags,''),'') || ',unitool_ref_master'
                       ELSE tags END,
          updated_at = NOW()
        WHERE id = %s
    """, (ref_code, account_id))
    conn.commit(); conn.close()

def get_ref_code_from_api(ssid: str) -> dict:
    cmd = [
        "curl", "-s",
        "-b", f"{AUTH_COOKIE}={ssid}",
        "-H", "Accept: application/json",
        "--max-time", "15",
        "https://unitool.ai/api/auth/session"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        return {"ok": False, "reason": f"curl_error: {r.stderr[:100]}"}
    try:
        data = json.loads(r.stdout)
    except Exception as e:
        return {"ok": False, "reason": f"json_parse: {e} raw={r.stdout[:200]}"}
    user = data.get("auth", {}).get("user", {})
    ref_code = user.get("ref_code", "")
    if not ref_code:
        return {"ok": False, "reason": f"no_ref_code: {json.dumps(data)[:300]}"}
    return {
        "ok": True,
        "ref_code": ref_code,
        "ref_url": f"https://unitool.ai/ref/{ref_code}",
        "unitool_user_id": user.get("id", 0),
        "email": user.get("email", ""),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ssid",       default="")
    ap.add_argument("--email",      default="")
    ap.add_argument("--account-id", type=int, default=0)
    args = ap.parse_args()

    ssid = args.ssid; acc_id = acc_email = None
    if not ssid:
        acc_id, acc_email, ssid = db_get_ssid(
            email=args.email or None,
            account_id=args.account_id or None)
        if not ssid:
            print(f"[FAIL] no_ssid_found", flush=True); sys.exit(1)
        log(f"[DB] {acc_email} id={acc_id} ssid_len={len(ssid)}")

    result = get_ref_code_from_api(ssid)
    if not result["ok"]:
        print(f"[FAIL] {result['reason']}", flush=True); sys.exit(1)

    ref_code = result["ref_code"]
    ref_url  = result["ref_url"]
    email    = result.get("email", acc_email or "?")
    uid      = result.get("unitool_user_id", 0)
    log(f"[OK] ref_code={ref_code} uid={uid} email={email}")

    if acc_id:
        db_save_ref_code(acc_id, ref_code)
        log(f"[DB] ref_code saved")

    print(f"[OK] {ref_code}|{ref_url}|{email}|{uid}", flush=True)

if __name__ == "__main__":
    main()
