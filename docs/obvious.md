# obvious.ai — 完整使用手册

> **最后更新**：2026-05-01  
> **覆盖版本**：obvious_keepalive v2（sandbox-init）+ repair_account + autoprovision

---

## 一、总览

obvious.ai 为每个免费账号提供一个持久 **e2b Debian 13 VM**（2 vCPU / 8 GB RAM / 26 GB 磁盘）
和一个 LLM agent，可通过 cookie HTTP 调用，无需浏览器，单次 shell 命令往返约 13 s。

| 关键指标 | 值 |
|---|---|
| OS | Debian GNU/Linux 13 (trixie)，kernel 6.1.x |
| CPU | 2 vCPU |
| RAM | 8 GB |
| Disk | 26 GB（首次约用 24%）|
| Python | 3.13 |
| Playwright | 1.59.0（chromium 可按需安装）|
| Credits | 25 credits/账号/月，约 100 次对话 |
| 沙箱 idle pause | ~30 分钟不活跃自动暂停 |

**Toolkit 内脚本分工**：

| 脚本 | 职责 |
|---|---|
| `obvious_provision.py` | Playwright 自动注册 + onboarding，输出 manifest + storage_state |
| `obvious_client.py` | 无头 HTTP 客户端，调用 obvious chat API |
| `obvious_sandbox.py` | ObviousSandbox 类，高层封装 execute/shell/credits |
| `obvious_executor.py` | CLI 工具，汇总 health/exec/credits/env/register/token-sniff |
| `obvious_pool.py` | 多账号池：健康检查、并发分发、自动获取 |
| `obvious_keepalive.py` | PM2 守护：定期 ping、自动唤醒、沙箱资源初始化、credit auto-reset |
| `obvious_autoprovision.py` | PM2 守护：监控池大小，低于 MIN_POOL 时自动注册新号 |
| `repair_account.py` | Playwright 修复：重建 null 的 projectId/threadId/sandboxId |
| `mailtm_client.py` | deltajohnsons.com 临时邮箱 API 封装 |

---

## 二、一次性环境准备（VPS）

```bash
pip install playwright requests[socks]
playwright install chromium

# 确认 xvfb 运行（Playwright 需要）
pm2 list | grep xvfb
# 如没有：
pm2 start --name xvfb -- /usr/bin/Xvfb :99 -screen 0 1920x1080x24
```

---

## 三、IP 隔离策略（必读）

obvious 使用 better-auth + 风控，**同一 IP 多账号或短时间密集注册必触发封号**。

Toolkit 的 xray 配置每条 sub-node 暴露独立 SOCKS5 端口（`10820`–`10835`），
每个端口对应不同境外出口。**注册规则**：

1. **每个账号独占一个端口**，不共用出口 IP
2. 先验证端口可用且出口 IP 唯一：

```bash
for p in 10820 10821 10822 10823 10824 10825; do
  ip=$(curl -s --max-time 5 --socks5-hostname 127.0.0.1:$p https://api.ipify.org)
  [ -n "$ip" ] && echo "port=$p ip=$ip"
done
```

3. 检查 `index.json` 里已用的 IP：

```bash
jq '[.[] | {label,egressIp,proxy}]' /root/obvious-accounts/index.json
```

4. **不要**用 `10808`（VPS 本机出口）或 Tailscale 内网
5. 同一端口 **最多注册 1 个号**；注册间隔 ≥ 1 小时

---

## 四、注册新账号

### 标准用法（mailtm 自动邮箱）

```bash
DISPLAY=:99 python3 /root/Toolkit/scripts/obvious_provision.py \
    --proxy socks5://127.0.0.1:10821 \
    --label eu-test1 \
    --check-ip
```

执行约 90 s，成功后输出 manifest 并写入：
- `/root/obvious-accounts/eu-test1/manifest.json`
- `/root/obvious-accounts/eu-test1/storage_state.json`
- `/root/obvious-accounts/eu-test1/shots/`（截图，排错用）
- `/root/obvious-accounts/index.json`（自动 append）

### 备用：手动邮箱

```bash
DISPLAY=:99 python3 /root/Toolkit/scripts/obvious_provision.py \
    --proxy socks5://127.0.0.1:10822 \
    --email me+xyz@outlook.com --password 'YourStrongPass!' \
    --label backup-1
```

⚠️ 手动邮箱必须提前注册好且能收信。mailtm 域名被屏蔽时才用此方式。

### 批量注册（带间隔）

