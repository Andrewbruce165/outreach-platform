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
                           checker_trip_count (capped at max_backoff).
  PROBE-04b 2026-06-30 fix — the escalating ladder must NOT reset on the weak
                           ≤5-sample recovery probe (that let the pool flap forever
                           at the base cooldown, trip_count stuck at 0). Recovery
                           returns the checker to rotation with trip history intact;
                           the ladder resets ONLY after a clean REAL resolve batch.
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


async def test_clean_recovery_preserves_trip_count(
    async_db_session, test_workspace, test_checker
):
    """2026-06-30 fix: _recover_checkers on a clean ≤5-sample probe restores the
    checker to rotation (restriction_status='none', restricted_until NULL) but does
    NOT reset checker_trip_count. The weak burst probe cannot prove genuine health,
    so the escalating-backoff ladder must persist — otherwise a still-throttled
    checker flaps forever at the base cooldown. The ladder resets only after a clean
    REAL batch (see test_clean_real_batch_resets_trip_count)."""
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
    # Batch A (quick 260703-j25): _recover_checkers now probes via the LIVE-ONLY
    # probe_control (not the cache-consulting check_phones). Patch that primitive.
    with patch(
        "app.services.contact_check_worker.checker_service.probe_control",
        new=AsyncMock(side_effect=lambda phones, **kw: _clean_probe_summary(phones)),
    ):
        await worker._recover_checkers()

    row = (
        await async_db_session.execute(
            text(
                "SELECT restriction_status, restricted_until, checker_trip_count "
                "FROM senders WHERE id = :id"
            ),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.restriction_status == "none", "clean recovery restores the checker"
    assert row.restricted_until is None, "clean recovery clears the cooldown"
    assert row.checker_trip_count == 3, (
        "recovery must PRESERVE the trip ladder — the ≤5-sample probe is too weak to "
        "reset it; only a clean real batch may"
    )


async def test_reset_checker_trip_helper_zeroes_and_guards(
    async_db_session, test_workspace, test_checker
):
    """_reset_checker_trip sets checker_trip_count → 0 for a tripped checker, and is
    a guarded no-op when the checker is already at 0 (the WHERE clause skips the
    write — no error, no row churn)."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker_id = str(test_checker.id)
    worker = ContactCheckWorker()

    await async_db_session.execute(
        text("UPDATE senders SET checker_trip_count = 5 WHERE id = :id"),
        {"id": checker_id},
    )
    await async_db_session.commit()

    await worker._reset_checker_trip(checker_id)
    row = (
        await async_db_session.execute(
            text("SELECT checker_trip_count FROM senders WHERE id = :id"),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.checker_trip_count == 0, "a clean real batch resets the trip ladder"

    # Already 0 → guarded no-op, must not raise.
    await worker._reset_checker_trip(checker_id)
    row = (
        await async_db_session.execute(
            text("SELECT checker_trip_count FROM senders WHERE id = :id"),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.checker_trip_count == 0


def _clean_batch_summary(phones, **kw):
    """A clean REAL resolve batch — every phone registered, no flood (NOT a throttle
    signal: registered > 0)."""
    return {
        "checked": len(phones),
        "registered": len(phones),
        "not_registered": 0,
        "flood_wait_hit": False,
        "results": [
            {"phone": p, "is_registered": True, "telegram_id": 6000 + i, "from_cache": False}
            for i, p in enumerate(phones)
        ],
    }


def _throttle_batch_summary(phones, **kw):
    """An anomalous all-empty live batch (≥ ANOMALY_MIN_BATCH, registered=0) — the
    14-05 inline throttle signature."""
    return {
        "checked": len(phones),
        "registered": 0,
        "not_registered": len(phones),
        "flood_wait_hit": False,
        "results": [
            {"phone": p, "is_registered": False, "from_cache": False} for p in phones
        ],
    }


async def test_clean_real_batch_resets_trip_count(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """A clean REAL resolve batch (probe_state stays 'clean') resets the escalating-
    backoff ladder to 0 — the genuine health proof that the weak recovery probe is
    not. Drives the full _tick resolve path with a tripped checker (trip_count=3)."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker_id = str(test_checker.id)
    await async_db_session.execute(
        text("UPDATE senders SET checker_trip_count = 3 WHERE id = :id"),
        {"id": checker_id},
    )
    await async_db_session.commit()

    await test_contacts_factory(count=1, tg_status="pending")

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(side_effect=_clean_batch_summary),
    ):
        await worker._tick()

    row = (
        await async_db_session.execute(
            text(
                "SELECT restriction_status, checker_trip_count FROM senders WHERE id = :id"
            ),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.restriction_status == "none", "clean batch leaves the checker active"
    assert row.checker_trip_count == 0, "a clean real batch resets the trip ladder"


async def test_throttle_real_batch_keeps_trip_count(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """A throttled REAL batch (14-05 anomalous all-empty signal) degrades the checker
    inline and must NOT reset the ladder — instead _flag_checker_degraded bumps it
    (3 → 4). Proves the reset is gated on a genuinely clean batch only."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker_id = str(test_checker.id)
    await async_db_session.execute(
        text("UPDATE senders SET checker_trip_count = 3 WHERE id = :id"),
        {"id": checker_id},
    )
    await async_db_session.commit()

    # ≥ ANOMALY_MIN_BATCH (8) pending contacts so the all-empty batch trips the signal.
    await test_contacts_factory(count=10, tg_status="pending")

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(side_effect=_throttle_batch_summary),
    ):
        await worker._tick()

    row = (
        await async_db_session.execute(
            text(
                "SELECT restriction_status, checker_trip_count FROM senders WHERE id = :id"
            ),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.restriction_status == "spam_limited", "throttled batch degrades the checker"
    assert row.checker_trip_count == 4, (
        "a throttled batch must escalate (3 → 4), never reset the ladder"
    )


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


# ─── CR-03 (re-review 260706): failed recovery probe must re-arm the cooldown ─
#
# Regression: after Batch A switched recovery to the live-only probe_control, a
# FAILED probe just `continue`d — restricted_until stayed in the past, so the
# recovery SELECT re-picked the same checker on EVERY ~5s poll tick: a silent
# live-probe hot loop (~20k resolves/day) against an already-throttled account
# (prod incident 2026-07-04..06, sender-8525079460).


def _dirty_probe_summary(phones: list[str]) -> dict:
    """A throttled probe result — every control number 'not registered' (all live)."""
    return {
        "checked": len(phones),
        "registered": 0,
        "not_registered": len(phones),
        "flood_wait_hit": False,
        "results": [
            {"phone": p, "is_registered": False, "from_cache": False} for p in phones
        ],
    }


async def test_failed_recovery_probe_rearms_cooldown_and_escalates(
    async_db_session, test_workspace, test_checker
):
    """A recovery probe that comes back dirty (live misses on known-live controls)
    is throttle evidence: the checker must be re-armed via the escalating ladder
    (trip += 1, restricted_until pushed into the FUTURE) — and, critically, a
    second _recover_checkers pass must then be a no-op (no hot loop)."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker_id = str(test_checker.id)
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
        "app.services.contact_check_worker.checker_service.probe_control",
        new=AsyncMock(side_effect=lambda phones, **kw: _dirty_probe_summary(phones)),
    ) as probe_mock:
        await worker._recover_checkers()
        first_pass_calls = probe_mock.await_count
        assert first_pass_calls >= 1, "elapsed cooldown → checker is probed once"

        row = (
            await async_db_session.execute(
                text(
                    "SELECT restriction_status, restricted_until, checker_trip_count "
                    "FROM senders WHERE id = :id"
                ),
                {"id": checker_id},
            )
        ).fetchone()
        assert row.restriction_status == "spam_limited", "dirty probe must NOT recover"
        assert row.restricted_until is not None and row.restricted_until > datetime.now(
            timezone.utc
        ), "CR-03: failed probe must re-arm restricted_until into the future"
        assert row.checker_trip_count == 4, "throttle evidence climbs the trip ladder"

        # The hot-loop guard itself: with the cooldown re-armed, an immediate second
        # recovery pass must not probe this checker again.
        await worker._recover_checkers()
        assert probe_mock.await_count == first_pass_calls, (
            "re-armed checker must not be re-probed on the next tick (hot-loop guard)"
        )


async def test_recovery_probe_exception_rearms_base_cooldown_without_trip_bump(
    async_db_session, test_workspace, test_checker
):
    """An infra failure (probe raises: network error, dead session) is NOT throttle
    evidence: re-arm restricted_until by the base cooldown so the loop is bounded,
    but do NOT bump the trip ladder and do NOT clear the restriction."""
    from app.services.contact_check_worker import ContactCheckWorker

    checker_id = str(test_checker.id)
    base = get_settings().contact_check_cooldown_seconds
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
    before = datetime.now(timezone.utc)
    with patch(
        "app.services.contact_check_worker.checker_service.probe_control",
        new=AsyncMock(side_effect=ConnectionError("boom")),
    ):
        await worker._recover_checkers()

    row = (
        await async_db_session.execute(
            text(
                "SELECT restriction_status, restricted_until, checker_trip_count "
                "FROM senders WHERE id = :id"
            ),
            {"id": checker_id},
        )
    ).fetchone()
    assert row.restriction_status == "spam_limited"
    assert row.restricted_until is not None and row.restricted_until > before, (
        "CR-03: an exception must still re-arm the cooldown (bounded retry, no hot loop)"
    )
    delta = (row.restricted_until - before).total_seconds()
    assert abs(delta - base) < 60, f"exception path re-arms ≈ base cooldown, got {delta}s"
    assert row.checker_trip_count == 3, (
        "an infra error is not throttle evidence — the trip ladder must not bump"
    )
