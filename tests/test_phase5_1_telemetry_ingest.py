"""POST /api/v1/telemetry/events — UI-TEL-01 (15-event whitelist + idempotency).

Covers:
- Whitelisted event → 202 with event_id in body
- Unknown event → 400 UNKNOWN_EVENT
- Idempotency: same event_id POST'd N times → only 1 row in telemetry_events
- Unauth → 401
- Whitelist size + canonical members (UI-SPEC §9 enumeration sanity check)
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ── helpers ──────────────────────────────────────────────────────────────────


def _auth_headers(jwt_factory, sub: str = "telemetry-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ── core flow ────────────────────────────────────────────────────────────────


async def test_ingest_whitelisted_event_accepted(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """signup_completed → 202 + body {accepted: True, event_id: <uuid>}."""
    await _bind(async_db_session, test_workspace.id, "u-tel-accept")
    resp = await async_client.post(
        "/api/v1/telemetry/events",
        json={"event": "signup_completed", "props": {}},
        headers=_auth_headers(valid_supabase_jwt, "u-tel-accept"),
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] is True
    assert "event_id" in body
    # event_id is a valid uuid string
    _uuid.UUID(body["event_id"])


async def test_ingest_whitelist_unknown_event_rejected(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Non-whitelisted event → 400 UNKNOWN_EVENT."""
    await _bind(async_db_session, test_workspace.id, "u-tel-unknown")
    resp = await async_client.post(
        "/api/v1/telemetry/events",
        json={"event": "totally_made_up_event", "props": {}},
        headers=_auth_headers(valid_supabase_jwt, "u-tel-unknown"),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["code"] == "UNKNOWN_EVENT"


async def test_ingest_idempotent_on_event_id(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Same event_id POST'd 3× → only 1 row in telemetry_events (ON CONFLICT)."""
    await _bind(async_db_session, test_workspace.id, "u-tel-idem")
    eid = str(_uuid.uuid4())
    for _ in range(3):
        resp = await async_client.post(
            "/api/v1/telemetry/events",
            json={"event_id": eid, "event": "signup_completed", "props": {}},
            headers=_auth_headers(valid_supabase_jwt, "u-tel-idem"),
        )
        assert resp.status_code == 202
        assert resp.json()["event_id"] == eid

    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM telemetry_events WHERE event_id = :eid
    """), {"eid": eid})).scalar()
    assert cnt == 1, f"Expected idempotency (1 row), got {cnt} for event_id={eid}"


async def test_ingest_requires_auth(async_client):
    """No Authorization header → 401."""
    resp = await async_client.post(
        "/api/v1/telemetry/events",
        json={"event": "signup_completed", "props": {}},
    )
    assert resp.status_code == 401


# ── whitelist sanity ─────────────────────────────────────────────────────────


def test_ingest_whitelist_contains_all_15_events():
    """The router-level whitelist matches UI-SPEC §9 enumeration + 2 derived."""
    from app.routers.telemetry import _EVENT_WHITELIST

    expected = {
        # 15 events from UI-SPEC §9
        "magic_link_requested",
        "signup_completed",
        "sender_added",
        "contacts_imported",
        "csv_import_completed",
        "agent_created",
        "campaign_created",
        "campaign_launched",
        "campaign_paused",
        "campaign_resumed",
        "conversation_taken_over_by_human",
        "llm_trace_opened",
        "workspace_api_key_created",
        "settings_changed",
        "agent_voice_changed",
        # 2 derived (dashboard engagement + custom-tool builder)
        "custom_tool_added",
        "dashboard_viewed",
    }
    assert _EVENT_WHITELIST == expected, (
        f"Whitelist drift detected. Diff: "
        f"router-only={_EVENT_WHITELIST - expected}, "
        f"expected-only={expected - _EVENT_WHITELIST}"
    )


# ── persistence sanity ──────────────────────────────────────────────────────


async def test_ingest_persists_workspace_id_and_event(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Posted event lands in telemetry_events with workspace_id + event populated."""
    await _bind(async_db_session, test_workspace.id, "u-tel-persist")
    eid = str(_uuid.uuid4())
    resp = await async_client.post(
        "/api/v1/telemetry/events",
        json={"event_id": eid, "event": "campaign_launched",
              "props": {"campaign_id": "abc"}},
        headers=_auth_headers(valid_supabase_jwt, "u-tel-persist"),
    )
    assert resp.status_code == 202
    row = (await async_db_session.execute(text("""
        SELECT workspace_id, event FROM telemetry_events WHERE event_id = :eid
    """), {"eid": eid})).first()
    assert row is not None
    assert str(row.workspace_id) == str(test_workspace.id)
    assert row.event == "campaign_launched"
