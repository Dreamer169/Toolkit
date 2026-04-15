#!/usr/bin/env python3
import json, random, subprocess, sys

CFG = '/workspaces/Toolkit/xray.json'

FALLBACK_IPS = [
    "172.67.214.57", "188.114.96.207", "104.21.57.7", "108.162.193.72",
    "104.26.15.24", "172.67.142.208", "188.114.97.138", "108.162.192.26",
    "108.162.194.91", "188.114.98.213", "188.114.97.250", "188.114.98.76",
    "188.114.99.227", "188.114.99.221", "188.114.97.237", "188.114.96.187",
    "108.162.194.172", "188.114.98.161", "172.67.229.151", "188.114.96.12",
    "162.159.142.40", "104.26.13.144", "108.162.192.60", "188.114.96.118",
    "108.162.193.117", "108.162.194.95", "108.162.194.54", "108.162.193.163",
    "108.162.193.214", "188.114.98.142", "104.21.44.152", "108.162.192.219",
    "188.114.96.68", "104.21.114.36", "108.162.192.222", "108.162.194.42",
    "104.21.52.174", "188.114.99.93", "172.67.81.179", "188.114.98.21",
    "188.114.99.148"
]

def get_good_ips():
    """Run cf_ip_expand.py to find live CF IPs dynamically."""
    try:
        result = subprocess.run(
            [sys.executable, '/workspaces/Toolkit/cf_ip_expand.py'],
            capture_output=True, text=True, timeout=60
        )
        for line in result.stdout.splitlines():
            if line.startswith('GOOD_IPS:'):
                ips = json.loads(line[len('GOOD_IPS:'):])
                if ips:
                    return ips
    except Exception as e:
        print(f'动态IP检测失败: {e}，使用备用列表')
    return list(FALLBACK_IPS)

def run(ips=None):
    """Update xray.json with given IP list. If ips is None, probe dynamically."""
    if ips is None:
        ips = get_good_ips()
    good_ips = list(ips)
    random.shuffle(good_ips)

    with open(CFG) as f:
        cfg = json.load(f)

    outbounds = cfg.get('outbounds', [])
    updated = 0
    used_ips = []
    for i, ob in enumerate(outbounds):
        s = ob.get('settings', {})
        if s.get('vnext'):
            new_ip = good_ips[i % len(good_ips)]
            s['vnext'][0]['address'] = new_ip
            used_ips.append(new_ip)
            updated += 1

    cfg['outbounds'] = outbounds
    with open(CFG, 'w') as f:
        json.dump(cfg, f, indent=2)

    unique = len(set(used_ips))
    print(f'已更新 {updated} 个 outbound，使用 {unique} 个唯一 IP')
    print('示例 IP:', used_ips[:8])

if __name__ == '__main__':
    run()
