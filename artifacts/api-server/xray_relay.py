"""
xray 静态端口中继 (v3 - 端口独占 + 真实探针版)
- v3 改动: 只保留实测可达 signup.live.com 的端口
  加入端口独占锁(每端口同时只给1个注册使用，保证IP一致性)
  加入真实连通性探针(不只检查SOCKS5握手，还探测目标可达性)
  动态xray实例保留作最后回退
"""
import os, json, subprocess, socket, time, threading, random, tempfile

XRAY_BIN     = os.path.join(os.path.dirname(__file__), "xray", "xray")
VLESS_UUID   = "b3be1361-709c-4cad-824a-732e434ea06f"
VLESS_SNI    = "iam.jimhacker.qzz.io"
VLESS_HOST   = "iam.jimhacker.qzz.io"
VLESS_PATH   = "/?ed=2048"
VLESS_PORT   = 443

# ── 静态端口池（v3: 仅保留实测可达 signup.live.com 的端口）──────────────
# 测试方式: curl -x socks5h://127.0.0.1:<port> --connect-timeout 8 -s
#           -o /dev/null -w '%{http_code}' https://signup.live.com/
# 可用: 10851(ss-in-1/edir2end), 10853(ss-in-3/edir2end), 10855(ss-in-5/edir2end)
#       10857(ss-in-7/layercon), 10859(ss-in-9/cuzthk), 10872(ps-in-2/47.83.168.191→85.254.137.104)
_STATIC_PORTS = [
    10851, 10853, 10855, 10857, 10859,   # ss-in-* Shadowsocks (已验证可达Microsoft)
    10872,                               # ps-in-2 SOCKS5 (85.254.137.104 Canada Bitecloud, proxy=false hosting=false)
]

_static_lock    = threading.Lock()
_port_cursor    = os.getpid() % max(1, len(_STATIC_PORTS))
_ports_in_use   = set()   # v3: 正在使用中的端口（保证每端口同时只给1个注册用）

def _is_port_alive(port: int, timeout: float = 2.0) -> bool:
    """TCP connect + SOCKS5 握手探针"""
    try:
        s = socket.socket()
        s.settimeout(timeout)
        s.connect(('127.0.0.1', port))
        s.sendall(b"\x05\x01\x00")
        r = s.recv(2)
        s.close()
        return r == b"\x05\x00"
    except Exception:
        return False

def _probe_microsoft(port: int, timeout: float = 10.0) -> bool:
    """v3: 通过 SOCKS5 探测 signup.live.com:443 TCP 可达性（不做TLS，只验证连通）"""
    try:
        s = socket.socket()
        s.settimeout(timeout)
        s.connect(('127.0.0.1', port))
        # SOCKS5 握手
        s.sendall(b"\x05\x01\x00")
        r = s.recv(2)
        if r != b"\x05\x00":
            s.close(); return False
        # 请求连接 signup.live.com:443
        host = b"signup.live.com"
        req = b"\x05\x01\x00\x03" + bytes([len(host)]) + host + (443).to_bytes(2, "big")
        s.sendall(req)
        resp = s.recv(10)
        s.close()
        return len(resp) >= 2 and resp[1] == 0x00
    except Exception:
        return False

def _pick_static_port() -> int | None:
    """
    v3: Round-robin 扫描静态端口池，返回第一个:
    1) SOCKS5握手正常 2) signup.live.com TCP可达 3) 当前未被其他注册占用
    """
    global _port_cursor
    n = len(_STATIC_PORTS)
    with _static_lock:
        start = _port_cursor
        for i in range(n):
            idx  = (start + i) % n
            port = _STATIC_PORTS[idx]
            if port in _ports_in_use:
                continue   # 此端口已被其他注册占用，跳过
            if not _is_port_alive(port, timeout=2.0):
                continue
            if not _probe_microsoft(port, timeout=10.0):
                continue   # 此端口无法到达Microsoft，跳过
            _port_cursor = (idx + 1) % n
            _ports_in_use.add(port)
            return port
    return None

def _release_static_port(port: int):
    """v3: 注册完成后释放端口独占锁"""
    with _static_lock:
        _ports_in_use.discard(port)

# ── 动态实例辅助（原逻辑，仅在静态端口全部失效时启用）────────────────────
def _find_free_port(start: int = 20000, end: int = 29999) -> int:
    for _ in range(200):
        port = random.randint(start, end)
        try:
            s = socket.socket()
            s.bind(('127.0.0.1', port))
            s.close()
            return port
        except OSError:
            continue
    raise RuntimeError("找不到空闲端口")

