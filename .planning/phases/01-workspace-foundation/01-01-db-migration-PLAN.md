---
phase: 01-workspace-foundation
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/012_workspace.sql
  - app/models/__init__.py
  - docker-compose.yml
autonomous: true
requirements:
  - TENT-01
  - TENT-04
user_setup: []
must_haves:
  truths:
    - "Миграция 012 применяется на пустой БД одной транзакцией без ошибок"
    - "Все 11 tenant-scoped таблиц получают NOT NULL workspace_id UUID FK на workspaces.id с ON DELETE CASCADE"
    - "Созданы 3 новые таблицы: workspaces, user_workspaces, workspace_api_keys"
    - "user_workspaces.role имеет CHECK constraint IN ('owner','admin','member')"
    - "workspace_api_keys имеет partial индекс по prefix WHERE revoked_at IS NULL"
    - "Docker-контейнеры переименованы в outreach-platform-{db,api,listener} — не убивает прод telegram-api"
    - "ORM-модели в app/models/__init__.py синхронны с SQL-схемой (Pitfall 4)"
  artifacts:
    - path: "migrations/012_workspace.sql"
      provides: "Raw SQL миграция: 3 новые таблицы + ALTER на 11 существующих в одной транзакции"
      contains: "BEGIN; ... COMMIT;"
    - path: "app/models/__init__.py"
      provides: "ORM-классы Workspace, UserWorkspace, WorkspaceApiKey + workspace_id Column на 11 моделей"
      contains: "class Workspace"
    - path: "docker-compose.yml"
      provides: "Переименованные container_name и service-имена"
      contains: "container_name: outreach-platform-db"
  key_links:
    - from: "migrations/012_workspace.sql"
      to: "app/models/__init__.py"
      via: "синхронность колонок (Pitfall 4): каждая ALTER ADD COLUMN имеет соответствующий Column в ORM"
      pattern: "workspace_id = Column\\(UUID"
    - from: "app/models/__init__.py"
      to: "workspaces table"
      via: "ForeignKey('workspaces.id', ondelete='CASCADE') на 11 моделях"
      pattern: "ForeignKey\\(\"workspaces\\.id\""
---

<objective>
План закладывает мультитенантный DB-фундамент: миграция 012 создаёт 3 новые таблицы (workspaces, user_workspaces, workspace_api_keys), добавляет `workspace_id UUID NOT NULL` FK на все 11 tenant-scoped таблиц (D-02, D-03), параллельно обновляются ORM-модели в `app/models/__init__.py` (синхронность обязательна — Pitfall 4 из RESEARCH.md). Также переименовываются Docker-контейнеры (D-18), чтобы запуск outreach-platform на VPS не убивал прод telegram-api.

Purpose: Без миграции и ORM-классов остальные планы (01-02 auth_dep, 01-03 workspace router) не имеют куда писать данные. Это фундамент Phase 1.
Output: Применённая миграция на пустой БД, обновлённый barrel ORM, переименованный docker-compose.
</objective>

<execution_context>
@/Users/andrewbruce/Documents/outreach-platform/CLAUDE.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/codebase/CONVENTIONS.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/codebase/STRUCTURE.md
</execution_context>

<context>
@/Users/andrewbruce/Documents/outreach-platform/.planning/PROJECT.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/ROADMAP.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/STATE.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/REQUIREMENTS.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-CONTEXT.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-RESEARCH.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-PATTERNS.md

# Канонические аналоги (читать перед изменением)
@/Users/andrewbruce/Documents/outreach-platform/migrations/005_warmup.sql
@/Users/andrewbruce/Documents/outreach-platform/migrations/010_missing_indexes.sql
@/Users/andrewbruce/Documents/outreach-platform/migrations/011_sender_auth_status.sql
@/Users/andrewbruce/Documents/outreach-platform/migrations/003_add_sender_role.sql
@/Users/andrewbruce/Documents/outreach-platform/app/models/__init__.py
@/Users/andrewbruce/Documents/outreach-platform/docker-compose.yml

