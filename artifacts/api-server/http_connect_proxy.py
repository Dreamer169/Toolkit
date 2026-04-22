#!/usr/bin/env python3
"""
Simple HTTP CONNECT proxy server.
Allows Playwright to use the VPS direct IP (45.205.27.69) for outbound connections.
No xray/SOCKS5 involved — uses OS default network interface.
"""
import socket
import threading
import select
import sys
import os
import base64
import logging

logging.basicConfig(level=logging.INFO, format='[proxy] %(asctime)s %(message)s')
log = logging.getLogger()

PORT   = int(os.environ.get('CONNECT_PROXY_PORT', '8091'))
TOKEN  = os.environ.get('CONNECT_PROXY_TOKEN', os.environ.get('SESSION_SECRET', 'replproxy2024'))
ALLOWED_PORTS = {80, 443, 8080}

def check_auth(headers: str) -> bool:
    for line in headers.split('\r\n'):
        k, _, v = line.partition(':')
        k = k.strip().lower()
        if k in ('proxy-authorization', 'x-proxy-token'):
            v = v.strip()
            if v.lower().startswith('basic '):
                try:
                    decoded = base64.b64decode(v[6:]).decode()
                    if decoded in (f':{TOKEN}', TOKEN):
                        return True
                except Exception:
                    pass
            if v == TOKEN:
                return True
    return False

def pipe(src, dst, label):
    try:
        while True:
            try:
                data = src.recv(4096)
            except (ConnectionResetError, BrokenPipeError, OSError):
                break
            if not data:
                break
            try:
                dst.sendall(data)
            except (ConnectionResetError, BrokenPipeError, OSError):
                break
    except Exception:
        pass
    finally:
        try: src.close()
        except: pass
        try: dst.close()
        except: pass

def handle_client(client_sock: socket.socket, addr):
    try:
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = client_sock.recv(4096)
            if not chunk:
                return
            data += chunk

        header_end = data.index(b'\r\n\r\n')
        header_raw = data[:header_end].decode('utf-8', errors='replace')
        extra = data[header_end + 4:]

        lines = header_raw.split('\r\n')
        request_line = lines[0]
        parts = request_line.split()
        if len(parts) < 2 or parts[0].upper() != 'CONNECT':
            client_sock.sendall(b'HTTP/1.1 405 Method Not Allowed\r\n\r\n')
            return

        target = parts[1]
        if ':' in target:
            host, _, port_str = target.rpartition(':')
            port = int(port_str)
        else:
            host, port = target, 443

        if port not in ALLOWED_PORTS:
            log.warning(f'[{addr}] blocked port {port}')
            client_sock.sendall(b'HTTP/1.1 403 Forbidden\r\n\r\n')
            return

        if not check_auth(header_raw):
            log.warning(f'[{addr}] unauthorized CONNECT to {target}')
            client_sock.sendall(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="proxy"\r\n\r\n')
            return

        remote_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote_sock.settimeout(10)
        remote_sock.connect((host, port))
        remote_sock.settimeout(None)

        client_sock.sendall(b'HTTP/1.1 200 Connection Established\r\nProxy-Agent: DirectProxy/1.0\r\n\r\n')
        if extra:
            remote_sock.sendall(extra)

        log.info(f'[{addr}] tunnel → {target}')

        t1 = threading.Thread(target=pipe, args=(client_sock, remote_sock, 'c→r'), daemon=True)
        t2 = threading.Thread(target=pipe, args=(remote_sock, client_sock, 'r→c'), daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

    except Exception as e:
        log.error(f'[{addr}] error: {e}')
        try: client_sock.sendall(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
        except: pass
    finally:
        try: client_sock.close()
        except: pass

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', PORT))
    srv.listen(128)
    log.info(f'HTTP CONNECT proxy listening on 0.0.0.0:{PORT} (token={TOKEN[:6]}...)')
    while True:
        client, addr = srv.accept()
        threading.Thread(target=handle_client, args=(client, addr), daemon=True).start()

if __name__ == '__main__':
    main()
