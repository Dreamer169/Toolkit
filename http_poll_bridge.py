#!/usr/bin/env python3
"""
VPS HTTP-poll SOCKS5 bridge (Protocol-C, sub-node relay).
Connects to sub-node /api/stream/open|read|write (HTTP polling, not WS).
No Replit WS proxy blocking. Looks like normal HTTP data streaming.

Env:
  GATEWAY_API   - local gateway API base URL (default http://localhost:8080/api)
  STREAM_TOKEN  - auth token matching sub-node TUNNEL_TOKEN
  SUBNODE_URLS  - manual seed URLs (comma-separated), used when gateway unavailable
  SOCKS_PORT    - local SOCKS5 port (default 1092)
  REFRESH_SECS  - how often to re-sync node list from gateway (default 60)
"""
import socket, threading, struct, os, random, json, time
import urllib.request, urllib.parse, urllib.error, http.client, ssl as ssl_mod
from concurrent.futures import ThreadPoolExecutor, as_completed

TOKEN        = os.environ.get("STREAM_TOKEN", os.environ.get("TUNNEL_TOKEN", ""))
PORT         = int(os.environ.get("SOCKS_PORT", "1092"))
R_TOUT       = int(os.environ.get("POLL_TIMEOUT", "25"))
W_TOUT       = int(os.environ.get("CHUNK_TIMEOUT", "10"))
REFRESH_SECS = int(os.environ.get("REFRESH_SECS", "60"))
GATEWAY_API  = os.environ.get("GATEWAY_API", "http://localhost:8080/api")

# 初始种子：来自 env，gateway 不可用时的兜底
_seed_raw = os.environ.get("SUBNODE_URLS", "")
_seed     = [u.strip().rstrip("/") for u in _seed_raw.split(",") if u.strip()]

# 动态节点列表（由后台线程维护）
_nodes_lock = threading.Lock()
_nodes      = list(_seed)
_fc         = {}   # fail counters per URL

# stream 探测缓存：url -> (ok: bool, timestamp: float)
_stream_probe_cache = {}
_STREAM_PROBE_TTL   = 120  # 秒：同一节点两次探测最小间隔

CTX = ssl_mod.create_default_context()

def http_post(url, body=b"", timeout=10):
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""

def http_del(url, timeout=5):
    try:
        req = urllib.request.Request(url, method="DELETE")
        urllib.request.urlopen(req, timeout=timeout, context=CTX)
    except Exception: pass

def strip_gateway_path(b):
    for sfx in ("/api/gateway", "/api"):
        if b.endswith(sfx):
            return b[:-len(sfx)]
    return b

def probe_stream(base_url):
    """
    独立探测节点的 /api/stream 通道，与 AI 健康状态完全解耦。
    向节点发一个 open 请求，拿到 session id 后立即 DELETE 关闭。
    有缓存 TTL，避免频繁探测。
    """
    now = time.time()
    cached = _stream_probe_cache.get(base_url)
    if cached and now - cached[1] < _STREAM_PROBE_TTL:
        return cached[0]

    stream_base = strip_gateway_path(base_url)
    qs  = urllib.parse.urlencode({"host": "1.1.1.1", "port": "80", "token": TOKEN})
    url = f"{stream_base}/api/stream/open?{qs}"
    try:
        st, body = http_post(url, timeout=8)
        if st in (200, 201):
            try:
                sid = json.loads(body).get("id", "")
                if sid:
                    tq = urllib.parse.quote(TOKEN, safe="")
                    http_del(f"{stream_base}/api/stream/{sid}?token={tq}")
                    _stream_probe_cache[base_url] = (True, now)
                    return True
            except Exception:
                pass
        _stream_probe_cache[base_url] = (False, now)
        return False
    except Exception:
        _stream_probe_cache[base_url] = (False, now)
        return False

