"""Campaign max_new_dialogs_per_day API — Phase 12 NDLG-03/NDLG-04 (D-12/D-13/D-14).

Mirrors the senders D-14 soft/hard-cap contract for the per-campaign daily new-dialog
cap on the campaign write path:

- create with no value      → 201, persisted default 50, warnings == []
- create with 70 (>soft 50) → 201 + warnings[] (recommended_max=50)
- create with 120 (>hard)   → 422
- patch to 80 (>soft)       → 200 + warnings[]
- patch to 150 (>hard)      → 422
- GET                       → flat CampaignResponse, no warnings, echoes stored value

The create/patch responses are the CampaignWriteResponse wrapper {campaign, warnings[]};
GET stays flat CampaignResponse (D-14: GET carries no warnings).
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "ndlg-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


def _base_payload(agent_id, folder_id, name: str, **extra) -> dict:
    payload = {
        "name": name,
        "agent_id": str(agent_id),
        "folder_id": str(folder_id),
        "message_template": "Hi {{name}}",
    }
    payload.update(extra)
    return payload


async def test_create_default_is_50(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """POST without max_new_dialogs_per_day → 201, default 50 persisted, no warnings."""
    await _bind(async_db_session, test_workspace.id, "ndlg-def")
    agent = await test_agent_factory()
    r = await async_client.post(
        "/api/v1/campaigns",
        json=_base_payload(agent.id, test_folder.id, "NDLG default"),
        headers=_auth_headers(valid_supabase_jwt, "ndlg-def"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["campaign"]["max_new_dialogs_per_day"] == 50
    assert body["warnings"] == []


async def test_create_soft_cap_warns(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """POST with 70 (>soft 50, <=hard 100) → 201 + one warning (recommended_max=50)."""
    await _bind(async_db_session, test_workspace.id, "ndlg-soft")
    agent = await test_agent_factory()
    r = await async_client.post(
        "/api/v1/campaigns",
        json=_base_payload(
            agent.id, test_folder.id, "NDLG soft", max_new_dialogs_per_day=70
        ),
        headers=_auth_headers(valid_supabase_jwt, "ndlg-soft"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["campaign"]["max_new_dialogs_per_day"] == 70
    assert len(body["warnings"]) == 1
    w = body["warnings"][0]
    assert w["field"] == "max_new_dialogs_per_day"
    assert w["recommended_max"] == 50


async def test_create_hard_cap_422(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """POST with 120 (>hard 100) → 422 (Pydantic le=100 or explicit hard-cap check)."""
    await _bind(async_db_session, test_workspace.id, "ndlg-hard")
    agent = await test_agent_factory()
    r = await async_client.post(
        "/api/v1/campaigns",
        json=_base_payload(
            agent.id, test_folder.id, "NDLG hard", max_new_dialogs_per_day=120
        ),
        headers=_auth_headers(valid_supabase_jwt, "ndlg-hard"),
    )
    assert r.status_code == 422, r.text


async def test_patch_soft_cap_warns(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """PATCH to 80 (>soft) → 200 + warnings[]; value round-trips on the wrapper."""
    await _bind(async_db_session, test_workspace.id, "ndlg-psoft")
    agent = await test_agent_factory()
    create = await async_client.post(
        "/api/v1/campaigns",
        json=_base_payload(agent.id, test_folder.id, "NDLG patch soft"),
        headers=_auth_headers(valid_supabase_jwt, "ndlg-psoft"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["campaign"]["id"]

    r = await async_client.patch(
        f"/api/v1/campaigns/{cid}",
        json={"max_new_dialogs_per_day": 80},
        headers=_auth_headers(valid_supabase_jwt, "ndlg-psoft"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["campaign"]["max_new_dialogs_per_day"] == 80
    assert len(body["warnings"]) == 1


async def test_patch_hard_cap_422(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """PATCH to 150 (>hard) → 422."""
    await _bind(async_db_session, test_workspace.id, "ndlg-phard")
    agent = await test_agent_factory()
    create = await async_client.post(
        "/api/v1/campaigns",
        json=_base_payload(agent.id, test_folder.id, "NDLG patch hard"),
        headers=_auth_headers(valid_supabase_jwt, "ndlg-phard"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["campaign"]["id"]

    r = await async_client.patch(
        f"/api/v1/campaigns/{cid}",
        json={"max_new_dialogs_per_day": 150},
        headers=_auth_headers(valid_supabase_jwt, "ndlg-phard"),
    )
    assert r.status_code == 422, r.text


async def test_get_carries_no_warnings_and_echoes_stored_value(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """GET returns flat CampaignResponse (no warnings) and echoes a NON-default value.

    Creating with 70 then reading back 70 proves the explicit `_campaign_to_response`
    mapping is wired — a missing mapping would silently return the 50 default and this
    NON-default assertion would fail. (D-14: GET carries no warnings.)
    """
    await _bind(async_db_session, test_workspace.id, "ndlg-get")
    agent = await test_agent_factory()
    create = await async_client.post(
        "/api/v1/campaigns",
        json=_base_payload(
            agent.id, test_folder.id, "NDLG get echo", max_new_dialogs_per_day=70
        ),
        headers=_auth_headers(valid_supabase_jwt, "ndlg-get"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["campaign"]["id"]

    r = await async_client.get(
        f"/api/v1/campaigns/{cid}",
        headers=_auth_headers(valid_supabase_jwt, "ndlg-get"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Flat CampaignResponse — value at top level, NOT nested under "campaign".
    assert "campaign" not in body
    assert "warnings" not in body
    assert body["max_new_dialogs_per_day"] == 70
