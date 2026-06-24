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

**Coverage:**

- v1 requirements: 70 total
- Mapped to phases: 70
- Unmapped: 0 ✓
- Post-v1 (Sender Pool Resilience): FRZ-01..05 (Phase 7), POOL-01..09 (Phase 8), FAIL-01..09 (Phase 9), HLTH-01..03 + POOLV-01..04 (Phase 10) — all mapped

**Deprecated from previous v1 scope** (replaced by new model):

- Старые `AGNT-01..06` (per-sender настройки + страница агента) → разделены на `SNDR-01..03` (Phase 2) и новые `AGNT-01..04` (Phase 3, шаблоны)
- Старые `AIRC-01..03, AIRC-05` (AI-контекст с auto_pause_triggers, привязка к workspace) → переехали в `AGNT-01..04` (агент-шаблон) и `CAMP-11..16` (сигналы на уровне кампании)
- `CONT-04` старый (переменные `{{имя}}`) → переехал в `CAMP-10`

---
*Requirements defined: 2026-04-02*
*Last updated: 2026-05-21 — restructured into 6 phases with Campaign entity*
*2026-06-24 — added HLTH-01..03 (Account Health & Restriction Audit) to Phase 10*
*2026-06-24 — derived POOLV-01..04 (Pool Visibility) during Phase 10 planning*
