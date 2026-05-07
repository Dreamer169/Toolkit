#!/usr/bin/env python3
"""
unitool_ref_cache_refresh.py -- backend timer to refresh ref_code balance cache
Every 25 minutes calls unitool_ref_stats.py via subprocess to update /tmp/unitool_ref_code_cache.json

Usage:
  python3 unitool_ref_cache_refresh.py [--once]
"""
import argparse, json, subprocess, sys, time

STATS_SCRIPT = "/root/Toolkit/scripts/unitool_ref_stats.py"
LOG_FILE     = "/tmp/unitool_ref_cache_refresh.log"
INTERVAL     = 25 * 60   # 25 min

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = "[{}] {}".format(ts, msg)
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def do_refresh():
    log("\u5f00\u59cb\u5237\u65b0 ref_code \u4f59\u989d\u7f13\u5b58...")
    t0 = time.time()
    try:
        r = subprocess.run(
            ["python3", STATS_SCRIPT],
            capture_output=True, text=True, timeout=600
        )
        elapsed = int(time.time() - t0)
        if r.returncode != 0:
            log("\u274c \u811a\u672c\u9000\u51fa\u7801 {} ({}s): {}".format(r.returncode, elapsed, r.stderr.strip()[:200]))
            return
        raw = r.stdout.strip()
        json_start = raw.find("{")
        if json_start == -1:
            log("\u274c \u65e0 JSON \u8f93\u51fa ({}s), stdout={}".format(elapsed, raw[:100]))
            return
        data = json.loads(raw[json_start:])
        summary = data.get("summary", {})
        log("\u2705 \u5237\u65b0\u5b8c\u6210 ({}s): {} \u4e2a\u7801, slots={}, earnings=${}".format(
            elapsed,
            summary.get("with_own_code", 0),
            summary.get("available_slots", 0),
            summary.get("total_earnings", 0)
        ))
    except subprocess.TimeoutExpired:
        log("\u274c \u8d85\u65f6 (600s)")
    except Exception as e:
        elapsed = int(time.time() - t0)
        log("\u274c \u5237\u65b0\u5f02\u5e38 ({}s): {}".format(elapsed, e))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    if args.once:
        do_refresh()
        return

    log("=== unitool_ref_cache_refresh \u542f\u52a8 (25min \u5faa\u73af) ===")
    while True:
        do_refresh()
        log("\u4e0b\u6b21\u5237\u65b0\u5728 {} \u5206\u949f\u540e...".format(INTERVAL // 60))
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