```bash
PORTS=(10821 10822 10823 10824 10825)
for i in "${!PORTS[@]}"; do
  DISPLAY=:99 python3 /root/Toolkit/scripts/obvious_provision.py \
      --proxy socks5://127.0.0.1:${PORTS[$i]} \
      --label "batch-$i" --check-ip
  sleep 3600  # 1 小时间隔，避开同一 ASN 速率风控
done
```

---

## 五、使用账号

### CLI 快速执行

```bash
python3 /root/Toolkit/scripts/obvious_executor.py \
    --account eu-test1 \
    --exec "uname -a && df -h /home/user && python3 --version"
```

### Python 库用法

```python
import json
from obvious_sandbox import ObviousSandbox

sb = ObviousSandbox.from_account("eu-test1")
result = sb.execute("print('hello from sandbox')")
print(result)

# shell 命令
out = sb.shell("uname -a && free -m")
print(out)
```

### 检查账号状态

```bash
# 单账号
python3 /root/Toolkit/scripts/obvious_executor.py --account eu-test1 --health

# 所有账号
python3 /root/Toolkit/scripts/obvious_executor.py --health --all
```

---

## 六、Pool 管理（多账号并发）

`obvious_pool.py` 是推荐的多账号接口，优先用它而非直接调 `obvious_client.py`。

```bash
cd /root/Toolkit/scripts

# 查看所有账号状态表格
python3 obvious_pool.py status

# 强制刷新健康状态
python3 obvious_pool.py refresh

# 用最佳账号执行一条命令（mode 必须用 auto，fast 模式无 run-shell 工具）
python3 obvious_pool.py ask "run: uname -a && python3 --version"

# 并发批量执行（文件每行一条 prompt）
python3 obvious_pool.py batch /tmp/prompts.txt --concurrent 2

# 自动补号（健康账号 < target 时自动 provision）
python3 obvious_pool.py maintain --target 3 --max-provision 1
```

> ⚠️ `ask`/`batch` 必须用 `mode=auto`，`fast` 模式的工具列表**不含** `run-shell`，
> 会报 `"unavailable tool"` 错误。

---

## 七、Credit 管理

每条用户消息约 **0.25 credit**，25 credits ≈ 100 次对话。

```bash
# 查看单账号 credit 余额
python3 obvious_executor.py --account eu-test1 --credits

# 查看所有账号余额汇总
python3 obvious_executor.py --credits-all

# 强制重置（删除所有项目，归零计数器）
python3 obvious_executor.py --account eu-test1 --reset-credits

# 超过阈值才重置（如 >= 20 credits 用量才执行）
python3 obvious_executor.py --account eu-test1 --reset-credits --reset-threshold 20
```

**自动 credit 监控**：`obvious_keepalive.py` 每次 tick 检查余额，
若真实余额 (`creditBalance`) < 3 则标记账号为 `dead` 并触发 autoprovision 补号；
余额 >= 5 且消耗 >= 10 时自动清理计数器。

---

## 八、沙箱休眠与唤醒

沙箱约 **30 分钟不活跃后自动 pause**，恢复需几秒。

```bash
# obvious_keepalive.py 自动处理唤醒（PM2 守护）
pm2 logs obvious-keepalive --lines 30

# 手动唤醒单个账号
python3 obvious_executor.py --account eu-test1 --health
```

**唤醒后资源初始化**（`obvious_keepalive.py` 自动执行）：
- 文件描述符上限：4096 → 65536
- `/tmp` tmpfs：扩至 7 GB
- 记录 `/dev/shm` 当前状态

---

## 九、修复账号（projectId/threadId/sandboxId 为 null）

```bash
# 有头模式（默认，方便看截图排错）
DISPLAY=:99 python3 /root/Toolkit/scripts/repair_account.py --label cz-test1

# 无头模式
python3 /root/Toolkit/scripts/repair_account.py --label cz-test1 --headless
```

脚本会：打开 obvious.ai、创建新 project、发一条 ping 消息、从 URL + API 调用中
抓取新的 projectId/threadId，并从 messages API 拿 sandboxId，最后写回 manifest。

---

## 十、PM2 守护进程

两个守护进程由 `ecosystem.config.cjs` 管理，已注册在 pm2 中：

| 进程名 | 脚本 | 功能 |
|---|---|---|
| `obvious-keepalive` | `obvious_keepalive.py` | 每 90–180 s ping 全部账号，自动唤醒，credit 监控 |
| `autoprovision` | `obvious_autoprovision.py` | 每 600 s 检查池，低于 MIN_POOL=10 时自动注册 |

```bash
pm2 list | grep -E "obvious|autoprov"
pm2 logs obvious-keepalive --lines 50
pm2 logs autoprovision --lines 50
```

---

## 十一、账号注册表（index.json）

