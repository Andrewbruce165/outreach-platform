"""Migration 017 (Phase 5) — defensive messages CREATE + conversations.status CHECK
(bot_ignored) + llm_calls audit table + 3 composite indexes for analytics.

Tests cover:
- Idempotency (apply twice → no error)
- Schema shape (columns, FK, CHECK constraints, composite indexes)
- conversations.status CHECK extended to include 'bot_ignored'
- llm_calls FK behaviour: CASCADE on workspace/conversation, SET NULL on
  campaign/agent/sender
- 3 composite indexes on conversations present
"""

import pathlib
import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIG_017 = (PROJECT_ROOT / "migrations" / "017_phase5.sql").read_text()


# ─── 1. Migration idempotency & schema ────────────────────────────────────────


async def test_migration_017_applies_once(async_db_session):
    """Migration 017 already applied by conftest fixture — assert schema present."""
    cols = (await async_db_session.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'llm_calls'
    """))).scalars().all()
    expected = {
        "id", "workspace_id", "conversation_id", "campaign_id", "agent_id",
        "sender_id", "model", "prompt", "response_text", "tool_calls",
        "prompt_tokens", "completion_tokens", "total_tokens", "latency_ms",
        "error", "created_at",
    }
    missing = expected - set(cols)
    assert not missing, f"Missing llm_calls columns: {missing}"


async def test_migration_017_idempotent_double_apply(async_db_session):
    """Apply 017 again — must not fail (IF NOT EXISTS / DROP CONSTRAINT IF EXISTS)."""
    conn = await async_db_session.connection()
    raw = await conn.get_raw_connection()
    await raw.driver_connection.execute(MIG_017)


# ─── 2. conversations.status CHECK extension ──────────────────────────────────


async def test_conversations_check_accepts_bot_ignored(
    async_db_session, test_workspace, test_sender_factory
):
    """status='bot_ignored' must INSERT cleanly after migration 017."""
    sender = await test_sender_factory()
    cid = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO conversations (id, workspace_id, sender_id, contact_phone, status)
        VALUES (:cid, :wid, :sid, '+79001234567', 'bot_ignored')
    """), {
        "cid": str(cid),
        "wid": str(test_workspace.id),
        "sid": str(sender.id),
    })
    await async_db_session.commit()
    # No CheckViolation = pass.


async def test_conversations_check_rejects_unknown_status(
    async_db_session, test_workspace, test_sender_factory
):
    """Unknown status string must violate conversations_status_check."""
    from sqlalchemy.exc import IntegrityError

    sender = await test_sender_factory()
    with pytest.raises(IntegrityError):
        await async_db_session.execute(text("""
            INSERT INTO conversations (id, workspace_id, sender_id, contact_phone, status)
            VALUES (:cid, :wid, :sid, '+79001111111', 'nonexistent_status')
        """), {
            "cid": str(_uuid.uuid4()),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
        })
        await async_db_session.commit()
    await async_db_session.rollback()


# ─── 3. llm_calls FK behaviour ────────────────────────────────────────────────


async def test_llm_calls_cascade_on_workspace_delete(
    async_db_session, test_workspace, test_conversation_factory
):
    """DELETE FROM workspaces → llm_calls row CASCADE-deleted (workspace FK)."""
    conv = await test_conversation_factory()
    llm_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO llm_calls (id, workspace_id, conversation_id, model, prompt)
        VALUES (:lid, :wid, :cid, 'gpt-4o-mini', '{}'::jsonb)
    """), {
        "lid": str(llm_id),
        "wid": str(test_workspace.id),
        "cid": str(conv["id"]),
    })
    await async_db_session.commit()

    await async_db_session.execute(
        text("DELETE FROM workspaces WHERE id = :wid"),
        {"wid": str(test_workspace.id)},
    )
    await async_db_session.commit()

    cnt = (await async_db_session.execute(
        text("SELECT COUNT(*) FROM llm_calls WHERE id = :lid"),
        {"lid": str(llm_id)},
    )).scalar()
    assert cnt == 0


async def test_llm_calls_cascade_on_conversation_delete(
    async_db_session, test_conversation_factory
):
    """DELETE FROM conversations → llm_calls row CASCADE-deleted (conv FK)."""
    conv = await test_conversation_factory()
    llm_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO llm_calls (id, workspace_id, conversation_id, model, prompt)
        VALUES (:lid, :wid, :cid, 'gpt-4o-mini', '{}'::jsonb)
    """), {
        "lid": str(llm_id),
        "wid": str(conv["workspace_id"]),
        "cid": str(conv["id"]),
    })
    await async_db_session.commit()

    await async_db_session.execute(
        text("DELETE FROM conversations WHERE id = :cid"),
        {"cid": str(conv["id"])},
    )
    await async_db_session.commit()

    cnt = (await async_db_session.execute(
        text("SELECT COUNT(*) FROM llm_calls WHERE id = :lid"),
        {"lid": str(llm_id)},
    )).scalar()
    assert cnt == 0


