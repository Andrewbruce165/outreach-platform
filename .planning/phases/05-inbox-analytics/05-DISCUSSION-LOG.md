# Phase 5: Inbox & Analytics - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-22
**Phase:** 05-inbox-analytics
**Areas discussed:** Manager mode + queue (INBX-04), Bot blocklist (AIRC-04), LLM request log (ANLX-05), Analytics — counters + time-windows (ANLX-01..04)

---

## Manager mode + queue (INBX-04)

### Q1.1: Какой status ставит ручной switch?

| Option | Description | Selected |
|--------|-------------|----------|
| 'manual' — отдельно от 'handoff' | Два разных сигнала: handoff = LLM сам решил transfer_to_manager, manual = юзер вручную взял. Разные бейджи. | ✓ |
| 'handoff' — переиспользуем | Один статус для обоих случаев. Меньше enum-значений. | |
| Ты решаешь | По рекомендации с учётом фильтров/аналитики. | |

**User's choice:** 'manual' — отдельно от 'handoff'
**Notes:** Semantic distinction важна для retrospective ("AI сам передал" vs "менеджер сам взял"). UI рисует разные бейджи.

### Q1.2: Что делаем с pending message_queue items при переводе в manual?

| Option | Description | Selected |
|--------|-------------|----------|
| Cancel все pending этого контакта | UPDATE status='cancelled', error_message='manual_takeover'. Pattern из _handle_antispam_signal. | |
| Оставить pending в очереди | В v1 pending обычно пусто (1 контакт = 1 первое сообщение). | |
| Cancel + пометка «paused by manager» | Как (1), но с явным reason='Conversation taken over manually' и UI показывает что N автосообщений отменены. | ✓ |

**User's choice:** Cancel + пометка «paused by manager»
**Notes:** Больше прозрачности — менеджер видит что именно отменилось.

### Q1.3: Обратный перевод — из manual обратно на AI?

| Option | Description | Selected |
|--------|-------------|----------|
| Одна кнопка → status='active' + ai_enabled=true | Простой тоггл, но стирает 'lead'/'finished' если был. | |
| Только ai_enabled=true (status не трогаем) | Сохраняет факт lead/finished/handoff. Manual switch — manual остаётся пока юзер явно не сменит. | ✓ |
| Backend решает по логике | Manual → active автоматически; lead/handoff/finished → только ai_enabled. | |

**User's choice:** Только ai_enabled=true (status не трогаем)
**Notes:** UX: «AI снова отвечает, но факт лида/финиша — это историческая правда, не стираем».

### Q1.4: Legacy POST /conversations/{id}/send — переносим в v1?

| Option | Description | Selected |
|--------|-------------|----------|
| Да, переносим (рерайт под AuthDep+workspace) | INBX-04 в духе: менеджер взял диалог → хочет ответить. | ✓ |
| Да, но только когда ai_enabled=false | Больше дисциплины — сначала disable AI, потом пишет. | |
| Отложить в v2 | Inbox = «monitor only», менеджер отвечает в своём Telegram-приложении. | |

**User's choice:** Да, переносим — при ручной отправке из UI автоматически переход в status='manual' + ai_enabled=false.
**Notes:** Изначальная формулировка содержала опечатку (ai_enabled=true), уточнено в follow-up: ai_enabled=false. Logic: сам акт отправки = takeover.

---

## Bot blocklist (AIRC-04)

### Q2.1: Откуда берём «это бот»?

| Option | Description | Selected |
|--------|-------------|----------|
| Telethon event.sender.bot=True | Официальный Telegram-side флаг. Покрывает все BotFather-боты. | ✓ |
| Hardcoded blocklist telegram_id/username | Точно, но не ловит «new» системные боты. | |
| Оба: user.bot=True + workspace blocklist override | Default + workspace может разрешить отвечать конкретному боту. | |

**User's choice:** Telethon event.sender.bot=True
**Notes:** KISS — один универсальный признак, без поддержки списков.

### Q2.2: Где в потоке listener'а срабатывает фильтр?

