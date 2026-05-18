#!/usr/bin/env python3
"""
unitool_chain_v3.py — 端到端全自动链路 v3.3
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
import atexit, asyncio as _asyncio, glob, json, os, re, signal, subprocess, sys, time, threading
import urllib.parse, urllib.request
import psycopg2
import sys as _sys_rp
_sys_rp.path.insert(0, "/data/Toolkit/scripts") if "/data/Toolkit/scripts" not in _sys_rp.path else None
import resi_pool as _rpool

# ── 常量 ──────────────────────────────────────────────────────────────────────
WORKER_ID    = int(os.environ.get("WORKER_ID",    "0"))
CHROME_LIMIT = int(os.environ.get("CHROME_LIMIT", "4"))
STARTUP_DELAY= int(os.environ.get("STARTUP_DELAY","0"))
LOG            = f"/tmp/unitool_chain_v3_w{WORKER_ID}.log"
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
RESI_PORTS = list(range(10851, 10860))  # v3.2: 9 live candidates (10870-10889 dead, removed Fix-5a)

# -- cfmail config -------------------------------------------------------------
CFMAIL_INSTANCES = [
    {
        "host":       "mail-api.jonjim.eu.cc",
        "domain":     "jonjim.eu.cc",
        "site_auth":  "8GKNFyLCo0pL7drOqKZQ6jGB",
        "admin_auth": "360cb32181e4ef281afb3b63",
        # D1 凭证由 _cfmail_load_d1_creds() 在运行时从 /root/credentials.json 注入
        "d1_token":   None,
        "d1_acc":     None,
        "d1_db":      None,
    },
    {
        "host":       "mail-api.hackerjim.eu.cc",
        "domain":     "hackerjim.eu.cc",
        "site_auth":  "ak4yJVQ8szp8H5jS3Mx6Y1sm",
        "admin_auth": "ufmTbatyzZ0jkKrDvYhIc281",
        # D1 凭证由 _cfmail_load_d1_creds() 在运行时从 /root/credentials.json 注入
        "d1_token":   None,
        "d1_acc":     None,
        "d1_db":      None,
    },
]
# 运行时注入 D1 凭证（从 /root/credentials.json，不硬编码在源码里）
def _cfmail_load_d1_creds():
    import json as _jcred
    _CRED_FILE = "/root/credentials.json"
    _D1_MAP = {
        "jonjim.eu.cc":    ("cf_services", "temp_email"),
        "hackerjim.eu.cc": ("cf_services", "hackerjim_temp_email"),
    }
    try:
        creds = _jcred.loads(open(_CRED_FILE).read())
        for inst in CFMAIL_INSTANCES:
            dom = inst["domain"]
            if dom in _D1_MAP:
                sec1, sec2 = _D1_MAP[dom]
                cf_svc = creds[sec1][sec2]
                # jonjim uses main token; hackerjim uses cf_accounts.CF-NEW-4.main_token
                if dom == "jonjim.eu.cc":
                    tok = creds["cloudflare_accounts"][2]["api_token"]  # CF-NEW-3
                    acc = creds["cloudflare_accounts"][2]["account_id"]
                else:
                    tok = creds["cf_accounts"]["CF-NEW-4"]["main_token"]
                    acc = creds["cf_accounts"]["CF-NEW-4"]["account_id"]
                inst["d1_token"] = tok
                inst["d1_acc"]   = acc
                inst["d1_db"]    = cf_svc["d1_id"]
        log("[cfmail] D1 creds loaded from credentials.json OK")
    except Exception as _e:
        log("[cfmail] WARNING: D1 creds load failed: " + str(_e))
_cfmail_load_d1_creds()
# cfmail 轮换状态文件
_CFMAIL_ROTATE_FILE = "/tmp/cfmail_rotate_state.json"
_cfmail_rotate_lock = __import__("threading").Lock()

def _cfmail_pick_inst():
    """
    轮换策略：use_count 最少优先（round-robin）
    fail_count>=3 且 disabled_until>now → 跳过（60s 冷却）
    全部被禁 → 重置 fail_count 选 use_count 最少
    """
    import json as _jrot, time as _trot
    now = _trot.time()
    with _cfmail_rotate_lock:
        try:
            state = _jrot.loads(open(_CFMAIL_ROTATE_FILE).read())
        except Exception:
            state = {}
        candidates = []
        for _inst in CFMAIL_INSTANCES:
            dom = _inst["domain"]
            s = state.get(dom, {"use_count": 0, "fail_count": 0, "disabled_until": 0})
            if s.get("fail_count", 0) >= 3 and now < s.get("disabled_until", 0):
                continue
            candidates.append((_inst, s))
        if not candidates:
            log("[cfmail_pick] all instances cooling, resetting")
            for _inst in CFMAIL_INSTANCES:
                dom = _inst["domain"]
                state.setdefault(dom, {"use_count": 0, "fail_count": 0, "disabled_until": 0})
                state[dom]["fail_count"] = 0
                state[dom]["disabled_until"] = 0
            candidates = [(i, state.get(i["domain"], {"use_count": 0, "fail_count": 0, "disabled_until": 0})) for i in CFMAIL_INSTANCES]
        chosen_inst, chosen_s = min(candidates, key=lambda x: x[1].get("use_count", 0))
        dom = chosen_inst["domain"]
        state.setdefault(dom, {"use_count": 0, "fail_count": 0, "disabled_until": 0})
        state[dom]["use_count"] = state[dom].get("use_count", 0) + 1
        try:
            open(_CFMAIL_ROTATE_FILE, "w").write(_jrot.dumps(state))
        except Exception:
            pass
        log("[cfmail_pick] chose " + dom + " use=" + str(state[dom]["use_count"]) + " fail=" + str(state[dom]["fail_count"]))
        return chosen_inst

def _cfmail_mark_fail(inst):
    """标记失败，fail_count>=3 禁用 60s"""
    import json as _jrot, time as _trot
    dom = inst["domain"]
    with _cfmail_rotate_lock:
        try:
            state = _jrot.loads(open(_CFMAIL_ROTATE_FILE).read())
        except Exception:
            state = {}
        s = state.setdefault(dom, {"use_count": 0, "fail_count": 0, "disabled_until": 0})
        s["fail_count"] = s.get("fail_count", 0) + 1
        if s["fail_count"] >= 3:
            s["disabled_until"] = _trot.time() + 60
            log("[cfmail_pick] " + dom + " fail=" + str(s["fail_count"]) + " -> disabled 60s")
        try:
            open(_CFMAIL_ROTATE_FILE, "w").write(_jrot.dumps(state))
        except Exception:
            pass

def _cfmail_mark_ok(inst):
    """成功 → 重置 fail_count"""
    import json as _jrot
    dom = inst["domain"]
    with _cfmail_rotate_lock:
        try:
            state = _jrot.loads(open(_CFMAIL_ROTATE_FILE).read())
        except Exception:
            state = {}
        s = state.setdefault(dom, {"use_count": 0, "fail_count": 0, "disabled_until": 0})
        s["fail_count"] = 0
        s["disabled_until"] = 0
        try:
            open(_CFMAIL_ROTATE_FILE, "w").write(_jrot.dumps(state))
        except Exception:
            pass
# Real-name word lists for generating firstname.lastname<digits> addresses
_CFMAIL_FIRST = [
    "james","john","robert","michael","william","david","richard","joseph",
    "thomas","charles","christopher","daniel","matthew","anthony","mark",
    "donald","steven","paul","andrew","joshua","kevin","brian","george",
    "edward","ronald","timothy","jason","jeffrey","ryan","jacob",
    "gary","nicholas","eric","jonathan","stephen","larry","justin",
    "scott","brandon","benjamin","samuel","raymond","gregory","frank",
    "mary","patricia","jennifer","linda","barbara","susan","jessica",
    "sarah","karen","lisa","nancy","betty","margaret","sandra",
    "ashley","emily","kimberly","donna","michelle","carol","amanda",
    "melissa","deborah","stephanie","rebecca","sharon","laura",
]
_CFMAIL_LAST = [
    "smith","johnson","williams","brown","jones","garcia","miller",
    "davis","rodriguez","martinez","hernandez","lopez","gonzalez",
    "wilson","anderson","thomas","taylor","moore","jackson","martin",
    "lee","perez","thompson","white","harris","sanchez","clark",
    "ramirez","lewis","robinson","walker","young","allen","king",
    "wright","scott","torres","nguyen","hill","flores","green",
    "adams","nelson","baker","hall","rivera","campbell","mitchell",
    "carter","roberts","turner","phillips","evans","diaz","parker",
]

# CF Worker 直连代理（proxy.jimjio.indevs.in），提供 CF 边缘 IP 多样性
# 用于 ref-code API 查询/创建的 IP 分散（RESI 失败时自动降级）
# v3.4: 4-Worker round-robin pool（CF 边缘 IP 多样性 + 自动故障转移）
_CF_WORKERS = [
    "https://proxy.jimjio.indevs.in/proxy",
    "https://proxy.jimjon.eu.cc/proxy",
    "https://proxy.jonjim.indevs.in/proxy",
    "https://proxy.hackerjim.indevs.in/proxy",
]
_cf_rr_index    = 0
_cf_circuit_until = [0] * len(_CF_WORKERS)   # per-worker circuit breaker (60s)

def _pick_cf_worker():
    """Round-robin picker，跳过 circuit open 的 Worker。"""
    global _cf_rr_index
    import time
    now = time.time()
    for i in range(len(_CF_WORKERS)):
        idx = (_cf_rr_index + i) % len(_CF_WORKERS)
        if now >= _cf_circuit_until[idx]:
            _cf_rr_index = (idx + 1) % len(_CF_WORKERS)
            return idx
    # 全部 circuit open → 重置最旧
    oldest = min(range(len(_CF_WORKERS)), key=lambda i: _cf_circuit_until[i])
    _cf_circuit_until[oldest] = 0
    _cf_rr_index = (oldest + 1) % len(_CF_WORKERS)
    return oldest

def _cf_worker_api(target_url: str, method: str, ssid: str,
                   extra_headers: dict = None, timeout: int = 15) -> str:
    """
    通过 CF Worker 池发起 unitool API 请求（round-robin，自动故障转移）。
    返回: target API 响应体字符串，失败返回 ""。
    """
    import json as _jcf, urllib.request as _urcf, time as _time
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
    last_err = None
    for _attempt in range(len(_CF_WORKERS)):
        _idx = _pick_cf_worker()
        _worker_url = _CF_WORKERS[_idx]
        try:
            req = _urcf.Request(_worker_url, data=payload, method="POST",
                                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"})
            resp = _urcf.urlopen(req, timeout=timeout)
            outer = _jcf.loads(resp.read())
            body = outer.get("body", "")
            if isinstance(body, dict):
                return _jcf.dumps(body)
            return str(body)
        except Exception as _e:
            last_err = _e
            # 连接级错误 → 触发 circuit breaker 60s
            _emsg = str(_e).lower()
            if any(k in _emsg for k in ("connection", "timeout", "refused", "reset", "urlopen")):
                _cf_circuit_until[_idx] = _time.time() + 60
            log(f"[CF] worker[{_idx}] {_worker_url.split('/')[2]} err: {_e}, trying next")
    log(f"[CF] all workers failed: {last_err}")
    return ""


# v3.2: RESI health delegated to resi_pool
# (easy_proxies-style: failure threshold + TTL blacklist, 29 candidates vs old 9)
def _get_healthy_resi_ports_chain() -> list:
    """Delegate to resi_pool: parallel probe 29 ports, cached 5 min."""
    return _rpool.refresh()
WATERMARK       = 5     # fresh 账号低于此值时触发 outlook 补充
REPLENISH_CNT   = 1     # 单次补充目标数量（降低Chrome并发）
COOLDOWN_S      = 600   # 水位补充冷却（15 分钟）
LOCK_FILE       = "/tmp/unitool_chain_replenish.lock"
_REPAIR_COOLDOWN_FILE = "/tmp/ssid_repair_cooldown.json"  # v3.3
_REPAIR_BONUS_MIN     = 5.0   # v3.3: only repair bonus >= 5 accounts


CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"   # reg_v2 用的 MS OAuth client

# ─── 并发状态（thread-local + atexit registry）─────────────────────────────
_thread_local    = threading.local()
_active_lock     = threading.Lock()
_active_registry = {}          # account_id -> {email, done}

# init lock: 多 worker 并发时，只让一个 worker 执行初始化类操作
_init_lock = threading.Lock()

def _reg_account(account_id, email):
    with _active_lock:
        _active_registry[account_id] = {"email": email, "done": False}
    _thread_local.account_id    = account_id
    _thread_local.account_email = email

def _done_account(account_id):
    with _active_lock:
        if account_id in _active_registry:
            _active_registry[account_id]["done"] = True
    _thread_local.account_id    = None
    _thread_local.account_email = None

os.makedirs(SSID_DIR, exist_ok=True)

# ── 日志 ──────────────────────────────────────────────────────────────────────
def log(msg):
    ts   = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    _log_path = getattr(_thread_local, "log_path", LOG)
    try:
        with open(_log_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── atexit：崩溃时解锁 ────────────────────────────────────────────────────────
def _atexit_handler():
    import random as _rand
    with _active_lock:
        items = list(_active_registry.items())
    for _aid, _info in items:
        if _info.get("done"):
            continue
        _aem = _info.get("email")
        if _aem:
            try: _rpool.release_session(_aem)
            except Exception: pass
        try:
            conn = psycopg2.connect(DB_URL)
            cur  = conn.cursor()
            cur.execute("SELECT tags FROM accounts WHERE id=%s", (_aid,))
            row  = cur.fetchone()
            tags = row[0] if row and row[0] else ""
            if "unitool_registered" not in tags:
                new_tags = re.sub(r",?unitool_processing", "", tags).strip(",")
                new_tags = re.sub(r",?unitool_fail", "", new_tags).strip(",")
                if ("unitool_reg_retry" not in new_tags
                        and "unitool_registered" not in new_tags
                        and "unitool_already" not in new_tags
                        and "unitool_verify_pending" not in new_tags):
                    new_tags = (new_tags + ",unitool_reg_retry").strip(",")
                if "abuse_mode" in new_tags and "unitool_reg_retry" in new_tags:
                    new_tags = re.sub(r",?unitool_reg_retry", "", new_tags).strip(",")
                    print(f"[atexit] abuse_mode跳过reg_retry id={_aid}", flush=True)
                _retry_min = _rand.randint(30, 60)
                cur.execute(
                    "UPDATE accounts SET tags=%s, updated_at=NOW() + INTERVAL '%s minutes' WHERE id=%s",
                    (new_tags, _retry_min, _aid))
                conn.commit()
                print(f"[atexit] id={_aid} → {new_tags}", flush=True)
            conn.close()
        except Exception as e:
            print(f"[atexit] err id={_aid}: {e}", flush=True)

atexit.register(_atexit_handler)

# SIGTERM → kill current child proc first, then sys.exit(0) so atexit fires cleanly
_current_proc_lock = threading.Lock()
_current_proc = None

def _sigterm_handler(signum, frame):
    global _current_proc
    log("[signal] SIGTERM received — killing child and exiting cleanly")
    with _current_proc_lock:
        if _current_proc is not None:
            try:
                _current_proc.kill()
            except Exception:
                pass
    sys.exit(0)
signal.signal(signal.SIGTERM, _sigterm_handler)
signal.signal(signal.SIGINT,  _sigterm_handler)  # PM2默认发 SIGINT

# ── DB 工具 ───────────────────────────────────────────────────────────────────
def db_connect():
    return psycopg2.connect(DB_URL)

def db_get_fresh_account():
    """原子选取并锁定一个未注册过 unitool 的 outlook 账号。
    使用 SELECT FOR UPDATE SKIP LOCKED + 同事务内 UPDATE tags，
    彻底消除多 worker 同时选到同一账号的 TOCTOU 竞态。
    返回 (id, email, password, refresh_token) 或 None。
    """
    conn = db_connect()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        # 1. 在事务内原子选取并行锁定候选行
        cur.execute("""
            SELECT id, email, password, refresh_token FROM accounts
            WHERE platform='outlook' AND status='active'
              AND refresh_token IS NOT NULL AND refresh_token != ''
              AND LENGTH(COALESCE(password,'')) >= 8
              AND (tags IS NULL OR (
                   tags NOT LIKE '%%unitool_registered%%'
               AND (tags NOT LIKE '%%unitool_fail%%' OR updated_at < NOW())
               AND tags NOT LIKE '%%unitool_already%%'
               AND tags NOT LIKE '%%ms_token_expired%%'
               AND (tags NOT LIKE '%%unitool_reg_retry%%' OR updated_at < NOW())
               AND tags NOT LIKE '%%unitool_processing%%'
               AND tags NOT LIKE '%%unitool_already%%'
               AND tags NOT LIKE '%%unitool_rescue_dead%%'
               AND tags NOT LIKE '%%unitool_verify_pending%%'
               AND tags NOT LIKE '%%not_found%%'
               AND tags NOT LIKE '%%abuse_mode%%'
              ))
            ORDER BY RANDOM() LIMIT 1
            FOR UPDATE SKIP LOCKED
        """)
        row = cur.fetchone()
        if not row:
            conn.rollback()
            conn.close()
            return None
        account_id = row[0]
        # 2. 同事务内立即打上 unitool_processing 标签（原子锁）
        cur.execute("""
            UPDATE accounts
            SET tags = TRIM(BOTH ',' FROM
                            COALESCE(tags,'') || ',unitool_processing'),
                updated_at = NOW()
            WHERE id = %s
              AND tags NOT LIKE '%%unitool_processing%%'
        """, (account_id,))
        conn.commit()
        return row  # (id, email, password, refresh_token)
    except Exception as e:
        conn.rollback()
        conn.close()
        raise
    finally:
        try: conn.close()
        except: pass

def db_count_fresh():
    conn = db_connect(); cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM accounts
        WHERE platform='outlook' AND status='active'
          AND refresh_token IS NOT NULL AND refresh_token != ''
          AND LENGTH(COALESCE(password,'')) >= 8
          AND (tags IS NULL OR (
               tags NOT LIKE '%%unitool_registered%%'
           AND (tags NOT LIKE '%%unitool_fail%%' OR updated_at < NOW())
           AND tags NOT LIKE '%%unitool_already%%'
           AND tags NOT LIKE '%%ms_token_expired%%'
               AND (tags NOT LIKE '%%unitool_reg_retry%%' OR updated_at < NOW())
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
    import random as _rand2
    if "unitool_reg_retry" in new_tags and "abuse_mode" in new_tags:
        # abuse_mode 账号永不重试注册
        new_tags = re.sub(r",?unitool_reg_retry", "", new_tags).strip(",")
        log(f"[db_mark_fail] abuse_mode跳过reg_retry id={account_id}")
    _retry_sql = ("NOW() + INTERVAL '%d minutes'" % _rand2.randint(30, 60)
                  if "unitool_reg_retry" in new_tags else "NOW()")
    cur.execute(f"UPDATE accounts SET tags=%s, notes=COALESCE(notes,'') || %s, updated_at={_retry_sql} WHERE id=%s",
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

    # 同步写入 SQLite ai_accounts，让数据管理中心/AI服务池能统计 unitool 账号
    try:
        import sqlite3 as _sq
        _db_path = "/data/Toolkit/artifacts/api-server/data.db"
        _sc = _sq.connect(_db_path, timeout=10)
        _sc.execute("""
            INSERT INTO ai_accounts (service, email, api_key, status, notes, created_at, updated_at)
            VALUES ('unitool', ?, ?, 'active', 'chain_v3_auto', datetime('now'), datetime('now'))
            ON CONFLICT DO NOTHING
        """, (email, ssid))
        _sc.commit(); _sc.close()
        log(f"[DB] ai_accounts unitool 写入 OK {email}")
    except Exception as _e:
        log(f"[DB] ai_accounts unitool 写入失败(非致命): {_e}")

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
REF_CODE_CACHE_TTL  = 10800  # 3 小时 (Fix: 1800s 每半小时触发 351×API 全量扫，改为 3h 降低扫描频率)
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

def _verify_ssid_api(ssid: str, account_id: int = 0, max_attempts: int = 2) -> bool:
    """
    Step 6b: 用新账号 SSID 调 /api/user/billing-accounts 验证真实可用性。
    返回 True  = SSID 有效且 API 可读（list 含数据）。
    返回 False = SSID 无效（null / 空列表 / 错误）。
    网络超时最多重试 max_attempts 次，最后兜底 CF Worker。
    """
    for attempt in range(max_attempts):
        if attempt > 0:
            log(f"[verify_ssid] retry {attempt}/{max_attempts-1} ...")
            time.sleep(6)
        try:
            _hp = _get_healthy_resi_ports_chain()
            _port = _hp[(hash(str(account_id)) + attempt) % len(_hp)]
            cmd = [
                "curl", "-s",
                "--socks5-hostname", f"127.0.0.1:{_port}",
                "-b", f"__Secure-unitool-ssid={ssid}",
                "-H", "Accept: application/json",
                "--max-time", "12",
                "https://unitool.ai/api/user/billing-accounts",
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                out, _ = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.communicate()
                log(f"[verify_ssid] timeout attempt={attempt}")
                continue
            raw = out.decode("utf-8", errors="ignore").strip()
            if not raw or raw == "null":
                log(f"[verify_ssid] null/empty → SSID 不可用 id={account_id}")
                return False
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                log(f"[verify_ssid] bad JSON attempt={attempt}: {raw[:60]}")
                continue
            # billing-accounts 返回 {"accounts":[...]} dict 格式
            accts = []
            if isinstance(data, dict):
                accts = data.get("accounts", [])
                if data.get("error"):
                    log(f"[verify_ssid] API error={data.get('error')} → SSID 无效")
                    return False
            elif isinstance(data, list):
                accts = data  # 兜底：旧版 list 格式
            if isinstance(accts, list) and len(accts) > 0:
                log(f"[verify_ssid] OK: {len(accts)} billing 账户 id={account_id}")
                return True
            if isinstance(accts, list) and len(accts) == 0:
                log(f"[verify_ssid] 空账户列表 attempt={attempt}（新账号延迟？），重试")
                continue
            log(f"[verify_ssid] 意外响应: {raw[:80]}")
            continue
        except KeyboardInterrupt:
            raise
        except Exception as _e:
            log(f"[verify_ssid] err attempt={attempt}: {_e}")
            continue
    # RESI 全失败 → CF Worker 兜底
    try:
        _cf_r = _cf_worker_api("https://unitool.ai/api/user/billing-accounts", "GET", ssid)
        if _cf_r and _cf_r not in ("", "null"):
            _cfd = json.loads(_cf_r)
            cf_accts = _cfd.get("accounts", _cfd) if isinstance(_cfd, dict) else _cfd
            if isinstance(cf_accts, list) and len(cf_accts) > 0:
                log(f"[verify_ssid] OK via CF Worker id={account_id}")
                return True
        log(f"[verify_ssid] CF 兜底也失败 → SSID 不可用 id={account_id}")
        return False
    except Exception as _cfe:
        log(f"[verify_ssid] CF err: {_cfe}")
        return False


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
    FIX F: 通过代理调 POST /api/ref-codes，为账号生成专属邀请码。
    本地 RESI 端口出口 IP 已全部被 unitool 记录(ip-already-existed)，直接跳过。
    优先级: 外部代理池 → QuarkIP(IP flip) → 空
    """
    # 本地 RESI 端口已全部 ip-already-existed，直接跳过
    log(f"[ref_create] 跳过本地RESI端口(ip-already-existed)，直接用外部代理+QuarkIP ({email})")
    # v9.45: Tor 优先 - 出口IP从未被CF Worker标记过，速度远超QuarkIP
    _TOR_PORTS = [9050, 9052, 9053, 9054]
    import random as _tor_rnd; _tor_rnd.shuffle(_TOR_PORTS)
    for _tor_port in _TOR_PORTS:
        try:
            _tor_cmd = [
                "curl", "-s", "--max-time", "20",
                "--socks5-hostname", f"127.0.0.1:{_tor_port}",
                "-b", f"__Secure-unitool-ssid={ssid}",
                "-X", "POST", "-H", "Content-Type: application/json",
                "-H", "Accept: application/json",
                "https://unitool.ai/api/ref-codes",
            ]
            _tor_proc = subprocess.Popen(_tor_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                _tor_out, _ = _tor_proc.communicate(timeout=25)
                _tor_raw = _tor_out.decode("utf-8", errors="ignore").strip()
            except subprocess.TimeoutExpired:
                _tor_proc.kill(); _tor_proc.communicate()
                log(f"[tor_ref] port={_tor_port} timeout"); continue
            if not _tor_raw:
                log(f"[tor_ref] port={_tor_port} empty"); continue
            _tor_d = json.loads(_tor_raw)
            _tor_code = _tor_d.get("code", "")
            _tor_err  = _tor_d.get("error", "")
            if _tor_code:
                log(f"[tor_ref] ref_code={_tor_code} port={_tor_port} email={email}")
                try:
                    import subprocess as _sp
                    _nn = _sp.run(
                        ["python3", "/data/Toolkit/scripts/tor_newnym.py"],
                        capture_output=True, timeout=8
                    )
                    log(f"[tor_ref] NEWNYM {'OK' if _nn.returncode == 0 else 'FAIL'}: "
                        f"{_nn.stdout.decode().strip()}")
                except Exception as _ne:
                    log(f"[tor_ref] NEWNYM warn: {_ne}")
                return _tor_code
            log(f"[tor_ref] port={_tor_port} err={_tor_err}")
        except Exception as _tor_e:
            log(f"[tor_ref] port={_tor_port} exc={_tor_e}")
    # 全 RESI 端口失败 → CF Worker 底创建 ref_code（CF 边缘 IP）
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
            else:
                log(f"[CF] create ref_code CF-FAIL body={_cfc_body[:200]}")
        else:
            log(f"[CF] create ref_code CF-EMPTY body={repr(_cfc_body)[:80]}")
    except Exception as _cfe:
        log(f"[CF] create ref fallback err: {_cfe}")

    # 全 RESI + CF 均失败 → 外部代理池底（/tmp/resi_pool_external.json）
    try:
        import json as _jext, random as _rnd
        _ext_file = "/tmp/resi_pool_external.json"
        if os.path.exists(_ext_file):
            _ext_list = list(_jext.loads(open(_ext_file).read()).get("proxies", []))
            _rnd.shuffle(_ext_list)
            for _ext_proxy in _ext_list[:20]:
                try:
                    # 自动检测代理格式: HTTP(s)://... 用 --proxy，否则 socks5-hostname
                    if _ext_proxy.startswith("http://") or _ext_proxy.startswith("https://"):
                        _ep_proxy_args = ["--proxy", _ext_proxy]
                    else:
                        _ep_proxy_args = ["--socks5-hostname", _ext_proxy]
                    _ep_cmd = [
                        "curl", "-s", "--max-time", "15",
                    ] + _ep_proxy_args + [
                        "-b", f"__Secure-unitool-ssid={ssid}",
                        "-X", "POST",
                        "-H", "Content-Type: application/json",
                        "-H", "Accept: application/json",
                        "https://unitool.ai/api/ref-codes",
                    ]
                    _ep_proc = subprocess.Popen(_ep_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    try:
                        _ep_out, _ = _ep_proc.communicate(timeout=20)
                        _ep_raw = _ep_out.decode("utf-8", errors="ignore").strip()
                    except subprocess.TimeoutExpired:
                        _ep_proc.kill(); _ep_proc.communicate(); continue
                    if not _ep_raw:
                        continue
                    _ep_d = _jext.loads(_ep_raw)
                    _ep_code = _ep_d.get("code", "")
                    if _ep_code:
                        log(f"[ext_proxy] ref_code OK proxy={_ext_proxy} code={_ep_code}")
                        return _ep_code
                    _ep_err = _ep_d.get("error", "")
                    log(f"[ext_proxy] {_ext_proxy} err={_ep_err}")
                except Exception as _ep_e:
                    log(f"[ext_proxy] {_ext_proxy} exc={_ep_e}")
    except Exception as _ext_top:
        log(f"[ext_proxy] top err: {_ext_top}")

    # QuarkIP fallback - ONLY for POST /api/ref-codes
    # Integrated from quarkip_ref_create.py: flip IP before each attempt, retry up to 3x
    # WARNING: QuarkIP 200MB quota only, do NOT use QUARK_PROXY outside this function
    try:
        import urllib.request as _ureq, time as _qt
        _QUARK_PROXY = "http://j4eOruul5w:A1enIA12wwBGSKB@pool-us.quarkip.io:7777"
        _QUARK_FLIP  = "http://change.quarkip.io?username=j4eOruul5w&password=A1enIA12wwBGSKB"

        def _quark_flip(wait=4):
            try:
                _ureq.urlopen(_ureq.Request(_QUARK_FLIP, headers={"User-Agent": "curl/7.88"}), timeout=6)
            except Exception:
                pass
            _qt.sleep(wait)

        def _quark_create_one(ssid_v, timeout=20):
            """QuarkIP POST /api/ref-codes, returns (status, value)"""
            _cmd = [
                "curl", "-s", "--max-time", str(timeout),
                "--proxy", _QUARK_PROXY,
                "-b", f"__Secure-unitool-ssid={ssid_v}",
                "-X", "POST",
                "-H", "Content-Type: application/json",
                "-H", "Accept: application/json",
                "https://unitool.ai/api/ref-codes",
            ]
            _p = subprocess.Popen(_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                _o, _ = _p.communicate(timeout=timeout + 5)
                _raw = _o.decode("utf-8", errors="ignore").strip()
            except subprocess.TimeoutExpired:
                _p.kill(); _p.communicate(); return "TIMEOUT", ""
            if not _raw: return "EMPTY", ""
            _d = json.loads(_raw)
            _code = _d.get("code", "")
            _err  = _d.get("error", "")
            if _code: return "OK", _code
            if _err == "ip-already-existed": return "USED", ""
            return "ERR", _err[:80]

        # Attempt up to 3 times, flip IP before each attempt
        for _qattempt in range(3):
            _quark_flip(wait=4 if _qattempt == 0 else 6)
            _qstatus, _qval = _quark_create_one(ssid)
            if _qstatus == "OK":
                log(f"[quarkip] ref_code={_qval} email={email} attempt={_qattempt+1}")
                return _qval
            elif _qstatus == "USED":
                log(f"[quarkip] ip-already-existed attempt={_qattempt+1}, retrying with new IP")
            else:
                log(f"[quarkip] {_qstatus}={_qval} attempt={_qattempt+1}")
                break
        log(f"[quarkip] all 3 attempts failed email={email}")
    except Exception as _qke:
        log(f"[quarkip] exc={_qke}")

    return ""


def db_get_all_ref_codes() -> list:
    """
    获取 DB 中所有有余量的 ref_code（conversions < MAX_REF_SLOTS）。
    对每个账号调 API 验证真实 conversions（带 30min 缓存）。
    返回: [{"id": int, "email": str, "ref_code": str, "used": int}, ...]
    按 used 升序排列（余量多的在前）。
    """
    # -- shared pool cache fast-path (2026-05-14) --
    # w0 completes full API scan once and saves result as _pool_cache;
    # w1/w2 read cache on startup and skip the entire 342-account loop (~8.5 min).
    _pc = _load_ref_cache()
    _pe = _pc.get("_pool_cache", {})
    if _pe and (time.time() - _pe.get("ts", 0)) < REF_CODE_CACHE_TTL:
        _pool_data = _pe.get("data", [])
        _age_min = (time.time() - _pe["ts"]) / 60
        log(f"[ref] pool-cache HIT: {len(_pool_data)} codes age={_age_min:.1f}min, skip full scan")
        return _pool_data
    # scan mutex: if another worker started scanning < 10 min ago, wait
    _scan_started = _pe.get("scan_ts", 0) if _pe else 0
    if _scan_started and (time.time() - _scan_started) < 600:
        log(f"[ref] another worker scanning ({(time.time()-_scan_started):.0f}s ago), waiting for cache...")
        for _wi in range(20):  # wait up to 600s (30s × 20)
            time.sleep(30)
            _wc = _load_ref_cache()
            _wp = _wc.get("_pool_cache", {})
            if _wp.get("ts", 0) > _scan_started:
                _pool_data = _wp.get("data", [])
                log(f"[ref] pool-cache ready after {(_wi+1)*30}s wait: {len(_pool_data)} codes")
                return _pool_data
            # 检测原始 scanner 已挂（scan_ts 被覆盖/清除）→ 立即退出等待
            cur_scan_ts = _wp.get("scan_ts", 0)
            if cur_scan_ts and cur_scan_ts != _scan_started:
                log(f"[ref] scan_ts changed ({_scan_started:.0f}→{cur_scan_ts:.0f}), original scanner died, aborting wait")
                return []  # 返回空让上层重新调度
        log("[ref] wait timeout 600s, scanning ourselves")
    # mark scan-in-progress so other workers wait instead of duplicating work
    _pc["_pool_cache"] = dict(_pe) if _pe else {}
    _pc["_pool_cache"]["scan_ts"] = time.time()
    _save_ref_cache(_pc)
    # -- end shared pool cache fast-path --
    # ── 智能扫描 v2：两阶段快速选码，不做全量 API 扫描 ──────────────────────
    # Phase 1: 只查已被用过的码（notes 含 ref_registered=），数量 < 200，API 验真实 conversions
    # Phase 2: 若 Phase 1 无可用码，取最新 20 个账号的码，本地计数=0，直接用
    conn = db_connect(); cur = conn.cursor()

    # Phase 1: 已被用过的 ref_code（本地有 ref_registered= 记录）
    cur.execute("""
        SELECT id, email, notes, tags FROM accounts
        WHERE platform='outlook'
          AND notes LIKE '%%unitool_ref_code=%%'
          AND notes LIKE '%%ref_registered=%%'
          AND (tags LIKE '%%unitool_ref_master%%' OR tags LIKE '%%unitool_ref_activated%%')
        ORDER BY updated_at DESC
    """)
    used_rows = cur.fetchall()
    log(f"[ref] Phase1: {len(used_rows)} 个已用过的 ref_code 账号，逐个 API 验证")

    available = []

    def _process_row(acc_id, acc_email, notes, tags, force_local=False):
        if not notes:
            return
        m = re.search(r"unitool_ref_code=(?!ref_code=)([A-Za-z0-9_-]+)", notes)
        if not m:
            return
        rc = m.group(1)
        local_used = len(re.findall(r"ref_registered=", notes))
        if force_local:
            used = local_used
        else:
            ssid_m = re.search(r"unitool_ssid=([0-9a-f]{40,})", notes)
            if ssid_m:
                api_rc, api_conv = _api_check_ref_code(ssid_m.group(1), acc_id)
                if api_conv < 0:
                    used = local_used
                    log(f"[ref] id={acc_id} {acc_email} API failed, fallback local={local_used}")
                elif not api_rc:
                    if "unitool_ref_activated" in (tags or "") and "unitool_ref_master" not in (tags or ""):
                        used = local_used
                        log(f"[ref] id={acc_id} {acc_email} ssid_expired/null but ref_activated, local={local_used}")
                    else:
                        log(f"[ref] id={acc_id} {acc_email} API=null + ref_master, skip")
                        return
                else:
                    if api_rc != rc:
                        log(f"[ref] id={acc_id} API rc={api_rc} != DB rc={rc}, use API")
                        rc = api_rc
                    used = api_conv
                    log(f"[ref] id={acc_id} {acc_email} rc={rc} conversions={api_conv}/{MAX_REF_SLOTS}")
            else:
                used = local_used
        if used < MAX_REF_SLOTS and "unitool_high_balance" not in (tags or ""):
            available.append({"id": acc_id, "email": acc_email, "ref_code": rc, "used": used})
        elif "unitool_high_balance" in (tags or "") and used < MAX_REF_SLOTS:
            log(f"[ref] SKIP HB id={acc_id} {acc_email}: already HB, API used={used}/{MAX_REF_SLOTS}, excluded from pool")
        else:
            # HB-promote
            if "unitool_high_balance" not in (tags or ""):
                try:
                    _pr_conn = db_connect(); _pr_cur = _pr_conn.cursor()
                    _new_tags = ((tags or "").rstrip(",") + ",unitool_high_balance").strip(",")
                    _pr_cur.execute("UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s", (_new_tags, acc_id))
                    _pr_conn.commit(); _pr_conn.close()
                    log(f"[ref] HB-promote id={acc_id} {acc_email} conv={used}/{MAX_REF_SLOTS}")
                    try:
                        import urllib.request as _ureq2
                        _hb2 = json.dumps({"email": acc_email}).encode()
                        _ureq2.urlopen(_ureq2.Request(
                            f"http://localhost:{PROXY_PORT}/mark-high-balance",
                            data=_hb2, headers={"Content-Type": "application/json"}), timeout=2)
                    except Exception:
                        pass
                except Exception as _pre:
                    log(f"[ref] HB-promote err id={acc_id}: {_pre}")

    for acc_id, acc_email, notes, tags in used_rows:
        _process_row(acc_id, acc_email, notes, tags, force_local=False)

    if not available:
        # Phase 2: 没有可用的已用码 → 取最新 20 个账号的 ref_code，本地计数，不调 API
        log("[ref] Phase1 无可用码，Phase2: 取最新 20 个账号 ref_code（本地计数=0）")
        cur.execute("""
            SELECT id, email, notes, tags FROM accounts
            WHERE platform='outlook'
              AND notes LIKE '%%unitool_ref_code=%%'
              AND (tags LIKE '%%unitool_ref_master%%' OR tags LIKE '%%unitool_ref_activated%%')
            ORDER BY updated_at DESC
            LIMIT 20
        """)
        fresh_rows = cur.fetchall()
        for acc_id, acc_email, notes, tags in fresh_rows:
            _process_row(acc_id, acc_email, notes, tags, force_local=True)
        log(f"[ref] Phase2: 得到 {len(available)} 个可用码")

    conn.close()

    # 去重：同一 ref_code 保留 used 最大那条
    dedup: dict = {}
    for r in available:
        rc = r["ref_code"]
        if rc not in dedup or r["used"] > dedup[rc]["used"]:
            dedup[rc] = r
    available = list(dedup.values())
    # 填满优先：used 多的排前；used 相同时 id 大（更新）的排前
    available.sort(key=lambda x: (x["used"], x["id"]), reverse=True)
    log(f"[ref] 可用 ref_code 池: {len(available)} 个 → " +
        ", ".join(f"{r['ref_code']}({r['used']}/{MAX_REF_SLOTS})" for r in available[:10]))
    # 保存缓存
    try:
        _fc = _load_ref_cache()
        _fc["_pool_cache"] = {"data": available, "ts": time.time()}
        _save_ref_cache(_fc)
        log(f"[ref] pool-cache saved: {len(available)} codes -> {REF_CODE_CACHE_FILE}")
    except Exception as _fce:
        log(f"[ref] pool-cache save failed (ignored): {_fce}")
    return available


def _pool_cache_increment_used(used_ref_code: str) -> None:
    """
    每次注册成功后立即更新 pool-cache：把 used_ref_code 的 used +1。
    若达到 MAX_REF_SLOTS 则从 pool 中剔除，避免缓存期内继续被选中浪费注册次数。
    同时重置 _api_check_ref_code 的单账号缓存，使下次扫描得到真实值。
    """
    try:
        fc = _load_ref_cache()
        pc = fc.get("_pool_cache", {})
        data = pc.get("data", [])
        updated = False
        new_data = []
        for entry in data:
            if entry.get("ref_code") == used_ref_code:
                entry = dict(entry)
                new_used = entry.get("used", 0) + 1
                # 反漂移：pool-cache 不能比个人 API 真实值高超过 3
                # （允许 3 个在途注册尚未被 unitool 计入 conversion）
                acc_key = str(entry.get("id", ""))
                ind_entry = fc.get(acc_key, {})
                ind_conv = ind_entry.get("conversions") if isinstance(ind_entry, dict) else None
                if isinstance(ind_conv, int) and ind_conv >= 0 and new_used > ind_conv + 3:
                    log(f"[ref] pool-cache drift: {used_ref_code} pool={new_used} api={ind_conv}, cap→{ind_conv + 1}")
                    new_used = ind_conv + 1
                entry["used"] = new_used
                updated = True
                if entry["used"] >= MAX_REF_SLOTS:
                    log(f"[ref] pool-cache: {used_ref_code} used={entry['used']}/{MAX_REF_SLOTS} → 剔除")
                    # 同时清除该账号的单账号 API 缓存（让下次扫描重新查 API）
                    acc_key = str(entry.get("id", ""))
                    if acc_key and acc_key in fc:
                        del fc[acc_key]
                    continue  # 从池中移除
                new_data.append(entry)
            else:
                new_data.append(entry)
        if updated:
            # 重新排序：填满优先（used 降序）
            new_data.sort(key=lambda x: x["used"], reverse=True)
            pc["data"] = new_data
            fc["_pool_cache"] = pc
            _save_ref_cache(fc)
            log(f"[ref] pool-cache updated: {used_ref_code} +1, 剩余可用池 {len(new_data)} 个")
        else:
            log(f"[ref] pool-cache: {used_ref_code} not found in cache (may be new or already evicted)")
    except Exception as _pce:
        log(f"[ref] pool-cache increment err: {_pce}")


def pick_rotating_ref_code(pool: list) -> dict | None:
    """
    填满优先：pool 已按 used 降序排列，始终选 pool[0]。
    当前码用满 10 个后，_pool_cache_increment_used 会立即将其剔除，
    pool[0] 自然切换到下一个待填满的码（无需等待 3h 缓存过期）。
    """
    if not pool:
        return None
    chosen = pool[0]
    log(f"[ref] 填满优先 #{1}/{len(pool)}: ref_code={chosen['ref_code']} "
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
    # 只允许主进程(WORKER_ID=0)触发注册，避免 w1/w2 并发触发造成 Chrome 风暴
    if WORKER_ID != 0:
        return
    fresh = db_count_fresh()
    log(f"[watermark] fresh={fresh} watermark={WATERMARK}")
    if fresh >= WATERMARK:
        return

    # 冷却检查：fresh=0 用短冷却(120s)，fresh>0 用正常冷却(COOLDOWN_S)
    # Fix: 原逻辑 fresh=0 完全绕过冷却 → 3个内部线程依次各触发一次造成Chrome风暴
    _cd = 120 if fresh == 0 else COOLDOWN_S
    try:
        data = json.loads(open(LOCK_FILE).read())
        ts = float(data.get("ts", 0))
        if time.time() - ts < _cd:
            remaining = int(_cd - (time.time() - ts))
            if fresh == 0:
                log(f"[watermark] ⚠ fresh=0 但冷却中 {remaining}s，跳过（防止Chrome风暴）")
            else:
                log(f"[watermark] 冷却中 {remaining}s，跳过补充")
            return
    except Exception:
        pass
    if fresh == 0:
        log("[watermark] ⚠ fresh=0 紧急状态，触发补充")

    # 内存检查（outlook 注册每 worker 约 400-600 MB）
    try:
        for line in open("/proc/meminfo"):
            if "MemAvailable" in line:
                mb = int(line.split()[1]) // 1024
                if mb < 2000:
                    log(f"[watermark] 内存不足 {mb}MB < 2000MB，跳过补充"); return
                break
    except Exception:
        pass

    # fresh=0 时按 N_WORKERS 动态扩大 batch，保证 3 个 worker 都有账号
    _batch = max(REPLENISH_CNT, N_WORKERS) if fresh == 0 else REPLENISH_CNT
    log(f"[watermark] 🚀 触发 outlook 补充注册 fresh={fresh} batch={_batch}")
    try:
        payload = json.dumps({
            "count":     _batch,
            "headless":  True,
            "proxyMode": "cf",
            "engine":    "patchright",
            "wait":      11,
            "retries":   2,
            "workers":   2,   # 显式指定2防止API自动升档到6
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
def db_get_ssid_repair_account():
    """v3.3: pick highest-bonus registered account with no valid SSID."""
    conn = db_connect(); cur = conn.cursor()
    cur.execute("""
        SELECT a.id, a.email, a.password
        FROM accounts a
        WHERE a.platform = 'outlook'
          AND a.tags LIKE '%%unitool_registered%%'
          AND a.status = 'active'
          AND a.password IS NOT NULL AND LENGTH(a.password) >= 8
          AND NOT EXISTS (
              SELECT 1 FROM unitool_ssids us
              WHERE LOWER(TRIM(us.source_email)) = LOWER(TRIM(a.email))
                AND us.is_valid = true
          )
    """)
    rows = cur.fetchall(); conn.close()
    if not rows:
        return None
    try:
        cache = json.loads(open("/tmp/unitool_token_cache.json").read())
    except Exception:
        cache = {}
    try:
        cd_raw = json.loads(open(_REPAIR_COOLDOWN_FILE).read())
    except Exception:
        cd_raw = {}
    now_ts = time.time()
    active_cd = {int(k) for k, ts in cd_raw.items() if now_ts - float(ts) < 86400}
    enriched = []
    for acc_id, email, pw in rows:
        if acc_id in active_cd:
            continue
        bonus = float((cache.get(str(acc_id)) or {}).get("bonus") or 0)
        if bonus >= _REPAIR_BONUS_MIN:
            enriched.append((acc_id, email, pw, bonus))
    if not enriched:
        return None
    enriched.sort(key=lambda x: -x[3])
    return enriched[0]  # (id, email, password, bonus)


def run_ssid_repair():
    """v3.3: re-login to get fresh SSID for highest-bonus no-SSID account."""
    row = db_get_ssid_repair_account()
    if not row:
        return
    acc_id, email, password, bonus = row
    log(f"[repair] v3.3 bonus={bonus:.2f} {email} id={acc_id}")
    ssid = run_login(email, password)
    if ssid:
        db_save_ssid_full(acc_id, email, ssid)
        persist_ssid(email, ssid)
        log(f"[repair] OK {email} ssid_len={len(ssid)}")
        # hot-push high_balance to proxy if bonus qualifies
        try:
            _hd = json.dumps({"email": email}).encode()
            urllib.request.urlopen(
                urllib.request.Request(
                    f"http://localhost:{PROXY_PORT}/mark-high-balance",
                    data=_hd,
                    headers={"Content-Type": "application/json"}),
                timeout=3)
            log(f"[repair] HB push OK {email}")
        except Exception:
            pass
    else:
        log(f"[repair] FAIL {email} -> 24h cooldown")
        try:
            try:
                cd = json.loads(open(_REPAIR_COOLDOWN_FILE).read())
            except Exception:
                cd = {}
            cd[str(acc_id)] = time.time()
            open(_REPAIR_COOLDOWN_FILE, "w").write(json.dumps(cd))
        except Exception as e:
            log(f"[repair] cooldown write err: {e}")


def check_resources(max_wait: int = 300, interval: int = 30) -> bool:
    """v5.15: 内存>=700MB 且 Chrome主进程<=2个。
    内部轮询等待直到满足条件或超时（默认最多等 5 分钟）。
    返回 True=可以继续，False=等待超时仍不满足。
    """
    def _get_mem_mb():
        try:
            for line in open("/proc/meminfo"):
                if "MemAvailable" in line:
                    return int(line.split()[1]) // 1024
        except Exception:
            pass
        return 9999

    def _get_chrome_main():
        try:
            _p = subprocess.Popen(
                ["bash", "-c",
                 "ps aux | grep chrome-linux64/chrome | grep 'remote-debugging-port' | grep -v 'crashpad\|grep\|--type=' | wc -l"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                _o, _ = _p.communicate(timeout=10)
                return max(0, int(_o.decode("utf-8", errors="ignore").strip() or 0))
            except KeyboardInterrupt:
                try: _p.kill(); _p.communicate()
                except Exception: pass
                raise
            except Exception:
                return 0
        except Exception:
            return 0

    waited = 0
    while True:
        mb = _get_mem_mb()
        n  = _get_chrome_main()
        log(f"[res] 内存={mb}MB  Chrome主进程数={n}")

        mem_ok    = mb >= 700
        chrome_ok = n < CHROME_LIMIT

        if mem_ok and chrome_ok:
            return True

        if waited >= max_wait:
            log(f"[res] ⚠ 等待超时 {max_wait}s：mem={mb}MB chrome={n}，强制跳过")
            return False

        reasons = []
        if not mem_ok:    reasons.append(f"内存{mb}MB<700")
        if not chrome_ok: reasons.append(f"Chrome={n}>=3")
        log(f"[res] 等待 {interval}s（{'、'.join(reasons)}）已等 {waited}s / 上限 {max_wait}s")
        time.sleep(interval)
        waited += interval

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
        global _current_proc
        with _current_proc_lock:
            _current_proc = proc
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.communicate()
            log(f"[cmd] TIMEOUT {timeout}s: {label}")
            return "", "TIMEOUT", -1
        finally:
            with _current_proc_lock:
                if _current_proc is proc:
                    _current_proc = None
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
        # 暂态失败: bypass/CF 类错误先换 IP 重试一次再降级全浏览器 [chain_v3_v9_applied]
        _is_bypass_err = any(p in err_s for p in (
            "bypass_failed", "token_empty", "bypass", "cf_", "cloudflare",
            "timeout", "connection", "proxy", "socks"))
        if _is_bypass_err:
            log(f"[http_reg] ⚠ 暂态bypass失败: {err} → 换IP重试一次")
            try:
                # 释放当前 sticky IP，强制 pick_sticky 分配新 IP
                if hasattr(_rpool, 'release_session'):
                    _rpool.release_session(email)
                elif hasattr(_rpool, '_sticky') and email in _rpool._sticky:
                    del _rpool._sticky[email]
            except Exception as _re:
                log(f"[http_reg] release IP err(non-fatal): {_re}")
            try:
                result2 = _run_http_reg_with_pw(email, ref_code)
            except KeyboardInterrupt:
                raise
            except Exception as e2:
                log(f"[http_reg] 重试异常: {e2}")
                result2 = {"ok": False, "error": str(e2)}
            if result2.get("ok"):
                log(f"[http_reg] ✅ 换IP重试成功 method={result2.get('method','?')}")
                return {"ok": True, "ssid": "", "reason": ""}
            err2 = str(result2.get("error_type") or result2.get("error") or "")
            log(f"[http_reg] 换IP重试失败: {err2} → 降级全浏览器")
        else:
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
    if rc != 0:
        _stderr_s = stderr.decode(errors="replace") if isinstance(stderr, bytes) else str(stderr)
        log(f"[login] stderr: {_stderr_s[-300:]}")
    log(f"[login] 未拿到 ssid rc={rc}")
    return ""

def run_inline_verify(email: str, password: str, refresh_token: str,
                      max_wait: int = 90, after_ts: float = 0.0,
                      account_id: int = 0) -> str:
    # Graph Only（无 IMAP）: after_ts=注册时刻，仅处理之后到达的邮件。
    # settle: 找到首个 verify_url 后多等 SETTLE_SECONDS，确认无更新邮件才点击。
    SETTLE_SECONDS = 6
    if not refresh_token:
        log("[inline_verify] 无 refresh_token -> 跳过"); return ""

    access_token = ""
    try:
        _body = urllib.parse.urlencode({
            "grant_type":    "refresh_token",
            "client_id":     CLIENT_ID,
            "refresh_token": refresh_token,
            "scope":         "https://graph.microsoft.com/Mail.Read offline_access",
        }).encode()
        _req = urllib.request.Request(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data=_body,
        )
        import json as _jj
        _resp = _jj.loads(urllib.request.urlopen(_req, timeout=20).read())
        access_token = _resp.get("access_token", "")
        log(f"[inline_verify] Graph token len={len(access_token)}")
    except urllib.error.HTTPError as _he:
        _err_raw = _he.read().decode(errors="replace")
        log(f"[inline_verify] token fail HTTP {_he.code}: {_err_raw[:200]}")
        # AADSTS70000 = 微软永久封禁该账号（service abuse mode），token 不可恢复
        # 直接标 abuse_mode+suspended，返回特殊标记让调用方跳过 verify_pending 队列
        if "AADSTS70000" in _err_raw or "service abuse mode" in _err_raw:
            log(f"[inline_verify] ⛔ AADSTS70000 → 账号已被微软永久封禁，标 abuse_mode")
            try:
                _ac = db_connect(); _ac_cur = _ac.cursor()
                _ac_cur.execute("SELECT tags FROM accounts WHERE id=%s", (account_id,))
                _row = _ac_cur.fetchone(); _tags = _row[0] if _row and _row[0] else ""
                _new = re.sub(r",?unitool_(processing|verify_pending|reg_retry)", "", _tags).strip(",")
                for _t in ("abuse_mode", "unitool_fail"):
                    if _t not in _new:
                        _new = (_new + "," + _t).strip(",")
                _ac_cur.execute(
                    "UPDATE accounts SET status='suspended', tags=%s, updated_at=NOW() WHERE id=%s",
                    (_new, account_id))
                _ac.commit(); _ac.close()
                log(f"[inline_verify] DB 标记完成 → {_new}")
            except Exception as _dbe:
                log(f"[inline_verify] DB 标记失败(非致命): {_dbe}")
            return "__AADSTS70000__"   # 特殊标记，让调用方不进 verify_pending
        return ""
    except Exception as e:
        log(f"[inline_verify] token fail: {e}"); return ""

    if not access_token:
        return ""

    import json as _jjv
    import datetime as _dt
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    verify_url = ""
    # 时间过滤: 只取注册之后收到的邮件，防止复用旧 token（容差 -30s）
    _after_iso = ""
    if after_ts > 0:
        _after_iso = _dt.datetime.utcfromtimestamp(after_ts - 30).strftime("%Y-%m-%dT%H:%M:%SZ")
        log(f"[inline_verify] 时间过滤 receivedDateTime >= {_after_iso}")
    _poll_schedule = [5, 10, 10, 15, 15, 15, 15]
    _elapsed_poll = 0
    _settle_until = 0.0
    for _pi, _pw in enumerate(_poll_schedule):
        if _elapsed_poll >= max_wait:
            break
        _sleep_now = min(_pw, max_wait - _elapsed_poll)
        time.sleep(_sleep_now)
        _elapsed_poll += _sleep_now
        _found_new = False
        for folder in ("JunkEmail", "Inbox", "Clutter", "DeletedItems"):
            try:
                _f_parts = ["from/emailAddress/address eq 'noreply@unitool.ai'"]
                if _after_iso:
                    _f_parts.append(f"receivedDateTime ge {_after_iso}")
                _filter = urllib.parse.quote(" and ".join(_f_parts))
                _url = (f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
                        f"?$filter={_filter}"
                        f"&$top=5&$select=subject,body,receivedDateTime,uniqueBody")
                _rq = urllib.request.Request(_url, headers=headers)
                msgs = _jjv.loads(urllib.request.urlopen(_rq, timeout=15).read()).get("value", [])
                log(f"[inline_verify] {folder}: {len(msgs)} msgs")
                for m in msgs:
                    body = (m.get("uniqueBody") or m.get("body") or {}).get("content", "")
                    links = re.findall(
                        r"https://[^\s\"'<>]*unitool\.ai/api/auth/email[^\s\"'<>]*", body)
                    if not links:
                        links = re.findall(
                            r"https://unitool\.ai[^\s\"'<>]*token=[^\s\"'<>]*", body)
                    if links and links[0] != verify_url:
                        verify_url = links[0]
                        _settle_until = time.time() + SETTLE_SECONDS
                        log(f"[inline_verify] 新url @{_elapsed_poll}s {folder}: {verify_url[:80]}")
                        _found_new = True
                        break
                if _found_new:
                    break
            except Exception as e:
                log(f"[inline_verify] {folder} err: {e}")
        if verify_url:
            _remain = _settle_until - time.time()
            if _remain > 0.3:
                log(f"[inline_verify] settle {_remain:.1f}s — 等待确认最新 token...")
                continue
            break
        log(f"[inline_verify] [{_elapsed_poll}s] not found")

    if not verify_url:
        log(f"[inline_verify] {max_wait}s 内未收到验证邮件 -> 交 verify_rescue 处理")
        return ""

    _ekey = re.sub(r"[^a-z0-9]", "_", email.lower())[:24]
    ck  = f"/tmp/unitool_ck_{_ekey}.txt"
    hdr = f"/tmp/unitool_hdr_{_ekey}.txt"
    for f in [ck, hdr]:
        try: os.remove(f)
        except: pass
    # FIX A: pick_sticky 可返回外部代理字符串，需 isinstance 区分 (同 Bug 1)
    _verify_port = _rpool.pick_sticky(email)
    # Bug F fix: 外部 SOCKS5 代理无法正确返回 HTTPS Set-Cookie → 降级到 RESI 端口
    if not isinstance(_verify_port, int):
        _hp_v = _get_healthy_resi_ports_chain()
        if _hp_v:
            _verify_port = _hp_v[hash(email) % len(_hp_v)]
            log(f"[inline_verify] Bug-F: ext proxy → RESI fallback port={_verify_port}")
    log(f"[inline_verify] 通过代理 port={_verify_port} 点击验证链接")
    if isinstance(_verify_port, int):
        _verify_proxy_args = ["--socks5-hostname", f"127.0.0.1:{_verify_port}"]
    elif _verify_port.startswith("http://") or _verify_port.startswith("https://"):
        _verify_proxy_args = ["--proxy", _verify_port]
    else:
        _verify_proxy_args = ["--socks5-hostname", _verify_port]
    _curl = [
        "curl", "-sS", "-L", "--max-redirs", "8",
    ] + _verify_proxy_args + [
        "-c", ck, "-b", ck, "-D", hdr,
        "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0",
        "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.9",
        "--max-time", "30",
        verify_url,
    ]
    ssid = ""; to_entry = False
    try:
        _proc = subprocess.Popen(_curl, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            _proc.communicate(timeout=35)
        except subprocess.TimeoutExpired:
            _proc.kill(); _proc.communicate()
        raw_hdrs = open(hdr, encoding="utf-8", errors="ignore").read() if os.path.exists(hdr) else ""
        for line in raw_hdrs.splitlines():
            if "unitool-ssid" in line.lower() and "set-cookie" in line.lower():
                m2 = re.search(r"unitool-ssid=([^;\s]+)", line, re.I)
                if m2: ssid = m2.group(1)
            if "/entry" in line and "location" in line.lower():
                to_entry = True
        if not ssid and os.path.exists(ck):
            for line in open(ck, encoding="utf-8", errors="ignore"):
                if "unitool-ssid" in line.lower():
                    parts = line.strip().split("\t")
                    ssid  = parts[-1] if parts else ""; break
        log(f"[inline_verify] curl ssid={len(ssid) if ssid else 0} to_entry={to_entry}")
    except KeyboardInterrupt:
        raise
    except Exception as e:
        log(f"[inline_verify] curl err: {e}")

    if ssid:
        log(f"[inline_verify] ssid from curl Set-Cookie len={len(ssid)}")
        return ssid

    # FIX C: curl无ssid -> 5s等待DB传播，再run_login；若仍失败则重poll 60s找新验证邮件
    log("[inline_verify] curl无ssid -> 等5s后run_login()")
    time.sleep(5)
    if not check_resources():
        log("[inline_verify] 资源不足，跳过 run_login"); return ""
    _first_ssid = run_login(email, password)
    if _first_ssid:
        return _first_ssid
    # run_login 失败 -> 尝试重新 poll 收件箱（token 可能被 Outlook 预消费）
    log("[inline_verify] run_login失败 -> 再等60s重poll收件箱")
    _new_verify_url = ""
    for _ri in range(6):
        time.sleep(10)
        for _rfolder in ("JunkEmail", "Inbox", "Clutter"):
            try:
                _rf_parts = ["from/emailAddress/address eq 'noreply@unitool.ai'"]
                if _after_iso:
                    _rf_parts.append(f"receivedDateTime ge {_after_iso}")
                _rf2 = urllib.parse.quote(" and ".join(_rf_parts))
                _rurl = (f"https://graph.microsoft.com/v1.0/me/mailFolders/{_rfolder}/messages"
                         f"?$filter={_rf2}"
                         f"&$top=10&$select=subject,body,receivedDateTime,uniqueBody")
                _rq2 = urllib.request.Request(_rurl, headers=headers)
                _rmsgs = _jjv.loads(urllib.request.urlopen(_rq2, timeout=15).read()).get("value", [])
                for _rm in _rmsgs:
                    _rb = (_rm.get("uniqueBody") or _rm.get("body") or {}).get("content", "")
                    _rls = re.findall(
                        r"https://[^\s\"'<>]*unitool\.ai/api/auth/email[^\s\"'<>]*", _rb)
                    if not _rls:
                        _rls = re.findall(
                            r"https://unitool\.ai[^\s\"'<>]*token=[^\s\"'<>]*", _rb)
                    for _rl in _rls:
                        if _rl != verify_url:
                            _new_verify_url = _rl
                            log(f"[inline_verify] repoll新url +{10*(1+_ri)}s: {_rl[:80]}")
                            break
                if _new_verify_url:
                    break
            except Exception as _re:
                log(f"[inline_verify] repoll {_rfolder} err: {_re}")
        if _new_verify_url:
            break
        log(f"[inline_verify] retry_poll [{10*(1+_ri)}s] no new url")
    if _new_verify_url:
        _nck = f"/tmp/unitool_ck2_{_ekey}.txt"; _nhdr = f"/tmp/unitool_hdr2_{_ekey}.txt"
        for _f2 in [_nck, _nhdr]:
            try: os.remove(_f2)
            except: pass
        _ncurl = ["curl", "-sS", "-L", "--max-redirs", "8"] + _verify_proxy_args + [
            "-c", _nck, "-b", _nck, "-D", _nhdr,
            "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0",
            "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.9",
            "--max-time", "30", _new_verify_url,
        ]
        try:
            _np = subprocess.Popen(_ncurl, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try: _np.communicate(timeout=35)
            except subprocess.TimeoutExpired: _np.kill(); _np.communicate()
            _nraw = open(_nhdr, encoding="utf-8", errors="ignore").read() if os.path.exists(_nhdr) else ""
            _nssid = ""
            for _nl in _nraw.splitlines():
                if "unitool-ssid" in _nl.lower() and "set-cookie" in _nl.lower():
                    _nm = re.search(r"unitool-ssid=([^;\s]+)", _nl, re.I)
                    if _nm: _nssid = _nm.group(1)
            if not _nssid and os.path.exists(_nck):
                for _nl2 in open(_nck, encoding="utf-8", errors="ignore"):
                    if "unitool-ssid" in _nl2.lower():
                        _nssid = _nl2.strip().split("\t")[-1]; break
            log(f"[inline_verify] new_click ssid={len(_nssid) if _nssid else 0}")
            if _nssid:
                return _nssid
        except Exception as _ne:
            log(f"[inline_verify] new_click err: {_ne}")
        time.sleep(3)
        if check_resources():
            return run_login(email, password)
    else:
        log("[inline_verify] 60s内无新验证邮件 -> 交 verify_rescue 处理")
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


# =============================================================================
# cfmail pipeline helpers
# =============================================================================

def _cfmail_gen_name():
    import random as _rcf
    first  = _rcf.choice(_CFMAIL_FIRST)
    last   = _rcf.choice(_CFMAIL_LAST)
    suffix = str(_rcf.randint(10, 9999)) if _rcf.random() > 0.10 else ""
    return first + "." + last + suffix


def _cfmail_create_addr(inst):
    for _attempt in range(5):
        name = _cfmail_gen_name()
        url  = "https://" + inst["host"] + "/jimhacker/new_address"
        cmd  = [
            "curl", "-sS", "-X", "POST", url,
            "-H", "x-custom-auth: " + inst["site_auth"],
            "-H", "x-admin-auth: "  + inst["admin_auth"],
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"name": name}),
            "--max-time", "20",
        ]
        try:
            out  = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=25)
            data = json.loads(out)
            if data.get("address") or data.get("email"):
                email = data.get("address") or data.get("email") or (name + "@" + inst["domain"])
                jwt   = data.get("jwt") or data.get("token") or ""
                log("[cfmail] created " + email + "  jwt_len=" + str(len(jwt)))
                return {"ok": True, "email": email, "jwt": jwt}
            log("[cfmail] create fail attempt=" + str(_attempt+1) + ": " + str(data))
        except Exception as _e:
            log("[cfmail] create exc attempt=" + str(_attempt+1) + ": " + str(_e))
        time.sleep(2)
    return {"ok": False, "error": "create_failed_after_5_attempts"}


def _cfmail_poll_d1(email_addr, inst, after_ts=0.0, max_wait=120):
    import quopri as _qp
    # BugFix: per-instance D1 凭证，不再硬编码 jonjim
    _CF_TOKEN = inst["d1_token"]
    _CF_ACC   = inst["d1_acc"]
    _CF_DB    = inst["d1_db"]
    _API      = ("https://api.cloudflare.com/client/v4/accounts/" + _CF_ACC
                 + "/d1/database/" + _CF_DB + "/query")
    log("[cfmail_poll] D1 target=" + inst["domain"] + " db=" + _CF_DB[:8] + "...")
    SCHED   = [8, 10, 12, 15, 15, 15, 20, 20]
    elapsed = 0
    for _wait in SCHED:
        if elapsed >= max_wait:
            break
        _sleep = min(_wait, max_wait - elapsed)
        log("[cfmail_poll] wait " + str(_sleep) + "s (" + str(elapsed) + "/" + str(max_wait) + "s)...")
        time.sleep(_sleep); elapsed += _sleep
        _sql = ("SELECT raw FROM raw_mails WHERE address='" + email_addr + "' "
                "ORDER BY id DESC LIMIT 3")
        try:
            _body = json.dumps({"sql": _sql}).encode()
            _req  = urllib.request.Request(_API, data=_body, headers={
                "Authorization": "Bearer " + _CF_TOKEN,
                "Content-Type":  "application/json",
            })
            _resp = json.loads(urllib.request.urlopen(_req, timeout=20).read())
            _rows = _resp["result"][0]["results"]
            log("[cfmail_poll] D1 rows=" + str(len(_rows)))
        except Exception as _e:
            log("[cfmail_poll] D1 err: " + str(_e)); continue
        for _row in _rows:
            _raw = _row.get("raw", "")
            if not _raw:
                continue
            try:
                _decoded = _qp.decodestring(_raw.encode()).decode("utf-8", errors="replace")
            except Exception:
                _decoded = _raw
            _urls = re.findall(
                r"https://unitool\.ai/api/auth/email\?token=[A-Za-z0-9._\-]+", _decoded)
            if not _urls:
                _urls = re.findall(
                    r"https://unitool\.ai[^\s\"'<>]*token=[^\s\"'<>]*", _decoded)
            if _urls:
                log("[cfmail_poll] found verify URL: " + _urls[0][:80])
                return _urls[0]
        log("[cfmail_poll] [" + str(elapsed) + "s] no mail yet")
    log("[cfmail_poll] timeout " + str(max_wait) + "s")
    return ""


def run_cfmail_chain(ref_code):
    # BugFix: 轮换策略选 inst，不再随机
    inst = _cfmail_pick_inst()

    addr = _cfmail_create_addr(inst)
    if not addr["ok"]:
        _cfmail_mark_fail(inst)
        return {"ok": False, "email": "", "ssid": "", "reason": addr["error"]}
    email = addr["email"]
    jwt   = addr["jwt"]

    account_id = 0
    try:
        _c = db_connect(); _cu = _c.cursor()
        _cu.execute(
            "INSERT INTO accounts "
            "(platform, email, password, refresh_token, status, tags, created_at, updated_at) "
            "VALUES ('cfmail', %s, 'Unitool@2024!', %s, 'active', 'unitool_processing', NOW(), NOW()) "
            "ON CONFLICT (platform, email) DO UPDATE "
            "  SET refresh_token = EXCLUDED.refresh_token, "
            "      tags = 'unitool_processing', updated_at = NOW()",
            (email, jwt))
        _c.commit()
        _cu.execute("SELECT id FROM accounts WHERE email=%s", (email,))
        _r2 = _cu.fetchone()
        account_id = _r2[0] if _r2 else 0
        _c.close()
        log("[cfmail] DB account_id=" + str(account_id))
    except Exception as _e:
        log("[cfmail] DB insert err: " + str(_e))

    reg_ts = time.time()

    reg = run_register_fast(email, ref_code)
    if not reg["ok"]:
        reason = reg.get("reason", "unknown")
        log("[cfmail] reg fail: " + reason)
        if account_id:
            db_mark_fail(account_id, reason)
        return {"ok": False, "email": email, "ssid": "", "reason": reason}
    log("[cfmail] unitool register OK")

    verify_url = _cfmail_poll_d1(email, inst, after_ts=reg_ts, max_wait=120)
    if not verify_url:
        _cfmail_mark_fail(inst)
        if account_id:
            db_mark_fail(account_id, "verify_email_not_found")
        return {"ok": False, "email": email, "ssid": "", "reason": "verify_email_not_found"}

    _ekey = re.sub(r"[^a-z0-9]", "_", email.lower())[:24]
    _ck   = "/tmp/cfmail_ck_" + _ekey + ".txt"
    _hdr  = "/tmp/cfmail_hdr_" + _ekey + ".txt"
    for _f in [_ck, _hdr]:
        try: os.remove(_f)
        except: pass
    _port = RESI_PORTS[hash(email) % len(RESI_PORTS)]
    _cmd  = [
        "curl", "-sS", "-L", "--max-redirs", "8",
        "--socks5-hostname", "127.0.0.1:" + str(_port),
        "-c", _ck, "-b", _ck, "-D", _hdr,
        "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0",
        "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.9",
        "--max-time", "30",
        verify_url,
    ]
    log("[cfmail] click verify port=" + str(_port))
    ssid = ""
    try:
        _proc = subprocess.Popen(_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try: _proc.communicate(timeout=35)
        except subprocess.TimeoutExpired: _proc.kill(); _proc.communicate()
        if os.path.exists(_hdr):
            for _ln in open(_hdr, encoding="utf-8", errors="ignore"):
                if "unitool-ssid" in _ln.lower() and "set-cookie" in _ln.lower():
                    _m = re.search(r"unitool-ssid=([^;\s]+)", _ln, re.I)
                    if _m: ssid = _m.group(1); break
        if not ssid and os.path.exists(_ck):
            for _ln in open(_ck, encoding="utf-8", errors="ignore"):
                if "unitool-ssid" in _ln.lower():
                    _pts = _ln.strip().split("\t")
                    ssid = _pts[-1] if _pts else ""; break
    except Exception as _e:
        log("[cfmail] curl err: " + str(_e))
    log("[cfmail] ssid len=" + str(len(ssid)))

    if not ssid:
        _cfmail_mark_fail(inst)
        if account_id:
            db_mark_fail(account_id, "no_ssid_after_click")
        return {"ok": False, "email": email, "ssid": "", "reason": "no_ssid_after_click"}

    _cfmail_mark_ok(inst)
    if account_id:
        db_save_ssid_full(account_id, email, ssid)
        try:
            _c2 = db_connect(); _cu2 = _c2.cursor()
            _cu2.execute(
                "UPDATE accounts SET tags='unitool_registered', updated_at=NOW() WHERE id=%s",
                (account_id,))
            _c2.commit(); _c2.close()
        except Exception: pass
    persist_ssid(email, ssid)
    return {"ok": True, "email": email, "ssid": ssid, "reason": ""}

def main():
    _thread_local.account_id    = None
    _thread_local.account_email = None
    _log_path = getattr(_thread_local, "log_path", LOG)
    open(_log_path, "w").write("")
    log("=" * 60)
    log("=== unitool_chain_v3 start ===")

    # ── Step 0: 初始化（多 worker 并发时只让第一个进入的 worker 执行）──────────
    if _init_lock.acquire(blocking=False):
        try:
            try:
                _check_na_daily()
            except Exception as e:
                log(f"[na_probe] 异常(忽略): {e}")
            try:
                run_ssid_repair()
            except Exception as e:
                log(f"[repair] exception (ignored): {e}")
            try:
                cleanup_stale_processing(30)
            except Exception as e:
                log(f"[stale] 异常(忽略): {e}")
            try:
                replenish_if_needed()
            except Exception as e:
                log(f"[watermark] 异常(忽略): {e}")
        finally:
            _init_lock.release()
    else:
        log("[main] 其他 worker 正在执行初始化，本轮跳过")

    # ── Step 1: 资源检查 ───────────────────────────────────────────────────────
    if not check_resources():
        log("[main] 资源超时仍不足，跳过本轮")
        return

    # ── Step 2: 获取当前可用 ref_code ──────────────────────────────────────────
    ref_master_id, ref_master_email, ref_code, ref_used = db_get_current_ref_code()
    if not ref_code:
        # FIX G: xjfjk 已 conversions=10/10 耗尽，不再硬编码兜底。
        # 无可用 ref_code 说明所有已知码均已用满，需要先为一个注册账号 POST /api/ref-codes 生成新码。
        log("[ref] ⚠ 无可用 ref_code（所有已知码已满或未生成），跳过本轮注册")
        time.sleep(60); return
    log(f"[ref] ref_code={ref_code} master={ref_master_email} used={ref_used}/{MAX_REF_SLOTS}")

    # -- Step 3: pick fresh outlook account (fallback to cfmail if none) ------
    row = db_get_fresh_account()
    if not row:
        log("[main] no outlook account -> trying cfmail pipeline")
        cf_result = run_cfmail_chain(ref_code)
        if cf_result["ok"]:
            log("[main] cfmail OK email=" + cf_result["email"] + " ssid_len=" + str(len(cf_result["ssid"])))
            _verify_ssid_api(cf_result["ssid"], 0)
            return
        log("[main] cfmail fail: " + cf_result["reason"] + " -> wait for outlook")
        _waited = 0
        while _waited < 120:
            time.sleep(30); _waited += 30
            if db_count_fresh() > 0:
                log("[main] fresh account ready after " + str(_waited) + "s, retry")
                return
        log("[main] wait timeout 120s, next round"); return

    account_id, email, password, refresh_token = row
    log(f"\n{'─'*60}")
    log(f"[main] 账号: {email}  id={account_id}  ref_code={ref_code}")
    db_lock_account(account_id)   # 二次保险（db_get_fresh_account 已原子锁定）
    _reg_account(account_id, email)  # 注册到 atexit 清理注册表
    reg_ts = time.time()             # 注册开始时刻，用于过滤旧验证邮件

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
    # Step 5b: 注册成功无 ssid -> 内联等待验证邮件（90s），避免立即入 verify_rescue 队列
    if not ssid and reg_result.get("ok"):
        log("[ssid] 注册成功无 ssid -> 内联等待验证邮件（90s）...")
        ssid = run_inline_verify(email, password, refresh_token, max_wait=90, after_ts=reg_ts,
                                 account_id=account_id)
        if ssid:
            log(f"[ssid] inline_verify ssid len={len(ssid)}")
        else:
            log("[ssid] inline_verify 超时 -> 交 verify_rescue 处理")
    elif not ssid:
        log("[ssid] DB \u4e5f\u65e0 ssid\uff0c\u5c1d\u8bd5 unitool_login.py \u5c3c\u5e95\u767b\u5f55...")
        if check_resources():
            ssid = run_login(email, password)
        else:
            log("[ssid] \u8d44\u6e90\u4e0d\u8db3\uff0c\u8df3\u8fc7\u767b\u5f55\u5c3c\u5e95")

    # Bug 2 fix: __AADSTS70000__ is truthy, must check BEFORE `if not ssid`
    # otherwise it gets saved to DB as a real SSID
    if ssid == "__AADSTS70000__":
        log("[main] ⛔ AADSTS70000 已处理，跳出（不进 verify_pending）")
        _done_account(account_id)
        return

    if not ssid:
        if ssid is None:
            # already marked abuse_mode+unitool_fail inside run_inline_verify
            log("[main] ⛔ None ssid 已处理，跳出")
        elif reg_result.get("ok"):
            log("[main] ⚠ email_sent OK but no ssid -> unitool_verify_pending")
            db_mark_fail(account_id, "verify_email_not_found")
        else:
            log(f"[main] ❌ three-fallback fail, no ssid")
            db_mark_fail(account_id, "no_ssid_after_3_fallbacks")
        return
    log(f"[main] ✅ ssid 获取成功 len={len(ssid)}")

    # ── Step 6: 保存完整 ssid（覆盖截断版本）──────────────────────────────────
    db_save_ssid_full(account_id, email, ssid)  # DB 全长保存（修复 80/200 字截断）
    persist_ssid(email, ssid)                   # /data/ + /tmp/ + proxy 热推

    # ── Step 6b: SSID API 真实验证 ─────────────────────────────────────────────
    # 用 billing-accounts 接口确认该 SSID 真的能调 API，才算真正成功注册
    log(f"[main] ▶ Step6b: SSID API 验证 (billing-accounts)...")
    if not _verify_ssid_api(ssid, account_id):
        log(f"[main] ❌ SSID 获取但 API 不可用 → token_invalid, 转 verify_rescue")
        try:
            _tc = db_connect(); _tcur = _tc.cursor()
            _tcur.execute(
                "UPDATE accounts SET tags = COALESCE(tags,'') || ',token_invalid' "
                "WHERE id=%s AND (tags IS NULL OR tags NOT LIKE '%%token_invalid%%')",
                (account_id,))
            _tc.commit(); _tc.close()
        except Exception as _te:
            log(f"[main] token_invalid tag err: {_te}")
        db_mark_fail(account_id, "ssid_api_unverified")
        return
    log(f"[main] ✅ Step6b SSID API 验证通过 → 继续建立 ref_code")

    _done_account(account_id)  # 告知 atexit 此账号成功，无需清理

    # ── Step 7: 为该账号生成专属 ref_code，并激活到 DB ─────────────────────────
    # FIX F: 先通过代理 POST /api/ref-codes 为新账号创建专属码，再 run_reflink 读取保存
    log(f"[main] ▶ Step7a: 通过代理为 {email} 创建专属 ref_code...")
    # FIX: 用 email hash 作 hint，保持与注册 IP 一致（而非无关的 account_id）
    _port_hint = hash(email)
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
        if created_code:
            # BUG FIX: QuarkIP/proxy created ref_code but run_reflink failed → save directly
            db_save_ref_code(account_id, created_code)
            log(f"[main] ref_code saved directly (reflink fallback): {created_code}")
            print(f"[CHAIN_OK] {email}|{ssid}...|{ref_code}|{created_code}(direct)", flush=True)
        else:
            log(f"[main] no ref_code: proxy=FAIL reflink=FAIL")
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
            # 立即更新 pool-cache：避免缓存期内 ref_code 被重复超量选用
            _pool_cache_increment_used(ref_code)
        except Exception as e:
            log(f"[ref] track referral err: {e}")

    # ── Step 7d: ref_master ref_code 用满 MAX_REF_SLOTS → 升入高余额池 ────────────────
    if ref_master_id:
        try:
            _hb_conn = db_connect(); _hb_cur = _hb_conn.cursor()
            _hb_cur.execute("SELECT notes, tags FROM accounts WHERE id=%s", (ref_master_id,))
            _hb_row = _hb_cur.fetchone()
            _hb_notes = (_hb_row[0] or "") if _hb_row else ""
            _hb_tags  = (_hb_row[1] or "") if _hb_row else ""
            _hb_count = len(re.findall(r"ref_registered=", _hb_notes))
            if _hb_count >= MAX_REF_SLOTS and "unitool_high_balance" not in _hb_tags:
                _new_hb_tags = (_hb_tags.rstrip(",") + ",unitool_high_balance").strip(",")
                _hb_cur.execute(
                    "UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s",
                    (_new_hb_tags, ref_master_id))
                _hb_conn.commit()
                log(f"[ref] 🎉 ref_master({ref_master_email}) "
                    f"ref×{_hb_count}/{MAX_REF_SLOTS} → unitool_high_balance 高余额池")
                try:
                    _hb_req_data = json.dumps({"email": ref_master_email}).encode()
                    _hb_req = urllib.request.Request(
                        f"http://localhost:{PROXY_PORT}/mark-high-balance",
                        data=_hb_req_data,
                        headers={"Content-Type": "application/json"})
                    urllib.request.urlopen(_hb_req, timeout=3)
                except Exception:
                    pass
            else:
                log(f"[ref] ref_master({ref_master_email}) ref_used={_hb_count}/{MAX_REF_SLOTS} "
                    f"high_balance={'yes' if 'unitool_high_balance' in _hb_tags else 'no'}")
            _hb_conn.close()
        except Exception as _hb_e:
            log(f"[ref] high_balance check err: {_hb_e}")

    log(f"\n{'='*60}")
    log(f"=== chain_v3 完成 email={email} ssid_len={len(ssid)} ref_new={new_ref_code} ===")


if __name__ == "__main__":
    import time as _loop_time

    # N_WORKERS: 进程内并发数，替代 PM2 多进程 w0/w1/w2
    # 使用: export N_WORKERS=3 → 单 PM2 进程内启 3 个并发 worker 线程
    N_WORKERS = int(os.environ.get("N_WORKERS", "1"))

    def _worker_loop(worker_idx: int):
        _thread_local.log_path = f"/tmp/unitool_chain_v3_w{worker_idx}.log"
        _delay = STARTUP_DELAY if worker_idx == 0 else worker_idx * 15
        if _delay > 0:
            print(f"[loop:w{worker_idx}] stagger {_delay}s", flush=True)
            _loop_time.sleep(_delay)
        _stats_ok = 0; _stats_fail = 0
        _stats_hour_ts = _loop_time.time()
        _proxy_filter_ts = _loop_time.time()
        while True:
            try:
                if worker_idx == 0:
                    _added = _rpool.reload_externals()
                    if _added > 0:
                        print(f"[loop:w0] hot-loaded {_added} external proxies", flush=True)
                    if _loop_time.time() - _proxy_filter_ts >= 3600:
                        print("[loop:w0] 每小时代理探活过滤...", flush=True)
                        try:
                            _before = len(_rpool._externals)
                            _rpool._externals = [p for p in _rpool._externals
                                if _rpool._probe_external(p)]
                            _rpool._save_externals_file()
                            print(f"[loop:w0] {_before}→{len(_rpool._externals)} 存活", flush=True)
                        except Exception as _fe:
                            print(f"[loop:w0] 过滤失败: {_fe}", flush=True)
                        _proxy_filter_ts = _loop_time.time()
                main()
                _stats_ok += 1  # Bug 3 fix: was never incremented
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as _loop_e:
                print(f"[loop:w{worker_idx}] uncaught: {_loop_e}", flush=True)
                _stats_fail += 1
                _loop_time.sleep(5)
            if _loop_time.time() - _stats_hour_ts >= 3600:
                _total = _stats_ok + _stats_fail
                _rate = f"{100*_stats_ok//_total}%" if _total else "N/A"
                print(f"[stats:w{worker_idx}] ok={_stats_ok} fail={_stats_fail} rate={_rate}", flush=True)
                _stats_ok = 0; _stats_fail = 0
                _stats_hour_ts = _loop_time.time()

    if N_WORKERS > 1:
        print(f"[main] 启动 {N_WORKERS} 并发 worker", flush=True)
        _threads = [threading.Thread(target=_worker_loop, args=(i,),
                    daemon=True, name=f"chain-w{i}") for i in range(N_WORKERS)]
        for _t in _threads: _t.start()
        try:
            for _t in _threads: _t.join()
        except (KeyboardInterrupt, SystemExit):
            pass
    else:
        _worker_loop(WORKER_ID)
