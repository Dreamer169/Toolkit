#!/usr/bin/env python3
"""
resi_pool.py v1.0 -- Residential SOCKS5 Port Pool Manager

Inspired by:
  easy_proxies (jasonwong1991): failure threshold + TTL blacklist + round-robin
  goproxy (elazarl): cascadeproxy single-endpoint multi-upstream SOCKS5

Port ranges (xray.json):
  10851-10859: ss-in-1..9  -> Shadowsocks residential (confirmed alive)
  10870-10889: ps-in-0..19 -> SOCKS5 upstream (need probing)
"""
from __future__ import annotations
import subprocess, threading, time, concurrent.futures
from typing import List

RESI_CANDIDATE_PORTS: List[int] = list(range(10851, 10860)) + list(range(10870, 10890))
PROBE_TARGET    = "https://www.google.com/generate_204"
PROBE_TIMEOUT   = 6
PROBE_CACHE_TTL = 300  # 5 min (same as existing scripts)
FAIL_THRESHOLD  = 3    # easy_proxies: FailureThreshold
BLACKLIST_TTL   = 300  # easy_proxies: BlacklistDuration

_lock              = threading.Lock()
_healthy_ports:    List[int] = []
_last_good_healthy: List[int] = []   # fallback when live probe yields nothing
_health_ts:        float = 0.0
_rr_idx:           int = 0
_fail_counts:      dict = {}
_blacklisted:      dict = {}


def _probe_port(port: int) -> bool:
    try:
        p = subprocess.Popen(
            ["curl", "-s", "--max-time", str(PROBE_TIMEOUT),
             "--proxy", f"socks5h://127.0.0.1:{port}",
             "-o", "/dev/null", "-w", "%{http_code}",
             PROBE_TARGET],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, _ = p.communicate(timeout=PROBE_TIMEOUT + 2)
        except subprocess.TimeoutExpired:
            p.kill(); p.communicate(); return False
        return out.decode().strip() not in ("", "000")
    except Exception:
        return False


def _expire_blacklist() -> None:
    """Clear expired blacklist entries (caller holds _lock)."""
    now = time.time()
    for p in [k for k, v in list(_blacklisted.items()) if now >= v]:
        del _blacklisted[p]
        _fail_counts[p] = 0


def refresh(force: bool = False) -> List[int]:
    """Probe all candidate ports in parallel; update healthy list."""
    global _healthy_ports, _health_ts
    with _lock:
        if not force and _healthy_ports and time.time() - _health_ts < PROBE_CACHE_TTL:
            return list(_healthy_ports)
        _expire_blacklist()
    max_w = min(len(RESI_CANDIDATE_PORTS), 16)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_w) as ex:
        results = list(ex.map(_probe_port, RESI_CANDIDATE_PORTS))
    healthy = [p for p, ok in zip(RESI_CANDIDATE_PORTS, results) if ok]
    if healthy:
        with _lock:
            _healthy_ports = healthy
            _last_good_healthy[:] = healthy   # save for fallback
            _health_ts = time.time()
        return list(healthy)
    # Probe returned nothing — network blip or all ports dead
    # Use last known good list (safe fallback), else first-range ports (SS range)
    with _lock:
        fallback = list(_last_good_healthy) if _last_good_healthy else list(range(10851, 10860))
        bl = {p for p, u in _blacklisted.items() if time.time() < u}
        fallback = [p for p in fallback if p not in bl] or fallback
        _healthy_ports = fallback
        _health_ts = time.time()
    import sys as _sys; print(f"[resi_pool] WARN: probe found 0 healthy — using fallback {fallback}", file=_sys.stderr)
    return list(fallback)


def _available() -> List[int]:
    """Healthy ports not currently blacklisted (caller holds _lock)."""
    now = time.time()
    bl = {p for p, u in _blacklisted.items() if now < u}
    return [p for p in _healthy_ports if p not in bl]


def pick(hint: int = 0) -> int:
    """
    Select a healthy RESI SOCKS5 port.
    hint=0  -> round-robin (goproxy cascadeproxy style)
    hint>0  -> deterministic hash (same account -> same IP region)
    """
    global _rr_idx
    with _lock:
        if _healthy_ports and time.time() - _health_ts < PROBE_CACHE_TTL:
            avail = _available()
            if avail:
                if hint:
                    return avail[hint % len(avail)]
                idx = _rr_idx % len(avail)
                _rr_idx = (idx + 1) % len(avail)
                return avail[idx]
    healthy = refresh()
    with _lock:
        avail = _available()
        if not avail:
            avail = healthy or list(RESI_CANDIDATE_PORTS)
        if hint:
            return avail[hint % len(avail)]
        idx = _rr_idx % len(avail)
        _rr_idx = (idx + 1) % len(avail)
        return avail[idx]


def report_failure(port: int) -> None:
    """
    Record port failure (easy_proxies FailureThreshold logic).
    After FAIL_THRESHOLD consecutive failures -> blacklist BLACKLIST_TTL sec.
    """
    global _healthy_ports
    with _lock:
        _fail_counts[port] = _fail_counts.get(port, 0) + 1
        if _fail_counts[port] >= FAIL_THRESHOLD:
            _blacklisted[port] = time.time() + BLACKLIST_TTL
            _healthy_ports = [p for p in _healthy_ports if p != port]


def report_success(port: int) -> None:
    """Reset failure count; remove from blacklist."""
    with _lock:
        _fail_counts[port] = 0
        _blacklisted.pop(port, None)


def status() -> dict:
    """Return pool status dict for monitoring."""
    with _lock:
        now = time.time()
        bl_ttl = {p: round(u - now, 0) for p, u in _blacklisted.items() if now < u}
        avail = _available()
        return {
            "healthy": list(_healthy_ports),
            "available": avail,
            "available_count": len(avail),
            "blacklisted": bl_ttl,
            "fail_counts": dict(_fail_counts),
            "cache_age_s": round(now - _health_ts, 1) if _health_ts else None,
            "candidates": RESI_CANDIDATE_PORTS,
        }


def startup_check(log_fn=print) -> List[int]:
    """Full probe at process startup."""
    t0 = time.time()
    healthy = refresh(force=True)
    total = len(RESI_CANDIDATE_PORTS)
    log_fn(f"[resi_pool] startup: alive={healthy} ({len(healthy)}/{total}) in {time.time()-t0:.1f}s")
    return healthy


if __name__ == "__main__":
    import json as _j
    healthy = startup_check()
    print(_j.dumps(status(), indent=2))
