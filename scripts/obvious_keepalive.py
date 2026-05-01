#!/usr/bin/env python3
"""
obvious_keepalive.py — Sandbox keepalive daemon for obvious.ai e2b sandboxes.

Every PING_INTERVAL seconds for every account in index.json:
  - If sandbox health 200: execute no-op to warm Jupyter kernel too  → ✓ alive
  - If sandbox dead/missing: send "run: print(1)" to obvious AI chat,
    poll until sandboxId appears and is not paused, then persist new ID  → revived

Env vars:
  OBVIOUS_ACC_DIR        default /root/obvious-accounts
  OBVIOUS_PING_INTERVAL  default 120 (seconds between ticks)
  OBVIOUS_WAKE_TIMEOUT   default 150 (seconds to wait for sandbox after ping)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
import urllib.request
import urllib.error
from pathlib import Path

# ── config ─────────────────────────────────────────────────────────────────────
ACC_DIR       = Path(os.environ.get("OBVIOUS_ACC_DIR", "/root/obvious-accounts"))
PING_INTERVAL = int(os.environ.get("OBVIOUS_PING_INTERVAL", "120"))
WAKE_TIMEOUT  = int(os.environ.get("OBVIOUS_WAKE_TIMEOUT",  "150"))
API_BASE      = "https://api.app.obvious.ai/prepare"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [keepalive] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── HTTP (stdlib) ───────────────────────────────────────────────────────────────

def _http(method: str, url: str, body: dict | None = None,
          headers: dict | None = None, timeout: float = 15.0) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    h = dict(headers or {})
    if body is not None:
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return -1, str(e)

# ── index helpers ───────────────────────────────────────────────────────────────

def load_index() -> list[dict]:
    p = ACC_DIR / "index.json"
    return json.loads(p.read_text()) if p.exists() else []

def save_index(accounts: list[dict]) -> None:
    (ACC_DIR / "index.json").write_text(json.dumps(accounts, indent=2, ensure_ascii=False))

def _persist_sandbox(label: str, sandbox_id: str) -> None:
    """Write new sandboxId into both index.json and manifest.json."""
    mp = ACC_DIR / label / "manifest.json"
    if mp.exists():
        m = json.loads(mp.read_text())
        m["sandboxId"] = sandbox_id
        mp.write_text(json.dumps(m, indent=2))
    accs = load_index()
    for a in accs:
        if a["label"] == label:
            a["sandboxId"] = sandbox_id
            break
    save_index(accs)

def _cookies(label: str) -> str:
    state = json.loads((ACC_DIR / label / "storage_state.json").read_text())
    return "; ".join(
        f"{c['name']}={c['value']}" for c in state["cookies"]
        if "obvious.ai" in c.get("domain", "")
    )

def _manifest(label: str) -> dict:
    return json.loads((ACC_DIR / label / "manifest.json").read_text())

# ── sandbox checks ──────────────────────────────────────────────────────────────

def sandbox_alive(sandbox_id: str) -> bool:
    if not sandbox_id:
        return False
    s, _ = _http("GET", f"https://49999-{sandbox_id}.e2b.app/health", timeout=8)
    return s == 200

def warm_kernel(sandbox_id: str) -> None:
    """Fire-and-forget no-op execute to keep Jupyter kernel warm."""
    _http("POST", f"https://49999-{sandbox_id}.e2b.app/execute",
          body={"code": "1", "language": "python"}, timeout=10)

# ── wake ────────────────────────────────────────────────────────────────────────

def wake_sandbox(label: str) -> str | None:
    """
    Send a code-execution message to obvious AI to trigger sandbox allocation.
    Polls project info every 4s until sandboxId appears and is not paused.
    Returns new sandboxId or None on failure.
    """
    try:
        manifest = _manifest(label)
        thread_id  = manifest.get("threadId")
        project_id = manifest.get("projectId")
    except Exception as e:
        log.error("[%s] manifest error: %s", label, e)
        return None

    if not thread_id or not project_id:
        log.warning("[%s] missing threadId/projectId — skipping", label)
        return None

    cookies = _cookies(label)
    headers = {
        "Cookie":     cookies,
        "Content-Type": "application/json",
        "Origin":     "https://app.obvious.ai",
        "Referer":    "https://app.obvious.ai/",
        "User-Agent": "Mozilla/5.0 obvious-keepalive/1.0",
    }

    # "run: print(1)" reliably triggers code execution → sandbox allocation
    body = {
        "message":               "run: print(1)",
        "messageId":             uuid.uuid4().hex,
        "projectId":             project_id,
        "visibleRecords":        [],
        "fileIds":               [],
        "modeId":                "auto",
        "isIntentionalModeChange": True,
        "timezone":              "UTC",
        "deliveryMode":          "queued",
    }
    s, b = _http("POST", f"{API_BASE}/api/v2/agent/chat/{thread_id}", body, headers, timeout=20)
    if s != 200:
        log.error("[%s] wake ping failed %s: %s", label, s, b[:120])
        return None
    log.info("[%s] wake ping sent (executionId=%s), polling...",
             label, json.loads(b).get("executionId", "?"))

    deadline = time.time() + WAKE_TIMEOUT
    while time.time() < deadline:
        time.sleep(4)
        s2, b2 = _http("GET", f"{API_BASE}/projects/{project_id}/info",
                        headers=headers, timeout=10)
        if s2 == 200:
            meta   = json.loads(b2).get("metadata", {}).get("sandbox", {})
            sb_id  = meta.get("sandboxId")
            paused = meta.get("isPaused", True)
            if sb_id and not paused:
                log.info("[%s] sandbox live: %s", label, sb_id)
                _persist_sandbox(label, sb_id)
                return sb_id
        else:
            log.warning("[%s] project info returned %s", label, s2)

    log.error("[%s] sandbox did not appear within %ds — account may lack code-exec access",
              label, WAKE_TIMEOUT)
    return None

# ── main loop ───────────────────────────────────────────────────────────────────

def tick() -> None:
    accounts = load_index()
    if not accounts:
        log.warning("index.json empty or missing — nothing to keep alive")
        return

    for acc in accounts:
        label      = acc.get("label", "?")
        sandbox_id = acc.get("sandboxId") or ""

        if not (ACC_DIR / label / "manifest.json").exists():
            log.debug("[%s] no manifest, skipping", label)
            continue
        if not (ACC_DIR / label / "storage_state.json").exists():
            log.debug("[%s] no cookies, skipping", label)
            continue

        if sandbox_alive(sandbox_id):
            warm_kernel(sandbox_id)
            log.info("[%s] ✓ alive (%s)", label, sandbox_id)
        else:
            log.warning("[%s] ✗ dead (was: %s) — waking", label, sandbox_id or "none")
            new_id = wake_sandbox(label)
            if new_id:
                log.info("[%s] ✓ revived → %s", label, new_id)
            else:
                log.error("[%s] ✗ revive failed (will retry next tick)", label)

def main() -> None:
    log.info("obvious-keepalive starting  interval=%ds  acc_dir=%s", PING_INTERVAL, ACC_DIR)
    while True:
        try:
            tick()
        except Exception as e:
            log.exception("tick() error: %s", e)
        log.info("sleeping %ds until next tick", PING_INTERVAL)
        time.sleep(PING_INTERVAL)

if __name__ == "__main__":
    main()
