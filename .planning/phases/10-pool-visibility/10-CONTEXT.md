# Phase 10: Pool Visibility & Restriction Audit - Context

**Gathered:** 2026-06-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Два связанных направления (фаза помечена `optional` в ROADMAP):

1. **Видимость пула.** В ответе кампании (`CampaignResponse`) показывать здоровье пула (N активно / K на паузе до T) + бейдж во фронте, чтобы была видна **частичная** пауза кампании (часть аккаунтов под ограничением, кампания «полу-стоит»).
2. **Аудит ограничений.** Durable, append-only event-log всех restriction-событий аккаунта (`spam_limited` / `frozen` / `flood_wait` / `cleared` / `banned`) с источником (`queue_error` / `spambot_reconcile`) и **срезом предшествующей активности** sender'а — чтобы реконструировать «что делали → за что получили». Сегодня этих данных негде взять (см. `.planning/notes/account-restriction-audit-gap.md`).

**Вне scope (отложено / non-goal этой фазы):**
- **Агрегат-дашборд** (флуд/ограничения по дням, графики, % пула под ограничением во времени) → backlog. В этой фазе только бейдж пула + мини-список событий по аккаунту.
- Real-time алерты по банам — non-goal блока (аудит копит данные; алерты строятся поверх позже).
- Failover активных диалогов, cross-campaign load awareness, «затихать на ответах» — non-goals всего блока Sender Pool Resilience (см. ROADMAP §Phase 10 Non-goals).

</domain>

<decisions>
## Implementation Decisions

### Гранулярность event-log (HLTH-01)
- **D-01:** «Событие» = **смена состояния** restriction (`none→spam_limited`, `→frozen`, `→banned`, `→cleared`) **И** **продление срока** (когда @SpamBot-reconcile сдвинул `restricted_until` вправо). Рядовые reconcile-тики «still limited» **без** сдвига срока событий НЕ порождают (только обновляют `senders.restricted_until`). Цель: чистая хронология «вошёл → продлили N раз → сняли» без шума 37-тиков-в-сутки.
- **D-02:** Каждое restriction-событие хранит минимум: `sender_id`, тип, источник (`queue_error` / `spambot_reconcile`), `restricted_until` на момент события, сырой текст ошибки/ответа @SpamBot, server timestamp. (Append-only, не затирается — в отличие от `message_queue.error_message`.)

### Класс не-restriction ошибок
- **D-03:** Ошибки уровня **получателя** (`PRIVACY_PREMIUM_REQUIRED` и подобные privacy-ошибки) логировать **отдельным классом** через явное поле категории (рабочее: `category='recipient_privacy'` vs `category='restriction'`). Они НЕ являются ограничением аккаунта (аккаунт здоров) и **обязаны** быть исключаемы из restriction-аналитики одним фильтром. Заметка прямо предупреждает не смешивать эти классы.
- **D-04 (discretion):** точное имя/значения поля категории и полный перечень не-restriction классов — research/plan. Интент: один столбец, по которому restriction и не-restriction события разделяются без эвристик.

### Срез активности (HLTH-02)
- **D-05:** Срез снимается **снапшотом в момент записи события** (вычислить и записать в строку события), НЕ вычислять позже из `messages_log`. Обоснование: исходные данные эфемерны (`message_queue.error_message` затирается, логи контейнера ~18ч) — отложенное вычисление сломает реконструкцию, что прямо противоречит мотивации фазы.
- **D-06:** Обязательные поля среза (все четыре):
  1. **Отправки 1ч / 24ч** до события (ядро «что делали», видно превышение темпа).
  2. **Уникальные новые контакты** за окно (холодные первые контакты — чаще всего триггерят PEER_FLOOD).
  3. **Прокси** на момент события (из `senders.proxy` — для корреляции банов с IP/прокси).
  4. **Фактический темп** (реальный темп отправки vs настроенные лимиты).
- **D-07 (discretion):** точные SQL-окна и источник для каждого поля (счёт по `messages_log` по `sender_id`+time vs `message_queue`), формат хранения среза (отдельные колонки vs JSONB), как выразить «фактический темп» — research/plan. Интент зафиксирован: самодостаточная строка события.

