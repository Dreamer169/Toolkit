#!/usr/bin/env python3
"""
unitool_ref_pipeline.py  v2.1
==============================

概述
----
把 unitool.ai 推荐（Referral）计划完整跑通：
  1. 确定 master 账号并拿到其 ref_code（邀请码）
  2. 用 N 个全新 Outlook 账号，通过推荐链接注册 unitool（每个给 master +10 tokens）
  3. 每个成功注册的 referral 账号也自动激活自己的 ref_code，存入 DB
  4. [v2.1] --chain 模式：一个 ref_code 跑满 10 人后，自动切换到
     新激活的 referral 账号的 ref_code 继续注册，无限扩展

Referral 限制
-------------
- 每个账号最多邀请 10 人（unitool 官方上限）
- 每次注册前访问 https://unitool.ai/ref/{ref_code} 写入推荐 cookie，再注册

脚本间调用关系
--------------
  unitool_ref_pipeline.py         <- 本脚本（主控）
    |-- unitool_register.py  --ref-code  # 访问 ref URL 然后注册 referral
    |-- unitool_pipeline.py  --batch 1   # 注册 master（无 master 时）
    |-- unitool_login.py     --email ... # 备用：登录获取 ssid
    +-- unitool_reflink.py   --email ... # 获取任意账号 ref_code

输出格式（供 tools.ts 解析）
-----------------------------
  [MASTER]   email|ssid_prefix|ref_code|ref_url
  [REF_OK]   N|email|ssid_prefix
  [REF_CODE] N|email|ref_code|ref_url    <- referral 账号自己的 ref_code
  [REF_FAIL] N|email|reason
  [CHAIN]    depth|old_ref_code|new_ref_code|new_master_email
  [DONE]     ok/total ref_code=XXX depth=D

用法
----
  # 最常用：用已知 ref_code 跑 10 个 referral
  python3 unitool_ref_pipeline.py --ref-code xjfjk --batch 10

  # chain 模式：跑完 10 个后自动切换到新 ref_code 继续，最多跑 50 个
  python3 unitool_ref_pipeline.py --ref-code xjfjk --batch 50 --chain

  # chain + 限制链路深度（最多切换几次 ref_code）
  python3 unitool_ref_pipeline.py --ref-code xjfjk --batch 50 --chain --max-depth 5

  # 从 master 邮箱自动获取 ref_code
  python3 unitool_ref_pipeline.py --master-email sarahrivera639@outlook.com --batch 10

  # 全自动：无 master 时自动注册一个再跑 referral
  python3 unitool_ref_pipeline.py --batch 10

DB tags 约定
------------
  unitool_registered    -- 已注册 unitool
  unitool_ref_master    -- 持有 ref_code，可邀请别人（原始 master）
  unitool_via_ref       -- 通过推荐链接注册
  unitool_ref_activated -- referral 账号自己的 ref_code 已激活存入 DB
"""
import asyncio, json, os, re, subprocess, sys, time, argparse
import psycopg2

DB_URL   = "postgresql://postgres:postgres@localhost/toolkit"
DISPLAY  = ":99"
_here    = os.path.dirname(os.path.abspath(__file__))
SCRIPTS  = _here if os.path.exists(os.path.join(_here, "unitool_pipeline.py")) else "/root/Toolkit/scripts"
PIPELINE = os.path.join(SCRIPTS, "unitool_pipeline.py")
REGISTER = os.path.join(SCRIPTS, "unitool_register.py")
REFLINK  = os.path.join(SCRIPTS, "unitool_reflink.py")
LOGIN    = os.path.join(SCRIPTS, "unitool_login.py")
LOG_FILE = "/tmp/unitool_ref_pipeline.log"

MAX_REFERRALS_PER_CODE = 10   # unitool 官方每个 ref_code 上限


# ── 日志 ──────────────────────────────────────────────────────────────────────
def log(msg):
    ts   = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── DB helpers ────────────────────────────────────────────────────────────────
def db_connect():
    return psycopg2.connect(DB_URL)


