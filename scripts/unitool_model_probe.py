#!/usr/bin/env python3
"""
unitool_model_probe.py v2.0 — 并发多线程模型探针

策略:
  - 10路并发，每服务独立 SSID 账号（防止同账号并发冲突）
  - Phase 1 (max_tokens probe): 发 max_tokens=999999 → 等 32s
    unitool 忽略此字段但可能引发 claude-opus 32768>32000 的 400 错误
  - Phase 2 (alive probe, 若 p1 timeout): 正常发消息 → 等 30s
  - 全程捕获 HTTP 层错误、msg_send 错误、paginatedMessages 错误

关键发现 (v1.x 运行结果):
  - claude-haiku: 404 "model: claude-3-5-haiku-20241022" (即时报错)
  - gpt-5/5.5/5.4/gpt-o*系列: double_timeout (>67s 无响应，可能彻底挂死)
  - unitool 忽略 chat_settings.max_tokens，所以 999999 不影响后端

v2.0 新增:
  - ThreadPoolExecutor 10路并发
  - 每个线程使用不同 SSID（从文件列表按索引分配）
  - 每个线程用不同 RESI 端口（轮询分配）
  - 最终汇总表 + JSON 输出
"""
import argparse, json, os, re, sys, time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE     = "https://unitool.ai"
SSID_DIR = "/data/unitool_ssids"
ALL_RESI = list(range(10851, 10860)) + list(range(10870, 10890))

ALL_SERVICES = [
    "gpt-5", "gpt-5.5", "gpt-5.4", "gpt-5-nano",
    "gpt-4o", "gpt-4o-mini", "gpt-4-1",
    "gpt-o1", "gpt-o1-mini", "gpt-o3", "gpt-o3-mini", "gpt-o3-pro", "gpt-o4-mini",
    "claude-sonnet", "claude-sonnet-4-5", "claude-sonnet-4-6",
    "claude-opus", "claude-opus-4-6", "claude-haiku",
    "gemini-3.1-pro", "gemini-3-pro",
    "grok",
]

REASONING_SVCS = {
    "gemini-3.1-pro", "gemini-3-pro", "grok",
    "gpt-o1", "gpt-o1-mini", "gpt-o3", "gpt-o3-mini", "gpt-o3-pro", "gpt-o4-mini", "gpt-5-nano",
}

PROBE_BIG  = 999999
PROBE_MSG  = "Reply only: PROBE_OK"
ALIVE_MSG  = "Say: ALIVE"
POLL_SECS  = 32
ALIVE_SECS = 30
POLL_IV    = 1.0
CONCURRENCY = 10

# ── SSID / port pool ──────────────────────────────────────────────────────────

GOOD_SSID_CACHE = "/tmp/probe_ssids.txt"  # pre-verified accounts with balance

def _load_ssids(n):
    """Load pre-verified SSIDs from cache, falling back to raw files."""
    # Prefer pre-verified balance-checked list
    if os.path.exists(GOOD_SSID_CACHE):
        lines = [l.strip() for l in open(GOOD_SSID_CACHE).readlines() if l.strip()]
        if lines:
            # cycle if we need more than what's in the file
            out = []
            while len(out) < n:
                out.extend(lines)
            return out[:n]
    # Fallback: raw SSID directory
    files = sorted(f for f in os.listdir(SSID_DIR) if f.endswith(".txt") and "@" in f)
    out = []
    for fn in files:
        if len(out) >= n:
            break
        ssid = open(os.path.join(SSID_DIR, fn)).read().strip()
        if ssid:
            out.append(ssid)
    if not out:
        raise RuntimeError("No SSIDs in " + SSID_DIR)
    return out

def _pick_port():
    for p in ALL_RESI:
        try:
            s = requests.Session()
            s.proxies = {"https": "socks5h://127.0.0.1:%d" % p}
            r = s.get("https://www.google.com/generate_204", timeout=5)
            if r.status_code in (200, 204):
                return p
        except Exception:
            pass
    raise RuntimeError("No alive RESI port")

# ── per-thread session (thread-local) ─────────────────────────────────────────

import threading
_tl = threading.local()

def _get_sess(port):
    if not hasattr(_tl, "sessions"):
        _tl.sessions = {}
    if port not in _tl.sessions:
        s = requests.Session()
        s.proxies = {
            "http":  "socks5h://127.0.0.1:%d" % port,
            "https": "socks5h://127.0.0.1:%d" % port,
        }
        _tl.sessions[port] = s
    return _tl.sessions[port]

