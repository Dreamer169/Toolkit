# cfmail × unitool 全链路 — 新人接手文档

> 最后更新：2026-05-17  
> 环境：VPS 45.205.27.248（root）  
> 仓库：github.com/Dreamer169/Toolkit（fork of Dreamer7070/Toolkit）

---
> 📄 **邀请链接（Referral）系统专项文档** → 见 [chain-referral-system.md](./chain-referral-system.md)  
> 本文档专注 cfmail 链路；referral 角色定义、两种账号区别、Bug 清单请看上方专项文档。

---


## 一、这是什么

用 Cloudflare Workers 搭建的临时邮箱（cfmail），代替 Outlook 给 unitool.ai 注册账号，全流程自动化：

```
生成真实姓名邮箱 → 创建 cfmail 地址 → 注册 unitool → 等待验证邮件（CF D1）→ 点击 verify → 保存 ssid
```

**为什么要用 cfmail 代替 Outlook？**
- Outlook 账号需要提前注册、验证、维护，存量有限
- cfmail 按需创建，无限量，无需维护
- 邮箱名用真实人名风格，不暴露机器特征

---

## 二、关键基础设施

### cfmail 实例

| 实例 | API Host | 收件域名 | 备注 |
|------|----------|----------|------|
| jonjim（主力） | `mail-api.jonjim.eu.cc` | `@jonjim.eu.cc` | 首选 |
| hackerjim（备用） | `mail-api.hackerjim.eu.cc` | `@hackerjim.eu.cc` | 轮换备用 |

**认证信息（写在代码常量里，不要硬编码在 shell 里）：**

```
jonjim:
  site_auth:  8GKNFyLCo0pL7drOqKZQ6jGB
  admin_auth: 360cb32181e4ef281afb3b63

hackerjim:
  site_auth:  ak4yJVQ8szp8H5jS3Mx6Y1sm
  admin_auth: ufmTbatyzZ0jkKrDvYhIc281
```

### Cloudflare D1 数据库（存储原始邮件）

```
CF 账号 ID:  f7a0cd49eddc664419f9a783be8ce73d
D1 数据库 ID: f6cab1c2-a473-40a1-b289-06d5360cc246
CF API Token: cfat_1nsWRzCWTK6ezNt6zDzVuW5OckDeFFaZPnY9MzOm962c7b75
```

**D1 邮件表结构（`raw_mails`）：**

| 列名 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| message_id | TEXT | 邮件 Message-ID |
| source | TEXT | 发件人 |
| **address** | TEXT | **收件地址（用这个过滤！不是 recipient）** |
| raw | TEXT | 原始邮件（quoted-printable 编码） |
| raw_blob | BLOB | 二进制原始邮件 |
| metadata | TEXT | 元数据 JSON |
| created_at | DATETIME | 入库时间 |

> ⚠️ 关键坑：列名是 `address` 不是 `recipient`，用错会返回 HTTP 400。

---

## 三、核心文件

```
/data/Toolkit/scripts/
├── cfmail_register.py      ← 独立完整链路脚本（新增）
└── unitool_chain_v3.py     ← 主链路（已集成 cfmail fallback）
```

### cfmail_register.py（独立脚本）

**作用**：完整走一次「创建地址→注册unitool→D1收件→点击验证→保存ssid」  
**用法**：

```bash
cd /data/Toolkit/scripts

# 基本用法（需要一个有效 ref_code）
python3 cfmail_register.py --ref-code NU5XB

# 指定域名
python3 cfmail_register.py --ref-code NU5XB --domain hackerjim

# 延长等待时间（网络慢时用）
python3 cfmail_register.py --ref-code NU5XB --max-wait 200
```

**输出**：
```
[OK] ericthomas1326@jonjim.eu.cc|{ssid 264字符}
```
或
```
[FAIL] {email}|{reason}
```

**生成的邮箱名样例**（从 100+ 真实英文姓名词库随机组合）：
```
ericthomas1326@jonjim.eu.cc
sandrajohnson47@jonjim.eu.cc
michaelwilliams@jonjim.eu.cc      ← 10% 概率不带数字
robertbrown9821@jonjim.eu.cc
```

