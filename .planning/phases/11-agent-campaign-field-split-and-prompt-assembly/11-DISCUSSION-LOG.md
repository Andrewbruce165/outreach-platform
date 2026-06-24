# Phase 11: Agent/Campaign Field Split & Prompt Assembly - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-24
**Phase:** 11-agent-campaign-field-split-and-prompt-assembly
**Areas discussed:** Тон (схлопнуть до enum), «Ход разговора» (формат), Override полей агента, Скоуп фронтенда

---

## Тон — схлопнуть до enum

| Option | Description | Selected |
|--------|-------------|----------|
| Жёсткий cut → 1 enum | Новый `tone_preset`; дроп `tone` JSONB + `tone_of_voice` + `voice_baseline`; миграция voice_baseline→preset | ✓ |
| enum + advanced слайдеры | enum основной, слайдеры под спойлером как тонкая настройка | |
| enum + старые как fallback | Добавить enum, старые колонки не дропать (dual-read) | |

**User's choice:** Жёсткий cut → 1 enum
**Notes:** Соответствует BRIEF «единственный источник тона». Риск миграции низкий — проект до первого внешнего клиента.

---

## «Ход разговора» — формат

| Option | Description | Selected |
|--------|-------------|----------|
| Структура (JSONB steps) | JSONB массив [{title, instruction}], отдельный UI-редактор стадий, рендер как нумерованные стадии | ✓ |
| Один textarea | Свободный текст с подсказкой «опиши 3-5 шагов» | |

**User's choice:** Структура (JSONB steps)
**Notes:** Ключевое новое поле фазы; структура даёт качество промпта и валидацию.

---

## Override полей агента на уровне кампании

| Option | Description | Selected |
|--------|-------------|----------|
| Отложить в v2 | Кампания только select агента, без override | ✓ |
| Включить сейчас | JSONB agent_overrides, merge при сборке промпта | |

**User's choice:** Отложить в v2
**Notes:** Держим скоуп узким — ядро фазы (разведение + рерайт сборки промпта) и так большое.

---

## Скоуп фронтенда

| Option | Description | Selected |
|--------|-------------|----------|
| Бэкенд + UI-SPEC ТЗ | Только бэкенд + openapi.json + UI-контракт для Lovable | |
| Бэкенд + реализация UI | Сюда же реализация форм визарда в репо aimly-tg-outreach | ✓ |

**User's choice:** Бэкенд + реализация UI
**Notes:** Фаза охватывает два репо. Обязательна осторожность с коммитами — параллельно идёт другая работа (Phase 10).

---

## Claude's Discretion

- Конкретные enum-значения `response_speed` и дефолтные секунды.
- JSON-формат элемента `dialogue_flow` (`title/instruction` vs `stage/do`).
- Формат рендера блоков промпта (guard'ы, разделители) при сохранении «один источник на блок» и порядка BRIEF §7.
- Маппинг `voice_baseline` → `tone_preset`.

## Deferred Ideas

- Override полей агента на кампании → v2.
- «Используемые базы знаний» (реальное подтягивание КБ) → будущая фича; в Phase 11 максимум disabled-плейсхолдер в UI.
- Wire `response_speed` в дебаунс listener'а — базово пытаемся в этой фазе, иначе выносится отдельно.
