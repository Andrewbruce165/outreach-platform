# Phase 11: Agent/Campaign Field Split & Prompt Assembly - Context

**Gathered:** 2026-06-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Развести поля **Агент (КТО)** и **Кампания (ЧТО)**, убрать дубли в системном промпте (один источник на блок), добавить новые поля и перестроить UI визарда. Цель — убрать перегруз и противоречивые повторы, из-за которых GPT-5 mini «плывёт».

**Источник истины по структуре полей и порядку блоков промпта:** `.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/BRIEF.md`. BRIEF фиксирует WHAT (формы Агента/Кампании §3–4, чек-лист изменений §5, шаги визарда §6, фиксированный порядок блоков промпта §7). Эта дискуссия фиксирует HOW для спорных мест.

**В скоупе (бэкенд + фронтенд — фаза охватывает ДВА репо):**
- Миграция `030_*.sql` (raw SQL, идемпотентная, авто-применяется) — новые/переименованные/удаляемые колонки в `ai_contexts` и `campaigns`.
- Модели (`app/models/__init__.py`), схемы и роутеры агентов/кампаний (`app/routers/agents.py`, `app/routers/campaigns.py`).
- Рерайт сборки системного промпта в `app/services/ai_engine.py::build_system_prompt` на фиксированный порядок блоков §7 + дедуп правил (один источник на блок).
- Чтение новых полей в `get_context_for_conversation` / `get_context`.
- Фронтенд в **отдельном репо** `aimly-tg-outreach` (Lovable, TanStack Start): реализация форм Агента/Кампании в визарде, блок поведения, редактор стадий «Ход разговора».
- Синхронизация `lovable-handoff/openapi.json` + UI-контракт.

**Не в скоупе (deferred — см. `<deferred>`):**
- Override полей агента на уровне кампании → v2.
- «Используемые базы знаний» (knowledge bases) — реальное подтягивание КБ → будущая фича.
- A/B (несколько агентов на кампанию), мульти-папки, мульти-window расписание — v2 (зафиксировано ещё в Phase 4).

</domain>

<decisions>
## Implementation Decisions

### Тон — схлопнуть до одного enum (D-01)
- **D-01:** Жёсткий cut. Новый enum-источник тона на агенте — **один** (`tone_preset`: Friendly / Professional / Direct / Casual). Удаляем **три** текущих источника: `voice_baseline` (enum), `tone` (JSONB слайдеры formal/warm/brief), `tone_of_voice` (free text).
- **D-02:** Миграция данных best-effort: маппинг существующего `voice_baseline` → новый `tone_preset`. Слайдеры (`tone` JSONB) и `tone_of_voice` — дропаются (риск низкий, проект до первого внешнего клиента — данные внутренние/тестовые). При желании planner может сохранить старые значения в бэкап-комментарий миграции, но в рантайм/промпт они не идут.
- **D-03:** В промпте блок `[ТОН]` рендерится из `tone_preset` в 1–2 строки. Тон **больше нигде** не дублируется (в частности — убрать из «Жёстких правил», BRIEF §5).

### «Ход разговора» — структурированные стадии (D-04)
- **D-04:** Хранение — **JSONB-массив стадий** на кампании, напр. `dialogue_flow: [{title, instruction}, ...]` (3–5 элементов). Не free textarea.
- **D-05:** UI — отдельный редактор стадий в форме кампании: add / remove / reorder. (Реализуется в Lovable-репо.)
- **D-06:** Рендер в промпт — блок `[ХОД РАЗГОВОРА]` как нумерованные стадии (порядок из массива). Точный формат рендера — на усмотрение реализации, но один источник = `dialogue_flow`.

### Override полей агента — отложить (D-07)
- **D-07:** В Phase 11 кампания только **выбирает** агента (`select`, существующий `agent_id`). Override полей агента на уровне кампании — **в v2**. Не добавляем `agent_overrides`, не усложняем merge-логику сборки промпта. Сборка читает агента как есть.