### unitool_chain_v3.py（集成 cfmail fallback）

**PM2 进程**：`unitool_chain_v3`（id=42）  
**启动脚本**：`/data/Toolkit/scripts/chain_w0.sh`  
**配置**：`/data/Toolkit/ecosystem.config.cjs`

**cfmail 在 chain_v3 中的触发时机**：  
当 outlook 账号全部用完（`db_get_fresh_account()` 返回 None）时，自动切换 cfmail 全链路：

```python
# Step 3 逻辑（简化）：
row = db_get_fresh_account()    # 取 outlook 账号
if not row:
    result = run_cfmail_chain(ref_code)   # ← 自动用 cfmail 补
    if result["ok"]:
        persist_ssid(...)
        return  # 成功直接进下一轮
    # cfmail 也失败则等 120s 等 outlook
```

**新增的 cfmail 函数（chain_v3 内部）**：
```
_cfmail_gen_name()       生成 firstname.lastname<数字> 名字
_cfmail_create_addr()    curl 调 admin API 创建地址
_cfmail_poll_d1()        查 CF D1 找验证邮件（用 address 列）
run_cfmail_chain()       完整 cfmail 子链路
```

---

## 四、API 说明

### 创建 cfmail 地址

**只能用 curl subprocess（Python urllib/requests 被 CF 403 拦截）**

```bash
curl -X POST https://mail-api.jonjim.eu.cc/jimhacker/new_address \
  -H "x-custom-auth: 8GKNFyLCo0pL7drOqKZQ6jGB" \
  -H "x-admin-auth: 360cb32181e4ef281afb3b63" \
  -H "Content-Type: application/json" \
  -d '{"name": "john.doe123"}'
```

**响应（注意字段名）**：
```json
{
  "jwt": "eyJ...",
  "address": "johndoe123@jonjim.eu.cc",
  "password": "abc12345",
  "address_id": 42
}
```

> ⚠️ 坑1：响应字段是 `address`，不是 `email`  
> ⚠️ 坑2：名字中的点号（`.`）会被 API 自动去掉，`john.doe123` → `johndoe123`  
> ⚠️ 坑3：没有 `success` 字段，用 `data.get("address")` 判断是否成功

### 查 D1 收件箱

```bash
curl -X POST "https://api.cloudflare.com/client/v4/accounts/f7a0cd49eddc664419f9a783be8ce73d/d1/database/f6cab1c2-a473-40a1-b289-06d5360cc246/query" \
  -H "Authorization: Bearer cfat_1nsWRzCWTK6ezNt6zDzVuW5OckDeFFaZPnY9MzOm962c7b75" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT id, address, created_at FROM raw_mails ORDER BY id DESC LIMIT 10"}'
```

**提取 verify URL 的正确流程**：
```python
import quopri, re
raw = row["raw"]
decoded = quopri.decodestring(raw.encode()).decode("utf-8", errors="replace")
urls = re.findall(r"https://unitool\.ai/api/auth/email\?token=[A-Za-z0-9._\-]+", decoded)
```

> ⚠️ 邮件 body 是 quoted-printable 编码，必须先 `quopri.decodestring()` 解码再提取 URL

### 点击 verify 拿 ssid

```bash
curl -sS -L --max-redirs 8 \
  --socks5-hostname 127.0.0.1:10851 \   # RESI 代理端口
  -c /tmp/cookie.txt -D /tmp/headers.txt \
  "https://unitool.ai/api/auth/email?token=eyJ..."
```

从 headers.txt 提取：
```
set-cookie: __Secure-unitool-ssid=ef0597fa...; Path=/; ...
```

---

## 五、数据库（PostgreSQL）

**连接**：`postgresql://postgres:postgres@localhost/toolkit`

**cfmail 账号如何存储**：

```sql
-- 查 cfmail 账号
SELECT id, email, status, tags, LEFT(notes,100)
FROM accounts
WHERE platform = 'cfmail'
ORDER BY id DESC;
```

**accounts 表关键字段**：

