"""Phase 02.1 (CR-08): ContactCheckWorker._tick — FOR UPDATE SKIP LOCKED + tg_checked_at claim.

При горизонтальном масштабе (≥2 api-контейнеров) или ошибочном запуске двух
worker'ов в одном процессе оба SELECT pending → одни и те же rows → дубль
checker-вызовов и лишний rate-limit burn. Plan 02.1-03 добавляет двойную
защиту:

1. **In-transaction row lock**: ``FOR UPDATE OF c SKIP LOCKED`` — другой
   worker, делающий тот же SELECT в **параллельной** транзакции, пропустит
   эти rows.
2. **Persistent claim window**: после SELECT мы UPDATE'им ``tg_checked_at = NOW()``
   и фильтр SELECT'а — ``tg_checked_at IS NULL OR tg_checked_at < NOW() - INTERVAL '5 minutes'``.
   Это переживает commit транзакции — другой worker, пришедший на следующем
   тике (или вторая инстанция), не подберёт recently-claimed rows.

Тесты:
- ``test_sql_uses_for_update_skip_locked`` — статический grep по исходнику _tick.
- ``test_recently_claimed_contacts_skipped`` — контакт с ``tg_checked_at = NOW()`` пропускается.
- ``test_stale_claim_recovered_after_5_min`` — контакт с ``tg_checked_at = NOW() - 10 min`` подбирается.
"""

import inspect
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.services import contact_check_worker as ccw_module
from app.services.contact_check_worker import ContactCheckWorker

pytestmark = pytest.mark.asyncio


# ─── Static SQL guard ────────────────────────────────────────────────────────


def test_sql_uses_for_update_skip_locked():
    """SELECT в _tick содержит 'FOR UPDATE OF c SKIP LOCKED' и tg_checked_at-claim filter."""
    source = inspect.getsource(ccw_module.ContactCheckWorker._tick)
    assert "FOR UPDATE OF c SKIP LOCKED" in source, (
        "CR-08: SELECT pending должен использовать FOR UPDATE OF c SKIP LOCKED"
    )
    assert "tg_checked_at" in source, (
        "CR-08: _tick должен трогать tg_checked_at для claim window"
    )
    # Транзакционный wrap (db.begin) — без него FOR UPDATE row-lock освободится
    # сразу после SELECT'а в auto-commit режиме AsyncSession.
    assert "async with db.begin" in source or "await db.begin" in source, (
        "CR-08: SELECT + UPDATE claim должны быть в одной транзакции"
    )


# ─── Functional: claim window ────────────────────────────────────────────────


async def test_recently_claimed_contacts_skipped(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """Контакты с tg_checked_at < 5 минут не подхватываются (claim guard).

    Помечаем 2 из 3 pending как только что зарезервированные другим worker'ом
    (tg_checked_at = NOW()). Запускаем _tick → check_phones должен получить
    только 1 phone (третий контакт без claim'а).
    """
    contacts = await test_contacts_factory(count=3, tg_status="pending")
    claimed_ids = [str(contacts[0].id), str(contacts[1].id)]
    await async_db_session.execute(
        text("UPDATE contacts SET tg_checked_at = NOW() WHERE id = ANY(:ids)"),
        {"ids": claimed_ids},
    )
    await async_db_session.commit()

    fake_summary = {
        "checked": 1,
        "registered": 0,
        "not_registered": 1,
        "flood_wait_hit": False,
        "results": [{"phone": contacts[2].phone, "is_registered": False}],
    }
    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=fake_summary),
    ) as mock:
        await worker._tick()

    # Должен был быть вызван 1 раз, и phones содержит ровно один — третий
    # контакт. Первые два — recently-claimed, пропущены.
    assert mock.await_count == 1
    call_kwargs = mock.await_args.kwargs
    assert call_kwargs["phones"] == [contacts[2].phone]


async def test_stale_claim_recovered_after_5_min(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """Контакт с tg_checked_at старше 5 минут подбирается (recovery от падения worker'а).

    Сценарий: worker A зарезервировал contact, упал (или container kill -9), не
    успел записать финальный статус. tg_checked_at застрял в "давно". Следующий
    tick должен подобрать contact (NOW() - INTERVAL '5 minutes' > stale claim).
    """
    contact = await test_contacts_factory(count=1, tg_status="pending")
    # Помечаем claim'ом 10 минут назад
    await async_db_session.execute(
        text(
            "UPDATE contacts SET tg_checked_at = NOW() - INTERVAL '10 minutes' "
            "WHERE id = :cid"
        ),
        {"cid": str(contact.id)},
    )
    await async_db_session.commit()

    fake_summary = {
        "checked": 1,
        "registered": 1,
        "not_registered": 0,
        "flood_wait_hit": False,
        "results": [
            {"phone": contact.phone, "is_registered": True, "telegram_id": 999}
        ],
    }
    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=fake_summary),
    ) as mock:
        await worker._tick()
        assert mock.await_count == 1, "Stale-claim contact должен быть подобран"


async def test_claim_update_sets_tg_checked_at(
    async_db_session, test_workspace, test_checker, test_contacts_factory
):
    """После _tick'а ВСЕ подобранные contacts имеют свежий tg_checked_at.

    Это документирует контракт: claim window — это persistent guard, не только
    in-transaction row lock.
    """
    contacts = await test_contacts_factory(count=2, tg_status="pending")
    fake_summary = {
        "checked": 2,
        "registered": 0,
        "not_registered": 2,
        "flood_wait_hit": False,
        "results": [
            {"phone": c.phone, "is_registered": False} for c in contacts
        ],
    }
    worker = ContactCheckWorker()
    with patch(
        "app.services.contact_check_worker.checker_service.check_phones",
        new=AsyncMock(return_value=fake_summary),
    ):
        await worker._tick()

    result = await async_db_session.execute(
        text(
            "SELECT tg_checked_at FROM contacts WHERE id = ANY(:ids)"
        ),
        {"ids": [str(c.id) for c in contacts]},
    )
    rows = result.fetchall()
    assert len(rows) == 2
    assert all(r.tg_checked_at is not None for r in rows)
