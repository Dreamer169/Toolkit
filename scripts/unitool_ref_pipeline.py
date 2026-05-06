#!/usr/bin/env python3
"""
unitool_ref_pipeline.py — unitool.ai Referral Pipeline
=======================================================
流程:
  1. 选一个 master outlook 账号（尚未在 unitool 注册）
  2. 在 unitool.ai 完整注册 master（走 unitool_pipeline.py 逻辑）
  3. 获取 master 的 ref_code（via /api/auth/session API）
  4. 用 ref_code 注册最多 N 个 referral 账号（每个 = 给 master +10 tokens）
  5. 把每个 referral 账号的 ssid 写入 DB

用法:
  python3 unitool_ref_pipeline.py --batch 10
  python3 unitool_ref_pipeline.py --master-email foo@outlook.com --batch 5
  python3 unitool_ref_pipeline.py --master-ssid <ssid> --batch 10   # 跳过 master 注册
  python3 unitool_ref_pipeline.py --ref-code kZMLT --batch 3        # 直接用已知 ref_code

输出:
  [MASTER] email|ssid|ref_code|ref_url
  [REF_OK]  N|email|ssid
  [REF_FAIL] N|email|reason
  [DONE] ok/total ref_code=XXX
"""
import asyncio, json, os, re, subprocess, sys, time, argparse
import psycopg2

DB_URL   = "postgresql://postgres:postgres@localhost/toolkit"
DISPLAY  = ":99"
SCRIPTS  = "/root/Toolkit/scripts"
PIPELINE = f"{SCRIPTS}/unitool_pipeline.py"
REFLINK  = f"{SCRIPTS}/unitool_reflink.py"
LOG_FILE = "/tmp/unitool_ref_pipeline.log"

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f: f.write(line + "\n")

def db_connect(): return psycopg2.connect(DB_URL)

def get_fresh_outlook(exclude_emails=None):
    """拉一个未注册 unitool 的 outlook 账号"""
    conn = db_connect(); cur = conn.cursor()
    excl = tuple(exclude_emails or []) or ("__NONE__",)
    cur.execute("""
        SELECT id, email, password, refresh_token FROM accounts
        WHERE platform='outlook' AND status='active'
          AND refresh_token IS NOT NULL AND refresh_token != ''
          AND LENGTH(COALESCE(password,'')) >= 12
          AND (tags IS NULL OR (
               tags NOT LIKE '%%unitool_registered%%'
           AND tags NOT LIKE '%%unitool_fail%%'
           AND tags NOT LIKE '%%unitool_processing%%'
           AND tags NOT LIKE '%%token_invalid%%'
          ))
          AND email NOT IN %s
        ORDER BY created_at DESC NULLS LAST
        LIMIT 1
    """, (excl,))
    row = cur.fetchone(); conn.close()
    return row

def db_get_ssid_by_email(email):
    conn = db_connect(); cur = conn.cursor()
    cur.execute("SELECT id, notes FROM accounts WHERE email=%s", (email,))
    row = cur.fetchone(); conn.close()
    if not row: return None, None
    acc_id, notes = row
    ssid = ""
    if notes:
        m = re.search(r"unitool_ssid=([0-9a-f]{40,})", notes)
        if m: ssid = m.group(1)
    return acc_id, ssid

def db_get_ref_code_by_email(email):
    conn = db_connect(); cur = conn.cursor()
    cur.execute("SELECT notes FROM accounts WHERE email=%s", (email,))
    row = cur.fetchone(); conn.close()
    if not row or not row[0]: return ""
    m = re.search(r"unitool_ref_code=([A-Za-z0-9_-]+)", row[0])
    return m.group(1) if m else ""

def mark_account(account_id, tag, extra_notes=""):
    conn = db_connect(); cur = conn.cursor()
    cur.execute("""
        UPDATE accounts SET
            tags = CASE WHEN COALESCE(tags,'')='' THEN %s
                        WHEN tags NOT LIKE '%%' || %s || '%%' THEN tags || ',' || %s
                        ELSE tags END,
            notes = COALESCE(notes,'') || E'\n' || %s,
            updated_at = NOW()
        WHERE id = %s
    """, (tag, tag, tag, f"{tag} at={time.strftime('%Y-%m-%d %H:%M:%S')} {extra_notes}", account_id))
    conn.commit(); conn.close()

def db_track_referral(master_id, ref_email, ref_acc_id, ref_code):
    """在 master 账号 notes 里追加 referral 记录"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("""
        UPDATE accounts SET
          notes = COALESCE(notes,'') || E'\nref_registered=' || %s || '|id=' || %s,
          updated_at = NOW()
        WHERE id = %s
    """, (ref_email, str(ref_acc_id), master_id))
    # 也给 ref 账号加 tag
    cur.execute("""
        UPDATE accounts SET
          tags = CASE WHEN COALESCE(tags,'') NOT LIKE '%%unitool_via_ref%%'
                      THEN COALESCE(NULLIF(tags,''),'') || ',unitool_via_ref'
                      ELSE tags END,
          notes = COALESCE(notes,'') || E'\nvia_ref_code=' || %s || '|master_id=' || %s,
          updated_at = NOW()
        WHERE id = %s
    """, (ref_code, str(master_id), ref_acc_id))
    conn.commit(); conn.close()

def get_existing_ref_count(master_id):
    """统计 master 账号已产生多少 referral"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("SELECT notes FROM accounts WHERE id=%s", (master_id,))
    row = cur.fetchone(); conn.close()
    if not row or not row[0]: return 0
    return len(re.findall(r"ref_registered=", row[0]))

