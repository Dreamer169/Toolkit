# Tailscale Funnel 交接手册

公网入口由 Tailscale Funnel 提供，替代旧的 ngrok 隧道。本文档说明现状、备份策略、以及"换 VPS / 换账号"三种恢复场景的完整流程。

---

## 1. 当前生产配置

| 项 | 值 |
|---|---|
| 公网入口 | `https://vps-toolkit.tail98ceae.ts.net` |
| 注册账号 | `zoeychavez420@outlook.com` (Microsoft OAuth) |
| Tailnet | `tail98ceae.ts.net` (账号自动分配) |
| Hostname | `vps-toolkit` (脚本固定值) |
| 后端端口 | `127.0.0.1:8081` (api-server, 已合并 frontend 反代) |
| state 文件 | `/var/lib/tailscale/tailscaled.state` (含节点私钥) |
| 系统服务 | `tailscaled` (systemd, enabled 开机自启) |

> Funnel URL 公式: `https://${hostname}.${tailnet}.ts.net`
> hostname 由脚本固定为 `vps-toolkit`，tailnet 由账号决定 → **同账号换 VPS, URL 不变**；**换账号, URL 必变**。

---

## 2. 已硬编码 URL 的位置

如果 URL 变 (换账号场景), 这些文件必须同步改:

- `.env` — `LOCAL_GATEWAY_BASE_URL`
- `autostart.sh` — 启动结束打印的"访问地址"
- `ecosystem.config.cjs` — `LOCAL_GATEWAY_BASE_URL` 环境变量
- `data/tailscale-funnel-snapshot.json` — serve 配置快照 (自动重建可忽略)

**`scripts/setup-tailscale-funnel.sh` 默认会自动 sed 替换前 3 个**, 替换后需要:
```bash
pm2 restart api-server --update-env
```

---

## 3. 备份策略

state 文件 `/var/lib/tailscale/tailscaled.state` (~3KB) 含节点私钥. **拥有它即可在新机器上"复活"成同一个节点**, 无需 authkey, 无需登录.

### 自动备份
```bash
bash scripts/setup-tailscale-funnel.sh backup
# → 拷到 /root/tailscale-backup/tailscaled.state.YYYYMMDD-HHMMSS
# → 软链 tailscaled.state.latest 指向最新
```

`setup` 子命令每次执行末尾会自动 backup。

### 异地备份 (强烈推荐)
```bash
# 复制到本地机器
scp root@<VPS>:/root/tailscale-backup/tailscaled.state.latest ~/secrets/

# 或者 GPG 加密后存私有 git
gpg --symmetric --cipher-algo AES256 tailscaled.state.latest
# → tailscaled.state.latest.gpg, 解密: gpg --decrypt
```

> ⚠️ **此文件含私钥, 严禁进公开仓库 / Slack / 邮件附件**.
> 当前 `.gitignore` 已默认忽略 `tailscale-backup/`.

---

## 4. 三种恢复场景

### 场景 A — state 文件还在 (重启 / 重新部署同一台机)
什么都不用做。`tailscaled` systemd 自启, Funnel 配置已持久化在 state 里, 自动恢复。

### 场景 B — 换 VPS, 想保留 URL (`vps-toolkit.tail98ceae.ts.net`)

**前提**: 有上次的 `tailscaled.state` 备份 **OR** 能登录原 Tailscale 账号 (`zoeychavez420@outlook.com`)。

**方式 1 (推荐) — 用 state 备份直接复活**:
```bash
# 1. 新 VPS 上拉代码
git clone https://github.com/Dreamer169/Toolkit.git /root/Toolkit && cd /root/Toolkit

# 2. 拷 state 备份过来
mkdir -p /root/tailscale-backup
scp <旧机或本地>:tailscaled.state.latest /root/tailscale-backup/

# 3. 装 tailscale 并恢复
bash scripts/setup-tailscale-funnel.sh restore
# → 同节点身份直接复活, URL 不变, 旧 VPS 自动下线
```

**方式 2 — 登录账号生成 reusable authkey**:
1. 浏览器登录 https://login.tailscale.com 用 `zoeychavez420@outlook.com`
2. Settings → Keys → Generate auth key → 勾 **Reusable** + **Pre-approved** + 设 90d 过期
3. 复制 `tskey-auth-xxxxx` 后:
```bash
export TS_AUTHKEY=tskey-auth-xxxxx
bash scripts/setup-tailscale-funnel.sh
# → 会注册一台新节点 hostname=vps-toolkit
# → 旧节点 (如还在) 会被新节点顶替同名 (Tailscale 自动加后缀 vps-toolkit-1)
# → URL 仍是 https://vps-toolkit.tail98ceae.ts.net
```

> 建议: 一次生成 authkey 后记入私密保险库 (1Password / Bitwarden), 90d 内换机不用再登录.

### 场景 C — 完全新人 + 全新账号 (最坏情况, 旧账号不可用)

```bash
# 1. 新 VPS 拉代码
git clone https://github.com/Dreamer169/Toolkit.git /root/Toolkit && cd /root/Toolkit

# 2. 跑脚本 (不带 TS_AUTHKEY → 会进交互式 OAuth)
bash scripts/setup-tailscale-funnel.sh
# → 终端打印一行 https://login.tailscale.com/a/XXXXX 链接
# → 浏览器打开, 用任意 Outlook / Google / GitHub / Apple 账号注册新 Tailscale 账号
# → 注册完成后 tailnet 后缀 (如 tail123abc.ts.net) 自动分配
# → 脚本接着自动:
#     - 配 Funnel 指向 :8081
#     - 检测出新 URL = https://vps-toolkit.<新tailnet>.ts.net
#     - 自动 sed 替换 .env / autostart.sh / ecosystem.config.cjs 里的旧 URL
#     - 备份 state 到 /root/tailscale-backup/

# 3. 重启服务让新 URL 生效
pm2 restart api-server --update-env
pm2 save

# 4. 验证
bash scripts/setup-tailscale-funnel.sh status
curl https://vps-toolkit.<新tailnet>.ts.net/api/healthz
```

> 唯一需要手工跟进的: **如果有上游/外部系统 (Replit、监控、第三方 webhook) 写死了旧 URL**, 需要单独通知更新. 当前已知没有外部硬编码, 全在 repo 内.

---

## 5. Funnel 限额提醒

Tailscale Funnel 在 Free / Personal Pro plan 下的限额:
- 节点数: Free 100 / Personal Pro 1000
- Funnel 节点: 任意 plan 上限 3 个开 Funnel 的节点 (够用)
- 流量: 无硬限额, 但有 fair-use 软上限 (大流量场景考虑自架 frp)

如果将来流量超出 Funnel 适用范围, 切回 frp 方案: 重启 ngrok-gateway pm2 服务 + 修 `.env` LOCAL_GATEWAY_BASE_URL 即可。

---

## 6. 常用命令速查

```bash
# 状态摘要
bash scripts/setup-tailscale-funnel.sh status

# 备份当前 state
bash scripts/setup-tailscale-funnel.sh backup

# 从备份恢复 (默认 latest, 也可指定文件)
bash scripts/setup-tailscale-funnel.sh restore [/path/to/state.file]

# 完整重新 setup (幂等, 已注册会跳过)
bash scripts/setup-tailscale-funnel.sh

# 查 serve / funnel 配置
tailscale serve status
tailscale funnel status

# 强制下线本节点
tailscale logout

# 查节点 ID/key
cat /var/lib/tailscale/tailscaled.state | head -20
```
