"""Migration 026: per-campaign re-contact policy.

Covers the cross-campaign dedup branch in CampaignEnqueueWorker:
  * allow_recontact=false (default) → any existing conversation still blocks.
  * allow_recontact=true → only a PROTECTED (live & fresh) dialog blocks;
    closed (finished) or stale dialogs are released for re-contact.
Plus the conversations.updated_at freshness trigger.
"""

import pytest
from sqlalchemy import text

from app.services.campaign_enqueue import CampaignEnqueueWorker

pytestmark = pytest.mark.asyncio


async def _make_worker():
    return CampaignEnqueueWorker()


async def _set_recontact(db, campaign_id, allow: bool, age_days: int = 30):
    await db.execute(
        text("""
            UPDATE campaigns
               SET allow_recontact = :allow, recontact_min_age_days = :age
             WHERE id = :cid
        """),
        {"allow": allow, "age": age_days, "cid": str(campaign_id)},
    )


async def _age_conversation(db, conv_id, days: int):
    """Backdate a conversation's updated_at to simulate a stale dialog."""
    await db.execute(
        text("UPDATE conversations SET updated_at = now() - make_interval(days => :d) WHERE id = :id"),
        {"d": days, "id": str(conv_id)},
    )


async def _move_into_folder(db, contact, folder_id):
    await db.execute(
        text("UPDATE contacts SET folder_id = :fid WHERE id = :cid"),
        {"fid": str(folder_id), "cid": str(contact.id)},
    )


async def _queued(db, campaign_id, phone) -> int:
    return (await db.execute(
        text("""
            SELECT COUNT(*) FROM message_queue
            WHERE campaign_id = :cid AND recipient_phone = :p
        """),
        {"cid": str(campaign_id), "p": phone},
    )).scalar()


async def test_default_finished_conversation_still_blocks(
    async_db_session, test_running_campaign_factory, test_contacts_factory,
    test_conversation_factory,
):
    """allow_recontact=false (default): even a FINISHED dialog blocks re-contact
    — strict behavior unchanged for existing campaigns."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    await test_conversation_factory(contact_phone=contact.phone, status="finished")
    await async_db_session.commit()

    enqueued = await (await _make_worker())._tick()
    assert enqueued == 0
    assert await _queued(async_db_session, camp["id"], contact.phone) == 0


async def test_recontact_releases_finished_conversation(
    async_db_session, test_running_campaign_factory, test_contacts_factory,
    test_conversation_factory,
):
    """allow_recontact=true: a FINISHED (closed) dialog no longer blocks —
    contact is enqueued for a fresh first-touch."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    await test_conversation_factory(contact_phone=contact.phone, status="finished")
    await _set_recontact(async_db_session, camp["id"], allow=True)
    await async_db_session.commit()

    enqueued = await (await _make_worker())._tick()
    assert enqueued == 1
    assert await _queued(async_db_session, camp["id"], contact.phone) == 1


async def test_recontact_still_protects_active_fresh_conversation(
    async_db_session, test_running_campaign_factory, test_contacts_factory,
    test_conversation_factory,
):
    """allow_recontact=true: a live (lead) + fresh dialog is PROTECTED — a cold
    opener must never interrupt an active manager conversation."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    await test_conversation_factory(contact_phone=contact.phone, status="lead")
    await _set_recontact(async_db_session, camp["id"], allow=True)
    await async_db_session.commit()

    enqueued = await (await _make_worker())._tick()
    assert enqueued == 0
    assert await _queued(async_db_session, camp["id"], contact.phone) == 0


async def test_recontact_releases_stale_conversation(
    async_db_session, test_running_campaign_factory, test_contacts_factory,
    test_conversation_factory,
):
    """allow_recontact=true: a live-status (lead) dialog older than
    recontact_min_age_days is stale → released for re-contact."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    conv = await test_conversation_factory(contact_phone=contact.phone, status="lead")
    await _set_recontact(async_db_session, camp["id"], allow=True, age_days=30)
    await _age_conversation(async_db_session, conv["id"], days=60)
    await async_db_session.commit()

    enqueued = await (await _make_worker())._tick()
    assert enqueued == 1
    assert await _queued(async_db_session, camp["id"], contact.phone) == 1


async def test_recontact_protects_bot_ignored(
    async_db_session, test_running_campaign_factory, test_contacts_factory,
    test_conversation_factory,
):
    """allow_recontact=true: bot_ignored stays PROTECTED — the AI was
    deliberately silenced on that peer; it must not get a cold opener."""
    camp, _ = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    await test_conversation_factory(contact_phone=contact.phone, status="bot_ignored")
    await _set_recontact(async_db_session, camp["id"], allow=True)
    await async_db_session.commit()

    enqueued = await (await _make_worker())._tick()
    assert enqueued == 0
    assert await _queued(async_db_session, camp["id"], contact.phone) == 0


async def test_updated_at_trigger_bumps_on_message_insert(
    async_db_session, test_conversation_factory,
):
    """Migration 026 trigger: inserting a message bumps conversations.updated_at
    (the freshness signal the recontact staleness check relies on)."""
    conv = await test_conversation_factory(status="active")
    await _age_conversation(async_db_session, conv["id"], days=60)
    await async_db_session.commit()

    before = (await async_db_session.execute(
        text("SELECT updated_at FROM conversations WHERE id = :id"),
        {"id": str(conv["id"])},
    )).scalar()

    await async_db_session.execute(
        text("""
            INSERT INTO messages (conversation_id, direction, message_text, sent_by)
            VALUES (:cid, 'inbound', 'ping', 'human')
        """),
        {"cid": str(conv["id"])},
    )
    await async_db_session.commit()

    after = (await async_db_session.execute(
        text("SELECT updated_at FROM conversations WHERE id = :id"),
        {"id": str(conv["id"])},
    )).scalar()

    assert after > before
