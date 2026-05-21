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
