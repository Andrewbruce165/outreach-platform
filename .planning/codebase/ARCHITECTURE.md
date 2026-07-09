# Architecture

**Analysis Date:** 2026-07-09

## Pattern Overview

**Overall:** Modular-monolith SaaS backend (FastAPI) + background workers in the same process, paired with a static SPA frontend served independently by nginx. Two separate git repositories function as one system:

- **Backend** — `/root/apps/aimly/tg-outreach` (this repo). FastAPI REST API + Telethon listener, PostgreSQL-backed queue (no Redis/Celery).
- **Frontend** — historically a sibling repo (`AGS-Venture-Lab/aimly-tg-outreach`, generated/maintained via Lovable), **now also vendored into this repo's history** at `frontend/` (commit `c176901`, 2026-07-09) and built as a static SPA (commits `3e42fa6`, `6abac9f`). The frontend still has its own git remote when cloned standalone; inside this repo it is tracked as a plain subdirectory (not a submodule).

**Key Characteristics:**
- Workspace-scoped multi-tenancy enforced at the query layer (every table with tenant data carries `workspace_id`; every router filters `.where(Model.workspace_id == ctx.workspace_id)`) — no Postgres RLS yet (tracked as `TODO(v2-rls)` in `app/utils/auth.py`).
- Dual authentication: Supabase JWT (ES256 via JWKS, HS256 legacy fallback) for the UI, and `X-Workspace-Key` bearer tokens for machine integrations (n8n). Both resolve to the same `AuthCtx(workspace_id, user_id, source, role)` via `app/utils/auth.py::auth_dep`.
- No task queue framework — all async work (message sending, warmup, contact checking, campaign enqueue, KB ingestion, follow-ups, account import, grade progression) runs as long-lived `asyncio` background tasks started in the FastAPI `lifespan` (`app/main.py`), each polling PostgreSQL tables under `SELECT ... FOR UPDATE SKIP LOCKED`-style claiming.
- The Telegram listener (`app/services/listener.py`) runs as a **separate Docker container/process** (`Dockerfile.listener`, service `listener` in `docker-compose.yml`), not inside the API process — it holds long-lived Telethon client connections per sender account and must never run concurrently with any other Telegram automation on the same accounts (see root `/root/CLAUDE.md` warning about the retired `telegram-api` project).
- Migrations are raw SQL files auto-applied at API startup (`app/database.py::_apply_migrations`), never Alembic. No independent migration-runner step in CI/deploy — the API container self-heals schema drift on every boot.
- The frontend is a fully client-side rendered SPA (TanStack Start with `spa: { enabled: true }`, `nitro: false`) — SSR/server functions are inert; all data fetching happens client-side against the FastAPI backend via `fetch`-based `frontend/src/lib/api.ts`. It is deployed as static files, not a running Node/Bun process.

## Layers

**API Routing Layer:**
- Purpose: HTTP surface — request validation, auth, workspace scoping, calling services/ORM directly.
- Location: `app/routers/*.py` (18 router modules, one per resource: `campaigns.py`, `senders.py`, `contacts.py`, `conversations.py`, `warmup.py`, `knowledge_bases.py`, `account_import.py`, `grade_settings.py`, `onboarding.py`, `check_contacts.py`, `analytics.py`, `agents.py`, `folders.py`, `llm_settings.py`, `send.py`, `telemetry.py`, `workspace.py`, `health.py`).
- Contains: FastAPI `APIRouter` instances, Pydantic request/response wiring, `Depends(auth_dep)`, docstrings enumerating each endpoint's HTTP verb + path + purpose (kept current — read the header docstring of any router before adding an endpoint).
- Depends on: `app/schemas` (Pydantic I/O models), `app/models` (ORM), `app/services/*` (business logic), `app/utils/auth.py`.
- Used by: the frontend SPA (`frontend/src/lib/api.ts`), n8n workflows (via `X-Workspace-Key`), `lovable-handoff/openapi.json` consumers.

