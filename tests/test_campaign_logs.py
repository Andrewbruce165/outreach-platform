"""Campaign detail redesign — GET /campaigns/{id}/logs + attachment read endpoints.

Logs (redesign brief, Logs tab MVP): a UNION of message_queue outcomes and
llm_calls.tool_calls built-in triggers, newest-first, cursor pagination via
?before=. No new tables.

Attachment reads: GET /{id}/attachment (metadata list, blob stays out of the
SELECT) + GET /{id}/attachment/{attachment_id} (raw bytes for the preview).
"""
import json
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _bind(db, ws_id, uid):
    await db.execute(
        text(
            "INSERT INTO user_workspaces (supabase_user_id, workspace_id, role) "
            "VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING"
        ),
        {"uid": uid, "wid": str(ws_id)},
    )
    await db.commit()


async def _seed_llm_call(db, ws_id, conversation_id, campaign_id, tool_calls):
    """Minimal llm_calls row with the given tool_calls JSONB."""
    await db.execute(text("""
        INSERT INTO llm_calls (
            workspace_id, conversation_id, campaign_id, model, prompt, tool_calls
        ) VALUES (
            :wid, :conv, :cid, 'gpt-test', '[]'::jsonb, CAST(:tcs AS jsonb)
        )
    """), {
        "wid": str(ws_id),
        "conv": str(conversation_id),
        "cid": str(campaign_id),
        "tcs": json.dumps(tool_calls) if tool_calls is not None else None,
    })
    await db.commit()


# ─── GET /campaigns/{id}/logs ─────────────────────────────────────────────────


async def test_logs_empty_campaign(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """A fresh campaign has no events; next_before is null."""
    await _bind(async_db_session, test_workspace.id, "u-logs-empty")
    camp = await test_campaign_factory()
    r = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/logs",
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-logs-empty')}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["events"] == []
    assert body["next_before"] is None


async def test_logs_queue_event_types_and_error_detail(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory, test_queue_item_factory,
):
    """Queue rows map to message_sent / message_failed / message_queued;
    failed rows carry error_message in detail; sent/pending carry none."""
    await _bind(async_db_session, test_workspace.id, "u-logs-q")
    camp = await test_campaign_factory()
    sender = await test_sender_factory()

    await test_queue_item_factory(camp["id"], sender.id, "+79990000001",
                                  status="sent", with_cca=False)
    await test_queue_item_factory(camp["id"], sender.id, "+79990000002",
                                  status="failed", with_cca=False)
    await test_queue_item_factory(camp["id"], sender.id, "+79990000003",
                                  status="pending", with_cca=False)
    # finished_at + error_message on the terminal rows (factory leaves them NULL).
    await async_db_session.execute(text("""
        UPDATE message_queue SET finished_at = NOW()
        WHERE campaign_id = :cid AND status IN ('sent', 'failed')
    """), {"cid": str(camp["id"])})
    await async_db_session.execute(text("""
        UPDATE message_queue SET error_message = 'FloodWait 42s'
        WHERE campaign_id = :cid AND status = 'failed'
    """), {"cid": str(camp["id"])})
    await async_db_session.commit()

    r = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/logs",
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-logs-q')}"},
    )
    assert r.status_code == 200, r.text
    events = r.json()["events"]
    by_phone = {e["contact_phone"]: e for e in events}
    assert by_phone["+79990000001"]["type"] == "message_sent"
    assert by_phone["+79990000001"]["detail"] is None
    assert by_phone["+79990000002"]["type"] == "message_failed"
    assert by_phone["+79990000002"]["detail"] == "FloodWait 42s"
    assert by_phone["+79990000003"]["type"] == "message_queued"
    # newest-first ordering
    ts = [e["ts"] for e in events]
    assert ts == sorted(ts, reverse=True)


