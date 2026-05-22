# Phase 5: Inbox & Analytics - Context

**Gathered:** 2026-05-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 5 строит **Inbox** (workspace-scoped CRUD диалогов с фильтром и историей сообщений + ручник + UI-отправка от лица менеджера), **AIRC-04** (проактивный фильтр системных ботов в listener'е), **Analytics dashboard** (4 одинаковые карточки на 4 уровнях: workspace / campaign / agent / sender — real-time COUNT'ы) и **LLM request log** (отдельная таблица `llm_calls` со всеми ai_engine-вызовами для inbox-debug).

**В скоупе:**

1. **Миграция `017_phase5.sql`**:
   - Расширение CHECK constraint `conversations.status`: добавляется новое значение `'bot_ignored'`. Финальный список — `active / manual / paused / lead / handoff / finished / bot_ignored`. Pattern из Phase 4 migration 016 (DROP CONSTRAINT + ADD CONSTRAINT, idempotent).
   - Новая таблица `llm_calls` (см. D-09).
   - Composite indexes для real-time analytics (см. C-04) — planner подберёт по реальным запросам.

2. **`app/routers/conversations.py` — полный рерайт под AuthDep + workspace-scope** (legacy файл существует с Phase 1, не зарегистрирован в `main.py`, использует выпиленный `verify_api_key` и `senders.is_active` дропнутую колонку Phase 2 D-11):
   - `GET /api/v1/conversations` — список с фильтрами `?campaign_id=&agent_id=&sender_id=&status=&ai_enabled=&search=&limit=&offset=`. Workspace-scope обязателен. Pagination — LIMIT+OFFSET (как Phase 2 contacts/folders). По умолчанию exclude `status='bot_ignored'` (показывается только при явном фильтре).
   - `GET /api/v1/conversations/{id}` — детали с last_message preview.
   - `GET /api/v1/conversations/{id}/messages` — история сообщений с pagination.
   - `PATCH /api/v1/conversations/{id}` — обновить `ai_enabled` / `status` (валидация: status ∈ enum).
   - `POST /api/v1/conversations/{id}/enable-ai` — fast toggle (см. D-03).
   - `POST /api/v1/conversations/{id}/disable-ai` — fast toggle в режим менеджера (см. D-01, D-02).
   - `POST /api/v1/conversations/{id}/send` — ручная отправка из inbox (см. D-04). Workspace-scope обязателен: проверяем что conversation.workspace_id == ctx.workspace_id ДО telegram-вызова.
   - `DELETE /api/v1/conversations/{id}` — hard delete (legacy уже имеет; переносим под AuthDep).
   - Регистрация в `main.py`: `app.include_router(conversations.router)`.

3. **`app/services/listener.py` — два изменения**:
   - **Фильтр ботов проактивный** (см. D-05, D-06): в `handle_incoming_message` после получения `event.sender` — проверка `event.sender.bot` ИЛИ `getattr(event.sender, 'is_bot', False)`. Если True: INSERT в `messages` (история сохраняется) + создание `conversations` с `status='bot_ignored'`, `ai_enabled=false`, `paused_at=NOW()`, `paused_reason='Telegram bot account (event.sender.bot=True)'`. AI **не вызывается**. Return сразу.
   - **LLM-логирование вокруг `ai_engine.generate_response`** (см. D-09..D-12): wrapping в новой утилите `app/services/llm_logger.py` (или встроенно в ai_engine) — INSERT в `llm_calls` после каждого вызова OpenAI с conversation_id, model, full messages array, response, tool_calls, token counts, latency_ms, error если был.
   - Сохраняется `_handle_antispam_signal` (см. D-08).

4. **`app/services/ai_engine.py` — wrap log around generate_response**:
   - Перед `openai_client.chat.completions.create(...)` — захват timestamp + messages.
   - После — capture response.usage (tokens), choices[0] (response_text/tool_calls), latency_ms (now - timestamp).
   - INSERT в `llm_calls` в новой утилите (D-09). Не блокирует возврат response клиенту: лог пишется в той же async-функции, но в transaction-savepoint (ошибка лога не валит ответ).

5. **`app/routers/analytics.py` — новый роутер** (см. D-13..D-16):
   - `GET /api/v1/analytics/workspace` — карточки workspace-уровня.
   - `GET /api/v1/analytics/campaigns/{id}` — карточки одной кампании.
   - `GET /api/v1/analytics/agents/{id}` — карточки одного агента.
   - `GET /api/v1/analytics/senders/{id}` — карточки одного sender'а.
   - Все 4 endpoint'а возвращают одинаковую схему `AnalyticsCards`: `{sent: int, replied: {conversation_count: int, message_count: int}, leads: int, finishes: int}`.
   - Real-time COUNT'ы (D-13), all-time (D-14). Никаких background-тиков, materialized views, pre-aggregated counters.
   - Регистрация в `main.py`.

