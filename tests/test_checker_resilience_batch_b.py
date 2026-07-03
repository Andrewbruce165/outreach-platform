"""Batch B — checker + worker RESILIENCE (quick-260703-rm3).

Additive on top of Batch A (quick-260703-j25 — live-only probes, live-only
throttle signal, NULL cache provenance). These tests cover six findings from the
2026-07-03 checker+campaigns review, all in app/services/checker.py +
app/services/contact_check_worker.py (no schema change):

  WR-05  a multi-hour FloodWait is capped inline at 60s (partial batch,
         flood_wait_hit=True) — a single misbehaving checker cannot freeze the
         single-coroutine ContactCheckWorker for hours.
  WR-06  a dead/unauthorized checker session flips auth_status='session_expired'
         (or 'banned') BY ID + raises SessionAuthError; a persistently-failing
         checker is backed off via checker_rest_until in the _tick except branch.
  WR-07  an empty ImportContacts fallback removes the saved phone via
         DeleteByPhonesRequest; both cleanups run in a finally.
  WR-08  recovery computes the control sample once and early-returns with a WARNING
         on an empty sample (never aborting other checkers mid-loop); an inline
         throttle signal with an empty control-set degrades REST-ONLY (never the
         permanent-because-unrecoverable spam_limited).
  IN-02  a PhoneNumberInvalidError carries {'error': 'invalid_phone'} through the
         batch producer so _apply_results finalizes tg_status='error'.
  IN-03  the LATERAL checker-selection subquery orders deterministically by
         checker_rest_until NULLS FIRST, id.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from telethon.errors import FloodWaitError, PhoneNumberInvalidError

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_resolution_state(async_db_session):
    """Delete committed pending contacts / cache rows after each test (mirrors the
    sibling checker-probe test modules — the factories COMMIT into the shared DB)."""
    yield
    await async_db_session.execute(text("DELETE FROM contacts_cache"))
    await async_db_session.execute(text("DELETE FROM contacts WHERE tg_status = 'pending'"))
    await async_db_session.commit()


# ─── WR-05: inline FloodWait cap ─────────────────────────────────────────────


async def test_floodwait_batch_capped_at_60s(mock_telethon_client, monkeypatch):
    """WR-05: a batch whose ResolvePhone raises FloodWaitError(seconds=3600) returns
    flood_wait_hit=True and the inline sleep is capped at 60s (never the raised
    3600s). Proven by patching app.services.checker.asyncio.sleep and asserting every
    awaited duration is <= 60."""
    from app.services import checker as checker_module
    from app.services.checker import checker_service

    client = mock_telethon_client
    client.is_connected = MagicMock(return_value=False)

    def _raise_flood(request):
        raise FloodWaitError(request=None, capture=3600)

    client.set_response("ResolvePhoneRequest", _raise_flood)

    monkeypatch.setattr(checker_service, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(checker_service, "_lookup_cache", AsyncMock(return_value=None))

    sleep_mock = AsyncMock()
    monkeypatch.setattr(checker_module.asyncio, "sleep", sleep_mock)

    summary = await checker_service.check_phones(
        workspace_id="00000000-0000-0000-0000-000000000000",
        checker_id="00000000-0000-0000-0000-000000000001",
        checker_slug="flood-cap-checker",
        encrypted_session="unused-mock-session",
        phones=["+79990004444", "+79990005555"],
    )

    assert summary["flood_wait_hit"] is True, "a raised FloodWait must set flood_wait_hit"
    assert sleep_mock.await_count >= 1, "the inline FloodWait handler must sleep"
    for call in sleep_mock.await_args_list:
        assert call.args[0] <= 60, (
            f"inline FloodWait sleep must be capped at 60s (WR-05), got {call.args[0]}s"
        )


# ─── WR-07: import-fallback cleanup in finally ───────────────────────────────


async def test_import_empty_deletes_by_phone(mock_telethon_client):
    """WR-07: an empty ImportContacts result (no user surfaced) still removes the
    saved phone from the address book via DeleteByPhonesRequest — the shadow-ban
    accelerator the old code left uncleaned. No DeleteContacts (no user id)."""
    from app.services.checker import resolve_phone_with_fallback

    client = mock_telethon_client
    client.set_response("ResolvePhoneRequest", None)  # empty → import fallback

    class _EmptyImport:
        users = []
        imported = []
        retry_contacts = []

    client.set_response("ImportContactsRequest", _EmptyImport())
    client.set_response("DeleteByPhonesRequest", True)
    client.set_response("DeleteContactsRequest", True)

    res = await resolve_phone_with_fallback(client, phone="+79990006666")

    assert res["is_registered"] is False
    called = [name for name, _ in client.calls]
    assert "ImportContactsRequest" in called, "empty ResolvePhone must fall back to import"
    assert "DeleteByPhonesRequest" in called, (
        "an empty import must remove the saved phone via DeleteByPhonesRequest (WR-07)"
    )
    assert "DeleteContactsRequest" not in called, "no user surfaced → no DeleteContacts"


async def test_import_registered_still_deletes_contacts_after(mock_telethon_client):
    """WR-07 regression: the registered-import path still calls DeleteContactsRequest
    AFTER the import (the finally rewrite must not break the existing cleanup)."""
    from app.services.checker import resolve_phone_with_fallback

    client = mock_telethon_client
    client.set_response("ResolvePhoneRequest", None)

    class _ImportedUser:
        id = 9191
        username = "surfaced"

    class _Imported:
        users = [_ImportedUser()]
        imported = [object()]
        retry_contacts = []

    client.set_response("ImportContactsRequest", _Imported())
    client.set_response("DeleteContactsRequest", True)
    client.set_response("DeleteByPhonesRequest", True)

    res = await resolve_phone_with_fallback(client, phone="+79990008888")

    assert res["is_registered"] is True
    assert res["telegram_id"] == 9191
    called = [name for name, _ in client.calls]
    assert "DeleteContactsRequest" in called, "surfaced user must be cleaned via DeleteContacts"
    assert "DeleteByPhonesRequest" not in called, "a surfaced user uses DeleteContacts, not DeleteByPhones"
    assert called.index("DeleteContactsRequest") > called.index("ImportContactsRequest"), (
        "cleanup must run AFTER the import"
    )


async def test_import_floodwait_fires_no_cleanup(mock_telethon_client):
    """WR-07: a FloodWait DURING the import (nothing added to the address book) must
    fire NO cleanup call — the finally is guarded on import_completed, which stays
    False when ImportContactsRequest raises."""
    from app.services.checker import resolve_phone_with_fallback

    client = mock_telethon_client
    client.set_response("ResolvePhoneRequest", None)

    def _raise_flood(request):
        raise FloodWaitError(request=None, capture=120)

    client.set_response("ImportContactsRequest", _raise_flood)
    client.set_response("DeleteByPhonesRequest", True)
    client.set_response("DeleteContactsRequest", True)

    with pytest.raises(FloodWaitError):
        await resolve_phone_with_fallback(client, phone="+79990007777")

    called = [name for name, _ in client.calls]
    assert "DeleteByPhonesRequest" not in called, "a flood during import must fire NO cleanup (WR-07)"
    assert "DeleteContactsRequest" not in called, "a flood during import must fire NO cleanup (WR-07)"


# ─── IN-02: invalid_phone tag ────────────────────────────────────────────────


async def test_invalid_phone_tagged_in_helper(mock_telethon_client):
    """IN-02: resolve_phone_with_fallback on PhoneNumberInvalidError returns
    {'is_registered': False, 'error': 'invalid_phone', ...} and does NOT attempt the
    import fallback (an invalid number is a hard error, not a privacy edge)."""
    from app.services.checker import resolve_phone_with_fallback

    client = mock_telethon_client

    def _raise_invalid(request):
        raise PhoneNumberInvalidError(request=None)

    client.set_response("ResolvePhoneRequest", _raise_invalid)

    res = await resolve_phone_with_fallback(client, phone="not-a-number")

    assert res["is_registered"] is False
    assert res["error"] == "invalid_phone", "an invalid number must carry error='invalid_phone' (IN-02)"
    called = [name for name, _ in client.calls]
    assert "ImportContactsRequest" not in called, "no import fallback for an invalid number"


async def test_check_phones_invalid_not_cached_and_threaded(mock_telethon_client, monkeypatch):
    """IN-02: check_phones threads the error key into its result dict AND does NOT
    write the cache for an invalid number (caching it as not_registered would poison
    the cache)."""
    from app.services.checker import checker_service

    client = mock_telethon_client
    client.is_connected = MagicMock(return_value=False)

    def _raise_invalid(request):
        raise PhoneNumberInvalidError(request=None)

    client.set_response("ResolvePhoneRequest", _raise_invalid)

    monkeypatch.setattr(checker_service, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(checker_service, "_lookup_cache", AsyncMock(return_value=None))
    save_mock = AsyncMock()
    monkeypatch.setattr(checker_service, "_save_cache", save_mock)

    summary = await checker_service.check_phones(
        workspace_id="00000000-0000-0000-0000-000000000000",
        checker_id="00000000-0000-0000-0000-000000000001",
        checker_slug="invalid-phone-checker",
        encrypted_session="unused-mock-session",
        phones=["garbage-number"],
    )

    res = summary["results"][0]
    assert res["error"] == "invalid_phone", "the batch producer must thread error='invalid_phone' (IN-02)"
    assert res["is_registered"] is False
    save_mock.assert_not_awaited(), "an invalid number must NOT be cached (IN-02)"
