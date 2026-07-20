#!/usr/bin/env python3
"""
unitool_ssid_refresh.py
=======================
刷新 unitool_registered 账号中 SSID 已失效的账号：
  1. 从 DB 取候选账号（registered + is_valid=false + 有密码）
  2. 并发调 unitool_login.py 重新登录拿新 SSID
  3. 写入 unitool_ssids + /data/unitool_ssids/ 文件
  4. 通知 proxy /add-ssid 热推
  5. 触发 token_stats 扫描，升格 bonus>=10.1 的账号

PM2 cron: 0 */4 * * *  (每4小时)
"""
import argparse, json, os, re, subprocess, sys, time
import psycopg2
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_URL      = "postgresql://postgres:postgres@localhost/toolkit"
SCRIPTS     = "/data/Toolkit/scripts"
LOGIN_PY    = f"{SCRIPTS}/unitool_login.py"
TOKEN_STATS = f"{SCRIPTS}/unitool_token_stats.py"
SSID_DIR    = "/data/unitool_ssids"
PROXY_PORT  = 8089
BATCH       = 30
WORKERS     = 3

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def db_connect():
    return psycopg2.connect(DB_URL)

def get_candidates(limit):
    conn = db_connect(); cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (a.id)
               a.id, a.email, a.password
        FROM accounts a
        JOIN unitool_ssids us ON LOWER(TRIM(a.email)) = LOWER(TRIM(us.source_email))
        WHERE a.platform = 'outlook'
          AND a.tags LIKE '%%unitool_registered%%'
          AND a.tags NOT LIKE '%%unitool_deleted%%'
          AND a.tags NOT LIKE '%%ssid_refresh_fail%%'
          AND (us.is_valid = false OR LENGTH(us.ssid) < 50)
          AND LENGTH(COALESCE(a.password, '')) >= 8
          AND a.status = 'active'
        ORDER BY a.id, us.id DESC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    log(f"[candidates] {len(rows)} 个候选账号")
    return rows

def run_login(acc_id, email, password):
    log(f"[login] id={acc_id} {email} 开始登录...")
    try:
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":99")
        result = subprocess.run(
            ["python3", LOGIN_PY,
             "--email", email, "--password", password, "--no-headless"],
            capture_output=True, text=True, timeout=240, env=env
        )
        for line in result.stdout.splitlines():
            if line.startswith("[OK]"):
                parts = line.split("|")
                if len(parts) >= 3:
                    ssid = parts[2].strip()
                    if len(ssid) > 50:
                        log(f"[login] OK id={acc_id} {email} ssid_len={len(ssid)}")
                        return ssid, True
            if line.startswith("[FAIL]"):
                log(f"[login] FAIL id={acc_id} {email}: {line}")
        log(f"[login] 无结果 id={acc_id} rc={result.returncode}")
        return "", False
    except subprocess.TimeoutExpired:
        log(f"[login] 超时 id={acc_id} {email}")
        return "", False
    except Exception as e:
        log(f"[login] 异常 id={acc_id}: {e}")
        return "", False

def save_ssid(acc_id, email, ssid):
    conn = db_connect(); cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE unitool_ssids SET is_valid=FALSE WHERE LOWER(TRIM(source_email))=LOWER(TRIM(%s))",
            (email,))
        cur.execute("""
            INSERT INTO unitool_ssids (source_account_id, source_email, ssid, is_valid, collected_at)
            VALUES (%s, %s, %s, TRUE, NOW())
        """, (acc_id, email, ssid))
        # 更新 accounts.tags 确保 ssid_ok 在
        cur.execute("SELECT tags FROM accounts WHERE id=%s", (acc_id,))
        row = cur.fetchone(); tags = (row[0] or "") if row else ""
        if "ssid_refresh_fail" in tags:
            tags = re.sub(r",?ssid_refresh_fail", "", tags).strip(",")
            cur.execute("UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s", (tags, acc_id))
        conn.commit()
        log(f"[save] DB 已更新 id={acc_id} {email}")
    except Exception as e:
        conn.rollback()
        log(f"[save] DB 错误 id={acc_id}: {e}")
        conn.close(); return False
    conn.close()
    # 写文件
    try:
        os.makedirs(SSID_DIR, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9@._-]", "_", email)
        path = os.path.join(SSID_DIR, f"{safe}.txt")
        open(path, "w").write(ssid)
    except Exception as e:
        log(f"[save] 文件写入失败(非致命): {e}")
    return True

