# Phase 15: Account Warmup via Inter-Account AI Chat - Context

**Gathered:** 2026-06-29
**Status:** Ready for planning

<domain>
## Phase Boundary

Продуктизировать готовый движок взаимного AI-прогрева: аккаунты одного workspace переписываются между собой через AI, чтобы безопасно набирать «возраст»/активность без риска бана. Делаем: (1) **железобетонную изоляцию** прогрева от основного флоу аутрича (не триггерить AI-ответчик, не пачкать аналитику, не жечь лимиты sender'ов — именно отсутствие изоляции убило фичу в старой `telegram-api`); (2) **workspace-scoped API** (рерайт с `verify_api_key` на `AuthDep`); (3) **отдельную UI-вкладку** (master старт/стоп + per-account, статус каждого аккаунта).

Движок (`app/services/warmup.py`) и таблицы (`migrations/005_warmup.sql`) уже есть и работают — фаза НЕ переписывает механику диалогов, а оборачивает её в продукт + закрывает дыры безопасности.

**Вне scope:** настраиваемое окно расписания/таймзона (оставляем 09–20 МСК, → backlog); ручной слом кривой интенсивности (авто по дням остаётся); биллинг; многоязычный UI самой вкладки. Observability/алерты на здоровье прогрева — отдельная фаза при необходимости.

</domain>

<decisions>
## Implementation Decisions

### Изоляция от аутрича (главная развилка — что убило старую фичу)
- **D-01:** Главный признак изоляции — **«свой со своим» = internal**. Любой Telegram-трафик между двумя нашими senders ОДНОГО workspace (по `telegram_id ∈ senders` этого workspace) считается internal — НЕ зависит от `phone` (закрывает leak при `phone="unknown"`) и НЕ зависит от членства в `warmup_pool`. Тот же признак уже используется в аналитике (`_EXCLUDE_INTERNAL_CLAUSE`). (заменяет нынешнюю phone/pool-cache-зависимость в листенере)
- **D-02:** Листенер при детекте internal-трафика **дропает его до AI** (никогда не вызывает `schedule_ai_response`) и **не создаёт строк в `conversations`/`messages`**. Весь warmup живёт ТОЛЬКО в таблицах `warmup_*` — чисто на записи, без фильтрации на чтении. (закрывает корневую причину pollution из `dashboard-analytics-warmup-pollution.md`)
- **D-03:** Лимиты **независимые**. Warmup шлёт напрямую через Telethon, минуя `message_queue` → не жжёт rate-limits кампаний (4/20/150). Дневные warmup-лимиты по уровням остаются отдельными. (риск суммарного объёма «кампания + прогрев» в один день осознан и принят — см. D-09; верхний warmup-уровень капается 120/день)
- **D-04:** **Регресс-тест-гард** изоляции (обязателен) — доказывает, что internal-трафик не триггерит AI-ответчик и не попадает в метрики аналитики. Аналог source-introspection гардов из Phase 13: регрессия pollution больше не вернётся.

### Workspace-scoping / API
- **D-05:** Рерайт всех эндпоинтов `/api/v1/warmup` с `verify_api_key` на **`AuthDep` + workspace scope** (паттерн Phase 3/4/5): все запросы фильтруются по `workspace_id` из токена. Форма ответов существующих эндпоинтов (`/pool`, `/stats`, `/sessions`, ...) сохраняется; добавляем только нужные control-эндпоинты.
- **D-06:** Старт/стоп прогрева = **`warmup_enabled` флаг per-workspace** в БД (НЕ перезапуск процесса). `WarmupWorker` остаётся глобальным singleton-тиком, но на каждом тике читает флаг и **пропускает workspace с выключенным прогревом**.

### Контролы UI-вкладки
- **D-07:** Гранулярность — **master + per-account**: одна кнопка «прогрев вкл/выкл» на workspace (D-06 флаг) + существующий per-account toggle в пуле (добавить/убрать/пауза конкретного аккаунта — endpoints уже есть).
- **D-08:** Расписание — **оставить 09–20 МСК** (захардкожено в воркере). Настраиваемое окно/TZ → deferred.
- **D-09:** Интенсивность — **авто по дням** (`LEVEL_CONFIG`, 5 уровней, 5→120 msg/день). UI показывает текущий уровень/прогресс, но НЕ даёт ломать безопасную кривую руками (без ручного уровня и без пресета slow/normal/fast в v1).
- **D-10:** Контент прогрева — **настраиваемый per-workspace** (темы / язык / тон). Сейчас захардкожены 24 RU-темы + `WARMUP_SYSTEM_PROMPT`. Хранить per-workspace вместе с `warmup_enabled` (единый объект настроек прогрева workspace, напр. таблица/строка `warmup_settings`). Дефолт = текущие 24 RU-темы + промпт, чтобы существующее поведение не сломалось при пустых настройках.
- **D-11:** Per-account статус в вкладке — **расширенный**: на базе `/pool`+`/stats` (уровень, `sent_today`, `enrolled_days`, активен/пауза) + **ДОБАВИТЬ** `restriction_status` и последнюю ошибку/активность прогрева, чтобы было видно, ПОЧЕМУ аккаунт не греется.

### Пул и совмещение с кампаниями
- **D-12:** Совмещение **разрешено** — аккаунт может одновременно греться и быть в активной кампании (согласуется с D-03; активный аккаунт выглядит живым). Авто-паузы прогрева при работе в кампании нет.
- **D-13:** Новые аккаунты — **ручное зачисление** в пул (НЕ авто-enroll при онбординге). Предсказуемо, без сюрпризов с неожиданным трафиком.
- **D-14:** **ДЫРА БЕЗОПАСНОСТИ (закрыть):** текущая выборка пула (`_get_active_pool`) фильтрует `lifecycle_status='active' AND auth_status='ok'`, но **НЕ смотрит `restriction_status`** → аккаунт с `spam_limited`/`frozen` продолжает греться. Добавить в выборку пропуск аккаунтов с `restriction_status != 'none'` ИЛИ `restricted_until` в будущем (тот же паттерн, что Phase 14 закрыл для чекеров, RESV-05).

### Claude's Discretion
- Точная схема хранения `warmup_settings` workspace (новая таблица vs строка vs JSONB-колонка) — реализация D-06/D-10.
- Форма control-эндпоинтов (master toggle, обновление настроек) и их имена в пределах паттерна Phase 3/4/5 — D-05.
- Где именно в `listener.py` ставить internal-short-circuit (до буфера/дебаунса), при сохранении симметрии для incoming и outgoing — D-01/D-02.
- Набор полей и формат «последней ошибки/активности» прогрева для D-11.

### Производный набор требований (WARM-XX — внести в REQUIREMENTS.md при планировании)
По паттерну прошлых фаз («derived this phase, see 15-CONTEXT.md decisions»):
- **WARM-01:** Internal-детекция «свой со своим» по `telegram_id ∈ senders` workspace; листенер дропает до AI, не зависит от phone/членства в пуле (D-01).
- **WARM-02:** Internal-трафик не создаёт строк в `conversations`/`messages`; warmup только в `warmup_*` (D-02). Аналитика остаётся чистой (`_EXCLUDE_INTERNAL_CLAUSE` сохранить).
- **WARM-03:** Warmup-лимиты независимы от rate-limits кампаний; отправка минует `message_queue` (D-03).
- **WARM-04:** Регресс-тест-гард: internal не триггерит AI и не попадает в метрики (D-04).
- **WARM-05:** Все `/api/v1/warmup` под `AuthDep` + workspace scope (D-05).
- **WARM-06:** `warmup_enabled` per-workspace; глобальный воркер honors флаг (D-06).
- **WARM-07:** UI master toggle + per-account enroll/toggle (D-07).
- **WARM-08:** Расписание 09–20 МСК без UI-настройки (D-08).
- **WARM-09:** Интенсивность авто по дням; UI read-only уровень/прогресс (D-09).
- **WARM-10:** Per-workspace настройки контента прогрева (темы/язык/тон) с дефолтом = текущие 24 RU-темы + промпт (D-10).
- **WARM-11:** Расширенный per-account статус (+`restriction_status`, +последняя ошибка/активность) (D-11).
- **WARM-12:** Совмещение прогрева с активной кампанией разрешено (D-12).
- **WARM-13:** Новые аккаунты не авто-зачисляются в пул (D-13).
- **WARM-14:** Выборка пула пропускает аккаунты с `restriction_status != 'none'`/`restricted_until` в будущем (D-14).
- **WARM-15:** Изучить старую `telegram-api` warmup как референс и зафиксировать, почему она конфликтовала (изоляция).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Почему фича раньше конфликтовала (читать первым)
- `.planning/debug/dashboard-analytics-warmup-pollution.md` — полный разбор pollution: листенер aimly ловил warmup-трафик между общими 13 аккаунтами → 5382 фейковых «sent»; phone-фильтр листенера течёт при `phone="unknown"`; уже применённый фикс (`_EXCLUDE_INTERNAL_CLAUSE` в analytics + telegram_id-фильтр в листенере). Основа для D-01/D-02.
- `/root/CLAUDE.md` (корневой) §Telegram API — почему старые `telegram-api`/`outreach-platform` остановлены: те же 13 аккаунтов, листенеры перехватывают друг у друга.

### Код, который правим/оборачиваем
- `app/services/warmup.py` — движок full-mesh AI-прогрева: `WarmupWorker._tick`, `_get_active_pool` (выборка пула — точка фикса D-14), `_is_working_hours` (09–20 МСК, D-08), `LEVEL_CONFIG` (D-09), `WARMUP_TOPICS`/`WARMUP_SYSTEM_PROMPT` (D-10), `_send_via_telethon` (прямая отправка, минует очередь — D-03).
- `app/routers/warmup.py` — CRUD пула / stats / sessions; сейчас на `verify_api_key` → рерайт под `AuthDep` (D-05), добавить master toggle + settings (D-06/D-07/D-10).
- `app/services/listener.py` — warmup-фильтры (`_refresh_warmup_cache`, `_get_warmup_telegram_ids/_phones`, internal-skip на ~681/1138); точка установки детерминированного internal-short-circuit (D-01/D-02). `WARMUP_CACHE_TTL`.
- `app/routers/analytics.py` — `_EXCLUDE_INTERNAL_CLAUSE` (internal-трафик исключён из метрик/funnel) — сохранить, не сломать (D-02/D-04).
- `migrations/005_warmup.sql` — таблицы `warmup_pool` / `warmup_sessions` / `warmup_messages` (есть `workspace_id` после mig 012, partition по workspace).

### Референс (изучить, не запускать)
- `/root/apps/telegram-api/app/services/warmup.py`, `/root/apps/telegram-api/app/routers/warmup.py`, `/root/apps/telegram-api/app/services/bot_chat.py` — прототип остановленной фичи; понять, почему конфликтовала с основным флоу (WARM-15). **НЕ запускать `telegram-api` — конфликт сессий.**

### Паттерны мультитенантности / переиспользуемая инфраструктура
- `.planning/phases/04-*/04-CONTEXT.md` (или `app/routers/campaigns.py`) — паттерн per-campaign `zoneinfo.ZoneInfo` + `work_hour_start/end` (для будущей настройки расписания, deferred).
- Phase 10/14 — `restriction_status` / `restricted_until` / `sender_restriction_events`; паттерн пропуска ограниченных аккаунтов в выборке (RESV-05) — основа D-14.
- `.planning/REQUIREMENTS.md` — конвенция derived-блоков фазы (см. RESV/PACE/NDLG) для внесения WARM-01..15.
- `.planning/ROADMAP.md` §«Phase 15» — Goal + контекстный блок (код-референсы).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `WarmupWorker` целиком — движок диалогов, уровни, pacing-гард, FloodWait-обработка уже работают; фаза оборачивает, а не переписывает.
- Существующие эндпоинты `/pool`, `/stats`, `/sessions`, `/sessions/{id}/messages` — основа UI-вкладки (D-07/D-11), нужен лишь рерайт scope (D-05) + расширение статуса.
- `_EXCLUDE_INTERNAL_CLAUSE` (analytics) + internal-фильтры листенера — частично уже реализуют D-01; фаза делает их детерминированными и единообразными.
- `restriction_status`/`restricted_until` + `senders.role='sender'` + lifecycle-поля (Phase 10/14) — готовы для D-14.

### Established Patterns
- `AuthDep` + workspace-scoped запросы (Phase 3/4/5) — D-05.
- Миграции raw SQL `NNN_short_name.sql`, идемпотентные, авто-применяются на старте api — для `warmup_settings`/`warmup_enabled`.
- Глобальные background-воркеры (`QueueWorker`, `CampaignEnqueueWorker`, `WarmupWorker`) читают БД-флаги на тике — паттерн для D-06.
- Env-knobs через `app/config.py`.

### Integration Points
- `listener.py` incoming/outgoing хендлеры — точка internal-short-circuit (D-01/D-02), симметрично для обеих сторон.
- `senders.telegram_id` (сохраняется в БД, listener.py:1271) — источник истины для «свой со своим» (D-01).
- `warmup_pool` выборка ↔ `senders` lifecycle/restriction поля (D-14).
- Workspace-настройки (`warmup_enabled`, контент) ↔ `WarmupWorker._tick` / `_get_active_pool` / генерация сообщений (D-06/D-10).

</code_context>

<specifics>
## Specific Ideas

- Пользователь явно хочет, чтобы было видно **почему аккаунт не греется** (D-11) — restriction + последняя ошибка в статусе аккаунта.
- Изоляция должна быть детерминированной и доказуемой тестом (D-04), а не «надеемся, что кэш свежий» — прямой урок из инцидента 2026-06-23/24.
- Контент прогрева настраиваемый per-workspace (D-10) — заложить на случай нерусскоязычных клиентов, но с дефолтом = текущие RU-темы.

</specifics>

<deferred>
## Deferred Ideas

- **Настраиваемое окно расписания + таймзона прогрева per-workspace** — оставили 09–20 МСК (D-08); переиспользовать паттерн `work_hour_start/end` + `ZoneInfo` из Phase 4 при запросе. → backlog.
- **Пресет интенсивности slow/normal/fast или ручной уровень** — отклонено для v1 (D-09), авто-кривая безопаснее. → при запросе.
- **Observability / алерты на здоровье прогрева** (rate деградации, % пула под ограничением, FloodWait-тренды) — отдельная фаза при необходимости.
- **Auto-pause прогрева при активной кампании** — отклонено (D-12, совмещение разрешено); вернуться, если суммарный объём окажется рискованным на практике.
- **Многоязычный UI самой вкладки** — вне scope (контент диалогов настраиваемый D-10, но интерфейс — нет).

None beyond the above — discussion stayed within phase scope.

</deferred>

---

*Phase: 15-account-warmup-via-inter-account-ai-chat*
*Context gathered: 2026-06-29*
