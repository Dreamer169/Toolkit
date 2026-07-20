#!/usr/bin/env bash
# setup-tailscale-funnel.sh — Toolkit 的 Tailscale Funnel 公网入口 一键脚本
#
# 三种用法 (子命令):
#   ./setup-tailscale-funnel.sh             ← 默认: 安装 + 注册 + Funnel + 重写硬编码 URL
#   ./setup-tailscale-funnel.sh backup      ← 备份 tailscaled.state 到 $TS_BACKUP_DIR
#   ./setup-tailscale-funnel.sh restore     ← 从 $TS_BACKUP_DIR 恢复 state (同节点身份直接复活, 无需 authkey)
#   ./setup-tailscale-funnel.sh status      ← 打印当前账号/tailnet/Funnel URL 摘要
#
# 注册模式 (默认子命令):
#   1) auth key 无人值守:       export TS_AUTHKEY=tskey-auth-xxx; ./setup-tailscale-funnel.sh
#   2) 交互 OAuth (Outlook/Google/GitHub): ./setup-tailscale-funnel.sh
#
# 环境变量:
#   TS_HOSTNAME=vps-toolkit          # tailnet 内显示名 → 决定 Funnel 子域 (默认 vps-toolkit)
#   TS_BACKEND_PORT=8081             # 本地暴露端口 (api-server)
#   TS_TAGS=tag:toolkit              # 节点标签 (需在 ACL 里允许)
#   TS_BACKUP_DIR=/root/tailscale-backup    # state 备份目录 (默认 /root/tailscale-backup)
#   TS_REWRITE_URLS=1                # 1=自动重写 .env / autostart.sh / ecosystem.config.cjs 里硬编码 URL (默认 1)
#
# 切换账号/换 VPS 流程见 docs/tailscale-handover.md

set -euo pipefail

TS_HOSTNAME="${TS_HOSTNAME:-vps-toolkit}"
TS_BACKEND_PORT="${TS_BACKEND_PORT:-8081}"
TS_TAGS="${TS_TAGS:-}"
TS_BACKUP_DIR="${TS_BACKUP_DIR:-/root/tailscale-backup}"
TS_REWRITE_URLS="${TS_REWRITE_URLS:-1}"
TS_STATE_FILE="/var/lib/tailscale/tailscaled.state"
REPO_ROOT="${REPO_ROOT:-/root/Toolkit}"

log() { echo -e "[$(date +%H:%M:%S)] $*"; }
err() { echo -e "[$(date +%H:%M:%S)] ERROR: $*" >&2; exit 1; }

cmd_status() {
  if ! command -v tailscale >/dev/null 2>&1; then err "tailscale 未安装"; fi
  if ! tailscale status >/dev/null 2>&1; then err "tailscaled 未运行或未登录"; fi
  local tailnet hostname dnsname user funnel
  tailnet=$(tailscale status --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("MagicDNSSuffix","").rstrip("."))')
  hostname=$(tailscale status --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("Self",{}).get("HostName",""))')
  dnsname=$(tailscale status --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("Self",{}).get("DNSName","").rstrip("."))')
  user=$(tailscale status --json | python3 -c 'import json,sys;d=json.load(sys.stdin);s=d.get("Self",{});print(d.get("User",{}).get(str(s.get("UserID","")),{}).get("LoginName","?"))')
  funnel="https://${dnsname}"
  echo "================== Tailscale Funnel Status =================="
  echo "  Account     : ${user}"
  echo "  Tailnet     : ${tailnet}"
  echo "  Hostname    : ${hostname}"
  echo "  Funnel URL  : ${funnel}"
  echo "  Backend     : http://127.0.0.1:${TS_BACKEND_PORT}"
  echo "  state file  : ${TS_STATE_FILE} ($(stat -c%s "$TS_STATE_FILE" 2>/dev/null || echo "?") bytes)"
  echo "  systemd     : $(systemctl is-enabled tailscaled 2>/dev/null)"
  echo "============================================================="
  echo
  tailscale serve status 2>&1 | sed 's/^/  /'
}

cmd_backup() {
  mkdir -p "$TS_BACKUP_DIR"
  chmod 700 "$TS_BACKUP_DIR"
  if [[ ! -s "$TS_STATE_FILE" ]]; then err "state 文件不存在或为空: $TS_STATE_FILE"; fi
  local ts dest
  ts=$(date +%Y%m%d-%H%M%S)
  dest="${TS_BACKUP_DIR}/tailscaled.state.${ts}"
  cp -a "$TS_STATE_FILE" "$dest"
  chmod 600 "$dest"
  ln -sfn "$dest" "${TS_BACKUP_DIR}/tailscaled.state.latest"
  log "✅ 备份完成: $dest"
  log "   软链   : ${TS_BACKUP_DIR}/tailscaled.state.latest"
  log "   说明   : 本文件含节点私钥, 严禁进 git / 上传公开仓库."
  log "   恢复   : 拷到新机器同路径 → 跑 'setup-tailscale-funnel.sh restore'"
  ls -la "$TS_BACKUP_DIR" | tail -10
}

