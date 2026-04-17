#!/usr/bin/env python3
import sys, json, time, urllib.request, urllib.error, argparse, re

GATEWAY_API = "http://localhost:8080/api/gateway"
T_PROBE = 15
T_TEST  = 30
G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; B = "\033[1m"; X = "\033[0m"

def ok(m):  print(f"{G}[OK]{X}  {m}")
def er(m):  print(f"{R}[ERR]{X} {m}")
def inf(m): print(f"{Y}[..]{X}  {m}")
def hdr(m): print(f"\n{B}{m}{X}")

def normalize(raw):
    raw = raw.strip().rstrip("/")
    if not raw.startswith("http"):
        raw = "https://" + raw
    m = re.match(r"(https?://[^/]+)(/api/gateway)?", raw)
    return (m.group(1) + "/api/gateway") if m else (raw + "/api/gateway")

def http_get(url, t=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "replit-onboard/1.0"})
        with urllib.request.urlopen(req, timeout=t) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}

def http_post(url, data, t=10):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "replit-onboard/1.0"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=t) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}

def step_probe(base_url):
    hdr("1/4  Probing Replit gateway")
    inf("Target: " + base_url)
    t0 = time.time()
    s, b = http_get(base_url + "/v1/info", T_PROBE)
    ms = int((time.time() - t0) * 1000)
    if s == 200 and b.get("service") == "replit-local-gateway":
        ok(f"Probe OK ({ms}ms)  workspace={b.get('workspace','?')}  nodes={b.get('nodes_ready','?')}/{b.get('nodes_total','?')}")
        return b
    inf(f"/v1/info -> HTTP {s}, falling back to /v1/health")
    t0 = time.time()
    s2, b2 = http_get(base_url + "/v1/health", T_PROBE)
    ms2 = int((time.time() - t0) * 1000)
    if s2 == 200 and b2.get("ok"):
        ok(f"Health OK ({ms2}ms)")
        return {"service": "replit-local-gateway", "nodes_ready": b2.get("nodes_ready")}
    er(f"Probe FAILED: /v1/info={s}  /v1/health={s2}")
    if b.get("error"):
        er("Detail: " + b["error"])
    return None

def step_register(base_url, name):
    hdr("2/4  Registering node into gateway")
    endpoint = GATEWAY_API + "/v1/nodes/replit"
    inf("POST " + endpoint)
    s, b = http_post(endpoint, {"url": base_url, "name": name}, t=20)
    if s == 200 and b.get("success"):
        action = b.get("action", "?")
        n = b.get("node", {})
        ok(f"Registered  action={action}  node_id={n.get('id','?')}  priority={n.get('priority','?')}")
        return n
    er(f"Register FAILED HTTP {s}: {b.get('error', json.dumps(b)[:120])}")
    return None

def step_health(node):
    hdr("3/4  Health check (real chat request)")
    inf("Testing via /v1/chat/completions ...")
    t0 = time.time()
    s, b = http_post(
        GATEWAY_API + "/v1/chat/completions",
        {
            "model": "gpt-5-nano",
            "messages": [{"role": "user", "content": "Reply with the single word OK."}],
            "max_completion_tokens": 20,
        },
        t=T_TEST
    )
    ms = int((time.time() - t0) * 1000)
    if s == 200:
        try:    reply = b["choices"][0]["message"]["content"][:80]
        except: reply = "(no content)"
        ok(f"Chat OK ({ms}ms)  reply: {repr(reply)}")
        return True
    er(f"Chat FAILED HTTP {s}: {str(b)[:120]}")
    return False

def step_status(node_id):
    hdr("4/4  Verifying persistence & final status")
    s, b = http_get(GATEWAY_API + "/v1/nodes", 10)
    if s != 200:
        er(f"Cannot fetch node list HTTP {s}")
        return
    nodes = b.get("nodes", [])
    my = next((n for n in nodes if n.get("id") == node_id), None)
    if my:
        st = my.get("status", "down")
        ok(f"Persisted  id={my['id']}  status={st}  url={my.get('baseUrl', my.get('url','?'))}")
    ready = sum(1 for n in nodes if n.get("status") == "ready")
    total = len(nodes)
    print(f"\n{B}{'='*55}{X}")
    print(f"{G}Onboarding complete!{X}  Gateway: {ready}/{total} nodes ready")
    print(f"{B}{'='*55}{X}\n")

def main():
    ap = argparse.ArgumentParser(description="Replit account one-click onboarding")
    ap.add_argument("url",    help="Replit workspace URL (any format)")
    ap.add_argument("--name", default="", help="Node display name (optional)")
    a = ap.parse_args()
    base = normalize(a.url)
    print(f"\n{B}Replit Auto-Onboard{X}  ->  {base}")
    print("-" * 55)
    info = step_probe(base)
    if not info:
        er("Probe failed. Check the URL and that api-server is running.")
        sys.exit(1)
    ws   = info.get("workspace", "")
    name = a.name or f"Replit-{ws or base.split('.')[0][-8:]}"
    node = step_register(base, name)
    if not node:
        er("Registration failed.")
        sys.exit(1)
    healthy = step_health(node)
    step_status(node.get("id", ""))
    if not healthy:
        print(f"{Y}[WARN]{X} Node registered but health check failed (api-server may still be warming up).")
        sys.exit(2)

if __name__ == "__main__":
    main()
