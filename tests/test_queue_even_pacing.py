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
from contextlib import contextmanager
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
    """Set the sender-wide daily new-chat budget for the campaign's workspace.

    Phase 22 (D-01/D-05): the new-dialog cap is now driven by the ACCOUNT grade
    budget — sender_grade_settings.level1_chats_per_day for a level-1 sender
    (test senders default to current_level=1). The legacy per-campaign cap column
    was dropped in 22-06 (mig 059), so we only upsert the workspace grade-settings
    row so a level-1 sender resolves budget == cap."""
    wid = (await db.execute(text(
        "SELECT workspace_id FROM campaigns WHERE id = :cid"
    ), {"cid": str(campaign_id)})).scalar()
    await db.execute(text("""
        INSERT INTO sender_grade_settings (workspace_id, level1_chats_per_day)
        VALUES (:wid, :cap)
        ON CONFLICT (workspace_id)
        DO UPDATE SET level1_chats_per_day = EXCLUDED.level1_chats_per_day
    """), {"wid": str(wid), "cap": cap})
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


# ── Pace numerator helper (mirrors _count_in_window_sent but window-start floor) ─


async def _count_since_window_start_sent(db, *, sender_id, campaign_id, since) -> int:
    """COUNT(DISTINCT recipient_phone) of status='sent' rows for this
    (sender,campaign) with finished_at >= ``since`` — mirrors the Phase 13
    pacing numerator (D-06: counted from TODAY's window start, NOT trailing-24h).
    Used to assert divergence from the Phase 12 24h cap counter."""
    return (await db.execute(text("""
        SELECT COUNT(DISTINCT recipient_phone) FROM message_queue
        WHERE sender_id = :sid
          AND campaign_id = :cid
          AND status = 'sent'
          AND finished_at >= :since
    """), {"sid": str(sender_id), "cid": str(campaign_id), "since": since})).scalar()


@contextmanager
def _pin_pacing(*, window_start_utc, frac, jitter=1.0):
    """Pin the two non-deterministic inputs to the pacing predicate so the
    expected-by-now math is exact and the assertion direction cannot flip on
    wall-clock or RNG:

      * ``_window_elapsed_fraction`` (wall-clock dependent) → fixed
        ``(window_start_utc, frac)``;
      * ``random.uniform`` (per-call jitter) → fixed ``jitter``.

    With both pinned, ``expected_now == max_new_dialogs_per_day * frac * jitter``
    deterministically. The real ``_window_elapsed_fraction`` is exercised
    independently by the PACE-02 unit test; here we only need the predicate's
    behaviour for a KNOWN (window_start, expected_now). ``window_start_utc`` must
    sit at/after any ``finished_at`` rows the test wants the pace numerator to
    count and before any it wants excluded.
    """
    def _fixed(**_kwargs):
        return window_start_utc, frac

    with patch.object(queue_module, "_window_elapsed_fraction", _fixed), \
         patch.object(queue_module.random, "uniform", lambda _lo, _hi: jitter):
        yield


def _assert_pacing_predicate_wired():
    """Guard that the expected-by-now pacing predicate is actually wired into the
    candidate SELECT (RESEARCH Pattern 2 / threat-model: the implementation must
    bind ``:expected_now`` / ``:window_start_utc`` rather than interpolate).

    This makes the behavioural integration tests genuinely RED against the
    current (pre-13-02) code instead of passing for the wrong reason (e.g. the
    Phase 12 cap coincidentally blocking, or the no-predicate path coincidentally
    picking). 13-02 adds the bound predicate → these turn GREEN for the right
    reason.
    """
    src = inspect.getsource(QueueWorker._process_next_for_sender)
    assert "expected_now" in src, (
        "pacing predicate not wired yet — _process_next_for_sender must bind "
        ":expected_now (the expected-by-now count, D-05)"
    )
    assert "window_start_utc" in src, (
        "pacing predicate not wired yet — _process_next_for_sender must bind "
        ":window_start_utc (today's window-start floor, D-06)"
    )


# ── PACE-03: expected-by-now pacing predicate in the candidate SELECT ──────────


