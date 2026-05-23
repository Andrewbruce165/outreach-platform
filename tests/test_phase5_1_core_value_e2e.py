"""End-to-end Core Value pytest — Phase 05.1 Core-Value-E2E requirement.

Simulates the 10-minute Core Value flow by firing the 6 telemetry events that
mark a new user's first campaign launch path. Asserts:

    GET /api/v1/telemetry/core-value → time_to_first_campaign_seconds < 600

This is the canonical regression for UI-SPEC §9 KPI #9 (`time_to_first_campaign_seconds`).
The Lovable UI build that ships to a customer must fire these same 6 events;
this test proves the backend ingest + KPI computation is correct in isolation.

Note: this is an API-level test, NOT a UI test. It does not boot Lovable; it
fires the events via httpx AsyncClient against the FastAPI test app.

Fixtures: uses the conftest.py canonical set (async_client, valid_supabase_jwt,
async_db_session, test_workspace) — mirrors the pattern in
tests/test_phase5_1_telemetry_ingest.py + test_phase5_1_core_value_query.py.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import text

from app.models import Workspace

pytestmark = pytest.mark.asyncio


# The 6 events that mark the Core Value path (subset of the 17-event whitelist).
CORE_VALUE_EVENTS = [
    "signup_completed",
    "sender_added",
    "contacts_imported",
    "agent_created",
    "campaign_created",
    "campaign_launched",
]


# ── helpers (same pattern as test_phase5_1_telemetry_ingest.py) ──────────────


def _auth_headers(jwt_factory, sub: str = "core-value-e2e-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid: str) -> None:
    """Bind a Supabase user (JWT sub) to a workspace as owner."""
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ── tests ────────────────────────────────────────────────────────────────────


async def test_core_value_e2e_full_flow_under_600_seconds(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Fire 6 events in sequence; expect time_to_first_campaign_seconds < 600.

    Server_ts is set by NOW() at insert time, so the elapsed wall-clock between
    the first POST (signup_completed) and the last POST (campaign_launched) is
    the delta. In a test that completes in seconds, delta will be 0-3 — well
    under the 600s target. This proves the COMPUTATION is correct; live UX
    timing is verified separately during HUMAN-UAT.
    """
    await _bind(async_db_session, test_workspace.id, "u-cv-e2e-full")
    headers = _auth_headers(valid_supabase_jwt, "u-cv-e2e-full")

    for ev in CORE_VALUE_EVENTS:
        resp = await async_client.post(
            "/api/v1/telemetry/events",
            json={
                "event_id": str(_uuid.uuid4()),
                "event": ev,
                "props": {"e2e": True},
            },
            headers=headers,
        )
        assert resp.status_code == 202, (
            f"Event '{ev}' POST returned {resp.status_code}: {resp.text}"
        )

    kpi = await async_client.get(
        "/api/v1/telemetry/core-value", headers=headers,
    )
    assert kpi.status_code == 200, kpi.text
    body = kpi.json()
    assert body["time_to_first_campaign_seconds"] is not None, (
        f"Expected non-null delta after firing all 6 events; got {body}"
    )
    # Core Value target: < 600 seconds (10 minutes) per UI-SPEC §9 KPI #9.
    assert body["time_to_first_campaign_seconds"] < 600, (
        f"Core Value target violated: expected < 600s, "
        f"got {body['time_to_first_campaign_seconds']}s"
    )
    # In a test, delta will be very small (server_ts of last POST minus
    # server_ts of first POST — they happen in rapid succession):
    assert body["time_to_first_campaign_seconds"] >= 0, (
        "Negative delta — clock or ordering bug"
    )
    # Anchor timestamps must both be set when delta is non-null.
    assert body["signup_at"] is not None
    assert body["first_launch_at"] is not None


async def test_core_value_e2e_returns_null_with_partial_flow(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Firing only signup_completed (no launch yet) → delta is null."""
    await _bind(async_db_session, test_workspace.id, "u-cv-e2e-partial")
    headers = _auth_headers(valid_supabase_jwt, "u-cv-e2e-partial")

    resp = await async_client.post(
        "/api/v1/telemetry/events",
        json={
            "event_id": str(_uuid.uuid4()),
            "event": "signup_completed",
            "props": {},
        },
        headers=headers,
    )
    assert resp.status_code == 202

    kpi = await async_client.get(
        "/api/v1/telemetry/core-value", headers=headers,
    )
    assert kpi.status_code == 200
    body = kpi.json()
    assert body["time_to_first_campaign_seconds"] is None, (
        f"Expected null delta with no campaign_launched; got {body}"
    )
    assert body["signup_at"] is not None
    assert body["first_launch_at"] is None


async def test_core_value_e2e_workspace_isolation(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Workspace A fires full flow; workspace B sees null (events scoped by workspace_id)."""
    # Workspace A — bind user A to the default test_workspace
    await _bind(async_db_session, test_workspace.id, "u-cv-e2e-ws-a")
    headers_a = _auth_headers(valid_supabase_jwt, "u-cv-e2e-ws-a")

    # Workspace B — separate workspace, separate user
    other = Workspace(name="OtherWS-core-value-e2e")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-cv-e2e-ws-b")
    headers_b = _auth_headers(valid_supabase_jwt, "u-cv-e2e-ws-b")

    # Workspace A fires all 6 events.
    for ev in CORE_VALUE_EVENTS:
        resp = await async_client.post(
            "/api/v1/telemetry/events",
            json={
                "event_id": str(_uuid.uuid4()),
                "event": ev,
                "props": {},
            },
            headers=headers_a,
        )
        assert resp.status_code == 202

    # Workspace A — non-null delta.
    kpi_a = await async_client.get(
        "/api/v1/telemetry/core-value", headers=headers_a,
    )
    assert kpi_a.status_code == 200
    body_a = kpi_a.json()
    assert body_a["time_to_first_campaign_seconds"] is not None
    assert body_a["time_to_first_campaign_seconds"] < 600

    # Workspace B — no events; all null (workspace isolation enforced in SQL).
    kpi_b = await async_client.get(
        "/api/v1/telemetry/core-value", headers=headers_b,
    )
    assert kpi_b.status_code == 200
    body_b = kpi_b.json()
    assert body_b["time_to_first_campaign_seconds"] is None, body_b
    assert body_b["signup_at"] is None
    assert body_b["first_launch_at"] is None
