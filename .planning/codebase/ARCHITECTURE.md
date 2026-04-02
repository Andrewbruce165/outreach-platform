# Architecture

**Analysis Date:** 2026-04-02

## Pattern Overview

**Overall:** Multi-Container Async Service with PostgreSQL-backed Queue

**Key Characteristics:**
- Two separate Docker containers: `api` (FastAPI + background workers) and `listener` (Telethon event loop)
- PostgreSQL serves as the message queue — no Redis or Celery
- Fully async throughout: asyncio, AsyncSession, Telethon async client
- Stateless HTTP layer; all durable state persists in PostgreSQL

## Layers

**Router Layer:**
- Purpose: Accept HTTP requests, validate input, delegate to services
- Location: `app/routers/`
- Contains: FastAPI `APIRouter` instances, Pydantic request/response validation, API key auth via `Depends(verify_api_key)`
- Depends on: Service layer, Database layer
- Used by: FastAPI app (`app/main.py`) via `app.include_router()`

**Service Layer:**
- Purpose: Business logic, background workers, Telegram operations
- Location: `app/services/`
- Contains: Queue worker, AI engine, Telegram client abstraction, listener, warmup worker, sender rotation, encryption, checker
- Depends on: Database layer, models, external APIs (Telegram MTProto, OpenAI)
- Used by: Routers, listener process, background tasks

**Data Layer:**
- Purpose: SQLAlchemy ORM models and async DB session management
- Location: `app/models/__init__.py`, `app/database.py`
- Contains: All ORM model classes, `AsyncSessionLocal`, `get_db()` dependency
- Depends on: PostgreSQL 16 via `asyncpg`
- Used by: Services, routers (via `Depends(get_db)`)

**Schema Layer:**
- Purpose: Pydantic models for API request/response serialization and validation
- Location: `app/schemas/__init__.py`
- Contains: Request/response Pydantic models, shared type definitions
- Depends on: Nothing (pure Pydantic)
- Used by: Router layer only

**Configuration Layer:**
- Purpose: Centralized settings management via environment variables
- Location: `app/config.py`
- Contains: `Settings` (pydantic-settings `BaseSettings`), `get_settings()` singleton via `lru_cache`
- Depends on: `.env` file or environment
- Used by: Services, routers, database setup

## Data Flow

**Outbound Message Send (HTTP → Queue → Telegram):**

1. Client POSTs to `POST /api/v1/send` with `X-API-Key` header
2. `app/routers/send.py` validates API key via `verify_api_key` dependency
3. Router resolves sender: explicit slug lookup OR auto-rotation via `app/services/rotation.py` (`get_or_assign_sender`)
4. Router calls `enqueue_message()` from `app/services/queue.py`, which inserts a `MessageQueue` row into PostgreSQL
5. Router returns immediately with `queue_id`, `queue_position`, and `estimated_send_at`
6. `QueueWorker` background task (runs inside `api` container, started at lifespan) polls DB every 3 seconds
7. Worker checks working hours (09:00–20:00 MSK), per-sender rate limits (4/min, 20/hr, 150/day)
8. Worker calls `app/services/telegram.py` (`send_message` or `send_file`) using Telethon
9. On success: updates `MessageQueue` status to `sent`, writes `MessageLog`, upserts `Conversation`
10. If `callback_url` provided: fires webhook POST via `httpx` (fire-and-forget asyncio task)

**Inbound Message Handling (Listener Process):**

1. `listener` container runs `app/services/listener.py` as `__main__`
2. Listener loads all active `Sender` records from DB, decrypts sessions via `app/services/encryption.py`
3. Registers Telethon event handlers for each sender's account
4. On incoming message: stores in `messages` table, checks `ai_enabled` on conversation
5. Debounce timer (3–5 min) fires; `app/services/ai_engine.py` (`AIEngine`) generates reply via OpenAI
6. Checks `auto_pause_triggers` in `AIContext`; if matched, pauses AI and sets conversation status to `paused`
7. Sends AI reply back through Telethon client

**Account Onboarding Flow:**

1. Client calls `POST /api/v1/onboarding/start` with phone number
2. `app/routers/onboarding.py` starts a Telethon client, stores in-memory `_onboarding_sessions` dict
3. Client submits SMS code or 2FA password via subsequent endpoints
4. On completion: session string encrypted via `app/services/encryption.py`, stored in `senders` table

