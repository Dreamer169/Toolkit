#!/usr/bin/env python3
"""
unitool_model_probe.py v1.0
通过故意传超大 max_tokens 探查各服务真实模型名和参数限制

原理: unitool 后端将 Anthropic/OpenAI 400 错误原文透传
  "max_tokens: 32768 > 32000, which is the maximum allowed number of output tokens for claude-opus-4-20250514"
  -> backend_model=claude-opus-4-20250514, max_output=32000

用法:
  python3 unitool_model_probe.py              # 探所有服务
  python3 unitool_model_probe.py gpt-5        # 只探单个
  python3 unitool_model_probe.py --confirm    # limit-1 处再确认
"""
import argparse, json, os, re, sys, time
import requests

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

PROBE_BIG = 999999
PROBE_MSG = "Reply only: PROBE_OK"
POLL_SECS = 40
POLL_IV   = 1.5

# ── session / network helpers ─────────────────────────────────────────────────

_sess = {}

def _session(port):
    if port not in _sess:
        s = requests.Session()
        s.proxies = {
            "http":  "socks5h://127.0.0.1:%d" % port,
            "https": "socks5h://127.0.0.1:%d" % port,
        }
        _sess[port] = s
    return _sess[port]

def _pick_ssid():
    files = sorted(f for f in os.listdir(SSID_DIR) if f.endswith(".txt") and "@" in f)
    if not files:
        raise RuntimeError("No SSID files in " + SSID_DIR)
    return open(os.path.join(SSID_DIR, files[0])).read().strip()

def _pick_port():
    for p in ALL_RESI:
        try:
            r = _session(p).get("https://www.google.com/generate_204", timeout=5)
            if r.status_code in (200, 204):
                print("[resi] alive port=%d" % p, flush=True)
                return p
        except Exception:
            pass
    raise RuntimeError("No alive RESI port")

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
    r = _session(port).post(BASE + path, json=body, headers=_hdrs(ssid), timeout=20)
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text[:300], "_status": r.status_code}

def _paginated(chat_id, ssid, port, limit=10):
    try:
        r = _session(port).post(
            "%s/api/chats/%d/messages/paginated" % (BASE, chat_id),
            json={"chat_id": chat_id, "limit": limit, "last_id": 0},
            headers=_hdrs(ssid), timeout=15,
        )
        d = r.json()
        return d.get("messages", []) or d.get("data", [])
    except Exception:
        return []

def _del(chat_id, ssid, port):
    try:
        _session(port).delete(
            "%s/api/chats/%d" % (BASE, chat_id),
            headers=_hdrs(ssid), timeout=8,
        )
    except Exception:
        pass

# ── error parsers ─────────────────────────────────────────────────────────────

RE_MT  = re.compile(
    r"max_tokens[:\s]+([0-9,]+)\s*>\s*([0-9,]+)[^\n]*?(?:for\s+([\w\-.]+))?", re.I
)
RE_CTX = re.compile(r"context.{0,20}(?:length|window|limit)[^0-9]*([0-9,]+)", re.I)
RE_MDL = re.compile(r"(?:for|model)\s+([\w][\w\-.]+(?:-\d{8})?)", re.I)

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
        mm = RE_MDL.search(text)
        if mm:
            n = mm.group(1)
            if len(n) > 5 and any(c.isdigit() for c in n):
                info["model"] = n
    lo = text.lower()
    if "maintained" in lo or "maintenance" in lo:
        info["kind"] = "maintenance"
    elif re.search(r"[Uu]nsupported|not.{0,5}found|not.{0,5}available", text):
        info["kind"] = "unsupported"
    elif "kind" not in info:
        info["kind"] = "other_error"
    return info

# ── poll helper ───────────────────────────────────────────────────────────────

def _poll(chat_id, ssid, port, secs=POLL_SECS):
    deadline = time.time() + secs
    while time.time() < deadline:
        time.sleep(POLL_IV)
        for m in _paginated(chat_id, ssid, port):
            if m.get("role") != "assistant":
                continue
            st = m.get("status", "")
            ct = m.get("content") or ""
            if st == "error" and ct:
                return "err", ct
            if st == "ended":
                return "ok",  ct
    return "timeout", ""

# ── single service probe ──────────────────────────────────────────────────────