def _hdrs(ssid):
    return {
        "Cookie":       "__Secure-unitool-ssid=" + ssid,
        "Content-Type": "application/json",
        "User-Agent":   "Mozilla/5.0 (X11; Linux x86_64) Chrome/136",
        "Origin":       "https://unitool.ai",
        "Referer":      "https://unitool.ai/en/chatgpt",
        "Accept":       "application/json",
    }

def _post(path, body, ssid, port):
    ports = [port] + [p for p in ALL_RESI if p != port]
    last_exc = None
    for try_port in ports[:4]:
        try:
            r = _get_sess(try_port).post(BASE + path, json=body, headers=_hdrs(ssid), timeout=20)
            try:
                return r.json()
            except Exception:
                return {"_raw": r.text[:300], "_status": r.status_code}
        except Exception as e:
            last_exc = e
            err_str = str(e)
            if any(k in err_str for k in ("SSL", "EOF", "Connection", "Connect", "socks")):
                if hasattr(_tl, "sessions"):
                    _tl.sessions.pop(try_port, None)
                continue
            raise
    raise last_exc

def _paginated(chat_id, ssid, port):
    ports = [port] + [p for p in ALL_RESI if p != port]
    for try_port in ports[:3]:
        try:
            r = _get_sess(try_port).post(
                "%s/api/chats/%d/messages/paginated" % (BASE, chat_id),
                json={"chat_id": chat_id, "limit": 10, "last_id": 0},
                headers=_hdrs(ssid), timeout=15,
            )
            d = r.json()
            return d.get("messages", []) or d.get("data", [])
        except Exception as e:
            err_str = str(e)
            if any(k in err_str for k in ("SSL", "EOF", "Connection")):
                if hasattr(_tl, "sessions"):
                    _tl.sessions.pop(try_port, None)
                continue
            return []
    return []

def _del(chat_id, ssid, port):
    try:
        _get_sess(port).delete(
            "%s/api/chats/%d" % (BASE, chat_id),
            headers=_hdrs(ssid), timeout=8,
        )
    except Exception:
        pass

# ── error parsers ─────────────────────────────────────────────────────────────

RE_MT     = re.compile(r"max_tokens[:\s]+([0-9,]+)\s*>\s*([0-9,]+)[^\n]*?(?:for\s+([\w\-.]+))?", re.I)
RE_CTX    = re.compile(r"context.{0,20}(?:length|window|limit)[^0-9]*([0-9,]+)", re.I)
RE_404MDL = re.compile(r'"message"\s*:\s*"model:\s*([\w\-\.]+)"', re.I)
RE_MODELN = re.compile(r'"model"\s*:\s*"([\w\-\.]+(?:-\d{8})?)"', re.I)
RE_MDL    = re.compile(r"(?:for|model)\s+([\w][\w\-.]+(?:-\d{8})?)", re.I)

def _parse(text):
    info = {}
    m = RE_MT.search(text)
    if m:
        info["sent"]  = int(m.group(1).replace(",", ""))
        info["limit"] = int(m.group(2).replace(",", ""))
        info["kind"]  = "max_tokens_exceeded"
        if m.group(3):
            info["model"] = m.group(3)
    mc = RE_CTX.search(text)
    if mc:
        info["ctx"] = int(mc.group(1).replace(",", ""))
    if "model" not in info:
        for pat in (RE_404MDL, RE_MODELN, RE_MDL):
            mm = pat.search(text)
            if mm:
                n = mm.group(1)
                if len(n) > 5 and (any(c.isdigit() for c in n) or "-" in n):
                    info["model"] = n
                    break
    lo = text.lower()
    if "maintained" in lo or "maintenance" in lo:
        info["kind"] = "maintenance"
    elif re.search(r"[Uu]nsupported|not.{0,5}(?:found|available|supported)", text):
        info["kind"] = "unsupported"
    elif "balance" in lo or "tokens are over" in lo or "insufficient" in lo:
        info["kind"] = "no_balance"
    elif "kind" not in info:
        info["kind"] = "other_error"
    return info

