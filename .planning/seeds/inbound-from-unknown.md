---
title: Обработка входящих от ранее незнакомых пользователей
trigger_condition: При планировании v2 — milestone v1.0 закрыт
planted_date: 2026-05-27
v2_code: INBD-01
related_phases: [v2]
---

## Идея

Сейчас listener реагирует только на open conversations (созданные outbound first-message из кампании). Если контакт сам пишет первым (или знакомый из старой кампании, conversation удалён) — игнорируется.

Дать workspace policy: что делать с такими incoming.

## Скоуп

- Listener: при `incoming message` от `phone_id`, для которого нет conversation → попасть в policy-обработку
- Policy на уровне workspace или per-account (TBD):
  - **`auto_reply`** — создать conversation + AI отвечает по default-agent для inbound (workspace.default_inbound_agent_id)
  - **`ignore`** — ничего не делать, не сохранять
  - **`notify`** — сохранить как conversation со специальным статусом `new_inbound_pending`, показать в inbox, опционально уведомить через admin-bot (ADMN-01)
- Default policy = **`ignore`** (безопасный)
- В inbox показывать как отдельную секцию "Новые входящие — без кампании"

## Зачем

1. Клиенты ловят входящие от потенциальных лидов (sapomy не отправляли) — сейчас теряются
2. Знакомые контакты которые пишут после прошлой завершённой кампании — тоже теряются если conversation удалён
3. Защита от spam-юзеров и атаки-через-входящие — нужна ignore-policy чтобы AI не отвечал на провокации

## Зависимости

- `app/services/listener.py` — добавить ветку для unknown-conversation incoming
- Настройка на `workspaces.inbound_policy` и/или `senders.inbound_policy` (sender override)
- Связано с ADMN-01 — `notify` policy требует admin-bot

## Альтернатива

Только `notify` (показать в inbox без AI-ответа) — самый безопасный MVP, без auto_reply.

## Открытые вопросы

- Какого агента использовать для `auto_reply`? Дефолтный workspace-agent или новый "default_inbound_agent"?
- Что делать если контакт пишет в несколько подключенных аккаунтов workspace одновременно? (Дедуп по phone?)
- Должна ли auto_reply policy создавать псевдо-кампанию (для аналитики) или conversation без campaign_id (campaign_id NULL уже supported в Phase 04)?
