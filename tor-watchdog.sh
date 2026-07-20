#!/bin/bash
# Tor 守护脚本 — 检测端口存活，挂掉自动 restart
LOG=/tmp/toolkit_logs/tor-watchdog.log
mkdir -p /tmp/toolkit_logs

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a $LOG; }

log "tor-watchdog 启动，监控端口 9050"
FAIL=0

while true; do
  # 用 nc 探测 9050 是否响应
  if nc -z 127.0.0.1 9050 2>/dev/null; then
    [ $FAIL -gt 0 ] && log "Tor 已恢复 (9050 正常)" && FAIL=0
  else
    FAIL=$((FAIL+1))
    log "Tor 9050 无响应 (${FAIL}次)"
    if [ $FAIL -ge 2 ]; then
      log "重启 Tor..."
      systemctl restart tor
      sleep 15
      if nc -z 127.0.0.1 9050 2>/dev/null; then
        log "Tor 重启成功"
        FAIL=0
      else
        log "Tor 重启后仍无响应，60s后再试"
      fi
    fi
  fi
  sleep 60
done
