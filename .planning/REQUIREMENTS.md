# Requirements: Outreach Platform

**Defined:** 2026-04-02
**Revised:** 2026-05-21 — Campaign entity added, scope restructured into 6 phases
**Core Value:** Клиент подключил аккаунт и через 10 минут первая кампания запущена — без программистов, без DevOps, без настройки серверов.

## v1 Requirements

### Multitenancy (Phase 1)

- [ ] **TENT-01**: Все сущности (senders, agents, contacts, folders, campaigns, queue, conversations) изолированы по workspace_id
- [ ] **TENT-02**: Workspace создаётся автоматически при регистрации
- [ ] **TENT-03**: Workspace имеет уникальный API-ключ для интеграций (n8n и др.)
- [ ] **TENT-04**: Запросы к API без валидного workspace-контекста отклоняются (403)

### Authentication (Phase 1)

- [ ] **AUTH-01**: Пользователь вводит email → получает magic link на почту (Supabase Auth)
- [ ] **AUTH-02**: Переход по magic link создаёт JWT-сессию (Supabase)
- [ ] **AUTH-03**: FastAPI верифицирует Supabase JWT и извлекает workspace_id
- [ ] **AUTH-04**: Сессия сохраняется через browser refresh

### TG Account Onboarding (Phase 2)

- [x] **ONBD-01**: Пользователь добавляет Telegram-аккаунт через телефон + SMS-код
- [x] **ONBD-02**: Поддерживается 2FA (пароль Telegram) при онбординге
- [x] **ONBD-03**: Поддерживается QR-вход как альтернатива SMS
- [x] **ONBD-04**: Добавленный аккаунт привязан к workspace пользователя
- [x] **ONBD-05**: Пользователь видит список своих аккаунтов со статусом

### TG Account Settings (Phase 2)

- [x] **SNDR-01**: Per-account rate limits: сообщений в минуту / час / день (с предупреждением при выходе за рекомендованный «зелёный коридор» 4/20/150)
- [x] **SNDR-02**: Per-account прокси (или выбор из workspace-пула)
- [x] **SNDR-03**: Статус аккаунта: активен / прогрев / пауза / ошибка

### Contacts (Phase 2)

- [x] **CONT-01**: Пользователь загружает CSV с полями: телефон (обязательно), имя, компания, любые переменные
- [x] **CONT-02**: Загруженные контакты привязаны к workspace
- [x] **CONT-03**: Push-контакты через Workspace API (POST /api/v1/contacts)
- [x] **CONT-04**: При добавлении контакта проверяется наличие в Telegram через checker-аккаунт; результат сохраняется в поле статуса
- [x] **CONT-05**: Поля контакта: `phone, username, full_name, source, custom (JSONB)` — `custom` для произвольных переменных подстановки

### Contact Folders (Phase 2)

- [x] **FLDR-01**: Контакты группируются по папкам внутри workspace; каждый контакт принадлежит одной папке
- [x] **FLDR-02**: Пользователь создаёт / переименовывает / удаляет папки
- [x] **FLDR-03**: При импорте CSV или push через API выбирается целевая папка (создаётся если не существует)

### Agents — AI Templates (Phase 3)

- [x] **AGNT-01**: Пользователь создаёт агента (AI-шаблон) с именем — workspace-level
- [x] **AGNT-02**: Задаёт настройки агента: контекст (промпт), задача, тон, FAQ
- [x] **AGNT-03**: Агент переиспользуется между несколькими кампаниями
- [x] **AGNT-04**: Список агентов workspace с CRUD (создать / редактировать / удалить, дубликат)

### Campaigns (Phase 4)

- [x] **CAMP-01**: Создание кампании с именем и описанием
- [x] **CAMP-02**: Выбор агента-шаблона из списка workspace
- [x] **CAMP-03**: Выбор папки контактов как таргета кампании
- [x] **CAMP-04**: Выбор TG-аккаунтов (senders) — с каких аккаунтов идёт рассылка; sender блокируется за активной кампанией
- [x] **CAMP-05**: Расписание кампании: рабочие часы и дни (заменяет глобальные 09–20 МСК)
- [x] **CAMP-06**: Старт и стоп даты кампании (опционально)
- [x] **CAMP-07**: Статусы кампании: draft / running / paused / done
- [x] **CAMP-08**: Пользователь запускает / паузит / останавливает кампанию
- [x] **CAMP-09**: Контакты досыпаются в активную кампанию через папку (добавление в папку = добавление в очередь кампании)
- [x] **CAMP-10**: Переменные `{{имя}}, {{username}}, {{source}}, {{custom.X}}` подставляются из контакта в текст сообщения
- [x] **CAMP-11**: Сигнал «передать лид» — паттерн/фраза; срабатывание помечает диалог как лид и триггерит webhook
- [x] **CAMP-12**: Сигнал «передать на менеджера» — заменяет старый auto_pause_triggers, AI замолкает, диалог помечается
- [x] **CAMP-13**: Сигнал «финиш диалога» — диалог закрывается, AI замолкает, triggerит webhook
- [x] **CAMP-14**: Webhook кампании — 3 отдельных URL на типы событий: `lead_webhook_url`, `handoff_webhook_url`, `finish_webhook_url`. Любой может быть NULL (тогда событие не вызывает webhook, но `conversation.status` всё равно обновляется). Pre-Phase-4 формулировка «один webhook на кампанию» обновлена по итогам discuss-phase (D-13) — клиент предпочёл явное разделение endpoint'ов под разные интеграции
- [x] **CAMP-15**: Tools кампании — спецификация function calling, передаётся в LLM вместе с агентским промптом
- [x] **CAMP-16**: Сигналы + tools передаются в LLM-промпт вместе с агентским контекстом при каждом ответе
- [x] **CAMP-17**: Очередь сообщений учитывает `campaign_id` — каждое сообщение принадлежит кампании

