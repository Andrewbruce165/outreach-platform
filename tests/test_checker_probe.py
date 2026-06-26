"""Phase 14 Wave-0 RED scaffold — health-probe miss-counting + suspect rollback.

RESV-01 / D-05 / D-07. These tests are intentionally RED until Wave 2-3 add:
  - a control-probe path on the worker that resolves known-live numbers LIVE
    (bypassing contacts_cache) and counts consecutive misses per checker,
  - degradation on ≥2 consecutive misses → mark checker spam_limited + write a
    sender_restriction_events row,
  - suspect-batch rollback: the degraded checker's not_registered results roll
    back to 'pending' (tg_checked_at cleared), registered results untouched.

Deferred in-body imports of the not-yet-existing helpers keep `--collect-only`
clean (mirrors the Phase 13 13-01 scaffold approach); the helpers/behaviours do
not exist yet, so the test BODIES fail (genuinely RED, real assertions).
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

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


# ─── Control-set / probe miss counting (D-05) ────────────────────────────────


async def test_two_misses_flags(async_db_session, test_workspace, test_checker):
    """≥2 consecutive control-set misses → checker marked spam_limited + audit row.

    D-05: a single miss is stochastic noise; two consecutive control-set misses
    (a known-live number resolving as not_registered twice running) flag the
    checker degraded. The mark MUST go through the Phase-10 restriction infra
    (restriction_status='spam_limited' + a sender_restriction_events row), NEVER
    by nuking auth_status (Pitfall 2).
    """
    # Wave 2 helper — does not exist yet. In-body import keeps collection clean.
    from app.services.contact_check_worker import run_control_probe  # noqa: F401

    checker_id = str(test_checker.id)

    async def _one_miss():
        # A control number (known-live) resolving as not_registered = a miss.
        with patch(
            "app.services.contact_check_worker.checker_service.check_phones",
            new=AsyncMock(
                return_value={
                    "checked": 1,
                    "registered": 0,
                    "not_registered": 1,
                    "flood_wait_hit": False,
                    "results": [{"phone": "+79990000001", "is_registered": False}],
                }
            ),
        ):
            return await run_control_probe(checker_id=checker_id)

    await _one_miss()  # miss #1 — must NOT flag (noise)
    row = (
        await async_db_session.execute(
            text("SELECT restriction_status FROM senders WHERE id = :id"),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.restriction_status == "none", "single miss must not flag (D-05 noise)"

    await _one_miss()  # miss #2 — consecutive → flag

    row = (
        await async_db_session.execute(
            text("SELECT restriction_status FROM senders WHERE id = :id"),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.restriction_status == "spam_limited"

    events = (
        await async_db_session.execute(
            text(
                "SELECT event_type FROM sender_restriction_events "
                "WHERE sender_id = :id ORDER BY created_at DESC"
            ),
            {"id": checker_id},
        )
    ).fetchall()
    assert any(e.event_type == "spam_limited" for e in events)


async def test_single_miss_no_flag(async_db_session, test_workspace, test_checker):
    """One control-set miss is noise (D-05) — checker stays restriction_status='none'."""
    from app.services.contact_check_worker import run_control_probe

    checker_id = str(test_checker.id)
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(
            return_value={
                "checked": 1,
                "registered": 0,
                "not_registered": 1,
                "flood_wait_hit": False,
                "results": [{"phone": "+79990000001", "is_registered": False}],
            }
        ),
    ):
        await run_control_probe(checker_id=checker_id)

    row = (
        await async_db_session.execute(
            text("SELECT restriction_status FROM senders WHERE id = :id"),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.restriction_status == "none"


# ─── Suspect-batch rollback (D-07) ───────────────────────────────────────────


async def test_suspect_rollback_keeps_registered(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """Degraded checker: batch not_registered → pending (tg_checked_at cleared);
    registered rows untouched (D-07 / Pitfall 3).

    A throttle produces FALSE NEGATIVES only — never false positives — so the
    `registered` results of a suspect batch are kept while the `not_registered`
    results roll back to `pending` for re-check by another checker.
    """
    # Wave 3 helper — does not exist yet.
    from app.services.contact_check_worker import apply_results_with_confidence

    contacts = await test_contacts_factory(count=2, tg_status="pending")
    reg_contact, notreg_contact = contacts[0], contacts[1]

    summary = {
        "checked": 2,
        "registered": 1,
        "not_registered": 1,
        "flood_wait_hit": False,
        "results": [
            {"phone": reg_contact.phone, "is_registered": True, "telegram_id": 777},
            {"phone": notreg_contact.phone, "is_registered": False},
        ],
    }
    items = [
        type("It", (), {"contact_id": reg_contact.id, "phone": reg_contact.phone, "username": None}),
        type("It", (), {"contact_id": notreg_contact.id, "phone": notreg_contact.phone, "username": None}),
    ]

    # Checker is degraded → not_registered must NOT finalize.
    await apply_results_with_confidence(
        items, summary, checker_id=str(test_checker.id), probe_state="suspect"
    )

    reg_row = (
        await async_db_session.execute(
            text("SELECT tg_status, tg_telegram_id FROM contacts WHERE id = :id"),
            {"id": str(reg_contact.id)},
        )
    ).fetchone()
    assert reg_row.tg_status == "registered"
    assert reg_row.tg_telegram_id == 777

    notreg_row = (
        await async_db_session.execute(
            text(
                "SELECT tg_status, tg_checked_at FROM contacts WHERE id = :id"
            ),
            {"id": str(notreg_contact.id)},
        )
    ).fetchone()
    assert notreg_row.tg_status == "pending", "suspect not_registered must roll back, not finalize"
    assert notreg_row.tg_checked_at is None, "claim timestamp must be cleared for re-check"


# ─── Inline flood/throttle-aware finalization (Plan 14-05, Gap A) ────────────
#
# The 14-04 live-smoke gap: a freshly-throttled checker (FloodWait, or an
# anomalous all-empty resolve batch — checked=20..30 reg=0) finalized its empty
# resolves as not_registered/high/clean BEFORE the decoupled ≥2-miss control-probe
# (which runs in the _run loop, NOT in _tick) ever flagged it. These tests drive
# _tick() DIRECTLY (so _probe_cycle never populates _degraded_this_tick) and prove
# the INLINE trigger fires from the resolve tick itself: a flood/throttle batch is
# treated as suspect (rollback to pending, no high-confidence) AND the checker is
# degraded inline (spam_limited + event row + paused + cooldown), leaving rotation
# on the next tick. "Unknown" (pending) always beats a false "not_registered".
#
# ANOMALY_MIN_BATCH threshold for the all-empty branch is 8 (Task 2 matches this);
# the anomalous-batch test seeds 10 contacts (> 8) so the all-empty signal fires.


def _flood_summary(phones: list[str]) -> dict:
    """A FloodWait summary: flood_wait_hit=True, all results not_registered."""
    return {
        "checked": len(phones),
        "registered": 0,
        "not_registered": len(phones),
        "flood_wait_hit": True,
        "results": [{"phone": p, "is_registered": False} for p in phones],
    }


def _anomalous_empty_summary(phones: list[str]) -> dict:
    """The 14-04 signature: flood_wait_hit=False but checked=N, reg=0, all empty.
    Live (non-cache) results so the all-empty anomaly branch counts them."""
    return {
        "checked": len(phones),
        "registered": 0,
        "not_registered": len(phones),
        "flood_wait_hit": False,
        "results": [
            {"phone": p, "is_registered": False, "from_cache": False} for p in phones
        ],
    }


async def test_flood_batch_rolls_back_to_pending(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """A FloodWait resolve batch NEVER finalizes not_registered — every seeded
    contact rolls back to tg_status='pending' (tg_checked_at NULL), none carries
    tg_confidence='high', and the rolled-back rows are tg_probe_state='suspect'."""
    from app.services.contact_check_worker import ContactCheckWorker

    contacts = await test_contacts_factory(count=3, tg_status="pending")
    phones = [c.phone for c in contacts]

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=_flood_summary(phones)),
    ):
        await worker._tick()

    rows = (
        await async_db_session.execute(
            text(
                "SELECT tg_status, tg_checked_at, tg_confidence, tg_probe_state "
                "FROM contacts WHERE id = ANY(:ids)"
            ),
            {"ids": [str(c.id) for c in contacts]},
        )
    ).fetchall()
    assert len(rows) == 3
    assert all(r.tg_status == "pending" for r in rows), "flood batch must roll back, not finalize"
    assert all(r.tg_checked_at is None for r in rows), "claim timestamp cleared for re-check"
    assert all(r.tg_confidence != "high" for r in rows), "flood batch must never carry high confidence"
    assert all(r.tg_probe_state == "suspect" for r in rows)


async def test_flood_batch_writes_no_high_confidence(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """Over the seeded rows, COUNT(tg_confidence='high') is 0 after a flood batch."""
    from app.services.contact_check_worker import ContactCheckWorker

    contacts = await test_contacts_factory(count=4, tg_status="pending")
    phones = [c.phone for c in contacts]

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=_flood_summary(phones)),
    ):
        await worker._tick()

    high = (
        await async_db_session.execute(
            text(
                "SELECT COUNT(*) AS n FROM contacts "
                "WHERE id = ANY(:ids) AND tg_confidence = 'high'"
            ),
            {"ids": [str(c.id) for c in contacts]},
        )
    ).fetchone()
    assert high.n == 0


async def test_flood_batch_degrades_checker_inline(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """After a flood tick the checker is degraded INLINE — without a prior ≥2-miss
    control-probe: restriction_status='spam_limited', lifecycle_status='paused',
    restricted_until in the future, AND a sender_restriction_events row exists with
    event_type='spam_limited'. auth_status is UNCHANGED ('ok' — Pitfall 2)."""
    from app.services.contact_check_worker import ContactCheckWorker

    contacts = await test_contacts_factory(count=3, tg_status="pending")
    phones = [c.phone for c in contacts]
    checker_id = str(test_checker.id)

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=_flood_summary(phones)),
    ):
        await worker._tick()

    row = (
        await async_db_session.execute(
            text(
                "SELECT restriction_status, lifecycle_status, restricted_until, "
                "auth_status FROM senders WHERE id = :id"
            ),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.restriction_status == "spam_limited"
    assert row.lifecycle_status == "paused"
    assert row.restricted_until is not None
    assert row.auth_status == "ok", "Pitfall 2 — degrade must NOT touch auth_status"

    events = (
        await async_db_session.execute(
            text(
                "SELECT event_type FROM sender_restriction_events "
                "WHERE sender_id = :id"
            ),
            {"id": checker_id},
        )
    ).fetchall()
    assert any(e.event_type == "spam_limited" for e in events), (
        "inline degrade must emit a sender_restriction_events row"
    )


async def test_flood_checker_left_out_of_next_selection(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """After the flood tick degrades the checker, a re-run of _tick() (contacts are
    still pending) does NOT await check_phones for the now-flagged checker — the
    RESV-05 JOIN-LATERAL gate excludes it; contacts stay pending."""
    from app.services.contact_check_worker import ContactCheckWorker

    contacts = await test_contacts_factory(count=3, tg_status="pending")
    phones = [c.phone for c in contacts]

    worker = ContactCheckWorker()
    # First tick: flood → rollback + inline degrade.
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=_flood_summary(phones)),
    ):
        await worker._tick()

    # Second tick: the only checker is now spam_limited/paused → gate excludes it.
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=_flood_summary(phones)),
    ) as mock2:
        await worker._tick()
        mock2.assert_not_awaited()

    rows = (
        await async_db_session.execute(
            text("SELECT tg_status FROM contacts WHERE id = ANY(:ids)"),
            {"ids": [str(c.id) for c in contacts]},
        )
    ).fetchall()
    assert all(r.tg_status == "pending" for r in rows)


async def test_no_healthy_checker_leaves_pending(
    async_db_session, test_workspace, test_sender_factory, test_contacts_factory
):
    """D-04 safe-stop at N=0 healthy: with the only checker already flagged/paused,
    _tick() leaves the seeded contacts pending and awaits check_phones 0 times.
    'Unknown' (pending) beats a false 'not_registered'."""
    from app.services.contact_check_worker import ContactCheckWorker

    # The only checker is already degraded (spam_limited + paused).
    await test_sender_factory(
        role="checker",
        slug="paused-checker",
        restriction_status="spam_limited",
        lifecycle_status="paused",
    )
    contacts = await test_contacts_factory(count=2, tg_status="pending")

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(),
    ) as mock:
        await worker._tick()
        mock.assert_not_awaited()

    rows = (
        await async_db_session.execute(
            text("SELECT tg_status FROM contacts WHERE id = ANY(:ids)"),
            {"ids": [str(c.id) for c in contacts]},
        )
    ).fetchall()
    assert all(r.tg_status == "pending" for r in rows)


async def test_anomalous_all_empty_batch_treated_as_throttle(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """The 14-04 signature — flood_wait_hit=False but checked=N (>= ANOMALY_MIN_BATCH=8),
    registered=0, not_registered=N (all live, non-cache) — must ALSO roll back to
    pending and degrade the checker inline (the trigger covers flood AND all-empty).
    Batch size 10 > the threshold 8 so the all-empty signal fires."""
    from app.services.contact_check_worker import ContactCheckWorker

    contacts = await test_contacts_factory(count=10, tg_status="pending")
    phones = [c.phone for c in contacts]
    checker_id = str(test_checker.id)

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=_anomalous_empty_summary(phones)),
    ):
        await worker._tick()

    rows = (
        await async_db_session.execute(
            text(
                "SELECT tg_status, tg_confidence FROM contacts WHERE id = ANY(:ids)"
            ),
            {"ids": [str(c.id) for c in contacts]},
        )
    ).fetchall()
    assert len(rows) == 10
    assert all(r.tg_status == "pending" for r in rows), "anomalous all-empty batch must roll back"
    assert all(r.tg_confidence != "high" for r in rows)

    checker = (
        await async_db_session.execute(
            text(
                "SELECT restriction_status, lifecycle_status FROM senders WHERE id = :id"
            ),
            {"id": checker_id},
        )
    ).fetchone()
    assert checker.restriction_status == "spam_limited"
    assert checker.lifecycle_status == "paused"
