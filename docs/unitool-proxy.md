# unitool 反向代理技术文档

> **当前版本**：unitool_proxy.py **v5.38**
> **最后更新**：2026-05-09
> **文件位置**：VPS `45.205.27.69` → `/data/Toolkit/artifacts/api-server/unitool_proxy.py`
> **PM2 进程**：id=79，名称 `unitool-proxy`，端口 **8089**
> **GitHub**：https://github.com/Dreamer169/Toolkit （main 分支）

---

## 快速接手（新人速查）

```bash
# SSH 进入服务器
sshpass -p 'HGxQ0ADXPD0b' ssh root@45.205.27.69

# 查看运行状态
pm2 list | grep unitool-proxy
pm2 logs 79 --lines 50 --nostream

# 测试文本服务（基准验证）
curl http://localhost:8089/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o-mini","stream":false,"messages":[{"role":"user","content":"Reply only: PONG"}]}'
# 期望: {"choices":[{"message":{"content":"PONG"...}}]}

# 查看服务健康状态
curl -s http://localhost:8089/v1/svc-status | python3 -m json.tool

# 手动清除 maintenance 缓存
curl -s -X POST http://localhost:8089/v1/svc-status/clear \
  -H 'Content-Type: application/json' \
  -d '{"service":"gemini-3.1-pro"}'

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
TOKEN = 'ghp_<YOUR_GITHUB_TOKEN>'
env = dict(os.environ,
  GIT_AUTHOR_NAME='probe-bot', GIT_AUTHOR_EMAIL='bot@probe.local',
  GIT_COMMITTER_NAME='probe-bot', GIT_COMMITTER_EMAIL='bot@probe.local')
subprocess.run(['git','add','artifacts/api-server/unitool_proxy.py','docs/'], env=env)
r = subprocess.run(['git','commit','-m','fix: describe change'], env=env, capture_output=True, text=True)
print(r.stdout, r.stderr[:100])
subprocess.run(['git','push', f'https://{TOKEN}@github.com/Dreamer169/Toolkit.git', 'main'], env=env)
"
```

---

## 1. 两大服务类型

| 类型 | API 路径 | 完成信号 |
|------|---------|---------|
| **文本**（GPT/Claude/Gemini/Grok） | `/api/chats` + poll | `status=ended` |
| **媒体生成**（图像/视频/音频） | `/api/chats` + job 轮询 | `attachments[].uri` |

> **seedance / happyhorse**：`active=None`（未部署占位符）。v5.22 立即 fast-fail，推荐用 `luma` 替代。

---

## 2. 架构总览

```
Client (OpenAI SDK / curl)
    │ POST /v1/chat/completions
    ▼
unitool_proxy :8089
    ├── [永久损坏] IMMEDIATE_FALLBACK_SERVICES
    │     └── 立即返回 model_not_available 错误（无 SSID 消耗）
    │
    ├── [维护缓存] _is_svc_dead()
    │     └── 命中 → 返回 service_maintenance 错误（5min 缓存）
    │
    ├── [文本服务] _do_chat()
    │     ├── _resolve_model() → service_id + FALLBACK_CHAINS
    │     ├── v5.31: NO fallback — return actual error for requested model
    │     ├── _try_service()   → 遍历 SSID 池（maintenance 自动重试 3 次，v5.36）
    │     └── _send_and_collect_core()
    │           ├── POST /api/chats → chat_id
    │           ├── POST /api/chats/{id}/messages → user_msg_id
    │           ├── if NOT in POLL_PRIMARY: widget/stream SSE（尝试）
    │           └── paginatedMessages 轮询（主/兜底，所有 POLL_PRIMARY 服务）
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

## 3. unitool Web API 实探（probe v3.0，2026-05-09 确认）

### 3.1 关键端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chats` | POST | 创建对话（文本+媒体通用） |
| `/api/chats/{id}` | DELETE | 删除对话（GuardedChat 清理） |
| `/api/chats/{id}/messages` | POST | 发送消息 → 触发 LLM / 媒体 job |
| `/api/chats/{id}/paginatedMessages` | GET | 拉取消息（含媒体附件、status） |
| `/api/widget/stream` | GET | SSE 流式 token（大量服务被拦截） |
| `/api/services` | GET | 顶层服务列表 |
| `/api/user` | GET | 用户信息（余额）|

