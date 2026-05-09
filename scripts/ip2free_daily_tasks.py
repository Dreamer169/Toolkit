#!/usr/bin/env python3
"""
ip2free_daily_tasks.py v2 -- 每日自动领取 ip2free 任务奖励代理

功能:
  1. 登录所有 9 个 ip2free 账号
  2. 先 GET /api/website/linkClick 触发点击（解锁 client_click 任务条件）
  3. 领取 task_code=client_click 的每日点击奖励（finishTask）
  4. 拉取 taskIpList 获取活动代理（时限住宅 IP）
  5. 将新代理写入 proxy_manager DB（/data/proxy_db.json）
  6. 记录领取历史 /data/ip2free_task_state.json

任务说明:
  task_id=6  client_click       每天 → 1天住宅代理（US/SG）  ★ 可自动领
  task_id=8  register_one_three 每周 → 3天住宅代理（UK）      邀请1人自动触发
  task_id=2  register_three     每月 → 30天住宅代理           邀请3人自动触发
  task_id=11/9 manual_review    限时 → 需人工审核              无法自动领

已知限制:
  老账号（sophiagray574/e.lewis904/rylan_rivera98）直接支持纯 API 领取，
  邀请注册的新账号（reg2026a*/ip2r_*）需通过 patchright 浏览器完成第一次登录
  后才能激活 finishTask，届时再用本脚本即可全量自动领取。

API 端点（由 Next.js JS bundle 逆向得到）:
  GET  /api/website/link?          → 获取合作链接列表（含 link_id）
  GET  /api/website/linkClick?id=  → 记录链接点击（触发任务条件）
  POST /api/account/taskList?      → 获取任务列表 + 完成状态
  POST /api/account/finishTask?    → {"id": <record_id>} 领取奖励
  POST /api/ip/taskIpList?         → {"size":100} 获取活动代理 IP 列表
"""

import sys, os, json, time, argparse, logging, datetime, pathlib
import requests, urllib3

urllib3.disable_warnings()
sys.path.insert(0, os.path.dirname(__file__))
from proxy_manager import ProxyManager, ProxyEntry, EXCLUSION_RULES

# ── 配置 ─────────────────────────────────────────────────────────────────────
BASE_API         = 'https://api.ip2free.com'
TASK_STATE_FILE  = '/data/ip2free_task_state.json'
AUTO_CLAIMABLE   = {'client_click'}

H_BASE = {
    'User-Agent':   'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.6778.85 Safari/537.36',
    'Content-Type': 'text/plain;charset=UTF-8',
    'Origin':       'https://www.ip2free.com',
    'Referer':      'https://www.ip2free.com/',
    'lang': 'cn', 'domain': 'www.ip2free.com', 'webname': 'IP2FREE',
    'affid': '', 'invitecode': '', 'serviceid': '',
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-7s %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/ip2free_daily_tasks.log', encoding='utf-8'),
    ]
)
log = logging.getLogger('ip2free_tasks')

# ── 状态文件 ─────────────────────────────────────────────────────────────────
def load_state() -> dict:
    try:
        return json.loads(pathlib.Path(TASK_STATE_FILE).read_text())
    except Exception:
        return {}

def save_state(state: dict):
    pathlib.Path(TASK_STATE_FILE).write_text(
        json.dumps(state, indent=2, ensure_ascii=False)
    )

# ── ip2free API 封装 ──────────────────────────────────────────────────────────
def _session_login(email: str, password: str):
    s = requests.Session()
    s.verify = False
    s.headers.update(H_BASE)
    r = s.post(BASE_API + '/api/account/login?',
               data=json.dumps({'email': email, 'password': password}), timeout=15)
    d = r.json()
    tok = (d.get('data') or {}).get('token')
    if not tok:
        raise RuntimeError(f'login failed: {d.get("msg","?")}')
    inv = (d.get('data', {}).get('profile') or {}).get('invite_code', '')
    s.headers['x-token'] = tok
    return s, tok, inv

def _get_link_ids(s: requests.Session) -> list:
    """GET /api/website/link → 返回所有 link_id 列表"""
    r = s.get(BASE_API + '/api/website/link?', timeout=10)
    acc = []
    def _recurse(obj):
        if isinstance(obj, dict):
            if 'id' in obj and 'link' in obj:
                acc.append(obj['id'])
            for v in obj.values():
                _recurse(v)
        elif isinstance(obj, list):
            for item in obj:
                _recurse(item)
    _recurse(r.json())
    return acc

def _click_all_links(s: requests.Session) -> int:
    """点击所有合作链接触发 client_click 任务条件，返回点击数"""
    link_ids = _get_link_ids(s)
    for lid in link_ids:
        try:
            s.get(BASE_API + f'/api/website/linkClick?id={lid}',
                  timeout=8, allow_redirects=True)
        except Exception:
            pass
        time.sleep(0.2)
    return len(link_ids)

