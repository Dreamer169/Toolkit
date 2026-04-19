#!/usr/bin/env python3
"""VPS SOCKS5 -> Replit WS proxy bridge (ws-bridge). Multi-instance BASE_URLS."""
import socket, threading, struct, os, ssl, random
import urllib.parse, websocket

_ENV = os.environ.get("BASE_URLS", "")
_LEG = os.environ.get("BASE_URL", "https://f7ad08f6-a36b-43b9-a1b8-5ee9a31134a1-00-3by2ge12ctshs.kirk.replit.dev")
BASE_URLS = [s.strip().rstrip("/") for s in _ENV.split(",") if s.strip()] if _ENV else [_LEG]
TOKEN     = os.environ.get("TUNNEL_TOKEN", "1NnCcQJcNgwlTDPEnDIkWEKzWIdmZ/4+BmsOp1/jLP6ojCWsv8+xTwcLj34Mu2viWy0q5SEoDP0q2qE5xHaRRg==")
PORT      = int(os.environ.get("SOCKS_PORT", "1090"))
WS_PATH   = os.environ.get("WS_PATH", "/api/tunnel/ws")

_fc = {}
def pick():
    if len(BASE_URLS)==1: return BASE_URLS[0]
    w=[1.0/(1+_fc.get(u,0)) for u in BASE_URLS]; t=sum(w); r=random.random()*t
    for u,wt in zip(BASE_URLS,w):
        r-=wt
        if r<=0: return u
    return BASE_URLS[-1]
def fail(u): _fc[u]=_fc.get(u,0)+1
def ok(u):   _fc[u]=max(0,_fc.get(u,0)-1)

def handle(client, addr):
    base=""
    try:
        d=client.recv(256)
        if not d or d[0]!=5: return
        client.sendall(b"\x05\x00")
        r=client.recv(256)
        if len(r)<7 or r[1]!=1: client.sendall(b"\x05\x07\x00\x01"+b"\x00"*6); return
        a=r[3]
        if a==1:   h=socket.inet_ntoa(r[4:8]);           p=struct.unpack("!H",r[8:10])[0]
        elif a==3: n=r[4]; h=r[5:5+n].decode();          p=struct.unpack("!H",r[5+n:7+n])[0]
        elif a==4: h=socket.inet_ntop(socket.AF_INET6,r[4:20]); p=struct.unpack("!H",r[20:22])[0]
        else: client.sendall(b"\x05\x08\x00\x01"+b"\x00"*6); return

        base=pick()
        print(f"[ws-proxy] {addr} -> {h}:{p} via {base[:50]}", flush=True)
        qs=urllib.parse.urlencode({"host":h,"port":p,"token":TOKEN})
        url=base.replace("https://","wss://").replace("http://","ws://")+WS_PATH+"?"+qs

        ev=threading.Event(); err=[None]
        def on_msg(ws,msg):
            if isinstance(msg,bytes):
                try: client.sendall(msg)
                except: ws.close()
            else:
                try:
                    import json; d2=json.loads(msg)
                    if d2.get("ok"): ev.set()
                except: pass
        def on_err(ws,e): err[0]=str(e); ev.set()
        def on_cls(ws,c,m): ev.set()
        ws=websocket.WebSocketApp(url,on_message=on_msg,on_error=on_err,on_close=on_cls)
        t=threading.Thread(target=ws.run_forever,kwargs={"sslopt":{"cert_reqs":ssl.CERT_REQUIRED}},daemon=True)
        t.start()
        if not ev.wait(15):
            print(f"[ws-proxy] timeout",flush=True); fail(base); ws.close()
            client.sendall(b"\x05\x04\x00\x01"+b"\x00"*6); return
        if err[0]:
            print(f"[ws-proxy] err:{err[0]}",flush=True); fail(base)
            client.sendall(b"\x05\x04\x00\x01"+b"\x00"*6); return
        ok(base)
        client.sendall(b"\x05\x00\x00\x01"+socket.inet_aton("0.0.0.0")+struct.pack("!H",p))
        client.settimeout(None)
        while True:
            try: data=client.recv(4096)
            except: break
            if not data: break
            try: ws.send(data,opcode=websocket.ABNF.OPCODE_BINARY)
            except: break
        ws.close()
    except Exception as e:
        print(f"[ws-proxy] error {addr}:{e}",flush=True)
        if base: fail(base)
        try: client.sendall(b"\x05\x04\x00\x01"+b"\x00"*6)
        except: pass
    finally:
        try: client.close()
        except: pass

def main():
    s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    s.bind(("127.0.0.1",PORT)); s.listen(64)
    print(f"[ws-proxy] SOCKS5 bridge on 127.0.0.1:{PORT}, {len(BASE_URLS)} repl(s), path:{WS_PATH}",flush=True)
    for u in BASE_URLS: print(f"[ws-proxy]   {u}",flush=True)
    while True:
        c,a=s.accept()
        threading.Thread(target=handle,args=(c,a),daemon=True).start()

if __name__=="__main__": main()
