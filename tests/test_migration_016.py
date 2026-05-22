"""Migration 016 (Phase 4) — campaigns + campaign_senders + cca + +columns + DROP cca.

Tests cover:
- Idempotency (apply twice → no error)
- Schema shape (columns, FK, CHECK constraints, indexes)
- DROP of context_contact_assignments
- conversations.campaign_id NULLable + status CHECK extension
- message_queue.campaign_id NULLable per AUDIT Q1 + ON DELETE SET NULL semantics
"""

import pathlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIG_016 = (PROJECT_ROOT / "migrations" / "016_phase4.sql").read_text()


async def test_migration_016_idempotent(async_db_session):
    """Applying migration 016 twice does not fail (IF NOT EXISTS / DROP IF EXISTS).

    Migration is already applied once by conftest._setup_database fixture before
    this test runs. We apply it ONCE more here and check no error.
    """
    # Need autocommit (DROP CONSTRAINT / DROP TABLE inside transaction is fine,
    # but the migration wraps its own BEGIN/COMMIT — use exec_driver_sql via raw
    # connection to mimic conftest pattern).
    conn = await async_db_session.connection()
    raw_conn = await conn.get_raw_connection()
    # asyncpg connection is here:
    await raw_conn.driver_connection.execute(MIG_016)


async def test_campaigns_table_exists(async_db_session):
    cols = (await async_db_session.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'campaigns'
    """))).scalars().all()
    expected = {
        "id", "workspace_id", "agent_id", "folder_id",
        "name", "description", "status",
        "timezone", "work_hour_start", "work_hour_end", "work_days_mask",
        "start_date", "stop_date",
        "message_template",
        "lead_webhook_url", "handoff_webhook_url", "finish_webhook_url",
        "lead_trigger_hint", "handoff_trigger_hint", "finish_trigger_hint",
        "tools",
        "created_at", "updated_at",
    }
    missing = expected - set(cols)
    assert not missing, f"campaigns missing columns: {missing}"


async def test_campaigns_status_check_constraint(async_db_session, test_workspace, test_agent_factory, test_folder):
    """Status only accepts draft/running/paused/done (Q6 VARCHAR+CHECK)."""
    agent = await test_agent_factory()
    bad_status = "invalid_status"
    with pytest.raises(Exception) as exc_info:
        await async_db_session.execute(text("""
            INSERT INTO campaigns (workspace_id, agent_id, folder_id, name, status, message_template)
            VALUES (:wid, :aid, :fid, :name, :status, 'hi')
        """), {
            "wid": str(test_workspace.id),
            "aid": str(agent.id),
            "fid": str(test_folder.id),
            "name": f"Bad Status {uuid.uuid4()}",
            "status": bad_status,
        })
        await async_db_session.commit()
    await async_db_session.rollback()
    assert "campaigns_status_check" in str(exc_info.value) or "check constraint" in str(exc_info.value).lower()


async def test_campaigns_work_hours_check(async_db_session, test_workspace, test_agent_factory, test_folder):
    """work_hour_start must be < work_hour_end, both within [0,24]."""
    agent = await test_agent_factory()
    with pytest.raises(Exception):
        await async_db_session.execute(text("""
            INSERT INTO campaigns (workspace_id, agent_id, folder_id, name, work_hour_start, work_hour_end)
            VALUES (:wid, :aid, :fid, :name, 20, 9)
        """), {
            "wid": str(test_workspace.id),
            "aid": str(agent.id),
            "fid": str(test_folder.id),
            "name": f"Bad Hours {uuid.uuid4()}",
        })
        await async_db_session.commit()
    await async_db_session.rollback()


async def test_campaigns_work_days_check(async_db_session, test_workspace, test_agent_factory, test_folder):
    """work_days_mask must be between 1 and 127."""
    agent = await test_agent_factory()
    with pytest.raises(Exception):
        await async_db_session.execute(text("""
            INSERT INTO campaigns (workspace_id, agent_id, folder_id, name, work_days_mask)
            VALUES (:wid, :aid, :fid, :name, 200)
        """), {
            "wid": str(test_workspace.id),
            "aid": str(agent.id),
            "fid": str(test_folder.id),
            "name": f"Bad Days {uuid.uuid4()}",
        })
        await async_db_session.commit()
    await async_db_session.rollback()


async def test_campaign_senders_table_exists(async_db_session):
    cols = (await async_db_session.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'campaign_senders'
    """))).scalars().all()
    assert {"campaign_id", "sender_id", "workspace_id", "added_at"} <= set(cols)

    pk = (await async_db_session.execute(text("""
        SELECT a.attname
        FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = 'campaign_senders'::regclass AND i.indisprimary
    """))).scalars().all()
    assert set(pk) == {"campaign_id", "sender_id"}


