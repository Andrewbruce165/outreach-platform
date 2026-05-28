---
title: Скорость ответа AI (debounce) — workspace / agent override
trigger_condition: При планировании v2 — milestone v1.0 закрыт
planted_date: 2026-05-27
v2_code: REPLY-01
related_phases: [v2]
---

## Идея

Сейчас debounce ответов 3–5 мин в listener захардкожено (`app/services/listener.py`). Вынести как настройку с уровнем override workspace → agent.

## Скоуп

Решение по уровню настройки:

- **A** Workspace-default только — простая модель
- **B** Workspace-default + agent-override — гибче (**рекомендация**)
- **C** Per-campaign — максимальная гибкость, но overkill

Рекомендуется B: workspace задаёт дефолт, agent может override под свой характер ("энергичная Аня" vs "вдумчивый Иван").

- Поля:
  - `workspaces.reply_debounce_min`, `workspaces.reply_debounce_max`
  - `agents.reply_debounce_min` / `agents.reply_debounce_max` — NULLable override
- UI: ползунок 1–15 мин с подписями "слишком быстро = выглядит как бот; слишком медленно = клиент уже ушёл"
- Жёсткий пол = 30 секунд (anti-bot detection)

## Зачем

1. Разные ниши = разные ожидания (B2B SaaS — нормально 5 мин, e-commerce — нужно <2 мин)
2. Часть клиентов в support жаловалась что AI отвечает "слишком быстро/медленно"
3. Связано с QUEUE-01 — anti-spam settings cluster, единый раздел в UI

## Зависимости

- `app/services/listener.py` — текущая константа становится default из настроек
- Cluster с QUEUE-01 (вместе в UI "Anti-Spam Settings")

## Альтернатива

Уровень A (workspace-only) — проще, меньше комбинаций для тестов. Если в support не жалуются на per-agent — оставить A.

## Открытые вопросы

- Какой минимум reply speed безопасен (с т.з. anti-bot detection)? Возможно жёсткий пол = 30 сек
- Variance (jitter) внутри min/max — нужна обязательно (chat-bot palette) или можно single value?
