"""Batch F — POST /api/v1/send hardening (quick-260704-d64).

Regression coverage for three production defects surfaced in the 2026-07-03
checker/campaigns review:

- WR-10 — recipient_phone was passed through UNNORMALIZED, forking the pipeline
  identity key (`+E164` / `@handle`) across CCA / message_queue / conversations /
  contacts_cache. `/send` now normalizes to a local recipient_key: phones → E.164,
  `@username` passes through unchanged, garbage → 422 INVALID_PHONE.
- WR-11(a) — a push into a non-running campaign (draft/paused/done) sat pending
  forever. `/send` now rejects with 409 CAMPAIGN_NOT_RUNNING (detail.status).
- WR-11(b) — an n8n timeout→replay double-sent the same opener. `/send` is now
  idempotent: a duplicate (campaign, recipient) returns the EXISTING
  pending/processing queue row (200), no second message_queue insert. Dedup keys
  off the normalized recipient_key.
- IN-09 — an explicit sender_slug for a spam_limited/frozen (but active+auth-ok)
  sender used to return 200. `/send` now rejects 409 SENDER_NOT_READY with
  detail.restriction_status.

Isolated in its own file (not tests/test_send.py) to avoid merge conflicts with
parallel agents. Fixtures mirror the interfaces block in the plan.
"""
import pytest
from uuid import uuid4
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _link_user_to_workspace(db, user_sub, workspace_id):
    from app.models import UserWorkspace
    db.add(UserWorkspace(supabase_user_id=user_sub, workspace_id=workspace_id, role="owner"))
    await db.commit()


# ─── WR-10: phone normalization ──────────────────────────────────────────────

async def test_wr10_normalizes_phone_to_e164(
    async_client, async_db_session, valid_supabase_jwt, test_workspace, test_running_campaign_factory,
):
    """WR-10: an unnormalized RU legacy phone ('89001234567') is enqueued under the
    normalized E.164 key ('+79001234567') — identity no longer forks vs CSV/UI."""
    user_sub = f"user-wr10-norm-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    camp, senders = await test_running_campaign_factory(sender_count=1)
    token = valid_supabase_jwt(sub=user_sub)

    resp = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "sender_slug": senders[0].slug,
            "recipient_phone": "89001234567",
            "message": "hi",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    queue_id = resp.json()["queue_id"]

    row = (await async_db_session.execute(
        text("SELECT recipient_phone FROM message_queue WHERE id = :qid"),
        {"qid": queue_id},
    )).first()
    assert row is not None, "enqueue_message commits, so the row must be visible cross-session"
    assert row.recipient_phone == "+79001234567", (
        f"phone must be normalized to E.164, got {row.recipient_phone!r}"
    )


async def test_wr10_invalid_phone_rejected_422(
    async_client, async_db_session, valid_supabase_jwt, test_workspace, test_running_campaign_factory,
):
    """WR-10: a garbage phone ('abc') is rejected 422 INVALID_PHONE."""
    user_sub = f"user-wr10-invalid-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    camp, senders = await test_running_campaign_factory(sender_count=1)
    token = valid_supabase_jwt(sub=user_sub)

    resp = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "sender_slug": senders[0].slug,
            "recipient_phone": "abc",
            "message": "hi",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "INVALID_PHONE"


async def test_wr10_username_passthrough(
    async_client, async_db_session, valid_supabase_jwt, test_workspace, test_running_campaign_factory,
):
    """WR-10: an @username key passes through unchanged (still enqueues, key preserved)."""
    user_sub = f"user-wr10-uname-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    camp, senders = await test_running_campaign_factory(sender_count=1)
    token = valid_supabase_jwt(sub=user_sub)

    resp = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "sender_slug": senders[0].slug,
            "recipient_phone": "@somehandle",
            "message": "hi",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    queue_id = resp.json()["queue_id"]

    row = (await async_db_session.execute(
        text("SELECT recipient_phone FROM message_queue WHERE id = :qid"),
        {"qid": queue_id},
    )).first()
    assert row.recipient_phone == "@somehandle", (
        f"username key must pass through unchanged, got {row.recipient_phone!r}"
    )


# ─── WR-11(a): campaign-status guard ─────────────────────────────────────────