**Service Layer (business logic + background workers):**
- Purpose: everything that isn't pure request/response — rate-limited sending, warmup ladders, AI reply generation, checker/contact-resolution, restriction bookkeeping, LLM provider abstraction.
- Location: `app/services/*.py` (30 modules) and `app/services/llm/*.py` (provider abstraction: `base.py`, `openai_provider.py`, `anthropic_provider.py`, `resolve.py`, `capabilities.py`, `models_filter.py`).
- Contains: `*_worker` singleton objects with `.start()`/`.stop()` (asyncio task lifecycle), `TelegramService` (Telethon wrapper, `app/services/telegram.py`), AI engine (`app/services/ai_engine.py`), queue engine (`app/services/queue.py`), checker/contact-resolution ladder (`app/services/checker.py`, `app/services/contact_check_worker.py`).
- Depends on: `app/models`, `app/database.py::AsyncSessionLocal`, `app/config.py::get_settings()`, Telethon, OpenAI/Anthropic SDKs.
- Used by: routers (call services directly, no separate "use case" layer) and by each other (e.g. `queue.py` imports `restriction_audit.py`, `grade_ladder.py`, `variation.py`).

**Data / Persistence Layer:**
- Purpose: ORM models + raw-SQL migrations + engine/session management.
- Location: `app/models/__init__.py` (single file, ~1080 lines, all ~37 ORM classes — no per-model file split), `app/database.py` (engine, `AsyncSessionLocal`, migration applier), `migrations/*.sql` (61 files as of 2026-07-09, `NNN_short_name.sql` + bootstrap `_schema_migrations.sql`).
- Contains: SQLAlchemy 2.0 declarative models (`Workspace`, `Sender`, `Campaign`, `MessageQueue`, `Conversation`, `Contact`, `KnowledgeBase`/`KbDocument`/`KbChunk` for pgvector RAG, `LLMSettings`, `AccountImportJob`, etc.), enums (`MessageType`, `QueueItemStatus`, `QueueItemType`).
- Depends on: PostgreSQL 16 (`pgvector/pgvector:pg16` image — pgvector extension required for KB embeddings), asyncpg driver.
- Used by: every router and service.

**Schema / Validation Layer:**
- Purpose: Pydantic request/response contracts exposed to the frontend and via `lovable-handoff/openapi.json`.
- Location: `app/schemas/__init__.py` (single file, ~1360 lines — same "one big file" convention as models) plus `app/schemas/knowledge_bases.py` (split out separately for Phase 16).
- Contains: `CampaignCreate/Update/Response`, `PoolHealth`, `SenderAttachWarning`, etc. Some routers define small inline `BaseModel`s locally instead of importing from `schemas` (e.g. `campaigns.py`) — check both places when tracing a contract.
- Used by: routers for request/response typing; mirrored (by hand, via Lovable generation) into `frontend/src/types` / `lovable-handoff/types/api.ts`.

**Frontend Presentation Layer:**
- Purpose: SPA UI consuming the backend REST API.
- Location: `frontend/src/routes/` (file-based TanStack Router routes, `_authenticated/` layout group gated by Supabase session), `frontend/src/components/` (feature components + `components/ui` shadcn primitives), `frontend/src/lib/` (`api.ts` fetch client, `supabase.ts` auth client, `telemetry.ts`, `error-codes.ts`).
- Depends on: Supabase JS SDK (auth only — no direct DB access from frontend), backend `/api/v1/*` routes via `VITE_BACKEND_URL`.
- Used by: end users via browser; built once into static files, no runtime dependency on the repo after build.

## Data Flow

**Outbound campaign message send:**

1. Campaign created via `POST /api/v1/campaigns` (`app/routers/campaigns.py`), senders attached via `rebalance_on_attach` (`app/services/rebalance.py`).
2. `campaign_enqueue_worker` (`app/services/campaign_enqueue.py`) polls active campaigns, renders the template (`app/services/template.py`, personalization variables), applies anti-spam text variation (`app/services/variation.py`), and inserts a row into `message_queue` (snapshotting the rendered text at enqueue time — editing the campaign template later does **not** retroactively update already-queued rows).
3. `queue_worker` (`app/services/queue.py`) polls `message_queue`, enforces per-sender rate limits (4/min, 20/hour, 150/day — DB-column-backed `senders.rate_per_*`, empirically tuned, "green corridor"), randomized human-like send intervals, long-pause cycles, and campaign working-hour windows, then calls `TelegramService` (`app/services/telegram.py`) to actually send via Telethon.
4. Send failures (`FloodWaitError`, spam signals) route through `app/services/restriction_audit.py` to append an immutable event to `sender_restriction_events` and flip `senders.restriction_status`.

