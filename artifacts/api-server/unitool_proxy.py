#!/usr/bin/env python3
"""
unitool.ai → OpenAI 兼容反代 v5.10
=====================================
改进 (相对 v5.9):
  v5.10 三大核心改造（来自 ds-free-api 分析 + unitool API 实测）：
  1. 主路径切换为 /api/widget/stream 真实 SSE 流
     — POST {"chat_id":X,"messages":[snapshot]} → data:{"content":"delta"}
     — 逐 token 推送，零延迟，无截断
  2. 轮询端点从 /api/chats/{id}/messages 改为 /api/chats/{id}/paginatedMessages
     — 响应 key 为 "data"（非 "messages"）
     — status 流：updating（生成中）→ ended（完成）
  3. 移除 active_stable 计数器
     — 以 status=="ended" 为唯一完成信号，彻底解决长输出截断问题
  其余改进保留 v5.2–v5.9 的全部功能。
"""
import json, time, uuid, threading, ssl, os, re, sys, subprocess
import psycopg2
import requests as _rq
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# ── 住宅代理配置（与 chain_v3 / register / login 保持一致）────────────────────
RESI_PORTS = [10851, 10853, 10854, 10857, 10859, 10870, 10872, 10878, 10879]

def _pick_resi_port(ssid: str) -> int:
    return RESI_PORTS[hash(ssid[:16] if ssid else "x") % len(RESI_PORTS)]

# 每个代理端口对应一个 requests.Session（连接复用 + socks5h 代理）
_resi_sessions: dict = {}
_resi_sess_lock = threading.Lock()

def _get_resi_session(port: int) -> _rq.Session:
    with _resi_sess_lock:
        if port not in _resi_sessions:
            sess = _rq.Session()
            sess.proxies = {
                "https": f"socks5h://127.0.0.1:{port}",
                "http":  f"socks5h://127.0.0.1:{port}",
            }
            _resi_sessions[port] = sess
        return _resi_sessions[port]

PORT     = int(os.environ.get("PORT", 8089))
BASE     = "https://unitool.ai"
SSID_DIR = "/data/unitool_ssids"   # 持久化目录（vdb1 上）
TMP_DIR  = "/tmp"                  # 兼容旧 /tmp/unitool_ssid*.txt
DB_URL   = "postgresql://postgres:postgres@localhost/toolkit"
LOGIN_SCRIPT = "/data/Toolkit/scripts/unitool_login.py"
BALANCE_CHECK_INTERVAL = 900   # 15 min
BALANCE_LOW_WARN = 0.5

os.makedirs(SSID_DIR, exist_ok=True)
ctx = ssl.create_default_context()
_lock = threading.Lock()

# ─── SSID 池 ────────────────────────────────────────────────────────────────
_pool: list = []
_pool_mtime: float = 0.0

def _read_ssid_file(path: str) -> str:
    try:
        v = open(path).read().strip()
        return v if len(v) > 50 else ""
    except Exception:
        return ""

def _scan_files() -> list[tuple[str, str]]:
    """扫描 /data/unitool_ssids/ 和 /tmp/unitool_ssid*.txt，返回 (label, ssid) 列表，去重"""
    found: dict[str, str] = {}  # ssid → label
    # 1. /data/unitool_ssids/*.txt (label = 文件名 stem)
    try:
        for fn in sorted(os.listdir(SSID_DIR)):
            if fn.endswith(".txt"):
                ssid = _read_ssid_file(os.path.join(SSID_DIR, fn))
                if ssid and ssid not in found:
                    found[ssid] = fn[:-4]   # strip .txt
    except Exception:
        pass
    # 2. /tmp/unitool_ssid*.txt (兼容旧格式)
    pat = re.compile(r"^unitool_ssid\d*\.txt$")
    try:
        for fn in sorted(os.listdir(TMP_DIR)):
            if pat.match(fn):
                ssid = _read_ssid_file(os.path.join(TMP_DIR, fn))
                if ssid and ssid not in found:
                    found[ssid] = fn[:-4]
    except Exception:
        pass
    return [(label, ssid) for ssid, label in found.items()]

def _load_from_db() -> list[tuple[str, str]]:
    """从 unitool_ssids 表加载 (label/email, ssid)"""
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("""
            SELECT COALESCE(source_email, 'db_' || id::text), ssid
            FROM unitool_ssids
            WHERE is_valid = true AND ssid IS NOT NULL AND LENGTH(ssid) > 50
            ORDER BY collected_at DESC
        """)
        rows = [(r[0], r[1]) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"[DB] load error: {e}", flush=True)
        return []