def get_fresh_outlook(exclude_emails=None):
    """
    从 DB 拉一个全新 outlook 账号（有 refresh_token，未注册 unitool，未被排除）。
    返回: (id, email, password, refresh_token) or None
    """
    conn = db_connect()
    cur  = conn.cursor()
    excl = tuple(exclude_emails or []) or ("__NONE__",)
    cur.execute("""
        SELECT id, email, password, refresh_token FROM accounts
        WHERE platform='outlook' AND status='active'
          AND refresh_token IS NOT NULL AND refresh_token != ''
          AND LENGTH(COALESCE(password,'')) >= 12
          AND (tags IS NULL OR (
               tags NOT LIKE '%%unitool_registered%%'
           AND tags NOT LIKE '%%unitool_fail%%'
           AND tags NOT LIKE '%%unitool_already%%'
           AND (tags NOT LIKE '%%unitool_reg_retry%%' OR updated_at < NOW() - INTERVAL '4 hours')
           AND tags NOT LIKE '%%unitool_processing%%'
           AND tags NOT LIKE '%%token_invalid%%'
          ))
          AND email NOT IN %s
        ORDER BY created_at DESC NULLS LAST
        LIMIT 1
    """, (excl,))
    row = cur.fetchone()
    conn.close()
    return row


def db_get_ssid_by_email(email):
    """
    从 accounts.notes 里提取 unitool_ssid。
    兼容：unitool_ssid=<hex>  和  ' | unitool_ssid=<hex>'
    返回: (account_id, ssid_str)
    """
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("SELECT id, notes FROM accounts WHERE email=%s", (email,))
    row  = cur.fetchone()
    conn.close()
    if not row:
        return None, None
    acc_id, notes = row
    ssid = ""
    if notes:
        m = re.search(r"unitool_ssid=([0-9a-f]{40,})", notes)
        if m:
            ssid = m.group(1)
        if not ssid:
            m2 = re.search(r"unitool_ssid=([A-Za-z0-9_\-]{20,})", notes)
            if m2:
                ssid = m2.group(1)
    return acc_id, ssid


def db_get_ref_code_by_email(email):
    """读取已存入 DB 的 ref_code（格式：unitool_ref_code=XXX）"""
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("SELECT notes FROM accounts WHERE email=%s", (email,))
    row  = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        return ""
    m = re.search(r"unitool_ref_code=([A-Za-z0-9_-]+)", row[0])
    return m.group(1) if m else ""


def db_save_ref_code(account_id, ref_code, tag="unitool_ref_master"):
    """
    把 ref_code 写入账号 notes，并打指定标签。
    修复：不再把 tag 插入 f-string SQL，改用两次 UPDATE 规避 SQL 注入。
    """
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("SELECT notes FROM accounts WHERE id=%s", (account_id,))
    row   = cur.fetchone()
    notes = row[0] or "" if row else ""
    if f"unitool_ref_code={ref_code}" in notes:
        conn.close()
        return
    # 写 notes
    cur.execute("""
        UPDATE accounts
        SET notes = COALESCE(notes,'') || E'\\n' || %s,
            updated_at = NOW()
        WHERE id = %s
    """, (f"unitool_ref_code={ref_code}", account_id))
    # 打标签（两次 UPDATE，用参数化 LIKE，不拼接 SQL）
    cur.execute("""
        UPDATE accounts
        SET tags = CASE
                     WHEN COALESCE(tags,'') = ''
                       THEN %s
                     WHEN POSITION(%s IN COALESCE(tags,'')) = 0
                       THEN COALESCE(NULLIF(tags,''),'') || ',' || %s
                     ELSE tags
                   END,
            updated_at = NOW()
        WHERE id = %s
    """, (tag, tag, tag, account_id))
    conn.commit()
    conn.close()


def mark_account(account_id, tag, extra_notes=""):
    """给账号打 tag 并在 notes 里追加一行记录。"""
    conn = db_connect()
    cur  = conn.cursor()
    note_line = f"{tag} at={time.strftime('%Y-%m-%d %H:%M:%S')} {extra_notes}"
    cur.execute("""
        UPDATE accounts
        SET tags = CASE
                     WHEN COALESCE(tags,'') = ''
                       THEN %s
                     WHEN POSITION(%s IN COALESCE(tags,'')) = 0
                       THEN tags || ',' || %s
                     ELSE tags
                   END,
            notes = COALESCE(notes,'') || E'\\n' || %s,
            updated_at = NOW()
        WHERE id = %s
    """, (tag, tag, tag, note_line, account_id))
    conn.commit()
    conn.close()


