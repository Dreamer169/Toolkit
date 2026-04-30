# obvious.ai exploration — findings (REVISED)

**Date:** 2026-04-30
**Status:** ⚠️ Prior version (commit `420d8d9c`) was wrong — said "dead end".
              That dismissed the agent's tool surface. obvious.ai is in fact a
              **production-usable e2b sandbox + LLM agent**, drivable headless via
              cookie auth. See below.

## TL;DR

obvious.ai gives every account a **persistent e2b VM** plus an agent that issues
shell / python / playwright tool calls inside it. Free tier = 25 credits/account,
no credit card. We can drive it from any host with `scripts/obvious_client.py`
(no browser, ~1 s round-trip to issue a shell command).

## Sandbox specs (verified)

| Property | Value |
|---|---|
| OS | Debian GNU/Linux 13 (trixie), kernel 6.1.158 |
| CPU | 2 vCPU |
| RAM | 8 GB (≈7.9 free at boot) |
| Disk | 26 GB (24% used at first boot) |
| Hostname | `e2b.local` |
| Python | 3.13.12 |
| Playwright | 1.59.0 (chromium installable on demand) |
| Working dir | `/home/user/work` (persists across messages in same thread) |
| sandboxId | stable per thread (e.g. `iwn9r8g0s4p2vlkpoan1e`) |

## Available agent modes

`GET /prepare/modes`:

| id | name | model |
|---|---|---|
| `auto` | Auto | balanced default |
| `fast` | Fast | Haiku 4.5, 1M context |
| `deep` | Deep Work | GPT-5.4 advanced reasoning |
| `analyst` | Analyst | GPT-5.4 quantitative |
| `skill-builder` | Skill Builder | for custom skills |

## API surface (all under `https://api.app.obvious.ai/prepare/`)

Cookie auth via `__Secure-better-auth.session_token` (on `api.app.obvious.ai`)
plus `obvious_www_session` (on `.obvious.ai`). No CSRF token required.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v2/agent/chat/{threadId}` | send user message, returns `executionId` |
| GET | `/threads/{threadId}/messages` | full message + tool-call history |
| GET | `/hydrate/project/{projectId}` | project state, includes `agentStatus` |
| GET | `/modes` | list of agent modes |
| GET | `/workspaces/{wks}/billing/status` | tier + payment status |
| GET | `/skills/mentionable` | available `@`-mentionable skills |
| GET | `/user/event-stream` | SSE for live updates (we currently poll instead) |

### POST chat body

```json
{
  "message": "uname -a",
  "messageId": "<uuid>",
  "projectId": "prj_xxx",
  "fileIds": [],
  "modeId": "auto",
  "timezone": "UTC"
}
→ 200 {"message":"Agent started","action":"started","executionId":"exec_xxx"}
```

### Tool result shape

Every `run-shell` tool emits a `tool-result` with:
```json
{
  "type":"json",
  "value":{"data":{
    "cwd":"/home/user/work",
    "stdout":"...","stderr":"","exitCode":0,
    "sandboxId":"iwn9r8g0s4p2vlkpoan1e",
    "durationMs":1311
  }}
}
```

## Headless client

`scripts/obvious_client.py` ships with this commit. Smoke-test:

```bash
python3 scripts/obvious_client.py \
    --cookies /root/obvious_state.json \
    --thread  th_2UNzFlj1 \
    --project prj_Hd6ka1l3 \
    "date -u && df -h /home/user | tail -1 && nproc && free -m | head -2"
```

Response time ≈ 13 s end-to-end for a trivial 4-command shell job (poll
interval default 3 s; raise/lower via constructor).

## Where this fits in Toolkit

Concrete uses:

1. **Off-host Playwright** — burst scraping / verification jobs without
   spinning up the local chrome on the VPS.
2. **Code generation + execution** — let the deep mode write a script and
   actually run it before returning a verified output.
3. **Doc/data artifacts** — quick CSV/markdown/SQL reports outside Toolkit's
   stack.

Hard limits:

- 25 credits / free account / month — we burn one credit per turn (≈ one
  multi-tool agent run). Plan for ≤ ~25 small jobs per account.
- Cookie sessions expire (better-auth default ~30 days). Re-extract via the
  Playwright signup flow when stale.
- ToS likely forbids high-volume automated reuse — keep it to occasional
  on-demand probes, not a workhorse.

## Cookie extraction recipe

The client reads any Playwright `storage_state` JSON. To refresh:

```python
# inside an authenticated Playwright session:
state = await ctx.storage_state()
json.dump(state, open('/root/obvious_state.json', 'w'))
```

The current state file lives on the VPS at `/root/obvious_state.json`
(account `michael_robinson85@outlook.com` / user `usr_3ykd45K8` /
workspace `wks_vEwwoc47` / project `prj_Hd6ka1l3` / thread `th_2UNzFlj1`).
