#!/usr/bin/env python3
"""
inactive_ssid_repush.py v1.0
把 status='inactive' 且 notes 里有 unitool_ssid 的账号
验证 ssid 是否仍有效，有效则热推进 proxy 池并写文件。
"""
import psycopg2, re, json, urllib.request, time, os, sys, datetime

DB_URL     = "postgresql://postgres:postgres@localhost/toolkit"
PROXY_PORT = 8089
SSID_DIR   = "/data/unitool_ssids"
VALIDATE_URL = "https://unitool.ai/api/auth/session"
INTERVAL   = 1.5   # 每个账号之间间隔秒数，避免频率太高

def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def db_connect():
    return psycopg2.connect(DB_URL)

def validate_ssid(ssid: str, email: str) -> bool:
    """用 ssid 调 /api/auth/session，返回 True 表示仍有效。"""
    try:
        req = urllib.request.Request(
            VALIDATE_URL,
            headers={
                "Cookie": f"__Secure-unitool-ssid={ssid}",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            }
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            body = json.loads(r.read())
        user = body.get("auth", {}).get("user", {})
        u_email = user.get("email", "").lower()
        # 邮件匹配或者至少有 user.id 说明 ssid 被接受
        if user.get("id") and u_email == email.lower():
            return True
        if user.get("id") and not u_email:
            return True
        return False
    except Exception as e:
        log(f"  [validate] 异常 {e}")
        return False

def push_ssid(ssid: str, email: str) -> int:
    """热推到 proxy 池，返回 pool_size，失败返回 -1。"""
    try:
        data = json.dumps({"ssid": ssid, "label": email}).encode()
        req  = urllib.request.Request(
            f"http://localhost:{PROXY_PORT}/add-ssid",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
        return resp.get("pool_size", -1)
    except Exception as e:
        log(f"  [push] 失败 {e}")
        return -1

def write_ssid_file(ssid: str, email: str):
    try:
        os.makedirs(SSID_DIR, exist_ok=True)
        fname = os.path.join(SSID_DIR, email.replace("@","_").replace(".","_") + ".txt")
        if not os.path.exists(fname):
            with open(fname, "w") as f:
                f.write(ssid)
    except Exception as e:
        log(f"  [file] 写入失败 {e}")

def mark_repushed(conn, account_id: int, email: str):
    """在 notes 追加 repush 记录，避免重复处理。"""
    cur = conn.cursor()
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        UPDATE accounts
        SET notes = COALESCE(notes,'') || '\nssid_repushed=ok at=' || %s,
            updated_at = NOW()
        WHERE id = %s
    """, (ts, account_id))
    conn.commit()
    cur.close()

def mark_invalid(conn, account_id: int):
    cur = conn.cursor()
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        UPDATE accounts
        SET notes = COALESCE(notes,'') || '\nssid_repush_invalid at=' || %s,
            updated_at = NOW()
        WHERE id = %s
    """, (ts, account_id))
    conn.commit()
    cur.close()

def main():
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("""
        SELECT id, email, notes FROM accounts
        WHERE platform = 'outlook'
          AND status    = 'inactive'
          AND notes LIKE '%%unitool_ssid=%%'
          AND notes NOT LIKE '%%ssid_repushed=%%'
          AND notes NOT LIKE '%%ssid_repush_invalid%%'
        ORDER BY updated_at ASC
    """)
    rows = cur.fetchall()
    cur.close()
    log(f"待处理 {len(rows)} 个 inactive 账号（有 ssid）")

    ok = 0; skip = 0; fail = 0
    for idx, (acc_id, email, notes) in enumerate(rows, 1):
        m = re.search(r"unitool_ssid=([a-f0-9]{100,})", notes or "")
        if not m:
            skip += 1
            continue
        ssid = m.group(1)
        log(f"[{idx}/{len(rows)}] {email}  ssid_len={len(ssid)}")
        if validate_ssid(ssid, email):
            pool_size = push_ssid(ssid, email)
            write_ssid_file(ssid, email)
            mark_repushed(conn, acc_id, email)
            ok += 1
            log(f"  ✅ 有效 → 推入池 pool_size={pool_size}")
        else:
            mark_invalid(conn, acc_id)
            fail += 1
            log(f"  ❌ ssid 已失效")
        time.sleep(INTERVAL)

    conn.close()
    log(f"完成：有效推入={ok}  已失效={fail}  跳过={skip}")

if __name__ == "__main__":
    main()
