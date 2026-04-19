#!/usr/bin/env python3
"""
WS-SOCKS5 Bridge Client
本地启动 SOCKS5 服务器，每条连接通过 WebSocket 隧道到 Replit 中转节点出网
隐藏真实 VPS IP，流量看起来来自 Replit
"""
import asyncio
import urllib.parse
import json
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ws-bridge] %(levelname)s %(message)s"
)
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

    writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
    await writer.drain()
    return host, port


async def handle(reader, writer):
    peer = writer.get_extra_info("peername")
    try:
        host, port = await socks5_handshake(reader, writer)
    except Exception as e:
        log.warning(f"handshake fail {peer}: {e}")
        writer.close()
        return

    log.info(f"{peer} → {host}:{port}")
    url = f"{WS_URL}?t={urllib.parse.quote(WS_TOKEN, safe=chr(39))}"

    try:
        async with websockets.connect(
            url,
            max_size=None,
            ping_interval=20,
            ping_timeout=30,
            additional_headers={"X-Token": WS_TOKEN}
        ) as ws:
            await ws.send(json.dumps({"host": host, "port": port}))

            async def ws_to_local():
                try:
                    async for msg in ws:
                        writer.write(msg if isinstance(msg, bytes) else msg.encode())
                        await writer.drain()
                except Exception:
                    pass
                writer.close()

            async def local_to_ws():
                try:
                    while True:
                        data = await reader.read(65536)
                        if not data:
                            break
                        await ws.send(data)
                except Exception:
                    pass
                await ws.close()

            await asyncio.gather(ws_to_local(), local_to_ws())
    except Exception as e:
        log.error(f"ws error {host}:{port}: {e}")
        writer.close()


async def main():
    if not WS_URL or not WS_TOKEN:
        log.error("WS_URL and WS_TOKEN must be set")
        return
    srv = await asyncio.start_server(handle, HOST, PORT)
    log.info(f"SOCKS5 bridge listening {HOST}:{PORT}")
    log.info(f"Tunnel → {WS_URL}")
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
