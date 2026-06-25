"""Phase 3 — POST /api/v1/send (rewrite under AuthDep + campaign_id body, D-16).

Post username-outreach refactor the /send body carries `campaign_id` (NOT
`ai_context_id`); the agent is resolved through the campaign. Cross-workspace
isolation flows through the campaign lookup (404 CAMPAIGN_NOT_FOUND), and a
single campaign's agent is shared across all senders attached to it (AGNT-03).
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


async def test_send_cross_workspace_agent_404(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_running_campaign_factory):
    """Phase 3 D-06 (post D-16): POST /send с campaign из другого workspace → 404.

    The agent is resolved through the campaign, so cross-workspace agent
    protection now flows through the campaign lookup.
    """
    # campaign (с агентом) в test_workspace
    camp, _ = await test_running_campaign_factory(sender_count=1)

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
            "campaign_id": str(camp["id"]),  # принадлежит ws1, не ws2
            "recipient_phone": "+79991234567",
            "message": "hi",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, f"expected 404 for cross-ws campaign, got {resp.status_code}: {resp.text}"
    assert resp.json()["detail"]["code"] == "CAMPAIGN_NOT_FOUND"


async def test_same_agent_id_works_for_multiple_senders(async_client, async_db_session, valid_supabase_jwt, test_workspace, test_running_campaign_factory):
    """AGNT-03: один и тот же агент (через campaign) успешно используется
    с разными sender'ами, attached к этой кампании."""
    user_sub = f"user-multi-send-{uuid4()}"
    await _link_user_to_workspace(async_db_session, user_sub, test_workspace.id)
    # Single campaign → single agent, two senders attached.
    camp, senders = await test_running_campaign_factory(name="Multi Sender Camp", sender_count=2)
    sender_a, sender_b = senders

    token = valid_supabase_jwt(sub=user_sub)
    # send via sender_a
    r1 = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "sender_slug": sender_a.slug,
            "recipient_phone": "+79991111111",
            "message": "msg1",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200, r1.text
    # send via sender_b — SAME campaign (and thus SAME agent) reused
    r2 = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "sender_slug": sender_b.slug,
            "recipient_phone": "+79992222222",
            "message": "msg2",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200, r2.text
