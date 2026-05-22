# Phase 4: Campaigns - Research

**Researched:** 2026-05-22
**Domain:** Outreach campaign orchestration — модель Campaign связывает agent + folder + senders + signals + webhook + tools + расписание; рерайт queue.py per-campaign; built-in LLM tools для сигналов; миграция 016
**Confidence:** HIGH (живой код + явный CONTEXT.md с 19 D-решениями + audit `app/services/*` показал чёткую картину переезда)

## Summary

Phase 4 — самая большая фаза проекта по объёму (5 планов, 17 требований CAMP-01..17), но архитектурно прямолинейная: добавить таблицу `campaigns` и три связанных таблицы (`campaign_senders`, `campaign_contact_assignments`, +колонки в `conversations`/`message_queue`), новый роутер CRUD+lifecycle, новый background worker `CampaignEnqueueWorker` (по паттерну ContactCheckWorker), и подключить пять `TODO(phase-4)` точек в существующих сервисах. Большая часть «бизнес-кода» (ai_engine, queue, rotation, listener) переиспользуется — Phase 4 меняет источник данных, а не алгоритмы.

Самая нетривиальная часть — это **переезд webhook+tools с дропнутого `ai_contexts.webhook_functions` (Phase 3 D-01) на `campaigns.tools`** и **добавление 3 built-in LLM tools (`mark_as_lead` / `transfer_to_manager` / `finish_conversation`)**, которые семантически (через free-text `*_trigger_hint`) сообщают модели когда переключить статус диалога и стрельнуть webhook. Built-in tools инжектятся в массив `tools` всегда — даже у кампании с пустым `tools` JSONB. OpenAI parallel tool calls — поддерживается «из коробки» через `response.tool_calls[]`, что уже корректно обрабатывается в `ai_engine.generate_response()` (строка 327): нужно просто добавить ветку «built-in → UPDATE conversation + fire webhook + НЕ возвращать tool result в LLM».

Второй риск — **миграция 016 для расширения `conversations.status` enum**. В Phase 3 это `String(20)` без CHECK (модель строка 230); расширение значениями `'lead'`, `'handoff'`, `'finished'` (D-12) требует либо (а) оставить String + добавить CHECK constraint в той же миграции, либо (б) превратить в PostgreSQL ENUM и использовать `ALTER TYPE ADD VALUE` — но это нельзя делать внутри транзакции (Postgres limitation, см. Pitfall 4). Рекомендация — пойти по варианту (а) для простоты идемпотентности.

**Primary recommendation:** Plan 04-01 (audit) проводит финальный grep по `webhook_functions` / `ai_context_id` в коде, фиксирует точный shape webhook_functions из Phase 3 коммитов (живой код уже его не имеет — нужно git log), и определяет окончательный список TODO(phase-4) меток к закрытию (найдено 10 меток). Plan 04-02 делает миграцию 016 + ORM модели + базовый CRUD + lifecycle endpoints. Plan 04-03 (расписание) можно фоллдить в 04-02 (это +4 колонки в `campaigns`). Plan 04-04 (queue rewrite + CampaignEnqueueWorker + send.py рерайт) — самый большой. Plan 04-05 (signals + webhook + tools wiring в ai_engine) — финальный.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Campaign модель и связи:**

- **D-01:** 1 agent на campaign. Колонка `campaigns.agent_id UUID NOT NULL FK ai_contexts(id) ON DELETE RESTRICT` (защита от потери агента при наличии running кампании; для draft/paused/done кампаний при DELETE агента → 409 с ссылкой на кампании — уже задано Phase 3 D-09 TODO). Несколько агентов в одной кампании (A/B) — v2 ADVN-02.

- **D-02:** 1 папка контактов на campaign. Колонка `campaigns.folder_id UUID NOT NULL FK folders(id) ON DELETE RESTRICT`. Phase 2 D-06 уже имеет TODO «block delete folder если есть active campaign» — здесь его реализуем: DELETE folder → 409 если есть `SELECT 1 FROM campaigns WHERE folder_id=? AND status='running'`. Для draft/paused/done — позволить delete folder с CASCADE на contacts (Phase 2 D-06 `?force=true`).

- **D-03:** Sender lock per active campaign — через **through-table + derived check**, не физический FK на sender:
  - Таблица `campaign_senders(campaign_id UUID FK CASCADE, sender_id UUID FK CASCADE, workspace_id UUID NOT NULL, added_at TIMESTAMPTZ, PRIMARY KEY (campaign_id, sender_id))`.
  - Sender может быть прикреплён к нескольким draft / paused / done кампаниям. Lock наступает при `POST /campaigns/{id}/start`: проверяем `SELECT cs.sender_id, c.id FROM campaign_senders cs JOIN campaigns c ON c.id=cs.campaign_id WHERE cs.sender_id = ANY(:requested_senders) AND c.status='running' AND c.id != :starting_campaign`. Если есть — 409 с детализацией конфликтных пар `[{sender_id, campaign_id, campaign_name}]`.
  - UI на форме создания кампании при выборе senders показывает статус «занят кампанией X» (read-only поле в GET response).
  - Plus: history sender'а в разных кампаниях остаётся как есть (через campaign_senders), не теряется при stop+restart другой кампании.

- **D-04:** Lifecycle статусов — **100% manual, done terminal**:
  - Состояния: `draft`, `running`, `paused`, `done` (SQLEnum, не String — Phase 2 D-13 pattern для новых enum-полей).
  - Переходы:
    - `draft → running` (POST /start, требует agent + folder + ≥1 sender + walks sender lock check).
    - `running → paused` (POST /pause).
    - `paused → running` (POST /resume, требует sender lock re-check — sender мог быть взят другой кампанией пока эта стояла).
    - `running → done` (POST /finish).
    - `paused → done` (POST /finish).
    - `done` — terminal, никаких переходов обратно. Чтобы перезапустить — POST /duplicate.
    - `draft → done` — недопустимо (только через running или paused).
  - В API ответе `GET /campaigns/{id}` всегда есть computed `is_exhausted: bool` = true, если `SELECT COUNT(*) FROM contacts WHERE folder_id = ? AND id NOT IN campaign_contact_assignments` = 0 И `SELECT COUNT(*) FROM message_queue WHERE campaign_id = ? AND status IN ('pending','processing')` = 0. UI рендерит подсказку «Mark as done?» когда `is_exhausted && status='running'`.
  - Никаких background-тиков, переводящих статусы автоматически — Phase 2 D-12 lifecycle pattern (юзер сам нажимает кнопки).

- **D-05:** `conversations.campaign_id` — NULLable FK ON DELETE SET NULL. Заполняется CampaignEnqueueWorker'ом при INSERT первого item'а кампании в queue (D-17) — а conversation сам создаётся при первой отправке в queue.py:691 уже сейчас. Расширяем этот INSERT на `campaign_id = :cid` (берётся из `message_queue.campaign_id`). Для legacy входящих от незнакомых и для conversation, созданных в Phase 3 — `NULL`. Phase 5 inbox фильтрует `WHERE campaign_id = :id OR campaign_id IS NULL` в зависимости от UI-режима.

- **D-06:** Rotation per-campaign — **drop + create новую таблицу**:
  - DROP TABLE `context_contact_assignments`.
  - CREATE TABLE `campaign_contact_assignments (id UUID PK, workspace_id UUID FK NOT NULL, campaign_id UUID FK CASCADE NOT NULL, contact_phone VARCHAR(20) NOT NULL, sender_id UUID FK CASCADE NOT NULL, created_at TIMESTAMPTZ, UNIQUE(campaign_id, contact_phone))`.
  - `services/rotation.py:get_or_assign_sender()` обновляется: вместо `context_id` принимает `campaign_id`. Подбор sender'а — из `campaign_senders` (не глобально из workspace senders). Если в кампании ≥1 sender активен — выбираем round-robin / least-loaded. Если все senders кампании выпали — пытаемся переподобрать на лету с обновлением assignment row.
  - БД чистая (Phase 1 D-01) — DROP TABLE без backfill.

- **D-07:** DELETE /campaigns/{id} — **hard delete с 409 на running**:
  - `running` → 409 `{code: "CAMPAIGN_RUNNING", message: "Stop campaign before deleting"}`.
  - `draft` / `paused` / `done` → 204, hard delete.
  - FK на удаление: `campaign_senders` CASCADE, `campaign_contact_assignments` CASCADE, `conversations.campaign_id` SET NULL, `message_queue.campaign_id` SET NULL.

**Schedule:**

- **D-08:** **Timezone per-campaign**. Колонка `campaigns.timezone TEXT NOT NULL DEFAULT 'Europe/Moscow'`. Валидация на API: значение должно резолвиться через `zoneinfo.ZoneInfo(...)`.
- **D-09:** **Одно окно** рабочих часов. `work_hour_start INT NOT NULL DEFAULT 9`, `work_hour_end INT NOT NULL DEFAULT 20`. CHECK constraint `work_hour_start < work_hour_end`.
- **D-10:** **Дни недели — INT bitmask** `work_days_mask INT NOT NULL DEFAULT 31` (Mo=1..Su=64, Mo-Fri=31). CHECK `BETWEEN 1 AND 127`.
- **D-11:** **Stop_date soft skip**. `start_date`, `stop_date` NULLABLE TIMESTAMPTZ. CampaignEnqueueWorker не вставляет items раньше start_date; queue worker помечает items после stop_date как failed; campaign.status НЕ меняется автоматически.

**Signals + webhook + tools:**

- **D-12:** **Сигналы через LLM tool call** — 3 built-in tools (`mark_as_lead(reason)`, `transfer_to_manager(reason)`, `finish_conversation(reason)`) с description из `lead_trigger_hint` / `handoff_trigger_hint` / `finish_trigger_hint` кампании. Когда LLM вызывает: UPDATE conversations.status ('lead'/'handoff'/'finished') + (для handoff/finish) ai_enabled=false + POST на соответствующий webhook URL.

- **D-13:** **Три отдельных webhook URL** `lead_webhook_url`, `handoff_webhook_url`, `finish_webhook_url` — каждый может быть NULL. Fire-and-forget httpx. REQUIREMENTS.md CAMP-14 уже обновлён в коммите CONTEXT.md.

- **D-14:** **Tools spec переезжает as-is** — `campaigns.tools JSONB NOT NULL DEFAULT '[]'`. Shape тот же что в дропнутой `ai_contexts.webhook_functions`. Existing `ai_engine.build_tools()` / `execute_webhook()` reuse без изменений API.

