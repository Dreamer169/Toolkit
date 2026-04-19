import random, socket, time, threading, json, os
from ipaddress import ip_network

CF_IP_RANGES = [
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22',
    '141.101.64.0/18', '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20',
    '197.234.240.0/22', '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13',
    '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
]

POOL_STATE_FILE = '/tmp/cf_pool_state.json'
_pool_lock = threading.Lock()
_available = []
_in_use = {}
_used_history = []
_banned_ips: set = set()


def _entry_ip(entry):
    return entry.get('ip') if isinstance(entry, dict) else None


def _normalise_available(items):
    out = []
    seen = set()
    blocked = set(_used_history) | set(_banned_ips)
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


def _save_state(extra_banned: list | None = None):
    try:
        if extra_banned:
            _banned_ips.update([x for x in extra_banned if x])
        hist = list(dict.fromkeys(_used_history))[-2000:]
        banned = list(dict.fromkeys(_banned_ips))[-2000:]
        available = _normalise_available(_available)
        with open(POOL_STATE_FILE, 'w') as f:
            json.dump({
                'available': available,
                'used_history': hist,
                'history': hist,
                'history_count': len(hist),
                'banned': banned,
            }, f)
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


def generate_cf_ips(count: int = 60) -> list:
    networks = [ip_network(r, strict=False) for r in CF_IP_RANGES]
    with _pool_lock:
        seen = set(_used_history) | set(_banned_ips) | {_entry_ip(x) for x in _available}
    seen.discard(None)
    ips = []
    attempts = 0
    while len(ips) < count and attempts < count * 30:
        attempts += 1
        net = random.choice(networks)
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
            if item['ip'] not in existing and item['ip'] not in _banned_ips and item['ip'] not in _used_history:
                _available.append(item)
                existing.add(item['ip'])
        _available.sort(key=lambda x: x['latency'])
        _save_state()

    if log_cb:
        log_cb(f'✅ 测速完成：{len(candidates)} 个候选，{len(new_ips)} 个有效（≤{max_latency}ms）入池')
    return new_ips


def acquire_ip(job_id: str, auto_refresh: bool = False, log_cb=None) -> dict | None:
    with _pool_lock:
        _available[:] = _normalise_available(_available)
        if _available:
            ip_info = _available.pop(0)
            _in_use[job_id] = ip_info
            if ip_info['ip'] not in _used_history:
                _used_history.append(ip_info['ip'])
            _save_state()
            return ip_info

    if auto_refresh:
        if log_cb:
            log_cb('CF 池为空，按需刷新…')
        refresh_pool(log_cb=log_cb)
        with _pool_lock:
            _available[:] = _normalise_available(_available)
            if _available:
                ip_info = _available.pop(0)
                _in_use[job_id] = ip_info
                if ip_info['ip'] not in _used_history:
                    _used_history.append(ip_info['ip'])
                _save_state()
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
