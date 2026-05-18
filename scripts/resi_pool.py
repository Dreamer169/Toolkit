#!/usr/bin/env python3
"""
resi_pool.py v2.0 -- Residential SOCKS5 Pool Manager
  + sticky sessions (nesting-proxy route-pool style)
  + external proxy injection (ip2free / proxyscrape)

Inspired by:
  easy_proxies (jasonwong1991): failure threshold + TTL blacklist + round-robin
  goproxy (elazarl): cascadeproxy single-endpoint multi-upstream SOCKS5
  nesting-proxy (ydddp): sticky routing per session key
"""
from __future__ import annotations
import subprocess, threading, time, concurrent.futures, json, os
from typing import List, Dict, Tuple, Union

RESI_CANDIDATE_PORTS: List[int] = list(range(10820, 10846))  # Fix-6: use in-socks VLESS/CF ports (10820-10845), old ss-in 10851-10859 dead
PROBE_TARGET    = "https://www.google.com/generate_204"
PROBE_TIMEOUT   = 6
PROBE_CACHE_TTL = 300
FAIL_THRESHOLD  = 3
BLACKLIST_TTL   = 300
SESSION_TTL     = 1800  # 30 min sticky binding
EXTERNAL_FILE   = "/tmp/resi_pool_external.json"
MAX_EXTERNALS   = 100

# ProxyRef: int = local xray port, str = "host:port" external
ProxyRef = Union[int, str]

_lock                = threading.Lock()
_healthy_ports:      List[int] = []
_last_good_healthy:  List[int] = []
_health_ts:          float = 0.0
_rr_idx:             int = 0
_fail_counts:        Dict[int, int] = {}
_blacklisted:        Dict[int, float] = {}

_externals:          List[str] = []
_ext_failed:         Dict[str, int] = {}
_ext_blacklist:      Dict[str, float] = {}

_sessions:           Dict[str, Tuple[ProxyRef, float]] = {}


# ── helpers ──────────────────────────────────────────────────────────────────

def proxy_url(ref: ProxyRef) -> str:
    if isinstance(ref, int):
        return f"socks5h://127.0.0.1:{ref}"
    return f"socks5h://{ref}"


def proxy_host_port(ref: ProxyRef) -> Tuple[str, int]:
    if isinstance(ref, int):
        return ("127.0.0.1", ref)
    # Strip user:pass@ auth prefix if present (format: user:pass@host:port)
    addr = ref.rsplit("@", 1)[-1]
    h, p = addr.rsplit(":", 1)
    return (h, int(p))


def add_external_full(auth_str: str, probe: bool = False) -> bool:
    """Add external proxy using full auth string: user:pass@host:port or host:port.
    Unlike add_external(), this preserves credentials for authenticated proxies."""
    if probe and not _probe_external(auth_str):
        return False
    with _lock:
        if auth_str not in _externals:
            _externals.append(auth_str)
            if len(_externals) > MAX_EXTERNALS:
                _externals.pop(0)
        _save_externals_file()
    return True


# ── probing ───────────────────────────────────────────────────────────────────

