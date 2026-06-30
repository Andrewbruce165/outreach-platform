# Architecture

**Analysis Date:** 2026-06-30

## Pattern Overview

**Overall:** Multi-tenant SaaS backend — layered FastAPI + async background workers + dedicated listener container.

**Key Characteristics:**
- All database access is async via SQLAlchemy 2.0 AsyncSession (no sync ORM calls, no `time.sleep()`)
- Message queue lives entirely in PostgreSQL — no Redis, no Celery, no external broker
- Six background workers run as asyncio tasks inside the API container's lifespan; one worker runs in a dedicated listener container
- All endpoints are workspace-scoped; workspace isolation is enforced at the SQL WHERE clause level on every query (no RLS yet — planned as v2 TODO)
- Raw-SQL migrations auto-applied on every API startup via `app/database.py::_apply_migrations`, tracked in `schema_migrations`

---

## Deployment Layers

**Docker Compose — 3 services:**

| Container | Image / Dockerfile | Port | Responsibilities |
|---|---|---|---|
| `outreach-platform-db` | `pgvector/pgvector:pg16` | 5432 (internal) | PostgreSQL 16 + pgvector extension; DDL logging enabled |
| `outreach-platform-api` | `Dockerfile` | `127.0.0.1:8005:8000` | FastAPI app + 6 asyncio workers (queue, warmup, campaign-enqueue, contact-check, kb-ingest, onboarding-cleanup) |
| `outreach-platform-listener` | `Dockerfile.listener` | internal | Persistent Telethon MTProto listener; debounces inbound messages; fires AI responses via OpenAI; no HTTP |

**Network chain:** `:443 (nginx SNI stream) → nginx:8444 ssl proxy_protocol → 127.0.0.1:8005 → api:8000`

---

## Layers

**Router Layer (HTTP boundary):**
- Purpose: Parse + validate requests, enforce auth, delegate to service layer, return JSON
- Location: `app/routers/`
- Contains: FastAPI `APIRouter` objects, Pydantic request/response schemas (inline or from `app/schemas/`)
- Depends on: `app/utils/auth.py::auth_dep` (Depends), `app/database.py::get_db` (Depends), service functions
- Used by: `app/main.py` (registered via `app.include_router(...)`)

**Service Layer (business logic):**
- Purpose: All domain logic — Telegram I/O, AI calls, queue processing, checker resolve, warmup, KB ingest
- Location: `app/services/`
- Contains: Background worker classes, `TelegramService` singleton, `CheckerService` singleton, AI engine, KB search/ingest, restriction audit
- Depends on: `app/database.py::AsyncSessionLocal`, `app/models/`, `app/config.py`
- Used by: Routers (sync calls), background workers (self-loop), listener container

**Model Layer (ORM definitions):**
- Purpose: SQLAlchemy ORM table declarations; single source of truth for `Base.metadata.create_all`
- Location: `app/models/__init__.py` (all models in one file)
- Contains: All 20+ ORM classes (Workspace, Sender, Campaign, Contact, Conversation, AIContext, MessageQueue, KnowledgeBase, KbChunk, etc.)
- Depends on: `app/database.py::Base`
- Used by: Service layer, router layer

**Migration Layer (schema evolution):**
- Purpose: Raw-SQL schema additions not covered by `create_all` (indexes, constraints, enum values, new columns)
- Location: `migrations/` — 43 files named `NNN_short_name.sql` + `_schema_migrations.sql` bootstrap
- Applied by: `app/database.py::_apply_migrations()` at API startup, behind `pg_advisory_lock`
- Tracked in: `schema_migrations` table (version, sha256, applied_at)

**Utility Layer:**
- Purpose: Cross-cutting helpers with no business logic
- Location: `app/utils/`
- Files: `auth.py` (dual-auth FastAPI `Depends`), `phone.py` (normalize/identity-key helpers), `names.py`

**Configuration:**
- Location: `app/config.py`
- Pattern: `pydantic_settings.BaseSettings` with `@lru_cache` singleton via `get_settings()`
- All knobs (rate limits, checker burst-cap, KB chunk size, etc.) are env vars with documented defaults

---

## Data Flows

**Outbound campaign message (enqueue → send):**

1. `CampaignEnqueueWorker` ticks every 30s (configurable `CAMPAIGN_ENQUEUE_TICK_SECONDS`)
2. Selects `running` campaigns → for each: fetch `registered` contacts from folder not yet in `campaign_contact_assignments`
3. Calls `rotation.get_or_assign_sender(campaign_id, contact_phone, db)` → picks sender from `campaign_senders` pool using round-robin
4. `template.render_template(message_template, contact)` → rendered opener stored in `message_queue` row at enqueue time (NOT re-rendered later)
5. `QueueWorker` ticks every ~12–55s per sender (randomised, fatigue factor), respects per-sender rate limits (4/min, 20/hr, 150/day stored on `senders` columns)
6. Picks next `pending` item via `SELECT … FOR UPDATE SKIP LOCKED` per sender
7. Calls `TelegramService.send_message(sender, recipient_phone, text)` → Telethon `SendMessageRequest`
8. On success: writes `MessageLog` row, fires `callback_url` webhook if set, marks item `sent`
9. On `FloodWaitError`: reschedules all sender's pending items beyond the wait window; writes `SenderRestrictionEvent` row via `restriction_audit.py`