async def test_pacing_gate(async_db_session, test_running_campaign_factory):
    """PACE-03 (D-05, D-06, D-07, D-09, D-10): a new-dialog item is eligible iff
    new dialogs opened since TODAY's window start are below the expected-by-now
    count. Cap (Phase 12) is NOT the binding constraint here.

    Fraction-robust setup (RESEARCH determinism recipe): work_hour_start=0,
    work_hour_end=24 so the wall-clock fraction lands in (0,1] deterministically.
    """
    # RED now: the pacing predicate does not exist in the SELECT yet. Without
    # this guard the behavioural assertions below would pass for the WRONG reason
    # (the Phase 12 cap, not pacing). 13-02 wires the predicate → GREEN.
    _assert_pacing_predicate_wired()

    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    sid = senders[0].id
    wid = camp["workspace_id"]
    cid = camp["id"]

    # Pacing inputs are pinned via _pin_pacing so the assertion direction is set
    # purely by (count_opened vs expected_now), never by wall-clock fraction or
    # jitter — this is what keeps the test deterministic across time-of-day/RNG.
    # The daily cap is deliberately set ABOVE count_opened so the Phase 12 cap is
    # NOT the binding constraint — the expected-by-now pace predicate is.
    now = datetime.now(timezone.utc)
    ws = now - timedelta(hours=2)  # window start 2h ago: seeded sent rows (NOW()) count

    # ── Case 1: OVER expected ⇒ blocked.
    # cap=10 (cap not binding: 2 opened < 10); frac=0.1 → expected_now = 10×0.1 = 1.0;
    # 2 new dialogs opened since window start → count_opened (2) ≥ expected_now (1.0)
    # ⇒ a fresh new dialog is paced out.
    await _set_cap(async_db_session, campaign_id=cid, cap=10)
    await _seed_sent_dialog(async_db_session, workspace_id=wid, sender_id=sid,
                            campaign_id=cid, recipient_phone="+79991110001")
    await _seed_sent_dialog(async_db_session, workspace_id=wid, sender_id=sid,
                            campaign_id=cid, recipient_phone="+79991110002")

    blocked_qid = await _insert_pending_item(
        async_db_session, workspace_id=wid, sender_id=sid,
        campaign_id=cid, recipient_phone="+79991110003",
    )

    worker = QueueWorker()
    captured, cm_rate, cm_pause, cm_send = _run_worker_capturing_picked(worker)
    with _pin_pacing(window_start_utc=ws, frac=0.1), cm_rate, cm_pause, cm_send:
        await worker._process_next_for_sender(sid)

    assert blocked_qid not in captured["picked"], (
        "new dialog over expected-by-now must NOT be selected (D-05)"
    )
    assert await _item_status(async_db_session, blocked_qid) == "pending", (
        "paced-out new-dialog item must stay pending"
    )

    # ── Case 2: UNDER expected ⇒ allowed (fresh campaign, count_opened == 0).
    camp2, senders2 = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    sid2 = senders2[0].id
    wid2 = camp2["workspace_id"]
    cid2 = camp2["id"]
    # cap=10, frac=0.9 → expected_now = 10×0.9 = 9.0; count_opened == 0 ⇒ 0 < 9 ⇒ allowed.
    await _set_cap(async_db_session, campaign_id=cid2, cap=10)

    allowed_qid = await _insert_pending_item(
        async_db_session, workspace_id=wid2, sender_id=sid2,
        campaign_id=cid2, recipient_phone="+79992220001",
    )

    worker2 = QueueWorker()
    captured2, cm_rate2, cm_pause2, cm_send2 = _run_worker_capturing_picked(worker2)
    with _pin_pacing(window_start_utc=ws, frac=0.9), cm_rate2, cm_pause2, cm_send2:
        await worker2._process_next_for_sender(sid2)

    assert allowed_qid in captured2["picked"], (
        "new dialog under expected-by-now must be selected (D-05)"
    )
    assert await _item_status(async_db_session, allowed_qid) == "processing"

    # ── Pitfall 5 guard: the candidate SELECT keeps its Phase 4/12 invariants.
    src = inspect.getsource(QueueWorker._process_next_for_sender)
    assert "FOR UPDATE OF mq SKIP LOCKED" in src, (
        "pacing predicate must not drop FOR UPDATE OF mq SKIP LOCKED"
    )
    assert "LIMIT 8" in src, "pacing predicate must not drop LIMIT 8"


# ── PACE-04: pace numerator = today's window start, NOT trailing-24h ───────────


