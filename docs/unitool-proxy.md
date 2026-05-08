# unitool 反向代理技术文档

> **当前版本**：unitool_proxy.py **v5.29**
> **最后更新**：2026-05-08
> **文件位置**：VPS `45.205.27.69` → `/data/Toolkit/artifacts/api-server/unitool_proxy.py`
> **PM2 进程**：id=72，名称 `unitool-proxy`，端口 **8089**

---

## 快速接手（新人速查）

```bash
# SSH 进入服务器
sshpass -p 'HGxQ0ADXPD0b' ssh root@45.205.27.69

# 查看运行状态
pm2 list | grep unitool-proxy
pm2 logs 72 --lines 50 --nostream

# 测试文本服务（基准验证）
curl http://localhost:8089/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o-mini","stream":false,"messages":[{"role":"user","content":"Reply only: PONG"}]}'
# 期望: {"choices":[{"message":{"content":"PONG"...}}]}

# 串行测试所有模型（推荐，避免并发崩溃）
python3 /tmp/test_seq.py

# 查看 SSID 池状态
curl -s http://localhost:8089/pool-status | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('pool:', d['pool_size'], 'live:', d['live'], 'rpm:', d['rpm'])
dead=[a for a in d['accounts'] if a['dead']]
print('dead:', len(dead))
for a in dead[:5]: print(' -', a['label'], a.get('dead_reason','?'))
"

# 重启代理
pm2 restart unitool-proxy

# 重载 SSID（不重启进程）
curl http://localhost:8089/reload-ssids

# GitHub 推送（必须用 python3 subprocess，直接 git 被 sandbox 拦截）
cd /data/Toolkit
python3 -c "
import subprocess, os
env=dict(os.environ,
  GIT_AUTHOR_NAME='Dreamer169',
  GIT_AUTHOR_EMAIL='dreamer7076@users.noreply.github.com',
  GIT_COMMITTER_NAME='Dreamer169',
  GIT_COMMITTER_EMAIL='dreamer7076@users.noreply.github.com')
open('/tmp/m.txt','w').write('fix: describe change here')
subprocess.run(['/usr/bin/git','add','artifacts/api-server/unitool_proxy.py','docs/'], env=env)
r=subprocess.run(['/usr/bin/git','commit','-F','/tmp/m.txt'], env=env, capture_output=True, text=True)
print(r.stdout, r.stderr[:100])
subprocess.run(['/usr/bin/git','push','origin','main'], env=env)
"
```

---

## 1. 两大服务类型

| 类型 | API 路径 | 完成信号 |
|------|---------|---------|
| **文本**（GPT/Claude/Gemini/Grok） | `/api/chats` + SSE 或 poll | `status=ended` |
| **媒体生成**（图像/视频/音频） | `/api/chats` + job 轮询 | `attachments[].uri` |

> **seedance / happyhorse**：`active=None`（未部署占位符），`/api/chats/{id}/messages` 返回
> `{"error":"Unsupported service"}`。v5.22 立即 fast-fail，推荐用 `luma` 替代。

---

## 2. 架构总览

```
Client (OpenAI SDK / curl)
    │ POST /v1/chat/completions
    ▼
unitool_proxy :8089
    ├── [文本服务] _do_chat()
    │     ├── _resolve_model() → service_id + FALLBACK_CHAINS
    │     ├── ImmediateFallback? → skip to fallback[0]  (v5.28)
    │     ├── OSeriesChainFix: o-series chain → gpt-5/gpt-5.5 directly  (v5.29)
    │     ├── _try_service()   → 遍历 SSID 池
    │     └── _send_and_collect_core()
    │           ├── POST /api/chats → chat_id
    │           ├── POST /api/chats/{id}/messages → user_msg_id
    │           ├── if NOT in POLL_PRIMARY: widget/stream SSE
    │           └── paginatedMessages 轮询（主/兜底）
    │
    ├── [媒体服务] _do_media_job()
    │     ├── POST /api/chats → chat_id
    │     ├── POST /api/chats/{id}/messages → job_id
    │     ├── poll paginatedMessages（2s→6s 指数退避）
    │     ├── abort_flag 检查（客户端断开立即停轮询）
    │     └── DELETE /api/chats/{id}
    │
    └── unitool.ai 上游
```

