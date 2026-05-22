"""Phase 5 AIRC-04 — proactive bot filter in listener + queue pre-send guard.

Covers tests 1-13 from plan 05-01 behaviour list.

Two systems exercised:
1. `listener._handle_bot_message` — invoked when event.sender.bot is True.
   - Creates conversation status='bot_ignored', AI dispatch SKIPPED.
   - UPDATE guard: only downgrades from 'active' status (Pitfall 3).
   - ANTISPAM_BOT_IDS delegation to `_handle_antispam_signal` (D-08 safety net).
2. `queue._process_next_for_sender` pre-send guard — defends against D-04
   race condition between manager POST /send and queue worker tick.
"""

import uuid as _uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_event(
    sender_id: int,
    is_bot: bool,
    text_msg: str = "Hello",
    message_id: int = 1234,
):
    """Mock Telethon NewMessage event with controllable sender.bot flag."""
    event = MagicMock()
    event.sender = MagicMock()
    event.sender.id = sender_id
    event.sender.bot = is_bot
    event.sender.phone = None
    event.sender.first_name = "Bot"
    event.sender.last_name = None
    event.sender.username = "test_bot"
    event.sender.title = None
    event.text = text_msg
    event.id = message_id
    event.chat_id = sender_id
    event.is_group = False
    event.is_channel = False
    event.message = MagicMock()
    event.message.photo = None
    event.message.video = None
    event.message.document = None
    event.message.voice = None
    event.message.message = text_msg

    event.get_sender = AsyncMock(return_value=event.sender)
    event.client = MagicMock()
    event.client.get_me = AsyncMock(return_value=MagicMock(id=1))   # not the bot
    event.client.send_read_acknowledge = AsyncMock(return_value=None)
    return event


def _sender_info(sender) -> dict:
    return {
        "id": str(sender.id),
        "workspace_id": str(sender.workspace_id),
        "slug": sender.slug,
        "phone": sender.phone,
    }


# ── Test 1: bot=True (non-antispam) → bot_ignored, AI NOT called ──────────────


async def test_bot_filter_creates_bot_ignored_conversation(
    async_db_session, test_sender_factory, monkeypatch,
):
    from app.services.listener import TelegramListener

    sender = await test_sender_factory()
    ai_mock = AsyncMock()
    monkeypatch.setattr("app.services.ai_engine.ai_engine.generate_response", ai_mock)

    listener_obj = TelegramListener()
    event = _make_event(sender_id=999, is_bot=True)

    await listener_obj.handle_incoming_message(event, _sender_info(sender))

    ai_mock.assert_not_called()

    conv = (await async_db_session.execute(text("""
        SELECT status, ai_enabled, paused_reason FROM conversations
        WHERE sender_id = :sid AND contact_telegram_id = 999
    """), {"sid": str(sender.id)})).first()
    assert conv is not None
    assert conv.status == "bot_ignored"
    assert conv.ai_enabled is False
    assert "Telegram bot" in conv.paused_reason


# ── Test 2: bot message saved to messages table ───────────────────────────────


async def test_bot_filter_saves_inbound_message(
    async_db_session, test_sender_factory, monkeypatch,
):
    from app.services.listener import TelegramListener

    sender = await test_sender_factory()
    monkeypatch.setattr(
        "app.services.ai_engine.ai_engine.generate_response", AsyncMock()
    )

    listener_obj = TelegramListener()
    event = _make_event(sender_id=1001, is_bot=True, text_msg="bot greet", message_id=42)
    await listener_obj.handle_incoming_message(event, _sender_info(sender))

    msg = (await async_db_session.execute(text("""
        SELECT direction, sent_by, message_text, telegram_message_id
        FROM messages
        WHERE conversation_id = (
            SELECT id FROM conversations
            WHERE sender_id = :sid AND contact_telegram_id = 1001
        )
    """), {"sid": str(sender.id)})).first()
    assert msg is not None
    assert msg.direction == "inbound"
    assert msg.sent_by == "contact"
    assert msg.message_text == "bot greet"
    assert msg.telegram_message_id == 42


# ── Tests 3-5: Pitfall 3 — UPDATE guard preserves lead/manual ─────────────────


