# unitool 邀请链接（Referral）系统 — 完整接手文档

> 最后更新：2026-05-23  
> 适用代码：`/data/Toolkit/scripts/unitool_chain_v3.py`  
> 相关脚本：`unitool_http_register.py`、`unitool_register.py`

---

## 一、两种账号的角色——必须先搞清楚

unitool 的邀请系统里存在**两个完全不同的角色**，混淆两者会导致调试方向错误。

### 1.1 提供方账号（Referral Provider / ref_code master）

> "我有一个邀请链接，别人用我的链接注册，我的 **Registrations by link** 计数 +1，我赚 10 tokens"

| 属性 | 说明 |
|------|------|
| 定义 | 拥有 ref_code 的 unitool 账号 |
| DB 标记 | `tags LIKE '%unitool_ref_activated%'` |
| DB 存码位置 | `notes` 字段里有 `ref_code=XXXXX`（5位字母数字） |
| 目标 | Registrations by link 达到 10 → 升级为 `unitool_high_balance` |
| 本地计数 | `notes` 里的 `ref_registered=N`（**不可靠，只作参考**） |
| API 真实计数 | `GET /api/user/ref-code` → 字段 `conversions`（**以此为准**） |

**单个提供方的注册收益链路：**
```
提供方发出链接 → 新用户访问 /ref/CODE → 新用户注册 unitool
  → 新用户邮件验证通过
    → unitool 后端记录 conversion
      → 提供方 Registrations by link +1，earnings +10 tokens
```

**达到 10/10 后：**
- chain_v3 的 Step 7d 检测到 `API conversions >= 10`
- 自动给提供方账号打上 `unitool_high_balance` 标签
- 该账号进入高余额池，可被用于不同的消费场景

### 1.2 使用方账号（Referral User / via_ref registrant）

> "我通过别人的邀请链接注册了 unitool，我是被邀请方"

| 属性 | 说明 |
|------|------|
| 定义 | 用别人的 ref_code 注册的 unitool 账号 |
| DB 标记 | `notes LIKE '%via_ref=CODE%'`（CODE=提供方的 ref_code） |
| 邮箱类型 | 全部为 `@outlook.com`（chain_v3 用 outlook 账号注册） |
| 自身是否有 ref_code | 有（Step 7 会立即为使用方生成自己的 ref_code，使其也成为提供方） |

> ⚠️ **关键：每个账号注册完成后立即生成自己的 ref_code（Step 7），所以使用方同时也会成为新的提供方。这是"链"的核心——每轮产出一个新码供下一轮用。**

---

## 二、unitool UI 对应关系

| unitool UI 字段 | API 字段 | 含义 |
|----------------|----------|------|
| Your Referral Progress | `clicks` | 访问过 ref 链接的次数（目前始终=0，pydoll 的访问不被计入） |
| **Registrations by link** | **`conversions`** | **通过链接完成注册的次数（真实业务指标，以此为准）** |
| Your Earnings | `earnings` | 邀请收益（conversions × 10 tokens） |
| Partner cabinet | — | 独立的合作伙伴后台页，路径：`/en/partner` |

**API 接口：**
```bash
curl -s --socks5-hostname 127.0.0.1:10853 \
  -b "__Secure-unitool-ssid=<SSID>" \
  -H "Accept: application/json" \
  "https://unitool.ai/api/user/ref-code"
# 返回：{"code":"JPcVh","user_id":3012994,"clicks":0,"conversions":1,"earnings":10,"ip_address":"..."}
```

---

## 三、chain_v3 完整流程（Step by Step）

```
┌─────────────────────────────────────────────────────────────────┐
│  每一轮 main() 循环：消耗一个 outlook 账号，产出一条新链节点  │
└─────────────────────────────────────────────────────────────────┘

Step 0  初始化（多 worker 时只有第一个 worker 执行）
Step 1  资源检查（outlook 存量、代理端口健康）
Step 2  从 pool 选一个未满的 ref_code（提供方 ref_code，used < 10）
          → db_get_current_ref_code()
          → 返回 (ref_master_id, ref_master_email, ref_code, used_count)
Step 3  取一个 outlook 账号作为使用方（这轮要注册的新账号）
          → db_get_fresh_account() → outlook 账号
          → outlook 耗尽时 fallback: run_cfmail_chain()（cfmail 备用）
Step 4  注册 unitool
          → run_register_fast(email, ref_code)
          → 优先 pydoll + JS fetch（unitool_http_register.py）
          → 失败降级全浏览器 unitool_register.py（Playwright）
          → 关键：必须带 ref_code，否则注册不关联邀请
Step 5  获取 SSID（三级兜底）
          5a: 注册响应里直接拿（pydoll 场景通常拿不到，因 httpOnly）
          5b: 内联等验证邮件（90s），点击 verify link，拿 ssid
          5c: run_login() 密码登录再拿
Step 6  保存 SSID + API 验证
          → db_save_ssid_full()
          → _verify_ssid_api() 验证 SSID 有效
Step 7a 为新账号创建专属 ref_code（使其成为提供方）
          → create_ref_code_via_proxy(ssid, email)
          → POST https://unitool.ai/api/ref-codes
          → ⚠️ 当前 Bug：所有 RESI/Tor IP 均报 ip-already-existed（见第五节）
Step 7b 读取并保存 ref_code
          → run_reflink(email) → GET /api/user/ref-code 确认
          → db_save_ref_code(account_id, new_ref_code)
Step 7b  在被注册账号(使用方) notes 写入 via_ref=CODE（统计用）
Step 7b  在提供方账号 notes 追加 ref_registered=N（本地计数，仅供参考）
Step 7d 检查提供方是否达到升级条件
          → 本地计数 >= MAX_REF_SLOTS (10) 时触发
          → 调 _api_check_ref_code(ssid) 核验 API conversions
          → API conversions >= 10 → 打 unitool_high_balance 标签
          → API conversions < 10  → 记录日志，不升级，等下轮
```

