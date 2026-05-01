# 沙盒开发指南 — Sandbox & Pipeline Engineering Guide
> 最后更新: 2026-05-01 | VPS: 45.205.27.69 | 状态: **生产运行中**

---

## 0. 执行摘要 (TL;DR)

| 指标 | 当前值 (2026-05-01) |
|------|---------------------|
| Outlook 账号 | **374 个**（333 个含 OAuth refresh_token） |
| Replit 账号 | **107 个**（5 active / 4 stale / 98 exists_no_password） |
| Outlook 创建速度 | ~3–5 个/天（CF IP 池 + patchright） |
| Replit 成功率 | ~5% active（CAPTCHA + CF 挑战待优化） |
| 数据库 | PostgreSQL `toolkit` @ localhost:5432 |

---

## 1. 真实架构（与原计划对比）

### 1a. 原计划架构（obvious.ai 沙盒方案）
```
obvious acc-4 (e2b sandbox)
   │ Playwright + Mobile UA → outlook.com 注册
   │ POST VPS /api/accounts
   ↓
VPS PostgreSQL
   ↓
obvious acc-6/7/8 (e2b sandbox)
   │ Playwright → replit.com 注册
   └── POST VPS /api/replit-accounts
```
> 问题: e2b 沙盒超时后删除（lifecycleMode: "legacy"），唤醒失败（502/sandbox not found），
> 沙盒 ID `ivcvhq4db8y13qnwe2lm8` 已永久失效。

### 1b. 实际运行架构（VPS 直接方案）
```
VPS 45.205.27.69
├── outlook_register.py (patchright, Desktop UA, CF IP 池)
│     │ 创建 outlook.com 账号 + OAuth2 refresh_token
│     └── → PostgreSQL accounts (platform='outlook')
│
├── replit_register.py (playwright-stealth, CDP broker, WARP 代理)
│     │ 读取 outlook refresh_token → 验证 OTP → 完成 Replit 注册
│     └── → PostgreSQL accounts (platform='replit')
│
├── autoprovision (obvious_autoprovision.py --watch --min-active 10)
│     └── 维护 10 个活跃 obvious.ai 账号池（用于 e2b 沙盒）
│
├── browser-model (CDP Chromium, WARP socks5://127.0.0.1:40000)
│     └── Replit 注册的主力浏览器（CF 绕过）
│
├── api-server (Node.js/Express, port 8081)
│     └── 管理账号、OAuth、CF 池、job 队列
│
└── PostgreSQL toolkit
      ├── accounts (platform=outlook: 374, platform=replit: 107)
      ├── proxies (xray 代理节点)
      └── 其他表
```

---

## 2. 关键组件详情

### 2a. Outlook 注册流水线

**脚本**: `/root/Toolkit/artifacts/api-server/outlook_register.py`（2799 行）

**核心参数**:
```bash
python3 outlook_register.py \
  --count 3 \
  --engine patchright \
  --wait 11 \
  --retries 2 \
  --proxy-mode cf \
  --cf-port 443
```

**关键机制**:
- **patchright**: 修改版 Playwright，通过 CDP 绕过 bot 检测
- **Desktop Chrome UA**: `Mozilla/5.0 (Windows NT 10.0; Win64; x64)...Chrome/131...`
  → 微软渲染传统 `<select>` 元素，`select_option()` 正常工作
- **CF IP 池**: 从 Cloudflare 全球节点选取验证通过 IP，通过 xray SOCKS5 中继
- **`date_option_selector`**: 双语 `:text-is` 选择器（zh-CN + en-US）
- **成功判定**: URL + auth cookie + DOM，任一命中即为成功

**生日下拉框实现**（Desktop UA 下可用）:
```python
# [name="BirthMonth"] 是原生 <select>，在 Desktop UA 下存在
page.locator('[name="BirthMonth"]').select_option(value=month, timeout=1000)
page.locator('[name="BirthDay"]').select_option(value=day)
page.locator('[name="BirthYear"]').fill(year)
```

**账号状态流**:
```
注册中 → needs_oauth → active (refresh_token 入库后)
                    → suspended (微软风控)
```

### 2b. Replit 注册流水线

**脚本**: `/root/Toolkit/artifacts/api-server/replit_register.py`（~2700 行）

**关键机制**:
- **CDP broker**: 连接 `browser-model` 的已暖 Chromium (port 9222)，复用 cf_clearance
- **WARP 代理**: socks5://127.0.0.1:40000 → Cloudflare backbone，避免 CF challenge
- **reCAPTCHA Enterprise**: 通过 warmup (YouTube + Google) 提升评分
- **OTP 验证**: 用 outlook_refresh_token 读取收件箱 OTP
- **`exists_no_password`**: 检测到 `isnewuser:false` → 邮箱已有 Replit 账号（前次注册完成但未保存）