def db_track_referral(master_id, ref_email, ref_acc_id, ref_code):
    """
    在 master 账号 notes 里追加 referral 记录（ref_registered=email|id=N），
    同时给 referral 账号打 unitool_via_ref 标签。
    """
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("""
        UPDATE accounts
        SET notes = COALESCE(notes,'') || E'\\n' || %s,
            updated_at = NOW()
        WHERE id = %s
    """, (f"ref_registered={ref_email}|id={ref_acc_id}", master_id))
    cur.execute("""
        UPDATE accounts
        SET tags = CASE
                     WHEN POSITION('unitool_via_ref' IN COALESCE(tags,'')) = 0
                       THEN COALESCE(NULLIF(tags,''),'') || ',unitool_via_ref'
                     ELSE tags
                   END,
            notes = COALESCE(notes,'') || E'\\n' || %s,
            updated_at = NOW()
        WHERE id = %s
    """, (f"via_ref_code={ref_code}|master_id={master_id}", ref_acc_id))
    conn.commit()
    conn.close()


def get_existing_ref_count(master_id):
    """统计 master 已产生多少条 referral（按 notes 里 ref_registered= 数量）。"""
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("SELECT notes FROM accounts WHERE id=%s", (master_id,))
    row  = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        return 0
    return len(re.findall(r"ref_registered=", row[0]))


def db_pick_activated_ref(exclude_ref_codes=None):
    """
    [chain 模式] 从 unitool_ref_activated 账号里取一个尚未用过的 ref_code。
    排除已经跑过的 ref_codes。
    返回: (account_id, email, ref_code) or None
    """
    conn = db_connect()
    cur  = conn.cursor()
    cur.execute("""
        SELECT id, email, notes FROM accounts
        WHERE tags LIKE '%%unitool_ref_activated%%'
          AND notes LIKE '%%unitool_ref_code=%%'
        ORDER BY id DESC
    """)
    rows = cur.fetchall()
    conn.close()
    excl = set(exclude_ref_codes or [])
    for row in rows:
        acc_id, email, notes = row
        m = re.search(r"unitool_ref_code=([A-Za-z0-9_-]+)", notes or "")
        if not m:
            continue
        rc = m.group(1)
        if rc in excl:
            continue
        return acc_id, email, rc
    return None


