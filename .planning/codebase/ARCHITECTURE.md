# Architecture

**Analysis Date:** 2026-06-18

## Pattern Overview

**Overall:** Multi-tenant SaaS with two separate repositories — a Python async backend and a TypeScript SSR frontend. Backend uses a layered FastAPI + worker-loop pattern; frontend uses file-based routing with server-side rendering via TanStack Start (Cloudflare Workers compatible).

**Key Characteristics:**
- Workspace-scoped multi-tenancy: every DB row carries `workspace_id` (FK to `workspaces`), every API request resolves an `AuthCtx` with `workspace_id`
- No message broker (Redis/Celery absent): outbound send queue lives entirely in PostgreSQL `message_queue` table, drained by an asyncio background task (`QueueWorker`) inside the API container
- Listener (AI reply) runs in a separate Docker container (`outreach-platform-listener`) sharing the same PostgreSQL DB
- Auth is dual-path: Supabase JWT (ES256/JWKS, HS256 fallback) for UI; bcrypt `wsk_` API keys for integrations (n8n, etc.)
- Schema migrations are raw SQL files auto-applied at API startup via `_apply_migrations()` under `pg_advisory_lock`

---

## Backend

### Containers

Three Docker Compose services (`/root/apps/aimly/tg-outreach/docker-compose.yml`):

| Service | Container | Binds | Role |
|---|---|---|---|
| `db` | `outreach-platform-db` | (internal) | PostgreSQL 16 |
| `api` | `outreach-platform-api` | `127.0.0.1:8005:8000` | FastAPI + background workers |
| `listener` | `outreach-platform-listener` | (none) | Telethon event loop for incoming Telegram messages |

### Layers

**Config Layer:**
- Purpose: Environment-driven settings with Pydantic BaseSettings
- Location: `app/config.py`
- Contains: `Settings` class, `get_settings()` cached factory
- Used by: all other layers

**Database Layer:**
- Purpose: Async SQLAlchemy engine, session factory, migration applier
- Location: `app/database.py`
- Contains: `engine`, `AsyncSessionLocal`, `get_db()` dependency, `init_db()`, `_apply_migrations()`
- Pattern: raw `asyncpg` connection for migration applier (avoids SQLAlchemy transaction conflicts); `SQLAlchemy async` for all ORM queries
- Depends on: `app/config.py`

**Models Layer:**
- Purpose: SQLAlchemy ORM table definitions
- Location: `app/models/__init__.py`
- Key models: `Workspace`, `UserWorkspace`, `WorkspaceApiKey`, `Sender`, `AIContext`, `MessageQueue`, `MessageLog`, `ContactCache`, `Conversation`, `Message`, `Campaign`, `CampaignSender`, `Folder`, `Contact`
- All tenant-scoped models carry `workspace_id UUID FK → workspaces.id ON DELETE CASCADE`

**Schemas Layer:**
- Purpose: Pydantic request/response validation and serialization
- Location: `app/schemas/__init__.py`
- Pattern: separate `*Create`, `*Update`, `*Response` classes per resource

**Auth Layer:**
- Purpose: Dual-path authentication FastAPI dependency
- Location: `app/utils/auth.py`
- Resolves to: `AuthCtx(workspace_id, user_id, source, role)`
- JWT path: JWKS fetched from Supabase, 1h process-local cache; ES256 primary, HS256 fallback; lazy workspace auto-create with race protection via `ON CONFLICT DO NOTHING` + orphan cleanup
- API key path: prefix-filtered DB lookup, bcrypt verify (via `asyncio.to_thread`), 5-min in-process LRU cache (`_TOKEN_CACHE`, max 1024 entries)

**Routers Layer:**
- Purpose: HTTP endpoint handlers, I/O validation, request orchestration
- Location: `app/routers/`
- Files: `agents.py`, `analytics.py`, `campaigns.py`, `check_contacts.py`, `contacts.py`, `conversations.py`, `folders.py`, `health.py`, `onboarding.py`, `send.py`, `senders.py`, `telemetry.py`, `workspace.py`
- Pattern: all routers use `Depends(auth_dep)` and filter every query by `workspace_id`; prefix `/api/v1/<resource>`

**Services Layer:**
- Purpose: Domain logic, Telegram MTProto operations, background workers
- Location: `app/services/`
- Key services:

