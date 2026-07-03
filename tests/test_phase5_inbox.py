"""Phase 5 inbox endpoints (INBX-01, INBX-02, INBX-05) — list, detail,
messages history, filters, workspace isolation, DELETE.

Covers tests 1-8, 14-18, 20 from plan 05-01 behaviour list.
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str = "inbox-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ── 1. Auth gate ──────────────────────────────────────────────────────────────


async def test_list_conversations_requires_auth(async_client):
    """Test 1: GET /conversations without credentials → 401."""
    r = await async_client.get("/api/v1/conversations")
    assert r.status_code == 401


# ── 2. Workspace isolation ────────────────────────────────────────────────────


async def test_list_conversations_workspace_scoped(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Test 2: list returns only this workspace's conversations."""
    from app.models import Workspace, Sender

    await test_conversation_factory(contact_phone="+79991110001")
    await _bind(async_db_session, test_workspace.id, "u-iso1")

    # Other workspace + conversation.
    other = Workspace(name="OtherWS")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    other_sender = Sender(
        workspace_id=other.id, slug="other-sender", name="Other",
        phone="+79002222222", session_string="x", role="sender",
        lifecycle_status="active", auth_status="ok",
    )
    async_db_session.add(other_sender)
    await async_db_session.commit()
    await async_db_session.refresh(other_sender)
    await async_db_session.execute(text("""
        INSERT INTO conversations (id, workspace_id, sender_id, contact_phone)
        VALUES (:cid, :wid, :sid, '+79992220002')
    """), {
        "cid": str(_uuid.uuid4()), "wid": str(other.id),
        "sid": str(other_sender.id),
    })
    await async_db_session.commit()

    r = await async_client.get(
        "/api/v1/conversations", headers=_auth_headers(valid_supabase_jwt, "u-iso1")
    )
    assert r.status_code == 200
    phones = {c["contact_phone"] for c in r.json()["conversations"]}
    assert "+79991110001" in phones
    assert "+79992220002" not in phones


# ── 3-4. D-17 hide bot_ignored by default ─────────────────────────────────────


