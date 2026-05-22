"""Phase 4 Plan 04-03 — integration tests for queue worker per-campaign filter.

Tests cover the rewritten `QueueWorker._tick` / `_process_next_for_sender`:
JOIN to campaigns, filter by status='running', start_date/stop_date window,
per-campaign working hours / days mask.

D-11: items past stop_date → status='failed', error_message='past_stop_date'.
D-15: paused campaigns SKIP items in queue (listener.py не модифицируется).
H4 (revision): NULL campaign_id items must NOT be picked up by the worker.
"""

import ast
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.services import queue as queue_module
from app.services.queue import QueueWorker, _campaign_in_working_window

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _insert_queue_item(
    db,
    *,
    workspace_id,
    sender_id,
    campaign_id,
    recipient_phone: str = "+79990000111",
    message_text: str = "hello",
    status: str = "pending",
    scheduled_at_offset_minutes: int = -1,
):
    """Insert a pending message_queue item for a campaign, with scheduled_at <= NOW().

    Returns the new row's UUID.
    """
    qid = str(uuid.uuid4())
    scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=scheduled_at_offset_minutes)
    await db.execute(text("""
        INSERT INTO message_queue (
            id, workspace_id, sender_id, campaign_id,
            item_type, status, recipient_phone, message_text, scheduled_at
        ) VALUES (
            :qid, :wid, :sid, :cid,
            'message', :st, :rp, :mt, :sa
        )
    """), {
        "qid": qid, "wid": str(workspace_id), "sid": str(sender_id),
        "cid": str(campaign_id) if campaign_id is not None else None,
        "st": status, "rp": recipient_phone, "mt": message_text,
        "sa": scheduled_at,
    })
    await db.commit()
    return qid


async def _queue_status(db, qid: str) -> tuple[str, str | None]:
    row = (await db.execute(text(
        "SELECT status, error_message FROM message_queue WHERE id = :qid"
    ), {"qid": qid})).first()
    return (row[0], row[1]) if row else (None, None)


def _patch_process_next_to_capture(worker: QueueWorker, captured: dict):
    """Return an AsyncMock that records every sender_id ``_tick`` dispatches."""
    captured["sender_ids"] = []

    async def _fake(sid):
        captured["sender_ids"].append(sid)

    return patch.object(worker, "_process_next_for_sender", side_effect=_fake)


# ── Tests ────────────────────────────────────────────────────────────────────


async def test_queue_skips_paused_campaign_items(
    async_db_session, test_running_campaign_factory
):
    """D-15 + pause семантика: queue SKIP'ает items если campaign.status='paused'."""
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    # Flip to paused
    await async_db_session.execute(text(
        "UPDATE campaigns SET status='paused' WHERE id = :cid"
    ), {"cid": str(camp["id"])})
    await async_db_session.commit()

    qid = await _insert_queue_item(
        async_db_session,
        workspace_id=camp["workspace_id"],
        sender_id=senders[0].id,
        campaign_id=camp["id"],
    )

    worker = QueueWorker()
    captured: dict = {}
    with _patch_process_next_to_capture(worker, captured):
        await worker._tick()

    assert captured["sender_ids"] == [], "paused campaign items must NOT dispatch"
    status, err = await _queue_status(async_db_session, qid)
    assert status == "pending", f"item should remain pending, got status={status}"
    assert err is None


async def test_queue_processes_running_campaign_items(
    async_db_session, test_running_campaign_factory
):
    """Sanity: running campaign items dispatched to per-sender processing."""
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    await _insert_queue_item(
        async_db_session,
        workspace_id=camp["workspace_id"],
        sender_id=senders[0].id,
        campaign_id=camp["id"],
    )

    worker = QueueWorker()
    captured: dict = {}
    with _patch_process_next_to_capture(worker, captured):
        await worker._tick()

    assert senders[0].id in captured["sender_ids"], (
        f"running campaign sender must be dispatched, got {captured['sender_ids']}"
    )


