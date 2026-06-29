"""Quick 260629-ig7: re-render pending queue items when message_template changes.

Helper-level unit tests (rerender_pending_queue) + router tests (PATCH auto-hook
and the explicit POST /campaigns/{id}/rerender-pending endpoint).

Background: message_queue snapshots the rendered opener at enqueue time, so a
template edit must be propagated to already-pending rows.
"""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.services.campaign_enqueue import rerender_pending_queue

pytestmark = pytest.mark.asyncio


async def _insert_queue_row(db, *, camp, sender_id, status, phone, name, message,
                            item_type="message"):
    qid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO message_queue
            (id, workspace_id, campaign_id, sender_id, item_type, status,
             recipient_phone, recipient_name, message_text, created_at)
        VALUES (:id, :wid, :cid, :sid, :it, :st, :ph, :nm, :txt, NOW())
    """), {
        "id": str(qid), "wid": str(camp["workspace_id"]), "cid": str(camp["id"]),
        "sid": str(sender_id), "it": item_type, "st": status,
        "ph": phone, "nm": name, "txt": message,
    })
    return qid


def _campaign_shim(camp, template):
    """Minimal object exposing only what rerender_pending_queue reads."""
    return SimpleNamespace(
        id=camp["id"],
        message_template=template,
        folder_id=camp["folder_id"],
        workspace_id=camp["workspace_id"],
    )


async def _text_of(db, qid):
    return (await db.execute(
        text("SELECT message_text FROM message_queue WHERE id = :id"),
        {"id": str(qid)},
    )).scalar()


# ── Helper unit tests ─────────────────────────────────────────────────────────


async def test_rerender_updates_pending_leaves_sent(
    async_db_session, test_running_campaign_factory
):
    """pending rows get the new template; sent rows are untouched; returns count."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    sid = senders[0].id

    pend = await _insert_queue_row(
        async_db_session, camp=camp, sender_id=sid, status="pending",
        phone="+79990000001", name="Иван", message="OLD opener")
    sent = await _insert_queue_row(
        async_db_session, camp=camp, sender_id=sid, status="sent",
        phone="+79990000002", name="Пётр", message="OLD opener")
    await async_db_session.commit()

    n = await rerender_pending_queue(async_db_session, _campaign_shim(camp, "NEW opener"))
    await async_db_session.commit()

    assert n == 1
    assert await _text_of(async_db_session, pend) == "NEW opener"
    assert await _text_of(async_db_session, sent) == "OLD opener"  # sent never touched


async def test_rerender_renders_variables_from_contact(
    async_db_session, test_running_campaign_factory
):
    """{{name}} resolves from the contact matched by identity within the folder."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    sid = senders[0].id
    phone = "+79991112233"

    # Contact in the campaign folder with full_name → drives {{name}}.
    await async_db_session.execute(text("""
        INSERT INTO contacts (id, workspace_id, folder_id, phone, full_name, tg_status)
        VALUES (:id, :wid, :fid, :ph, :nm, 'registered')
    """), {
        "id": str(uuid.uuid4()), "wid": str(camp["workspace_id"]),
        "fid": str(camp["folder_id"]), "ph": phone, "nm": "Полина",
    })
    pend = await _insert_queue_row(
        async_db_session, camp=camp, sender_id=sid, status="pending",
        phone=phone, name="Полина", message="OLD")
    await async_db_session.commit()

    n = await rerender_pending_queue(
        async_db_session, _campaign_shim(camp, "Привет, {{name}}!"))
    await async_db_session.commit()

    assert n == 1
    assert await _text_of(async_db_session, pend) == "Привет, Полина!"


async def test_rerender_falls_back_to_stored_name_when_contact_gone(
    async_db_session, test_running_campaign_factory
):
    """No matching contact in the folder → {{name}} renders from recipient_name."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    sid = senders[0].id

    pend = await _insert_queue_row(
        async_db_session, camp=camp, sender_id=sid, status="pending",
        phone="+79995550000", name="Гость", message="OLD")
    await async_db_session.commit()

    n = await rerender_pending_queue(
        async_db_session, _campaign_shim(camp, "Здравствуйте, {{name}}!"))
    await async_db_session.commit()

    assert n == 1
    assert await _text_of(async_db_session, pend) == "Здравствуйте, Гость!"


