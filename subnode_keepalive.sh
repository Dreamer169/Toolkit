#!/bin/bash
# 每 3 分钟对子节点发送一次 GET 入站请求，防止 Replit 实例休眠
# selfRegister 是出站请求（不能防止 Replit 休眠），本脚本从 VPS 侧发起入站请求

SUBNODE_URL="https://e30c0ae2-f8c5-4be1-ac08-513f36159e84-00-2mfc7zpe4ng5o.picard.replit.dev/api/health"
INTERVAL=180

while true; do
  STATUS=$(curl -sf --max-time 10 "$SUBNODE_URL" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get(ok,?))" 2>/dev/null)
  echo "$(date +%H:%M:%S) [subnode-keepalive] ping $SUBNODE_URL -> ok=${STATUS:-FAIL}"
  sleep $INTERVAL
done
