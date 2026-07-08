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
- ✓ Self-serve редактирование профиля TG-аккаунта: имя/фамилия/bio/username/фото + 2FA-пароль + recovery-email, per-field 1h guardrail на username/фото — Validated in Phase 20 (PROF-01..09)
- ✓ Bulk-импорт уже авторизованных TG-аккаунтов через загрузку ZIP пар `<phone>.json`+`<phone>.session` (vendor-формат) — offline SQLite→StringSession, per-account client fingerprint для реконнекта без re-login, Fernet-зашифрованный 2FA, per-file partial success, async job+worker+status, two-step UI — Validated in Phase 21 (IMPT-01..10)
- ✓ Вложение одного файла к первому сообщению кампании (auto-media доставка) + невидимая анти-спам вариация текста опенера (обход наивного дедупа Telegram) — Validated in Phase 24. Открытый пункт (1) doc-only живой смоук-тест не подтверждал фото-рендеринг для .jpg — закрыт в Phase 23 (тот же send-file код путь, живой .jpg-тест прошёл после фикса Telethon photo-detection). Открытый пункт (2) собственный inbox UI не отображал вложения — закрыт в Phase 23.
- ✓ Inbox: редактирование и delete-for-everyone отправленных сообщений (без takeover), отправка файла контакту (auto-media + caption-overflow), входящие файлы от контакта как типизированные баблы с ленивым скачиванием + inline-просмотр фото/видео по тапу (лайтбокс) — Validated in Phase 23 (INBM-01..09). Живой смоук нашёл и закрыл 2 реальных бага: Telethon photo/mime-детекция ключуется на РАСШИРЕНИЕ временного файла (не на отдельно переданное имя) — фото уходили как переименованный документ; `send_file` не имеет параметра `file_name` (тихо игнорируется через `**kwargs`) — имя нужно задавать через `DocumentAttributeFilename`. Задеплоенный inbox UI вообще не рендерил `message_type` — реализовано напрямую в `aimly-tg-outreach/inbox.tsx` в рамках этой же сессии, не дожидаясь отдельного Lovable-цикла.

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
*Last updated: 2026-07-08 — Phase 23 (Edit and Delete-for-Everyone of Sent Messages plus File Sending from Inbox UI) complete: migration 053 extends `messages` (`message_type` NOT NULL DEFAULT 'text' + CHECK text|photo|video|voice|document, `file_name`/`mime_type`/`size_bytes` nullable, `edited_at` TIMESTAMPTZ, `message_text` DROP NOT NULL) — coexists with Phase 24's bridge migration 055 (both idempotent, 053 additionally adds `edited_at` + nullable text); `app/services/telegram.py` gains `_resolve_peer_by_telegram_id` shared helper + 4 inbox mutation methods (edit/delete/send_file/download_media), each client-per-op with structured `{success, error:{code,message}}` returns; `app/routers/conversations.py` gains `PATCH`/`DELETE` on `/messages/{message_id}` (Telethon-first then DB, no-takeover — only outbound AI/human text messages are mutable), `POST /send-file` (streamed 50MB-capped upload, auto-takeover since it's a new outbound, auto-media `force_document=False`), `GET /messages/{message_id}/download` (lazy on-demand fetch from Telegram, `?disposition=inline|attachment`, works for both inbound and outbound); `app/services/listener.py` classifies incoming media into a typed row (metadata only, bytes never downloaded until requested). Live human-verified end-to-end against a real Telegram account and 2 real bugs were found and fixed live: (1) `_spool_upload_with_cap`'s bare `tempfile.mkstemp()` had no extension, so Telethon's `utils.is_image()`/`mimetypes.guess_type()` (which key off the file PATH's extension) always classified uploads as generic documents regardless of `force_document=False` — fixed by preserving the upload's suffix; (2) `send_file_by_telegram_id` passed `file_name=` to `client.send_file()`, a parameter Telethon doesn't have (silently absorbed by `**kwargs`) — fixed with an explicit `attributes=[DocumentAttributeFilename(...)]`. Also found live: the deployed sibling-repo inbox UI had zero Phase-23 rendering at all (both inbound and outbound media showed as a generic file row) — `23-UI-SPEC.md`'s Surface 4 was revised mid-session (inline view promoted from "nice-to-have" to the required primary behaviour for photo/video, since D-16's lazy-fetch intent was being read by the initial draft as "always download to disk") and implemented directly in `aimly-tg-outreach/src/routes/_authenticated/inbox.tsx` — tap-to-view photo/video bubbles with a shared per-message blob-URL cache (no auto-fetch on render/scroll, D-16 preserved), immediate local preview for freshly-sent outbound media (zero round-trip), a click-to-zoom photo lightbox, and download-to-disk parity for outbound document/voice bubbles (previously inbound-only) — pushed to the sibling repo's `main` on user confirmation (2 commits). 9/9 must-haves verified (INBM-01..09), targeted regression 136 passed/0 failed across 16 cross-cutting test files. api+listener deployed 2026-07-08; frontend pushed to sibling `main` twice during the live-smoke session. Prior: 2026-07-08 — Phase 24 (Campaign First-Message File Attachment + Invisible Anti-Spam Text Variation) complete: migration 054 adds `campaign_attachments` (1-1 BYTEA blob, UNIQUE campaign_id + CASCADE, kept off every `SELECT campaigns`) + `campaigns.variation_enabled` (NOT NULL DEFAULT true); pure stdlib `app/services/variation.py::vary()`/`strip_invisible()` inserts zero-width (U+200B/U+200C/U+2060) + NBSP/U+202F space-jitter only inside letter-letter spans (never inside URLs/emails/@mentions/#hashtags/digit-runs/emoji), capped ~10-20%/20 total, applied to a LOCAL COPY at send time only — DB (`message_queue`/`messages`/`messages_log`) stays byte-clean (D-14, confirmed live in prod); `send_file` gains `file_bytes`/`force_document` params (defaults preserve every existing caller byte-identical); `POST/DELETE /campaigns/{id}/attachment` (multipart, alias-tolerant `file`|`attachment`, 50MB→413 FILE_TOO_LARGE, one-per-campaign upsert) + `variation_enabled`/`has_attachment` wired through create/update/response + `duplicate_campaign` copies both; `CampaignEnqueueWorker` resolves attachment presence once per campaign per tick and emits `item_type='file'` rows with rendered caption (still counts as one send/one new-dialog cap); `rerender_pending_queue` extended to file-row captions; queue worker loads the blob by `campaign_id` and delivers via `send_file(file_bytes=..., force_document=False)`, and migration 055 bridges a gap where Phase 23's `messages.message_type/file_name/mime_type/size_bytes` columns were needed but Phase 23 itself was never executed (planned, zero SUMMARY.md). Frontend: attachment upload/delete + variation toggle built from scratch in both the campaign wizard and edit modal (did not exist before this plan — the live-smoke checkpoint was otherwise unexecutable), openapi types hand-patched (targeted, not full regen — the JSON snapshot was stale relative to ~35 other hand-patched endpoint types that a full regen would have silently deleted). Live human-verified end-to-end on real production data: a real PDF attachment → real Telegram delivery as a document with a clean caption → real `messages` row with correct `message_type`/`file_name`/`mime_type`/`size_bytes`, DB confirmed byte-clean under an active variation flag. 34/35 must-haves verified (74/74 targeted tests green); 2 items logged, not blocking: (1) D-06's specific "arrives as inline photo" claim needs a re-test with an actual `.jpg` — the live smoke used a PDF, which is always a Telegram document regardless of the code path; (2) discovered gap where the app's OWN inbox UI doesn't render file-opener attachments at all (`GET /conversations/{id}/messages` + `MessageResponse` never got the 4 media columns; frontend `MessageBubble` has no attachment-rendering branch) — correctly scoped to the still-unexecuted Phase 23, not a Phase 24 blocker, deferred per explicit user instruction. Bonus finding (WARNING, not scored): `failover.py`'s cold-backlog discovery hardcodes `item_type='message'`, so attachment-campaign backlog on a restricted sender won't auto-recover the way text-campaign backlog does. api rebuilt + frontend pushed to sibling `main` 2026-07-08; backend commits local only, not yet pushed to `Andrewbruce165/outreach-platform`. Prior: 2026-07-07 — Phase 21 (Bulk Telegram Account Import via session/JSON upload) complete: migration 051 adds `senders.client_fingerprint` (JSONB NULL) + `senders.twofa_password_enc` (TEXT NULL) + 3 tables (`account_import_stagings`/`account_import_jobs`/`account_import_items`) with ORM mirrors (server_default on every NOT NULL col). Fingerprint seam: `make_telegram_client`/`get_client` gain an optional `fingerprint` override with a STRICT NULL fallback (`{**_CLIENT_FINGERPRINT, **(fingerprint or {})}` → byte-identical for the phone-onboarded 13, api_id/api_hash stay global), threaded through every automated hot path (queue, listener, warmup, checker, contact_check_worker incl. the two-level LATERAL) + all 16 Phase-20 profile/2FA methods so imported accounts reconnect with the fingerprint that created them (no forced re-login / security-flag — the phase's key risk). Two-step flow: `POST /accounts/import/preview` (in-memory unzip, basename pairing, vendor-JSON validation, ZIP-bomb/path-traversal guards, stages raw ZIP BYTEA+TTL, NO Telegram connect) → `POST /import/{id}/confirm {role}` (batch role D-16, creates job+N pending items, 202 job_id) → `AccountImportWorker` (global FOR UPDATE SKIP LOCKED claim, per-item partial success, never dies on a per-item error) → `GET /import/{id}/status` (processed/total + secrets-free per-item rows). Per-account routine `import_one_account`: offline SQLite→encrypted StringSession, connect with own fingerprint, get_me, dedup by telegram_id (skip+report `already_connected`), Fernet-encrypted 2FA (D-05, plaintext NEVER logged/returned — reconciles with Phase-20 D-06 2FA-change autofill), proxy JSON-else-pool. Sibling-repo two-step bulk-import UI (upload→preview→role radio→confirm→progress poll) + openapi/types regen. Live human-verified end-to-end: real archive imported 13/13 (job da5998a0 done), listener auto-reconnected to all 13 with their own client_fingerprint — ZERO auth/security errors, ZERO sender_restriction_events (IMPT-04 confirmed live); mixed-batch UI UAT (2 matched incl. dedup, 2 unpaired, 1 malformed; duplicates→already_connected; broken entries did not abort batch). 30/30 must-haves, IMPT-01..10 closed; regression gate green (1 phase-introduced worker-test flake fixed — hermetic against globally-leaked pending rows; WARM-14 remains pre-existing out-of-scope). api+listener deployed 2026-07-07, frontend pushed to sibling `main`. Prior: 2026-07-06 — Phase 20 (Account Profile Management) complete: migration 049 adds cached-profile columns to `senders` (`tg_username`/`tg_bio`/`tg_photo`/`tg_photo_mime`/`profile_field_changed_at` JSONB NOT NULL with `server_default`); identity edit (`PATCH /senders/{slug}/profile` name/bio warning-only D-07, username 1h hard-block D-08) + live username-check; photo lifecycle (upload/delete/auth-gated serve, bytes never inlined/raw-URL, D-11) + manual resync (D-12, resync composes live `first_name`/`last_name` into the single `sender.name` column — no separate last-name storage exists); 2FA password set/change via one stateless `client.edit_2fa` call + two-step recovery-email confirm flow using raw Telethon functions (D-03: password never persisted); frontend redesigned twice during human-verify (profile modal → bordered per-section blocks with Role moved out of the identity fields; accounts list → table replaced by a card grid grouped Sender/Checker then priority-sorted reauth-needed > active > everything else). 9/9 must-haves, PROF-01..09 closed; Phase-20 test files 46/46 green (full-suite run has pre-existing shared-DB test-ordering pollution unrelated to this phase). Deployed 2026-07-06 (api rebuilt, frontend pushed to sibling repo `main`). One CR-04 regression (unrelated parallel batch broke the `get_client` call signature on all 9 Phase-20 call sites) caught and fixed before verification, locked by a dedicated regression test. Known non-blocking gap: avatar photo doesn't visually refresh immediately after resync (stale effect dependency). Bulk/mass account editing explicitly deferred — backlog item 999.1. Prior: Phase 19 (No Reply Follow-Up and Auto-Finish) complete: migration 045 extends `conversations.status` CHECK with `no_reply` (bot_ignored preserved) + `pings_sent` counter + 4 campaign columns (`follow_up_enabled`/`follow_up_interval_hours`/`follow_up_max_pings`/`auto_finish_hours`, API-layer Pydantic bounds not DB CHECK, D-12); `ai_engine.generate_followup_ping` reuses prompt assembly + Phase-18 provider (tools=None, D-07); listener reverts `no_reply`→`active` + cancels pending pings on genuine reply before the AI-dispatch check (D-03, Pitfall-4 safe) with a queue pre-send replied-since guard gated on `extra_data.kind=='followup'` (D-17 double guard); `FollowUpWorker` timer state machine (`FOR UPDATE OF c SKIP LOCKED`) applies auto-finish-first/ping-else anchored to last outbound message, fires the finish webhook with `reason='no_reply'`, registered in the FastAPI lifespan with a `follow_up_tick_seconds` knob; openapi.json regenerated offline + Follow Up settings block shipped in the sibling campaign create/edit form, human-verified (persistence + bounds; live E2E ping/auto-finish loop left as optional follow-up). 9/9 must-haves, NORP-01..13 closed; full suite 939 passed (1 pre-existing out-of-scope WARM-14). Not yet deployed to prod (api+listener rebuild pending). Prior: Phase 18 (Switchable LLM Provider) complete: workspace-scoped OpenAI↔Anthropic switch (D-01, single `llm_settings` row per workspace, migration 044) with Fernet-encrypted BYO key (D-04) behind a KEY_REQUIRED gate (D-03); `app/services/llm/` provider-adapter package (LLMProvider protocol + OpenAI/Anthropic adapters with role coalescing, capability gates D-09, green-corridor clamps D-10, key-level-error fallback classifier D-06); settings API (GET masked / PATCH / test-connection D-05 / live family-filtered model list D-08) + frontend AI/LLM Settings tab in the sibling repo; answerer (all 3 call sites) + warmup routed through the per-workspace resolved provider with provider/key_source persisted to `llm_calls` (D-07); Whisper + KB embeddings pinned to the platform OpenAI singleton (D-12). Live human-verified end-to-end: real Anthropic key → `claude-sonnet-5` replies in dialogue, `llm_calls` rows `provider='anthropic', key_source='byok'`. 2 live Anthropic API bugs found & fixed during UAT (thinking×temperature exclusivity; Claude-5-generation adaptive thinking via `output_config.effort` extra_body — SDK 0.115.1 lacks the kwarg). 25/25 must-haves, LLMP-01..12 closed; full suite 902 passed (1 pre-existing out-of-scope WARM-14). api+listener deployed 2026-07-02. Prior: Phase 17 (Sender-side Resolve Ladder) complete: resolve responsibility moved onto the SENDER — checker is now a pure exist/no-exist filter that ALSO captures the transferable `@username` (vs per-account `access_hash`) on both `ResolvePhone` and `ImportContacts` paths and confidence-gates its `is_registered=false` cache reads (SRLD-01/02/07); `telegram.py::resolve_contact` rebuilt as a 3-tier ladder cache(access_hash) → `ResolveUsername`(captured @username) → lazy per-send `ImportContacts` (gated on checker verdict `registered`, no `DeleteContacts` on the sender), with the sender's own `ResolvePhone` REMOVED entirely and a stale-username fall-through to import instead of finalizing False (SRLD-03..06, fixes the live «Barter - ВЭД хук» incident where 22 live RU mobiles died on ResolvePhone); `UserIsBlockedError` captured in send_message/send_file as a durable per-sender `sender_restriction_events` `blocked` event with a read-only `GET /senders/{slug}/block-rate` aggregate (no control loop, SRLD-08/D-15/D-16); and the unproven US-cannot-resolve-RU country claim reframed to a hypothesis in CLAUDE.md (SRLD-09/D-10). 0 migrations (reuses contacts.tg_username_resolved + tg_probe_state/tg_confidence + sender_restriction_events). 9/9 must-haves verified; full suite 850 passed via test-overlay (1 out-of-scope WARM-14 failure from parallel uncommitted Phase 15 warmup). SRLD-01..09 closed; subsumes Phase 14's deferred SC #3/#4 by removing the dependency on a dedicated checker pool. Prior: Phase 16 (RAG Knowledge Bases for Agents) complete: pgvector KB store (migrations 041 tables+HNSW / 042 id server_defaults / 043 FTS GIN); ingest worker (extract pdf/docx/txt → tiktoken chunk → embed text-embedding-3-small, FOR UPDATE SKIP LOCKED, idempotent re-index); HYBRID retrieval (cosine OR full-text `simple` keyword, workspace+kb_id double-filter KB-06); agent-level M:N attach with on-demand `search_knowledge_base` data-tool gated on ≥1 KB + a `<knowledge_base>` RAG-awareness directive; full UI (Knowledge bases tab/list/detail with 4 tabs + agent multi-select with one-step deferred-attach on create). Live human-verified end-to-end (real PDF → indexed → agent answered a question by searching the KB, workspace-isolated). 6/6 must-haves. 7 defects found & fixed during human-verify (pgvector init-ordering, NUL-strip, kb id NotNull, search threshold, hybrid search, chunk sizing, RAG-awareness). KB-01..06 closed. Prior: 2026-06-24 — Phase 10 (Pool Visibility & Restriction Audit) complete: migration 030 append-only `sender_restriction_events` table + migration 031 (flood_wait category); `record_restriction_event` dual-mode helper + activity slice (HLTH-01/02); 5 write-points wired in same tx as `senders.restriction_status` UPDATE (queue PEER_FLOOD/ACCOUNT_FROZEN, listener antispam+reconcile, PRIVACY_RESTRICTED); D-01 extension gate (only on SpamBot-quoted date, not recheck bumps); workspace-scoped `GET /senders/{slug}/restriction-events` history (HLTH-03); `pool_health {active,paused,total,earliest_resume_at}` aggregate on campaign response (POOLV-01); per-sender `restriction_status`/`restricted_until` enrichment (POOLV-02); frontend 3-state pool badge + restriction-event list committed in sibling repo, browser UAT pending deploy (POOLV-03/04). 7/7 backend must-haves verified. Prior: Phase 07 (Unified Freeze Policy) complete: `listener._handle_antispam_signal` rewritten onto the PEER_FLOOD soft-restriction pattern — antispam warning now PAUSES the sender's pending queue (+24h, reconcile auto-resumes) instead of terminally failing, flags `restriction_status='spam_limited'` with a `<> 'frozen'` precedence guard, and no longer disables `ai_enabled` so established dialogues keep replying (FRZ-01..03); rotation candidate filter gains `AND s.restriction_status='none'` so new cold contacts skip restricted senders (FRZ-04); worker pre-send skip asserted by regression (FRZ-05). No migration (028 pre-existing). Existing-assignment failover (CR-01) deferred to Phase 9. Prior milestone footer: 2026-05-23 — Phase 05.1 (Lovable UI v1) complete: migration 018 (telemetry_events + 11 agent v2 cols + 4 campaign v2 cols), CORS regex for *.lovableproject.com + HS256 pin comment, router widening (campaigns /stop alias + /auto-fill stub, senders /pause /resume, agents v2 passthrough, ai_engine COALESCE), 4 new endpoints (/analytics/funnel Sankey + /analytics/llm + /telemetry/events + /telemetry/core-value), lovable-handoff/ bundle (8 docs + 2 CI scripts) + UI-SPEC reconciliation, Core Value E2E pytest (<600s) + HUMAN-UAT mapping for the 7 Phase 5 items. 14/15 must-haves auto-verified; 4 infra-gated items in 05.1-HUMAN-UAT.md (handoff export, server-side pytest, Lovable UI walkthrough, Supabase HS256 dashboard setting). Phase 5 (Inbox & Analytics) complete: migration 017 + conversations router rewrite (9 endpoints under auth_dep) + analytics router (4 read-only endpoints) + listener bot filter + queue pre-send race guard + never-raise LLM call logger + per-conversation LLM call log endpoint.*