async def test_wr11a_draft_campaign_rejected_409(
    async_client, async_db_session, valid_supabase_jwt, test_workspace, test_running_campaign_factory,
):
    """WR-11(a): a push into a draft campaign is rejected 409 CAMPAIGN_NOT_RUNNING
    (detail.status='draft'). test_running_campaign_factory(status='draft') yields a
    DRAFT campaign that still has senders attached."""
    user_sub = f"user-wr11a-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    camp, senders = await test_running_campaign_factory(status="draft", sender_count=1)
    token = valid_supabase_jwt(sub=user_sub)

    resp = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "sender_slug": senders[0].slug,
            "recipient_phone": "+79991234567",
            "message": "hi",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "CAMPAIGN_NOT_RUNNING"
    assert detail["status"] == "draft"


# ─── WR-11(b): idempotent replay dedup ───────────────────────────────────────

async def test_wr11b_idempotent_replay_returns_existing(
    async_client, async_db_session, valid_supabase_jwt, test_workspace, test_running_campaign_factory,
):
    """WR-11(b): a duplicate /send for the same (campaign, recipient) returns the
    EXISTING pending/processing queue row (200, idempotent) — no second row created."""
    user_sub = f"user-wr11b-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    camp, senders = await test_running_campaign_factory(sender_count=1)
    token = valid_supabase_jwt(sub=user_sub)
    payload = {
        "campaign_id": str(camp["id"]),
        "sender_slug": senders[0].slug,
        "recipient_phone": "+79991234567",
        "message": "hi",
    }

    r1 = await async_client.post(
        "/api/v1/send", json=payload, headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200, r1.text
    queue_id_1 = r1.json()["queue_id"]

    r2 = await async_client.post(
        "/api/v1/send", json=payload, headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["queue_id"] == queue_id_1, "idempotent replay must return the existing row"

    count = (await async_db_session.execute(
        text("""
            SELECT COUNT(*) FROM message_queue
            WHERE campaign_id = :cid AND recipient_phone = '+79991234567'
              AND status IN ('pending', 'processing')
        """),
        {"cid": str(camp["id"])},
    )).scalar()
    assert count == 1, f"exactly one queue row must exist, got {count}"


async def test_wr11b_dedup_keys_off_normalized_recipient_key(
    async_client, async_db_session, valid_supabase_jwt, test_workspace, test_running_campaign_factory,
):
    """WR-11(b) + WR-10 interplay: a first push with '+79001234567' and a second push
    with '89001234567' (same human, un-normalized) return the SAME queue row — proving
    dedup keys off the normalized recipient_key, not the raw input."""
    user_sub = f"user-wr11b-interplay-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    camp, senders = await test_running_campaign_factory(sender_count=1)
    token = valid_supabase_jwt(sub=user_sub)

    r1 = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "sender_slug": senders[0].slug,
            "recipient_phone": "+79001234567",
            "message": "hi",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200, r1.text
    queue_id_1 = r1.json()["queue_id"]

    r2 = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "sender_slug": senders[0].slug,
            "recipient_phone": "89001234567",  # same human, un-normalized
            "message": "hi",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["queue_id"] == queue_id_1, (
        "dedup must key off the normalized recipient_key — the un-normalized replay "
        "must resolve to the same existing row"
    )


# ─── IN-09: restriction-aware explicit-sender readiness ──────────────────────

async def test_in09_spam_limited_sender_rejected_409(
    async_client, async_db_session, valid_supabase_jwt, test_workspace,
    test_sender_factory, test_campaign_factory, attach_sender_to_campaign,
):
    """IN-09: an explicit sender_slug for a spam_limited (but active + auth-ok) sender
    is rejected 409 SENDER_NOT_READY, detail.restriction_status='spam_limited'."""
    user_sub = f"user-in09-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    sender = await test_sender_factory(restriction_status="spam_limited")
    camp = await test_campaign_factory(status="running")
    await attach_sender_to_campaign(camp["id"], sender.id)
    token = valid_supabase_jwt(sub=user_sub)

    resp = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "sender_slug": sender.slug,
            "recipient_phone": "+79991234567",
            "message": "hi",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "SENDER_NOT_READY"
    assert detail["restriction_status"] == "spam_limited"
