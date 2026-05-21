# Phase 3: Agents (AI Templates) - Research

**Researched:** 2026-05-22
**Domain:** Backend cleanup + DB migration 015 + workspace-scoped CRUD rewrite (FastAPI / SQLAlchemy 2.0 async / Pydantic v2 / raw SQL Postgres)
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Финальная схема `ai_contexts`:**
- **D-01:** Миграция `015_phase3.sql` дропает с `ai_contexts`: `auto_pause_triggers`, `webhook_functions`, `document_webhook_url`, `max_message_length`, `response_delay_seconds`, `is_active`. БД чистая (Phase 1 D-01) — `DROP COLUMN` без backfill. Миграция идемпотентна (`DROP COLUMN IF EXISTS`).
- **D-02:** Финальные колонки `ai_contexts` после миграции 015: `id UUID PK`, `workspace_id UUID NOT NULL FK CASCADE`, `name VARCHAR(100) NOT NULL`, `system_prompt TEXT NULLABLE`, `rules TEXT NULLABLE`, `tone_of_voice TEXT NULLABLE`, `faq JSONB DEFAULT '{}'`, `company_info TEXT NULLABLE`, `product_info TEXT NULLABLE`, `created_at`, `updated_at`. UNIQUE INDEX `(workspace_id, name)`.
- **D-03:** UI-маппинг success criterion #1: «Контекст»→`system_prompt`, «Задача»→`rules`, «Тон»→`tone_of_voice`, «FAQ»→`faq` JSONB. Дополнительно: `company_info`, `product_info`. Никаких новых колонок.

**Cleanup старой связи sender↔agent:**
- **D-04:** Миграция `015_phase3.sql` делает `ALTER TABLE senders DROP COLUMN ai_context_id`. ORM-модель `Sender` теряет `ai_context_id` поле и `ai_context` relationship.
- **D-05:** `conversations.ai_context_id` и таблица `context_contact_assignments` **остаются как есть** в Phase 3. FK `ai_contexts.id` остаются: `conversations.ai_context_id ON DELETE SET NULL`, `context_contact_assignments.context_id ON DELETE CASCADE`.

**Backend cleanup:**
- **D-06:** Phase 3 переписывает три роутера: `app/routers/contexts.py` (полный рерайт под `AuthDep`, endpoints под `/api/v1/agents`, schemas `AgentCreate/Update/Response/ListResponse`); один из `queue.py`/`send.py` (см. C-04) — workspace-scoped рерайт под `AuthDep`, принимает явный `ai_context_id` в body; регистрация в `main.py`.
- **D-07:** `POST /api/v1/agents/{id}/duplicate` без body. Backend: пробует `«{name} (copy)»`, если занято — `«{name} (copy 2)»`, `«{name} (copy 3)»` и т.д.

**Delete семантика:**
- **D-08:** **Hard delete** (без soft-delete). `DELETE FROM ai_contexts WHERE id = :id AND workspace_id = :ws_id` — каскад FK сделает остальное.
- **D-09:** TODO для Phase 4: блокировать `DELETE` если агент привязан к active campaigns. Planner оставляет TODO-метку `# TODO(phase-4): also block on active campaign attachment`.

**Поле `campaign_count`:**
- **D-10:** `AgentResponse` всегда возвращает `campaign_count: int = 0` (хардкод в Phase 3). В Phase 4 заменяется на реальный `SELECT COUNT(*) FROM campaigns WHERE agent_id = ai_contexts.id`.

### Claude's Discretion

- **C-01:** Точный shape JSONB поля `faq` — массив `[{question, answer}]` или dict `{question: answer}`. Рекомендация: массив объектов.
- **C-02:** Точные имена endpoint'ов и Pydantic-схем. Старый файл `contexts.py` — переименовать в `agents.py` или переписать на месте — planner решит.
- **C-03:** Точная shape `AgentUpdate` (полный PUT или partial PATCH с Optional). Существующие Phase 2 используют partial PATCH с Optional.
- **C-04:** Решение, какой именно файл становится «entry point для отправки» — `send.py` или `queue.py`.
- **C-05:** Адаптация `app/routers/senders.py` под удаление `sender.ai_context_id`.
- **C-06:** Расширение `tests/conftest.py` под Phase 3 фикстуры (`agent_factory`, `mock_workspace_with_agent`).
- **C-07:** Опциональная адаптация `services/queue.py` и `services/listener.py` (НЕ переписывать целиком).

### Deferred Ideas (OUT OF SCOPE)

**Для Phase 4 (Campaigns):**
- Реальный `campaign_count` через COUNT campaigns.
- Блокировка DELETE если есть active campaigns.
- Переезд `auto_pause_triggers`, `webhook_functions`, `document_webhook_url`, `max_message_length`, `response_delay_seconds` на `campaigns`.
- Переименование `conversations.ai_context_id` под `campaign_id`.
- Переезд `context_contact_assignments` на `(campaign_id, contact_phone) → sender_id`.

**Для Phase 5 (Inbox/Analytics):**
- `usage_count` агента, лог LLM-запросов на уровне диалога.

**Из Phase 2 — деферрено дальше:**
- Перевод `senders.role` с `String(20)+CHECK` на `SQLEnum`.

**Tech debt:**
- `app/database.py` `Base.metadata.create_all` — не блокер Phase 3.

**Для v2:**
- Soft-delete агентов с timestamp `deleted_at`.
- Версионирование агентов.
- Шаблоны агентов для маркетплейса.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| AGNT-01 | Пользователь создаёт агента (AI-шаблон) с именем — workspace-level | D-01..D-06: миграция 015 + workspace-scoped `POST /api/v1/agents` под AuthDep + UNIQUE `(workspace_id, name)` (см. Pattern 1, Pattern 2) |
| AGNT-02 | Задаёт настройки агента: контекст (промпт), задача, тон, FAQ | D-02, D-03: 4 mandatory колонки (`system_prompt`/`rules`/`tone_of_voice`/`faq`) + 2 supplementary (`company_info`, `product_info`); FAQ shape resolved в "JSONB FAQ Shape" §, recommend array-of-objects |
| AGNT-03 | Агент переиспользуется между несколькими кампаниями | D-04, D-05: sender.ai_context_id дропается → агент больше не «прицеплен» к одному sender'у. Phase 4 будет cвязывать agent ↔ campaign через будущую `campaigns.agent_id`. В Phase 3 это доказывается тем, что один agent_id уже валиден в любом отправляющем запросе (через body) |
| AGNT-04 | Список агентов workspace с CRUD (создать / редактировать / удалить, дубликат) | D-06, D-07, D-08, D-10: 6 endpoints под `/api/v1/agents` (GET list / POST / GET one / PATCH / DELETE / POST duplicate), все workspace-scoped через AuthDep; hard delete; duplicate без body со auto-incrementing `(copy N)`; `campaign_count` всегда возвращается (хардкод 0 в Phase 3) |
</phase_requirements>

## Summary

Phase 3 — точечная backend-фаза: ровно одна миграция (015), полный рерайт ровно одного существующего роутера (`contexts.py` → `agents.py`), workspace-scoped рерайт ровно одного из `send.py`/`queue.py`, и небольшая правка-адаптация трёх сервисов (`queue.py` line 705, `listener.py` lines 345/359/689-708, `ai_engine.py` lines 70-79) под удалённую колонку. Главный риск — не миграция (БД чистая, можно дропать без backfill), а **runtime breakage в worker'ах**: `ai_engine.py` всё ещё `SELECT max_message_length, webhook_functions FROM ai_contexts WHERE is_active = true` — после миграции запрос упадёт, AI замолчит. Та же проблема в `listener.py:700` (`SELECT document_webhook_url`) и `rotation.py:189` (`WHERE s.ai_context_id = :ctx_id`). Эти adapter-правки D-04/C-07 — обязательные, не опциональные: Phase 3 не закроется без них, потому что worker'ы упадут на первом же реальном диалоге.

