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

---

## 十、obvious 当作 Replit 任务诊断助手

### 背景：obvious 沙箱不能直接当 Replit 注册的 IP 池

obvious 沙箱的 e2b 出口是 datacenter ASN（实测 47.83.x / 219.76.x /
176.98.x），跟当前失败的 SOCKS sub-node 同类，**reCAPTCHA Enterprise 同样会拒**。
所以不要把 obvious 接到 replit_register.py 的 attempt 链上。

### 真正的用法：让 obvious 读 Toolkit 的失败任务日志，给可执行建议

`scripts/obvious_diagnose.py` 把任意 `rpl_xxx` 任务的 logs + result 喂给
obvious sandbox，让它在 deep / skill-builder 模式分析 → 输出 4 段结构化诊断
(ROOT CAUSE / EVIDENCE / CHEAPEST FIX / INFRA RECOMMENDATION)。

```bash
# 单条任务深度诊断
python3 /root/Toolkit/scripts/obvious_diagnose.py rpl_moj4wx0o_9kx2

# 批量分析最近 5 条失败任务
python3 /root/Toolkit/scripts/obvious_diagnose.py --tail 5

# 指定其他账号 / 模式
python3 /root/Toolkit/scripts/obvious_diagnose.py rpl_xxx \
    --account /root/obvious-accounts/eu-test1 --mode skill-builder
```

实测：v8.71 patch 之前 6 条连续失败的 `captcha_token_invalid`，obvious 一句话定位
"low-reputation exit IP fails reCAPTCHA Enterprise score gate"，并给出可直接落地的
ASN allow/block 关键词列表 (Comcast / Verizon / BT / Orange 加分; M247 / OVH / Hetzner
/ DigitalOcean / Vultr 减分)。

### 注册前 IP 评分预检 (替代 obvious 直接当 IP 池)

`scripts/replit_ip_probe.py` 用 `ip-api.com` 公开 API 给每个 SOCKS 端口实时打分
(综合 ASN / hosting / mobile / residential 关键词)，broker 调度可以直接用：

```bash
# 表格 + 推荐
python3 /root/Toolkit/scripts/replit_ip_probe.py

# JSON 给 broker 解析
python3 /root/Toolkit/scripts/replit_ip_probe.py --json --respect-cooldown

# 只输出最佳 port 的 socks5 URL (适合 shell 嵌入)
BEST=$(python3 /root/Toolkit/scripts/replit_ip_probe.py --pick --respect-cooldown)
DISPLAY=:99 python3 replit_register.py --proxy "$BEST" ...
```

输出按 score 排序，> 0 的能打过 reCAPTCHA Enterprise，< 0 的几乎必拒。

---

## 十一、Pool 管理（多账号并发接口）

`obvious_pool.py` 是封装了多账号健康检查、并发分发、自动获取的高层接口，
日常操作优先用它而非直接调 `obvious_client.py`。

```bash
cd /root/Toolkit/scripts

# 查看所有账号状态
python3 obvious_pool.py status

# 强制刷新健康状态
python3 obvious_pool.py refresh

# 用最佳账号执行一条命令（默认 mode=auto，支持 run-shell）
python3 obvious_pool.py ask "run: uname -a && python3 --version"

# 并发批量执行（文件每行一条 prompt）
python3 obvious_pool.py batch /tmp/prompts.txt --concurrent 2

# 自动补号（健康账号 < target 时自动 provision）
python3 obvious_pool.py maintain --target 3 --max-provision 1
```

**重要**：`ask` 和 `batch` 默认 `mode=auto`，这是支持 `run-shell` 的最低模式。
不要传 `--mode fast`，fast 模式的工具列表**不包含** `run-shell`，会报
`"unavailable tool"` 错误。

---

## 十二、沙箱休眠与唤醒

obvious 沙箱在 **约 30 分钟不活跃后自动 pause**，恢复需要几秒钟：

```python
from obvious_client import ObviousClient
import json

m = json.load(open('/root/obvious-accounts/eu-test1/manifest.json'))
c = ObviousClient.from_storage_state(m['storageState'], m['threadId'], m['projectId'])

# 检查是否 paused
print(c.get_sandbox_paused())  # True / False / None

# 唤醒（发一条 ping 消息等 agentStatus=completed，最多等 90s）
ok = c.wake_sandbox()
print("awake:", ok)

# 之后正常用
msgs = c.ask("run: echo hello")
```

