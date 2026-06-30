"""Phase 3 — POST /api/v1/send (rewrite under AuthDep + campaign_id body, D-16).

Post username-outreach refactor the /send body carries `campaign_id` (NOT
`ai_context_id`); the agent is resolved through the campaign. Cross-workspace
isolation flows through the campaign lookup (404 CAMPAIGN_NOT_FOUND), and a
single campaign's agent is shared across all senders attached to it (AGNT-03).
"""
import pytest
from uuid import uuid4
from sqlalchemy import text

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


# ─── Phase 17 Wave-0 RED scaffold — sender resolve ladder ────────────────────
# SRLD-03 (cache→ResolveUsername→Import, sender ResolvePhone removed),
# SRLD-04 (Import gated on tg_status='registered'),
# SRLD-05 (lazy one-at-a-time import, no DeleteContacts on the sender),
# SRLD-06 (stale captured username falls through to Import, never finalizes
#          not_registered), SRLD-07 (confidence-gated cross-sender false read).
#
# All drive `TelegramService().resolve_contact(client, workspace_id, sender_id,
# phone)` against `mock_telethon_client`, asserting on the ordered request-type
# names in `client.calls`. Deferred in-body imports keep --collect-only clean.
# resolve_contact + its cache helpers open their OWN AsyncSessionLocal() sessions,
# so seed rows are COMMITTED via async_db_session before the call.

from uuid import uuid4 as _uuid4


def _user_obj(*, telegram_id=999, access_hash=555, username=None):
    """A Telethon `User`-like object as `.users[0]` in a Resolve*/Import response."""
    class _User:
        id = telegram_id

        def __init__(self):
            self.access_hash = access_hash
            self.first_name = "Test"
            self.last_name = None
            self.username = username

    return _User()


def _resolved(*, telegram_id=999, access_hash=555, username=None):
    """A `ResolvedPeer`-like response (`.users[0]` carries id/access_hash/username)."""
    class _Resolved:
        users = [_user_obj(telegram_id=telegram_id, access_hash=access_hash, username=username)]

    return _Resolved()


def _imported(*, telegram_id=999, access_hash=555, username=None):
    """An `ImportedContacts`-like response (`.users[0]` is the resolved user)."""
    class _Imported:
        users = [_user_obj(telegram_id=telegram_id, access_hash=access_hash, username=username)]
        imported = [object()]
        retry_contacts = []

    return _Imported()


def _raises(exc):
    """Callable response for the mock client that raises `exc` when invoked."""
    def _r(_request):
        raise exc
    return _r


async def _seed_contact(db, workspace_id, *, phone, tg_status, tg_username_resolved=None,
                        tg_probe_state=None, tg_confidence=None):
    """COMMIT a folder + contact row for `phone` in `workspace_id`."""
    suffix = _uuid4().hex[:8]
    folder_id = str(_uuid4())
    await db.execute(text("""
        INSERT INTO folders (id, workspace_id, name) VALUES (:id, :wid, :name)
    """), {"id": folder_id, "wid": str(workspace_id), "name": f"srld-folder-{suffix}"})
    await db.execute(text("""
        INSERT INTO contacts (id, workspace_id, folder_id, phone, full_name, tg_status,
                              tg_username_resolved, tg_probe_state, tg_confidence)
        VALUES (:id, :wid, :fid, :phone, 'SRLD contact', :st, :uname, :probe, :conf)
    """), {
        "id": str(_uuid4()), "wid": str(workspace_id), "fid": folder_id,
        "phone": phone, "st": tg_status, "uname": tg_username_resolved,
        "probe": tg_probe_state, "conf": tg_confidence,
    })
    await db.commit()


async def test_resolve_ladder_no_sender_resolvephone(
    async_db_session, test_workspace, mock_telethon_client,
):
    """SRLD-03 (D-01/D-02): the sender's own ResolvePhone is removed. With a captured
    `tg_username_resolved`, tier-2 ResolveUsername fires; ResolvePhone NEVER does.

    RED today: `resolve_contact` ignores `tg_username_resolved` and calls
    `ResolvePhoneRequest` for any phone-key cache miss.
    """
    from app.services.telegram import TelegramService

    phone = "+79990010001"
    await _seed_contact(
        async_db_session, test_workspace.id, phone=phone,
        tg_status="registered", tg_username_resolved="captured_handle",
    )
    client = mock_telethon_client
    client.set_response("ResolveUsernameRequest", _resolved(telegram_id=111, username="captured_handle"))

    res = await TelegramService().resolve_contact(
        client, str(test_workspace.id), str(_uuid4()), phone,
    )

    names = [c[0] for c in client.calls]
    assert "ResolvePhoneRequest" not in names, (
        "the sender's own ResolvePhone must be REMOVED (SRLD-03/D-01) — it gave the false "
        f"negatives in the Barter-ВЭД incident; calls={names}"
    )
    assert "ResolveUsernameRequest" in names, (
        "tier-2 ResolveUsername must fire on the captured @username (SRLD-03/D-01)"
    )
    assert res.get("is_registered") is True


