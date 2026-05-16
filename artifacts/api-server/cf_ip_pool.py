import random, socket, time, threading, json, os
from ipaddress import ip_network

CF_IP_RANGES = [
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22',
    '141.101.64.0/18', '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20',
    '197.234.240.0/22', '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13',
    '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
]

POOL_STATE_FILE = '/var/lib/toolkit/cf_pool_state.json'
ARKOSE_STATS_FILE = '/var/lib/toolkit/cf_arkose_stats.json'
_pool_lock = threading.Lock()
_available = []
_in_use = {}
_used_history = []
_banned_ips: set = set()
_arkose_stats: dict = {}  # {'/20-net-str': {'ok': N, 'fail': N}}


def _entry_ip(entry):
    return entry.get('ip') if isinstance(entry, dict) else None


def _normalise_available(items):
    out = []
    seen = set()
    blocked = set(_banned_ips)  # v9.92: used_history removed — CF IPs are reusable across sessions
    for item in items or []:
        if not isinstance(item, dict):
            continue
        ip = item.get('ip')
        lat = item.get('latency')
        if not isinstance(ip, str) or not isinstance(lat, (int, float)):
            continue
        if ip in seen or ip in blocked:
            continue
        seen.add(ip)
        out.append({'ip': ip, 'latency': lat, 'proxy': item.get('proxy') or f'http://{ip}:443'})
    out.sort(key=lambda x: x['latency'])
    return out


