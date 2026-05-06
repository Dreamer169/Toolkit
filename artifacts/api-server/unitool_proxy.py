#!/usr/bin/env python3
"""
unitool.ai → OpenAI 兼容反代 v4.5
改进:
  - 修复 pool 重复条目 bug（同一 ssid 出现在多个文件时去重）
  - 修复 /add-ssid 推入的条目在文件扫描后不丢失（live entries 保留）
  - 后台余额监控线程：每 30min 检查各账号余额，低余额/耗尽时打印警告
  - /pool-status 包含 balance 字段
  - 版本: v4.5
"""
import json, time, uuid, threading, ssl, os, re, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError

PORT = int(os.environ.get("PORT", 8089))
BASE = "https://unitool.ai"
SSID_DIR = "/tmp"
SSID_PATTERN = re.compile(r"^unitool_ssid\d*\.txt$")
BALANCE_CHECK_INTERVAL = 1800   # 每个账号每 30 分钟检查一次
BALANCE_LOW_WARN = 0.5          # 低于此值打警告

ctx = ssl.create_default_context()
_lock = threading.Lock()

# ─── SSID 池 ────────────────────────────────────────────────────────────────
_pool: list = []
_pool_idx: int = 0
_pool_mtime: float = 0.0

def _scan_ssid_files() -> list:
    """扫描 /tmp/unitool_ssid*.txt，返回 (filename, ssid) 列表"""
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
    """
    重建 pool：
    1. 文件扫描结果按 ssid 内容去重（同内容多个文件只取一次）
    2. /add-ssid 推入的 live 条目（不在文件里的）也保留，不再丢弃
    3. dead 条目（无论来源）在到期前保留
    """
    global _pool, _pool_mtime
    now = time.time()
    if now - _pool_mtime < 5:
        return
    _pool_mtime = now

    files = _scan_ssid_files()
    with _lock:
        existing = {e["ssid"]: e for e in _pool}
        new_pool = []
        seen_ssids: set = set()

        # 1. 文件来源（按 ssid 内容去重）
        for (fn, ssid) in files:
            if ssid in seen_ssids:
                continue          # 跳过重复内容
            seen_ssids.add(ssid)
            if ssid in existing:
                new_pool.append(existing[ssid])   # 保留已有条目（含 email label）
            else:
                new_pool.append({
                    "ssid": ssid, "label": fn,
                    "dead_until": 0, "dead_reason": "",
                    "chats": {}, "bad_chats": set(), "balance": None,
                })
                print(f"[POOL] added {fn} ssid={ssid[:16]}...", flush=True)

        # 2. 保留不在文件里的 dead 条目（暂时 423/expired，等恢复）
        for e in _pool:
            if e["ssid"] not in seen_ssids:
                if e["dead_until"] > now:
                    new_pool.append(e)

        _pool = new_pool

def _mark_dead(ssid: str, secs: int = 600, reason: str = ""):
    with _lock:
        for e in _pool:
            if e["ssid"] == ssid:
                e["dead_until"] = time.time() + secs
                e["dead_reason"] = reason
                print(f"[POOL] marked dead {ssid[:16]}... for {secs}s reason={reason!r}", flush=True)
                break

def _hdrs(ssid: str) -> dict:
    return {
        "Cookie":       f"__Secure-unitool-ssid={ssid}",
        "Content-Type": "application/json",
        "User-Agent":   "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/136",
        "Origin":       "https://unitool.ai",
        "Referer":      "https://unitool.ai/en/chatgpt",
        "Accept":       "application/json",
    }

def _check_balance(ssid: str) -> float | None:
    """返回账号 token 余额，失败返回 None"""
    try:
        req = Request(f"{BASE}/api/user/billing-accounts",
                      headers=_hdrs(ssid), method="GET")
        with urlopen(req, context=ctx, timeout=10) as r:
            d = json.loads(r.read())
        accounts = d.get("accounts", [])
        if not accounts:
            return None
        return sum(float(a.get("value", 0)) for a in accounts)
    except Exception:
        return None

