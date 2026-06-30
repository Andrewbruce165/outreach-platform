# Coding Conventions

**Analysis Date:** 2026-06-30

## Language and Communication

**Code:** English only (variables, functions, classes, comments, docstrings, commit messages).
**Developer communication:** Russian (per `CLAUDE.md`).
**Docstrings:** English prose; module-level docstrings describe purpose and endpoints.

## Naming Patterns

**Files:**
- Routers: `snake_case.py` under `app/routers/` — one file per resource domain (`campaigns.py`, `senders.py`, `warmup.py`)
- Services: `snake_case.py` under `app/services/` — one file per concern (`queue.py`, `ai_engine.py`, `contact_check_worker.py`)
- Tests: `test_{feature_or_phase_or_migration}.py` under `tests/` — prefixed by phase when feature-tied (`test_phase5_inbox.py`, `test_migration_013.py`)
- Migrations: `NNN_short_name.sql` (3-digit zero-padded prefix, lexical sort determines run order, e.g. `012_workspace.sql`)

**Functions:**
- `snake_case` everywhere: `enqueue_message`, `get_active_senders`, `_build_outreach_schema`
- Private helpers prefixed `_`: `_assert_test_dsn`, `_allowed_origin`, `_cors_headers`, `_validate_max_new_dialogs`
- Async background workers suffixed `_worker`: `queue_worker`, `warmup_worker`, `contact_check_worker`

**Variables:**
- `snake_case`: `sender_id`, `workspace_id`, `recipient_phone`
- Constants: `UPPER_SNAKE_CASE` — `MIN_SEND_INTERVAL`, `QUEUE_TICK_BATCH`, `FLOOD_HARD_THRESHOLD`

**Classes:**
- ORM models: `PascalCase` matching table concept (`Workspace`, `CampaignSender`, `WorkspaceApiKey`)
- Pydantic schemas: `PascalCase` with `Request`/`Response`/`Create`/`Update` suffixes (`CampaignCreate`, `SenderUpdate`, `PoolHealth`)
- Enums: `PascalCase` (`QueueItemStatus`, `MessageType`)

**Routes:**
- All under `/api/v1/` prefix
- Resource groups: `router = APIRouter(prefix="/api/v1/{resource}", tags=["{resource}"])`

## Async Everywhere

**Rule:** Every DB operation, HTTP call, and Telegram interaction uses `async/await`. No `time.sleep()`, no `requests`, no blocking I/O.

```python
# Correct
async def enqueue_message(db: AsyncSession, ...) -> dict:
    result = await db.execute(select(Sender).where(...))
    ...

# Forbidden
import requests  # never — use httpx
import time; time.sleep(5)  # never — use asyncio.sleep
print("debug")  # never — use logger.info/logger.warning
```

**DB sessions:** Always `AsyncSession` from `app.database.AsyncSessionLocal` or `get_db()`.
```python
from app.database import AsyncSessionLocal

async with AsyncSessionLocal() as session:
    result = await session.execute(...)
```

**HTTP client:** Always `httpx.AsyncClient` for outbound HTTP (used in `app/services/webhook_notify.py` and `app/services/ai_engine.py`).

## Logging

**Framework:** Python `logging` module only. Never `print()`.

**Setup (module level):**
```python
import logging
logger = logging.getLogger(__name__)
```

**Root config** (in `app/main.py`):
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
```

**Patterns:**
- `logger.info("Starting %s...", name)` — startup/normal events, use `%s` format args (not f-strings)
- `logger.warning("Validation error on %s %s: %s", method, path, errors)` — expected-but-notable
- `logger.error("[migrate] FAIL %s: %s", version, exc)` — recoverable errors
- `logger.exception("Unhandled exception on %s %s", method, path)` — unhandled exceptions (includes traceback)
- Never log API keys, session strings, or JWT payloads

## Error Handling

**HTTP errors:** Raise `fastapi.HTTPException` with a structured `detail` dict:
```python
raise HTTPException(
    status_code=422,
    detail={
        "code": "NEW_DIALOG_LIMIT_EXCEEDS_HARD_CAP",
        "field": "max_new_dialogs_per_day",
        "value": value,
        "hard_cap": DIALOG_LIMIT_HARD_CAP,
    },
)
```

**Global handlers** in `app/main.py`:
- `RequestValidationError` → 422 with `{"detail": {"code": "VALIDATION_ERROR", "errors": [...]}}`
- `StarletteHTTPException` → preserves status with CORS headers
- `Exception` (fallback) → 500 with `{"detail": "Internal Server Error", "code": "INTERNAL_ERROR"}`

**FloodWait retry** (Telethon, in `app/services/queue.py`):
```python
from telethon.errors import FloodWaitError

try:
    await client.send_message(...)
except FloodWaitError as exc:
    retry_after = exc.seconds
    if retry_after >= FLOOD_HARD_THRESHOLD:
        # reschedule ALL pending tasks for this sender
        ...
    else:
        # reschedule only this item
        item.scheduled_at = now + timedelta(seconds=retry_after)
```
**Rule:** Never break FloodWait retry logic without explicit discussion. Intervals are empirically tuned.

**SpamBan pattern:** After FloodWait/spam event, write to `sender_restriction_events` via `app/services/restriction_audit.py::record_restriction_event`.

## Pydantic Schemas

**Location:** `app/schemas/__init__.py` (all schemas in one file).

**Base pattern:**
```python
from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID

