#!/usr/bin/env python3
"""
obvious_keepalive.py — sandbox health probe, auto-recovery, credit auto-reset

全部 obvious.ai API 调用通过每账号独立的 SOCKS5 代理路由：
  manifest["proxy"] = "socks5://127.0.0.1:10820"  → 独立出口 IP
防止 obvious.ai 通过同一 IP 关联多个账号。

新增功能:
  - 每个 tick 检查 credit 用量；超过 CREDIT_RESET_THRESHOLD 自动删除所有项目归零
  - 跳过 manifest["status"]=="dead" 的账号
  - _http_proxy(): 用 requests + socks5h:// 走代理，DNS 也通过代理解析
"""
from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
import uuid
from pathlib import Path

import requests
import signal

# Optional autoprovision integration
MIN_POOL = int(os.environ.get("SB_MIN_POOL", "2"))
try:
    from obvious_autoprovision import check_and_replenish as _autoprovision
    _AUTOPROVISION_AVAILABLE = True
except ImportError:
    _AUTOPROVISION_AVAILABLE = False

ACC_DIR                = Path(os.environ.get("SB_ACC_DIR",               "/root/obvious-accounts"))
PING_MIN               = int(os.environ.get("SB_PING_MIN",               "45"))
PING_MAX               = int(os.environ.get("SB_PING_MAX",               "75"))
WAKE_TIMEOUT           = int(os.environ.get("SB_WAKE_TIMEOUT",           "150"))
CREDIT_RESET_THRESHOLD = float(os.environ.get("SB_CREDIT_RESET_THRESHOLD", "20.0"))
_BASE                  = "https://api.app.obvious.ai/prepare"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s probe %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# Graceful SIGTERM shutdown (prevents PM2 SIGINT→KBI propagation)
def _sigterm_handler(signum, frame):  # noqa
    log.info("keepalive shutdown (SIGTERM)")
    sys.exit(0)
signal.signal(signal.SIGTERM, _sigterm_handler)

_WAKE_MSGS = [
    "print(1+1)", "x = 42; print(x)",
    "import sys; print(sys.version)", "import os; print(os.getcwd())",
    "for i in range(3): print(i)", "print('hello')",
    "2**10", "len('hello world')",
]


# ─────────────────────────────────────────────────────────────────────────────
# Proxy-aware HTTP
# ─────────────────────────────────────────────────────────────────────────────

def _make_session(proxy_url: str | None) -> requests.Session:
    """requests.Session routed through per-account SOCKS5 proxy.
    Uses socks5h:// so DNS is resolved through the proxy (no DNS leak).
    """
    s = requests.Session()
    if proxy_url:
        socks5h = proxy_url.replace("socks5://", "socks5h://")
        s.proxies = {"http": socks5h, "https": socks5h}
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    return s


def _http(method: str, url: str, body: dict | None = None,
          headers: dict | None = None, timeout: float = 15.0,
          session: requests.Session | None = None) -> tuple[int, str]:
    h = dict(headers or {})
    if body is not None:
        h.setdefault("Content-Type", "application/json")
    if session is not None:
        try:
            resp = session.request(method, url, json=body, headers=h, timeout=timeout)
            return resp.status_code, resp.text
        except Exception as e:
            return -1, str(e)
    # Fallback bare urllib (e2b sandbox direct calls — no proxy needed)
    import urllib.request, urllib.error
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return -1, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# Account helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_index() -> list[dict]:
    p = ACC_DIR / "index.json"
    return json.loads(p.read_text()) if p.exists() else []


def _save_index(accounts: list[dict]) -> None:
    (ACC_DIR / "index.json").write_text(
        json.dumps(accounts, indent=2, ensure_ascii=False))


def _persist(label: str, sb_id: str) -> None:
    mp = ACC_DIR / label / "manifest.json"
    if mp.exists():
        m = json.loads(mp.read_text())
        m["sandboxId"] = sb_id
        mp.write_text(json.dumps(m, indent=2))
    accs = _load_index()
    for a in accs:
        if a["label"] == label:
            a["sandboxId"] = sb_id
    _save_index(accs)


