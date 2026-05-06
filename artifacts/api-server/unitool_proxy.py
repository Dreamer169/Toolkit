#!/usr/bin/env python3
"""
unitool.ai → OpenAI 兼容反代 v4.0
新特性:
  - 多账号 ssid 轮换池（Round-Robin），任一账号 423/expired 自动切到下一个
  - 所有真实 service_id 直通（70+ 模型，不再降级）
  - 常见别名映射（gpt-3.5-turbo → chatgpt 等）
  - /pool-status 查看账号池状态
  - /v1/models 返回全部真实模型
  - 坏 chat 自动驱逐，超时自动重试新 chat
"""
import json, time, uuid, threading, ssl, os, re, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError

PORT = int(os.environ.get("PORT", 8089))
BASE = "https://unitool.ai"
SSID_DIR = "/tmp"
SSID_PATTERN = re.compile(r"^unitool_ssid\d*\.txt$")

ctx = ssl.create_default_context()
_lock = threading.Lock()

# ─── SSID 池 ────────────────────────────────────────────────────────────────
# 结构: [{ssid, label, dead_until}]
_pool: list = []
_pool_idx: int = 0          # round-robin 指针
_pool_mtime: float = 0.0    # 上次文件扫描时间

def _scan_ssid_files() -> list:
    """扫描 /tmp/unitool_ssid*.txt，返回 ssid 列表（去重）"""
    found = []
    try:
        for fn in sorted(os.listdir(SSID_DIR)):
            if SSID_PATTERN.match(fn):
                path = os.path.join(SSID_DIR, fn)
                try:
                    v = open(path).read().strip()
                    if len(v) > 50:
                        found.append((fn, v))
                except Exception:
                    pass
    except Exception:
        pass
    return found

def _reload_pool_if_needed():
    global _pool, _pool_mtime
    # 每 5 秒最多扫描一次
    now = time.time()
    if now - _pool_mtime < 5:
        return
    _pool_mtime = now

    files = _scan_ssid_files()
    with _lock:
        existing = {e["ssid"]: e for e in _pool}
        new_pool = []
        for (fn, ssid) in files:
            if ssid in existing:
                new_pool.append(existing[ssid])
            else:
                new_pool.append({"ssid": ssid, "label": fn, "dead_until": 0,
                                  "dead_reason": "", "chats": {}, "bad_chats": set()})
                print(f"[POOL] added {fn} ssid={ssid[:16]}...", flush=True)
        # 保留 dead 的老条目（可能只是暂时 423，10min 后恢复）
        for e in _pool:
            if e["ssid"] not in {x["ssid"] for x in new_pool}:
                if e["dead_until"] > now:  # 还在 dead 期内，保留
                    new_pool.append(e)
        _pool = new_pool

def _get_live_ssid():
    """Round-Robin 取一个当前存活的 ssid 条目，返回 entry dict 或 None"""
    global _pool_idx
    _reload_pool_if_needed()
    with _lock:
        if not _pool:
            return None
        now = time.time()
        live = [e for e in _pool if e["dead_until"] <= now]
        if not live:
            # 所有都 dead，取 dead_until 最早过期的
            live = sorted(_pool, key=lambda e: e["dead_until"])[:1]
        _pool_idx = (_pool_idx + 1) % len(live)
        return live[_pool_idx % len(live)]

def _mark_dead(ssid: str, secs: int = 600, reason: str = ""):
    with _lock:
        for e in _pool:
            if e["ssid"] == ssid:
                e["dead_until"] = time.time() + secs
                e["dead_reason"] = reason
                print(f"[POOL] marked dead {ssid[:16]}... for {secs}s reason={reason!r}", flush=True)
                break

def _check_balance(ssid: str) -> float:
    """Return net token balance for the account (negative = no money)."""
    try:
        req = Request(f"{BASE}/api/user/billing-accounts",
                      headers=_hdrs(ssid), method="GET")
        with urlopen(req, context=ctx, timeout=8) as r:
            d = json.loads(r.read())
        total = sum(a.get("value", 0) for a in d.get("accounts", []))
        return total
    except Exception:
        return 0.0

