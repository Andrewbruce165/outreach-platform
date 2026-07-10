"""Plan 04-04 Task 4: CampaignEnqueueWorker integration tests.

Covers CAMP-09 (досыпание контактов) + CAMP-10 (enqueue из folder).

Tests instantiate a fresh ``CampaignEnqueueWorker`` instance per test
(NOT the module-level singleton) to avoid lifespan interference.
"""

import asyncio

import pytest
from sqlalchemy import text

from app.services.campaign_enqueue import CampaignEnqueueWorker, campaign_enqueue_worker

pytestmark = pytest.mark.asyncio


async def _make_worker():
    """Local helper — fresh worker (no module singleton)."""
    w = CampaignEnqueueWorker()
    return w


# ─── Sweep helpers (mirror tests/test_failover.py:42-93 freeze/pause pattern) ──

async def _pending_counts(db, campaign_id) -> dict[str, int]:
    rows = (await db.execute(text("""
        SELECT sender_id, COUNT(*) FROM message_queue
        WHERE campaign_id = :cid AND status = 'pending'
        GROUP BY sender_id
    """), {"cid": str(campaign_id)})).all()
    return {str(r[0]): int(r[1]) for r in rows}


async def _cca_sender_for(db, campaign_id, contact_phone):
    row = (await db.execute(text("""
        SELECT sender_id FROM campaign_contact_assignments
        WHERE campaign_id = :cid AND contact_phone = :phone
    """), {"cid": str(campaign_id), "phone": contact_phone})).first()
    return str(row[0]) if row else None


async def _scheduled_at(db, campaign_id, phone, status="pending"):
    row = (await db.execute(text("""
        SELECT scheduled_at FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :phone AND status = :st
    """), {"cid": str(campaign_id), "phone": phone, "st": status})).first()
    return row[0] if row else None


async def _freeze_sender(db, sender_id, status: str = "spam_limited"):
    """Flag a sender restricted exactly as the freeze paths do (queue.py / listener.py)."""
    await db.execute(text("""
        UPDATE senders
        SET restriction_status = :st,
            restricted_until = NOW() + INTERVAL '24 hours'
        WHERE id = :sid
    """), {"st": status, "sid": str(sender_id)})
    await db.commit()


async def _pause_pending(db, sender_id):
    """Push the sender's pending queue +24h — what every freeze path does before failover."""
    await db.execute(text("""
        UPDATE message_queue
        SET scheduled_at = NOW() + INTERVAL '24 hours'
        WHERE sender_id = :sid AND status = 'pending'
    """), {"sid": str(sender_id)})
    await db.commit()


