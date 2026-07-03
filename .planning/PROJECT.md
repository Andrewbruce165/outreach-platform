# Outreach Platform

## What This Is

SaaS-платформа для автоматизации Telegram-аутрича через личные аккаунты менеджеров с AI-ответчиком.
Клиенты (компании) регистрируются, создают workspace, подключают свои Telegram-аккаунты, загружают базу контактов, настраивают AI-агентов и запускают **кампании** — платформа сама рассылает сообщения и отвечает на входящие через GPT, передаёт лиды наружу через webhook.
Brownfield-проект: базовая механика (очередь, rate limiting, AI-ответчик, онбординг) уже реализована, ключевой пробел — мультитенантность и модель кампании.

## Core Value

Клиент подключил аккаунт и через 10 минут первая кампания запущена — без программистов, без DevOps, без настройки серверов.

## Current Milestone: v1.0 First External Client

**Goal:** Первый платящий внешний клиент самостоятельно регистрируется, настраивает workspace, запускает кампанию и видит результаты.

**Target features:**
- Мультитенантная схема + magic-link auth (Supabase)
- TG-аккаунты + база контактов с папками + проверка в TG
- AI-агенты как переиспользуемые шаблоны (контекст / задача / тон / FAQ)
- **Кампании** как первичная сущность: связывают агента + аккаунты + папку + сигналы + webhook + tools + расписание
- Inbox с фильтром по кампании + аналитика по уровням (workspace / campaign / agent / TG-account)
- Telegram-бот для админских уведомлений (ручник, ошибки аккаунтов)

## Requirements

### Validated

- ✓ Отправка сообщений через PostgreSQL-очередь с rate limiting (4/мин, 20/час, 150/день) — existing
- ✓ AI-ответчик: Telethon listener + дебаунс 3–5 мин + GPT-4o-mini — existing
- ✓ Онбординг Telegram-аккаунта через телефон/SMS/2FA/QR — existing
- ✓ AI-контексты: промпт, тон, правила, FAQ, auto_pause_triggers (модель в БД) — existing
- ✓ Прогрев аккаунтов (WarmupWorker) — existing
- ✓ Proxy pool per-sender (Decodo SOCKS5) — existing
- ✓ Проверка телефонов через checker-аккаунт — existing
- ✓ Ротация отправителей: детерминированный маппинг (context_id, phone) → sender — existing
- ✓ Шифрование Telegram-сессий (Fernet) — existing
- ✓ Базовый фронт на Lovable: онбординг, inbox, настройка AI, статистика — existing
- ✓ Webhook + function calling (частично) — existing, требует аудита и переноса на уровень кампании

### Active

**Multitenancy & Auth (Phase 1):**
- [ ] Модель Workspace: tenant-изоляция всех данных через workspace_id
- [ ] Вход через magic link (Supabase Auth)
- [ ] FastAPI верифицирует Supabase JWT, извлекает workspace_id
- [ ] Workspace API-ключ для n8n/интеграций
- [ ] Полный рерайт API-эндпоинтов

**TG Accounts & Contacts (Phase 2):**
- [ ] Онбординг TG-аккаунта в workspace (sender)
- [ ] Per-sender настройки: rate limits, прокси, статус
- [ ] База контактов с папками (несколько списков внутри workspace)
- [ ] Проверка контакта в Telegram при импорте (через checker)
- [ ] Поля контакта: phone, username, full_name, source, custom (JSONB)
- [ ] Загрузка CSV + push через Workspace API

**Agents (Phase 3): ✓ Complete (2026-05-21)**
- [x] Агент как переиспользуемый AI-шаблон workspace-level
- [x] Настройка агента: контекст, задача, тон, FAQ
- [x] CRUD списка агентов workspace
  - Validated in Phase 3: migration 015 cleaned `ai_contexts` schema (D-02), `app/routers/agents.py` exposes 6 workspace-scoped endpoints under `/api/v1/agents` (incl. duplicate + hard delete), `app/routers/send.py` rewritten under AuthDep with explicit `ai_context_id` in body. Phase 4 carry-overs: real `campaign_count` query and DELETE-block on active-campaign attachment (Phase 4 Campaign FK).

