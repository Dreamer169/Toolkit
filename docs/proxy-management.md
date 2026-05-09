# 代理管理系统文档

> **版本**：proxy_manager.py **v1.1**
> **最后更新**：2026-05-09
> **文件位置**：VPS `45.205.27.69` → `/data/Toolkit/scripts/proxy_manager.py`
> **数据库**：`/data/proxy_db.json`（自动持久化，重启不丢失）
> **GitHub**：https://github.com/Dreamer169/Toolkit（main 分支）

---

## 快速接手（新人速查）

```bash
# SSH 进入服务器
sshpass -p 'HGxQ0ADXPD0b' ssh root@45.205.27.69

# 查看代理库状态
python3 /data/Toolkit/scripts/proxy_manager.py status

# 全量刷新所有来源
python3 /data/Toolkit/scripts/proxy_manager.py refresh

# 只刷新 ip2free（最常用）
python3 /data/Toolkit/scripts/proxy_manager.py refresh-source ip2free

# 存活性检测（并发 20 线程）
python3 /data/Toolkit/scripts/proxy_manager.py probe --workers 20

# 选一个可用代理（注册 ip2free 时必须排除 ip2free 来源）
python3 /data/Toolkit/scripts/proxy_manager.py pick --not-for ip2free

# 列出 ip2free 存活代理
python3 /data/Toolkit/scripts/proxy_manager.py list --source ip2free --alive-only

# 从文件批量导入代理
python3 /data/Toolkit/scripts/proxy_manager.py load-file /tmp/ip2free_proxies.json --source ip2free

# 守护进程（每 30min 刷新，每 10min 探测）
python3 /data/Toolkit/scripts/proxy_manager.py daemon --interval 1800 --probe-interval 600
```

---

## 1. 系统架构总览

```
Python Library / CLI
      │
      ▼
  ProxyManager
      │
      ├── ip2free       -- 住宅 SOCKS5（user:pass 认证）
      ├── novproxy      -- 住宅 SOCKS5（user:pass 认证）
      ├── local_xray    -- 本地 xray SOCKS5 (127.0.0.1:10850-10859)
      ├── proxyscrape   -- 免费匿名 SOCKS5（无认证）
      └── manual        -- 手动添加（无限制）
             │
             ▼
       /data/proxy_db.json    ← 持久化 JSON DB，每次写入自动保存
```

---

## 2. 平台排除规则（核心）

**不同平台的代理不能用来注册同一平台的新账号**（防止 IP 被标记）：

| 代理来源 | 不能用于 | 原因 |
|---------|---------|------|
| `ip2free` | ip2free 注册 | 同平台出口 IP，必然被识别 |
| `novproxy` | novproxy 注册 | 同上 |
| `local_xray` | 无限制 | 本地独立出口，可用于任何平台 |
| `proxyscrape` | 无限制 | 无平台关联 |
| `manual` | 无限制 | 手动添加默认无限制 |

**代码用法（Python）**：

```python
from proxy_manager import ProxyManager

pm = ProxyManager()

# 注册 ip2free 时，必须排除 ip2free 来源
proxy = pm.pick(not_for="ip2free")

# 注册 novproxy 时
proxy = pm.pick(not_for="novproxy")

# 通用场景（无限制）
proxy = pm.pick()

if proxy:
    print(proxy.socks5h_url)   # socks5h://user:pass@host:port
    pm.report_success(proxy.uid)
else:
    print("No proxy available")
```

---

## 3. ip2free 代理详解

### 3.1 账号列表（2026-05-09 状态）

