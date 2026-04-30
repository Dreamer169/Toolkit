# obvious.ai exploration — findings

**Date:** 2026-04-30
**Tester:** michael_robinson85@outlook.com (throwaway, NOT linked to Tailscale account)
**Goal:** Determine whether obvious.ai exposes any usable backend that Toolkit could
piggyback on for proxying or Replit-style code generation.

## TL;DR — **Not useful for Toolkit's goals.**

obvious.ai is a **document/data agent SaaS** (Notion + AI), not a code-generation
or proxy platform. Its agent emits Docs / Tables / Slides / Boards / Calendars /
Timelines / Checklists / Galleries / Images — there is no "build me a Replit app"
or webcontainer-style code execution surface.

## Account model

- Email + password signup, **no email verification required**
- Lands on `/onboarding` — two text inputs ("Where do you work?" / "What do you do?"),
  Google "Continue with" SSO also offered
- New accounts get **25 free credits** (modal blocks `/landing` until dismissed)
- Session persists via `d0_session` cookie (HttpOnly, on `app.obvious.ai`)
- Auth profile cached in localStorage `auth-user` (`usr_3ykd45K8`)

## Discovered API surface

Everything is fronted at `https://api.app.obvious.ai/prepare/api/v2/...`. The only
endpoint actually exercised in this probe was the chat POST:

```
POST https://api.app.obvious.ai/prepare/api/v2/agent/chat/{threadId}
Content-Type: application/json

{
  "message": "...",
  "messageId": "<uuid>",
  "projectId": "prj_xxx",
  "fileIds": [],
  "modeId": "auto",
  "timezone": "UTC"
}

→ 200 {"message":"Agent started","action":"started","executionId":"exec_xxx"}
```

Streaming output is then delivered via a separate channel (likely SSE/WS, not
captured in this run). Auth is **cookie-based** (`d0_session`), not Bearer.

## Sample agent output

Prompt: "List 3 specific outputs you can produce for me — one short sentence each."

> 1. **A technical spec or RFC document** — structured, clear, ready to share with your team.
> 2. **A data workbook** — ingest a CSV or dataset and turn it into analyzed, queryable sheets.
> 3. **A research brief** — pull current info on any topic (libraries, tools, competitors) into a clean summary.

## Why this is a dead end for Toolkit

| Toolkit goal | obvious.ai fit |
|---|---|
| Replit-style code generation | ❌ no code-exec, no app-build agent |
| Proxy / agent-mode bridge | ❌ closed SaaS, cookie-auth, no public API |
| Cheap LLM passthrough | ❌ 25 credits/account, no documented per-call pricing |
| Document/spreadsheet generation | ✅ but unrelated to Toolkit's purpose |

If we ever needed an external doc-gen oracle, the chat POST above is reproducible
with the saved cookie jar (`/root/obvious_state.json` on VPS), but credit
exhaustion would be near-instant under any real workload, and the ToS likely
forbids automated reuse.

## Recommendation

**Stop the obvious.ai exploration.** Refocus on Toolkit's first-party stack
(api-server + browser-model + Tailscale Funnel) and whatever upstream oracle is
already wired in.
