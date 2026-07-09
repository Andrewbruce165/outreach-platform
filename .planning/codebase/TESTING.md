# Testing Patterns

**Analysis Date:** 2026-07-09

## Test Framework

**Runner:**
- `pytest>=8.0` + `pytest-asyncio>=0.23`
- Config: `pyproject.toml::[tool.pytest.ini_options]`
  - `asyncio_mode = "auto"` — every `async def test_...` runs automatically, no per-test `@pytest.mark.asyncio` is strictly required, but the codebase adds `pytestmark = pytest.mark.asyncio` at module top anyway (belt-and-suspenders / explicit-is-better convention — keep doing this in new test files).
  - `asyncio_default_fixture_loop_scope = "session"` and `asyncio_default_test_loop_scope = "session"` — deliberately shares one event loop for the whole session; this fixes cross-scope-fixture asyncpg "another operation is in progress" errors. **Do not** override loop scope per-test/module without understanding why this was pinned (see comment at top of `tests/conftest.py`).
  - `testpaths = ["tests"]`, `python_files = ["test_*.py"]`, `python_classes = ["Test*"]`, `python_functions = ["test_*"]` — standard discovery, no custom collection hooks.
  - `addopts = "-v --tb=short --strict-markers"` — unregistered `@pytest.mark.X` markers are a hard error; only marker currently registered is `integration` (`markers = ["integration: integration tests touching the database"]`), though in the ~135 test files sampled **no test currently uses `@pytest.mark.integration`** — nearly the whole suite touches the DB directly via fixtures without that marker. Don't assume the marker is used to select DB vs non-DB tests; it isn't, in practice.

