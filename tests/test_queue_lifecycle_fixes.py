"""Batch E (quick 260704-buq) — queue dispatcher lifecycle fixes.

Covers:
  * WR-12a — a cold terminal fail (no prior 'sent' for campaign+phone) releases the
    sticky campaign_contact_assignments row; a warm fail (prior 'sent') keeps it.
  * D-11 v2 (deadline-mass-fail fix, superseded IN-07) — past-stop_date campaigns
    are auto-PAUSED (pause_reason='past_stop_date'), not failed. The pending queue
    tail is left untouched and the pause is idempotent (guarded by status='running').
  * IN-12  — dispatcher-sent outbound messages are logged with sent_by='ai'.
"""

import pytest
from sqlalchemy import text

from app.models import MessageQueue, QueueItemStatus, QueueItemType
from app.services.queue import COLD_FAIL_RELEASE_CAP, QueueWorker

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


# ─── WR-15: the cold-fail CCA release is bounded (infinite re-enqueue loop) ──


async def _cold_fail_once(db, workspace, sender, camp, phone, prior_failed: int):
    """Seed `prior_failed` historical terminal fails for (camp, phone) + a live CCA,
    then drive ONE more terminal fail through _fail_item. Returns surviving CCA count."""
    await db.execute(text("""
        INSERT INTO campaign_contact_assignments
            (workspace_id, campaign_id, contact_phone, sender_id)
        VALUES (:wid, :cid, :phone, :sid)
        ON CONFLICT (campaign_id, contact_phone) DO NOTHING
    """), {"wid": str(workspace.id), "cid": str(camp["id"]),
           "phone": phone, "sid": str(sender.id)})
    for _ in range(prior_failed):
        await _insert_queue_row(
            db, workspace.id, sender.id, phone,
            status="failed", campaign_id=camp["id"], message_text="earlier cycle",
        )

    item = MessageQueue(
        workspace_id=workspace.id,
        campaign_id=camp["id"],
        sender_id=sender.id,
        item_type=QueueItemType.message,
        recipient_phone=phone,
        recipient_name="Cold",
        message_text="x",
        status=QueueItemStatus.processing,
        attempts=2,  # next failure → 3 = MAX_ATTEMPTS → terminal
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)

    worker = QueueWorker()
    await worker._fail_item(db, item, "terminal boom")

    return (await db.execute(text("""
        SELECT COUNT(*) FROM campaign_contact_assignments
        WHERE campaign_id = :cid AND contact_phone = :phone
    """), {"cid": str(camp["id"]), "phone": phone})).scalar()


async def test_cold_fail_still_releases_cca_below_cap(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory,
):
    """WR-15 control: one fail short of the cap the release still fires — the bound
    must not break the retry WR-12a added. Derived from COLD_FAIL_RELEASE_CAP so
    retuning the cap moves the probe instead of silently un-testing the boundary."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory(status="running")

    cca_cnt = await _cold_fail_once(
        async_db_session, test_workspace, sender, camp, "+79995550010",
        prior_failed=COLD_FAIL_RELEASE_CAP - 2,  # + this fail → CAP-1 < CAP
    )
    assert cca_cnt == 0  # released — contact re-enters the enqueue selector


async def test_cold_fail_retains_cca_at_cap(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory,
):
    """WR-15: this fail makes the running total exactly COLD_FAIL_RELEASE_CAP, so the
    CCA is RETAINED — the contact stops being re-enqueued and the
    enqueue → fail → DELETE CCA → enqueue loop terminates."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory(status="running")

    cca_cnt = await _cold_fail_once(
        async_db_session, test_workspace, sender, camp, "+79995550011",
        prior_failed=COLD_FAIL_RELEASE_CAP - 1,  # + this fail → exactly CAP
    )
    assert cca_cnt == 1  # retained — enqueue worker's NOT IN dedup now excludes it


