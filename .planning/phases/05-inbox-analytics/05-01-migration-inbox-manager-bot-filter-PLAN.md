---
phase: 05-inbox-analytics
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/017_phase5.sql
  - app/models/__init__.py
  - app/schemas/__init__.py
  - app/routers/conversations.py
  - app/services/listener.py
  - app/services/queue.py
  - app/main.py
  - tests/conftest.py
  - tests/test_phase5_migration_017.py
  - tests/test_phase5_inbox.py
  - tests/test_phase5_inbox_manager_mode.py
  - tests/test_phase5_inbox_send_takeover.py
  - tests/test_phase5_bot_filter.py
autonomous: true
requirements: [INBX-01, INBX-02, INBX-03, INBX-04, INBX-05, AIRC-04]
requirements_addressed: [INBX-01, INBX-02, INBX-03, INBX-04, INBX-05, AIRC-04]
gap_closure: false

must_haves:
  truths:
    - "Миграция 017_phase5.sql применяется идемпотентно: расширяет conversations.status CHECK на 7 значений (active/manual/paused/lead/handoff/finished/bot_ignored), создаёт таблицу llm_calls с 15 колонками + 2 индексами + 3 composite индекса на conversations(workspace_id, X, status) для analytics (X = campaign_id / ai_context_id / sender_id)"
    - "GET /api/v1/conversations возвращает workspace-scoped список с фильтрами ?campaign_id=&agent_id=&sender_id=&status=&ai_enabled=&search=&limit=&offset= под Depends(auth_dep); по дефолту скрывает status='bot_ignored' (D-17), warmup-LATERAL exclude сохранён, last_message + unread_count через LATERAL JOINs"
    - "GET /api/v1/conversations/{id}, GET /api/v1/conversations/{id}/messages, PATCH /api/v1/conversations/{id} — все workspace-scoped, 404 (не 403) на cross-workspace"
    - "POST /api/v1/conversations/{id}/disable-ai ставит ai_enabled=false, status='manual', paused_at=NOW(), paused_reason='Manager took over' (D-01) И отменяет pending message_queue items по recipient_phone+workspace_id (D-02, status='failed' с error_message='Conversation taken over manually')"
    - "POST /api/v1/conversations/{id}/enable-ai обратный переход: только ai_enabled=true, paused_at=NULL, paused_reason=NULL (D-03 — status НЕ трогаем; lead/finished/manual сохраняются исторически)"
    - "POST /api/v1/conversations/{id}/send — auto-takeover (D-04): UPDATE conversations (ai_enabled=false, status='manual', paused_at=NOW(), paused_reason='Manager sent message via UI'), cancel-queue D-02 pattern, Telethon вызов через telegram_service.send_message_by_telegram_id, INSERT в messages с sent_by='human'. Workspace check + senders.lifecycle_status='active' AND auth_status='ok' (НЕ is_active, дропнута Phase 2 D-11) ДО Telegram-вызова"
    - "Bot filter в listener.handle_incoming_message: после get_sender — если getattr(sender, 'bot', False) is True И sender.id NOT IN {178220800, 777000} → INSERT в messages (история сохраняется) + INSERT/UPDATE conversations со status='bot_ignored', ai_enabled=false, paused_reason='Telegram bot account (event.sender.bot=True)', AI dispatch SKIPPED через return (D-05, D-06). UPDATE-guard: WHERE status='active' — НЕ затираем lead/handoff/finished/manual (Pitfall 3)"
    - "Известные antispam IDs (178220800 = SpamBot, 777000 = Telegram service) delegate в _handle_antispam_signal (D-08 safety net не сломан — sender lifecycle pause + cancel ВСЕХ queue items sender'а сохраняется)"
    - "Pre-send guard в queue.py:_process_next_for_sender: после UPDATE status='processing' и ДО Telethon send — SELECT ai_enabled, status FROM conversations WHERE workspace_id=:wid AND sender_id=:sid AND contact_phone=:phone; если ai_enabled=false → UPDATE queue item status='failed' error_message='Conversation taken over manually' и SKIP send (защита от race condition D-04, Pitfall 6 / Open Question #5). НЕ ТРОГАЕМ rate-limit/debounce интервалы — только добавляем один SELECT перед send"
    - "conversations.py зарегистрирован в main.py через app.include_router(conversations.router); legacy verify_api_key dependency полностью удалён из файла; legacy senders.is_active reference (строка 364) удалён"
    - "Pydantic v2 schemas (ConversationResponse, ConversationListResponse, ConversationUpdate с model_validator на 7 status значений, MessageResponse, MessageListResponse, SendMessageFromUIRequest, SendMessageFromUIResponse) в app/schemas/__init__.py с ConfigDict(from_attributes=True)"
    - "ORM LLMCall модель добавлена в app/models/__init__.py (UUID PK, workspace_id NOT NULL FK CASCADE, conversation_id NOT NULL FK CASCADE, campaign_id/agent_id/sender_id NULL FK SET NULL, model VARCHAR(50), prompt JSONB NOT NULL, response_text TEXT, tool_calls JSONB, prompt_tokens/completion_tokens/total_tokens/latency_ms INT, error TEXT, created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL)"
  artifacts:
    - path: "migrations/017_phase5.sql"
      provides: "DDL для Phase 5: ALTER conversations.status CHECK (7 значений) + CREATE TABLE llm_calls (15 колонок) + 5 indexes (2 на llm_calls + 3 composite на conversations)"
      contains: "ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check; ALTER TABLE conversations ADD CONSTRAINT conversations_status_check CHECK (status IN ('active','manual','paused','lead','handoff','finished','bot_ignored')); CREATE TABLE IF NOT EXISTS llm_calls"
    - path: "app/routers/conversations.py"
      provides: "Полный рерайт legacy роутера: 8 endpoints под auth_dep + workspace-scope, drops senders.is_active, fixes enable-ai status semantics (D-03)"
      contains: "@router.get(\"\"), @router.get(\"/{conversation_id}\"), @router.get(\"/{conversation_id}/messages\"), @router.patch(\"/{conversation_id}\"), @router.post(\"/{conversation_id}/enable-ai\"), @router.post(\"/{conversation_id}/disable-ai\"), @router.post(\"/{conversation_id}/send\"), @router.delete(\"/{conversation_id}\")"
    - path: "app/services/listener.py"
      provides: "Proactive bot filter inject перед antispam-block + новый метод _handle_bot_message с isolated AsyncSessionLocal + Pitfall 3 UPDATE guard"
      contains: "if getattr(sender, 'bot', False) is True:, async def _handle_bot_message"
    - path: "app/services/queue.py"
      provides: "Pre-send guard в _process_next_for_sender (один SELECT перед Telethon send — НЕ трогаем rate-limit/debounce/long-pause/flood-threshold интервалы per CLAUDE.md)"
      contains: "SELECT ai_enabled, status FROM conversations WHERE workspace_id"
    - path: "app/main.py"
      provides: "app.include_router(conversations.router) (восстанавливается из legacy not-registered)"
      contains: "include_router(conversations.router)"
    - path: "app/models/__init__.py"
      provides: "LLMCall ORM модель"
      contains: "class LLMCall(Base)"
    - path: "app/schemas/__init__.py"
      provides: "Phase 5 inbox + send-UI Pydantic schemas"
      contains: "class ConversationResponse, class ConversationListResponse, class ConversationUpdate, class MessageResponse, class MessageListResponse, class SendMessageFromUIRequest, class SendMessageFromUIResponse"
  key_links:
    - from: "app/main.py"
      to: "app/routers/conversations.py"
      via: "app.include_router(conversations.router)"
      pattern: "include_router\\(conversations\\.router\\)"
    - from: "app/routers/conversations.py POST /send + POST /disable-ai"
      to: "app/services/queue.py message_queue table"
      via: "UPDATE message_queue SET status='failed' WHERE recipient_phone=(SELECT contact_phone FROM conversations WHERE id=:cid) AND workspace_id=:wid AND status='pending'"
      pattern: "UPDATE message_queue.*Conversation taken over manually"
    - from: "app/routers/conversations.py POST /send"
      to: "telegram_service.send_message_by_telegram_id"
      via: "Telethon outbound send с senders.lifecycle_status='active' AND auth_status='ok'"
      pattern: "send_message_by_telegram_id"
    - from: "app/services/listener.py handle_incoming_message"
      to: "_handle_bot_message OR _handle_antispam_signal (delegation by sender.id)"
      via: "getattr(sender, 'bot', False) is True → if sender.id in {178220800, 777000} → antispam ELSE _handle_bot_message"
      pattern: "getattr\\(sender, 'bot', False\\)"
    - from: "app/services/queue.py _process_next_for_sender"
      to: "conversations table (pre-send guard)"
      via: "SELECT ai_enabled, status FROM conversations WHERE workspace_id=:wid AND sender_id=:sid AND contact_phone=:phone — SKIP send if ai_enabled=false"
      pattern: "SELECT ai_enabled.*conversations.*workspace_id"