---

## 四、数据库字段说明

```sql
-- 连接
-- postgresql://postgres:postgres@localhost/toolkit

-- 查提供方账号（ref_code masters）
SELECT id, email, status, tags,
  substring(notes FROM 'ref_code=([A-Za-z0-9]+)') as ref_code,
  substring(notes FROM 'ref_registered=([0-9]+)')  as local_conversions_count
FROM accounts
WHERE tags LIKE '%unitool_ref_activated%'
ORDER BY id;

-- 查使用方账号（通过某个 ref_code 注册的）
SELECT id, email, status,
  substring(notes FROM 'via_ref=([A-Za-z0-9]+)') as used_ref_code
FROM accounts
WHERE notes LIKE '%via_ref=%'
ORDER BY id;

-- 查已达标的高余额账号
SELECT id, email, tags,
  substring(notes FROM 'ref_code=([A-Za-z0-9]+)') as ref_code
FROM accounts
WHERE tags LIKE '%unitool_high_balance%';

-- 统计各 ref_code 被使用次数（本地计数，不等于 API conversions）
SELECT substring(notes FROM 'via_ref=([A-Za-z0-9]+)') as ref_code,
       COUNT(*) as local_via_ref_count
FROM accounts WHERE notes LIKE '%via_ref=%'
GROUP BY ref_code ORDER BY local_via_ref_count DESC LIMIT 20;
```

**关键字段含义：**

| 字段 | 位置 | 含义 | 可信度 |
|------|------|------|--------|
| `ref_code=XXXXX` | `notes` | 该账号自身的邀请码（提供方身份） | 可信 |
| `ref_registered=N` | `notes` | 本地统计：多少账号用了这个码 | ⚠️ 不可靠，仅参考 |
| `via_ref=XXXXX` | `notes` | 该账号注册时用了谁的邀请码 | 可信 |
| `unitool_ref_activated` | `tags` | 该账号已生成并激活自己的 ref_code | 可信 |
| `unitool_high_balance` | `tags` | 该账号 conversions>=10，已晋升高余额 | 可信 |
| `conversions` | API only | unitool 官方统计的有效注册数 | **唯一权威来源** |

> ⚠️ 本地 `ref_registered=N` **必然大于** API `conversions`。两者差值 = 未被 unitool 认定的无效注册数。**不要用本地计数判断是否应该晋升，必须用 API。**

---

## 五、当前已知 Bug 和问题

### Bug 1：ref_code 创建全部失败（ip-already-existed）

**现象：**
```
[CF] create ref_code CF-FAIL body={"error":"ip-already-existed","message":"IP address already existed"}
[tor_ref] port=9050 err=ip-already-existed
[tor_ref] port=9052 err=ip-already-existed
```

**原因：** unitool 的 `POST /api/ref-codes` 接口会记录调用方 IP。服务器上所有 RESI 出口 IP 和 Tor 出口 IP 均已被该账号（或其他账号）记录过，触发去重限制。

**影响：** 新注册账号无法生成自己的 ref_code → Step 7a 失败 → 链条无法扩展新的提供方。

**现有兜底：** Step 7b 的 `run_reflink` 会尝试 GET 读取已有码；`created_code` 直存 DB fallback。但根本问题（无法用新 IP 创建码）未解决。

**可能的解：**
- 换新的出口 IP 段（新 RESI 账号/新代理）
- 让新注册账号的浏览器会话（非代理直连）来创建 ref_code

---

### Bug 2：API conversions 不增长（核心 Bug，根本原因未确认）

**现象：**  
chain_v3 用 outlook 账号注册了大量 via_ref 账号（本地计数 10~200+），但对应提供方的 API `conversions` 始终停在 0 或 1，无法达到 10。

**已确认的机制：**
- ref-code cookie 通过 `Page.addScriptToEvaluateOnNewDocument` 注入，每页加载都生效 ✅
- `--disable-blink-features=AutomationControlled` 已设置 ✅
- `navigator.webdriver` 已遮蔽 ✅
- Cookie 在 fetch 前确认存在（日志：`ref-code cookie OK: JPcVh`）✅
- 注册响应为 `email_sent`（注册本身成功）✅
- via_ref 账号全部使用 `@outlook.com` 邮箱（非自定义域名）✅

