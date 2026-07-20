#!/usr/bin/env python3
"""
replit_ip_probe.py — 给 broker 调度做 *注册前* IP 评估。

每个 xray sub-node socks 端口跑：
  1. 取出口 IP
  2. 查 ASN / org（ipinfo.io 或 ip-api.com 公开免费 API，不需要 key）
  3. 标记 datacenter / hosting vs residential / mobile / business ISP
  4. 输出按"reCAPTCHA Enterprise 友好度"排序的端口列表

reCAPTCHA Enterprise v3 在 Replit signup 上的实证规律：
  * datacenter ASN (AWS / GCP / Azure / Alibaba / Tencent / Vultr / DigitalOcean / Hostry …)
    → 几乎必判 score < 0.3 → captcha_token_invalid
  * residential / mobile / business ISP (HKBN / Chunghwa / Comcast / Vodafone …)
    → 一般 score ≥ 0.5，能过

使用：
  python3 replit_ip_probe.py                  # 表格 + 推荐
  python3 replit_ip_probe.py --json           # 给 broker 解析的 JSON
  python3 replit_ip_probe.py --pick           # 只输出 1 个最佳 port
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_PORTS = list(range(10808, 10870))
COOLDOWN_FILE = "/root/Toolkit/.local/port_cooldown.json"

# 已知 datacenter / hosting ASN 关键词（命中即降级）
DATACENTER_HINTS = {
    "amazon", "aws", "google cloud", "gcp", "azure", "microsoft", "oracle cloud",
    "alibaba", "tencent", "huawei cloud", "baidu", "ucloud",
    "digitalocean", "vultr", "linode", "hetzner", "ovh", "scaleway",
    "datacamp", "m247", "psychz", "cogent", "leaseweb", "choopa",
    "hostry", "fourplex", "greenhost", "contabo", "interserver",
    "data communications", "datacenter", "hosting", "vpn", "vps",
    "server", "colocation", "cloud", "cdn",
}
RESIDENTIAL_HINTS = {
    # 民用/移动 ISP（命中即加分）
    "comcast", "verizon", "at&t", "spectrum", "charter", "cox",
    "vodafone", "orange", "telekom", "btnet", "sky broadband",
    "chunghwa", "hkbn", "hkt", "pccw", "softbank", "kddi",
    "china telecom", "china unicom", "china mobile",
    "broadband", "cable", "fiber", "ftth", "fttp", "dsl", "adsl",
    "residential", "consumer", "wireless", "mobile",
}


def get_ip(port: int, timeout: float = 4.0) -> str | None:
    try:
        out = subprocess.run(
            ["curl", "-s", f"--max-time", str(timeout),
             "--socks5-hostname", f"127.0.0.1:{port}",
             "https://api.ipify.org"],
            capture_output=True, text=True, timeout=timeout + 1,
        )
        ip = (out.stdout or "").strip()
        return ip if ip and "." in ip else None
    except Exception:
        return None


def lookup_ip(ip: str, timeout: float = 4.0) -> dict:
    # ip-api.com 免费 45 req/min, 不需要 key, 字段全
    url = f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,regionName,city,isp,org,as,asname,mobile,proxy,hosting,query"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"status": "fail", "error": str(e)[:80], "query": ip}


def score(info: dict) -> tuple[int, str]:
    """返回 (score, reason)，score 越高越适合 Replit signup"""
    if info.get("status") != "success":
        return -1, f"lookup_fail:{info.get('error', '?')[:30]}"
    blob = " ".join([
        info.get("isp", ""), info.get("org", ""),
        info.get("as", ""), info.get("asname", ""),
    ]).lower()
    s = 50  # baseline
    hits_dc = [h for h in DATACENTER_HINTS if h in blob]
    hits_res = [h for h in RESIDENTIAL_HINTS if h in blob]
    if info.get("hosting"): s -= 40
    if info.get("proxy"):   s -= 30
    if info.get("mobile"):  s += 25
    if hits_dc:  s -= 20 + 5 * len(hits_dc)
    if hits_res: s += 25 + 5 * len(hits_res)
    s = max(-100, min(100, s))
    tag = "🟢 RES" if s >= 30 else ("🟡 MIX" if s >= 0 else "🔴 DC ")
    reason = f"{tag} dc={hits_dc[:2]} res={hits_res[:2]} hosting={info.get('hosting')} mobile={info.get('mobile')}"
    return s, reason


def load_cooldown() -> dict[int, int]:
    try:
        j = json.load(open(COOLDOWN_FILE))
        return {int(k): int(v) for k, v in (j.get("bans") or {}).items()}
    except Exception:
        return {}


def probe_one(port: int) -> dict:
    ip = get_ip(port)
    if not ip:
        return {"port": port, "ip": None, "status": "DEAD", "score": -100, "reason": "socks dead"}
    info = lookup_ip(ip)
    s, reason = score(info)
    return {
        "port": port, "ip": ip,
        "status": info.get("status"),
        "country": info.get("countryCode"), "city": info.get("city"),
        "isp": info.get("isp"), "asn": info.get("as"),
        "hosting": info.get("hosting"), "mobile": info.get("mobile"), "proxy": info.get("proxy"),
        "score": s, "reason": reason,
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ports", type=str, default=",".join(str(p) for p in DEFAULT_PORTS),
                    help="逗号分隔端口列表")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pick", action="store_true", help="仅输出最高分活端口的 socks5 URL")
    ap.add_argument("--respect-cooldown", action="store_true", help="排除 port_cooldown.json 里在禁的")
    args = ap.parse_args(argv)
    ports = [int(p) for p in args.ports.split(",") if p.strip().isdigit()]
    cd = load_cooldown() if args.respect_cooldown else {}
    now_ms = int(time.time() * 1000)
    if cd:
        before = len(ports)
        ports = [p for p in ports if cd.get(p, 0) <= now_ms]
        if not args.json: print(f"# cooldown filter: {before}→{len(ports)} ports", file=sys.stderr)

    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for r in as_completed([ex.submit(probe_one, p) for p in ports]):
            rows.append(r.result())
    rows.sort(key=lambda r: (-r["score"], r["port"]))

    if args.pick:
        live = [r for r in rows if r["status"] == "success" and r["score"] > 0]
        if not live:
            print("", end=""); sys.exit(2)
        print(f"socks5://127.0.0.1:{live[0]['port']}")
        return 0
    if args.json:
        print(json.dumps(rows, indent=2)); return 0
    print(f"{'port':<6} {'score':>5} {'cc':<3} {'ip':<16} {'isp':<32} reason")
    print("-" * 110)
    for r in rows:
        print(f"{r['port']:<6} {r['score']:>5} {(r.get('country') or '?'):<3} "
              f"{(r.get('ip') or 'DEAD'):<16} {(r.get('isp') or '-')[:32]:<32} {r['reason']}")
    live = [r for r in rows if r["status"] == "success" and r["score"] > 0]
    if live:
        print(f"\n✅ recommend: socks5://127.0.0.1:{live[0]['port']} "
              f"(score={live[0]['score']}, {live[0].get('isp')})")
    else:
        print("\n⚠ no port scores > 0 — Replit reCAPTCHA 必拒, 建议补充住宅/移动 ISP outbound")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
