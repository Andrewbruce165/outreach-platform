"""ContactCheckWorker (Phase 2 — CONT-04, D-19, D-20).

Background asyncio task в lifespan API-контейнера:

- SELECT pending contacts вместе с workspace's active checker через JOIN LATERAL
  (workspace-isolated: ``s.workspace_id = c.workspace_id`` AND ``role='checker'``
  AND ``auth_status='ok'``).
- Группируем по checker_id, батчем зовём
  ``checker_service.check_phones(...)`` — он уже умеет lock per checker_slug,
  FloodWait handling и polite delay 2–3.5s.
- По результатам UPDATE ``contacts.tg_status``
  (``'registered' | 'not_registered' | 'error'``) +
  ``tg_telegram_id`` / ``tg_username_resolved`` / ``tg_error`` /
  ``tg_checked_at``.

D-20: контакты в workspace без checker'а имеют ``tg_status='unchecked'``
(план 02-04 уже выставляет этот статус на импорте). JOIN LATERAL по
``role='checker'`` их пропускает — нечем резолвить. Когда checker появляется,
юзер вручную дёргает ``POST /api/v1/contacts/recheck`` (план 02-05 Task 2),
который переводит существующие контакты обратно в ``'pending'`` — этот worker
их подберёт на следующем тике.

Lifecycle: ``start()`` / ``stop()`` — registered in ``app/main.py`` lifespan.
"""

import asyncio
import logging
import os
from itertools import groupby
from typing import Optional

from sqlalchemy import text

from app.database import AsyncSessionLocal
from app.services.checker import checker_service

logger = logging.getLogger(__name__)


# Env-overridable knobs (RESEARCH §"ContactCheckWorker — стратегия rate-limit"
# + CONTEXT C-06). Defaults: batch=5, poll=5s — ~30 phones/min per checker
# с учётом polite delay 2–3.5s внутри CheckerService.check_phones.
CONTACT_CHECK_BATCH_SIZE = int(os.environ.get("CONTACT_CHECK_BATCH_SIZE", "5"))
CONTACT_CHECK_POLL_INTERVAL = int(os.environ.get("CONTACT_CHECK_POLL_INTERVAL", "5"))


