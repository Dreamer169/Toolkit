#!/usr/bin/env python3
"""
obvious_keepalive.py — Sandbox keepalive daemon for obvious.ai e2b sandboxes.

Strategy:
  Every PING_INTERVAL seconds:
    1. GET https://49999-{sandboxId}.e2b.app/health
       → 200 "OK"  : sandbox alive → also POST a lightweight execute to warm kernel
       → non-200   : sandbox dead  → wake via obvious AI chat, poll for new sandboxId,
                                     update index.json + manifest.json
  Keeps ALL accounts in index.json alive simultaneously.

Run: python3 obvious_keepalive.py
PM2: pm2 start obvious_keepalive.py --name obvious-keepalive --interpreter python3
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

# ── config ────────────────────────────────────────────────────────────────────
ACC_DIR     = Path(os.environ.get("OBVIOUS_ACC_DIR", "/root/obvious-accounts"))
PING_INTERVAL = int(os.environ.get("OBVIOUS_PING_INTERVAL", "120"))   # seconds
WAKE_TIMEOUT  = int(os.environ.get("OBVIOUS_WAKE_TIMEOUT",  "120"))   # seconds
API_BASE    = "https://api.app.obvious.ai/prepare"

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [keepalive] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── HTTP helper (stdlib-only) ─────────────────────────────────────────────────
import urllib.request
import urllib.error

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

# ── account helpers ───────────────────────────────────────────────────────────

def load_index() -> list[dict]:
    p = ACC_DIR / "index.json"
    return json.loads(p.read_text()) if p.exists() else []

def save_index(accounts: list[dict]) -> None:
    p = ACC_DIR / "index.json"
    p.write_text(json.dumps(accounts, indent=2, ensure_ascii=False))

def _cookies(label: str) -> str:
    state = json.loads((ACC_DIR / label / "storage_state.json").read_text())
    return "; ".join(
        f"{c['name']}={c['value']}" for c in state["cookies"]
        if "obvious.ai" in c.get("domain", "")
    )

def _manifest(label: str) -> dict:
    return json.loads((ACC_DIR / label / "manifest.json").read_text())

def _save_sandbox_id(label: str, sandbox_id: str) -> None:
    """Persist sandboxId into both index.json and manifest.json."""
    # manifest
    mp = ACC_DIR / label / "manifest.json"
    m  = json.loads(mp.read_text())
    m["sandboxId"] = sandbox_id
    mp.write_text(json.dumps(m, indent=2))
    # index
    accounts = load_index()
    for a in accounts:
        if a["label"] == label:
            a["sandboxId"] = sandbox_id
            break
    save_index(accounts)

# ── sandbox health ─────────────────────────────────────────────────────────────

def sandbox_alive(sandbox_id: str) -> bool:
    if not sandbox_id:
        return False
    s, _ = _http("GET", f"https://49999-{sandbox_id}.e2b.app/health", timeout=8)
    return s == 200

def ping_kernel(sandbox_id: str) -> bool:
    """Execute a no-op to keep the Jupyter kernel warm."""
    s, _ = _http("POST", f"https://49999-{sandbox_id}.e2b.app/execute",
                 body={"code": "1", "language": "python"}, timeout=10)
    return s == 200

# ── wake logic ────────────────────────────────────────────────────────────────

def wake_sandbox(label: str) -> str | None:
    """Send AI chat ping → poll until sandbox is live → return new sandboxId."""
    try:
        manifest = _manifest(label)
        thread_id  = manifest.get("threadId")
        project_id = manifest.get("projectId")
    except Exception as e:
        log.error("[%s] cannot read manifest: %s", label, e)
        return None

    if not thread_id or not project_id:
        log.warning("[%s] missing threadId/projectId in manifest — skipping", label)
        return None

    cookies = _cookies(label)
    headers = {
        "Cookie":    cookies,
        "Content-Type": "application/json",
        "Origin":    "https://app.obvious.ai",
        "Referer":   "https://app.obvious.ai/",
        "User-Agent": "Mozilla/5.0 obvious-keepalive/1.0",
    }

    # send minimal chat message to trigger sandbox allocation
    body = {
        "message":                  "1",
        "messageId":                uuid.uuid4().hex,
        "projectId":                project_id,
        "visibleRecords":           [],
        "fileIds":                  [],
        "modeId":                   "auto",
        "isIntentionalModeChange":  True,
        "timezone":                 "UTC",
        "deliveryMode":             "queued",
    }
    s, b = _http("POST", f"{API_BASE}/api/v2/agent/chat/{thread_id}", body, headers, timeout=20)
    if s != 200:
        log.error("[%s] wake ping failed: %s %s", label, s, b[:120])
        return None
    log.info("[%s] wake ping sent, polling for sandbox...", label)

    # poll project info until sandboxId appears and is not paused
    deadline = time.time() + WAKE_TIMEOUT
    while time.time() < deadline:
        time.sleep(4)
        s2, b2 = _http("GET", f"{API_BASE}/projects/{project_id}/info",
                        headers=headers, timeout=10)
        if s2 == 200:
            meta = json.loads(b2).get("metadata", {}).get("sandbox", {})
            sb_id  = meta.get("sandboxId")
            paused = meta.get("isPaused", True)
            if sb_id and not paused:
                log.info("[%s] sandbox live: %s", label, sb_id)
                _save_sandbox_id(label, sb_id)
                return sb_id
        else:
            log.warning("[%s] project info %s", label, s2)

    log.error("[%s] sandbox did not come up within %ds", label, WAKE_TIMEOUT)
    return None

# ── main loop ─────────────────────────────────────────────────────────────────

def tick() -> None:
    accounts = load_index()
    if not accounts:
        log.warning("index.json empty or missing at %s", ACC_DIR)
        return

    for acc in accounts:
        label     = acc.get("label", "?")
        sandbox_id = acc.get("sandboxId") or ""

        # skip accounts without a project configured
        if not (ACC_DIR / label / "manifest.json").exists():
            log.debug("[%s] no manifest, skipping", label)
            continue
        if not (ACC_DIR / label / "storage_state.json").exists():
            log.debug("[%s] no cookies, skipping", label)
            continue

        if sandbox_alive(sandbox_id):
            ping_kernel(sandbox_id)
            log.info("[%s] ✓ alive (%s)", label, sandbox_id)
        else:
            log.warning("[%s] ✗ dead (sandbox=%s) — waking", label, sandbox_id or "none")
            new_id = wake_sandbox(label)
            if new_id:
                log.info("[%s] ✓ revived → %s", label, new_id)
            else:
                log.error("[%s] ✗ revive failed", label)

def main() -> None:
    log.info("obvious-keepalive starting (interval=%ds acc_dir=%s)", PING_INTERVAL, ACC_DIR)
    while True:
        try:
            tick()
        except Exception as e:
            log.exception("tick() crashed: %s", e)
        log.info("sleeping %ds", PING_INTERVAL)
        time.sleep(PING_INTERVAL)

if __name__ == "__main__":
    main()