<interfaces>
<!-- Список 11 tenant-scoped таблиц (из RESEARCH §Tenant-Scoped Tables Inventory) — должны попасть в миграцию -->
<!-- senders, messages_log, contacts_cache, ai_contexts, message_queue, conversations, -->
<!-- warmup_pool, warmup_sessions, warmup_messages, proxy_pool, context_contact_assignments -->

<!-- Канонический паттерн ORM-модели (из app/models/__init__.py:29-48, Sender): -->
class Sender(Base):
    __tablename__ = "senders"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # workspace_id будет добавлен сразу после id
    ...

<!-- Канонический паттерн миграции (из 005_warmup.sql): -->
BEGIN;
CREATE TABLE IF NOT EXISTS foo (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ...
);
COMMIT;
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Создать миграцию 012_workspace.sql</name>
  <files>migrations/012_workspace.sql</files>
  <read_first>
    - /Users/andrewbruce/Documents/outreach-platform/CLAUDE.md (раздел "Архитектурные правила" — raw SQL миграции, никогда Alembic, идемпотентность через IF NOT EXISTS)
    - /Users/andrewbruce/Documents/outreach-platform/migrations/005_warmup.sql (canonical pattern: BEGIN/COMMIT + CREATE TABLE IF NOT EXISTS + UUID PK + FK CASCADE + индексы в одной транзакции)
    - /Users/andrewbruce/Documents/outreach-platform/migrations/010_missing_indexes.sql (canonical pattern: CREATE INDEX IF NOT EXISTS, partial WHERE)
    - /Users/andrewbruce/Documents/outreach-platform/migrations/011_sender_auth_status.sql (canonical pattern: ALTER TABLE ADD COLUMN IF NOT EXISTS VARCHAR NOT NULL DEFAULT)
    - /Users/andrewbruce/Documents/outreach-platform/migrations/003_add_sender_role.sql (anti-pattern: VARCHAR role БЕЗ CHECK — не повторять! Для user_workspaces.role обязателен CHECK constraint)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-CONTEXT.md (D-02: одна транзакция, без backfill; D-03: warmup/proxy тоже scoped; D-10: NO UNIQUE на supabase_user_id)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-RESEARCH.md (§Tenant-Scoped Tables Inventory — список 11 таблиц; §Architecture Patterns Pattern 1 — skeleton миграции; Pitfall 5 — race condition)
    - /Users/andrewbruce/Documents/outreach-platform/app/models/__init__.py (текущие определения 11 таблиц — для имён колонок)
  </read_first>
  <action>
Создать файл `migrations/012_workspace.sql` со следующим содержимым (всё внутри ОДНОГО `BEGIN; ... COMMIT;` блока — D-02).

**Раздел 1 — три новые таблицы:**

```sql
-- migrations/012_workspace.sql
-- Phase 1: multi-tenant foundation
-- Creates workspaces, user_workspaces, workspace_api_keys + adds workspace_id FK
-- to all 11 tenant-scoped tables in a single transaction (D-02).
-- БД должна быть пустой (D-01). Все операторы идемпотентны (IF NOT EXISTS / IF EXISTS).

BEGIN;

-- ── 1. Root tenant table ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS workspaces (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 2. user_workspaces (many-to-many; D-10 — NO UNIQUE на supabase_user_id) ─
CREATE TABLE IF NOT EXISTS user_workspaces (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supabase_user_id    TEXT NOT NULL,
    workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    role                VARCHAR(20) NOT NULL DEFAULT 'owner',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT user_workspaces_role_check
        CHECK (role IN ('owner', 'admin', 'member'))
);

CREATE INDEX IF NOT EXISTS idx_user_workspaces_supabase_user_id
    ON user_workspaces(supabase_user_id);

CREATE INDEX IF NOT EXISTS idx_user_workspaces_workspace_id
    ON user_workspaces(workspace_id);

-- ── 3. workspace_api_keys ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS workspace_api_keys (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    prefix        VARCHAR(12) NOT NULL,
    bcrypt_hash   TEXT NOT NULL,
    name          VARCHAR(50) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);

-- Partial index: только активные ключи участвуют в lookup (C-02 resolved)
CREATE INDEX IF NOT EXISTS idx_workspace_api_keys_prefix_active
    ON workspace_api_keys(prefix)
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_workspace_api_keys_workspace_id
    ON workspace_api_keys(workspace_id);
```

