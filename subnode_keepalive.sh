#!/bin/bash
# VPS -> Replit 子节点入站保活脚本
# 每 3 分钟发送 GET /api/health，防止 Replit 实例因无流量休眠
# 启动延迟 30s，避免与 PM2/Replit 同时重启时首次 ping 必失

SUBNODE_URL="https://e30c0ae2-f8c5-4be1-ac08-513f36159e84-00-2mfc7zpe4ng5o.picard.replit.dev/api/health"
INTERVAL=180
STARTUP_DELAY=30

# 首次启动等待（给 Replit 足够时间响应）
sleep $STARTUP_DELAY

while true; do
  RESP=$(curl -sf --max-time 12 "$SUBNODE_URL" 2>/dev/null)
  if [ -n "$RESP" ]; then
    OK=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get(ok,?))" 2>/dev/null)
    echo "$(date +%H:%M:%S) [keepalive] ok=${OK:-?} sessions=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get(sessions,0))" 2>/dev/null)"
  else
    echo "$(date +%H:%M:%S) [keepalive] UNREACHABLE"
  fi
  sleep $INTERVAL
done
