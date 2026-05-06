#!/bin/bash
# 完整环境初始化脚本 — 新 Codespace 首次运行
set -e
echo "============================="
echo " Toolkit 环境初始化"
echo "============================="

# ── 1. 系统库 (Chromium 依赖) ──────────────────────────────────────────────
echo "[1/6] 安装系统库..."
sudo apt-get update -qq --allow-insecure-repositories 2>/dev/null || true
sudo apt-get install -y -q   libasound2t64 libatk1.0-0 libatk-bridge2.0-0 libcups2   libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1   libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2   libdrm2 libnspr4 libnss3 libxss1 libxtst6   libpangocairo-1.0-0 libpangoft2-1.0-0 libx11-xcb1 libxcb1   jq curl wget git 2>&1 | tail -2
echo "  系统库 OK"

# ── 2. Python 包 ───────────────────────────────────────────────────────────
echo "[2/6] 安装 Python 包..."
pip install -q   faker   patchright   playwright   requests   httpx   aiohttp   aiofiles   beautifulsoup4   lxml   Pillow   imapclient   aioimaplib   python-telegram-bot \ \
  python-dotenv   dnspython   cryptography   pyOpenSSL   PySocks   stem   chardet 2>&1 | tail -3
echo "  Python 包 OK"

# ── 3. 浏览器引擎 ─────────────────────────────────────────────────────────
echo "[3/6] 安装浏览器引擎..."
python3 -m patchright install chromium 2>&1 | grep -E 'Downloading|chromium|OK|Error' | tail -3
python3 -m playwright install firefox chromium 2>&1 | grep -E 'Downloading|firefox|chromium|OK|Error' | tail -3
echo "  浏览器引擎 OK"

# ── 4. Node 包 ────────────────────────────────────────────────────────────
echo "[4/6] 安装 Node 包..."
cd /workspaces/Toolkit
pnpm install 2>&1 | tail -3
echo "  Node 包 OK"

# ── 5. PostgreSQL 数据库初始化 ────────────────────────────────────────────
echo "[5/6] 初始化数据库..."
sudo bash -c 'su -s /bin/bash postgres -c "pg_ctlcluster 16 main start"' 2>/dev/null || true
sleep 2
export DATABASE_URL='postgresql://postgres:postgres@localhost/toolkit'
# 建库（存在则跳过）
sudo -u postgres psql -c "CREATE DATABASE toolkit" 2>/dev/null || echo "  数据库已存在"
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres'" 2>/dev/null || true
echo "  数据库 OK"

# ── 6. 构建后端 ───────────────────────────────────────────────────────────
echo "[6/6] 构建 API 服务器..."
cd /workspaces/Toolkit
pnpm --filter @workspace/api-server run build 2>&1 | tail -3
echo "  构建 OK"

echo ""
echo "============================="
echo " 环境初始化完成！"
echo " 运行 bash start.sh 启动服务"
echo "============================="
