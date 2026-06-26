"""Phase 12 Plan 12-02 — integration tests for the per-(sender,campaign)
daily new-dialog cap enforced inside ``QueueWorker._process_next_for_sender``.

NDLG-02 / D-01,D-02,D-05,D-06,D-07,D-08,D-09:
- A new dialog = a message_queue item whose recipient_phone has NO prior
  status='sent' row for the SAME campaign_id.
- Once a (sender,campaign) has opened >= campaigns.max_new_dialogs_per_day
  distinct new dialogs in the trailing 24h, new-dialog items are excluded from
  the candidate SELECT.
- Follow-up / re-contact items (a recipient_phone that already has a prior
  status='sent' in THIS campaign) stay eligible regardless of the cap.
- ``_check_rate_limits`` (empirical 4/20/150 + 15/h) is untouched — these tests
  mock it to True to isolate the new-dialog cap as the only variable, and one
  test asserts via source introspection that the constant / function are intact.
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


# ── Helpers ──────────────────────────────────────────────────────────────────


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


async def _count_in_window_sent(db, *, sender_id, campaign_id) -> int:
    """COUNT(DISTINCT recipient_phone) of status='sent' rows in this
    (sender,campaign) within the trailing 24h — mirrors the enforcement SQL."""
    return (await db.execute(text("""
        SELECT COUNT(DISTINCT recipient_phone) FROM message_queue
        WHERE sender_id = :sid
          AND campaign_id = :cid
          AND status = 'sent'
          AND finished_at >= NOW() - INTERVAL '24 hours'
    """), {"sid": str(sender_id), "cid": str(campaign_id)})).scalar()


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


# ── Tests ────────────────────────────────────────────────────────────────────


async def test_new_dialog_blocked_when_cap_reached(
    async_db_session, test_running_campaign_factory
):
    """cap=2, 2 distinct new dialogs already opened in 24h → a 3rd, never-contacted
    new-dialog item must NOT be picked (stays pending)."""
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    sid = senders[0].id
    wid = camp["workspace_id"]
    cid = camp["id"]
    await _set_cap(async_db_session, campaign_id=cid, cap=2)

    await _seed_sent_dialog(async_db_session, workspace_id=wid, sender_id=sid,
                            campaign_id=cid, recipient_phone="+79990000001")
    await _seed_sent_dialog(async_db_session, workspace_id=wid, sender_id=sid,
                            campaign_id=cid, recipient_phone="+79990000002")

    # Guard: the seeded sent rows actually populate the 24h window count.
    assert await _count_in_window_sent(async_db_session, sender_id=sid, campaign_id=cid) == 2

    qid = await _insert_pending_item(async_db_session, workspace_id=wid, sender_id=sid,
                                     campaign_id=cid, recipient_phone="+79990000003")

    worker = QueueWorker()
    captured, cm_rate, cm_pause, cm_send = _run_worker_capturing_picked(worker)
    with cm_rate, cm_pause, cm_send:
        await worker._process_next_for_sender(sid)

    assert qid not in captured["picked"], "new dialog at cap must NOT be selected"
    assert await _item_status(async_db_session, qid) == "pending", (
        "blocked new-dialog item must stay pending"
    )


async def test_followup_eligible_when_cap_reached(
    async_db_session, test_running_campaign_factory
):
    """cap=2 reached. A follow-up / re-contact item (recipient_phone with a prior
    status='sent' in THIS campaign) must still be selected (D-06/D-08)."""
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    sid = senders[0].id
    wid = camp["workspace_id"]
    cid = camp["id"]
    await _set_cap(async_db_session, campaign_id=cid, cap=2)

    # Two distinct new dialogs opened — cap reached.
    await _seed_sent_dialog(async_db_session, workspace_id=wid, sender_id=sid,
                            campaign_id=cid, recipient_phone="+79990000001")
    await _seed_sent_dialog(async_db_session, workspace_id=wid, sender_id=sid,
                            campaign_id=cid, recipient_phone="+79990000002")
    assert await _count_in_window_sent(async_db_session, sender_id=sid, campaign_id=cid) == 2

    # A prior sent to +79990000999 makes a pending item to that phone a follow-up.
    await _seed_sent_dialog(async_db_session, workspace_id=wid, sender_id=sid,
                            campaign_id=cid, recipient_phone="+79990000999")
    followup_qid = await _insert_pending_item(
        async_db_session, workspace_id=wid, sender_id=sid,
        campaign_id=cid, recipient_phone="+79990000999",
    )

    worker = QueueWorker()
    captured, cm_rate, cm_pause, cm_send = _run_worker_capturing_picked(worker)
    with cm_rate, cm_pause, cm_send:
        await worker._process_next_for_sender(sid)

    assert followup_qid in captured["picked"], (
        "follow-up / recontact item must stay eligible at the cap (D-06/D-08)"
    )
    assert await _item_status(async_db_session, followup_qid) == "processing", (
        "selected follow-up item must transition out of pending"
    )


async def test_new_dialog_allowed_under_cap(
    async_db_session, test_running_campaign_factory
):
    """cap=2, only 1 new dialog opened (under cap) → a fresh new-dialog item IS
    selectable.

    Isolates the Phase 12 cap as the only variable (per this file's docstring):
    the 1 prior dialog is seeded with ``finished_at`` 23h ago — inside the
    trailing-24h cap window (so ``_count_in_window_sent == 1``, under cap=2) but
    BEFORE today's Phase 13 window start (the full-day window starts at the most
    recent UTC midnight; 23h-ago always lands in yesterday relative to that
    midnight), so the expected-by-now pace numerator is 0 and the fresh new dialog
    is pace-eligible regardless of wall-clock fraction. This keeps the cap
    assertion intact while decoupling it from the Phase 13 pacing gate (two
    distinct counters, Phase 13 D-06)."""
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    sid = senders[0].id
    wid = camp["workspace_id"]
    cid = camp["id"]
    await _set_cap(async_db_session, campaign_id=cid, cap=2)

    # Prior dialog finished 23h ago: counts for the trailing-24h cap, but predates
    # today's (UTC-midnight) window start at any wall-clock hour, so it does NOT
    # inflate the Phase 13 pace numerator — keeps this a pure cap test.
    old_qid = str(uuid.uuid4())
    await async_db_session.execute(text("""
        INSERT INTO message_queue (
            id, workspace_id, sender_id, campaign_id,
            item_type, status, recipient_phone, message_text,
            scheduled_at, finished_at
        ) VALUES (
            :qid, :wid, :sid, :cid,
            'message', 'sent', :rp, 'older',
            NOW() - INTERVAL '23 hours', NOW() - INTERVAL '23 hours'
        )
    """), {
        "qid": old_qid, "wid": str(wid), "sid": str(sid),
        "cid": str(cid), "rp": "+79990000001",
    })
    await async_db_session.commit()
    assert await _count_in_window_sent(async_db_session, sender_id=sid, campaign_id=cid) == 1

    qid = await _insert_pending_item(async_db_session, workspace_id=wid, sender_id=sid,
                                     campaign_id=cid, recipient_phone="+79990000050")

    worker = QueueWorker()
    captured, cm_rate, cm_pause, cm_send = _run_worker_capturing_picked(worker)
    with cm_rate, cm_pause, cm_send:
        await worker._process_next_for_sender(sid)

    assert qid in captured["picked"], "new dialog under cap must be selected"
    assert await _item_status(async_db_session, qid) == "processing"


async def test_check_rate_limits_untouched():
    """D-09 regression guard: the empirical constant is intact and the per-tick
    gate does not reference the new-dialog cap column."""
    assert MAX_NEW_CONTACTS_PER_HOUR == 15
    src = inspect.getsource(QueueWorker._check_rate_limits)
    assert "max_new_dialogs_per_day" not in src, (
        "_check_rate_limits must NOT reference the new-dialog cap (D-07/D-09)"
    )
