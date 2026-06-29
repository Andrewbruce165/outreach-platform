---
quick_id: 260629-kn4
slug: username-implies-registered-status
date: 2026-06-29
---

# Quick Task: username ⇒ registered

## Problem

Контакт с `username` всё равно гонялся через checker (phone-resolve), а при наличии
И телефона И username — телефон побеждал, username игнорировался
(`contact_check_worker.py`: `phone_items = [r for r in items if r.phone]`).
Это лишняя нагрузка на checker: по `@username` аккаунту можно написать напрямую
(`ResolveUsername`), phone-resolve для таких контактов не нужен.

## Decision

Наличие `username` = достаточный сигнал «зарегистрирован». При импорте ставим
`tg_status='registered'` сразу — независимо от наличия телефона и checker'а.
ContactCheckWorker выбирает только `tg_status='pending'`, поэтому registered-контакты
автоматически минуют чекер.

Решение пользователя (AskUserQuestion): **новые + бэкфилл существующих**.

## Scope

1. `app/routers/contacts.py::_insert_contacts_with_dedup` — per-record статус:
   `'registered' if rec.get("username") else default_tg_status`. Покрывает оба пути
   ингеста (push + CSV import — оба зовут эту функцию).
2. `migrations/039_username_contacts_registered.sql` — идемпотентный бэкфилл:
   `UPDATE contacts SET tg_status='registered' WHERE tg_status IN ('pending','unchecked')
   AND username IS NOT NULL AND username <> ''`. Терминальные статусы
   (`not_registered`/`error`) НЕ трогаем.
3. Тесты: username-only → registered; phone+username → registered (username wins).

Схему не меняем — `registered` уже валиден в CHECK (миграция 013). Миграция не нужна
для схемы, только data-backfill.

## Out of scope

- Деплой (rebuild api) — отдельный user-gated шаг; миграция применится на старте api.
- Фронт — без изменений (статус приходит из API как есть).
