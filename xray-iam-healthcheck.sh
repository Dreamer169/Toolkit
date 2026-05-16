#!/bin/bash
# IAM Worker 健康检查 v2 — 只监控+按需切换，不每次强制 reload
# 每次运行: 检查三源HTTP状态, 过滤429, 写入状态文件; 仅当健康域名集合变化时才写 xray.json + reload

LOG=/tmp/toolkit_logs/iam-healthcheck.log
STATE=/tmp/toolkit_logs/iam-healthy-domains.state
XRAY_CFG=/root/Toolkit/xray.json
DOMAINS=("iam.jimhacker.qzz.io" "iam.jimhacker.eu.cc" "iam.jimhacker.us.ci")

mkdir -p /tmp/toolkit_logs
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a $LOG; }

HEALTHY_DOMAINS=()
HEALTHY_IPS=()
FAILED_DOMAINS=()

for DOMAIN in "${DOMAINS[@]}"; do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "https://${DOMAIN}/jimhacker" 2>/dev/null)
  # 取第一个 A 记录（过滤 IPv6 和 "#53" 行）
  IP=$(nslookup "$DOMAIN" 2>/dev/null | awk '/^Address:/ && !/^Address:.*#/{print $2; exit}')
  if [ "$HTTP_CODE" = "200" ] && [ -n "$IP" ]; then
    HEALTHY_DOMAINS+=("$DOMAIN")
    HEALTHY_IPS+=("$IP")
    log "OK   $DOMAIN ($IP)"
  else
    FAILED_DOMAINS+=("$DOMAIN")
    log "FAIL $DOMAIN ($IP) HTTP=$HTTP_CODE"
  fi
done

if [ ${#HEALTHY_DOMAINS[@]} -eq 0 ]; then
  log "ALL_DOWN: 所有IAM源不可用，保留当前配置"
  exit 1
fi

# 当前健康域名集合（排序后比较，与顺序无关）
CURRENT_STATE=$(cat "$STATE" 2>/dev/null | sort | tr '\n' ',')
NEW_STATE=$(printf '%s\n' "${HEALTHY_DOMAINS[@]}" | sort | tr '\n' ',')

if [ "$CURRENT_STATE" = "$NEW_STATE" ]; then
  log "NO_CHANGE: 健康域名集合未变 (${NEW_STATE%,})"
  tail -200 $LOG > ${LOG}.tmp && mv ${LOG}.tmp $LOG
  exit 0
fi

# 健康域名集合已变化，更新 state 文件
printf '%s\n' "${HEALTHY_DOMAINS[@]}" > "$STATE"
log "CHANGE: $CURRENT_STATE -> $NEW_STATE"

# 更新 xray.json 中所有 VLESS outbound 的 address（若有）
VLESS_COUNT=$(node -e "
  try {
    const cfg = JSON.parse(require('fs').readFileSync('$XRAY_CFG','utf8'));
    const n = cfg.outbounds.filter(o=>o.settings&&o.settings.vnext).length;
    process.stdout.write(String(n));
  } catch(e){ process.stdout.write('0'); }
" 2>/dev/null)

if [ "$VLESS_COUNT" -gt 0 ] 2>/dev/null; then
  IP_JSON=$(printf '%s\n' "${HEALTHY_IPS[@]}" | node -e "
    const l=[];
    process.stdin.on('data',d=>l.push(...d.toString().trim().split('\n').filter(Boolean)));
    process.stdin.on('end',()=>process.stdout.write(JSON.stringify(l)));
  ")
  node -e "
    const fs=require('fs');
    const cfg=JSON.parse(fs.readFileSync('$XRAY_CFG','utf8'));
    const ips=$IP_JSON; let i=0;
    cfg.outbounds.forEach(ob=>{
      if(ob.settings&&ob.settings.vnext){ ob.settings.vnext[0].address=ips[i%ips.length]; i++; }
    });
    fs.writeFileSync('$XRAY_CFG',JSON.stringify(cfg,null,2));
    process.stdout.write('Updated '+i+' outbounds: '+ips.join(', ')+'\n');
  " 2>&1 | tee -a $LOG
  cp /root/Toolkit/xray.json /usr/local/etc/xray/config.json 2>/dev/null
  pm2 reload xray 2>/dev/null && log "RELOAD: pm2 reload xray OK"
else
  log "INFO: xray.json 无 VLESS 出口，跳过 address 更新（仅记录健康域名变化）"
fi

tail -300 $LOG > ${LOG}.tmp && mv ${LOG}.tmp $LOG