async def test_worker_tick_inserts_queue_item_for_new_contact(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
):
    """CAMP-09: contact в folder с tg_status='registered' → tick → INSERT в message_queue + cca."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    # Move contact into campaign folder.
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp["folder_id"]), "cid": str(contact.id)})
    await async_db_session.commit()

    worker = await _make_worker()
    enqueued = await worker._tick()
    assert enqueued >= 1

    # Verify message_queue row exists with campaign_id.
    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :p
    """), {"cid": str(camp["id"]), "p": contact.phone})).scalar()
    assert cnt == 1

    # Verify cca row exists.
    cca_cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM campaign_contact_assignments
        WHERE campaign_id = :cid AND contact_phone = :p
    """), {"cid": str(camp["id"]), "p": contact.phone})).scalar()
    assert cca_cnt == 1


async def test_worker_enqueues_username_only_contact(
    async_db_session,
    test_running_campaign_factory,
):
    """Migration 025: a registered contact with only a username (no phone) is
    enqueued under the '@username' identity key — in message_queue and cca."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    # Username-only contact in the campaign folder (factory forces a phone, so
    # insert raw). workspace_id must match the campaign's folder workspace.
    wid = (await async_db_session.execute(text("""
        SELECT workspace_id FROM folders WHERE id = :fid
    """), {"fid": str(camp["folder_id"])})).scalar()
    await async_db_session.execute(text("""
        INSERT INTO contacts (workspace_id, folder_id, phone, username, full_name, tg_status)
        VALUES (:wid, :fid, NULL, 'romanvdr', 'Roman', 'registered')
    """), {"wid": str(wid), "fid": str(camp["folder_id"])})
    await async_db_session.commit()

    worker = await _make_worker()
    enqueued = await worker._tick()
    assert enqueued >= 1

    q_cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = '@romanvdr'
    """), {"cid": str(camp["id"])})).scalar()
    assert q_cnt == 1

    cca_cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM campaign_contact_assignments
        WHERE campaign_id = :cid AND contact_phone = '@romanvdr'
    """), {"cid": str(camp["id"])})).scalar()
    assert cca_cnt == 1

    # Idempotent: a second tick must not duplicate (dedup by identity key).
    await worker._tick()
    q_cnt2 = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = '@romanvdr'
    """), {"cid": str(camp["id"])})).scalar()
    assert q_cnt2 == 1


async def _add_attachment(db, campaign, *, file_name="doc.pdf"):
    """Seed a campaign_attachments row (D-02 blob table) for the campaign."""
    import uuid as _uuid
    await db.execute(text("""
        INSERT INTO campaign_attachments
            (id, campaign_id, workspace_id, file_data, file_name, content_type, size_bytes)
        VALUES (:id, :cid, :wid, :data, :name, 'application/pdf', :sz)
    """), {
        "id": str(_uuid.uuid4()),
        "cid": str(campaign["id"]),
        "wid": str(campaign["workspace_id"]),
        "data": b"%PDF-1.4 fake",
        "name": file_name,
        "sz": 12,
    })
    await db.commit()


async def test_worker_enqueues_file_item_when_attachment_present(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
):
    """D-05/D-18: a campaign WITH a campaign_attachments row enqueues ONE
    item_type='file' row per contact, caption == message_text == rendered opener,
    status='pending'. One row per contact = one send / one new-dialog (limits
    unchanged)."""
    camp, _ = await test_running_campaign_factory(
        sender_count=1, message_template="Привет, {{name}}!"
    )
    await _add_attachment(async_db_session, camp)
    contacts = await test_contacts_factory(count=2, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = ANY(:ids)
    """), {"fid": str(camp["folder_id"]), "ids": [str(c.id) for c in contacts]})
    await async_db_session.commit()

    worker = await _make_worker()
    enqueued = await worker._tick()
    assert enqueued == 2

    rows = (await async_db_session.execute(text("""
        SELECT item_type, caption, message_text, status FROM message_queue
        WHERE campaign_id = :cid
    """), {"cid": str(camp["id"])})).fetchall()
    assert len(rows) == 2
    for r in rows:
        # item_type stored as the enum value 'file'
        assert str(r[0]).endswith("file"), f"expected file item_type, got {r[0]}"
        assert r[1] is not None and r[1] == r[2], "caption must mirror message_text"
        assert r[3] == "pending"
        assert r[1].startswith("Привет,"), "caption is the rendered opener"


async def test_worker_enqueues_message_item_when_no_attachment(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
):
    """A campaign WITHOUT an attachment enqueues item_type='message' rows with
    caption NULL — no behavior change."""
    camp, _ = await test_running_campaign_factory(
        sender_count=1, message_template="Привет!"
    )
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp["folder_id"]), "cid": str(contact.id)})
    await async_db_session.commit()

    worker = await _make_worker()
    await worker._tick()

    row = (await async_db_session.execute(text("""
        SELECT item_type, caption FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :p
    """), {"cid": str(camp["id"]), "p": contact.phone})).first()
    assert row is not None
    assert str(row[0]).endswith("message"), f"expected message item_type, got {row[0]}"
    assert row[1] is None, "message rows keep caption NULL"


async def test_worker_renders_template_at_enqueue(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
):
    """D-18: message_queue.message_text — уже с подставленными переменными."""
    camp, _ = await test_running_campaign_factory(
        sender_count=1,
        message_template="Привет, {{name}}!",
    )
    contact = await test_contacts_factory(count=1, tg_status="registered", full_name="Иван")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp["folder_id"]), "cid": str(contact.id)})
    await async_db_session.commit()

    worker = await _make_worker()
    await worker._tick()

    msg_text = (await async_db_session.execute(text("""
        SELECT message_text FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :p
    """), {"cid": str(camp["id"]), "p": contact.phone})).scalar()
    assert msg_text == "Привет, Иван!"