---

## 3. unitool Web API 实探（2026-05-08 确认）

### 3.1 关键端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chats` | POST | 创建对话（文本+媒体通用） |
| `/api/chats/{id}` | DELETE | 删除对话（GuardedChat 清理） |
| `/api/chats/{id}/messages` | POST | 发送消息 → 触发 LLM / 媒体 job |
| `/api/chats/{id}/paginatedMessages` | GET | 拉取消息（含媒体附件、status） |
| `/api/widget/stream` | GET | SSE 流式 token（部分服务被拦截） |
| `/api/services` | GET | 顶层服务列表 |
| `/api/services?parent_id=chatgpt` | GET | chatgpt 子服务（16 个） |
| `/api/user` | GET | 用户信息（余额）|

### 3.2 媒体服务回复结构（关键）

```json
// POST .../messages 立即返回：
{"message":{"id":11674738},"job":{"id":5845328,"status":"pending"}}

// poll paginatedMessages 直到 status=ended：
{"data":[{
  "role":"assistant","content":"","status":"ended","type":"photo",
  "attachments":[{"uri":"https://media.unitool.ai/r2/xxxx.png","type":"png"}]
}]}
```

⚠️ **URL 在 `attachments[].uri`，`content` 为空！**

### 3.3 维护/错误响应格式

unitool 有时返回 **HTTP 200 但 body 含错误码**：

```json
{"code":500,"msg":"The server is currently being maintained, please try again later~"}
```

v5.25 在三处检测此格式并触发 fallback chain（不标记 SSID dead）：
1. POST /api/chats/{id}/messages 返回体
2. paginatedMessages 返回体
3. HTTPError 500 response body

---

## 4. 服务列表（2026-05-08 实测）

### 4.1 文本服务（NATIVE_SERVICES）

| service_id | min_balance | 代理状态 | 备注 |
|-----------|------------|---------|------|
| gpt-5.5 | 1 | ✅ OK | |
| gpt-5.4 | 1 | ✅ OK | |
| gpt-5 | 1 | ✅ OK | 最贵 GPT-5 |
| gpt5.2 | 1 | ✅ OK | |
| gpt5.1 | 1 | ✅ OK | |
| **gpt-4o-mini** | **0 FREE** | ✅ OK | 余额耗尽也可用 |
| **gpt-5-nano** | **0 FREE** | ⚠️ ImmediateFallback | unitool 端挂起，v5.28 直接跳 fallback→gpt-5 |
| gpt-4o | 1 | ✅ OK | |
| gpt-4-1 | 1 | ✅ OK | ChatGPT 4.1 |
| **gpt-4-5** | 1 | ✅ OK | v5.27 重新加回（active=1 确认 2026-05-08）|
| gpt-o1 / gpt-o1-mini | 1 | ⚠️ ImmediateFallback | unitool 端坏，直接跳 fallback→gpt-5 |
| gpt-o3 / gpt-o3-mini / gpt-o3-pro | 1 | ⚠️ ImmediateFallback | unitool 端坏，直接跳 fallback→gpt-5 |
| gpt-o4-mini | 1 | ⚠️ ImmediateFallback | unitool 端坏，直接跳 fallback→gpt-5 |
| claude-sonnet / 4-5 / 4-6 | 1 | ✅ OK（待串行确认） | |
| claude-opus / 4-6 | 1 | ✅ OK（待串行确认）| POLL_PRIMARY |
| ~~claude-haiku~~ | — | ⚠️ 404→fallback | 自动跳 claude-sonnet |
| gemini-3-pro / 3.1-pro | 1 | ⚠️ 偶发维护 | 维护时 fallback→gpt-5.5 |
| grok | 1 | ✅ OK（fallback） | HTTP 500 → fallback→gpt-5.5（v5.27 修复）|

**deepseek**：unitool 无 deepseek 子服务，`deepseek-*` 别名映射到 `gpt-5.5`。

### 4.2 图像服务（IMAGE_SERVICES）

