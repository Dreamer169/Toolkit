#!/usr/bin/env python3
"""
obvious_autoprovision.py — 自动账号池补充守护进程
===================================================

修复（v2）
----------
1. 启动时清理孤儿 chrome-headless-shell 进程
2. SIGTERM 处理：通过进程组杀死子进程后再退出
3. MIN_POOL 上限检测：可用唯一 IP 端口耗尽时停止无效重试
4. PM2 watch 移除：通过 pm2 start --no-watch 注册

守护模式（watchdog）
-------------------
  python3 obvious_autoprovision.py --watch \\
      --min-active 2 --check-interval 600

一次性注册
---------
  python3 obvious_autoprovision.py --provision \\
      --label eu-test2 \\
      [--port 10823]

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
import signal
import subprocess
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

# 当前运行中的 provision 子进程（用于 SIGTERM 时 kill）
_active_proc: subprocess.Popen | None = None


# ─────────────────────────────────────────────────────────────────────────────
# 孤儿清理 & 信号处理
# ─────────────────────────────────────────────────────────────────────────────

def _cleanup_orphan_chrome() -> None:
    """启动时清理遗留的 chrome-headless-shell 孤儿进程。"""
    patterns = ["chrome-headless-shell", "chrome-headless-shell-linux64"]
    killed = 0
    for pat in patterns:
        r = subprocess.run(
            ["pkill", "-9", "-f", pat],
            capture_output=True,
        )
        if r.returncode == 0:
            killed += 1
    if killed:
        log.info("startup: cleaned orphan chrome-headless-shell processes")
        time.sleep(1)
    else:
        log.info("startup: no orphan chrome-headless-shell found")


def _sigterm_handler(signum: int, frame) -> None:
    """SIGTERM 接收时：先杀子进程组，再清理孤儿 Chrome，最后退出。"""
    global _active_proc
    log.info("SIGTERM received — cleaning up child processes")
    if _active_proc is not None:
        try:
            pgid = os.getpgid(_active_proc.pid)
            os.killpg(pgid, signal.SIGKILL)
            log.info("killed provision process group pgid=%d", pgid)
        except Exception as e:
            log.warning("failed to kill process group: %s", e)
            try:
                _active_proc.kill()
            except Exception:
                pass
    _cleanup_orphan_chrome()
    sys.exit(0)


signal.signal(signal.SIGTERM, _sigterm_handler)
signal.signal(signal.SIGINT,  _sigterm_handler)


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


def _free_port_count() -> int:
    """返回可用（未被使用）的代理端口数量。"""
    used = _used_ports()
    return sum(1 for p in range(PORT_START, PORT_END + 1) if p not in used)


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
    Tracks the subprocess in _active_proc so SIGTERM can kill it.
    """
    global _active_proc

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
    env["DISPLAY"] = env.get("DISPLAY", ":99")

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
        # start_new_session=True → 子进程在独立进程组，SIGTERM 时可用 killpg 一次清除
        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        _active_proc = proc

        try:
            stdout, stderr = proc.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            log.error("provision subprocess timed out (600s) — killing")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                proc.kill()
            proc.communicate()
            _active_proc = None
            return None
        finally:
            _active_proc = None

    except Exception as e:
        log.error("provision subprocess error: %s", e)
        _active_proc = None
        return None

    elapsed = round(time.time() - t0, 1)

    for line in (stdout or "").splitlines():
        log.info("  [provision] %s", line)
    for line in (stderr or "").splitlines():
        log.warning("  [provision:err] %s", line)

    if proc.returncode != 0:
        log.error("provision FAILED (rc=%d) in %.1fs", proc.returncode, elapsed)
        # 清理可能残留的 chrome 进程
        _cleanup_orphan_chrome()
        return None

    # 4. Read back the manifest
    mp = ACC_DIR / label / "manifest.json"
    if not mp.exists():
        log.error("manifest not found at %s — provision may have failed silently", mp)
        return None

    manifest = json.loads(mp.read_text())
    log.info("account provisioned OK: label=%s email=%s sandbox=%s (%.1fs)",
             label, manifest.get("email"), manifest.get("sandboxId"), elapsed)

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

    跳过条件：
    - active >= min_active（已满足）
    - 可用代理端口耗尽（避免无效循环）
    """
    active  = _active_accounts()
    current = len(active)
    need    = max(0, min_active - current)

    log.info("pool check: active=%d min=%d need=%d", current, min_active, need)

    if need == 0:
        return 0

    # ── 检查可用端口 ──────────────────────────────────────────────────────────
    free_ports = _free_port_count()
    if free_ports == 0:
        log.warning(
            "no free proxy ports available (used all %d-%d) — "
            "skipping provisioning; lower SB_MIN_POOL or expand port range",
            PORT_START, PORT_END,
        )
        return 0

    to_provision = min(need, max_provision, free_ports)
    log.info("will provision %d account(s) (free_ports=%d)", to_provision, free_ports)

    provisioned = 0
    for i in range(to_provision):
        log.info("provisioning account %d/%d …", i + 1, to_provision)
        m = provision_account(headless=headless)
        if m:
            provisioned += 1
            if i < to_provision - 1:
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
    Provisions new accounts when pool falls below `min_active`.

    修复：
    - 启动时清理孤儿 Chrome
    - SIGTERM 时通过 signal handler 干净退出
    - 端口耗尽时跳过注册（不再无限重试）
    """
    log.info("watchdog started min_active=%d interval=%ds headless=%s",
             min_active, check_interval, headless)

    # 启动时清理孤儿 Chrome
    _cleanup_orphan_chrome()

    while True:
        try:
            n = check_and_replenish(min_active=min_active, headless=headless)
            if n:
                log.info("watchdog provisioned %d new account(s) this cycle", n)
        except Exception as e:
            log.exception("watchdog cycle error: %s", e)

        log.info("watchdog sleeping %ds …", check_interval)
        time.sleep(check_interval)


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
