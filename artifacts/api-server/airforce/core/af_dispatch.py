#!/usr/bin/env python3
"""
af_dispatch.py — dispatch obvious_airforce_factory_code.py to Replit sandboxes
Uses e2b exec server at https://49999-{full_sb_id}.e2b.app/execute

Sandboxes: us-auto-2..8 (GCP US IPs: 34.x, 35.x, 104.x — Turnstile-friendly)
Usage: python3 af_dispatch.py [--count N] [--parallel P]
"""
import json, os, sys, time, threading, urllib.request, urllib.error
from pathlib import Path

VPS_API      = "http://45.205.27.69:8084"
FACTORY_FILE = Path("/root/AirForce/core/obvious_airforce_factory_code.py")

# Active Replit sandboxes with GCP US IPs (verified exec_ping 2026-05-03)
SANDBOXES = [
    {"label": "us-auto-2", "sb_id": "ic2vee5ef6e1l5sqoopr5"},
    {"label": "us-auto-3", "sb_id": "idzqla0vcz19agiw080s3"},
    {"label": "us-auto-4", "sb_id": "ipo3c7f30n5n5r03w9xlh"},
    {"label": "us-auto-5", "sb_id": "i7gd0aopszkbzuywab2qd"},
    {"label": "us-auto-6", "sb_id": "iy98y90zxasa0s6os1gdt"},
    {"label": "us-auto-8", "sb_id": "iu2qfpvs07mqoqg9t0de7"},
]

def _read_factory() -> str:
    return FACTORY_FILE.read_text()

def email_queue_available() -> int:
    try:
        with urllib.request.urlopen(VPS_API + "/emails/status", timeout=8) as r:
            d = json.loads(r.read())
            return d.get("available", 0)
    except:
        return -1

def push_emails(count=10):
    import subprocess
    r = subprocess.run(
        [sys.executable, "/root/AirForce/core/mailtm_email_factory.py",
         "--count", str(count), "--workers", "3"],
        capture_output=True, text=True, timeout=180
    )
    print("[dispatch] email push:\n" + r.stdout[-400:].strip(), flush=True)

def exec_factory_on_sandbox(sb: dict, timeout: int = 360) -> dict:
    """POST factory code to e2b exec server and parse streaming JSON response."""
    sb_id = sb["sb_id"]
    label = sb["label"]
    url   = "https://49999-" + sb_id + ".e2b.app/execute"

    # Prepend env vars so factory code picks up VPS_API and SANDBOX_LABEL
    env_header = (
        "import os\n"
        "os.environ['VPS_API']       = '" + VPS_API + "'\n"
        "os.environ['SANDBOX_LABEL'] = '" + label + "'\n"
        "os.environ['SOCKS5_RELAY']  = ''\n"
    )
    code = env_header + _read_factory()

    print("[dispatch:" + label + "] sending factory code to exec server...", flush=True)
    t0 = time.time()

    body = json.dumps({"code": code, "language": "python"}).encode("utf-8")
    req  = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return {"success": False, "label": label,
                "error": "HTTP " + str(e.code) + " " + e.reason}
    except Exception as e:
        return {"success": False, "label": label, "error": str(e)[:120]}

    # Parse streaming JSON lines
    stdout_lines = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
            if ev.get("type") == "stdout":
                text = ev.get("text", "")
                stdout_lines.append(text)
                # Print key log lines
                if any(kw in text for kw in ["af-factory", "RESULT:", "SUCCESS", "ERROR",
                                              "error", "dashboard", "TOKEN", "DASHBOARD"]):
                    print("[" + label + "] " + text.rstrip()[:120], flush=True)
        except Exception:
            pass  # non-JSON lines ignored

    # Extract RESULT JSON from stdout
    full_stdout = "".join(stdout_lines)
    elapsed = round(time.time() - t0, 1)
    for line in reversed(full_stdout.splitlines()):
        if line.startswith("RESULT:"):
            try:
                result = json.loads(line[7:])
                result["label"]   = label
                result["elapsed"] = elapsed
                return result
            except Exception:
                pass

    return {"success": False, "label": label, "error": "no RESULT line in output",
            "output_tail": full_stdout[-300:], "elapsed": elapsed}


def dispatch_to_sandbox(sb: dict, count: int = 1) -> list:
    """Run factory on one sandbox `count` times sequentially."""
    results = []
    for attempt in range(count):
        avail = email_queue_available()
        if avail == 0:
            print("[dispatch:" + sb["label"] + "] queue empty, stopping", flush=True)
            break
        print("[dispatch:" + sb["label"] + "] run " + str(attempt+1) + "/" + str(count)
              + "  queue=" + str(avail), flush=True)
        r = exec_factory_on_sandbox(sb)
        results.append(r)
        ok = r.get("success", False)
        if ok:
            print("[dispatch:" + sb["label"] + "] SUCCESS key="
                  + str(r.get("api_key",""))[:30] + "...", flush=True)
        else:
            print("[dispatch:" + sb["label"] + "] FAIL: "
                  + str(r.get("error",""))[:80], flush=True)
    return results


def dispatch_parallel(sandboxes: list, count: int = 1) -> list:
    """Dispatch factory to multiple sandboxes in parallel threads."""
    all_results = []
    lock = threading.Lock()

    def _worker(sb):
        results = dispatch_to_sandbox(sb, count=count)
        with lock:
            all_results.extend(results)

    threads = [threading.Thread(target=_worker, args=(sb,), daemon=True)
               for sb in sandboxes]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=400)

    return all_results


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Dispatch AirForce factory to Replit sandboxes")
    ap.add_argument("--count",       type=int, default=1, help="runs per sandbox")
    ap.add_argument("--parallel",    type=int, default=6, help="number of parallel sandboxes")
    ap.add_argument("--push-emails", type=int, default=15, help="emails to push first")
    ap.add_argument("--no-push",     action="store_true",  help="skip email push")
    args = ap.parse_args()

    avail = email_queue_available()
    print("[dispatch] email queue available=" + str(avail), flush=True)

    if not args.no_push and avail < args.count * args.parallel:
        needed = args.push_emails
        print("[dispatch] pushing " + str(needed) + " emails to queue...", flush=True)
        push_emails(needed)
        time.sleep(3)
        avail = email_queue_available()
        print("[dispatch] queue after push: available=" + str(avail), flush=True)

    sbs = SANDBOXES[:args.parallel]
    print("[dispatch] launching " + str(len(sbs)) + " parallel sandbox workers"
          + "  count=" + str(args.count) + "  queue=" + str(avail), flush=True)
    t0 = time.time()
    results = dispatch_parallel(sbs, count=args.count)
    elapsed = round(time.time() - t0, 1)

    success = [r for r in results if r.get("success")]
    fail    = [r for r in results if not r.get("success")]

    print("\n" + "="*50, flush=True)
    print("DISPATCH COMPLETE  success=" + str(len(success))
          + "  fail=" + str(len(fail)) + "  elapsed=" + str(elapsed) + "s", flush=True)
    for r in success:
        print("  OK " + str(r.get("label")) + "  api_key="
              + str(r.get("api_key",""))[:32] + "...", flush=True)
    for r in fail:
        print("  FAIL " + str(r.get("label")) + "  err="
              + str(r.get("error",""))[:60], flush=True)

    # Final account count
    import sqlite3
    try:
        conn = sqlite3.connect("/root/AirForce/accounts.db")
        total = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        print("\nTotal accounts in DB: " + str(total), flush=True)
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
