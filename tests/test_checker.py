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
