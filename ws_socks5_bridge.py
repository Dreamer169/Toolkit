#!/usr/bin/env python3
"""
WS-SOCKS5 Bridge Client (Protocol-A compatible)
连接协议对齐 handleTunnelWs: ?token=<tok>&host=<h>&port=<p> 查询参数
"""
import asyncio
import urllib.parse
import os
import struct
import logging
import socket

try:
    import websockets
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
    import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ws-bridge] %(levelname)s %(message)s")
log = logging.getLogger("ws-bridge")

WS_URL   = os.environ.get("WS_URL", "")
WS_TOKEN = os.environ.get("WS_TOKEN", "")
HOST     = os.environ.get("BRIDGE_HOST", "127.0.0.1")
PORT     = int(os.environ.get("BRIDGE_PORT", "1089"))


async def socks5_handshake(reader, writer):
    data = await reader.readexactly(2)
    ver, nmethods = data
    if ver != 5:
        raise ValueError(f"not SOCKS5 ver={ver}")
    await reader.readexactly(nmethods)
    writer.write(b"\x05\x00")
    await writer.drain()

    hdr = await reader.readexactly(4)
    ver, cmd, _, atyp = hdr
    if cmd != 1:
        writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
        await writer.drain()
        raise ValueError(f"unsupported cmd={cmd}")

    if atyp == 1:
        raw = await reader.readexactly(4)
        host = socket.inet_ntoa(raw)
    elif atyp == 3:
        ln = (await reader.readexactly(1))[0]
        host = (await reader.readexactly(ln)).decode()
    elif atyp == 4:
        raw = await reader.readexactly(16)
        host = socket.inet_ntop(socket.AF_INET6, raw)
    else:
        raise ValueError(f"unsupported atyp={atyp}")

    port_raw = await reader.readexactly(2)
    port = struct.unpack("!H", port_raw)[0]
    return host, port


async def handle(reader, writer):
    peer = writer.get_extra_info("peername")
    try:
        host, port = await socks5_handshake(reader, writer)
    except Exception as e:
        log.warning(f"handshake fail {peer}: {e}")
        writer.close()
        return

    log.info(f"{peer} -> {host}:{port}")
    qs = urllib.parse.urlencode({"token": WS_TOKEN, "host": host, "port": str(port)})
    url = f"{WS_URL}?{qs}"

    try:
        async with websockets.connect(url, max_size=None, ping_interval=20, ping_timeout=30) as ws:
            connected = asyncio.Event()

            async def recv_loop():
                async for msg in ws:
                    if isinstance(msg, str):
                        import json
                        try:
                            d = json.loads(msg)
                            if d.get("ok"):
                                writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
                                await writer.drain()
                                connected.set()
                        except Exception:
                            pass
                    else:
                        if not connected.is_set():
                            connected.set()
                        writer.write(msg)
                        await writer.drain()
                writer.close()

            async def send_loop():
                await asyncio.wait_for(connected.wait(), timeout=15)
                try:
                    while True:
                        data = await reader.read(65536)
                        if not data:
                            break
                        await ws.send(data)
                except Exception:
                    pass
                await ws.close()

            await asyncio.gather(recv_loop(), send_loop())
    except Exception as e:
        log.error(f"ws error {host}:{port}: {e}")
        try:
            writer.write(b"\x05\x04\x00\x01" + b"\x00" * 6)
            await writer.drain()
        except Exception:
            pass
        writer.close()


async def main():
    if not WS_URL or not WS_TOKEN:
        log.error("WS_URL and WS_TOKEN must be set")
        return
    log.info(f"SOCKS5 bridge listening {HOST}:{PORT}")
    log.info(f"Tunnel -> {WS_URL}")
    srv = await asyncio.start_server(handle, HOST, PORT)
    async with srv:
        await srv.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
