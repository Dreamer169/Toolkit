#!/bin/bash
export PORT=8092
export NODE_ENV=production
export PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
export DISPLAY=:99
export REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE=/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome
export FRONTEND_DIR=/root/browser-model/artifacts/api-server/public
export BROWSER_PROXY=socks5://127.0.0.1:1193
exec node --enable-source-maps /root/browser-model/artifacts/api-server/dist/index.mjs