threat_model:
  - id: T-05-01-WS-ISOLATION
    threat: "Cross-workspace inbox access — пользователь workspace A читает диалоги workspace B через подмену UUID в URL"
    mitigation: "Каждый из 8 conversations endpoints под Depends(auth_dep), каждый SELECT/UPDATE/DELETE имеет .where(Conversation.workspace_id == ctx.workspace_id) либо raw WHERE workspace_id = :wid с TODO(v2-rls) меткой. 404 (не 403) на cross-workspace чтобы не сливать information disclosure (Phase 1 D-04)."
    verification: "pytest tests/test_phase5_inbox.py::test_cross_workspace_returns_404 -x — создаёт 2 workspace, юзер A пытается GET/PATCH/DELETE conversation workspace B → 404 на всех 8 endpoints"
  - id: T-05-01-SEND-CROSS-WS
    threat: "POST /conversations/{id}/send — пользователь workspace A отправляет от лица sender'а workspace B через подмену UUID"
    mitigation: "В SELECT перед Telethon-вызовом JOIN на senders с двойным фильтром: c.workspace_id=:wid AND s.lifecycle_status='active' AND s.auth_status='ok'. Если row не найден → 404 ДО любого Telegram-вызова. Sender-session_string не возвращается до проверки workspace."
    verification: "pytest tests/test_phase5_inbox_send_takeover.py::test_send_cross_workspace_blocked -x — workspace A user → POST /send для workspace B conversation → 404, mock Telethon NOT called"
  - id: T-05-01-MIGRATION-SQLI
    threat: "Migration 017 SQL injection через CHECK constraint values"
    mitigation: "Migration — raw SQL без user input, все CHECK значения hardcoded literals (active/manual/paused/lead/handoff/finished/bot_ignored). Никаких dynamic SQL composition."
    verification: "grep -c 'CHECK (status IN' migrations/017_phase5.sql == 1; все 7 значений hardcoded в файле"
  - id: T-05-01-BOT-FILTER-BYPASS
    threat: "Атакующий помечает свой User-аккаунт как bot=True в Telethon чтобы избежать AI-ответа (DOS на AI-budget)"
    mitigation: "Telegram-side .bot=True доступно только @BotFather-аккаунтам, обычный User не может self-set этот флаг. Mitigation полагается на Telegram integrity. Дополнительно: status='bot_ignored' рендерится в inbox при ?status=bot_ignored — менеджер увидит подозрительные диалоги и может PATCH status='active' для возврата AI."
    verification: "pytest tests/test_phase5_bot_filter.py::test_bot_filter_creates_bot_ignored -x — mock event.sender.bot=True → создаётся conversation status='bot_ignored', ai_engine.generate_response NOT called"
  - id: T-05-01-QUEUE-RACE
    threat: "Race condition D-04: queue worker отправляет автосообщение после ручного takeover (Pitfall 6)"
    mitigation: "Pre-send guard в queue.py:_process_next_for_sender — между UPDATE status='processing' и Telethon send делает SELECT ai_enabled, status FROM conversations; если ai_enabled=false → UPDATE queue item на 'failed' SKIP send. Один extra SELECT, не трогает rate-limit интервалы (CLAUDE.md guard сохраняется)."
    verification: "pytest tests/test_phase5_inbox_send_takeover.py::test_send_race_with_queue_worker -x — seed queue item в processing → POST /send → assert mock_telethon NOT called for that recipient_phone, queue item.status='failed'"

---

<objective>
Создать миграцию 017_phase5.sql с DDL для Phase 5 (расширение conversations.status CHECK на 7 значений + новая таблица llm_calls + 5 composite indexes), полностью переписать legacy `app/routers/conversations.py` под `Depends(auth_dep)` + workspace-scope с 8 endpoints (list / detail / messages / PATCH / enable-ai / disable-ai / send / DELETE), добавить proactive bot filter в `listener.handle_incoming_message` с delegation в antispam safety net по hardcoded IDs, добавить pre-send guard в `queue._process_next_for_sender` для защиты от race condition D-04. Регистрация conversations.router в main.py.

Purpose: Фундаментальный план Phase 5 — закрывает 6 из 11 требований (INBX-01..05 + AIRC-04). Создаёт миграцию 017, которой воспользуются плана 05-02 (composite indexes для analytics) и 05-03 (таблица llm_calls). Все остальные планы зависят от этого через wave 2.

Output: 1 миграция, 1 рерайт роутера, 2 точечные правки сервисов (listener + queue pre-send guard), 1 добавление в main.py, расширение моделей и схем, 5 файлов тестов (migration / inbox / manager mode / send-takeover / bot-filter).
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/REQUIREMENTS.md
@.planning/phases/05-inbox-analytics/05-CONTEXT.md
@.planning/phases/05-inbox-analytics/05-RESEARCH.md
@.planning/phases/05-inbox-analytics/05-PATTERNS.md
@.planning/phases/05-inbox-analytics/05-VALIDATION.md
@.planning/phases/04-campaigns/04-CONTEXT.md
@.planning/phases/02.1-worker-hardening/02.1-CONTEXT.md
@.planning/codebase/ARCHITECTURE.md
@.planning/codebase/CONCERNS.md
@.planning/codebase/INTEGRATIONS.md
@CLAUDE.md
@migrations/016_phase4.sql
@app/models/__init__.py
@app/schemas/__init__.py
@app/routers/conversations.py
@app/routers/campaigns.py
@app/services/listener.py
@app/services/queue.py
@app/services/telegram.py
@app/utils/auth.py
@app/main.py
@tests/conftest.py
@tests/test_migration_016.py
@tests/test_campaign_router.py
@tests/test_listener.py

<interfaces>
<!-- Key types from existing codebase that this plan extends/depends on -->

From app/utils/auth.py:
```python
class AuthCtx(BaseModel):
    workspace_id: UUID
    user_id: Optional[str]
    source: Literal["jwt", "api_key"]
    role: Optional[str]

async def auth_dep(...) -> AuthCtx: ...
```

From app/models/__init__.py (existing models, do NOT modify python type for status):
```python
class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    sender_id = Column(UUID, ForeignKey("senders.id", ondelete="CASCADE"), nullable=False)
    contact_phone = Column(String(50), nullable=False)
    contact_name = Column(String(255))
    contact_telegram_id = Column(BigInteger)
    ai_enabled = Column(Boolean, default=True)
    ai_context_id = Column(UUID, ForeignKey("ai_contexts.id", ondelete="SET NULL"), nullable=True)
    campaign_id = Column(UUID, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)  # Phase 4 D-05
    status = Column(String(20), default="active", server_default="'active'")  # Phase 5 ONLY extends CHECK in SQL — NO python change
    paused_at = Column(DateTime(timezone=True))
    paused_reason = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

class MessageQueue(Base):
    # status enum: 'pending' | 'processing' | 'sent' | 'failed' | 'paused'  (NO 'cancelled')
    # workspace_id NOT NULL, recipient_phone, contact_phone, sender_id

class Sender(Base):
    # Phase 2 D-11: lifecycle_status ('active' | 'warming' | 'paused' | 'error') + auth_status ('ok' | 'reauth_needed' | ...)
    # is_active field DROPPED in Phase 2 — DO NOT REFERENCE
```

From app/services/telegram.py:
```python
async def send_message_by_telegram_id(
    sender_slug: str,
    encrypted_session: str,
    telegram_id: int,
    message: str,
    proxy: Optional[dict] = None,
) -> dict:  # {"success": bool, "telegram_message_id": Optional[int], "error": Optional[str]}
```

From app/services/listener.py (insertion points for Phase 5):
```python
# Line ~561: sender = await event.get_sender()
# Line ~573: if sender.id == me.id: return
# === Phase 5 D-06 inject HERE — BEFORE existing ANTISPAM_BOT_IDS block at line ~590 ===
# Line ~590-603: existing antispam block — DO NOT MODIFY (D-08 safety net)
# Line ~823-889: _handle_antispam_signal — DO NOT MODIFY (D-08 + CLAUDE.md)
```

From app/services/queue.py (insertion point for pre-send guard):
```python
# _process_next_for_sender method:
# 1. SELECT FOR UPDATE SKIP LOCKED pending item (Phase 02.1 CR-08)
# 2. UPDATE status='processing'
# === Phase 5 pre-send guard HERE — SELECT conversation, SKIP if ai_enabled=false ===
# 3. await telegram_service.send_message_by_telegram_id(...)
# 4. UPDATE status='sent' OR 'failed'
# CLAUDE.md GUARD: do NOT touch rate-limit / debounce / long-pause / flood-threshold intervals
```

From migrations/016_phase4.sql (idempotency pattern to mirror):
```sql
BEGIN;
ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active','manual','paused','lead','handoff','finished'));
COMMIT;
```

From app/routers/campaigns.py (workspace-scope helper pattern to copy):
```python
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
        raise HTTPException(404, detail={"code": "CAMPAIGN_NOT_FOUND", ...})
    return c
```
</interfaces>
</context>

<tasks>

<task type="execute" tdd="true">
  <name>Task 1: Migration 017 + ORM LLMCall + Pydantic schemas</name>
  <files>
    migrations/017_phase5.sql,
    app/models/__init__.py,
    app/schemas/__init__.py,
    tests/conftest.py,
    tests/test_phase5_migration_017.py
  </files>
  <wave>1</wave>
  <depends_on>[]</depends_on>
  <read_first>
    - migrations/016_phase4.sql (полностью — pattern idempotency, BEGIN/COMMIT, DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT, CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, partial WHERE indexes)
    - app/models/__init__.py (полностью — Campaign модель строки 440-486 как exact аналог LLMCall; Conversation модель строки 223-248 — НЕ менять python type для status, остаётся String(20))
    - app/schemas/__init__.py (полностью — CampaignResponse строки 556-585, CampaignCreate строки 499-528, CampaignUpdate строки 530-553 — паттерны для Phase 5 schemas)
    - .planning/phases/05-inbox-analytics/05-CONTEXT.md §D-07, §D-09, §D-10 (точная shape llm_calls и расширение CHECK)
    - .planning/phases/05-inbox-analytics/05-RESEARCH.md §"Pattern 8: Migration 017 shape" (строки 397-450 — full SQL block)
    - .planning/phases/05-inbox-analytics/05-PATTERNS.md §"`migrations/017_phase5.sql`" (строки 576-635 — анализ аналогов)
    - tests/conftest.py (строки 1-216 — pattern для applying migration в fixture; строка 67-68 — место где добавить migration 017 application)
    - tests/test_migration_016.py (полностью — exact аналог структуры test_phase5_migration_017.py)
  </read_first>
  <behavior>
    - Test 1: applying migration 017 двиновым (двойным) запуском не падает — `await raw_conn.driver_connection.execute(MIG_017); await raw_conn.driver_connection.execute(MIG_017)` succeeds
    - Test 2: после применения 017, INSERT в conversations со status='bot_ignored' проходит CHECK (raw INSERT через test fixture)
    - Test 3: INSERT в conversations со неизвестным status='nonexistent' падает с CheckViolation
    - Test 4: после применения 017, таблица llm_calls существует и имеет 15 ожидаемых колонок (information_schema.columns query)
    - Test 5: llm_calls FK on workspace DELETE — DELETE FROM workspaces WHERE id=:wid → llm_calls для этого workspace удалены (CASCADE)
    - Test 6: llm_calls FK on conversation DELETE — DELETE FROM conversations WHERE id=:cid → llm_calls для этой conversation удалены (CASCADE)
    - Test 7: llm_calls FK on campaign DELETE — DELETE FROM campaigns WHERE id=:cid → llm_calls.campaign_id становится NULL (SET NULL)
    - Test 8: llm_calls FK on agent (ai_contexts) DELETE — analogous SET NULL
    - Test 9: llm_calls FK on sender DELETE — analogous SET NULL
    - Test 10: composite index idx_conversations_workspace_campaign_status существует в pg_indexes
    - Test 11: composite index idx_conversations_workspace_agent_status существует
    - Test 12: composite index idx_conversations_workspace_sender_status существует
  </behavior>
  <action>