### Видимость пула (HLTH-03, часть 1)
- **D-08:** В ответе кампании — **и агрегат, и пер-sender**:
  - Агрегат-объект `pool_health` (рабочее): `{active, paused, total, earliest_resume_at}` — для бейджа.
  - Обогатить каждый `attached_senders[]` полями `restriction_status` / `restricted_until`, чтобы фронт показал «кто именно на паузе» без доп-запроса.
- **D-09:** Бейдж пула во фронте — **3 состояния**:
  - 🟢 зелёный = весь пул активен;
  - 🟡 жёлтый = **частичная пауза** (K из N на паузе до T) — главный сигнал фазы;
  - 🔴 красный = весь пул на паузе (кампания фактически стоит).
- **D-10 (discretion):** точная форма/имена полей `pool_health`, где считается агрегат (один проход в `_campaign_to_response`), нейминг — следовать стилю существующих computed-полей в `campaigns.py` / `CampaignResponse`.

### UI scope (HLTH-03, часть 2)
- **D-11:** В этой фазе: полный бэкенд (event-log + срез + эндпоинт истории по аккаунту) + **мини-UI** — простой список событий ограничений на странице аккаунта + бейдж пула на странице кампании. **Агрегат-дашборд (графики флуда по дням, % пула под ограничением) → backlog.** Данные начинают копиться сразу, богатый UI строится поверх позже.

### Claude's Discretion
- Схема таблицы event-log (имя, колонки vs JSONB для среза, индексы), миграция (raw SQL `NNN_*.sql`, идемпотентная — D-04/D-07).
- Точные SQL-окна среза, форма «фактического темпа» (D-07).
- Имена/форма `pool_health` и категории событий (D-04, D-10).
- Транзакционные границы записи события (внутри того же UPDATE, что меняет `restriction_status`, чтобы событие и состояние не разъезжались).
- Деривация требований фазы (pool-visibility reqs, помечены TBD в ROADMAP) — на research/plan, как для фаз 7/8/9.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Мотивация фазы / дизайн (главный источник)
- `.planning/notes/account-restriction-audit-gap.md` — снимок 2026-06-24: почему истории restriction нет (где что хранится и почему затирается), список restriction-типов, **важная оговорка про `PRIVACY_PREMIUM_REQUIRED` ≠ флуд** (основа D-03). Главный источник для HLTH-01..03.
- `.planning/proposals/sender-pool-resilience.md` — общий дизайн блока Sender Pool Resilience (фазы 7/8/9); §«Finalised freeze policy» = lifecycle restriction-статусов, на сменах которого строится event-log.

### Roadmap / requirements
- `.planning/ROADMAP.md` §«Phase 10: Pool Visibility & Restriction Audit» (~строка 255) — goal обоих направлений, **Non-goals блока** (~строка 266), depends on Phase 8 (пул) + Phase 7 (restriction lifecycle).
- `.planning/REQUIREMENTS.md` — HLTH-01 (event-log, строка 132), HLTH-02 (срез активности, 133), HLTH-03 (видимость + агрегат, 134).

### Прошлые фазы (источник событий и паттернов)
- `.planning/phases/09-cold-contact-failover/09-CONTEXT.md` — D-12 (failover уже логирует «что перенесено» без durable-хранилища; Phase 10 даёт хранилище). Точки фриза D-02 = те же точки, где надо писать restriction-события.
- `.planning/phases/08-pool-management-and-even-distribution/08-CONTEXT.md` — `attached_senders[]` / `_build_attached_senders` / `_campaign_to_response` (D-08 обогащает их), паттерн computed-полей в ответе кампании.

### Backend код (reuse / точки врезки)
- `app/models/__init__.py` — `Sender` (L73): `restriction_status` (L93), `restricted_until` (L94), `proxy` (L87), `last_used_at`. `MessageLog`/`messages_log` (L108: `sender`, `recipient_phone`, `message_type`, ts — источник среза D-06). Сюда добавляется новая ORM-модель event-log.
- `app/services/queue.py` — PEER_FLOOD блок (~L733), ACCOUNT_FROZEN блок (~L776): UPDATE `restriction_status` — **точки записи** restriction-события (D-01/D-02, source=`queue_error`). Не-restriction privacy-ошибки тоже всплывают здесь (D-03).
- `app/services/listener.py` — restriction-reconcile loop (~L1352-1449, авто-resume по `restricted_until`): источник событий `cleared` и **продление срока** (D-01, source=`spambot_reconcile`); `_handle_antispam_signal` (~L881) — antispam-путь.
- `app/routers/campaigns.py` — `_campaign_to_response` (L228), `_build_attached_senders` (L194): сюда добавляется `pool_health` + обогащение `attached_senders[]` (D-08/D-10).
- `app/schemas/__init__.py` — `CampaignSenderAttach` (L574), `CampaignResponse` (L685, computed-поля `is_exhausted`/`attached_senders` L726): расширить под D-08.