### Inbox (Phase 5)

- [x] **INBX-01**: Пользователь видит все входящие диалоги своего workspace
- [x] **INBX-02**: В каждом диалоге видна история сообщений (исходящие + входящие)
- [x] **INBX-03**: Виден статус AI диалога: активен / пауза / режим менеджера / лид / финиш
- [x] **INBX-04**: Пользователь может вручную переключить диалог в режим менеджера (AI отключается для диалога)
- [x] **INBX-05**: Фильтр диалогов по кампании / агенту / TG-аккаунту

### AI Behavior Rules (Phase 5)

- [x] **AIRC-04**: AI не отвечает системным ботам (SpamBot и аналоги) — фильтр на listener'е

### Analytics (Phase 5)

- [x] **ANLX-01**: Метрики workspace: карточки отправлено / отвечено / лидов / финишей
- [x] **ANLX-02**: Метрики кампании: те же карточки в разрезе одной кампании
- [x] **ANLX-03**: Метрики TG-аккаунта (sender): отправлено / отвечено / ошибки в разрезе аккаунта
- [x] **ANLX-04**: Метрики агента: использование в кампаниях, агрегированные ответы / лиды
- [x] **ANLX-05**: Лог запросов в OpenAI на уровне диалога — какие промпты ушли и какие пришли ответы

### Admin Master Bot (Phase 6)

- [ ] **ADMN-01**: Пользователь регистрирует Telegram-чат как admin-канал workspace (бот workspace отправляет туда сообщения)
- [ ] **ADMN-02**: Бот шлёт уведомление при срабатывании сигнала «передать на менеджера» в любой активной кампании
- [ ] **ADMN-03**: Бот шлёт уведомление при ошибке TG-аккаунта (logout / FloodWait > threshold / etc.)

### Sender Pool Management (Phase 8)

- [x] **POOL-01**: `POST /campaigns/{id}/senders` attaches a sender to a draft/paused/running campaign (D-01)
- [x] **POOL-02**: Attach rejects a sender locked by another running campaign — 409 SENDER_LOCK_CONFLICT, same `conflicts[]` contract as `/start` (D-02)
- [x] **POOL-03**: Attach rejects a sender not owned by the workspace — 404 SENDER_NOT_FOUND (D-02)
- [x] **POOL-04**: `DELETE /campaigns/{id}/senders/{sid}` detaches a sender (D-01)
- [x] **POOL-05**: Detach of the last sender of a running campaign → 409 MIN_POOL_GUARD (D-03)
- [x] **POOL-06**: Detach blocked (409 DETACH_BLOCKED_PENDING) when the sender has un-sent cold pending in this campaign (D-04)
- [x] **POOL-06b**: Detach allowed when the sender's only remaining work is engaged dialogs — engaged dialogs do not block detach (D-05)
- [x] **POOL-07**: Light rebalance moves un-sent cold pending from overloaded senders onto a newly-attached sender on a running campaign, toward an even split (D-08/D-09)
- [x] **POOL-08**: Rebalance is idempotent (second call moves 0) and concurrency-safe under worker ticks (FOR UPDATE SKIP LOCKED + status='pending') (D-09)
- [x] **POOL-08b**: Rebalance never moves sent / processing / engaged-dialog rows; keeps campaign_contact_assignments in sync (D-08)
- [x] **POOL-09**: Frontend "Senders / Пул" panel — add/remove, locked-sender display, human-readable 409s (D-10/D-11/D-12)

### Sender Pool — Cold-Contact Failover (Phase 9)

- [ ] **FAIL-01**: On freeze, the frozen sender's cold-pending backlog is reassigned to healthy pool senders via per-item least-loaded pick, inline, with zero new worker (D-01/D-09)
- [ ] **FAIL-02**: Failover is invoked from ALL three freeze paths that pause pending — PEER_FLOOD, ACCOUNT_FROZEN, antispam-signal (D-02/D-07)
- [ ] **FAIL-03**: A queue row is movable iff `status='pending'` AND `item_type='message'` AND no `sent`/`processing` row for `(campaign_id, recipient_phone)` AND no started dialog — no conversation OR conversation with zero `messages` rows (D-04/D-05/D-06)
- [ ] **FAIL-04**: Moving a row updates `message_queue.sender_id` + `scheduled_at=NOW()` AND `campaign_contact_assignments.sender_id` in the SAME transaction (D-10)
- [ ] **FAIL-05**: Failover never moves engaged-dialog rows; engaged dialogs stay on the frozen sender and keep replying (D-04/D-08)
- [ ] **FAIL-06**: Idempotent and concurrency-safe under the parallel worker (`FOR UPDATE OF mq SKIP LOCKED` + `status='pending'` guard); second call moves 0 (discretion)
- [ ] **FAIL-07**: When no healthy receiver exists, rows stay paused on the frozen sender; nothing is lost or failed; the existing reconcile loop resumes them; logged "nowhere to move" (D-13)
- [ ] **FAIL-08**: Failover logs COUNT moved + source sender UUID + receiver sender UUIDs only — never recipient phones/payloads (D-12)
- [ ] **FAIL-09**: No migration — failover operates on existing columns only (code_context)

