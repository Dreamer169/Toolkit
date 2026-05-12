# unitool-status.md

## Fix Log

### Fix-1: unitool_login.py – Managed Turnstile phase2 bypass
Replaced unmaintained selector with JS  hook to receive CF token.

### Fix-2: unitool_http_register.py – Same managed bypass
Ported identical 3-phase bypass (natural wait → click loop → reload) to registration script.

### Fix-3: RESI port cooldown
Added 120-second per-port cooldown in  to avoid RESI provider rate-limits.

### Fix-4: chain_v3.py – inline_verify scope AADSTS70011
Removed  from  token request.
CLIENT_ID 9e5f94bc has no IMAP permission; graph-only scope
`https://graph.microsoft.com/Mail.Read offline_access` is correct.

### Fix-5: chain_v3.py – inline_verify Graph API URL $-params
Fixed URL from `?={_filter}&=10&=subject,...`
to `?$filter={_filter}&$top=10&$select=subject,body,receivedDateTime`
so message list query actually returns results.

### Fix-6: chain_v3.py – inline_verify better HTTPError logging
Added `urllib.error.HTTPError` catch before generic Exception to log
HTTP status code and response body (first 200 chars) for future debugging.

---

## Current State (as of commit 68974e81584a)

| Component | Status |
|---|---|
| unitool_http_register.py | v5.0 bypass ✓ |
| unitool_login.py | v5.0 bypass ✓ |
| unitool_chain_v3.py | inline_verify fix ✓ (scope+URL+logging) |
| unitool_verify_rescue.py | Working ✓ (graph-only scope was already correct) |

## PM2 Process Map
| id | name | role |
|---|---|---|
| 61 | api-server | REST API |
| 69 | unitool_chain_v3 | register loop + inline_verify |
| 70 | unitool_verify_rescue | rescue pending verifications |
| 75 | unitool-proxy | OpenAI-compatible proxy |

## Account flow
Outlook accounts (platform=outlook, status=active, has refresh_token+password)
→ chain_v3 registers on unitool.ai (Turnstile bypass v5.0)
→ inline_verify reads confirmation email via Graph API (graph-only scope)
→ SSID added to proxy pool (pool ~1400+)
→ verify_rescue picks up any verify_pending stragglers
