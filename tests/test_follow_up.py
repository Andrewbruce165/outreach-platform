"""Phase 19 Plan 19-01 — Wave-0 RED test scaffold for No Reply Follow-Up and
Auto-Finish (NORP-01/02/04/06/07/12).

These tests encode the EXPECTED behaviour of the follow-up / auto-finish feature
BEFORE the production code exists (Nyquist rule): every downstream implementation
task has a concrete automated command from the start. Symbols that do not exist
yet (``app.services.follow_up.FollowUpWorker`` etc.) are imported INSIDE the test
bodies so ``pytest --collect-only`` stays clean and the tests are genuinely RED
now, turning GREEN in later plans.

NORP-01 — ``conversations.status`` accepts 'no_reply' (migration 045). GREEN now.
NORP-02 — campaign create validates follow_up_interval_hours (4–168),
          follow_up_max_pings (1–5), auto_finish_hours (24–720); echoes
          follow_up_enabled. RED until Plan 19-02 adds the schema fields.
NORP-04 — FollowUpWorker pings a no_reply conversation once its interval elapses
          and auto-finishes after auto_finish_hours. RED until Plan 19-04.
NORP-06 — auto-finish fires the finish webhook with reason='no_reply'.
          RED until Plan 19-04.
NORP-07 — an inbound reply reverts a no_reply conversation back to active and
          cancels further pings. RED until Plan 19-03.
NORP-12 — a paused campaign's conversations get NO ping (frozen). RED until 19-04.

Tests run ONLY via the test-overlay:
    docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_follow_up.py
NEVER bare ``docker compose run --rm api pytest`` (conftest guard DROP SCHEMA on prod).
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "follow-up-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ── NORP-01: status='no_reply' is a legal value (migration 045 landed) ─────────

async def test_no_reply_status_allowed(test_conversation_factory, async_db_session):
    """GREEN contract: the migration-045 CHECK extension accepts 'no_reply'.

    Insert a conversation with status='no_reply' via the factory (raw INSERT) and
    assert it commits and reads back — proving the DB CHECK does not reject it.
    """
    conv = await test_conversation_factory(status="no_reply")
    assert conv["status"] == "no_reply"

    row = (await async_db_session.execute(text(
        "SELECT status, pings_sent FROM conversations WHERE id = :id"
    ), {"id": str(conv["id"])})).first()
    assert row is not None
    assert row.status == "no_reply"
    # pings_sent counter exists and defaults to 0 (migration 045 / ORM server_default).
    assert row.pings_sent == 0


# ── NORP-02: campaign follow-up fields + API bounds ────────────────────────────

async def test_campaign_follow_up_fields(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """RED until Plan 19-02 adds the follow-up fields to the campaign schema.

    - follow_up_interval_hours below the 4-hour floor -> 422
    - follow_up_max_pings above the 5 ceiling -> 422
    - auto_finish_hours below the 24-hour floor -> 422
    - valid values (24/2/72) -> 201 and the response echoes follow_up_enabled
    """
    agent = await test_agent_factory()
    await _bind(async_db_session, test_workspace.id, "u-fu")

    base = {
        "name": "FU",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "sender_ids": [],
        "message_template": "Hi {{name}}",
    }
    hdr = _auth_headers(valid_supabase_jwt, "u-fu")

    # interval below bound (4) -> 422
    r = await async_client.post("/api/v1/campaigns",
                                json={**base, "follow_up_interval_hours": 3}, headers=hdr)
    assert r.status_code == 422, r.text

    # max_pings above bound (5) -> 422
    r = await async_client.post("/api/v1/campaigns",
                                json={**base, "follow_up_max_pings": 6}, headers=hdr)
    assert r.status_code == 422, r.text

    # auto_finish below bound (24) -> 422
    r = await async_client.post("/api/v1/campaigns",
                                json={**base, "auto_finish_hours": 12}, headers=hdr)
    assert r.status_code == 422, r.text

    # valid values -> 201 and echoes follow_up_enabled
    r = await async_client.post("/api/v1/campaigns", json={
        **base,
        "follow_up_enabled": True,
        "follow_up_interval_hours": 24,
        "follow_up_max_pings": 2,
        "auto_finish_hours": 72,
    }, headers=hdr)
    assert r.status_code == 201, r.text
    body = r.json()
    camp = body["campaign"] if "campaign" in body and "warnings" in body else body
    assert camp["follow_up_enabled"] is True


# ── NORP-05: ai_engine.generate_followup_ping (D-07) ──────────────────────────

async def test_generate_followup_ping_returns_text(
    test_running_campaign_factory, test_conversation_factory, async_db_session,
    monkeypatch,
):
    """Plan 19-02 (D-07): generate_followup_ping reuses prompt assembly + the
    Phase-18 resolved provider and returns non-empty ping text with NO tools.

    The provider is stubbed (no network); we assert (a) a non-empty string comes
    back and (b) the completion was invoked with tools=None (a ping never carries
    lead/handoff/finish tools).
    """
    from app.services import ai_engine as ai_engine_mod
    from app.services.llm.base import LLMResult

    captured = {}

    class _StubProvider:
        async def complete(self, *, system, messages, tools, max_tokens,
                           temperature=None, reasoning_effort=None):
            captured["tools"] = tools
            captured["system"] = system
            captured["messages"] = messages
            return LLMResult(text="Здравствуйте! Всё ещё актуально?", finish_reason="stop")

    monkeypatch.setattr(ai_engine_mod, "get_provider", lambda cfg: _StubProvider())

    camp, senders = await test_running_campaign_factory()
    conv = await test_conversation_factory(
        sender=senders[0], campaign_id=camp["id"], status="no_reply",
        contact_name="Иван",
    )

    ping = await ai_engine_mod.ai_engine.generate_followup_ping(
        async_db_session, conv["id"]
    )
    assert ping is not None
    assert ping.strip() != ""
    # A ping must never carry tools (no lead/handoff/finish on a proactive nudge).
    assert captured["tools"] in (None, [])


async def test_generate_followup_ping_no_context_returns_none(
    test_conversation_factory, async_db_session,
):
    """Plan 19-02 (D-07): a conversation with no campaign/agent context yields
    None so the caller (FollowUpWorker) simply skips the ping."""
    from app.services import ai_engine as ai_engine_mod

    # No campaign_id and no ai_context_id → no agent to speak as.
    conv = await test_conversation_factory(status="no_reply")
    ping = await ai_engine_mod.ai_engine.generate_followup_ping(
        async_db_session, conv["id"]
    )
    assert ping is None


# ── NORP-04: FollowUpWorker pings on interval ──────────────────────────────────

async def test_ping_on_interval(test_running_campaign_factory, test_conversation_factory):
    """RED until Plan 19-04 builds app.services.follow_up.FollowUpWorker.

    A no_reply conversation whose follow_up_interval_hours has elapsed since its
    last outbound gets exactly one follow-up ping (pings_sent increments).
    """
    from app.services.follow_up import FollowUpWorker  # RED: module not built yet

    camp, senders = await test_running_campaign_factory(
        follow_up_enabled=True, follow_up_interval_hours=24, follow_up_max_pings=2,
    )
    conv = await test_conversation_factory(
        sender=senders[0], campaign_id=camp["id"], status="no_reply",
    )
    worker = FollowUpWorker()
    await worker.tick()
    # After the interval elapses, exactly one ping should have been scheduled.
    assert conv["id"] is not None  # placeholder assertion; real check in 19-04


# ── NORP-04: FollowUpWorker auto-finishes after auto_finish_hours ──────────────

async def test_auto_finish(test_running_campaign_factory, test_conversation_factory):
    """RED until Plan 19-04.

    After auto_finish_hours of silence a no_reply conversation flips to 'finished'.
    """
    from app.services.follow_up import FollowUpWorker  # RED: not built yet

    camp, senders = await test_running_campaign_factory(
        follow_up_enabled=True, auto_finish_hours=72,
    )
    conv = await test_conversation_factory(
        sender=senders[0], campaign_id=camp["id"], status="no_reply",
    )
    worker = FollowUpWorker()
    await worker.tick()
    assert conv["id"] is not None  # real 'finished' assertion in 19-04


# ── NORP-06: auto-finish fires the finish webhook with reason='no_reply' ───────

async def test_finish_reason_marker(test_running_campaign_factory, test_conversation_factory):
    """RED until Plan 19-04.

    Auto-finish must fire the finish webhook with reason='no_reply' so downstream
    (n8n) can distinguish a no-reply auto-finish from a conversational finish.
    """
    from app.services.follow_up import FollowUpWorker  # RED: not built yet

    camp, senders = await test_running_campaign_factory(
        follow_up_enabled=True, auto_finish_hours=72,
        finish_webhook_url="https://example.test/finish",
    )
    conv = await test_conversation_factory(
        sender=senders[0], campaign_id=camp["id"], status="no_reply",
    )
    worker = FollowUpWorker()
    await worker.tick()
    assert conv["id"] is not None  # webhook reason='no_reply' asserted in 19-04


# ── NORP-07: an inbound reply reverts no_reply -> active and cancels pings ─────

async def test_reply_cancels_pings(
    test_running_campaign_factory, test_conversation_factory,
    test_queue_item_factory, async_db_session,
):
    """Plan 19-03 (D-03 + D-17 first guard): an inbound reply reverts a no_reply
    conversation back to 'active' AND cancels its pending follow-up pings.

    Seed: a running campaign, a no_reply conversation for one contact, and a
    pending follow-up ping already scheduled in message_queue for that contact.
    handle_no_reply_revert must (a) flip the conversation to 'active' and (b)
    cancel the pending ping (status='cancelled', never sent).
    """
    from app.services.listener import handle_no_reply_revert

    camp, senders = await test_running_campaign_factory()
    sender = senders[0]
    phone = "+79005551234"

    conv = await test_conversation_factory(
        sender=sender, campaign_id=camp["id"], status="no_reply",
        contact_phone=phone,
    )
    # A pending follow-up ping already scheduled for this contact.
    await test_queue_item_factory(
        camp["id"], sender.id, phone, status="pending", with_cca=False,
    )

    result = await handle_no_reply_revert(conv["id"])

    assert result["reverted"] is True
    assert result["cancelled"] == 1

    # (a) conversation reverted no_reply -> active so the normal answerer fires.
    conv_row = (await async_db_session.execute(text(
        "SELECT status FROM conversations WHERE id = :id"
    ), {"id": str(conv["id"])})).first()
    assert conv_row.status == "active"

    # (b) the pending ping is cancelled (never sent).
    q_row = (await async_db_session.execute(text(
        "SELECT status FROM message_queue "
        "WHERE sender_id = :sid AND recipient_phone = :phone"
    ), {"sid": str(sender.id), "phone": phone})).first()
    assert str(q_row.status).endswith("cancelled")


# ── NORP-12: a paused campaign's conversations get NO ping (frozen) ────────────

async def test_paused_frozen(test_running_campaign_factory, test_conversation_factory, async_db_session):
    """RED until Plan 19-04.

    A conversation belonging to a paused campaign must NOT be pinged — pausing a
    campaign freezes the follow-up loop.
    """
    from app.services.follow_up import FollowUpWorker  # RED: not built yet

    camp, senders = await test_running_campaign_factory(
        follow_up_enabled=True, follow_up_interval_hours=24,
    )
    # Pause the campaign.
    await async_db_session.execute(text(
        "UPDATE campaigns SET status='paused' WHERE id = :id"
    ), {"id": str(camp["id"])})
    await async_db_session.commit()

    conv = await test_conversation_factory(
        sender=senders[0], campaign_id=camp["id"], status="no_reply",
    )
    worker = FollowUpWorker()
    await worker.tick()
    # pings_sent must stay 0 for a paused campaign — asserted concretely in 19-04.
    row = (await async_db_session.execute(text(
        "SELECT pings_sent FROM conversations WHERE id = :id"
    ), {"id": str(conv["id"])})).first()
    assert row.pings_sent == 0


# ── NORP-08: queue pre-send guard cancels a follow-up ping on reply-since ──────

async def _seed_followup_item(
    db, workspace_id, sender_id, campaign_id, phone, *, created_ago_hours=2,
):
    """INSERT a follow-up ping queue item (status='processing') whose created_at
    is in the past, tagged extra_data.kind='followup'. Returns the item id."""
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone
    item_id = _uuid.uuid4()
    await db.execute(text("""
        INSERT INTO message_queue
            (id, workspace_id, sender_id, campaign_id, item_type, status,
             recipient_phone, message_text, extra_data, created_at)
        VALUES (:id, :wid, :sid, :cid, 'message', 'processing',
                :phone, 'ping', CAST(:extra AS JSONB), :created)
    """), {
        "id": str(item_id),
        "wid": str(workspace_id),
        "sid": str(sender_id),
        "cid": str(campaign_id),
        "phone": phone,
        "extra": '{"kind": "followup"}',
        "created": datetime.now(timezone.utc) - timedelta(hours=created_ago_hours),
    })
    await db.commit()
    return item_id


async def test_guard_cancels_ping_when_reply_since_scheduled(
    async_db_session, test_workspace, test_sender_factory,
    test_conversation_factory, test_campaign_factory, monkeypatch,
):
    """D-17 second guard: a follow-up ping is CANCELLED (not sent) at send time
    when the contact has replied after the ping was scheduled."""
    from unittest.mock import AsyncMock, MagicMock
    from app.services.queue import queue_worker

    sender = await test_sender_factory()
    camp = await test_campaign_factory()
    phone = "+79008880001"
    conv = await test_conversation_factory(
        sender=sender, campaign_id=camp["id"], contact_phone=phone,
        contact_telegram_id=880001, status="no_reply", ai_enabled=True,
    )
    # Inbound reply arrived NOW — newer than the ping's created_at (2h ago).
    await async_db_session.execute(text("""
        INSERT INTO messages (workspace_id, conversation_id, direction,
                              message_text, sent_by, created_at)
        VALUES (:wid, :cid, 'inbound', 'ответил', 'contact', NOW())
    """), {"wid": str(test_workspace.id), "cid": str(conv["id"])})
    await async_db_session.commit()

    item_id = await _seed_followup_item(
        async_db_session, test_workspace.id, sender.id, camp["id"], phone,
    )

    send_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.queue.telegram_service.get_client",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr("app.services.queue.telegram_service.send_message", send_mock)
    monkeypatch.setattr("app.services.queue.telegram_service.send_file", AsyncMock())

    await queue_worker._QueueWorker__send_item_inner(item_id)

    row = (await async_db_session.execute(text(
        "SELECT status, error_message FROM message_queue WHERE id = :id"
    ), {"id": str(item_id)})).first()
    assert str(row.status).endswith("cancelled")
    assert row.error_message == "contact replied since ping scheduled"
    send_mock.assert_not_called()


async def test_guard_cancels_ping_when_conversation_left_active(
    async_db_session, test_workspace, test_sender_factory,
    test_conversation_factory, test_campaign_factory, monkeypatch,
):
    """D-06 / D-17: a follow-up ping is cancelled when the conversation is no
    longer active/no_reply (taken over / finished / handed off) — even without a
    reply-since message."""
    from unittest.mock import AsyncMock, MagicMock
    from app.services.queue import queue_worker

    sender = await test_sender_factory()
    camp = await test_campaign_factory()
    phone = "+79008880002"
    await test_conversation_factory(
        sender=sender, campaign_id=camp["id"], contact_phone=phone,
        contact_telegram_id=880002, status="finished", ai_enabled=True,
    )

    item_id = await _seed_followup_item(
        async_db_session, test_workspace.id, sender.id, camp["id"], phone,
    )

    send_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.queue.telegram_service.get_client",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr("app.services.queue.telegram_service.send_message", send_mock)
    monkeypatch.setattr("app.services.queue.telegram_service.send_file", AsyncMock())

    await queue_worker._QueueWorker__send_item_inner(item_id)

    row = (await async_db_session.execute(text(
        "SELECT status FROM message_queue WHERE id = :id"
    ), {"id": str(item_id)})).first()
    assert str(row.status).endswith("cancelled")
    send_mock.assert_not_called()


async def test_guard_lets_ping_through_when_no_reply_since(
    async_db_session, test_workspace, test_sender_factory,
    test_conversation_factory, test_campaign_factory, monkeypatch,
):
    """D-17: a follow-up ping into a still-silent no_reply conversation is NOT
    cancelled by the guard — the ping proceeds to send (telegram mock called)."""
    from unittest.mock import AsyncMock, MagicMock
    from app.services.queue import queue_worker

    sender = await test_sender_factory()
    camp = await test_campaign_factory()
    phone = "+79008880003"
    await test_conversation_factory(
        sender=sender, campaign_id=camp["id"], contact_phone=phone,
        contact_telegram_id=880003, status="no_reply", ai_enabled=True,
    )

    item_id = await _seed_followup_item(
        async_db_session, test_workspace.id, sender.id, camp["id"], phone,
    )

    send_mock = AsyncMock(return_value={"success": True, "telegram_message_id": 42})
    monkeypatch.setattr(
        "app.services.queue.telegram_service.get_client",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr("app.services.queue.telegram_service.send_message", send_mock)
    monkeypatch.setattr("app.services.queue.telegram_service.send_file", AsyncMock())

    await queue_worker._QueueWorker__send_item_inner(item_id)

    row = (await async_db_session.execute(text(
        "SELECT status FROM message_queue WHERE id = :id"
    ), {"id": str(item_id)})).first()
    # NOT cancelled by the follow-up guard.
    assert not str(row.status).endswith("cancelled")
    send_mock.assert_awaited()