cmd_restore() {
  local src="${1:-${TS_BACKUP_DIR}/tailscaled.state.latest}"
  [[ -f "$src" ]] || err "找不到 state 备份: $src"
  systemctl stop tailscaled || true
  install -m 600 -o root -g root "$src" "$TS_STATE_FILE"
  systemctl start tailscaled
  systemctl enable tailscaled
  log "✅ state 已恢复, 等待节点上线..."
  for i in {1..30}; do
    if tailscale status --json 2>/dev/null | grep -q '"BackendState":"Running"'; then break; fi
    sleep 1
  done
  cmd_status
}

ensure_installed() {
  if command -v tailscale >/dev/null 2>&1; then return; fi
  log "→ 安装 tailscale..."
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/$(lsb_release -cs).noarmor.gpg" \
    | tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/$(lsb_release -cs).tailscale-keyring.list" \
    | tee /etc/apt/sources.list.d/tailscale.list
  apt-get update -qq
  apt-get install -y tailscale
}

ensure_registered() {
  systemctl enable --now tailscaled
  if tailscale status --json 2>/dev/null | grep -q '"BackendState":"Running"'; then
    local existing_host
    existing_host=$(tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin).get("Self",{}).get("HostName",""))')
    log "→ 已登录, 当前 hostname=${existing_host}, 跳过注册"
    return
  fi
  if [[ -n "${TS_AUTHKEY:-}" ]]; then
    log "→ 用 auth key 无人值守注册"
    local args="--authkey=${TS_AUTHKEY} --hostname=${TS_HOSTNAME} --accept-routes"
    [[ -n "${TS_TAGS}" ]] && args="${args} --advertise-tags=${TS_TAGS}"
    tailscale up $args
  else
    log "→ 无 auth key, 交互式 OAuth (复制下面 URL 到浏览器, 用 Outlook/Google/GitHub 登录):"
    log "  推荐用 Outlook (与 Toolkit 现有账号体系一致)"
    tailscale up --hostname="${TS_HOSTNAME}" --accept-routes
  fi
  for i in {1..30}; do
    tailscale status --json 2>/dev/null | grep -q '"BackendState":"Running"' && break
    sleep 1
  done
}

ensure_funnel() {
  log "→ 配 serve + funnel: :443 → http://127.0.0.1:${TS_BACKEND_PORT}"
  tailscale serve --https=443 off 2>/dev/null || true
  tailscale funnel --https=443 off 2>/dev/null || true
  tailscale serve --bg "http://127.0.0.1:${TS_BACKEND_PORT}"
  tailscale funnel --bg "${TS_BACKEND_PORT}"
}

rewrite_hardcoded_urls() {
  [[ "$TS_REWRITE_URLS" = "1" ]] || { log "→ TS_REWRITE_URLS=0, 跳过 URL 重写"; return; }
  local new_url old_pattern
  new_url=$(tailscale status --json | python3 -c 'import json,sys;print("https://"+json.load(sys.stdin).get("Self",{}).get("DNSName","").rstrip("."))')
  old_pattern='https://vps-toolkit\.[a-z0-9]\+\.ts\.net'
  log "→ 重写硬编码 URL → ${new_url}"
  local files=(
    "${REPO_ROOT}/.env"
    "${REPO_ROOT}/autostart.sh"
    "${REPO_ROOT}/ecosystem.config.cjs"
  )
  for f in "${files[@]}"; do
    [[ -f "$f" ]] || { log "  - 跳过缺失: $f"; continue; }
    if grep -q "vps-toolkit\..*\.ts\.net" "$f"; then
      sed -i.bak "s|${old_pattern}|${new_url}|g" "$f"
      log "  ✅ 重写: $f (备份 .bak)"
    else
      log "  - 无硬编码 URL: $f"
    fi
  done
  log "→ 提醒: ecosystem.config.cjs 改了需要 'pm2 restart api-server --update-env' 生效"
}

verify() {
  log "→ 验证内网 backend..."
  curl -sS --max-time 8 -o /dev/null -w "  127.0.0.1:${TS_BACKEND_PORT}/api/healthz → HTTP %{http_code}\n" \
    "http://127.0.0.1:${TS_BACKEND_PORT}/api/healthz" || true
  log "→ 验证 Funnel 公网..."
  local funnel
  funnel=$(tailscale status --json | python3 -c 'import json,sys;print("https://"+json.load(sys.stdin).get("Self",{}).get("DNSName","").rstrip("."))')
  curl -sS --max-time 12 -o /dev/null -w "  ${funnel}/api/healthz → HTTP %{http_code}\n" \
    "${funnel}/api/healthz" || true
}

cmd_setup() {
  ensure_installed
  ensure_registered
  ensure_funnel
  rewrite_hardcoded_urls
  cmd_backup
  verify
  echo
  cmd_status
  echo
  log "✅ 完成. 下一步: pm2 restart api-server --update-env  (若 ecosystem.config.cjs 被改写)"
}

case "${1:-setup}" in
  setup)   cmd_setup ;;
  backup)  cmd_backup ;;
  restore) cmd_restore "${2:-}" ;;
  status)  cmd_status ;;
  *) err "未知子命令: $1 (可用: setup|backup|restore|status)" ;;
esac
