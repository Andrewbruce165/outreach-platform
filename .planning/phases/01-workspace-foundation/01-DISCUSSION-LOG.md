# Phase 1: Workspace Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-21
**Phase:** 1-workspace-foundation
**Areas discussed:** DB starting state & migration strategy; Supabase JWT validation & workspace_id source; Auto-create workspace on first login; JWT + Workspace API-key coexistence

---

## DB starting state & migration strategy

### Sub-question 1: какое стартовое состояние БД у outreach-platform?

| Option | Description | Selected |
|--------|-------------|----------|
| Пустая БД с нуля | Новый Postgres-инстанс в docker-compose outreach-platform. Никаких данных AGS Foods не переносим. Миграция: 012_workspace сразу с NOT NULL FK. | ✓ |
| Клон prod-БД telegram-api | Копируем существующие senders/contexts/conversations. Миграция сложнее: nullable + backfill в 'AGS Internal' workspace, затем ALTER в NOT NULL. | |
| Пока не решил — обсудим trade-off | Покажи плюсы/минусы. | |

**User's choice:** Пустая БД с нуля
**Notes:** outreach-platform — отдельный docker-compose stack на том же VPS, поэтому собственный Postgres-контейнер. AGS Foods продолжает работать в /root/apps/telegram-api/ независимо.

### Sub-question 2: глобальные ресурсы — scoped по workspace или shared?

| Option | Description | Selected |
|--------|-------------|----------|
| Proxy pool — shared; warmup_pool — scoped | Платформенные прокси, клиенты их не приносят. Warmup per-workspace. | |
| Всё scoped по workspace | BYO-proxy: клиент приносит свои прокси. proxy_pool.workspace_id NOT NULL. warmup_pool тоже scoped. | ✓ |
| Всё shared (платформенный подход) | Прокси + warmup-пул общие. Риск: аккаунты разных клиентов общаются в warmup'е. | |

**User's choice:** Всё scoped по workspace
**Notes:** Решение делает прокси проблемой клиента — в Phase 2 потребуется UI для загрузки прокси. Записано в deferred ideas.

### Sub-question 3: на каком уровне гарантируем изоляцию по workspace_id?

| Option | Description | Selected |
|--------|-------------|----------|
| App-уровень: фильтры в каждом запросе | AuthDep возвращает workspace_id, репо обязаны фильтровать. DB-уровень: NOT NULL FK + индексы. | |
| Postgres RLS | BEFORE-statement SET app.workspace_id, политики RLS фильтруют автоматически. Listener и worker должны выставлять контекст. | |
| App-уровень сейчас, RLS в v2 | Фильтры в Phase 1 для скорости. RLS в v2 для жёсткой изоляции. TODO-метки в коде. | ✓ |

**User's choice:** App-уровень сейчас, RLS в v2
**Notes:** Прагматичный выбор — быстрее довести до первого клиента. v2 RLS добавит структурную защиту от багов фильтрации.

---

## Supabase JWT validation & workspace_id source

### Sub-question 1: как FastAPI валидирует Supabase JWT?

| Option | Description | Selected |
|--------|-------------|----------|
| HS256 + JWT_SECRET из Supabase | Статический секрет из project settings. python-jose локально. Нет HTTP-вызовов, нет кэша. Ротация ключа — ручная. | ✓ |
| JWKS (asymmetric) с кэшированием | Тянем JWKS из Supabase, кэшируем TTL 1ч. Auto-rotation, но сложнее. | |
| Supabase серверная библиотека | supabase.auth.get_user(token) — внешний HTTP-вызов на каждый запрос, +50-100мс латенси. | |

**User's choice:** HS256 + JWT_SECRET (Рекомендуется для v1)
**Notes:** Для v1 простота важнее. Ротация JWT_SECRET — операционный процесс, не часто.

### Sub-question 2: откуда FastAPI берёт workspace_id после валидации JWT?

| Option | Description | Selected |
|--------|-------------|----------|
| Локальная таблица user_workspaces | FastAPI декодит JWT → берёт sub → SELECT в локальной таблице. Полный контроль, индекс. | ✓ |
| Custom claim в JWT (Supabase Edge Function) | Supabase выпускает JWT с workspace_id в claims. Нет DB lookup. Сложнее в настройке. | |
| Комбинация: lookup + in-memory кэш | Локальная таблица + LRU-кэш TTL 5мин. Один DB-запрос на user в 5 мин. Риск: лаг при смене workspace. | |

**User's choice:** Локальная таблица user_workspaces
**Notes:** Никакого кэша на v1. Простой SELECT с индексом. Custom claim — рассмотрим в v2 для оптимизации.

---

## Auto-create workspace on first login

### Sub-question 1: кто и когда создаёт workspace для нового юзера?

| Option | Description | Selected |
|--------|-------------|----------|
| Lazy: создаёт FastAPI middleware | AuthDep видит валидный JWT + пустой lookup → INSERT workspace + user_workspaces в одной транзакции. Lovable ничего не знает. | ✓ |
| Explicit: Lovable вызывает POST /onboarding | После signup Lovable ведёт юзера на экран 'Workspace name'. Остальные эндпоинты до этого — 403. | |
| Supabase trigger при signup | Postgres trigger на auth.users создаёт запись. Сложно: 2 разных Postgres'а. Переусложнено для v1. | |

