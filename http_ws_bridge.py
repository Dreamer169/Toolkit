#!/usr/bin/env python3
"""
VPS-side SOCKS5 → Replit WebSocket tunnel bridge.
Uses WebSocket for real-time bidirectional TCP relay (no proxy buffering issues).
"""
import socket, threading, struct, os, ssl, time
import urllib.parse, websocket

BASE_URL     = os.environ.get("BASE_URL",  "https://f7ad08f6-a36b-43b9-a1b8-5ee9a31134a1-00-3by2ge12ctshs.kirk.replit.dev")
TUNNEL_TOKEN = os.environ.get("TUNNEL_TOKEN", "1NnCcQJcNgwlTDPEnDIkWEKzWIdmZ/4+BmsOp1/jLP6ojCWsv8+xTwcLj34Mu2viWy0q5SEoDP0q2qE5xHaRRg==")
SOCKS_PORT   = int(os.environ.get("SOCKS_PORT", "1090"))

def handle_socks5(client: socket.socket, addr):
    target_host = ""
    target_port = 0
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

        qs = urllib.parse.urlencode({"host": target_host, "port": target_port, "token": TUNNEL_TOKEN})
        ws_url = BASE_URL.replace("http://", "ws://").replace("https://", "wss://") + "/api/tunnel/ws?" + qs

        connected = threading.Event()
        connect_error = [None]
        ws_obj = [None]

        def on_open(ws):
            pass  # wait for JSON {ok:true}

        def on_message(ws, msg):
            if isinstance(msg, bytes):
                try: client.sendall(msg)
                except: ws.close()
            else:
                # first JSON message = connection confirmed
                try:
                    import json; d = json.loads(msg)
                    if d.get("ok"):
                        connected.set()
                except: pass

        def on_error(ws, error):
            connect_error[0] = str(error)
            connected.set()

        def on_close(ws, close_status_code, close_msg):
            connected.set()
            try: client.close()
            except: pass

        ws = websocket.WebSocketApp(ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close)
        ws_obj[0] = ws

        wst = threading.Thread(target=ws.run_forever, kwargs={"sslopt": {"cert_reqs": ssl.CERT_REQUIRED}}, daemon=True)
        wst.start()

        if not connected.wait(timeout=15):
            print(f"[bridge] ws connect timeout to {target_host}:{target_port}", flush=True)
            ws.close()
            client.sendall(b"\x05\x04\x00\x01" + b"\x00"*6)
            return

        if connect_error[0]:
            print(f"[bridge] ws connect error: {connect_error[0]}", flush=True)
            client.sendall(b"\x05\x04\x00\x01" + b"\x00"*6)
            return

        print(f"[bridge] ws tunnel ready {target_host}:{target_port}", flush=True)
        client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack("!H", target_port))

        client.settimeout(None)
        while True:
            try:
                data = client.recv(4096)
            except Exception:
                break
            if not data:
                break
            try:
                ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
            except Exception as e:
                print(f"[bridge] ws send error: {e}", flush=True)
                break

        ws.close()

    except Exception as e:
        print(f"[bridge] error {addr}: {e}", flush=True)
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
    print(f"[bridge] WS-SOCKS5 on 127.0.0.1:{SOCKS_PORT}", flush=True)
    print(f"[bridge] endpoint {BASE_URL}/api/tunnel/ws", flush=True)
    while True:
        cl, addr = srv.accept()
        threading.Thread(target=handle_socks5, args=(cl, addr), daemon=True).start()

if __name__ == "__main__":
    main()