def probe(svc, ssid, port, confirm=False):
    r = {
        "service": svc, "alive": None, "backend_model": None,
        "max_output": None, "ctx_limit": None, "kind": None, "raw": None,
    }

    if svc in REASONING_SVCS:
        cs   = json.dumps({"reasoning_effort": "high", "max_tokens": PROBE_BIG})
        opts = json.dumps({"reasoning_effort": "high"})
    else:
        cs   = json.dumps({"max_tokens": PROBE_BIG})
        opts = ""

    # 1. create chat with oversized max_tokens
    chat = _post("/api/chats",
                 {"service_id": svc, "title": "", "chat_settings": cs},
                 ssid, port)
    cid = chat.get("id")
    if not cid:
        r.update(alive=False, kind="chat_create_fail", raw=str(chat)[:150])
        return r

    # 2. send minimal message
    mr = _post(
        "/api/chats/%d/messages" % cid,
        {"content": PROBE_MSG, "attachments": [], "options": opts},
        ssid, port,
    )
    if mr.get("error"):
        parsed = _parse(mr["error"])
        r.update(
            alive=False, kind=parsed.get("kind", "err"),
            raw=mr["error"][:300],
            backend_model=parsed.get("model"),
            max_output=parsed.get("limit"),
            ctx_limit=parsed.get("ctx"),
        )
        _del(cid, ssid, port)
        return r
    if mr.get("code") == 500:
        r.update(alive=False, kind="backend_500", raw=mr.get("msg", "")[:150])
        _del(cid, ssid, port)
        return r

    # 3. poll for error or response
    status, content = _poll(cid, ssid, port)
    _del(cid, ssid, port)

    if status == "err":
        parsed = _parse(content)
        r.update(
            alive=True, kind=parsed.get("kind", "error"), raw=content[:400],
            backend_model=parsed.get("model"),
            max_output=parsed.get("limit"),
            ctx_limit=parsed.get("ctx"),
        )
    elif status == "ok":
        r.update(alive=True, kind="no_limit_enforced",
                 raw="replied OK: " + content[:60])
    else:
        r.update(alive=None, kind="timeout")

    # 4. optional: confirm at limit-1
    if confirm and r.get("max_output"):
        lim = r["max_output"]
        c2  = _post("/api/chats",
                    {"service_id": svc, "title": "",
                     "chat_settings": json.dumps({"max_tokens": lim - 1})},
                    ssid, port)
        cid2 = c2.get("id")
        if cid2:
            _post(
                "/api/chats/%d/messages" % cid2,
                {"content": PROBE_MSG, "attachments": [], "options": ""},
                ssid, port,
            )
            st2, ct2 = _poll(cid2, ssid, port, secs=30)
            _del(cid2, ssid, port)
            if st2 == "ok":
                r["confirm_limit_minus1"] = "OK"
            elif st2 == "err":
                r["confirm_limit_minus1"] = "ERR: " + ct2[:60]
            else:
                r["confirm_limit_minus1"] = "timeout"
    return r

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("service", nargs="?", default="")
    ap.add_argument("--confirm", action="store_true",
                    help="extra probe at max_output-1 to confirm boundary")
    ap.add_argument("--ssid", default="")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    ssid = args.ssid or _pick_ssid()
    port = args.port or _pick_port()
    svcs = [args.service] if args.service else ALL_SERVICES

    print("[probe] ssid=%s...  port=%d  probe_max_tokens=%d" % (ssid[:16], port, PROBE_BIG))
    print("=" * 75)

    results = []
    for svc in svcs:
        print("  -> %-22s" % svc, end=" ", flush=True)
        t0 = time.time()
        r  = probe(svc, ssid, port, confirm=args.confirm)
        el = round(time.time() - t0, 1)
        results.append(r)

        sym = "OK  " if r["alive"] else ("DEAD" if r["alive"] is False else "??  ")
        mdl = r["backend_model"] or "?"
        mo  = str(r["max_output"]) if r["max_output"] else "-"
        ctx = str(r["ctx_limit"]) if r["ctx_limit"] else "-"
        knd = r["kind"] or ""
        print("[%s] backend=%-36s max_out=%-7s ctx=%-7s [%s]  %.1fs" % (sym, mdl, mo, ctx, knd, el))

        if r.get("raw") and r.get("kind") not in ("max_tokens_exceeded", "no_limit_enforced"):
            print("         > %s" % r["raw"][:120])
        if r.get("confirm_limit_minus1"):
            print("         > confirm(limit-1): %s" % r["confirm_limit_minus1"])

    # summary table
    print("\n" + "=" * 75)
    print("SUMMARY")
    print("%-22s %-40s %8s %8s  %s" % ("Service", "Backend Model", "MaxOut", "CtxWin", "Status"))
    print("-" * 92)
    for r in results:
        sym = "OK  " if r["alive"] else ("DEAD" if r["alive"] is False else "??  ")
        print("%-22s %-40s %8s %8s  [%s] %s" % (
            r["service"],
            r["backend_model"] or "?",
            str(r["max_output"] or "-"),
            str(r["ctx_limit"] or "-"),
            sym,
            r["kind"] or "",
        ))

    out = "/tmp/unitool_model_probe_results.json"
    json.dump(results, open(out, "w"), indent=2, ensure_ascii=False)
    print("\n[probe] full results -> %s" % out)


if __name__ == "__main__":
    main()