CONTEXT в десятке мест говорит «не переписывать workers целиком — только адаптация». Это правильно по объёму, но planner должен видеть, что адаптация — не «может быть», а «должна быть, иначе runtime ломается». Точные точки правки документированы в Runtime State Inventory ниже.

Recommend: array-of-objects для FAQ (`[{question, answer}]`) — лучше под UI с DnD-ordering, понятнее в JSON Schema; рерайт-в-месте старого `contexts.py` с одновременным переименованием файла в `agents.py` (через `git mv` чтобы сохранить blame); выбор `send.py` для workspace-scoped рерайта (а не `queue.py`) — `send.py` это POST-эндпоинт отправки сообщения, который ждёт n8n flow; `queue.py` это вспомогательная query-only утилита; phrasing CONTEXT D-06 «возвращает основной endpoint отправки» однозначно про `/send`.

**Primary recommendation:** Plan `03-01` = миграция 015 + ORM model cleanup + worker-adapters (queue/listener/ai_engine/rotation). Plan `03-02` = новый `agents.py` роутер + новый workspace-scoped `send.py` + senders.py cleanup + pytest fixtures. Никакие cross-cutting changes в planning разделить нельзя — миграция и worker-adapters должны идти одной волной, иначе тесты упадут на коммите между ними.

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| fastapi | 0.109.0 (repo-pinned) | HTTP router/dependency-injection | Уже используется во всех роутерах Phase 1/2. Без изменений в Phase 3. |
| sqlalchemy | 2.0.25 (repo-pinned) | Async ORM | Уже используется. `Depends(get_db)` → `AsyncSession`. Без изменений. |
| pydantic | >=2.8,<3.0 (repo-pinned) | Schemas (request/response) | Используется. `model_config = ConfigDict(from_attributes=True)`. Без изменений. |
| pydantic-settings | >=2.3,<3.0 | Settings (env vars) | Без изменений в Phase 3. |
| asyncpg | 0.29.0 | Postgres async driver | Без изменений. |
| python-jose | 3.3.0 | JWT (Supabase auth) | Используется в `auth.py` через `AuthDep`. Без изменений. |
| bcrypt | >=4.1.0,<5.0 | Workspace API-key hash | Используется в `auth.py`. Без изменений. |
| pytest, pytest-asyncio, httpx | >=8.0 / >=0.23 / 0.26.0 | Тесты (фикстуры из Phase 1/2) | Расширяем conftest.py фикстурой `agent_factory` (C-06). |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| logging (stdlib) | — | structured logs | Все новые модули: `logger = logging.getLogger(__name__)`. |
| uuid (stdlib) | — | UUID PK | Все ORM-модели: `default=uuid.uuid4`. |
| sqlalchemy.ext.asyncio | (in sqlalchemy 2.0.25) | AsyncSession | Все DB-операции — async. |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Raw SQL `text()` для миграции 015 | Alembic | **Запрещено CLAUDE.md** — «Никогда Alembic. Только raw SQL в migrations/». |
| ORM Pattern для всех CRUD-операций | Raw SQL `text()` | Folders/contacts.py смешивают ORM (SELECT через `select(Folder)`) и raw `text()` (для ON CONFLICT). Phase 3 повторяет тот же mix — ORM для простого CRUD, raw SQL для duplicate-наименования с LIKE (см. Pattern 4). |
| Soft-delete (`deleted_at TIMESTAMPTZ`) | Hard DELETE | **Locked D-08** — hard delete. Никаких soft-delete полей. |

**Installation:** Никаких новых зависимостей не требуется. Phase 3 — pure refactor + один migration файл.

**Version verification:** Все версии уже зафиксированы в `requirements.txt` после Phase 1/2 — Phase 3 не меняет dependencies.

## Architecture Patterns

### Recommended Project Structure

```
app/
├── routers/
│   ├── agents.py            # NEW — переименование из contexts.py (git mv)
│   ├── send.py              # REWRITE — workspace-scoped под AuthDep + explicit ai_context_id
│   ├── senders.py           # CLEANUP — удалить ai_context_id поле (C-05)
│   ├── queue.py             # KEEP AS-IS — старый, не зарегистрирован (C-04 решает не он)
│   ├── folders.py           # REFERENCE PATTERN (без изменений)
│   ├── contacts.py          # REFERENCE PATTERN (без изменений)
│   └── workspace.py         # без изменений
├── models/
│   └── __init__.py          # MODIFY — AIContext (выпилить 6 дроп-колонок), Sender (выпилить ai_context_id + ai_context relationship)
├── schemas/
│   └── __init__.py          # ADD AgentCreate/Update/Response/ListResponse; REMOVE SenderCreate.ai_context_id поле
├── services/
│   ├── queue.py             # ADAPT line 705 — sender.ai_context_id больше нет
│   ├── listener.py          # ADAPT lines 345, 359, 689-708, 770, 791-794 — sender.ai_context_id больше нет, document_webhook_url дроп
│   ├── ai_engine.py         # ADAPT line 70-79 — выпилить max_message_length/webhook_functions/is_active из SELECT
│   └── rotation.py          # ADAPT line 189 — `WHERE s.ai_context_id = :ctx_id` упадёт после миграции
├── main.py                  # ADD include_router(agents.router); ADD include_router(send.router) — возвращаем отправку
└── utils/
    └── auth.py              # без изменений

migrations/
└── 015_phase3.sql           # NEW — drop 6 columns from ai_contexts + drop senders.ai_context_id

tests/
└── conftest.py              # EXTEND — agent_factory fixture
```

### Pattern 1: Workspace-Scoped CRUD Router (Phase 2 reference)

**What:** Все endpoints используют `Depends(auth_dep)` + явный `where(...workspace_id == ctx.workspace_id)` фильтр.

**When to use:** Все 6 endpoints в новом `agents.py`. И в новом `send.py`.

**Example:** (canonical pattern из `app/routers/folders.py:79-92`)

```python
# Source: app/routers/folders.py (Phase 2 working pattern)
@router.get("", response_model=List[FolderResponse])
async def list_folders(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Folder)
        .where(Folder.workspace_id == ctx.workspace_id)
        # TODO(v2-rls): replaced by RLS policy app.workspace_id
        .order_by(Folder.created_at.desc())
    )
    folders = result.scalars().all()
    return [await _folder_to_response(db, f) for f in folders]
```

**Применение в Phase 3:** Тот же скелет для `list_agents`, `get_agent`, `create_agent`, `update_agent`, `delete_agent`, `duplicate_agent`. Все фильтрованы `.where(AIContext.workspace_id == ctx.workspace_id)`.

### Pattern 2: Duplicate-Name Validation на Create

**What:** Friendly 409 на дубль `(workspace_id, name)` вместо raw `IntegrityError`.

**When to use:** `POST /api/v1/agents` создание.

**Example:** (из `app/routers/folders.py:95-122`)

```python
# Source: app/routers/folders.py (Phase 2)
existing = await db.execute(
    select(Folder).where(
        Folder.workspace_id == ctx.workspace_id,
        Folder.name == name,
    )
)
if existing.scalars().first():
    raise HTTPException(
        status_code=409,
        detail={
            "code": "AGENT_NAME_DUPLICATE",  # <- адаптировать имя
            "message": f"Agent '{name}' already exists",
        },
    )
```

### Pattern 3: Partial PATCH с Optional полями (Phase 2 convention для C-03)

**What:** PATCH-эндпоинт принимает все поля Optional, обновляет только заполненные.

**When to use:** `PATCH /api/v1/agents/{id}`.

**Example:** (из `app/routers/senders.py:301-345`)

```python
# Source: app/routers/senders.py:315-335 (Phase 2)
if request.name is not None:
    sender.name = request.name
if request.phone is not None:
    sender.phone = request.phone
# ... etc
await db.commit()
```

**Phase 3 schema (рекомендуемая):**

```python
class AgentUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    system_prompt: Optional[str] = None
    rules: Optional[str] = None
    tone_of_voice: Optional[str] = None
    faq: Optional[list[FaqItem]] = None
    company_info: Optional[str] = None
    product_info: Optional[str] = None
```

