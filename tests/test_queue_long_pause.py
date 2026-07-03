"""Quick 260703-ssv Task 4 (WR-04) — integration tests for the durable,
non-blocking long-pause mechanism in ``QueueWorker``.

The old code did ``await asyncio.sleep(long_pause)`` (3-10 min) inline in the
shared queue tick — head-of-line blocking that stalled EVERY sender in EVERY
workspace, and re-fired repeatedly on the static 30-min sent count. The fix
persists ``senders.long_pause_until`` and returns; ``_tick`` excludes the sender
until the marker expires (re-read from the DB every tick, no in-memory state).

Behaviours covered:
- Non-blocking: ``_process_next_for_sender`` sets ``long_pause_until`` in the
  future and returns WITHOUT calling ``asyncio.sleep`` for the long duration and
  WITHOUT sending; a second (unpaused) sender's items stay eligible.
- Restart-durable: a fresh ``_tick`` candidate SELECT (new session — simulates a
  process restart, no in-memory state) excludes a paused sender while
  ``long_pause_until > NOW()`` and includes it again once it has expired.
- No double-trigger: ``_get_long_pause_seconds`` returns None while a pause is
  active even when the modulo condition would otherwise fire.

Durations are randomised and MUST stay untouched — tests assert only against the
constant bounds, never exact values.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.services import queue as queue_module
from app.services.queue import (
    QueueWorker,
    LONG_PAUSE_EVERY_MIN,
    LONG_PAUSE_EVERY_MAX,
    LONG_PAUSE_MIN_SECS,
    LONG_PAUSE_MAX_SECS,
)

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _insert_pending(db, *, workspace_id, sender_id, campaign_id, recipient_phone):
    """Seed a pending message_queue item scheduled in the past (due now)."""
    qid = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO message_queue (
            id, workspace_id, sender_id, campaign_id,
            item_type, status, recipient_phone, message_text, scheduled_at
        ) VALUES (
            :qid, :wid, :sid, :cid,
            'message', 'pending', :rp, 'hello', NOW() - INTERVAL '1 minute'
        )
    """), {
        "qid": qid, "wid": str(workspace_id), "sid": str(sender_id),
        "cid": str(campaign_id), "rp": recipient_phone,
    })
    await db.commit()
    return qid


