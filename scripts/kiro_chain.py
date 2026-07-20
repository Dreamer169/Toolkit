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
import sys, os, time, json, random, subprocess, signal, threading, socket
sys.path.insert(0, "/root/Toolkit/artifacts/api-server")

# 添加 scripts 目录到 sys.path 以便 import proxy_manager
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
# data/Toolkit/scripts 也加上 (兼容 /root/ 和 /data/ 两种路径)
_DATA_SCRIPTS = "/data/Toolkit/scripts"
if _DATA_SCRIPTS not in sys.path:
    sys.path.insert(0, _DATA_SCRIPTS)

DATABASE_URL    = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")
CONCURRENCY     = int(os.environ.get("KIRO_CONCURRENCY", "1"))
INTERVAL        = int(os.environ.get("KIRO_INTERVAL", "60"))
MAX_PER_RUN     = int(os.environ.get("KIRO_MAX_PER_RUN", "0"))
# 代理端口配置 (fallback, 当 proxy_manager 无可用代理时使用)
PROXY_PORTS_RAW = os.environ.get("KIRO_PROXY_PORTS", "10910,10911,10912,10916,10854,10857,10851,10852,10853,10855,10856,10858,10859")
PROXY_PORTS     = [int(p.strip()) for p in PROXY_PORTS_RAW.split(",") if p.strip()]
SCRIPT_PATH     = "/root/Toolkit/artifacts/api-server/kiro_register.py"

# nestingproxy_bridge HTTP 代理端口 (CF Worker + RESI SOCKS5)
NEST_BRIDGE_PORT = int(os.environ.get("NEST_BRIDGE_PORT", "5559"))

_stop_flag = threading.Event()
_current_proc: subprocess.Popen | None = None
_current_proc_lock = threading.Lock()
_pm_instance = None
_pm_lock = threading.Lock()

def _get_pm():
    """获取 proxy_manager 实例 (懒加载，失败时返回 None)"""
    global _pm_instance
    if _pm_instance is not None:
        return _pm_instance
    with _pm_lock:
        if _pm_instance is not None:
            return _pm_instance
        try:
            from proxy_manager import ProxyManager
            pm = ProxyManager()
            _pm_instance = pm
            alive_count = sum(1 for e in pm.db._data.values() if e.alive is True)
            print(f"[proxy_manager] 加载成功, alive={alive_count}", flush=True)
            return pm
        except Exception as e:
            print(f"[proxy_manager] 加载失败: {e}, 使用 fallback 端口", flush=True)
            return None

def _nest_alive() -> bool:
    """检查 nestingproxy_bridge 是否在监听"""
    try:
        s = socket.create_connection(("127.0.0.1", NEST_BRIDGE_PORT), timeout=2)
        s.close()
        return True
    except Exception:
        return False

# 偏好的出口国家
PREFERRED_COUNTRIES = {"US", "JP", "KR", "HK", "SG"}
_port_country_cache: dict = {}

def _check_port_country(port: int) -> str:
    if port in _port_country_cache:
        return _port_country_cache[port]
    try:
        from curl_cffi import requests as _cr
        s = _cr.Session(impersonate="chrome131")
        s.proxies = {"http": f"socks5://127.0.0.1:{port}",
                     "https": f"socks5://127.0.0.1:{port}"}
        r = s.get("https://ipinfo.io/country", timeout=6)
        country = r.text.strip().upper()
        _port_country_cache[port] = country
        return country
    except Exception:
        _port_country_cache[port] = ""
        return ""

def _fallback_proxy_url() -> str:
    """从本地 xray 端口选一个 (geo 偏好 + 随机)"""
    ports = list(PROXY_PORTS)
    random.shuffle(ports)
    for p in ports:
        c = _check_port_country(p)
        if c in PREFERRED_COUNTRIES:
            return f"socks5://127.0.0.1:{p}"
    p = random.choice(PROXY_PORTS) if PROXY_PORTS else 10854
    return f"socks5://127.0.0.1:{p}"

_last_pm_uid: str = ""

