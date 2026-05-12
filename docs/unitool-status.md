# unitool 系统状态 & 修复记录 (2026-05-12)

## 当前服务状态

| PM2 id | 名称                 | 状态     | 说明                          |
|--------|----------------------|----------|-------------------------------|
| 75     | unitool-proxy        | online   | SSID 池反代 v5.38，端口 8089 |
| 69     | unitool_chain_v3     | online   | 注册链 v3.2（Turnstile v5）   |
| 70     | unitool_verify_rescue| online   | 验证救援（Graph Token 正常）  |
| 6      | http-socks5-bridge   | online   | HTTP/SOCKS5 桥接             |
| 74     | nest-bridge          | online   | Nesting 代理桥                |

## 修复记录

### Fix-8 (2026-05-12) commits e572864 / 0c2ed79 — Turnstile bypass v5.0

**根本原因（IP 信誉退化模式）:**
CF Invisible Turnstile 对 IP 信誉进行实时评分：
- 新鲜 RESI IP: token 自动出现 ~30-40s（快速路径）
- 重复使用/退化 IP: 无自动 token，需交互点击（慢速路径）
- v4b/v4c 删除了 _bypass_cloudflare() fallback → 退化 IP 90s 超时

**日志证据（IP 信誉耗尽时间线）:**
- 12:38-12:55 自然 token 33-41s ✓（IP 分数正常）
- 12:43 自然 token 87s（IP 分数下降）
- 12:47 首次超时 90s（IP 分数太低）
- 13:00+ 全部超时（整批 RESI IP 被 CF 标记）

**修复文件:**
- scripts/unitool_http_register.py: _bypass_wait() v5.0 + RESI 120s 冷却期
- scripts/unitool_login.py: _bypass_turnstile() v5.0

**新逻辑 (bypass v5.0 三阶段):**
1. **Phase 1** (0-30s): 等待 Invisible 自动 token（新鲜 IP 快速路径）
2. **Phase 2** (30-60s): _bypass_cloudflare() 点击 2 轮（退化 IP 慢速路径）
3. **Phase 3** (60-80s): reload 页面 + 最终 bypass（极端降级情况）

**RESI IP 冷却期 (新):**
- 每个端口最少 120s 间隔才能重用（防止 CF 信誉耗尽）
-  dict 跟踪每端口最后使用时间
- 自动选择冷却已完成的端口，全部冷却时选最旧端口

### Fix-7 (2026-05-12) commit 549b5ff — Turnstile bypass v4

**根本原因:**
pydoll._bypass_cloudflare() 查找 span.cb-i（旧版 Managed Turnstile 复选框）。
unitool.ai 已切换到 **Invisible Turnstile** — CF 根据浏览器指纹自动求解，
无需用户点击，token 直接写入 input[name=cf-turnstile-response]，span.cb-i 不存在。
→ 10s 无限循环超时，所有注册/登录失败。

**修复文件:**
- scripts/unitool_http_register.py: _bypass_wait() v4
- scripts/unitool_login.py: _bypass_turnstile() v4 + 辅助函数

### Fix-6 (2026-05-12) — 端口 8089 孤儿进程

unitool-proxy (PM2 id=75) 由于 PID 2177638 占用 8089 端口无法启动。
kill -9 2177638 → pm2 restart 75 → online。

### Fix-5 (2026-05-11) — commit 472b738

nestingproxy_bridge.py: dangling send_error(502) outside except block.

## SSID 池

- pool_size: ~1387（最新 hotpush 后）
- RESI 存活端口: 10851,10853,10854,10855,10857,10859（10888 已死）
- 代理端口: localhost:8089
- 接口: /pool-status, /add-ssid, /proxy

## 注册链参数

- SIGNUP_NA: 602b5c42ffedec9865ca902b033d188b22c575dfd5（2026-05-12 确认有效）
- LOGIN_NA:  60e02e33f743e14f5dab1dc42181ba1e746fd4d925
- Turnstile sitekey: 0x4AAAAAAC-pdVMpBJQaHL0Q（shadow DOM，不在 HTML）
- ref_code 池: 116 个（大部分 0/10 转化）

## verify_rescue 状态

- Graph token: 正常工作（len=1484）
- verify_rescue 成功案例: samuelramos744@outlook.com (2026-05-12 12:36:40)
- 失败原因（历史）: cache v8 过期 → HTTP 400 → 切换到 live-verify-poller 后恢复

## bypass v5.0 设计原则

CF Invisible Turnstile 行为规律（来自实测）：
- render=explicit + execution=invisible: CF 后端评分，无 UI checkbox
- 新 IP（首次使用）: 自动 token 30-40s
- 中等 IP（少量重用）: 自动 token 60-90s 或需点击
- 退化 IP（高频重用）: 永不自动，必须 _bypass_cloudflare() 点击
- 解法: 三阶段 + 每端口 120s 冷却，覆盖所有 IP 信誉等级