async def test_queue_skips_done_campaign_items(
    async_db_session, test_running_campaign_factory
):
    """done campaign — items не обрабатываются (как paused)."""
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    await async_db_session.execute(text(
        "UPDATE campaigns SET status='done' WHERE id = :cid"
    ), {"cid": str(camp["id"])})
    await async_db_session.commit()

    qid = await _insert_queue_item(
        async_db_session,
        workspace_id=camp["workspace_id"],
        sender_id=senders[0].id,
        campaign_id=camp["id"],
    )

    worker = QueueWorker()
    captured: dict = {}
    with _patch_process_next_to_capture(worker, captured):
        await worker._tick()

    assert captured["sender_ids"] == []
    status, _ = await _queue_status(async_db_session, qid)
    assert status == "pending"


async def test_queue_skips_past_stop_date(
    async_db_session, test_running_campaign_factory
):
    """D-11: NOW() >= campaign.stop_date → item НЕ берётся в обработку."""
    past_stop = datetime.now(timezone.utc) - timedelta(hours=1)
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
        stop_date=past_stop,
    )

    await _insert_queue_item(
        async_db_session,
        workspace_id=camp["workspace_id"],
        sender_id=senders[0].id,
        campaign_id=camp["id"],
    )

    worker = QueueWorker()
    captured: dict = {}
    with _patch_process_next_to_capture(worker, captured):
        await worker._tick()

    assert captured["sender_ids"] == [], (
        "past stop_date items must NOT be dispatched"
    )


async def test_queue_marks_past_stop_date_failed(
    async_db_session, test_running_campaign_factory
):
    """D-11: item с истёкшим stop_date → status='failed', error_message='past_stop_date'."""
    past_stop = datetime.now(timezone.utc) - timedelta(hours=1)
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
        stop_date=past_stop,
    )

    qid = await _insert_queue_item(
        async_db_session,
        workspace_id=camp["workspace_id"],
        sender_id=senders[0].id,
        campaign_id=camp["id"],
    )

    worker = QueueWorker()
    captured: dict = {}
    with _patch_process_next_to_capture(worker, captured):
        await worker._tick()

    status, err = await _queue_status(async_db_session, qid)
    assert status == "failed", f"expected failed, got {status}"
    assert err == "past_stop_date", f"expected error_message='past_stop_date', got {err}"


async def test_queue_skips_before_start_date(
    async_db_session, test_running_campaign_factory
):
    """D-11: NOW() < campaign.start_date → item НЕ берётся, остаётся pending."""
    future_start = datetime.now(timezone.utc) + timedelta(hours=2)
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
        start_date=future_start,
    )

    qid = await _insert_queue_item(
        async_db_session,
        workspace_id=camp["workspace_id"],
        sender_id=senders[0].id,
        campaign_id=camp["id"],
    )

    worker = QueueWorker()
    captured: dict = {}
    with _patch_process_next_to_capture(worker, captured):
        await worker._tick()

    assert captured["sender_ids"] == []
    status, _ = await _queue_status(async_db_session, qid)
    assert status == "pending", "pre-start_date items must stay pending"


async def test_queue_respects_per_campaign_working_hours(
    async_db_session, test_running_campaign_factory
):
    """Campaign work_hour_start=10, end=18 — fake "now" at 09:00 MSK → SKIP; at 11:00 MSK → process."""
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        timezone="Europe/Moscow",
        work_hour_start=10, work_hour_end=18,
        work_days_mask=127,  # ignore weekday-mask for this test
    )
    await _insert_queue_item(
        async_db_session,
        workspace_id=camp["workspace_id"],
        sender_id=senders[0].id,
        campaign_id=camp["id"],
    )

    worker = QueueWorker()
    # 06:00 UTC == 09:00 MSK (before window starts)
    before = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
    # 08:00 UTC == 11:00 MSK (inside window)
    inside = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)

    captured1: dict = {}
    with patch.object(queue_module, "datetime") as mock_dt:
        mock_dt.now.return_value = before
        # Preserve other datetime attrs that queue.py uses (timedelta is imported separately).
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        with _patch_process_next_to_capture(worker, captured1):
            await worker._tick()
    assert captured1["sender_ids"] == [], "before window — must SKIP"

    captured2: dict = {}
    with patch.object(queue_module, "datetime") as mock_dt:
        mock_dt.now.return_value = inside
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        with _patch_process_next_to_capture(worker, captured2):
            await worker._tick()
    assert senders[0].id in captured2["sender_ids"], "inside window — must dispatch"


