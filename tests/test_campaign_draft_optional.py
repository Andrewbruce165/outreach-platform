"""Campaign draft-optional create + start readiness (quick-260618-a97 / CAMP-DRAFT-OPT).

Covers migration 024 enabler for the 7-step wizard autosave (UI-SPEC §5.5):
- POST /campaigns with only `name` saves an incomplete draft (201).
- POST /{id}/start on an incomplete draft → 422 CAMPAIGN_INCOMPLETE with missing[].
- POST /{id}/start on a complete draft (agent+folder+template+≥1 sender) → running.
- Foreign agent_id on create still validates → 404 (contract preserved).
"""

import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


def _headers(jwt, sub):
    return {"Authorization": f"Bearer {jwt(sub=sub)}"}


async def test_create_draft_without_agent_folder_template_returns_201(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    await _bind(async_db_session, test_workspace.id, "u-draft")
    r = await async_client.post(
        "/api/v1/campaigns",
        json={"name": "DraftOnly"},
        headers=_headers(valid_supabase_jwt, "u-draft"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft"
    assert body["agent_id"] is None
    assert body["folder_id"] is None
    assert body["message_template"] == ""
    assert body["is_exhausted"] is False


async def test_start_incomplete_draft_returns_422_campaign_incomplete(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    await _bind(async_db_session, test_workspace.id, "u-inc")
    r = await async_client.post(
        "/api/v1/campaigns",
        json={"name": "Incomplete"},
        headers=_headers(valid_supabase_jwt, "u-inc"),
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    r2 = await async_client.post(
        f"/api/v1/campaigns/{cid}/start",
        headers=_headers(valid_supabase_jwt, "u-inc"),
    )
    assert r2.status_code == 422, r2.text
    detail = r2.json()["detail"]
    assert detail["code"] == "CAMPAIGN_INCOMPLETE"
    assert "agent_id" in detail["missing"]
    assert "folder_id" in detail["missing"]
    assert "message_template" in detail["missing"]


async def test_start_complete_draft_transitions_to_running(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    await _bind(async_db_session, test_workspace.id, "u-cmpl")
    agent = await test_agent_factory()
    sender = await test_sender_factory()

    r = await async_client.post(
        "/api/v1/campaigns",
        json={
            "name": "Complete",
            "agent_id": str(agent.id),
            "folder_id": str(test_folder.id),
            "sender_ids": [str(sender.id)],
            "message_template": "Hi {{name}}",
        },
        headers=_headers(valid_supabase_jwt, "u-cmpl"),
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    r2 = await async_client.post(
        f"/api/v1/campaigns/{cid}/start",
        headers=_headers(valid_supabase_jwt, "u-cmpl"),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "running"


async def test_create_with_foreign_agent_still_validates_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    await _bind(async_db_session, test_workspace.id, "u-fgn")
    r = await async_client.post(
        "/api/v1/campaigns",
        json={"name": "X", "agent_id": str(uuid.uuid4())},
        headers=_headers(valid_supabase_jwt, "u-fgn"),
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "AGENT_NOT_FOUND"