| 字段 | 说明 |
|------|------|
| platform | `'cfmail'`（区别于 `'outlook'`） |
| email | cfmail 地址（如 `ericthomas1326@jonjim.eu.cc`） |
| password | `Unitool@2024!`（unitool 登录密码） |
| refresh_token | cfmail JWT（创建地址时返回，轮询收件用） |
| status | `'active'`（正常） |
| tags | `unitool_registered` 成功 / `unitool_fail,xxx` 失败 |
| notes | ssid 长度、入库时间 |

> ⚠️ 唯一约束是 `UNIQUE (platform, email)`，不是单独的 `UNIQUE (email)`  
> ON CONFLICT 必须写 `ON CONFLICT (platform, email)`

---

## 六、PM2 管理

```bash
# 查看进程列表
pm2 list

# 查看 unitool_chain_v3 状态
pm2 show unitool_chain_v3

# 热重载（代码改了之后用这个，不会中断正在跑的任务）
pm2 reload unitool_chain_v3

# 查看实时日志
pm2 logs unitool_chain_v3 --lines 50

# 查看日志文件
tail -f /tmp/unitool_chain_v3_out.log
```

**PM2 ecosystem 配置位置**：`/data/Toolkit/ecosystem.config.cjs`

```javascript
// unitool_chain_v3 核心配置
{
  "name": "unitool_chain_v3",
  "script": "/data/Toolkit/scripts/chain_w0.sh",
  "interpreter": "bash",
  "env": {
    "DISPLAY": ":99",
    "PYTHONUNBUFFERED": "1",
    "N_WORKERS": "3"      // chain_w0.sh 里设置
  },
  "out_file": "/tmp/unitool_chain_v3_out.log",
  "error_file": "/tmp/unitool_chain_v3_err.log",
  "restart_delay": 30000,
  "autorestart": true
}
```

**chain_w0.sh 内容**：
```bash
#!/bin/bash
export WORKER_ID=0
export CHROME_LIMIT=3
export STARTUP_DELAY=0
export N_WORKERS=3
exec python3 /data/Toolkit/scripts/unitool_chain_v3.py
```

---

## 七、ssid 持久化路径

成功拿到 ssid 后，自动写入三处：

| 路径 | 格式 | 说明 |
|------|------|------|
| `/data/unitool_ssids/{label}.txt` | 纯 ssid 文本 | proxy 优先读取目录 |
| `/tmp/unitool_ssid{N}.txt` | 纯 ssid 文本 | 兼容旧格式 |
| `POST localhost:8089/add-ssid` | JSON `{"ssid":"...","label":"email"}` | proxy 内存热推（立即生效） |

**查已有 ssid**：
```bash
ls /data/unitool_ssids/ | grep jonjim    # 查 cfmail ssid
wc -c /data/unitool_ssids/ericthomas1326_jonjim_eu_cc.txt   # 264字节=正常
```

---

## 八、排查清单

### 地址创建失败
```
[FAIL] cfmail_create|address_create_failed
```
- 检查 site_auth / admin_auth 是否正确
- curl 到 admin API 返回了什么？手动测试：
  ```bash
  curl -X POST https://mail-api.jonjim.eu.cc/jimhacker/new_address \
    -H "x-custom-auth: 8GKNFyLCo0pL7drOqKZQ6jGB" \
    -H "x-admin-auth: 360cb32181e4ef281afb3b63" \
    -H "Content-Type: application/json" \
    -d '{"name":"testdebug"}'
  ```
- 响应要包含 `"address"` 字段

### 注册失败（Turnstile/代理）
```
ERR_CONNECTION_CLOSED  或  bypass_failed
```
- 正常现象：第一个 RESI 端口可能拒绝，自动换端口重试
- 超过 3 次失败才真正失败
- 检查代理是否存活：`pm2 show resi_pool` 或 `netstat -tlnp | grep 1085`

### 验证邮件没收到（D1 poll 超时）
```
[FAIL] email|verify_email_not_found
```

