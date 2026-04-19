#!/usr/bin/env python3
"""
VPS-side SOCKS5→WebSocket bridge (Protocol-B).
Connects to /api/tunnel/ws?token=TOKEN&host=HOST&port=PORT
Uses websocket-client library (same as http_ws_bridge.py which already works).
"""
import socket, threading, struct, os, time
import urllib.parse, websocket

WS_SERVER  = os.environ.get("WS_SERVER", "wss://a738e112-67aa-4781-95c0-aefd7e0860c8-00-3owssjt9lfedl.janeway.replit.dev/api/tunnel/ws")
WS_TOKEN   = os.environ.get("WS_TOKEN", "CHANGEME")
SOCKS_PORT = int(os.environ.get("SOCKS_PORT", "1091"))


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

        print(f"[tunnel-bridge] {addr} → {target_host}:{target_port}", flush=True)

        # Build WebSocket URL with host/port in query string
        base = WS_SERVER.rstrip("?").rstrip("&")
        sep = "&" if "?" in base else "?"
        qs = urllib.parse.urlencode({"token": WS_TOKEN, "host": target_host, "port": str(target_port)})
        ws_url = base + sep + qs

        ok_event = threading.Event()
        connect_error = [None]
        ws_obj = [None]
        handshake_done = [False]

        def on_open(ws):
            pass  # wait for {"ok": true}

        def on_message(ws, msg):
            if isinstance(msg, bytes):
                try:
                    client.sendall(msg)
                except Exception:
                    ws.close()
            else:
                # first text message = connection confirmed
                try:
                    import json
                    data = json.loads(msg)
                    if data.get("ok"):
                        handshake_done[0] = True
                        ok_event.set()
                    else:
                        connect_error[0] = data.get("error", "server error")
                        ok_event.set()
                except Exception as e:
                    connect_error[0] = str(e)
                    ok_event.set()

        def on_error(ws, error):
            connect_error[0] = str(error)
            ok_event.set()

        def on_close(ws, code, msg):
            ok_event.set()
            try: client.close()
            except: pass

        ws = websocket.WebSocketApp(
            ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws_obj[0] = ws

        wst = threading.Thread(
            target=ws.run_forever,
            kwargs={"sslopt": {"cert_reqs": 0}},
            daemon=True,
        )
        wst.start()

        # Wait for {ok:true} or error
        ok_event.wait(timeout=15)

        if not handshake_done[0]:
            err = connect_error[0] or "timeout"
            print(f"[tunnel-bridge] connect failed: {err}", flush=True)
            client.sendall(b"\x05\x01\x00\x01" + b"\x00" * 6)
            ws.close()
            return

        # SOCKS5 success reply
        client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack("!H", target_port))

        # Forward local TCP → WebSocket
        def tcp_to_ws():
            try:
                while True:
                    data = client.recv(4096)
                    if not data:
                        break
                    ws.send(data, websocket.ABNF.OPCODE_BINARY)
            except Exception:
                pass
            finally:
                ws.close()

        t = threading.Thread(target=tcp_to_ws, daemon=True)
        t.start()
        wst.join()
        t.join(timeout=2)

    except Exception as e:
        print(f"[tunnel-bridge] error {addr}: {e}", flush=True)
    finally:
        try: client.close()
        except: pass


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", SOCKS_PORT))
    srv.listen(64)
    print(f"[tunnel-bridge] listening on 127.0.0.1:{SOCKS_PORT}", flush=True)
    print(f"[tunnel-bridge] → {WS_SERVER}", flush=True)
    while True:
        client, addr = srv.accept()
        threading.Thread(target=handle_socks5, args=(client, addr), daemon=True).start()


if __name__ == "__main__":
    main()
