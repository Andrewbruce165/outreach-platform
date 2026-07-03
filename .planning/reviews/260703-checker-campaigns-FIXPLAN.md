# Fix Plan: Checker + Campaign Sending Review Findings

**Source:** `.planning/reviews/260703-checker-campaigns-REVIEW.md` (2026-07-03, deep, 28 findings)
**Prod facts verified 2026-07-03:** zombie pending rows on non-running campaigns: **1**; `message_queue.priority IS NULL` among pending: **14**; failed items: **127**; `routers/queue.py` + `routers/proxy_pool.py` not mounted, both import nonexistent `app.routers.auth`.

Порядок батчей = приоритет. Каждый батч — отдельный `/gsd:quick` (атомарные коммиты, только конкретные файлы — в репо параллельная работа по фазе 20). Процесс на батч: короткий план правок → подтверждение → код → тесты через test-overlay → deploy (`docker compose up -d --build api listener`) → live-верификация → commit.

---

## Батч A — Чекер: детекторы лжи (CR-01, CR-02, IN-01, IN-08) — КРИТИЧЕСКИЙ

**Цель:** пробы снова реально тестируют чекеров; инлайн-детектор ловит отравленные батчи. Оба бага реанимируют Phase-14 (false negatives → `not_registered/high/clean`).

Правки:
1. `contact_check_worker.py::probe_checker` (~591) и `::_recover_checkers` (~748): заменить `check_phones` на **`CheckerService.probe_control`** (live-only, уже написан — checker.py:305). `flood_wait_hit=True` или обрезанный батч в пробе = MISS, не clean.
2. `contact_check_worker.py::_is_throttle_signal` (~121): фильтровать live-результаты (`not r.get("from_cache")`), убрать условие на `summary["registered"]` — считать аномалию только по live.
3. IN-08: для `from_cache=True` результатов не штамповать `tg_resolved_by=<текущий чекер>` — писать NULL (или маркер `cache`), чтобы не портить D-09 forensics.

Прод-ремедиация (до деплоя): диагностика `contacts_cache` по контрольным номерам — есть ли строки `is_registered=false`; если есть — точечный DELETE (с бэкапом через backup.sh).

Тесты: unit на `_is_throttle_signal` (смешанный cache/live батч, все live=false → True; чистый батч → False); тест что probe-путь не читает/не пишет кеш (mock probe_control); flood_wait в пробе = miss.

Верификация: лог probe-цикла показывает живые резолвы; один recheck-батч проходит без ложных финализаций.

Оценка: маленький диф, ~3 файла. Риск низкий — probe_control уже существует и протестирован докстрингом на этот сценарий.

---

## Батч B — Чекер: устойчивость (WR-05, WR-06, WR-08, WR-07, IN-02, IN-03)

1. **WR-05:** `checker.py` (~448, ~583): инлайн-sleep на FloodWait капнуть `min(exc.seconds, 60)`; при большем — вернуть частичный батч с `flood_wait_hit=True` немедленно (инлайн-деградация уже паркует чекера durable-кулдауном — правильное место для долгих ожиданий).
2. **WR-06:** `checker.py::_get_client` (~184): классифицировать unauthorized/auth-ошибки как в `TelegramService.get_client` — `UPDATE senders SET auth_status='session_expired' WHERE id=:sid` + типизированная ошибка. В `_tick` except-ветке — короткий backoff через `checker_rest_until`, чтобы персистентно падающий чекер не крутился в горячем цикле.
3. **WR-08:** `_recover_checkers`: сэмпл считать до цикла; `return` на пустом сэмпле → предупреждение и выход ДО цикла (не обрывать recovery остальных). Если `_CONTROL_SET` пуст — инлайн-деградация НЕ ставит `spam_limited` (только rest), либо api падает громко на старте — обсудить, рекомендую rest-only + ERROR-лог.
4. **WR-07:** `checker.py` (~136-161): после пустого импорта — `DeleteByPhonesRequest(phones=[phone])` best-effort; оба клинапа в `finally` (привести код к контракту докстринга).
5. **IN-02:** результаты с `PhoneNumberInvalidError` тегировать `{"error": "invalid_phone"}` → ветка `tg_status='error'` в `_apply_results` оживает.
6. **IN-03:** LATERAL-выборка чекера: `ORDER BY checker_rest_until NULLS FIRST, id`.

Тесты: unit на кап FloodWait; на session-expired → auth_status flip; на recovery с пустым сэмплом; на invalid-phone → error.

---

## Батч C — Очередь: приоритет и позиция (WR-02, WR-03)

1. Миграция `0NN_message_queue_priority_default.sql` (идемпотентная):
   `ALTER TABLE message_queue ALTER COLUMN priority SET DEFAULT 0; UPDATE message_queue SET priority=0 WHERE priority IS NULL;` — плюс те же defaults для `attempts`/`as_draft` (сейчас NULL с raw-пути).
2. ORM: `server_default="0"` на priority (класс бага default= vs server_default= — известный).
3. `campaign_enqueue.py` INSERT: передавать `priority` явно.
4. **WR-03:** `_queue_position` (~1550): переписать — «впереди» = `(COALESCE(priority,0) > :p) OR (COALESCE(priority,0) = :p AND created_at < :c)`.

Прод: бэкфилл в миграции покрывает 14 NULL-строк. Тест: unit на позицию с разными приоритетами и NULL.

---

## Батч D — Head-of-line blocking в отправке (WR-04) — нужно решение

