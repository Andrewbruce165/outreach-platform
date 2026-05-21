"""Regression: listener.get_or_create_conversation writes workspace_id (CR-02).

After migration 012, conversations.workspace_id is NOT NULL. The listener
service must propagate it from senders (single source of truth) when creating
new conversations from incoming Telegram events.
"""

import pytest
from sqlalchemy import text

from app.services.listener import TelegramListener

pytestmark = pytest.mark.asyncio


async def test_get_or_create_conversation_writes_workspace_id(
    async_db_session, test_workspace, test_sender_factory
):
    sender = await test_sender_factory()

    listener = TelegramListener()
    conv = await listener.get_or_create_conversation(
        session=async_db_session,
        sender_id=str(sender.id),
        contact_phone="+79993334444",
        contact_name="Listener Target",
        contact_telegram_id=987654321,
    )

    assert conv["is_new"] is True

    row = (
        await async_db_session.execute(
            text("SELECT workspace_id FROM conversations WHERE id = :id"),
            {"id": conv["id"]},
        )
    ).fetchone()

    assert row is not None
    assert str(row[0]) == str(test_workspace.id)


async def test_get_or_create_conversation_existing_returns_same_row(
    async_db_session, test_workspace, test_sender_factory
):
    """If the conversation already exists, return it without changing workspace_id."""
    sender = await test_sender_factory()

    listener = TelegramListener()
    # First call → INSERT
    conv1 = await listener.get_or_create_conversation(
        session=async_db_session,
        sender_id=str(sender.id),
        contact_phone="+79993334445",
        contact_name="Existing",
        contact_telegram_id=123123123,
    )
    # Second call → SELECT existing
    conv2 = await listener.get_or_create_conversation(
        session=async_db_session,
        sender_id=str(sender.id),
        contact_phone="+79993334445",
        contact_name="Existing",
        contact_telegram_id=123123123,
    )

    assert conv1["id"] == conv2["id"]
    assert conv2["is_new"] is False

    # workspace_id remains the sender's workspace
    row = (
        await async_db_session.execute(
            text("SELECT workspace_id FROM conversations WHERE id = :id"),
            {"id": conv1["id"]},
        )
    ).fetchone()
    assert str(row[0]) == str(test_workspace.id)