### Кодовая карта
- `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INTEGRATIONS.md` — структура двух репо (backend + frontend `aimly-tg-outreach`) как единой системы.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `Sender.restriction_status` / `restricted_until` / `proxy` — текущее состояние; источник для агрегата `pool_health` и для записи `restricted_until` в событие.
- `messages_log` (sender_id + recipient_phone + message_type + ts) — источник для среза активности (отправки 1ч/24ч, уникальные контакты, темп — D-06).
- `_campaign_to_response` / `_build_attached_senders` (campaigns.py) — готовая сборка ответа кампании; расширяется computed-полем `pool_health` и restriction-полями в `attached_senders[]`.
- Точки UPDATE `restriction_status` (queue.py PEER_FLOOD/ACCOUNT_FROZEN, listener antispam + reconcile) — естественные хуки записи событий; те же, что фаза 9 использует для failover.

### Established Patterns
- Миграции: raw SQL `migrations/NNN_short.sql`, идемпотентные (`IF NOT EXISTS`), авто-applier при старте api (`app/database.py::_apply_migrations`). Новая таблица event-log = одна такая миграция.
- Computed-поля в `CampaignResponse` (`is_exhausted`, `attached_senders`) — образец для `pool_health` (D-08/D-10).
- `restriction_status` ортогонален `auth_status` (spam-limited аккаунт всё ещё аутентифицируется) — event-log про restriction, не про auth.
- Append-only durable лог поверх эфемерных источников — суть HLTH-01 (контраст с `message_queue.error_message`, который затирается на reschedule).

### Integration Points
- Новая ORM-модель + миграция (event-log таблица).
- Запись события (+ снапшот среза) в точках смены `restriction_status` / продления срока (queue.py, listener.py reconcile) — синхронно с UPDATE статуса.
- Расширение `CampaignResponse` (`pool_health` + restriction-поля в `attached_senders[]`) в `campaigns.py` + `schemas`.
- Новый read-эндпоинт «история событий по sender_id».
- Фронт (`aimly-tg-outreach`): бейдж пула на странице кампании (3 состояния) + мини-список событий на странице аккаунта. Возможный апдейт `lovable-handoff/openapi.json`.

</code_context>

<specifics>
## Specific Ideas

- Триггер фазы — реальный инцидент (заметка 2026-06-24): 2 аккаунта под `spam_limited`, reconcile отработал 37 раз за сутки все «extended», и восстановить «как часто за месяц ловили PEER_FLOOD» **невозможно** по текущим данным. Фаза закрывает именно эту слепую зону.
- «Частичная пауза незаметна» — главный UX-сигнал бейджа: жёлтое состояние (K из N на паузе) важнее, чем бинарное «ок/не-ок».
- Срез активности — самое ценное: цель буквально «что делали → за что получили», поэтому снапшот обязателен при записи (D-05).

</specifics>

<deferred>
## Deferred Ideas

- **Агрегат-дашборд restriction** (флуд/ограничения по дням, графики, % пула под ограничением во времени, тренды) → backlog. Строится поверх накопленного event-log позже (D-11).
- **Real-time алерты по банам/ограничениям** — non-goal блока; аудит копит данные, алерты — отдельная будущая работа (ROADMAP §Phase 10 Non-goals).
- **Расширенная корреляция прокси↔баны** (агрегаты по прокси) — данные снимаются (D-06.3), но аналитика поверх — backlog вместе с дашбордом.

None beyond the above — discussion stayed within phase scope.

</deferred>

---

*Phase: 10-pool-visibility*
*Context gathered: 2026-06-24*