**状态码含义**:
| 状态 | 含义 | 数量 |
|------|------|------|
| `active` | 注册成功，已入库 | 5 |
| `stale` | 老账号，凭证过期 | 4 |
| `exists_no_password` | 邮箱已绑定 Replit 账号，密码未存 | 98 |

### 2c. 代理基础设施

| 端口 | 类型 | 用途 |
|------|------|------|
| 10808 | xray VLESS | 通用 |
| 10809 | xray SOCKS5 | 通用 |
| 10827 | xray SOCKS5 | acc-4 |
| 10828 | xray SOCKS5 | 备用 |
| 10857 | xray SOCKS5 | broker Chromium |
| 40000 | WARP SOCKS5 | Replit 注册（CF backbone） |
| 19080 | socat 中继 | 对外暴露 |

---

## 3. obvious.ai 沙盒方案（历史/备用）

### 3a. 沙盒基础环境

| 项目 | 值 |
|------|-----|
| OS | Linux e2b.local 6.1.158 x86_64 (Debian 13 trixie) |
| Python | 3.13.12 |
| RAM | 8 GB / CPU 2 核 |
| 出口 IP | 34.105.125.127 (GCP us-central1) |
| Playwright | 1.59 + Chromium 147 |
| 持久化 | `/home/user/work/` 跨暂停永久保留 |
| 自动暂停 | 30 min 无活动 |
| Exec URL | `https://49999-{sandboxId}.e2b.app/execute` |

### 3b. sandbox_guide 历史账号

| 标签 | 旧 sandboxId | 旧 projectId | 状态 |
|------|-------------|-------------|------|
| acc-4 | ivcvhq4db8y13qnwe2lm8 | prj_FH65pHbW | **已失效** |

**沙盒失效原因**: e2b legacy 模式下，长期不活动后沙盒被永久删除。
Ping thread `th_JWe9sf1I` 发送 150s 后仍 `isPaused:true`，无法唤醒。

**重建方案**: 需在 obvious.ai 界面手动打开新项目，获取新 sandboxId 后更新 manifest.json。

### 3c. 工厂脚本 Bug 修复记录

**`scripts/outlook_factory_sandbox.py`** — 沙盒内 Outlook 注册器

| 版本 | Bug | 修复 |
|------|-----|------|
| v1 | birthday select 在 Mobile UA 下失效 | v2 用 JS 强制赋值（仍失败，<select> 不存在） |
| v2 | JS 设值失败（FluentUI 无原生 select） | v2 保留 JS fallback |
| **v3** | **根因**: Mobile UA → FluentUI React（无 `<select>`） | **改用 Desktop UA** → 恢复原生 select |

**根因分析**:
```
Mobile UA (iPhone/Safari) → 微软返回 FluentUI React 版注册页
  → birthday Month/Day 为自定义 combobox → querySelectorAll('select') = []
  → JS 赋值无效 → 生日提交失败

Desktop UA (Windows/Chrome) → 微软返回传统 HTML 版注册页
  → birthday Month/Day 为原生 <select name="BirthMonth/BirthDay">
  → select_option(value=month) 正常工作 ✅
```

**v3 修复内容**:
```python
# 旧 v2（失败）
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0..."
ctx = await browser.new_context(
    user_agent=MOBILE_UA,
    viewport={"width": 390, "height": 844},
    is_mobile=True, has_touch=True,
)
# JS 赋值: fill_select_js("select[name='BirthMonth']") → not_found

# 新 v3（修复）
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...Chrome/131..."
ctx = await browser.new_context(
    user_agent=DESKTOP_UA,
    viewport={"width": 1280, "height": 800},
    # is_mobile/has_touch 默认 False
)
# 原生 select: page.locator('[name="BirthMonth"]').select_option(value=month) ✅
```

---

## 4. 数据库 Schema

```
postgresql://postgres:postgres@localhost/toolkit
```

**`accounts` 表关键字段**:
| 字段 | 说明 |
|------|------|
| `platform` | `outlook` / `replit` |
| `email` | 邮箱地址 |
| `password` | 注册密码 |
| `token` | OAuth access_token |
| `refresh_token` | OAuth refresh_token |
| `status` | active / needs_oauth / suspended / exists_no_password / stale |
| `proxy_port` | 注册时使用的 xray SOCKS5 端口 |
| `fingerprint_json` | 浏览器指纹 JSON |
| `cookies_json` | Playwright storage_state cookies |