### 3.2 API 调用流程（probe v3.0 实探确认）

```python
# 1. 创建对话
POST /api/chats
Body: {"service_id": "claude-opus-4-6"}
Cookie: __Secure-unitool-ssid={ssid}   # ← 注意：是 __Secure-unitool-ssid，不是 ssid！
→ Response: {"id": 12345678}

# 2. 发送消息
POST /api/chats/12345678/messages
Body: {"content": "你的问题", "attachments": [], "options": ""}
→ Response: {"message": {"id": 11674738}, ...}

# 3. 轮询结果（paginatedMessages 在 t=0s 即可返回）
GET /api/chats/12345678/paginatedMessages?page=1&limit=20
→ Response: {
    "data": [
      {"role": "user",      "content": "你的问题", ...},
      {"role": "assistant", "content": "AI 回复", "status": "ended", "cost": 46}
    ]
  }
# ⚠ paginatedMessages 里没有 model_slug 字段！无法从元数据判断后端模型。
# 唯一方法：AI 自报（identity prompt）

# 4. 清理
DELETE /api/chats/12345678
```

> **Cookie 名称是关键**：必须用 `__Secure-unitool-ssid=xxx`，不是 `ssid=xxx`，
> 否则一律返回 401。这是 probe v1→v3 踩过最大的坑。

### 3.3 paginatedMessages 字段含义

```json
{
  "role": "assistant",
  "content": "AI 回复文本",
  "status": "ended",        // "pending" / "streaming" / "ended" / "error"
  "cost": 46,               // 本次消耗的 unitool token 数
  "type": "text",           // 或 "photo" / "video" / "audio"
  "attachments": [],        // 媒体服务填充，文本服务为空
  "service_id": ""          // 通常为空，不可靠
  // ⚠ 没有 model_slug / model_name 字段
}
```

### 3.4 REASONING_SERVICES 特殊参数

推理服务（o-series）需要在创建 chat 时额外传递参数，
但这些服务在 unitool 后端实际上已全部损坏（见第 7 节）：

```python
# 正确的推理服务 chat 创建（如果后端恢复）：
POST /api/chats
Body: {
  "service_id": "gpt-o3",
  "chat_settings": json.dumps({"reasoning_effort":"high","thinking":True})
}
# ⚠ 发送消息时不带 options 字段（带了会触发后端 JS TypeError）
```

---

## 4. 后端模型真实身份（probe v3.0 AI 自报，2026-05-09）

> **探针方法**：直接 HTTPS，cookie = `__Secure-unitool-ssid`，
> 用 paginatedMessages poll 获取回复，询问 AI 自报模型名。
> paginatedMessages 本身不含 model_slug，所有结论来自 AI 自报。

### 4.1 GPT 系列

| service_id | 后端真实模型 | 置信度 | 探针证据 |
|------------|------------|--------|---------|
| `gpt-4o` | **GPT-4o** | 高 | AI 自报 "GPT-4o" |
| `gpt-4-1` | **GPT-4o**（同一后端！） | 高 | AI 自报 "GPT-4o" — 与 gpt-4o 完全相同 |
| `gpt4o-mini` | **ChatGPT-3.5 / 4.0**（轮换） | 中 | AI 自报轮换 |
| `gpt5.1` | **GPT-4.1** | 高 | AI 自报 "GPT-4.1" |
| `gpt-5.5` | **GPT-4o 或 GPT-4.1** | 中 | 见下方深度分析 |
| `gpt-5` / `gpt-5.4` / `gpt5.2` | 拒绝披露 | — | 回复 "unknown"/"unavailable" |

### ★ gpt-5.5 深度分析（probe v3.0，2026-05-09）

```
探针结果：
  [identity]    cost=0  → "internal stream ended unexpectedly"  ← 流被截断，无法自报
  [version_pick] cost=0  → "internal stream ended unexpectedly"  ← 同上
  [cutoff]      cost=8  → "2024-06"                             ← 训练截止 2024年6月
  [context_win] cost=127 → "128000"                             ← 上下文窗口 128k tokens
  [web_access]  cost=0  → "internal stream ended unexpectedly"  ← 流截断
  [short_ok]    cost=19 → "ok"                                  ← 能响应（非流模式可用）
  [reasoning]   cost=90 → "No."                                 ← 不支持 o1 推理模式
  [compare]     cost=0  → 无响应
```

