#!/usr/bin/env python3
"""
kiro_chain.py — Kiro 账号批量注册流水线
类似 unitool_chain_v3.py, 持续从 Outlook 池取账号注册 Kiro

环境变量:
  DATABASE_URL       — PostgreSQL 连接串
  KIRO_CONCURRENCY   — 并发注册数 (默认 1, 内存有限)
  KIRO_INTERVAL      — 两次注册间隔秒数 (默认 60)
  KIRO_MAX_PER_RUN   — 单次启动最多注册数量 (0=无限, 默认 0)
  KIRO_PROXY_PORTS   — 逗号分隔代理端口 (默认 10854,10857,10859)
  DISPLAY            — Xvfb 显示 (虽然纯协议不需要, 保留兼容)
"""
import sys, os, time, json, random, subprocess, signal, threading
sys.path.insert(0, "/root/Toolkit/artifacts/api-server")

DATABASE_URL    = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")
CONCURRENCY     = int(os.environ.get("KIRO_CONCURRENCY", "1"))
INTERVAL        = int(os.environ.get("KIRO_INTERVAL", "60"))
MAX_PER_RUN     = int(os.environ.get("KIRO_MAX_PER_RUN", "0"))
# US: 10910/11/12/16, HK: 10854, others available
PROXY_PORTS_RAW = os.environ.get("KIRO_PROXY_PORTS", "10910,10911,10912,10916,10854")
PROXY_PORTS     = [int(p.strip()) for p in PROXY_PORTS_RAW.split(",") if p.strip()]
SCRIPT_PATH     = "/root/Toolkit/artifacts/api-server/kiro_register.py"

_stop_flag = threading.Event()

def _sig_handler(sig, _):
    print(f"\n[kiro_chain] 收到信号 {sig}, 正在优雅退出...", flush=True)
    _stop_flag.set()

signal.signal(signal.SIGTERM, _sig_handler)
signal.signal(signal.SIGINT, _sig_handler)

def ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def count_available():
    """统计 DB 中可用 Outlook 账号数"""
    import psycopg2
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM accounts
            WHERE platform='outlook' AND status='active'
              AND refresh_token IS NOT NULL
              AND (kiro_used IS NULL OR kiro_used=false)
        """)
        n = cur.fetchone()[0]
        cur.close(); conn.close()
        return n
    except Exception as e:
        print(f"[kiro_chain] DB 统计失败: {e}", flush=True)
        return -1

def count_kiro():
    """统计已注册 Kiro 账号数"""
    import psycopg2
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM accounts WHERE platform='kiro' AND status='active'")
        n = cur.fetchone()[0]
        cur.close(); conn.close()
        return n
    except:
        return -1

def run_one_register(proxy_port: int) -> bool:
    """在子进程中注册一个账号, 返回 True=成功"""
    proxy = f"socks5://127.0.0.1:{proxy_port}"
    cmd = [
        "python3", SCRIPT_PATH,
        "--auto",
        "--proxy", proxy,
    ]
    env = {**os.environ, "DATABASE_URL": DATABASE_URL, "PYTHONUNBUFFERED": "1"}
    try:
        result = subprocess.run(cmd, env=env, timeout=300,
                                capture_output=False, text=True)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"[kiro_chain] ⏱️ 注册超时 (300s)", flush=True)
        return False
    except Exception as e:
        print(f"[kiro_chain] ❌ 子进程异常: {e}", flush=True)
        return False

def main():
    print(f"[{ts()}] kiro_chain 启动", flush=True)
    print(f"  并发={CONCURRENCY} 间隔={INTERVAL}s 代理端口={PROXY_PORTS}", flush=True)

    done = 0
    consecutive_fail = 0

    while not _stop_flag.is_set():
        avail = count_available()
        kiro_total = count_kiro()
        print(f"\n[{ts()}] 可用 Outlook: {avail}  已注册 Kiro: {kiro_total}  本轮完成: {done}",
              flush=True)

        if avail == 0:
            print(f"[{ts()}] 没有可用 Outlook 账号, 等待 5 分钟...", flush=True)
            _stop_flag.wait(300)
            continue

        if MAX_PER_RUN > 0 and done >= MAX_PER_RUN:
            print(f"[{ts()}] 已达本次上限 {MAX_PER_RUN}, 退出", flush=True)
            break

        port = random.choice(PROXY_PORTS)
        print(f"[{ts()}] 开始注册 (proxy=:{port})...", flush=True)

        ok = run_one_register(port)
        done += 1

        if ok:
            consecutive_fail = 0
            print(f"[{ts()}] ✅ 成功 (累计={done})", flush=True)
            wait = INTERVAL
            # 每 10 次成功同步一次 kiro-rs credentials.json
            if done % 10 == 0:
                try:
                    subprocess.run(["bash", "/data/Toolkit/scripts/sync_kiro_creds.sh"],
                                   timeout=60, capture_output=True)
                    print(f"[{ts()}] 🔄 kiro-rs credentials 已同步", flush=True)
                except Exception as e:
                    print(f"[{ts()}] ⚠️ sync 失败: {e}", flush=True)
        else:
            consecutive_fail += 1
            # 指数退避: 连续失败时等待更久
            wait = min(INTERVAL * (2 ** min(consecutive_fail - 1, 4)), 600)
            print(f"[{ts()}] ❌ 失败 (连续={consecutive_fail}, 等待={wait}s)", flush=True)

        if consecutive_fail >= 10:
            print(f"[{ts()}] 连续失败 10 次, 暂停 30 分钟", flush=True)
            _stop_flag.wait(1800)
            consecutive_fail = 0

        _stop_flag.wait(wait)

    print(f"[{ts()}] kiro_chain 退出, 共完成 {done} 次注册", flush=True)

if __name__ == "__main__":
    main()
