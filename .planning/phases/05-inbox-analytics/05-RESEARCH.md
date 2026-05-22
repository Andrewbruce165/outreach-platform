# Phase 5: Inbox & Analytics — Research

**Researched:** 2026-05-22
**Domain:** FastAPI workspace-scoped CRUD (inbox) + real-time aggregate analytics + LLM call audit log + proactive bot filter
**Confidence:** HIGH (вся информация выведена напрямую из существующего кода и зафиксированных CONTEXT.md решений — внешние библиотеки не вводятся)

## Summary

Фаза 5 — это композиция четырёх уже зрелых паттернов в коде: (1) workspace-scoped router под `auth_dep` (как в `app/routers/campaigns.py`, `agents.py`); (2) per-message side-effect логирование через try/except (как `webhook_notify._fire_callback` в `queue.py:890`); (3) `_handle_antispam_signal` UPDATE+cancel pattern (`listener.py:823-889`) — переиспользуется и для D-02 (manager takeover) и для D-06 (bot filter); (4) raw-SQL миграция с `DROP CONSTRAINT IF EXISTS / ADD CONSTRAINT` на `conversations.status` CHECK (как в `migrations/016_phase4.sql:97-99`).

Новых внешних зависимостей нет. Новых background workers нет (D-13). Pydantic v2 + SQLAlchemy 2.0 async — те же что и в Phase 4. Точки расширения известны: `app/routers/conversations.py` (legacy файл, не зарегистрирован в `main.py`) — полный рерайт; `app/services/listener.py:553-803` (`handle_incoming_message`) — точечная inject'ка proactive bot filter сразу после `event.get_sender()`; `app/services/ai_engine.py:660` (`client.chat.completions.create`) — обёртка в try/except для INSERT в `llm_calls`; `app/routers/analytics.py` — новый router с 4 endpoint'ами идентичной shape; migration `017_phase5.sql` — ALTER CHECK + CREATE TABLE + 3-4 composite indexes.

**Primary recommendation:** План сборки — 1 миграция, 2 новых router'а, 1 новый сервисный модуль (`llm_logger.py` — отдельно, см. C-05), 2 точечные правки в `listener.py`, 1 wrap в `ai_engine.py`. Слияние 05-01 + 05-02 в один plan рекомендуется (см. C-07): оба правят `conversations.py` и `listener.py`, физически невозможно изолировать. Итоговый план: **3 плана** (Inbox+manager+bot, Analytics, LLM-log) вместо 4-х из ROADMAP.

## Project Constraints (from CLAUDE.md)

| Constraint | Source | Impact на Phase 5 |
|------------|--------|-------------------|
| Перед изменением — объясни, дождись подтверждения | CLAUDE.md "Главное правило" | Planner создаёт PLAN.md → ждёт approve перед написанием кода |
| Общение на русском, код/коммиты — английский | CLAUDE.md | Pydantic schema наименования English; logger messages могут быть RU; commit messages EN |
| Async everywhere, никаких `time.sleep` / `requests` / `print` | CLAUDE.md "Архитектурные правила" | llm_logger INSERT — `await db.execute`; нет sync HTTP вызовов; `logging.getLogger(__name__)` |
| Миграции — только raw SQL в `migrations/`, идемпотентные, нумерация 017 | CLAUDE.md | `migrations/017_phase5.sql`, `IF NOT EXISTS` / `DROP CONSTRAINT IF EXISTS`, никакого Alembic |
| **НЕ трогать** rate-limit/debounce/long-pause/flood-threshold интервалы в `queue.py` | CLAUDE.md | LLM logger не правит `MIN_SEND_INTERVAL` и т.п.; bot filter в listener'е не трогает debounce 3-5 мин |
| Безопасность: сессии зашифрованы, API_KEY не в логах | CLAUDE.md | `llm_calls.prompt` JSONB — единственное место где prompt сохраняется; в `logger.info()` prompt НЕ попадает |
| Retry/FloodWait логика — не ломать | CLAUDE.md | Bot filter возвращает `return` до AI dispatch — не затрагивает FloodWait recovery |
| Старый продакшн в `/root/apps/telegram-api/` не трогаем | CLAUDE.md | Только outreach-platform repo |

## User Constraints (from CONTEXT.md)

### Locked Decisions

| ID | Decision |
|----|----------|
| **D-01** | Отдельный статус `'manual'` для ручного перевода (≠ `'handoff'` от LLM-tool). Inbox UI рисует разные бейджи. `'manual'` уже в CHECK (Phase 4 mig 016) — расширение НЕ требуется. |
| **D-02** | При переводе в `'manual'` — UPDATE `message_queue SET status='cancelled', error_message='Conversation taken over manually'` WHERE `recipient_phone=(SELECT contact_phone FROM conversations WHERE id=:cid) AND workspace_id=:wid AND status='pending'`. Pattern из `listener.py:862-878`. |
| **D-03** | Обратный перевод (manager→AI): только `ai_enabled=true` + reset `paused_at=NULL, paused_reason=NULL`. Status НЕ трогаем (lead/finished/manual сохраняются исторически). |
| **D-04** | POST /send из inbox — auto-takeover: одновременно UPDATE conversation (`ai_enabled=false, status='manual', paused_at=NOW(), paused_reason='Manager sent message via UI'`) + INSERT в messages с `sent_by='human'` + Telethon вызов через `telegram_service.send_message_by_telegram_id`. Workspace check + `senders.lifecycle_status='active' AND auth_status='ok'` (вместо `is_active`). |
| **D-05** | Источник «это бот» — `event.sender.bot=True` от Telethon. Без hardcoded списков, без workspace-настроек. |
| **D-06** | Bot filter применяется в `listener.handle_incoming_message` сразу после `event.get_sender()`. INSERT в messages (история сохраняется), INSERT/UPDATE conversations со `status='bot_ignored', ai_enabled=false, paused_reason='Telegram bot account'`. AI не вызывается, debounce не буферится — `return`. |
| **D-07** | Новый `status='bot_ignored'` в `conversations.status` CHECK. Migration 017 расширяет CHECK на 7 значений. |
| **D-08** | `_handle_antispam_signal` (listener.py:823) — оставляем как safety net. Семантически отличается: антиспам ставит **весь sender** на паузу + cancel ВСЕХ queue items для sender'а. D-06 ставит **только один диалог** в bot_ignored. |
| **D-09** | Таблица `llm_calls` (id, workspace_id NOT NULL FK CASCADE, conversation_id NOT NULL FK CASCADE, campaign_id NULL FK SET NULL, agent_id NULL FK SET NULL, sender_id NULL FK SET NULL, model VARCHAR(50), prompt JSONB NOT NULL, response_text TEXT, tool_calls JSONB, prompt_tokens INT, completion_tokens INT, total_tokens INT, latency_ms INT, error TEXT, created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL). |
| **D-10** | `prompt JSONB` — полный messages array + tools + model + temperature. Без truncation. |
| **D-11** | Retention — без cleanup. Всё хранится навсегда. |
| **D-12** | Логируем только `ai_engine.generate_response` (listener-driven). Warmup-LLM-вызовы — НЕ логируем. |
| **D-13** | Real-time COUNT() per запрос. Без кеша, без materialized views, без background-тиков, без Redis. |
| **D-14** | Time-window — all-time (one number). API endpoint'ы НЕ принимают `?from=&to=`. |
| **D-15** | «Отвечено» = две цифры: `conversation_count` = COUNT(DISTINCT conversation_id) и `message_count` = COUNT(*) на JOIN messages+conversations с `m.direction='inbound' AND m.sent_by='contact'`. |
| **D-16** | Одинаковый набор карточек на всех 4 уровнях: Отправлено / Отвечено / Лиды / Финиши. Все 4 endpoint'а возвращают одну Pydantic-схему `AnalyticsCards`. Sender-errors на странице sender'а — отдельно (Phase 2 SNDR-03). |
| **D-17** | Inbox по дефолту прячет `status='bot_ignored'`. Фильтр `?status=bot_ignored` — explicit для debug. Warmup-LATERAL exclude из legacy `conversations.py:94-100` сохраняется. |
| **D-18** | Фильтры inbox `?campaign_id=&agent_id=&sender_id=` — strict EQ. Legacy conversations с `campaign_id IS NULL` НЕ попадают в результат при `?campaign_id=X`. |

### Claude's Discretion

| ID | Area |
|----|------|
| **C-01** | Источник для «Отправлено» — `messages_log` vs `message_queue.status='sent'` vs `messages.direction='outbound'`. Planner выберет; рекомендация ниже (Architecture Patterns §Analytics queries). |
| **C-02** | Race-condition защита для D-02 cancel-queue (item уже у воркера в обработке). |
| **C-03** | Точная shape Pydantic-схем + naming endpoint'ов. |
| **C-04** | Composite indexes для real-time COUNT'ов на `conversations`. |
| **C-05** | `llm_logger` — отдельный модуль vs inline в `ai_engine.generate_response`. |
| **C-06** | Pagination для `GET /conversations/{id}/messages` — LIMIT+OFFSET vs cursor. |
| **C-07** | Распределение фич по 4 планам ROADMAP — возможно слияние 05-01 + 05-02. |
| **C-08** | Lovable UI рендерит бейджи (palette/иконки) — Lovable-сторона, backend не касается. |
| **C-09** | Search в inbox (`?search=`) — нужен ли в v1? |

### Deferred Ideas (OUT OF SCOPE)

**Phase 6:**
- ADMN-02: уведомление в admin-канал при `transfer_to_manager` или ручном переводе в `'manual'` — hook добавляется в Phase 6.
- ADMN-03: уведомление при `lifecycle_status='paused'` после antispam.

**v2:**
- Materialized views / pre-aggregated counters (D-13 — v2 если real-time медленный).
- Time-window filters (D-14): `?from=&to=`.
- llm_calls retention / archival / partitioning (D-11).
- Truncation prompt'ов >8KB.
- Warmup-LLM-вызовы в llm_calls.
- Cursor-based pagination для messages.
- Search в inbox (C-09) — Claude оценит, оставляет или деферит.
- Bot blocklist per-workspace override.
- SSE/WebSocket real-time inbox.
- Reverse switch с restore previous status.
- Inbox export в CSV (ANLX-EXP-01).
- Per-status filter в analytics.