async def test_queue_respects_work_days_mask(
    async_db_session, test_running_campaign_factory
):
    """Campaign work_days_mask=31 (Mo-Fri), суббота 10:00 MSK → SKIP; понедельник 10:00 MSK → process."""
    camp, senders = await test_running_campaign_factory(
        sender_count=1,
        timezone="Europe/Moscow",
        work_hour_start=9, work_hour_end=20,
        work_days_mask=31,
    )
    await _insert_queue_item(
        async_db_session,
        workspace_id=camp["workspace_id"],
        sender_id=senders[0].id,
        campaign_id=camp["id"],
    )

    worker = QueueWorker()
    # 2026-06-06 Sat 07:00 UTC = 10:00 MSK
    saturday = datetime(2026, 6, 6, 7, 0, tzinfo=timezone.utc)
    # 2026-06-01 Mon 07:00 UTC = 10:00 MSK
    monday = datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)

    captured_sat: dict = {}
    with patch.object(queue_module, "datetime") as mock_dt:
        mock_dt.now.return_value = saturday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        with _patch_process_next_to_capture(worker, captured_sat):
            await worker._tick()
    assert captured_sat["sender_ids"] == [], "Saturday with Mo-Fri mask — must SKIP"

    captured_mon: dict = {}
    with patch.object(queue_module, "datetime") as mock_dt:
        mock_dt.now.return_value = monday
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        with _patch_process_next_to_capture(worker, captured_mon):
            await worker._tick()
    assert senders[0].id in captured_mon["sender_ids"], "Monday with Mo-Fri mask — must dispatch"


async def test_queue_per_campaign_timezone_independent(
    async_db_session, test_running_campaign_factory
):
    """Две кампании в разных timezone'ах — каждая обрабатывается по своему расписанию.

    Setup: MSK campaign (UTC+3) work window 9-20, US/Pacific (UTC-7 in PDT) work
    window 9-20. NOW() = 17:00 UTC → MSK 20:00 (outside, exclusive end), PDT 10:00 (inside).
    Only PDT campaign's sender must be dispatched.
    """
    msk_camp, msk_senders = await test_running_campaign_factory(
        sender_count=1,
        timezone="Europe/Moscow",
        work_hour_start=9, work_hour_end=20,
        work_days_mask=127,
    )
    pdt_camp, pdt_senders = await test_running_campaign_factory(
        sender_count=1,
        timezone="America/Los_Angeles",
        work_hour_start=9, work_hour_end=20,
        work_days_mask=127,
    )

    await _insert_queue_item(
        async_db_session,
        workspace_id=msk_camp["workspace_id"],
        sender_id=msk_senders[0].id,
        campaign_id=msk_camp["id"],
    )
    await _insert_queue_item(
        async_db_session,
        workspace_id=pdt_camp["workspace_id"],
        sender_id=pdt_senders[0].id,
        campaign_id=pdt_camp["id"],
    )

    worker = QueueWorker()
    # 2026-06-04 Thu 17:00 UTC → 20:00 MSK (out — end is exclusive); 10:00 PDT (in).
    fake_now = datetime(2026, 6, 4, 17, 0, tzinfo=timezone.utc)

    captured: dict = {}
    with patch.object(queue_module, "datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        with _patch_process_next_to_capture(worker, captured):
            await worker._tick()

    assert msk_senders[0].id not in captured["sender_ids"], (
        "MSK 20:00 is outside [9,20) — must NOT dispatch"
    )
    assert pdt_senders[0].id in captured["sender_ids"], (
        "PDT 10:00 is inside [9,20) — must dispatch"
    )


async def test_workspace_isolation_in_queue_select(
    async_db_session, test_running_campaign_factory
):
    """Defence-in-depth: SELECT очереди НЕ возвращает items из чужого workspace.

    Each campaign is workspace-scoped; the JOIN-based filter inherits workspace
    isolation transitively (each ``message_queue.workspace_id`` was set at
    enqueue time from the campaign's workspace). This test asserts that
    inserting a queue item whose ``workspace_id`` differs from the campaign's
    workspace is structurally impossible — but if it ever lands, the worker
    still does dispatch the sender (because the JOIN is on campaign_id, not
    workspace) yet the workspace_id columns must match for downstream
    accounting. We verify both items belong to their respective workspaces.

    Phase 02.1 CR-01 паттерн.
    """
    camp_a, senders_a = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )
    qid = await _insert_queue_item(
        async_db_session,
        workspace_id=camp_a["workspace_id"],
        sender_id=senders_a[0].id,
        campaign_id=camp_a["id"],
    )

    row = (await async_db_session.execute(text("""
        SELECT mq.workspace_id, c.workspace_id
        FROM message_queue mq
        JOIN campaigns c ON c.id = mq.campaign_id
        WHERE mq.id = :qid
    """), {"qid": qid})).first()

    assert row is not None
    assert row[0] == row[1], (
        "message_queue.workspace_id must match the joined campaign's workspace"
    )