def _load_manifest(label: str) -> dict:
    mp = ACC_DIR / label / "manifest.json"
    return json.loads(mp.read_text()) if mp.exists() else {}


def _cookies(label: str) -> str:
    state = json.loads((ACC_DIR / label / "storage_state.json").read_text())
    return "; ".join(
        c["name"] + "=" + c["value"]
        for c in state["cookies"]
        if "obvious.ai" in c.get("domain", "")
    )


def _headers(label: str) -> dict:
    return {
        "Cookie":       _cookies(label),
        "Content-Type": "application/json",
        "Origin":       "https://app.obvious.ai",
        "Referer":      "https://app.obvious.ai/",
    }


def _get_session(label: str) -> requests.Session:
    mf = _load_manifest(label)
    proxy = mf.get("proxy")
    return _make_session(proxy)


# ─────────────────────────────────────────────────────────────────────────────
# Credit management
# ─────────────────────────────────────────────────────────────────────────────

def _check_credits_remaining(label: str, session: requests.Session) -> float:
    """Return actual remaining creditBalance from /workspaces API (NOT usage counter).
    This is the REAL money left in the account, not the display counter.
    """
    hdr = _headers(label)
    s, b = _http("GET", _BASE + "/workspaces", headers=hdr, session=session)
    if s != 200:
        return -1.0
    try:
        wks_list = json.loads(b).get("workspaces", [])
        if not wks_list:
            return -1.0
        return float(wks_list[0].get("creditBalance") or 0.0)
    except Exception:
        return -1.0


def _check_credits(label: str, session: requests.Session) -> float:
    """Return totalCredits consumed (usage counter) — kept for backward compat."""
    mf  = _load_manifest(label)
    wid = mf.get("workspaceId", "")
    if not wid:
        return 0.0
    hdr = _headers(label)
    s, b = _http("GET", _BASE + "/workspaces/" + wid + "/usage",
                 headers=hdr, session=session)
    if s != 200:
        return 0.0
    try:
        summ = json.loads(b).get("usage", {}).get("summary", {})
        return float(summ.get("creditEstimate", {}).get("totalCredits", 0.0))
    except Exception:
        return 0.0


def _reset_credits(label: str, session: requests.Session) -> int:
    """Delete all projects → resets credit counter to 0. Returns # projects deleted."""
    mf  = _load_manifest(label)
    wid = mf.get("workspaceId", "")
    if not wid:
        return 0
    hdr = _headers(label)
    s, b = _http("GET", _BASE + "/workspaces/" + wid + "/projects",
                 headers=hdr, session=session)
    deleted = 0
    if s != 200:
        return 0
    try:
        data = json.loads(b)
        projects = data.get("projects", data) if isinstance(data, dict) else data
        if not isinstance(projects, list):
            return 0
    except Exception:
        return 0
    for proj in projects:
        pid = proj.get("id")
        if not pid:
            continue
        # Delete threads first
        si, bi = _http("GET", _BASE + "/projects/" + pid + "/info",
                       headers=hdr, session=session)
        if si == 200:
            try:
                for th in json.loads(bi).get("threads", []):
                    tid = th.get("id")
                    if tid:
                        _http("DELETE", _BASE + "/threads/" + tid,
                              headers=hdr, session=session)
            except Exception:
                pass
        sd, _ = _http("DELETE", _BASE + "/projects/" + pid,
                      headers=hdr, session=session)
        if sd == 200:
            deleted += 1
    return deleted


