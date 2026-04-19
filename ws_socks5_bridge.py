#!/usr/bin/env python3
"""
VPS-side SOCKS5→WebSocket bridge.
Playwright uses socks5://127.0.0.1:PORT as proxy.
Each SOCKS5 CONNECT tunnels through the Replit WS server (Replit's exit IP).
"""
import socket, threading, struct, select, sys, os, ssl, json
import urllib.request, urllib.parse

WS_SERVER   = os.environ.get("WS_SERVER",  "wss://4810fff0-32b6-424d-a20b-6a96c29a8a0d-00-mv8mjhwuqdxx.riker.replit.dev/ws/tunnel")
WS_TOKEN    = os.environ.get("WS_TOKEN",   "CHANGEME")
SOCKS_PORT  = int(os.environ.get("SOCKS_PORT", "1090"))

import base64, hashlib, secrets as _sec

def ws_handshake(host, port, path, token):
    """Return (ssl_sock, read_buf) after WebSocket handshake."""
    key = base64.b64encode(_sec.token_bytes(16)).decode()
    accept_expected = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()

    ctx = ssl.create_default_context()
    raw = socket.create_connection((host, port), timeout=10)
    sock = ctx.wrap_socket(raw, server_hostname=host)

    qs = urllib.parse.urlencode({"token": token})
    req = (
        f"GET {path}?{qs} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    ).encode()
    sock.sendall(req)

    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("WS handshake EOF")
        resp += chunk
    if b"101" not in resp:
        raise ConnectionError(f"WS upgrade failed: {resp[:200]}")
    extra = resp.split(b"\r\n\r\n", 1)[1]
    return sock, extra

def ws_send(sock, data: bytes, binary=True):
    opcode = 0x02 if binary else 0x01
    length = len(data)
    mask_key = os.urandom(4)
    header = bytes([0x80 | opcode])
    if length < 126:
        header += bytes([0x80 | length])
    elif length < 65536:
        header += struct.pack("!BH", 0x80 | 126, length)
    else:
        header += struct.pack("!BQ", 0x80 | 127, length)
    header += mask_key
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
    sock.sendall(header + masked)

def ws_recv_frame(sock, buf: bytes):
    """Read one WS frame. Returns (payload_bytes, new_buf, is_binary)."""
    while True:
        if len(buf) >= 2:
            fin_op = buf[0]
            opcode = fin_op & 0x0f
            masked = (buf[1] & 0x80) != 0
            plen   = buf[1] & 0x7f
            off = 2
            if plen == 126:
                if len(buf) < 4: pass
                else:
                    plen = struct.unpack("!H", buf[2:4])[0]; off = 4
            elif plen == 127:
                if len(buf) < 10: pass
                else:
                    plen = struct.unpack("!Q", buf[2:10])[0]; off = 10
            if plen not in (126, 127):
                moff = off + (4 if masked else 0)
                if len(buf) >= moff + plen:
                    mask = buf[off:off+4] if masked else b"\x00\x00\x00\x00"
                    off2 = off + (4 if masked else 0)
                    payload = bytes(b ^ mask[i%4] for i,b in enumerate(buf[off2:off2+plen]))
                    buf = buf[moff+plen:]
                    if opcode == 8: return None, buf, False  # close
                    if opcode == 9:  # ping → pong
                        ws_send(sock, payload, binary=False)
                        continue
                    return payload, buf, opcode == 2
        chunk = sock.recv(4096)
        if not chunk:
            return None, buf, False
        buf += chunk

def pipe_ws_to_tcp(ws_sock, tcp_sock, ws_buf, stop):
    try:
        while not stop[0]:
            payload, ws_buf, _ = ws_recv_frame(ws_sock, ws_buf)
            if payload is None:
                break
            if payload:
                tcp_sock.sendall(payload)
    except Exception:
        pass
    finally:
        stop[0] = True
        try: tcp_sock.close()
        except: pass
        try: ws_sock.close()
        except: pass

def pipe_tcp_to_ws(tcp_sock, ws_sock, stop):
    try:
        while not stop[0]:
            r, _, _ = select.select([tcp_sock], [], [], 2)
            if not r:
                continue
            data = tcp_sock.recv(4096)
            if not data:
                break
            ws_send(ws_sock, data)
    except Exception:
        pass
    finally:
        stop[0] = True
        try: tcp_sock.close()
        except: pass
        try: ws_sock.close()
        except: pass

def handle_socks5(client: socket.socket, addr):
    try:
        # SOCKS5 handshake
        data = client.recv(256)
        if not data or data[0] != 5:
            return
        client.sendall(b"\x05\x00")  # no auth

        # Read CONNECT request
        req = client.recv(256)
        if len(req) < 7 or req[1] != 1:
            client.sendall(b"\x05\x07\x00\x01" + b"\x00"*6)
            return

        atyp = req[3]
        if atyp == 1:
            target_host = socket.inet_ntoa(req[4:8])
            target_port = struct.unpack("!H", req[8:10])[0]
        elif atyp == 3:
            hlen = req[4]
            target_host = req[5:5+hlen].decode()
            target_port = struct.unpack("!H", req[5+hlen:7+hlen])[0]
        elif atyp == 4:
            target_host = socket.inet_ntop(socket.AF_INET6, req[4:20])
            target_port = struct.unpack("!H", req[20:22])[0]
        else:
            client.sendall(b"\x05\x08\x00\x01" + b"\x00"*6)
            return

        # Parse WS server URL
        url = WS_SERVER.replace("wss://", "").replace("ws://", "")
        ws_secure = WS_SERVER.startswith("wss://")
        if "/" in url:
            ws_hostport, ws_path = url.split("/", 1)
            ws_path = "/" + ws_path
        else:
            ws_hostport, ws_path = url, "/"

        if ":" in ws_hostport and not ws_hostport.startswith("["):
            ws_host, ws_port = ws_hostport.rsplit(":", 1)
            ws_port = int(ws_port)
        else:
            ws_host = ws_hostport
            ws_port = 443 if ws_secure else 80

        # Add target to path
        ws_path_full = f"{ws_path}?token={urllib.parse.quote(WS_TOKEN)}&host={urllib.parse.quote(target_host)}&port={target_port}"

        print(f"[socks5→ws] {addr} → {target_host}:{target_port} via {ws_host}", flush=True)

        try:
            ws_sock, ws_buf = ws_handshake(ws_host, ws_port, ws_path.rstrip("?").rstrip("&"), WS_TOKEN) if ws_secure else (None, b"")
            # Re-do with full path including params
            ws_sock, ws_buf = ws_handshake_full(ws_host, ws_port, ws_path, ws_path_full, WS_TOKEN, ws_secure)
        except Exception as e:
            print(f"[socks5→ws] WS connect failed: {e}", flush=True)
            client.sendall(b"\x05\x04\x00\x01" + b"\x00"*6)
            return

        # Wait for {ok:true} from server
        try:
            payload, ws_buf, _ = ws_recv_frame(ws_sock, ws_buf)
            if payload:
                msg = json.loads(payload.decode())
                if not msg.get("ok"):
                    raise ConnectionError(f"Server error: {msg}")
        except Exception as e:
            print(f"[socks5→ws] handshake response error: {e}", flush=True)
            client.sendall(b"\x05\x01\x00\x01" + b"\x00"*6)
            ws_sock.close()
            return

        # SOCKS5 success reply
        client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack("!H", target_port))

        # Pipe bidirectionally
        stop = [False]
        t1 = threading.Thread(target=pipe_ws_to_tcp, args=(ws_sock, client, ws_buf, stop), daemon=True)
        t2 = threading.Thread(target=pipe_tcp_to_ws, args=(client, ws_sock, stop), daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

    except Exception as e:
        print(f"[socks5→ws] error {addr}: {e}", flush=True)
    finally:
        try: client.close()
        except: pass

def ws_handshake_full(ws_host, ws_port, ws_path_base, ws_path_full, token, secure):
    """WebSocket handshake with full query-string path."""
    key = base64.b64encode(_sec.token_bytes(16)).decode()
    ctx = ssl.create_default_context()
    raw = socket.create_connection((ws_host, ws_port), timeout=10)
    sock = ctx.wrap_socket(raw, server_hostname=ws_host) if secure else raw

    req = (
        f"GET {ws_path_full} HTTP/1.1\r\n"
        f"Host: {ws_host}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    ).encode()
    sock.sendall(req)

    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("WS handshake EOF")
        resp += chunk
    header = resp.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
    if " 101 " not in header:
        raise ConnectionError(f"WS upgrade failed: {header[:300]}")
    extra = resp.split(b"\r\n\r\n", 1)[1]
    return sock, extra

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", SOCKS_PORT))
    srv.listen(64)
    print(f"[socks5→ws] listening on 127.0.0.1:{SOCKS_PORT}", flush=True)
    print(f"[socks5→ws] tunneling via {WS_SERVER}", flush=True)
    while True:
        client, addr = srv.accept()
        threading.Thread(target=handle_socks5, args=(client, addr), daemon=True).start()

if __name__ == "__main__":
    main()
