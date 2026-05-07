#!/usr/bin/env python3
"""
unitool_ref_cache_refresh.py — 后台定时刷新 ref_code 余额缓存
================================================================
每 25 分钟静默调用 unitool_ref_stats.py（不输出），更新 /tmp/unitool_ref_code_cache.json
被 PM2 以 cron 模式调度，或由 wrapper 循环调用。

Usage:
  python3 unitool_ref_cache_refresh.py [--once]
    --once: 只刷新一次然后退出（供 PM2 cron 使用）
    无参数: 进入循环，每 25 分钟刷新一次
"""
import argparse, subprocess, sys, time, json, os

STATS_SCRIPT = "/root/Toolkit/scripts/unitool_ref_stats.py"
CACHE_FILE   = "/tmp/unitool_ref_code_cache.json"
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
            capture_output=True, text=True, timeout=180
        )
        elapsed = int(time.time() - t0)
        if r.returncode == 0:
            try:
                data = json.loads(r.stdout)
                summary = data.get("summary", {})
                log(f"✅ 刷新完成 ({elapsed}s): {summary.get(with_own_code,0)} 个码, "
                    f"slots={summary.get(available_slots,0)}, "
                    f"earnings=${summary.get(total_earnings,0)}")
            except Exception:
                log(f"✅ 刷新完成 ({elapsed}s), 但解析 JSON 失败")
        else:
            log(f"❌ 刷新失败 ({elapsed}s): {r.stderr[:200]}")
    except subprocess.TimeoutExpired:
        log("❌ 刷新超时 (180s)")
    except Exception as e:
        log(f"❌ 刷新异常: {e}")

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
