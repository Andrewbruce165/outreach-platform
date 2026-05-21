"""
Tests для миграции 013_phase2.sql.

Покрывает Phase 2 D-01, D-05, D-11, D-13, D-16, D-21:
- folders / contacts / onboarding_sessions / csv_imports таблицы созданы
- CHECK constraints на role/lifecycle_status/tg_status/onboarding_sessions.status
- partial UNIQUE индексы по (workspace_id, phone) / (workspace_id, username)
- senders.is_active колонки больше не существует
- senders.lifecycle_status + rate_per_min/hour/day с правильными defaults

Стратегия:
- _setup_database fixture (conftest.py, session-scope) применила миграции 012 + 013.
- Здесь читаем information_schema + pg_indexes + пробуем нарушать CHECK через INSERT.
"""

import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


NEW_TABLES_PHASE2 = ["folders", "contacts", "onboarding_sessions", "csv_imports"]


@pytest.mark.parametrize("table", NEW_TABLES_PHASE2)
async def test_phase2_table_exists(async_db_session: AsyncSession, table: str):
    """Все 4 новых таблицы Phase 2 существуют после применения 013."""
    result = await async_db_session.execute(
        text(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = :t AND table_schema = 'public'
            """
        ),
        {"t": table},
    )
    assert result.fetchone() is not None, f"Table {table} missing after migration 013"


async def test_senders_is_active_column_dropped(async_db_session: AsyncSession):
    """D-11: senders.is_active колонки больше не существует."""
    result = await async_db_session.execute(
        text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'senders' AND column_name = 'is_active'
            """
        )
    )
    assert result.fetchone() is None, (
        "D-11 violation: senders.is_active still exists after migration 013"
    )


async def test_senders_new_columns_with_defaults(async_db_session: AsyncSession):
    """D-13: senders.lifecycle_status + rate_per_min/hour/day с правильными defaults."""
    result = await async_db_session.execute(
        text(
            """
            SELECT column_name, column_default, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'senders'
              AND column_name IN ('lifecycle_status', 'rate_per_min', 'rate_per_hour', 'rate_per_day')
            ORDER BY column_name
            """
        )
    )
    rows = {r[0]: (r[1], r[2]) for r in result.fetchall()}

    assert "lifecycle_status" in rows, "lifecycle_status column missing"
    assert "rate_per_min" in rows, "rate_per_min column missing"
    assert "rate_per_hour" in rows, "rate_per_hour column missing"
    assert "rate_per_day" in rows, "rate_per_day column missing"

    # NOT NULL на всех
    for col, (_, is_nullable) in rows.items():
        assert is_nullable == "NO", f"{col} should be NOT NULL"

    # Defaults
    assert "'active'" in rows["lifecycle_status"][0], (
        f"lifecycle_status default is {rows['lifecycle_status'][0]}"
    )
    assert rows["rate_per_min"][0] == "4", f"rate_per_min default is {rows['rate_per_min'][0]}"
    assert rows["rate_per_hour"][0] == "20", f"rate_per_hour default is {rows['rate_per_hour'][0]}"
    assert rows["rate_per_day"][0] == "150", f"rate_per_day default is {rows['rate_per_day'][0]}"


async def test_senders_role_check_constraint(async_db_session: AsyncSession):
    """D-21: senders.role имеет CHECK ('sender','checker')."""
    result = await async_db_session.execute(
        text(
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class cls ON cls.oid = con.conrelid
            WHERE cls.relname = 'senders'
              AND con.contype = 'c'
              AND con.conname = 'senders_role_check'
            """
        )
    )
    assert result.fetchone() is not None, "senders_role_check constraint missing"


async def test_senders_lifecycle_status_check_constraint(async_db_session: AsyncSession):
    """D-11: senders.lifecycle_status имеет CHECK ('active','warmup','paused')."""
    result = await async_db_session.execute(
        text(
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class cls ON cls.oid = con.conrelid
            WHERE cls.relname = 'senders'
              AND con.contype = 'c'
              AND con.conname = 'senders_lifecycle_status_check'
            """
        )
    )
    assert result.fetchone() is not None, (
        "senders_lifecycle_status_check constraint missing"
    )


async def test_contacts_tg_status_check_constraint(async_db_session: AsyncSession):
    """D-01: contacts.tg_status имеет CHECK constraint."""
    result = await async_db_session.execute(
        text(
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class cls ON cls.oid = con.conrelid
            WHERE cls.relname = 'contacts'
              AND con.contype = 'c'
              AND con.conname = 'contacts_tg_status_check'
            """
        )
    )
    assert result.fetchone() is not None, "contacts_tg_status_check missing"


async def test_contacts_phone_or_username_check_constraint(async_db_session: AsyncSession):
    """D-01: contacts должен иметь phone OR username (CHECK constraint)."""
    result = await async_db_session.execute(
        text(
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class cls ON cls.oid = con.conrelid
            WHERE cls.relname = 'contacts'
              AND con.contype = 'c'
              AND con.conname = 'contacts_phone_or_username_check'
            """
        )
    )
    assert result.fetchone() is not None, (
        "contacts_phone_or_username_check missing"
    )


