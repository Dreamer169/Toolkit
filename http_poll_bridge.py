#!/usr/bin/env python3
"""
VPS HTTP-poll SOCKS5 bridge (Protocol-C, sub-node relay).
Connects to sub-node /api/stream/open|read|write (HTTP polling, not WS).
No Replit WS proxy blocking. Looks like normal HTTP data streaming.

Env:
  SUBNODE_URLS  - comma-separated sub-node base URLs (required)
  STREAM_TOKEN  - auth token matching sub-node TUNNEL_TOKEN
  SOCKS_PORT    - local SOCKS5 port (default 1092)
"""
import socket, threading, struct, os, random, json
import urllib.request, urllib.parse, urllib.error, http.client, ssl as ssl_mod

_RAW = os.environ.get("SUBNODE_URLS", "")
SUBNODE_URLS = [u.strip().rstrip("/") for u in _RAW.split(",") if u.strip()]
TOKEN    = os.environ.get("STREAM_TOKEN", os.environ.get("TUNNEL_TOKEN", "1NnCcQJcNgwlTDPEnDIkWEKzWIdmZ/4+BmsOp1/jLP6ojCWsv8+xTwcLj34Mu2viWy0q5SEoDP0q2qE5xHaRRg=="))
PORT     = int(os.environ.get("SOCKS_PORT", "1092"))
R_TOUT   = int(os.environ.get("POLL_TIMEOUT", "25"))
W_TOUT   = int(os.environ.get("CHUNK_TIMEOUT", "10"))

_fc = {}
def pick():
    if not SUBNODE_URLS: return None
    if len(SUBNODE_URLS)==1: return SUBNODE_URLS[0]
    w=[1.0/(1+_fc.get(u,0)) for u in SUBNODE_URLS]; t=sum(w); r=random.random()*t
    for u,wt in zip(SUBNODE_URLS,w):
        r-=wt
        if r<=0: return u
    return SUBNODE_URLS[-1]
def fail(u): _fc[u]=_fc.get(u,0)+1
def ok(u):   _fc[u]=max(0,_fc.get(u,0)-1)

CTX = ssl_mod.create_default_context()

def http_post(url, body=b"", timeout=10):
    req=urllib.request.Request(url,data=body,method="POST")
    req.add_header("Content-Type","application/octet-stream")
    try:
        with urllib.request.urlopen(req,timeout=timeout,context=CTX) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""

def http_get_chunks(url, callback, timeout=30):
    parsed=urllib.parse.urlparse(url)
    use_ssl=parsed.scheme=="https"
    h=parsed.hostname; p=parsed.port or (443 if use_ssl else 80)
    path=parsed.path+("?"+parsed.query if parsed.query else "")
    if use_ssl: conn=http.client.HTTPSConnection(h,p,timeout=timeout,context=CTX)
    else: conn=http.client.HTTPConnection(h,p,timeout=timeout)
    try:
        conn.request("GET",path,headers={"Accept":"*/*","Connection":"close"})
        resp=conn.getresponse()
        status=resp.status
        while True:
            chunk=resp.read(4096)
            if not chunk: break
            callback(chunk)
        return status
    except Exception:
        return None
    finally: conn.close()

def http_del(url, timeout=5):
    try:
        req=urllib.request.Request(url,method="DELETE")
        urllib.request.urlopen(req,timeout=timeout,context=CTX)
    except Exception: pass

def handle(client, addr):
    base=""; sid=""
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

        base=pick()
        if not base:
            print("[poll-bridge] no SUBNODE_URLS configured",flush=True)
            client.sendall(b"\x05\x01\x00\x01"+b"\x00"*6); return

        print(f"[poll-bridge] {addr} -> {h}:{p} via {base[:50]}",flush=True)
        qs=urllib.parse.urlencode({"host":h,"port":str(p),"token":TOKEN})
        url=f"{base}/api/stream/open?{qs}"
        st,body=http_post(url,timeout=10)
        if st not in (200,201):
            print(f"[poll-bridge] open failed {st}",flush=True); fail(base)
            client.sendall(b"\x05\x04\x00\x01"+b"\x00"*6); return
        try: sid=json.loads(body)["id"]
        except Exception as e:
            print(f"[poll-bridge] parse err:{e}",flush=True); fail(base)
            client.sendall(b"\x05\x04\x00\x01"+b"\x00"*6); return

        ok(base)
        client.sendall(b"\x05\x00\x00\x01"+socket.inet_aton("0.0.0.0")+struct.pack("!H",p))
        print(f"[poll-bridge] session {sid} open",flush=True)

        tq=urllib.parse.quote(TOKEN,safe="")
        def read_loop():
            try:
                while True:
                    rurl=f"{base}/api/stream/read/{sid}?token={tq}"
                    st=http_get_chunks(rurl,lambda c: client.sendall(c),timeout=R_TOUT+5)
                    if st in (404,410):
                        break
            except Exception as e:
                print(f"[poll-bridge] read err:{e}",flush=True)
            finally:
                try: client.close()
                except: pass

        rt=threading.Thread(target=read_loop,daemon=True); rt.start()
        client.settimeout(None)
        while True:
            try: data=client.recv(4096)
            except: break
            if not data: break
            wurl=f"{base}/api/stream/write/{sid}?token={tq}"
            st2,_=http_post(wurl,body=data,timeout=W_TOUT)
            if st2 not in (200,201):
                print(f"[poll-bridge] write {st2}",flush=True); break
        rt.join(timeout=2)
    except Exception as e:
        print(f"[poll-bridge] error {addr}:{e}",flush=True)
        if base: fail(base)
    finally:
        if sid and base:
            _tq = urllib.parse.quote(TOKEN, safe=chr(34)+chr(34))
            http_del(f"{base}/api/stream/{sid}?token={_tq}")
        try: client.close()
        except: pass

def main():
    s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    s.bind(("127.0.0.1",PORT)); s.listen(64)
    print(f"[poll-bridge] HTTP-poll SOCKS5 on 127.0.0.1:{PORT}, {len(SUBNODE_URLS)} sub-node(s)",flush=True)
    for u in SUBNODE_URLS: print(f"[poll-bridge]   {u}",flush=True)
    if not SUBNODE_URLS: print("[poll-bridge]   (none - set SUBNODE_URLS env var to activate)",flush=True)
    while True:
        c,a=s.accept()
        threading.Thread(target=handle,args=(c,a),daemon=True).start()

if __name__=="__main__": main()
