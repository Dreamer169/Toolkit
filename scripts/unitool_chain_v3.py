#!/usr/bin/env python3
"""
unitool_chain_v3.py — 端到端全自动链路 v3.2
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
import atexit, asyncio as _asyncio, glob, json, os, re, signal, subprocess, sys, time
import urllib.parse, urllib.request
import psycopg2
import sys as _sys_rp
_sys_rp.path.insert(0, "/data/Toolkit/scripts") if "/data/Toolkit/scripts" not in _sys_rp.path else None
import resi_pool as _rpool

# ── 常量 ──────────────────────────────────────────────────────────────────────
LOG            = "/tmp/unitool_chain_v3.log"
DB_URL         = "postgresql://postgres:postgres@localhost/toolkit"
SCRIPTS        = "/data/Toolkit/scripts"
REGISTER_PY    = f"{SCRIPTS}/unitool_register.py"
REFLINK_PY     = f"{SCRIPTS}/unitool_reflink.py"
LOGIN_PY       = f"{SCRIPTS}/unitool_login.py"
HTTP_REGISTER_PY = f"{SCRIPTS}/unitool_http_register.py"  # v3.2 fast path

SSID_DIR       = "/data/unitool_ssids"   # proxy 优先读取目录
PROXY_PORT     = 8089                    # unitool_proxy.py 监听端口
API_BASE       = "http://localhost:8081/api"  # api-server 地址

MAX_REF_SLOTS   = 10    # unitool 每个 ref_code 最多邀请人数
RESI_PORTS = list(range(10851, 10860)) + list(range(10870, 10890))  # v3.2: 29 candidates

# CF Worker 直连代理（proxy.jimjio.indevs.in），提供 CF 边缘 IP 多样性
# 用于 ref-code API 查询/创建的 IP 分散（RESI 失败时自动降级）
CF_WORKER_URL = "https://proxy.jimjio.indevs.in/proxy"

def _cf_worker_api(target_url: str, method: str, ssid: str,
                   extra_headers: dict = None, timeout: int = 15) -> str:
    """
    通过 CF Worker 发起 unitool API 请求（CF 边缘 IP 出口）。
    返回: target API 响应体字符串，失败返回 ""。
    """
    import json as _jcf, urllib.request as _urcf
    hdrs = {
        "Cookie": f"__Secure-unitool-ssid={ssid}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    }
    if extra_headers:
        hdrs.update(extra_headers)
    payload = _jcf.dumps({
        "target_url": target_url,
        "method": method,
        "headers": hdrs,
    }).encode("utf-8")
    try:
        req = _urcf.Request(CF_WORKER_URL, data=payload, method="POST",
                            headers={"Content-Type": "application/json"})
        resp = _urcf.urlopen(req, timeout=timeout)
        outer = _jcf.loads(resp.read())
        body = outer.get("body", "")
        if isinstance(body, dict):
            return _jcf.dumps(body)
        return str(body)
    except Exception as _e:
        log(f"[CF] worker err: {_e}")
        return ""


# v3.2: RESI health delegated to resi_pool
# (easy_proxies-style: failure threshold + TTL blacklist, 29 candidates vs old 9)
def _get_healthy_resi_ports_chain() -> list:
    """Delegate to resi_pool: parallel probe 29 ports, cached 5 min."""
    return _rpool.refresh()
WATERMARK       = 5     # fresh 账号低于此值时触发 outlook 补充
REPLENISH_CNT   = 5     # 单次补充目标数量
COOLDOWN_S      = 300   # 水位补充冷却（15 分钟）
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
            new_tags = re.sub(r",?unitool_fail", "", new_tags).strip(",")  # 清除旧版遗留
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

# SIGTERM → sys.exit(0) so atexit fires cleanly when pm2 stops/restarts
def _sigterm_handler(signum, frame):
    log("[signal] SIGTERM received — exiting cleanly")
    sys.exit(0)
signal.signal(signal.SIGTERM, _sigterm_handler)
signal.signal(signal.SIGINT,  _sigterm_handler)  # PM2默认发 SIGINT

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
           AND (tags NOT LIKE '%%unitool_fail%%' OR updated_at < NOW() - INTERVAL '4 hours')
           AND tags NOT LIKE '%%unitool_already%%'
           AND (tags NOT LIKE '%%unitool_reg_retry%%' OR updated_at < NOW() - INTERVAL '4 hours')
           AND tags NOT LIKE '%%unitool_processing%%'
           AND tags NOT LIKE '%%unitool_already%%'
           AND tags NOT LIKE '%%unitool_rescue_dead%%'
           AND tags NOT LIKE '%%unitool_verify_pending%%'
           AND tags NOT LIKE '%%not_found%%'
           AND tags NOT LIKE '%%abuse_mode%%'
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
           AND (tags NOT LIKE '%%unitool_fail%%' OR updated_at < NOW() - INTERVAL '4 hours')
           AND tags NOT LIKE '%%unitool_already%%'
           AND (tags NOT LIKE '%%unitool_reg_retry%%' OR updated_at < NOW() - INTERVAL '4 hours')
           AND tags NOT LIKE '%%unitool_processing%%'
           AND tags NOT LIKE '%%unitool_already%%'
           AND tags NOT LIKE '%%unitool_rescue_dead%%'
           AND tags NOT LIKE '%%unitool_verify_pending%%'
           AND tags NOT LIKE '%%not_found%%'
           AND tags NOT LIKE '%%abuse_mode%%'
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
        # unitool_reg_retry: 4h 后自动重试（清除旧版遗留 unitool_fail）
        new_tags = re.sub(r',?unitool_fail', '', new_tags).strip(',')
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

REF_CODE_CACHE_FILE = "/tmp/unitool_ref_code_cache.json"
REF_CODE_CACHE_TTL  = 1800  # 30 分钟
ROTATE_FILE         = "/tmp/unitool_ref_rotate.json"  # 轮转索引持久化

def _load_ref_cache() -> dict:
    try:
        return json.loads(open(REF_CODE_CACHE_FILE).read())
    except Exception:
        return {}

def _save_ref_cache(cache: dict) -> None:
    try:
        open(REF_CODE_CACHE_FILE, "w").write(json.dumps(cache))
    except Exception:
        pass

def _api_check_ref_code(ssid: str, account_id: int = 0) -> tuple:
    """
    FIX G helper: 调 GET /api/user/ref-code 获取真实 conversions 计数。
    带 30min 文件缓存（key=account_id），减少每轮 18×API 开销。
    返回 (ref_code, conversions) 或 ("", -1) 表示失败。
    """
    key = str(account_id) if account_id else ssid[:16]
    cache = _load_ref_cache()
    entry = cache.get(key, {})
    if entry and (time.time() - entry.get("ts", 0)) < REF_CODE_CACHE_TTL:
        return (entry.get("code", ""), entry.get("conversions", 0))
    try:
        _hp = _get_healthy_resi_ports_chain()
        _rc_port = _hp[hash(str(account_id)) % len(_hp)]
        # v5.13 fix: Popen+communicate to avoid KBI crashing the main loop
        _rc_cmd = [
            "curl", "-s",
            "--socks5-hostname", f"127.0.0.1:{_rc_port}",
            "-b", f"__Secure-unitool-ssid={ssid}",
            "-H", "Accept: application/json", "--max-time", "10",
            "https://unitool.ai/api/user/ref-code",
        ]
        _rc_proc = subprocess.Popen(_rc_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            _rc_out, _ = _rc_proc.communicate(timeout=15)
            raw = _rc_out.decode("utf-8", errors="ignore").strip()
        except KeyboardInterrupt:
            try: _rc_proc.kill(); _rc_proc.communicate()
            except Exception: pass
            raise
        except subprocess.TimeoutExpired:
            try: _rc_proc.kill(); _rc_proc.communicate()
            except Exception: pass
            return ("", -1)
        raw = raw
        if raw == "null":
            # v5.15: server returned explicit null → SSID expired → mark invalid in DB
            try:
                _inv_c = db_connect(); _inv_cur = _inv_c.cursor()
                _inv_cur.execute(
                    "UPDATE unitool_ssids SET is_valid=FALSE WHERE ssid=%s",
                    (ssid,))
                _inv_c.commit(); _inv_c.close()
                log(f"[SSID] is_valid=FALSE → account_id={account_id} (null from API)")
            except Exception as _inv_e:
                log(f"[SSID] invalidate error: {_inv_e}")
            cache[key] = {"code": "", "conversions": 0, "ts": time.time()}
            _save_ref_cache(cache)
            return ("", 0)
        if not raw:
            # empty = network failure; do not invalidate
            cache[key] = {"code": "", "conversions": 0, "ts": time.time()}
            _save_ref_cache(cache)
            return ("", 0)
        data = json.loads(raw)
        code = data.get("code", "")
        conv = int(data.get("conversions", 0))
        cache[key] = {"code": code, "conversions": conv,
                      "earnings": data.get("earnings", 0),
                      "clicks": data.get("clicks", 0), "ts": time.time()}
        _save_ref_cache(cache)
        return (code, conv)
    except Exception:
        pass
    # RESI 全失败 → CF Worker 兜底（CF 边缘 IP，绕开 VPS IP 限速/封锁）
    try:
        _cf_raw = _cf_worker_api("https://unitool.ai/api/user/ref-code", "GET", ssid)
        if _cf_raw and _cf_raw not in ("", "null"):
            _cfd = json.loads(_cf_raw)
            _cfc = _cfd.get("code", "")
            _cfv = int(_cfd.get("conversions", 0))
            cache[key] = {"code": _cfc, "conversions": _cfv,
                          "earnings": _cfd.get("earnings", 0),
                          "clicks": _cfd.get("clicks", 0), "ts": time.time()}
            _save_ref_cache(cache)
            log(f"[CF] ref-code via Worker: code={_cfc} conv={_cfv}")
            return (_cfc, _cfv)
    except Exception:
        pass
    return ("", -1)


def create_ref_code_via_proxy(ssid: str, email: str, port_hint: int = 0) -> str:
    """
    FIX F: 通过 SOCKS5 代理调 POST /api/ref-codes，为账号生成专属邀请码。
    unitool 限制同一 IP 只能创建一个 ref_code，必须通过住宅代理绕开。
    成功返回 ref_code 字符串，失败返回 ""。
    """
    # 从 port_hint 偏移出发，分散 IP；v5.15: 跳过已知死端口
    _hp2 = _get_healthy_resi_ports_chain()
    _base = port_hint % len(_hp2)
    _ports = _hp2[_base:] + _hp2[:_base]
    for port in _ports:
        try:
            # v5.13 fix: Popen+communicate to avoid KBI crash
            _crf_cmd = [
                "curl", "-s", "--max-time", "12",
                "--socks5-hostname", f"127.0.0.1:{port}",
                "-b", f"__Secure-unitool-ssid={ssid}",
                "-X", "POST",
                "-H", "Content-Type: application/json",
                "-H", "Accept: application/json",
                "https://unitool.ai/api/ref-codes",
            ]
            _crf_proc = subprocess.Popen(_crf_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                _crf_out, _ = _crf_proc.communicate(timeout=18)
                _crf_stdout = _crf_out.decode("utf-8", errors="ignore")
            except KeyboardInterrupt:
                try: _crf_proc.kill(); _crf_proc.communicate()
                except Exception: pass
                raise
            except subprocess.TimeoutExpired:
                try: _crf_proc.kill(); _crf_proc.communicate()
                except Exception: pass
                _rpool.report_failure(port)
                continue
            if _crf_proc.returncode != 0 or not _crf_stdout.strip():
                continue
            data = json.loads(_crf_stdout)
            if "code" in data:
                _rpool.report_success(port)
                log(f"[ref_create] ✅ port={port} → ref_code={data['code']} email={email}")
                return data["code"]
            err = data.get("error", "")
            log(f"[ref_create] port={port} err={err} ({email})")
            if err == "ip-already-existed":
                continue
        except Exception as _e:
            log(f"[ref_create] port={port} exc={_e}")
    log(f"[ref_create] ❌ 所有代理端口均失败 ({email})")
    # 全 RESI 端口失败 → CF Worker 兜底创建 ref_code（CF 边缘 IP）
    try:
        _cfc_body = _cf_worker_api(
            "https://unitool.ai/api/ref-codes", "POST", ssid,
            extra_headers={"Content-Type": "application/json"}
        )
        if _cfc_body and _cfc_body not in ("", "null"):
            import json as _jcfc; _cfc_d = _jcfc.loads(_cfc_body)
            _cfc_code = _cfc_d.get("code", "")
            if _cfc_code:
                log(f"[CF] create ref_code via Worker: {_cfc_code}")
                return _cfc_code
    except Exception as _cfe:
        log(f"[CF] create ref fallback err: {_cfe}")
    return ""


def db_get_all_ref_codes() -> list:
    """
    获取 DB 中所有有余量的 ref_code（conversions < MAX_REF_SLOTS）。
    对每个账号调 API 验证真实 conversions（带 30min 缓存）。
    返回: [{"id": int, "email": str, "ref_code": str, "used": int}, ...]
    按 used 升序排列（余量多的在前）。
    """
    conn = db_connect(); cur = conn.cursor()
    cur.execute("""
        SELECT id, email, notes, tags FROM accounts
        WHERE platform='outlook'
          AND notes LIKE '%%unitool_ref_code=%%'
          AND (tags LIKE '%%unitool_ref_master%%' OR tags LIKE '%%unitool_ref_activated%%')
        ORDER BY updated_at ASC
    """)
    rows = cur.fetchall(); conn.close()

    available = []
    for acc_id, acc_email, notes, tags in rows:
        if not notes:
            continue
        m = re.search(r"unitool_ref_code=(?!ref_code=)([A-Za-z0-9_-]+)", notes)
        if not m:
            continue
        rc = m.group(1)
        local_used = len(re.findall(r"ref_registered=", notes))

        ssid_m = re.search(r"unitool_ssid=([0-9a-f]{40,})", notes)
        if ssid_m:
            api_rc, api_conv = _api_check_ref_code(ssid_m.group(1), acc_id)
            if api_conv < 0:
                used = local_used  # API 失败，保守降级
                log(f"[ref] id={acc_id} {acc_email} API failed, fallback local={local_used}")
            elif not api_rc:
                # API=null: ssid 可能过期或账号无自己的码
                # unitool_ref_activated = 脚本显式 POST 创建，可信（ssid 过期不等于码不存在）
                # unitool_ref_master (without ref_activated) = 旧脏数据路径，跳过
                if "unitool_ref_activated" in (tags or "") and "unitool_ref_master" not in (tags or ""):
                    used = local_used  # ssid 过期，信任 DB 码 + 本地计数
                    log(f"[ref] id={acc_id} {acc_email} ssid_expired/null but ref_activated, local={local_used}")
                else:
                    log(f"[ref] id={acc_id} {acc_email} API=null + ref_master, skip dirty inviter code")
                    continue
            else:
                if api_rc != rc:
                    log(f"[ref] id={acc_id} API rc={api_rc} != DB rc={rc}, use API")
                    rc = api_rc
                used = api_conv
                log(f"[ref] id={acc_id} {acc_email} rc={rc} conversions={api_conv}/{MAX_REF_SLOTS}")
        else:
            used = local_used

        if used < MAX_REF_SLOTS:
            available.append({"id": acc_id, "email": acc_email,
                               "ref_code": rc, "used": used})

    available.sort(key=lambda x: x["used"])
    log(f"[ref] 可用 ref_code 池: {len(available)} 个 → " +
        ", ".join(f"{r['ref_code']}({r['used']}/{MAX_REF_SLOTS})" for r in available))
    return available


def pick_rotating_ref_code(pool: list) -> dict | None:
    """
    从 pool 中轮转选取 ref_code，保证多码分散：
      - 读 ROTATE_FILE 中的 last_code 和 last_ts
      - 若上次用过的码仍有余量，选 pool 中下一个（round-robin）
      - 若只有1个码，直接返回它
    返回 pool 中的一个 dict，或 None（池为空）
    """
    if not pool:
        return None
    if len(pool) == 1:
        return pool[0]

    # 读上次状态
    last_code = ""
    try:
        state = json.loads(open(ROTATE_FILE).read())
        last_code = state.get("last_code", "")
    except Exception:
        pass

    codes = [r["ref_code"] for r in pool]
    if last_code in codes:
        idx = (codes.index(last_code) + 1) % len(pool)
    else:
        idx = 0  # 上次的码已不在池中（用完/失效），从头开始

    chosen = pool[idx]
    try:
        open(ROTATE_FILE, "w").write(json.dumps({
            "last_code": chosen["ref_code"],
            "last_email": chosen["email"],
            "ts": time.time(),
            "pool_size": len(pool),
        }))
    except Exception:
        pass
    log(f"[ref] 轮转选取 #{idx+1}/{len(pool)}: ref_code={chosen['ref_code']} "
        f"from {chosen['email']} used={chosen['used']}/{MAX_REF_SLOTS}")
    return chosen


def db_get_current_ref_code():
    """
    兼容旧调用接口，内部改为轮转多码。
    返回 (account_id, email, ref_code, used_count) 或 (None, None, "", 0)
    """
    pool = db_get_all_ref_codes()
    chosen = pick_rotating_ref_code(pool)
    if not chosen:
        log("[ref] DB 无可用 ref_code（所有码已满或未生成）")
        return None, None, "", 0
    return chosen["id"], chosen["email"], chosen["ref_code"], chosen["used"]


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
                if mb < 1500:
                    log(f"[watermark] 内存不足 {mb}MB < 1500MB，跳过补充"); return
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
            "workers":   1,
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
        # v5.13: Popen to avoid KBI propagation
        _ps_proc = subprocess.Popen(
            ["bash", "-c",
             "ps aux | grep chrome-linux64/chrome | grep -v 'crashpad\\|grep' | wc -l"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            _ps_out, _ = _ps_proc.communicate(timeout=10)
            _ps_stdout = _ps_out.decode("utf-8", errors="ignore")
        except KeyboardInterrupt:
            try: _ps_proc.kill(); _ps_proc.communicate()
            except Exception: pass
            raise
        except Exception:
            _ps_stdout = "0"
        n = max(0, int(_ps_stdout.strip() or 0))
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
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.communicate()
            log(f"[cmd] TIMEOUT {timeout}s: {label}")
            return "", "TIMEOUT", -1
        return stdout, stderr, proc.returncode
    except KeyboardInterrupt:
        if proc:
            try: proc.kill(); proc.communicate()
            except Exception: pass
        log(f"[cmd] KeyboardInterrupt — killing child and re-raising")
        raise
    except Exception as e:
        log(f"[cmd] ERR {e}")
        return "", str(e), -1



# === NA 哈希日常监控 (每天一次) ======================================
_NA_CHECK_TS_FILE = "/tmp/unitool_na_last_check.ts"
_KNOWN_SIGNUP_NA  = "602b5c42d2c7dccaa6e3a06bed4a8a99ba7d0bc4"


def _check_na_daily():
    """
    每天探测一次 SIGNUP_NA 是否变更。变更时 log 告警提示更新 _SIGNUP_NA_DEFAULT。
    内置到 main() Step 0c，无阻塞。
    """
    import urllib.request as _ur
    try:
        last = float(open(_NA_CHECK_TS_FILE).read().strip())
    except Exception:
        last = 0
    if time.time() - last < 86400:
        return
    try:
        req = _ur.Request(
            "https://unitool.ai/en/entry",
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        )
        html = _ur.urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
        candidates = list(set(re.findall(r"[a-f0-9]{42}", html)))
        if _KNOWN_SIGNUP_NA in candidates:
            log(f"[na_probe] ✓ SIGNUP_NA 未变 ({_KNOWN_SIGNUP_NA[:12]}...)")
        elif candidates:
            log(f"[na_probe] ⚠️ SIGNUP_NA 已变! 旧={_KNOWN_SIGNUP_NA[:12]}... 候选={candidates[:3]}")
            log(f"[na_probe] 请更新 unitool_http_register.py 中的 _SIGNUP_NA_DEFAULT")
        else:
            log(f"[na_probe] ⚠️ 页面中未找到 42 位哈希（CF 拦截？）")
        open(_NA_CHECK_TS_FILE, "w").write(str(time.time()))
    except Exception as e:
        log(f"[na_probe] 探测失败: {e}")


# === http_register 快速路径 + 全浏览器兆底 ======================================
_http_reg_cache = {}  # {"fn": callable_or_False}


def _load_http_reg():
    if "fn" in _http_reg_cache:
        return _http_reg_cache["fn"]
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("unitool_http_register", HTTP_REGISTER_PY)
        _mod  = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _http_reg_cache["fn"] = _mod.http_register
        log("[http_reg] ✓ unitool_http_register 加载成功")
    except Exception as e:
        _http_reg_cache["fn"] = False
        log(f"[http_reg] ⚠ 加载失败(将直接用全浏览器): {e}")
    return _http_reg_cache["fn"]


def _run_http_reg_with_pw(email: str, ref_code: str) -> dict:
    """从 DB 取密码后调 http_register（chain_v3 场景）"""
    fn = _load_http_reg()
    if not fn:
        return {"ok": False, "error": "http_reg_unavailable"}
    try:
        conn = db_connect(); cur = conn.cursor()
        cur.execute("SELECT password FROM accounts WHERE email=%s LIMIT 1", (email,))
        row = cur.fetchone(); conn.close()
        pw = (row[0] or "") if row else ""
    except Exception as e:
        return {"ok": False, "error": f"db_pw_err:{e}"}
    if not pw:
        return {"ok": False, "error": "no_password_in_db"}
    try:
        return fn(email, pw, ref_code)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def run_register_fast(email, ref_code):
    """
    Step 4 注册调度器 v3.2:
      ① 优先 http_register() - pydoll bypass + JS fetch (~20-35s, 快 40%)
         ssid httpOnly 无法捕获，由 Step 5 run_login() 兆底。
      ② 永久失败 (already_registered) → 直接返回失败。
      ③ 暂态失败 (bypass/CF/超时) → 自动降级全浏览器 run_register()。
    返回: {"ok": bool, "ssid": str, "reason": str}
    """
    fn = _load_http_reg()
    if fn:
        log(f"[main] ▶ [快速路径] http_register v3.2 --ref-code {ref_code}")
        try:
            result = _run_http_reg_with_pw(email, ref_code)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"[http_reg] 异常: {e} → 降级全浏览器")
            result = {"ok": False, "error": str(e)}

        if result.get("ok"):
            log(f"[http_reg] ✅ 注册成功 method={result.get('method','?')}")
            return {"ok": True, "ssid": "", "reason": ""}

        err = result.get("error_type") or result.get("error") or result.get("reason", "unknown")
        err_s = str(err).lower()
        # 永久失败：不降级
        if any(p in err_s for p in ("already_reg", "already_registered", "email_already",
                                     "already_use", "user with like email")):
            log(f"[http_reg] ❌ 永久失败 (already): {err}")
            return {"ok": False, "ssid": "", "reason": err_s}
        # 暂态失败：降级全浏览器
        log(f"[http_reg] ⚠ 暂态失败: {err} → 降级全浏览器 unitool_register.py")
    else:
        log(f"[main] ▶ http_register 不可用 → 直接全浏览器")

    log(f"[main] ▶ [兆底] unitool_register.py --ref-code {ref_code}")
    return run_register(email, ref_code)


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
            for _l in stdout.splitlines()[-60:]: log(f"[reg_out] {_l}")
            log(f"[register] ❌ FAIL reason={reason}")
            return {"ok": False, "ssid": "", "reason": reason}

    # 无 [OK]/[FAIL] 行 — dump full stdout for debug
    for _l in stdout.splitlines()[-50:]: log(f"[reg_out] {_l}")
    log(f"[register] 无结果行 rc={rc} stderr={stderr[-300:]}")
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

def run_reflink(email, _retries=3, _wait=30):
    """
    unitool_reflink.py --email EMAIL
    从 /api/auth/session 提取该账号的 ref_code
    输出: [OK] ref_code|ref_url|email|uid
    Bug B fix: 加管道校验防止解析 log 行; no_ref_code 时延迟重试
    """
    import time as _t
    args = ["python3", REFLINK_PY, "--email", email]
    for attempt in range(1, _retries + 1):
        stdout, stderr, rc = _run(args, timeout=30, label=f"reflink({email}) #{attempt}")
        no_ref = False
        for line in stdout.splitlines():
            if line.startswith("[OK]"):
                parts = line[5:].split("|")
                if len(parts) < 2:
                    continue  # 跳过非管道 log 行（Bug A guard）
                rc_val = parts[0].strip()
                if rc_val:
                    log(f"[reflink] ✅ ref_code={rc_val} (attempt {attempt})")
                    return rc_val
            if line.startswith("[FAIL]"):
                log(f"[reflink] FAIL: {line}")
                if "no_ref_code" in line:
                    no_ref = True
        if no_ref and attempt < _retries:
            log(f"[reflink] ref_code 暂空，{_wait}s 后重试 ({attempt}/{_retries})...")
            _t.sleep(_wait)
        else:
            break
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

    # ── Step 0c: NA 哈希监控（每天一次，无阻塞）───────────────────────
    try:
        _check_na_daily()
    except Exception as e:
        log(f"[na_probe] 异常(忽略): {e}")

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
        # FIX G: xjfjk 已 conversions=10/10 耗尽，不再硬编码兜底。
        # 无可用 ref_code 说明所有已知码均已用满，需要先为一个注册账号 POST /api/ref-codes 生成新码。
        log("[ref] ⚠ 无可用 ref_code（所有已知码已满或未生成），跳过本轮注册")
        time.sleep(60); return
    log(f"[ref] ref_code={ref_code} master={ref_master_email} used={ref_used}/{MAX_REF_SLOTS}")

    # ── Step 3: 取一个新鲜 outlook 账号 ────────────────────────────────────────
    row = db_get_fresh_account()
    if not row:
        log("[main] 无可用账号 → sleep 300s")
        time.sleep(300); return

    account_id, email, password, refresh_token = row
    _account_id = account_id
    log(f"\n{'─'*60}")
    log(f"[main] 账号: {email}  id={account_id}  ref_code={ref_code}")
    db_lock_account(account_id)   # 立即锁定，防 OOM 后重复选

    # ── Step 4: 注册 unitool（带 ref_code）──────────────────────────────────────
    reg_result = run_register_fast(email, ref_code)

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

    # ── Step 7: 为该账号生成专属 ref_code，并激活到 DB ─────────────────────────
    # FIX F: 先通过代理 POST /api/ref-codes 为新账号创建专属码，再 run_reflink 读取保存
    log(f"[main] ▶ Step7a: 通过代理为 {email} 创建专属 ref_code...")
    # 用账号 id 做偏移，确保不同账号使用不同 RESI IP
    _port_hint = account_id  # passed as hint to resi_pool.pick() inside create_ref_code_via_proxy
    created_code = create_ref_code_via_proxy(ssid, email, port_hint=_port_hint)
    if created_code:
        log(f"[main] ✅ 代理创建成功: ref_code={created_code}")
        time.sleep(3)  # 等服务器写入
    else:
        log(f"[main] ⚠ 代理创建失败，尝试直接 run_reflink（可能已有码或延迟）")

    log(f"[main] ▶ Step7b: run_reflink 读取并保存 {email} 的 ref_code...")
    # 代理创建失败时不重试（无意义），代理创建成功时最多重试3次等服务器写入
    reflink_retries = 3 if created_code else 1
    new_ref_code = run_reflink(email, _retries=reflink_retries, _wait=10)
    if new_ref_code:
        db_save_ref_code(account_id, new_ref_code)
        log(f"[main] ✅ ref_code 激活: {new_ref_code} → 下一轮可用")
        print(f"[CHAIN_OK] {email}|{ssid}...|{ref_code}|{new_ref_code}", flush=True)
    else:
        log(f"[main] ⚠ 未能获取 ref_code（代理创建={created_code or 'FAIL'}，reflink 也失败）")
        print(f"[OK] {email}|{ssid}...|{ref_code}|no_ref_code", flush=True)

    # ── Step 7b: 在被注册账号 notes 写 via_ref=（监控统计用）────────────────────
    if ref_code:
        try:
            _cn = db_connect(); _cu = _cn.cursor()
            _cu.execute("SELECT notes FROM accounts WHERE id=%s", (account_id,))
            _rr = _cu.fetchone()
            _nn = (_rr[0] or "") if _rr else ""
            if ("via_ref=" + ref_code) not in _nn:
                _cu.execute(
                    "UPDATE accounts SET notes=COALESCE(notes,'')||%s, updated_at=NOW() WHERE id=%s",
                    ("\nvia_ref=" + ref_code, account_id)
                )
                _cn.commit()
            _cn.close()
            log(f"[ref] via_ref={ref_code} -> id={account_id}")
        except Exception as _ex:
            log(f"[ref] via_ref err: {_ex}")

    # ── 追踪 referral 关系（ref 账号 notes 里追加 ref_registered=，用于 used 计数）──────────
    if ref_master_id:
        try:
            conn = db_connect(); cur = conn.cursor()
            # 防重复：同一账号不重复写
            cur.execute("SELECT notes FROM accounts WHERE id=%s", (ref_master_id,))
            _rn = cur.fetchone()
            _existing = (_rn[0] or "") if _rn else ""
            if f"ref_registered={email}" not in _existing:
                cur.execute("""
                    UPDATE accounts SET
                      notes = COALESCE(notes,'') || %s,
                      updated_at = NOW()
                    WHERE id = %s
                """, (f"\nref_registered={email}|id={account_id}", ref_master_id))
                conn.commit()
            conn.close()
            log(f"[ref] master({ref_master_email}) +1 referral → {email}")
        except Exception as e:
            log(f"[ref] track referral err: {e}")

    log(f"\n{'='*60}")
    log(f"=== chain_v3 完成 email={email} ssid_len={len(ssid)} ref_new={new_ref_code} ===")


if __name__ == "__main__":
    main()