### Account Health & Restriction Audit (Phase 10)

- [x] **HLTH-01**: Durable, append-only event-log всех предупреждений/ограничений аккаунта — типы `spam_limited` / `frozen` / `flood_wait` / `cleared` / `banned`. Каждое событие хранит: sender, тип, источник (`queue_error` / `spambot_reconcile`), `restricted_until`, сырой текст ошибки/ответа @SpamBot, server_ts. Не затирается (в отличие от `message_queue.error_message`)
- [x] **HLTH-02**: К каждому событию ограничения привязан срез предшествующей активности sender'а: объём отправок за 1ч / 24ч до события, число уникальных новых контактов, использованный прокси, фактический темп — чтобы реконструировать «что делали → за что получили»
- [x] **HLTH-03**: Видимость для команды: история событий по конкретному аккаунту + агрегат (флуд/ограничения по дням, % пула под ограничением сейчас). Источник для будущих алертов

### Pool Visibility (Phase 10 — derived this phase, see 10-RESEARCH.md §Phase Requirements)

- [x] **POOLV-01**: `CampaignResponse` exposes an aggregate `pool_health` object `{active, paused, total, earliest_resume_at}` computed in one pass in `_campaign_to_response` (derived; D-08/D-10)
- [x] **POOLV-02**: Each `attached_senders[]` entry is enriched with `restriction_status` + `restricted_until` (reuses `SenderResponse` field names verbatim) (derived; D-08)
- [x] **POOLV-03**: Frontend campaign-page pool badge with 3 states (green=all active, yellow=K/N partial pause, red=all paused), derived on the frontend from numeric `pool_health` — sibling repo `aimly-tg-outreach` (derived; D-09/D-11) — _implemented in code (10-04, sibling `566dce6`); human-UAT PENDING (closed on trust, awaiting frontend deploy — see 10-04-HUMAN-UAT.md)_
- [x] **POOLV-04**: Frontend account-page mini event-list reading the HLTH-03 restriction-events endpoint, newest-first (derived; D-11) — _implemented in code (10-04, sibling `566dce6`); human-UAT PENDING (closed on trust, awaiting frontend deploy — see 10-04-HUMAN-UAT.md)_

### Per-Campaign Daily New-Dialog Limit (Phase 12 — derived this phase, see 12-CONTEXT.md decisions)

- [ ] **NDLG-01**: `campaigns.max_new_dialogs_per_day INT NOT NULL DEFAULT 50` — idempotent migration `033_*.sql` (`ADD COLUMN IF NOT EXISTS`, auto-applied via `_apply_migrations`) + ORM `Campaign` column with `server_default="50"`; DEFAULT 50 applies to ALL existing campaigns incl. `running`, no backfill (D-10/D-11)
- [ ] **NDLG-02**: Per-sender-per-campaign enforcement in `_process_next_for_sender` item-selection — when `(sender_id, campaign_id)` has opened `>= max_new_dialogs_per_day` unique new dialogs (first `status='sent'` to a `recipient_phone` within this campaign) over a trailing-24h rolling window, new-dialog items of that campaign are excluded from the LIMIT 8 candidate set; follow-up / re-contact items stay eligible and are sent; `_check_rate_limits` (4/20/150 + 15/hour) untouched (D-01…D-09)
- [ ] **NDLG-03**: `max_new_dialogs_per_day: int = Field(ge=1, le=100)` (default 50) added to `CampaignCreate`, `CampaignUpdate`, and `CampaignResponse` (D-12)
- [ ] **NDLG-04**: Soft-cap = 50, hard-cap = 100: value >50 and ≤100 → 200 + `warnings[]` (`WarningItem` / `RATE_SOFT_CAP` pattern) on the write path (POST create / PATCH update); value >100 → 422 (`RATE_LIMIT_EXCEEDS_HARD_CAP` pattern); GET path carries no warnings; re-validates on PATCH when the field changes (D-13/D-14)
- [ ] **NDLG-05**: `lovable-handoff/openapi.json` + generated types regenerated via export-handoff flow (rebuild API container first), no manual spec editing (D-15)
- [ ] **NDLG-06**: Frontend campaign settings form field `max_new_dialogs_per_day` (default 50) with inline warning when value >50 («рекомендуем не больше 50 новых диалогов в сутки **на аккаунт** — выше растёт риск спам-бана») — sibling repo `aimly-tg-outreach`, human-UAT (D-16)

### Even Pacing Across Sending Window (Phase 13 — derived this phase, see 13-CONTEXT.md decisions)

