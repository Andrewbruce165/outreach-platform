# Phase 18: Switchable LLM Provider in UI - Context

**Gathered:** 2026-07-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Переключение LLM прямо из UI: выбор провайдера (пока только **Claude/Anthropic** и **OpenAI**) и конкретной модели в настройках workspace, частичные настройки модели (temperature, reasoning effort, max tokens), ввод API-ключа провайдера, и AI-ответчик в чате (+ warmup) работает через выбранную LLM. Сейчас модель захардкожена через env `OPENAI_MODEL` (gpt-5-mini) и один платформенный ключ.

Фаза **фактически втягивает и расширяет** сид BYOK-01 (`byo-openai-key.md`, был Out of Scope v2): не только свой ключ, но и выбор провайдера. При закрытии фазы обновить PROJECT.md Out of Scope.

</domain>

<decisions>
## Implementation Decisions

### Уровень настройки
- **D-01:** Выбор провайдера/модели живёт на **workspace-level** (Settings). Все агенты и кампании workspace используют одну настройку. Per-agent override — НЕ в этой фазе (deferred).
- **D-02:** Дефолтное состояние (workspace ничего не настроил) = текущее поведение: платформенный OpenAI-ключ + `settings.openai_model`. Ничего не ломается у существующих workspace.

### API-ключи
- **D-03:** Свой API-ключ **обязателен** для явного выбора провайдера/модели: без введённого ключа переключение недоступно, workspace остаётся на платформенном дефолте. Платформа не платит за токены переключившихся клиентов.
- **D-04:** Ключ хранится **encrypted** — переиспользуем Fernet-helper (`app/services/encryption.py`, как session strings). Ключ никогда не возвращается в API-ответах целиком (только маска/префикс) и не пишется в логи.
- **D-05:** Кнопка **Test connection** при вводе ключа — дешёвый пробный запрос к выбранному провайдеру; результат виден сразу в UI.
- **D-06:** Runtime-ошибки ключа (401 / invalid / quota): **fallback на платформенный OpenAI-дефолт** (`OPENAI_API_KEY` + `settings.openai_model`) — диалоги не останавливаются; ключ помечается invalid в UI (флаг статуса ключа в БД). Обоснование: ghosted-контакт уже был инцидентом (2026-07-02), живой диалог важнее чистоты биллинга.
- **D-07:** `llm_logger` пишет источник ключа/модели на каждый вызов (`platform` / `byok` / `fallback`) + фактический provider/model — для аналитики и будущего cost-биллинга (из сида BYOK-01).

