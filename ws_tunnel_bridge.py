#!/usr/bin/env python3
  """
  VPS-side SOCKS5 → Replit WS bridge (Protocol-B, proxy chain).
  支持多个 WS 服务器（WS_SERVERS，逗号分隔），故障时自动轮换。
  连接路径: VPS SOCKS5(:1091) → /api/tunnel/ws or /api/stream/ws → 代理 Replit repl → internet
  """
  import socket, threading, struct, os, time, random
  import urllib.parse, websocket

  # 支持多 WS 服务器：WS_SERVERS 优先（逗号分隔），回退到 WS_SERVER
  _WS_SERVERS_ENV = os.environ.get("WS_SERVERS", "")
  _WS_SERVER_LEGACY = os.environ.get("WS_SERVER", "wss://a738e112-67aa-4781-95c0-aefd7e0860c8-00-3owssjt9lfedl.janeway.replit.dev/api/tunnel/ws")
  WS_SERVERS = [s.strip() for s in _WS_SERVERS_ENV.split(",") if s.strip()] if _WS_SERVERS_ENV else [_WS_SERVER_LEGACY]
  WS_TOKEN   = os.environ.get("WS_TOKEN", "CHANGEME")
  SOCKS_PORT = int(os.environ.get("SOCKS_PORT", "1091"))

  # 服务器失败计数（用于降权，非硬性剔除）
  _fail_counts = {}

  def pick_server():
      """按失败次数加权随机选一个 WS 服务器。"""
      if len(WS_SERVERS) == 1:
          return WS_SERVERS[0]
      weights = [1.0 / (1 + _fail_counts.get(s, 0)) for s in WS_SERVERS]
      total = sum(weights)
      r = random.random() * total
      for s, w in zip(WS_SERVERS, weights):
          r -= w
          if r <= 0:
              return s
      return WS_SERVERS[-1]

  def mark_fail(server):
      _fail_counts[server] = _fail_counts.get(server, 0) + 1

  def mark_ok(server):
      _fail_counts[server] = max(0, _fail_counts.get(server, 0) - 1)

  def handle_socks5(client: socket.socket, addr):
      target_host = ""
      target_port = 0
      chosen_server = ""
      try:
          data = client.recv(256)
          if not data or data[0] != 5:
              return
          client.sendall(b"\x05\x00")

          req = client.recv(256)
          if len(req) < 7 or req[1] != 1:
              client.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6)
              return

          atyp = req[3]
          if atyp == 1:
              target_host = socket.inet_ntoa(req[4:8])
              target_port = struct.unpack("!H", req[8:10])[0]
          elif atyp == 3:
              hlen = req[4]
              target_host = req[5:5 + hlen].decode()
              target_port = struct.unpack("!H", req[5 + hlen:7 + hlen])[0]
          elif atyp == 4:
              target_host = socket.inet_ntop(socket.AF_INET6, req[4:20])
              target_port = struct.unpack("!H", req[20:22])[0]
          else:
              client.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6)
              return

          chosen_server = pick_server()
          print(f"[ws-bridge] {addr} → {target_host}:{target_port} via {chosen_server[:60]}", flush=True)

          base = chosen_server.rstrip("?").rstrip("&")
          sep = "&" if "?" in base else "?"
          qs = urllib.parse.urlencode({"token": WS_TOKEN, "host": target_host, "port": str(target_port)})
          ws_url = base + sep + qs

          ok_event = threading.Event()
          connect_error = [None]
          handshake_done = [False]

          def on_open(ws): pass

          def on_message(ws, msg):
              if isinstance(msg, bytes):
                  try: client.sendall(msg)
                  except: ws.close()
              else:
                  try:
                      import json; data = json.loads(msg)
                      if data.get("ok"):
                          handshake_done[0] = True
                          ok_event.set()
                      else:
                          connect_error[0] = data.get("error", "server error")
                          ok_event.set()
                  except Exception as e:
                      connect_error[0] = str(e); ok_event.set()

          def on_error(ws, error):
              connect_error[0] = str(error); ok_event.set()

          def on_close(ws, code, msg):
              ok_event.set()
              try: client.close()
              except: pass

          ws = websocket.WebSocketApp(ws_url, on_open=on_open, on_message=on_message,
                                       on_error=on_error, on_close=on_close)

          wst = threading.Thread(target=ws.run_forever, kwargs={"sslopt": {"cert_reqs": 0}}, daemon=True)
          wst.start()
          ok_event.wait(timeout=15)

          if not handshake_done[0]:
              err = connect_error[0] or "timeout"
              print(f"[ws-bridge] connect failed ({chosen_server[:40]}): {err}", flush=True)
              mark_fail(chosen_server)
              client.sendall(b"\x05\x01\x00\x01" + b"\x00" * 6)
              ws.close()
              return

          mark_ok(chosen_server)
          client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack("!H", target_port))

          def tcp_to_ws():
              try:
                  while True:
                      data = client.recv(4096)
                      if not data: break
                      ws.send(data, websocket.ABNF.OPCODE_BINARY)
              except: pass
              finally: ws.close()

          t = threading.Thread(target=tcp_to_ws, daemon=True)
          t.start()
          wst.join()
          t.join(timeout=2)

      except Exception as e:
          print(f"[ws-bridge] error {addr}: {e}", flush=True)
          if chosen_server: mark_fail(chosen_server)
      finally:
          try: client.close()
          except: pass

  def main():
      srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      srv.bind(("127.0.0.1", SOCKS_PORT))
      srv.listen(64)
      print(f"[ws-bridge] WS-SOCKS5 proxy bridge on 127.0.0.1:{SOCKS_PORT}", flush=True)
      print(f"[ws-bridge] {len(WS_SERVERS)} WS server(s):", flush=True)
      for s in WS_SERVERS:
          print(f"[ws-bridge]   {s}", flush=True)
      while True:
          client, addr = srv.accept()
          threading.Thread(target=handle_socks5, args=(client, addr), daemon=True).start()

  if __name__ == "__main__":
      main()
  