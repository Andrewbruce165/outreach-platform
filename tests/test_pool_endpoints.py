"""Phase 8 — Pool management attach/detach endpoint tests (Wave 0 RED stubs).

These tests are written to fully ASSERT the documented behaviour of the
attach/detach endpoints that Plan 08-03 will implement. Until that plan lands the
endpoints do not exist (`POST /api/v1/campaigns/{id}/senders`,
`DELETE /api/v1/campaigns/{id}/senders/{sid}`), so every test here FAILS RED —
that is the expected Wave-0 state per 08-VALIDATION.md.

Test → requirement map (contract — names consumed by later verify commands):
- test_attach_adds_sender        → POOL-01 (attach to workspace-owned sender → 200 + row)
- test_attach_locked_sender_409  → POOL-02 (sender in other running campaign → 409 SENDER_LOCK_CONFLICT)
- test_attach_foreign_sender_404 → POOL-03 (foreign-workspace sender → 404 SENDER_NOT_FOUND, no leak)
- test_detach_removes_sender     → POOL-04 (detach removes campaign_senders row → 200)
- test_detach_last_running_409    → POOL-05 (detach last sender of running → 409 MIN_POOL_GUARD)
- test_detach_cold_pending_409    → POOL-06 (un-sent cold pending → 409 DETACH_BLOCKED_PENDING)
- test_detach_engaged_only_ok     → POOL-06b (only engaged dialogs remain → 200, D-05)
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "pool-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


# NOTE on isolation: user_workspaces has a DB-level UNIQUE(supabase_user_id)
# (migration 023) and the test schema is created once per session, so a JWT
# `sub` stays bound to whichever workspace it was first _bind()'d to. Each test
# therefore uses a DISTINCT sub bound to its own per-test `test_workspace`
# (mirrors the convention in test_campaign_router.py — u-list / u-lock / ...),
# otherwise tests running after the first would resolve a stale workspace and
# 404 on their freshly-created campaign.


async def _bind(db, ws_id, uid):
    """Bind a Supabase user (JWT sub) to a workspace as owner."""
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def _count_campaign_senders(db, campaign_id, sender_id) -> int:
    row = (await db.execute(text("""
        SELECT COUNT(*) FROM campaign_senders
        WHERE campaign_id = :cid AND sender_id = :sid
    """), {"cid": str(campaign_id), "sid": str(sender_id)})).scalar_one()
    return int(row)


# ─── POOL-01 ─────────────────────────────────────────────────────────────────

async def test_attach_adds_sender(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    """POOL-01: attach a workspace-owned sender → 200 + campaign_senders row."""
    await _bind(async_db_session, test_workspace.id, "pool-add")
    camp = await test_campaign_factory(status="draft")
    sender = await test_sender_factory()

    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/senders",
        json={"sender_id": str(sender.id)},
        headers=_auth_headers(valid_supabase_jwt, "pool-add"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    attached_ids = [str(s["id"]) for s in body["attached_senders"]]
    assert str(sender.id) in attached_ids
    assert await _count_campaign_senders(async_db_session, camp["id"], sender.id) == 1


# ─── POOL-02 ─────────────────────────────────────────────────────────────────

async def test_attach_locked_sender_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory, attach_sender_to_campaign,
):
    """POOL-02: sender already in ANOTHER running campaign → 409 SENDER_LOCK_CONFLICT.

    detail.conflicts is a non-empty list of {sender_id, campaign_id, campaign_name}.
    """
    await _bind(async_db_session, test_workspace.id, "pool-lock")
    sender = await test_sender_factory()

    # Sender is locked by a running campaign in the SAME workspace.
    running = await test_campaign_factory(status="running", name="LockHolder")
    await attach_sender_to_campaign(running["id"], sender.id)

    target = await test_campaign_factory(status="draft", name="WantsSender")
    r = await async_client.post(
        f"/api/v1/campaigns/{target['id']}/senders",
        json={"sender_id": str(sender.id)},
        headers=_auth_headers(valid_supabase_jwt, "pool-lock"),
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "SENDER_LOCK_CONFLICT"
    conflicts = detail["conflicts"]
    assert isinstance(conflicts, list) and len(conflicts) >= 1
    first = conflicts[0]
    assert {"sender_id", "campaign_id", "campaign_name"} <= set(first.keys())
    assert str(first["sender_id"]) == str(sender.id)
    assert str(first["campaign_id"]) == str(running["id"])
    assert first["campaign_name"] == "LockHolder"

    # Lock must not have been broken / sender must not be attached to target.
    assert await _count_campaign_senders(async_db_session, target["id"], sender.id) == 0


# ─── POOL-03 ─────────────────────────────────────────────────────────────────

async def test_attach_foreign_sender_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """POOL-03: attaching a sender NOT owned by the workspace → 404 SENDER_NOT_FOUND.

    The response must not leak any data about the foreign sender.
    """
    from app.models import Workspace, Sender

    await _bind(async_db_session, test_workspace.id, "pool-foreign")
    camp = await test_campaign_factory(status="draft")

    # A sender living in a DIFFERENT workspace.
    other = Workspace(name="ForeignWS")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    foreign = Sender(
        workspace_id=other.id, slug="foreign-sender", name="Foreign Sender",
        phone="+79995550000", session_string="enc", role="sender",
        auth_status="ok", lifecycle_status="active",
        rate_per_min=4, rate_per_hour=20, rate_per_day=150,
    )
    async_db_session.add(foreign)
    await async_db_session.commit()
    await async_db_session.refresh(foreign)

    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/senders",
        json={"sender_id": str(foreign.id)},
        headers=_auth_headers(valid_supabase_jwt, "pool-foreign"),
    )
    assert r.status_code == 404, r.text
    detail = r.json()["detail"]
    code = detail["code"] if isinstance(detail, dict) else detail
    assert code == "SENDER_NOT_FOUND"
    # No foreign data leaked into the body.
    assert "Foreign Sender" not in r.text
    assert "+79995550000" not in r.text
    assert await _count_campaign_senders(async_db_session, camp["id"], foreign.id) == 0


# ─── POOL-04 ─────────────────────────────────────────────────────────────────

async def test_detach_removes_sender(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_running_campaign_factory,
):
    """POOL-04: DELETE a sender from a 2-sender campaign → 200 + row gone."""
    await _bind(async_db_session, test_workspace.id, "pool-detach")
    camp, senders = await test_running_campaign_factory(sender_count=2)
    victim = senders[0]

    r = await async_client.delete(
        f"/api/v1/campaigns/{camp['id']}/senders/{victim.id}",
        headers=_auth_headers(valid_supabase_jwt, "pool-detach"),
    )
    assert r.status_code == 200, r.text
    assert await _count_campaign_senders(async_db_session, camp["id"], victim.id) == 0
    # The other sender stays attached.
    assert await _count_campaign_senders(async_db_session, camp["id"], senders[1].id) == 1


# ─── POOL-05 ─────────────────────────────────────────────────────────────────

async def test_detach_last_running_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_running_campaign_factory,
):
    """POOL-05: detach the ONLY sender of a RUNNING campaign → 409 MIN_POOL_GUARD."""
    await _bind(async_db_session, test_workspace.id, "pool-min")
    camp, senders = await test_running_campaign_factory(sender_count=1)
    only = senders[0]

    r = await async_client.delete(
        f"/api/v1/campaigns/{camp['id']}/senders/{only.id}",
        headers=_auth_headers(valid_supabase_jwt, "pool-min"),
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    code = detail["code"] if isinstance(detail, dict) else detail
    assert code == "MIN_POOL_GUARD"
    # Sender must still be attached (guard blocked the removal).
    assert await _count_campaign_senders(async_db_session, camp["id"], only.id) == 1


# ─── POOL-06 ─────────────────────────────────────────────────────────────────

async def test_detach_cold_pending_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_running_campaign_factory, test_queue_item_factory,
):
    """POOL-06: detach a sender holding an un-sent COLD pending queue row → 409.

    Cold = pending queue row with NO conversation (not engaged). Such a row would be
    silently dropped on detach, so the endpoint must block with DETACH_BLOCKED_PENDING.
    """
    await _bind(async_db_session, test_workspace.id, "pool-cold")
    camp, senders = await test_running_campaign_factory(sender_count=2)
    victim = senders[0]

    # Victim holds one cold pending recipient (no conversation).
    await test_queue_item_factory(
        camp["id"], victim.id, "+79990010001",
        status="pending", with_cca=True, with_conversation=False,
    )

    r = await async_client.delete(
        f"/api/v1/campaigns/{camp['id']}/senders/{victim.id}",
        headers=_auth_headers(valid_supabase_jwt, "pool-cold"),
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    code = detail["code"] if isinstance(detail, dict) else detail
    assert code == "DETACH_BLOCKED_PENDING"
    assert await _count_campaign_senders(async_db_session, camp["id"], victim.id) == 1


# ─── POOL-06b ────────────────────────────────────────────────────────────────

async def test_detach_engaged_only_ok(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_running_campaign_factory, test_queue_item_factory,
):
    """POOL-06b: when the sender's only pending recipient is ENGAGED (has a
    conversation row) detach is ALLOWED → 200. Engaged dialogs do NOT block
    detach (D-05) — they belong to the dialog owner, not the cold queue."""
    await _bind(async_db_session, test_workspace.id, "pool-engaged")
    camp, senders = await test_running_campaign_factory(sender_count=2)
    victim = senders[0]

    # Victim's only pending recipient already has an open conversation → engaged.
    await test_queue_item_factory(
        camp["id"], victim.id, "+79990020002",
        status="pending", with_cca=True, with_conversation=True,
    )

    r = await async_client.delete(
        f"/api/v1/campaigns/{camp['id']}/senders/{victim.id}",
        headers=_auth_headers(valid_supabase_jwt, "pool-engaged"),
    )
    assert r.status_code == 200, r.text
    assert await _count_campaign_senders(async_db_session, camp["id"], victim.id) == 0
