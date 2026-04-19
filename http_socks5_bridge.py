#!/usr/bin/env python3
"""
VPS-side SOCKS5 → Replit HTTP tunnel bridge (3-endpoint, persistent keep-alive write).
"""
import socket, threading, struct, select, ssl, os, json, time
import urllib.parse

BASE_URL     = os.environ.get("BASE_URL",  "https://4810fff0-32b6-424d-a20b-6a96c29a8a0d-00-mv8mjhwuqdxx.riker.replit.dev")
TUNNEL_TOKEN = os.environ.get("TUNNEL_TOKEN", "1NnCcQJcNgwlTDPEnDIkWEKzWIdmZ/4+BmsOp1/jLP6ojCWsv8+xTwcLj34Mu2viWy0q5SEoDP0q2qE5xHaRRg==")
SOCKS_PORT   = int(os.environ.get("SOCKS_PORT", "1090"))

_parsed = urllib.parse.urlparse(BASE_URL)
_host   = _parsed.hostname
_port   = _parsed.port or (443 if _parsed.scheme == "https" else 80)
_secure = _parsed.scheme == "https"
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.set_alpn_protocols(["http/1.1"])

def make_conn():
    raw = socket.create_connection((_host, _port), timeout=12)
    if _secure:
        return _ssl_ctx.wrap_socket(raw, server_hostname=_host)
    return raw

def read_response(sock: socket.socket) -> tuple[int, bytes]:
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("EOF reading response")
        resp += chunk
    header, rest = resp.split(b"\r\n\r\n", 1)
    status = int(header.decode(errors="replace").split("\r\n")[0].split(" ")[1])
    cl_line = [l for l in header.decode(errors="replace").split("\r\n") if l.lower().startswith("content-length")]
    if cl_line:
        cl = int(cl_line[0].split(":")[1].strip())
        while len(rest) < cl:
            chunk = sock.recv(4096)
            if not chunk: break
            rest += chunk
        body = rest[:cl]
    else:
        body = rest
        while True:
            try:
                sock.settimeout(0.5)
                chunk = sock.recv(4096)
                if not chunk: break
                body += chunk
            except (socket.timeout, ssl.SSLWantReadError):
                break
            finally:
                sock.settimeout(12)
    return status, body