def _save_to_db(label: str, ssid: str):
    """把新 ssid 写入 unitool_ssids 表"""
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        # 先看是否有同 label 的旧记录
        cur.execute("SELECT id FROM unitool_ssids WHERE source_email=%s LIMIT 1", (label,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE unitool_ssids SET ssid=%s, is_valid=true, collected_at=NOW() WHERE id=%s",
                        (ssid, row[0]))
        else:
            cur.execute("""
                INSERT INTO unitool_ssids (source_email, ssid, is_valid, collected_at)
                VALUES (%s, %s, true, NOW())
            """, (label, ssid))
        conn.commit()
        conn.close()
        print(f"[DB] saved ssid for {label}", flush=True)
    except Exception as e:
        print(f"[DB] save error: {e}", flush=True)

def _invalidate_in_db(label: str):
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("UPDATE unitool_ssids SET is_valid=false WHERE source_email=%s", (label,))
        conn.commit()
        conn.close()
    except Exception:
        pass

def _save_to_file(label: str, ssid: str):
    """持久化到 /data/unitool_ssids/<label>.txt"""
    safe = re.sub(r"[^a-zA-Z0-9@._-]", "_", label)
    path = os.path.join(SSID_DIR, f"{safe}.txt")
    try:
        open(path, "w").write(ssid)
    except Exception as e:
        print(f"[FILE] save error: {e}", flush=True)

def _rebuild_pool():
    global _pool, _pool_mtime
    _pool_mtime = time.time()

    # 合并来源：文件 + DB
    sources: dict[str, str] = {}   # ssid → label
    for (label, ssid) in _scan_files():
        if ssid not in sources:
            sources[ssid] = label
    for (label, ssid) in _load_from_db():
        if ssid not in sources:
            sources[ssid] = label

    with _lock:
        existing = {e["ssid"]: e for e in _pool}
        new_pool = []
        seen = set()
        for ssid, label in sources.items():
            if ssid in seen:
                continue
            seen.add(ssid)
            if ssid in existing:
                new_pool.append(existing[ssid])
            else:
                new_pool.append(_make_entry(ssid, label))
                print(f"[POOL] loaded {label} ssid={ssid[:16]}...", flush=True)
        # 保留 dead 但还在冷却的旧条目（等恢复或重登）
        for e in _pool:
            if e["ssid"] not in seen and e["dead_until"] > time.time():
                new_pool.append(e)
        _pool = new_pool

def _reload_pool_if_needed():
    if time.time() - _pool_mtime < 5:
        return
    _rebuild_pool()

MAX_CONCURRENCY_PER_SSID = 2   # 每个 ssid 允许同时最多并发请求数

def _label_to_email(label: str) -> str:
    """将文件名 label 还原为 email (kmitchellnvh__outlook_com -> kmitchellnvh@outlook.com)"""
    if "@" in label:
        return label
    m = re.match(r"^(.+?)__(.+)$", label)
    if m:
        return "{}@{}".format(m.group(1), m.group(2).replace("_", "."))
    return label

def _make_entry(ssid: str, label: str, email: str = "") -> dict:
    return {"ssid": ssid, "label": label,
            "_email": email or _label_to_email(label),
            "dead_until": 0, "dead_reason": "",
            "balance": None, "_balance_ts": 0,
            "_relogin_pending": False,
            "_active": 0}

def _mark_dead(ssid: str, secs: int = 600, reason: str = ""):
    with _lock:
        for e in _pool:
            if e["ssid"] == ssid:
                e["dead_until"] = time.time() + secs
                e["dead_reason"] = reason
                print(f"[POOL] dead {e['label']} {secs}s reason={reason!r}", flush=True)
                # 如果是 auth 错误，触发后台重登
                if "auth" in reason and not e.get("_relogin_pending"):
                    e["_relogin_pending"] = True
                    t = threading.Thread(target=_bg_relogin, args=(e["label"],), daemon=True)
                    t.start()
                elif "balance" in reason:
                    _invalidate_in_db(e["label"])
                break

# ─── 自动重登 ────────────────────────────────────────────────────────────────
def _get_password_for_label(label: str) -> str:
    """从 DB 查密码；同时尝试 label 原值和还原的 email 格式"""
    candidates = list(dict.fromkeys([label, _label_to_email(label)]))
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        for cand in candidates:
            cur.execute("SELECT password FROM accounts WHERE email=%s LIMIT 1", (cand,))
            row = cur.fetchone()
            if row:
                conn.close()
                return row[0]
        conn.close()
        return ""
    except Exception:
        return ""

def _bg_relogin(label: str):
    """后台线程：用 unitool_login.py 重新登录，成功后推入 pool"""
    email = _label_to_email(label)   # 还原 email（文件名 label 含下划线编码）
    print(f"[RELOGIN] starting for {email} (label={label})", flush=True)
    pw = _get_password_for_label(label)
    if not pw:
        print(f"[RELOGIN] no password found for {email}/{label}, skip", flush=True)
        return
    try:
        result = subprocess.run(
            ["python3", LOGIN_SCRIPT, "--email", email, "--password", pw, "--no-headless"],
            capture_output=True, text=True, timeout=180,
            env={**os.environ, "DISPLAY": ":99", "PYTHONUNBUFFERED": "1"}
        )
        for line in result.stdout.split("\n"):
            if line.startswith("[OK]"):
                parts = line.split("|")
                if len(parts) >= 3:
                    new_ssid = parts[2].strip()
                    print(f"[RELOGIN] ✅ {label} new ssid={new_ssid[:16]}...", flush=True)
                    _add_ssid_to_pool(label, new_ssid)
                    return
        print(f"[RELOGIN] ❌ {label} no OK line found", flush=True)
        if result.stderr:
            print(f"[RELOGIN] stderr: {result.stderr[-200:]}", flush=True)
    except Exception as ex:
        print(f"[RELOGIN] error: {ex}", flush=True)
    finally:
        with _lock:
            for e in _pool:
                if e["label"] == label:
                    e["_relogin_pending"] = False
                    break

def _add_ssid_to_pool(label: str, ssid: str):
    """将 ssid 加入/更新 pool，并持久化到文件+DB"""
    _save_to_file(label, ssid)
    _save_to_db(label, ssid)
    with _lock:
        same_label = next((e for e in _pool if e["label"] == label), None)
        same_ssid  = next((e for e in _pool if e["ssid"] == ssid), None)
        if same_label:
            same_label["ssid"] = ssid
            same_label["dead_until"] = 0
            same_label["dead_reason"] = ""
            same_label["_relogin_pending"] = False
            print(f"[POOL] updated {label} ssid={ssid[:16]}...", flush=True)
        elif not same_ssid:
            _pool.append(_make_entry(ssid, label))
            print(f"[POOL] added {label} ssid={ssid[:16]}...", flush=True)

# ─── 余额监控 ────────────────────────────────────────────────────────────────
def _hdrs(ssid: str) -> dict:
    return {
        "Cookie":       f"__Secure-unitool-ssid={ssid}",
        "Content-Type": "application/json",
        "User-Agent":   "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/136",
        "Origin":       "https://unitool.ai",
        "Referer":      "https://unitool.ai/en/chatgpt",
        "Accept":       "application/json",
    }

def _check_balance(ssid: str) -> float | None:
    """v5.6: balance 检查也走住宅代理"""
    try:
        port = _pick_resi_port(ssid)
        sess = _get_resi_session(port)
        resp = sess.get(f"{BASE}/api/user/billing-accounts",
                        headers=_hdrs(ssid), timeout=10, verify=True)
        if resp.status_code != 200:
            return None
        d = resp.json()
        return sum(float(a.get("value", 0)) for a in d.get("accounts", []))
    except Exception:
        return None

def _check_session_valid(ssid: str) -> bool:
    """v5.6: 主动探 /api/auth/session 检测 ssid 是否仍有效"""
    try:
        port = _pick_resi_port(ssid)
        sess = _get_resi_session(port)
        resp = sess.get(f"{BASE}/api/auth/session",
                        headers=_hdrs(ssid), timeout=8, verify=True)
        if resp.status_code != 200:
            return False
        d = resp.json()
        # NextAuth 格式：{"auth": {"user": {...}}} 或 NextAuth v4: {"user": {...}}
        user = (d.get("auth") or {}).get("user") or d.get("user")
        return bool(user and user.get("id"))
    except Exception:
        return False

def _balance_monitor_loop():
    time.sleep(120)
    while True:
        try:
            with _lock:
                entries = list(_pool)
            now = time.time()
            for e in entries:
                # 跳过 dead 条目（ssid 过期，检查无意义）
                if e["dead_until"] > now:
                    continue
                if now - e.get("_balance_ts", 0) < BALANCE_CHECK_INTERVAL:
                    continue
                # v5.6: 先做 session 有效性检查，再做 balance 检查
                lbl = e["label"]
                sess_ok = _check_session_valid(e["ssid"])
                if not sess_ok:
                    print(f"[SES] ⚠ {lbl}: session invalid (ssid expired)", flush=True)
                    _mark_dead(e["ssid"], secs=300, reason="auth_error")
                    e["_balance_ts"] = now  # 避免反复检查
                    time.sleep(2)
                    continue
                bal = _check_balance(e["ssid"])
                e["_balance_ts"] = now
                e["balance"] = bal
                if bal is None:
                    print(f"[BAL] {lbl}: check failed", flush=True)
                elif bal <= 0:
                    print(f"[BAL] ⚠ {lbl}: balance={bal:.3f} EXHAUSTED", flush=True)
                elif bal < BALANCE_LOW_WARN:
                    print(f"[BAL] ⚠ {lbl}: balance={bal:.3f} LOW", flush=True)
                else:
                    print(f"[BAL] ✓ {lbl}: balance={bal:.3f}", flush=True)
                time.sleep(2)
        except Exception as ex:
            print(f"[BAL] monitor error: {ex}", flush=True)
        time.sleep(60)

# ─── 服务/模型映射 ────────────────────────────────────────────────────────────
NATIVE_SERVICES = {
    # ChatGPT sub-services (from /api/services?parent_id=chatgpt)
    "gpt-5", "gpt-5.5", "gpt-5.4", "gpt-5-nano",
    "gpt5.1", "gpt5.2",
    "gpt-4o", "gpt-4o-mini", "gpt-4-1", "gpt-4-5",
    "gpt-o1", "gpt-o1-mini", "gpt-o3", "gpt-o3-mini", "gpt-o3-pro", "gpt-o4-mini",
    # Gemini sub-services
    "gemini-3.1-pro", "gemini-3-pro",
    # xAI
    "grok",
    # Claude sub-services (from /api/services?parent_id=claude)
    "claude-sonnet", "claude-sonnet-4-5", "claude-sonnet-4-6",
    "claude-opus", "claude-opus-4-6", "claude-haiku",
}

# [v5.4] 需要 reasoning_effort 的服务：chat 创建用 /api/chats + chat_settings, 消息带 options
# [v5.7] o-series 也属于 reasoning 服务（需要 reasoning_effort options）
REASONING_SERVICES = {"gemini-3.1-pro", "gemini-3-pro", "grok", "gpt-o1", "gpt-o1-mini", "gpt-o3", "gpt-o3-mini", "gpt-o3-pro", "gpt-o4-mini"}

FALLBACK_CHAINS: dict[str, list[str]] = {
    "gpt-5":            ["gpt-5.5",   "gpt-5.4",  "gpt-4-1",  "gpt-4o-mini"],
    "gpt-5.5":          ["gpt-5",     "gpt-5.4",  "gpt-4-1",  "gpt-4o-mini"],
    "gpt-5.4":          ["gpt-5.5",   "gpt-5",    "gpt-4-1",  "gpt-4o-mini"],
    "gpt5.1":           ["gpt5.2",    "gpt-5",    "gpt-5.5",  "gpt-4-1"],
    "gpt5.2":           ["gpt5.1",    "gpt-5",    "gpt-5.5",  "gpt-4-1"],
    "gpt-4-1":          ["gpt-5.4",   "gpt-5",    "gpt-4o-mini"],
    "gpt-4o-mini":      ["gpt-4-1",   "gpt-5.4",  "gpt-5"],
    "claude-opus-4-6":  ["claude-opus", "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-sonnet"],
    "claude-opus":      ["claude-opus-4-6", "claude-sonnet-4-6", "claude-sonnet"],
    "claude-haiku":     ["claude-sonnet", "claude-sonnet-4-5"],
    "claude-sonnet-4-6":["claude-sonnet-4-5", "claude-sonnet",     "claude-opus-4-6"],
    "claude-sonnet-4-5":["claude-sonnet-4-6", "claude-sonnet",     "claude-opus-4-6"],
    "claude-sonnet":    ["claude-sonnet-4-5", "claude-sonnet-4-6", "claude-opus-4-6"],
    # [v5.3] gpt-5-nano: "Reasoning is mandatory" → 降级 gpt-5
    "gpt-5-nano":       ["gpt-5",        "gpt-5.5",  "gpt-4-1",  "gpt-4o-mini"],
    # [v5.4] gemini-3.1-pro / grok: 原生修复，用户明确禁止降级 → 无 fallback chain
}

MODEL_ALIASES = {
    "gpt-4": "gpt-4-1", "gpt-4-turbo": "gpt-4-1", "gpt-4-turbo-preview": "gpt-4-1",
    "gpt-4.1": "gpt-4-1", "gpt-4o": "gpt-4o", "gpt-4o-search": "gpt-4o",
    "gpt-4.5": "gpt-4-1", "gpt-4o-2024-11-20": "gpt-4-1",
    "gpt-4o-mini-2024-07-18": "gpt-4o-mini", "gpt-4o-mini-search": "gpt-4o-mini",
    "gpt-3.5-turbo": "gpt-4o-mini", "gpt-3.5-turbo-0613": "gpt-4o-mini",
    "gpt-3.5-turbo-16k": "gpt-4o-mini", "text-davinci-003": "gpt-4o-mini",
    "o1": "gpt-o1", "o1-mini": "gpt-o1-mini", "o1-pro": "gpt-o1",
    "o1-preview": "gpt-o1", "o3": "gpt-o3", "o3-mini": "gpt-o3-mini",
    "o4-mini": "gpt-o4-mini", "o4": "gpt-o3-pro",
    "gpt-5-turbo": "gpt-5", "chatgpt-5": "gpt-5", "chatgpt-5-turbo": "gpt-5",
    "chatgpt-5.5": "gpt-5.5", "chatgpt-5.5-turbo": "gpt-5.5", "chatgpt": "gpt-5.5",
    "claude-opus": "claude-opus", "claude-opus-4": "claude-opus",
    "claude-opus-4-5": "claude-opus-4-6", "claude-opus-4.5": "claude-opus-4-6",
    "claude-opus-4.6": "claude-opus-4-6", "claude-opus-4-latest": "claude-opus-4-6",
    "claude-opus-latest": "claude-opus-4-6",
    "claude-3-opus": "claude-sonnet", "claude-3-opus-20240229": "claude-sonnet",
    "claude": "claude-sonnet", "claude-sonnet-4": "claude-sonnet",
    "claude-3-7-sonnet": "claude-sonnet", "claude-3-7-sonnet-20250219": "claude-sonnet",
    "claude-3-5-sonnet": "claude-sonnet", "claude-3-5-sonnet-20241022": "claude-sonnet",
    "claude-3-5-haiku": "claude-haiku", "claude-3-5-haiku-20241022": "claude-haiku",
    "claude-haiku-3-5": "claude-haiku",
    "claude-3-haiku": "claude-haiku", "claude-3-haiku-20240307": "claude-haiku",
    "claude-3-sonnet": "claude-sonnet", "claude-3-sonnet-20240229": "claude-sonnet",
    "claude-haiku": "claude-sonnet", "claude-3-5-sonnet-latest": "claude-sonnet",
    "claude-3-7-sonnet-latest": "claude-sonnet", "claude-sonnet-latest": "claude-sonnet",
    # [v5.4] gemini: 所有别名 → gemini-3.1-pro（原生支持）
    "gemini": "gemini-3.1-pro", "gemini-pro": "gemini-3.1-pro",
    "gemini-3": "gemini-3-pro", "gemini-3-flash": "gemini-3-pro",
    "gemini-flash": "gemini-3.1-pro", "gemini-ultra": "gemini-3.1-pro",
    "gemini-2.5-pro": "gemini-3.1-pro", "gemini-2.5-flash": "gemini-3.1-pro",
    "gemini-2.0-pro": "gemini-3.1-pro", "gemini-2.0-flash": "gemini-3.1-pro",
    "gemini-1.5-pro": "gemini-3.1-pro", "gemini-1.5-flash": "gemini-3.1-pro",
    # [v5.4] grok: 原生支持，无降级
    "grok-3": "grok", "grok-3-fast": "grok", "grok-3-mini": "grok",
    "grok-2": "grok", "grok-beta": "grok",
    "deepseek": "gpt-5.5", "deepseek-r1": "gpt-5.5", "deepseek-v3": "gpt-5.5",
    "deepseek-chat": "gpt-5.5",
}

def _resolve_model(model: str) -> str:
    if model in NATIVE_SERVICES:  return model
    if model in MODEL_ALIASES:    return MODEL_ALIASES[model]
    m = model.lower()
    if "claude" in m:   return "claude-sonnet"
    if "gemini" in m:   return "gemini-3.1-pro"
    if "grok" in m:     return "grok"
    if "deepseek" in m: return "gpt-5.5"
    return "gpt-5.5"

ALL_MODELS   = sorted(NATIVE_SERVICES | set(MODEL_ALIASES.keys()))
MODELS_LIST  = [{"id": m, "object": "model", "created": 1700000000, "owned_by": "unitool"}
                for m in ALL_MODELS]

# ─── 核心 API 调用 ───────────────────────────────────────────────────────────
def _api(method: str, path: str, body=None, ssid: str = ""):
    """v5.7: 直连 unitool.ai (保持原 urllib 路径；socks5h 仅用于 balance/session 健康检查)"""
    data = json.dumps(body).encode() if body is not None else None
    req  = Request(f"{BASE}{path}", data=data, headers=_hdrs(ssid), method=method)
    with urlopen(req, context=ctx, timeout=35) as r:
        return json.loads(r.read())

def _fmt(messages: list) -> str:
    parts = []
    for m in messages:
        role    = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content
                               if isinstance(c, dict) and c.get("type") == "text")
        if role == "system":
            parts.append(f"[System: {content}]")
        elif role == "assistant":
            parts.append(f"[Assistant: {content}]")
        else:
            parts.append(content)
    return "\n\n".join(parts)