async def test_contacts_partial_unique_phone_index(async_db_session: AsyncSession):
    """D-02: partial UNIQUE по (workspace_id, phone) WHERE phone IS NOT NULL."""
    result = await async_db_session.execute(
        text(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'contacts'
              AND indexname = 'idx_contacts_workspace_phone_unique'
            """
        )
    )
    row = result.fetchone()
    assert row is not None, "idx_contacts_workspace_phone_unique missing"
    assert "phone IS NOT NULL" in row[0], (
        f"Partial index WHERE clause missing: {row[0]}"
    )


async def test_contacts_partial_unique_username_index(async_db_session: AsyncSession):
    """D-02: partial UNIQUE по (workspace_id, username) WHERE username IS NOT NULL."""
    result = await async_db_session.execute(
        text(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'contacts'
              AND indexname = 'idx_contacts_workspace_username_unique'
            """
        )
    )
    row = result.fetchone()
    assert row is not None, "idx_contacts_workspace_username_unique missing"
    assert "username IS NOT NULL" in row[0], (
        f"Partial index WHERE clause missing: {row[0]}"
    )


async def test_contacts_partial_unique_rejects_duplicate_phone(async_db_session: AsyncSession):
    """D-02 runtime: вставка двух контактов с одинаковым phone в одном workspace → IntegrityError."""
    # Создаём workspace + folder
    ws_id = uuid.uuid4()
    folder_id = uuid.uuid4()
    await async_db_session.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:wid, 'TestWS013')"),
        {"wid": str(ws_id)},
    )
    await async_db_session.execute(
        text("INSERT INTO folders (id, workspace_id, name) VALUES (:fid, :wid, 'F1')"),
        {"fid": str(folder_id), "wid": str(ws_id)},
    )
    # Первый INSERT — OK
    await async_db_session.execute(
        text(
            "INSERT INTO contacts (workspace_id, folder_id, phone) "
            "VALUES (:wid, :fid, '+79001234567')"
        ),
        {"wid": str(ws_id), "fid": str(folder_id)},
    )
    await async_db_session.commit()

    # Второй INSERT с тем же phone — должен упасть.
    with pytest.raises(IntegrityError):
        await async_db_session.execute(
            text(
                "INSERT INTO contacts (workspace_id, folder_id, phone) "
                "VALUES (:wid, :fid, '+79001234567')"
            ),
            {"wid": str(ws_id), "fid": str(folder_id)},
        )
        await async_db_session.commit()


async def test_contacts_invalid_tg_status_rejected(async_db_session: AsyncSession):
    """contacts.tg_status='invalid' → IntegrityError (CHECK)."""
    ws_id = uuid.uuid4()
    folder_id = uuid.uuid4()
    await async_db_session.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:wid, 'TestWS013b')"),
        {"wid": str(ws_id)},
    )
    await async_db_session.execute(
        text("INSERT INTO folders (id, workspace_id, name) VALUES (:fid, :wid, 'F2')"),
        {"fid": str(folder_id), "wid": str(ws_id)},
    )
    await async_db_session.commit()

    with pytest.raises(IntegrityError):
        await async_db_session.execute(
            text(
                "INSERT INTO contacts (workspace_id, folder_id, phone, tg_status) "
                "VALUES (:wid, :fid, '+79001111111', 'totally_invalid')"
            ),
            {"wid": str(ws_id), "fid": str(folder_id)},
        )
        await async_db_session.commit()


async def test_senders_invalid_role_rejected(async_db_session: AsyncSession):
    """senders.role='random' → IntegrityError (senders_role_check)."""
    ws_id = uuid.uuid4()
    await async_db_session.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:wid, 'TestWS013c')"),
        {"wid": str(ws_id)},
    )
    await async_db_session.commit()

    with pytest.raises(IntegrityError):
        await async_db_session.execute(
            text(
                "INSERT INTO senders (workspace_id, slug, name, phone, session_string, role) "
                "VALUES (:wid, 'invalid-role-slug', 'X', '+79009999999', 'stub', 'random_role')"
            ),
            {"wid": str(ws_id)},
        )
        await async_db_session.commit()


async def test_senders_invalid_lifecycle_status_rejected(async_db_session: AsyncSession):
    """senders.lifecycle_status='broken' → IntegrityError."""
    ws_id = uuid.uuid4()
    await async_db_session.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:wid, 'TestWS013d')"),
        {"wid": str(ws_id)},
    )
    await async_db_session.commit()

    with pytest.raises(IntegrityError):
        await async_db_session.execute(
            text(
                "INSERT INTO senders "
                "(workspace_id, slug, name, phone, session_string, role, lifecycle_status) "
                "VALUES (:wid, 'invalid-lc-slug', 'X', '+79008888888', 'stub', 'sender', 'broken')"
            ),
            {"wid": str(ws_id)},
        )
        await async_db_session.commit()
