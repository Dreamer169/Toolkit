#!/bin/bash
# CF 临时邮箱 Worker 部署脚本
# 用法: ./cf-email-deploy.sh [jonjim|hackerjim|all]
# 两个 wrangler.toml 在 /data/cf-email/worker/

set -e
TARGET="${1:-all}"
WORKER_DIR="/data/cf-email/worker"

deploy_jonjim() {
  echo "[jonjim] 开始部署..."
  cd "$WORKER_DIR"
  CLOUDFLARE_API_TOKEN=REDACTED_CF_API_TOKEN \
  CLOUDFLARE_ACCOUNT_ID=f7a0cd49eddc664419f9a783be8ce73d \
    wrangler deploy --config wrangler.toml 2>&1
  echo "[jonjim] 部署完成 → https://mail-api.jonjim.eu.cc"
}

deploy_hackerjim() {
  echo "[hackerjim] 开始部署..."
  cd "$WORKER_DIR"
  CLOUDFLARE_API_TOKEN=REDACTED_CF_API_TOKEN \
  CLOUDFLARE_ACCOUNT_ID=d2a1b22bd8cf8bbdff62953315347a63 \
    wrangler deploy --config wrangler.hackerjim.toml 2>&1
  echo "[hackerjim] 部署完成 → https://mail-api.hackerjim.eu.cc"
}

case "$TARGET" in
  jonjim)   deploy_jonjim ;;
  hackerjim) deploy_hackerjim ;;
  all)      deploy_jonjim; deploy_hackerjim ;;
  *)        echo "用法: $0 [jonjim|hackerjim|all]"; exit 1 ;;
esac