```bash
# 查看所有账号（标签、出口 IP、创建时间）
jq -r '.[] | "\(.label)\t\(.egressIp)\t\(.createdAt[:10])"' \
    /root/obvious-accounts/index.json | column -t -s $'\t'

# autoprovision 状态总览（包含 credit）
python3 /root/Toolkit/scripts/obvious_autoprovision.py --status
```

---

## 十二、新人一键全链路（快速参考）

```bash
# 0. 确认环境
pm2 list | grep -E "xvfb|xray|obvious"

# 1. 查可用端口
for p in 10821 10822 10823 10824 10825; do
  ip=$(curl -s --max-time 5 --socks5-hostname 127.0.0.1:$p https://api.ipify.org 2>/dev/null)
  [ -n "$ip" ] && echo "port=$p ip=$ip"
done

# 2. 注册
DISPLAY=:99 python3 /root/Toolkit/scripts/obvious_provision.py \
    --proxy socks5://127.0.0.1:10823 --label new-acc-1 --check-ip

# 3. 验证
python3 /root/Toolkit/scripts/obvious_pool.py refresh

# 4. 测试沙箱
python3 /root/Toolkit/scripts/obvious_pool.py ask \
    "run: python3 --version && echo SANDBOX_OK"
```

---

## 附录 A：obvious.ai API 接口参考

所有接口在 `https://api.app.obvious.ai/prepare/` 下，使用 Cookie 认证
（`__Secure-better-auth.session_token` + `obvious_www_session`），无需 CSRF token。

| Method | Path | 用途 |
|---|---|---|
| POST | `/api/v2/agent/chat/{threadId}` | 发送消息，返回 `executionId` |
| GET | `/threads/{threadId}/messages` | 完整消息 + tool-call 历史 |
| GET | `/hydrate/project/{projectId}` | 项目状态，含 `agentStatus` |
| GET | `/modes` | 可用 agent 模式列表 |
| GET | `/workspaces` | workspace 信息含 `creditBalance` |
| GET | `/workspaces/{wks}/billing/status` | 订阅层级 + 支付状态 |
| GET | `/user/event-stream` | SSE 实时更新（目前用轮询替代）|

**POST chat body 示例**：

```json
{
  "message": "uname -a",
  "messageId": "<uuid>",
  "projectId": "prj_xxx",
  "fileIds": [],
  "modeId": "auto",
  "timezone": "UTC"
}
```

**tool-result 结构**（`run-shell` 工具）：

```json
{
  "type": "json",
  "value": {
    "data": {
      "cwd": "/home/user/work",
      "stdout": "...", "stderr": "", "exitCode": 0,
      "sandboxId": "iwn9r8g0s4p2vlkpoan1e",
      "durationMs": 1311
    }
  }
}
```

**可用 agent 模式**：

| id | 名称 | 说明 |
|---|---|---|
| `auto` | Auto | 默认平衡模式，**含 run-shell** |
| `fast` | Fast | Haiku 4.5，1M context，**不含 run-shell** |
| `deep` | Deep Work | GPT-5.4 高级推理 |
| `analyst` | Analyst | GPT-5.4 量化分析 |
| `skill-builder` | Skill Builder | 自定义 skill 构建 |

---

## 附录 B：注意事项

- mailtm 域名（`deltajohnsons.com`）不稳定，若被 obvious 拉黑，改 `mailtm_client.py`
  顶部的 `DOMAIN` 即可（mailtm 提供多域名）
- obvious 注册页若改版（新增 captcha 或邮箱验证 step），脚本会在截图停下，
  去 `<out-dir>/<label>/shots/` 查看具体出错画面，再调整 selector
- `obvious-accounts/` 目录已加入 `.gitignore`，`storage_state.json` 含会话凭据，
  **绝对不要提交到 git**
- obvious 沙箱出口是 datacenter ASN，**不适合**作 Replit 注册的 IP 池，
  reCAPTCHA Enterprise 会同样拒绝

---

## 附录 C：已修复 Bug 记录

| Bug | 文件 | 现象 | 修复 |
|-----|------|------|------|
| `extract_shell_results` AttributeError | `obvious_client.py` | `tool-result` 是 `error-text` 类型时崩溃 | commit `7053ffe` |
| `mode=fast` 无 `run-shell` | pool/executor | 沙箱命令全部报 "unavailable tool" | commit `7053ffe` |
| `get_agent_status` SSE 解析偏移 | `obvious_client.py` | 扫描可能错过真实 status | commit `7053ffe` |
| credit 检查用消耗计数器 | `obvious_keepalive.py` | reset 后归零，耗尽账号误判健康 | commit `dd79ff2` |
