#!/bin/bash
# v7.75 — broker chromium 启动包装。
#
# 关键拓扑修正 (vs v7.72 错误判断 "WARP CF datacenter score 低")：
#   WARP 出口 IP (104.28.x.x) = Cloudflare 自家 CDN backbone (NOT GCP datacenter)。
#   replit.com 站在 Cloudflare 后面，CF 不会用 challenge layer 拦自家 backbone 出口。
#   --proxy-server=socks5://127.0.0.1:40000 (WARP) 走 replit.com 是天然干净路径，
#   能稳定拿到 cf_clearance + /signup 不被 captcha 死循环。
#
#   reCAPTCHA Enterprise 评分 由 google_proxy_route.ts 的 attachGoogleProxyRouting()
#   接管：拦 *.google.com / *.gstatic.com / *.recaptcha.net / *.youtube.com 转走
#   非 GCP 的 SOCKS5 池 (10824 Kirino / 10826 DO / 10830 MULTACOM 等住宅/中小 ISP)。
#   两层职责分离：broker 走 WARP 解决 CF challenge，Google 子请求走 VLESS 抬 score。
#
# BROWSER_PROXY 选择优先级：
#   1) WARP (socks5://127.0.0.1:40000) — 首选，理由如上
#   2) Kirino (socks5://127.0.0.1:10824, AS215311) — backup
#   3) DigitalOcean (socks5://127.0.0.1:10826) — 二次 backup
#   4) MULTACOM (10830), 其它干净 xray 子节点
#   5) Tor (9050) — 最后住宅风格 fallback
#   6) DIRECT (45.205.27.69) — 全部失活时硬 fallback (会被 CF challenge)
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

# v7.75 — WARP-first picker (拓扑修正)
_pick_browser_proxy() {
  # 1) WARP (40000) — CF backbone，replit.com 信任路径
  if ss -tln 2>/dev/null | grep -qE "127\.0\.0\.1:40000\b" && \
     curl -s --max-time 10 --socks5 127.0.0.1:40000 https://ifconfig.me/ip 2>/dev/null | grep -qE "^[0-9]"; then
    echo "socks5://127.0.0.1:40000|WARP"
    return 0
  fi
  # 2) xray clean SOCKS — 排除 10827/10829 (GCP, audit 0391f15 已剔出)
  for cand in 10824:Kirino 10826:DigitalOcean 10830:MULTACOM 10828:Misaka 10822:Vultr 10832:Linode 10820:Static 10825:Static 10831:Static 10836:Static 10837:Static 10845:Static; do
    port="${cand%%:*}"; name="${cand##*:}"
    ss -tln 2>/dev/null | grep -qE "127\.0\.0\.1:${port}\b" || continue
    if curl -s --max-time 10 --socks5 "127.0.0.1:${port}" https://ifconfig.me/ip 2>/dev/null | grep -qE "^[0-9]"; then
      echo "socks5://127.0.0.1:${port}|${name}"
      return 0
    fi
  done
  # 3) Tor residential-style fallback
  if ss -tln 2>/dev/null | grep -qE "127\.0\.0\.1:9050\b" && \
     curl -s --max-time 12 --socks5 127.0.0.1:9050 https://ifconfig.me/ip 2>/dev/null | grep -qE "^[0-9]"; then
    echo "socks5://127.0.0.1:9050|Tor"
    return 0
  fi
  # 4) DIRECT (will get CF-challenged on replit.com — last resort)
  echo "|DIRECT-VPS-fallback"
}
_picked="$(_pick_browser_proxy)"
export BROWSER_PROXY="${_picked%%|*}"
echo "[start-browser-model] BROWSER_PROXY=${BROWSER_PROXY}  (chosen: ${_picked##*|})"

# v7.67 — sanity log warp-cli (只读, 绝不修改)
if command -v warp-cli >/dev/null 2>&1; then
  _wmode="$(warp-cli --accept-tos settings 2>/dev/null | grep -E "^\(user set\)Mode:" | head -1)"
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
