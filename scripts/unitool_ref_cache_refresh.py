#!/usr/bin/env python3
"""
unitool_ref_cache_refresh.py — 后台定时刷新 ref_code 余额缓存
================================================================
每 25 分钟用 subprocess 调用 unitool_ref_stats.py --refresh
更新 /tmp/unitool_ref_code_cache.json

Usage:
  python3 unitool_ref_cache_refresh.py [--once]
"""
import argparse, json, subprocess, sys, time

STATS_SCRIPT = "/root/Toolkit/scripts/unitool_ref_stats.py"
LOG_FILE     = "/tmp/unitool_ref_cache_refresh.log"
INTERVAL     = 25 * 60   # 25 min

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def do_refresh():
    log("开始刷新 ref_code 余额缓存...")
    t0 = time.time()
    try:
        r = subprocess.run(
            ["python3", STATS_SCRIPT, "--refresh"],
            capture_output=True, text=True, timeout=300
        )
        elapsed = int(time.time() - t0)
        if r.returncode != 0:
            log(f"❌ 脚本退出码 {r.returncode} ({elapsed}s): {r.stderr.strip()[:200]}")
            return
        raw = r.stdout.strip()
        # 找到第一个 { 开始的 JSON（防止脚本有非 JSON 前置输出）
        json_start = raw.find("{")
        if json_start == -1:
            log(f"❌ 无 JSON 输出 ({elapsed}s), stdout={raw[:100]}")
            return
        data = json.loads(raw[json_start:])
        summary = data.get("summary", {})
        log(f"✅ 刷新完成 ({elapsed}s): {summary.get(with_own_code, 0)} 个码, "
            f"slots={summary.get(available_slots, 0)}, "
            f"earnings=${summary.get(total_earnings, 0)}")
    except subprocess.TimeoutExpired:
        log(f"❌ 超时 (300s)")
    except Exception as e:
        elapsed = int(time.time() - t0)
        log(f"❌ 刷新异常 ({elapsed}s): {e}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只刷新一次后退出")
    args = ap.parse_args()

    if args.once:
        do_refresh()
        return

    log("=== unitool_ref_cache_refresh 启动 (25min 循环) ===")
    while True:
        do_refresh()
        log(f"下次刷新在 {INTERVAL//60} 分钟后...")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
