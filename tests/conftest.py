"""pytest-фикстуры для outreach-platform."""

import os

# Выставляем env vars ДО любого импорта app.* — иначе pydantic Settings
# упадёт с ValidationError на module-level get_settings() в app/utils/auth.py.
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-for-pytest-only-do-not-use-in-prod")
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/outreach_test")
# Phase 2 fixture-level env vars: app.config requires these for Settings.__init__
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test-api-hash-for-pytest-only")
os.environ.setdefault("ENCRYPTION_KEY", "test-encryption-key-for-pytest-only-do-not-use-in-prod-32b")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-pytest-only")

import logging
from typing import AsyncGenerator, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

# Только теперь — импорты app:
from app.config import get_settings  # noqa: E402
from app.database import AsyncSessionLocal, engine, Base  # noqa: E402
from app.main import app  # noqa: E402

logger = logging.getLogger(__name__)


# ⚠ HARD GUARD against destructive setup on prod DB.
# 2026-05-26: `docker compose run --rm api pytest …` resolves DATABASE_URL from
# docker-compose.yml::api::environment, which points at prod (`outreach_user@db:5432/
# outreach_platform`). The DROP SCHEMA below WILL execute against prod under that
# invocation and has done so before — the full outreach_platform schema was rebuilt at
# 2026-05-26 13:18:21 UTC (proven by identical `file_mtime` across all 22 relations).
# Refuse to run unless the DSN clearly identifies a test DB.
_ALLOWED_TEST_DSN_MARKERS = (
    "outreach_test",        # explicit test DB (incl. outreach_test_migrations)
    "_test@", "_test/",     # *_test user/db suffix
    "/test_",               # /test_db naming
    "@localhost",           # host-run pytest with local DB
    "@127.0.0.1",
)

# Dedicated throwaway DB for migration RE-application (idempotency) tests. Those run raw
# DDL via asyncpg which COMMITS and is NOT rolled back by async_db_session — re-applying a
# destructive migration (e.g. 015 drops ai_contexts columns that 018 re-adds) on the SHARED
# session DB poisons every later test. Built identically to the main DB, dropped at teardown.
_MIGRATIONS_DB_NAME = "outreach_test_migrations"


def _assert_test_dsn(dsn: str, action: str = "DESTRUCTIVE TEST SETUP") -> None:
    if not any(marker in dsn for marker in _ALLOWED_TEST_DSN_MARKERS):
        raise RuntimeError(
            f"REFUSING TO RUN {action} AGAINST {dsn!r}. "
            f"None of the allowed test-DSN markers {_ALLOWED_TEST_DSN_MARKERS!r} are present. "
            "DATABASE_URL must point at a test database (containing one of the markers above). "
            "Inside `docker compose run/exec api`, DATABASE_URL is inherited from "
            "docker-compose.yml and points at PROD outreach_platform — pytest must NOT run "
            "in that context. See .claude/projects/-root/memory/feedback_pytest_drop_schema_prod.md."
        )


def _swap_db_name(raw_dsn: str, new_db: str) -> str:
    """Replace the database name in a `postgresql://…/db[?params]` DSN."""
    base, sep, query = raw_dsn.partition("?")
    head, _, _old = base.rpartition("/")
    return f"{head}/{new_db}" + (f"{sep}{query}" if sep else "")


