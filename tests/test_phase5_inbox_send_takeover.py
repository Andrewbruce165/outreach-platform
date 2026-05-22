"""Phase 5 POST /conversations/{id}/send — auto-takeover (INBX-04, D-04).

Covers:
- Happy path: telegram mock OK → conversation flipped to manual, message saved
- Sender inactive (lifecycle_status/auth_status off) → 404, Telethon NOT called
- Cross-workspace → 404 ДО любого Telegram-вызова
- Race protection: when /send fires, pre-existing pending queue items
  for that recipient_phone are flipped to status='failed' (D-02 pattern).
  The queue-worker pre-send guard tested separately in
  tests/test_phase5_bot_filter.py — this file checks the router side.
"""

import uuid as _uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "send-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ── Test 11: happy path — Telethon mock OK → auto-takeover state ─────────────


async def test_send_happy_path_flips_to_manual(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory, monkeypatch,
):
    """POST /send with success mock:
       - conversation.status='manual', ai_enabled=false, paused_reason='Manager sent message via UI'
       - new message row with sent_by='human', direction='outbound'
       - response success=True with telegram_message_id
    """
    sender = await test_sender_factory()
    conv = await test_conversation_factory(
        sender=sender,
        contact_phone="+79991201001",
        contact_telegram_id=987654321,
        status="active",
        ai_enabled=True,
    )

    send_mock = AsyncMock(
        return_value={"success": True, "telegram_message_id": 555000}
    )
    monkeypatch.setattr(
        "app.services.telegram.send_message_by_telegram_id", send_mock
    )

    await _bind(async_db_session, test_workspace.id, "u-send-happy")

    r = await async_client.post(
        f"/api/v1/conversations/{conv['id']}/send",
        json={"message": "Привет от менеджера"},
        headers=_auth_headers(valid_supabase_jwt, "u-send-happy"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["telegram_message_id"] == 555000

    send_mock.assert_awaited_once()

    conv_row = (await async_db_session.execute(text("""
        SELECT status, ai_enabled, paused_reason
        FROM conversations WHERE id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert conv_row.status == "manual"
    assert conv_row.ai_enabled is False
    assert conv_row.paused_reason == "Manager sent message via UI"

    msg_row = (await async_db_session.execute(text("""
        SELECT direction, sent_by, telegram_message_id, message_text
        FROM messages WHERE conversation_id = :cid
        ORDER BY created_at DESC LIMIT 1
    """), {"cid": str(conv["id"])})).first()
    assert msg_row.direction == "outbound"
    assert msg_row.sent_by == "human"
    assert msg_row.telegram_message_id == 555000
    assert msg_row.message_text == "Привет от менеджера"


# ── Test 12: sender inactive → 404, Telethon NOT called ──────────────────────


@pytest.mark.parametrize(
    "lifecycle,auth_status",
    [("paused", "ok"), ("active", "session_expired"), ("warmup", "ok")],
)
async def test_send_rejects_inactive_sender(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory, monkeypatch,
    lifecycle, auth_status,
):
    """sender.lifecycle_status != 'active' OR auth_status != 'ok' → 404."""
    sender = await test_sender_factory(
        lifecycle_status=lifecycle, auth_status=auth_status,
    )
    conv = await test_conversation_factory(
        sender=sender, contact_phone="+79991202001",
        contact_telegram_id=987654322,
    )

    send_mock = AsyncMock(
        return_value={"success": True, "telegram_message_id": 1}
    )
    monkeypatch.setattr(
        "app.services.telegram.send_message_by_telegram_id", send_mock
    )

    uid = f"u-send-inactive-{lifecycle}-{auth_status}"
    await _bind(async_db_session, test_workspace.id, uid)

    r = await async_client.post(
        f"/api/v1/conversations/{conv['id']}/send",
        json={"message": "Hi"},
        headers=_auth_headers(valid_supabase_jwt, uid),
    )
    assert r.status_code == 404
    send_mock.assert_not_called()


# ── Test 13: cross-workspace → 404 BEFORE Telethon call ──────────────────────


async def test_send_cross_workspace_blocked(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, monkeypatch,
):
    """User of workspace A POST /send для conversation workspace B → 404."""
    from app.models import Workspace

    conv = await test_conversation_factory(
        contact_phone="+79991203001", contact_telegram_id=987654323,
    )

    send_mock = AsyncMock(
        return_value={"success": True, "telegram_message_id": 1}
    )
    monkeypatch.setattr(
        "app.services.telegram.send_message_by_telegram_id", send_mock
    )

    other = Workspace(name="OtherSendXws")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-send-xws")

    r = await async_client.post(
        f"/api/v1/conversations/{conv['id']}/send",
        json={"message": "from other"},
        headers=_auth_headers(valid_supabase_jwt, "u-send-xws"),
    )
    assert r.status_code == 404
    send_mock.assert_not_called()


# ── Test 19: D-02 race — pending queue items flipped before send ─────────────


async def test_send_flips_pending_queue_to_failed(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory, test_campaign_factory,
    monkeypatch,
):
    """POST /send: pre-existing pending queue items for that recipient_phone
    are flipped to status='failed' with error 'Conversation taken over manually'
    BEFORE the Telethon mock is invoked.

    Demonstrates the router side of the D-04 race protection. The worker side
    is exercised in tests/test_phase5_bot_filter.py::test_pre_send_guard_*.
    """
    camp = await test_campaign_factory()
    sender = await test_sender_factory()
    conv = await test_conversation_factory(
        sender=sender, contact_phone="+79991204001",
        contact_telegram_id=987654324, campaign_id=camp["id"],
    )

    target_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO message_queue
            (id, workspace_id, sender_id, campaign_id, item_type, status,
             recipient_phone, message_text)
        VALUES (:tid, :wid, :sid, :cid, 'message', 'pending',
                '+79991204001', 'auto-queue-msg')
    """), {
        "tid": str(target_id), "wid": str(test_workspace.id),
        "sid": str(sender.id), "cid": str(camp["id"]),
    })
    await async_db_session.commit()

    send_mock = AsyncMock(
        return_value={"success": True, "telegram_message_id": 999}
    )
    monkeypatch.setattr(
        "app.services.telegram.send_message_by_telegram_id", send_mock
    )

    await _bind(async_db_session, test_workspace.id, "u-send-race")

    r = await async_client.post(
        f"/api/v1/conversations/{conv['id']}/send",
        json={"message": "human reply"},
        headers=_auth_headers(valid_supabase_jwt, "u-send-race"),
    )
    assert r.status_code == 200

    row = (await async_db_session.execute(text("""
        SELECT status, error_message FROM message_queue WHERE id = :id
    """), {"id": str(target_id)})).first()
    assert row.status == "failed"
    assert row.error_message == "Conversation taken over manually"
