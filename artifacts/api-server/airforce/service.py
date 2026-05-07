"""
AirForce API 自动注册服务
集成到 Toolkit PM2 生态，暴露 FastAPI 端点
端口: 8084 (可通过 AIRFORCE_PORT 环境变量覆盖)

2026-05-02 重构: 改用全浏览器注册方式
  - 填写4个字段 (username/email/password/confirmPassword)
  - Turnstile shadow bypass (10s wait + click checkbox)
  - 浏览器内 /api/me 提取 API key
  - 代理端口轮转 (10854/10857/10820/...)
"""
import sys
import os
import random
import string
import threading
import time
from typing import Optional

sys.path.insert(0, "/data/Toolkit/artifacts/api-server/airforce")

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from core.registrar import Registrar, _parallel_register_batch, _next_port, _blacklist_port
from core.generator import generate_password
from core.storage import AccountStorage
from core.validator import KeyValidator

PORT    = int(os.environ.get("AIRFORCE_PORT", "8084"))
DB_PATH = os.environ.get("AIRFORCE_DB", "/root/AirForce/accounts.db")

app = FastAPI(
    title="AirForce API 自动注册服务",
    description="自动注册 api.airforce 账号获取 API Key (浏览器方式)",
    version="2.0.0"
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

storage   = AccountStorage(DB_PATH)
registrar = Registrar(timeout=150.0, max_retries=2)


def _gen_identity():
    """Generate a unique username + email pair."""
    sfx      = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    username = f"af_{sfx}"
    email    = f"af_{sfx}@proton.me"
    return username, email


# ── 批量任务状态 ──
_batch_state = {
    "running": False,
    "target": 0,
    "success": 0,
    "failure": 0,
    "logs": [],
    "start_time": None,
}
_batch_lock = threading.Lock()


def _log(msg: str):
    ts = time.strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    with _batch_lock:
        _batch_state["logs"].append(entry)
        if len(_batch_state["logs"]) > 500:
            _batch_state["logs"] = _batch_state["logs"][-500:]
    print(entry, flush=True)


# ── 请求模型 ──
class RegisterRequest(BaseModel):
    username: Optional[str] = None
    email:    Optional[str] = None
    password: Optional[str] = None

class BatchRequest(BaseModel):
    count:          int   = 5
    interval:       float = 0.0
    max_retries:    int   = 2
    max_concurrent: int   = 3

class ValidateRequest(BaseModel):
    api_key: str


# ── 单次注册 ──
@app.post("/register")
def register_one(req: RegisterRequest):
    username, email = _gen_identity()
    if req.username: username = req.username
    if req.email:    email    = req.email
    password = req.password or generate_password()

    result = registrar.register_and_get_key(
        username=username, password=password, email=email
    )
    if result.success:
        # Save account even if api_key is not yet available
        storage.save_account(result.username or username, result.password or password, result.api_key or '')
        return {
            "success": True,
            "username": result.username,
            "email":    result.email,
            "password": result.password,
            "api_key":  result.api_key,
            "user_id":  result.user_id,
            "proxy_port": result.proxy_port,
        }
    return {
        "success": False,
        "username": username,
        "error":    result.error,
        "proxy_port": result.proxy_port,
    }


# ── 批量注册（后台任务）──
def _batch_worker(count: int, interval: float, max_retries: int, max_concurrent: int = 3):
    """Parallel batch: runs max_concurrent Chrome instances simultaneously per round."""

    with _batch_lock:
        _batch_state.update({
            "running": True, "target": count,
            "success": 0, "failure": 0,
            "logs": [], "start_time": time.time(),
        })

    _log("并行批量注册开始: 目标=%d 账号, 并发=%d" % (count, max_concurrent))

    completed = 0
    while completed < count:
        if not _batch_state["running"]:
            _log("任务已停止")
            break

        round_size = min(max_concurrent, count - completed)
        items = []
        used_ports = set()
        for _ in range(round_size):
            sfx = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
            username = "af_" + sfx
            email    = "af_" + sfx + "@proton.me"
            password = generate_password()
            port = _next_port()
            tries = 0
            while port in used_ports and tries < 20:
                port = _next_port()
                tries += 1
            used_ports.add(port)
            items.append((username, email, password, port))

        port_list = [str(pt) for *_, pt in items]
        _log("  本轮 %d 并行 ports=[%s]" % (round_size, ", ".join(port_list)))

        try:
            results = _parallel_register_batch(items, max_concurrent=round_size)
        except Exception as ex:
            _log("  本轮异常: %s" % ex)
            results = []

        for result in results:
            completed += 1
            if not _batch_state["running"]:
                break
            if isinstance(result, Exception):
                _log("  异常: %s" % result)
                with _batch_lock:
                    _batch_state["failure"] += 1
                continue
            if result.success and result.api_key:
                storage.save_account(result.username, result.password, result.api_key)
                with _batch_lock:
                    _batch_state["success"] += 1
                key_p = (result.api_key or "")[:22]
                _log("  成功: %s key=%s... port=%s" % (result.username, key_p, result.proxy_port))
            else:
                with _batch_lock:
                    _batch_state["failure"] += 1
                err = getattr(result, "error", "unknown")
                _log("  失败: %s: %s" % (getattr(result, "username", "?"), err))
                port_r = getattr(result, "proxy_port", None)
                if port_r and any(kw in str(err) for kw in ("Rate limited", "Too many", "429")):
                    _blacklist_port(port_r)

        if _batch_state["running"] and completed < count and interval > 0:
            _log("  等待 %.0fs 后下一轮..." % interval)
            time.sleep(interval)

    with _batch_lock:
        _batch_state["running"] = False
    elapsed = time.time() - _batch_state["start_time"]
    _log("完成: 成功=%d 失败=%d 耗时=%.1fs" % (
        _batch_state["success"], _batch_state["failure"], elapsed))

@app.post("/batch/start")
def batch_start(req: BatchRequest, bg: BackgroundTasks):
    if _batch_state["running"]:
        raise HTTPException(400, "批量任务正在运行中")
    conc = max(1, min(req.max_concurrent, 5))
    bg.add_task(_batch_worker, req.count, req.interval, req.max_retries, conc)
    return {
        "ok": True,
        "message": "并行批量注册已启动: 目标=%d 账号, 并发=%d" % (req.count, conc),
        "max_concurrent": conc,
    }


@app.post("/batch/stop")
def batch_stop():
    with _batch_lock:
        _batch_state["running"] = False
    return {"ok": True, "message": "已发送停止信号"}


@app.get("/batch/status")
def batch_status():
    with _batch_lock:
        state = dict(_batch_state)
    elapsed = time.time() - state["start_time"] if state["start_time"] else 0
    return {
        "running":     state["running"],
        "target":      state["target"],
        "success":     state["success"],
        "failure":     state["failure"],
        "elapsed_s":   round(elapsed, 1),
        "rate_per_min": round(state["success"] / max(elapsed, 1) * 60, 2),
        "recent_logs": state["logs"][-50:],
    }


# ── Key 管理 ──
@app.get("/keys")
def list_keys():
    keys = storage.get_all_keys()
    return {"count": len(keys), "keys": keys}


@app.get("/accounts")
def list_accounts():
    accounts = storage.get_all_accounts()
    return {"count": len(accounts), "accounts": accounts}


@app.get("/stats")
def get_stats():
    return storage.get_stats()


# ── Key 验证 ──
@app.post("/validate")
def validate_key(req: ValidateRequest):
    is_valid, msg = KeyValidator.validate_key(req.api_key)
    if is_valid:
        storage.update_validation_status(req.api_key, True)
    return {"valid": is_valid, "message": msg, "api_key": req.api_key[:20] + "..."}


@app.post("/validate/all")
def validate_all():
    keys = storage.get_all_keys()
    results = {"valid": 0, "invalid": 0, "total": len(keys)}
    for k in keys:
        is_valid, _ = KeyValidator.validate_key(k, timeout=10.0)
        storage.update_validation_status(k, is_valid)
        if is_valid:
            results["valid"] += 1
        else:
            results["invalid"] += 1
    return results


# ── 自动调度器 ──
import sched as _sched
_scheduler_state = {
    "running":   False,
    "interval":  300,   # seconds between registrations
    "total":     0,
    "success":   0,
    "failure":   0,
    "start_time": None,
    "next_run":   None,
}
_sched_lock = threading.Lock()
_sched_thread = None


def _schedule_worker(interval: float, max_per_run: int):
    """Background thread: register one account every `interval` seconds."""
    import time as _time
    with _sched_lock:
        _scheduler_state["running"]    = True
        _scheduler_state["start_time"] = _time.time()
        _scheduler_state["total"]      = 0
        _scheduler_state["success"]    = 0
        _scheduler_state["failure"]    = 0

    _log(f"调度器启动: 间隔={interval}s, 每次最多={max_per_run}个")

    while True:
        with _sched_lock:
            if not _scheduler_state["running"]:
                break

        for _n in range(max_per_run):
            with _sched_lock:
                if not _scheduler_state["running"]:
                    break
            _username, _email = _gen_identity()
            _password = generate_password()
            _log(f"[scheduler] 注册 #{_scheduler_state['total']+1}: {_username}")
            _res = registrar.register_and_get_key(
                username=_username, password=_password, email=_email
            )
            with _sched_lock:
                _scheduler_state["total"] += 1
                if _res and _res.success:
                    storage.save_account(_res.username or _username, _res.password or _password, _res.api_key or '')
                    _scheduler_state["success"] += 1
                    _log(f"[scheduler] 成功: key={str(_res.api_key or '')[:25]}...")
                else:
                    _scheduler_state["failure"] += 1
                    _log(f"[scheduler] 失败: {getattr(_res,'error',str(_res))}")

        with _sched_lock:
            if not _scheduler_state["running"]:
                break
            next_t = _time.time() + interval
            _scheduler_state["next_run"] = next_t
        _log(f"[scheduler] 等待 {interval:.0f}s 后下一轮...")
        _time.sleep(interval)

    _log("[scheduler] 已停止")
    with _sched_lock:
        _scheduler_state["running"] = False


class ScheduleRequest(BaseModel):
    interval:    float = 300.0   # seconds between each batch
    max_per_run: int   = 1       # accounts to register each interval


@app.post("/schedule/start")
def schedule_start(req: ScheduleRequest, bg: BackgroundTasks):
    global _sched_thread
    with _sched_lock:
        if _scheduler_state["running"]:
            raise HTTPException(400, "调度器已在运行")
    _sched_thread = threading.Thread(
        target=_schedule_worker,
        args=(req.interval, req.max_per_run),
        daemon=True
    )
    _sched_thread.start()
    return {"ok": True, "message": f"调度器已启动: 间隔={req.interval}s, 每次={req.max_per_run}个"}


@app.post("/schedule/stop")
def schedule_stop():
    with _sched_lock:
        _scheduler_state["running"] = False
    return {"ok": True, "message": "调度器停止信号已发送"}


@app.get("/schedule/status")
def schedule_status():
    import time as _t
    with _sched_lock:
        state = dict(_scheduler_state)
    remaining = max(0, (state.get("next_run") or 0) - _t.time())
    return {
        "running":       state["running"],
        "total":         state["total"],
        "success":       state["success"],
        "failure":       state["failure"],
        "next_run_in_s": round(remaining, 1) if state["running"] else None,
    }



# ── ProxyScrape 代理池手动刷新 ──
_refresh_lock = threading.Lock()
_refresh_state = {"running": False, "last_run": None, "last_result": None}

def _do_refresh():
    import subprocess as _sub, time as _t
    with _refresh_lock:
        _refresh_state["running"] = True
    try:
        r = _sub.run(
            ["python3", "/root/AirForce/core/proxyscrape_manager.py"],
            capture_output=True, text=True, timeout=300
        )
        out = r.stdout[-2000:] if r.stdout else r.stderr[-1000:]
        with _refresh_lock:
            _refresh_state["last_result"] = out
            _refresh_state["last_run"] = _t.time()
        _log(f"[proxyscrape] 刷新完成: {out[-200:]}")
    except Exception as e:
        with _refresh_lock:
            _refresh_state["last_result"] = f"error: {e}"
        _log(f"[proxyscrape] 刷新失败: {e}")
    finally:
        with _refresh_lock:
            _refresh_state["running"] = False

@app.post("/refresh_proxies")
def refresh_proxies(bg: BackgroundTasks):
    with _refresh_lock:
        if _refresh_state["running"]:
            return {"ok": False, "message": "刷新已在运行中"}
    bg.add_task(_do_refresh)
    return {"ok": True, "message": "已在后台启动 ProxyScrape 代理池刷新 (约60-90s)"}

@app.get("/refresh_proxies/status")
def refresh_status():
    import time as _t
    with _refresh_lock:
        s = dict(_refresh_state)
    age = round(_t.time() - s["last_run"], 1) if s["last_run"] else None
    return {"running": s["running"], "last_run_ago_s": age, "result": s["last_result"]}

# ── 健康检查 ──
@app.get("/health")
def health():
    stats = storage.get_stats()
    return {"ok": True, "service": "airforce-register", "port": PORT,
            "db": DB_PATH, "stats": stats, "mode": "browser-based"}


@app.get("/")
def root():
    return {"service": "AirForce API 自动注册服务 v2",
            "docs":    f"http://45.205.27.69:{PORT}/docs",
            "health":  f"http://45.205.27.69:{PORT}/health"}


# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline: Email Queue API (沙盒 A/B 通信中继)
# ═══════════════════════════════════════════════════════════════════════════════
import sqlite3 as _sqlite3

def _db():
    conn = _sqlite3.connect(DB_PATH)
    conn.row_factory = _sqlite3.Row
    return conn

class EmailPushRequest(BaseModel):
    email:    str
    password: str
    username: Optional[str] = None
    platform: str           = "outlook"
    sandbox:  Optional[str] = None

class AccountPushRequest(BaseModel):
    username: str
    email:    str
    password: str
    api_key:  str
    sandbox:  Optional[str] = None

@app.post("/emails/push")
def emails_push(req: EmailPushRequest):
    """Sandbox-A deposits a generated email into the queue."""
    conn = _db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO email_queue (email,password,username,platform,status) "
            "VALUES (?,?,?,?,'available')",
            (req.email, req.password, req.username or req.email.split("@")[0], req.platform)
        )
        conn.commit()
        _log(f"[email-queue] +1 {req.email} from sandbox={req.sandbox}")
        cnt = conn.execute("SELECT COUNT(*) FROM email_queue WHERE status='available'").fetchone()[0]
        return {"ok": True, "queue_depth": cnt}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()