async def _build_outreach_schema(raw_dsn: str, sa_url: str) -> None:
    """Build the full test schema on the target DB: create_all (ORM) + migrations
    012-032 + post-migration default tweaks. `raw_dsn` is the asyncpg DSN; `sa_url`
    is the matching `postgresql+asyncpg://` URL used to build a throwaway engine for
    create_all. Used for BOTH the main test DB and the dedicated migrations DB.

    Setup strategy:
    1. Base.metadata.create_all builds base tables from the ORM (defines `messages`,
       `ai_contexts`, `senders`, etc. that migrations 001-011 ALTER but never CREATE).
    2. Migrations 012-032 layer the Phase 1-11 extensions on top.
    3. The ORM has `default=uuid.uuid4` (Python-side) for id columns. Raw-SQL INSERT
       tests need server-side defaults — added post-create_all.
    """
    import asyncpg
    import pathlib
    from sqlalchemy.ext.asyncio import create_async_engine

    PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

    # 1. Wipe + create base tables from ORM
    #    Phase 16 (Pitfall 1): CREATE EXTENSION vector MUST run before create_all —
    #    the ORM KbChunk.embedding column emits VECTOR(1536), and create_all raises
    #    `type "vector" does not exist` if the extension isn't present yet.
    conn = await asyncpg.connect(dsn=raw_dsn)
    try:
        await conn.execute(
            "DROP SCHEMA public CASCADE; CREATE SCHEMA public; "
            "CREATE EXTENSION IF NOT EXISTS vector;"
        )
    finally:
        await conn.close()

    build_engine = create_async_engine(sa_url)
    try:
        async with build_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await build_engine.dispose()

    # 2. Defensive stub: cca was dropped from the ORM by Phase 4 (016) but migration 012
    #    still ALTERs it. Stub it minimally so 012 can run; 016 drops it again.
    # 3. Add server-side `gen_random_uuid()` default on UUID PKs so raw-SQL tests work.
    asyncpg_conn = await asyncpg.connect(dsn=raw_dsn)
    try:
        # Stub for migration 012's cca ALTER
        await asyncpg_conn.execute(
            "CREATE TABLE IF NOT EXISTS context_contact_assignments ("
            "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), "
            "context_id UUID, contact_phone VARCHAR(20), sender_id UUID, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )

        # Add server-side UUID defaults to tables tested with raw INSERT statements.
        # The ORM uses Python-side default=uuid.uuid4 which only fires via ORM mapper.
        for table in (
            "ai_contexts", "senders", "contacts", "folders", "campaigns",
            "campaign_senders", "campaign_contact_assignments",
            "message_queue", "conversations", "messages", "llm_calls",
            "workspaces", "user_workspaces", "workspace_api_keys",
            "onboarding_sessions", "csv_imports", "proxy_pool",
            "warmup_pool", "warmup_sessions", "warmup_messages",
            # Phase 16 KB tables (single-id PK). agent_knowledge_bases has a
            # composite PK / no `id` column — the try/except below swallows it.
            "knowledge_bases", "kb_documents", "kb_chunks",
        ):
            try:
                await asyncpg_conn.execute(
                    f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT gen_random_uuid()"
                )
            except (asyncpg.exceptions.UndefinedTableError,
                    asyncpg.exceptions.UndefinedColumnError):
                # Table or `id` column not present (e.g. junction tables w/ composite PK).
                pass

        # Apply Phase 1-5.1 migrations 012-018 in order, then the 2026-05-26
        # schema-drift / hotfix batch 019-025 (idempotent), then 026.
        # 019-025 close gaps create_all can't express (CHECK constraints, partial
        # UNIQUE indexes for ON CONFLICT dedup, user_workspaces uniqueness) — without
        # them workspace setup and contact dedup raise InvalidColumnReferenceError.
        for filename in (
            "012_workspace.sql",
            "013_phase2.sql",
            "014_phase2_1_hardening.sql",
            "015_phase3.sql",
            "016_phase4.sql",
            "017_phase5.sql",
            "018_phase5_1.sql",
            "019_schema_drift_fix.sql",
            "020_contacts_cache_unique.sql",
            "021_uuid_defaults.sql",
            "022_conversations_status_default.sql",
            "023_user_workspaces_unique.sql",
            "024_campaign_draft_nullable.sql",
            "025_username_outreach.sql",
            # 026: allow_recontact columns come from ORM create_all (ADD COLUMN
            # IF NOT EXISTS are no-ops here), but the conversations.updated_at
            # freshness trigger is SQL-only — apply it so recontact tests see it.
            "026_campaign_allow_recontact.sql",
            # 027: folders(workspace_id, name) UNIQUE — never landed because 013's
            # inline constraint is skipped by CREATE TABLE IF NOT EXISTS after
            # create_all. Required for get_or_create_by_name ON CONFLICT.
            "027_folders_workspace_name_unique.sql",
            # 028-031: Phase 7/8/9/10 sender-restriction + pool-resilience migrations.
            # These must be in the test DB so Phase 11 integration tests don't hit
            # UndefinedColumn on restriction_status / sender_restriction_events / etc.
            # (RESEARCH Pitfall 3 — hardcoded list does NOT glob).
            "028_sender_restriction.sql",
            "029_campaign_pause_reason.sql",
            "030_sender_restriction_events.sql",
            "031_sre_flood_wait_category.sql",
        ):
            sql_text = (PROJECT_ROOT / "migrations" / filename).read_text()
            await asyncpg_conn.execute(sql_text)

        # 032: Phase 11 field-split migration — applied only when the file exists
        # so this conftest change is green now and auto-activates once Plan 11-02 lands.
        _mig_032 = PROJECT_ROOT / "migrations" / "032_phase11_field_split.sql"
        if _mig_032.exists():
            await asyncpg_conn.execute(_mig_032.read_text())

        # 038: Phase 15 warmup_settings table — applied via an exists-guard so the
        # ephemeral test DB gets `warmup_settings` (raw-SQL warmup tests need it).
        # (Slot 037 is taken by 037_campaign_prompt_presets.sql; warmup is 038.)
        _mig_038 = PROJECT_ROOT / "migrations" / "038_warmup_settings.sql"
        if _mig_038.exists():
            await asyncpg_conn.execute(_mig_038.read_text())

        # 041: Phase 16 RAG knowledge bases. The four KB tables come from ORM
        # create_all (the IF NOT EXISTS CREATE TABLEs in the migration are no-ops
        # here), but the HNSW vector index (idx_kbchunk_embedding_hnsw) is SQL-only
        # — apply the migration so the test DB exercises the same index path as prod.
        # Exists-guard so this conftest change is green now and auto-activates once
        # migrations/041_knowledge_bases.sql lands. (Slot 040 is taken by
        # 040_warmup_sessions_defaults_drift.sql; KB is 041.)
        _mig_041 = PROJECT_ROOT / "migrations" / "041_knowledge_bases.sql"
        if _mig_041.exists():
            await asyncpg_conn.execute(_mig_041.read_text())

        # 044: Phase 18 llm_settings table + llm_calls.provider/key_source columns.
        # The table and the two llm_calls columns come from ORM create_all (the
        # CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS are no-ops here), but
        # the CHECK constraints (provider IN ..., api_key_status IN ...) are SQL-only —
        # apply the migration so the test DB exercises the same constraint path as prod.
        # Exists-guard so this conftest change stays green until migrations/044 lands.
        _mig_044 = PROJECT_ROOT / "migrations" / "044_llm_settings.sql"
        if _mig_044.exists():
            await asyncpg_conn.execute(_mig_044.read_text())

        # 045: Phase 19 no-reply follow-up + auto-finish. conversations.pings_sent and
        # the four campaigns.follow_up_* columns come from ORM create_all (ADD COLUMN
        # IF NOT EXISTS are no-ops here), but the conversations.status CHECK extension
        # (adds 'no_reply') is SQL-only — apply the migration so the test DB accepts
        # status='no_reply'. Exists-guard so this conftest change stays green until
        # migrations/045_follow_up.sql lands.
        _mig_045 = PROJECT_ROOT / "migrations" / "045_follow_up.sql"
        if _mig_045.exists():
            await asyncpg_conn.execute(_mig_045.read_text())

        # 046 (adds 'telegram_service' to conversations_status_check) is SQL-only —
        # apply so the test DB accepts the Telegram service-account status. Exists-
        # guard keeps this green until migrations/046_telegram_service_status.sql lands.
        _mig_046 = PROJECT_ROOT / "migrations" / "046_telegram_service_status.sql"
        if _mig_046.exists():
            await asyncpg_conn.execute(_mig_046.read_text())

        # 054: Phase 24 campaign attachment + variation flag. The campaign_attachments
        # table and campaigns.variation_enabled column come from ORM create_all (CREATE
        # TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS are no-ops here), but the
        # ALTER ... SET DEFAULT true drift-guard, the campaign_id UNIQUE constraint and
        # the workspace index are SQL-only — apply the migration so the test DB matches
        # prod exactly. Exists-guard keeps this green until migrations/054 lands.
        _mig_054 = PROJECT_ROOT / "migrations" / "054_campaign_attachment_and_variation.sql"
        if _mig_054.exists():
            await asyncpg_conn.execute(_mig_054.read_text())

        # Migration 018 uses ADD COLUMN IF NOT EXISTS ... DEFAULT, but create_all already
        # created these columns (ORM has them) — IF NOT EXISTS skips, defaults never apply.
        # Set them explicitly post-migration so raw-SQL tests get the expected defaults.
        #
        # NOTE: `tone` default is intentionally OMITTED here — migration 032 (Plan 11-02)
        # DROPS the `tone` column. If that migration is applied, this ALTER would raise
        # "column tone does not exist" and crash the entire integration suite.
        # The `tone` default was handled by migration 018's ADD COLUMN DEFAULT clause
        # before create_all bypassed it; not needed for correctness of existing tests.
        await asyncpg_conn.execute("""
            ALTER TABLE ai_contexts ALTER COLUMN max_message_length SET DEFAULT 280;
            ALTER TABLE ai_contexts ALTER COLUMN mirror_language SET DEFAULT TRUE;
            ALTER TABLE ai_contexts ALTER COLUMN allow_emoji SET DEFAULT FALSE;
            ALTER TABLE ai_contexts ALTER COLUMN auto_pause_scope SET DEFAULT 'conversation';
        """)

        # warmup_sessions server-side defaults: migrations 001-011 are not replayed
        # here (warmup tables come from ORM create_all, which carries only Python-side
        # defaults). Product code in app/services/warmup.py::_create_new_sessions INSERTs
        # without status/messages_sent, relying on migration 005's column DEFAULTs.
        # Replicate them so raw-SQL warmup tests (and the worker) match production.
        await asyncpg_conn.execute("""
            ALTER TABLE warmup_sessions ALTER COLUMN status SET DEFAULT 'active';
            ALTER TABLE warmup_sessions ALTER COLUMN messages_sent SET DEFAULT 0;
            ALTER TABLE warmup_sessions ALTER COLUMN target_messages SET DEFAULT 6;
            ALTER TABLE warmup_sessions ALTER COLUMN next_message_at SET DEFAULT NOW();
        """)
    finally:
        await asyncpg_conn.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_database():
    """Создаёт схему перед всеми тестами и применяет миграции 012-032."""
    import asyncpg

    settings = get_settings()
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    _assert_test_dsn(dsn)

    await _build_outreach_schema(dsn, settings.database_url)

    yield

    # Teardown: drop schema with CASCADE (drop_all can't handle FK cycles like
    # messages -> conversations -> messages). Guard re-checked defensively.
    _assert_test_dsn(dsn, action="DESTRUCTIVE TEARDOWN")
    asyncpg_conn = await asyncpg.connect(dsn=dsn)
    try:
        await asyncpg_conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await asyncpg_conn.close()


@pytest_asyncio.fixture(scope="session")
async def migrations_raw_dsn():
    """Session-scoped throwaway database for migration RE-application (idempotency) tests.

    Migration tests re-run a .sql file to prove idempotency; that DDL COMMITS and is NOT
    rolled back by async_db_session. Re-applying a destructive migration on the SHARED
    session DB poisons later tests (e.g. 015 drops ai_contexts columns that 018 re-adds).
    This dedicated DB is built identically to the main test DB so re-application exercises
    each migration's IF EXISTS / IF NOT EXISTS guards without touching the shared schema.
    """
    import asyncpg

    settings = get_settings()
    main_dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    _assert_test_dsn(main_dsn, action="DEDICATED MIGRATIONS DB SETUP")

    mig_dsn = _swap_db_name(main_dsn, _MIGRATIONS_DB_NAME)
    mig_sa_url = _swap_db_name(settings.database_url, _MIGRATIONS_DB_NAME)
    admin_dsn = _swap_db_name(main_dsn, "postgres")

    async def _drop_db():
        admin = await asyncpg.connect(dsn=admin_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{_MIGRATIONS_DB_NAME}' AND pid <> pg_backend_pid()"
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{_MIGRATIONS_DB_NAME}"')
        finally:
            await admin.close()

    admin = await asyncpg.connect(dsn=admin_dsn)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{_MIGRATIONS_DB_NAME}"')
        await admin.execute(f'CREATE DATABASE "{_MIGRATIONS_DB_NAME}"')
    finally:
        await admin.close()

    await _build_outreach_schema(mig_dsn, mig_sa_url)

    yield mig_dsn

    await _drop_db()


@pytest_asyncio.fixture
async def async_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Изолированная DB-сессия с rollback после теста."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """httpx.AsyncClient с in-process ASGITransport — без реальной сети."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
def valid_supabase_jwt() -> Callable[..., str]:
    """Фабрика валидных HS256 JWT для тестов auth_dep."""
    settings = get_settings()

    def _factory(
        sub: str = "test-user-uuid-default",
        email: str | None = "test@example.com",
        exp: int = 9999999999,  # 2286 год
        aud: str = "authenticated",
    ) -> str:
        claims = {"sub": sub, "email": email, "aud": aud, "exp": exp}
        return jwt.encode(claims, settings.supabase_jwt_secret, algorithm="HS256")

    return _factory


@pytest_asyncio.fixture
def expired_supabase_jwt(valid_supabase_jwt: Callable[..., str]) -> str:
    """Истёкший JWT для теста TOKEN_EXPIRED."""
    return valid_supabase_jwt(exp=1)  # 1970-01-01


# ─── Phase 05.1-DEBUG (2026-05-23): ES256 / JWKS fixtures ────────────────────

import base64 as _b64
import time as _time_mod


def _b64u_int(value: int, size: int) -> str:
    raw = value.to_bytes(size, "big")
    return _b64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest_asyncio.fixture
def _es256_keypair():
    """Generate an ephemeral EC P-256 keypair + matching JWK for tests.

    Returns (private_pem: str, jwk_dict: dict). The JWK is what would be served
    by Supabase from /auth/v1/.well-known/jwks.json — we inject it directly
    into the in-process _JWKS_CACHE to avoid making a real HTTP call.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_numbers()
    jwk_dict = {
        "kty": "EC",
        "crv": "P-256",
        "use": "sig",
        "alg": "ES256",
        "kid": "test-kid-es256-1",
        "x": _b64u_int(pub.x, 32),
        "y": _b64u_int(pub.y, 32),
    }
    return pem, jwk_dict


@pytest_asyncio.fixture
def _seed_jwks_cache(_es256_keypair):
    """Pre-populate the auth module's JWKS cache with our test JWK.

    Fresh fetched_at = now so the auth code won't try to refetch from a real
    Supabase URL during the test.
    """
    from app.utils import auth as _auth_module

    _pem, jwk_dict = _es256_keypair
    _auth_module._JWKS_CACHE["keys_by_kid"] = {jwk_dict["kid"]: jwk_dict}
    _auth_module._JWKS_CACHE["fetched_at"] = _time_mod.time()
    yield jwk_dict
    # teardown: clear so other tests don't see stale state
    _auth_module._JWKS_CACHE["keys_by_kid"] = {}
    _auth_module._JWKS_CACHE["fetched_at"] = 0.0


@pytest_asyncio.fixture
def es256_supabase_jwt(_es256_keypair, _seed_jwks_cache) -> Callable[..., str]:
    """Factory of valid ES256 JWTs signed by our test key (in JWKS cache)."""
    pem, jwk_dict = _es256_keypair

    def _factory(
        sub: str = "test-user-es256",
        email: str | None = "es256@example.com",
        exp: int = 9999999999,
        aud: str = "authenticated",
    ) -> str:
        claims = {"sub": sub, "email": email, "aud": aud, "exp": exp}
        return jwt.encode(
            claims, pem, algorithm="ES256", headers={"kid": jwk_dict["kid"]}
        )

    return _factory


@pytest_asyncio.fixture
def es256_supabase_jwt_unknown_kid(_es256_keypair, _seed_jwks_cache) -> str:
    """ES256 JWT signed by our test key but with a kid not in the cached JWKS.

    The auth code will refetch JWKS once; we mock _fetch_jwks to return the same
    seeded keys (so the unknown kid stays unknown) — patching via env. In our
    test setup the SUPABASE_URL points to localhost:54321 (no real server), so
    the refetch attempt will hit the except branch in _get_jwk_for_kid and
    return None → 401 TOKEN_INVALID.
    """
    pem, _jwk_dict = _es256_keypair
    claims = {"sub": "x", "email": "x@y.z", "aud": "authenticated", "exp": 9999999999}
    return jwt.encode(
        claims, pem, algorithm="ES256", headers={"kid": "unknown-kid-zzz"}
    )


@pytest_asyncio.fixture
def unsupported_alg_jwt(valid_supabase_jwt) -> str:
    """JWT with unsupported algorithm (none) → header alg=none → 401.

    python-jose blocks alg=none on decode but we test that our header routing
    also rejects it explicitly. Use jwt.encode with algorithm="HS512" which
    we don't accept (only HS256/ES256 are in our allowlist).
    """
    settings = get_settings()
    secret = settings.supabase_jwt_secret or "test-secret-fallback"
    claims = {"sub": "x", "email": "x@y.z", "aud": "authenticated", "exp": 9999999999}
    return jwt.encode(claims, secret, algorithm="HS512")




# ─── Phase 2 fixtures: Workspace / Sender / Folder / Contact factories ───────

# Локальные импорты ниже, чтобы не платить за них при collection-time
# тестов Phase 1, и чтобы импорт ORM-моделей произошёл уже после
# инициализации app/config через env vars выше.

from app.models import Folder, Contact, Sender, Workspace, AIContext  # noqa: E402


@pytest_asyncio.fixture
async def test_workspace(async_db_session: AsyncSession) -> Workspace:
    """Создаёт workspace для теста (Phase 2 fixture)."""
    ws = Workspace(name="Test Workspace Phase 2")
    async_db_session.add(ws)
    await async_db_session.commit()
    await async_db_session.refresh(ws)
    return ws


@pytest_asyncio.fixture
async def test_sender_factory(async_db_session: AsyncSession, test_workspace: Workspace):
    """Фабрика sender'ов.

    Usage:
        sender = await test_sender_factory(role='checker', slug='my-checker')
    """
    counter = {"n": 0}

    async def _make(**overrides) -> Sender:
        counter["n"] += 1
        n = counter["n"]
        defaults = dict(
            workspace_id=test_workspace.id,
            slug=f"test-sender-{n}",
            name=f"Test Sender {n}",
            phone=f"+7900000{n:04d}",
            session_string="encrypted_stub",
            role="sender",
            auth_status="ok",
            lifecycle_status="active",
            rate_per_min=4,
            rate_per_hour=20,
            rate_per_day=150,
        )
        defaults.update(overrides)
        s = Sender(**defaults)
        async_db_session.add(s)
        await async_db_session.commit()
        await async_db_session.refresh(s)
        return s

    return _make


@pytest_asyncio.fixture
async def test_checker(test_sender_factory) -> Sender:
    """Checker-аккаунт для теста."""
    return await test_sender_factory(role="checker", slug="test-checker")


@pytest_asyncio.fixture
async def test_folder(async_db_session: AsyncSession, test_workspace: Workspace) -> Folder:
    """Папка контактов для теста."""
    f = Folder(workspace_id=test_workspace.id, name="Test Folder")
    async_db_session.add(f)
    await async_db_session.commit()
    await async_db_session.refresh(f)
    return f


@pytest_asyncio.fixture
async def test_agent_factory(async_db_session: AsyncSession, test_workspace: Workspace):
    """Factory for AIContext (agent) test fixtures. Phase 3 C-06.

    Usage:
        agent = await test_agent_factory(name="Sales", system_prompt="You are...")
    """
    counter = {"n": 0}

    async def _make(
        tone_preset: str | None = None,
        response_speed: str | None = None,
        response_delay_seconds: int | None = None,
        **overrides,
    ) -> AIContext:
        """Create a test AIContext (agent).

        Phase 11 kwargs:
          tone_preset: 'Friendly'|'Professional'|'Direct'|'Casual' (new Phase 11 field)
          response_speed: 'instant'|'human'|'slow'|'manual' (new Phase 11 field)
          response_delay_seconds: int (new Phase 11 field, used when response_speed='manual')

        These fields are passed through `overrides` so the ORM can ignore them
        gracefully until migration 032 adds the columns (SQLAlchemy raises on
        unknown columns only when mapped; raw-kwarg usage lets tests set them
        conditionally). Use `**overrides` path for post-032 integration tests.
        """
        counter["n"] += 1
        defaults = dict(
            workspace_id=test_workspace.id,
            name=f"Test Agent {counter['n']}",
            system_prompt="You are a helpful sales agent.",
            # tone_of_voice dropped Phase 11 D-01 (migration 032) — column no longer exists.
            rules="Always be polite.",
            faq={},
            company_info="Test Co.",
            product_info="Test Product.",
        )
        defaults.update(overrides)
        # Phase 11 new-era fields — only add to defaults if explicitly passed
        # (avoids AttributeError on pre-032 ORM model that lacks these columns).
        if tone_preset is not None:
            defaults["tone_preset"] = tone_preset
        if response_speed is not None:
            defaults["response_speed"] = response_speed
        if response_delay_seconds is not None:
            defaults["response_delay_seconds"] = response_delay_seconds
        agent = AIContext(**defaults)
        async_db_session.add(agent)
        await async_db_session.commit()
        await async_db_session.refresh(agent)
        return agent

    return _make


@pytest_asyncio.fixture
async def test_contacts_factory(
    async_db_session: AsyncSession,
    test_workspace: Workspace,
    test_folder: Folder,
):
    """Фабрика контактов в test_folder. Возвращает Contact или list[Contact]."""

    async def _make(count: int = 1, tg_status: str = "pending", **overrides):
        contacts = []
        for i in range(count):
            # defaults + update so any field (full_name, phone, …) is overridable
            # without a duplicate-keyword TypeError.
            fields = dict(
                workspace_id=test_workspace.id,
                folder_id=test_folder.id,
                phone=f"+7901000{i:04d}",
                full_name=f"Contact {i}",
                source="test",
                tg_status=tg_status,
            )
            fields.update(overrides)
            c = Contact(**fields)
            async_db_session.add(c)
            contacts.append(c)
        await async_db_session.commit()
        for c in contacts:
            await async_db_session.refresh(c)
        return contacts if count > 1 else contacts[0]

    return _make


# ─── Phase 4 fixtures: Campaign factories ────────────────────────────────────

@pytest_asyncio.fixture
async def test_campaign_factory(
    async_db_session: AsyncSession,
    test_workspace: Workspace,
    test_agent_factory,
    test_folder: Folder,
):
    """Factory creates draft campaign in workspace.

    Usage:
        c = await test_campaign_factory(name="Camp", message_template="Hello {{name}}")
        c = await test_campaign_factory(status="running")  # explicit override
    """
    from sqlalchemy import text as _t
    import json

    counter = {"n": 0}

    async def _make(
        name: str | None = None,
        agent_id=None,
        folder_id=None,
        message_template: str = "Hello {{name}}!",
        timezone: str = "Europe/Moscow",
        work_hour_start: int = 9,
        work_hour_end: int = 20,
        work_days_mask: int = 31,
        start_date=None,
        stop_date=None,
        lead_webhook_url: str | None = None,
        handoff_webhook_url: str | None = None,
        finish_webhook_url: str | None = None,
        lead_trigger_hint: str | None = None,
        handoff_trigger_hint: str | None = None,
        finish_trigger_hint: str | None = None,
        tools=None,
        status: str = "draft",
        description: str | None = None,
        # Phase 19 (NORP) follow-up / auto-finish fields (migration 045).
        follow_up_enabled: bool = False,
        follow_up_interval_hours: int = 24,
        follow_up_max_pings: int = 2,
        auto_finish_hours: int = 72,
        webhook_url: str | None = None,
    ) -> dict:
        counter["n"] += 1
        if name is None:
            name = f"Test Campaign {counter['n']}"
        if agent_id is None:
            agent = await test_agent_factory()
            agent_id = agent.id
        if folder_id is None:
            folder_id = test_folder.id

        row = (await async_db_session.execute(_t("""
            INSERT INTO campaigns (
                workspace_id, agent_id, folder_id, name, description, status,
                timezone, work_hour_start, work_hour_end, work_days_mask,
                start_date, stop_date, message_template,
                lead_webhook_url, handoff_webhook_url, finish_webhook_url,
                lead_trigger_hint, handoff_trigger_hint, finish_trigger_hint,
                tools, follow_up_enabled, follow_up_interval_hours,
                follow_up_max_pings, auto_finish_hours, webhook_url
            ) VALUES (
                :wid, :aid, :fid, :name, :desc, :status,
                :tz, :wstart, :wend, :wmask,
                :sd, :stop, :tpl,
                :lwurl, :hwurl, :fwurl,
                :lhint, :hhint, :fhint,
                CAST(:tools AS JSONB), :fu_en, :fu_int, :fu_max, :fu_af, :wurl
            ) RETURNING id, workspace_id, agent_id, folder_id, name, status,
                       timezone, work_hour_start, work_hour_end, work_days_mask,
                       message_template, created_at
        """), {
            "wid": str(test_workspace.id),
            "aid": str(agent_id),
            "fid": str(folder_id),
            "name": name,
            "desc": description,
            "status": status,
            "tz": timezone,
            "wstart": work_hour_start,
            "wend": work_hour_end,
            "wmask": work_days_mask,
            "sd": start_date,
            "stop": stop_date,
            "tpl": message_template,
            "lwurl": lead_webhook_url,
            "hwurl": handoff_webhook_url,
            "fwurl": finish_webhook_url,
            "lhint": lead_trigger_hint,
            "hhint": handoff_trigger_hint,
            "fhint": finish_trigger_hint,
            "tools": json.dumps(tools or []),
            "fu_en": follow_up_enabled,
            "fu_int": follow_up_interval_hours,
            "fu_max": follow_up_max_pings,
            "fu_af": auto_finish_hours,
            "wurl": webhook_url,
        })).first()
        await async_db_session.commit()
        return {
            "id": row[0],
            "workspace_id": row[1],
            "agent_id": row[2],
            "folder_id": row[3],
            "name": row[4],
            "status": row[5],
            "timezone": row[6],
            "work_hour_start": row[7],
            "work_hour_end": row[8],
            "work_days_mask": row[9],
            "message_template": row[10],
            "created_at": row[11],
        }

    return _make


@pytest_asyncio.fixture
async def attach_sender_to_campaign(async_db_session: AsyncSession, test_workspace: Workspace):
    """Attach an existing sender to an existing campaign (campaign_senders row)."""
    from sqlalchemy import text as _t

    async def _attach(campaign_id, sender_id, workspace_id=None):
        wid = workspace_id or test_workspace.id
        await async_db_session.execute(_t("""
            INSERT INTO campaign_senders (campaign_id, sender_id, workspace_id)
            VALUES (:cid, :sid, :wid)
            ON CONFLICT DO NOTHING
        """), {"cid": str(campaign_id), "sid": str(sender_id), "wid": str(wid)})
        await async_db_session.commit()

    return _attach


@pytest_asyncio.fixture
async def test_queue_item_factory(async_db_session: AsyncSession, test_workspace: Workspace):
    """Seed a message_queue row (+ optional sticky CCA + conversation) for Phase 8.

    Mirrors `attach_sender_to_campaign` (conftest:583) raw-SQL + commit shape.
    Used by the pool-management / rebalance tests to build a campaign backlog.

    Usage:
        await test_queue_item_factory(camp_id, sender.id, "+79990001111")
        await test_queue_item_factory(camp_id, sender.id, "+79990001111",
                                      status="sent", with_cca=False)
        await test_queue_item_factory(camp_id, sender.id, "+79990001111",
                                      with_conversation=True)  # engaged recipient

    Behaviour:
    - Always INSERTs one `message_queue` row keyed on `recipient_phone` with
      `scheduled_at = NOW()` and `workspace_id = test_workspace.id`.
    - When `with_cca=True` (default) upserts a matching
      `campaign_contact_assignments(campaign_id, contact_phone)` row pointing at the
      same sender — this keeps the sticky assignment in sync with the queue row, which
      is exactly the invariant the rebalance tests assert (D-08).
    - When `with_conversation=True` also INSERTs a `conversations` row for
      `(workspace_id, sender_id, contact_phone=recipient_phone)` so a recipient can be
      marked "engaged" (POOL-06b / POOL-08b — engaged dialogs must NOT be moved/blocked).
    - When `with_message=True` (requires `with_conversation=True`) additionally INSERTs
      one inbound `messages` row tied to that conversation so the recipient counts as a
      *has-message* (truly engaged) dialog. With `with_conversation=True, with_message=False`
      (default) the conversation stays EMPTY (zero messages) — the D-05 "empty conversation
      is still cold" case that Phase 9 failover MUST still treat as movable.
    """
    from sqlalchemy import text as _t

    async def _make(
        campaign_id,
        sender_id,
        recipient_phone,
        status: str = "pending",
        *,
        with_cca: bool = True,
        with_conversation: bool = False,
        with_message: bool = False,
        **overrides,
    ):
        wid = str(test_workspace.id)
        cid = str(campaign_id)
        sid = str(sender_id)

        await async_db_session.execute(_t("""
            INSERT INTO message_queue (
                workspace_id, campaign_id, sender_id,
                recipient_phone, item_type, status, scheduled_at
            ) VALUES (
                :wid, :cid, :sid, :phone, 'message', :status, NOW()
            )
        """), {
            "wid": wid, "cid": cid, "sid": sid,
            "phone": recipient_phone, "status": status, **overrides,
        })

        if with_cca:
            # Sticky upsert mirrors rotation.py:150-163 so CCA.sender_id tracks the
            # queue row's sender — rebalance keeps the two in lock-step.
            await async_db_session.execute(_t("""
                INSERT INTO campaign_contact_assignments
                    (workspace_id, campaign_id, contact_phone, sender_id)
                VALUES (:wid, :cid, :phone, :sid)
                ON CONFLICT (campaign_id, contact_phone)
                    DO UPDATE SET sender_id = EXCLUDED.sender_id
            """), {"wid": wid, "cid": cid, "phone": recipient_phone, "sid": sid})

        if with_conversation:
            # A dialog row for this recipient. contact_phone is the same identity
            # key the cold-pending guard joins on (NOT EXISTS conversations). When
            # with_message=False this conversation stays EMPTY (zero messages) — the
            # D-05 "empty conversation is still cold" case (Phase 9 failover-movable).
            conv_row = (await async_db_session.execute(_t("""
                INSERT INTO conversations (
                    workspace_id, sender_id, contact_phone, campaign_id, status
                ) VALUES (
                    :wid, :sid, :phone, :cid, 'active'
                )
                RETURNING id
            """), {"wid": wid, "sid": sid, "phone": recipient_phone, "cid": cid})).first()

            if with_message:
                # One inbound (received reply) message → the conversation is now
                # ENGAGED (has-message). Phase 9 failover must NOT move this row;
                # rebalance must keep it on the donor. Keyed by conversation_id only
                # (messages has no recipient_phone — migration 017).
                await async_db_session.execute(_t("""
                    INSERT INTO messages (
                        workspace_id, conversation_id, direction, message_text,
                        sent_by, created_at
                    ) VALUES (
                        :wid, :conv_id, 'inbound', 'engaged reply', 'contact', NOW()
                    )
                """), {"wid": wid, "conv_id": str(conv_row.id)})

        await async_db_session.commit()

    return _make


@pytest_asyncio.fixture
async def test_running_campaign_factory(test_campaign_factory, test_sender_factory, attach_sender_to_campaign):
    """Factory creates a running campaign with N senders attached."""

    async def _make(name: str | None = None, sender_count: int = 1, **kwargs):
        kwargs.setdefault("status", "running")
        camp = await test_campaign_factory(name=name, **kwargs)
        senders = [await test_sender_factory() for _ in range(sender_count)]
        for s in senders:
            await attach_sender_to_campaign(camp["id"], s.id)
        return camp, senders

    return _make


# ─── Phase 5 fixtures: Conversation / Message factories ──────────────────────

@pytest_asyncio.fixture
async def test_conversation_factory(
    async_db_session: AsyncSession,
    test_workspace: Workspace,
    test_sender_factory,
):
    """Insert a conversation row via raw SQL (lets us drive status freely).

    Usage:
        conv = await test_conversation_factory()                  # default
        conv = await test_conversation_factory(status='lead')     # explicit
        conv = await test_conversation_factory(sender=existing)   # reuse sender
    """
    from sqlalchemy import text as _t
    import uuid as _uuid

    counter = {"n": 0}
    created_ids: list[str] = []

    async def _make(
        sender=None,
        campaign_id=None,
        ai_context_id=None,
        contact_phone: str | None = None,
        contact_name: str | None = None,
        contact_telegram_id: int | None = None,
        status: str = "active",
        ai_enabled: bool = True,
        workspace_id=None,
    ) -> dict:
        counter["n"] += 1
        if sender is None:
            sender = await test_sender_factory()
        sender_id = sender["id"] if isinstance(sender, dict) else sender.id
        sender_workspace_id = (
            sender["workspace_id"] if isinstance(sender, dict) else sender.workspace_id
        )
        wid = workspace_id or sender_workspace_id

        if contact_phone is None:
            contact_phone = f"+7910{counter['n']:07d}"

        row = (await async_db_session.execute(_t("""
            INSERT INTO conversations (
                workspace_id, sender_id, contact_phone, contact_name,
                contact_telegram_id, ai_enabled, ai_context_id, campaign_id, status
            ) VALUES (
                :wid, :sid, :phone, :name, :tid, :ai_en, :aid, :cid, :status
            )
            RETURNING id, workspace_id, sender_id, contact_phone, contact_name,
                      contact_telegram_id, ai_enabled, ai_context_id, campaign_id,
                      status, paused_at, paused_reason, created_at, updated_at
        """), {
            "wid": str(wid),
            "sid": str(sender_id),
            "phone": contact_phone,
            "name": contact_name,
            "tid": contact_telegram_id,
            "ai_en": ai_enabled,
            "aid": str(ai_context_id) if ai_context_id else None,
            "cid": str(campaign_id) if campaign_id else None,
            "status": status,
        })).first()
        await async_db_session.commit()
        created_ids.append(str(row.id))
        return dict(row._mapping)

    yield _make

    # Teardown: the factory COMMITS rows into the shared (non-rolled-back) test DB,
    # so a conversation with a status added by a LATER migration (e.g. 'no_reply'
    # from migration 045) would survive and violate an EARLIER constraint when a
    # migration-reapply test (test_phase5_migration_017) rebuilds the old
    # conversations_status_check — the resulting aborted transaction then poisons a
    # pooled connection and cascades ("cannot use Connection.transaction() in a
    # manually started transaction"). Delete what we created to keep the shared DB
    # clean across tests.
    # Only 'no_reply' rows (a status added by migration 045) are removed: they are
    # the sole offender for test_phase5_migration_017's constraint-reapply, and
    # deleting ONLY them avoids unblocking the re-contact dedup that count-based
    # tests (test_recontact / test_campaign_enqueue_worker) rely on from leftover
    # 'active'/'finished' conversations in the shared DB.
    if created_ids:
        try:
            offenders = (await async_db_session.execute(
                _t("SELECT id FROM conversations "
                   "WHERE id = ANY(CAST(:ids AS uuid[])) AND status = 'no_reply'"),
                {"ids": created_ids},
            )).scalars().all()
            offenders = [str(x) for x in offenders]
            if offenders:
                # Remove dependent messages first (FK conversation_id) so the
                # conversation DELETE isn't blocked and rolled back.
                await async_db_session.execute(
                    _t("DELETE FROM messages WHERE conversation_id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": offenders},
                )
                await async_db_session.execute(
                    _t("DELETE FROM conversations WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": offenders},
                )
                await async_db_session.commit()
        except Exception:
            await async_db_session.rollback()


@pytest_asyncio.fixture
async def test_message_factory(async_db_session: AsyncSession):
    """Insert one or more messages rows under a conversation.

    Usage:
        msgs = await test_message_factory(conv_id, count=3)
        msgs = await test_message_factory(conv_id, direction='outbound', sent_by='ai')
    """
    from sqlalchemy import text as _t

    counter = {"n": 0}

    async def _make(
        conversation_id,
        count: int = 1,
        direction: str = "inbound",
        sent_by: str = "contact",
        text_prefix: str = "msg",
        workspace_id=None,
    ):
        rows = []
        for _ in range(count):
            counter["n"] += 1
            tmid = 100_000 + counter["n"]
            r = (await async_db_session.execute(_t("""
                INSERT INTO messages
                    (workspace_id, conversation_id, direction, message_text,
                     sent_by, telegram_message_id)
                VALUES (:wid, :cid, :dir, :txt, :sb, :tmid)
                RETURNING id, conversation_id, direction, message_text,
                          sent_by, telegram_message_id, created_at
            """), {
                "wid": str(workspace_id) if workspace_id else None,
                "cid": str(conversation_id),
                "dir": direction,
                "txt": f"{text_prefix}-{counter['n']}",
                "sb": sent_by,
                "tmid": tmid,
            })).first()
            rows.append(dict(r._mapping))
        await async_db_session.commit()
        return rows

    return _make


# ─── Phase 14 fixtures: Telethon client mock (probe / importContacts fallback) ──

@pytest_asyncio.fixture
async def mock_telethon_client():
    """Minimal mocked Telethon client for the Phase 14 checker tests.

    Phase 14 needs to exercise the resolve path (`ResolvePhoneRequest`), the
    importContacts fallback (`ImportContactsRequest`) and the address-book cleanup
    (`DeleteContactsRequest`) without a live Telegram session (RESV-01/D-02). No
    such fixture existed in conftest before Phase 14 (verified 2026-06-26), so this
    is the canonical mock the 14-03 `test_import_fallback_and_cleanup` test depends
    on.

    The returned object is an `AsyncMock` whose `__call__` (i.e. `await client(req)`)
    dispatches on the request type name and returns a configurable response. Tests
    drive behaviour by mutating the per-request-type response map, e.g.:

        client = mock_telethon_client
        client.set_response("ResolvePhoneRequest", _resolved_users(telegram_id=123))
        client.set_response("ImportContactsRequest", _imported(telegram_id=123))
        res = await client(SomeResolvePhoneRequest(phone="+79990001111"))

    `client.calls` records `(request_type_name, request_obj)` tuples in order so a
    test can assert that `DeleteContactsRequest` was invoked after an import.
    """
    from unittest.mock import AsyncMock

    class _MockTelethonClient(AsyncMock):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # request-type-name -> response object (or callable(request)->response)
            self._responses: dict[str, object] = {}
            # ordered log of (request_type_name, request_obj)
            self.calls: list[tuple[str, object]] = []

        def set_response(self, request_type_name: str, response) -> None:
            self._responses[request_type_name] = response

        async def __call__(self, request, *args, **kwargs):  # noqa: D401
            name = type(request).__name__
            self.calls.append((name, request))
            resp = self._responses.get(name)
            if callable(resp):
                return resp(request)
            return resp

        # Connection lifecycle no-ops so `async with` / connect/disconnect work.
        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def is_user_authorized(self):
            return True

    return _MockTelethonClient()


# ─── Phase 21 fixtures: bulk account-import (synthetic session + stubbed Telethon) ──
# The vendor .session sample is gitignored / holds a LIVE auth_key — never read or commit
# it in tests. Instead we synthesize a SQLiteSession on disk with a fake dc + 256-byte
# auth_key so the offline SQLite→StringSession conversion (IMPT-03) runs with no network.


def _valid_string_session_blob() -> str:
    """A real (empty-auth-key) Telethon StringSession string — round-trippable.

    ``StringSession(<blob>)`` raises ValueError on a non-Telethon string, so the import
    stub must hand back a genuine one (mirror tests/test_onboarding.py). This is what the
    stubbed connected client's ``.session.save()`` returns.
    """
    from telethon.crypto import AuthKey
    from telethon.sessions import StringSession

    s = StringSession()
    s.set_dc(2, "149.154.167.40", 443)
    s.auth_key = AuthKey(b"\x00" * 256)
    return s.save()


@pytest.fixture
def build_vendor_sqlite_session():
    """Return a builder for a SYNTHETIC vendor SQLiteSession file on disk (no live sample).

    Usage:
        path = build_vendor_sqlite_session(tmp_path)                 # dc 2, auth_key 0x11*256
        path = build_vendor_sqlite_session(tmp_path, dc_id=4, auth_key_byte=0x22)

    Constructs a real telethon ``SQLiteSession`` with a fake dc_id + 256-byte auth_key and
    flushes it to ``<tmp_path>/vendor_account.session``. Returns the file path; read its
    bytes (``open(path, "rb").read()``) for the ``session_blob`` an import item carries.
    Round-trips offline: SQLite → StringSession conversion (IMPT-03) needs no Telegram net.
    """
    def _build(tmp_path, dc_id: int = 2, server: str = "149.154.167.40",
               port: int = 443, auth_key_byte: int = 0x11) -> str:
        from telethon.crypto import AuthKey
        from telethon.sessions import SQLiteSession

        base = str(tmp_path / "vendor_account")
        s = SQLiteSession(base)              # SQLiteSession appends '.session'
        s.set_dc(dc_id, server, port)
        s.auth_key = AuthKey(bytes([auth_key_byte]) * 256)
        s.save()                             # commit to sqlite on disk
        s.close()
        return base + ".session"

    return _build


@pytest.fixture
def stub_import_telethon(monkeypatch):
    """Reusable stubbed Telethon client for account-import tests — NO Telegram network.

    Returns a namespace with:
      * ``.client`` — a MagicMock that quacks like a CONNECTED TelegramClient
        (``connect`` / ``disconnect`` / ``is_connected`` / ``is_user_authorized`` /
        ``get_me`` + a ``.session`` whose ``.save()`` yields a VALID empty-auth-key
        StringSession string). ``get_me`` returns a SimpleNamespace(id/username/
        first_name/last_name/phone) — mutate ``.get_me.return_value`` per test (e.g. to
        force a duplicate telegram_id for the dedup path).
      * ``.install(module)`` — monkeypatch ``make_telegram_client`` on the given module
        object to return ``.client``. Call it after the (deferred) import of the
        account-import module lands, so the import routine never touches the network.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock(name="StubImportTelethonClient")
    client.connect = AsyncMock(return_value=None)
    client.disconnect = AsyncMock(return_value=None)
    client.is_connected = MagicMock(return_value=True)
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_me = AsyncMock(return_value=SimpleNamespace(
        id=778899, username="imported_user", first_name="Imported",
        last_name=None, phone="18646884306",
    ))
    session = MagicMock()
    session.save = MagicMock(return_value=_valid_string_session_blob())
    client.session = session

    def _install(module):
        def _factory(session, proxy=None, **kwargs):
            return client
        monkeypatch.setattr(module, "make_telegram_client", _factory, raising=False)
        return client

    return SimpleNamespace(client=client, install=_install)
