#!/usr/bin/env python3
"""
proxyscrape_manager.py -- Free SOCKS5 proxy fetcher + resi_pool injector

Sources:
  1. proxyscrape.com free API (no key needed)
  2. Validates each proxy via curl probe
  3. Injects live proxies into resi_pool external pool

Usage:
  python3 proxyscrape_manager.py           # fetch + probe + inject
  python3 proxyscrape_manager.py --status  # show pool status
"""
import sys, time, subprocess, json, argparse, datetime, concurrent.futures
sys.path.insert(0, "/root/Toolkit/scripts")

PROBE_TARGET  = "http://www.gstatic.com/generate_204"  # HTTP (not HTTPS) works through more cheap proxies
PROBE_TARGET2 = "http://connectivitycheck.gstatic.com/generate_204"  # fallback
PROBE_TIMEOUT = 3
MAX_INJECT    = 20   # max proxies to add per run
MAX_PROBE_W   = 30   # parallel probe workers
LOG_FILE      = "/tmp/proxyscrape_manager.log"

_lf = open(LOG_FILE, "a", buffering=1)
def log(msg):
    ts = datetime.datetime.now().strftime("[%H:%M:%S]")
    line = f"{ts} {msg}"
    print(line, flush=True)
    _lf.write(line + "\n")

SOURCES = [
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=3000&country=all&simplified=true",
    "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&protocol=socks5&timeout=3000&country=all&simplified=true",
]


def fetch_proxy_list() -> list:
    import urllib.request
    proxies = set()
    for url in SOURCES:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.88"})
            with urllib.request.urlopen(req, timeout=15) as r:
                text = r.read().decode("utf-8", errors="ignore")
            for line in text.splitlines():
                line = line.strip()
                if ":" in line and not line.startswith("#"):
                    proxies.add(line)
            log(f"  source {url[:60]}: +{len(text.splitlines())} entries")
        except Exception as e:
            log(f"  source fetch err: {e}")
    return list(proxies)


def probe_proxy(proxy_str: str) -> bool:
    for target in (PROBE_TARGET, PROBE_TARGET2):
        try:
            p = subprocess.Popen(
                ["curl", "-s", "--max-time", str(PROBE_TIMEOUT),
                 "--proxy", f"socks5h://{proxy_str}",
                 "-o", "/dev/null", "-w", "%{http_code}", target],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, _ = p.communicate(timeout=PROBE_TIMEOUT + 2)
            if out.decode().strip() not in ("", "000"):
                return True
        except Exception:
            pass
    return False


def run(max_inject: int = MAX_INJECT) -> dict:
    log("=" * 50)
    log(f"proxyscrape_manager — fetch + probe + inject (max={max_inject})")

    import resi_pool as rp

    # 1. Fetch
    t0 = time.time()
    raw = fetch_proxy_list()
    log(f"Fetched {len(raw)} candidates in {time.time()-t0:.1f}s")

    if not raw:
        log("ERROR: no proxies fetched")
        return {"ok": False, "injected": 0}

    # 2. Probe in parallel — shuffle + cap candidates to avoid timeout
    import random
    random.shuffle(raw)
    candidates = raw[:min(150, len(raw))]  # max 150 to probe per run
    log(f"Probing {len(candidates)} candidates ({MAX_PROBE_W} workers, timeout={PROBE_TIMEOUT}s)...")
    t1 = time.time()
    good = []
    # Use manual future tracking to cancel early once we have enough
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PROBE_W)
    futs = {ex.submit(probe_proxy, p): p for p in candidates}
    try:
        for fut in concurrent.futures.as_completed(futs, timeout=90):
            if fut.result():
                good.append(futs[fut])
                log(f"  alive: {futs[fut]} ({len(good)}/{max_inject})")
                if len(good) >= max_inject:
                    break
    except concurrent.futures.TimeoutError:
        log("WARN: probe round timed out at 90s")
    finally:
        # Cancel remaining futures and shutdown without waiting
        for f in futs:
            f.cancel()
        ex.shutdown(wait=False)
    log(f"Probe done: {len(good)}/{len(candidates)} alive in {time.time()-t1:.1f}s")

    # 3. Inject into resi_pool
    injected = 0
    for proxy_str in good[:max_inject]:
        host, port_s = proxy_str.rsplit(":", 1)
        if rp.add_external(host, int(port_s), probe=False):
            injected += 1
            log(f"  + injected {proxy_str}")

    log(f"Done: injected {injected} proxies into resi_pool")
    st = rp.status()
    log(f"Pool status: local={st['available_local_count']}, externals={st['externals_available']}/{st['externals_total']}")
    result = {"ok": True, "fetched": len(raw), "alive": len(good), "injected": injected, "pool": st}
    with open("/tmp/proxyscrape_result.json", "w") as f:
        json.dump(result, f, indent=2)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--max", type=int, default=MAX_INJECT)
    args = ap.parse_args()

    if args.status:
        import resi_pool as rp
        rp.startup_check(log)
        print(json.dumps(rp.status(), indent=2))
    else:
        result = run(max_inject=args.max)
        print(json.dumps(result, indent=2))
