"""Plan 04-04 Task 4: CampaignEnqueueWorker integration tests.

Covers CAMP-09 (досыпание контактов) + CAMP-10 (enqueue из folder).

Tests instantiate a fresh ``CampaignEnqueueWorker`` instance per test
(NOT the module-level singleton) to avoid lifespan interference.
"""

import asyncio

import pytest
from sqlalchemy import text

from app.services.campaign_enqueue import CampaignEnqueueWorker

pytestmark = pytest.mark.asyncio


async def _make_worker():
    """Local helper — fresh worker (no module singleton)."""
    w = CampaignEnqueueWorker()
    return w


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
    """Cross-campaign dedup: contact already has a conversation in the workspace
    → worker does NOT enqueue a first-touch (regression for the duplicate-intro
    incident, where a copied campaign re-introduced itself to an active lead)."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await async_db_session.execute(text("""
        UPDATE contacts SET folder_id = :fid WHERE id = :cid
    """), {"fid": str(camp["folder_id"]), "cid": str(contact.id)})
    # Existing conversation for this phone in the same workspace.
    await test_conversation_factory(contact_phone=contact.phone, status="lead")
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
