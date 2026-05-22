"""Sender lock per-active-campaign (CAMP-04, D-03).

Covers:
- /start → 409 if any campaign_senders overlap with another running campaign in same workspace
- 409 conflict response contains [{sender_id, campaign_id, campaign_name}]
- /resume re-checks sender lock
- Two draft campaigns CAN share senders
- Done campaign does not lock senders
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _hdr(jwt_factory, sub: str) -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def _mk(client, jwt, uid, agent_id, folder_id, sender_ids, name):
    r = await client.post("/api/v1/campaigns", json={
        "name": name,
        "agent_id": str(agent_id),
        "folder_id": str(folder_id),
        "sender_ids": [str(s) for s in sender_ids],
        "message_template": "hi",
    }, headers=_hdr(jwt, uid))
    assert r.status_code == 201, r.text
    return r.json()


async def test_start_409_when_sender_in_other_running_campaign(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    s = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "lk-1")

    c1 = await _mk(async_client, valid_supabase_jwt, "lk-1",
                   agent.id, test_folder.id, [s.id], "LK1A")
    c2 = await _mk(async_client, valid_supabase_jwt, "lk-1",
                   agent.id, test_folder.id, [s.id], "LK1B")

    r1 = await async_client.post(f"/api/v1/campaigns/{c1['id']}/start",
                                 headers=_hdr(valid_supabase_jwt, "lk-1"))
    assert r1.status_code == 200

    r2 = await async_client.post(f"/api/v1/campaigns/{c2['id']}/start",
                                 headers=_hdr(valid_supabase_jwt, "lk-1"))
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "SENDER_LOCK_CONFLICT"


async def test_409_conflict_response_has_sender_id_campaign_id_campaign_name_list(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    s = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "lk-2")

    c1 = await _mk(async_client, valid_supabase_jwt, "lk-2",
                   agent.id, test_folder.id, [s.id], "LK2A")
    c2 = await _mk(async_client, valid_supabase_jwt, "lk-2",
                   agent.id, test_folder.id, [s.id], "LK2B")

    await async_client.post(f"/api/v1/campaigns/{c1['id']}/start",
                            headers=_hdr(valid_supabase_jwt, "lk-2"))
    r = await async_client.post(f"/api/v1/campaigns/{c2['id']}/start",
                                headers=_hdr(valid_supabase_jwt, "lk-2"))
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "SENDER_LOCK_CONFLICT"
    conflicts = detail["conflicts"]
    assert isinstance(conflicts, list)
    assert len(conflicts) >= 1
    assert conflicts[0]["sender_id"] == str(s.id)
    assert conflicts[0]["campaign_id"] == c1["id"]
    assert conflicts[0]["campaign_name"] == "LK2A"


async def test_resume_re_checks_sender_lock(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """D-04: paused→running must re-check sender lock — другая кампания могла занять sender."""
    agent = await test_agent_factory()
    s = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "lk-3")

    c1 = await _mk(async_client, valid_supabase_jwt, "lk-3",
                   agent.id, test_folder.id, [s.id], "LK3A")
    c2 = await _mk(async_client, valid_supabase_jwt, "lk-3",
                   agent.id, test_folder.id, [s.id], "LK3B")

    # start c1 → pause c1 (sender becomes available)
    await async_client.post(f"/api/v1/campaigns/{c1['id']}/start",
                            headers=_hdr(valid_supabase_jwt, "lk-3"))
    await async_client.post(f"/api/v1/campaigns/{c1['id']}/pause",
                            headers=_hdr(valid_supabase_jwt, "lk-3"))
    # start c2 (now holding sender)
    r2 = await async_client.post(f"/api/v1/campaigns/{c2['id']}/start",
                                 headers=_hdr(valid_supabase_jwt, "lk-3"))
    assert r2.status_code == 200
    # resume c1 → should be 409
    r1 = await async_client.post(f"/api/v1/campaigns/{c1['id']}/resume",
                                 headers=_hdr(valid_supabase_jwt, "lk-3"))
    assert r1.status_code == 409
    assert r1.json()["detail"]["code"] == "SENDER_LOCK_CONFLICT"


async def test_two_draft_campaigns_can_share_senders(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    s = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "lk-4")
    c1 = await _mk(async_client, valid_supabase_jwt, "lk-4",
                   agent.id, test_folder.id, [s.id], "LK4A")
    c2 = await _mk(async_client, valid_supabase_jwt, "lk-4",
                   agent.id, test_folder.id, [s.id], "LK4B")
    assert c1["status"] == "draft"
    assert c2["status"] == "draft"
    # Both draft, sharing sender — OK


async def test_done_campaign_does_not_block_sender_reuse(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    s = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "lk-5")

    c1 = await _mk(async_client, valid_supabase_jwt, "lk-5",
                   agent.id, test_folder.id, [s.id], "LK5A")
    c2 = await _mk(async_client, valid_supabase_jwt, "lk-5",
                   agent.id, test_folder.id, [s.id], "LK5B")
    await async_client.post(f"/api/v1/campaigns/{c1['id']}/start",
                            headers=_hdr(valid_supabase_jwt, "lk-5"))
    await async_client.post(f"/api/v1/campaigns/{c1['id']}/finish",
                            headers=_hdr(valid_supabase_jwt, "lk-5"))
    # c1 now done — c2 should start fine
    r = await async_client.post(f"/api/v1/campaigns/{c2['id']}/start",
                                headers=_hdr(valid_supabase_jwt, "lk-5"))
    assert r.status_code == 200
    assert r.json()["status"] == "running"
