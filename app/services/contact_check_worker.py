"""ContactCheckWorker (Phase 2 — CONT-04, D-19, D-20).

Background asyncio task в lifespan API-контейнера:

- SELECT pending contacts вместе с workspace's active checker через JOIN LATERAL
  (workspace-isolated: ``s.workspace_id = c.workspace_id`` AND ``role='checker'``
  AND ``auth_status='ok'``). Phase 14 (RESV-05/D-11) добавляет
  ``restriction_status='none'`` AND ``lifecycle_status <> 'paused'`` AND
  ``(restricted_until IS NULL OR restricted_until <= NOW())`` — degraded/paused/
  cooling-down checker НЕ выбирается, поэтому ``spam_limited``-флаг реально
  останавливает worker (закрытая дыра «checker keeps lying»). Mobiles (+79…)
  дренируются первыми (RESV-04/D-08), а per-checker daily-cap считается из
  durable источника (``contacts_cache`` writes today, RESV-02/D-10).
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
        """Один tick: подобрать pending → resolve → update. Returns processed count.

        Phase 02.1 (CR-08): двойная защита от race между двумя ContactCheckWorker
        экземплярами (горизонтальный масштаб или ошибочный запуск).

        1. ``FOR UPDATE OF c SKIP LOCKED`` — в открытой транзакции row-lock
           держится до commit'а; другой worker, выполняющий тот же SELECT
           параллельно, пропустит lock'нутые rows.
        2. ``tg_checked_at`` claim window — после SELECT'а мы UPDATE'им
           ``tg_checked_at = NOW()`` (без смены ``tg_status`` — CHECK constraint
           не разрешает 'processing'). Фильтр SELECT'а отсекает контакты,
           заклеймленные менее 5 минут назад. Это переживает commit и защищает
           от второго worker'а, пришедшего на **следующем тике**.

        Stale claim (worker упал между SELECT и _apply_results) автоматически
        восстанавливается: через 5 минут фильтр снова допустит contact.
        """
        async with AsyncSessionLocal() as db:
            # JOIN LATERAL: для каждого pending контакта подтягиваем checker
            # из ЕГО workspace (workspace isolation). Если в workspace нет
            # checker'а — контакт пропускается (JOIN LATERAL без match → строка
            # выпадает). D-20 ``unchecked`` контакты тут не выбираются по
            # ``tg_status='pending'`` фильтру.
            async with db.begin():
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
                              -- RESV-05/D-11: a degraded/paused checker is NEVER
                              -- selected, so the spam_limited flag actually stops
                              -- the worker (the hole that let the broken checker lie).
                              AND restriction_status = 'none'
                              AND lifecycle_status <> 'paused'
                            LIMIT 1
                        ) s ON TRUE
                        WHERE c.tg_status = 'pending'
                          AND (c.phone IS NOT NULL OR c.username IS NOT NULL)
                          AND (c.tg_checked_at IS NULL
                               OR c.tg_checked_at < NOW() - INTERVAL '5 minutes')
                        -- RESV-04/D-08: mobiles (+79…) ~50% live → drain first.
                        ORDER BY (c.phone LIKE '+79%') DESC,
                                 c.created_at ASC
                        LIMIT :n
                        FOR UPDATE OF c SKIP LOCKED
                        """
                    ),
                    {"n": self.batch_size},
                )
                rows = result.fetchall()

                if rows:
                    # Claim: tg_checked_at = NOW() — другой worker увидит < 5min
                    # и пропустит на следующем тике. Без смены tg_status
                    # (CHECK constraint не позволяет 'processing').
                    contact_ids = [str(r.contact_id) for r in rows]
                    await db.execute(
                        text(
                            "UPDATE contacts SET tg_checked_at = NOW() "
                            "WHERE id = ANY(:ids)"
                        ),
                        {"ids": contact_ids},
                    )
                # commit при выходе из async with db.begin()

        if not rows:
            return 0

        # Группируем по checker_id (обычно один checker per workspace в v1, но
        # SQL может вернуть несколько workspaces в одном tick'е).
        rows_sorted = sorted(rows, key=lambda r: str(r.checker_id))
        processed = 0
        for checker_id, items_iter in groupby(rows_sorted, key=lambda r: r.checker_id):
            items = list(items_iter)
            first = items[0]

            # Phone wins when present; username-only contacts resolve via username.
            phone_items = [r for r in items if r.phone]
            username_items = [r for r in items if not r.phone and r.username]

            common = dict(
                workspace_id=str(first.workspace_id),
                checker_id=str(checker_id),
                checker_slug=first.checker_slug,
                encrypted_session=first.session_string,
                proxy=first.proxy,
            )

            if phone_items:
                try:
                    summary = await checker_service.check_phones(
                        phones=[r.phone for r in phone_items], **common
                    )
                    await self._apply_results(phone_items, summary)
                    processed += len(phone_items)
                    logger.info(
                        f"📋 ContactCheckWorker: checker={first.checker_slug} (phones) "
                        f"checked={summary.get('checked', 0)} "
                        f"reg={summary.get('registered', 0)} "
                        f"not_reg={summary.get('not_registered', 0)} "
                        f"flood={summary.get('flood_wait_hit', False)}"
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        f"❌ ContactCheckWorker: checker={first.checker_slug} phones failed: {exc}",
                        exc_info=True,
                    )

            if username_items:
                try:
                    summary = await checker_service.check_usernames(
                        usernames=[r.username for r in username_items], **common
                    )
                    await self._apply_results(username_items, summary)
                    processed += len(username_items)
                    logger.info(
                        f"📋 ContactCheckWorker: checker={first.checker_slug} (usernames) "
                        f"checked={summary.get('checked', 0)} "
                        f"reg={summary.get('registered', 0)} "
                        f"not_reg={summary.get('not_registered', 0)} "
                        f"flood={summary.get('flood_wait_hit', False)}"
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        f"❌ ContactCheckWorker: checker={first.checker_slug} usernames failed: {exc}",
                        exc_info=True,
                    )

        return processed

    async def _apply_results(self, items: list, summary: dict) -> None:
        """UPDATE contacts по результатам checker'а.

        ``summary['results']`` — список ``{phone|username, is_registered,
        telegram_id?, error?, from_cache?}``. Phone-контакты сматчиваем по phone
        (E.164 нормализован при импорте), username-контакты — по bare username.
        Если ключ отсутствует в ``results`` — не трогаем строку: для FloodWait
        partial run эти контакты останутся в ``'pending'`` и попадут в след. tick.
        """
        results_by_phone = {
            r.get("phone"): r for r in summary.get("results", []) if r.get("phone")
        }
        results_by_username = {
            r.get("username"): r for r in summary.get("results", []) if r.get("username")
        }
        if not results_by_phone and not results_by_username:
            return
        async with AsyncSessionLocal() as db:
            for item in items:
                if item.phone:
                    res = results_by_phone.get(item.phone)
                else:
                    res = results_by_username.get((item.username or "").lstrip("@"))
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
