# Testing Patterns

**Analysis Date:** 2026-06-30

## Test Framework

**Runner:** pytest 8.x + pytest-asyncio 0.23+
**Config:** `pyproject.toml` (project root)
**Assertion library:** pytest built-ins + `sqlalchemy.exc.IntegrityError` for DB constraint tests

**Run Commands:**

```bash
# ONLY valid way to run tests — test-overlay required
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest

# With specific test file
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_router.py

# With verbose output
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -v

# FORBIDDEN — DATABASE_URL points at prod inside the api container; conftest guard raises RuntimeError
docker compose run --rm api pytest
```

**pytest-asyncio config** (from `pyproject.toml`):
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
asyncio_default_test_loop_scope = "session"
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "-v --tb=short --strict-markers"
markers = ["integration: integration tests touching the database"]
```

`asyncio_mode = "auto"` means all `async def test_*` functions are automatically treated as asyncio tests — no `@pytest.mark.asyncio` decorator needed (though `pytestmark = pytest.mark.asyncio` is still used by most files for explicitness).

## Test File Organization

**Location:** All tests in `tests/` (co-located with app, not inside `app/`)

**Naming:**
- Feature/service tests: `test_{feature}.py` — `test_queue_enqueue.py`, `test_senders.py`, `test_checker.py`
- Migration idempotency tests: `test_migration_{NNN}.py` — `test_migration_013.py`, `test_migration_032.py`
- Phase-grouped tests: `test_phase{N}_{topic}.py` — `test_phase5_inbox.py`, `test_phase5_1_campaign_v2.py`

**Structure:**
```
tests/
├── conftest.py              # All shared fixtures (prod-guard, DB setup, factories)
├── utils/
│   ├── __init__.py
│   └── openai_mocks.py      # Mock OpenAI response builder (used by ai_engine tests)
└── test_*.py                # ~99 test files
```

## Production Guard (CRITICAL)

**2026-05-26 incident:** Running `docker compose run --rm api pytest` used DATABASE_URL from `docker-compose.yml` pointing at prod. `conftest._setup_database` executed `DROP SCHEMA public CASCADE` against live `outreach_platform`. All 22 relations were rebuilt at the same timestamp.

**The guard** in `tests/conftest.py:40-64`:
```python
_ALLOWED_TEST_DSN_MARKERS = (
    "outreach_test",   # explicit test DB
    "_test@", "_test/",
    "/test_",
    "@localhost",
    "@127.0.0.1",
)

def _assert_test_dsn(dsn: str, action: str = "DESTRUCTIVE TEST SETUP") -> None:
    if not any(marker in dsn for marker in _ALLOWED_TEST_DSN_MARKERS):
        raise RuntimeError(
            f"REFUSING TO RUN {action} AGAINST {dsn!r}. ..."
        )
```
Called at session fixture start AND teardown. Never bypass this guard.

## Test Overlay (docker-compose.test.yml)

`docker-compose.test.yml` overrides the api service to:
1. Spin up an ephemeral `db-test` container (`pgvector/pgvector:pg16`) with `tmpfs` storage — data never persists
2. Override `DATABASE_URL` to point at `db-test:5432/outreach_test`
3. Mount `./app`, `./tests`, `./migrations`, `./pyproject.toml` into the api container so no rebuild is needed for code edits

```yaml
services:
  db-test:
    image: pgvector/pgvector:pg16
    command: ["postgres", "-c", "fsync=off", "-c", "synchronous_commit=off"]
    tmpfs:
      - /var/lib/postgresql/data
    environment:
      POSTGRES_DB: outreach_test

  api:
    environment:
      DATABASE_URL: postgresql+asyncpg://outreach_user:outreach_test_pass@db-test:5432/outreach_test
    volumes:
      - ./app:/app/app
      - ./tests:/app/tests
      - ./migrations:/app/migrations
      - ./pyproject.toml:/app/pyproject.toml:ro