async def test_import_gate_registered_only(
    async_db_session, test_workspace, mock_telethon_client,
):
    """SRLD-04 (D-03/D-11): tier-3 ImportContacts is attempted ONLY when the checker
    verdict is `registered`; a `not_registered` contact triggers NO import.

    RED today: there is NO import tier on the sender at all (ResolvePhone-only),
    so a registered-no-username contact never reaches ImportContacts.
    """
    from app.services.telegram import TelegramService

    # Case A: registered + no captured username → tier-3 import fires.
    phone_a = "+79990020001"
    await _seed_contact(
        async_db_session, test_workspace.id, phone=phone_a, tg_status="registered",
    )
    client_a = mock_telethon_client
    client_a.set_response("ImportContactsRequest", _imported(telegram_id=222))
    client_a.set_response("DeleteContactsRequest", True)

    res_a = await TelegramService().resolve_contact(
        client_a, str(test_workspace.id), str(_uuid4()), phone_a,
    )
    names_a = [c[0] for c in client_a.calls]
    assert "ImportContactsRequest" in names_a, (
        "registered + no username must fall through to tier-3 ImportContacts (SRLD-04/D-03)"
    )
    assert res_a.get("is_registered") is True

    # Case B: not_registered → NO import attempted (don't waste a risky import).
    phone_b = "+79990020002"
    await _seed_contact(
        async_db_session, test_workspace.id, phone=phone_b, tg_status="not_registered",
    )
    # Fresh mock so calls don't bleed across cases.
    client_b = type(client_a)()
    res_b = await TelegramService().resolve_contact(
        client_b, str(test_workspace.id), str(_uuid4()), phone_b,
    )
    names_b = [c[0] for c in client_b.calls]
    assert "ImportContactsRequest" not in names_b, (
        "a not_registered contact must NOT trigger an import (SRLD-04/D-03)"
    )
    assert not res_b.get("is_registered")


async def test_lazy_import_no_delete_on_sender(
    async_db_session, test_workspace, mock_telethon_client,
):
    """SRLD-05 (D-04): when the sender imports a contact it KEEPS it — no
    DeleteContactsRequest on the sender path (unlike the checker, which deletes).

    RED today: no import tier exists, so the import-then-keep behavior is not built.
    """
    from app.services.telegram import TelegramService

    phone = "+79990030001"
    await _seed_contact(
        async_db_session, test_workspace.id, phone=phone, tg_status="registered",
    )
    client = mock_telethon_client
    client.set_response("ImportContactsRequest", _imported(telegram_id=333))

    await TelegramService().resolve_contact(
        client, str(test_workspace.id), str(_uuid4()), phone,
    )
    names = [c[0] for c in client.calls]
    assert "ImportContactsRequest" in names, "the import tier must fire for a registered contact"
    assert "DeleteContactsRequest" not in names, (
        "the SENDER keeps the imported contact (hot entity-cache for follow-ups) — "
        f"no DeleteContacts (SRLD-05/D-04); calls={names}"
    )


async def test_stale_username_fallthrough(
    async_db_session, test_workspace, mock_telethon_client,
):
    """SRLD-06 (D-09): a captured @username that is now stale
    (UsernameNotOccupiedError) must FALL THROUGH to the import tier (the contact is
    registered), never finalize as not_registered.

    RED today: `_resolve_username` caches {is_registered: False} and returns on
    USERNAME_NOT_OCCUPIED — it does NOT fall through to import.
    """
    from telethon.errors import UsernameNotOccupiedError

    from app.services.telegram import TelegramService

    phone = "+79990040001"
    await _seed_contact(
        async_db_session, test_workspace.id, phone=phone,
        tg_status="registered", tg_username_resolved="gone_handle",
    )
    client = mock_telethon_client
    client.set_response(
        "ResolveUsernameRequest",
        _raises(UsernameNotOccupiedError(request=None)),
    )
    client.set_response("ImportContactsRequest", _imported(telegram_id=444))
    client.set_response("DeleteContactsRequest", True)

    res = await TelegramService().resolve_contact(
        client, str(test_workspace.id), str(_uuid4()), phone,
    )
    names = [c[0] for c in client.calls]
    assert "ResolveUsernameRequest" in names, "tier-2 ResolveUsername must be attempted first"
    assert "ImportContactsRequest" in names, (
        "a stale captured username must FALL THROUGH to tier-3 import (SRLD-06/D-09), "
        f"not finalize not_registered; calls={names}"
    )
    assert res.get("is_registered") is True, (
        "the registered contact must resolve via import after the stale-username fall-through"
    )


