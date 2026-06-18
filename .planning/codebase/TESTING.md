# Testing Patterns

**Analysis Date:** 2026-06-18

---

## Backend (Python)

### Test Framework

**Runner:** pytest with pytest-asyncio
**Config:** `pyproject.toml` at `/root/apps/aimly/tg-outreach/pyproject.toml`

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
asyncio_default_test_loop_scope = "session"
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --tb=short --strict-markers"
```

**Key packages:** `pytest-asyncio`, `httpx` (ASGITransport), `asyncpg`, `python-jose`

**Run commands:**
```bash
# ONLY valid invocation — never omit the test overlay
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest

# Run a specific test file
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_router.py

# Run with -k filter
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -k "test_list_campaigns"
```

**CRITICAL — Test Overlay Rule:**
Never run `docker compose run --rm api pytest` without the `-f docker-compose.test.yml` overlay. Without the overlay, `DATABASE_URL` resolves from `docker-compose.yml` and points at the **production** `outreach_platform` database. The session-scoped `_setup_database` fixture executes `DROP SCHEMA public CASCADE` — this destroyed the entire prod schema on 2026-05-26.

The overlay file is at `/root/apps/aimly/tg-outreach/docker-compose.test.yml`. It:
- Spins up an ephemeral `db-test` postgres in `tmpfs` (fsync off, no persistence)
- Overrides `DATABASE_URL` to `postgresql+asyncpg://outreach_user:outreach_test_pass@db-test:5432/outreach_test`
- Mounts `./app`, `./tests`, `./migrations`, `./pyproject.toml` into the api container (allows in-source edits without image rebuild)
- The `db-test` container has no name and no volume — it disappears after the run

**Conftest guard:** `tests/conftest.py` lines 57-77 implement a hard `RuntimeError` if `DATABASE_URL` does not contain one of `("outreach_test", "_test@", "_test/", "/test_", "@localhost", "@127.0.0.1")`. This is belt-and-suspenders; the overlay is still required.

### Test File Organization

**Location:** All tests in `tests/` directory (flat, co-located with project root)

**Naming pattern:**
- Feature tests: `test_<resource>.py` — `test_campaigns_model.py`, `test_contacts.py`, `test_folders.py`
- Router integration tests: `test_<resource>_router.py` — `test_campaign_router.py`
- Migration tests: `test_migration_NNN.py` — `test_migration_016.py`, `test_migration_015.py`
- Phase-tagged tests: `test_phase5_<area>.py` — `test_phase5_inbox.py`, `test_phase5_analytics.py`
- Phase 05.1 tests: `test_phase5_1_<area>.py` — `test_phase5_1_agents_v2.py`, `test_phase5_1_core_value_e2e.py`

**Utilities:** `tests/utils/openai_mocks.py` — mock OpenAI Chat Completions response builder

### Test Structure

**All test functions are async and marked with module-level `pytestmark`:**
```python
import pytest

pytestmark = pytest.mark.asyncio

async def test_something(async_db_session, test_workspace):
    ...
```

**Suite organization pattern:**
```python
"""Module docstring describing what area is being tested and why."""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "some-user") -> dict:
    """Local helper — keeps test bodies readable."""
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    """Bind user to workspace — repeated helper pattern in router tests."""
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def test_feature_behavior(async_client, valid_supabase_jwt, async_db_session, test_workspace):
    # Arrange
    agent = await test_agent_factory(name="Test")
    await _bind(async_db_session, test_workspace.id, "u1")
    # Act
    r = await async_client.post("/api/v1/campaigns", json={...},
                                headers=_auth_headers(valid_supabase_jwt, "u1"))
    # Assert
    assert r.status_code == 201
    assert r.json()["name"] == "Test"
```

### Core Fixtures (from `tests/conftest.py`)

**Session-scoped setup:**
- `_setup_database` — destroys + recreates public schema; creates ORM tables via `Base.metadata.create_all`; applies migrations 012–018 and 026 via asyncpg; adds server-side UUID defaults. Runs once per pytest session. Contains the hard DSN guard.

**Function-scoped fixtures (each test gets fresh state via rollback):**
- `async_db_session` — `AsyncSession` with `await session.rollback()` on teardown; provides isolation without drop/recreate overhead
- `async_client` — `httpx.AsyncClient` with `ASGITransport(app=app)` — in-process, no real network
- `valid_supabase_jwt` — callable factory `(sub, email, exp, aud) -> str`; returns HS256 JWT signed with test secret
- `expired_supabase_jwt` — pre-built expired JWT (`exp=1`)
- `es256_supabase_jwt` — ES256 JWT factory using ephemeral EC P-256 key; JWKS cache seeded in-process
- `unsupported_alg_jwt` — HS512-signed JWT to test rejection of unsupported algorithms