**Campaigns (Phase 4): ✓ Complete (2026-05-22)**
- [x] Модель Campaign: agent + senders + folder + status
- [x] Расписание кампании (рабочие часы + старт/стоп даты)
- [x] Сигналы кампании: «передать лид», «передать на менеджера», «финиш диалога»
- [x] **Webhook кампании** — URL для передачи событий (лид/финиш/ручник)
- [x] **Tools кампании** — function calling спецификация
- [x] Запуск / пауза / стоп кампании + досыпание контактов
- [x] Переменные `{{имя}}, {{username}}, {{source}}, {{custom.X}}` в тексте
- [x] Очередь учитывает campaign_id
  - Validated in Phase 4: migration `016_phase4.sql` adds `campaigns` + `campaign_senders` + `campaign_contact_assignments`, drops `context_contact_assignments`, extends `conversations.status` CHECK, NULLable `message_queue.campaign_id` with `ON DELETE SET NULL` (Q1 override of D-16), VARCHAR(20)+CHECK for `campaigns.status` instead of PG ENUM (Q6 override of D-04). `app/routers/campaigns.py` exposes CRUD + 5 lifecycle endpoints + duplicate + sender-lock check. Global schedule constants (`MOSCOW_TZ`, `WORK_HOUR_*`) removed from `queue.py` — replaced by per-campaign `zoneinfo.ZoneInfo` + `work_days_mask` JOIN gate. `CampaignEnqueueWorker` (30s tick) renders templates ({{имя}}, {{username}}, {{source}}, {{custom.X}} + RU aliases) and tops up queue with `campaign_id` set. Built-in tools (mark_as_lead, transfer_to_manager, finish_conversation) wired into `ai_engine` with priority dispatch finish>handoff>lead and Q3 text+tool_call farewell handling. `webhook_notify.notify_signal` fires per-campaign URLs (C-01 uniform payload). All 10 TODO(phase-4) markers closed.

