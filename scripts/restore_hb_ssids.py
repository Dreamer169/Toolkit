#!/usr/bin/env python3
"""
启动时恢复高余额账号 SSID 到 /data/unitool_ssids/ 目录。
在 unitool-proxy PM2 启动前执行，确保 SSIDs 持久可用。
"""
import psycopg2, re, os, json

c = psycopg2.connect("postgresql://postgres:postgres@localhost/toolkit")
cur = c.cursor()
cur.execute(
    "SELECT email, notes FROM accounts WHERE tags ILIKE %s AND platform=%s",
    ("%unitool_high_balance%", "outlook")
)
rows = cur.fetchall()
c.close()

written = 0
for email, notes in rows:
    m = re.search(r"unitool_ssid=([A-Za-z0-9_-]+)", notes or "")
    if not m:
        continue
    ssid = m.group(1)
    if len(ssid) < 100:
        continue
    fname = email.lower().replace("@","_").replace(".","_") + ".txt"
    fpath = "/data/unitool_ssids/" + fname
    if not os.path.exists(fpath):
        with open(fpath, "w") as fp:
            fp.write(ssid)
        written += 1

print(f"[restore_hb_ssids] wrote {written} missing SSID files from high_balance accounts")
