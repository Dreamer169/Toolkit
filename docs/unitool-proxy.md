# unitool 反向代理技术文档

> **当前版本**：unitool_proxy.py **v5.22**
> **最后更新**：2026-05-08（实探全面确认）
> **文件位置**：VPS `45.205.27.69` → `/data/Toolkit/artifacts/api-server/unitool_proxy.py`
> **PM2 进程**：id=72，名称 `unitool-proxy`，端口 **8089**

---

## 快速接手（新人速查）

```bash
# 查看运行状态
pm2 list | grep unitool-proxy
pm2 logs 72 --lines 50 --nostream

# 测试文本服务
curl http://localhost:8089/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o-mini","stream":false,"messages":[{"role":"user","content":"Reply: PONG"}]}'

# 测试图像生成 non-stream（gpt-image 约 15 秒）
curl http://localhost:8089/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-image","stream":false,"messages":[{"role":"user","content":"a red circle"}]}'

# 测试图像生成 streaming（v5.21 实测修复，图像 URL 正确返回）
curl http://localhost:8089/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-image","stream":true,"messages":[{"role":"user","content":"a blue square"}]}'

# 查看 SSID 池状态
curl http://localhost:8089/pool-status | python3 -m json.tool

# 重启代理
pm2 restart 72

# 重载 SSID（不重启）
curl http://localhost:8089/reload-ssids
```

---

## 1. 两大服务类型

| 类型 | API 路径 | 完成信号 |
|------|---------|---------|
| **文本**（GPT/Claude/Gemini/Grok） | `/api/chats` + SSE | `status=ended` |
| **媒体生成**（图像/视频/音频） | `/api/chats` + job 轮询 | `attachments[].uri` |

> **seedance / happyhorse 例外**：`/api/chats/{id}/messages` 返回 `{"error":"Unsupported service"}`，provider-runtime 子路径全 404，消息路径未知（可能 WebSocket）。v5.22 立即 fast-fail，不再 200s 超时。

---

## 2. 架构总览

```
Client (OpenAI SDK / curl)
    │ POST /v1/chat/completions
    ▼
unitool_proxy :8089
    ├── [文本服务] _do_chat()
    │     ├── POST /api/chats → chat_id
    │     ├── POST /api/chats/{id}/messages → user_msg_id
    │     ├── GET /api/widget/stream [SSE 主路径]
    │     └── paginatedMessages 轮询兜底
    │
    ├── [媒体服务] _do_media_job()
    │     ├── POST /api/chats → chat_id
    │     ├── POST /api/chats/{id}/messages → job_id
    │     ├── 轮询 paginatedMessages（2s → 6s 指数退避）
    │     │    → 检查 abort_flag（v5.22：客户端断开立即停轮询）
    │     ├── 返回 Markdown（图像/视频/音频链接）
    │     └── DELETE /api/chats/{id}
    │
    └── unitool.ai → 上游 LLM / 媒体生成后端
```

---

## 3. unitool Web API 实探结果（2026-05-08 全面确认）

### 3.1 关键端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chats` | POST | 创建对话（文本+媒体通用） |
| `/api/chats/{id}` | DELETE | 删除对话（GuardedChat 清理） |
| `/api/chats/{id}/messages` | POST | 发送消息（触发 LLM/媒体 job） |
| `/api/chats/{id}/paginatedMessages` | GET | 拉取消息（含媒体附件） |
| `/api/widget/stream` | GET | SSE 流式 token（文本专用） |
| `/api/services` | GET | 服务列表（顶层 21 个） |
| `/api/services?parent_id=chatgpt` | GET | chatgpt 子服务（16 个，全部 active） |
| `/api/user` | GET | 用户信息（余额） |
| `/api/provider-runtime/chats` | POST | seedance/happyhorse chat shell；**消息路径未知 ❌** |

### 3.2 媒体服务回复结构（关键差异）

```json
// POST /api/chats/{id}/messages 立即返回：
{"message":{"id":11674738},"job":{"id":5845328,"status":"pending"}}

// 轮询 paginatedMessages 直到 status=ended：
{"data":[{
  "role":"assistant","content":"","status":"ended","type":"photo",
  "attachments":[{
    "uri":"https://media.unitool.ai/r2/xxxx.png",
    "type":"png","width":1024,"height":1024
  }]
}]}
```