def fetch_nodes_from_gateway():
    """
    Pull all enabled friend-openai nodes from gateway.
    Uses gateway streamStatus: ok=trust, down=skip, unknown=probe ourselves.
    AI status is fully decoupled from proxy (stream) availability.
    """
    try:
        req = urllib.request.Request(f"{GATEWAY_API}/gateway/nodes/status",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())

        direct_ok    = []  # gateway confirmed stream ok
        need_probe   = []  # gateway unknown, probe ourselves
        gateway_down = []  # gateway confirmed stream down

        for n in data.get("nodes", []):
            if n.get("status") == "disabled":
                continue
            base = (n.get("baseUrl") or "").rstrip("/")
            if not base:
                continue
            ai_st     = n.get("status", "unknown")
            stream_st = n.get("streamStatus", "unknown")
            if stream_st == "ok":
                direct_ok.append((base, ai_st))
            elif stream_st == "down":
                gateway_down.append((base, ai_st))
            else:
                need_probe.append((base, ai_st))

        urls = [base for base, _ in direct_ok]
        for base, ai_st in direct_ok:
            if ai_st != "ready":
                print(f"[poll-bridge] +stream(gw-ok) AI:{ai_st} {base[:70]}", flush=True)

        for base, ai_st in gateway_down:
            print(f"[poll-bridge] skip(gw-down) AI:{ai_st} {base[:70]}", flush=True)

        if need_probe:
            with ThreadPoolExecutor(max_workers=min(len(need_probe), 5)) as ex:
                futs = {ex.submit(probe_stream, base): (base, ai_st)
                        for base, ai_st in need_probe}
                for fut in as_completed(futs):
                    base, ai_st = futs[fut]
                    try:
                        stream_ok = fut.result()
                    except Exception:
                        stream_ok = False
                    if stream_ok:
                        urls.append(base)
                        print(f"[poll-bridge] +stream(probe-ok) AI:{ai_st} {base[:70]}", flush=True)
                    else:
                        print(f"[poll-bridge] -stream(probe-fail) AI:{ai_st} {base[:70]}", flush=True)

        return urls
    except Exception as e:
        print(f"[poll-bridge] gateway sync failed: {e}", flush=True)
        return None
def node_sync_loop():
    """后台线程：每 REFRESH_SECS 秒从网关同步一次子节点列表。"""
    while True:
        urls = fetch_nodes_from_gateway()
        if urls is not None:
            with _nodes_lock:
                added   = [u for u in urls if u not in _nodes]
                removed = [u for u in _nodes if u not in urls]
                _nodes.clear()
                _nodes.extend(urls)
                for su in _seed:
                    if su not in _nodes: _nodes.insert(0, su)
                if added:
                    print(f"[poll-bridge] +nodes: {added}", flush=True)
                if removed:
                    print(f"[poll-bridge] -nodes: {removed}", flush=True)
                    for u in removed:
                        _fc.pop(u, None)
        time.sleep(REFRESH_SECS)

def pick():
    with _nodes_lock:
        nodes = list(_nodes)
    if not nodes: return None
    if len(nodes) == 1: return nodes[0]
    w = [1.0 / (1 + _fc.get(u, 0)) for u in nodes]
    t = sum(w); r = random.random() * t
    for u, wt in zip(nodes, w):
        r -= wt
        if r <= 0: return u
    return nodes[-1]

def fail(u): _fc[u] = _fc.get(u, 0) + 1
def ok(u):   _fc[u] = max(0, _fc.get(u, 0) - 1)

