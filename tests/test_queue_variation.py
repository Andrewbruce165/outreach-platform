"""Plan 24-06 Task 1 — send-time invisible variation gate (D-12/D-14/D-16).

Integration tests for the queue worker send branch:

- VAR-FLAG: variation_enabled read at SEND time (SELECT on campaigns). Flag on →
  the text passed to telegram_service.send_message is byte-different but
  strip_invisible-identical to item.message_text (varied local copy). Flag off →
  the send gets exactly the clean item.message_text.
- VAR-SCOPE: only campaign + non-followup sends are varied. A follow-up ping
  (extra_data.kind='followup') and a non-campaign item (campaign_id NULL) are
  NEVER varied even with the flag on.
- D-14 clean-DB invariant: message_queue.message_text and the inbox `messages`
  row stay the untouched clean original (no invisible glyphs) — variation lives
  ONLY on the local copy handed to Telethon.
- D-16 freshness: two consecutive sends of the same opener produce different
  sent bytes (vary() called per send).

telegram_service.get_client + send_message are mocked (AsyncMock) so nothing
touches Telegram; the real Postgres test DB is used for the queue/messages rows.
"""

import json as _json
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.services import queue as queue_module
from app.services.queue import QueueWorker
from app.services.variation import strip_invisible

pytestmark = pytest.mark.asyncio


_CLEAN_OPENER = "Привет как дела друг мой хороший расскажи про подсолнечник сегодня"


def _ok_result(message_id: str = "1001") -> dict:
    return {
        "success": True,
        "message_id": message_id,
        "recipient": {"telegram_id": 555, "name": "Тест", "username": None},
    }


async def _insert_message_item(
    db,
    *,
    workspace_id,
    sender_id,
    campaign_id,
    recipient_phone: str = "+79995551122",
    message_text: str = _CLEAN_OPENER,
    extra_data: dict | None = None,
) -> str:
    """Insert a message-type queue item (raw SQL — mirrors the suite idiom)."""
    qid = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO message_queue (
            id, workspace_id, sender_id, campaign_id,
            item_type, status, recipient_phone, message_text,
            scheduled_at, extra_data
        ) VALUES (
            :qid, :wid, :sid, :cid,
            'message', 'processing', :rp, :txt, :sa, CAST(:extra AS jsonb)
        )
    """), {
        "qid": qid, "wid": str(workspace_id), "sid": str(sender_id),
        "cid": str(campaign_id) if campaign_id else None,
        "rp": recipient_phone, "txt": message_text,
        "sa": datetime.now(timezone.utc) - timedelta(minutes=1),
        "extra": _json.dumps(extra_data or {}),
    })
    await db.commit()
    return qid


async def _set_variation(db, *, campaign_id, enabled: bool):
    await db.execute(
        text("UPDATE campaigns SET variation_enabled = :v WHERE id = :cid"),
        {"v": enabled, "cid": str(campaign_id)},
    )
    await db.commit()


def _patch_send(capture: dict):
    """Patch get_client + send_message; capture the message kwarg per call."""
    async def _fake_send_message(**kwargs):
        capture.setdefault("messages", []).append(kwargs.get("message"))
        return _ok_result(str(1000 + len(capture["messages"])))

    return (
        patch.object(queue_module.telegram_service, "get_client",
                     new=AsyncMock(return_value=MagicMock())),
        patch.object(queue_module.telegram_service, "send_message",
                     new=AsyncMock(side_effect=_fake_send_message)),
    )


async def _queue_text(db, qid: str) -> str:
    return (await db.execute(
        text("SELECT message_text FROM message_queue WHERE id = :q"), {"q": qid}
    )).scalar()


# ── Tests ────────────────────────────────────────────────────────────────────


async def test_variation_applied_on_local_copy_flag_on(
    async_db_session, test_running_campaign_factory
):
    """VAR-FLAG + D-14: flag on → sent text is varied (strip==clean, bytes differ)
    while message_queue.message_text + the inbox messages row stay clean."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    await _set_variation(async_db_session, campaign_id=camp["id"], enabled=True)
    qid = await _insert_message_item(
        async_db_session, workspace_id=camp["workspace_id"],
        sender_id=senders[0].id, campaign_id=camp["id"],
    )

    cap: dict = {}
    cm_client, cm_send = _patch_send(cap)
    with cm_client, cm_send:
        await QueueWorker()._send_item(qid)

    sent = cap["messages"][0]
    assert sent != _CLEAN_OPENER, "flag on → sent bytes must differ (varied)"
    assert strip_invisible(sent) == _CLEAN_OPENER, "recipient must read the clean text"

    # DB stays clean (D-14): queue row + inbox messages row are the untouched original.
    assert await _queue_text(async_db_session, qid) == _CLEAN_OPENER
    msg_txt = (await async_db_session.execute(text("""
        SELECT m.message_text FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.contact_phone = '+79995551122' AND m.direction = 'outbound'
        ORDER BY m.created_at DESC LIMIT 1
    """))).scalar()
    assert msg_txt == _CLEAN_OPENER, "inbox messages row must be the clean text"


