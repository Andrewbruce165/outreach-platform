"""Phase 14 Wave-0 RED scaffold — CheckerService importContacts fallback + cleanup.

RESV-01 / D-02. Intentionally RED until Wave 3 (Plan 14-03) adds to
`CheckerService._check_phones_locked`:
  - an `ImportContactsRequest` fallback when `ResolvePhoneRequest` returns empty
    / raises PhoneNotOccupiedError, and
  - mandatory address-book cleanup via `DeleteContactsRequest` after each import
    (Pitfall 4 — uncleaned imports drift the behavioural profile → faster throttle;
    this is how the original checker died).

The `mock_telethon_client` fixture (added to conftest in Plan 14-01) is the
canonical Telethon client mock this test depends on; this file's presence also
satisfies the 14-01 acceptance criterion that `tests/test_checker.py` collects
cleanly with that fixture available. Deferred in-body import keeps collection
clean; the fallback/cleanup behaviour does not exist yet so the body fails (RED).
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def test_import_fallback_and_cleanup(mock_telethon_client):
    """ResolvePhone empty → importContacts fallback resolves → DeleteContacts cleanup.

    Wave 3 wires a fallback resolve helper that, given a live client, tries
    ResolvePhone first and falls back to ImportContacts; on a positive import it
    MUST immediately invoke DeleteContactsRequest to clean the address book.
    """
    # Wave 3 helper — does not exist yet. In-body import keeps collection clean.
    from app.services.checker import resolve_phone_with_fallback

    client = mock_telethon_client
    # ResolvePhone returns nothing (privacy edge), importContacts finds the user.
    client.set_response("ResolvePhoneRequest", None)

    class _User:
        id = 4242

    class _Imported:
        users = [_User()]
        imported = [object()]
        retry_contacts = []

    client.set_response("ImportContactsRequest", _Imported())
    client.set_response("DeleteContactsRequest", True)

    result = await resolve_phone_with_fallback(client, phone="+79990001234")

    assert result["is_registered"] is True
    assert result["telegram_id"] == 4242

    called = [name for name, _ in client.calls]
    assert "ImportContactsRequest" in called, "must fall back to importContacts"
    assert "DeleteContactsRequest" in called, "must clean address book after import (D-02)"
    # Cleanup must come AFTER the import that created the contact.
    assert called.index("DeleteContactsRequest") > called.index("ImportContactsRequest")


# ─── Phase 17 Wave-0 RED scaffold ────────────────────────────────────────────
# SRLD-01 (checker captures @username) + SRLD-07 (confidence-gated cache READ on
# the checker side). Intentionally RED until Plan 17-02 makes the checker stop
# discarding `user.username` and gates `_lookup_cache` against the matching
# contacts row's probe state. Deferred in-body imports keep --collect-only clean.


def _resolved_users(*, telegram_id: int, username: str | None = None):
    """Mirror the inline `_resolved_users` helper used elsewhere, but carry a
    `.username` on the resolved user (SRLD-01 — the field the checker drops today).

    Shapes a `contacts.ResolvedPeer`-like object: `.users[0]` is a `User` carrying
    BOTH `.id` and `.username`, exactly what `ResolvePhoneRequest` returns live.
    """
    class _User:
        id = telegram_id

        def __init__(self):
            self.username = username

    class _Resolved:
        users = [_User()]

    return _Resolved()


async def test_username_capture_in_resolve_phone(mock_telethon_client):
    """SRLD-01: `resolve_phone_with_fallback` returns the captured `@username`.

    The checker reads `result.users[0]` (a full `User` carrying `.username`) but
    today returns only `{is_registered, telegram_id}` — the public/transferable
    `username` is discarded (D-06). Plan 17-02 adds it to the return shape so the
    sender can do the cheap tier-2 `ResolveUsername` on it.

    RED today: `res["username"]` raises KeyError / is absent.
    """
    from app.services.checker import resolve_phone_with_fallback

    # Case A: ResolvePhone resolves directly and carries a username.
    client = mock_telethon_client
    client.set_response("ResolvePhoneRequest", _resolved_users(telegram_id=123, username="durov"))

    res = await resolve_phone_with_fallback(client, phone="+79990001111")

    assert res["is_registered"] is True
    assert res["telegram_id"] == 123
    assert res["username"] == "durov", "checker must CAPTURE @username from ResolvePhone (SRLD-01/D-06)"


async def test_username_capture_in_import_fallback(mock_telethon_client):
    """SRLD-01: the ImportContacts fallback path also captures `@username`.

    A registered-but-private number (find-by-phone hidden) only surfaces through
    `ImportContactsRequest`; the imported `User` still carries `.username`. The
    checker must capture it there too (D-06).

    RED today: `res["username"]` raises KeyError / is absent.
    """
    from app.services.checker import resolve_phone_with_fallback

    client = mock_telethon_client
    # ResolvePhone empty (privacy edge) → fall back to importContacts.
    client.set_response("ResolvePhoneRequest", None)

    class _ImportedUser:
        id = 4242
        username = "hidden_user"

    class _Imported:
        users = [_ImportedUser()]
        imported = [object()]
        retry_contacts = []

    client.set_response("ImportContactsRequest", _Imported())
    client.set_response("DeleteContactsRequest", True)

    res = await resolve_phone_with_fallback(client, phone="+79990001112")

    assert res["is_registered"] is True
    assert res["telegram_id"] == 4242
    assert res["username"] == "hidden_user", (
        "checker must capture @username from the ImportContacts fallback too (SRLD-01/D-06)"
    )


async def test_check_phones_batch_carries_username(mock_telethon_client, monkeypatch):
    """SRLD-01/02 regression: the BATCH producer (`check_phones` /
    `_check_phones_locked`) must thread the captured `@username` into each result
    dict — not only `resolve_phone_with_fallback`.

    Guards the integration gap found in live UAT (2026-06-30): the helper captured
    `user.username` but the batch path extracted only `is_registered`/`telegram_id`
    and built its result dict WITHOUT the `username` key, so the worker's
    `res.get("username")` was always None and `contacts.tg_username_resolved` never
    populated (0/16 on a healthy ca-account-1 batch). The earlier persistence test
    drove `_apply_results` directly with a synthetic username, bypassing this producer.
    """
    from unittest.mock import MagicMock

    from app.services.checker import checker_service

    client = mock_telethon_client
    client.set_response(
        "ResolvePhoneRequest", _resolved_users(telegram_id=777, username="captured_handle")
    )
    # AsyncMock.is_connected() would yield an un-awaited coroutine in the finally
    # disconnect guard — pin it to a clean no-op (same fix as the 17-01 send tests).
    client.is_connected = MagicMock(return_value=False)

    async def _fake_get_client(*args, **kwargs):
        return client

    monkeypatch.setattr(checker_service, "_get_client", _fake_get_client)

    summary = await checker_service.check_phones(
        workspace_id="00000000-0000-0000-0000-000000000000",
        checker_id="00000000-0000-0000-0000-000000000001",
        checker_slug="srld-regress-checker",
        encrypted_session="unused-mock-session",
        phones=["+79990003333"],
    )

    res = summary["results"][0]
    assert res["is_registered"] is True
    assert res["telegram_id"] == 777
    assert res["username"] == "captured_handle", (
        "the batch producer must carry the captured @username into its results so the "
        "worker persists tg_username_resolved (SRLD-01/02 — live UAT regression)"
    )


async def test_confidence_gated_cache_checker_read(async_db_session):
    """SRLD-07 (checker side): a `is_registered=false` cache row from a SUSPECT
    source is NOT served by `_lookup_cache` → forces a live re-resolve (D-12).

    The confidence signal lives on the matching `contacts` row (Phase 14:
    `tg_probe_state`/`tg_confidence`). `contacts_cache` is keyed on phone only, so
    the gate is a correlated lookup: a false cache row is served ONLY when the
    workspace's matching contact is clean+high-confidence. A suspect contact must
    suppress the blind false (the Igor cross-contamination root cause).

    `_lookup_cache` opens its OWN `AsyncSessionLocal()` session, so the seed rows
    must be COMMITTED (this test uses `async_db_session`, which commits).

    RED today: `_lookup_cache` returns the false row blind regardless of probe state.
    """
    from uuid import uuid4

    from app.models import Workspace, Sender, Folder, Contact
    from app.services.checker import CheckerService

    suffix = uuid4().hex[:8]

    # Workspace + a checker sender to own the cache row.
    ws = Workspace(name=f"SRLD07 checker-read {suffix}")
    async_db_session.add(ws)
    await async_db_session.commit()
    await async_db_session.refresh(ws)

    checker = Sender(
        workspace_id=ws.id, slug=f"srld07-chk-{suffix}", name="SRLD07 checker",
        phone="+79995550001", session_string="enc", role="checker",
        auth_status="ok", lifecycle_status="active",
        rate_per_min=4, rate_per_hour=20, rate_per_day=150,
    )
    async_db_session.add(checker)
    await async_db_session.commit()
    await async_db_session.refresh(checker)

    folder = Folder(workspace_id=ws.id, name="SRLD07 folder")
    async_db_session.add(folder)
    await async_db_session.commit()
    await async_db_session.refresh(folder)

    suspect_phone = "+79990002222"
    clean_phone = "+79990003333"

    # SUSPECT case: a false cache row + a matching contact flagged suspect.
    await async_db_session.execute(text("""
        INSERT INTO contacts_cache (id, workspace_id, sender_id, phone, telegram_id, is_registered)
        VALUES (:id, :wid, :sid, :phone, NULL, false)
    """), {"id": str(uuid4()), "wid": str(ws.id), "sid": str(checker.id), "phone": suspect_phone})
    async_db_session.add(Contact(
        workspace_id=ws.id, folder_id=folder.id, phone=suspect_phone,
        full_name="Suspect false", tg_status="pending", tg_probe_state="suspect",
        tg_confidence=None,
    ))

    # CLEAN control: a false cache row + a matching contact clean+high-confidence.
    await async_db_session.execute(text("""
        INSERT INTO contacts_cache (id, workspace_id, sender_id, phone, telegram_id, is_registered)
        VALUES (:id, :wid, :sid, :phone, NULL, false)
    """), {"id": str(uuid4()), "wid": str(ws.id), "sid": str(checker.id), "phone": clean_phone})
    async_db_session.add(Contact(
        workspace_id=ws.id, folder_id=folder.id, phone=clean_phone,
        full_name="Clean false", tg_status="not_registered", tg_probe_state="clean",
        tg_confidence="high",
    ))
    await async_db_session.commit()

    svc = CheckerService()

    # Suspect false → NOT served (gated) → fall through to live resolve.
    suspect_hit = await svc._lookup_cache(str(ws.id), suspect_phone)
    assert suspect_hit is None, (
        "a suspect-source is_registered=false must NOT be served from cache (SRLD-07/D-12) "
        f"— forces live re-resolve; got {suspect_hit}"
    )

    # Clean+high false → IS served (no needless live resolve).
    clean_hit = await svc._lookup_cache(str(ws.id), clean_phone)
    assert clean_hit is not None, "a clean+high-confidence false IS served from cache (D-12 negative control)"
    assert clean_hit["is_registered"] is False