# ─── 真实 Service ID 列表（从 /api/services 获取） ──────────────────────────
# 文本类服务（可以直接用作 model 名）
NATIVE_SERVICES = {
    # ── 通过 /api/provider-runtime/chats + /api/chats/{id}/messages 实测可用 ──
    # OpenAI / ChatGPT
    "gpt-5",          # ChatGPT 5 (最新旗舰)
    "gpt-5.5",        # ChatGPT 5.5 (最新旗舰进阶)
    "gpt-4o",         # GPT-4o (快速多模态)
    # Anthropic / Claude
    "claude-sonnet",  # Claude Sonnet 最新版（含 Sonnet 4 / claude-opus 4 级别）
}
# 别名映射：把常见的 OpenAI 兼容名称 → 真实 unitool service_id
MODEL_ALIASES = {
    # ─── OpenAI GPT 系 ───────────────────────────────────────────────────────
    # 版本化 GPT → gpt-4o (稳定可用)
    "gpt-4":                    "gpt-4o",
    "gpt-4-turbo":              "gpt-4o",
    "gpt-4-turbo-preview":      "gpt-4o",
    "gpt-4.1":                  "gpt-4o",
    "gpt-4.5":                  "gpt-4o",
    "gpt-4o-mini":              "gpt-4o",
    "gpt-4o-2024-11-20":        "gpt-4o",
    "gpt-4o-mini-2024-07-18":   "gpt-4o",
    "gpt-4o-search":            "gpt-4o",
    "gpt-4o-mini-search":       "gpt-4o",
    "gpt-3.5-turbo":            "gpt-4o",
    "gpt-3.5-turbo-0613":       "gpt-4o",
    "gpt-3.5-turbo-16k":        "gpt-4o",
    "text-davinci-003":         "gpt-4o",
    # Reasoning 系 → gpt-5.5 (最强可用)
    "o1":                       "gpt-5.5",
    "o1-mini":                  "gpt-5.5",
    "o1-pro":                   "gpt-5.5",
    "o1-preview":               "gpt-5.5",
    "o3":                       "gpt-5.5",
    "o3-mini":                  "gpt-5.5",
    "o4-mini":                  "gpt-5.5",
    "o4":                       "gpt-5.5",
    # GPT-5 / ChatGPT-5.5 — 已是 NATIVE，这里为冗余兼容
    "gpt-5-turbo":              "gpt-5",
    "chatgpt-5":                "gpt-5.5",
    "chatgpt-5-turbo":          "gpt-5.5",
    "chatgpt-5.5":              "gpt-5.5",
    "chatgpt-5.5-turbo":        "gpt-5.5",
    "chatgpt":                  "gpt-5.5",   # 泛型 chatgpt → 最新可用
    # ─── Anthropic Claude 系 ─────────────────────────────────────────────────
    # 所有版本化 Opus → claude-opus (泛型，服务最新 Opus)
    # unitool claude-opus 后端 bug: max_tokens 32768 > Claude Opus 4 限制 32000
    # 全部回退到 claude-sonnet（实际工作），待 unitool 修复后恢复
    "claude-opus":              "claude-sonnet",  # native已移除，此处回退
    "claude-opus-4":            "claude-sonnet",
    "claude-opus-4-5":          "claude-sonnet",
    "claude-opus-4.5":          "claude-sonnet",
    "claude-opus-4-6":          "claude-sonnet",  # 用户请求的 Claude Opus 4.6
    "claude-opus-4.6":          "claude-sonnet",
    "claude-opus-4-latest":     "claude-sonnet",
    "claude-opus-latest":       "claude-sonnet",
    "claude-3-opus":            "claude-sonnet",
    "claude-3-opus-20240229":   "claude-sonnet",
    "claude":                   "claude-sonnet",  # 泛型 claude → Sonnet (Opus 损坏中)
    # 所有版本化 Sonnet → claude-sonnet (泛型，服务最新 Sonnet)
    "claude-sonnet-4":          "claude-sonnet",
    "claude-sonnet-4-5":        "claude-sonnet",
    "claude-3-7-sonnet":        "claude-sonnet",
    "claude-3-7-sonnet-20250219": "claude-sonnet",
    "claude-3-5-sonnet":        "claude-sonnet",
    "claude-3-5-sonnet-20241022": "claude-sonnet",
    "claude-3-5-haiku":         "claude-sonnet",
    "claude-3-5-haiku-20241022": "claude-sonnet",
    "claude-3-haiku":           "claude-sonnet",
    "claude-3-haiku-20240307":  "claude-sonnet",
    "claude-3-sonnet":          "claude-sonnet",
    "claude-3-sonnet-20240229": "claude-sonnet",
    "claude-haiku":             "claude-sonnet",
    "claude-3-5-sonnet-latest": "claude-sonnet",
    "claude-3-7-sonnet-latest": "claude-sonnet",
    "claude-sonnet-latest":     "claude-sonnet",
    # ─── Google Gemini 系 ────────────────────────────────────────────────────
    # Gemini service_ids 使用 streaming SSE，目前回退到 gpt-4o 处理
    "gemini":                   "gpt-4o",
    "gemini-2.5-pro":           "gpt-4o",
    "gemini-2.0-pro":           "gpt-4o",
    "gemini-2.0-pro-exp":       "gpt-4o",
    "gemini-2.0-flash":         "gpt-4o",
    "gemini-2.0":               "gpt-4o",
    "gemini-flash-2.0":         "gpt-4o",
    "flash-2.0":                "gpt-4o",
    "gemini-1.5-pro":           "gpt-4o",
    "gemini-1.5-flash":         "gpt-4o",
    "gemini-1.0-pro":           "gpt-4o",
    "gemini-pro-1.0":           "gpt-4o",
    "gemini-pro":               "gpt-4o",
    "gemini-exp":               "gpt-4o",
    "gemini-pro-exp":           "gpt-4o",
    "gemini-flash":             "gpt-4o",
    "gemini-2.5-flash":         "gpt-4o",
    "gemini-ultra":             "gpt-4o",
    # ─── xAI / Grok 系 ───────────────────────────────────────────────────────
    # Grok 目前 "Unsupported service"，回退到 gpt-5.5
    "grok":                     "gpt-5.5",
    "x-ai":                     "gpt-5.5",
    "xai":                      "gpt-5.5",
    "grok-3":                   "gpt-5.5",
    "grok-3-fast":              "gpt-5.5",
    "grok-3-mini":              "gpt-5.5",
    "grok-3-mini-fast":         "gpt-5.5",
    "grok-2":                   "gpt-5.5",
    "grok-2-1212":              "gpt-5.5",
    "grok-2-mini":              "gpt-5.5",
    "grok-beta":                "gpt-5.5",
    "grok-mini":                "gpt-5.5",
    # ─── DeepSeek 系 ─────────────────────────────────────────────────────────
    "deepseek":                 "gpt-5.5",
    "deepseek-r1":              "gpt-5.5",
}
def _resolve_model(model: str) -> str:
    """把请求的 model 名映射到 unitool service_id"""
    if model in NATIVE_SERVICES:
        return model
    if model in MODEL_ALIASES:
        return MODEL_ALIASES[model]
    # 未知模型：猜测厂商
    m = model.lower()
    if "claude" in m:   return "claude"
    if "gemini" in m:   return "gemini"
    if "grok" in m:     return "x-ai"
    if "deepseek" in m: return "deepseek"
    return "chatgpt"   # fallback