### Pattern 4: Duplicate-Name Auto-increment (D-07)

**What:** Найти свободный индекс N для `«{name} (copy N)»`.

**When to use:** `POST /api/v1/agents/{id}/duplicate`.

**Recommended implementation:**

```python
# Try plain '(copy)' first, then '(copy 2)', '(copy 3)' ...
# Approach: single SQL pre-fetch all conflicts using LIKE
async def _generate_duplicate_name(
    db: AsyncSession, workspace_id: UUID, base_name: str
) -> str:
    """Generate '{name} (copy)' or '{name} (copy N)' for next free N."""
    # Fetch all names that match either '{base} (copy)' or '{base} (copy N)' pattern
    pattern_no_n = f"{base_name} (copy)"
    pattern_with_n = f"{base_name} (copy %)"
    result = await db.execute(
        text("""
            SELECT name FROM ai_contexts
            WHERE workspace_id = :wid
              AND (name = :exact OR name LIKE :pattern)
        """),
        {"wid": str(workspace_id), "exact": pattern_no_n, "pattern": pattern_with_n}
    )
    existing = {row[0] for row in result.fetchall()}
    if pattern_no_n not in existing:
        return pattern_no_n
    n = 2
    while f"{base_name} (copy {n})" in existing:
        n += 1
    return f"{base_name} (copy {n})"
```

**Rationale for this approach (over alternatives):**
- **vs. retry-on-IntegrityError loop:** Сейчас БД пустая, гонок нет; даже когда появится конкуренция — INSERT всё равно защищён UNIQUE индексом, и в worst case planner может обернуть в while-try-except UniqueViolation с переходом N+1. Но preload-через-LIKE проще читать и быстрее в normal case.
- **vs. regex extraction `(copy (\d+))`:** Регулярки в Postgres медленнее простого LIKE.
- **vs. MAX(N) + 1:** Если у клиента есть «X (copy)», «X (copy 2)», «X (copy 5)» — `MAX+1` вернёт 6 и оставит дыру в 3,4. Set-membership ищет первое свободное N.
- **Idempotency:** Если duplicate вызывается дважды быстро подряд для одного и того же оригинала, второй вызов получит другой N (т.к. первый уже зафиксирован в БД) — корректно.

### Pattern 5: Hard Delete с FK CASCADE/SET NULL (D-08)

**What:** `DELETE FROM ai_contexts WHERE id = ...` — FK сами сделают остальное.

**Example:**

```python
# Source: app/routers/folders.py:206-260 (Phase 2 reference, но без is_empty check для агентов)
# D-08: hard delete. D-09: TODO for Phase 4 active-campaign block.
@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AIContext).where(
            AIContext.id == agent_id,
            AIContext.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy app.workspace_id
        )
    )
    agent = result.scalars().first()
    if agent is None:
        raise HTTPException(status_code=404, detail={"code": "AGENT_NOT_FOUND", "message": "Agent not found"})

    # TODO(phase-4): also block on active campaign attachment
    # (как Phase 2 D-06 для folders)

    await db.delete(agent)
    await db.commit()
    # FK: conversations.ai_context_id → SET NULL (existing FK in migration 012)
    # FK: context_contact_assignments.context_id → CASCADE delete
    logger.info(f"[agents] deleted workspace={ctx.workspace_id} id={agent_id}")
```

### Anti-Patterns to Avoid

- **НЕ использовать `verify_api_key`** (выпилен в Phase 1 D-14) — только `AuthDep`. Если planner случайно скопирует import из старого `contexts.py` — это сразу провал auth tests.
- **НЕ возвращать workspace-данные без `where(workspace_id == ctx.workspace_id)` фильтра** — Phase 1 D-04. Все 6 endpoints + workspace-scoped send.
- **НЕ хранить агент-поля в `senders.ai_context_id`** — поле дропается миграцией 015.
- **НЕ добавлять soft-delete `is_active` обратно** — `is_active` дропается, hard delete по D-08.
- **НЕ менять hardcoded debounce 3-5 мин в `listener.py` и rate-limit логику в `services/queue.py`** — CLAUDE.md явно запрещает. C-07 — это адаптация одной-двух строк, не рерайт.
- **НЕ возвращать raw `IntegrityError`** на дубль имени — всегда 409 с понятным message.
- **НЕ оставлять `WHERE is_active = true` в SQL запросах к ai_contexts** — после миграции 015 этой колонки нет.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Auth/workspace context extraction | Свой middleware/декоратор | `Depends(auth_dep)` from `app/utils/auth.py` | Phase 1 D-11..D-14 закрыл это; готовый `AuthCtx(workspace_id, user_id, source, role)`. |
| Workspace API key validation | Bcrypt check, prefix lookup | `AuthDep` (внутри) | Phase 1 D-13 + Phase 02.1 CR-09 (LRU cache 5 min). |
| Workspace lazy-create на первом запросе | Свой transaction | `_resolve_or_create_workspace` (внутри `AuthDep`) | Phase 1 D-08. |
| FK CASCADE/SET NULL поведение при удалении агента | Manual UPDATE statements в DELETE handler | Existing FK constraints from migration 012 | `conversations.ai_context_id ON DELETE SET NULL` и `context_contact_assignments.context_id ON DELETE CASCADE` уже работают — старый `contexts.py:240-258` вручную делает UPDATE, что было нужно из-за отсутствия FK; в Phase 3 это излишне. |
| Folder-like auto-create by name | Manual ON CONFLICT | Pattern из `folders.get_or_create_by_name` (Phase 2) — но для agents auto-create НЕ нужен (D-07 duplicate работает по другому сценарию: explicit POST с {id} оригинала). |
| Phone E.164 normalize | — | Не нужно в Phase 3, agents без phone. |
| CSV import preview | — | Не нужно в Phase 3. |
| JSON pretty-print/validation | — | Pydantic v2 валидирует faq автоматически если задана `list[FaqItem]` модель. |

**Key insight:** Phase 3 — это reuse-фаза. Все паттерны уже есть в Phase 2 (folders/contacts.py). Главная работа planner'а — копи-паста с правильной адаптацией, а не изобретение нового.

## Runtime State Inventory

