"""POST /api/v1/campaigns/{id}/stop is alias of /finish — UI-CAMPL-01.

Phase 05.1 plan 03 Task 1. The new endpoint is an additive alias — Phase 4
test_campaign_router.py uses POST /finish directly and must stay green.
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "stop-alias-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    """Bind a Supabase user to a workspace as owner."""
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def test_stop_alias_finishes_running_campaign(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """Running → done via /stop (mirrors /finish)."""
    await _bind(async_db_session, test_workspace.id, "u-stop-run")
    c = await test_campaign_factory(status="running", name="StopRun")
    resp = await async_client.post(
        f"/api/v1/campaigns/{c['id']}/stop",
        headers=_auth_headers(valid_supabase_jwt, "u-stop-run"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "done"


async def test_stop_alias_finishes_paused_campaign(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """Paused → done via /stop (matches /finish allowed transitions)."""
    await _bind(async_db_session, test_workspace.id, "u-stop-paused")
    c = await test_campaign_factory(status="paused", name="StopPaused")
    resp = await async_client.post(
        f"/api/v1/campaigns/{c['id']}/stop",
        headers=_auth_headers(valid_supabase_jwt, "u-stop-paused"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "done"


async def test_stop_alias_rejects_draft(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """Draft campaign cannot be stopped — 409 INVALID_TRANSITION."""
    await _bind(async_db_session, test_workspace.id, "u-stop-draft")
    c = await test_campaign_factory(status="draft", name="StopDraft")
    resp = await async_client.post(
        f"/api/v1/campaigns/{c['id']}/stop",
        headers=_auth_headers(valid_supabase_jwt, "u-stop-draft"),
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "INVALID_TRANSITION"
    assert body["detail"]["from"] == "draft"


async def test_stop_alias_404_on_cross_workspace(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """Cross-workspace caller gets 404 (silent — Phase 1 D-04 security)."""
    from app.models import Workspace

    await _bind(async_db_session, test_workspace.id, "u-owner")
    c = await test_campaign_factory(status="running", name="StopOther")

    # Different workspace + different user binding.
    other = Workspace(name="OtherStopWS")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-stranger")

    resp = await async_client.post(
        f"/api/v1/campaigns/{c['id']}/stop",
        headers=_auth_headers(valid_supabase_jwt, "u-stranger"),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "CAMPAIGN_NOT_FOUND"


async def test_stop_alias_terminal_no_double_done(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """Already-done campaign rejects /stop with 409 (terminal, like /finish)."""
    await _bind(async_db_session, test_workspace.id, "u-stop-done")
    c = await test_campaign_factory(status="done", name="StopAlreadyDone")
    resp = await async_client.post(
        f"/api/v1/campaigns/{c['id']}/stop",
        headers=_auth_headers(valid_supabase_jwt, "u-stop-done"),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "INVALID_TRANSITION"