def _balance_monitor_loop():
    """后台线程：定期检查所有账号余额，打印告警"""
    # 启动延迟 2 分钟，避免和启动初始化竞争
    time.sleep(120)
    while True:
        try:
            with _lock:
                entries = list(_pool)
            now = time.time()
            for e in entries:
                # 跳过太快检查的（每账号间隔 BALANCE_CHECK_INTERVAL）
                last_check = e.get("_balance_ts", 0)
                if now - last_check < BALANCE_CHECK_INTERVAL:
                    continue
                bal = _check_balance(e["ssid"])
                e["_balance_ts"] = now
                e["balance"] = bal
                if bal is None:
                    print(f"[BAL] {e['label']}: check failed", flush=True)
                elif bal <= 0:
                    print(f"[BAL] ⚠ {e['label']}: balance={bal:.3f} EXHAUSTED", flush=True)
                elif bal < BALANCE_LOW_WARN:
                    print(f"[BAL] ⚠ {e['label']}: balance={bal:.3f} LOW", flush=True)
                else:
                    print(f"[BAL] ✓ {e['label']}: balance={bal:.3f}", flush=True)
                time.sleep(2)   # 账号间稍作间隔
        except Exception as ex:
            print(f"[BAL] monitor error: {ex}", flush=True)
        time.sleep(60)   # 每分钟检查一轮（跳过未到期的）

# ─── 服务 ID & 模型映射 ──────────────────────────────────────────────────────
NATIVE_SERVICES = {
    # ── 实测可用 (批量扫描 2026-05-06 确认（含 gpt-5.4）) ──
    "gpt-5",             # ChatGPT 5  ✓
    "gpt-5.5",           # ChatGPT 5.5  ✓
    "gpt-4-1",           # GPT-4.1 (连字符，实测最快)  ✓
    "gpt5.1",            # GPT-5.1  ✓
    "gpt5.2",            # GPT-5.2  ✓
    "gpt-4o-mini",       # GPT-4o-mini (快速小模型)  ✓
    "gpt-5.4",           # GPT-5.4  ✓
    "claude-sonnet",     # Claude Sonnet 最新版  ✓
    "claude-sonnet-4-5", # Claude Sonnet 4.5  ✓
    "claude-sonnet-4-6", # Claude Sonnet 4.6  ✓
    "claude-opus-4-6",   # Claude Opus 4.6  ✓
    # gpt-4o → 持续超时暂不 native；gpt-o3-pro → 后端无内容
}

# 服务降级链：primary 失败时按顺序尝试备选（同家族）
# 触发条件：service_error / timeout / 500
FALLBACK_CHAINS: dict[str, list[str]] = {
    # ── GPT 系 ──
    "gpt-5":       ["gpt-5.5",   "gpt-5.4",  "gpt4.1",    "gpt-4-1",  "gpt-4o-mini"],
    "gpt-5.5":     ["gpt-5",     "gpt-5.4",  "gpt-4-1",   "gpt-4o-mini"],
    "gpt-5.4":     ["gpt-5.5",   "gpt-5",    "gpt-4-1",   "gpt-4o-mini"],
    "gpt5.1":      ["gpt5.2",    "gpt-5",    "gpt-5.5",   "gpt-4-1"],
    "gpt5.2":      ["gpt5.1",    "gpt-5",    "gpt-5.5",   "gpt-4-1"],
    "gpt-4-1":     ["gpt-5.4",   "gpt-5",    "gpt-4o-mini"],
    "gpt-4o-mini": ["gpt-4-1",   "gpt-5.4",  "gpt-5"],
    # ── Claude 系 ──
    "claude-opus-4-6":  ["claude-sonnet-4-6", "claude-sonnet-4-5", "claude-sonnet"],
    "claude-sonnet-4-6":["claude-sonnet-4-5", "claude-sonnet",     "claude-opus-4-6"],
    "claude-sonnet-4-5":["claude-sonnet-4-6", "claude-sonnet",     "claude-opus-4-6"],
    "claude-sonnet":    ["claude-sonnet-4-5", "claude-sonnet-4-6", "claude-opus-4-6"],
}

def _is_retryable_service_error(msg: str) -> bool:
    """判断 service_error 是否值得切换 service 重试"""
    RETRYABLE = [
        "No content returned",
        "Reasoning is mandatory",
        "max_tokens",
        "context_length_exceeded",
        "overloaded",
        "rate_limit",
        "capacity",
        "unavailable",
        "timeout",
        "upstream",
        "Bad gateway",
        "Service Unavailable",
    ]
    ml = msg.lower()
    return any(k.lower() in ml for k in RETRYABLE)

