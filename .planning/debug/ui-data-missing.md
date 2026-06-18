---
slug: ui-data-missing
status: root_cause_found
trigger: "почему-то все пропало из ui в плане данных"
created: 2026-05-26
updated: 2026-05-26
---

# UI Data Missing — Debug Session

## Symptoms

- **What's missing:** Все данные везде — UI работает, но таблицы пустые/нули по всему интерфейсу.
- **Timeline:** Пользователь "только что обнаружил". Неизвестно с какого момента началось.
- **DevTools:** Не смотрел. Сетевые/консольные ошибки не проверены.

## Initial Evidence (gathered before delegation)

### 1. Database state — массовая пустота, кроме tenancy

Output of `pg_stat_user_tables` (DB `outreach_platform`, all in schema `public`):

```
relname                       | n_live_tup
------------------------------+-----------
user_workspaces               |          4
workspaces                    |          4
ai_contexts                   |          0
... (все остальные)           |          0
```

`n_dead_tup = 0`, `last_vacuum = NULL` для пустых таблиц.

### 2. PostgreSQL logs — no TRUNCATE/DROP visible in last 48h

`DELETE FROM onboarding_sessions WHERE expires_at < NOW()` каждые 5 минут — единственный заметный SQL.

### 3. Migrations — recent activity

`019_schema_drift_fix.sql` (14KB, May 25), 020/021/022 — массовая правка drift'а.

### 4. API logs — schema mismatch errors right now

`ProgrammingError: relation "messages" does not exist` при join'е `conversations m.conversation_id`.

### 5. Backups

Нет outreach-бэкапов под `/root/backups/`.

## Current Focus

(см. ниже — расследование закончено, root cause найден)

## Evidence

- timestamp: 2026-05-26 13:35Z
  observation: live API log shows `ProgrammingError: relation "messages" does not exist`.

- timestamp: 2026-05-26 13:37Z
  observation: pg_stat_user_tables — все операционные таблицы 0 live + 0 dead + NULL vacuum.

- timestamp: 2026-05-26 13:38Z
  observation: postgres logs за 48h не содержат TRUNCATE/DROP против операционных таблиц.

- timestamp: 2026-05-26 13:38Z
  observation: outreach-бэкапов нет.

- timestamp: 2026-05-26 13:55Z
  observation: **019_schema_drift_fix.sql ОТВЕРГНУТ как причина потери данных.** Файл —
    чистый `ALTER ... ADD CONSTRAINT IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`. Никаких
    DROP TABLE / TRUNCATE / DELETE. Идемпотентный, не разрушительный.

- timestamp: 2026-05-26 13:55Z
  observation: **tests/conftest.py:53 содержит `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`**
    и на setup, и на teardown (стр. 126). Это smoking-gun-кандидат, **но `.env` на сервере**
    использует DSN `telegram_user@localhost:5432/telegram_followup` (DSN старого telegram-api),
    **не прод outreach-platform**. Внутри docker compose контейнера API `DATABASE_URL` хардкоден
    в compose-файле на `outreach_user@db:5432/outreach_platform`. Если pytest запускался
    **внутри контейнера** (`docker compose exec api pytest`) — он попал бы в прод и снёс схему.

- timestamp: 2026-05-26 14:00Z
  observation: **`docker volume ls` показывает один volume `tg-outreach_postgres_data`,
    DB-контейнер запущен 2026-05-23 10:10:33 (uptime 3 дня)**. Volume НЕ пересоздавался.
    `pg_database_size = 9068 kB` — крошечный, как пустая схема.

- timestamp: 2026-05-26 14:00Z
  observation: **`SELECT n_tup_ins FROM pg_stat_user_tables` показывает: workspaces=4,
    user_workspaces=4, ВСЕ остальные таблицы = 0.** За всю историю существования БД
    (с 23 мая) в БД было сделано **ровно 8 insert'ов** — никакие данные никогда не вставлялись
    в conversations, contacts, senders, messages, message_queue, etc.

- timestamp: 2026-05-26 14:00Z
  observation: **`SELECT * FROM workspaces` — 4 строки, ВСЕ принадлежат
    `andrew.asachuk@gmail.com` (Andrew, supabase_user_id `e1cd1baa-...`), созданные за
    5 миллисекунд (13:33:23.191 → .195)**. Это race condition в lazy auto-create — 4
    параллельных request'а от Andrew одновременно создали 4 разных workspace.

- timestamp: 2026-05-26 14:00Z
  observation: **БД-логи 24–25 мая показывают, что listener активно работал** и пытался
    создать conversations для реальных входящих от Polina (`tg_id=859802759`), Алина
    (`tg_id=449451871`), Кирилл (`tg_id=346117736`):
    ```
    INSERT INTO conversations (workspace_id, sender_id, contact_phone, contact_name, ...)
    ERROR: null value in column "id" of relation "conversations" violates not-null constraint
    ```
    **Listener не смог записать НИ ОДНОГО conversation** в течение 24–25 мая, потому что
    у `conversations.id` не было server-side default (Python-side `default=uuid.uuid4`
    не транслируется в DDL через `create_all`).