---

## 5. 运行管理 (PM2)

```bash
pm2 list          # 查看所有服务
pm2 logs api-server --lines 50   # api-server 日志
pm2 logs autoprovision --lines 20
```

| PM2 ID | 名称 | 状态 | 说明 |
|--------|------|------|------|
| 36 | api-server | online | Node.js API, port 8081 |
| 41 | autoprovision | online | obvious.ai 账号池维护 |
| 43 | batch-provision | stopped | 批量预置（停用） |
| 19 | browser-model | online | CDP Chromium broker |
| 3 | fakemail-bridge | online | 临时邮件服务 |
| 51 | http-connect-proxy | online | HTTP CONNECT 代理 |

---

## 6. 常用调试命令

```bash
# 查看账号统计
PGPASSWORD=postgres psql -h localhost -U postgres -d toolkit -c \
  "SELECT platform, status, COUNT(*) FROM accounts GROUP BY platform,status ORDER BY platform,count DESC;"

# 查看最新 Outlook 账号
PGPASSWORD=postgres psql -h localhost -U postgres -d toolkit -c \
  "SELECT email, status, refresh_token IS NOT NULL as has_rt, created_at::date FROM accounts WHERE platform='outlook' ORDER BY created_at DESC LIMIT 10;"

# 查看 Outlook 注册截图
ls -lt /tmp/outlook_ok_*.png | head -5      # 成功
ls -lt /tmp/outlook_fail_*.png | head -5    # 失败
ls -lt /tmp/outlook_captcha_done_*.png | head -5  # 验证码通过

# 查看 outline_register.py 运行状态
ps aux | grep outlook_register | grep -v grep

# 手动触发 Outlook 注册
python3 /root/Toolkit/artifacts/api-server/outlook_register.py \
  --count 1 --engine patchright --wait 11 --proxy-mode cf --cf-port 443

# 沙盒工厂（需要活跃沙盒）
python3 /root/Toolkit/scripts/obvious_executor_v2.py \
  --account acc-4 \
  --exec-file /root/Toolkit/scripts/outlook_factory_sandbox.py \
  -- --count 1 --proxy
```

---

## 7. obvious.ai API 关键端点

```
POST /prepare/api/v2/agent/chat/{threadId}   ← 唤醒沙盒 / 发消息
GET  /prepare/projects/{projectId}/info       ← 获取 sandboxId, isPaused
GET  /prepare/hydrate/project/{id}?resources=threads  ← 获取 threadId
POST /prepare/projects                        ← 创建新项目
```

**沙盒 exec-server** (无需认证):
```
POST https://49999-{sandboxId}.e2b.app/execute   ← 执行 Python 代码
GET  https://49999-{sandboxId}.e2b.app/health    ← 健康检查
```

**重要**: exec-server 与 obvious.ai API 独立，无需 cookie 认证。
沙盒失效后 exec-server 返回 502。

---

## 8. 待优化事项

| 优先级 | 问题 | 状态 |
|--------|------|------|
| 🔴 高 | 98个 `exists_no_password` Replit 账号恢复 | 待处理 |
| 🔴 高 | outlook_register.py 运行 2+ 天，96.8% CPU（正常？） | 监控中 |
| 🟡 中 | Replit 注册成功率低（5 active / 107 total） | 优化中 |
| 🟡 中 | e2b 沙盒唤醒失败，需在 obvious.ai UI 手动重建 | 待处理 |
| 🟢 低 | outlook_factory_sandbox.py v3 Desktop UA 待验证 | 已修复 |
| 🟢 低 | sandbox_guide.md 与实际架构同步 | ✅ 本次更新 |

---

## 9. 变更日志

| 日期 | 变更 |
|------|------|
| 2026-05-01 | sandbox_guide.md v3 — 记录真实 VPS 直接架构 |
| 2026-05-01 | outlook_factory_sandbox.py v3 — Desktop UA 修复 birthday bug |
| 2026-05-01 | e2b 沙盒 ivcvhq4db8y13qnwe2lm8 确认永久失效 |
| 2026-04-29 | outlook_register.py 开始运行（当前仍在运行，已创建 374 账号） |
| 2026-04-25 | 首批 5 个 Replit 账号注册成功 |
| 2026-04-24 | VPS 直接方案启动，outlook_register.py + replit_register.py 部署 |
| 2026-05-01 | obvious.ai acc-4 沙盒环境确认：Playwright 1.59 + Chromium 147 |
| 2026-05-01 | 发现 Bug1 根因：Mobile UA → FluentUI（无 select）|
