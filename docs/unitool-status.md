# unitool-status.md

## Fix Log

- Fix-1: unitool_login.py Turnstile bypass JS hook
- Fix-2: unitool_http_register.py bypass
- Fix-3: RESI port 120s cooldown
- Fix-4: chain_v3 inline_verify scope AADSTS70011
- Fix-5: chain_v3 Graph API URL dollar-params
- Fix-6: chain_v3 HTTPError logging

---

## Current State (2026-05-12, proxy v5.39)

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

## SSID Pool (2026-05-12)

- Total: 2035 SSIDs (/data/unitool_ssids/)
- RESI ports: 10851-10859, 10870-10889

### High-balance accounts (ref_code used by others -> reward tokens)

| email | tokens | own_ref | used_by |
|---|---|---|---|
| robertcruz806@outlook.com | 110 | kZno0 | 8 |
| lwhitedjs@outlook.com | 107.2 | xjfMd | 5 |
| lauranct242@outlook.com | 106.5 | 2KQ4m | 9 |
| sarahrivera639@outlook.com | 106.4 | xjfjk | 42 |
| l_walker296 | 106.1 | 5n3ik | 5 |

Test account: lwhitedjs@outlook.com (107 bonus tokens, expires 2026-05-08, user_id=3008933)

---

## Deep Probe Results (probe v4.0, 2026-05-12, account: lwhitedjs@outlook.com)

### Real Backend Mapping (CONFIRMED)

| unitool service_id | real backend | confirmed by | context | cutoff | ext-think | cost/msg |
|---|---|---|---|---|---|---|
| claude-opus-4-6 | claude-sonnet-4-20250514 | src+probe | 200k | early 2025 | YES | 128-218 |
| claude-opus-4-7 | unconfirmed (suspected larger Sonnet/Opus variant) | behavior | 200k | early 2025 | untested | 166-366 |
| gpt-5.5 | GPT-4o | cutoff match | 128k | June 2024 | NO | 103-423 |
| gpt-4-1 | GPT-4o (same backend as gpt-4o) | v5.38 probe | 128k | - | NO | - |
| gpt5.1 | GPT-4.1 | v5.38 self-report | 1M | Jan 2025 | NO | - |
| claude-sonnet-4-6 | Claude 3.5/3.7 Sonnet (rotates) | v5.38 probe | 200k | - | - | - |

### GPT-4o vs GPT-4.1 determination

    gpt-5.5 self-reported cutoff = June 2024
    GPT-4o  cutoff = June 2024  -> MATCH -> gpt-5.5 = GPT-4o
    GPT-4.1 cutoff = Jan  2025  -> NO MATCH (eliminated)

### claude-opus-4-6 = claude-sonnet-4-20250514 confirmation chain

    1. proxy source comment v5.38 explicitly states "claude-sonnet-4-20250514 (!!!)"
    2. probe: 200k context (Sonnet 4 spec confirmed)
    3. probe: extended thinking = YES (Sonnet 4 feature)
    4. identity probe: model indirectly acknowledged "20250514" date as plausible
    5. cost 128-218/msg (Sonnet 4 pricing, well below real Opus 4)

### Conversation protocol (tested)

    POST   /api/chats                -> {id, service_id, uri, user_id}
    POST   /api/chats/{id}/messages  -> {message:{id,role,status}, job:{id,status}}
    GET    /api/chats/{id}/messages  -> {messages:[...]} poll until status=ended
    DELETE /api/chats/{id}           -> 204

### SSE stream format (proxy endpoint)

    data: {"id":"chatcmpl-xxx","model":"gpt-5.5","choices":[{"delta":{"content":"..."},"finish_reason":null}]}
    data: {"id":"chatcmpl-xxx","choices":[{"delta":{},"finish_reason":"stop"}]}
    data: [DONE]

### PoW / Turnstile

    unitool has NO PoW endpoint (/api/pow etc all return HTTP 000)
    Registration protected by Cloudflare Turnstile (site-key in /en/entry page)
    Bypass: pydoll headless Chromium waits for shadow-root token

### POLL_PRIMARY services (widget/stream intercepted, must use poll)

    gpt-5.5 / gpt-5-nano / gpt-4-1 / gpt-4o / gpt-4o-mini
    claude-sonnet / claude-opus / claude-sonnet-4-6
    claude-opus-4-6 / claude-opus-4-7
    grok / gpt-o1/o3/o4-mini series
    gemini-3.1-pro / gemini-3-pro / gpt-5.4
    perplexity-sonar / perplexity-sonar-pro / perplexity-sonar-pro-search

### Stream OK services (widget/stream works)

    gpt-5 / gpt5.1 / gpt5.2 / gpt-o3-mini / gpt-o3 / gpt-o4-mini / claude-sonnet-4-5

---

## API Endpoints (v5.39)

| Endpoint | Status |
|---|---|
| GET /api/user | OK (requires Cookie header, not -b flag) |
| GET /api/user/billing-accounts | OK JSON |
| GET /api/services | OK full list |
| POST /api/chats | OK |
| POST /api/chats/{id}/messages | OK |
| GET /api/chats/{id}/messages | OK poll |
| DELETE /api/chats/{id} | OK |
| GET /api/chats/{id}/widget/stream | INTERCEPTED for POLL_PRIMARY |
| GET /api/chats/{id}/paginatedMessages | 404 removed |
| GET /api/pow | HTTP 000 does not exist |

## New Services (v5.39, 2026-05-12)

| service_id | min_bal | status |
|---|---|---|
| claude-opus-4-7 | 10.1 | POLL_PRIMARY OK |
| perplexity-sonar | 1 | under maintenance |
| perplexity-sonar-pro | 1 | under maintenance |
| perplexity-sonar-pro-search | 3 | under maintenance |