**Раздел 2 — ALTER на 11 tenant-scoped таблицах (D-03 включая proxy_pool и warmup_*):**

Для КАЖДОЙ из 11 таблиц добавить блок:

```sql
ALTER TABLE <table>
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_<table>_workspace
    ON <table>(workspace_id);
```

Список 11 таблиц (исчерпывающий, из RESEARCH §Tenant-Scoped Tables Inventory — точные имена `__tablename__`):
1. `senders`
2. `messages_log`
3. `contacts_cache`
4. `ai_contexts`
5. `message_queue`
6. `conversations`
7. `warmup_pool`
8. `warmup_sessions`
9. `warmup_messages`
10. `proxy_pool`
11. `context_contact_assignments`

Имя индекса — `idx_<full_table_name>_workspace` snake_case (CONVENTIONS.md §Naming Patterns + аналог `010_missing_indexes.sql`).

Завершить файл `COMMIT;` + пустая строка.

**Критично — следовать паттернам:**
- `gen_random_uuid()` (Postgres 13+), НЕ `uuid_generate_v4()` (Pitfall в RESEARCH).
- `TIMESTAMPTZ` (не `TIMESTAMP`) — соответствует `DateTime(timezone=True)` в ORM.
- `ON DELETE CASCADE` на все FK к `workspaces` — удалил workspace, удалились все его данные.
- Никаких `messages` таблицы (Pitfall 7 — она в migration 001 как legacy, в ORM нет, не трогаем).
- БД пустая (D-01), поэтому `NOT NULL` без `DEFAULT` безопасен — в существующих строк нет.

**Что НЕ делать:**
- НЕ создавать UNIQUE constraint на `user_workspaces.supabase_user_id` (D-10).
- НЕ использовать SQLEnum / CREATE TYPE для role — только VARCHAR + CHECK (Open Question §3 в RESEARCH).
- НЕ ставить composite индексы типа `(workspace_id, sender_id, status)` в этой миграции — это задача Phase 2-4 (RESEARCH §Composite Indexes — только базовые `idx_<table>_workspace`).
  </action>
  <verify>
    <automated>
test -f migrations/012_workspace.sql && \
grep -c "^BEGIN;" migrations/012_workspace.sql | grep -q "^1$" && \
grep -c "^COMMIT;" migrations/012_workspace.sql | grep -q "^1$" && \
grep -q "CREATE TABLE IF NOT EXISTS workspaces" migrations/012_workspace.sql && \
grep -q "CREATE TABLE IF NOT EXISTS user_workspaces" migrations/012_workspace.sql && \
grep -q "CREATE TABLE IF NOT EXISTS workspace_api_keys" migrations/012_workspace.sql && \
grep -q "user_workspaces_role_check" migrations/012_workspace.sql && \
grep -q "WHERE revoked_at IS NULL" migrations/012_workspace.sql && \
[ "$(grep -c 'ADD COLUMN IF NOT EXISTS workspace_id' migrations/012_workspace.sql)" = "11" ] && \
for t in senders messages_log contacts_cache ai_contexts message_queue conversations warmup_pool warmup_sessions warmup_messages proxy_pool context_contact_assignments; do
  grep -q "ALTER TABLE $t" migrations/012_workspace.sql || { echo "MISSING: $t"; exit 1; }
done
    </automated>
  </verify>
  <acceptance_criteria>
