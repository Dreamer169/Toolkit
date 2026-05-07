#!/usr/bin/env python3
"""
unitool_chain_v3.py — 端到端全自动链路 v3.0
============================================
整合所有子脚本，闭环实现完整链路：
  outlook_register.py（水位补充）
    → unitool_register.py --ref-code（注册+验证）
      → unitool_login.py（ssid 兜底登录）
        → unitool_reflink.py（激活 ref_code 链）
          → unitool_proxy.py /add-ssid（热推 ssid 到反代池）

PM2 模式：每次处理一个账号后退出，PM2 自动重启继续下一个

关键修复：
  ① ssid 全长保存（覆盖 unitool_register.py 的 80 字截断）
  ② ref_code 自动轮换（每个 ref_code 最多邀请 10 人）
  ③ 水位检查 + 非阻塞触发 outlook 账号补充
  ④ ssid 同时写 /data/unitool_ssids/ + /tmp/ + proxy /add-ssid 热推
  ⑤ atexit 兜底：崩溃时自动解锁账号

日志：/tmp/unitool_chain_v3.log
"""
import atexit, glob, json, os, re, subprocess, sys, time
import urllib.parse, urllib.request
import psycopg2

# ── 常量 ──────────────────────────────────────────────────────────────────────
LOG            = "/tmp/unitool_chain_v3.log"
DB_URL         = "postgresql://postgres:postgres@localhost/toolkit"
SCRIPTS        = "/root/Toolkit/scripts"
REGISTER_PY    = f"{SCRIPTS}/unitool_register.py"
REFLINK_PY     = f"{SCRIPTS}/unitool_reflink.py"
LOGIN_PY       = f"{SCRIPTS}/unitool_login.py"

SSID_DIR       = "/data/unitool_ssids"   # proxy 优先读取目录
PROXY_PORT     = 8089                    # unitool_proxy.py 监听端口
API_BASE       = "http://localhost:8081/api"  # api-server 地址

MAX_REF_SLOTS   = 10    # unitool 每个 ref_code 最多邀请人数
WATERMARK       = 5     # fresh 账号低于此值时触发 outlook 补充
REPLENISH_CNT   = 5     # 单次补充目标数量
COOLDOWN_S      = 900   # 水位补充冷却（15 分钟）
LOCK_FILE       = "/tmp/unitool_chain_replenish.lock"

CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"   # reg_v2 用的 MS OAuth client

# 全局状态（供 atexit 使用）
_account_id   = None
_success_flag = False

os.makedirs(SSID_DIR, exist_ok=True)

# ── 日志 ──────────────────────────────────────────────────────────────────────
def log(msg):
    ts   = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── atexit：崩溃时解锁 ────────────────────────────────────────────────────────
def _atexit_handler():
    if not _account_id or _success_flag:
        return
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("SELECT tags FROM accounts WHERE id=%s", (_account_id,))
        row  = cur.fetchone()
        tags = row[0] if row and row[0] else ""
        if "unitool_registered" not in tags:
            new_tags = re.sub(r",?unitool_processing", "", tags).strip(",")
            # atexit: 崩溃时用 reg_retry 而非永久 fail
            # already_registered 类账号 (unitool_already) 不加 reg_retry — 永久跳过
            if ("unitool_reg_retry" not in new_tags
                    and "unitool_registered" not in new_tags
                    and "unitool_already" not in new_tags
                    and "unitool_verify_pending" not in new_tags):
                new_tags = (new_tags + ",unitool_reg_retry").strip(",")
            cur.execute(
                "UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s",
                (new_tags, _account_id))
            conn.commit()
            log(f"[atexit] id={_account_id} → {new_tags}")
        conn.close()
    except Exception as e:
        log(f"[atexit] err: {e}")

atexit.register(_atexit_handler)

# ── DB 工具 ───────────────────────────────────────────────────────────────────
def db_connect():
    return psycopg2.connect(DB_URL)

