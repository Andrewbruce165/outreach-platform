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
- [~] **Phase 6: Admin Master Bot** — TG-бот workspace для уведомлений (ручник, ошибки аккаунтов) — **DEFERRED TO v2** (see `.planning/seeds/admin-master-bot.md`, PROJECT.md → ADMN-01..03)

_Block: Sender Pool Resilience & Failover (post-v1) — design: `.planning/proposals/sender-pool-resilience.md`_

- [ ] **Phase 7: Unified Freeze Policy** — antispam-путь как PEER_FLOOD (пауза+флаг вместо `failed`, реконсайл авто-resume; ответы в диалогах не глушим) + ротация не садит новые контакты на ограниченный аккаунт
- [x] **Phase 8: Pool Management & Even Distribution** — attach/detach аккаунтов к кампании + фронт-мультиселект + равномерная раздача по пулу (completed 2026-06-23)
- [x] **Phase 9: Cold-Contact Failover** — не-контактированные задачи замёрзшего аккаунта уходят на здоровые; активные диалоги ждут свой аккаунт (completed 2026-06-24)
- [x] **Phase 10: Pool Visibility & Restriction Audit** (optional) — здоровье пула в кампании (N активно / K на паузе до T) + бейдж; durable аудит всех предупреждений/блокировок аккаунтов с привязкой к предшествующей активности (completed 2026-06-24)
- [ ] **Phase 11: Agent/Campaign Field Split & Prompt Assembly** — развести слои Агент(КТО)/Кампания(ЧТО), убрать дубли в системном промпте (один источник на блок), новые поля (скорость ответа, ход разговора, аргументы и факты, базы знаний) + перестройка UI визарда
- [x] **Phase 14: Reliable Contact Resolution** — надёжная и масштабируемая проверка контактов в TG: health-probe на заведомо-живых, burst-кап + cooldown, пул чекеров с ротацией, перепроверка контаминированных данных (диагноз: единственный чекер занижал живых в ~15–20 раз) (closed 2026-06-30 — механика задеплоена; перечек контаминированной базы + re-activation пула передан в Phase 17)
- [x] **Phase 15: Account Warmup via Inter-Account AI Chat** — продуктизация взаимного AI-прогрева аккаунтов (переписка между своими аккаунтами через AI, безопасный набор активности) + отдельная UI-вкладка, изолированная от основного флоу аутрича (completed 2026-06-29)
- [x] **Phase 16: RAG Knowledge Bases for Agents** — базы знаний для агентов на pgvector (гибридный keyword+vector поиск) (completed 2026-06-30)
- [x] **Phase 17: Sender-side resolve ladder with username capture and import fallback** — чекер → чистый фильтр + захват @username; отправитель сам резолвит по лестнице кэш→ResolveUsername→ImportContacts (лениво перед отправкой), фолбэк на phone-резолв; чинит инцидент «Barter - ВЭД хук» (22 живых РФ-номера упали на ResolvePhone) — **planned (4 plans, waves 1→3, SRLD-01..09)** (completed 2026-06-30)
- [x] **Phase 18: Switchable LLM Provider in UI** — выбор провайдера (Claude/OpenAI) и модели в настройках + частичные настройки модели из конфига + API-ключ провайдера; чат/AI-ответчик работает через выбранную LLM (completed 2026-07-02)

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
- [x] 05-03-llm-logger-and-read-endpoint-PLAN.md — app/services/llm_logger.py (never-raise log_llm_call) + 2 wrap points в ai_engine.generate_response + GET /conversations/{id}/llm-calls endpoint + LLMCallResponse/LLMCallListResponse schemas [Wave 2, depends_on: 05-01]

---

### Phase 05.1: Lovable UI v1 (INSERTED)

**Goal:** Клиент через UI Lovable проходит весь v1-флоу — регистрируется (Supabase), создаёт workspace, подключает TG-аккаунт, загружает контакты, настраивает агента, запускает кампанию и смотрит inbox + analytics. Закрывает Core Value ("за 10 минут запустил кампанию") и 7 пунктов HUMAN-UAT, открытых после Phase 5.
**Requirements**: UI-MIG-018, UI-AGNT-01, UI-CAMPB-01, UI-CAMPL-01, UI-ACCT-01, UI-DASH-01, UI-CAMPD-01, UI-TEL-01, UI-TEL-02, UI-CORS, UI-AUTH-01, UI-CONT-01, UI-INBX-01, UI-HANDOFF, Core-Value-E2E
**Depends on:** Phase 5
**Plans:** 6/6 plans complete

Plans:

- [x] 05.1-01-PLAN.md — Migration 018 + ORM/Pydantic widening (telemetry_events + 11 agent cols + 4 campaign cols + ToolSpec.webhook_url Optional) [Wave 1, no deps]
- [x] 05.1-02-PLAN.md — CORS regex для *.lovableproject.com + Pitfall 3 (HS256 pin) комментарий в auth.py [Wave 1, no deps]
- [x] 05.1-03-PLAN.md — Campaign /stop alias + /auto-fill stub + Senders /pause /resume + Agent v2 passthrough + ai_engine COALESCE(new, legacy) [Wave 2, depends_on: 05.1-01]
- [x] 05.1-04-PLAN.md — Analytics /funnel (5 stages incl. engaged) + /llm aggregates + Telemetry /events + /core-value [Wave 2, depends_on: 05.1-01]
- [x] 05.1-05-PLAN.md — lovable-handoff/ bundle (AGENTS/KNOWLEDGE/openapi.json/types/api.ts/design-source) + UI-SPEC reconciliation patches + 2 CI scripts [Wave 3, depends_on: 05.1-03, 05.1-04]
- [x] 05.1-06-PLAN.md — Core Value E2E pytest + HUMAN-UAT mapping (7 items) + phase sentinel test [Wave 3, depends_on: 05.1-05]

### Phase 6: Admin Master Bot — **DEFERRED TO v2**

> **Status:** Перенесено в v2 (ADMN-01..03). См. seed `.planning/seeds/admin-master-bot.md` и PROJECT.md "Out of Scope for v1". Причина: v1 завершён без Phase 6; admin-bot не блокирует первого платящего клиента, но входит в обязательный v2 scope.

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

