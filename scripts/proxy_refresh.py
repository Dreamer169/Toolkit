#!/usr/bin/env python3
"""
proxy_refresh.py — proxy_manager 定时刷新守护进程

Run via PM2:
  pm2 start /data/Toolkit/scripts/proxy_refresh.py \
      --name proxy-refresh \
      --interpreter python3 \
      --restart-delay 5000

Or directly:
  python3 /data/Toolkit/scripts/proxy_refresh.py

Behavior:
  - Refresh all proxy sources every REFRESH_INTERVAL seconds
  - Probe all proxies every PROBE_INTERVAL seconds
  - Inject alive proxies into resi_pool after each probe round
  - Log to /var/log/proxy_refresh.log
"""
import sys, os, time, logging, json

sys.path.insert(0, "/data/Toolkit/scripts")

REFRESH_INTERVAL = int(os.environ.get("PROXY_REFRESH_INTERVAL", "1800"))   # 30 min
PROBE_INTERVAL   = int(os.environ.get("PROXY_PROBE_INTERVAL",   "600"))    # 10 min
LOG_FILE         = "/var/log/proxy_refresh.log"

logging.basicConfig(
    level=logging.INFO,
    format="[proxy_refresh] %(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ])
log = logging.getLogger("proxy_refresh")


def run():
    from proxy_manager import ProxyManager
    pm = ProxyManager()
    log.info(f"Starting — refresh={REFRESH_INTERVAL}s  probe={PROBE_INTERVAL}s")
    log.info(f"DB: {pm.db.path}  existing={pm.db.count()}")

    # Run immediately on startup
    last_refresh = 0.0
    last_probe   = 0.0

    while True:
        now = time.time()

        if now - last_refresh >= REFRESH_INTERVAL:
            log.info("=== Refreshing proxy sources ===")
            try:
                results = pm.refresh_all(log_fn=log.info)
                log.info(f"Refresh done: {results}")
            except Exception as e:
                log.error(f"Refresh error: {e}", exc_info=True)
            last_refresh = time.time()

        if now - last_probe >= PROBE_INTERVAL:
            log.info("=== Probing all proxies ===")
            try:
                r = pm.probe_all(max_workers=25, log_fn=log.info)
                log.info(f"Probe done: {r}")
                n = pm.inject_resi_pool(log_fn=log.info)
                log.info(f"Injected {n} proxies into resi_pool")
            except Exception as e:
                log.error(f"Probe error: {e}", exc_info=True)
            last_probe = time.time()

            # Print status summary to log
            st = pm.status()
            for src, d in sorted(st["by_source"].items()):
                log.info(f"  {src:14s} total={d['total']} alive={d['alive']} "
                         f"unknown={d['unknown']} dead={d['dead']}")

        time.sleep(30)


if __name__ == "__main__":
    run()