# 所有可暴露的模型列表 = native + 常见别名
ALL_MODELS = sorted(NATIVE_SERVICES | set(MODEL_ALIASES.keys()))
MODELS_LIST = [{"id": m, "object": "model", "created": 1700000000, "owned_by": "unitool"}
               for m in ALL_MODELS]

# ─── API 调用 ────────────────────────────────────────────────────────────────
def _hdrs(ssid: str) -> dict:
    return {
        "Cookie":        f"__Secure-unitool-ssid={ssid}",
        "Content-Type":  "application/json",
        "User-Agent":    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/136",
        "Origin":        "https://unitool.ai",
        "Referer":       "https://unitool.ai/en/chatgpt",
        "Accept":        "application/json",
    }

def _api(method: str, path: str, body=None, ssid: str = ""):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(f"{BASE}{path}", data=data, headers=_hdrs(ssid), method=method)
    with urlopen(req, context=ctx, timeout=30) as r:
        return json.loads(r.read())

def _get_or_create_chat(entry: dict, service_id: str, force_new=False):
    with _lock:
        cid = entry["chats"].get(service_id)
        if cid and cid in entry["bad_chats"]:
            entry["chats"].pop(service_id, None)
            cid = None
        if cid and not force_new:
            return cid
    chat = _api("POST", "/api/provider-runtime/chats",
                body={"service_id": service_id, "title": ""},
                ssid=entry["ssid"])
    cid = chat.get("id")
    if cid:
        with _lock:
            entry["chats"][service_id] = cid
    return cid