> ВКЛЮЧЕНО потому что Phase 3 — это refactor + DROP COLUMN миграция. Категорично важно перечислить, что ломается в runtime после применения миграции 015.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| **Stored data (DB rows)** | `ai_contexts` строки: 0 (БД чистая, Phase 1 D-01). `senders.ai_context_id` значения: 0. `conversations.ai_context_id` значения: 0 (Phase 1 D-01). `context_contact_assignments` строки: 0. | **None** — миграция дропает колонки без backfill (Locked D-01). |
| **Live service config (worker SQL queries against ai_contexts)** | **CRITICAL ITEMS:** `app/services/ai_engine.py:70-79` — `SELECT system_prompt, tone_of_voice, rules, company_info, max_message_length, webhook_functions FROM ai_contexts WHERE id = :id AND is_active = true` — **3 из 6 запрошенных колонок дропается миграцией 015**. `app/services/listener.py:700` — `SELECT document_webhook_url FROM ai_contexts WHERE id = :id` — **колонка дропается**. `app/services/rotation.py:189` — `WHERE s.ai_context_id = :ctx_id` — **колонка дропается**. `app/services/queue.py:705` — `sender.ai_context_id` reads на conversation INSERT — **колонка дропается**. `app/services/listener.py:345,359` — `SELECT ai_context_id FROM senders` + `dict["ai_context_id"]` — **колонка дропается**. | **ОБЯЗАТЕЛЬНО code edit:** (1) `ai_engine.py:70-79` — убрать `max_message_length`, `webhook_functions` из SELECT, убрать `AND is_active = true`. Фунция `get_context()` теперь возвращает только то, что осталось (5 полей: system_prompt, tone_of_voice, rules, company_info, product_info, faq). `build_system_prompt` уже использует system_prompt/tone/rules/company_info — без изменений в логике, только schema. `webhook_functions` логика и `execute_webhook` — мёртвый код после Phase 3, переезжает в Phase 4 на campaign-уровень (см. Phase 4 CAMP-15). В Phase 3 — оставить мёртвый код «как есть» (LLM не получит tools, но это OK — это Phase 4 фичу restoring). (2) `listener.py:700` — выпилить блок `SELECT document_webhook_url` целиком, переменная `document_webhook_url = None` остаётся, документная-webhook ветка становится no-op (тоже мёртвый код до Phase 4 CAMP-14). (3) `rotation.py:189` — `WHERE s.ai_context_id = :ctx_id` теряет смысл (sender больше не «знает» агент). **Стратегически:** `_pick_best_sender` теперь должен искать ВСЕХ активных sender'ов в workspace, без фильтра по ai_context_id. Это меняет семантику rotation — но в Phase 3 это допустимо: D-05 говорит «`context_contact_assignments` остаётся, в Phase 4 переедет на campaign». В Phase 3 sender выбирается из всего workspace pool — пока нет Campaign'ов это и так весь pool. (4) `queue.py:705` — INSERT INTO conversations использует `sender.ai_context_id` — заменить на чтение из request payload (которое теперь прилетает явно через body `send.py`). Planner добавляет параметр `ai_context_id: UUID` в `enqueue_message` функцию. (5) `listener.py:345,359,689,770,791-794` — `sender.ai_context_id` исчезает из `get_active_senders()` SELECT и из всех `sender_info["ai_context_id"]` reads. **Стратегия минимального касания:** `get_active_senders` возвращает sender_info без `ai_context_id` (просто None); затем `get_or_create_conversation` всё равно работает (conversations.ai_context_id может остаться NULL если sender больше не знает агента) — это OK, AI просто не отвечает (`if not ai_context_id: logger.warning`). В Phase 4 при появлении campaign listener будет подтягивать ai_context_id из campaign'а через JOIN. |
| **OS-registered state** | Нет (нет cron, нет systemd, нет Task Scheduler — все workers это asyncio tasks в Docker контейнерах). | None. |
| **Secrets/env vars** | Нет — Phase 3 не добавляет и не переименовывает env vars. | None. |
| **Build artifacts/installed packages** | Нет новых пакетов. ORM model classes `AIContext` и `Sender` модифицируются в `models/__init__.py` — после рестарта Docker контейнера `Base.metadata.create_all` на пустой test БД отразит новую схему. На реальной БД схема приходит из миграции 015. | **None** — Docker контейнеры пересоберутся при деплое. |

**Nothing found in category:**
- Stored data: 0 строк к миграции (Phase 1 D-01 — чистая БД).
- OS state: None — verified by `ls /etc/cron* /etc/systemd/system/*outreach*` отсутствуют (Docker-only).
- Env vars: None — verified by grep по docker-compose.yml + app/config.py: ничего из ai_contexts колонок не упоминается в env vars.

**Canonical question answered:** *После применения миграции 015, что ломается в runtime?* — ответ: 5 SQL запросов в 4 файлах (`ai_engine.py`, `listener.py`, `rotation.py`, `queue.py`) перестанут компилироваться/выполнять. Все 5 точек перечислены выше с конкретной стратегией адаптации. **Это блокер для коммита миграции в одной волне с roll-out** — миграция 015 и worker adapters должны быть в одном Wave, иначе между коммитами тесты падают и/или AI отвечает с null context.

## Common Pitfalls

### Pitfall 1: ai_engine.py `WHERE is_active = true` не упадёт сразу — упадёт асимптотически

**What goes wrong:** `SELECT ... FROM ai_contexts WHERE id = :id AND is_active = true` после `DROP COLUMN is_active`. Postgres вернёт `column "is_active" does not exist` error на КАЖДОМ входящем сообщении в Telegram.
**Why it happens:** Listener вызывает `ai_engine.get_context()` через debounce таймер (3-5 мин после сообщения). Если миграция применена, а worker не пересобран — ошибка не видна сразу при `docker-compose up -d`, она появляется через ~3 мин после первого входящего.
**How to avoid:** В test plan'е Phase 3 должно быть: (1) применить миграцию, (2) рестартануть listener, (3) сэмулировать входящее → проверить логи `ai_engine`. Без этого regress'а ловится только в production.
**Warning signs:** В логах listener после rollout: `column "is_active" does not exist` либо `column "webhook_functions" does not exist`. Тишина AI-ответа без логирования error — silent fail.

### Pitfall 2: Duplicate-name генерация имени даёт race condition при двух parallel POST /duplicate

**What goes wrong:** Два parallel POST `/agents/{id}/duplicate` для одного оригинала. Pattern 4 (preload через LIKE) — обе транзакции читают set existing names одновременно, обе видят `«X (copy 2)»` свободным, обе INSERT'ят, вторая упадёт на UNIQUE violation.
**Why it happens:** Read-modify-write без транзакционного locking.
**How to avoid:** **Recommended:** обернуть Pattern 4 в `try/except IntegrityError` цикл с переходом N+1. Postgres UNIQUE indexes гарантируют корректность; цикл reasonable bound (max 5 retries, потом 500). **Альтернатива (simpler но slower):** `SELECT ... FOR UPDATE` на родительском row — но это lock на оригинал, что мешает чтению. Гонка не реальна для UI-кейса (юзер не кликает 2 раза за миллисекунду), но защита нужна.

```python
# Pattern: retry on UniqueViolation
from asyncpg.exceptions import UniqueViolationError
from sqlalchemy.exc import IntegrityError

for attempt in range(5):
    try:
        new_name = await _generate_duplicate_name(db, ctx.workspace_id, original.name)
        new_agent = AIContext(workspace_id=ctx.workspace_id, name=new_name, ...)
        db.add(new_agent)
        await db.commit()
        break
    except IntegrityError:
        await db.rollback()
        continue
else:
    raise HTTPException(status_code=409, detail={"code": "DUPLICATE_RACE"})
```

### Pitfall 3: `migrations/015_phase3.sql` забыть оба DROP в одной транзакции

**What goes wrong:** Если миграция запустится частично (например, дропает только `senders.ai_context_id` но не `ai_contexts.is_active`) — БД останется в неконсистентом состоянии. Тесты упадут половиной на колонке `is_active`.
**Why it happens:** Раздельные `BEGIN/COMMIT` блоки или забыт `BEGIN`.
**How to avoid:** Шаблон из миграции 013/014:
```sql
BEGIN;
-- Phase 3: drop deprecated columns from ai_contexts (move to Campaign in Phase 4)
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS auto_pause_triggers;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS webhook_functions;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS document_webhook_url;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS max_message_length;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS response_delay_seconds;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS is_active;
-- Phase 3: drop senders.ai_context_id (agent no longer tied to sender)
ALTER TABLE senders DROP COLUMN IF EXISTS ai_context_id;
-- Phase 3: add UNIQUE (workspace_id, name) — для duplicate-протекции
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_contexts_workspace_name
    ON ai_contexts(workspace_id, name);
COMMIT;
```
**Warning signs:** Если test_conftest.py пытается create_all (а не миграции) — schema может разойтись. Решение: тестовая миграция 015 должна быть в conftest как 012, 013 (см. `tests/conftest.py:46-52` для шаблона).

### Pitfall 4: senders.py:96-97 ai_context_id поле в Response — ломает Sender API

**What goes wrong:** `SenderResponse.ai_context_id: Optional[UUID]` поле в `app/schemas/__init__.py:131` — у sender больше нет этого поля. Pydantic `from_attributes=True` упадёт на отсутствие attribute.
**Why it happens:** C-05 деперрено CONTEXT'ом как «адаптация», но planner может забыть.
**How to avoid:** **Required в Phase 3:** удалить из `SenderResponse` поля `ai_context_id`, `ai_context_name`; удалить из `SenderCreate` поле `ai_context_id`; удалить из `SenderUpdate` поле `ai_context_id`. Удалить `selectinload(Sender.ai_context)` вызовы в senders.py (lines 96-97, 150, 184, 231, 247, 329, 330, 342, 438). Удалить ORM `ai_context` relationship из `Sender` модели.
**Warning signs:** Тесты `test_senders.py` упадут с `AttributeError: 'Sender' object has no attribute 'ai_context_id'`.

