"""
Tests для миграции 014_phase2_1_hardening.sql.

Закрывает:
- WR-02: senders.slug глобально UNIQUE → per-workspace UNIQUE (workspace_id, slug)
- CR-05: onboarding_sessions.original_sender_id (nullable FK) добавлен

Стратегия:
- Module-scope autouse fixture применяет 014 поверх 012+013 (которые уже применил
  session-scope _setup_database в conftest.py). Это работает потому что 014 —
  idempotent (IF EXISTS / IF NOT EXISTS), и применяется один раз для всего модуля.
- Дальше — обычные проверки information_schema / pg_constraint / pg_indexes
  + runtime assertions (INSERT'ы с одинаковым slug в разных workspace).
"""

import pathlib
import uuid

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION_014 = PROJECT_ROOT / "migrations" / "014_phase2_1_hardening.sql"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def apply_migration_014(migrations_raw_dsn):
    """Re-apply 014 on the DEDICATED migrations DB (migrations_raw_dsn), NOT the shared
    session DB. conftest._setup_database already applied 014 to the shared DB (which the
    schema-assertion tests below query); this only proves 014 re-applies cleanly without
    polluting the shared schema.

    Raw asyncpg (simple query protocol) — мульти-командный .sql нельзя гнать через
    SQLAlchemy exec_driver_sql (prepared statement → "cannot insert multiple commands").
    """
    conn = await asyncpg.connect(dsn=migrations_raw_dsn)
    try:
        await conn.execute(MIGRATION_014.read_text())
    finally:
        await conn.close()
    yield


# ─── Schema checks ───────────────────────────────────────────────────────────


async def test_senders_slug_global_unique_dropped(async_db_session: AsyncSession):
    """WR-02: senders_slug_key constraint больше не существует."""
    result = await async_db_session.execute(
        text(
            """
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'senders'::regclass
              AND conname = 'senders_slug_key'
            """
        )
    )
    assert result.fetchone() is None, (
        "senders_slug_key constraint должен быть удалён миграцией 014 (WR-02)"
    )


async def test_idx_senders_workspace_slug_exists(async_db_session: AsyncSession):
    """WR-02: per-workspace UNIQUE INDEX (workspace_id, slug) создан."""
    result = await async_db_session.execute(
        text(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'senders'
              AND indexname = 'idx_senders_workspace_slug'
            """
        )
    )
    row = result.fetchone()
    assert row is not None, "idx_senders_workspace_slug должен существовать"
    indexdef = row[0]
    assert "UNIQUE" in indexdef, f"index должен быть UNIQUE: {indexdef}"
    assert "workspace_id" in indexdef and "slug" in indexdef, (
        f"index должен быть на (workspace_id, slug): {indexdef}"
    )


async def test_onboarding_sessions_has_original_sender_id_column(
    async_db_session: AsyncSession,
):
    """CR-05: onboarding_sessions.original_sender_id колонка добавлена."""
    result = await async_db_session.execute(
        text(
            """
            SELECT column_name, is_nullable, data_type
            FROM information_schema.columns
            WHERE table_name = 'onboarding_sessions'
              AND column_name = 'original_sender_id'
            """
        )
    )
    row = result.fetchone()
    assert row is not None, "original_sender_id колонка должна быть добавлена"
    assert row[1] == "YES", "original_sender_id должна быть nullable (NULL = обычный onboarding)"
    assert row[2] == "uuid", f"тип должен быть uuid, получен {row[2]}"


async def test_onboarding_sessions_original_sender_id_fk(
    async_db_session: AsyncSession,
):
    """CR-05: original_sender_id — FK на senders(id) ON DELETE CASCADE."""
    result = await async_db_session.execute(
        text(
            """
            SELECT
                rc.delete_rule,
                ccu.table_name AS referenced_table,
                ccu.column_name AS referenced_column
            FROM information_schema.referential_constraints rc
            JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_name = rc.constraint_name
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = rc.constraint_name
            WHERE kcu.table_name = 'onboarding_sessions'
              AND kcu.column_name = 'original_sender_id'
            """
        )
    )
    row = result.fetchone()
    assert row is not None, "FK constraint на original_sender_id должен существовать"
    assert row[0] == "CASCADE", f"delete_rule должен быть CASCADE, получен {row[0]}"
    assert row[1] == "senders", f"referenced_table должна быть senders, получена {row[1]}"
    assert row[2] == "id", f"referenced_column должна быть id, получена {row[2]}"


async def test_idx_onboarding_sessions_original_sender_id_exists(
    async_db_session: AsyncSession,
):
    """CR-05: partial index на original_sender_id WHERE NOT NULL."""
    result = await async_db_session.execute(
        text(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'onboarding_sessions'
              AND indexname = 'idx_onboarding_sessions_original_sender_id'
            """
        )
    )
    row = result.fetchone()
    assert row is not None, "idx_onboarding_sessions_original_sender_id должен существовать"
    assert "original_sender_id IS NOT NULL" in row[0], (
        f"index должен быть partial WHERE original_sender_id IS NOT NULL: {row[0]}"
    )


