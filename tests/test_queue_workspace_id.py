"""Regression: queue.py worker INSERTs include workspace_id (CR-01, Phase 02.1-01).

After migration 012 the columns message_queue.workspace_id, messages_log.workspace_id
and conversations.workspace_id are NOT NULL. This file pins the worker invariants:

1. enqueue_message writes message_queue.workspace_id.
2. enqueue_file writes message_queue.workspace_id.
3. _fail_item writes messages_log.workspace_id (failed path).
"""

import uuid

import pytest
from sqlalchemy import text

from app.models import MessageQueue, MessageType, QueueItemStatus, QueueItemType
from app.services.queue import (
    QueueWorker,
    enqueue_file,
    enqueue_message,
)

pytestmark = pytest.mark.asyncio


# ─── Tests ─────────────────────────────────────────────────────────────────────


async def test_enqueue_message_writes_workspace_id(
    async_db_session, test_workspace, test_sender_factory
):
    """enqueue_message must persist workspace_id on the message_queue row."""
    sender = await test_sender_factory()

    result = await enqueue_message(
        async_db_session,
        workspace_id=test_workspace.id,
        sender_id=sender.id,
        sender_slug=sender.slug,
        recipient_phone="+79991234567",
        recipient_name="Test",
        message_text="hello workspace",
    )

    row = (
        await async_db_session.execute(
            text("SELECT workspace_id FROM message_queue WHERE id = :id"),
            {"id": result["queue_id"]},
        )
    ).fetchone()

    assert row is not None
    assert str(row[0]) == str(test_workspace.id)


async def test_enqueue_file_writes_workspace_id(
    async_db_session, test_workspace, test_sender_factory
):
    """enqueue_file must persist workspace_id on the message_queue row."""
    sender = await test_sender_factory()

    result = await enqueue_file(
        async_db_session,
        workspace_id=test_workspace.id,
        sender_id=sender.id,
        sender_slug=sender.slug,
        recipient_phone="+79991234567",
        recipient_name="Test",
        file_url="http://example.com/f.pdf",
        file_name="f.pdf",
        caption=None,
    )

    row = (
        await async_db_session.execute(
            text("SELECT workspace_id FROM message_queue WHERE id = :id"),
            {"id": result["queue_id"]},
        )
    ).fetchone()

    assert row is not None
    assert str(row[0]) == str(test_workspace.id)


async def test_fail_item_writes_messagelog_with_workspace_id(
    async_db_session, test_workspace, test_sender_factory
):
    """_fail_item must write messages_log.workspace_id on the terminal 'failed' branch.

    We bypass the worker loop and call _fail_item directly with attempts=MAX-1
    so that the next failure flips the item to 'failed' and emits a MessageLog row.
    """
    sender = await test_sender_factory()

    item = MessageQueue(
        workspace_id=test_workspace.id,
        sender_id=sender.id,
        item_type=QueueItemType.message,
        recipient_phone="+79991111111",
        recipient_name="Fail Target",
        message_text="will fail",
        status=QueueItemStatus.processing,
        attempts=2,  # next failure will be the 3rd → MAX_ATTEMPTS → 'failed'
    )
    async_db_session.add(item)
    await async_db_session.commit()
    await async_db_session.refresh(item)

    worker = QueueWorker()
    await worker._fail_item(async_db_session, item, "boom")

    # Verify message went to status='failed'
    refreshed = (
        await async_db_session.execute(
            text("SELECT status FROM message_queue WHERE id = :id"),
            {"id": str(item.id)},
        )
    ).fetchone()
    assert refreshed[0] == "failed"

    # MessageLog row must include workspace_id
    log_row = (
        await async_db_session.execute(
            text(
                "SELECT workspace_id, message_type FROM messages_log "
                "WHERE sender_id = :sid"
            ),
            {"sid": str(sender.id)},
        )
    ).fetchone()

    assert log_row is not None, "MessageLog row was not created on failure"
    assert str(log_row[0]) == str(test_workspace.id)
    assert log_row[1] == "failed"


async def test_upsert_conversation_writes_workspace_id(
    async_db_session, test_workspace, test_sender_factory
):
    """_upsert_conversation must persist workspace_id from sender on new conversations."""
    sender = await test_sender_factory()

    item = MessageQueue(
        workspace_id=test_workspace.id,
        sender_id=sender.id,
        item_type=QueueItemType.message,
        recipient_phone="+79992222222",
        recipient_name="Conv Target",
        message_text="hi",
        status=QueueItemStatus.sent,
    )
    async_db_session.add(item)
    await async_db_session.commit()
    await async_db_session.refresh(item)

    fake_tg_id = 1234567890
    result = {
        "success": True,
        "message_id": None,  # skip messages INSERT branch
        "recipient": {"telegram_id": fake_tg_id, "name": "Conv Target"},
    }

    worker = QueueWorker()
    await worker._upsert_conversation(async_db_session, sender, item, result)
    await async_db_session.commit()

    conv_row = (
        await async_db_session.execute(
            text(
                "SELECT workspace_id FROM conversations "
                "WHERE sender_id = :sid AND contact_telegram_id = :tg_id"
            ),
            {"sid": str(sender.id), "tg_id": fake_tg_id},
        )
    ).fetchone()

    assert conv_row is not None, "Conversation was not created"
    assert str(conv_row[0]) == str(test_workspace.id)