async def test_list_hides_bot_ignored_by_default(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Test 3 / D-17: default response hides status='bot_ignored'."""
    await test_conversation_factory(contact_phone="+79991111101", status="active")
    await test_conversation_factory(
        contact_phone="+79991111102", status="bot_ignored", ai_enabled=False
    )
    await _bind(async_db_session, test_workspace.id, "u-hide")

    r = await async_client.get(
        "/api/v1/conversations", headers=_auth_headers(valid_supabase_jwt, "u-hide")
    )
    assert r.status_code == 200
    phones = {c["contact_phone"] for c in r.json()["conversations"]}
    assert "+79991111101" in phones
    assert "+79991111102" not in phones


async def test_list_status_bot_ignored_explicit(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Test 4 / D-17: ?status=bot_ignored returns those rows explicitly."""
    await test_conversation_factory(
        contact_phone="+79991111103", status="bot_ignored", ai_enabled=False
    )
    await _bind(async_db_session, test_workspace.id, "u-hide2")

    r = await async_client.get(
        "/api/v1/conversations?status=bot_ignored",
        headers=_auth_headers(valid_supabase_jwt, "u-hide2"),
    )
    assert r.status_code == 200
    phones = {c["contact_phone"] for c in r.json()["conversations"]}
    assert "+79991111103" in phones


# ── 4b. quick-260703-goh: hide telegram_service by default ────────────────────


async def test_list_hides_telegram_service_by_default(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Default response hides status='telegram_service' (Telegram tab only)."""
    await test_conversation_factory(contact_phone="+79991111201", status="active")
    await test_conversation_factory(
        contact_phone="+79991111202", status="telegram_service", ai_enabled=False
    )
    await _bind(async_db_session, test_workspace.id, "u-tg-hide")

    r = await async_client.get(
        "/api/v1/conversations", headers=_auth_headers(valid_supabase_jwt, "u-tg-hide")
    )
    assert r.status_code == 200
    phones = {c["contact_phone"] for c in r.json()["conversations"]}
    assert "+79991111201" in phones
    assert "+79991111202" not in phones


async def test_list_status_telegram_service_explicit(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """?status=telegram_service returns exactly those rows → the Telegram tab."""
    await test_conversation_factory(
        contact_phone="+79991111203", status="telegram_service", ai_enabled=False
    )
    await _bind(async_db_session, test_workspace.id, "u-tg-exp")

    r = await async_client.get(
        "/api/v1/conversations?status=telegram_service",
        headers=_auth_headers(valid_supabase_jwt, "u-tg-exp"),
    )
    assert r.status_code == 200
    phones = {c["contact_phone"] for c in r.json()["conversations"]}
    assert "+79991111203" in phones


# ── 5. Warmup-pair exclude ────────────────────────────────────────────────────


async def test_list_excludes_warmup_pair_conversations(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory,
):
    """Test 5: warmup-pair conversations excluded from inbox.

    Sender2 is the workspace's own warmup peer (telegram_id matches the
    contact_telegram_id of a conversation). That conversation must not
    surface in the list.
    """
    # Sender1 — the real sender, conversation contacts have telegram_id=999.
    sender1 = await test_sender_factory()
    # Sender2 — a workspace-internal sender whose telegram_id matches contact.
    sender2 = await test_sender_factory()
    await async_db_session.execute(text("""
        UPDATE senders SET telegram_id = 999 WHERE id = :sid
    """), {"sid": str(sender2.id)})
    await async_db_session.commit()

    await test_conversation_factory(
        sender=sender1, contact_phone="+79991112001", contact_telegram_id=999
    )
    # Control conversation: contact_telegram_id None or different value.
    await test_conversation_factory(
        sender=sender1, contact_phone="+79991112002", contact_telegram_id=12345
    )
    await _bind(async_db_session, test_workspace.id, "u-warmup")

    r = await async_client.get(
        "/api/v1/conversations", headers=_auth_headers(valid_supabase_jwt, "u-warmup")
    )
    assert r.status_code == 200
    phones = {c["contact_phone"] for c in r.json()["conversations"]}
    # Warmup-pair excluded.
    assert "+79991112001" not in phones
    # Control survives.
    assert "+79991112002" in phones


# ── 6-7. INBX-02 messages history + pagination ────────────────────────────────


async def test_get_messages_cross_workspace_404(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Test 6: GET /conversations/{id}/messages cross-workspace → 404."""
    from app.models import Workspace, Sender

    conv = await test_conversation_factory(contact_phone="+79991113001")
    # Bind a different user to a different workspace.
    other = Workspace(name="OtherMsg")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-msg-other")

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/messages",
        headers=_auth_headers(valid_supabase_jwt, "u-msg-other"),
    )
    assert r.status_code == 404