def db_get_fresh_account():
    """取一个未注册过 unitool 的 outlook 账号（有密码 + refresh_token）"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("""
        SELECT id, email, password, refresh_token FROM accounts
        WHERE platform='outlook' AND status='active'
          AND refresh_token IS NOT NULL AND refresh_token != ''
          AND LENGTH(COALESCE(password,'')) >= 8
          AND (tags IS NULL OR (
               tags NOT LIKE '%%unitool_registered%%'
           AND tags NOT LIKE '%%unitool_fail%%'
           AND tags NOT LIKE '%%unitool_already%%'
           AND (tags NOT LIKE '%%unitool_reg_retry%%' OR updated_at < NOW() - INTERVAL '4 hours')
           AND tags NOT LIKE '%%unitool_processing%%'
           AND tags NOT LIKE '%%unitool_already%%'
           AND tags NOT LIKE '%%unitool_rescue_dead%%'
           AND tags NOT LIKE '%%unitool_verify_pending%%'
          ))
        ORDER BY RANDOM() LIMIT 1
    """)
    row = cur.fetchone(); conn.close()
    return row  # (id, email, password, refresh_token) or None

def db_count_fresh():
    conn = db_connect(); cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM accounts
        WHERE platform='outlook' AND status='active'
          AND refresh_token IS NOT NULL AND refresh_token != ''
          AND LENGTH(COALESCE(password,'')) >= 8
          AND (tags IS NULL OR (
               tags NOT LIKE '%%unitool_registered%%'
           AND tags NOT LIKE '%%unitool_fail%%'
           AND tags NOT LIKE '%%unitool_already%%'
           AND (tags NOT LIKE '%%unitool_reg_retry%%' OR updated_at < NOW() - INTERVAL '4 hours')
           AND tags NOT LIKE '%%unitool_processing%%'
           AND tags NOT LIKE '%%unitool_already%%'
           AND tags NOT LIKE '%%unitool_rescue_dead%%'
           AND tags NOT LIKE '%%unitool_verify_pending%%'
          ))
    """)
    count = cur.fetchone()[0]; conn.close()
    return count