- **D-15:** **Pause = только отправка, AI продолжает отвечать**. queue worker SKIP'ает items paused/done кампаний, listener/ai_engine НЕ проверяет campaign.status — AI отвечает если `conversations.ai_enabled=true`.

**Очередь, досыпание, переменные:**

- **D-16:** **`message_queue.campaign_id` NOT NULL FK ON DELETE SET NULL**. БД чистая — NOT NULL применим сразу. `/api/v1/send` рерайт: body требует `campaign_id` вместо `ai_context_id`. Composite index `(workspace_id, campaign_id, status, scheduled_at)`.

- **D-17:** **CampaignEnqueueWorker** — background worker в lifespan API-контейнера (паттерн ContactCheckWorker). Tick ~30s. Цикл: SELECT running campaigns → SELECT contacts из folder где `tg_status='registered'` и `NOT IN campaign_contact_assignments` LIMIT N → get_or_assign_sender → render_template → bulk INSERT в message_queue. Без LISTEN/NOTIFY. Singleton `campaign_enqueue_worker = CampaignEnqueueWorker()` в module, start/stop в lifespan.

- **D-18:** **Variable substitution на enqueue**, не на send. INSERT в `message_queue.message_text` уже подставленным. queue worker не знает о шаблонах.

- **D-19:** **`{{name}}` Mustache-style + empty fallback**. Переменные: `{{name}}`, `{{username}}`, `{{phone}}`, `{{source}}`, `{{custom.X}}`. Русские алиасы (`{{имя}}`→name). Отсутствующая → empty string + warning.

### Claude's Discretion

- **C-01:** Точный shape webhook payload (D-13) — какие именно поля включать (message_history_excerpt — сколько последних сообщений? в каком формате?), нужен ли HMAC signature header. Planner подберёт.
- **C-02:** Точная таблица алиасов переменных (русск/англ для `{{name}}` / `{{имя}}` и др., D-19) — synchronize с Lovable UI.
- **C-03:** Regex для парсинга `{{...}}` (D-19) — допускать ли пробелы внутри (`{{ name }}`), допускать ли пайпы как Mustache filters. Рекомендация: strict без filters, с пробелами внутри.
- **C-04:** Built-in tool names (D-12) — `mark_as_lead` / `transfer_to_manager` / `finish_conversation` могут быть переименованы под convention OpenAI.
- **C-05:** Точные имена endpoint'ов и Pydantic-схем (`CampaignCreate`, `CampaignResponse`, `CampaignStatusUpdate` и т.д.).
- **C-06:** Composite indexes на `campaigns` (status partial index `WHERE status='running'`), `message_queue` (composite `(workspace_id, campaign_id, status, scheduled_at)`), `campaign_contact_assignments` (`(campaign_id, contact_phone)` уже UNIQUE).
- **C-07:** Распределение фич по 5 планам ROADMAP: возможно фоллдинг 04-03 schedule в 04-02 model (schedule = +4 поля в campaigns). Planner решит. Если 04-01 audit покажет, что webhook_functions shape в коде сильно разошёлся с D-14 — может потребоваться отдельный plan 04-01.5 на чистку.
- **C-08:** `senders.role` String(20)+CHECK → SQLEnum — деферрено из Phase 2 D-21 / Phase 3. Planner может включить как мелкую правку в Plan 04-02 либо снова отложить. Не блокер.
- **C-09:** `app/database.py` `Base.metadata.create_all` (Phase 1 C-04, Phase 2/3 carry-over) — всё ещё нерешён.
- **C-10:** Точная shape поля `campaigns.tools` Pydantic-схемы (D-14) — JSON schema валидация на API-уровне (POST /campaigns принимает только валидный shape) либо raw JSONB. Рекомендация: pydantic-валидация shape (имя, description, parameters, webhook_url, webhook_method).
- **C-11:** Семантика `POST /campaigns/{id}/duplicate` — копировать campaign_senders? copy queue items? Рекомендация: копировать campaigns row + campaign_senders. НЕ копировать queue items и НЕ копировать campaign_contact_assignments.
- **C-12:** Точная shape `lifecycle_status` транзит-логирования (для audit log будущего, ANLX-05 Phase 5). В Phase 4 — не вводим audit log.
- **C-13:** Conversation.status enum новые значения `'lead'`, `'handoff'`, `'finished'` — добавляются к существующим `'active'`, `'manual'`, `'paused'`. Phase 4 может либо расширить String + CHECK, либо превратить в SQLEnum.

### Deferred Ideas (OUT OF SCOPE)

**Для Phase 5 (Inbox & Analytics):**
- Inbox-фильтр по кампании (INBX-05), аналитика per-campaign (ANLX-02), LLM request log (ANLX-05), AI-фильтр системных ботов (AIRC-04), визуализация status='lead'/'handoff'/'finished' в inbox.

**Для Phase 6 (Admin Master Bot):**
- ADMN-02: бот шлёт уведомление в admin-канал при срабатывании transfer_to_manager. ADMN-03: уведомление при ошибке sender'а.

**Для v2:**
- Multi-window расписание (D-09), multi-folder/multi-agent кампании (ADVN-02 A/B), strict-mode variable substitution (D-19), `{{name | upper}}` Mustache filters (C-03), HMAC signature на webhook'ах (D-13 C-01), tool webhook timeout / retry (CAMP-15), per-contact timezone scheduling (ADVN-03), multi-step follow-up (ADVN-01), audit log переходов lifecycle (C-12), background scaling (FOR UPDATE SKIP LOCKED для CampaignEnqueueWorker), NOTIFY/LISTEN моментальность досыпания.

**Tech debt из Phase 1/2/3:**
- `senders.role` String(20)+CHECK → SQLEnum (C-08), `app/database.py` `Base.metadata.create_all` (C-09), `DEFAULT_SYSTEM_PROMPT` AGS Foods хардкод в ai_engine.py (CONCERNS.md brand-leak) — НЕ закрывается в Phase 4. OpenAI model ID `gpt-5-mini-2025-08-07` (CONCERNS.md «Known Bugs») — отдельный bug, НЕ часть Phase 4.

</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CAMP-01 | Создание кампании с именем и описанием | Standard Stack §"Campaign Model"; Plan 04-02 CRUD endpoints. Шаблон — Phase 3 agents.py |
| CAMP-02 | Выбор агента-шаблона из списка workspace | D-01 `campaigns.agent_id NOT NULL FK ai_contexts ON DELETE RESTRICT`; UI: dropdown `GET /api/v1/agents` |
| CAMP-03 | Выбор папки контактов | D-02 `campaigns.folder_id NOT NULL FK folders ON DELETE RESTRICT`; UI: dropdown `GET /api/v1/folders` |
| CAMP-04 | Sender lock | D-03 через `campaign_senders` through-table + derived check; реализация в Plan 04-02 (POST /start) |
| CAMP-05 | Расписание (рабочие часы и дни) | D-08/D-09/D-10 timezone + work_hour_start/end + work_days_mask; Plan 04-03 (или fold в 04-02) |
| CAMP-06 | Старт и стоп даты (опционально) | D-11 start_date/stop_date NULLABLE TIMESTAMPTZ; soft skip semantics |
| CAMP-07 | Статусы (draft/running/paused/done) | D-04 SQLEnum CampaignStatus; Plan 04-02 |
| CAMP-08 | Старт/пауза/стоп | D-04 manual lifecycle endpoints POST /start, /pause, /resume, /finish; Plan 04-02/04-03 |
| CAMP-09 | Досыпание контактов | D-17 CampaignEnqueueWorker tick 30s — добавление в папку = пик worker'а подхватит на следующем tick'е; Plan 04-04 |
| CAMP-10 | Variable substitution `{{name}}` etc | D-18/D-19 render_template на enqueue; Plan 04-04 (app/services/template.py) |
| CAMP-11 | Сигнал «передать лид» | D-12 built-in tool `mark_as_lead(reason)` + UPDATE status='lead' + POST lead_webhook_url; Plan 04-05 |
| CAMP-12 | Сигнал «передать на менеджера» | D-12 built-in tool `transfer_to_manager(reason)` + UPDATE status='handoff', ai_enabled=false + POST handoff_webhook_url; Plan 04-05 |
| CAMP-13 | Сигнал «финиш диалога» | D-12 built-in tool `finish_conversation(reason)` + UPDATE status='finished', ai_enabled=false + POST finish_webhook_url; Plan 04-05 |
| CAMP-14 | 3 webhook URL (lead/handoff/finish), любой NULL | D-13; 3 nullable TEXT колонки; REQUIREMENTS.md уже обновлён |
| CAMP-15 | Custom tools (function calling) | D-14 `campaigns.tools JSONB`; reuse existing `ai_engine.build_tools()` + `execute_webhook()`; Plan 04-05 |
| CAMP-16 | Signals+tools передаются в LLM-промпт | Реализация в `ai_engine.generate_response()` — built-in tools всегда инжектятся, custom tools читаются из campaigns.tools; Plan 04-05 |
| CAMP-17 | Очередь учитывает campaign_id | D-16 `message_queue.campaign_id NOT NULL FK SET NULL`; рерайт send.py; CampaignEnqueueWorker INSERT; Plan 04-04 |

</phase_requirements>

## Project Constraints (from CLAUDE.md)

| Constraint | Details |
|------------|---------|
| **Common discussion в чате** | На русском. Перед изменением кода — короткое объяснение что/зачем, дождаться подтверждения (auto-mode: делать reasonable call) |
| **Async everywhere** | Все DB через async/await + AsyncSession (применимо для CampaignEnqueueWorker, нового роутера, ai_engine хука) |
| **Миграции — raw SQL** | Нумерация 016_. Идемпотентность через IF NOT EXISTS / DROP IF EXISTS. Никогда Alembic |
| **НЕ трогать эмпирические интервалы queue.py** | `MIN_SEND_INTERVAL`, `MAX_SEND_INTERVAL`, `LONG_PAUSE_*`, `FLOOD_HARD_THRESHOLD`, `MAX_NEW_CONTACTS_PER_HOUR` — оставлены как есть; comment строки 45-46 явно фиксирует это для Phase 4 |
| **НЕ ломать FloodWait retry-логику** | Существующая обработка `FloodWaitError` / `PEER_FLOOD` в `_send_item` остаётся |
| **Безопасность сессий** | TG session_string зашифрован Fernet'ом — Phase 4 не трогает |
| **Логирование через logger** | `logging.getLogger(__name__)`, без print() |
| **Никогда time.sleep()** | `asyncio.sleep()` в worker'ах |

