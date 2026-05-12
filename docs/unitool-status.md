# unitool 系统状态 & 修复记录 (2026-05-12)

## 当前服务状态

| PM2 id | 名称                 | 状态     | 说明                          |
|--------|----------------------|----------|-------------------------------|
| 75     | unitool-proxy        | online   | SSID 池反代 v5.38，端口 8089 |
| 69     | unitool_chain_v3     | online   | 注册链 v3.2（Turnstile v4）   |
| 70     | unitool_verify_rescue| online   | 验证救援（Graph Token 正常）  |
| 6      | http-socks5-bridge   | online   | HTTP/SOCKS5 桥接             |
| 74     | nest-bridge          | online   | Nesting 代理桥                |

## 修复记录

### Fix-7 (2026-05-12) commit 549b5ff — Turnstile bypass v4

**根本原因:**
pydoll._bypass_cloudflare() 查找 span.cb-i（旧版 Managed Turnstile 复选框）。
unitool.ai 已切换到 **Invisible Turnstile** — CF 根据浏览器指纹自动求解，
无需用户点击，token 直接写入 input[name=cf-turnstile-response]，span.cb-i 不存在。
→ 10s 无限循环超时，所有注册/登录失败。

**修复文件:**
- scripts/unitool_http_register.py: _bypass_wait() v4
- scripts/unitool_login.py: _bypass_turnstile() v4 + 辅助函数

**新逻辑 (bypass v4):**
1. 注入 postMessage 拦截器 (_PM_HOOK_JS / _PM_JS) — 捕获 CF iframe 回传 token
2. 等待 token 自然出现 30s（invisible 模式自动求解，无需点击）
3. 30s 后 fallback: 尝试 managed bypass (span.cb-i checkbox)
   → 日志输出: "managed N/A (invisible confirmed)" 表示确认 invisible 模式
4. 60s: reload 页面重试

### Fix-6 (2026-05-12) — 端口 8089 孤儿进程

unitool-proxy (PM2 id=75) 由于 PID 2177638 占用 8089 端口无法启动。
kill -9 2177638 → pm2 restart 75 → online。

### Fix-5 (2026-05-11) — commit 472b738

nestingproxy_bridge.py: dangling send_error(502) outside except block.

## SSID 池

- pool_size: ~1387（最新 hotpush 后）
- RESI 存活端口: 10851,10853,10854,10855,10857,10859,10872,10888
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
