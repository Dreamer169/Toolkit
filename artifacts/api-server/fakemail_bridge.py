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


def _s(v):
    return str(v).strip() if v is not None else ""


# ── 内建身份生成器（fng_api 不可用时的降级方案）────────────────────────────
import random as _rand, secrets as _sec, string as _str

_FIRST = ['james','john','robert','michael','william','david','richard',
          'emma','olivia','sophia','isabella','ava','mia','emily','sarah',
          'alex','jordan','taylor','morgan','casey','riley','quinn']
_LAST  = ['smith','jones','chen','kim','lee','wang','patel','garcia',
          'miller','davis','wilson','anderson','clark','lewis','hall']

def _gen_identity_builtin():
    first = _rand.choice(_FIRST)
    last  = _rand.choice(_LAST)
    num   = _rand.randint(10, 9999)
    domain = _rand.choice(FMG_DOMAINS)
    login = f"{first}{last[:3]}{num}".lower()[:20]
    email = f"{login}@{domain}"
    pw_chars = _str.ascii_letters + _str.digits + "!@#$"
    password = _sec.token_hex(3).upper() + _sec.token_hex(3) + _sec.token_urlsafe(4)
    return {
        "email": email, "login": login, "domain": domain,
        "username": login, "password": password,
        "name": f"{first.capitalize()} {last.capitalize()}",
        "first_name": first.capitalize(), "last_name": last.capitalize(),
        "guid": _sec.token_hex(8),
    }

def get_identity():
    try:
        from fng_api import getIdentity
        i = getIdentity(country=["us"])
        email = i.email.strip() if i.email else None
        if not email or "@" not in email:
            raise ImportError("No valid email from fng_api")
        login, domain = email.lower().split("@", 1)
        return {
            # 基本账号信息
            "email": email.strip(),
            "login": login.strip(),
            "domain": domain.strip(),
            "username": _s(i.username) or login,
            "password": _s(i.password),
            "guid": _s(i.guid),
            # 个人信息
            "name": _s(i.name),
            "phone": _s(i.phone),
            "birthday": _s(i.birthday),
            "birthdayDay": _s(i.birthdayDay),
            "birthdayMonth": _s(i.birthdayMonth),
            "birthdayYear": _s(i.birthdayYear),
            "age": _s(i.age),
            "zodiac": _s(i.zodiac),
            "blood": _s(i.blood),
            "color": _s(i.color),
            "motherMaidenName": _s(i.motherMaidenName),
            # 地址信息
            "street": _s(i.street),
            "city": _s(i.city),
            "state": _s(i.state),
            "zip": _s(i.zip),
            "coords": _s(i.coords),
            "countryCode": _s(i.countryCode),
            # 工作信息
            "company": _s(i.company),
            "occupation": _s(i.occupation),
            "website": _s(i.website),
            # 体格信息
            "height": _s(i.height),
            "heightcm": _s(i.heightcm),
            "weight": _s(i.weight),
            "weightkg": _s(i.weightkg),
            "vehicle": _s(i.vehicle),
            # 财务信息
            "ssn": _s(i.ssn),
            "card": _s(i.card),
            "cvv2": _s(i.cvv2),
            "expiration": _s(i.expiration),
            "moneygram": _s(i.moneygram),
            "westernunion": _s(i.westernunion),
            "ups": _s(i.ups),
            # 技术信息
            "useragent": _s(i.useragent),
        }, None
    except Exception:
        # fng_api 不可用，使用内建生成器
        ident = _gen_identity_builtin()
        return ident, None


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
