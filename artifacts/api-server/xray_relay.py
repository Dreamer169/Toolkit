"""
xray CF VLESS relay v3 (dynamic-first)
v9.95 fixes (patch9):
  - _make_xray_config: CF pool IP is now used as the VLESS server address.
    Confirmed via test: ALL CF anycast IPs correctly route to jimhacker Worker
    when SNI=iam.jimhacker.qzz.io is set. v9.48 was wrong to restrict to 4 fixed IPs.
    Each registration now uses a UNIQUE CF PoP as entry point.
  - ProxyIP regions: expanded from 6 to 12 (added AU, CA, FR, KR, GB, IN).
  - WORKER_IPS kept as fallback for static-port mode only.
v9.48 fixes:
  - WORKER_IPS: fixed server address list (only DNS-resolved IPs route to Worker)
  - _make_xray_config: cf_ip now used as ProxyIP param (not server address)
  - VLESS address roundrobins WORKER_IPS so Worker is always reachable
v9.47 fixes:
  - VLESS_PATH: add ProxyIP param (sync with /root/Toolkit/xray.json proxy-0)
  - wsSettings: use top-level 'host' field (xray 26+ deprecated headers.Host)
  - users: remove 'flow' field (match main xray.json exactly)
  - _STATIC_PORTS: trim to confirmed-working ss-in ports only
  - _is_port_alive: real CONNECT probe instead of SOCKS5 handshake only
  - force_dynamic=True is now default for CF registrations
"""
import os, json, subprocess, socket, time, threading, random, tempfile

XRAY_BIN   = os.path.join(os.path.dirname(__file__), "xray", "xray")
VLESS_UUID = "b3be1361-709c-4cad-824a-732e434ea06f"
VLESS_SNI  = "iam.jimhacker.eu.cc"
VLESS_HOST = "iam.jimhacker.eu.cc"
VLESS_PORT = 443
# v9.48 FIX: only these two DNS IPs actually route to jimhacker Worker;
# random CF pool IPs (104.x.x.x etc.) connect to CF TCP but miss the Worker routing.
# We use these as the VLESS server address and pass the random pool IP as ProxyIP.
WORKER_IPS = ["104.21.40.74", "172.67.181.55", "104.21.36.180", "172.67.198.66"]  # eu.cc + us.ci (qzz.io removed: daily quota)
_worker_ip_cursor = 0
_worker_ip_lock = threading.Lock()

def _next_worker_ip() -> str:
    global _worker_ip_cursor
    with _worker_ip_lock:
        ip = WORKER_IPS[_worker_ip_cursor % len(WORKER_IPS)]
        _worker_ip_cursor += 1
    return ip

# Static port pool (confirmed working via main xray process)
# ss-in ports exit via real ISP IPs (proxy:false); in-socks exit via CF (fallback)
_STATIC_PORTS = [
    10851, 10853, 10855, 10859,         # ss-in-1/3/5/9: Italy/Turkey/Russia/HK
    10820, 10821, 10822, 10823, 10824,  # in-socks-0~4: CF VLESS (proxy:true, fallback)
    10825, 10826, 10827, 10828, 10829,  # in-socks-5~9: CF VLESS (fallback)
]
_static_lock = threading.Lock()
_port_cursor = os.getpid() % max(1, len(_STATIC_PORTS))

# Cache port liveness TTL to avoid slow probes on every pick
_port_alive_cache: dict = {}   # port -> last-confirmed timestamp
_PORT_ALIVE_TTL = 120.0        # 2 minutes


