#!/usr/bin/env python3
"""
unitool_batch_refcode.py — 批量为所有已注册账号生成专属 ref_code
================================================================
用法:
  python3 unitool_batch_refcode.py [--dry-run] [--limit N] [--delay N]

流程:
  1. 查 DB: unitool_registered + has_ssid + no ref_code in DB
  2. 对每个账号先 GET /api/user/ref-code 检查是否已有码（可能只是DB未记录）
  3. 没有则通过轮换 RESI 代理 POST /api/ref-codes 创建
  4. 保存到 DB (notes 追加 + tag unitool_ref_activated)

日志: /tmp/unitool_batch_refcode.log
"""
import argparse, json, os, re, subprocess, sys, time
import psycopg2

DB_URL      = "postgresql://postgres:postgres@localhost/toolkit"
AUTH_COOKIE = "__Secure-unitool-ssid"
LOG_FILE    = "/tmp/unitool_batch_refcode.log"

RESI_PORTS  = [10851, 10853, 10854, 10857, 10859, 10870, 10872, 10878, 10879]


CACHE_FILE = "/tmp/unitool_ref_code_cache.json"

def cache_write_ref_code(account_id: int, code: str, conversions: int = 0):
    """写入 chain_v3 的 ref_code 缓存，避免重复 API 验证"""
    try:
        try:
            cache = json.loads(open(CACHE_FILE).read())
        except Exception:
            cache = {}
        cache[str(account_id)] = {
            "code": code,
            "conversions": conversions,
            "ts": time.time(),
        }
        open(CACHE_FILE, "w").write(json.dumps(cache))
    except Exception:
        pass

def log(*a):
    ts   = time.strftime("%H:%M:%S")
    line = f"[{ts}] " + " ".join(str(x) for x in a)
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def db_connect():
    return psycopg2.connect(DB_URL)

def api_check_existing(ssid: str) -> str:
    """GET /api/user/ref-code → 返回已有 ref_code 或 ''"""
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "12",
             "-b", f"{AUTH_COOKIE}={ssid}",
             "-H", "Accept: application/json",
             "https://unitool.ai/api/user/ref-code"],
            capture_output=True, text=True, timeout=18)
        raw = r.stdout.strip()
        if raw == "null" or not raw:
            return ""
        data = json.loads(raw)
        code = data.get("code", "")
        conv = data.get("conversions", 0)
        return code or ""
    except Exception as e:
        log(f"  [check_api] err: {e}")
        return ""

def api_create_refcode(ssid: str, port: int) -> tuple:
    """POST /api/ref-codes via SOCKS5 → (ref_code, error_str)"""
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "15",
             "--socks5-hostname", f"127.0.0.1:{port}",
             "-b", f"{AUTH_COOKIE}={ssid}",
             "-X", "POST",
             "-H", "Content-Type: application/json",
             "-H", "Accept: application/json",
             "https://unitool.ai/api/ref-codes"],
            capture_output=True, text=True, timeout=20)
        if r.returncode != 0 or not r.stdout.strip():
            return "", f"curl_rc={r.returncode}"
        data = json.loads(r.stdout)
        code = data.get("code", "")
        err  = data.get("error", "")
        return code, err
    except Exception as e:
        return "", str(e)

def db_save_ref_code(account_id: int, ref_code: str):
    conn = db_connect(); cur = conn.cursor()
    cur.execute("SELECT notes, tags FROM accounts WHERE id=%s", (account_id,))
    row = cur.fetchone()
    if not row:
        conn.close(); return
    notes = row[0] or ""
    tags  = row[1] or ""
    if f"unitool_ref_code={ref_code}" not in notes:
        notes += f"\nunitool_ref_code={ref_code}"
    if "unitool_ref_activated" not in tags:
        tags = (tags.strip(",") + ",unitool_ref_activated").strip(",")
    cur.execute(
        "UPDATE accounts SET notes=%s, tags=%s, updated_at=NOW() WHERE id=%s",
        (notes, tags, account_id))
    conn.commit(); conn.close()
    log(f"  [DB] saved ref_code={ref_code} → id={account_id}")