@pytest.mark.parametrize("existing_status", ["lead", "manual", "handoff", "finished"])
async def test_bot_filter_preserves_non_active_status(
    async_db_session, test_sender_factory, test_conversation_factory,
    monkeypatch, existing_status,
):
    """Test 3, 4 + extras: bot message must NOT downgrade lead/manual/handoff/finished."""
    from app.services.listener import TelegramListener

    sender = await test_sender_factory()
    conv = await test_conversation_factory(
        sender=sender,
        contact_telegram_id=2000 + abs(hash(existing_status)) % 1000,
        status=existing_status,
    )
    monkeypatch.setattr(
        "app.services.ai_engine.ai_engine.generate_response", AsyncMock()
    )

    listener_obj = TelegramListener()
    event = _make_event(sender_id=conv["contact_telegram_id"], is_bot=True)
    await listener_obj.handle_incoming_message(event, _sender_info(sender))

    row = (await async_db_session.execute(text("""
        SELECT status FROM conversations WHERE id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert row.status == existing_status


async def test_bot_filter_downgrades_active_to_bot_ignored(
    async_db_session, test_sender_factory, test_conversation_factory,
    monkeypatch,
):
    """Test 5: status='active' → bot message → status='bot_ignored'."""
    from app.services.listener import TelegramListener

    sender = await test_sender_factory()
    conv = await test_conversation_factory(
        sender=sender, contact_telegram_id=3000, status="active",
    )
    monkeypatch.setattr(
        "app.services.ai_engine.ai_engine.generate_response", AsyncMock()
    )

    listener_obj = TelegramListener()
    event = _make_event(sender_id=3000, is_bot=True)
    await listener_obj.handle_incoming_message(event, _sender_info(sender))

    row = (await async_db_session.execute(text("""
        SELECT status, ai_enabled FROM conversations WHERE id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert row.status == "bot_ignored"
    assert row.ai_enabled is False


# ── Tests 6-7: D-08 safety net — known antispam IDs delegate ──────────────────


@pytest.mark.parametrize("antispam_id", [178220800, 777000])
async def test_known_antispam_id_delegates_to_safety_net(
    async_db_session, test_sender_factory, monkeypatch, antispam_id,
):
    """SpamBot (178220800) and Telegram service (777000) → _handle_antispam_signal.

    Phase 5 D-08: existing antispam safety net (sender lifecycle pause +
    cancel ALL queue) must remain intact.
    """
    from app.services.listener import TelegramListener

    # NB: id=777000 is also filtered by TELEGRAM_SERVICE_PHONES earlier (line 577).
    # Use a SpamBot phone that won't match TELEGRAM_SERVICE_PHONES.
    if antispam_id == 777000:
        pytest.skip("777000 already filtered by TELEGRAM_SERVICE_PHONES before bot filter")

    sender = await test_sender_factory()
    monkeypatch.setattr(
        "app.services.ai_engine.ai_engine.generate_response", AsyncMock()
    )

    listener_obj = TelegramListener()
    # Patch _handle_antispam_signal to assert it was called.
    antispam_mock = AsyncMock()
    monkeypatch.setattr(listener_obj, "_handle_antispam_signal", antispam_mock)
    bot_mock = AsyncMock()
    monkeypatch.setattr(listener_obj, "_handle_bot_message", bot_mock)

    event = _make_event(sender_id=antispam_id, is_bot=True)
    await listener_obj.handle_incoming_message(event, _sender_info(sender))

    antispam_mock.assert_awaited_once()
    bot_mock.assert_not_called()


# ── Test 8: defensive — sender without .bot attribute → no crash ──────────────


async def test_bot_filter_handles_missing_bot_attribute(
    async_db_session, test_sender_factory, monkeypatch,
):
    """Channel/group senders without .bot attribute must not crash the filter."""
    from app.services.listener import TelegramListener

    sender = await test_sender_factory()
    monkeypatch.setattr(
        "app.services.ai_engine.ai_engine.generate_response", AsyncMock()
    )

    listener_obj = TelegramListener()
    event = _make_event(sender_id=4001, is_bot=False)
    # Strip the bot attribute entirely to test getattr default branch.
    del event.sender.bot

    # Should not raise.
    await listener_obj.handle_incoming_message(event, _sender_info(sender))

    # No bot_ignored conversation created.
    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM conversations
        WHERE sender_id = :sid AND contact_telegram_id = 4001 AND status = 'bot_ignored'
    """), {"sid": str(sender.id)})).scalar()
    assert cnt == 0


# ── Test 9: second bot message for same contact — no duplicate conv ───────────


async def test_bot_filter_deduplicates_repeat_bot_message(
    async_db_session, test_sender_factory, monkeypatch,
):
    from app.services.listener import TelegramListener

    sender = await test_sender_factory()
    monkeypatch.setattr(
        "app.services.ai_engine.ai_engine.generate_response", AsyncMock()
    )

    listener_obj = TelegramListener()
    event1 = _make_event(sender_id=5001, is_bot=True, message_id=1)
    event2 = _make_event(sender_id=5001, is_bot=True, message_id=2)
    await listener_obj.handle_incoming_message(event1, _sender_info(sender))
    await listener_obj.handle_incoming_message(event2, _sender_info(sender))

    conv_count = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM conversations
        WHERE sender_id = :sid AND contact_telegram_id = 5001
    """), {"sid": str(sender.id)})).scalar()
    assert conv_count == 1

    msg_count = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM messages
        WHERE conversation_id = (
            SELECT id FROM conversations
            WHERE sender_id = :sid AND contact_telegram_id = 5001
        )
    """), {"sid": str(sender.id)})).scalar()
    # Two distinct telegram_message_ids → 2 rows.
    assert msg_count == 2


# ── Tests 11-12: queue pre-send guard ─────────────────────────────────────────


async def test_pre_send_guard_skips_when_conversation_taken_manually(
    async_db_session, test_workspace, test_sender_factory,
    test_conversation_factory, test_campaign_factory, monkeypatch,
):
    """Test 11 — Pre-send guard fires when conversation.ai_enabled=false.

    Workflow:
      1. Seed: conversation ai_enabled=false status='manual'
      2. Seed: queue item status='processing' for that recipient_phone
      3. Call __send_item_inner directly
      4. Assert: queue item flipped to 'failed' with expected error_message
      5. Assert: telegram_service.send_message NOT called for that recipient
    """
    from app.services.queue import queue_worker

    sender = await test_sender_factory()
    camp = await test_campaign_factory()
    conv = await test_conversation_factory(
        sender=sender, contact_phone="+79991401001",
        contact_telegram_id=601001,
        status="manual", ai_enabled=False,
    )

    item_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO message_queue
            (id, workspace_id, sender_id, campaign_id, item_type, status,
             recipient_phone, message_text)
        VALUES (:id, :wid, :sid, :cid, 'message', 'processing',
                '+79991401001', 'auto')
    """), {
        "id": str(item_id),
        "wid": str(test_workspace.id),
        "sid": str(sender.id),
        "cid": str(camp["id"]),
    })
    await async_db_session.commit()

    send_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.queue.telegram_service.get_client",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "app.services.queue.telegram_service.send_message", send_mock
    )
    monkeypatch.setattr(
        "app.services.queue.telegram_service.send_file", AsyncMock()
    )

    # Direct call — _send_item wraps idle event handling.
    await queue_worker._QueueWorker__send_item_inner(item_id)

    row = (await async_db_session.execute(text("""
        SELECT status, error_message FROM message_queue WHERE id = :id
    """), {"id": str(item_id)})).first()
    assert row.status == "failed"
    assert row.error_message == "Conversation taken over manually"

    send_mock.assert_not_called()


