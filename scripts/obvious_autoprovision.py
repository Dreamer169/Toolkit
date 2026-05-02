#!/usr/bin/env python3
"""
obvious_autoprovision.py — 自动账号池补充守护进程
===================================================

功能
----
1. 检测活跃账号数量；当活跃账号 < MIN_POOL 时自动注册新账号
2. 挑选下一个空闲的 xray SOCKS5 端口（按 index.json 已用端口避开）
3. 用 mailtm_client.py 创建 deltajohnsons.com 邮箱
4. 调用 obvious_provision.py（subprocess）完成 Playwright 注册
5. 新账号自动入 index.json；keepalive 下次循环即可接管
6. 每次注册前先检测代理出口 IP，确保与已有账号不重复

守护模式（watchdog）
-------------------
  python3 obvious_autoprovision.py --watch \\
      --min-active 2 --check-interval 600

一次性注册
---------
  python3 obvious_autoprovision.py --provision \\
      --label eu-test2 \\
      [--port 10823]           # 不写则自动选

环境变量
--------
  SB_ACC_DIR          账号目录（默认 /root/obvious-accounts）
  SB_MIN_POOL         最小活跃账号数（默认 2）
  SB_CHECK_INTERVAL   watchdog 检查间隔秒（默认 600）
  SB_PORT_START       xray 端口起始（默认 10820）
  SB_PORT_END         xray 端口结束含（默认 10844）
  DISPLAY             Xvfb 显示号（默认 :99，headless 模式时忽略）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

ACC_DIR        = Path(os.environ.get("SB_ACC_DIR",       "/root/obvious-accounts"))
MIN_POOL       = int(os.environ.get("SB_MIN_POOL",       "2"))
CHECK_INTERVAL = int(os.environ.get("SB_CHECK_INTERVAL", "600"))
PORT_START     = int(os.environ.get("SB_PORT_START",     "10820"))
PORT_END       = int(os.environ.get("SB_PORT_END",       "10844"))

SCRIPTS_DIR    = Path(__file__).resolve().parent
PROVISION_PY   = SCRIPTS_DIR / "obvious_provision.py"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s autoprovision %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Index helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_index() -> list[dict]:
    p = ACC_DIR / "index.json"
    try:
        return json.loads(p.read_text()) if p.exists() else []
    except Exception:
        return []


def _active_accounts() -> list[dict]:
    result = []
    for entry in _load_index():
        label = entry.get("label", "")
        mp = ACC_DIR / label / "manifest.json"
        if not mp.exists():
            continue
        m = json.loads(mp.read_text())
        if m.get("status") != "dead":
            result.append(entry)
    return result


def _used_ports() -> set[int]:
    ports: set[int] = set()
    for entry in _load_index():
        proxy = entry.get("proxy", "")
        if proxy:
            m = re.search(r":(\d+)$", proxy)
            if m:
                ports.add(int(m.group(1)))
    return ports


def _used_egress_ips() -> set[str]:
    ips: set[str] = set()
    for entry in _load_index():
        label = entry.get("label", "")
        mp = ACC_DIR / label / "manifest.json"
        if not mp.exists():
            continue
        m = json.loads(mp.read_text())
        ip = m.get("egressIp", "")
        if ip and ip not in ("?", "err"):
            ips.add(ip)
    return ips


# ─────────────────────────────────────────────────────────────────────────────
# Port / IP selection
# ─────────────────────────────────────────────────────────────────────────────

def _probe_egress_ip(port: int, timeout: float = 12.0) -> str:
    """Probe actual egress IP through SOCKS5 port using curl."""
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", str(int(timeout)),
             "--socks5-hostname", f"127.0.0.1:{port}",
             "https://api.ipify.org"],
            capture_output=True, text=True, timeout=timeout + 3,
        )
        ip = (r.stdout or "").strip()
        return ip if ip and re.match(r"^\d+\.\d+\.\d+\.\d+$", ip) else "?"
    except Exception as e:
        return f"err:{type(e).__name__}"


def find_free_port(check_unique_ip: bool = True) -> tuple[int, str] | None:
    """
    Return (port, egress_ip) for the first free xray port whose egress IP
    isn't already used by another account.  Returns None if none found.
    """
    used_ports = _used_ports()
    used_ips   = _used_egress_ips() if check_unique_ip else set()

    for port in range(PORT_START, PORT_END + 1):
        if port in used_ports:
            continue
        log.info("checking port %d for unique egress IP …", port)
        ip = _probe_egress_ip(port)
        if ip.startswith("err") or ip == "?":
            log.warning("port %d unreachable (%s)", port, ip)
            continue
        if ip in used_ips:
            log.info("port %d egress %s already used — skip", port, ip)
            continue
        log.info("port %d → egress IP %s (unique ✓)", port, ip)
        return port, ip

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Label generation
# ─────────────────────────────────────────────────────────────────────────────

_REGIONS = ["us", "sg", "eu", "au", "jp", "ca", "br", "uk"]

def _next_label() -> str:
    """Generate label like us-auto-3 (next unused in sequence)."""
    existing = {e.get("label", "") for e in _load_index()}
    for region in _REGIONS:
        for n in range(1, 50):
            candidate = f"{region}-auto-{n}"
            if candidate not in existing:
                return candidate
    return f"acc-{int(time.time())}"


# ─────────────────────────────────────────────────────────────────────────────
# Core provisioning (subprocess)
# ─────────────────────────────────────────────────────────────────────────────

def provision_account(label: str | None = None,
                      port: int | None = None,
                      headless: bool = True) -> dict | None:
    """
    Register a new obvious.ai account using the given (or auto-selected) proxy port.

    Runs obvious_provision.py in a subprocess so Playwright has its own
    clean event loop.  Returns the manifest dict on success, None on failure.
    """
    # 1. Find port + egress IP
    if port is None:
        result = find_free_port(check_unique_ip=True)
        if result is None:
            log.error("no free proxy port with unique egress IP available")
            return None
        port, egress_ip = result
    else:
        egress_ip = _probe_egress_ip(port)

    # 2. Label
    if label is None:
        label = _next_label()

    proxy_url = f"socks5://127.0.0.1:{port}"
    log.info("provisioning account label=%s port=%d ip=%s", label, port, egress_ip)

    # 3. Build subprocess command
    env = dict(os.environ)
    env["DISPLAY"] = env.get("DISPLAY", ":99")   # xvfb

    cmd = [
        sys.executable, str(PROVISION_PY),
        "--proxy", proxy_url,
        "--out-dir", str(ACC_DIR),
        "--label", label,
        "--check-ip",
    ]
    if headless:
        cmd.append("--headless")

    log.info("running: %s", " ".join(cmd))
    t0 = time.time()

    try:
        proc = subprocess.run(
            cmd, env=env, timeout=600,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        log.error("provision subprocess timed out (600s)")
        return None
    except Exception as e:
        log.error("provision subprocess error: %s", e)
        return None

    elapsed = round(time.time() - t0, 1)
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    # Echo output
    for line in stdout.splitlines():
        log.info("  [provision] %s", line)
    for line in stderr.splitlines():
        log.warning("  [provision:err] %s", line)

    if proc.returncode != 0:
        log.error("provision FAILED (rc=%d) in %.1fs", proc.returncode, elapsed)
        return None

    # 4. Read back the manifest that provision.py wrote
    mp = ACC_DIR / label / "manifest.json"
    if not mp.exists():
        log.error("manifest not found at %s — provision may have failed silently", mp)
        return None

    manifest = json.loads(mp.read_text())
    log.info("account provisioned OK: label=%s email=%s sandbox=%s (%.1fs)",
             label, manifest.get("email"), manifest.get("sandboxId"), elapsed)

    # 5. Update egressIp in manifest if provision.py stored "(skipped)"
    if manifest.get("egressIp") in (None, "?", "(skipped)"):
        manifest["egressIp"] = egress_ip
        mp.write_text(json.dumps(manifest, indent=2))

    return manifest


# ─────────────────────────────────────────────────────────────────────────────
# Pool health check + replenishment
# ─────────────────────────────────────────────────────────────────────────────

def check_and_replenish(min_active: int = MIN_POOL,
                        max_provision: int = 2,
                        headless: bool = True) -> int:
    """
    If active account count < min_active, provision new ones up to max_provision
    per call to avoid hammering obvious.ai.
    Returns number of accounts successfully provisioned.
    """
    active = _active_accounts()
    current = len(active)
    need    = max(0, min_active - current)

    log.info("pool check: active=%d min=%d need=%d", current, min_active, need)

    if need == 0:
        return 0

    provisioned = 0
    for i in range(min(need, max_provision)):
        log.info("provisioning account %d/%d …", i + 1, min(need, max_provision))
        m = provision_account(headless=headless)
        if m:
            provisioned += 1
            # Small delay between registrations to avoid burst detection
            if i < need - 1:
                log.info("waiting 90s before next registration …")
                time.sleep(90)
        else:
            log.error("provisioning attempt %d failed — stopping this cycle", i + 1)
            break

    return provisioned


# ─────────────────────────────────────────────────────────────────────────────
# Watchdog daemon
# ─────────────────────────────────────────────────────────────────────────────

def watchdog(min_active: int = MIN_POOL,
             check_interval: int = CHECK_INTERVAL,
             headless: bool = True) -> None:
    """
    Infinite loop: check pool every `check_interval` seconds.
    Provision new accounts when pool falls below `min_active`.
    Designed to run under PM2 (will be restarted on crash).
    """
    log.info("watchdog started min_active=%d interval=%ds headless=%s",
             min_active, check_interval, headless)
    while True:
        try:
            n = check_and_replenish(min_active=min_active, headless=headless)
            if n:
                log.info("watchdog provisioned %d new account(s) this cycle", n)
        except KeyboardInterrupt:
            log.info("autoprovision watchdog shutdown")
            sys.exit(0)
        except Exception as e:
            log.exception("watchdog cycle error: %s", e)
        log.info("watchdog sleeping %ds …", check_interval)
        try:
            time.sleep(check_interval)
        except KeyboardInterrupt:
            log.info("autoprovision watchdog shutdown (SIGTERM)")
            sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="obvious.ai 自动账号池管理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 守护模式（PM2 管理，最少保持 2 个活跃账号）
  python3 obvious_autoprovision.py --watch --min-active 2

  # 立即注册一个新账号（自动选端口）
  python3 obvious_autoprovision.py --provision

  # 指定 label 和端口
  python3 obvious_autoprovision.py --provision --label eu-test2 --port 10823

  # 查看当前池状态
  python3 obvious_autoprovision.py --status
""",
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--watch",     action="store_true", help="守护模式：持续监控并补充")
    mode.add_argument("--provision", action="store_true", help="立即注册一个新账号")
    mode.add_argument("--status",    action="store_true", help="显示当前池状态")

    ap.add_argument("--min-active",     type=int,   default=MIN_POOL,       help=f"最小活跃账号数 (默认 {MIN_POOL})")
    ap.add_argument("--check-interval", type=int,   default=CHECK_INTERVAL, help=f"watchdog 检查间隔秒 (默认 {CHECK_INTERVAL})")
    ap.add_argument("--label",          default=None,   help="账号标签 (--provision 时)")
    ap.add_argument("--port",           type=int, default=None, help="强制使用指定代理端口 (--provision 时)")
    ap.add_argument("--no-headless",    action="store_true", help="显示 Chromium 窗口（调试用，需 Xvfb）")

    args = ap.parse_args()
    headless = not args.no_headless

    if args.status:
        _cmd_status()
    elif args.provision:
        m = provision_account(label=args.label, port=args.port, headless=headless)
        if m:
            print("\n=== provisioned ===")
            print(json.dumps(m, indent=2))
        else:
            print("\n=== FAILED ===", file=sys.stderr)
            sys.exit(1)
    elif args.watch:
        watchdog(
            min_active=args.min_active,
            check_interval=args.check_interval,
            headless=headless,
        )


