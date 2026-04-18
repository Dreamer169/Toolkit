"""
CF IP 代理池
- 从 Cloudflare 官方 IP 段随机抽取 IP
- TCP 连通性测速 (port 443)
- 池子维护：每批独占，用完换新批
- 持久化：进程重启后从文件恢复已测速的有效 IP，无需重新测速
"""
import random, socket, time, threading, json, os
from ipaddress import ip_network

# Cloudflare 官方 IPv4 段（来源 https://www.cloudflare.com/ips-v4/）
CF_IP_RANGES = [
    '173.245.48.0/20',
    '103.21.244.0/22',
    '103.22.200.0/22',
    '103.31.4.0/22',
    '141.101.64.0/18',
    '108.162.192.0/18',
    '190.93.240.0/20',
    '188.114.96.0/20',
    '197.234.240.0/22',
    '198.41.128.0/17',
    '162.158.0.0/15',
    '104.16.0.0/13',
    '104.24.0.0/14',
    '172.64.0.0/13',
    '131.0.72.0/22',
]

POOL_STATE_FILE = '/tmp/cf_pool_state.json'

# ── 内存池（线程安全） ──────────────────────────────────────────
_pool_lock    = threading.Lock()
_available    = []   # [{"ip":..., "latency":..., "proxy":...}]
_in_use       = {}   # job_id -> ip_info
_used_history = []   # 已用过的 IP（本次会话内不重用）

def _save_state(extra_banned: list = []):
    try:
        global _banned_ips
        if extra_banned:
            _banned_ips.update(extra_banned)
        with open(POOL_STATE_FILE, 'w') as f:
            json.dump({
                'available': _available,
                'used_history': _used_history[-500:],
                'banned': list(_banned_ips)[-1000:],
            }, f)
    except Exception:
        pass

def _load_state():
    """进程启动时从文件恢复已测速的有效 IP，避免每次重新测速"""
    global _available, _used_history
    try:
        if not os.path.exists(POOL_STATE_FILE):
            return
        with open(POOL_STATE_FILE, 'r') as f:
            data = json.load(f)
        loaded_avail = data.get('available', [])
        loaded_hist  = data.get('used_history', [])
        # 只恢复 latency 字段合法的条目
        valid = [x for x in loaded_avail if isinstance(x.get('ip'), str) and isinstance(x.get('latency'), (int, float))]
        if valid:
            _available = valid
            _used_history = list(loaded_hist)
        banned = data.get('banned', [])
        if banned:
            _banned_ips.update(banned)
    except Exception:
        pass

# ── 启动时加载持久化状态 ───────────────────────────────────────
_banned_ips: set = set()
_load_state()

def get_pool_status() -> dict:
    with _pool_lock:
        return {
            'available': len(_available),
            'in_use': len(_in_use),
            'used_total': len(_used_history),
            'pool': [{'ip': x['ip'], 'latency': x['latency']} for x in _available[:20]],
        }

# ── IP 生成 ────────────────────────────────────────────────────
def generate_cf_ips(count: int = 60) -> list:
    """从 CF 段随机生成 count 个不重复 IP"""
    networks = [ip_network(r, strict=False) for r in CF_IP_RANGES]
    seen = set(_used_history)
    ips = []
    attempts = 0
    while len(ips) < count and attempts < count * 20:
        attempts += 1
        net = random.choice(networks)
        offset = random.randint(1, min(net.num_addresses - 2, 65535))
        ip = str(net.network_address + offset)
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips

# ── 延迟测试 ──────────────────────────────────────────────────
def test_ip_latency(ip: str, port: int = 443, timeout: float = 3.0):
    """TCP 握手测速，成功返回 ms，失败返回 None"""
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