- [x] **PACE-01**: New even-pacing config (jitter fraction `PACE_JITTER_LOW`/`PACE_JITTER_HIGH`, any helper bounds) is appended to the rate-config block (`queue.py:39-69`) WITHOUT modifying any PROTECTED constant; a source-introspection guard asserts `MIN_SEND_INTERVAL=20`, `MAX_SEND_INTERVAL=55`, `SEND_INTERVAL_FATIGUE=0.5`, `LONG_PAUSE_*`, `MAX_NEW_CONTACTS_PER_HOUR=15` are unchanged (D-08, discretion)
- [x] **PACE-02**: A pure Python helper `_window_elapsed_fraction(now=...)` computes today's `window_start_utc` and `elapsed_fraction` per-campaign timezone from `work_hour_start/end` over the RAW window width (no long-pause subtraction), clamped to `[0,1]` (never negative, never >1), handling the post-midnight tail defensively; injectable `now` makes it unit-testable without freezegun (D-01, D-02, D-05, D-06, discretion)
- [x] **PACE-03**: An "expected-by-now" pacing predicate is added beside the Phase 12 cap inside the new-dialog branch of the `_process_next_for_sender` candidate SELECT: a new-dialog item is eligible iff `count_opened_since_window_start < :expected_now`; follow-up / re-contact items bypass it entirely; `LIMIT 8` + `FOR UPDATE OF mq SKIP LOCKED` + Phase 4 D-15 working-window re-check + Phase 12 trailing-24h cap predicate all preserved (D-05, D-07, D-09, D-10)
- [x] **PACE-04**: The pacing numerator counts new dialogs opened SINCE the start of today's window (`finished_at >= :window_start_utc`), a distinct counter from the Phase 12 trailing-24h cap; verified by a case where the two counters diverge (a dialog opened before window-start counts toward the 24h cap but NOT toward today's pace) (D-06)
- [x] **PACE-05**: The target-interval clamp `max(target, base_20–55s)` is realised STRUCTURALLY (base interval gate untouched as the floor; expected-by-now predicate as the ceiling) with no numeric `max()` and no special-casing — when the window cannot fit the limit at the floor, the limit is simply not reached, no crash (D-03, D-10)
- [x] **PACE-06**: Jitter (`random.uniform(PACE_JITTER_LOW, PACE_JITTER_HIGH)`) is applied to `expected_now` per evaluation so new-dialog openings don't form a machine grid; the worker sends exactly ONE item per `_process_next_for_sender` call so the `LIMIT 8` batch never multi-fires; follow-ups unaffected (D-04, D-08)
- [x] **PACE-07**: Follow-ups and AI replies are NEVER throttled by pacing (only cold first-touches via the queue are paced); `_check_rate_limits` (4/20/150 + 15/h) stays untouched and pacing-free (verified by introspection) (D-07, D-10, CLAUDE.md guard)

### Reliable Contact Resolution (Phase 14 — derived from investigation 2026-06-26, see `.planning/notes/checker-false-negatives.md`)

- [ ] **RESV-01**: Health-probe — между резолвами чекер периодически проверяет набор заведомо-живых контрольных номеров (стартовый набор: 49 registered из папки «Barter», `folder_id 4ecdde17-…`); контроль вернул `not_registered` → чекер помечается затроттленным (`restriction_status='spam_limited'` + событие в `sender_restriction_events`), выводится из ротации на cooldown, текущая пачка результатов помечается suspect и не финализируется. Обязательный механизм: «магического капа» нет — троттл молчаливый и стохастичный.
- [ ] **RESV-02**: Per-account burst-кап + cooldown — резолвов на пачку ≤ ~30 (под эмпирическим онсетом мягкого троттла ~45–50 при темпе 2–3с), минимальный темп 2–3с между резолвами, cooldown между пачками, дневной кап на аккаунт; калибруется и выносится в env-knobs (паттерн `CONTACT_CHECK_*`).
- [ ] **RESV-03**: Пул из нескольких checker-аккаунтов + ротация — resolve-нагрузка размазана так, чтобы ни один аккаунт не словил жёсткий shadow-ban (тысячи/день); ротация учитывает `restriction_status`, `restricted_until` и даёт аккаунтам отдых.
- [ ] **RESV-04**: Перепроверка контаминированных данных — вернутые в `pending` 2110 контактов (+ 699 из папки «Barter») перечекиваются здоровыми резолверами; результаты `not_registered`, полученные от деградировавшего чекера, не доверяются как финальные.
- [ ] **RESV-05**: `contact_check_worker` selection пропускает чекеры с `restriction_status != 'none'` ИЛИ `lifecycle_status='paused'` (сейчас фильтрует только `role='checker' AND auth_status='ok'` — дыра, позволившая битому чекеру продолжать врать).
- [ ] **RESV-06**: `not_registered` несёт confidence/source (каким чекером и когда получен) — чтобы отличать «честное отсутствие» от результата подозрительного аккаунта и не строить на нём аналитику/дедуп.
- [ ] **RESV-07**: Docs — поправить раздел «Семантика checker'а (is_registered)» в `/root/CLAUDE.md` (сейчас утверждает, что checker здоров на 2026-06-23) + зафиксировать диагноз и калибровку в `.planning/notes/checker-false-negatives.md`.

**Открытая развилка (решить в discuss/plan Phase 14):** отдельный управляемый пул чекеров (probe + кап + ротация + отдых) vs ленивый резолв при отправке самим сендером.

### Account Warmup via Inter-Account AI Chat (Phase 15 — derived this phase, see 15-CONTEXT.md decisions)

