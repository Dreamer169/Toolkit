#!/bin/bash
# v10 — browser-model 启动脚本 (两台服务器通用)
# v10 新增:
#   - 两阶段 picker: Pass-1 住宅代理 (tp-* ports 10910-10916) 优先
#     Pass-2 ISP/数据中心回退 (HKBN/HGC/Fourplex/M247/Datacamp/HKT)
#   - 静态 tier 分类 (基于端口名称, 不依赖不可靠的外部 IP API)
#   - exit.json 增加 tier 字段 (residential / datacenter / direct)
#   - 兼容 .248 (v9) 和 .69 (v8.64) 两台服务器

export PORT=8092
export NODE_ENV=production
export PLAYWRIGHT_BROWSERS_PATH=/data/cache/ms-playwright
export REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE=/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome

# v9: Locale 强制 en-US（消除 Linux zh_CN.UTF-8 中文指纹泄漏）
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
export LANGUAGE=en_US:en

export FRONTEND_DIR=/data/browser-model/public

# ── CF IP 过滤 ────────────────────────────────────────────────────────────
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

# ── Replit 可达性探针 ─────────────────────────────────────────────────────
_probe_replit_reachable() {
  local proxy_arg="$1" url="https://replit.com/signup"
  local out rc body
  if [[ -n "$proxy_arg" ]]; then
    out=$(curl -s -w "\n%{http_code}" --max-time 10 --socks5 "$proxy_arg" "$url" 2>/dev/null)
  else
    out=$(curl -s -w "\n%{http_code}" --max-time 10 "$url" 2>/dev/null)
  fi
  rc=$(echo "$out" | tail -1)
  body=$(echo "$out" | head -c 600)
  case "$rc" in
    2*|3*) echo "replit-200/30x"; return 0 ;;
    403)
      if echo "$body" | grep -q "Just a moment"; then
        echo "cf-js-challenge-acceptable"; return 0
      fi
      echo "replit-403-banned"; return 1 ;;
    *) echo "replit-unreachable($rc)"; return 1 ;;
  esac
}

