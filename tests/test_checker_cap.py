"""Phase 14 Wave-0 RED scaffold — burst-cap + durable daily-cap.

RESV-02 / D-10. Intentionally RED until Wave 2 adds:
  - a per-batch burst-cap (≤ contact_check_burst_cap) so a single tick resolves
    at most that many phones for one checker,
  - a per-checker daily-cap derived from a DURABLE source (contacts_cache writes
    today per sender_id), so the cap survives a fresh ContactCheckWorker()
    instance (Pitfall 5 — worker restart must not reset the counter).

Deferred in-body imports keep `--collect-only` clean; the cap behaviour does not
exist yet so the bodies fail (genuinely RED).
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_resolution_state(async_db_session):
    """Delete committed pending contacts / cache rows after each test.

    The session-scoped test DB is NOT rolled back for committed rows (conftest
    async_db_session only rolls back). ContactCheckWorker._tick() resolves ANY
    workspace's pending contacts globally, so leftover pending rows from one test
    would leak into a later worker test. Clean up post-test to keep _tick tests
    isolated.
    """
    yield
    await async_db_session.execute(text("DELETE FROM contacts_cache"))
    await async_db_session.execute(text("DELETE FROM contacts WHERE tg_status = 'pending'"))
    await async_db_session.commit()


async def test_burst_cap(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """The per-batch resolve count is driven by contact_check_burst_cap (D-10).

    With cap+10 pending contacts and one checker, the worker's claim SELECT must
    hand UP TO the burst cap (and never MORE than it) to check_phones in a single
    tick. Asserting the cap is the ACTIVE limit (not the legacy hard-coded
    batch_size=5) makes this genuinely RED until Wave 2 wires the cap — the
    current worker resolves only 5/tick regardless of the 30 cap.
    """
    from app.config import get_settings

    cap = get_settings().contact_check_burst_cap

    # Seed cap + 10 pending contacts — more than one batch can resolve.
    await test_contacts_factory(count=cap + 10, tg_status="pending")

    # Echo back is_registered=True for every phone the worker passes.
    def _fake(phones, **kwargs):
        return {
            "checked": len(phones),
            "registered": len(phones),
            "not_registered": 0,
            "flood_wait_hit": False,
            "results": [
                {"phone": p, "is_registered": True, "telegram_id": 1000 + i}
                for i, p in enumerate(phones)
            ],
        }

    from app.services.contact_check_worker import ContactCheckWorker

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(side_effect=lambda **kw: _fake(**kw)),
    ) as mock:
        await worker._tick()

    assert mock.await_count >= 1
    total_resolved = sum(len(c.kwargs["phones"]) for c in mock.await_args_list)
    # Never exceed the cap in a single tick (the ceiling).
    assert total_resolved <= cap, (
        f"burst-cap violated: {total_resolved} resolves in one tick (cap={cap})"
    )
    # The cap is the ACTIVE per-batch budget — the worker must resolve up to it
    # when enough work is pending (fails today: legacy batch_size=5 ≪ cap).
    assert total_resolved == cap, (
        f"burst-cap not wired: resolved {total_resolved}/tick, expected cap={cap}"
    )


async def test_daily_cap_durable(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """Per-checker daily count is derived from a durable source and survives a
    fresh ContactCheckWorker() instance (Pitfall 5).

    Pre-seed today's contacts_cache writes up to the daily cap for this checker,
    then a BRAND-NEW worker instance must refuse to resolve more for that checker
    on this tick (the cap is read from the DB, not a process-local counter).
    """
    from app.config import get_settings
    from app.services.contact_check_worker import ContactCheckWorker

    daily_cap = get_settings().contact_check_daily_cap
    checker_id = str(test_checker.id)
    wid = str(test_workspace.id)

    # Pre-seed `daily_cap` contacts_cache rows written TODAY by this checker.
    for i in range(daily_cap):
        await async_db_session.execute(
            text(
                """
                INSERT INTO contacts_cache
                    (workspace_id, sender_id, phone, is_registered, updated_at)
                VALUES (:wid, :sid, :phone, true, NOW())
                """
            ),
            {"wid": wid, "sid": checker_id, "phone": f"+7999{i:07d}"},
        )
    await async_db_session.commit()

    await test_contacts_factory(count=5, tg_status="pending")

    # A FRESH worker (no in-memory state carried) must honour the durable cap.
    fresh_worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(),
    ) as mock:
        await fresh_worker._tick()
        mock.assert_not_awaited()  # daily cap already reached → no resolves