def _is_port_alive(port: int, timeout: float = 3.0) -> bool:
    """Real CONNECT probe: SOCKS5 handshake + CONNECT 1.1.1.1:443."""
    now = time.time()
    if now - _port_alive_cache.get(port, 0) < _PORT_ALIVE_TTL:
        return True
    try:
        s = socket.socket()
        s.settimeout(timeout)
        s.connect(("127.0.0.1", port))
        s.sendall(b"\x05\x01\x00")
        if s.recv(2) != b"\x05\x00":
            s.close(); return False
        # CONNECT to 1.1.1.1:443
        s.sendall(b"\x05\x01\x00\x01\x01\x01\x01\x01\x01\xbb")
        r = s.recv(10)
        s.close()
        alive = len(r) >= 2 and r[1] == 0x00
        if alive:
            _port_alive_cache[port] = now
        return alive
    except Exception:
        return False


def _pick_static_port() -> "int | None":
    global _port_cursor
    n = len(_STATIC_PORTS)
    with _static_lock:
        start = _port_cursor
        for i in range(n):
            idx  = (start + i) % n
            port = _STATIC_PORTS[idx]
            if _is_port_alive(port):
                _port_cursor = (idx + 1) % n
                return port
    return None


def _find_free_port(start: int = 20000, end: int = 29999) -> int:
    for _ in range(200):
        port = random.randint(start, end)
        try:
            s = socket.socket(); s.bind(("127.0.0.1", port)); s.close(); return port
        except OSError:
            continue
    raise RuntimeError("no free port")


def _make_xray_config(proxy_ip: str, socks_port: int) -> dict:
    """
    patch9 (v9.95):
      - server address: proxy_ip (CF pool IP) used directly as VLESS server.
        CF anycast routes any CF IP to jimhacker Worker correctly via SNI.
        Tested and confirmed: 104.24.x, 104.17.x, 172.65.x, 172.67.x all work.
        This gives each registration a unique CF PoP entry → IP diversity.
      - ProxyIP: expanded to 12 regions (was 6). Each region has different exit IP
        -> Arkose sees more diverse exit IPs across registrations.
      - WORKER_IPS retained for static-port fallback mode only.
    """
    # patch9: use pool IP directly as VLESS server (CF anycast handles routing)
    server_ip = proxy_ip
    # patch9: 12 ProxyIP regions (was 6: HK/US/NL/DE/SG/JP)
    _PROXYIP_REGIONS = [
        "proxyip.fxxk.dedyn.io%3A443",
    ]
    _chosen_enc = random.choice(_PROXYIP_REGIONS)
    _chosen_label = _chosen_enc.replace("%3A443", "")
    path = f"/?ed=2048&p={_chosen_enc}&rm=no"
    print(f"[xray_relay] VLESS server={server_ip}(pool) ProxyIP={_chosen_label}", flush=True)
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
                    "address": server_ip,
                    "port": VLESS_PORT,
                    "users": [{"id": VLESS_UUID, "encryption": "none"}]
                }]
            },
            "streamSettings": {
                "network": "ws",
                "security": "tls",
                "tlsSettings": {
                    "serverName": VLESS_SNI,
                    "fingerprint": "chrome"
                },
                "wsSettings": {
                    "path": path,
                    "host": VLESS_HOST
                }
            }
        }]
    }


