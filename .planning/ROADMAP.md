# Roadmap: Outreach Platform

## Overview

Превращаем внутренний инструмент AGS Foods в мультитенантную SaaS-платформу для Telegram-аутрича.
Бизнес-логика (очередь, AI-ответчик, Telethon-клиент, webhook+tools) уже работает — строим поверх неё workspace-изоляцию, auth, модель кампании, переиспользуемые AI-агенты, аналитику и админ-бота.
Цель v1: первый внешний клиент может зарегистрироваться, подключить аккаунты, загрузить контакты, создать агента, запустить кампанию и видеть результаты самостоятельно.

## Phases

- [x] **Phase 1: Workspace Foundation** — мультитенантная схема БД + auth middleware + новый API-скелет (completed 2026-05-21)
- [ ] **Phase 2: TG Accounts & Contacts** — онбординг TG-аккаунтов в workspace + база контактов с папками + проверка в TG
- [ ] **Phase 3: Agents (AI Templates)** — переиспользуемые AI-агенты на уровне workspace
- [ ] **Phase 4: Campaigns** — модель кампании + расписание + сигналы + webhook/tools + рерайт очереди
- [ ] **Phase 5: Inbox & Analytics** — inbox с фильтром по кампании + ручник + метрики + лог LLM-запросов
- [ ] **Phase 6: Admin Master Bot** — TG-бот workspace для уведомлений (ручник, ошибки аккаунтов)

## Phase Details

### Phase 1: Workspace Foundation

**Goal**: Заложить мультитенантный фундамент — все данные изолированы по workspace_id, вход через magic link, новый API-слой готов к расширению.
**Depends on**: Nothing (first phase)
**Requirements**: TENT-01, TENT-02, TENT-03, TENT-04, AUTH-01, AUTH-02, AUTH-03, AUTH-04
**Success Criteria** (what must be TRUE):

1. Пользователь вводит email → получает magic link → входит в систему
2. При первом входе workspace создаётся автоматически
3. FastAPI принимает Supabase JWT и отклоняет запросы без валидного токена (403)
4. Workspace имеет уникальный API-ключ (виден в настройках)
5. Все новые таблицы имеют `workspace_id`; запросы без него невозможны на уровне кода

**Plans**: 3 plans

Plans:

- [x] 01-01: DB migration — add workspaces table, workspace_id FK to all core tables
- [x] 01-02: Auth middleware — Supabase JWT verification, workspace context injection
- [x] 01-03: API skeleton rewrite — new router structure, workspace API key endpoint

---

### Phase 2: TG Accounts & Contacts

**Goal**: Клиент подключает свои Telegram-аккаунты в workspace, настраивает их (rate limits, прокси), загружает базу контактов с папками и проверяет наличие в Telegram при импорте.
**Depends on**: Phase 1
**Requirements**: ONBD-01, ONBD-02, ONBD-03, ONBD-04, ONBD-05, SNDR-01, SNDR-02, SNDR-03, CONT-01, CONT-02, CONT-03, CONT-04, CONT-05, FLDR-01, FLDR-02, FLDR-03
**Success Criteria** (what must be TRUE):

1. Пользователь проходит онбординг TG-аккаунта (телефон → SMS → готово), поддерживается 2FA и QR; аккаунт привязан к workspace
2. На странице аккаунта пользователь задаёт rate limits (с warning при выходе за «зелёный коридор» 4/20/150) и прокси
3. Список аккаунтов workspace показывает live-статус каждого (активен / прогрев / пауза / ошибка)
4. Пользователь загружает CSV в выбранную папку — телефоны проверяются в TG через checker, статус сохраняется
5. Папки CRUD: создание / переименование / удаление; контакты можно перемещать между папками

**Plans**: 5 plans

Plans:

- [ ] 02-01: Wire onboarding flow to workspace — scope sessions and senders to workspace_id, expose status
- [x] 02-02: Per-sender settings model & API — rate limits, proxy, status fields with workspace scoping
- [ ] 02-03: Contact folders model — folders table, contact.folder_id FK, CRUD endpoints
- [ ] 02-04: Contact model & CSV import — fields (phone/username/full_name/source/custom JSONB), CSV parser with folder target
- [ ] 02-05: Contact check via checker on import — async pipeline marks contacts with Telegram presence status

---

### Phase 3: Agents (AI Templates)

**Goal**: Клиент создаёт переиспользуемые AI-агентов на уровне workspace — каждый агент содержит контекст / задачу / тон / FAQ и используется в нескольких кампаниях.
**Depends on**: Phase 1 (workspace foundation; формально не требует Phase 2, но логически после)
**Requirements**: AGNT-01, AGNT-02, AGNT-03, AGNT-04
**Success Criteria** (what must be TRUE):

1. Пользователь создаёт агента с именем, задаёт контекст / задачу / тон / FAQ
2. Существующая модель `ai_contexts` переиспользуется (без переименования), но отвязывается от sender'а — становится workspace-level
3. Тот же агент можно подключать в нескольких кампаниях
4. Страница списка агентов показывает: имя, кол-во кампаний где использован, кнопки дубликата и удаления

**Plans**: 2 plans

Plans:

- [ ] 03-01: Agent model decoupling — отвязать `ai_contexts` от sender_id, привязать к workspace_id, миграция данных
- [ ] 03-02: Agent CRUD API & UI contract — endpoints для создания / редактирования / списка / дубликата / удаления

