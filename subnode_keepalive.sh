#!/bin/bash
# 动态 keepalive：从网关 API 获取 ready 节点列表并 ping，避免硬编码 DEAD URL
GATEWAY_STATUS="http://45.205.27.69:8080/api/gateway/nodes/status"
INTERVAL=30
STARTUP_DELAY=30

sleep $STARTUP_DELAY

while true; do
  # 从 API 获取 ready 节点的 baseUrl 列表
  READY_URLS=$(curl -sf --max-time 8 "$GATEWAY_STATUS" 2>/dev/null     | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for n in d.get('nodes', []):
        if n.get('status') == 'ready' and n.get('baseUrl','').startswith('http'):
            print(n['baseUrl'] + '/api/gateway/health')
except: pass
" 2>/dev/null)

  if [ -z "$READY_URLS" ]; then
    echo "$(date +%H:%M:%S) [keepalive] WARN: could not fetch node list"
    sleep $INTERVAL
    continue
  fi

  while IFS= read -r NODE_URL; do
    RESP=$(curl -sf --max-time 12 -H "ngrok-skip-browser-warning: 1" "$NODE_URL" 2>/dev/null)
    if [ -n "$RESP" ]; then
      echo "$(date +%H:%M:%S) [keepalive] OK ${NODE_URL%%/api/*}"
    else
      echo "$(date +%H:%M:%S) [keepalive] DEAD $NODE_URL"
    fi
  done <<< "$READY_URLS"

  sleep $INTERVAL
done