def _poll(chat_id, ssid, port, secs):
    """Poll paginatedMessages. Mirrors proxy v5.31 any-error catch."""
    deadline = time.time() + secs
    while time.time() < deadline:
        time.sleep(POLL_IV)
        msgs = _paginated(chat_id, ssid, port)
        got_ended = None
        for m in msgs:
            if m.get("role") != "assistant":
                continue
            st = m.get("status", "")
            ct = m.get("content") or ""
            if st == "error" and ct:
                return "err", ct
            if st == "ended":
                got_ended = ct
        if got_ended is not None:
            return "ok", got_ended
        # v5.31 fallback
        for m in msgs:
            if m.get("role") == "assistant" and m.get("status") == "error":
                ct = m.get("content") or ""
                if ct:
                    return "err", ct
    return "timeout", ""

# ── probe core ────────────────────────────────────────────────────────────────

def _do_chat_and_poll(svc, ssid, port, cs_json, opts_json, msg, poll_secs):
    """Create chat, send msg, poll. Returns (phase, status, content, elapsed)."""
    t0 = time.time()
    body = {"service_id": svc, "title": ""}
    if cs_json:
        body["chat_settings"] = cs_json
    chat = _post("/api/chats", body, ssid, port)
    cid  = chat.get("id")
    if not cid:
        return "chat", "dead", str(chat)[:120], time.time()-t0

    mr = _post(
        "/api/chats/%d/messages" % cid,
        {"content": msg, "attachments": [], "options": opts_json or ""},
        ssid, port,
    )
    if mr.get("error"):
        _del(cid, ssid, port)
        return "send", "err", mr["error"][:300], time.time()-t0
    if mr.get("code") == 500:
        _del(cid, ssid, port)
        return "send", "dead", mr.get("msg","backend_500")[:150], time.time()-t0

    status, content = _poll(cid, ssid, port, poll_secs)
    _del(cid, ssid, port)
    return "poll", status, content, time.time()-t0


def probe(svc, ssid, port):
    """Full probe: max_tokens phase → alive phase on timeout. Returns result dict."""
    r = {
        "service": svc, "alive": None, "backend_model": None,
        "max_output": None, "ctx_limit": None, "kind": None, "raw": None,
        "notes": [], "elapsed": 0,
    }
    t_start = time.time()

    if svc in REASONING_SVCS:
        cs   = json.dumps({"reasoning_effort": "high", "max_tokens": PROBE_BIG})
        opts = json.dumps({"reasoning_effort": "high"})
    else:
        cs   = json.dumps({"max_tokens": PROBE_BIG})
        opts = ""

    # Phase 1: max_tokens probe
    phase, status, content, el = _do_chat_and_poll(svc, ssid, port, cs, opts, PROBE_MSG, POLL_SECS)

    if phase in ("chat", "send") and status in ("dead", "err"):
        parsed = _parse(content)
        r.update(
            alive=(parsed.get("kind") not in ("unsupported","no_balance","other_error")),
            kind=parsed.get("kind","error"),
            raw=content[:300],
            backend_model=parsed.get("model"),
            max_output=parsed.get("limit"),
            ctx_limit=parsed.get("ctx"),
        )
        if parsed.get("kind") in ("unsupported", "no_balance"):
            r["alive"] = False
        r["elapsed"] = round(time.time()-t_start, 1)
        return r

    if status == "err":
        parsed = _parse(content)
        r.update(
            alive=True,
            kind=parsed.get("kind","error"),
            raw=content[:400],
            backend_model=parsed.get("model"),
            max_output=parsed.get("limit"),
            ctx_limit=parsed.get("ctx"),
        )
        r["elapsed"] = round(time.time()-t_start, 1)
        return r

    if status == "ok":
        r.update(alive=True, kind="no_limit_enforced",
                 raw="p1 replied OK: " + content[:60])
        r["elapsed"] = round(time.time()-t_start, 1)
        return r

    # Phase 2: alive probe with normal message
    r["notes"].append("p1 timeout → alive_probe")
    if svc in REASONING_SVCS:
        cs2   = json.dumps({"reasoning_effort": "high"})
        opts2 = json.dumps({"reasoning_effort": "high"})
    else:
        cs2   = ""
        opts2 = ""

    phase2, status2, content2, el2 = _do_chat_and_poll(
        svc, ssid, port, cs2, opts2, ALIVE_MSG, ALIVE_SECS
    )

    if phase2 in ("chat", "send") and status2 in ("dead", "err"):
        parsed = _parse(content2)
        r.update(
            alive=False,
            kind=parsed.get("kind","dead_on_alive"),
            raw=("alive_probe: "+content2)[:250],
            backend_model=parsed.get("model"),
        )
    elif status2 == "err":
        parsed = _parse(content2)
        r.update(
            alive=True,
            kind=parsed.get("kind","error_on_alive"),
            raw=content2[:400],
            backend_model=parsed.get("model"),
            max_output=parsed.get("limit"),
            ctx_limit=parsed.get("ctx"),
        )
    elif status2 == "ok":
        r.update(alive=True, kind="alive", raw="alive_probe OK: "+content2[:60])
    else:
        r.update(alive=None, kind="double_timeout")

    r["elapsed"] = round(time.time()-t_start, 1)
    return r