**Inbound message → AI response (listener flow):**

1. `Dockerfile.listener` runs `python -m app.services.listener` — standalone process with its own DB connection
2. All active senders' Telethon clients are loaded from `senders` table (decrypted sessions)
3. Each client registers `@client.on(events.NewMessage)` handler
4. Handler debounces 3–5 minutes (configurable per-conversation `response_speed` setting on `AIContext`)
5. Debounce fires → calls `ai_engine.generate_response(conversation, message_history, ai_context)`
6. `generate_response` calls `AsyncOpenAI.chat.completions.create` with function tools (built-in signals + custom campaign tools + optional KB search tool)
7. If LLM calls `search_knowledge_base` tool: `kb_search.search(query, kb_ids, workspace_id, db)` → pgvector cosine-distance query over `kb_chunks`, returns top-K chunks
8. If LLM calls `mark_as_lead` / `transfer_to_manager` / `finish_conversation`: fires webhook via `webhook_notify.notify_signal`, updates `conversations.status`
9. Response text sent via `TelegramService`, written to `MessageLog`
10. `LLMCall` row written for audit/debug

**Phone number checker resolve flow (Phase 14):**

1. `ContactCheckWorker` ticks every 5s, selects `pending` contacts using JOIN LATERAL that picks an eligible checker per workspace
2. Checker eligibility: `role='checker'` AND `auth_status='ok'` AND `restriction_status='none'` AND `lifecycle_status != 'paused'` AND `checker_rest_until` expired AND not at daily cap
3. Groups contacts by checker, calls `checker_service.check_phones(checker_slug, [phones], burst_cap=30)`
4. Inline anomaly detection (`_is_throttle_signal`): if batch has `flood_wait_hit` OR all-not_registered with no registered results → degrade checker inline, roll back results to `pending`
5. On clean batch: writes `contacts.tg_status`, `tg_confidence='high'`, `tg_probe_state='clean'`; stamps `checker_rest_until = now + 300s`; increments daily resolve count
6. On spam signal: sets `restriction_status='spam_limited'`, increments `checker_trip_count`, escalating backoff `cooldown * 2^(trip-1)` capped at 6h; writes `SenderRestrictionEvent`

**Account warmup flow:**

1. `WarmupWorker` ticks every 30s, active 09:00–20:00 MSK
2. Reads `warmup_pool` for workspace-enrolled senders + `warmup_settings` (enabled flag, topics, tone)
3. Pairs two senders from pool (A↔B), creates `WarmupSession` with AI-generated topic
4. Per session: sends message A→B or B→A via Telethon, writes `WarmupMessage`, increments `messages_sent`
5. Session completes when `messages_sent >= target_messages` (default 6)

**Knowledge base ingest flow (Phase 16):**

1. User uploads file via `POST /api/v1/knowledge-bases/{kb_id}/documents`
2. Router stores raw bytes in `kb_documents.raw_content`, `status='pending'`
3. `KbIngestWorker` claims one `pending` doc via `SELECT … FOR UPDATE SKIP LOCKED`, flips to `processing`
4. `kb_ingest.extract_text_async(doc)` → text (to_thread for CPU-bound PDF/DOCX parsing)
5. `kb_ingest.chunk_text(text)` → list of chunks (max 300 tokens, 60 overlap, tiktoken cl100k_base)
6. `kb_ingest.embed_texts(chunks)` → list of 1536-dim vectors via `AsyncOpenAI.embeddings.create` (text-embedding-3-small)
7. Bulk INSERT into `kb_chunks` (content + embedding), DELETE old chunks first (idempotent re-index)
8. Updates `kb_documents.status='indexed'`, `chunk_count`

---

## Key Abstractions

**`AuthCtx`:**
- Purpose: Resolved identity for every request — workspace_id + user_id + auth source
- Location: `app/utils/auth.py`
- Pattern: FastAPI `Depends(auth_dep)` — accepts `Authorization: Bearer <Supabase JWT>` (ES256/JWKS or HS256) OR `X-Workspace-Key: wsk_...` (bcrypt + 5-min LRU cache)
- Lazy workspace creation: valid JWT with no existing `user_workspaces` row → atomic create guarded by DB UNIQUE + ON CONFLICT

**`TelegramService` singleton:**
- Purpose: Per-sender Telethon client lifecycle (create, cache, auth, proxy config, FloodWait retry, entity-cache warm)
- Location: `app/services/telegram.py`
- Pattern: Module-level `telegram_service` instance; clients cached by sender slug; sessions decrypted from DB on first use