**Tech debt (carry-over):**
- `Base.metadata.create_all` (Phase 1 C-04) — всё ещё нерешён, planner может закрыть либо отложить.
- `senders.role` String→SQLEnum — не блокер.
- `DEFAULT_SYSTEM_PROMPT` AGS Foods leak — отдельная фаза.
- OpenAI model ID `gpt-5-mini-2025-08-07` — ассумим что пофиксен.

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INBX-01 | Видны все входящие диалоги workspace | `GET /api/v1/conversations` — workspace-scoped SELECT с warmup-LATERAL-exclude (legacy 94-100), default WHERE `status != 'bot_ignored'` (D-17). |
| INBX-02 | В каждом диалоге история сообщений | `GET /api/v1/conversations/{id}/messages` — pagination LIMIT/OFFSET через JOIN на conversations для workspace-scope (C-06: OFFSET достаточен в v1). |
| INBX-03 | Виден статус AI диалога | `ConversationResponse.status` — 7 значений. Lovable рендерит бейджи (C-08). |
| INBX-04 | Manual переключение в режим менеджера | `POST /api/v1/conversations/{id}/disable-ai` (D-01) + `POST .../send` auto-takeover (D-04) + `POST .../enable-ai` reverse (D-03). D-02 cancel-queue side-effect внутри disable-ai и send. |
| INBX-05 | Фильтр по кампании / агенту / TG-аккаунту | Query params `?campaign_id=&agent_id=&sender_id=` strict EQ (D-18). |
| AIRC-04 | AI не отвечает системным ботам | Bot filter в `listener.handle_incoming_message` (D-05, D-06): `event.sender.bot=True` → INSERT messages + INSERT/UPDATE conversations со `status='bot_ignored'` → return. |
| ANLX-01 | Метрики workspace | `GET /api/v1/analytics/workspace` — 4 COUNT'а скоупом WHERE `workspace_id=:wid`. |
| ANLX-02 | Метрики кампании | `GET /api/v1/analytics/campaigns/{id}` — те же 4 COUNT'а + scope `AND c.campaign_id=:cid`. |
| ANLX-03 | Метрики TG-аккаунта (sender) | `GET /api/v1/analytics/senders/{id}` — те же 4 COUNT'а + scope `AND c.sender_id=:sid`. Sender-errors (SNDR-03) — НЕ часть analytics dashboard (D-16). |
| ANLX-04 | Метрики агента | `GET /api/v1/analytics/agents/{id}` — те же 4 COUNT'а + scope `AND c.ai_context_id=:aid`. Agent campaign_count видна в `/api/v1/agents` (Phase 3), не здесь. |
| ANLX-05 | Лог LLM-запросов на уровне диалога | Таблица `llm_calls` (D-09) + `GET /api/v1/conversations/{id}/llm-calls` (планер подберёт точное имя). INSERT в llm_calls — wrap вокруг `ai_engine.client.chat.completions.create` (D-12). |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.110+ (existing) | HTTP routing, dependency injection | Уже в проекте (см. `app/main.py`). Поддерживает `Depends(auth_dep)` + Pydantic v2 нативно. |
| SQLAlchemy | 2.0+ async (existing) | ORM + raw SQL execution | Уже в проекте. `AsyncSession.execute(text(...))` для raw SQL — паттерн всех Phase 1-4 routers. |
| asyncpg | 0.30+ (existing) | PostgreSQL async driver | Используется через `postgresql+asyncpg://` DSN в DATABASE_URL. |
| Pydantic | v2 (existing) | API I/O validation | `model_config = ConfigDict(from_attributes=True)` — паттерн всех новых schemas. |
| pytest-asyncio | (existing, asyncio_mode='auto') | Тесты async-кода | Уже в pyproject.toml. Все Phase 4 тесты используют. |
| python-jose | (existing) | JWT verify (для auth_dep) | Уже в `app/utils/auth.py`. Phase 5 НЕ касается auth-логики. |

### Supporting (НЕ вводим новых — переиспользуем существующие)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Telethon | 1.42.0 (existing) | `event.sender.bot` (D-05) | Уже в `listener.py`. Поле `.bot: bool` — официальный Telethon API (часть `User` TLObject). |
| OpenAI | >=1.40.0,<2.0.0 (existing) | `response.usage.{prompt_tokens, completion_tokens, total_tokens}` + `response.choices[0].message.{content, tool_calls}` | Уже singleton `client = AsyncOpenAI(...)` в `ai_engine.py:35`. |
| httpx | (existing) | Webhook fire-and-forget (через `webhook_notify`) | Phase 5 webhook'ов не добавляет — переиспользуем существующий `webhook_notify.notify_signal`. |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Inline llm_logger в ai_engine | Отдельный `llm_logger.py` (recommended C-05) | Inline проще для одного call site, но усложняет тестирование (mock OpenAI клиента сложнее когда лог-логика в той же функции). Отдельный модуль — clean abstraction + lru_cache для resolve campaign_id/sender_id. |
| LIMIT+OFFSET pagination | Cursor-based (after_id) | OFFSET работает в v1 (короткие диалоги, <100 messages). Cursor — premature optimization для phase 5. |
| Materialized view для analytics | Real-time COUNT (D-13 locked) | MV — v2 если real-time медленный. В v1 (~10k messages) PG COUNT с правильным индексом — <100ms. |
| Trigger-based llm_calls INSERT | Application-level INSERT | Trigger требует postgres function (не вписывается в "raw SQL миграции" pattern); ошибки сложно ловить. Application-level + try/except — гибче. |

**Installation:**
Никаких новых пакетов. `requirements.txt` НЕ меняется.

**Version verification:** Все библиотеки уже зафиксированы в проекте; Phase 5 не вводит новых depending'ов.

## Architecture Patterns

### Recommended Project Structure (новые / правленые файлы)

```
app/
├── routers/
│   ├── conversations.py        # REWRITE — workspace-scoped, AuthDep, 7 endpoints
│   └── analytics.py            # NEW — 4 endpoints, single AnalyticsCards schema
├── services/
│   ├── llm_logger.py           # NEW (C-05) — log_llm_call() helper, try/except wrap
│   ├── listener.py             # MODIFY — bot filter inject (line ~590, after event.get_sender())
│   └── ai_engine.py            # MODIFY — wrap chat.completions.create with log_llm_call
├── models/
│   └── __init__.py             # MODIFY — add LLMCall ORM model
├── schemas/
│   └── __init__.py             # MODIFY — add Phase 5 Pydantic models
└── main.py                     # MODIFY — include_router conversations + analytics

migrations/
└── 017_phase5.sql              # NEW — ALTER CHECK + CREATE TABLE llm_calls + indexes

tests/
├── test_inbox_router.py        # NEW
├── test_inbox_workspace_isolation.py  # NEW
├── test_inbox_manager_mode.py  # NEW (disable-ai + send + cancel-queue)
├── test_inbox_send_takeover.py # NEW (D-04 atomic)
├── test_bot_filter.py          # NEW (D-05, D-06)
├── test_analytics_router.py    # NEW (4 levels + workspace_id)
├── test_analytics_correctness.py  # NEW (seeded fixtures → expected counts)
├── test_llm_logger.py          # NEW (error path, payload shape)
├── test_llm_logger_no_block_on_error.py  # NEW (INSERT fail → generate_response still returns)
├── test_migration_017.py       # NEW (idempotency, double-apply)
└── conftest.py                 # MODIFY — add conversation_factory + inbox_state_helpers
```

### Pattern 1: Workspace-scoped router under `auth_dep`

**What:** Каждый endpoint объявляет `ctx: AuthCtx = Depends(auth_dep)` и каждый SELECT/UPDATE/DELETE добавляет `.where(*.workspace_id == ctx.workspace_id)` или `WHERE workspace_id = :wid` в raw SQL. С TODO(v2-rls) меткой.

**When to use:** Всегда (Phase 1 D-04).

**Example (extracted from `app/routers/campaigns.py`):**
```python
# Source: app/routers/campaigns.py:67-82
async def _load_campaign(db: AsyncSession, ctx: AuthCtx, campaign_id: UUID) -> Campaign:
    res = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy app.workspace_id
        )
    )
    c = res.scalars().first()
    if c is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CAMPAIGN_NOT_FOUND", "message": "Campaign not found"},
        )
    return c
```

Применение в Phase 5: `_load_conversation`, `_load_campaign_for_analytics`, `_load_agent_for_analytics`, `_load_sender_for_analytics`. Все возвращают 404 на missing OR cross-workspace (одинаковый response, чтобы не сливать information disclosure).

### Pattern 2: Cancel-queue side-effect (D-02)

**What:** При flip `ai_enabled=false`/`status='manual'` — UPDATE message_queue для отмены pending items по `recipient_phone+workspace_id+status='pending'`. Pattern из `_handle_antispam_signal:862-878`.

**Example (extracted from `app/services/listener.py:823-887`):**
```python
# Source: app/services/listener.py:862-878 — antispam signal cancel-queue
result2 = await session.execute(
    text("""
        UPDATE message_queue
        SET status = 'failed',
            error_message = :reason,
            finished_at = NOW()
        WHERE sender_id = :sender_id
          AND status IN ('pending', 'processing')
        RETURNING id
    """),
    {"sender_id": sender_id, "reason": f"Auto-cancelled: antispam signal received from {bot_name}"}
)
```

**Adaptation для D-02** (`status='cancelled'` per CONTEXT.md, не `'failed'` — это семантически разное):
```sql
UPDATE message_queue
SET status = 'cancelled',
    error_message = 'Conversation taken over manually',
    finished_at = NOW()
WHERE workspace_id = :wid
  AND recipient_phone = (SELECT contact_phone FROM conversations WHERE id = :cid)
  AND status = 'pending'
RETURNING id
```

**Note:** `'cancelled'` НЕ входит в текущий `QueueItemStatus` SQLEnum (см. `app/models/__init__.py`). Planner должен либо использовать `'failed'` (consistent с антиспамом) либо расширить SQLEnum + миграция. **Рекомендация:** использовать `'failed'` с error_message='Conversation taken over manually' — антипаттерн ввести новое enum значение без миграции; антиспам тоже использует 'failed'. CONTEXT.md D-02 явно говорит `status='cancelled'` — это **разночтение, требующее planner-уровневого решения**. Open Question #1.

### Pattern 3: Atomic auto-takeover (D-04)

**What:** Один transaction с двумя UPDATE + INSERT message + Telethon вызов. Order:

```
1. SELECT conversation (workspace check) + sender (lifecycle_status='active' AND auth_status='ok') — JOIN-ом одним запросом
2. IF NOT row → 404
3. IF contact_telegram_id IS NULL → 400
4. UPDATE conversations SET ai_enabled=false, status='manual', paused_at=NOW(), paused_reason='Manager sent message via UI', updated_at=NOW() WHERE id=:cid AND workspace_id=:wid
5. UPDATE message_queue (cancel-queue per D-02 pattern)
6. await telegram_service.send_message_by_telegram_id(...)  # OUTSIDE transaction — внешний side-effect
7. IF success: INSERT INTO messages (sent_by='human', telegram_message_id, ...)
8. IF Telethon fail: return 500 — но ai_enabled уже false (acceptable: менеджер всё равно в режиме)
9. commit()
```

**Critical:** Telethon вызов — outside DB transaction. Если bd-commit упадёт после Telethon-успеха, message уже улетел в Telegram но не сохранён локально. Это согласуется с legacy паттерном (`conversations.py:384-431` — также outside transaction). Acceptable trade-off.

### Pattern 4: Proactive bot filter (D-06)

**What:** В `handle_incoming_message` сразу после `event.get_sender()` и БЕФОР существующих фильтров (Telegram service phones / antispam keywords / warmup phones).

**Insertion point:** `app/services/listener.py:561-573` — сразу после `if not sender: return` (line 563), ПЕРЕД блока ANTISPAM_BOT_IDS (line 591). Точная вставка:

```python
# Source: app/services/listener.py:561-580 (insertion point)
sender = await event.get_sender()
if not sender:
    return

phone = getattr(sender, 'phone', None) or "unknown"
name = (...)

me = await event.client.get_me()
if sender.id == me.id:
    return

# --- INSERT NEW BLOCK HERE (D-06): proactive bot filter ---
if getattr(sender, 'bot', False) is True:
    await self._handle_bot_message(sender_info, sender, event, name, phone)
    return  # AI dispatch SKIPPED
# --- END NEW BLOCK ---

# Existing TELEGRAM_SERVICE_PHONES check (line 576) continues
```

Новый метод `_handle_bot_message(sender_info, sender, event, name, phone)`:
1. `async with AsyncSessionLocal() as session:`
2. Resolve conversation: `SELECT id, status FROM conversations WHERE sender_id=:sid AND contact_telegram_id=:tid` (с workspace_id derived из sender_info).
3. IF NOT EXISTS — INSERT (workspace_id=sender_info['workspace_id'], sender_id, contact_phone, contact_name, contact_telegram_id, ai_enabled=false, status='bot_ignored', paused_at=NOW(), paused_reason='Telegram bot account (event.sender.bot=True)').
4. IF EXISTS AND status != 'bot_ignored' — UPDATE на 'bot_ignored' (edge case: контакт переключился на бота? крайне редко, защищаемся).
5. INSERT INTO messages (conversation_id, direction='inbound', message_text=event.text or '<media>', sent_by='contact', telegram_message_id=event.id) ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING.
6. commit.

