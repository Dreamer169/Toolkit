#!/usr/bin/env python3
  """
  VPS-side SOCKS5 → Replit WS proxy bridge (Protocol-A, ws-bridge).
  用途: VPS → /api/tunnel/ws (或 /api/stream/ws) → 代理 Replit repl → internet
  支持多 BASE_URLS（逗号分隔），故障时自动降权切换。
  """
  import socket, threading, struct, os, ssl, time, random
  import urllib.parse, websocket

  # BASE_URLS: 逗号分隔多个 Replit 代理 repl 的 base URL
  _BASE_URLS_ENV = os.environ.get("BASE_URLS", "")
  _BASE_URL_LEGACY = os.environ.get("BASE_URL", "https://f7ad08f6-a36b-43b9-a1b8-5ee9a31134a1-00-3by2ge12ctshs.kirk.replit.dev")
  BASE_URLS    = [s.strip().rstrip("/") for s in _BASE_URLS_ENV.split(",") if s.strip()] if _BASE_URLS_ENV else [_BASE_URL_LEGACY]
  TUNNEL_TOKEN = os.environ.get("TUNNEL_TOKEN", "1NnCcQJcNgwlTDPEnDIkWEKzWIdmZ/4+BmsOp1/jLP6ojCWsv8+xTwcLj34Mu2viWy0q5SEoDP0q2qE5xHaRRg==")
  SOCKS_PORT   = int(os.environ.get("SOCKS_PORT", "1090"))
  # 端点路径：新部署用 /api/stream/ws，旧兼容用 /api/tunnel/ws
  WS_PATH      = os.environ.get("WS_PATH", "/api/tunnel/ws")

  _fail_counts = {}

  def pick_base():
      if len(BASE_URLS) == 1:
          return BASE_URLS[0]
      weights = [1.0 / (1 + _fail_counts.get(u, 0)) for u in BASE_URLS]
      total = sum(weights)
      r = random.random() * total
      for u, w in zip(BASE_URLS, weights):
          r -= w
          if r <= 0: return u
      return BASE_URLS[-1]

  def mark_fail(base): _fail_counts[base] = _fail_counts.get(base, 0) + 1
  def mark_ok(base): _fail_counts[base] = max(0, _fail_counts.get(base, 0) - 1)

  def handle_socks5(client: socket.socket, addr):
      chosen_base = ""
      try:
          data = client.recv(256)
          if not data or data[0] != 5: return
          client.sendall(b"\x05\x00")
          req = client.recv(256)
          if len(req) < 7 or req[1] != 1:
              client.sendall(b"\x05\x07\x00\x01" + b"\x00"*6); return

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
              client.sendall(b"\x05\x08\x00\x01" + b"\x00"*6); return

          chosen_base = pick_base()
          print(f"[ws-proxy] {addr} → {target_host}:{target_port} via {chosen_base[:50]}", flush=True)

          qs = urllib.parse.urlencode({"host": target_host, "port": target_port, "token": TUNNEL_TOKEN})
          ws_url = chosen_base.replace("http://", "ws://").replace("https://", "wss://") + WS_PATH + "?" + qs

          connected = threading.Event()
          connect_error = [None]
          ws_obj = [None]

          def on_open(ws): pass
          def on_message(ws, msg):
              if isinstance(msg, bytes):
                  try: client.sendall(msg)
                  except: ws.close()
              else:
                  try:
                      import json; d = json.loads(msg)
                      if d.get("ok"): connected.set()
                  except: pass

          def on_error(ws, error): connect_error[0] = str(error); connected.set()
          def on_close(ws, close_status_code, close_msg): connected.set(); 
              
          ws = websocket.WebSocketApp(ws_url, on_open=on_open, on_message=on_message,
                                       on_error=on_error, on_close=on_close)
          ws_obj[0] = ws

          wst = threading.Thread(target=ws.run_forever, kwargs={"sslopt": {"cert_reqs": ssl.CERT_REQUIRED}}, daemon=True)
          wst.start()

          if not connected.wait(timeout=15):
              print(f"[ws-proxy] connect timeout ({chosen_base[:40]})", flush=True)
              mark_fail(chosen_base); ws.close()
              client.sendall(b"\x05\x04\x00\x01" + b"\x00"*6); return

          if connect_error[0]:
              print(f"[ws-proxy] connect error: {connect_error[0]}", flush=True)
              mark_fail(chosen_base)
              client.sendall(b"\x05\x04\x00\x01" + b"\x00"*6); return

          mark_ok(chosen_base)
          client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack("!H", target_port))
          client.settimeout(None)
          while True:
              try: data = client.recv(4096)
              except: break
              if not data: break
              try: ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
              except: break
          ws.close()

      except Exception as e:
          print(f"[ws-proxy] error {addr}: {e}", flush=True)
          if chosen_base: mark_fail(chosen_base)
          try: client.sendall(b"\x05\x04\x00\x01" + b"\x00"*6)
          except: pass
      finally:
          try: client.close()
          except: pass

  def main():
      srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      srv.bind(("127.0.0.1", SOCKS_PORT))
      srv.listen(64)
      print(f"[ws-proxy] WS-proxy bridge (ws-bridge) on 127.0.0.1:{SOCKS_PORT}", flush=True)
      print(f"[ws-proxy] {len(BASE_URLS)} Replit proxy repl(s), WS path: {WS_PATH}", flush=True)
      for u in BASE_URLS:
          print(f"[ws-proxy]   {u}", flush=True)
      while True:
          cl, addr = srv.accept()
          threading.Thread(target=handle_socks5, args=(cl, addr), daemon=True).start()

  if __name__ == "__main__":
      main()
  