**`QueueWorker`:**
- Purpose: Rate-limited outbound message processor
- Location: `app/services/queue.py`
- Pattern: Module-level singleton `queue_worker`, `start()`/`stop()` wired in `app/main.py` lifespan; `SELECT … FOR UPDATE SKIP LOCKED` per sender

**`CampaignEnqueueWorker`:**
- Purpose: Generates `message_queue` rows from running campaigns' folder contacts
- Location: `app/services/campaign_enqueue.py`
- Pattern: Same lifecycle pattern as QueueWorker; per-contact atomicity via `begin_nested()` savepoint

**`AIContext` (called "agent" in the UI):**
- Purpose: Per-workspace AI configuration — system prompt, tone preset, rules, FAQ, auto-pause triggers, KB attachments
- Location: ORM in `app/models/__init__.py`, CRUD in `app/routers/agents.py`
- Relationship: Campaign FK `agent_id` → `ai_contexts.id`

**`Conversation`:**
- Purpose: One Telegram dialog thread between a sender and a contact; holds `ai_enabled`, `ai_context_id`, `campaign_id`, `status` (active/manual/paused/lead/handoff/finished)
- Location: ORM in `app/models/__init__.py`, inbox API in `app/routers/conversations.py`

**`SenderRestrictionEvent` (restriction audit log):**
- Purpose: Append-only log of every restriction state change with activity snapshot
- Location: ORM in `app/models/__init__.py`, writer in `app/services/restriction_audit.py`
- Pattern: Written in the SAME transaction as `senders.restriction_status` UPDATE to guarantee consistency

---

## Entry Points

**API container (`outreach-platform-api`):**
- Location: `app/main.py`
- Triggers: `uvicorn app.main:app --host 0.0.0.0 --port 8000` (see `Dockerfile`)
- Startup sequence (lifespan):
  1. `init_db()` → `CREATE EXTENSION IF NOT EXISTS vector` → `Base.metadata.create_all` → `_apply_migrations()`
  2. `recover_stuck_jobs()` — reset any `processing` queue items left from unclean shutdown
  3. Start workers: `queue_worker`, `warmup_worker`, `onboarding_cleanup_worker`, `contact_check_worker`, `campaign_enqueue_worker`, `kb_ingest_worker`

**Listener container (`outreach-platform-listener`):**
- Location: `app/services/listener.py` (run as `__main__`)
- Triggers: `python -m app.services.listener` (see `Dockerfile.listener`)
- Startup: connects to DB, loads all active senders, creates Telethon clients with decrypted sessions, registers `NewMessage` handlers, starts `_reconcile_loop` background task

**Migration auto-applier:**
- Location: `app/database.py::_apply_migrations()`
- Trigger: Called from `init_db()` on every API startup
- Lock: `pg_advisory_lock(7261841720260526)` ensures only one API instance applies migrations in parallel scale-out

---

## Error Handling

**Strategy:** Fail-fast on startup (migration failure → API does not start); resilient loop for background workers (log + continue, never die).

**Patterns:**
- Routers: raise `HTTPException` with structured `{"code": "...", "message": "..."}` detail; global handlers in `app/main.py` add CORS headers to all error responses (4xx/5xx)
- Queue worker: `FloodWaitError` → reschedule + log; auth errors → mark sender `auth_status='session_expired'`; `MAX_ATTEMPTS=3` retry logic
- Listener: `ResilientTelegramClient` wraps `GetDifference` to prevent disconnect on unknown constructors
- Background workers: `try/except Exception` wrapping each tick with `logger.exception(...)`, loop continues

---

## Auth Flow

**JWT path (frontend):**
- Supabase issues ES256 JWT → frontend sends `Authorization: Bearer <token>`
- `_decode_supabase_jwt` routes on `alg` header: ES256 → JWKS fetch+cache; HS256 → `supabase_jwt_secret`
- `_resolve_or_create_workspace` finds or lazily creates workspace row

**API key path (integrations / n8n):**
- Key format: `wsk_<random>` — prefix (12 chars) stored in DB, full key bcrypt-hashed
- `_verify_api_key` → prefix SQL lookup → `bcrypt.checkpw` (in `asyncio.to_thread`) → 5-min LRU cache hit on repeat calls

---

## Cross-Cutting Concerns

**Logging:** `logging.basicConfig` INFO level; all workers use module-level `logger = logging.getLogger(__name__)`; never `print()`

**Workspace isolation:** Every DB query in routers and services includes `.where(Model.workspace_id == ctx.workspace_id)`; enforced at application layer (RLS planned as v2)

**Session encryption:** Telegram session strings stored AES-encrypted in `senders.session_string`; decrypted at runtime via `app/services/encryption.py`; key is `ENCRYPTION_KEY` env var

**Async:** `asyncio.to_thread()` for CPU-bound operations (bcrypt, text parsing); `httpx.AsyncClient` for all outbound HTTP; no `requests` library

---

*Architecture analysis: 2026-06-30*