MODEL_ALIASES = {
    # ── OpenAI GPT 系 ──
    "gpt-4":                      "gpt-4-1",     # → native (实测快)
    "gpt-4-turbo":                "gpt-4-1",
    "gpt-4-turbo-preview":        "gpt-4-1",
    "gpt-4.1":                    "gpt-4-1",     # 点号 → 连字符
    "gpt-4o":                     "gpt-4-1",     # gpt-4o 超时，回退 gpt-4-1
    "gpt-4o-search":              "gpt-4-1",
    "gpt-4.5":                    "gpt-4-1",
    "gpt-4o-2024-11-20":          "gpt-4-1",
    "gpt-4o-mini-2024-07-18":     "gpt-4o-mini", # → native
    "gpt-4o-mini-search":         "gpt-4o-mini",
    "gpt-3.5-turbo":              "gpt-4o-mini", # 轻量 → mini
    "gpt-3.5-turbo-0613":         "gpt-4o-mini",
    "gpt-3.5-turbo-16k":          "gpt-4o-mini",
    "text-davinci-003":           "gpt-4o-mini",
    # Reasoning 系 → gpt-5.5
    "o1":                         "gpt-5.5",
    "o1-mini":                    "gpt-5.5",
    "o1-pro":                     "gpt-5.5",
    "o1-preview":                 "gpt-5.5",
    "o3":                         "gpt-5.5",
    "o3-mini":                    "gpt-5.5",
    "o4-mini":                    "gpt-5.5",
    "o4":                         "gpt-5.5",
    "gpt-5-turbo":                "gpt-5",
    "chatgpt-5":                  "gpt-5",       # → gpt-5 (旗舰)
    "chatgpt-5-turbo":            "gpt-5",
    "chatgpt-5.5":                "gpt-5.5",
    "chatgpt-5.5-turbo":          "gpt-5.5",
    "chatgpt":                    "gpt-5.5",
    # ── Anthropic Claude 系 ──
    # claude-opus-4-6 (连字符 service_id) 实测可用 ✓
    # claude-opus-4.5 / claude-opus-4-5 均不可用，全部指向 4-6
    "claude-opus":                "claude-opus-4-6",   # 泛型 → 最新可用 Opus
    "claude-opus-4":              "claude-opus-4-6",
    "claude-opus-4-5":            "claude-opus-4-6",   # 4-5 不可用，回退 4-6
    "claude-opus-4.5":            "claude-opus-4-6",
    "claude-opus-4-6":            "claude-opus-4-6",   # NATIVE (别名冗余兼容)
    "claude-opus-4.6":            "claude-opus-4-6",   # 点号 → 连字符 service_id
    "claude-opus-4-latest":       "claude-opus-4-6",
    "claude-opus-latest":         "claude-opus-4-6",
    "claude-3-opus":              "claude-sonnet",
    "claude-3-opus-20240229":     "claude-sonnet",
    "claude":                     "claude-sonnet",
    "claude-sonnet-4":            "claude-sonnet",
    "claude-sonnet-4-5":          "claude-sonnet-4-5",  # native alias
    "claude-3-7-sonnet":          "claude-sonnet",
    "claude-3-7-sonnet-20250219": "claude-sonnet",
    "claude-3-5-sonnet":          "claude-sonnet",
    "claude-3-5-sonnet-20241022": "claude-sonnet",
    "claude-3-5-haiku":           "claude-sonnet",
    "claude-3-5-haiku-20241022":  "claude-sonnet",
    "claude-3-haiku":             "claude-sonnet",
    "claude-3-haiku-20240307":    "claude-sonnet",
    "claude-3-sonnet":            "claude-sonnet",
    "claude-3-sonnet-20240229":   "claude-sonnet",
    "claude-haiku":               "claude-sonnet",
    "claude-3-5-sonnet-latest":   "claude-sonnet",
    "claude-3-7-sonnet-latest":   "claude-sonnet",
    "claude-sonnet-latest":       "claude-sonnet",
    # ── Google Gemini 系 (SSE协议，回退 gpt-4o) ──
    "gemini":                     "gpt-4o",
    "gemini-2.5-pro":             "gpt-4o",
    "gemini-2.5-flash":           "gpt-4o",
    "gemini-2.0-pro":             "gpt-4o",
    "gemini-2.0-pro-exp":         "gpt-4o",
    "gemini-2.0-flash":           "gpt-4o",
    "gemini-2.0":                 "gpt-4o",
    "gemini-flash-2.0":           "gpt-4o",
    "flash-2.0":                  "gpt-4o",
    "gemini-1.5-pro":             "gpt-4o",
    "gemini-1.5-flash":           "gpt-4o",
    "gemini-1.0-pro":             "gpt-4o",
    "gemini-pro-1.0":             "gpt-4o",
    "gemini-pro":                 "gpt-4o",
    "gemini-exp":                 "gpt-4o",
    "gemini-pro-exp":             "gpt-4o",
    "gemini-flash":               "gpt-4o",
    "gemini-ultra":               "gpt-4o",
    # ── xAI / Grok 系 (回退 gpt-5.5) ──
    "grok":                       "gpt-5.5",
    "x-ai":                       "gpt-5.5",
    "xai":                        "gpt-5.5",
    "grok-3":                     "gpt-5.5",
    "grok-3-fast":                "gpt-5.5",
    "grok-3-mini":                "gpt-5.5",
    "grok-3-mini-fast":           "gpt-5.5",
    "grok-2":                     "gpt-5.5",
    "grok-2-1212":                "gpt-5.5",
    "grok-2-mini":                "gpt-5.5",
    "grok-beta":                  "gpt-5.5",
    "grok-mini":                  "gpt-5.5",
    # ── DeepSeek 系 ──
    "deepseek":                   "gpt-5.5",
    "deepseek-r1":                "gpt-5.5",
    "deepseek-v3":                "gpt-5.5",
    "deepseek-chat":              "gpt-5.5",
}