> **Block: Sender Pool Resilience & Failover** (post-v1, поверх Phase 4). Полный дизайн и обоснование: [`.planning/proposals/sender-pool-resilience.md`](proposals/sender-pool-resilience.md). Триггер — инцидент кампании b7cc7d06 (37 контактов терминально `failed` antispam-сигналом, без авто-возобновления; см. quick-задачу 260622-j52).

### Phase 7: Unified Freeze Policy

**Goal:** Единая политика мягкого спам-ограничения для всех путей. Переписать `listener._handle_antispam_signal` по образцу PEER_FLOOD: вместо терминального `failed` — пауза pending этого sender'а + `restriction_status='spam_limited'`/`restricted_until`, чтобы существующий restriction-reconcile авто-возобновлял; **перестать выключать `ai_enabled` во всех диалогах** — ответы в идущих диалогах продолжаются (Telegram их не блокирует). Добавить `AND s.restriction_status='none'` в фильтр кандидатов `rotation.py:112-125` — новые холодные контакты не садятся на ограниченный аккаунт. Регресс-тест: воркер скипает restricted sender'а.
**Requirements**: FRZ-01, FRZ-02, FRZ-03, FRZ-04, FRZ-05 (derived this phase — see 07-RESEARCH.md §Phase Requirements)
**Depends on:** Phase 4 (Campaigns), Phase 5 (Inbox / AI-reply path). Миграций нет (028 уже есть).
**Plans:** 1 plan

Plans:

- [x] 07-01-PLAN.md — Rewrite `_handle_antispam_signal` to PEER_FLOOD pause+flag mirror (delete ai_enabled block, preserve self-check guard, frozen-precedence guard) + rotation candidate filter `AND s.restriction_status='none'` + flip cancel-path test to new contract + new rotation restricted-sender regression + assert FRZ-05 worker-skip — FRZ-01..05 [Wave 1, no deps]

### Phase 8: Pool Management and Even Distribution

**Goal:** Дать кампании реальный пул из ≥2 аккаунтов. Эндпоинты `POST /campaigns/{id}/senders` и `DELETE /campaigns/{id}/senders/{sid}` (валидация workspace `_validate_workspace_owns_senders` уже есть; решить, разрешать ли на `running`); мультиселект аккаунтов во фронте (репо `aimly-tg-outreach`); подтвердить равномерную раздачу least-loaded по пулу (worker уже round-robin'ит всех eligible sender'ов). Сейчас у всех кампаний привязан 1 аккаунт.
**Requirements**: POOL-01, POOL-02, POOL-03, POOL-04, POOL-05, POOL-06, POOL-06b, POOL-07, POOL-08, POOL-08b, POOL-09 (derived this phase — see 08-RESEARCH.md §Phase Requirements)
**Depends on:** Phase 7
**Plans:** 4/4 plans complete

Plans:
**Wave 1**

- [x] 08-01-test-scaffold-PLAN.md — Wave 0 test scaffolding: conftest `test_queue_item_factory` + `tests/test_pool_endpoints.py` + `tests/test_rebalance.py` (POOL-01..08b RED stubs) [Wave 1, no deps]

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 08-02-rebalance-service-PLAN.md — NEW `app/services/rebalance.py::rebalance_on_attach` campaign-scoped even-split (FOR UPDATE SKIP LOCKED, CCA-synced, idempotent) — POOL-07/08/08b [Wave 1, depends_on: 01]

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 08-03-attach-detach-endpoints-PLAN.md — `POST /campaigns/{id}/senders` + `DELETE /campaigns/{id}/senders/{sid}` reusing _validate_workspace_owns_senders/_check_sender_lock/_campaign_to_response + MIN_POOL/DETACH guards + rebalance hook — POOL-01..06b [Wave 2, depends_on: 01, 02]

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 08-04-frontend-pool-panel-PLAN.md — interactive Senders/Пул panel (sibling repo aimly-tg-outreach) + error-codes.ts fix + OpenAPI regen + human UAT — POOL-09 [Wave 3, depends_on: 03]

### Phase 9: Cold-Contact Failover

**Goal:** При фризе sender'а перекинуть его **не-контактированные** pending-задачи (никогда не отправлялись, диалог не начат) на здоровые аккаунты пула через `get_or_assign_sender`; идущие диалоги остаются на своём аккаунте и продолжают отвечать (континуити). Safety: не заваливать один здоровый аккаунт — учитывать rate-headroom, cap батча, логировать перенос. Точный предикат «безопасно-передать» определить на этапе плана.
**Requirements**: FAIL-01, FAIL-02, FAIL-03, FAIL-04, FAIL-05, FAIL-06, FAIL-07, FAIL-08, FAIL-09 (derived this phase — see 09-RESEARCH.md §Phase Requirements)
**Depends on:** Phase 8. No migration (failover uses existing columns only — FAIL-09).
**Plans:** 2/2 plans complete

Plans:

- [x] 09-01-test-scaffold-PLAN.md — Wave 0 RED test scaffold: tests/test_failover.py (FAIL-01/03..08 stubs, import-inside-body) + conftest test_queue_item_factory with_message flag [Wave 1, no deps]
- [x] 09-02-failover-service-and-call-sites-PLAN.md — NEW app/services/failover.py::failover_cold_backlog (per-row even-spread off frozen sender, _COLD_PENDING_PREDICATE with empty-conv widening, scheduled_at=NOW, FOR UPDATE SKIP LOCKED, COUNT/UUID-only log) + wire 3 freeze call sites (PEER_FLOOD/ACCOUNT_FROZEN/antispam) + FAIL-02 integration tests — FAIL-01..09 [Wave 2, depends_on: 09-01]

### Phase 10: Pool Visibility & Restriction Audit (optional)

