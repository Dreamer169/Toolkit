#!/usr/bin/env python3
"""
yonoo-proxy v2.5
账号池维护系统（参考 gratis 后台维护逻辑）:
  - 失败计数: fail >= SOFT_FAIL → 隔离，fail >= HARD_FAIL → 永久禁用
  - 隔离区账号每 REVIVE_INTERVAL 分钟尝试 re-login 复活
  - 错误分类: timeout/连接重置 = 临时(soft)，403/非200 = 严重(hard)
  - /pool/status 显示 active/isolated/disabled 三个分区数
注册节奏 (同 gratis):
  启动恢复磁盘账号 → 快速注册 50 个 → 5/10min 批量注册到 500
  active < 300 自动补充
"""
import json, os, random, re, time, uuid, threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

YONOO_URL      = "https://yonoo.ai/yonoo/chat"
YONOO_REGISTER = "https://yonoo.ai/api/auth/register"
YONOO_LOGIN    = "https://yonoo.ai/api/auth/login"
ACCOUNTS_FILE  = "/data/yonoo-proxy/accounts.json"

QUICK_BOOT     = 50
BATCH_SIZE     = 10
INTERVAL_SMALL = 5.0
INTERVAL_LARGE = 10.0
LARGE_THRESHOLD= 100
VARIANCE       = 0.20
INTRA_DELAY    = 3.0
TARGET_POOL    = 500
REFILL_BELOW   = 300
TIMEOUT        = 60
PASSWORD       = "Pool@Pass2026!x"

# 维护参数
SOFT_FAIL      = 2     # fail >= SOFT_FAIL → 隔离（还可复活）
HARD_FAIL      = 5     # fail >= HARD_FAIL → 永久禁用
REVIVE_INTERVAL= 15    # 分钟：隔离账号多久尝试一次复活
REVIVE_BATCH   = 20    # 每轮最多复活尝试数

SOCKS_PORTS = [
    10870, 10871, 10872, 10873, 10874, 10875, 10876, 10877, 10878, 10879,
    10880, 10881, 10882, 10883, 10884, 10885, 10886, 10887, 10888, 10889,
    10890, 10891, 10892, 10893, 10894, 10895, 10896, 10897, 10898, 10899,
    10900, 10901, 10902, 10903, 10904, 10905, 10906, 10907, 10908, 10909,
    10910, 10911, 10912, 10913, 10914, 10915, 10916, 10917, 10918, 10919,
]

_proxy_idx  = 0
_proxy_lock = threading.Lock()

def _jitter(base, variance=VARIANCE):
    return max(0.5, random.uniform(base * (1 - variance), base * (1 + variance)))

def _next_proxy():
    global _proxy_idx
    with _proxy_lock:
        port = SOCKS_PORTS[_proxy_idx % len(SOCKS_PORTS)]
        _proxy_idx += 1
    url = "socks5h://127.0.0.1:" + str(port)
    return {"http": url, "https": url}

PROVIDER_MAP = {
    "deepseek": "deepseek", "gpt-4o": "openai", "gpt-5": "openai",
    "openai": "openai", "claude": "claude", "gemini": "gemini",
    "grok": "grok", "perplexity": "perplexity", "llama": "llama",
    "glm": "glm", "qwen": "nscale", "minimax": "infercom", "sonar": "perplexity",
}

# ── 账号池 ────────────────────────────────────────────────────────────────────
# 每个 slot:
#   sess: requests.Session   - 当前会话
#   email: str               - 账号邮箱
#   password: str            - 密码（用于 re-login）
#   state: "active"|"isolated"|"disabled"
#   fail: int                - 连续失败次数
#   hard_fail: int           - 严重错误次数（403/非200）
#   last_fail_ts: float      - 最后一次失败时间
#   revive_at: float         - 下次尝试复活的时间戳

_pool_lock  = threading.Lock()
_pool       = []
_rr_idx     = 0
_seq        = 0