async def test_worker_skips_already_assigned_contact(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
):
    """Контакт уже в cca → второй tick не дублирует item."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp["folder_id"]), "cid": str(contact.id)})
    await async_db_session.commit()

    worker = await _make_worker()
    await worker._tick()
    await worker._tick()  # second tick

    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM message_queue WHERE campaign_id = :cid
    """), {"cid": str(camp["id"])})).scalar()
    assert cnt == 1


async def test_worker_skips_contact_with_existing_conversation(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
    test_conversation_factory,
):
    """Identity-scoped dedup: contact already has an in-scope conversation (a
    sender from this campaign's pool) → worker does NOT enqueue a first-touch
    (regression for the duplicate-intro incident, where a copied campaign
    re-introduced itself to an active lead via the same agent)."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp["folder_id"]), "cid": str(contact.id)})
    # Existing conversation for this phone, handled by a sender in the pool.
    await test_conversation_factory(
        contact_phone=contact.phone, status="lead", sender=senders[0]
    )
    await async_db_session.commit()

    worker = await _make_worker()
    enqueued = await worker._tick()
    assert enqueued == 0

    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :p
    """), {"cid": str(camp["id"]), "p": contact.phone})).scalar()
    assert cnt == 0


async def test_worker_skips_unregistered_contact(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
):
    """tg_status='not_registered' / 'pending' / 'unchecked' → не добавляется в очередь."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    # contacts in same folder, but tg_status != 'registered'
    contacts = await test_contacts_factory(count=3, tg_status="pending")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = ANY(:ids)
    """), {"fid": str(camp["folder_id"]), "ids": [str(c.id) for c in contacts]})
    await async_db_session.commit()

    worker = await _make_worker()
    enqueued = await worker._tick()
    assert enqueued == 0


async def test_worker_skips_non_running_campaigns(
    async_db_session,
    test_campaign_factory,
    test_sender_factory,
    attach_sender_to_campaign,
    test_contacts_factory,
):
    """draft / paused / done — worker не enqueue'ит."""
    s = await test_sender_factory()
    camp = await test_campaign_factory(status="draft")
    await attach_sender_to_campaign(camp["id"], s.id)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp["folder_id"]), "cid": str(contact.id)})
    await async_db_session.commit()

    worker = await _make_worker()
    enqueued = await worker._tick()
    assert enqueued == 0


async def test_worker_workspace_isolation(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
):
    """Pitfall 8: контакт в другом workspace НЕ enqueues."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    # Create contact in DIFFERENT workspace_id (via raw INSERT bypassing factory).
    from uuid import uuid4
    other_wid = uuid4()
    await async_db_session.execute(text("""
        INSERT INTO workspaces (id, name) VALUES (:id, 'OtherWs')
    """), {"id": str(other_wid)})
    # Folder in OTHER workspace.
    other_fid = uuid4()
    await async_db_session.execute(text("""
        INSERT INTO folders (id, workspace_id, name) VALUES (:id, :wid, 'OtherFolder')
    """), {"id": str(other_fid), "wid": str(other_wid)})
    # Same folder_id as our campaign but different workspace_id wouldn't happen in
    # production — workspace_id derived from folder. Test the SELECT guard by
    # ensuring contacts in folder X but workspace Y are NOT picked when campaign
    # targets folder X workspace W.
    # Simpler test: contact in campaign's folder but with wrong workspace_id.
    cid_phone = "+79992223344"
    await async_db_session.execute(text("""
        INSERT INTO contacts (workspace_id, folder_id, phone, full_name, tg_status)
        VALUES (:wid, :fid, :p, 'Other', 'registered')
    """), {"wid": str(other_wid), "fid": str(camp["folder_id"]), "p": cid_phone})
    await async_db_session.commit()

    worker = await _make_worker()
    await worker._tick()

    # Contact with wrong workspace_id should not be enqueued.
    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :p
    """), {"cid": str(camp["id"]), "p": cid_phone})).scalar()
    assert cnt == 0


