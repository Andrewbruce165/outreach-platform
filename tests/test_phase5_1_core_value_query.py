"""GET /api/v1/telemetry/core-value — UI-TEL-02 (Core Value KPI #9).

Covers:
- Empty workspace → all None fields
- After signup_completed @ T0 and campaign_launched @ T0+300s →
    time_to_first_campaign_seconds ≈ 300, signup_at ≈ T0, first_launch_at ≈ T1
- Workspace isolation: events in ws A do not bleed into ws B's response
"""

import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ── helpers ──────────────────────────────────────────────────────────────────


def _auth_headers(jwt_factory, sub: str = "core-value-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ── happy paths ──────────────────────────────────────────────────────────────


async def test_core_value_returns_null_when_no_events(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """No telemetry events for workspace → all three fields are null."""
    await _bind(async_db_session, test_workspace.id, "u-cv-empty")
    resp = await async_client.get(
        "/api/v1/telemetry/core-value",
        headers=_auth_headers(valid_supabase_jwt, "u-cv-empty"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "time_to_first_campaign_seconds", "signup_at", "first_launch_at",
    }
    assert body["time_to_first_campaign_seconds"] is None
    assert body["signup_at"] is None
    assert body["first_launch_at"] is None


async def test_core_value_requires_auth(async_client):
    resp = await async_client.get("/api/v1/telemetry/core-value")
    assert resp.status_code == 401


async def test_core_value_computes_delta(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """signup at T0, campaign_launched at T0+300s → delta_seconds ≈ 300."""
    await _bind(async_db_session, test_workspace.id, "u-cv-delta")

    t0 = datetime.now(timezone.utc) - timedelta(minutes=10)
    t1 = t0 + timedelta(seconds=300)
    for ev, ts in [("signup_completed", t0), ("campaign_launched", t1)]:
        await async_db_session.execute(text("""
            INSERT INTO telemetry_events
                (event_id, workspace_id, event, props, server_ts)
            VALUES (:eid, :wid, :ev, '{}'::jsonb, :ts)
        """), {"eid": str(_uuid.uuid4()), "wid": str(test_workspace.id),
               "ev": ev, "ts": ts})
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/telemetry/core-value",
        headers=_auth_headers(valid_supabase_jwt, "u-cv-delta"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["time_to_first_campaign_seconds"] is not None
    # Delta should be ~300 seconds (Postgres EPOCH from interval → INT cast).
    # Allow tiny tolerance for timestamptz round-tripping.
    assert abs(body["time_to_first_campaign_seconds"] - 300) <= 2, body
    assert body["signup_at"] is not None
    assert body["first_launch_at"] is not None


async def test_core_value_min_signup_used_when_multiple(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """KPI uses MIN(signup_completed.server_ts) — if multiple signups recorded
    (shouldn't happen in prod, but tests must be defensive), the earliest
    one is the anchor."""
    await _bind(async_db_session, test_workspace.id, "u-cv-min")

    t_first = datetime.now(timezone.utc) - timedelta(hours=2)
    t_dup = t_first + timedelta(minutes=5)        # duplicate signup later
    t_launch = t_first + timedelta(minutes=15)    # launch 15 min after first signup
    for ev, ts in [
        ("signup_completed", t_first),
        ("signup_completed", t_dup),
        ("campaign_launched", t_launch),
    ]:
        await async_db_session.execute(text("""
            INSERT INTO telemetry_events
                (event_id, workspace_id, event, props, server_ts)
            VALUES (:eid, :wid, :ev, '{}'::jsonb, :ts)
        """), {"eid": str(_uuid.uuid4()), "wid": str(test_workspace.id),
               "ev": ev, "ts": ts})
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/telemetry/core-value",
        headers=_auth_headers(valid_supabase_jwt, "u-cv-min"),
    )
    body = resp.json()
    # Delta = launch - MIN(signup) = 15 min = 900 sec
    assert abs(body["time_to_first_campaign_seconds"] - 900) <= 2, body


async def test_core_value_isolated_by_workspace(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Events in workspace A do NOT contribute to workspace B's /core-value."""
    from app.models import Workspace

    # ws A — bind user A (the default test_workspace via valid_supabase_jwt)
    await _bind(async_db_session, test_workspace.id, "u-cv-ws-a")

    # ws B — separate workspace, separate user
    other = Workspace(name="OtherWS-core-value")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-cv-ws-b")

    # Insert signup+launch ONLY in ws A
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    t1 = t0 + timedelta(seconds=60)
    for ev, ts in [("signup_completed", t0), ("campaign_launched", t1)]:
        await async_db_session.execute(text("""
            INSERT INTO telemetry_events
                (event_id, workspace_id, event, props, server_ts)
            VALUES (:eid, :wid, :ev, '{}'::jsonb, :ts)
        """), {"eid": str(_uuid.uuid4()), "wid": str(test_workspace.id),
               "ev": ev, "ts": ts})
    await async_db_session.commit()

    # ws A: has delta
    resp_a = await async_client.get(
        "/api/v1/telemetry/core-value",
        headers=_auth_headers(valid_supabase_jwt, "u-cv-ws-a"),
    )
    assert resp_a.status_code == 200
    body_a = resp_a.json()
    assert body_a["time_to_first_campaign_seconds"] is not None

    # ws B: no events → all None
    resp_b = await async_client.get(
        "/api/v1/telemetry/core-value",
        headers=_auth_headers(valid_supabase_jwt, "u-cv-ws-b"),
    )
    assert resp_b.status_code == 200
    body_b = resp_b.json()
    assert body_b["time_to_first_campaign_seconds"] is None, body_b
    assert body_b["signup_at"] is None
    assert body_b["first_launch_at"] is None
