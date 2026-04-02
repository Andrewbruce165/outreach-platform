# Codebase Structure

**Analysis Date:** 2026-04-02

## Directory Layout

```
outreach-platform/
├── app/                    # All Python application code
│   ├── models/             # SQLAlchemy ORM models
│   ├── routers/            # FastAPI route handlers
│   ├── schemas/            # Pydantic request/response models
│   ├── services/           # Business logic and background workers
│   ├── utils/              # Shared utilities (currently empty)
│   ├── config.py           # Settings via pydantic-settings
│   ├── database.py         # Async engine, session factory, get_db()
│   └── main.py             # FastAPI app, lifespan, router registration
├── migrations/             # Raw SQL migration files (numbered)
├── docs/                   # Internal documentation
├── .planning/              # GSD planning files (not committed to production)
│   └── codebase/           # Codebase analysis documents
├── Dockerfile              # API container (uvicorn)
├── Dockerfile.listener     # Listener container (python -m app.services.listener)
├── docker-compose.yml      # Three services: db, api, listener
├── requirements.txt        # Python dependencies
├── CLAUDE.md               # Project instructions for Claude Code
└── DOCS.md                 # Developer documentation
```

## Directory Purposes

**`app/models/`:**
- Purpose: All SQLAlchemy ORM model definitions
- Contains: Single file `__init__.py` with all models and enums
- Key files: `app/models/__init__.py` — defines `Sender`, `MessageLog`, `ContactCache`, `AIContext`, `MessageQueue`, `Conversation`, `WarmupPool`, `WarmupSession`, `WarmupMessage`, `ProxyPool`, `ContextContactAssignment`
- Subdirectories: None — all models in one file

**`app/routers/`:**
- Purpose: FastAPI route handlers (HTTP boundary)
- Contains: One file per domain; all use `APIRouter` with prefix and `Depends(verify_api_key)`
- Key files:
  - `app/routers/send.py` — `POST /api/v1/send`, `POST /api/v1/send/file`, `POST /api/v1/send/batch`
  - `app/routers/senders.py` — CRUD for sender accounts
  - `app/routers/conversations.py` — inbox, conversation state management
  - `app/routers/contexts.py` — AI context CRUD
  - `app/routers/onboarding.py` — Telegram account onboarding (SMS/2FA/QR)
  - `app/routers/queue.py` — queue status and management endpoints
  - `app/routers/check_contacts.py` — phone number validation via checker account
  - `app/routers/warmup.py` — warmup pool management
  - `app/routers/proxy_pool.py` — proxy pool management
  - `app/routers/health.py` — `GET /api/v1/health`
  - `app/routers/auth.py` — `verify_api_key` dependency (not a router itself)

**`app/schemas/`:**
- Purpose: Pydantic models for API I/O
- Contains: All request/response schemas in one file
- Key files: `app/schemas/__init__.py` — all Pydantic models (`SendMessageRequest`, `SendFileRequest`, `EnqueueResponse`, `BatchSendRequest`, etc.)
- Subdirectories: None

**`app/services/`:**
- Purpose: Core business logic, background workers, external service clients
- Contains: One file per concern
- Key files:
  - `app/services/queue.py` — `QueueWorker` class + `enqueue_message()`, `enqueue_file()` helpers
  - `app/services/listener.py` — standalone listener process; Telethon event loop per sender
  - `app/services/telegram.py` — Telethon client factory, `send_message()`, `send_file()`, device fingerprint
  - `app/services/ai_engine.py` — `AIEngine` class, OpenAI GPT integration
  - `app/services/warmup.py` — `WarmupWorker` class; AI-generated warmup dialogs
  - `app/services/rotation.py` — `get_or_assign_sender()` for context-based sender selection
  - `app/services/encryption.py` — Fernet encrypt/decrypt for session strings
  - `app/services/checker.py` — phone number validation using checker Telegram account

**`app/utils/`:**
- Purpose: Shared helper utilities
- Contains: Currently empty (`__init__.py` only)

**`migrations/`:**
- Purpose: Database schema migration history
- Contains: Raw SQL files, numbered sequentially
- Key files: `001_add_unique_constraint_messages.sql` through `011_sender_auth_status.sql`
- Pattern: Always idempotent (`IF NOT EXISTS`); never use Alembic; next migration is `012_*.sql`
- Committed: Yes

