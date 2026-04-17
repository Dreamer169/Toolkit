#!/usr/bin/env bash
# =============================================================================
# 新 Replit 账号 → 子节点 自动上线脚本
# 用法：在 Replit Shell 里运行一次即完成注册
#   curl -s https://raw.githubusercontent.com/Dreamer169/Toolkit/main/scripts/setup-subnode.sh | bash
# 或者已 fork 时直接运行：bash scripts/setup-subnode.sh
# =============================================================================
set -euo pipefail

REMOTE_GATEWAY="http://45.205.27.69:8080/api/gateway"
EXEC_SECRET="${EXEC_SECRET:-$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p)}"

echo "=== Replit 子节点上线脚本 ==="
echo ""

# 1. 获取当前工作区的公开 URL
if [ -n "${REPLIT_DEV_DOMAIN:-}" ]; then
  GATEWAY_URL="https://${REPLIT_DEV_DOMAIN}/api/gateway"
  echo "[1/5] 当前工作区 URL: $GATEWAY_URL"
  echo "      注意：此为开发 URL，工作区睡眠后失效"
  echo "      建议部署（Deploy）后使用 xxx.replit.app URL"
elif [ -n "${REPLIT_DOMAINS:-}" ]; then
  DOMAIN=$(echo "$REPLIT_DOMAINS" | cut -d',' -f1 | xargs)
  GATEWAY_URL="https://${DOMAIN}/api/gateway"
  echo "[1/5] 当前工作区 URL: $GATEWAY_URL"
else
  echo "[1/5] 无法检测工作区域名，请手动指定:"
  read -rp "  Gateway URL: " GATEWAY_URL
fi

# 2. 确保本地 api-server 在运行
echo ""
echo "[2/5] 检查本地 API Server..."
if curl -sf "http://localhost:${PORT:-8080}/api/gateway/health" > /dev/null 2>&1; then
  echo "      API Server 已运行 (port ${PORT:-8080})"
else
  echo "      未运行，尝试启动..."
  cd /home/runner/workspace 2>/dev/null || cd ~/workspace 2>/dev/null || true
  pnpm --filter @workspace/api-server run dev &
  sleep 8
  if curl -sf "http://localhost:${PORT:-8080}/api/gateway/health" > /dev/null 2>&1; then
    echo "      启动成功"
  else
    echo "      启动失败，请手动在 Replit 界面启动 api-server 工作流后重试"
    exit 1
  fi
fi

# 3. 写入 EXEC_SECRET 到 .env（让远端可以通过 /exec 端点控制此节点）
echo ""
echo "[3/5] 设置 EXEC_SECRET..."
ENV_FILE="/home/runner/workspace/.env"
if [ -f "$ENV_FILE" ] && grep -q "EXEC_SECRET" "$ENV_FILE"; then
  EXEC_SECRET=$(grep "^EXEC_SECRET=" "$ENV_FILE" | cut -d'=' -f2 | head -1)
  echo "      已有 EXEC_SECRET（复用）"
else
  echo "EXEC_SECRET=${EXEC_SECRET}" >> "$ENV_FILE"
  echo "      已写入 EXEC_SECRET 到 .env"
fi

# 4. 向远端服务器自注册
echo ""
echo "[4/5] 向远端网关注册..."
RESULT=$(curl -sf -X POST "$REMOTE_GATEWAY/self-register" \
  -H "Content-Type: application/json" \
  -d "{\"gatewayUrl\":\"$GATEWAY_URL\",\"name\":\"Replit($(hostname -s 2>/dev/null || echo auto))\",\"execSecret\":\"$EXEC_SECRET\"}" 2>&1) || true

if echo "$RESULT" | grep -q '"ok":true'; then
  NODE_ID=$(echo "$RESULT" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
  echo "      注册成功！节点 ID: $NODE_ID"
elif echo "$RESULT" | grep -q 'already-registered'; then
  echo "      已在节点池中（无需重复注册）"
else
  echo "      注册失败（远端可能暂时不可达）"
  echo "      响应: $(echo "$RESULT" | head -c 200)"
  echo "      你可以稍后手动注册："
  echo "      curl -X POST $REMOTE_GATEWAY/self-register \\"
  echo "        -H 'Content-Type: application/json' \\"
  echo "        -d '{\"gatewayUrl\":\"$GATEWAY_URL\"}'"
fi

# 5. 验证连通性
echo ""
echo "[5/5] 验证..."
HEALTH=$(curl -sf "$GATEWAY_URL/health" 2>/dev/null || echo '{}')
NODES=$(echo "$HEALTH" | grep -o '"nodes":[0-9]*' | head -1 | cut -d':' -f2)
if [ -n "$NODES" ]; then
  echo "      本节点健康，gateway 节点数: $NODES"
else
  echo "      无法探测本节点（URL 可能不可达，先确保 API Server 已运行）"
fi

echo ""
echo "=== 完成 ==="
echo ""
echo "  当前 Gateway URL: $GATEWAY_URL"
echo "  远端管理地址:     $REMOTE_GATEWAY"
echo "  节点池状态:       $REMOTE_GATEWAY/health"
echo ""
echo "添加更多子节点时，在其他 Replit 账号上同样运行此脚本即可。"
echo "所有子节点共享同一个 45.205.27.69 节点池，互相作为 fallback。"