async def test_worker_atomic_transaction_failure_rollback(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
    monkeypatch,
):
    """Q5: если INSERT в queue упал — INSERT в cca rollback'ится."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp["folder_id"]), "cid": str(contact.id)})
    await async_db_session.commit()

    # Patch render_template to raise — should rollback the savepoint.
    def boom(*a, **kw):
        raise RuntimeError("render explosion")

    import app.services.campaign_enqueue as ce
    monkeypatch.setattr(ce, "render_template", boom)

    worker = await _make_worker()
    enqueued = await worker._tick()
    # Worker swallows exception per-contact, continues.
    assert enqueued == 0

    # Neither cca nor queue should have rows for this contact (savepoint rolled back).
    cca_cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM campaign_contact_assignments
        WHERE campaign_id = :cid AND contact_phone = :p
    """), {"cid": str(camp["id"]), "p": contact.phone})).scalar()
    assert cca_cnt == 0

    q_cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :p
    """), {"cid": str(camp["id"]), "p": contact.phone})).scalar()
    assert q_cnt == 0


async def test_worker_atomic_no_double_commit(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
):
    """M2 (revision): worker zoves get_or_assign_sender(commit=False) — no double commit error."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp["folder_id"]), "cid": str(contact.id)})
    await async_db_session.commit()

    worker = await _make_worker()
    # Should NOT raise SQLAlchemy InvalidRequestError on normal path.
    await worker._tick()


async def test_worker_respects_start_date(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
):
    """campaign.start_date в будущем → scheduled_at = MAX(now, start_date)."""
    from datetime import datetime, timezone, timedelta
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    camp, _ = await test_running_campaign_factory(
        sender_count=1, start_date=future
    )
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp["folder_id"]), "cid": str(contact.id)})
    await async_db_session.commit()

    worker = await _make_worker()
    await worker._tick()

    scheduled = (await async_db_session.execute(text("""
        SELECT scheduled_at FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :p
    """), {"cid": str(camp["id"]), "p": contact.phone})).scalar()
    assert scheduled is not None
    # Allow 1s skew.
    assert scheduled >= future - timedelta(seconds=1)


async def test_worker_batch_size_limit(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
):
    """LIMIT CAMPAIGN_ENQUEUE_BATCH_SIZE — больше N не обрабатывает за tick."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    contacts = await test_contacts_factory(count=3, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = ANY(:ids)
    """), {"fid": str(camp["folder_id"]), "ids": [str(c.id) for c in contacts]})
    await async_db_session.commit()

    worker = await _make_worker()
    worker.batch_size = 2  # force small batch

    enqueued_first = await worker._tick()
    assert enqueued_first == 2

    enqueued_second = await worker._tick()
    assert enqueued_second == 1


async def test_worker_start_stop_lifecycle():
    """campaign_enqueue_worker.start() создаёт task, .stop() корректно отменяет."""
    worker = await _make_worker()
    worker.poll_interval = 60  # avoid actual ticks during test
    assert worker._task is None
    worker.start()
    assert worker._task is not None and not worker._task.done()
    await worker.stop()
    assert worker._task.done() or worker._task.cancelled()


async def test_tick_one_campaign_no_op_when_flipped_to_done(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
):
    """WR-09: a campaign that flips out of 'running' between the tick-start
    snapshot and the per-contact commit gets ZERO queue rows — no zombie pending.

    Mirrors the concurrency race: `_tick` selects running campaigns into `c`
    snapshots, then a status flip (finish/stop/auto-pause) lands before the
    per-contact INSERT. The status-gated INSERT ... SELECT ... WHERE EXISTS must
    add 0 rows and `_tick_one_campaign` must report enqueued == 0.
    """
    from app.database import AsyncSessionLocal

    camp, _ = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp["folder_id"]), "cid": str(contact.id)})
    await async_db_session.commit()

    worker = await _make_worker()

    # Snapshot the campaign row exactly as `_tick`'s running-campaign SELECT does
    # (status='running' at selection time).
    async with AsyncSessionLocal() as snap_db:
        c = (await snap_db.execute(text("""
            SELECT id, workspace_id, folder_id, message_template, start_date,
                   allow_recontact, recontact_min_age_days
            FROM campaigns WHERE id = :cid
        """), {"cid": str(camp["id"])})).first()

    # Concurrent flip to 'done' AFTER the snapshot.
    await async_db_session.execute(text("""
        UPDATE campaigns SET status = 'done' WHERE id = :cid
    """), {"cid": str(camp["id"])})
    await async_db_session.commit()

    async with AsyncSessionLocal() as db:
        enqueued = await worker._tick_one_campaign(db, c)
    assert enqueued == 0

    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM message_queue WHERE campaign_id = :cid
    """), {"cid": str(camp["id"])})).scalar()
    assert cnt == 0


async def test_tick_continues_after_one_campaign_raises(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
    monkeypatch,
):
    """IN-11: if `_tick_one_campaign` raises for one campaign, the tick logs +
    rolls back + still processes the remaining running campaigns (no propagation).
    """
    camp1, _ = await test_running_campaign_factory(sender_count=1, name="Raiser")
    camp2, _ = await test_running_campaign_factory(sender_count=1, name="Survivor")
    # A registered contact to enqueue (folders are shared in fixtures; the
    # per-campaign CCA dedup lets camp2 enqueue it regardless of order).
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp2["folder_id"]), "cid": str(contact.id)})
    await async_db_session.commit()

    worker = await _make_worker()

    real_tick_one = worker._tick_one_campaign

    async def flaky(db, c):
        if str(c.id) == str(camp1["id"]):
            raise RuntimeError("boom in campaign 1")
        return await real_tick_one(db, c)

    monkeypatch.setattr(worker, "_tick_one_campaign", flaky)

    # Must NOT raise even though camp1 blows up.
    await worker._tick()

    # camp2 still enqueued its contact.
    cnt2 = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM message_queue WHERE campaign_id = :cid
    """), {"cid": str(camp2["id"])})).scalar()
    assert cnt2 == 1


