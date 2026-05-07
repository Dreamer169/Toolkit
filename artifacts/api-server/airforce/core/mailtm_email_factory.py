#!/usr/bin/env python3
"""
mailtm_email_factory.py — Sandbox-A 零成本替代方案
====================================================
用 mail.tm 免费 API 批量生成真实可用邮箱，推入 VPS 队列。
* 纯 HTTP 调用，无浏览器，无验证码，无短信，无付费
* VPS 直接运行，不需要 sandbox 代理
* api.airforce 注册器不验证邮件，任何 mail.tm 地址均可用

用法:
  python3 /root/AirForce/core/mailtm_email_factory.py [--count N] [--workers W]

环境变量:
  VPS_API   默认 http://45.205.27.69:8084
"""

import argparse, json, secrets, string, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

VPS_API  = "http://45.205.27.69:8084"
MAILTM   = "https://api.mail.tm"
DOMAIN   = "deltajohnsons.com"


def _http(method, url, data=None, token=None, timeout=15):
    body = json.dumps(data).encode() if data else None
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as exc:
        return 0, {"error": str(exc)}


def create_mailtm() -> tuple[str, str]:
    """创建 mail.tm 账号，返回 (address, password)"""
    chars = string.ascii_lowercase + string.digits
    login = "".join(secrets.choice(chars) for _ in range(14))
    addr  = f"{login}@{DOMAIN}"
    pw    = "M@" + secrets.token_hex(10)
    code, body = _http("POST", f"{MAILTM}/accounts", {"address": addr, "password": pw})
    if code not in (200, 201):
        raise RuntimeError(f"mail.tm create failed {code}: {body}")
    return addr, pw


def vps_push(email: str, password: str) -> dict:
    """推送邮件到 VPS 队列"""
    data = json.dumps({
        "email":    email,
        "password": password,
        "platform": "mailtm",
        "sandbox":  "vps-direct",
    }).encode()
    req = urllib.request.Request(
        f"{VPS_API}/emails/push", data=data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def make_one(worker_id: int) -> dict:
    import random
    time.sleep(worker_id * 1.2 + random.uniform(0, 0.3))   # mail.tm rate-limit: 1 req/s per IP
    t0 = time.time()
    try:
        addr, pw = create_mailtm()
        push     = vps_push(addr, pw)
        ok = push.get("ok", False)
        elapsed = round(time.time() - t0, 2)
        return {"status": "ok" if ok else "push_fail",
                "email": addr, "elapsed": elapsed, "vps": push}
    except Exception as e:
        return {"status": "fail", "reason": str(e),
                "elapsed": round(time.time() - t0, 2)}


def check_vps() -> dict:
    try:
        with urllib.request.urlopen(f"{VPS_API}/emails/status", timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="mail.tm email factory — 免费 Sandbox-A 替代")
    parser.add_argument("--count",   type=int, default=5,  help="生成邮箱数量")
    parser.add_argument("--workers", type=int, default=5,  help="并发数 (mail.tm 无限速，可开高)")
    args = parser.parse_args()

    pre = check_vps()
    print(f"[mailtm] VPS 队列状态: available={pre.get('available',0)} claimed={pre.get('claimed',0)}")
    print(f"[mailtm] 开始生成 {args.count} 个 mail.tm 邮箱，并发={args.workers}", flush=True)

    ok = fail = 0
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(make_one, i): i for i in range(1, args.count + 1)}
        for fut in as_completed(futures):
            res     = fut.result()
            total   = ok + fail + 1
            status  = res["status"]
            email   = res.get("email", "-")
            elapsed = res.get("elapsed", 0)
            if status == "ok":
                ok += 1
                depth = res.get("vps", {}).get("queue_depth", "?")
                print(f"  [{total}/{args.count}] OK  {email}  t={elapsed}s  queue={depth}", flush=True)
            else:
                fail += 1
                print(f"  [{total}/{args.count}] FAIL {email}  {res.get('reason',status)}", flush=True)

    elapsed_total = round(time.time() - t_start, 1)
    post = check_vps()
    print(f"\n=== 完成: {ok} 成功 / {fail} 失败 / {args.count} 总计  耗时={elapsed_total}s ===")
    print(f"[mailtm] 队列当前: available={post.get('available',0)}", flush=True)
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
