#!/bin/bash
# Xray IP 自动守护脚本：检测代理失效后自动换IP并重启
LOG=/tmp/toolkit_logs/xray-watchdog.log
XRAY_CFG=/root/Toolkit/xray.json
DOMAIN=iam.jimhacker.qzz.io

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a $LOG; }

get_fresh_ips() {
  DNS_IPS=$(nslookup $DOMAIN 2>/dev/null | grep 'Address:' | grep -v '#' | awk '{print $2}' | grep -v ':')
  echo "$DNS_IPS" | tr ' ' '\n' | grep -v '^$' | head -4
}

test_proxy() {
  OUT=$(curl -s --proxy socks5://127.0.0.1:10808 --connect-timeout 8 https://api.ipify.org 2>/dev/null)
  [ -n "$OUT" ] && echo "$OUT" || echo ""
}

update_xray_ips() {
  NEW_IPS=($@)
  COUNT=${#NEW_IPS[@]}
  [ $COUNT -eq 0 ] && return 1
  IP_JSON=$(printf '%s\n' "${NEW_IPS[@]}" | node -e "const l=[];process.stdin.on('data',d=>l.push(...d.toString().trim().split('\n')));process.stdin.on('end',()=>console.log(JSON.stringify(l)))")
  node -e "
    const fs = require('fs');
    const cfg = JSON.parse(fs.readFileSync('$XRAY_CFG','utf8'));
    const ips = $IP_JSON;
    cfg.outbounds.forEach((ob,i) => {
      if(ob.settings&&ob.settings.vnext){
        ob.settings.vnext[0].address = ips[i % ips.length];
      }
    });
    fs.writeFileSync('$XRAY_CFG', JSON.stringify(cfg,null,2));
    console.log('Updated with IPs: ' + ips.join(', '));
  " 2>&1
}

log "守护脚本启动"
FAIL_COUNT=0

while true; do
  PROXY_OUT=$(test_proxy)
  if [ -z "$PROXY_OUT" ]; then
    FAIL_COUNT=$((FAIL_COUNT+1))
    log "代理检测失败 (${FAIL_COUNT}次) - 尝试修复..."
    if [ $FAIL_COUNT -ge 2 ]; then
      FRESH_IPS=$(get_fresh_ips)
      log "获取到新IP: $FRESH_IPS"
      update_xray_ips $FRESH_IPS
      # 用 pm2 reload 而非 pkill，避免 pm2 计入重启次数且不产生双进程
      pm2 reload xray
      sleep 4
      PROXY_OUT=$(test_proxy)
      if [ -n "$PROXY_OUT" ]; then
        log "修复成功，出口IP: $PROXY_OUT"
        FAIL_COUNT=0
      else
        log "修复失败，60秒后再试"
      fi
    fi
  else
    [ $FAIL_COUNT -gt 0 ] && log "代理恢复正常，出口IP: $PROXY_OUT"
    FAIL_COUNT=0
  fi
  sleep 120
done
