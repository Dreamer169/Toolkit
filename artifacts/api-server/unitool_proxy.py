#!/usr/bin/env python3
"""
unitool.ai → OpenAI 兼容反代 v3.0
改进: 坏chat自动驱逐、多ssid轮换、流式token逐字输出
"""
import json, time, uuid, threading, ssl, os, sys, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

PORT = int(os.environ.get("PORT", 8089))
BASE = "https://unitool.ai"
SSID_FILE = "/tmp/unitool_ssid.txt"
SSID_FILE2 = "/tmp/unitool_ssid2.txt"

def get_ssid():
    """读取最新 ssid，优先 ssid2（新登录账号），回退 ssid，再回退内置"""
    for f in [SSID_FILE2, SSID_FILE]:
        if os.path.exists(f):
            v = open(f).read().strip()
            if len(v) > 50:
                return v
    env = os.environ.get("UNITOOL_SSID", "")
    if env: return env
    return "ccc7e893a79df9eb6111789d3c73b7acb317a3b21b6ad84622ee9c092c15ec92ed9d1289cfc04e507844d2f9711b81aabe384330242bd2a7c0a731aea7bccbac8623c7dc307f4df4ac4777605e96638ea08a672371f0d53b5e1fda31e1514e76db8c9118ef73b512c4f0f73d45d4b20ca068d6f3dceea676a4f706e48502586c298f14c7"

MODEL_MAP = {
    "gpt-4o": "gpt-4o", "gpt-4o-mini": "gpt-4o-mini", "gpt-5": "gpt-5",
    "gpt-4": "gpt-4o", "gpt-4-turbo": "gpt-4o", "gpt-4-turbo-preview": "gpt-4o",
    "gpt-3.5-turbo": "gpt-4o-mini",
    "o1": "gpt-4o", "o1-mini": "gpt-4o-mini", "o1-preview": "gpt-4o",
    "o3": "gpt-5", "o3-mini": "gpt-4o-mini", "o4-mini": "gpt-4o-mini",
    "claude-sonnet": "claude-sonnet", "claude-sonnet-4-5": "claude-sonnet-4-5",
    "claude-3-5-sonnet-20241022": "claude-sonnet",
    "claude-3-7-sonnet-20250219": "claude-sonnet-4-5",
    "claude-opus-4-5": "claude-sonnet-4-5",
    "claude-3-opus-20240229": "claude-sonnet",
    "claude-3-sonnet-20240229": "claude-sonnet",
    "claude-3-5-haiku-20241022": "claude-sonnet",
    "claude-3-haiku-20240307": "claude-sonnet",
    "gemini-2.0-flash": "gpt-4o", "gemini-2.5-pro": "gpt-5",
    "gemini-1.5-pro": "gpt-4o", "gemini-1.5-flash": "gpt-4o-mini",
    "gemini-2.0-flash-lite": "gpt-4o-mini",
    "grok-3": "gpt-5", "grok-3-mini": "gpt-4o-mini",
    "grok-2": "gpt-4o", "grok-beta": "gpt-4o",
}

MODELS_LIST = [{"id": m, "object": "model", "created": 1700000000, "owned_by": "unitool"} for m in MODEL_MAP]

ctx = ssl.create_default_context()
_chat_cache = {}      # service_id → chat_id
_bad_chats  = set()   # evicted chat_ids
_lock = threading.Lock()


def _hdrs(ssid=None):
    return {
        "Cookie": f"__Secure-unitool-ssid={ssid or get_ssid()}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/136",
        "Origin": "https://unitool.ai",
        "Referer": "https://unitool.ai/en/chatgpt",
        "Accept": "application/json",
    }


def _api(method, path, body=None, ssid=None):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(f"{BASE}{path}", data=data, headers=_hdrs(ssid), method=method)
    with urlopen(req, context=ctx, timeout=30) as r:
        return json.loads(r.read())


def _get_or_create_chat(service_id, ssid=None, force_new=False):
    with _lock:
        cid = _chat_cache.get(service_id)
        if cid and cid in _bad_chats:
            del _chat_cache[service_id]
            cid = None
        if cid and not force_new:
            return cid
    chat = _api("POST", "/api/provider-runtime/chats", body={"service_id": service_id, "title": ""}, ssid=ssid)
    cid = chat.get("id")
    if cid:
        with _lock:
            _chat_cache[service_id] = cid
    return cid


def _evict(chat_id):
    with _lock:
        _bad_chats.add(chat_id)
        for k, v in list(_chat_cache.items()):
            if v == chat_id:
                del _chat_cache[k]
    print(f"[EVICT] chat {chat_id} evicted", flush=True)


def _fmt(messages):
    parts = []
    for m in messages:
        role, content = m.get("role","user"), m.get("content","")
        if isinstance(content, list):
            content = " ".join(c.get("text","") for c in content if isinstance(c,dict) and c.get("type")=="text")
        if role == "system":    parts.append(f"[System: {content}]")
        elif role == "user":    parts.append(content)
        elif role == "assistant": parts.append(f"[Assistant: {content}]")
    return "\n\n".join(parts)


