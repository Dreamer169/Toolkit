#!/usr/bin/env python3
"""
quarkip_ref_create.py
用 QuarkIP 住宅代理（HTTP CONNECT，支持 HTTPS）给账号创建 ref_code
每次尝试后切换 IP，彻底绕过 unitool.ai 的 ip-already-existed 限制

用法:
  python3 quarkip_ref_create.py              # 处理所有缺少 ref_code 的账号
  python3 quarkip_ref_create.py --limit 10  # 最多处理10个
  python3 quarkip_ref_create.py --test       # 仅测试代理和切换IP

配置:
  主机: pool-us.quarkip.io  端口: 7777
  账号: j4eOruul5w  密码: A1enIA12wwBGSKB
  切换IP: http://change.quarkip.io?username=j4eOruul5w&password=A1enIA12wwBGSKB
"""
import argparse, json, re, subprocess, sys, time, logging
import urllib.request
import psycopg2

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('quarkip_ref')

# === QuarkIP 配置 ===
QUARK_PROXY = 'http://j4eOruul5w:A1enIA12wwBGSKB@pool-us.quarkip.io:7777'
QUARK_CHANGE_IP_URL = 'http://change.quarkip.io?username=j4eOruul5w&password=A1enIA12wwBGSKB'

DB_URL = 'postgresql://postgres:postgres@localhost/toolkit'
UNITOOL_REF_URL = 'https://unitool.ai/api/ref-codes'
AUTH_COOKIE = '__Secure-unitool-ssid'


def change_ip(wait=3):
    """触发 QuarkIP 切换出口IP，等待生效"""
    try:
        req = urllib.request.Request(QUARK_CHANGE_IP_URL, headers={'User-Agent': 'curl/7.88'})
        urllib.request.urlopen(req, timeout=6)
        log.info(f'[IP切换] 已触发，等待 {wait}s 生效...')
        time.sleep(wait)
        return True
    except Exception as e:
        log.warning(f'[IP切换] 失败: {e}')
        return False


def get_current_ip(timeout=12):
    """通过 QuarkIP 代理获取当前出口IP"""
    try:
        cmd = ['curl', '-s', '--max-time', str(timeout),
               '--proxy', QUARK_PROXY,
               'https://api.ipify.org']
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+2)
        ip = r.stdout.strip()
        if re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
            return ip
        return None
    except Exception as e:
        log.warning(f'[get_ip] error: {e}')
        return None


def create_ref_code(ssid, timeout=20):
    """用 QuarkIP 代理调用 POST /api/ref-codes，返回 (status, value)"""
    try:
        cmd = ['curl', '-s', '--max-time', str(timeout),
               '--proxy', QUARK_PROXY,
               '-b', f'{AUTH_COOKIE}={ssid}',
               '-X', 'POST',
               '-H', 'Content-Type: application/json',
               '-H', 'Accept: application/json',
               UNITOOL_REF_URL]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+3)
        raw = r.stdout.strip()
        if not raw:
            return 'DEAD', 'empty response'
        d = json.loads(raw)
        code = d.get('code', '')
        err = d.get('error', '')
        if code:
            return 'OK', code
        if err == 'ip-already-existed':
            return 'USED', ''
        if err:
            return 'ERR', err[:80]
        return 'ERR', raw[:100]
    except json.JSONDecodeError:
        return 'ERR', f'json_parse: {r.stdout[:80]}'
    except Exception as e:
        return 'EXC', str(e)[:60]


def save_ref_code(acc_id, ref_code):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute('SELECT notes, tags FROM accounts WHERE id=%s', (acc_id,))
    row = cur.fetchone()
    notes = (row[0] or '')
    tags = (row[1] or '')
    if f'unitool_ref_code={ref_code}' not in notes:
        notes += f'\nunitool_ref_code={ref_code}'
    if 'unitool_ref_activated' not in tags:
        tags = (tags.rstrip(',') + ',unitool_ref_activated').strip(',')
    cur.execute('UPDATE accounts SET notes=%s, tags=%s, updated_at=NOW() WHERE id=%s',
                (notes, tags, acc_id))
    conn.commit()
    conn.close()


def get_accounts_needing_ref(limit=200):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute('''
        SELECT id, email, notes FROM accounts
        WHERE notes LIKE \'%%unitool_ssid=%%\'
          AND notes NOT LIKE \'%%unitool_ref_code=%%\'
          AND tags LIKE \'%%unitool_registered%%\'
        ORDER BY id DESC LIMIT %s
    ''', (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def extract_ssid(notes):
    m = re.search(r'unitool_ssid=([0-9a-f]{40,})', notes or '')
    return m.group(1) if m else None


def run_test():
    log.info('=== QuarkIP 代理测试 ===')
    ip1 = get_current_ip()
    log.info(f'当前出口IP: {ip1}')
    change_ip(wait=3)
    ip2 = get_current_ip()
    log.info(f'切换后IP: {ip2}')
    if ip1 and ip2 and ip1 != ip2:
        log.info('✅ IP切换正常')
    else:
        log.warning('⚠️  IP切换后相同，可能需要更长等待时间')
    return ip1, ip2


def run(limit=200):
    log.info('=== quarkip_ref_create 开始 ===')

    # 测试代理可用性
    ip = get_current_ip()
    if not ip:
        log.error('QuarkIP 代理不可用，退出')
        return
    log.info(f'初始出口IP: {ip}')

    accounts = get_accounts_needing_ref(limit)
    if not accounts:
        log.info('没有账号需要创建 ref_code')
        return
    log.info(f'需要 ref_code 的账号: {len(accounts)} 个')

    created = 0
    used_ip = 0
    failed = 0

    for i, (acc_id, email, notes) in enumerate(accounts):
        ssid = extract_ssid(notes)
        if not ssid:
            log.warning(f'[{i+1}] {email} 无 ssid，跳过')
            continue

        # 每次尝试前切换IP（避免使用同一IP重复创建）
        if i > 0:
            change_ip(wait=4)

        cur_ip = get_current_ip()
        log.info(f'[{i+1}/{len(accounts)}] {email} id={acc_id} 出口IP={cur_ip}')

        status, val = create_ref_code(ssid)

        if status == 'OK':
            save_ref_code(acc_id, val)
            log.info(f'  ✅ CREATED ref_code={val}')
            created += 1
        elif status == 'USED':
            log.warning(f'  ❌ ip-already-existed (IP={cur_ip} 已被标记，切换IP)')
            used_ip += 1
            # 立即再切换一次IP
            change_ip(wait=5)
        else:
            log.warning(f'  ⚠️  {status}: {val}')
            failed += 1

        time.sleep(2)

    log.info(f'=== 完成: 创建={created} IP已用={used_ip} 其他失败={failed} ===')
    return created


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='QuarkIP ref_code 创建工具')
    ap.add_argument('--test', action='store_true', help='仅测试代理连通和IP切换')
    ap.add_argument('--limit', type=int, default=200, help='最多处理账号数 (默认200)')
    args = ap.parse_args()

    if args.test:
        run_test()
    else:
        run(limit=args.limit)
