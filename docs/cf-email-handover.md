# CF 临时邮箱系统交接文档

> 更新时间：2026-05-13  作者：自动生成

---

## 一、系统概览

本系统基于 [cloudflare_temp_email](https://github.com/dreamhunter2333/cloudflare_temp_email) 开源项目，
在两个 Cloudflare 账户上各部署了一套独立的临时邮箱服务。

| 实例 | 域名 | 账户标签 | 前端 | API |
|------|------|----------|------|-----|
| jonjim | jonjim.eu.cc | CF-NEW-3 | https://mail.jonjim.eu.cc | https://mail-api.jonjim.eu.cc |
| hackerjim | hackerjim.eu.cc | CF-NEW-4 | https://mail.hackerjim.eu.cc | https://mail-api.hackerjim.eu.cc |

---

## 二、凭证速查（详细见 /root/credentials.json）

### jonjim（CF-NEW-3）
- Account ID: `f7a0cd49eddc664419f9a783be8ce73d`
- API Token: `REDACTED_CF_API_TOKEN`
- Zone ID (jonjim.eu.cc): `33097881ad982860e3263c44f32dced9`
- Zone Token: `REDACTED_CF_API_TOKEN`
- D1 DB: `cf-email-db`  ID: `f6cab1c2-a473-40a1-b289-06d5360cc246`
- JWT Secret: `jnm-a278e6da13af9d1d96c061897ddb1ee1`
- 网站密码: `8GKNFyLCo0pL7drOqKZQ6jGB`
- 管理员密码: `360cb32181e4ef281afb3b63`
- wrangler 配置: `/data/cf-email/worker/wrangler.toml`

### hackerjim（CF-NEW-4）
- Account ID: `d2a1b22bd8cf8bbdff62953315347a63`
- API Token(主): `REDACTED_CF_API_TOKEN`
- Zone Token: `REDACTED_CF_API_TOKEN`
- Zone ID (hackerjim.eu.cc): `b93e0fc054da64bbf6ba43c4883d894f`
- D1 DB: `hj-email-db`  ID: `43641199-b692-4608-92be-011372c1c108`
- JWT Secret: `1ceac80ccc5de802e3c25e11b19b0becc5bcfca9`
- 网站密码: `ak4yJVQ8szp8H5jS3Mx6Y1sm`
- 管理员密码: `ufmTbatyzZ0jkKrDvYhIc281`
- wrangler 配置: `/data/cf-email/worker/wrangler.hackerjim.toml`

---

## 三、架构说明

```
用户 → mail.jonjim.eu.cc (CF Pages 前端)
         ↓
     mail-api.jonjim.eu.cc (CF Worker: cloudflare-temp-email)
         ↓
     CF D1 Database (cf-email-db)  ←→  CF Email Routing (catch-all → worker)
```

**关键组件：**
1. **CF Worker** (`cloudflare-temp-email`): 后端 API，处理邮件收发、用户鉴权
2. **CF D1**: SQLite 数据库，存储邮箱/邮件数据
3. **CF Email Routing**: catch-all 规则，把所有入站邮件转发给 worker
4. **CF Pages**: 托管前端静态文件

**重要：CF Worker 运行在 Cloudflare 边缘，VPS 服务器上没有对应进程，PM2 不管理它。**

---

## 四、部署操作

### Worker 代码目录
```
/data/cf-email/worker/
├── src/              # Worker TypeScript 源码
├── wrangler.toml     # jonjim 配置
├── wrangler.hackerjim.toml  # hackerjim 配置
└── package.json
```

### 重新部署 Worker
```bash
# 部署单个或全部
/root/Toolkit/scripts/cf-email-deploy.sh jonjim
/root/Toolkit/scripts/cf-email-deploy.sh hackerjim
/root/Toolkit/scripts/cf-email-deploy.sh all
```

### 手动部署（原始命令）
```bash
cd /data/cf-email/worker

# jonjim
CLOUDFLARE_API_TOKEN=REDACTED_CF_API_TOKEN \
CLOUDFLARE_ACCOUNT_ID=f7a0cd49eddc664419f9a783be8ce73d \
  wrangler deploy --config wrangler.toml

# hackerjim
CLOUDFLARE_API_TOKEN=REDACTED_CF_API_TOKEN \
CLOUDFLARE_ACCOUNT_ID=d2a1b22bd8cf8bbdff62953315347a63 \
  wrangler deploy --config wrangler.hackerjim.toml
```

---

## 五、CF Email Routing 规则（勿动）

两个 zone 都已配置 catch-all 规则：
- matchers: `type: all`（捕获所有入站邮件）
- actions: `type: worker, value: cloudflare-temp-email`

**注意**：CF Email Routing API 字段用 `type`/`value`，不是 `from_mail`/`source`。

验证命令：
```bash
# jonjim
curl -s "https://api.cloudflare.com/client/v4/zones/33097881ad982860e3263c44f32dced9/email/routing/rules" \
  -H "Authorization: Bearer REDACTED_CF_API_TOKEN"

# hackerjim
curl -s "https://api.cloudflare.com/client/v4/zones/b93e0fc054da64bbf6ba43c4883d894f/email/routing/rules" \
  -H "Authorization: Bearer REDACTED_CF_API_TOKEN"
```

---

## 六、Worker 路由（勿动）

| 实例 | Route ID | Pattern |
|------|----------|---------|
| jonjim | 61658feb7939465dbf7a3b4b61dd147a | mail-api.jonjim.eu.cc/* |
| hackerjim | 2a14e0e525b14b8ebcbb7341b8d4fc90 | mail-api.hackerjim.eu.cc/* |

---

## 七、API 鉴权说明

所有 API 均需要 Bearer Token（通过前端登录获取）。

```bash
# 获取 token（需先设网站密码）
curl -X POST https://mail-api.jonjim.eu.cc/api/new_address \
  -H "x-custom-auth: 8GKNFyLCo0pL7drOqKZQ6jGB" \
  -H "Content-Type: application/json" \
  -d '{"name":"test","domain":"jonjim.eu.cc"}'

# 管理员接口
curl https://mail-api.jonjim.eu.cc/admin/... \
  -H "x-admin-auth: 360cb32181e4ef281afb3b63"
```

---

## 八、D1 数据库直接查询

```bash
# jonjim - 查看邮件数量
CLOUDFLARE_API_TOKEN=REDACTED_CF_API_TOKEN \
CLOUDFLARE_ACCOUNT_ID=f7a0cd49eddc664419f9a783be8ce73d \
  wrangler d1 execute cf-email-db --command "SELECT COUNT(*) FROM mails"

# hackerjim
CLOUDFLARE_API_TOKEN=REDACTED_CF_API_TOKEN \
CLOUDFLARE_ACCOUNT_ID=d2a1b22bd8cf8bbdff62953315347a63 \
  wrangler d1 execute hj-email-db --command "SELECT COUNT(*) FROM mails"
```

---

## 九、日常巡检

```bash
# 快速健康检查
curl -o /dev/null -w "jonjim: %{http_code}\n" https://mail-api.jonjim.eu.cc/api/settings
curl -o /dev/null -w "hackerjim: %{http_code}\n" https://mail-api.hackerjim.eu.cc/api/settings
# 正常返回 401（需鉴权），说明 Worker 在运行
```

---

## 十、常见问题

| 问题 | 原因 | 解法 |
|------|------|------|
| API 返回 500 | Worker 代码错误或 D1 异常 | 检查 CF 控制台 Worker 日志 |
| 收不到邮件 | Email Routing 未启用或规则错误 | 验证第五节路由规则 |
| 部署失败 Authentication error | Token 缺少 Worker Scripts 写权限 | 在 CF 控制台重新建 token |
| 部署失败 route binding error | Token 缺少 Zone 权限 | 用 zone_token 或全权限 token |
| wrangler 用错账户 | 有缓存账户 | 加 `CLOUDFLARE_ACCOUNT_ID` env |

