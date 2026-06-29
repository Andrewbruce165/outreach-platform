"""quick-260629-b7j — checker probe-burn fix (RED-first).

The health-probe (`_probe_cycle`) was burning the checker pool: it fired every
~5s poll tick, ignored the Plan-14-07 `checker_rest_until` rest, was not counted
against the per-checker daily budget, and a tripped checker auto-recovered in a
fixed ~15min only to re-trip. This module asserts the four fixes plus the 14-05
suspect-rollback invariant (must stay intact — the fix is purely throughput/
longevity, NOT a finalization change):

  PROBE-01  rest gate     — a checker resting on checker_rest_until is NOT probed.
  PROBE-02  interval      — a given checker is probed at most once per
                           contact_check_probe_interval_seconds, not every tick.
  PROBE-03  budget gate   — a checker at/over the daily_cap is NOT probed.
  PROBE-04  escalating    — _flag_checker_degraded escalates cooldown by
                           checker_trip_count (capped at max_backoff); a clean
                           recovery resets checker_trip_count to 0.
  INVARIANT (regression)  — a suspect/throttled batch still rolls not_registered
                           → pending (14-05), proving the probe changes did not
                           alter finalization.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config import get_settings

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_resolution_state(async_db_session):
    """Delete committed pending contacts / cache rows after each test."""
    yield
    await async_db_session.execute(text("DELETE FROM contacts_cache"))
    await async_db_session.execute(text("DELETE FROM contacts WHERE tg_status = 'pending'"))
    await async_db_session.commit()


def _clean_probe_summary(phones: list[str]) -> dict:
    """A clean probe result — every control number registered (no miss)."""
    return {
        "checked": len(phones),
        "registered": len(phones),
        "not_registered": 0,
        "flood_wait_hit": False,
        "results": [
            {"phone": p, "is_registered": True, "telegram_id": 5000 + i, "from_cache": False}
            for i, p in enumerate(phones)
        ],
    }


# ─── PROBE-01: rest gate ─────────────────────────────────────────────────────


async def test_probe_skips_resting_checker(
    async_db_session, test_workspace, test_checker
):
    """A checker with checker_rest_until > NOW() is NOT returned by _probe_cycle's
    selection — probe_checker is never invoked for it (the probe path honors the
    14-07 post-batch rest, closing the burn-during-rest hole)."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker_id = str(test_checker.id)
    # Put the checker on a benign post-batch rest in the future.
    await async_db_session.execute(
        text(
            "UPDATE senders SET checker_rest_until = NOW() + INTERVAL '10 minutes' "
            "WHERE id = :id"
        ),
        {"id": checker_id},
    )
    await async_db_session.commit()

    worker = ContactCheckWorker()
    with patch.object(
        worker, "probe_checker", new=AsyncMock(return_value=False)
    ) as probe_mock:
        await worker._probe_cycle()
        # Scoped to THIS checker — _probe_cycle selects across all workspaces, so a
        # leaked-but-eligible checker from another test must not flip this assertion.
        probed_ids = {str(c.args[0]) for c in probe_mock.await_args_list}
        assert checker_id not in probed_ids, "a resting checker must not be probed"