Создать `migrations/017_phase5.sql` со следующим exact содержанием (BEGIN/COMMIT блок, идемпотентность через `DROP CONSTRAINT IF EXISTS / IF NOT EXISTS`, копия pattern из migrations/016_phase4.sql:97-99):

```sql
-- migrations/017_phase5.sql
-- Phase 5: Inbox & Analytics
-- - Extends conversations.status CHECK to include 'bot_ignored' (D-07)
-- - Creates llm_calls audit table (D-09)
-- - Adds 3 composite indexes on conversations for real-time analytics (C-04)

BEGIN;

-- 1. Extend conversations.status CHECK constraint to include 'bot_ignored' (Phase 5 D-07)
ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active','manual','paused','lead','handoff','finished','bot_ignored'));

-- 2. llm_calls table (Phase 5 D-09 — OpenAI chat.completions audit log)
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

-- 3. Composite indexes for real-time analytics queries (Phase 5 C-04)
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

Добавить в `app/models/__init__.py` ORM модель `LLMCall(Base)` (insert после Campaign, перед summary блока с `__all__`), точная shape копии Campaign:

```python
class LLMCall(Base):
    """Audit log of OpenAI chat.completions.create() calls (Phase 5 D-09..D-12).

    Logged from ai_engine.generate_response wrap (NOT warmup). Used for
    inbox-debug "почему AI ответил так".
    """
    __tablename__ = "llm_calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    campaign_id = Column(
        UUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_contexts.id", ondelete="SET NULL"),
        nullable=True,
    )
    sender_id = Column(
        UUID(as_uuid=True),
        ForeignKey("senders.id", ondelete="SET NULL"),
        nullable=True,
    )
    model = Column(String(50), nullable=False)
    prompt = Column(JSONB, nullable=False)
    response_text = Column(Text, nullable=True)
    tool_calls = Column(JSONB, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

Добавить в `__all__` экспортов `"LLMCall"`. **НЕ менять** `Conversation.status` python type (остаётся `String(20)`). Все 7 значений CHECK обеспечиваются миграцией.

Добавить в `app/schemas/__init__.py` (вставить после CampaignResponse блока, ~строка 590):

```python
# === Phase 5 inbox schemas ===

CONVERSATION_STATUSES = {'active', 'manual', 'paused', 'lead', 'handoff', 'finished', 'bot_ignored'}


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    sender_id: UUID
    sender_slug: Optional[str] = None  # filled via JOIN in router
    contact_phone: str
    contact_name: Optional[str] = None
    contact_telegram_id: Optional[int] = None
    ai_enabled: bool
    ai_context_id: Optional[UUID] = None
    campaign_id: Optional[UUID] = None
    status: str  # one of 7 CONVERSATION_STATUSES
    paused_at: Optional[datetime] = None
    paused_reason: Optional[str] = None
    last_message: Optional[str] = None
    last_message_at: Optional[datetime] = None
    unread_count: int = 0
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    total: int


class ConversationUpdate(BaseModel):
    ai_enabled: Optional[bool] = None
    ai_context_id: Optional[UUID] = None
    status: Optional[str] = None

    @model_validator(mode='after')
    def _validate_status(self) -> "ConversationUpdate":
        if self.status is not None and self.status not in CONVERSATION_STATUSES:
            raise ValueError(
                f"Invalid status '{self.status}'. Must be one of: {sorted(CONVERSATION_STATUSES)}"
            )
        return self


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    direction: str
    message_text: str
    sent_by: str
    telegram_message_id: Optional[int] = None
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
```

Если `model_validator` ещё не импортирован в schemas/__init__.py — добавить в import block: `from pydantic import BaseModel, ConfigDict, Field, model_validator, constr`. Если уже импортирован — не дублировать.

Расширить `tests/conftest.py` — добавить применение migration 017 в session-fixture (вставить ПОСЛЕ application migration 016, копируя точный pattern из conftest.py:67-68):

```python
# tests/conftest.py — добавить ПОСЛЕ строки 67-68:
sql_017 = (PROJECT_ROOT / "migrations" / "017_phase5.sql").read_text()
await conn.exec_driver_sql(sql_017)
```

Также добавить фабрики (после `test_campaign_factory` определения, ~строка 360):

```python
@pytest_asyncio.fixture
async def test_conversation_factory(
    async_db_session,
    test_workspace,
    test_sender_factory,
):
    async def _make(
        sender=None,
        campaign_id=None,
        ai_context_id=None,
        contact_phone=None,
        contact_name=None,
        contact_telegram_id=None,
        status="active",
        ai_enabled=True,
    ):
        if sender is None:
            sender_dict = await test_sender_factory()
            sender_id = sender_dict["id"]
        else:
            sender_id = sender["id"] if isinstance(sender, dict) else sender.id

        if contact_phone is None:
            contact_phone = f"+7900{uuid.uuid4().hex[:7]}"

        row = (await async_db_session.execute(text("""
            INSERT INTO conversations (
                workspace_id, sender_id, contact_phone, contact_name,
                contact_telegram_id, ai_enabled, ai_context_id, campaign_id, status
            ) VALUES (
                :wid, :sid, :phone, :name, :tid, :ai_en, :aid, :cid, :status
            )
            RETURNING id, workspace_id, sender_id, contact_phone, contact_name,
                      contact_telegram_id, ai_enabled, ai_context_id, campaign_id,
                      status, paused_at, paused_reason, created_at, updated_at
        """), {
            "wid": str(test_workspace["id"]),
            "sid": str(sender_id),
            "phone": contact_phone,
            "name": contact_name,
            "tid": contact_telegram_id,
            "ai_en": ai_enabled,
            "aid": str(ai_context_id) if ai_context_id else None,
            "cid": str(campaign_id) if campaign_id else None,
            "status": status,
        })).first()
        await async_db_session.commit()
        return dict(row._mapping)
    return _make


@pytest_asyncio.fixture
async def test_message_factory(async_db_session):
    async def _make(conversation_id, count=1, direction="inbound", sent_by="contact", text_prefix="msg"):
        rows = []
        for i in range(count):
            r = (await async_db_session.execute(text("""
                INSERT INTO messages (conversation_id, direction, message_text, sent_by, telegram_message_id)
                VALUES (:cid, :dir, :txt, :sb, :tmid)
                RETURNING id, conversation_id, direction, message_text, sent_by, telegram_message_id, created_at
            """), {
                "cid": str(conversation_id),
                "dir": direction,
                "txt": f"{text_prefix}-{i}",
                "sb": sent_by,
                "tmid": None,
            })).first()
            rows.append(dict(r._mapping))
        await async_db_session.commit()
        return rows
    return _make
```

Создать `tests/test_phase5_migration_017.py` (12 тестов из <behavior>):

```python
import pathlib
import uuid as _uuid
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIG_017 = (PROJECT_ROOT / "migrations" / "017_phase5.sql").read_text()


async def test_migration_017_applies_once(async_db_session):
    """Migration 017 already applied by conftest fixture — assert schema present."""
    cols = (await async_db_session.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'llm_calls'
    """))).scalars().all()
    expected = {
        "id", "workspace_id", "conversation_id", "campaign_id", "agent_id",
        "sender_id", "model", "prompt", "response_text", "tool_calls",
        "prompt_tokens", "completion_tokens", "total_tokens", "latency_ms",
        "error", "created_at",
    }
    assert expected.issubset(set(cols)), f"Missing columns: {expected - set(cols)}"


async def test_migration_017_idempotent_double_apply(async_db_session):
    """Apply 017 again — must not fail (IF NOT EXISTS / DROP CONSTRAINT IF EXISTS)."""
    conn = await async_db_session.connection()
    raw = await conn.get_raw_connection()
    await raw.driver_connection.execute(MIG_017)


async def test_conversations_check_accepts_bot_ignored(
    async_db_session, test_workspace, test_sender_factory
):
    sender = await test_sender_factory()
    cid = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO conversations (id, workspace_id, sender_id, contact_phone, status)
        VALUES (:cid, :wid, :sid, '+79001234567', 'bot_ignored')
    """), {"cid": str(cid), "wid": str(test_workspace["id"]), "sid": str(sender["id"])})
    await async_db_session.commit()
    # passes if no CheckViolation


async def test_conversations_check_rejects_unknown_status(
    async_db_session, test_workspace, test_sender_factory
):
    from sqlalchemy.exc import IntegrityError
    sender = await test_sender_factory()
    with pytest.raises(IntegrityError):
        await async_db_session.execute(text("""
            INSERT INTO conversations (id, workspace_id, sender_id, contact_phone, status)
            VALUES (:cid, :wid, :sid, '+79001111111', 'nonexistent_status')
        """), {"cid": str(_uuid.uuid4()), "wid": str(test_workspace["id"]), "sid": str(sender["id"])})
        await async_db_session.commit()
    await async_db_session.rollback()


async def test_llm_calls_cascade_on_workspace_delete(
    async_db_session, test_workspace, test_sender_factory, test_conversation_factory
):
    conv = await test_conversation_factory()
    llm_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO llm_calls (id, workspace_id, conversation_id, model, prompt)
        VALUES (:lid, :wid, :cid, 'gpt-4o-mini', '{}'::jsonb)
    """), {"lid": str(llm_id), "wid": str(test_workspace["id"]), "cid": str(conv["id"])})
    await async_db_session.commit()
    await async_db_session.execute(text("DELETE FROM workspaces WHERE id = :wid"),
                                    {"wid": str(test_workspace["id"])})
    await async_db_session.commit()
    cnt = (await async_db_session.execute(text("SELECT COUNT(*) FROM llm_calls WHERE id = :lid"),
                                           {"lid": str(llm_id)})).scalar()
    assert cnt == 0


async def test_llm_calls_set_null_on_campaign_delete(
    async_db_session, test_conversation_factory, test_campaign_factory
):
    camp = await test_campaign_factory()
    conv = await test_conversation_factory(campaign_id=camp["id"])
    llm_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO llm_calls (id, workspace_id, conversation_id, campaign_id, model, prompt)
        VALUES (:lid, :wid, :cid, :camp, 'gpt-4o-mini', '{}'::jsonb)
    """), {"lid": str(llm_id), "wid": str(conv["workspace_id"]),
           "cid": str(conv["id"]), "camp": str(camp["id"])})
    await async_db_session.commit()
    await async_db_session.execute(text("DELETE FROM campaigns WHERE id = :cid"),
                                    {"cid": str(camp["id"])})
    await async_db_session.commit()
    camp_col = (await async_db_session.execute(text(
        "SELECT campaign_id FROM llm_calls WHERE id = :lid"
    ), {"lid": str(llm_id)})).scalar()
    assert camp_col is None


async def test_composite_indexes_exist(async_db_session):
    rows = (await async_db_session.execute(text("""
        SELECT indexname FROM pg_indexes WHERE tablename = 'conversations'
    """))).scalars().all()
    expected_indexes = {
        "idx_conversations_workspace_campaign_status",
        "idx_conversations_workspace_agent_status",
        "idx_conversations_workspace_sender_status",
    }
    missing = expected_indexes - set(rows)
    assert not missing, f"Missing indexes: {missing}"
```

Аналогичные тесты для conversation/agent/sender SET NULL — копировать pattern, заменяя campaign на agent (`DELETE FROM ai_contexts`), sender (`DELETE FROM senders`).
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_phase5_migration_017.py -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "CREATE TABLE IF NOT EXISTS llm_calls" migrations/017_phase5.sql` returns 0
    - `grep -q "CHECK (status IN ('active','manual','paused','lead','handoff','finished','bot_ignored'))" migrations/017_phase5.sql` returns 0
    - `grep -c "CREATE INDEX IF NOT EXISTS" migrations/017_phase5.sql` >= 5 (2 на llm_calls + 3 composite на conversations)
    - `grep -q "class LLMCall(Base)" app/models/__init__.py` returns 0
    - `grep -q "class ConversationResponse" app/schemas/__init__.py` returns 0
    - `grep -q "class ConversationUpdate" app/schemas/__init__.py` returns 0
    - `grep -q "_validate_status" app/schemas/__init__.py` returns 0 (model_validator for 7-status enum)
    - `grep -q "017_phase5.sql" tests/conftest.py` returns 0 (migration applied in fixture)
    - `grep -q "test_conversation_factory" tests/conftest.py` returns 0
    - `pytest tests/test_phase5_migration_017.py -x -q` — 12 tests pass
    - `pytest tests/test_migration_016.py -x -q` — existing Phase 4 tests still pass (regression)
  </acceptance_criteria>
  <done>
    Migration 017 идемпотентна, расширяет conversations.status CHECK на 7 значений, создаёт llm_calls с 15 колонками + 5 indexes (2 на llm_calls + 3 composite на conversations). ORM LLMCall зарегистрирована. Pydantic schemas для inbox endpoints определены с model_validator на 7 status. Conftest применяет migration 017 и предоставляет test_conversation_factory + test_message_factory. Все 12 тестов test_phase5_migration_017.py зелёные.
  </done>
</task>

<task type="execute" tdd="true">
  <name>Task 2: Conversations router rewrite (8 endpoints под auth_dep, drop legacy is_active/verify_api_key, D-01..D-04 manager mode, register в main.py)</name>
  <files>
    app/routers/conversations.py,
    app/main.py,
    tests/test_phase5_inbox.py,
    tests/test_phase5_inbox_manager_mode.py,
    tests/test_phase5_inbox_send_takeover.py
  </files>
  <wave>1</wave>
  <depends_on>["Task 1"]</depends_on>
  <read_first>
    - app/routers/conversations.py (полностью — legacy 475 строк, ВСЕ паттерны рерайта; особенно строки 60-145 warmup-LATERAL SQL для сохранения, строки 294-302 — bug D-03 fix, строки 338-438 — POST /send pattern, строка 364 — anti-pattern senders.is_active удаляем)
    - app/routers/campaigns.py (полностью — exact analog для AuthDep + workspace-scope helpers + CRUD pattern, особенно строки 67-82 _load_campaign, 145-202 multi-COUNT helpers)
    - app/routers/agents.py (паттерн workspace isolation + 404 на cross-workspace)
    - app/utils/auth.py (полностью — AuthCtx shape, auth_dep dependency)
    - app/services/telegram.py (send_message_by_telegram_id signature)
    - app/services/listener.py (строки 823-889 — _handle_antispam_signal cancel-queue pattern для копии в D-02)
    - app/main.py (полностью — pattern app.include_router; включая ВСЕ существующие импорты роутеров; импорт conversations добавляется в alphabetic order)
    - .planning/phases/05-inbox-analytics/05-CONTEXT.md §D-01..D-04, §D-17, §D-18 (CRUD endpoint requirements)
    - .planning/phases/05-inbox-analytics/05-RESEARCH.md §"Example 1", §"Example 2" (строки 656-823 — полный код list + send-takeover endpoints)
    - .planning/phases/05-inbox-analytics/05-PATTERNS.md §"`app/routers/conversations.py`" (строки 37-167 — anti-patterns + adaptation guide)
    - tests/test_campaign_router.py (полностью — exact pattern для workspace isolation tests, 404 на cross-workspace, integration test setup)
  </read_first>
  <behavior>
    - Test 1 (INBX-01): GET /api/v1/conversations возвращает 401 без credentials
    - Test 2 (INBX-01): GET /conversations с auth возвращает только conversations workspace юзера; cross-workspace conversations НЕ видны
    - Test 3 (INBX-01 / D-17): GET /conversations без status фильтра скрывает status='bot_ignored'
    - Test 4 (INBX-01 / D-17): GET /conversations?status=bot_ignored ЯВНО показывает их
    - Test 5 (INBX-01 warmup): GET /conversations исключает warmup-pair диалоги (NOT EXISTS s2.telegram_id=c.contact_telegram_id)
    - Test 6 (INBX-02): GET /conversations/{id}/messages возвращает только messages для conversation в workspace юзера, 404 на cross-workspace
    - Test 7 (INBX-02): pagination ?limit=10&offset=10 возвращает следующие 10 messages
    - Test 8 (INBX-03): GET /conversations/{id} возвращает status поле; все 7 значений отображаются в response
    - Test 9 (INBX-04 / D-01 / D-02): POST /conversations/{id}/disable-ai → ai_enabled=false, status='manual', paused_at NOT NULL, paused_reason set; pending message_queue items для recipient_phone → status='failed' с error_message='Conversation taken over manually'
    - Test 10 (INBX-04 / D-03): POST /conversations/{id}/enable-ai — НЕ трогает status (если был 'lead' → остаётся 'lead'); ai_enabled=true, paused_at=NULL, paused_reason=NULL
    - Test 11 (INBX-04 / D-04): POST /conversations/{id}/send — mock telegram_service.send_message_by_telegram_id возвращает success → conversation.status='manual', ai_enabled=false, paused_reason='Manager sent message via UI', INSERT в messages с sent_by='human'
    - Test 12 (INBX-04 / D-04 / Pitfall 1): POST /send отказывается с 404 если sender.lifecycle_status != 'active' или auth_status != 'ok'
    - Test 13 (INBX-04 cross-workspace): POST /send для conversation чужого workspace → 404, mock telegram NOT called
    - Test 14 (INBX-05 / D-18): GET /conversations?campaign_id=X strict EQ — conversations с campaign_id=NULL НЕ возвращаются
    - Test 15 (INBX-05): GET /conversations?agent_id=X — strict EQ
    - Test 16 (INBX-05): GET /conversations?sender_id=X — strict EQ
    - Test 17 (INBX-05 combined): GET /conversations?campaign_id=X&agent_id=Y&sender_id=Z — все 3 фильтра одновременно
    - Test 18 (auth): все 8 endpoints отвечают 401 без credentials, 404 на cross-workspace (НЕ 403)
    - Test 19 (D-04 race): POST /send когда есть pending queue item — после endpoint queue item.status='failed', queue worker НЕ отправляет (mock telethon NOT called for that recipient_phone) — race condition guard работает (тест проверяет ЧТО UPDATE заранее изменил queue item; пред-send guard в queue worker — Task 3)
    - Test 20 (DELETE): DELETE /conversations/{id} → 204; cross-workspace → 404 ДО любого UPDATE
  </behavior>
  <action>
ПОЛНОСТЬЮ переписать `app/routers/conversations.py` с нуля (legacy 475 строк удаляется). Не оставлять никаких legacy паттернов. Структура файла:

```python
"""Conversations / Inbox router — Phase 5 rewrite.

Workspace-scoped CRUD inbox с filters + manual manager mode (D-01..D-04).
Заменяет legacy роутер, который не был зарегистрирован в main.py и использовал
выпиленные verify_api_key и senders.is_active.
"""

import logging
import uuid
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import (
    ConversationResponse,
    ConversationListResponse,
    ConversationUpdate,
    MessageResponse,
    MessageListResponse,
    SendMessageFromUIRequest,
    SendMessageFromUIResponse,
)
from app.services import telegram as telegram_service
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


# === Workspace-scope helpers ===

async def _load_conversation_or_404(
    db: AsyncSession, ctx: AuthCtx, conversation_id: UUID
) -> dict:
    """Return conversation row or raise 404 (cross-workspace = 404, not 403)."""
    row = (await db.execute(text("""
        SELECT id, workspace_id, sender_id, contact_phone, contact_name,
               contact_telegram_id, ai_enabled, ai_context_id, campaign_id,
               status, paused_at, paused_reason, created_at, updated_at
        FROM conversations
        WHERE id = :cid AND workspace_id = :wid
        -- TODO(v2-rls): replaced by RLS policy app.workspace_id
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONVERSATION_NOT_FOUND", "message": "Conversation not found"},
        )
    return dict(row._mapping)


# === Endpoints ===

@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    campaign_id: Optional[UUID] = Query(None),
    agent_id: Optional[UUID] = Query(None),
    sender_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None),
    ai_enabled: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, le=100, ge=1),
    offset: int = Query(0, ge=0),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> ConversationListResponse:
    """INBX-01 + INBX-05 — list conversations workspace-scoped с фильтрами.

    D-17: по дефолту скрывает status='bot_ignored'; explicit ?status=bot_ignored показывает.
    Warmup-pair exclude (legacy preserved).
    """
    where_clauses = ["c.workspace_id = :wid"]
    params: dict = {"wid": str(ctx.workspace_id), "limit": limit, "offset": offset}

    # D-17: hide bot_ignored unless explicit
    if status is None:
        where_clauses.append("c.status != 'bot_ignored'")
    else:
        where_clauses.append("c.status = :status")
        params["status"] = status

    if campaign_id is not None:
        where_clauses.append("c.campaign_id = :campaign_id")
        params["campaign_id"] = str(campaign_id)
    if agent_id is not None:
        where_clauses.append("c.ai_context_id = :agent_id")
        params["agent_id"] = str(agent_id)
    if sender_id is not None:
        where_clauses.append("c.sender_id = :sender_id")
        params["sender_id"] = str(sender_id)
    if ai_enabled is not None:
        where_clauses.append("c.ai_enabled = :ai_enabled")
        params["ai_enabled"] = ai_enabled
    if search:
        where_clauses.append("(c.contact_phone ILIKE :search OR c.contact_name ILIKE :search)")
        params["search"] = f"%{search}%"

    where_sql = " AND ".join(where_clauses)

    list_query = text(f"""
        SELECT
            c.id, c.workspace_id, c.sender_id, s.slug AS sender_slug,
            c.contact_phone, c.contact_name, c.contact_telegram_id,
            c.ai_enabled, c.ai_context_id, c.campaign_id, c.status,
            c.paused_at, c.paused_reason, c.created_at, c.updated_at,
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
          -- warmup pair exclude (legacy preserved, workspace boundary added)
          AND NOT EXISTS (
              SELECT 1 FROM senders s2
              WHERE s2.workspace_id = :wid
                AND s2.telegram_id = c.contact_telegram_id
                AND s2.telegram_id IS NOT NULL
          )
        ORDER BY c.updated_at DESC
        LIMIT :limit OFFSET :offset
    """)

    count_query = text(f"""
        SELECT COUNT(*) FROM conversations c
        WHERE {where_sql}
          AND NOT EXISTS (
              SELECT 1 FROM senders s2
              WHERE s2.workspace_id = :wid
                AND s2.telegram_id = c.contact_telegram_id
                AND s2.telegram_id IS NOT NULL
          )
    """)

    rows = (await db.execute(list_query, params)).fetchall()
    total = (await db.execute(count_query, params)).scalar() or 0

    return ConversationListResponse(
        conversations=[ConversationResponse(**dict(r._mapping)) for r in rows],
        total=total,
    )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    """INBX-02 — детали диалога с last_message preview."""
    row = (await db.execute(text("""
        SELECT
            c.id, c.workspace_id, c.sender_id, s.slug AS sender_slug,
            c.contact_phone, c.contact_name, c.contact_telegram_id,
            c.ai_enabled, c.ai_context_id, c.campaign_id, c.status,
            c.paused_at, c.paused_reason, c.created_at, c.updated_at,
            last_msg.message_text AS last_message,
            last_msg.created_at AS last_message_at,
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
        WHERE c.id = :cid AND c.workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).first()

    if row is None:
        raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND",
                                          "message": "Conversation not found"})
    return ConversationResponse(**dict(row._mapping))


@router.get("/{conversation_id}/messages", response_model=MessageListResponse)
async def get_messages(
    conversation_id: UUID,
    limit: int = Query(100, le=200, ge=1),
    offset: int = Query(0, ge=0),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> MessageListResponse:
    """INBX-02 — история сообщений диалога с pagination."""
    # Workspace check via JOIN
    rows = (await db.execute(text("""
        SELECT m.id, m.conversation_id, m.direction, m.message_text,
               m.sent_by, m.telegram_message_id, m.created_at
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.id = :cid AND c.workspace_id = :wid
        ORDER BY m.created_at ASC
        LIMIT :limit OFFSET :offset
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id),
           "limit": limit, "offset": offset})).fetchall()

    # 404 если conversation не существует/не в workspace
    if not rows and offset == 0:
        # double-check existence
        exists = (await db.execute(text("""
            SELECT 1 FROM conversations WHERE id = :cid AND workspace_id = :wid
        """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).first()
        if exists is None:
            raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND",
                                              "message": "Conversation not found"})

    total = (await db.execute(text("""
        SELECT COUNT(*) FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.id = :cid AND c.workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).scalar() or 0

    return MessageListResponse(
        messages=[MessageResponse(**dict(r._mapping)) for r in rows],
        total=total,
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    """Обновить ai_enabled / status / ai_context_id (status validated в Pydantic)."""
    await _load_conversation_or_404(db, ctx, conversation_id)

    updates: list[str] = []
    params: dict = {"cid": str(conversation_id), "wid": str(ctx.workspace_id)}
    if payload.ai_enabled is not None:
        updates.append("ai_enabled = :ai_enabled")
        params["ai_enabled"] = payload.ai_enabled
    if payload.status is not None:
        updates.append("status = :status")
        params["status"] = payload.status
    if payload.ai_context_id is not None:
        updates.append("ai_context_id = :aid")
        params["aid"] = str(payload.ai_context_id)

    if not updates:
        return await get_conversation(conversation_id, ctx, db)

    updates.append("updated_at = NOW()")
    await db.execute(text(f"""
        UPDATE conversations SET {", ".join(updates)}
        WHERE id = :cid AND workspace_id = :wid
    """), params)
    await db.commit()

    return await get_conversation(conversation_id, ctx, db)


@router.post("/{conversation_id}/disable-ai", response_model=ConversationResponse)
async def disable_ai(
    conversation_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    """INBX-04 / D-01 / D-02 — manual switch to manager mode + cancel queue."""
    await _load_conversation_or_404(db, ctx, conversation_id)

    # UPDATE conversation (D-01)
    await db.execute(text("""
        UPDATE conversations
        SET ai_enabled = false,
            status = 'manual',
            paused_at = NOW(),
            paused_reason = 'Manager took over',
            updated_at = NOW()
        WHERE id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})

    # Cancel pending queue items (D-02; using 'failed' per Open Question #1 — QueueItemStatus enum lacks 'cancelled')
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
    return await get_conversation(conversation_id, ctx, db)


@router.post("/{conversation_id}/enable-ai", response_model=ConversationResponse)
async def enable_ai(
    conversation_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    """INBX-04 / D-03 — reverse switch. Status НЕ трогаем (preserve lead/finished/manual)."""
    await _load_conversation_or_404(db, ctx, conversation_id)

    # D-03 fix relative to legacy: НЕ ставим status='active'
    await db.execute(text("""
        UPDATE conversations
        SET ai_enabled = true,
            paused_at = NULL,
            paused_reason = NULL,
            updated_at = NOW()
        WHERE id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})
    await db.commit()
    return await get_conversation(conversation_id, ctx, db)


@router.post("/{conversation_id}/send", response_model=SendMessageFromUIResponse)
async def send_message_from_ui(
    conversation_id: UUID,
    payload: SendMessageFromUIRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> SendMessageFromUIResponse:
    """INBX-04 / D-04 — auto-takeover send из inbox UI."""
    # 1. Load conversation + sender (workspace + sender active check ДО Telegram-вызова)
    row = (await db.execute(text("""
        SELECT c.contact_telegram_id, c.contact_name,
               s.id AS sender_id, s.slug AS sender_slug,
               s.session_string, s.proxy
        FROM conversations c
        JOIN senders s ON c.sender_id = s.id
        WHERE c.id = :cid
          AND c.workspace_id = :wid
          AND s.lifecycle_status = 'active'
          AND s.auth_status = 'ok'
        -- Phase 2 D-11: senders.is_active is DROPPED; using lifecycle_status + auth_status.
        -- TODO(v2-rls): replaced by RLS policy
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).first()

    if row is None:
        raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND",
                                          "message": "Conversation not found or sender inactive"})
    if row.contact_telegram_id is None:
        raise HTTPException(400, detail={"code": "NO_TELEGRAM_ID",
                                          "message": "Contact has no Telegram ID"})

    # 2. Auto-takeover UPDATE (D-04)
    await db.execute(text("""
        UPDATE conversations
        SET ai_enabled = false,
            status = 'manual',
            paused_at = NOW(),
            paused_reason = 'Manager sent message via UI',
            updated_at = NOW()
        WHERE id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})

    # 3. Cancel pending queue items (D-02 pattern)
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

    # 4. Telethon send OUTSIDE transaction
    result = await telegram_service.send_message_by_telegram_id(
        sender_slug=row.sender_slug,
        encrypted_session=row.session_string,
        telegram_id=row.contact_telegram_id,
        message=payload.message,
        proxy=row.proxy,
    )

    if not result.get("success"):
        return SendMessageFromUIResponse(
            success=False,
            error=result.get("error", "Telegram send failed"),
        )

    # 5. INSERT message row after Telethon success
    message_id = uuid.uuid4()
    telegram_message_id = result.get("telegram_message_id")
    await db.execute(text("""
        INSERT INTO messages (id, conversation_id, direction, message_text, sent_by, telegram_message_id)
        VALUES (:id, :cid, 'outbound', :txt, 'human', :tg_mid)
        ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING
    """), {"id": str(message_id), "cid": str(conversation_id),
           "txt": payload.message, "tg_mid": telegram_message_id})
    await db.commit()

    return SendMessageFromUIResponse(
        success=True,
        message_id=message_id,
        telegram_message_id=telegram_message_id,
    )


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Hard delete (FK CASCADE удаляет messages + llm_calls)."""
    await _load_conversation_or_404(db, ctx, conversation_id)
    await db.execute(text("""
        DELETE FROM conversations
        WHERE id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})
    await db.commit()
```

Зарегистрировать роутер в `app/main.py` — добавить `conversations` в существующий import block (in alphabetic order между `contacts` и `folders`), и добавить `app.include_router(conversations.router)` после campaigns.router include (~line 98). Существующий код:

```python
# app/main.py — modify
from app.routers import (
    agents,
    campaigns,
    check_contacts,
    contacts,
    conversations,                       # Phase 5 — re-register (was legacy not-registered)
    folders,
    health,
    onboarding,
    send,
    senders,
    workspace,
)
# ...
app.include_router(campaigns.router)
app.include_router(conversations.router)  # Phase 5 — rewrite under AuthDep
```

Создать 3 файла тестов с покрытием 20 тестов из <behavior>. Структуру каждого файла копировать из `tests/test_campaign_router.py`:

**`tests/test_phase5_inbox.py`** — тесты 1-8, 14-18, 20 (list, detail, messages, filters, auth, DELETE).

**`tests/test_phase5_inbox_manager_mode.py`** — тесты 9 (disable-ai cancel queue), 10 (enable-ai preserves status).

**`tests/test_phase5_inbox_send_takeover.py`** — тесты 11 (auto-takeover happy path), 12 (sender inactive 404), 13 (cross-workspace 404 no telegram call), 19 (race: queue item marked failed pre-send).

Каждый тест использует:
- `auth_client_factory` или эквивалент из conftest для AuthCtx-mocked requests
- `monkeypatch.setattr("app.services.telegram.send_message_by_telegram_id", AsyncMock(...))` для mocking Telethon
- `test_conversation_factory` для seed conversations с правильным sender/workspace/campaign

Пример теста для D-03 (test 10):
```python
async def test_enable_ai_preserves_lead_status(
    async_client, auth_headers, test_conversation_factory
):
    conv = await test_conversation_factory(status='lead', ai_enabled=False)
    resp = await async_client.post(f"/api/v1/conversations/{conv['id']}/enable-ai",
                                    headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ai_enabled"] is True
    assert data["status"] == "lead"  # D-03: status not touched
    assert data["paused_at"] is None
    assert data["paused_reason"] is None
```
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_phase5_inbox.py tests/test_phase5_inbox_manager_mode.py tests/test_phase5_inbox_send_takeover.py -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "Depends(auth_dep)" app/routers/conversations.py` >= 8 (each of 8 endpoints)
    - `grep -c "verify_api_key" app/routers/conversations.py` == 0 (legacy fully removed)
    - `grep -c "is_active" app/routers/conversations.py` == 0 (legacy `senders.is_active` removed, only `lifecycle_status` + `auth_status` used)
    - `grep -q "lifecycle_status = 'active'" app/routers/conversations.py` returns 0
    - `grep -q "auth_status = 'ok'" app/routers/conversations.py` returns 0
    - `grep -q "Manager took over" app/routers/conversations.py` returns 0 (D-01 reason)
    - `grep -q "Manager sent message via UI" app/routers/conversations.py` returns 0 (D-04 reason)
    - `grep -q "Conversation taken over manually" app/routers/conversations.py` returns 0 (D-02 cancel reason)
    - `grep -q "include_router(conversations.router)" app/main.py` returns 0
    - `grep -q "from app.routers import" app/main.py && grep -q "conversations," app/main.py` returns 0
    - In `POST /enable-ai` handler — NO `status = '...'` in SQL (D-03 fix): `grep -A 10 'async def enable_ai' app/routers/conversations.py | grep -c 'SET status' == 0`
    - In `POST /disable-ai` handler — `grep -A 15 'async def disable_ai' app/routers/conversations.py | grep -q "status = 'manual'"` returns 0
    - In `POST /send` handler — workspace+sender check BEFORE Telethon call: `grep -B 5 'telegram_service.send_message_by_telegram_id' app/routers/conversations.py | grep -q 'lifecycle_status'`
    - `pytest tests/test_phase5_inbox.py -x -q` — all tests pass (test 1-8, 14-18, 20)
    - `pytest tests/test_phase5_inbox_manager_mode.py -x -q` — all tests pass (test 9, 10)
    - `pytest tests/test_phase5_inbox_send_takeover.py -x -q` — all tests pass (test 11, 12, 13, 19)
  </acceptance_criteria>
  <done>
    app/routers/conversations.py полностью переписан с нуля, 8 endpoints под auth_dep, workspace-isolation на каждом, legacy verify_api_key и senders.is_active удалены, D-01 (disable-ai → status='manual'), D-02 (cancel pending queue), D-03 (enable-ai НЕ трогает status), D-04 (POST /send auto-takeover) реализованы. main.py регистрирует роутер. 20 тестов в 3 файлах зелёные.
  </done>
</task>

<task type="execute" tdd="true">
  <name>Task 3: Listener proactive bot filter (D-05/D-06) + queue.py pre-send race guard + ANTISPAM_BOT_IDS delegation safety net (D-08)</name>
  <files>
    app/services/listener.py,
    app/services/queue.py,
    tests/test_phase5_bot_filter.py
  </files>
  <wave>1</wave>
  <depends_on>["Task 1", "Task 2"]</depends_on>
  <read_first>
    - app/services/listener.py (полностью — строки 540-890; ОСОБЕННО строки 561-580 (sender resolve + self-skip — точка inject'а), строки 590-603 (existing ANTISPAM block — pattern для bot filter inject), строки 823-889 (_handle_antispam_signal — НЕ трогаем, safety net D-08, копируем structure для нового _handle_bot_message)
    - app/services/queue.py (полностью — особенно _process_next_for_sender, строки 540-930; найти место между UPDATE status='processing' и Telethon send — туда вставляем pre-send guard SELECT)
    - .planning/phases/05-inbox-analytics/05-CONTEXT.md §D-05, §D-06, §D-07, §D-08 (точные требования)
    - .planning/phases/05-inbox-analytics/05-RESEARCH.md §"Pattern 4: Proactive bot filter" (строки 261-299), §"Example 5: Bot filter inject" (строки 1036-1127), §"Pitfall 3" (строки 543-562 — UPDATE guard для preserve lead/handoff/finished/manual), §"Pitfall 6" (строки 584-606 — pre-send guard rationale), §"Open Question #2" (delegation в antispam для известных IDs)
    - .planning/phases/05-inbox-analytics/05-PATTERNS.md §"`app/services/listener.py`" (строки 318-396)
    - CLAUDE.md §"Архитектурные правила" — НЕ трогать rate-limit/debounce/long-pause/flood-threshold интервалы в queue.py; НЕ ломать _handle_antispam_signal
    - tests/test_listener.py (полностью — pattern для mock Telethon event)
    - tests/test_listener_workspace_id.py (workspace_id discipline в тестах listener'а)
  </read_first>
  <behavior>
    - Test 1 (AIRC-04 / D-05 / D-06): mock event.sender с .bot=True (id=999, не в ANTISPAM_BOT_IDS) → handle_incoming_message вызывает _handle_bot_message; ai_engine.generate_response НЕ вызывается; conversation создаётся со status='bot_ignored', ai_enabled=false, paused_reason содержит 'Telegram bot account'
    - Test 2 (AIRC-04): bot message сохраняется в messages таблицу (direction='inbound', sent_by='contact', text из event.text)
    - Test 3 (Pitfall 3 — UPDATE guard): seeded conversation status='lead' → bot message arrives (event.sender.bot=True) → conversation.status остаётся 'lead' (НЕ затирается 'bot_ignored')
    - Test 4 (Pitfall 3): seeded conversation status='manual' → bot message → status остаётся 'manual'
    - Test 5 (Pitfall 3): seeded conversation status='active' → bot message → status меняется на 'bot_ignored'
    - Test 6 (D-08 safety net): mock event.sender.id=178220800 (SpamBot) и .bot=True → handle_incoming_message делегирует в _handle_antispam_signal (НЕ в _handle_bot_message); sender.lifecycle_status='paused', cancel queue для ВСЕХ recipient_phone of sender (existing antispam behaviour preserved)
    - Test 7 (D-08 safety net): mock event.sender.id=777000 (Telegram service) и .bot=True → also delegates to _handle_antispam_signal
    - Test 8 (Pitfall 7 defensive): mock event.sender без .bot attribute (e.g. Channel) → bot filter не падает, проходит через (НЕ возвращает) — но мы не вызываем _handle_bot_message
    - Test 9 (D-06): второе bot message для того же контакта — conversation НЕ дублируется, INSERT message ON CONFLICT DO NOTHING для повторного telegram_message_id
    - Test 10 (D-08 NOT broken): existing test_listener.py::test_antispam_signal_still_works все ещё проходит (regression)
    - Test 11 (Pre-send guard — Pitfall 6 / Open Question #5): seeded conversation ai_enabled=false status='manual'; seeded queue item status='processing' для recipient_phone == conversation.contact_phone; _process_next_for_sender вызывает pre-send guard → SELECT обнаруживает ai_enabled=false → UPDATE queue item status='failed' error_message='Conversation taken over manually'; mock telegram_service.send_message NOT called
    - Test 12 (Pre-send guard — happy path): conversation ai_enabled=true status='active'; queue worker НЕ skip'ает send, mock telegram_service.send_message CALLED
    - Test 13 (CLAUDE.md guard): grep -c "MIN_SEND_INTERVAL\\|MIN_RATE_INTERVAL\\|MAX_PER_HOUR\\|MAX_PER_DAY\\|DEBOUNCE" app/services/queue.py — count unchanged от baseline (assert no empirical constants modified — это grep-check; tests verify behaviour)
  </behavior>
  <action>
**Часть 1 — bot filter в listener.py.**

Добавить в `app/services/listener.py` новый метод `_handle_bot_message` (вставить ПОСЛЕ существующего `_handle_antispam_signal`, ~строка 890), точная structure (`async with AsyncSessionLocal()` + try/except + isolated session — копия pattern из `_handle_antispam_signal:840-880`):

```python
# app/services/listener.py — добавить после _handle_antispam_signal

# Hardcoded delegation list — Open Question #2: known antispam bots fall through
# to _handle_antispam_signal (safety net) instead of _handle_bot_message.
# This preserves the "sender lifecycle pause + cancel ALL queue" behaviour for
# accounts at risk of being flagged by Telegram.
ANTISPAM_BOT_IDS = {178220800, 777000}  # SpamBot, Telegram service


async def _handle_bot_message(
    self,
    sender_info: dict,
    sender,                              # Telethon User object
    event,                               # Telethon NewMessage event
    name: str,
    phone: str,
) -> None:
    """Phase 5 D-06 — store inbound bot message + flag conversation as bot_ignored.

    AI dispatch SKIPPED. UPDATE guard (Pitfall 3): only downgrades from
    status='active' to status='bot_ignored' — preserves lead/handoff/finished/manual.
    """
    try:
        async with AsyncSessionLocal() as session:
            existing = (await session.execute(text("""
                SELECT id, status FROM conversations
                WHERE sender_id = :sid AND contact_telegram_id = :tid
            """), {
                "sid": str(sender_info["id"]),
                "tid": sender.id,
            })).fetchone()

            if existing is None:
                conv_id = uuid.uuid4()
                await session.execute(text("""
                    INSERT INTO conversations (
                        id, workspace_id, sender_id, contact_phone, contact_name,
                        contact_telegram_id, ai_enabled, status, paused_at, paused_reason
                    )
                    VALUES (:id, :wid, :sid, :phone, :name, :tid,
                            false, 'bot_ignored', NOW(),
                            'Telegram bot account (event.sender.bot=True)')
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
                # Pitfall 3 guard: only downgrade from 'active'.
                # Preserve lead/handoff/finished/manual/paused — historic truth.
                if existing.status == 'active':
                    await session.execute(text("""
                        UPDATE conversations
                        SET status = 'bot_ignored',
                            ai_enabled = false,
                            paused_at = NOW(),
                            paused_reason = 'Telegram bot account (event.sender.bot=True)',
                            updated_at = NOW()
                        WHERE id = :cid
                    """), {"cid": str(conv_id)})

            # Save inbound message history (D-06)
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

            logger.info(
                "🤖 Bot message ignored: %s (%s) → conv=%s",
                name, phone, str(conv_id)[:8],
            )
    except Exception as e:
        logger.error("Bot filter failed: %s", e, exc_info=True)
```

Вставить inject в `handle_incoming_message` ПЕРЕД существующим antispam block (~line 590), точное положение — между self-skip (line 573) и existing ANTISPAM_BOT_IDS check:

```python
# app/services/listener.py — в handle_incoming_message, line ~574
# (после self-message skip, ПЕРЕД существующего ANTISPAM_KEYWORDS блока)

# === Phase 5 D-06: proactive bot filter ===
if getattr(sender, 'bot', False) is True:
    # Open Question #2: known antispam bot IDs delegate to safety net
    # (preserves sender lifecycle pause + cancel ALL queue behaviour per D-08).
    if sender.id in ANTISPAM_BOT_IDS:
        logger.warning(
            "🚨 Antispam bot ID detected (%s), delegating to safety net",
            sender.id,
        )
        await self._handle_antispam_signal(
            sender_info, name, sender.id, event.text or ""
        )
        return

    # Regular bot — store as bot_ignored, no AI dispatch.
    await self._handle_bot_message(sender_info, sender, event, name, phone)
    return  # AI dispatch SKIPPED
# === End Phase 5 D-06 ===

# Existing TELEGRAM_SERVICE_PHONES / ANTISPAM_KEYWORDS check continues unchanged
```

**Critical: НЕ ломаем `_handle_antispam_signal`** — оставляем строки 823-889 как есть. НЕ удаляем существующий ANTISPAM_KEYWORDS block (он покрывает edge case бота без `.bot=True` через keyword detection — например, если SpamBot когда-нибудь поменяет ID, keyword match всё равно сработает).

**Если `import uuid` или `AsyncSessionLocal` отсутствуют в listener.py** — добавить в import block. Если уже есть — не дублировать.

**Часть 2 — pre-send guard в queue.py (Pitfall 6 / Open Question #5).**

В `_process_next_for_sender` найти место МЕЖДУ `UPDATE message_queue SET status='processing'` и `await telegram_service.send_message_by_telegram_id(...)`. Вставить pre-send guard SELECT:

```python
# app/services/queue.py — в _process_next_for_sender
# Между UPDATE status='processing' и Telethon send

# === Phase 5 D-04 / Pitfall 6: pre-send race guard ===
# Защита от race-condition: менеджер делает POST /conversations/{id}/send
# одновременно с queue worker. UPDATE conversation в /send route уже произошёл,
# но cancel-queue не нашёл этот item (он уже в 'processing').
# Этот SELECT смотрит conversation.ai_enabled — если false → SKIP send.
# CLAUDE.md guard: НЕ трогаем эмпирические интервалы; только один extra SELECT.
guard_row = (await session.execute(text("""
    SELECT ai_enabled, status FROM conversations
    WHERE workspace_id = :wid
      AND sender_id = :sid
      AND contact_phone = :phone
    LIMIT 1
"""), {
    "wid": str(item.workspace_id),
    "sid": str(item.sender_id),
    "phone": item.recipient_phone,
})).first()

if guard_row is not None and guard_row.ai_enabled is False:
    logger.info(
        "⏭️  Pre-send guard: skipping queue item %s — conversation in manual mode",
        item.id,
    )
    await session.execute(text("""
        UPDATE message_queue
        SET status = 'failed',
            error_message = 'Conversation taken over manually',
            finished_at = NOW()
        WHERE id = :id
    """), {"id": str(item.id)})
    await session.commit()
    return  # SKIP Telethon send
# === End Phase 5 pre-send guard ===

# Existing await telegram_service.send_message_by_telegram_id(...) continues
```

Параметризация (`item.workspace_id`, `item.sender_id`, `item.recipient_phone`) зависит от точной структуры `_process_next_for_sender` — read_first инструкция требует прочитать функцию полностью; адаптировать имена переменных под реальные.

**Критично — НЕ трогать:**
- `MIN_SEND_INTERVAL`, `MAX_PER_MINUTE`, `MAX_PER_HOUR`, `MAX_PER_DAY` константы
- Debounce 3-5 мин логику
- Long-pause/flood-threshold логику
- `_handle_antispam_signal`
- Любые SQLEnum'ы (включая `QueueItemStatus` — используем существующее `'failed'`, не вводим `'cancelled'`)

**Часть 3 — тесты.**

Создать `tests/test_phase5_bot_filter.py` с 13 тестами из <behavior>. Структуру копировать из `tests/test_listener.py`. Использовать mock pattern для Telethon event:

```python
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _make_event(sender_id, is_bot, text_msg="Hello", message_id=12345):
    """Helper: mock Telethon event с настроенным sender."""
    event = MagicMock()
    event.sender = MagicMock()
    event.sender.id = sender_id
    event.sender.bot = is_bot
    event.sender.phone = None
    event.sender.first_name = "Bot"
    event.sender.last_name = None
    event.sender.username = "test_bot"
    event.text = text_msg
    event.id = message_id
    event.is_group = False
    event.is_channel = False
    event.get_sender = AsyncMock(return_value=event.sender)
    event.client = MagicMock()
    event.client.get_me = AsyncMock(return_value=MagicMock(id=1))  # not the bot
    return event


async def test_bot_filter_creates_bot_ignored_conversation(
    async_db_session, test_sender_factory, monkeypatch
):
    """Test 1: bot=True (non-antispam ID) → conversation status='bot_ignored', AI NOT called."""
    from app.services.listener import TelegramListener
    sender = await test_sender_factory()
    sender_info = {"id": sender["id"], "workspace_id": sender["workspace_id"], ...}

    # Mock ai_engine.generate_response to assert NOT called
    ai_mock = AsyncMock()
    monkeypatch.setattr("app.services.ai_engine.generate_response", ai_mock)

    listener_obj = TelegramListener(...)
    event = _make_event(sender_id=999, is_bot=True)

    await listener_obj.handle_incoming_message(event, sender_info)

    ai_mock.assert_not_called()

    conv = (await async_db_session.execute(text("""
        SELECT status, ai_enabled, paused_reason FROM conversations
        WHERE sender_id = :sid AND contact_telegram_id = 999
    """), {"sid": str(sender["id"])})).first()
    assert conv is not None
    assert conv.status == 'bot_ignored'
    assert conv.ai_enabled is False
    assert "Telegram bot" in conv.paused_reason
```

Аналогично для остальных 12 тестов из behavior list. Test 6, 7 — assert `_handle_antispam_signal` called (mock + assert called once); test 11 — seed conversation+queue item, monkeypatch telegram_service.send_message, call _process_next_for_sender, assert mock NOT called + queue.status='failed'; test 13 — regex grep test через subprocess.

  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_phase5_bot_filter.py tests/test_listener.py -x -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -q "async def _handle_bot_message" app/services/listener.py` returns 0
    - `grep -q "ANTISPAM_BOT_IDS" app/services/listener.py` returns 0 (the new constant for delegation)
    - `grep -q "178220800" app/services/listener.py` returns 0 (SpamBot id in delegation set)
    - `grep -q "777000" app/services/listener.py` returns 0 (Telegram service id in delegation set)
    - `grep -q "getattr(sender, 'bot', False) is True" app/services/listener.py` returns 0 (defensive Telethon check, Pitfall 7)
    - `grep -q "Telegram bot account (event.sender.bot=True)" app/services/listener.py` returns 0 (D-06 reason)
    - `grep -c "async def _handle_antispam_signal" app/services/listener.py` == 1 (existing safety net unchanged)
    - `grep -B 2 -A 5 "status = 'bot_ignored'" app/services/listener.py | grep -q "WHERE id = :cid"` (UPDATE guard exists)
    - `grep -B 5 "status = 'bot_ignored'" app/services/listener.py | grep -q "if existing.status == 'active'"` (Pitfall 3 guard)
    - `grep -q "Pre-send guard" app/services/queue.py` returns 0 (Phase 5 guard inserted)
    - `grep -q "Conversation taken over manually" app/services/queue.py` returns 0 (queue.py side of pre-send guard)
    - `grep -B 3 "telegram_service.send_message" app/services/queue.py | grep -q "SELECT ai_enabled"` (guard SQL right before telegram send)
    - **CLAUDE.md guard verification:** `grep -c "MIN_SEND_INTERVAL\|RATE_PER_MINUTE\|DEBOUNCE\|MAX_PER_HOUR" app/services/queue.py` matches baseline count (no constants modified — only one SELECT added). Baseline can be checked by `git diff app/services/queue.py | grep -E '^-.*MIN_|^-.*MAX_|^-.*DEBOUNCE'` returning empty (no removed empirical constants)
    - `pytest tests/test_phase5_bot_filter.py -x -q` — 13 tests pass
    - `pytest tests/test_listener.py -x -q` — existing tests still pass (D-08 safety net regression check)
  </acceptance_criteria>
  <done>
    Listener.handle_incoming_message содержит proactive bot filter ПЕРЕД antispam-block: `getattr(sender, 'bot', False) is True` → delegation в _handle_antispam_signal для ANTISPAM_BOT_IDS = {178220800, 777000} (D-08 safety net), иначе вызов нового _handle_bot_message который создаёт conversation со status='bot_ignored' (D-06) с UPDATE guard для preserve lead/handoff/finished/manual (Pitfall 3). _handle_antispam_signal остаётся неизменным. Queue.py содержит pre-send guard (один SELECT перед Telethon send) для защиты от D-04 race (Pitfall 6 / Open Question #5); эмпирические интервалы НЕ тронуты (CLAUDE.md guard). 13 тестов test_phase5_bot_filter.py + regression test_listener.py зелёные.
  </done>
</task>

</tasks>

<verification>
**Phase-level checks:**

1. **Migration 017 idempotency + schema correctness:**
   - `pytest tests/test_phase5_migration_017.py -x -q` — все тесты зелёные (12 тестов)
   - Manual: `psql -c "\d llm_calls"` показывает 15 колонок + 2 индекса
   - Manual: `psql -c "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='conversations_status_check'"` — CHECK включает 'bot_ignored'

2. **Inbox endpoints (INBX-01..05):**
   - `pytest tests/test_phase5_inbox.py -x -q` — все тесты зелёные (~15 тестов)
   - `curl -H "Authorization: Bearer $JWT" "http://localhost:8000/api/v1/conversations?campaign_id=$CID"` — workspace-scoped результат
   - `curl ... /api/v1/conversations` — без credentials возвращает 401
   - `curl ... /api/v1/conversations/other-workspace-conv-id` — 404 (cross-workspace)

3. **Manager mode (INBX-04 / D-01..D-04):**
   - `pytest tests/test_phase5_inbox_manager_mode.py tests/test_phase5_inbox_send_takeover.py -x -q`
   - Manual: POST /disable-ai → check pending message_queue items status='failed' для recipient_phone

4. **Bot filter (AIRC-04):**
   - `pytest tests/test_phase5_bot_filter.py -x -q` — все 13 тестов зелёные
   - Manual: send sample message от @BotFather → assert NO ai_engine.generate_response call, conversation.status='bot_ignored'

5. **D-08 safety net regression:**
   - `pytest tests/test_listener.py -x -q` — все existing tests зелёные
   - Manual: simulate SpamBot (id=178220800) message → assert sender.lifecycle_status='paused', cancel ВСЕХ queue items sender'а

6. **CLAUDE.md empirical intervals guard:**
   - `git diff app/services/queue.py | grep -E '^[+-].*MIN_|^[+-].*MAX_PER_|^[+-].*DEBOUNCE|^[+-].*FLOOD'` — empty output (никаких изменений constants)
   - `grep -c "MIN_SEND_INTERVAL\|RATE_PER_MINUTE\|DEBOUNCE\|MAX_PER_HOUR\|MAX_PER_DAY" app/services/queue.py` matches baseline

7. **Pre-send guard race protection:**
   - `pytest tests/test_phase5_inbox_send_takeover.py::test_send_race_with_queue_worker -x -q` зелёный
   - Manual: parallel POST /send + queue worker tick → no duplicate sends

8. **Main app smoke:**
   - `uvicorn app.main:app --reload` стартует без ошибок
   - GET /docs показывает все 8 conversations endpoints под "conversations" tag
</verification>

<success_criteria>
**Plan 05-01 complete when:**

- [ ] `migrations/017_phase5.sql` создан, идемпотентен, расширяет conversations.status CHECK на 7 значений включая 'bot_ignored', создаёт llm_calls с 15 колонками, 5 indexes (2 на llm_calls + 3 composite)
- [ ] `app/models/__init__.py` содержит `LLMCall(Base)` ORM модель (`Conversation.status` python type НЕ изменён, остаётся String(20))
- [ ] `app/schemas/__init__.py` содержит 7 Phase 5 schemas (ConversationResponse, ConversationListResponse, ConversationUpdate с model_validator на 7 status, MessageResponse, MessageListResponse, SendMessageFromUIRequest, SendMessageFromUIResponse)
- [ ] `app/routers/conversations.py` полностью переписан: 8 endpoints под Depends(auth_dep), workspace-scope на каждом, legacy `verify_api_key` и `senders.is_active` полностью удалены, D-01..D-04 manager mode реализованы, D-17 (default hide bot_ignored) implemented
- [ ] `app/main.py` регистрирует `conversations.router` (был legacy not-registered с Phase 1)
- [ ] `app/services/listener.py` содержит proactive bot filter перед antispam-block; новый `_handle_bot_message` с UPDATE guard (Pitfall 3); ANTISPAM_BOT_IDS delegation в safety net (D-08); `_handle_antispam_signal` НЕ изменён
- [ ] `app/services/queue.py` содержит pre-send guard SELECT перед Telethon-вызовом (Pitfall 6 / Open Question #5); эмпирические интервалы НЕ изменены (CLAUDE.md guard)
- [ ] `tests/conftest.py` применяет migration 017 в session-fixture и предоставляет `test_conversation_factory` + `test_message_factory`
- [ ] 5 файлов тестов созданы и все зелёные: `test_phase5_migration_017.py` (12), `test_phase5_inbox.py` (~15), `test_phase5_inbox_manager_mode.py` (2), `test_phase5_inbox_send_takeover.py` (4), `test_phase5_bot_filter.py` (13) — итого ~46 тестов
- [ ] Regression: `pytest tests/test_listener.py tests/test_migration_016.py -x -q` зелёные
- [ ] Все 6 требований Phase 5 покрытые этим планом (INBX-01..05, AIRC-04) реализованы и проверены тестами
</success_criteria>

<output>
After completion, create `.planning/phases/05-inbox-analytics/05-01-SUMMARY.md` следуя `.claude/get-shit-done/templates/summary.md`.

Include in summary:
- Migration 017 applied artifacts (CHECK extended, llm_calls created, 5 indexes)
- 8 conversations endpoints registered
- Bot filter inject location (listener.py:~575) + ANTISPAM_BOT_IDS delegation list
- Pre-send guard location в queue.py + CLAUDE.md guard confirmation
- Open Questions resolutions:
  - #1 (D-02 cancel queue status): used `'failed'` consistent с antispam, не вводили `'cancelled'` enum value
  - #2 (D-06 antispam delegation): hardcoded `ANTISPAM_BOT_IDS = {178220800, 777000}` fall-through
  - #5 (D-04 race): pre-send guard в queue.py — один SELECT, не трогает intervals
- Test coverage: ~46 tests across 5 files
- Files passed: `app/routers/conversations.py` rewrite verified — no `verify_api_key`, no `is_active`
</output>
