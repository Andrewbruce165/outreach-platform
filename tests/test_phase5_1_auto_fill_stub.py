"""POST /api/v1/campaigns/auto-fill returns canned defaults (v1 stub) — UI-CAMPB-01.

UI-SPEC §5.5 AI co-pilot button: real LLM-driven auto-fill is deferred to v2
per RESEARCH §"Backend Gap Map" Campaigns row. This v1 stub exists so the UI
button stops looking broken; behaviour is intentionally deterministic.
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "autofill-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def test_auto_fill_returns_canned_defaults_with_empty_body(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Empty JSON body → canned defaults exactly as documented in plan 03."""
    await _bind(async_db_session, test_workspace.id, "u-af-empty")
    resp = await async_client.post(
        "/api/v1/campaigns/auto-fill",
        json={},
        headers=_auth_headers(valid_supabase_jwt, "u-af-empty"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "name": "Untitled campaign",
        "audience_hints": "",
        "primary_goal": "book_meeting",
        "success_criteria": "",
        "tools": [],
    }


async def test_auto_fill_accepts_brief_text_but_ignores_it(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Brief text is currently ignored (v1 stub) — still returns canned defaults.

    Wire format already accepts `brief` so the UI can start sending it now; v2
    implementation will switch behaviour without breaking clients.
    """
    await _bind(async_db_session, test_workspace.id, "u-af-brief")
    resp = await async_client.post(
        "/api/v1/campaigns/auto-fill",
        json={"brief": "We sell Telegram outreach for B2B SaaS"},
        headers=_auth_headers(valid_supabase_jwt, "u-af-brief"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["primary_goal"] == "book_meeting"
    assert body["name"] == "Untitled campaign"


async def test_auto_fill_requires_auth(async_client):
    """No bearer token → 401 (auth_dep enforced)."""
    resp = await async_client.post("/api/v1/campaigns/auto-fill", json={})
    assert resp.status_code == 401


async def test_auto_fill_response_shape_uses_v2_field_names(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Response keys match Pydantic CampaignCreate.v2_fields — UI can feed it back
    into POST /campaigns directly without renaming.
    """
    await _bind(async_db_session, test_workspace.id, "u-af-shape")
    resp = await async_client.post(
        "/api/v1/campaigns/auto-fill",
        json={},
        headers=_auth_headers(valid_supabase_jwt, "u-af-shape"),
    )
    assert resp.status_code == 200
    body = resp.json()
    # UI-SPEC §5.5: response is the start of a CampaignCreate payload.
    assert set(body.keys()) == {"name", "audience_hints", "primary_goal", "success_criteria", "tools"}