# ── v10: 两阶段 proxy picker ─────────────────────────────────────────────
# 住宅代理 (tp-*) 优先: iphey Trustworthy 历史更佳
# 数据中心/ISP 回退: 功能可用但 iphey 评分较低
_pick_browser_proxy() {
  # 读取 cooldown
  _COOLED_PORTS=""
  if [[ -f /root/Toolkit/.local/port_cooldown.json ]]; then
    _COOLED_PORTS=$(python3 -c "
import json,time
try:
  d=json.load(open('/root/Toolkit/.local/port_cooldown.json'))
  now=time.time()*1000
  print(' '.join(p for p,t in d.get('bans',{}).items() if t>now))
except: pass
" 2>/dev/null)
    [[ -n "$_COOLED_PORTS" ]] && echo "[picker] cooldown skip: ${_COOLED_PORTS}" >&2
  fi
  _is_cooled() { [[ " $_COOLED_PORTS " == *" $1 "* ]]; }

  # 通用候选探针: 检查监听 + exit IP + CF过滤 + Replit可达性
  # stdout: "socks5://...|name@ip|tier"   返回 0=成功 1=失败
  _try_cand() {
    local port="$1" name="$2" tier="$3"
    _is_cooled "$port" && { echo "[picker] skip ${name}(${port}) — cooldown" >&2; return 1; }
    ss -tln 2>/dev/null | grep -qE "127\.0\.0\.1:${port}\b" \
      || { echo "[picker] skip ${name}(${port}) — not listening" >&2; return 1; }
    local EXIT
    EXIT=$(curl -s --max-time 8 --socks5 "127.0.0.1:${port}" \
           https://api.ipify.org 2>/dev/null | tr -d "[:space:]")
    [[ -z "$EXIT" ]] && { echo "[picker] skip ${name}(${port}) — exit probe failed" >&2; return 1; }
    _is_cf_ip "$EXIT" && { echo "[picker] skip ${name}(${port}) — CF IP ${EXIT}" >&2; return 1; }
    local replit_status
    replit_status=$(_probe_replit_reachable "127.0.0.1:${port}")
    [[ $? -ne 0 ]] && { echo "[picker] skip ${name}(${port}) — Replit unreachable: ${replit_status}" >&2; return 1; }
    echo "[picker] OK ${tier}: ${name}(${port}) exit=${EXIT}" >&2
    echo "socks5://127.0.0.1:${port}|${name}@${EXIT}|${tier}"
    return 0
  }

  local result

  # ── Pass 1: 住宅代理 (tp-*) — iphey Trustworthy 优先 ────────────────
  echo "[picker] Pass-1: residential proxies (tp-* 10910-10916)" >&2
  for cand in 10910:tp-US1 10911:tp-US2 10912:tp-US3 10916:tp-US4 10914:tp-UK 10915:tp-MX; do
    local port="${cand%%:*}" name="${cand##*:}"
    result=$(_try_cand "$port" "$name" "residential")
    if [[ $? -eq 0 ]]; then echo "$result"; return 0; fi
  done
  echo "[picker] Pass-1: no residential proxy available — falling back to ISP/DC" >&2

  # ── Pass 2: ISP/数据中心回退 — iphey Unreliable 但功能可用 ──────────
  echo "[picker] Pass-2: ISP/datacenter proxies (fallback)" >&2
  for cand in 10857:HKBN-HK 10859:HGC-HK 10853:Fourplex-US 10855:M247-GB 10851:Datacamp-US 10854:HKT-HK; do
    local port="${cand%%:*}" name="${cand##*:}"
    result=$(_try_cand "$port" "$name" "datacenter")
    if [[ $? -eq 0 ]]; then echo "$result"; return 0; fi
  done
  echo "[picker] Pass-2: no datacenter proxy available" >&2

  # ── Pass 3: 直连回退 ─────────────────────────────────────────────────
  local DIRECT_EXIT
  DIRECT_EXIT=$(curl -s --max-time 6 https://api.ipify.org 2>/dev/null | tr -d "[:space:]")
  echo "[picker] DIRECT fallback exit=${DIRECT_EXIT}" >&2
  echo "|DIRECT-VPS@${DIRECT_EXIT:-unknown}|direct"
}

# ── 执行 picker ───────────────────────────────────────────────────────────
_picked="$(_pick_browser_proxy)"
_proxy_tier="${_picked##*|}"
_picked_core="${_picked%|*}"
export BROWSER_PROXY="${_picked_core%%|*}"
_brox="$BROWSER_PROXY"

if [[ -z "$_brox" ]]; then
  export BROKER_EXIT_FAMILY="direct"; unset BROKER_EXIT_SOCKS_PORT
elif [[ "$_brox" == *":40000" ]]; then
  export BROKER_EXIT_FAMILY="warp"; unset BROKER_EXIT_SOCKS_PORT
else
  export BROKER_EXIT_FAMILY="socks"
  export BROKER_EXIT_SOCKS_PORT="$(echo "$_brox" | sed -E 's/.*:([0-9]+).*/\1/')"
fi

_brox_label="${_picked_core##*|}"
echo "[start-browser-model v10] BROWSER_PROXY=${BROWSER_PROXY} (${_brox_label})"
echo "[start-browser-model v10] BROKER_EXIT_FAMILY=${BROKER_EXIT_FAMILY} SOCKS_PORT=${BROKER_EXIT_SOCKS_PORT:-N/A}"
echo "[start-browser-model v10] PROXY_TIER=${_proxy_tier}"
echo "[start-browser-model v10] LANG=${LANG} LC_ALL=${LC_ALL}"
echo "[start-browser-model v10] CAPSOLVER_API_KEY=$([ -n "${CAPSOLVER_API_KEY}" ] && echo 'SET' || echo 'not set')"

mkdir -p /tmp/replit-broker
printf '{"family":"%s","port":"%s","tier":"%s","ts":%d}\n' \
  "${BROKER_EXIT_FAMILY}" "${BROKER_EXIT_SOCKS_PORT:-}" "${_proxy_tier}" "$(date +%s)" \
  > /tmp/replit-broker/exit.json

# ── dbus ─────────────────────────────────────────────────────────────────
if [ ! -S /var/run/dbus/system_bus_socket ] || ! pgrep -f "dbus-daemon --system --fork" >/dev/null 2>&1; then
  mkdir -p /var/run/dbus
  /usr/bin/dbus-daemon --system --fork 2>/dev/null || true
fi

# ── Xvfb 多显示器探针 + 健康检查 ─────────────────────────────────────────
_xvfb_display=""
for _xd in 99 100 77 102; do
  if [ -S "/tmp/.X11-unix/X${_xd}" ] && xdpyinfo -display ":${_xd}" >/dev/null 2>&1; then
    _xvfb_display=":${_xd}"
    echo "[start-browser-model v10] Xvfb healthy on :${_xd}"
    break
  fi
done

if [[ -z "$_xvfb_display" ]]; then
  echo "[start-browser-model v10] Xvfb not found — starting :99"
  pkill -f "Xvfb :99" 2>/dev/null; sleep 0.5
  Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render &
  sleep 2
  if [ -S "/tmp/.X11-unix/X99" ]; then
    _xvfb_display=":99"
    echo "[start-browser-model v10] Xvfb :99 started OK"
  else
    echo "[start-browser-model v10] Xvfb :99 start FAILED — running headless"
  fi
fi
[[ -n "$_xvfb_display" ]] && export DISPLAY="$_xvfb_display"

# ── 彻底清理孤儿 Chromium ─────────────────────────────────────────────────
for _pid in $(pgrep -f "remote-debugging-port=9222" 2>/dev/null); do
  kill -9 "$_pid" 2>/dev/null && echo "[start-browser-model v10] killed orphan chromium(9222) pid=$_pid"
done
_cdp_pid=$(ss -lntp 2>/dev/null | grep ":9222" | grep -oP "pid=\K[0-9]+" | head -1)
[[ -n "$_cdp_pid" ]] && kill -9 "$_cdp_pid" 2>/dev/null

for _pid in $(pgrep -f "chrome.*--no-sandbox" 2>/dev/null | head -10); do
  _age=$(ps -o etimes= -p "$_pid" 2>/dev/null | tr -d ' ')
  if [[ "${_age:-0}" -gt 300 ]]; then
    kill -9 "$_pid" 2>/dev/null && echo "[start-browser-model v10] killed stale chrome pid=$_pid age=${_age}s"
  fi
done

rm -f /tmp/broker-chromium-profile/Singleton* 2>/dev/null
rm -f /tmp/pydoll-*/Singleton* 2>/dev/null
sleep 0.5

# ── release port 8092 ────────────────────────────────────────────────────
_stale=$(ss -lntp 2>/dev/null | grep ":8092" | grep -oP "pid=\K[0-9]+" | head -1)
[[ -n "$_stale" ]] && kill -9 "$_stale" 2>/dev/null && echo "[start-browser-model v10] cleared stale :8092"
sleep 0.3

exec node --enable-source-maps /data/browser-model/dist/index.mjs
