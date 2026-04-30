#!/usr/bin/env bash
# setup-tailscale-funnel.sh — 一键搭建 Toolkit 的 Tailscale Funnel 公网入口
#
# 用途: 任何新 VPS / 替换节点时, 一条命令复刻 https://vps-toolkit.tail98ceae.ts.net 反代
# 前置: root 权限; 已有 Outlook 账号或预先生成的 Tailscale auth key
#
# 用法:
#   1) 用 auth key (无人值守, 推荐):
#        export TS_AUTHKEY=tskey-auth-xxx
#        bash setup-tailscale-funnel.sh
#   2) 用 Outlook OAuth (交互式, 需手工点链接登录):
#        bash setup-tailscale-funnel.sh
#
# 环境变量 (有默认):
#   TS_HOSTNAME=vps-toolkit          # tailnet 内显示的机器名 → 决定 Funnel 子域
#   TS_BACKEND_PORT=8081             # 本地要暴露的服务端口 (api-server)
#   TS_TAGS=tag:toolkit              # 节点标签 (需在 ACL 里允许)
#
# 重要: hostname 决定 Funnel URL → https://${TS_HOSTNAME}.${tailnet}.ts.net

set -euo pipefail

TS_HOSTNAME="${TS_HOSTNAME:-vps-toolkit}"
TS_BACKEND_PORT="${TS_BACKEND_PORT:-8081}"
TS_TAGS="${TS_TAGS:-}"

log() { echo -e "[$(date +%H:%M:%S)] $*"; }

# 1. 安装 tailscale (Debian/Ubuntu)
if ! command -v tailscale >/dev/null 2>&1; then
  log "→ 安装 tailscale..."
  curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/$(lsb_release -cs).noarmor.gpg \
    | tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
  curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/$(lsb_release -cs).tailscale-keyring.list \
    | tee /etc/apt/sources.list.d/tailscale.list
  apt-get update -qq
  apt-get install -y tailscale
fi

# 2. 启动 daemon + 开机自启
log "→ enable tailscaled"
systemctl enable --now tailscaled

# 3. 节点登录 (auth key 优先; 否则交互 OAuth)
if tailscale status >/dev/null 2>&1 && tailscale status --json | grep -q '"BackendState":"Running"'; then
  log "→ 已登录, 跳过 tailscale up"
else
  if [[ -n "${TS_AUTHKEY:-}" ]]; then
    log "→ 用 auth key 登录 (无人值守)"
    UP_ARGS="--authkey=${TS_AUTHKEY} --hostname=${TS_HOSTNAME} --accept-routes"
    [[ -n "${TS_TAGS}" ]] && UP_ARGS="${UP_ARGS} --advertise-tags=${TS_TAGS}"
    tailscale up ${UP_ARGS}
  else
    log "→ 无 auth key, 交互式 OAuth (复制下面 URL 到浏览器, 用 Outlook/Google/GitHub 登录):"
    tailscale up --hostname="${TS_HOSTNAME}" --accept-routes
  fi
fi

# 4. 等待节点 Running
for i in {1..20}; do
  if tailscale status --json | grep -q '"BackendState":"Running"'; then break; fi
  sleep 1
done

# 5. 获取 tailnet 域名
TAILNET=$(tailscale status --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("MagicDNSSuffix","").rstrip("."))')
FUNNEL_URL="https://${TS_HOSTNAME}.${TAILNET}"
log "→ Funnel 目标 URL: ${FUNNEL_URL}"

# 6. 配 serve + funnel: 把 :443 → 本地 backend
# 先清旧配置避免重复
tailscale serve --https=443 off 2>/dev/null || true
tailscale funnel --https=443 off 2>/dev/null || true

log "→ tailscale serve --bg https://+${TS_BACKEND_PORT}"
tailscale serve --bg "http://127.0.0.1:${TS_BACKEND_PORT}"

log "→ tailscale funnel --bg ${TS_BACKEND_PORT}"
tailscale funnel --bg "${TS_BACKEND_PORT}"

# 7. 验证
log "→ 验证内网可达..."
sleep 2
if curl -sS --max-time 8 -o /dev/null -w "  本地 :${TS_BACKEND_PORT} → HTTP %{http_code}\n" "http://127.0.0.1:${TS_BACKEND_PORT}/"; then :; fi
log "→ 验证 Funnel 公网可达..."
if curl -sS --max-time 12 -o /dev/null -w "  ${FUNNEL_URL}/ → HTTP %{http_code}\n" "${FUNNEL_URL}/"; then :; fi

log "✅ 完成. Funnel URL: ${FUNNEL_URL}"
log ""
log "持久化检查清单:"
log "  systemctl is-enabled tailscaled  →  $(systemctl is-enabled tailscaled)"
log "  tailscale serve status            →"
tailscale serve status | sed 's/^/    /'
log ""
log "下次重启 VPS, 上面三项会自动恢复, 无需重跑本脚本."