**关键**：URL 在 `attachments[].uri`，`content` 字段为空！

### 3.3 NATIVE_SERVICES 准确性声明（重要）

> ✅ **2026-05-08 `/api/services?parent_id=chatgpt` 全量探测**，16 个服务**全部 active=1**。
>
> ❌ **此前误报已澄清**："gpt-5/gpt-4-1/gpt-o3 不存在" — **完全错误**。实探全部在线，gpt-4-1 在前端测试也确认可用。

---

## 4. 服务列表（2026-05-08 实测，/api/services 全量探测）

### 4.1 文本服务（NATIVE_SERVICES）

| service_id | min_balance | 价格（input/output tokens） | 备注 |
|-----------|------------|--------------------------|------|
| gpt-5.5 | 1 | 0.0002 / 0.0012 | |
| gpt-5.4 | 1 | 0.0002 / 0.0012 | |
| gpt-5 | 1 | 0.0013 / 0.007 | 最贵 GPT-5 |
| gpt5.2 | 1 | 0.0004 / 0.00135 | |
| gpt5.1 | 1 | 0.0003 / 0.00125 | |
| **gpt-4o-mini** | **0 FREE** | 0 / 0 | 余额耗尽也可用 |
| **gpt-5-nano** | **0 FREE** | 0 / 0 | 余额耗尽也可用 |
| gpt-4o | 1 | 0.001 / 0.003 | |
| gpt-4-1 | 1 | 0.0007 / 0.00125 | ChatGPT 4.1（实测前端可用）|
| gpt-4-5 | 1 | 0.009 / 0.01875 | 最贵 GPT-4 |
| gpt-o1 | 1 | 0.001 / 0.003 | 推理 |
| gpt-o1-mini | 1 | 0.001 / 0.003 | 推理 |
| gpt-o3 | 1 | 0.0007 / 0.00125 | 推理 |
| gpt-o3-mini | 1 | 0.0025 / 0.01 | 推理 |
| gpt-o3-pro | 1 | 0.0025 / 0.015 | 推理 |
| gpt-o4-mini | 1 | 0.0007 / 0.00125 | 推理 |
| claude-sonnet / 4-5 / 4-6 | 1 | — | Claude Sonnet 系 |
| claude-opus / 4-6 | 1 | — | Claude Opus 系 |
| claude-haiku | 1 | — | 轻量 |
| gemini-3-pro / 3.1-pro | 1 | — | 推理模型 |
| grok | 1 | — | x-ai |

**deepseek**：unitool `/api/services` 无 deepseek 子服务，请求映射到 `gpt-5.5`。

### 4.2 图像服务（IMAGE_SERVICES）

| service_id | min_balance（2026-05-08 实测） | 备注 |
|-----------|---------------------------|------|
| **gpt-image** | 1 | ✅ 实测 ~15s，1024×1024 PNG，bonus tokens 可用 |
| dalle-3 | 6.74 | 需 regular tokens |
| midjourney | 6.5 | 需 regular tokens |
| stable-diffusion | 6.74 | |
| flux | 6.74 | |
| nanobanana | 7 | |
| remove-background | **3.74** | ⚠️ 旧注释 0.1 有误，sdxl 子服务 |
| cleanup | **3.74** | ⚠️ 旧注释 0.1 有误，sdxl 子服务 |
| uncrop | **7.49** | ⚠️ 旧注释 0.1 有误，sdxl 子服务 |
| reimagine | **7.49** | ⚠️ 旧注释 0.1 有误，sdxl 子服务 |
| upscaler | **37.49** | ⚠️ 旧注释 0.1 有误，sdxl 子服务（极贵）|
| image-to-video | **37.49** | ⚠️ 旧注释 0.1 有误，sdxl 子服务（极贵）|

### 4.3 视频服务（VIDEO_SERVICES）