async def test_get_messages_pagination(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, test_message_factory,
):
    """Test 7: ?limit=10&offset=10 returns the second page (msgs 11..20)."""
    conv = await test_conversation_factory(contact_phone="+79991113002")
    await test_message_factory(conv["id"], count=25)
    await _bind(async_db_session, test_workspace.id, "u-msg-paginate")

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}/messages?limit=10&offset=10",
        headers=_auth_headers(valid_supabase_jwt, "u-msg-paginate"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 25
    assert len(body["messages"]) == 10


# ── 8. INBX-03 status field present (all 7 values) ────────────────────────────


@pytest.mark.parametrize(
    "status",
    ["active", "manual", "paused", "lead", "handoff", "finished", "bot_ignored"],
)
async def test_get_conversation_returns_status(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, status,
):
    """Test 8: detail endpoint returns each of the 7 status values."""
    conv = await test_conversation_factory(
        contact_phone=f"+7999{abs(hash(status)) % 10_000_000:07d}",
        status=status,
        ai_enabled=(status != "bot_ignored"),
    )
    uid = f"u-status-{status}"
    await _bind(async_db_session, test_workspace.id, uid)

    r = await async_client.get(
        f"/api/v1/conversations/{conv['id']}",
        headers=_auth_headers(valid_supabase_jwt, uid),
    )
    assert r.status_code == 200
    assert r.json()["status"] == status


# ── 14-17. INBX-05 filters: campaign_id / agent_id / sender_id strict EQ ──────


async def test_list_filter_by_campaign_id_strict(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, test_campaign_factory,
):
    """Test 14 / D-18: ?campaign_id=X strict EQ; campaign_id=NULL excluded."""
    camp = await test_campaign_factory()
    await test_conversation_factory(
        campaign_id=camp["id"], contact_phone="+79991141001"
    )
    await test_conversation_factory(contact_phone="+79991141002")  # campaign_id NULL
    await _bind(async_db_session, test_workspace.id, "u-camp-filter")

    r = await async_client.get(
        f"/api/v1/conversations?campaign_id={camp['id']}",
        headers=_auth_headers(valid_supabase_jwt, "u-camp-filter"),
    )
    assert r.status_code == 200
    phones = {c["contact_phone"] for c in r.json()["conversations"]}
    assert phones == {"+79991141001"}


async def test_list_filter_by_agent_id_strict(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, test_agent_factory,
):
    """Test 15 / INBX-05: ?agent_id=X strict EQ."""
    agent = await test_agent_factory()
    await test_conversation_factory(
        ai_context_id=agent.id, contact_phone="+79991151001"
    )
    await test_conversation_factory(contact_phone="+79991151002")
    await _bind(async_db_session, test_workspace.id, "u-agent-filter")

    r = await async_client.get(
        f"/api/v1/conversations?agent_id={agent.id}",
        headers=_auth_headers(valid_supabase_jwt, "u-agent-filter"),
    )
    assert r.status_code == 200
    phones = {c["contact_phone"] for c in r.json()["conversations"]}
    assert phones == {"+79991151001"}


async def test_list_filter_by_sender_id_strict(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory,
):
    """Test 16 / INBX-05: ?sender_id=X strict EQ."""
    s_a = await test_sender_factory()
    s_b = await test_sender_factory()
    await test_conversation_factory(sender=s_a, contact_phone="+79991161001")
    await test_conversation_factory(sender=s_b, contact_phone="+79991161002")
    await _bind(async_db_session, test_workspace.id, "u-sender-filter")

    r = await async_client.get(
        f"/api/v1/conversations?sender_id={s_a.id}",
        headers=_auth_headers(valid_supabase_jwt, "u-sender-filter"),
    )
    assert r.status_code == 200
    phones = {c["contact_phone"] for c in r.json()["conversations"]}
    assert phones == {"+79991161001"}


async def test_list_filter_combined_three_dimensions(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory, test_campaign_factory,
    test_agent_factory,
):
    """Test 17 / INBX-05: combined ?campaign_id=X&agent_id=Y&sender_id=Z."""
    camp = await test_campaign_factory()
    agent = await test_agent_factory()
    sender = await test_sender_factory()

    target = await test_conversation_factory(
        sender=sender,
        campaign_id=camp["id"],
        ai_context_id=agent.id,
        contact_phone="+79991171001",
    )
    # Decoys: each missing one dimension.
    await test_conversation_factory(
        sender=sender, campaign_id=camp["id"], contact_phone="+79991171002"
    )
    await test_conversation_factory(
        sender=sender, ai_context_id=agent.id, contact_phone="+79991171003"
    )
    await test_conversation_factory(
        campaign_id=camp["id"], ai_context_id=agent.id, contact_phone="+79991171004"
    )
    await _bind(async_db_session, test_workspace.id, "u-combo-filter")

    r = await async_client.get(
        f"/api/v1/conversations?campaign_id={camp['id']}&agent_id={agent.id}"
        f"&sender_id={sender.id}",
        headers=_auth_headers(valid_supabase_jwt, "u-combo-filter"),
    )
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()["conversations"]]
    assert str(target["id"]) in ids
    assert len(ids) == 1


# ── 18. Auth: all 8 endpoints reject anonymous; cross-workspace = 404 ─────────


@pytest.mark.parametrize(
    "method,suffix",
    [
        ("GET", ""),
        ("GET", "/{cid}"),
        ("GET", "/{cid}/messages"),
        ("PATCH", "/{cid}"),
        ("POST", "/{cid}/disable-ai"),
        ("POST", "/{cid}/enable-ai"),
        ("POST", "/{cid}/send"),
        ("DELETE", "/{cid}"),
    ],
)
async def test_endpoints_require_auth(async_client, method, suffix):
    """Test 18a: every endpoint returns 401 without credentials."""
    url = "/api/v1/conversations" + suffix.replace("{cid}", str(_uuid.uuid4()))
    if method == "POST":
        r = await async_client.post(url, json={})
    elif method == "PATCH":
        r = await async_client.patch(url, json={})
    elif method == "DELETE":
        r = await async_client.delete(url)
    else:
        r = await async_client.get(url)
    assert r.status_code == 401


