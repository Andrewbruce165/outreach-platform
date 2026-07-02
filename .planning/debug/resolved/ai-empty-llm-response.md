---
slug: ai-empty-llm-response
status: resolved
trigger: "запустил кампанию 6c9e8776-9f8a-40f9-a381-fe88b7894455, робот перестал отвечать пользователям (Polina, Mariya)"
created: 2026-07-02
updated: 2026-07-02
---

## Resolution
- root_cause: gpt-5-mini (reasoning-модель) исчерпывал `max_completion_tokens:2000` на reasoning-токенах → `content='' finish_reason=length` без ошибки; `generate_response` возвращал None, листенер молча не отправлял (`if reply`).
- fix: `_is_reasoning_model()` + `_build_completion_params()` в ai_engine.py — для reasoning-моделей добавляется `reasoning_effort='low'` (retry: 'minimal') и `max_completion_tokens` 4000 (retry: 6000). При пустом `content` c `finish_reason='length'` — лог ERROR + один ретрай с эскалацией; повторно пусто → лог ERROR, но больше не «тихо». Применено к обоим call-site (первый ход + суммаризация tool-результатов). `reasoning_effort` гейтится по модели → откат на gpt-4o-mini не сломается (400).
- verification: 8 новых тестов (tests/test_ai_engine_empty_retry.py) + 70 смежных GREEN через test-overlay. Деплой: listener+api ребилд 2026-07-02 07:43, миграции up-to-date (43), все 6 senders слушают, ошибок нет. Флаппинг sender-8525079460 исчез после рестарта (побочно).
- files_changed: app/services/ai_engine.py, tests/test_ai_engine_empty_retry.py
- follow_up: (1) два зависших диалога (Polina 07:12, Mariya 07:28) НЕ авто-ответятся — in-memory debounce потерян при рестарте, catch_up не пере-триггерит; нужен ручной nudge из inbox. (2) CLAUDE.md «Стек» всё ещё говорит gpt-4o-mini — обновить. (3) warmup использует ту же settings.openai_model — те же пустые ответы возможны, вне scope инцидента.

# Debug: AI перестал отвечать после запуска кампании

## Symptoms
- Expected: AI-ответчик отвечает контактам в диалогах кампании 6c9e8776.
- Actual: часть входящих остаётся без ответа. Polina (07:12:51 "Нет расскажи") и Mariya (07:28:28 "Не знаю его") — без ответа, хотя ранее в тех же диалогах AI отвечал.
- Timeline: кампания создана 07:01:33 UTC, running. AI отвечал на 07:04/07:11/07:13/07:20/07:27, но интермиттентно возвращает пусто.
- Reproduction: чем «сложнее»/открытее реплика контакта, тем чаще пустой ответ.

## Current Focus
- hypothesis: gpt-5-mini (reasoning-модель) исчерпывает `max_completion_tokens: 2000` на reasoning-токенах → пустой `content` c `finish_reason=length`. Код молча возвращает None, листенер ничего не шлёт.
- next_action: выбрать и применить фикс (см. Root Cause → Fix options), после подтверждения пользователя.

## Evidence
- timestamp 2026-07-02 07:15:24 — Polina llm_call: `content='' finish_reason=length`, latency 18984ms, no error, tool_calls=None → нет outbound.
- timestamp 2026-07-02 07:28:50 — Mariya llm_call: `content='' finish_reason=length`, latency 20959ms, no error → нет outbound.
- Успешные ответы всегда `finish_reason=stop` (07:04/07:11/07:13/07:20/07:27), пустые — всегда `finish_reason=length`.
- С запуска кампании (07:01): 3 из 11 llm_calls пустые (~27%).
- `app/config.py:54-57` — `openai_model` default = `gpt-5-mini-2025-08-07` (reasoning-модель; CLAUDE.md всё ещё говорит про gpt-4o-mini → модель сменили).
- `app/services/ai_engine.py:1368` и `:1550` — `max_completion_tokens: 2000` (общий бюджет reasoning+вывод у reasoning-моделей). Нет `reasoning_effort` в request_params.
- `app/services/ai_engine.py:1409-1416` — `text_content_clean = None` при пустом content → возвращается None БЕЗ лога/ретрая/фолбэка.
- `app/services/listener.py:345` — `if reply and client:` → при None ничего не отправляется, молча (silent drop).
- Диалоги Polina/Mariya: `ai_enabled=true`, `status=active` — гейтинг AI не причём. Sender sender-8526195634 active, no restriction. Листенер входящие ПРИНИМАЕТ (записаны в messages) — значит подключён; проблема строго на генерации ответа.

## Eliminated
- hypothesis: sender отключён от листенера (reconcile «desired set»). ОТВЕРГНУТО — входящие Polina/Mariya записаны, значит листенер получает события на этом sender.
- hypothesis: диалог на паузе / ai_enabled=false / working-hours. ОТВЕРГНУТО — оба active+ai_enabled, 10:32 МСК в рабочем окне.
- hypothesis: rate-limit / FloodWait на отправке. ОТВЕРГНУТО — до отправки не доходит: reply=None, отправка не вызывается.

## Root Cause
gpt-5-mini — reasoning-модель. `max_completion_tokens: 2000` — общий лимит на reasoning-токены + видимый вывод. На «сложных» ходах reasoning съедает весь бюджет → `content=''`, `finish_reason=length`. `generate_response()` возвращает None (ai_engine.py:1409-1416, без лога/ретрая), листенер (listener.py:345 `if reply`) молча не отправляет ничего. Контакт остаётся без ответа. Ошибок нет нигде → «тихо перестал отвечать». Запуск кампании лишь увеличил объём/сложность диалогов, обнажив дефект.

## Fix options (await user decision)
1. Немедленная митигация (config, без деплоя кода): `OPENAI_MODEL=gpt-4o-mini` в .env + рестарт listener/api → уходим с reasoning-модели.
2. Таргетный код-фикс: добавить `reasoning_effort: "low"|"minimal"` в оба request_params + поднять `max_completion_tokens` (напр. 2000→4000).
3. Защита от пустого ответа: при пустом content (finish_reason=length) — залогировать ERROR + ретрай с бóльшим бюджетом/меньшим reasoning, чтобы пустой ответ никогда не «терялся молча».
Рекомендация: 1 (сейчас, остановить кровотечение) + 2 + 3 (правильный фикс, PR).

## Files (candidate)
- app/services/ai_engine.py (request_params ~1366-1370, 1547-1551; empty handling ~1409-1416, 1573-1579)
- app/config.py:54-57 (openai_model)
- app/services/listener.py:345 (None handling)