| File | Role |
|---|---|
| `telegram.py` | `TelegramService` singleton — Telethon client pool, `send_message`, `send_file`, entity-cache warmup on `ValueError` cold start |
| `queue.py` | `QueueWorker` — polls `message_queue` every 3s; per-sender rate limits read from `senders` row; `FOR UPDATE SKIP LOCKED` for concurrency safety |
| `campaign_enqueue.py` | `CampaignEnqueueWorker` — populates queue from running campaigns' folder contacts; per-contact `begin_nested()` savepoint atomicity |
| `ai_engine.py` | OpenAI GPT chat completion with built-in signal tools (`mark_as_lead`, `transfer_to_manager`, `finish_conversation`) and JSONB custom tools from campaign |
| `listener.py` | Standalone entry point for `listener` container; `ResilientTelegramClient` event handlers for all active sender sessions; debounce + AI reply loop |
| `onboarding_state.py` | In-memory FSM for Telegram phone/SMS/2FA/QR account onboarding |
| `warmup.py` | `WarmupWorker` — gradual send ramp for new Telegram accounts |
| `csv_import.py` | CSV parsing and batch contact upsert |
| `template.py` | `{{variable}}` substitution for campaign message templates |
| `rotation.py` | `get_or_assign_sender` — load-balances contacts across campaign's attached senders |
| `recontact.py` | `protected_conversation_sql` — shared SQL predicate for per-campaign re-contact age policy |
| `encryption.py` | Fernet-based session string encrypt/decrypt for stored Telethon sessions |
| `checker.py` | Phone/username Telegram registration check via checker-role sender |
| `webhook_notify.py` | Fire-and-forget webhook POSTs for AI signal events (`mark_as_lead`, etc.) |
| `llm_logger.py` | Persists LLM call trace to `llm_calls` table for audit (ANLX-05) |

**Utils Layer:**
- Purpose: Shared stateless helpers
- Location: `app/utils/`
- Files: `auth.py` (the `auth_dep` dependency + `AuthCtx`), `names.py` (name formatting), `phone.py` (normalization + `contact_identity_key`)

### Migrations

- Location: `/root/apps/aimly/tg-outreach/migrations/`
- Naming: `NNN_short_name.sql` (lexical sort), bootstrap table: `_schema_migrations.sql`
- Applied: automatically at API startup via `init_db()` → `_apply_migrations()` under `pg_advisory_lock`
- Tracking table: `schema_migrations(version, sha256, applied_at)`
- Current count: migrations `001` through `026` as of 2026-06-18
- Must be idempotent: `IF NOT EXISTS`, `DO $$ EXCEPTION duplicate_object $$`, `ON CONFLICT DO NOTHING`
- Failure semantics: migration failure blocks API startup (fail-fast)

### Background Workers (all asyncio tasks in API process)

| Worker | Poll | Purpose |
|---|---|---|
| `QueueWorker` | 3s | Drain `message_queue` respecting per-sender rate limits and campaign working-hour windows |
| `CampaignEnqueueWorker` | configurable (`campaign_enqueue_tick_seconds`) | Populate queue from running campaigns' contact folders |
| `WarmupWorker` | configurable | Send warmup messages for new Telegram accounts |
| `OnboardingCleanupWorker` | configurable | Expire stale onboarding sessions |
| `ContactCheckWorker` | configurable | Batch-check phone/username Telegram registration status |

All workers follow the same pattern: `start()` / `stop()` registered in FastAPI `lifespan`; `_running` flag + asyncio task cancel on shutdown.

### Queue Flow (critical send path)

1. Campaign `status='running'` → `CampaignEnqueueWorker` reads contacts from folder (`tg_status='registered'`, not yet in `campaign_contact_assignments`), assigns sender via `rotation.get_or_assign_sender`, renders template, inserts into `message_queue` with `campaign_id`
2. `QueueWorker` ticks every 3s:
   - Batch SELECT with `JOIN campaigns` filtering `status='running'`, `scheduled_at <= NOW()`, `FOR UPDATE SKIP LOCKED`
   - Python-side working-hour check (`_campaign_in_working_window` using `zoneinfo`)
   - Per-sender rate limit check (reads `rate_per_min/hour/day` from `senders` row)
   - Calls `TelegramService.send_message()` (Telethon MTProto)
   - Success: queue item `status='sent'`, `MessageLog` row written, `Conversation` upserted, `Message` row written, optional callback webhook (`asyncio.create_task`)
   - `FloodWaitError`: reschedule; if `>= 300s` (hard threshold) → pause ALL pending for sender
   - `PEER_FLOOD`: 24h pause all sender items
   - `SessionAuthError`: deactivate sender, fail all pending items
   - Default retry: max 3 attempts, 60s × attempt backoff

### Error Handling

**HTTP:** Global exception handlers in `app/main.py` inject CORS headers on all 4xx/5xx (prevents browser CORS masking real errors).