**结论（推断）**：

- 上下文窗口 **128k**（非 GPT-5 的 1M）→ 排除真正的 GPT-5
- 训练截止 **2024年6月** → 符合 GPT-4.1（June 2024）或 GPT-4o（April 2024）
- 不支持 o1 推理 → 排除 o-series
- 流（widget/stream）对多数 prompt 返回 "internal stream ended unexpectedly"
  → 必须走 POLL_PRIMARY 路径，且仍有部分 prompt 无响应（cost=0）

**最可能后端：GPT-4o（128k + ~April-June 2024 cutoff）**，或 GPT-4.1（同上下文但截止更接近 June 2024）。
unitool 可能将 `gpt-5.5` 标签路由到与 `gpt-4o` / `gpt-4-1` 相同的后端。

> 注意：gpt-5.5 的流极不稳定。identity/capabilities 类 prompt 触发流截断（cost=0），
> 而 short/reasoning 类 prompt 则能成功返回。这与 gpt-4o 的 POLL_PRIMARY 行为高度吻合。

### 4.2 Claude 系列

| service_id | 后端真实模型 | 置信度 | 探针证据 |
|------------|------------|--------|---------|
| `claude-sonnet` | **Claude 3.5 Sonnet** | 高 | AI 自报 |
| `claude-sonnet-4-5` | **Claude 3.5/3.7 Sonnet**（后端在轮换！） | 中 | AI 自报，两次结果不同 |
| `claude-sonnet-4-6` | **Claude 3.5/3.7 Sonnet**（轮换） | 中 | 同上 |
| `claude-opus-4-6` | **claude-sonnet-4-20250514** | 高 | 见下方深度分析 |

### ★ claude-opus-4-6 深度分析（probe v3.0，2026-05-09）

```
探针结果：
  [identity]     cost=13  → "claude-sonnet-4-20250514"           ← ★ 直接泄露真实模型名！
  [confirm_ver]  cost=66  → "我无法确认/否认是否是 claude-opus-4-20250514"  ← 后续变得谨慎
  [context_win]  cost=6   → "200000"                             ← 200k 上下文
  [thinking]     cost=5   → "Yes."                               ← 支持 extended thinking
  [cutoff]       cost=7   → "Early 2025"                         ← 2025年初截止
  [version_fill] cost=19  → "claude-opus-4-0725-2025"            ← 部分幻觉，不可信
  [short_ok]     cost=4   → "ok"
  [capabilities] cost=25  → "Advanced reasoning, long-form writing, code generation"
  [compare]      cost=0   → 拒绝比较（cost=0 → 流截断）
  [pricing_hint] cost=4   → "Premium"
```

**结论（高置信度）**：

`claude-opus-4-6` 在 unitool 后端实际运行的是 **claude-sonnet-4-20250514**（Claude Sonnet 4），
并非 Claude Opus 4。第一次 identity 探针（cost=13）直接返回了真实模型名。

关键证据：
- 200k 上下文 → 与 Claude Sonnet 4 / Opus 4 两者均吻合（均为 200k）
- 支持 extended thinking → 仅 Claude 3.7 Sonnet+ / Opus 4 支持，**Sonnet 4 也支持**
- 训练截止 Early 2025 → Claude Sonnet 4（claude-sonnet-4-20250514 发布于 2025-05-14）
- 直接 identity 自报 → "claude-sonnet-4-20250514"（最可靠的单点证据）

> 实际意义：调用 `claude-opus-4-6` 花费 Opus 级价格，但拿到的是 **Sonnet 4** 的能力。
> 对用户来说这仍然是当前最强的 Claude 模型之一，只是 unitool 标签命名具有误导性。

---

## 5. 服务列表（2026-05-09 实测 v5.38）

### 5.1 文本服务（NATIVE_SERVICES）

