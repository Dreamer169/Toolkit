#!/usr/bin/env python3
"""
xray-update-subs.py — 定时从 credentials.json 拉取订阅并更新 xray.json
用法: python3 /root/Toolkit/xray-update-subs.py
可加入 crontab: 0 */6 * * * python3 /root/Toolkit/xray-update-subs.py && pm2 restart xray
"""
import json, urllib.request, yaml, shutil, time, subprocess, sys, os

CFG_PATH  = "/data/Toolkit/xray.json"
CRED_PATH = "/root/Toolkit/credentials.json"
SUB_START = 10950
MAX_NODES = 46

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ClashForWindows/0.20.39"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode()

def parse(content):
    d = yaml.safe_load(content)
    return d.get("proxies", []) if d else []

def clash_to_xray_out(p, tag):
    ptype = p.get("type", "")
    if ptype == "vmess":
        network = p.get("network", "tcp")
        security = "tls" if p.get("tls") else "none"
        stream = {"network": network, "security": security}
        if security == "tls":
            stream["tlsSettings"] = {
                "allowInsecure": bool(p.get("skip-cert-verify", True)),
                "serverName": p.get("sni", p["server"])
            }
        if network == "ws":
            ws_path = p.get("ws-path", p.get("path", "/"))
            ws_headers = p.get("ws-headers", p.get("headers", {}))
            if not ws_headers and p.get("servername"):
                ws_headers = {"Host": p["servername"]}
            stream["wsSettings"] = {"path": ws_path, "headers": ws_headers}
        elif network == "grpc":
            opts = p.get("grpc-opts", {})
            stream["grpcSettings"] = {"serviceName": opts.get("grpc-service-name", "")}
        elif network == "h2":
            opts = p.get("h2-opts", {})
            stream["httpSettings"] = {"path": opts.get("path", "/"), "host": opts.get("host", [])}
        elif network == "tcp" and p.get("http-opts"):
            opts = p["http-opts"]
            stream["tcpSettings"] = {"header": {"type": "http", "request": {"path": opts.get("path", ["/"]), "headers": opts.get("headers", {})}}}
        return {"tag": tag, "protocol": "vmess",
                "settings": {"vnext": [{"address": p["server"], "port": int(p["port"]),
                    "users": [{"id": p["uuid"], "alterId": int(p.get("alterId", 0)),
                               "security": p.get("cipher", "auto")}]}]},
                "streamSettings": stream}
    elif ptype == "trojan":
        return {"tag": tag, "protocol": "trojan",
                "settings": {"servers": [{"address": p["server"], "port": int(p["port"]),
                                          "password": p["password"]}]},
                "streamSettings": {"network": p.get("network", "tcp"), "security": "tls",
                    "tlsSettings": {"allowInsecure": bool(p.get("skip-cert-verify", True)),
                                    "serverName": p.get("sni", p["server"])}}}
    elif ptype == "ss":
        return {"tag": tag, "protocol": "shadowsocks",
                "settings": {"servers": [{"address": p["server"], "port": int(p["port"]),
                    "method": p.get("cipher", "aes-256-gcm"), "password": p["password"]}]}}
    return None

def main():
    print("[update-subs] " + time.strftime("%Y-%m-%d %H:%M:%S"))
    creds = json.load(open(CRED_PATH))
    sub_urls = [s["url"] for s in creds.get("subscriptions", []) if not s.get("disabled")]
    # Also pull from node_subscription if present
    ns = creds.get("proxy_endpoints", {}).get("node_subscription", {})
    if isinstance(ns, dict):
        for u in ns.get("urls", []):
            url = u if isinstance(u, str) else u.get("url", "")
            if url and url not in sub_urls:
                sub_urls.append(url)
    print("[update-subs] subscriptions: " + str(len(sub_urls)))

    all_proxies = []
    for url in sub_urls:
        try:
            proxies = parse(fetch(url))
            valid = [p for p in proxies if p.get("server", "") not in ("", "0.0.0.0")]
            all_proxies.extend(valid)
            print("  " + str(len(valid)) + " nodes from " + url[:55] + "...")
        except Exception as e:
            print("  SKIP " + url[:50] + ": " + str(e))

    all_proxies = all_proxies[:MAX_NODES]
    if not all_proxies:
        print("[update-subs] no valid nodes, abort")
        sys.exit(1)

    bak = CFG_PATH + ".bak.subs." + str(int(time.time()))
    shutil.copy2(CFG_PATH, bak)

    cfg = json.load(open(CFG_PATH))
    cfg["inbounds"]  = [x for x in cfg["inbounds"]  if not x.get("tag","").startswith("sub-in")]
    cfg["outbounds"] = [x for x in cfg["outbounds"] if not x.get("tag","").startswith("sub-out")]
    cfg["routing"]["rules"] = [r for r in cfg["routing"].get("rules", [])
                                if not any(t.startswith("sub-in") for t in r.get("inboundTag", []))]

    for i, p in enumerate(all_proxies):
        in_tag, out_tag, port = f"sub-in-{i}", f"sub-out-{i}", SUB_START + i
        cfg["inbounds"].append({"tag": in_tag, "port": port, "protocol": "socks",
            "listen": "127.0.0.1", "settings": {"auth": "noauth", "udp": True}})
        ob = clash_to_xray_out(p, out_tag)
        if ob:
            cfg["outbounds"].append(ob)
            cfg["routing"]["rules"].insert(0, {"type": "field",
                "inboundTag": [in_tag], "outboundTag": out_tag})

    json.dump(cfg, open(CFG_PATH, "w"), indent=2, ensure_ascii=False)
    print("[update-subs] " + str(len(all_proxies)) + " nodes written, ports " + str(SUB_START) + "~" + str(SUB_START+len(all_proxies)-1))
    subprocess.run(["pm2", "restart", "xray"], check=False)
    print("[update-subs] xray restarted")

if __name__ == "__main__":
    main()
