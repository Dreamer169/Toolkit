#!/usr/bin/env python3
import json, socket, random
from concurrent.futures import ThreadPoolExecutor, as_completed

# Cloudflare anycast IP 段
CF_SUBNETS = [
    "104.21",    # /16
    "104.26",    # /16
    "172.67",    # /16
    "162.159",   # /16
    "198.41.128",# /17
    "188.114.96",# /20
    "188.114.97",
    "188.114.98",
    "188.114.99",
    "141.101.64",
    "141.101.65",
    "108.162.192",
    "108.162.193",
    "108.162.194",
]

def gen_candidates(n=200):
    ips = []
    seen = set()
    for subnet in CF_SUBNETS:
        parts = subnet.split(".")
        for _ in range(20):
            if len(parts) == 2:
                ip = f"{subnet}.{random.randint(0,255)}.{random.randint(1,254)}"
            else:
                ip = f"{subnet}.{random.randint(1,254)}"
            if ip not in seen:
                seen.add(ip)
                ips.append(ip)
    random.shuffle(ips)
    return ips[:n]

def test_ip(ip, port=443, timeout=3):
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.close()
        return ip, True
    except:
        return ip, False

candidates = gen_candidates(200)
print(f"测试 {len(candidates)} 个候选 CF IP (port 443)...", flush=True)

good = []
with ThreadPoolExecutor(max_workers=30) as ex:
    futs = {ex.submit(test_ip, ip): ip for ip in candidates}
    for f in as_completed(futs):
        ip, ok = f.result()
        status = "✅" if ok else "❌"
        print(f"  {status} {ip}", flush=True)
        if ok:
            good.append(ip)

print(f"\n可用: {len(good)} 个")
print("GOOD_IPS:" + json.dumps(good))
