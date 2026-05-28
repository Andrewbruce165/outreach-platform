---
title: Свой OpenAI ключ на workspace (Bring Your Own Key)
trigger_condition: При планировании v2 — milestone v1.0 закрыт
planted_date: 2026-05-27
v2_code: BYOK-01
related_phases: [v2]
---

## Идея

Клиент может ввести свой OpenAI API key — все LLM-вызовы для этого workspace идут через его аккаунт, не через платформенный.

## Скоуп

- Поле `workspaces.openai_api_key` (encrypted, как session strings — переиспользуем helper)
- UI: Settings → Integrations → OpenAI API Key
- Кнопка "Test connection" — ping в `/v1/models` или дешёвый `chat.completions` с 1 токеном
- `ai_engine` priority: `workspace.openai_key` → `settings.OPENAI_API_KEY` (platform fallback)
- `llm_logger` пишет какой ключ использован (`platform` / `byok`) — для аналитики и cost-биллинга
- При ошибке (401/quota) — отметить ключ как invalid, опционально fallback на platform с уведомлением

## Зачем

1. **Cost-model:** платформа не перепродаёт токены, не несёт риск spike-cost от одного клиента
2. **Compliance:** enterprise клиенты хотят свой OpenAI org для аудита промптов
3. **Биллинг:** упрощает тарификацию (платформа берёт за оркестрацию, не за токены) — выручка предсказуемая
4. **Бесплатная модель монетизации:** "BYOK = free tier, platform key = paid tier"

## Зависимости

- Encryption helper уже есть для session strings — переиспользуем
- `ai_engine.py` — добавить параметр `api_key` в OpenAI client init (сейчас глобальный singleton)
- Pre-req: WSPC-01 (Settings UI должен существовать)

## Альтернатива

Только platform key + перепродажа с маржой — простая модель, но рисковая (один runaway client с большим volume = убыток).

## Открытые вопросы

- Что делать если key невалиден / превысил quota — fallback на platform или fail-and-notify?
- Поддерживать ли альтернативные провайдеры (Anthropic, OpenRouter) или только OpenAI? (Не входит в v2, но архитектура должна позволять)
- Платформа всё равно платит за `AIUX-01` (AI-ассистент при заполнении полей) — это вне BYOK-логики