def _send_wait(chat_id, content, ssid=None, timeout=90):
    result = _api("POST", f"/api/chats/{chat_id}/messages",
                  body={"content": content, "attachments": [], "options": ""}, ssid=ssid)
    user_msg_id = result.get("message", {}).get("id")
    if not user_msg_id:
        raise Exception(f"No message ID: {result}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        msgs_resp = _api("GET", f"/api/chats/{chat_id}/messages", ssid=ssid)
        msgs = msgs_resp.get("messages", msgs_resp) if isinstance(msgs_resp, dict) else msgs_resp
        for m in reversed(msgs):
            if (m.get("role") == "assistant" and m.get("reply_to") == user_msg_id
                    and m.get("status") in ("ended","active","done")):
                return m.get("content","")
    _evict(chat_id)
    raise Exception(f"Timeout chat={chat_id} msg={user_msg_id}")


def _do_chat(model, messages, stream, ssid=None):
    service_id = MODEL_MAP.get(model, model)
    content = _fmt(messages)
    print(f"[REQ] model={model}→{service_id} stream={stream} msgs={len(messages)}", flush=True)

    for attempt in range(2):
        force_new = attempt > 0
        chat_id = _get_or_create_chat(service_id, ssid=ssid, force_new=force_new)
        if not chat_id:
            raise Exception(f"Cannot create chat for {service_id}")
        try:
            return _send_wait(chat_id, content, ssid=ssid)
        except Exception as e:
            if "Timeout" in str(e) and attempt == 0:
                print(f"[RETRY] chat {chat_id} timed out, retrying with new chat", flush=True)
                continue
            raise


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): print(f"[{self.address_string()}] {fmt%args}", flush=True)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/v1/models", "/v1/models/"):
            b = json.dumps({"object":"list","data":MODELS_LIST}).encode()
            self.send_response(200); self.send_header("Content-Type","application/json"); self._cors(); self.end_headers(); self.wfile.write(b)
        elif p == "/healthz":
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        elif p == "/ssid-status":
            ssid = get_ssid()
            b = json.dumps({"ssid_prefix": ssid[:40]+"...", "ssid_len": len(ssid), "chats": len(_chat_cache)}).encode()
            self.send_response(200); self.send_header("Content-Type","application/json"); self._cors(); self.end_headers(); self.wfile.write(b)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        p = self.path.split("?")[0]
        if p not in ("/v1/chat/completions", "/v1/chat/completions/"):
            self.send_response(404); self.end_headers(); return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            return self._err(400, str(e))

        model = body.get("model","gpt-4o-mini")
        messages = body.get("messages",[])
        do_stream = body.get("stream", False)

        # Extract auth header as optional ssid override
        auth = self.headers.get("Authorization","")
        ssid = None
        if auth.startswith("Bearer ") and len(auth) > 60:
            ssid = auth[7:]

        try:
            text = _do_chat(model, messages, do_stream, ssid=ssid)
            if do_stream:
                self.send_response(200)
                self.send_header("Content-Type","text/event-stream")
                self.send_header("Cache-Control","no-cache")
                self._cors(); self.end_headers()
                words = text.split(" ")
                for i, w in enumerate(words):
                    chunk = {"id":f"chatcmpl-{uuid.uuid4().hex[:12]}","object":"chat.completion.chunk",
                             "created":int(time.time()),"model":model,
                             "choices":[{"index":0,"delta":{"content":w if i==0 else " "+w},"finish_reason":None}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode()); self.wfile.flush()
                stop = {"id":f"chatcmpl-{uuid.uuid4().hex[:12]}","object":"chat.completion.chunk",
                        "created":int(time.time()),"model":model,
                        "choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
                self.wfile.write(f"data: {json.dumps(stop)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
            else:
                resp = json.dumps({"id":f"chatcmpl-{uuid.uuid4().hex[:12]}","object":"chat.completion",
                    "created":int(time.time()),"model":model,
                    "choices":[{"index":0,"message":{"role":"assistant","content":text},"finish_reason":"stop"}],
                    "usage":{"prompt_tokens":len((" ".join(m.get("content","") for m in messages)).split()),
                             "completion_tokens":len(text.split()),"total_tokens":0}}).encode()
                self.send_response(200); self.send_header("Content-Type","application/json")
                self._cors(); self.end_headers(); self.wfile.write(resp)
        except BrokenPipeError: pass
        except Exception as e:
            print(f"[ERR] {e}", flush=True); self._err(500, str(e))

    def _err(self, code, msg):
        try:
            b = json.dumps({"error":{"message":msg,"type":"proxy_error"}}).encode()
            self.send_response(code); self.send_header("Content-Type","application/json")
            self._cors(); self.end_headers(); self.wfile.write(b)
        except: pass


class ThreadedHTTPServer(HTTPServer):
    def process_request(self, req, addr):
        t = threading.Thread(target=self.finish_request, args=(req, addr)); t.daemon=True; t.start()


if __name__ == "__main__":
    server = ThreadedHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[unitool-proxy v3] port={PORT} models={len(MODEL_MAP)}", flush=True)
    server.serve_forever()
