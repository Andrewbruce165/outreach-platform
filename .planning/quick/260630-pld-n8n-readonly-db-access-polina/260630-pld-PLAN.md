---
quick_id: 260630-pld
slug: n8n-readonly-db-access-polina
description: Read-only доступ к outreach_platform для дашборда Полины через n8n (internal docker network, без внешних портов)
date: 2026-06-30
status: complete
---

# Quick Task 260630-pld: Read-only DB access for Polina's n8n dashboard

## Цель

Коллега (Полина) строит дашборд **внутри n8n**, который уже крутится на сервере.
Нужно дать ей read-only доступ к БД `outreach_platform`, чтобы n8n мог подключиться
с её кредами. **Внешний доступ (SSH / порты наружу) НЕ нужен** — n8n и наша БД
оба на сервере, соединяем по внутренней docker-сети.

> Изначальная формулировка («SSH-туннель + read-only») отменена пользователем:
> дашборд работает через серверный n8n, наружу открывать ничего не надо.

## Решение (3 части)

### 1. Read-only роль `polina_gocrazy` в Postgres
- `CREATE ROLE polina_gocrazy LOGIN PASSWORD '***'` (NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOREPLICATION)
- `GRANT CONNECT ON DATABASE outreach_platform`
- `GRANT USAGE ON SCHEMA public` + `GRANT SELECT ON ALL TABLES IN SCHEMA public` (26 таблиц)
- `ALTER DEFAULT PRIVILEGES FOR ROLE outreach_user IN SCHEMA public GRANT SELECT ON TABLES` —
  будущие таблицы (миграции авто-applier'а под `outreach_user`) тоже авто-читаются
- `REVOKE CREATE ON SCHEMA public` — никакого DDL/записи
- Пароль **НЕ коммитится в git** — передан пользователю в чат.

### 2. Сетевой путь n8n → outreach-platform-db
БД сидит в сети `tg-outreach_default` и НЕ опубликована на хост (только docker-сеть).
Контейнер `n8n` раньше её не видел. По уже существующему паттерну (n8n уже
подключён к `nocodb_nocodb_network` для доступа к nocodb-БД):
- **вживую (0 простоя):** `docker network connect tg-outreach_default n8n`
- **персистентно:** `tg-outreach_default: external: true` в
  `/root/apps/n8n/docker-compose.yml` + привязка к сервису `n8n` (переживёт пересоздание)

### 3. Параметры Postgres-credential для n8n (Полина вводит у себя)
- Host: `outreach-platform-db`
- Port: `5432`
- Database: `outreach_platform`
- User: `polina_gocrazy`
- Password: `***` (из чата)
- SSL: off (внутренняя docker-сеть)

## Verify
- [x] SELECT как polina работает; DELETE и CREATE TABLE → permission denied
- [x] n8n резолвит `outreach-platform-db` (172.28.0.2)
- [x] Пароль аутентифицируется по TCP из сети `tg-outreach_default` (mimics n8n)
- [x] `docker compose config` для n8n валиден после правки

## Done
- Роль создана, read-only подтверждён
- n8n подключён к сети (live + persisted)
- Креды переданы пользователю; в git пароля нет
