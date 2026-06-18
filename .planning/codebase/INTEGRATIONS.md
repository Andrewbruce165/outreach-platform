# External Integrations

**Analysis Date:** 2026-06-18

---

## Backend Integrations

### Telegram MTProto API

**What:** Telegram's native MTProto protocol for sending/receiving messages via user accounts (not bots).

**SDK:** telethon 1.42.0 + PySocks 1.7.1

**Auth:** `TELEGRAM_API_ID` + `TELEGRAM_API_HASH` env vars (from `my.telegram.org`)

**Usage locations:**
- `/root/apps/aimly/tg-outreach/app/services/telegram.py` — core send/receive, entity cache warm-up via `get_dialogs(limit=200)` on cold start
- `/root/apps/aimly/tg-outreach/app/services/listener.py` — incoming message listener; runs in `outreach-platform-listener` container; reconcile loop (`_reconcile_loop`) restarts disconnected clients
- `/root/apps/aimly/tg-outreach/app/services/checker.py` — bulk phone-number Telegram registration check via dedicated `checker` sender accounts
- `/root/apps/aimly/tg-outreach/app/services/warmup.py` — AI-to-AI warmup conversations between pool accounts
- `/root/apps/aimly/tg-outreach/app/routers/onboarding.py` — account onboarding (phone/SMS/2FA/QR login); QR endpoints: `POST /api/v1/onboarding/qr-start`, `GET /api/v1/onboarding/qr-status/{session_id}`

**Session storage:** Telethon session strings are encrypted via Fernet (`/root/apps/aimly/tg-outreach/app/services/encryption.py`) using `ENCRYPTION_KEY` and stored in the `senders.session_string` column.

**Proxy:** Optional SOCKS5 proxy per sender; pool managed via `proxy_pool` table and Decodo ISP (see below).

**Rate limits (empirically tuned — do NOT change without discussion):**
- 4 messages/min, 20/hour, 150/day per sender
- Stored per-row in `senders.rate_per_min/rate_per_hour/rate_per_day`

**Cold start entity cache:** On `ValueError: Could not find the input entity for PeerUser`, the service calls `get_dialogs(limit=200)` to warm the cache, then retries.

---

### OpenAI API

**What:** LLM responses for AI auto-reply in conversations; account warmup conversations; audio transcription.

**SDK:** openai 1.40.0+ (`AsyncOpenAI`)

**Auth:** `OPENAI_API_KEY` env var

**Model:** configurable via `OPENAI_MODEL` env (default: `gpt-5-mini-2025-08-07`)

**Usage locations:**
- `/root/apps/aimly/tg-outreach/app/services/ai_engine.py` — primary: `generate_response()` with function calling (3 built-in tools: `mark_as_lead`, `transfer_to_manager`, `finish_conversation`; custom tools from `campaigns.tools` JSONB); secondary: OpenAI Whisper for audio transcription via `httpx`
- `/root/apps/aimly/tg-outreach/app/services/warmup.py` — generates warmup dialogue between accounts

**Function calling:** AI engine supports two tool classes:
1. Built-in signals (`mark_as_lead`, `transfer_to_manager`, `finish_conversation`) — fire campaign webhooks + update conversation status
2. Custom tools from `campaigns.tools` JSONB — execute external webhooks and return results to the model

**Audit log:** Every `chat.completions.create()` call from `ai_engine` is logged to the `llm_calls` table (model, prompt, response, token counts, latency, error). NOT logged: warmup calls.

---

### Supabase (Auth Provider)

**What:** JWT issuance and user identity for the Lovable frontend. The backend verifies tokens; it does NOT call Supabase APIs beyond the JWKS endpoint.

**Auth verification path (`/root/apps/aimly/tg-outreach/app/utils/auth.py`):**
- Primary: ES256 — verifies against JWKS from `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`; keys cached per-process for 1 hour; `kid` miss triggers one refetch for key rotation
- Fallback: HS256 — uses `SUPABASE_JWT_SECRET` (optional env var); only for projects pinned to symmetric signing