**Не в скоупе:**
- TG-бот workspace для админских уведомлений (ADMN-01..03) — Phase 6.
- Per-contact timezone scheduling (ADVN-03), multi-step follow-up (ADVN-01), A/B (ADVN-02) — v2.
- Time-window dropdown в аналитике (всё-time достаточно для v1) — v2.
- Materialized views / pre-aggregated counters — v2 если real-time медленный на реальных объёмах.
- Truncation prompt'ов >8KB в llm_calls — v2 если данные покажут патологические prompt'ы.
- Логирование warmup-LLM-вызовов (WarmupWorker генерит сообщения через GPT) — v2 (audit-cost tracking).
- llm_calls archival/partitioning — v2 когда таблица перерастёт performance threshold.
- Custom date range фильтры на аналитике (`?from=&to=`) — v2.
- SSE/WebSocket real-time inbox updates — v2 (UI поллит).
- `senders.is_active` legacy reference в `app/routers/conversations.py:364` — выпиливается в рерайте Phase 5.

</domain>

<decisions>
## Implementation Decisions

### Manager mode + очередь (INBX-04)

- **D-01:** **Отдельный статус `'manual'`** для ручного перевода — разделён с `'handoff'` (последний ставится только LLM-tool `transfer_to_manager` в Phase 4 D-12). Inbox UI рисует разные бейджи: «Менеджер» (ручной) vs «AI передал на менеджера» (LLM-tool). Логика отключения AI одинаковая в обоих (ai_enabled=false), но семантика разная для аналитики и фильтров. Расширение CHECK constraint `conversations.status` НЕ требуется — `'manual'` уже включён в Phase 4 migration 016 (см. CHECK constraint строка 99).

- **D-02:** **При переводе в `'manual'` — cancel pending message_queue items этого контакта** + явная пометка `error_message='Conversation taken over manually'`. SQL:
  ```sql
  UPDATE message_queue
  SET status='cancelled',
      error_message='Conversation taken over manually',
      updated_at=NOW()
  WHERE workspace_id=:wid
    AND recipient_phone=(SELECT contact_phone FROM conversations WHERE id=:cid)
    AND status='pending';
  ```
  Pattern из `_handle_antispam_signal` (listener.py:823). В v1 при «один контакт → одно первое сообщение» pending обычно пусто или «первое ещё не ушло» — но защита от race-condition «pending уже у воркера в обработке» важна (см. C-02). UI показывает в inbox «N автосообщений отменены менеджером» (Lovable рендерит на основе error_message суммарно).

- **D-03:** **Обратный перевод (manager → AI): только `ai_enabled=true`**. Status НЕ трогаем — если был `'lead'` или `'finished'` (LLM-tool зафиксировал событие), сохраняется. Если был `'manual'` — остаётся `'manual'` пока юзер явно через PATCH не выставит `'active'`. UX-объяснение: «AI снова отвечает в этом диалоге, но факт что был лид/финиш — это историческая правда, не стираем». Дополнительно reset'ятся `paused_at=NULL`, `paused_reason=NULL`.

- **D-04:** **POST /api/v1/conversations/{id}/send — переносим в v1 с auto-takeover**. Когда менеджер пишет сообщение из inbox UI → автоматически:
  ```sql
  UPDATE conversations
  SET ai_enabled=false,
      status='manual',
      paused_at=NOW(),
      paused_reason='Manager sent message via UI',
      updated_at=NOW()
  WHERE id=:cid
  ```
  Т.е. сам акт отправки = «менеджер берёт диалог». Не нужна отдельная кнопка «Отключить AI» сначала. После отправки messages-row пишется с `sent_by='human'` (legacy роутер уже это делает корректно). Через Telethon отправка идёт по `sender.session_string` + `sender.proxy` — паттерн из legacy conversations.py:339, но workspace-scope check **обязателен** + `senders.lifecycle_status='active' AND auth_status='ok'` (вместо дропнутого `is_active`).

### Bot blocklist (AIRC-04)

- **D-05:** **Источник определения «это бот» — `event.sender.bot=True` от Telethon**. Это официальный Telegram-side флаг (все @BotFather-боты помечены). Без hardcoded списков telegram_id, без workspace-настроек. В Telethon `event.sender` может быть `User` (с полем `.bot: bool`) — если `True` → блокируем.

- **D-06:** **Точка применения фильтра — `listener.handle_incoming_message`, INSERT сохраняем, AI не вызываем**:
  - Сразу после получения `event.sender`: проверка `if event.sender and getattr(event.sender, 'bot', False) is True:`.
  - INSERT в `messages` (история сохраняется для inbox).
  - INSERT (или UPDATE если уже есть) в `conversations` со статусом `'bot_ignored'`, `ai_enabled=false`, `paused_reason='Telegram bot account'`.
  - return — не вызываем `ai_engine.generate_response`, не buffer'им сообщения для debounce.
  - Inbox по дефолту прячет `status='bot_ignored'` (см. D-13/inbox-filter).

