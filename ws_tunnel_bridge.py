#!/usr/bin/env python3
"""VPS SOCKS5 -> Replit WS bridge (ws-tunnel-bridge).
动态从网关发现 friend-openai 节点，每 REFRESH_SECS 秒同步一次。

Env:
  GATEWAY_API   - 本地网关 (default http://localhost:8080/api)
  WS_TOKEN      - 隧道认证 token
  SOCKS_PORT    - 本地 SOCKS5 端口 (default 1091)
  REFRESH_SECS  - 节点刷新间隔 (default 60)
  WS_SERVERS    - 手动种子 WSS URL（逗号分隔），网关不可达时兜底
"""
import socket, threading, struct, os, random, time, json
import urllib.parse, urllib.request, websocket

GATEWAY_API  = os.environ.get("GATEWAY_API",  "http://localhost:8080/api")
WS_TOKEN     = os.environ.get("WS_TOKEN",     os.environ.get("TUNNEL_TOKEN", "123456"))
SOCKS_PORT   = int(os.environ.get("SOCKS_PORT", "1091"))
REFRESH_SECS = int(os.environ.get("REFRESH_SECS", "60"))

_seed_raw  = os.environ.get("WS_SERVERS", os.environ.get("WS_SERVER", ""))
_seed      = [s.strip() for s in _seed_raw.split(",") if s.strip()]

_nodes_lock = threading.Lock()
_nodes      = list(_seed)
_fc         = {}

def _base_to_wss(base_http: str) -> str:
    """http(s)://domain[/api] -> wss://domain/api/tunnel/ws"""
    b = base_http.strip().rstrip("/")
    if b.endswith("/api"):
        b = b[:-4].rstrip("/")
    b = b.replace("https://", "wss://").replace("http://", "ws://")
    return b + "/api/tunnel/ws"

def fetch_nodes_from_gateway():
    try:
        req = urllib.request.Request(f"{GATEWAY_API}/gateway/nodes/status",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        urls = []
        for n in data.get("nodes", []):
            if n.get("status") == "ready":
                base = (n.get("baseUrl") or "").strip()
                if base:
                    urls.append(_base_to_wss(base))
            elif n.get("status") == "down":
                print("[ws-tunnel] skip down: {} until={}".format((n.get("baseUrl") or "")[:50], n.get("downUntil")), flush=True)
        return urls
    except Exception as e:
        print(f"[ws-tunnel] gateway sync failed: {e}", flush=True)
        return None

def node_sync_loop():
    while True:
        urls = fetch_nodes_from_gateway()
        if urls is not None:
            with _nodes_lock:
                added   = [u for u in urls if u not in _nodes]
                removed = [u for u in _nodes if u not in urls]
                _nodes.clear(); _nodes.extend(urls)
                if added:   print(f"[ws-tunnel] +nodes: {added}", flush=True)
                if removed: print(f"[ws-tunnel] -nodes: {removed}", flush=True)
                for u in removed: _fc.pop(u, None)
        time.sleep(REFRESH_SECS)

def pick():
    with _nodes_lock: nodes = list(_nodes)
    if not nodes: return None
    if len(nodes) == 1: return nodes[0]
    w=[1.0/(1+_fc.get(s,0)) for s in nodes]; t=sum(w); r=random.random()*t
    for s,wt in zip(nodes,w):
        r-=wt
        if r<=0: return s
    return nodes[-1]
def fail(s): _fc[s]=_fc.get(s,0)+1
def ok(s):   _fc[s]=max(0,_fc.get(s,0)-1)

def handle(client, addr):
    chosen=""
    try:
        d=client.recv(256)
        if not d or d[0]!=5: return
        client.sendall(b"\x05\x00")
        r=client.recv(256)
        if len(r)<7 or r[1]!=1: client.sendall(b"\x05\x07\x00\x01"+b"\x00"*6); return
        a=r[3]
        if a==1:   h=socket.inet_ntoa(r[4:8]);              p=struct.unpack("!H",r[8:10])[0]
        elif a==3: n=r[4]; h=r[5:5+n].decode();             p=struct.unpack("!H",r[5+n:7+n])[0]
        elif a==4: h=socket.inet_ntop(socket.AF_INET6,r[4:20]); p=struct.unpack("!H",r[20:22])[0]
        else: client.sendall(b"\x05\x08\x00\x01"+b"\x00"*6); return

        chosen=pick()
        if not chosen:
            print("[ws-tunnel] no nodes available", flush=True)
            client.sendall(b"\x05\x01\x00\x01"+b"\x00"*6); return

        print(f"[ws-tunnel] {addr} -> {h}:{p} via {chosen[:60]}", flush=True)
        base=chosen.rstrip("?").rstrip("&")
        sep="&" if "?" in base else "?"
        qs=urllib.parse.urlencode({"token":WS_TOKEN,"host":h,"port":str(p)})
        url=base+sep+qs

        ev=threading.Event(); err=[None]; done=[False]
        def on_msg(ws,msg):
            if isinstance(msg,bytes):
                try: client.sendall(msg)
                except: ws.close()
            else:
                try:
                    d2=json.loads(msg)
                    if d2.get("ok"): done[0]=True; ev.set()
                    else: err[0]=d2.get("error","err"); ev.set()
                except Exception as e: err[0]=str(e); ev.set()
        def on_err(ws,e): err[0]=str(e); ev.set()
        def on_cls(ws,c,m): ev.set()

        ws=websocket.WebSocketApp(url,on_message=on_msg,on_error=on_err,on_close=on_cls)
        wst=threading.Thread(target=ws.run_forever,kwargs={"sslopt":{"cert_reqs":0}},daemon=True)
        wst.start()
        ev.wait(timeout=15)
        if not done[0]:
            print(f"[ws-tunnel] failed ({chosen[:40]}): {err[0] or timeout}",flush=True)
            fail(chosen); client.sendall(b"\x05\x01\x00\x01"+b"\x00"*6); ws.close(); return
        ok(chosen)
        client.sendall(b"\x05\x00\x00\x01"+socket.inet_aton("0.0.0.0")+struct.pack("!H",p))
        def tcp_to_ws():
            try:
                while True:
                    data=client.recv(4096)
                    if not data: break
                    ws.send(data,websocket.ABNF.OPCODE_BINARY)
            except: pass
            finally: ws.close()
        t=threading.Thread(target=tcp_to_ws,daemon=True); t.start()
        wst.join(); t.join(timeout=2)
    except Exception as e:
        print(f"[ws-tunnel] error {addr}:{e}",flush=True)
        if chosen: fail(chosen)
    finally:
        try: client.close()
        except: pass

def main():
    t=threading.Thread(target=node_sync_loop,daemon=True); t.start()
    time.sleep(2)
    s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    s.bind(("127.0.0.1",SOCKS_PORT)); s.listen(64)
    with _nodes_lock: nc=len(_nodes)
    print(f"[ws-tunnel] SOCKS5 bridge on 127.0.0.1:{SOCKS_PORT}, {nc} WS server(s) (auto-sync every {REFRESH_SECS}s)",flush=True)
    with _nodes_lock:
        for u in _nodes: print(f"[ws-tunnel]   {u}",flush=True)
    while True:
        c,a=s.accept()
        threading.Thread(target=handle,args=(c,a),daemon=True).start()

if __name__=="__main__": main()