**Required env vars (backend):**
- `SUPABASE_URL` — used to derive JWKS endpoint
- `SUPABASE_JWT_SECRET` — optional; HS256 fallback only

**Dual-auth system:** All protected endpoints use `AuthDep` in `/root/apps/aimly/tg-outreach/app/utils/auth.py`:
1. `Authorization: Bearer <Supabase JWT>` — UI path (Lovable frontend, source=`jwt`)
2. `X-Workspace-Key: wsk_<random>` — integration path (n8n, external; source=`api_key`)

Both resolve to `AuthCtx(workspace_id, user_id, source, role)`.

**Lazy workspace creation:** Valid JWT + no `user_workspaces` row → atomically creates Workspace + UserWorkspace in one transaction (D-08 TENT-02 pattern, `app/utils/auth.py`).

**Workspace API key:** bcrypt-hashed `wsk_*` keys in `workspace_api_keys` table; issued via `POST /api/v1/workspace/api-keys` (JWT-only endpoint).

---

### Decodo ISP Proxy Pool

**What:** Residential static proxy endpoints to assign per-sender Telegram accounts (prevents IP-level bans).

**Protocol:** SOCKS5 via PySocks

**Auth:** `DECODO_HOST`, `DECODO_USERNAME`, `DECODO_PASSWORD`, `DECODO_PORTS` env vars

**Storage:** `/root/apps/aimly/tg-outreach/app/models/__init__.py::ProxyPool` table — each row = one proxy endpoint (host + port = unique static IP); `assigned_to_sender_id` NULL = free

**Management:** `/root/apps/aimly/tg-outreach/app/routers/proxy_pool.py`

**Optional:** All Decodo env vars are optional; proxy is per-sender JSONB field on `senders` table.

---

## Data Storage

### PostgreSQL 16

**Container:** `outreach-platform-db`
**Database:** `outreach_platform`
**User:** `outreach_user`
**Connection:** `postgresql+asyncpg://outreach_user:...@db:5432/outreach_platform`

**Schema (key tables):**
- `workspaces`, `user_workspaces`, `workspace_api_keys` — multi-tenant foundation
- `senders` — Telegram accounts (lifecycle: active/warmup/paused; auth: ok/session_expired/etc.)
- `message_queue` — outbound message queue (rate-limited, processed by `queue.py` worker)
- `messages_log` — sent message audit log
- `contacts_cache` — per-sender Telegram entity cache (telegram_id + access_hash)
- `contacts`, `folders` — workspace address book
- `ai_contexts` — AI agent configs (prompt, tone, rules, FAQ, pause triggers)
- `campaigns`, `campaign_senders`, `campaign_contact_assignments` — campaign management + sender rotation
- `conversations` — per-contact dialogue state (ai_enabled, status, campaign link)
- `warmup_pool`, `warmup_sessions`, `warmup_messages` — account warmup system
- `proxy_pool` — Decodo proxy assignments
- `onboarding_sessions` — persistent Telegram onboarding state
- `csv_imports` — CSV preview blobs (temporary, with `expires_at`)
- `llm_calls` — OpenAI call audit log (Phase 5)
- `telemetry_events` — frontend UI event ingest
- `schema_migrations` — migration applier tracking table