def run_pipeline_for_account(email, password, refresh_token, ref_code=""):
    """调用 unitool_pipeline.py 注册单个账号（内嵌 ref_code 支持）"""
    log(f"  [pipeline] 注册 {email} ref_code={ref_code or 'none'}")
    env = {**os.environ, "DISPLAY": DISPLAY, "PYTHONUNBUFFERED": "1"}

    # 用 subprocess 调用 unitool_pipeline.py
    # pipeline.py 不支持 ref_code，所以直接调用 unitool_register.py
    cmd = ["python3", f"{SCRIPTS}/unitool_register.py",
           "--email", email,
           "--count", "1"]
    if ref_code:
        cmd += ["--ref-code", ref_code]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    stdout = proc.stdout; stderr = proc.stderr

    for line in stdout.split("\n"):
        t = line.strip()
        if t: log(f"    {t}")

    # 解析结果
    ok_line = next((l for l in stdout.split("\n") if l.startswith("[OK]")), "")
    fail_line = next((l for l in stdout.split("\n") if l.startswith("[FAIL]")), "")

    if ok_line:
        parts = ok_line.strip().split("|")
        ssid = parts[1] if len(parts) > 1 else ""
        return {"ok": True, "email": email, "ssid": ssid}

    if fail_line:
        reason = fail_line.split("|")[1] if "|" in fail_line else fail_line
        return {"ok": False, "email": email, "reason": reason.strip()}

    if proc.returncode == 0:
        return {"ok": True, "email": email, "ssid": "", "note": "no_ok_line"}

    return {"ok": False, "email": email, "reason": f"exit={proc.returncode} stderr={stderr[-200:]}"}

def get_ssid_from_login(email, password):
    """通过 unitool_login.py 获取已注册账号的 ssid"""
    log(f"  [login] 获取 ssid: {email}")
    env = {**os.environ, "DISPLAY": DISPLAY, "PYTHONUNBUFFERED": "1"}
    r = subprocess.run(
        ["python3", f"{SCRIPTS}/unitool_login.py",
         "--email", email, "--password", password, "--no-headless"],
        capture_output=True, text=True, timeout=180, env=env
    )
    for line in r.stdout.split("\n"):
        if line.startswith("[OK]"):
            parts = line.split("|")
            if len(parts) >= 3:
                return parts[2]
    return ""

