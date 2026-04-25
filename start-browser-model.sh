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

# v7.78e — datacenter-SOCKS-first picker + CF-段出口过滤 (拓扑修正 v7.75 / v7.78d)
#
# 实证 (v7.78d CDP 测试 2026-04-23 23:00):
# - 所有 26 个 xray VLESS outbound 上游都指向 CF edge IP (104.21.21.136 / 172.67.199.22),
#   后端 VLESS server 自身跑在 CF Workers / CF backbone, 出口 IP 全在 104.28.x 段。
# - CF 不发自家 IP 段的 cf_clearance → cf-warmup 永远 cf_clearance=False
#   → Replit integrity_check_failed_after_step1。
# - VPS 公网 45.205.27.69 = AS8796 FASTNET DATA (真 datacenter, CF 友好)。
#
# 修复策略: picker 测每个候选 SOCKS 实际出口 IP, 落在 CF 段就跳过；
# 所有 SOCKS 都 CF 时 fallback DIRECT (空 BROWSER_PROXY → chromium 直走 VPS 公网)。
# *.google 子请求由 google_proxy_route.py 单独走 WARP (40000)。
#
# CF IP 段 (AS-13335 主要):
#   104.16.0.0/12 (含 104.16/17/18/19/20/21/22/23/24/25/26/27/28/29/30/31)
#   172.64.0.0/13 (含 172.64/65/66/67/68/69/70/71)
#   141.101.64.0/18, 162.158.0.0/15, 173.245.48.0/20, 188.114.96.0/20 等
_is_cf_ip() {
  local ip="$1"
  [[ -z "$ip" ]] && return 1
  case "$ip" in
    104.1[6-9].*|104.2[0-9].*|104.3[01].*) return 0 ;;
    172.6[4-9].*|172.7[01].*) return 0 ;;
    141.101.6[4-9].*|141.101.[7-9][0-9].*|141.101.1[0-2][0-9].*) return 0 ;;
    162.158.*|173.245.4[8-9].*|173.245.5[0-9].*|173.245.6[0-3].*) return 0 ;;
    188.114.9[6-9].*|188.114.1[01][0-9].*) return 0 ;;
    *) return 1 ;;
  esac
}

