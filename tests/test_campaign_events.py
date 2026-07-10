"""Campaign event log endpoint (quick 260710-cge).

GET /api/v1/campaigns/{id}/events — read-only merge of message_queue
(sent/failed) and llm_calls.tool_calls (mark_as_lead / transfer_to_manager /
finish_conversation) into one newest-first, cursor-paginated event list.

Covers:
- merged ordering across both sources (newest first, detail mapping)
- jsonb-null tool_calls guard (jsonb 'null' rows crash jsonb_array_elements
  without the jsonb_typeof(...)='array' WHERE predicate)
- workspace isolation (foreign campaign → 404)
- cursor pagination (has_more / next_before, no duplicates across pages)
"""

import json

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "events-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def _seed_queue_event(
    db, wid, cid, sid, phone, status, *,
    error=None, name=None, r_name=None, r_username=None, age_seconds=0,
):
    """Insert one message_queue row aged `age_seconds` into the past."""
    await db.execute(text("""
        INSERT INTO message_queue (
            workspace_id, campaign_id, sender_id, recipient_phone,
            recipient_name, result_recipient_name, result_recipient_username,
            item_type, status, scheduled_at, created_at, finished_at,
            error_message
        ) VALUES (
            :wid, :cid, :sid, :phone,
            :name, :rname, :rusername,
            'message', :status, NOW(),
            NOW() - (:age || ' seconds')::interval,
            NOW() - (:age || ' seconds')::interval,
            :err
        )
    """), {
        "wid": str(wid), "cid": str(cid), "sid": str(sid), "phone": phone,
        "name": name, "rname": r_name, "rusername": r_username,
        "status": status, "age": str(age_seconds), "err": error,
    })
    await db.commit()


async def _make_conversation(db, wid, sid, cid, phone, name=None):
    row = (await db.execute(text("""
        INSERT INTO conversations (
            workspace_id, sender_id, contact_phone, contact_name,
            campaign_id, status
        ) VALUES (:wid, :sid, :phone, :name, :cid, 'active')
        RETURNING id
    """), {
        "wid": str(wid), "sid": str(sid), "phone": phone,
        "name": name, "cid": str(cid),
    })).first()
    await db.commit()
    return row.id


async def _seed_llm_call(
    db, wid, conv_id, cid, sid, *, tool_calls, age_seconds=0,
):
    """Insert one llm_calls row.

    ``tool_calls``: python list → stored as jsonb array;
    None → stored as jsonb 'null' (JSON null, NOT SQL NULL — the exact shape
    that blows up an unguarded jsonb_array_elements).
    """
    await db.execute(text("""
        INSERT INTO llm_calls (
            workspace_id, conversation_id, campaign_id, sender_id,
            model, prompt, tool_calls, created_at
        ) VALUES (
            :wid, :conv, :cid, :sid,
            'gpt-test', CAST(:prompt AS jsonb), CAST(:tc AS jsonb),
            NOW() - (:age || ' seconds')::interval
        )
    """), {
        "wid": str(wid), "conv": str(conv_id), "cid": str(cid),
        "sid": str(sid),
        "prompt": json.dumps({"messages": []}),
        "tc": json.dumps(tool_calls),  # None → 'null' → jsonb null
        "age": str(age_seconds),
    })
    await db.commit()


# ── Test 1: merged ordering across both sources ───────────────────────────────