def _make_xray_config(cf_ip: str, socks_port: int) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "port": socks_port,
            "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": False}
        }],
        "outbounds": [{
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": cf_ip,
                    "port": VLESS_PORT,
                    "users": [{"id": VLESS_UUID, "encryption": "none", "flow": ""}]
                }]
            },
            "streamSettings": {
                "network": "ws",
                "security": "tls",
                "tlsSettings": {
                    "serverName": VLESS_SNI,
                    "fingerprint": "chrome",
                    "alpn": ["h3", "h2", "http/1.1"],
                    "allowInsecure": False
                },
                "wsSettings": {
                    "path": VLESS_PATH,
                    "headers": {"Host": VLESS_HOST}
                }
            }
        }]
    }

# ───────────────────────────────────────────────────────────────────────────
class XrayRelay:
    """
    单个账号的代理中继。
    优先路径: 静态 SOCKS5 端口（独占锁，保证IP一致性）。
    回退路径: 动态 xray 实例（CF VLESS/WS/TLS → CF Worker）。
    """

    def __init__(self, cf_ip: str, force_dynamic: bool = False):
        self.cf_ip         = cf_ip
        self.force_dynamic = force_dynamic
        self.socks_port  = 0
        self.socks5_url  = ""
        self._proc       = None
        self._cfg_path   = None
        self._is_static  = False

    def start(self, timeout: float = 8.0) -> bool:
        if not self.force_dynamic:
            static_port = _pick_static_port()
            if static_port:
                self.socks_port = static_port
                self.socks5_url = f"socks5://127.0.0.1:{static_port}"
                self._is_static = True
                print(f"[xray_relay] ✅ 静态端口 {static_port} (cf_ip={self.cf_ip} 已跳过，Worker降级模式)", flush=True)
                return True
        else:
            print(f"[xray_relay] 🚀 force_dynamic=True，跳过静态端口，直接启动 CF VLESS 隧道 cf_ip={self.cf_ip}", flush=True)

        print(f"[xray_relay] ⚠ 静态端口全部失败，启动动态 xray 实例 cf_ip={self.cf_ip}", flush=True)
        self.socks_port = _find_free_port()
        self.socks5_url = f"socks5://127.0.0.1:{self.socks_port}"
        cfg = _make_xray_config(self.cf_ip, self.socks_port)
        fd, self._cfg_path = tempfile.mkstemp(suffix=".json", prefix="xray_")
        with os.fdopen(fd, 'w') as f:
            json.dump(cfg, f)

        self._proc = subprocess.Popen(
            [XRAY_BIN, "run", "-config", self._cfg_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                s = socket.socket()
                s.settimeout(0.5)
                s.connect(('127.0.0.1', self.socks_port))
                s.close()
                if self.ensure_tunnel(probe_timeout=4.0):
                    return True
                else:
                    time.sleep(0.4)
                    continue
            except Exception:
                time.sleep(0.3)

        self.stop()
        return False

    def ensure_tunnel(self, probe_timeout: float = 4.0,
                      probe_host: str = '1.1.1.1', probe_port: int = 443) -> bool:
        if self._is_static:
            return True
        try:
            s = socket.socket()
            s.settimeout(probe_timeout)
            s.connect(('127.0.0.1', self.socks_port))
            s.sendall(b"\x05\x01\x00")
            r = s.recv(2)
            if r != b"\x05\x00":
                s.close(); return False
            host_b = probe_host.encode()
            req = b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + probe_port.to_bytes(2, "big")
            s.sendall(req)
            r = s.recv(10)
            s.close()
            return len(r) >= 2 and r[1] == 0x00
        except Exception:
            return False

    def stop(self):
        if self._is_static:
            _release_static_port(self.socks_port)   # v3: 释放端口独占
            return
        if self._proc:
            try: self._proc.terminate(); self._proc.wait(timeout=3)
            except Exception: pass
            self._proc = None
        if self._cfg_path and os.path.exists(self._cfg_path):
            try: os.unlink(self._cfg_path)
            except Exception: pass

    def __enter__(self): self.start(); return self
    def __exit__(self, *_): self.stop()


# ───────────────────────────────────────────────────────────────────────────
def test_relay(cf_ip: str, timeout: float = 10.0):
    relay = XrayRelay(cf_ip)
    try:
        if not relay.start(timeout=timeout):
            return None
        import urllib.request, json as _j
        handler = urllib.request.ProxyHandler({
            'http': relay.socks5_url, 'https': relay.socks5_url
        })
        opener = urllib.request.build_opener(handler)
        t0 = time.time()
        try:
            r = opener.open("http://ip-api.com/json?fields=query,isp,proxy", timeout=8)
            body = r.read().decode()
            latency = (time.time() - t0) * 1000
            d = _j.loads(body)
            return {'latency': round(latency, 1), 'ip': d.get('query'), 'isp': d.get('isp'),
                    'mode': 'static' if relay._is_static else 'dynamic'}
        except Exception:
            return None
    finally:
        relay.stop()
