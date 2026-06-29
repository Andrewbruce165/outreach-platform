---
quick_id: 260629-kn4
slug: username-implies-registered-status
date: 2026-06-29
status: complete
---

# Summary: username ⇒ registered

## Что сделано

- **`app/routers/contacts.py::_insert_contacts_with_dedup`** — статус считается per-record:
  `tg_status = "registered" if rec.get("username") else default_tg_status`. Один choke-point
  покрывает оба пути ингеста (push `POST /contacts` + CSV `import`). username побеждает:
  контакт с И телефоном И username тоже получает `registered`.
- **`migrations/039_username_contacts_registered.sql`** — идемпотентный бэкфилл
  `'pending'/'unchecked'` + непустой username → `'registered'`. Терминальные статусы
  (`not_registered`/`error`) не трогаются (могли быть проставлены осознанно).
- **Тесты** (`tests/test_contacts.py`): `test_push_username_only_sets_registered`,
  `test_push_phone_and_username_sets_registered`. Все существующие D-20-тесты статуса
  используют phone-only контакты → не задеты.

## Verification

- `pytest tests/test_contacts.py` через test-overlay → **24 passed** (22 + 2 новых).
- Read-only прод-срез: контактов с username — 3, все уже `registered` ⇒ бэкфилл затронет
  **0 строк** на текущем проде (миграция корректна/идемпотентна для будущих контактов и
  других workspace).

## Эффект

Контакты с `@username` больше не попадают в очередь чекера (worker берёт только
`tg_status='pending'`) — снимает лишнюю нагрузку с phone-resolve и устраняет старое
правило «phone wins, username ignored». См. [[project-phase14-checker-throttle-pool-wide]].

## NB

- **НЕ задеплоено.** Миграция-бэкфилл применится на старте api при следующем
  `docker compose up -d --build api` (user-gated, как и другие отложенные OPS).