def _is_retryable(msg: str) -> bool:
    KEYS = ["No content returned","Reasoning is mandatory","max_tokens",
            "context_length_exceeded","overloaded","rate_limit","capacity",
            "unavailable","timeout","upstream","Bad gateway","Service Unavailable",
            # [v5.3] grok 子型号: unitool 明确返回不支持
            "Unsupported service",
            # [v5.4] gemini 后端临时维护
            "currently being maintained"]
    ml = msg.lower()
    return any(k.lower() in ml for k in KEYS)

_rr_lock = threading.Lock()
_rr_idx  = 0
def _pick_entry(entries: list) -> dict:
    global _rr_idx
    with _rr_lock:
        idx = _rr_idx % len(entries)
        _rr_idx += 1
        return entries[idx]


# ─── v5.10: paginatedMessages 辅助 ──────────────────────────────────────────
def _api_paginated(chat_id: int, ssid: str, limit: int = 20) -> list:
    """GET /api/chats/{id}/paginatedMessages via socks5h → data[] list"""
    port = _pick_resi_port(ssid)
    sess = _get_resi_session(port)
    try:
        r = sess.get(
            f"{BASE}/api/chats/{chat_id}/paginatedMessages",
            params={"limit": limit},
            headers=_hdrs(ssid),
            timeout=15,
        )
        return r.json().get("data", [])
    except Exception as e:
        print(f"[paginatedMessages] chat={chat_id} error: {e}", flush=True)
        return []