async def test_cold_fail_cap_is_scoped_per_campaign(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory,
):
    """WR-15 scope guard: prior fails of the SAME phone in a DIFFERENT campaign must
    not count towards this campaign's cap (each campaign gets its own budget)."""
    sender = await test_sender_factory()
    other = await test_campaign_factory(status="running")
    camp = await test_campaign_factory(status="running")
    phone = "+79995550012"

    # A full cap's worth of terminal fails for the same phone — but on another campaign.
    for _ in range(COLD_FAIL_RELEASE_CAP):
        await _insert_queue_row(
            async_db_session, test_workspace.id, sender.id, phone,
            status="failed", campaign_id=other["id"], message_text="other campaign",
        )

    cca_cnt = await _cold_fail_once(
        async_db_session, test_workspace, sender, camp, phone, prior_failed=0,
    )
    assert cca_cnt == 0  # first fail in THIS campaign → still released


# ─── D-11 v2: past-stop_date campaigns auto-PAUSE (deadline-mass-fail fix) ───


async def test_pause_expired_campaigns_pauses_running_campaign(
    async_db_session, test_workspace, test_campaign_factory,
):
    """D-11 v2: a running campaign flips to paused/pause_reason='past_stop_date'
    when its id is passed to _pause_expired_campaigns — the trigger queue.py
    itself no longer decides here (it just detects and hands off campaign ids)."""
    camp = await test_campaign_factory(status="running")

    worker = QueueWorker()
    await worker._pause_expired_campaigns(async_db_session, [camp["id"]])
    await async_db_session.commit()

    row = (await async_db_session.execute(text(
        "SELECT status, pause_reason, paused_at FROM campaigns WHERE id = :id"
    ), {"id": str(camp["id"])})).first()
    assert row.status == "paused"
    assert row.pause_reason == "past_stop_date"
    assert row.paused_at is not None


async def test_pause_expired_campaigns_preserves_pending_queue(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory,
):
    """D-11 v2 core fix: unlike the superseded IN-07 fail path, the pending queue
    tail is left completely untouched — extend stop_date + resume continues
    sending it, no reanimation of failed rows required."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory(status="running")
    item_id = await _insert_queue_row(
        async_db_session, test_workspace.id, sender.id, "+79996660005",
        status="pending", campaign_id=camp["id"],
    )

    worker = QueueWorker()
    await worker._pause_expired_campaigns(async_db_session, [camp["id"]])
    await async_db_session.commit()

    status = (await async_db_session.execute(text(
        "SELECT status FROM message_queue WHERE id = :id"
    ), {"id": str(item_id)})).scalar()
    assert status == "pending", "pending queue tail must survive the deadline pause"


async def test_pause_expired_campaigns_idempotent_on_already_paused(
    async_db_session, test_workspace, test_campaign_factory,
):
    """D-11 v2: the UPDATE is guarded by status='running', so a campaign already
    paused (by this path, manually, or by the 029 no-sender auto-pause) is left
    untouched — no repeated churn, no clobbering an unrelated pause_reason."""
    camp = await test_campaign_factory(status="paused")
    await async_db_session.execute(text(
        "UPDATE campaigns SET pause_reason = 'no_senders_attached' WHERE id = :id"
    ), {"id": str(camp["id"])})
    await async_db_session.commit()

    worker = QueueWorker()
    await worker._pause_expired_campaigns(async_db_session, [camp["id"]])
    await async_db_session.commit()

    row = (await async_db_session.execute(text(
        "SELECT status, pause_reason FROM campaigns WHERE id = :id"
    ), {"id": str(camp["id"])})).first()
    assert row.status == "paused"
    assert row.pause_reason == "no_senders_attached", (
        "an already-paused campaign's pause_reason must NOT be clobbered"
    )


async def test_pause_expired_campaigns_empty_list_is_noop(async_db_session):
    """D-11 v2: an empty campaign_ids list must not raise or touch the DB."""
    worker = QueueWorker()
    await worker._pause_expired_campaigns(async_db_session, [])  # must not raise


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
