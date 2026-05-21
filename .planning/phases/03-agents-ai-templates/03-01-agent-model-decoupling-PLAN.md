---
phase: 03-agents-ai-templates
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/015_phase3.sql
  - app/models/__init__.py
  - app/services/ai_engine.py
  - app/services/listener.py
  - app/services/rotation.py
  - app/services/queue.py
  - app/routers/senders.py
  - app/schemas/__init__.py
  - tests/conftest.py
  - tests/test_migration_015.py
  - tests/test_ai_engine.py
  - tests/test_listener.py
  - tests/test_rotation.py
  - tests/test_queue_enqueue.py
  - tests/test_senders.py
autonomous: true
requirements: [AGNT-02, AGNT-03]
requirements_addressed: [AGNT-02, AGNT-03]

must_haves:
  truths:
    - "Миграция 015 успешно дропает 6 колонок ai_contexts (auto_pause_triggers, webhook_functions, document_webhook_url, max_message_length, response_delay_seconds, is_active) и колонку senders.ai_context_id"
    - "Миграция 015 создаёт UNIQUE INDEX (workspace_id, name) на ai_contexts — два агента с одинаковым именем в одном workspace запрещены"
    - "Миграция 015 идемпотентна — повторный запуск не падает (IF EXISTS / IF NOT EXISTS)"
    - "ORM-модель AIContext не содержит дропнутых полей; ORM-модель Sender не содержит ai_context_id и relationship ai_context"
    - "Worker-сервисы (ai_engine, listener, rotation, queue) не SELECT'ят дропнутые колонки — не падают на column does not exist"
    - "Senders router и схемы (SenderCreate/Update/Response) очищены от ai_context_id поля и selectinload(Sender.ai_context)"
  artifacts:
    - path: "migrations/015_phase3.sql"
      provides: "DROP COLUMN'ы + UNIQUE INDEX, идемпотентно"
      contains: "ALTER TABLE ai_contexts DROP COLUMN IF EXISTS auto_pause_triggers"
    - path: "app/models/__init__.py"
      provides: "Очищенная ORM AIContext + Sender без ai_context_id"
      contains: "class AIContext"
    - path: "app/services/ai_engine.py"
      provides: "get_context() без is_active/max_message_length/webhook_functions SELECT"
      contains: "FROM ai_contexts"
    - path: "app/services/rotation.py"
      provides: "_pick_best_sender без фильтра s.ai_context_id"
      contains: "_pick_best_sender"
    - path: "app/services/queue.py"
      provides: "INSERT INTO conversations принимает явный ai_context_id"
      contains: "ai_context_id"
    - path: "tests/test_migration_015.py"
      provides: "Smoke + idempotent + UNIQUE constraint + senders.ai_context_id absent"
      contains: "test_dropped_columns_absent"
  key_links:
    - from: "tests/conftest.py"
      to: "migrations/015_phase3.sql"
      via: "exec_driver_sql после 013"
      pattern: "sql_015 = .*migrations.*015_phase3.sql"
    - from: "app/services/queue.py"
      to: "enqueue_message ai_context_id parameter"
      via: "INSERT INTO conversations"
      pattern: "ai_context_id"
    - from: "app/models/__init__.py Sender"
      to: "никаких ai_context_id"
      via: "удалить Column + relationship"
      pattern: "ai_context_id"
---

<objective>
Phase 3 Plan 1: финальная чистка схемы ai_contexts — миграция 015 дропает 6 deprecated-колонок (концерн будущей Campaign) + senders.ai_context_id; ORM-модели приведены в соответствие; 5 worker-сервисов адаптированы под новую схему минимально-точечно, чтобы не уронить runtime после `docker compose up -d --build`.

Purpose: Подготовить чистый schema-фундамент для plan 03-02 (CRUD API). Без этой работы plan 02 не сможет писать тесты — ai_engine упадёт с `column "is_active" does not exist` на каждом входящем сообщении (RESEARCH Pitfall 1).

Output:
  - migrations/015_phase3.sql (идемпотентная миграция)
  - app/models/__init__.py (очищенный AIContext + Sender)
  - 5 точечных правок в services/ai_engine.py, services/listener.py, services/rotation.py, services/queue.py, routers/senders.py
  - schemas/__init__.py: SenderCreate/Update/Response без ai_context_id
  - tests/conftest.py: применение миграции 015 + test_agent_factory fixture
  - 6 Wave-0 тестовых файлов (test_migration_015, test_ai_engine, test_listener, test_rotation, test_queue_enqueue, test_senders extension)
</objective>

<execution_context>
@/Users/andrewbruce/Documents/outreach-platform/.claude/get-shit-done/workflows/execute-plan.md
@/Users/andrewbruce/Documents/outreach-platform/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/03-agents-ai-templates/03-CONTEXT.md
@.planning/phases/03-agents-ai-templates/03-RESEARCH.md
@.planning/phases/03-agents-ai-templates/03-VALIDATION.md
@.planning/phases/01-workspace-foundation/01-CONTEXT.md
@.planning/phases/02-tg-accounts-contacts/02-CONTEXT.md
@CLAUDE.md

# Existing code to read before modifying
@app/models/__init__.py
@app/services/ai_engine.py
@app/services/listener.py
@app/services/rotation.py
@app/services/queue.py
@app/routers/senders.py
@app/schemas/__init__.py
@tests/conftest.py
@migrations/013_phase2.sql
@migrations/014_phase2_1_hardening.sql

<interfaces>
<!-- Key types and contracts the executor needs. Extracted from codebase. -->

From app/models/__init__.py (current AIContext — to be cleaned):
```python
class AIContext(Base):
    __tablename__ = "ai_contexts"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    system_prompt = Column(Text, nullable=True)
    tone_of_voice = Column(Text, nullable=True)
    rules = Column(Text, nullable=True)
    company_info = Column(Text, nullable=True)
    product_info = Column(Text, nullable=True)
    faq = Column(JSONB, default={})
    # TO DROP (Phase 3 D-01):
    max_message_length = Column(BigInteger, default=500)
    response_delay_seconds = Column(BigInteger, default=5)
    auto_pause_triggers = Column(JSONB, default=[])
    is_active = Column(Boolean, default=True, server_default='true')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    senders = relationship("Sender", back_populates="ai_context")  # TO DROP
```

From app/models/__init__.py (current Sender — to lose ai_context_id):
```python
class Sender(Base):
    # ... other fields ...
    ai_context_id = Column(UUID(as_uuid=True), ForeignKey("ai_contexts.id", ondelete="SET NULL"), nullable=True)  # TO DROP (line 96)
    ai_context = relationship("AIContext", back_populates="senders")  # TO DROP (line 101)
```

From app/utils/auth.py (AuthCtx — reference, unchanged):
```python
class AuthCtx(BaseModel):
    workspace_id: UUID
    user_id: Optional[str]
    source: Literal["jwt", "api_key"]
    role: Optional[str]
```

