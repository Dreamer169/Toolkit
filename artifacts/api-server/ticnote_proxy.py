#!/usr/bin/env python3
"""
ticnote_proxy.py — v1.4
OpenAI-compatible proxy for ticnote.com.

━━━ 逆向协议 (playwright capture 2026-05-24) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WS connect: wss://prd-chat-socket-api.ticnote.com/socket.io/?token={JWT}&EIO=4&transport=websocket
  TX namespace: 40/business,{"userId":"{userId}"}
  TX join:      42/business,["join",{"room":"{chatId}","currentUserInfo":{...}}]
  TX message:   42/business,["chat_message",{...}]
  RX stream:    42/business,["streaming_message",{...,"type":"streaming_chunk","data":{"chunk":"..."},...}]
  RX ack:       42/business,["chat_ack",{...}]

━━━ 系统提示词分析 (最终结论 2026-05-24) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  根因：ticnote 后端对「所有 WS 会话」均注入 Ticnote persona 系统提示
        注入在 org/账号级别，与 virtual_employee、agent_type、rolePrompt 无关

  已确认无效的绕过手段：
    ❌ 任意 agent_type（assistant/chat_agent/coding_agent/chatter/raw/direct/null）
    ❌ rolePrompt 覆盖（包括强指令、XML 逃逸注入）
    ❌ 任意 execution_mode（general/code/...）
    ❌ knowledge/workflow/memory JSON 字段修改
    ❌ 删除 virtual_employee_id 或使用不存在的 VIRE ID
    ❌ 删除 agent_engine 字段
    ❌ 第三方 LLM 配置（/api/v1/third-party-config 正确参数未知）

  rolePrompt 可用于追加上下文：
    ✅ rolePrompt IS 传给模型（模型能看到内容）
    ✅ 任务指令（风格/格式/语言要求）通过 rolePrompt 有效
    ❌ 身份覆盖无效（base 系统提示优先且含反 jailbreak 指令）

  最优配置（v1.2 验证）：
    chat_agent + general → 任务执行最宽松，无附加限制

  VIRE（Virtual Employee）资源：
    CleanProxy  chat=72db315c vire=vire-cleanproxy-1779572399145-tgut70wx
    RawModel    chat=1f1d4ad3 vire=vire-rawmodel-1779572399633-4un6zfkf
    CodeProxy   chat=c072f30b vire=vire-codeproxy-1779572579847-690vs2ip
    ChatterProxy chat=afb3c8b1 vire=vire-chatterproxy-1779581671300-xl438afq
                              (agentType=chatter → 无 AI 响应，可能是人工路由)

  未解谜团：
    - /api/v1/third-party-config POST 正确参数（JS bundle 中未找到，后端专有功能）
    - 是否有 org-admin 级别的系统提示控制（当前账号非 admin）

━━━ JWT 刷新 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  当前 JWT 过期时间: ~2026-06-22
  账号: william_alvArez95@outlook.com (见账号 DB)
  刷新: playwright 登录 ticnote.com → localStorage.token
"""
import asyncio, json, random, ssl, string, threading, time, uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
import socks as _socks
import websockets.asyncio.client as _wsc

PORT     = 8090
API_KEY  = "sk-ticnote"

# ─── Account ──────────────────────────────────────────────────────────────────
JWT      = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjbGllbnRfaW5mbyI6IndlYiIsInVzZXJfaWQiOiIyMDU4MjUzMjY3Njg5ODgxNjAxIiwiZXhwaXJlX3RpbWUiOjE3ODIxNzYyMjYwMzAsImFjY291bnQiOiJ3aWxsaWFtX2FsdkFyZXo5NUBvdXRsb29rLmNvbSIsImlhdCI6MTc3OTU4NDIzMCwiZXhwIjoxNzgyMTc2MjI2LCJzdWIiOiIyMDU4MjUzMjY3Njg5ODgxNjAxIiwidXNlcm5hbWUiOiJ3aWxsaWFtX2FsdkFyZXo5NUBvdXRsb29rLmNvbSIsIm9yZ2FuaXphdGlvbklkIjoiMjA1ODI1MzI2NzY4OTg4MTYwMSIsIm5hbWUiOiJ3aWxsaWFtX2FsdkFyZXo5NSIsImlzT3JnQWRtaW4iOmZhbHNlLCJlbWFpbCI6IndpbGxpYW1fYWx2QXJlejk1QG91dGxvb2suY29tIn0.ib3lgjA4VCRRY4JqGOwVnWgwjc8uQc4PjLGaAO2iX54"
USER_ID  = "2058253267689881601"
ORG_ID   = "2058253267689881601"
PROJ_ID  = "2058253267815710721"