```

**pgvector image required** (Phase 16): the test DB must have the `vector` extension. Plain `postgres:16` will fail with `type "vector" does not exist` during schema setup.

## Session-Scoped DB Setup

The `_setup_database` session fixture in `tests/conftest.py:244-264` runs ONCE per test session:

1. Verifies DSN against `_ALLOWED_TEST_DSN_MARKERS`
2. Drops and recreates the `public` schema
3. Runs `CREATE EXTENSION IF NOT EXISTS vector` (Phase 16 requirement — before `create_all`)
4. Calls `Base.metadata.create_all` to build ORM tables
5. Applies migrations `012` through `041` in explicit named order (hardcoded list, NOT a glob)
6. Adds `server_default=gen_random_uuid()` on UUID PK columns and warmup column defaults (so raw-SQL tests work without ORM)
7. Sets DB-level defaults for `ai_contexts` columns (Phase 11 migration 032 interaction)

**Teardown:** `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`

**Important:** migrations `001-011` are NOT replayed in tests — they are assumed covered by ORM `create_all`. Only `012+` are applied.

## Migrations DB (Separate Throwaway DB)

Migration idempotency tests (`test_migration_013.py`, `test_migration_032.py`, etc.) use a **dedicated throwaway DB** `outreach_test_migrations` — isolated from the shared session DB to prevent DDL commits from poisoning other tests.

The `migrations_raw_dsn` session fixture (`conftest.py:267-309`) builds this DB identically to the main test DB, then drops it at teardown.

```python
# Usage in migration idempotency test:
async def test_constraint_idempotent(migrations_raw_dsn: str):
    conn = await asyncpg.connect(dsn=migrations_raw_dsn)
    sql = (PROJECT_ROOT / "migrations" / "013_phase2.sql").read_text()
    await conn.execute(sql)  # re-apply — must not raise
```

## Test Structure Patterns

**Module-level asyncio marking:**
```python
import pytest
pytestmark = pytest.mark.asyncio  # marks every async def in the file
```

**Fixture injection:** All standard fixtures injected by name:
```python
async def test_example(
    async_db_session: AsyncSession,
    async_client: AsyncClient,
    test_workspace,
    valid_supabase_jwt,
):
    ...
```

**Typical test shape:**
```python
async def test_enqueue_message(async_db_session, test_sender_factory, test_agent_factory):
    from app.services.queue import enqueue_message  # in-body import

    sender = await test_sender_factory(slug="enq-1")
    agent = await test_agent_factory()

    result = await enqueue_message(
        db=async_db_session,
        workspace_id=sender.workspace_id,
        sender_id=sender.id,
        ...
    )
    assert "queue_id" in result

    # Verify via raw SQL
    row = await async_db_session.execute(
        text("SELECT extra_data FROM message_queue WHERE id = :qid"),
        {"qid": result["queue_id"]},
    )
    assert row.fetchone()[0].get("ai_context_id") == str(agent.id)
```

**In-body imports:** Services are imported inside the test function body, not at module level. This keeps test collection fast and lets tests fail at import time only if the specific phase feature is exercised.

## Fixtures (conftest.py)

All shared fixtures live in `tests/conftest.py`. Key fixtures:

| Fixture | Scope | Purpose |
|---|---|---|
| `_setup_database` | session, autouse | Build test schema, apply migrations |
| `migrations_raw_dsn` | session | Throwaway DB for migration re-apply tests |
| `async_db_session` | function | `AsyncSession` with rollback-on-teardown |
| `async_client` | function | `httpx.AsyncClient` via `ASGITransport` (no real network) |
| `test_workspace` | function | Creates a `Workspace` row |
| `test_sender_factory` | function | Factory returning `Sender`; pass keyword overrides |
| `test_checker` | function | Sender with `role="checker"` |
| `test_folder` | function | Creates a `Folder` row |
| `test_agent_factory` | function | Factory for `AIContext` |
| `test_contacts_factory` | function | Factory for `Contact` rows (batch-capable) |
| `test_campaign_factory` | function | Factory creates draft campaign via raw SQL |
| `test_queue_item_factory` | function | Seeds `message_queue` row + optional CCA/conversation |
| `test_conversation_factory` | function | Inserts `conversations` row via raw SQL |
| `test_message_factory` | function | Inserts `messages` rows |
| `test_running_campaign_factory` | function | Campaign + N senders, status=`running` |
| `valid_supabase_jwt` | function | Callable factory — HS256 JWT signed with test secret |
| `es256_supabase_jwt` | function | Callable factory — ES256 JWT for JWKS auth tests |
| `expired_supabase_jwt` | function | Pre-expired JWT (exp=1) |
| `mock_telethon_client` | function | Async mock Telethon client for Phase 14 checker tests |

**Factory pattern — override via kwargs:**
```python
sender = await test_sender_factory(role="checker", slug="my-checker", lifecycle_status="paused")
contact = await test_contacts_factory(count=5, tg_status="registered")
agent = await test_agent_factory(tone_preset="Friendly", system_prompt="Custom prompt")
```

**Session isolation:** `async_db_session` always rolls back after each test — no test leaves permanent data in the shared test DB. Exception: migration idempotency tests use `migrations_raw_dsn` directly with asyncpg (DDL commits are real on that dedicated DB).

## HTTP Client Testing (Router Tests)

```python
from httpx import ASGITransport, AsyncClient

