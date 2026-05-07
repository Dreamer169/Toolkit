#!/usr/bin/env python3
"""
obvious_pipeline.py — 分布式模块化联动注册调度器

链路:
  [沙盒A群 - Email Center]  registers Outlook → POST VPS:8084/emails/push
  [沙盒B群 - AF Registrar]  claims email      → GET  VPS:8084/emails/pop
                             registers af       → POST VPS:8084/accounts/push

沙盒分工由调度器决定:
  - 前 N 个健康沙盒 → email-center 角色 (Outlook 工厂)
  - 其余沙盒        → af-registrar 角色 (api.airforce 注册)

通信: 全部通过 VPS 8084 HTTP API 中继，无 Tailscale，无沙盒间直连。

CLI:
  python3 obvious_pipeline.py --email-workers 2 --reg-workers 4 --target 5
  python3 obvious_pipeline.py --status
  python3 obvious_pipeline.py --test-single --role email    # 单沙盒冒烟测试
  python3 obvious_pipeline.py --test-single --role register
"""
from __future__ import annotations
import argparse, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, "/root/Toolkit/scripts")
sys.path.insert(0, "/root/AirForce")

from obvious_sandbox import ObviousSandbox, DEFAULT_ACC_DIR
from obvious_pool    import ObviousPool

ACC_DIR      = Path("/root/obvious-accounts")
VPS_API      = "http://45.205.27.69:8084"
EMAIL_FACTORY= Path("/root/AirForce/core/obvious_email_factory_code.py").read_text
AF_FACTORY   = Path("/root/AirForce/core/obvious_airforce_factory_code.py").read_text

# ── single sandbox execution ───────────────────────────────────────────────────
def _run_email_worker(label: str) -> dict:
    """Sandbox-A: generate one Outlook account and push to VPS queue."""
    t0 = time.time()
    try:
        sb = ObviousSandbox.from_account_fast(label, acc_dir=ACC_DIR)
        ip = sb.get_public_ip() or "?"
        print(f"[email-center:{label}] ip={ip}", flush=True)
        sb.shell("mkdir -p /home/user/work/shots 2>/dev/null", timeout=8)

        env = f"import os\nos.environ['VPS_API']='http://45.205.27.69:8084'\nos.environ['SANDBOX_LABEL']={label!r}\n"
        out = sb.execute(env + EMAIL_FACTORY(), timeout=210)

        result_line = next((l for l in out.splitlines() if l.startswith("RESULT:")), None)
        if not result_line:
            return {"role":"email","sandbox":label,"ip":ip,"success":False,
                    "error":"no RESULT line","stdout_tail":out[-400:],
                    "elapsed":round(time.time()-t0,1)}
        r = json.loads(result_line[len("RESULT:"):])
        r["role"] = "email"; r["sandbox_ip"] = ip
        r["total_elapsed"] = round(time.time()-t0, 1)
        return r
    except Exception as e:
        return {"role":"email","sandbox":label,"success":False,
                "error":f"{type(e).__name__}:{str(e)[:200]}",
                "elapsed":round(time.time()-t0,1)}

def _run_register_worker(label: str) -> dict:
    """Sandbox-B: claim email from VPS queue, register api.airforce."""
    t0 = time.time()
    try:
        sb = ObviousSandbox.from_account_fast(label, acc_dir=ACC_DIR)
        ip = sb.get_public_ip() or "?"
        print(f"[af-registrar:{label}] ip={ip}", flush=True)
        sb.shell("mkdir -p /home/user/work/shots 2>/dev/null", timeout=8)

        env = f"import os\nos.environ['VPS_API']='http://45.205.27.69:8084'\nos.environ['SANDBOX_LABEL']={label!r}\n"
        out = sb.execute(env + AF_FACTORY(), timeout=210)

        result_line = next((l for l in out.splitlines() if l.startswith("RESULT:")), None)
        if not result_line:
            return {"role":"register","sandbox":label,"ip":ip,"success":False,
                    "error":"no RESULT line","stdout_tail":out[-400:],
                    "elapsed":round(time.time()-t0,1)}
        r = json.loads(result_line[len("RESULT:"):])
        r["role"] = "register"; r["sandbox_ip"] = ip
        r["total_elapsed"] = round(time.time()-t0, 1)
        return r
    except Exception as e:
        return {"role":"register","sandbox":label,"success":False,
                "error":f"{type(e).__name__}:{str(e)[:200]}",
                "elapsed":round(time.time()-t0,1)}

