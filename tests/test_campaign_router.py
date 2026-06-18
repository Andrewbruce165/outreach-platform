"""Campaign router CRUD + lifecycle + duplicate + cross-router blocks.

Covers:
- GET list / single (workspace-isolated)
- attached_senders with locked_by_campaign_id (Q4)
- is_exhausted computed
- PATCH partial-update
- DELETE 409 on running, 204 on draft/paused/done (Q1 SET NULL on queue history)
- Lifecycle: draft→running, running→paused, paused→running, →done, terminal done
- POST /duplicate: row + senders, NOT queue/cca (Q2)
- TODO(phase-4) closures: agent/folder/sender DELETE blocks, agent campaign_count
- Sender PATCH lifecycle_status block when sender in running campaign
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "router-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def _make_campaign(client, jwt, uid, agent_id, folder_id, sender_ids=None, name="Cmp"):
    payload = {
        "name": name,
        "agent_id": str(agent_id),
        "folder_id": str(folder_id),
        "sender_ids": [str(s) for s in (sender_ids or [])],
        "message_template": "Hi {{name}}",
    }
    r = await client.post("/api/v1/campaigns", json=payload,
                          headers={"Authorization": f"Bearer {jwt(sub=uid)}"})
    assert r.status_code == 201, r.text
    return r.json()


async def test_list_campaigns_workspace_isolated(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    from app.models import Workspace, AIContext, Folder

    agent = await test_agent_factory()
    await _bind(async_db_session, test_workspace.id, "u-list")
    await _make_campaign(async_client, valid_supabase_jwt, "u-list",
                         agent.id, test_folder.id, name="L1")

    # other workspace + campaign
    other = Workspace(name="OtherL")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    o_agent = AIContext(workspace_id=other.id, name="OA")
    o_folder = Folder(workspace_id=other.id, name="OF")
    async_db_session.add_all([o_agent, o_folder])
    await async_db_session.commit()
    await async_db_session.refresh(o_agent)
    await async_db_session.refresh(o_folder)

    await _bind(async_db_session, other.id, "u-other")
    await _make_campaign(async_client, valid_supabase_jwt, "u-other",
                         o_agent.id, o_folder.id, name="Other1")

    r = await async_client.get("/api/v1/campaigns", headers=_auth_headers(valid_supabase_jwt, "u-list"))
    assert r.status_code == 200
    names = [c["name"] for c in r.json()["items"]]
    assert "L1" in names
    assert "Other1" not in names


async def test_get_campaign_returns_is_exhausted_computed(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    agent = await test_agent_factory()
    await _bind(async_db_session, test_workspace.id, "u-ex")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-ex",
                             agent.id, test_folder.id, name="ExBox")
    r = await async_client.get(f"/api/v1/campaigns/{c['id']}",
                               headers=_auth_headers(valid_supabase_jwt, "u-ex"))
    assert r.status_code == 200
    body = r.json()
    assert "is_exhausted" in body
    # No contacts in folder → exhausted is True
    assert body["is_exhausted"] is True


async def test_get_campaign_returns_attached_senders_with_locked_flag(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """Q4: attached_senders include locked_by_campaign_id if sender in another running camp."""
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-lock")

    c1 = await _make_campaign(async_client, valid_supabase_jwt, "u-lock",
                              agent.id, test_folder.id, sender_ids=[sender.id], name="L1Lock")
    c2 = await _make_campaign(async_client, valid_supabase_jwt, "u-lock",
                              agent.id, test_folder.id, sender_ids=[sender.id], name="L2Lock")
    # Start c1 → running
    r_start = await async_client.post(f"/api/v1/campaigns/{c1['id']}/start",
                                      headers=_auth_headers(valid_supabase_jwt, "u-lock"))
    assert r_start.status_code == 200, r_start.text

    r = await async_client.get(f"/api/v1/campaigns/{c2['id']}",
                               headers=_auth_headers(valid_supabase_jwt, "u-lock"))
    assert r.status_code == 200
    attached = r.json()["attached_senders"]
    assert len(attached) == 1
    assert attached[0]["sender_id"] == str(sender.id)
    assert attached[0]["locked_by_campaign_id"] == c1["id"]


async def test_patch_campaign_partial_update(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    agent = await test_agent_factory()
    await _bind(async_db_session, test_workspace.id, "u-pa")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-pa",
                             agent.id, test_folder.id, name="Patchable")
    r = await async_client.patch(f"/api/v1/campaigns/{c['id']}",
                                 json={"description": "new desc"},
                                 headers=_auth_headers(valid_supabase_jwt, "u-pa"))
    assert r.status_code == 200
    assert r.json()["description"] == "new desc"


async def test_recontact_fields_default_and_roundtrip(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """026: allow_recontact defaults to false/30 on create, and PATCH round-trips
    both fields back through the response."""
    agent = await test_agent_factory()
    await _bind(async_db_session, test_workspace.id, "u-rc")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-rc",
                             agent.id, test_folder.id, name="Recontactable")
    # Defaults present in response.
    assert c["allow_recontact"] is False
    assert c["recontact_min_age_days"] == 30

    r = await async_client.patch(
        f"/api/v1/campaigns/{c['id']}",
        json={"allow_recontact": True, "recontact_min_age_days": 45},
        headers=_auth_headers(valid_supabase_jwt, "u-rc"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allow_recontact"] is True
    assert body["recontact_min_age_days"] == 45


async def test_recontact_min_age_days_validation(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """026: recontact_min_age_days is bounded (ge=1, le=365) — 0 is rejected."""
    agent = await test_agent_factory()
    await _bind(async_db_session, test_workspace.id, "u-rcv")
    r = await async_client.post(
        "/api/v1/campaigns",
        json={
            "name": "BadAge", "agent_id": str(agent.id),
            "folder_id": str(test_folder.id), "sender_ids": [],
            "message_template": "Hi", "recontact_min_age_days": 0,
        },
        headers=_auth_headers(valid_supabase_jwt, "u-rcv"),
    )
    assert r.status_code == 422


async def test_delete_running_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-del-run")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-del-run",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="DelRun")
    await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                            headers=_auth_headers(valid_supabase_jwt, "u-del-run"))
    r = await async_client.delete(f"/api/v1/campaigns/{c['id']}",
                                  headers=_auth_headers(valid_supabase_jwt, "u-del-run"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "CAMPAIGN_RUNNING"


async def test_delete_draft_204_hard_delete(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    agent = await test_agent_factory()
    await _bind(async_db_session, test_workspace.id, "u-del-draft")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-del-draft",
                             agent.id, test_folder.id, name="DelDraft")
    r = await async_client.delete(f"/api/v1/campaigns/{c['id']}",
                                  headers=_auth_headers(valid_supabase_jwt, "u-del-draft"))
    assert r.status_code == 204
    # Confirm gone
    r2 = await async_client.get(f"/api/v1/campaigns/{c['id']}",
                                headers=_auth_headers(valid_supabase_jwt, "u-del-draft"))
    assert r2.status_code == 404


async def test_delete_done_204_hard_delete(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-del-done")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-del-done",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="DelDone")
    await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                            headers=_auth_headers(valid_supabase_jwt, "u-del-done"))
    await async_client.post(f"/api/v1/campaigns/{c['id']}/finish",
                            headers=_auth_headers(valid_supabase_jwt, "u-del-done"))
    r = await async_client.delete(f"/api/v1/campaigns/{c['id']}",
                                  headers=_auth_headers(valid_supabase_jwt, "u-del-done"))
    assert r.status_code == 204


async def test_delete_done_keeps_queue_history_via_set_null(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """Q1: hard delete of done campaign preserves message_queue rows via SET NULL FK."""
    import uuid
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-del-hist")

    c = await _make_campaign(async_client, valid_supabase_jwt, "u-del-hist",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="DelHist")
    await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                            headers=_auth_headers(valid_supabase_jwt, "u-del-hist"))

    # Inject a queue row tied to this campaign
    qid = str(uuid.uuid4())
    await async_db_session.execute(text("""
        INSERT INTO message_queue (id, workspace_id, sender_id, campaign_id, item_type, status, recipient_phone, message_text)
        VALUES (:qid, :wid, :sid, :cid, 'message', 'sent', '+79990000777', 'done')
    """), {"qid": qid, "wid": str(test_workspace.id), "sid": str(sender.id), "cid": c["id"]})
    await async_db_session.commit()

    await async_client.post(f"/api/v1/campaigns/{c['id']}/finish",
                            headers=_auth_headers(valid_supabase_jwt, "u-del-hist"))
    r = await async_client.delete(f"/api/v1/campaigns/{c['id']}",
                                  headers=_auth_headers(valid_supabase_jwt, "u-del-hist"))
    assert r.status_code == 204

    row = (await async_db_session.execute(text(
        "SELECT campaign_id FROM message_queue WHERE id = :qid"
    ), {"qid": qid})).first()
    assert row is not None, "queue row deleted — should be SET NULL"
    assert row[0] is None


async def test_delete_cancels_pending_queue_items(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """Hard delete cancels still-pending queue items (no zombie 'pending' rows
    left with a dangling NULL campaign_id). 'sent' rows keep SET-NULL history."""
    import uuid
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-del-pend")

    c = await _make_campaign(async_client, valid_supabase_jwt, "u-del-pend",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="DelPend")
    await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                            headers=_auth_headers(valid_supabase_jwt, "u-del-pend"))

    pending_id = str(uuid.uuid4())
    sent_id = str(uuid.uuid4())
    await async_db_session.execute(text("""
        INSERT INTO message_queue (id, workspace_id, sender_id, campaign_id, item_type, status, recipient_phone, message_text)
        VALUES (:pid, :wid, :sid, :cid, 'message', 'pending', '+79990000888', 'p'),
               (:sid2, :wid, :sid, :cid, 'message', 'sent', '+79990000999', 's')
    """), {"pid": pending_id, "sid2": sent_id, "wid": str(test_workspace.id),
           "sid": str(sender.id), "cid": c["id"]})
    await async_db_session.commit()

    await async_client.post(f"/api/v1/campaigns/{c['id']}/finish",
                            headers=_auth_headers(valid_supabase_jwt, "u-del-pend"))
    r = await async_client.delete(f"/api/v1/campaigns/{c['id']}",
                                  headers=_auth_headers(valid_supabase_jwt, "u-del-pend"))
    assert r.status_code == 204

    rows = (await async_db_session.execute(text(
        "SELECT id, status, campaign_id FROM message_queue WHERE id = ANY(:ids)"
    ), {"ids": [pending_id, sent_id]})).all()
    by_id = {str(row[0]): (row[1], row[2]) for row in rows}
    # pending → cancelled on finish (campaign_id then nulled by delete FK)
    assert by_id[pending_id][0] == "cancelled"
    # sent → untouched status, history preserved via SET NULL
    assert by_id[sent_id][0] == "sent"
    assert by_id[sent_id][1] is None


async def test_lifecycle_draft_to_running_via_start(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-lf1")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-lf1",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="LF1")
    r = await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                                headers=_auth_headers(valid_supabase_jwt, "u-lf1"))
    assert r.status_code == 200
    assert r.json()["status"] == "running"


async def test_lifecycle_running_to_paused(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-lf2")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-lf2",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="LF2")
    await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                            headers=_auth_headers(valid_supabase_jwt, "u-lf2"))
    r = await async_client.post(f"/api/v1/campaigns/{c['id']}/pause",
                                headers=_auth_headers(valid_supabase_jwt, "u-lf2"))
    assert r.status_code == 200
    assert r.json()["status"] == "paused"


async def test_lifecycle_paused_to_running_via_resume(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-lf3")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-lf3",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="LF3")
    await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                            headers=_auth_headers(valid_supabase_jwt, "u-lf3"))
    await async_client.post(f"/api/v1/campaigns/{c['id']}/pause",
                            headers=_auth_headers(valid_supabase_jwt, "u-lf3"))
    r = await async_client.post(f"/api/v1/campaigns/{c['id']}/resume",
                                headers=_auth_headers(valid_supabase_jwt, "u-lf3"))
    assert r.status_code == 200
    assert r.json()["status"] == "running"


async def test_lifecycle_running_to_done_via_finish(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-lf4")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-lf4",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="LF4")
    await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                            headers=_auth_headers(valid_supabase_jwt, "u-lf4"))
    r = await async_client.post(f"/api/v1/campaigns/{c['id']}/finish",
                                headers=_auth_headers(valid_supabase_jwt, "u-lf4"))
    assert r.status_code == 200
    assert r.json()["status"] == "done"


async def test_lifecycle_done_terminal_no_back(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-lf5")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-lf5",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="LF5")
    await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                            headers=_auth_headers(valid_supabase_jwt, "u-lf5"))
    await async_client.post(f"/api/v1/campaigns/{c['id']}/finish",
                            headers=_auth_headers(valid_supabase_jwt, "u-lf5"))
    for action in ["start", "pause", "resume"]:
        r = await async_client.post(f"/api/v1/campaigns/{c['id']}/{action}",
                                    headers=_auth_headers(valid_supabase_jwt, "u-lf5"))
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "INVALID_TRANSITION"


async def test_lifecycle_draft_to_done_forbidden(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    agent = await test_agent_factory()
    await _bind(async_db_session, test_workspace.id, "u-lf6")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-lf6",
                             agent.id, test_folder.id, name="LF6")
    r = await async_client.post(f"/api/v1/campaigns/{c['id']}/finish",
                                headers=_auth_headers(valid_supabase_jwt, "u-lf6"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "INVALID_TRANSITION"


async def test_start_requires_at_least_one_sender_422(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    agent = await test_agent_factory()
    await _bind(async_db_session, test_workspace.id, "u-nosen")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-nosen",
                             agent.id, test_folder.id, name="NoSenders")
    r = await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                                headers=_auth_headers(valid_supabase_jwt, "u-nosen"))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "NO_SENDERS_ATTACHED"


async def test_duplicate_endpoint_copies_row_and_senders_not_queue_assignments(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """Q2: /duplicate copies row + senders, NOT queue items nor cca."""
    import uuid as _uuid

    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-dup")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-dup",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="DupBase")
    # Inject queue + cca rows tied to the original
    await async_db_session.execute(text("""
        INSERT INTO message_queue (id, workspace_id, sender_id, campaign_id, item_type, status, recipient_phone, message_text)
        VALUES (:qid, :wid, :sid, :cid, 'message', 'pending', '+79991110000', 'hi')
    """), {"qid": str(_uuid.uuid4()), "wid": str(test_workspace.id),
           "sid": str(sender.id), "cid": c["id"]})
    await async_db_session.execute(text("""
        INSERT INTO campaign_contact_assignments (id, workspace_id, campaign_id, contact_phone, sender_id)
        VALUES (:id, :wid, :cid, '+79991110000', :sid)
    """), {"id": str(_uuid.uuid4()), "wid": str(test_workspace.id),
           "cid": c["id"], "sid": str(sender.id)})
    await async_db_session.commit()

    r = await async_client.post(f"/api/v1/campaigns/{c['id']}/duplicate",
                                headers=_auth_headers(valid_supabase_jwt, "u-dup"))
    assert r.status_code == 201
    new_c = r.json()
    assert new_c["status"] == "draft"
    assert new_c["name"].startswith("DupBase (copy")
    # Senders copied
    assert len(new_c["attached_senders"]) == 1
    assert new_c["attached_senders"][0]["sender_id"] == str(sender.id)

    # NO queue items NOR cca for the new campaign
    queue_for_new = (await async_db_session.execute(text(
        "SELECT COUNT(*) FROM message_queue WHERE campaign_id = :cid"
    ), {"cid": new_c["id"]})).scalar()
    cca_for_new = (await async_db_session.execute(text(
        "SELECT COUNT(*) FROM campaign_contact_assignments WHERE campaign_id = :cid"
    ), {"cid": new_c["id"]})).scalar()
    assert queue_for_new == 0
    assert cca_for_new == 0


async def test_block_delete_agent_when_running_campaign_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """Closes Phase 3 D-09 TODO — delete agent while running campaign attached."""
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-ag-del")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-ag-del",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="AgDel")
    await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                            headers=_auth_headers(valid_supabase_jwt, "u-ag-del"))
    r = await async_client.delete(f"/api/v1/agents/{agent.id}",
                                  headers=_auth_headers(valid_supabase_jwt, "u-ag-del"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "AGENT_USED_BY_RUNNING_CAMPAIGN"


async def test_block_delete_folder_when_running_campaign_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    """Closes Phase 2 D-06 TODO — delete folder while running campaign attached."""
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-fl-del")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-fl-del",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="FlDel")
    await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                            headers=_auth_headers(valid_supabase_jwt, "u-fl-del"))
    r = await async_client.delete(f"/api/v1/folders/{test_folder.id}",
                                  headers=_auth_headers(valid_supabase_jwt, "u-fl-del"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "FOLDER_USED_BY_RUNNING_CAMPAIGN"


async def test_block_delete_sender_when_running_campaign_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-sn-del")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-sn-del",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="SnDel")
    await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                            headers=_auth_headers(valid_supabase_jwt, "u-sn-del"))
    r = await async_client.delete(f"/api/v1/senders/{sender.slug}",
                                  headers=_auth_headers(valid_supabase_jwt, "u-sn-del"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SENDER_USED_BY_RUNNING_CAMPAIGN"


async def test_block_patch_sender_lifecycle_to_paused_when_running_campaign_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder, test_sender_factory,
):
    agent = await test_agent_factory()
    sender = await test_sender_factory()
    await _bind(async_db_session, test_workspace.id, "u-sn-pat")
    c = await _make_campaign(async_client, valid_supabase_jwt, "u-sn-pat",
                             agent.id, test_folder.id, sender_ids=[sender.id], name="SnPat")
    await async_client.post(f"/api/v1/campaigns/{c['id']}/start",
                            headers=_auth_headers(valid_supabase_jwt, "u-sn-pat"))
    r = await async_client.patch(f"/api/v1/senders/{sender.slug}",
                                 json={"lifecycle_status": "paused"},
                                 headers=_auth_headers(valid_supabase_jwt, "u-sn-pat"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SENDER_USED_BY_RUNNING_CAMPAIGN"


async def test_agent_campaign_count_real_not_hardcoded_zero(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_folder,
):
    """Closes Phase 3 D-10 TODO — campaign_count must be SELECT COUNT, not 0."""
    agent = await test_agent_factory()
    await _bind(async_db_session, test_workspace.id, "u-cnt")
    await _make_campaign(async_client, valid_supabase_jwt, "u-cnt",
                         agent.id, test_folder.id, name="Cnt1")
    await _make_campaign(async_client, valid_supabase_jwt, "u-cnt",
                         agent.id, test_folder.id, name="Cnt2")
    r = await async_client.get(f"/api/v1/agents/{agent.id}",
                               headers=_auth_headers(valid_supabase_jwt, "u-cnt"))
    assert r.status_code == 200
    assert r.json()["campaign_count"] == 2
