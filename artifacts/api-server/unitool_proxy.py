#!/usr/bin/env python3
"""
unitool.ai → OpenAI 兼容反代 v1.0
监听 :8089
POST /v1/chat/completions  → https://unitool.ai/api/widget/stream
GET  /v1/models            → 返回支持的模型列表
"""
import json, time, uuid, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError
import ssl, os, sys

PORT = int(os.environ.get("PORT", 8089))
TARGET = "https://unitool.ai/api/widget/stream"

# model → {service, model}
MODEL_MAP = {
    # ChatGPT
    "gpt-4o":               {"service": "chatgpt", "model": "gpt-4o"},
    "gpt-4o-mini":          {"service": "chatgpt", "model": "gpt-4o-mini"},
    "gpt-4-turbo":          {"service": "chatgpt", "model": "gpt-4-turbo"},
    "gpt-4":                {"service": "chatgpt", "model": "gpt-4"},
    "gpt-3.5-turbo":        {"service": "chatgpt", "model": "gpt-3.5-turbo"},
    "o1":                   {"service": "chatgpt", "model": "o1"},
    "o1-mini":              {"service": "chatgpt", "model": "o1-mini"},
    "o3-mini":              {"service": "chatgpt", "model": "o3-mini"},
    # Claude
    "claude-3-5-sonnet-20241022": {"service": "claude", "model": "claude-3-5-sonnet-20241022"},
    "claude-3-5-haiku-20241022":  {"service": "claude", "model": "claude-3-5-haiku-20241022"},
    "claude-3-opus-20240229":     {"service": "claude", "model": "claude-3-opus-20240229"},
    "claude-3-sonnet-20240229":   {"service": "claude", "model": "claude-3-sonnet-20240229"},
    "claude-3-haiku-20240307":    {"service": "claude", "model": "claude-3-haiku-20240307"},
    "claude-sonnet-4-5":          {"service": "claude", "model": "claude-sonnet-4-5"},
    # Gemini
    "gemini-1.5-pro":       {"service": "gemini", "model": "gemini-1.5-pro"},
    "gemini-1.5-flash":     {"service": "gemini", "model": "gemini-1.5-flash"},
    "gemini-2.0-flash":     {"service": "gemini", "model": "gemini-2.0-flash"},
    "gemini-2.5-pro":       {"service": "gemini", "model": "gemini-2.5-pro"},
    # xAI
    "grok-2":               {"service": "x-ai", "model": "grok-2"},
    "grok-3":               {"service": "x-ai", "model": "grok-3"},
    "grok-3-mini":          {"service": "x-ai", "model": "grok-3-mini"},
}

MODELS_LIST = [{"id": m, "object": "model", "created": 1700000000, "owned_by": "unitool"} for m in MODEL_MAP]

ctx = ssl.create_default_context()

def stream_unitool(service, model, messages, stream=True):
    body = json.dumps({"service": service, "model": model, "messages": messages}).encode()
    req = Request(TARGET, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://unitool.ai",
        "Referer": "https://unitool.ai/en/chatgpt",
    }, method="POST")
    with urlopen(req, context=ctx, timeout=120) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
                yield chunk.get("content", "")
            except Exception:
                continue

def make_chunk(model, content, finish=None):
    delta = {"content": content} if content else {}
    if finish:
        delta = {}
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {fmt % args}", flush=True)

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/v1/models" or self.path == "/v1/models/":
            body = json.dumps({"object": "list", "data": MODELS_LIST}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors()
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path not in ("/v1/chat/completions", "/v1/chat/completions/"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
            return

        model = body.get("model", "gpt-4o-mini")
        messages = body.get("messages", [])
        do_stream = body.get("stream", False)

        mapping = MODEL_MAP.get(model, {"service": "chatgpt", "model": model})
        service = mapping["service"]
        unitool_model = mapping["model"]

        print(f"[REQ] model={model} → service={service}/{unitool_model} stream={do_stream}", flush=True)

        try:
            if do_stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_cors()
                self.end_headers()
                for content in stream_unitool(service, unitool_model, messages):
                    chunk = make_chunk(model, content)
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                stop_chunk = make_chunk(model, "", finish_reason="stop")
                self.wfile.write(f"data: {json.dumps(stop_chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                full_text = "".join(stream_unitool(service, unitool_model, messages))
                resp_body = json.dumps({
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": full_text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_cors()
                self.end_headers()
                self.wfile.write(resp_body)
        except URLError as e:
            err = json.dumps({"error": {"message": str(e), "type": "upstream_error"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err)
        except BrokenPipeError:
            pass
        except Exception as e:
            print(f"[ERR] {e}", flush=True)
            try:
                err = json.dumps({"error": {"message": str(e)}}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err)
            except Exception:
                pass

class ThreadedHTTPServer(HTTPServer):
    def process_request(self, request, client_address):
        t = threading.Thread(target=self._process_request, args=(request, client_address))
        t.daemon = True
        t.start()
    def _process_request(self, request, client_address):
        self.finish_request(request, client_address)
        self.shutdown_request(request)

if __name__ == "__main__":
    server = ThreadedHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[unitool-proxy] listening on :{PORT}", flush=True)
    print(f"[unitool-proxy] models: {len(MODEL_MAP)}", flush=True)
    server.serve_forever()