- **D-07:** **Новый status='bot_ignored' в `conversations.status` CHECK constraint**. Migration 017:
  ```sql
  ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
  ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
      CHECK (status IN ('active','manual','paused','lead','handoff','finished','bot_ignored'));
  ```
  Pattern полностью идентичен Phase 4 migration 016 строки 97-99. Idempotent.

- **D-08:** **`_handle_antispam_signal` (listener.py:823) — оставить как safety net**. Покрывает рядкие service-аккаунты которые не флагнуты как `bot=True` в Telethon (например, через ENV-список `ANTISPAM_BOT_TELEGRAM_IDS` / `ANTISPAM_BOT_NAMES`). Срабатывает реактивно: при сообщении от antispam-бота — UPDATE conversation.ai_enabled=false + cancel queue items + UPDATE sender.lifecycle_status='paused'. Это другая семантика (антиспам = аккаунт ПОЛНОСТЬЮ ставится на паузу), не путать с D-06 (один диалог ignored).

### LLM request log (ANLX-05)

- **D-09:** **Отдельная таблица `llm_calls`**:
  ```sql
  CREATE TABLE IF NOT EXISTS llm_calls (
      id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
      campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL,  -- из conversations.campaign_id
      agent_id UUID REFERENCES ai_contexts(id) ON DELETE SET NULL,   -- из conversations.ai_context_id
      sender_id UUID REFERENCES senders(id) ON DELETE SET NULL,      -- из conversations.sender_id
      model VARCHAR(50) NOT NULL,                -- из app/config.py settings.openai_model
      prompt JSONB NOT NULL,                     -- full messages array (D-10)
      response_text TEXT,                        -- choices[0].message.content (может быть NULL если только tool_calls)
      tool_calls JSONB,                          -- choices[0].message.tool_calls (NULLable)
      prompt_tokens INT,
      completion_tokens INT,
      total_tokens INT,
      latency_ms INT,
      error TEXT,                                -- exception text если openai упал
      created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_llm_calls_workspace ON llm_calls(workspace_id, created_at DESC);
  CREATE INDEX IF NOT EXISTS idx_llm_calls_conversation ON llm_calls(conversation_id, created_at DESC);
  ```
  `conversation_id` NOT NULL — все логируемые вызовы привязаны к диалогу (см. D-12). Foreign keys `campaign_id/agent_id/sender_id` дублируют связи через conversation для быстрых JOIN-free аналитических запросов (но они опциональные SET NULL — denormalisation не критична).

- **D-10:** **`prompt JSONB` — полный messages array, отправленный в OpenAI**. Включает:
  - `messages: [{role, content}, ...]` — system + user (история диалога) + assistant + tool roles.
  - `tools: [...]` — full OpenAI function-tools spec (built-in + custom из campaigns.tools).
  - `model: str`, `temperature: float`.
  - Размер ~5-50KB per row — PG JSONB это легко выдерживает. Никакого truncation в v1 (см. deferred).

- **D-11:** **Retention — без cleanup, всё хранится навсегда**. Никаких nightly cron'ов, никаких N-day retention policies. PG растёт линейно с числом LLM-вызовов. Когда таблица станет проблемой (10M+ rows) — v2 ведёт archival в отдельную таблицу или partitioning по `created_at`. Для v1 (один клиент) объём оценивается в ~30k rows/мес — несущественно.

- **D-12:** **Логируем ТОЛЬКО `ai_engine.generate_response` (listener-driven)**. Warmup-LLM-вызовы (WarmupWorker генерит «светские» сообщения через OpenAI в `warmup.py`) НЕ пишутся в `llm_calls`. Покрытие inbox-debug «почему AI так ответил» — 100%. Audit cost-tracking warmup'а — v2.

### Analytics — counters + time-windows (ANLX-01..04)

- **D-13:** **Real-time COUNT() per запрос — без кеша, без materialized views, без pre-aggregated counters**. Все аналитические endpoint'ы выполняют ~4-5 SELECT'ов с COUNT/COUNT DISTINCT и возвращают результат. Объёмы v1 (1-3 клиента, ~10k messages в первый месяц) делают это <100ms при существующих индексах (workspace_id, campaign_id, sender_id). Никаких background-тиков, никаких CTE-cron'ов, никакого Redis. Если real-time станет медленным — Phase 6/v2 введёт materialized view (deferred).

- **D-14:** **Time-window — all-time (one number)**. Карточки показывают «отправлено: 1240», «ответили: 156 диалогов (4536 сообщений)», «лидов: 23», «финишей: 12» — все с момента создания entity. Никакого dropdown'а «7 дней / 30 дней / custom range» в v1 UI. API endpoint'ы НЕ принимают `?from=&to=` параметры в v1. Простая UI.