**Auth:** `SessionAuthError` in queue worker deactivates sender immediately; all pending queue items failed.

**FloodWait:** Respected exactly — not counted as retry attempt.

**Startup:** Any migration failure → process exits (fail-fast).

---

## Frontend

**Repository:** `/root/apps/aimly/aimly-tg-outreach`

### Framework & Rendering

- **TanStack Start** (SSR framework on Vite + TanStack Router)
- **Deployment target:** Cloudflare Workers (via `@cloudflare/vite-plugin`, `wrangler.jsonc`)
- **Package manager:** bun (`bun.lock`)
- **Config:** `vite.config.ts` delegates entirely to `@lovable.dev/vite-tanstack-config`
- **Server entry:** `src/start.ts` — `createStart()` with error-handling middleware; renders 500 HTML on unhandled exception
- **SSR note:** `_authenticated.tsx` has `ssr: false` — the entire authenticated subtree is client-rendered (Supabase auth state lives in `localStorage`)

### Routing

File-based routing with TanStack Router. Route tree is auto-generated to `src/routeTree.gen.ts` (do not hand-edit).

**Route hierarchy:**
```
src/routes/
  __root.tsx             → Root shell (HTML/head/Scripts) + QueryClientProvider + AuthSync + Toaster
  login.tsx              → Public login page (Supabase Magic Link / PKCE)
  auth.callback.tsx      → OAuth PKCE callback handler
  _authenticated.tsx     → Auth guard (beforeLoad checks Supabase session) + AppSidebar layout
    index.tsx                  → Dashboard (analytics cards, funnel, recent campaigns)
    campaigns.index.tsx        → Campaign list (tabs: all/running/paused/scheduled/draft/finished)
    campaigns.new.tsx          → Create campaign wizard
    campaigns.$id.tsx          → Campaign detail + contact progress
    inbox.tsx                  → Conversation inbox (AI/manager mode toggle, LLM trace)
    contacts.tsx               → Contact list + CSV import
    accounts.tsx               → Telegram account management + OnboardingFlow component
    agents.tsx                 → AI agent (AIContext) CRUD
    settings.tsx               → Workspace settings, API key management
    onboarding.tsx             → First-run guided onboarding
```

**Auth guard:** `_authenticated.tsx` `beforeLoad` calls `supabase.auth.getSession()`; if no session → `redirect` to `/login`. DEV mode (`import.meta.env.DEV`) bypasses guard for Lovable preview.

**`AuthSync` component** (in `__root.tsx`): subscribes to `supabase.auth.onAuthStateChange`, invalidates all TanStack Query caches and router on session change; listens for `aimly:auth-expired` DOM event to sign out + redirect.

### Data Fetching Pattern

Library: TanStack Query (`@tanstack/react-query`). `QueryClient` created in `src/router.tsx` and injected as router context.

**Queries:**
```typescript
const q = useQuery({
  queryKey: ["campaigns"],
  queryFn: () => api<CampaignList>("/api/v1/campaigns"),
  staleTime: 30_000,   // varies by resource
});
```

**Mutations with optimistic invalidation:**
```typescript
const mut = useMutation({
  mutationFn: ({ id, action }) =>
    api<Campaign>(`/api/v1/campaigns/${id}/${action}`, { method: "POST" }),
  onSuccess: () => void qc.invalidateQueries({ queryKey: ["campaigns"] }),
  onError: (e) => setActionError(errMsg(e)),
});
```

### API Client (`src/lib/api.ts`)

`api<T>(path, opts)` is the only function that calls the backend:
- Reads `VITE_BACKEND_URL` env var for base URL
- Polls up to 2s for Supabase JWT token before request
- Sets `Authorization: Bearer <token>` on authenticated requests
- Throws typed `ApiError(status, code, message, detail)` on non-OK responses
- Fires `aimly:auth-expired` DOM event on 401 `TOKEN_EXPIRED`
- Supports `body: FormData` (for CSV import) and `body: JsonObject`

### State Management

- **Server/async state:** TanStack Query cache — all API data
- **Local UI state:** React `useState` per route component — no global state store
- **Auth state:** Supabase JS SDK (persisted in `localStorage`, auto-refreshed via PKCE)

### Type Safety

API types generated from OpenAPI spec:
- Spec: `docs/openapi.json` (mirrors backend `/docs`)
- Generated: `src/types/api.ts` — do NOT edit manually
- Usage pattern: `import type { components } from "@/types/api"` → `type Campaign = components["schemas"]["CampaignResponse"]`