- Файл `migrations/012_workspace.sql` существует
- Содержит ровно один `BEGIN;` и один `COMMIT;` (одна транзакция, D-02)
- Содержит `CREATE TABLE IF NOT EXISTS workspaces`, `CREATE TABLE IF NOT EXISTS user_workspaces`, `CREATE TABLE IF NOT EXISTS workspace_api_keys`
- Содержит `CONSTRAINT user_workspaces_role_check CHECK (role IN ('owner', 'admin', 'member'))` (Pitfall: не повторять anti-pattern senders.role без CHECK)
- Содержит partial индекс `WHERE revoked_at IS NULL` для workspace_api_keys.prefix
- НЕ содержит UNIQUE на `supabase_user_id` (`grep "UNIQUE.*supabase_user_id" migrations/012_workspace.sql` пусто) — D-10
- НЕ содержит `uuid_generate_v4()` — только `gen_random_uuid()`
- Содержит ровно 11 строк `ADD COLUMN IF NOT EXISTS workspace_id`
- Для каждой из 11 таблиц (`senders`, `messages_log`, `contacts_cache`, `ai_contexts`, `message_queue`, `conversations`, `warmup_pool`, `warmup_sessions`, `warmup_messages`, `proxy_pool`, `context_contact_assignments`) присутствует `ALTER TABLE <table>`
- Каждый ALTER имеет соответствующий `CREATE INDEX IF NOT EXISTS idx_<table>_workspace ON <table>(workspace_id)`
- НЕ упоминается таблица `messages` (Pitfall 7)
- НЕ упоминается composite index типа `(workspace_id, sender_id, ...)` (отложено в Phase 2+)
  </acceptance_criteria>
  <done>
Миграция 012 готова к применению на пустой БД одной транзакцией; покрывает 3 новые таблицы + 11 ALTER + 13+ индексов; все паттерны идемпотентны.
  </done>
</task>

<task type="auto">
  <name>Task 2: Добавить ORM-модели и workspace_id Column на 11 моделей</name>
  <files>app/models/__init__.py</files>
  <read_first>
    - /Users/andrewbruce/Documents/outreach-platform/app/models/__init__.py (текущая структура: импорты строки 1-7, Sender строки 29-48, AIContext строки 87-106, WarmupPool строки 179-189 — канонические паттерны)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-PATTERNS.md (§2 — Pattern B/C/D для новых моделей; Shared 6 — UUID PK; Shared 7 — section dividers)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-RESEARCH.md (Pitfall 4 — синхронность миграции и ORM критична; §Architecture Patterns Pattern 3)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/codebase/CONVENTIONS.md (§Naming Patterns — PascalCase для моделей; §Module Design — barrel в __init__.py)
    - /Users/andrewbruce/Documents/outreach-platform/migrations/012_workspace.sql (только что созданный — для синхронности колонок)
  </read_first>
  <action>
Изменить `app/models/__init__.py` в двух частях.

**Часть A — добавить 3 новые модели в начало файла (после импортов, до class Sender)**

Добавить section divider и 3 класса:

```python
# ─── Multi-tenant foundation (Phase 1 — TENT-01..04) ─────────────────────────

class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserWorkspace(Base):
    __tablename__ = "user_workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supabase_user_id = Column(Text, nullable=False, index=True)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    role = Column(String(20), nullable=False, server_default="owner")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    workspace = relationship("Workspace")


class WorkspaceApiKey(Base):
    __tablename__ = "workspace_api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    prefix = Column(String(12), nullable=False)
    bcrypt_hash = Column(Text, nullable=False)
    name = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    workspace = relationship("Workspace")
```

Конвенции (из PATTERNS.md):
- `Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)` — UUID PK с Python-default
- `Column(DateTime(timezone=True), server_default=func.now())` — server-side timestamp
- `Column(String(N), nullable=False, server_default="value")` — VARCHAR с дефолтом
- `relationship("Workspace")` — barebones relationship (без back_populates — не нужно для Phase 1)
- НЕ использовать `model_config = ConfigDict(...)` — barebones BaseModel везде в репо

**Часть B — добавить `workspace_id` Column на каждую из 11 tenant-scoped моделей**