**Goal:** Два связанных направления. (1) **Видимость пула:** в ответе кампании показывать здоровье пула (N активно / K на паузе до T) + бейдж во фронте, чтобы была видна частичная пауза кампании. (2) **Аудит ограничений:** durable append-only event-log всех предупреждений/блокировок аккаунтов (`spam_limited`/`frozen`/`flood_wait`/`cleared`/`banned`), с источником (queue-ошибка / @SpamBot-реконсайл) и привязкой к предшествующей активности sender'а — чтобы реконструировать «что делали → за что получили». Сегодня этих данных негде взять: `message_queue.error_message` затирается при reschedule, `telemetry_events` смену restriction не пишет, логи контейнера живут ~18ч (см. `.planning/notes/account-restriction-audit-gap.md`).
**Requirements**: HLTH-01, HLTH-02, HLTH-03, POOLV-01, POOLV-02, POOLV-03, POOLV-04 (POOLV-* derived this phase — see 10-RESEARCH.md §Phase Requirements)
**Depends on:** Phase 8 (пул), Phase 7 (restriction lifecycle — источник событий)
**Plans:** 4/4 plans complete

Plans:

- [x] 10-01-test-scaffold-PLAN.md — Wave 0 RED test scaffold: tests/test_restriction_audit.py (HLTH-01/02 + D-03 + HLTH-03 stubs) + tests/test_pool_health.py (POOLV-01/02 stubs) [Wave 1, no deps]
- [x] 10-02-event-log-and-write-points-PLAN.md — migration 030 sender_restriction_events + SenderRestrictionEvent ORM + restriction_audit.py dual-mode helper (event + activity-slice snapshot) + wire 5 write-points (queue PEER_FLOOD/FROZEN/FLOOD_WAIT, listener antispam + reconcile cleared/banned/extension-gated, D-01 forward-shift gate) — HLTH-01/02 [Wave 2, depends_on: 01]
- [x] 10-03-pool-health-and-history-endpoint-PLAN.md — PoolHealth/RestrictionEventResponse schemas + pool_health aggregate & per-sender enrichment in _campaign_to_response + GET /senders/{slug}/restriction-events — POOLV-01/02, HLTH-03 [Wave 3, depends_on: 01, 02]
- [x] 10-04-frontend-pool-badge-and-event-list-PLAN.md — sibling-repo 3-state pool badge + account-page event-list + openapi regen + human UAT — POOLV-03/04 [Wave 4, depends_on: 03]

> **Non-goals (v1 этого блока):** failover **активных** диалогов на другой аккаунт (ломает континуити — ждут свой аккаунт); режим «затихать и на ответах» при мягком лимите (дефолт — продолжаем отвечать); cross-campaign load awareness; real-time алерты по банам (аудит копит данные — алерты строятся поверх позже).

### Phase 11: Agent/Campaign Field Split & Prompt Assembly

**Goal:** Убрать перегруз и дубли в настройке так, чтобы GPT-5 mini перестал «плыть» и вёл диалог предсказуемо. Развести два слоя — **Агент = КТО** (стабильная личность, переиспользуема) и **Кампания = ЧТО** (задача, ход разговора, факты, цель); правило «одно поле = одна мысль, ноль пересечений». Перестроить формы Агента и Кампании, добавить новые поля (скорость ответа с ручным вводом; ход разговора 3–5 стадий; аргументы и факты; используемые базы знаний), слить/переименовать дублирующиеся (Success criteria → Сигнал «Лид», тон только в пресете, Audience hints → «Кому пишем»), и собрать итоговый системный промпт с фиксированным порядком блоков и ровно одним источником на блок. Brief auto-fill заполняет поля, но сырой текст брифа в промпт не уходит. Включает перестройку UI визарда. Полный бриф: `BRIEF.md` в директории фазы.
**Requirements**: FLD-01..06, MIG-01..03, PMT-01..07, RT-01, UI-FLD-01..03 (derived this phase — see 11-RESEARCH.md §Phase Requirements; tracked via decisions D-01..D-15)
**Depends on:** Phase 10
**Plans:** 1/4 plans executed

Plans:
**Wave 1**

- [x] 11-01-test-scaffold-PLAN.md — Wave-0 RED tests + conftest migration-list fix (028/029/030/031) [Wave 1, no deps]

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 11-02-migration-schema-and-crud-PLAN.md — migration 032 (tone_preset/response_speed/response_delay_seconds/dialogue_flow/arguments_facts/campaign_rules; backfill voice_baseline→tone_preset + success_criteria→lead_trigger_hint; drop legacy) + ORM/schemas/routers [Wave 2, depends_on: 11-01]

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 11-03-prompt-assembly-and-runtime-PLAN.md — build_system_prompt §7 rewrite (single-source tone, dialogue_flow, facts guard, rules dedup) + context SELECTs + response_speed wire-up in listener [Wave 3, depends_on: 11-02]

**Wave 4** *(blocked on Wave 3 completion)*

- [ ] 11-04-frontend-forms-and-handoff-PLAN.md — openapi regen + Agent/Campaign wizard rebuild (tone select, response-speed, stage editor) + human UAT (cross-repo) [Wave 4, depends_on: 11-02, 11-03]

### Phase 12: Per-campaign daily new-dialog limit (max_new_dialogs_per_day)