### Скоуп фронтенда — backend + UI (D-08)
- **D-08:** Phase 11 включает и бэкенд, и **реализацию UI** форм визарда в репо `aimly-tg-outreach`. Это означает работу в двух репозиториях в одной фазе.
- **D-09:** Обязательна синхронизация `lovable-handoff/openapi.json` после изменения схем; UI-контракт (поля + шаги визарда §6) — основа для правок фронта.
- **D-10:** **Осторожно с коммитами** — параллельно ведётся другая работа (Phase 10 — pool visibility; STATE.md показывает focus=Phase 10). Коммитить **только** файлы этой фазы поимённо (`--files ...`), НИКОГДА `git add -A` / `git add .`. Это касается обоих репо. STATE.md обновлять хирургически (он общий с параллельной работой) — при конфликте не затирать чужие изменения.

### Storage / миграция остальных полей (locked by BRIEF, captured here)
- **D-11:** «Скорость ответа» (BRIEF §3, `NEW`) — re-add типизированных колонок на агенте: `response_speed` enum (instant / human / slow / manual) + `response_delay_seconds` int для ручного режима. (Поле `response_delay_seconds` было дропнуто в миграции 015 — возвращаем.) **Открытый вопрос для research:** где это поле wire-ится в рантайм (дебаунс listener'а 3–5 мин в `app/services/listener.py`) — хранить no-op бессмысленно; researcher определяет точку подключения. См. `<deferred>`/research notes.
- **D-12:** «Аргументы и факты» (BRIEF §4, `NEW`) — новое поле кампании (textarea, факты + пары возражение→ответ). Рендер с anti-hallucination guard «только это, не выдумывай» (по аналогии с существующим `_PROMPT_PRODUCT_GUARD` в ai_engine.py).
- **D-13:** Слияния/переименования (BRIEF §5), data-migration без потери:
  - «Success criteria» → «Сигнал Лид»: смигрировать `campaigns.success_criteria` в `lead_trigger_hint` (COALESCE/concat), затем `success_criteria` дропнуть.
  - «Audience hints» → «Кому пишем»: **переименовать только в UI/API-лейбле**, колонку `audience_hints` оставить (избегаем data-migration).
  - «Задача/роль» убрать из поля «Идентичность» агента (`who_is_agent`) — это контент-гайд (label/help-text + промпт-инструкция), не схема; задача теперь живёт в кампании (Цель + Ход разговора).
- **D-14:** «Правила кампании» (BRIEF §4) — новое поле кампании (campaign-specific rules). Блок `[ПРАВИЛА]` в промпте = правила агента + правила кампании с **дедупом**.
- **D-15:** Brief auto-fill (BRIEF §4 note, §5) — остаётся только генератором черновика структурных полей. Сырой текст брифа в итоговый системный промпт **не уходит**.

### Claude's Discretion
- Точный набор enum-значений `response_speed` и дефолтные секунды для каждого режима.
- Конкретный JSON-формат элемента `dialogue_flow` (ключи `title`/`instruction` vs `stage`/`do`).
- Формат рендера каждого блока промпта (текст guard'ов, разделители) — при условии «один источник на блок» и порядка из BRIEF §7.
- Маппинг `voice_baseline` → `tone_preset` (какое старое значение в какой пресет).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Источник истины фазы
- `.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/BRIEF.md` — формы Агента (§3) и Кампании (§4), чек-лист изменений (§5), шаги визарда (§6), **фиксированный порядок блоков системного промпта (§7)**, заметки по UI (§8), открытые вопросы (§«Открытые вопросы»).

### Сборка промпта и контекст диалога
- `app/services/ai_engine.py` — `build_system_prompt` (строка ~559, текущая сборка блоков `<role>/<company>/<product>/<tone>/<rules>/<tools>...`), `get_context_for_conversation` (~147), `get_context` (~50). Все три переписываются под новые поля и порядок §7.
- `app/services/listener.py` — дебаунс входящих (точка возможного wire-апа `response_speed`, D-11).

### Схема / миграции (паттерн raw SQL, авто-applier)
- `migrations/015_phase3.sql` — что дропнули у агента (`response_delay_seconds`, `max_message_length` потом вернули, `tone`-related история).
- `migrations/016_phase4.sql` — создание `campaigns` (тек. поля: `audience_hints`, `success_criteria`, `primary_goal`, `*_trigger_hint`, `tools`).
- `migrations/029_campaign_pause_reason.sql` — последняя миграция; новая будет `030_*`.
- `app/models/__init__.py` — ORM-модели `AIContext`/`Campaign` (строка ~184 — комментарий про дропнутые поля).
- `CLAUDE.md` (корень проекта `/root/apps/aimly/tg-outreach/CLAUDE.md`) — правила миграций (идемпотентность, fail-fast applier), rate-limit/queue константы НЕ трогать, тесты только через test-overlay.

### Прошлые решения по агенту/кампании
- `.planning/phases/03-agents-ai-templates/03-CONTEXT.md` — модель агента (ai_contexts), v2-поля.
- `.planning/phases/04-campaigns/04-CONTEXT.md` — модель кампании, связи, что НЕ трогать в queue.py.

### Фронтенд (отдельный репо)
- `/root/apps/aimly/aimly-tg-outreach` — Lovable-репо (TanStack Start), origin `AGS-Venture-Lab/aimly-tg-outreach`. Визард, формы Агента/Кампании.
- `lovable-handoff/openapi.json` — контракт для генерации/правки фронта; синхронизировать после изменения схем (D-09).
- `.planning/codebase/` — карта обоих репо как единой системы.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `build_system_prompt` (ai_engine.py:559) — уже блочная сборка с условным рендером непустых полей; рерайт = перетасовка/дедуп блоков под §7, а не с нуля.
- `_PROMPT_PRODUCT_GUARD` / `_PROMPT_*` константы (ai_engine.py) — готовый паттерн anti-hallucination guard для блока «Аргументы и факты» (D-12).
- Существующий enum-паттерн `voice_baseline` с CHECK-constraint (`ai_contexts_voice_baseline_check`) — образец для `tone_preset` и `response_speed`.
- JSONB-паттерны уже в проде (`tone`, `banlist`, `qa_pairs`, `tools`) — образец для `dialogue_flow` JSONB.

### Established Patterns
- Миграции: только raw SQL `NNN_short_name.sql`, идемпотентные, авто-применяются при старте api (fail-fast). Никогда Alembic.
- Async everywhere, AsyncSession.
- Условный рендер блоков промпта: блок появляется только если поле непустое.

### Integration Points
- `conversations.campaign_id` → JOIN до `campaigns` (источник полей кампании в сборке промпта).
- `campaigns.agent_id` → `ai_contexts` (источник полей агента).
- openapi.json → Lovable-фронт.

</code_context>

<specifics>
## Specific Ideas

- Принцип BRIEF: **одно поле = одна мысль, ноль пересечений между слоями**. Агент = КТО (стабильная личность, без задачи и без хода разговора). Кампания = ЧТО (задача, ход разговора, цель, факты).
- Порядок блоков промпта строго по BRIEF §7: ИДЕНТИЧНОСТЬ → КОМПАНИЯ → ПРОДУКТ → ТОН → ЗАДАЧА+КОМУ ПИШЕМ → ХОД РАЗГОВОРА → АРГУМЕНТЫ И ФАКТЫ → (БАЗА ЗНАНИЙ, future) → ПРАВИЛА → СИГНАЛЫ → ФОРМАТ ОТВЕТА.
- Цель — поведенческая: модель перестаёт получать одну мысль 2–3 раза с противоречиями.

</specifics>

<deferred>
## Deferred Ideas

- **Override полей агента на уровне кампании** (D-07) — v2. BRIEF упоминает как «опц. override»; не делаем в Phase 11, чтобы не усложнять merge в сборке промпта.
- **«Используемые базы знаний» (knowledge bases)** — будущая фича. В Phase 11 НЕ wire-им реальное подтягивание КБ и не добавляем `[БАЗА ЗНАНИИ]` контент в промпт. UI может показать disabled «coming soon» multi-select (на усмотрение планировщика), но без бэкенд-колонки/логики. Полноценно — отдельная фаза.
- **Wire `response_speed` в дебаунс listener'а** — если researcher решит, что подключение к рантайму выходит за объём Phase 11, поле хранится, а wire-ап выносится в отдельную задачу/фазу. Базово — пытаемся wire-ить в этой фазе (D-11).

### Reviewed Todos (not folded)
None — todos не проверялись (нет совпадений по фазе).

</deferred>

---

*Phase: 11-agent-campaign-field-split-and-prompt-assembly*
*Context gathered: 2026-06-24*
