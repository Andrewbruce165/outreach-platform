# Phase 3: Agents (AI Templates) - Context

**Gathered:** 2026-05-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 3 превращает существующую модель `ai_contexts` (per-sender AI-настройка из эпохи telegram-api) в **workspace-level переиспользуемый шаблон агента**. Это «agent-as-template» — клиент создаёт агента с именем, контекстом, задачей, тоном и FAQ один раз и использует его в нескольких кампаниях (Phase 4) без копи-пасты.

**В скоупе:**

1. **Миграция `015_phase3.sql`** — финальная чистка схемы `ai_contexts`:
   - DROP лишних колонок: `auto_pause_triggers`, `webhook_functions`, `document_webhook_url`, `max_message_length`, `response_delay_seconds`, `is_active` (эти поля логически принадлежат Campaign — переедут в Phase 4 на правильную таблицу).
   - DROP COLUMN `senders.ai_context_id` (sender больше не «знает» агента — связь идёт через campaign в Phase 4).
   - Финальный набор колонок `ai_contexts`: `id`, `workspace_id`, `name`, `system_prompt`, `rules`, `tone_of_voice`, `faq` (JSONB), `company_info`, `product_info`, `created_at`, `updated_at`.

2. **Workspace-scoped CRUD `app/routers/contexts.py`** — полностью переписан под `AuthDep` + `workspace_id` фильтр (старый файл существует, но не зарегистрирован в `main.py` после Phase 1 D-14). Регистрируется обратно в `main.py`. Endpoints под `/api/v1/agents` (UI/API наружу использует терминологию «agent», DB-таблица остаётся `ai_contexts` per success criterion #2):
   - `GET /api/v1/agents` — список с `campaign_count: 0` (хардкод-заглушка до Phase 4).
   - `POST /api/v1/agents` — создание (валидация дубля по `(workspace_id, name)`).
   - `GET /api/v1/agents/{id}` — workspace-scoped read.
   - `PATCH /api/v1/agents/{id}` — обновление.
   - `DELETE /api/v1/agents/{id}` — hard delete (FK SET NULL у `conversations`, CASCADE у `context_contact_assignments`).
   - `POST /api/v1/agents/{id}/duplicate` — без body, автоимя `«{name} (copy)»` / `«{name} (copy N)»`.

3. **`app/routers/queue.py` / `app/routers/send.py` — workspace-scoped рерайт под AuthDep + явный `ai_context_id` в request body.** n8n / UI шлёт `ai_context_id` напрямую в payload отправки сообщения; больше не читается из `sender.ai_context_id` (этой колонки не существует). Это возвращает основной endpoint отправки в `main.py` без ожидания Phase 4.

**Не в скоупе (для последующих фаз):**

- Модель `Campaign`, `campaign_id` в очереди, расписание per-campaign, signals (lead/finish/manual handoff), webhook кампании, tools кампании, переменные `{{имя}}` — всё это Phase 4 (CAMP-01..17).
- Реальный `campaign_count` через `COUNT(*) FROM campaigns WHERE agent_id=...` — Phase 4.
- Перевод `senders.role` с `String(20)+CHECK` на `SQLEnum` — деферрено из Phase 2, дотащим если planner сочтёт уместным (не блокер).
- Inbox UI, AI-фильтр системных ботов, аналитика — Phase 5.
- Admin-бот workspace — Phase 6.

</domain>

<decisions>
## Implementation Decisions

### Финальная схема `ai_contexts`

- **D-01:** Миграция `015_phase3.sql` дропает с `ai_contexts` следующие колонки:
  - `auto_pause_triggers` (JSONB) — концерн кампании, реализуется как CAMP-12 signal в Phase 4.
  - `webhook_functions` (JSONB) — концерн кампании, CAMP-15 в Phase 4.
  - `document_webhook_url` (TEXT) — концерн кампании, CAMP-14 в Phase 4.
  - `max_message_length`, `response_delay_seconds` (BIGINT) — концерн кампании (per-campaign расписание/лимиты в Phase 4).
  - `is_active` (BOOLEAN) — soft-delete заменяется на hard delete (см. D-08), колонка больше не нужна.

  БД чистая (Phase 1 D-01) — `DROP COLUMN` без backfill. Миграция идемпотентна (`DROP COLUMN IF EXISTS`).

- **D-02:** Финальные колонки `ai_contexts` после миграции 015 (порядок в модели):
  `id UUID PK`, `workspace_id UUID NOT NULL FK CASCADE`, `name VARCHAR(100) NOT NULL`, `system_prompt TEXT NULLABLE`, `rules TEXT NULLABLE`, `tone_of_voice TEXT NULLABLE`, `faq JSONB DEFAULT '{}'`, `company_info TEXT NULLABLE`, `product_info TEXT NULLABLE`, `created_at`, `updated_at`.

  UNIQUE INDEX `(workspace_id, name)` — два агента в одном workspace с одинаковым именем запрещены (используется для duplicate-логики D-07).

- **D-03:** UI-маппинг success criterion #1 («контекст / задача / тон / FAQ») на DB-колонки:
  - **«Контекст»** → `system_prompt` (большой текст — общий контекст компании/продукта/ситуации).
  - **«Задача»** → `rules` (что агент должен сделать, какие правила, что НЕ делать).
  - **«Тон»** → `tone_of_voice` (стиль общения).
  - **«FAQ»** → `faq` JSONB (массив `{question, answer}` — точный shape решит planner, см. C-01).
  - Дополнительно (поверх обязательных 4 полей): `company_info` (общая информация о компании, переиспользуется в нескольких агентах) и `product_info` (общая информация о продукте). Lovable рендерит 6 текстовых полей + FAQ-редактор.

  Никакие новые колонки не добавляются — переиспользуем существующие имена, чтобы избежать «двойной» миграции.

### Cleanup старой связи sender↔agent

- **D-04:** Миграция `015_phase3.sql` делает `ALTER TABLE senders DROP COLUMN ai_context_id`. ORM-модель `Sender` тоже теряет `ai_context_id` поле и `ai_context` relationship. После миграции sender больше не «знает», какой агент к нему привязан — связь через campaign в Phase 4.

  Все упоминания `sender.ai_context_id` в коде должны быть удалены или адаптированы (planner проводит grep — текущие места: `routers/senders.py:96,97,150,184,231,247,329,330,342,438`, `services/queue.py:705`, `services/listener.py:345,359`).

- **D-05:** `conversations.ai_context_id` и таблица `context_contact_assignments` **остаются как есть** в Phase 3. Они продолжают работать в текущей AI-runtime цепочке (`services/listener.py` читает `conversation.ai_context_id`, `services/rotation.py` использует `context_contact_assignments` для маппинга `(context_id, contact_phone) → sender_id`).

  В Phase 4 при появлении Campaign:
  - `conversations.ai_context_id` либо переименуется в `agent_id`, либо рядом добавится `campaign_id` (планер Phase 4 решит).
  - `context_contact_assignments` переедет на `(campaign_id, contact_phone) → sender_id` — rotation per-campaign.

  FK `ai_contexts.id` остаются: `conversations.ai_context_id ON DELETE SET NULL` (как сейчас), `context_contact_assignments.context_id ON DELETE CASCADE` (как сейчас). Эти FK работают корректно с hard delete агента (D-08).

### Backend cleanup: queue.py / send.py / contexts.py

- **D-06:** Phase 3 переписывает три роутера и регистрирует их в `app/main.py`:
  1. **`app/routers/contexts.py`** — полный рерайт под `AuthDep`. Endpoints под `/api/v1/agents` (новый prefix), API-схемы названы `AgentCreate`, `AgentResponse`, `AgentListResponse`, `AgentUpdate` (UI/API наружу = «agent»; tag в OpenAPI `agents`). Все запросы фильтруются по `workspace_id == ctx.workspace_id` (паттерн Phase 1 D-04).
  2. **`app/routers/queue.py`** (или `send.py` — planner выберет, какой именно файл становится «entry point для отправки», старые оба не зарегистрированы) — workspace-scoped рерайт под `AuthDep`. Принимает явный `ai_context_id` в request body (n8n шлёт его сам, не выводит из sender). Валидация: агент должен существовать в том же workspace.
  3. **Регистрация в `main.py`**: `app.include_router(agents.router)` (новый), `app.include_router(send.router)` (восстановленный). После Phase 3 продукт снова отвечает на основной endpoint отправки + CRUD агентов.

- **D-07:** Эндпоинт duplicate: `POST /api/v1/agents/{id}/duplicate` без body. Backend:
  1. Читает оригинал.
  2. Генерирует имя: пробует `«{name} (copy)»`. Если уже существует — `«{name} (copy 2)»`, `«{name} (copy 3)»` и т.д. (SELECT COUNT с LIKE для нахождения свободного индекса).
  3. INSERT нового агента со всеми полями оригинала + новое имя.
  4. Возвращает `AgentResponse` нового агента (с новым id).

### Delete семантика

- **D-08:** **Hard delete** (без soft-delete флага). Endpoint `DELETE /api/v1/agents/{id}`:
  1. Workspace-scoped check: агент существует и принадлежит workspace caller'а.
  2. `DELETE FROM ai_contexts WHERE id = :id AND workspace_id = :ws_id` — каскад FK сделает остальное:
     - `conversations.ai_context_id` → NULL (FK уже SET NULL в существующей схеме).
     - `context_contact_assignments` записи удаляются (FK CASCADE).
  3. Возвращает 204 No Content.

  Старый soft-delete через `is_active=false` не используется — колонка `is_active` дропается в миграции 015 (D-01). Семантика «черновики vs активные агенты» в v1 не нужна.

- **D-09:** TODO для Phase 4: блокировать `DELETE` если агент привязан к active campaigns (как `folders` D-06 в Phase 2). В Phase 3 этой проверки нет, потому что таблицы `campaigns` ещё не существует. Planner оставляет TODO-метку `# TODO(phase-4): also block on active campaign attachment` рядом с handler'ом DELETE (точно так же как Phase 2 сделал для folders).

### Поле `campaign_count` в ответе API

- **D-10:** `AgentResponse` всегда возвращает поле `campaign_count: int = 0` (хардкод в Phase 3). Lovable рендерит колонку «In 0 campaigns» в списке агентов. Это стабилизирует контракт API с фронтом — в Phase 4 хардкод заменяется на реальный `SELECT COUNT(*) FROM campaigns WHERE agent_id = ai_contexts.id` (либо subquery в основном SELECT, либо отдельный JOIN).

  Не добавляем `usage_count` через `conversations` — это уже history-метрика для аналитики (Phase 5), не для UI списка агентов.

### Claude's Discretion

- **C-01:** Точный shape JSONB поля `faq` — массив `[{question: "...", answer: "..."}]` или dict `{question: answer}`. Planner выберет — массив более типичен для UI-редактора с порядком и DnD, dict проще для LLM prompt-инъекции. Рекомендация: массив объектов.
- **C-02:** Точные имена endpoint'ов и Pydantic-схем — planner подберёт под существующие конвенции (`schemas/__init__.py` PascalCase, `AgentCreate`, `AgentUpdate`, `AgentResponse`, `AgentListResponse`, `DuplicateAgentResponse` и т.п.). Старый файл `app/routers/contexts.py` можно либо переименовать в `agents.py`, либо переписать на месте — planner решит, что чище.
- **C-03:** Точная shape `AgentUpdate` (полный PUT или partial PATCH с Optional полями) — planner подберёт под convention. Существующие роутеры Phase 2 используют partial PATCH с Optional — продолжаем.
- **C-04:** Решение, какой именно файл становится «entry point для отправки» в D-06 (между `app/routers/send.py` и `app/routers/queue.py`). Сейчас оба существуют, оба не зарегистрированы. Planner оценит, какой ближе к workspace-scoped паттерну, и переписывает один (другой остаётся как есть либо удаляется).
- **C-05:** Адаптация `app/routers/senders.py` под удаление `sender.ai_context_id`: убрать поле из `SenderCreate`, `SenderUpdate`, `SenderResponse` и `selectinload(Sender.ai_context)` calls (строки 96, 97, 150, 184, 231, 247, 329, 330, 342, 438). Planner включает это в swap-план.
- **C-06:** Расширение `tests/conftest.py` под Phase 3 фикстуры (`agent_factory`, `mock_workspace_with_agent`) — planner добавит в pytest setup.
- **C-07:** Опциональная адаптация `services/queue.py` и `services/listener.py` (НЕ переписывать целиком — workers остаются в legacy режиме, читают `conversation.ai_context_id` как и сейчас). Если planner найдёт небольшие места, где `sender.ai_context_id` мешает миграции (например, INSERT в conversations) — заменить на чтение из request payload. Большой рерайт workers — Phase 4.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level
- `CLAUDE.md` — главные правила: raw SQL миграции 015_+, async everywhere, общение на русском, не трогать rate-limit и debounce-интервалы без явного обсуждения.
- `.planning/PROJECT.md` — Key Decisions: agent отвязан от sender'а (workspace-level template), Campaign первичная сущность (Phase 4), webhook/tools принадлежат кампании, расписание per-campaign.
- `.planning/REQUIREMENTS.md` §"Agents (Phase 3)" — AGNT-01, AGNT-02, AGNT-03, AGNT-04 (ровно 4 требования, которые должен закрыть Phase 3).
- `.planning/ROADMAP.md` §"Phase 3: Agents (AI Templates)" — Success Criteria (4 пункта) и состав плана (2 plan'а: `03-01: Agent model decoupling`, `03-02: Agent CRUD API & UI contract`).

### Phase 1 / Phase 2 контекст (must read)
- `.planning/phases/01-workspace-foundation/01-CONTEXT.md` — Phase 1 D-04 (workspace isolation pattern: `.where(workspace_id == ctx.workspace_id)`), D-11..D-14 (AuthDep / AuthCtx, dual auth JWT+API-key, выпил старого `verify_api_key` и старых роутеров из `main.py`), D-15 (`app/services/` не трогался, очередь и листенер ждут рерайта).
- `.planning/phases/02-tg-accounts-contacts/02-CONTEXT.md` — D-13 (rate limits per-sender, выпил глобальных констант из `queue.py`), D-18 (periodic reconcile loop в listener, замена `subprocess.run docker restart`), deferred §"Для Phase 3" (`senders.role` String→SQLEnum, `max_message_length / response_delay_seconds` переезд на agent/campaign).
- `.planning/phases/02-tg-accounts-contacts/02-PATTERNS.md` — рабочие паттерны workspace-scoped роутеров (если файл существует — planner проверит).

### Codebase intel
- `.planning/codebase/ARCHITECTURE.md` — слойная разбивка router→service→data; новые роутеры `agents` живут под `app/routers/`.
- `.planning/codebase/STRUCTURE.md` — где должны жить новые файлы (`app/routers/agents.py` или переименование `contexts.py`), миграции в `migrations/015_*.sql`.
- `.planning/codebase/INTEGRATIONS.md` — Telethon abstraction, AI engine, function calling (текущее состояние — для понимания, что НЕ ломается).
- `.planning/codebase/CONCERNS.md` — старый `routers/contexts.py` использует выпиленный `verify_api_key`, `is_active` как soft-delete (заменяется на hard delete в D-08), `Sender.role String(20)` без `SQLEnum` (можно адаптировать в Phase 3, см. C-08 — пока не критично).

### Существующий код (читать перед изменением)
- `app/models/__init__.py` — `AIContext` (строки 146-168, расширяется/чистится через ALTER в миграции 015 — ORM приводится в соответствие), `Sender` (строка 96 — `ai_context_id` колонка дропается, relationship удаляется), `Conversation` (строки 233, 242 — `ai_context_id` FK остаётся, не трогается), `ContextContactAssignment` (строки 334-349 — таблица остаётся, не трогается).
- `app/routers/contexts.py` — текущий старый роутер: использует выпиленный `verify_api_key`, без workspace_id фильтра, имеет лишние поля `webhook_functions`/`document_webhook_url` в Pydantic-схемах, soft-delete через `is_active`. Phase 3 переписывает полностью под AuthDep + workspace-scope + drop лишних полей + hard delete + duplicate endpoint.
- `app/routers/senders.py` — упоминания `ai_context_id` (строки 96, 97, 150, 184, 231, 247, 329, 330, 342, 438) — удалить вместе с `selectinload(Sender.ai_context)` calls, дропнуть поле из `SenderCreate/Update/Response` schemas.
- `app/routers/send.py` / `app/routers/queue.py` — старые файлы, оба не зарегистрированы в `main.py`. Один из них перерисовывается под AuthDep + explicit ai_context_id в body (см. C-04).
- `app/main.py` — добавить `app.include_router(agents.router)` и `app.include_router(send.router)` (или `queue.router`, в зависимости от C-04). Текущее состояние: только health/workspace/senders/folders/contacts/check_contacts/onboarding.
- `app/services/queue.py` — НЕ трогать (workers остаются в legacy режиме). Чтение `sender.ai_context_id` (строка 705) — заменить на чтение из request payload (мелкая правка, не рерайт) — см. C-07.
- `app/services/listener.py` — НЕ трогать архитектурно. Чтение `sender.ai_context_id` (строки 345, 359, 372, 381-390) — адаптировать чтобы не падало после DROP COLUMN: либо удалить чтение из SELECT, либо вернуть NULL (planner решит).
- `app/services/rotation.py` — НЕ трогать. `context_contact_assignments` таблица остаётся (D-05).
- `migrations/014_phase2_1_hardening.sql` — последняя миграция, следующая 015.

### AI Engine / OpenAI (внешний)
- `app/services/ai_engine.py` — текущая логика чтения `ai_contexts` через прямой SQL (строка 74). НЕ трогать в Phase 3 — продолжает работать через `conversation.ai_context_id` цепочку.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **AuthDep / AuthCtx** из Phase 1 (`app/utils/auth.py`) — новый `agents.py` роутер строится по паттерну `Depends(auth_dep)` + `where(AIContext.workspace_id == ctx.workspace_id)`.
- **Существующие workspace-scoped роутеры Phase 2** (`folders.py`, `contacts.py`) — drop-in шаблоны для CRUD-эндпоинтов, валидации (409 на дубль имени, 404 на отсутствие), ответных схем.
- **Pattern duplicate-имя** — пока нет в codebase, но похожий «name autoincrement» можно увидеть в фронт-flow `folders` Phase 2 (auto-create через `folder_name` без `folder_id`). Логика «X (copy N)» — простой SELECT с LIKE для нахождения свободного индекса (planner определит SQL точно).
- **`tests/conftest.py` Phase 1+Phase 2 фикстуры** — расширяются `agent_factory` для интеграционных тестов CRUD/duplicate/delete.

### Established Patterns

- Миграции — raw SQL, идемпотентные (`IF NOT EXISTS`, `DROP COLUMN IF EXISTS`), нумерация `015_`.
- Все запросы к новым таблицам должны иметь `.where(... .workspace_id == ctx.workspace_id)` (Phase 1 D-04). Без `RLS` (Phase 1 решение — TODO для v2).
- Pydantic v2: `model_config = ConfigDict(from_attributes=True)`, partial PATCH с Optional полями.
- API endpoints под `/api/v1/<resource>`. Resource в API-наружу = «agents», в DB = «ai_contexts» (success criterion #2 — таблица переиспользуется без переименования).
- Response через Pydantic schemas из `app/schemas/__init__.py` — добавить `AgentCreate`, `AgentUpdate`, `AgentResponse`, `AgentListResponse`.
- HTTP коды: 201 на create, 200 на read/update, 204 на delete, 404 на отсутствие, 409 на дубль уникального поля, 422 на pydantic validation, 403 на чужой workspace.

### Integration Points

- **`app/main.py`** — `app.include_router(agents.router)` и `app.include_router(send.router)` (восстанавливается). После Phase 3 продукт снова отвечает на endpoint отправки + agent CRUD.
- **`app/models/__init__.py`** — обновить `AIContext` (убрать дропнутые поля), `Sender` (убрать `ai_context_id` поле и `ai_context` relationship), `Conversation` (без изменений), `ContextContactAssignment` (без изменений).
- **`app/schemas/__init__.py`** — добавить `Agent*` Pydantic-схемы (или импортировать из `app/routers/agents.py` локально, как сделано в Phase 2 для `contacts.py`).
- **`docker-compose.yml`** — без изменений в Phase 3.

### Anti-patterns, которые НЕ повторять (из CONCERNS.md и Phase 1/2)

- НЕ использовать выпиленный `verify_api_key` — только `AuthDep`.
- НЕ возвращать workspace-данные без `where(workspace_id == ctx.workspace_id)` фильтра. Phase 1 паттерн: оставлять `# TODO(v2-rls): replaced by RLS policy app.workspace_id` метки в коде там, где могла бы быть RLS-политика.
- НЕ хранить агент-поля в `senders.ai_context_id` (поле дропается).
- НЕ добавлять soft-delete `is_active` обратно — hard delete по D-08.
- НЕ менять hardcoded debounce 3-5 мин в `listener.py` и не трогать `services/queue.py` rate-limit логику (CLAUDE.md явно запрещает).
- НЕ создавать новые таблицы без `workspace_id UUID NOT NULL FK CASCADE` — всё workspace-scoped.

</code_context>

<specifics>
## Specific Ideas

- **БД чистая** (Phase 1 D-01): миграция 015 не делает backfill / data migration. `DROP COLUMN` напрямую — данных, на которые могли бы ссылаться FK или которые могли бы быть полезны, нет.
- **API-наружу = «agent», DB = «ai_contexts»**: терминологический сдвиг происходит только на уровне FastAPI router prefix (`/api/v1/agents`), tag (`agents`), и Pydantic-схем (`AgentCreate`/`AgentResponse`). Таблица, ORM-класс `AIContext` и все internal FK имён `ai_context_id` остаются — это успех-критерий #2 «без переименования» и сохраняет совместимость с existing services/queue.py, services/listener.py, services/ai_engine.py.
- **`POST /api/v1/agents/{id}/duplicate` без body**: один POST-запрос, имя генерируется на сервере. UX = «одна кнопка → один новый агент». Если юзер хочет переименовать — делает PATCH после.
- **n8n flow после Phase 3**: внешний клиент шлёт `POST /api/v1/send` (или `/api/v1/queue/send`, имя — C-04) с body `{ai_context_id: "uuid", sender_id: "uuid" | null, contact: {...}, message: "..."}` + `X-Workspace-Key: wsk_...` header. Workspace_id извлекается из API-ключа (AuthDep), `ai_context_id` валидируется на принадлежность тому же workspace. Это и есть «agent явно в запросе» из D-06.
- **Hard delete + FK SET NULL у conversations**: после удаления агента старые conversations остаются в БД, но `ai_context_id = NULL`. Inbox в Phase 5 рендерит их как «диалог без агента» (графа аналитики покажет агрегированно через is_null). Это лучше, чем CASCADE (потеряли бы историю диалогов).
- **Hard delete + CASCADE у context_contact_assignments**: эти записи — это per-context rotation state. Без агента они бессмысленны — удаляются вместе. В Phase 4 при удалении campaign'а аналогично будут каскадно дропаться campaign-уровневые assignments.

</specifics>

<deferred>
## Deferred Ideas

### Для Phase 4 (Campaigns)
- Реальный `campaign_count` через `COUNT(*) FROM campaigns WHERE agent_id=...` — заменяет хардкод D-10.
- Блокировка `DELETE /api/v1/agents/{id}` если есть active campaigns — TODO-метка ставится в Phase 3 (D-09).
- Переезд `auto_pause_triggers` (CAMP-12 «передать на менеджера»), `webhook_functions` (CAMP-15 tools), `document_webhook_url` (близко к CAMP-14 webhook кампании) на таблицу `campaigns` с правильными именами.
- Переезд `max_message_length`, `response_delay_seconds` на `campaigns` — per-campaign лимиты в составе расписания.
- Переименование / адаптация `conversations.ai_context_id` под `campaign_id` (или добавление `campaign_id` рядом) — Phase 4 решит.
- Переезд `context_contact_assignments` на `(campaign_id, contact_phone) → sender_id` — rotation per-campaign.
- Sender lock per active campaign (CAMP-04) — Phase 4.

### Для Phase 5 (Inbox / Analytics)
- `usage_count` агента через `COUNT(DISTINCT conversation_id) FROM conversations WHERE ai_context_id = agent.id` — analytics метрика (ANLX-04).
- Лог запросов в OpenAI на уровне диалога (ANLX-05) — здесь не реализуется.

### Из Phase 2 (перенесено в "может быть в Phase 3" но решено отложить дальше)
- Перевод `senders.role` с `String(20)+CHECK` на `SQLEnum` — не блокер Phase 3. Planner может включить как небольшую чистку модели в swap-плане 03-01 миграции, если эстетически удобно. Иначе деферрим в Phase 4 или v2.

### Tech debt из Phase 1 / 2, продолжающий висеть
- `app/database.py` `Base.metadata.create_all` в `init_db()` (Phase 1 C-04) — пока без замены на runner. Не блокер Phase 3, не трогаем здесь.

### Для v2
- Soft-delete агентов с timestamp `deleted_at` (вместо удаления записей) — если клиенты захотят историю всех когда-либо созданных агентов. В v1 — hard delete (D-08).
- Версионирование агентов (одного агента несколько версий, в кампании можно «прикрутить» определённую версию) — out-of-scope.
- Шаблоны агентов для маркетплейса (готовые промпты под индустрии) — out-of-scope.

</deferred>

---

*Phase: 03-agents-ai-templates*
*Context gathered: 2026-05-22*