def main():
    open(LOG_FILE, "w").write("")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run",  action="store_true", help="只查不写")
    ap.add_argument("--limit",    type=int, default=0, help="最多处理N个账号")
    ap.add_argument("--delay",    type=float, default=3.0, help="每个账号之间延迟秒数")
    ap.add_argument("--include-existing", action="store_true",
                    help="同时检查已有 ref_code 的账号是否 DB 记录正确")
    args = ap.parse_args()

    conn = db_connect(); cur = conn.cursor()
    if args.include_existing:
        where_no_ref = ""   # 不过滤
    else:
        where_no_ref = "AND (notes NOT LIKE '%unitool_ref_code=%')"

    cur.execute(f"""
        SELECT id, email,
               substring(notes from 'unitool_ssid=([0-9a-f]{{40,}})') as ssid
        FROM accounts
        WHERE platform='outlook'
          AND tags LIKE '%unitool_registered%'
          AND notes LIKE '%unitool_ssid=%'
          {where_no_ref}
        ORDER BY id DESC
    """)
    rows = cur.fetchall()
    conn.close()

    if args.limit:
        rows = rows[:args.limit]

    log(f"=== unitool_batch_refcode 开始: {len(rows)} 个账号待处理 ===")
    if args.dry_run:
        log("  [dry-run] 只检查，不写入")

    ok_count = already_count = fail_count = skip_count = 0
    port_idx = 0  # 全局 RESI port 轮转索引

    for i, (acc_id, email, ssid) in enumerate(rows):
        prefix = f"[{i+1}/{len(rows)}]"
        if not ssid:
            log(f"{prefix} {email} — ssid 为空，跳过")
            skip_count += 1
            continue

        log(f"{prefix} {email} (id={acc_id})")

        # ── Step 1: 先查 API 是否已经有码（DB 可能未记录）──────────────────────
        existing = api_check_existing(ssid)
        if existing:
            log(f"  ✅ API 已有 ref_code={existing}，写入 DB")
            if not args.dry_run:
                db_save_ref_code(acc_id, existing)
                cache_write_ref_code(acc_id, existing)
            already_count += 1
            time.sleep(1)
            continue

        # ── Step 2: 通过轮换代理创建新码 ───────────────────────────────────────
        created = False
        tried_ports = set()
        # 先用当前轮转 port，失败则依次尝试其他
        for attempt in range(len(RESI_PORTS)):
            port = RESI_PORTS[port_idx % len(RESI_PORTS)]
            port_idx += 1
            if port in tried_ports:
                continue
            tried_ports.add(port)

            log(f"  → POST /api/ref-codes via port={port} (attempt {attempt+1})")
            if args.dry_run:
                log(f"  [dry-run] 跳过")
                created = True
                break

            code, err = api_create_refcode(ssid, port)
            if code:
                log(f"  ✅ 创建成功 ref_code={code} port={port}")
                db_save_ref_code(acc_id, code)
                ok_count += 1
                created = True
                break
            elif err == "ip-already-existed":
                log(f"  port={port} ip已用，换下一个...")
                continue
            elif "already" in err.lower() or "exists" in err.lower():
                # 账号已有码但 API 之前没返回？再查一次
                log(f"  port={port} 提示已存在，重查 API...")
                time.sleep(2)
                existing2 = api_check_existing(ssid)
                if existing2:
                    log(f"  ✅ 二次查到 ref_code={existing2}")
                    db_save_ref_code(acc_id, existing2)
                    already_count += 1
                    created = True
                    break
                log(f"  二次查 API 仍为空，继续换 port")
            else:
                log(f"  port={port} err={err}，换下一个...")

        if not created and not args.dry_run:
            log(f"  ❌ 所有代理端口均失败，跳过")
            fail_count += 1

        time.sleep(args.delay)

    log(f"\n{'='*60}")
    log(f"=== 完成: 新建={ok_count} 已有={already_count} 跳过={skip_count} 失败={fail_count} ===")

if __name__ == "__main__":
    main()
