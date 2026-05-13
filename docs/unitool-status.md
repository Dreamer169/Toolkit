# unitool-status.md — probe v5.0 (2026-05-13)

## Backend Identity Map (probe v5.0, 2026-05-13)

### CONFIRMED Real Backends (交叉验证: unitool 探针 + Replit AI 集成直接调用对比)

| unitool service_id | real backend | confidence | 验证方法 |
|---|---|---|---|
| claude-opus-4-6 | **claude-opus-4-6** | HIGH | Replit integration model_returned=claude-opus-4-6; 回答逐字匹配 |
| claude-opus-4-7 | **claude-opus-4-7** | HIGH | Replit integration model_returned=claude-opus-4-7; 回答匹配 |
| gpt-5.5 | **GPT-5** (gpt-5-2025-08-07) | HIGH | reasoning tokens + Apr-May 2025知识 + proxy代码等价 |
| gpt-4-1 | GPT-4o (same pool as gpt-4o) | MEDIUM | v5.38 probe |
| gpt5.1 | GPT-4.1 (gpt-4.1-2025-04-14) | HIGH | v5.38 self-report |
| gpt-5.4 | gpt-5.4-2026-03-05 | HIGH | Replit direct call |

### ❌ 历史错误注释 (已修正)

| 字段 | 旧错误注释 | 修正后 | 修正依据 |
|---|---|---|---|
| claude-opus-4-6 | claude-sonnet-4-20250514 | claude-opus-4-6 (真实 Opus 4) | Replit API 直接返回 model=claude-opus-4-6 |
| gpt-5.5 | GPT-4o | GPT-5 (gpt-5-2025-08-07) | reasoning tokens + 2025知识 |

---

## gpt-5.5 = GPT-5 完整证据链 (probe v5.0)

### 用于判定的维度

| 维度 | gpt-5.5 (unitool) | gpt-5 (Replit 真实) | gpt-4o (真实) | gpt-4.1 (真实) | gpt-5.4 (真实) |
|---|---|---|---|---|---|
| model_returned | — | gpt-5-2025-08-07 | gpt-4o-2024-11-20 | gpt-4.1-2025-04-14 | gpt-5.4-2026-03-05 |
| reasoning_tokens | YES (block) | YES (300) | 0 | 0 | 0 |
| 知道 o3 Apr 2025 | TRUE ✓ | 未测 | FALSE | FALSE | FALSE |
| 知道 Claude Opus 4 May 2025 | TRUE ✓ | 未测 | FALSE | FALSE | FALSE |
| 自报 cutoff | June 2024* | 未测 | Oct 2023 | June 2024 | June 2024 |
| proxy fallback | gpt-5 ↔ gpt-5.5 | — | — | — | — |

*自报 cutoff 不可靠: gpt-5 系列模型自报常与实际训练截止不符

### 关键排除逻辑

    gpt-5.5 有 reasoning_tokens →
      排除 gpt-4o  (reasoning_tokens=0)
      排除 gpt-4.1 (reasoning_tokens=0)
      排除 gpt-5.4 (reasoning_tokens=0)
    
    gpt-5.5 知道 April-May 2025 事件 →
      排除 gpt-4o  (cutoff Oct 2023)
      排除 gpt-o4-mini/gpt-o3 on unitool (二者对相同事件答 FALSE)
    
    proxy.py 源码第 713-716 行: gpt-5 ↔ gpt-5.5 互为 fallback →
      proxy 开发者视二者为等价/同池模型
    
    真实 gpt-5-2025-08-07 有 reasoning_tokens → 与 gpt-5.5 行为一致
    
    结论: gpt-5.5 = GPT-5 (gpt-5-2025-08-07 或其 A/B 变体)

---

## claude-opus-4-6 / claude-opus-4-7 = 真实 Anthropic Opus 4 证据链

### Replit AI Integration 直接验证

    Replit Anthropic integration → POST /messages → model="claude-opus-4-6"
    Response: model_returned = "claude-opus-4-6" (真实 Anthropic API 字段)
    
    unitool claude-opus-4-6 回答:
      "My training data cutoff is early 2025 (exact month not publicly specified).
       My maximum context window is 200,000 tokens.
       Yes, I support extended thinking."
    
    Replit claude-opus-4-6 回答:
      "My training data cutoff is early 2025 (exact month not publicly specified).
       My maximum context window is 200,000 tokens.
       Yes, I support extended thinking."
    
    → 逐字相同 → unitool claude-opus-4-6 = 真实 claude-opus-4-6 直透传