| service_id | min_balance | 备注 |
|-----------|------------|------|
| luma | 31.25 | ✅ 标准 `/api/chats` 路径 |
| kling | 80 | 标准路径 |
| sora2 | 19 | 标准路径 |
| veo3 | 59 | Google Veo 3 |
| hailuo | 50 | Minimax |
| runwayml | 48 | Runway ML |
| **seedance** | ? | ❌ **Unsupported service**，v5.22 fast-fail |
| **happyhorse** | ? | ❌ **Unsupported service**，v5.22 fast-fail |

#### seedance/happyhorse 调查详情（2026-05-08 全面探测）

| 路径 | 结果 |
|------|------|
| `POST /api/chats/{id}/messages` | `{"error":"Unsupported service"}` ❌ |
| `POST /api/provider-runtime/chats/{id}/messages` | HTTP 404 ❌ |
| `POST /api/provider-runtime/jobs` | HTTP 404 ❌ |
| `GET /api/provider-runtime/chats` | HTTP 404 ❌ |
| `POST /api/provider-runtime/chats` | 返回已有 chat shell，无法发消息 |

**结论**：消息提交路径可能是 WebSocket 或其他机制；需抓包 unitool 前端流量才能确认。

### 4.4 音频服务（AUDIO_SERVICES）

| service_id | min_balance（实测） |
|-----------|-----------------|
| suno | 15 |
| text-to-speech | 2 |
| **voice-cloning** | **8**（之前未知，实测确认）|
| text-to-sound-effects | 2 |
| library | 0.0012 |

---

## 5. 模型别名体系

### 5.1 媒体模型别名（MEDIA_ALIASES）

```
dall-e-3 / dalle-2           → dalle-3
image-generation / gpt-image-1 → gpt-image
mj / midjourney-v6/v7        → midjourney
sd / stable-diffusion-xl     → stable-diffusion
flux-pro/schnell/dev         → flux
luma-dream / dream-machine   → luma
runway / runway-gen4         → runwayml
sora                         → sora2
veo / google-veo3            → veo3
minimax-video                → hailuo
music-generation / suno-v4   → suno
tts / elevenlabs             → text-to-speech
```

### 5.2 模型后缀

| 后缀 | 效果 |
|------|------|
| `-rp` | ReducedPrompt：max_turns=4 |
| `-nothinking` | 注入 `<no_thinking/>` 禁用推理链 |

---

## 6. chat_settings 陷阱（实测确认）

`chat_settings.system_prompt` **只存 DB，不传 LLM**。

有效 system prompt 注入：在 content 开头加 `[System: ...]` 前缀。`_fmt()` 自动注入，空 system 也输出 `[System: ]` 以覆盖 unitool 默认俄语 system prompt。

---

## 7. SSID 池管理

```bash
# 文件目录
ls /data/unitool_ssids/

# 热添加 SSID
curl -X POST http://localhost:8089/add-ssid \
  -H 'Content-Type: application/json' \
  -d '{"ssid":"你的SSID值","label":"账号名"}'

# 重载所有文件（不重启）
curl http://localhost:8089/reload-ssids
```

**调度策略**：IdleLongestFirst — 选 `_last_released` 最小（最久未用）的 SSID。

**死亡标记**：ConnReset≥3 → 90s | EmptyStreak≥3 → 120s | 401/403 → 600s | 余额耗尽 → 86400s

---

## 8. 媒体生成流程（v5.20+）

```
POST /v1/chat/completions {"model":"gpt-image",...}
    ↓ _do_media_job()
    ├── POST /api/chats → chat_id
    ├── POST /api/chats/{id}/messages → job_id
    ├── poll paginatedMessages（2s 起，指数退避至 6s）
    │    → 检查 abort_flag（v5.22 新增）
    │    → status=ended → attachments[0].uri
    ├── stream=true → chunk_cb(markdown)（v5.21 修复，实测确认）
    └── DELETE /api/chats/{id}
```

**实测**：gpt-image ~15s → 1024×1024 PNG，URL `https://media.unitool.ai/r2/{uuid}.png`

---

## 9. Bug 记录与修复历史

### v5.22（2026-05-08）

| 内容 | 说明 |
|------|------|
| **seedance/happyhorse fast-fail** | 之前 200s 超时静默失败；现在立即抛出带调查信息的错误，提示用 `luma` 替代 |
| **abort 参数传入 _do_media_job** | 客户端断开可立即中止媒体 job 轮询 |
| **sdxl min_balance 注释修正** | 旧值 0.1 有误；实测：3.74 / 7.49 / 37.49 |
| **voice-cloning min_balance** | 实测 min_bal=8（之前未记录）|
| **版本字符串修正** | docstring v5.20 → v5.22 |

