#!/bin/bash
set -e

LOG_DIR=/tmp/toolkit_logs
mkdir -p $LOG_DIR

echo "[0/6] 检查依赖环境..."
NEED_SETUP=0

# Python 包检查
for pkg in faker patchright playwright telegram aiohttp imapclient capsolver twocaptcha Pillow; do
  python3 -c "import $(echo $pkg | tr '-' '_' | tr '[:upper:]' '[:lower:]')" 2>/dev/null || NEED_SETUP=1
done

# 系统库检查
ldconfig -p | grep -q 'libasound.so.2' || NEED_SETUP=1
ldconfig -p | grep -q 'libatk-1.0.so.0' || NEED_SETUP=1

# 浏览器检查
[ -d "$HOME/.cache/ms-playwright/chromium-1208" ] || [ -d "$HOME/.cache/ms-playwright/chromium_headless_shell-1208" ] || NEED_SETUP=1

if [ $NEED_SETUP -eq 1 ]; then
  echo "  检测到环境不完整，运行 setup-env.sh..."
  bash /workspaces/Toolkit/setup-env.sh
else
  echo "  依赖检查完成 ✅"
fi

echo "[1/6] 启动 PostgreSQL..."
sudo bash -c 'su -s /bin/bash postgres -c "pg_ctlcluster 16 main start"' 2>/dev/null || echo "PostgreSQL 已在运行"
sleep 2