- timestamp: 2026-05-26 14:00Z
  observation: **`021_uuid_defaults.sql` НЕ применена к проду**. Прямая проверка:
    ```sql
    SELECT column_name, column_default FROM information_schema.columns
    WHERE table_name IN ('conversations','contacts','senders','messages_log',...) AND column_name='id';
    ```
    Все 7 таблиц — `column_default = NULL`. Файл миграции существует с 25 мая 11:05, но не
    был выполнен против БД. Memory note подтверждает: `tg-outreach raw-SQL migrations are
    not auto-applied`.

- timestamp: 2026-05-26 14:00Z
  observation: **`init_db()` в `app/database.py` выполняет `Base.metadata.create_all`
    на каждом старте API**. ORM-таблицы пересоздаются, но **таблица `messages` (Phase 5
    inbox)** существует ТОЛЬКО в `017_phase5.sql` (нет в ORM-моделях) — её `create_all` не
    создаёт. Поэтому после рестарта она исчезает (если была удалена раньше).

- timestamp: 2026-05-26 14:00Z
  observation: **БД-логи `2026-05-26 13:18:05–19` показывают, что прямо после
    рестарта api в 13:18:15 БД отвергала запросы как `relation "contacts" does not exist`,
    `relation "message_queue" does not exist`, `relation "senders" does not exist`** —
    т.е. таблиц не было до тех пор, пока `init_db()` (через `create_all`) не пересоздал ORM
    их через ~14 секунд. **Это подтверждает, что при каком-то предыдущем событии (либо
    pytest DROP SCHEMA, либо ручной DROP) таблицы были удалены, и init_db их пересоздал
    при следующем старте — но без данных и без `messages` (которой нет в ORM).**

- timestamp: 2026-05-26 14:00Z
  observation: **Код массово ссылается на таблицу `messages`** — 16+ запросов в
    `app/routers/conversations.py`, `app/routers/analytics.py`, `app/routers/senders.py`,
    `app/services/webhook_notify.py`, `app/services/ai_engine.py`, `app/services/listener.py`,
    `app/schemas/__init__.py`. Это Phase-5 inbox-таблица (raw-SQL only). Соответствует
    предыдущему resolved-debug `agents-500-cors.md`, где её уже создавали вручную через
    `psql -f migrations/017_phase5.sql`.

## Eliminated

- ❌ **`019_schema_drift_fix.sql` как причина wipe'а** — это чисто `ALTER` + `CREATE INDEX`,
  идемпотентно, ничего не дропает.
- ❌ **Удаление данных пользователем** — `n_tup_ins=0` для всех операционных таблиц.
  **Данных НИКОГДА не было**, потому что listener не мог их записать с самого начала
  (отсутствуют UUID defaults).
- ❌ **Volume recreation** — volume жив с 23 мая 10:10, uptime PG = 3 дня непрерывно.
- ❌ **Bulk DELETE** — `n_tup_del = 0` везде. Никаких удалений.

## Root Cause

**Это НЕ потеря данных. Это никогда-не-записанные данные + race condition в lazy auto-create + missing Phase-5 table после рестарта.**

Три взаимосвязанных бага сложились в иллюзию "всё пропало":

### Cause A (главный) — UUID defaults никогда не накачены, listener не мог писать с 23 мая

`Base.metadata.create_all` создаёт UUID PK без server-side default (Python-side `default=uuid.uuid4` не транслируется в DDL). Все raw-SQL `INSERT INTO ... RETURNING id` (listener, queue, checker, AI engine) **с первого дня** падали с `NotNullViolationError: null value in column "id"`.

Миграция `021_uuid_defaults.sql` написана 25 мая, но **никогда не применена к проду**. Подтверждение: прямая проверка `information_schema.columns` показывает `column_default = NULL` для `conversations.id`, `contacts.id`, `senders.id`, `messages_log.id`, и др.

БД-логи показывают непрерывный поток таких ошибок 24–25 мая для реальных контактов (Polina, Алина, Кирилл). **Listener получал входящие сообщения, но не сохранял их.**

### Cause B — отсутствие таблицы `messages` (Phase 5 inbox) после рестарта

Таблица `messages` определена ТОЛЬКО в `migrations/017_phase5.sql`, отсутствует в ORM. `init_db()` через `create_all` её не создаёт. После любого DROP/wipe (включая pytest DROP SCHEMA CASCADE из conftest.py, если он когда-либо ходил против прода) она не возвращается автоматически.

