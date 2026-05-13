#!/bin/bash
# 安全重启 api-server，始终通过 ecosystem.config.cjs 保证 PORT=8081
set -e
ECOSYSTEM=/data/Toolkit/ecosystem.config.cjs

echo '[restart-api] 检查是否有孤儿 api-server 进程...'
ORPHAN_PID=
if [ -n "$ORPHAN_PID" ]; then
  echo "[restart-api] 发现占用8081的进程 PID=$ORPHAN_PID，先杀掉"
  kill -9 $ORPHAN_PID 2>/dev/null || true
  sleep 1
fi

echo '[restart-api] 通过 ecosystem 重载 api-server...'
pm2 reload $ECOSYSTEM --only api-server
pm2 save
echo '[restart-api] 完成，当前状态:'
pm2 show api-server | grep -E 'status|pid|PORT|uptime'