### 历史错误原因

    proxy 源码注释 (v5.38) 写 "claude-sonnet-4-20250514"
    → 该注释是开发者错误标注或过时注释
    → 实际路由是真实 Opus 4 系列直接透传
    → claude-opus-4-6 和 claude-opus-4-7 都是真实 Anthropic 模型 ID

---

## Replit AI Integration 获取的真实模型版本号 (2026-05-13)

| 模型名 | Replit 返回的 model_returned | reasoning_tokens | cutoff 自报 |
|---|---|---|---|
| claude-opus-4-6 | claude-opus-4-6 | — | early 2025 |
| claude-opus-4-7 | claude-opus-4-7 | — (0 visible) | early 2025 |
| gpt-4o | gpt-4o-2024-11-20 | 0 | Oct 2023 |
| gpt-4.1 | gpt-4.1-2025-04-14 | 0 | June 2024 |
| gpt-5.4 | gpt-5.4-2026-03-05 | 0 | June 2024 |
| gpt-5 | gpt-5-2025-08-07 | 300 | — |
| o4-mini | o4-mini-2025-04-16 | 512 | — |

---

## Fix Log

- Fix-1: unitool_login.py Turnstile bypass JS hook
- Fix-2: unitool_http_register.py bypass
- Fix-3: RESI port 120s cooldown
- Fix-4: chain_v3 inline_verify scope AADSTS70011
- Fix-5: chain_v3 Graph API URL dollar-params
- Fix-6: chain_v3 HTTPError logging
- Fix-7 (probe v5.0): 修正 gpt-5.5=GPT-4o 错误注释 → GPT-5
- Fix-8 (probe v5.0): 修正 claude-opus-4-6=sonnet 错误注释 → 真实 claude-opus-4-6

---

## Current State (2026-05-13, proxy v5.39)

| Component | Status |
|---|---|
| unitool_http_register.py | v3.2 pydoll+curl_cffi OK |
| unitool_chain_v3.py | inline_verify fix OK |
| unitool_verify_rescue.py | Working OK |
| unitool-proxy | v5.39 :8089 OK |

## PM2 Process Map

| id | name | role |
|---|---|---|
| 61 | api-server | REST API :8081 |
| 69 | unitool_chain_v3 | register + inline_verify |
| 70 | unitool_verify_rescue | rescue pending |
| 75 | unitool-proxy | OpenAI proxy :8089 |

## SSID Pool (2026-05-13)

- Total: 2035 SSIDs (/data/unitool_ssids/)
- RESI ports: 10851-10859, 10870-10889

### High-balance test accounts

| email | tokens |
|---|---|
| lwhitedjs@outlook.com | 107.2 |

---

## Conversation Protocol

    POST   /api/chats                     → {id, service_id, uri, user_id}
    POST   /api/chats/{id}/messages       → {message, job:{id,status:pending}}
    GET    /api/chats/{id}/messages       → {messages:[...]} poll until status=ended
    DELETE /api/chats/{id}               → 204

## POLL_PRIMARY services (stream intercepted → HTML page)

    gpt-5.5 / gpt-5-nano / gpt-4-1 / gpt-4o / gpt-4o-mini
    claude-sonnet / claude-opus / claude-sonnet-4-6
    claude-opus-4-6 / claude-opus-4-7
    gpt-o1/o3/o4-mini series / grok
    gemini-3.1-pro / gemini-3-pro / gpt-5.4
    perplexity-sonar / perplexity-sonar-pro / perplexity-sonar-pro-search

## New Services (v5.39, 2026-05-12)

| service_id | min_bal | status |
|---|---|---|
| claude-opus-4-7 | 10.1 | POLL_PRIMARY OK |
| perplexity-sonar | 1 | under maintenance |
| perplexity-sonar-pro | 1 | under maintenance |
| perplexity-sonar-pro-search | 3 | under maintenance |
