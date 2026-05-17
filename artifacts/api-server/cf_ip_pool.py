import random, socket, time, threading, json, os
from ipaddress import ip_network

CF_IP_RANGES = [
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22', '141.101.64.0/20', '141.101.80.0/20',
    '141.101.96.0/20', '141.101.112.0/20', '108.162.192.0/20', '108.162.208.0/20', '108.162.224.0/20', '108.162.240.0/20',
    '190.93.240.0/20', '188.114.96.0/20', '197.234.240.0/22', '198.41.128.0/20', '198.41.144.0/20', '198.41.160.0/20',
    '198.41.176.0/20', '198.41.192.0/20', '198.41.208.0/20', '198.41.224.0/20', '198.41.240.0/20', '162.158.0.0/20',
    '162.158.16.0/20', '162.158.32.0/20', '162.158.48.0/20', '162.158.64.0/20', '162.158.80.0/20', '162.158.96.0/20',
    '162.158.112.0/20', '162.158.128.0/20', '162.158.144.0/20', '162.158.160.0/20', '162.158.176.0/20', '162.158.192.0/20',
    '162.158.208.0/20', '162.158.224.0/20', '162.158.240.0/20', '162.159.0.0/20', '162.159.16.0/20', '162.159.32.0/20',
    '162.159.48.0/20', '162.159.64.0/20', '162.159.80.0/20', '162.159.96.0/20', '162.159.112.0/20', '162.159.128.0/20',
    '162.159.144.0/20', '162.159.160.0/20', '162.159.176.0/20', '162.159.192.0/20', '162.159.208.0/20', '162.159.224.0/20',
    '162.159.240.0/20', '104.16.0.0/20', '104.16.16.0/20', '104.16.32.0/20', '104.16.48.0/20', '104.16.64.0/20',
    '104.16.80.0/20', '104.16.96.0/20', '104.16.112.0/20', '104.16.128.0/20', '104.16.144.0/20', '104.16.160.0/20',
    '104.16.176.0/20', '104.16.192.0/20', '104.16.208.0/20', '104.16.224.0/20', '104.16.240.0/20', '104.17.0.0/20',
    '104.17.16.0/20', '104.17.32.0/20', '104.17.48.0/20', '104.17.64.0/20', '104.17.80.0/20', '104.17.96.0/20',
    '104.17.112.0/20', '104.17.128.0/20', '104.17.144.0/20', '104.17.160.0/20', '104.17.176.0/20', '104.17.192.0/20',
    '104.17.208.0/20', '104.17.224.0/20', '104.17.240.0/20', '104.18.0.0/20', '104.18.16.0/20', '104.18.32.0/20',
    '104.18.48.0/20', '104.18.64.0/20', '104.18.80.0/20', '104.18.96.0/20', '104.18.112.0/20', '104.18.128.0/20',
    '104.18.144.0/20', '104.18.160.0/20', '104.18.176.0/20', '104.18.192.0/20', '104.18.208.0/20', '104.18.224.0/20',
    '104.18.240.0/20', '104.19.0.0/20', '104.19.16.0/20', '104.19.32.0/20', '104.19.48.0/20', '104.19.64.0/20',
    '104.19.80.0/20', '104.19.96.0/20', '104.19.112.0/20', '104.19.128.0/20', '104.19.144.0/20', '104.19.160.0/20',
    '104.19.176.0/20', '104.19.192.0/20', '104.19.208.0/20', '104.19.224.0/20', '104.19.240.0/20', '104.20.0.0/20',
    '104.20.16.0/20', '104.20.32.0/20', '104.20.48.0/20', '104.20.64.0/20', '104.20.80.0/20', '104.20.96.0/20',
    '104.20.112.0/20', '104.20.128.0/20', '104.20.144.0/20', '104.20.160.0/20', '104.20.176.0/20', '104.20.192.0/20',
    '104.20.208.0/20', '104.20.224.0/20', '104.20.240.0/20', '104.21.0.0/20', '104.21.16.0/20', '104.21.32.0/20',
    '104.21.48.0/20', '104.21.64.0/20', '104.21.80.0/20', '104.21.96.0/20', '104.21.112.0/20', '104.21.128.0/20',
    '104.21.144.0/20', '104.21.160.0/20', '104.21.176.0/20', '104.21.192.0/20', '104.21.208.0/20', '104.21.224.0/20',
    '104.21.240.0/20', '104.22.0.0/20', '104.22.16.0/20', '104.22.32.0/20', '104.22.48.0/20', '104.22.64.0/20',
    '104.22.80.0/20', '104.22.96.0/20', '104.22.112.0/20', '104.22.128.0/20', '104.22.144.0/20', '104.22.160.0/20',
    '104.22.176.0/20', '104.22.192.0/20', '104.22.208.0/20', '104.22.224.0/20', '104.22.240.0/20', '104.23.0.0/20',
    '104.23.16.0/20', '104.23.32.0/20', '104.23.48.0/20', '104.23.64.0/20', '104.23.80.0/20', '104.23.96.0/20',
    '104.23.112.0/20', '104.23.128.0/20', '104.23.144.0/20', '104.23.160.0/20', '104.23.176.0/20', '104.23.192.0/20',
    '104.23.208.0/20', '104.23.224.0/20', '104.23.240.0/20', '104.24.0.0/20', '104.24.16.0/20', '104.24.32.0/20',
    '104.24.48.0/20', '104.24.64.0/20', '104.24.80.0/20', '104.24.96.0/20', '104.24.112.0/20', '104.24.128.0/20',
    '104.24.144.0/20', '104.24.160.0/20', '104.24.176.0/20', '104.24.192.0/20', '104.24.208.0/20', '104.24.224.0/20',
    '104.24.240.0/20', '104.25.0.0/20', '104.25.16.0/20', '104.25.32.0/20', '104.25.48.0/20', '104.25.64.0/20',
    '104.25.80.0/20', '104.25.96.0/20', '104.25.112.0/20', '104.25.128.0/20', '104.25.144.0/20', '104.25.160.0/20',
    '104.25.176.0/20', '104.25.192.0/20', '104.25.208.0/20', '104.25.224.0/20', '104.25.240.0/20', '104.26.0.0/20',
    '104.26.16.0/20', '104.26.32.0/20', '104.26.48.0/20', '104.26.64.0/20', '104.26.80.0/20', '104.26.96.0/20',
    '104.26.112.0/20', '104.26.128.0/20', '104.26.144.0/20', '104.26.160.0/20', '104.26.176.0/20', '104.26.192.0/20',
    '104.26.208.0/20', '104.26.224.0/20', '104.26.240.0/20', '104.27.0.0/20', '104.27.16.0/20', '104.27.32.0/20',
    '104.27.48.0/20', '104.27.64.0/20', '104.27.80.0/20', '104.27.96.0/20', '104.27.112.0/20', '104.27.128.0/20',
    '104.27.144.0/20', '104.27.160.0/20', '104.27.176.0/20', '104.27.192.0/20', '104.27.208.0/20', '104.27.224.0/20',
    '104.27.240.0/20', '172.64.0.0/20', '172.64.16.0/20', '172.64.32.0/20', '172.64.48.0/20', '172.64.64.0/20',
    '172.64.80.0/20', '172.64.96.0/20', '172.64.112.0/20', '172.64.128.0/20', '172.64.144.0/20', '172.64.160.0/20',
    '172.64.176.0/20', '172.64.192.0/20', '172.64.208.0/20', '172.64.224.0/20', '172.64.240.0/20', '172.65.0.0/20',
    '172.65.16.0/20', '172.65.32.0/20', '172.65.48.0/20', '172.65.64.0/20', '172.65.80.0/20', '172.65.96.0/20',
    '172.65.112.0/20', '172.65.128.0/20', '172.65.144.0/20', '172.65.160.0/20', '172.65.176.0/20', '172.65.192.0/20',
    '172.65.208.0/20', '172.65.224.0/20', '172.65.240.0/20', '172.66.0.0/20', '172.66.16.0/20', '172.66.32.0/20',
    '172.66.48.0/20', '172.66.64.0/20', '172.66.80.0/20', '172.66.96.0/20', '172.66.112.0/20', '172.66.128.0/20',
    '172.66.144.0/20', '172.66.160.0/20', '172.66.176.0/20', '172.66.192.0/20', '172.66.208.0/20', '172.66.224.0/20',
    '172.66.240.0/20', '172.67.0.0/20', '172.67.16.0/20', '172.67.32.0/20', '172.67.48.0/20', '172.67.64.0/20',
    '172.67.80.0/20', '172.67.96.0/20', '172.67.112.0/20', '172.67.128.0/20', '172.67.144.0/20', '172.67.160.0/20',
    '172.67.176.0/20', '172.67.192.0/20', '172.67.208.0/20', '172.67.224.0/20', '172.67.240.0/20', '172.68.0.0/20',
    '172.68.16.0/20', '172.68.32.0/20', '172.68.48.0/20', '172.68.64.0/20', '172.68.80.0/20', '172.68.96.0/20',
    '172.68.112.0/20', '172.68.128.0/20', '172.68.144.0/20', '172.68.160.0/20', '172.68.176.0/20', '172.68.192.0/20',
    '172.68.208.0/20', '172.68.224.0/20', '172.68.240.0/20', '172.69.0.0/20', '172.69.16.0/20', '172.69.32.0/20',
    '172.69.48.0/20', '172.69.64.0/20', '172.69.80.0/20', '172.69.96.0/20', '172.69.112.0/20', '172.69.128.0/20',
    '172.69.144.0/20', '172.69.160.0/20', '172.69.176.0/20', '172.69.192.0/20', '172.69.208.0/20', '172.69.224.0/20',
    '172.69.240.0/20', '172.70.0.0/20', '172.70.16.0/20', '172.70.32.0/20', '172.70.48.0/20', '172.70.64.0/20',
    '172.70.80.0/20', '172.70.96.0/20', '172.70.112.0/20', '172.70.128.0/20', '172.70.144.0/20', '172.70.160.0/20',
    '172.70.176.0/20', '172.70.192.0/20', '172.70.208.0/20', '172.70.224.0/20', '172.70.240.0/20', '172.71.0.0/20',
    '172.71.16.0/20', '172.71.32.0/20', '172.71.48.0/20', '172.71.64.0/20', '172.71.80.0/20', '172.71.96.0/20',
    '172.71.112.0/20', '172.71.128.0/20', '172.71.144.0/20', '172.71.160.0/20', '172.71.176.0/20', '172.71.192.0/20',
    '172.71.208.0/20', '172.71.224.0/20', '172.71.240.0/20', '131.0.72.0/22',
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
            anchor_int = na + int(frac * (size - 1))  # patch8: 全范围权重采样
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
    while len(ips) < count and attempts < count * 100:  # patch8: 扩大尝试次数
        attempts += 1
        net = random.choices(networks, weights=_net_weights, k=1)[0]
        offset = random.randint(1, net.num_addresses - 2)  # patch8: 移除65535上限，全范围采样
        ip = str(net.network_address + offset)
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips


# v9.96: VLESS_SNI used for TLS+HTTP test so we verify real traffic routing, not just TCP open
_VLESS_SNI = "iam.jimhacker.qzz.io"

def test_ip_latency(ip: str, port: int = 443, timeout: float = 10.0):
    """v9.99: TCP连通性测试（恢复原始行为，不做TLS握手）。
    原因：TLS ban 过激导致 banned 列表撑满→池枯竭→注册失败率上升。
    坏IP由 xray 实际使用后 ban_ip() 处理，不在测速阶段预判。"""
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
def refresh_pool(generate_count: int = 60, target_valid: int = 20, threads: int = 5, port: int = 443, max_latency: float = 9999.0, log_cb=None) -> list:
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


def retest_pool(max_latency: float = 9999.0, threads: int = 8, port: int = 443, log_cb=None) -> dict:
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