# ─── Runtime behaviour: per-workspace slug uniqueness ───────────────────────


async def test_two_workspaces_can_share_slug(async_db_session: AsyncSession):
    """WR-02 runtime: два workspace могут иметь sender со одинаковым slug."""
    ws_a = uuid.uuid4()
    ws_b = uuid.uuid4()
    await async_db_session.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:wid, 'WS-A-014')"),
        {"wid": str(ws_a)},
    )
    await async_db_session.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:wid, 'WS-B-014')"),
        {"wid": str(ws_b)},
    )
    await async_db_session.commit()

    shared_slug = "sender-shared-014"
    # Sender в workspace A
    await async_db_session.execute(
        text(
            "INSERT INTO senders (workspace_id, slug, name, phone, session_string, role) "
            "VALUES (:wid, :slug, 'A', '+79001110001', 'stub', 'sender')"
        ),
        {"wid": str(ws_a), "slug": shared_slug},
    )
    # Sender в workspace B с тем же slug — должно пройти
    await async_db_session.execute(
        text(
            "INSERT INTO senders (workspace_id, slug, name, phone, session_string, role) "
            "VALUES (:wid, :slug, 'B', '+79001110002', 'stub', 'sender')"
        ),
        {"wid": str(ws_b), "slug": shared_slug},
    )
    await async_db_session.commit()

    # Проверяем, что оба sender'а в БД
    count = (
        await async_db_session.execute(
            text(
                "SELECT COUNT(*) FROM senders WHERE slug = :slug "
                "AND workspace_id IN (:wa, :wb)"
            ),
            {"slug": shared_slug, "wa": str(ws_a), "wb": str(ws_b)},
        )
    ).scalar()
    assert count == 2, f"оба sender'а с одинаковым slug должны быть в БД, найдено {count}"


async def test_same_workspace_cannot_duplicate_slug(async_db_session: AsyncSession):
    """WR-02 runtime: в пределах ОДНОГО workspace slug всё ещё UNIQUE."""
    from sqlalchemy.exc import IntegrityError

    ws_id = uuid.uuid4()
    await async_db_session.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:wid, 'WS-Dup-014')"),
        {"wid": str(ws_id)},
    )
    await async_db_session.commit()

    slug = "sender-dup-014"
    await async_db_session.execute(
        text(
            "INSERT INTO senders (workspace_id, slug, name, phone, session_string, role) "
            "VALUES (:wid, :slug, 'X', '+79002220001', 'stub', 'sender')"
        ),
        {"wid": str(ws_id), "slug": slug},
    )
    await async_db_session.commit()

    # Второй INSERT с тем же slug в тот же workspace — упадёт
    with pytest.raises(IntegrityError):
        await async_db_session.execute(
            text(
                "INSERT INTO senders (workspace_id, slug, name, phone, session_string, role) "
                "VALUES (:wid, :slug, 'Y', '+79002220002', 'stub', 'sender')"
            ),
            {"wid": str(ws_id), "slug": slug},
        )
        await async_db_session.commit()


# ─── Idempotency ────────────────────────────────────────────────────────────


async def test_migration_014_is_idempotent(migrations_raw_dsn):
    """Повторное применение 014 не падает (IF NOT EXISTS / IF EXISTS).

    Runs on the dedicated migrations DB so the re-application never touches the shared
    session schema.
    """
    sql = MIGRATION_014.read_text()
    conn = await asyncpg.connect(dsn=migrations_raw_dsn)
    try:
        await conn.execute(sql)
        await conn.execute(sql)  # повторно — должно пройти без ошибки
    finally:
        await conn.close()


# ─── ORM sync ────────────────────────────────────────────────────────────────


def test_sender_orm_slug_no_unique_attr():
    """ORM: Sender.slug не должен иметь unique=True (WR-02)."""
    from app.models import Sender

    col = Sender.__table__.c.slug
    # SQLAlchemy: после Column(String(50), nullable=False, index=True) — col.unique = None or False.
    assert not col.unique, (
        f"Sender.slug не должен иметь unique=True (WR-02). col.unique={col.unique}"
    )


def test_onboarding_session_orm_has_original_sender_id():
    """ORM: OnboardingSession.original_sender_id определён как Column (CR-05)."""
    from app.models import OnboardingSession

    cols = OnboardingSession.__table__.c
    assert "original_sender_id" in cols, (
        "OnboardingSession.original_sender_id колонка должна быть в ORM"
    )
    col = cols["original_sender_id"]
    assert col.nullable is True, "original_sender_id должна быть nullable"
