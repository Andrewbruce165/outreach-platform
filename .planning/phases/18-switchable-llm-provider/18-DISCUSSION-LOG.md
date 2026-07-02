# Phase 18: Switchable LLM Provider in UI - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-02
**Phase:** 18-switchable-llm-provider
**Areas discussed:** Уровень настройки, API-ключи и fallback, Настройки модели в UI, Границы переключения

---

## Уровень настройки

| Option | Description | Selected |
|--------|-------------|----------|
| Workspace-level (Recommended) | Одна настройка в Settings, все агенты/кампании её используют | ✓ |
| Per-agent | Каждый агент выбирает свою модель | |
| Workspace + per-agent override | Дефолт на workspace, агент переопределяет | |

**User's choice:** Workspace-level
**Notes:** Per-agent override отложен (Deferred Ideas).

---

## API-ключи и fallback

### Обязательность ключа

| Option | Description | Selected |
|--------|-------------|----------|
| Platform default + BYOK optional (Recommended) | Без ключа — платформенный, ввёл свой — через него | |
| Ключ обязателен | Выбрал провайдера — обязан ввести свой ключ | ✓ |
| Свой ключ только для Claude | OpenAI на платформенном, Claude только BYOK | |

**User's choice:** Ключ обязателен — платформа не платит за токены переключившихся клиентов. Дефолтное состояние без настройки = платформенный OpenAI (текущее поведение).

### Runtime-ошибки ключа (401/quota)

| Option | Description | Selected |
|--------|-------------|----------|
| Fallback на платформу + пометить ключ (Recommended) | Диалоги не останавливаются, ключ помечен invalid в UI | ✓ |
| Fail + пометить, без fallback | AI-ответы падают до исправления ключа | |

**User's choice:** Fallback + пометка.

### Fallback-цель

| Option | Description | Selected |
|--------|-------------|----------|
| Платформенный OpenAI-дефолт (Recommended) | Всегда OPENAI_API_KEY + settings.openai_model | ✓ |
| Тот же провайдер, если есть платформенный ключ | Сначала платформенный ключ того же провайдера | |

**User's choice:** Платформенный OpenAI-дефолт.

### Test connection

| Option | Description | Selected |
|--------|-------------|----------|
| Да (Recommended) | Пробный запрос при сохранении ключа | ✓ |
| Нет, валидация только в рантайме | | |

**User's choice:** Да.

---

## Настройки модели в UI

### Источник списка моделей

| Option | Description | Selected |
|--------|-------------|----------|
| Курируемый список (Recommended) | Статический проверенный набор на бэкенде | |
| Живой список из API провайдера | /models по ключу клиента | ✓ |

**User's choice:** Живой список из API.

### Фильтрация живого списка (follow-up)

| Option | Description | Selected |
|--------|-------------|----------|
| Фильтровать до chat-совместимых (Recommended) | Бэк отдаёт только chat+tools модели, без embeddings/whisper/tts/deprecated | ✓ |
| Показывать всё как есть | | |

**User's choice:** Фильтровать на бэкенде.

### Какие настройки выставить (multi-select)

| Option | Selected |
|--------|----------|
| Temperature | ✓ |
| Reasoning effort | ✓ |
| Max tokens (бюджет ответа) | ✓ |
| Ничего — только провайдер + модель | |

**User's choice:** Все три ручки.

### Защита от опасных значений (follow-up)

| Option | Description | Selected |
|--------|-------------|----------|
| Жёсткий кламп на бэкенде + подсказки в UI (Recommended) | Бэк валидирует диапазоны (max_tokens ≥ 4000 для reasoning), UI показывает «зелёный коридор» | ✓ |
| Только предупреждение в UI | Warning, но сохраняем любые значения | |

**User's choice:** Жёсткий кламп + зелёный коридор.

---

## Границы переключения

| Option | Description | Selected |
|--------|-------------|----------|
| Чат + warmup на выбранной; сервисные — платформенный OpenAI (Recommended) | Whisper и KB-эмбеддинги всегда платформенный OpenAI | ✓ |
| Только чат; warmup и сервисные — как сейчас | | |
| Всё на ключе клиента, где возможно | Whisper/эмбеддинги через OpenAI-ключ клиента если введён | |

**User's choice:** Чат + warmup на выбранной модели; Whisper/эмбеддинги — платформенный OpenAI.

---

## Claude's Discretion

- Расположение секции в Settings UI
- Схема хранения (колонки workspaces vs отдельная таблица)
- Маппинг reasoning_effort ↔ Claude extended thinking
- Конкретные диапазоны клампа / зелёного коридора per-модель
- Абстракция провайдера в ai_engine + anthropic SDK
- Кэширование живого списка моделей

## Deferred Ideas

- Per-agent override модели (v2)
- BYOK для Whisper/эмбеддингов (полный BYOK)
- Другие провайдеры (OpenRouter и т.д.)
- Cost-биллинг на базе key_source из llm_logger
- Обновить PROJECT.md: BYOK-01 Out of Scope субсуммирован фазой 18