- [ ] **WARM-01**: Internal-детекция «свой со своим» по `telegram_id ∈ senders` workspace; листенер дропает до AI, не зависит от phone/членства в пуле (D-01).
- [ ] **WARM-02**: Internal-трафик не создаёт строк в `conversations`/`messages`; warmup только в `warmup_*` (D-02). Аналитика остаётся чистой (`_EXCLUDE_INTERNAL_CLAUSE` сохранить).
- [ ] **WARM-03**: Warmup-лимиты независимы от rate-limits кампаний; отправка минует `message_queue` (D-03).
- [ ] **WARM-04**: Регресс-тест-гард: internal не триггерит AI и не попадает в метрики (D-04).
- [ ] **WARM-05**: Все `/api/v1/warmup` под `AuthDep` + workspace scope (D-05).
- [ ] **WARM-06**: `warmup_enabled` per-workspace; глобальный воркер honors флаг (D-06).
- [ ] **WARM-07**: UI master toggle + per-account enroll/toggle (D-07).
- [ ] **WARM-08**: Расписание 09–20 МСК без UI-настройки (D-08).
- [ ] **WARM-09**: Интенсивность авто по дням; UI read-only уровень/прогресс (D-09).
- [ ] **WARM-10**: Per-workspace настройки контента прогрева (темы/язык/тон) с дефолтом = текущие 24 RU-темы + промпт (D-10).
- [ ] **WARM-11**: Расширенный per-account статус (+`restriction_status`, +последняя ошибка/активность) (D-11).
- [ ] **WARM-12**: Совмещение прогрева с активной кампанией разрешено (D-12).
- [ ] **WARM-13**: Новые аккаунты не авто-зачисляются в пул (D-13).
- [ ] **WARM-14**: Выборка пула пропускает аккаунты с `restriction_status != 'none'`/`restricted_until` в будущем (D-14).
- [ ] **WARM-15**: Изучить старую `telegram-api` warmup как референс и зафиксировать, почему она конфликтовала (изоляция) (D-15).

### RAG Knowledge Bases for Agents (Phase 16 — derived this phase, see 16-CONTEXT.md / 16-RESEARCH.md / 16-VALIDATION.md)

- [x] **KB-01**: Пользователь создаёт workspace-изолированную KB (`knowledge_bases` table, workspace_id FK ON DELETE CASCADE); другой workspace её не видит и не трогает (D-05).
- [x] **KB-02**: Пользователь загружает файлы (PDF/DOCX/TXT/MD/CSV через multipart) или вставляет текст — создаётся `kb_documents` строка `status='pending'` + `size_bytes`, 202 Accepted, воркер индексирует асинхронно (D-01/D-02).
- [x] **KB-03**: Ingest-воркер ведёт документ pending→processing→indexed/failed (`chunk_count`, `error`), re-index идемпотентен (delete-then-insert чанков); KB detail даёт D-09 агрегат (DOCUMENTS/INDEXED/PROCESSING/FAILED/STORAGE) + D-10 per-document статус/размер/дату (D-02/D-09/D-10).
- [x] **KB-04**: KB вешается на агента (M:N `agent_knowledge_bases`, mirror `CampaignSender`); attach/detach + обратный список агентов для KB (Agents tab); KB переиспользуемы между агентами (D-07).
- [x] **KB-05**: Retrieval через data-tool `search_knowledge_base` — регистрируется только когда у агента ≥1 KB (D-04), ищет по объединению подключённых KB, возвращает чанки `role:"tool"` сообщением, модель продолжает (two-pass), НЕ меняет `conversation.status`; пустой результат → off-topic fallback (D-03/D-04).
- [x] **KB-06**: Поиск и все KB-эндпоинты строго workspace-scoped — `kb_search` фильтрует по `workspace_id` + подключённым `kb_id`, утечки между workspace нет (D-05).
- Статическое поле `ai_contexts.knowledge_base` (Phase 11) остаётся рядом, не трогается (D-08).

### Sender-side Resolve Ladder (Phase 17 — derived this phase, see 17-CONTEXT.md / 17-RESEARCH.md / 17-VALIDATION.md; tracked via decisions D-01..D-16)

