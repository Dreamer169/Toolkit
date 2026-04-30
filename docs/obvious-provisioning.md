# obvious.ai — 自动开号 + 安全使用手册

> **目的**：让任何新人能在 Toolkit 上一键开一个 obvious.ai 账号，拿到一个
> 持久 Debian 13 / 2 vCPU / 8 GB RAM / 26 GB 沙箱 + cookie 调用入口，
> 不用手动注册、不会因 IP 集中触发风控。

## 一、整套链路概览

```
                 mailtm 临时邮箱           xray 多出口
                       │                      │
                       ▼                      ▼
   obvious_provision.py  →  Playwright(经代理)  →  obvious.ai 注册
                                                   │
                                                   ├─ onboarding 自动填
                                                   ├─ 关 25-credits modal
                                                   └─ 创建第一个 thread
                                                   ▼
              <out-dir>/<label>/                   ▼
                manifest.json   ◄── 写入 ──   抓取 IDs:
                storage_state.json              userId / workspaceId
                shots/                           projectId / threadId
                                                 sandboxId / creditBalance
                       │
                       ▼
              obvious_client.py  →  POST /agent/chat → run shell / playwright
```

## 二、一次性环境准备（VPS root 级）

已经做过的话跳过。

```bash
# Python 依赖
pip install playwright
playwright install chromium

# Toolkit 自带依赖
ls /root/Toolkit/scripts/{mailtm_client,obvious_client,obvious_provision}.py

# xvfb（无头 X server，让 Playwright 能开 chromium）
pm2 list | grep xvfb || pm2 start --name xvfb -- /usr/bin/Xvfb :99 -screen 0 1920x1080x24
```

## 三、IP 隔离策略（关键）

obvious 用 better-auth + 风控，**同一 IP 多注册或同 ASN 短时间多注册都会触发封号**。

Toolkit 已经在 `xray.json` 里给每条 sub-node 暴露了独立 socks 端口
(`10820 / 10821 / 10822 / …`)，每个端口对应一条不同的境外 vmess/vless
出站。**注册新账号时必须**：

1. **每个新账号选一条没用过的 socks 端口**：
   ```bash
   # 列出活跃端口 + 出口 IP（quick health check）
   for p in 10808 10820 10821 10822 10823 10824 10825; do
     ip=$(curl -s --max-time 5 --socks5-hostname 127.0.0.1:$p https://api.ipify.org)
     [ -n "$ip" ] && echo "  socks $p → $ip"
   done
   ```

2. 在 `<out-dir>/index.json` 检查这个 IP / ASN 是不是已经用过：
   ```bash
   jq '[.[] | {label,egressIp,proxy}]' /root/obvious-accounts/index.json
   ```

3. **不要**用主 socks (10808 = VPS 本机出口)、**不要**用 Tailscale 内网。
   用海外 sub-node 的端口最安全。

4. 同一 sub-node 端口**至多注册 1 个号**，多账号共用同一出口 = 高风险。

5. 注册间隔 ≥ 1 小时；同时跑多个 provision 会在 mailtm 端踩到速率限制。

## 四、注册一个新账号

### 标准用法（mailtm 临时邮箱，全自动）

```bash
DISPLAY=:99 python3 /root/Toolkit/scripts/obvious_provision.py \
    --proxy socks5://127.0.0.1:10821 \
    --label eu-test1 \
    --check-ip
```

执行 ≈ 90 s，结束后输出：

```
=== ✅ provisioned ===
{
  "label": "eu-test1",
  "email": "<random>@deltajohnsons.com",
  "password": "M@<hex>",
  "userId": "usr_xxx",
  "workspaceId": "wks_xxx",
  "projectId": "prj_xxx",
  "threadId": "th_xxx",
  "sandboxId": "<sandbox-hash>",
  "creditBalance": 24.74,
  "tier": "free",
  "proxy": "socks5://127.0.0.1:10821",
  "egressIp": "176.98.181.71",
  ...
}
```

### 特殊用法：手动邮箱（mailtm 域名被屏蔽时的兜底）

```bash
DISPLAY=:99 python3 /root/Toolkit/scripts/obvious_provision.py \
    --proxy socks5://127.0.0.1:10822 \
    --email me+xyz@outlook.com --password 'YourStrongPass!' \
    --label backup-1
```

⚠️ 手动邮箱**必须自己事先注册好**且能收信。

## 五、用账号干活

```bash
# 一行 CLI
python3 /root/Toolkit/scripts/obvious_client.py \
    --cookies /root/obvious-accounts/eu-test1/storage_state.json \
    --thread  $(jq -r .threadId /root/obvious-accounts/eu-test1/manifest.json) \
    --project $(jq -r .projectId /root/obvious-accounts/eu-test1/manifest.json) \
    "uname -a; df -h /home/user; pip list 2>/dev/null | head"
```

或 Python 库使用：

```python
import json
from obvious_client import ObviousClient

m = json.load(open('/root/obvious-accounts/eu-test1/manifest.json'))
c = ObviousClient.from_storage_state(
    m['storageState'], thread_id=m['threadId'], project_id=m['projectId'],
    mode='fast',  # auto | fast | deep | analyst | skill-builder
)
new = c.ask("Run a Playwright script that fetches the title of https://news.ycombinator.com")
for s in c.extract_shell_results(new):
    print(s['command'], '→', s['stdout'][:200])
print(c.extract_text(new))
```

## 六、credit 监控 / 维护

每条用户消息约 **0.25 credit**，每个新账号 25 credits ≈ **100 次对话**。

```bash
# 单个账号实时余额
python3 -c "
from obvious_client import ObviousClient
import json
m=json.load(open('/root/obvious-accounts/eu-test1/manifest.json'))
c=ObviousClient.from_storage_state(m['storageState'], m['threadId'], m['projectId'])
print(c.billing_status(m['workspaceId']))
"
```

跌到 < 5 时换号。删除老账号目录即可（cookies 失效自动报错）。

## 七、批量开号（带间隔）

```bash
#!/bin/bash
PORTS=(10821 10822 10823 10824 10825)
for i in "${!PORTS[@]}"; do
  port="${PORTS[$i]}"
  label="batch-$(date +%s)-$i"
  echo "=== $label via $port ==="
  DISPLAY=:99 python3 /root/Toolkit/scripts/obvious_provision.py \
      --proxy socks5://127.0.0.1:$port --label "$label" --check-ip
  sleep 3600  # 1 小时间隔，避开同一 ASN 的速率风控
done
```

## 八、登记表（自动维护）

`<out-dir>/index.json` 在每次注册后自动 append。查询：

```bash
jq '.[] | "\(.label)\t\(.egressIp)\t\(.tier // "?")\t\(.createdAt)"' -r \
    /root/obvious-accounts/index.json | column -t -s $'\t'
```

## 九、注意事项

- mailtm 域名 (`deltajohnsons.com`) 不稳定，可能某天被 obvious 拉黑 →
  改 `mailtm_client.py` 顶部的 `DOMAIN` 即可；mailtm 提供多域名。
- obvious 注册页若改版（多了 captcha / 邮箱验证 step），脚本会在
  `03_after_signup` / `05_onboarding` 截图停下，去 `<out-dir>/<label>/shots/`
  看具体出错画面，再调整 selector。
- 不要在 git 里提交 `<out-dir>` —— 已经在 `.gitignore` 加了
  `/root/obvious-accounts/`（如果你换路径要自己加）。
- `storage_state.json` 含会话凭据，泄漏 = 被人冒用账号 + 烧光 credits。
