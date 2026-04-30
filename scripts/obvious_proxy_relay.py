#!/usr/bin/env python3
"""
obvious_proxy_relay.py — VPS公网SOCKS5入口 → 本地住宅代理转发

架构:
  obvious沙箱Playwright
    └─socks5h://obv:PASS@45.205.27.69:19857
        └─ 本脚本 (0.0.0.0:19857, 认证)
            └─ 127.0.0.1:10857 (xray TW住宅socks5)
                └─ TW中华电信住宅IP

用法:
  python3 obvious_proxy_relay.py               # 前台运行
  python3 obvious_proxy_relay.py --port 19857  # 指定端口
  python3 obvious_proxy_relay.py --health      # 检查中继状态
"""
from __future__ import annotations
import asyncio, struct, socket, argparse, sys, os, json, time, signal
from pathlib import Path

# ─── 认证凭证 ──────────────────────────────────────────────
RELAY_USER  = os.environ.get("RELAY_USER", "obv")
RELAY_PASS  = os.environ.get("RELAY_PASS", "Obv@R3layS3cr3t_2026")

# ─── 上游住宅SOCKS5端口列表 (非CF/AS13335) ──────────────────
UPSTREAM_PORTS = [10857, 10855, 10853, 10859, 10851, 10854]
UPSTREAM_HOST  = "127.0.0.1"

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 19857