**State Management:**
- All durable state in PostgreSQL (messages, queue, conversations, contexts, warmup sessions)
- In-memory state: `_onboarding_sessions` dict in `app/routers/onboarding.py` (not persisted — lost on restart), `AIEngine._context_cache` dict (5-min TTL cache of AI contexts)
- Queue worker and warmup worker are singleton asyncio tasks, created once at app lifespan startup

## Key Abstractions

**QueueWorker:**
- Purpose: Background asyncio task that drains `message_queue` table at safe rate
- Location: `app/services/queue.py`
- Pattern: Singleton instance (`queue_worker = QueueWorker()`), started/stopped in FastAPI lifespan. Implements rate limiting, FloodWait handling, long human-like pauses, and retry logic entirely within the class.

**TelegramService / make_telegram_client:**
- Purpose: Abstraction over Telethon client creation and send operations
- Location: `app/services/telegram.py`
- Pattern: Module-level service object (`telegram_service`) with `get_client()`, `send_message()`, `send_file()`. Manages device fingerprint spoofing (Telegram Desktop impersonation) and proxy setup.

**AIEngine:**
- Purpose: OpenAI GPT response generation with context loading and function-calling support
- Location: `app/services/ai_engine.py`
- Pattern: Singleton class with in-memory context cache. Loads `AIContext` from DB (with 5-min TTL), builds system prompt, calls OpenAI API.

**Sender Rotation:**
- Purpose: Deterministic mapping of `(context_id, contact_phone)` to a specific sender account
- Location: `app/services/rotation.py`
- Pattern: Persists assignment in `context_contact_assignments` table; uses `ON CONFLICT DO NOTHING` to guard against race conditions. Picks least-used sender by 24h message count.

**Encryption:**
- Purpose: Encrypt/decrypt Telethon session strings at rest
- Location: `app/services/encryption.py`
- Pattern: Fernet symmetric encryption; key derived from `ENCRYPTION_KEY` env var via SHA-256.

## Entry Points

**API Process:**
- Location: `app/main.py`
- Triggers: `uvicorn app.main:app` (via `Dockerfile`)
- Responsibilities: Register all routers, start `QueueWorker` and `WarmupWorker` background tasks, initialize DB schema via `init_db()`, recover stuck queue jobs at startup

**Listener Process:**
- Location: `app/services/listener.py` (run as `python -m app.services.listener`)
- Triggers: Container startup via `Dockerfile.listener`
- Responsibilities: Connect all active senders to Telegram, register event handlers for incoming messages, run AI reply logic

**Background Workers (inside API process):**
- `app/services/queue.py` → `QueueWorker` — outbound message delivery
- `app/services/warmup.py` → `WarmupWorker` — account warmup via AI-generated inter-account dialogs

## Error Handling

**Strategy:** Exception catching at worker level and service level; errors logged and persisted to DB; HTTP layer returns structured error dicts (not raises for business errors in queue routes)

**Patterns:**
- `FloodWaitError`: caught in both queue worker and listener; items rescheduled by exact Telegram-specified delay. Hard threshold (≥300s) triggers pause of ALL pending items for the sender.
- `PEER_FLOOD`: all pending items paused 24h; logged as CRITICAL
- `SessionAuthError`: sender deactivated in DB, all pending items failed
- Queue items: up to 3 retry attempts (`MAX_ATTEMPTS = 3`) with exponential backoff before permanent `failed` status
- Startup recovery: `recover_stuck_jobs()` resets any items stuck in `processing` for >10 minutes
- Webhook callbacks: fire-and-forget via `asyncio.create_task`, never raises

## Cross-Cutting Concerns

**Logging:**
- `logging.basicConfig` configured at `app/main.py` and `app/services/listener.py`
- All modules use `logger = logging.getLogger(__name__)`
- `API_KEY` and session strings not logged (encrypted at DB level)

**Validation:**
- Pydantic `BaseModel` with `model_validator` at API boundary (`app/schemas/__init__.py`)
- Constraint: either `sender` or `ai_context_id` must be present on send requests

**Authentication:**
- Single global API key checked via `X-API-Key` header
- `verify_api_key` dependency in `app/routers/auth.py`, applied per-router via `Depends()`
- No per-user auth, no JWT — single shared key for all callers
- No multi-tenancy: no `workspace_id` anywhere in models

**Session Security:**
- All Telegram session strings encrypted with Fernet before storing in `senders.session_string`
- Encryption key loaded from `ENCRYPTION_KEY` env var

---

*Architecture analysis: 2026-04-02*
*Update when major patterns change*
