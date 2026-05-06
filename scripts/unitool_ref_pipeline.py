#!/usr/bin/env python3
  """
  unitool_ref_pipeline.py  v2.0
  ==============================

  概述
  ----
  把 unitool.ai 推荐（Referral）计划完整跑通：
    1. 确定 master 账号并拿到其 ref_code（邀请码）
    2. 用 N 个全新 Outlook 账号，通过推荐链接注册 unitool（每个给 master +10 tokens）
    3. 每个成功注册的 referral 账号也自动激活自己的 ref_code，存入 DB
    4. 统一输出结构化日志供上层 API 解析

  Referral 限制
  -------------
  - 每个账号最多邀请 10 人（unitool 官方上限）
  - 每次注册前访问 https://unitool.ai/ref/{ref_code} 写入推荐 cookie，再注册

  上游依赖（必须先有 Outlook 账号）
  ---------------------------------
    POST /api/tools/outlook/register
      -> outlook_register.py（patchright + CF 代理）
      -> 成功后写入 PostgreSQL accounts 表（platform='outlook', status='active', refresh_token 非空）

  脚本间调用关系
  --------------
    unitool_ref_pipeline.py         <- 本脚本（主控）
      ├─ unitool_register.py   --ref-code  # 访问 ref URL 然后注册 referral
      ├─ unitool_pipeline.py   --batch 1   # 注册 master（无 master 时）
      ├─ unitool_login.py      --email ... # 备用：登录获取 ssid
      └─ unitool_reflink.py    --email ... # 获取任意账号 ref_code

  API 端点（tools.ts 暴露）
  ---------------------------
    POST /api/tools/unitool/ref-pipeline
         body: { batch, masterEmail, masterSsid, refCode, masterId }
         返回: { jobId }

    GET  /api/tools/unitool/ref-pipeline/:jobId
         返回: { status, logs, refCode, refUrl, okCount, results[] }

    GET  /api/tools/unitool/reflink?email=&ssid=&accountId=
         返回: { refCode, refUrl, email }

  输出格式（供 tools.ts 解析）
  -----------------------------
    [MASTER]   email|ssid_prefix|ref_code|ref_url
    [REF_OK]   N|email|ssid_prefix
    [REF_CODE] N|email|ref_code|ref_url       <- NEW v2.0: referral 账号自己的 ref_code
    [REF_FAIL] N|email|reason
    [DONE]     ok/total ref_code=XXX

  用法
  ----
    # 最常用：直接用已知 ref_code 跑 10 个 referral
    python3 unitool_ref_pipeline.py --ref-code xjfjk --batch 10

    # 自动从 master 账号邮箱拿 ref_code
    python3 unitool_ref_pipeline.py --master-email sarahrivera639@outlook.com --batch 5

    # 直接传 master ssid（最快，跳过 DB 查询）
    python3 unitool_ref_pipeline.py --master-ssid <ssid> --batch 3

    # 跳过 referral 账号自己的 ref_code 激活
    python3 unitool_ref_pipeline.py --ref-code xjfjk --batch 10 --no-activate-ref

    # 全自动：无现成 master 时自动注册一个
    python3 unitool_ref_pipeline.py --batch 10

  DB tags 约定
  ------------
    unitool_registered    -- 已注册 unitool
    unitool_ref_master    -- 持有 ref_code，可邀请别人
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

  MAX_REFERRALS_PER_CODE = 10   # unitool 官方上限


  # ── 日志 ──────────────────────────────────────────────────────────────────────
  def log(msg):
      ts   = time.strftime("%H:%M:%S")
      line = f"[{ts}] {msg}"
      print(line, flush=True)
      with open(LOG_FILE, "a") as f:
          f.write(line + "\n")


  # ── DB helpers ────────────────────────────────────────────────────────────────
  def db_connect():
      return psycopg2.connect(DB_URL)


  def get_fresh_outlook(exclude_emails=None):
      """
      从 DB 拉一个全新 outlook 账号（有 refresh_token，未注册 unitool，未被排除）。
      供 master 注册或 referral 注册使用。
      返回: (id, email, password, refresh_token) or None
      """
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
      """
      从 accounts.notes 里提取 unitool_ssid。
      兼容两种写法：
        unitool_ssid=<hex64>        -- unitool_pipeline.py
        ' | unitool_ssid=<hex64>'  -- unitool_register.py
      返回: (account_id, ssid_str)
      """
      conn = db_connect(); cur = conn.cursor()
      cur.execute("SELECT id, notes FROM accounts WHERE email=%s", (email,))
      row = cur.fetchone(); conn.close()
      if not row: return None, None
      acc_id, notes = row
      ssid = ""
      if notes:
          m = re.search(r"unitool_ssid=([0-9a-f]{40,})", notes)
          if m: ssid = m.group(1)
          if not ssid:
              m2 = re.search(r"unitool_ssid=([A-Za-z0-9_\-]{20,})", notes)
              if m2: ssid = m2.group(1)
      return acc_id, ssid


  def db_get_ref_code_by_email(email):
      """读取已存入 DB 的 ref_code（格式：unitool_ref_code=XXX）"""
      conn = db_connect(); cur = conn.cursor()
      cur.execute("SELECT notes FROM accounts WHERE email=%s", (email,))
      row = cur.fetchone(); conn.close()
      if not row or not row[0]: return ""
      m = re.search(r"unitool_ref_code=([A-Za-z0-9_-]+)", row[0])
      return m.group(1) if m else ""


  def db_save_ref_code(account_id, ref_code, tag="unitool_ref_master"):
      """
      把 ref_code 写入账号 notes，打指定标签。
      tag: 'unitool_ref_master'（master 账号）或 'unitool_ref_activated'（referral 激活）
      """
      conn = db_connect(); cur = conn.cursor()
      cur.execute("SELECT notes FROM accounts WHERE id=%s", (account_id,))
      row = cur.fetchone()
      notes = row[0] or "" if row else ""
      if f"unitool_ref_code={ref_code}" in notes:
          conn.close(); return
      cur.execute(f"""
          UPDATE accounts SET
            notes = COALESCE(notes,'') || E'\\nunitool_ref_code=' || %s,
            tags  = CASE WHEN COALESCE(tags,'') NOT LIKE '%%{tag}%%'
                         THEN COALESCE(NULLIF(tags,''),'') || ',{tag}'
                         ELSE tags END,
            updated_at = NOW()
          WHERE id = %s
      """, (ref_code, account_id))
      conn.commit(); conn.close()


  def mark_account(account_id, tag, extra_notes=""):
      conn = db_connect(); cur = conn.cursor()
      cur.execute("""
          UPDATE accounts SET
              tags = CASE WHEN COALESCE(tags,'')='' THEN %s
                          WHEN tags NOT LIKE '%%' || %s || '%%' THEN tags || ',' || %s
                          ELSE tags END,
              notes = COALESCE(notes,'') || E'\\n' || %s,
              updated_at = NOW()
          WHERE id = %s
      """, (tag, tag, tag,
            f"{tag} at={time.strftime('%Y-%m-%d %H:%M:%S')} {extra_notes}",
            account_id))
      conn.commit(); conn.close()


  def db_track_referral(master_id, ref_email, ref_acc_id, ref_code):
      """
      在 master 账号 notes 里追加 referral 记录（ref_registered=email|id=N），
      同时给 referral 账号打 unitool_via_ref 标签。
      用于后续统计 master 已邀请多少人。
      """
      conn = db_connect(); cur = conn.cursor()
      cur.execute("""
          UPDATE accounts SET
            notes = COALESCE(notes,'') || E'\\nref_registered=' || %s || '|id=' || %s,
            updated_at = NOW()
          WHERE id = %s
      """, (ref_email, str(ref_acc_id), master_id))
      cur.execute("""
          UPDATE accounts SET
            tags = CASE WHEN COALESCE(tags,'') NOT LIKE '%%unitool_via_ref%%'
                        THEN COALESCE(NULLIF(tags,''),'') || ',unitool_via_ref'
                        ELSE tags END,
            notes = COALESCE(notes,'') || E'\\nvia_ref_code=' || %s || '|master_id=' || %s,
            updated_at = NOW()
          WHERE id = %s
      """, (ref_code, str(master_id), ref_acc_id))
      conn.commit(); conn.close()


  def get_existing_ref_count(master_id):
      """统计 master 账号已成功产生多少条 referral（按 notes 里 ref_registered= 数量）"""
      conn = db_connect(); cur = conn.cursor()
      cur.execute("SELECT notes FROM accounts WHERE id=%s", (master_id,))
      row = cur.fetchone(); conn.close()
      if not row or not row[0]: return 0
      return len(re.findall(r"ref_registered=", row[0]))


  # ── 外部脚本调用 ──────────────────────────────────────────────────────────────
  def _run(cmd, timeout=900):
      """运行子进程，返回 (stdout, stderr, returncode)"""
      env = {**os.environ, "DISPLAY": DISPLAY, "PYTHONUNBUFFERED": "1"}
      proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
      return proc.stdout, proc.stderr, proc.returncode


  def run_register_with_ref(email, ref_code):
      """
      调用 unitool_register.py --email <email> --ref-code <ref_code>
      内部流程（unitool_register.py register_one()）：
        1. 访问 https://unitool.ai/ref/{ref_code} 写入推荐 cookie
        2. 打开注册页，填 email/password，bypass Turnstile
        3. 轮询 Inbox + JunkEmail 找验证邮件（修复：unitool 邮件常落入垃圾箱）
        4. 点击验证链接拿 ssid cookie

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
          if line.strip(): log(f"    {line.strip()}")

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
              "reason": f"exit={rc} stderr={stderr[-150:]}"}


  def fetch_ref_code_via_api(ssid):
      """
      curl /api/auth/session 获取 ref_code。
      unitool session JSON 结构: { auth: { user: { ref_code: "xxxx", ... } } }
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
      调用 unitool_reflink.py --email 获取 ref_code（自动从 DB 读 ssid）。
      输出行格式: [OK] ref_code|ref_url|email|uid
      用于 ssid 未能直接传入时的备用路径。
      """
      stdout, _, _ = _run(["python3", REFLINK, "--email", email], timeout=30)
      ok_line = next((l for l in stdout.split("\n") if l.startswith("[OK]")), "")
      if not ok_line: return ""
      parts = ok_line[5:].split("|")
      return parts[0].strip() if parts else ""


  def get_ssid_via_login(email, password):
      """
      备用：调用 unitool_login.py 登录拿 ssid。
      注册脚本未能持久化 ssid（如 DB 写入失败）时使用。
      输出行格式: [OK] email|password|ssid|...
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


  # ── 主流程 ────────────────────────────────────────────────────────────────────
  async def main():
      ap = argparse.ArgumentParser(description="unitool Referral Pipeline v2.0")
      ap.add_argument("--batch",          type=int, default=10,
                      help="最多注册几个 referral 账号（<=10，受 unitool 上限限制）")
      ap.add_argument("--master-email",   default="",
                      help="master 账号邮箱（已注册 unitool 的账号）")
      ap.add_argument("--master-ssid",    default="",
                      help="master 账号 ssid（直接传入，跳过 DB 查询）")
      ap.add_argument("--ref-code",       default="",
                      help="直接使用指定 ref_code（跳过 master 注册/查询，最快）")
      ap.add_argument("--master-id",      type=int, default=0,
                      help="master 账号 DB id（用于追踪 referral 记录数量）")
      ap.add_argument("--no-activate-ref", action="store_true",
                      help="跳过为每个 referral 账号激活其自己的 ref_code")
      args = ap.parse_args()

      open(LOG_FILE, "w").write("")
      log(f"[main] unitool referral pipeline v2.0 batch={args.batch}")

      batch        = min(args.batch, MAX_REFERRALS_PER_CODE)
      master_id    = args.master_id
      master_ssid  = args.master_ssid
      master_email = args.master_email
      ref_code     = args.ref_code

      # ══════════════════════════════════════════════════════════════════════════
      # Step 0: 确定 ref_code
      # 优先级: 命令行 --ref-code > DB/ssid 查询 > 注册全新 master
      # ══════════════════════════════════════════════════════════════════════════
      if not ref_code:

          # 0a. 从传入的 ssid 直接 API 查询
          if master_ssid:
              log("[step0] 用传入 ssid 查询 ref_code...")
              ref_code = fetch_ref_code_via_api(master_ssid)

          # 0b. 从 DB 中现有 master 账号读 ref_code / ssid
          if not ref_code and (master_email or master_id):
              if master_email:
                  _id, _ssid = db_get_ssid_by_email(master_email)
                  if _id:   master_id   = _id
                  if _ssid: master_ssid = _ssid
              if master_ssid:
                  ref_code = fetch_ref_code_via_api(master_ssid)
              if not ref_code and master_email:
                  ref_code = db_get_ref_code_by_email(master_email)

          # 0c. 注册全新 master 账号（以上全部失败时）
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

              master_id = m_id; master_email = m_email
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
              print(f"[MASTER] {m_email}|{master_ssid[:20]}...|{ref_code}|{ref_url}", flush=True)

      else:
          log(f"[step0] 使用指定 ref_code={ref_code}")
          if not master_id and master_email:
              conn = db_connect(); cur = conn.cursor()
              cur.execute("SELECT id FROM accounts WHERE email=%s", (master_email,))
              row = cur.fetchone(); conn.close()
              if row: master_id = row[0]

      ref_url        = f"https://unitool.ai/ref/{ref_code}"
      existing_count = get_existing_ref_count(master_id) if master_id else 0
      remaining      = min(batch, MAX_REFERRALS_PER_CODE - existing_count)

      log(f"[main] ref_code={ref_code}  url={ref_url}")
      log(f"[main] 已用={existing_count}  本次计划={remaining}  上限={MAX_REFERRALS_PER_CODE}")

      if remaining <= 0:
          log(f"[main] WARNING: ref_code 已达到 {MAX_REFERRALS_PER_CODE} 人上限！")
          print(f"[DONE] 0/0 ref_code={ref_code} (quota_full)", flush=True)
          sys.exit(0)

      # ══════════════════════════════════════════════════════════════════════════
      # Step 1: 逐个注册 referral 账号
      # ══════════════════════════════════════════════════════════════════════════
      ok_count    = 0
      used_emails = []

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
              mark_account(r_id, "unitool_fail", f"ref_reg_fail:{reason[:60]}")
              if i < remaining - 1:
                  await asyncio.sleep(5)
              continue

          ok_count += 1
          r_ssid = result.get("ssid", "")

          # 若注册脚本未返回 ssid，从 DB 或登录获取
          if not r_ssid:
              _, r_ssid = db_get_ssid_by_email(r_email)
          if not r_ssid:
              log(f"  [ref {i+1}] DB 无 ssid，尝试登录...")
              r_ssid = get_ssid_via_login(r_email, r_password)

          log(f"[ref {i+1}] OK {r_email} ssid_len={len(r_ssid) if r_ssid else 0}")
          ssid_disp = (r_ssid[:40] + "...") if r_ssid else ""
          print(f"[REF_OK] {i+1}|{r_email}|{ssid_disp}", flush=True)

          # 追踪 referral 关系到 DB（master notes + referral tag）
          if master_id:
              db_track_referral(master_id, r_email, r_id, ref_code)

          # ── Step 2: 激活 referral 账号自己的 ref_code (v2.0 新增) ────────────
          # 每个新注册账号都有自己的 ref_code，可以继续邀请别人。
          # 这一步获取并存入 DB，方便后续以该账号作为新 master 继续 referral。
          if not args.no_activate_ref:
              log(f"[ref {i+1}] 激活 {r_email} 自己的 ref_code...")
              r_ref_code = ""
              if r_ssid:
                  r_ref_code = fetch_ref_code_via_api(r_ssid)
              if not r_ref_code:
                  # 备用：通过 unitool_reflink.py（会从 DB 读 ssid）
                  r_ref_code = fetch_ref_code_via_script(r_email)

              if r_ref_code:
                  r_ref_url = f"https://unitool.ai/ref/{r_ref_code}"
                  log(f"[ref {i+1}] OK ref_code 激活: {r_ref_code}")
                  db_save_ref_code(r_id, r_ref_code, tag="unitool_ref_activated")
                  print(f"[REF_CODE] {i+1}|{r_email}|{r_ref_code}|{r_ref_url}", flush=True)
              else:
                  log(f"[ref {i+1}] WARNING: 无法获取 ref_code（ssid 可能未就绪）")

          if i < remaining - 1:
              log("[main] 间隔 5s...")
              await asyncio.sleep(5)

      # ══════════════════════════════════════════════════════════════════════════
      # 汇总
      # ══════════════════════════════════════════════════════════════════════════
      log(f"\n{'='*60}")
      log(f"[main] 完成 ok={ok_count}/{remaining}  ref_code={ref_code}  url={ref_url}")
      print(f"[DONE] {ok_count}/{remaining} ref_code={ref_code}", flush=True)


  if __name__ == "__main__":
      asyncio.run(main())
  