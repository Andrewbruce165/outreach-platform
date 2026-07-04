"""Batch E (quick 260704-buq) — queue dispatcher lifecycle fixes.

Covers:
  * WR-12a — a cold terminal fail (no prior 'sent' for campaign+phone) releases the
    sticky campaign_contact_assignments row; a warm fail (prior 'sent') keeps it.
  * IN-07  — the past_stop_date fail path carries an `AND status='pending'` guard
    (never clobbers a concurrently-cancelled row) and fires a per-item callback.
  * IN-12  — dispatcher-sent outbound messages are logged with sent_by='ai'.
"""

import asyncio

import pytest
from sqlalchemy import text

from app.models import MessageQueue, QueueItemStatus, QueueItemType
from app.services.queue import QueueWorker

pytestmark = pytest.mark.asyncio


async def _insert_queue_row(
    db, wid, sid, phone, status="pending", callback_url=None,
    campaign_id=None, message_text="x",
):
    row = (await db.execute(text("""
        INSERT INTO message_queue (workspace_id, campaign_id, sender_id, item_type,
            status, recipient_phone, message_text, callback_url, scheduled_at)
        VALUES (:wid, :cid, :sid, 'message', :status, :phone, :txt, :cb, NOW())
        RETURNING id
    """), {
        "wid": str(wid),
        "cid": str(campaign_id) if campaign_id else None,
        "sid": str(sid),
        "status": status,
        "phone": phone,
        "txt": message_text,
        "cb": callback_url,
    })).first()
    await db.commit()
    return row.id


# ─── WR-12a: cold terminal fail releases the sticky CCA ──────────────────────


async def test_fail_item_cold_terminal_releases_cca(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory,
):
    """WR-12a: terminal fail with NO prior 'sent' for (campaign, phone) → the
    campaign_contact_assignments row is deleted so the contact is eligible again."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory(status="running")
    phone = "+79995550001"

    await async_db_session.execute(text("""
        INSERT INTO campaign_contact_assignments
            (workspace_id, campaign_id, contact_phone, sender_id)
        VALUES (:wid, :cid, :phone, :sid)
    """), {"wid": str(test_workspace.id), "cid": str(camp["id"]),
           "phone": phone, "sid": str(sender.id)})
    await async_db_session.commit()

    item = MessageQueue(
        workspace_id=test_workspace.id,
        campaign_id=camp["id"],
        sender_id=sender.id,
        item_type=QueueItemType.message,
        recipient_phone=phone,
        recipient_name="Cold",
        message_text="x",
        status=QueueItemStatus.processing,
        attempts=2,  # next failure → 3 = MAX_ATTEMPTS → terminal
    )
    async_db_session.add(item)
    await async_db_session.commit()
    await async_db_session.refresh(item)

    worker = QueueWorker()
    await worker._fail_item(async_db_session, item, "terminal boom")

    status = (await async_db_session.execute(text(
        "SELECT status FROM message_queue WHERE id = :id"
    ), {"id": str(item.id)})).scalar()
    assert status == "failed"

    cca_cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM campaign_contact_assignments
        WHERE campaign_id = :cid AND contact_phone = :phone
    """), {"cid": str(camp["id"]), "phone": phone})).scalar()
    assert cca_cnt == 0  # released — the enqueue-worker dedup lets it re-enter


async def test_fail_item_warm_terminal_keeps_cca(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory,
):
    """WR-12a control: a prior 'sent' row for (campaign, phone) means the contact is
    engaged — a later terminal fail must NOT delete its CCA."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory(status="running")
    phone = "+79995550002"

    await async_db_session.execute(text("""
        INSERT INTO campaign_contact_assignments
            (workspace_id, campaign_id, contact_phone, sender_id)
        VALUES (:wid, :cid, :phone, :sid)
    """), {"wid": str(test_workspace.id), "cid": str(camp["id"]),
           "phone": phone, "sid": str(sender.id)})
    # A prior successful send for the same (campaign, phone).
    await _insert_queue_row(
        async_db_session, test_workspace.id, sender.id, phone,
        status="sent", campaign_id=camp["id"], message_text="earlier",
    )

    item = MessageQueue(
        workspace_id=test_workspace.id,
        campaign_id=camp["id"],
        sender_id=sender.id,
        item_type=QueueItemType.message,
        recipient_phone=phone,
        recipient_name="Warm",
        message_text="follow-up that fails",
        status=QueueItemStatus.processing,
        attempts=2,
    )
    async_db_session.add(item)
    await async_db_session.commit()
    await async_db_session.refresh(item)

    worker = QueueWorker()
    await worker._fail_item(async_db_session, item, "terminal boom")

    cca_cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM campaign_contact_assignments
        WHERE campaign_id = :cid AND contact_phone = :phone
    """), {"cid": str(camp["id"]), "phone": phone})).scalar()
    assert cca_cnt == 1  # engaged contact retained