async def test_pace_counter_window_start(
    async_db_session, test_running_campaign_factory
):
    """PACE-04 (D-06): the pacing numerator counts new dialogs opened since
    TODAY's window start, a DISTINCT counter from the Phase 12 trailing-24h cap.

    Setup so the two counters diverge: a narrow window whose start is only
    minutes ago (work_hour_start = current UTC hour, end = +1h), plus a row
    finished 2h ago — that row is inside the trailing-24h cap window but BEFORE
    today's window start, so it counts toward the cap counter but NOT toward the
    pace numerator. The fresh never-contacted new dialog therefore stays eligible
    by pace (pace count == 0) and IS picked.
    """
    _assert_pacing_predicate_wired()  # RED until 13-02 wires the predicate.

    now = datetime.now(timezone.utc)
    cur_hour = now.hour
    # Window: [cur_hour, cur_hour+1) UTC. timezone defaults to UTC in the factory
    # so window-start ≈ top of this hour (minutes ago). Avoid the 23→0 wrap edge.
    if cur_hour == 23:
        cur_hour = 22
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=cur_hour, work_hour_end=cur_hour + 1, work_days_mask=127,
    )
    sid = senders[0].id
    wid = camp["workspace_id"]
    cid = camp["id"]
    # Force UTC so "window start ≈ top of the current UTC hour" holds regardless
    # of the factory's default timezone.
    await async_db_session.execute(text(
        "UPDATE campaigns SET timezone = 'UTC' WHERE id = :cid"
    ), {"cid": str(cid)})
    await async_db_session.commit()
    await _set_cap(async_db_session, campaign_id=cid, cap=50)

    # A prior 'sent' finished 2h ago: inside trailing-24h, but BEFORE today's
    # window start (which is < 1h ago) → counts for the cap, NOT for the pace.
    old_phone = "+79993330001"
    old_qid = str(uuid.uuid4())
    await async_db_session.execute(text("""
        INSERT INTO message_queue (
            id, workspace_id, sender_id, campaign_id,
            item_type, status, recipient_phone, message_text,
            scheduled_at, finished_at
        ) VALUES (
            :qid, :wid, :sid, :cid,
            'message', 'sent', :rp, 'older', NOW() - INTERVAL '2 hours',
            NOW() - INTERVAL '2 hours'
        )
    """), {
        "qid": old_qid, "wid": str(wid), "sid": str(sid),
        "cid": str(cid), "rp": old_phone,
    })
    await async_db_session.commit()

    # Divergence guard: the cap counter sees the 2h-old row; the pace numerator
    # (floor = top of the current hour) does not.
    window_start_approx = now.replace(minute=0, second=0, microsecond=0)
    cap_count = (await async_db_session.execute(text("""
        SELECT COUNT(DISTINCT recipient_phone) FROM message_queue
        WHERE sender_id = :sid AND campaign_id = :cid AND status = 'sent'
          AND finished_at >= NOW() - INTERVAL '24 hours'
    """), {"sid": str(sid), "cid": str(cid)})).scalar()
    pace_count = await _count_since_window_start_sent(
        async_db_session, sender_id=sid, campaign_id=cid, since=window_start_approx,
    )
    assert cap_count == 1, "trailing-24h cap counter must include the 2h-old row"
    assert pace_count == 0, (
        "pace numerator (today's window start) must EXCLUDE the pre-window row (D-06)"
    )

    # A fresh never-contacted new dialog: pace count is 0 ⇒ eligible by pace.
    fresh_qid = await _insert_pending_item(
        async_db_session, workspace_id=wid, sender_id=sid,
        campaign_id=cid, recipient_phone="+79993330002",
    )

    worker = QueueWorker()
    captured, cm_rate, cm_pause, cm_send = _run_worker_capturing_picked(worker)
    with cm_rate, cm_pause, cm_send:
        await worker._process_next_for_sender(sid)

    assert fresh_qid in captured["picked"], (
        "new dialog must be eligible — the pre-window row counts only for the "
        "24h cap, not the window-start pace numerator (D-06)"
    )
    assert await _item_status(async_db_session, fresh_qid) == "processing"


# ── PACE-05: structural interval floor (narrow window + high limit) ────────────