async def test_worker_move_contact_race(
    async_db_session,
    test_running_campaign_factory,
    test_contacts_factory,
):
    """Pitfall 3: contact перемещён между папками — accept race в v1.

    Тест: контакт enqueued в первой кампании; затем contact перемещают в
    другую папку, привязанную ко второй кампании. Вторая кампания тоже
    enqueue'ит контакт — это accepted race (контакт может получить лишнее
    сообщение). Проверяем, что worker не падает.
    """
    camp1, _ = await test_running_campaign_factory(sender_count=1, name="C1")
    camp2, _ = await test_running_campaign_factory(sender_count=1, name="C2")
    contact = await test_contacts_factory(count=1, tg_status="registered")

    # Move contact into camp1's folder.
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp1["folder_id"]), "cid": str(contact.id)})
    await async_db_session.commit()

    worker = await _make_worker()
    await worker._tick()  # camp1 enqueues

    # Move into camp2's folder.
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp2["folder_id"]), "cid": str(contact.id)})
    await async_db_session.commit()

    # camp2 will enqueue same phone for camp2 — accepted race.
    await worker._tick()

    # Both campaigns have rows — that's the accepted race.
    c1 = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM message_queue WHERE campaign_id = :cid
    """), {"cid": str(camp1["id"])})).scalar()
    c2 = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM message_queue WHERE campaign_id = :cid
    """), {"cid": str(camp2["id"])})).scalar()
    assert c1 == 1 and c2 == 1


# ─── EVAC-03: periodic sweep drains stranded backlog (attach-before-freeze) ───

async def test_sweep_evacuates_stranded_backlog(
    async_db_session,
    test_running_campaign_factory,
    test_queue_item_factory,
):
    """EVAC-03: a healthy sender was attached BEFORE the freeze, so failover never
    fired for the frozen sender's backlog (failover only runs inline at freeze
    time and never re-runs — root cause #1). The periodic sweep drains that
    stranded cold-pending backlog onto the healthy sender within one tick, resets
    scheduled_at to NOW(), and keeps CCA in lock-step."""
    camp, senders = await test_running_campaign_factory(sender_count=2)
    frozen, healthy = senders[0], senders[1]

    phones = [f"+7990060{i:04d}" for i in range(3)]
    for ph in phones:
        await test_queue_item_factory(camp["id"], frozen.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)

    # Freeze one sender AFTER both are attached + pause its pending +24h.
    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    moved = await campaign_enqueue_worker._sweep_stranded_cold_backlog()

    after = await _pending_counts(async_db_session, camp["id"])
    assert moved == 3, "sweep must drain all 3 stranded cold rows off the frozen sender"
    assert sum(after.values()) == 3, "sweep must not create or drop rows"
    assert after.get(str(frozen.id), 0) == 0, "frozen sender holds 0 pending after sweep"
    assert after.get(str(healthy.id), 0) == 3, "healthy sender holds all 3 rows"

    horizon = (await async_db_session.execute(
        text("SELECT NOW() + INTERVAL '1 hour'")
    )).scalar()
    for ph in phones:
        assert await _cca_sender_for(async_db_session, camp["id"], ph) == str(healthy.id)
        sched = await _scheduled_at(async_db_session, camp["id"], ph)
        assert sched is not None and sched < horizon, (
            "swept row scheduled_at must be reset to NOW() (not the +24h pause)"
        )


