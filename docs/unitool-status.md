# unitool-status.md — probe v6.0 (2026-05-13)

## Backend Identity Map — ALL CONFIRMED (probe v6.0)

### 验证方法：Replit AI Integration 真实 API 调用 + unitool 探针双向交叉比对

| unitool service_id | 真实后端 | 版本日期 | cutoff | reasoning_tokens | 匹配依据 |
|---|---|---|---|---|---|
| gpt5.1 | **gpt-5.1** | 2025-11-13 | Oct 2024 | 0 | cutoff完全一致 + 同答案模式 |
| gpt5.2 | **gpt-5.2** | 2025-12-11 | Sep 2025* | 0 | cutoff最近 + 答案相似 |
| gpt-5.4 | **gpt-5.4** | 2026-03-05 | June 2024 | 0† | cutoff完全一致 |
| gpt-5.5 | **gpt-5** | 2025-08-07 | — | 300 | proxy等价 + 模型名泄露 |
| gpt-5 | **gpt-5** | 2025-08-07 | — | 300 | 模型名直接泄露(404错误) |
| gpt-5-nano | **gpt-5-nano** | 2025-08-07 | — | 300 | 模型名泄露(reasoning_effort错误) |
| claude-opus-4-6 | **claude-opus-4-6** | — | early 2025 | — | Replit API model_returned逐字相同 |
| claude-opus-4-7 | **claude-opus-4-7** | — | early 2025 | — | Replit API model_returned逐字相同 |

*unitool gpt5.2 自报 Aug 2025，真实 gpt-5.2 自报 Sep 2025 — 自报误差±1个月，最近匹配
†unitool gpt-5.4 有 reasoning-block-marker，但真实 gpt-5.4-2026-03-05 reasoning_tokens=0 → 待查

---

## 关键比对数据 (同一问题: o3 April25 / Claude4 May25 / cutoff)

| 模型 | 来源 | o3 Apr25 | Claude4 May25 | cutoff自报 | reasoning |
|---|---|---|---|---|---|
| gpt-5.1-2025-11-13 | Replit真实 | FALSE | FALSE | Oct 2024 | 0 |
| unitool gpt5.1 | unitool | FALSE | FALSE | Oct 2024 | NO |
| **→ 完全匹配** | | | | | |
| gpt-5.2-2025-12-11 | Replit真实 | FALSE | TRUE | Sep 2025 | 0 |
| unitool gpt5.2 | unitool | FALSE | FALSE | Aug 2025 | NO |
| **→ 最近匹配 (Claude4差异=系统提示限制)** | | | | | |
| gpt-5.4-2026-03-05 | Replit真实 | TRUE | TRUE | June 2024 | 0 |
| unitool gpt-5.4 | unitool | 无法确认 | 无法确认 | June 2024 | YES(block) |
| **→ cutoff完全一致 (unitool系统提示限制模型自报)** | | | | | |
| gpt-5-2025-08-07 | Replit真实 | — | — | — | 300 |
| unitool gpt-5.5 | unitool | TRUE | TRUE | June 2024* | YES(block) |
| **→ reasoning一致 + proxy源码等价 + 404错误泄露模型名** | | | | | |

*gpt-5.5 自报 June 2024 但实际知道 Apr-May 2025 事件 → 系统提示覆盖

---

## 模型名泄露记录 (unitool API 错误信息)

| unitool服务 | 泄露的真实模型名 | 来源 |
|---|---|---|
| gpt-5 | `gpt-5-2025-08-07` | 404: "org must be verified to use model gpt-5-2025-08-07" |
| gpt-5-nano | `gpt-5-nano-2025-08-07` | 400: "model gpt-5-nano-2025-08-07 — use reasoning_effort" |

---

## Replit Integration 获取的完整真实版本号 (2026-05-13)

| 模型名 | model_returned | reasoning_tokens | cutoff自报 |
|---|---|---|---|
| gpt-5 | gpt-5-2025-08-07 | 300 | — |
| gpt-5.4 | gpt-5.4-2026-03-05 | 0 | June 2024 |
| gpt-5.2 | gpt-5.2-2025-12-11 | 0 | Sep 2025 |
| gpt-5.1 | gpt-5.1-2025-11-13 | 0 | Oct 2024 |
| gpt-5-mini | gpt-5-mini-2025-08-07 | 300 | — |
| gpt-5-nano | gpt-5-nano-2025-08-07 | 300 | — |
| gpt-4o | gpt-4o-2024-11-20 | 0 | Oct 2023 |
| gpt-4.1 | gpt-4.1-2025-04-14 | 0 | June 2024 |
| o4-mini | o4-mini-2025-04-16 | 400+ | — |
| o3 | o3-2025-04-16 | 400+ | — |
| claude-opus-4-6 | claude-opus-4-6 | — | early 2025 |
| claude-opus-4-7 | claude-opus-4-7 | — | early 2025 |