- **D-15:** **«Отвечено» = две цифры на одной карточке**:
  - `conversation_count` = `SELECT COUNT(DISTINCT conversation_id) FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.workspace_id=:wid [+ scope filter] AND m.direction='inbound' AND m.sent_by='contact'`.
  - `message_count` = `SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.workspace_id=:wid [+ scope filter] AND m.direction='inbound' AND m.sent_by='contact'`.
  - UI рендерит: «Ответили: 156 диалогов (4536 сообщений)». Дополнительный insight: response rate (conversation_count / sent) + average depth (message_count / conversation_count).

- **D-16:** **Одинаковый набор карточек на всех 4 уровнях**: Отправлено / Отвечено / Лиды / Финиши. Никаких per-level вариаций (errors на sender'е, campaigns на agent'е и т.п.). Все 4 endpoint'а возвращают одну Pydantic-схему `AnalyticsCards` с одинаковыми полями. Sender-специфичные ошибки (FloodWait/Failed/auth) видны на странице sender'а отдельно (Phase 2 SNDR-03), не в analytics-dashboard. Agent-campaign-count видна в `/api/v1/agents` (Phase 3 AGNT-04, computed `campaign_count`), не в analytics. Это упрощает UI и API.
  - **Метрики per-level (формулы)**:
    - **workspace** — фильтр `WHERE workspace_id=:wid`.
    - **campaign** — `WHERE workspace_id=:wid AND campaign_id=:cid` (через message_queue или conversations.campaign_id).
    - **agent** — `WHERE workspace_id=:wid AND ai_context_id=:aid` (через conversations.ai_context_id).
    - **sender** — `WHERE workspace_id=:wid AND sender_id=:sid` (через conversations.sender_id или messages.sender_id через JOIN).
  - **«Отправлено»** = `COUNT(*) FROM messages_log WHERE message_type='sent' AND [scope]` ИЛИ `COUNT(*) FROM message_queue WHERE status='sent'` — Claude's Discretion (C-01) planner выберет источник.
  - **«Лиды/Финиши»** = `COUNT(*) FROM conversations WHERE status='lead' AND [scope]` / `status='finished'`.

### Inbox details (Claude's Discretion area)

- **D-17:** **Inbox по дефолту прячет `status='bot_ignored'`** — UI не загромождается мусором. Фильтр `?status=bot_ignored` должен явно показывать ignored-диалоги (для debug). Default behaviour: `WHERE status != 'bot_ignored'` (или включённое через query param). Аналогично warmup-LATERAL-исключение из legacy conversations.py:95-100 сохраняется (диалоги с warmup-парами не показываются в inbox).

- **D-18:** **Фильтры inbox `?campaign_id=&agent_id=&sender_id=` — strict EQ** (не «также NULL»). Если юзер выбрал «кампания X» — показываем только conversations с `campaign_id=X`. Legacy conversations (без campaign_id, до Phase 4 миграции 016) — фильтр `?campaign_id=` НЕ возвращает их. Без фильтра возвращаются все (включая NULL campaign_id). Phase 4 D-05: `conversations.campaign_id` NULLable для legacy и для входящих от незнакомых.

### Claude's Discretion