**Coexistence с _handle_antispam_signal:** `_handle_antispam_signal` остаётся неизменным (D-08, CLAUDE.md). Антиспам сработает в реальной ситуации когда бот НЕ помечен `.bot=True` Telegram'ом (rare — SpamBot is officially marked). Но: SpamBot IS marked `.bot=True`, поэтому D-06 теперь сработает РАНЬШЕ antispam-block для большинства antispam-кейсов. Это **сознательное изменение поведения**: при сообщении от SpamBot теперь НЕ будет: (1) AI отключения для ВСЕХ диалогов sender'а, (2) cancel ВСЕХ pending items, (3) sender lifecycle → 'paused'. **Вместо этого** только один диалог станет `bot_ignored`. Если SpamBot пишет — это сигнал что Telegram реально заметил аккаунт, и нам нужна safety net антиспама.

**Recommendation для planner:** В bot filter (D-06) проверяем `sender.id in ANTISPAM_BOT_IDS` ПЕРВЫМ (`SpamBot` etc.) — если match, делегируем в `_handle_antispam_signal` (safety net) и НЕ создаём `bot_ignored`. Иначе обычный bot — `bot_ignored`. Это сохраняет защиту аккаунта для известных антиспам-ботов. Open Question #2.

### Pattern 5: LLM logger wrap (D-09..D-12, C-05)

**What:** Отдельный модуль `app/services/llm_logger.py` с `log_llm_call()` корутиной. Принимает: conversation_id, model, prompt (messages+tools+model+temperature dict), response (OpenAI response object), latency_ms, error (Optional[str]). Внутри:
1. Resolve workspace_id / campaign_id / agent_id / sender_id из `conversations` одним SELECT.
2. Extract response.usage.{prompt_tokens, completion_tokens, total_tokens}.
3. Extract response.choices[0].message.{content, tool_calls}.
4. INSERT INTO llm_calls (...).
5. `try/except SQLAlchemyError: logger.warning("llm_calls INSERT failed: %s", e); pass` — ошибка не блокирует возврат AI-ответа клиенту.

**Wrap site в `ai_engine.py:660`:**

```python
# Source pattern at app/services/ai_engine.py:648-660
request_params = {
    "model": "gpt-5-mini-2025-08-07",
    "messages": messages,
    "max_completion_tokens": 2000,
}
if all_tools:
    request_params["tools"] = all_tools
    request_params["tool_choice"] = "auto"

# === NEW: timestamp + log wrap ===
import time as _time
_start_ts = _time.perf_counter()
_log_error: Optional[str] = None
try:
    response = await client.chat.completions.create(**request_params)
except Exception as e:
    _log_error = str(e)[:500]
    response = None
    raise  # re-raise — внешний except APIError/etc. handles it
finally:
    _latency_ms = int((_time.perf_counter() - _start_ts) * 1000)
    # Fire-and-forget log (не валит ответ при ошибке INSERT)
    asyncio.create_task(log_llm_call(
        conversation_id=conversation_id,
        model=request_params["model"],
        prompt=request_params,  # full dict — messages + tools + temperature
        response=response,
        latency_ms=_latency_ms,
        error=_log_error,
    ))
```

**Subtlety:** `asyncio.create_task` для INSERT даёт fire-and-forget. Альтернатива — inline `await log_llm_call(...)` со своим try/except. Inline проще для тестирования (детерминированный порядок). `create_task` лучше для latency — клиент не ждёт INSERT.

**Recommendation:** inline `await` с обёрткой в try/except — детерминированный, тестируется через assert SELECT после AI response. Накладные расходы 1-3ms на INSERT — приемлемо. **Если** performance в реальности станет проблемой — переключим на `create_task` (одна строчка). Open Question #3.

**Second LLM call** (`ai_engine.py:780-784` — custom tool result summarization): тоже логируем отдельной row. Это даёт 2 row'a в llm_calls per turn когда есть custom tools. Acceptable для debug (видно «AI сначала позвал tool, потом подытожил»).

### Pattern 6: Analytics queries — выбор источника «Отправлено» (C-01)

**3 кандидата для outbound-count:**

| Кандидат | Pros | Cons | Recommendation |
|----------|------|------|----------------|
| `messages_log WHERE message_type='sent'` | Audit log per send; явный workspace_id колонкой. | Записывается только queue worker'ом (queue.py:570). UI-send (`POST /conversations/{id}/send`) пишет в `messages` НЕ в `messages_log` — пропадает manager-message. Warmup тоже не пишет. Inconsistent. | **Не рекомендуется** |
| `message_queue WHERE status='sent'` | Workspace_id колонкой; consistent с очередью. | Manager-send (D-04) НЕ идёт через очередь — пропускается. | **Не рекомендуется** |
| **`messages JOIN conversations ON conversations.id=messages.conversation_id WHERE messages.direction='outbound' AND conversations.workspace_id=:wid`** | Содержит ВСЕ исходящие: queue worker (`queue.py:877`), listener (`listener.py:482`), manager-send (D-04 future). Consistent с «Отвечено» (тот же `messages` table, противоположный direction). | Workspace_id только через JOIN. Нужен индекс `messages(conversation_id, direction)`. | **Рекомендуется** |

**Подтверждение:** `app/services/queue.py:877` INSERT'ит `messages (direction='outbound', sent_by='human', telegram_message_id)`. `app/services/listener.py:482` — INSERT inbound. `app/routers/conversations.py:411` (legacy) — UI-send тоже в `messages`. Все три источника outbound сходятся в одной таблице.

**Композитный indexes для analytics queries (C-04):**

| Index | Query coverage | Size impact |
|-------|----------------|-------------|
| `conversations(workspace_id, status)` partial WHERE `status IN ('lead','finished')` | leads/finishes count per workspace | Малый — только 2 значения |
| `conversations(workspace_id, campaign_id, status)` | campaign-level leads/finishes | Средний — все статусы |
| `conversations(workspace_id, ai_context_id, status)` | agent-level leads/finishes | Средний |
| `conversations(workspace_id, sender_id, status)` | sender-level leads/finishes | Средний |
| `messages(conversation_id, direction)` — possibly existing partial | inbound/outbound count via JOIN | Большой (но `idx_messages_conversation_created` уже есть из mig 001) |

**Достаточный набор:** добавить только три первых — `(workspace_id, status)` уже частично покрывает workspace-level через bitmap-scan; `(workspace_id, X, status)` где X = `campaign_id`/`ai_context_id`/`sender_id` для 3-х level'ов. Альтернатива: один общий `(workspace_id, status, campaign_id, ai_context_id, sender_id)` — нерационально (низкая селективность по 5-му полю). **Рекомендация planner'у:** три раздельных composite + проверка EXPLAIN на тестовых данных. Если EXPLAIN покажет, что PG предпочитает Seq Scan на маленьких таблицах — оставить только `(workspace_id, status)` и положиться на in-memory быстроту PG.

### Pattern 7: Two-figure «Отвечено» (D-15) — одним SELECT

**Query:**
```sql
SELECT
    COUNT(DISTINCT m.conversation_id) AS conversation_count,
    COUNT(*)                          AS message_count
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE c.workspace_id = :wid
  -- AND c.campaign_id = :cid       -- per-campaign scope
  -- AND c.ai_context_id = :aid     -- per-agent scope
  -- AND c.sender_id = :sid         -- per-sender scope
  AND m.direction = 'inbound'
  AND m.sent_by = 'contact'
```

Один проход по индексу — выгодно. Альтернатива (CTE/subquery) дороже без причины.

### Pattern 8: Migration 017 shape (raw SQL, idempotent)

**Source pattern from `migrations/016_phase4.sql:97-99`:**
```sql
ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active','manual','paused','lead','handoff','finished'));
```

**Phase 5 migration 017 shape:**
```sql
BEGIN;

-- 1. Extend status CHECK to include 'bot_ignored'
ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active','manual','paused','lead','handoff','finished','bot_ignored'));

-- 2. llm_calls table (D-09)
CREATE TABLE IF NOT EXISTS llm_calls (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    conversation_id   UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    campaign_id       UUID REFERENCES campaigns(id) ON DELETE SET NULL,
    agent_id          UUID REFERENCES ai_contexts(id) ON DELETE SET NULL,
    sender_id         UUID REFERENCES senders(id) ON DELETE SET NULL,
    model             VARCHAR(50) NOT NULL,
    prompt            JSONB NOT NULL,
    response_text     TEXT,
    tool_calls        JSONB,
    prompt_tokens     INT,
    completion_tokens INT,
    total_tokens      INT,
    latency_ms        INT,
    error             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_workspace_created
    ON llm_calls(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_calls_conversation_created
    ON llm_calls(conversation_id, created_at DESC);

-- 3. Composite indexes for analytics (C-04)
CREATE INDEX IF NOT EXISTS idx_conversations_workspace_campaign_status
    ON conversations(workspace_id, campaign_id, status)
    WHERE campaign_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversations_workspace_agent_status
    ON conversations(workspace_id, ai_context_id, status)
    WHERE ai_context_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversations_workspace_sender_status
    ON conversations(workspace_id, sender_id, status);

COMMIT;
```

**Idempotency:** `IF NOT EXISTS` для CREATE TABLE и CREATE INDEX. `DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT` — повторное применение не падает (constraint снова дропается и пересоздаётся). Pattern абсолютно идентичен 016.

### Anti-Patterns to Avoid

- **`senders.is_active`:** дропнута в Phase 2 D-11. Используем `lifecycle_status='active' AND auth_status='ok'` (legacy `conversations.py:364` использует — выпиливается в рерайте).
- **`verify_api_key`:** дропнут Phase 1 D-14. Только `auth_dep`.
- **Materialized views / pre-aggregated counters / Redis для аналитики:** D-13 forbid. v2 only.
- **Background workers для analytics tick:** D-13 forbid. Всё request-time.
- **Trogating `_handle_antispam_signal`:** D-08 + CLAUDE.md. Safety net остаётся.
- **Trogating debounce 3-5 мин и rate-limit интервалы в queue.py:** CLAUDE.md.
- **Logging `prompt` в обычные application logs:** только в `llm_calls.prompt` JSONB (содержит чувствительные данные клиента).
- **`Base.metadata.create_all` для llm_calls:** не регрессируем Phase 1 C-04. Таблица создаётся ТОЛЬКО миграцией 017 — модель LLMCall регистрируется в ORM только для SELECT-операций. (То же что Campaign в Phase 4.)
- **Python type `Conversation.status` менять:** остаётся `String(20)`. Расширяется только CHECK constraint в SQL.
- **`'cancelled'` как новое QueueItemStatus:** ввести новое enum значение без миграции — антипаттерн. Использовать `'failed'` (см. Pattern 2 above). **Open Question #1.**
- **Truncation prompt'ов в v1 (D-10):** не делаем; всё в JSONB как есть. v2 если будут патологические prompt'ы.
- **`POST /conversations/{id}/enable-ai` не сбрасывает `status`:** D-03 явно — только `ai_enabled=true` + `paused_at=NULL, paused_reason=NULL`. Status (lead/finished/manual) остаётся. Legacy `conversations.py:294` ставит `status='active'` — это **поведение надо сменить** в рерайте.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JWT validation | Custom decode | `app/utils/auth.py` `auth_dep` | Уже есть HS256 verify + workspace resolve + lazy create + cache (Phase 02.1 CR-09). |
| Workspace API key bcrypt | Custom verify | `auth_dep` | Bcrypt + LRU cache + constant-time compare уже есть. |
| OpenAI client | New AsyncOpenAI() | Singleton `client` в `ai_engine.py:35` | Уже инициализирован с env API_KEY. |
| Telethon send_message | Direct Telethon call | `telegram_service.send_message_by_telegram_id` | Уже обернуто (device fingerprint, proxy, error handling). |
| Webhook fire-and-forget | Custom httpx call | `webhook_notify.notify_signal` (если будет нужен) | Phase 5 webhook'ов не добавляет, но если planner решит логировать manager-takeover события — есть готовый helper. |
| Pagination | Custom logic | Standard `?limit=&offset=` Query params | Все Phase 1-4 routers используют этот pattern. |
| Async DB session | New AsyncSession() | `Depends(get_db)` | Уже dependency-injected с rollback semantics. |
| FAQ JSONB parsing | Custom | `FaqItem` Pydantic + `agent.faq` JSONB | Schema уже есть; для llm_calls.prompt тоже JSONB — Pydantic не нужен (raw dict). |
| Composite index migrations | Manual SQL для каждого | Один SQL block с `IF NOT EXISTS` | Pattern из `migrations/016_phase4.sql` уже подтвердил работоспособность. |
| Conversation upsert | Custom | Существующий `listener.py:get_or_create_conversation` | Phase 5 bot-filter может вызывать его (или INSERT inline — оба варианта; **рекомендация: inline INSERT**, т.к. семантика отличается — мы хотим status='bot_ignored', а `get_or_create_conversation` ставит status='active'). |
| Cancel-queue logic | Custom | Pattern из `_handle_antispam_signal:862-878` | Готовый UPDATE message_queue SET status=... WHERE ... |