def db_lock_account(account_id):
    """打 unitool_processing 标签，防止并发重复选取"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("SELECT tags FROM accounts WHERE id=%s", (account_id,))
    row = cur.fetchone(); tags = row[0] if row and row[0] else ""
    if "unitool_processing" not in tags:
        new_tags = (tags + ",unitool_processing").strip(",")
        cur.execute("UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s",
                    (new_tags, account_id))
        conn.commit()
    conn.close()


# ── 失败分类器 (ref_reg_fail 核心修复) ─────────────────────────────────────
def classify_reg_fail(reason: str) -> str:
    """
    永久  → unitool_already      (already_registered 等账号级错误)
    待验证 → unitool_verify_pending (注册提交成功但验证邮件未到，交给 verify_rescue)
    暂态  → unitool_reg_retry     (CF/pydoll 崩溃/超时，4h 后自动重试)
    """
    r = reason.lower()
    if any(p in r for p in ('already_registered','already_reg','already exist',
                              'user with like email','email_already','already_use')):
        return 'unitool_already'
    if any(p in r for p in ('no_verify_email','verify_email_not_found','verify_email')):
        return 'unitool_verify_pending'
    return 'unitool_reg_retry'

def db_mark_fail(account_id, reason=""):
    """注册失败：按类型分类，避免瞬态错误被永久标为 unitool_fail"""
    fail_tag = classify_reg_fail(reason)
    conn = db_connect(); cur = conn.cursor()
    cur.execute("SELECT tags FROM accounts WHERE id=%s", (account_id,))
    row = cur.fetchone(); tags = row[0] if row and row[0] else ""
    new_tags = re.sub(r",?unitool_processing", "", tags).strip(",")
    if fail_tag == "unitool_already":
        if "unitool_already" not in new_tags:
            new_tags = (new_tags + ",unitool_already").strip(",")
        # Strip any existing unitool_fail (no place for permanently-registered accounts)
        new_tags = re.sub(r",?unitool_fail", "", new_tags).strip(",")
    elif fail_tag == "unitool_verify_pending":
        if "unitool_verify_pending" not in new_tags:
            new_tags = (new_tags + ",unitool_verify_pending").strip(",")
    else:
        # unitool_reg_retry: 4h 后自动重试
        if "unitool_reg_retry" not in new_tags:
            new_tags = (new_tags + ",unitool_reg_retry").strip(",")
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    # note prefix matches actual fail_tag type (avoids confusion in notes)
    _note_prefix = ("unitool_already" if fail_tag == "unitool_already"
                    else "unitool_verify_pending" if fail_tag == "unitool_verify_pending"
                    else "unitool_reg_retry")
    note_line = "\n" + _note_prefix + "_fail=" + reason[:120] + " at=" + ts
    cur.execute("UPDATE accounts SET tags=%s, notes=COALESCE(notes,'') || %s, updated_at=NOW() WHERE id=%s",
                (new_tags, note_line, account_id))
    conn.commit(); conn.close()
    log(f"[db_mark_fail] id={account_id} → {new_tags} reason={reason[:60]}")

def db_save_ssid_full(account_id, email, ssid):
    """
    关键修复：保存完整 ssid 到 DB（覆盖 unitool_register.py 的 80 字截断）。
    同时将 tags 清理为 unitool_registered，去掉 processing/fail。
    """
    conn = db_connect(); cur = conn.cursor()
    # 先读当前 notes
    cur.execute("SELECT notes, tags FROM accounts WHERE id=%s", (account_id,))
    row = cur.fetchone()
    notes = row[0] or "" if row else ""
    tags  = row[1] or "" if row else ""

    # 替换已有截断的 unitool_ssid（任意长度），或追加新行
    if re.search(r"unitool_ssid=[A-Za-z0-9_-]+", notes):
        notes = re.sub(r"unitool_ssid=[A-Za-z0-9_-]+", f"unitool_ssid={ssid}", notes)
    else:
        notes = notes + f"\nunitool_ssid={ssid}"

    # 追加时间戳行（方便 debug）
    notes = notes + f"\nchain_v3_saved at={time.strftime('%Y-%m-%d %H:%M:%S')}"

    # 清理 tags
    new_tags = re.sub(r",?unitool_(processing|fail|verify_pending)", "", tags).strip(",")
    if "unitool_registered" not in new_tags:
        new_tags = (new_tags + ",unitool_registered").strip(",")

    cur.execute("""UPDATE accounts SET tags=%s, notes=%s, updated_at=NOW() WHERE id=%s""",
                (new_tags, notes, account_id))
    conn.commit(); conn.close()
    log(f"[DB] ssid全长保存 {email} id={account_id} ssid_len={len(ssid)}")

def db_save_ref_code(account_id, ref_code):
    """保存 ref_code 到 notes，打 unitool_ref_activated 标签"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("SELECT notes, tags FROM accounts WHERE id=%s", (account_id,))
    row = cur.fetchone()
    notes = row[0] or "" if row else ""
    tags  = row[1] or "" if row else ""
    if f"unitool_ref_code={ref_code}" in notes:
        conn.close(); return
    notes += f"\nunitool_ref_code={ref_code}"
    # 打 unitool_ref_activated（referral 账号自己的 ref_code 已激活）
    tag_to_add = "unitool_ref_activated"
    if tag_to_add not in tags:
        tags = (tags + f",{tag_to_add}").strip(",")
    cur.execute("UPDATE accounts SET notes=%s, tags=%s, updated_at=NOW() WHERE id=%s",
                (notes, tags, account_id))
    conn.commit(); conn.close()
    log(f"[DB] ref_code 保存 id={account_id} ref_code={ref_code}")

