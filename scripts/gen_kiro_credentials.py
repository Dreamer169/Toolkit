#!/usr/bin/env python3
"""
gen_kiro_credentials.py — 从 Toolkit DB 生成 kiro-rs credentials.json
- 读取所有 platform=kiro 的活跃账号 (token 列 = Kiro accessToken)
- 按 proxy_formatted 分配 proxyUrl (IP 一致性)
- 美/日/韩 IP 优先 priority=0，其余 priority=1
- 保留已有手动 credentials (id<=100) 不覆盖
"""
import json, secrets, psycopg2, sys, urllib.request, urllib.parse

DB_URL = "postgresql://postgres:postgres@localhost/toolkit"
CREDS_PATH = "/opt/kiro.rs/credentials.json"

# 代理出口 IP geo 信息 (已检测)
PROXY_GEO = {
    # US exits (tp-out, priority=0)
    "socks5://127.0.0.1:10910": {"ip": "154.44.73.141",  "cc": "US", "country": "United States", "city": "Los Angeles"},
    "socks5://127.0.0.1:10911": {"ip": "38.96.191.150",  "cc": "US", "country": "United States", "city": "New York"},
    "socks5://127.0.0.1:10912": {"ip": "38.111.30.111",  "cc": "US", "country": "United States", "city": "New York"},
    "socks5://127.0.0.1:10916": {"ip": "205.179.217.31", "cc": "US", "country": "United States", "city": "Los Angeles"},
    # Asia/EU exits (ss-out, priority=1)
    "socks5://127.0.0.1:10851": {"ip": "185.49.57.133",  "cc": "IT", "country": "Italy"},
    "socks5://127.0.0.1:10853": {"ip": "5.180.32.18",    "cc": "TR", "country": "Turkey"},
    "socks5://127.0.0.1:10854": {"ip": "112.120.48.16",  "cc": "HK", "country": "Hong Kong"},
    "socks5://127.0.0.1:10855": {"ip": "213.109.202.195","cc": "RU", "country": "Russia"},
    "socks5://127.0.0.1:10857": {"ip": "203.186.234.178", "cc": "HK", "country": "Hong Kong"},
    "socks5://127.0.0.1:10859": {"ip": "218.190.242.49",  "cc": "HK", "country": "Hong Kong"},
    "socks5://127.0.0.1:10872": {"ip": "185.45.95.70",    "cc": "HK", "country": "Hong Kong"},
}
# 优先国家 (美/日/韩 = 0, 其余 = 1)
PREFERRED_CC = {"US", "JP", "KR"}

def geo_priority(proxy_fmt):
    geo = PROXY_GEO.get(proxy_fmt, {})
    cc  = geo.get("cc", "??")
    return 0 if cc in PREFERRED_CC else 1

def gen_machine_id():
    return secrets.token_hex(32)  # 64-char hex

def load_existing():
    try:
        data = json.load(open(CREDS_PATH))
        arr  = data if isinstance(data, list) else [data]
        # keep manually-added entries (id <= 100 or no db_account_id in email)
        manual = [c for c in arr if c.get("id", 999) <= 100]
        return manual, max((c.get("id",0) for c in arr), default=0)
    except Exception as e:
        print(f"[gen] no existing creds or parse err: {e}")
        return [], 0

def fetch_kiro_accounts():
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute("""
        SELECT id, email, token, proxy_formatted,
               refresh_token,
               notes
        FROM   accounts
        WHERE  platform = 'kiro'
          AND  status   = 'active'
          AND  token    IS NOT NULL
          AND  length(token) > 100
        ORDER  BY id
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def main():
    manual_creds, max_existing_id = load_existing()
    print(f"[gen] existing manual credentials: {len(manual_creds)}, max_id={max_existing_id}")

    rows = fetch_kiro_accounts()
    print(f"[gen] kiro accounts in DB: {len(rows)}")

    # Build set of emails already in manual_creds
    manual_emails = {c.get("email","") for c in manual_creds}

    new_creds = []
    next_id   = max_existing_id + 1

    for row in rows:
        acc_id, email, token, proxy_fmt, db_refresh_token, notes_raw = row
        # Safely parse notes JSON
        try:
            notes_json = json.loads(notes_raw or '{}') if notes_raw and notes_raw.strip().startswith('{') else {}
        except Exception:
            notes_json = {}
        client_id     = notes_json.get('clientId', '')
        client_secret = notes_json.get('clientSecret', '')
        if email in manual_emails:
            print(f"[gen] skip (already in manual): {email}")
            continue

        geo      = PROXY_GEO.get(proxy_fmt or "", {})
        country  = geo.get("country", "unknown")
        priority = geo_priority(proxy_fmt or "")

        from datetime import datetime, timezone, timedelta
        expires_in_8h = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Use real refreshToken from DB if available; else fall back to accessToken (social mode)
        real_refresh = db_refresh_token if db_refresh_token and len(db_refresh_token) > 50 else token
        cred = {
            "id":           next_id,
            "email":        email,
            "accessToken":  token,
            "refreshToken": real_refresh,
            "expiresAt":    expires_in_8h,
            "authMethod":   "social",
            "machineId":    gen_machine_id(),
            "priority":     priority,
            "disabled":     False,
        }
        # Assign per-credential proxy for IP consistency
        if proxy_fmt:
            cred["proxyUrl"] = proxy_fmt.replace("127.0.0.1", "127.0.0.1")
            cred["_proxyCountry"] = country
        
        new_creds.append(cred)
        next_id += 1

    all_creds = manual_creds + new_creds
    print(f"[gen] total credentials: {len(all_creds)} ({len(manual_creds)} manual + {len(new_creds)} from DB)")

    # Priority distribution
    from collections import Counter
    cc_dist = Counter(PROXY_GEO.get(c.get("proxyUrl",""),{}).get("country","manual") for c in all_creds)
    print(f"[gen] geo distribution: {dict(cc_dist)}")
    prio_dist = Counter(c.get("priority", 0) for c in all_creds)
    print(f"[gen] priority distribution: {dict(prio_dist)}")

    # Write
    with open(CREDS_PATH, "w") as f:
        json.dump(all_creds, f, indent=2, ensure_ascii=False)
    print(f"[gen] written → {CREDS_PATH}")
    print(f"[gen] DONE. kiro-rs needs reload to pick up new credentials.")

if __name__ == "__main__":
    main()