def _read_state():
    try:
        if not os.path.exists(POOL_STATE_FILE):
            return {}
        with open(POOL_STATE_FILE, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state_nolock(extra_banned: list | None = None):
    """在已持有文件锁时调用（不再重复 flock，否则死锁）。"""
    try:
        import tempfile as _tmp, shutil as _shu
        if extra_banned:
            _banned_ips.update([x for x in extra_banned if x])
        hist      = list(dict.fromkeys(_used_history))[-500:]  # v9.92: reduced cap
        banned    = list(dict.fromkeys(_banned_ips))[-2000:]
        available = _normalise_available(_available)
        payload   = {
            "available": available,
            "used_history": hist,
            "history": hist,
            "history_count": len(hist),
            "banned": banned,
        }
        fd_tmp, tmp_path = _tmp.mkstemp(
            dir=os.path.dirname(POOL_STATE_FILE) or ".", suffix=".tmp")
        with os.fdopen(fd_tmp, "w") as f:
            json.dump(payload, f)
        _shu.move(tmp_path, POOL_STATE_FILE)
    except Exception:
        pass


def _save_state(extra_banned: list | None = None):
    # v9.31 Fix: flock + 原子 replace，仅在外部直接调用时加锁
    try:
        import fcntl
        lock_path = POOL_STATE_FILE + ".lock"
        with open(lock_path, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                _save_state_nolock(extra_banned)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except Exception:
        pass


def _load_state():
    global _available, _used_history
    data = _read_state()
    history = data.get('used_history') or data.get('history') or []
    banned = data.get('banned') or []
    _used_history = [x for x in history if isinstance(x, str)]
    _banned_ips.update([x for x in banned if isinstance(x, str)])
    _available = _normalise_available(data.get('available') or [])


_load_state()


def get_pool_status() -> dict:
    with _pool_lock:
        return {
            'available': len(_available),
            'in_use': len(_in_use),
            'used_total': len(_used_history),
            'banned_total': len(_banned_ips),
            'pool': [{'ip': x['ip'], 'latency': x['latency']} for x in _available[:20]],
        }


def _subnet20(ip: str) -> str:
    """Return /20 network string for an IP (e.g. '104.16.0.0/20')."""
    try:
        from ipaddress import ip_address, ip_network as _ipn
        addr = int(ip_address(ip))
        base = addr & 0xFFFFF000  # mask to /20
        b = [(base >> s) & 0xFF for s in (24, 16, 8, 0)]
        return f"{b[0]}.{b[1]}.{b[2]}.{b[3]}/20"
    except Exception:
        return "unknown"


def _load_arkose_stats():
    global _arkose_stats
    try:
        if os.path.exists(ARKOSE_STATS_FILE):
            with open(ARKOSE_STATS_FILE, "r") as f:
                _arkose_stats = json.load(f)
    except Exception:
        _arkose_stats = {}


def _save_arkose_stats():
    try:
        import tempfile as _tmp, shutil as _shu
        fd, tmp = _tmp.mkstemp(dir=os.path.dirname(ARKOSE_STATS_FILE) or ".", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(_arkose_stats, f)
        _shu.move(tmp, ARKOSE_STATS_FILE)
    except Exception:
        pass


def record_arkose_result(ip: str, success: bool):
    """v9.91: Record Arkose pass/fail for this IP's /20 subnet for weighted generation."""
    subnet = _subnet20(ip)
    with _pool_lock:
        if subnet not in _arkose_stats:
            _arkose_stats[subnet] = {"ok": 0, "fail": 0}
        _arkose_stats[subnet]["ok" if success else "fail"] += 1
        _save_arkose_stats()
    result_str = "✅ok" if success else "❌fail"
    print(f"[cf_pool] v9.91 Arkose subnet record: {subnet} → {result_str}  "
          f"(ok={_arkose_stats[subnet]['ok']} fail={_arkose_stats[subnet]['fail']})", flush=True)


def _net_arkose_weight(net) -> float:
    """v9.91: Weight for sampling from this network, based on Arkose success history."""
    from ipaddress import ip_network as _ipn
    # Sample a few /20 subnets from this network and average their weights
    try:
        na = int(net.network_address)
        # Check 4 /20 anchors within the network
        weights = []
        size = net.num_addresses
        for frac in [0, 0.25, 0.5, 0.75]:
            anchor_int = na + int(frac * min(size - 1, 65535))
            anchor_int &= 0xFFFFF000  # snap to /20
            b = [(anchor_int >> s) & 0xFF for s in (24, 16, 8, 0)]
            sn = f"{b[0]}.{b[1]}.{b[2]}.{b[3]}/20"
            stats = _arkose_stats.get(sn, {})
            ok = stats.get("ok", 0)
            fail = stats.get("fail", 0)
            total = ok + fail
            if total < 3:
                w = 0.65  # unknown: slightly pessimistic to favour known-good
            else:
                rate = ok / total
                w = max(0.05, rate)  # floor at 5% so no range is permanently excluded
            weights.append(w)
        return sum(weights) / len(weights)
    except Exception:
        return 0.65


_load_arkose_stats()


def generate_cf_ips(count: int = 60) -> list:
    networks = [ip_network(r, strict=False) for r in CF_IP_RANGES]
    with _pool_lock:
        seen = set(_banned_ips) | {_entry_ip(x) for x in _available}  # v9.92: drop used_history filter
    seen.discard(None)
    ips = []
    attempts = 0
    # v9.91: weight network selection by historical Arkose success rate
    _net_weights = [_net_arkose_weight(n) for n in networks]
    while len(ips) < count and attempts < count * 30:
        attempts += 1
        net = random.choices(networks, weights=_net_weights, k=1)[0]
        offset = random.randint(1, min(net.num_addresses - 2, 65535))
        ip = str(net.network_address + offset)
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips


def test_ip_latency(ip: str, port: int = 443, timeout: float = 3.0):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        t0 = time.time()
        err = s.connect_ex((ip, port))
        lat = round((time.time() - t0) * 1000, 1)
        s.close()
        return lat if err == 0 else None
    except Exception:
        return None


def refresh_pool(generate_count: int = 60, target_valid: int = 20, threads: int = 5, port: int = 443, max_latency: float = 800.0, log_cb=None) -> list:
    if log_cb:
        log_cb(f'🔄 生成 {generate_count} 个 CF IP 并测速（端口{port}，线程{threads}）…')
    candidates = generate_cf_ips(generate_count)
    results = []
    lock = threading.Lock()
    sem = threading.Semaphore(max(1, threads))

    def worker(ip):
        with sem:
            lat = test_ip_latency(ip, port)
            if lat is not None and lat <= max_latency:
                with lock:
                    results.append({'ip': ip, 'latency': lat, 'proxy': f'http://{ip}:{port}'})

    ts = [threading.Thread(target=worker, args=(ip,), daemon=True) for ip in candidates]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)

    results.sort(key=lambda x: x['latency'])
    new_ips = results[:target_valid]

    with _pool_lock:
        existing = {_entry_ip(x) for x in _available}
        for item in new_ips:
            if item['ip'] not in existing and item['ip'] not in _banned_ips:  # v9.92
                _available.append(item)
                existing.add(item['ip'])
        _available.sort(key=lambda x: x['latency'])
        _save_state()

    if log_cb:
        log_cb(f'✅ 测速完成：{len(candidates)} 个候选，{len(new_ips)} 个有效（≤{max_latency}ms）入池')
    return new_ips


def acquire_ip(job_id: str, auto_refresh: bool = False, log_cb=None) -> dict | None:
    # v9.31: 跨进程安全由 Fix2(父进程统一预分配) 保证；同进程用 threading.Lock 即可。
    # _save_state 已加 flock 原子写，此处无需额外 flock。
    with _pool_lock:
        _available[:] = _normalise_available(_available)
        if _available:
            ip_info = _available.pop(0)
            _in_use[job_id] = ip_info
            if ip_info["ip"] not in _used_history:
                _used_history.append(ip_info["ip"])
            _save_state_nolock()
            return ip_info

    if auto_refresh:
        if log_cb:
            log_cb("CF 池为空，按需刷新…")
        refresh_pool(log_cb=log_cb)
        with _pool_lock:
            _available[:] = _normalise_available(_available)
            if _available:
                ip_info = _available.pop(0)
                _in_use[job_id] = ip_info
                if ip_info["ip"] not in _used_history:
                    _used_history.append(ip_info["ip"])
                _save_state_nolock()
                return ip_info
    return None

def release_ip(job_id: str):
    with _pool_lock:
        _in_use.pop(job_id, None)
        _save_state()


def ban_ip(ip: str):
    with _pool_lock:
        before = len(_available)
        _available[:] = [x for x in _available if x.get('ip') != ip]
        removed = before - len(_available)
        if ip and ip not in _used_history:
            _used_history.append(ip)
        _save_state(extra_banned=[ip])
    return removed


def retest_pool(max_latency: float = 800.0, threads: int = 8, port: int = 443, log_cb=None) -> dict:
    with _pool_lock:
        candidates = list(_normalise_available(_available))
    if not candidates:
        _save_state()
        return {'kept': 0, 'removed': 0}
    if log_cb:
        log_cb(f'🔍 重测 {len(candidates)} 个 CF IP（port {port}，延迟≤{max_latency}ms）…')
    results = []
    lock = threading.Lock()
    sem = threading.Semaphore(max(1, threads))

    def worker(entry):
        with sem:
            lat = test_ip_latency(entry['ip'], port)
            with lock:
                if lat is not None and lat <= max_latency:
                    results.append({**entry, 'latency': lat})
                else:
                    _banned_ips.add(entry['ip'])

    ts = [threading.Thread(target=worker, args=(e,), daemon=True) for e in candidates]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=15)

    results.sort(key=lambda x: x['latency'])
    removed = len(candidates) - len(results)
    with _pool_lock:
        _available.clear()
        _available.extend(results)
        _save_state()
    if log_cb:
        log_cb(f'✅ 重测完成：保留 {len(results)} 个，移除 {removed} 个无效 IP')
    return {'kept': len(results), 'removed': removed}