# ─── 统计 ──────────────────────────────────────────────────
_stats = {"connections": 0, "errors": 0, "start_time": time.time()}


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _socks5_upstream_connect(target_host: str, target_port: int,
                                    atyp: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """与127.0.0.1上的xray SOCKS5握手并CONNECT到目标"""
    for port in UPSTREAM_PORTS:
        try:
            ur, uw = await asyncio.wait_for(
                asyncio.open_connection(UPSTREAM_HOST, port), timeout=8
            )
            # SOCKS5握手: 无认证
            uw.write(b"\x05\x01\x00")
            await uw.drain()
            resp = await asyncio.wait_for(ur.readexactly(2), timeout=5)
            if resp[1] != 0x00:
                uw.close()
                continue
            # CONNECT请求
            if atyp == 1:  # IPv4
                addr_bytes = socket.inet_aton(target_host)
                req = struct.pack("!BBBB", 5, 1, 0, 1) + addr_bytes
            elif atyp == 3:  # 域名
                enc = target_host.encode()
                req = struct.pack("!BBBB", 5, 1, 0, 3) + bytes([len(enc)]) + enc
            else:  # IPv6
                addr_bytes = socket.inet_pton(socket.AF_INET6, target_host)
                req = struct.pack("!BBBB", 5, 1, 0, 4) + addr_bytes
            req += struct.pack("!H", target_port)
            uw.write(req)
            await uw.drain()
            # 读取响应
            resp_hdr = await asyncio.wait_for(ur.readexactly(4), timeout=8)
            if resp_hdr[1] != 0x00:
                uw.close()
                continue
            # 跳过BND.ADDR
            batyp = resp_hdr[3]
            if batyp == 1:
                await ur.readexactly(4)
            elif batyp == 3:
                blen = (await ur.readexactly(1))[0]
                await ur.readexactly(blen)
            elif batyp == 4:
                await ur.readexactly(16)
            await ur.readexactly(2)  # BND.PORT
            return ur, uw
        except Exception:
            continue
    raise RuntimeError(f"所有上游端口均失败: {UPSTREAM_PORTS}")


async def handle_client(reader: asyncio.StreamReader,
                          writer: asyncio.StreamWriter) -> None:
    _stats["connections"] += 1
    peer = writer.get_extra_info("peername", ("?", 0))
    try:
        # ── SOCKS5握手 ──────────────────────────────────────
        header = await asyncio.wait_for(reader.readexactly(2), timeout=10)
        ver, nauth = header
        if ver != 5:
            writer.close()
            return
        methods = await asyncio.wait_for(reader.readexactly(nauth), timeout=5)
        if 2 not in methods:
            writer.write(b"\x05\xff")
            writer.close()
            return
        writer.write(b"\x05\x02")
        await writer.drain()

        # ── 用户名/密码认证 ──────────────────────────────────
        sub_ver = (await asyncio.wait_for(reader.readexactly(1), timeout=5))[0]
        ulen = (await asyncio.wait_for(reader.readexactly(1), timeout=5))[0]
        uname = await asyncio.wait_for(reader.readexactly(ulen), timeout=5)
        plen = (await asyncio.wait_for(reader.readexactly(1), timeout=5))[0]
        passwd = await asyncio.wait_for(reader.readexactly(plen), timeout=5)
        if uname.decode() != RELAY_USER or passwd.decode() != RELAY_PASS:
            writer.write(b"\x01\x01")
            writer.close()
            _stats["errors"] += 1
            return
        writer.write(b"\x01\x00")
        await writer.drain()

        # ── SOCKS5 CONNECT请求 ───────────────────────────────
        req_hdr = await asyncio.wait_for(reader.readexactly(4), timeout=10)
        ver2, cmd, rsv, atyp = req_hdr
        if cmd != 1:
            writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            writer.close()
            return
        if atyp == 1:
            ab = await asyncio.wait_for(reader.readexactly(4), timeout=5)
            target_host = socket.inet_ntoa(ab)
        elif atyp == 3:
            alen = (await asyncio.wait_for(reader.readexactly(1), timeout=5))[0]
            target_host = (await asyncio.wait_for(reader.readexactly(alen), timeout=5)).decode()
        elif atyp == 4:
            ab = await asyncio.wait_for(reader.readexactly(16), timeout=5)
            target_host = socket.inet_ntop(socket.AF_INET6, ab)
        else:
            writer.write(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
            writer.close()
            return
        port_bytes = await asyncio.wait_for(reader.readexactly(2), timeout=5)
        target_port = struct.unpack("!H", port_bytes)[0]

        # ── 连接上游xray SOCKS5 ──────────────────────────────
        try:
            ur, uw = await asyncio.wait_for(
                _socks5_upstream_connect(target_host, target_port, atyp),
                timeout=20
            )
        except Exception as e:
            writer.write(b"\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00")
            writer.close()
            _stats["errors"] += 1
            return

        # ── 告知客户端连接成功 ───────────────────────────────
        writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()

        # ── 双向中继 ─────────────────────────────────────────
        await asyncio.gather(_pipe(reader, uw), _pipe(ur, writer))
    except Exception:
        _stats["errors"] += 1
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def health_server() -> None:
    """简单HTTP健康检查服务, 端口=LISTEN_PORT+1"""
    health_port = LISTEN_PORT + 1

    async def health_handler(r, w):
        uptime = int(time.time() - _stats["start_time"])
        body = json.dumps({
            "status": "ok", "uptime_s": uptime,
            "connections": _stats["connections"], "errors": _stats["errors"],
            "upstream_ports": UPSTREAM_PORTS,
        })
        w.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n{body}".encode())
        await w.drain()
        w.close()
    try:
        srv = await asyncio.start_server(health_handler, "0.0.0.0", health_port)
        async with srv:
            await srv.serve_forever()
    except Exception as e:
        print(f"[health] 启动失败: {e}", file=sys.stderr)


async def main_server() -> None:
    srv = await asyncio.start_server(handle_client, LISTEN_HOST, LISTEN_PORT)
    addrs = [s.getsockname() for s in srv.sockets]
    print(f"[obvious-relay] 监听 {addrs}  用户={RELAY_USER}", flush=True)
    async with srv:
        await asyncio.gather(srv.serve_forever(), health_server())


def check_health(port: int = LISTEN_PORT) -> None:
    import urllib.request, urllib.error
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port+1}/health", timeout=5)
        data = json.loads(resp.read())
        print(json.dumps(data, indent=2))
        print(f"RELAY_STATUS=OK uptime={data['uptime_s']}s")
    except Exception as e:
        print(f"RELAY_STATUS=DOWN error={e}")
        sys.exit(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=LISTEN_PORT)
    ap.add_argument("--user", default=RELAY_USER)
    ap.add_argument("--pass", dest="password", default=RELAY_PASS)
    ap.add_argument("--health", action="store_true")
    args = ap.parse_args()
    if args.health:
        check_health(args.port)
        sys.exit(0)
    LISTEN_PORT = args.port
    RELAY_USER = args.user
    RELAY_PASS = args.password
    os.environ["RELAY_USER"] = RELAY_USER
    os.environ["RELAY_PASS"] = RELAY_PASS
    try:
        asyncio.run(main_server())
    except KeyboardInterrupt:
        print("\n[obvious-relay] 停止")