**Из CONTEXT.md anti-patterns раздела (повторяю для acid-проверки):**

- НЕ трогать rate-limit / long-pause / flood constants (CLAUDE.md).
- НЕ дублировать `DEFAULT_SYSTEM_PROMPT` AGS Foods хардкод (CONCERNS.md brand-leak, отдельная фаза).
- НЕ изменять `gpt-5-mini-2025-08-07` model ID (отдельный bug, не Phase 4).
- НЕ автоматически менять campaign.status в background tick'ах (D-04 — 100% manual).
- НЕ хардкодить fallback "Europe/Moscow" в новом коде — читать из `campaign.timezone`.
- НЕ возвращать workspace-данные без `where(workspace_id == ctx.workspace_id)` + `# TODO(v2-rls)` метки.
- НЕ создавать новые таблицы без `workspace_id UUID NOT NULL FK CASCADE`.
- НЕ использовать выпиленный `verify_api_key` (Phase 1 D-14) — только `AuthDep`.

## Standard Stack

### Core (already in project, reuse)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.109.0 | Web framework | Существующий router pattern Phase 1-3 |
| SQLAlchemy | 2.0.25 (async) | ORM + raw SQL via `text()` | Async-only, через `AsyncSession` |
| asyncpg | 0.29.0 | PostgreSQL driver | Существующий стек |
| Pydantic | 2.8+ | Request/response validation | v2, `ConfigDict(from_attributes=True)` |
| python-jose | 3.3.0 | JWT для Supabase | Phase 1, reuse в auth_dep |
| OpenAI SDK | >=1.40.0,<2.0.0 | LLM tool calls | `client.chat.completions.create()` с `tools=[...]` параметром; parallel tool calls через `response.tool_calls[]` массив |
| httpx | 0.26.0 | Async HTTP для webhook'ов | Существующий `_fire_callback` pattern в queue.py:731 и `execute_webhook` в ai_engine.py:201 |
| Telethon | 1.42.0 | Telegram MTProto | НЕ трогаем — переиспользуем `telegram_service.send_message()` |
| zoneinfo | stdlib | Timezone handling | Standard library — для валидации `campaigns.timezone` (IANA имена) |

### Supporting (already in project, reuse)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | >=8.0 | Test runner | Phase 1+2+3 фикстуры в `tests/conftest.py` уже включают `test_agent_factory`, `test_workspace`, `test_folder` |
| pytest-asyncio | >=0.23 | Async test support | `asyncio_mode = "auto"` в pyproject.toml |
| cryptography (Fernet) | 42.0.0 | Encryption for session_string | Не трогаем |

### New: NO new dependencies needed

**Verified:** `cat requirements.txt` показывает что все необходимые библиотеки для Phase 4 уже установлены. `zoneinfo` — stdlib (Python 3.9+, проект Python 3.11). Regex для template — stdlib `re`. JSON-schema валидация — Pydantic v2 встроена.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Mustache regex `\{\{...\}\}` | `chevron` (Mustache lib) | Mustache full-spec бесплатно, но D-19/C-03 явно strict без filters → stdlib `re` достаточен |
| zoneinfo (stdlib) | `pytz` library | pytz уже legacy, stdlib zoneinfo — выбор современного Python |
| Pydantic для tool spec валидации (C-10) | `jsonschema` library | Pydantic уже в проекте; OpenAI Function Calling JSON-schema подмножество — Pydantic покрывает |
| asyncio polling 30s (D-17) | LISTEN/NOTIFY на contacts INSERT | NOTIFY требует второго connection в asyncpg + LISTEN-loop в worker'е; для outreach 30s lag acceptable (CONTEXT.md явно зафиксировал) |
| Singleton `campaign_enqueue_worker` в module | DI-инжекция через FastAPI Depends | Существующий pattern: `queue_worker`, `contact_check_worker`, `warmup_worker` — все singleton'ы в module |
| Recursive AI call после tool_call (как сейчас) | Tool result → не возвращать LLM (для built-in) | D-12 явно: built-in tool result в LLM НЕ возвращается (диалог уже закрыт / помечен). Только для custom tools (CAMP-15) — старая логика второго вызова `client.chat.completions.create()` |

**Installation:** Никаких новых пакетов добавлять не надо.

**Version verification:**

```bash
# Все версии уже зафиксированы в requirements.txt
grep -E "openai|httpx|fastapi|sqlalchemy" requirements.txt
```

Уже зафиксированные версии (HIGH confidence): см. таблицу Core выше. Никаких npm/pypi запросов для Phase 4 не требуется.

## Architecture Patterns

### Recommended Project Structure (Phase 4 additions)

```
app/
├── models/__init__.py              # +Campaign, CampaignSender, CampaignContactAssignment ORM
│                                   # +Conversation.campaign_id, MessageQueue.campaign_id
│                                   # -ContextContactAssignment (DROP)
├── routers/
│   ├── campaigns.py                # NEW: CRUD + lifecycle endpoints (D-04)
│   ├── send.py                     # REWRITE: campaign_id вместо ai_context_id (D-16)
│   ├── agents.py                   # PATCH: campaign_count реальный COUNT + блок DELETE при active campaign
│   ├── folders.py                  # PATCH: блок DELETE при active campaign (закрытие TODO Phase 2 D-06)
│   └── senders.py                  # PATCH: dropped ai_context links, plus check active campaign references
├── schemas/__init__.py             # +CampaignCreate, CampaignUpdate, CampaignResponse,
│                                   #  CampaignListResponse, CampaignSenderAttach, ToolSpec
├── services/
│   ├── campaign_enqueue.py         # NEW: CampaignEnqueueWorker (паттерн ContactCheckWorker)
│   ├── template.py                 # NEW: render_template(template, contact_dict) → str
│   ├── webhook_notify.py           # NEW (опционально): fire-and-forget webhook helper
│   │                               # (или inline в campaigns/ai_engine)
│   ├── queue.py                    # PATCH: выпилить MOSCOW_TZ/WORK_HOUR_*/_is_working_hours/_next_working_window
│   │                               # → per-campaign check (JOIN на campaigns)
│   │                               # INSERT conversations: +campaign_id
│   ├── ai_engine.py                # PATCH: get_context_for_conversation резолвит campaign
│   │                               # build_tools добавляет 3 built-in
│   │                               # generate_response ветка для built-in tool calls
│   ├── rotation.py                 # PATCH: get_or_assign_sender(campaign_id, ...) вместо context_id
│   └── listener.py                 # PATCH (минимальный): get_context через campaign_id JOIN
├── main.py                         # +include_router(campaigns.router)
│                                   # +campaign_enqueue_worker.start()/stop() в lifespan
└── config.py                       # +CAMPAIGN_ENQUEUE_TICK_SECONDS, CAMPAIGN_ENQUEUE_BATCH_SIZE

migrations/
└── 016_phase4.sql                  # NEW: campaigns + campaign_senders + campaign_contact_assignments
                                    # + conversations.campaign_id + message_queue.campaign_id
                                    # DROP context_contact_assignments
                                    # CHECK constraint conversations.status (lead/handoff/finished added)

tests/
├── conftest.py                     # +campaign_factory, +running_campaign_factory, +campaign_sender_attach
├── test_migration_016.py           # NEW
├── test_campaigns.py               # NEW: CRUD + lifecycle (sender lock check)
├── test_campaign_enqueue_worker.py # NEW: tick → INSERT в queue
├── test_template.py                # NEW: render_template
├── test_send_campaign.py           # REWRITE: campaign_id-based body
├── test_ai_engine_signals.py       # NEW: built-in tool calls → UPDATE conversation + fire webhook
├── test_queue_per_campaign_schedule.py  # NEW: timezone/work_hours/work_days/stop_date
└── test_rotation_campaign.py       # REWRITE: get_or_assign_sender по campaign_id
```

### Pattern 1: Workspace-scoped CRUD router (reuse Phase 2/3)

**What:** Все новые endpoints `/api/v1/campaigns/*` под `Depends(auth_dep)` + явный workspace filter `where(Campaign.workspace_id == ctx.workspace_id)` + `# TODO(v2-rls)` метки.

**When to use:** Любой endpoint, читающий/пишущий campaigns или связанные таблицы.

**Example:**

```python
# Source: app/routers/agents.py:70-85 (Phase 3 working pattern)
async def _load_campaign(db: AsyncSession, ctx: AuthCtx, campaign_id: UUID) -> Campaign:
    result = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy app.workspace_id
        )
    )
    campaign = result.scalars().first()
    if campaign is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CAMPAIGN_NOT_FOUND", "message": "Campaign not found"},
        )
    return campaign
```

### Pattern 2: Background worker singleton (reuse Phase 2)

**What:** `CampaignEnqueueWorker` повторяет паттерн `ContactCheckWorker` из `app/services/contact_check_worker.py` — singleton instance, `start()`/`stop()` в FastAPI lifespan.

**When to use:** Любой periodic background task в API-контейнере.

**Example:**

```python
# Source: app/services/contact_check_worker.py:47-93 (Phase 2 working pattern)
class CampaignEnqueueWorker:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.batch_size = CAMPAIGN_ENQUEUE_BATCH_SIZE      # env-conf, default 500
        self.poll_interval = CAMPAIGN_ENQUEUE_TICK_SECONDS  # env-conf, default 30

    def start(self):
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._run(), name="campaign-enqueue-worker")
            logger.info(f"📤 CampaignEnqueueWorker started (batch={self.batch_size}, poll={self.poll_interval}s)")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("📤 CampaignEnqueueWorker stopped")

    async def _run(self):
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"❌ CampaignEnqueueWorker tick error: {exc}", exc_info=True)
            await asyncio.sleep(self.poll_interval)

    async def _tick(self) -> int:
        # 1. SELECT running campaigns (workspace-isolated naturally — campaign carries workspace_id)
        # 2. Per campaign: SELECT contacts из folder где tg_status='registered' AND NOT IN campaign_contact_assignments LIMIT N
        # 3. get_or_assign_sender(campaign_id, contact_phone) → INSERT в campaign_contact_assignments (UNIQUE защищает от race)
        # 4. render_template(campaign.message_template, contact) → final text
        # 5. Bulk INSERT в message_queue (workspace_id, campaign_id, sender_id, recipient_phone, recipient_name, message_text, scheduled_at, status='pending')
        ...


campaign_enqueue_worker = CampaignEnqueueWorker()  # module-level singleton
```