async def test_fail_item_null_campaign_does_not_touch_cca(
    async_db_session, test_workspace, test_sender_factory,
):
    """WR-12a guard: item with campaign_id NULL never runs the CCA release path."""
    sender = await test_sender_factory()
    item = MessageQueue(
        workspace_id=test_workspace.id,
        sender_id=sender.id,
        item_type=QueueItemType.message,
        recipient_phone="+79995550003",
        recipient_name="NoCampaign",
        message_text="x",
        status=QueueItemStatus.processing,
        attempts=2,
    )
    async_db_session.add(item)
    await async_db_session.commit()
    await async_db_session.refresh(item)

    worker = QueueWorker()
    # Must not raise (campaign_id is None → guard short-circuits).
    await worker._fail_item(async_db_session, item, "boom")

    status = (await async_db_session.execute(text(
        "SELECT status FROM message_queue WHERE id = :id"
    ), {"id": str(item.id)})).scalar()
    assert status == "failed"


# ─── IN-07: past_stop_date fail guard + callback ─────────────────────────────


async def test_past_stop_date_fail_skips_cancelled_row(
    async_db_session, test_workspace, test_sender_factory,
):
    """IN-07: the status='pending' guard means a concurrently-cancelled row is NOT
    flipped to 'failed' by the stop_date fail path; a pending row still fails."""
    sender = await test_sender_factory()
    cancelled_id = await _insert_queue_row(
        async_db_session, test_workspace.id, sender.id, "+79996660001",
        status="cancelled",
    )
    pending_id = await _insert_queue_row(
        async_db_session, test_workspace.id, sender.id, "+79996660002",
        status="pending",
    )

    worker = QueueWorker()
    await worker._fail_past_stop_date_items(async_db_session, [cancelled_id, pending_id])
    await async_db_session.commit()

    cancelled_status = (await async_db_session.execute(text(
        "SELECT status FROM message_queue WHERE id = :id"
    ), {"id": str(cancelled_id)})).scalar()
    pending_status = (await async_db_session.execute(text(
        "SELECT status FROM message_queue WHERE id = :id"
    ), {"id": str(pending_id)})).scalar()

    assert cancelled_status == "cancelled"  # untouched — not clobbered
    assert pending_status == "failed"


async def test_past_stop_date_fail_fires_callback(
    async_db_session, test_workspace, test_sender_factory, monkeypatch,
):
    """IN-07: a past_stop_date fail fires a status='failed' callback for a row with
    a non-null callback_url, resolving the sender_slug."""
    sender = await test_sender_factory()
    item_id = await _insert_queue_row(
        async_db_session, test_workspace.id, sender.id, "+79996660003",
        status="pending", callback_url="http://callback.test/hook",
    )

    worker = QueueWorker()
    calls: list = []

    async def _fake_fire(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(worker, "_fire_callback", _fake_fire)

    await worker._fail_past_stop_date_items(async_db_session, [item_id])
    await async_db_session.commit()
    await asyncio.sleep(0)  # let the fire-and-forget create_task run

    assert len(calls) == 1
    assert calls[0]["status"] == "failed"
    assert calls[0]["error"] == "past_stop_date"
    assert calls[0]["sender_slug"] == sender.slug
    assert calls[0]["recipient_phone"] == "+79996660003"


async def test_past_stop_date_fail_no_callback_when_url_null(
    async_db_session, test_workspace, test_sender_factory, monkeypatch,
):
    """IN-07: no callback is scheduled when the failed row has no callback_url."""
    sender = await test_sender_factory()
    item_id = await _insert_queue_row(
        async_db_session, test_workspace.id, sender.id, "+79996660004",
        status="pending", callback_url=None,
    )

    worker = QueueWorker()
    calls: list = []

    async def _fake_fire(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(worker, "_fire_callback", _fake_fire)

    await worker._fail_past_stop_date_items(async_db_session, [item_id])
    await async_db_session.commit()
    await asyncio.sleep(0)

    assert calls == []


# ─── IN-12: dispatcher message logged as sent_by='ai' ────────────────────────


async def test_upsert_conversation_dispatcher_message_sent_by_ai(
    async_db_session, test_workspace, test_sender_factory,
):
    """IN-12: an outbound message written by the queue dispatcher is sent_by='ai'
    (NOT 'human' — human is reserved for manager takeover via the inbox router)."""
    sender = await test_sender_factory()
    item = MessageQueue(
        workspace_id=test_workspace.id,
        sender_id=sender.id,
        item_type=QueueItemType.message,
        recipient_phone="+79997770001",
        recipient_name="AI Target",
        message_text="hello from the dispatcher",
        status=QueueItemStatus.sent,
        as_draft=False,
    )
    async_db_session.add(item)
    await async_db_session.commit()
    await async_db_session.refresh(item)

    fake_tg_id = 2233445566
    result = {
        "success": True,
        "message_id": 777001,  # non-null → the messages INSERT branch runs
        "recipient": {"telegram_id": fake_tg_id, "name": "AI Target"},
    }

    worker = QueueWorker()
    await worker._upsert_conversation(async_db_session, sender, item, result)
    await async_db_session.commit()

    msg_row = (await async_db_session.execute(text("""
        SELECT m.direction, m.sent_by
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.sender_id = :sid AND c.contact_telegram_id = :tg_id
        ORDER BY m.created_at DESC LIMIT 1
    """), {"sid": str(sender.id), "tg_id": fake_tg_id})).first()

    assert msg_row is not None, "dispatcher message row was not created"
    assert msg_row.direction == "outbound"
    assert msg_row.sent_by == "ai"
