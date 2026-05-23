"""POST /senders/{slug}/pause + /resume — UI-ACCT-01 (Phase 05.1 plan 03 Task 2).

These endpoints are additive — the existing PATCH /senders/{slug} lifecycle path
stays untouched (Phase 4 test_sender_lock_check / Phase 2 SNDR-01 regress here).
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "lc-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def test_pause_active_sender_flips_to_paused(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    await _bind(async_db_session, test_workspace.id, "u-pause-active")
    s = await test_sender_factory(lifecycle_status="active")
    resp = await async_client.post(
        f"/api/v1/senders/{s.slug}/pause",
        headers=_auth_headers(valid_supabase_jwt, "u-pause-active"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sender"]["lifecycle_status"] == "paused"


async def test_pause_paused_sender_idempotent(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    """Already paused → 200 idempotent (matches frontend retry semantics)."""
    await _bind(async_db_session, test_workspace.id, "u-pause-idem")
    s = await test_sender_factory(lifecycle_status="paused")
    resp = await async_client.post(
        f"/api/v1/senders/{s.slug}/pause",
        headers=_auth_headers(valid_supabase_jwt, "u-pause-idem"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sender"]["lifecycle_status"] == "paused"


async def test_resume_paused_sender_flips_to_active(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    await _bind(async_db_session, test_workspace.id, "u-resume-paused")
    s = await test_sender_factory(lifecycle_status="paused")
    resp = await async_client.post(
        f"/api/v1/senders/{s.slug}/resume",
        headers=_auth_headers(valid_supabase_jwt, "u-resume-paused"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sender"]["lifecycle_status"] == "active"


async def test_resume_active_sender_idempotent(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    await _bind(async_db_session, test_workspace.id, "u-resume-idem")
    s = await test_sender_factory(lifecycle_status="active")
    resp = await async_client.post(
        f"/api/v1/senders/{s.slug}/resume",
        headers=_auth_headers(valid_supabase_jwt, "u-resume-idem"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sender"]["lifecycle_status"] == "active"


async def test_pause_sender_in_running_campaign_returns_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_campaign_factory, attach_sender_to_campaign,
):
    """Sender-lock guard preserved — cannot pause if attached to running campaign."""
    await _bind(async_db_session, test_workspace.id, "u-pause-locked")
    s = await test_sender_factory(lifecycle_status="active")
    c = await test_campaign_factory(status="running", name="LockedCamp")
    await attach_sender_to_campaign(c["id"], s.id)

    resp = await async_client.post(
        f"/api/v1/senders/{s.slug}/pause",
        headers=_auth_headers(valid_supabase_jwt, "u-pause-locked"),
    )
    assert resp.status_code == 409
    body = resp.json()
    # _check_sender_not_in_running_campaign raises SENDER_USED_BY_RUNNING_CAMPAIGN.
    assert body["detail"]["code"] == "SENDER_USED_BY_RUNNING_CAMPAIGN"


async def test_pause_404_on_cross_workspace_slug(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    """Cross-workspace pause → 404 (silent — Phase 1 D-04)."""
    from app.models import Workspace

    await _bind(async_db_session, test_workspace.id, "u-pause-owner")
    s = await test_sender_factory(lifecycle_status="active")

    other = Workspace(name="OtherPauseWS")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-pause-stranger")

    resp = await async_client.post(
        f"/api/v1/senders/{s.slug}/pause",
        headers=_auth_headers(valid_supabase_jwt, "u-pause-stranger"),
    )
    assert resp.status_code == 404


async def test_resume_404_on_cross_workspace_slug(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    from app.models import Workspace

    await _bind(async_db_session, test_workspace.id, "u-res-owner")
    s = await test_sender_factory(lifecycle_status="paused")

    other = Workspace(name="OtherResumeWS")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-res-stranger")

    resp = await async_client.post(
        f"/api/v1/senders/{s.slug}/resume",
        headers=_auth_headers(valid_supabase_jwt, "u-res-stranger"),
    )
    assert resp.status_code == 404


async def test_pause_warmup_sender_rejected_with_invalid_transition(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    """Warmup → paused is NOT supported via /pause (only active→paused).

    PATCH path can still flip warmup→paused directly; /pause is the one-button
    action for the active row in UI list, so we keep it strict to surface bugs.
    """
    await _bind(async_db_session, test_workspace.id, "u-pause-warmup")
    s = await test_sender_factory(lifecycle_status="warmup")
    resp = await async_client.post(
        f"/api/v1/senders/{s.slug}/pause",
        headers=_auth_headers(valid_supabase_jwt, "u-pause-warmup"),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "INVALID_TRANSITION"
    assert resp.json()["detail"]["from"] == "warmup"


async def test_existing_patch_lifecycle_path_still_works(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    """Regression: PATCH /senders/{slug} with lifecycle_status body still flips status.

    The new /pause /resume endpoints are ADDITIVE — they do not replace PATCH.
    """
    await _bind(async_db_session, test_workspace.id, "u-patch-regress")
    s = await test_sender_factory(lifecycle_status="active")
    resp = await async_client.patch(
        f"/api/v1/senders/{s.slug}",
        json={"lifecycle_status": "paused"},
        headers=_auth_headers(valid_supabase_jwt, "u-patch-regress"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sender"]["lifecycle_status"] == "paused"
