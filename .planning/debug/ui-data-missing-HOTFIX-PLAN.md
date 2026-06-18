---
slug: ui-data-missing-hotfix
parent_session: ui-data-missing
status: ready_to_execute
created: 2026-05-26
updated: 2026-05-26 (revised after physical evidence of DROP SCHEMA)
mode: hotfix
data_loss: confirmed (no backups, accepted by user)
---

# Hotfix Plan — UI Data Missing (revised)

Reference: `.planning/debug/ui-data-missing.md` (root cause analysis).

**Revised diagnosis:** `DROP SCHEMA public CASCADE` выполнился в 2026-05-26 13:18:21 UTC
(подтверждено идентичным `file_mtime` всех 22 relations через `pg_stat_file`). Источник —
`tests/conftest.py::_setup_database` через `docker compose run --rm api pytest`. Бэкапов нет
(`archive_mode=off`, нет pg_dump на хосте, нет DO snapshot для этой точки времени).
Данные считаются потерянными — пользователь принял это решение.

**Приоритет № 1 — никогда больше.** Wave 1 = guard от повторения катастрофы + ежедневный
pg_dump. Wave 2 = вернуть рабочее состояние схемы и кода (миграции + race fix), чтобы новые
данные могли писаться корректно.

Each task has `read_first` and `acceptance_criteria` so it is safe to execute step-by-step.

---

## Task 0 — Daily pg_dump для outreach_platform (Wave 1)

**Why:** В `/root/backups/` уже есть рабочая инфраструктура daily бэкапов для nocodb
(`nocodb_YYYYMMDD_040001.sql.gz` каждые 24h). Для tg-outreach аналогичного нет — поэтому
сегодняшний инцидент стал безвозвратным. Создаём тот же паттерн, retention 14 дней.

**Wave:** 1 (выполняется первым, до любых других изменений в проде).
**Depends on:** —
**Files modified:**
  - `/etc/cron.d/outreach-backup` (новый, по образцу nocodb)
  - `/root/backups/tg-outreach/` (новая директория)
**Autonomous:** yes (после согласования содержимого cron-файла с пользователем).

