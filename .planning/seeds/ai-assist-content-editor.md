---
title: AI-ассистент при заполнении текстовых полей агента и кампании
trigger_condition: При планировании v2 — milestone v1.0 закрыт
planted_date: 2026-05-27
v2_code: AIUX-01
related_phases: [v2]
---

## Идея

Meta-AI: пользователь пишет черновик в большом текстовом поле (context, task, tone, FAQ, brief) — кнопка "✨ Improve" / "Expand" — side-LLM-call возвращает структурированную, расширенную версию.

## Скоуп

- **Поля где применимо:**
  - `agent.context` (большой свободный текст — кто мы, что продаём, кому пишем)
  - `agent.task` (что должен делать AI в диалоге)
  - `agent.tone` (как говорить — стиль)
  - `agent.faq` (per-question expand)
  - `campaign.brief` (опционально — описание цели кампании)
- **UI:**
  - Кнопка "✨ Improve" рядом с textarea
  - Опционально дропдаун стиля: "more formal" / "more casual" / "shorter" / "longer" / "translate to RU"
  - Diff-view before/after — пользователь принимает или редактирует
- **Backend:**
  - Новый endpoint `POST /api/v1/ai-assist/improve` — body: `{ field_type, draft_text, style? }` → `{ improved_text }`
  - Промпт для каждого `field_type` зашит в backend (не редактируется клиентом, иначе джейлбрейк-вектор)
  - Использует системный OpenAI key (не BYOK) — поддержка фичи бесплатна на платформе
  - Cap по rate: 50 improve-вызовов в день per-workspace

## Зачем

1. **Самый частый затык клиента** — "не знаю как написать промпт для AI-агента, что туда писать"
2. Снижает онбординг с часов до минут (видим в support чатах AGS Foods)
3. Сейчас клиент видит пустую textarea → пишет 2 строки → AI-агент выдаёт мусор → клиент уходит

## Зависимости

- OpenAI client уже есть в `ai_engine.py`
- Не требует BYOK (используем platform key)
- Pre-req: rate-limit middleware на endpoint (защита от abuse)

## Альтернатива

- **Готовые шаблоны** (templates library "сэмплы агентов по индустриям") вместо AI-assist — дешевле, но не персонализирует
- **Inline-suggestion** как у GitHub Copilot — сложнее имплементация, лучше UX

## Открытые вопросы

- Сохранять ли историю улучшений (для undo всех версий)?
- Multilingual: русский draft → русский improved (или билингва — pусский + английский варианты)?
- Использовать `gpt-4o-mini` (быстро, дёшево) или `gpt-4o` (лучше структура, но дороже)?
