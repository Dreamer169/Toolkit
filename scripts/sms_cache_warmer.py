#!/usr/bin/env python3
"""SMS Cache Warmer — runs every 4 minutes, pre-fetches messages for popular numbers into file cache."""
import asyncio, json, os, sys, time, signal

sys.path.insert(0, '/root/Toolkit/scripts')

# Popular numbers to keep warm (verified working on smsreceivefree.xyz)
POPULAR = [
    '7437695823',
    '5183535766',
    '3397875789',
    '8053479366',
    '7739934816',
    '4157773804',
    '9258986521',
    '6467093288',
]

INTERVAL = 240  # 4 minutes

def log(msg):
    print(f'[warmer {time.strftime("%H:%M:%S")}] {msg}', flush=True)

async def warm_one(phone: str):
    from smsreceivefree_fetch import fetch_messages_async, _cache_get, TTL_MSG
    cached = _cache_get(f'msgs_{phone}', TTL_MSG)
    if cached:
        log(f'{phone}: cache fresh, skipping')
        return
    log(f'{phone}: cache miss, fetching...')
    try:
        r = await fetch_messages_async(phone)
        n = len(r.get('messages', []))
        log(f'{phone}: got {n} messages')
    except Exception as e:
        log(f'{phone}: error {e}')

async def warm_all():
    log(f'Warming {len(POPULAR)} numbers...')
    for p in POPULAR:
        try:
            await warm_one(p)
            await asyncio.sleep(5)  # stagger to avoid port conflicts
        except Exception as e:
            log(f'error warming {p}: {e}')
    log('Warm cycle complete')

def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    log('SMS Cache Warmer started')
    log(f'Warming {len(POPULAR)} numbers every {INTERVAL}s')
    while True:
        try:
            asyncio.run(warm_all())
        except Exception as e:
            log(f'cycle error: {e}')
        time.sleep(INTERVAL)

if __name__ == '__main__':
    main()