async def test_probe_runs_for_non_resting_checker(
    async_db_session, test_workspace, test_checker
):
    """A checker with checker_rest_until <= NOW() (or NULL) IS eligible for the
    probe — probe_checker is invoked for it."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker_id = str(test_checker.id)
    # Ensure rest is in the past (eligible).
    await async_db_session.execute(
        text(
            "UPDATE senders SET checker_rest_until = NOW() - INTERVAL '1 minute' "
            "WHERE id = :id"
        ),
        {"id": checker_id},
    )
    await async_db_session.commit()

    worker = ContactCheckWorker()
    with patch.object(
        worker, "probe_checker", new=AsyncMock(return_value=False)
    ) as probe_mock:
        await worker._probe_cycle()
        probe_mock.assert_awaited()
        called_ids = {str(c.args[0]) for c in probe_mock.await_args_list}
        assert checker_id in called_ids


# ─── PROBE-02: interval throttle ─────────────────────────────────────────────


async def test_probe_throttled_within_interval(
    async_db_session, test_workspace, test_checker
):
    """Two _probe_cycle calls within contact_check_probe_interval_seconds probe a
    given checker only ONCE; after the interval elapses (manipulate the in-memory
    last-probe timestamp) it probes again."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker_id = str(test_checker.id)
    worker = ContactCheckWorker()

    with patch.object(
        worker, "probe_checker", new=AsyncMock(return_value=False)
    ) as probe_mock:
        await worker._probe_cycle()  # probes (first time)
        await worker._probe_cycle()  # within interval → no-op for this checker
        first_calls = [
            c for c in probe_mock.await_args_list if str(c.args[0]) == checker_id
        ]
        assert len(first_calls) == 1, "second cycle within interval must not re-probe"

        # Age the in-memory last-probe timestamp beyond the interval.
        interval = get_settings().contact_check_probe_interval_seconds
        worker._last_probe_at[checker_id] = datetime.now(timezone.utc) - timedelta(
            seconds=interval + 5
        )

        await worker._probe_cycle()  # interval elapsed → probes again
        total_calls = [
            c for c in probe_mock.await_args_list if str(c.args[0]) == checker_id
        ]
        assert len(total_calls) == 2, "after interval elapses the checker is probed again"


# ─── PROBE-03: budget gate ───────────────────────────────────────────────────


async def test_probe_skips_over_budget_checker(
    async_db_session, test_workspace, test_checker
):
    """A checker already at/over contact_check_daily_cap (today's contacts_cache
    writes) is NOT probed by _probe_cycle — the probe respects the same daily
    budget the resolve _tick uses, so probes cannot silently blow it."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker_id = str(test_checker.id)
    daily_cap = get_settings().contact_check_daily_cap

    # Seed exactly daily_cap contacts_cache rows for this checker today → over budget.
    for i in range(daily_cap):
        await async_db_session.execute(
            text(
                "INSERT INTO contacts_cache "
                "(workspace_id, sender_id, phone, is_registered, updated_at) "
                "VALUES (:wid, :sid, :phone, true, NOW())"
            ),
            {
                "wid": str(test_workspace.id),
                "sid": checker_id,
                "phone": f"+79{i:09d}",
            },
        )
    await async_db_session.commit()

    worker = ContactCheckWorker()
    with patch.object(
        worker, "probe_checker", new=AsyncMock(return_value=False)
    ) as probe_mock:
        await worker._probe_cycle()
        called_ids = {str(c.args[0]) for c in probe_mock.await_args_list}
        assert checker_id not in called_ids, "over-budget checker must not be probed"


# ─── PROBE-04: escalating cooldown + reset ───────────────────────────────────


async def test_escalating_cooldown_doubles_per_trip(
    async_db_session, test_workspace, test_checker
):
    """_flag_checker_degraded on a fresh checker (trip_count=0) sets restricted_until
    ≈ NOW()+cooldown and bumps checker_trip_count to 1; a second trip (1→2) sets a
    cooldown ≈ cooldown*2 and bumps to 2."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker_id = str(test_checker.id)
    base = get_settings().contact_check_cooldown_seconds
    worker = ContactCheckWorker()

    # Trip 1 — trip_count 0 → 1, cooldown ≈ base * 2^0 = base.
    before1 = datetime.now(timezone.utc)
    await worker._flag_checker_degraded(checker_id, miss_count=2)
    row1 = (
        await async_db_session.execute(
            text(
                "SELECT checker_trip_count, restricted_until, restriction_status "
                "FROM senders WHERE id = :id"
            ),
            {"id": checker_id},
        )
    ).fetchone()
    assert row1.checker_trip_count == 1
    assert row1.restriction_status == "spam_limited"
    delta1 = (row1.restricted_until - before1).total_seconds()
    assert abs(delta1 - base) < 60, f"first cooldown ≈ base ({base}s), got {delta1}s"

    # Trip 2 — trip_count 1 → 2, cooldown ≈ base * 2^1 = 2*base.
    before2 = datetime.now(timezone.utc)
    await worker._flag_checker_degraded(checker_id, miss_count=2)
    row2 = (
        await async_db_session.execute(
            text(
                "SELECT checker_trip_count, restricted_until FROM senders WHERE id = :id"
            ),
            {"id": checker_id},
        )
    ).fetchone()
    assert row2.checker_trip_count == 2
    delta2 = (row2.restricted_until - before2).total_seconds()
    assert abs(delta2 - 2 * base) < 60, f"second cooldown ≈ 2*base, got {delta2}s"


