#!/usr/bin/env python3
"""
nestingproxy_bridge.py — 本地 HTTP/HTTPS 代理桥接到 proxy.jimjio.indevs.in
监听 127.0.0.1:10900，把 requests 转发至私有 CF Worker JSON API

协议：
  HTTP  → GET/POST /proxy 转发到 nestingproxy /proxy
  HTTPS → CONNECT 隧道 (base64 body 透传)

运行：python3 nestingproxy_bridge.py
"""
import http.server, socketserver, urllib.request, json, base64, threading, sys, os, socket, ssl, time

NEST_URL  = 'https://proxy.jimjio.indevs.in/proxy'
LISTEN_PORT = int(os.environ.get("NEST_BRIDGE_PORT", ""))

class NestProxy(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *a): pass  # silence

    def _nest_request(self, url, method, headers, body_b64=None):
        payload = {'target_url': url, 'method': method, 'headers': dict(headers)}
        if body_b64:
            payload['body_b64'] = body_b64
        data = json.dumps(payload).encode()
        req = urllib.request.Request(NEST_URL, data=data,
            headers={'Content-Type':'application/json','User-Agent':'NestBridge/1.0'})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())

    def do_CONNECT(self):
        # HTTPS CONNECT tunnel — return 200 then raw SSL passthrough via thread
        host, port = self.path.split(':')
        port = int(port)
        self.send_response(200, 'Connection established')
        self.end_headers()
        # open raw socket to remote via nestingproxy HTTP
        # For CONNECT we use direct TCP (CF Worker doesn't support raw TCP tunneling)
        # Fall back to direct connection for CONNECT
        try:
            remote = socket.create_connection((host, port), timeout=15)
            self._tunnel(self.connection, remote)
            remote.close()
        except Exception as e:
            pass

    def _tunnel(self, client, remote):
        import select
        client.setblocking(False)
        remote.setblocking(False)
        while True:
            r,_,_ = select.select([client, remote], [], [], 30)
            if not r: break
            for s in r:
                try:
                    data = s.recv(8192)
                    if not data: return
                    (remote if s is client else client).sendall(data)
                except: return

    def do_GET(self):  self._forward()
    def do_POST(self): self._forward()
    def do_PUT(self):  self._forward()
    def do_DELETE(self): self._forward()
    def do_HEAD(self): self._forward()

    def _forward(self):
        url = self.path
        if not url.startswith('http'):
            url = f'http://{self.headers.get("Host","localhost")}{self.path}'
        hdrs = {k:v for k,v in self.headers.items()
                if k.lower() not in ('proxy-connection','proxy-authorization','host')}
        body_b64 = None
        if self.command in ('POST','PUT','PATCH'):
            length = int(self.headers.get('Content-Length',0))
            if length:
                body_b64 = base64.b64encode(self.rfile.read(length)).decode()
        try:
            resp = self._nest_request(url, self.command, hdrs, body_b64)
            body = resp.get('body','')
            body_bytes = body.encode() if isinstance(body,str) else body
            self.send_response(resp.get('status',200))
            for k,v in resp.get('headers',{}).items():
                try: self.send_header(k,v)
                except: pass
            self.send_header('Content-Length', len(body_bytes))
            self.end_headers()
            self.wfile.write(body_bytes)
        except Exception as e:
            self.send_error(502, str(e)[:80])

socketserver.TCPServer.allow_reuse_address = True
if __name__ == '__main__':
    with socketserver.ThreadingTCPServer(('127.0.0.1', LISTEN_PORT), NestProxy) as srv:
        pass  # already set via class
        print(f'[nest-bridge] listening on 127.0.0.1:{LISTEN_PORT} -> {NEST_URL}', flush=True)
        srv.serve_forever()
