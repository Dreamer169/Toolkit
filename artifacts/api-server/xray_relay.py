"""
xray 静态端口中继 (v2 - Worker降级兼容版)
- 原 v1: 为每账号启动独立 xray 实例 → CF IP:443 → jimhacker CF Worker
- v2 改动: jimhacker Worker 429/1027配额用尽时，回退到主 xray 进程
  已运行的静态 SOCKS5 端口池（ss-in/ps-in/in-socks 系列），
  round-robin 分配，每账号独占一个端口（避免重用）。
  若静态端口也不可用，再回退到动态 xray 实例（原逻辑）。
"""
import os, json, subprocess, socket, time, threading, random, tempfile

XRAY_BIN     = os.path.join(os.path.dirname(__file__), "xray", "xray")
VLESS_UUID   = "b3be1361-709c-4cad-824a-732e434ea06f"
VLESS_SNI    = "iam.jimhacker.qzz.io"
VLESS_HOST   = "iam.jimhacker.qzz.io"
VLESS_PATH   = "/?ed=2048"
VLESS_PORT   = 443

# ── 静态端口池 ──────────────────────────────────────────────────────────────
# 主 xray 进程（/root/Toolkit/xray.json）监听的 SOCKS5 端口。
# 每个端口走不同出口节点，提供 IP 隔离。
_STATIC_PORTS = [
    10851, 10853, 10854, 10855, 10857, 10859,   # ss-in-* (已验证可用)
    10870, 10871,                                # ps-in-0/1 (已验证可用)
    10820, 10821, 10822, 10823, 10824,           # in-socks-0~4 (备用)
    10825, 10826, 10827, 10828, 10829,           # in-socks-5~9 (备用)
    10830, 10831, 10832, 10833, 10834,           # in-socks-10~14 (备用)
    10835, 10836, 10837, 10838, 10839,           # in-socks-15~19 (备用)
]
_static_lock  = threading.Lock()
_port_cursor  = 0   # round-robin 游标

def _is_port_alive(port: int, timeout: float = 2.0) -> bool:
    """TCP connect + SOCKS5 握手探针，确认端口在线"""
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

def _pick_static_port() -> int | None:
    """Round-robin 扫描静态端口池，返回第一个存活端口；全部失败返回 None"""
    global _port_cursor
    n = len(_STATIC_PORTS)
    with _static_lock:
        start = _port_cursor
        for i in range(n):
            idx  = (start + i) % n
            port = _STATIC_PORTS[idx]
            if _is_port_alive(port, timeout=2.0):
                _port_cursor = (idx + 1) % n   # 下次从下一个开始
                return port
    return None

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
    优先路径: 主 xray 静态 SOCKS5 端口（绕过 CF Worker）。
    回退路径: 动态 xray 实例（原 VLESS/WS/TLS → CF Worker 方式）。
    """

    def __init__(self, cf_ip: str):
        self.cf_ip       = cf_ip
        self.socks_port  = 0
        self.socks5_url  = ""
        self._proc       = None
        self._cfg_path   = None
        self._is_static  = False   # True = 使用静态端口（无子进程）

    # ------------------------------------------------------------------
    def start(self, timeout: float = 8.0) -> bool:
        """
        1) 尝试从静态端口池取一个存活端口（快，~2s）
        2) 若全部失败，fallback 到动态 xray 实例（原逻辑）
        """
        # ── 优先：静态端口 ─────────────────────────────────────────
        static_port = _pick_static_port()
        if static_port:
            self.socks_port = static_port
            self.socks5_url = f"socks5://127.0.0.1:{static_port}"
            self._is_static = True
            print(f"[xray_relay] ✅ 静态端口 {static_port} (cf_ip={self.cf_ip} 已跳过，Worker降级模式)", flush=True)
            return True

        # ── 回退：动态 xray 实例 ───────────────────────────────────
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

    # ------------------------------------------------------------------
    def ensure_tunnel(self, probe_timeout: float = 4.0,
                      probe_host: str = '1.1.1.1', probe_port: int = 443) -> bool:
        """端到端隧道探针（仅动态实例时实际检查，静态端口直接返回 True）"""
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

    # ------------------------------------------------------------------
    def stop(self):
        """关闭动态 xray 进程（静态端口模式为 no-op）"""
        if self._is_static:
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
    """
    测试中继链路。静态端口模式下测试静态端口延迟；
    动态模式下测试 CF IP → jimhacker Worker 链路。
    """
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