<read_first>
- /etc/cron.d/* — посмотреть существующий cron для nocodb (если он в /etc/cron.d/) и взять
  образец синтаксиса. Если nocodb-backup делается иначе (systemd timer, скрипт в /root/bin),
  взять оттуда.
- `ls /root/backups/nocodb/` — формат имени файла, размеры (sanity check).
- Существует ли уже `/root/bin/backup-*.sh` для nocodb — да/нет.
</read_first>

<action>
1. Найти как сделан nocodb backup (cron file или systemd timer), скопировать паттерн.
2. Создать скрипт `/root/bin/backup-outreach.sh`:
   ```bash
   #!/bin/bash
   set -euo pipefail
   DEST=/root/backups/tg-outreach
   mkdir -p "$DEST"
   STAMP=$(date -u +%Y%m%d_%H%M%S)
   docker exec outreach-platform-db pg_dump \
     -U outreach_user -d outreach_platform --clean --if-exists \
     | gzip > "$DEST/outreach_${STAMP}.sql.gz"
   # Retention: keep last 14 daily backups
   ls -1t "$DEST"/outreach_*.sql.gz | tail -n +15 | xargs -r rm -f
   ```
   `chmod 755 /root/bin/backup-outreach.sh`.
3. Cron entry в `/etc/cron.d/outreach-backup` (запуск в 04:05 UTC, через 5 минут после nocodb):
   ```
   5 4 * * * root /root/bin/backup-outreach.sh >> /var/log/outreach-backup.log 2>&1
   ```
4. Сразу запустить первый backup вручную: `/root/bin/backup-outreach.sh`. На этот момент БД
   пустая, но это smoke-тест что pg_dump работает и retention/gzip правильные.
</action>

<acceptance_criteria>
- `ls -la /root/backups/tg-outreach/` показывает хотя бы один `outreach_*.sql.gz` от сегодня.
- `gunzip -t /root/backups/tg-outreach/outreach_*.sql.gz` — корректный gzip (exit 0).
- `zcat /root/backups/tg-outreach/outreach_*.sql.gz | head -20` показывает корректный SQL
  начинающийся с `-- PostgreSQL database dump`.
- `grep outreach /etc/cron.d/outreach-backup` или эквивалент в systemctl timers — присутствует.
- Через 1 минуту: `tail /var/log/outreach-backup.log` (если cron уже отстрелял в эту минуту)
  или ручной dry-run скрипта — exit 0.
</acceptance_criteria>

---

## Task 3 (поднят до Wave 1) — Guard в `tests/conftest.py` против прод-DSN

**Why:** **Это main lesson.** Сегодня в 13:18:21 UTC именно эта дыра уничтожила прод.
Никаких отговорок про «event-loop spared us» — DROP реально выполнился (доказательство:
идентичный `file_mtime` у всех 22 relations через `pg_stat_file`). Гард обязателен
до начала любого другого фикса; без него Task 1/Task 2 не имеют смысла — следующий pytest
снова всё снесёт.

**Wave:** 1 (параллельно с Task 0, не зависят).
**Depends on:** —
**Files modified:**
  - `tests/conftest.py` (добавить guard в самое начало `_setup_database`).
  - `docker-compose.yml` (опционально — добавить отдельный `db-test` сервис; обсудить отдельно).

<read_first>
- tests/conftest.py — первые 60 строк, найти `_setup_database` фикстуру.
- docker-compose.yml — секция `api.environment` где задан DATABASE_URL.
- /root/.claude/projects/-root/memory/feedback_pytest_drop_schema_prod.md — для согласованности
  текста ошибки и логики.
</read_first>

<action>
1. В `tests/conftest.py` в самом начале `_setup_database` (до открытия любого asyncpg
   connection), добавить:
   ```python
   settings = get_settings()
   dsn = settings.database_url

   # Hard guard — не дать destructive setup пойти в прод.
   # См. .claude/projects/-root/memory/feedback_pytest_drop_schema_prod.md
   _ALLOWED_TEST_DSN_MARKERS = ("outreach_test", "_test@", "/test_", "localhost", "127.0.0.1")
   if not any(marker in dsn for marker in _ALLOWED_TEST_DSN_MARKERS):
       raise RuntimeError(
           f"REFUSING TO RUN DESTRUCTIVE TEST SETUP AGAINST {dsn!r}. "
           "DATABASE_URL must contain 'outreach_test' OR run from host with localhost DSN. "
           "Inside docker compose run/exec api, DATABASE_URL points at PROD — "
           "use a separate db-test service or override DATABASE_URL via -e flag."
       )
   ```
   Подбор markers'ов — обсуждаемо. Главное чтобы прод-DSN
   `outreach_user@db:5432/outreach_platform` НЕ матчился.

2. Smoke-тест guard'а: `docker compose run --rm api python -m pytest tests/test_ai_engine.py -x`
   → должен **упасть** с `RuntimeError: REFUSING TO RUN DESTRUCTIVE TEST SETUP AGAINST 'postgresql+asyncpg://outreach_user:...@db:5432/outreach_platform'`.
   Это правильное поведение — pytest должен запускаться либо локально с тест-DSN, либо через
   отдельный сервис.

3. Добавить в `CLAUDE.md` или `tests/README.md` строку: «pytest требует test-DSN (см. guard
   в conftest.py); запуск через `docker compose run api pytest` — заблокирован».
</action>

<acceptance_criteria>
- `grep -n "REFUSING TO RUN" tests/conftest.py` → ≥ 1 совпадение в первых ~80 строках.
- `docker compose run --rm -T -v "$(pwd)/tests:/app/tests" -v "$(pwd)/conftest.py:/app/conftest.py" api python -m pytest tests/test_ai_engine.py -x 2>&1 | grep -c "REFUSING TO RUN"` → ≥ 1.
- Прогон pytest с явно переопределённым DSN (`-e DATABASE_URL=...outreach_test`) — guard не срабатывает, тесты идут.
- В DB-логах постгреса (`docker logs outreach-platform-db --since 1m`) **нет** `DROP SCHEMA`
  / `CREATE SCHEMA public` после запуска заблокированного pytest.
- Дополнительно: после применения file_mtime таблиц (см. memory note) остаются стабильными
  при повторных pytest-попытках — DROP не случается.
</acceptance_criteria>

---

## Task 1 — Apply missing migrations 017–022 to prod (idempotent)

**Why:** После сегодняшнего DROP SCHEMA `init_db()::create_all` восстановило ORM-таблицы
БЕЗ server-side UUID defaults и БЕЗ таблицы `messages` (которой нет в ORM, только в
`017_phase5.sql`). Без этого фикса даже когда новые входящие пойдут — listener будет падать
в `NotNullViolation`, а `/api/v1/conversations` / `/api/v1/analytics` будут 500-ить на
`relation "messages" does not exist`. Это **последствие** Task 0/3-инцидента, не причина.

**Wave:** 2 (после Task 0 + Task 3 — сначала запрём дыру и поднимем бэкап, потом фиксим схему).
**Depends on:** Task 0 (backup running), Task 3 (guard merged).
**Files modified:** —
**Autonomous:** yes (все 5 миграций идемпотентные).

<read_first>
- migrations/017_phase5.sql — `CREATE TABLE IF NOT EXISTS messages (...)` + добавление
  `bot_ignored` в conversations.status CHECK, llm_calls, 3 индекса.
- migrations/019_schema_drift_fix.sql — закрывает накопленный drift (см. memory note
  `project_aimly_tg_outreach_migrations.md`). Чистый `ALTER … ADD CONSTRAINT IF NOT EXISTS`
  + `CREATE INDEX IF NOT EXISTS`. Не разрушительный.
- migrations/020_contacts_cache_unique.sql — UNIQUE на contacts_cache(sender_id, phone).
- migrations/021_uuid_defaults.sql — `ALTER … SET DEFAULT gen_random_uuid()` на 7+ таблицах.
  Это критический фикс корня — listener начнёт писать сразу после применения.
- migrations/022_conversations_status_default.sql — default на conversations.status.
</read_first>

<action>
1. Применить миграции по порядку в БД `outreach_platform`:

```bash
cd /root/apps/aimly/tg-outreach
for f in 017_phase5.sql 019_schema_drift_fix.sql 020_contacts_cache_unique.sql \
         021_uuid_defaults.sql 022_conversations_status_default.sql; do
  echo "=== Applying migrations/$f ==="
  docker cp "migrations/$f" outreach-platform-db:/tmp/$f
  docker exec outreach-platform-db psql -U outreach_user -d outreach_platform \
    -v ON_ERROR_STOP=1 -f /tmp/$f
done
```

2. После применения 017 — подтвердить что таблица `messages` существует.
3. После применения 021 — подтвердить что `column_default` появился на UUID PK.
4. Зафиксировать факт применения в .planning/STATE.md (одна строка под текущей датой).
</action>

<acceptance_criteria>
- `docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c "\dt messages"` → возвращает 1 строку (таблица существует).
- Запрос:
  ```sql
  SELECT column_name, column_default FROM information_schema.columns
   WHERE table_name IN ('conversations','contacts','senders','messages_log','messages',
                        'ai_contexts','warmup_pool','campaigns','message_queue')
     AND column_name = 'id';
  ```
  возвращает строки где `column_default = 'gen_random_uuid()'` для **всех** перечисленных
  таблиц (исключая campaign_senders — composite PK без id).
- Smoke-тест listener'а: получить тестовое входящее → `SELECT COUNT(*) FROM conversations`
  должен инкрементироваться (раньше падал в NotNullViolation).
- Никаких ошибок при повторном прогоне того же блока миграций (идемпотентность —
  обязательное условие).
- В `docker logs outreach-platform-api --tail 200` после рестарта **нет** более
  `ProgrammingError: relation "messages" does not exist`.
</acceptance_criteria>

---

## Task 2 — Race condition в `_resolve_or_create_workspace`

**Why:** `SELECT * FROM workspaces` сейчас содержит **4 строки** для одного supabase_user_id
Andrew, созданные за 5 мс (13:33:23.191 → .195). Lovable-фронт при загрузке шлёт 4-10
параллельных fetch'ей, каждый идёт через middleware → не находит UserWorkspace → создаёт
новый. JWT-запросы потом случайно попадают в один из 4 workspace'ов → UI «пуст» даже когда
данные есть.

**Wave:** 2 (после Task 1, до cleanup'а; параллельно с Task 1 нельзя — нужны UUID defaults).
**Depends on:** Task 0, Task 3, Task 1 (UUID defaults должны быть на месте, иначе INSERT в новой миграции упадёт).
**Files modified:**
  - `migrations/023_user_workspaces_unique.sql` (новая)
  - `app/utils/auth.py` (функция `_resolve_or_create_workspace` lines ~292-380)

<read_first>
- app/utils/auth.py — текущая реализация `_resolve_or_create_workspace` (lines 292-380),
  обрати внимание на двойной SELECT по UserWorkspace.supabase_user_id (lines 308 и 360) —
  оба в read-then-create flow без транзакционной защиты.
- app/database.py — модель `UserWorkspace` (для понимания existing columns/constraints).
- migrations/012_workspace.sql — для конвенций naming/PK.
</read_first>

<action>
1. **Cleanup перед constraint'ом** — оставить только canonical workspace для Andrew:
   ```sql
   -- workspace_id для сохранения (выбран как первый по created_at)
   -- bb96789d-ca84-4880-9568-90867aae6acd

   BEGIN;
   -- Список 4 workspace для Andrew с created_at:
   --   bb96789d-... (13:33:23.191) — оставляем
   --   остальные 3 — удалить (cascade FK уберёт user_workspaces)
   DELETE FROM workspaces
    WHERE id IN (SELECT id FROM workspaces
                 WHERE name LIKE '%andrew%'  -- адаптировать по фактическим именам
                   AND id <> 'bb96789d-ca84-4880-9568-90867aae6acd');
   COMMIT;
   ```
   ⚠ Перед DELETE — `SELECT id, name, created_at FROM workspaces ORDER BY created_at;` и
   обсудить с пользователем какой именно workspace оставить (3 из 4 пустые, но проверить).

2. Написать `migrations/023_user_workspaces_unique.sql`:
   ```sql
   BEGIN;
   ALTER TABLE user_workspaces
     ADD CONSTRAINT user_workspaces_supabase_user_id_key UNIQUE (supabase_user_id);
   COMMIT;
   ```
   Идемпотентно через `DO $$ BEGIN … EXCEPTION WHEN duplicate_object THEN NULL END $$`.

3. Применить миграцию: `docker cp migrations/023_user_workspaces_unique.sql ... && psql -f ...`.

4. Переписать `_resolve_or_create_workspace` в `app/utils/auth.py`:
   - текущий flow: SELECT → если None → INSERT workspace → INSERT user_workspace.
   - новый flow: `INSERT INTO user_workspaces (...) VALUES (...) ON CONFLICT (supabase_user_id) DO NOTHING RETURNING workspace_id` →
     если RETURNING пуст → SELECT workspace_id FROM user_workspaces WHERE supabase_user_id = … (вторая попытка взяла существующий).
   - workspace создаётся внутри той же транзакции: `INSERT INTO workspaces … RETURNING id`,
     затем `INSERT INTO user_workspaces … ON CONFLICT DO NOTHING`. Если ON CONFLICT сработал —
     откатить созданный workspace (DELETE), потому что user уже привязан к другому.
   - всё внутри `async with session.begin():` (одна транзакция).

5. Rebuild контейнера: `docker compose up -d --build api`.
</action>

<acceptance_criteria>
- `SELECT COUNT(*) FROM workspaces WHERE id != 'bb96789d-ca84-4880-9568-90867aae6acd' AND ...`
  → 0 для Andrew после cleanup.
- `\d user_workspaces` показывает `UNIQUE CONSTRAINT user_workspaces_supabase_user_id_key`.
- Стресс-тест: 10 параллельных `curl … /api/v1/folders` от свежего Supabase JWT (нового
  пользователя) → итог `SELECT COUNT(*) FROM workspaces WHERE name LIKE '%test%'` = 1, не 10.
- Логи api: на 9 из 10 параллельных запросов лог `[auth] resolved existing workspace=…`,
  на 1 — `[auth] created new workspace=…`. Никаких `IntegrityError` в stderr.
- `python -m pytest tests/test_auth.py -x` (если есть) — проходит.
</acceptance_criteria>

---

## Verification (после всех 4 tasks)

1. UI smoke:
   - Открыть `https://aimly.agsventurelab.com` в браузере под Andrew.
   - Network tab: GET /api/v1/folders → 200 + непустой массив (если онбординг был).
   - Network tab: GET /api/v1/conversations → 200 (не 500).
   - Network tab: GET /api/v1/analytics/... → 200 (не 500).
2. Listener smoke:
   - Отправить тестовое входящее на один из подключённых аккаунтов.
   - В `docker logs outreach-platform-listener --tail 50` — без NotNullViolation.
   - `SELECT COUNT(*) FROM conversations` инкрементировался.
3. Auth smoke:
   - `SELECT COUNT(DISTINCT supabase_user_id), COUNT(*) FROM user_workspaces` — числа равны
     (нет дублей).

## Must Haves (goal-backward)

- [ ] **М0:** Daily pg_dump tg-outreach в `/root/backups/tg-outreach/`, retention 14 дней.
- [ ] **М1:** Listener способен записать новое входящее в `conversations` (UUID default ОК).
- [ ] **М2:** Эндпоинты, ссылающиеся на таблицу `messages`, не возвращают 500.
- [ ] **М3:** Параллельные запросы от одного supabase_user_id создают ≤ 1 workspace (DB-constraint).
- [ ] **М4:** `docker compose run api pytest` падает с явным RuntimeError до DROP SCHEMA.
- [ ] **М5:** Все миграции 017-023 применены к проду, факт зафиксирован в STATE.md.

## Out of scope

- Восстановление исторических данных. Данные за 23–26 мая (senders, agents, conversations,
  messages, contacts) — потеряны безвозвратно, бэкапов не существовало. Восстанавливаются
  только через ручной онбординг senders/contacts заново.
- Перевод `init_db()` на applier миграций (010-023 идемпотентно применять при старте API —
  тема отдельной фазы; снимает риск future schema drift).
- Сборка отдельного `db-test` сервиса в docker-compose.yml — упомянуто в Task 3, но
  делать опционально.
- Переключение `log_statement = ddl` в postgresql.conf — для лучшей trail-визности destructive
  операций. Не обязательно сейчас, но желательно (отдельный фикс).
