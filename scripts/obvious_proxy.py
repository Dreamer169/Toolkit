#!/usr/bin/env python3
"""
obvious_proxy.py — OpenAI-compatible HTTP proxy wrapping obvious_client
Listens on PORT (default 8083), routes /v1/chat/completions to obvious.ai accounts
Round-robin across alive accounts, auto-failover on error
"""
import json, time, threading, traceback, sys, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from obvious_client import ObviousClient

PORT = int(os.environ.get("OBVIOUS_PROXY_PORT", "8083"))
ACC_DIR = Path("/root/obvious-accounts")
POLL_TIMEOUT = float(os.environ.get("OBVIOUS_POLL_TIMEOUT", "60"))

PREFERRED_ORDER = [
    "acc-1","acc-2","acc-3","acc-4","acc-5","acc-6","acc-7","acc-8",
    "acc-9","acc-10","eu-new-1","eu-new-2","eu-test2","dryrun-test",
]

def _load_clients():
    clients = []
    index = json.loads((ACC_DIR / "index.json").read_text()) if (ACC_DIR / "index.json").exists() else []
    labels_in_index = {a.get("label") for a in index}
    ordered = [l for l in PREFERRED_ORDER if l in labels_in_index]
    for acc in index:
        lbl = acc.get("label","")
        if lbl not in ordered:
            ordered.append(lbl)
    for label in ordered:
        acc_path = ACC_DIR / label
        storage = acc_path / "storage_state.json"
        manifest_path = acc_path / "manifest.json"
        if not storage.exists() or not manifest_path.exists():
            continue
        try:
            m = json.loads(manifest_path.read_text())
            tid = m.get("threadId","")
            pid = m.get("projectId","")
            if not tid or not pid:
                continue
            clients.append({"label": label, "storage": str(storage),
                            "threadId": tid, "projectId": pid})
        except Exception:
            pass
    return clients

_clients_cache = _load_clients()
_rr_lock = threading.Lock()
_rr_idx = 0
_err_counts: dict = {}

def _get_client_roundrobin():
    global _rr_idx
    with _rr_lock:
        if not _clients_cache:
            raise RuntimeError("No obvious accounts available")
        attempts = len(_clients_cache)
        for _ in range(attempts):
            idx = _rr_idx % len(_clients_cache)
            _rr_idx += 1
            c = _clients_cache[idx]
            if _err_counts.get(c["label"], 0) < 5:
                return c
        _err_counts.clear()
        return _clients_cache[_rr_idx % len(_clients_cache)]

def _extract_prompt(messages: list) -> str:
    """Convert OpenAI messages array to a single prompt string."""
    parts = []
    for m in messages:
        role = m.get("role","user")
        content = m.get("content","")
        if isinstance(content, list):
            content = " ".join(
                p.get("text","") for p in content if p.get("type")=="text"
            )
        if role == "system":
            parts.append(f"[System]: {content}")
        elif role == "assistant":
            parts.append(f"[Assistant]: {content}")
        else:
            parts.append(content)
    return "\n".join(parts)

def _chat(messages: list, model: str = "") -> str:
    prompt = _extract_prompt(messages)
    mode = "fast"
    if model and ("deep" in model or "analyst" in model):
        mode = "deep"
    last_err = None
    for attempt in range(len(_clients_cache)):
        acc = _get_client_roundrobin()
        try:
            c = ObviousClient.from_storage_state(
                acc["storage"], acc["threadId"], acc["projectId"], mode=mode
            )
            c.poll_timeout = POLL_TIMEOUT
            new_msgs = c.ask(prompt)
            text = ObviousClient.extract_text(new_msgs)
            if not text:
                text = json.dumps(new_msgs, ensure_ascii=False)[:500]
            _err_counts[acc["label"]] = 0
            return text
        except Exception as e:
            last_err = e
            _err_counts[acc["label"]] = _err_counts.get(acc["label"], 0) + 1
    raise RuntimeError(f"All accounts failed: {last_err}")

MODELS = [
    "obvious-auto","obvious-fast","obvious-deep",
    "gpt-4o","gpt-4o-mini","gpt-4-turbo",
    "claude-3-5-sonnet-20241022","claude-3-haiku-20240307",
]

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[proxy] {self.address_string()} {fmt % args}", flush=True)

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/v1/models", "/api/gateway/v1/models"):
            data = {"object": "list", "data": [
                {"id": m, "object": "model", "created": 1700000000, "owned_by": "obvious-proxy"}
                for m in MODELS
            ]}
            self._send_json(200, data)
        elif self.path in ("/health", "/api/gateway/health", "/v1/health"):
            self._send_json(200, {"ok": True, "accounts": len(_clients_cache), "ts": int(time.time())})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path in ("/v1/chat/completions", "/api/gateway/v1/chat/completions"):
            messages = body.get("messages", [])
            if not messages:
                self._send_json(400, {"error": {"message": "messages cannot be empty"}})
                return
            model = body.get("model", "obvious-auto")
            t0 = time.time()
            try:
                text = _chat(messages, model)
                elapsed = time.time() - t0
                resp = {
                    "id": f"chatcmpl-obv-{int(t0)}",
                    "object": "chat.completion",
                    "created": int(t0),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop"
                    }],
                    "usage": {
                        "prompt_tokens": sum(len(str(m.get("content",""))) for m in messages) // 4,
                        "completion_tokens": len(text) // 4,
                        "total_tokens": (sum(len(str(m.get("content",""))) for m in messages) + len(text)) // 4
                    },
                    "x_latency_s": round(elapsed, 2)
                }
                self._send_json(200, resp)
            except Exception as e:
                traceback.print_exc()
                self._send_json(503, {"error": {"message": str(e), "type": "obvious_proxy_error"}})
        else:
            self._send_json(404, {"error": "not found"})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

if __name__ == "__main__":
    print(f"[obvious-proxy] starting on port {PORT}, {len(_clients_cache)} accounts loaded", flush=True)
    for c in _clients_cache:
        print(f"  account: {c['label']} thread={c['threadId'][:12]}", flush=True)
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