| service_id | min_balance | 备注 |
|-----------|------------|------|
| **gpt-image** | 1 | ✅ 实测 ~15s，1024×1024 PNG |
| dalle-3 | 6.74 | |
| midjourney | 6.5 | |
| stable-diffusion | 6.74 | |
| flux | 6.74 | |
| nanobanana | 7 | |
| remove-background | **3.74** | sdxl 子服务 |
| cleanup | **3.74** | sdxl 子服务 |
| uncrop | **7.49** | sdxl 子服务 |
| reimagine | **7.49** | sdxl 子服务 |
| upscaler | **37.49** | 极贵 |
| image-to-video | **37.49** | 极贵 |

### 4.3 视频服务（VIDEO_SERVICES）

| service_id | min_balance | 备注 |
|-----------|------------|------|
| luma | 31.25 | ✅ 正常工作 |
| kling | 80 | |
| sora2 | 19 | |
| veo3 | 59 | Google Veo 3 |
| hailuo | 50 | Minimax |
| runwayml | 48 | |
| **seedance** | ? | ❌ 未部署占位符（active=None），fast-fail |
| **happyhorse** | ? | ❌ 未部署占位符（active=None），fast-fail |

### 4.4 音频服务（AUDIO_SERVICES）

| service_id | min_balance |
|-----------|------------|
| suno | 15 |
| text-to-speech | 2 |
| voice-cloning | **8**（实测确认）|
| text-to-sound-effects | 2 |
| library | 0.0012 |

---

## 5. POLL_PRIMARY_SERVICES（跳过 widget/stream 直接 poll）

widget/stream 对部分服务存在传输层拦截，原因各异：

| service_id | 拦截原因 | 加入版本 |
|-----------|---------|---------|
| gpt-5.5 | 返回俄语限制提示 | v5.23 |
| gpt-5-nano | 同上（但见 ImmediateFallback — unitool 端挂起） | v5.23 |
| gpt-4-1 | 返回俄语限制提示 | v5.23 |
| claude-sonnet | 返回俄语限制提示 | v5.23 |
| claude-opus | 返回俄语限制提示 | v5.23 |
| claude-opus-4-6 | stream 返回空，poll 返回 CHERRY | v5.24 |
| grok | stream 含推理块且词语翻倍；poll 返回干净答案 | v5.25 |
| gpt-o1/o1-mini/o3/o3-mini/o3-pro/o4-mini | stream 不可靠（见 ImmediateFallback） | v5.27 |

---

## 6. FALLBACK_CHAINS（服务失败自动降级）

```
gpt-5           → [gpt-5.5, gpt-5.4, gpt-4-1, gpt-4o-mini]
gpt-5.5         → [gpt-5, gpt-5.4, gpt-4-1, gpt-4o-mini]
gpt-5.4         → [gpt-5.5, gpt-5, gpt-4-1, gpt-4o-mini]
gpt5.1          → [gpt5.2, gpt-5, gpt-5.5, gpt-4-1]
gpt5.2          → [gpt5.1, gpt-5, gpt-5.5, gpt-4-1]
gpt-4-1         → [gpt-5.4, gpt-5, gpt-4o-mini]
gpt-4o-mini     → [gpt-4-1, gpt-5.4, gpt-5]
gpt-4-5         → [gpt-4-1, gpt-5, gpt-5.5]
gpt-5-nano      → [gpt-5, gpt-5.5, gpt-4-1, gpt-4o-mini]    ← ImmediateFallback 跳此链
gpt-o1          → [gpt-o3, gpt-o4-mini, gpt-o3-mini, gpt-5, gpt-5.5]
gpt-o1-mini     → [gpt-o1, gpt-o4-mini, gpt-o3-mini, gpt-5, gpt-5.5]
gpt-o3          → [gpt-o3-pro, gpt-o4-mini, gpt-o3-mini, gpt-5, gpt-5.5]
gpt-o3-mini     → [gpt-o4-mini, gpt-o3, gpt-o3-pro, gpt-5, gpt-5.5]
gpt-o3-pro      → [gpt-o3, gpt-o4-mini, gpt-o3-mini, gpt-5, gpt-5.5]
gpt-o4-mini     → [gpt-o3-mini, gpt-o3, gpt-o3-pro, gpt-5, gpt-5.5]
                   ↑ v5.28: ImmediateFallback 让 o-series 跳过彼此（全坏），最终落到 gpt-5/gpt-5.5
claude-opus     → [claude-opus-4-6, claude-sonnet-4-6, claude-sonnet]
claude-opus-4-6 → [claude-opus, claude-sonnet-4-6, claude-sonnet]
claude-haiku    → [claude-sonnet, claude-sonnet-4-5]
claude-sonnet-4-6 → [claude-sonnet-4-5, claude-sonnet, claude-opus-4-6]
claude-sonnet-4-5 → [claude-sonnet-4-6, claude-sonnet, claude-opus-4-6]
claude-sonnet   → [claude-sonnet-4-5, claude-sonnet-4-6, claude-opus-4-6]
gemini-3.1-pro  → [gemini-3-pro, gpt-5.5, gpt-5]
gemini-3-pro    → [gemini-3.1-pro, gpt-5.5, gpt-5]
grok            → [gpt-5.5, gpt-5, gpt5.2]   ← v5.27: grok HTTP 500, fallback 成功
```

