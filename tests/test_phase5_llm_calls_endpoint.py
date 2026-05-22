"""Phase 5 ANLX-05 — GET /api/v1/conversations/{id}/llm-calls endpoint.

Covers 8 tests from plan 05-03 behaviour list:
  1. auth required (401 without credentials)
  2. cross-workspace 404 (T-05-03-WS-ISOLATION)
  3. happy path (3 rows seeded → response total=3)
  4. sorted DESC created_at
  5. pagination (limit/offset)
  6. cross-workspace defence-in-depth — row in workspace B not leaked
  7. prompt JSONB returned as dict
  8. empty list (no llm_calls for conv)
"""

import json
import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "llmcalls-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def _seed_llm_call(
    async_db_session,
    conv,
    model: str = "gpt-4o-mini",
    response_text: str = "reply",
    prompt: dict | None = None,
):
    """Insert one llm_calls row directly for testing the read endpoint."""
    if prompt is None:
        prompt = {"messages": [{"role": "system", "content": "test"}]}
    await async_db_session.execute(text("""
        INSERT INTO llm_calls (
            workspace_id, conversation_id, model, prompt, response_text
        ) VALUES (
            :wid, :cid, :model, :prompt::jsonb, :rt
        )
    """), {
        "wid": str(conv["workspace_id"]),
        "cid": str(conv["id"]),
        "model": model,
        "prompt": json.dumps(prompt),
        "rt": response_text,
    })
    await async_db_session.commit()


# ── Test 1: auth gate ─────────────────────────────────────────────────────────


async def test_llm_calls_endpoint_auth_required(async_client):
    """GET without Authorization header → 401."""
    fake_cid = _uuid.uuid4()
    r = await async_client.get(f"/api/v1/conversations/{fake_cid}/llm-calls")
    assert r.status_code == 401


# ── Test 2: cross-workspace 404 (T-05-03-WS-ISOLATION) ────────────────────────


async def test_llm_calls_cross_workspace_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """User from workspace A → GET /conversations/{B-conv-id}/llm-calls → 404."""
    from app.models import Workspace

    # Conversation lives in test_workspace.
    conv = await test_conversation_factory(contact_phone="+79991115001")

    # Bind a different user to a *different* workspace.
    other = Workspace(name="OtherLLMCallsWS")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-llm-cross")

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/llm-calls",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-cross"),
    )
    assert r.status_code == 404


# ── Test 3: happy path ────────────────────────────────────────────────────────


async def test_llm_calls_happy_path(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """3 llm_calls seeded → endpoint returns 3 rows + total=3."""
    conv = await test_conversation_factory(contact_phone="+79991115002")
    await _bind(async_db_session, test_workspace.id, "u-llm-happy")
    await _seed_llm_call(async_db_session, conv, response_text="reply 1")
    await _seed_llm_call(async_db_session, conv, response_text="reply 2")
    await _seed_llm_call(async_db_session, conv, response_text="reply 3")

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/llm-calls",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-happy"),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert len(data["llm_calls"]) == 3


# ── Test 4: sorted DESC created_at ────────────────────────────────────────────


async def test_llm_calls_sorted_desc_created_at(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Newest first — first row has the latest created_at."""
    conv = await test_conversation_factory(contact_phone="+79991115003")
    await _bind(async_db_session, test_workspace.id, "u-llm-sort")
    await _seed_llm_call(async_db_session, conv, response_text="oldest")
    await _seed_llm_call(async_db_session, conv, response_text="middle")
    await _seed_llm_call(async_db_session, conv, response_text="newest")

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/llm-calls",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-sort"),
    )
    data = r.json()
    items = data["llm_calls"]
    assert items[0]["response_text"] == "newest"
    assert items[2]["response_text"] == "oldest"
    # Strictly descending: each timestamp >= next
    assert items[0]["created_at"] >= items[1]["created_at"] >= items[2]["created_at"]


# ── Test 5: pagination ────────────────────────────────────────────────────────


async def test_llm_calls_pagination(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """5 rows seeded → ?limit=2&offset=1 returns 2 items, total=5."""
    conv = await test_conversation_factory(contact_phone="+79991115004")
    await _bind(async_db_session, test_workspace.id, "u-llm-paginate")
    for i in range(5):
        await _seed_llm_call(async_db_session, conv, response_text=f"r{i}")

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/llm-calls?limit=2&offset=1",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-paginate"),
    )
    data = r.json()
    assert data["total"] == 5
    assert len(data["llm_calls"]) == 2


# ── Test 6: defence-in-depth — row in workspace B not leaked ─────────────────


async def test_llm_calls_defence_in_depth_workspace_b_not_leaked(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Both prequery 404 + WHERE workspace_id filter — explicit defence-in-depth.

    Seed llm_calls in workspace B; user from workspace A tries to GET. Even
    if a forged URL ever bypassed the prequery (defence-in-depth), the inner
    SELECT would filter out the workspace-B row. Endpoint returns 404 from
    prequery; row count for the conv stays accessible only to workspace B.
    """
    from app.models import Workspace

    # Conversation + llm_call in test_workspace (workspace B for our purposes).
    conv_b = await test_conversation_factory(contact_phone="+79991115005")
    await _seed_llm_call(async_db_session, conv_b, response_text="secret-B")

    # User A in a different workspace tries to read it.
    workspace_a = Workspace(name="WorkspaceALLMCalls")
    async_db_session.add(workspace_a)
    await async_db_session.commit()
    await async_db_session.refresh(workspace_a)
    await _bind(async_db_session, workspace_a.id, "u-llm-defence")

    r = await async_client.get(
        f"/api/v1/conversations/{conv_b['id']}/llm-calls",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-defence"),
    )
    assert r.status_code == 404
    # Verify the row IS still in DB — just not accessible from workspace A.
    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM llm_calls WHERE conversation_id = :cid
    """), {"cid": str(conv_b["id"])})).scalar()
    assert cnt == 1


# ── Test 7: prompt JSONB returned as dict ────────────────────────────────────


async def test_llm_calls_prompt_jsonb_returned_as_dict(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """JSONB column deserialized into dict by Pydantic model."""
    conv = await test_conversation_factory(contact_phone="+79991115006")
    await _bind(async_db_session, test_workspace.id, "u-llm-jsonb")
    custom_prompt = {
        "messages": [{"role": "user", "content": "hi"}],
        "model": "gpt-4o-mini",
        "temperature": 0.7,
    }
    await _seed_llm_call(async_db_session, conv, prompt=custom_prompt)

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/llm-calls",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-jsonb"),
    )
    data = r.json()
    assert len(data["llm_calls"]) == 1
    prompt = data["llm_calls"][0]["prompt"]
    assert isinstance(prompt, dict)
    assert "messages" in prompt
    assert prompt["model"] == "gpt-4o-mini"


# ── Test 8: empty list ────────────────────────────────────────────────────────


async def test_llm_calls_empty_list(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Conv exists in workspace but no llm_calls → 200 with empty list + total=0."""
    conv = await test_conversation_factory(contact_phone="+79991115007")
    await _bind(async_db_session, test_workspace.id, "u-llm-empty")

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/llm-calls",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-empty"),
    )
    assert r.status_code == 200
    data = r.json()
    assert data == {"llm_calls": [], "total": 0}