**Inbox & Analytics (Phase 5): ✓ Complete (2026-05-22)**
- [x] Inbox с фильтром по кампании / агенту / аккаунту
- [x] Ручной перевод диалога в режим «менеджер»
- [x] AI не отвечает системным ботам (SpamBot и др.)
- [x] Метрики по уровням: workspace / campaign / agent / TG-account
- [x] Лог запросов в OpenAI на уровне диалога
  - Validated in Phase 5: migration `017_phase5.sql` extends `conversations.status` CHECK to 7 values (adds `bot_ignored`), creates `llm_calls` (15 cols + 2 indexes) and 3 composite indexes on conversations. `app/routers/conversations.py` rewritten under `auth_dep` + workspace scope with 9 endpoints (list / detail / messages / PATCH / enable-ai / disable-ai / send / DELETE / llm-calls). `app/routers/analytics.py` exposes 4 read-only endpoints (workspace / campaigns / agents / senders) with identical `AnalyticsCards` schema and `_compute_cards` helper (raw COUNTs, `bot_ignored` excluded). `app/services/listener.py` adds proactive bot filter (`getattr(sender, 'bot', False)`) with delegation to `_handle_antispam_signal` for hardcoded `ANTISPAM_BOT_IDS`; `app/services/queue.py` adds pre-send race guard (D-04). `app/services/llm_logger.py` provides never-raise `log_llm_call()` coroutine with denormalisation resolve; `app/services/ai_engine.py` wraps both OpenAI `chat.completions.create` calls (points #1 and #2 = tool result summarisation). Awaiting server-side pytest + live smoke (05-HUMAN-UAT.md).

**Admin Master Bot (Phase 6):**
- [ ] TG-бот workspace для админских уведомлений
- [ ] Уведомление при срабатывании «передать на менеджера»
- [ ] Уведомление при ошибке аккаунта (logout / flood / etc.)

### Out of Scope

| Feature | Reason |
|---------|--------|
| Биллинг / платёжный шлюз | Отдельная интеграция после v1, не блокирует первого клиента |
| Мобильное приложение | Web-first |
| OAuth (Google/GitHub) | Magic link через Supabase достаточно для v1 |
| Real-time чат между операторами | Telegram inbox достаточен |
| Другие мессенджеры (WhatsApp, Instagram) | Платформа Telegram-специфична |
| Собственный AI (fine-tuning) | GPT-4o-mini достаточно для v1 |
| Многошаговые follow-up последовательности | v2 — ADVN-01 (see seed `nudge-and-followup-sequences.md`) |
| A/B тестирование текстов | v2 — ADVN-02 |
| Расписание по тайм-зонам контакта | v2 — ADVN-03 |
| Несколько пользователей в одном workspace (инвайт + роли + управление) | v2 — TEAM-01..02 (see seed `multi-user-workspace.md`) |
| Экспорт аналитики в CSV | v2 — карточек метрик в v1 достаточно |
| Self-serve редактирование профиля TG-аккаунта (имя/bio/фото/username) | v2 — PROF-01 (see seed `account-profile-self-serve.md`) |
| Admin Master Bot — уведомления ручника + ошибок аккаунтов (deferred Phase 6) | v2 — ADMN-01..03 (see seed `admin-master-bot.md`) |
| Редактирование данных workspace (имя/аватар/soft-delete/экспорт) | v2 — WSPC-01 (see seed `workspace-metadata.md`) |
| Несколько workspace на одного пользователя + UI switcher | v2 — WSPC-02 (see seed `multi-workspace-per-user.md`) |
| Свой OpenAI ключ на workspace (Bring Your Own Key) | v2 — BYOK-01 (see seed `byo-openai-key.md`) |
| Workspace-level настройки очереди (вынести захардкоженные числа в UI) | v2 — QUEUE-01 (see seed `queue-settings-workspace.md`) |
| Скорость ответа AI (debounce) — workspace/agent override | v2 — REPLY-01 (see seed `ai-reply-speed.md`) |
| Re-engagement nudge после read+silent (single-shot) | v2 — NUDGE-01 (see seed `nudge-and-followup-sequences.md`) |
| Обработка входящих от ранее незнакомых пользователей (AI/ignore/notify) | v2 — INBD-01 (see seed `inbound-from-unknown.md`) |
| AI-ассистент при заполнении текстовых полей агента/кампании | v2 — AIUX-01 (see seed `ai-assist-content-editor.md`) |
| Кастомные переменные при загрузке базы контактов (UX над готовым backend) | v2 — CVAR-01 (see seed `custom-contact-variables.md`) |

## Context

**База кода:** унаследована от `/root/apps/telegram-api` — внутреннего инструмента AGS Foods. Вся бизнес-логика работает; кодовая база async-first (asyncio + AsyncSession + Telethon). Миграции — raw SQL, нумерация 012_, 013_..., всегда IF NOT EXISTS.

**Терминология:**
- **Sender** (БД термин) = **TG-аккаунт** (UI термин) — физический подключенный Telegram-аккаунт с сессией
- **Agent** (UI термин) = AI-шаблон workspace-level (БД: `ai_contexts` — не переименовываем, чтобы не тащить миграцию)
- **Campaign** — объект-обёртка над рассылкой: связывает агента + аккаунты + папку + сигналы + webhook + tools + расписание

**Текущая auth:** единственный глобальный API-ключ (`X-API-Key` header). `python-jose` уже в `requirements.txt` — JWT не используется, но библиотека готова.

**Критические эмпирические константы:** rate limits (4/мин, 20/час, 150/день) подобраны под реальный Telegram anti-spam — менять только после явного обсуждения. Рабочие часы (09–20 МСК) переезжают с уровня сервиса на уровень кампании — клиент задаёт сам.

**Фронт:** Lovable (React, отдельный репо). Supabase Auth выбран потому что Lovable нативно с ним интегрируется.

**Хостинг:** DigitalOcean VPS, Docker Compose (3 сервиса: db, api, listener). Деплой ручной через SSH.

**Существующий webhook + tools:** в коде есть `webhook_functions` — частичная реализация function calling и передачи данных наружу. На уровне модели сейчас привязано к sender/AIContext. В v1 переезжает на уровень кампании (Phase 4 начинается с аудита).

## Constraints

- **Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 async / PostgreSQL 16 / Telethon — не менять без явного решения
- **Migrations:** только raw SQL в `migrations/`. Никогда Alembic.
- **Async:** никаких `time.sleep()`, синхронных `requests`, `print()` вместо `logging`
- **Rate Limits:** дефолтные значения (4/мин, 20/час, 150/день) хранятся как default на уровне sender'а; менять дефолты только после обсуждения
- **Retry / FloodWait:** не ломать логику без явной просьбы
- **API Endpoints:** полный рерайт — старые эндпоинты остаются в telegram-api (prod), в outreach-platform новые
- **Security:** сессии зашифрованы, API_KEY не в логах

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Magic link (Supabase Auth) | Нет паролей — проще для клиента; Lovable нативно с Supabase | — Pending |
| **Campaign как первичная сущность** | Запуск рассылки = объект с состоянием (статус / расписание / связи). Без него рассылка «глобальная», нельзя изолировать аналитику и сигналы. | — Pending |
| **Agent отвязан от sender'а** | Один AI-шаблон переиспользуется в разных кампаниях с разными аккаунтами. Раньше AIContext был привязан к sender — мешает переиспользованию. | — Pending |
| **Webhook + tools на уровне кампании, не агента** | Агент описывает «как говорить», кампания решает «куда передавать данные и какими инструментами пользоваться». Тот же агент в разных кампаниях может иметь разные webhook'и. | — Pending |
| **Сигналы (лид/менеджер/финиш) на уровне кампании** | Сигналы зависят от бизнес-цели рассылки, а не от стиля разговора. В LLM-промпт передаются вместе с агентским контекстом. | — Pending |
| **Папки в базе контактов** | Клиенты ведут несколько списков (по проектам / источникам / городам). Папка — таргет кампании. | — Pending |
| **Rate limits per-sender** | Telegram anti-spam смотрит на аккаунт. Sender в одной кампании за раз — лочится. | — Pending |
| **Расписание на уровне кампании** | Рабочие часы и дни — бизнес-параметр конкретной рассылки, не sender'а. | — Pending |
| Per-agent настройки вместо per-workspace | Каждый TG-аккаунт имеет свои лимиты/прокси/статус — тонкая настройка | — Pending |
| Полный рерайт API-эндпоинтов | Старые используются в telegram-api; нельзя менять — пишем новые с нуля | — Pending |
| PostgreSQL-очередь вместо Redis/Celery | Упрощает деплой (один меньше сервис), достаточно для текущих объёмов | ✓ Good |
| Brownfield: не переписывать логику, добавить тенантность | Рабочий код уже есть; переписываем только слой API и модели | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-07-03 — Phase 19 (No Reply Follow-Up and Auto-Finish) complete: migration 045 extends `conversations.status` CHECK with `no_reply` (bot_ignored preserved) + `pings_sent` counter + 4 campaign columns (`follow_up_enabled`/`follow_up_interval_hours`/`follow_up_max_pings`/`auto_finish_hours`, API-layer Pydantic bounds not DB CHECK, D-12); `ai_engine.generate_followup_ping` reuses prompt assembly + Phase-18 provider (tools=None, D-07); listener reverts `no_reply`→`active` + cancels pending pings on genuine reply before the AI-dispatch check (D-03, Pitfall-4 safe) with a queue pre-send replied-since guard gated on `extra_data.kind=='followup'` (D-17 double guard); `FollowUpWorker` timer state machine (`FOR UPDATE OF c SKIP LOCKED`) applies auto-finish-first/ping-else anchored to last outbound message, fires the finish webhook with `reason='no_reply'`, registered in the FastAPI lifespan with a `follow_up_tick_seconds` knob; openapi.json regenerated offline + Follow Up settings block shipped in the sibling campaign create/edit form, human-verified (persistence + bounds; live E2E ping/auto-finish loop left as optional follow-up). 9/9 must-haves, NORP-01..13 closed; full suite 939 passed (1 pre-existing out-of-scope WARM-14). Not yet deployed to prod (api+listener rebuild pending). Prior: Phase 18 (Switchable LLM Provider) complete: workspace-scoped OpenAI↔Anthropic switch (D-01, single `llm_settings` row per workspace, migration 044) with Fernet-encrypted BYO key (D-04) behind a KEY_REQUIRED gate (D-03); `app/services/llm/` provider-adapter package (LLMProvider protocol + OpenAI/Anthropic adapters with role coalescing, capability gates D-09, green-corridor clamps D-10, key-level-error fallback classifier D-06); settings API (GET masked / PATCH / test-connection D-05 / live family-filtered model list D-08) + frontend AI/LLM Settings tab in the sibling repo; answerer (all 3 call sites) + warmup routed through the per-workspace resolved provider with provider/key_source persisted to `llm_calls` (D-07); Whisper + KB embeddings pinned to the platform OpenAI singleton (D-12). Live human-verified end-to-end: real Anthropic key → `claude-sonnet-5` replies in dialogue, `llm_calls` rows `provider='anthropic', key_source='byok'`. 2 live Anthropic API bugs found & fixed during UAT (thinking×temperature exclusivity; Claude-5-generation adaptive thinking via `output_config.effort` extra_body — SDK 0.115.1 lacks the kwarg). 25/25 must-haves, LLMP-01..12 closed; full suite 902 passed (1 pre-existing out-of-scope WARM-14). api+listener deployed 2026-07-02. Prior: Phase 17 (Sender-side Resolve Ladder) complete: resolve responsibility moved onto the SENDER — checker is now a pure exist/no-exist filter that ALSO captures the transferable `@username` (vs per-account `access_hash`) on both `ResolvePhone` and `ImportContacts` paths and confidence-gates its `is_registered=false` cache reads (SRLD-01/02/07); `telegram.py::resolve_contact` rebuilt as a 3-tier ladder cache(access_hash) → `ResolveUsername`(captured @username) → lazy per-send `ImportContacts` (gated on checker verdict `registered`, no `DeleteContacts` on the sender), with the sender's own `ResolvePhone` REMOVED entirely and a stale-username fall-through to import instead of finalizing False (SRLD-03..06, fixes the live «Barter - ВЭД хук» incident where 22 live RU mobiles died on ResolvePhone); `UserIsBlockedError` captured in send_message/send_file as a durable per-sender `sender_restriction_events` `blocked` event with a read-only `GET /senders/{slug}/block-rate` aggregate (no control loop, SRLD-08/D-15/D-16); and the unproven US-cannot-resolve-RU country claim reframed to a hypothesis in CLAUDE.md (SRLD-09/D-10). 0 migrations (reuses contacts.tg_username_resolved + tg_probe_state/tg_confidence + sender_restriction_events). 9/9 must-haves verified; full suite 850 passed via test-overlay (1 out-of-scope WARM-14 failure from parallel uncommitted Phase 15 warmup). SRLD-01..09 closed; subsumes Phase 14's deferred SC #3/#4 by removing the dependency on a dedicated checker pool. Prior: Phase 16 (RAG Knowledge Bases for Agents) complete: pgvector KB store (migrations 041 tables+HNSW / 042 id server_defaults / 043 FTS GIN); ingest worker (extract pdf/docx/txt → tiktoken chunk → embed text-embedding-3-small, FOR UPDATE SKIP LOCKED, idempotent re-index); HYBRID retrieval (cosine OR full-text `simple` keyword, workspace+kb_id double-filter KB-06); agent-level M:N attach with on-demand `search_knowledge_base` data-tool gated on ≥1 KB + a `<knowledge_base>` RAG-awareness directive; full UI (Knowledge bases tab/list/detail with 4 tabs + agent multi-select with one-step deferred-attach on create). Live human-verified end-to-end (real PDF → indexed → agent answered a question by searching the KB, workspace-isolated). 6/6 must-haves. 7 defects found & fixed during human-verify (pgvector init-ordering, NUL-strip, kb id NotNull, search threshold, hybrid search, chunk sizing, RAG-awareness). KB-01..06 closed. Prior: 2026-06-24 — Phase 10 (Pool Visibility & Restriction Audit) complete: migration 030 append-only `sender_restriction_events` table + migration 031 (flood_wait category); `record_restriction_event` dual-mode helper + activity slice (HLTH-01/02); 5 write-points wired in same tx as `senders.restriction_status` UPDATE (queue PEER_FLOOD/ACCOUNT_FROZEN, listener antispam+reconcile, PRIVACY_RESTRICTED); D-01 extension gate (only on SpamBot-quoted date, not recheck bumps); workspace-scoped `GET /senders/{slug}/restriction-events` history (HLTH-03); `pool_health {active,paused,total,earliest_resume_at}` aggregate on campaign response (POOLV-01); per-sender `restriction_status`/`restricted_until` enrichment (POOLV-02); frontend 3-state pool badge + restriction-event list committed in sibling repo, browser UAT pending deploy (POOLV-03/04). 7/7 backend must-haves verified. Prior: Phase 07 (Unified Freeze Policy) complete: `listener._handle_antispam_signal` rewritten onto the PEER_FLOOD soft-restriction pattern — antispam warning now PAUSES the sender's pending queue (+24h, reconcile auto-resumes) instead of terminally failing, flags `restriction_status='spam_limited'` with a `<> 'frozen'` precedence guard, and no longer disables `ai_enabled` so established dialogues keep replying (FRZ-01..03); rotation candidate filter gains `AND s.restriction_status='none'` so new cold contacts skip restricted senders (FRZ-04); worker pre-send skip asserted by regression (FRZ-05). No migration (028 pre-existing). Existing-assignment failover (CR-01) deferred to Phase 9. Prior milestone footer: 2026-05-23 — Phase 05.1 (Lovable UI v1) complete: migration 018 (telemetry_events + 11 agent v2 cols + 4 campaign v2 cols), CORS regex for *.lovableproject.com + HS256 pin comment, router widening (campaigns /stop alias + /auto-fill stub, senders /pause /resume, agents v2 passthrough, ai_engine COALESCE), 4 new endpoints (/analytics/funnel Sankey + /analytics/llm + /telemetry/events + /telemetry/core-value), lovable-handoff/ bundle (8 docs + 2 CI scripts) + UI-SPEC reconciliation, Core Value E2E pytest (<600s) + HUMAN-UAT mapping for the 7 Phase 5 items. 14/15 must-haves auto-verified; 4 infra-gated items in 05.1-HUMAN-UAT.md (handoff export, server-side pytest, Lovable UI walkthrough, Supabase HS256 dashboard setting). Phase 5 (Inbox & Analytics) complete: migration 017 + conversations router rewrite (9 endpoints under auth_dep) + analytics router (4 read-only endpoints) + listener bot filter + queue pre-send race guard + never-raise LLM call logger + per-conversation LLM call log endpoint.*
