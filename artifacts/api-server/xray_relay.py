"""
xray VLESS 中继
- 为每个账号启动独立的 xray 实例
- xray 连接到 CF IP:443 (VLESS/WS/TLS → jimhacker CF Worker)
- 暴露本地 SOCKS5 端口给 Playwright 使用
"""
import os, json, subprocess, socket, time, threading, random, tempfile

XRAY_BIN     = os.path.join(os.path.dirname(__file__), "xray", "xray")
VLESS_UUID   = "b3be1361-709c-4cad-824a-732e434ea06f"
VLESS_SNI    = "iam.jimhacker.qzz.io"
VLESS_HOST   = "iam.jimhacker.qzz.io"
VLESS_PATH   = "/?ed=2048"
VLESS_PORT   = 443

def _find_free_port(start: int = 20000, end: int = 29999) -> int:
    """找一个空闲的本地端口"""
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
    """生成 xray 配置（SOCKS5 入站 → VLESS/WS/TLS 出站）"""
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
                    "users": [{
                        "id": VLESS_UUID,
                        "encryption": "none",
                        "flow": ""
                    }]
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

class XrayRelay:
    """单个账号的 xray 中继实例"""

    def __init__(self, cf_ip: str):
        self.cf_ip      = cf_ip
        self.socks_port = _find_free_port()
        self.socks5_url = f"socks5://127.0.0.1:{self.socks_port}"
        self._proc      = None
        self._cfg_path  = None

    def start(self, timeout: float = 8.0) -> bool:
        """启动 xray，等待 SOCKS5 端口就绪"""
        cfg = _make_xray_config(self.cf_ip, self.socks_port)
        fd, self._cfg_path = tempfile.mkstemp(suffix=".json", prefix="xray_")
        with os.fdopen(fd, 'w') as f:
            json.dump(cfg, f)

        self._proc = subprocess.Popen(
            [XRAY_BIN, "run", "-config", self._cfg_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # 等待端口就绪
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                s = socket.socket()
                s.settimeout(0.5)
                s.connect(('127.0.0.1', self.socks_port))
                s.close()
                return True
            except Exception:
                time.sleep(0.3)

        self.stop()
        return False

    def stop(self):
        """关闭 xray 进程"""
        if self._proc:
            try: self._proc.terminate(); self._proc.wait(timeout=3)
            except Exception: pass
            self._proc = None
        if self._cfg_path and os.path.exists(self._cfg_path):
            try: os.unlink(self._cfg_path)
            except Exception: pass

    def __enter__(self): self.start(); return self
    def __exit__(self, *_): self.stop()


def test_relay(cf_ip: str, timeout: float = 10.0) -> float | None:
    """
    测试 CF IP → jimhacker Worker → 外网的完整链路延迟
    返回 ms 或 None（失败）
    """
    relay = XrayRelay(cf_ip)
    try:
        if not relay.start(timeout=6.0):
            return None
        # 通过 SOCKS5 测试能否连接目标
        import urllib.request
        handler = urllib.request.ProxyHandler({'http': relay.socks5_url, 'https': relay.socks5_url})
        opener  = urllib.request.build_opener(handler)
        t0 = time.time()
        try:
            r = opener.open("http://ip-api.com/json?fields=query,isp,proxy", timeout=8)
            body = r.read().decode()
            latency = (time.time() - t0) * 1000
            import json as _j
            d = _j.loads(body)
            return {'latency': round(latency, 1), 'ip': d.get('query'), 'isp': d.get('isp')}
        except Exception:
            return None
    finally:
        relay.stop()