# ─── v5.10: widget/stream 真实 SSE 主路径 ───────────────────────────────────
def _widget_stream_sse(chat_id: int, msgs_snapshot: list, ssid: str,
                       chunk_cb, deadline: float) -> str:
    """
    POST /api/widget/stream → SSE 逐 token 流。
    body: {"chat_id": X, "messages": [snapshot from paginatedMessages]}
    events: data: {"content":"delta"}
    Returns: 完整拼接内容（空字符串表示无数据，调用方需回退轮询）。
    """
    port    = _pick_resi_port(ssid)
    sess    = _get_resi_session(port)
    timeout = max(8.0, deadline - time.time())
    hdrs    = {
        **_hdrs(ssid),
        "Content-Type":  "application/json",
        "Accept":        "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection":    "keep-alive",
    }
    full = ""
    with sess.post(
        f"{BASE}/api/widget/stream",
        json={"chat_id": chat_id, "messages": msgs_snapshot},
        headers=hdrs,
        stream=True,
        timeout=(10, timeout),
    ) as r:
        if r.status_code != 200:
            raise Exception(f"widget/stream HTTP {r.status_code}: {r.text[:200]}")
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            if time.time() >= deadline:
                break
            if line.startswith("data:"):
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    break
                try:
                    delta = json.loads(raw).get("content", "")
                    if delta:
                        full += delta
                        if chunk_cb:
                            chunk_cb(delta)
                except Exception:
                    pass
    return full


