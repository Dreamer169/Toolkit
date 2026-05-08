#!/usr/bin/env python3
"""
nestingproxy_bridge.py v2.0 -- CF Worker HTTP + RESI SOCKS5 CONNECT tunnel

Improvement (ref: goproxy elazarl/goproxy cascadeproxy-socks):
  v1.0 CONNECT: direct TCP -> exposes VPS IP (45.205.27.69)
  v2.0 CONNECT: route through alive RESI SOCKS5 port -> residential IP
               auto-rotate on failure, report to resi_pool failure threshold
               fallback to direct if all RESI ports exhausted

HTTP GET/POST -> still relayed via proxy.jimjio.indevs.in (CF edge IP)

Listen: 127.0.0.1:NEST_BRIDGE_PORT (default 5559 via PM2 env)
"""
import http.server, socketserver, urllib.request, json, base64
import threading, sys, os, socket, struct, select, time

NEST_URL    = "https://proxy.jimjio.indevs.in/proxy"
LISTEN_PORT = int(os.environ.get("NEST_BRIDGE_PORT", "5559"))

# Import resi_pool for CONNECT tunnel port selection
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
try:
    import resi_pool as _rpool
    _USE_RESI = True
except ImportError:
    _USE_RESI = False
    print("[nest-bridge] WARNING: resi_pool not found, CONNECT will fallback to direct", flush=True)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except Exception:
            break
        if not chunk:
            break
        buf += chunk
    return buf


def _socks5_connect(host: str, port: int, socks5_port: int, timeout: int = 10) -> socket.socket:
    """
    goproxy ConnectDial concept: establish TCP tunnel to (host,port) via SOCKS5.
    Implements RFC 1928 SOCKS5 CONNECT handshake without external libraries.
    """
    sock = socket.create_connection(("127.0.0.1", socks5_port), timeout=timeout)
    # Greeting: no-auth
    sock.sendall(b"\x05\x01\x00")
    resp = _recv_exact(sock, 2)
    if len(resp) < 2 or resp[1] != 0x00:
        sock.close()
        raise ConnectionError(f"SOCKS5 auth rejected: {resp!r}")
    # CONNECT request
    host_b = host.encode("idna")
    req = (struct.pack("!BBBB", 5, 1, 0, 3)
           + bytes([len(host_b)]) + host_b
           + struct.pack("!H", port))
    sock.sendall(req)
    # Parse response (4-byte header + bound addr)
    hdr = _recv_exact(sock, 4)
    if len(hdr) < 4:
        sock.close()
        raise ConnectionError(f"SOCKS5 short response: {hdr!r}")
    if hdr[1] != 0x00:
        sock.close()
        raise ConnectionError(f"SOCKS5 CONNECT failed rep={hdr[1]:#04x}")
    atyp = hdr[3]
    if atyp == 0x01:
        _recv_exact(sock, 6)   # IPv4 + port
    elif atyp == 0x03:
        n = _recv_exact(sock, 1)
        if n:
            _recv_exact(sock, n[0] + 2)
    elif atyp == 0x04:
        _recv_exact(sock, 18)  # IPv6 + port
    return sock


def _tunnel(client: socket.socket, remote: socket.socket):
    """Bidirectional passthrough (goproxy hijack equivalent)."""
    client.setblocking(False)
    remote.setblocking(False)
    while True:
        try:
            r, _, _ = select.select([client, remote], [], [], 30)
        except Exception:
            break
        if not r:
            break
        for s in r:
            try:
                data = s.recv(16384)
                if not data:
                    return
                (remote if s is client else client).sendall(data)
            except Exception:
                return


class NestProxy(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass  # silence access log

    def do_CONNECT(self):
        """
        HTTPS CONNECT tunnel.
        v2.0: route through RESI SOCKS5 (residential IP) instead of direct VPS.
        easy_proxies style: try up to 5 ports with failure-threshold tracking.
        """
        try:
            host, port_s = self.path.rsplit(":", 1)
            port = int(port_s)
        except Exception:
            self.send_error(400, "Bad CONNECT target")
            return

        remote = None

        if _USE_RESI:
            tried = set()
            for _ in range(5):
                socks_port = _rpool.pick()
                if socks_port in tried:
                    continue
                tried.add(socks_port)
                try:
                    remote = _socks5_connect(host, port, socks_port, timeout=10)
                    _rpool.report_success(socks_port)
                    break
                except Exception:
                    _rpool.report_failure(socks_port)

        if remote is None:
            # Fallback: direct TCP (VPS IP)
            try:
                remote = socket.create_connection((host, port), timeout=15)
            except Exception as e:
                self.send_error(502, f"CONNECT failed: {e}")
                return

        self.send_response(200, "Connection established")
        self.end_headers()
        try:
            _tunnel(self.connection, remote)
        finally:
            try:
                remote.close()
            except Exception:
                pass

    def _cf_forward(self):
        """Forward HTTP request through CF Worker (CF edge IP)."""
        url = self.path
        if not url.startswith("http"):
            host = self.headers.get("Host", "localhost")
            url = f"http://{host}{self.path}"
        hdrs = {k: v for k, v in self.headers.items()
                if k.lower() not in ("proxy-connection", "proxy-authorization", "host")}
        body_b64 = None
        if self.command in ("POST", "PUT", "PATCH"):
            length = int(self.headers.get("Content-Length", 0))
            if length:
                body_b64 = base64.b64encode(self.rfile.read(length)).decode()
        payload = {"target_url": url, "method": self.command, "headers": hdrs}
        if body_b64:
            payload["body_b64"] = body_b64
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            NEST_URL, data=data,
            headers={"Content-Type": "application/json", "User-Agent": "NestBridge/2.0"})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                resp = json.loads(r.read())
            body = resp.get("body", "")
            body_b = body.encode() if isinstance(body, str) else body
            self.send_response(resp.get("status", 200))
            for k, v in (resp.get("headers") or {}).items():
                try:
                    self.send_header(k, v)
                except Exception:
                    pass
            self.send_header("Content-Length", len(body_b))
            self.end_headers()
            self.wfile.write(body_b)
        except Exception as e:
            self.send_error(502, str(e)[:120])

    def _handle_status(self):
        body = json.dumps({
            "status": "ok",
            "version": "2.0",
            "listen": LISTEN_PORT,
            "resi_pool": _rpool.status() if _USE_RESI else None,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/status"):
            return self._handle_status()
        self._cf_forward()

    do_POST    = _cf_forward
    do_PUT     = _cf_forward
    do_DELETE  = _cf_forward
    do_HEAD    = _cf_forward


socketserver.TCPServer.allow_reuse_address = True

if __name__ == "__main__":
    if _USE_RESI:
        print("[nest-bridge v2.0] initializing RESI pool...", flush=True)
        _rpool.startup_check(print)
    candidates = len(_rpool.RESI_CANDIDATE_PORTS) if _USE_RESI else 0
    mode = f"RESI SOCKS5 ({candidates} candidates)" if _USE_RESI else "direct fallback"
    print(f"[nest-bridge v2.0] 127.0.0.1:{LISTEN_PORT} -> CF:{NEST_URL}", flush=True)
    print(f"[nest-bridge v2.0] CONNECT mode: {mode}", flush=True)
    with socketserver.ThreadingTCPServer(("127.0.0.1", LISTEN_PORT), NestProxy) as srv:
        srv.serve_forever()
