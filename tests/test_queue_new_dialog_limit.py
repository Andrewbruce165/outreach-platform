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


# ── Phase 22 (D-01/D-06/D-13): sender-wide, account-budget-driven cap ──────────


async def test_followup_is_sender_wide_across_campaigns(
    async_db_session, test_running_campaign_factory,
    test_campaign_factory, attach_sender_to_campaign,
):
    """D-13: a phone the sender already sent to in campaign A is a KNOWN peer when
    the same phone is queued in campaign B — serviced as a follow-up, spending no
    new-dialog budget. Even with the account budget set to 0 (all NEW dialogs
    blocked), the cross-campaign follow-up is still picked."""
    camp_a, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    sid = senders[0].id
    wid = camp_a["workspace_id"]
    cid_a = camp_a["id"]

    # Second running campaign in the SAME workspace, same sender attached.
    camp_b = await test_campaign_factory(
        status="running", work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    cid_b = camp_b["id"]
    await attach_sender_to_campaign(cid_b, sid)

    # Budget 0 → every NEW dialog is blocked; only sender-wide follow-ups survive.
    await _set_cap(async_db_session, campaign_id=cid_a, cap=0)

    # Sent to phone P in campaign A → P is now a known peer for this sender.
    phone_p = "+79990000777"
    await _seed_sent_dialog(async_db_session, workspace_id=wid, sender_id=sid,
                            campaign_id=cid_a, recipient_phone=phone_p)

    # Same phone queued in campaign B — sender-wide dedup makes it a follow-up.
    followup_qid = await _insert_pending_item(
        async_db_session, workspace_id=wid, sender_id=sid,
        campaign_id=cid_b, recipient_phone=phone_p,
    )

    worker = QueueWorker()
    captured, cm_rate, cm_pause, cm_send = _run_worker_capturing_picked(worker)
    with cm_rate, cm_pause, cm_send:
        await worker._process_next_for_sender(sid)

    assert followup_qid in captured["picked"], (
        "a phone contacted in ANOTHER campaign must be a sender-wide follow-up "
        "(D-13) — picked even at budget 0"
    )
    assert await _item_status(async_db_session, followup_qid) == "processing"


async def test_account_budget_shared_across_campaigns_blocks(
    async_db_session, test_running_campaign_factory,
    test_campaign_factory, attach_sender_to_campaign,
):
    """D-01/D-06: the daily new-dialog budget is SENDER-WIDE, spent across all of a
    sender's campaigns combined. Budget 5, 3 new dialogs opened in campaign A + 2
    in campaign B = 5 exhausted → a 6th new dialog (in campaign A) is blocked, even
    though campaign A alone only opened 3 (< 5). Per-campaign logic would allow it;
    sender-wide logic blocks it.

    The 5 prior dialogs are seeded finished 23h ago: inside the trailing-24h cap
    window (so the cap counts 5) but before today's UTC-midnight pace window start
    (so the pace numerator is 0 and pacing ALLOWS) — isolating the sender-wide cap
    as the sole blocker (same technique as test_new_dialog_allowed_under_cap)."""
    camp_a, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    sid = senders[0].id
    wid = camp_a["workspace_id"]
    cid_a = camp_a["id"]

    camp_b = await test_campaign_factory(
        status="running", work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    cid_b = camp_b["id"]
    await attach_sender_to_campaign(cid_b, sid)

    await _set_cap(async_db_session, campaign_id=cid_a, cap=5)

    async def _seed_old_sent(campaign_id, phone):
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
            "qid": str(uuid.uuid4()), "wid": str(wid), "sid": str(sid),
            "cid": str(campaign_id), "rp": phone,
        })
        await async_db_session.commit()

    # 3 distinct new dialogs in A + 2 in B = 5 sender-wide (== budget).
    for i in range(3):
        await _seed_old_sent(cid_a, f"+799911100{i:02d}")
    for i in range(2):
        await _seed_old_sent(cid_b, f"+799922200{i:02d}")

    # A fresh never-contacted new dialog in campaign A. Campaign A alone opened
    # only 3 (< 5); a per-campaign cap would ALLOW this. Sender-wide (3+2=5) blocks.
    blocked_qid = await _insert_pending_item(
        async_db_session, workspace_id=wid, sender_id=sid,
        campaign_id=cid_a, recipient_phone="+79993330099",
    )

    worker = QueueWorker()
    captured, cm_rate, cm_pause, cm_send = _run_worker_capturing_picked(worker)
    with cm_rate, cm_pause, cm_send:
        await worker._process_next_for_sender(sid)

    assert blocked_qid not in captured["picked"], (
        "sender-wide budget (3 in A + 2 in B = 5) must block a 6th new dialog "
        "even though campaign A alone is under budget (D-01/D-06)"
    )
    assert await _item_status(async_db_session, blocked_qid) == "pending"


async def test_account_budget_shared_across_campaigns_allows_under_total(
    async_db_session, test_running_campaign_factory,
    test_campaign_factory, attach_sender_to_campaign,
):
    """D-01/D-06 complement: with budget 5 and only 2 (A) + 2 (B) = 4 opened
    sender-wide (< 5), a fresh new dialog in campaign B IS selectable — proving the
    shared budget boundary, not a per-campaign one."""
    camp_a, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    sid = senders[0].id
    wid = camp_a["workspace_id"]
    cid_a = camp_a["id"]

    camp_b = await test_campaign_factory(
        status="running", work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    cid_b = camp_b["id"]
    await attach_sender_to_campaign(cid_b, sid)

    await _set_cap(async_db_session, campaign_id=cid_a, cap=5)

    async def _seed_old_sent(campaign_id, phone):
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
            "qid": str(uuid.uuid4()), "wid": str(wid), "sid": str(sid),
            "cid": str(campaign_id), "rp": phone,
        })
        await async_db_session.commit()

    for i in range(2):
        await _seed_old_sent(cid_a, f"+799944400{i:02d}")
    for i in range(2):
        await _seed_old_sent(cid_b, f"+799955500{i:02d}")

    allowed_qid = await _insert_pending_item(
        async_db_session, workspace_id=wid, sender_id=sid,
        campaign_id=cid_b, recipient_phone="+79996660099",
    )

    worker = QueueWorker()
    captured, cm_rate, cm_pause, cm_send = _run_worker_capturing_picked(worker)
    with cm_rate, cm_pause, cm_send:
        await worker._process_next_for_sender(sid)

    assert allowed_qid in captured["picked"], (
        "4 opened sender-wide (< budget 5) must leave a new dialog selectable (D-01)"
    )
    assert await _item_status(async_db_session, allowed_qid) == "processing"