echo "[1.5/6] 初始化数据库表..."
export DATABASE_URL='postgresql://postgres:postgres@localhost/toolkit'
sudo -u postgres psql -c "CREATE DATABASE toolkit" 2>/dev/null || true
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres'" 2>/dev/null || true
psql "postgresql://postgres:postgres@localhost/toolkit" << 'SQL' 2>/dev/null
CREATE TABLE IF NOT EXISTS accounts (
  id SERIAL PRIMARY KEY,
  platform TEXT NOT NULL,
  email TEXT NOT NULL,
  password TEXT,
  token TEXT,
  refresh_token TEXT,
  status TEXT DEFAULT 'active',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(platform, email)
);
CREATE TABLE IF NOT EXISTS identities (
  id SERIAL PRIMARY KEY,
  first_name TEXT, last_name TEXT, email TEXT, phone TEXT,
  address TEXT, city TEXT, state TEXT, zip TEXT, country TEXT,
  birth_date TEXT, username TEXT, password TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS temp_emails (
  id SERIAL PRIMARY KEY,
  email TEXT NOT NULL, password TEXT, provider TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS configs (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS proxies (
  id SERIAL PRIMARY KEY,
  raw TEXT NOT NULL,
  formatted TEXT NOT NULL UNIQUE,
  host TEXT,
  port INT,
  username TEXT,
  password TEXT,
  status TEXT DEFAULT 'idle',
  used_count INT DEFAULT 0,
  last_used TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
-- 如果旧表存在，补充缺失列
ALTER TABLE proxies ADD COLUMN IF NOT EXISTS raw TEXT;
ALTER TABLE proxies ADD COLUMN IF NOT EXISTS formatted TEXT;
ALTER TABLE proxies ADD COLUMN IF NOT EXISTS host TEXT;
ALTER TABLE proxies ADD COLUMN IF NOT EXISTS port INT;
ALTER TABLE proxies ADD COLUMN IF NOT EXISTS username TEXT;
ALTER TABLE proxies ADD COLUMN IF NOT EXISTS password TEXT;
UPDATE proxies SET formatted = url, raw = url WHERE formatted IS NULL AND url IS NOT NULL;
CREATE TABLE IF NOT EXISTS job_snapshots (
  job_id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'running',
  started_at BIGINT NOT NULL,
  logs JSONB NOT NULL DEFAULT '[]',
  accounts JSONB NOT NULL DEFAULT '[]',
  exit_code INT,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
SQL
echo "  数据库表初始化完成 ✅"

echo "[1.6/6] 构建最新代码..."
cd /workspaces/Toolkit
pnpm --filter @workspace/api-server run build
pnpm --filter @workspace/ai-toolkit run build
echo "  最新代码构建完成 ✅"

echo "[2/6] 启动 API 服务器..."
pkill -f 'dist/index.mjs' 2>/dev/null || true
sleep 1
export DATABASE_URL='postgresql://postgres:postgres@localhost/toolkit'
export PORT=8080
cd /workspaces/Toolkit
nohup node --enable-source-maps ./artifacts/api-server/dist/index.mjs > $LOG_DIR/api.log 2>&1 &
echo $! > /tmp/api.pid
sleep 3
curl -sf http://localhost:8080/api/healthz > /dev/null && echo "  API 服务器 OK (port 8080)" || echo "  API 服务器启动失败，查看 $LOG_DIR/api.log"

echo "[3/6] 启动前端..."
pkill -f 'vite' 2>/dev/null || true
sleep 1
nohup pnpm --filter @workspace/ai-toolkit run dev > $LOG_DIR/frontend.log 2>&1 &
sleep 6
FRONTEND_PORT=$(grep -o 'localhost:[0-9]*' $LOG_DIR/frontend.log | head -1 | cut -d: -f2)
FRONTEND_PORT=${FRONTEND_PORT:-8081}
echo "  前端 OK (port $FRONTEND_PORT)"

echo "[3.5/6] 启动 OpenAI Pool..."
pkill -f 'openai_pool_orchestrator' 2>/dev/null || true
pkill -f '/workspaces/Toolkit/artifacts/openai-pool' 2>/dev/null || true
if command -v fuser >/dev/null 2>&1; then fuser -k 8000/tcp 2>/dev/null || true; fi
sleep 1
cd /workspaces/Toolkit/artifacts/openai-pool
nohup env PORT=8000 PYTHONPATH=/workspaces/Toolkit/artifacts/openai-pool python3 -m openai_pool_orchestrator > $LOG_DIR/openai-pool.log 2>&1 &
sleep 3
curl -sf http://localhost:8000/api/status > /dev/null && echo "  OpenAI Pool OK (port 8000)" || echo "  OpenAI Pool 启动中（查看 $LOG_DIR/openai-pool.log）"
cd /workspaces/Toolkit

echo "[4/6] 启动 ngrok..."
pkill ngrok 2>/dev/null || true
sleep 1
nohup ngrok http $FRONTEND_PORT --domain=recycling-tragedy-projector.ngrok-free.dev --request-header-add 'ngrok-skip-browser-warning:true' --log=stdout > $LOG_DIR/ngrok.log 2>&1 &
sleep 5
NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels | grep -o '"public_url":"[^"]*"' | head -1 | cut -d'"' -f4)
echo "  ngrok OK: $NGROK_URL"

echo "[5/6] 启动 FakeMail Bridge..."
pkill -f 'fakemail_bridge.py' 2>/dev/null || true
sleep 1
nohup python3 /workspaces/Toolkit/artifacts/api-server/fakemail_bridge.py > $LOG_DIR/fakemail.log 2>&1 &
sleep 2
curl -sf http://localhost:6100/health > /dev/null && echo "  FakeMail Bridge OK (port 6100)" || echo "  FakeMail Bridge 启动中（非关键）"

echo "[6/6] 启动 Xray..."
pkill xray 2>/dev/null || true
sleep 1

node /workspaces/Toolkit/xray-update-ip.js
nohup xray run -c /workspaces/Toolkit/xray.json > $LOG_DIR/xray.log 2>&1 &
sleep 3
XRAY_IP=$(curl -s --proxy socks5://127.0.0.1:10808 --connect-timeout 10 https://api.ipify.org 2>/dev/null)
echo "  Xray OK - 出口IP: $XRAY_IP"
pkill -f xray-watchdog 2>/dev/null || true
nohup bash /workspaces/Toolkit/xray-watchdog.sh > /tmp/toolkit_logs/xray-watchdog.log 2>&1 &
echo "  Xray IP守护已启动"

echo ""
echo "=============================="
echo " 所有服务启动完成"
echo " 访问地址: $NGROK_URL"
echo " 日志目录: $LOG_DIR"
echo "=============================="
echo ""
echo " Python 工具包:"
echo "   - patchright/playwright (Chromium + Firefox)"
echo "   - telegram bot / capsolver / twocaptcha"
echo "   - imapclient / aioimaplib / aiohttp"
echo "   - Pillow / lxml / beautifulsoup4"
echo " Node 工具包:"
echo "   - puppeteer-core / node-imap / mailparser"
echo "   - node-telegram-bot-api / nodemailer"
echo "=============================="