def _task_list(s: requests.Session) -> list:
    r = s.post(BASE_API + '/api/account/taskList?', data='{}', timeout=12)
    return r.json().get('data', {}).get('list', [])

def _finish_task(s: requests.Session, record_id: int) -> dict:
    r = s.post(BASE_API + '/api/account/finishTask?',
               data=json.dumps({'id': record_id}), timeout=12)
    return r.json()

def _task_ip_list(s: requests.Session, size: int = 100) -> list:
    r = s.post(BASE_API + '/api/ip/taskIpList?',
               data=json.dumps({'size': size}), timeout=12)
    return (r.json().get('data', {}).get('page') or {}).get('list', [])

# ── 写入 proxy_manager DB ────────────────────────────────────────────────────
def _ingest_task_proxies(task_ips: list, email: str, pm: ProxyManager) -> int:
    added = 0
    for p in task_ips:
        raw_id = p.get('id', f"{p.get('ip')}:{p.get('port')}")
        uid = f"ip2free:task:{raw_id}"
        if pm.db.get(uid):
            continue
        exp_str = p.get('expired_at') or p.get('expire_time')
        exp_ts = None
        if exp_str:
            try:
                from datetime import datetime, timezone
                exp_ts = datetime.strptime(exp_str, '%Y-%m-%d %H:%M:%S').replace(
                    tzinfo=timezone.utc).timestamp()
            except Exception:
                pass
        pm.db.put(ProxyEntry(
            uid=uid,
            proto=p.get('protocol', 'socks5'),
            host=p.get('ip', ''),
            port=int(p.get('port', 0)),
            user=p.get('username', ''),
            passwd=p.get('password', ''),
            source='ip2free',
            source_account=email,
            country=p.get('country_code', ''),
            city=p.get('city', ''),
            proxy_type='residential',
            not_for=list(EXCLUSION_RULES['ip2free']),
            expire_ts=exp_ts,
            meta={
                'remark':      p.get('remark', ''),
                'task_proxy':  True,
                'bind_status': p.get('bind_status', 0),
                'assigned_at': p.get('assigned_at', ''),
            },
        ), save=False)
        added += 1
    if added:
        pm.db._save()
    return added

# ── 单账号处理 ────────────────────────────────────────────────────────────────
def process_account(email: str, password: str, today: str,
                    state: dict, pm: ProxyManager, force: bool = False) -> dict:
    result = {'email': email, 'claimed': 0, 'proxies_added': 0,
              'task_ips_total': 0, 'error': None, 'needs_browser': False}
    acct_state = state.setdefault(email, {})

    try:
        log.info(f'[{email}] Logging in...')
        s, tok, inv = _session_login(email, password)
        log.info(f'[{email}] OK  invite={inv}')

        # Step 1: 点击所有合作链接（触发 client_click 任务条件）
        n_clicked = _click_all_links(s)
        log.info(f'[{email}] Clicked {n_clicked} partner links')

        # Step 2: 获取任务列表
        tasks = _task_list(s)
        log.info(f'[{email}] Tasks: {len(tasks)} records')

        claimed_today = 0
        needs_browser = False
        for t in tasks:
            tid      = t.get('task_id')
            rec_id   = t['id']
            code     = t.get('task_code', '')
            finished = t.get('is_finished', 1)

            if code not in AUTO_CLAIMABLE:
                continue
            if finished:
                log.info(f'[{email}] task_id={tid} already done ({t.get("finished_at","?")})')
                continue

            already_key = f'claimed_{today}_{rec_id}'
            if not force and acct_state.get(already_key):
                log.info(f'[{email}] task_id={tid} already claimed today (cached)')
                continue

            log.info(f'[{email}] Claiming task_id={tid} rec_id={rec_id}...')
            res = _finish_task(s, rec_id)
            rc  = res.get('code', -1)
            msg = res.get('msg', '?')

            if rc in (0, 200) or 'success' in msg.lower():
                log.info(f'[{email}] ✓ Claimed! msg={msg}')
                acct_state[already_key] = datetime.datetime.now().isoformat()
                claimed_today += 1
            elif 'invalid' in msg.lower():
                # 账号需要先经过一次浏览器登录后才能激活 finishTask
                log.warning(f'[{email}] finishTask → "{msg}" — needs browser login once to activate')
                needs_browser = True
            else:
                log.warning(f'[{email}] finishTask failed: {msg} (code={rc})')

        result['claimed']       = claimed_today
        result['needs_browser'] = needs_browser

        # Step 3: 拉取活动代理
        task_ips = _task_ip_list(s, size=100)
        result['task_ips_total'] = len(task_ips)
        log.info(f'[{email}] taskIpList: {len(task_ips)} activity proxies')

        added = _ingest_task_proxies(task_ips, email, pm)
        result['proxies_added'] = added
        if added:
            log.info(f'[{email}] +{added} new proxies added to DB')
        else:
            log.info(f'[{email}] No new proxies (all already in DB)')

        acct_state['last_run']      = datetime.datetime.now().isoformat()
        acct_state['last_task_ips'] = len(task_ips)

        for p in task_ips[:3]:
            exp = p.get('expired_at', '?')
            url = f"{p.get('ip')}:{p.get('port')}"
            log.info(f'  [{p.get("country_code","?")}] {url}  user={p.get("username","")}  exp={exp}')
        if len(task_ips) > 3:
            log.info(f'  ... and {len(task_ips)-3} more')

    except Exception as e:
        log.error(f'[{email}] ERROR: {e}')
        result['error'] = str(e)

    return result

