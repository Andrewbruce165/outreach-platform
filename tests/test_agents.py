"""Phase 3 — Agent CRUD API tests (AGNT-01..04).

Wave 0 RED — endpoints created in Task 2-5 of plan 03-02.
"""
import pytest
from sqlalchemy import text
from uuid import uuid4

pytestmark = pytest.mark.asyncio


# ─── Auth helper ───────────────────────────────────────────────────────────
async def _link_user_to_workspace(db, user_sub, workspace_id):
    """Create user_workspaces link so JWT auth resolves to existing workspace."""
    from app.models import UserWorkspace
    uw = UserWorkspace(supabase_user_id=user_sub, workspace_id=workspace_id, role="owner")
    db.add(uw)
    await db.commit()


# ─── AGNT-01: create ────────────────────────────────────────────────────────
async def test_create_agent_returns_201(async_client, async_db_session, valid_supabase_jwt, test_workspace):
    user_sub = f"user-create-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    token = valid_supabase_jwt(sub=user_sub)
    resp = await async_client.post(
        "/api/v1/agents",
        json={"name": "Sales Agent", "system_prompt": "be helpful"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Sales Agent"
    assert body["system_prompt"] == "be helpful"
    assert body["campaign_count"] == 0
    assert "id" in body


async def test_create_agent_workspace_scoped(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory):
    """Agent в другом workspace отдаёт 404 (security: no cross-tenant leak)."""
    # Create agent in test_workspace
    other_agent = await test_agent_factory(name="Other WS Agent")

    # Create a separate workspace + user, try to GET other_agent → must be 404
    from app.models import Workspace
    ws2 = Workspace(name="Workspace 2")
    async_db_session.add(ws2)
    await async_db_session.commit()
    user2 = f"user-cross-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user2, ws2.id)

    token = valid_supabase_jwt(sub=user2)
    resp = await async_client.get(
        f"/api/v1/agents/{other_agent.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.text


async def test_create_agent_duplicate_name_409(async_client, async_db_session, valid_supabase_jwt, test_workspace):
    user_sub = f"user-dup-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    token = valid_supabase_jwt(sub=user_sub)
    # First create
    r1 = await async_client.post(
        "/api/v1/agents",
        json={"name": "Dup Agent"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 201
    # Second create with same name → 409
    r2 = await async_client.post(
        "/api/v1/agents",
        json={"name": "Dup Agent"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "AGENT_NAME_DUPLICATE"


# ─── AGNT-02: fields ────────────────────────────────────────────────────────
async def test_create_agent_persists_all_fields(async_client, async_db_session, valid_supabase_jwt, test_workspace):
    user_sub = f"user-fields-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    token = valid_supabase_jwt(sub=user_sub)
    resp = await async_client.post(
        "/api/v1/agents",
        json={
            "name": "Full Agent",
            "system_prompt": "prompt",
            "rules": "be polite",
            # Phase 11 D-01: tone_preset replaces tone_of_voice/voice_baseline
            "tone_preset": "Friendly",
            "response_speed": "human",
            "faq": [{"question": "Q1", "answer": "A1"}],
            "company_info": "Co",
            "product_info": "Prod",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["system_prompt"] == "prompt"
    assert body["rules"] == "be polite"
    assert body["tone_preset"] == "Friendly"
    assert body["response_speed"] == "human"
    assert body["faq"] == [{"question": "Q1", "answer": "A1"}]
    assert body["company_info"] == "Co"
    assert body["product_info"] == "Prod"


async def test_faq_shape_validation(async_client, async_db_session, valid_supabase_jwt, test_workspace):
    """FAQ shape: list[{question, answer}] — wrong shape → 422."""
    user_sub = f"user-faq-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    token = valid_supabase_jwt(sub=user_sub)
    # Wrong shape: dict instead of list
    resp = await async_client.post(
        "/api/v1/agents",
        json={"name": "Bad FAQ", "faq": {"Q1": "A1"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_patch_faq_replaces_not_merges(async_client, async_db_session, valid_supabase_jwt, test_workspace):
    """Pitfall 7: PATCH faq = full replacement, not concat/merge."""
    user_sub = f"user-faq-replace-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    token = valid_supabase_jwt(sub=user_sub)
    r1 = await async_client.post(
        "/api/v1/agents",
        json={"name": "FAQ Test", "faq": [{"question": "Q1", "answer": "A1"}, {"question": "Q2", "answer": "A2"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    agent_id = r1.json()["id"]
    # PATCH with new array of length 1 — must REPLACE, not merge
    r2 = await async_client.patch(
        f"/api/v1/agents/{agent_id}",
        json={"faq": [{"question": "NewQ", "answer": "NewA"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["faq"] == [{"question": "NewQ", "answer": "NewA"}], \
        f"FAQ must be replaced, got: {body['faq']}"


# ─── AGNT-04: list / patch / delete ─────────────────────────────────────────
async def test_list_agents_with_campaign_count(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory):
    user_sub = f"user-list-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    await test_agent_factory(name="L1")
    await test_agent_factory(name="L2")
    token = valid_supabase_jwt(sub=user_sub)
    resp = await async_client.get("/api/v1/agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    # AgentListResponse shape: {agents: [...], total: N}
    assert "agents" in body
    assert body["total"] >= 2
    for a in body["agents"]:
        assert a["campaign_count"] == 0  # D-10 hardcoded


async def test_patch_agent_partial(async_client, async_db_session, valid_supabase_jwt, test_workspace):
    user_sub = f"user-patch-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    token = valid_supabase_jwt(sub=user_sub)
    r1 = await async_client.post(
        "/api/v1/agents",
        json={"name": "Patch Agent", "system_prompt": "old", "rules": "old rules"},
        headers={"Authorization": f"Bearer {token}"},
    )
    agent_id = r1.json()["id"]
    # Partial PATCH only system_prompt
    r2 = await async_client.patch(
        f"/api/v1/agents/{agent_id}",
        json={"system_prompt": "new"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["system_prompt"] == "new"
    assert body["rules"] == "old rules"  # preserved


async def test_delete_agent_sets_conversation_to_null(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory, test_sender_factory):
    """D-08: DELETE hard; FK conversations.ai_context_id → NULL."""
    user_sub = f"user-del1-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    agent = await test_agent_factory(name="Delete Me")
    sender = await test_sender_factory(slug="del-test-sender")
    # Create conversation pointing to this agent
    await async_db_session.execute(
        text("""
            INSERT INTO conversations (workspace_id, sender_id, contact_phone, ai_context_id, ai_enabled)
            VALUES (:wid, :sid, '+79999999999', :aid, true)
        """),
        {"wid": str(test_workspace.id), "sid": str(sender.id), "aid": str(agent.id)},
    )
    await async_db_session.commit()

    token = valid_supabase_jwt(sub=user_sub)
    resp = await async_client.delete(f"/api/v1/agents/{agent.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204

    # conversation должна остаться, но ai_context_id = NULL
    row = await async_db_session.execute(
        text("SELECT ai_context_id FROM conversations WHERE workspace_id = :wid"),
        {"wid": str(test_workspace.id)},
    )
    result = row.fetchone()
    assert result is not None
    assert result[0] is None, "conversation.ai_context_id must be NULL after agent delete (FK SET NULL)"


# NB: test_delete_agent_cascades_assignments was removed — the
# context_contact_assignments table it exercised was DROPPED by migration 016
# (Phase 4). Agent deletion now only needs to SET NULL conversation.ai_context_id,
# which is covered by the preceding test.


# ─── AGNT-04: duplicate ─────────────────────────────────────────────────────
async def test_duplicate_agent_auto_name(async_client, async_db_session, valid_supabase_jwt, test_workspace):
    """D-07: POST /agents/{id}/duplicate без body. Auto-name (copy) → (copy 2) → (copy 3)."""
    user_sub = f"user-dup-name-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    token = valid_supabase_jwt(sub=user_sub)
    r1 = await async_client.post(
        "/api/v1/agents",
        json={"name": "Original", "system_prompt": "p"},
        headers={"Authorization": f"Bearer {token}"},
    )
    orig_id = r1.json()["id"]
    # 1st duplicate → "Original (copy)"
    r2 = await async_client.post(f"/api/v1/agents/{orig_id}/duplicate", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 201
    assert r2.json()["name"] == "Original (copy)"
    # 2nd duplicate of original → "Original (copy 2)"
    r3 = await async_client.post(f"/api/v1/agents/{orig_id}/duplicate", headers={"Authorization": f"Bearer {token}"})
    assert r3.status_code == 201
    assert r3.json()["name"] == "Original (copy 2)"
    # 3rd → "Original (copy 3)"
    r4 = await async_client.post(f"/api/v1/agents/{orig_id}/duplicate", headers={"Authorization": f"Bearer {token}"})
    assert r4.status_code == 201
    assert r4.json()["name"] == "Original (copy 3)"


async def test_duplicate_race_handling(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory):
    """Pitfall 2: retry on IntegrityError when 2 parallel duplicate calls race."""
    # Simplified single-call sanity check (true race requires parallel runner) —
    # Just verify the endpoint doesn't 500 on a normal call.
    user_sub = f"user-dup-race-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    agent = await test_agent_factory(name="Race Source")
    token = valid_supabase_jwt(sub=user_sub)
    resp = await async_client.post(f"/api/v1/agents/{agent.id}/duplicate", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201
