---
quick_id: 260701-fjz
title: Checker ban history analysis + account-checking strategy
type: analysis
status: complete
date: 2026-07-01
---

# Quick Task 260701-fjz — Аналитика банов чекеров и стратегия проверки аккаунтов

## Задача

Провести аналитику: **как чекеры исторически ловили ограничения, как мы их отпускали и возвращали**, и **продумать стратегию работы на проверке аккаунтов**. Учесть, что многие чекеры перешли в статус `sender`.

Это аналитическая задача (не изменение кода): работа = запросы к живым данным + синтез стратегии. Выполнена inline (у оркестратора есть DB-доступ и контекст notes), без planner/executor-агентов.

## Источники данных

- `senders` (роли, restriction_status, lifecycle_status, checker_trip_count, checker_rest_until) — срез 2026-07-01
- `sender_restriction_events` (301 событие, append-only с 2026-06-24)
- `contacts` (tg_status/tg_confidence/tg_resolved_by), `contacts_cache`
- `warmup_sessions`
- `app/config.py` (checker knobs), `app/services/contact_check_worker.py`
- Заметки: `checker-problem-and-history.md`, `sender-side-resolve-redesign.md`

## Задачи

1. **Собрать историческую аналитику банов** — распределение `sender_restriction_events` по типу/источнику/аккаунту, флаппинг, времена возврата (`spam_limited→cleared`), текущий срез пула. → `verify`: цифры из живой БД, не из памяти.
2. **Диагностировать текущее состояние** — сколько доступных чекеров по selection-gate, куда ушли рабочие чекеры, размер backlog. → `verify`: `available_checkers=0`, 5345 unchecked подтверждены запросом.
3. **Синтезировать стратегию** — 4 приоритизированных принципа + операционный план разморозки + метрики + развилки для решения. → `done`: `.planning/notes/checker-strategy.md`.

## Deliverable

`.planning/notes/checker-strategy.md` — аналитика на данных + стратегия (durable note, живёт в общей экосистеме checker-заметок).