def _resolve_model(model: str) -> str:
    if model in NATIVE_SERVICES:
        return model
    if model in MODEL_ALIASES:
        return MODEL_ALIASES[model]
    m = model.lower()
    if "claude" in m:   return "claude-sonnet"     # 未知 claude → Sonnet (更稳定)
    if "gemini" in m:   return "gpt-4o"
    if "grok" in m:     return "gpt-5.5"
    if "deepseek" in m: return "gpt-5.5"
    return "gpt-5.5"   # 未知模型 → 最强可用

ALL_MODELS = sorted(NATIVE_SERVICES | set(MODEL_ALIASES.keys()))
MODELS_LIST = [{"id": m, "object": "model", "created": 1700000000, "owned_by": "unitool"}
               for m in ALL_MODELS]

# ─── API 核心 ────────────────────────────────────────────────────────────────
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

_rr_idx = 0
_rr_lock = threading.Lock()

def _pick_entry(entries: list) -> dict:
    global _rr_idx
    with _rr_lock:
        idx = _rr_idx % len(entries)
        _rr_idx += 1
        return entries[idx]

def _try_service(service_id: str, content: str, entries: list) -> str:
    """在给定 entries 池中对 service_id 发起请求（含 ssid 轮转 + 单次 chat 重试）"""
    last_err = None
    for _ in range(len(entries) * 2):
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
    raise Exception(f"all ssids failed for {service_id}: {last_err}")


