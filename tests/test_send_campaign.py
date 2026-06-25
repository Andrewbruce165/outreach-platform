"""Plan 04-04 Task 5: send.py rewrite — campaign_id body + workspace isolation + template render.

Covers D-16:
- POST /api/v1/send body имеет campaign_id, NOT ai_context_id.
- Agent выводится через campaign (через _upsert_conversation в queue.py).
- Workspace-isolated (404 if campaign not in caller's workspace).
- Template rendering при отсутствии request.message.
"""

import pytest
from sqlalchemy import text

from app.schemas import SendMessageRequest

pytestmark = pytest.mark.asyncio


async def test_send_endpoint_accepts_campaign_id_not_ai_context_id():
    """D-16: SendMessageRequest body uses campaign_id (NOT ai_context_id)."""
    fields = SendMessageRequest.model_fields
    assert "campaign_id" in fields
    assert "ai_context_id" not in fields
    # message is now Optional (renders from template if missing).
    assert not fields["message"].is_required()


async def test_send_resolves_agent_from_campaign(
    async_client,
    async_db_session,
    valid_supabase_jwt,
    test_workspace,
    test_running_campaign_factory,
):
    """Agent = SELECT agent_id FROM campaigns WHERE id=:cid (via _upsert_conversation)."""
    # Bind user to workspace for AuthDep.
    user_id = "test-user-resolves-agent"
    await async_db_session.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'admin') ON CONFLICT DO NOTHING
    """), {"uid": user_id, "wid": str(test_workspace.id)})
    await async_db_session.commit()

    camp, senders = await test_running_campaign_factory(sender_count=1)

    token = valid_supabase_jwt(sub=user_id)
    response = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "recipient_phone": "+71234567890",
            "recipient_name": "Тест",
            "message": "Custom text — skip template render",
            "sender_slug": senders[0].slug,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["queued"] is True

    # Verify queue row exists with campaign_id.
    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = '+71234567890'
    """), {"cid": str(camp["id"])})).scalar()
    assert cnt == 1


async def test_send_with_other_workspace_campaign_404(
    async_client,
    async_db_session,
    valid_supabase_jwt,
    test_running_campaign_factory,
):
    """Workspace isolation: campaign из чужого workspace → 404."""
    from uuid import uuid4

    # Caller in workspace A.
    other_user = "user-with-no-camp-workspace"
    other_wid = uuid4()
    await async_db_session.execute(text("""
        INSERT INTO workspaces (id, name) VALUES (:id, 'OtherWs')
    """), {"id": str(other_wid)})
    await async_db_session.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'admin')
    """), {"uid": other_user, "wid": str(other_wid)})
    await async_db_session.commit()

    # Campaign in workspace B (via factory).
    camp, _ = await test_running_campaign_factory(sender_count=1)

    token = valid_supabase_jwt(sub=other_user)
    response = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "recipient_phone": "+79991111111",
            "message": "Hi",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["code"] == "CAMPAIGN_NOT_FOUND"


async def test_send_via_workspace_api_key(
    async_client,
    async_db_session,
    test_workspace,
    test_running_campaign_factory,
):
    """n8n push через X-Workspace-Key — тот же endpoint (dual auth)."""
    # Issue API key for workspace (bcrypt hash + 12-char prefix, per CR-09).
    import asyncio
    import bcrypt
    raw_key = "wsk_test_12345_send_campaign"
    prefix = raw_key[:12]
    hash_bytes = await asyncio.to_thread(
        bcrypt.hashpw, raw_key.encode(), bcrypt.gensalt(rounds=4)
    )
    await async_db_session.execute(text("""
        INSERT INTO workspace_api_keys (workspace_id, bcrypt_hash, name, prefix)
        VALUES (:wid, :h, 'test', :prefix)
    """), {"wid": str(test_workspace.id), "h": hash_bytes.decode(), "prefix": prefix})
    await async_db_session.commit()

    camp, senders = await test_running_campaign_factory(sender_count=1)

    response = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "recipient_phone": "+71113334455",
            "message": "API-key path",
            "sender_slug": senders[0].slug,
        },
        headers={"X-Workspace-Key": raw_key},
    )
    assert response.status_code == 200, response.text


async def test_send_renders_template_when_text_not_provided(
    async_client,
    async_db_session,
    valid_supabase_jwt,
    test_workspace,
    test_running_campaign_factory,
):
    """Если в body нет message — render_template(campaign.message_template, contact)."""
    user_id = "user-render-test"
    await async_db_session.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'admin') ON CONFLICT DO NOTHING
    """), {"uid": user_id, "wid": str(test_workspace.id)})

    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        message_template="Hi {{name}}, welcome!",
    )

    # Create contact in workspace so render uses real data.
    target_phone = "+71112223344"
    await async_db_session.execute(text("""
        INSERT INTO contacts (workspace_id, folder_id, phone, full_name, tg_status)
        VALUES (:wid, :fid, :p, 'Alice', 'registered')
    """), {
        "wid": str(test_workspace.id),
        "fid": str(camp["folder_id"]),
        "p": target_phone,
    })
    await async_db_session.commit()

    token = valid_supabase_jwt(sub=user_id)
    response = await async_client.post(
        "/api/v1/send",
        json={
            "campaign_id": str(camp["id"]),
            "recipient_phone": target_phone,
            "sender_slug": senders[0].slug,
            # No "message" — should render from template.
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text

    queue_text = (await async_db_session.execute(text("""
        SELECT message_text FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :p
    """), {"cid": str(camp["id"]), "p": target_phone})).scalar()
    assert queue_text == "Hi Alice, welcome!"
