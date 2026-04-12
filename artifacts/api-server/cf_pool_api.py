#!/usr/bin/env python3
"""
CF IP 池命令行 API（被 tools.ts 通过 spawnSync 调用）
用法：
  python3 cf_pool_api.py status
  python3 cf_pool_api.py refresh [--count N] [--target N] [--threads N] [--port N] [--max-latency N]
  python3 cf_pool_api.py acquire --job-id JOB_ID
  python3 cf_pool_api.py release --job-id JOB_ID
"""
import sys, json, argparse, os

# 确保能找到 cf_ip_pool
sys.path.insert(0, os.path.dirname(__file__))
import cf_ip_pool

def cmd_status(args):
    # 从持久化文件读取池状态
    try:
        import json as _j
        with open('/tmp/cf_pool_state.json') as f:
            state = _j.load(f)
        available = state.get('available', [])
    except Exception:
        available = []
    print(json.dumps({
        'available': len(available),
        'pool': available[:20],
        'used_total': state.get('history_count', 0) if 'state' in dir() else 0
    }))

def cmd_refresh(args):
    logs = []
    new_ips = cf_ip_pool.refresh_pool(
        generate_count = args.count,
        target_valid   = args.target,
        threads        = args.threads,
        port           = args.port,
        max_latency    = args.max_latency,
        log_cb         = lambda m: logs.append(m),
    )
    # 读取新的持久化状态
    try:
        with open('/tmp/cf_pool_state.json') as f:
            state = json.load(f)
        total_available = len(state.get('available', []))
    except Exception:
        total_available = len(new_ips)

    print(json.dumps({
        'new_ips': len(new_ips),
        'total_available': total_available,
        'pool': new_ips[:20],
        'logs': logs,
    }))

def cmd_acquire(args):
    ip_info = cf_ip_pool.acquire_ip(args.job_id, auto_refresh=True)
    if ip_info:
        print(json.dumps({'success': True, **ip_info}))
    else:
        print(json.dumps({'success': False, 'error': '池中无可用 IP'}))

def cmd_release(args):
    cf_ip_pool.release_ip(args.job_id)
    print(json.dumps({'success': True}))

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd')

    sub.add_parser('status')

    ref = sub.add_parser('refresh')
    ref.add_argument('--count',       type=int,   default=60)
    ref.add_argument('--target',      type=int,   default=20)
    ref.add_argument('--threads',     type=int,   default=5)
    ref.add_argument('--port',        type=int,   default=443)
    ref.add_argument('--max-latency', type=float, default=800.0, dest='max_latency')

    acq = sub.add_parser('acquire')
    acq.add_argument('--job-id', required=True, dest='job_id')

    rel = sub.add_parser('release')
    rel.add_argument('--job-id', required=True, dest='job_id')

    args = p.parse_args()
    if   args.cmd == 'status':  cmd_status(args)
    elif args.cmd == 'refresh': cmd_refresh(args)
    elif args.cmd == 'acquire': cmd_acquire(args)
    elif args.cmd == 'release': cmd_release(args)
    else: print(json.dumps({'error': 'unknown command'}))