# ─── v5.10: paginatedMessages 轮询兜底 ──────────────────────────────────────
def _paginated_poll(chat_id: int, user_msg_id: int, ssid: str,
                    chunk_cb, deadline: float, prev_content: str = "") -> str:
    """
    paginatedMessages 轮询兜底（widget/stream 失败时使用）。
    每 0.7s 轮询一次；唯一完成信号：status == "ended"。
    无 active_stable 计数器，不会截断长输出。
    """
    while time.time() < deadline:
        time.sleep(0.7)
        msgs = _api_paginated(chat_id, ssid)
        for m in msgs:
            if m.get("role") != "assistant" or m.get("reply_to") != user_msg_id:
                continue
            status  = m.get("status", "")
            cur_txt = m.get("content") or ""
            if chunk_cb and cur_txt and cur_txt != prev_content:
                delta = cur_txt[len(prev_content):]
                if delta:
                    chunk_cb(delta)
                prev_content = cur_txt
            if status == "error":
                raise Exception(f"service_error: {cur_txt[:300]}")
            if status == "ended" and cur_txt:
                return cur_txt
            break   # 匹配到消息后退出 for，等下一轮
    raise TimeoutError(f"timeout chat={chat_id}")


def _send_and_collect(entry: dict, service_id: str, content: str,
                      chunk_cb=None, timeout: int = 180) -> str:
    """并发计数 wrapper → _send_and_collect_core"""
    with _lock:
        entry["_active"] = entry.get("_active", 0) + 1
    try:
        return _send_and_collect_core(entry, service_id, content, chunk_cb, timeout)
    finally:
        with _lock:
            entry["_active"] = max(0, entry.get("_active", 0) - 1)


