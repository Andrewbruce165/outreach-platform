"""
Tests для миграции 012_workspace.sql.

Покрывает TENT-01: все 11 tenant-scoped таблиц получили NOT NULL workspace_id UUID FK.

Стратегия:
- _setup_database fixture (conftest.py, session-scope) применила миграцию.
- Здесь читаем information_schema.columns и information_schema.table_constraints,
  проверяем что схема соответствует ожидаемой.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


TENANT_SCOPED_TABLES = [
    "senders",
    "messages_log",
    "contacts_cache",
    "ai_contexts",
    "message_queue",
    "conversations",
    "warmup_pool",
    "warmup_sessions",
    "warmup_messages",
    "proxy_pool",
    "context_contact_assignments",
]

NEW_TABLES = ["workspaces", "user_workspaces", "workspace_api_keys"]


@pytest.mark.parametrize("table", TENANT_SCOPED_TABLES)
async def test_workspace_id_column_exists_not_null(
    async_db_session: AsyncSession, table: str
):
    """Каждая из 11 tenant-scoped таблиц должна иметь NOT NULL UUID workspace_id."""
    result = await async_db_session.execute(
        text(
            """
            SELECT data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = :t AND column_name = 'workspace_id'
            """
        ),
        {"t": table},
    )
    row = result.fetchone()
    assert row is not None, f"{table}: column 'workspace_id' is missing"
    assert row[0] == "uuid", f"{table}: workspace_id type is {row[0]}, expected uuid"
    assert row[1] == "NO", f"{table}: workspace_id is nullable, expected NOT NULL"


@pytest.mark.parametrize("table", TENANT_SCOPED_TABLES)
async def test_workspace_id_has_fk_cascade(
    async_db_session: AsyncSession, table: str
):
    """FK workspace_id → workspaces.id с ON DELETE CASCADE на каждой таблице."""
    result = await async_db_session.execute(
        text(
            """
            SELECT tc.constraint_name, rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_name = :t
              AND tc.constraint_type = 'FOREIGN KEY'
              AND kcu.column_name = 'workspace_id'
            """
        ),
        {"t": table},
    )
    row = result.fetchone()
    assert row is not None, f"{table}: no FK constraint on workspace_id"
    assert row[1] == "CASCADE", f"{table}: delete_rule is {row[1]}, expected CASCADE"


@pytest.mark.parametrize("table", NEW_TABLES)
async def test_new_table_exists(async_db_session: AsyncSession, table: str):
    """workspaces / user_workspaces / workspace_api_keys существуют."""
    result = await async_db_session.execute(
        text(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = :t AND table_schema = 'public'
            """
        ),
        {"t": table},
    )
    assert result.fetchone() is not None, f"Table {table} missing after migration 012"


async def test_user_workspaces_role_check_constraint(async_db_session: AsyncSession):
    """user_workspaces.role имеет CHECK constraint (anti-pattern protection)."""
    result = await async_db_session.execute(
        text(
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class cls ON cls.oid = con.conrelid
            WHERE cls.relname = 'user_workspaces'
              AND con.contype = 'c'
              AND con.conname = 'user_workspaces_role_check'
            """
        )
    )
    assert result.fetchone() is not None, (
        "user_workspaces_role_check constraint missing"
    )


async def test_workspace_api_keys_partial_index(async_db_session: AsyncSession):
    """Partial индекс по prefix WHERE revoked_at IS NULL (C-02)."""
    result = await async_db_session.execute(
        text(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'workspace_api_keys'
              AND indexname = 'idx_workspace_api_keys_prefix_active'
            """
        )
    )
    row = result.fetchone()
    assert row is not None, "partial index on workspace_api_keys missing"
    assert "revoked_at IS NULL" in row[0], (
        f"index def missing WHERE clause: {row[0]}"
    )


async def test_no_unique_on_supabase_user_id(async_db_session: AsyncSession):
    """D-10: НЕТ UNIQUE constraint на user_workspaces.supabase_user_id."""
    result = await async_db_session.execute(
        text(
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class cls ON cls.oid = con.conrelid
            JOIN pg_attribute att ON att.attrelid = cls.oid
            WHERE cls.relname = 'user_workspaces'
              AND con.contype = 'u'
              AND att.attname = 'supabase_user_id'
              AND att.attnum = ANY(con.conkey)
            """
        )
    )
    assert result.fetchone() is None, (
        "D-10 violation: UNIQUE constraint on supabase_user_id (must be many-to-many)"
    )