В КАЖДОМ классе из списка ниже добавить ОДНУ строку **сразу после `id = Column(...)` поля** (если есть `slug` сразу после id, ставить ПЕРЕД slug — слой workspace_id важнее):

```python
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
```

Список 11 классов и их `__tablename__` (имена классов согласно текущему файлу):
1. `Sender` → `senders`
2. `MessageLog` → `messages_log`
3. `ContactCache` → `contacts_cache`
4. `AIContext` → `ai_contexts`
5. `MessageQueue` → `message_queue`
6. `Conversation` → `conversations`
7. `WarmupPool` → `warmup_pool`
8. `WarmupSession` → `warmup_sessions`
9. `WarmupMessage` → `warmup_messages`
10. `ProxyPool` → `proxy_pool`
11. `ContextContactAssignment` → `context_contact_assignments`

**Критично (Pitfall 4 из RESEARCH):** колонка должна точно соответствовать SQL из миграции 012 — `UUID NOT NULL` с FK на `workspaces.id ON DELETE CASCADE`. Любое расхождение (например забыть `nullable=False`) даст dirvergence между ORM и БД.

**Что НЕ делать:**
- НЕ добавлять `relationship("Workspace")` в существующие 11 моделей в Phase 1 — это потенциально шумит circular imports и не нужно для скоупа Phase 1 (D-15 — services не трогаем).
- НЕ менять существующие колонки (slug, name и т.д.) — только добавить одну строку.
- НЕ удалять и не переименовывать существующие классы.

**Проверка импортов:** Убедиться, что `Text`, `ForeignKey`, `UUID`, `relationship`, `func` уже импортированы в шапке (строки 1-7) — если нет, добавить (но они ВСЕ уже там, см. строки PATTERNS.md §2 Pattern A).
  </action>
  <verify>
    <automated>
python3 -c "from app.models import Workspace, UserWorkspace, WorkspaceApiKey, Sender, MessageLog, ContactCache, AIContext, MessageQueue, Conversation, WarmupPool, WarmupSession, WarmupMessage, ProxyPool, ContextContactAssignment; assert hasattr(Workspace, '__tablename__') and Workspace.__tablename__ == 'workspaces'; assert hasattr(UserWorkspace, '__tablename__') and UserWorkspace.__tablename__ == 'user_workspaces'; assert hasattr(WorkspaceApiKey, '__tablename__') and WorkspaceApiKey.__tablename__ == 'workspace_api_keys'; tenant_models = [Sender, MessageLog, ContactCache, AIContext, MessageQueue, Conversation, WarmupPool, WarmupSession, WarmupMessage, ProxyPool, ContextContactAssignment]; assert all(hasattr(m, 'workspace_id') for m in tenant_models), [m.__name__ for m in tenant_models if not hasattr(m, 'workspace_id')]; assert all(m.workspace_id.nullable is False for m in tenant_models)" 2>&1
    </automated>
  </verify>
  <acceptance_criteria>