### v5.21（2026-05-08）—— Bug 1/2/3 全部实测验证

| Bug | 严重性 | 状态 |
|-----|-------|------|
| **streaming 丢图（Bug 1）** | 🔴 CRITICAL | ✅ **live 验证 FIXED**：streaming curl 正确输出图像 URL |
| **pool _active 不追踪（Bug 2）** | 🟡 | ✅ FIXED（代码审查确认）|
| **_record_rpm 不调用（Bug 3）** | 🟡 | ✅ FIXED（代码审查确认）|

### v5.21 streaming 验证记录

```
# 实测命令（2026-05-08）
curl http://localhost:8089/v1/chat/completions \
  -d '{"model":"gpt-image","stream":true,"messages":[{"role":"user","content":"red square"}]}'

# 实测输出（正确）
data: {"choices":[{"delta":{"content":"![red-square.png (1024x1024)](https://media.unitool.ai/r2/ac08c5fc-66b8-46ed-9aa4-97cb2994066f.png)\n\n[Download](...)"}}]}
data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]
```

---

## 10. 版本历史

| 版本 | 关键改动 |
|------|---------|
| v5.10 | SSE 主路径 + paginatedMessages 兜底 + SSID 池 |
| v5.11 | GuardedChat、AbortFlag、IdleLongestFirst、ConnErrCount、SSEParser、HistTrunc |
| v5.12 | SnapshotRetry、SkipEmptyStream |
| v5.13 | RESI 健康检查、ExponentialBackoff、EmptyStreakGuard、RPMCounter |
| v5.14 | AutoContinue、AcquireWait、EmailDedup |
| v5.15–v5.18 | StartupRESICheck、EmailDedup、SystemPromptFix、VersionStringFix |
| v5.19 | `-nothinking` 后缀支持 |
| **v5.20** | 📦 媒体服务（IMAGE/VIDEO/AUDIO）`_do_media_job()` + 164 模型 |
| **v5.21** | 🐛 Bug 1/2/3 修复（streaming + pool + RPM），实测验证 |
| **v5.22** | 🐛 seedance fast-fail + abort 参数 + min_balance 注释修正 |

---

## 11. 特性清单（v5.22）

```
GuardedChat       — finally 删 chat，避免孤儿 chat
AbortFlag         — BrokenPipe → abort → 中止流
AbortMedia        — 媒体 job 轮询检查 abort（v5.22）
IdleLongestFirst  — _last_released 最小的 SSID 优先
ConnErrCount      — ConnReset≥3 → dead 90s
SSEParser         — buffer+\n\n 分割，处理跨 chunk 边界
HistTrunc         — 保留最近 MAX_HISTORY_TURNS 轮
SnapshotRetry     — msgs_snapshot=[] → 0.5s 重试
SkipEmptyStream   — stream 无内容 → fallback poll
RESIHealthMap     — hash 选端口 + 跳过不健康端口
ExponentialBackoff— 指数退避
EmptyStreakGuard  — 连续空响应 → dead
RPMCounter        — 实时 RPM（文本+媒体）
AcquireWait       — 无 SSID 最多等 30s
EmailDedup        — 同邮箱 SSID 去重
AutoContinue      — stream 早结束 → 切 poll
StartupRESICheck  — 启动并行健康检查
NoThinking        — -nothinking → <no_thinking/>
MediaJob          — 图像/视频/音频 job 轮询
StreamFix         — streaming 媒体 URL 正确发送（v5.21 实测）
PoolTracking      — 媒体 job _active 计数（v5.21）
SeedanceFastFail  — seedance/happyhorse 立即报错（v5.22）
```

---

## 12. 目录结构

```
/data/Toolkit/
  artifacts/api-server/unitool_proxy.py   ← 主代理（v5.22）
  scripts/unitool_chain_v3.py             ← SSID 维护
  scripts/unitool_login.py                ← 登录
  scripts/unitool_verify_rescue.py        ← SSID 验证
  docs/unitool-proxy.md                   ← 本文档

/data/unitool_ssids/                      ← SSID 文件（311 个）
  <email>.txt                             ← 每文件一个 SSID
```