class ContactCheckWorker:
    """Background worker: poll pending contacts → batch resolve via checker.

    Singleton instance per process (создаётся в module scope ниже). Lifecycle
    повторяет паттерн ``OnboardingCleanupWorker`` / ``WarmupWorker`` — старт
    в ``app/main.py`` lifespan, остановка в shutdown.
    """

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.batch_size = CONTACT_CHECK_BATCH_SIZE
        self.poll_interval = CONTACT_CHECK_POLL_INTERVAL

    def start(self):
        """Запустить background task. Идемпотентно (повторный start — no-op)."""
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._run(), name="contact-check-worker")
            logger.info(
                f"📋 ContactCheckWorker started "
                f"(batch={self.batch_size}, poll={self.poll_interval}s)"
            )

    async def stop(self):
        """Остановить background task — gracefully (cancel + await)."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("📋 ContactCheckWorker stopped")

    async def _run(self):
        """Главный цикл — sleep после tick, чтобы не лочить startup."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 — worker must not die
                logger.error(
                    f"❌ ContactCheckWorker tick error: {exc}", exc_info=True
                )
            await asyncio.sleep(self.poll_interval)

    async def _tick(self) -> int:
        """Один tick: подобрать pending → resolve → update. Returns processed count."""
        async with AsyncSessionLocal() as db:
            # JOIN LATERAL: для каждого pending контакта подтягиваем checker
            # из ЕГО workspace (workspace isolation). Если в workspace нет
            # checker'а — контакт пропускается (JOIN LATERAL без match → строка
            # выпадает). D-20 ``unchecked`` контакты тут не выбираются по
            # ``tg_status='pending'`` фильтру.
            result = await db.execute(
                text(
                    """
                    SELECT c.id AS contact_id,
                           c.workspace_id,
                           c.phone,
                           c.username,
                           s.id AS checker_id,
                           s.slug AS checker_slug,
                           s.session_string,
                           s.proxy
                    FROM contacts c
                    JOIN LATERAL (
                        SELECT id, slug, session_string, proxy
                        FROM senders
                        WHERE workspace_id = c.workspace_id
                          AND role = 'checker'
                          AND auth_status = 'ok'
                        LIMIT 1
                    ) s ON TRUE
                    WHERE c.tg_status = 'pending'
                      AND c.phone IS NOT NULL
                    ORDER BY c.created_at ASC
                    LIMIT :n
                    """
                ),
                {"n": self.batch_size},
            )
            rows = result.fetchall()

        if not rows:
            return 0

        # Группируем по checker_id (обычно один checker per workspace в v1, но
        # SQL может вернуть несколько workspaces в одном tick'е).
        rows_sorted = sorted(rows, key=lambda r: str(r.checker_id))
        processed = 0
        for checker_id, items_iter in groupby(rows_sorted, key=lambda r: r.checker_id):
            items = list(items_iter)
            phones = [r.phone for r in items if r.phone]
            if not phones:
                continue
            first = items[0]

            try:
                summary = await checker_service.check_phones(
                    checker_id=str(checker_id),
                    checker_slug=first.checker_slug,
                    encrypted_session=first.session_string,
                    phones=phones,
                    proxy=first.proxy,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"❌ ContactCheckWorker: checker={first.checker_slug} "
                    f"failed: {exc}",
                    exc_info=True,
                )
                continue

            await self._apply_results(items, summary)
            processed += len(items)

            logger.info(
                f"📋 ContactCheckWorker: checker={first.checker_slug} "
                f"checked={summary.get('checked', 0)} "
                f"reg={summary.get('registered', 0)} "
                f"not_reg={summary.get('not_registered', 0)} "
                f"flood={summary.get('flood_wait_hit', False)}"
            )
        return processed

    async def _apply_results(self, items: list, summary: dict) -> None:
        """UPDATE contacts по результатам checker'а.

        ``summary['results']`` — список ``{phone, is_registered, telegram_id?,
        error?, from_cache?, username?}``. Сматчиваем по phone (E.164 уже
        нормализован при импорте). Если phone отсутствует в ``results`` —
        не трогаем строку: для FloodWait partial run эти контакты останутся
        в ``'pending'`` и попадут в следующий tick.
        """
        results_by_phone = {
            r.get("phone"): r for r in summary.get("results", []) if r.get("phone")
        }
        if not results_by_phone:
            return
        async with AsyncSessionLocal() as db:
            for item in items:
                res = results_by_phone.get(item.phone)
                if res is None:
                    # Не обработан (partial из-за FloodWait) — оставляем pending.
                    continue
                if res.get("error"):
                    await db.execute(
                        text(
                            """
                            UPDATE contacts
                            SET tg_status = 'error',
                                tg_error = :err,
                                tg_checked_at = NOW(),
                                updated_at = NOW()
                            WHERE id = :cid
                            """
                        ),
                        {
                            "err": str(res["error"])[:500],
                            "cid": str(item.contact_id),
                        },
                    )
                elif res.get("is_registered"):
                    await db.execute(
                        text(
                            """
                            UPDATE contacts
                            SET tg_status = 'registered',
                                tg_telegram_id = :tid,
                                tg_username_resolved = :uname,
                                tg_checked_at = NOW(),
                                updated_at = NOW()
                            WHERE id = :cid
                            """
                        ),
                        {
                            "tid": res.get("telegram_id"),
                            "uname": res.get("username"),
                            "cid": str(item.contact_id),
                        },
                    )
                else:
                    await db.execute(
                        text(
                            """
                            UPDATE contacts
                            SET tg_status = 'not_registered',
                                tg_checked_at = NOW(),
                                updated_at = NOW()
                            WHERE id = :cid
                            """
                        ),
                        {"cid": str(item.contact_id)},
                    )
            await db.commit()


# Module-level singleton — register start/stop in app/main.py lifespan.
contact_check_worker = ContactCheckWorker()