async def test_interval_floor(async_db_session, test_running_campaign_factory):
    """PACE-05 (D-03, D-10): with a narrow window and a high
    max_new_dialogs_per_day, the limit physically cannot fit at the base 20–55s
    floor. There is NO numeric max(target, base) to assert — the clamp emerges
    structurally: the base interval gate (untouched) is the binding floor and the
    expected-by-now predicate simply lets at most the already-allowed quantity
    through. We assert the run does not crash and at most one item leaves.
    """
    _assert_pacing_predicate_wired()  # RED until 13-02 wires the predicate.

    now = datetime.now(timezone.utc)
    cur_hour = now.hour
    if cur_hour == 23:
        cur_hour = 22
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=cur_hour, work_hour_end=cur_hour + 1, work_days_mask=127,
    )
    sid = senders[0].id
    wid = camp["workspace_id"]
    cid = camp["id"]
    await async_db_session.execute(text(
        "UPDATE campaigns SET timezone = 'UTC' WHERE id = :cid"
    ), {"cid": str(cid)})
    await async_db_session.commit()
    # High limit in a 1h window: target interval = 3600s / 500 ≈ 7s < base 20s.
    # The base 20–55s gate (which _check_rate_limits enforces BEFORE selection,
    # mocked True here) is the binding floor — the limit is simply not reached
    # (D-03 "safety over volume"); no special-casing, no max() expression.
    await _set_cap(async_db_session, campaign_id=cid, cap=500)

    q1 = await _insert_pending_item(async_db_session, workspace_id=wid, sender_id=sid,
                                    campaign_id=cid, recipient_phone="+79994440001")
    q2 = await _insert_pending_item(async_db_session, workspace_id=wid, sender_id=sid,
                                    campaign_id=cid, recipient_phone="+79994440002")

    worker = QueueWorker()
    captured, cm_rate, cm_pause, cm_send = _run_worker_capturing_picked(worker)
    with cm_rate, cm_pause, cm_send:
        # Must not raise (no ZeroDivisionError / no crash on a tight window).
        await worker._process_next_for_sender(sid)

    # One item per call regardless of how generous the limit is (the worker
    # sends exactly one item per _process_next_for_sender call). The base floor
    # is what spaces subsequent opens, not the pacing predicate.
    assert len(captured["picked"]) <= 1, (
        "at most one new dialog leaves per call; base interval is the floor (D-03)"
    )
    assert {q1, q2} >= set(captured["picked"]), (
        "any picked item must be one of the two seeded items"
    )


# ── PACE-06: catch-up does not burst; jitter present ───────────────────────────


async def test_catchup_no_burst(async_db_session, test_running_campaign_factory):
    """PACE-06 (D-04, D-08): catch-up scenario (0 opened, large expected so many
    new dialogs are eligible). Two never-contacted items, ONE worker call → at
    most one item leaves (LIMIT 8 is a candidate pool, not a multi-fire). Jitter
    must be present in the SELECT source (random.uniform with the jitter consts).
    """
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    sid = senders[0].id
    wid = camp["workspace_id"]
    cid = camp["id"]
    # Large limit, 0 opened → expected_now ≫ 0 ⇒ both items eligible by pace.
    await _set_cap(async_db_session, campaign_id=cid, cap=1000)

    q1 = await _insert_pending_item(async_db_session, workspace_id=wid, sender_id=sid,
                                    campaign_id=cid, recipient_phone="+79995550001")
    q2 = await _insert_pending_item(async_db_session, workspace_id=wid, sender_id=sid,
                                    campaign_id=cid, recipient_phone="+79995550002")

    worker = QueueWorker()
    captured, cm_rate, cm_pause, cm_send = _run_worker_capturing_picked(worker)
    with cm_rate, cm_pause, cm_send:
        await worker._process_next_for_sender(sid)

    assert len(captured["picked"]) <= 1, (
        "catch-up must NOT burst: exactly one item per call even when many are eligible"
    )
    assert set(captured["picked"]) <= {q1, q2}

    # Jitter present in source (D-08): the pacing computation applies
    # random.uniform with the jitter constants so openings don't form a grid.
    # Deferred name reference inside the body keeps collection clean before 13-02.
    from app.services.queue import PACE_JITTER_LOW, PACE_JITTER_HIGH  # noqa: F401

    src = inspect.getsource(QueueWorker._process_next_for_sender)
    assert "random.uniform" in src, (
        "pacing must jitter the expected-by-now count via random.uniform (D-08)"
    )
    assert ("PACE_JITTER_LOW" in src and "PACE_JITTER_HIGH" in src), (
        "jitter must use the PACE_JITTER_* constants (D-08)"
    )


# ── PACE-07: follow-ups bypass pacing; _check_rate_limits untouched ────────────