async def test_logs_llm_tool_call_events(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_conversation_factory,
):
    """Built-in tool calls map to lead / handoff / dialog_finished with contact
    data joined from conversations; non-builtin tools are ignored."""
    await _bind(async_db_session, test_workspace.id, "u-logs-llm")
    camp = await test_campaign_factory()
    conv = await test_conversation_factory(
        campaign_id=camp["id"], contact_name="Иван", contact_phone="+79991112233",
    )

    await _seed_llm_call(async_db_session, test_workspace.id, conv["id"], camp["id"], [
        {"id": "t1", "name": "mark_as_lead", "arguments": "{}"},
    ])
    await _seed_llm_call(async_db_session, test_workspace.id, conv["id"], camp["id"], [
        {"id": "t2", "name": "transfer_to_manager", "arguments": "{}"},
        {"id": "t3", "name": "some_custom_webhook_tool", "arguments": "{}"},
    ])
    await _seed_llm_call(async_db_session, test_workspace.id, conv["id"], camp["id"], [
        {"id": "t4", "name": "finish_conversation", "arguments": "{}"},
    ])

    # Prod-shape hazard: plain-text LLM responses store JSON null (jsonb 'null',
    # NOT SQL NULL) in tool_calls — jsonb_array_elements() errors on scalars.
    # This row must be silently skipped, not 500 the endpoint.
    await _seed_llm_call(async_db_session, test_workspace.id, conv["id"], camp["id"], None)
    await async_db_session.execute(text("""
        UPDATE llm_calls SET tool_calls = 'null'::jsonb
        WHERE campaign_id = :cid AND tool_calls IS NULL
    """), {"cid": str(camp["id"])})
    await async_db_session.commit()

    r = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/logs",
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-logs-llm')}"},
    )
    assert r.status_code == 200, r.text
    events = r.json()["events"]
    types = sorted(e["type"] for e in events)
    # custom tool dropped, jsonb-null row skipped — exactly the 3 built-ins survive
    assert types == ["dialog_finished", "handoff", "lead"]
    lead = next(e for e in events if e["type"] == "lead")
    assert lead["contact_name"] == "Иван"
    assert lead["contact_phone"] == "+79991112233"
    assert lead["conversation_id"] == str(conv["id"])


async def test_logs_cursor_pagination(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory, test_queue_item_factory,
):
    """limit smaller than the event count → next_before set; the second page
    returns strictly older events and terminates (next_before null)."""
    await _bind(async_db_session, test_workspace.id, "u-logs-pg")
    camp = await test_campaign_factory()
    sender = await test_sender_factory()
    for i in range(5):
        await test_queue_item_factory(camp["id"], sender.id, f"+7999100000{i}",
                                      status="sent", with_cca=False)
    # Spread finished_at so ordering/cursor comparisons are unambiguous.
    await async_db_session.execute(text("""
        UPDATE message_queue mq SET finished_at = NOW() - (n.rn || ' minutes')::interval
        FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY recipient_phone) AS rn
              FROM message_queue WHERE campaign_id = :cid) n
        WHERE mq.id = n.id
    """), {"cid": str(camp["id"])})
    await async_db_session.commit()

    hdr = {"Authorization": f"Bearer {valid_supabase_jwt(sub='u-logs-pg')}"}
    r1 = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/logs", params={"limit": 3}, headers=hdr,
    )
    assert r1.status_code == 200, r1.text
    p1 = r1.json()
    assert len(p1["events"]) == 3
    assert p1["next_before"] is not None

    r2 = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/logs",
        params={"limit": 3, "before": p1["next_before"]}, headers=hdr,
    )
    assert r2.status_code == 200, r2.text
    p2 = r2.json()
    assert len(p2["events"]) == 2
    assert p2["next_before"] is None
    # No overlap between pages
    seen1 = {e["contact_phone"] for e in p1["events"]}
    seen2 = {e["contact_phone"] for e in p2["events"]}
    assert not (seen1 & seen2)


async def test_logs_cross_workspace_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """A user of another workspace gets a silent 404."""
    camp = await test_campaign_factory()
    other_ws = uuid.uuid4()
    await async_db_session.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:wid, 'other-ws-logs')"),
        {"wid": str(other_ws)},
    )
    await _bind(async_db_session, other_ws, "u-logs-foreign")
    r = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/logs",
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-logs-foreign')}"},
    )
    assert r.status_code == 404, r.text


