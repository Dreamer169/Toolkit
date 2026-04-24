#!/bin/bash
export PORT=8092
export NODE_ENV=production
export PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
export DISPLAY=:99
export REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE=/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome
export FRONTEND_DIR=/root/browser-model/artifacts/api-server/public
export BROWSER_PROXY=socks5://127.0.0.1:10824  # was :40000 (WARP, dead since da6f05e); :10824 = Kirino, audited non-GCP/non-Google in 0391f15
# v7.66 — ensure dbus system socket exists (chromium D-Bus FATAL fix)
if [ ! -S /var/run/dbus/system_bus_socket ] || ! pgrep -f "dbus-daemon --system --fork" >/dev/null 2>&1; then
  mkdir -p /var/run/dbus
  /usr/bin/dbus-daemon --system --fork 2>/dev/null || true
fi

exec node --enable-source-maps /root/browser-model/artifacts/api-server/dist/index.mjs