def tunnel_open(target_host: str, target_port: int) -> str:
    qs = urllib.parse.urlencode({"host": target_host, "port": target_port, "token": TUNNEL_TOKEN})
    conn = make_conn()
    req = (
        f"POST /api/tunnel/open?{qs} HTTP/1.1\r\n"
        f"Host: {_host}\r\n"
        f"Content-Length: 0\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode()
    conn.sendall(req)
    status, body = read_response(conn)
    conn.close()
    if status != 200:
        raise ConnectionError(f"open failed status={status}")
    data = json.loads(body)
    if not data.get("ok"):
        raise ConnectionError(f"open returned: {data}")
    return data["id"]

def recv_loop(session_id: str, client_sock: socket.socket, stop: list):
    try:
        qs = urllib.parse.urlencode({"token": TUNNEL_TOKEN})
        path = f"/api/tunnel/read/{session_id}?{qs}"
        conn = make_conn()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {_host}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode()
        conn.sendall(req)

        resp_buf = b""
        while b"\r\n\r\n" not in resp_buf:
            chunk = conn.recv(4096)
            if not chunk:
                return
            resp_buf += chunk

        header_part, buf = resp_buf.split(b"\r\n\r\n", 1)
        status = int(header_part.decode(errors="replace").split("\r\n")[0].split(" ")[1])
        if status != 200:
            print(f"[recv] bad status: {status}", flush=True)
            return

        conn.settimeout(30)
        while not stop[0]:
            while True:
                i = buf.find(b"\r\n")
                if i >= 0:
                    break
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    return
                buf += data

            size_str = buf[:i].decode(errors="replace").strip()
            if not size_str:
                buf = buf[i+2:]
                continue
            try:
                chunk_size = int(size_str.split(";")[0], 16)
            except ValueError:
                break

            if chunk_size == 0:
                break

            buf = buf[i+2:]
            while len(buf) < chunk_size + 2:
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    return
                buf += data

            payload = buf[:chunk_size]
            buf = buf[chunk_size+2:]

            if payload and not stop[0]:
                try:
                    client_sock.sendall(payload)
                except Exception:
                    return

    except Exception as e:
        if not stop[0]:
            print(f"[recv] error: {e}", flush=True)
    finally:
        stop[0] = True
        try: client_sock.close()
        except: pass

def write_loop(session_id: str, client_sock: socket.socket, stop: list):
    """Persistent keep-alive connection for multiple writes."""
    conn = None
    try:
        qs = urllib.parse.urlencode({"token": TUNNEL_TOKEN})
        path = f"/api/tunnel/write/{session_id}?{qs}"

        conn = make_conn()
        client_sock.settimeout(2)

        while not stop[0]:
            try:
                data = client_sock.recv(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            if not data:
                break

            req = (
                f"POST {path} HTTP/1.1\r\n"
                f"Host: {_host}\r\n"
                f"Content-Type: application/octet-stream\r\n"
                f"Content-Length: {len(data)}\r\n"
                f"Connection: keep-alive\r\n"
                f"\r\n"
            ).encode() + data

            try:
                conn.sendall(req)
                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = conn.recv(1024)
                    if not chunk:
                        raise ConnectionError("write conn EOF")
                    resp += chunk
                status_line = resp.decode(errors="replace").split("\r\n")[0]
                if "20" not in status_line:
                    print(f"[write] bad status: {status_line}", flush=True)
                    break
            except Exception as e:
                print(f"[write] reconnecting after error: {e}", flush=True)
                try: conn.close()
                except: pass
                try:
                    conn = make_conn()
                    conn.sendall(req)
                    resp = b""
                    while b"\r\n\r\n" not in resp:
                        chunk = conn.recv(1024)
                        if not chunk:
                            raise ConnectionError("write conn2 EOF")
                        resp += chunk
                except Exception as e2:
                    print(f"[write] reconnect failed: {e2}", flush=True)
                    break

    except Exception as e:
        if not stop[0]:
            print(f"[write] loop error: {e}", flush=True)
    finally:
        stop[0] = True
        if conn:
            try: conn.close()
            except: pass
        try: client_sock.close()
        except: pass

def handle_socks5(client: socket.socket, addr):
    try:
        data = client.recv(256)
        if not data or data[0] != 5:
            return
        client.sendall(b"\x05\x00")

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

        print(f"[bridge] {addr} → {target_host}:{target_port}", flush=True)

        try:
            session_id = tunnel_open(target_host, target_port)
            print(f"[bridge] session={session_id[:8]}", flush=True)
        except Exception as e:
            print(f"[bridge] open failed: {e}", flush=True)
            client.sendall(b"\x05\x04\x00\x01" + b"\x00"*6)
            return

        client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack("!H", target_port))

        stop = [False]
        t1 = threading.Thread(target=recv_loop,  args=(session_id, client, stop), daemon=True)
        t2 = threading.Thread(target=write_loop, args=(session_id, client, stop), daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

    except Exception as e:
        print(f"[bridge] error {addr}: {e}", flush=True)
    finally:
        try: client.close()
        except: pass

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", SOCKS_PORT))
    srv.listen(64)
    print(f"[bridge] SOCKS5 on 127.0.0.1:{SOCKS_PORT}", flush=True)
    print(f"[bridge] endpoint {BASE_URL}/api/tunnel/*", flush=True)
    while True:
        client, addr = srv.accept()
        threading.Thread(target=handle_socks5, args=(client, addr), daemon=True).start()

if __name__ == "__main__":
    main()