def _pool_stats():
    with _pool_lock:
        active   = sum(1 for s in _pool if s["state"] == "active")
        isolated = sum(1 for s in _pool if s["state"] == "isolated")
        disabled = sum(1 for s in _pool if s["state"] == "disabled")
        total    = len(_pool)
    return {"total": total, "active": active, "isolated": isolated, "disabled": disabled}

# ── 持久化 ────────────────────────────────────────────────────────────────────
_file_lock = threading.Lock()

def _load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    try:
        with open(ACCOUNTS_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def _save_account(email, password):
    with _file_lock:
        accs = _load_accounts()
        if any(a["email"] == email for a in accs):
            return
        accs.append({"email": email, "password": password})
        try:
            with open(ACCOUNTS_FILE, "w") as f:
                json.dump(accs, f, indent=2)
        except Exception as e:
            print("[persist] 写入失败: " + str(e), flush=True)

def _restore_from_disk():
    global _seq
    saved = _load_accounts()
    if not saved:
        print("[persist] 无已保存账号，从头注册", flush=True)
        return 0
    print("[persist] 从磁盘恢复 " + str(len(saved)) + " 个账号...", flush=True)
    ok_count = 0
    for i, rec in enumerate(saved):
        email = rec.get("email", "")
        pwd   = rec.get("password", PASSWORD)
        # 直连登录（VPS直连yonoo.ai无限速，SOCKS大量失败）
        for use_proxy in [False, True]:
            sess = requests.Session()
            if use_proxy:
                sess.proxies.update(_next_proxy())
            try:
                r = sess.post(YONOO_LOGIN,
                    json={"email": email, "password": pwd}, timeout=20)
                if r.status_code == 200:
                    d = r.json()
                    if d.get("success") or d.get("user"):
                        with _pool_lock:
                            _pool.append(_make_slot(sess, email, pwd))
                        ok_count += 1
                        if i > 0 and i % 10 == 0:
                            print("[persist] 恢复进度 " + str(ok_count) + "/" + str(i+1), flush=True)
                        break
                    else:
                        print("[persist] " + email + " login nok: " + r.text[:40], flush=True)
                        break
                else:
                    print("[persist] " + email + " login=" + str(r.status_code), flush=True)
                    break
            except Exception as e:
                if not use_proxy:
                    continue
                print("[persist] " + email + " err: " + str(e)[:60], flush=True)
        time.sleep(0.3)
    with _pool_lock:
        _seq = len(saved)
    print("[persist] 恢复完成: " + str(ok_count) + "/" + str(len(saved)), flush=True)
    return ok_count

# ── 槽位构造 ──────────────────────────────────────────────────────────────────

def _make_slot(sess, email, password):
    return {
        "sess": sess, "email": email, "password": password,
        "state": "active", "fail": 0, "hard_fail": 0,
        "last_fail_ts": 0.0, "revive_at": 0.0,
    }

# ── 注册 ──────────────────────────────────────────────────────────────────────

def _next_seq():
    global _seq
    with _pool_lock:
        v = _seq
        _seq += 1
    return v

def _register_one(idx):
    import string as _str, random as _rnd
    suffix = "".join(_rnd.choices(_str.ascii_lowercase + _str.digits, k=8))
    email = "ynp_" + uuid.uuid4().hex[:10] + "_" + suffix + "@mail.ru"
    name  = "YPool" + str(idx)
    # 优先直连VPS（无限速）；失败后fallback SOCKS
    for use_proxy in [False, True]:
        sess = requests.Session()
        if use_proxy:
            proxy = _next_proxy()
            sess.proxies.update(proxy)
            tag = "socks:" + proxy["https"].rsplit(":", 1)[-1]
        else:
            tag = "direct"
        try:
            r = sess.post(YONOO_REGISTER,
                json={"name": name, "email": email, "password": PASSWORD},
                timeout=20)
            if r.status_code in (200, 201):
                d = r.json()
                if d.get("success") or d.get("user"):
                    r2 = sess.post(YONOO_LOGIN,
                        json={"email": email, "password": PASSWORD}, timeout=15)
                    if r2.status_code == 200:
                        print("[pool] +#" + str(idx) + ": " + email + " (" + tag + ")", flush=True)
                        _save_account(email, PASSWORD)
                        return _make_slot(sess, email, PASSWORD)
                    else:
                        print("[pool] #" + str(idx) + " login " + str(r2.status_code), flush=True)
                        break
                else:
                    print("[pool] #" + str(idx) + " nok(" + tag + "): " + r.text[:60], flush=True)
                    break
            else:
                print("[pool] #" + str(idx) + " reg " + str(r.status_code) + "(" + tag + "): " + r.text[:60], flush=True)
                if r.status_code == 429:
                    break  # 限速，不重试
        except Exception as e:
            if not use_proxy:
                continue  # 直连失败，尝试代理
            print("[pool] #" + str(idx) + " err: " + str(e)[:80], flush=True)
    return None

# ── 请求路由 ──────────────────────────────────────────────────────────────────

def _get_slot():
    global _rr_idx
    with _pool_lock:
        ok = [s for s in _pool if s["state"] == "active"]
        if not ok:
            return None
        slot = ok[_rr_idx % len(ok)]
        _rr_idx += 1
        return slot

def _mark_fail(slot, hard=False):
    """
    soft fail (timeout/连接重置): fail+1，>= SOFT_FAIL → isolated
    hard fail (403/非200/error):  hard_fail+1 且 fail+1，>= HARD_FAIL → disabled
    """
    with _pool_lock:
        slot["fail"] += 1
        slot["last_fail_ts"] = time.time()
        if hard:
            slot["hard_fail"] += 1

        if slot["hard_fail"] >= HARD_FAIL:
            if slot["state"] != "disabled":
                slot["state"] = "disabled"
                print("[maint] disabled(perm) " + slot["email"], flush=True)
        elif slot["fail"] >= SOFT_FAIL and slot["state"] == "active":
            slot["state"] = "isolated"
            slot["revive_at"] = time.time() + _jitter(REVIVE_INTERVAL * 60)
            print("[maint] isolated " + slot["email"]
                  + " (fail=" + str(slot["fail"]) + ")", flush=True)

def _mark_ok(slot):
    with _pool_lock:
        slot["fail"] = 0
        slot["state"] = "active"

# ── 维护线程：隔离区复活 ──────────────────────────────────────────────────────

def _maintainer():
    """每 5 分钟扫描一次隔离区，对到期账号尝试 re-login 复活"""
    while True:
        time.sleep(300)
        now = time.time()
        with _pool_lock:
            candidates = [s for s in _pool
                          if s["state"] == "isolated" and s["revive_at"] <= now]
        candidates = candidates[:REVIVE_BATCH]
        if not candidates:
            continue
        print("[maint] 复活检查: " + str(len(candidates)) + " 个隔离账号...", flush=True)
        revived = 0
        for slot in candidates:
            proxy = _next_proxy()
            sess  = requests.Session()
            sess.proxies.update(proxy)
            try:
                r = sess.post(YONOO_LOGIN, json={
                    "email": slot["email"], "password": slot.get("password", PASSWORD)
                }, timeout=20)
                if r.status_code == 200:
                    # 探测一次确认真的可用
                    probe = sess.post(YONOO_URL,
                        json={"message": "hi", "taskType": "general"}, timeout=20)
                    if probe.status_code == 200:
                        with _pool_lock:
                            slot["sess"]  = sess
                            slot["state"] = "active"
                            slot["fail"]  = 0
                        print("[maint] revived " + slot["email"], flush=True)
                        revived += 1
                    else:
                        # 探测失败，推迟下次复活
                        with _pool_lock:
                            slot["hard_fail"] += 1
                            if slot["hard_fail"] >= HARD_FAIL:
                                slot["state"] = "disabled"
                                print("[maint] disabled(probe) " + slot["email"], flush=True)
                            else:
                                slot["revive_at"] = time.time() + _jitter(REVIVE_INTERVAL * 60 * 2)
                else:
                    with _pool_lock:
                        slot["revive_at"] = time.time() + _jitter(REVIVE_INTERVAL * 60)
            except Exception as e:
                with _pool_lock:
                    slot["revive_at"] = time.time() + _jitter(REVIVE_INTERVAL * 60)
            time.sleep(1)
        if revived:
            print("[maint] 本轮复活 " + str(revived) + " 个，"
                  + str(_pool_stats()["active"]) + " active", flush=True)

# ── 注册调度 ──────────────────────────────────────────────────────────────────

def _active_count():
    with _pool_lock:
        return sum(1 for s in _pool if s["state"] == "active")

def _pool_size():
    with _pool_lock:
        return len(_pool)

def _do_batch(count, label="batch"):
    ok = 0
    for i in range(count):
        idx = _next_seq()
        r = _register_one(idx)
        if r:
            with _pool_lock:
                _pool.append(r)
            ok += 1
        if i < count - 1:
            time.sleep(_jitter(INTRA_DELAY))
    st = _pool_stats()
    print("[sched] " + label + " ok=" + str(ok) + "/" + str(count)
          + " active=" + str(st["active"]) + " iso=" + str(st["isolated"])
          + " dis=" + str(st["disabled"]) + " total=" + str(st["total"]), flush=True)
    return ok

def _schedule_register():
    restored = _restore_from_disk()
    already  = _pool_size()

    need_quick = max(0, QUICK_BOOT - already)
    if need_quick > 0:
        print("[sched] 阶段1: 快速注册 " + str(need_quick) + " 个（已恢复=" + str(already) + "）", flush=True)
        _do_batch(need_quick, label="quick-boot")
    else:
        print("[sched] 阶段1: 已恢复 " + str(already) + " 个，跳过快速注册", flush=True)

    print("[sched] 阶段2: gratis节奏填充至 " + str(TARGET_POOL) + "...", flush=True)
    while _pool_size() < TARGET_POOL:
        active = _active_count()
        wait_sec = _jitter((INTERVAL_LARGE if active >= LARGE_THRESHOLD else INTERVAL_SMALL) * 60)
        st = _pool_stats()
        print("[sched] 等 {:.1f}min (active={} iso={} dis={} total={}/{})".format(
            wait_sec/60, st["active"], st["isolated"], st["disabled"], st["total"], TARGET_POOL), flush=True)
        time.sleep(wait_sec)
        remaining = TARGET_POOL - _pool_size()
        count = min(BATCH_SIZE, remaining)
        if count > 0:
            _do_batch(count, label="fill")

    print("[sched] 已达 " + str(TARGET_POOL) + "，进入监控模式", flush=True)

    while True:
        time.sleep(60)
        active = _active_count()
        total  = _pool_size()
        if active < REFILL_BELOW:
            need = max(TARGET_POOL - total, REFILL_BELOW - active + 20)
            print("[sched] active=" + str(active) + " < " + str(REFILL_BELOW)
                  + "，补充 " + str(need) + " 个", flush=True)
            done = 0
            while done < need:
                count = min(BATCH_SIZE, need - done)
                done += _do_batch(count, label="refill")
                if done < need:
                    time.sleep(_jitter(INTERVAL_SMALL * 60))

# ── HTTP 处理 ─────────────────────────────────────────────────────────────────

def detect_provider(model):
    m = (model or "").lower()
    for key, prov in PROVIDER_MAP.items():
        if key in m:
            return prov
    return "deepseek"

MODELS = ["deepseek-chat", "gpt-4o", "claude-sonnet-4", "gemini-flash",
          "grok-3", "llama-4-scout", "glm-4.7", "qwen-3", "minimax-m2", "sonar-pro"]

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        for h, v in [("Access-Control-Allow-Origin", "*"),
                     ("Access-Control-Allow-Methods", "POST, GET, OPTIONS"),
                     ("Access-Control-Allow-Headers", "Content-Type, Authorization")]:
            self.send_header(h, v)
        self.end_headers()

    def do_GET(self):
        if self.path == "/v1/models":
            self._json({"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "yonoo"} for m in MODELS]})
        elif self.path == "/pool/status":
            st = _pool_stats()
            st["saved_on_disk"] = len(_load_accounts())
            st["target"] = TARGET_POOL
            st["refill_below"] = REFILL_BELOW
            st["soft_fail_threshold"] = SOFT_FAIL
            st["hard_fail_threshold"] = HARD_FAIL
            st["revive_interval_min"] = REVIVE_INTERVAL
            self._json(st)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self._json({"error": "not found"}, 404); return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"error": {"message": "bad json"}}, 400); return

        messages = body.get("messages", [])
        model    = body.get("model", "deepseek-chat")
        stream   = body.get("stream", False)

        user_parts = []
        for m in messages:
            role    = m.get("role", "")
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            if role == "system":
                user_parts.insert(0, "[System]: " + content)
            elif role == "user":
                user_parts.append(content)
            elif role == "assistant":
                user_parts.append("[Assistant]: " + content)
        user_msg = "\n".join(user_parts)

        provider  = detect_provider(model)
        yonoo_msg = "[" + provider + "] " + user_msg if provider != "openai" else user_msg

        slot = _get_slot()
        if not slot:
            self._json({"error": {"message": "No available yonoo accounts",
                                  "type": "service_unavailable"}}, 503)
            return

        try:
            resp = slot["sess"].post(YONOO_URL,
                json={"message": yonoo_msg, "taskType": "general"}, timeout=TIMEOUT)

            if resp.status_code != 200:
                # 非200 = 严重错误（hard fail）
                _mark_fail(slot, hard=True)
                self._json({"error": {"message": "upstream " + str(resp.status_code)
                                      + ": " + resp.text[:120]}}, 502)
                return

            data = resp.json()
            if data.get("type") == "error" or "error" in data:
                _mark_fail(slot, hard=True)
                self._json({"error": {"message": data.get("message",
                                      str(data.get("error", "Unknown")))}}, 500)
                return

            _mark_ok(slot)
            reply = data.get("response", data.get("reply", ""))
            for pfx in ["[deepseek] ", "[claude] ", "[gpt-5.2] ",
                        "[openai] ", "[gemini] ", "[grok] "]:
                reply = reply.replace(pfx, "")
            reply = re.sub(r'\[\d+\]', '', reply)

            resp_id = "chatcmpl-" + uuid.uuid4().hex[:12]
            ts      = int(time.time())

            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                for chunk in [reply[i:i+10] for i in range(0, len(reply), 10)]:
                    d = {"id": resp_id, "object": "chat.completion.chunk",
                         "created": ts, "model": model,
                         "choices": [{"index": 0, "delta": {"content": chunk},
                                      "finish_reason": None}]}
                    self.wfile.write(("data: " + json.dumps(d, ensure_ascii=False)
                                     + "\n\n").encode())
                    self.wfile.flush()
                    time.sleep(0.02)
                stop = {"id": resp_id, "object": "chat.completion.chunk",
                        "created": ts, "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                self.wfile.write(("data: " + json.dumps(stop) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                pt = len(user_msg) // 4
                ct = len(reply) // 4
                self._json({"id": resp_id, "object": "chat.completion",
                    "created": ts, "model": model,
                    "choices": [{"index": 0,
                                 "message": {"role": "assistant", "content": reply},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                              "total_tokens": pt + ct}})
        except Exception as e:
            err = str(e).lower()
            # 连接/超时 = 临时(soft)；其他 = 严重(hard)
            is_soft = any(k in err for k in ("timeout", "connection", "reset", "aborted", "sock"))
            _mark_fail(slot, hard=not is_soft)
            self._json({"error": {"message": str(e)}}, 500)


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    print("[yonoo-proxy v2.5] target=" + str(TARGET_POOL)
          + " soft=" + str(SOFT_FAIL) + " hard=" + str(HARD_FAIL)
          + " revive=" + str(REVIVE_INTERVAL) + "min", flush=True)

    # 维护线程
    threading.Thread(target=_maintainer, name="maintainer", daemon=True).start()
    # 注册调度线程
    threading.Thread(target=_schedule_register, name="sched-reg", daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", 3099), Handler)
    print("[yonoo-proxy] listening on :3099", flush=True)
    server.serve_forever()