### Pattern 3: Per-campaign queue scheduling (replace global constants)

**What:** Выпиливаем глобальные `MOSCOW_TZ`, `WORK_HOUR_START/END`, `_is_working_hours()`, `_next_working_window()` (queue.py:62-65, 111-125). Per-campaign проверка через JOIN при выборе следующего item'а.

**When to use:** В `_tick()` и `_process_next_for_sender()` — заменяем глобальную working-hours проверку.

**Example (рекомендуемая структура):**

```python
# REPLACE queue.py:127-135 (_tick global filter) on per-campaign check
# Sketch — planner подберёт final shape

async def _tick(self):
    async with AsyncSessionLocal() as db:
        # SELECT distinct sender_ids с pending items in active campaigns within working hours
        rows = await db.execute(text("""
            SELECT DISTINCT mq.sender_id
            FROM message_queue mq
            JOIN campaigns c ON c.id = mq.campaign_id
            WHERE mq.status = 'pending'
              AND mq.scheduled_at <= NOW()
              AND c.status = 'running'
              AND (c.stop_date IS NULL OR NOW() < c.stop_date)
              AND (c.start_date IS NULL OR NOW() >= c.start_date)
              -- working hours check via timezone — see helper below
              AND _campaign_in_working_window(c.id, NOW()) = true
        """))
        # alt: SELECT campaigns rows + Python-side filter (zoneinfo trickier in SQL)
```

**Alternative (proven simpler):** SELECT campaigns/sender_ids в Python, filter с `zoneinfo.ZoneInfo(c.timezone)` в Python (для нескольких десятков running campaigns цена приемлема — это per-tick, не per-message).

### Pattern 4: Built-in LLM tools + custom tools merge

**What:** В `ai_engine.generate_response()` массив `tools` собирается из (a) 3 built-in (mark_as_lead / transfer_to_manager / finish_conversation) с description из campaign hints + (b) custom через существующий `build_tools(campaign.tools)`. При парсинге `response.tool_calls[]` диспатч по имени: built-in → новая ветка обработки, custom → существующий `execute_webhook()`.

**When to use:** Заменяет существующий `build_tools(context["webhook_functions"])` в ai_engine.py:301.

**Example:**

```python
# Source: app/services/ai_engine.py:165-199 (build_tools — reuse for custom)
# + Plan 04-05 addition: built-in tools

BUILT_IN_TOOL_NAMES = {"mark_as_lead", "transfer_to_manager", "finish_conversation"}

def build_builtin_tools(campaign: dict) -> list:
    """3 built-in tools — description из *_trigger_hint полей кампании."""
    return [
        {
            "type": "function",
            "function": {
                "name": "mark_as_lead",
                "description": (
                    f"Mark this contact as a qualified lead. "
                    f"Use when: {campaign.get('lead_trigger_hint') or 'клиент явно подтвердил интерес или готовность купить'}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"reason": {"type": "string", "description": "Краткое объяснение почему вы помечаете контакт лидом"}},
                    "required": ["reason"],
                },
            },
        },
        # ... аналогично для transfer_to_manager + finish_conversation
    ]


# В generate_response (новая обработка tool_calls):
for tool_call in response_message.tool_calls:
    func_name = tool_call.function.name
    func_args = json.loads(tool_call.function.arguments or "{}")
    if func_name in BUILT_IN_TOOL_NAMES:
        # NEW: built-in dispatch
        await self._handle_builtin_signal(
            db=session,
            conversation_id=conversation_id,
            campaign_id=campaign_id,
            signal=func_name,
            reason=func_args.get("reason", ""),
            campaign=campaign,
        )
        # NB: tool result в LLM НЕ возвращается (D-12) — собираем reply из text_content параллельного ответа
    else:
        # EXISTING: custom webhook dispatch (unchanged)
        ...
```

### Pattern 5: Sender lock acquire через transaction-level lock (D-03)

**What:** При `POST /campaigns/{id}/start` проверяем конфликт по `campaign_senders` + `campaigns.status='running'`. Race-safety — через `SELECT ... FOR UPDATE` на собственной campaigns row, плюс UPDATE status в той же транзакции.

**When to use:** Только в `start`/`resume` endpoints.

**Example:**

```python
async def start_campaign(campaign_id, ctx, db):
    async with db.begin():
        # 1. Lock campaign row first
        camp = (await db.execute(
            text("SELECT id, status, workspace_id FROM campaigns WHERE id=:cid AND workspace_id=:wid FOR UPDATE"),
            {"cid": str(campaign_id), "wid": str(ctx.workspace_id)},
        )).fetchone()
        if not camp:
            raise HTTPException(404, ...)
        if camp.status not in ("draft", "paused"):
            raise HTTPException(409, {"code": "INVALID_TRANSITION", ...})

        # 2. Sender lock check (cross-campaign within workspace)
        conflicts = (await db.execute(
            text("""
                SELECT cs.sender_id, c.id, c.name
                FROM campaign_senders cs
                JOIN campaigns c ON c.id = cs.campaign_id
                WHERE cs.sender_id IN (
                    SELECT sender_id FROM campaign_senders WHERE campaign_id = :cid
                )
                AND c.status = 'running'
                AND c.id != :cid
                AND c.workspace_id = :wid
            """),
            {"cid": str(campaign_id), "wid": str(ctx.workspace_id)},
        )).fetchall()

        if conflicts:
            raise HTTPException(409, {
                "code": "SENDER_LOCK_CONFLICT",
                "conflicts": [{"sender_id": str(r[0]), "campaign_id": str(r[1]), "campaign_name": r[2]} for r in conflicts],
            })

        # 3. UPDATE status='running'
        await db.execute(text("UPDATE campaigns SET status='running' WHERE id=:cid"), {"cid": str(campaign_id)})
        # commit при выходе из async with
```

### Anti-Patterns to Avoid

- **Webhook вызов inside DB transaction:** Fire-and-forget через `asyncio.create_task(self._fire_callback(...))` — НЕ блокирует TX. Pattern из `queue.py:_fire_callback` (строка 731).
- **Возврат built-in tool result в LLM:** D-12 явно — для built-in мы НЕ возвращаем tool result во второй вызов `client.chat.completions.create()`. Диалог уже зафиксирован (status='lead'/'handoff'/'finished'). В чат уходит `response_message.content` если есть (а LLM мог параллельно вернуть текст и tool_call).
- **Полное копирование queue items в /duplicate:** C-11 рекомендация — НЕ копировать queue items и НЕ копировать campaign_contact_assignments при duplicate. Только row + campaign_senders.
- **Хардкод "Europe/Moscow" в новом коде:** Per D-08, default в БД-уровне, в коде читаем `campaign.timezone`.
- **Авто-переход campaign.status:** D-04, никаких background-тиков, переводящих статусы. Только manual через endpoints.
- **PATCH со status в body:** Используем explicit action endpoints `/start`, `/pause`, `/resume`, `/finish` (D-04). Это понятнее в логах и менее ambiguous для UI.
- **`message_text.format(**vars)`:** Python `str.format` упадёт на любом `{` в тексте кампании (например JSON, формулы). Используем regex `\{\{...\}\}` substitution (D-19, C-03).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Webhook signature/security | Custom HMAC scheme | Deferred to v2 (D-13 С-01); сейчас raw POST fire-and-forget | Phase 4 принципиально fire-and-forget без auth; ssl/HMAC — v2 |
| Variable substitution | `str.format()` or `Jinja2` | stdlib `re` с regex `\{\{\s*([a-zа-я_]+(\.[a-z_0-9]+)?)\s*\}\}` | Jinja2 — overkill для 5 vars; format() ломается на `{` в шаблоне |
| Background task scheduler | `apscheduler`/`celery` | asyncio task в lifespan (паттерн `ContactCheckWorker`) | Phase 2 уже работающий паттерн — без Redis/Celery |
| OpenAI tool calling JSON-schema | Custom JSON-schema builder | OpenAI SDK Python `pydantic_function_tool()` helper *или* ручной dict (уже есть в `build_tools`) | OpenAI SDK поддерживает Pydantic-генерацию автоматически |
| Timezone resolution | Custom `MSK_OFFSET = 3` арифметика | `zoneinfo.ZoneInfo(campaign.timezone)` + `datetime.astimezone()` | warmup.py делает ручную арифметику (CONCERNS.md «Hardcoded Moscow timezone») — Phase 4 этот паттерн НЕ повторяет |
| HTTP fire-and-forget | thread + sync `requests` | `asyncio.create_task(httpx_post(...))` (паттерн `_fire_callback`) | Уже работающий паттерн; async-only convention |
| Campaign duplicate-name guard | Custom retry loop | UNIQUE constraint + ON CONFLICT DO NOTHING (паттерн `folders.get_or_create_by_name`) — или retry-on-IntegrityError (паттерн `agents.duplicate_agent`) | Race-safe |
| Lifecycle audit log | Сейчас не нужно | TODO для Phase 5/ANLX-05 | C-12 deferred |
| Cron-like парсинг "Mo,Tu,Fr,09:00-20:00" | String parsing | INT bitmask `work_days_mask` (D-10) + integers work_hour_start/end (D-09) | Простая bitwise проверка `(mask & (1 << weekday)) != 0` |

**Key insight:** Phase 4 — это в основном «соединить уже работающие куски через новую entity Campaign». Big-ticket reusable assets (queue worker, ai_engine, rotation, listener, telegram_service) переиспользуются почти без изменений API. Hand-rolling появляется только в трёх местах: (1) sender lock check для CAMP-04, (2) built-in tools dispatch в ai_engine, (3) render_template для variable substitution. Всё остальное — копирование Phase 2/3 паттернов.

## Runtime State Inventory

