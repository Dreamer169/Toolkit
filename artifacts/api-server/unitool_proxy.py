#!/usr/bin/env python3
"""
unitool.ai → OpenAI 兼容反代 v2.0
监听 :8089
POST /v1/chat/completions  → https://unitool.ai/api/chats/{id}/messages
GET  /v1/models            → 返回支持的模型列表
"""
import json, time, uuid, threading, ssl, os, sys, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import urlencode

PORT = int(os.environ.get("PORT", 8089))
BASE = "https://unitool.ai"
SSID_FILE = "/tmp/unitool_ssid.txt"

# 从文件或环境读取 cookie
def get_ssid():
    ssid = os.environ.get("UNITOOL_SSID", "")
    if not ssid and os.path.exists(SSID_FILE):
        ssid = open(SSID_FILE).read().strip()
    if not ssid:
        ssid = "ccc7e893a79df9eb6111789d3c73b7acb317a3b21b6ad84622ee9c092c15ec92ed9d1289cfc04e507844d2f9711b81aabe384330242bd2a7c0a731aea7bccbac8623c7dc307f4df4ac4777605e96638ea08a672371f0d53b5e1fda31e1514e76db8c9118ef73b512c4f0f73d45d4b20ca068d6f3dceea676a4f706e48502586c298f14c7"
    return ssid

# model → unitool service_id (model-specific chat creates its own context)
MODEL_MAP = {
    # ChatGPT
    "gpt-4o":                     "gpt-4o",
    "gpt-4o-mini":                "gpt-4o-mini",
    "gpt-4-turbo":                "gpt-4-turbo",
    "gpt-4":                      "gpt-4",
    "gpt-3.5-turbo":              "gpt-3.5-turbo",
    "gpt-5":                      "gpt-5",
    "o1":                         "o1",
    "o1-mini":                    "o1-mini",
    "o3-mini":                    "o3-mini",
    "o3":                         "o3",
    # Claude
    "claude-3-5-sonnet-20241022": "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022":  "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229":     "claude-3-opus-20240229",
    "claude-3-sonnet-20240229":   "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307":    "claude-3-haiku-20240307",
    "claude-opus-4-5":            "claude-opus-4-5",
    "claude-sonnet-4-5":          "claude-sonnet-4-5",
    "claude-3-7-sonnet-20250219": "claude-3-7-sonnet-20250219",
    # Gemini
    "gemini-1.5-pro":             "gemini-1.5-pro",
    "gemini-1.5-flash":           "gemini-1.5-flash",
    "gemini-2.0-flash":           "gemini-2.0-flash",
    "gemini-2.5-pro":             "gemini-2.5-pro",
    "gemini-2.0-flash-lite":      "gemini-2.0-flash-lite",
    # xAI
    "grok-2":                     "grok-2",
    "grok-3":                     "grok-3",
    "grok-3-mini":                "grok-3-mini",
    "grok-beta":                  "grok-beta",
}

MODELS_LIST = [
    {"id": m, "object": "model", "created": 1700000000, "owned_by": "unitool"}
    for m in MODEL_MAP
]

ctx = ssl.create_default_context()

# Per-model chat cache: {service_id: chat_id}
_chat_cache = {}
_chat_cache_lock = threading.Lock()


def _make_headers(ssid=None):
    ssid = ssid or get_ssid()
    return {
        "Cookie": f"__Secure-unitool-ssid={ssid}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/136",
        "Origin": "https://unitool.ai",
        "Referer": "https://unitool.ai/en/chatgpt",
        "Accept": "application/json",
    }


def _api(method, path, body=None, ssid=None):
    """Make an API call to unitool.ai"""
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, headers=_make_headers(ssid), method=method)
    with urlopen(req, context=ctx, timeout=30) as resp:
        return json.loads(resp.read())


def _get_or_create_chat(service_id, ssid=None):
    """Get or create a chat for the given service_id (model name)."""
    with _chat_cache_lock:
        if service_id in _chat_cache:
            return _chat_cache[service_id]
    
    # Try to get existing chat
    try:
        chat = _api("GET", f"/api/services/{service_id}/chat", ssid=ssid)
        chat_id = chat.get("id")
        if chat_id:
            with _chat_cache_lock:
                _chat_cache[service_id] = chat_id
            return chat_id
    except Exception:
        pass
    
    # Create new chat
    chat = _api("POST", "/api/provider-runtime/chats",
                body={"service_id": service_id, "title": ""}, ssid=ssid)
    chat_id = chat.get("id")
    if chat_id:
        with _chat_cache_lock:
            _chat_cache[service_id] = chat_id
    return chat_id


