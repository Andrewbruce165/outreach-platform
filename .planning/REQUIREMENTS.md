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

- [ ] **AGNT-01**: Пользователь создаёт агента (AI-шаблон) с именем — workspace-level
- [x] **AGNT-02**: Задаёт настройки агента: контекст (промпт), задача, тон, FAQ
- [x] **AGNT-03**: Агент переиспользуется между несколькими кампаниями
- [ ] **AGNT-04**: Список агентов workspace с CRUD (создать / редактировать / удалить, дубликат)

### Campaigns (Phase 4)

- [ ] **CAMP-01**: Создание кампании с именем и описанием
- [ ] **CAMP-02**: Выбор агента-шаблона из списка workspace
- [ ] **CAMP-03**: Выбор папки контактов как таргета кампании
- [ ] **CAMP-04**: Выбор TG-аккаунтов (senders) — с каких аккаунтов идёт рассылка; sender блокируется за активной кампанией
- [ ] **CAMP-05**: Расписание кампании: рабочие часы и дни (заменяет глобальные 09–20 МСК)
- [ ] **CAMP-06**: Старт и стоп даты кампании (опционально)
- [ ] **CAMP-07**: Статусы кампании: draft / running / paused / done
- [ ] **CAMP-08**: Пользователь запускает / паузит / останавливает кампанию
- [ ] **CAMP-09**: Контакты досыпаются в активную кампанию через папку (добавление в папку = добавление в очередь кампании)
- [ ] **CAMP-10**: Переменные `{{имя}}, {{username}}, {{source}}, {{custom.X}}` подставляются из контакта в текст сообщения
- [ ] **CAMP-11**: Сигнал «передать лид» — паттерн/фраза; срабатывание помечает диалог как лид и триггерит webhook
- [ ] **CAMP-12**: Сигнал «передать на менеджера» — заменяет старый auto_pause_triggers, AI замолкает, диалог помечается
- [ ] **CAMP-13**: Сигнал «финиш диалога» — диалог закрывается, AI замолкает, triggerит webhook
- [ ] **CAMP-14**: Webhook кампании — URL для передачи событий (лид / финиш / переход на менеджера); один webhook на кампанию
- [ ] **CAMP-15**: Tools кампании — спецификация function calling, передаётся в LLM вместе с агентским промптом
- [ ] **CAMP-16**: Сигналы + tools передаются в LLM-промпт вместе с агентским контекстом при каждом ответе
- [ ] **CAMP-17**: Очередь сообщений учитывает `campaign_id` — каждое сообщение принадлежит кампании

### Inbox (Phase 5)

- [ ] **INBX-01**: Пользователь видит все входящие диалоги своего workspace
- [ ] **INBX-02**: В каждом диалоге видна история сообщений (исходящие + входящие)
- [ ] **INBX-03**: Виден статус AI диалога: активен / пауза / режим менеджера / лид / финиш
- [ ] **INBX-04**: Пользователь может вручную переключить диалог в режим менеджера (AI отключается для диалога)
- [ ] **INBX-05**: Фильтр диалогов по кампании / агенту / TG-аккаунту

### AI Behavior Rules (Phase 5)

- [ ] **AIRC-04**: AI не отвечает системным ботам (SpamBot и аналоги) — фильтр на listener'е

### Analytics (Phase 5)

- [ ] **ANLX-01**: Метрики workspace: карточки отправлено / отвечено / лидов / финишей
- [ ] **ANLX-02**: Метрики кампании: те же карточки в разрезе одной кампании
- [ ] **ANLX-03**: Метрики TG-аккаунта (sender): отправлено / отвечено / ошибки в разрезе аккаунта
- [ ] **ANLX-04**: Метрики агента: использование в кампаниях, агрегированные ответы / лиды
- [ ] **ANLX-05**: Лог запросов в OpenAI на уровне диалога — какие промпты ушли и какие пришли ответы

### Admin Master Bot (Phase 6)

- [ ] **ADMN-01**: Пользователь регистрирует Telegram-чат как admin-канал workspace (бот workspace отправляет туда сообщения)
- [ ] **ADMN-02**: Бот шлёт уведомление при срабатывании сигнала «передать на менеджера» в любой активной кампании
- [ ] **ADMN-03**: Бот шлёт уведомление при ошибке TG-аккаунта (logout / FloodWait > threshold / etc.)

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
| AGNT-01 | Phase 3 | Pending |
| AGNT-02 | Phase 3 | Complete |
| AGNT-03 | Phase 3 | Complete |
| AGNT-04 | Phase 3 | Pending |
| CAMP-01 | Phase 4 | Pending |
| CAMP-02 | Phase 4 | Pending |
| CAMP-03 | Phase 4 | Pending |
| CAMP-04 | Phase 4 | Pending |
| CAMP-05 | Phase 4 | Pending |
| CAMP-06 | Phase 4 | Pending |
| CAMP-07 | Phase 4 | Pending |
| CAMP-08 | Phase 4 | Pending |
| CAMP-09 | Phase 4 | Pending |
| CAMP-10 | Phase 4 | Pending |
| CAMP-11 | Phase 4 | Pending |
| CAMP-12 | Phase 4 | Pending |
| CAMP-13 | Phase 4 | Pending |
| CAMP-14 | Phase 4 | Pending |
| CAMP-15 | Phase 4 | Pending |
| CAMP-16 | Phase 4 | Pending |
| CAMP-17 | Phase 4 | Pending |
| INBX-01 | Phase 5 | Pending |
| INBX-02 | Phase 5 | Pending |
| INBX-03 | Phase 5 | Pending |
| INBX-04 | Phase 5 | Pending |
| INBX-05 | Phase 5 | Pending |
| AIRC-04 | Phase 5 | Pending |
| ANLX-01 | Phase 5 | Pending |
| ANLX-02 | Phase 5 | Pending |
| ANLX-03 | Phase 5 | Pending |
| ANLX-04 | Phase 5 | Pending |
| ANLX-05 | Phase 5 | Pending |
| ADMN-01 | Phase 6 | Pending |
| ADMN-02 | Phase 6 | Pending |
| ADMN-03 | Phase 6 | Pending |

**Coverage:**

- v1 requirements: 59 total
- Mapped to phases: 59
- Unmapped: 0 ✓

**Deprecated from previous v1 scope** (replaced by new model):

- Старые `AGNT-01..06` (per-sender настройки + страница агента) → разделены на `SNDR-01..03` (Phase 2) и новые `AGNT-01..04` (Phase 3, шаблоны)
- Старые `AIRC-01..03, AIRC-05` (AI-контекст с auto_pause_triggers, привязка к workspace) → переехали в `AGNT-01..04` (агент-шаблон) и `CAMP-11..16` (сигналы на уровне кампании)
- `CONT-04` старый (переменные `{{имя}}`) → переехал в `CAMP-10`

---
*Requirements defined: 2026-04-02*
*Last updated: 2026-05-21 — restructured into 6 phases with Campaign entity*
