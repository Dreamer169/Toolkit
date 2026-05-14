#!/usr/bin/env python3
"""
auto_ref_replenish.py
自动从 proxyscrape 获取代理 → 测连通 → 获取唯一出口IP → 直接给账号创建 ref_code
不做预先探测（避免浪费出口IP额度）
"""
import subprocess, concurrent.futures, json, time, random, re, logging
import urllib.request, psycopg2
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("replenish")

PROXYSCRAPE_URL = (
    "https://api.proxyscrape.com/v4/free-proxy-list/get"
    "?request=display_proxies&protocol=socks5&timeout=5000&country=all&simplified=true"
)
POOL_FILE = "/tmp/resi_pool_external.json"
DB_URL = "postgresql://postgres:postgres@localhost/toolkit"
UNITOOL_REF_URL = "https://unitool.ai/api/ref-codes"


def fetch_proxyscrape(max_count=5201):
    try:
        req = urllib.request.Request(PROXYSCRAPE_URL, headers={"User-Agent": "curl/7.88"})
        resp = urllib.request.urlopen(req, timeout=20)
        lines = resp.read().decode("utf-8", errors="ignore").strip().splitlines()
        log.info(f"proxyscrape 返回 {len(lines)} 条")
        return [l.strip() for l in lines if ":" in l][:max_count]
    except Exception as e:
        log.error(f"fetch_proxyscrape error: {e}")
        return []


def probe_alive(px, timeout=5):
    try:
        p = subprocess.Popen(
            ["curl", "-s", f"--max-time", str(timeout), "--socks5-hostname", px,
             "-o", "/dev/null", "-w", "%{http_code}",
             "https://www.google.com/generate_204"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = p.communicate(timeout=timeout + 2)
        return px, out.decode().strip() not in ("", "000")
    except:
        return px, False


def get_exit_ip(px, timeout=8):
    try:
        p = subprocess.Popen(
            ["curl", "-s", f"--max-time", str(timeout), "--socks5-hostname", px,
             "https://api.ipify.org"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = p.communicate(timeout=timeout + 2)
        ip = out.decode().strip()
        return px, ip if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip) else ""
    except:
        return px, ""


def create_ref_for_account(acc_id, email, ssid, px, timeout=12):
    """用指定代理给账号创建 ref_code，返回 (status, code/err)"""
    try:
        cmd = ["curl", "-s", f"--max-time", str(timeout),
               "--socks5-hostname", px,
               "-b", f"__Secure-unitool-ssid={ssid}",
               "-X", "POST", "-H", "Content-Type: application/json",
               "-H", "Accept: application/json",
               UNITOOL_REF_URL]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = p.communicate(timeout=timeout + 2)
        raw = out.decode().strip()
        if not raw:
            return "DEAD", ""
        d = json.loads(raw)
        code = d.get("code", "")
        err = d.get("error", "")
        if code:
            return "OK", code
        if err == "ip-already-existed":
            return "USED", ""
        return "ERR", err[:60]
    except Exception as e:
        return "EXC", str(e)[:50]


def save_ref_code(acc_id, ref_code):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT notes, tags FROM accounts WHERE id=%s", (acc_id,))
    row = cur.fetchone()
    notes = (row[0] or "") + f"\nunitool_ref_code={ref_code}"
    tags = row[1] or ""
    if "unitool_ref_activated" not in tags:
        tags = (tags.rstrip(",") + ",unitool_ref_activated").strip(",")
    cur.execute("UPDATE accounts SET notes=%s, tags=%s, updated_at=NOW() WHERE id=%s",
                (notes, tags, acc_id))
    conn.commit()
    conn.close()


def get_accounts_needing_ref(limit=100):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, email, notes FROM accounts
        WHERE platform='outlook' AND status='active'
          AND notes LIKE '%%unitool_ssid=%%'
          AND notes NOT LIKE '%%unitool_ref_code=%%'
        ORDER BY id DESC LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def run(workers_probe=100, workers_exit=40, workers_create=15):
    log.info("=== auto_ref_replenish 开始 ===")

    need_ref = get_accounts_needing_ref(100)
    if not need_ref:
        log.info("没有账号需要 ref_code，退出")
        return 0

    log.info(f"需要 ref_code 的账号: {len(need_ref)} 个")

    # Step 1: 拉代理列表
    proxies = list(set(fetch_proxyscrape()))
    if not proxies:
        log.error("未能获取代理列表")
        return 0
    random.shuffle(proxies)

    # Step 2: 并发测连通性
    log.info(f"测连通性 {len(proxies)} 个 (workers={workers_probe})...")
    alive = []
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers_probe) as ex:
        for px, ok in ex.map(probe_alive, proxies):
            if ok:
                alive.append(px)
    log.info(f"存活: {len(alive)} / {len(proxies)} ({time.time()-t0:.0f}s)")

    if not alive:
        log.error("没有存活代理")
        return 0

    # Step 3: 获取唯一出口IP（每个出口IP只取1个代理入口）
    log.info(f"获取出口IP (workers={workers_exit})...")
    by_exit = defaultdict(list)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers_exit) as ex:
        for px, exit_ip in ex.map(get_exit_ip, alive):
            if exit_ip:
                by_exit[exit_ip].append(px)

    unique_proxies = [entries[0] for entries in by_exit.values()]
    log.info(f"唯一出口IP: {len(unique_proxies)} 个")

    # Step 4: 直接给每个账号分配一个代理创建 ref_code（不提前探测）
    # 如果返回 ip-already-existed，用队列里下一个补充
    available = list(unique_proxies)
    random.shuffle(available)
    px_queue = list(available)  # 工作队列

    created = 0
    skipped = 0
    proxy_idx = 0

    # 准备任务列表：每账号最多尝试3个不同代理
    def worker(args):
        acc_id, email, notes, px_list = args
        m = re.search(r"unitool_ssid=([0-9a-f]{40,})", notes or "")
        if not m:
            return acc_id, email, "NO_SSID", "", ""
        ssid = m.group(1)
        for px in px_list:
            status, val = create_ref_for_account(acc_id, email, ssid, px)
            if status == "OK":
                return acc_id, email, "OK", val, px
            elif status in ("DEAD", "EXC"):
                continue  # 换下一个代理
            else:
                return acc_id, email, status, val, px
        return acc_id, email, "EXHAUSTED", "", ""

    # 分配：每账号给3个候选代理（按顺序不重叠）
    tasks = []
    for i, (acc_id, email, notes) in enumerate(need_ref):
        start = i * 3
        candidates = px_queue[start:start + 3]
        if not candidates:
            log.warning(f"代理不足，跳过 id={acc_id} {email}")
            continue
        tasks.append((acc_id, email, notes, candidates))

    log.info(f"开始创建 ref_code for {len(tasks)} 个账号 (workers={workers_create})...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers_create) as ex:
        for acc_id, email, status, val, px in ex.map(worker, tasks):
            if status == "OK":
                save_ref_code(acc_id, val)
                log.info(f"  CREATED  id={acc_id} {email}  code={val}  via={px}")
                created += 1
            else:
                log.warning(f"  {status:10s}  id={acc_id} {email}  {val}")
                skipped += 1

    log.info(f"=== 完成: 创建={created} 失败={skipped} ===")
    return created


if __name__ == "__main__":
    run()