def http_get_chunks(url, callback, timeout=30):
    parsed = urllib.parse.urlparse(url)
    use_ssl = parsed.scheme == "https"
    h = parsed.hostname; p = parsed.port or (443 if use_ssl else 80)
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    if use_ssl: conn = http.client.HTTPSConnection(h, p, timeout=timeout, context=CTX)
    else:        conn = http.client.HTTPConnection(h, p, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Accept": "*/*", "Connection": "close"})
        resp = conn.getresponse()
        status = resp.status
        if status in (204, 404, 410):
            try: resp.read()
            except Exception: pass
            return status
        while True:
            chunk = resp.read(4096)
            if not chunk: break
            callback(chunk)
        return status
    except Exception: return None
    finally: conn.close()

def handle(client, addr):
    base = ""; sid = ""
    try:
        d = client.recv(256)
        if not d or d[0] != 5: return
        client.sendall(b"\x05\x00")
        r = client.recv(256)
        if len(r) < 7 or r[1] != 1:
            client.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6); return
        a = r[3]
        if   a == 1: h = socket.inet_ntoa(r[4:8]);               p = struct.unpack("!H", r[8:10])[0]
        elif a == 3: n = r[4]; h = r[5:5+n].decode();             p = struct.unpack("!H", r[5+n:7+n])[0]
        elif a == 4: h = socket.inet_ntop(socket.AF_INET6,r[4:20]); p = struct.unpack("!H", r[20:22])[0]
        else: client.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6); return

        base = pick()
        if not base:
            print("[poll-bridge] no subnodes available", flush=True)
            client.sendall(b"\x05\x01\x00\x01" + b"\x00" * 6); return

        print(f"[poll-bridge] {addr} -> {h}:{p} via {base[:60]}", flush=True)
        qs  = urllib.parse.urlencode({"host": h, "port": str(p), "token": TOKEN})
        url = f"{strip_gateway_path(base)}/api/stream/open?{qs}"
        st, body = http_post(url, timeout=10)
        if st not in (200, 201):
            print(f"[poll-bridge] open failed {st}", flush=True); fail(base)
            client.sendall(b"\x05\x04\x00\x01" + b"\x00" * 6); return
        try: sid = json.loads(body)["id"]
        except Exception as e:
            print(f"[poll-bridge] parse err:{e}", flush=True); fail(base)
            client.sendall(b"\x05\x04\x00\x01" + b"\x00" * 6); return

        ok(base)
        client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack("!H", p))
        print(f"[poll-bridge] session {sid} open", flush=True)

        tq = urllib.parse.quote(TOKEN, safe="")
        def read_loop():
            try:
                while True:
                    rurl = f"{strip_gateway_path(base)}/api/stream/read/{sid}?token={tq}"
                    st   = http_get_chunks(rurl, lambda c: client.sendall(c), timeout=R_TOUT+5)
                    if st in (204, 404, 410): break
            except Exception as e:
                print(f"[poll-bridge] read err:{e}", flush=True)
            finally:
                try: client.close()
                except: pass

        rt = threading.Thread(target=read_loop, daemon=True); rt.start()
        client.settimeout(None)
        while True:
            try: data = client.recv(4096)
            except: break
            if not data: break
            wurl = f"{strip_gateway_path(base)}/api/stream/write/{sid}?token={tq}"
            st2, _ = http_post(wurl, body=data, timeout=W_TOUT)
            if st2 not in (200, 201):
                print(f"[poll-bridge] write {st2}", flush=True); break
        rt.join(timeout=2)
    except Exception as e:
        print(f"[poll-bridge] error {addr}:{e}", flush=True)
        if base: fail(base)
    finally:
        if sid and base:
            tq2 = urllib.parse.quote(TOKEN, safe="")
            http_del(f"{base}/api/stream/{sid}?token={tq2}")
        try: client.close()
        except: pass

def main():
    # 启动后台节点同步线程
    t = threading.Thread(target=node_sync_loop, daemon=True)
    t.start()

    # 等一次同步完成（最多 5 秒），用网关数据替换种子
    time.sleep(2)

    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", PORT)); s.listen(64)
    with _nodes_lock: n_count = len(_nodes)
    print(f"[poll-bridge] HTTP-poll SOCKS5 on 127.0.0.1:{PORT}, "
          f"{n_count} sub-node(s) (auto-sync every {REFRESH_SECS}s)", flush=True)
    with _nodes_lock:
        for u in _nodes: print(f"[poll-bridge]   {u}", flush=True)
    if not _nodes: print("[poll-bridge]   (none - waiting for gateway sync)", flush=True)
    while True:
        c, a = s.accept()
        threading.Thread(target=handle, args=(c, a), daemon=True).start()

if __name__ == "__main__": main()