def db_get_current_ref_code():
    """
    从 DB 获取当前可用 ref_code（剩余邀请槽 > 0）。
    优先选 unitool_ref_activated 中使用最少的，最后兜底 unitool_ref_master。
    返回 (account_id, email, ref_code, used_count) 或 (None, None, "", 0)
    """
    conn = db_connect(); cur = conn.cursor()
    # 查所有有 ref_code 的账号（activated 优先，其次 master）
    cur.execute("""
        SELECT id, email, notes, tags FROM accounts
        WHERE platform='outlook' AND status='active'
          AND notes LIKE '%%unitool_ref_code=%%'
          AND (tags LIKE '%%unitool_ref_master%%' OR tags LIKE '%%unitool_ref_activated%%')
        ORDER BY
          CASE WHEN tags LIKE '%%unitool_ref_activated%%' THEN 0 ELSE 1 END,
          updated_at ASC
    """)
    rows = cur.fetchall(); conn.close()

    best_id = best_email = best_rc = None
    best_used = MAX_REF_SLOTS  # 初始化为上限

    for acc_id, acc_email, notes, tags in rows:
        if not notes:
            continue
        m = re.search(r"unitool_ref_code=([A-Za-z0-9_-]+)", notes)
        if not m:
            continue
        rc = m.group(1)
        # 统计该账号下已注册的 referral 数（通过 master 账号 notes 里 ref_registered= 计数）
        used = len(re.findall(r"ref_registered=", notes))
        if used < MAX_REF_SLOTS and used < best_used:
            best_id    = acc_id
            best_email = acc_email
            best_rc    = rc
            best_used  = used

    if best_rc:
        log(f"[ref] 使用 ref_code={best_rc} from {best_email} used={best_used}/{MAX_REF_SLOTS}")
    else:
        log("[ref] DB 无可用 ref_code，将使用 fallback")
    return best_id, best_email, best_rc, best_used


def cleanup_stale_processing(max_age_min=30):
    """
    模块隔离保障：清理卡死超过 max_age_min 分钟的 unitool_processing 标签。
    防止 chain_v3 崩溃/超时后账号永久卡死，影响 OAuth 邮件中心等其他模块。
    """
    try:
        conn = db_connect(); cur = conn.cursor()
        cur.execute("""
            UPDATE accounts SET
              tags = TRIM(BOTH ',' FROM regexp_replace(tags, ',?unitool_processing', '', 'g')),
              updated_at = NOW()
            WHERE platform='outlook'
              AND tags LIKE '%%unitool_processing%%'
              AND tags NOT LIKE '%%unitool_registered%%'
              AND updated_at < NOW() - INTERVAL '%s minutes'
            RETURNING id, email
        """ % max_age_min)
        cleaned = cur.fetchall()
        if cleaned:
            log(f"[stale] 🧹 {len(cleaned)} 个卡死 processing 已自动解锁: {[r[1] for r in cleaned]}")
        conn.commit(); conn.close()
    except Exception as e:
        log(f"[stale] 解锁异常(忽略): {e}")

# ── 水位检查 + 非阻塞 outlook 补充 ───────────────────────────────────────────
def replenish_if_needed():
    """账号水位不足时，非阻塞触发 api-server 的 outlook 批量注册"""
    fresh = db_count_fresh()
    log(f"[watermark] fresh={fresh} watermark={WATERMARK}")
    if fresh >= WATERMARK:
        return

    # 冷却检查
    try:
        data = json.loads(open(LOCK_FILE).read())
        ts = float(data.get("ts", 0))
        if time.time() - ts < COOLDOWN_S:
            remaining = int(COOLDOWN_S - (time.time() - ts))
            log(f"[watermark] 冷却中 {remaining}s，跳过补充"); return
    except Exception:
        pass

    # 内存检查（outlook 注册每 worker 约 400-600 MB）
    try:
        for line in open("/proc/meminfo"):
            if "MemAvailable" in line:
                mb = int(line.split()[1]) // 1024
                if mb < 800:
                    log(f"[watermark] 内存不足 {mb}MB < 800MB，跳过补充"); return
                break
    except Exception:
        pass

    log(f"[watermark] 🚀 触发 outlook 补充注册 fresh={fresh} batch={REPLENISH_CNT}")
    try:
        payload = json.dumps({
            "count":     REPLENISH_CNT,
            "headless":  True,
            "proxyMode": "cf",
            "engine":    "patchright",
            "wait":      11,
            "retries":   2,
            "workers":   2,
        }).encode()
        req  = urllib.request.Request(
            f"{API_BASE}/tools/outlook/register",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if resp.get("success"):
            log(f"[watermark] ✅ 注册任务启动 jobId={resp.get('jobId','')}")
            try:
                open(LOCK_FILE, "w").write(json.dumps({"ts": time.time(), "batch": REPLENISH_CNT}))
            except Exception:
                pass
        else:
            log(f"[watermark] ❌ 启动失败: {resp}")
    except Exception as e:
        log(f"[watermark] ❌ 请求异常: {e}")

# ── 资源检查 ──────────────────────────────────────────────────────────────────
def check_resources():
    """确保内存 ≥ 600MB 且 Chrome 实例 ≤ 5 个"""
    try:
        for line in open("/proc/meminfo"):
            if "MemAvailable" in line:
                mb = int(line.split()[1]) // 1024
                log(f"[res] 内存={mb}MB")
                if mb < 600:
                    log("[res] SKIP mem<600MB"); return False
                break
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["bash", "-c",
             "ps aux | grep chrome-linux64/chrome | grep -v 'crashpad\\|grep' | wc -l"],
            capture_output=True, text=True)
        n = max(0, int(r.stdout.strip() or 0))
        log(f"[res] Chrome进程数={n}")
        if n > 5:
            log(f"[res] SKIP chrome_count={n}>5"); return False
    except Exception:
        pass
    return True

