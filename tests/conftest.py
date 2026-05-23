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

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

# Только теперь — импорты app:
from app.config import get_settings  # noqa: E402
from app.database import AsyncSessionLocal, engine, Base  # noqa: E402
from app.main import app  # noqa: E402

logger = logging.getLogger(__name__)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_database():
    """Создаёт схему перед всеми тестами и применяет миграцию 012."""
    import pathlib

    # Setup strategy:
    # 1. Base.metadata.create_all builds base tables from the ORM (defines `messages`,
    #    `ai_contexts`, `senders`, etc. that the migrations 001-011 ALTER but never CREATE).
    # 2. Migrations 012-018 then layer the Phase 1-5.1 extensions on top.
    # 3. The ORM has `default=uuid.uuid4` (Python-side) for id columns. Tests that do raw
    #    SQL INSERT need server-side defaults — we add them post-create_all.
    import asyncpg

    settings = get_settings()
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

    # 1. Wipe + create base tables from ORM
    asyncpg_conn = await asyncpg.connect(dsn=dsn)
    try:
        await asyncpg_conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await asyncpg_conn.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2. Defensive stub: cca was dropped from the ORM by Phase 4 (016) but migration 012
    #    still ALTERs it. Stub it minimally so 012 can run; 016 drops it again.
    # 3. Add server-side `gen_random_uuid()` default on UUID PKs so raw-SQL tests work.
    asyncpg_conn = await asyncpg.connect(dsn=dsn)
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
        ):
            try:
                await asyncpg_conn.execute(
                    f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT gen_random_uuid()"
                )
            except (asyncpg.exceptions.UndefinedTableError,
                    asyncpg.exceptions.UndefinedColumnError):
                # Table or `id` column not present (e.g. junction tables w/ composite PK).
                pass

        # Apply Phase 1-5.1 migrations 012-018 in order.
        for filename in (
            "012_workspace.sql",
            "013_phase2.sql",
            "014_phase2_1_hardening.sql",
            "015_phase3.sql",
            "016_phase4.sql",
            "017_phase5.sql",
            "018_phase5_1.sql",
        ):
            sql_text = (PROJECT_ROOT / "migrations" / filename).read_text()
            await asyncpg_conn.execute(sql_text)
    finally:
        await asyncpg_conn.close()

    yield

    # Teardown
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


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

    async def _make(**overrides) -> AIContext:
        counter["n"] += 1
        defaults = dict(
            workspace_id=test_workspace.id,
            name=f"Test Agent {counter['n']}",
            system_prompt="You are a helpful sales agent.",
            tone_of_voice="friendly",
            rules="Always be polite.",
            faq={},
            company_info="Test Co.",
            product_info="Test Product.",
        )
        defaults.update(overrides)
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
            c = Contact(
                workspace_id=test_workspace.id,
                folder_id=test_folder.id,
                phone=f"+7901000{i:04d}",
                full_name=f"Contact {i}",
                source="test",
                tg_status=tg_status,
                **overrides,
            )
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
                tools
            ) VALUES (
                :wid, :aid, :fid, :name, :desc, :status,
                :tz, :wstart, :wend, :wmask,
                :sd, :stop, :tpl,
                :lwurl, :hwurl, :fwurl,
                :lhint, :hhint, :fhint,
                CAST(:tools AS JSONB)
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
        return dict(row._mapping)

    return _make


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