# ── main ──────────────────────────────────────────────────────────────────────

_print_lock = threading.Lock()

def _probe_task(args_tuple):
    idx, svc, ssid, port = args_tuple
    r = probe(svc, ssid, port)
    sym = "OK  " if r["alive"] else ("DEAD" if r["alive"] is False else "??  ")
    mdl = r["backend_model"] or "?"
    mo  = str(r["max_output"]) if r["max_output"] else "-"
    ctx = str(r["ctx_limit"]) if r["ctx_limit"] else "-"
    knd = r["kind"] or ""
    line = ("  -> %-22s [%s] backend=%-38s max_out=%-7s ctx=%-7s [%s]  %.1fs" %
            (svc, sym, mdl, mo, ctx, knd, r["elapsed"]))
    extra = []
    if r.get("raw") and r.get("kind") not in ("no_limit_enforced","alive","alive_no_error"):
        extra.append("         > %s" % r["raw"][:120])
    for n in r.get("notes", []):
        extra.append("         note: %s" % n)
    with _print_lock:
        print(line, flush=True)
        for e in extra:
            print(e, flush=True)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("service", nargs="?", default="")
    ap.add_argument("--workers", type=int, default=CONCURRENCY)
    ap.add_argument("--ssid", default="")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    svcs = [args.service] if args.service else ALL_SERVICES
    n    = len(svcs)

    # Load enough SSIDs to go around (one per worker slot)
    ssids = _load_ssids(max(args.workers, n))
    # Assign SSIDs round-robin per service so concurrent jobs don't share one SSID
    port  = args.port or _pick_port()

    tasks = []
    for idx, svc in enumerate(svcs):
        ssid = args.ssid if args.ssid else ssids[idx % len(ssids)]
        resi = ALL_RESI[(idx + ALL_RESI.index(port)) % len(ALL_RESI)]
        tasks.append((idx, svc, ssid, resi))

    print("[probe v2.0] workers=%d  services=%d  probe_max_tokens=%d" % (args.workers, n, PROBE_BIG))
    print("POLL_SECS=%d  ALIVE_SECS=%d" % (POLL_SECS, ALIVE_SECS))
    print("=" * 90)

    t0 = time.time()
    results_map = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_probe_task, t): t[1] for t in tasks}
        for fut in as_completed(futures):
            svc = futures[fut]
            try:
                results_map[svc] = fut.result()
            except Exception as e:
                results_map[svc] = {"service": svc, "alive": None, "backend_model": None,
                                     "max_output": None, "ctx_limit": None,
                                     "kind": "probe_exception", "raw": str(e)[:200],
                                     "notes": [], "elapsed": 0}
                with _print_lock:
                    print("  !! %s EXCEPTION: %s" % (svc, e), flush=True)

    total = round(time.time() - t0, 1)

    # Print summary in original order
    print("\n" + "=" * 90)
    print("SUMMARY  (total %.1fs)" % total)
    print("%-22s %-42s %8s %8s  %s" % ("Service", "Backend Model", "MaxOut", "CtxWin", "Status"))
    print("-" * 98)
    for svc in svcs:
        r   = results_map.get(svc, {})
        sym = "OK  " if r.get("alive") else ("DEAD" if r.get("alive") is False else "??  ")
        print("%-22s %-42s %8s %8s  [%s] %s" % (
            svc,
            r.get("backend_model") or "?",
            str(r.get("max_output") or "-"),
            str(r.get("ctx_limit") or "-"),
            sym,
            r.get("kind") or "",
        ))

    out = "/tmp/unitool_model_probe_results.json"
    results_list = [results_map.get(s, {"service": s}) for s in svcs]
    json.dump(results_list, open(out, "w"), indent=2, ensure_ascii=False)
    print("\n[probe] %d services in %.1fs  →  %s" % (n, total, out))


if __name__ == "__main__":
    main()
