"""Unit-тесты для ContactCheckWorker._tick (Phase 2 plan 02-05, CONT-04).

Покрытие:
- start()/stop() lifecycle (идемпотентно)
- _tick: нет pending → ранний return, нет вызовов check_phones
- _tick: pending без checker в workspace → skip (JOIN LATERAL без match)
- _tick: pending + checker → batch resolve → tg_status='registered' + tg_telegram_id
- _tick: result is_registered=False → tg_status='not_registered'
- _tick: result error → tg_status='error' + tg_error
- _tick: FloodWait partial — только matched phones обновляются, остальные остаются pending
- _tick: workspace isolation — checker workspace A НЕ резолвит контакты workspace B
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models import Contact, Folder, Workspace
from app.services.contact_check_worker import ContactCheckWorker

pytestmark = pytest.mark.asyncio


# ─── Lifecycle ────────────────────────────────────────────────────────────────


async def test_start_creates_task_and_stop_cancels_gracefully():
    """start() создаёт task; stop() отменяет gracefully; повторный stop — safe."""
    worker = ContactCheckWorker()
    worker.start()
    assert worker._task is not None
    assert not worker._task.done()

    await worker.stop()
    assert worker._task.done()
    # stop повторно — no-op, не падает
    await worker.stop()


async def test_start_idempotent():
    """Повторный start() пока task жив — no-op (тот же task)."""
    worker = ContactCheckWorker()
    worker.start()
    first_task = worker._task
    worker.start()
    assert worker._task is first_task
    await worker.stop()


# ─── _tick: empty / no-checker paths ─────────────────────────────────────────


async def test_tick_no_pending_returns_early(test_workspace, test_checker):
    """Пустая БД pending → tick без вызовов check_phones."""
    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(),
    ) as mock:
        processed = await worker._tick()
        mock.assert_not_awaited()
    assert processed == 0


async def test_tick_skips_when_no_checker_in_workspace(
    test_workspace, test_contacts_factory
):
    """Контакты pending, но в workspace нет checker'а → JOIN LATERAL пуст → skip.

    test_checker НЕ создаётся → нет sender'а с role='checker' AND auth_status='ok'.
    """
    await test_contacts_factory(count=2, tg_status="pending")
    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(),
    ) as mock:
        processed = await worker._tick()
        mock.assert_not_awaited()
    assert processed == 0


# ─── _tick: happy paths ──────────────────────────────────────────────────────


async def test_tick_resolves_pending_to_registered(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """Pending + checker → tick → tg_status='registered' + tg_telegram_id."""
    contacts = await test_contacts_factory(count=2, tg_status="pending")
    fake_summary = {
        "checked": 2,
        "registered": 2,
        "not_registered": 0,
        "flood_wait_hit": False,
        "results": [
            {
                "phone": contacts[0].phone,
                "is_registered": True,
                "telegram_id": 111,
            },
            {
                "phone": contacts[1].phone,
                "is_registered": True,
                "telegram_id": 222,
            },
        ],
    }
    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=fake_summary),
    ) as mock:
        processed = await worker._tick()

    assert processed == 2
    mock.assert_awaited_once()
    # Проверяем через свежий SELECT (другая сессия — _apply_results commit'ил).
    result = await async_db_session.execute(
        text(
            "SELECT id, tg_status, tg_telegram_id, tg_checked_at "
            "FROM contacts WHERE id = ANY(:ids) ORDER BY phone"
        ),
        {"ids": [str(c.id) for c in contacts]},
    )
    rows = result.fetchall()
    assert len(rows) == 2
    assert all(r.tg_status == "registered" for r in rows)
    assert {r.tg_telegram_id for r in rows} == {111, 222}
    assert all(r.tg_checked_at is not None for r in rows)


async def test_tick_marks_not_registered(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """is_registered=False → tg_status='not_registered'."""
    contact = await test_contacts_factory(count=1, tg_status="pending")
    fake_summary = {
        "checked": 1,
        "registered": 0,
        "not_registered": 1,
        "flood_wait_hit": False,
        "results": [{"phone": contact.phone, "is_registered": False}],
    }
    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=fake_summary),
    ):
        await worker._tick()

    result = await async_db_session.execute(
        text(
            "SELECT tg_status, tg_telegram_id, tg_checked_at FROM contacts WHERE id = :cid"
        ),
        {"cid": str(contact.id)},
    )
    row = result.fetchone()
    assert row.tg_status == "not_registered"
    assert row.tg_telegram_id is None
    assert row.tg_checked_at is not None


async def test_tick_marks_error_when_result_has_error(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """result.error → tg_status='error' + tg_error."""
    contact = await test_contacts_factory(count=1, tg_status="pending")
    fake_summary = {
        "checked": 1,
        "registered": 0,
        "not_registered": 0,
        "flood_wait_hit": False,
        "results": [
            {
                "phone": contact.phone,
                "is_registered": False,
                "error": "PHONE_NOT_OCCUPIED",
            }
        ],
    }
    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=fake_summary),
    ):
        await worker._tick()

    result = await async_db_session.execute(
        text("SELECT tg_status, tg_error FROM contacts WHERE id = :cid"),
        {"cid": str(contact.id)},
    )
    row = result.fetchone()
    assert row.tg_status == "error"
    assert "PHONE_NOT_OCCUPIED" in (row.tg_error or "")


# ─── _tick: FloodWait partial ────────────────────────────────────────────────


async def test_tick_floodwait_partial_keeps_unprocessed_pending(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """FloodWait: только matched phones обновляются; остальные остаются pending."""
    contacts = await test_contacts_factory(count=3, tg_status="pending")
    # Только первый contact обработан, остальные — partial result от CheckerService.
    fake_summary = {
        "checked": 1,
        "registered": 1,
        "not_registered": 0,
        "flood_wait_hit": True,
        "results": [
            {
                "phone": contacts[0].phone,
                "is_registered": True,
                "telegram_id": 555,
            }
        ],
    }
    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=fake_summary),
    ):
        await worker._tick()

    result = await async_db_session.execute(
        text("SELECT id, tg_status FROM contacts WHERE id = ANY(:ids)"),
        {"ids": [str(c.id) for c in contacts]},
    )
    statuses = {r.id: r.tg_status for r in result.fetchall()}
    assert statuses[contacts[0].id] == "registered"
    assert statuses[contacts[1].id] == "pending"
    assert statuses[contacts[2].id] == "pending"


# ─── _tick: workspace isolation ──────────────────────────────────────────────


async def test_tick_workspace_isolation_does_not_cross_tenants(
    async_db_session, test_workspace, test_checker
):
    """Checker в workspace A; pending контакт в workspace B → НЕ обрабатывается.

    JOIN LATERAL фильтрует по ``s.workspace_id = c.workspace_id``.
    """
    # test_workspace + test_checker — это workspace A с активным checker'ом.
    # Создаём workspace B и контакт там БЕЗ своего checker'а.
    ws_b = Workspace(name="Other Workspace")
    async_db_session.add(ws_b)
    await async_db_session.commit()
    await async_db_session.refresh(ws_b)

    folder_b = Folder(workspace_id=ws_b.id, name="Folder B")
    async_db_session.add(folder_b)
    await async_db_session.commit()
    await async_db_session.refresh(folder_b)

    contact_b = Contact(
        workspace_id=ws_b.id,
        folder_id=folder_b.id,
        phone="+79991110001",
        full_name="Cross-tenant contact",
        tg_status="pending",
    )
    async_db_session.add(contact_b)
    await async_db_session.commit()
    await async_db_session.refresh(contact_b)

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(),
    ) as mock:
        await worker._tick()
        mock.assert_not_awaited()

    # Контакт workspace B остался pending — workspace A checker не виден ему.
    result = await async_db_session.execute(
        text("SELECT tg_status FROM contacts WHERE id = :cid"),
        {"cid": str(contact_b.id)},
    )
    assert result.scalar() == "pending"


# ─── _tick: batched call ─────────────────────────────────────────────────────


async def test_tick_batches_phones_into_single_check_phones_call(
    test_workspace, test_checker, test_contacts_factory
):
    """3 pending контакта одного workspace + один checker → один батч-вызов."""
    contacts = await test_contacts_factory(count=3, tg_status="pending")
    fake_summary = {
        "checked": 3,
        "registered": 3,
        "not_registered": 0,
        "flood_wait_hit": False,
        "results": [
            {"phone": c.phone, "is_registered": True, "telegram_id": 100 + i}
            for i, c in enumerate(contacts)
        ],
    }
    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=fake_summary),
    ) as mock:
        await worker._tick()
        # Один-единственный вызов с phones=[3 номера]
        assert mock.await_count == 1
        call_kwargs = mock.await_args.kwargs
        assert sorted(call_kwargs["phones"]) == sorted([c.phone for c in contacts])
        assert call_kwargs["checker_slug"] == test_checker.slug


# ─── _tick: only checker with auth_status='ok' is used ───────────────────────


async def test_tick_skips_when_checker_auth_status_not_ok(
    test_workspace, test_sender_factory, test_contacts_factory
):
    """Checker с auth_status='session_expired' → JOIN LATERAL без match → skip."""
    await test_sender_factory(
        role="checker", auth_status="session_expired", slug="broken-checker"
    )
    await test_contacts_factory(count=2, tg_status="pending")

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(),
    ) as mock:
        await worker._tick()
        mock.assert_not_awaited()


# ─── Phase 14 Wave-0 RED scaffold ────────────────────────────────────────────
# RESV-05/D-11 selection-skip, RESV-04/D-08 mobile-first, RESV-06/D-09 confidence.
# Intentionally RED until Wave 2-3 add the JOIN LATERAL restriction/lifecycle
# gate, mobile-first ORDER BY, and confidence/source writes.


async def test_selection_skips_restricted(
    async_db_session, test_workspace, test_sender_factory, test_contacts_factory
):
    """RESV-05/D-11: a checker with restriction_status='spam_limited' is NOT picked
    by _tick — its pending contacts stay pending.

    This is the root-cause fix (the hole that let the broken checker keep lying):
    the JOIN LATERAL currently filters only auth_status='ok', so a semantically
    correct spam_limited flag does NOT stop the worker. Wave 2 adds
    `AND restriction_status='none'` to the selection.
    """
    await test_sender_factory(
        role="checker",
        auth_status="ok",
        restriction_status="spam_limited",
        slug="spam-limited-checker",
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


async def test_selection_skips_paused(
    async_db_session, test_workspace, test_sender_factory, test_contacts_factory
):
    """RESV-05/D-11: a checker with lifecycle_status='paused' is NOT picked by _tick."""
    await test_sender_factory(
        role="checker",
        auth_status="ok",
        lifecycle_status="paused",
        slug="paused-checker",
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


async def test_mobile_first_order(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """RESV-04/D-08: given mixed +79… (mobile) and +73… (landline) pending, the
    claim SELECT returns mobiles first.

    Mobiles (+79…) are ~50% live and should drain before landlines. We seed a
    landline FIRST (older created_at) and a mobile SECOND; with mobile-first
    ordering the worker must still hand the +79 number to check_phones before
    the +73 one even though it was created later. Asserted via the order of
    phones passed to check_phones with batch_size=1.
    """
    # Landline created first (older), mobile created second (newer).
    await test_contacts_factory(count=1, tg_status="pending", phone="+73491234567")
    await test_contacts_factory(count=1, tg_status="pending", phone="+79991234567")

    def _fake(phones, **kwargs):
        return {
            "checked": len(phones),
            "registered": len(phones),
            "not_registered": 0,
            "flood_wait_hit": False,
            "results": [
                {"phone": p, "is_registered": True, "telegram_id": 1} for p in phones
            ],
        }

    worker = ContactCheckWorker()
    worker.batch_size = 1  # one phone per tick → first claimed phone is observable
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(side_effect=lambda **kw: _fake(**kw)),
    ) as mock:
        await worker._tick()

    assert mock.await_count == 1
    first_phones = mock.await_args.kwargs["phones"]
    assert first_phones == ["+79991234567"], (
        "mobile (+79…) must be claimed before landline (+73…) regardless of created_at"
    )


async def test_confidence_written(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """RESV-06/D-09: a clean-probe checker writes tg_confidence='high',
    tg_resolved_by=<checker_id>, tg_probe_state='clean' on resolution.

    Both registered and not_registered results from a clean (non-degraded) checker
    carry high-confidence provenance so downstream code can trust the result.
    """
    contacts = await test_contacts_factory(count=2, tg_status="pending")
    reg, notreg = contacts[0], contacts[1]
    fake_summary = {
        "checked": 2,
        "registered": 1,
        "not_registered": 1,
        "flood_wait_hit": False,
        "results": [
            {"phone": reg.phone, "is_registered": True, "telegram_id": 555},
            {"phone": notreg.phone, "is_registered": False},
        ],
    }

    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=fake_summary),
    ):
        await worker._tick()

    rows = (
        await async_db_session.execute(
            text(
                "SELECT tg_status, tg_confidence, tg_resolved_by, tg_probe_state "
                "FROM contacts WHERE id = ANY(:ids)"
            ),
            {"ids": [str(reg.id), str(notreg.id)]},
        )
    ).fetchall()
    assert len(rows) == 2
    for r in rows:
        assert r.tg_confidence == "high", "clean-probe checker → high confidence"
        assert str(r.tg_resolved_by) == str(test_checker.id), "resolver-provenance (D-09)"
        assert r.tg_probe_state == "clean"