# ── 刷新池 ────────────────────────────────────────────────────
def refresh_pool(
    generate_count: int = 60,
    target_valid:   int = 20,
    threads:        int = 5,
    port:           int = 443,
    max_latency:    float = 800.0,
    log_cb=None,
) -> list:
    """
    生成 generate_count 个 CF IP，并发测速，
    取延迟 ≤ max_latency 的前 target_valid 个写入池子。
    返回新增有效 IP 列表。
    """
    if log_cb:
        log_cb(f"🔄 生成 {generate_count} 个 CF IP 并测速（端口{port}，线程{threads}）…")

    candidates = generate_cf_ips(generate_count)
    results    = []
    lock       = threading.Lock()
    sem        = threading.Semaphore(threads)

    def worker(ip):
        with sem:
            lat = test_ip_latency(ip, port)
            if lat is not None and lat <= max_latency:
                with lock:
                    results.append({'ip': ip, 'latency': lat,
                                    'proxy': f'http://{ip}:{port}'})

    ts = [threading.Thread(target=worker, args=(ip,), daemon=True) for ip in candidates]
    for t in ts: t.start()
    for t in ts: t.join(timeout=10)

    results.sort(key=lambda x: x['latency'])
    new_ips = results[:target_valid]

    with _pool_lock:
        _available.extend(new_ips)
        _available.sort(key=lambda x: x['latency'])
        _save_state()

    if log_cb:
        log_cb(f"✅ 测速完成：{len(candidates)} 个候选，{len(new_ips)} 个有效（≤{max_latency}ms）入池")

    return new_ips

# ── 分配 / 归还 ───────────────────────────────────────────────
def acquire_ip(job_id: str, auto_refresh: bool = True, log_cb=None) -> dict | None:
    """
    从池中取出一个 IP（独占），若池空则自动刷新。
    返回 {"ip":..., "latency":..., "proxy":"http://IP:443"}
    """
    with _pool_lock:
        if _available:
            ip_info = _available.pop(0)
            _in_use[job_id] = ip_info
            _used_history.append(ip_info['ip'])
            _save_state()
            return ip_info

    # 池空，自动刷新
    if auto_refresh:
        if log_cb: log_cb("CF 池为空，自动刷新…")
        new_ips = refresh_pool(log_cb=log_cb)
        with _pool_lock:
            if _available:
                ip_info = _available.pop(0)
                _in_use[job_id] = ip_info
                _used_history.append(ip_info['ip'])
                _save_state()
                return ip_info

    return None

def release_ip(job_id: str):
    """注册完成后释放（已用 IP 不放回池，由 history 记录）"""
    with _pool_lock:
        _in_use.pop(job_id, None)


# ── ban_ip：立即从 available 里移除，写入 banned 黑名单 ─────────────────
def ban_ip(ip: str):
    """把 IP 从 available 里踢出并加入 banned 集合（持久化到磁盘）"""
    with _pool_lock:
        before = len(_available)
        _available[:] = [x for x in _available if x["ip"] != ip]
        removed = before - len(_available)
    _save_state(extra_banned=[ip])
    return removed

# ── retest_pool：对 available 里所有 IP 重跑延迟测试，移除死链 ──────────
def retest_pool(
    max_latency: float = 800.0,
    threads:     int   = 8,
    port:        int   = 443,
    log_cb=None,
) -> dict:
    """重测 _available 里所有 IP，删掉超时或不通的。返回 {kept, removed}"""
    with _pool_lock:
        candidates = list(_available)
    if not candidates:
        return {"kept": 0, "removed": 0}

    if log_cb:
        log_cb(f"🔍 重测 {len(candidates)} 个 CF IP（port {port}，延迟≤{max_latency}ms）…")

    results  = []
    lock     = threading.Lock()
    sem      = threading.Semaphore(threads)

    def worker(entry):
        with sem:
            lat = test_ip_latency(entry["ip"], port)
            with lock:
                if lat is not None and lat <= max_latency:
                    results.append({**entry, "latency": lat})

    ts = [threading.Thread(target=worker, args=(e,), daemon=True) for e in candidates]
    for t in ts: t.start()
    for t in ts: t.join(timeout=15)

    results.sort(key=lambda x: x["latency"])
    removed = len(candidates) - len(results)

    with _pool_lock:
        _available.clear()
        _available.extend(results)
    _save_state()

    if log_cb:
        log_cb(f"✅ 重测完成：保留 {len(results)} 个，移除 {removed} 个无效 IP")

    return {"kept": len(results), "removed": removed}