---

*文档基于 2026-05-08 `/api/services` 全量实探 + live curl 验证，由 Replit Agent 整理。*


---

## v5.23 — Stream Interception Fix (2026-05-08)

### Critical Bug: widget/stream Interception by Service

Confirmed via direct comparison tests (same SSID, same prompt):

| Service | widget/stream | paginatedMessages | Fix Applied |
|---------|--------------|-------------------|-------------|
| `gpt-4o-mini` | ✅ Real response | ✅ Real response | None needed |
| `gpt-4o` | ✅ Real response | ✅ Real response | None needed |
| `gpt-5`, `gpt5.1`, `gpt5.2` | ✅ Real response | ✅ Real response | None needed |
| `gpt-o3-mini`, `gpt-o3`, `gpt-o4-mini` | ✅ Real response | ✅ Real response | None needed |
| `claude-sonnet-4-5`, `claude-sonnet-4-6` | ✅ Real response | ✅ Real response | None needed |
| **`gpt-5.5`** | ❌ Russian restriction msg | ✅ Real response | `POLL_PRIMARY_SERVICES` |
| **`gpt-5-nano`** | ❌ Russian restriction msg | ✅ Real response | `POLL_PRIMARY_SERVICES` |
| **`gpt-4-1`** | ❌ Russian restriction msg | ✅ Real response | `POLL_PRIMARY_SERVICES` |
| **`claude-sonnet`** | ❌ Russian restriction msg | ✅ Real response | `POLL_PRIMARY_SERVICES` |
| **`claude-opus`** | ❌ Russian restriction msg | ✅ Real response | `POLL_PRIMARY_SERVICES` |

**Russian restriction message** (verbatim from widget/stream for affected services):
> "Я помогаю только с вопросами платформы Unitool и написанием..."

The `[System: ]` prefix in content bypasses this for paginatedMessages but NOT for
widget/stream. widget/stream's interception happens at the transport layer, before
the model processes the prompt.

**Mechanism**: unitool's SSE transport layer intercepts widget/stream for certain
service_ids and injects the Russian restriction response. paginatedMessages polls
the backend DB directly where the actual LLM response is written — no interception.

### Fix: `POLL_PRIMARY_SERVICES` + `_STREAM_INTERCEPT_RU`

```python
POLL_PRIMARY_SERVICES = {
    "gpt-5.5", "gpt-5-nano", "gpt-4-1",
    "claude-sonnet", "claude-opus",
}
_STREAM_INTERCEPT_RU = "помогаю только"
```

`_send_and_collect_core` now:
1. Skips widget/stream entirely for `POLL_PRIMARY_SERVICES` (goes straight to
   `_paginated_poll`)
2. Detects Russian interception string in stream output as safety net for
   any unlisted intercepted services

**Verified (2026-05-08)**:
- `claude-sonnet` → PAPAYA ✅ (was returning Russian msg before v5.23)
- `gpt-5.5` (via `chatgpt` alias) → LEMON ✅ (was returning Russian msg)
- `gpt-4o-mini` → PONG ✅ (still uses fast widget/stream path)

### Other v5.23 Changes

**seedance/happyhorse error message corrected**: Changed from "API path unknown
(possibly WebSocket)" to "service is inactive — active=None in /api/services
(no pricing/balance fields). This is a placeholder not yet deployed."

Root cause confirmation: `/api/services` returns `active=None` (null) with no
pricing/balance fields for seedance and happyhorse. Both are confirmed placeholder
entries not yet deployed by unitool. luma is the correct working alternative.

**API architecture clarified** (from deep analysis session):
- `service_id="chatgpt"` (top-level) → "Unsupported service" on message send
- Model-level IDs (`gpt-4o-mini`, `gpt-5.5`, `claude-sonnet`, etc.) required
- Proxy already correctly uses model-level IDs in `NATIVE_SERVICES`
- `/api/developer`, `/api/api`, `/api/keys` endpoints return 200 but are CSR
  pages — content only in JS bundle, no API key issuance via REST