### Key Frontend Abstractions

| File | Purpose |
|---|---|
| `src/lib/api.ts` | Central HTTP client — all backend calls go through `api<T>()` |
| `src/lib/supabase.ts` | Browser-only Supabase client; SSR stub; `hasSupabaseEnv` flag |
| `src/lib/telemetry.ts` | `track(event, props)` — batched, 1.5s delayed, `sendBeacon` on `pagehide` |
| `src/lib/error-codes.ts` | Maps backend `code` strings to user-facing messages |
| `src/components/AppSidebar.tsx` | Persistent nav sidebar in `_authenticated.tsx` layout |
| `src/components/OnboardingFlow.tsx` | Multi-step Telegram account onboarding (phone/SMS/2FA) |
| `src/components/EditCampaignModal.tsx` | Campaign edit modal shared by campaign list and detail |
| `src/router.tsx` | Creates `QueryClient` + router with shared `queryClient` context |

### Path Alias

`@/` maps to `src/` (configured in `@lovable.dev/vite-tanstack-config`).

---

## End-to-End Flow: Frontend → Backend API → Telegram

### Outbound Campaign Message

1. UI: user creates campaign → `POST /api/v1/campaigns` (status `draft`)
2. UI: user attaches senders → `POST /api/v1/campaigns/{id}/senders`
3. UI: user imports contacts CSV → `POST /api/v1/contacts/import` → contacts + folder created
4. UI: user starts campaign → `POST /api/v1/campaigns/{id}/start` → status `running`
5. Backend `CampaignEnqueueWorker`: picks running campaign, reads eligible contacts from folder, assigns sender via `rotation.get_or_assign_sender`, renders `{{variable}}` template, inserts rows into `message_queue`
6. Backend `QueueWorker`: every 3s picks first eligible queue item per sender (SKIP LOCKED), validates rate limits + working-hour window, calls `TelegramService.send_message()` → Telethon MTProto
7. On success: queue item → `sent`; `MessageLog` written; `Conversation` upserted; callback webhook fired (fire-and-forget)
8. UI: `GET /api/v1/analytics/campaigns/{id}` polled every 30s for funnel metrics

### Incoming Message → AI Reply

1. `listener` container: `ResilientTelegramClient` event handlers registered for all active sender sessions (reconciled from DB)
2. Incoming Telegram message → saved to `messages` table, `conversations.last_message_at` updated
3. Debounce (3–5 min) to batch rapid multi-message exchanges
4. `ai_engine.generate_response()`:
   - Loads `AIContext` (agent) via `conversations.ai_context_id` → `campaigns.agent_id`
   - Builds OpenAI messages array from `messages` history
   - Calls `AsyncOpenAI.chat.completions.create()` with signal function tools
   - If signal tool called (`mark_as_lead`, `transfer_to_manager`, `finish_conversation`): updates conversation status, fires campaign webhook
   - LLM call logged to `llm_calls` table
5. `TelegramService.send_message()` sends AI reply via Telethon

### Manager Takeover (Inbox)

1. Manager clicks "Take over" → `POST /api/v1/conversations/{id}/disable-ai` → `ai_enabled=false`, `status='manual'`, pending queue items cancelled
2. `QueueWorker` pre-send guard: every send checks `conversations.ai_enabled`; if `false` → item marked `failed` (reason: `Conversation taken over manually`)
3. Manager types message → `POST /api/v1/conversations/{id}/send` → `TelegramService.send_message()` directly (bypasses queue)
4. Manager re-enables AI → `POST /api/v1/conversations/{id}/enable-ai` (does NOT change `status`)

---

## Cross-Cutting Concerns

**Logging:** `logging.basicConfig`, module-level `logger = logging.getLogger(__name__)`. No structured logging library.

**Validation:** Pydantic v2 on all request/response; `RequestValidationError` handled globally with CORS headers preserved.

**CORS:** Explicit allowlist + regex for Lovable preview subdomains; custom exception handlers inject `Access-Control-Allow-Origin` on all error responses.

**Async discipline:** All DB via `async with AsyncSessionLocal() as db`; no `time.sleep()`; no synchronous `requests`; CPU-bound work (bcrypt) via `asyncio.to_thread`.

**Workspace isolation:** Application-level `WHERE workspace_id = :wid` on every query. Postgres RLS not yet in use (planned v2).

**Telemetry:** Frontend fires `track(event, props)` to `POST /api/v1/telemetry/events` (backend stores in `telemetry_events` table). 17-event whitelist in `app/routers/telemetry.py`.

---

*Architecture analysis: 2026-06-18*
