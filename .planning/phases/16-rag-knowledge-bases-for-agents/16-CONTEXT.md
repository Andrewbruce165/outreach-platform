# Phase 16: RAG Knowledge Bases for Agents - Context

**Gathered:** 2026-06-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Дать пользователю настоящую RAG-базу знаний для AI-агентов:

1. **Вкладка Knowledge Bases** в UI — пользователь создаёт изолированные KB и загружает в каждую свои данные.
2. **Подключение KB на уровне агента** — у агента может быть несколько KB; во время генерации ответа агент достаёт релевантные куски **по необходимости** и использует их в ответе.

KB строго workspace-scoped. В рамках фазы: модель данных, ingest-пайплайн (загрузка → парсинг → чанкинг → эмбеддинги в pgvector), retrieval как tool-call, привязка KB↔агент (M:N), UI-вкладка управления KB.

**Вне фазы:** уровень кампании для KB, URL/сайт-краулинг, аналитика по использованию KB, шаринг KB между workspace — отдельные фазы (см. Deferred).
</domain>

<decisions>
## Implementation Decisions

### Источники данных KB (ingest)
- **D-01:** v1 поддерживает **вставку текста вручную + загрузку файлов** (PDF, DOCX, TXT, MD, CSV). URL/сайт-краулинг в v1 НЕ делаем (отложено).
- **D-02:** Пайплайн ingest: загрузка источника → извлечение текста по типу файла → чанкинг → эмбеддинги → запись чанков с векторами в pgvector. Загрузка файла идёт через multipart (паттерн уже есть — см. code_context).

### Retrieval (когда агент идёт в KB)
- **D-03:** Retrieval реализуется как **tool-call** — агент сам решает вызвать инструмент `search_knowledge_base`, когда сочтёт нужным («по необходимости»). НЕ авто-retrieval на каждое сообщение.
- **D-04:** Tool `search_knowledge_base` доступен агенту тогда и только тогда, когда у агента есть хотя бы одна подключённая KB. Вызов ищет по **всем подключённым к агенту KB** (один поиск по объединённому набору). Результат возвращается модели как tool-result сообщение (данные), затем модель продолжает ответ — это НЕ signal-tool (в отличие от mark_as_lead/transfer_to_manager/finish_conversation, которые меняют состояние диалога).

### Хранилище / поиск
- **D-05:** Векторы хранятся в **pgvector в текущем PostgreSQL 16** (та же БД, workspace-изоляция и бэкапы бесплатно, не плодим инфру). Образ db меняется на `pgvector/pgvector:pg16` (или `CREATE EXTENSION vector`), миграция идемпотентная.
- **D-06:** Модель эмбеддингов — **OpenAI `text-embedding-3-small` (1536 измерений)**. Размерность фиксирует тип vector-колонки. Имя модели вынести в env-knob (`OPENAI_EMBEDDING_MODEL`) на будущее, дефолт — 3-small.

### Привязка KB ↔ агент
- **D-07:** Связь **многие-ко-многим агент↔KB** (таблица `agent_knowledge_bases`). KB вешается **только на уровень агента** (не кампании в v1). KB переиспользуемы между агентами в пределах workspace.

### Существующее статическое поле knowledge_base
- **D-08:** Статическая Text-колонка `ai_contexts.knowledge_base` (Phase 11) **остаётся рядом** с новой RAG-механикой. Статический блок продолжает занимать слот `[БАЗА ЗНАНИЙ]` в системном промпте (короткие факты, всегда в промпте); RAG KB — отдельный механизм через tool-call. Существующих агентов не ломаем, миграции содержимого нет.

### Claude's Discretion
- **UX статуса индексации** — пользователь оставил на усмотрение планировщика/UI-фазы. Рекомендация: статус на каждый документ (загружается / обрабатывается / готов / ошибка) + кол-во чанков; но финальный контракт — за планировщиком.
- Стратегия чанкинга (размер/overlap), top-K и порог в `search_knowledge_base`, формат tool-result, индекс pgvector (HNSW vs IVFFlat), обработка пустого результата поиска (наследовать существующее off-topic-поведение ai_engine), извлечение текста из PDF/DOCX (выбор библиотек) — детали реализации, решает research/plan.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

Внешних ADR/спеков у проекта нет — требования зафиксированы в решениях выше и в planning-артефактах ниже.

### Точка инжекта знаний в промпт (Phase 11)
- `.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/11-CONTEXT.md` — KB явно отложены сюда; порядок блоков промпта; статус поля `knowledge_base`.
- `.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/BRIEF.md` §7 — канонический порядок блоков системного промпта (слот «БАЗА ЗНАНИЙ» между «АРГУМЕНТЫ И ФАКТЫ» и «ПРАВИЛА»).
- `app/services/ai_engine.py` — `build_system_prompt` (~стр. 731), комментарий с порядком блоков и маркером `[БАЗА ЗНАНИЙ: deferred, skip]` (~стр. 745).