def _send_and_collect_core(entry: dict, service_id: str, content: str,
                           chunk_cb=None, timeout: int = 180) -> str:
    """
    v5.10: 创建新 chat → 发送消息 →
      主路径: POST /api/widget/stream SSE（逐 token 推送）
      兜底:   GET  /api/chats/{id}/paginatedMessages 轮询（status=ended）
    移除了 active_stable 计数器。
    """
    # 1. 创建新 chat（每请求独立，避免上下文污染）
    if service_id in REASONING_SERVICES:
        _rsettings = '{"reasoning_effort":"high","thinking":true}'
        chat = _api("POST", "/api/chats",
                    body={"service_id": service_id, "title": "",
                          "chat_settings": _rsettings},
                    ssid=entry["ssid"])
    else:
        chat = _api("POST", "/api/chats",
                    body={"service_id": service_id, "title": ""}, ssid=entry["ssid"])
    chat_id = chat.get("id")
    if not chat_id:
        raise Exception(f"cannot create chat: {chat}")

    # 2. 发送用户消息
    _msg_opts = '{"reasoning_effort":"high"}' if service_id in REASONING_SERVICES else ""
    result = _api("POST", f"/api/chats/{chat_id}/messages",
                  body={"content": content, "attachments": [], "options": _msg_opts},
                  ssid=entry["ssid"])
    # [v5.9] 检测 "Unsupported service"（账号无 regular 余额）
    if result.get("error"):
        err_msg = result["error"]
        if "Unsupported service" in err_msg or "Unknown" in err_msg:
            raise Exception(f"unsupported_service: {err_msg}")
        raise Exception(f"msg_send_error: {err_msg}")
    user_msg_id = result.get("message", {}).get("id")
    if not user_msg_id:
        raise Exception(f"no message id: {result}")

    deadline = time.time() + timeout

    # 3. 取消息快照（0.3s 后 user msg 可见），供 widget/stream body 使用
    time.sleep(0.3)
    msgs_snapshot = _api_paginated(chat_id, entry["ssid"], limit=5)

    # 4. 主路径：widget/stream 真实 SSE 流
    try:
        text = _widget_stream_sse(chat_id, msgs_snapshot, entry["ssid"], chunk_cb, deadline)
        if text:
            print(f"[v5.10] widget/stream ok chat={chat_id} len={len(text)}", flush=True)
            return text
        print(f"[v5.10] widget/stream empty → fallback poll chat={chat_id}", flush=True)
    except Exception as e:
        print(f"[v5.10] widget/stream error → fallback: {e}", flush=True)

    # 5. 兜底：paginatedMessages 轮询（status=ended 为唯一完成信号）
    return _paginated_poll(chat_id, user_msg_id, entry["ssid"], chunk_cb, deadline)

