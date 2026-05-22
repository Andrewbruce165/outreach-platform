"""Plan 04-04 Task 5: message_queue.campaign_id + conversations.campaign_id propagation.

Covers CAMP-17 (queue учитывает campaign_id):
- message_queue.campaign_id NULLable per AUDIT Q1.
- ON DELETE SET NULL on campaign hard-delete (D-07).
- INSERT в conversations расширен на campaign_id (D-05).
- Both queue.py TODO(phase-4) markers закрыты (B1 revision).
- enqueue_file accepts campaign_id (B1 revision).
"""

import inspect

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def test_message_queue_campaign_id_nullable_per_audit_q1(
    async_db_session,
    test_sender_factory,
    test_workspace,
):
    """INSERT в message_queue без campaign_id — успешно (legacy items support)."""
    s = await test_sender_factory()
    row = (await async_db_session.execute(text("""
        INSERT INTO message_queue
            (workspace_id, sender_id, item_type, status,
             recipient_phone, message_text, campaign_id)
        VALUES (:wid, :sid, 'message', 'pending',
                '+79990001112', 'Hi (legacy)', NULL)
        RETURNING id, campaign_id
    """), {"wid": str(test_workspace.id), "sid": str(s.id)})).first()
    await async_db_session.commit()
    assert row is not None
    assert row[1] is None  # campaign_id remains NULL


async def test_message_queue_campaign_id_fk_set_null_on_delete(
    async_db_session,
    test_campaign_factory,
    test_sender_factory,
    test_workspace,
):
    """DELETE campaign → existing queue items.campaign_id → NULL (D-07 ON DELETE SET NULL)."""
    camp = await test_campaign_factory(status="draft")
    s = await test_sender_factory()
    qrow = (await async_db_session.execute(text("""
        INSERT INTO message_queue
            (workspace_id, sender_id, item_type, status,
             recipient_phone, message_text, campaign_id)
        VALUES (:wid, :sid, 'message', 'sent',
                '+79991234567', 'Done', :cid)
        RETURNING id
    """), {
        "wid": str(test_workspace.id),
        "sid": str(s.id),
        "cid": str(camp["id"]),
    })).first()
    queue_id = qrow[0]
    await async_db_session.commit()

    # Hard-delete the (draft) campaign.
    await async_db_session.execute(
        text("DELETE FROM campaigns WHERE id = :cid"),
        {"cid": str(camp["id"])},
    )
    await async_db_session.commit()

    # Queue item.campaign_id should be NULL now.
    cid_after = (await async_db_session.execute(text("""
        SELECT campaign_id FROM message_queue WHERE id = :qid
    """), {"qid": str(queue_id)})).scalar()
    assert cid_after is None


async def test_conversations_campaign_id_set_at_first_send(
    async_db_session,
    test_running_campaign_factory,
):
    """D-05: INSERT в conversations при первой отправке campaign'ской очереди → campaign_id заполнен.

    Тест моделирует path через QueueWorker._upsert_conversation. Поскольку
    реальный send в Telegram нельзя автотестировать, проверяем prepared INSERT
    напрямую — расширенный за счёт campaign_id (миграция 016 + Plan 04-04 Task 5).
    """
    camp, senders = await test_running_campaign_factory(sender_count=1)
    sender = senders[0]
    phone = "+79994443322"

    # Simulate _upsert_conversation INSERT путь.
    r = (await async_db_session.execute(text("""
        INSERT INTO conversations
            (workspace_id, sender_id, contact_phone, contact_name,
             contact_telegram_id, ai_enabled, ai_context_id, campaign_id)
        VALUES (:wid, :sid, :phone, 'Alice', 123456789, true, :aid, :cid)
        RETURNING id, campaign_id, ai_context_id
    """), {
        "wid": str(sender.workspace_id),
        "sid": str(sender.id),
        "phone": phone,
        "aid": str(camp["agent_id"]),
        "cid": str(camp["id"]),
    })).first()
    await async_db_session.commit()

    assert r is not None
    assert str(r[1]) == str(camp["id"])
    assert str(r[2]) == str(camp["agent_id"])


async def test_queue_todo_phase4_resolved():
    """L2: both queue.py TODO markers закрыты (:708 + :849 per B1)."""
    import app.services.queue as q
    src = open(q.__file__).read()
    # queue.py:708 (old) — INSERT conversations branch.
    assert "TODO(phase-4): pull from conversation.campaign_id JOIN" not in src
    # queue.py:849 (old) — enqueue_file branch (B1 revision).
    assert "TODO(phase-4): apply same ai_context_id propagation as enqueue_message" not in src
    # No remaining TODO(phase-4) anywhere in queue.py.
    assert "TODO(phase-4)" not in src


async def test_enqueue_file_signature_accepts_campaign_id():
    """B1: enqueue_file принимает campaign_id (как enqueue_message)."""
    from app.services.queue import enqueue_file

    sig = inspect.signature(enqueue_file)
    assert "campaign_id" in sig.parameters
    assert sig.parameters["campaign_id"].default is None