**Key insight:** Phase 5 — это **композиция существующих кирпичей**. Новой бизнес-логики мало; новых SQL-структур одна (llm_calls); новых внешних интеграций ноль. Главная работа — workspace-scope + правильная склейка существующих patterns.

## Runtime State Inventory

Phase 5 — не migration / rename / refactor. Только добавление нового кода + одна новая таблица + одна правка CHECK constraint. Этот шаг применим частично:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | **conversations.status** содержит 6 значений по Phase 4. После миграции 017 CHECK расширится на 7 (добавится 'bot_ignored'). Существующие данные не трогаются — backfill не нужен (Phase 1 D-01: БД чистая). | Только миграция CHECK, без UPDATE-операций. |
| Live service config | None — Phase 5 не меняет live service config (no n8n changes, no env vars). | None. |
| OS-registered state | None — Phase 5 не вводит новых OS-services, не меняет docker-compose. | None. |
| Secrets/env vars | None — Phase 5 не добавляет новых env vars (D-11: retention отсутствует — нет cleanup cron config). | None. |
| Build artifacts | None — Phase 5 не меняет pyproject.toml / requirements.txt. | None. |

**Nothing found in any category** — verified by: (1) CONTEXT.md Specific Ideas §1 "БД чистая (Phase 1 D-01): миграция 017 не делает backfill"; (2) Integration Points §`docker-compose.yml` "без изменений"; (3) Integration Points §`app/config.py` "без новых env vars".

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PostgreSQL 16 | All DB ops (llm_calls, queries) | ✓ (assumed running per existing project) | 16 | — |
| Python 3.11+ | All code | ✓ | 3.11+ | — |
| FastAPI / SQLAlchemy 2 async / asyncpg / Pydantic v2 | Routers, models | ✓ | per requirements.txt | — |
| OpenAI client (>=1.40.0,<2.0.0) | ai_engine wrap | ✓ (existing singleton) | per requirements.txt | — |
| Telethon 1.42.0 | event.sender.bot field | ✓ (existing) | 1.42.0 | `getattr(sender, 'bot', False)` defensive — если поле отсутствует, считаем False (acceptable; non-bot users никак не пометятся). |
| pytest / pytest-asyncio | Тесты | ✓ (existing) | per pyproject.toml | — |
| Supabase JWT (auth_dep) | Все endpoint'ы под `auth_dep` | ✓ (existing infra) | HS256 | — |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** None.

## Common Pitfalls

### Pitfall 1: `senders.is_active` reference в legacy `POST /send` (line 364)

**What goes wrong:** Текущий legacy `conversations.py:364` использует `WHERE s.is_active = true` — поле дропнуто в Phase 2 D-11.

**Why it happens:** Файл не зарегистрирован в `main.py` с момента Phase 1 и не обновлялся (так же как legacy `send.py` до Phase 3).

**How to avoid:** В рерайте полностью заменить на `WHERE s.lifecycle_status = 'active' AND s.auth_status = 'ok'`. Это явный поинт в CONTEXT.md (D-04, Anti-patterns).

**Warning signs:** ANY SQL string containing `s.is_active` or `senders.is_active` в новом коде = ошибка.

### Pitfall 2: D-03 reverse switch ставит status='active' (legacy bug)

**What goes wrong:** Legacy `conversations.py:294` устанавливает `status='active'` при `enable-ai`. По D-03 — НЕ трогаем status; если был 'lead' / 'finished' — сохраняется.

**Why it happens:** В Phase 1 ещё не было `'lead'` / `'finished'` статусов (они появились в Phase 4 D-12). Поведение legacy было корректным для своего времени.

**How to avoid:** В рерайте `POST /enable-ai` SQL должен быть:
```sql
UPDATE conversations
SET ai_enabled = true, paused_at = NULL, paused_reason = NULL, updated_at = NOW()
WHERE id = :id AND workspace_id = :wid
```
БЕЗ `status = '...'`.

**Warning signs:** `enable-ai` SQL с `SET status=...` = bug.

### Pitfall 3: Bot filter UPDATE существующего диалога ломает retroactive статус

**What goes wrong:** Если диалог был `status='lead'` (например LLM пометил лида), а потом пришёл response от бот-аккаунта (теоретически возможно при переключении контакта на бота) — UPDATE на `'bot_ignored'` уничтожит важный статус.

**Why it happens:** D-06 текст «INSERT (или UPDATE если уже есть)» предполагает безусловный UPDATE.

**How to avoid:** UPDATE только если текущий `status='active'`. Иначе оставляем как есть (логируем + INSERT message). Бизнес-логика: если уже был лид/handoff/finished/manual — это пометка пришла РАНЬШЕ bot-сообщения, не отменяем её.

```sql
UPDATE conversations
SET status = 'bot_ignored',
    ai_enabled = false,
    paused_at = NOW(),
    paused_reason = 'Telegram bot account (event.sender.bot=True)',
    updated_at = NOW()
WHERE id = :id
  AND status = 'active'  -- guard: не затираем lead/handoff/finished/manual
```

**Warning signs:** Test seeded with `conversation.status='lead'` → bot message arrives → status changes to 'bot_ignored' = bug.

### Pitfall 4: llm_calls FK CASCADE при hard delete agent / sender / campaign

**What goes wrong:** При `DELETE` agent / sender / campaign — FK constraints `ON DELETE SET NULL` (per D-09 — denormalised cols) корректно nullify. Но если FK на conversation_id `ON DELETE CASCADE` — все llm_calls для этой conversation удаляются. Это согласовано с CONTEXT.md D-09 (`conversation_id NOT NULL ... ON DELETE CASCADE`) — phase осознанно отдаёт audit data при удалении диалога.

**Why it happens:** Conversation hard delete уже есть в legacy роутере (line 441-474) и переносится в Phase 5. Если юзер сделает DELETE conversation → llm_calls тоже удалятся.

**How to avoid:** Документировать в `DELETE /conversations/{id}` response. Возможно warning «N llm_calls будут удалены». Альтернатива (опция для planner): сменить FK на `SET NULL` — но тогда llm_calls становятся orphan'ными. Compliance / audit-trail perspective: SET NULL предпочтительнее. **Open Question #4.**

**Warning signs:** Тест `DELETE /conversations/{id}` → проверить что llm_calls для другого conversation не задеты + проверить ожидаемое поведение (cascade vs SET NULL).

### Pitfall 5: llm_calls.workspace_id derive race condition

**What goes wrong:** В `llm_logger.log_llm_call` resolve workspace_id из conversation SELECT'ом. Если AI-генерация заняла >5 секунд И в это время conversation был удалён через `DELETE /conversations/{id}` — SELECT вернёт NULL, INSERT упадёт.

**Why it happens:** llm_logger асинхронный относительно delete; нет блокировки.

**How to avoid:** В try/except — warning + skip (consistent с другими ошибками лога). Не валим возврат AI-ответа. Альтернатива — пробрасывать workspace_id в `generate_response` (он уже знает через `conversation_context` или campaign_context). **Рекомендация:** передавать workspace_id через параметр функции `log_llm_call(workspace_id, conversation_id, ...)` — избегаем второго SELECT'а.

**Warning signs:** llm_calls INSERT падают в production logs (warning level) после DELETE conversation.

### Pitfall 6: D-04 race с queue worker (C-02)

**What goes wrong:** Менеджер делает `POST /send` (auto-takeover). Параллельно queue worker `_process_next_for_sender` уже вытащил pending item с `recipient_phone=X` и сейчас отправляет. UPDATE conversations + cancel-queue не успевают остановить — клиент получает И ручное сообщение И автосообщение.

**Why it happens:** `_process_next_for_sender` (queue.py) сначала SELECT'ит item со `status='pending'` (FOR UPDATE SKIP LOCKED added in Phase 02.1), UPDATE'ит на `processing`, потом делает Telegram send (несколько секунд). cancel-UPDATE D-02 ищет `WHERE status='pending'` — items в `processing` уже не находит.

**How to avoid:** Два варианта:

**A. Tighter race-safe pattern (расширить D-02 на 'processing' тоже):**
```sql
UPDATE message_queue
SET status='failed', error_message='Conversation taken over manually', finished_at=NOW()
WHERE workspace_id=:wid AND recipient_phone=:phone AND status IN ('pending', 'processing')
```
Проблема: worker не знает что row was cancelled, продолжает Telegram send, потом UPDATE'ит на `sent` — финальный статус = `sent` (worker последний пишет). Эффект: контакт получает оба сообщения, но в БД menager-take-over пометка теряется.

**B. Pre-send re-check в queue worker:**
В `_process_next_for_sender` после `UPDATE status='processing'` и ПЕРЕД Telethon send'ом — re-SELECT conversation по `recipient_phone`: если `ai_enabled=false AND status IN ('manual','handoff','finished','bot_ignored')` — SKIP send, UPDATE queue item на `cancelled` со reason.

**Recommendation:** **B** — добавить pre-send guard в queue worker. Это правит queue worker, что нарушает CLAUDE.md "не трогать эмпирические интервалы" — но добавление SKIP-логики НЕ ТРОГАЕТ интервалы, только добавляет one SELECT. Acceptable. Open Question #5.

**Warning signs:** Test: seeded conversation with pending queue item → POST /send → verify queue item НЕ ушёл в Telegram (assert mock telethon NOT called for that recipient_phone).

### Pitfall 7: Telethon `event.sender.bot` может быть `None` или отсутствовать