# ── pipeline orchestrator ──────────────────────────────────────────────────────
def run_pipeline(email_workers: int, reg_workers: int, target_accounts: int,
                 min_credits: float = 0.5, progress_cb=None) -> dict:
    """
    Run full pipeline:
      Phase-1: email_workers sandboxes generate Outlook emails (→ VPS queue)
      Phase-2: reg_workers sandboxes register api.airforce with queued emails
    Phases overlap: reg workers start as soon as first email is in queue.
    """
    pool    = ObviousPool(ACC_DIR)
    healthy = pool.healthy(min_credits=min_credits)

    if len(healthy) < email_workers + reg_workers:
        # Not enough sandboxes — reduce workers
        total = len(healthy)
        email_workers = max(1, total // 3)
        reg_workers   = max(1, total - email_workers)
        print(f"[pipeline] only {total} sandboxes, adjusted: "
              f"email={email_workers} reg={reg_workers}", flush=True)

    email_labels = [healthy[i].label for i in range(email_workers)]
    reg_labels   = [healthy[i + email_workers].label
                    for i in range(min(reg_workers, len(healthy)-email_workers))]

    print(f"[pipeline] email-center: {email_labels}", flush=True)
    print(f"[pipeline] af-registrar: {reg_labels}", flush=True)

    results = {"email": [], "register": [], "summary": {}}
    lock    = threading.Lock()

    # Phase-1: launch email workers
    email_futs = {}
    email_ex   = ThreadPoolExecutor(max_workers=email_workers)
    for label in email_labels:
        f = email_ex.submit(_run_email_worker, label)
        email_futs[f] = label

    # Phase-2: launch register workers with slight delay (wait for 1st email)
    import time as _t
    _t.sleep(5)  # give email workers a head start

    reg_futs = {}
    reg_ex   = ThreadPoolExecutor(max_workers=reg_workers)
    # Expand: register target accounts across reg_labels (round-robin)
    for i in range(target_accounts):
        label = reg_labels[i % len(reg_labels)] if reg_labels else email_labels[0]
        f = reg_ex.submit(_run_register_worker, label)
        reg_futs[f] = label

    # Collect email results
    for f in as_completed(email_futs):
        r = f.result()
        with lock: results["email"].append(r)
        if progress_cb: progress_cb(r)
        status = "OK" if r.get("success") else "FAIL"
        print(f"[pipeline] email [{status}] {r.get('sandbox','?')} "
              f"email={r.get('email','-')} t={r.get('total_elapsed','?')}s", flush=True)

    # Collect register results
    for f in as_completed(reg_futs):
        r = f.result()
        with lock: results["register"].append(r)
        if progress_cb: progress_cb(r)
        status = "OK" if r.get("success") else "FAIL"
        key_p = (r.get("api_key") or "")[:22]
        print(f"[pipeline] register [{status}] {r.get('sandbox','?')} "
              f"key={key_p or '-'} t={r.get('total_elapsed','?')}s", flush=True)

    email_ex.shutdown(wait=False)
    reg_ex.shutdown(wait=False)

    e_ok = sum(1 for r in results["email"]    if r.get("success"))
    r_ok = sum(1 for r in results["register"] if r.get("success"))
    results["summary"] = {
        "email_ok":    e_ok,  "email_total":    len(results["email"]),
        "register_ok": r_ok,  "register_total": len(results["register"]),
    }
    print(f"[pipeline] DONE emails={e_ok}/{len(results['email'])} "
          f"registrations={r_ok}/{len(results['register'])}", flush=True)
    return results

# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="obvious 分布式模块化注册链路")
    ap.add_argument("--email-workers",  type=int, default=2,
                    help="并行 Outlook 生成沙盒数 (Sandbox-A 角色)")
    ap.add_argument("--reg-workers",    type=int, default=3,
                    help="并行 api.airforce 注册沙盒数 (Sandbox-B 角色)")
    ap.add_argument("--target",         type=int, default=3,
                    help="目标注册账号数")
    ap.add_argument("--min-credits",    type=float, default=0.5)
    ap.add_argument("--status",         action="store_true",
                    help="查看沙盒池状态 + 邮件队列状态")
    ap.add_argument("--test-single",    action="store_true",
                    help="单沙盒冒烟测试")
    ap.add_argument("--role",           choices=["email","register"], default="email",
                    help="--test-single 时的角色")
    ap.add_argument("--account",        default=None,
                    help="--test-single 时指定沙盒账号 (默认用最高信用)")
    args = ap.parse_args()

    if args.status:
        import urllib.request
        pool = ObviousPool(ACC_DIR)
        pool.refresh_health()
        pool.print_status()
        print()
        try:
            with urllib.request.urlopen(f"{VPS_API}/emails/status", timeout=5) as r:
                print("Email queue:", json.loads(r.read()))
        except Exception as e:
            print("Email queue API error:", e)
        try:
            with urllib.request.urlopen(f"{VPS_API}/stats", timeout=5) as r:
                print("AF accounts:", json.loads(r.read()))
        except Exception as e:
            print("AF stats error:", e)
        return

    if args.test_single:
        pool    = ObviousPool(ACC_DIR)
        healthy = pool.healthy(min_credits=args.min_credits)
        if not healthy:
            print("No healthy sandboxes"); return
        label = args.account or healthy[0].label
        print(f"[test] role={args.role} sandbox={label}")
        if args.role == "email":
            r = _run_email_worker(label)
        else:
            r = _run_register_worker(label)
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return

    results = run_pipeline(
        email_workers=args.email_workers,
        reg_workers=args.reg_workers,
        target_accounts=args.target,
        min_credits=args.min_credits,
    )
    print(json.dumps(results["summary"], indent=2))

if __name__ == "__main__":
    main()