def get_proxy_url() -> tuple[str, str | None]:
    """
    返回 (proxy_url, pm_uid_or_None).
    优先级:
      1. proxy_manager 池 (proxyscrape/ip2free/local_xray alive 代理)
      2. nestingproxy_bridge :5559 (CF Worker + RESI, 仅 HTTP)
      3. 本地 xray 端口 (fallback)
    """
    # 1. proxy_manager
    pm = _get_pm()
    if pm is not None:
        try:
            entry = pm.pick_for("kiro", probe_if_unknown=True)
            if entry is not None:
                url = entry.socks5h_url if not entry.is_local() else f"socks5h://127.0.0.1:{entry.port}"
                # socks5h → socks5 (curl_cffi 更兼容)
                url = url.replace("socks5h://", "socks5://")
                print(f"[proxy] pm={entry.source} country={entry.country or '?'} alive={entry.alive} → {url[:50]}", flush=True)
                return url, entry.uid
        except Exception as e:
            print(f"[proxy] pm.pick_for 异常: {e}", flush=True)

    # 2. nestingproxy_bridge (HTTP/CONNECT → CF Worker + RESI)
    if _nest_alive():
        url = f"http://127.0.0.1:{NEST_BRIDGE_PORT}"
        print(f"[proxy] nestingproxy_bridge → {url}", flush=True)
        return url, None

    # 3. 本地 xray 端口 fallback
    url = _fallback_proxy_url()
    print(f"[proxy] fallback xray → {url}", flush=True)
    return url, None

def _sig_handler(sig, _):
    print(f"\n[kiro_chain] 收到信号 {sig}, 正在优雅退出...", flush=True)
    with _current_proc_lock:
        if _current_proc is not None:
            try:
                _current_proc.kill()
            except Exception:
                pass
    _stop_flag.set()

signal.signal(signal.SIGTERM, _sig_handler)
signal.signal(signal.SIGINT, _sig_handler)

def ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def count_available():
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

def run_one_register(proxy_url: str) -> bool:
    """在子进程中注册一个账号, 返回 True=成功"""
    global _current_proc
    cmd = [
        "python3", SCRIPT_PATH,
        "--auto",
        "--proxy", proxy_url,
    ]
    env = {**os.environ, "DATABASE_URL": DATABASE_URL, "PYTHONUNBUFFERED": "1"}
    try:
        with _current_proc_lock:
            if _stop_flag.is_set():
                return False
            proc = subprocess.Popen(cmd, env=env, text=True)
            _current_proc = proc
        try:
            deadline = time.time() + 300
            while time.time() < deadline:
                try:
                    proc.wait(timeout=2)
                    break
                except subprocess.TimeoutExpired:
                    if _stop_flag.is_set():
                        proc.kill()
                        proc.wait()
                        print(f"[kiro_chain] 🛑 子进程已强制终止", flush=True)
                        return False
            else:
                proc.kill()
                proc.wait()
                print(f"[kiro_chain] ⏱️ 注册超时 (300s)", flush=True)
                return False
        finally:
            with _current_proc_lock:
                _current_proc = None
        return proc.returncode == 0
    except Exception as e:
        print(f"[kiro_chain] ❌ 子进程异常: {e}", flush=True)
        return False

def main():
    print(f"[{ts()}] kiro_chain 启动 (proxy_manager 模式)", flush=True)
    print(f"  并发={CONCURRENCY} 间隔={INTERVAL}s", flush=True)
    print(f"  fallback端口={PROXY_PORTS}", flush=True)
    print(f"  nestingproxy={NEST_BRIDGE_PORT}", flush=True)

    # 预加载 proxy_manager
    pm = _get_pm()
    if pm:
        alive = sum(1 for e in pm.db._data.values() if e.alive is True)
        print(f"  proxy_manager alive={alive}", flush=True)
    else:
        print(f"  proxy_manager: 不可用, 使用 xray 端口 fallback", flush=True)

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

        proxy_url, pm_uid = get_proxy_url()
        print(f"[{ts()}] 开始注册 (proxy={proxy_url[:60]})...", flush=True)

        ok = run_one_register(proxy_url)
        done += 1

        # 向 proxy_manager 反馈结果
        if pm_uid and pm is not None:
            try:
                outcome = "success" if ok else "fail"
                pm.report_use(pm_uid, platform="kiro", outcome=outcome)
            except Exception:
                pass

        if ok:
            consecutive_fail = 0
            print(f"[{ts()}] ✅ 成功 (累计={done})", flush=True)
            wait = INTERVAL
            if done % 10 == 0:
                try:
                    subprocess.run(["bash", "/data/Toolkit/scripts/sync_kiro_creds.sh"],
                                   timeout=60, capture_output=True)
                    print(f"[{ts()}] 🔄 kiro-rs credentials 已同步", flush=True)
                except Exception as e:
                    print(f"[{ts()}] ⚠️ sync 失败: {e}", flush=True)
        else:
            consecutive_fail += 1
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