async def test_llm_calls_set_null_on_campaign_delete(
    async_db_session, test_conversation_factory, test_campaign_factory
):
    """DELETE FROM campaigns → llm_calls.campaign_id NULLed (SET NULL FK)."""
    camp = await test_campaign_factory()
    conv = await test_conversation_factory(campaign_id=camp["id"])
    llm_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO llm_calls (id, workspace_id, conversation_id, campaign_id, model, prompt)
        VALUES (:lid, :wid, :cid, :camp, 'gpt-4o-mini', '{}'::jsonb)
    """), {
        "lid": str(llm_id),
        "wid": str(conv["workspace_id"]),
        "cid": str(conv["id"]),
        "camp": str(camp["id"]),
    })
    await async_db_session.commit()

    await async_db_session.execute(
        text("DELETE FROM campaigns WHERE id = :cid"),
        {"cid": str(camp["id"])},
    )
    await async_db_session.commit()

    camp_col = (await async_db_session.execute(text(
        "SELECT campaign_id FROM llm_calls WHERE id = :lid"
    ), {"lid": str(llm_id)})).scalar()
    assert camp_col is None


async def test_llm_calls_set_null_on_agent_delete(
    async_db_session, test_conversation_factory, test_agent_factory
):
    """DELETE FROM ai_contexts → llm_calls.agent_id NULLed (SET NULL FK)."""
    agent = await test_agent_factory()
    conv = await test_conversation_factory(ai_context_id=agent.id)
    llm_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO llm_calls (id, workspace_id, conversation_id, agent_id, model, prompt)
        VALUES (:lid, :wid, :cid, :aid, 'gpt-4o-mini', '{}'::jsonb)
    """), {
        "lid": str(llm_id),
        "wid": str(conv["workspace_id"]),
        "cid": str(conv["id"]),
        "aid": str(agent.id),
    })
    await async_db_session.commit()

    await async_db_session.execute(
        text("DELETE FROM ai_contexts WHERE id = :aid"),
        {"aid": str(agent.id)},
    )
    await async_db_session.commit()

    agent_col = (await async_db_session.execute(text(
        "SELECT agent_id FROM llm_calls WHERE id = :lid"
    ), {"lid": str(llm_id)})).scalar()
    assert agent_col is None


async def test_llm_calls_set_null_on_sender_delete(
    async_db_session, test_conversation_factory, test_sender_factory
):
    """DELETE FROM senders → llm_calls.sender_id NULLed (SET NULL FK).

    Уточнение: conversations.sender_id has CASCADE, so deleting the sender also
    deletes the conversation. Use a SECOND conversation that uses a different
    sender so the llm_calls row survives.
    """
    sender_for_conv = await test_sender_factory()
    sender_to_delete = await test_sender_factory()
    conv = await test_conversation_factory(sender=sender_for_conv)
    llm_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO llm_calls (id, workspace_id, conversation_id, sender_id, model, prompt)
        VALUES (:lid, :wid, :cid, :sid, 'gpt-4o-mini', '{}'::jsonb)
    """), {
        "lid": str(llm_id),
        "wid": str(conv["workspace_id"]),
        "cid": str(conv["id"]),
        "sid": str(sender_to_delete.id),
    })
    await async_db_session.commit()

    await async_db_session.execute(
        text("DELETE FROM senders WHERE id = :sid"),
        {"sid": str(sender_to_delete.id)},
    )
    await async_db_session.commit()

    sender_col = (await async_db_session.execute(text(
        "SELECT sender_id FROM llm_calls WHERE id = :lid"
    ), {"lid": str(llm_id)})).scalar()
    assert sender_col is None


# ─── 4. Composite indexes for analytics ───────────────────────────────────────


async def test_composite_indexes_exist(async_db_session):
    """3 composite indexes on conversations(workspace_id, X, status) (C-04)."""
    rows = (await async_db_session.execute(text("""
        SELECT indexname FROM pg_indexes WHERE tablename = 'conversations'
    """))).scalars().all()
    expected_indexes = {
        "idx_conversations_workspace_campaign_status",
        "idx_conversations_workspace_agent_status",
        "idx_conversations_workspace_sender_status",
    }
    missing = expected_indexes - set(rows)
    assert not missing, f"Missing indexes: {missing}"


async def test_llm_calls_indexes_exist(async_db_session):
    """Two indexes on llm_calls — workspace+created, conversation+created."""
    rows = (await async_db_session.execute(text("""
        SELECT indexname FROM pg_indexes WHERE tablename = 'llm_calls'
    """))).scalars().all()
    expected = {
        "idx_llm_calls_workspace_created",
        "idx_llm_calls_conversation_created",
    }
    missing = expected - set(rows)
    assert not missing, f"Missing llm_calls indexes: {missing}"