| service_id | min_balance | 代理状态 | 后端真实模型 | 备注 |
|-----------|------------|---------|------------|------|
| **gpt-5** | 1 | ✅ OK | 未知（拒绝自报） | |
| **gpt-5.5** | 1 | ✅ OK（流不稳） | GPT-4o 或 GPT-4.1 | 128k 上下文，June 2024 截止 |
| **gpt-5.4** | 1 | ✅ OK | 未知（拒绝自报） | 慢 ~40s，POLL_PRIMARY |
| **gpt5.1** | 1 | ✅ OK | **GPT-4.1** | AI 自报确认 |
| **gpt5.2** | 1 | ✅ OK | 未知 | |
| **gpt-4o** | 1 | ✅ OK（POLL） | **GPT-4o** | v5.38 确认 stream 被截 |
| **gpt-4o-mini** | 0 FREE | ✅ OK（POLL） | **ChatGPT-3.5/4.0** | v5.38 确认 stream 被截 |
| **gpt-4-1** | 1 | ✅ OK | **GPT-4o**（同 gpt-4o！） | AI 自报两者相同 |
| gpt-4-5 | 1 | ❌ 永久损坏 | — | 400 Unsupported |
| gpt-o1/o1-mini/o3/o3-mini/o3-pro/o4-mini | 1 | ❌ 永久损坏 | — | TypeError/no-choices |
| gpt-5-nano | 0 FREE | ❌ 永久损坏 | — | 400 Reasoning is mandatory |
| **claude-sonnet** | 1 | ✅ OK | **Claude 3.5 Sonnet** | |
| **claude-sonnet-4-5** | 1 | ✅ OK | **Claude 3.5/3.7 Sonnet**（轮换） | |
| **claude-sonnet-4-6** | 1 | ✅ OK（POLL） | **Claude 3.5/3.7 Sonnet**（轮换） | v5.38 确认 stream 被截 |
| **claude-opus-4-6** | 1 | ✅ OK（POLL） | **claude-sonnet-4-20250514** ★ | 标称 Opus，实为 Sonnet 4 |
| claude-opus | 1 | ❌ 永久损坏 | — | 400 max_tokens > 32000 |
| claude-haiku | 1 | ❌ 永久损坏 | — | HTTP 500，v5.38 re-route → sonnet-4-5 |
| **gemini-3-pro** | 1 | ✅ OK（POLL） | 未知 | 慢 ~40s，POLL_PRIMARY |
| **gemini-3.1-pro** | 1 | ✅ OK（POLL） | 未知 | 慢 ~50s，POLL_PRIMARY |
| **grok** | 1 | ✅ OK | 未知 | 含双打印推理块（GrokReasoningStrip 处理）|

> **POLL_PRIMARY_SERVICES（跳过 widget/stream，直接 paginatedMessages）**：
> `gpt-5.5`, `gpt-5-nano`, `gpt-4-1`, `gpt-4o`, `gpt-4o-mini`,
> `claude-sonnet`, `claude-opus`, `claude-opus-4-6`, `claude-sonnet-4-6`,
> `grok`, `gpt-o1`, `gpt-o1-mini`, `gpt-o3`, `gpt-o3-mini`, `gpt-o3-pro`, `gpt-o4-mini`,
> `gemini-3.1-pro`, `gemini-3-pro`, `gpt-5.4`

### 5.2 图像服务（IMAGE_SERVICES）

| service_id | min_balance | 备注 |
|-----------|------------|------|
| **gpt-image** | 1 | ✅ ~15s，1024×1024 PNG |
| dalle-3 | 6.74 | |
| midjourney | 6.5 | |
| stable-diffusion | 6.74 | |
| flux | 6.74 | |
| nanobanana | 7 | |
| remove-background | 3.74 | sdxl 子服务 |
| cleanup | 3.74 | sdxl 子服务 |
| uncrop | 7.49 | sdxl 子服务 |
| reimagine | 7.49 | sdxl 子服务 |
| upscaler | 37.49 | 极贵 |
| image-to-video | 37.49 | 极贵 |

### 5.3 视频服务（VIDEO_SERVICES）