def _auto_reset_credits(label: str, session: requests.Session,
                        threshold: float = CREDIT_RESET_THRESHOLD) -> None:
    """Check REAL remaining creditBalance and act:

      remaining < 3.0               → mark dead (truly out of money, reset won't help)
      remaining >= 3.0              → account healthy, log and continue
      remaining >= 5.0 AND          → optionally clean up usage counter
        consumed >= 10.0

    Bug fixed: old code read totalCredits-CONSUMED from /usage API which resets to 0
    after project deletion, making depleted accounts look healthy.  Now we read the
    actual billing creditBalance from /workspaces which never resets artificially.
    """
    remaining = _check_credits_remaining(label, session)
    if remaining < 0:
        log.warning("[%s] credit check failed (proxy/cookie/API error)", label)
        return

    # Genuinely out of money — mark dead so autoprovision replaces this account
    if remaining < 3.0:
        log.warning("[%s] remaining=%.2f < 3.0 — marking dead (credits truly depleted)",
                    label, remaining)
        mf = _load_manifest(label)
        if mf.get("status") != "dead":
            mf["status"]     = "dead"
            mf["deadReason"] = "credit_depleted"
            mf["deadAt"]     = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            (ACC_DIR / label / "manifest.json").write_text(
                json.dumps(mf, indent=2, ensure_ascii=False))
        return

    log.info("[%s] credits=%.2f remaining (healthy)", label, remaining)

    # Cosmetic: reset usage counter when it gets large AND we still have headroom.
    # This keeps dashboard clean and avoids confusion — but does NOT restore balance.
    consumed = _check_credits(label, session)
    if consumed >= 10.0 and remaining >= 5.0:
        log.info("[%s] consumed=%.2f >= 10 & remaining=%.2f >= 5 — cleaning counter",
                 label, consumed, remaining)
        n = _reset_credits(label, session)
        log.info("[%s] counter reset: deleted %d projects", label, n)
        mf = _load_manifest(label)
        mf["creditResetAt"]   = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        mf["creditResetFrom"] = round(consumed, 4)
        mf["projectId"]       = None
        mf["threadId"]        = None
        mf["sandboxId"]       = None
        mf["execBase"]        = None
        mf["jupyterBase"]     = None
        mf["needsRepair"]     = True
        (ACC_DIR / label / "manifest.json").write_text(
            json.dumps(mf, indent=2, ensure_ascii=False))
        log.info("[%s] needsRepair flag set — repair_account will be triggered", label)


# ─────────────────────────────────────────────────────────────────────────────
# Sandbox wake / keepwarm
# ─────────────────────────────────────────────────────────────────────────────

def _alive(sb_id: str) -> bool:
    if not sb_id:
        return False
    s, _ = _http("GET", "https://49999-" + sb_id + ".e2b.app/health", timeout=8)
    return s == 200


def _e2b_recycled(sb_id: str) -> bool:
    """Return True if e2b explicitly returns 502 (sandbox recycled, not just paused)."""
    if not sb_id:
        return True
    s, _ = _http("GET", "https://49999-" + sb_id + ".e2b.app/health", timeout=8)
    return s == 502


def _shell_keep_warm(label: str, tid: str, session: requests.Session) -> bool:
    s, _ = _http("POST", _BASE + "/threads/" + tid + "/shell/wake",
                 {}, _headers(label), timeout=15, session=session)
    return s == 200


def _e2b_exec_ping(sb_id: str) -> bool:
    """Directly execute a noop in the e2b sandbox to reset its idle timer.

    /health is passive and does NOT reset e2b idle counter.
    Only actual code execution resets the timer, so we POST a trivial
    print(1) to the sandbox exec server (port 49999, no proxy needed).
    Returns True if the sandbox responded with execution output.
    """
    if not sb_id:
        return False
    url = "https://49999-" + sb_id + ".e2b.app/execute"
    body = {"code": "print(1)", "language": "python"}
    s, _resp = _http("POST", url, body, timeout=10)
    return 200 <= s < 300


def _chat_wake(label: str, session: requests.Session) -> str | None:
    mf = _load_manifest(label)
    tid, pid = mf.get("threadId"), mf.get("projectId")
    if not tid or not pid:
        log.warning("[%s] missing threadId/projectId, skip wake", label)
        return None

    hdr = _headers(label)
    msg = random.choice(_WAKE_MSGS)
    body = {
        "message":               msg,
        "messageId":             uuid.uuid4().hex,
        "projectId":             pid,
        "visibleRecords":        [],
        "fileIds":               [],
        "modeId":                "auto",
        "isIntentionalModeChange": True,
        "timezone":              "America/New_York",
        "deliveryMode":          "queued",
    }
    s, _ = _http("POST", _BASE + "/api/v2/agent/chat/" + tid, body, hdr,
                 timeout=20, session=session)
    if s != 200:
        log.warning("[%s] chat wake failed %s", label, s)
        return None

    deadline = time.time() + WAKE_TIMEOUT
    while time.time() < deadline:
        try:
            time.sleep(random.uniform(4, 8))
        except KeyboardInterrupt:
            log.info("keepalive shutdown during chat-wake")
            sys.exit(0)
        s2, b2 = _http("GET", _BASE + "/projects/" + pid + "/info",
                       headers=hdr, timeout=10, session=session)
        if s2 == 200:
            meta = json.loads(b2).get("metadata", {}).get("sandbox", {})
            sb, paused = meta.get("sandboxId"), meta.get("isPaused", True)
            if sb and not paused:
                _persist(label, sb)
                return sb
    return None