**Assertion Library:** plain `assert` (pytest's rewritten asserts) — no `unittest.TestCase`, no third-party assertion library.

**Run Commands:**
```bash
# The ONLY correct way to run tests — test-overlay with an ephemeral tmpfs postgres.
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest

# Target a single file / test:
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_senders.py -k test_create_sender

# NEVER run this — DATABASE_URL resolves to prod, and _setup_database DROPs the schema:
docker compose run --rm api pytest        # <- FORBIDDEN, guarded but do not attempt
```
`docker-compose.test.yml` spins up `db-test` (image `pgvector/pgvector:pg16`, `tmpfs` data dir, `fsync=off`) and overrides the `api` service's `DATABASE_URL` to point at it, plus bind-mounts `tests/`, `pyproject.toml`, and `lovable-handoff/` (none of which are baked into the prod image). The whole overlay is torn down automatically when `run --rm` exits — nothing persists.

## Test File Organization

**Location:** all tests live flat in `tests/` (no subdirectory tree mirroring `app/`), except `tests/utils/` for shared test-only helpers (`tests/utils/openai_mocks.py`). ~135 `test_*.py` files at time of writing.

**Naming:** `tests/test_<feature_or_phase>.py`. Two overlapping naming styles coexist:
- Feature-named: `test_senders.py`, `test_checker.py`, `test_queue_enqueue.py`, `test_warmup_worker.py`.
- Phase-prefixed (used heavily for Phase 5.1 / Phase 5 work and anywhere multiple files land in the same phase): `test_phase5_1_agents_v2.py`, `test_phase5_1_agents_v2_router.py`, `test_phase5_analytics_since.py`. When a phase produces several related test files, prefix them all with `test_phase<N>[_<sub>]_` so they sort/group together — follow this when your phase adds >1 test file for related functionality.
- Migration re-application/idempotency tests are named `test_migration_<NNN>.py` (`test_migration_012.py` ... `test_migration_032.py`) and use the dedicated `migrations_raw_dsn` fixture (a throwaway DB, see below), never the shared session DB.

**Structure:** flat function-based tests (`async def test_...`) grouped by `# ─── Section ─── ` comment banners within a file, mirroring the router/service section-banner style from CONVENTIONS.md. `python_classes = ["Test*"]` is configured but class-based tests are not the dominant style in the files sampled — prefer flat functions unless grouping genuinely needs shared class-level state (rare here; fixtures cover that need instead).

## Test Structure

**Typical shape** (from `tests/test_checker_probe.py`):
```python
"""Module docstring explaining WHY these tests exist — phase, decision IDs, and
what they'll assert once the not-yet-built helper lands (RED-scaffold pattern)."""

from unittest.mock import AsyncMock, patch
import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_resolution_state(async_db_session):
    """Per-test cleanup of rows the SHARED session-scoped DB would otherwise leak
    into later tests (contacts_cache, ad-hoc pending contacts, etc.)."""
    yield
    await async_db_session.execute(text("DELETE FROM contacts_cache"))
    await async_db_session.commit()


async def test_two_misses_flags(async_db_session, test_workspace, test_checker):
    """Docstring states the invariant under test AND the decision ID (D-05) that
    justifies the specific threshold/behaviour being asserted."""
    from app.services.contact_check_worker import run_control_probe  # deferred import

    ...
    with patch("app.services.contact_check_worker.checker_service.probe_control",
               new=AsyncMock(return_value={...})):
        await run_control_probe(checker_id=checker_id)

    row = (await async_db_session.execute(text("SELECT ... FROM senders WHERE id = :id"),
                                           {"id": checker_id})).fetchone()
    assert row.restriction_status == "spam_limited"
```

**Patterns:**
- **Deferred (in-function) imports of the module under test** are common, especially for not-yet-built functionality during red/TDD phases ("Deferred in-body imports ... keep `--collect-only` clean"). This is a deliberate scaffold technique for phase-driven TDD, not sloppiness — use it when writing a genuinely-RED test against code that doesn't exist yet.
- Assertions verify **DB state directly via raw SQL** (`SELECT ... FROM senders WHERE id = :id`) rather than only checking a function's return value — the codebase treats persisted DB state as the real assertion target, especially for worker/background-job tests.
- Docstrings on individual test functions explain the business invariant + cite the decision ID (`D-05`, `D-07`, `POOL-08b`) that motivates the specific edge case, mirroring the app-code docstring convention.

## Mocking

**Framework:** `unittest.mock` (`AsyncMock`, `MagicMock`, `patch`) plus `monkeypatch` (pytest built-in fixture) — no `pytest-mock`, no `responses`/`httpretty` for HTTP.

**Telethon/Telegram client mocking** — `tests/conftest.py::mock_telethon_client` fixture:
```python
client = mock_telethon_client
client.set_response("ResolvePhoneRequest", _resolved_users(telegram_id=123))
client.set_response("ImportContactsRequest", _imported(telegram_id=123))
res = await client(SomeResolvePhoneRequest(phone="+79990001111"))
# client.calls records [(request_type_name, request_obj), ...] in call order
```
An `AsyncMock` subclass whose `__call__` dispatches on `type(request).__name__` against a response map — this is the canonical way to fake a Telethon RPC without a live session. Extend the response map via `set_response(...)`, assert call order/shape via `client.calls`.

**Bulk account-import Telethon client** — `tests/conftest.py::stub_import_telethon` fixture wraps a `MagicMock` that quacks like a connected `TelegramClient` (`connect`/`disconnect`/`is_connected`/`is_user_authorized`/`get_me` as `AsyncMock`s, `.session.save()` returning a real round-trippable `StringSession` string). Install into the module under test via `stub_import_telethon.install(module)`, which monkeypatches `make_telegram_client` on that module.

**OpenAI mocking** — `tests/utils/openai_mocks.py`:
```python
from tests.utils.openai_mocks import make_openai_response, patched_openai_client

resp = make_openai_response(text_content="Hello!", tool_calls=[{"name": "kb_search", "arguments": "{...}"}])
patched_openai_client(monkeypatch, resp)   # or multiple responses for a 2-pass tool-call flow
```
`patched_openai_client` monkeypatches `app.services.ai_engine.client.chat.completions.create` with an `AsyncMock(side_effect=...)` that pops queued responses in order — use this rather than hand-rolling a new OpenAI mock; it already mirrors the real SDK response shape (`.choices[0].message.content` / `.tool_calls[i].function.{name,arguments}`).

**What to mock:** external network boundaries only — Telegram/Telethon RPCs, OpenAI API calls, outbound webhooks. Never mock the database; tests hit a real (ephemeral) Postgres via `async_db_session`.

**What NOT to mock:** SQLAlchemy sessions, the FastAPI app itself (tested via a real in-process ASGI transport, see below), or internal service functions unless isolating one specific unit from a slow/networked dependency it calls.

## Fixtures and Factories

All in `tests/conftest.py` (single file, ~1280 lines, no `tests/conftest/*.py` split). Organized under `# ─── Phase N fixtures: ... ─── ` banners in the order features were built. Key fixtures:

- `async_db_session` (function-scoped) — a real `AsyncSession` against the shared test DB; wraps the test body and always `rollback()` + `close()` in `finally`. **Important caveat:** raw-SQL factory fixtures (below) call `db.commit()` internally, so their rows are **not** rolled back by this fixture — several factories (`test_conversation_factory`) implement their own explicit teardown/DELETE to avoid polluting later tests in the shared session-scoped DB.
- `async_client` — `httpx.AsyncClient` over `ASGITransport(app=app)`, i.e. **in-process** HTTP calls against the real FastAPI app with no real socket/network. This is the standard way to test routers end-to-end.
- Auth fixtures: `valid_supabase_jwt` / `expired_supabase_jwt` (HS256 factory), `es256_supabase_jwt` / `es256_supabase_jwt_unknown_kid` / `_seed_jwks_cache` (ES256 + JWKS cache injection, avoiding a real Supabase JWKS HTTP call), `unsupported_alg_jwt`.
- Entity factories, all `async def _make(**overrides) -> Model` closures returned by a `pytest_asyncio.fixture`, following one consistent shape: build a `defaults` dict, `defaults.update(overrides)`, construct + `db.add` + `commit` + `refresh`, return the object (or a `dict` when the row comes from raw SQL, e.g. campaigns/conversations/queue rows that predate their ORM model or intentionally bypass it):
  - `test_workspace`, `test_sender_factory` / `test_checker`, `test_folder`, `test_agent_factory`, `test_contacts_factory`, `test_campaign_factory`, `test_running_campaign_factory`, `attach_sender_to_campaign`, `test_queue_item_factory`, `test_conversation_factory`, `test_message_factory`.
  - Use these factories instead of hand-writing INSERTs in a new test — nearly every table has one already, and they're workspace-scoped by default (through `test_workspace`).
- Phase-14 Telethon mocks (`mock_telethon_client`) and Phase-21 account-import synthetic session builders (`build_vendor_sqlite_session`, `stub_import_telethon`) — see Mocking above.

**Location:** exclusively `tests/conftest.py` — no per-directory `conftest.py` files exist (flat test layout).

## Database Test Setup (session-scoped, real Postgres)

- `_setup_database` (session-scoped, `autouse=True`) builds the **entire** schema once per test session: `Base.metadata.create_all` (ORM tables) → `CREATE EXTENSION vector` (must precede `create_all` — a `Vector(1536)` column errors otherwise) → every migration file `012_...sql` through the newest, applied in a **hardcoded, explicitly-ordered list** (not a directory glob) → a handful of post-migration `ALTER ... SET DEFAULT` statements to backfill server-side defaults that `default=` (Python-side, ORM-only) doesn't provide for raw-SQL `INSERT`s.
- **Newer migrations (038+) are wrapped in an `if <path>.exists(): ...` exists-guard** so conftest changes can be authored/land ahead of the migration file itself and the suite stays green either way. When adding migration `NNN`, check whether it needs a corresponding conftest block (only if it does something `create_all` can't express — CHECK constraints, indexes, backfills, defaults) — see the extensive inline comments in `conftest.py` for the exact reasoning per past migration; migration `059` (a pure DROP with no ORM column) needed **no** conftest block.
- A **hard guard function** (`_assert_test_dsn`) refuses to run schema setup/teardown unless `DATABASE_URL` contains one of a fixed set of test-DB markers (`outreach_test`, `_test@`, `@localhost`, `@127.0.0.1`, ...) — this is the safety net against the 2026-05-26 incident where a bare `docker compose run --rm api pytest` DROPped the prod schema. Never weaken or bypass this guard.
- Teardown (session end): `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` on the test DB — safe because it's the ephemeral tmpfs `db-test` container, destroyed anyway when `run --rm` exits.
- `migrations_raw_dsn` fixture — a **second, dedicated throwaway database** (`outreach_test_migrations`) built identically to the main one, used ONLY by `test_migration_NNN.py` idempotency tests that re-apply raw DDL (which commits and isn't rolled back by `async_db_session`). Re-running a destructive migration against the shared main test DB would poison later tests (e.g. migration 015 drops columns that 018 re-adds) — always use `migrations_raw_dsn`, never `async_db_session`, for migration re-application tests.

## Coverage

**Requirements:** no coverage tool configured (no `pytest-cov`, no `.coveragerc`, no CI coverage gate observed). Coverage is not enforced or measured — the ~135 test files serve as the de facto regression suite instead.

## Test Types

**Unit-ish tests:** service-level functions tested directly (e.g. `run_control_probe`, `apply_results_with_confidence`) with the DB layer real (via `async_db_session`) but external RPCs (Telethon/OpenAI) mocked. Most of the suite is this shape — "unit" in the sense of isolating one service function, but always against a real Postgres.

**Integration tests:** full HTTP-through-router-through-service-through-DB flows via `async_client` (e.g. `test_campaign_router.py`, `test_workspace_router.py`, `test_senders.py` hitting real `/api/v1/...` endpoints). The `integration` pytest marker exists in config but is not actually applied to these files — don't rely on `-m integration` to select them.

**E2E tests:** none — no browser/Playwright/Cypress test suite exists in this repo (the frontend is a separate repo/deploy pipeline).

**Migration idempotency tests:** a distinct category (`test_migration_NNN.py`) that re-applies a migration file's raw SQL against the dedicated `migrations_raw_dsn` database and asserts it doesn't error / produces the same end state — this guards the idempotency requirement described in CONVENTIONS.md.

## Common Patterns

**Async testing:**
```python
pytestmark = pytest.mark.asyncio   # module-level; asyncio_mode=auto makes it belt-and-suspenders

async def test_something(async_db_session, test_workspace, test_sender_factory):
    sender = await test_sender_factory(role="checker")
    ...
```

**HTTP endpoint testing:**
```python
async def test_list_senders(async_client, valid_supabase_jwt, test_workspace, test_sender_factory):
    await test_sender_factory()
    token = valid_supabase_jwt(sub=str(test_workspace.id))  # or whatever claim shape the endpoint needs
    resp = await async_client.get("/api/v1/senders", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
```

**Error/exception testing:** assert on the structured `detail` dict shape, not just the status code, matching the app-code convention of `{"code": ..., "message": ...}`:
```python
resp = await async_client.post("/api/v1/senders", json={..., "rate_per_min": 999}, headers=...)
assert resp.status_code == 422
assert resp.json()["detail"]["code"] == "RATE_LIMIT_EXCEEDS_HARD_CAP"
```

**Mocking a Telegram RPC failure path:**
```python
with patch("app.services.contact_check_worker.checker_service.probe_control",
           new=AsyncMock(return_value={"checked": 1, "flood_wait_hit": False, "results": [...]})):
    await run_control_probe(checker_id=checker_id)
```

---

*Testing analysis: 2026-07-09*
