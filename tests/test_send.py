"""Phase 3 — POST /api/v1/send (rewrite under AuthDep + explicit ai_context_id).

Wave 0 RED — endpoint rewritten in Task 4.
"""
import pytest
from uuid import uuid4

pytestmark = pytest.mark.asyncio


async def _link_user_to_workspace(db, user_sub, workspace_id):
    from app.models import UserWorkspace
    uw = UserWorkspace(supabase_user_id=user_sub, workspace_id=workspace_id, role="owner")
    db.add(uw)
    await db.commit()


async def test_send_requires_ai_context_id(async_client, async_db_session, valid_supabase_jwt, test_workspace):
    """Phase 3 D-06: POST /send без ai_context_id в body → 422."""
    user_sub = f"user-send-1-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    token = valid_supabase_jwt(sub=user_sub)
    resp = await async_client.post(
        "/api/v1/send",
        json={"recipient_phone": "+79991234567", "message": "hi"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, f"expected 422 for missing ai_context_id, got {resp.status_code}: {resp.text}"


async def test_send_cross_workspace_agent_404(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory):
    """Phase 3 D-06: POST /send с ai_context_id из другого workspace → 404."""
    # agent в test_workspace
    agent = await test_agent_factory(name="Cross WS Agent")

    # user в другом workspace
    from app.models import Workspace
    ws2 = Workspace(name="Other WS for send test")
    async_db_session.add(ws2)
    await async_db_session.commit()
    user_sub = f"user-send-cross-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, ws2.id)

    token = valid_supabase_jwt(sub=user_sub)
    resp = await async_client.post(
        "/api/v1/send",
        json={
            "ai_context_id": str(agent.id),  # принадлежит ws1, не ws2
            "recipient_phone": "+79991234567",
            "message": "hi",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, f"expected 404 for cross-ws agent, got {resp.status_code}: {resp.text}"


async def test_same_agent_id_works_for_multiple_senders(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_agent_factory, test_sender_factory):
    """AGNT-03: один и тот же agent_id успешно используется с разными sender'ами."""
    user_sub = f"user-multi-send-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    agent = await test_agent_factory(name="Multi Sender Agent")
    sender_a = await test_sender_factory(slug="sender-a-multi", lifecycle_status="active", auth_status="ok")
    sender_b = await test_sender_factory(slug="sender-b-multi", lifecycle_status="active", auth_status="ok")

    token = valid_supabase_jwt(sub=user_sub)
    # send via sender_a
    r1 = await async_client.post(
        "/api/v1/send",
        json={
            "ai_context_id": str(agent.id),
            "sender_slug": sender_a.slug,
            "recipient_phone": "+79991111111",
            "message": "msg1",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200, r1.text
    # send via sender_b — SAME agent_id reused
    r2 = await async_client.post(
        "/api/v1/send",
        json={
            "ai_context_id": str(agent.id),
            "sender_slug": sender_b.slug,
            "recipient_phone": "+79992222222",
            "message": "msg2",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200, r2.text
