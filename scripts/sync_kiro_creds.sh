#!/bin/bash
# sync_kiro_creds.sh — 同步 DB kiro 账号 → kiro-rs credentials.json
# 每次 kiro_chain 注册完新账号后可调用此脚本
set -e
export DATABASE_URL='postgresql://postgres:postgres@localhost/toolkit'
cd /data/Toolkit/scripts

echo '[sync] Running gen_kiro_credentials.py...'
python3 /data/Toolkit/scripts/gen_kiro_credentials.py

echo '[sync] Reloading kiro-rs via Admin API (hot reload)...'
# kiro-rs 支持通过 POST /api/admin/credentials 热添加；但最简单是 pm2 restart
# 先尝试 Admin API reload（如果 adminApiKey 配置了）
ADMIN_KEY=$(python3 -c "import json; c=json.load(open('/opt/kiro.rs/config.json')); print(c.get('adminApiKey',''))" 2>/dev/null)
if [ -n "$ADMIN_KEY" ]; then
  echo '[sync] Admin API available, using hot reload...'
  curl -s -X POST http://127.0.0.1:8990/api/admin/credentials/reload     -H "x-api-key: $ADMIN_KEY" 2>/dev/null || true
fi

echo '[sync] pm2 restart kiro-rs...'
pm2 restart kiro-rs
echo '[sync] Done.'
