# unitool 注册系统文档 v3.2

> 最后更新：2026-05-08  
> 维护者：chain_v3 自动链路

---

## 一、系统概览

全自动闭环链路，将 Outlook 账号批量注册为 unitool.ai 账号并激活邀请码。

```
outlook 账号池
    └─► unitool_chain_v3.py  (PM2, 一账号一进程)
          ├─► [快速] unitool_http_register.py  pydoll+JSfetch  ~20-35s
          │    └─► [降级] unitool_register.py  全浏览器  ~60-90s
          ├─► unitool_login.py   ssid 兆底登录
          ├─► unitool_reflink.py ref_code 读取
          └─► unitool_proxy.py   /add-ssid 热推
```

**PM2 关键服务**

| PM2 名称             | 脚本                              | 说明                      |
|----------------------|-----------------------------------|---------------------------|
| unitool-proxy        | artifacts/api-server/unitool_proxy.py | OpenAI 兼容反代 v5.26  |
| unitool_chain_v3     | scripts/unitool_chain_v3.py       | 注册主链路 v3.2          |
| unitool_verify_rescue| scripts/unitool_verify_rescue.py  | 验证邮件重发救援          |
| ref-cache-refresh    | scripts/unitool_ref_cache_refresh.py | 定时刷新 ref_code 缓存 |
| token-cache-refresh  | scripts/unitool_token_cache_refresh.py| 定时刷新 token 缓存    |

---

## 二、关键文件

```
scripts/
  unitool_chain_v3.py        主链路 v3.2（本文档对应版本）
  unitool_http_register.py   混合注册 v3.2（快速路径）
  unitool_register.py        全浏览器注册（降级兆底）
  unitool_login.py           ssid 兆底登录（httpOnly cookie CDP 捕获）
  unitool_reflink.py         从 /api/auth/session 提取 ref_code
  unitool_verify_rescue.py   verify_pending 账号救援
  resi_pool.py               住宅代理池（29端口，5min TTL 健康缓存）

artifacts/api-server/
  unitool_proxy.py           OpenAI 兼容反代（SSID 池 + 余额监控）
```

---

## 三、注册流程详解

### Step 0c — NA 哈希日常监控
每天一次 GET /en/entry，提取页面内 42 位十六进制哈希 (SIGNUP_NA)，与已知值比对。
**若变更立即 log 告警**，需更新 unitool_http_register.py 中的 _SIGNUP_NA_DEFAULT。

### Step 0a — 清理卡死账号
unitool_processing 标签超过 30 分钟的账号自动解锁，重新进入可用池。

### Step 0b — 水位检查
新鲜 Outlook 账号数量 < WATERMARK=5 时，非阻塞触发 outlook_register.py 补充。

### Step 4 — 注册（v3.2 双路径）

```
run_register_fast(email, ref_code)
  |
  |─[尝试] http_register(email, password, ref_code)
  |   pydoll Chrome(RESI) → bypass_cloudflare → JS fetch() POST
  |   约 20-35 秒，比全浏览器快 ~40%
  |   注意：ssid 为 httpOnly cookie，JS 无法读取
  |         → 注册成功后 ssid 由 Step 5 run_login() 捕获
  |
  |─[永久失败] already_registered → 直接返回失败，标 unitool_already
  |
  └─[暂态失败] bypass失败/CF拒绝/超时 → 降级全浏览器
      run_register() → subprocess unitool_register.py
      约 60-90 秒，全流程（含 ssid 捕获）
```

### Step 5 — ssid 三级兆底
1. reg_result["ssid"] —— 全浏览器模式直接返回（http 模式为空）
2. DB notes 读取 unitool_ssid= —— 历史写入可能截断（80/200字）
3. run_login() —— unitool_login.py 重新登录捕获 httpOnly ssid

### Step 6 — ssid 持久化
- DB notes 字段全长写入（db_save_ssid_full，修复旧版 80 字截断）
- /data/unitool_ssids/<label>.txt 文件持久化
- unitool_proxy.py /add-ssid 热推（proxy 立即可用）

### Step 7 — ref_code 激活
1. create_ref_code_via_proxy() — RESI代理 POST /api/ref-codes 生成专属邀请码
2. run_reflink() — unitool_reflink.py 读取并保存 ref_code 到 DB
3. 在主账号 notes 追加 ref_registered=email 用于 conversions 计数

---

## 四、账号标签系统（DB accounts.tags）

| 标签                      | 含义                                      | 下次处理         |
|---------------------------|-------------------------------------------|------------------|
| unitool_processing      | 正在处理中                                | 30min 后自动解锁 |
| unitool_registered      | 注册成功 ✅                               | 永久跳过         |
| unitool_already         | 已注册（服务器返回 already_registered）   | 永久跳过         |
| unitool_reg_retry       | 暂态失败（CF/timeout/bypass）             | 4h 后重试        |
| unitool_verify_pending  | 注册提交成功，验证邮件未到                | verify_rescue 处理|
| unitool_rescue_dead     | verify_rescue 多次失败，放弃              | 永久跳过         |
| unitool_ref_activated   | 已生成自己的 ref_code                     | —                |

失败分类器 classify_reg_fail(reason):
- already_reg* / user with like email → unitool_already（永久）
- no_verify_email / verify_email_not_found → unitool_verify_pending
- 其他所有错误 → unitool_reg_retry（4h 重试）

---

## 五、SIGNUP_NA 哈希说明