**Goal:** Ввести явный настраиваемый дневной лимит **новых холодных диалогов** на уровне кампании. Сейчас такого лимита нет вообще — только захардкоженный `MAX_NEW_CONTACTS_PER_HOUR = 15` ([services/queue.py:52](app/services/queue.py#L52)) и общий потолок 150 сообщений/день per-sender, что позволяет одному аккаунту написать до ~150 незнакомцам в сутки при индустриальном пороге опасности 50+/сутки.

**Scope:**

- **Модель + миграция:** `campaigns.max_new_dialogs_per_day INT NOT NULL DEFAULT 50`. Миграция `NNN_*.sql`, идемпотентная (`ADD COLUMN IF NOT EXISTS`), авто-применяется через `_apply_migrations`.
- **Enforcement в queue-воркере:** per-item фильтр в выборке кандидатов `_process_next_for_sender` (НЕ в `_check_rate_limits` — D-07/D-09): подсчёт уникальных новых диалогов (нет предыдущего `status='sent'` к этому `recipient_phone` в рамках ЭТОЙ кампании), открытых этим sender'ом за trailing-24h, против `max_new_dialogs_per_day`. При достижении — из кандидатов LIMIT 8 / `FOR UPDATE OF mq SKIP LOCKED` **исключаются новые-диалоговые элементы** этой кампании, follow-up/re-contact элементы остаются eligible. Per-sender лимиты 4/20/150 + `MAX_NEW_CONTACTS_PER_HOUR=15` в `_check_rate_limits` нетронуты. Фоллоу-апы существующим контактам **не блокируются**.
- **Soft-cap warning (как D-14):** значение >50 → не блокировать, вернуть `warnings[]` (паттерн `RATE_SOFT_CAP` / `WarningItem` из [senders.py:135-168](app/routers/senders.py#L135-L168)). Выше hard cap (**100** — верх «прогретого» диапазона из индустрии) → 422. Зелёный коридор: ≤50.
- **API:** `max_new_dialogs_per_day` в `CampaignCreate` / `CampaignUpdate` / `CampaignResponse` (`Field(ge=1, le=100)`), warning при >50 в ответе create/update. Обновить `lovable-handoff/openapi.json` + типы.
- **UI-контракт (UI-SPEC):** поле в форме настроек кампании, дефолт 50, inline-предупреждение при значении выше 50 («рекомендуем не больше 50 новых диалогов в сутки на аккаунт — выше растёт риск спам-бана»).

**Acceptance:**

- Аккаунт в кампании с `max_new_dialogs_per_day=50` после 50 новых диалогов за 24ч встаёт на паузу по этому лимиту; фоллоу-апы существующим контактам не блокируются.
- Создание кампании со значением >50 → 200 + `warnings[]`; >100 → 422.
- Дефолт новой кампании = 50.

**Requirements**: NDLG-01, NDLG-02, NDLG-03, NDLG-04, NDLG-05, NDLG-06 (derived during /gsd:plan-phase 12, see REQUIREMENTS.md §Per-Campaign Daily New-Dialog Limit)
**Depends on:** Phase 11
**Plans:** 3/4 plans executed

Plans:

- [x] 12-01-PLAN.md — migration 033 + ORM column max_new_dialogs_per_day (DEFAULT 50) [NDLG-01]
- [x] 12-02-PLAN.md — queue per-(sender,campaign) new-dialog cap filter + integration test [NDLG-02]
- [x] 12-03-PLAN.md — API schemas + soft/hard-cap validation + warnings[] write-response + API tests [NDLG-03, NDLG-04]
- [ ] 12-04-PLAN.md — regenerate openapi/types + frontend campaign field with >50 warning (cross-repo, human-UAT) [NDLG-05, NDLG-06]

---

### Phase 13: Even pacing across sending window (smooth new-dialog distribution)

**Goal:** Распределять открытие **новых диалогов** равномерно по активному окну рассылки кампании, а не выпаливать дневной лимит в начале окна. Сейчас движок очереди шлёт с интервалом 20–55 сек ([queue.py:41-44](app/services/queue.py#L41-L44)) + длинные паузы каждые 12–25 отправок — это сглаживает темп, но не привязано к дневному лимиту и ширине окна, поэтому весь дневной объём может уйти за первые часы. Цель — производный темп: `max_new_dialogs_per_day / активные_часы → целевой интервал`, с плавающими интервалами и батчингом пула.

**Scope (предварительно — уточнить в /gsd:discuss-phase 13):**

- **Производный темп:** активное окно = `work_hour_end − work_hour_start` за вычетом длинных пауз (≈ «7 активных часов»). Целевой интервал между новыми диалогами = окно / `max_new_dialogs_per_day` (вход из Phase 12).
- **Батчинг пула:** пул аккаунтов кампании делится на батчи; внутри батча — 1 новый диалог каждые 3–5 мин с плавающими интервалами.
- **Эмпирические константы под защитой** (CLAUDE.md): `MIN/MAX_SEND_INTERVAL`, `LONG_PAUSE_*`, `MAX_NEW_CONTACTS_PER_HOUR` — менять только в рамках этой фазы и с явным обсуждением. Возможен per-campaign override вместо правки глобалей.
- **Применяется только к новым диалогам** — follow-ups (как и в Phase 12) не троттлятся этим механизмом.

**Acceptance (предварительно):**

- При `max_new_dialogs_per_day=50` и окне 9–20 новые диалоги открываются размазанно по окну (≈ целевой интервал), а не пачкой в первый час.
- Внутри батча интервал между новыми диалогами 3–5 мин с дрожанием.
- Follow-ups идут вне этого темпа.

**Requirements**: PACE-01, PACE-02, PACE-03, PACE-04, PACE-05, PACE-06, PACE-07 (derived during /gsd:plan-phase 13, see REQUIREMENTS.md §Even Pacing Across Sending Window)
**Depends on:** Phase 12
**Plans:** 2/2 plans complete

Plans:
**Wave 1**

- [x] 13-01-PLAN.md — Wave 0 RED test scaffold: tests/test_queue_even_pacing.py (PACE-01..07 stubs, deferred-import RED, Phase 12 helpers reused) [Wave 1, no deps]

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 13-02-PLAN.md — PACE_JITTER constants + _window_elapsed_fraction helper + expected-by-now pacing predicate in _process_next_for_sender (queue.py only, D-09) [Wave 2, depends_on: 13-01]

### Phase 14: Reliable Contact Resolution

> **✅ CLOSED 2026-06-30 (superseded).** Защитная механика чекера построена и задеплоена (14-01/02/03/05/06/07 + пост-фазовые фиксы b7j/mig-036 и trip_count-reset): деградировавший чекер больше не финализирует false-negatives, селекция гейтит restricted/paused, есть burst-cap/cooldown/rotation/rest и health-probe. **SC #1/#2/#5 закрыты.** **SC #3/#4 НЕ закрыты намеренно** — re-activation пула (14-04 live-smoke провалился) и перечек контаминированной базы (2110+699+14k) требуют genuinely-fresh warmed RU чекеров, которых нет. Этот остаток передан в **Phase 17** (sender-side resolve ladder), который снимает зависимость от отдельного пула чекеров. Фаза закрыта по решению: «ушли дальше, неактуально».

**Goal:** Сделать проверку контактов (phone → есть ли в Telegram) надёжной и масштабируемой, чтобы кампании доставали всех достижимых лидов, а не сливали их молча из-за деградировавшего чекера.
**Requirements**: RESV-01..RESV-07 (см. REQUIREMENTS.md)
**Depends on:** Phase 2 (checker / contacts_cache / contact_check_worker). Связано с Phase 10 (sender_restriction_events, restriction_status).
**Plans:** 6/7 executed (14-01/02/03/05/06/07); 14-04 (re-activation live-smoke) FAILED → остаток передан в Phase 17

**Контекст (расследование 2026-06-26 — `.planning/notes/checker-false-negatives.md`):**

- Единственный checker `sender-8428118140` получил теневое ограничение contacts-API → систематические ложноотрицательные. Рапортовал **2.5%** живых (53/2148) против настоящих **~26%** в целом / **~50%+** среди мобильных. Занижение в ~15–20 раз.
- Два режима троттла: **мягкий burst** (~45–50 быстрых резолвов подряд → редкие ложные «нет», восстановление минуты) и **жёсткий shadow-ban** (тысячи/день изо дня в день → почти всё ложное «нет», 0.07%, восстановление дни).
- Доказано: тот же номер/момент — checker пусто, два здоровых сендера резолвят обоими методами (resolvePhone + importContacts). Метод-асимметрии нет; дело в поведенческом профиле аккаунта (объём resolve).
- **Часть 1 уже выполнена вручную:** чекер на паузе (`auth_status=restricted`, `restriction_status=spam_limited`, `lifecycle_status=paused`), удалено 2216 ложных строк `contacts_cache`, **2110** контактов возвращены в `pending`. Активных чекеров сейчас НЕТ → проверка остановлена (безопасное состояние).
- Готовый health-probe набор: **49** заведомо-живых номеров (registered из папки «Barter_список пещивиков Ромы», `folder_id 4ecdde17-f454-4a1b-b4ba-732fd6b9449f`).

**Success Criteria** (what must be TRUE):

1. Затроттленный/деградировавший чекер авто-детектится в пределах N резолвов (health-probe на контролях) и перестаёт продуцировать `not_registered`.
2. `not_registered` несёт confidence/source; результаты деградировавшего чекера никогда не финализируются как истина.
3. 14k контактов проверяются end-to-end по пулу чекеров без жёсткого shadow-ban (контроль-проба держит высокую точность).
4. Возвращённые в `pending` 2110 + 699 (папка Barter) контактов перепроверены здоровыми резолверами.
5. `contact_check_worker` никогда не выбирает чекер с флагом `restricted`/`paused` (сейчас фильтрует только `role='checker' AND auth_status='ok'` — эта дыра позволила битому чекеру продолжать врать).

**Развилка решена (D-01):** управляемый пул чекеров (probe + кап + ротация + отдых), НЕ ленивый резолв при отправке.

Plans:
**Wave 1**
- [x] 14-01-PLAN.md — migration 034 (confidence/source на contacts) + ORM mirror + CONTACT_CHECK_* env-knobs + Wave-0 RED test scaffold [RESV-02, RESV-06]

**Wave 2** *(blocked on Wave 1)*
- [x] 14-02-PLAN.md — RESV-05 selection fix (restriction/lifecycle gate) + RESV-04 mobile-first ordering + RESV-02 burst-cap + durable daily-cap + cooldown в contact_check_worker [RESV-05, RESV-04, RESV-02] [depends_on: 14-01]

**Wave 3** *(blocked on Wave 2 — same file: contact_check_worker.py)*
- [x] 14-03-PLAN.md — RESV-01 health-probe (≥2-miss detect + Phase-10 restriction mark) + RESV-06/D-09 suspect rollback + confidence finalization + RESV-02/D-02 importContacts fallback + DeleteContacts cleanup [RESV-01, RESV-06, RESV-02] [depends_on: 14-02]

**Wave 4** *(blocked on Wave 3 — D-03 activation gate, human-verify)*
- [x] 14-04-PLAN.md — ❌ FAILED (re-activation live-smoke: оба чекера зафлудились на 0% mobile, прод откатили к baseline) → передано в Phase 17 [RESV-04, RESV-07] [depends_on: 14-02, 14-03] (completed 2026-06-30)

**Wave 5** *(gap-closure — 14-04 live-smoke FAILED; blocked on Wave 3 worker fix)*
- [x] 14-05-PLAN.md — Gap A: inline flood/throttle-aware finalization in contact_check_worker (FloodWait or anomalous all-empty batch → roll back to pending, never not_registered/high, degrade checker inline + leave rotation, N=0-healthy safe-stop) + RED-first tests [RESV-01, RESV-02, RESV-06] [depends_on: 14-03]

**Wave 6** *(gap-closure — blocked on Wave 5 fix; human-verify gate)*
- [x] 14-06-PLAN.md — Gap B: read-only diagnostic spike (phone-resolve pool-wide? @username viable? our-rate triggers throttle?) → findings note + conditional GO/NO-GO (phone-resolve жив 96–98%, @username мёртв) [RESV-01, RESV-02] [depends_on: 14-05]

**Wave 7** *(gap-closure — benign per-checker rest after batch)*
- [x] 14-07-PLAN.md — per-checker `checker_rest_until` (mig 035) + CONTACT_CHECK_REST_SECONDS knob: чекер уходит на отдых после батча, ротация чередует ≥2 здоровых; НЕ трогает restriction/lifecycle [RESV-02] [depends_on: 14-05]

> **Gap-closure note (2026-06-26):** 14-04 Task-4 live smoke activated the 2 "healthy" parked checkers and both flooded instantly at 0% mobile (`checked=20..30 reg=0 flood=True`). Root cause: on `flood=True` the worker finalized empty resolves as `not_registered`/high-confidence before the decoupled ≥2-miss probe could flag the checker. Prod rolled back to baseline (0 active checkers). 14-05 fixes the finalization inline; 14-06 diagnoses whether re-activation is even viable. RESV-04 (re-check 14k/2110/699) + full re-activation DEFERRED — not closed by this gap-closure. Evidence: `.planning/notes/checker-false-negatives.md` §"Часть 2".

### Phase 15: Account Warmup via Inter-Account AI Chat

**Goal:** Продуктизировать взаимный AI-прогрев: аккаунты workspace переписываются между собой через AI, чтобы безопасно набирать «возраст»/активность без риска бана. Отдельная вкладка в UI (старт/стоп, расписание, интенсивность, статус каждого аккаунта). **Ключевое требование — изоляция от основного флоу аутрича:** прогрев не должен перехватывать входящие/исходящие реальных кампаний, не садить лимиты sender'ов, не триггерить AI-ответчик (именно это убило похожую фичу в старой `telegram-api` — её пришлось остановить).

**Контекст (что уже есть в коде):**
- Текущий проект: [`app/services/warmup.py`](app/services/warmup.py) — background-воркер взаимного AI-прогрева (тик 30с, окно 09–20 МСК, уровень по дням в пуле) + [`app/routers/warmup.py`](app/routers/warmup.py). Базовый движок есть, нет продуктовой UI-вкладки и явной изоляции от кампаний.
- Старая остановленная `telegram-api`: [`/root/apps/telegram-api/app/services/warmup.py`](/root/apps/telegram-api/app/services/warmup.py), [`/root/apps/telegram-api/app/routers/warmup.py`](/root/apps/telegram-api/app/routers/warmup.py) (398 строк), `bot_chat.py` — прототип; **остановлен т.к. влиял на основной флоу** (см. CLAUDE.md). Изучить как референс + понять, почему конфликтовал.

**Requirements**: WARM-01..15 (derived this phase, see 15-CONTEXT.md decisions)
**Depends on:** Phase 14
**Plans:** 4/4 plans complete

Plans:
- [x] 15-01-PLAN.md — Wave-0 foundation: migration 037 warmup_settings + ORM, RED isolation/router/worker test stubs, WARM-01..15 in REQUIREMENTS.md
- [x] 15-02-PLAN.md — deterministic per-workspace internal short-circuit in listener (isolation: WARM-01/02/04/15)
- [x] 15-03-PLAN.md — engine: enabled-gate + restriction-skip + per-workspace content defaults (WARM-03/06/10/12/13/14)
- [x] 15-04-PLAN.md — workspace-scoped router rewrite + is_active fix + settings/master-toggle + enriched status (WARM-05/07/08/09/11)

### Phase 16: RAG Knowledge Bases for Agents

**Goal:** Дать пользователю RAG-базу знаний для работы AI-агентов. Логика: (1) отдельная вкладка **Knowledge Bases** в UI, где пользователь создаёт изолированные KB и загружает в каждую свои данные; (2) созданную KB можно подключить **на уровне агента** — агент ходит в неё по необходимости (retrieval при генерации ответа). KB — workspace-scoped, привязка многие-ко-многим (агент может иметь несколько KB, KB можно переиспользовать между агентами).

**Scope (предварительно — уточнить в /gsd:discuss-phase 16):**

- **Модель данных:** `knowledge_bases` (workspace-scoped) + `kb_documents` (загруженные источники) + `kb_chunks` (чанки с эмбеддингами) + связь `agent_knowledge_bases` (M:N агент↔KB).
- **Загрузка данных:** UI-загрузка файлов/текста в KB → парсинг → чанкинг → эмбеддинги. Форматы и пайплайн ingest — решить в discuss.
- **Хранилище векторов:** pgvector в существующем PostgreSQL vs внешний (решить — D-NN). Предпочтительно остаться в той же БД.
- **Retrieval на уровне агента:** при генерации ответа listener/AI-сервис подтягивает релевантные чанки из подключённых KB и инжектит в контекст. «По необходимости» — уточнить: всегда retrieval vs tool-call/by-trigger.
- **Изоляция:** KB строго workspace-scoped (мультитенантность), без утечки между workspace.

**Acceptance (предварительно):**

- Пользователь создаёт KB, загружает в неё данные, видит статус индексации.
- KB подключается к агенту; в ответах агента видно влияние знаний из KB (retrieval работает).
- KB изолированы по workspace; один агент может иметь несколько KB и наоборот.

**Requirements**: KB-01, KB-02, KB-03, KB-04, KB-05, KB-06 (derived during /gsd:plan-phase 16 — see REQUIREMENTS.md §RAG Knowledge Bases; tracked via decisions D-01..D-12)
**Depends on:** Phase 3 (Agents / AIContext — точка привязки KB к агенту), Phase 1 (Workspace — scoping)
**Plans:** 5/5 plans complete

Plans:
**Wave 1**
- [x] 16-01-infra-data-model-test-scaffold-PLAN.md — deps (pgvector/tiktoken/pypdf/python-docx) + db image swap pgvector/pgvector:pg16 (prod+test, command preserved) + conftest CREATE EXTENSION vector + migration 041 (4 tables, HNSW) + ORM mirror + RED test scaffold [Wave 1, no deps] — KB-01..06 scaffold

**Wave 2** *(blocked on Wave 1)*
- [x] 16-02-ingest-pipeline-and-worker-PLAN.md — kb_ingest.py (extract/chunk-tiktoken/embed text-embedding-3-small) + KnowledgeIngestWorker (mirror ContactCheckWorker, idempotent re-index) + lifespan + config knobs [Wave 2, depends_on: 16-01] — KB-02, KB-03

**Wave 3** *(blocked on Wave 2 — parallel: router vs ai_engine, no file overlap)*
- [x] 16-03-api-endpoints-and-handoff-PLAN.md — workspace-scoped /api/v1/knowledge-bases router (CRUD + upload/paste 202 + list/reindex/delete docs + D-09 aggregate + manual search + agent attach/detach + reverse list) + schemas + openapi handoff regen [Wave 3, depends_on: 16-01, 16-02] — KB-01, KB-02, KB-03, KB-04
- [x] 16-04-search-tool-wiring-PLAN.md — kb_search.py (cosine query over attached KBs, workspace-filtered) + search_knowledge_base data-tool in ai_engine (gated on ≥1 KB, two-pass continuation, no status change) [Wave 3, depends_on: 16-01, 16-02] — KB-05, KB-06

**Wave 4** *(blocked on Wave 3 — frontend, human-verify)*
- [x] 16-05-frontend-surfaces-PLAN.md — sibling repo aimly-tg-outreach: Knowledge bases sidebar tab + list page + KB detail (D-09 header + 5 metrics + 4 tabs Documents/Search/Agents/Settings, poll-while-processing) + agent-editor KB multi-select + human UAT [Wave 4, depends_on: 16-03, 16-04] — KB-01..05 UI

### Phase 17: Sender-side resolve ladder with username capture and import fallback

**Goal:** Перестроить резолв так, чтобы **отправитель сам резолвил и дотягивался** до получателя, а **чекер стал чистым фильтром** «есть/нет». Чекер перестаёт выбрасывать `username` из ответа `ResolvePhone` и **сохраняет @username** (публичный, переносимый между аккаунтами — в отличие от per-account `access_hash`). На отправителе — **тройная лестница резолва**: (1) кэш per-sender → (2) `ResolveUsername` по @username, захваченному чекером (дёшево, безопасно, обходит приватность по телефону, не засоряет адресную книгу) → (3) `ImportContacts` **лениво, по одному перед отправкой** (не пачкой 50 с утра — пачка = burst у порога ~47–49; лимит 4/мин сам размазывает). Чужой `access_hash` не переиспользуется. Фолбэк на phone-резолв, если username сменился/исчез. Очистку кэша не делаем.
**Триггер (живой инцидент):** кампания «Barter - ВЭД хук» — 22 живых РФ-номера терминально упали на `ResolvePhone` несмотря на флаг registered/high/clean. Флаг ставил US-аккаунт (чужой резолв не переносится + US на РФ врёт), собственный `ResolvePhone` отправителя дал ложное «нет» (приватность или троттл), а в send-пути нет import-фолбэка. Дизайн-документ: `.planning/notes/sender-side-resolve-redesign.md`.
**Requirements**: SRLD-01, SRLD-02, SRLD-03, SRLD-04, SRLD-05, SRLD-06, SRLD-07, SRLD-08, SRLD-09 (derived during /gsd:plan-phase 17 — see REQUIREMENTS.md §Sender-side Resolve Ladder; tracked via decisions D-01..D-16)
**Depends on:** Phase 14 (Reliable Contact Resolution)
**Plans:** 4/4 plans complete

Plans:
**Wave 1**
- [x] 17-01-test-scaffold-PLAN.md — Wave-0 RED scaffold: SRLD-01..08 failing tests across test_checker/test_send/test_contact_check_worker/test_restriction_audit (no prod code) [Wave 1, no deps]

**Wave 2** *(parallel — checker.py vs telegram.py, no file overlap)*
- [x] 17-02-checker-username-capture-and-gated-read-PLAN.md — checker captures @username (D-06) + confidence-gated _lookup_cache (D-12) [Wave 2, depends_on: 17-01] — SRLD-01, SRLD-02, SRLD-07
- [x] 17-03-sender-resolve-ladder-PLAN.md — resolve_contact ladder cache→ResolveUsername→ImportContacts (drop sender ResolvePhone, D-01/D-02), import gate (D-03), stale-username fall-through (D-09), sender false-read gate (D-12) [Wave 2, depends_on: 17-01] — SRLD-03, SRLD-04, SRLD-05, SRLD-06, SRLD-07

**Wave 3** *(blocked on 17-03 — shares telegram.py::send_message)*
- [x] 17-04-block-capture-and-docs-PLAN.md — UserIsBlockedError capture → durable 'blocked' event + read-only block-rate endpoint (D-15/D-16) + CLAUDE.md country-hypothesis softening (D-10) [Wave 3, depends_on: 17-01, 17-03] — SRLD-08, SRLD-09

**NB:** Phase 17 adds 0 migrations — all storage reuses existing columns.

### Phase 18: Switchable LLM Provider in UI

**Goal:** Переключение LLM-модели прямо из UI: (1) выбор провайдера в настройках — пока только Claude (Anthropic) и OpenAI; (2) выбор конкретной модели + частичные настройки модели из нашего конфига (temperature, reasoning effort, token budget и т.п.); (3) подстановка API-ключа для работы выбранного провайдера; (4) AI-ответчик в чате работает через ту LLM, которая выбрана. Сейчас модель захардкожена через env `OPENAI_MODEL` (gpt-5-mini) — вынести в настройки.
**Requirements**: LLMP-01, LLMP-02, LLMP-03, LLMP-04, LLMP-05, LLMP-06, LLMP-07, LLMP-08, LLMP-09, LLMP-10, LLMP-11, LLMP-12 (derived during /gsd:plan-phase 18 — see REQUIREMENTS.md §Switchable LLM Provider in UI; tracked via decisions D-01..D-12)
**Depends on:** Phase 17
**Plans:** 5/5 plans complete

Plans:
**Wave 1**
- [x] 18-01-infra-migration-and-test-scaffold-PLAN.md — anthropic SDK + migration 044 (llm_settings table + llm_calls.provider/key_source) + ORM mirror + 7 RED test files [Wave 1, no deps] — LLMP-01/04/06/07/08/09/10/11/12 scaffold

**Wave 2** *(parallel — adapter package vs settings router, no file overlap)*
- [x] 18-02-provider-adapter-and-resolution-PLAN.md — app/services/llm/ adapter (base/capabilities/openai_provider/anthropic_provider/resolve) + Fernet key aliases [Wave 2, depends_on: 18-01] — LLMP-03/04/06/09/10/11
- [x] 18-03-settings-api-model-listing-test-connection-PLAN.md — workspace-scoped llm-settings router (GET masked / PATCH encrypt+D-03 gate / test-connection / live model filter) + main.py registration [Wave 2, depends_on: 18-01] — LLMP-01/02/03/04/05/08

**Wave 3** *(blocked on Wave 2 — wires the adapter into the hot answerer path)*
- [x] 18-04-wire-answerer-warmup-and-logger-PLAN.md — route generate_response (3 call sites) + warmup through the adapter + D-06 fallback + llm_logger provider/key_source; Whisper/embeddings stay platform (D-12) [Wave 3, depends_on: 18-02, 18-03] — LLMP-06/07/11/12

**Wave 4** *(frontend + handoff, human-verify)*
- [x] 18-05-openapi-handoff-and-frontend-settings-PLAN.md — regenerate openapi/types + sibling-repo AI/LLM Settings section (provider/model/key/knobs + green corridor + Test connection) + human UAT [Wave 4, depends_on: 18-03, 18-04] — LLMP-03/05/08/09/10/11

### Phase 19: No Reply Follow-Up and Auto-Finish

**Goal:** Contacts we messaged and who haven't replied get a "no reply" state; campaigns gain an Enable Follow Up toggle with a user-defined ping interval and an auto-finish after N hours without reply, configurable in the campaign create/edit form.
**Requirements**: NORP-01..NORP-13
**Depends on:** Phase 18
**Plans:** 5/5 plans complete

Plans:
- [x] 19-01-PLAN.md — Migration 045 (no_reply status CHECK + conversations.pings_sent + 4 campaign follow-up columns) + ORM mirrors + RED test scaffold (Wave 1)
- [x] 19-02-PLAN.md — Campaign follow-up API fields (bounds) + ai_engine.generate_followup_ping (Wave 2)
- [x] 19-03-PLAN.md — Listener revert no_reply→active + cancel pending pings (D-03/D-17) + queue pre-send replied-since guard (Wave 2)
- [x] 19-04-PLAN.md — FollowUpWorker (timer state machine: no_reply flip, ping, auto-finish, webhook reason=no_reply) + lifespan + config knob (Wave 3)
- [x] 19-05-PLAN.md — openapi.json regen + campaign form Follow Up block (cross-repo, human-verify) (Wave 4)

### Phase 20: Account Profile Management

**Goal:** Editable Telegram account profile (name, username, bio, photo, linked email if possible, 2FA) from the account edit view, plus richer account cards (photo, name, username, phone, update/delete/reauth) on the accounts list page.
**Requirements**: PROF-01, PROF-02, PROF-03, PROF-04, PROF-05, PROF-06, PROF-07, PROF-08, PROF-09 (derived during /gsd:plan-phase 20 — see REQUIREMENTS.md §Account Profile Management; tracked via decisions D-01..D-14)
**Depends on:** Phase 19
**Plans:** 5/5 plans complete

Plans:
**Wave 1**
- [x] 20-01-foundation-schema-and-test-scaffold-PLAN.md — migration 047 (5 cached-profile columns) + ORM mirror + Pydantic schemas + Wave-0 RED test scaffold [Wave 1, no deps] — PROF-01

**Wave 2** *(blocked on 20-01 — shared telegram.py/senders.py spine)*
- [x] 20-02-profile-identity-and-guardrail-PLAN.md — update_profile/check_username/set_username + PATCH /profile + /username-check + guardrail (D-08 hard-block / D-09 advisory) + onboarding cache [Wave 2, depends_on: 20-01] — PROF-02, PROF-03, PROF-08

**Wave 3** *(blocked on 20-02 — shares telegram.py/senders.py)*
- [x] 20-03-photo-and-resync-PLAN.md — set/delete_profile_photo + resync_profile + photo upload/delete/serve + resync endpoints (D-08/D-11/D-12) [Wave 3, depends_on: 20-02] — PROF-04, PROF-06, PROF-07

**Wave 4** *(blocked on 20-03 — shares telegram.py/senders.py)*
- [x] 20-04-two-fa-and-recovery-email-PLAN.md — edit_2fa password path + raw two-request recovery-email confirm flow + 3 endpoints (D-03/D-04/D-05) [Wave 4, depends_on: 20-03] — PROF-05

**Wave 5** *(frontend + handoff, human-verify)*
- [x] 20-05-frontend-and-handoff-PLAN.md — openapi/types regen + enriched accounts.tsx (row/kebab/two-section modal/guardrails) + human UAT (cross-repo) [Wave 5, depends_on: 20-02, 20-03, 20-04] — PROF-09

### Phase 21: Bulk Telegram account import via session JSON upload in UI

**Goal:** Import already-authorized Telegram accounts into a workspace by uploading vendor-format **pairs** `<phone>.json` + `<phone>.session` through the UI, with bulk (multi-account) upload support — bypassing the phone/SMS onboarding flow. The `.session` is a live Telethon SQLite session (auth_key present) that must be converted to our encrypted StringSession storage; the `.json` carries the client fingerprint (app_id/app_hash/device/sdk/app_version/lang) + optional proxy/2FA. **Key risk to design around:** our reconnect currently forces one hardcoded global api_id/api_hash + `_CLIENT_FINGERPRINT` — reconnecting an imported session with a different fingerprint than the one that created it risks a Telegram security-flag / forced re-login. See `21-NOTES.md` for the grounded file analysis and codebase findings.
**Requirements**: TBD
**Depends on:** Phase 20
**Plans:** 0 plans

Plans:
- [ ] TBD (run /gsd:plan-phase 21 to break down)

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
| 6. Admin Master Bot | 0/2 | Deferred to v2 | - |
| 7. Unified Freeze Policy | 0/1 | Planned (1 plan, Wave 1) | - |
| 8. Pool Management & Even Distribution | 4/4 | Complete   | 2026-06-23 |
| 9. Cold-Contact Failover | 2/2 | Complete   | 2026-06-24 |
| 10. Pool Visibility & Restriction Audit (optional) | 4/4 | Complete    | 2026-06-24 |
| 14. Reliable Contact Resolution | 6/7 | Closed (superseded → Phase 17) | 2026-06-30 |
| 15. Account Warmup via Inter-Account AI Chat | 4/4 | Complete   | 2026-06-29 |
| 16. RAG Knowledge Bases for Agents | 5/5 | Complete    | 2026-06-30 |
| 17. Sender-side Resolve Ladder | 4/4 | Complete    | 2026-06-30 |

**Total: 7 phases (incl. 02.1 hardening), 23 plans, 59 requirements mapped + 9 CR findings traced, 0 unmapped ✓**
**Post-v1 block (Sender Pool Resilience): +4 phases (7–10); Phase 7 planned (1 plan, FRZ-01..05).**

## Backlog

### Phase 999.1: Bulk account profile editing (BACKLOG)

**Goal:** [Captured for future planning]
**Requirements:** TBD
**Plans:** 0 plans

Select multiple senders and batch-update photo, first name/last name, description (bio), and username in one action. Raised by user during Phase 20 (account-profile-management) human-verify checkpoint; explicitly scoped OUT of Phase 20 by agreement (Phase 20 covers only single-account profile editing, PROF-01..09). Needs its own design pass: how to handle partial failures across a batch (some accounts hit the 1h per-field cooldown, some fail Telegram-side), whether to preview changes before applying, and whether fields are set identically for all selected accounts or templated (e.g. per-account username can't literally be identical since Telegram usernames are globally unique).

Plans:
- [ ] TBD (promote with /gsd:review-backlog when ready)