# ── 外部脚本调用 ──────────────────────────────────────────────────────────────
def _run(cmd, timeout=900):
    """运行子进程，返回 (stdout, stderr, returncode)"""
    env  = {**os.environ, "DISPLAY": DISPLAY, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    return proc.stdout, proc.stderr, proc.returncode



# ── 失败分类 ─────────────────────────────────────────────────────────────────
def classify_reg_fail(reason: str):
    """返回 (tag, notes_prefix)。
    永久: unitool_already
    待验证: unitool_verify_pending  (注册已提交，验证邮件未到)
    暂态: unitool_reg_retry         (CF/pydoll崩溃/超时，4h后重试)
    """
    r = reason.lower()
    if any(p in r for p in ('already_registered','already_reg','already exist',
                              'user with like email','email_already','already_use')):
        return 'unitool_already', 'already_registered'
    if any(p in r for p in ('no_verify_email','verify_email_not_found','verify_email')):
        return 'unitool_verify_pending', 'verify_pending'
    # CF/pydoll崩溃/超时/no_redirect_no_ssid → 暂态
    return 'unitool_reg_retry', 'reg_retry'

def run_register_with_ref(email, ref_code):
    """
    调用 unitool_register.py --email <email> --ref-code <ref_code>
    内部流程：
      1. 访问 https://unitool.ai/ref/{ref_code} 写入推荐 cookie
      2. 打开注册页，填 email/password，bypass Turnstile
      3. 轮询 Inbox + JunkEmail 找验证邮件（unitool 邮件常落入垃圾箱）
      4. 点击验证链接拿 __Secure-unitool-ssid cookie

    返回: {"ok": bool, "email": str, "ssid": str, "reason": str}
    """
    log(f"  [register] {email} via ref_code={ref_code}")
    stdout, stderr, rc = _run([
        "python3", REGISTER,
        "--email",    email,
        "--count",    "1",
        "--ref-code", ref_code,
    ])
    for line in stdout.split("\n"):
        if line.strip():
            log(f"    {line.strip()}")

    ok_line   = next((l for l in stdout.split("\n") if l.startswith("[OK]")),   "")
    fail_line = next((l for l in stdout.split("\n") if l.startswith("[FAIL]")), "")

    if ok_line:
        parts = ok_line.strip().split("|")
        ssid  = parts[1].strip() if len(parts) > 1 else ""
        return {"ok": True, "email": email, "ssid": ssid}
    if fail_line:
        reason = (fail_line.split("|")[1].strip() if "|" in fail_line else fail_line[7:])[:120]
        return {"ok": False, "email": email, "reason": reason}
    if rc == 0:
        return {"ok": True, "email": email, "ssid": "", "note": "no_ok_line"}
    return {"ok": False, "email": email,
            "reason": f"exit={rc} stderr={stderr[-120:]}"}


def fetch_ref_code_via_api(ssid):
    """
    curl /api/auth/session 获取 ref_code。
    JSON 结构: { auth: { user: { ref_code: "xxxx" } } }
    返回 ref_code 字符串，失败返回空串。
    """
    stdout, _, _ = _run([
        "curl", "-s",
        "-b", f"__Secure-unitool-ssid={ssid}",
        "-H", "Accept: application/json",
        "--max-time", "15",
        "https://unitool.ai/api/auth/session",
    ], timeout=25)
    try:
        data = json.loads(stdout)
        return data.get("auth", {}).get("user", {}).get("ref_code", "")
    except Exception:
        return ""


def fetch_ref_code_via_script(email):
    """
    调用 unitool_reflink.py --email 拿 ref_code（脚本自动从 DB 读 ssid）。
    输出格式: [OK] ref_code|ref_url|email|uid
    """
    stdout, _, _ = _run(["python3", REFLINK, "--email", email], timeout=30)
    ok_line = next((l for l in stdout.split("\n") if l.startswith("[OK]")), "")
    if not ok_line:
        return ""
    parts = ok_line[5:].split("|")
    return parts[0].strip() if parts else ""


def get_ssid_via_login(email, password):
    """
    备用：调用 unitool_login.py 登录拿 ssid。
    注册脚本未能持久化 ssid 时使用。
    输出格式: [OK] email|password|ssid|...
    """
    stdout, _, _ = _run([
        "python3", LOGIN,
        "--email", email, "--password", password, "--no-headless",
    ], timeout=180)
    for line in stdout.split("\n"):
        if line.startswith("[OK]"):
            parts = line.split("|")
            if len(parts) >= 3:
                return parts[2].strip()
    return ""


# ── 单 ref_code 批次注册 ───────────────────────────────────────────────────────
async def run_batch(ref_code, master_id, batch_size, used_emails,
                    activate_ref=True):
    """
    用 ref_code 注册 batch_size 个 referral 账号。
    返回 (ok_count, newly_activated_refs) — 后者是 [(acc_id, email, ref_code), ...]
    """
    newly_activated = []
    ok_count        = 0
    existing_count  = get_existing_ref_count(master_id) if master_id else 0
    remaining       = min(batch_size, MAX_REFERRALS_PER_CODE - existing_count)

    if remaining <= 0:
        log(f"[batch] ref_code={ref_code} 已达上限 {MAX_REFERRALS_PER_CODE}，需要切换")
        return 0, [], True    # ref_code_full=True

    ref_url = f"https://unitool.ai/ref/{ref_code}"
    log(f"[batch] ref_code={ref_code}  url={ref_url}")
    log(f"[batch] 已用={existing_count}  本次计划={remaining}  上限={MAX_REFERRALS_PER_CODE}")

    for i in range(remaining):
        log(f"\n{'─'*60}")
        log(f"[ref {i+1}/{remaining}] 开始...")

        row = get_fresh_outlook(exclude_emails=used_emails)
        if not row:
            log(f"[ref {i+1}] ERROR: 没有可用的 outlook 账号，提前结束")
            break
        r_id, r_email, r_password, r_rt = row
        used_emails.append(r_email)
        log(f"[ref {i+1}] 账号: {r_email} id={r_id}")
        mark_account(r_id, "unitool_processing", f"via_ref={ref_code}")

        # 注册（访问推荐链接 -> 注册 -> 邮件验证 -> 拿 ssid）
        result = run_register_with_ref(r_email, ref_code)

        if not result["ok"]:
            reason = result.get("reason", "?")
            log(f"[ref {i+1}] FAIL: {reason}")
            print(f"[REF_FAIL] {i+1}|{r_email}|{reason}", flush=True)
            _ftag = classify_reg_fail(reason)
            if _ftag == 'unitool_verify_pending':
                mark_account(r_id, 'unitool_verify_pending', f'ref_verify_pending:{reason[:60]}')
            elif _ftag == 'unitool_already':
                mark_account(r_id, 'unitool_already', f'ref_already:{reason[:60]}')
            else:
                mark_account(r_id, 'unitool_reg_retry', f'ref_reg_fail:{reason[:60]}')
            if i < remaining - 1:
                await asyncio.sleep(5)
            continue

        ok_count += 1
        r_ssid    = result.get("ssid", "")

        # ssid 三级兜底：注册返回 -> DB 查询 -> 登录
        if not r_ssid:
            _, r_ssid = db_get_ssid_by_email(r_email)
        if not r_ssid:
            log(f"  [ref {i+1}] DB 无 ssid，尝试登录...")
            r_ssid = get_ssid_via_login(r_email, r_password)

        log(f"[ref {i+1}] OK {r_email} ssid_len={len(r_ssid) if r_ssid else 0}")
        ssid_disp = (r_ssid + "...") if r_ssid else ""
        print(f"[REF_OK] {i+1}|{r_email}|{ssid_disp}", flush=True)

        # 追踪 referral 关系到 DB
        if master_id:
            db_track_referral(master_id, r_email, r_id, ref_code)

        # 激活 referral 账号自己的 ref_code
        if activate_ref:
            log(f"[ref {i+1}] 激活 {r_email} 自己的 ref_code...")
            r_ref_code = ""
            if r_ssid:
                r_ref_code = fetch_ref_code_via_api(r_ssid)
            if not r_ref_code:
                r_ref_code = fetch_ref_code_via_script(r_email)

            if r_ref_code:
                r_ref_url = f"https://unitool.ai/ref/{r_ref_code}"
                log(f"[ref {i+1}] OK ref_code 激活: {r_ref_code}")
                db_save_ref_code(r_id, r_ref_code, tag="unitool_ref_activated")
                print(f"[REF_CODE] {i+1}|{r_email}|{r_ref_code}|{r_ref_url}", flush=True)
                newly_activated.append((r_id, r_email, r_ref_code))
            else:
                log(f"[ref {i+1}] WARNING: 无法获取 ref_code（ssid 可能未就绪）")

        if i < remaining - 1:
            log("[batch] 间隔 5s...")
            await asyncio.sleep(5)

    return ok_count, newly_activated, False  # ref_code_full=False


# ── 主流程 ────────────────────────────────────────────────────────────────────
async def main():
    ap = argparse.ArgumentParser(description="unitool Referral Pipeline v2.1")
    ap.add_argument("--batch",          type=int, default=10,
                    help="目标注册账号总数（chain 模式下跨多个 ref_code）")
    ap.add_argument("--master-email",   default="",
                    help="master 账号邮箱（已注册 unitool）")
    ap.add_argument("--master-ssid",    default="",
                    help="master 账号 ssid（直接传入，跳过 DB 查询）")
    ap.add_argument("--ref-code",       default="",
                    help="直接使用指定 ref_code（最快）")
    ap.add_argument("--master-id",      type=int, default=0,
                    help="master 账号 DB id")
    ap.add_argument("--no-activate-ref", action="store_true",
                    help="跳过为每个 referral 账号激活其自己的 ref_code")
    ap.add_argument("--chain",          action="store_true",
                    help="[v2.1] chain 模式：一个 ref_code 跑满后自动切换到下一个")
    ap.add_argument("--max-depth",      type=int, default=10,
                    help="[v2.1] chain 最多切换几次 ref_code（默认 10）")
    args = ap.parse_args()

    open(LOG_FILE, "w").write("")
    log(f"[main] unitool referral pipeline v2.1 batch={args.batch} chain={args.chain}")

    batch        = args.batch
    master_id    = args.master_id
    master_ssid  = args.master_ssid
    master_email = args.master_email
    ref_code     = args.ref_code
    activate_ref = not args.no_activate_ref

    # ══════════════════════════════════════════════════════════════════════════
    # Step 0: 确定初始 ref_code
    # 优先级: 命令行 --ref-code > DB/ssid 查询 > 注册全新 master
    # ══════════════════════════════════════════════════════════════════════════
    if not ref_code:
        # 0a. 从传入 ssid 查
        if master_ssid:
            log("[step0] 用传入 ssid 查询 ref_code...")
            ref_code = fetch_ref_code_via_api(master_ssid)

        # 0b. 从 DB 查已有 master
        if not ref_code and (master_email or master_id):
            if master_email:
                _id, _ssid = db_get_ssid_by_email(master_email)
                if _id:   master_id   = _id
                if _ssid: master_ssid = _ssid
            if master_ssid:
                ref_code = fetch_ref_code_via_api(master_ssid)
            if not ref_code and master_email:
                ref_code = db_get_ref_code_by_email(master_email)

        # 0c. 注册全新 master
        if not ref_code:
            log("[step0] 无可用 ref_code，注册全新 master 账号...")
            row = get_fresh_outlook()
            if not row:
                log("[step0] ERROR: 没有可用的未注册 outlook 账号！")
                sys.exit(1)
            m_id, m_email, m_password, m_rt = row
            log(f"[step0] master: {m_email} id={m_id}")
            mark_account(m_id, "unitool_processing", "ref_master")

            env = {**os.environ, "DISPLAY": DISPLAY, "PYTHONUNBUFFERED": "1"}
            proc = subprocess.run(
                ["python3", PIPELINE, "--batch", "1"],
                capture_output=True, text=True, timeout=900, env=env)
            for ln in proc.stdout.split("\n"):
                if ln.strip(): log(f"  {ln.strip()}")

            master_id    = m_id
            master_email = m_email
            _, master_ssid = db_get_ssid_by_email(m_email)
            if not master_ssid:
                log("[step0] DB 无 ssid，尝试登录...")
                master_ssid = get_ssid_via_login(m_email, m_password)
            if not master_ssid:
                log("[step0] ERROR: master 注册失败：无法获取 ssid")
                sys.exit(1)

            ref_code = fetch_ref_code_via_api(master_ssid)
            if not ref_code:
                log("[step0] ERROR: 无法获取 master ref_code")
                sys.exit(1)

            db_save_ref_code(master_id, ref_code, tag="unitool_ref_master")
            ref_url = f"https://unitool.ai/ref/{ref_code}"
            log(f"[step0] OK master={m_email} ref_code={ref_code}")
            print(f"[MASTER] {m_email}|{master_ssid}...|{ref_code}|{ref_url}", flush=True)

    else:
        log(f"[step0] 使用指定 ref_code={ref_code}")
        if not master_id and master_email:
            conn = db_connect(); cur = conn.cursor()
            cur.execute("SELECT id FROM accounts WHERE email=%s", (master_email,))
            row = cur.fetchone(); conn.close()
            if row: master_id = row[0]

    # ══════════════════════════════════════════════════════════════════════════
    # Step 1: 注册 referral 账号（支持 chain 模式）
    # ══════════════════════════════════════════════════════════════════════════
    total_ok      = 0
    remaining     = batch
    used_emails   = []
    used_ref_codes = {ref_code}
    chain_depth   = 0
    cur_ref_code  = ref_code
    cur_master_id = master_id

    while remaining > 0:
        log(f"\n{'='*60}")
        log(f"[chain depth={chain_depth}] ref_code={cur_ref_code} 目标剩余={remaining}")

        this_batch = min(remaining, MAX_REFERRALS_PER_CODE)
        ok, newly, ref_code_full = await run_batch(
            cur_ref_code, cur_master_id, this_batch,
            used_emails, activate_ref=activate_ref)

        total_ok  += ok
        if not ref_code_full:
            remaining -= ok   # 只有实际尝试过才减

        if remaining <= 0:
            break

        # chain 模式：切换到新 ref_code
        # ref_code 满了必须换（即使非 chain 模式）；非 chain 模式且未满就结束
        if not ref_code_full and not args.chain:
            log("[main] 非 chain 模式，结束")
            break

        if chain_depth >= args.max_depth:
            log(f"[main] 已达 chain 最大深度 {args.max_depth}，停止")
            break

        # 优先从本次新激活的 ref_code 里取
        next_entry = None
        for entry in newly:
            _, _, rc = entry
            if rc not in used_ref_codes:
                next_entry = entry
                break

        # 备用：从 DB 里取历史激活的
        if not next_entry:
            next_entry = db_pick_activated_ref(exclude_ref_codes=list(used_ref_codes))

        if not next_entry:
            log("[chain] 没有可用的下一个 ref_code，停止")
            break

        next_id, next_email, next_rc = next_entry
        chain_depth += 1
        used_ref_codes.add(next_rc)
        log(f"[chain] 切换 ref_code: {cur_ref_code} -> {next_rc} (来自 {next_email})")
        print(f"[CHAIN] {chain_depth}|{cur_ref_code}|{next_rc}|{next_email}", flush=True)
        cur_ref_code  = next_rc
        cur_master_id = next_id
        await asyncio.sleep(3)

    # ══════════════════════════════════════════════════════════════════════════
    # 汇总
    # ══════════════════════════════════════════════════════════════════════════
    log(f"\n{'='*60}")
    log(f"[main] 完成 total_ok={total_ok} depth={chain_depth} ref_code={ref_code}")
    print(f"[DONE] {total_ok}/{batch} ref_code={ref_code} depth={chain_depth}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