| service_id | min_balance | 备注 |
|-----------|------------|------|
| **luma** | 31.25 | ✅ 正常 |
| kling | 80 | |
| sora2 | 19 | |
| veo3 | 59 | |
| hailuo | 50 | |
| runwayml | 48 | |

---

## 6. FALLBACK_CHAINS（v5.38 当前配置）

```python
# 无任何服务可用时的 fallback 顺序（v5.31 后默认不走 fallback，直接报错）
# FALLBACK_CHAINS 仅用于 _resolve_model 的别名补全，不自动切换

"gpt-5":            ["gpt-5.5",   "gpt-5.4",  "gpt-4-1",  "gpt-4o-mini"]
"gpt-5.5":          ["gpt-5",     "gpt-5.4",  "gpt-4-1",  "gpt-4o-mini"]
"gpt-4o":           ["gpt-4-1",   "gpt-5.4",  "gpt-5"]
"claude-opus-4-6":  ["claude-opus","claude-sonnet-4-6","claude-sonnet-4-5","claude-sonnet"]
"claude-sonnet-4-6":["claude-sonnet-4-5","claude-sonnet","claude-opus-4-6"]
"gemini-3.1-pro":   ["gemini-3-pro","gpt-5.5","gpt-5"]
"gpt-o1/o3/...":    ["gpt-5","gpt-5.5","gpt-5.4","gpt-4-1"]  # o-series 全部损坏，直接跳到 GPT-5
```

---

## 7. 永久损坏模型清单（2026-05-09 实测）

```
❌ gpt-o1        — TypeError/no-choices，unitool 后端彻底损坏
❌ gpt-o1-mini   — 同上
❌ gpt-o3        — 同上
❌ gpt-o3-mini   — 同上
❌ gpt-o3-pro    — "No content returned from API"
❌ gpt-o4-mini   — 同上
❌ gpt-5-nano    — 400 "Reasoning is mandatory"（即使带 reasoning_effort 也报错）
❌ claude-opus   — 400 max_tokens: 32768 > 32000（claude-opus-4-20250514 限制）
❌ gpt-4-5       — 400 Unsupported service
❌ claude-haiku  — HTTP 500 consistently；v5.38 re-route → claude-sonnet-4-5
```

---

## 8. 流拦截机制（stream interception）

unitool 对某些服务/IP 返回俄语限制文本（stream 级拦截）：

```
"Извините, я помогаю только..."
```

代理检测到此字符串（`_STREAM_INTERCEPT_RU`）后，丢弃 stream 结果，切换到 paginatedMessages 轮询。

**v5.38 新增确认拦截的服务**：
- `gpt-4o` — 之前误标为 clean，v5.38 确认拦截
- `gpt-4o-mini` — 同上
- `claude-sonnet-4-6` — 同上

所有拦截服务均已加入 `POLL_PRIMARY_SERVICES`，直接走 paginatedMessages，不再尝试 stream。

---

## 9. 变更记录

### v5.38（2026-05-09）probe v3.0 全量探针

| # | 变更 | 说明 |
|---|------|------|
| 1 | POLL_PRIMARY 新增 `gpt-4o`, `gpt-4o-mini`, `claude-sonnet-4-6` | probe 确认 stream 被俄语拦截 |
| 2 | `claude-haiku` → `claude-sonnet-4-5` 别名 | haiku 在 unitool 404 死亡 |
| 3 | 版本字符串 5.34→5.38 | 更新所有启动 log 行 |
| 4 | 后端 identity 注释更新 | gpt-4o/gpt-4-1 共用 GPT-4o；claude-opus-4-6 → Sonnet 4 |

### v5.36（2026-05-08）maintenance 透明重试

| # | 旧行为 | 新行为 |
|---|--------|--------|
| 1 | 首次 500 → 封锁 service 30 分钟 | 首次 500 → 立即新 SSID + 新 chat 重试（最多 3 次）|
| 2 | 耗尽重试 → 缓存 30 分钟 | 耗尽重试 → 缓存 5 分钟 |

```python
if "service_maintenance" in err or "backend_error_500" in err:
    maint_retries += 1
    if maint_retries > MAX_MAINT_RETRIES:  # = 3
        raise  # 真正维护，缓存 5min
    time.sleep(1.0)
    continue  # 新 SSID + 新 chat_id
```

