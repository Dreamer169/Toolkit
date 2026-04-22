#!/bin/bash
# Codespace 自动启动脚本 - 由 devcontainer postStartCommand 调用
set -e
LOG=/tmp/autostart.log
exec > >(tee -a $LOG) 2>&1

echo "[$(date)] === Codespace 自动启动 ==="

# 等待系统就绪
sleep 3

# 启动 PostgreSQL
echo "[1/5] 启动 PostgreSQL..."
sudo bash -c 'su -s /bin/bash postgres -c "pg_ctlcluster 16 main start"' 2>/dev/null || echo "PostgreSQL 已在运行"
sleep 2

# 初始化数据库
export DATABASE_URL="postgresql://postgres:postgres@localhost/toolkit"
sudo -u postgres psql -c "CREATE DATABASE toolkit" 2>/dev/null || true
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres'" 2>/dev/null || true
psql "$DATABASE_URL" -f /dev/stdin 2>/dev/null << SQL
CREATE TABLE IF NOT EXISTS accounts (id SERIAL PRIMARY KEY, platform TEXT NOT NULL, email TEXT NOT NULL, password TEXT, token TEXT, refresh_token TEXT, status TEXT DEFAULT 'active', created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(platform, email));
CREATE TABLE IF NOT EXISTS identities (id SERIAL PRIMARY KEY, first_name TEXT, last_name TEXT, email TEXT, phone TEXT, address TEXT, city TEXT, state TEXT, zip TEXT, country TEXT, birth_date TEXT, username TEXT, password TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS temp_emails (id SERIAL PRIMARY KEY, email TEXT NOT NULL, password TEXT, provider TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS configs (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS proxies (id SERIAL PRIMARY KEY, raw TEXT, formatted TEXT UNIQUE, host TEXT, port INT, username TEXT, password TEXT, status TEXT DEFAULT 'idle', used_count INT DEFAULT 0, last_used TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS job_snapshots (job_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'running', started_at BIGINT NOT NULL, logs JSONB NOT NULL DEFAULT '[]', accounts JSONB NOT NULL DEFAULT '[]', exit_code INT, updated_at TIMESTAMPTZ DEFAULT NOW());
SQL
echo "  数据库 OK"

# 确保 API Server 已编译
echo "[2/5] 检查 API Server 编译..."
if [ ! -f /workspaces/Toolkit/artifacts/api-server/dist/index.mjs ]; then
  echo "  重新编译..."
  cd /workspaces/Toolkit
  pnpm --filter @workspace/api-server run build
fi
echo "  编译 OK"

# 更新 Xray IP
echo "[3/5] 更新 Xray IP..."
node /workspaces/Toolkit/xray-update-ip.js 2>/dev/null || true

# 启动 ngrok（PM2外单独启动因为需要先获取端口）
echo "[4/5] 启动 ngrok..."
pkill ngrok 2>/dev/null || true
sleep 1
nohup ngrok http 3000 --domain=recycling-tragedy-projector.ngrok-free.dev \
  --request-header-add "ngrok-skip-browser-warning:true" \
  --log=stdout > /tmp/toolkit_logs/ngrok.log 2>&1 &
echo "  ngrok 启动中..."

# PM2 启动所有服务
echo "[5/5] PM2 启动所有服务..."
mkdir -p /tmp/toolkit_logs
cd /workspaces/Toolkit
pm2 delete all 2>/dev/null || true
pm2 start ecosystem.config.cjs
pm2 save

sleep 8

# 验证
echo ""
echo "=== 服务状态 ==="
pm2 list
echo ""
curl -sf http://localhost:8080/api/healthz && echo "API OK" || echo "API 启动中..."
NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | grep -o public_url:[^]*' | head -1 | cut -d' -f4)
echo "访问地址: ${NGROK_URL:-https://recycling-tragedy-projector.ngrok-free.dev}"
echo "[$(date)] === 启动完成 ==="
