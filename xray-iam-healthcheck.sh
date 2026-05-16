#!/bin/bash
# IAM Worker 健康检查 + 自动切换
# 每次运行: 检查三源HTTP状态, 过滤429, 用健康源IP更新xray.json

LOG=/tmp/toolkit_logs/iam-healthcheck.log
XRAY_CFG=/root/Toolkit/xray.json
DOMAINS=("iam.jimhacker.qzz.io" "iam.jimhacker.eu.cc" "iam.jimhacker.us.ci")

mkdir -p /tmp/toolkit_logs
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a $LOG; }

HEALTHY_IPS=()
FAILED_DOMAINS=()

for DOMAIN in "${DOMAINS[@]}"; do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "https://${DOMAIN}/jimhacker" 2>/dev/null)
  IPS=$(nslookup "$DOMAIN" 2>/dev/null | grep 'Address:' | grep -v '#' | awk '{print $2}' | grep -v ':' | head -1)
  if [ "$HTTP_CODE" = "200" ] && [ -n "$IPS" ]; then
    HEALTHY_IPS+=("$IPS")
    log "OK $DOMAIN ($IPS) HTTP $HTTP_CODE"
  else
    FAILED_DOMAINS+=("$DOMAIN")
    log "FAIL $DOMAIN ($IPS) HTTP $HTTP_CODE"
  fi
done

if [ ${#HEALTHY_IPS[@]} -eq 0 ]; then
  log "ALL_DOWN: 所有IAM源不可用，保留当前配置"
  exit 1
fi

CURRENT_IPS=$(node -e "
  const cfg = JSON.parse(require('fs').readFileSync('$XRAY_CFG','utf8'));
  const ips = cfg.outbounds.filter(o=>o.settings&&o.settings.vnext).map(o=>o.settings.vnext[0].address);
  console.log([...new Set(ips)].join(','));
" 2>/dev/null)

NEED_UPDATE=0
IFS=',' read -ra CURR_ARR <<< "$CURRENT_IPS"
for IP in "${CURR_ARR[@]}"; do
  FOUND=0
  for HIP in "${HEALTHY_IPS[@]}"; do [ "$IP" = "$HIP" ] && FOUND=1 && break; done
  [ $FOUND -eq 0 ] && NEED_UPDATE=1 && break
done

if [ $NEED_UPDATE -eq 0 ] && [ ${#FAILED_DOMAINS[@]} -eq 0 ]; then
  log "ALL_OK: 无需更新"
  exit 0
fi

IP_JSON=$(printf '%s\n' "${HEALTHY_IPS[@]}" | node -e "
  const l=[];
  process.stdin.on('data',d=>l.push(...d.toString().trim().split('\n').filter(Boolean)));
  process.stdin.on('end',()=>console.log(JSON.stringify(l)));
")

HEALTHY_STR=$(IFS=,; echo "${HEALTHY_IPS[*]}")
log "UPDATE: 健康IP=$HEALTHY_STR 跳过=${FAILED_DOMAINS[*]}"

node -e "
  const fs = require('fs');
  const cfg = JSON.parse(fs.readFileSync('$XRAY_CFG','utf8'));
  const ips = $IP_JSON;
  let i = 0;
  cfg.outbounds.forEach(ob => {
    if(ob.settings && ob.settings.vnext) { ob.settings.vnext[0].address = ips[i % ips.length]; i++; }
  });
  fs.writeFileSync('$XRAY_CFG', JSON.stringify(cfg,null,2));
  console.log('Updated: ' + ips.join(', '));
" 2>&1 | tee -a $LOG

cp /root/Toolkit/xray.json /usr/local/etc/xray/config.json 2>/dev/null
pm2 reload xray 2>/dev/null && log "RELOAD: pm2 reload xray OK"

tail -300 $LOG > ${LOG}.tmp && mv ${LOG}.tmp $LOG