**Inbound message + AI reply:**

1. `listener` service (`app/services/listener.py`, separate container) holds live Telethon clients per sender, receives `events.NewMessage`, persists to `Conversation`/message log tables.
2. Debounces 3–5 min (tunable, see `feedback` re: debounce window 40s–120s in commit history) before triggering AI.
3. `app/services/ai_engine.py` builds context from `AIContext` (prompts/tone/rules/FAQ/auto_pause_triggers), optionally does RAG lookup via `app/services/kb_search.py` against pgvector-embedded `kb_chunks`, calls the configured LLM provider (`app/services/llm/resolve.py` picks OpenAI/Anthropic per `LLMSettings`), computes a human-like typing hold (`compute_typing_hold`), and sends the reply back through `TelegramService`.
4. Auto-pause triggers or manual "switch to manager mode" from the inbox UI (`frontend/src/routes/_authenticated/inbox.tsx`) stop AI auto-reply for a conversation.

**Contact resolution (checker) flow:**

1. Contacts imported via CSV (`app/services/csv_import.py`) or bulk account import land in `pending` state in `contacts_cache`.
2. `contact_check_worker.py` selects a healthy checker `Sender` (restriction-gated, rest-gated via `checker_rest_until`), resolves via a ladder: cache → `ResolveUsername` (captured `@username`) → `ImportContacts` (`app/services/checker.py`).
3. Suspect/throttle detection inline (`_is_throttle_signal`) rolls back ambiguous `not_registered` results to `pending` rather than finalizing false negatives; confidence/source fields (`tg_confidence`, `tg_resolved_by`, `tg_probe_state`) are stamped per row.

**State Management:**
- No client-side global store beyond React Query-style hooks colocated per route (`frontend/src/hooks`, `frontend/src/routes/*`) and Supabase session state (`frontend/src/lib/supabase.ts`).
- Server-side "state" is entirely PostgreSQL — job/queue status columns (`QueueItemStatus`, campaign `status` enum, `restriction_status`, `lifecycle_status`) drive all worker behavior; there is no in-memory job state that survives a container restart except the per-process JWT/token caches in `app/utils/auth.py`.

## Key Abstractions

**Worker (asyncio background task singleton):**
- Purpose: represents one polling loop (queue send, warmup, contact-check, campaign enqueue, KB ingest, follow-up, account import, grade progression).
- Examples: `queue_worker` (`app/services/queue.py`), `warmup_worker` (`app/services/warmup.py`), `contact_check_worker` (`app/services/contact_check_worker.py`), `campaign_enqueue_worker` (`app/services/campaign_enqueue.py`), `kb_ingest_worker` (`app/services/kb_ingest_worker.py`), `follow_up_worker` (`app/services/follow_up.py`), `account_import_worker` (`app/services/account_import_worker.py`), `grade_progression_worker` (`app/services/grade_progression.py`).
- Pattern: module-level singleton object with `.start()` (spawns `asyncio.create_task`) / `.stop()` (cancels + awaits), wired explicitly in `app/main.py` lifespan in both start order and reverse-stop order.

**AuthCtx (tenant + identity context):**
- Purpose: unifies the two auth paths into one object routers can depend on.
- Examples: `app/utils/auth.py::AuthCtx`, `auth_dep` (FastAPI `Depends`).
- Pattern: every workspace-scoped router takes `ctx: AuthCtx = Depends(auth_dep)` and filters every query by `ctx.workspace_id`. There is no ORM-level enforcement (no RLS) — omitting the `.where()` clause in a new router is a silent tenant-isolation bug.

**Sender (Telegram account) lifecycle/health model:**
- Purpose: represents one onboarded Telegram account with rate limits, restriction status, grade, proxy assignment.
- Examples: `Sender` ORM model (`app/models/__init__.py`), `SenderRestrictionEvent` (audit log), `SenderGradeSettings`/grade ladder (`app/services/grade_ladder.py`, `app/services/grade_progression.py`).
- Pattern: state machine driven by columns (`restriction_status`, `lifecycle_status`, `checker_rest_until`) rather than a separate state-machine library; every worker that touches a sender must re-check these flags before acting (documented extensively in root `CLAUDE.md` "Семантика checker'а" section).