async def test_escalating_cooldown_capped_at_max_backoff(
    async_db_session, test_workspace, test_checker
):
    """The escalating cooldown is capped at contact_check_max_backoff_seconds — a
    checker with a high trip_count does not back off past the ceiling."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker_id = str(test_checker.id)
    max_backoff = get_settings().contact_check_max_backoff_seconds

    # Seed a high trip_count so base * 2^(new_trip-1) would exceed the ceiling.
    await async_db_session.execute(
        text("UPDATE senders SET checker_trip_count = 30 WHERE id = :id"),
        {"id": checker_id},
    )
    await async_db_session.commit()

    worker = ContactCheckWorker()
    before = datetime.now(timezone.utc)
    await worker._flag_checker_degraded(checker_id, miss_count=2)
    row = (
        await async_db_session.execute(
            text("SELECT restricted_until FROM senders WHERE id = :id"),
            {"id": checker_id},
        )
    ).fetchone()
    delta = (row.restricted_until - before).total_seconds()
    assert abs(delta - max_backoff) < 60, (
        f"cooldown must be capped at max_backoff ({max_backoff}s), got {delta}s"
    )


async def test_clean_recovery_resets_trip_count(
    async_db_session, test_workspace, test_checker
):
    """_recover_checkers on a clean probe resets checker_trip_count back to 0 (and
    restores restriction_status='none'), so a checker that genuinely recovers starts
    its next backoff ladder from the base again."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker_id = str(test_checker.id)
    # Simulate a degraded checker with an elapsed cooldown and a non-zero trip count.
    await async_db_session.execute(
        text(
            "UPDATE senders SET restriction_status = 'spam_limited', "
            "lifecycle_status = 'paused', "
            "restricted_until = NOW() - INTERVAL '1 minute', "
            "checker_trip_count = 3 WHERE id = :id"
        ),
        {"id": checker_id},
    )
    await async_db_session.commit()

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(side_effect=lambda phones, **kw: _clean_probe_summary(phones)),
    ):
        await worker._recover_checkers()

    row = (
        await async_db_session.execute(
            text(
                "SELECT restriction_status, checker_trip_count FROM senders WHERE id = :id"
            ),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.restriction_status == "none", "clean recovery restores the checker"
    assert row.checker_trip_count == 0, "clean recovery resets the trip ladder"


# ─── INVARIANT: 14-05 suspect rollback preserved ─────────────────────────────


async def test_suspect_batch_still_rolls_back_not_registered(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """Regression guard: a suspect batch from a throttled checker still rolls
    not_registered → pending (14-05 invariant) — the probe-burn changes did NOT
    alter finalization."""
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

    await apply_results_with_confidence(
        items, summary, checker_id=str(test_checker.id), probe_state="suspect"
    )

    reg_row = (
        await async_db_session.execute(
            text("SELECT tg_status FROM contacts WHERE id = :id"),
            {"id": str(reg_contact.id)},
        )
    ).fetchone()
    assert reg_row.tg_status == "registered", "registered kept (false positives impossible)"

    notreg_row = (
        await async_db_session.execute(
            text("SELECT tg_status, tg_checked_at FROM contacts WHERE id = :id"),
            {"id": str(notreg_contact.id)},
        )
    ).fetchone()
    assert notreg_row.tg_status == "pending", "suspect not_registered must roll back, not finalize"
    assert notreg_row.tg_checked_at is None, "claim timestamp cleared for re-check"