> Phase 4 — частично refactoring/migration phase: переезд `webhook_functions` с дропнутого `ai_contexts` на новый `campaigns`, рерайт `send.py`, изменение сигнатуры `get_or_assign_sender`. Это требует runtime state аудита.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| **Stored data** | (1) `context_contact_assignments` — Phase 1 DB-clean rule: пусто. DROP TABLE без migration. (2) `message_queue` существующие записи (если есть на dev DB) — после `ALTER TABLE message_queue ADD COLUMN campaign_id NOT NULL` упадёт. **БД чистая** (Phase 1 D-01) — поэтому acceptable. Если planner найдёт `message_queue` с rows на dev — нужно DROP TABLE или truncate перед миграцией. | Verify DB empty before applying 016; or document «migration drops existing queue» |
| **Live service config** | (3) Lovable UI текущие формы могут отсылать `ai_context_id` в `/api/v1/send` body (Phase 3 contract). После Phase 4 эндпоинт принимает `campaign_id`. **Коммуникация Lovable**: planner Phase 4 должен отметить breaking change в endpoint contract. | Document in PHASE_DONE message — Lovable надо переключиться на `campaign_id` |
| **OS-registered state** | None — нет cron tasks, нет Task Scheduler entries, нет systemd unit с упоминанием context_id. WarmupWorker / ContactCheckWorker / QueueWorker — модуль-level singletons, перезапускаются с container'ом. | None — verified by grep |
| **Secrets/env vars** | (4) Новые env vars: `CAMPAIGN_ENQUEUE_TICK_SECONDS=30`, `CAMPAIGN_ENQUEUE_BATCH_SIZE=500` — добавить в `docker-compose.yml` и `app/config.py`. (5) `OPENAI_API_KEY` — уже есть, не меняется. (6) `SUPABASE_JWT_SECRET` — не меняется. | Add 2 new env vars to compose + config |
| **Build artifacts / installed packages** | None — нет egg-info / нет docker image rebuilds, кроме standard `docker compose up -d --build api` (CLAUDE.md). Listener container в Phase 4 НЕ меняется (нет новых dependencies, нет файлов листенера переписываемых полностью). | None — standard `docker compose up -d --build api listener` после деплоя |

**Key takeaway:** БД чистая → DROP TABLE `context_contact_assignments` / ALTER TABLE без backfill. Единственное «runtime breaking» — это рерайт `/api/v1/send` body с `ai_context_id` на `campaign_id` — Lovable UI должен синхронно переключиться.

## Common Pitfalls

### Pitfall 1: OpenAI parallel tool calls — built-in + custom в одном response

**What goes wrong:** LLM в одном `chat.completions.create()` может вернуть несколько tool calls сразу — например `mark_as_lead` и `transfer_to_manager` параллельно, или `mark_as_lead` + custom `save_to_crm`. Если код обрабатывает только первый — теряются события и conversation.status конфликтует.

**Why it happens:** OpenAI SDK Chat Completions API возвращает `response.choices[0].message.tool_calls` — это **массив**, а не одиночный объект. Существующий код `ai_engine.py:324-364` уже корректно итерирует по `tool_calls`, но Phase 4 добавляет новую ветку (built-in) и легко забыть, что одна итерация может содержать оба типа.

**How to avoid:**

1. **Сначала обработать ВСЕ tool_calls** — собрать массив built-in actions, отдельно массив custom webhook calls.
2. Built-in выполнить с `await asyncio.gather(...)` (UPDATE status + fire webhook) — но **с приоритетом**: `finish` > `handoff` > `lead` (если LLM почему-то вызвал несколько). Финальный status = highest-priority.
3. Custom tools — как сейчас, через `execute_webhook` (parallel safe).
4. Для built-in: если хотя бы один из `finish`/`handoff` сработал — НЕ делать второй `chat.completions.create()` (диалог закрыт). Если только `lead` — можно сделать второй вызов чтобы получить текст ответа.

**Warning signs:** В тестах должен быть кейс «LLM вернул 2 tool calls» с моком на `client.chat.completions.create`.

### Pitfall 2: PostgreSQL ALTER TYPE ADD VALUE — нельзя внутри транзакции

**What goes wrong:** Если planner решит превратить `conversations.status` String(20) в PostgreSQL ENUM type (C-13 option B) и потом расширить новыми значениями `'lead'`, `'handoff'`, `'finished'` — `ALTER TYPE conversation_status ADD VALUE 'lead'` упадёт с ошибкой `ALTER TYPE ... ADD cannot run inside a transaction block`, если миграция обёрнута в `BEGIN; ... COMMIT;` (а это паттерн всех migrations 012-015).

**Why it happens:** PostgreSQL ограничение — `ALTER TYPE ... ADD VALUE` нельзя в transaction block. После добавления значение становится доступно только после commit'а текущей транзакции.

**How to avoid:**

1. **Рекомендация (D-21 paттерн Phase 2):** Оставить `conversations.status` как `String(20)` + добавить CHECK constraint `conversations_status_check CHECK (status IN ('active','manual','paused','lead','handoff','finished'))`. Это идемпотентно в одной транзакции.
2. **Если SQLEnum (C-13 option B):** Создать **новый** ENUM type в одной миграции, ALTER TABLE сменить тип колонки с String на новый ENUM в той же миграции. Это `ALTER TYPE ... CREATE TYPE ... AS ENUM (...)` (создание полного нового типа допустимо в транзакции). Никаких `ADD VALUE` к существующему типу не делаем.
3. Если **очень нужно** добавить в существующий ENUM — миграционный runner должен выполнить `ALTER TYPE ADD VALUE` отдельным statement'ом БЕЗ обёртки BEGIN/COMMIT (autocommit mode). В текущем outreach-platform нет migration runner'а (Phase 1 C-04 unresolved) — миграции применяются вручную или через `exec_driver_sql` в conftest. Это сложнее.

**Warning signs:** Tests падают с `ERROR: ALTER TYPE ... ADD cannot run inside a transaction block`. См. test_migration_016.py.

### Pitfall 3: CampaignEnqueueWorker race с move контакта между папками

**What goes wrong:** Phase 2 D-04 разрешает `POST /contacts/{id}/move` для перемещения контакта между папками. Сценарий race:

1. CampaignEnqueueWorker tick: SELECT contacts WHERE folder_id=A AND NOT IN campaign_contact_assignments → возвращает contact X.
2. Юзер делает `POST /contacts/X/move` → folder_id меняется с A на B.
3. Worker делает INSERT в `campaign_contact_assignments` для contact X (всё ещё мыслит, что он в folder A) + INSERT в `message_queue`.
4. Результат: контакт «осиротел» — он в folder B, но получит сообщение от кампании folder A.

**Why it happens:** Между шагами 1 и 3 нет lock'а на contact row.

**How to avoid:**

