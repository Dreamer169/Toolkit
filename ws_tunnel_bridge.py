#!/usr/bin/env python3
"""VPS SOCKS5 -> Replit WS bridge (ws-tunnel-bridge). Multi-instance WS_SERVERS."""
import socket, threading, struct, os, random
import urllib.parse, websocket

_ENV = os.environ.get("WS_SERVERS", "")
_LEG = os.environ.get("WS_SERVER", "wss://a738e112-67aa-4781-95c0-aefd7e0860c8-00-3owssjt9lfedl.janeway.replit.dev/api/tunnel/ws")
WS_SERVERS = [s.strip() for s in _ENV.split(",") if s.strip()] if _ENV else [_LEG]
WS_TOKEN   = os.environ.get("WS_TOKEN", "CHANGEME")
SOCKS_PORT = int(os.environ.get("SOCKS_PORT", "1091"))

_fc = {}
def pick():
    if len(WS_SERVERS)==1: return WS_SERVERS[0]
    w=[1.0/(1+_fc.get(s,0)) for s in WS_SERVERS]; t=sum(w); r=random.random()*t
    for s,wt in zip(WS_SERVERS,w):
        r-=wt
        if r<=0: return s
    return WS_SERVERS[-1]
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
                    import json; d2=json.loads(msg)
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
    s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    s.bind(("127.0.0.1",SOCKS_PORT)); s.listen(64)
    print(f"[ws-tunnel] SOCKS5 bridge on 127.0.0.1:{SOCKS_PORT}, {len(WS_SERVERS)} WS server(s)",flush=True)
    for u in WS_SERVERS: print(f"[ws-tunnel]   {u}",flush=True)
    while True:
        c,a=s.accept()
        threading.Thread(target=handle,args=(c,a),daemon=True).start()

if __name__=="__main__": main()