def _format_messages(messages):
    """Format OpenAI messages into a single prompt string for unitool."""
    # Build conversation context
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            # Handle multi-modal content
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
            )
        if role == "system":
            parts.append(f"[System: {content}]")
        elif role == "user":
            parts.append(content)
        elif role == "assistant":
            parts.append(f"[Assistant: {content}]")
    return "\n\n".join(parts)


def _send_and_wait(chat_id, content, ssid=None, timeout=60):
    """Send a message and poll for the assistant response."""
    # Send the message
    result = _api("POST", f"/api/chats/{chat_id}/messages",
                  body={"content": content, "attachments": [], "options": ""},
                  ssid=ssid)
    
    user_msg_id = result.get("message", {}).get("id")
    job_id = result.get("job", {}).get("id")
    
    if not user_msg_id:
        raise Exception(f"No message ID in response: {result}")
    
    # Poll for the assistant response
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(0.5)
        msgs_resp = _api("GET", f"/api/chats/{chat_id}/messages", ssid=ssid)
        msgs = msgs_resp.get("messages", msgs_resp) if isinstance(msgs_resp, dict) else msgs_resp
        
        # Look for assistant reply to our message
        for m in reversed(msgs):
            if (m.get("role") == "assistant" and
                    m.get("reply_to") == user_msg_id and
                    m.get("status") in ("ended", "active", "done")):
                return m.get("content", "")
        
        # Check job status if available
        if job_id:
            try:
                job = _api("GET", f"/api/chats/{chat_id}/jobs/{job_id}", ssid=ssid)
                if job.get("status") in ("failed", "error"):
                    raise Exception(f"Job failed: {job}")
            except Exception:
                pass
    
    raise Exception(f"Timeout waiting for response (chat={chat_id}, msg={user_msg_id})")


def make_chunk(model, content, finish=None):
    delta = {"content": content} if content and not finish else {}
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
        path = self.path.split("?")[0]
        if path in ("/v1/models", "/v1/models/"):
            body = json.dumps({"object": "list", "data": MODELS_LIST}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors()
            self.end_headers()
            self.wfile.write(body)
        elif path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path not in ("/v1/chat/completions", "/v1/chat/completions/"):
            self.send_response(404)
            self.end_headers()
            return
        
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            self._error(400, str(e))
            return

        model = body.get("model", "gpt-4o-mini")
        messages = body.get("messages", [])
        do_stream = body.get("stream", False)
        ssid = get_ssid()

        service_id = MODEL_MAP.get(model, model)
        content = _format_messages(messages)

        print(f"[REQ] model={model} → service={service_id} stream={do_stream} msgs={len(messages)}", flush=True)

        try:
            chat_id = _get_or_create_chat(service_id, ssid=ssid)
            if not chat_id:
                self._error(502, f"Could not get/create chat for {service_id}")
                return

            response_text = _send_and_wait(chat_id, content, ssid=ssid)

            if do_stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_cors()
                self.end_headers()
                # Stream word by word
                words = response_text.split(" ")
                for i, word in enumerate(words):
                    chunk_text = word if i == 0 else " " + word
                    chunk = make_chunk(model, chunk_text)
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                stop_chunk = make_chunk(model, "", finish_reason="stop")
                self.wfile.write(f"data: {json.dumps(stop_chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                resp_body = json.dumps({
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": response_text},
                        "finish_reason": "stop"
                    }],
                    "usage": {"prompt_tokens": len(content.split()), "completion_tokens": len(response_text.split()), "total_tokens": 0},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_cors()
                self.end_headers()
                self.wfile.write(resp_body)

        except BrokenPipeError:
            pass
        except Exception as e:
            print(f"[ERR] {e}", flush=True)
            self._error(500, str(e))

    def _error(self, code, msg):
        try:
            body = json.dumps({"error": {"message": msg, "type": "proxy_error"}}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_cors()
            self.end_headers()
            self.wfile.write(body)
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
    print(f"[unitool-proxy v2] listening on :{PORT}", flush=True)
    print(f"[unitool-proxy v2] models: {len(MODEL_MAP)}", flush=True)
    server.serve_forever()
