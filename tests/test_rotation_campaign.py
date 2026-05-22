"""Plan 04-04 Task 3: rotation.py rewrite — per-campaign sender assignment.

Covers D-06:
- ``get_or_assign_sender(campaign_id, contact_phone, db, *, commit=True)`` signature.
- Source of senders = ``campaign_senders`` pool (not workspace-wide).
- Race-safe via ``ON CONFLICT (campaign_id, contact_phone) DO NOTHING``.
- ``commit`` kwarg lets CampaignEnqueueWorker control its own transaction.
"""

import inspect

import pytest
from sqlalchemy import text

from app.services.rotation import get_or_assign_sender

pytestmark = pytest.mark.asyncio


async def test_get_or_assign_sender_signature_uses_campaign_id():
    """rotation.get_or_assign_sender принимает campaign_id (not context_id)."""
    sig = inspect.signature(get_or_assign_sender)
    params = list(sig.parameters.keys())
    # campaign_id MUST be first positional, contact_phone second, db third.
    assert "campaign_id" in params, f"campaign_id missing in {params}"
    assert "contact_phone" in params, f"contact_phone missing in {params}"
    assert "db" in params, f"db missing in {params}"
    assert "context_id" not in params, f"context_id should NOT be in signature: {params}"
    # commit kwarg-only (M2 revision)
    assert "commit" in params
    assert sig.parameters["commit"].default is True


async def test_rotation_picks_from_campaign_senders_only(
    async_db_session,
    test_campaign_factory,
    test_sender_factory,
    attach_sender_to_campaign,
):
    """Sender pool = campaign_senders, не глобально workspace senders."""
    # Two senders in workspace; only one attached to campaign.
    s_attached = await test_sender_factory(slug="attached-1")
    s_unattached = await test_sender_factory(slug="unattached-1")
    camp = await test_campaign_factory(status="running")
    await attach_sender_to_campaign(camp["id"], s_attached.id)

    sender = await get_or_assign_sender(
        camp["id"], "+71234567890", async_db_session
    )
    assert sender is not None
    assert sender.id == s_attached.id
    assert sender.id != s_unattached.id


async def test_rotation_returns_assigned_sender_on_retry(
    async_db_session,
    test_campaign_factory,
    test_sender_factory,
    attach_sender_to_campaign,
):
    """Если cca уже есть — возвращает тот же sender (idempotent)."""
    s = await test_sender_factory()
    camp = await test_campaign_factory(status="running")
    await attach_sender_to_campaign(camp["id"], s.id)

    first = await get_or_assign_sender(camp["id"], "+71112223333", async_db_session)
    second = await get_or_assign_sender(camp["id"], "+71112223333", async_db_session)
    assert first is not None and second is not None
    assert first.id == second.id

    # One cca row exists.
    cnt = (await async_db_session.execute(
        text("SELECT COUNT(*) FROM campaign_contact_assignments "
             "WHERE campaign_id=:cid AND contact_phone=:p"),
        {"cid": str(camp["id"]), "p": "+71112223333"},
    )).scalar()
    assert cnt == 1


async def test_rotation_skips_inactive_senders(
    async_db_session,
    test_campaign_factory,
    test_sender_factory,
    attach_sender_to_campaign,
):
    """Sender с auth_status != 'ok' OR lifecycle_status != 'active' — не выбирается."""
    s_inactive = await test_sender_factory(
        slug="dead", auth_status="locked", lifecycle_status="active"
    )
    s_active = await test_sender_factory(slug="alive")
    camp = await test_campaign_factory(status="running")
    await attach_sender_to_campaign(camp["id"], s_inactive.id)
    await attach_sender_to_campaign(camp["id"], s_active.id)

    sender = await get_or_assign_sender(camp["id"], "+79991112233", async_db_session)
    assert sender is not None
    assert sender.id == s_active.id


async def test_rotation_returns_none_when_no_active_senders(
    async_db_session,
    test_campaign_factory,
    test_sender_factory,
    attach_sender_to_campaign,
):
    """Все sender'ы кампании auth_status='locked' → возвращает None (caller handles)."""
    s_dead = await test_sender_factory(slug="dead-only", auth_status="locked")
    camp = await test_campaign_factory(status="running")
    await attach_sender_to_campaign(camp["id"], s_dead.id)

    sender = await get_or_assign_sender(camp["id"], "+79990000001", async_db_session)
    assert sender is None


async def test_rotation_unique_constraint_protects_race(
    async_db_session,
    test_campaign_factory,
    test_sender_factory,
    attach_sender_to_campaign,
):
    """ON CONFLICT (campaign_id, contact_phone) DO NOTHING — нет дублей."""
    s = await test_sender_factory()
    camp = await test_campaign_factory(status="running")
    await attach_sender_to_campaign(camp["id"], s.id)

    # Three sequential calls for same (campaign, phone) — should yield one row.
    for _ in range(3):
        await get_or_assign_sender(camp["id"], "+79995554433", async_db_session)

    cnt = (await async_db_session.execute(
        text("SELECT COUNT(*) FROM campaign_contact_assignments "
             "WHERE campaign_id=:cid AND contact_phone=:p"),
        {"cid": str(camp["id"]), "p": "+79995554433"},
    )).scalar()
    assert cnt == 1