From app/services/queue.py current INSERT (line ~705):
```python
"ai_ctx": str(sender.ai_context_id) if sender.ai_context_id else None,
```
After: `enqueue_message` accepts new `ai_context_id: Optional[UUID] = None` parameter, INSERT uses it directly.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Wave 0 — apply migration 015 in conftest + create test scaffolds + agent_factory fixture</name>
  <files>
    tests/conftest.py
    tests/test_migration_015.py
    tests/test_ai_engine.py
    tests/test_listener.py
    tests/test_rotation.py
    tests/test_queue_enqueue.py
    tests/test_senders.py
    migrations/015_phase3.sql
  </files>
  <read_first>
    - tests/conftest.py — посмотреть `_setup_database` fixture (строки 33-57), Phase 2 `test_sender_factory` (строки 121-153), `test_folder` (строки 162-169) — копируем pattern.
    - migrations/013_phase2.sql — взять BEGIN/COMMIT + IF NOT EXISTS / IF EXISTS шаблон.
    - migrations/014_phase2_1_hardening.sql — пример короткой идемпотентной миграции.
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"Финальная схема ai_contexts" (D-01..D-04) — точный список колонок к дропу.
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Example 1" (lines 456-484) — готовый migration template + §"Example 6" (lines 740-782) — test_agent_factory fixture.
    - .planning/phases/03-agents-ai-templates/03-VALIDATION.md §"Wave 0 Requirements" — точный список тест-файлов.
  </read_first>
  <behavior>
    - Test 1 (test_migration_015::test_dropped_columns_absent): `SELECT column_name FROM information_schema.columns WHERE table_name='ai_contexts'` НЕ возвращает auto_pause_triggers, webhook_functions, document_webhook_url, max_message_length, response_delay_seconds, is_active.
    - Test 2 (test_migration_015::test_unique_workspace_name): попытка INSERT'нуть второй AIContext с тем же (workspace_id, name) падает на UniqueViolationError.
    - Test 3 (test_migration_015::test_idempotent): exec_driver_sql(sql_015) дважды подряд — не падает на втором запуске.
    - Test 4 (test_migration_015::test_senders_no_ai_context_id): `SELECT column_name FROM information_schema.columns WHERE table_name='senders' AND column_name='ai_context_id'` возвращает пустой результат.
    - Test stubs (skeleton tests with `@pytest.mark.skip("pending implementation")`) — для test_ai_engine, test_listener, test_rotation, test_queue_enqueue, test_senders. Фактическая логика заполняется в задачах 3-7 этого плана.
    - Fixture test_agent_factory работает: `agent = await test_agent_factory(name="Sales")` → возвращает AIContext с workspace_id=test_workspace.id.
  </behavior>
  <action>
    Создать файл `migrations/015_phase3.sql` ровно с содержимым:
    ```sql
    -- migrations/015_phase3.sql
    -- Phase 3: Agents (AI Templates) — cleanup ai_contexts schema
    -- Drops 6 deprecated columns (Campaign-concern — moved to Phase 4)
    -- Drops senders.ai_context_id (agent no longer tied to sender — D-04)
    -- Adds UNIQUE (workspace_id, name) for duplicate-protection (D-02)
    -- БД чистая (Phase 1 D-01) — no backfill needed.
    -- All operators idempotent (IF EXISTS / IF NOT EXISTS).

    BEGIN;

    -- ── 1. ai_contexts: drop deprecated columns (D-01) ──────────────────────────
    ALTER TABLE ai_contexts DROP COLUMN IF EXISTS auto_pause_triggers;
    ALTER TABLE ai_contexts DROP COLUMN IF EXISTS webhook_functions;
    ALTER TABLE ai_contexts DROP COLUMN IF EXISTS document_webhook_url;
    ALTER TABLE ai_contexts DROP COLUMN IF EXISTS max_message_length;
    ALTER TABLE ai_contexts DROP COLUMN IF EXISTS response_delay_seconds;
    ALTER TABLE ai_contexts DROP COLUMN IF EXISTS is_active;

    -- ── 2. senders: drop ai_context_id (D-04) ───────────────────────────────────
    ALTER TABLE senders DROP COLUMN IF EXISTS ai_context_id;

    -- ── 3. ai_contexts: UNIQUE (workspace_id, name) for duplicate-protection (D-02) ──
    CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_contexts_workspace_name
        ON ai_contexts(workspace_id, name);

    COMMIT;
    ```

    Обновить `tests/conftest.py`:
    1. После строки `sql_013 = ...` + `await conn.exec_driver_sql(sql_013)` (строки 51-52) добавить:
    ```python
    # Phase 3 migration: drop deprecated ai_contexts columns + drop senders.ai_context_id
    # + UNIQUE (workspace_id, name).
    sql_015 = (PROJECT_ROOT / "migrations" / "015_phase3.sql").read_text()
    await conn.exec_driver_sql(sql_015)
    ```
    2. После блока импортов моделей (строка 108: `from app.models import Folder, Contact, Sender, Workspace`) — расширить на `AIContext`:
    ```python
    from app.models import Folder, Contact, Sender, Workspace, AIContext  # noqa: E402
    ```
    3. После `test_folder` fixture (после строки 169) добавить (по шаблону RESEARCH Example 6):
    ```python
    @pytest_asyncio.fixture
    async def test_agent_factory(async_db_session: AsyncSession, test_workspace: Workspace):
        """Factory for AIContext (agent) test fixtures. Phase 3 C-06."""
        counter = {"n": 0}

        async def _make(**overrides) -> AIContext:
            counter["n"] += 1
            defaults = dict(
                workspace_id=test_workspace.id,
                name=f"Test Agent {counter['n']}",
                system_prompt="You are a helpful sales agent.",
                tone_of_voice="friendly",
                rules="Always be polite.",
                faq=[],
                company_info="Test Co.",
                product_info="Test Product.",
            )
            defaults.update(overrides)
            agent = AIContext(**defaults)
            async_db_session.add(agent)
            await async_db_session.commit()
            await async_db_session.refresh(agent)
            return agent

        return _make
    ```

    Создать `tests/test_migration_015.py` с 4 тестами:
    ```python
    """Phase 3 — migration 015 smoke + idempotency + schema invariants."""
    import pathlib
    import pytest
    import pytest_asyncio
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from app.database import engine
    from app.models import AIContext, Workspace

    PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

    pytestmark = pytest.mark.asyncio


    async def test_dropped_columns_absent(_setup_database):
        async with engine.connect() as conn:
            r = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='ai_contexts'"
            ))
            cols = {row[0] for row in r.fetchall()}
        for dropped in {"auto_pause_triggers", "webhook_functions", "document_webhook_url",
                        "max_message_length", "response_delay_seconds", "is_active"}:
            assert dropped not in cols, f"Column '{dropped}' must be dropped by migration 015"


    async def test_senders_no_ai_context_id(_setup_database):
        async with engine.connect() as conn:
            r = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='senders' AND column_name='ai_context_id'"
            ))
            assert r.fetchone() is None, "senders.ai_context_id must be dropped by migration 015"


    async def test_unique_workspace_name(async_db_session, test_workspace):
        a1 = AIContext(workspace_id=test_workspace.id, name="DupName", system_prompt="p1")
        async_db_session.add(a1)
        await async_db_session.commit()
        a2 = AIContext(workspace_id=test_workspace.id, name="DupName", system_prompt="p2")
        async_db_session.add(a2)
        with pytest.raises(IntegrityError):
            await async_db_session.commit()
        await async_db_session.rollback()


    async def test_idempotent():
        sql_015 = (PROJECT_ROOT / "migrations" / "015_phase3.sql").read_text()
        async with engine.begin() as conn:
            await conn.exec_driver_sql(sql_015)
        # Re-running must not fail
        async with engine.begin() as conn:
            await conn.exec_driver_sql(sql_015)
    ```

    Создать skeleton test files (с `@pytest.mark.skip("pending: filled by task N")`) для:
    - `tests/test_ai_engine.py` — test `test_get_context_phase3_schema` (skip)
    - `tests/test_listener.py` — test `test_get_active_senders_no_ai_context_id` (skip)
    - `tests/test_rotation.py` — test `test_pick_best_sender_workspace_only` (skip)
    - `tests/test_queue_enqueue.py` — test `test_enqueue_with_explicit_ai_context_id` (skip)
    - `tests/test_senders.py` — test `test_response_has_no_ai_context_id` (skip)

    Каждый skeleton — минимум 5 строк: импорты + `pytestmark = pytest.mark.asyncio` + один skip'нутый async-test с TODO docstring.
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_migration_015.py -x -v</automated>
  </verify>
  <acceptance_criteria>
    - File `migrations/015_phase3.sql` exists and contains all of: `ALTER TABLE ai_contexts DROP COLUMN IF EXISTS auto_pause_triggers`, `ALTER TABLE ai_contexts DROP COLUMN IF EXISTS webhook_functions`, `ALTER TABLE ai_contexts DROP COLUMN IF EXISTS document_webhook_url`, `ALTER TABLE ai_contexts DROP COLUMN IF EXISTS max_message_length`, `ALTER TABLE ai_contexts DROP COLUMN IF EXISTS response_delay_seconds`, `ALTER TABLE ai_contexts DROP COLUMN IF EXISTS is_active`, `ALTER TABLE senders DROP COLUMN IF EXISTS ai_context_id`, `CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_contexts_workspace_name`, `BEGIN;`, `COMMIT;`.
    - `tests/conftest.py` contains `sql_015 = (PROJECT_ROOT / "migrations" / "015_phase3.sql").read_text()` AND `await conn.exec_driver_sql(sql_015)`.
    - `tests/conftest.py` imports `AIContext` from `app.models` (in line ~108 import block).
    - `tests/conftest.py` defines `async def test_agent_factory` returning a factory that creates AIContext with workspace_id from test_workspace.
    - File `tests/test_migration_015.py` exists with all 4 async test functions named: `test_dropped_columns_absent`, `test_senders_no_ai_context_id`, `test_unique_workspace_name`, `test_idempotent`.
    - Skeleton test files exist: `tests/test_ai_engine.py`, `tests/test_listener.py`, `tests/test_rotation.py`, `tests/test_queue_enqueue.py`, `tests/test_senders.py` — each contains `pytestmark = pytest.mark.asyncio` AND at least one `@pytest.mark.skip` async test.
    - `pytest tests/test_migration_015.py -x -v` exits 0 with 4 passing tests.
  </acceptance_criteria>
  <done>Миграция 015 написана и применяется в conftest; test_agent_factory готова; 4 теста migration 015 зелёные; skeleton-файлы остальных тестов созданы — готовы наполняться по мере выполнения следующих задач.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Очистить ORM — AIContext без 4 дроп-полей + Sender без ai_context_id и relationship</name>
  <files>
    app/models/__init__.py
  </files>
  <read_first>
    - app/models/__init__.py — текущее состояние AIContext (строки 146-168) и Sender (строки 73-101).
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"D-02" — финальный список колонок AIContext.
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"D-04" — удалить ai_context_id + relationship из Sender.
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Pitfall 4" — почему важно удалить relationship одновременно (Pydantic from_attributes=True упадёт).
  </read_first>
  <behavior>
    - После правки `Sender(...)` ORM-объект НЕ имеет атрибута `ai_context_id` — `hasattr(Sender, 'ai_context_id') == False`.
    - После правки `AIContext(...)` ORM-объект НЕ имеет атрибутов `max_message_length`, `response_delay_seconds`, `auto_pause_triggers`, `is_active`, `senders` (relationship).
    - `Base.metadata.create_all` в тестах создаёт схему без дропнутых колонок (миграция 015 затем "no-op" применится через IF EXISTS).
  </behavior>
  <action>
    В `app/models/__init__.py`:

    1. В классе `Sender` (строка 73) **удалить** строки:
       - Строка 96: `ai_context_id = Column(UUID(as_uuid=True), ForeignKey("ai_contexts.id", ondelete="SET NULL"), nullable=True)`
       - Строка 101: `ai_context = relationship("AIContext", back_populates="senders")`

    2. В классе `AIContext` (строка 146) **удалить** строки:
       - Строка 160: `max_message_length = Column(BigInteger, default=500)`
       - Строка 161: `response_delay_seconds = Column(BigInteger, default=5)`
       - Строка 162: `auto_pause_triggers = Column(JSONB, default=[])`
       - Строка 163: `is_active = Column(Boolean, default=True, server_default='true')`
       - Строка 168: `senders = relationship("Sender", back_populates="ai_context")`

    3. После строки 159 (`faq = Column(JSONB, default={})`) — оставить порядок D-02:
       `id`, `workspace_id`, `name`, `system_prompt`, `rules`, `tone_of_voice`, `faq`, `company_info`, `product_info`, `created_at`, `updated_at`.
       НБ: текущий порядок в файле уже почти соответствует D-02 (нужно лишь убрать дропнутые поля); не переупорядочивать остальное.

    4. Сразу под `class AIContext` — закомментировать (НЕ удалять) импорт `Boolean` если он больше не используется в этом файле. Сначала grep'нуть: если Boolean используется где-то ещё (например `ContactCheckWorker`-related поля), оставить.

    После правки:
    ```python
    class Sender(Base):
        __tablename__ = "senders"
        id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
        slug = Column(String(50), nullable=False, index=True)
        name = Column(String(100), nullable=False)
        phone = Column(String(20), nullable=False)
        session_string = Column(Text, nullable=False)
        role = Column(String(20), nullable=False, server_default='sender')
        proxy = Column(JSONB, nullable=True)
        auth_status = Column(String(30), nullable=False, server_default='ok')
        lifecycle_status = Column(String(20), nullable=False, server_default='active')
        rate_per_min = Column(Integer, nullable=False, server_default='4')
        rate_per_hour = Column(Integer, nullable=False, server_default='20')
        rate_per_day = Column(Integer, nullable=False, server_default='150')
        created_at = Column(DateTime(timezone=True), server_default=func.now())
        last_used_at = Column(DateTime(timezone=True), onupdate=func.now())
        # NB: ai_context_id dropped (Phase 3 D-04). Sender больше не "знает" агента —
        # связь идёт через Campaign в Phase 4.

        messages = relationship("MessageLog", back_populates="sender")
        contacts = relationship("ContactCache", back_populates="sender")


    class AIContext(Base):
        __tablename__ = "ai_contexts"
        id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
        name = Column(String(100), nullable=False)
        system_prompt = Column(Text, nullable=True)
        tone_of_voice = Column(Text, nullable=True)
        rules = Column(Text, nullable=True)
        company_info = Column(Text, nullable=True)
        product_info = Column(Text, nullable=True)
        faq = Column(JSONB, default={})
        created_at = Column(DateTime(timezone=True), server_default=func.now())
        updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
        # NB: max_message_length, response_delay_seconds, auto_pause_triggers, is_active dropped
        # (Phase 3 D-01) — концерны кампании, переезжают в Phase 4.
        # NB: senders relationship dropped — связь sender↔agent больше не через FK.
    ```

    НЕ ТРОГАТЬ: `Conversation` (строки 221-242) — `ai_context_id` FK там **остаётся** (D-05). НЕ ТРОГАТЬ: `ContextContactAssignment` (строки 334-349) — таблица остаётся (D-05).
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && python -c "from app.models import AIContext, Sender; assert not hasattr(Sender, 'ai_context_id'); assert not hasattr(Sender, 'ai_context'); assert not hasattr(AIContext, 'max_message_length'); assert not hasattr(AIContext, 'response_delay_seconds'); assert not hasattr(AIContext, 'auto_pause_triggers'); assert not hasattr(AIContext, 'is_active'); assert not hasattr(AIContext, 'senders'); print('OK')"</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "ai_context_id" app/models/__init__.py` does NOT show any line inside `class Sender` block (lines 73-101 area).
    - `grep -n "ai_context = relationship" app/models/__init__.py` returns no matches.
    - `grep -n "max_message_length" app/models/__init__.py` returns no matches.
    - `grep -n "response_delay_seconds" app/models/__init__.py` returns no matches.
    - `grep -n "auto_pause_triggers" app/models/__init__.py` returns no matches.
    - `grep -n "is_active" app/models/__init__.py` does NOT show any line inside `class AIContext` block (other classes like WarmupPool may keep their own `is_active`).
    - `grep -n "senders = relationship" app/models/__init__.py` does NOT match inside `class AIContext` (may still exist in other places).
    - Python smoke check passes: `python -c "from app.models import AIContext, Sender; assert not hasattr(Sender, 'ai_context_id')"` exits 0.
    - `pytest tests/test_migration_015.py -x` still passes (model + migration aligned).
  </acceptance_criteria>
  <done>ORM очищена; Python-import не падает; `Base.metadata.create_all` теперь генерирует ai_contexts без дропнутых колонок; миграция 015 — идемпотентный no-op в этих условиях.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Адаптировать ai_engine.get_context — убрать is_active/max_message_length/webhook_functions из SELECT (RESEARCH Pitfall 1)</name>
  <files>
    app/services/ai_engine.py
    tests/test_ai_engine.py
  </files>
  <read_first>
    - app/services/ai_engine.py lines 50-97 — текущий `get_context` с SELECT'ом дропнутых колонок.
    - app/services/ai_engine.py lines 132-160 — `build_system_prompt` использует `context["max_message_length"]` — нужно поставить дефолт.
    - app/services/ai_engine.py lines 162-196 — `build_tools` использует `context.get("webhook_functions", [])` — после правки вернётся `[]`, никаких ошибок.
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Pitfall 1" — критический риск: ai_engine падает на каждом входящем сообщении после миграции если колонки остались в SELECT.
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Open Question 2" — webhook_functions = [] логика, build_tools([]) — chat.completion работает без tools. Phase 4 (CAMP-15) перенесёт tools на campaign.
  </read_first>
  <behavior>
    - Test 1 (test_ai_engine::test_get_context_phase3_schema): `ai_engine.get_context(session, context_id)` для существующего AIContext не падает на column error; возвращает dict с ключами `system_prompt`, `tone_of_voice`, `rules`, `company_info`, `max_message_length` (default 500), `webhook_functions` (default []).
    - Test 2 (test_ai_engine::test_get_context_returns_defaults_for_missing): для несуществующего context_id возвращает default_context (без обращения к дропнутым колонкам).
    - Существующая логика `build_system_prompt` и `build_tools([])` работает без изменений.
  </behavior>
  <action>
    В `app/services/ai_engine.py`:

    1. Заменить блок `try:` строки 69-91 (метод `get_context`) на:
    ```python
    try:
        result = await session.execute(
            text("""
                SELECT system_prompt, tone_of_voice, rules, company_info
                FROM ai_contexts
                WHERE id = :id
            """),
            {"id": context_id}
        )
        row = result.fetchone()

        if row:
            ctx = {
                "system_prompt": row[0] or DEFAULT_SYSTEM_PROMPT,
                "tone_of_voice": row[1] or "",
                "rules": row[2] or "",
                "company_info": row[3] or "",
                # Phase 3 D-01: max_message_length / webhook_functions / is_active columns
                # dropped — provide defaults so build_system_prompt + build_tools keep working.
                # TODO(phase-4): max_message_length moves to Campaign; webhook_functions
                # moves to Campaign tools (CAMP-15).
                "max_message_length": 500,
                "webhook_functions": []
            }
            self._context_cache[context_id] = (ctx, time.time())
            return ctx

        return default_context

    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка БД при получении контекста {context_id}: {e}")
        return default_context
    ```

    2. НЕ трогать функции `build_system_prompt`, `build_tools`, `execute_webhook`, `generate_response` — они уже корректно работают с дефолтными значениями `max_message_length=500` и `webhook_functions=[]`.

    Заполнить `tests/test_ai_engine.py` (заменить skeleton):
    ```python
    """Phase 3 — ai_engine.get_context adapter (RESEARCH Pitfall 1)."""
    import pytest

    pytestmark = pytest.mark.asyncio


    async def test_get_context_phase3_schema(async_db_session, test_agent_factory):
        """После миграции 015 get_context работает без is_active/max_message_length/webhook_functions в SELECT."""
        from app.services.ai_engine import ai_engine

        agent = await test_agent_factory(
            name="Phase 3 Agent",
            system_prompt="test prompt",
            tone_of_voice="friendly",
            rules="rule 1",
            company_info="Test Co.",
        )
        # Clear cache to force DB hit
        ai_engine._context_cache.clear()

        ctx = await ai_engine.get_context(async_db_session, str(agent.id))

        assert ctx["system_prompt"] == "test prompt"
        assert ctx["tone_of_voice"] == "friendly"
        assert ctx["rules"] == "rule 1"
        assert ctx["company_info"] == "Test Co."
        # Phase 3: defaults because columns dropped
        assert ctx["max_message_length"] == 500
        assert ctx["webhook_functions"] == []


    async def test_get_context_returns_defaults_for_missing(async_db_session):
        """Несуществующий context_id → default_context, без SQL ошибок."""
        from app.services.ai_engine import ai_engine

        ai_engine._context_cache.clear()
        ctx = await ai_engine.get_context(async_db_session, "00000000-0000-0000-0000-000000000000")

        assert ctx["max_message_length"] == 500
        assert ctx["webhook_functions"] == []
    ```
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_ai_engine.py -x -v</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "is_active" app/services/ai_engine.py` returns no matches.
    - `grep -n "max_message_length" app/services/ai_engine.py` returns matches ONLY in `build_system_prompt` (line ~146) and DEFAULT defaults dict (line ~57) AND inside the new return dict (~"max_message_length": 500). NO match inside the SQL text() block.
    - `grep -n "webhook_functions" app/services/ai_engine.py` returns matches in `build_tools`, `build_system_prompt`, `generate_response` and the new defaults dict. NO match inside the SQL text() SELECT clause.
    - The SQL block in `get_context` contains exactly: `SELECT system_prompt, tone_of_voice, rules, company_info FROM ai_contexts WHERE id = :id` (no `AND is_active = true`, no `max_message_length`, no `webhook_functions`).
    - `pytest tests/test_ai_engine.py::test_get_context_phase3_schema -x` exits 0.
    - `pytest tests/test_ai_engine.py::test_get_context_returns_defaults_for_missing -x` exits 0.
  </acceptance_criteria>
  <done>ai_engine не упадёт на `column does not exist` после миграции 015. Pitfall 1 закрыт. build_system_prompt + build_tools работают по-прежнему через дефолты.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 4: Адаптировать listener.get_active_senders + document_webhook_url block — выпилить ai_context_id и document_webhook_url SELECT (RESEARCH Pitfall 5)</name>
  <files>
    app/services/listener.py
    tests/test_listener.py
  </files>
  <read_first>
    - app/services/listener.py lines 335-363 — `get_active_senders` SELECT ai_context_id из senders (после миграции колонки нет).
    - app/services/listener.py lines 365-440 — `get_or_create_conversation` принимает `ai_context_id` параметром, INSERT'ит в conversations — НЕ трогать (D-05 conversations.ai_context_id остаётся).
    - app/services/listener.py lines 680-710 — блок SELECT `document_webhook_url FROM ai_contexts` — колонка дропнута, выпилить блок.
    - app/services/listener.py lines 770, 790-794 — `sender_info.get("ai_context_id")` использует dict.get() — после правки get_active_senders ключа просто не будет, dict.get вернёт None — это OK (D-05 fallthrough в "Нет ai_context_id" warning).
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Pitfall 5" — known regression в Phase 3 для входящих без campaign, TODO для Phase 4.
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"D-04" + §"D-05" — sender больше не "знает" агента; conversations.ai_context_id остаётся, но populate'ить его в Phase 3 нечем (Phase 4 через campaign.agent_id).
    - CLAUDE.md — НЕ ТРОГАТЬ debounce-логику.
  </read_first>
  <behavior>
    - Test 1 (test_listener::test_get_active_senders_no_ai_context_id): `await listener.get_active_senders()` возвращает list[dict] где КАЖДЫЙ dict НЕ содержит ключа "ai_context_id" — только id/slug/phone/session_string/proxy.
    - Не трогаем: debounce timer, message handler logic, отправка ответов через AI.
  </behavior>
  <action>
    В `app/services/listener.py`:

    1. **Lines 343-363**: заменить SELECT в `get_active_senders` на:
    ```python
    result = await session.execute(
        text("""
            SELECT id, slug, phone, session_string, proxy
            FROM senders
            WHERE role = 'sender'
              AND lifecycle_status = 'active'
              AND auth_status = 'ok'
        """)
    )
    rows = result.fetchall()
    return [
        {
            "id": str(r[0]),
            "slug": r[1],
            "phone": r[2],
            "session_string": r[3],
            # Phase 3 D-04: ai_context_id больше не на sender'е — agent_id придёт через
            # conversation.campaign_id JOIN в Phase 4.
            # TODO(phase-4): pull ai_context_id from conversation.campaign_id via JOIN.
            "proxy": r[4]
        }
        for r in rows
    ]
    ```

    2. **Lines 689-746**: заменить весь блок `document_webhook_url` (от `# Получаем webhook URL из контекста` до `else:\n                    logger.info(f"ℹ️ document_webhook_url не настроен...")`) на:
    ```python
                # Phase 3 D-01: document_webhook_url column dropped from ai_contexts.
                # Document-webhook feature пауза до Phase 4 (CAMP-14) где webhook
                # перейдёт на уровень кампании. В Phase 3 — no-op.
                # TODO(phase-4): pull document_webhook_url from conversation.campaign_id.
                logger.info(f"ℹ️ document_webhook (Phase 3): функция отключена до миграции на Campaign в Phase 4")
    ```

    Удалить весь блок с `document_webhook_url = None`, SELECT, и условие `if document_webhook_url:`. Оставить только log-message + downstream код (text сообщения, save_message и т.д.) продолжается с message_text = f"{document_info}..." что уже есть ниже.

    3. **Lines 770, 791**: `sender_info.get("ai_context_id")` — ОСТАВИТЬ КАК ЕСТЬ. После Task 4 ключа "ai_context_id" в sender_info dict нет — dict.get() вернёт None, и далее идёт fallthrough в `if not ai_context_id: logger.warning("⚠️ AI включён, но у sender {sender_info['slug']} нет контекста")`. Это known regression в Phase 3 (RESEARCH Pitfall 5) — нормальное поведение до Phase 4.

    4. **Lines 247-256** (где-то рядом с `_send_to_ai`) — добавить комментарий:
    ```python
    # TODO(phase-4): pull ai_context_id from conversation.campaign_id via JOIN.
    # В Phase 3 ai_context_id приходит из conversation.ai_context_id (D-05) — может быть NULL
    # для conversations созданных через listener без явного контекста.
    ```

    Заполнить `tests/test_listener.py`:
    ```python
    """Phase 3 — listener.get_active_senders adapter (RESEARCH Pitfall 5)."""
    import pytest

    pytestmark = pytest.mark.asyncio


    async def test_get_active_senders_no_ai_context_id(async_db_session, test_sender_factory):
        """Phase 3 D-04: get_active_senders больше не SELECT'ит ai_context_id из senders."""
        from app.services.listener import TelegramListener

        await test_sender_factory(slug="test-active-sender", lifecycle_status="active", auth_status="ok")

        listener = TelegramListener()
        senders = await listener.get_active_senders()

        assert isinstance(senders, list)
        assert len(senders) >= 1
        # Phase 3: every returned dict MUST NOT have 'ai_context_id' key
        for s in senders:
            assert "ai_context_id" not in s, \
                f"sender dict still has 'ai_context_id' key (got: {list(s.keys())})"
            # required keys still present
            assert "id" in s
            assert "slug" in s
            assert "session_string" in s
            assert "proxy" in s
    ```

    НЕ ТРОГАТЬ: `get_or_create_conversation` функцию (строки 365-440) — она принимает `ai_context_id` параметром (для будущих вызовов из send-flow с явным агентом). 

    НЕ ТРОГАТЬ: debounce timer, AI generate_response calls.
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_listener.py -x -v</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "SELECT.*ai_context_id.*FROM senders" app/services/listener.py` returns no matches.
    - `grep -n "ai_context_id" app/services/listener.py` does NOT match in the `get_active_senders` SELECT or in the returned dict comprehension (lines 335-365 area).
    - `grep -n "document_webhook_url" app/services/listener.py` returns no matches (the entire SELECT block + variable usage removed).
    - `grep -n "SELECT document_webhook_url FROM ai_contexts" app/services/listener.py` returns no matches.
    - File still contains `TelegramListener` class definition (not deleted).
    - File still contains `debounce` / buffered_message logic (not touched per CLAUDE.md).
    - `app/services/listener.py` contains the comment `TODO(phase-4): pull ai_context_id from conversation.campaign_id via JOIN`.
    - `pytest tests/test_listener.py::test_get_active_senders_no_ai_context_id -x` exits 0.
  </acceptance_criteria>
  <done>listener не падает на column does not exist после миграции 015. document_webhook_url-ветка — no-op до Phase 4. AI continues to work via conversation.ai_context_id (через send-flow, не через sender).</done>
</task>

<task type="auto" tdd="true">
  <name>Task 5: Адаптировать rotation._pick_best_sender — убрать WHERE s.ai_context_id = :ctx_id (workspace-only выбор)</name>
  <files>
    app/services/rotation.py
    tests/test_rotation.py
  </files>
  <read_first>
    - app/services/rotation.py lines 163-205 — `_pick_best_sender` использует WHERE s.ai_context_id = :ctx_id (после миграции колонки нет).
    - app/services/rotation.py lines 22-160 — `get_or_assign_sender` сохраняется (context_contact_assignments таблица остаётся per D-05).
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"D-05" — context_contact_assignments таблица остаётся.
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Runtime State Inventory" item (3) — стратегия: `_pick_best_sender` теперь ищет ВСЕХ активных sender'ов в workspace, без фильтра по ai_context_id.
  </read_first>
  <behavior>
    - Test 1 (test_rotation::test_pick_best_sender_workspace_only): `_pick_best_sender(db, context_id, workspace_id)` возвращает sender из правильного workspace, фильтрация по `role='sender' AND lifecycle_status='active' AND auth_status='ok'`. Sender из другого workspace — НЕ возвращается.
    - Test 2 (test_rotation::test_get_or_assign_sender_workspace_isolated): full integration — `get_or_assign_sender` создаёт assignment + возвращает sender в правильном workspace.
  </behavior>
  <action>
    В `app/services/rotation.py`:

    1. **Lines 181-198** в `_pick_best_sender` — заменить SQL на:
    ```python
    row = (await db.execute(
        text("""
            SELECT s.id
            FROM senders s
            LEFT JOIN message_queue mq
                ON mq.sender_id = s.id
                AND mq.status = 'sent'
                AND mq.finished_at >= NOW() - INTERVAL '24 hours'
            WHERE s.workspace_id = :wid
              AND s.lifecycle_status = 'active'
              AND s.auth_status = 'ok'
              AND s.role = 'sender'
            GROUP BY s.id, s.created_at
            ORDER BY COUNT(mq.id) ASC, s.created_at ASC
            LIMIT 1
        """),
        {"wid": str(workspace_id)},
    )).fetchone()
    ```

    Удалить параметр-связку `s.ai_context_id = :ctx_id` И параметр `"ctx_id": str(context_id)` из bindings.

    2. **Lines 163-180** — docstring `_pick_best_sender` обновить:
    ```python
    """
    Return the active sender within `workspace_id` with the fewest messages
    sent in the last 24 hours. Returns None if no active senders exist.

    Phase 3 D-04: sender больше не «знает» агента (senders.ai_context_id dropped) —
    выбор идёт по всему workspace pool. context_id параметр сохраняется в сигнатуре
    для обратной совместимости с get_or_assign_sender (который продолжает писать
    context_contact_assignments per D-05) — но в SQL filter больше не используется.

    TODO(phase-4): selection по campaign_id когда появится Campaign.sender_lock.
    """
    ```

    Сигнатура функции **остаётся** `(db, context_id, workspace_id)` — её вызывают из get_or_assign_sender. Параметр context_id просто не используется в SQL (но остаётся в сигнатуре, чтобы не ломать вызывающий код).

    3. НЕ ТРОГАТЬ: `get_or_assign_sender` (строки 22-160) — он продолжает работать с `context_contact_assignments` таблицей per D-05.

    Заполнить `tests/test_rotation.py`:
    ```python
    """Phase 3 — rotation._pick_best_sender adapter (Phase 3 D-04)."""
    import pytest
    from uuid import uuid4

    pytestmark = pytest.mark.asyncio


    async def test_pick_best_sender_workspace_only(async_db_session, test_sender_factory, test_workspace):
        """Phase 3: _pick_best_sender выбирает из workspace pool (не фильтрует по ai_context_id)."""
        from app.services.rotation import _pick_best_sender

        # Create active sender in test_workspace
        s_active = await test_sender_factory(slug="active-1", lifecycle_status="active", auth_status="ok")

        # Dummy context_id (no longer relevant for SQL, but parameter remains)
        dummy_ctx = uuid4()

        winner = await _pick_best_sender(async_db_session, dummy_ctx, test_workspace.id)
        assert winner is not None
        assert winner.id == s_active.id


    async def test_pick_best_sender_workspace_isolated(async_db_session, test_sender_factory, test_workspace):
        """Phase 3: senders из другого workspace не выбираются."""
        from app.services.rotation import _pick_best_sender
        from app.models import Workspace

        # Other workspace + sender there
        other_ws = Workspace(name="Other WS")
        async_db_session.add(other_ws)
        await async_db_session.commit()
        await async_db_session.refresh(other_ws)

        # No sender in test_workspace at all — only in other_ws
        from app.models import Sender
        other_sender = Sender(
            workspace_id=other_ws.id,
            slug="other-sender",
            name="Other",
            phone="+79999999999",
            session_string="stub",
            role="sender",
            auth_status="ok",
            lifecycle_status="active",
        )
        async_db_session.add(other_sender)
        await async_db_session.commit()

        dummy_ctx = uuid4()
        # Should return None — no senders in test_workspace
        result = await _pick_best_sender(async_db_session, dummy_ctx, test_workspace.id)
        assert result is None, "should not pick sender from other workspace"
    ```
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_rotation.py -x -v</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "s.ai_context_id" app/services/rotation.py` returns no matches.
    - `grep -n "WHERE s.ai_context_id" app/services/rotation.py` returns no matches.
    - `grep -n "ai_context_id" app/services/rotation.py` returns no matches in SQL text() blocks (may still appear in docstrings as historical comment).
    - The SQL in `_pick_best_sender` contains `WHERE s.workspace_id = :wid AND s.lifecycle_status = 'active' AND s.auth_status = 'ok' AND s.role = 'sender'`.
    - The function signature `async def _pick_best_sender(db, context_id, workspace_id)` is preserved (no broken callers).
    - `pytest tests/test_rotation.py::test_pick_best_sender_workspace_only -x` exits 0.
    - `pytest tests/test_rotation.py::test_pick_best_sender_workspace_isolated -x` exits 0.
  </acceptance_criteria>
  <done>Rotation работает workspace-only. Sender-pool в Phase 3 — весь workspace; Phase 4 добавит фильтр через campaign.sender_lock.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 6: Адаптировать queue.enqueue_message — принимать explicit ai_context_id параметром, INSERT в conversations</name>
  <files>
    app/services/queue.py
    tests/test_queue_enqueue.py
  </files>
  <read_first>
    - app/services/queue.py — найти функцию `enqueue_message` (export). Это публичная функция, используется в `app/routers/send.py`.
    - app/services/queue.py lines 680-708 — `_upsert_conversation_for_queue_item` использует `sender.ai_context_id` (после миграции колонки нет).
    - app/services/queue.py CLAUDE.md явный запрет — НЕ ТРОГАТЬ rate-limit логику.
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Runtime State Inventory" item (4) — стратегия: enqueue_message принимает explicit ai_context_id, INSERT в conversations использует его.
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"D-06" + §"C-07" — мелкая правка, не рерайт.
  </read_first>
  <behavior>
    - Test 1 (test_queue_enqueue::test_enqueue_with_explicit_ai_context_id): вызов `enqueue_message(db=..., workspace_id=..., sender_id=..., sender_slug=..., ai_context_id=<UUID>, recipient_phone=...)` записывает в `conversations.ai_context_id` переданное значение.
    - Test 2 (test_queue_enqueue::test_enqueue_without_ai_context_id_inserts_null): `ai_context_id=None` — INSERT с NULL в conversations.ai_context_id.
    - Не трогаем: rate-limit логику, queue worker loop, debounce.
  </behavior>
  <action>
    В `app/services/queue.py`:

    1. Найти определение `enqueue_message` (export). Расширить сигнатуру — **добавить параметр** `ai_context_id: Optional[UUID] = None` (после sender_slug, перед recipient_phone — или в keyword-only позицию). Пример:
    ```python
    async def enqueue_message(
        db: AsyncSession,
        workspace_id: UUID,
        sender_id: UUID,
        sender_slug: str,
        recipient_phone: str,
        recipient_name: Optional[str] = None,
        message_text: str = "",
        as_draft: bool = False,
        metadata: Optional[dict] = None,
        priority: int = 0,
        callback_url: Optional[str] = None,
        ai_context_id: Optional[UUID] = None,  # Phase 3: explicit agent (D-06)
    ) -> dict:
        ...
    ```

    Сохранить `ai_context_id` в `message_queue.extra_data` если функция уже использует extra_data, или передавать его дальше во внутренние функции через scope (планировщик может выбрать наиболее аккуратный путь — главное, чтобы значение дошло до `_upsert_conversation_for_queue_item`).

    Рекомендованный путь (минимум кода): хранить ai_context_id в `extra_data={"ai_context_id": str(ai_context_id) if ai_context_id else None, ...existing_metadata}`.

    2. **Lines 680-708** (`_upsert_conversation_for_queue_item` или эквивалент — функция содержит INSERT INTO conversations):
       Заменить `"ai_ctx": str(sender.ai_context_id) if sender.ai_context_id else None,` (line ~705) на:
       ```python
       "ai_ctx": item.extra_data.get("ai_context_id") if item.extra_data else None,
       ```
       Эта правка работает с item (MessageQueue ORM), читает ai_context_id из extra_data JSONB.

    3. НЕ ТРОГАТЬ: `MIN_SEND_INTERVAL`, `MAX_SEND_INTERVAL`, `LONG_PAUSE_*`, `FLOOD_HARD_THRESHOLD`, `MAX_NEW_CONTACTS_PER_HOUR`, `WORK_HOUR_*`, `_is_working_hours`, `_run`, `_tick` — все эти куски остаются как есть.

    4. `enqueue_file` — НЕ адаптировать в Phase 3 (он не задействован в send-flow Phase 3, остаётся legacy). Если в `enqueue_file` есть аналогичный INSERT — пометить TODO(phase-4): apply same ai_context_id propagation.

    Заполнить `tests/test_queue_enqueue.py`:
    ```python
    """Phase 3 — queue.enqueue_message принимает explicit ai_context_id (D-06)."""
    import pytest
    from sqlalchemy import text
    from uuid import uuid4

    pytestmark = pytest.mark.asyncio


    async def test_enqueue_with_explicit_ai_context_id(
        async_db_session, test_sender_factory, test_agent_factory
    ):
        """Phase 3: enqueue_message получает ai_context_id напрямую (не из sender.ai_context_id)."""
        from app.services.queue import enqueue_message

        sender = await test_sender_factory(slug="enq-test-1", lifecycle_status="active", auth_status="ok")
        agent = await test_agent_factory(name="Enqueue Agent")

        result = await enqueue_message(
            db=async_db_session,
            workspace_id=sender.workspace_id,
            sender_id=sender.id,
            sender_slug=sender.slug,
            recipient_phone="+79991234567",
            recipient_name="Test",
            message_text="hello",
            ai_context_id=agent.id,
        )
        assert "queue_id" in result

        # Проверяем что extra_data в message_queue содержит ai_context_id
        row = await async_db_session.execute(
            text("SELECT extra_data FROM message_queue WHERE id = :qid"),
            {"qid": result["queue_id"]}
        )
        extra = row.fetchone()[0]
        assert extra.get("ai_context_id") == str(agent.id)


    async def test_enqueue_without_ai_context_id(
        async_db_session, test_sender_factory
    ):
        """Phase 3: ai_context_id=None разрешено (fall-back на legacy callers)."""
        from app.services.queue import enqueue_message

        sender = await test_sender_factory(slug="enq-test-2", lifecycle_status="active", auth_status="ok")

        result = await enqueue_message(
            db=async_db_session,
            workspace_id=sender.workspace_id,
            sender_id=sender.id,
            sender_slug=sender.slug,
            recipient_phone="+79991234567",
            recipient_name="Test",
            message_text="hello",
            ai_context_id=None,
        )
        assert "queue_id" in result
    ```
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_queue_enqueue.py -x -v</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "sender.ai_context_id" app/services/queue.py` returns no matches.
    - Function `enqueue_message` signature contains parameter `ai_context_id: Optional[UUID] = None`.
    - Inside `_upsert_conversation_for_queue_item` (or wherever conversations INSERT happens), the binding `"ai_ctx"` is set from `item.extra_data.get("ai_context_id")` (NOT from `sender.ai_context_id`).
    - `grep -n "MIN_SEND_INTERVAL" app/services/queue.py` still matches — rate-limit constants NOT touched (CLAUDE.md).
    - `grep -n "WORK_HOUR_START" app/services/queue.py` still matches — work-hours NOT touched.
    - `pytest tests/test_queue_enqueue.py::test_enqueue_with_explicit_ai_context_id -x` exits 0.
    - `pytest tests/test_queue_enqueue.py::test_enqueue_without_ai_context_id -x` exits 0.
  </acceptance_criteria>
  <done>queue.enqueue_message принимает explicit ai_context_id; INSERT в conversations работает; rate-limit логика не тронута.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 7: Очистить senders router и schemas от ai_context_id (C-05, RESEARCH Pitfall 4)</name>
  <files>
    app/routers/senders.py
    app/schemas/__init__.py
    tests/test_senders.py
  </files>
  <read_first>
    - app/routers/senders.py lines 34 (`from sqlalchemy.orm import selectinload`), 96-97 (`ai_context_id`, `ai_context_name` в _sender_to_response), 157 (`selectinload(Sender.ai_context)`), 184, 231, 247, 329-330, 342, 438 — все упоминания ai_context_id и selectinload(Sender.ai_context).
    - app/schemas/__init__.py — найти SenderCreate, SenderUpdate, SenderResponse, проверить поля `ai_context_id` и `ai_context_name`.
    - .planning/phases/03-agents-ai-templates/03-CONTEXT.md §"C-05" — точные строки.
    - .planning/phases/03-agents-ai-templates/03-RESEARCH.md §"Pitfall 4" — Pydantic `from_attributes=True` упадёт на отсутствие attribute, если не убрать поле из Response schema.
  </read_first>
  <behavior>
    - Test 1 (test_senders::test_response_has_no_ai_context_id): GET /api/v1/senders/{slug} возвращает SenderResponse без поля `ai_context_id` и `ai_context_name`.
    - Test 2 (test_senders::test_create_sender_no_ai_context_id_field): POST /api/v1/senders с body, содержащим `ai_context_id`, **либо** игнорирует поле (Pydantic v2 default), **либо** возвращает 422 (если schema strict). Главное — sender создаётся успешно.
  </behavior>
  <action>
    1. В `app/schemas/__init__.py` — найти классы `SenderCreate`, `SenderUpdate`, `SenderResponse`. **Удалить** поля:
       - `ai_context_id: Optional[UUID] = ...` (из всех трёх схем)
       - `ai_context_name: Optional[str] = ...` (из `SenderResponse` если есть)

    2. В `app/routers/senders.py`:

       a) **Line 34**: оставить `from sqlalchemy.orm import selectinload` (используется ещё где-то? проверить grep — если ТОЛЬКО для Sender.ai_context, удалить импорт. Если в файле есть `selectinload(...)` для других relationship — оставить).

       b) **Lines 96-97** в `_sender_to_response`: удалить:
       ```python
       ai_context_id=sender.ai_context_id,
       ai_context_name=sender.ai_context.name if sender.ai_context else None,
       ```

       c) **Line 157** в `_load_sender_by_slug`: удалить `.options(selectinload(Sender.ai_context))`. Заменить `select(Sender).options(...).where(...)` на `select(Sender).where(...)`.

       d) **Line 184** в `list_senders`: удалить `.options(selectinload(Sender.ai_context))`.

       e) **Lines 231, 247, 329-330, 342, 438** — удалить:
       - Любые `request.ai_context_id` reads в create_sender, update_sender.
       - `selectinload(Sender.ai_context)` calls в `select(Sender).options(...).where(Sender.id == sender.id)`.
       - В create_sender — удалить `ai_context_id=request.ai_context_id,` из конструктора Sender(...).
       - В update_sender — удалить блок:
         ```python
         if request.ai_context_id is not None:
             sender.ai_context_id = request.ai_context_id
         ```

    3. Дополнить `tests/test_senders.py`:
    ```python
    """Phase 3 — senders router + schemas cleanup (C-05, Pitfall 4)."""
    import pytest

    pytestmark = pytest.mark.asyncio


    async def test_response_has_no_ai_context_id(
        async_client, valid_supabase_jwt, test_sender_factory
    ):
        """Phase 3 C-05: SenderResponse больше не содержит ai_context_id / ai_context_name."""
        sender = await test_sender_factory(slug="phase3-test", lifecycle_status="active", auth_status="ok")

        token = valid_supabase_jwt(sub=f"user-{sender.workspace_id}")
        # NB: AuthDep auto-resolves workspace from JWT 'sub' via user_workspaces.
        # Test relies on existing Phase 1 lazy-create — but here we need explicit user link.
        # Simpler: use X-Workspace-Key path if available, OR pass through valid_supabase_jwt + accept lazy-create
        # (which will give a fresh workspace different from test_workspace — sender will be 404).
        # For Phase 3 we test the schema shape only — do a direct GET via lazy-created workspace
        # by inserting via API:
        # Skip if Phase 1 lazy-create makes this test impractical — use direct schema check instead:
        from app.routers.senders import _sender_to_response

        response = _sender_to_response(sender)
        # Pydantic v2: model_dump shouldn't include dropped fields
        dump = response.model_dump()
        assert "ai_context_id" not in dump, f"ai_context_id leaked into SenderResponse: {dump}"
        assert "ai_context_name" not in dump, f"ai_context_name leaked into SenderResponse: {dump}"
    ```
  </action>
  <verify>
    <automated>cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_senders.py::test_response_has_no_ai_context_id -x -v</automated>
  </verify>
  <acceptance_criteria>
    - `grep -n "ai_context_id" app/schemas/__init__.py` returns no matches inside `SenderCreate`, `SenderUpdate`, or `SenderResponse` class definitions.
    - `grep -n "ai_context_name" app/schemas/__init__.py` returns no matches.
    - `grep -n "selectinload(Sender.ai_context)" app/routers/senders.py` returns no matches.
    - `grep -n "sender.ai_context_id" app/routers/senders.py` returns no matches.
    - `grep -n "sender.ai_context" app/routers/senders.py` returns no matches.
    - `grep -n "request.ai_context_id" app/routers/senders.py` returns no matches.
    - `pytest tests/test_senders.py::test_response_has_no_ai_context_id -x` exits 0.
    - Existing Phase 2 senders tests still pass: `pytest tests/test_senders.py -x` exits 0.
  </acceptance_criteria>
  <done>Senders schemas + router очищены; SenderResponse не падает на `AttributeError: 'Sender' object has no attribute 'ai_context_id'`; Phase 2 функциональность сохранена.</done>
</task>

</tasks>

<verification>
**Per-task automated commands** (run after each task):
- Task 1: `pytest tests/test_migration_015.py -x -v` (4 tests pass)
- Task 2: `python -c "from app.models import AIContext, Sender; ..."` smoke + `pytest tests/test_migration_015.py -x` re-run
- Task 3: `pytest tests/test_ai_engine.py -x -v` (2 tests pass)
- Task 4: `pytest tests/test_listener.py -x -v` (1 test pass)
- Task 5: `pytest tests/test_rotation.py -x -v` (2 tests pass)
- Task 6: `pytest tests/test_queue_enqueue.py -x -v` (2 tests pass)
- Task 7: `pytest tests/test_senders.py::test_response_has_no_ai_context_id -x -v` (1 test pass)

**Plan-level verification** (run at end):
```bash
pytest tests/test_migration_015.py tests/test_ai_engine.py tests/test_listener.py tests/test_rotation.py tests/test_queue_enqueue.py tests/test_senders.py -x -v
```
All tests pass, ~30 sec.

**Goal-backward truths verification:**
- Run `psql -c "SELECT column_name FROM information_schema.columns WHERE table_name='ai_contexts'"` in test DB → доказать дропнутые поля отсутствуют.
- Run `psql -c "SELECT indexname FROM pg_indexes WHERE tablename='ai_contexts'"` → доказать `idx_ai_contexts_workspace_name` есть.
- Run `grep -r "is_active" app/services/ai_engine.py app/services/listener.py app/services/rotation.py app/services/queue.py` → 0 совпадений в SQL запросах к ai_contexts/senders.
</verification>

<success_criteria>
- [ ] migrations/015_phase3.sql существует и применяется в conftest._setup_database
- [ ] ORM AIContext — без max_message_length/response_delay_seconds/auto_pause_triggers/is_active/senders relationship
- [ ] ORM Sender — без ai_context_id Column + ai_context relationship
- [ ] ai_engine.get_context — SELECT без is_active/max_message_length/webhook_functions; defaults для legacy callers
- [ ] listener.get_active_senders — без SELECT ai_context_id; document_webhook_url block — no-op
- [ ] rotation._pick_best_sender — workspace-only фильтр, без WHERE s.ai_context_id
- [ ] queue.enqueue_message — принимает explicit ai_context_id параметром, INSERT в conversations использует extra_data
- [ ] senders.py + schemas очищены от ai_context_id поля и selectinload(Sender.ai_context)
- [ ] All 12 tests pass (`pytest tests/test_migration_015.py tests/test_ai_engine.py tests/test_listener.py tests/test_rotation.py tests/test_queue_enqueue.py tests/test_senders.py -x`)
- [ ] CLAUDE.md constraints respected: rate-limit / debounce / FloodWait retry не тронуты
- [ ] requirements addressed: AGNT-02 (поля агента — Sender больше не имеет shape агента), AGNT-03 (sender decoupled от агента)
</success_criteria>

<output>
After completion, create `.planning/phases/03-agents-ai-templates/03-01-SUMMARY.md` per template, with sections:
- What was built (migration 015 + ORM cleanup + 5 worker adapters + senders cleanup)
- Files modified (full list with line counts)
- Test coverage (12 Wave-0 tests passing)
- Carry-overs for plan 03-02 (test_agent_factory ready, AIContext model clean, senders.ai_context_id absent — plan 02 builds CRUD on top)
- TODOs left for Phase 4 (5 sites with `TODO(phase-4):` markers in code)
</output>