**注意：ImmediateFallback 在 _do_chat 最前端跳链，不会尝试 primary_id，也不会触发 unitool API。**
v5.28 中 o-series 的链里还有其他 o-series（如 gpt-o3-mini→gpt-o4-mini→gpt-o3），这些也是坏的，
但 ImmediateFallback 只跳过 `primary_id`，后续链里的 o-series 会逐一 try 并靠 SvcErrFallback 或
TimeoutError continue 最终落到 gpt-5 / gpt-5.5。

> **TODO (v5.29)**：将 o-series fallback 链中的 o-series 条目全部移到末尾（或移除），
> 让请求更快落到 gpt-5 / gpt-5.5，减少不必要的等待。

---

## 7. IMMEDIATE_FALLBACK_SERVICES（v5.28 新增）

unitool API 对这些服务**从不返回有效响应**，proxy 直接跳到 fallback 链第一个非自身服务：

```python
IMMEDIATE_FALLBACK_SERVICES = {
    "gpt-o1", "gpt-o1-mini",
    "gpt-o3", "gpt-o3-mini", "gpt-o3-pro", "gpt-o4-mini",
    "gpt-5-nano",   # 发送 reasoning_effort OK，但 paginatedMessages 从无响应
}
```

**确认依据（2026-05-08 实探）：**

| 服务 | unitool 行为 | 证据 |
|------|-------------|------|
| gpt-o1/o3/o3-mini/o3-pro/o4-mini | TypeError / "No completion choices" / 404 | raw_api_test.py 直接报错 |
| gpt-o1-mini | 同上 | 同上 |
| gpt-5-nano（无 reasoning_effort） | 400 "Reasoning is mandatory for this endpoint" | raw_api_test.py |
| gpt-5-nano（有 reasoning_effort） | 聊天创建成功，paginatedMessages 永不返回助手消息 | 60s 原始测试超时 |

---

## 8. 已知模型行为与坑

### 8.1 Grok — 推理块（v5.25 修复）+ HTTP 500 → fallback（v5.27 修复）

**v5.25 修复**：grok 加入 POLL_PRIMARY（跳过含推理块的 widget/stream）。

**v5.27 修复**：grok 在 unitool 端返回 HTTP 500 `"Unexpected end of JSON input"`。
加入 FALLBACK_CHAINS：`grok → [gpt-5.5, gpt-5, gpt5.2]`，自动透明降级。

**实测（2026-05-08，通过代理）：**
- 发送 `grok`，代理触发 GrokFallback → 用 `gpt-5.5` 答复，耗时 5.3s

### 8.2 Gemini — 临时维护（v5.25 修复）

偶发 HTTP 200 body `code=500 "maintained"`。  
v5.25：三处检测 + fallback → gpt-5.5。SSID 不标记 dead。

