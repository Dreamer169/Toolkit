#!/bin/bash
# 代理守护脚本 v2 — 同时监控 Tor(9050) + WARP(40000)
# Tor:  挂了 → systemctl restart tor
# WARP: 挂了 → warp-cli connect (Proxy模式，不影响系统路由/SSH)
LOG=/tmp/toolkit_logs/proxy-watchdog.log
mkdir -p /tmp/toolkit_logs

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a $LOG; }

log "proxy-watchdog 启动，监控 Tor:9050 + WARP:40000"
TOR_FAIL=0
WARP_FAIL=0

while true; do
  # === Tor 检测 ===
  if nc -z 127.0.0.1 9050 2>/dev/null; then
    [ $TOR_FAIL -gt 0 ] && log "Tor 已恢复 (9050 正常)" && TOR_FAIL=0
  else
    TOR_FAIL=$((TOR_FAIL+1))
    log "Tor 9050 无响应 (${TOR_FAIL}次)"
    if [ $TOR_FAIL -ge 2 ]; then
      log "重启 Tor..."
      systemctl restart tor
      sleep 15
      if nc -z 127.0.0.1 9050 2>/dev/null; then
        log "Tor 重启成功"
        TOR_FAIL=0
      else
        log "Tor 重启后仍无响应，60s后再试"
      fi
    fi
  fi

  # === WARP 检测 ===
  if nc -z 127.0.0.1 40000 2>/dev/null; then
    [ $WARP_FAIL -gt 0 ] && log "WARP 已恢复 (40000 正常)" && WARP_FAIL=0
  else
    WARP_FAIL=$((WARP_FAIL+1))
    log "WARP 40000 无响应 (${WARP_FAIL}次)"
    if [ $WARP_FAIL -ge 2 ]; then
      log "尝试重连 WARP..."
      warp-cli --accept-tos connect 2>&1 | tail -1
      sleep 10
      if nc -z 127.0.0.1 40000 2>/dev/null; then
        log "WARP 重连成功"
        WARP_FAIL=0
      else
        # 服务挂了就重启
        log "WARP 重连失败，重启 warp-svc..."
        systemctl restart warp-svc
        sleep 15
        warp-cli --accept-tos connect 2>/dev/null
        sleep 5
        nc -z 127.0.0.1 40000 2>/dev/null && log "WARP 重启后恢复" && WARP_FAIL=0 || log "WARP 仍无响应"
      fi
    fi
  fi

  sleep 60
done