def fetch_ref_code_via_api(ssid):
    """调用 /api/auth/session 获取 ref_code"""
    r = subprocess.run([
        "curl", "-s",
        "-b", f"__Secure-unitool-ssid={ssid}",
        "-H", "Accept: application/json",
        "--max-time", "15",
        "https://unitool.ai/api/auth/session"
    ], capture_output=True, text=True, timeout=20)
    try:
        data = json.loads(r.stdout)
        return data.get("auth", {}).get("user", {}).get("ref_code", "")
    except:
        return ""

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch",        type=int, default=10, help="最多注册几个 referral 账号（≤10）")
    ap.add_argument("--master-email", default="",  help="master 账号邮箱（已注册 unitool）")
    ap.add_argument("--master-ssid",  default="",  help="master 账号 ssid（直接传入）")
    ap.add_argument("--ref-code",     default="",  help="直接使用指定 ref_code（跳过 master 注册）")
    ap.add_argument("--master-id",    type=int, default=0)
    args = ap.parse_args()

    open(LOG_FILE, "w").write("")
    log(f"[main] unitool referral pipeline 启动 batch={args.batch}")

    batch = min(args.batch, 10)
    master_id  = args.master_id
    master_ssid = args.master_ssid
    ref_code   = getattr(args, "ref_code", "")
    master_email = args.master_email

    # ─── Step 0: 确定 ref_code ─────────────────────────────────────────────────
    if not ref_code:
        # 0a. 从指定 ssid 获取
        if master_ssid:
            log(f"[step0] 用提供的 ssid 获取 ref_code…")
            ref_code = fetch_ref_code_via_api(master_ssid)

        # 0b. 从 DB 中现有 master 账号获取
        elif master_email or master_id:
            if master_email:
                master_id_tmp, master_ssid_tmp = db_get_ssid_by_email(master_email)
                if master_id_tmp: master_id = master_id_tmp
                if master_ssid_tmp: master_ssid = master_ssid_tmp
            if master_ssid:
                ref_code = fetch_ref_code_via_api(master_ssid)
            if not ref_code and master_id:
                ref_code = db_get_ref_code_by_email(master_email)

        # 0c. 注册全新 master 账号
        if not ref_code:
            log(f"[step0] 无可用 ref_code，注册全新 master 账号…")
            row = get_fresh_outlook()
            if not row:
                log("[step0] ❌ 没有可用的未注册 outlook 账号！"); sys.exit(1)
            m_id, m_email, m_password, m_rt = row
            log(f"[step0] master 账号: {m_email} id={m_id}")
            mark_account(m_id, "unitool_processing", "ref_master")

            # 用 unitool_pipeline.py 注册 master（无 ref_code）
            env = {**os.environ, "DISPLAY": DISPLAY, "PYTHONUNBUFFERED": "1"}
            log(f"[step0] 调用 unitool_pipeline.py 注册 master…")
            proc = subprocess.run(
                ["python3", PIPELINE, "--batch", "1"],
                capture_output=True, text=True, timeout=900, env=env
            )
            for ln in proc.stdout.split("\n"):
                if ln.strip(): log(f"  {ln.strip()}")

            # 取 ssid
            master_id = m_id; master_email = m_email
            master_id2, master_ssid = db_get_ssid_by_email(m_email)
            if not master_ssid:
                log(f"[step0] 尝试 login 获取 ssid…")
                master_ssid = get_ssid_from_login(m_email, m_password)

            if not master_ssid:
                log("[step0] ❌ master 注册失败：无法获取 ssid"); sys.exit(1)

            # 获取 ref_code
            ref_code = fetch_ref_code_via_api(master_ssid)
            if not ref_code:
                log("[step0] ❌ 无法获取 ref_code"); sys.exit(1)

            # 保存 ref_code 到 DB
            import psycopg2 as _pg
            conn = _pg.connect(DB_URL); cur = conn.cursor()
            cur.execute("""
                UPDATE accounts SET
                  notes = COALESCE(notes,'') || E'\nunitool_ref_code=' || %s,
                  tags  = CASE WHEN tags NOT LIKE '%%unitool_ref_master%%'
                               THEN COALESCE(NULLIF(tags,''),'') || ',unitool_ref_master'
                               ELSE tags END,
                  updated_at = NOW()
                WHERE id = %s
            """, (ref_code, master_id))
            conn.commit(); conn.close()

            ref_url = f"https://unitool.ai/ref/{ref_code}"
            log(f"[step0] ✅ master={m_email} ref_code={ref_code}")
            print(f"[MASTER] {m_email}|{master_ssid[:20]}...|{ref_code}|{ref_url}", flush=True)
    else:
        log(f"[step0] 使用指定 ref_code={ref_code}")
        # 尝试找 master_id
        if not master_id and master_email:
            conn = db_connect(); cur = conn.cursor()
            cur.execute("SELECT id FROM accounts WHERE email=%s", (master_email,))
            row = cur.fetchone(); conn.close()
            if row: master_id = row[0]

    ref_url = f"https://unitool.ai/ref/{ref_code}"
    existing_count = get_existing_ref_count(master_id) if master_id else 0
    remaining = min(batch, 10 - existing_count)
    log(f"[main] ref_code={ref_code} existing={existing_count} remaining={remaining}")

    if remaining <= 0:
        log(f"[main] ⚠ ref_code 已达到10人上限！"); sys.exit(1)

    # ─── Step 1: 注册 referral 账号 ─────────────────────────────────────────────
    ok_count = 0
    used_emails = []

    for i in range(remaining):
        log(f"\n[ref {i+1}/{remaining}] 开始注册 referral 账号…")
        row = get_fresh_outlook(exclude_emails=used_emails)
        if not row:
            log(f"[ref {i+1}] ❌ 没有可用的 outlook 账号"); break
        r_id, r_email, r_password, r_rt = row
        used_emails.append(r_email)
        log(f"[ref {i+1}] 账号: {r_email} id={r_id}")
        mark_account(r_id, "unitool_processing", f"via_ref={ref_code}")

        result = run_pipeline_for_account(r_email, r_password, r_rt, ref_code=ref_code)

        if result["ok"]:
            ok_count += 1
            ssid = result.get("ssid", "")
            log(f"[ref {i+1}] ✅ {r_email} ssid={ssid[:20] if ssid else 'none'}...")
            print(f"[REF_OK] {i+1}|{r_email}|{ssid[:40] if ssid else ''}", flush=True)

            # 从 DB 读最新 ssid（如果注册脚本直接写了 DB）
            if not ssid:
                _, ssid = db_get_ssid_by_email(r_email)

            if master_id:
                db_track_referral(master_id, r_email, r_id, ref_code)
        else:
            reason = result.get("reason", "?")
            log(f"[ref {i+1}] ❌ {r_email} 失败: {reason}")
            print(f"[REF_FAIL] {i+1}|{r_email}|{reason}", flush=True)
            mark_account(r_id, "unitool_fail", f"ref_reg_fail:{reason[:60]}")

        if i < remaining - 1:
            log("[main] 间隔 5s…"); await asyncio.sleep(5)

    print(f"[DONE] {ok_count}/{remaining} ref_code={ref_code}", flush=True)
    log(f"\n[main] 完成 ok={ok_count}/{remaining} ref_code={ref_code} ref_url={ref_url}")

if __name__ == "__main__":
    asyncio.run(main())