def _cmd_status() -> None:
    idx = _load_index()
    active = _active_accounts()
    used_ports = _used_ports()
    free_ports = [p for p in range(PORT_START, PORT_END + 1) if p not in used_ports]

    print(f"\n{'─'*55}")
    print(f"obvious.ai 账号池状态  ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    print(f"{'─'*55}")
    print(f"总账号: {len(idx)}   活跃: {len(active)}   最小保持: {MIN_POOL}")
    print(f"空闲代理端口: {len(free_ports)}  ({free_ports[:5]}…)")
    print()

    for entry in idx:
        label = entry.get("label", "?")
        mp = ACC_DIR / label / "manifest.json"
        if not mp.exists():
            print(f"  [{label}]  ⚠ manifest missing")
            continue
        m = json.loads(mp.read_text())
        status   = m.get("status", "active")
        email    = m.get("email", "?")
        proxy    = m.get("proxy", "NONE")
        egress   = m.get("egressIp", "?")
        sandbox  = str(m.get("sandboxId") or "—")[:12]
        credits  = m.get("creditBalance", "?")
        created  = m.get("createdAt", "?")[:10]
        icon     = "✅" if status != "dead" else "💀"
        print(f"  {icon} [{label}]  {email}")
        print(f"       status={status}  proxy={proxy}  egress={egress}")
        print(f"       sandbox={sandbox}  credits={credits}  created={created}")
    print(f"{'─'*55}\n")


if __name__ == "__main__":
    main()