async def test_rerender_empty_template_is_noop(
    async_db_session, test_running_campaign_factory
):
    """Blank template never blanks a message — returns 0, row unchanged."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    sid = senders[0].id

    pend = await _insert_queue_row(
        async_db_session, camp=camp, sender_id=sid, status="pending",
        phone="+79994440000", name="X", message="KEEP")
    await async_db_session.commit()

    n = await rerender_pending_queue(async_db_session, _campaign_shim(camp, "   "))
    await async_db_session.commit()

    assert n == 0
    assert await _text_of(async_db_session, pend) == "KEEP"


# ── Router tests (PATCH auto-hook + endpoint) ──────────────────────────────────


async def _api_create_campaign(client, jwt, name, template):
    r = await client.post(
        "/api/v1/campaigns",
        json={"name": name, "message_template": template},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["campaign"]


async def _insert_sender_and_pending(db, *, wid, cid, message):
    sid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO senders (id, workspace_id, slug, name, phone, session_string)
        VALUES (:id, :wid, :slug, :name, :phone, :sess)
    """), {
        "id": str(sid), "wid": str(wid), "slug": f"s-{sid.hex[:8]}",
        "name": "S", "phone": "+79990001122", "sess": "enc",
    })
    qid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO message_queue
            (id, workspace_id, campaign_id, sender_id, item_type, status,
             recipient_phone, recipient_name, message_text, created_at)
        VALUES (:id, :wid, :cid, :sid, 'message', 'pending', :ph, :nm, :txt, NOW())
    """), {
        "id": str(qid), "wid": str(wid), "cid": str(cid), "sid": str(sid),
        "ph": "+79993334455", "nm": "Аноним", "txt": message,
    })
    await db.commit()
    return qid


async def test_patch_template_rerenders_pending(
    async_client, valid_supabase_jwt, async_db_session
):
    """PATCH that changes message_template re-renders pending rows in the same txn."""
    jwt = valid_supabase_jwt(sub="rerender-patch-user")
    camp = await _api_create_campaign(async_client, jwt, "rr-patch", "OLD opener")
    qid = await _insert_sender_and_pending(
        async_db_session, wid=camp["workspace_id"], cid=camp["id"], message="OLD opener")

    r = await async_client.patch(
        f"/api/v1/campaigns/{camp['id']}",
        json={"message_template": "NEW opener"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert r.status_code == 200, r.text

    await async_db_session.rollback()  # fresh read of the API-committed row
    assert await _text_of(async_db_session, qid) == "NEW opener"


async def test_patch_without_template_change_leaves_pending(
    async_client, valid_supabase_jwt, async_db_session
):
    """PATCH of an unrelated field does not touch pending rows."""
    jwt = valid_supabase_jwt(sub="rerender-noop-user")
    camp = await _api_create_campaign(async_client, jwt, "rr-noop", "OLD opener")
    qid = await _insert_sender_and_pending(
        async_db_session, wid=camp["workspace_id"], cid=camp["id"], message="OLD opener")

    r = await async_client.patch(
        f"/api/v1/campaigns/{camp['id']}",
        json={"description": "just a note"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert r.status_code == 200, r.text

    await async_db_session.rollback()
    assert await _text_of(async_db_session, qid) == "OLD opener"


async def test_rerender_endpoint(
    async_client, valid_supabase_jwt, async_db_session
):
    """POST /campaigns/{id}/rerender-pending re-renders against the current template."""
    jwt = valid_supabase_jwt(sub="rerender-ep-user")
    camp = await _api_create_campaign(async_client, jwt, "rr-ep", "TEMPLATE v2")
    # Simulate a stale pending row (e.g. enqueued before the template was set).
    qid = await _insert_sender_and_pending(
        async_db_session, wid=camp["workspace_id"], cid=camp["id"], message="STALE v1")

    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/rerender-pending",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rerendered"] == 1

    await async_db_session.rollback()
    assert await _text_of(async_db_session, qid) == "TEMPLATE v2"


async def test_rerender_endpoint_cross_workspace_404(
    async_client, valid_supabase_jwt
):
    """Endpoint is workspace-scoped: another user's campaign id → 404."""
    jwt_a = valid_supabase_jwt(sub="rr-owner")
    camp = await _api_create_campaign(async_client, jwt_a, "rr-owned", "T")

    jwt_b = valid_supabase_jwt(sub="rr-stranger")
    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/rerender-pending",
        headers={"Authorization": f"Bearer {jwt_b}"},
    )
    assert r.status_code == 404, r.text
