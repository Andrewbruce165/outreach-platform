"""Migration 026 + identity-scoped re-contact policy.

Covers the re-contact dedup branch in CampaignEnqueueWorker. A prior
conversation only blocks this campaign's cold opener when it is *in scope* —
same campaign OR handled by a sender that is also in this campaign's pool
(the same agent could be routed to). The protected/freshness rules then layer
on top of that scope:
  * allow_recontact=false (default) → any in-scope conversation blocks.
  * allow_recontact=true → only an in-scope PROTECTED (live & fresh) dialog
    blocks; closed (finished) or stale ones are released for re-contact.
  * out of scope (different campaign AND a sender not in the pool) → never
    blocks, even with allow_recontact=false (different agent + campaign).
Plus the conversations.updated_at freshness trigger.

Tests that exercise the protected/freshness branches pin the conversation to a
pool sender (``sender=senders[0]``) so the dialog is in scope; otherwise the
identity scope alone would release it for a different reason.
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
    """allow_recontact=false (default): an in-scope (pool sender) FINISHED dialog
    still blocks re-contact — strict behavior unchanged for the same agent."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    await test_conversation_factory(
        contact_phone=contact.phone, status="finished", sender=senders[0]
    )
    await async_db_session.commit()

    enqueued = await (await _make_worker())._tick()
    assert enqueued == 0
    assert await _queued(async_db_session, camp["id"], contact.phone) == 0


async def test_recontact_releases_finished_conversation(
    async_db_session, test_running_campaign_factory, test_contacts_factory,
    test_conversation_factory,
):
    """allow_recontact=true: an in-scope (pool sender) FINISHED (closed) dialog no
    longer blocks — contact is enqueued for a fresh first-touch."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    await test_conversation_factory(
        contact_phone=contact.phone, status="finished", sender=senders[0]
    )
    await _set_recontact(async_db_session, camp["id"], allow=True)
    await async_db_session.commit()

    enqueued = await (await _make_worker())._tick()
    assert enqueued == 1
    assert await _queued(async_db_session, camp["id"], contact.phone) == 1


async def test_recontact_still_protects_active_fresh_conversation(
    async_db_session, test_running_campaign_factory, test_contacts_factory,
    test_conversation_factory,
):
    """allow_recontact=true: an in-scope (pool sender) live (lead) + fresh dialog
    is PROTECTED — a cold opener must never interrupt an active manager
    conversation handled by the same agent."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    await test_conversation_factory(
        contact_phone=contact.phone, status="lead", sender=senders[0]
    )
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
    camp, senders = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    conv = await test_conversation_factory(
        contact_phone=contact.phone, status="lead", sender=senders[0]
    )
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
    """allow_recontact=true: an in-scope (pool sender) bot_ignored dialog stays
    PROTECTED — the AI was deliberately silenced on that peer; it must not get a
    cold opener."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    await test_conversation_factory(
        contact_phone=contact.phone, status="bot_ignored", sender=senders[0]
    )
    await _set_recontact(async_db_session, camp["id"], allow=True)
    await async_db_session.commit()

    enqueued = await (await _make_worker())._tick()
    assert enqueued == 0
    assert await _queued(async_db_session, camp["id"], contact.phone) == 0


async def test_different_agent_and_campaign_allows_recontact(
    async_db_session, test_running_campaign_factory, test_contacts_factory,
    test_conversation_factory,
):
    """Regression ("Паша аналитика"): allow_recontact=false, but the only existing
    dialog is out of scope — a different campaign run by a different agent (sender
    NOT in this campaign's pool). The contact is enqueued anyway, even though the
    dialog is ACTIVE — a different agent + campaign is free to re-contact."""
    camp, _senders = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    # sender omitted → fresh sender, NOT attached to camp's pool; campaign_id=None.
    await test_conversation_factory(contact_phone=contact.phone, status="active")
    await async_db_session.commit()

    enqueued = await (await _make_worker())._tick()
    assert enqueued == 1
    assert await _queued(async_db_session, camp["id"], contact.phone) == 1


async def test_same_campaign_conversation_blocks(
    async_db_session, test_running_campaign_factory, test_contacts_factory,
    test_conversation_factory,
):
    """A dialog tagged with THIS campaign blocks re-contact regardless of which
    sender handled it (same campaign = in scope)."""
    camp, _senders = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    # Different (fresh) sender, but campaign_id pins it to THIS campaign.
    await test_conversation_factory(
        contact_phone=contact.phone, status="finished", campaign_id=camp["id"]
    )
    await async_db_session.commit()

    enqueued = await (await _make_worker())._tick()
    assert enqueued == 0
    assert await _queued(async_db_session, camp["id"], contact.phone) == 0


async def test_pool_sender_blocks_other_campaign(
    async_db_session, test_running_campaign_factory, test_contacts_factory,
    test_conversation_factory,
):
    """A dialog from another campaign still blocks when its sender is in THIS
    campaign's pool (same agent = in scope)."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    # campaign_id=None (outside this campaign) but sender IS in the pool.
    await test_conversation_factory(
        contact_phone=contact.phone, status="finished", sender=senders[0]
    )
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