async def test_cross_workspace_returns_404_not_403(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Test 18b: cross-workspace conversation access → 404 (not 403)."""
    from app.models import Workspace

    conv = await test_conversation_factory(contact_phone="+79991181001")

    other = Workspace(name="OtherCross")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-cross")

    # All 5 read+mutating endpoints — 404, not 403.
    headers = _auth_headers(valid_supabase_jwt, "u-cross")
    for url, method in [
        (f"/api/v1/conversations/{conv['id']}", "get"),
        (f"/api/v1/conversations/{conv['id']}/messages", "get"),
        (f"/api/v1/conversations/{conv['id']}", "patch"),
        (f"/api/v1/conversations/{conv['id']}/disable-ai", "post"),
        (f"/api/v1/conversations/{conv['id']}/enable-ai", "post"),
        (f"/api/v1/conversations/{conv['id']}", "delete"),
    ]:
        if method == "get":
            r = await async_client.get(url, headers=headers)
        elif method == "patch":
            r = await async_client.patch(url, json={}, headers=headers)
        elif method == "delete":
            r = await async_client.delete(url, headers=headers)
        else:
            r = await async_client.post(url, json={}, headers=headers)
        assert r.status_code == 404, f"{method.upper()} {url} → {r.status_code}"


# ── 20. DELETE happy path + cross-workspace 404 ───────────────────────────────


async def test_delete_conversation_happy_path(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Test 20a: DELETE returns 204 and removes the row."""
    conv = await test_conversation_factory(contact_phone="+79991201001")
    await _bind(async_db_session, test_workspace.id, "u-del")

    r = await async_client.delete(
        f"/api/v1/conversations/{conv['id']}",
        headers=_auth_headers(valid_supabase_jwt, "u-del"),
    )
    assert r.status_code == 204

    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM conversations WHERE id = :cid
    """), {"cid": str(conv["id"])})).scalar()
    assert cnt == 0


async def test_delete_conversation_cross_workspace_404_no_delete(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Test 20b: cross-workspace DELETE → 404 and row remains intact."""
    from app.models import Workspace

    conv = await test_conversation_factory(contact_phone="+79991202001")
    other = Workspace(name="OtherDel")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-del-other")

    r = await async_client.delete(
        f"/api/v1/conversations/{conv['id']}",
        headers=_auth_headers(valid_supabase_jwt, "u-del-other"),
    )
    assert r.status_code == 404

    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM conversations WHERE id = :cid
    """), {"cid": str(conv["id"])})).scalar()
    assert cnt == 1


# ── 21. POST /delete batch happy path + cross-workspace skip ──────────────────


async def test_delete_conversations_batch_happy_path(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Test 21a: POST /delete removes the listed rows, returns {deleted: N},
    and leaves unlisted conversations intact."""
    keep = await test_conversation_factory(contact_phone="+79991203001")
    drop1 = await test_conversation_factory(contact_phone="+79991203002")
    drop2 = await test_conversation_factory(contact_phone="+79991203003")
    await _bind(async_db_session, test_workspace.id, "u-batch-del")

    r = await async_client.post(
        "/api/v1/conversations/delete",
        json={"conversation_ids": [str(drop1["id"]), str(drop2["id"])]},
        headers=_auth_headers(valid_supabase_jwt, "u-batch-del"),
    )
    assert r.status_code == 200
    assert r.json() == {"deleted": 2}

    gone = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM conversations WHERE id = ANY(:ids)
    """), {"ids": [str(drop1["id"]), str(drop2["id"])]})).scalar()
    assert gone == 0

    alive = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM conversations WHERE id = :cid
    """), {"cid": str(keep["id"])})).scalar()
    assert alive == 1


async def test_delete_conversations_batch_cross_workspace_skipped(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Test 21b: batch delete silently skips ids from another workspace —
    deleted=0, foreign row remains intact (no cross-tenant disclosure)."""
    from app.models import Workspace

    conv = await test_conversation_factory(contact_phone="+79991204001")
    other = Workspace(name="OtherBatchDel")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    await _bind(async_db_session, other.id, "u-batch-other")

    r = await async_client.post(
        "/api/v1/conversations/delete",
        json={"conversation_ids": [str(conv["id"])]},
        headers=_auth_headers(valid_supabase_jwt, "u-batch-other"),
    )
    assert r.status_code == 200
    assert r.json() == {"deleted": 0}

    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM conversations WHERE id = :cid
    """), {"cid": str(conv["id"])})).scalar()
    assert cnt == 1
