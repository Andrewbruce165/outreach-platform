"""Phase 13 Plan 13-01 — Wave-0 RED test scaffold for even-pacing across the
campaign sending window (PACE-01..07).

These tests encode the EXPECTED behaviour of even-pacing BEFORE any production
code exists (Nyquist rule): every implementation task in plan 13-02 has a
concrete automated command from the start. The helper / predicate they exercise
(``_window_elapsed_fraction``, ``PACE_JITTER_*``, the pacing subquery in
``_process_next_for_sender``) does NOT exist yet — so the tests are genuinely
RED now and turn GREEN in 13-02. ``--collect-only`` stays clean because the
not-yet-existing symbols are imported INSIDE the test bodies.

PACE-01 — new jitter constants added, PROTECTED empirical constants unchanged.
PACE-02 — ``_window_elapsed_fraction`` pure-function math (tz / boundary /
          midnight-cross / degenerate), injectable ``now``, clamped to [0,1].
PACE-03 — expected-by-now pacing predicate in the candidate SELECT
          (over expected ⇒ blocked, under expected ⇒ allowed; SKIP LOCKED / LIMIT 8 intact).
PACE-04 — pace numerator counts from TODAY's window start (NOT trailing-24h);
          the two counters diverge.
PACE-05 — structural interval floor (narrow window + high limit ⇒ base floor
          binds, limit not reached, no crash).
PACE-06 — catch-up does not burst (≤1 new dialog per call; jitter present in source).
PACE-07 — follow-ups bypass pacing entirely; ``_check_rate_limits`` untouched.

Helpers below are copied VERBATIM from tests/test_queue_new_dialog_limit.py
(Phase 12) so this file mirrors the proven pattern. Do NOT behaviourally edit
them — only the surrounding tests are new.

Determinism (RESEARCH §Validation Architecture): no ``freezegun`` in the
project, so the pure helper takes an injectable ``now`` and the integration
tests pick fraction-robust window bounds (``work_hour_start=0, work_hour_end=24``)
so the wall-clock fraction cannot flip the assertion direction.

Tests run ONLY via the test-overlay:
    docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_even_pacing.py
NEVER bare ``docker compose run --rm api pytest`` (conftest guard DROP SCHEMA on prod).
"""

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.services import queue as queue_module
from app.services.queue import QueueWorker, MAX_NEW_CONTACTS_PER_HOUR

pytestmark = pytest.mark.asyncio


# ── Helpers (copied verbatim from tests/test_queue_new_dialog_limit.py) ────────


async def _insert_pending_item(
    db,
    *,
    workspace_id,
    sender_id,
    campaign_id,
    recipient_phone: str,
    scheduled_at_offset_minutes: int = -1,
) -> str:
    """Insert a pending (not-yet-sent) message_queue item, scheduled_at <= NOW()."""
    qid = str(uuid.uuid4())
    scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=scheduled_at_offset_minutes)
    await db.execute(text("""
        INSERT INTO message_queue (
            id, workspace_id, sender_id, campaign_id,
            item_type, status, recipient_phone, message_text, scheduled_at
        ) VALUES (
            :qid, :wid, :sid, :cid,
            'message', 'pending', :rp, 'hello', :sa
        )
    """), {
        "qid": qid, "wid": str(workspace_id), "sid": str(sender_id),
        "cid": str(campaign_id), "rp": recipient_phone, "sa": scheduled_at,
    })
    await db.commit()
    return qid