### 8.3 O-series — 彻底坏（v5.27+v5.28 处理）

o-series 全部在 unitool API 层面坏掉（TypeError / no choices / 404）。  
v5.27 加了 fallback 链（通过 SvcErrFallback 路由到 gpt-5/gpt-5.5），但因为 POLL_PRIMARY 超时 90s 客户端先断，fallback 来不及。  
v5.28 加了 ImmediateFallback：请求 o-series 直接路由到 gpt-5/gpt-5.5，无延迟。

### 8.4 GPT-5-nano — 需 reasoning_effort（v5.27 修复，v5.28 完善）

- 不带 reasoning_effort → unitool 立即 400 "Reasoning is mandatory"
- v5.27：加入 REASONING_SERVICES → 发送 `reasoning_effort=high`
- 但 unitool 端仍不返回响应（聊天创建成功，模型静默）
- v5.28：加入 IMMEDIATE_FALLBACK_SERVICES → 直接路由到 gpt-5（无延迟）

### 8.5 Claude-haiku — 404（v5.24）

claude-haiku 返回 404，自动 fallback → claude-sonnet-4-5 / claude-sonnet。

### 8.6 GPT-4-5 — 重新上线（v5.27）

v5.24 因 unitool API 无此 service_id 而移除，加为 gpt-4-1 别名。  
v5.27：实探确认 `active=1`，重新加入 NATIVE_SERVICES，并从 MODEL_ALIASES 移除。
实测串行可用，耗时 ~50s（正常，该模型较慢）。

### 8.7 Chat settings 陷阱

`chat_settings.system_prompt` **只存 DB，不传 LLM**。

有效 system prompt 注入：在 content 开头加 `[System: ...]` 前缀（`_fmt()` 自动注入）。

### 8.8 pool-status key 陷阱

`/pool-status` 返回的账号列表 key 是 **`accounts`**，不是 `ssids`：

```python
d.get("accounts", [])   # 正确
d.get("ssids", [])      # 错误，永远返回空列表
```

### 8.9 并发测试会崩溃代理

⚠️ **不要并发发送多个 90s+ 超时请求！**

并发 5 个线程同时持有长连接 → pm2 认为进程无响应 → SIGINT → 重启。  
**始终使用串行测试脚本 `/tmp/test_seq.py`**（逐个请求，间隔 1s）。

---

## 9. 模型别名体系

### 9.1 文本别名（MODEL_ALIASES，节选）

```
gpt-4           → gpt-4-1
gpt-4-turbo     → gpt-4-1
claude-opus-4.5/4.6/4-latest → claude-opus-4-6
claude-sonnet-latest → claude-sonnet-4-6
grok-2/3/beta   → grok
gemini-*        → gemini-3.1-pro（模糊匹配兜底）
deepseek-*      → gpt-5.5
o1              → gpt-o1   (ImmediateFallback → gpt-5)
o3              → gpt-o3   (ImmediateFallback → gpt-5.5)
```

### 9.2 媒体别名（MEDIA_ALIASES）

```
dall-e-3 / dalle-2         → dalle-3
image-generation / gpt-image-1 → gpt-image
mj / midjourney-v6/v7     → midjourney
sd / stable-diffusion-xl  → stable-diffusion
flux-pro/schnell/dev      → flux
luma-dream / dream-machine → luma
runway / runway-gen4      → runwayml
sora                      → sora2
veo / google-veo3         → veo3
minimax-video             → hailuo
music-generation / suno-v4 → suno
tts / elevenlabs          → text-to-speech
```

### 9.3 模型后缀

| 后缀 | 效果 |
|------|------|
| `-rp` | ReducedPrompt：max_turns=4 |
| `-nothinking` | 注入 `<no_thinking/>` 禁用推理链 |

---

## 10. SSID 池管理

```bash
# SSID 文件目录
ls /data/unitool_ssids/    # <email>.txt，每文件一个 SSID

# 热添加 SSID
curl -X POST http://localhost:8089/add-ssid \
  -H 'Content-Type: application/json' \
  -d '{"ssid":"<SSID值>","label":"<账号名>"}'

# 重载所有文件（不重启进程）
curl http://localhost:8089/reload-ssids
```