async def test_variation_off_sends_clean(
    async_db_session, test_running_campaign_factory
):
    """VAR-FLAG: variation_enabled=false → send gets EXACTLY the clean text."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    await _set_variation(async_db_session, campaign_id=camp["id"], enabled=False)
    qid = await _insert_message_item(
        async_db_session, workspace_id=camp["workspace_id"],
        sender_id=senders[0].id, campaign_id=camp["id"],
    )

    cap: dict = {}
    cm_client, cm_send = _patch_send(cap)
    with cm_client, cm_send:
        await QueueWorker()._send_item(qid)

    assert cap["messages"][0] == _CLEAN_OPENER, "flag off → send must be clean"


async def test_followup_never_varied(
    async_db_session, test_running_campaign_factory
):
    """VAR-SCOPE: extra_data.kind='followup' → never varied even with flag on."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    await _set_variation(async_db_session, campaign_id=camp["id"], enabled=True)
    qid = await _insert_message_item(
        async_db_session, workspace_id=camp["workspace_id"],
        sender_id=senders[0].id, campaign_id=camp["id"],
        extra_data={"kind": "followup"},
    )

    cap: dict = {}
    cm_client, cm_send = _patch_send(cap)
    with cm_client, cm_send:
        await QueueWorker()._send_item(qid)

    assert cap["messages"][0] == _CLEAN_OPENER, "follow-up ping must never be varied"


async def test_non_campaign_never_varied(
    async_db_session, test_sender_factory
):
    """VAR-SCOPE: campaign_id NULL → never varied (no campaign, no flag)."""
    sender = await test_sender_factory()
    qid = await _insert_message_item(
        async_db_session, workspace_id=sender.workspace_id,
        sender_id=sender.id, campaign_id=None,
    )

    cap: dict = {}
    cm_client, cm_send = _patch_send(cap)
    with cm_client, cm_send:
        await QueueWorker()._send_item(qid)

    assert cap["messages"][0] == _CLEAN_OPENER, "non-campaign send must never be varied"


async def test_two_sends_produce_different_bytes(
    async_db_session, test_running_campaign_factory
):
    """D-16: two sends of the same opener (flag on) differ in bytes (fresh per send)."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    await _set_variation(async_db_session, campaign_id=camp["id"], enabled=True)

    cap: dict = {}
    cm_client, cm_send = _patch_send(cap)
    with cm_client, cm_send:
        for i in range(2):
            qid = await _insert_message_item(
                async_db_session, workspace_id=camp["workspace_id"],
                sender_id=senders[0].id, campaign_id=camp["id"],
                recipient_phone=f"+7999555{i:04d}",
            )
            await QueueWorker()._send_item(qid)

    a, b = cap["messages"][0], cap["messages"][1]
    assert a != b, "two sends must differ in bytes (vary() called fresh per send)"
    assert strip_invisible(a) == _CLEAN_OPENER and strip_invisible(b) == _CLEAN_OPENER