- `app/models/__init__.py` содержит `class Workspace(Base):` с `__tablename__ = "workspaces"`
- Содержит `class UserWorkspace(Base):` с `__tablename__ = "user_workspaces"` и `role = Column(String(20), nullable=False, server_default="owner")`
- Содержит `class WorkspaceApiKey(Base):` с `__tablename__ = "workspace_api_keys"` и полями `prefix`, `bcrypt_hash`, `revoked_at`
- Каждый из 11 классов (`Sender`, `MessageLog`, `ContactCache`, `AIContext`, `MessageQueue`, `Conversation`, `WarmupPool`, `WarmupSession`, `WarmupMessage`, `ProxyPool`, `ContextContactAssignment`) имеет атрибут `workspace_id` — `grep -c "workspace_id = Column(UUID" app/models/__init__.py` ≥ 11 (11 в существующих моделях + дубли FK в новых моделях — итого ≥ 13)
- Python импорт работает: `python3 -c "from app.models import Workspace, UserWorkspace, WorkspaceApiKey"` exit 0
- Все 11 `workspace_id` колонок имеют `nullable=False` (синхронность с SQL NOT NULL — Pitfall 4)
- Все 11 `workspace_id` колонок имеют `ForeignKey("workspaces.id", ondelete="CASCADE")`
- Не сломан импорт остальных моделей: `python3 -c "from app.models import Sender, MessageLog, ContactCache, AIContext, MessageQueue, Conversation, WarmupPool, WarmupSession, WarmupMessage, ProxyPool, ContextContactAssignment"` exit 0
- НЕ добавлен `relationship("Workspace")` в существующие 11 моделей (избегаем circular imports — `grep -c 'relationship("Workspace")' app/models/__init__.py` == 2, только в UserWorkspace и WorkspaceApiKey)
- Все 11 FK-определений `workspace_id` в `app/models/__init__.py` содержат `ondelete="CASCADE"` (синхронность с миграцией 012, защита от Pitfall 4 — orphan rows при удалении workspace). Команда проверки: `grep -c 'ondelete="CASCADE"' app/models/__init__.py` >= 11 (B-5)
  </acceptance_criteria>
  <done>
ORM-модели синхронны с миграцией 012; импорт работает; все 11 моделей имеют NOT NULL workspace_id FK; 3 новые модели заиндексированы в barrel.
  </done>
</task>

<task type="auto">
  <name>Task 3: Переименовать docker-контейнеры outreach-platform-{db,api,listener}</name>
  <files>docker-compose.yml</files>
  <read_first>
    - /Users/andrewbruce/Documents/outreach-platform/docker-compose.yml (текущее состояние — найти service-ключи и container_name)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-CONTEXT.md (D-18: ОБЯЗАТЕЛЬНО переименовать, иначе деплой убъёт прод telegram-api)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-RESEARCH.md (§Runtime State Inventory — critical: коллизия имён убъёт прод; листенер env API_KEY оставить, supabase env vars пока НЕ добавлять — это в 01-03)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-PATTERNS.md (§8 Pattern A — rename map)
  </read_first>
  <action>
Изменить `docker-compose.yml` — только переименование, без добавления supabase env vars (это в 01-03 task 3, чтобы избежать конфликта merge).

**Переименовать `container_name` для трёх сервисов (D-18):**

| Что | Было | Стало |
|-----|------|-------|
| db сервис | `container_name: telegram-api-db` | `container_name: outreach-platform-db` |
| api сервис | `container_name: telegram-api` | `container_name: outreach-platform-api` |
| listener сервис | `container_name: telegram-listener` | `container_name: outreach-platform-listener` |

**Также переименовать service-ключи (`services:` верхнего уровня) для консистентности — D-18:**

Если в файле сервисы названы `db:`, `api:`, `listener:` — оставить как есть (это короткие имена внутри compose-сети, не конфликтуют с прод). Только `container_name` (которое влияет на `docker ps` глобально) обязательно меняем.

**DB credentials (discretion) — переименовать для чистоты (см. PATTERNS.md §8):**
- `postgres_user: telegram_user` → `postgres_user: outreach_user`
- `postgres_password: telegram_secure_pass_2025` → `postgres_password: outreach_secure_pass_2026`
- `postgres_db: telegram_followup` → `postgres_db: outreach_platform`
- Обновить также строку `DATABASE_URL: postgresql+asyncpg://...` во ВСЕХ environment-секциях (api и listener) — должны указывать на новые user/pass/db.

**Что НЕ делать:**
- НЕ добавлять `SUPABASE_JWT_SECRET`, `SUPABASE_URL`, `CORS_ALLOWED_ORIGINS` — это в плане 01-03 task "config + env" (избегаем cross-plan конфликта).
- НЕ удалять `API_KEY: ${API_KEY}` из listener секции — listener использует его в Phase 1 (D-15 services не трогаем).
- НЕ менять `Dockerfile`, `Dockerfile.listener`, network-настройки, volumes.
- НЕ удалять `API_KEY: ${API_KEY}` из api секции — это снова делается в 01-03 (там же удаляется `api_key` из config.py).
  </action>
  <verify>
    <automated>