def _try_service(service_id: str, content: str, entries: list,
                 chunk_cb=None) -> str:
    last_err = None
    max_attempts = max(len(entries) * 2, 4)
    for attempt in range(max_attempts):
        # 动态过滤: 跳过 dead + 并发已满的 ssid
        now = time.time()
        live_now = [
            e for e in entries
            if e["dead_until"] <= now
            and e.get("_active", 0) < MAX_CONCURRENCY_PER_SSID
        ]
        if not live_now:
            # 全部繁忙/死亡：先取 live 忽略并发上限，再兜底全量
            live_now = [e for e in entries if e["dead_until"] <= now] or entries
        entry = _pick_entry(live_now)
        try:
            return _send_and_collect(entry, service_id, content, chunk_cb=chunk_cb)
        except HTTPError as e:
            body_txt = ""
            try: body_txt = e.read().decode(errors="ignore")
            except Exception: pass
            if e.code in (401, 403, 423):
                if "Free tokens are over" in body_txt or "Balance need" in body_txt:
                    _mark_dead(entry["ssid"], secs=86400, reason="balance_exhausted")
                else:
                    _mark_dead(entry["ssid"], secs=600,   reason="auth_error")
                last_err = e
                continue
            raise
        except TimeoutError as e:
            _mark_dead(entry["ssid"], secs=120, reason="timeout")
            last_err = e
            continue
        except Exception as e:
            err = str(e)
            if "unsupported_service" in err:
                # [v5.9] 账号无 regular 余额 → 标记 balance_exhausted (24h)
                _mark_dead(entry["ssid"], secs=86400, reason="balance_exhausted")
            last_err = e
            continue
    raise Exception(f"all ssids failed for {service_id}: {last_err}")

def _do_chat(model: str, messages: list, ssid_override: str | None,
             chunk_cb=None) -> str:
    primary_id = _resolve_model(model)
    content    = _fmt(messages)
    print(f"[REQ] {model}→{primary_id} msgs={len(messages)}", flush=True)

    if ssid_override and len(ssid_override) > 50:
        entries = [_make_entry(ssid_override, "header")]
    else:
        _reload_pool_if_needed()
        with _lock:
            live    = [e for e in _pool if e["dead_until"] <= time.time()]
            entries = live or _pool[:]

    if not entries:
        raise Exception("ssid pool empty — please login first")

    chain    = [primary_id] + [s for s in FALLBACK_CHAINS.get(primary_id, []) if s != primary_id]
    last_err = None
    for idx, service_id in enumerate(chain):
        if idx > 0:
            print(f"[FALLBACK] {chain[idx-1]} → {service_id}", flush=True)
        try:
            return _try_service(service_id, content, entries, chunk_cb=chunk_cb)
        except TimeoutError as e:
            print(f"[FALLBACK] {service_id} timeout", flush=True); last_err = e; continue
        except Exception as e:
            err = str(e)
            if "service_error:" in err:
                detail = err.split("service_error:", 1)[-1].strip()
                if _is_retryable(detail):
                    print(f"[FALLBACK] {service_id} retryable: {detail[:60]}", flush=True)
                    last_err = e; continue
                raise
            if "500" in err and idx < len(chain) - 1:
                print(f"[FALLBACK] {service_id} HTTP 500", flush=True)
                last_err = e; continue
            raise

    raise Exception(f"all services failed ({chain}): {last_err}")