async def test_sweep_idempotent(
    async_db_session,
    test_running_campaign_factory,
    test_queue_item_factory,
):
    """A second sweep moves nothing and leaves the distribution unchanged (the
    backlog now sits on an eligible sender, so it is no longer discovered)."""
    camp, senders = await test_running_campaign_factory(sender_count=2)
    frozen, healthy = senders[0], senders[1]

    for i in range(3):
        await test_queue_item_factory(camp["id"], frozen.id, f"+7990061{i:04d}",
                                      status="pending", with_cca=True,
                                      with_conversation=False)
    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    first = await campaign_enqueue_worker._sweep_stranded_cold_backlog()
    dist_after_first = await _pending_counts(async_db_session, camp["id"])

    second = await campaign_enqueue_worker._sweep_stranded_cold_backlog()
    dist_after_second = await _pending_counts(async_db_session, camp["id"])

    assert first == 3, "first sweep drains the stranded backlog"
    assert second == 0, "second sweep must move 0 rows"
    assert dist_after_second == dist_after_first, "distribution must be unchanged"


# ─── EVEN-split worker pass: idle eligible senders picked up every tick ───────
# (debug: campaign-pending-not-on-idle-senders, 2026-07-10)


async def test_worker_even_split_backfills_idle_senders(
    async_db_session,
    test_running_campaign_factory,
    test_queue_item_factory,
):
    """The per-tick even-split pass redistributes a standing cold-pending backlog
    onto eligible-but-idle senders of a RUNNING campaign — the exact production
    symptom: healthy senders sat at 0 pending while the backlog stayed stuck on
    the rest of the pool, because nothing evened load among eligible senders."""
    camp, senders = await test_running_campaign_factory(sender_count=2)
    loaded, idle = senders[0], senders[1]

    phones = [f"+7990062{i:04d}" for i in range(4)]
    for ph in phones:
        await test_queue_item_factory(camp["id"], loaded.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)

    moved = await campaign_enqueue_worker._rebalance_even_running_campaigns()

    after = await _pending_counts(async_db_session, camp["id"])
    assert moved == 2, "half of the backlog must move onto the idle sender"
    assert sum(after.values()) == 4, "even-split must not create or drop rows"
    assert after.get(str(loaded.id), 0) == 2
    assert after.get(str(idle.id), 0) == 2
    # Moved rows keep scheduled_at (no reset — donors are healthy) and CCA syncs.
    for ph in phones:
        row = (await async_db_session.execute(text("""
            SELECT sender_id FROM message_queue
            WHERE campaign_id = :cid AND recipient_phone = :phone AND status = 'pending'
        """), {"cid": str(camp["id"]), "phone": ph})).first()
        assert await _cca_sender_for(async_db_session, camp["id"], ph) == str(row[0])


async def test_worker_even_split_skips_non_running_campaign(
    async_db_session,
    test_running_campaign_factory,
    test_queue_item_factory,
):
    """A paused campaign's backlog is NOT touched by the even-split pass — the
    worker only iterates campaigns with status='running'."""
    camp, senders = await test_running_campaign_factory(sender_count=2)
    loaded, idle = senders[0], senders[1]

    for i in range(4):
        await test_queue_item_factory(camp["id"], loaded.id, f"+7990063{i:04d}",
                                      status="pending", with_cca=True,
                                      with_conversation=False)
    await async_db_session.execute(text(
        "UPDATE campaigns SET status = 'paused' WHERE id = :cid"
    ), {"cid": str(camp["id"])})
    await async_db_session.commit()

    moved = await campaign_enqueue_worker._rebalance_even_running_campaigns()

    after = await _pending_counts(async_db_session, camp["id"])
    assert moved == 0, "paused campaign must not be rebalanced"
    assert after.get(str(loaded.id), 0) == 4
    assert after.get(str(idle.id), 0) == 0