def mark_fail(acc_id):
    try:
        conn = db_connect(); cur = conn.cursor()
        cur.execute("SELECT tags FROM accounts WHERE id=%s", (acc_id,))
        row = cur.fetchone(); tags = (row[0] or "") if row else ""
        if "ssid_refresh_fail" not in tags:
            tags = (tags.rstrip(",") + ",ssid_refresh_fail").lstrip(",")
            cur.execute("UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s", (tags, acc_id))
            conn.commit()
        conn.close()
    except Exception as e:
        log(f"[mark_fail] err id={acc_id}: {e}")

def notify_proxy(email, ssid):
    try:
        import urllib.request
        data = json.dumps({"email": email, "ssid": ssid}).encode()
        req  = urllib.request.Request(
            f"http://127.0.0.1:{PROXY_PORT}/add-ssid",
            data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        log(f"[proxy] add-ssid OK {email}")
    except Exception as e:
        log(f"[proxy] add-ssid 失败(非致命): {e}")

def run_token_stats():
    log("[token_stats] 触发 HB 升格扫描...")
    try:
        subprocess.run(["python3", TOKEN_STATS], timeout=300)
        log("[token_stats] 完成")
    except Exception as e:
        log(f"[token_stats] 错误(非致命): {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=BATCH)
    args = parser.parse_args()

    # 资源保护：load1>8 或 Chrome主进程数>6 时跳过
    # w1/w2 正常运行时保有3-9个Chrome，>6才说明大批注册在跑，此时跳过
    import subprocess as _sp
    load1 = os.getloadavg()[0]
    r = _sp.run(["bash","-c",
        "ps aux | grep chromium-1208/chrome | grep -v grep | grep -v -- --type= | wc -l"],
        capture_output=True, text=True)
    chrome_cnt = int(r.stdout.strip() or 0)
    log(f"[main] 资源检查: load1={load1:.1f} chrome主进程={chrome_cnt}")
    if load1 > 8:
        log(f"[main] 负载过高(load1={load1:.1f})，跳过本次"); return
    if chrome_cnt > 6:
        log(f"[main] Chrome过多({chrome_cnt}>6)，跳过避免资源冲突"); return

    candidates = get_candidates(args.batch)
    if not candidates:
        log("[main] 无候选账号，退出")
        return

    log(f"[main] 开始处理 {len(candidates)} 个账号, WORKERS={WORKERS}")
    ok_count = fail_count = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {
            ex.submit(run_login, acc_id, email, pwd): (acc_id, email)
            for acc_id, email, pwd in candidates
        }
        for fut in as_completed(futures):
            acc_id, email = futures[fut]
            try:
                ssid, ok = fut.result()
            except Exception as e:
                log(f"[main] future error id={acc_id}: {e}")
                ssid, ok = "", False
            if ok and ssid:
                if save_ssid(acc_id, email, ssid):
                    notify_proxy(email, ssid)
                    ok_count += 1
                else:
                    mark_fail(acc_id); fail_count += 1
            else:
                mark_fail(acc_id); fail_count += 1

    log(f"[main] 完成: 成功={ok_count} 失败={fail_count}/{len(candidates)}")
    if ok_count > 0:
        run_token_stats()
    else:
        log("[main] 无新 SSID，跳过 token_stats")

if __name__ == "__main__":
    main()
