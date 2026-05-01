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

# Optional autoprovision integration
MIN_POOL = int(os.environ.get("SB_MIN_POOL", "2"))
try:
    from obvious_autoprovision import check_and_replenish as _autoprovision
    _AUTOPROVISION_AVAILABLE = True
except ImportError:
    _AUTOPROVISION_AVAILABLE = False

ACC_DIR                = Path(os.environ.get("SB_ACC_DIR",               "/root/obvious-accounts"))
PING_MIN               = int(os.environ.get("SB_PING_MIN",               "90"))
PING_MAX               = int(os.environ.get("SB_PING_MAX",               "180"))
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

def _check_credits(label: str, session: requests.Session) -> float:
    """Return totalCredits consumed for this account's workspace."""
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
    """Check credits and reset if >= threshold. Updates manifest after reset."""
    credits = _check_credits(label, session)
    if credits < threshold:
        log.info("[%s] credits=%.2f (ok, below %.1f)", label, credits, threshold)
        return
    log.warning("[%s] credits=%.2f >= threshold %.1f — resetting ...",
                label, credits, threshold)
    n = _reset_credits(label, session)
    credits_after = _check_credits(label, session)
    log.info("[%s] reset done: deleted %d projects, credits %.2f → %.2f",
             label, n, credits, credits_after)
    # After reset the project/thread are gone — update manifest to reflect this
    mf = _load_manifest(label)
    mf["creditResetAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    mf["creditResetFrom"] = round(credits, 4)
    # Clear project/thread/sandbox since they're deleted
    # (they'll be recreated on next from_account() call)
    mf["projectId"]   = None
    mf["threadId"]    = None
    mf["sandboxId"]   = None
    mf["execBase"]    = None
    mf["jupyterBase"] = None
    (ACC_DIR / label / "manifest.json").write_text(
        json.dumps(mf, indent=2, ensure_ascii=False))


# ─────────────────────────────────────────────────────────────────────────────
# Sandbox wake / keepwarm
# ─────────────────────────────────────────────────────────────────────────────

def _alive(sb_id: str) -> bool:
    if not sb_id:
        return False
    s, _ = _http("GET", "https://49999-" + sb_id + ".e2b.app/health", timeout=8)
    return s == 200


def _shell_keep_warm(label: str, tid: str, session: requests.Session) -> bool:
    s, _ = _http("POST", _BASE + "/threads/" + tid + "/shell/wake",
                 {}, _headers(label), timeout=15, session=session)
    return s == 200


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
        time.sleep(random.uniform(4, 8))
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
# Main tick
# ─────────────────────────────────────────────────────────────────────────────

def _tick() -> None:
    for acc in _load_index():
        label = acc.get("label", "?")
        sb    = acc.get("sandboxId") or ""

        # Skip missing files
        if not (ACC_DIR / label / "manifest.json").exists():
            continue
        if not (ACC_DIR / label / "storage_state.json").exists():
            continue

        # Skip dead accounts
        mf = _load_manifest(label)
        if mf.get("status") == "dead":
            log.info("[%s] dead — skipped", label)
            continue

        # Build per-account proxy session
        sess = _get_session(label)

        # Credit auto-reset (every tick, before keepalive)
        try:
            _auto_reset_credits(label, sess, CREDIT_RESET_THRESHOLD)
        except Exception as e:
            log.warning("[%s] credit check error: %s", label, e)

        # Re-read manifest in case credit reset cleared project/sandbox
        mf = _load_manifest(label)
        sb = mf.get("sandboxId") or acc.get("sandboxId") or ""
        tid = mf.get("threadId")

        if _alive(sb):
            if tid:
                ok = _shell_keep_warm(label, tid, sess)
                log.info("[%s] ok sb=%s warm=%s proxy=%s",
                         label, sb[:8], ok, mf.get("proxy", "NONE"))
            else:
                log.info("[%s] ok sb=%s (no tid) proxy=%s",
                         label, sb[:8], mf.get("proxy", "NONE"))
        else:
            log.info("[%s] paused, waking via chat (proxy=%s)",
                     label, mf.get("proxy", "NONE"))
            new_sb = _chat_wake(label, sess)
            if new_sb:
                log.info("[%s] ready sb=%s", label, new_sb[:8])
            else:
                log.warning("[%s] unavailable after wake attempt", label)


def main() -> None:
    log.info("probe starting  acc_dir=%s  credit_threshold=%.1f  min_pool=%d",
             ACC_DIR, CREDIT_RESET_THRESHOLD, MIN_POOL)
    while True:
        try:
            _tick()
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
        time.sleep(interval)


if __name__ == "__main__":
    main()
