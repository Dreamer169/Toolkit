#!/usr/bin/env python3
  """
  VPS-side HTTP-poll SOCKS5 bridge (子节点 TCP relay, Protocol-C).

  分工:
    - http_ws_bridge.py (port 1090): VPS → WS → 代理 Replit repl → internet  [ws-bridge / 代理链路]
    - ws_tunnel_bridge.py (port 1091): VPS → WS → 代理 Replit repl → internet  [ws-tunnel-bridge / 代理链路]
    - http_poll_bridge.py (port 1092): VPS → HTTP 轮询 → 子节点 /api/stream/* → internet  [poll-bridge / 子节点]

  子节点优势: HTTP 轮询不经过 Replit 代理拦截，WS 会被子节点 Replit 环境的反代过滤。
  子节点 /api/stream/open|read|write 是 tunnel.ts 的 HTTP 轮询变体，流量看起来像普通 HTTP 数据流。

  配置:
    SUBNODE_URLS   - 逗号分隔的子节点 base URL 列表（必须），例如:
                     https://xxx.replit.dev,https://yyy.replit.dev
    STREAM_TOKEN   - tunnel.ts TUNNEL_TOKEN 值（与子节点部署时的 token 一致）
    SOCKS_PORT     - 本机 SOCKS5 监听端口（默认 1092）
    POLL_TIMEOUT   - 单次 read 长轮询超时秒数（默认 25）
    CHUNK_TIMEOUT  - 写入 chunk 等待超时（默认 10）
  """
  import socket, threading, struct, os, time, random, queue
  import urllib.request, urllib.parse, urllib.error, http.client, json

  SUBNODE_URLS  = [u.strip().rstrip("/") for u in os.environ.get("SUBNODE_URLS", "").split(",") if u.strip()]
  STREAM_TOKEN  = os.environ.get("STREAM_TOKEN", os.environ.get("TUNNEL_TOKEN", "1NnCcQJcNgwlTDPEnDIkWEKzWIdmZ/4+BmsOp1/jLP6ojCWsv8+xTwcLj34Mu2viWy0q5SEoDP0q2qE5xHaRRg=="))
  SOCKS_PORT    = int(os.environ.get("SOCKS_PORT", "1092"))
  POLL_TIMEOUT  = int(os.environ.get("POLL_TIMEOUT", "25"))
  CHUNK_TIMEOUT = int(os.environ.get("CHUNK_TIMEOUT", "10"))

  _fail_counts = {}

  def pick_subnode():
      if not SUBNODE_URLS:
          return None
      if len(SUBNODE_URLS) == 1:
          return SUBNODE_URLS[0]
      weights = [1.0 / (1 + _fail_counts.get(u, 0)) for u in SUBNODE_URLS]
      total = sum(weights)
      r = random.random() * total
      for u, w in zip(SUBNODE_URLS, weights):
          r -= w
          if r <= 0: return u
      return SUBNODE_URLS[-1]

  def mark_fail(u): _fail_counts[u] = _fail_counts.get(u, 0) + 1
  def mark_ok(u):   _fail_counts[u] = max(0, _fail_counts.get(u, 0) - 1)

  def http_post(url, body=None, timeout=10):
      """简单 HTTP POST，返回 (status, data_bytes)。"""
      import ssl as ssl_mod
      ctx = ssl_mod.create_default_context()
      req = urllib.request.Request(url, data=body, method="POST")
      req.add_header("Content-Type", "application/octet-stream")
      try:
          with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
              return r.status, r.read()
      except urllib.error.HTTPError as e:
          return e.code, e.read()

  def http_get_chunked(url, timeout=30):
      """读取分块响应，返回 bytes 列表（阻塞直到连接关闭）。"""
      import ssl as ssl_mod
      parsed = urllib.parse.urlparse(url)
      use_ssl = parsed.scheme == "https"
      host = parsed.hostname
      port = parsed.port or (443 if use_ssl else 80)
      path = parsed.path + ("?" + parsed.query if parsed.query else "")

      if use_ssl:
          ctx = ssl_mod.create_default_context()
          conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
      else:
          conn = http.client.HTTPConnection(host, port, timeout=timeout)

      chunks = []
      try:
          conn.request("GET", path, headers={"Accept": "*/*", "Connection": "close"})
          resp = conn.getresponse()
          while True:
              chunk = resp.read(4096)
              if not chunk:
                  break
              chunks.append(chunk)
      except Exception:
          pass
      finally:
          conn.close()
      return chunks

  def handle_socks5(client: socket.socket, addr):
      chosen = ""
      session_id = ""
      base = ""
      try:
          data = client.recv(256)
          if not data or data[0] != 5: return
          client.sendall(b"\x05\x00")
          req = client.recv(256)
          if len(req) < 7 or req[1] != 1:
              client.sendall(b"\x05\x07\x00\x01" + b"\x00"*6); return

          atyp = req[3]
          if atyp == 1:
              host = socket.inet_ntoa(req[4:8])
              port = struct.unpack("!H", req[8:10])[0]
          elif atyp == 3:
              hlen = req[4]
              host = req[5:5+hlen].decode()
              port = struct.unpack("!H", req[5+hlen:7+hlen])[0]
          elif atyp == 4:
              host = socket.inet_ntop(socket.AF_INET6, req[4:20])
              port = struct.unpack("!H", req[20:22])[0]
          else:
              client.sendall(b"\x05\x08\x00\x01" + b"\x00"*6); return

          base = pick_subnode()
          if not base:
              print(f"[poll-bridge] no subnode configured", flush=True)
              client.sendall(b"\x05\x01\x00\x01" + b"\x00"*6); return

          # /api/stream/open: 打开 TCP relay session
          qs = urllib.parse.urlencode({"host": host, "port": str(port), "token": STREAM_TOKEN})
          open_url = f"{base}/api/stream/open?{qs}"
          print(f"[poll-bridge] {addr} → {host}:{port} via {base[:50]}", flush=True)

          import ssl as ssl_mod
          ctx = ssl_mod.create_default_context()
          req2 = urllib.request.Request(open_url, data=b"", method="POST")
          try:
              with urllib.request.urlopen(req2, timeout=10, context=ctx) as r:
                  body = r.read()
                  resp_data = json.loads(body)
                  if not resp_data.get("ok"):
                      raise Exception(f"open failed: {resp_data}")
                  session_id = resp_data["id"]
          except Exception as e:
              print(f"[poll-bridge] open failed ({base[:40]}): {e}", flush=True)
              mark_fail(base)
              client.sendall(b"\x05\x04\x00\x01" + b"\x00"*6); return

          mark_ok(base)
          client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack("!H", port))
          print(f"[poll-bridge] session {session_id} open {host}:{port}", flush=True)

          # 读线程: 轮询 /api/stream/read/:id → 发给 client
          def read_loop():
              try:
                  while True:
                      read_url = f"{base}/api/stream/read/{session_id}?token={urllib.parse.quote(STREAM_TOKEN)}"
                      chunks = http_get_chunked(read_url, timeout=POLL_TIMEOUT + 5)
                      if not chunks:
                          break
                      for chunk in chunks:
                          try: client.sendall(chunk)
                          except: return
              except Exception as e:
                  print(f"[poll-bridge] read_loop error: {e}", flush=True)
              finally:
                  try: client.close()
                  except: pass

          rt = threading.Thread(target=read_loop, daemon=True)
          rt.start()

          # 主线程: TCP 数据 → POST /api/stream/write/:id
          client.settimeout(None)
          while True:
              try: data = client.recv(4096)
              except: break
              if not data: break
              write_url = f"{base}/api/stream/write/{session_id}?token={urllib.parse.quote(STREAM_TOKEN)}"
              st, _ = http_post(write_url, body=data, timeout=CHUNK_TIMEOUT)
              if st not in (200, 201):
                  print(f"[poll-bridge] write returned {st}, closing", flush=True)
                  break

          rt.join(timeout=2)

      except Exception as e:
          print(f"[poll-bridge] error {addr}: {e}", flush=True)
          if base: mark_fail(base)
      finally:
          # 清理 session
          if session_id and base:
              try:
                  del_url = f"{base}/api/stream/{session_id}?token={urllib.parse.quote(STREAM_TOKEN)}"
                  req3 = urllib.request.Request(del_url, method="DELETE")
                  import ssl as ssl_mod
                  urllib.request.urlopen(req3, timeout=5, context=ssl_mod.create_default_context())
              except: pass
          try: client.close()
          except: pass

  def main():
      if not SUBNODE_URLS:
          print("[poll-bridge] WARNING: SUBNODE_URLS not set. Configure env var:", flush=True)
          print("[poll-bridge]   SUBNODE_URLS=https://xxx.replit.dev,https://yyy.replit.dev", flush=True)
      srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      srv.bind(("127.0.0.1", SOCKS_PORT))
      srv.listen(64)
      print(f"[poll-bridge] HTTP-poll bridge (sub-nodes) on 127.0.0.1:{SOCKS_PORT}", flush=True)
      print(f"[poll-bridge] {len(SUBNODE_URLS)} sub-node(s) configured, path: /api/stream/*", flush=True)
      for u in SUBNODE_URLS:
          print(f"[poll-bridge]   {u}", flush=True)
      while True:
          cl, addr = srv.accept()
          threading.Thread(target=handle_socks5, args=(cl, addr), daemon=True).start()

  if __name__ == "__main__":
      main()
  