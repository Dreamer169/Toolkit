#!/usr/bin/env python3
"""
socks5_bridge.py — HTTP CONNECT proxy that tunnels through a SOCKS5 backend.
Allows undici (Node.js) to use SOCKS5 residential proxies via standard HTTP proxy.
Usage: BRIDGE_PORT=8092 SOCKS5_HOST=127.0.0.1 SOCKS5_PORT=10854 python3 socks5_bridge.py
"""
import socket, threading, os, sys, time

BRIDGE_PORT = int(os.environ.get('BRIDGE_PORT', '8092'))
SOCKS5_HOST = os.environ.get('SOCKS5_HOST', '127.0.0.1')
SOCKS5_PORT = int(os.environ.get('SOCKS5_PORT', '10854'))

FALLBACK_PORTS = [int(p) for p in os.environ.get('SOCKS5_FALLBACK_PORTS', '10857,10859,10851').split(',')]

def _try_socks5_connect(host, port, socks_port):
    sock = socket.create_connection((SOCKS5_HOST, socks_port), timeout=10)
    sock.settimeout(30)
    sock.sendall(b'\x05\x01\x00')
    resp = sock.recv(2)
    if len(resp) < 2 or resp[1] != 0:
        sock.close()
        raise Exception(f'SOCKS5 auth rejected: {resp.hex()}')
    host_bytes = host.encode('idna')
    request = b'\x05\x01\x00\x03' + bytes([len(host_bytes)]) + host_bytes + port.to_bytes(2, 'big')
    sock.sendall(request)
    resp = sock.recv(10)
    if len(resp) < 2 or resp[1] != 0:
        sock.close()
        raise Exception(f'SOCKS5 connect error code={resp[1] if len(resp)>1 else "?"}')
    return sock

def socks5_connect(host, port):
    for socks_port in [SOCKS5_PORT] + FALLBACK_PORTS:
        try:
            return _try_socks5_connect(host, port, socks_port)
        except Exception as e:
            print(f'[bridge] socks5 port {socks_port} failed: {e}', flush=True)
    raise Exception(f'All SOCKS5 ports exhausted for {host}:{port}')

def pipe(src, dst):
    try:
        src.settimeout(60)
        while True:
            data = src.recv(32768)
            if not data: break
            dst.sendall(data)
    except: pass
    finally:
        for s in (src, dst):
            try: s.close()
            except: pass

def handle_client(client_sock):
    try:
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = client_sock.recv(4096)
            if not chunk: return
            data += chunk
        header_raw = data.split(b'\r\n\r\n')[0].decode(errors='replace')
        lines = header_raw.split('\r\n')
        if not lines: return
        parts = lines[0].split()
        if len(parts) < 2 or parts[0].upper() != 'CONNECT':
            client_sock.sendall(b'HTTP/1.1 405 Method Not Allowed\r\n\r\n')
            return
        target = parts[1]
        if ':' in target:
            host, port_str = target.rsplit(':', 1)
            port = int(port_str)
        else:
            host, port = target, 443
        remote = socks5_connect(host, port)
        client_sock.sendall(b'HTTP/1.1 200 Connection established\r\nProxy-Agent: socks5-bridge\r\n\r\n')
        t1 = threading.Thread(target=pipe, args=(client_sock, remote), daemon=True)
        t2 = threading.Thread(target=pipe, args=(remote, client_sock), daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()
    except Exception as e:
        try: client_sock.sendall(f'HTTP/1.1 502 Bad Gateway\r\nX-Error: {e}\r\n\r\n'.encode())
        except: pass
        try: client_sock.close()
        except: pass

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', BRIDGE_PORT))
    srv.listen(100)
    print(f'[socks5-bridge] port {BRIDGE_PORT} -> socks5://{SOCKS5_HOST}:{SOCKS5_PORT} (fallback: {FALLBACK_PORTS})', flush=True)
    while True:
        try:
            client, addr = srv.accept()
            threading.Thread(target=handle_client, args=(client,), daemon=True).start()
        except Exception as e:
            print(f'[bridge] accept error: {e}', flush=True)
            time.sleep(1)

if __name__ == '__main__':
    main()