class XrayRelay:
    """
    Per-account proxy relay.

    force_dynamic=True (default for CF registrations):
        Spawns a dedicated xray instance using cf_ip as VLESS entry,
        routed via CF Worker -> ProxyIP exit. Each account gets a unique CF IP.

    force_dynamic=False (non-registration use):
        Tries static port pool first (fast, ~1s). Falls back to dynamic
        instance only if all static ports are dead.
    """

    def __init__(self, cf_ip: str, force_dynamic: bool = False):
        self.cf_ip         = cf_ip
        self.force_dynamic = force_dynamic
        self.socks_port    = 0
        self.socks5_url    = ""
        self._proc         = None
        self._cfg_path     = None
        self._is_static    = False

    def start(self, timeout: float = 12.0) -> bool:
        # Try static pool first (only when force_dynamic=False)
        if not self.force_dynamic:
            static_port = _pick_static_port()
            if static_port:
                self.socks_port = static_port
                self.socks5_url = f"socks5://127.0.0.1:{static_port}"
                self._is_static = True
                print(f"[xray_relay] static port {static_port} (cf_ip={self.cf_ip} skipped)", flush=True)
                return True
            print(f"[xray_relay] all static ports dead, falling back to dynamic cf_ip={self.cf_ip}", flush=True)
        else:
            print(f"[xray_relay] force_dynamic -> CF VLESS tunnel cf_ip={self.cf_ip}", flush=True)

        # Launch dynamic xray instance
        self.socks_port = _find_free_port()
        self.socks5_url = f"socks5://127.0.0.1:{self.socks_port}"
        cfg = _make_xray_config(self.cf_ip, self.socks_port)  # cf_ip -> ProxyIP; WORKER_IPS -> server addr
        fd, self._cfg_path = tempfile.mkstemp(suffix=".json", prefix="xray_")
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f)
        self._proc = subprocess.Popen(
            [XRAY_BIN, "run", "-config", self._cfg_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                s = socket.socket(); s.settimeout(0.5)
                s.connect(("127.0.0.1", self.socks_port)); s.close()
                if self.ensure_tunnel(probe_timeout=min(8.0, deadline - time.time())):
                    return True
                time.sleep(0.4)
            except Exception:
                time.sleep(0.3)
        self.stop()
        return False

    def ensure_tunnel(self, probe_timeout: float = 8.0) -> bool:
        """End-to-end tunnel probe. Static ports skip (already verified)."""
        if self._is_static:
            return True
        try:
            s = socket.socket(); s.settimeout(probe_timeout)
            s.connect(("127.0.0.1", self.socks_port))
            s.sendall(b"\x05\x01\x00")
            if s.recv(2) != b"\x05\x00":
                s.close(); return False
            # CONNECT 1.1.1.1:443 to verify outbound works
            s.sendall(b"\x05\x01\x00\x01\x01\x01\x01\x01\x01\xbb")
            r = s.recv(10); s.close()
            return len(r) >= 2 and r[1] == 0x00
        except Exception:
            return False

    def stop(self):
        if self._is_static:
            return
        if self._proc:
            try: self._proc.terminate(); self._proc.wait(timeout=3)
            except Exception: pass
            self._proc = None
        if self._cfg_path and os.path.exists(self._cfg_path):
            try: os.unlink(self._cfg_path)
            except Exception: pass

    def __del__(self):
        # v9.92: cleanup on GC to prevent zombie xray processes
        try:
            self.stop()
        except Exception:
            pass

    def __enter__(self): self.start(); return self
    def __exit__(self, *_): self.stop()


def test_relay(cf_ip: str, timeout: float = 15.0):
    """Test relay end-to-end via ip-api.com (dynamic CF VLESS mode)."""
    relay = XrayRelay(cf_ip, force_dynamic=True)
    try:
        if not relay.start(timeout=timeout):
            return None
        s = socket.socket(); s.settimeout(10)
        s.connect(("127.0.0.1", relay.socks_port))
        s.sendall(b"\x05\x01\x00"); s.recv(2)
        host = b"ip-api.com"
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + (80).to_bytes(2, "big"))
        r = s.recv(10)
        if r[1] != 0: s.close(); return None
        t0 = time.time()
        s.sendall(b"GET /json?fields=query,isp,proxy HTTP/1.1\r\nHost: ip-api.com\r\nConnection: close\r\n\r\n")
        data = b""; s.settimeout(10)
        while True:
            d = s.recv(4096)
            if not d: break
            data += d
        s.close()
        body = data.split(b"\r\n\r\n", 1)[-1].strip()
        import json as _j; d = _j.loads(body)
        return {
            "latency": round((time.time() - t0) * 1000, 1),
            "ip": d.get("query"),
            "isp": d.get("isp"),
            "proxy": d.get("proxy"),
            "mode": "dynamic",
        }
    except Exception:
        return None
    finally:
        relay.stop()