- [x] **SRLD-01**: Чекер захватывает `@username` из результата `ResolvePhone`/`ImportContacts` и возвращает его — `resolve_phone_with_fallback` перестаёт выбрасывать `user.username` (возвращал только `{is_registered, telegram_id}`). Username публичный/переносимый (в отличие от per-account `access_hash`) (D-06).
- [x] **SRLD-02**: Захваченный username durable на `contacts.tg_username_resolved` (resolve-provenance, mig 013 — reuse, без новой колонки) + `contacts_cache.username`; НИКОГДА не затирает пользовательский `contacts.username` (CSV-provenance). Воркер уже пишет `tg_username_resolved = res.get("username")` (worker:875) — оживает с SRLD-01 (D-07).
- [x] **SRLD-03**: Резолв отправителя = трёхступенчатая лестница `кэш(access_hash) → ResolveUsername(захваченный @username) → ImportContacts`; собственный `ResolvePhone` отправителя удаляется полностью (именно он дал ложные «нет» в инциденте Barter-ВЭД) (D-01, D-02).
- [x] **SRLD-04**: Tier-3 `ImportContacts` гейтится вердиктом чекера `registered`; `not_registered` → skip (не тратим рискованный import) (D-03, D-11).
- [x] **SRLD-05**: Ленивый import по одному прямо перед отправкой; опора на существующий 4/мин лимит очереди (под burst-онсетом ~47–49); НЕ трогать константы `queue.py`; НЕТ `DeleteContacts` на отправителе (книга остаётся горячей для фоллоу-апов, D-04) (D-04, D-05).
- [x] **SRLD-06**: Протухший username — `ResolveUsername` бросает `UsernameNotOccupiedError`/`UsernameInvalidError` → fall-through на import-tier (если registered), НИКОГДА не финализировать `not_registered` (сейчас `_resolve_username` кэширует False и выходит) (D-09).
- [x] **SRLD-07**: Confidence-gated чтение кэша — строка `is_registered=false` от suspect/low-confidence источника НЕ отдаётся (оба read-site: `checker.py::_lookup_cache` + `telegram.py::_get_cached_contact`) → live-перерезолв. Кэш НИКОГДА не удаляется (ROADMAP «не чистим»). Фикс cross-contamination Igor (D-12, D-13).
- [x] **SRLD-08**: Durable захват блока — `UserIsBlockedError` на send-пути → `sender_restriction_events` строка (`event_type='blocked'`, `category='restriction'`, free-form — без CHECK-миграции); read-only per-sender block-rate эндпоинт (blocks/sends за окно) поверх захваченных блоков + Phase 10 событий; НЕТ control-loop/auto-pause (D-15, D-16).
- [x] **SRLD-09**: Docs — смягчить формулировку «страна = факт» в `/root/CLAUDE.md` §«Семантика checker'а» до гипотезы (country-gate непроверен, в коде НЕ гейтим) (D-10).

**NB:** Phase 17 добавляет 0 миграций — всё хранилище переиспользует существующие колонки (`contacts.tg_username_resolved`, `contacts_cache.username`, `contacts.tg_probe_state`/`tg_confidence`, `sender_restriction_events.event_type` free-form).

### Switchable LLM Provider in UI (Phase 18 — derived this phase, see 18-CONTEXT.md / 18-RESEARCH.md / 18-VALIDATION.md; tracked via decisions D-01..D-12)

- [x] **LLMP-01**: Per-workspace `llm_settings` row (PK `workspace_id`, mirrors `warmup_settings`) holds provider/model/knobs + Fernet-encrypted key + key-status; migration `044_llm_settings.sql` idempotent, auto-applied; ORM `LLMSettings` mirror with `server_default` on every NOT NULL column (ORM-drift lesson mig 040/042) (D-01, D-04).
- [ ] **LLMP-02**: Default-off resolution — no `llm_settings` row (or no valid key) resolves to the platform OpenAI key + `settings.openai_model`, byte-identical to today; nothing breaks for existing workspaces (D-02).
- [ ] **LLMP-03**: Own API key is mandatory to switch provider/model — PATCH without a stored/entered key → 400 `KEY_REQUIRED`; the UI blocks the switch until a key is present (D-03).
- [x] **LLMP-04**: Key stored Fernet-encrypted at rest (reuse `app/services/encryption.py`); only a masked `api_key_prefix` is ever returned in API responses; the key is never written to logs or error details (D-04).
- [ ] **LLMP-05**: Test-connection endpoint probes the chosen provider (cheap `models.list`) and reports valid/invalid, flipping `api_key_status` accordingly (D-05).
- [x] **LLMP-06**: Runtime key-level error (401/403/`insufficient_quota`/402) on a byok call → fallback to the platform OpenAI default (dialog continues) + flag `api_key_status='invalid'`; transient 429/5xx do NOT fall back (Pitfall 6 — no client-traffic leak) (D-06).
- [x] **LLMP-07**: `llm_logger` records `provider` + `key_source` (`platform`/`byok`/`fallback`) + the actual model on every logged call; `llm_calls.provider`/`key_source` columns (migration 044); never-raise contract + no-prompt-in-logs guard preserved (D-07).
- [x] **LLMP-08**: Model list is live from the provider API (`models.list()`) per the client's key, server-side family-filtered to chat-with-tools families (gpt-4o*/gpt-5*/o*/claude-*), dropping embeddings/whisper/tts/dall-e/realtime/transcribe/deprecated (D-08).
- [x] **LLMP-09**: UI knobs temperature / reasoning-effort / max-tokens are capability-gated — temperature hidden for OpenAI reasoning models (400 unsupported_value), reasoning-effort shown only for reasoning models / Claude (maps to extended-thinking budget or effort) (D-09).
- [x] **LLMP-10**: Backend hard-clamp + UI green corridor — reasoning-model max-tokens floored at ≥4000 (2026-07-02 ghosted-contact incident), sane ceiling; impossible to break the prod answerer with a setting; Claude thinking `budget < max_tokens` (Pitfall 2) (D-10).
- [x] **LLMP-11**: The chat answerer (all `ai_engine.generate_response` LLM calls incl. empty-retry + second-pass) AND warmup route through the chosen provider/model/knobs via a thin `app/services/llm/` adapter (OpenAIProvider + AnthropicProvider normalizing to a single `LLMResult`); switch applies at the next call, no redeploy (D-11).
- [x] **LLMP-12**: Whisper transcription + KB embeddings (ingest + search) ALWAYS stay on the platform OpenAI singleton regardless of provider choice — Anthropic has no such APIs; choosing Claude does not break voice or KB (D-12).