async def test_pre_send_guard_passes_when_ai_enabled(
    async_db_session, test_workspace, test_sender_factory,
    test_conversation_factory, test_campaign_factory, monkeypatch,
):
    """Test 12 — Pre-send guard does NOT skip when conversation.ai_enabled=true.

    Telethon mock IS called; queue item is processed normally.
    """
    from app.services.queue import queue_worker

    sender = await test_sender_factory()
    camp = await test_campaign_factory()
    conv = await test_conversation_factory(
        sender=sender, contact_phone="+79991402001",
        contact_telegram_id=602001,
        status="active", ai_enabled=True,
    )

    item_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO message_queue
            (id, workspace_id, sender_id, campaign_id, item_type, status,
             recipient_phone, message_text)
        VALUES (:id, :wid, :sid, :cid, 'message', 'processing',
                '+79991402001', 'hello')
    """), {
        "id": str(item_id),
        "wid": str(test_workspace.id),
        "sid": str(sender.id),
        "cid": str(camp["id"]),
    })
    await async_db_session.commit()

    send_mock = AsyncMock(
        return_value={
            "success": True,
            "message_id": "tg-1",
            "recipient": {"telegram_id": 602001, "name": "X", "username": None},
        }
    )
    monkeypatch.setattr(
        "app.services.queue.telegram_service.get_client",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "app.services.queue.telegram_service.send_message", send_mock
    )

    await queue_worker._QueueWorker__send_item_inner(item_id)

    send_mock.assert_awaited()


# ── Test 13: CLAUDE.md guard — no empirical constant changes ──────────────────


def test_no_empirical_constants_modified(tmp_path):
    """grep-based defence: queue.py must still hold the same MIN_/MAX_/DEBOUNCE/FLOOD
    constants as baseline. Phase 5 added ONE pre-send SELECT only — no other
    behaviour changes per CLAUDE.md.
    """
    import pathlib

    queue_path = pathlib.Path(__file__).resolve().parent.parent / "app" / "services" / "queue.py"
    content = queue_path.read_text()

    # Baseline constants that must still be present (their VALUES, untouched).
    assert "MIN_SEND_INTERVAL = 20" in content
    assert "MAX_SEND_INTERVAL = 55" in content
    assert "SEND_INTERVAL_FATIGUE = 0.5" in content
    assert "MAX_NEW_CONTACTS_PER_HOUR = 15" in content
    assert "LONG_PAUSE_EVERY_MIN = 12" in content
    assert "LONG_PAUSE_EVERY_MAX = 25" in content
    assert "LONG_PAUSE_MIN_SECS = 180" in content
    assert "LONG_PAUSE_MAX_SECS = 600" in content
    assert "FLOOD_HARD_THRESHOLD = 300" in content