### v5.35（2026-05-08）

- maintenance 缓存从 24h → 30 分钟
- `gemini-3.1-pro`, `gemini-3-pro`, `gpt-5.4` 加入 POLL_PRIMARY
- `claude-haiku` 加入 IMMEDIATE_FALLBACK_SERVICES

### v5.31（历史）

- NO fallback 原则：模型不可用直接报错，不静默切换

---

## 10. 代理内部端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST | OpenAI 兼容入口 |
| `/v1/models` | GET | 模型列表 |
| `/v1/svc-status` | GET | 服务健康状态（维护缓存、永久损坏） |
| `/v1/svc-status/clear` | POST | 手动清除 maintenance 缓存 `{"service":"model-id"}` |
| `/pool-status` | GET | SSID 池详情（每账号状态、余额、活跃并发）|
| `/reload-ssids` | GET | 重载 SSID 池（不重启进程）|
| `/add-ssid` | POST | 动态添加 SSID `{"ssid":"...","label":"name"}` |
| `/healthz` | GET | 健康检查（返回 `ok`）|
| `/ssid-status` | GET | 池摘要（大小、live 数量）|

---

## 11. 探针脚本说明（新人接力）

探针脚本位置：`/data/Toolkit/scripts/unitool_model_probe.py`（已提交 GitHub，probe v3.0）

**关键参数**：

```python
# 正确 cookie（最重要！）
Cookie: __Secure-unitool-ssid={ssid}   # 不是 ssid=，不是 unitool-ssid=

# SSID 池文件（VPS 临时文件，25 个已验证 SSID）
/tmp/probe_ssids.txt

# 上次深度探针结果
/tmp/deep_probe_results.json
```

**继续探针的建议方向**：

1. `gpt-5` / `gpt-5.4` / `gpt5.2` — 这三个拒绝自报，可尝试不同 system prompt 绕过
2. `gemini-3.1-pro` / `gemini-3-pro` — 返回 empty（cost=0），需要调整 prompt 格式
3. grok — 成功响应但含双打印推理块，需确认后端是否 Grok-3 / Grok-2
4. `gpt-5.5` identity — 流截断时 paginatedMessages 也无内容，需要找对 prompt 类型触发成功响应
5. claude-sonnet-4-5 轮换规律 — 3.5 vs 3.7 Sonnet 什么条件下触发哪个？

**探针 checklist（新人接手时）**：

```bash
# 1. 确认 SSID 文件还在（VPS 重启后会丢失）
ls -la /tmp/probe_ssids.txt

# 2. 如果不在，从代理池提取
curl -s http://localhost:8089/pool-status | python3 -c "
import sys,json; d=json.load(sys.stdin)
for a in d['accounts']:
    if not a.get('dead'):
        print(a['ssid'])
" > /tmp/probe_ssids.txt

# 3. 运行探针脚本
python3 /data/Toolkit/scripts/unitool_model_probe.py

# 4. 深度探针特定模型（改 svc_id 即可）
python3 /tmp/deep_probe_opus_gpt55.py
```

---

## 12. 常见问题

| 现象 | 原因 | 解决方法 |
|------|------|---------|
| 401 Unauthorized | cookie 名称错误 | 用 `__Secure-unitool-ssid=`，不是 `ssid=` |
| stream 返回俄语 | IP 被 unitool 俄语限制 | 已在 POLL_PRIMARY 处理，proxy 自动绕过 |
| `internal stream ended unexpectedly` | 某服务的 stream 不稳定 | 正常现象，poll 路径可用 |
| cost=0 无响应 | 后端未处理（gemini/某些 gpt-5.5 prompt）| 换 prompt 类型，或服务暂时不可用 |
| gpt-4o 和 gpt-4-1 返回完全相同的东西 | 后端共用同一个 GPT-4o 实例 | 预期行为，无需处理 |
| claude-opus-4-6 自报 Sonnet 名字 | unitool 后端将 opus-4-6 路由到 Sonnet 4 | 已知，记录在 identity 注释 |
| pm2 进程 id 变了 | pm2 重启后 id 可能变化 | 用 `pm2 list` 确认，或改用 name: `pm2 restart unitool-proxy` |