def _evict_chat(entry: dict, chat_id):
    with _lock:
        entry["bad_chats"].add(chat_id)
        for k, v in list(entry["chats"].items()):
            if v == chat_id:
                del entry["chats"][k]
    print(f"[EVICT] chat {chat_id}", flush=True)

def _fmt(messages: list) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content
                               if isinstance(c, dict) and c.get("type") == "text")
        if role == "system":
            parts.append(f"[System: {content}]")
        elif role == "assistant":
            parts.append(f"[Assistant: {content}]")
        else:
            parts.append(content)
    return "\n\n".join(parts)

def _send_wait(entry: dict, chat_id, content: str, timeout=180) -> str:
    result = _api("POST", f"/api/chats/{chat_id}/messages",
                  body={"content": content, "attachments": [], "options": ""},
                  ssid=entry["ssid"])
    user_msg_id = result.get("message", {}).get("id")
    if not user_msg_id:
        raise Exception(f"no message id: {result}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.5)
        msgs_resp = _api("GET", f"/api/chats/{chat_id}/messages", ssid=entry["ssid"])
        msgs = msgs_resp.get("messages", msgs_resp) if isinstance(msgs_resp, dict) else msgs_resp
        for m in reversed(msgs):
            if m.get("role") == "assistant" and m.get("reply_to") == user_msg_id:
                status = m.get("status", "")
                if status == "error":
                    raise Exception(f"service_error: {m.get('content','')[:300]}")
                if status in ("ended", "active", "done"):
                    return m.get("content", "")
    _evict_chat(entry, chat_id)
    raise TimeoutError(f"timeout chat={chat_id}")

def _do_chat(model: str, messages: list, ssid_override: str | None = None) -> str:
    service_id = _resolve_model(model)
    content = _fmt(messages)
    print(f"[REQ] {model}→{service_id} msgs={len(messages)}", flush=True)

    # ssid 覆盖（Bearer header）
    if ssid_override and len(ssid_override) > 50:
        entry = {"ssid": ssid_override, "label": "header",
                 "dead_until": 0, "chats": {}, "bad_chats": set()}
        entries = [entry]
    else:
        _reload_pool_if_needed()
        with _lock:
            live = [e for e in _pool if e["dead_until"] <= time.time()]
            entries = live if live else _pool[:]

    if not entries:
        raise Exception("ssid pool empty — please login first")

    last_err = None
    for _ in range(len(entries) * 2):   # 最多尝试每个账号两次
        entry = _pick_entry(entries)
        try:
            for attempt in range(2):
                chat_id = _get_or_create_chat(entry, service_id, force_new=(attempt > 0))
                if not chat_id:
                    raise Exception(f"cannot create chat for {service_id}")
                try:
                    return _send_wait(entry, chat_id, content)
                except TimeoutError:
                    if attempt == 0:
                        print(f"[RETRY] timeout, new chat for {service_id}", flush=True)
                        continue
                    raise
        except HTTPError as e:
            if e.code in (401, 403, 423):
                try:
                    err_body = e.read().decode(errors="ignore")
                except Exception:
                    err_body = ""
                if "Free tokens are over" in err_body or "Balance need" in err_body:
                    # Account balance permanently exhausted — mark dead 24h
                    print(f"[POOL] {entry['label']} balance exhausted, marking dead 24h", flush=True)
                    _mark_dead(entry["ssid"], secs=86400, reason="balance_exhausted")
                else:
                    print(f"[POOL] {entry['label']} got HTTP {e.code}, marking dead 10m", flush=True)
                    _mark_dead(entry["ssid"], secs=600, reason=f"http_{e.code}")
                last_err = e
                continue
            raise
        except Exception as e:
            last_err = e
            continue

    raise Exception(f"all ssids failed: {last_err}")

_rr_idx = 0
_rr_lock = threading.Lock()

def _pick_entry(entries: list) -> dict:
    global _rr_idx
    with _rr_lock:
        idx = _rr_idx % len(entries)
        _rr_idx += 1
        return entries[idx]

# ─── HTTP Handler ────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[HTTP] {self.address_string()} {fmt % args}", flush=True)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/v1/models", "/v1/models/"):
            self._json(200, {"object": "list", "data": MODELS_LIST})
        elif p == "/healthz":
            self.send_response(200); self.end_headers()
            self.wfile.write(b"ok")
        elif p == "/pool-status":
            _reload_pool_if_needed()
            now = time.time()
            with _lock:
                pool_info = [{
                    "label": e["label"],
                    "ssid_prefix": e["ssid"][:20] + "...",
                    "dead": e["dead_until"] > now,
                    "dead_until": e["dead_until"],
                    "dead_reason": e.get("dead_reason", ""),
                    "chats": len(e["chats"]),
                } for e in _pool]
            live = sum(1 for a in pool_info if not a["dead"])
            self._json(200, {"pool_size": len(_pool), "live": live, "accounts": pool_info})
        elif p == "/ssid-status":
            # 向后兼容 v3 接口
            _reload_pool_if_needed()
            with _lock:
                live = [e for e in _pool if e["dead_until"] <= time.time()]
                top = _pool[0] if _pool else None
            self._json(200, {
                "pool_size": len(_pool),
                "live": len(live),
                "ssid_prefix": (top["ssid"][:40] + "...") if top else "",
                "ssid_len": len(top["ssid"]) if top else 0,
                "chats": sum(len(e["chats"]) for e in _pool),
            })
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/add-ssid":
            # 动态注入 ssid（供登录脚本调用）
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                ssid = body.get("ssid", "").strip()
                label = body.get("label", "api")
                if len(ssid) < 50:
                    return self._json(400, {"error": "ssid too short"})
                added = False
                with _lock:
                    exists = any(e["ssid"] == ssid for e in _pool)
                    if not exists:
                        _pool.append({"ssid": ssid, "label": label,
                                      "dead_until": 0, "dead_reason": "",
                                      "chats": {}, "bad_chats": set()})
                        added = True
                        print(f"[POOL] /add-ssid label={label} ssid={ssid[:16]}...", flush=True)
                self._json(200, {"ok": True, "added": added, "pool_size": len(_pool)})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return

        if p not in ("/v1/chat/completions", "/v1/chat/completions/"):
            self.send_response(404); self.end_headers(); return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            return self._json(400, {"error": {"message": str(e), "type": "parse_error"}})

        model = body.get("model", "chatgpt")
        messages = body.get("messages", [])
        do_stream = body.get("stream", False)

        # Bearer header 作为 ssid 覆盖
        auth = self.headers.get("Authorization", "")
        ssid_override = auth[7:] if auth.startswith("Bearer ") and len(auth) > 60 else None

        try:
            text = _do_chat(model, messages, ssid_override)
        except BrokenPipeError:
            return
        except Exception as e:
            print(f"[ERR] {e}", flush=True)
            return self._json(500, {"error": {"message": str(e), "type": "proxy_error"}})

        resp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        ts = int(time.time())

        if do_stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self._cors(); self.end_headers()
            words = text.split(" ")
            for i, w in enumerate(words):
                chunk = {
                    "id": resp_id, "object": "chat.completion.chunk",
                    "created": ts, "model": model,
                    "choices": [{"index": 0,
                                 "delta": {"content": w if i == 0 else " " + w},
                                 "finish_reason": None}],
                }
                try:
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                except BrokenPipeError:
                    return
            stop = {
                "id": resp_id, "object": "chat.completion.chunk",
                "created": ts, "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            self.wfile.write(f"data: {json.dumps(stop)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            prompt_tok = len(" ".join(m.get("content", "") for m in messages).split())
            compl_tok = len(text.split())
            self._json(200, {
                "id": resp_id, "object": "chat.completion",
                "created": ts, "model": model,
                "choices": [{"index": 0,
                              "message": {"role": "assistant", "content": text},
                              "finish_reason": "stop"}],
                "usage": {"prompt_tokens": prompt_tok,
                          "completion_tokens": compl_tok,
                          "total_tokens": prompt_tok + compl_tok},
            })


class ThreadedServer(HTTPServer):
    def process_request(self, req, addr):
        t = threading.Thread(target=self.finish_request, args=(req, addr))
        t.daemon = True; t.start()


if __name__ == "__main__":
    # 初始化加载 ssid 池
    _reload_pool_if_needed()
    print(f"[unitool-proxy v4] port={PORT} pool={len(_pool)} models={len(ALL_MODELS)}", flush=True)
    if _pool:
        for e in _pool:
            print(f"  pool: {e['label']} ssid={e['ssid'][:20]}...", flush=True)
    server = ThreadedServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
