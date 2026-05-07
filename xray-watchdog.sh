#!/bin/bash
# Xray IP 自动守护脚本（v2: 改用 SS 静态端口探针，不再走 VLESS→jimhacker）
# 根因修复(2026-05-07): 原版用 socks5://127.0.0.1:10808 (VLESS→jimhacker) 探测
# api.ipify.org，每120秒耗1次jimhacker配额=720次/天。jimhacker挂时永远失败→
# 循环 reload xray，加剧配额耗尽。修法: 改用 SS 静态端口(10851/10853)探测，
# 完全绕开 jimhacker Worker。

mkdir -p /tmp/toolkit_logs
LOG=/tmp/toolkit_logs/xray-watchdog.log
XRAY_CFG=/root/Toolkit/xray.json
DOMAIN_PRIMARY=iam.jimhacker.qzz.io
DOMAIN_BACKUP=iam.jimhacker.eu.cc

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a $LOG; }

get_fresh_ips() {
  DNS_IPS=$(nslookup $DOMAIN 2>/dev/null | grep 'Address:' | grep -v '#' | awk '{print $2}' | grep -v ':')
  echo "$DNS_IPS" | tr ' ' '\n' | grep -v '^$' | head -4
}

# v2: 改用 SS 静态端口(10851=ss-in-1/edir2end)，不走 VLESS→jimhacker
test_proxy() {
  # 先试 10851，失败再试 10853
  for PORT in 10851 10853 10855; do
    OUT=$(curl -s --proxy socks5h://127.0.0.1:$PORT --connect-timeout 8 https://api.ipify.org 2>/dev/null)
    if [ -n "$OUT" ]; then
      echo "$OUT"
      return
    fi
  done
  echo ""
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

log "守护脚本启动 (v2: SS静态端口探针，不走jimhacker)"
FAIL_COUNT=0

while true; do
  PROXY_OUT=$(test_proxy)
  if [ -z "$PROXY_OUT" ]; then
    FAIL_COUNT=$((FAIL_COUNT+1))
    log "SS代理检测失败 (${FAIL_COUNT}次) - 等待..."
    if [ $FAIL_COUNT -ge 5 ]; then
      # SS持续失败才尝试更新VLESS的CF IP（不影响jimhacker配额）
      FRESH_IPS=$(get_fresh_ips)
      log "获取到新IP: $FRESH_IPS"
      update_xray_ips $FRESH_IPS
      cp /root/Toolkit/xray.json /usr/local/etc/xray/config.json
      pm2 reload xray 2>/dev/null
      sleep 4
      PROXY_OUT=$(test_proxy)
      if [ -n "$PROXY_OUT" ]; then
        log "修复成功，出口IP: $PROXY_OUT"
        FAIL_COUNT=0
      else
        log "修复失败，60秒后再试"
        FAIL_COUNT=3
      fi
    fi
  else
    [ $FAIL_COUNT -gt 0 ] && log "SS代理恢复正常，出口IP: $PROXY_OUT"
    FAIL_COUNT=0
  fi
  sleep 120
done
