# Phase 11: Agent/Campaign Field Split & Prompt Assembly — Research

**Researched:** 2026-06-24
**Domain:** Brownfield schema refactor + LLM system-prompt assembly rewrite + cross-repo Lovable wizard rebuild (FastAPI/SQLAlchemy/Postgres backend + TanStack Start frontend)
**Confidence:** HIGH (всё подтверждено чтением живого кода и прод-схемы; внешние библиотеки не вводятся)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01 (Тон — один enum):** Жёсткий cut. Новый источник тона на агенте — **один** `tone_preset` enum (Friendly / Professional / Direct / Casual). Удаляем **три** текущих источника: `voice_baseline` (enum), `tone` (JSONB слайдеры formal/warm/brief), `tone_of_voice` (free text).
- **D-02 (Миграция тона):** Best-effort маппинг `voice_baseline` → `tone_preset`. Слайдеры (`tone` JSONB) и `tone_of_voice` дропаются (риск низкий — данные внутренние/тестовые). Planner может сохранить старые значения в бэкап-комментарий миграции, но в рантайм/промпт они не идут.
- **D-03:** Блок `[ТОН]` рендерится из `tone_preset` в 1–2 строки. Тон **больше нигде** не дублируется (убрать из «Жёстких правил»).
- **D-04 (Ход разговора):** Хранение — **JSONB-массив стадий** на кампании, напр. `dialogue_flow: [{title, instruction}, ...]` (3–5 элементов). Не free textarea.
- **D-05:** UI — отдельный редактор стадий: add / remove / reorder (в Lovable-репо).
- **D-06:** Блок `[ХОД РАЗГОВОРА]` — нумерованные стадии (порядок из массива). Один источник = `dialogue_flow`.
- **D-07 (Override — отложить):** В Phase 11 кампания только **выбирает** агента (`select`, существующий `agent_id`). Override полей агента — **v2**. Не добавляем `agent_overrides`, не усложняем merge. Сборка читает агента как есть.
- **D-08 (Скоуп):** Phase 11 включает бэкенд + **реализацию UI** форм визарда в репо `aimly-tg-outreach`. Работа в двух репозиториях.
- **D-09:** Обязательна синхронизация `lovable-handoff/openapi.json` после изменения схем; UI-контракт (поля + шаги §6) — основа для правок фронта.
- **D-10 (Коммиты):** Коммитить **только** файлы этой фазы поимённо (`--files ...`), НИКОГДА `git add -A` / `git add .`. Касается **обоих** репо. STATE.md обновлять хирургически (параллельно Phase 10).
- **D-11 (Скорость ответа):** Re-add типизированных колонок на агенте: `response_speed` enum (instant / human / slow / manual) + `response_delay_seconds` int. Открытый вопрос: где wire-ится в рантайм (дебаунс listener'а). Базово — пытаемся wire-ить (см. research notes ниже).
- **D-12 (Аргументы и факты):** Новое поле кампании (textarea, факты + пары возражение→ответ). Рендер с anti-hallucination guard «только это, не выдумывай» (по аналогии с `_PROMPT_PRODUCT_GUARD`).
- **D-13 (Слияния/переименования):**
  - «Success criteria» → «Сигнал Лид»: смигрировать `campaigns.success_criteria` в `lead_trigger_hint` (COALESCE/concat), затем `success_criteria` дропнуть.
  - «Audience hints» → «Кому пишем»: **переименовать только в UI/API-лейбле**, колонку `audience_hints` оставить.
  - «Задача/роль» убрать из поля «Идентичность» агента (`who_is_agent`) — контент-гайд (label/help-text + промпт-инструкция), не схема.
- **D-14 (Правила кампании):** Новое поле кампании. Блок `[ПРАВИЛА]` = правила агента + правила кампании с **дедупом**.
- **D-15 (Brief auto-fill):** Остаётся только генератором черновика структурных полей. Сырой текст брифа в итоговый системный промпт **не уходит**.

### Claude's Discretion

- Точный набор enum-значений `response_speed` и дефолтные секунды для каждого режима.
- Конкретный JSON-формат элемента `dialogue_flow` (`title`/`instruction` vs `stage`/`do`).
- Формат рендера каждого блока промпта (текст guard'ов, разделители) — при «один источник на блок» и порядке §7.
- Маппинг `voice_baseline` → `tone_preset`.

### Deferred Ideas (OUT OF SCOPE)

- **Override полей агента на уровне кампании** (D-07) — v2.
- **«Используемые базы знаний» (knowledge bases)** — будущая фича. НЕ wire-им реальное подтягивание КБ, не добавляем `[БАЗА ЗНАНИЙ]` контент в промпт. UI может показать disabled «coming soon» multi-select (на усмотрение планировщика), без бэкенд-колонки/логики.
- **Wire `response_speed` в дебаунс** — если выходит за объём, поле хранится, wire-ап выносится. (Research recommendation ниже: wire-ап **выполним и дёшев** — см. §"Runtime State / response_speed".)
</user_constraints>

## Project Constraints (from CLAUDE.md)

Treat with same authority as locked decisions:

- **Миграции:** только raw SQL `NNN_short_name.sql`, **идемпотентные** (`IF NOT EXISTS`, `DROP COLUMN IF EXISTS`, `DROP CONSTRAINT IF EXISTS`, `DO $$ … EXCEPTION duplicate_object $$`, `ON CONFLICT DO NOTHING`). Авто-применяются при старте api через `app/database.py::_apply_migrations` в **лексическом** порядке под `pg_advisory_lock`. **Fail-fast** — если миграция падает, api не стартует. **Никогда Alembic.** Новая миграция = `030_*.sql`.
- **Async everywhere:** все DB через `async/await` + `AsyncSession`. Никогда `time.sleep()`, синхронный `requests`, `print()`.
- **НЕ трогать** rate-limit / debounce / long-pause / flood-threshold константы в `queue.py` — эмпирически подобраны. ВНИМАНИЕ: `response_speed` wire-ап касается `DEBOUNCE_MIN/MAX` в `listener.py` — это **AI-debounce реакции на входящие**, НЕ rate-limit очереди отправки. Это разные подсистемы (см. §"Runtime State"). D-11 явно разрешает менять точку дебаунса listener'а.
- **Тесты:** ТОЛЬКО через test-overlay:
  ```
  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest
  ```
  Никогда `docker compose run --rm api pytest` без overlay (DATABASE_URL уйдёт в прод → conftest DROP SCHEMA). Никогда `down -v` (удаляет прод-volume).
- **Коммиты:** перед любым изменением (кроме typo/rename/docstring) — объяснить по-русски, дождаться подтверждения. Общение русский, код/коммиты английский.
- **Безопасность:** API_KEY не в логах; промпты логируются только в `llm_calls` (Phase 5), не в общий logger.
- **Деплой бэкенда:** `docker compose up -d --build api` (и `listener`) — `restart` не подхватывает код.

## Summary

Phase 11 — чисто внутренний рефактор без новых внешних зависимостей. Вся работа сводится к (1) миграции `030_*.sql` на колонки `ai_contexts`/`campaigns`, (2) переписыванию **двух** функций в `app/services/ai_engine.py` — `build_system_prompt` (line 559) и SELECT внутри `get_context_for_conversation` (line 147), (3) обновлению Pydantic-схем + роутеров `agents.py`/`campaigns.py`, (4) опциональному wire-апу `response_speed` в `listener.py::schedule_ai_response` (line 208/229), (5) перестройке форм Agent/Campaign в Lovable-репо `aimly-tg-outreach` + регенерации `openapi.json`.

Ключевая находка: **поля, которые BRIEF/CONTEXT называют "текущими источниками тона", это НЕ Phase-3 поля, а Phase-05.1 v2 поля** (миграция 018). Прод-схема (проверена 2026-06-24) подтверждает: `ai_contexts` имеет `who_is_agent`, `company_knowledge`, `knowledge_base`, `voice_baseline`, `tone` (JSONB), `tone_of_voice`, `mirror_language`, `allow_emoji`, `banlist`, `qa_pairs`, `max_message_length`, `auto_pause_triggers`, `auto_pause_scope` — поверх legacy `system_prompt`/`company_info`/`product_info`/`rules`/`faq`. `build_system_prompt` уже COALESCE'ит new↔legacy. Это значит: Phase 11 переписывает ПОВЕРХ 05.1, а не Phase 3 — planner должен читать живую схему, не CONTEXT.md Phase 3.

**Primary recommendation:** Одна идемпотентная миграция `030_*.sql` (ADD `tone_preset`, `response_speed`, re-ADD `response_delay_seconds`, ADD `dialogue_flow` JSONB, `arguments_facts` TEXT, `campaign_rules` TEXT; data-migrate `voice_baseline`→`tone_preset` и `success_criteria`→`lead_trigger_hint`; DROP `voice_baseline`/`tone`/`tone_of_voice`/`success_criteria` + их CHECK-и). Затем полный rewrite `build_system_prompt` под фиксированный порядок §7 с дедупом `[ПРАВИЛА]`. Wire `response_speed` в `schedule_ai_response` (дёшево). Frontend — перестроить шаги Brief+Agent визарда, добавить редактор стадий. Тесты — golden-prompt assertion (точный порядок блоков + «нет дубля») как поведенческое ядро Nyquist.

## Phase Requirements

> Phase 11 requirements derived here (REQUIREMENTS.md помечает их TBD). Planner маппит планы на эти ID. Группы: FLD (поля/миграция), PMT (prompt assembly), MIG (data migration), RT (runtime), UI (frontend).

| ID | Description | Research Support |
|----|-------------|------------------|
| **FLD-01** | Добавить `ai_contexts.tone_preset` enum (Friendly/Professional/Direct/Casual) + CHECK, как единственный источник тона | D-01; образец CHECK — `ai_contexts_voice_baseline_check` (mig 018:33) |
| **FLD-02** | Re-add `ai_contexts.response_speed` enum (instant/human/slow/manual) + CHECK | D-11; образец enum-CHECK тот же |
| **FLD-03** | Re-add `ai_contexts.response_delay_seconds` INT (дропнут mig 015:16) | D-11; mig 015 строка 16 |
| **FLD-04** | Добавить `campaigns.dialogue_flow` JSONB DEFAULT `[]` (массив `{title,instruction}`) | D-04; JSONB-паттерн `campaigns.tools` (mig 016:37) |
| **FLD-05** | Добавить `campaigns.arguments_facts` TEXT | D-12 |
| **FLD-06** | Добавить `campaigns.campaign_rules` TEXT | D-14 |
| **MIG-01** | Data-migrate `voice_baseline` → `tone_preset` (best-effort mapping), затем DROP `voice_baseline` + CHECK | D-01/D-02 |
| **MIG-02** | DROP `ai_contexts.tone` (JSONB слайдеры) и `ai_contexts.tone_of_voice` (опц. backup-comment) | D-01/D-02 |
| **MIG-03** | Data-migrate `campaigns.success_criteria` → `lead_trigger_hint` (COALESCE/concat не теряя existing hint), затем DROP `success_criteria` + CHECK не задействован | D-13 |
| **PMT-01** | Rewrite `build_system_prompt` под фиксированный порядок §7: ИДЕНТИЧНОСТЬ→КОМПАНИЯ→ПРОДУКТ→ТОН→ЗАДАЧА+КОМУ ПИШЕМ→ХОД РАЗГОВОРА→АРГУМЕНТЫ И ФАКТЫ→ПРАВИЛА→СИГНАЛЫ→ФОРМАТ ОТВЕТА | BRIEF §7; текущая сборка ai_engine.py:559-713 |
| **PMT-02** | Блок `[ТОН]` рендерится ТОЛЬКО из `tone_preset` (1–2 строки); удалить старую `<tone>`-сборку из voice_baseline/tone_spec/tone_of_voice | D-03; ai_engine.py:614-635 |
| **PMT-03** | Блок `[ХОД РАЗГОВОРА]` — нумерованные стадии из `dialogue_flow`; **заменяет** статический `_PROMPT_DIALOGUE_GOAL` | D-06; ai_engine.py:402-408, 649-651 |
| **PMT-04** | Блок `[АРГУМЕНТЫ И ФАКТЫ]` — `arguments_facts` + anti-hallucination guard | D-12; `_PROMPT_PRODUCT_GUARD` ai_engine.py:397-400 |
| **PMT-05** | Блок `[ПРАВИЛА]` = правила агента (`rules`) + `campaign_rules` с **дедупом** (нормализация + set) | D-14 |
| **PMT-06** | `[ЗАДАЧА КАМПАНИИ + КОМУ ПИШЕМ]` рендерится из `primary_goal` + `audience_hints` (Кампания); убрать задачу/роль из `who_is_agent` (контент-гайд) | D-13; BRIEF §3/§5 |
| **PMT-07** | Brief raw-текст НЕ попадает в промпт (auto-fill только заполняет структурные поля) | D-15 |
| **RT-01** | Wire `response_speed`/`response_delay_seconds` в `listener.py::schedule_ai_response` (мгновенно/как-человек/медленно/ручной-delay) | D-11; listener.py:208-233 (см. §Runtime) |
| **UI-FLD-01** | Перестроить форму Agent в визарде: Идентичность, Компания, Что продаёт, Тон (select из 4), Жёсткие правила, Скорость ответа (enum+ручной ввод), Макс. длина | BRIEF §3; aimly-tg-outreach `campaigns.new.tsx` agent-step + `agents.tsx` |
| **UI-FLD-02** | Перестроить форму Campaign: Кому пишем (rename label), Цель, **редактор стадий «Ход разговора»** (add/remove/reorder), Аргументы и факты, Правила кампании, 3 сигнала | BRIEF §4/§6; D-05 |
| **UI-FLD-03** | Синхронизировать `lovable-handoff/openapi.json` + `types/api.ts` через `scripts/export-handoff.sh` | D-09 |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | в проде | API/роутеры agents.py/campaigns.py | уже стек проекта (CLAUDE.md) |
| SQLAlchemy 2.0 async | в проде | ORM `AIContext`/`Campaign` | async everywhere |
| PostgreSQL | 16 | JSONB `dialogue_flow`/`tools`, ARRAY, CHECK | уже стек |
| Pydantic | v2 | схемы Agent/Campaign (`model_config=ConfigDict(from_attributes=True)`) | существующий паттерн |
| openai (AsyncOpenAI) | в проде | model `gpt-5-mini-2025-08-07` (config.py:54) — НЕ трогать | существующий |

### Frontend (репо aimly-tg-outreach)
| Library | Purpose | Where |
|---------|---------|-------|
| TanStack Start + React + TS + Vite + bun | визард | `src/routes/_authenticated/campaigns.new.tsx` (1903 строки, STEPS на line 45) |
| shadcn/ui | формы/поля | `src/components/ui/` |
| openapi-typescript@7 | генерация `types/api.ts` из openapi.json | `scripts/export-handoff.sh:49` |

**НЕ вводятся новые библиотеки.** Для редактора стадий «Ход разговора» (add/remove/reorder) — нативный React state + кнопки вверх/вниз достаточно; drag-and-drop библиотека НЕ нужна для v1 (3–5 элементов).

**Установка:** ничего ставить не нужно — всё уже в проде.

## Architecture Patterns

### Recommended structure (где что трогаем)
```
tg-outreach/                       (backend, Andrewbruce165/outreach-platform)
├── migrations/030_*.sql           # НОВАЯ — idempotent raw SQL
├── app/models/__init__.py         # AIContext:152, Campaign:465 — ORM колонки
├── app/schemas/__init__.py        # AgentCreate:451, CampaignCreate:604, ToneSpec:438
├── app/routers/agents.py          # 61-71/212-221/283-388 — поля в CRUD
├── app/routers/campaigns.py       # 258-265/365-372/780-787 — поля в CRUD
├── app/services/ai_engine.py      # build_system_prompt:559, get_context_for_conversation:147
├── app/services/listener.py       # schedule_ai_response:208, DEBOUNCE_*:133-135 (RT-01)
├── tests/conftest.py              # migration list:127-150 — ДОБАВИТЬ 028,029,030
├── tests/test_ai_engine.py        # golden-prompt assertions
└── scripts/export-handoff.sh      # регенерация openapi.json (UI-FLD-03)

aimly-tg-outreach/                 (frontend, AGS-Venture-Lab/aimly-tg-outreach)
├── src/routes/_authenticated/campaigns.new.tsx   # визард STEPS:45, agent/campaign steps
├── src/routes/_authenticated/agents.tsx          # standalone agent CRUD
├── src/components/EditCampaignModal.tsx           # редактирование кампании
└── lovable-handoff/openapi.json + types/api.ts    # контракт (в backend-репо!)
```

> **Важно:** `lovable-handoff/` физически живёт в backend-репо `tg-outreach`, но потребляется фронтом. Frontend генерится Lovable из этого openapi.json. Это два независимых git-remote (D-10).

### Pattern 1: Block-conditional prompt assembly (СУЩЕСТВУЕТ — переиспользуем)
**What:** `build_system_prompt` собирает `blocks: list[str]`, каждый блок добавляется только если поле непустое, в конце `"\n\n".join(blocks)`.
**When:** rewrite под §7 — меняется ПОРЯДОК append'ов и ИСТОЧНИК каждого блока, скелет тот же.
```python
# Source: app/services/ai_engine.py:597-713 (текущая структура)
blocks: list[str] = []
blocks.append("<role>\n" + ... + "\n</role>")     # ИДЕНТИЧНОСТЬ
if company_knowledge: blocks.append(f"<company>\n{...}\n</company>")
if knowledge_base: blocks.append(f"<product>\n{...}\n\n{_PROMPT_PRODUCT_GUARD}\n</product>")
# ... rewrite: tone из tone_preset, dialogue_flow вместо _PROMPT_DIALOGUE_GOAL, dedup rules
return "\n\n".join(blocks)
```

### Pattern 2: COALESCE(new, legacy) в SELECT (СУЩЕСТВУЕТ)
**What:** `get_context_for_conversation` SELECT'ит `COALESCE(a.who_is_agent, a.system_prompt) AS system_prompt` и т.д. — чтобы 05.1-агенты и Phase-3-агенты давали одинаковые ключи dict.
**When:** Phase 11 убирает COALESCE для тона (источник теперь один `tone_preset`), добавляет `response_speed`/`response_delay_seconds`/`dialogue_flow`/`arguments_facts`/`campaign_rules` в SELECT.
```sql
-- Source: app/services/ai_engine.py:174-211 (текущий SELECT)
-- ПОСЛЕ Phase 11: a.tone_preset (вместо voice_baseline/tone/tone_of_voice),
-- a.response_speed, a.response_delay_seconds,
-- c.dialogue_flow, c.arguments_facts, c.campaign_rules
```

### Pattern 3: Idempotent migration (СУЩЕСТВУЕТ — обязателен)
```sql
-- Source: migrations/018_phase5_1.sql + 016_phase4.sql
BEGIN;
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS tone_preset VARCHAR(20);
ALTER TABLE ai_contexts DROP CONSTRAINT IF EXISTS ai_contexts_tone_preset_check;
ALTER TABLE ai_contexts ADD CONSTRAINT ai_contexts_tone_preset_check
    CHECK (tone_preset IS NULL OR tone_preset IN ('Friendly','Professional','Direct','Casual'));
-- data-migrate ПЕРЕД drop:
UPDATE ai_contexts SET tone_preset = CASE voice_baseline
    WHEN 'Professional' THEN 'Professional' WHEN 'Friendly' THEN 'Friendly'
    WHEN 'Playful' THEN 'Casual' ELSE tone_preset END
    WHERE tone_preset IS NULL AND voice_baseline IS NOT NULL;
ALTER TABLE ai_contexts DROP CONSTRAINT IF EXISTS ai_contexts_voice_baseline_check;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS voice_baseline;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS tone;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS tone_of_voice;
COMMIT;
```
> Note: `ALTER TYPE ADD VALUE` нельзя в транзакции (AUDIT Q6, mig 016) — поэтому enum'ы делаем VARCHAR+CHECK, не SQLEnum. Это уже устоявшийся паттерн (`campaigns.status`, `voice_baseline`, `primary_goal`).

### Anti-Patterns to Avoid
- **SQLEnum для новых enum-полей** — используй VARCHAR(20)+CHECK (ALTER TYPE blocks transactions). Прецеденты: status, voice_baseline, primary_goal, auto_pause_scope.
- **Дубль тона в `[ПРАВИЛА]`** — D-03 явно запрещает; именно это «плывёт» GPT-5 mini.
- **Raw brief-текст в промпт** — D-15.
- **Менять MIN_SEND_INTERVAL/LONG_PAUSE/FLOOD в queue.py** — CLAUDE.md (это НЕ то же что debounce в listener.py).
- **`git add -A`** в любом из двух репо — D-10.
- **Хардкод "Europe/Moscow"/AGS Foods/бренд** в новом коде — `DEFAULT_SYSTEM_PROMPT` brand-leak уже выпилен, не возвращать.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Применение миграций | свой runner / psql -f вручную | `app/database.py::_apply_migrations` (авто, lexical, advisory-lock) | fail-fast, idempotent трекинг в schema_migrations |
| Дедуп правил | сложный NLP | нормализация (strip+lower) + сохранение порядка через dict.fromkeys | строки правил короткие; точное совпадение достаточно для v1 |
| openapi → TS types | ручная правка types/api.ts | `scripts/export-handoff.sh` (openapi-typescript@7) | hand-edit = drift; скрипт идемпотентен |
| enum-валидация на API | свои if'ы | Pydantic `Literal[...]` (как `voice_baseline: Literal["Professional","Friendly","Playful"]` schemas:465) | один источник + 422 автоматом |
| Anti-hallucination guard | новый текст | `_PROMPT_PRODUCT_GUARD` паттерн (ai_engine.py:397) | проверенная формулировка «strictly from this block» |
| Редактор стадий drag-n-drop | dnd-библиотека | React state + move-up/down кнопки | 3–5 элементов, overkill |

**Key insight:** Всё уже есть в кодовой базе — Phase 11 это перестановка и дедуп существующих кирпичей, а не строительство нового. Главный риск — НЕ переусложнить.

## Runtime State Inventory

> Это рефактор-фаза. Grep находит файлы, не рантайм-состояние. Ответы по 5 категориям:

| Категория | Найдено | Действие |
|-----------|---------|----------|
| **Stored data** | `ai_contexts.voice_baseline` / `tone` / `tone_of_voice` хранят значения в прод-БД (внутренние/тестовые). `campaigns.success_criteria` хранит текст. `dialogue_flow`/`arguments_facts`/`campaign_rules` ещё не существуют. | **Data migration** в `030_*.sql`: `voice_baseline`→`tone_preset` (MIG-01), `success_criteria`→`lead_trigger_hint` (MIG-03) ПЕРЕД DROP. Слайдеры/free-text тона дропаются без миграции (D-02). |
| **Live service config** | n8n-флоу клиентов AGS Foods живут в **старом** `/root/apps/telegram-api/` (PROJECT.md) — НЕ затрагиваются. Новый `/api/v1/send` принимает `campaign_id`, поля агента/кампании n8n напрямую не шлёт. | **None** — внешние интеграции читают через campaign_id, имена полей агента им не видны. Verified: send.py принимает campaign_id, не tone-поля. |
| **OS-registered state** | Нет cron/Task Scheduler/pm2/systemd, привязанных к именам полей агента. backup.sh использует имя БД `outreach_platform` (не меняется). | **None** — verified: имена колонок не фигурируют в cron/деплой-скриптах. |
| **Secrets / env vars** | Ни один env var / SOPS-ключ не назван по имени поля агента. `OPENAI_API_KEY`, `DATABASE_URL` не затрагиваются. | **None** — verified grep по config.py: только generic env. |
| **Build artifacts** | Frontend `types/api.ts` (в `lovable-handoff/`) — генерится из openapi.json, **застареет** после изменения Pydantic-схем. Lovable-фронт билдится из этого. | **Code edit + regen:** `scripts/export-handoff.sh` после изменения схем (UI-FLD-03/D-09). Backend Docker-образ ребилдится `docker compose up -d --build api` (миграции COPY'ятся в образ). |

**Канонический вопрос — что останется кэшировано:** `AIEngine._context_cache` (ai_engine.py:463, TTL 60s) кэширует контекст агента по `context_id`. После деплоя rewrite — кэш пустой (новый процесс). При PATCH агента из UI вызывается `invalidate_context` (line 521). Угроза минимальна. `get_context_for_conversation` (line 147) НЕ кэшируется — читает свежее каждый раз.

## Common Pitfalls

### Pitfall 1: Путать debounce listener'а с rate-limit queue
**What:** Тронуть `MIN_SEND_INTERVAL`/`LONG_PAUSE` в `queue.py` думая что это «скорость ответа».
**Why:** Два разных контура. `queue.py` = темп **исходящих первых касаний** (4/мин — нельзя трогать, CLAUDE.md). `listener.py::DEBOUNCE_MIN/MAX` (line 133-135) = задержка **AI-ответа на входящее** — именно сюда wire-ится `response_speed`.
**Avoid:** RT-01 трогает ТОЛЬКО `listener.py::schedule_ai_response` (line 229, расчёт `delay`). D-11 это явно разрешает.
**Warning signs:** diff затронул queue.py константы.

### Pitfall 2: Считать поля тона "Phase-3 полями"
**What:** CONTEXT.md/BRIEF говорят о `voice_baseline`/`tone`/`tone_of_voice` как о текущих — но Phase-3 CONTEXT (D-01/015) их дропал; реально они **возвращены 05.1 migration 018**.
**Why:** Прод-схема (verified 2026-06-24) показывает все три + 9 других v2-колонок. `build_system_prompt` COALESCE'ит new↔legacy (ai_engine.py:183-193).
**Avoid:** Planner читает **живую схему** (см. §Environment), не Phase-3 CONTEXT. Rewrite поверх 05.1.
**Warning signs:** план ссылается на «6 dropped columns» из 015 как актуальное состояние.

### Pitfall 3: conftest migration list не включает 028/029/030
**What:** Тесты применяют миграции по **явному списку** (conftest.py:127-150), который заканчивается на `027`. Новые `028`, `029`, `030` НЕ применятся в тестовой БД → `column does not exist`.
**Why:** conftest НЕ глобит — список захардкожен (в отличие от прод-applier'а который глобит).
**Avoid:** Добавить `028_sender_restriction.sql`, `029_campaign_pause_reason.sql`, `030_*.sql` в список conftest.py. Это Wave-0 задача.
**Warning signs:** красные тесты с `UndefinedColumn` после миграции.

### Pitfall 4: DROP колонки тона до data-migrate
**What:** DROP `voice_baseline` раньше `UPDATE tone_preset = ...` → потеря маппинга.
**Why:** порядок операторов в миграции.
**Avoid:** В `030_*.sql` строго: ADD tone_preset → UPDATE из voice_baseline → DROP constraint → DROP voice_baseline. То же для success_criteria→lead_trigger_hint.
**Warning signs:** tone_preset NULL у агентов где был voice_baseline.

### Pitfall 5: Дедуп правил ломает порядок/смысл
**What:** Наивный `set()` теряет порядок; агрессивная нормализация склеивает разные правила.
**Why:** правила — человекочитаемый текст.
**Avoid:** Дедуп по строкам (split по newline), нормализация strip()+lower() для сравнения, `dict.fromkeys` для сохранения порядка, склейка agent.rules ПЕРЕД campaign_rules. Точное совпадение, не fuzzy.
**Warning signs:** правило исчезло или порядок изменился в golden-prompt.

### Pitfall 6: Lovable перегенерит фронт и затрёт ручные правки
**What:** STATE.md (Phase 08-04) показывает: Lovable делает много параллельных коммитов; rebase нужен.
**Why:** фронт генерится из openapi.json + промптов в Lovable.
**Avoid:** Сначала backend+openapi.json, потом фронт; коммитить frontend поимённо; rebase на origin/main перед push (как 08-04 на 16 коммитов). D-10.
**Warning signs:** merge-конфликт в campaigns.new.tsx.

## Code Examples

### Текущая `<tone>` сборка — ПОЛНОСТЬЮ заменяется (PMT-02)
```python
# Source: app/services/ai_engine.py:614-635 — УДАЛИТЬ, заменить на:
tone_preset = (context.get("tone_preset") or "").strip()
_TONE_LINES = {
    "Friendly":     "Tone: warm and friendly. Write like a helpful acquaintance.",
    "Professional": "Tone: professional and concise. Businesslike, no fluff.",
    "Direct":       "Tone: direct and to the point. No hedging, no filler.",
    "Casual":       "Tone: casual and relaxed. Conversational, light.",
}
if tone_preset:
    blocks.append(f"<tone>\n{_TONE_LINES.get(tone_preset, '')}\n</tone>")
# тон БОЛЬШЕ НИГДЕ не появляется (D-03)
```

### Dialogue flow render (PMT-03) — заменяет `_PROMPT_DIALOGUE_GOAL`
```python
# Source: dialogue_flow = campaign.get("dialogue_flow") or []
if dialogue_flow:
    stage_lines = [
        f"{i}. {s.get('title','').strip()}: {s.get('instruction','').strip()}"
        for i, s in enumerate(dialogue_flow, start=1)
        if s.get("instruction")
    ]
    if stage_lines:
        blocks.append("<dialogue_flow>\nFollow these stages in order:\n"
                      + "\n".join(stage_lines) + "\n</dialogue_flow>")
```

### Rules dedup (PMT-05)
```python
# Source: agent rules + campaign_rules, preserve order, drop exact dups
def _dedup_rules(*texts: str) -> list[str]:
    seen, out = set(), []
    for t in texts:
        for line in (t or "").splitlines():
            line = line.strip()
            if not line: continue
            key = line.lower()
            if key not in seen:
                seen.add(key); out.append(line)
    return out
```

### Verified migration applier order (для понимания)
```python
# Source: app/database.py:82 — `_schema_migrations.sql` first, then lexical.
# 030_* сортируется ПОСЛЕ 029_*. Prod applier глобит; conftest НЕ глобит (Pitfall 3).
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| 3 источника тона (voice_baseline enum + tone JSONB sliders + tone_of_voice text) | 1 enum `tone_preset` | Phase 11 (D-01) | убирает дубль/противоречие в `<tone>` |
| Static `_PROMPT_DIALOGUE_GOAL` (3 хардкод-шага в ai_engine.py:402) | Per-campaign `dialogue_flow` JSONB стадии | Phase 11 (PMT-03) | задача из кампании, не из кода |
| `success_criteria` отдельное поле | слито в `lead_trigger_hint` | Phase 11 (D-13/MIG-03) | один сигнал «Лид» |
| `response_delay_seconds` (дропнут mig 015) | re-added + `response_speed` enum, wired в debounce | Phase 11 (D-11/RT-01) | настраиваемая скорость ответа |

**Deprecated/outdated после Phase 11:**
- `ai_contexts.voice_baseline`, `.tone`, `.tone_of_voice` — DROP.
- `campaigns.success_criteria` — DROP (после миграции в lead_trigger_hint).
- `_PROMPT_DIALOGUE_GOAL` константа — удаляется/перестаёт использоваться.
- `<tone>` COALESCE-логика в SELECT и build_system_prompt — удаляется.
- `audience_hints` колонка ОСТАЁТСЯ (D-13: переименование только в UI/API label, не в схеме).
- `knowledge_base` колонка ОСТАЁТСЯ (используется как `[ПРОДУКТ]`/«Что продаёт»; «Базы знаний» как фича — deferred).

## Open Questions

1. **`response_speed` → точка wire-апа (D-11)**
   - **Что знаем:** `schedule_ai_response` (listener.py:208) кладёт `context` в `pending_contexts` и считает `delay = min(random.uniform(DEBOUNCE_MIN, DEBOUNCE_MAX), MAX_BUFFER_TIME - buffer_age)` (line 229). Контекст строится в `handle_incoming_message` (line 847) и в нём НЕТ `response_speed`.
   - **Что неясно:** надо ли тащить агентский `response_speed` в `context` через доп. SELECT, или резолвить в самом `schedule_ai_response`.
   - **Recommendation:** **Wire-ап выполним и дёшев** — НЕ деферить. В `handle_incoming_message` уже есть `ai_context_id` (line 825); добавить в `context` dict (line 847) `response_speed`/`response_delay_seconds` агента (один доп. SELECT, либо взять из `get_context` который уже кэшируется TTL 60s). В `schedule_ai_response` заменить расчёт `delay`: `instant`→0–2s, `human`→текущий DEBOUNCE_MIN/MAX (default), `slow`→увеличенный, `manual`→`response_delay_seconds`. **MAX_BUFFER_TIME-гард сохранить.** Не трогает queue.py.

2. **Формат `dialogue_flow` JSONB (Claude's Discretion)**
   - **Recommendation:** `[{"title": str, "instruction": str}]` (BRIEF D-04 уже даёт этот пример). Валидация на Pydantic: `list[DialogueStage]`, 0–7 элементов (мягкий лимит), instruction обязателен.

3. **`response_speed` дефолтные секунды (Claude's Discretion)**
   - **Recommendation:** instant=2, human=использовать существующие DEBOUNCE_MIN=20/MAX=180 (текущее поведение = default), slow=множитель ×3 или фикс 300–600, manual=`response_delay_seconds` (пользовательский). Default enum-значение = `human` (back-compat с текущим поведением).

4. **«Базы знаний» multi-select в UI (deferred)**
   - **Recommendation:** показать disabled «coming soon» (на усмотрение planner'а) ИЛИ не рендерить вовсе. Без бэкенд-колонки. Не блокер.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PostgreSQL (outreach_platform) | миграция 030, ORM | ✓ | 16 | — |
| Прод-схема ai_contexts/campaigns | data-migration target | ✓ verified 2026-06-24 (см. ниже) | — | — |
| Docker compose test-overlay | прогон pytest | ✓ | `docker-compose.test.yml` | — |
| bun + node 18+ + openapi-typescript@7 | regen types (UI-FLD-03) | ✓ (frontend репо) | — | — |
| Lovable-фронт репо | UI-FLD-01/02 | ✓ `/root/apps/aimly/aimly-tg-outreach` | — | — |
| OpenAI API | рантайм генерации (не для тестов) | ✓ (model `gpt-5-mini-2025-08-07`, config.py:54 — НЕ трогать) | — | — |

**Прод-схема verified (`docker exec outreach-platform-db psql`):**
- `ai_contexts`: id, workspace_id, name, system_prompt, tone_of_voice, rules, company_info, product_info, faq, who_is_agent, company_knowledge, knowledge_base, voice_baseline, tone, mirror_language, allow_emoji, banlist, qa_pairs, auto_pause_scope, created_at, updated_at, max_message_length, auto_pause_triggers
- `campaigns`: id, workspace_id, agent_id, folder_id, name, description, status, timezone, work_hour_start, work_hour_end, work_days_mask, start_date, stop_date, message_template, lead/handoff/finish_webhook_url, lead/handoff/finish_trigger_hint, tools, audience_hints, primary_goal, success_criteria, webhook_url, created_at, updated_at, allow_recontact, recontact_min_age_days, pause_reason, paused_at
- CHECK-и для drop/keep: `ai_contexts_voice_baseline_check` (DROP с voice_baseline), `ai_contexts_auto_pause_scope_check` (keep), `campaigns_primary_goal_check` (keep), `campaigns_status_check`/`work_days`/`work_hours` (keep).

**Missing dependencies:** None blocking. Все зависимости на месте.

## Validation Architecture

> nyquist_validation = true (config.json). Раздел обязателен.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (в проде) |
| Config file | `tests/conftest.py` (ephemeral db-test в tmpfs через overlay) |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_ai_engine.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

> **НИКОГДА** `docker compose run --rm api pytest` без overlay (DROP SCHEMA на проде). **НИКОГДА** `down -v`.

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MIG-01 | voice_baseline→tone_preset маппится, voice_baseline дропнут | integration | `pytest tests/test_migration_030.py::test_tone_preset_backfill -x` | ❌ Wave 0 |
| MIG-03 | success_criteria→lead_trigger_hint (existing hint не теряется), success_criteria дропнут | integration | `pytest tests/test_migration_030.py::test_lead_hint_merge -x` | ❌ Wave 0 |
| FLD-01..06 | новые колонки существуют с правильными CHECK/типом | integration | `pytest tests/test_migration_030.py::test_new_columns -x` | ❌ Wave 0 |
| PMT-01 | **Блоки в точном порядке §7** (ИДЕНТИЧНОСТЬ→…→ФОРМАТ) | unit | `pytest tests/test_ai_engine.py::test_prompt_block_order -x` | ⚠️ extend test_ai_engine.py |
| PMT-02 | tone рендерится ТОЛЬКО из tone_preset, нет voice_baseline/sliders | unit | `pytest tests/test_ai_engine.py::test_tone_single_source -x` | ⚠️ extend |
| PMT-03 | dialogue_flow → нумерованные стадии; нет static goal | unit | `pytest tests/test_ai_engine.py::test_dialogue_flow_render -x` | ⚠️ extend |
| PMT-04 | arguments_facts + guard «не выдумывай» | unit | `pytest tests/test_ai_engine.py::test_arguments_facts_guard -x` | ⚠️ extend |
| PMT-05 | **дедуп: правило не появляется дважды** (поведенческое ядро) | unit | `pytest tests/test_ai_engine.py::test_rules_dedup_no_duplicate -x` | ⚠️ extend |
| RT-01 | response_speed=manual → delay==response_delay_seconds; instant→~0; human→default диапазон | unit | `pytest tests/test_listener_response_speed.py -x` | ❌ Wave 0 |
| UI-FLD-* | tsc clean; формы рендерят новые поля | manual + tsc | `cd ../aimly-tg-outreach && bun run tsc` | manual UAT |

### Поведенческое ядро — как доказать «нет дубля»
Главная цель фазы — устранить повтор инструкций. Тестируется детерминированно:
1. **Golden-prompt order test (PMT-01):** собрать `build_system_prompt` с полностью заполненным агентом+кампанией, проверить что индексы тегов идут в порядке §7: `assert prompt.index("<role>") < prompt.index("<tone>") < prompt.index("<dialogue_flow>") < ...`.
2. **Single-source tone (PMT-02):** задать tone_preset + (в БД) остаточные voice_baseline → проверить что в промпте есть строка пресета и НЕТ «Baseline persona»/«Tone calibration»/слайдеров.
3. **No-duplicate rules (PMT-05):** агент.rules = «Не давить.» + campaign_rules = «Не давить.\nОтвечать кратко.» → `assert prompt.count("Не давить") == 1` и «Отвечать кратко» присутствует.
4. **Tone-not-in-rules (D-03):** задать tone_preset → проверить что блок `[ТОН]` единственное место с tone-инструкцией (нет tone-текста внутри `<rules>`).

### Sampling Rate
- **Per task commit:** `pytest tests/test_ai_engine.py tests/test_migration_030.py -x` (< 30s)
- **Per wave merge:** полный suite (test-overlay)
- **Phase gate:** полный suite зелёный + frontend `bun run tsc` clean перед `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_migration_030.py` — MIG-01/MIG-03/FLD-01..06 (применить 030 на эфемерной БД, проверить backfill + drop + CHECK)
- [ ] `tests/test_listener_response_speed.py` — RT-01 (мок context, проверить расчёт delay по enum)
- [ ] Extend `tests/test_ai_engine.py` — PMT-01..05 golden-prompt assertions
- [ ] `tests/conftest.py:127-150` — **ДОБАВИТЬ** `028_sender_restriction.sql`, `029_campaign_pause_reason.sql`, `030_*.sql` в migration list (иначе UndefinedColumn — Pitfall 3)
- [ ] conftest fixture `test_agent_factory` — расширить под `tone_preset`/`response_speed` (сейчас даёт voice_baseline-эру)

## Sources

### Primary (HIGH confidence — живой код + прод-БД)
- `app/services/ai_engine.py` — build_system_prompt:559-713, get_context_for_conversation:147-268, build_builtin_tools:85, _PROMPT_* constants:389-457, get_context:466, _context_cache:463
- `app/services/listener.py` — DEBOUNCE_*:133-135, schedule_ai_response:208-233, _debounce_timer:235, process_buffered_messages:247, _send_to_ai:265, context build:847-859
- `app/models/__init__.py` — AIContext:152-187, Campaign:465-530, MessageQueue:190
- `migrations/015_phase3.sql`, `016_phase4.sql`, `018_phase5_1.sql`, `025`, `026`, `029` — schema history
- `app/schemas/__init__.py` — ToneSpec:438, AgentCreate:451 (voice_baseline Literal:465), CampaignCreate:604
- `app/routers/agents.py`:61-388, `app/routers/campaigns.py`:258-787, auto_fill:428
- `app/database.py`:75-82 — migration applier (lexical, advisory-lock)
- `tests/conftest.py`:127-160 — migration list (заканчивается 027)
- Прод-БД `outreach-platform-db` — verified ai_contexts/campaigns columns + CHECK constraints (2026-06-24)
- `scripts/export-handoff.sh` — openapi.json regen flow
- `/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.new.tsx`:45-167 (STEPS), agents.tsx, EditCampaignModal.tsx
- CLAUDE.md (root + tg-outreach), CONTEXT.md (D-01..D-15), BRIEF.md (§3-§7)

### Secondary (MEDIUM)
- STATE.md — Phase 08-04 cross-repo rebase precedent, Phase 4/5.1 decisions

### Tertiary (LOW)
- None — всё подтверждено первоисточниками.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — без новых библиотек, всё в проде.
- Architecture/prompt rewrite: HIGH — прочитан весь build_system_prompt + SELECT построчно.
- Migration/data-migration: HIGH — прод-схема verified, паттерны из 016/018.
- response_speed wire-up: HIGH — точка дебаунса найдена (listener.py:229), wire-ап выполним.
- Pitfalls: HIGH — conftest migration-list gap и Phase-3-vs-05.1 путаница подтверждены кодом.
- Frontend: MEDIUM — структура визарда найдена; точные diff'ы зависят от Lovable-генерации.

**Research date:** 2026-06-24
**Valid until:** 2026-07-24 (стабильный внутренний код; пересмотреть если Lovable значительно перегенерит фронт или появятся новые миграции 030+)