**什么是 SIGNUP_NA？**
unitool.ai 使用 Next.js Server Actions，注册请求头中需要 Next-Action: <42位十六进制哈希>。
此哈希在每次 Next.js 重新部署后会改变。

**当前已知值（v3.2 实测）：**
```
SIGNUP_NA: 602b5c42d2c7dccaa6e3a06bed4a8a99ba7d0bc4
LOGIN_NA:  60e02e331f0f6a6cac52b4a39a5cd45a18d1d0b9
```

**变更告警：** chain_v3 每天运行 _check_na_daily() 探测，变更时 log 输出 [na_probe] SIGNUP_NA 已变！
需立即更新 unitool_http_register.py 中的 _SIGNUP_NA_DEFAULT。

**手动探测：**
```bash
python3 /data/Toolkit/scripts/unitool_http_register.py --probe
```

---

## 六、http_register v3.2 技术细节

### 为什么 pydoll + JS fetch()，而不是纯 HTTP？

CF Turnstile token 绑定产生它的浏览器 cookie（__cf_bm / cf_clearance）。
若将 token 传给新建的 curl_cffi session，CF 服务端检测到 cookie↔token 不匹配
→ 返回 digest=3453729035 (turnstile_invalid)。

**解决方案（v3.1+）：** bypass 完成后，直接在浏览器内执行 JS fetch()，
浏览器自动携带全部 cookie，token 验证必然通过。

### 为什么 multipart/form-data？（v3.2 修复）

unitool 注册按鈕实际提交格式为 multipart/form-data，字段名带 1_ 前缀：

```
1_email                  → 邮笱
1_password               → 密码
1_cf-turnstile-response  → Turnstile token
1_captcha_token          → Turnstile token（同值）
1_captcha_action         → "signup"
1_ref_code               → 邀请码（可选）
0                        → React Server Component state JSON
```

v3.0/3.1 发送 application/json，服务端解析不到 token 字段 → turnstile_invalid。

### ssid 捕获问题

__Secure-unitool-ssid 是 httpOnly SameSite=Strict cookie，
JS document.cookie 无法读取。
http_register 注册成功后 ssid 的获取由 unitool_login.py 负责（CDP Network 事件捕获 Set-Cookie）。

---

## 七、常见问题排查

### unitool-proxy 频繁重启（NameError）
检查 artifacts/api-server/unitool_proxy.py 常量块是否缺少定义：
```bash
grep "^MAX_" /data/Toolkit/artifacts/api-server/unitool_proxy.py
```
已知坑：v5.26 引入 MAX_UPDATING 但常量块漏写 → 修复：MAX_UPDATING = 60。

### http_register 返回 turnstile_invalid（digest=3453729035）
1. Xvfb 是否正常：pm2 show xvfb，DISPLAY=:99
2. pydoll 版本：pip show pydoll-python
3. RESI 代理：pm2 logs unitool-proxy | grep RESI

### http_register 返回 payload_parse_error（digest=1068100299）
SIGNUP_NA 哈希已过期，需更新 _SIGNUP_NA_DEFAULT：
```bash
python3 /data/Toolkit/scripts/unitool_http_register.py --probe
```

### ref_code 全部用满，chain 停止注册
```sql
SELECT email, notes FROM accounts
WHERE tags LIKE '%25unitool_ref_activated%25'
ORDER BY updated_at DESC LIMIT 10;
```

### accounts 卡在 unitool_processing 超过 30min
```bash
psql -U postgres toolkit -c "
  UPDATE accounts SET
    tags = regexp_replace(tags, ',?unitool_processing', '', 'g'),
    updated_at = NOW()
  WHERE tags LIKE '%25unitool_processing%25'
    AND updated_at < NOW() - INTERVAL '30 minutes';"
```

---

## 八、监控命令速查

```bash
# 查看主链路运行状态
pm2 logs unitool_chain_v3 --lines 50 --nostream

# 查看最近注册结果（DB）
psql -U postgres toolkit -c "
  SELECT id, email, LEFT(tags,60), updated_at::text
  FROM accounts WHERE tags LIKE '%25unitool%25'
  ORDER BY updated_at DESC LIMIT 20;"

# 查看 ref_code 可用状态
psql -U postgres toolkit -c "
  SELECT email, LEFT(notes,200) FROM accounts
  WHERE tags LIKE '%25unitool_ref_activated%25'
  ORDER BY updated_at DESC LIMIT 10;"

# 手动探测 SIGNUP_NA
python3 /data/Toolkit/scripts/unitool_http_register.py --probe

# 查看 unitool-proxy 状态
pm2 logs unitool-proxy --lines 30 --nostream | grep -E "RESI|BAL|stream|error"

# 查看所有 unitool 相关进程
pm2 list | grep -E "unitool|chain|rescue|refresh"
```

---

## 九、版本历史

| 版本 | 日期       | 关键变更                                              |
|------|------------|-------------------------------------------------------|
| v3.2 | 2026-05-08 | chain_v3 集成 http_register 快速路径 + NA 哈希日监控  |
| v3.2 | 2026-05-08 | http_register: multipart/form-data 修复 turnstile_invalid |
| v3.1 | 2026-05-08 | http_register: pydoll JS fetch() 修复 cookie↔token 绑定 |
| v3.0 | 2026-05-07 | 首版混合注册（pydoll bypass + curl_cffi POST）         |
| v5.26| 2026-05-08 | unitool_proxy: o系列 POLL_PRIMARY + MAX_UPDATING 修复  |
| v5.25| 2026-05-08 | unitool_proxy: grok reasoning-block strip + gemini maintenance |