class CampaignCreate(BaseModel):
    name: str = Field(..., max_length=100)
    agent_id: UUID
    folder_id: Optional[UUID] = None
```

**AliasChoices for Lovable frontend quirks:**
When the Lovable-generated client sends a field under a different name than the canonical spec, use `AliasChoices` — do not change the canonical field name:
```python
from pydantic import AliasChoices

class SendMessageFromUIRequest(BaseModel):
    message: str = Field(
        ...,
        validation_alias=AliasChoices("message", "message_text"),
        # Lovable sends "message_text"; canonical is "message"
    )
```
Current instance: `app/schemas/__init__.py::SendMessageFromUIRequest` — accepts both `message` and `message_text`.

**Config settings** in `app/config.py`:
```python
class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", case_sensitive=False)
    some_knob: int = Field(
        default=300,
        validation_alias="SOME_KNOB_ENV_VAR",
        description="...",
    )
```
`validation_alias` maps the env var name (UPPER_CASE) to the Python attribute name (snake_case).

## ORM Models

**Location:** `app/models/__init__.py` (all models in one file).

**Pattern:**
```python
from sqlalchemy import Column, String, UUID
from app.database import Base
import uuid

class Workspace(Base):
    __tablename__ = "workspaces"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Use server_default=func.now() for timestamps (NOT default=datetime.now)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

**Critical:** `default=uuid.uuid4` is Python-side only — raw SQL INSERTs omitting `id` will fail unless the column also has `server_default=text("gen_random_uuid()")`. See `ORM default= vs server_default= drift` in project memory.

**Relationships:** Use SQLAlchemy `relationship()` sparingly. Workspace-scoped queries always add `.where(Model.workspace_id == ctx.workspace_id)`.

## Migrations

**Convention:**
- File: `migrations/NNN_short_name.sql` (3-digit prefix, lexical order = run order)
- Every statement **must be idempotent**: `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `ON CONFLICT DO NOTHING`
- CHECK constraints: use `DO $$ BEGIN ALTER TABLE ... ADD CONSTRAINT ...; EXCEPTION WHEN duplicate_object THEN NULL; END $$;` if PostgreSQL version doesn't support `IF NOT EXISTS` on constraints
- Wrap logically-atomic migrations in `BEGIN; ... COMMIT;`
- Bootstrap: `migrations/_schema_migrations.sql` (underscore prefix sorts first, never tracked)

**Auto-applier** runs at every API startup via `app/database.py::_apply_migrations()`:
- Uses `pg_advisory_lock` to prevent race on parallel container startup
- Failing migration = API does not start (fail-fast, not half-applied)
- Applied versions tracked in `schema_migrations` table

**Adding a migration:** drop `NNN_short_name.sql` in `migrations/`, then `docker compose up -d --build api`.

## Import Organization

**Order in source files:**
1. Standard library (`os`, `logging`, `asyncio`, `uuid`, `datetime`)
2. Third-party (`fastapi`, `sqlalchemy`, `pydantic`, `telethon`, `httpx`)
3. Internal app (`from app.config import get_settings`, `from app.models import Sender`)

**In-body imports:** Used selectively in tests and services to defer costly imports until needed (avoids paying collection-time costs for Phase-1 tests that don't need Phase-4 models).

## Auth Pattern

Every router endpoint that touches workspace data depends on `auth_dep`:
```python
from app.utils.auth import AuthCtx, auth_dep
from fastapi import Depends

@router.get("/{id}")
async def get_campaign(id: UUID, ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Campaign).where(Campaign.id == id, Campaign.workspace_id == ctx.workspace_id)
    )
```

`AuthCtx` carries `workspace_id`, `user_id`, `source` (`jwt` or `api_key`), `role`.

Two auth paths (see `app/utils/auth.py`):
- `Authorization: Bearer <Supabase JWT>` — ES256 via JWKS (primary) or HS256 legacy
- `X-Workspace-Key: wsk_<token>` — bcrypt-verified API key with 5-minute in-process cache

## Rate Limits (DO NOT CHANGE without discussion)

Constants in `app/services/queue.py` — empirically tuned, protected by CLAUDE.md:
- `MIN_SEND_INTERVAL = 20` / `MAX_SEND_INTERVAL = 55` (seconds)
- `MAX_NEW_CONTACTS_PER_HOUR = 15`
- `LONG_PAUSE_EVERY_MIN/MAX = 12/25` messages → `LONG_PAUSE_MIN/MAX_SECS = 180/600`
- `FLOOD_HARD_THRESHOLD = 300` seconds
- DB defaults: `rate_per_min=4`, `rate_per_hour=20`, `rate_per_day=150`

## Comments

**Module-level docstrings:** All routers and services have a docstring listing endpoints, phase/decision IDs, and key design notes.

**Inline comments:** Used for non-obvious decisions, citing phase plan IDs (e.g. `# Phase 10 D-13`), and recording tradeoffs ("empirically tuned — not to be changed").

**Warning annotations:** `# NB:`, `# IMPORTANT:`, `# CRITICAL:` for constraints callers must respect.

---

*Convention analysis: 2026-06-30*