| 邮箱 | 状态 | 邀请码 | 备注 |
|------|------|--------|------|
| emily_gomez98@outlook.com | ❌ 密码错误 | I3qD20OQyg | 密码已更改，无法登录 |
| sophiagray574@outlook.com | ✅ OK | 9A8a27QSKi | |
| e.lewis904@outlook.com | ✅ OK | x9ZmE6Y4Ia | |
| rylan_rivera98@outlook.com | ✅ OK | 6b9e4jo42S | |
| reg2026a1@guerrillamailblock.com | ✅ OK | kTxgsUUCb6 | 2026-05 注册批 |
| reg2026b2@guerrillamailblock.com | ✅ OK | agPYdgA3yp | |
| reg2026c3@guerrillamailblock.com | ✅ OK | ahnQlcicsk | |
| ip2r_ysrlrfeu@wshu.net | ✅ OK | qE8Z4f95Xa | 邀请码注册，密码 Reg2026@Secure! |
| ip2r_7vgq5rxn@wshu.net | ✅ OK | obh0m0RO8B | |
| ip2r_lhs9p54x@wshu.net | ✅ OK | i6GhAFnhZK | |
| 5pygn9r8bhlie7@wshu.net | ❌ 账号不存在 | — | 注册失败，已从列表移除 |
| fd46qce8g3fm5m@wshu.net | ❌ 账号不存在 | — | 已从列表移除 |
| bjd6c2ayft0zr1@wshu.net | ❌ 账号不存在 | — | 已从列表移除 |
| caseyjon2860@cuvox.de | ❌ 账号不存在 | — | 已从列表移除 |
| jamesdav8027@dayrep.com | ❌ 账号不存在 | — | 已从列表移除 |
| emilywan9588@teleworm.us | ❌ 账号不存在 | — | 已从列表移除 |

### 3.2 免费代理池（freeList）工作原理

- 每个账号调用 `/api/ip/freeList` 返回**同一个共享池**（约 10 个代理）
- 代理每天轮换约 3 次（`last_checked_at` 更新时凭据同时更换）
- `proxy_uid` 不变，但 `username` / `password` 随轮换更换
- proxy_manager 通过 `last_checked_at` 变化自动检测并更新凭据
- `IP2FREE_STALE_DAYS = 1.5`：超过 1.5 天未见凭据更新的代理标为 stale（跳过探测）

### 3.3 活动任务奖励系统

完成以下任务后，ip2free 会发放**带时限的住宅代理**：

| task_id | task_code | 任务 | 奖励 |
|---------|-----------|------|------|
| 6 | client_click | 每天点击一次 | 1天不限流量住宅代理（US / SG） |
| 8 | register_one_three | 每周邀请 1 人 | 3天不限流量住宅代理（UK） |
| 2 | register_three | 每月邀请 3 人 | 30天不限流量住宅代理（MX + US 各1） |
| 7 | register | 每月邀请 1 人 | 10元无门槛优惠券 |
| 9 | manual_review | 限时社媒分享 | 30天不限流量×10（US） |
| 11 | manual_review | 限时社媒分享 | 1GB 动态住宅流量包 |

活动代理的 `expires_at` 字段非空，proxy_manager 到期后自动跳过（`is_expired()` 检查）。

提取活动代理：

```bash
# 提取所有账号 freeList（含活动奖励代理）
python3 /data/Toolkit/scripts/ip2free_get_proxies.py

# 导入 proxy_manager
python3 /data/Toolkit/scripts/proxy_manager.py load-file /tmp/ip2free_proxies.json --source ip2free
```

### 3.4 邀请码系统与 I3qD20OQyg 分析

邀请 3 人注册 → 解锁 task_id=2（30天美国 + 墨西哥代理）。

当前邀请码状态（2026-05-09）：

| 邀请码 | 所有者 | 已用 / 共需 | 状态 |
|--------|--------|------------|------|
| **I3qD20OQyg** | emily_gomez98 | **1 / 3** | ⚠️ 账号密码丢失，奖励无法领取 |
| 9A8a27QSKi | sophiagray574 | 1 / 3 | 差 2 人 |
| x9ZmE6Y4Ia | e.lewis904 | 1 / 3 | 差 2 人 |
| 6b9e4jo42S | rylan_rivera98 | 0 / 3 | 未使用 |
| kTxgsUUCb6 | reg2026a1 | 0 / 3 | 未使用 |
| agPYdgA3yp | reg2026b2 | 0 / 3 | 未使用 |
| ahnQlcicsk | reg2026c3 | 0 / 3 | 未使用 |

**I3qD20OQyg 分析（外部传入的邀请码）**：
- 所有者：emily_gomez98@outlook.com（密码已丢失，所有已知密码均失效）
- 当前进度：1/3（ip2r_ysrlrfeu@wshu.net 已用此码注册）
- 问题：即使再注册 2 人完成 3/3，emily 也无法登录领取奖励代理
- **建议**：改用其他账号邀请码（如 sophiagray574 的 9A8a27QSKi，可立即领取奖励）