### Выбор модели и настройки в UI
- **D-08:** Список моделей — **живой из API провайдера** по ключу клиента (`/v1/models` у OpenAI, `/v1/models` у Anthropic), но **серверно отфильтрованный до chat-совместимых с tools**: без embeddings/whisper/tts/dall-e/realtime/deprecated. Фильтр — на бэкенде (whitelist-паттерны семейств: gpt-4o*/gpt-5*/o*/claude-*).
- **D-09:** Частичные настройки модели в UI: **temperature**, **reasoning effort**, **max tokens (бюджет ответа)**. Temperature показывается только для моделей, которые её принимают (reasoning-модели OpenAI отвергают). Reasoning effort — только для reasoning-моделей; у Claude маппится на extended thinking (детали маппинга — Claude's discretion).
- **D-10:** Защита от опасных значений: **жёсткий кламп на бэкенде + «зелёный коридор» (рекомендованные диапазоны) в UI**. В частности, нижняя граница max tokens для reasoning-моделей (≥4000) — урок инцидента 2026-07-02 (gpt-5-mini съедал бюджет на reasoning → пустой ответ → ghosted contact). Невозможно сломать прод-ответчик настройкой.

### Границы переключения
- **D-11:** Через выбранную модель/ключ идут: **AI-ответчик чата** (все вызовы `ai_engine.generate_response`, включая tool-обработку и second-pass) и **warmup-переписка** (единый тон везде).
- **D-12:** **Whisper-транскрипция голосовых и KB-эмбеддинги (ingest + search) всегда остаются на платформенном OpenAI-ключе** независимо от выбора провайдера — у Anthropic этих API нет. Выбор Claude не ломает голосовые и KB.

### Claude's Discretion
- Расположение секции в Settings UI (отдельная вкладка "AI / LLM" или раздел в существующих настройках)
- Схема хранения (колонки на `workspaces` vs отдельная таблица настроек LLM) — с учётом ORM default vs server_default drift (memory-урок, мигр. 040/042)
- Точный маппинг reasoning_effort ↔ Claude extended thinking budget
- Конкретные диапазоны клампа и значения «зелёного коридора» per-модель
- Абстракция провайдера в `ai_engine` (адаптер/фабрика клиентов) и добавление `anthropic` SDK в requirements
- Кэширование живого списка моделей (TTL), поведение при недоступности `/models`

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### BYOK / ключи
- `.planning/seeds/byo-openai-key.md` — сид BYOK-01: encrypted-хранение как session strings, Test connection, `llm_logger` key-source, открытые вопросы по fallback (решены в D-06) и мультипровайдерности (решена этой фазой)

### Архитектура и правила
- `CLAUDE.md` (корень репо tg-outreach) — стек (gpt-5-mini reasoning, env `OPENAI_MODEL`), правила миграций (raw SQL, идемпотентность, auto-applier), тесты только через test-overlay
- `.planning/codebase/ARCHITECTURE.md` — карта системы (api/listener/db)
- `.planning/codebase/INTEGRATIONS.md` — текущая интеграция с OpenAI

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `app/services/ai_engine.py:71` — `_build_completion_params()`: **единственная точка** сборки model/max_completion_tokens/reasoning_effort для всех chat-вызовов ответчика — идеальное место для подстановки provider/model/knobs из workspace-настроек
- `app/services/ai_engine.py:61` — `_is_reasoning_model()`: гейт reasoning-параметров по имени модели — расширить на Claude-модели
- `app/services/encryption.py` — Fernet-helper (`encrypt_session`/`decrypt_session`) — переиспользовать для API-ключей (D-04)
- `app/services/llm_logger.py` — never-raise `log_llm_call()` — добавить поля provider/key_source (D-07)
- `app/routers/workspace.py` — существующий workspace-роутер — точка для settings-эндпоинтов

### Established Patterns
- Модуль-синглтон `client = AsyncOpenAI(...)` в `ai_engine.py:41` — его переиспользуют `kb_ingest`/`kb_search` (эмбеддинги). При рефакторинге под мультипровайдер **эмбеддинг-путь должен остаться на платформенном OpenAI-клиенте** (D-12)
- `app/services/warmup.py:99` — warmup держит **свой** `AsyncOpenAI()` — перевести на ту же workspace-aware фабрику (D-11)
- `app/services/ai_engine.py:1720` — `transcribe_audio()` = Whisper, OpenAI-only — остаётся на платформенном ключе (D-12)
- Миграции: raw SQL, следующий номер **044**, идемпотентность обязательна; ORM-урок `default=` vs `server_default=` (memory: мигр. 040/042 инциденты)
- 3 вызова `chat.completions.create` в ai_engine (initial ~1424 / retry ~1478 / second-pass ~1652) + вызовы warmup — все должны пройти через одну точку выбора клиента

### Integration Points
- `app/config.py:54` — `openai_model` (default gpt-5-mini-2025-08-07) становится платформенным дефолтом/fallback-моделью; `openai_embedding_model` не трогаем
- Фронт: sibling-репо `/root/apps/aimly/aimly-tg-outreach` (Lovable) — секция Settings; новые эндпоинты задокументировать в `lovable-handoff/openapi.json`
- `anthropic` SDK отсутствует в `requirements.txt` — добавить
- Тесты: только через test-overlay (`docker-compose.test.yml`)

</code_context>

<specifics>
## Specific Ideas

- «Зелёный коридор» — та же продуктовая идея, что заявлена для лимитов рассылки в CLAUDE.md (рекомендованные безопасные значения + предупреждение при выходе): применить к model-knobs
- Инцидент 2026-07-02 (gpt-5-mini: пустые ответы при малом max_completion_tokens, контакты ghosted) — прямое обоснование D-10; кламп обязан не дать клиенту воспроизвести этот инцидент настройкой
- Пользователь сформулировал фичу как «переключаться между моделями прямо в UI» — переключение должно применяться сразу (следующий LLM-вызов), без редеплоя/env

</specifics>

<deferred>
## Deferred Ideas

- **Per-agent override модели** — workspace default + переопределение на агенте (v2, отмечено при обсуждении уровня настройки)
- **BYOK для Whisper/эмбеддингов** (все сервисные вызовы через ключ клиента) — отклонённый вариант границ, вернуться если появится enterprise-запрос на полный BYOK
- **Другие провайдеры** (OpenRouter, Google, локальные модели) — сид BYOK-01 упоминал; архитектура адаптера должна позволять, но в фазе только Claude + OpenAI
- **Cost-биллинг на базе key_source из llm_logger** (тарификация «BYOK = free tier») — бизнес-логика за пределами фазы; D-07 закладывает данные
- **Обновить PROJECT.md**: строка Out of Scope «Свой OpenAI ключ на workspace (BYOK-01)» субсуммирована этой фазой — снять при phase transition

</deferred>

---

*Phase: 18-switchable-llm-provider*
*Context gathered: 2026-07-02*