**User's choice:** Lazy: создаёт FastAPI middleware
**Notes:** Минимум фронт-логики, всё авто. Имя workspace дефолтное — email из JWT, пользователь переименует.

### Sub-question 2: отношение user ↔ workspace в v1?

| Option | Description | Selected |
|--------|-------------|----------|
| 1 user = 1 workspace, жёстко | UNIQUE на supabase_user_id. Для v1 достаточно. v2 — снимем UNIQUE. | |
| user-workspace many-to-many с ролями | Схема без UNIQUE, role enum (owner/admin/member). v2 возможности включаются без миграции. JWT остаётся без workspace_id, lookup выбирает 'current'. | ✓ |

**User's choice:** user-workspace many-to-many с ролями
**Notes:** Закладываем гибкость сразу, в v1 lookup возвращает единственный workspace. v2 — header X-Workspace-Id для выбора active.

---

## JWT + Workspace API-key coexistence

### Sub-question 1: как живут рядом JWT (UI) и Workspace API-ключ (n8n)?

| Option | Description | Selected |
|--------|-------------|----------|
| Один Depends, ветвится по заголовку | AuthDep смотрит Authorization: Bearer → JWT; иначе X-Workspace-Key → хэш-lookup. Возвращает AuthCtx(workspace_id, user_id?, source). Один контракт. | ✓ |
| Два независимых Depends | verify_jwt() и verify_workspace_key() — каждый эндпоинт явно выбирает. Плюс: физически нельзя красть API-ключ и ходить в UI-настройки. | |
| JWT + scoped key, но разные роутеры | /api/v1/* — только JWT. /api/v1/integrations/* — только API-ключ. Чёткая разводка по префиксу. | |

**User's choice:** Один Depends, ветвится по заголовку
**Notes:** Простота и единый AuthCtx-контракт перевесили "physical separation" аргумент. Если в v2 потребуется ограничить API-ключи только определёнными эндпоинтами — добавим scope-логику в AuthCtx.

### Sub-question 2: хранение и жизненный цикл Workspace API-ключа?

| Option | Description | Selected |
|--------|-------------|----------|
| bcrypt-хеш в БД, plaintext только при создании | Юзер видит ключ один раз. В БД: bcrypt-хеш + первые 8-12 символов prefix. Стиль GitHub PAT/Stripe. | ✓ |
| SHA-256 хеш в БД | Прямой lookup по sha256(token). Быстрее, чем bcrypt, но при сливе БД более уязвимо (хотя 32-байтный random токен практически невзламываем). | |
| Plaintext в БД, всегда виден в UI | Стиль Postgres password в Supabase. Проще всего. Любой слив БД раскрывает все ключи. | |

**User's choice:** bcrypt-хеш в БД, plaintext только при создании
**Notes:** Безопаснее всего. Lookup-стратегия: парсим prefix → SELECT кандидатов → bcrypt.verify. У workspace может быть несколько ключей; регенерация = revoke старого + create нового.

### Sub-question 3: что делаем со старым X-API-Key middleware и 11 роутерами в Phase 1?

| Option | Description | Selected |
|--------|-------------|----------|
| Сносим сразу: clean slate | verify_api_key удаляем, все 11 роутеров убираем из main.py. Только workspace-скелет остаётся. Phase 2-4 переписывают по доменам. | ✓ |
| Deprecated fallback (legacy-режим) | X-API-Key работает с хардкоженным AGS Internal workspace_id. Старые роутеры пишут в этот workspace. | |
| Выпиливаем + сразу переписываем health/senders/contexts в Phase 1 | Расширяем скоуп Phase 1. Phase 1 раздувается, больше риска. | |

**User's choice:** Сносим сразу: clean slate
**Notes:** Внешних клиентов ещё нет. AGS Foods продолжает работать в /root/apps/telegram-api/. После Phase 1 outreach-platform отвечает только на новые workspace/auth-эндпоинты — это OK.

---

## Claude's Discretion

- C-01: точное имя файла AuthDep (`app/utils/auth.py` рекомендовано)
- C-02: длина prefix в workspace_api_keys (8 или 12 символов)
- C-03: точный список эндпоинтов workspace-скелета — researcher уточнит
- C-04: решение по `init_db()` Base.metadata.create_all в database.py — фиксить в Phase 1 или отложить

## Deferred Ideas

**Phase 2:**
- UI для загрузки клиентом своих прокси (BYO-proxy)
- Перепись senders.py + onboarding.py поверх workspace_id
- Замена subprocess.run(["docker","restart"]) в senders.py на DB-flag + listener-poll

**Phase 3 / Phase 4:**
- Перепись send.py, conversations.py, contexts.py, queue.py (router), check_contacts.py, warmup.py (router), proxy_pool.py поверх workspace_id и AuthCtx

**v2:**
- Postgres RLS на арендатор-скоупленных таблицах
- Выбор active workspace через X-Workspace-Id header или custom JWT claim
- Custom JWT claim workspace_id через Supabase Edge Function (устранит DB lookup)
- In-memory кэш user_id → workspace_id при росте нагрузки
- Team support (TEAM-01, TEAM-02): приглашение по email, роли уже в schema

**Tech debt обнаруженный во время обсуждения:**
- `init_db()` Base.metadata.create_all противоречит raw-SQL миграциям — фиксить или удалить