async def test_followup_bypasses_pacing(
    async_db_session, test_running_campaign_factory
):
    """PACE-07 (D-07, D-10): pacing would block a NEW dialog (over expected), but
    a follow-up / re-contact item (recipient_phone with a prior status='sent' in
    THIS campaign) bypasses pacing entirely and IS picked. Plus an introspection
    guard that pacing does NOT live in the per-tick _check_rate_limits gate.
    """
    _assert_pacing_predicate_wired()  # RED until 13-02 wires the predicate.

    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    sid = senders[0].id
    wid = camp["workspace_id"]
    cid = camp["id"]
    # Tiny limit + an opened dialog → expected_now ≤ 1, count_opened ≥ 1 ⇒ a NEW
    # dialog would be blocked by pace (same posture as PACE-03 Case 1).
    await _set_cap(async_db_session, campaign_id=cid, cap=1)
    await _seed_sent_dialog(async_db_session, workspace_id=wid, sender_id=sid,
                            campaign_id=cid, recipient_phone="+79996660001")

    # A prior sent to +79996660999 makes a pending item to that phone a follow-up.
    await _seed_sent_dialog(async_db_session, workspace_id=wid, sender_id=sid,
                            campaign_id=cid, recipient_phone="+79996660999")
    followup_qid = await _insert_pending_item(
        async_db_session, workspace_id=wid, sender_id=sid,
        campaign_id=cid, recipient_phone="+79996660999",
    )

    worker = QueueWorker()
    captured, cm_rate, cm_pause, cm_send = _run_worker_capturing_picked(worker)
    with cm_rate, cm_pause, cm_send:
        await worker._process_next_for_sender(sid)

    assert followup_qid in captured["picked"], (
        "follow-up / re-contact item must bypass pacing entirely (D-07/D-10)"
    )
    assert await _item_status(async_db_session, followup_qid) == "processing", (
        "selected follow-up item must transition out of pending"
    )

    # Introspection guard (D-07): pacing must NOT live in the per-tick gate, or
    # it would throttle follow-ups and AI replies too.
    rate_src = inspect.getsource(QueueWorker._check_rate_limits)
    assert "window_start" not in rate_src, (
        "_check_rate_limits must NOT reference the pacing window-start (D-07)"
    )
    assert "expected_now" not in rate_src, (
        "_check_rate_limits must NOT reference the expected-by-now count (D-07)"
    )
    assert "PACE_JITTER" not in rate_src, (
        "_check_rate_limits must NOT reference the pacing jitter constants (D-07)"
    )


# ── Phase 22 (D-05): pace numerator = account grade budget, window preserved ───


async def test_expected_now_uses_account_budget_source():
    """D-05 guard: expected_now is computed from the account grade budget, and the
    campaign working window (timezone/hours) still feeds the elapsed fraction."""
    src = inspect.getsource(QueueWorker._process_next_for_sender)
    assert "account_budget * frac" in src, (
        "expected_now numerator must be the account budget (D-05)"
    )
    assert "c.max_new_dialogs_per_day" not in src, (
        "the legacy per-campaign cap column must no longer drive pacing (D-01/D-05)"
    )
    assert "c.timezone AS c_tz" in src and "_window_elapsed_fraction" in src, (
        "the campaign working window must still supply the pacing fraction (D-05)"
    )


async def test_pacing_numerator_is_account_budget(
    async_db_session, test_running_campaign_factory
):
    """D-05: the expected-by-now pace numerator is the ACCOUNT grade budget.

    Account budget = 10 (via _set_cap). Pin frac=0.1, jitter=1.0:
      - numerator = account budget (10) → expected = 1.0; 2 opened ≥ 1.0 ⇒ BLOCKED.
    The item is blocked, proving the numerator is the account budget. The cap
    (budget 10) allows (2 < 10), so pacing is the sole binding constraint. The
    legacy per-campaign cap column was dropped in 22-06 (mig 059); the source-
    introspection test above guards that the queue no longer reads it.
    """
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    sid = senders[0].id
    wid = camp["workspace_id"]
    cid = camp["id"]

    now = datetime.now(timezone.utc)
    ws = now - timedelta(hours=2)  # window start 2h ago: seeded NOW() rows count

    await _set_cap(async_db_session, campaign_id=cid, cap=10)  # account budget = 10

    await _seed_sent_dialog(async_db_session, workspace_id=wid, sender_id=sid,
                            campaign_id=cid, recipient_phone="+79997770001")
    await _seed_sent_dialog(async_db_session, workspace_id=wid, sender_id=sid,
                            campaign_id=cid, recipient_phone="+79997770002")

    blocked_qid = await _insert_pending_item(
        async_db_session, workspace_id=wid, sender_id=sid,
        campaign_id=cid, recipient_phone="+79997770003",
    )

    worker = QueueWorker()
    captured, cm_rate, cm_pause, cm_send = _run_worker_capturing_picked(worker)
    with _pin_pacing(window_start_utc=ws, frac=0.1), cm_rate, cm_pause, cm_send:
        await worker._process_next_for_sender(sid)

    assert blocked_qid not in captured["picked"], (
        "pace numerator must be the account budget (10 → expected 1.0), not the "
        "legacy campaign column (1000 → expected 100 would allow) (D-05)"
    )
    assert await _item_status(async_db_session, blocked_qid) == "pending"