_pick_browser_proxy() {
  # 1) datacenter SOCKS clean 池 — 必须出口非 CF 段
  for cand in 10824:Kirino 10826:DigitalOcean 10830:MULTACOM 10828:Misaka 10822:Vultr 10832:Linode 10838:Static 10820:Static 10825:Static 10831:Static 10836:Static 10837:Static 10845:Static; do
    port="${cand%%:*}"; name="${cand##*:}"
    ss -tln 2>/dev/null | grep -qE "127\.0\.0\.1:${port}\b" || continue
    EXIT=$(curl -s --max-time 8 --socks5 "127.0.0.1:${port}" https://api.ipify.org 2>/dev/null | tr -d "[:space:]")
    [[ -z "$EXIT" ]] && continue
    if _is_cf_ip "$EXIT"; then
      echo "[picker] skip ${name}(${port}) — exit ${EXIT} 在 CF 段 (cf_clearance 拿不到)" >&2
      continue
    fi
    echo "socks5://127.0.0.1:${port}|${name}@${EXIT}"
    return 0
  done
  # v8.01 — DIRECT BEFORE WARP:
  #   实证 2026-04-25 03:00: 3次成功注册全走 DIRECT(45.205.27.69 AS8796 FASTNET DATA).
  #   WARP(104.28.x CF backbone) 被 replit.com/signup CF-challenge 且 reCAPTCHA 评分极低.
  #   DIRECT VPS IP 不被 CF 拦截, broker=direct 时 google-route SKIPPED(IP一致), 成功率最高.
  #   原注释 v7.75/v7.78 说 DIRECT "会被 CF challenge" 是错的 — 实证推翻.
  #
  # 2) DIRECT — VPS 公网 IP 45.205.27.69 AS8796 FASTNET DATA
  DIRECT_EXIT=$(curl -s --max-time 6 https://api.ipify.org 2>/dev/null | tr -d "[:space:]")
  if [[ -n "$DIRECT_EXIT" ]]; then
    echo "|DIRECT-VPS@${DIRECT_EXIT}(AS8796-FASTNET)"
    return 0
  fi
  # 3) WARP — socks5://127.0.0.1:40000 (最后兜底; CF IP, reCAPTCHA 评分低, CF challenge on signup)
  if ss -uln 2>/dev/null | grep -qE "127\.0\.0\.1:40000\b" || ss -tln 2>/dev/null | grep -qE "127\.0\.0\.1:40000\b"; then
    WARP_EXIT=$(curl -s --max-time 8 --socks5 "127.0.0.1:40000" https://api.ipify.org 2>/dev/null | tr -d "[:space:]")
    if [[ -n "$WARP_EXIT" ]]; then
      echo "socks5://127.0.0.1:40000|WARP@${WARP_EXIT}"
      return 0
    fi
    echo "[picker] WARP port 40000 listen 但 curl 失败, 跳过" >&2
  fi
  # 4) 完全失活硬兜底
  echo "|DIRECT-VPS@45.205.27.69(AS8796-FASTNET-fallback)"
  return 0
}
_picked="$(_pick_browser_proxy)"
export BROWSER_PROXY="${_picked%%|*}"
# v7.95 — export broker exit family so register can align google_proxy_route
#         to broker's exit IP (avoid IP串台/recaptcha code:1).
#  socks  -> google-route pinned to SAME socks port (token-gen IP == submit IP)
#  warp   -> google-route SKIPPED (let *.google traffic exit via WARP too,
#            consistent with broker's WARP exit; user-validated WARP works
#            for sign-up when used end-to-end consistently)
#  direct -> google-route SKIPPED (let *.google exit via VPS IP same as broker)
_brox="$BROWSER_PROXY"
if [[ -z "$_brox" ]]; then
  export BROKER_EXIT_FAMILY="direct"
  unset BROKER_EXIT_SOCKS_PORT
elif [[ "$_brox" == *":40000" ]]; then
  export BROKER_EXIT_FAMILY="warp"
  unset BROKER_EXIT_SOCKS_PORT
else
  export BROKER_EXIT_FAMILY="socks"
  export BROKER_EXIT_SOCKS_PORT="$(echo "$_brox" | sed -E 's/.*:([0-9]+).*/\1/')"
fi
echo "[start-browser-model] BROWSER_PROXY=${BROWSER_PROXY}  (chosen: ${_picked##*|})"
echo "[start-browser-model] BROKER_EXIT_FAMILY=${BROKER_EXIT_FAMILY} BROKER_EXIT_SOCKS_PORT=${BROKER_EXIT_SOCKS_PORT:-N/A}"
# v7.95b — 写入跨进程共享文件 (api-server spawn 的 python 子进程从此处读)
mkdir -p /tmp/replit-broker
printf '{"family":"%s","port":"%s","ts":%d}\n' "${BROKER_EXIT_FAMILY}" "${BROKER_EXIT_SOCKS_PORT:-}" "$(date +%s)" > /tmp/replit-broker/exit.json
echo "[start-browser-model] wrote /tmp/replit-broker/exit.json"

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

# v7.89 — preflight: kill any orphan chromium owning the broker debug port (9222)
# before spawning new broker. Without this, pm2 restart of browser-model leaks
# orphan chromium that holds 9222 (its parent broker died but chromium kept
# running with --user-data-dir=/tmp/broker-chromium-profile + --remote-debugging
# -port=9222). Next broker spawns fresh chromium that fails to bind 9222 (port
# taken) → all CDP traffic still goes to the dead-broker-orphan → playwright
# connect_over_cdp times out 180s after WebSocket connects (the orphan replies
# to /devtools/browser/<uuid> handshake but never to subsequent CDP commands
# because its parent renderer process tree is half-dead and spamming SSL
# net_error -178). Downstream visible failure is signup_username_field_missing
# but real cause is broker-chromium attach hang.
_kill_orphan_broker_chromium() {
  local pid
  # 1) anything whose cmdline mentions remote-debugging-port=9222 (the broker chromium tree)
  for pid in $(pgrep -f "remote-debugging-port=9222" 2>/dev/null); do
    kill -9 "$pid" 2>/dev/null && echo "[start-browser-model] killed orphan chromium pid=$pid (--remote-debugging-port=9222)"
  done
  # 2) anything still bound to :9222 (extra safety)
  pid=$(ss -lntp 2>/dev/null | grep -oP "127\.0\.0\.1:9222[^,]*pid=\K[0-9]+" | head -1)
  if [[ -n "$pid" ]]; then
    kill -9 "$pid" 2>/dev/null && echo "[start-browser-model] killed leftover pid=$pid still bound to :9222"
  fi
  # 3) wipe singleton locks in shared user-data-dir so new chromium does not refuse to start
  rm -f /tmp/broker-chromium-profile/Singleton* 2>/dev/null
  # 4) brief delay for kernel to release port + FDs
  sleep 0.5
}
_kill_orphan_broker_chromium

# v7.66 — ensure dbus system socket exists (chromium D-Bus FATAL fix)
if [ ! -S /var/run/dbus/system_bus_socket ] || ! pgrep -f "dbus-daemon --system --fork" >/dev/null 2>&1; then
  mkdir -p /var/run/dbus
  /usr/bin/dbus-daemon --system --fork 2>/dev/null || true
fi

exec node --enable-source-maps /root/browser-model/artifacts/api-server/dist/index.mjs
