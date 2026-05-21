"""Phase 3 — queue.enqueue_message accepts explicit ai_context_id (D-06).

After migration 015 dropped senders.ai_context_id, the queue worker can no
longer read ai_context_id from sender. Callers must pass it explicitly via
enqueue_message(ai_context_id=...); it is stored in extra_data and
propagated to conversations.ai_context_id by _upsert_conversation.
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def test_enqueue_with_explicit_ai_context_id(
    async_db_session, test_sender_factory, test_agent_factory
):
    """Phase 3: enqueue_message получает ai_context_id напрямую (не из sender.ai_context_id)."""
    from app.services.queue import enqueue_message

    sender = await test_sender_factory(
        slug="enq-test-1", lifecycle_status="active", auth_status="ok"
    )
    agent = await test_agent_factory(name="Enqueue Agent")

    result = await enqueue_message(
        db=async_db_session,
        workspace_id=sender.workspace_id,
        sender_id=sender.id,
        sender_slug=sender.slug,
        recipient_phone="+79991234567",
        recipient_name="Test",
        message_text="hello",
        ai_context_id=agent.id,
    )
    assert "queue_id" in result

    # Проверяем что extra_data в message_queue содержит ai_context_id
    row = await async_db_session.execute(
        text("SELECT extra_data FROM message_queue WHERE id = :qid"),
        {"qid": result["queue_id"]},
    )
    extra = row.fetchone()[0]
    assert extra.get("ai_context_id") == str(agent.id)


async def test_enqueue_without_ai_context_id(
    async_db_session, test_sender_factory
):
    """Phase 3: ai_context_id=None разрешено (fall-back на legacy callers)."""
    from app.services.queue import enqueue_message

    sender = await test_sender_factory(
        slug="enq-test-2", lifecycle_status="active", auth_status="ok"
    )

    result = await enqueue_message(
        db=async_db_session,
        workspace_id=sender.workspace_id,
        sender_id=sender.id,
        sender_slug=sender.slug,
        recipient_phone="+79991234567",
        recipient_name="Test",
        message_text="hello",
        ai_context_id=None,
    )
    assert "queue_id" in result

    # extra_data should not include ai_context_id when not passed
    row = await async_db_session.execute(
        text("SELECT extra_data FROM message_queue WHERE id = :qid"),
        {"qid": result["queue_id"]},
    )
    extra = row.fetchone()[0]
    assert "ai_context_id" not in extra or extra.get("ai_context_id") is None