`queue.py` (~355): убрать `await asyncio.sleep(long_pause)` из общего цикла. Варианты:
- **(рекомендую)** колонка `senders.long_pause_until` (миграция) — выставляется вместо sleep, проверяется в eligibility-предикате `_tick`; пауза переживает рестарт, replica-safe;
- сдвиг `scheduled_at` всех pending этого сендера — без новой колонки, но мутирует расписание строк и ломает Phase-13 pacing-математику.

**НЕ менять** эмпирические значения пауз/интервалов — только механизм. Плюс фикс повторного срабатывания паузы на неизменном 30-мин счётчике (`recent_count % pause_every == 0` на соседних тиках) — after-pause маркер в той же колонке решает.

Тесты: пауза не блокирует других сендеров; пауза переживает рестарт; не срабатывает дважды на одном счётчике.

---

## Батч E — Кампании: lifecycle (WR-09, WR-12, IN-05, IN-06, IN-07, IN-10, IN-11, IN-12)

1. **WR-09:** enqueue-worker INSERT через `WHERE EXISTS (campaigns.status='running')` + идемпотентный клинап: воркер видит pending на non-running кампании → cancel. Прод: одноразовый SQL-cancel существующей 1 зомби-строки.
2. **WR-12 — нужно продуктовое решение.** Терминально-failed item поглощает контакт навсегда (в проде 127 failed vs 135 sent). Варианты: (а) снимать CCA при терминальном fail без предшествующего sent → контакт снова eligible; (б) `POST /campaigns/{id}/requeue-failed` + сводка failed в ответе кампании; (в) оба. Рекомендую **(в)**: авто-освобождение CCA только для «холодных» fail (не отправляли ничего), requeue-endpoint для ручного повтора, `failed_count` в campaign response.
3. **IN-11:** per-campaign try/except в `_tick` (одна битая кампания не морит остальных).
4. **IN-05:** `attach_sender`: конфликт-лист фильтровать по `payload.sender_id`.
5. **IN-06:** `duplicate_campaign`: IntegrityError→409 как в create-пути.
6. **IN-07:** `past_stop_date`-фейл: `AND status='pending'` guard + callback webhook.
7. **IN-10:** `pool_health.active` = restriction none AND auth ok AND lifecycle active (зеркало `_maybe_autopause`).
8. **IN-12:** `sent_by` выводить из item'а (`campaign_id`/extra_data → 'ai'|'campaign'), 'human' только для ручных. Опционально: бэкфилл истории — обсудить, скорее нет.

---

## Батч F — /send hardening (WR-10, WR-11, IN-09) — координация с n8n

1. **WR-10:** нормализовать `recipient_phone` через `normalize_to_e164` (utils/phone.py уже есть), username-ключи — как есть; 422 на невалид. **До деплоя:** посмотреть реальные payload'ы n8n (что сейчас приходит — `79…`? `8…`?), чтобы 422 не поломал живой флоу; нормализация должна принимать их все.
2. **WR-11:** (а) 409 `CAMPAIGN_NOT_RUNNING` для non-running кампаний; (б) дедуп: существующий `pending/processing` row по `(campaign_id, recipient_phone)` → вернуть его же (200, idempotent) вместо второй вставки. Решение по семантике: reject vs return-existing — рекомендую return-existing (n8n-retry-friendly).
3. **IN-09:** явный `sender_slug`: `restriction_status != 'none'` → 409 `SENDER_NOT_READY`.

---

## Батч G — Identity / rotation (WR-13, WR-14, IN-04)

1. **WR-13:** `rotation.py` (~72): sticky-предикат дополнить `role='sender' AND restriction_status='none'` — stale-ветка ниже уже корректно переназначает. Снять ручной обход в failover.py, если станет лишним (проверить).
2. **WR-14:** `telegram.py::_set_auth_status`: UPDATE по `sender_id` (callers имеют Sender row), не по slug; локи клиентов в `TelegramService`/`CheckerService` ключевать по `sender.id`. Осторожно: telegram.py задевает всё — прогнать полный сьют.
3. **IN-04:** строгий guard-lookup конверсаций: `ORDER BY updated_at DESC LIMIT 1`.

---

## Батч H — Мёртвый код (WR-01)

Удалить `app/routers/queue.py` и `app/routers/proxy_pool.py` (не смонтированы, импорт битый — проверено). Перед удалением: grep по tests/ и openapi — убедиться, что никто не импортирует. Если функциональность proxy-pool нужна в v1 — вместо удаления переписать на `auth_dep` + workspace-scope (отдельной задачей).

---

## Открытые решения (нужен ответ владельца)

| # | Вопрос | Рекомендация |
|---|--------|--------------|
| 1 | WR-12: политика поглощённых контактов | (в) авто-снятие CCA для холодных fail + requeue-endpoint + failed_count |
| 2 | WR-04: механизм паузы | колонка `senders.long_pause_until` |
| 3 | WR-11: семантика дедупа /send | return-existing (идемпотентный 200) |
| 4 | WR-08: пустой control-set | rest-only деградация + ERROR-лог (не spam_limited) |
| 5 | WR-01: proxy_pool.py | удалить сейчас, переписать когда понадобится |

## Отложено (не фиксим сейчас)

- Структурные риски №1-2 (single-coroutine workers, in-memory health state) — архитектурная работа, кандидат в отдельную фазу «worker hardening v2» при росте тенантов; батчи A/B/D снимают самые острые проявления.
- Идемпотентный ключ отправки против дублей при crash-recovery (структурный риск №4) — частично закрывается WR-11; полный fix (UNIQUE partial index) — в ту же будущую фазу.
- IN-12 бэкфилл исторических `sent_by` — только если аналитика на нём уже строится.
