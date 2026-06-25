"""Campaign POST/PATCH/GET round-trip with 05.1 v2 + Phase 11 columns — UI-CAMPB-01.

Wire-up test that ensures campaign fields survive a full create→get→patch→get round-trip
through the router layer (not just the schema layer — schema-only validation
lives in test_phase5_1_campaign_v2.py from plan 01).

Phase 11 D-13: success_criteria removed (merged into lead_trigger_hint).
Phase 11 D-04/D-12/D-14: dialogue_flow, arguments_facts, campaign_rules added.
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "v2-router-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


def _camp(resp_json: dict) -> dict:
    """Phase 12 NDLG-04: create/patch now return CampaignWriteResponse
    {campaign, warnings[]}; GET stays flat CampaignResponse. Unwrap when wrapped."""
    if isinstance(resp_json, dict) and "campaign" in resp_json and "warnings" in resp_json:
        return resp_json["campaign"]
    return resp_json


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
    """POST + GET round-trip persists v2 + Phase 11 fields."""
    await _bind(async_db_session, test_workspace.id, "u-v2-create")
    agent = await test_agent_factory()
    payload = {
        "name": "V2 builder",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "message_template": "Hi {{name}}",
        "audience_hints": "Bay-Area SaaS founders",
        "primary_goal": "book_meeting",
        # Phase 11 D-13: success_criteria removed — use lead_trigger_hint
        "lead_trigger_hint": "They book a calendar slot",
        "webhook_url": "https://hook.test/aimly",
        # Phase 11 D-04: dialogue_flow
        "dialogue_flow": [
            {"title": "Intro", "instruction": "Greet the prospect and introduce Aimly"},
            {"instruction": "Ask about their current outreach stack"},
        ],
        "arguments_facts": "Aimly saves 10h/week per SDR.",
        "campaign_rules": "Never mention competitors.",
    }
    resp = await async_client.post(
        "/api/v1/campaigns",
        json=payload,
        headers=_auth_headers(valid_supabase_jwt, "u-v2-create"),
    )
    assert resp.status_code == 201, resp.text
    cid = _camp(resp.json())["id"]

    got = await async_client.get(
        f"/api/v1/campaigns/{cid}",
        headers=_auth_headers(valid_supabase_jwt, "u-v2-create"),
    )
    assert got.status_code == 200
    body = got.json()
    assert body["primary_goal"] == "book_meeting"
    assert body["audience_hints"] == "Bay-Area SaaS founders"
    assert body["lead_trigger_hint"] == "They book a calendar slot"
    assert body["webhook_url"] == "https://hook.test/aimly"
    # Phase 11 D-04/D-12/D-14 assertions:
    assert len(body["dialogue_flow"]) == 2
    assert body["dialogue_flow"][0]["title"] == "Intro"
    assert body["arguments_facts"] == "Aimly saves 10h/week per SDR."
    assert body["campaign_rules"] == "Never mention competitors."
    # success_criteria must NOT be in the response (Phase 11 D-13)
    assert "success_criteria" not in body


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
            # Phase 11 D-13: use lead_trigger_hint instead of success_criteria
            "lead_trigger_hint": "Initial criteria",
            "webhook_url": "https://hook.test/initial",
        },
        headers=_auth_headers(valid_supabase_jwt, "u-v2-patch"),
    )
    assert create.status_code == 201, create.text
    cid = _camp(create.json())["id"]

    patch = {"primary_goal": "qualify", "audience_hints": "Different hint"}
    resp = await async_client.patch(
        f"/api/v1/campaigns/{cid}",
        json=patch,
        headers=_auth_headers(valid_supabase_jwt, "u-v2-patch"),
    )
    assert resp.status_code == 200, resp.text
    body = _camp(resp.json())
    assert body["primary_goal"] == "qualify"
    assert body["audience_hints"] == "Different hint"
    # Untouched fields preserved.
    assert body["lead_trigger_hint"] == "Initial criteria"
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
    cid = _camp(create.json())["id"]

    resp = await async_client.patch(
        f"/api/v1/campaigns/{cid}",
        json={"webhook_url": "https://hook.test/new"},
        headers=_auth_headers(valid_supabase_jwt, "u-v2-hookpatch"),
    )
    assert resp.status_code == 200, resp.text
    assert _camp(resp.json())["webhook_url"] == "https://hook.test/new"


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
    body = _camp(resp.json())
    assert body["audience_hints"] is None
    assert body["primary_goal"] is None
    # Phase 11 D-13: success_criteria removed
    assert "success_criteria" not in body
    assert body["webhook_url"] is None
    # Phase 11 D-04/D-12/D-14: new fields present with defaults
    assert body["dialogue_flow"] == []
    assert body["arguments_facts"] is None
    assert body["campaign_rules"] is None


async def test_campaign_phase11_dialogue_flow_patch_full_replace(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """Phase 11 D-04: dialogue_flow PATCH = full replacement (not merge)."""
    await _bind(async_db_session, test_workspace.id, "u-df-patch")
    agent = await test_agent_factory()
    create = await async_client.post(
        "/api/v1/campaigns",
        json={
            "name": "DF patch test",
            "agent_id": str(agent.id),
            "folder_id": str(test_folder.id),
            "message_template": "Hi",
            "dialogue_flow": [
                {"title": "Stage 1", "instruction": "Do stage 1"},
                {"title": "Stage 2", "instruction": "Do stage 2"},
            ],
        },
        headers=_auth_headers(valid_supabase_jwt, "u-df-patch"),
    )
    assert create.status_code == 201, create.text
    cid = _camp(create.json())["id"]

    # PATCH with single-item list — must REPLACE (full replacement, not append)
    resp = await async_client.patch(
        f"/api/v1/campaigns/{cid}",
        json={"dialogue_flow": [{"instruction": "Only stage now"}]},
        headers=_auth_headers(valid_supabase_jwt, "u-df-patch"),
    )
    assert resp.status_code == 200, resp.text
    body = _camp(resp.json())
    assert len(body["dialogue_flow"]) == 1, \
        f"dialogue_flow must be replaced (not merged), got {len(body['dialogue_flow'])} stages"
    assert body["dialogue_flow"][0]["instruction"] == "Only stage now"
