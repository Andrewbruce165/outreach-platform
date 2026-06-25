# Phase 12: Per-campaign daily new-dialog limit (max_new_dialogs_per_day) - Context

**Gathered:** 2026-06-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Ввести явный настраиваемый дневной лимит **новых холодных диалогов** на уровне кампании
(`campaigns.max_new_dialogs_per_day`, default 50). Enforcement в queue-воркере: при
достижении лимита sender перестаёт открывать новые диалоги в этой кампании, но
follow-ups существующим контактам продолжают идти. API/UI: поле в Create/Update/Response
с soft-cap warning (>50) и hard-cap 422 (>100), по паттерну D-14.

Вне scope: изменение per-sender лимитов (4/20/150), `MAX_NEW_CONTACTS_PER_HOUR=15`,
любых эмпирических констант (CLAUDE.md guard). Аналитика/дашборд по новым диалогам — не сюда.
</domain>

<decisions>
## Implementation Decisions

### Counting scope (счётчик)
- **D-01:** Лимит считается **per-sender в рамках кампании**, НЕ campaign-wide. Каждый
  аккаунт в кампании может открыть до `max_new_dialogs_per_day` новых диалогов/сутки.
  Согласуется с принципом «rate limits per-sender» (Telegram anti-spam смотрит на аккаунт)
  и индустриальным порогом 50/сутки **на аккаунт**. Кампания-wide потолок = `limit × N`
  аккаунтов — это осознанное следствие, не баг.
- **D-02:** Счётный ключ — `(sender_id, campaign_id)`. SQL считает уникальные новые
  `recipient_phone`, открытые этим sender'ом в этой кампании за trailing-24h.

### "Новый диалог" — определение и дедуп
- **D-03:** Новый диалог = в `message_queue` НЕТ предыдущей строки `status='sent'` к этому
  `recipient_phone` **в рамках ЭТОЙ кампании** (scope = `campaign_id`, не workspace-wide).
- **D-04:** Считаем **только `status='sent'`** (фактически отправленные). Pending/processing
  (in-flight) НЕ учитываются в счётчике «уже открытых» — лимит отражает реально открытые диалоги.
- **D-05:** Окно — **trailing-24h rolling** (`finished_at >= NOW() - INTERVAL '24 hours'`),
  как существующий per-day cap (`one_day_ago`), НЕ календарный день с полночным reset.
- **D-06:** allow_recontact-перезаливки к телефону, у которого уже есть прошлый `sent` в
  этой кампании, классифицируются как **follow-up** (не новый диалог) → не считаются и не
  блокируются по этому лимиту.

