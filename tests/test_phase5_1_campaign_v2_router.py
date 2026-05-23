"""Campaign POST/PATCH/GET round-trip with 05.1 v2 columns — UI-CAMPB-01.

Wire-up test that ensures the four v2 fields (audience_hints, primary_goal,
success_criteria, webhook_url) survive a full create→get→patch→get round-trip
through the router layer (not just the schema layer — schema-only validation
lives in test_phase5_1_campaign_v2.py from plan 01).
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "v2-router-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def test_campaign_create_and_get_with_v2_fields(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """POST + GET round-trip persists 4 v2 fields."""
    await _bind(async_db_session, test_workspace.id, "u-v2-create")
    agent = await test_agent_factory()
    payload = {
        "name": "V2 builder",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "message_template": "Hi {{name}}",
        "audience_hints": "Bay-Area SaaS founders",
        "primary_goal": "book_meeting",
        "success_criteria": "They book a calendar slot",
        "webhook_url": "https://hook.test/aimly",
    }
    resp = await async_client.post(
        "/api/v1/campaigns",
        json=payload,
        headers=_auth_headers(valid_supabase_jwt, "u-v2-create"),
    )
    assert resp.status_code == 201, resp.text
    cid = resp.json()["id"]

    got = await async_client.get(
        f"/api/v1/campaigns/{cid}",
        headers=_auth_headers(valid_supabase_jwt, "u-v2-create"),
    )
    assert got.status_code == 200
    body = got.json()
    assert body["primary_goal"] == "book_meeting"
    assert body["audience_hints"] == "Bay-Area SaaS founders"
    assert body["success_criteria"] == "They book a calendar slot"
    assert body["webhook_url"] == "https://hook.test/aimly"


async def test_campaign_patch_v2_fields_partial(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """PATCH with partial v2 body — unspecified fields stay as previous value."""
    await _bind(async_db_session, test_workspace.id, "u-v2-patch")
    agent = await test_agent_factory()

    # Create with full v2 payload first so patches have something to mutate.
    create = await async_client.post(
        "/api/v1/campaigns",
        json={
            "name": "V2 patch base",
            "agent_id": str(agent.id),
            "folder_id": str(test_folder.id),
            "message_template": "Hello {{name}}",
            "audience_hints": "Initial hint",
            "primary_goal": "book_meeting",
            "success_criteria": "Initial criteria",
            "webhook_url": "https://hook.test/initial",
        },
        headers=_auth_headers(valid_supabase_jwt, "u-v2-patch"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]

    patch = {"primary_goal": "qualify", "audience_hints": "Different hint"}
    resp = await async_client.patch(
        f"/api/v1/campaigns/{cid}",
        json=patch,
        headers=_auth_headers(valid_supabase_jwt, "u-v2-patch"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["primary_goal"] == "qualify"
    assert body["audience_hints"] == "Different hint"
    # Untouched fields preserved.
    assert body["success_criteria"] == "Initial criteria"
    assert body["webhook_url"] == "https://hook.test/initial"


async def test_campaign_patch_v2_webhook_url_only(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """PATCH a fresh webhook_url converts HttpUrl → str (matches legacy URL handling)."""
    await _bind(async_db_session, test_workspace.id, "u-v2-hookpatch")
    agent = await test_agent_factory()

    create = await async_client.post(
        "/api/v1/campaigns",
        json={
            "name": "V2 hook patch",
            "agent_id": str(agent.id),
            "folder_id": str(test_folder.id),
            "message_template": "Hi",
        },
        headers=_auth_headers(valid_supabase_jwt, "u-v2-hookpatch"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]

    resp = await async_client.patch(
        f"/api/v1/campaigns/{cid}",
        json={"webhook_url": "https://hook.test/new"},
        headers=_auth_headers(valid_supabase_jwt, "u-v2-hookpatch"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["webhook_url"] == "https://hook.test/new"


async def test_campaign_create_rejects_invalid_primary_goal(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """Pydantic Literal enforcement — bogus primary_goal value → 422."""
    await _bind(async_db_session, test_workspace.id, "u-v2-bad")
    agent = await test_agent_factory()
    resp = await async_client.post(
        "/api/v1/campaigns",
        json={
            "name": "Bad goal",
            "agent_id": str(agent.id),
            "folder_id": str(test_folder.id),
            "message_template": "Hi",
            "primary_goal": "ascend_to_godhood",
        },
        headers=_auth_headers(valid_supabase_jwt, "u-v2-bad"),
    )
    assert resp.status_code == 422


async def test_campaign_legacy_create_v2_fields_default_null(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """Legacy (Phase 4) create payload (no v2 fields) → response shows v2 fields as None."""
    await _bind(async_db_session, test_workspace.id, "u-legacy-create")
    agent = await test_agent_factory()
    resp = await async_client.post(
        "/api/v1/campaigns",
        json={
            "name": "Legacy Phase4 shape",
            "agent_id": str(agent.id),
            "folder_id": str(test_folder.id),
            "message_template": "Hi",
        },
        headers=_auth_headers(valid_supabase_jwt, "u-legacy-create"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["audience_hints"] is None
    assert body["primary_goal"] is None
    assert body["success_criteria"] is None
    assert body["webhook_url"] is None
