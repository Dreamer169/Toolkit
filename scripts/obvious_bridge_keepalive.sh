#!/bin/bash
# obvious_bridge_keepalive.sh — 监控obvious沙箱SSH桥隧道状态, 断了自动重建
# pm2配置: pm2 start /root/Toolkit/scripts/obvious_bridge_keepalive.sh --name obvious-bridge-keepalive --interpreter bash

SCRIPT_DIR="/root/Toolkit/scripts"
LOG_FILE="/tmp/obvious_bridge_keepalive.log"
CHECK_INTERVAL=300  # 5分钟检查一次

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

log "obvious桥接守护启动"

while true; do
  # 通过obvious_ssh_bridge.py检查状态
  STATUS_OUT=$(cd "$SCRIPT_DIR" && python3 obvious_ssh_bridge.py --status --account eu-test1 2>/dev/null)
  
  if echo "$STATUS_OUT" | grep -q "EXIT_IP="; then
    EXIT_IP=$(echo "$STATUS_OUT" | grep "EXIT_IP=" | head -1 | cut -d= -f2)
    if [ "$EXIT_IP" = "FAILED" ] || [ "$EXIT_IP" = "UNREACHABLE" ] || [ -z "$EXIT_IP" ]; then
      log "隧道出口IP异常($EXIT_IP), 重建隧道..."
      cd "$SCRIPT_DIR" && python3 obvious_ssh_bridge.py --setup --account eu-test1 2>&1 | tail -20 | tee -a "$LOG_FILE"
    else
      log "隧道正常: EXIT_IP=$EXIT_IP"
    fi
  else
    log "无法获取状态, 尝试重建..."
    cd "$SCRIPT_DIR" && python3 obvious_ssh_bridge.py --setup --account eu-test1 2>&1 | tail -20 | tee -a "$LOG_FILE"
  fi
  
  sleep "$CHECK_INTERVAL"
done