---

### Phase 4: Campaigns

**Goal**: Клиент создаёт кампанию (объект-обёртка над рассылкой) — связывает агента + TG-аккаунты + папку контактов + сигналы (лид/менеджер/финиш) + webhook + tools + расписание, запускает её и видит как сообщения уходят.
**Depends on**: Phase 2, Phase 3
**Requirements**: CAMP-01..17 (17 требований)
**Success Criteria** (what must be TRUE):

1. Пользователь создаёт кампанию: выбирает агента, папку контактов, TG-аккаунты, задаёт расписание (часы / дни / старт-стоп даты)
2. Задаёт сигналы кампании (паттерны для «передать лид», «передать на менеджера», «финиш диалога»), webhook URL и (опционально) tools-спецификацию
3. Запускает кампанию — очередь генерируется из контактов папки, рассылка идёт через выбранные аккаунты с подстановкой переменных `{{имя}}, {{username}}, {{source}}, {{custom.X}}`
4. При срабатывании сигнала диалог помечается соответствующим статусом и webhook вызывается с данными события
5. Пользователь паузит / останавливает кампанию; добавление контакта в папку = досыпание в активную кампанию
6. Sender, подключенный к активной кампании, не может быть выбран в другую активную кампанию (лочится)

**Plans**: 5 plans

Plans:

- [ ] 04-01: Audit existing webhook + function calling — что реализовано в коде, что переносим на уровень кампании, что переписываем
- [ ] 04-02: Campaign model & lifecycle — таблица campaigns, статусы (draft/running/paused/done), sender lock, attached folder/agent
- [ ] 04-03: Campaign schedule & start/stop — рабочие часы кампании (заменяет глобальные 09–20 МСК), даты, переходы статусов
- [ ] 04-04: Queue rewrite for campaign_id — миграция queue под campaign_id, подстановка переменных при постановке, генерация задач из папки
- [ ] 04-05: Signals + webhook + tools wiring — сигналы передаются в LLM-промпт вместе с агентским контекстом, webhook вызывается на событие, tools передаются как function calling

---

### Phase 5: Inbox & Analytics

**Goal**: Клиент видит входящие диалоги с фильтром по кампании, переключает на ручник и смотрит метрики по уровням (workspace / campaign / agent / sender) + лог LLM-запросов на уровне диалога.
**Depends on**: Phase 4
**Requirements**: INBX-01, INBX-02, INBX-03, INBX-04, INBX-05, AIRC-04, ANLX-01, ANLX-02, ANLX-03, ANLX-04, ANLX-05
**Success Criteria** (what must be TRUE):

1. Inbox показывает все диалоги workspace с историей сообщений и статусом AI (активен / пауза / менеджер / лид / финиш)
2. Из inbox можно переключить диалог в режим менеджера (AI отключается для диалога)
3. Доступен фильтр диалогов по кампании, агенту, TG-аккаунту
4. AI не отвечает системным ботам (SpamBot и аналоги) — фильтр на listener'е
5. Дашборд показывает карточки метрик на 4 уровнях: workspace / campaign / agent / sender (отправлено / отвечено / лидов / финишей / ошибки для sender)
6. В каждом диалоге доступен лог LLM-запросов (промпт → ответ) для отладки

**Plans**: 4 plans

Plans:

- [ ] 05-01: Inbox API & filters — conversations endpoint with workspace + campaign/agent/sender filters
- [ ] 05-02: Manual manager mode + system bot filter — toggle endpoint, listener-side bot blocklist
- [ ] 05-03: Analytics aggregation — карточки метрик по 4 уровням, агрегаты из очереди и диалогов
- [ ] 05-04: LLM request log per conversation — сохранение запросов/ответов с привязкой к conversation_id

---

### Phase 6: Admin Master Bot

**Goal**: Workspace имеет свой Telegram-бот, который шлёт админу уведомления при срабатывании ручника и при ошибках TG-аккаунтов.
**Depends on**: Phase 4 (нужны кампании и сигналы для уведомлений)
**Requirements**: ADMN-01, ADMN-02, ADMN-03
**Success Criteria** (what must be TRUE):

1. Пользователь регистрирует Telegram-чат (приватный с ботом или группа с ботом) как admin-канал workspace
2. При срабатывании сигнала «передать на менеджера» в любой активной кампании бот шлёт уведомление в admin-канал с ссылкой на диалог
3. При ошибке TG-аккаунта (logout / FloodWait > N / session expired) бот шлёт уведомление с указанием аккаунта и причины

**Plans**: 2 plans

Plans:

- [ ] 06-01: Admin bot registration — botfather token storage per workspace, chat registration flow, /start handler
- [ ] 06-02: Event notifications — listener hooks для manager-takeover и sender-error events, отправка в admin chat

---

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Workspace Foundation | 0/3 | Not started | - |
| 2. TG Accounts & Contacts | 0/5 | Not started | - |
| 3. Agents (AI Templates) | 0/2 | Not started | - |
| 4. Campaigns | 0/5 | Not started | - |
| 5. Inbox & Analytics | 0/4 | Not started | - |
| 6. Admin Master Bot | 0/2 | Not started | - |

**Total: 6 phases, 21 plans, 59 requirements mapped, 0 unmapped ✓**