**`docs/`:**
- Purpose: Developer and operational documentation
- Contains: Markdown files
- Committed: Yes

## Key File Locations

**Entry Points:**
- `app/main.py` — FastAPI app, lifespan startup/shutdown, router registration
- `app/services/listener.py` — standalone listener process (run as `__main__`)

**Configuration:**
- `app/config.py` — `Settings` class (pydantic-settings); all env vars defined here
- `docker-compose.yml` — container definitions, env var passing, service dependencies
- `Dockerfile` — API container build
- `Dockerfile.listener` — Listener container build
- `requirements.txt` — Python dependencies (no lockfile)

**Core Logic:**
- `app/services/queue.py` — outbound rate-limited send loop; all rate limit constants
- `app/services/telegram.py` — Telethon client abstraction; device fingerprint
- `app/services/ai_engine.py` — AI response generation
- `app/services/listener.py` — inbound message handling, AI reply dispatch

**Models:**
- `app/models/__init__.py` — all ORM models in one file; import from here

**Schemas:**
- `app/schemas/__init__.py` — all Pydantic I/O models; import from here

**Auth:**
- `app/routers/auth.py` — `verify_api_key` FastAPI dependency

**Database:**
- `app/database.py` — `AsyncSessionLocal`, `get_db()` dependency, `init_db()`

**Testing:**
- Not present — no test files exist in the codebase

## Naming Conventions

**Files:**
- `snake_case.py` for all Python modules
- Singular nouns for service files: `telegram.py`, `encryption.py`, `rotation.py`
- Plural nouns for router files matching their domain: `senders.py`, `conversations.py`, `contexts.py`

**Directories:**
- Lowercase plural for collections: `models/`, `routers/`, `schemas/`, `services/`

**Migrations:**
- `{NNN}_{description}.sql` (zero-padded 3 digits): `011_sender_auth_status.sql`

**Models:**
- `PascalCase` for SQLAlchemy model classes: `Sender`, `MessageQueue`, `AIContext`
- `snake_case` for table names via `__tablename__`: `"senders"`, `"message_queue"`, `"ai_contexts"`

**Enums:**
- `PascalCase` class name: `QueueItemStatus`, `MessageType`
- `lowercase` enum values: `QueueItemStatus.pending`, `MessageType.sent`

**Routes:**
- All under `/api/v1/` prefix
- Plural resource names: `/api/v1/senders`, `/api/v1/conversations`
- Kebab-case for multi-word: `/api/v1/check-contacts`, `/api/v1/proxy-pool`

## Where to Add New Code

**New API endpoint:**
- Router: `app/routers/{domain}.py`
- Pydantic schemas: `app/schemas/__init__.py`
- Business logic: `app/services/{domain}.py`
- Register router in: `app/main.py` via `app.include_router()`
- Apply auth: add `_: str = Depends(verify_api_key)` to handler signature

**New ORM model:**
- Add class to `app/models/__init__.py`
- Write migration: `migrations/{NNN}_{description}.sql` (next: `012_`)
- Export via existing `from app.models import ...` imports

**New background worker:**
- Implement in `app/services/{worker_name}.py` following `QueueWorker` / `WarmupWorker` pattern (asyncio task with `start()`/`stop()`)
- Start/stop in lifespan in `app/main.py`

**New configuration value:**
- Add field to `Settings` class in `app/config.py`
- Add to `docker-compose.yml` environment blocks for relevant services

**Database migration:**
- Create `migrations/012_{description}.sql`
- Use `IF NOT EXISTS` / `IF EXISTS` for idempotency
- Never use Alembic — raw SQL only

**Shared utility:**
- Add to `app/utils/` (currently empty — create new file there)

## Special Directories

**`migrations/`:**
- Purpose: Schema history; applied manually or at deploy
- Source: Hand-written raw SQL
- Committed: Yes — never auto-generated

**`.planning/`:**
- Purpose: GSD workflow files (PROJECT.md, ROADMAP.md, phase plans, codebase analysis)
- Source: Written by Claude Code during planning sessions
- Committed: Yes (tracked in git)

**`__pycache__/`:**
- Purpose: Python bytecode cache
- Generated: Yes, by Python interpreter
- Committed: No (in `.gitignore`)

---

*Structure analysis: 2026-04-02*
*Update when directory structure changes*
