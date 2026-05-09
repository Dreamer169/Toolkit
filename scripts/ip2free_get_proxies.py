#!/usr/bin/env python3
"""
ip2free 代理提取器 — 从所有已知账号拉取全量 freeList
输出: /tmp/ip2free_proxies.json + 控制台打印 socks5://user:pass@ip:port
"""
import requests, json, urllib3, sys
urllib3.disable_warnings()

BASE_API = "https://api.ip2free.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.6778.85 Safari/537.36"
H_BASE = {
    "User-Agent": UA,
    "Content-Type": "text/plain;charset=UTF-8",
    "Origin": "https://www.ip2free.com",
    "Referer": "https://www.ip2free.com/",
    "lang": "cn", "domain": "www.ip2free.com", "webname": "IP2FREE",
    "affid": "", "invitecode": "", "serviceid": "",
}

# ===== 已知账号列表 =====
ACCOUNTS = [
    {"email": "emily_gomez98@outlook.com", "password": "inAyy$X87Uj^"},
    # 注册成功后追加
]

def login(email, password):
    s = requests.Session()
    s.verify = False
    s.headers.update(H_BASE)
    pl = json.dumps({"email": email, "password": password})
    r = s.post(f"{BASE_API}/api/account/login?", data=pl, timeout=15)
    d = r.json()
    tok = d.get("data", {}).get("token")
    if not tok:
        print(f"  login failed for {email}: {d.get('msg','?')}")
        return None
    s.headers["x-token"] = tok
    return s

def fetch_free_list(s, size=200):
    pl = json.dumps({"size": size})
    r = s.post(f"{BASE_API}/api/ip/freeList?", data=pl, timeout=15)
    d = r.json()
    return d.get("data", {}).get("free_ip_list", [])

def main():
    all_proxies = []
    seen_uids = set()

    for acct in ACCOUNTS:
        email = acct["email"]
        print(f"\n=== {email} ===")
        s = login(email, acct["password"])
        if not s:
            continue
        proxies = fetch_free_list(s)
        print(f"  got {len(proxies)} proxies")
        for p in proxies:
            uid = p.get("proxy_uid", p.get("id",""))
            if uid in seen_uids:
                continue
            seen_uids.add(uid)
            entry = {
                "proxy_uid": uid,
                "ip": p.get("ip",""),
                "port": p.get("port",0),
                "username": p.get("username",""),
                "password": p.get("password",""),
                "protocol": p.get("protocol","socks5"),
                "city": p.get("city",""),
                "country_code": p.get("country_code",""),
                "status": p.get("status",1),
                "is_new": p.get("is_new",0),
                "last_checked_at": p.get("last_checked_at",""),
            }
            all_proxies.append(entry)
            flag = "🆕" if entry["is_new"] else "  "
            url = f"socks5://{entry['username']}:{entry['password']}@{entry['ip']}:{entry['port']}"
            print(f"  {flag} {url}  ({entry['city']},{entry['country_code']}) status={entry['status']}")

    print(f"\n=== 合计 {len(all_proxies)} 个独立代理 ===")

    # 保存
    out = {
        "total": len(all_proxies),
        "proxies": all_proxies,
    }
    with open("/tmp/ip2free_proxies.json", "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("已保存到 /tmp/ip2free_proxies.json")

    # 同时写 txt 格式
    with open("/tmp/ip2free_proxies.txt", "w") as f:
        for p in all_proxies:
            f.write(f"socks5://{p['username']}:{p['password']}@{p['ip']}:{p['port']}\n")
    print("已保存到 /tmp/ip2free_proxies.txt")

if __name__ == "__main__":
    main()
