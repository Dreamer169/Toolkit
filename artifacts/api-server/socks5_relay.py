"""
本地 SOCKS5 无认证中转代理
Chromium 不支持带认证的 SOCKS5，但支持无认证本地代理。
此模块在 127.0.0.1 上启动一个无认证 SOCKS5 服务器，
所有连接经过认证后转发到上游 SOCKS5。

用法：
    relay = Socks5Relay("upstream.example.com", 7777, "user", "pass")
    local_port = relay.start()
    # Chromium 使用 socks5://127.0.0.1:local_port（无需认证）
    relay.stop()
"""
import socket
import struct
import threading
import socks  # PySocks


class Socks5Relay:
    def __init__(self, upstream_host: str, upstream_port: int,
                 upstream_user: str, upstream_pass: str):
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.upstream_user = upstream_user
        self.upstream_pass = upstream_pass
        self._server: socket.socket | None = None
        self.port: int = 0

    def start(self) -> int:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self.port = self._server.getsockname()[1]
        self._server.listen(32)
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        return self.port

    def stop(self):
        try:
            if self._server:
                self._server.close()
        except Exception:
            pass

    # ── 接受循环 ──────────────────────────────────────────────────────────────
    def _accept_loop(self):
        while True:
            try:
                conn, _ = self._server.accept()
                threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
            except Exception:
                break

    # ── 处理单个客户端连接 ────────────────────────────────────────────────────
    def _handle(self, client: socket.socket):
        try:
            # ① SOCKS5 握手 — 客户端发送支持的认证方式
            hdr = self._recv_exact(client, 2)
            if not hdr or hdr[0] != 5:
                return
            n = hdr[1]
            self._recv_exact(client, n)         # 忽略认证列表
            client.sendall(b"\x05\x00")         # 选择「无需认证」

            # ② 读取 CONNECT 请求
            req = self._recv_exact(client, 4)
            if not req or req[1] != 1:          # 仅支持 CONNECT
                client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                return

            atyp = req[3]
            if atyp == 0x01:                    # IPv4
                addr = socket.inet_ntoa(self._recv_exact(client, 4))
            elif atyp == 0x03:                  # 域名
                length = self._recv_exact(client, 1)[0]
                addr = self._recv_exact(client, length).decode()
            elif atyp == 0x04:                  # IPv6
                addr = socket.inet_ntop(socket.AF_INET6, self._recv_exact(client, 16))
            else:
                return
            port = struct.unpack("!H", self._recv_exact(client, 2))[0]

            # ③ 通过上游代理（带认证）连接目标
            upstream = socks.create_connection(
                (addr, port),
                proxy_type=socks.SOCKS5,
                proxy_addr=self.upstream_host,
                proxy_port=self.upstream_port,
                proxy_username=self.upstream_user,
                proxy_password=self.upstream_pass,
            )

            # ④ 回复 Chromium 成功
            client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")

            # ⑤ 双向中继
            self._relay(client, upstream)

        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    # ── 双向数据中继 ──────────────────────────────────────────────────────────
    def _relay(self, a: socket.socket, b: socket.socket):
        def forward(src: socket.socket, dst: socket.socket):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            finally:
                for s in (src, dst):
                    try:
                        s.close()
                    except Exception:
                        pass

        t = threading.Thread(target=forward, args=(a, b), daemon=True)
        t.start()
        forward(b, a)       # 主线程负责 b→a 方向

    # ── 工具：精确读取 N 字节 ─────────────────────────────────────────────────
    @staticmethod
    def _recv_exact(s: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("连接关闭")
            buf += chunk
        return buf