1. Добавить `FOR UPDATE` в SELECT contacts (lock на время tick'а). Минус: блокирует POST /contacts/move на десятки секунд. Не рекомендуется.
2. **Лучше:** При INSERT в `message_queue` делать **WHERE folder_id=:expected** check на contact:
   ```sql
   INSERT INTO message_queue (..., recipient_phone, ...) 
   SELECT ..., :phone, ...
   WHERE EXISTS (SELECT 1 FROM contacts WHERE id=:cid AND folder_id=:expected_folder)
   ```
   Если контакт переехал — INSERT просто не выполнится, на следующем tick'е worker нового folder его подхватит.
3. **Простейший вариант (recommended for v1):** Принять race как acceptable — контакт получит одно лишнее сообщение от кампании старой папки, потом будет работать в новой. Документировать в `move` endpoint warning: «контакт может получить уже запланированное сообщение от предыдущей кампании».

**Warning signs:** Интеграционный тест `test_campaign_enqueue_worker_move_race` должен покрыть кейс.

### Pitfall 4: Sender lock check race — два POST /start одновременно

**What goes wrong:** Два юзера одного workspace делают `POST /campaigns/A/start` и `POST /campaigns/B/start` одновременно, обе кампании привязали один общий sender X. Без advisory lock обе пройдут select-then-insert — обе станут running → sender X в двух running кампаниях (нарушение D-03).

**Why it happens:** SELECT existing conflicts + UPDATE status = TOCTOU race.

**How to avoid:**

1. **Рекомендация:** В транзакции `POST /start` делать `SELECT * FROM campaigns WHERE id=:cid FOR UPDATE` сначала — это row-lock на самой кампании. НО это не защищает от двух разных кампаний.
2. **Лучше:** Брать advisory lock per workspace: `SELECT pg_advisory_xact_lock(:workspace_id_hash)`. Все `POST /start` в одном workspace сериализуются. Стоимость — несколько ms.
3. **Альтернатива:** Создать UNIQUE constraint partial index `CREATE UNIQUE INDEX ... ON campaign_senders(sender_id) WHERE EXISTS (SELECT 1 FROM campaigns WHERE id=campaign_senders.campaign_id AND status='running')` — но partial с subquery не работает в Postgres. Можно через trigger, но это сложно.
4. **Простейший вариант (recommended for v1):** Принимать TOCTOU race как редкий — два concurrent POST /start в одном workspace в одну миллисекунду маловероятны для outreach SaaS. Документировать в audit Plan 04-01 как known limitation.

**Warning signs:** Интеграционный тест `test_concurrent_start_sender_lock` (запустить 2 task'а одновременно через asyncio.gather, проверить что только одна стала running).

### Pitfall 5: Variable substitution Unicode + escaping

**What goes wrong:** Текст шаблона может содержать `{{` в неожиданных местах (например, JSON snippet в подписи). Простой regex `\{\{(\w+)\}\}` найдёт ложные совпадения и подставит пустоту, ломая текст.

**Why it happens:** Variable names могут быть на русском (`{{имя}}`), regex `\w` зависит от Python re flags (`re.UNICODE`).

**How to avoid:**

1. Regex `r"\{\{\s*([a-zа-я_]+(?:\.[a-z_0-9]+)?)\s*\}\}"` с `re.IGNORECASE | re.UNICODE` явно — включает кириллицу + допускает пробелы внутри `{{ name }}` (C-03 рекомендация).
2. Не подставлять «потенциальное» совпадение — если `\1` group не в whitelist переменных + не в `custom.*` префиксе → оставлять `{{X}}` as-is (или возвращать empty + warning, D-19).
3. **Тесты:** unit-test для `render_template` с edge cases: `{{name}}`, `{{ name }}`, `{{NAME}}`, `{{имя}}`, `{{custom.company}}`, `{{custom.компания}}`, `{{not_a_var}}` (warning + empty), JSON snippet `{"key": "value"}` (НЕ должен резолвиться).

**Warning signs:** Сообщения уходят с пустыми скобками или поломанным текстом — нужны тесты на edge cases.

### Pitfall 6: `conversations.campaign_id` NULLable + Phase 5 inbox confusion

**What goes wrong:** D-05 — `conversations.campaign_id` NULLable. Старые conversations Phase 3 имеют NULL. Новые Phase 4 — заполнены. Phase 5 inbox должен показывать оба, но фильтр «по кампании X» должен НЕ показывать NULL.

**Why it happens:** Semantic split между outbound (всегда из кампании) и inbound (может быть без).

**How to avoid:**

1. Документировать в Plan 04-02 как явное условие в `CampaignResponse` API: «conversations без campaign_id — legacy, inbox их покажет в общей ленте».
2. Phase 5 design — TODO note в RESEARCH/PLAN: фильтр должен использовать `WHERE campaign_id = :id` (strict — без NULL) для кампания-specific view, и `WHERE campaign_id IS NULL OR campaign_id = :id` для «все диалоги с возможностью фильтра».

**Warning signs:** Phase 5 тесты покажут несоответствие — пока что только flag в Plan 04-02 description.

### Pitfall 7: Built-in tool description без trigger_hint — generic LLM behavior

**What goes wrong:** Если у кампании `lead_trigger_hint IS NULL`, fallback description «Mark as a qualified lead» слишком generic — LLM начнёт помечать как лида на любое позитивное «спасибо», создавая шум.

**Why it happens:** LLM tool calling в OpenAI sensitive к описаниям. Без конкретного hint модель тяготеет к over-triggering.

**How to avoid:**

1. **Required-by-default UI:** Lovable требует заполнить hint при создании кампании. Можно сделать обязательным на API (Pydantic validator).
2. **OR — default description более restrictive:** «Mark contact as lead ONLY when contact explicitly confirms interest in buying, requests pricing, or asks for a meeting. Do not mark for casual greetings or general questions.» Это снижает false positives.
3. **Test:** Unit-test с моком на OpenAI client и проверкой что built-in tools переданы с правильным description.

**Warning signs:** Production logs покажут много false-positive marks. В Phase 4 — мониторить руками, в Phase 5 (ANLX-05 LLM request log) — будет видно в логах prompt+response.

### Pitfall 8: Workspace isolation в CampaignEnqueueWorker через JOIN

**What goes wrong:** Worker SELECT'ит contacts из folders + senders из campaign_senders — если SQL JOIN не enforce'ит workspace_id явно, теоретически возможна cross-workspace утечка (sender workspace A видит contact workspace B).

**Why it happens:** Phase 02.1 CR-01 closed это для других writers — Phase 4 worker должен повторить паттерн.

**How to avoid:**

1. Каждый INSERT в `message_queue` / `campaign_contact_assignments` ДОЛЖЕН содержать `workspace_id`. Источник: `campaigns.workspace_id` (NOT NULL FK). Sender, контакт и folder — все принадлежат тому же workspace по invariant'у (campaign требует agent/folder/sender одного workspace).
2. В SELECT добавить явный guard: `WHERE c.workspace_id = f.workspace_id AND c.workspace_id = s.workspace_id` — defence-in-depth (паттерн `rotation._pick_best_sender` Phase 02.1 CR-03).
3. **Test:** Интеграционный тест `test_campaign_enqueue_workspace_isolation` — создать 2 workspace, в каждом по campaign+folder+contacts, проверить что `message_queue` rows одного workspace не содержат contacts другого.

**Warning signs:** Code review должен проверить наличие явных workspace_id guards.

## Code Examples

Верифицированные паттерны из живого кода и стандартных источников.

### Пример 1: Workspace-scoped CRUD endpoint (reuse Phase 3 pattern)

```python
# Source: app/routers/agents.py:118-176 (Phase 3 — working pattern)

@router.post("", response_model=CampaignResponse, status_code=201)
async def create_campaign(
    payload: CampaignCreate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    # 1. Workspace-isolated existence checks
    agent = (await db.execute(
        select(AIContext).where(
            AIContext.id == payload.agent_id,
            AIContext.workspace_id == ctx.workspace_id,
        )
    )).scalars().first()
    if agent is None:
        raise HTTPException(404, {"code": "AGENT_NOT_FOUND", "message": "Agent not in your workspace"})

    folder = (await db.execute(
        select(Folder).where(
            Folder.id == payload.folder_id,
            Folder.workspace_id == ctx.workspace_id,
        )
    )).scalars().first()
    if folder is None:
        raise HTTPException(404, {"code": "FOLDER_NOT_FOUND", "message": "Folder not in your workspace"})

    # 2. Validate timezone (D-08)
    try:
        zoneinfo.ZoneInfo(payload.timezone)
    except zoneinfo.ZoneInfoNotFoundError:
        raise HTTPException(422, {"code": "INVALID_TIMEZONE", "message": f"Unknown IANA timezone '{payload.timezone}'"})

    # 3. Duplicate name check
    existing = (await db.execute(
        select(Campaign).where(
            Campaign.workspace_id == ctx.workspace_id,
            Campaign.name == payload.name.strip(),
        )
    )).scalars().first()
    if existing:
        raise HTTPException(409, {"code": "CAMPAIGN_NAME_DUPLICATE", "message": f"Campaign '{payload.name}' already exists"})

    # 4. Create
    campaign = Campaign(
        workspace_id=ctx.workspace_id,
        agent_id=payload.agent_id,
        folder_id=payload.folder_id,
        name=payload.name.strip(),
        # ... все остальные поля
        status="draft",
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)

    # 5. Attach senders (campaign_senders rows)
    for sender_id in payload.sender_ids:
        db.add(CampaignSender(
            workspace_id=ctx.workspace_id,
            campaign_id=campaign.id,
            sender_id=sender_id,
        ))
    await db.commit()

    return _campaign_to_response(db, campaign)
```

### Пример 2: render_template (D-19)

```python
# Source: NEW — app/services/template.py

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Phase 4 D-19 / C-02 / C-03: Mustache-style {{var}}, поддержка точечной нотации
# и русских алиасов. Strict без filters (no {{name | upper}}).
TEMPLATE_VAR_RE = re.compile(
    r"\{\{\s*([a-zа-я_][a-zа-я_0-9]*(?:\.[a-z_0-9]+)?)\s*\}\}",
    re.IGNORECASE | re.UNICODE,
)

# C-02 placeholder — точная таблица определяется planner'ом
RUSSIAN_ALIASES = {
    "имя": "name",
    "username": "username",
    "телефон": "phone",
    "источник": "source",
    "компания": "custom.company",
}


def _resolve(var_name: str, contact: dict[str, Any]) -> Optional[str]:
    """Resolve {{var}} → contact field. Returns None if missing."""
    var_name = var_name.lower()
    # Russian alias
    if var_name in RUSSIAN_ALIASES:
        var_name = RUSSIAN_ALIASES[var_name]

    if "." in var_name:
        # custom.X → contact.custom.get(X)
        prefix, key = var_name.split(".", 1)
        if prefix == "custom":
            custom = contact.get("custom") or {}
            value = custom.get(key)
            return str(value) if value is not None else None
        return None

    if var_name == "name":
        return contact.get("full_name") or None
    if var_name == "username":
        u = contact.get("username")
        return f"@{u}" if u else None
    if var_name == "phone":
        return contact.get("phone") or None
    if var_name == "source":
        return contact.get("source") or None

    return None


def render_template(template: str, contact: dict[str, Any], *, campaign_id: str = "?", phone: str = "?") -> str:
    """Render Mustache-style template with contact fields.

    Missing variables → empty string + warning (D-19). Strict mode → v2.
    """
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        value = _resolve(var_name, contact)
        if value is None:
            logger.warning(
                f"Template variable {{{{{var_name}}}}} missing for contact {phone}, campaign {campaign_id}"
            )
            return ""
        return value

    return TEMPLATE_VAR_RE.sub(replacer, template)
```

### Пример 3: OpenAI tools assembly + parallel tool calls handling

```python
# Source: app/services/ai_engine.py:300-394 (existing pattern, extended for Phase 4)

# Phase 4: built-in tools всегда добавляются (D-12)
all_tools = build_builtin_tools(campaign) + build_tools(campaign.get("tools", []))

request_params = {
    "model": "gpt-5-mini-2025-08-07",  # NB: known bug — model ID hardcoded; not part of Phase 4
    "messages": messages,
    "max_completion_tokens": 2000,
    "tools": all_tools,
    "tool_choice": "auto",
}

response = await client.chat.completions.create(**request_params)
response_message = response.choices[0].message

if response_message.tool_calls:
    builtin_signals = []   # Phase 4 NEW
    custom_calls = []      # Phase 4 EXISTING flow

    for tool_call in response_message.tool_calls:
        func_name = tool_call.function.name
        try:
            func_args = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError:
            logger.error(f"Bad JSON args for tool {func_name}: {tool_call.function.arguments[:200]}")
            continue

        if func_name in BUILT_IN_TOOL_NAMES:
            builtin_signals.append((func_name, func_args.get("reason", "")))
        else:
            custom_calls.append((tool_call, func_name, func_args))

    # 1. Process built-in signals (priority: finish > handoff > lead)
    PRIORITY = {"finish_conversation": 0, "transfer_to_manager": 1, "mark_as_lead": 2}
    builtin_signals.sort(key=lambda x: PRIORITY.get(x[0], 99))

    final_status = None
    for signal_name, reason in builtin_signals:
        final_status = await self._handle_builtin_signal(
            db=session, conversation_id=conversation_id, campaign=campaign,
            signal=signal_name, reason=reason,
        )

    # 2. Process custom webhooks (parallel-safe via existing flow)
    if custom_calls:
        # ... existing build messages + second client.chat.completions.create() flow
        ...
    
    # 3. Decide what to return
    if final_status in ("handoff", "finished"):
        # Conversation closed — AI больше не отвечает в этом chat'е.
        # Если LLM генерировал текст параллельно — отдаём его как "прощальное" сообщение.
        return response_message.content.strip() if response_message.content else None
    
    # lead OR только custom tools — нормальный return через второй call (existing flow)
    ...
```

### Пример 4: Migration 016 skeleton (raw SQL idempotent)

```sql
-- Source: migrations/016_phase4.sql — pattern from migrations/015_phase3.sql:9-26

BEGIN;

-- ── 1. campaigns table (D-04, D-08..D-11, D-13, D-14) ──────────────────────
CREATE TABLE IF NOT EXISTS campaigns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    agent_id        UUID NOT NULL REFERENCES ai_contexts(id) ON DELETE RESTRICT,
    folder_id       UUID NOT NULL REFERENCES folders(id) ON DELETE RESTRICT,
    name            VARCHAR(150) NOT NULL,
    description     TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'draft',  -- draft/running/paused/done
    timezone        TEXT NOT NULL DEFAULT 'Europe/Moscow',
    work_hour_start INT NOT NULL DEFAULT 9,
    work_hour_end   INT NOT NULL DEFAULT 20,
    work_days_mask  INT NOT NULL DEFAULT 31,  -- Mo-Fri
    start_date      TIMESTAMPTZ,
    stop_date       TIMESTAMPTZ,
    message_template TEXT NOT NULL,           -- содержит {{name}} плейсхолдеры
    lead_webhook_url    TEXT,
    handoff_webhook_url TEXT,
    finish_webhook_url  TEXT,
    lead_trigger_hint    TEXT,
    handoff_trigger_hint TEXT,
    finish_trigger_hint  TEXT,
    tools           JSONB NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT campaigns_status_check CHECK (status IN ('draft','running','paused','done')),
    CONSTRAINT campaigns_work_hours_check CHECK (work_hour_start >= 0 AND work_hour_end <= 24 AND work_hour_start < work_hour_end),
    CONSTRAINT campaigns_work_days_check CHECK (work_days_mask BETWEEN 1 AND 127)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_workspace_name 
    ON campaigns(workspace_id, name);
CREATE INDEX IF NOT EXISTS idx_campaigns_status_running 
    ON campaigns(workspace_id, status) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_campaigns_agent_id ON campaigns(agent_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_folder_id ON campaigns(folder_id);

-- ── 2. campaign_senders through-table (D-03) ───────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_senders (
    campaign_id  UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    sender_id    UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campaign_id, sender_id)
);
CREATE INDEX IF NOT EXISTS idx_campaign_senders_sender ON campaign_senders(sender_id);

-- ── 3. campaign_contact_assignments (D-06) ─────────────────────────────────
DROP TABLE IF EXISTS context_contact_assignments;

CREATE TABLE IF NOT EXISTS campaign_contact_assignments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    campaign_id   UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    contact_phone VARCHAR(20) NOT NULL,
    sender_id     UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cca_campaign_phone 
    ON campaign_contact_assignments(campaign_id, contact_phone);
CREATE INDEX IF NOT EXISTS idx_cca_sender ON campaign_contact_assignments(sender_id);

-- ── 4. conversations.campaign_id (D-05) ─────────────────────────────────────
ALTER TABLE conversations 
    ADD COLUMN IF NOT EXISTS campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL;

-- C-13 / D-12: extend status CHECK constraint (drop old + add new with extended values)
ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
ALTER TABLE conversations ADD CONSTRAINT conversations_status_check 
    CHECK (status IN ('active','manual','paused','lead','handoff','finished'));

-- ── 5. message_queue.campaign_id (D-16) ────────────────────────────────────
-- БД чистая (Phase 1 D-01) — NOT NULL применяем сразу
ALTER TABLE message_queue 
    ADD COLUMN IF NOT EXISTS campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE SET NULL;

-- C-06: composite index для efficient queue tick'ов
CREATE INDEX IF NOT EXISTS idx_message_queue_workspace_campaign_status_scheduled 
    ON message_queue(workspace_id, campaign_id, status, scheduled_at);

COMMIT;
```

**Note про NOT NULL + ON DELETE SET NULL противоречие:** Если `message_queue.campaign_id` NOT NULL и FK ON DELETE SET NULL — Postgres при удалении campaign упадёт с violation. Нужно либо:
- (a) Сделать `message_queue.campaign_id` NULLable + добавить partial index (рекомендация — пересмотреть D-16 в Plan 04-02).
- (b) Использовать ON DELETE NO ACTION + проверять что нет queue items при DELETE campaign (paranoid).
- (c) Hard delete campaign только для draft/done, для running/paused — 409 (уже D-07). А done campaign с историей queue items — тогда SET NULL имеет смысл только если nullable.

**Recommendation для Plan 04-02:** Сделать `message_queue.campaign_id` **NULLable**, не NOT NULL. CONTEXT.md D-16 говорит «БД чистая → NOT NULL применим сразу» но не учёл противоречие с D-07 hard delete + SET NULL. Planner должен перерешить. Альтернатива: ON DELETE NO ACTION + 409 в DELETE campaign если queue items есть (но это резко усложняет UX).

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Global `MOSCOW_TZ`, `WORK_HOUR_START/END` constants в queue.py | Per-campaign `timezone`, `work_hour_start`, `work_hour_end`, `work_days_mask` | Phase 4 D-08..D-10 | Каждая кампания может иметь своё расписание; multi-tenant ready |
| `ai_contexts.webhook_functions` JSONB (Phase 1-2 era) | `campaigns.tools` JSONB | Phase 3 D-01 drop → Phase 4 D-14 recreate | Webhook/tools — концерн кампании, не агента |
| `ai_contexts.auto_pause_triggers` (lexical keyword match) | 3 built-in LLM tools `mark_as_lead` / `transfer_to_manager` / `finish_conversation` с `*_trigger_hint` semantic descriptions | Phase 3 D-01 drop → Phase 4 D-12 | Семантическое распознавание сигналов через LLM tool calling — лучше чем regex/keyword match |
| `context_contact_assignments` per-agent rotation | `campaign_contact_assignments` per-campaign rotation | Phase 4 D-06 | Sender pool блокируется на уровне кампании (не агента) |
| `POST /api/v1/send` принимает `ai_context_id` в body (Phase 3) | `POST /api/v1/send` принимает `campaign_id` (Phase 4) | Phase 4 D-16 | Agent выводится из campaign; breaking change для Lovable UI и n8n integrations |
| Single OpenAI tool call → single response | Parallel tool calls (OpenAI SDK поддерживает массив) | OpenAI 2024 | Code в `ai_engine.py:324` уже корректно итерирует по массиву; Phase 4 добавляет диспатч built-in vs custom |

**Deprecated/outdated:**

- `services/queue.py` `_is_working_hours()` / `_next_working_window()` глобальные методы — выпиливаются в Phase 4 (заменяются на per-campaign проверки)
- `services/rotation.py` `get_or_assign_sender(context_id, ...)` сигнатура — сигнатура меняется на `(campaign_id, ...)`. Backward compat НЕ поддерживается — старый caller (send.py) переписан в Phase 4.
- `_handle_antispam_signal` (listener.py:813) — остаётся как safety net параллельно с новыми signal-tools (D-12 явно). Не deprecated, complementary.

## Open Questions

1. **`message_queue.campaign_id` NULLable vs NOT NULL — противоречие D-07 hard delete + D-16 NOT NULL + SET NULL FK?**
   - What we know: D-16 говорит NOT NULL (БД чистая, не нужен backfill), D-07 говорит ON DELETE SET NULL — это конфликт при удалении кампании.
   - What's unclear: Какой path выбрать.
   - Recommendation: NULLable + composite index. Запретить DELETE campaign только для running (как D-07). Для done — позволить hard delete + SET NULL. Plan 04-02 должен перерешить D-16.

2. **`POST /campaigns/{id}/duplicate` — включить ли в Phase 4?**
   - What we know: C-11 говорит «опционально, planner оценит».
   - What's unclear: Лоyable UI требует ли это для UX в v1?
   - Recommendation: Включить как небольшой endpoint (15 минут) в Plan 04-02 — копирует campaigns row + campaign_senders, статус='draft', имя «{name} (copy N)» (паттерн agents.py:255).

3. **Конфликт LLM tool_call с message_text reply — может ли LLM вернуть и текст и tool_call одновременно?**
   - What we know: OpenAI Chat Completions API — yes, может вернуть и `content` и `tool_calls` в одном `message`. Pitfall 1 описывает обработку.
   - What's unclear: Что делать с этим текстом, когда `finish_conversation` сработал — отправлять контакту как «прощальное» или дропать?
   - Recommendation: Отправлять. Контакт видит финальную фразу LLM («Спасибо! Я передал ваш запрос менеджеру.») + триггерится handoff_webhook_url. Документировать в Plan 04-05.

4. **Workspace-isolation на campaign_senders — может ли planner добавить sender из ДРУГОГО workspace?**
   - What we know: API endpoint должен валидировать workspace принадлежность каждого sender_id перед INSERT.
   - What's unclear: Уровень defence-in-depth — добавить DB-level CHECK или Trigger?
   - Recommendation: API-level валидация достаточна (паттерн Phase 1-3). DB-level: `workspace_id NOT NULL` на campaign_senders + tests на cross-workspace попытку.

5. **CampaignEnqueueWorker startup recovery — что если worker упал на середине bulk INSERT?**
   - What we know: `campaign_contact_assignments` UNIQUE(campaign_id, contact_phone) защищает от дублей. INSERT в `message_queue` — без UNIQUE.
   - What's unclear: Если упал ПОСЛЕ INSERT в cca, но ДО INSERT в message_queue — контакт «помечен как обработанный», а в queue его нет → молчание.
   - Recommendation: INSERT в обе таблицы в одной транзакции. Если упал — TX rollback'нется, на следующем tick'е переподхватим. Plan 04-04 должен зафиксировать atomic transaction.

## Environment Availability

> Phase 4 — чисто backend changes (Python + PostgreSQL + Docker). Внешние зависимости проверены.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11+ | All Phase 4 code (async features, zoneinfo) | ✓ | 3.11 (Dockerfile) | — |
| PostgreSQL 16 | Migration 016, новые таблицы | ✓ | 16 (docker-compose db service) | — |
| FastAPI 0.109.0 | New router /api/v1/campaigns | ✓ | 0.109.0 | — |
| OpenAI Python SDK >=1.40.0 | Built-in LLM tools (parallel tool calls) | ✓ | 1.40.0+ (requirements.txt) | — |
| Telethon 1.42.0 | Не трогаем — reuse telegram_service | ✓ | 1.42.0 | — |
| pytest 8+ / pytest-asyncio 0.23+ | tests/test_*.py для Phase 4 | ✓ | 8.0+ / 0.23+ | — |
| Docker Compose | docker compose up -d --build api/listener (CLAUDE.md deploy) | ✓ (предположительно на dev/prod VPS) | — | — |
| Supabase | Auth (Phase 1 dependency) | ✓ | — (external SaaS) | — |
| zoneinfo (Python stdlib) | Timezone validation в D-08 | ✓ | stdlib | — |

**Missing dependencies with no fallback:** None — все нужные пакеты уже в requirements.txt.

**Missing dependencies with fallback:** None.

**Note про OpenAI API key:** `OPENAI_API_KEY` env var — assumption что задан в docker-compose. Тесты используют `sk-test-pytest-only` (conftest.py:15) — реальные OpenAI вызовы мокаются в test_ai_engine.py.

**Note про CONCERNS.md «Known Bug» OpenAI model ID:** `gpt-5-mini-2025-08-07` хардкод — НЕ Phase 4 fix (отдельная фаза). Ассумим что либо bug fixed в config.py до Phase 4 execute, либо тесты мокают `client.chat.completions.create` напрямую.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8+ / pytest-asyncio 0.23+ (asyncio_mode=auto) |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `pytest tests/test_<module>.py -x --tb=short` |
| Full suite command | `pytest tests/` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CAMP-01 | POST /campaigns создаёт кампанию | integration | `pytest tests/test_campaigns.py::test_create_campaign -x` | ❌ Wave 0 |
| CAMP-02 | agent_id валидируется workspace-scoped | integration | `pytest tests/test_campaigns.py::test_create_with_other_workspace_agent_404 -x` | ❌ Wave 0 |
| CAMP-03 | folder_id валидируется workspace-scoped | integration | `pytest tests/test_campaigns.py::test_create_with_other_workspace_folder_404 -x` | ❌ Wave 0 |
| CAMP-04 | Sender lock — два start с общим sender → 409 | integration | `pytest tests/test_campaigns.py::test_sender_lock_conflict_on_start -x` | ❌ Wave 0 |
| CAMP-05 | timezone+work_hour+days сохраняются | integration | `pytest tests/test_campaigns.py::test_schedule_fields -x` | ❌ Wave 0 |
| CAMP-06 | start_date / stop_date сохраняются и применяются | integration | `pytest tests/test_queue_per_campaign_schedule.py::test_stop_date_skip -x` | ❌ Wave 0 |
| CAMP-07 | Все 4 status'а валидируются CHECK constraint | integration | `pytest tests/test_migration_016.py::test_status_check_constraint -x` | ❌ Wave 0 |
| CAMP-08 | Lifecycle переходы (start/pause/resume/finish) | integration | `pytest tests/test_campaigns.py::test_lifecycle_transitions -x` | ❌ Wave 0 |
| CAMP-09 | Контакт добавлен в папку → tick → item в queue | integration | `pytest tests/test_campaign_enqueue_worker.py::test_new_contact_enqueued -x` | ❌ Wave 0 |
| CAMP-10 | render_template `{{name}}` etc | unit | `pytest tests/test_template.py -x` | ❌ Wave 0 |
| CAMP-11 | mark_as_lead → conversation.status='lead' + webhook | integration | `pytest tests/test_ai_engine_signals.py::test_mark_as_lead -x` | ❌ Wave 0 |
| CAMP-12 | transfer_to_manager → status='handoff', ai_enabled=false + webhook | integration | `pytest tests/test_ai_engine_signals.py::test_transfer_to_manager -x` | ❌ Wave 0 |
| CAMP-13 | finish_conversation → status='finished', ai_enabled=false + webhook | integration | `pytest tests/test_ai_engine_signals.py::test_finish_conversation -x` | ❌ Wave 0 |
| CAMP-14 | 3 webhook URL могут быть NULL без error | integration | `pytest tests/test_ai_engine_signals.py::test_null_webhook_no_error -x` | ❌ Wave 0 |
| CAMP-15 | Custom tools работают (build_tools backward compat) | integration | `pytest tests/test_ai_engine.py::test_custom_tools_from_campaign -x` | ❌ Wave 0 (extend existing) |
| CAMP-16 | LLM получает built-in + custom tools одновременно | integration | `pytest tests/test_ai_engine_signals.py::test_builtin_and_custom_tools_merged -x` | ❌ Wave 0 |
| CAMP-17 | message_queue.campaign_id NOT NULL enforced | integration | `pytest tests/test_migration_016.py::test_queue_campaign_id_not_null -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/test_<module>.py -x --tb=short` (quick run; ~10–30s)
- **Per wave merge:** `pytest tests/ -x` (full suite ~2–5 min с реальной test DB)
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_migration_016.py` — covers CAMP-07, CAMP-17, conversations.status CHECK extension, DROP context_contact_assignments
- [ ] `tests/test_campaigns.py` — covers CAMP-01..08 (CRUD + lifecycle + sender lock)
- [ ] `tests/test_campaign_enqueue_worker.py` — covers CAMP-09 (tick → INSERT queue, race с move контакта, workspace isolation)
- [ ] `tests/test_template.py` — covers CAMP-10 (render_template unit tests: `{{name}}`, `{{ имя }}`, `{{custom.X}}`, missing var, edge cases JSON in template)
- [ ] `tests/test_ai_engine_signals.py` — covers CAMP-11..14, CAMP-16 (built-in tool dispatch с моком на OpenAI client)
- [ ] `tests/test_queue_per_campaign_schedule.py` — covers per-campaign timezone/work_hour/stop_date (regression от глобальных констант)
- [ ] `tests/test_rotation_campaign.py` — covers `get_or_assign_sender(campaign_id, ...)` после рерайта сигнатуры
- [ ] `tests/test_send_campaign.py` — covers рерайт /api/v1/send (body с campaign_id)
- [ ] `tests/conftest.py` extension — добавить `test_campaign_factory`, `test_running_campaign_factory`, `test_campaign_sender_attach` фикстуры
- [ ] Migration runner для test setup в conftest.py:62 — добавить `sql_016 = ... .read_text()` + `await conn.exec_driver_sql(sql_016)`
- [ ] Mock helper для OpenAI client с parallel tool calls (built-in + custom mix)

*(Каждый Wave 0 файл — это test file который надо создать ДО implementation, по TDD-pattern. Существующие test_*.py файлы Phase 1-3 — модель для structure.)*

## Sources

### Primary (HIGH confidence)

- `app/services/queue.py` (lines 38-66, 111-135, 215-358, 691-732) — точные константы для выпиливания, текущая INSERT conversations логика, `_fire_callback` pattern
- `app/services/ai_engine.py` (lines 50-100, 165-199, 201-265, 267-430) — `get_context`, `build_tools`, `execute_webhook`, `generate_response` — текущая логика и точки модификации
- `app/services/listener.py` (lines 247-305, 341-372, 543-803, 813-879) — `_send_to_ai`, `get_active_senders`, `handle_incoming_message`, `_handle_antispam_signal` — паттерны для signal handling
- `app/services/contact_check_worker.py` (entire file, lines 1-283) — pattern для CampaignEnqueueWorker (FOR UPDATE SKIP LOCKED, workspace-isolated SELECT через JOIN, idempotent claim window)
- `app/services/rotation.py` (entire file, lines 1-211) — текущая `get_or_assign_sender(context_id, ...)` сигнатура, race-safety через ON CONFLICT
- `app/routers/agents.py` (entire file) — Phase 3 workspace-scoped CRUD pattern + duplicate-name pattern + retry-on-IntegrityError для race
- `app/routers/folders.py` (entire file) — `get_or_create_by_name` ON CONFLICT pattern, delete with 409 на occupancy
- `app/routers/send.py` (entire file) — pattern которое будем рерайтить под campaign_id
- `app/models/__init__.py` (lines 146-168 AIContext, 217-238 Conversation, 167-214 MessageQueue, 330-345 ContextContactAssignment) — ORM модели для миграции
- `migrations/015_phase3.sql` — pattern для migration 016 (idempotent DROP/CREATE/ALTER)
- `tests/conftest.py` — фикстуры для расширения в Phase 4 (test_workspace, test_sender_factory, test_folder, test_agent_factory)
- `.planning/codebase/CONCERNS.md` (lines 21-26, 53-58, 73-87, 102-115) — Hardcoded Moscow timezone, OpenAI model bug, известные баги, ratelimit логика
- CLAUDE.md (entire file) — главные правила проекта (raw SQL, async, не трогать эмпирические интервалы)

### Secondary (MEDIUM confidence)

- [OpenAI Function Calling Guide](https://developers.openai.com/api/docs/guides/function-calling) — parallel tool calls, JSON Schema format, parsing tool_calls массив (2026 docs)
- [PostgreSQL ALTER TYPE documentation](https://www.postgresql.org/docs/current/sql-altertype.html) — `ALTER TYPE ... ADD VALUE` cannot run in transaction limitation
- [Hookdeck: Anatomy of a Good Webhook Payload](https://hookdeck.com/outpost/guides/webhook-payload-best-practices) — webhook payload best practices (C-01 для shape)
- [Hookdeck: HMAC Signature Verification](https://hookdeck.com/webhooks/guides/how-to-implement-sha256-webhook-signature-verification) — для C-01 HMAC deferred-to-v2 design

### Tertiary (LOW confidence — для информирования, не блокирующее)

- [GitHub Webhook Validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries) — стандартная industry практика (HMAC + timestamp), deferred для outreach-platform v2
- [OpenAI Developer Community: ChatCompletions vs Responses API](https://community.openai.com/t/chatcompletions-vs-responses-api-difference-in-parallel-tool-call-behaviour-observed/1369663) — OpenAI рекомендует переход на Responses API для новых проектов; outreach-platform остаётся на Chat Completions API (no migration в Phase 4)

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH — все зависимости уже в проекте, версии зафиксированы в requirements.txt
- Architecture patterns: HIGH — все паттерны (singleton worker, AuthDep CRUD, ON CONFLICT INSERT, FOR UPDATE SKIP LOCKED) уже работают в Phase 1-3 коде
- Pitfalls: HIGH (1, 4, 5, 6, 8 — из живого кода и существующих паттернов) / MEDIUM (2, 3, 7 — частично теоретические, требуют валидации в тестах)
- Migration 016 sketch: HIGH — pattern из 015_phase3.sql + явная D-04..D-19 спецификация в CONTEXT.md
- Validation architecture: HIGH — pytest infrastructure уже work'ает (32 test files существуют), нужно только добавить Phase 4 тесты по TDD pattern
- OpenAI built-in tools integration: MEDIUM-HIGH — API хорошо документирован, parallel tool calls — стандартная feature, но точная семантика «не возвращать tool result в LLM» (D-12) требует careful implementation

**Open architectural questions:** 5 (см. Open Questions выше) — все нерешённые вопросы небольшие и могут решаться planner'ом Plan 04-02 (NULLable vs NOT NULL для message_queue.campaign_id — самое важное).

**Research date:** 2026-05-22
**Valid until:** 2026-06-22 (30 days — стабильная информация, OpenAI tool calling API стабилен, PostgreSQL ENUM limitation — это hard fact не меняющийся)

---

*Phase: 04-campaigns*
*Research completed: 2026-05-22*
