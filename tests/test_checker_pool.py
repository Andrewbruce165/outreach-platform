"""Phase 14 Wave-0 RED scaffold — checker pool rotation + N=1 cooldown.

RESV-03 / D-04. Intentionally RED until Wave 2 adds pool-aware selection:
  - rotation across ≥2 eligible checkers (both usable, load spread),
  - at N=1, a single checker on cooldown (restricted_until in the future) →
    _tick processes nothing and never emits a false not_registered (D-04: the
    pool must PAUSE resolution rather than lie when no healthy checker exists).

Deferred in-body imports keep `--collect-only` clean; pool behaviour does not
exist yet so the bodies fail (genuinely RED).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_resolution_state(async_db_session):
    """Delete committed pending contacts / cache rows after each test (see test_checker_cap)."""
    yield
    await async_db_session.execute(text("DELETE FROM contacts_cache"))
    await async_db_session.execute(text("DELETE FROM contacts WHERE tg_status = 'pending'"))
    await async_db_session.commit()


async def test_rotation_picks_eligible(
    async_db_session, test_workspace, test_sender_factory, test_contacts_factory
):
    """Two eligible checkers → load spreads / both usable across resolution.

    With two healthy checkers and a batch of pending contacts, the pool-aware
    selection (Wave 2) must be able to use BOTH checkers (rotation), not pin all
    work onto a single account. Asserted via a per-checker pick helper that does
    not exist yet.
    """
    # Wave 2 helper — does not exist yet.
    from app.services.contact_check_worker import select_eligible_checkers

    c1 = await test_sender_factory(role="checker", slug="checker-a")
    c2 = await test_sender_factory(role="checker", slug="checker-b")
    await test_contacts_factory(count=4, tg_status="pending")

    eligible = await select_eligible_checkers(workspace_id=str(test_workspace.id))
    eligible_ids = {str(c) for c in eligible}
    assert str(c1.id) in eligible_ids
    assert str(c2.id) in eligible_ids
    assert len(eligible_ids) == 2, "both healthy checkers must be eligible for rotation"


async def test_rotation_n1_pauses(
    async_db_session, test_workspace, test_sender_factory, test_contacts_factory
):
    """Single checker on cooldown (restricted_until future) → _tick resolves
    nothing and writes NO false not_registered (D-04).

    The whole point of the phase: at N=1 with the only checker resting, resolution
    PAUSES — it must never fall through to marking pending contacts not_registered.
    """
    from app.services.contact_check_worker import ContactCheckWorker

    cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=30)
    await test_sender_factory(
        role="checker",
        slug="only-checker-resting",
        restriction_status="spam_limited",
        restricted_until=cooldown_until,
    )
    contacts = await test_contacts_factory(count=3, tg_status="pending")

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(),
    ) as mock:
        processed = await worker._tick()
        mock.assert_not_awaited()

    assert processed == 0
    # No contact may have been finalized as not_registered — they stay pending.
    rows = (
        await async_db_session.execute(
            text("SELECT tg_status FROM contacts WHERE id = ANY(:ids)"),
            {"ids": [str(c.id) for c in contacts]},
        )
    ).fetchall()
    assert all(r.tg_status == "pending" for r in rows), (
        "resting-checker N=1 must not emit false not_registered (D-04)"
    )
