"""Campaign model integration tests — POST /api/v1/campaigns (CAMP-01..04).

Covers:
- CAMP-01 create
- CAMP-02 workspace-scoped agent_id validation → 404
- CAMP-03 workspace-scoped folder_id validation → 404
- CAMP-04 (workspace-isolation half — Q4): cross-workspace sender → 404
- Defaults (timezone, work_hours, work_days, status='draft')
- Duplicate name → 409
- Invalid timezone → 422
"""

import pytest

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "user-default") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


def _camp(resp_json):
    """Phase 12 NDLG-04: create/patch return CampaignWriteResponse {campaign, warnings[]}."""
    if isinstance(resp_json, dict) and "campaign" in resp_json and "warnings" in resp_json:
        return resp_json["campaign"]
    return resp_json


async def _bind_workspace_to_user(db_session, workspace_id, supabase_user_id: str):
    """auth_dep resolves workspace via user_workspaces table — bind helper."""
    from sqlalchemy import text
    await db_session.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner')
        ON CONFLICT DO NOTHING
    """), {"uid": supabase_user_id, "wid": str(workspace_id)})
    await db_session.commit()


async def test_create_campaign(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """CAMP-01: POST /api/v1/campaigns with valid payload → 201."""
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind_workspace_to_user(async_db_session, test_workspace.id, "user-1")

    payload = {
        "name": "Test Campaign 01",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "sender_ids": [str(sender.id)],
        "message_template": "Hello {{name}}!",
    }
    r = await async_client.post("/api/v1/campaigns", json=payload,
                                headers=_auth_headers(valid_supabase_jwt, "user-1"))
    assert r.status_code == 201, r.text
    body = _camp(r.json())
    assert body["name"] == "Test Campaign 01"
    assert body["status"] == "draft"
    assert body["workspace_id"] == str(test_workspace.id)
    assert body["timezone"] == "Europe/Moscow"
    assert body["work_hour_start"] == 9
    assert body["work_hour_end"] == 20
    assert body["work_days_mask"] == 31
    assert body["message_template"] == "Hello {{name}}!"
    assert len(body["attached_senders"]) == 1
    assert body["attached_senders"][0]["sender_id"] == str(sender.id)


async def test_create_with_other_workspace_agent_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace, test_folder,
):
    """CAMP-02: agent_id из чужого workspace → 404 AGENT_NOT_FOUND."""
    from app.models import Workspace, AIContext

    other_ws = Workspace(name="Other WS")
    async_db_session.add(other_ws)
    await async_db_session.commit()
    await async_db_session.refresh(other_ws)

    other_agent = AIContext(workspace_id=other_ws.id, name="Other Agent")
    async_db_session.add(other_agent)
    await async_db_session.commit()
    await async_db_session.refresh(other_agent)

    await _bind_workspace_to_user(async_db_session, test_workspace.id, "user-2")
    payload = {
        "name": "X-Workspace",
        "agent_id": str(other_agent.id),
        "folder_id": str(test_folder.id),
        "message_template": "hi",
    }
    r = await async_client.post("/api/v1/campaigns", json=payload,
                                headers=_auth_headers(valid_supabase_jwt, "user-2"))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "AGENT_NOT_FOUND"


async def test_create_with_other_workspace_folder_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace, test_agent_factory,
):
    """CAMP-03: folder_id из чужого workspace → 404 FOLDER_NOT_FOUND."""
    from app.models import Workspace, Folder

    other_ws = Workspace(name="Other WS F")
    async_db_session.add(other_ws)
    await async_db_session.commit()
    await async_db_session.refresh(other_ws)
    other_folder = Folder(workspace_id=other_ws.id, name="Other Folder")
    async_db_session.add(other_folder)
    await async_db_session.commit()
    await async_db_session.refresh(other_folder)

    agent = await test_agent_factory()
    await _bind_workspace_to_user(async_db_session, test_workspace.id, "user-3")
    payload = {
        "name": "X-WS F",
        "agent_id": str(agent.id),
        "folder_id": str(other_folder.id),
        "message_template": "hi",
    }
    r = await async_client.post("/api/v1/campaigns", json=payload,
                                headers=_auth_headers(valid_supabase_jwt, "user-3"))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "FOLDER_NOT_FOUND"


async def test_create_with_other_workspace_sender_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """Q4: sender_id из чужого workspace → 404 SENDER_NOT_FOUND."""
    from app.models import Workspace, Sender

    other_ws = Workspace(name="Other WS S")
    async_db_session.add(other_ws)
    await async_db_session.commit()
    await async_db_session.refresh(other_ws)
    other_sender = Sender(
        workspace_id=other_ws.id, slug="other-sender-xx",
        name="Other Sender", phone="+79111111111",
        session_string="x", role="sender",
        auth_status="ok", lifecycle_status="active",
        rate_per_min=4, rate_per_hour=20,
    )
    async_db_session.add(other_sender)
    await async_db_session.commit()
    await async_db_session.refresh(other_sender)

    agent = await test_agent_factory()
    await _bind_workspace_to_user(async_db_session, test_workspace.id, "user-4")
    payload = {
        "name": "X-WS S",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "sender_ids": [str(other_sender.id)],
        "message_template": "hi",
    }
    r = await async_client.post("/api/v1/campaigns", json=payload,
                                headers=_auth_headers(valid_supabase_jwt, "user-4"))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SENDER_NOT_FOUND"


async def test_default_timezone_is_europe_moscow(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    agent = await test_agent_factory()
    await _bind_workspace_to_user(async_db_session, test_workspace.id, "user-5")
    r = await async_client.post("/api/v1/campaigns", json={
        "name": "TZ Default",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "message_template": "hi",
    }, headers=_auth_headers(valid_supabase_jwt, "user-5"))
    assert r.status_code == 201
    assert _camp(r.json())["timezone"] == "Europe/Moscow"


async def test_default_work_hours_9_to_20(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    agent = await test_agent_factory()
    await _bind_workspace_to_user(async_db_session, test_workspace.id, "user-6")
    r = await async_client.post("/api/v1/campaigns", json={
        "name": "Hours Default",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "message_template": "hi",
    }, headers=_auth_headers(valid_supabase_jwt, "user-6"))
    assert r.status_code == 201
    b = _camp(r.json())
    assert b["work_hour_start"] == 9
    assert b["work_hour_end"] == 20


async def test_default_work_days_mask_31(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    agent = await test_agent_factory()
    await _bind_workspace_to_user(async_db_session, test_workspace.id, "user-7")
    r = await async_client.post("/api/v1/campaigns", json={
        "name": "Days Default",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "message_template": "hi",
    }, headers=_auth_headers(valid_supabase_jwt, "user-7"))
    assert r.status_code == 201
    assert _camp(r.json())["work_days_mask"] == 31


async def test_create_status_default_draft(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    agent = await test_agent_factory()
    await _bind_workspace_to_user(async_db_session, test_workspace.id, "user-8")
    r = await async_client.post("/api/v1/campaigns", json={
        "name": "Status Default",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "message_template": "hi",
    }, headers=_auth_headers(valid_supabase_jwt, "user-8"))
    assert r.status_code == 201
    assert _camp(r.json())["status"] == "draft"


async def test_create_duplicate_name_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    agent = await test_agent_factory()
    await _bind_workspace_to_user(async_db_session, test_workspace.id, "user-9")
    headers = _auth_headers(valid_supabase_jwt, "user-9")
    payload = {
        "name": "Dup Name",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "message_template": "hi",
    }
    r1 = await async_client.post("/api/v1/campaigns", json=payload, headers=headers)
    assert r1.status_code == 201
    r2 = await async_client.post("/api/v1/campaigns", json=payload, headers=headers)
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "CAMPAIGN_NAME_DUPLICATE"


async def test_create_invalid_timezone_422(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    agent = await test_agent_factory()
    await _bind_workspace_to_user(async_db_session, test_workspace.id, "user-10")
    r = await async_client.post("/api/v1/campaigns", json={
        "name": "Bad TZ",
        "agent_id": str(agent.id),
        "folder_id": str(test_folder.id),
        "timezone": "Invalid/Zone-xx-yy",
        "message_template": "hi",
    }, headers=_auth_headers(valid_supabase_jwt, "user-10"))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID_TIMEZONE"