grep -q "container_name: outreach-platform-db" docker-compose.yml && \
grep -q "container_name: outreach-platform-api" docker-compose.yml && \
grep -q "container_name: outreach-platform-listener" docker-compose.yml && \
! grep -q "container_name: telegram-api-db" docker-compose.yml && \
! grep -q "container_name: telegram-api$" docker-compose.yml && \
! grep -q "container_name: telegram-listener" docker-compose.yml && \
grep -q "outreach_user" docker-compose.yml && \
grep -q "outreach_platform" docker-compose.yml && \
docker compose config -q 2>&1
    </automated>
  </verify>
  <acceptance_criteria>
- `grep "container_name:" docker-compose.yml` показывает ровно 3 строки, все начинаются с `outreach-platform-`
- НЕТ ни одной строки `container_name: telegram-*` (старые имена убраны полностью)
- DB credentials переименованы: `outreach_user`, `outreach_secure_pass_2026`, `outreach_platform` присутствуют
- `DATABASE_URL` в секции api и listener указывает на `outreach_user@db:5432/outreach_platform`
- `API_KEY: ${API_KEY}` сохранён в листенер секции (НЕ удалён)
- `docker compose config -q` exit 0 (YAML валиден)
- НЕ добавлены пока `SUPABASE_JWT_SECRET`, `SUPABASE_URL`, `CORS_ALLOWED_ORIGINS` (это в 01-03)
  </acceptance_criteria>
  <done>
Docker-контейнеры переименованы; запуск `docker compose up -d` на VPS создаст контейнеры с новыми именами, не убивая прод telegram-api.
  </done>
</task>

</tasks>

<verification>
**Phase-уровневая верификация после всех 3 задач плана:**

1. SQL миграция применима к чистому Postgres-контейнеру:
   ```bash
   docker compose up -d db
   docker compose exec -T db psql -U outreach_user -d outreach_platform -f /dev/stdin < migrations/012_workspace.sql
   # Должен пройти без ошибок (БД пустая на этом этапе → CREATE TABLE отработает; ALTER упадёт на отсутствующих таблицах — это OK для smoke на чистой БД. Полный E2E прогон в 01-02 после Base.metadata.create_all + 012.)
   ```

2. ORM импорт работает: `python3 -c "from app.models import Workspace, UserWorkspace, WorkspaceApiKey"` exit 0

3. Docker compose валиден: `docker compose config -q` exit 0

**Замечание:** Полная E2E-проверка миграции (init_db создаёт ORM-таблицы → 012 ALTER-ит) делается в плане 01-02 task "smoke миграция через pytest". Здесь только static-валидация.
</verification>

<success_criteria>
- [ ] `migrations/012_workspace.sql` создан, одна транзакция, идемпотентен
- [ ] 3 новые таблицы (workspaces, user_workspaces, workspace_api_keys) определены в миграции и ORM
- [ ] 11 tenant-scoped таблиц получили workspace_id NOT NULL FK (SQL + ORM синхронно — Pitfall 4)
- [ ] CHECK constraint на user_workspaces.role (без anti-pattern senders.role)
- [ ] Partial индекс на workspace_api_keys.prefix WHERE revoked_at IS NULL (C-02)
- [ ] НЕТ UNIQUE на supabase_user_id (D-10)
- [ ] Docker-контейнеры переименованы — не убивает прод telegram-api
- [ ] python3 -c "from app.models import ..." импорт работает
</success_criteria>

<output>
После завершения создать файл `.planning/phases/01-workspace-foundation/01-01-SUMMARY.md` с описанием:
- Что создано (3 новые таблицы + 11 ALTER + 13+ индексов)
- Какие имена контейнеров теперь
- Какие модели появились в `app/models/__init__.py`
- Готовность к плану 01-02 (auth_dep middleware)
</output>
