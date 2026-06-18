---
quick_id: 260618-r7k
slug: campaign-allow-recontact
date: 2026-06-18
status: complete
commit: 08a1c5a
---

# Summary: per-campaign re-contact policy (allow_recontact)

Добавлен opt-in флаг переконтакта на уровне кампании. Default `false` → поведение
существующих кампаний не меняется. При `allow_recontact=true` cross-campaign дедуп
блокирует только **защищённый** (живой и свежий) диалог; закрытые/устаревшие —
снова доступны для первого касания, причём в **новой** строке `conversations`
(пустая AI-история = настоящий fresh start).

## Что сделано

- **migration 026** — `campaigns.allow_recontact` (BOOL, false) +
  `recontact_min_age_days` (INT, 30) + триггер `messages_touch_conversation`,
  бампающий `conversations.updated_at` на каждый INSERT в `messages`
  (ORM-`onupdate` не срабатывал на raw-SQL — `updated_at` был непригоден как
  сигнал активности).
- **app/services/recontact.py** — единый предикат «защищённый диалог»
  (`PROTECTED_STATUSES` + `protected_conversation_sql`), общий для enqueue и upsert,
  чтобы логика не разъезжалась. `bot_ignored` остаётся защищённым.
- **campaign_enqueue.py** — дедуп-подзапрос условный по `allow_recontact`.
- **queue.py::_upsert_conversation** — политика кампании тянется один раз;
  переиспользование строки только при защищённом+свежем диалоге, иначе новая;
  оба lookup детерминированы (`ORDER BY … LIMIT 1`).
- **listener.py::get_or_create_conversation** — роутинг входящих в новейший
  диалог (`ORDER BY created_at DESC LIMIT 1`); попутно устранён недетерминизм
  `fetchone()` при дублях строк на один peer.

## Тесты

`tests/test_recontact.py` (6): default-blocks-finished, finished-released,
active-fresh-protected, stale-released, bot_ignored-protected, updated_at-trigger.
Все зелёные через test-overlay. conftest применяет 026 (для триггера) и
`test_contacts_factory` теперь принимает override-поля без TypeError
(пред-существующий баг, всплыл на render-тесте).

## Деплой и ops

- Бэкап `outreach_20260618_133622.sql.gz`.
- Кампания «Barter» (`5f8750cb…`) переведена `running → paused`.
- Удалены все тестовые данные: 9 conversations (каскад messages+llm_calls),
  1 assignment, 5 queue-items.
- `docker compose up -d --build api` (автоаплайер применил 026: колонки + триггер
  подтверждены) + `listener`. Оба сервиса здоровы.

## Замечания

- Пред-существующие падения в `test_send_campaign.py` / `test_listener_reconcile.py`
  (`user_workspaces.user_id does not exist`, ImportError) — НЕ связаны с этой
  задачей (conftest не применяет миграции 019–025 → дрейф схемы `user_workspaces`).
  Зафиксировано на baseline через `git stash`.
- Чтобы протестировать фичу на «Barter»: выставить `allow_recontact=true` на
  кампании и снять с паузы (`paused → running`). 2 registered-контакта
  (Polina, RomanVdr) пройдут фильтр, т.к. их диалоги удалены.