@app.get("/emails/pop")
def emails_pop():
    """Sandbox-B claims one email from the queue (FIFO, atomic)."""
    conn = _db()
    try:
        row = conn.execute(
            "SELECT id,email,password,username FROM email_queue "
            "WHERE status='available' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            raise HTTPException(404, "email queue is empty")
        conn.execute(
            "UPDATE email_queue SET status='claimed', claimed_at=datetime('now') WHERE id=?",
            (row["id"],)
        )
        conn.commit()
        _log(f"[email-queue] claimed {row['email']}")
        return {"email": row["email"], "password": row["password"],
                "username": row["username"], "id": row["id"]}
    finally:
        conn.close()

@app.get("/emails/status")
def emails_status():
    conn = _db()
    try:
        available = conn.execute(
            "SELECT COUNT(*) FROM email_queue WHERE status='available'").fetchone()[0]
        claimed = conn.execute(
            "SELECT COUNT(*) FROM email_queue WHERE status='claimed'").fetchone()[0]
        recent = [dict(r) for r in conn.execute(
            "SELECT email,platform,status,deposited_at FROM email_queue "
            "ORDER BY id DESC LIMIT 10"
        ).fetchall()]
        return {"available": available, "claimed": claimed, "recent": recent}
    finally:
        conn.close()

@app.post("/accounts/push")
def accounts_push(req: AccountPushRequest):
    """Sandbox-B deposits a registered api.airforce account (also saves to DB)."""
    storage.save_account(req.username, req.password, req.api_key)
    _log(f"[accounts/push] {req.username} key={req.api_key[:20]}... sandbox={req.sandbox}")
    return {"ok": True, "username": req.username}

# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline: Orchestration
# ═══════════════════════════════════════════════════════════════════════════════
_pipeline_state = {
    "running": False, "start_time": None,
    "email_ok": 0, "email_fail": 0,
    "reg_ok": 0,   "reg_fail": 0,
    "logs": [],
}
_pipeline_lock = threading.Lock()

class PipelineRequest(BaseModel):
    email_workers: int   = 2
    reg_workers:   int   = 3
    target:        int   = 3
    min_credits:   float = 0.5

def _pipeline_worker(email_workers: int, reg_workers: int, target: int, min_credits: float):
    with _pipeline_lock:
        _pipeline_state.update({
            "running": True, "start_time": time.time(),
            "email_ok": 0, "email_fail": 0,
            "reg_ok": 0, "reg_fail": 0, "logs": [],
        })
    try:
        sys.path.insert(0, "/data/Toolkit/artifacts/api-server/airforce/core")
        sys.path.insert(0, "/data/Toolkit/scripts")
        from obvious_pipeline import run_pipeline
        def _cb(r):
            with _pipeline_lock:
                role = r.get("role","?")
                ok   = r.get("success", False)
                if role == "email":
                    if ok: _pipeline_state["email_ok"] += 1
                    else:  _pipeline_state["email_fail"] += 1
                elif role == "register":
                    if ok: _pipeline_state["reg_ok"] += 1
                    else:  _pipeline_state["reg_fail"] += 1
                ts = time.strftime("%H:%M:%S")
                _pipeline_state["logs"].append(
                    f"[{ts}] [{role}] {'OK' if ok else 'FAIL'} "
                    f"sandbox={r.get('sandbox','?')} "
                    + (f"key={r.get('api_key','')[:18]}..." if ok and role=='register' else
                       f"email={r.get('email','-')}" if ok and role=='email' else
                       f"err={r.get('error','?')[:60]}")
                )
        run_pipeline(email_workers, reg_workers, target, min_credits, progress_cb=_cb)
    except Exception as e:
        _log(f"[pipeline] exception: {e}")
    finally:
        with _pipeline_lock:
            _pipeline_state["running"] = False

@app.post("/pipeline/start")
def pipeline_start(req: PipelineRequest, bg: BackgroundTasks):
    if _pipeline_state["running"]:
        raise HTTPException(400, "pipeline already running")
    bg.add_task(_pipeline_worker, req.email_workers, req.reg_workers,
                req.target, req.min_credits)
    return {"ok": True,
            "message": f"pipeline started: email_workers={req.email_workers} "
                       f"reg_workers={req.reg_workers} target={req.target}"}

@app.get("/pipeline/status")
def pipeline_status():
    with _pipeline_lock:
        s = dict(_pipeline_state)
    elapsed = time.time() - s["start_time"] if s["start_time"] else 0
    return {
        "running":    s["running"],
        "elapsed_s":  round(elapsed, 1),
        "email_ok":   s["email_ok"],  "email_fail":  s["email_fail"],
        "reg_ok":     s["reg_ok"],    "reg_fail":    s["reg_fail"],
        "recent_logs": s["logs"][-30:],
    }

@app.post("/pipeline/stop")
def pipeline_stop():
    with _pipeline_lock:
        _pipeline_state["running"] = False
    return {"ok": True}



if __name__ == "__main__":
    print(f"[airforce] 启动在端口 {PORT}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