| Option | Description | Selected |
|--------|-------------|----------|
| До INSERT conversation — вообще не сохраняем | event.sender.bot → return сразу. Минус: история сообщений от ботов теряется. | |
| INSERT сообщения, но не вызываем AI | Сообщение пишется, conversation создаётся с ai_enabled=false + новый status. История видна. | ✓ |
| INSERT + флаг contact_is_bot, AI решает сам | Гибкий, но избыточный для v1. | |

**User's choice:** INSERT сообщения, но не вызываем AI
**Notes:** Сохраняет историю для inbox-debug.

### Q2.3: Какой status у conversation от бота?

| Option | Description | Selected |
|--------|-------------|----------|
| Новый status='bot_ignored' в CHECK | Расширяем CHECK constraint migration 016 → 017. Inbox прячет по дефолту. | ✓ |
| Переиспользуем 'paused' + paused_reason='bot_ignored' | Без расширения enum. Смешивает с обычным paused. | |
| Вообще не создаём conversation | Противоречит Q2.2 выбору. | |

**User's choice:** Новый status='bot_ignored' в CHECK
**Notes:** Migration 017 расширяет CHECK constraint. Inbox по дефолту скрывает.

### Q2.4: Что делаем с _handle_antispam_signal?

| Option | Description | Selected |
|--------|-------------|----------|
| Оставить как safety net | Покрывает редкие service-аккаунты без bot=True flag. | ✓ |
| Убрать (выпилить) | user.bot покрывает всё. Чистим код. | |
| Слить с новым фильтром | Общий handler в listener'е. | |

**User's choice:** Оставить как safety net
**Notes:** Семантика разная: антиспам = ставит sender на паузу полностью; bot_ignored = только один диалог.

---

## LLM request log (ANLX-05)

### Q3.1: Куда храним log каждого LLM-вызова?

| Option | Description | Selected |
|--------|-------------|----------|
| Отдельная таблица llm_calls | Полноценная схема с FK на conversation/campaign/agent/sender. Индексы по conv_id + workspace_id. | ✓ |
| JSONB массив на conversations.llm_history | Без join'ов, но разрастается с каждым вызовом, тяжело при >100 calls. | |
| Append-only файлы /var/log/llm/{wsp}/{conv}.jsonl | Дёшево, но нет SQL-фильтра. Избыточная инфра. | |

**User's choice:** Отдельная таблица llm_calls
**Notes:** Migration 017 создаёт таблицу с FK + indexes.

### Q3.2: Что в поле prompt?

| Option | Description | Selected |
|--------|-------------|----------|
| Полный messages array отправленный в OpenAI | JSONB со всеми ролями + tools spec + model + temperature. 5-50KB per row. | ✓ |
| Только последний user-message + summary system | Компактнее (<1KB), но теряем контекст. | |
| Полный + truncate large texts > 8KB | JSONB + защита от патологически больших. | |

**User's choice:** Полный messages array
**Notes:** Полная воспроизводимость для inbox-debug.

### Q3.3: Retention — сколько храним llm_calls?

| Option | Description | Selected |
|--------|-------------|----------|
| 30 дней + nightly DELETE | Cron очистка. | |
| Всё навсегда (нет cleanup) | Никаких cron'ов. PG растёт линейно. v2 archival. | ✓ |
| Последние 50 per conversation | Предсказуемый размер. Двойная запись на каждый вызов. | |

**User's choice:** Всё навсегда (нет cleanup)
**Notes:** В v1 объём оценивается ~30k rows/мес — несущественно. v2 решит archival.

### Q3.4: Что логируем — только listener или + warmup?

| Option | Description | Selected |
|--------|-------------|----------|
| Только listener (ai_engine.generate_response) | conversation_id NOT NULL. Warmup отдельно. | ✓ |
| Всё: listener + warmup + warmup-initial-message | conversation_id NULLable. Полный audit OpenAI расходов. | |
| Planner решает | Phase 5 жёстко закрепляет только listener. | |

**User's choice:** Только listener
**Notes:** Audit cost-tracking warmup'а — v2.

---

## Analytics — counters + time-windows (ANLX-01..04)