- **C-01:** Источник для «Отправлено» — `messages_log` (per-message audit) vs `message_queue` (с фильтром status='sent') vs `messages` (с direction='outbound'). Все три содержат outbound-факт. Planner выберет самый эффективный для индексов; рекомендация — `messages` (workspace_id уже scoped через conversations JOIN, индексы на conversation_id + created_at, и это согласуется с «Отвечено» которое тоже из `messages`).
- **C-02:** Race-condition защита при D-02 cancel-queue (item уже у воркера в обработке): planner может добавить advisory-lock либо CHECK на `processing` status вместо `pending`. Альтернатива: явный signal handler в `_process_next_for_sender` который при следующем tick'е увидит `ai_enabled=false` и просто skip'нет.
- **C-03:** Точная shape Pydantic-схем (`AnalyticsCards`, `ConversationResponse`, `LLMCallResponse`) и naming endpoint'ов — planner подбирает под convention (`schemas/__init__.py` PascalCase + `model_config = ConfigDict(from_attributes=True)`).
- **C-04:** Composite indexes для real-time COUNT'ов на `conversations` (например `(workspace_id, status, campaign_id)`, `(workspace_id, status, ai_context_id)`, `(workspace_id, status, sender_id)`) — planner оценит реальные SELECT'ы и подберёт.
- **C-05:** Структура `llm_logger` — отдельный модуль `app/services/llm_logger.py` (recommended; чистая абстракция) vs inline в `ai_engine.generate_response` (compact). Planner решит после оценки complexity. Wrap должен быть try/except — ошибка лога НЕ должна валить response клиенту.
- **C-06:** Pagination для `GET /conversations/{id}/messages` — LIMIT+OFFSET vs cursor-based (after_id). Legacy роутер использует OFFSET. Для v1 OFFSET достаточен (диалоги короткие). Cursor — v2 если будет нужно.
- **C-07:** Распределение фич по 4 планам (ROADMAP 05-01..05-04): возможно слияние 05-01 (inbox API) и 05-02 (manager mode + bot filter), т.к. оба правят `app/routers/conversations.py` и `app/services/listener.py`. Planner решит после оценки.
- **C-08:** Lovable UI рендерит badge'и для всех 7 значений `conversations.status` — palette/иконки/labels — Claude's Discretion (Lovable-сторона). Backend просто отдаёт `status: string`.
- **C-09:** Search в inbox (`?search=`) — фильтр по `contact_phone` / `contact_name` / fragment в last message. Planner оценит, нужно ли в v1 или deferred.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level
- `CLAUDE.md` — главные правила: raw SQL миграции 017+, async everywhere, общение на русском, **НЕ трогать** rate-limit/debounce/long-pause/flood-threshold интервалы в `queue.py` (эмпирически подобраны), `_handle_antispam_signal` НЕ ломать.
- `.planning/PROJECT.md` — Key Decisions: Campaign первичная сущность (Phase 4 done), agent отвязан от sender (Phase 3 done), `conversations.campaign_id` NULLable (Phase 4 D-05), 6 значений `conversations.status` + новый `bot_ignored` (Phase 5 D-07).
- `.planning/REQUIREMENTS.md` §"Inbox (Phase 5)" + "AI Behavior Rules (Phase 5)" + "Analytics (Phase 5)" — 11 требований (INBX-01..05, AIRC-04, ANLX-01..05).
- `.planning/ROADMAP.md` §"Phase 5: Inbox & Analytics" — Success Criteria (6 пунктов) и состав плана (4 plan'а: 05-01 Inbox API, 05-02 Manager mode + bot filter, 05-03 Analytics, 05-04 LLM log). C-07 предупреждает что 05-01/05-02 могут быть слиты.

### Phase 1 / 2 / 3 / 4 контекст (must read)
- `.planning/phases/01-workspace-foundation/01-CONTEXT.md` — D-04 (workspace isolation `.where(workspace_id == ctx.workspace_id)` + TODO(v2-rls) метки), D-11..D-14 (AuthDep / AuthCtx / dual auth JWT+API-key), D-01 (БД чистая, миграции без backfill).
- `.planning/phases/02-tg-accounts-contacts/02-CONTEXT.md` — D-11 (`senders.is_active` дропнут, заменён `lifecycle_status` + `auth_status` derived `status`), D-18 (periodic reconcile loop в listener'е — не ломаем).
- `.planning/phases/03-agents-ai-templates/03-CONTEXT.md` — D-08 (hard delete + FK SET NULL/CASCADE), D-04 (`senders.ai_context_id` дропнута — связь только через campaigns.agent_id).
- `.planning/phases/04-campaigns/04-CONTEXT.md` — D-04 (lifecycle статусов кампании), **D-05** (`conversations.campaign_id` NULLable FK SET NULL — критично для inbox-фильтра, см. Phase 5 D-18), **D-12** (signals детектируются через LLM tool call, ставят `conversation.status='lead'/'handoff'/'finished'`), D-13 (3 webhook URL уже работают, Phase 5 не трогает), C-13 (status enum расширен `'lead'/'handoff'/'finished'` в Phase 4 — Phase 5 добавляет `'bot_ignored'`).
- `.planning/phases/02.1-worker-hardening` — CR-01..09 BLOCKER findings и их fixes. Phase 5 продолжает паттерны (workspace_id в каждом INSERT в `messages` / `llm_calls`).

### Codebase intel
- `.planning/codebase/ARCHITECTURE.md` — слойная разбивка router→service→data; analytics-роутер живёт под `app/routers/analytics.py`; llm_logger — под `app/services/`.
- `.planning/codebase/STRUCTURE.md` — миграции `migrations/017_*.sql`, новый роутер `app/routers/analytics.py`, рерайт `app/routers/conversations.py`, новый сервис `app/services/llm_logger.py` (или inline в ai_engine).
- `.planning/codebase/CONCERNS.md` — `verify_api_key` legacy (выпилен в Phase 1) — НЕ используем в `conversations.py` рерайте. **DEFAULT_SYSTEM_PROMPT** AGS Foods brand-leak — НЕ трогаем (отдельная фаза). **OpenAI model ID `gpt-5-mini-2025-08-07`** Known Bug — ассумим что пофиксен в config до Phase 5 execute.
- `.planning/codebase/INTEGRATIONS.md` — Telethon abstraction (НЕ трогаем; используем `event.sender.bot` field для D-05); OpenAI вызывается из `ai_engine.generate_response` (Phase 5 wrap для логирования).

### Существующий код (читать перед изменением)
- `app/routers/conversations.py` — **legacy файл, не зарегистрирован в main.py** (как `send.py` до Phase 3). Полный рерайт под AuthDep + workspace-scope + дроп `senders.is_active` reference (строка 364) + 6+1=7 значений status enum.
- `app/routers/auth.py` / `app/utils/auth.py` — `AuthDep` / `auth_dep` / `AuthCtx` — паттерн всех новых routers.
- `app/services/listener.py:543-803` (`handle_incoming_message`) — точка добавления proactive bot filter (D-05, D-06).
- `app/services/listener.py:823-879` (`_handle_antispam_signal`) — **safety net остаётся работать** (D-08).
- `app/services/listener.py:188-237` (debounce buffer + AI dispatch) — здесь же llm_logger wrap'ит `ai_engine.generate_response` call.
- `app/services/ai_engine.py:267-430` (`generate_response`) — точка LLM-логирования (D-09..D-12). Wrap try/except, INSERT в llm_calls после получения OpenAI response.
- `app/services/queue.py:691-712` (INSERT conversations) — НЕ трогаем; Phase 4 D-05 уже добавил campaign_id.
- `app/services/queue.py:_handle_antispam_signal` cancel-queue pattern — переиспользуется для D-02 (manager takeover cancel).
- `app/models/__init__.py` — добавляется `LLMCall` ORM-модель; `Conversation.status` enum не меняется на python-уровне (остаётся `String(20)`), CHECK constraint в migration 017.
- `app/main.py` — `app.include_router(conversations.router)` (восстанавливается), `app.include_router(analytics.router)` (новый). НЕТ новых workers/lifespans — все аналитические вычисления — request-time.
- `migrations/016_phase4.sql` — последняя миграция, следующая `017_phase5.sql`.

### AI Engine / OpenAI (внешний)
- `app/services/ai_engine.py:get_context_for_conversation` (Phase 4 D-12) — переиспользуется как есть; llm_logger wrap'ит результат вызова.
- OpenAI `chat.completions.create` response shape — `usage.{prompt_tokens, completion_tokens, total_tokens}`, `choices[0].message.{content, tool_calls}` — источники для llm_calls полей.

### Lovable UI contract (downstream)
- Lovable рендерит inbox: список диалогов (с фильтром `?campaign_id=&agent_id=&sender_id=&status=&search=`), детали диалога с историей сообщений, переключатель «AI / Менеджер» (POST enable-ai/disable-ai), textarea для ручной отправки (POST /send — auto-takeover, см. D-04), badge'и для 7 значений `status` (D-07, C-08).
- Lovable рендерит analytics dashboard: 4 карточки на 4 уровнях (workspace в шапке, campaign/agent/sender — на их страницах). Никакого dropdown'а времени (D-14). Для «Отвечено» рендерит обе цифры (D-15).
- Lovable рендерит LLM-debug панель в детали диалога: список llm_calls с timestamp + model + prompt-snippet + response-snippet (расширение по клику). Endpoint `GET /api/v1/conversations/{id}/llm-calls?limit=&offset=` (planner подберёт точное имя).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **AuthDep / AuthCtx** (Phase 1 D-11..14, `app/utils/auth.py`) — все 5 новых/рерайт endpoint'ов под `Depends(auth_dep)` + `where(workspace_id == ctx.workspace_id)`.
- **Существующий `app/routers/conversations.py`** — карта что нужно: SELECT structure с LATERAL JOIN'ами для last_message и unread_count, PATCH/enable/disable handlers, POST /send через `telegram_service.send_message_by_telegram_id`. Workspace-scope добавляем, `senders.is_active` заменяем на `lifecycle_status='active' AND auth_status='ok'`.
- **`_handle_antispam_signal` cancel-queue pattern** (`listener.py:874`) — UPDATE message_queue SET status='cancelled' WHERE recipient_phone=? AND status='pending'. Шаблон для D-02 (manager takeover cancel).
- **`telegram_service.send_message_by_telegram_id`** (`telegram.py`) — ручная отправка из UI; reuse для D-04.
- **Phase 2 ContactCheckWorker** — НЕ используется в Phase 5 (нет background workers); упоминается только для понимания паттерна.
- **`ai_engine.generate_response`** (Phase 4 D-12 + Phase 3 рерайт) — точка LLM-логирования. Wrap try/except + INSERT в llm_calls.
- **OpenAI client** в `ai_engine.py` (singleton AsyncOpenAI) — переиспользуется без изменений.
- **`messages` table** — schema из Phase 1+ (id, conversation_id, direction, message_text, sent_by, telegram_message_id, created_at). Phase 5 НЕ меняет.
- **`conversations` schema** (Phase 4 расширенная): `workspace_id, sender_id, contact_phone, contact_name, contact_telegram_id, ai_enabled, ai_context_id NULLable SET NULL, campaign_id NULLable SET NULL, status (7 values после Phase 5), paused_at, paused_reason, created_at, updated_at`.
- **`tests/conftest.py`** (Phase 1+2+3+4) — расширяется `conversation_factory(workspace, sender, campaign?)`, `inbox_state_helpers` (создание диалогов всех 7 статусов для smoke-тестов).
- **legacy SQL: warmup-LATERAL exclude в `conversations.py:94-100`** — сохраняем (исключает diary-conversations создаваемые WarmupWorker'ом из inbox-видимости).

### Established Patterns

- Все таблицы: UUID PK, `workspace_id UUID NOT NULL FK CASCADE`, server_default для timestamp'ов.
- Миграции — raw SQL, идемпотентные (`IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS`), нумерация `017_`.
- Запросы к таблицам: `.where(... .workspace_id == ctx.workspace_id)` + `# TODO(v2-rls): replaced by RLS policy app.workspace_id`.
- Pydantic v2: `model_config = ConfigDict(from_attributes=True)`, partial PATCH с Optional полями.
- API endpoints под `/api/v1/conversations`, `/api/v1/analytics/{level}/[{id}]`. Snake_case плюс kebab в URL allowed (`/api/v1/conversations/{id}/enable-ai`).
- HTTP коды: 200 на read/update, 201 на create, 204 на delete, 404 на отсутствие, 403 на чужой workspace, 422 на pydantic validation.
- Background workers: НЕТ новых в Phase 5 (всё request-time). LLM log пишется inline в `ai_engine.generate_response`.
- Logging: `logger = logging.getLogger(__name__)` — никаких print().

### Integration Points

- **`app/main.py`** — `app.include_router(conversations.router)` (восстанавливается), `app.include_router(analytics.router)` (новый). НЕТ изменений lifespan (workers не добавляются).
- **`app/models/__init__.py`** — добавляется `LLMCall` ORM-модель. `Conversation` (без python-изменений, CHECK расширяется в SQL).
- **`app/schemas/__init__.py`** — добавляются `ConversationResponse`, `ConversationListResponse`, `ConversationUpdate`, `ConversationFilter`, `MessageResponse`, `SendMessageFromUIRequest`, `SendMessageFromUIResponse`, `AnalyticsCards`, `LLMCallResponse`, `LLMCallListResponse`. `SendMessageFromUIResponse` уже есть в legacy — переиспользовать имя.
- **`docker-compose.yml`** — без изменений (новых сервисов не добавляется).
- **`app/config.py`** — без новых env vars в v1 (retention/cleanup отсутствует).
- **`migrations/`** — следующая `017_phase5.sql`. Idempotent. Содержит ALTER CHECK constraint + CREATE TABLE llm_calls + composite indexes на conversations для аналитики (C-04 planner).

### Anti-patterns, которые НЕ повторять

- НЕ использовать `senders.is_active` (дропнута Phase 2 D-11) — фильтр через `lifecycle_status='active' AND auth_status='ok'`.
- НЕ использовать `verify_api_key` (Phase 1 D-14) — только AuthDep.
- НЕ ломать `_handle_antispam_signal` (CLAUDE.md + D-08) — safety net.
- НЕ ломать debounce 3-5 мин в `listener.py` и эмпирические интервалы в `queue.py` (CLAUDE.md).
- НЕ создавать materialized views или pre-aggregated counters в v1 (D-13) — Phase 6/v2.
- НЕ хранить аналитику в Redis или другом каше — БД достаточна.
- НЕ дублировать workspace-isolation: ВСЕ SELECT'ы к conversations/messages/llm_calls/etc. имеют `where(workspace_id == ctx.workspace_id)`.
- НЕ блокировать response клиенту при ошибке llm_log INSERT (try/except + warning log).
- НЕ менять python type `Conversation.status` (`String(20)`) — расширяется только CHECK constraint в SQL (миграция 017).
- НЕ создавать новые таблицы без `workspace_id UUID NOT NULL FK CASCADE`.
- НЕ логировать `prompt` в обычные application logs (он попадает только в llm_calls.prompt JSONB; PROJECT.md context может содержать чувствительные данные клиента).

</code_context>

<specifics>
## Specific Ideas

- **БД чистая (Phase 1 D-01)**: миграция 017 не делает backfill / data migration. `llm_calls` создаётся пустой; CHECK constraint расширяется без UPDATE существующих rows.
- **«Manual» и «handoff» — два разных бейджа в UI** (D-01): semantic distinction важна для аналитики и retrospective ("сколько раз AI сам передал" vs "сколько раз менеджер сам взял"). Backend хранит два разных значения; Lovable рендерит разные цвета/иконки.
- **Auto-takeover при отправке из UI** (D-04): акт отправки сообщения = «менеджер берёт диалог», без отдельной кнопки. Это критично для UX: если требовать сначала disable-ai потом send — менеджер будет забывать и попадёт в race с AI.
- **Cancel pending очереди при manual switch** (D-02): защита от UX-cringe «менеджер только что написал, через 2 секунды AI отправляет автосообщение». Особенно важно когда сообщение в pending уже несколько часов (например ждёт working_window кампании).
- **Reverse switch не сбрасывает status** (D-03): сохраняет факт «был lead», «был finished». UX «AI снова отвечает, но факт лида остаётся».
- **Bot filter через Telethon `event.sender.bot`** (D-05): один универсальный признак, без поддержки списков. Покрывает 99% случаев. Безопасно: false-positives крайне редки (Telegram официально различает bot vs user аккаунты).
- **`status='bot_ignored'` сохраняется в БД** (D-06, D-07): история сообщений от ботов не теряется — клиент видит «SpamBot написал X», но AI на это не реагирует. По умолчанию inbox скрывает (D-17), но debug-режим доступен.
- **`llm_calls` со ВСЕМ messages array** (D-10): для inbox-debug нужна полная воспроизводимость. «Почему AI ответил так на сообщение „привет"? — посмотри prompt, увидишь что в system_prompt был FAQ с этим словом». 5-50KB per row — нормально для PG JSONB.
- **Retention навсегда** (D-11): аудит trail для compliance + debug. PG растёт линейно — в v1 это не проблема. v2 решит archival/partitioning если нужно.
- **Только listener LLM-вызовы** (D-12): warmup-генерация сообщений между «своими» аккаунтами — это infrastructure, не клиентский диалог. Audit cost-tracking warmup'а — отдельный концерн (v2).
- **Real-time COUNT'ы без кеша** (D-13): KISS. На v1 объёмах работает. Когда надо будет — добавим materialized view (одна миграция + один cron).
- **All-time, без time-window** (D-14): UX-простота. Custom range в v2 если клиенты попросят.
- **Одинаковые карточки на всех уровнях** (D-16): UI-consistency. Sender-errors на странице sender'а (Phase 2), agent-campaign-count в `/api/v1/agents` response (Phase 3) — отдельные концерны, не путаем с analytics dashboard.
- **Inbox по дефолту прячет `bot_ignored`** (D-17): чтобы не загромождать UI входящими от SpamBot и других системных. Debug-фильтр `?status=bot_ignored` доступен.
- **Phase 5 НЕ добавляет background workers** — всё request-time. lifespan-список (QueueWorker, WarmupWorker, ContactCheckWorker, CampaignEnqueueWorker) остаётся 4.

</specifics>

<deferred>
## Deferred Ideas

### Для Phase 6 (Admin Master Bot)
- ADMN-02: бот шлёт уведомление в admin-канал при срабатывании `transfer_to_manager` (CAMP-12) или ручного перевода в `'manual'` (Phase 5 D-01). Hook добавляется в conversation status flip.
- ADMN-03: уведомление при `lifecycle_status='paused'` после _handle_antispam_signal (Phase 5 D-08 продолжает работать) или при `auth_status != 'ok'`.

### Для v2
- **Materialized views или pre-aggregated counters** (D-13): когда real-time COUNT станет медленным. Trigger threshold — ~1M+ rows в `messages` per workspace.
- **Time-window filters** (D-14): `?from=&to=` + UI dropdown «7d / 30d / 90d / custom». Сейчас all-time.
- **llm_calls retention / archival / partitioning** (D-11): когда таблица перерастёт 10M+ rows. Partition по `created_at` ежемесячно либо archive в `llm_calls_archive`.
- **Truncation prompt'ов >8KB в llm_calls** (D-10): если данные покажут патологические prompt'ы (например 100KB context dump).
- **Warmup-LLM-вызовы в llm_calls** (D-12): для audit OpenAI расходов на warmup. Сейчас только listener.
- **Cursor-based pagination для messages** (C-06): если диалоги станут длинными.
- **Search в inbox** (C-09): full-text по contact_name / contact_phone / last_message snippet. Сейчас Claude's Discretion — planner оценит, нужно ли в v1.
- **Bot blocklist per-workspace override** (D-05): «всё равно отвечать на этот bot». Сейчас all-bots-ignored.
- **SSE/WebSocket real-time inbox updates** — сейчас UI поллит.
- **Reverse switch с restore previous status**: «вернуть на AI и status в 'active'» через extra-param. Сейчас D-03 только ai_enabled.
- **Inbox export в CSV** (REQUIREMENTS.md ANLX-EXP-01) — v2.
- **Per-status filter в analytics**: «лидов за неделю», «handoff'ов в кампании X» — сейчас всё-time-totals.

### Tech debt из Phase 1 / 2 / 3 / 4, продолжающий висеть
- `app/database.py` `Base.metadata.create_all` (Phase 1 C-04, Phase 2/3/4 carry-over) — всё ещё нерешён. Planner Phase 5 может закрыть либо отложить.
- `senders.role` String(20)+CHECK → SQLEnum (Phase 2/3/4 deferred) — не блокер Phase 5.
- `DEFAULT_SYSTEM_PROMPT` AGS Foods brand-leak в `ai_engine.py` (CONCERNS.md) — НЕ часть Phase 5, отдельная фаза.
- OpenAI model ID `gpt-5-mini-2025-08-07` (CONCERNS.md «Known Bugs») — НЕ часть Phase 5 (но без фикса AI-engine не работает; ассумим что пофиксен).

### Reviewed Todos (not folded)
Phase 5 todo match вернул 0 совпадений — нечего деферить.

</deferred>

---

*Phase: 05-inbox-analytics*
*Context gathered: 2026-05-22*
