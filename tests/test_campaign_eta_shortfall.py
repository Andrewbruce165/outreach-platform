"""Variant 1 (deadline-mass-fail fix) — ETA-vs-deadline forecast.

Covers `app/routers/campaigns.py::_compute_eta_shortfall` /
`_count_work_days`, and the `eta_shortfall` field surfaced on
CampaignResponse (GET/POST /start etc., via `_campaign_to_response`).

Root cause this closes: a campaign past stop_date used to silently fail its
entire pending queue with no warning beforehand. This is the "warn before it
happens" half of the fix (the other half is D-11 v2 auto-pause in queue.py,
covered by tests/test_queue_per_campaign_hours.py and
tests/test_queue_lifecycle_fixes.py).
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.models import Campaign
from app.routers.campaigns import _compute_eta_shortfall, _count_work_days

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str) -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def _load_campaign_orm(db, campaign_id) -> Campaign:
    return (await db.execute(
        select(Campaign).where(Campaign.id == campaign_id)
    )).scalar_one()


# ── _count_work_days: pure function edge cases ────────────────────────────────


def test_count_work_days_past_stop_date_returns_zero():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    past_stop = now - timedelta(hours=1)
    assert _count_work_days(
        campaign_tz="Europe/Moscow", work_days_mask=127,
        stop_date=past_stop, now=now,
    ) == 0


def test_count_work_days_invalid_timezone_returns_zero():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    future_stop = now + timedelta(days=5)
    assert _count_work_days(
        campaign_tz="Not/ARealZone", work_days_mask=127,
        stop_date=future_stop, now=now,
    ) == 0


def test_count_work_days_counts_today_inclusive():
    # 2026-07-10 is a Friday. Mo-Fri mask (31), 3 days out (Fri, Sat, Sun, Mon)
    # → only Fri (today) + Mon count = 2.
    now = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)  # Fri
    stop = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)  # Mon
    assert _count_work_days(
        campaign_tz="UTC", work_days_mask=31,  # Mo-Fri
        stop_date=stop, now=now,
    ) == 2


def test_count_work_days_full_week_mask():
    now = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)  # Fri
    stop = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)  # Mon, 4 days later
    assert _count_work_days(
        campaign_tz="UTC", work_days_mask=127,  # every day
        stop_date=stop, now=now,
    ) == 4


# ── _compute_eta_shortfall: None short-circuits ───────────────────────────────


async def test_eta_shortfall_none_without_stop_date(
    async_db_session, test_campaign_factory,
):
    camp = await test_campaign_factory(stop_date=None)
    campaign = await _load_campaign_orm(async_db_session, camp["id"])
    assert await _compute_eta_shortfall(async_db_session, campaign) is None


async def test_eta_shortfall_none_without_folder(
    async_db_session, test_campaign_factory,
):
    future_stop = datetime.now(timezone.utc) + timedelta(days=10)
    camp = await test_campaign_factory(stop_date=future_stop)
    # 024: folder_id is nullable for an incomplete draft — force it NULL to hit
    # the short-circuit (the factory always substitutes test_folder.id).
    await async_db_session.execute(text(
        "UPDATE campaigns SET folder_id = NULL WHERE id = :id"
    ), {"id": str(camp["id"])})
    await async_db_session.commit()
    campaign = await _load_campaign_orm(async_db_session, camp["id"])
    assert await _compute_eta_shortfall(async_db_session, campaign) is None


# ── _compute_eta_shortfall: real forecast math ────────────────────────────────


async def test_eta_shortfall_detects_shortfall(
    async_db_session, test_campaign_factory, test_sender_factory,
    attach_sender_to_campaign, test_contacts_factory,
):
    """1 sender at grade level 1 (LADDER_DEFAULTS budget=5/day), 3 work days
    left, 20 unassigned registered contacts → capacity 15 < remaining 20."""
    now = datetime.now(timezone.utc)
    stop = now + timedelta(days=2, hours=1)  # today + 2 more days ≈ 3 work days
    camp = await test_campaign_factory(
        stop_date=stop, timezone="UTC", work_days_mask=127,
    )
    sender = await test_sender_factory(current_level=1)
    await attach_sender_to_campaign(camp["id"], sender.id)
    await test_contacts_factory(count=20, tg_status="registered")

    campaign = await _load_campaign_orm(async_db_session, camp["id"])
    eta = await _compute_eta_shortfall(async_db_session, campaign)

    assert eta is not None
    assert eta.remaining_contacts == 20
    assert eta.daily_capacity == 5
    assert eta.work_days_left == 3
    assert eta.shortfall_contacts == 20 - 5 * 3
    assert eta.on_track is False


async def test_eta_shortfall_on_track_when_capacity_sufficient(
    async_db_session, test_campaign_factory, test_sender_factory,
    attach_sender_to_campaign, test_contacts_factory,
):
    now = datetime.now(timezone.utc)
    stop = now + timedelta(days=9, hours=1)  # ~10 work days
    camp = await test_campaign_factory(
        stop_date=stop, timezone="UTC", work_days_mask=127,
    )
    sender = await test_sender_factory(current_level=1)
    await attach_sender_to_campaign(camp["id"], sender.id)
    await test_contacts_factory(count=5, tg_status="registered")

    campaign = await _load_campaign_orm(async_db_session, camp["id"])
    eta = await _compute_eta_shortfall(async_db_session, campaign)

    assert eta is not None
    assert eta.remaining_contacts == 5
    assert eta.daily_capacity == 5
    assert eta.shortfall_contacts == 0
    assert eta.on_track is True


async def test_eta_shortfall_excludes_ineligible_senders(
    async_db_session, test_campaign_factory, test_sender_factory,
    attach_sender_to_campaign, test_contacts_factory,
):
    """A spam_limited sender's grade budget must NOT count toward capacity —
    mirrors PoolHealth.active's eligibility predicate."""
    now = datetime.now(timezone.utc)
    stop = now + timedelta(days=2, hours=1)
    camp = await test_campaign_factory(
        stop_date=stop, timezone="UTC", work_days_mask=127,
    )
    healthy = await test_sender_factory(current_level=1)
    restricted = await test_sender_factory(current_level=3, restriction_status="spam_limited")
    await attach_sender_to_campaign(camp["id"], healthy.id)
    await attach_sender_to_campaign(camp["id"], restricted.id)
    await test_contacts_factory(count=1, tg_status="registered")

    campaign = await _load_campaign_orm(async_db_session, camp["id"])
    eta = await _compute_eta_shortfall(async_db_session, campaign)

    assert eta is not None
    assert eta.daily_capacity == 5, "only the healthy level-1 sender's budget should count"