批量注册新账号（使邀请者达到 3 人目标）：

```bash
python3 /data/Toolkit/scripts/ip2free_register_invite.py \
  --invite-code 9A8a27QSKi \
  --count 2 \
  --email-domain wshu.net

# 追踪文件
cat /data/ip2free_invite_state.json    # 各邀请码使用进度
cat /data/ip2free_new_accounts.json    # 新注册的账号列表
```

---

## 4. novproxy 代理详解

### 4.1 注册获取代理

```bash
# 方式一：纯 API（推荐，mail.tm 临时邮箱，无浏览器）
python3 /data/Toolkit/scripts/novproxy_register_final.py --count 3

# 方式二：pydoll 浏览器（需 Outlook 邮箱读取验证码）
python3 /data/Toolkit/scripts/novproxy_register_worker.py \
  --accounts '[["email@domain.com","password"]]' \
  --proxy socks5://127.0.0.1:10850

# 账号文件（proxy_manager 自动加载）
cat /data/novproxy_accounts.json
```

### 4.2 注册脚本指纹质量对比

| 脚本 | 浏览器引擎 | 反指纹措施 | 风险 |
|------|-----------|-----------|------|
| novproxy_register_worker.py | pydoll (CDP) | 仅 webrtc_leak_protection | ⚠️ navigator.webdriver 暴露 |
| novproxy_register_final.py | 无（纯 requests） | N/A | ✅ API 直连，无需反指纹 |
| ip2free_reg_final.py | patchright | AutomationControlled + UA | ✅ 反检测完善 |
| ip2free_register_invite.py | 无（纯 requests） | N/A | ✅ API 直连 |
| outlook_factory_sandbox.py | patchright | 完整 stealth | ✅ 最强防检测 |

**novproxy_register_worker.py 指纹风险详情**：
- pydoll 不注入 `navigator.webdriver = false`（CF 机器人检测必查此字段）
- 缺少 locale / timezone / canvas / WebGL 指纹伪装
- 改进方案（在 `_make_options()` 中添加）：

```python
options.add_argument('--disable-blink-features=AutomationControlled')
options.add_argument('--lang=en-US')
```

- 或直接切换为 `novproxy_register_final.py`（API 方式，成功率更高）

---

## 5. local_xray 代理

- 端口：`10850–10859`（10 个端口）
- 偶数端口（10850/10852/10854/10856/10858）：VLESS，经 jimhacker CF Worker
- 奇数端口（10851/10853/10855/10857/10859）：Shadowsocks，独立出口不经 CF Worker
- **推荐用奇数端口**发 Microsoft / Outlook 请求，不消耗 jimhacker 每日 100k 配额

TypeScript 用法（`proxy-fetch.ts`）：

```typescript
import { pickProxyForAccount, microsoftFetch } from "./lib/proxy-fetch";

// 按账号 ID 稳定选取（同账号始终用同一出口 IP）
const proxy = pickProxyForAccount(accountId);   // => "http://127.0.0.1:10851"

// Microsoft 请求走代理
const resp = await microsoftFetch(url, init, proxy);
```

---

## 6. proxyscrape 免费代理

- 来源：proxyscrape.com API（SOCKS5，匿名）
- 每次刷新最多注入 30 个，存活率约 60-80%
- 适合低风险批量操作；不适合账号注册（IP 质量差）

---

## 7. 代理选取算法（pick）

```
pick(not_for="ip2free") 执行流程：
  1. 过滤 source="ip2free" 代理（排除规则）
  2. 过滤已过期（expire_ts < now）
  3. 过滤黑名单（blacklist_until > now）
  4. 过滤 fail_count >= 3
  5. 优先返回 alive=True 的代理
  6. 无存活则从 alive=None 里选并即时探测
  7. report_success → fail_count 清零
     report_failure → fail_count++，达阈值加黑名单 5 min（BLACKLIST_TTL=300s）
```

---

## 8. CLI 命令速查表