# Primary chat (original, non-VIRE chat room — chat_agent+general)
PRIMARY_CHAT_ID  = "2058253267815710722"
PRIMARY_AGENT_ID = "vire-ignoreprompt-1779583708787-d6h7vey6"
PRIMARY_AGENT_NAME = "AssistantVire"

# VIRE chat rooms (fallback option; tested, also show Ticnote persona but stable)
VIRE_CLEANPROXY_CHAT  = "72db315c-961e-48e7-ae18-684d9ab5aaa0"
VIRE_CLEANPROXY_ID    = "vire-cleanproxy-1779572399145-tgut70wx"
VIRE_RAWMODEL_CHAT    = "1f1d4ad3-a8b3-4cb6-b2c4-c6de82271edd"
VIRE_RAWMODEL_ID      = "vire-rawmodel-1779572399633-4un6zfkf"
VIRE_CODEPROXY_CHAT   = "c072f30b-b516-4fdc-94cb-86d46c6eedeb"
VIRE_CODEPROXY_ID     = "vire-codeproxy-1779572579847-690vs2ip"

# Active config (chat_agent+general is the best combo found via exhaustive testing)
CHAT_ID    = PRIMARY_CHAT_ID
AGENT_ID   = PRIMARY_AGENT_ID
AGENT_NAME = PRIMARY_AGENT_NAME
AGENT_TYPE     = "chat_agent"
EXECUTION_MODE = "general"

WS_HOST  = "prd-chat-socket-api.ticnote.com"
WS_URL   = f"wss://{WS_HOST}/socket.io/?token={{jwt}}&EIO=4&transport=websocket"
RESI_PORTS = [10853, 10859]

# ─── Model mapping ────────────────────────────────────────────────────────────
MODEL_MAP = {
    "gpt-4.1": "gpt-4.1", "gpt-4": "gpt-4.1", "gpt-4o": "gpt-4.1",
    "gpt-4-turbo": "gpt-4.1",
    "gpt-5": "gpt-5", "gpt-5.4": "gpt-5.4", "gpt-5.4-mini": "gpt-5.4-mini",
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-sonnet-4": "claude-sonnet-4-6",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-sonnet-4-5": "claude-sonnet-4-5",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-6",
    "claude-opus": "claude-opus-4-6",
    "claude-opus-4": "claude-opus-4-6",
    "claude-opus-4-6": "claude-opus-4-6",
    "claude-opus-4-7": "claude-opus-4-7",
    "claude-opus-4-5": "claude-opus-4-5",
    "claude-3-opus": "claude-opus-4-6",
}
DEFAULT_MODEL = "claude-sonnet-4-6"
ALL_MODELS = sorted({
    "gpt-4.1", "gpt-5", "gpt-5.4", "gpt-5.4-mini",
    "claude-sonnet-4-5", "claude-sonnet-4-6",
    "claude-opus-4-5", "claude-opus-4-6", "claude-opus-4-7",
})

def _resolve(model: str) -> str:
    return MODEL_MAP.get(model, DEFAULT_MODEL)

def _make_msg_id() -> str:
    ts   = int(time.time() * 1000)
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"msg_{ts}_{rand}"

def _messages_to_text(messages: list) -> str:
    """Convert OpenAI messages to ticnote single-text input."""
    parts, sys_parts = [], []
    for m in messages:
        role    = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        if role == "system":
            sys_parts.append(f"[System: {content}]")
        elif role == "user":
            parts.append(f"User: {content}")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
    return "\n\n".join(sys_parts + parts) or "Hello"

# ─── SOCKS5 ───────────────────────────────────────────────────────────────────
_ssl_ctx  = ssl.create_default_context()
_resi_idx = 0
_resi_lock = threading.Lock()