# ── HTTP surface: eta_shortfall on CampaignResponse ───────────────────────────


async def test_get_campaign_surfaces_eta_shortfall(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_contacts_factory,
):
    from app.models import AIContext

    agent = await test_agent_factory()
    await _bind(async_db_session, test_workspace.id, "u-eta")
    future_stop = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()

    r = await async_client.post("/api/v1/campaigns", json={
        "name": "EtaCamp",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "message_template": "Hi {{name}}",
        "stop_date": future_stop,
    }, headers=_auth_headers(valid_supabase_jwt, "u-eta"))
    assert r.status_code == 201, r.text
    body = r.json()
    camp = body["campaign"] if "campaign" in body else body

    r2 = await async_client.get(f"/api/v1/campaigns/{camp['id']}",
                                headers=_auth_headers(valid_supabase_jwt, "u-eta"))
    assert r2.status_code == 200, r2.text
    got = r2.json()
    assert "eta_shortfall" in got
    # No senders attached yet → 0 capacity, 0 remaining contacts → on_track
    # (nothing to send yet, not a false shortfall warning).
    assert got["eta_shortfall"]["remaining_contacts"] == 0
    assert got["eta_shortfall"]["on_track"] is True


async def test_get_campaign_eta_shortfall_null_without_stop_date(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    agent = await test_agent_factory()
    await _bind(async_db_session, test_workspace.id, "u-eta-null")

    r = await async_client.post("/api/v1/campaigns", json={
        "name": "NoDeadlineCamp",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "message_template": "Hi {{name}}",
    }, headers=_auth_headers(valid_supabase_jwt, "u-eta-null"))
    assert r.status_code == 201, r.text
    body = r.json()
    camp = body["campaign"] if "campaign" in body else body

    r2 = await async_client.get(f"/api/v1/campaigns/{camp['id']}",
                                headers=_auth_headers(valid_supabase_jwt, "u-eta-null"))
    assert r2.status_code == 200, r2.text
    assert r2.json()["eta_shortfall"] is None