# ─── GET /campaigns/{id}/attachment (+/{attachment_id}) ──────────────────────


async def _seed_attachment(db, ws_id, campaign_id, *, file_name, blob,
                           content_type, position=0):
    return (await db.execute(text("""
        INSERT INTO campaign_attachments (
            campaign_id, workspace_id, file_data, file_name, content_type,
            size_bytes, position
        ) VALUES (:cid, :wid, :blob, :fname, :ctype, :size, :pos)
        RETURNING id
    """), {
        "cid": str(campaign_id), "wid": str(ws_id), "blob": blob,
        "fname": file_name, "ctype": content_type, "size": len(blob),
        "pos": position,
    })).scalar()


async def test_attachment_list_metadata_ordered(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """GET /{id}/attachment returns metadata ordered by position — no blob."""
    await _bind(async_db_session, test_workspace.id, "u-att-list")
    camp = await test_campaign_factory()
    await _seed_attachment(async_db_session, test_workspace.id, camp["id"],
                           file_name="b.png", blob=b"png-bytes",
                           content_type="image/png", position=1)
    await _seed_attachment(async_db_session, test_workspace.id, camp["id"],
                           file_name="a.jpg", blob=b"jpg-bytes-longer",
                           content_type="image/jpeg", position=0)
    await async_db_session.commit()

    r = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/attachment",
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-att-list')}"},
    )
    assert r.status_code == 200, r.text
    atts = r.json()["attachments"]
    assert [a["file_name"] for a in atts] == ["a.jpg", "b.png"]
    assert atts[0]["content_type"] == "image/jpeg"
    assert atts[0]["size_bytes"] == len(b"jpg-bytes-longer")
    assert "file_data" not in atts[0]


async def test_attachment_content_bytes(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """GET /{id}/attachment/{attachment_id} streams the exact blob with its
    content-type; unknown id → 404 ATTACHMENT_NOT_FOUND."""
    await _bind(async_db_session, test_workspace.id, "u-att-get")
    camp = await test_campaign_factory()
    blob = b"\x89PNG-fake-image-bytes"
    att_id = await _seed_attachment(
        async_db_session, test_workspace.id, camp["id"],
        file_name="pic.png", blob=blob, content_type="image/png",
    )
    await async_db_session.commit()
    hdr = {"Authorization": f"Bearer {valid_supabase_jwt(sub='u-att-get')}"}

    r = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/attachment/{att_id}", headers=hdr,
    )
    assert r.status_code == 200
    assert r.content == blob
    assert r.headers["content-type"].startswith("image/png")
    assert 'filename="pic.png"' in r.headers["content-disposition"]

    r404 = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/attachment/{uuid.uuid4()}", headers=hdr,
    )
    assert r404.status_code == 404
    assert r404.json()["detail"]["code"] == "ATTACHMENT_NOT_FOUND"


async def test_attachment_cross_workspace_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory,
):
    """Foreign-workspace user cannot list or fetch attachments (silent 404)."""
    camp = await test_campaign_factory()
    att_id = await _seed_attachment(
        async_db_session, test_workspace.id, camp["id"],
        file_name="secret.pdf", blob=b"pdf", content_type="application/pdf",
    )
    other_ws = uuid.uuid4()
    await async_db_session.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:wid, 'other-ws-att')"),
        {"wid": str(other_ws)},
    )
    await _bind(async_db_session, other_ws, "u-att-foreign")
    hdr = {"Authorization": f"Bearer {valid_supabase_jwt(sub='u-att-foreign')}"}

    r_list = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/attachment", headers=hdr,
    )
    assert r_list.status_code == 404
    r_get = await async_client.get(
        f"/api/v1/campaigns/{camp['id']}/attachment/{att_id}", headers=hdr,
    )
    assert r_get.status_code == 404
