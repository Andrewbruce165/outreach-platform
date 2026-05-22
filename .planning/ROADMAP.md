# Roadmap: Outreach Platform

## Overview

Превращаем внутренний инструмент AGS Foods в мультитенантную SaaS-платформу для Telegram-аутрича.
Бизнес-логика (очередь, AI-ответчик, Telethon-клиент, webhook+tools) уже работает — строим поверх неё workspace-изоляцию, auth, модель кампании, переиспользуемые AI-агенты, аналитику и админ-бота.
Цель v1: первый внешний клиент может зарегистрироваться, подключить аккаунты, загрузить контакты, создать агента, запустить кампанию и видеть результаты самостоятельно.

## Phases

- [x] **Phase 1: Workspace Foundation** — мультитенантная схема БД + auth middleware + новый API-скелет (completed 2026-05-21)
- [ ] **Phase 2: TG Accounts & Contacts** — онбординг TG-аккаунтов в workspace + база контактов с папками + проверка в TG
- [x] **Phase 02.1: Multi-tenant Worker Hardening** — закрыть BLOCKER findings code-review (queue/listener/warmup/rotation без workspace_id, reauth, /health, SKIP LOCKED) (completed 2026-05-21)
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

- [x] 02-01: Wire onboarding flow to workspace — scope sessions and senders to workspace_id, expose status
- [x] 02-02: Per-sender settings model & API — rate limits, proxy, status fields with workspace scoping
- [x] 02-03: Contact folders model — folders table, contact.folder_id FK, CRUD endpoints
- [x] 02-04: Contact model & CSV import — fields (phone/username/full_name/source/custom JSONB), CSV parser with folder target
- [x] 02-05: Contact check via checker on import — async pipeline marks contacts with Telegram presence status

---

### Phase 02.1: Multi-tenant Worker Hardening

**Goal:** Закрыть 9 BLOCKER findings code-review Phase 2 (CR-01..CR-09) — все унаследованные worker'ы (queue, listener, warmup, rotation) пишут с `workspace_id`; reauth flow корректно UPDATE'ит существующего sender'а; `/health` не раскрывает per-tenant aggregates; ContactCheckWorker использует `FOR UPDATE SKIP LOCKED`; `_verify_api_key` кэширован.
**Depends on:** Phase 2
**Requirements**: TENT-01 (multi-tenant isolation), SNDR-01 (sender lifecycle), ONBD-01 (onboarding incl. reauth)
**Success Criteria** (what must be TRUE):

1. Все INSERT в `messages_log`, `conversations`, `context_contact_assignments`, `warmup_*` содержат `workspace_id`; нет `NotNullViolation` при отправке/входящем/warmup/ротации (CR-01, CR-02, CR-03, CR-04, CR-06)
2. Warmup-парирование контактов изолировано по workspace_id (sender из workspace A не может парироваться с sender из workspace B) (CR-04 issue 3)
3. Reauth flow работает на повторе для того же sender_slug — нет `IntegrityError`; per-workspace UNIQUE (workspace_id, slug) (CR-05, WR-02)
4. `GET /api/v1/health` (public, без auth) не раскрывает per-tenant aggregates — возвращает только {status, database, version, uptime_seconds} (CR-07)
5. ContactCheckWorker безопасен при горизонтальном масштабе — `FOR UPDATE OF c SKIP LOCKED` + `tg_checked_at` claim (CR-08)
6. `_verify_api_key` кэширован (LRU/TTL 5 мин) + `hmac.compare_digest` + `datetime.now(timezone.utc)` — n8n push с тем же ключом не делает bcrypt на каждом запросе (CR-09)

**Plans:** 3/3 plans complete

Plans:

- [x] 02.1-01: Worker workspace_id sweep (queue/listener/warmup/rotation) + warmup partitioning + SQL precedence fix — CR-01, CR-02, CR-03, CR-04, CR-06
- [x] 02.1-02: Reauth flow rewrite + migration 014 (slug per-workspace UNIQUE + onboarding_sessions.original_sender_id) — CR-05, WR-02
- [x] 02.1-03: Health lockdown + ContactCheckWorker SKIP LOCKED + _verify_api_key LRU cache — CR-07, CR-08, CR-09

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

- [x] 03-01-agent-model-decoupling-PLAN.md — миграция 015 (DROP 6 ai_contexts columns + senders.ai_context_id + UNIQUE workspace_name) + ORM cleanup + 5 worker adapters (ai_engine/listener/rotation/queue/senders.py) [Wave 1]
- [x] 03-02-agent-crud-api-and-ui-contract-PLAN.md — новый /api/v1/agents router (CRUD + duplicate) + рерайт /api/v1/send под AuthDep с explicit ai_context_id + регистрация в main.py [Wave 1, depends_on: 03-01]

---

### Phase 4: Campaigns

**Goal**: Клиент создаёт кампанию (объект-обёртка над рассылкой) — связывает агента + TG-аккаунты + папку контактов + сигналы (лид/менеджер/финиш) + webhook + tools + расписание, запускает её и видит как сообщения уходят.
**Depends on**: Phase 2, Phase 3
**Requirements**: CAMP-01, CAMP-02, CAMP-03, CAMP-04, CAMP-05, CAMP-06, CAMP-07, CAMP-08, CAMP-09, CAMP-10, CAMP-11, CAMP-12, CAMP-13, CAMP-14, CAMP-15, CAMP-16, CAMP-17
**Success Criteria** (what must be TRUE):