async def test_campaign_contact_assignments_table_exists(async_db_session):
    cols = (await async_db_session.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'campaign_contact_assignments'
    """))).scalars().all()
    assert {"id", "workspace_id", "campaign_id", "contact_phone", "sender_id", "created_at"} <= set(cols)


async def test_context_contact_assignments_dropped(async_db_session):
    exists = (await async_db_session.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables WHERE table_name = 'context_contact_assignments'
        )
    """))).scalar()
    assert exists is False, "context_contact_assignments must be DROPPED by migration 016"


async def test_conversations_campaign_id_added_nullable(async_db_session):
    row = (await async_db_session.execute(text("""
        SELECT column_name, is_nullable, data_type
        FROM information_schema.columns
        WHERE table_name = 'conversations' AND column_name = 'campaign_id'
    """))).first()
    assert row is not None, "conversations.campaign_id missing"
    assert row[1] == "YES", "conversations.campaign_id must be NULLable"
    assert row[2] == "uuid"


async def test_conversations_status_check_extended(async_db_session, test_workspace, test_sender_factory):
    """conversations_status_check must accept new values lead/handoff/finished."""
    sender = await test_sender_factory()
    for st in ["lead", "handoff", "finished"]:
        await async_db_session.execute(text("""
            INSERT INTO conversations (id, workspace_id, sender_id, contact_phone, status)
            VALUES (:id, :wid, :sid, :phone, :st)
        """), {
            "id": str(uuid.uuid4()),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "phone": f"+790000{st[:4]}",
            "st": st,
        })
    await async_db_session.commit()
    # And rejects bogus values
    with pytest.raises(Exception):
        await async_db_session.execute(text("""
            INSERT INTO conversations (id, workspace_id, sender_id, contact_phone, status)
            VALUES (:id, :wid, :sid, :phone, 'invalid_xx')
        """), {
            "id": str(uuid.uuid4()),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "phone": "+79999999999",
        })
        await async_db_session.commit()
    await async_db_session.rollback()


async def test_message_queue_campaign_id_added_nullable_per_audit_q1(async_db_session):
    """Q1 override: message_queue.campaign_id is NULLable (NOT NOT NULL)."""
    row = (await async_db_session.execute(text("""
        SELECT column_name, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'message_queue' AND column_name = 'campaign_id'
    """))).first()
    assert row is not None, "message_queue.campaign_id missing"
    assert row[1] == "YES", (
        "Q1 override: message_queue.campaign_id MUST be NULLable. "
        "ON DELETE SET NULL requires NULLable per AUDIT 04-01 Section 6."
    )


async def test_queue_campaign_id_set_null_on_campaign_delete(
    async_db_session, test_workspace, test_sender_factory, test_agent_factory, test_folder,
):
    """When campaign is hard-deleted, message_queue.campaign_id rows become NULL (SET NULL FK)."""
    sender = await test_sender_factory()
    agent = await test_agent_factory()

    cid = str(uuid.uuid4())
    await async_db_session.execute(text("""
        INSERT INTO campaigns (id, workspace_id, agent_id, folder_id, name, status, message_template)
        VALUES (:cid, :wid, :aid, :fid, :name, 'draft', 'hi')
    """), {"cid": cid, "wid": str(test_workspace.id), "aid": str(agent.id),
           "fid": str(test_folder.id), "name": f"Del Camp {uuid.uuid4()}"})

    qid = str(uuid.uuid4())
    await async_db_session.execute(text("""
        INSERT INTO message_queue (id, workspace_id, sender_id, campaign_id, item_type, status, recipient_phone, message_text)
        VALUES (:id, :wid, :sid, :cid, 'message', 'sent', '+79990000001', 'done')
    """), {"id": qid, "wid": str(test_workspace.id), "sid": str(sender.id), "cid": cid})
    await async_db_session.commit()

    # Hard delete campaign
    await async_db_session.execute(text("DELETE FROM campaigns WHERE id = :cid"), {"cid": cid})
    await async_db_session.commit()

    # queue item should remain with campaign_id NULL
    row = (await async_db_session.execute(text("""
        SELECT campaign_id FROM message_queue WHERE id = :id
    """), {"id": qid})).first()
    assert row is not None, "message_queue row must NOT be cascade-deleted"
    assert row[0] is None, "campaign_id should be SET NULL after campaign delete"
