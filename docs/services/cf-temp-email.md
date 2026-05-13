# Cloudflare Temp Email — JonJim

> 基于 [cloudflare_temp_email](https://github.com/dreamhunter2333/cloudflare_temp_email) 部署的临时邮箱服务，使用 CF-NEW-3 账号 (jonjim)，支持固定邮箱地址。

## 访问地址

| 服务 | URL |
|------|-----|
| 前端界面 | https://jonjim-temp-email.pages.dev |
| API (Workers.dev) | https://cloudflare-temp-email.etqryiacrl.workers.dev |
| API (自定义域名) | https://mail-api.jonjim.eu.cc |

## 邮箱域名

- `@jonjim.eu.cc` — 主域名，Email Routing 已启用，catch-all 转发至 Worker

## 账号信息 (CF-NEW-3)

| 字段 | 值 |
|------|-----|
| 账号名 | jonjim |
| 登录邮箱 | Etqryiacrl@rommiui.com |
| Account ID | f7a0cd49eddc664419f9a783be8ce73d |
| 域名 | jonjim.eu.cc (zone: 33097881ad982860e3263c44f32dced9) |
| D1 数据库 | cf-email-db (f6cab1c2-a473-40a1-b289-06d5360cc246) |
| Worker | cloudflare-temp-email |
| Pages 项目 | jonjim-temp-email |

> 所有密钥/密码详见 `/root/credentials.json` 和 `/root/.cf_credentials`（VPS 45.205.27.69）。

## 架构

```
用户浏览器
  -> https://jonjim-temp-email.pages.dev  (CF Pages 前端)
       -> API: https://mail-api.jonjim.eu.cc  (CF Worker 自定义路由)
              -> D1 数据库: cf-email-db

邮件接收:
  任意地址@jonjim.eu.cc
    -> Email Routing catch-all
    -> CF Worker (cloudflare-temp-email)
    -> D1 数据库存储
```

## Worker 配置 (wrangler.toml)

| 变量 | 值 |
|------|-----|
| PREFIX | tmp |
| DEFAULT_DOMAINS | jonjim.eu.cc |
| TITLE | JonJim Mail |
| DEFAULT_LANG | zh |
| ENABLE_USER_CREATE_EMAIL | true |
| ENABLE_ADDRESS_PASSWORD | true |
| FRONTEND_URL | https://jonjim-temp-email.pages.dev |
| cron | 0 0 * * * (每日清理) |

> **机密变量**（不记录于此）：`JWT_SECRET`, `ADMIN_PASSWORDS` — 见 `/root/credentials.json`

## API Tokens

| Token 名称 | 用途 |
|-----------|------|
| jonjim-email-zone | Worker 部署 + Email Routing + DNS (zone-level) |
| jonjim-pages-deploy | CF Pages 部署 |

> Token 值见 `/root/credentials.json`

## 部署步骤

### 1. D1 数据库
```bash
wrangler d1 create cf-email-db
wrangler d1 execute cf-email-db --file=../worker-script/schema.sql
# 执行所有 DB patch files in worker-script/
```

### 2. Email Routing (CF API)
```bash
# 启用 Email Routing
curl -X PUT https://api.cloudflare.com/client/v4/zones/{zone_id}/email/routing/enable \
  -H "Authorization: Bearer $TOKEN"

# 自动设置 MX/SPF DNS 记录
curl -X POST https://api.cloudflare.com/client/v4/zones/{zone_id}/email/routing/dns \
  -H "Authorization: Bearer $TOKEN"

# catch-all -> Worker
curl -X PUT https://api.cloudflare.com/client/v4/zones/{zone_id}/email/routing/rules/catch_all \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"enabled":true,"name":"catch-all","actions":[{"type":"worker","value":["cloudflare-temp-email"]}],"matchers":[{"type":"all"}]}'
```

### 3. Worker 部署
```bash
cd /data/cf-email/worker
export CLOUDFLARE_API_TOKEN='<jonjim-email-zone token>'
export CLOUDFLARE_ACCOUNT_ID='f7a0cd49eddc664419f9a783be8ce73d'
wrangler deploy
```

### 4. 前端构建 & 部署
```bash
cd /data/cf-email/frontend
# .env.production: VITE_API_BASE=https://mail-api.jonjim.eu.cc
npm run build -- --mode prod
export CLOUDFLARE_API_TOKEN='<jonjim-pages-deploy token>'
wrangler pages deploy dist --project-name jonjim-temp-email --branch main --commit-dirty=true
```

## 文件位置 (VPS: 45.205.27.69)

```
/data/cf-email/              cloudflare_temp_email 项目根目录
  worker/wrangler.toml       Worker 配置
  frontend/dist/             前端构建产物
  frontend/.env.production   前端环境变量
/root/credentials.json       所有账号凭证
/root/.cf_credentials        CF 账号快速参考
```

## 日常维护

```bash
# 更新 Worker
cd /data/cf-email/worker && wrangler deploy

# 更新前端
cd /data/cf-email/frontend
npm run build -- --mode prod
wrangler pages deploy dist --project-name jonjim-temp-email --commit-dirty=true

# 查看邮件日志
wrangler tail cloudflare-temp-email

# D1 查询
wrangler d1 execute cf-email-db --command "SELECT count(*) FROM mails"
```

## 邮箱地址格式

- 自动生成: `tmp.xxxxxxxx@jonjim.eu.cc`
- 自定义前缀: `<prefix>@jonjim.eu.cc`
- 可设置地址密码保护

## 管理员

- 路由: `https://mail-api.jonjim.eu.cc/admin`
- 密码: 见 `/root/credentials.json` ADMIN_PASSWORDS 字段