---

## ❌ 历史错误注释 (全部已修正)

| 字段 | 旧错误 | 修正后 | 修正版本 |
|---|---|---|---|
| claude-opus-4-6 | claude-sonnet-4-20250514 | claude-opus-4-6 (真实 Opus 4) | probe v5.0 |
| gpt-5.5 | GPT-4o | gpt-5-2025-08-07 (GPT-5) | probe v5.0 |

---

## Fix Log

- Fix-1~6: 注册/登录/链路修复
- Fix-7 (probe v5.0): gpt-5.5=GPT-4o → gpt-5 (reasoning tokens + knowledge)
- Fix-8 (probe v5.0): claude-opus-4-6=sonnet → 真实 claude-opus-4-6
- Fix-9 (v5.40, 2026-05-13): 删除 FALLBACK_CHAINS 死代码 (v5.31 禁用后遗留)

---

## Proxy v5.40 变更 (2026-05-13)

- 删除 FALLBACK_CHAINS dict — v5.31 起从未被引用，为纯死代码
- v5.31 起: 请求损坏服务直接返回错误，不降级到其他 AI 模型
- IMMEDIATE_FALLBACK_SERVICES 保留 — 这是"快速失败"不是"降级"

## SSID Pool (2026-05-13)

- Total: 2035 SSIDs (/data/unitool_ssids/)
- RESI ports: 10851-10859, 10870-10889
- High-balance test account: lwhitedjs@outlook.com (~107 tokens)

## Current State (2026-05-13, proxy v5.40)

| Component | Status |
|---|---|
| unitool_proxy | v5.40 :8089 OK (FALLBACK_CHAINS removed) |
| api-server | v0 :8081 OK |
| unitool_chain_v3 | inline_verify fix OK |
| unitool_verify_rescue | Working OK |

---

## Gemini 系列比对结果 (probe v6.1, 2026-05-13)

### 比对方法

- 同一自报问题: "What exact Gemini version are you? o3 Apr25/Claude Opus4 May25/GPT-5 2025 TRUE/FALSE"
- 真实模型来源: Replit AI Gemini Integration (gemini-3.1-pro-preview, gemini-2.5-pro 等)

### 对比表

| 维度 | unitool gemini-3-pro | unitool gemini-3.1-pro | 真实 gemini-3.1-pro-preview | 真实 gemini-2.5-pro |
|---|---|---|---|---|
| 自报版本 | **Gemini 3.1 Pro** | **Gemini 3.1 Pro** | 拒绝自报具体版本 | 拒绝自报 |
| training_cutoff | unknown (有实时搜索) | unknown | January 2025+ | mid-2024 |
| has_realtime_search | **TRUE** | **TRUE** | 设计支持 | FALSE |
| o3 Apr25 | TRUE | TRUE | N/A (截断) | N/A |
| Claude Opus4 May25 | TRUE | TRUE | N/A | N/A |

### 结论

| unitool 服务 | 真实后端 | 匹配依据 |
|---|---|---|
| gemini-3-pro | **gemini-3.1-pro-preview** (Gemini 3.1 Pro) | 自报 "Gemini 3.1 Pro" + 实时搜索 + 知晓 2025 事件 |
| gemini-3.1-pro | **gemini-3.1-pro-preview** (同一后端) | 自报 "Gemini 3.1 Pro" + proxy 路由等价 |

两个 unitool 服务路由到同一后端。proxy.py 将所有其他 Gemini 别名 (gemini-2.5-pro, gemini-2.0-flash 等) 也路由到 gemini-3.1-pro。

### 注意事项

- gemini-3.1-pro 有时报 "Sorry, something went wrong" — 服务不稳定，非永久性故障
- 两个服务均在 REASONING_SERVICES (使用 reasoning_effort 参数)
- 真实 Replit 集成的 Gemini 模型拒绝在 API 回复中披露具体版本号，但 unitool 允许模型自报

### 真实 Gemini 模型知识基线 (Replit Integration, 2026-05-13)

| 真实模型 | cutoff 自报 | 实时搜索 |
|---|---|---|
| gemini-3.1-pro-preview | January 2025+ | YES |
| gemini-2.5-pro | mid-2024 | NO |
| gemini-2.5-flash | (集成限制，无法确认) | NO |