# ─────────────────────────────────────────────────────────────────────────────
# Sandbox resource initialization (run once after wake)
# ─────────────────────────────────────────────────────────────────────────────

SANDBOX_INIT_CODE = r"""
import subprocess, os

results = {}

# 1. 提升文件描述符上限 (default 4096 → 65536)
try:
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = min(65536, hard if hard != resource.RLIM_INFINITY else 65536)
    resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    results['nofile'] = f"{soft} → {target}"
except Exception as e:
    results['nofile'] = f"err:{e}"

# 2. 扩展 /tmp tmpfs (default 4GB → 7GB)
try:
    r = subprocess.run(
        ["mount", "-o", "remount,size=7G", "/tmp"],
        capture_output=True, text=True, timeout=10
    )
    if r.returncode == 0:
        df = subprocess.run(["df", "-h", "/tmp"], capture_output=True, text=True)
        results['tmp'] = df.stdout.strip().splitlines()[-1]
    else:
        results['tmp'] = f"remount_err:{r.stderr.strip()[:80]}"
except Exception as e:
    results['tmp'] = f"err:{e}"

# 3. /dev/shm 确认无限制 (已验证，仅记录)
try:
    df2 = subprocess.run(["df", "-h", "/dev/shm"], capture_output=True, text=True)
    results['shm'] = df2.stdout.strip().splitlines()[-1]
except Exception as e:
    results['shm'] = f"err:{e}"

print(__import__('json').dumps(results))
"""