async def test_events_merged_newest_first(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    camp = await test_campaign_factory(name="EventsCamp")
    sender = await test_sender_factory(slug="events-sender-1")
    await _bind(async_db_session, test_workspace.id, "u-events-merge")
    wid, cid, sid = test_workspace.id, camp["id"], sender.id

    # Oldest: successful send with resolved recipient info.
    await _seed_queue_event(
        async_db_session, wid, cid, sid, "+79990001001", "sent",
        name="Fallback Name", r_name="Resolved Name", r_username="resolved_u",
        age_seconds=300,
    )
    # Middle: failed send with an error message.
    await _seed_queue_event(
        async_db_session, wid, cid, sid, "+79990001002", "failed",
        name="Fail Name", error="FLOOD_WAIT 30", age_seconds=200,
    )
    # Newest: mark_as_lead tool call.
    conv = await _make_conversation(
        async_db_session, wid, sid, cid, "+79990001003", name="Lead Name")
    await _seed_llm_call(
        async_db_session, wid, conv, cid, sid,
        tool_calls=[{"id": "x", "name": "mark_as_lead", "arguments": "{}"}],
        age_seconds=100,
    )

    r = await async_client.get(
        f"/api/v1/campaigns/{cid}/events",
        headers=_auth_headers(valid_supabase_jwt, "u-events-merge"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["type"] for e in body["events"]] == \
        ["lead", "message_failed", "message_sent"]
    assert body["has_more"] is False
    assert body["next_before"] == body["events"][-1]["at"]

    lead, failed, sent = body["events"]
    assert lead["contact_name"] == "Lead Name"
    assert lead["contact_phone"] == "+79990001003"
    assert lead["contact_username"] is None
    assert lead["sender_slug"] == "events-sender-1"

    assert failed["detail"] == "FLOOD_WAIT 30"
    assert failed["contact_name"] == "Fail Name"

    assert sent["detail"] is None
    # result_* fields win over the enqueue-time recipient_name.
    assert sent["contact_name"] == "Resolved Name"
    assert sent["contact_username"] == "resolved_u"


# ── Test 2: jsonb null tool_calls must not crash nor emit events ──────────────


async def test_jsonb_null_tool_calls_is_harmless(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    camp = await test_campaign_factory(name="NullTCCamp")
    sender = await test_sender_factory(slug="events-sender-null")
    await _bind(async_db_session, test_workspace.id, "u-events-null")
    wid, cid, sid = test_workspace.id, camp["id"], sender.id

    conv = await _make_conversation(
        async_db_session, wid, sid, cid, "+79990002001")
    # tool_calls = JSON null (jsonb 'null', not SQL NULL).
    await _seed_llm_call(
        async_db_session, wid, conv, cid, sid, tool_calls=None, age_seconds=10)

    r = await async_client.get(
        f"/api/v1/campaigns/{cid}/events",
        headers=_auth_headers(valid_supabase_jwt, "u-events-null"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["events"] == []
    assert body["has_more"] is False
    assert body["next_before"] is None


# ── Test 3: workspace isolation ───────────────────────────────────────────────


async def test_events_cross_workspace_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    from app.models import Workspace

    camp = await test_campaign_factory(name="IsoCamp")

    other = Workspace(name="OtherEventsWS")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-events-cross")

    r = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/events",
        headers=_auth_headers(valid_supabase_jwt, "u-events-cross"),
    )
    assert r.status_code == 404


# ── Test 4: cursor pagination ─────────────────────────────────────────────────


async def test_events_cursor_pagination(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    camp = await test_campaign_factory(name="PageCamp")
    sender = await test_sender_factory(slug="events-sender-page")
    await _bind(async_db_session, test_workspace.id, "u-events-page")
    wid, cid, sid = test_workspace.id, camp["id"], sender.id

    # 5 sent events with distinct timestamps (10s apart).
    for i in range(5):
        await _seed_queue_event(
            async_db_session, wid, cid, sid, f"+7999000300{i}", "sent",
            age_seconds=10 * (i + 1),
        )

    headers = _auth_headers(valid_supabase_jwt, "u-events-page")

    r1 = await async_client.get(
        f"/api/v1/campaigns/{cid}/events", params={"limit": 2},
        headers=headers,
    )
    assert r1.status_code == 200, r1.text
    p1 = r1.json()
    assert len(p1["events"]) == 2
    assert p1["has_more"] is True
    assert p1["next_before"] is not None

    r2 = await async_client.get(
        f"/api/v1/campaigns/{cid}/events",
        params={"limit": 2, "before": p1["next_before"]},
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    p2 = r2.json()
    assert len(p2["events"]) == 2
    assert p2["has_more"] is True

    r3 = await async_client.get(
        f"/api/v1/campaigns/{cid}/events",
        params={"limit": 2, "before": p2["next_before"]},
        headers=headers,
    )
    assert r3.status_code == 200, r3.text
    p3 = r3.json()
    assert len(p3["events"]) == 1
    assert p3["has_more"] is False

    # No duplicates across pages; all 5 seeded phones covered exactly once.
    phones = [e["contact_phone"]
              for e in p1["events"] + p2["events"] + p3["events"]]
    assert len(phones) == len(set(phones)) == 5


# ── Test 5: D-11 v2 deadline auto-pause surfaces as campaign_paused ───────────


async def test_events_includes_campaign_paused_for_deadline_pause(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """D-11 v2 (deadline-mass-fail fix): a campaign paused with
    pause_reason='past_stop_date' surfaces a synthetic campaign_paused event
    (Source 3) sourced directly from campaigns.paused_at, no contact fields."""
    camp = await test_campaign_factory(name="DeadlinePauseCamp", status="running")
    await _bind(async_db_session, test_workspace.id, "u-events-pause")

    await async_db_session.execute(text("""
        UPDATE campaigns
        SET status = 'paused', pause_reason = 'past_stop_date', paused_at = NOW()
        WHERE id = :cid
    """), {"cid": str(camp["id"])})
    await async_db_session.commit()

    r = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/events",
        headers=_auth_headers(valid_supabase_jwt, "u-events-pause"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["events"]) == 1
    ev = body["events"][0]
    assert ev["type"] == "campaign_paused"
    assert ev["detail"] == "past_stop_date"
    assert ev["contact_name"] is None
    assert ev["contact_phone"] is None


async def test_events_excludes_non_deadline_pause_reason(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """A 029 no-eligible-sender auto-pause (pause_reason != 'past_stop_date')
    must NOT be surfaced as a campaign_paused event — only the deadline one is."""
    camp = await test_campaign_factory(name="OtherPauseCamp", status="running")
    await _bind(async_db_session, test_workspace.id, "u-events-other-pause")

    await async_db_session.execute(text("""
        UPDATE campaigns
        SET status = 'paused', pause_reason = 'senders_unavailable', paused_at = NOW()
        WHERE id = :cid
    """), {"cid": str(camp["id"])})
    await async_db_session.commit()

    r = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/events",
        headers=_auth_headers(valid_supabase_jwt, "u-events-other-pause"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["events"] == []
