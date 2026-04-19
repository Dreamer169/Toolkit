#!/usr/bin/env python3
"""
WS-SOCKS5 Bridge — 动态多节点版 (Protocol-A compatible)
后台线程每 REFRESH_SECS 秒从本地网关同步 friend-openai 节点列表。

Env:
  GATEWAY_API   - 本地网关 (default http://localhost:8080/api)
  WS_TOKEN      - 隧道认证 token
  BRIDGE_HOST   - 监听地址 (default 127.0.0.1)
  BRIDGE_PORT   - 监听端口 (default 1089)
  REFRESH_SECS  - 节点刷新间隔 (default 60)
  WS_URL        - 手动单 URL 兜底（旧配置兼容）
"""
import asyncio, urllib.parse, os, struct, logging, socket
import threading, time, json, urllib.request, random

try:
    import websockets
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
    import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ws-socks5] %(levelname)s %(message)s")
log = logging.getLogger("ws-socks5")

GATEWAY_API  = os.environ.get("GATEWAY_API",  "http://localhost:8080/api")
WS_TOKEN     = os.environ.get("WS_TOKEN",     os.environ.get("TUNNEL_TOKEN", "123456"))
HOST         = os.environ.get("BRIDGE_HOST",  "127.0.0.1")
PORT         = int(os.environ.get("BRIDGE_PORT", "1089"))
REFRESH_SECS = int(os.environ.get("REFRESH_SECS", "60"))

# 旧配置兼容种子
_seed_raw = os.environ.get("WS_URL", "")
_seed     = [_seed_raw.strip().rstrip("/").split("?")[0]] if _seed_raw.strip() else []

_nodes_lock = threading.Lock()
_nodes      = list(_seed)
_fc         = {}

def _base_to_wss(base_http: str) -> str:
    """http(s)://domain[/api] -> wss://domain/api/stream/ws"""
    b = base_http.strip().rstrip("/")
    if b.endswith("/api"):
        b = b[:-4].rstrip("/")
    b = b.replace("https://", "wss://").replace("http://", "ws://")
    return b + "/api/stream/ws"

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
                log.warning(f"skip down: {(n.get("baseUrl") or "")[:50]} until={n.get("downUntil")}")
        return urls
    except Exception as e:
        log.warning(f"gateway sync failed: {e}")
        return None

def node_sync_loop():
    while True:
        urls = fetch_nodes_from_gateway()
        if urls is not None:
            with _nodes_lock:
                added   = [u for u in urls if u not in _nodes]
                removed = [u for u in _nodes if u not in urls]
                _nodes.clear(); _nodes.extend(urls)
                if added:   log.info(f"+nodes: {added}")
                if removed: log.info(f"-nodes: {removed}")
                for u in removed: _fc.pop(u, None)
        time.sleep(REFRESH_SECS)

def pick_node():
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

async def socks5_handshake(reader, writer):
    data = await reader.readexactly(2)
    ver, nmethods = data
    if ver != 5: raise ValueError(f"not SOCKS5 ver={ver}")
    await reader.readexactly(nmethods)
    writer.write(b"\x05\x00"); await writer.drain()
    hdr = await reader.readexactly(4)
    ver, cmd, _, atyp = hdr
    if cmd != 1:
        writer.write(b"\x05\x07\x00\x01"+b"\x00"*6); await writer.drain()
        raise ValueError(f"unsupported cmd={cmd}")
    if atyp == 1:
        raw = await reader.readexactly(4); host = socket.inet_ntoa(raw)
    elif atyp == 3:
        ln = (await reader.readexactly(1))[0]; host = (await reader.readexactly(ln)).decode()
    elif atyp == 4:
        raw = await reader.readexactly(16); host = socket.inet_ntop(socket.AF_INET6, raw)
    else: raise ValueError(f"unsupported atyp={atyp}")
    port_raw = await reader.readexactly(2)
    port = struct.unpack("!H", port_raw)[0]
    return host, port

async def handle(reader, writer):
    peer = writer.get_extra_info("peername")
    chosen = None
    try:
        host, port = await socks5_handshake(reader, writer)
    except Exception as e:
        log.warning(f"handshake fail {peer}: {e}"); writer.close(); return

    chosen = pick_node()
    if not chosen:
        log.error("no WS nodes available")
        try: writer.write(b"\x05\x01\x00\x01"+b"\x00"*6); await writer.drain()
        except: pass
        writer.close(); return

    log.info(f"{peer} -> {host}:{port} via {chosen[:60]}")
    base = chosen.split("?")[0]
    qs   = urllib.parse.urlencode({"token": WS_TOKEN, "host": host, "port": str(port)})
    url  = f"{base}?{qs}"

    try:
        async with websockets.connect(url, max_size=None, ping_interval=20, ping_timeout=30,
                                      ssl=__import__("ssl").create_default_context()) as ws:
            connected = asyncio.Event()

            async def recv_loop():
                async for msg in ws:
                    if isinstance(msg, str):
                        try:
                            d = json.loads(msg)
                            if d.get("ok"):
                                writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
                                await writer.drain(); connected.set()
                        except Exception: pass
                    else:
                        if not connected.is_set(): connected.set()
                        writer.write(msg); await writer.drain()
                writer.close()

            async def send_loop():
                await asyncio.wait_for(connected.wait(), timeout=15)
                try:
                    while True:
                        data = await reader.read(65536)
                        if not data: break
                        await ws.send(data)
                except Exception: pass
                await ws.close()

            await asyncio.gather(recv_loop(), send_loop())
        ok(chosen)
    except Exception as e:
        log.error(f"ws error {host}:{port}: {e}")
        if chosen: fail(chosen)
        try: writer.write(b"\x05\x04\x00\x01"+b"\x00"*6); await writer.drain()
        except: pass
        writer.close()

async def amain():
    log.info(f"SOCKS5 bridge listening {HOST}:{PORT} (auto-sync every {REFRESH_SECS}s)")
    with _nodes_lock:
        for u in _nodes: log.info(f"  seed: {u}")
    srv = await asyncio.start_server(handle, HOST, PORT)
    async with srv:
        await srv.serve_forever()

if __name__ == "__main__":
    # 启动后台节点同步线程
    t = threading.Thread(target=node_sync_loop, daemon=True); t.start()
    time.sleep(2)  # 等首次同步
    asyncio.run(amain())
