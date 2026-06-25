"""Phase 3 — migration 015 smoke + idempotency + schema invariants."""
import pathlib
import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.database import engine
from app.models import AIContext

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.asyncio


async def test_dropped_columns_absent(_setup_database):
    """Phase 3 D-01: 6 deprecated columns must be dropped from ai_contexts."""
    async with engine.connect() as conn:
        r = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='ai_contexts'"
        ))
        cols = {row[0] for row in r.fetchall()}
    for dropped in {"auto_pause_triggers", "webhook_functions", "document_webhook_url",
                    "max_message_length", "response_delay_seconds", "is_active"}:
        assert dropped not in cols, f"Column '{dropped}' must be dropped by migration 015"


async def test_senders_no_ai_context_id(_setup_database):
    """Phase 3 D-04: senders.ai_context_id must be dropped."""
    async with engine.connect() as conn:
        r = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='senders' AND column_name='ai_context_id'"
        ))
        assert r.fetchone() is None, "senders.ai_context_id must be dropped by migration 015"


async def test_unique_workspace_name(async_db_session, test_workspace):
    """Phase 3 D-02: UNIQUE (workspace_id, name) on ai_contexts."""
    a1 = AIContext(workspace_id=test_workspace.id, name="DupName", system_prompt="p1")
    async_db_session.add(a1)
    await async_db_session.commit()
    a2 = AIContext(workspace_id=test_workspace.id, name="DupName", system_prompt="p2")
    async_db_session.add(a2)
    with pytest.raises(IntegrityError):
        await async_db_session.commit()
    await async_db_session.rollback()


async def test_idempotent(migrations_raw_dsn):
    """Phase 3: migration 015 must be safely re-runnable (IF EXISTS / IF NOT EXISTS).

    Runs against the dedicated migrations DB (migrations_raw_dsn), NOT the shared session
    DB: 015 DROPs ai_contexts columns that migration 018 re-adds, so re-applying it on the
    shared DB would strip those columns and break every later test that inserts an agent.
    Raw asyncpg (simple query protocol) — multi-statement .sql can't go through SQLAlchemy
    exec_driver_sql (prepared statement → "cannot insert multiple commands").
    """
    sql_015 = (PROJECT_ROOT / "migrations" / "015_phase3.sql").read_text()
    conn = await asyncpg.connect(dsn=migrations_raw_dsn)
    try:
        await conn.execute(sql_015)
        await conn.execute(sql_015)  # re-running must not fail
    finally:
        await conn.close()
