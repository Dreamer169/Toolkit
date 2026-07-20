#!/bin/bash
# IAM Worker 健康检查 v3 — 只监控，不写xray.json
# iam.*域名全部是CF橙云代理，nslookup只会返回CF Anycast IP（104.21.x.x/172.67.x.x）
# xray.json的IP更新由xray-update-bestcfip.js每4小时从真实优选源更新，healthcheck不再干预

LOG=/tmp/toolkit_logs/iam-healthcheck.log
STATE=/tmp/toolkit_logs/iam-healthy-domains.state
DOMAINS=("iam.jimhacker.qzz.io" "iam.jimhacker.eu.cc" "iam.jimhacker.us.ci")

mkdir -p /tmp/toolkit_logs
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a $LOG; }

HEALTHY_DOMAINS=()
FAILED_DOMAINS=()

for DOMAIN in "${DOMAINS[@]}"; do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "https://${DOMAIN}/jimhacker" 2>/dev/null)
  if [ "$HTTP_CODE" = "200" ]; then
    HEALTHY_DOMAINS+=("$DOMAIN")
    log "OK   $DOMAIN HTTP=$HTTP_CODE"
  else
    FAILED_DOMAINS+=("$DOMAIN")
    log "FAIL $DOMAIN HTTP=$HTTP_CODE"
  fi
done

if [ ${#HEALTHY_DOMAINS[@]} -eq 0 ]; then
  log "ALL_DOWN: 所有IAM源不可用"
  tail -200 $LOG > ${LOG}.tmp && mv ${LOG}.tmp $LOG
  exit 1
fi

CURRENT_STATE=$(cat "$STATE" 2>/dev/null | sort | tr n ,)
NEW_STATE=$(printf %sn "${HEALTHY_DOMAINS[@]}" | sort | tr n ,)

if [ "$CURRENT_STATE" = "$NEW_STATE" ]; then
  log "NO_CHANGE: 健康域名集合未变 (${NEW_STATE%,})"
else
  printf %sn "${HEALTHY_DOMAINS[@]}" > "$STATE"
  log "CHANGE: $CURRENT_STATE -> $NEW_STATE (仅记录，IP由xray-update-bestcfip.js管理)"
fi

tail -300 $LOG > ${LOG}.tmp && mv ${LOG}.tmp $LOG