# ── 主程序 ────────────────────────────────────────────────────────────────────
def cmd_run(force: bool = False):
    today = datetime.datetime.now().strftime('%Y%m%d')
    state = load_state()
    pm    = ProxyManager()
    accounts = pm._ip2free_accounts

    log.info(f'=== ip2free daily tasks  {today}  accounts={len(accounts)} ===')

    results = []
    total_claimed = 0
    total_proxies = 0
    needs_browser_list = []

    for i, acct in enumerate(accounts, 1):
        log.info(f'\n--- Account {i}/{len(accounts)}: {acct["email"]} ---')
        r = process_account(acct['email'], acct['password'], today, state, pm, force=force)
        results.append(r)
        total_claimed += r['claimed']
        total_proxies += r['proxies_added']
        if r['needs_browser']:
            needs_browser_list.append(acct['email'])
        save_state(state)
        if i < len(accounts):
            time.sleep(2)

    log.info('\n' + '='*65)
    log.info(f' 完成: {len(accounts)} 账号 | 领取任务: {total_claimed} | 新增代理: {total_proxies}')
    log.info('='*65)

    # 汇总表
    print(f'\n{"Email":<42} {"领取":>4} {"新增代理":>6} {"活动代理数":>8} {"状态":<15}')
    print('-'*82)
    for r in results:
        if r['needs_browser']:
            status = '⚠ need browser'
        elif r['error']:
            status = f'ERR: {r["error"][:12]}'
        else:
            status = 'OK'
        print(f'{r["email"]:<42} {r["claimed"]:>4} {r["proxies_added"]:>6} {r["task_ips_total"]:>8}    {status}')

    if needs_browser_list:
        print(f'\n⚠  {len(needs_browser_list)} 账号需要先通过 patchright 浏览器登录一次激活每日任务:')
        for e in needs_browser_list:
            print(f'   python3 ip2free_browser_login.py --email {e}')

    print()
    pm.print_status()
    save_state(state)

def cmd_status():
    state = load_state()
    pm    = ProxyManager()
    accounts = pm._ip2free_accounts
    today = datetime.datetime.now().strftime('%Y%m%d')

    print(f'\nip2free task claim status  ({today})')
    print(f'  {"Email":<42} {"Last Run":<20} {"Task IPs":>8} {"Today":>5}')
    print('  ' + '-'*78)
    for acct in accounts:
        e  = acct['email']
        st = state.get(e, {})
        last = st.get('last_run', 'never')[:16]
        tips = st.get('last_task_ips', 0)
        done = any(k.startswith(f'claimed_{today}_') for k in st)
        tag  = '✓' if done else ' '
        print(f'  {tag} {e:<40} {last:<20} {tips:>8}')

    # 统计活动代理
    task_proxies = [v for v in pm.db._data.values()
                    if getattr(v, 'meta', {}).get('task_proxy')
                    and getattr(v, 'source', '') == 'ip2free']
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    alive  = [p for p in task_proxies
              if getattr(p, 'expire_ts', None) is None or p.expire_ts > now_ts]
    print(f'\nActivity proxies in DB: {len(task_proxies)} total, {len(alive)} not expired')
    for p in sorted(alive, key=lambda x: getattr(x, 'expire_ts', 0) or 0, reverse=True)[:10]:
        exp = ''
        if getattr(p, 'expire_ts', None):
            exp = datetime.datetime.fromtimestamp(p.expire_ts).strftime('%m-%d %H:%M')
        print(f'  [{p.country:<4}] {p.host}:{p.port}  {p.user}  exp={exp}  ← {p.source_account[:30]}')

def main():
    ap = argparse.ArgumentParser(description='ip2free 每日任务自动领取 v2')
    ap.add_argument('--status', action='store_true', help='仅显示状态')
    ap.add_argument('--all',    action='store_true', help='强制重新领取（忽略今日已领缓存）')
    args = ap.parse_args()
    if args.status:
        cmd_status()
    else:
        cmd_run(force=args.all)

if __name__ == '__main__':
    main()
