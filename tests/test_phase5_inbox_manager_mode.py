"""Phase 5 manager-mode endpoints (INBX-04, D-01..D-03).

POST /disable-ai → status='manual', ai_enabled=false, paused_reason set,
                   cancels pending queue items (D-01 + D-02).
POST /enable-ai  → ai_enabled=true, paused_at=NULL, paused_reason=NULL,
                   'manual'/'handoff' → 'active' (manager-takeover states
                   reversed); lead/finished/bot_ignored preserved (D-03).
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "manager-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ── Test 9: disable-ai cancels pending queue items (D-01 + D-02) ─────────────


async def test_disable_ai_cancels_pending_queue(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory, test_campaign_factory,
):
    """POST /disable-ai:
       - sets conversation ai_enabled=false, status='manual', paused_at NOT NULL
       - sets pending message_queue items (same recipient_phone + workspace_id)
         to status='failed' with error_message='Conversation taken over manually'
    """
    camp = await test_campaign_factory()
    sender = await test_sender_factory()
    conv = await test_conversation_factory(
        sender=sender, contact_phone="+79991901001", campaign_id=camp["id"]
    )

    # Seed pending queue items: one matches the conversation phone, one decoy.
    target_id = _uuid.uuid4()
    decoy_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO message_queue
            (id, workspace_id, sender_id, campaign_id, item_type, status,
             recipient_phone, message_text)
        VALUES
            (:tid, :wid, :sid, :cid, 'message', 'pending', '+79991901001', 'hello'),
            (:did, :wid, :sid, :cid, 'message', 'pending', '+79990000099', 'decoy')
    """), {
        "tid": str(target_id), "did": str(decoy_id),
        "wid": str(test_workspace.id), "sid": str(sender.id),
        "cid": str(camp["id"]),
    })
    await async_db_session.commit()

    await _bind(async_db_session, test_workspace.id, "u-dis-ai")

    r = await async_client.post(
        f"/api/v1/conversations/{conv['id']}/disable-ai",
        headers=_auth_headers(valid_supabase_jwt, "u-dis-ai"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai_enabled"] is False
    assert body["status"] == "manual"
    assert body["paused_at"] is not None
    assert body["paused_reason"] == "Manager took over"

    target_row = (await async_db_session.execute(text("""
        SELECT status, error_message FROM message_queue WHERE id = :id
    """), {"id": str(target_id)})).first()
    assert target_row.status == "failed"
    assert target_row.error_message == "Conversation taken over manually"

    decoy_row = (await async_db_session.execute(text("""
        SELECT status FROM message_queue WHERE id = :id
    """), {"id": str(decoy_id)})).first()
    assert decoy_row.status == "pending"


# ── Test 10: enable-ai preserves historic status (D-03) ──────────────────────


# enable-ai reverses manager-takeover states ('manual' AND 'handoff' → 'active')
# but preserves genuine conversation outcomes. 'manual' and 'handoff' both mean
# "AI paused, human handling" — an explicit switch-back-to-AI must resume the
# listener (gated on status=='active') and clear the handoff badge. lead/
# finished/bot_ignored are outcomes and stay put so they are never destroyed.
@pytest.mark.parametrize("initial_status,expected_status", [
    ("lead", "lead"),
    ("handoff", "active"),  # AI auto-transfer reversed — else bot stays mute + badge sticks
    ("finished", "finished"),
    ("bot_ignored", "bot_ignored"),
    ("manual", "active"),  # takeover reversed — else the bot stays mute (listener gate)
])
async def test_enable_ai_reverses_manual_preserves_others(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, initial_status, expected_status,
):
    """enable-ai: 'manual'/'handoff' → 'active' (undo takeover so the listener
    resumes AI and the badge clears), outcome statuses preserved; ai_enabled=true
    and paused fields cleared."""
    conv = await test_conversation_factory(
        contact_phone=f"+7999{abs(hash(initial_status)) % 10_000_000:07d}",
        status=initial_status, ai_enabled=False,
    )
    # Set paused_at + paused_reason to verify they're cleared.
    await async_db_session.execute(text("""
        UPDATE conversations
        SET paused_at = NOW(), paused_reason = 'previous reason'
        WHERE id = :cid
    """), {"cid": str(conv["id"])})
    await async_db_session.commit()

    uid = f"u-ena-{initial_status}"
    await _bind(async_db_session, test_workspace.id, uid)

    r = await async_client.post(
        f"/api/v1/conversations/{conv['id']}/enable-ai",
        headers=_auth_headers(valid_supabase_jwt, uid),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai_enabled"] is True
    assert body["status"] == expected_status
    assert body["paused_at"] is None
    assert body["paused_reason"] is None


# ── Test 11: mark-lead sets status='lead' without touching ai_enabled ─────────


async def test_mark_lead_sets_status_and_keeps_ai_enabled(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory, test_campaign_factory,
):
    """POST /mark-lead on an active conversation:
       - returns 200 with body.status == 'lead'
       - body.ai_enabled unchanged (still True — lead is a marker, the
         conversation continues, mirroring the auto-lead flow)
       - no httpx mock needed: the factory campaign has no webhook URL so
         notify_signal short-circuits before any HTTP call.
    """
    camp = await test_campaign_factory()
    sender = await test_sender_factory()
    conv = await test_conversation_factory(
        sender=sender, contact_phone="+79991902002", campaign_id=camp["id"],
        status="active", ai_enabled=True,
    )

    await _bind(async_db_session, test_workspace.id, "u-mark-lead")

    r = await async_client.post(
        f"/api/v1/conversations/{conv['id']}/mark-lead",
        headers=_auth_headers(valid_supabase_jwt, "u-mark-lead"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "lead"
    assert body["ai_enabled"] is True


async def test_mark_lead_cross_workspace_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """POST /mark-lead on an unknown/cross-workspace id → 404 CONVERSATION_NOT_FOUND."""
    await _bind(async_db_session, test_workspace.id, "u-mark-lead-404")
    r = await async_client.post(
        f"/api/v1/conversations/{_uuid.uuid4()}/mark-lead",
        headers=_auth_headers(valid_supabase_jwt, "u-mark-lead-404"),
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "CONVERSATION_NOT_FOUND"


# ── Test 12: finish sets status='finished' AND turns AI off ───────────────────


async def test_finish_sets_status_and_disables_ai(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory, test_campaign_factory,
):
    """POST /finish on an active conversation:
       - returns 200 with body.status == 'finished'
       - body.ai_enabled is False (finishing ENDS the conversation — AI off,
         mirroring the auto finish_conversation flow, unlike mark-lead)
       - body.paused_reason == 'Finished manually via UI'
       - no httpx mock needed: the factory campaign has no webhook URL so
         notify_signal short-circuits before any HTTP call.
    """
    camp = await test_campaign_factory()
    sender = await test_sender_factory()
    conv = await test_conversation_factory(
        sender=sender, contact_phone="+79991902003", campaign_id=camp["id"],
        status="active", ai_enabled=True,
    )

    await _bind(async_db_session, test_workspace.id, "u-finish")

    r = await async_client.post(
        f"/api/v1/conversations/{conv['id']}/finish",
        headers=_auth_headers(valid_supabase_jwt, "u-finish"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "finished"
    assert body["ai_enabled"] is False
    assert body["paused_reason"] == "Finished manually via UI"


async def test_finish_cross_workspace_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """POST /finish on an unknown/cross-workspace id → 404 CONVERSATION_NOT_FOUND."""
    await _bind(async_db_session, test_workspace.id, "u-finish-404")
    r = await async_client.post(
        f"/api/v1/conversations/{_uuid.uuid4()}/finish",
        headers=_auth_headers(valid_supabase_jwt, "u-finish-404"),
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "CONVERSATION_NOT_FOUND"
