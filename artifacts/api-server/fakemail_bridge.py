#!/usr/bin/env python3
"""
免费邮箱桥接服务 - 无需任何 API Key
1. 用 fng-api 从 fakenamegenerator.com 生成真实身份（含邮箱）
2. 通过 socket.io 监听 fakemailgenerator.com 收件箱
3. 对外暴露 HTTP 接口供 Express 调用
"""

import json
import threading
import time
import os
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import socketio

PORT = int(os.environ.get("FAKEMAIL_PORT", 6100))

FMG_DOMAINS = [
    "armyspy.com", "cuvox.de", "dayrep.com", "einrot.com",
    "fleckens.hu", "gustr.com", "jourrapide.com", "superrito.com", "teleworm.us"
]

_inboxes: dict = {}
_inbox_lock = threading.Lock()


def get_identity():
    try:
        from fng_api import getIdentity
        i = getIdentity(country=["us"])
        email = i.email.strip() if i.email else None
        if not email or "@" not in email:
            return None, "No email in identity"
        login, domain = email.lower().split("@", 1)
        return {
            "email": email.strip(),
            "login": login.strip(),
            "domain": domain.strip(),
            "name": i.name.strip() if i.name else "",
            "phone": i.phone.strip() if i.phone else "",
            "address": i.street.strip() if i.street else "",
            "city": i.city.strip() if i.city else "",
            "state": i.state.strip() if i.state else "",
            "zip": i.zip.strip() if i.zip else "",
            "username": i.username.strip() if i.username else login,
            "password": i.password.strip() if i.password else "",
            "birthday": i.birthday.strip() if i.birthday else "",
            "ssn": i.ssn.strip() if i.ssn else "",
        }, None
    except Exception as e:
        return None, str(e)


def start_watching(login: str, domain: str):
    key = f"{login}@{domain}"
    with _inbox_lock:
        if key in _inboxes and _inboxes[key].get("watching"):
            return _inboxes[key]
        _inboxes[key] = {"messages": [], "watching": True, "error": None, "started": time.time()}

    entry = _inboxes[key]

    def _watch():
        try:
            sio = socketio.Client(logger=False, engineio_logger=False)

            @sio.on("connect")
            def on_connect():
                sio.emit("watch_address", {"login": login, "domain": domain})

            @sio.on("new_message")
            def on_msg(data):
                entry["messages"].append({
                    "id": data.get("id"),
                    "from": data.get("from", ""),
                    "subject": data.get("subject", ""),
                    "date": data.get("date", ""),
                    "body": data.get("body", data.get("intro", "")),
                    "received_at": time.time(),
                })

            @sio.on("disconnect")
            def on_dc():
                entry["watching"] = False

            sio.connect(
                "https://www.fakemailgenerator.com",
                transports=["polling", "websocket"],
                wait_timeout=10,
            )
            # Keep connection alive for 30 minutes max
            deadline = time.time() + 1800
            while time.time() < deadline and entry.get("watching"):
                time.sleep(1)
            sio.disconnect()
        except Exception as e:
            entry["error"] = str(e)
        finally:
            entry["watching"] = False

    t = threading.Thread(target=_watch, daemon=True)
    t.start()
    return entry


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        path = parsed.path.rstrip("/")

        if path == "/identity":
            data, err = get_identity()
            if data:
                # Also start watching automatically
                start_watching(data["login"], data["domain"])
                self.send_json({"success": True, "data": data})
            else:
                self.send_json({"success": False, "error": err})

        elif path == "/watch":
            login = qs.get("login", [""])[0].strip().lower()
            domain = qs.get("domain", [""])[0].strip().lower()
            if not login or not domain:
                self.send_json({"success": False, "error": "login and domain required"})
                return
            entry = start_watching(login, domain)
            self.send_json({
                "success": True,
                "email": f"{login}@{domain}",
                "watching": entry.get("watching", False),
                "messages": entry.get("messages", []),
                "count": len(entry.get("messages", [])),
            })

        elif path == "/messages":
            login = qs.get("login", [""])[0].strip().lower()
            domain = qs.get("domain", [""])[0].strip().lower()
            key = f"{login}@{domain}"
            entry = _inboxes.get(key, {"messages": [], "watching": False})
            self.send_json({
                "success": True,
                "email": key,
                "watching": entry.get("watching", False),
                "messages": entry.get("messages", []),
                "count": len(entry.get("messages", [])),
            })

        elif path == "/domains":
            self.send_json({"success": True, "domains": FMG_DOMAINS})

        elif path == "/health":
            self.send_json({"success": True, "service": "fakemail_bridge", "port": PORT})

        else:
            self.send_json({"success": False, "error": "Unknown endpoint"}, 404)


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"FakeMail Bridge running on port {PORT}", flush=True)
    server.serve_forever()