`obvious_pool.py ask` 会自动路由到健康账号，但不会自动唤醒 paused 沙箱。
如果发现所有账号均 paused，先手动调一次 `c.wake_sandbox()` 再继续。

---

## 十三、已知 Bug 记录（已修复，勿重踩）

| Bug | 文件 | 现象 | 修复版本 |
|-----|------|------|---------|
| `extract_shell_results` AttributeError | `obvious_client.py` | `tool-result` 是 `error-text` 类型时 `str.get("data")` 崩溃 | commit `7053ffe` |
| `mode=fast` 无 `run-shell` | `obvious_pool.py` / `obvious_executor.py` | 所有 pool.ask/acquire 默认 fast，沙箱命令全部报 "unavailable tool" | commit `7053ffe` |
| `get_agent_status` SSE 解析偏移 | `obvious_client.py` | 字符串扫描可能错过真实 status 值 | commit `7053ffe` |
| `e2b_direct.py` 多处错误 | `scripts/e2b_direct.py` | obvious API base 用 `app.obvious.ai/api/...` 返回 HTML；e2b envd URL 格式错 | commit `7053ffe` |

---

## 十四、e2b 直连（绕过 AI 层）

`obvious_client.py` 通过 obvious AI 层收发消息，AI 层本身会拦截"敏感"命令。
若需要绕过 AI 直接操控沙箱，需要 e2b API key，流程如下：

### Step 1：捕获 e2b token（一次性）

obvious 后端在沙箱创建时向 e2b API 发送带 `Authorization: Bearer e2b_xxx` 的请求，
用 Playwright CDP 可以拦截到：

```bash
# 需要 XVFB 运行中
DISPLAY=:99 python3 /root/Toolkit/scripts/capture_e2b_token.py  # 捕获 cz-test1
DISPLAY=:99 python3 /root/Toolkit/scripts/sniff_e2b_token.py cz-test1
```

捕获到的 token 格式：`e2b_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

### Step 2：直连 envd

```python
# 沙箱 envd HTTP API（需要 e2b API key）
# sandbox_id 来自 manifest["sandboxId"]
# envd URL 格式：https://{sandboxId}-49983.e2b.dev/
import httpx
r = httpx.post(
    f"https://{sandbox_id}-49983.e2b.dev/process",
    headers={"Authorization": f"Bearer {e2b_api_key}"},
    json={"cmd": ["bash", "-c", "echo hello"]}
)
```

> ⚠️ 注意：沙箱 paused 时 envd 返回 `invalid sandbox port`（即使 URL 正确）。
> 必须先通过 obvious chat API 唤醒沙箱，再做 envd 直连。

### Step 3：快速诊断（探测当前状态）

```bash
python3 /root/Toolkit/scripts/e2b_direct.py cz-test1
```

输出：沙箱元数据、唤醒流程、最近 tool-result、envd 连通性探测。

---

## 十五、新人一键走完全链路（快速参考）

```bash
# 0. 确认环境
pm2 list | grep -E "xvfb|xray|api-server"

# 1. 查看可用 socks 端口 + 出口 IP
for p in 10821 10822 10823 10824 10825; do
  ip=$(curl -s --max-time 5 --socks5-hostname 127.0.0.1:$p https://api.ipify.org 2>/dev/null)
  [ -n "$ip" ] && echo "port=$p ip=$ip"
done

# 2. 选一个没用过的端口注册新号
DISPLAY=:99 python3 /root/Toolkit/scripts/obvious_provision.py \
    --proxy socks5://127.0.0.1:10823 \
    --label new-acc-1 \
    --check-ip

# 3. 确认注册成功
python3 /root/Toolkit/scripts/obvious_pool.py refresh

# 4. 验证沙箱可用
python3 /root/Toolkit/scripts/obvious_pool.py ask \
    "run: python3 --version && echo SANDBOX_OK"

# 5. 若提示 paused，先唤醒
python3 /root/Toolkit/scripts/e2b_direct.py new-acc-1
```

---