### Агенты / AIContext (Phase 3)
- `.planning/phases/03-agents-ai-templates/03-CONTEXT.md` — агент как workspace-шаблон; точка привязки KB.
- `app/models/__init__.py` — `class AIContext` (~стр. 203), колонка `knowledge_base` (~стр. 221).

### Built-in tools / function calling (Phase 4)
- `app/services/ai_engine.py` — `BUILT_IN_TOOL_NAMES` (~стр. 45), `build_builtin_tools(campaign)` (~стр. 85), dispatch по `tool_call.function.name`. Паттерн для добавления `search_knowledge_base` (но это data-tool, не signal-tool).

### Проектные правила
- `CLAUDE.md` (корень `tg-outreach`) — raw-SQL идемпотентные миграции (авто-применяются при старте api), async everywhere, запрет Alembic, тесты только через test-overlay, сетевая топология/деплой.
- `.planning/codebase/STACK.md` — стек (PostgreSQL 16, SQLAlchemy 2.0 async, OpenAI-клиент, multipart-upload).
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **Multipart-загрузка файлов:** `app/routers/contacts.py` — `UploadFile = File(...)` + парсинг + хранение blob (CSV import/preview ~стр. 298–307). Прямой паттерн для загрузки файлов в KB.
- **Built-in tools / function calling:** `app/services/ai_engine.py::build_builtin_tools` + `BUILT_IN_TOOL_NAMES` + dispatch по имени tool_call. `search_knowledge_base` регистрируется похоже, но возвращает данные (tool-result), а не меняет состояние диалога.
- **Слот в промпте:** `build_system_prompt` уже резервирует место под БАЗУ ЗНАНИЙ — статический `knowledge_base` заполняет его; RAG приходит отдельно через tool-result.
- **OpenAI-клиент:** `app/services/ai_engine.py` (AsyncOpenAI) — переиспользуем для embeddings (`text-embedding-3-small`).
- **Фоновые воркеры:** lifespan в `app/main.py` (~стр. 47) запускает воркеры (CampaignEnqueueWorker, WarmupWorker, contact_check_worker). Ingest/embedding-воркер кладётся по тому же паттерну (асинхронная обработка загруженных документов вне HTTP-запроса).
- **Raw-SQL миграции:** `migrations/NNN_*.sql` (последняя 039) — авто-применяются, идемпотентны. Новые таблицы + pgvector-extension добавляются миграцией(ями) ~040+.

### Established Patterns
- **Мультитенантность:** всё через `workspace_id`, эндпоинты под `AuthDep`. KB/документы/чанки — workspace-scoped; `search_knowledge_base` фильтрует по workspace и подключённым к агенту KB.
- **Async everywhere:** AsyncSession, без блокирующих вызовов. Парсинг файлов/эмбеддинги — в фоновом воркере, не в обработчике запроса.
- **Tool dispatch:** signal-tools имеют приоритет (finish>handoff>lead); `search_knowledge_base` — отдельный data-путь (вернуть чанки → дать модели договорить).

### Integration Points
- **Схема БД:** новые таблицы `knowledge_bases`, `kb_documents`, `kb_chunks` (vector-колонка), `agent_knowledge_bases` (M:N). pgvector-extension.
- **AI-движок:** регистрация `search_knowledge_base` в наборе tools при наличии у агента KB; ветка обработки tool-result.
- **Агент-форма (фронт):** существующая форма агента (Phase 11) — добавить multi-select подключения KB (в Phase 11 был disabled «coming soon»).
- **Фронтенд — отдельный репо** `/root/apps/aimly/aimly-tg-outreach` (origin `AGS-Venture-Lab/aimly-tg-outreach`), генерится Lovable из `lovable-handoff/openapi.json`. Новая вкладка Knowledge Bases + KB-management UI — кросс-репо работа; вероятно понадобится `/gsd:ui-phase 16` для UI-контракта.
</code_context>

<specifics>
## Specific Ideas

- Пользователь сформулировал поведение дословно: «агент **ходит в неё по необходимости**» → это и закрепили как tool-call (`search_knowledge_base`), а не безусловный retrieval.
- «Отдельные KB, в которые загружает данные» → KB — самостоятельная сущность с собственным набором документов, а не поле агента.
</specifics>

<deferred>
## Deferred Ideas

- **URL / сайт-краулинг как источник KB** — отложено из v1 (требует скрейпинга + чистки HTML). Кандидат на следующую итерацию.
- **KB на уровне кампании** (переопределение/добавление набора KB кампанией) — в v1 только агент. Отдельная фаза при необходимости.
- **Замена/миграция статического поля `knowledge_base` на RAG** — сейчас «оставить рядом»; консолидация механизмов знаний — потенциальная будущая уборка.
- **Аналитика использования KB** (какие чанки/документы реально влияли на ответы) — отдельно.
- **Шаринг KB между workspace / маркетплейс готовых баз** — вне мультитенантной модели v1.

None отклонённых todo — совпадений с бэклогом по фазе не было.
</deferred>

---

*Phase: 16-rag-knowledge-bases-for-agents*
*Context gathered: 2026-06-30*