async def _seed_sent(db, *, workspace_id, sender_id, recipient_phone):
    """Seed a status='sent' row finished NOW() (inside the 30-min activity window).

    The conftest factory does not name finished_at, so a raw insert is required
    for the long-pause modulo count to see the row."""
    qid = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO message_queue (
            id, workspace_id, sender_id, campaign_id,
            item_type, status, recipient_phone, message_text,
            scheduled_at, finished_at
        ) VALUES (
            :qid, :wid, :sid, NULL,
            'message', 'sent', :rp, 'prior', NOW(), NOW()
        )
    """), {
        "qid": qid, "wid": str(workspace_id), "sid": str(sender_id), "rp": recipient_phone,
    })
    await db.commit()
    return qid


async def _set_long_pause(db, sender_id, interval_sql: str):
    """Set senders.long_pause_until = NOW() + <interval_sql> (or NULL)."""
    if interval_sql is None:
        await db.execute(text("UPDATE senders SET long_pause_until = NULL WHERE id = :sid"),
                         {"sid": str(sender_id)})
    else:
        await db.execute(
            text(f"UPDATE senders SET long_pause_until = NOW() + INTERVAL '{interval_sql}' WHERE id = :sid"),
            {"sid": str(sender_id)},
        )
    await db.commit()


async def _get_long_pause_until(db, sender_id):
    return (await db.execute(
        text("SELECT long_pause_until FROM senders WHERE id = :sid"),
        {"sid": str(sender_id)},
    )).scalar()


# ── Test 1: non-blocking ───────────────────────────────────────────────────────


async def test_long_pause_sets_durable_marker_and_does_not_block(
    async_db_session, test_sender_factory, monkeypatch
):
    """When a long pause is due, _process_next_for_sender persists
    senders.long_pause_until in the future and returns WITHOUT a long
    asyncio.sleep and WITHOUT sending."""
    sender = await test_sender_factory()
    sid = sender.id

    sleeps: list = []

    async def _rec_sleep(secs, *a, **k):
        sleeps.append(secs)

    monkeypatch.setattr(queue_module.asyncio, "sleep", _rec_sleep)

    worker = QueueWorker()
    # Force the "pause is due" branch deterministically (an in-bounds duration);
    # this isolates _process_next_for_sender's mechanism from the modulo logic.
    a_duration = LONG_PAUSE_MIN_SECS + 60
    with patch.object(worker, "_check_rate_limits", new=AsyncMock(return_value=True)), \
         patch.object(worker, "_get_long_pause_seconds", new=AsyncMock(return_value=a_duration)), \
         patch.object(worker, "_send_item", new=AsyncMock()) as send_mock:
        await worker._process_next_for_sender(sid)

    # Durable marker set in the future (read back on a separate session).
    lpu = await _get_long_pause_until(async_db_session, sid)
    assert lpu is not None, "long_pause_until must be persisted"
    assert lpu > datetime.now(timezone.utc), "long_pause_until must be in the future"

    # No blocking long sleep leaked into the shared tick.
    assert all(s < LONG_PAUSE_MIN_SECS for s in sleeps), (
        f"a long blocking asyncio.sleep leaked (>= {LONG_PAUSE_MIN_SECS}s): {sleeps}"
    )

    # Sender skipped this tick — no send happened while pausing.
    send_mock.assert_not_called()


async def test_unpaused_sender_stays_eligible_beside_a_paused_one(
    async_db_session, test_running_campaign_factory, monkeypatch
):
    """A paused sender does NOT block a second, unpaused sender in the same tick:
    _tick picks the unpaused sender and skips the paused one."""
    # No-op sleep so the inter-sender 0.5s pacing pause doesn't slow the test.
    async def _noop_sleep(secs, *a, **k):
        return None
    monkeypatch.setattr(queue_module.asyncio, "sleep", _noop_sleep)

    camp, senders = await test_running_campaign_factory(
        sender_count=2, work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    wid, cid = camp["workspace_id"], camp["id"]
    a, b = senders[0].id, senders[1].id
    await _insert_pending(async_db_session, workspace_id=wid, sender_id=a,
                          campaign_id=cid, recipient_phone="+79990000101")
    await _insert_pending(async_db_session, workspace_id=wid, sender_id=b,
                          campaign_id=cid, recipient_phone="+79990000102")
    await _set_long_pause(async_db_session, a, "5 minutes")

    worker = QueueWorker()
    processed: list = []

    async def _capture(sender_id):
        processed.append(str(sender_id))

    with patch.object(worker, "_process_next_for_sender", new=_capture):
        await worker._tick()

    assert str(b) in processed, "the unpaused sender must still be processed"
    assert str(a) not in processed, "the paused sender must be skipped this tick"


# ── Test 2: restart-durable ─────────────────────────────────────────────────────


async def test_pause_is_restart_durable_read_from_db_not_memory(
    async_db_session, test_running_campaign_factory, monkeypatch
):
    """A brand-new worker (no in-memory state — simulates a process restart)
    excludes a paused sender while long_pause_until > NOW(), and includes it
    again once long_pause_until <= NOW(). Proves the pause lives in the DB."""
    async def _noop_sleep(secs, *a, **k):
        return None
    monkeypatch.setattr(queue_module.asyncio, "sleep", _noop_sleep)

    camp, senders = await test_running_campaign_factory(
        sender_count=1, work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    wid, cid = camp["workspace_id"], camp["id"]
    a = senders[0].id
    await _insert_pending(async_db_session, workspace_id=wid, sender_id=a,
                          campaign_id=cid, recipient_phone="+79990000201")

    # Pause A in the future, then a FRESH worker (restart) must exclude it.
    await _set_long_pause(async_db_session, a, "5 minutes")
    worker1 = QueueWorker()
    processed1: list = []

    async def _cap1(sender_id):
        processed1.append(str(sender_id))

    with patch.object(worker1, "_process_next_for_sender", new=_cap1):
        await worker1._tick()
    assert str(a) not in processed1, "paused sender excluded after simulated restart"

    # Move the pause into the past → a fresh worker includes A again.
    await async_db_session.execute(
        text("UPDATE senders SET long_pause_until = NOW() - INTERVAL '1 minute' WHERE id = :sid"),
        {"sid": str(a)},
    )
    await async_db_session.commit()

    worker2 = QueueWorker()
    processed2: list = []

    async def _cap2(sender_id):
        processed2.append(str(sender_id))

    with patch.object(worker2, "_process_next_for_sender", new=_cap2):
        await worker2._tick()
    assert str(a) in processed2, "sender eligible again once long_pause_until expired"


# ── Test 3: no double-trigger ────────────────────────────────────────────────────


async def test_get_long_pause_does_not_retrigger_while_paused(
    async_db_session, test_sender_factory, monkeypatch
):
    """While long_pause_until is in the future, _get_long_pause_seconds returns
    None even though the modulo condition would otherwise fire — the guard
    prevents extending/re-firing an active pause. A control run with the marker
    cleared proves the modulo WOULD have fired."""
    sender = await test_sender_factory()
    sid = sender.id
    wid = sender.workspace_id

    # Deterministic modulo: pause_every=12, and seed exactly 12 sent rows.
    def _fake_randint(a, b):
        if (a, b) == (LONG_PAUSE_EVERY_MIN, LONG_PAUSE_EVERY_MAX):
            return 12
        return LONG_PAUSE_MIN_SECS  # duration branch
    monkeypatch.setattr(queue_module.random, "randint", _fake_randint)

    for i in range(12):
        await _seed_sent(async_db_session, workspace_id=wid, sender_id=sid,
                         recipient_phone=f"+7999030{i:04d}")

    worker = QueueWorker()

    # Active pause → guard returns None (no re-trigger) despite 12 % 12 == 0.
    await _set_long_pause(async_db_session, sid, "5 minutes")
    assert await worker._get_long_pause_seconds(sid) is None, (
        "must NOT re-trigger a long pause while one is already active"
    )

    # Control: clear the marker → modulo fires → a bounded duration is returned.
    await _set_long_pause(async_db_session, sid, None)
    got = await worker._get_long_pause_seconds(sid)
    assert got is not None, "with no active pause and 12 % 12 == 0, a pause must fire"
    assert LONG_PAUSE_MIN_SECS <= got <= LONG_PAUSE_MAX_SECS, (
        "pause duration must stay within the untouched empirical bounds"
    )