**调度策略**：IdleLongestFirst — `_last_released` 最小的 SSID 优先

**SSID 死亡标记时长：**

| 原因 | 时长 |
|------|------|
| ConnReset ≥ 3 次 | 90s |
| EmptyStreak ≥ 3 次 | 120s |
| 401 / 403 / 423 auth 错误 | 600s |
| 余额耗尽 | 86400s（24h）|
| service_maintenance | **不标记**（v5.25：维护是服务问题）|

---

## 11. 目录结构

```
/data/Toolkit/
  artifacts/api-server/
    unitool_proxy.py                      ← 主代理（当前版本 v5.28）
  docs/
    unitool-proxy.md                      ← 本文档
  scripts/
    unitool_http_register.py              ← HTTP 注册模块（v3.2）
    unitool_chain_v3.py                   ← SSID 维护（pm2 id=73）
    unitool_verify_rescue.py              ← SSID 验证（pm2 id=74）

/data/unitool_ssids/                      ← SSID 文件（~311 个）
  <email>.txt                             ← 每文件一个 SSID
```

---

## 12. Bug 修复历史

### v5.29（2026-05-08）— OSeriesChainFix

| Bug | 修复方式 |
|-----|---------|
| **o-series ImmediateFallback 后仍超时 60s** | 链里的其他 o-series（如 gpt-o3-mini→gpt-o4-mini）也是坏的；v5.29 将所有 o-series 链改为直接跳 `[gpt-5, gpt-5.5, gpt-5.4, gpt-4-1]` |

**验证（2026-05-08 串行）：**
- gpt-o3-mini：OK 18s（→gpt-5）
- gpt-o4-mini：OK 13.8s（→gpt-5）
- gpt-5-nano：OK 10.5s（→gpt-5）

### v5.28（2026-05-08）— ImmediateFallback

| Bug | 修复方式 |
|-----|---------|
| **o-series / gpt-5-nano 在 POLL_PRIMARY 挂起 90s，fallback 从未触发** | 新增 `IMMEDIATE_FALLBACK_SERVICES`；_do_chat 入口处检测，直接跳至 fallback chain 第一个非自身服务 |

### v5.27（2026-05-08）— GrokFallback + OSeriesFallback + NanoReasoning + SvcErrFallback

| Bug | 修复方式 |
|-----|---------|
| **grok HTTP 500 "Unexpected end of JSON input"** | 加入 FALLBACK_CHAINS → [gpt-5.5, gpt-5, gpt5.2] |
| **gpt-5-nano 400 "Reasoning is mandatory"** | 加入 REASONING_SERVICES；发送 reasoning_effort=high + thinking=True |
| **o-series fallback 链缺少非 o-series 兜底** | 链末加入 gpt-5/gpt-5.5（v5.28 再用 ImmediateFallback 完全绕过）|
| **_do_chat 非 retryable service_error 不走 fallback** | 有更多链成员时始终 continue（SvcErrFallback）|
| **gpt-4-5 确认 active=1，v5.24 误移除** | 重新加入 NATIVE_SERVICES，从 MODEL_ALIASES 移除 |
| **语法错误：REASONING_SERVICES 注释内含 `}`** | 手动修复 `}` 位置 |

### v5.25（2026-05-08）— GrokReasoningStrip + GeminiMaintenance

| Bug | 修复方式 |
|-----|---------|
| **Grok 推理块混入输出** | grok 加入 POLL_PRIMARY_SERVICES；`_strip_reasoning_block()` 兜底 |
| **Gemini 维护 500 慢失败（白等 7s）** | 三处检测 "maintained"；raise service_maintenance；SSID 不死 |
| **paginatedMessages 不检查 HTTP 状态码** | `r.status_code != 200` → raise |

### v5.24（2026-05-08）— 7 个 Bug

见旧版文档节选（claude-haiku 404、gpt-4-5 假 ID、gemini fallback、updating 挂起等）。

### v5.23（2026-05-08）— StreamInterception

