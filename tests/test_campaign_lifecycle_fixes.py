"""Batch E (quick 260704-buq) — campaigns router + schema lifecycle fixes.

Covers:
  * WR-12b — POST /campaigns/{id}/requeue-failed re-pends failed rows + returns
    {requeued_count}; GET /campaigns/{id} carries failed_count.
  * IN-05  — attach only checks the newly-attached sender's lock (a pre-existing
    pool member locked elsewhere must not block a conflict-free attach); a genuine
    conflict on the attached sender still 409s.
  * IN-06  — duplicate_campaign translates a unique-index IntegrityError into 409.
  * IN-10  — pool_health.active excludes session_expired / lifecycle-paused senders.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Campaign

pytestmark = pytest.mark.asyncio


async def _warm_for_attach(db, ws_id, sender_id, messages=150):
    """Seed warmup so the sender clears the hard WARMUP_COLD attach gate (>=100 msgs)."""
    from sqlalchemy import text as _t
    await db.execute(_t("""
        INSERT INTO warmup_sessions
            (workspace_id, sender_a_id, sender_b_id, topic, status,
             messages_sent, target_messages)
        VALUES (:wid, :sid, :sid, 'warm', 'done', :msgs, :msgs)
    """), {"wid": str(ws_id), "sid": str(sender_id), "msgs": messages})
    await db.commit()



def _auth_headers(jwt_factory, sub: str = "lifecycle-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def _seed_failed_item(db, wid, cid, sid, phone):
    await db.execute(text("""
        INSERT INTO message_queue (workspace_id, campaign_id, sender_id, item_type,
            status, recipient_phone, message_text, error_message, finished_at,
            attempts, scheduled_at)
        VALUES (:wid, :cid, :sid, 'message', 'failed', :phone, 'x', 'boom', NOW(),
                3, NOW())
    """), {"wid": str(wid), "cid": str(cid), "sid": str(sid), "phone": phone})
    await db.commit()


# ─── WR-12b: requeue-failed + failed_count ───────────────────────────────────


async def test_requeue_failed_repends_items(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    """WR-12b: POST /requeue-failed re-pends all failed rows (status='pending',
    attempts=0, error/finished cleared) and returns {requeued_count}."""
    await _bind(async_db_session, test_workspace.id, "u-requeue")
    camp = await test_campaign_factory(status="running")
    sender = await test_sender_factory()
    await _seed_failed_item(async_db_session, test_workspace.id, camp["id"], sender.id, "+79990001001")
    await _seed_failed_item(async_db_session, test_workspace.id, camp["id"], sender.id, "+79990001002")

    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/requeue-failed",
        headers=_auth_headers(valid_supabase_jwt, "u-requeue"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["requeued_count"] == 2

    rows = (await async_db_session.execute(text("""
        SELECT status, attempts, error_message, finished_at
        FROM message_queue WHERE campaign_id = :cid
    """), {"cid": str(camp["id"])})).fetchall()
    assert len(rows) == 2
    assert all(row.status == "pending" for row in rows)
    assert all(row.attempts == 0 for row in rows)
    assert all(row.error_message is None for row in rows)
    assert all(row.finished_at is None for row in rows)


async def test_requeue_failed_foreign_campaign_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """WR-12b: workspace-scoped — a campaign not in the caller's workspace 404s."""
    await _bind(async_db_session, test_workspace.id, "u-requeue-404")
    from uuid import uuid4
    r = await async_client.post(
        f"/api/v1/campaigns/{uuid4()}/requeue-failed",
        headers=_auth_headers(valid_supabase_jwt, "u-requeue-404"),
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "CAMPAIGN_NOT_FOUND"


async def test_get_campaign_carries_failed_count(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    """WR-12b: GET /campaigns/{id} carries failed_count = COUNT of failed rows."""
    await _bind(async_db_session, test_workspace.id, "u-fc")
    camp = await test_campaign_factory(status="running")
    sender = await test_sender_factory()
    await _seed_failed_item(async_db_session, test_workspace.id, camp["id"], sender.id, "+79990002001")

    r = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}",
        headers=_auth_headers(valid_supabase_jwt, "u-fc"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["failed_count"] == 1


async def _seed_failed_cycle(db, wid, cid, sid, phone, marker, age_minutes):
    """Seed one failed row for (cid, phone) with an explicit created_at age, so the
    'most recent failed row' is unambiguous regardless of clock resolution."""
    row = (await db.execute(text("""
        INSERT INTO message_queue (workspace_id, campaign_id, sender_id, item_type,
            status, recipient_phone, message_text, error_message, finished_at,
            attempts, scheduled_at, created_at)
        VALUES (:wid, :cid, :sid, 'message', 'failed', :phone, :txt, 'boom', NOW(),
                3, NOW(), NOW() - make_interval(mins => :age))
        RETURNING id
    """), {"wid": str(wid), "cid": str(cid), "sid": str(sid), "phone": phone,
           "txt": marker, "age": age_minutes})).first()
    await db.commit()
    return row.id


async def test_requeue_failed_dedups_per_recipient(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    """WR-15: the cold-fail release cap leaves up to CAP failed rows for ONE contact.
    requeue-failed must re-pend exactly ONE of them (the most recent) — otherwise the
    operator recovery action the cap's WARNING recommends would send CAP identical
    openers to the same person. Distinct recipients are still re-pended independently.
    """
    await _bind(async_db_session, test_workspace.id, "u-requeue-dedup")
    camp = await test_campaign_factory(status="running")
    sender = await test_sender_factory()
    capped, other = "+79990003001", "+79990003002"

    # 3 loop cycles for the same phone (oldest → newest) + 1 unrelated recipient.
    await _seed_failed_cycle(async_db_session, test_workspace.id, camp["id"], sender.id,
                             capped, "cycle-1", 30)
    await _seed_failed_cycle(async_db_session, test_workspace.id, camp["id"], sender.id,
                             capped, "cycle-2", 20)
    await _seed_failed_cycle(async_db_session, test_workspace.id, camp["id"], sender.id,
                             capped, "cycle-3", 10)
    await _seed_failed_cycle(async_db_session, test_workspace.id, camp["id"], sender.id,
                             other, "solo", 15)

    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/requeue-failed",
        headers=_auth_headers(valid_supabase_jwt, "u-requeue-dedup"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["requeued_count"] == 2, "one row per recipient, not per failed row"

    rows = (await async_db_session.execute(text("""
        SELECT recipient_phone, message_text, status
        FROM message_queue WHERE campaign_id = :cid
    """), {"cid": str(camp["id"])})).fetchall()

    pending = {(row.recipient_phone, row.message_text)
               for row in rows if row.status == "pending"}
    assert pending == {(capped, "cycle-3"), (other, "solo")}, (
        "only the most recent failed row per recipient may be re-pended"
    )

    still_failed = sorted(row.message_text for row in rows if row.status == "failed")
    assert still_failed == ["cycle-1", "cycle-2"], (
        "older duplicate cycles stay failed as the audit trail of the loop"
    )


# ─── IN-05: attach lock filter ───────────────────────────────────────────────


async def test_attach_conflict_free_sender_ok_despite_locked_pool_member(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory, attach_sender_to_campaign,
):
    """IN-05: campaign B already contains sender A (which is locked by running
    campaign A). Attaching a conflict-free sender C to B must NOT 409 — only the
    newly-attached sender C is checked."""
    await _bind(async_db_session, test_workspace.id, "u-in05-ok")
    sender_a = await test_sender_factory()
    sender_c = await test_sender_factory()
    await _warm_for_attach(async_db_session, test_workspace.id, sender_c.id)

    camp_a = await test_campaign_factory(status="running", name="RunningA")
    await attach_sender_to_campaign(camp_a["id"], sender_a.id)  # A holds sender_a

    camp_b = await test_campaign_factory(status="draft", name="DraftB")
    await attach_sender_to_campaign(camp_b["id"], sender_a.id)  # B already has sender_a (locked by A)

    r = await async_client.post(
        f"/api/v1/campaigns/{camp_b['id']}/senders",
        json={"sender_id": str(sender_c.id)},
        headers=_auth_headers(valid_supabase_jwt, "u-in05-ok"),
    )
    assert r.status_code == 200, r.text
    attached_ids = [str(s["id"]) for s in r.json()["attached_senders"]]
    assert str(sender_c.id) in attached_ids


async def test_attach_sender_in_running_campaign_still_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory, attach_sender_to_campaign,
):
    """IN-05 control: the narrowed check still catches a real conflict — attaching
    a sender that IS in another running campaign → 409 SENDER_LOCK_CONFLICT."""
    await _bind(async_db_session, test_workspace.id, "u-in05-409")
    sender = await test_sender_factory()

    running = await test_campaign_factory(status="running", name="LockHolderA")
    await attach_sender_to_campaign(running["id"], sender.id)

    target = await test_campaign_factory(status="draft", name="WantsLocked")
    r = await async_client.post(
        f"/api/v1/campaigns/{target['id']}/senders",
        json={"sender_id": str(sender.id)},
        headers=_auth_headers(valid_supabase_jwt, "u-in05-409"),
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "SENDER_LOCK_CONFLICT"
    assert str(detail["conflicts"][0]["sender_id"]) == str(sender.id)


# ─── IN-06: duplicate name collision → 409 (not 500) ─────────────────────────


async def test_duplicate_name_collision_returns_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, monkeypatch,
):
    """IN-06: a TOCTOU name collision surfacing as a unique-index IntegrityError on
    flush is translated to 409 CAMPAIGN_NAME_DUPLICATE (not a raw 500).

    Deterministically simulate the race: the first flush that has a pending
    Campaign (the duplicate INSERT) raises the unique-index violation, mimicking a
    concurrent create/duplicate that won the name between the loop's check and the
    flush.
    """
    await _bind(async_db_session, test_workspace.id, "u-in06")
    src = await test_campaign_factory(status="draft", name="DupSrc")

    orig_flush = AsyncSession.flush
    fired = {"done": False}

    async def flaky_flush(self, *a, **kw):
        if not fired["done"] and any(isinstance(o, Campaign) for o in self.new):
            fired["done"] = True
            raise IntegrityError(
                "INSERT INTO campaigns", {},
                Exception('duplicate key value violates unique constraint '
                          '"idx_campaigns_workspace_name"'),
            )
        return await orig_flush(self, *a, **kw)

    monkeypatch.setattr(AsyncSession, "flush", flaky_flush)

    r = await async_client.post(
        f"/api/v1/campaigns/{src['id']}/duplicate",
        headers=_auth_headers(valid_supabase_jwt, "u-in06"),
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "CAMPAIGN_NAME_DUPLICATE"


# ─── IN-10: pool_health.active excludes unhealthy senders ────────────────────


async def test_pool_health_active_excludes_session_expired(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory, attach_sender_to_campaign,
):
    """IN-10: pool_health.active counts only restriction_status='none' AND
    auth_status='ok' AND lifecycle_status='active'. A session_expired sender is in
    total but NOT in active."""
    await _bind(async_db_session, test_workspace.id, "u-in10")
    camp = await test_campaign_factory(status="running")
    healthy = await test_sender_factory()
    expired = await test_sender_factory(auth_status="session_expired")
    await attach_sender_to_campaign(camp["id"], healthy.id)
    await attach_sender_to_campaign(camp["id"], expired.id)

    r = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}",
        headers=_auth_headers(valid_supabase_jwt, "u-in10"),
    )
    assert r.status_code == 200, r.text
    ph = r.json()["pool_health"]
    assert ph["total"] == 2
    assert ph["active"] == 1