### Pitfall 5: services/listener.py:689,770 `sender_info.get("ai_context_id")` без default

**What goes wrong:** Listener `_handle_message` после миграции получает `sender_info` без ключа `ai_context_id` (потому что `get_active_senders` (line 354) больше не SELECT'ит эту колонку). `sender_info.get("ai_context_id")` вернёт `None` — это OK для `dict.get()`. Но: `conv["ai_context_id"]` (line 693, 774) — если новая conversation создана с ai_context_id=None, AI не ответит, и юзер не поймёт почему.
**Why it happens:** В Phase 3 связь sender↔agent выпилена. Listener больше не знает агента из sender'а.
**How to avoid:** Документировать как «known regression в Phase 3»: для входящих сообщений на sender'а **без активной кампании** (т.е. в Phase 3 вообще для всех, потому что Campaign'ов ещё нет) — AI не отвечает. Это OK потому что: (1) outbound отправка работает (explicit ai_context_id в body); (2) inbound на «случайного» sender'а без кампании в продакшене не должен происходить (sender онбордится для кампании); (3) Phase 4 (CAMP-02 связь agent↔campaign + CAMP-17 queue по campaign_id) восстановит inbound AI через `conversation.campaign_id → campaigns.agent_id`. Planner оставляет TODO-метку в `_send_to_ai` (line 247-256) `# TODO(phase-4): pull ai_context_id from conversation.campaign_id via JOIN`.
**Warning signs:** Test_listener.py (если есть) на сценарий «inbound message без conversation.ai_context_id» — должен ассертить именно warning log `Нет ai_context_id`, не ошибку. Это ожидаемое поведение в Phase 3.

### Pitfall 6: send.py и queue.py imports пересекаются

**What goes wrong:** Если planner оставит старый `app/routers/queue.py` нерегистрированным (как сейчас), но импорты `MIN_SEND_INTERVAL`, `MAX_SEND_INTERVAL`, `_queue_position` из `app.services.queue` — эти служебные импорты могут заломаться при рерайте `send.py` если он перестанет вызывать `enqueue_message` тем же сигнатурным контрактом.
**Why it happens:** `send.py` и `queue.py` (router) живут параллельно: `send.py` — POST для enqueue, `queue.py` — GET для status. Они независимы. Но обе зависят от `app.services.queue` функций.
**How to avoid:** В Phase 3 **НЕ трогать** `app/routers/queue.py` (router) и **НЕ трогать** `app/services/queue.py` (worker). Рерайтить только `app/routers/send.py`. Это C-04: рекомендация planner'у — `send.py` это «entry point для отправки», `queue.py` (router) это служебный query-эндпоинт и его можно вернуть в `main.py` отдельно (но это не обязательно для Phase 3 success criteria).

### Pitfall 7: FAQ JSONB partial-update семантика

**What goes wrong:** `PATCH /api/v1/agents/{id}` с `{"faq": [{...}]}` должен **полностью заменить** массив, а не merge'ить. Если planner случайно сделает `UPDATE ai_contexts SET faq = faq || :new` (concat) — каждый PATCH добавляет дубликаты.
**Why it happens:** JSONB ||  operator может выглядеть «правильно» как merge.
**How to avoid:** REST PATCH-семантика для array fields = **full replacement** (как PUT для этого поля). UI делает все Add/Remove/Edit FAQ на клиенте и шлёт полный массив. Это самая простая и стандартная для JSON API семантика.

```python
# Right:
if request.faq is not None:
    agent.faq = [item.model_dump() for item in request.faq]  # full replace
# Wrong:
# UPDATE ai_contexts SET faq = faq || :new  -- concatenates duplicates
```

## Code Examples

### Example 1: Migration 015 polished template

```sql
-- Source: pattern from migrations/013_phase2.sql + 014_phase2_1_hardening.sql
-- migrations/015_phase3.sql
-- Phase 3: Agents (AI Templates) — cleanup ai_contexts schema
-- Drops 6 deprecated columns (Campaign-concern, move to Phase 4)
-- Drops senders.ai_context_id (agent no longer tied to sender)
-- Adds UNIQUE (workspace_id, name) for duplicate-protection (D-02)
-- DB clean (Phase 1 D-01) — no backfill needed.
-- All operators idempotent (IF EXISTS).

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

### Example 2: AgentResponse Pydantic schema (C-02 resolution)

```python
# Source: pattern from app/schemas/__init__.py (Phase 2 Folder/Contact)
# To add to app/schemas/__init__.py

class FaqItem(BaseModel):
    """Single FAQ Q&A pair. C-01 resolution: array of objects (over dict)."""
    question: str = Field(..., max_length=500)
    answer: str = Field(..., max_length=2000)


class AgentCreate(BaseModel):
    """POST /api/v1/agents body."""
    name: str = Field(..., min_length=1, max_length=100)
    system_prompt: Optional[str] = None
    rules: Optional[str] = None
    tone_of_voice: Optional[str] = None
    faq: List[FaqItem] = Field(default_factory=list)
    company_info: Optional[str] = None
    product_info: Optional[str] = None


class AgentUpdate(BaseModel):
    """PATCH /api/v1/agents/{id}. Partial PATCH (C-03 — Phase 2 convention)."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    system_prompt: Optional[str] = None
    rules: Optional[str] = None
    tone_of_voice: Optional[str] = None
    faq: Optional[List[FaqItem]] = None  # None = leave unchanged; [] = clear FAQ
    company_info: Optional[str] = None
    product_info: Optional[str] = None


class AgentResponse(BaseModel):
    """GET / POST / PATCH response body. D-10: campaign_count hardcoded 0 in Phase 3."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    system_prompt: Optional[str]
    rules: Optional[str]
    tone_of_voice: Optional[str]
    faq: List[FaqItem] = []
    company_info: Optional[str]
    product_info: Optional[str]
    campaign_count: int = 0  # D-10: hardcoded in Phase 3, real query in Phase 4
    created_at: datetime
    updated_at: datetime


class AgentListResponse(BaseModel):
    agents: List[AgentResponse]
    total: int
```

### Example 3: POST /api/v1/agents (create) — full endpoint

```python
# Source: pattern from app/routers/folders.py:95 + app/routers/senders.py:195
# In app/routers/agents.py (new file or rewrite of contexts.py)