**已排除的错误方向：**
- ❌ 与 `replit_used` 标签无关
- ❌ 与邮箱域名（outlook vs 自定义域名）无关——via_ref 全是 outlook

**待验证的假设（按可能性排序）：**
1. unitool 服务端用某种信号（IP 信誉、TLS 指纹、请求特征）识别自动化注册，不计 conversions
2. ref-code cookie 在 `/en/entry` 页面 JS 执行期间被消费/清除，导致 fetch POST 时 cookie 已不在
3. unitool 要求注册者的邮件验证必须在 N 分钟内完成才计 conversion（时序问题）

**下一步调试建议：**
```python
# 在 fetch_js 里加入 cookie 快照，确认 fetch 执行时 ref-code 是否存在：
# 在 fetch 前加：cookies_before = document.cookie
# return JSON.stringify({status: r.status, body: text.slice(0,800), cookies: document.cookie})
```

---

### Bug 3：conversions 缓存 30min 导致晋升延迟

`_api_check_ref_code` 有 30 分钟文件缓存（`REF_CODE_CACHE_TTL`）。  
即使 conversions 真的到达 10，最多延迟 30 分钟才会触发晋升。  
可手动清缓存：
```bash
rm -f /tmp/ref_code_cache.json  # 或查代码确认缓存文件路径
```

---

## 六、快速排查命令

```bash
# 查某个 ref_code 的 API 真实 conversions
SSID="<提供方账号的SSID>"
curl -s --socks5-hostname 127.0.0.1:10853 \
  -b "__Secure-unitool-ssid=$SSID" \
  -H "Accept: application/json" \
  "https://unitool.ai/api/user/ref-code"
# 重点看 conversions 字段

# 查所有提供方账号（ref_code masters）
psql 'postgresql://postgres:postgres@localhost/toolkit' -c "
SELECT id, email, tags,
  substring(notes FROM 'ref_code=([A-Za-z0-9]+)') as ref_code,
  substring(notes FROM 'ref_registered=([0-9]+)') as local_count,
  CASE WHEN tags LIKE '%unitool_high_balance%' THEN 'HB' ELSE '-' END as hb
FROM accounts
WHERE tags LIKE '%unitool_ref_activated%'
ORDER BY id LIMIT 30;"

# 查某个 ref_code 的使用方列表
psql 'postgresql://postgres:postgres@localhost/toolkit' -c "
SELECT id, email, created_at
FROM accounts
WHERE notes LIKE '%via_ref=JPcVh%'
ORDER BY id;"

# 统计整体状态
psql 'postgresql://postgres:postgres@localhost/toolkit' -c "
SELECT
  COUNT(*) FILTER (WHERE tags LIKE '%unitool_ref_activated%') AS masters,
  COUNT(*) FILTER (WHERE tags LIKE '%unitool_high_balance%') AS high_balance,
  COUNT(*) FILTER (WHERE notes LIKE '%via_ref=%') AS total_via_ref
FROM accounts;"

# 看 chain_v3 实时日志
pm2 logs unitool-chain --lines 50

# 看 ref_code 创建失败情况
pm2 logs unitool-chain --nostream --lines 500 | grep -E 'ip-already|CF-FAIL|ref_code.*fail'
```

---

## 七、当前状态快照（2026-05-23）

| 指标 | 数值 |
|------|------|
| 提供方账号总数（unitool_ref_activated） | ~20+（正在增长） |
| 高余额账号（unitool_high_balance） | 114 |
| 使用方账号总数（via_ref） | 3081 |
| JPcVh API conversions | 1/10 |
| zLKHs API conversions | 1/10 |
| aNwdE API conversions | 0/10 |
| 所有账号邮箱域 | 全部 @outlook.com |
| ref_code 创建状态 | ❌ ip-already-existed（RESI/Tor 全部失败） |
| 转化计数增长 | ❌ 注册成功但 conversions 不增长（Bug 2，原因待查） |

---

## 八、代码位置速查

| 功能 | 文件 | 函数/位置 |
|------|------|-----------|
| ref_code 池选择 | `unitool_chain_v3.py` | `db_get_current_ref_code()` L966 |
| ref_code 创建（代理） | `unitool_chain_v3.py` | `create_ref_code_via_proxy()` L787 |
| API conversions 查询 | `unitool_chain_v3.py` | `_api_check_ref_code()` L691 |
| 晋升到高余额 | `unitool_chain_v3.py` | Step 7d，L2539 附近 |
| ref-code cookie 注入 | `unitool_http_register.py` | `_pydoll_register()` L737-761 |
| 指纹反检测 JS | `unitool_http_register.py` | `_FINGERPRINT_JS` L52 |
| 注册表单 fetch | `unitool_http_register.py` | `fetch_js` L798-840 |
| 邀请链接读取 | `unitool_chain_v3.py` | `run_reflink()` L2008 |
