#!/bin/bash
# obvious_relay_keepalive.sh — 监控SOCKS5住宅中继状态, 挂了自动重启pm2进程
SCRIPT_DIR="/root/Toolkit/scripts"
LOG_FILE="/tmp/obvious_relay_keepalive.log"
CHECK_INTERVAL=120

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
log "obvious中继守护启动"

while true; do
  STATUS=$(python3 "$SCRIPT_DIR/obvious_proxy_relay.py" --health 2>/dev/null)
  if echo "$STATUS" | grep -q "RELAY_STATUS=OK"; then
    log "中继正常: $(echo "$STATUS" | grep RELAY_STATUS)"
  else
    log "中继异常! 尝试重启 obvious-proxy-relay ..."
    pm2 restart obvious-proxy-relay 2>&1 | tee -a "$LOG_FILE"
    sleep 10
    # 二次验证
    STATUS2=$(python3 "$SCRIPT_DIR/obvious_proxy_relay.py" --health 2>/dev/null)
    if echo "$STATUS2" | grep -q "RELAY_STATUS=OK"; then
      log "重启后恢复正常"
    else
      log "重启后仍然异常, 需人工介入"
    fi
  fi
  sleep "$CHECK_INTERVAL"
done