1. Пользователь создаёт кампанию: выбирает агента, папку контактов, TG-аккаунты, задаёт расписание (часы / дни / старт-стоп даты)
2. Задаёт сигналы кампании (паттерны для «передать лид», «передать на менеджера», «финиш диалога»), webhook URL и (опционально) tools-спецификацию
3. Запускает кампанию — очередь генерируется из контактов папки, рассылка идёт через выбранные аккаунты с подстановкой переменных `{{имя}}, {{username}}, {{source}}, {{custom.X}}`
4. При срабатывании сигнала диалог помечается соответствующим статусом и webhook вызывается с данными события
5. Пользователь паузит / останавливает кампанию; добавление контакта в папку = досыпание в активную кампанию
6. Sender, подключенный к активной кампании, не может быть выбран в другую активную кампанию (лочится)

**Plans**: 5 plans

Plans:

- [x] 04-01-PLAN.md — Audit existing webhook+tools+signals code, recover dropped webhook_functions shape from git, classify 10 TODO(phase-4) markers across plans 04-02..04-05, resolve 5 Open Questions (Q1=NULLable message_queue.campaign_id, Q2=include /duplicate, Q3=text+tool_call → send farewell, Q4=API-level workspace validation, Q5=atomic transaction in worker) — output `.planning/phases/04-campaigns/04-01-AUDIT.md` [Wave 1]
- [x] 04-02-PLAN.md — Migration 016_phase4.sql (campaigns + campaign_senders + campaign_contact_assignments + conversations.campaign_id + message_queue.campaign_id NULLable + DROP context_contact_assignments + extend conversations.status CHECK), ORM models, Pydantic schemas, /api/v1/campaigns router (CRUD + 5 lifecycle endpoints + /duplicate + sender lock), close 4 TODO markers (agents.py campaign_count + DELETE blocks in agents/folders/senders), update REQUIREMENTS.md CAMP-14 — addresses CAMP-01..04, 07, 08, 14 [Wave 2, depends_on: 04-01]
- [x] 04-03-PLAN.md — Per-campaign schedule rewrite в queue.py: remove globals MOSCOW_TZ / WORK_HOUR_START/END / _is_working_hours, add `_campaign_in_working_window` helper, JOIN campaigns в queue tick, mark items past stop_date as failed, paused campaign skip; CLAUDE.md guard for empirical intervals — addresses CAMP-05, 06 [Wave 2, depends_on: 04-01]
- [x] 04-04-PLAN.md — app/services/template.py (render_template Mustache + Russian aliases), app/services/campaign_enqueue.py (CampaignEnqueueWorker singleton, tick 30s, atomic INSERT cca+queue), rotation.py rewrite (campaign_id signature), send.py rewrite (campaign_id body), queue.py INSERT conversations.campaign_id, config env vars, lifespan registration, close 3 TODO markers — addresses CAMP-09, 10, 17 [Wave 3, depends_on: 04-02, 04-03]
- [x] 04-05-PLAN.md — ai_engine.py extensions (BUILT_IN_TOOL_NAMES, build_builtin_tools, _handle_builtin_signal, get_context_for_conversation), webhook_notify.py helper with uniform payload, listener.py minimal switch to get_context_for_conversation (3 TODO closed), priority dispatch finish>handoff>lead, Q3 farewell text+tool_call handling, custom tools migration from ai_contexts.webhook_functions to campaigns.tools — addresses CAMP-11, 12, 13, 15, 16 [Wave 4, depends_on: 04-02, 04-04]

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

**Plans**: 3 plans

Plans:

- [x] 05-01-migration-inbox-manager-bot-filter-PLAN.md — Migration 017 (status CHECK +bot_ignored, llm_calls table, 3 composite indexes) + полный рерайт app/routers/conversations.py (8 endpoints под auth_dep, D-01..D-04 manager mode) + listener.py proactive bot filter (D-05/D-06) + queue.py pre-send race guard + регистрация в main.py [Wave 1]
- [x] 05-02-analytics-router-PLAN.md — app/routers/analytics.py (4 endpoints workspace/campaigns/agents/senders с identical AnalyticsCards schema) + _compute_cards helper (real-time COUNT, D-13/D-14/D-15/D-16) + AnalyticsReplied/AnalyticsCards schemas [Wave 2, depends_on: 05-01]
- [ ] 05-03-llm-logger-and-read-endpoint-PLAN.md — app/services/llm_logger.py (never-raise log_llm_call) + 2 wrap points в ai_engine.generate_response + GET /conversations/{id}/llm-calls endpoint + LLMCallResponse/LLMCallListResponse schemas [Wave 2, depends_on: 05-01]

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
| 1. Workspace Foundation | 3/3 | Complete | 2026-05-21 |
| 2. TG Accounts & Contacts | 5/5 | Verified (gaps_found → 02.1) | 2026-05-21 |
| 02.1. Worker Hardening | 3/3 | Complete   | 2026-05-21 |
| 3. Agents (AI Templates) | 0/2 | Planned (2 plans, both Wave 1) | - |
| 4. Campaigns | 0/5 | Planned (5 plans, waves 1→4) | - |
| 5. Inbox & Analytics | 0/3 | Planned (3 plans, waves 1→2) | - |
| 6. Admin Master Bot | 0/2 | Not started | - |

**Total: 7 phases (incl. 02.1 hardening), 23 plans, 59 requirements mapped + 9 CR findings traced, 0 unmapped ✓**