def _init_sandbox(sb_id: str) -> dict:
    """Run resource-expansion init on a freshly-woken sandbox.
    Sends Python code to port-49999 exec server (no proxy, direct).
    Returns dict of applied changes.
    """
    import urllib.request, urllib.error
    url = "https://49999-" + sb_id + ".e2b.app/execute"
    body = json.dumps({"code": SANDBOX_INIT_CODE, "language": "python"}).encode()
    req = urllib.request.Request(url, data=body,
                                  headers={"Content-Type": "application/json"},
                                  method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
    except Exception as e:
        return {"error": str(e)}
    # Parse streaming JSON lines from exec server
    for line in raw.strip().splitlines():
        try:
            ev = json.loads(line)
            if ev.get("type") == "stdout":
                return json.loads(ev.get("text", "{}"))
        except Exception:
            pass
    return {"raw": raw[:200]}

# ─────────────────────────────────────────────────────────────────────────────
# Main tick
# ─────────────────────────────────────────────────────────────────────────────

def _tick_one(acc: dict) -> None:
    """Process a single account: credit check, auto-repair, exec-ping + wake."""
    label = acc.get("label", "?")

    if not (ACC_DIR / label / "manifest.json").exists():
        return
    if not (ACC_DIR / label / "storage_state.json").exists():
        return

    mf = _load_manifest(label)
    if mf.get("status") == "dead":
        log.info("[%s] dead -- skipped", label)
        return

    sess = _get_session(label)

    try:
        _auto_reset_credits(label, sess, CREDIT_RESET_THRESHOLD)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        log.warning("[%s] credit check error: %s", label, e)

    mf = _load_manifest(label)
    sb = mf.get("sandboxId") or acc.get("sandboxId") or ""
    tid = mf.get("threadId")

    # Auto-repair: trigger on ANY needsRepair=True (v2)
    if mf.get("needsRepair"):
        log.info("[%s] needsRepair=True -- running repair_account.py", label)
        import subprocess as _sp, os as _os
        repair_py = str(Path(__file__).parent / "repair_account.py")
        env = dict(_os.environ, DISPLAY=_os.environ.get("DISPLAY", ":99"))
        try:
            result = _sp.run(
                ["python3", repair_py, "--label", label, "--headless"],
                timeout=180, env=env, capture_output=True, text=True
            )
            log.info("[%s] repair exit=%d stdout=%s",
                     label, result.returncode, result.stdout[-200:] if result.stdout else "")
            mf = _load_manifest(label)
            if mf.get("projectId") and mf.get("threadId"):
                mf["needsRepair"] = False
                (ACC_DIR / label / "manifest.json").write_text(
                    json.dumps(mf, indent=2, ensure_ascii=False))
                log.info("[%s] repair succeeded, needsRepair cleared", label)
            else:
                log.warning("[%s] repair ran but still missing IDs", label)
        except KeyboardInterrupt:
            raise
        except Exception as _e:
            log.warning("[%s] repair_account error: %s", label, _e)
        mf = _load_manifest(label)
        sb = mf.get("sandboxId") or ""
        tid = mf.get("threadId")

    if _alive(sb):
        # v3: direct e2b exec-ping resets idle timer;
        # /health and shell/wake do NOT reset e2b idle counter
        exec_ok = _e2b_exec_ping(sb)
        warm_ok = _shell_keep_warm(label, tid, sess) if tid else False
        log.info("[%s] ok sb=%s exec_ping=%s warm=%s proxy=%s",
                 label, sb[:8], exec_ok, warm_ok, mf.get("proxy", "NONE"))
    else:
        log.info("[%s] paused, waking via chat (proxy=%s)",
                 label, mf.get("proxy", "NONE"))
        new_sb = _chat_wake(label, sess)
        if new_sb:
            init_result = _init_sandbox(new_sb)
            log.info("[%s] ready sb=%s init=%s", label, new_sb[:8], init_result)
        else:
            log.warning("[%s] unavailable after wake attempt", label)
            if _e2b_recycled(sb):
                log.warning("[%s] e2b 502 -- sandbox recycled, setting needsRepair=True", label)
                mf2 = _load_manifest(label)
                mf2["sandboxId"]   = None
                mf2["execBase"]    = None
                mf2["needsRepair"] = True
                (ACC_DIR / label / "manifest.json").write_text(
                    json.dumps(mf2, indent=2, ensure_ascii=False))


def _tick() -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    accounts = _load_index()
    # Parallel: prevents sequential starvation where accounts at the tail
    # only get pinged every 3-5 min while early ones hog the loop
    with ThreadPoolExecutor(max_workers=min(len(accounts), 14)) as pool:
        futs = {pool.submit(_tick_one, acc): acc.get("label", "?") for acc in accounts}
        for fut in as_completed(futs):
            lbl = futs[fut]
            try:
                fut.result()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log.exception("[%s] tick_one error: %s", lbl, e)

def main() -> None:
    log.info("probe starting  acc_dir=%s  credit_threshold=%.1f  min_pool=%d",
             ACC_DIR, CREDIT_RESET_THRESHOLD, MIN_POOL)
    while True:
        try:
            _tick()
        except KeyboardInterrupt:
            log.info("keepalive shutdown (SIGTERM)")
            sys.exit(0)
        except Exception as e:
            log.exception("tick error: %s", e)

        # Pool replenishment: provision new accounts when pool is low
        if _AUTOPROVISION_AVAILABLE:
            try:
                active_count = sum(
                    1 for a in _load_index()
                    if (ACC_DIR / a.get("label","") / "manifest.json").exists()
                    and json.loads((ACC_DIR / a["label"] / "manifest.json").read_text()).get("status") != "dead"
                )
                if active_count < MIN_POOL:
                    log.warning("pool low: %d active < %d min — triggering autoprovision",
                                active_count, MIN_POOL)
                    _autoprovision(min_active=MIN_POOL, headless=True)
            except Exception as e:
                log.exception("autoprovision error: %s", e)

        interval = random.randint(PING_MIN, PING_MAX)
        log.info("next check in %ds", interval)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            log.info("keepalive shutdown (SIGTERM/KeyboardInterrupt)")
            sys.exit(0)


if __name__ == "__main__":
    main()
