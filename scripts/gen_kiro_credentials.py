#!/usr/bin/env python3
"""
gen_kiro_credentials.py — 从 Toolkit DB 生成 kiro-rs credentials.json
- 读取所有 platform=kiro 的活跃账号 (token 列 = Kiro accessToken)
- 按 proxy_formatted 分配 proxyUrl (IP 一致性)
- 美/日/韩 IP 优先 priority=0，其余 priority=1
- 保留已有手动 credentials (id<=100) 不覆盖
"""
import json, secrets, psycopg2, sys
from collections import Counter
from datetime import datetime, timezone, timedelta

DB_URL = "postgresql://postgres:postgres@localhost/toolkit"
CREDS_PATH = "/opt/kiro.rs/credentials.json"

# 代理出口 IP geo 信息 (经实测验证)
PROXY_GEO = {
    # === US exits (tp-out-0..6, priority=0) ===
    "socks5://127.0.0.1:10910": {"ip": "154.44.73.141",  "cc": "US", "country": "United States", "city": "Los Angeles"},
    "socks5://127.0.0.1:10911": {"ip": "38.96.191.150",  "cc": "US", "country": "United States", "city": "New York"},
    "socks5://127.0.0.1:10912": {"ip": "38.111.30.111",  "cc": "US", "country": "United States", "city": "New York"},
    "socks5://127.0.0.1:10913": {"ip": "131.143.161.47", "cc": "US", "country": "United States", "city": ""},
    "socks5://127.0.0.1:10916": {"ip": "205.179.217.31", "cc": "US", "country": "United States", "city": "Los Angeles"},
    # === JP exit (ss-out-0 → jp.111000.cc.cd:443, priority=0) ===
    "socks5://127.0.0.1:10850": {"ip": "",               "cc": "JP", "country": "Japan",         "city": "Tokyo"},
    # === Other exits (ss-out / ws-out / ps-out, priority=1) ===
    "socks5://127.0.0.1:10851": {"ip": "185.49.57.133",  "cc": "IT", "country": "Italy",         "city": "Viterbo"},
    "socks5://127.0.0.1:10853": {"ip": "5.180.32.18",    "cc": "TR", "country": "Turkey",        "city": "Istanbul"},
    "socks5://127.0.0.1:10854": {"ip": "112.120.48.16",  "cc": "HK", "country": "Hong Kong",     "city": "Hong Kong"},
    "socks5://127.0.0.1:10855": {"ip": "213.109.202.195","cc": "RU", "country": "Russia",        "city": "Saint Petersburg"},
    "socks5://127.0.0.1:10857": {"ip": "203.186.234.178","cc": "HK", "country": "Hong Kong",     "city": "Hong Kong"},
    "socks5://127.0.0.1:10859": {"ip": "218.190.242.49", "cc": "HK", "country": "Hong Kong",     "city": "Tung Chung"},
    "socks5://127.0.0.1:10871": {"ip": "62.60.151.74",   "cc": "FI", "country": "Finland",       "city": "Helsinki"},
    "socks5://127.0.0.1:10872": {"ip": "85.254.103.132", "cc": "CA", "country": "Canada",        "city": "Toronto"},
    "socks5://127.0.0.1:10914": {"ip": "82.110.34.3",    "cc": "GB", "country": "United Kingdom","city": "London"},
    "socks5://127.0.0.1:10915": {"ip": "200.36.9.33",    "cc": "MX", "country": "Mexico",        "city": ""},
}

# 优先国家 (美/日/韩 priority=0, 其余 priority=1)
PREFERRED_CC = {"US", "JP", "KR"}

def geo_priority(proxy_fmt: str) -> int:
    geo = PROXY_GEO.get(proxy_fmt, {})
    return 0 if geo.get("cc", "") in PREFERRED_CC else 1

def gen_machine_id() -> str:
    return secrets.token_hex(32)  # 64-char hex

