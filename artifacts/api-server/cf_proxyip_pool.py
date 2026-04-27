#!/usr/bin/env python3
"""
v8.25 ProxyIP 优选器 (with bug fixes)
- 探测 cfnew Worker ProxyIP 池 (cmliu cmliussss.net) 14 个 region 的 A 记录
- 对每个出口 IP 查 ASN（ip-api → 失败回退 cymru DNS）
- 严格白名单制：只有列入 RESIDENTIAL_ASN 的算 clean；datacenter 黑名单大幅扩充
- 评分: clean_ratio = clean / (total - unknown)，clean>=1 即可推荐
- 写入 /tmp/proxyip_pool.json
"""
import json, socket, subprocess, time, sys, os, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

PROXYIP_REGIONS = [
    ("HK",            "ProxyIP.HK.CMLiussss.net"),
    ("US",            "ProxyIP.US.CMLiussss.net"),
    ("SG",            "ProxyIP.SG.CMLiussss.net"),
    ("JP",            "ProxyIP.JP.CMLiussss.net"),
    ("KR",            "ProxyIP.KR.CMLiussss.net"),
    ("DE",            "ProxyIP.DE.CMLiussss.net"),
    ("SE",            "ProxyIP.SE.CMLiussss.net"),
    ("NL",            "ProxyIP.NL.CMLiussss.net"),
    ("FI",            "ProxyIP.FI.CMLiussss.net"),
    ("GB",            "ProxyIP.GB.CMLiussss.net"),
    ("Oracle",        "ProxyIP.Oracle.cmliussss.net"),
    ("DigitalOcean",  "ProxyIP.DigitalOcean.CMLiussss.net"),
    ("Vultr",         "ProxyIP.Vultr.CMLiussss.net"),
    ("Multacom",      "ProxyIP.Multacom.CMLiussss.net"),
]

# 大型云厂 + VPS（reCAPTCHA Enterprise 0.1 score 元凶）
DATACENTER_ASN = {
    14061, 20473, 16509, 14618, 8075, 15169, 396982, 31898, 132203,
    45102, 37963, 16276, 24940, 63949, 36352, 53667, 46844, 23470,
    62240, 51167, 14315, 35540, 136907, 134963, 49981, 20454, 21859,
    8100, 32613, 62567, 206092, 198605, 205041, 34971, 202425, 133752,
    35017, 19905,
    # Bug B 扩充：v8.25 实测发现的小厂 datacenter
    41745,   # Baykov Ilya Sergeevich (RU shell hosting)
    56971,   # CGI GLOBAL LIMITED
    215439,  # Play2go
    210644,  # Aeza International
    200740,  # First Server Ltd
    215540,  # Global Connectivity Solutions
    212706,  # Livi Hosting
    59711,   # HZ Hosting
    8560,    # IONOS SE
    20860,   # Iomart Hosting
    9009,    # M247 Europe
    42708,   # GleSYS AB
    215346,  # Big Data Host
    216154,  # Clodo Cloud
    216071,  # Servers Tech Fzco
    216127,  # International Hosting Company
    25198,   # Interkvm Host SRL
    197540,  # netcup GmbH
    204548,  # Kamatera Inc
    36007,   # Kamatera
    35916,   # Multacom Corporation
    204154,  # First Server Limited
    152900,  # Onidel Pty Ltd
    906,     # DMIT Cloud
    7979,    # Servers.com (SERVERS-COM-AMS)
    209693,  # OC Networks
    9370,    # SAKURA Internet (jp midgrade datacenter)
    18526,   # DDPS Networks
    135377,  # UCloud HK
    139341,  # Aceville Pte (HK)
    38136,   # Akari Networks
    142113,  # eServer
    140227,  # Hong Kong Communications International
    150452,  # LANDUPS LIMITED (HostUS shell)
    63150,   # BAGE CLOUD LLC
    13335,   # Cloudflare (special)
}

# 真住宅 / 国家级电信 ISP — 命中 = 优质
RESIDENTIAL_ASN = {
    9269, 4760, 4515, 4609, 9381, 9304,
    17676, 17511, 4713, 2516, 7506, 9595,
    3786, 4766, 9318,
    9824, 4788, 7713, 7552, 9299,
    3209, 8881, 3320, 13285, 5089, 3215, 12322,
    1257, 3301, 1759, 3216, 1136, 3265,
    7018, 7922, 701, 20057, 22773, 3651,
}

DIG_TIMEOUT = 5
ASN_TIMEOUT = 8


def dns_lookup_a(domain):
    try:
        result = subprocess.run(
            ["dig", "+short", "+time=" + str(DIG_TIMEOUT), "+tries=1", "A", domain],
            capture_output=True, text=True, timeout=DIG_TIMEOUT + 2,
        )
        ips = [
            line.strip() for line in result.stdout.splitlines()
            if line.strip() and all(p.isdigit() for p in line.strip().split("."))
            and len(line.strip().split(".")) == 4
        ]
        return list(dict.fromkeys(ips))
    except Exception:
        return []


