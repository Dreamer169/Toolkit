#!/usr/bin/env python3
import sys, json, argparse, os

sys.path.insert(0, os.path.dirname(__file__))
import cf_ip_pool

POOL_STATE_FILE = '/tmp/cf_pool_state.json'


def _read_state():
    try:
        with open(POOL_STATE_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_pool_from_disk():
    state = _read_state()
    available = state.get('available', [])
    history = state.get('used_history') or state.get('history') or []
    banned = state.get('banned') or []
    with cf_ip_pool._pool_lock:
        cf_ip_pool._used_history.clear()
        cf_ip_pool._used_history.extend([x for x in history if isinstance(x, str)])
        cf_ip_pool._banned_ips.clear()
        cf_ip_pool._banned_ips.update([x for x in banned if isinstance(x, str)])
        blocked = set(cf_ip_pool._used_history) | set(cf_ip_pool._banned_ips)
        clean = []
        seen = set()
        for item in available:
            if not isinstance(item, dict):
                continue
            ip = item.get('ip')
            lat = item.get('latency')
            if not isinstance(ip, str) or not isinstance(lat, (int, float)):
                continue
            if ip in blocked or ip in seen:
                continue
            seen.add(ip)
            clean.append({'ip': ip, 'latency': lat, 'proxy': item.get('proxy') or f'http://{ip}:443'})
        clean.sort(key=lambda x: x['latency'])
        cf_ip_pool._available.clear()
        cf_ip_pool._available.extend(clean)


def _persist_history():
    try:
        with cf_ip_pool._pool_lock:
            hist = list(dict.fromkeys(cf_ip_pool._used_history))[-2000:]
            banned = list(dict.fromkeys(cf_ip_pool._banned_ips))[-2000:]
            available = []
            seen = set()
            blocked = set(hist) | set(banned)
            for item in cf_ip_pool._available:
                ip = item.get('ip') if isinstance(item, dict) else None
                lat = item.get('latency') if isinstance(item, dict) else None
                if not isinstance(ip, str) or not isinstance(lat, (int, float)):
                    continue
                if ip in seen or ip in blocked:
                    continue
                seen.add(ip)
                available.append({'ip': ip, 'latency': lat, 'proxy': item.get('proxy') or f'http://{ip}:443'})
            available.sort(key=lambda x: x['latency'])
        with open(POOL_STATE_FILE, 'w') as f:
            json.dump({
                'available': available,
                'used_history': hist,
                'history': hist,
                'history_count': len(hist),
                'banned': banned,
            }, f)
    except Exception:
        pass


def cmd_status(args):
    state = _read_state()
    available = state.get('available', [])
    history = state.get('used_history') or state.get('history') or []
    banned = state.get('banned') or []
    print(json.dumps({
        'available': len(available),
        'pool': available[:20],
        'used_total': len(history),
        'banned_total': len(banned),
    }))


def cmd_refresh(args):
    _load_pool_from_disk()
    logs = []
    before = len(cf_ip_pool._available)
    new_ips = cf_ip_pool.refresh_pool(
        generate_count=args.count,
        target_valid=args.target,
        threads=args.threads,
        port=args.port,
        max_latency=args.max_latency,
        log_cb=lambda m: logs.append(m),
    )
    _persist_history()
    state = _read_state()
    total_available = len(state.get('available', []))
    print(json.dumps({
        'new_ips': len(new_ips),
        'added': max(0, total_available - before),
        'total_available': total_available,
        'pool': new_ips[:20],
        'logs': logs,
    }))


def cmd_acquire(args):
    _load_pool_from_disk()
    ip_info = cf_ip_pool.acquire_ip(args.job_id, auto_refresh=args.auto_refresh)
    _persist_history()
    if ip_info:
        print(json.dumps({'success': True, **ip_info}))
    else:
        print(json.dumps({'success': False, 'error': 'pool_empty'}))


def cmd_release(args):
    _load_pool_from_disk()
    cf_ip_pool.release_ip(args.job_id)
    _persist_history()
    print(json.dumps({'success': True}))


def cmd_ban(args):
    _load_pool_from_disk()
    removed = cf_ip_pool.ban_ip(args.ip)
    _persist_history()
    print(json.dumps({'success': True, 'removed': removed, 'ip': args.ip}))


def cmd_retest(args):
    _load_pool_from_disk()
    logs = []
    result = cf_ip_pool.retest_pool(
        max_latency=args.max_latency,
        threads=args.threads,
        port=args.port,
        log_cb=lambda m: logs.append(m),
    )
    _persist_history()
    print(json.dumps({**result, 'logs': logs}))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd')
    sub.add_parser('status')
    ref = sub.add_parser('refresh')
    ref.add_argument('--count', type=int, default=60)
    ref.add_argument('--target', type=int, default=20)
    ref.add_argument('--threads', type=int, default=5)
    ref.add_argument('--port', type=int, default=443)
    ref.add_argument('--max-latency', type=float, default=800.0, dest='max_latency')
    acq = sub.add_parser('acquire')
    acq.add_argument('--job-id', required=True, dest='job_id')
    acq.add_argument('--auto-refresh', action='store_true', dest='auto_refresh')
    rel = sub.add_parser('release')
    rel.add_argument('--job-id', required=True, dest='job_id')
    ban = sub.add_parser('ban')
    ban.add_argument('--ip', required=True)
    ret = sub.add_parser('retest')
    ret.add_argument('--max-latency', type=float, default=800.0, dest='max_latency')
    ret.add_argument('--threads', type=int, default=8)
    ret.add_argument('--port', type=int, default=443)
    args = p.parse_args()
    if args.cmd == 'status':
        cmd_status(args)
    elif args.cmd == 'refresh':
        cmd_refresh(args)
    elif args.cmd == 'acquire':
        cmd_acquire(args)
    elif args.cmd == 'release':
        cmd_release(args)
    elif args.cmd == 'ban':
        cmd_ban(args)
    elif args.cmd == 'retest':
        cmd_retest(args)
    else:
        print(json.dumps({'error': 'unknown command'}))