def _pick_resi() -> int:
    global _resi_idx
    with _resi_lock:
        port = RESI_PORTS[_resi_idx % len(RESI_PORTS)]
        _resi_idx += 1
    return port

def _make_socks5_sock(port: int, timeout: int = 15):
    raw = _socks.create_connection(
        (WS_HOST, 443),
        proxy_type=_socks.SOCKS5,
        proxy_addr="127.0.0.1",
        proxy_port=port,
        timeout=timeout,
    )
    raw.setblocking(False)
    return raw

# ─── WS chat ──────────────────────────────────────────────────────────────────
async def _ws_chat(content: str, model: str, chunk_cb=None, timeout: int = 120) -> str:
    tic_model = _resolve(model)
    msg_id    = _make_msg_id()
    ts        = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    payload = ["chat_message", {
        "room": CHAT_ID, "org_id": ORG_ID,
        "user_id": USER_ID, "human_user_id": USER_ID,
        "chat_id": CHAT_ID, "session_id": msg_id,
        "message_type": "user_request",
        "message_id": msg_id, "timestamp": ts,
        "chat_owner_id": USER_ID, "reply_to_message_id": None,
        "data": {
            "context_info": {
                "chat_type": "private", "urgency": "normal", "sender": USER_ID,
                "db_config": {
                    "coding_project_id": PROJ_ID,
                    "virtual_employee_id": AGENT_ID,
                    "virtual_employee_name": AGENT_NAME,
                    "chat_type": 1,
                },
                "agent_config": {
                    "execution_mode": EXECUTION_MODE,
                    "preferred_llm_model": tic_model,
                    "project_type": 0,
                    "timezone": "UTC",
                    "enable_search": False,
                    "enable_thinking": False,
                },
            },
            "content": {"text": content, "userContext": "", "attachments": []},
        },
        "chat_type": 1, "agent_engine": ORG_ID,
        "agent_type": AGENT_TYPE, "agent_id": AGENT_ID,
    }]

    resi_port = _pick_resi()
    raw_sock  = _make_socks5_sock(resi_port)
    url       = WS_URL.format(jwt=JWT)
    chunks    = []
    deadline  = time.time() + timeout

    async with _wsc.connect(
        url, sock=raw_sock, ssl=_ssl_ctx,
        additional_headers={"Origin": "https://ticnote.com"},
        ping_interval=None, open_timeout=20, max_size=10_000_000,
    ) as ws:
        print(f"[ticnote] connected resi={resi_port} model={tic_model}", flush=True)
        await asyncio.wait_for(ws.recv(), timeout=8)
        await ws.send(f'40/business,{{"userId":"{USER_ID}"}}')
        await asyncio.sleep(0.3)
        for _ in range(4):
            try:
                frm = await asyncio.wait_for(ws.recv(), timeout=2)
                if frm == "2": await ws.send("3")
            except asyncio.TimeoutError: break
        join = json.dumps(["join", {"room": CHAT_ID,
                                     "currentUserInfo": {"userId": USER_ID, "userName": None}}])
        await ws.send(f"42/business,{join}")
        await asyncio.sleep(0.4)
        await ws.send(f"42/business,{json.dumps(payload)}")
        print(f"[ticnote] sent msg_id={msg_id[:30]}", flush=True)

        done = False
        while not done and time.time() < deadline:
            try:
                frm = await asyncio.wait_for(ws.recv(), timeout=8)
            except asyncio.TimeoutError:
                if chunks: break
                continue
            except Exception: break
            if frm == "2": await ws.send("3"); continue
            if not frm.startswith("42/business,"): continue
            try: arr = json.loads(frm[len("42/business,"):])
            except: continue
            if not isinstance(arr, list) or not arr: continue
            evt  = arr[0]; data = arr[1] if len(arr) > 1 else {}
            if evt == "streaming_message" and isinstance(data, dict):
                if (data.get("session_id") != msg_id
                        and data.get("human_user_id") != USER_ID):
                    continue
                md = data.get("data", {}); ct = md.get("type", "")
                if ct == "streaming_chunk":
                    ch = md.get("chunk", "")
                    if ch:
                        chunks.append(ch)
                        if chunk_cb: chunk_cb(ch)
                elif ct in ("streaming_done", "done", "message_complete", "end"):
                    done = True; break

    result = "".join(chunks)
    print(f"[ticnote] done: {len(chunks)} chunks, {len(result)} chars", flush=True)
    return result


