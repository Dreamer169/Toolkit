#!/usr/bin/env python3
"""
v8.25 把 ProxyIP 注入 xray.json 的 wsSettings.path
- 给所有 protocol=vless 的 outbound 的 wsSettings.path 追加 &p={ProxyIP}&rm=no
- 不覆盖原 path（保留 ed=、其他 query）
- 默认: ProxyIP.HK.CMLiussss.net:443  (实测唯一含 PCCW/Sun Network HK 真住宅 ISP)
- 输出新文件，不直接覆盖原 xray.json
"""
import json, sys, copy, urllib.parse, argparse
from pathlib import Path

DEFAULT_PROXYIP = "ProxyIP.HK.CMLiussss.net:443"
DEFAULT_RM = "no"


def inject_proxyip_to_path(orig_path: str, proxyip: str, rm: str = "no") -> str:
    """Add &p=PROXYIP&rm=no to existing ws path while preserving other params."""
    if not orig_path:
        orig_path = "/"
    if "?" in orig_path:
        base, qs = orig_path.split("?", 1)
    else:
        base, qs = orig_path, ""
    params = urllib.parse.parse_qsl(qs, keep_blank_values=True)
    # remove any existing p / rm
    params = [(k, v) for (k, v) in params if k not in ("p", "rm")]
    params.append(("p", proxyip))
    params.append(("rm", rm))
    return base + "?" + urllib.parse.urlencode(params)


def transform(cfg: dict, proxyip: str, rm: str, port_filter=None):
    """Mutate vless outbounds in-place. Returns (touched, skipped, details)."""
    touched, skipped, details = 0, 0, []
    for ob in cfg.get("outbounds", []):
        if ob.get("protocol") != "vless":
            skipped += 1
            continue
        ss = ob.get("streamSettings") or {}
        if ss.get("network") != "ws":
            skipped += 1
            continue
        ws = ss.get("wsSettings") or {}
        old = ws.get("path", "/")
        # optional inbound→outbound port filter (for canary rollout)
        if port_filter is not None:
            tag = ob.get("tag", "")
            if tag and tag not in port_filter:
                skipped += 1
                continue
        new = inject_proxyip_to_path(old, proxyip, rm)
        if new != old:
            ws["path"] = new
            ss["wsSettings"] = ws
            ob["streamSettings"] = ss
            touched += 1
            details.append({
                "tag": ob.get("tag", ""),
                "addr": ob.get("settings", {}).get("vnext", [{}])[0].get("address", ""),
                "old_path": old,
                "new_path": new,
            })
    return touched, skipped, details


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="input xray.json")
    ap.add_argument("output", help="output xray.json (do NOT overwrite production)")
    ap.add_argument("--proxyip", default=DEFAULT_PROXYIP, help="ProxyIP host:port")
    ap.add_argument("--rm", default=DEFAULT_RM, help='rm value ("no" disables Worker region match)')
    ap.add_argument("--filter", default=None, help="comma-separated outbound tags to touch (default: all vless ws)")
    ap.add_argument("--remap-inbound", default=None, help="if set (e.g. 30000), remap inbound listen ports starting from this base")
    args = ap.parse_args()

    cfg = json.loads(Path(args.input).read_text())
    cfg = copy.deepcopy(cfg)

    pf = set(args.filter.split(",")) if args.filter else None
    touched, skipped, details = transform(cfg, args.proxyip, args.rm, port_filter=pf)

    # optional: remap inbound listen ports to avoid clashing with prod xray
    port_map = {}
    if args.remap_inbound:
        base = int(args.remap_inbound)
        for i, ib in enumerate(cfg.get("inbounds", [])):
            old = ib.get("port")
            new = base + i
            ib["port"] = new
            port_map[old] = new

    Path(args.output).write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    print(f"[apply] in={args.input} out={args.output}")
    print(f"[apply] proxyip={args.proxyip} rm={args.rm}")
    print(f"[apply] touched={touched} vless-ws outbounds, skipped={skipped} non-target")
    if port_map:
        print(f"[apply] remapped inbound ports (old→new): {port_map}")
    for d in details[:5]:
        print(f"[apply]  {d['tag']:<24} {d['addr']:<18} path={d['new_path']}")
    print(f"[apply] (total {len(details)} outbound rewrites; first 5 shown)")


if __name__ == "__main__":
    main()