### Enforcement (как НЕ блокировать follow-ups)
- **D-07:** Проверка НЕ в `_check_rate_limits` (он per-tick → `return False` → пропускает
  весь tick, включая follow-ups). Вместо этого — **per-item фильтр в выборке элемента** в
  `_process_next_for_sender` ([queue.py:273-339](app/services/queue.py#L273-L339)).
- **D-08:** При достижении лимита для `(sender, campaign)` — из кандидатов LIMIT 8
  **исключаются новые-диалоговые элементы** этой кампании (через `NOT EXISTS` prior-sent
  предикат + per-campaign счётчик), а follow-up/re-contact элементы остаются eligible и
  реально отправляются. Это честно реализует обещание роадмапа «фоллоу-апы не блокируются».
- **D-09:** Per-sender лимиты 4/20/150 + `MAX_NEW_CONTACTS_PER_HOUR=15` в `_check_rate_limits`
  **остаются нетронутыми** на своём месте (CLAUDE.md guard). Новый лимит — отдельный механизм
  в выборке элемента, НЕ добавляется в `_check_rate_limits`.

### Schema / migration
- **D-10:** `campaigns.max_new_dialogs_per_day INT NOT NULL DEFAULT 50`. Миграция
  `033_*.sql` (033 — следующий свободный слот после 032), идемпотентная
  (`ADD COLUMN IF NOT EXISTS`), авто-применяется через `_apply_migrations`. Добавить колонку
  в ORM `Campaign` ([models/__init__.py:503](app/models/__init__.py#L503)) с
  `server_default="50"`.
- **D-11:** DEFAULT 50 применяется ко **ВСЕМ существующим кампаниям, включая `running`**
  (как в acceptance). Существующие горячие кампании могут притормозиться по новому лимиту —
  это и есть цель фичи (снизить риск спам-бана). Backfill повышенным значением НЕ делаем.

### API (D-14 pattern)
- **D-12:** `max_new_dialogs_per_day: int = Field(ge=1, le=100)` в `CampaignCreate` /
  `CampaignUpdate` (default 50) и в `CampaignResponse`.
- **D-13:** Soft-cap = 50, hard-cap = 100 (верх «прогретого» диапазона). Значение >100 → 422
  (паттерн `RATE_LIMIT_EXCEEDS_HARD_CAP` из [senders.py:154-163](app/routers/senders.py#L154-L163)).
  Значение >50 и ≤100 → **не блокировать**, вернуть `warnings[]` (`WarningItem` /
  `RATE_SOFT_CAP`-паттерн). Зелёный коридор: ≤50.
- **D-14:** Create/Update сейчас возвращают `CampaignResponse` напрямую (без warnings).
  Нужно вернуть warnings на **write-путях** (POST/PATCH): добавить `warnings: List[WarningItem]`
  в write-response (по образцу sender create/update response,
  [schemas/__init__.py:159-161](app/schemas/__init__.py#L159-L161)). GET-путь warnings НЕ несёт.
  Re-валидация warning происходит и на PATCH при изменении поля.
- **D-15:** Обновить `lovable-handoff/openapi.json` + регенерировать типы (через export-handoff,
  rebuild API container first — как в Phase 10/11), без ручного редактирования спеки.

### Claude's Discretion
- Точная формулировка warning-message и green-corridor copy (рус.) — придерживаться тона
  существующих сообщений senders D-14.
- Конкретный SQL-shape per-item фильтра (correlated subquery vs CTE vs window-count) — на
  усмотрение планнера, лишь бы держал LIMIT 8 / SKIP LOCKED семантику и не ломал per-campaign
  working-window re-check (D-15 Phase 4).
- Где разместить порог-константы (модульные dict как `RATE_SOFT_CAP` vs inline).

### UI-контракт (UI-SPEC)
- **D-16:** Поле `max_new_dialogs_per_day` в форме настроек кампании, дефолт 50, inline-
  предупреждение при значении >50 («рекомендуем не больше 50 новых диалогов в сутки на
  аккаунт — выше растёт риск спам-бана»). Это per-sender семантика (D-01) — формулировка
  должна сказать «на аккаунт», не «на кампанию», чтобы не вводить в заблуждение.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase spec
- `.planning/ROADMAP.md` §"Phase 12: Per-campaign daily new-dialog limit" — полный scope/acceptance.

### D-14 warning/cap pattern (источник, который этот phase зеркалит)
- `app/routers/senders.py:61-63` — `RATE_HARD_CAP` / `RATE_SOFT_CAP` dict-константы.
- `app/routers/senders.py:135-175` — `_validate_rate_limits`: soft → `warnings[]`, hard → 422.
- `app/schemas/__init__.py:82` — `class WarningItem`.
- `app/schemas/__init__.py:159-161` — sender write-response с `warnings: List[WarningItem]` (образец для D-14 кампании).

### Enforcement integration points
- `app/services/queue.py:273-339` — `_process_next_for_sender` item-selection (сюда добавляется per-item фильтр, D-07/D-08).
- `app/services/queue.py:363-475` — `_check_rate_limits` (per-sender 4/20/150 + 15/hour, НЕ трогать — D-09).
- `app/services/queue.py:449-461` — существующий `COUNT(DISTINCT recipient_phone)` за 1ч — паттерн для нового COUNT за 24ч.
- `app/services/campaign_enqueue.py:205-320` — enqueue-воркер: dedup по conversations (контекст «почти всё в очереди — холодные»).

### Schema / model
- `app/models/__init__.py:503-575` — ORM `Campaign` (добавить колонку).
- `migrations/032_phase11_field_split.sql` — последняя миграция; следующий слот 033.
- `app/schemas/__init__.py:630-750` — `CampaignCreate` / `CampaignUpdate` / `CampaignResponse`.
- `app/routers/campaigns.py:350` / `:527` — `create_campaign` / `patch_campaign` (где вернуть warnings).

Внешних ADR/SPEC-документов у проекта для этой фазы нет — требования полностью в ROADMAP §Phase 12 + решениях выше.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **D-14 soft/hard-cap машинерия** (`RATE_SOFT_CAP`/`RATE_HARD_CAP`/`WarningItem`/`_validate_rate_limits`
  в senders.py) — прямой образец для нового лимита; не изобретать заново.
- **`COUNT(DISTINCT recipient_phone)` за trailing-window** уже есть в `_check_rate_limits`
  (queue.py:449) — паттерн для счётчика новых диалогов за 24ч.
- **Sender write-response с `warnings[]`** (schemas:159) — образец расширения CampaignResponse на write-пути.
- **export-handoff flow** (Phase 10/11) — регенерация openapi.json + типов без ручного редактирования.

### Established Patterns
- Item-selection в `_process_next_for_sender` уже делает JOIN campaigns + post-filter по
  working-window (Phase 4 D-15) и `FOR UPDATE OF mq SKIP LOCKED` LIMIT 8 — новый фильтр
  должен встроиться сюда, не сломав эти инварианты.
- `_check_rate_limits` — per-tick gate (`return False` → весь tick пропущен). Поэтому новый
  лимит сознательно НЕ туда (D-07), иначе блокировал бы follow-ups.
- Миграции: raw SQL `NNN_*.sql`, идемпотентные, авто-применяются; ORM `server_default` дублирует
  DB-default для create_all-пути после DROP-инцидента.

### Integration Points
- `migrations/033_*.sql` — новая колонка.
- `app/models/__init__.py` Campaign — ORM-колонка.
- `app/schemas/__init__.py` — три Campaign-схемы + write-response с warnings.
- `app/routers/campaigns.py` create/patch — валидация + возврат warnings.
- `app/services/queue.py` `_process_next_for_sender` — enforcement.
- `lovable-handoff/openapi.json` + типы — регенерация.
- Frontend (sibling `aimly-tg-outreach`) — поле в форме кампании + inline warning (cross-repo, human-UAT).

### Enqueue context (важно для определения «нового диалога»)
- `CampaignEnqueueWorker` кладёт в `message_queue` только **холодные первые касания** (дедуп по
  `conversations`). AI-ответы (follow-ups диалога) уходят напрямую через listener, НЕ через очередь.
  Поэтому follow-ups в очереди появляются в основном через allow_recontact-перезаливки — это и есть
  класс элементов, который D-08 должен оставлять eligible при достигнутом лимите.
</code_context>

<specifics>
## Specific Ideas

- «Зелёный коридор ≤50» — формулировка предупреждения должна явно говорить «на аккаунт»
  (per-sender семантика D-01), чтобы пользователь не считал, что 50 — это потолок всей кампании.
- Hard-cap 100 = верх «прогретого» диапазона из индустрии (а не произвольное число).
</specifics>

<deferred>
## Deferred Ideas

- **Equal pacing по окну рассылки → Phase 13** (добавлена в ROADMAP, depends on Phase 12).
  Распределять дневной лимит равномерно по активному окну (`max_new_dialogs_per_day / активные_часы
  → целевой интервал`), батчинг пула, 1 новый диалог каждые 3–5 мин с плавающими интервалами.
  Phase 12 даёт жёсткий потолок (вход для Phase 13); сглаживание под потолок — отдельный механизм,
  трогает защищённые эмпирические константы (`MIN/MAX_SEND_INTERVAL`, `LONG_PAUSE_*`).
- Аналитика/дашборд «сколько новых диалогов в день на кампанию/аккаунт» — отдельная фича, не сюда.
- Campaign-wide агрегатный потолок (в дополнение к per-sender) — если понадобится, отдельная фаза.
- Календарный-день reset (вместо rolling-24h) — отклонён в пользу консистентности с существующими лимитами.

None beyond the above — discussion stayed within phase scope.
</deferred>

---

*Phase: 12-per-campaign-daily-new-dialog-limit-max-new-dialogs-per-day*
*Context gathered: 2026-06-25*