async def _seed_sent_dialog(
    db,
    *,
    workspace_id,
    sender_id,
    campaign_id,
    recipient_phone: str,
):
    """Seed a status='sent' row with a NON-NULL finished_at inside the 24h window.

    Done via a raw insert naming the finished_at column explicitly — the conftest
    test_queue_item_factory does NOT name finished_at, so factory rows would land
    with finished_at=NULL and the 24h cap COUNT would return 0 (cap never fires).
    """
    qid = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO message_queue (
            id, workspace_id, sender_id, campaign_id,
            item_type, status, recipient_phone, message_text,
            scheduled_at, finished_at
        ) VALUES (
            :qid, :wid, :sid, :cid,
            'message', 'sent', :rp, 'prior', NOW(), NOW()
        )
    """), {
        "qid": qid, "wid": str(workspace_id), "sid": str(sender_id),
        "cid": str(campaign_id), "rp": recipient_phone,
    })
    await db.commit()
    return qid


async def _set_cap(db, *, campaign_id, cap: int):
    """Set campaigns.max_new_dialogs_per_day for a campaign.

    The conftest test_campaign_factory does NOT accept this column as a kwarg
    (its INSERT column list is fixed), so we UPDATE it explicitly. The column
    exists with DEFAULT 50 (migration 033 / ORM server_default)."""
    await db.execute(text(
        "UPDATE campaigns SET max_new_dialogs_per_day = :cap WHERE id = :cid"
    ), {"cap": cap, "cid": str(campaign_id)})
    await db.commit()


async def _item_status(db, qid: str) -> str | None:
    row = (await db.execute(text(
        "SELECT status FROM message_queue WHERE id = :qid"
    ), {"qid": qid})).first()
    return row[0] if row else None


def _run_worker_capturing_picked(worker: QueueWorker):
    """Patch the worker so _process_next_for_sender exercises the real candidate
    SELECT but never hits Telegram. _check_rate_limits → True (isolate the
    new-dialog cap), _get_long_pause_seconds → None, _send_item → capture id."""
    captured: dict = {"picked": []}

    async def _fake_send(item_id):
        captured["picked"].append(str(item_id))

    cm_rate = patch.object(worker, "_check_rate_limits", new=AsyncMock(return_value=True))
    cm_pause = patch.object(worker, "_get_long_pause_seconds", new=AsyncMock(return_value=None))
    cm_send = patch.object(worker, "_send_item", side_effect=_fake_send)
    return captured, cm_rate, cm_pause, cm_send


# ── PACE-01: PROTECTED constants intact + new jitter constants ─────────────────


async def test_protected_constants_intact():
    """PACE-01 (D-08, CLAUDE.md guard): every PROTECTED empirical constant is
    unchanged, and the new pacing-jitter constants exist and are sane.

    Mirrors test_check_rate_limits_untouched from Phase 12. The jitter constants
    are imported INSIDE the test body so --collect-only stays clean before 13-02
    creates them.
    """
    # PROTECTED — must NOT be modified by Phase 13 (CLAUDE.md / RESEARCH §Project Constraints).
    assert queue_module.MIN_SEND_INTERVAL == 20
    assert queue_module.MAX_SEND_INTERVAL == 55
    assert queue_module.SEND_INTERVAL_FATIGUE == 0.5
    assert queue_module.LONG_PAUSE_EVERY_MIN == 12
    assert queue_module.LONG_PAUSE_EVERY_MAX == 25
    assert queue_module.LONG_PAUSE_MIN_SECS == 180
    assert queue_module.LONG_PAUSE_MAX_SECS == 600
    assert queue_module.MAX_NEW_CONTACTS_PER_HOUR == 15

    # NEW (Phase 13, PACE-01) — jitter multiplier bounds for the expected-by-now
    # count. RESEARCH recommends ±25% (0.75 / 1.25). Deferred import keeps
    # collection clean before 13-02 adds them.
    from app.services.queue import PACE_JITTER_LOW, PACE_JITTER_HIGH

    assert isinstance(PACE_JITTER_LOW, float)
    assert isinstance(PACE_JITTER_HIGH, float)
    assert 0 < PACE_JITTER_LOW < 1 < PACE_JITTER_HIGH, (
        "jitter bounds must straddle 1.0 (shrink below, stretch above the target)"
    )


# ── PACE-02: _window_elapsed_fraction pure-function math ───────────────────────


async def test_window_elapsed_fraction():
    """PACE-02 (D-01, D-02, D-05, D-06): the elapsed-fraction helper computes
    (window_start_utc, fraction in [0,1]) per-campaign timezone from
    work_hour_start/end and an injectable ``now``, clamped at both ends and
    safe on a degenerate (zero-width) window.

    Imported INSIDE the test body (deferred) so collection passes before 13-02.
    """
    from app.services.queue import _window_elapsed_fraction

    MSK = "Europe/Moscow"  # UTC+3, no DST — wall clock is deterministic

    # (a) now exactly at window start (09:00 MSK == 06:00 UTC) → fraction 0.0,
    #     window_start_utc equals that instant.
    now_a = datetime(2026, 6, 26, 6, 0, 0, tzinfo=timezone.utc)
    ws_a, frac_a = _window_elapsed_fraction(
        campaign_tz=MSK, work_hour_start=9, work_hour_end=20, now=now_a,
    )
    assert frac_a == pytest.approx(0.0, abs=1e-9)
    assert ws_a == now_a, "window_start_utc must equal the start instant"

    # (b) mid-window: 14:30 MSK == 11:30 UTC; window 9..20 (width 11h).
    #     elapsed = 5.5h → fraction ≈ 5.5/11 = 0.5.
    now_b = datetime(2026, 6, 26, 11, 30, 0, tzinfo=timezone.utc)
    ws_b, frac_b = _window_elapsed_fraction(
        campaign_tz=MSK, work_hour_start=9, work_hour_end=20, now=now_b,
    )
    assert 0.0 < frac_b < 1.0
    assert frac_b == pytest.approx((14.5 - 9) / 11, abs=1e-6)
    assert ws_b == datetime(2026, 6, 26, 6, 0, 0, tzinfo=timezone.utc)

    # (c) just before close: 19:59 MSK == 16:59 UTC → fraction near 1.0, <= 1.0.
    now_c = datetime(2026, 6, 26, 16, 59, 0, tzinfo=timezone.utc)
    _, frac_c = _window_elapsed_fraction(
        campaign_tz=MSK, work_hour_start=9, work_hour_end=20, now=now_c,
    )
    assert frac_c <= 1.0
    assert frac_c > 0.95

    # (d) before window start (08:00 MSK == 05:00 UTC) → negative raw → clamp 0.0.
    now_d = datetime(2026, 6, 26, 5, 0, 0, tzinfo=timezone.utc)
    _, frac_d = _window_elapsed_fraction(
        campaign_tz=MSK, work_hour_start=9, work_hour_end=20, now=now_d,
    )
    assert frac_d == pytest.approx(0.0, abs=1e-9)

    # (e) after close (21:00 MSK == 18:00 UTC) → raw >1 → saturate 1.0.
    now_e = datetime(2026, 6, 26, 18, 0, 0, tzinfo=timezone.utc)
    _, frac_e = _window_elapsed_fraction(
        campaign_tz=MSK, work_hour_start=9, work_hour_end=20, now=now_e,
    )
    assert frac_e == pytest.approx(1.0, abs=1e-9)

    # (f) degenerate / zero-width window (start == end) → NO ZeroDivisionError,
    #     fraction in [0,1] (helper must guard ``width or 24``).
    now_f = datetime(2026, 6, 26, 11, 30, 0, tzinfo=timezone.utc)
    _, frac_f = _window_elapsed_fraction(
        campaign_tz=MSK, work_hour_start=12, work_hour_end=12, now=now_f,
    )
    assert 0.0 <= frac_f <= 1.0