def _probe_port(port: int) -> bool:
    try:
        p = subprocess.Popen(
            ["curl", "-s", "--max-time", str(PROBE_TIMEOUT),
             "--proxy", f"socks5h://127.0.0.1:{port}",
             "-o", "/dev/null", "-w", "%{http_code}", PROBE_TARGET],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, _ = p.communicate(timeout=PROBE_TIMEOUT + 2)
        except subprocess.TimeoutExpired:
            p.kill(); p.communicate(); return False
        return out.decode().strip() not in ("", "000")
    except Exception:
        return False


def _probe_external(proxy_str: str) -> bool:
    try:
        p = subprocess.Popen(
            ["curl", "-s", "--max-time", str(PROBE_TIMEOUT),
             "--proxy", f"socks5h://{proxy_str}",
             "-o", "/dev/null", "-w", "%{http_code}", PROBE_TARGET],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, _ = p.communicate(timeout=PROBE_TIMEOUT + 2)
        except subprocess.TimeoutExpired:
            p.kill(); p.communicate(); return False
        return out.decode().strip() not in ("", "000")
    except Exception:
        return False


# ── external proxy management ─────────────────────────────────────────────────

def _load_externals_file() -> None:
    global _externals
    try:
        if os.path.exists(EXTERNAL_FILE):
            data = json.loads(open(EXTERNAL_FILE).read())
            with _lock:
                for entry in data.get("proxies", []):
                    if entry not in _externals:
                        _externals.append(entry)
    except Exception:
        pass



def reload_externals() -> int:
    """Re-read external proxy file; merge new entries into live pool.
    Safe to call repeatedly. Returns count of newly added proxies."""
    old_count = len(_externals)
    _load_externals_file()
    return len(_externals) - old_count

def _save_externals_file() -> None:
    try:
        with open(EXTERNAL_FILE, "w") as f:
            json.dump({"proxies": list(_externals), "ts": time.time()}, f)
    except Exception:
        pass


def add_external(host: str, port: int, probe: bool = False) -> bool:
    """Add external SOCKS5 proxy. probe=True tests connectivity first."""
    entry = f"{host}:{port}"
    if probe and not _probe_external(entry):
        return False
    with _lock:
        if entry not in _externals:
            _externals.append(entry)
            if len(_externals) > MAX_EXTERNALS:
                _externals.pop(0)
        _save_externals_file()
    return True


def remove_external(host: str, port: int) -> None:
    entry = f"{host}:{port}"
    with _lock:
        if entry in _externals:
            _externals.remove(entry)
        _ext_failed.pop(entry, None)
        _ext_blacklist.pop(entry, None)
        _save_externals_file()


def report_external_failure(proxy_str: str) -> None:
    with _lock:
        _ext_failed[proxy_str] = _ext_failed.get(proxy_str, 0) + 1
        if _ext_failed[proxy_str] >= FAIL_THRESHOLD:
            _ext_blacklist[proxy_str] = time.time() + BLACKLIST_TTL
            if proxy_str in _externals:
                _externals.remove(proxy_str)
            _save_externals_file()


def report_external_success(proxy_str: str) -> None:
    with _lock:
        _ext_failed[proxy_str] = 0
        _ext_blacklist.pop(proxy_str, None)


def _available_externals() -> List[str]:
    now = time.time()
    bl = {p for p, u in _ext_blacklist.items() if now < u}
    return [p for p in _externals if p not in bl]


# ── local port management ─────────────────────────────────────────────────────

def _expire_blacklist() -> None:
    now = time.time()
    for p in [k for k, v in list(_blacklisted.items()) if now >= v]:
        del _blacklisted[p]
        _fail_counts[p] = 0


def refresh(force: bool = False) -> List[int]:
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
            _last_good_healthy[:] = healthy
            _health_ts = time.time()
        return list(healthy)
    with _lock:
        fallback = list(_last_good_healthy) if _last_good_healthy else list(range(10851, 10860))
        bl = {p for p, u in _blacklisted.items() if time.time() < u}
        fallback = [p for p in fallback if p not in bl] or fallback
        _healthy_ports = fallback
        _health_ts = time.time()
    import sys as _sys
    print(f"[resi_pool] WARN: probe found 0 healthy — using fallback {fallback}", file=_sys.stderr)
    return list(fallback)


def _available() -> List[int]:
    now = time.time()
    bl = {p for p, u in _blacklisted.items() if now < u}
    return [p for p in _healthy_ports if p not in bl]


def pick(hint: int = 0) -> int:
    """Select local RESI port (backward compatible). hint>0 = deterministic hash."""
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
    global _healthy_ports
    with _lock:
        _fail_counts[port] = _fail_counts.get(port, 0) + 1
        if _fail_counts[port] >= FAIL_THRESHOLD:
            _blacklisted[port] = time.time() + BLACKLIST_TTL
            _healthy_ports = [p for p in _healthy_ports if p != port]


def report_success(port: int) -> None:
    with _lock:
        _fail_counts[port] = 0
        _blacklisted.pop(port, None)


# ── sticky session (nesting-proxy route-pool style) ───────────────────────────

def _clean_expired_sessions() -> None:
    now = time.time()
    expired = [k for k, (_, exp) in list(_sessions.items()) if now >= exp]
    for k in expired:
        del _sessions[k]


def _pick_any() -> ProxyRef:
    # Bug G fix: 优先 RESI 端口，仅在 RESI 全不可用时降级外部代理
    global _rr_idx
    avail_local = _available()
    if avail_local:
        idx = _rr_idx % len(avail_local)
        _rr_idx = (idx + 1) % len(avail_local)
        return avail_local[idx]
    avail_ext = _available_externals()
    if avail_ext:
        idx = _rr_idx % len(avail_ext)
        _rr_idx = (idx + 1) % len(avail_ext)
        return avail_ext[idx]
    return list(RESI_CANDIDATE_PORTS)[0]


def pick_sticky(session_key: str, ttl: int = SESSION_TTL) -> ProxyRef:
    """
    Sticky session pick (nesting-proxy route-pool concept).
    Same key returns same proxy within TTL. On expiry or dead proxy -> reassign.
    """
    now = time.time()
    with _lock:
        _clean_expired_sessions()
        if session_key in _sessions:
            ref, expires = _sessions[session_key]
            still_ok = False
            if isinstance(ref, int):
                still_ok = ref in _healthy_ports and ref not in _blacklisted
            else:
                bl = {p for p, u in _ext_blacklist.items() if now < u}
                still_ok = ref in _externals and ref not in bl
            if still_ok and now < expires:
                return ref
    # Refresh local pool if stale
    with _lock:
        needs_refresh = not _healthy_ports or time.time() - _health_ts >= PROBE_CACHE_TTL
    if needs_refresh:
        refresh()
    with _lock:
        ref = _pick_any()
        _sessions[session_key] = (ref, now + ttl)
    return ref


def release_session(session_key: str) -> None:
    with _lock:
        _sessions.pop(session_key, None)


def report_ref_failure(ref: ProxyRef) -> None:
    if isinstance(ref, int):
        report_failure(ref)
    else:
        report_external_failure(ref)


def report_ref_success(ref: ProxyRef) -> None:
    if isinstance(ref, int):
        report_success(ref)
    else:
        report_external_success(ref)


# ── status & startup ──────────────────────────────────────────────────────────

def status() -> dict:
    with _lock:
        now = time.time()
        bl_ttl = {p: round(u - now, 0) for p, u in _blacklisted.items() if now < u}
        avail = _available()
        avail_ext = _available_externals()
        sess_count = len([k for k, (_, exp) in _sessions.items() if now < exp])
        return {
            "healthy_local": list(_healthy_ports),
            "available_local": avail,
            "available_local_count": len(avail),
            "externals_total": len(_externals),
            "externals_available": len(avail_ext),
            "externals_sample": avail_ext[:5],
            "blacklisted_local": bl_ttl,
            "fail_counts_local": dict(_fail_counts),
            "active_sessions": sess_count,
            "cache_age_s": round(now - _health_ts, 1) if _health_ts else None,
            "candidates": RESI_CANDIDATE_PORTS,
        }


def startup_check(log_fn=print) -> List[int]:
    _load_externals_file()
    t0 = time.time()
    healthy = refresh(force=True)
    total = len(RESI_CANDIDATE_PORTS)
    with _lock:
        ext_count = len(_externals)
    log_fn(f"[resi_pool v2.0] startup: local={healthy} ({len(healthy)}/{total}), externals={ext_count}, elapsed={time.time()-t0:.1f}s")
    return healthy


if __name__ == "__main__":
    import json as _j
    healthy = startup_check()
    print(_j.dumps(status(), indent=2))