**Factory fixtures (function-scoped, delegate to `async_db_session`):**
- `test_workspace` — creates a `Workspace` row
- `test_sender_factory` — callable: `await test_sender_factory(slug=..., role=..., **overrides) -> Sender`
- `test_checker` — pre-built checker-role sender
- `test_folder` — creates a `Folder` row in `test_workspace`
- `test_agent_factory` — callable: `await test_agent_factory(name=..., system_prompt=...) -> AIContext`
- `test_contacts_factory` — callable: `await test_contacts_factory(count=N, tg_status=..., **overrides) -> Contact | list[Contact]`
- `test_campaign_factory` — callable via raw SQL INSERT RETURNING: `await test_campaign_factory(name=..., status=...) -> dict`
- `test_running_campaign_factory` — composes `test_campaign_factory` + `test_sender_factory` + `attach_sender_to_campaign`
- `test_conversation_factory` — raw SQL INSERT: `await test_conversation_factory(status=..., ai_enabled=...) -> dict`
- `test_message_factory` — raw SQL INSERT: `await test_message_factory(conversation_id, count=N, direction=...) -> list[dict]`
- `attach_sender_to_campaign` — callable: inserts `campaign_senders` row with ON CONFLICT DO NOTHING

### Mocking

**Framework:** `unittest.mock.patch`, `pytest.monkeypatch`, `unittest.mock.AsyncMock`

**OpenAI mocking (`tests/utils/openai_mocks.py`):**
```python
from tests.utils.openai_mocks import make_openai_response, patched_openai_client

async def test_ai_generates_reply(monkeypatch, async_db_session):
    resp = make_openai_response(text_content="Hello from AI")
    patched_openai_client(monkeypatch, resp)
    # Now ai_engine.client.chat.completions.create returns resp
```

`patched_openai_client` supports multiple responses in order (for tool-call → final-reply two-pass flow).

**Telethon mocking:**
- `telegram_service` patched via `monkeypatch.setattr` on the service's methods
- `TelegramListener` instantiated directly in unit tests; `get_active_senders()` tested against real DB

**SQL spy pattern (for testing what queries are executed):**
```python
async def spy_execute(self, statement, *args, **kwargs):
    executed_sql.append(str(statement))
    return await original_execute(self, statement, *args, **kwargs)

monkeypatch.setattr(AsyncSession, "execute", spy_execute)
```
Used in `tests/test_health.py` to assert no `senders` table scan on public health endpoint.

**JWKS cache injection (for ES256 auth tests):**
```python
_auth_module._JWKS_CACHE["keys_by_kid"] = {jwk_dict["kid"]: jwk_dict}
_auth_module._JWKS_CACHE["fetched_at"] = time.time()
```
Injected directly into `app.utils.auth` module's in-process cache; cleared in fixture teardown.

### Migration Tests

Pattern in `test_migration_NNN.py`:
```python
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIG_016 = (PROJECT_ROOT / "migrations" / "016_phase4.sql").read_text()

async def test_migration_016_idempotent(async_db_session):
    """Applying twice does not fail."""
    conn = await async_db_session.connection()
    raw_conn = await conn.get_raw_connection()
    await raw_conn.driver_connection.execute(MIG_016)  # second apply
```

Migration tests verify:
- Idempotency (apply twice → no error)
- Schema shape (expected columns present via `information_schema.columns`)
- FK and CHECK constraints
- Data behavior after migration (nullability, defaults)

### What to Mock vs What Not to Mock

**Mock:**
- `ai_engine.client.chat.completions.create` — always mock in tests; never call real OpenAI
- `telegram_service.send_message` / `get_dialogs` / Telethon client methods — no real Telegram in tests
- Webhook HTTP calls (`httpx.AsyncClient.post` in `notify_signal`) — use `respx` or `AsyncMock`

**Do NOT mock:**
- PostgreSQL — all tests hit the real `outreach_test` ephemeral DB (no SQLite, no in-memory)
- FastAPI app itself — `ASGITransport` runs the real app in-process
- `app/utils/auth.py` JWT verification — real crypto, test fixtures produce real JWTs

### Test Types

**Unit tests (service-level):**
- Import service class/function directly; use `async_db_session` fixture
- Example: `tests/test_ai_engine.py`, `tests/test_template_render.py`, `tests/test_phone_normalization.py`

**Integration/router tests:**
- Use `async_client` to hit FastAPI endpoints; DB changes committed via `async_db_session`
- Example: `tests/test_campaign_router.py`, `tests/test_workspace_router.py`, `tests/test_phase5_inbox.py`

**Migration tests:**
- Verify schema shape post-migration; test idempotency
- Example: `tests/test_migration_016.py`, `tests/test_migration_015.py`

**E2E API tests:**
- Full user flow through multiple endpoints in sequence
- Example: `tests/test_phase5_1_core_value_e2e.py` — fires 6 telemetry events, asserts KPI

### Coverage

**Requirements:** No enforced coverage threshold

**View coverage:**
```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest --cov=app --cov-report=term-missing
```

---

## Frontend (TypeScript/React)

### Test Framework

**No tests exist in the frontend repository.**

The frontend at `/root/apps/aimly/aimly-tg-outreach/` contains no test files, no test runner configuration (`vitest.config.*`, `jest.config.*`), and no test-related devDependencies in `package.json`. There is no `tests/` or `__tests__/` directory.

All verification of frontend behavior is done manually via Lovable preview builds and the per-screen checklist in `AGENTS.md`:
- Lighthouse accessibility >= 90
- Reduced-motion CSS guard present
- Icon-only buttons have `aria-label`
- Empty states follow 4-element formula
- 401 redirects to `/login` with correct toast

**If adding frontend tests:** The project uses Vite + TanStack Start. The natural test stack would be Vitest + Testing Library, but this has not been set up.

---

*Testing analysis: 2026-06-18*