# ─── HTTP Handler ─────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[HTTP] {self.address_string()} {fmt % args}", flush=True)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors(); self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def do_GET(self):
        p = self.path.split("?")[0]

        if p in ("/v1/models", "/v1/models/"):
            return self._json(200, {"object": "list", "data": MODELS_LIST})

        if p == "/healthz":
            self.send_response(200); self.end_headers()
            self.wfile.write(b"ok"); return

        if p == "/pool-status":
            _reload_pool_if_needed()
            now = time.time()
            with _lock:
                info = [{
                    "label":       e["label"],
                    "ssid_prefix": e["ssid"][:20] + "...",
                    "dead":        e["dead_until"] > now,
                    "dead_until":  e["dead_until"],
                    "dead_reason": e.get("dead_reason", ""),
                    "balance":     e.get("balance"),
                    "relogin":     e.get("_relogin_pending", False),
                    "active":      e.get("_active", 0),
                } for e in _pool]
            live = sum(1 for a in info if not a["dead"])
            return self._json(200, {"pool_size": len(_pool), "live": live, "accounts": info})

        if p == "/reload-ssids":
            _rebuild_pool()
            return self._json(200, {"ok": True, "pool_size": len(_pool)})

        if p == "/ssid-status":
            _reload_pool_if_needed()
            with _lock:
                live = [e for e in _pool if e["dead_until"] <= time.time()]
                top  = _pool[0] if _pool else None
            return self._json(200, {
                "pool_size": len(_pool), "live": len(live),
                "ssid_prefix": (top["ssid"][:40] + "...") if top else "",
                "ssid_len":    len(top["ssid"]) if top else 0,
            })

        self.send_response(404); self.end_headers()

    def do_POST(self):
        p = self.path.split("?")[0]

        # ── /add-ssid ────────────────────────────────────────────────────────
        if p == "/add-ssid":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body   = json.loads(self.rfile.read(length))
                ssid   = body.get("ssid", "").strip()
                label  = body.get("label", "api")
                if len(ssid) < 50:
                    return self._json(400, {"error": "ssid too short"})
                _add_ssid_to_pool(label, ssid)
                return self._json(200, {"ok": True, "pool_size": len(_pool)})
            except Exception as e:
                return self._json(500, {"error": str(e)})

        if p not in ("/v1/chat/completions", "/v1/chat/completions/"):
            self.send_response(404); self.end_headers(); return

        # ── /v1/chat/completions ──────────────────────────────────────────────
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
        except Exception as e:
            return self._json(400, {"error": {"message": str(e), "type": "parse_error"}})

        model     = body.get("model", "gpt-5.5")
        messages  = body.get("messages", [])
        do_stream = body.get("stream", False)
        auth      = self.headers.get("Authorization", "")
        ssid_ov   = auth[7:] if auth.startswith("Bearer ") and len(auth) > 60 else None

        resp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        ts      = int(time.time())

        if do_stream:
            # 真实 SSE 流式：通过 chunk_cb 增量推送
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self._cors(); self.end_headers()

            def send_chunk(delta: str):
                chunk = {
                    "id": resp_id, "object": "chat.completion.chunk",
                    "created": ts, "model": model,
                    "choices": [{"index": 0,
                                 "delta": {"content": delta},
                                 "finish_reason": None}],
                }
                try:
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            try:
                text = _do_chat(model, messages, ssid_ov, chunk_cb=send_chunk)
            except BrokenPipeError:
                return
            except Exception as e:
                print(f"[ERR] {e}", flush=True)
                err_chunk = {"error": {"message": str(e), "type": "proxy_error"}}
                try:
                    self.wfile.write(f"data: {json.dumps(err_chunk)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
                except Exception:
                    pass
                return

            stop = {
                "id": resp_id, "object": "chat.completion.chunk",
                "created": ts, "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            try:
                self.wfile.write(f"data: {json.dumps(stop)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
            except Exception:
                pass

        else:
            # 非流式：等待完整回复
            try:
                text = _do_chat(model, messages, ssid_ov)
            except BrokenPipeError:
                return
            except Exception as e:
                print(f"[ERR] {e}", flush=True)
                return self._json(500, {"error": {"message": str(e), "type": "proxy_error"}})

            pt = len(" ".join(m.get("content","") for m in messages
                              if isinstance(m.get("content"), str)).split())
            ct = len(text.split())
            self._json(200, {
                "id": resp_id, "object": "chat.completion",
                "created": ts, "model": model,
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                          "total_tokens": pt + ct},
            })


class ThreadedServer(HTTPServer):
    def process_request(self, req, addr):
        t = threading.Thread(target=self.finish_request, args=(req, addr), daemon=True)
        t.start()


if __name__ == "__main__":
    print(f"[unitool-proxy v5.10] loading ssids...", flush=True)
    _rebuild_pool()
    print(f"[unitool-proxy v5.10] port={PORT} pool={len(_pool)} models={len(ALL_MODELS)}", flush=True)
    with _lock:
        for e in _pool:
            print(f"  pool: {e['label']} ssid={e['ssid'][:20]}...", flush=True)

    threading.Thread(target=_balance_monitor_loop, daemon=True).start()
    print("[unitool-proxy v5.10] balance monitor started | widget/stream=primary paginatedMessages=fallback", flush=True)

    server = ThreadedServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