# ── ssid 持久化 ───────────────────────────────────────────────────────────────
def persist_ssid(email, ssid):
    """
    三路持久化：
      1. /data/unitool_ssids/EMAIL_label.txt  （proxy 首选目录）
      2. /tmp/unitool_ssid{N}.txt             （兼容旧格式）
      3. POST localhost:8089/add-ssid         （proxy 内存热推）
    """
    label = re.sub(r"[^a-z0-9]", "_", email.lower())

    # 1. /data/unitool_ssids/
    try:
        path = os.path.join(SSID_DIR, f"{label}.txt")
        open(path, "w").write(ssid)
        log(f"[ssid] 写入 {path}")
    except Exception as e:
        log(f"[ssid] /data write err: {e}")

    # 2. /tmp/unitool_ssid{N}.txt
    try:
        existing = sorted(glob.glob("/tmp/unitool_ssid*.txt"))
        idxs = []
        for fp in existing:
            m = re.search(r"unitool_ssid(\d+)\.txt$", fp)
            if m: idxs.append(int(m.group(1)))
        n     = (max(idxs) + 1) if idxs else 1
        fname = f"/tmp/unitool_ssid{n}.txt"
        open(fname, "w").write(ssid)
        log(f"[ssid] 写入 {fname}")
    except Exception as e:
        log(f"[ssid] /tmp write err: {e}")

    # 3. 热推 proxy（立即生效，无需等 5s 热加载间隔）
    try:
        data = json.dumps({"ssid": ssid, "label": email}).encode()
        req  = urllib.request.Request(
            f"http://localhost:{PROXY_PORT}/add-ssid",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
        log(f"[ssid] proxy热推 OK pool_size={resp.get('pool_size','?')}")
    except Exception as e:
        log(f"[ssid] proxy热推 warn: {e}")

# ── 子进程调用 ────────────────────────────────────────────────────────────────
def _run(cmd, timeout=900, label=""):
    """运行子进程，返回 (stdout, stderr, returncode)"""
    env = {**os.environ, "DISPLAY": ":99", "PYTHONUNBUFFERED": "1",
           "PLAYWRIGHT_BROWSERS_PATH": "/data/cache/ms-playwright"}
    log(f"[cmd] {label or ''} {' '.join(str(c) for c in cmd[:6])}...")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        log(f"[cmd] TIMEOUT {timeout}s: {label}")
        return "", "TIMEOUT", -1
    except Exception as e:
        log(f"[cmd] ERR {e}")
        return "", str(e), -1

def run_register(email, ref_code):
    """
    调用 unitool_register.py --email EMAIL --ref-code CODE
    流程（在 unitool_register.py 内部）：
      1. 访问 https://unitool.ai/ref/{ref_code}（写入推荐 cookie）
      2. 打开注册页，bypass CF Turnstile
      3. 填写 email + password，提交
      4. Graph API 轮询 Inbox+JunkEmail 找验证邮件
      5. 点击验证链接 / pydoll click_verify
      6. 提取 __Secure-unitool-ssid cookie
    返回: {"ok": bool, "ssid": str, "reason": str}
    """
    args = ["python3", REGISTER_PY,
            "--email", email,
            "--headless",    # 服务器无头（有 Xvfb）
    ]
    if ref_code:
        args += ["--ref-code", ref_code]

    stdout, stderr, rc = _run(args, timeout=600, label=f"register({email})")

    for line in stdout.splitlines():
        if line.startswith("[OK]"):
            parts = line[5:].split("|", 1)  # [OK] email|ssid
            ssid  = parts[1].strip() if len(parts) > 1 else ""
            log(f"[register] ✅ OK email={email} ssid_len={len(ssid)}")
            return {"ok": True, "ssid": ssid}
        if line.startswith("[FAIL]"):
            reason = line[7:].split("|")[-1].strip() if "|" in line else line[7:].strip()
            log(f"[register] ❌ FAIL reason={reason}")
            return {"ok": False, "ssid": "", "reason": reason}

    # 无 [OK]/[FAIL] 行
    log(f"[register] 无结果行 rc={rc} stderr={stderr[-200:]}")
    return {"ok": False, "ssid": "", "reason": f"no_output rc={rc}"}

def run_login(email, password):
    """
    unitool_login.py 兜底登录：
    当 unitool_register.py 完成注册但未能立即拿到 ssid 时调用
    （验证链接重定向到 /entry 而非直接设置 cookie 的情况）
    """
    if not password:
        log("[login] 无密码，跳过"); return ""
    args = ["python3", LOGIN_PY,
            "--email", email, "--password", password, "--no-headless"]
    stdout, stderr, rc = _run(args, timeout=180, label=f"login({email})")
    for line in stdout.splitlines():
        if line.startswith("[OK]"):
            parts = line.split("|")
            if len(parts) >= 3:
                ssid = parts[2].strip()
                log(f"[login] ✅ ssid_len={len(ssid)}")
                return ssid
        if line.startswith("[FAIL]"):
            log(f"[login] FAIL: {line}")
    log(f"[login] 未拿到 ssid rc={rc}")
    return ""

def run_reflink(email):
    """
    unitool_reflink.py --email EMAIL
    从 /api/auth/session 提取该账号的 ref_code
    输出: [OK] ref_code|ref_url|email|uid
    返回 ref_code 字符串，失败返回空串
    """
    args = ["python3", REFLINK_PY, "--email", email]
    stdout, stderr, rc = _run(args, timeout=30, label=f"reflink({email})")
    for line in stdout.splitlines():
        if line.startswith("[OK]"):
            parts = line[5:].split("|")
            rc_val = parts[0].strip() if parts else ""
            if rc_val:
                log(f"[reflink] ✅ ref_code={rc_val}")
                return rc_val
        if line.startswith("[FAIL]"):
            log(f"[reflink] FAIL: {line}")
    log("[reflink] 未拿到 ref_code")
    return ""

def db_get_ssid_from_notes(account_id):
    """从 DB notes 读 ssid（unitool_register.py 已写入，可能截断）"""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("SELECT notes FROM accounts WHERE id=%s", (account_id,))
    row = cur.fetchone(); conn.close()
    if not row or not row[0]: return ""
    m = re.search(r"unitool_ssid=([A-Za-z0-9_-]{20,})", row[0])
    return m.group(1) if m else ""

# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    global _account_id, _success_flag

    open(LOG, "w").write("")
    log("=" * 60)
    log("=== unitool_chain_v3 start ===")

    # ── Step 0a: 模块隔离 — 清理卡死 processing（>30min 自动解锁）─────────────
    try:
        cleanup_stale_processing(30)
    except Exception as e:
        log(f"[stale] 异常(忽略): {e}")

    # ── Step 0b: 水位检查（非阻塞） ────────────────────────────────────────────
    try:
        replenish_if_needed()
    except Exception as e:
        log(f"[watermark] 异常(忽略): {e}")

    # ── Step 1: 资源检查 ───────────────────────────────────────────────────────
    if not check_resources():
        log("[main] 资源不足 → sleep 60s")
        time.sleep(60); return

    # ── Step 2: 获取当前可用 ref_code ──────────────────────────────────────────
    ref_master_id, ref_master_email, ref_code, ref_used = db_get_current_ref_code()
    if not ref_code:
        # 兜底：使用已知的 ref_code（sarahrivera639 的 xjfjk）
        ref_code = "xjfjk"
        log(f"[ref] 兜底使用 ref_code={ref_code}")
    log(f"[ref] ref_code={ref_code} master={ref_master_email} used={ref_used}/{MAX_REF_SLOTS}")

    # ── Step 3: 取一个新鲜 outlook 账号 ────────────────────────────────────────
    row = db_get_fresh_account()
    if not row:
        log("[main] 无可用账号 → sleep 120s")
        time.sleep(120); return

    account_id, email, password, refresh_token = row
    _account_id = account_id
    log(f"\n{'─'*60}")
    log(f"[main] 账号: {email}  id={account_id}  ref_code={ref_code}")
    db_lock_account(account_id)   # 立即锁定，防 OOM 后重复选

    # ── Step 4: 注册 unitool（带 ref_code）──────────────────────────────────────
    log(f"[main] ▶ 调用 unitool_register.py --ref-code {ref_code}")
    reg_result = run_register(email, ref_code)

    if not reg_result["ok"]:
        reason = reg_result.get("reason", "unknown")
        log(f"[main] ❌ 注册失败: {reason}")
        db_mark_fail(account_id, reason)
        return   # atexit 会二次保险

    # ── Step 5: ssid 三级兜底 ───────────────────────────────────────────────────
    ssid = reg_result.get("ssid", "")
    if not ssid:
        log("[ssid] 注册返回无 ssid，尝试从 DB 读...")
        ssid = db_get_ssid_from_notes(account_id)
    if not ssid:
        log("[ssid] DB 也无 ssid，尝试 unitool_login.py 兜底登录...")
        if check_resources():
            ssid = run_login(email, password)
        else:
            log("[ssid] 资源不足，跳过登录兜底")

    if not ssid:
        log(f"[main] ❌ 三级兜底均失败，无法获取 ssid")
        db_mark_fail(account_id, "no_ssid_after_3_fallbacks")
        return

    log(f"[main] ✅ ssid 获取成功 len={len(ssid)}")

    # ── Step 6: 保存完整 ssid（覆盖截断版本）──────────────────────────────────
    db_save_ssid_full(account_id, email, ssid)  # DB 全长保存（修复 80/200 字截断）
    persist_ssid(email, ssid)                   # /data/ + /tmp/ + proxy 热推

    _success_flag = True  # 告知 atexit 不需要标 fail

    # ── Step 7: 激活该账号自己的 ref_code ──────────────────────────────────────
    log(f"[main] ▶ 激活 {email} 的 ref_code...")
    new_ref_code = run_reflink(email)
    if new_ref_code:
        db_save_ref_code(account_id, new_ref_code)
        log(f"[main] ✅ ref_code 激活: {new_ref_code} → 下一轮可用")
        print(f"[CHAIN_OK] {email}|{ssid}...|{ref_code}|{new_ref_code}", flush=True)
    else:
        log(f"[main] ⚠ 未能激活 ref_code（ssid 可能未就绪，下次重试）")
        print(f"[OK] {email}|{ssid}...|{ref_code}|no_ref_code", flush=True)

    # ── 追踪 referral 关系（master 账号 notes 里追加 ref_registered=）──────────
    if ref_master_id:
        try:
            conn = db_connect(); cur = conn.cursor()
            cur.execute("""
                UPDATE accounts SET
                  notes = COALESCE(notes,'') || %s,
                  updated_at = NOW()
                WHERE id = %s
            """, (f"\nref_registered={email}|id={account_id}", ref_master_id))
            conn.commit(); conn.close()
            log(f"[ref] master({ref_master_email}) +1 referral → {email}")
        except Exception as e:
            log(f"[ref] track referral err: {e}")

    log(f"\n{'='*60}")
    log(f"=== chain_v3 完成 email={email} ssid_len={len(ssid)} ref_new={new_ref_code} ===")


if __name__ == "__main__":
    main()