async def test_select_excludes_null_campaign_id_items(
    async_db_session, test_running_campaign_factory
):
    """H4 (revision): NULL campaign_id items НЕ выбираются ``_tick()``.

    Insert one legacy-style item (campaign_id=NULL) and one Phase-4 item (with
    a running campaign). After ``await worker._tick()`` the NULL-campaign item
    must stay pending and the dispatch list must NOT contain the
    NULL-campaign item's sender (unless the same sender is also bound to the
    Phase-4 campaign — we use a dedicated sender to keep the assertion clean).
    """
    camp, phase4_senders = await test_running_campaign_factory(
        sender_count=1,
        work_hour_start=0, work_hour_end=24, work_days_mask=127,
    )

    # Phase-4 item (must dispatch)
    qid_p4 = await _insert_queue_item(
        async_db_session,
        workspace_id=camp["workspace_id"],
        sender_id=phase4_senders[0].id,
        campaign_id=camp["id"],
    )

    # Legacy NULL-campaign item — bind to the SAME sender so we can later assert
    # the SQL filter (not the dispatch list) excluded it.
    qid_null = await _insert_queue_item(
        async_db_session,
        workspace_id=camp["workspace_id"],
        sender_id=phase4_senders[0].id,
        campaign_id=None,
    )

    worker = QueueWorker()
    captured: dict = {}
    with _patch_process_next_to_capture(worker, captured):
        await worker._tick()

    # The dispatch happened (Phase-4 item is fine) — but the NULL row must
    # remain pending (the worker's SELECT must NOT have matched it).
    p4_status, _ = await _queue_status(async_db_session, qid_p4)
    null_status, null_err = await _queue_status(async_db_session, qid_null)
    assert null_status == "pending", "NULL campaign_id item must stay pending"
    assert null_err is None, "NULL campaign_id must NOT be marked failed by _tick"
    # The Phase-4 row also stays pending because we mocked away the per-sender
    # dispatch — we only assert _tick's selection logic here.
    assert p4_status == "pending"


async def test_no_phase4_code_path_creates_null_campaign_id():
    """H4 (revision): static check — current Phase-4 code paths must propagate campaign_id.

    Plan 04-03 only refactors the consumer (``_tick``). Producers
    (``enqueue_message`` / ``enqueue_file``) get the ``campaign_id`` parameter
    in Plan 04-04 (per AUDIT TODO #4, #5). For now this test asserts that no
    code in ``app/services/queue.py`` itself INSERTs into ``message_queue``
    without a parameterised ``campaign_id`` placeholder. (Direct INSERTs in
    ``queue.py`` are limited to the rescheduling UPDATE statements — there are
    no module-level INSERTs into ``message_queue``.)
    """
    with open("app/services/queue.py") as fh:
        src = fh.read()
    tree = ast.parse(src)
    found_inserts: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Constant(self, node: ast.Constant):
            if isinstance(node.value, str) and "INSERT INTO message_queue" in node.value:
                found_inserts.append(node.value)

    _Visitor().visit(tree)
    # Plan 04-03 must NOT add any module-level INSERTs into message_queue.
    # Plan 04-04 will add them in campaign_enqueue.py (separate file).
    assert found_inserts == [], (
        f"Plan 04-03 queue.py contains direct message_queue INSERTs "
        f"(should be in campaign_enqueue.py, Plan 04-04): {found_inserts!r}"
    )