```bash
python3 proxy_manager.py status                                  # 总览
python3 proxy_manager.py refresh                                 # 全量刷新
python3 proxy_manager.py refresh-source ip2free                  # 单源刷新
python3 proxy_manager.py probe                                   # 只探需要更新的
python3 proxy_manager.py probe --force                           # 强制全探
python3 proxy_manager.py probe --workers 30                      # 调大并发
python3 proxy_manager.py pick --not-for ip2free                  # 排除 ip2free 来源
python3 proxy_manager.py pick --country US                       # 指定国家
python3 proxy_manager.py pick --source local_xray                # 指定来源
python3 proxy_manager.py list --source ip2free --alive-only      # 列出存活代理
python3 proxy_manager.py add socks5://user:pass@1.2.3.4:1080     # 手动添加
python3 proxy_manager.py load-file /tmp/proxies.json             # 批量导入
python3 proxy_manager.py inject-resi-pool --not-for ip2free      # 注入 resi_pool
python3 proxy_manager.py daemon --interval 1800                  # 守护进程
```

---

## 9. 代码库文件速查

| 文件（路径相对 /data/Toolkit/） | 用途 |
|-------------------------------|------|
| scripts/proxy_manager.py | 统一代理管理器（核心，v1.1） |
| scripts/ip2free_get_proxies.py | ip2free 多账号批量提取（v2，9 账号） |
| scripts/ip2free_register_invite.py | ip2free 邀请码注册（纯 API） |
| scripts/ip2free_reg_final.py | ip2free 注册（patchright 浏览器） |
| scripts/ip2free_solve_v4.py | ip2free 活动任务求解（领奖代理） |
| scripts/ip2free_monitor2.py | ip2free 账号监控 + 自动任务完成 |
| scripts/novproxy_register_worker.py | novproxy 注册（pydoll + Outlook 邮箱） |
| scripts/novproxy_register_final.py | novproxy 注册（纯 API + mail.tm） |
| artifacts/api-server/src/lib/proxy-fetch.ts | TypeScript xray 代理选取 |
| /data/proxy_db.json | 代理数据库（运行时） |
| /data/proxy_accounts.json | 账号覆盖文件（可选，覆盖内置列表） |
| /data/ip2free_invite_state.json | 邀请码使用进度追踪 |
| /data/ip2free_new_accounts.json | 邀请注册的新账号列表 |
| /data/novproxy_accounts.json | novproxy 账号及代理凭据 |

---

## 10. 常见问题排查

| 现象 | 原因 | 解决方法 |
|------|------|---------|
| ip2free 代理全死 | 凭据已轮换（每天约 3 次） | `refresh-source ip2free` |
| ip2free 登录「密码错误」 | 密码被修改 | emily_gomez98 已知失效，忽略 |
| ip2free 登录「用户名不存在」 | 注册失败，账号未激活 | 已从账号列表移除（v1.1） |
| `pick` 返回 None | 代理池耗尽或全死 | 先 `probe --force` 再 `refresh` |
| novproxy 注册失败率高 | pydoll 无 stealth，CF 检测 | 改用 `novproxy_register_final.py` |
| I3qD20OQyg 奖励无法领取 | emily 密码丢失 | 用其他账号邀请码（如 9A8a27QSKi） |
| 代理进了黑名单 | fail_count >= 3 | 等 5 分钟（BLACKLIST_TTL）自动解除 |
| 活动代理 task_id=6 未领 | 每天限 1 次 | 用 ip2free_monitor2.py 自动每日领取 |

---

## 11. 变更记录

### v1.1（2026-05-09）

| # | 变更 | 说明 |
|---|------|------|
| 1 | 移除 6 个死亡账号 | 5pygn9r8bhlie7 / fd46qce8g3fm5m / bjd6c2ayft0zr1 (wshu.net)、caseyjon2860 / jamesdav8027 / emilywan9588 — 全部「用户名不存在」 |
| 2 | emily_gomez98 注释掉 | 密码已更改，登录失败；邀请码 I3qD20OQyg 已用 1/3 但无法领奖 |
| 3 | ip2free_get_proxies.py 升级 v2 | 1 账号 → 9 账号；新增 expires_at 字段、--out-json/txt 参数 |
| 4 | 新增 docs/proxy-management.md | 本文档 |

### v1.0（初始版本）

- 支持 ip2free / novproxy / local_xray / proxyscrape / manual 五大来源
- 平台排除规则（ip2free 不能用于 ip2free 注册）
- 持久化 JSON 数据库、存活探测、黑名单机制、守护进程模式
