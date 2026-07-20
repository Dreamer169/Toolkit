#!/usr/bin/env python3
"""
captcha_api.py -- HTTP 服务封装，供 Toolkit API Server 调用
POST /recognize  body: { "base64": "<base64_png>" } | { "image_path": "/abs/path" }
GET  /health
GET  /train/status
POST /train/start   body: { "skip_gen": false }
"""
import json, os, subprocess, sys, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO
import base64
from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("CAPTCHA_API_PORT", "8765"))

_train_proc = None
_train_log  = []
_train_lock = threading.Lock()


def _do_recognize(body: dict) -> dict:
    sys.path.insert(0, SCRIPT_DIR)
    from recognize import recognize_image
    if "base64" in body:
        raw = base64.b64decode(body["base64"])
        img = Image.open(BytesIO(raw))
    elif "image_path" in body:
        img = Image.open(body["image_path"])
    else:
        return {"error": "需要 base64 或 image_path 字段"}
    return recognize_image(img)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, code, data):
        try:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass  # client disconnected early, ignore

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self):
        if self.path == "/health":
            model_path = os.path.join(SCRIPT_DIR, "model", "captcha_recognition.pt")
            self._send_json(200, {
                "ok": True,
                "model_ready": os.path.exists(model_path),
                "script_dir": SCRIPT_DIR,
            })
        elif self.path == "/train/status":
            global _train_proc, _train_log
            with _train_lock:
                running = _train_proc is not None and _train_proc.poll() is None
                returncode = None if _train_proc is None else _train_proc.poll()
                logs = list(_train_log[-100:])
            self._send_json(200, {"running": running, "returncode": returncode, "logs": logs})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        global _train_proc, _train_log
        body = self._read_body()
        if self.path == "/recognize":
            try:
                result = _do_recognize(body)
                self._send_json(200, result)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
        elif self.path == "/train/start":
            with _train_lock:
                if _train_proc is not None and _train_proc.poll() is None:
                    self._send_json(409, {"error": "训练任务已在运行"})
                    return
                skip_gen = body.get("skip_gen", False)
                cmd = [sys.executable, os.path.join(SCRIPT_DIR, "train.py")]
                if skip_gen:
                    cmd.append("--skip-gen")
                _train_log = []
                _train_proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=SCRIPT_DIR, text=True, bufsize=1,
                )
            def _tail():
                for line in _train_proc.stdout:
                    with _train_lock:
                        _train_log.append(line.rstrip())
            threading.Thread(target=_tail, daemon=True).start()
            self._send_json(200, {"started": True, "skip_gen": skip_gen})
        else:
            self._send_json(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"[captcha_api] listening on :{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