def load_existing():
    try:
        data = json.load(open(CREDS_PATH))
        arr  = data if isinstance(data, list) else [data]
        # 保留 id<=100 的手动添加条目
        manual = [c for c in arr if c.get("id", 999) <= 100]
        return manual, max((c.get("id", 0) for c in arr), default=0)
    except Exception as e:
        print(f"[gen] no existing creds or parse err: {e}")
        return [], 0

def fetch_kiro_accounts():
    conn = psycopg2.connect(DB_URL)
    cur  = conn.cursor()
    cur.execute("""
        SELECT id, email, token, proxy_formatted,
               refresh_token, notes
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

    manual_emails = {c.get("email", "") for c in manual_creds}

    new_creds = []
    next_id   = max(max_existing_id, 100) + 1  # always start above 100 for DB creds

    for row in rows:
        acc_id, email, token, proxy_fmt, db_refresh_token, notes_raw = row

        # Parse notes JSON safely
        notes_json = {}
        try:
            if notes_raw and notes_raw.strip().startswith('{'):
                notes_json = json.loads(notes_raw)
        except Exception:
            pass

        if email in manual_emails:
            print(f"[gen] skip (already manual): {email}")
            continue

        # Determine authMethod: prefer notes JSON, else infer from token length
        auth_method = notes_json.get("authMethod", "")
        client_id     = notes_json.get("clientId", "")
        client_secret = notes_json.get("clientSecret", "")
        if not auth_method:
            # IdC tokens contain clientId; social (Google/GitHub) don't
            auth_method = "IdC" if client_id else "social"

        # expiresAt: try notes JSON first, then default now+8h
        expires_at_str = notes_json.get("expiresAt", "")
        if not expires_at_str:
            expires_at_str = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # refreshToken: prefer DB column, fall back to accessToken (social mode)
        real_refresh = db_refresh_token if (db_refresh_token and len(db_refresh_token) > 50) else token

        geo      = PROXY_GEO.get(proxy_fmt or "", {})
        country  = geo.get("country", "unknown")
        priority = geo_priority(proxy_fmt or "")

        cred = {
            "id":           next_id,
            "email":        email,
            "accessToken":  token,
            "refreshToken": real_refresh,
            "expiresAt":    expires_at_str,
            "authMethod":   auth_method,
            "machineId":    gen_machine_id(),
            "priority":     priority,
            "disabled":     False,
        }

        # Add IdC-specific fields if available
        if auth_method == "IdC" and client_id:
            cred["clientId"]     = client_id
            cred["clientSecret"] = client_secret

        # IP consistency: bind per-account proxy
        if proxy_fmt:
            cred["proxyUrl"]      = proxy_fmt
            cred["_proxyCountry"] = country

        new_creds.append(cred)
        next_id += 1

    all_creds = manual_creds + new_creds
    print(f"[gen] total credentials: {len(all_creds)}"
          f" ({len(manual_creds)} manual + {len(new_creds)} from DB)")

    # Stats
    cc_dist = Counter(
        PROXY_GEO.get(c.get("proxyUrl", ""), {}).get("cc", "manual")
        for c in all_creds
    )
    prio_dist = Counter(c.get("priority", 0) for c in all_creds)
    print(f"[gen] geo distribution: {dict(sorted(cc_dist.items()))}")
    print(f"[gen] priority distribution: priority=0:{prio_dist[0]}  priority=1:{prio_dist[1]}")

    # US/JP/KR breakdown
    preferred = [c for c in all_creds if PROXY_GEO.get(c.get("proxyUrl",""),{}).get("cc","") in PREFERRED_CC]
    print(f"[gen] preferred (US/JP/KR) credentials: {len(preferred)}")

    with open(CREDS_PATH, "w") as f:
        json.dump(all_creds, f, indent=2, ensure_ascii=False)
    print(f"[gen] written → {CREDS_PATH}")
    print(f"[gen] DONE — restart kiro-rs to reload credentials.")

if __name__ == "__main__":
    main()
