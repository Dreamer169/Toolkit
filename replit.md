# Workspace

## Overview

pnpm workspace monorepo using TypeScript. Each package manages its own dependencies.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **Build**: esbuild (CJS bundle)

## Key Commands

- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-server run dev` — run API server locally
- `pnpm --filter @workspace/ai-toolkit run dev` — run AI Toolkit web app

## Artifacts

### ai-toolkit (React + Vite, previewPath: /)
Chinese-language web portal for [AI-Account-Toolkit](https://github.com/adminlove520/AI-Account-Toolkit).

**Password gate**: password `yu123456`, sessionStorage key `toolkit_auth_v1`

**Navigation tabs (18 total)**:
| Tab | Page | Description |
|-----|------|-------------|
| home | Home.tsx | 工具导航总览（29 个工具卡片） |
| monitor | Monitor.tsx | 实时任务监控 |
| full-workflow | FullWorkflow.tsx | Outlook 批量注册完整流程 |
| data-manager | DataManager.tsx | 数据管理中心（账号/邮箱/身份/配置） |
| email | TempEmail.tsx | 临时邮箱（MailTM API） |
| bulk-email | BulkEmail.tsx | 批量 MailTM 邮箱 |
| free-email | FreeEmail.tsx | 免费身份邮箱（无需 Key） |
| outlook | OutlookManager.tsx | Outlook OAuth2 管理 |
| cursor-register | CursorRegister.tsx | Cursor.sh 账号自动注册（MailTM + patchright） |
| sub2api | Sub2ApiManager.tsx | Sub2Api / CPA Token 批量上传管理 |
| keycheck | KeyChecker.tsx | API Key 验证（7 平台：OpenAI/Claude/Gemini/Grok/DeepSeek/Cursor/OpenAI-Token） |
| tokencheck | TokenBatch.tsx | 批量 Key 检测（6 平台 + 导出功能，最多 50 个） |
| ip | IpChecker.tsx | IP 查询 |
| info | InfoGenerator.tsx | 信息生成 |
| machine-reset | MachineReset.tsx | Cursor 机器 ID 重置 |
| fingerprint | Fingerprint.tsx | 浏览器指纹 |
| team-register | iframe → team-all-in-one | ChatGPT Team 注册面板 (Flask, port 5000) |
| openai-pool | iframe → openai-pool | OpenAI 账号池编排器 (FastAPI, port 8000) |

### api-server (Express, previewPath: /api)
Shared backend API server on port 8080.

**Key endpoints**:
- `POST /api/tools/key-check` — 单个 Key 验证（openai/claude/gemini/grok/cursor/deepseek/openai-token）
- `POST /api/tools/token-batch-check` — 批量 Key 检测（openai/claude/gemini/grok/deepseek/cursor，max 50）
- `POST /api/tools/outlook/register` — 启动 Outlook 注册任务
- `GET /api/tools/outlook/register/:jobId` — 轮询注册任务状态
- `DELETE /api/tools/outlook/register/:jobId` — 停止注册任务
- `POST /api/tools/cursor/register` — 启动 Cursor 注册任务
- `GET /api/tools/cursor/register/:jobId` — 轮询 Cursor 注册任务状态
- `DELETE /api/tools/cursor/register/:jobId` — 停止 Cursor 注册任务
- `GET /api/tools/jobs` — 列出所有任务（实时监控用）
- `POST /api/tools/proxy-request` — 代理请求（避免 CORS，限 sub2api/cpa/xai 等白名单域名）
- `GET /api/tools/gateway/status` — 检查 45.205.27.69 远程网关与远程执行桥状态
- `POST /api/tools/gateway/request` — 服务端调用 45.205.27.69:9090 网关相对路径
- `/api/gateway/*` — OpenAI/Sub2API 兼容网关转发入口，转发到 `REMOTE_GATEWAY_BASE_URL`（默认 `http://45.205.27.69:9090`）
- `GET /api/tools/machine-id/generate` — 生成 Cursor 机器 ID
- `GET /api/tools/ip-check` — IP 查询
- `GET /api/tools/info-generate` — 信息生成
- `GET /api/tools/fingerprint` — 浏览器指纹生成
- `GET /api/tools/full-workflow` — 完整信息 + Outlook 账号生成

### mockup-sandbox (Design, previewPath: /__mockup)
UI component sandbox.

---

## Outlook Batch Registration — Architecture

### How it works
1. Frontend (`FullWorkflow.tsx`) POSTs to `POST /api/tools/outlook/register`
2. Node.js spawns `artifacts/api-server/outlook_register.py` as a subprocess
3. Python uses **patchright** (patched Chromium) + SOCKS5 relay to register accounts
4. Node.js polls Python's stdout for JSON log lines and streams them to the frontend every 2s
5. On completion, Node.js saves successful accounts to PostgreSQL `accounts` table

### CAPTCHA bypass (FREE, no paid service needed) — PROVEN 3/3 success rate
Three-click flow verified working (LainsNL/hrhcode method):
1. **First click**: `[aria-label="可访问性挑战"]` button OUTSIDE FunCaptcha (bot protection gate) — opens image puzzle
2. **Second click**: Inside `frame_locator('iframe[title="验证质询"]')` → `frame_locator('iframe[style*="display: block"]')` → `[aria-label="可访问性挑战"]` — switches from image puzzle to audio/hold mode
3. **Third click (KEY)**: `[aria-label="再次按下"]` — this is the "press again" button that actually triggers and passes the challenge

Critical implementation details:
- Use `frame_locator` API (NOT `page.frames[]`) — automatically handles cross-iframe coordinate offsets
- `iframe[style*="display: block"]` targets the VISIBLE nested iframe (ignores `display:none` ones)
- `bounding_box()` on frame_locator returns correct PAGE coordinates → use `page.mouse.click(x, y)`
- Success detected by: presence of "取消" button OR redirect to `account.live.com/interrupt`
- Works in **headless mode** — no display required, ~75s per account

### Cursor Auto Registration — Architecture
1. Frontend (`CursorRegister.tsx`) POSTs to `POST /api/tools/cursor/register`
2. Node.js spawns `artifacts/api-server/cursor_register.py`
3. Python creates MailTM temp email → opens cursor.sh signup → waits for OTP email (polls MailTM) → enters OTP → saves account
4. Supports up to 5 concurrent registrations

### Proxy pool
- Outlook registration no longer uses the old DB/quarkip proxy pool by default.
- Default automatic proxy mode is CF IP pool + per-account xray relay.
- Manual proxy input is still supported for known-good proxies.
- `socks5_relay.py` creates a local unauthenticated SOCKS5 relay only when a manual authenticated SOCKS5 proxy is provided.

### Key files
- `artifacts/api-server/outlook_register.py` — Outlook registration (patchright + CAPTCHA bypass)
- `artifacts/api-server/cursor_register.py` — Cursor registration (patchright + MailTM OTP)
- `artifacts/api-server/socks5_relay.py` — SOCKS5 relay for authenticated proxies
- `artifacts/api-server/src/routes/tools.ts` — All tool API routes
- `artifacts/ai-toolkit/src/pages/` — All frontend pages
- `artifacts/ai-toolkit/src/data/tools.ts` — 29 tool catalog entries

---

## Database Schema (PostgreSQL)
- `accounts` — email, password, platform, username, token, refresh_token, status, notes, tags
- `emails` — temp email addresses with provider/status info
- `identities` — generated identity info (name, DOB, address, etc.)
- `proxies` — proxy pool with used_count, status (active/banned)
- `configs` — key-value config store (captcha config, etc.)
- `work_guide` — work guide entries (type: tip/warning/workflow/doc)
- API startup now calls `initDatabase()` from `artifacts/api-server/src/db.ts`, creating `accounts`, `temp_emails`, `archives`, `proxies`, `configs`, and `identities` automatically before live pollers start.
- Outlook generation runtime requires Python 3.11 packages from `pyproject.toml`, Patchright/Playwright Chromium browsers, and Chromium system libraries including `nspr`, `nss`, and `libgbm`.

---

## Critical Notes
- **Password**: `yu123456` (stored in sessionStorage as `toolkit_auth_v1`)
- **Never say "Replit"** — always say "Reseek"
- **Username patterns**: 12 formats with birth-year style, never 4-5 digit random suffixes
- **CAPTCHA**: Always try free accessibility bypass first; never use paid solver unless user explicitly sets it in config
- **team-all-in-one**: Flask service on port 5000 (reads PORT env var)
- **openai-pool**: FastAPI service on port 8000 (reads PORT env var)
- **Multi-node gateway bridge**: frontend defaults to `/api/gateway`. `/api/gateway/v1/models`, `/api/gateway/v1/chat/completions`, and `/api/gateway/v1/stats` expose an OpenAI-compatible gateway. It tries `45.205.27.69:9090` Sub2API first, marks it down on `no available OpenAI accounts`/503/429, then falls back to multiple virtual Reseek AI nodes backed by `AI_INTEGRATIONS_OPENAI_BASE_URL` + `AI_INTEGRATIONS_OPENAI_API_KEY`. Configure virtual fallback node count with `RESEEK_AI_NODE_COUNT` (default 4). `remote-exec.js` exposes `/health` and `/exec` on port 9999 for controlled remote operations.