def _do_chat(model: str, messages: list, ssid_override: str | None = None) -> str:
    primary_id = _resolve_model(model)
    content = _fmt(messages)
    print(f"[REQ] {model}→{primary_id} msgs={len(messages)}", flush=True)

    if ssid_override and len(ssid_override) > 50:
        entry = {"ssid": ssid_override, "label": "header",
                 "dead_until": 0, "chats": {}, "bad_chats": set(), "balance": None}
        entries = [entry]
    else:
        _reload_pool_if_needed()
        with _lock:
            live = [e for e in _pool if e["dead_until"] <= time.time()]
            entries = live if live else _pool[:]

    if not entries:
        raise Exception("ssid pool empty — please login first")

    # 构建本次请求的 service 尝试顺序：primary + 该 primary 的降级链
    chain = [primary_id] + [s for s in FALLBACK_CHAINS.get(primary_id, [])
                             if s != primary_id]

    last_err = None
    for idx, service_id in enumerate(chain):
        if idx > 0:
            print(f"[FALLBACK] {model}: {chain[idx-1]} → {service_id}", flush=True)
        try:
            result = _try_service(service_id, content, entries)
            if idx > 0:
                print(f"[FALLBACK] succeeded with {service_id}", flush=True)
            return result
        except TimeoutError as e:
            print(f"[FALLBACK] {service_id} timeout, trying next", flush=True)
            last_err = e
            continue
        except Exception as e:
            err_msg = str(e)
            # service_error → 判断是否值得换服务
            if "service_error:" in err_msg:
                detail = err_msg.split("service_error:", 1)[-1].strip()
                if _is_retryable_service_error(detail):
                    print(f"[FALLBACK] {service_id} svc_err={detail[:80]!r}, trying next", flush=True)
                    last_err = e
                    continue
                else:
                    # 不可重试的服务错误（如内容违规等）直接抛出
                    raise
            # HTTP 500 → 也尝试降级
            if "500" in err_msg and idx < len(chain) - 1:
                print(f"[FALLBACK] {service_id} HTTP 500, trying next", flush=True)
                last_err = e
                continue
            # 其他错误（pool 耗尽等）直接抛出
            raise

    raise Exception(f"all services in chain failed ({chain}): {last_err}")

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
                    "label":       e["label"],
                    "ssid_prefix": e["ssid"][:20] + "...",
                    "dead":        e["dead_until"] > now,
                    "dead_until":  e["dead_until"],
                    "dead_reason": e.get("dead_reason", ""),
                    "chats":       len(e["chats"]),
                    "balance":     e.get("balance"),
                } for e in _pool]
            live = sum(1 for a in pool_info if not a["dead"])
            self._json(200, {"pool_size": len(_pool), "live": live, "accounts": pool_info})
        elif p == "/ssid-status":
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
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                ssid = body.get("ssid", "").strip()
                label = body.get("label", "api")
                if len(ssid) < 50:
                    return self._json(400, {"error": "ssid too short"})
                added = False
                updated = False
                with _lock:
                    same_label = next((e for e in _pool if e["label"] == label), None)
                    same_ssid  = next((e for e in _pool if e["ssid"]  == ssid),  None)
                    if same_label and same_label["ssid"] != ssid:
                        # 同账号新 ssid：原地替换，重置状态
                        same_label["ssid"] = ssid
                        same_label["dead_until"] = 0
                        same_label["dead_reason"] = ""
                        same_label["chats"] = {}
                        same_label["bad_chats"] = set()
                        updated = True
                        print(f"[POOL] /add-ssid UPDATE {label} ssid={ssid[:16]}...", flush=True)
                    elif same_ssid and not same_label:
                        # 同 ssid 但 label 变了（文件→email）
                        if "@" in label:
                            same_ssid["label"] = label
                            updated = True
                    elif not same_label and not same_ssid:
                        _pool.append({
                            "ssid": ssid, "label": label,
                            "dead_until": 0, "dead_reason": "",
                            "chats": {}, "bad_chats": set(), "balance": None,
                        })
                        added = True
                        print(f"[POOL] /add-ssid NEW {label} ssid={ssid[:16]}...", flush=True)
                self._json(200, {"ok": True, "added": added, "updated": updated,
                                 "pool_size": len(_pool)})
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

        model = body.get("model", "gpt-5.5")
        messages = body.get("messages", [])
        do_stream = body.get("stream", False)

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
            stop_chunk = {
                "id": resp_id, "object": "chat.completion.chunk",
                "created": ts, "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            self.wfile.write(f"data: {json.dumps(stop_chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            prompt_tok = len(" ".join(m.get("content", "") for m in messages
                                      if isinstance(m.get("content"), str)).split())
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
    _reload_pool_if_needed()
    print(f"[unitool-proxy v4.5] port={PORT} pool={len(_pool)} models={len(ALL_MODELS)}", flush=True)
    for e in _pool:
        print(f"  pool: {e['label']} ssid={e['ssid'][:20]}...", flush=True)

    # 启动余额监控后台线程
    t = threading.Thread(target=_balance_monitor_loop, daemon=True)
    t.start()
    print("[unitool-proxy v4.5] balance monitor started (first check in 2min)", flush=True)

    server = ThreadedServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