**LLM Provider abstraction:**
- Purpose: switch between OpenAI/Anthropic per-workspace without touching call sites.
- Examples: `app/services/llm/base.py` (interface), `openai_provider.py`, `anthropic_provider.py`, `resolve.py` (picks provider from `LLMSettings` row), `capabilities.py`/`models_filter.py` (per-model feature gating).
- Pattern: thin adapter classes implementing a common `complete()`-style interface; `ai_engine.py` and `warmup.py` both consume the resolved provider rather than importing SDKs directly.

## Entry Points

**API HTTP server:**
- Location: `app/main.py` (`app = FastAPI(...)`), started via `uvicorn app.main:app` (`Dockerfile` CMD).
- Triggers: HTTP requests on `127.0.0.1:8005` (container), reverse-proxied by nginx at `127.0.0.1:8444 ssl proxy_protocol` behind the host's `:443` SNI dispatcher, public domain `https://aimly.agsventurelab.com/api/*`.
- Responsibilities: routing, auth, DB init + migrations, starts/stops all 8 background workers in `lifespan`.

**Listener process:**
- Location: `app/services/listener.py`, entry via `python -m app.services.listener` (`Dockerfile.listener` CMD), separate container `outreach-platform-listener`.
- Triggers: runs continuously; internally holds one Telethon client per active `Sender` and reacts to `events.NewMessage`.
- Responsibilities: inbound message capture, AI-reply orchestration, periodic reconciliation loop (`_reconcile_loop`) that self-heals dead Telethon connections (replacing the old Docker-restart-via-socket-mount approach).

**Frontend SPA:**
- Location: `frontend/src/routes/__root.tsx` + TanStack Start SSR shell, built by `bun run build` (invoked via `deploy-frontend.sh` inside a `oven/bun:1` Docker container — the host has no bun installed).
- Triggers: browser navigation to `https://aimly.agsventurelab.com/*`; nginx `try_files $uri /_shell.html` serves the static shell for any unmatched path (SPA fallback), static assets served directly from `/var/www/aimly/`.
- Responsibilities: none server-side — `dist/server` (SSR bundle) is built but explicitly **not served** (inert); all rendering and data-fetching is client-side.

## Error Handling

**Strategy:** FastAPI exception handlers + explicit CORS-header echoing on error paths (because CORSMiddleware doesn't run on paths that raise before routing completes).

**Patterns:**
- Global handlers for `RequestValidationError` and `StarletteHTTPException` in `app/main.py`, manually attaching `Access-Control-Allow-Origin` via `_cors_headers(request)` so 4xx/5xx errors don't appear to the browser as opaque "CORS blocked" failures (documented root cause: Phase 05.1-DEBUG `agents-500-cors`).
- Structured error envelope with `code` field — frontend maps `code` → user-facing message via `frontend/src/lib/error-codes.ts` / `lovable-handoff/error-codes.md`. Unknown telemetry event names return `400 UNKNOWN_EVENT` (whitelist enforced in `app/routers/telemetry.py`).
- Telethon-specific errors (`FloodWaitError`, `AuthKeyError` family, `UserDeactivatedBanError`) are caught at the service boundary (`app/services/telegram.py`, `app/services/queue.py`, `app/services/listener.py`) and translated into restriction-status updates rather than propagated as raw exceptions.
- Migration failures are fail-fast: an exception in `_apply_migrations` prevents API startup entirely (no half-applied state left running).

## Cross-Cutting Concerns

**Logging:** Standard library `logging`, configured once in `app/main.py` (`logging.basicConfig`, `%(asctime)s - %(name)s - %(levelname)s - %(message)s`). Every module gets its own `logger = logging.getLogger(__name__)`. Postgres itself logs all DDL + slow queries (`log_statement=ddl`, `log_min_duration_statement=1000` in `docker-compose.yml`) as an anti-drift safety net after a 2026-05-26 incident.

**Validation:** Pydantic v2 models (`app/schemas`) at the API boundary; DB-level `CHECK` constraints and idempotent migration guards (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`) as a second line of defense against drift between ORM (`Base.metadata.create_all`) and raw-SQL migrations.

**Authentication:** Centralized in `app/utils/auth.py::auth_dep` — Supabase JWT (JWKS/ES256, HS256 fallback) or `X-Workspace-Key`, with an in-process 5-minute LRU-ish cache to avoid re-hashing bcrypt-backed workspace keys on every request.

---

*Architecture analysis: 2026-07-09*