**What goes wrong:** `event.sender` иногда `None` (защищено существующим check'ом line 561), а в редких случаях `User` объект без поля `bot` (channel sender — не User, или анонимный sender в группе — но мы фильтруем группы выше).

**Why it happens:** Telethon TLObject `User` имеет `bot: bool` всегда; но `event.sender` для каналов/чатов это `Channel` / `Chat` объект — без поля `bot`. Защищаем `getattr(sender, 'bot', False)`.

**How to avoid:** Использовать `getattr(sender, 'bot', False) is True` (явное сравнение с True — None / "" не пройдут как truthy). И этот block идёт ПОСЛЕ `event.is_group or event.is_channel: return` (line 582) — поэтому до bot-check'а доходит только private chat с `User` sender'ом, где `.bot` гарантированно есть.

**Warning signs:** Pytest на `event.sender = MagicMock(spec=[])` (no `bot` attr) → bot filter не должен взорваться.

### Pitfall 8: Bot filter creates conversation БЕЗ campaign_id

**What goes wrong:** Бот написал sender'у — конверсация создаётся (D-06). Conversations.campaign_id = NULL (legitimate per D-05 / D-18 «strict EQ»). Аналитика per-campaign не учитывает их (это правильно). Но analytics workspace-level через `WHERE workspace_id=:wid` включит их в «Отвечено» counts.

**Why it happens:** Bot reply IS an inbound message — формально это replied (тех. `direction='inbound', sent_by='contact'`). Но семантически бот ≠ контакт.

**How to avoid:** Аналитический query «Отвечено» исключает `status='bot_ignored'`:
```sql
WHERE c.workspace_id = :wid
  AND c.status != 'bot_ignored'  -- exclude bot dialogs from "replied"
  AND m.direction = 'inbound'
  AND m.sent_by = 'contact'
```

**Warning signs:** Test: seed 1 bot conversation with 5 inbound messages + 1 real conversation with 3 inbound — assert analytics replied.conversation_count=1, message_count=3 (not 6).

### Pitfall 9: Analytics для `lead` / `finished` — terminal только для finished

**What goes wrong:** D-12 (Phase 4) пометил: `mark_as_lead` ставит `status='lead'` но AI продолжает работать. `finish_conversation` ставит `'finished'` и `ai_enabled=false`. То есть conversation может перейти `lead → finished` → анализ по `status='lead'` пропустит её.

**Why it happens:** Lead — это маркер прохождения этапа, не финальный статус.

**How to avoid:** Carry-over подсчёт: лиды считать как `COUNT(*) FROM conversations WHERE status IN ('lead','finished')` ИЛИ — лучше — ввести колонку `conversations.is_lead BOOLEAN` помечаемую LLM tool'ом. **Не вводим колонку в Phase 5** (нарушает phase boundary). **Рекомендация:** `leads = COUNT(*) WHERE status IN ('lead','finished')` если CONTEXT.md так подразумевает. CONTEXT.md D-16 explicit: `«Лиды/Финиши» = COUNT(*) FROM conversations WHERE status='lead' ... / status='finished'` — то есть mutually exclusive. По текущей семантике lead → finished теряет lead-метку. **Open Question #6.**

### Pitfall 10: Pagination total count expensive

**What goes wrong:** `GET /conversations` делает COUNT(*) для total + SELECT с LIMIT/OFFSET. На больших workspace (10k+ диалогов) COUNT через ту же фильтрацию (warmup-LATERAL exclude + status filter + 3 EQ filters) — медленно.

**Why it happens:** Legacy шаблон `SELECT COUNT(*) FROM (query) sub` — wraps уже-составленный query в subquery. Это создаёт второй пробег по индексам.

**How to avoid:**
- Если list query маленький (with LIMIT) — COUNT остаётся. В v1 acceptable (~100ms per request).
- v2: pagination cursor без COUNT (только `has_more: bool` через `LIMIT N+1`).
- В Phase 5 — оставляем COUNT, но проверяем EXPLAIN на seed-данных.

**Warning signs:** EXPLAIN ANALYZE на GET /conversations показывает Seq Scan по `conversations` table.

## Code Examples

### Example 1: Workspace-scoped GET /conversations с warmup exclude

Source pattern: `app/routers/conversations.py:60-145` (legacy structure) + `app/routers/campaigns.py:67-82` (workspace scope).

```python
# /app/routers/conversations.py (rewrite)
@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    campaign_id: Optional[UUID] = Query(None),
    agent_id: Optional[UUID] = Query(None),
    sender_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None),
    ai_enabled: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),  # C-09 — planner оценит
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """INBX-01 + INBX-05: list conversations со scope и фильтрами."""
    where_clauses = ["c.workspace_id = :wid"]
    params = {"wid": str(ctx.workspace_id), "limit": limit, "offset": offset}

    # D-17: hide bot_ignored unless explicit filter
    if status is None:
        where_clauses.append("c.status != 'bot_ignored'")
    else:
        where_clauses.append("c.status = :status")
        params["status"] = status

    if campaign_id:
        where_clauses.append("c.campaign_id = :campaign_id")
        params["campaign_id"] = str(campaign_id)
    if agent_id:
        where_clauses.append("c.ai_context_id = :agent_id")
        params["agent_id"] = str(agent_id)
    if sender_id:
        where_clauses.append("c.sender_id = :sender_id")
        params["sender_id"] = str(sender_id)
    if ai_enabled is not None:
        where_clauses.append("c.ai_enabled = :ai_enabled")
        params["ai_enabled"] = ai_enabled
    if search:
        where_clauses.append("(c.contact_phone ILIKE :search OR c.contact_name ILIKE :search)")
        params["search"] = f"%{search}%"

    where_sql = " AND ".join(where_clauses)

    # Sources:
    #   - LATERAL JOINs for last_message + unread_count: app/routers/conversations.py:82-93 (legacy)
    #   - warmup-LATERAL exclude pattern: app/routers/conversations.py:95-100 (legacy, preserved)
    list_query = text(f"""
        SELECT
            c.id, s.slug AS sender_slug, c.contact_phone, c.contact_name,
            c.contact_telegram_id, c.ai_enabled, c.ai_context_id, c.campaign_id,
            c.status, c.paused_at, c.paused_reason, c.created_at, c.updated_at,
            last_msg.message_text AS last_message,
            last_msg.created_at   AS last_message_at,
            COALESCE(unread_sq.unread_count, 0) AS unread_count
        FROM conversations c
        JOIN senders s ON c.sender_id = s.id
        LEFT JOIN LATERAL (
            SELECT message_text, created_at FROM messages
            WHERE conversation_id = c.id
            ORDER BY created_at DESC LIMIT 1
        ) last_msg ON true
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS unread_count FROM messages
            WHERE conversation_id = c.id AND direction = 'inbound' AND sent_by = 'contact'
        ) unread_sq ON true
        WHERE {where_sql}
          -- warmup pair exclude (legacy preserved)
          AND NOT EXISTS (
              SELECT 1 FROM senders s2
              WHERE s2.workspace_id = :wid
                AND s2.telegram_id = c.contact_telegram_id
                AND s2.telegram_id IS NOT NULL
          )
        ORDER BY c.updated_at DESC
        LIMIT :limit OFFSET :offset
    """)
    # ... (count query similar) ...
```

**Note:** Legacy warmup-exclude (line 95-100) JOIN'ил `warmup_pool` — но реальный фильтр там через `s2.telegram_id` на `senders` (NOT `warmup_pool`). Sanity-check показывает: legacy SQL фильтрует диалоги где `contact_telegram_id` равен telegram_id ЛЮБОГО sender'а в workspace (т.е. собственные senders). Это диалоги между нашими senders в warmup. Сохраняем pattern, добавляем `s2.workspace_id = :wid` (workspace boundary).

### Example 2: D-04 POST /send с auto-takeover

```python
# /app/routers/conversations.py (rewrite)
@router.post("/{conversation_id}/send", response_model=SendMessageFromUIResponse)
async def send_message_from_ui(
    conversation_id: UUID,
    payload: SendMessageFromUIRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """D-04: auto-takeover send."""
    # 1. SELECT conversation + sender + workspace check + sender activeness
    row = (await db.execute(text("""
        SELECT
            c.contact_telegram_id, c.contact_name,
            s.id AS sender_id, s.slug, s.session_string, s.proxy
        FROM conversations c
        JOIN senders s ON c.sender_id = s.id
        WHERE c.id = :cid
          AND c.workspace_id = :wid
          AND s.lifecycle_status = 'active'
          AND s.auth_status = 'ok'
        -- TODO(v2-rls): replaced by RLS policy
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).fetchone()

    if not row:
        raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND",
                                          "message": "Conversation not found or sender inactive"})
    if not row.contact_telegram_id:
        raise HTTPException(400, detail={"code": "NO_TELEGRAM_ID",
                                          "message": "Contact has no Telegram ID"})

    # 2. UPDATE conversation atomically (auto-takeover)
    await db.execute(text("""
        UPDATE conversations
        SET ai_enabled = false,
            status = 'manual',
            paused_at = NOW(),
            paused_reason = 'Manager sent message via UI',
            updated_at = NOW()
        WHERE id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})

    # 3. Cancel pending queue items (D-02)
    await db.execute(text("""
        UPDATE message_queue
        SET status = 'failed',
            error_message = 'Conversation taken over manually',
            finished_at = NOW()
        WHERE workspace_id = :wid
          AND recipient_phone = (SELECT contact_phone FROM conversations WHERE id = :cid)
          AND status = 'pending'
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})

    await db.commit()

    # 4. Telethon send (OUTSIDE transaction)
    result = await telegram_service.send_message_by_telegram_id(
        sender_slug=row.slug,
        encrypted_session=row.session_string,
        telegram_id=row.contact_telegram_id,
        message=payload.message,
        proxy=row.proxy,
    )

    if not result["success"]:
        return SendMessageFromUIResponse(success=False, error=result.get("error", "Telegram send failed"))

    # 5. INSERT message row (post-send success)
    message_id = uuid.uuid4()
    telegram_message_id = result.get("telegram_message_id")
    await db.execute(text("""
        INSERT INTO messages (id, conversation_id, direction, message_text, sent_by, telegram_message_id)
        VALUES (:id, :cid, 'outbound', :txt, 'human', :tg_mid)
        ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING
    """), {"id": str(message_id), "cid": str(conversation_id),
           "txt": payload.message, "tg_mid": telegram_message_id})
    await db.commit()

    return SendMessageFromUIResponse(success=True, message_id=message_id,
                                      telegram_message_id=telegram_message_id)
```

### Example 3: Analytics workspace endpoint

```python
# /app/routers/analytics.py
@router.get("/workspace", response_model=AnalyticsCards)
async def workspace_analytics(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """ANLX-01: workspace-level metrics."""
    return await _compute_cards(db, ctx.workspace_id, scope=None)


@router.get("/campaigns/{campaign_id}", response_model=AnalyticsCards)
async def campaign_analytics(
    campaign_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """ANLX-02: per-campaign metrics."""
    # Verify campaign in workspace (404 on cross-workspace)
    await _ensure_campaign(db, ctx, campaign_id)
    return await _compute_cards(db, ctx.workspace_id, scope=("campaign_id", campaign_id))


async def _compute_cards(
    db: AsyncSession,
    workspace_id: UUID,
    scope: Optional[tuple[str, UUID]],
) -> AnalyticsCards:
    """Run 4 COUNTs for one scope. scope=None means workspace-only."""
    scope_clause = ""
    params: dict = {"wid": str(workspace_id)}
    if scope:
        col, val = scope
        scope_clause = f" AND c.{col} = :scope_val"
        params["scope_val"] = str(val)

    # Sent (C-01 recommendation: source = messages, direction='outbound')
    sent_row = (await db.execute(text(f"""
        SELECT COUNT(*)
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.workspace_id = :wid
          AND c.status != 'bot_ignored'
          {scope_clause}
          AND m.direction = 'outbound'
    """), params)).scalar() or 0

    # Replied (D-15: two figures in one query)
    replied_row = (await db.execute(text(f"""
        SELECT
            COUNT(DISTINCT m.conversation_id) AS conv_count,
            COUNT(*)                          AS msg_count
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.workspace_id = :wid
          AND c.status != 'bot_ignored'
          {scope_clause}
          AND m.direction = 'inbound'
          AND m.sent_by = 'contact'
    """), params)).fetchone()

    # Leads + Finishes (D-16)
    leads_row = (await db.execute(text(f"""
        SELECT COUNT(*) FROM conversations c
        WHERE c.workspace_id = :wid
          AND c.status = 'lead'
          {scope_clause}
    """), params)).scalar() or 0

    finishes_row = (await db.execute(text(f"""
        SELECT COUNT(*) FROM conversations c
        WHERE c.workspace_id = :wid
          AND c.status = 'finished'
          {scope_clause}
    """), params)).scalar() or 0

    return AnalyticsCards(
        sent=sent_row,
        replied=AnalyticsReplied(
            conversation_count=replied_row.conv_count or 0,
            message_count=replied_row.msg_count or 0,
        ),
        leads=leads_row,
        finishes=finishes_row,
    )
```

### Example 4: llm_logger.log_llm_call (recommended C-05: separate module)

```python
# /app/services/llm_logger.py
"""LLM call audit logger.

Wraps openai_client.chat.completions.create result into an llm_calls INSERT.
Phase 5 D-09..D-12.

Critical: This MUST NOT raise — failure to log should not bubble up to the
caller (ai_engine.generate_response). All exceptions caught + logged at warning.
"""

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


async def log_llm_call(
    db: AsyncSession,
    *,
    workspace_id: Optional[UUID],   # if None, resolved from conversation
    conversation_id: UUID | str,
    model: str,
    prompt: dict,                    # full request_params (messages + tools + temperature + model)
    response: Any,                   # OpenAI response object OR None on error
    latency_ms: int,
    error: Optional[str] = None,
) -> None:
    """Insert one llm_calls row. Never raises."""
    try:
        # Resolve denormalised cols + workspace_id if not provided.
        ws_id = workspace_id
        campaign_id = None
        agent_id = None
        sender_id = None

        row = (await db.execute(text("""
            SELECT workspace_id, campaign_id, ai_context_id, sender_id
            FROM conversations WHERE id = :cid
        """), {"cid": str(conversation_id)})).fetchone()

        if row:
            if ws_id is None:
                ws_id = row.workspace_id
            campaign_id = row.campaign_id
            agent_id = row.ai_context_id
            sender_id = row.sender_id

        if ws_id is None:
            logger.warning("llm_calls: workspace_id unresolved for conv=%s — skipping", conversation_id)
            return

        # Extract from OpenAI response (None on error)
        response_text = None
        tool_calls_json = None
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None
        if response is not None:
            try:
                msg = response.choices[0].message
                response_text = msg.content
                if msg.tool_calls:
                    tool_calls_json = [
                        {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                        for tc in msg.tool_calls
                    ]
                if response.usage:
                    prompt_tokens = response.usage.prompt_tokens
                    completion_tokens = response.usage.completion_tokens
                    total_tokens = response.usage.total_tokens
            except (AttributeError, IndexError) as e:
                logger.warning("llm_calls: extract from response failed: %s", e)

        await db.execute(text("""
            INSERT INTO llm_calls
                (workspace_id, conversation_id, campaign_id, agent_id, sender_id,
                 model, prompt, response_text, tool_calls,
                 prompt_tokens, completion_tokens, total_tokens, latency_ms, error)
            VALUES
                (:wid, :cid, :camp, :agent, :sender,
                 :model, :prompt::jsonb, :response_text, :tool_calls::jsonb,
                 :pt, :ct, :tt, :latency, :error)
        """), {
            "wid": str(ws_id),
            "cid": str(conversation_id),
            "camp": str(campaign_id) if campaign_id else None,
            "agent": str(agent_id) if agent_id else None,
            "sender": str(sender_id) if sender_id else None,
            "model": model,
            "prompt": _safe_jsonify(prompt),
            "response_text": response_text,
            "tool_calls": _safe_jsonify(tool_calls_json) if tool_calls_json else None,
            "pt": prompt_tokens,
            "ct": completion_tokens,
            "tt": total_tokens,
            "latency": latency_ms,
            "error": error,
        })
        await db.commit()
    except SQLAlchemyError as e:
        logger.warning("llm_calls INSERT failed for conv=%s: %s", conversation_id, e)
    except Exception as e:
        logger.warning("llm_calls log unexpected error for conv=%s: %s", conversation_id, e)


def _safe_jsonify(obj) -> str:
    """Serialize dict/list to JSON string for PG JSONB binding."""
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)
```

**Subtlety re: DB session.** `ai_engine.generate_response` уже принимает `session: AsyncSession`. Передавать ту же session — но `commit()` в `log_llm_call` может конфликтовать с другими операциями. Альтернатива: новая `async with AsyncSessionLocal() as db_log:` внутри log_llm_call (изолированный коммит). **Recommendation:** новая session — изолирует ошибки + не блокирует основной flow.

### Example 5: Bot filter inject в listener

```python
# /app/services/listener.py — added method after _handle_antispam_signal

async def _handle_bot_message(
    self,
    sender_info: dict,
    sender,                          # Telethon User object
    event,
    name: str,
    phone: str,
) -> None:
    """Phase 5 D-06: store inbound bot message + flag conversation as bot_ignored.

    AI dispatch SKIPPED. Future messages from this contact remain bot_ignored
    unless manager manually flips status.
    """
    try:
        async with AsyncSessionLocal() as session:
            # Find or insert conversation
            existing = (await session.execute(text("""
                SELECT id, status FROM conversations
                WHERE sender_id = :sid AND contact_telegram_id = :tid
            """), {"sid": str(sender_info["id"]), "tid": sender.id})).fetchone()

            if existing is None:
                conv_id = uuid.uuid4()
                await session.execute(text("""
                    INSERT INTO conversations (
                        id, workspace_id, sender_id, contact_phone, contact_name,
                        contact_telegram_id, ai_enabled, status, paused_at, paused_reason
                    )
                    VALUES (:id, :wid, :sid, :phone, :name, :tid, false, 'bot_ignored',
                            NOW(), 'Telegram bot account (event.sender.bot=True)')
                """), {
                    "id": str(conv_id),
                    "wid": str(sender_info["workspace_id"]),
                    "sid": str(sender_info["id"]),
                    "phone": phone,
                    "name": name,
                    "tid": sender.id,
                })
            else:
                conv_id = existing.id
                if existing.status == 'active':
                    # Pitfall 3: only downgrade from 'active', preserve lead/handoff/finished/manual.
                    await session.execute(text("""
                        UPDATE conversations
                        SET status='bot_ignored', ai_enabled=false,
                            paused_at=NOW(),
                            paused_reason='Telegram bot account (event.sender.bot=True)',
                            updated_at=NOW()
                        WHERE id = :cid
                    """), {"cid": str(conv_id)})

            # Save message (preserve history per D-06)
            await session.execute(text("""
                INSERT INTO messages (conversation_id, direction, message_text,
                                       sent_by, telegram_message_id)
                VALUES (:cid, 'inbound', :txt, 'contact', :tmid)
                ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING
            """), {
                "cid": str(conv_id),
                "txt": event.text or "<media>",
                "tmid": event.id,
            })
            await session.commit()

            logger.info("🤖 Bot message ignored: %s (%s) → conv=%s", name, phone, str(conv_id)[:8])

    except Exception as e:
        logger.error("Bot filter failed: %s", e, exc_info=True)
```

Inserted in `handle_incoming_message` between line 573 (self-message skip) and line 576 (TELEGRAM_SERVICE_PHONES skip):

```python
# ... (existing self-skip on line 569-573) ...

# === Phase 5 D-06: proactive bot filter ===
if getattr(sender, 'bot', False) is True:
    # Optional: if sender.id in ANTISPAM_BOT_IDS — fall through to antispam (Open Question #2)
    if sender.id in {178220800, 777000}:  # known antispam bots — keep safety net
        await self._handle_antispam_signal(sender_info, name, sender.id, event.text or "")
        return
    await self._handle_bot_message(sender_info, sender, event, name, phone)
    return  # AI dispatch SKIPPED
# === End D-06 ===

# Existing TELEGRAM_SERVICE_PHONES check (line 576) continues unchanged
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `senders.is_active` — Boolean | `lifecycle_status='active' AND auth_status='ok'` | Phase 2 D-11 | `conversations.py:364` legacy reference выпиливается. |
| `verify_api_key` — single global key | `auth_dep` (JWT + workspace API key) | Phase 1 D-11..14 | Все 5+ новых endpoint'ов под `Depends(auth_dep)`. |
| Per-context webhook (`ai_contexts.webhook_functions`) | Per-campaign webhook (`campaigns.tools` + `*_webhook_url`) | Phase 3 D-01 + Phase 4 D-14 | Phase 5 wrap'ит `ai_engine.generate_response` — структура остаётся неизменной, только добавляется log. |
| Conversation.status 3 значений (`active|manual|paused`) | 7 значений (+ `lead|handoff|finished|bot_ignored`) | Phase 4 D-12 + Phase 5 D-07 | UI рендерит все 7 (C-08). |
| Background workers для всего | Request-time для analytics | Phase 5 D-13 | Никаких новых workers; lifespan list остаётся {QueueWorker, WarmupWorker, ContactCheckWorker, CampaignEnqueueWorker}. |
| Legacy `conversations.py` not in main.py | Registered под `auth_dep` | Phase 5 (this phase) | `main.py:99` добавит `app.include_router(conversations.router)`. |

**Deprecated/outdated:**
- `senders.is_active` field — drop Phase 2 D-11. **Phase 5 final cleanup** of last legacy reference (line 364).
- `verify_api_key` dependency — drop Phase 1 D-14. **Phase 5** drops last usage in legacy `conversations.py` рерайтом.
- Legacy `ConversationResponse.ai_context_id` field (legacy line 23) — переименовываем в semantic `agent_id` (consistent с Phase 3/4 naming) OR оставляем как `ai_context_id` для backward-compat с Lovable UI. **C-08** — Lovable side, planner проверит.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 7.x + pytest-asyncio (asyncio_mode='auto') — existing |
| Config file | `/Users/andrewbruce/Documents/outreach-platform/pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `pytest tests/test_inbox_router.py -x -q` (single test file) |
| Full suite command | `pytest -x` (existing markers: `integration`) |
| Phase 5 wave 0 gates | New conftest fixtures: `conversation_factory`, `messages_factory`, `llm_calls_factory`, `inbox_state_helpers` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INBX-01 | list conversations workspace-scoped | integration | `pytest tests/test_inbox_router.py::test_list_workspace_isolation -x` | ❌ Wave 0 |
| INBX-01 | warmup-LATERAL exclude | integration | `pytest tests/test_inbox_router.py::test_list_excludes_warmup_pairs -x` | ❌ Wave 0 |
| INBX-01 | default hides bot_ignored (D-17) | integration | `pytest tests/test_inbox_router.py::test_list_default_excludes_bot_ignored -x` | ❌ Wave 0 |
| INBX-01 | explicit `?status=bot_ignored` shows them (D-17) | integration | `pytest tests/test_inbox_router.py::test_list_explicit_bot_ignored_filter -x` | ❌ Wave 0 |
| INBX-02 | get messages with workspace scope | integration | `pytest tests/test_inbox_router.py::test_get_messages_workspace_scoped -x` | ❌ Wave 0 |
| INBX-02 | pagination (limit/offset) correctness | integration | `pytest tests/test_inbox_router.py::test_get_messages_pagination -x` | ❌ Wave 0 |
| INBX-03 | status field shows correctly per all 7 values | unit | `pytest tests/test_inbox_router.py::test_status_field_all_7_values -x` | ❌ Wave 0 |
| INBX-04 | disable-ai flips ai_enabled + status='manual' (D-01) | integration | `pytest tests/test_inbox_manager_mode.py::test_disable_ai_flip -x` | ❌ Wave 0 |
| INBX-04 | disable-ai cancels pending queue (D-02) | integration | `pytest tests/test_inbox_manager_mode.py::test_disable_ai_cancels_queue -x` | ❌ Wave 0 |
| INBX-04 | enable-ai does NOT touch status (D-03) | integration | `pytest tests/test_inbox_manager_mode.py::test_enable_ai_preserves_status -x` | ❌ Wave 0 |
| INBX-04 | POST /send auto-takeover (D-04) | integration | `pytest tests/test_inbox_send_takeover.py::test_send_flips_to_manual -x` | ❌ Wave 0 |
| INBX-04 | POST /send race with queue worker (Pitfall 6) | integration | `pytest tests/test_inbox_send_takeover.py::test_send_race_with_queue -x` | ❌ Wave 0 |
| INBX-05 | filter by campaign_id strict EQ (D-18) | integration | `pytest tests/test_inbox_router.py::test_filter_campaign_id_strict -x` | ❌ Wave 0 |
| INBX-05 | filter by agent_id / sender_id | integration | `pytest tests/test_inbox_router.py::test_filter_agent_sender -x` | ❌ Wave 0 |
| AIRC-04 | bot filter creates bot_ignored row | integration | `pytest tests/test_bot_filter.py::test_bot_creates_bot_ignored -x` | ❌ Wave 0 |
| AIRC-04 | bot filter saves message history (D-06) | integration | `pytest tests/test_bot_filter.py::test_bot_message_saved -x` | ❌ Wave 0 |
| AIRC-04 | bot filter skips AI dispatch | unit | `pytest tests/test_bot_filter.py::test_bot_no_ai_call -x` (mock ai_engine) | ❌ Wave 0 |
| AIRC-04 | bot filter preserves lead/handoff/finished/manual status (Pitfall 3) | integration | `pytest tests/test_bot_filter.py::test_bot_preserves_terminal_status -x` | ❌ Wave 0 |
| AIRC-04 | known antispam bots delegate to safety net (Pitfall 2) | integration | `pytest tests/test_bot_filter.py::test_antispam_id_falls_through -x` | ❌ Wave 0 |
| AIRC-04 | _handle_antispam_signal NOT broken (D-08) | integration | `pytest tests/test_listener.py::test_antispam_signal_still_works -x` | ❌ Wave 0 (extends existing test_listener.py) |
| ANLX-01 | workspace cards 4 metrics correct | integration | `pytest tests/test_analytics_correctness.py::test_workspace_seed_4_metrics -x` | ❌ Wave 0 |
| ANLX-01 | workspace isolation | integration | `pytest tests/test_analytics_router.py::test_workspace_isolation -x` | ❌ Wave 0 |
| ANLX-02 | campaign-level metrics | integration | `pytest tests/test_analytics_correctness.py::test_campaign_scope -x` | ❌ Wave 0 |
| ANLX-03 | sender-level metrics | integration | `pytest tests/test_analytics_correctness.py::test_sender_scope -x` | ❌ Wave 0 |
| ANLX-04 | agent-level metrics | integration | `pytest tests/test_analytics_correctness.py::test_agent_scope -x` | ❌ Wave 0 |
| ANLX (all) | replied returns two figures (D-15) | unit | `pytest tests/test_analytics_correctness.py::test_replied_two_figures -x` | ❌ Wave 0 |
| ANLX (all) | bot_ignored conversations excluded from replied (Pitfall 8) | integration | `pytest tests/test_analytics_correctness.py::test_bot_ignored_excluded -x` | ❌ Wave 0 |
| ANLX (all) | lead+finished mutually exclusive count (Pitfall 9) | integration | `pytest tests/test_analytics_correctness.py::test_lead_finished_counts -x` | ❌ Wave 0 |
| ANLX-05 | llm_calls row created post-generate_response | integration | `pytest tests/test_llm_logger.py::test_log_inserted -x` | ❌ Wave 0 |
| ANLX-05 | llm_calls.prompt contains full messages + tools | unit | `pytest tests/test_llm_logger.py::test_prompt_jsonb_shape -x` | ❌ Wave 0 |
| ANLX-05 | INSERT error does NOT block response (Pitfall 5) | integration | `pytest tests/test_llm_logger_no_block_on_error.py::test_db_fail_does_not_break_ai -x` (mock SQLAlchemyError) | ❌ Wave 0 |
| ANLX-05 | warmup-LLM calls NOT logged (D-12) | integration | `pytest tests/test_llm_logger.py::test_warmup_not_logged -x` | ❌ Wave 0 |
| Migration 017 | applies idempotently | integration | `pytest tests/test_migration_017.py::test_idempotent_double_apply -x` | ❌ Wave 0 |
| Migration 017 | CHECK constraint accepts 'bot_ignored' | integration | `pytest tests/test_migration_017.py::test_check_bot_ignored -x` | ❌ Wave 0 |
| Migration 017 | FK CASCADE on workspace delete | integration | `pytest tests/test_migration_017.py::test_llm_calls_cascade_workspace -x` | ❌ Wave 0 |
| Migration 017 | FK SET NULL on agent/sender/campaign delete | integration | `pytest tests/test_migration_017.py::test_llm_calls_set_null -x` | ❌ Wave 0 |
| Auth | All new endpoints under auth_dep return 401 без credentials | integration | `pytest tests/test_inbox_router.py::test_auth_required -x` + analytics + llm-calls | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/test_inbox_router.py tests/test_inbox_manager_mode.py tests/test_inbox_send_takeover.py -x -q` (или соответствующий subset для plan'а)
- **Per wave merge:** `pytest tests/test_inbox_*.py tests/test_analytics_*.py tests/test_bot_filter.py tests/test_llm_logger*.py tests/test_migration_017.py -x`
- **Phase gate:** Full suite green `pytest -x` (47+ phase 5 tests + ~150 inherited tests) перед `/gsd:verify-work`.

### Wave 0 Gaps

- [ ] `tests/test_inbox_router.py` — INBX-01, INBX-02, INBX-03, INBX-05 endpoints + workspace isolation + filter combinations
- [ ] `tests/test_inbox_workspace_isolation.py` — фокус на cross-workspace 404 (нельзя видеть чужие диалоги)
- [ ] `tests/test_inbox_manager_mode.py` — INBX-04 disable/enable-ai (D-01/D-02/D-03)
- [ ] `tests/test_inbox_send_takeover.py` — INBX-04 D-04 auto-takeover + race-condition test
- [ ] `tests/test_bot_filter.py` — AIRC-04 D-05/D-06 + Pitfall 2/3 coverage
- [ ] `tests/test_analytics_router.py` — workspace isolation на всех 4 endpoint'ах
- [ ] `tests/test_analytics_correctness.py` — seeded fixtures → expected counts (covering all 5 ANLX requirements)
- [ ] `tests/test_llm_logger.py` — payload shape + warmup exclusion (D-12)
- [ ] `tests/test_llm_logger_no_block_on_error.py` — Pitfall 5 — INSERT fail НЕ блокирует ответ
- [ ] `tests/test_migration_017.py` — idempotency, CHECK constraint, FK behaviour
- [ ] `tests/conftest.py` extensions:
  - `conversation_factory(workspace, sender, campaign=None, status='active')` — builds Conversation row
  - `messages_factory(conversation, count, direction='inbound', sent_by='contact')` — builds messages
  - `llm_calls_factory(conversation, model='gpt-4o-mini')` — builds llm_calls row
  - `inbox_state_helpers.seed_all_7_statuses(workspace)` — one conv per status (active/manual/paused/lead/handoff/finished/bot_ignored) for smoke
  - `mock_telegram_service` — for D-04 send tests
  - `mock_openai_response` — for llm_logger tests with controlled `response.usage`/`response.choices`

**Framework install:** None — pytest-asyncio + httpx ASGITransport уже в `tests/conftest.py:21`. Никаких новых dev-зависимостей.

## Risks / Landmines

| ID | Risk | Severity | Mitigation |
|----|------|----------|------------|
| R1 | D-02 cancel-queue uses `status='cancelled'` (CONTEXT.md) but enum lacks value — must decide: расширить enum или использовать `'failed'`. | Medium (blocking impl) | Planner решает: рекомендуем `'failed'` consistent with antispam pattern + расширенный `error_message='Conversation taken over manually'`. Open Question #1. |
| R2 | Bot filter (D-06) сработает раньше antispam-block для SpamBot — меняется поведение (один диалог `bot_ignored` вместо полного sender lifecycle pause). | High (regression risk on account safety) | Известные antispam bot_id (178220800, 777000) явно делегируем в `_handle_antispam_signal`. Test coverage: assert SpamBot incoming → sender.lifecycle_status='paused' (existing behaviour preserved). Open Question #2. |
| R3 | llm_calls cascade on conversation hard delete = audit data loss. | Medium | CONTEXT.md D-09 явно `ON DELETE CASCADE` — заложено. Альтернатива (`SET NULL` со orphan'ами) — Open Question #4. Test: DELETE conversation → llm_calls для другой conversation НЕ задеты. |
| R4 | D-04 auto-takeover ↔ queue worker race — Pitfall 6. | High (UX-cringe + duplicate sends) | Pre-send re-check guard в `_process_next_for_sender` (one extra SELECT, не трогает rate-limit intervals). Open Question #5. |
| R5 | `event.sender.bot` missing attribute on Channel/Chat sender — но мы фильтруем groups/channels раньше (line 582). | Low | `getattr(sender, 'bot', False) is True` — defensive. Test: mock sender без `.bot` → не падает. |
| R6 | bot filter false-positive — добропорядочный user marked as bot. | Very Low | Telegram официально различает bot vs user через `User.bot` поле. False-positive невозможен на уровне Telegram'а. Mitigation: оператор UI может вручную PATCH `status='active'` через `/conversations/{id}` PATCH. |
| R7 | Pitfall 9 — lead → finished переход теряет lead-метку для аналитики. | Medium | Decision needed (Open Question #6): leads = `status='lead'` (mutually exclusive, текущая CONTEXT.md D-16) ИЛИ `status IN ('lead','finished')` (накопительно). Рекомендация: оставить mutually exclusive как в CONTEXT.md, документировать в UI «leads = ещё не финишировали». |
| R8 | llm_calls.prompt JSONB 5-50KB rows → таблица растёт быстро. | Low (v1 acceptable per D-11) | Phase 5 не трогает; v2 решит partitioning. Composite индекс `(workspace_id, created_at DESC)` для inbox-debug query'ев. |
| R9 | C-09 search ILIKE без trigram index = Seq Scan на больших workspace. | Low | v1 OK при <10k диалогов. v2: добавить `pg_trgm` extension + GIN index. Planner может deferить search полностью (C-09 deferred ok). |
| R10 | `INSERT INTO messages` без workspace_id колонки — таблица не имеет её. | Resolved (not a risk) | Phase 2 D-11 / 02.1 CR-01 разобрались: `messages` workspace-scoped через JOIN на `conversations.workspace_id`. Все Phase 5 SELECT'ы JOIN на conversations. Insert не требует workspace_id (FK через conversation_id). |
| R11 | UPDATE `messages_log` отсутствует — но Phase 5 не трогает `messages_log`. | None | `messages_log` остаётся audit-trail для queue worker'а; analytics использует `messages` (per C-01 recommendation). |
| R12 | `Base.metadata.create_all` Phase 1 C-04 — может попытаться auto-create `llm_calls` из ORM модели, конфликтуя с миграцией. | Low | LLMCall ORM модель регистрируется БЕЗ `Base.metadata.create_all` (model used only for SELECT through SQLAlchemy expressions). Если `Base.metadata.create_all` всё-таки бежит — `CREATE TABLE IF NOT EXISTS` в миграции защищает. Test: idempotency test (test_migration_017 already covers). |

## File Map

| File | Action | One-line Purpose |
|------|--------|------------------|
| `migrations/017_phase5.sql` | NEW | ALTER conversations.status CHECK + CREATE TABLE llm_calls + 3 composite indexes |
| `app/routers/conversations.py` | REWRITE | Workspace-scoped CRUD inbox (8 endpoints) под `auth_dep`, drops `senders.is_active`, fixes enable-ai status semantics (D-03) |
| `app/routers/analytics.py` | NEW | 4 endpoints (workspace / campaigns / agents / senders) возвращающих AnalyticsCards — 4 одинаковых COUNT'а per scope (D-13) |
| `app/services/llm_logger.py` | NEW (C-05) | `log_llm_call()` — INSERT в `llm_calls` с try/except никогда не raise; resolves denormalised cols |
| `app/services/listener.py` | MODIFY | Inject proactive bot filter в `handle_incoming_message` (line 573-576) перед antispam-block; add `_handle_bot_message` helper |
| `app/services/ai_engine.py` | MODIFY | Wrap `client.chat.completions.create` (line 660 + line 780 second call) — timestamp + try/except + `await log_llm_call(...)` |
| `app/models/__init__.py` | MODIFY | Добавить `LLMCall(Base)` ORM модель (для SELECT'ов в `GET /conversations/{id}/llm-calls`) |
| `app/schemas/__init__.py` | MODIFY | Добавить Phase 5 Pydantic models (см. Pattern §C-03 ниже) |
| `app/main.py` | MODIFY | `app.include_router(conversations.router)` (восстанавливается) + `app.include_router(analytics.router)` (новый). Никаких новых workers в lifespan. |
| `tests/conftest.py` | MODIFY | Apply migration 017 + добавить `conversation_factory`, `messages_factory`, `llm_calls_factory`, `inbox_state_helpers` |
| `tests/test_inbox_router.py` | NEW | INBX-01/02/03/05 — list/get/messages + workspace isolation + filter combinations + warmup exclude + bot_ignored hide/show |
| `tests/test_inbox_manager_mode.py` | NEW | INBX-04 — disable-ai/enable-ai/PATCH (D-01/D-02/D-03) + cancel-queue side-effect |
| `tests/test_inbox_send_takeover.py` | NEW | INBX-04 D-04 auto-takeover + race с queue worker (Pitfall 6) |
| `tests/test_bot_filter.py` | NEW | AIRC-04 D-05/D-06 + antispam delegation + Pitfall 2/3/7 |
| `tests/test_analytics_router.py` | NEW | 4 endpoints workspace isolation + 401 без auth + 404 cross-workspace |
| `tests/test_analytics_correctness.py` | NEW | ANLX-01..04 seeded fixtures → expected counts (Pitfall 8/9 coverage) |
| `tests/test_llm_logger.py` | NEW | ANLX-05 payload shape + warmup exclusion + denormalisation correctness |
| `tests/test_llm_logger_no_block_on_error.py` | NEW | Pitfall 5 — INSERT fail НЕ блокирует возврат AI ответа |
| `tests/test_migration_017.py` | NEW | Idempotency double-apply + CHECK accepts 'bot_ignored' + FK CASCADE/SET NULL |

**Total files touched: 11 source + 9 test files = 20 files.**

## C-03: Pydantic Schemas Recommended Shape

```python
# Inbox schemas
class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    sender_id: UUID
    sender_slug: str
    contact_phone: str
    contact_name: Optional[str]
    contact_telegram_id: Optional[int]
    ai_enabled: bool
    ai_context_id: Optional[UUID]  # alias 'agent_id' (C-08 — Lovable contract)
    campaign_id: Optional[UUID]
    status: str  # one of 7 values
    paused_at: Optional[datetime]
    paused_reason: Optional[str]
    last_message: Optional[str] = None
    last_message_at: Optional[datetime] = None
    unread_count: int = 0
    created_at: datetime
    updated_at: datetime

class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    total: int

class ConversationUpdate(BaseModel):  # PATCH
    ai_enabled: Optional[bool] = None
    ai_context_id: Optional[UUID] = None
    status: Optional[str] = None  # validate against {'active','manual','paused','lead','handoff','finished','bot_ignored'}

    @model_validator(mode='after')
    def validate_status(self):
        if self.status and self.status not in {'active','manual','paused','lead','handoff','finished','bot_ignored'}:
            raise ValueError(f"Invalid status '{self.status}'")
        return self

class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    direction: str  # 'inbound' | 'outbound'
    message_text: str
    sent_by: str  # 'contact' | 'human' | 'ai'
    telegram_message_id: Optional[int]
    created_at: datetime

class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    total: int

class SendMessageFromUIRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096)