async def test_confidence_gated_cache_sender_read(
    async_db_session, test_workspace, mock_telethon_client,
):
    """SRLD-07 (sender side / D-12): a cross-sender `is_registered=false` cache row
    written by a SUSPECT source must NOT short-circuit the sender — a live Telegram
    resolve is attempted instead of returning the blind false.

    RED today: `_get_cached_contact` returns the cross-sender false blind
    (telegram.py:442), so no Telegram call happens.
    """
    from app.models import Sender
    from app.services.telegram import TelegramService

    phone = "+79990050001"
    suffix = _uuid4().hex[:8]

    # A different sender wrote the poisoned false cache row for this phone.
    other_sender = Sender(
        workspace_id=test_workspace.id, slug=f"srld07-snd-{suffix}", name="poison writer",
        phone="+79995559999", session_string="enc", role="checker",
        auth_status="ok", lifecycle_status="active",
        rate_per_min=4, rate_per_hour=20, rate_per_day=150,
    )
    async_db_session.add(other_sender)
    await async_db_session.commit()
    await async_db_session.refresh(other_sender)

    await async_db_session.execute(text("""
        INSERT INTO contacts_cache (id, workspace_id, sender_id, phone, telegram_id, is_registered)
        VALUES (:id, :wid, :sid, :phone, NULL, false)
    """), {"id": str(_uuid4()), "wid": str(test_workspace.id), "sid": str(other_sender.id), "phone": phone})
    # Matching contact flagged suspect (the confidence signal — D-12).
    await _seed_contact(
        async_db_session, test_workspace.id, phone=phone,
        tg_status="registered", tg_username_resolved="live_handle",
        tg_probe_state="suspect", tg_confidence=None,
    )

    client = mock_telethon_client
    client.set_response("ResolveUsernameRequest", _resolved(telegram_id=555, username="live_handle"))
    client.set_response("ImportContactsRequest", _imported(telegram_id=555))
    client.set_response("DeleteContactsRequest", True)

    await TelegramService().resolve_contact(
        client, str(test_workspace.id), str(_uuid4()), phone,
    )
    names = [c[0] for c in client.calls]
    assert ("ResolveUsernameRequest" in names) or ("ImportContactsRequest" in names), (
        "a suspect-source cross-sender false must NOT short-circuit — the sender must "
        f"attempt a LIVE resolve (SRLD-07/D-12); calls={names}"
    )


async def test_user_blocked_records_event(
    async_db_session, test_workspace, mock_telethon_client,
):
    """SRLD-08 (D-15, send path): when the recipient has blocked the sender, the
    send raises UserIsBlockedError and TelegramService.send_message returns the
    structured error code 'USER_IS_BLOCKED' (not the generic SEND_FAILED).

    Resolve is satisfied from the per-sender cache (registered + access_hash) so the
    send path is reached without any live resolve; client.send_message then raises.

    RED today: send_message has no UserIsBlockedError branch → falls into the
    generic `except Exception` → code='SEND_FAILED'.
    """
    from unittest.mock import AsyncMock, MagicMock

    from telethon.errors import UserIsBlockedError

    from app.models import Sender
    from app.services.telegram import TelegramService

    suffix = _uuid4().hex[:8]
    phone = "+79990060001"

    sender = Sender(
        workspace_id=test_workspace.id, slug=f"srld08-snd-{suffix}", name="block sender",
        phone="+79995558888", session_string="enc", role="sender",
        auth_status="ok", lifecycle_status="active",
        rate_per_min=4, rate_per_hour=20, rate_per_day=150,
    )
    async_db_session.add(sender)
    await async_db_session.commit()
    await async_db_session.refresh(sender)

    # Per-sender cache hit: registered + telegram_id + access_hash → send path reached.
    await async_db_session.execute(text("""
        INSERT INTO contacts_cache
            (id, workspace_id, sender_id, phone, telegram_id, access_hash, is_registered)
        VALUES (:id, :wid, :sid, :phone, 777, 888, true)
    """), {"id": str(_uuid4()), "wid": str(test_workspace.id), "sid": str(sender.id), "phone": phone})
    await async_db_session.commit()

    client = mock_telethon_client
    client.send_message = AsyncMock(side_effect=UserIsBlockedError(request=None))
    # Synchronous is_connected() so the finally: disconnect_client() guard is a
    # clean no-op (avoids a never-awaited-coroutine warning on the AsyncMock).
    client.is_connected = MagicMock(return_value=False)

    res = await TelegramService().send_message(
        client, phone, "Recipient", "hi",
        sender_id=str(sender.id), workspace_id=str(test_workspace.id),
    )

    assert res["success"] is False
    assert res["error"]["code"] == "USER_IS_BLOCKED", (
        "a recipient block must surface as the structured USER_IS_BLOCKED code "
        f"(SRLD-08/D-15), not generic SEND_FAILED; got {res['error']}"
    )