@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    payload: AgentCreate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Create new agent (workspace-level template). 409 on duplicate name."""
    name = payload.name.strip()
    # Check duplicate (friendlier than IntegrityError)
    existing = await db.execute(
        select(AIContext).where(
            AIContext.workspace_id == ctx.workspace_id,
            AIContext.name == name,
        )
        # TODO(v2-rls): replaced by RLS policy app.workspace_id
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "AGENT_NAME_DUPLICATE",
                "message": f"Agent '{name}' already exists",
            },
        )
    agent = AIContext(
        workspace_id=ctx.workspace_id,
        name=name,
        system_prompt=payload.system_prompt,
        rules=payload.rules,
        tone_of_voice=payload.tone_of_voice,
        faq=[item.model_dump() for item in payload.faq],
        company_info=payload.company_info,
        product_info=payload.product_info,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    logger.info(f"[agents] created workspace={ctx.workspace_id} name='{name}' id={agent.id}")
    return _agent_to_response(agent)


def _agent_to_response(agent: AIContext) -> AgentResponse:
    """Build AgentResponse with hardcoded campaign_count=0 (D-10)."""
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        system_prompt=agent.system_prompt,
        rules=agent.rules,
        tone_of_voice=agent.tone_of_voice,
        faq=[FaqItem(**item) for item in (agent.faq or [])],
        company_info=agent.company_info,
        product_info=agent.product_info,
        campaign_count=0,  # D-10: hardcoded in Phase 3, real query in Phase 4
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )
```

### Example 4: POST /api/v1/agents/{id}/duplicate — full endpoint

```python
# Source: Pattern 4 + Pitfall 2 protection

@router.post("/{agent_id}/duplicate", response_model=AgentResponse, status_code=201)
async def duplicate_agent(
    agent_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """D-07: POST /api/v1/agents/{id}/duplicate без body. Auto-name '(copy)' / '(copy N)'."""
    # 1. Fetch original (workspace-scoped)
    result = await db.execute(
        select(AIContext).where(
            AIContext.id == agent_id,
            AIContext.workspace_id == ctx.workspace_id,
        )
    )
    original = result.scalars().first()
    if original is None:
        raise HTTPException(status_code=404, detail={"code": "AGENT_NOT_FOUND", "message": "Agent not found"})

    # 2. Retry-on-UniqueViolation loop (Pitfall 2)
    for attempt in range(5):
        new_name = await _generate_duplicate_name(db, ctx.workspace_id, original.name)
        new_agent = AIContext(
            workspace_id=ctx.workspace_id,
            name=new_name,
            system_prompt=original.system_prompt,
            rules=original.rules,
            tone_of_voice=original.tone_of_voice,
            faq=original.faq,
            company_info=original.company_info,
            product_info=original.product_info,
        )
        db.add(new_agent)
        try:
            await db.commit()
            await db.refresh(new_agent)
            logger.info(f"[agents] duplicated workspace={ctx.workspace_id} src={agent_id} dst={new_agent.id} name='{new_name}'")
            return _agent_to_response(new_agent)
        except IntegrityError:
            await db.rollback()
            continue
    raise HTTPException(status_code=409, detail={"code": "DUPLICATE_RACE", "message": "Failed to allocate unique name after retries"})
```

### Example 5: POST /api/v1/send rewrite — workspace-scoped + explicit ai_context_id

```python
# Source: pattern from app/routers/senders.py:195 (AuthDep workspace filter)
# Replaces existing app/routers/send.py (which uses verify_api_key — выпилен в Phase 1)

router = APIRouter(prefix="/api/v1", tags=["send"])


class SendMessageRequest(BaseModel):
    """Phase 3 rewrite: ai_context_id REQUIRED (no auto-derive from sender)."""
    ai_context_id: UUID = Field(..., description="Agent ID (workspace-scoped validation)")
    sender_slug: Optional[str] = Field(None, description="Explicit sender, else rotation")
    recipient_phone: str
    recipient_name: Optional[str] = None
    message: str = Field(..., max_length=4096)
    as_draft: bool = False
    metadata: Optional[dict] = Field(default_factory=dict)
    callback_url: Optional[str] = None


@router.post("/send", response_model=EnqueueResponse)
async def send_message(
    request: SendMessageRequest,
    ctx: AuthCtx = Depends(auth_dep),  # JWT or X-Workspace-Key (D-13 Phase 1)
    db: AsyncSession = Depends(get_db),
):
    """Enqueue Telegram message. Phase 3: agent_id is explicit in body."""
    # 1. Validate agent exists in caller's workspace
    agent_result = await db.execute(
        select(AIContext).where(
            AIContext.id == request.ai_context_id,
            AIContext.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    agent = agent_result.scalars().first()
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "AGENT_NOT_FOUND", "message": f"Agent {request.ai_context_id} not found in workspace"},
        )

    # 2. Resolve sender (explicit slug OR rotation within workspace)
    if request.sender_slug:
        sender_result = await db.execute(
            select(Sender).where(
                Sender.slug == request.sender_slug,
                Sender.workspace_id == ctx.workspace_id,
            )
        )
        sender = sender_result.scalars().first()
        if sender is None:
            raise HTTPException(404, detail={"code": "SENDER_NOT_FOUND"})
        if sender.lifecycle_status != "active" or sender.auth_status != "ok":
            raise HTTPException(409, detail={"code": "SENDER_NOT_READY", "lifecycle_status": sender.lifecycle_status, "auth_status": sender.auth_status})
    else:
        # Rotation (rotation.py uses ai_context_id + workspace_id — but after migration
        # rotation needs adjustment: ai_context_id no longer on senders table.
        # Strategy: pass workspace_id+context_id+phone, rotation picks among ALL workspace senders.
        try:
            sender = await get_or_assign_sender(
                db=db,
                context_id=request.ai_context_id,
                contact_phone=request.recipient_phone,
                workspace_id=ctx.workspace_id,
            )
        except ValueError as e:
            raise HTTPException(409, detail={"code": "NO_ACTIVE_SENDER", "message": str(e)})

    # 3. Enqueue (queue.py adapted to take explicit ai_context_id)
    info = await enqueue_message(
        db=db,
        workspace_id=ctx.workspace_id,
        sender_id=sender.id,
        sender_slug=sender.slug,
        ai_context_id=request.ai_context_id,  # NEW PARAMETER (Phase 3 adaptation)
        recipient_phone=request.recipient_phone,
        recipient_name=request.recipient_name,
        message_text=request.message,
        as_draft=request.as_draft,
        metadata=request.metadata,
        callback_url=request.callback_url,
    )
    return EnqueueResponse(success=True, queued=True, queue_id=info["queue_id"], ...)
```

### Example 6: tests/conftest.py extension — agent_factory fixture (C-06)

```python
# Source: pattern from existing test_workspace, test_sender_factory fixtures
# Add to tests/conftest.py

from app.models import AIContext  # noqa: E402 (existing import block)


@pytest_asyncio.fixture
async def test_agent_factory(
    async_db_session: AsyncSession,
    test_workspace: Workspace,
):
    """Factory for AIContext (agent) test fixtures.

    Usage:
        agent = await test_agent_factory(name="Sales Agent")
        agent_with_faq = await test_agent_factory(faq=[{"question": "Q1", "answer": "A1"}])
    """
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

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Один глобальный API-key (`X-API-Key`) | `AuthDep` dual auth (Supabase JWT + Workspace API key) | Phase 1 (2026-05-21) | Все Phase 3 routers используют `Depends(auth_dep)`. Никакой regression на `verify_api_key`. |
| `sender.ai_context_id` (per-sender привязка) | `agent_id` explicit в request body | Phase 3 (this phase) | Agent decoupled from sender — workspace-level template. |
| `WHERE is_active = true` soft-delete | Hard DELETE с CASCADE/SET NULL | Phase 3 D-08 | Старый paтeрn убирается из всех 5 ai_contexts SELECT. |
| `subprocess.run(["docker", "restart"])` listener reload | Periodic 30-sec reconcile loop | Phase 2 D-18 | Phase 3 не вносит изменений в listener startup, но не репродуцирует subprocess.run. |
| Hardcoded rate-limits в queue.py | `senders.rate_per_min/hour/day` per-sender | Phase 2 D-13 | Phase 3 не трогает rate-limit логику (явно запрещено CLAUDE.md). |
| In-memory `_onboarding_sessions` dict | `onboarding_sessions` таблица | Phase 2 D-16 | Phase 3 не трогает onboarding. |

**Deprecated/outdated:**
- `app/routers/contexts.py` (старая версия): использует `verify_api_key` (выпилен), `is_active` (дроп), `webhook_functions`/`document_webhook_url`/`max_message_length`/`response_delay_seconds` (дроп). После Phase 3 — полный рерайт или git-rename `contexts.py` → `agents.py`.
- `app/routers/send.py` (старая версия): использует `verify_api_key`, `sender.is_active` (line 53 — это поле дропнуто Phase 2), `AIContext.is_active` (line 68, 190 — дропается Phase 3). Полный рерайт.
- `app/routers/queue.py` (router): использует `verify_api_key` — не рерайтится в Phase 3 (C-04 — выбор `send.py` рекомендуется ниже), остаётся как «не зарегистрирован в main.py».

## Open Questions

1. **Что делать с `routers/queue.py` (router file) после Phase 3?**
   - Что знаю: Это GET-only query endpoint (`/api/v1/queue/{queue_id}`, `/api/v1/queue/stats/{slug}`, `DELETE /api/v1/queue/{queue_id}`). Использует `verify_api_key` (выпилен Phase 1). Не зарегистрирован в `main.py` после Phase 1 D-14.
   - Что неясно: Нужен ли n8n/Lovable для polling статуса очереди в Phase 3 или это можно отложить.
   - Рекомендация: **Не трогать в Phase 3**. CONTEXT D-06 говорит явно «выберет один файл — `send.py` или `queue.py`», planner выбирает `send.py` (см. рекомендацию ниже). `queue.py` router возвращается в `main.py` отдельно в Phase 4/5 при ребилде analytics — там же реrайт под AuthDep + workspace filter. В Phase 3 — остаётся «dead code» как и `routers/conversations.py`, `routers/warmup.py`, `routers/check_contacts.py` legacy.

2. **`services/ai_engine.py.webhook_functions` логика — мёртвый код в Phase 3?**
   - Что знаю: `webhook_functions` колонка дропается. `ai_engine.get_context` сейчас возвращает `webhook_functions` в context dict, `build_tools` вызывается на каждый AI ответ.
   - Что неясно: Если возвращать пустой список — `build_tools([]) → []` — chat.completion работает без tools. Никакая ошибка не вылетит.
   - Рекомендация: Адаптировать `get_context()` чтобы всегда возвращать `webhook_functions: []`. Функции `build_tools`, `execute_webhook` оставить нетронутыми — Phase 4 (CAMP-15) их переиспользует, перенося источник tools со `ai_contexts` на `campaigns`. Никакого риска.

3. **C-04 рекомендация: `send.py` или `queue.py` для workspace-scoped рерайта?**
   - Что знаю: `send.py` (346 строк) — три POST-эндпоинта для отправки (`/send`, `/send-file`, `/send-batch`). `queue.py` (router, 155 строк) — три GET/DELETE для status/stats. Обе используют `verify_api_key`.
   - Что неясно: На какой роутер ожидает n8n.
   - **Рекомендация (HIGH confidence):** `send.py`. Причины: (а) CONTEXT D-06 пишет «возвращает основной endpoint отправки в `main.py`» — `/send` это и есть основной endpoint; (б) бизнес-логика «отправка с явным `ai_context_id`» (D-06) очевидно про POST `/send`, а не про GET `/queue/{id}`; (в) `send.py` уже импортирует `enqueue_message`, `get_or_assign_sender` — те самые функции, которые нужны под Phase 3 «agent explicit в body».

4. **Регистрировать ли `send.py` сразу после рерайта в `main.py`?**
   - Что знаю: После Phase 1/2 в `main.py` нет ни одного `send`-эндпоинта. После Phase 3 D-06 — «продукт снова отвечает на основной endpoint отправки + CRUD агентов».
   - Что неясно: Нет, всё ясно.
   - Рекомендация: **Да, регистрировать `send.router` в `main.py`** одной из задач плана 03-02. Это явное success criterion в D-06.

5. **Test conftest — нужно ли тестировать migration 015 в conftest setup?**
   - Что знаю: Phase 1 conftest применяет 012 + 013; в Phase 02.1 добавили 014 (не вижу в conftest — но `Base.metadata.create_all` сначала строит ORM schema). После Phase 3 ORM `AIContext` будет без дропнутых колонок — `create_all` сразу даст правильную schema. НО: миграция 015 ALTER'ит существующую таблицу (созданную через create_all без этих колонок) — она idempotent на `DROP COLUMN IF EXISTS` поэтому не упадёт. Финальная схема в тестах будет корректной.
   - Рекомендация: Добавить применение 015 в `tests/conftest.py:46-52` (по шаблону 012+013) для **explicit migration testing** — тогда смок-тест миграции 015 покрыт by-construction.

## Environment Availability

> Phase 3 — pure code/config changes. Никаких новых external dependencies не вводится.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11 | Runtime | ✓ | 3.11+ (pinned) | — |
| PostgreSQL 16 | Migration 015 | ✓ (Docker compose db) | 16 (Docker image) | — |
| Existing libs (FastAPI/SQLAlchemy/Pydantic/asyncpg) | Routers/models | ✓ | requirements.txt pinned | — |
| pytest, pytest-asyncio, httpx | Phase 3 tests | ✓ | Phase 1 D-17 baseline | — |

**Missing dependencies with no fallback:** None.
**Missing dependencies with fallback:** None.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 0.23+ |
| Config file | `tests/conftest.py` (Phase 1 Wave 0 baseline, Phase 2 extension) |
| Quick run command | `pytest tests/test_agents.py -x -v` |
| Full suite command | `pytest tests/ -x -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| AGNT-01 | Создание агента workspace-level | unit/integration | `pytest tests/test_agents.py::test_create_agent_returns_201 -x` | Wave 0 |
| AGNT-01 | Workspace isolation (cross-tenant 404) | integration | `pytest tests/test_agents.py::test_create_agent_workspace_scoped -x` | Wave 0 |
| AGNT-01 | Duplicate name → 409 | integration | `pytest tests/test_agents.py::test_create_agent_duplicate_name_409 -x` | Wave 0 |
| AGNT-02 | system_prompt/rules/tone_of_voice/faq поля сохраняются | unit | `pytest tests/test_agents.py::test_create_agent_persists_all_fields -x` | Wave 0 |
| AGNT-02 | FAQ JSONB shape `[{question,answer}]` валидация | unit | `pytest tests/test_agents.py::test_faq_shape_validation -x` | Wave 0 |
| AGNT-02 | FAQ partial PATCH = full replacement | unit | `pytest tests/test_agents.py::test_patch_faq_replaces_not_merges -x` | Wave 0 |
| AGNT-03 | sender.ai_context_id колонка дропнута (Sender без ai_context attribute) | smoke/migration | `pytest tests/test_migration_015.py::test_senders_no_ai_context_id -x` | Wave 0 |
| AGNT-03 | Один agent_id валиден в любом send-запросе workspace'а (доказательство переиспользования) | integration | `pytest tests/test_send.py::test_same_agent_id_works_for_multiple_senders -x` | Wave 0 |
| AGNT-04 | GET /agents возвращает список с campaign_count=0 | integration | `pytest tests/test_agents.py::test_list_agents_with_campaign_count -x` | Wave 0 |
| AGNT-04 | PATCH /agents/{id} partial update | integration | `pytest tests/test_agents.py::test_patch_agent_partial -x` | Wave 0 |
| AGNT-04 | DELETE /agents/{id} hard delete, conversations.ai_context_id → NULL | integration | `pytest tests/test_agents.py::test_delete_agent_sets_conversation_to_null -x` | Wave 0 |
| AGNT-04 | DELETE каскадно удаляет context_contact_assignments | integration | `pytest tests/test_agents.py::test_delete_agent_cascades_assignments -x` | Wave 0 |
| AGNT-04 | POST /agents/{id}/duplicate auto-name (copy)/(copy 2)/(copy 3) | integration | `pytest tests/test_agents.py::test_duplicate_agent_auto_name -x` | Wave 0 |
| AGNT-04 | POST /agents/{id}/duplicate race protection (5 retries) | unit | `pytest tests/test_agents.py::test_duplicate_race_handling -x` | Wave 0 |
| Phase 3 worker adaptation | ai_engine.get_context работает без is_active/max_message_length/webhook_functions | unit | `pytest tests/test_ai_engine.py::test_get_context_phase3_schema -x` | Wave 0 |
| Phase 3 worker adaptation | listener get_active_senders больше не SELECT'ит ai_context_id | unit | `pytest tests/test_listener.py::test_get_active_senders_no_ai_context_id -x` | Wave 0 |
| Phase 3 worker adaptation | rotation.py _pick_best_sender больше не фильтрует по ai_context_id | unit | `pytest tests/test_rotation.py::test_pick_best_sender_workspace_only -x` | Wave 0 |
| Phase 3 worker adaptation | queue.py enqueue принимает ai_context_id explicit, INSERT'ит в conversations | integration | `pytest tests/test_queue_enqueue.py::test_enqueue_with_explicit_ai_context_id -x` | Wave 0 |
| send.py rewrite (C-04) | POST /api/v1/send требует ai_context_id обязательно | integration | `pytest tests/test_send.py::test_send_requires_ai_context_id -x` | Wave 0 |
| send.py rewrite | POST /api/v1/send 404 если agent в другом workspace | integration | `pytest tests/test_send.py::test_send_cross_workspace_agent_404 -x` | Wave 0 |
| Migration 015 | Все 6 колонок ai_contexts дропнуты | migration smoke | `pytest tests/test_migration_015.py::test_dropped_columns_absent -x` | Wave 0 |
| Migration 015 | UNIQUE (workspace_id, name) на ai_contexts | migration smoke | `pytest tests/test_migration_015.py::test_unique_workspace_name -x` | Wave 0 |
| Migration 015 | Idempotent (повторный запуск не падает) | migration smoke | `pytest tests/test_migration_015.py::test_idempotent -x` | Wave 0 |
| Senders cleanup (C-05) | SenderResponse без ai_context_id поля | unit | `pytest tests/test_senders.py::test_response_has_no_ai_context_id -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/test_agents.py tests/test_migration_015.py -x` (~30 sec)
- **Per wave merge:** `pytest tests/ -x -v` (полный suite — ~2-3 min)
- **Phase gate:** Full suite green + manual smoke (POST /agents → POST /send) before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_agents.py` — covers AGNT-01..04 (CRUD + duplicate + delete cascades), не существует, создать
- [ ] `tests/test_migration_015.py` — covers migration smoke + idempotent + UNIQUE constraint
- [ ] `tests/test_send.py` — covers Phase 3 rewrite of POST /api/v1/send
- [ ] `tests/test_ai_engine.py` — covers worker adapter (get_context without is_active/max_message_length)
- [ ] `tests/test_listener.py` — covers get_active_senders adaptation
- [ ] `tests/test_rotation.py` — covers _pick_best_sender adaptation
- [ ] `tests/test_queue_enqueue.py` — covers enqueue_message new ai_context_id parameter
- [ ] `tests/test_senders.py::test_response_has_no_ai_context_id` — extension к существующему senders test file (если он есть) или новый
- [ ] `tests/conftest.py` — добавить `test_agent_factory` fixture + apply migration 015 в `_setup_database`

*(Если test_senders.py / test_listener.py / etc. уже существуют в репо для Phase 2 — extend; если нет — create from scratch. Phase 1 D-17 заложил pytest baseline.)*

## Project Constraints (from CLAUDE.md)

**Required behaviors:**
- Общение с пользователем — русский. Код и коммиты — английский.
- Перед любым изменением (кроме однострочных правок): объяснить что/зачем по-русски (2-3 предложения), дождаться подтверждения, потом писать код. Исключения для immediate-write: typo, переименование, docstring, форматирование.

**Required tools/conventions:**
- Async everywhere: все DB через `async/await` + `AsyncSession`. **Никаких `time.sleep()`, синхронных `requests`, `print()` вместо `logging`**.
- Миграции: только raw SQL в `migrations/`. Нумерация `015_phase3.sql` (следующая после 014). **Всегда идемпотентны (`IF EXISTS` / `IF NOT EXISTS`). Никогда Alembic.**
- Pydantic v2: `model_config = ConfigDict(from_attributes=True)`, partial PATCH с Optional.

**Forbidden patterns:**
- **Не менять rate-limit интервалы** (4 msg/мин, 20/час, 150/день) в `services/queue.py` без явного обсуждения — подобраны эмпирически.
- **Не ломать retry-логику FloodWait** без явной просьбы.
- **Не ломать debounce 3-5 мин** в `listener.py` (Phase 3 явно ограничивает worker-изменения до 1-2 строк адаптации, не рерайт).
- **Не использовать `time.sleep()`, синхронный `requests`, `print()`** — только async + logging.

**Security requirements:**
- Сессии Telegram зашифрованы (Fernet) — не логировать decrypted session_string.
- `API_KEY` не в логах — `AuthDep` уже соблюдает.

**Code style:**
- Имя workspace-level CRUD-роутера: `agents.py` (recommendation для C-02 — соответствует `senders.py`, `folders.py`, `contacts.py`).
- ORM model name: остаётся `AIContext` (D-02). Никаких новых таблиц.
- Все таблицы: `workspace_id UUID NOT NULL FK CASCADE` (Phase 1 D-04 + Phase 2 паттерн).

## Sources

### Primary (HIGH confidence)

- `app/models/__init__.py` — ORM schema (Phase 1+2 actual state)
- `app/routers/folders.py` — Phase 2 reference CRUD pattern (workspace-scoped + duplicate name 409 + helper `get_or_create_by_name`)
- `app/routers/contacts.py` — Phase 2 reference (push body, batch dedupe, workspace-scope helpers)
- `app/routers/senders.py` — Phase 2 reference (`AuthDep` + derived response + warnings pattern + delete with manual cascades)
- `app/utils/auth.py` — Phase 1 `AuthCtx` + `auth_dep` final signature
- `app/services/ai_engine.py` lines 70-79, 162-196 — confirmed SELECT against dropped columns
- `app/services/listener.py` lines 200-300, 335-440, 680-800 — confirmed sender.ai_context_id reads + document_webhook_url SELECT
- `app/services/rotation.py` lines 22-205 — confirmed `WHERE s.ai_context_id = :ctx_id`
- `app/services/queue.py` lines 680-720 — confirmed sender.ai_context_id INSERT into conversations
- `app/main.py` — Phase 1/2 router registration (no `send`, no `agents` yet)
- `migrations/013_phase2.sql` lines 100-119 — DROP COLUMN pattern + CHECK constraint pattern
- `migrations/014_phase2_1_hardening.sql` — BEGIN/COMMIT idempotent migration pattern
- `tests/conftest.py` — Phase 2 fixture pattern для test_workspace, test_sender_factory, test_folder
- `.planning/phases/03-agents-ai-templates/03-CONTEXT.md` — full Phase 3 decisions D-01..D-10, C-01..C-07, deferred ideas
- `.planning/phases/01-workspace-foundation/01-CONTEXT.md` — Phase 1 AuthDep / workspace isolation pattern source
- `.planning/phases/02-tg-accounts-contacts/02-CONTEXT.md` — Phase 2 patterns + deferred Phase 3 items

### Secondary (MEDIUM confidence)

- [SQLAlchemy 2.0 Asyncio docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) — partial-update best practices confirmed via WebSearch
- `requirements.txt` — pinned versions (fastapi 0.109.0, sqlalchemy 2.0.25, pydantic >=2.8) verified against repo state

### Tertiary (LOW confidence)

- (none — all critical findings cross-verified against repo source)

## Metadata

**Confidence breakdown:**

- **Standard stack: HIGH** — все версии уже зафиксированы в `requirements.txt`, никаких новых deps.
- **Architecture: HIGH** — паттерн `AuthDep + workspace filter + partial PATCH + duplicate-409` уже работает в `folders.py` / `contacts.py` / `senders.py`. Phase 3 — applying-existing-patterns.
- **Pitfalls: HIGH** — все 7 pitfalls verified против actual code (grep'нул все 5 точек worker breakage напрямую). Pitfall 1 (ai_engine выпадает после миграции) — critical, cross-verified в файлах `ai_engine.py:70-79`, `listener.py:700`, `rotation.py:189`.
- **Runtime state inventory: HIGH** — каждая категория явно исследована, нет blank категорий.
- **Validation architecture: HIGH** — 22 теста маппят на 4 phase requirements (AGNT-01..04) + 6 worker-adapter тестов + 3 migration smoke tests.

**Research date:** 2026-05-22
**Valid until:** 2026-06-22 (30 дней — стабильный refactor-domain, без cutting-edge integrations)