class SendMessageFromUIResponse(BaseModel):
    success: bool
    message_id: Optional[UUID] = None
    telegram_message_id: Optional[int] = None
    error: Optional[str] = None

# Analytics
class AnalyticsReplied(BaseModel):
    conversation_count: int
    message_count: int

class AnalyticsCards(BaseModel):
    sent: int
    replied: AnalyticsReplied
    leads: int
    finishes: int

# LLM calls
class LLMCallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    campaign_id: Optional[UUID]
    agent_id: Optional[UUID]
    sender_id: Optional[UUID]
    model: str
    prompt: dict  # full messages + tools + temp
    response_text: Optional[str]
    tool_calls: Optional[list[dict]]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
    latency_ms: Optional[int]
    error: Optional[str]
    created_at: datetime

class LLMCallListResponse(BaseModel):
    llm_calls: list[LLMCallResponse]
    total: int
```

## Open Questions

1. **D-02 `'cancelled'` vs `'failed'` queue status.**
   - What we know: CONTEXT.md D-02 explicitly says `status='cancelled'`. Existing `QueueItemStatus` SQLEnum doesn't have this value (only `pending|processing|sent|failed|paused`). Antispam pattern uses `'failed'`.
   - What's unclear: Add new enum value (+migration mini-extend) or reuse `'failed'`?
   - Recommendation: Use `'failed'` with explicit `error_message='Conversation taken over manually'`. Consistent with antispam, no enum/migration shape change. Mention in planner doc that this is a deliberate divergence from CONTEXT.md verbatim text. **Risk: minimal — UI behaviour unchanged (it reads error_message).**

2. **D-06 bot filter ordering vs antispam safety net.**
   - What we know: Both D-06 (bot_ignored, single dialog) and D-08 antispam (sender lifecycle pause + ALL queue cancel) currently fire on `event.sender.bot=True`. The order determines which wins.
   - What's unclear: Should D-06 take ALL bots (including SpamBot)? Or whitelist known antispam IDs to fall through to safety net?
   - Recommendation: Hardcoded delegation list `ANTISPAM_BOT_IDS = {178220800, 777000}` falls through to `_handle_antispam_signal`. All other bots → `_handle_bot_message`. Document in code why.

3. **llm_logger inline `await` vs `asyncio.create_task`.**
   - What we know: Inline `await log_llm_call(...)` is deterministic + easier to test. `create_task` fire-and-forget is faster (no INSERT latency on response).
   - What's unclear: Is +1-3ms INSERT latency acceptable per-AI-response?
   - Recommendation: Inline `await` in v1; if production latency becomes problem, switch to `create_task` (one line). Same try/except guarantee either way.

4. **llm_calls FK on conversation hard delete: CASCADE vs SET NULL.**
   - What we know: CONTEXT.md D-09 specifies `conversation_id NOT NULL ... ON DELETE CASCADE`. This means DELETE conversation drops llm_calls.
   - What's unclear: Audit trail considerations — should llm_calls survive conversation deletion (compliance) or follow it?
   - Recommendation: Follow CONTEXT.md verbatim (`CASCADE`). Document that conversation hard-delete drops audit. v2 may switch to SET NULL + soft-delete. Add UI warning «N llm_calls будут удалены» in DELETE response (count returned).

5. **Race condition D-04 send vs queue worker (Pitfall 6, C-02).**
   - What we know: Without guard, queue worker can send AI-message after manager takeover started.
   - What's unclear: Add pre-send re-check in queue worker (touches queue.py but NOT empirical intervals)?
   - Recommendation: Add re-check (`SELECT ai_enabled, status FROM conversations WHERE ...`) in `_process_next_for_sender` between status='processing' UPDATE and Telethon send. SKIP+UPDATE='failed', error='Conversation taken over manually' if ai_enabled=false. CLAUDE.md guard not violated — no interval changes, only one extra SELECT.

6. **Pitfall 9 — `leads = COUNT(*) WHERE status='lead'` vs `COUNT(*) WHERE status IN ('lead','finished')`.**
   - What we know: Current LLM tool logic: `mark_as_lead` → `status='lead'`, then `finish_conversation` → `status='finished'` (overwrites lead marker).
   - What's unclear: Does product want «total leads ever marked» or «leads currently open»?
   - Recommendation: Follow CONTEXT.md D-16 verbatim — `leads = COUNT(*) WHERE status='lead'`. Document semantic: this is «open leads not yet finished». UI label: «Активные лиды». If product wants cumulative leads, **Phase 6 / v2** may add `conversations.is_lead BOOLEAN` column or count via `llm_calls` tool_calls scan.

7. **C-09 search in v1 — keep or defer?**
   - What we know: Simple ILIKE on contact_phone/contact_name works in v1 (<10k diags). No trigram index needed yet.
   - What's unclear: Lovable UI provides search box? If no, defer.
   - Recommendation: **Include simple `?search=` param** — implementation is 3 lines (ILIKE on phone OR name). If Lovable wires it, it works; if not, no harm. Cost ~0.

8. **C-07 plan distribution: 4 plans vs 3 (merge 05-01 + 05-02).**
   - What we know: 05-01 (Inbox API) and 05-02 (Manager mode + bot filter) both modify `conversations.py` + `listener.py`. Splitting forces second plan to re-touch files.
   - What's unclear: ROADMAP commits to 4 plans.
   - Recommendation: **Merge into 3 plans**: (05-01) Inbox + Manager + Bot filter [conversations.py + listener.py + migration 017 partial], (05-02) Analytics [analytics.py + indexes in 017], (05-03) LLM log [llm_logger.py + ai_engine.py wrap + llm_calls table in 017]. Migration 017 lives in plan 05-01 (first in sequence). Plans 05-02 and 05-03 are largely independent and can run parallel after 05-01.

## Sources

### Primary (HIGH confidence)
- `app/routers/conversations.py` (legacy) — отдельно прочитан полностью (475 строк); все паттерны рерайта verified.
- `app/routers/campaigns.py:1-340` — workspace-scoped router pattern.
- `app/routers/agents.py:1-100` — campaign_count aggregation pattern.
- `app/services/listener.py:540-890` — `handle_incoming_message` + `_handle_antispam_signal` точная карта.
- `app/services/ai_engine.py:1-885` — полный файл; точка wrap'а на line 660 + line 780.
- `app/services/queue.py:540-930` — `_upsert_conversation` (line 822), `_fire_callback` (890), `enqueue_message` (934) патterns.
- `app/utils/auth.py:1-308` — auth_dep + AuthCtx shape.
- `app/models/__init__.py` — Conversation, MessageQueue, MessageLog, AIContext schemas.
- `app/schemas/__init__.py` — Pydantic conventions (ConfigDict from_attributes).
- `migrations/016_phase4.sql` — DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT idempotent pattern.
- `tests/conftest.py:1-216` — fixture infrastructure + migration application chain.
- `.planning/phases/05-inbox-analytics/05-CONTEXT.md` — 18 locked D-decisions + 9 C-discretion items (verbatim).
- `.planning/phases/04-campaigns/04-CONTEXT.md` — Phase 4 status enum + LLM tool signals.
- `.planning/REQUIREMENTS.md` — 11 phase 5 requirements (INBX-01..05, AIRC-04, ANLX-01..05).
- `.planning/codebase/ARCHITECTURE.md, CONCERNS.md, INTEGRATIONS.md` — architecture intel.
- `CLAUDE.md` — project rules.

### Secondary (MEDIUM confidence)
- Telethon `User.bot: bool` field — official Telethon API (referenced in CONTEXT.md D-05; not externally verified through context7 since Telethon already in use and `getattr(sender, 'bot', False)` defensive pattern is safe even if field absent).
- OpenAI Python SDK >=1.40.0 response object shape `response.usage.{prompt|completion|total}_tokens` + `response.choices[0].message.{content, tool_calls}` — verified through existing usage in `ai_engine.py:662-668`.

### Tertiary (LOW confidence)
- None — Phase 5 reuses existing patterns extensively; no external research needed.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new libraries; all referenced through existing code with line citations.
- Architecture: HIGH — 8 patterns extracted directly from existing files; D-decisions all map to clear implementations.
- Pitfalls: HIGH — 10 pitfalls identified, each with reproducible failure mode + concrete mitigation.
- Validation: HIGH — 36 test cases mapped to all 11 phase requirements; framework already established.
- Open Questions: 8 — none blocking; all have clear recommendations, awaiting planner ratification.

**Research date:** 2026-05-22
**Valid until:** 2026-06-22 (30 days — stable; no external API changes expected before Phase 5 implementation)

---

*Phase: 05-inbox-analytics*
*Research completed: 2026-05-22*