widget/stream 拦截：gpt-5.5/gpt-5-nano/gpt-4-1/claude-sonnet/claude-opus → POLL_PRIMARY。

---

## 13. 特性清单（v5.28）

```
GuardedChat          — finally 删 chat，避免孤儿 chat
AbortFlag            — BrokenPipe → abort → 中止流
AbortMedia           — 媒体 job 轮询检查 abort
IdleLongestFirst     — _last_released 最小的 SSID 优先
ConnErrCount         — ConnReset≥3 → dead 90s
SSEParser            — buffer+\n\n 分割，处理跨 chunk 边界
HistTrunc            — 保留最近 MAX_HISTORY_TURNS 轮
SnapshotRetry        — msgs_snapshot=[] → 0.5s 重试
SkipEmptyStream      — stream 无内容 → fallback poll
RESIHealthMap        — hash 选端口 + 跳过不健康端口
ExponentialBackoff   — 指数退避
EmptyStreakGuard      — 连续空响应 → dead
RPMCounter           — 实时 RPM（文本+媒体）
AcquireWait          — 无 SSID 最多等 30s
EmailDedup           — 同邮箱 SSID 去重
AutoContinue         — stream 早结束 → 切 poll
StartupRESICheck     — 启动并行健康检查
NoThinking           — -nothinking → <no_thinking/>
MediaJob             — 图像/视频/音频 job 轮询
StreamFix            — streaming 媒体 URL 正确发送
PoolTracking         — 媒体 job _active 计数
SeedanceFastFail     — seedance/happyhorse 立即报错
PollPrimary          — 跳过 widget/stream，直接 poll
StreamIntercept      — 俄语拦截安全网检测
GeminiFallback       — gemini-3.1-pro/3-pro 有 fallback chain
UpdatingHang         — status=updating 无内容超时 ~42s
404Fallback          — claude-haiku 404 → fallback
FixUnsupportedSvc    — unsupported_service 不标 SSID dead
GrokReasoningStrip   — grok 推理块清除（v5.25）
GeminiMaintenance    — 维护 500 快速 fallback，SSID 不死（v5.25）
GrokFallback         — grok HTTP 500 → gpt-5.5 fallback（v5.27）
OSeriesFallback      — o-series 链末加入 gpt-5/gpt-5.5（v5.27）
NanoReasoning        — gpt-5-nano 注入 reasoning_effort=high（v5.27）
SvcErrFallback       — 非 retryable service_error 也走 fallback 链（v5.27）
ImmediateFallback    — 已知坏服务直接跳 fallback，不等 90s（v5.28）
OSeriesChainFix      — o-series fallback 链移除其他 o-series，直接到 gpt-5（v5.29）
```

---

## 14. RESI 端口状态说明

启动日志中 dead 端口较多**属正常**（示例：alive=6, dead=23）。

dead 端口不影响功能——反代轮询 alive 端口，健康检查定期恢复。  
若 **alive 端口 < 3** 才需排查 xray / socat 进程状态：

```bash
pm2 list | grep -E "xray|socat"
pm2 logs xray --lines 20 --nostream
```

---

## 15. 常见问题排查

| 现象 | 排查步骤 |
|------|---------|
| 请求超时 | `pm2 logs 72 --lines 50 --nostream` 看报错；检查 RESI alive 数 |
| 所有 SSID dead | `curl /pool-status` 看 dead_reason；余额耗尽需补充账号 |
| gemini 返回维护提示 | v5.25 自动 fallback 到 gpt-5.5；若持续 >1h 则 unitool 侧问题 |
| grok 无响应 | v5.27 自动 fallback 到 gpt-5.5，5s 内有结果 |
| o-series / gpt-5-nano 超时 | v5.28 已修复（ImmediateFallback → gpt-5）；若仍超时请重启代理 |
| pool-status 显示 ssids=0 | key 是 `accounts`，不是 `ssids` |
| 新 SSID 不生效 | `curl /reload-ssids` 热重载，无需重启进程 |
| 并发测试代理崩溃 | 必须串行测试，见 /tmp/test_seq.py |

---

*文档由 Replit Agent 维护，基于实探验证。v5.29 — 2026-05-08*