# async_client fixture wraps the FastAPI app — no real HTTP, no real Telegram
async def test_list_campaigns(async_client, valid_supabase_jwt, async_db_session, test_workspace):
    await _bind(async_db_session, test_workspace.id, "u-list")

    resp = await async_client.get(
        "/api/v1/campaigns",
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-list')}"},
    )
    assert resp.status_code == 200
    assert "items" in resp.json()
```

Auth helper pattern (common in router test files):
```python
def _auth_headers(jwt_factory, sub: str = "router-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}

def _bind(db, ws_id, uid):
    # Inserts user_workspaces row to make JWT sub resolve to workspace
    await db.execute(text("INSERT INTO user_workspaces ..."), ...)
    await db.commit()
```

## Mocking

**Primary tools:**
- `unittest.mock.AsyncMock` — for async callables (Telethon client, DB methods)
- `unittest.mock.MagicMock` — for sync callables (Telethon event objects)
- `monkeypatch` — for replacing module attributes

**OpenAI mocking** (`tests/utils/openai_mocks.py`):
```python
from tests.utils.openai_mocks import make_openai_response, patched_openai_client

def test_ai_response(monkeypatch):
    patched_openai_client(
        monkeypatch,
        make_openai_response(text_content="Hello"),
    )
    # ai_engine.client.chat.completions.create is now patched
```

**Telethon client mock** (`conftest::mock_telethon_client`):
```python
async def test_checker(mock_telethon_client):
    client = mock_telethon_client
    client.set_response("ResolvePhoneRequest", None)
    client.set_response("ImportContactsRequest", _Imported())
    result = await resolve_phone_with_fallback(client, phone="+79990001234")
    called = [name for name, _ in client.calls]
    assert "ImportContactsRequest" in called
```

**monkeypatch for listener/services:**
```python
async def test_reconcile(monkeypatch):
    from app.services import listener as listener_mod
    listener = listener_mod.TelegramListener()
    listener.get_active_senders = AsyncMock(return_value=[...])
    listener.start_client = AsyncMock()
    ...
```

**What to mock:**
- Telegram network (Telethon client, all `await client(...)` calls)
- OpenAI API (`ai_engine.client.chat.completions.create`)
- External webhooks (`httpx.AsyncClient.post` when testing fire-and-forget)

**What NOT to mock:**
- PostgreSQL (use the ephemeral test DB via overlay)
- FastAPI routing (use `async_client` with `ASGITransport`)
- Pydantic validation (test it directly)

## DB Constraint Testing

Migration tests assert schema correctness by querying `information_schema` and triggering `IntegrityError`:

```python
async def test_invalid_role_rejected(async_db_session):
    with pytest.raises(IntegrityError):
        await async_db_session.execute(
            text("INSERT INTO senders (..., role) VALUES (..., 'invalid_role')")
        )
        await async_db_session.commit()
```

## Current Test Baseline

**Status:** GREEN as of 2026-06-25 (per project memory).
- **Total test files:** ~99 test files
- All tests pass via the test-overlay command
- Migration re-apply tests isolated on `outreach_test_migrations` DB
- One previously-found product bug (422→500 handler) was fixed but not yet deployed to prod at time of baseline

## Error: `asyncio` loop conflicts

If tests fail with `"cannot perform operation: another operation is in progress"` or `"Future attached to a different loop"`, the cause is session-scoped fixtures on different event loops. The fix is already in `pyproject.toml`:
```toml
asyncio_default_fixture_loop_scope = "session"
asyncio_default_test_loop_scope = "session"
```
and `pyproject.toml` **must be mounted** into the test container — see `docker-compose.test.yml` volumes. Without the mount, pytest-asyncio defaults to function-scope loops and the session-scoped DB engine binds to a different loop.

---

*Testing analysis: 2026-06-30*
