# CF Email Service

基于 [dreamhunter2333/cloudflare_cf_email](https://github.com/dreamhunter2333/cloudflare_cf_email) 的CF邮箱服务。

## 实例清单

| 账号 | 域名 | Frontend | API | 状态 |
|------|------|----------|-----|------|
| CF-NEW-3 (jonjim) | jonjim.eu.cc | https://mail.jonjim.eu.cc | https://mail-api.jonjim.eu.cc | active |
| CF-NEW-4 (hackerjim) | hackerjim.eu.cc | https://mail.hackerjim.eu.cc | https://mail-api.hackerjim.eu.cc | active |

## 认证机制

- **站点访问**: needAuth=true，site_password 保护
- **API 访问**: 两种方式
  - x-custom-auth: <site_password> — 明文直接访问
  - Authorization: Bearer <JWT> — 登录后获取 JWT
- **管理员**: ADMIN_PASSWORDS 中的密码

## 配置明细

### CF-NEW-3 (jonjim)

- Frontend: https://mail.jonjim.eu.cc
- API: https://mail-api.jonjim.eu.cc
- Worker: cloudflare-temp-email
- Pages: jonjim-temp-email
- D1: jonjim-email-db
- Wrangler config: /data/cf-email/worker/wrangler.toml
- 凭据: /root/credentials.json -> cf_services.cf_email

### CF-NEW-4 (hackerjim)

- Frontend: https://mail.hackerjim.eu.cc
- API: https://mail-api.hackerjim.eu.cc
- Worker: cloudflare-temp-email
- Pages: hackerjim-mail
- D1: hj-email-db (43641199-b692-4608-92be-011372c1c108)
- Wrangler config: /data/cf-email/worker/wrangler.hackerjim.toml
- 凭据: /root/credentials.json -> cf_services.hackerjim_cf_email

## 部署新实例步骤

1. 创建 D1: wrangler d1 create <name>-email-db，执行 db/schema.sql
2. 创建 Email Routing 专用 zone token (POST /accounts/{id}/tokens)
   - 需要权限: Email Routing Addresses/Rules Write, Zone Settings Write, DNS Write, Workers Scripts/Routes Write
   - Resource: com.cloudflare.api.account.zone.{zone_id}
3. 启用 Email Routing: POST /zones/{zone_id}/email/routing/enable (注意是POST不是PUT)
4. catch-all rule: PUT /zones/{zone_id}/email/routing/rules/catch_all -> worker
5. 创建 wrangler.{name}.toml (参考 wrangler.hackerjim.toml)
6. 部署 Worker: CLOUDFLARE_API_TOKEN=<zone_token> wrangler deploy --config wrangler.{name}.toml
7. 构建部署前端: cd frontend && VITE_API_BASE=https://mail-api.{domain} pnpm build && wrangler pages deploy dist --project-name {name}-mail
8. 设置 secrets: PASSWORDS, DISABLE_ANONYMOUS_USER_CREATE_EMAIL
9. DNS: CNAME mail-api -> Worker, CNAME mail -> Pages (均 proxied)
10. 保存凭据到 /root/credentials.json

## Permission Group IDs (Email Routing 相关)

- Email Routing Addresses Write: e4589eb09e63436686cd64252a3aebeb
- Email Routing Rules Write: 79b3ec0d10ce4148a8f8bdc0cc5f97f2
- Email Routing Rules Read: 1b600d9d8062443e986a973f097e728a
- Zone Settings Write: 3030687196b94b638145a3953da2b699
- Zone Settings Read: 517b21aee92c4d89936c976ba6e4be55
- Zone Write: e6d2666161e84845a636613608cee8d5
- Zone Read: c8fed203ed3043cba015a93ad1616f1f
- DNS Write: 4755a26eedb94da69e1066d98aa820be
- DNS Read: 82e64a83756745bbbb1c9c2701bf816b
- Workers Scripts Write: e086da7e2179491d91ee5f35b3ca210a
- Workers Routes Write: 28f4b596e7d643029c524985477ae49a
- D1 Write: 09b2857d1c31407795e75e3fed8617a1
- D1 Read: 192192df92ee43ac90f2aeeffce67e35

## 常见问题

- Email Routing 405: enable 端点要用 POST 不是 PUT
- Authentication error 10000: token 缺 Zone Settings Write + Workers Routes Write
- Workers Script Info not found: 先部署 Worker 再设 catch-all rule
- Pages 域名: CNAME 指向 {project}.pages.dev (proxied)

## 代码位置

- 仓库: /data/cf-email (dreamhunter2333/cloudflare_cf_email)
- Worker: /data/cf-email/worker/
- Frontend: /data/cf-email/frontend/
- DB Schema: /data/cf-email/db/schema.sql
