#!/usr/bin/env python3
"""
rescue_dead.py -- 批量恢复 unitool_rescue_dead 账号
1. unitool_already -> 标 unitool_registered (已注册)
2. refresh_token 有效 -> 清 rescue_fail_at + 改回 unitool_verify_pending
3. refresh_token 400  -> 标 token_invalid (真死)
"""
import json, re, sys, time, urllib.parse, urllib.request
import psycopg2

DB_URL    = "postgresql://postgres:postgres@localhost/toolkit"
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
LOG_FILE  = "/tmp/rescue_dead.log"
LIKE_PAT  = "%unitool_rescue_dead%"

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    print(line, flush=True)
    with open(LOG_FILE, "a") as f: f.write(line + "\n")

def db():
    return psycopg2.connect(DB_URL)

def upd(acc_id, tags, notes):
    conn = db(); cur = conn.cursor()
    cur.execute("UPDATE accounts SET tags=%s, notes=%s, updated_at=NOW() WHERE id=%s",
                (tags, notes, acc_id))
    conn.commit(); conn.close()

def test_rt(rt):
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "scope": "https://graph.microsoft.com/.default offline_access",
    }).encode()
    req = urllib.request.Request(
        "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return True, json.loads(r.read()).get("access_token", "ok")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try: err = json.loads(body).get("error", body[:80])
        except: err = body[:80]
        return False, "HTTP %d: %s" % (e.code, err)
    except Exception as ex:
        return False, str(ex)

def main():
    conn = db(); cur = conn.cursor()
    cur.execute(
        "SELECT id, email, refresh_token, tags, notes FROM accounts WHERE tags LIKE %s ORDER BY id",
        (LIKE_PAT,)
    )
    rows = cur.fetchall()
    conn.close()
    log("=== rescue_dead.py start: %d accounts ===" % len(rows))
    restored = dead = already = 0
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")

    for (acc_id, email, rt, tags, notes) in rows:
        tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

        # Case A: already registered on unitool -- just relabel
        if "unitool_already" in tag_list:
            new_tags = re.sub(r",?unitool_rescue_dead", "", tags).strip(",")
            new_tags = re.sub(r",?unitool_processing", "", new_tags).strip(",")
            if "unitool_registered" not in new_tags:
                new_tags = new_tags + ",unitool_registered"
            new_tags = re.sub(r",+", ",", new_tags).strip(",")
            new_notes = (notes or "") + ("\nrescue_dead: already->registered at=%s" % now_str)
            upd(acc_id, new_tags, new_notes)
            log("[already->registered] id=%d %s" % (acc_id, email))
            already += 1
            continue

        # Case B: no refresh_token
        if not rt:
            new_tags = tags if "token_invalid" in tags else tags + ",token_invalid"
            new_notes = (notes or "") + ("\nrescue_dead: no_rt at=%s" % now_str)
            upd(acc_id, new_tags, new_notes)
            log("[no_rt->dead] id=%d %s" % (acc_id, email))
            dead += 1
            continue

        # Case C: test refresh_token
        log("[test_rt] id=%d %s ..." % (acc_id, email))
        valid, info = test_rt(rt)
        time.sleep(0.4)

        if valid:
            new_tags = re.sub(r",?unitool_rescue_dead", "", tags).strip(",")
            new_tags = re.sub(r",?unitool_processing", "", new_tags).strip(",")
            if "unitool_verify_pending" not in new_tags:
                new_tags = new_tags + ",unitool_verify_pending"
            new_tags = re.sub(r",+", ",", new_tags).strip(",")
            clean_notes = re.sub(r"\nrescue_fail_at=[^\n]*", "", notes or "")
            clean_notes = re.sub(r"\nunitool_verify_pending_fail=[^\n]*", "", clean_notes)
            clean_notes += ("\nrescued_at=%s token_ok" % now_str)
            upd(acc_id, new_tags, clean_notes)
            log("[restored->verify_pending] id=%d %s" % (acc_id, email))
            restored += 1
        else:
            new_tags = tags if "token_invalid" in tags else tags + ",token_invalid"
            new_notes = (notes or "") + ("\nrescue_dead: token_dead (%s) at=%s" % (info[:60], now_str))
            upd(acc_id, new_tags, new_notes)
            log("[token_dead] id=%d %s err=%s" % (acc_id, email, info[:60]))
            dead += 1

    log("=== DONE: restored=%d already->registered=%d dead=%d ===" % (restored, already, dead))

if __name__ == "__main__":
    main()