def asn_lookup_ipapi(ip):
    """Primary: ip-api.com free 45req/min."""
    try:
        url = f"http://ip-api.com/json/{ip}?fields=status,message,country,countryCode,city,isp,org,as,asname,query"
        req = urllib.request.Request(url, headers={"User-Agent": "cf-proxyip-pool/1.0"})
        with urllib.request.urlopen(req, timeout=ASN_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        if data.get("status") != "success":
            return None
        as_str = data.get("as", "") or ""
        asn = 0
        if as_str.startswith("AS"):
            try:
                asn = int(as_str.split()[0][2:])
            except Exception:
                pass
        return {
            "asn": asn,
            "as_name": data.get("asname", "") or as_str,
            "isp": data.get("isp", ""),
            "org": data.get("org", ""),
            "country": data.get("countryCode", ""),
            "city": data.get("city", ""),
            "src": "ipapi",
        }
    except Exception:
        return None


def asn_lookup_cymru(ip):
    """Bug D fix: Cymru DNS-based ASN lookup. No rate limit, 100% free."""
    try:
        parts = ip.split(".")
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            return None
        rev = ".".join(reversed(parts))
        out = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=1", "TXT", f"{rev}.origin.asn.cymru.com"],
            capture_output=True, text=True, timeout=6,
        ).stdout.strip().strip('"')
        if not out:
            return None
        # "23028 | 1.2.3.0/24 | US | arin | 1995-04-12"
        fields = [f.strip() for f in out.split("|")]
        asn_token = fields[0].split()[0]
        asn = int(asn_token)
        country = fields[2] if len(fields) > 2 else ""
        # AS name lookup
        out2 = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=1", "TXT", f"AS{asn}.asn.cymru.com"],
            capture_output=True, text=True, timeout=6,
        ).stdout.strip().strip('"')
        as_name = ""
        if out2:
            fs = [f.strip() for f in out2.split("|")]
            as_name = fs[-1] if fs else ""
        return {
            "asn": asn,
            "as_name": as_name,
            "isp": as_name,
            "org": as_name,
            "country": country,
            "city": "",
            "src": "cymru",
        }
    except Exception:
        return None


def asn_lookup(ip):
    """Try ip-api first, fall back to cymru DNS if asn=0 / failed."""
    info = asn_lookup_ipapi(ip)
    if info and info.get("asn"):
        return info
    # Bug D fix: fallback
    cymru = asn_lookup_cymru(ip)
    if cymru and cymru.get("asn"):
        return cymru
    # last resort: return whatever ip-api gave (even if asn=0) so we still have country/city
    return info or cymru


def classify(asn):
    if not asn or asn == 0:
        return "unknown"
    if asn in RESIDENTIAL_ASN:
        return "residential"
    if asn in DATACENTER_ASN:
        return "datacenter"
    return "other"


def probe_region(label, domain, ip_limit=8):
    ips = dns_lookup_a(domain)
    if not ips:
        return {
            "region": label, "domain": domain, "dns_ok": False, "ips": [],
            "ip_count": 0, "clean": [], "other": [], "dirty": [], "unknown": [],
            "score": 0, "recommend": False, "asn_breakdown": {},
        }
    ips = ips[:ip_limit]
    enriched = []
    breakdown = {}
    for ip in ips:
        info = asn_lookup(ip)
        cls = classify((info or {}).get("asn", 0))
        enriched.append({"ip": ip, "class": cls, "info": info})
        breakdown[cls] = breakdown.get(cls, 0) + 1
        time.sleep(0.05)

    clean = [e for e in enriched if e["class"] == "residential"]
    other = [e for e in enriched if e["class"] == "other"]
    dirty = [e for e in enriched if e["class"] == "datacenter"]
    unknown = [e for e in enriched if e["class"] == "unknown"]

    # Bug C fix: clean_ratio over known ASNs
    known = len(clean) + len(other) + len(dirty)
    clean_ratio = (len(clean) / known) if known else 0.0
    score = round(100.0 * clean_ratio, 1)
    # bonus for purely clean+other (no datacenter)
    if len(dirty) == 0 and (len(clean) + len(other)) > 0:
        score += 10
    # recommend if at least 1 residential AND not >75% datacenter
    recommend = (len(clean) >= 1) and (len(dirty) <= 0.75 * len(ips))
    return {
        "region": label, "domain": domain, "dns_ok": True,
        "ips": [e["ip"] for e in enriched], "ip_count": len(enriched),
        "clean": clean, "other": other, "dirty": dirty, "unknown": unknown,
        "score": score, "clean_ratio": round(clean_ratio, 3),
        "recommend": recommend, "asn_breakdown": breakdown,
    }


def main():
    print(f"[probe] {len(PROXYIP_REGIONS)} regions × up to 8 IPs each (v8.25 with bug-B/C/D fixes)", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(probe_region, label, dom): label for label, dom in PROXYIP_REGIONS}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            tag = "✅" if r["recommend"] else ("⚠️" if r["dns_ok"] else "❌")
            cd = r["asn_breakdown"]
            print(
                f"  {tag} {r['region']:<14} ips={r['ip_count']:>2} "
                f"residential={cd.get('residential',0)} other={cd.get('other',0)} "
                f"datacenter={cd.get('datacenter',0)} unknown={cd.get('unknown',0)} "
                f"clean_ratio={r['clean_ratio']} score={r['score']}",
                flush=True,
            )

    results.sort(key=lambda r: -r["score"])
    out = {
        "generated_at": int(time.time()), "version": "v8.25",
        "ranked": results,
        "recommended_regions": [r["region"] for r in results if r["recommend"]],
    }
    with open("/tmp/proxyip_pool.json", "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\n[probe] wrote /tmp/proxyip_pool.json", flush=True)
    print(f"[probe] recommended: {out['recommended_regions']}", flush=True)
    print(f"[probe] top 5:", flush=True)
    for r in results[:5]:
        clean_isps = [(e['info']['isp'] if e['info'] else '?') for e in r['clean'][:3]]
        print(f"  {r['region']:<14} score={r['score']:>5} clean_ratio={r['clean_ratio']} clean_isps={clean_isps}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