**NB:** Phase 18 subsumes seed BYOK-01 (own OpenAI key per workspace, was Out of Scope v2) and extends it to multi-provider. Adds ONE migration (044). Remove the PROJECT.md Out-of-Scope BYOK-01 line at phase transition.

## v2 Requirements

### Advanced Outreach

- **ADVN-01**: Многошаговые последовательности (follow-up через N дней)
- **ADVN-02**: A/B тестирование текстов сообщений
- **ADVN-03**: Расписание отправки по временным зонам контакта

### Team

- **TEAM-01**: Несколько пользователей в одном workspace (роли: admin, member)
- **TEAM-02**: Приглашение по email

### Analytics — Advanced

- **ANLX-EXP-01**: Экспорт статистики в CSV

## Out of Scope

| Feature | Reason |
|---------|--------|
| Биллинг / платёжный шлюз | Отдельная интеграция после v1, не блокирует первого клиента |
| Мобильное приложение | Web-first |
| OAuth (Google/GitHub) | Magic link через Supabase достаточно для v1 |
| Real-time чат между операторами | Telegram inbox достаточен |
| Другие мессенджеры (WhatsApp, Instagram) | Платформа Telegram-специфична |
| Собственный AI (fine-tuning) | GPT-4o-mini достаточно для v1 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| TENT-01 | Phase 1 | Pending |
| TENT-02 | Phase 1 | Pending |
| TENT-03 | Phase 1 | Pending |
| TENT-04 | Phase 1 | Pending |
| AUTH-01 | Phase 1 | Pending |
| AUTH-02 | Phase 1 | Pending |
| AUTH-03 | Phase 1 | Pending |
| AUTH-04 | Phase 1 | Pending |
| ONBD-01 | Phase 2 | Complete |
| ONBD-02 | Phase 2 | Complete |
| ONBD-03 | Phase 2 | Complete |
| ONBD-04 | Phase 2 | Complete |
| ONBD-05 | Phase 2 | Complete |
| SNDR-01 | Phase 2 | Complete |
| SNDR-02 | Phase 2 | Complete |
| SNDR-03 | Phase 2 | Complete |
| CONT-01 | Phase 2 | Complete |
| CONT-02 | Phase 2 | Complete |
| CONT-03 | Phase 2 | Complete |
| CONT-04 | Phase 2 | Complete |
| CONT-05 | Phase 2 | Complete |
| FLDR-01 | Phase 2 | Complete |
| FLDR-02 | Phase 2 | Complete |
| FLDR-03 | Phase 2 | Complete |
| AGNT-01 | Phase 3 | Complete |
| AGNT-02 | Phase 3 | Complete |
| AGNT-03 | Phase 3 | Complete |
| AGNT-04 | Phase 3 | Complete |
| CAMP-01 | Phase 4 | Complete |
| CAMP-02 | Phase 4 | Complete |
| CAMP-03 | Phase 4 | Complete |
| CAMP-04 | Phase 4 | Complete |
| CAMP-05 | Phase 4 | Complete |
| CAMP-06 | Phase 4 | Complete |
| CAMP-07 | Phase 4 | Complete |
| CAMP-08 | Phase 4 | Complete |
| CAMP-09 | Phase 4 | Complete |
| CAMP-10 | Phase 4 | Complete |
| CAMP-11 | Phase 4 | Complete |
| CAMP-12 | Phase 4 | Complete |
| CAMP-13 | Phase 4 | Complete |
| CAMP-14 | Phase 4 | Complete |
| CAMP-15 | Phase 4 | Complete |
| CAMP-16 | Phase 4 | Complete |
| CAMP-17 | Phase 4 | Complete |
| INBX-01 | Phase 5 | Complete |
| INBX-02 | Phase 5 | Complete |
| INBX-03 | Phase 5 | Complete |
| INBX-04 | Phase 5 | Complete |
| INBX-05 | Phase 5 | Complete |
| AIRC-04 | Phase 5 | Complete |
| ANLX-01 | Phase 5 | Complete |
| ANLX-02 | Phase 5 | Complete |
| ANLX-03 | Phase 5 | Complete |
| ANLX-04 | Phase 5 | Complete |
| ANLX-05 | Phase 5 | Complete |
| ADMN-01 | Phase 6 | Pending |
| ADMN-02 | Phase 6 | Pending |
| ADMN-03 | Phase 6 | Pending |
| POOL-01 | Phase 8 | Complete |
| POOL-02 | Phase 8 | Complete |
| POOL-03 | Phase 8 | Complete |
| POOL-04 | Phase 8 | Complete |
| POOL-05 | Phase 8 | Complete |
| POOL-06 | Phase 8 | Complete |
| POOL-06b | Phase 8 | Complete |
| POOL-07 | Phase 8 | Complete |
| POOL-08 | Phase 8 | Complete |
| POOL-08b | Phase 8 | Complete |
| POOL-09 | Phase 8 | Complete |
| FAIL-01 | Phase 9 | Pending |
| FAIL-02 | Phase 9 | Pending |
| FAIL-03 | Phase 9 | Pending |
| FAIL-04 | Phase 9 | Pending |
| FAIL-05 | Phase 9 | Pending |
| FAIL-06 | Phase 9 | Pending |
| FAIL-07 | Phase 9 | Pending |
| FAIL-08 | Phase 9 | Pending |
| FAIL-09 | Phase 9 | Pending |
| HLTH-01 | Phase 10 | Complete |
| HLTH-02 | Phase 10 | Complete |
| HLTH-03 | Phase 10 | Complete |
| POOLV-01 | Phase 10 | Complete |
| POOLV-02 | Phase 10 | Complete |
| POOLV-03 | Phase 10 | Code done · human-UAT pending |
| POOLV-04 | Phase 10 | Code done · human-UAT pending |
| NDLG-01 | Phase 12 | Pending |
| NDLG-02 | Phase 12 | Pending |
| NDLG-03 | Phase 12 | Pending |
| NDLG-04 | Phase 12 | Pending |
| NDLG-05 | Phase 12 | Pending |
| NDLG-06 | Phase 12 | Pending |
| PACE-01 | Phase 13 | Complete |
| PACE-02 | Phase 13 | Complete |
| PACE-03 | Phase 13 | Complete |
| PACE-04 | Phase 13 | Complete |
| PACE-05 | Phase 13 | Complete |
| PACE-06 | Phase 13 | Complete |
| PACE-07 | Phase 13 | Complete |
| WARM-01 | Phase 15 | Pending |
| WARM-02 | Phase 15 | Pending |
| WARM-03 | Phase 15 | Pending |
| WARM-04 | Phase 15 | Pending |
| WARM-05 | Phase 15 | Pending |
| WARM-06 | Phase 15 | Pending |
| WARM-07 | Phase 15 | Pending |
| WARM-08 | Phase 15 | Pending |
| WARM-09 | Phase 15 | Pending |
| WARM-10 | Phase 15 | Pending |
| WARM-11 | Phase 15 | Pending |
| WARM-12 | Phase 15 | Pending |
| WARM-13 | Phase 15 | Pending |
| WARM-14 | Phase 15 | Pending |
| WARM-15 | Phase 15 | Pending |
| KB-01 | Phase 16 | Complete |
| KB-02 | Phase 16 | Complete |
| KB-03 | Phase 16 | Complete |
| KB-04 | Phase 16 | Complete |
| KB-05 | Phase 16 | Complete |
| KB-06 | Phase 16 | Complete |
| SRLD-01 | Phase 17 | Complete |
| SRLD-02 | Phase 17 | Complete |
| SRLD-03 | Phase 17 | Complete |
| SRLD-04 | Phase 17 | Complete |
| SRLD-05 | Phase 17 | Complete |
| SRLD-06 | Phase 17 | Complete |
| SRLD-07 | Phase 17 | Complete |
| SRLD-08 | Phase 17 | Complete |
| SRLD-09 | Phase 17 | Complete |
| LLMP-01 | Phase 18 | Complete |
| LLMP-02 | Phase 18 | Pending |
| LLMP-03 | Phase 18 | Pending |
| LLMP-04 | Phase 18 | Complete |
| LLMP-05 | Phase 18 | Pending |
| LLMP-06 | Phase 18 | Complete |
| LLMP-07 | Phase 18 | Complete |
| LLMP-08 | Phase 18 | Complete |
| LLMP-09 | Phase 18 | Complete |
| LLMP-10 | Phase 18 | Complete |
| LLMP-11 | Phase 18 | Complete |
| LLMP-12 | Phase 18 | Complete |