### Q4.1: Как считаем метрики на карточках?

| Option | Description | Selected |
|--------|-------------|----------|
| Real-time COUNT() per запрос | 4-5 SELECT'ов с COUNT. <100ms на v1 объёмах. | ✓ |
| Materialized view + periodic REFRESH | Чтение <10ms, но данные с лагом. v1 overkill. | |
| Pre-aggregated counters в отдельных таблицах | Самый быстрый вывод, но сложность синхронизации. Риск расхождения. | |

**User's choice:** Real-time COUNT() per запрос
**Notes:** KISS. v2 добавит materialized view если потребуется.

### Q4.2: Какой time-window?

| Option | Description | Selected |
|--------|-------------|----------|
| All-time (одно число без dropdown) | Простая UI. Никаких ?from=&to= параметров. | ✓ |
| All-time + last 7 days (две цифры на карточке) | Сразу trend-сигнал. | |
| Custom range (?from=&to=) + default all-time | API гибкое; UI в v2 добавит datepicker. | |

**User's choice:** All-time (одно число)
**Notes:** UX-простота. Custom range — v2 если попросят клиенты.

### Q4.3: Что значит «Отвечено»?

| Option | Description | Selected |
|--------|-------------|----------|
| Количество conversations с хотя бы одним inbound | Response rate по диалогам. | |
| Количество всех inbound messages от contact | Полный волум. | |
| Оба: '156 диалогов (4536 сообщений)' | Response rate + engagement depth. | ✓ |

**User's choice:** Оба
**Notes:** Информативнее. Два SELECT'a — дёшево.

### Q4.4: Набор карточек на 4 уровнях — одинаковый?

| Option | Description | Selected |
|--------|-------------|----------|
| Workspace/campaign — 4 карточки; sender — +ошибки; agent — +кампании | Per-level вариации (соответствует ANLX-01..04 буквально). | |
| Одинаковый набор 4 карточек на всех уровнях | Отправлено / Отвечено / Лиды / Финиши везде. Sender-errors на странице sender'а отдельно. Agent-campaigns — в /agents response. | ✓ |
| Planner выберет по принципу ANLX-01..04 | Гибкость planner'у. | |

**User's choice:** Одинаковый набор 4 карточек на всех уровнях
**Notes:** UI-consistency. Sender-specific errors живут на странице sender'а (Phase 2 SNDR-03), не в analytics-dashboard.

---

## Claude's Discretion

- **C-01:** Источник для «Отправлено» — `messages_log` / `message_queue` / `messages`. Planner выберет.
- **C-02:** Race-condition защита при D-02 cancel-queue (item у воркера в обработке) — advisory-lock или signal-handler-skip.
- **C-03:** Точная shape Pydantic-схем и naming endpoint'ов.
- **C-04:** Composite indexes для real-time COUNT'ов.
- **C-05:** llm_logger — отдельный модуль vs inline в ai_engine.
- **C-06:** Pagination для messages — OFFSET vs cursor.
- **C-07:** Распределение фич по 4 планам ROADMAP.
- **C-08:** Lovable UI badge'и для 7 значений status — Lovable-сторона.
- **C-09:** Search в inbox — v1 или deferred.

## Deferred Ideas

### Для Phase 6 (Admin Master Bot)
- ADMN-02: бот шлёт уведомление при flip в 'manual' (Phase 5) или 'handoff' (Phase 4 D-12).
- ADMN-03: уведомление при lifecycle_status='paused' после _handle_antispam_signal.

### Для v2
- Materialized views / pre-aggregated counters когда real-time медленный
- Time-window filters (?from=&to=) + UI dropdown
- llm_calls retention / archival / partitioning
- Truncation prompt'ов >8KB
- Warmup-LLM-вызовы в llm_calls (audit-cost)
- Cursor-based pagination для messages
- Search в inbox (если C-09 deferred)
- Bot blocklist per-workspace override
- SSE/WebSocket real-time updates
- Reverse switch с restore previous status
- Inbox export в CSV (REQUIREMENTS ANLX-EXP-01)
- Per-status фильтр в analytics
