#!/usr/bin/env python3
"""
proxy_cf_probe.py v2 — 测试 ProxyScrape 代理能否访问 unitool.ai API（绕过 Cloudflare）
测试逻辑: GET https://unitool.ai/api/ref-codes → HTTP 405 = CF 放行 = 可用于 ref_code 创建
将通过测试的代理写入 /tmp/resi_pool_external.json 并热加载到 resi_pool

用法:
  python3 /data/Toolkit/scripts/proxy_cf_probe.py [--workers N] [--max N] [--no-merge]
"""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
import urllib.request

PROXYSCRAPE_URLS = [
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=3000&country=all&simplified=true",
    "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&protocol=socks5&timeout=3000&country=all&simplified=true",
]

# 用 /api/ref-codes GET：直连返回 405（App 拒绝 GET，CF 放行）
# 代理能拿到 405 = CF 认可该 IP，可用于 POST /api/ref-codes 创建 ref_code
TEST_URL      = "https://unitool.ai/api/ref-codes"
GOOD_CODES    = {"405", "401", "403", "200", "302", "301"}  # 任何 App 响应（非000/000）
PROBE_TIMEOUT = 9
OUTPUT_FILE   = "/tmp/resi_pool_external.json"
LOG_FILE      = "/var/log/proxy_cf_probe.log"


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def fetch_proxyscrape() -> list:
    proxies = set()
    for url in PROXYSCRAPE_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                text = r.read().decode()
            for line in text.strip().splitlines():
                line = line.strip()
                if ":" in line and not line.startswith("#"):
                    proxies.add(line)
            log(f"[fetch] {url} -> {len(proxies)} so far")
        except Exception as e:
            log(f"[fetch] error {url}: {e}")
    return list(proxies)


def test_cf(proxy_str: str) -> tuple:
    """
    Return (proxy_str, ok, http_code).
    ok=True if proxy gets an App-level HTTP response (405/401/403/200 etc.)
    from unitool.ai/api/ref-codes, meaning Cloudflare let the IP through.
    """
    try:
        p = subprocess.Popen(
            ["curl", "-s", "--max-time", str(PROBE_TIMEOUT),
             "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
             "-H", "Accept: application/json",
             "--proxy", f"socks5h://{proxy_str}",
             "-o", "/dev/null", "-w", "%{http_code}",
             TEST_URL],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, _ = p.communicate(timeout=PROBE_TIMEOUT + 2)
        except subprocess.TimeoutExpired:
            try: p.kill(); p.communicate()
            except Exception: pass
            return proxy_str, False, "timeout"
        code = out.decode().strip()
        ok = code in GOOD_CODES
        return proxy_str, ok, code
    except Exception as e:
        return proxy_str, False, f"exc:{e}"


def run(max_workers: int = 80, max_results: int = 100, merge: bool = True):
    log("=== proxy_cf_probe v2 start ===")
    log(f"Test: GET {TEST_URL} → accept codes {GOOD_CODES}")
    t_start = time.time()

    candidates = fetch_proxyscrape()
    log(f"Total candidates: {len(candidates)}")
    if not candidates:
        log("No candidates fetched, aborting")
        return

    good = []
    tested = 0
    code_dist = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(test_cf, p): p for p in candidates}
        for fut in concurrent.futures.as_completed(futs):
            proxy_str, ok, code = fut.result()
            tested += 1
            code_dist[code] = code_dist.get(code, 0) + 1
            if ok:
                good.append(proxy_str)
                log(f"  +++ CF-OK [{code}] {proxy_str}  (total={len(good)})")
            if tested % 200 == 0:
                top_codes = sorted(code_dist.items(), key=lambda x: -x[1])[:5]
                log(f"  progress: {tested}/{len(candidates)} CF-OK={len(good)} "
                    f"elapsed={time.time()-t_start:.0f}s codes={dict(top_codes)}")
            if len(good) >= max_results:
                log(f"Reached max_results={max_results}, stopping early")
                for f2 in futs:
                    f2.cancel()
                break

    elapsed = time.time() - t_start
    top_codes = sorted(code_dist.items(), key=lambda x: -x[1])[:8]
    log(f"Scan done: tested={tested} CF-OK={len(good)} in {elapsed:.0f}s")
    log(f"Code distribution: {dict(top_codes)}")

    if not good:
        log("No CF-passing proxies found — check if unitool.ai is reachable")
        return

    good = good[:max_results]

    # 合并现有文件（保留历史好代理）
    existing = []
    if merge:
        try:
            d = json.loads(open(OUTPUT_FILE).read())
            existing = d.get("proxies", [])
        except Exception:
            pass

    merged = list(dict.fromkeys(good + existing))[:max_results]
    with open(OUTPUT_FILE, "w") as f:
        json.dump({"proxies": merged, "ts": time.time()}, f)
    log(f"Written {len(merged)} proxies to {OUTPUT_FILE} "
        f"(new_cf_ok={len(good)} retained_existing={len(existing)})")

    # 热加载到 resi_pool
    try:
        sys.path.insert(0, "/data/Toolkit/scripts")
        import resi_pool as rp
        added = rp.reload_externals()
        log(f"resi_pool hot-reloaded: +{added} new entries in live pool")
    except Exception as e:
        log(f"reload_externals error: {e}")

    log("=== proxy_cf_probe v2 done ===")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers",  type=int, default=80)
    ap.add_argument("--max",      type=int, default=100)
    ap.add_argument("--no-merge", action="store_true")
    args = ap.parse_args()
    run(max_workers=args.workers, max_results=args.max, merge=not args.no_merge)
