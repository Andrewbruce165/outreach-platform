---
title: Admin Master Bot — TG-бот workspace для уведомлений (deferred Phase 6 → v2)
trigger_condition: При планировании v2 — milestone v1.0 закрыт
planted_date: 2026-05-27
v2_code: ADMN-01..03
related_phases: [v2]
---

## Идея

Workspace имеет свой Telegram-бот (botfather token, хранится encrypted per-workspace). Бот шлёт админу уведомления при срабатывании ручника в любой активной кампании и при ошибках TG-аккаунтов (logout / FloodWait > N / session expired).

Изначально планировалось как Phase 6 в v1 — перенесено в v2.

## Скоуп

- Per-workspace storage botfather token (encrypted, как session strings)
- Регистрация admin chat: приватный чат с ботом или группа с ботом
- `/start` handler — приём `workspace_register_token` для привязки чата к workspace
- Event hooks в listener:
  - `conversations.status` → `manager` (ручник) → уведомление с дип-линком на диалог в UI
  - `senders.status` → `error` / `logged_out` / FloodWait > порога → уведомление с указанием аккаунта и причины
- Шаблоны уведомлений (workspace-customizable optional, в v2 — hardcoded ok)

## Зачем

1. Без бота клиент не узнаёт о ручнике вовремя (ручник = срочная ситуация, AI передал диалог человеку — нужна реакция в минутах)
2. Ошибки TG-аккаунтов сейчас теряются в логах — клиент видит просто что кампания "встала", без причины
3. Email/in-app notifications не подходят — клиент в Telegram целый день, уведомление в TG = моментально

## Зависимости

- Использует существующие signals (Phase 4) — handoff/lead/finish
- Использует conversations.status='manager' и senders.status (Phase 5)
- Pre-req для NUDGE-01 если nudge даёт уведомление "ошибка nudge"
- Может быть pre-req для INBD-01 (notify policy)

## Альтернатива

Email-уведомления (через Resend, как Vaultwarden) или in-app notifications без TG-бота. Дешевле, но хуже UX.

## Открытые вопросы

- Один бот на workspace или один на платформу с роутингом по chat_id? (Per-workspace дороже, но даёт брендинг)
- Хранить ли историю уведомлений для аудита?
