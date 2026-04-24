#!/bin/bash
# v7.67 — broker chromium 启动包装。BROWSER_PROXY 选择策略：
#   1) WARP (socks5://127.0.0.1:40000, Cloudflare 自家 backbone, MASQUE/HTTP3) — 首选
#      Cloudflare 不会用自己的 challenge layer 拦截自家 WARP 出口，对 replit.com
#      /data/user/exists 这类被 CF API tier 拦截的端点是天然解药。
#   2) Kirino (socks5://127.0.0.1:10824, 0391f15 audit pass) — 备份
#   3) DigitalOcean (socks5://127.0.0.1:10826) — 二次备份
#
# WARP-SSH 安全约束（绝不可破）：
#   warp-cli 必须保持 proxy 模式 (Mode: WarpProxy on port 40000)。
#   任何脚本绝不可调用 `warp-cli set-mode warp` (full tunnel) — 那会安装
#   CloudflareWARP 隧道接口并把所有出口路由打到 wg/utun，导致到 SSH 客户端的
#   反向流量改走 WARP，原 eth0 路由失效，SSH 立刻断开且无法复连。
#   本脚本只读 warp-cli 状态，不修改 mode。

export PORT=8092
export NODE_ENV=production
export PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
export DISPLAY=:99
export REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE=/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome
export FRONTEND_DIR=/root/browser-model/artifacts/api-server/public

# v7.67 — 选 BROWSER_PROXY (优先 WARP，fallback Kirino → DigitalOcean)
_pick_browser_proxy() {
  # v7.71: 优先 clean SOCKS exit (Kirino/DO/Multacom). 全死时 fallback DIRECT (VPS IP),
  # 不再用 WARP — WARP exit 是 Cloudflare datacenter IP, reCAPTCHA Enterprise 直接判低分 → code:2 reject.
  for cand in 10826:DigitalOcean 10824:Kirino 10830:MULTACOM 10828:Misaka 10822:Vultr 10832:Linode; do
    port="${cand%%:*}"; name="${cand##*:}"
    ss -tln 2>/dev/null | grep -qE "127\.0\.0\.1:${port}\b" || continue
    if curl -s --max-time 10 --socks5 "127.0.0.1:${port}" https://1.1.1.1/cdn-cgi/trace 2>/dev/null | grep -q '^h=1\.1\.1\.1'; then
      echo "socks5://127.0.0.1:${port}|${name}"
      return 0
    fi
  done
  echo "|DIRECT-VPS-fallback"
}
_picked="$(_pick_browser_proxy)"
export BROWSER_PROXY="${_picked%%|*}"
echo "[start-browser-model] BROWSER_PROXY=${BROWSER_PROXY}  (chosen: ${_picked##*|})"

# v7.67 — sanity log warp-cli (只读, 绝不修改)
if command -v warp-cli >/dev/null 2>&1; then
  _wmode="$(warp-cli --accept-tos settings 2>/dev/null | grep -E '^\(user set\)Mode:' | head -1)"
  echo "[start-browser-model] warp-cli ${_wmode:-(no settings)}"
  case "$_wmode" in
    *WarpProxy*) : ;;  # OK, safe
    *Warp[^P]*|*WarpTunnel*)
      echo "[start-browser-model] !!! WARP is in TUNNEL mode — SSH 路由可能已坏。脚本不会自动切换，需手工修复 !!!" >&2 ;;
  esac
fi

# v7.66 — ensure dbus system socket exists (chromium D-Bus FATAL fix)
if [ ! -S /var/run/dbus/system_bus_socket ] || ! pgrep -f "dbus-daemon --system --fork" >/dev/null 2>&1; then
  mkdir -p /var/run/dbus
  /usr/bin/dbus-daemon --system --fork 2>/dev/null || true
fi

exec node --enable-source-maps /root/browser-model/artifacts/api-server/dist/index.mjs