**Coverage:**

- v1 requirements: 70 total
- Mapped to phases: 70
- Unmapped: 0 ✓
- Post-v1 (Sender Pool Resilience): FRZ-01..05 (Phase 7), POOL-01..09 (Phase 8), FAIL-01..09 (Phase 9), HLTH-01..03 + POOLV-01..04 (Phase 10) — all mapped
- Phase 16 (RAG Knowledge Bases): KB-01..06 derived during /gsd:plan-phase 16 — all mapped across plans 16-01..16-05

**Deprecated from previous v1 scope** (replaced by new model):

- Старые `AGNT-01..06` (per-sender настройки + страница агента) → разделены на `SNDR-01..03` (Phase 2) и новые `AGNT-01..04` (Phase 3, шаблоны)
- Старые `AIRC-01..03, AIRC-05` (AI-контекст с auto_pause_triggers, привязка к workspace) → переехали в `AGNT-01..04` (агент-шаблон) и `CAMP-11..16` (сигналы на уровне кампании)
- `CONT-04` старый (переменные `{{имя}}`) → переехал в `CAMP-10`

---
*Requirements defined: 2026-04-02*
*Last updated: 2026-05-21 — restructured into 6 phases with Campaign entity*
*2026-06-24 — added HLTH-01..03 (Account Health & Restriction Audit) to Phase 10*
*2026-06-24 — derived POOLV-01..04 (Pool Visibility) during Phase 10 planning*
*2026-06-25 — derived NDLG-01..06 (Per-Campaign Daily New-Dialog Limit) during Phase 12 planning*
*2026-06-26 — derived PACE-01..07 (Even Pacing Across Sending Window) during Phase 13 planning*
*2026-06-29 — derived WARM-01..15 (Account Warmup via Inter-Account AI Chat) during Phase 15 planning*
*2026-06-30 — derived KB-01..06 (RAG Knowledge Bases for Agents) during Phase 16 planning*
*2026-06-30 — derived SRLD-01..09 (Sender-side Resolve Ladder) during Phase 17 planning*
*2026-07-02 — derived LLMP-01..12 (Switchable LLM Provider in UI) during Phase 18 planning*