def _ws_chat_sync(content: str, model: str, chunk_cb=None, timeout: int = 120) -> str:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_ws_chat(content, model, chunk_cb, timeout))
    finally:
        loop.close()


# ─── HTTP Handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[ticnote-proxy] {fmt % args}", flush=True)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors(); self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models",):
            self._json(200, {"object": "list",
                              "data": [{"id": m, "object": "model",
                                        "created": 1700000000, "owned_by": "ticnote"}
                                       for m in ALL_MODELS]})
        elif self.path in ("/health", "/healthz"):
            self._json(200, {"status": "ok", "service": "ticnote-proxy",
                              "version": "1.4",
                              "agent_type": AGENT_TYPE,
                              "execution_mode": EXECUTION_MODE,
                              "chat_id": CHAT_ID})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") not in ("/v1/chat/completions",):
            self.send_response(404); self.end_headers(); return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
        except Exception as e:
            return self._json(400, {"error": {"message": str(e), "type": "parse_error"}})

        model     = body.get("model", DEFAULT_MODEL)
        messages  = body.get("messages", [])
        do_stream = body.get("stream", False)
        content   = _messages_to_text(messages)
        resp_id   = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        ts        = int(time.time())

        if do_stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self._cors(); self.end_headers()

            def send_chunk(delta: str):
                pkt = {"id": resp_id, "object": "chat.completion.chunk",
                       "created": ts, "model": model,
                       "choices": [{"index": 0, "delta": {"content": delta},
                                    "finish_reason": None}]}
                try:
                    self.wfile.write(f"data: {json.dumps(pkt, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError): pass

            try:
                _ws_chat_sync(content, model, chunk_cb=send_chunk)
            except Exception as e:
                print(f"[ticnote ERR] {e}", flush=True)
                err_pkt = {"id": resp_id, "object": "chat.completion.chunk",
                           "created": ts, "model": model,
                           "choices": [{"index": 0, "delta": {"content": f"[Error: {e}]"},
                                        "finish_reason": "stop"}]}
                try:
                    self.wfile.write(f"data: {json.dumps(err_pkt)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
                except Exception: pass
                return

            stop = {"id": resp_id, "object": "chat.completion.chunk",
                    "created": ts, "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            try:
                self.wfile.write(f"data: {json.dumps(stop)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
            except Exception: pass
        else:
            try:
                text = _ws_chat_sync(content, model)
            except Exception as e:
                print(f"[ticnote ERR] {e}", flush=True)
                return self._json(500, {"error": {"message": str(e), "type": "proxy_error"}})
            pt = len(" ".join(m.get("content","") for m in messages
                               if isinstance(m.get("content"), str)).split())
            ct = len(text.split())
            self._json(200, {
                "id": resp_id, "object": "chat.completion",
                "created": ts, "model": model,
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                          "total_tokens": pt + ct},
            })


class ThreadedServer(HTTPServer):
    allow_reuse_address = True
    def process_request(self, req, addr):
        threading.Thread(target=self.finish_request, args=(req, addr), daemon=True).start()


if __name__ == "__main__":
    import signal, sys
    print(f"[ticnote-proxy v1.4] port={PORT} resi={RESI_PORTS}", flush=True)
    print(f"[ticnote-proxy] agent={AGENT_TYPE} mode={EXECUTION_MODE}", flush=True)
    print(f"[ticnote-proxy] models: {sorted(ALL_MODELS)}", flush=True)
    print(f"[ticnote-proxy] chat_id={CHAT_ID}", flush=True)
    print(f"[ticnote-proxy] NOTE: Ticnote platform-level system prompt always injected", flush=True)

    def _exit(sig, _):
        print("[ticnote-proxy] exit", flush=True); sys.exit(0)
    signal.signal(signal.SIGTERM, _exit)
    signal.signal(signal.SIGINT, _exit)

    server = ThreadedServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