**Migration system:** `/root/apps/aimly/tg-outreach/migrations/*.sql` — 26 numbered migrations + bootstrap `_schema_migrations.sql`; auto-applied on API startup via `pg_advisory_lock`; fail-fast if migration fails (API won't start)

**Backup:** `/root/apps/aimly/tg-outreach/backup.sh` — `pg_dump --clean --if-exists | gzip` to `/root/backups/tg-outreach/`

---

## Outgoing Webhooks (Backend → External)

### Campaign Signal Webhooks

**What:** Fire-and-forget HTTP POST callbacks when AI signals a lead, handoff, or conversation finish.

**Implementation:** `/root/apps/aimly/tg-outreach/app/services/webhook_notify.py` — `asyncio.create_task` (non-blocking)

**Trigger:** AI function calls to `mark_as_lead`, `transfer_to_manager`, `finish_conversation` in `ai_engine.py`

**Configured per-campaign:**
- `campaigns.lead_webhook_url` — fires on `mark_as_lead`
- `campaigns.handoff_webhook_url` — fires on `transfer_to_manager`
- `campaigns.finish_webhook_url` — fires on `finish_conversation`
- `campaigns.webhook_url` — universal fallback (UI-only field populated by Lovable)

**Failure handling:** Webhook errors never propagate to the originating conversation flow; logged only.

### Queue Item Callbacks

**What:** Per-queue-item HTTP POST callback after send (success or failure).

**Field:** `message_queue.callback_url` (nullable) — supplied at enqueue time.

**Use case:** n8n workflows can supply a callback URL when pushing messages via the workspace API key (`POST /api/v1/send`).

---

## Frontend → Backend Integration

### API Communication

**Base URL:** `VITE_BACKEND_URL` env var (production: `https://aimly.agsventurelab.com`)

**API client:** `/root/apps/aimly/aimly-tg-outreach/src/lib/api.ts` — thin `fetch` wrapper with:
- Automatic `Authorization: Bearer <token>` injection (waits up to 2s for Supabase session, polling 100ms)
- JSON body serialization + FormData passthrough
- Structured error parsing: backend envelope `{detail: {code, message}}` → `ApiError(status, code, message)`
- `TOKEN_EXPIRED` 401 → dispatches `aimly:auth-expired` custom event (handled in route layer for redirect to `/login`)

**Type safety:** All request/response types imported from `/root/apps/aimly/aimly-tg-outreach/src/types/api.ts` — generated from backend OpenAPI via `openapi-typescript`. Never invent types; if type is missing, flag the gap.

**OpenAPI handoff:** Backend OpenAPI spec is checked into `/root/apps/aimly/tg-outreach/lovable-handoff/openapi.json` and mirrored to `/root/apps/aimly/aimly-tg-outreach/docs/openapi.json`. Lovable regenerates frontend types from this.

**Known Lovable quirks (from `CLAUDE.md`):**
- `POST /conversations/{id}/send` — Lovable sends `{"message_text": "..."}` instead of `{"message": "..."}`. Backend `SendMessageFromUIRequest` schema accepts both via `AliasChoices("message", "message_text")`.
- `POST /telemetry/events` — unknown event names return 400 `UNKNOWN_EVENT`. Whitelist in `/root/apps/aimly/tg-outreach/app/routers/telemetry.py::_EVENT_WHITELIST` (17 events). Update whitelist when frontend adds a new event.

**CORS:**
- Explicit allowlist: `CORS_ALLOWED_ORIGINS` env var
- Regex for Lovable preview deployments: `CORS_ALLOWED_ORIGIN_REGEX` (accepts `*.lovableproject.com` and `*.lovable.app`)
- CORS headers manually added to 4xx/5xx error responses via custom exception handlers (`app/main.py`) — prevents browser masking real errors as CORS errors

**Request headers the frontend sends:**
- `Authorization: Bearer <Supabase JWT>` — all authenticated endpoints
- `Content-Type: application/json` — JSON bodies
- `Content-Type: multipart/form-data` — CSV upload

**Auth headers the backend also accepts:**
- `X-Workspace-Key: wsk_<key>` — for n8n/integration paths (not used by the Lovable UI)

### Auth Flow (Frontend)

**Provider:** Supabase (`/root/apps/aimly/aimly-tg-outreach/src/lib/supabase.ts`)

**Flow:** PKCE magic-link; `persistSession: true`, `autoRefreshToken: true`

**Session handling:**
- Supabase JWT stored in browser localStorage
- `src/lib/api.ts::getAccessToken()` retrieves via `supabase.auth.getSession()`
- Token passed as `Authorization: Bearer` to backend
- Backend verifies ES256 via JWKS, maps `sub` claim → `supabase_user_id` → workspace

**Routes:**
- `/login` — magic link request form
- `/auth/callback` — PKCE callback handler (`/root/apps/aimly/aimly-tg-outreach/src/routes/auth.callback.tsx`)
- `_authenticated` layout (`/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated.tsx`) — guards all authenticated routes

### Telemetry

**What:** Frontend UI event ingest to backend for product analytics (KPI: "time to first campaign").

**Implementation:** `/root/apps/aimly/aimly-tg-outreach/src/lib/telemetry.ts`

**Endpoint:** `POST /api/v1/telemetry/events` (backend: `/root/apps/aimly/tg-outreach/app/routers/telemetry.py`)

**Mechanism:** 1.5s batched flush via `fetch`; on `pagehide` uses `navigator.sendBeacon` for reliability (Pitfall 4 in AGENTS.md). Telemetry errors are silently swallowed — never break the UI.

**Whitelist:** 17 event types in backend (`_EVENT_WHITELIST`) + 24 types in frontend `TelemetryEvent` union (slight divergence — some duplicates in frontend type definition).

### Data Fetching Conventions

**Tool:** TanStack Query (`@tanstack/react-query`)

**Poll intervals (where applicable):**
- Inbox conversation list: 10s
- Inbox thread + thought trace: 15s
- Messages in thread: 10s
- Dashboard: 30s

**Endpoint path reconciliation:** Canonical paths are in `/root/apps/aimly/aimly-tg-outreach/docs/reconciliation.md`. Key deltas vs UI-SPEC:
- Onboarding QR: `POST /api/v1/onboarding/qr-start` (not `/qr`)
- Contacts CSV: `POST /api/v1/contacts/import/preview` then `POST /api/v1/contacts/import`
- Contact recheck: `POST /api/v1/contacts/recheck`
- Senders use `{slug}` path param (not `{id}`)
- Folder contacts: `GET /api/v1/contacts?folder_id={id}`

---

## CI/CD & Deployment

**Hosting (backend):**
- VPS DigitalOcean (134.209.239.97); manual deploy via `git pull + docker compose up -d --build`
- GitHub repo: `git@github.com:Andrewbruce165/outreach-platform.git`

**Hosting (frontend):**
- Cloudflare Workers (deployed via `wrangler`)

**CI Pipeline:** None configured (manual deploy only)

**TLS:** Let's Encrypt via `certbot certonly --webroot`; renewal via `certbot.timer`

---

## Environment Variables Summary

**Backend (`.env` / Docker Compose `environment:`):**

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `TELEGRAM_API_ID` | Yes | Telegram MTProto app ID |
| `TELEGRAM_API_HASH` | Yes | Telegram MTProto app hash |
| `ENCRYPTION_KEY` | Yes | Fernet key for session encryption |
| `OPENAI_API_KEY` | Yes | OpenAI API access |
| `SUPABASE_URL` | Yes | Supabase project URL (JWKS) |
| `SUPABASE_JWT_SECRET` | No | HS256 JWT fallback secret |
| `CORS_ALLOWED_ORIGINS` | No | Comma-separated origin allowlist |
| `CORS_ALLOWED_ORIGIN_REGEX` | No | Regex for Lovable preview origins |
| `DECODO_HOST` | No | Decodo proxy host |
| `DECODO_USERNAME` | No | Decodo proxy username |
| `DECODO_PASSWORD` | No | Decodo proxy password |
| `DECODO_PORTS` | No | Comma-separated Decodo ports |
| `OPENAI_MODEL` | No | Override LLM model |
| `CAMPAIGN_ENQUEUE_TICK_SECONDS` | No | Worker tick interval (default: 30) |
| `CAMPAIGN_ENQUEUE_BATCH_SIZE` | No | Contacts per tick (default: 500) |

**Frontend (`.env` in `/root/apps/aimly/aimly-tg-outreach/`):**

| Variable | Required | Purpose |
|---|---|---|
| `VITE_BACKEND_URL` | Yes | Backend API base URL |
| `VITE_SUPABASE_URL` | Yes | Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Yes | Supabase anon key |

---

*Integration audit: 2026-06-18*
