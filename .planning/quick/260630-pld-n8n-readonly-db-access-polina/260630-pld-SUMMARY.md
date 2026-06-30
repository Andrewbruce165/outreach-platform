---
quick_id: 260630-pld
slug: n8n-readonly-db-access-polina
date: 2026-06-30
status: complete
---

# Summary — 260630-pld: Read-only DB access for Polina's n8n dashboard

## Что сделано

**1. Read-only роль `polina_gocrazy`** в `outreach_platform` (выполнено как owner `outreach_user`):
```sql
CREATE ROLE polina_gocrazy LOGIN PASSWORD '***';   -- DO-guarded, идемпотентно
ALTER ROLE polina_gocrazy NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
GRANT CONNECT ON DATABASE outreach_platform TO polina_gocrazy;
GRANT USAGE ON SCHEMA public TO polina_gocrazy;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO polina_gocrazy;
ALTER DEFAULT PRIVILEGES FOR ROLE outreach_user IN SCHEMA public GRANT SELECT ON TABLES TO polina_gocrazy;
REVOKE CREATE ON SCHEMA public FROM polina_gocrazy;
```
Проверено: SELECT (2 строки из `ai_contexts`) ✓ · `DELETE` → permission denied ✓ ·
`CREATE TABLE` → permission denied for schema public ✓.

**2. Сеть n8n → БД.** БД `outreach-platform-db` живёт в `tg-outreach_default` (172.28.0.2),
на хост НЕ опубликована. Контейнер `n8n` подключён к этой сети:
- live: `docker network connect tg-outreach_default n8n` (n8n получил IP 172.28.0.6, 0 простоя)
- persisted: `/root/apps/n8n/docker-compose.yml` — добавлена `tg-outreach_default: external: true`
  + привязка к сервису `n8n` (по образцу `nocodb_nocodb_network`). `docker compose config` валиден.
  n8n НЕ пересоздавался — правка подхватится при следующем `docker compose up -d n8n`.

Проверка связности: n8n резолвит `outreach-platform-db`→172.28.0.2; psql по TCP из
`tg-outreach_default` под `polina_gocrazy` + пароль → `current_user=polina_gocrazy`,
`current_database=outreach_platform` ✓.

**3. Параметры для Postgres-credential в n8n** (Полина создаёт credential у себя):
| поле | значение |
|---|---|
| Host | `outreach-platform-db` |
| Port | `5432` |
| Database | `outreach_platform` |
| User | `polina_gocrazy` |
| Password | передан в чате (в git НЕТ) |
| SSL | off |

## Файлы / изменения вне репо
- `/root/apps/n8n/docker-compose.yml` — добавлена внешняя сеть `tg-outreach_default`
  (серверный конфиг чужого стека; в git tg-outreach не входит, задокументировано здесь).
- Роль `polina_gocrazy` — состояние БД (не в git).

## Безопасность
- Доступ строго read-only (SELECT). Без записи, без DDL, без superuser.
- Только внутренняя docker-сеть — наружу/в интернет ничего не открыто, портов на хосте не добавлено.
- Пароль не в репозитории; при желании положить в Vaultwarden (`passwords.agsventurelab.com`).

## Откат
```sql
-- убрать доступ
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM polina_gocrazy;
ALTER DEFAULT PRIVILEGES FOR ROLE outreach_user IN SCHEMA public REVOKE SELECT ON TABLES FROM polina_gocrazy;
REVOKE USAGE ON SCHEMA public FROM polina_gocrazy;
REVOKE CONNECT ON DATABASE outreach_platform FROM polina_gocrazy;
DROP ROLE polina_gocrazy;
```
```bash
# отключить n8n от сети + убрать из compose
docker network disconnect tg-outreach_default n8n
# затем откатить правку networks в /root/apps/n8n/docker-compose.yml
```
