#!/usr/bin/env python3
"""
unitool_ref_cache_refresh.py — 后台定时刷新 ref_code 余额缓存
================================================================
每 25 分钟直接 import unitool_ref_stats.main() 更新 /tmp/unitool_ref_code_cache.json
被 PM2 以循环模式调度。

Usage:
  python3 unitool_ref_cache_refresh.py [--once]
    --once: 只刷新一次然后退出
"""
import argparse, json, os, sys, time

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
        # 直接 exec 脚本内容，捕获 sys.argv
        old_argv = sys.argv[:]
        sys.argv = [STATS_SCRIPT, "--refresh"]
        
        # 捕获 print 到 stdout
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        
        script_globals = {"__name__": "__main__", "__file__": STATS_SCRIPT}
        with open(STATS_SCRIPT) as f:
            code = compile(f.read(), STATS_SCRIPT, "exec")
        exec(code, script_globals)
        
        captured = sys.stdout.getvalue()
        sys.stdout = old_stdout
        sys.argv = old_argv
        
        elapsed = int(time.time() - t0)
        data = json.loads(captured)
        summary = data.get("summary", {})
        log(f"✅ 刷新完成 ({elapsed}s): {summary.get('with_own_code',0)} 个码, "
            f"slots={summary.get('available_slots',0)}, "
            f"earnings=${summary.get('total_earnings',0)}")
    except Exception as e:
        sys.stdout = old_stdout if "old_stdout" in dir() else sys.stdout
        sys.argv = old_argv if "old_argv" in dir() else sys.argv
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