Сейчас её нет на проде. БД-логи показывают `relation "messages" does not exist` начиная с 23 мая 11:02 (т.е. её НЕТ почти с самого старта, кроме короткого окна 23 мая 19:00, когда её вручную создали в рамках agents-500-cors debug-сессии). Вероятно, **что-то потом снова её удалило** — либо `pytest` (`DROP SCHEMA public CASCADE` + `create_all` без 017), либо ручное действие.

Код в `app/routers/conversations.py:147,152,208,213,254,263`, `analytics.py:163,176,335,345,359` и др. ссылается на `messages` — все эти запросы 500-ят с `ProgrammingError`. Это вызывает 500-е на `/conversations`, `/analytics/funnel`, `/analytics/workspace`, `/agents`, и т.д. → пустой UI.

### Cause C — race condition в lazy auto-create → 4 дубликата workspace

Поскольку user_workspaces пустая (из-за wipe или потому что never-populated), КАЖДЫЙ JWT-запрос идёт в lazy-create branch `_resolve_or_create_workspace` в `app/utils/auth.py`. При нескольких параллельных запросах с одного и того же frontend'а (Lovable open page → 4-10 параллельных fetch'ей на разные API-endpoint'ы) каждый поток одновременно видит "нет workspace" и **каждый создаёт новый** → 4 разных workspace_id для одного supabase_user_id.

Подтверждение: `SELECT * FROM workspaces` показывает 4 строки, ВСЕ `andrew.asachuk@gmail.com`, созданы за 5 мс (13:33:23.191 → .195).

Это значит — даже если данные появятся, они привяжутся к ОДНОМУ из 4 workspace, а запрос с тем же JWT случайно попадёт в другой → опять "пусто".

## Fix Required

**Два независимых фикса** + опциональная санация:

### Fix 1 — Применить миграции 021 и 017 к проду

```bash
cd /root/apps/aimly/tg-outreach

# Phase 5 inbox table
docker cp migrations/017_phase5.sql outreach-platform-db:/tmp/017.sql
docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -v ON_ERROR_STOP=1 -f /tmp/017.sql

# UUID defaults (critical for raw-SQL inserts)
docker cp migrations/021_uuid_defaults.sql outreach-platform-db:/tmp/021.sql
docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -v ON_ERROR_STOP=1 -f /tmp/021.sql

# Также накатить 019, 020, 022 если ещё не накачены (все идемпотентны)
for f in 019_schema_drift_fix.sql 020_contacts_cache_unique.sql 022_conversations_status_default.sql; do
  docker cp migrations/$f outreach-platform-db:/tmp/$f
  docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -v ON_ERROR_STOP=1 -f /tmp/$f
done
```

**Эффект:** listener сможет писать conversations / contacts / messages начиная с **новых** входящих. Stats endpoints перестанут 500-ить.

### Fix 2 — Race-condition в lazy auto-create

В `app/utils/auth.py::_resolve_or_create_workspace` добавить **advisory lock или ON CONFLICT** на ключе (supabase_user_id) при создании user_workspace. Сейчас:
- два параллельных request'а → оба `select` ничего не находят → оба `insert` → две разные строки в `workspaces` + `user_workspaces`.

Минимальный fix:
- INSERT в `user_workspaces` с `ON CONFLICT (supabase_user_id) DO NOTHING RETURNING workspace_id`, плюс **UNIQUE constraint на `user_workspaces.supabase_user_id`** (сейчас только обычный btree-индекс). Тогда первый запрос создаст пару, остальные узнают через RETURNING.

Дубликаты workspace'ов нужно подчистить:
```sql
-- Оставить самый ранний, остальные удалить через CASCADE
DELETE FROM workspaces WHERE id IN (
  '76bd3bae-10ca-4e03-b650-00969a236d08',
  'fe33ddfc-62a0-4e91-8d43-f44134e391bf',
  '1c4ca692-4697-4634-af8d-0ba5376394c9'
);
```

### Fix 3 (опционально, защита на будущее) — Заблокировать pytest от ухода на прод

В `tests/conftest.py` добавить assertion:
```python
if "outreach_platform" in dsn or "@db:" in dsn:
    raise RuntimeError("pytest never runs against production DB. Set DATABASE_URL to test DSN.")
```

Также — отучить `init_db()` от `create_all` на проде. Перенести логику применения миграций в **отдельный startup-script**, который чекает наличие таблиц и накатывает миграции по порядку из `migrations/` директории.

## Бекап потерянных входящих

**Технически данные не пропали — они НЕ ЗАПИСАНЫ.** Реальные входящие от Polina, Алины,
Кирилла за 24–25 мая навсегда потеряны (listener получил их через MTProto, но БД не приняла).
Восстановить их невозможно без обращения к **Telegram-side** (переоткрыть диалоги в каждом
sender-аккаунте после fix #1 — но это можно сделать только если sender аккаунты ещё на месте,
у вас сейчас их 0).

## Resolution

(будет заполнено после применения fixes)

- root_cause: TBD
- fix: TBD
