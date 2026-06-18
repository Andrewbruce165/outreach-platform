---
quick_id: 260618-r7k
slug: campaign-allow-recontact
date: 2026-06-18
---

# Quick Task: per-campaign re-contact policy (allow_recontact)

Cross-campaign дедуп в `campaign_enqueue.py` никогда не пишет повторно контакту,
у которого уже есть ЛЮБОЙ диалог в workspace. Корректно как дефолт, но навсегда
блокирует переконтакт тех, чей старый диалог закрыт или давно неактивен (именно
поэтому кампания «Barter» давала 0 подходящих контактов).

## Scope (подтверждено пользователем)

1. **Миграция 026**: `campaigns.allow_recontact BOOL DEFAULT false` +
   `recontact_min_age_days INT DEFAULT 30`. Default false → существующие кампании
   не меняются. Триггер `messages_touch_conversation` бампает
   `conversations.updated_at` на INSERT в `messages` (ORM-onupdate не срабатывал
   на raw-SQL записи → updated_at был «мёртв»).
2. **recontact.py** — единый предикат «защищённый диалог»:
   `status IN (active, manual, paused, lead, handoff, bot_ignored)` И свежий в
   пределах N дней. `bot_ignored` намеренно остаётся защищённым.
3. **campaign_enqueue**: при `allow_recontact` блокирует только защищённый диалог;
   закрытые (`finished`) / устаревшие — снова eligible. Strict-режим без изменений.
4. **queue._upsert_conversation**: при `allow_recontact` переиспользует строку
   только если она защищённая+свежая, иначе INSERT новой (чистая AI-история).
5. **listener routing**: `ORDER BY created_at DESC LIMIT 1` — входящие в новейший
   диалог; чинит латентный недетерминизм при >1 строке на peer.

## Решения пользователя

- Свежесть — через DB-триггер на `updated_at` (надёжнее `MAX(messages.created_at)`).
- `bot_ignored` — остаётся защищённым (холодный опенер не уходит на системного бота).
- Живую кампанию остановили, все тестовые диалоги удалили (бэкап снят).

## Ops (разово, не в коммите)

- Бэкап БД `outreach_20260618_133622.sql.gz`.
- Кампания «Barter» (`5f8750cb…`) `running → paused`.
- `DELETE` 9 conversations (каскад messages+llm_calls), 1 assignment, 5 queue.

## Файлы

- `migrations/026_campaign_allow_recontact.sql` (new)
- `app/services/recontact.py` (new)
- `app/models/__init__.py` — Campaign +2 колонки
- `app/services/campaign_enqueue.py`, `app/services/queue.py`, `app/services/listener.py`
- `tests/test_recontact.py` (new), `tests/conftest.py`

## Verify

- Тесты через test-overlay: `test_recontact.py` (6) + enqueue/queue/listener — зелёные.
- Миграция 026 применена автоаплайером на проде; колонки + триггер на месте.
