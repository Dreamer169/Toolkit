#!/usr/bin/env python3
"""
unitool_token_cache_refresh.py — 定时刷新 token 余额缓存
每 6 小时调 unitool_token_stats.py，更新 /tmp/unitool_token_cache.json

Usage:
  python3 unitool_token_cache_refresh.py [--once]
"""
import argparse, json, subprocess, time

STATS_SCRIPT = "/root/Toolkit/scripts/unitool_token_stats.py"
LOG_FILE     = "/tmp/unitool_token_cache_refresh.log"
INTERVAL     = 6 * 60 * 60   # 6 小时（token 余额变化慢）

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
    log("开始刷新 token 余额缓存...")
    t0 = time.time()
    try:
        r = subprocess.run(
            ["python3", STATS_SCRIPT],
            capture_output=True, text=True, timeout=600
        )
        elapsed = int(time.time() - t0)
        if r.returncode != 0:
            log("❌ 脚本退出码 {} ({}s): {}".format(r.returncode, elapsed, r.stderr.strip()[:200]))
            return
        raw = r.stdout.strip()
        idx = raw.find("{")
        if idx == -1:
            log("❌ 无 JSON 输出 ({}s): {}".format(elapsed, raw[:100]))
            return
        data = json.loads(raw[idx:])
        s = data.get("summary", {})
        log("✅ 刷新完成 ({}s): {} 账号, regular={}, bonus={}, 零余额={}".format(
            elapsed,
            s.get("total_accounts", 0),
            s.get("total_regular", 0),
            s.get("total_bonus", 0),
            s.get("zero_regular", 0),
        ))
    except subprocess.TimeoutExpired:
        log("❌ 超时 (600s)")
    except Exception as e:
        log("❌ 刷新异常 ({}s): {}".format(int(time.time() - t0), e))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只跑一次")
    args = ap.parse_args()
    if args.once:
        do_refresh()
        return
    log("=== unitool_token_cache_refresh 启动 (6h 循环) ===")
    while True:
        do_refresh()
        log("下次刷新在 {} 小时后...".format(INTERVAL // 3600))
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