1. 手动查 D1 确认邮件是否到了：
   ```bash
   curl -X POST "https://api.cloudflare.com/client/v4/accounts/f7a0cd49eddc664419f9a783be8ce73d/d1/database/f6cab1c2-a473-40a1-b289-06d5360cc246/query" \
     -H "Authorization: Bearer cfat_1nsWRzCWTK6ezNt6zDzVuW5OckDeFFaZPnY9MzOm962c7b75" \
     -H "Content-Type: application/json" \
     -d '{"sql": "SELECT id, address, created_at FROM raw_mails ORDER BY id DESC LIMIT 10"}'
   ```
2. 如果邮件到了但 SQL 报 400，检查列名：**`address` 不是 `recipient`**
3. 如果邮件没到，等 3-5 分钟再查（CF email routing 偶有延迟）

### ssid 点击失败（no_ssid_after_click）
```
[FAIL] email|no_ssid_after_click
```
- verify URL 是否已过期（unitool token 默认 1 小时有效）
- 代理端口是否能访问 unitool.ai：
  ```bash
  curl --socks5-hostname 127.0.0.1:10851 -I https://unitool.ai --max-time 10
  ```

---

## 九、已知规律和注意事项

1. **API 限速**：cfmail admin API 没有明显限速，但连续创建过快（<1s）偶尔返回空响应，代码已加 2s 重试间隔

2. **dot 被去掉**：创建 `john.doe123` 实际地址是 `johndoe123@jonjim.eu.cc`，没有点号。这是 cfmail API 的行为，无法改变

3. **verify token 有效期**：unitool 的 verify token 约 1 小时，超时后重新注册即可（旧账号变 `already_registered`）

4. **D1 写入延迟**：unitool 发验证邮件到 CF D1 入库约需 5-15 秒，代码第一次 poll 等 8 秒是合理的

5. **chain_v3 优先级**：`outlook账号优先 → outlook耗尽时自动切 cfmail → cfmail 失败等 120s 等 outlook`

6. **多 worker 并发**：`N_WORKERS=3`，三个线程共用同一套 cfmail 函数，不存在竞态（cfmail 地址按需创建）

---

## 十、实测结果记录

| 时间 | 邮箱 | 结果 | ssid 长度 |
|------|------|------|-----------|
| 2026-05-17 23:49 | ericthomas1326@jonjim.eu.cc | ✅ 成功 | 264 |
| 2026-05-17 23:45 | stevenroberts4717@jonjim.eu.cc | ❌ D1列名错误(已修复) | — |
| 2026-05-17 23:40 | sandrahall6867@jonjim.eu.cc | ❌ DB约束错误(已修复) | — |
| 2026-05-17 23:06 | ut1779059148@jonjim.eu.cc | ✅ 手动验证（早期测试） | — |

**所有 bug 均已修复，链路稳定可用。**

---

## 十一、快速命令速查

```bash
# 手动跑一次 cfmail 注册
cd /data/Toolkit/scripts
python3 cfmail_register.py --ref-code NU5XB

# 查 cfmail 账号入库情况
psql postgresql://postgres:postgres@localhost/toolkit \
  -c "SELECT id,email,tags FROM accounts WHERE platform='cfmail' ORDER BY id DESC LIMIT 10;"

# 查最新 D1 邮件
curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/f7a0cd49eddc664419f9a783be8ce73d/d1/database/f6cab1c2-a473-40a1-b289-06d5360cc246/query" \
  -H "Authorization: Bearer cfat_1nsWRzCWTK6ezNt6zDzVuW5OckDeFFaZPnY9MzOm962c7b75" \
  -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT id,address,created_at FROM raw_mails ORDER BY id DESC LIMIT 10"}' \
  | python3 -m json.tool

# 查 chain_v3 日志（最近50行）
pm2 logs unitool_chain_v3 --lines 50 --nostream

# 热重载 chain_v3（代码更新后）
pm2 reload unitool_chain_v3

# 查 ssid 是否存在
ls /data/unitool_ssids/ | grep jonjim

# 查整体账号水位
psql postgresql://postgres:postgres@localhost/toolkit \
  -c "SELECT platform, COUNT(*), SUM(CASE WHEN tags LIKE '%unitool_registered%' THEN 1 ELSE 0 END) as ok FROM accounts GROUP BY platform;"
```
