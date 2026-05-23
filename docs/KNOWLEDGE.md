# aimly — Project Knowledge

## What aimly is
- Multi-tenant SaaS for Telegram outreach automation via personal accounts plus AI auto-responder
- Each workspace owns: TG accounts (senders), contacts, agents (AI templates), campaigns, inbox, analytics
- v1 goal: first paying external customer connects accounts and launches a campaign without a developer
- Core Value: under 10 minutes from signup to first launched campaign (`time_to_first_campaign_seconds < 600`)

## User persona
- B2B SaaS founders and SDR teams running Telegram outreach
- Not technical — paste CSV, pick agent, launch
- 1-5 Telegram accounts to send from
- English UI; leads may speak any language (agent mirrors via mirror_language flag)

## Key entities
- Workspace — tenant boundary; everything scoped by workspace_id
- Sender — connected Telegram account; rate limits 4/min, 20/hour, 150/day (NOT configurable in v1)
- Agent — reusable AI template (prompt, voice, FAQ, tone, safety triggers) — workspace-level
- Campaign — wrapper around agent + folder + senders + schedule + signals + webhook + tools
- Contact — phone-based; lives in a Folder; can be Telegram-verified via checker account
- Conversation — generated when contact replies; status active/manual/lead/handoff/finished/bot_ignored; `ai_enabled` toggle
- LLM call — every chat.completions request is logged for the inbox Thought Trace pane

## Architecture
- Python 3.11 / FastAPI / SQLAlchemy 2.0 async / PostgreSQL 16 (single VPS)
- Telethon for Telegram MTProto; OpenAI gpt-4o-mini for AI
- Docker Compose: db + api + listener (separate container for inbound listener)
- This Lovable frontend: React 18 + Vite + Tailwind + shadcn/ui + TanStack Query + react-hook-form + zod
- Auth: Supabase magic link → JWT bearer → backend HS256 verify (Pitfall 3: pin Supabase to HS256)

## Design system source of truth
- `design-source/project/styles.css` — every CSS var (color, spacing, radius, shadow, motion)
- `design-source/project/screens/*.jsx` — JSX reference for each of the 11 screens
- `design-source/project/uploads/*.png` — prototype mockups
- Brand: aimly (lowercase, no exclamation marks); Geist + Geist Mono fonts
- Primary `--tg-blue #3390ec`; AI accent `--ai-purple #8774e1` (AI moments only)

## Telemetry (full list in telemetry-events.md)
- 17 events total
- Every event_id is a client-supplied UUID for idempotency (backend uses ON CONFLICT DO NOTHING)
- POST /api/v1/telemetry/events with whitelisted event_name; non-whitelisted = 400 `{code: UNKNOWN_EVENT}`
- Core Value KPI: `time_to_first_campaign_seconds = MIN(campaign_launched.server_ts) - MIN(signup_completed.server_ts)` per workspace

## What we do NOT do (v1)
- Multi-user workspaces (v2)
- Dark theme (v2)
- Mobile-native app (v2)
- Real-time websocket (10s poll is enough)
- HMAC-signed webhooks (v2)
- Multi-step follow-up sequences (v2)
- A/B testing message variants (v2)
- CSV analytics export (v2)
- GDPR data export portal (v1.1 — mailto placeholder for now)

## Hard rules (NEVER break)
- Never log API keys, JWT tokens, or full LLM prompts (security — see Phase 5 prompt-leak guard)
- Never lower the 4/20/150 rate limits in UI (Pitfall 9)
- Never invent backend response shapes — use `types/api.ts` (Pitfall 8)
- Sessions encrypted at rest — UI never sees decrypted session strings
- `--ai-purple` is reserved for AI moments only
