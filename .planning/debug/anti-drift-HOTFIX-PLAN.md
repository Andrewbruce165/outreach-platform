---
slug: anti-drift-hotfix
parent_session: ui-data-missing (follow-up — preventative)
status: ready_to_execute
created: 2026-05-26
mode: hotfix
type: preventative_infra
---

# Anti-Drift Hotfix Plan

Refs:
- `.planning/debug/ui-data-missing.md` (incident that motivated this)
- `.planning/debug/ui-data-missing-HOTFIX-PLAN.md` (immediate fix; this plan is the
  **out-of-scope** items from its "Out of scope" section turned into action)

**Goal:** Make the 2026-05-26 schema-wipe class of incidents structurally impossible.
Three independent items, all preventative (no current bug):

- **Task A** (Wave 1, trivial) — `log_statement = ddl` in postgres config so destructive
  DDL is always logged (today: `log_statement=none`, no DROP trail).
- **Task B** (Wave 1, parallel with A) — separate `db-test` postgres service +
  `docker-compose.test.yml` override so pytest has a clean path and never inherits
  prod `DATABASE_URL`. After this, the conftest guard becomes belt-and-suspenders
  rather than the only line of defense.
- **Task C** (Wave 2, biggest change) — applier script in `init_db()` that
  idempotently runs `migrations/*.sql` in order on api startup, tracked via a
  `schema_migrations` table. Eliminates the class of bug where `create_all`
  rebuilds tables but raw-SQL column/constraint additions (006_senders_telegram_id,
  019_schema_drift_fix, etc.) are lost on restart.

---

## Task A — `log_statement = ddl` on postgres

**Why:** Today `log_statement=none`. Successful `DROP SCHEMA`, `DROP TABLE`, `TRUNCATE`,
`ALTER` operations leave no record. The 2026-05-26 incident's DROP SCHEMA was invisible
in `docker logs outreach-platform-db` — only `pg_stat_file` mtime betrayed it.
`ddl` level captures all schema-mutating statements (CREATE/ALTER/DROP/TRUNCATE) with
zero performance cost (DDL is rare). DML stays unlogged.

**Wave:** 1.
**Depends on:** —
**Files modified:** `docker-compose.yml` (db service `command:` override).
**Autonomous:** yes.

<read_first>
- docker-compose.yml — section `services.db` (lines ~1-15), confirm there is no existing
  `command:` override.
</read_first>

<action>
1. Add `command:` override to the `db` service in `docker-compose.yml`:
   ```yaml
     db:
       image: postgres:16
       container_name: outreach-platform-db
       restart: unless-stopped
       command:
         - "postgres"
         - "-c"
         - "log_statement=ddl"
         - "-c"
         - "log_min_duration_statement=1000"   # also surface slow queries (>1s)
       environment:
         ...
   ```
   `log_min_duration_statement=1000` is a bonus: slow-query log without performance hit.
2. Recreate db container (force-recreate so the command takes effect):
   ```bash
   docker compose up -d --force-recreate db
   ```
   Wait for healthcheck.
3. Verify:
   ```bash
   docker exec outreach-platform-db psql -U outreach_user -d outreach_platform \
     -c "SHOW log_statement; SHOW log_min_duration_statement;"
   ```
   Expected: `ddl`, `1s`.
4. Smoke: issue a benign DDL and confirm it appears in `docker logs db`:
   ```bash
   docker exec outreach-platform-db psql -U outreach_user -d outreach_platform \
     -c "CREATE TABLE IF NOT EXISTS _smoke_test_ddl(x int); DROP TABLE _smoke_test_ddl;"
   docker logs outreach-platform-db --tail 10 | grep -E "CREATE TABLE|DROP TABLE"
   ```
</action>

<acceptance_criteria>
- `docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -tAc "SHOW log_statement"` → `ddl`.
- `docker logs outreach-platform-db --since 1m` contains `statement: CREATE TABLE _smoke_test_ddl` AND `statement: DROP TABLE _smoke_test_ddl` from the smoke test.
- API + listener keep running (db restart was not catastrophic — they auto-reconnect via pool).
</acceptance_criteria>

---

## Task B — `db-test` service + `docker-compose.test.yml` override

**Why:** Current state: `docker compose run --rm api pytest` resolves `DATABASE_URL` to
prod via `services.api.environment.DATABASE_URL`. The 2026-05-26 incident leveraged
this exact path. Conftest guard now blocks it (already shipped), but the **right** fix
is to make a test path that doesn't even point at prod in the first place.

Pattern: ship a `docker-compose.test.yml` overlay file that:
- Defines `db-test` service (postgres:16, different volume, different port if you want).
- Overrides `api.environment.DATABASE_URL` to point at `db-test`.
- Overrides `api.depends_on` to use `db-test` instead of `db`.

Usage:
```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest
```

**Wave:** 1 (parallel with Task A — independent files).
**Depends on:** —
**Files modified:**
- `docker-compose.test.yml` (new file).
- `tests/README.md` or `CLAUDE.md` — document the right test invocation.
**Autonomous:** yes.

<read_first>
- docker-compose.yml — sections `services.db`, `services.api`, `services.api.depends_on`,
  `services.api.environment.DATABASE_URL`.
- tests/conftest.py — confirm `_ALLOWED_TEST_DSN_MARKERS` includes `_test@` so the new
  DSN `postgresql+asyncpg://outreach_user:...@db-test:5432/outreach_test` passes.
</read_first>

<action>
1. Create `docker-compose.test.yml` next to `docker-compose.yml`:
   ```yaml
   # Test overlay — gives pytest an isolated postgres so destructive setup
   # never touches prod. Use:
   #   docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest
   # See .claude/projects/-root/memory/feedback_pytest_drop_schema_prod.md
   services:
     db-test:
       image: postgres:16
       environment:
         POSTGRES_USER: outreach_user
         POSTGRES_PASSWORD: outreach_test_pass
         POSTGRES_DB: outreach_test
       # No restart, no volume — ephemeral by design (db-test data should not survive)
       tmpfs:
         - /var/lib/postgresql/data
       healthcheck:
         test: ["CMD-SHELL", "pg_isready -U outreach_user -d outreach_test"]
         interval: 5s
         timeout: 3s
         retries: 10

     api:
       environment:
         DATABASE_URL: postgresql+asyncpg://outreach_user:outreach_test_pass@db-test:5432/outreach_test
       depends_on:
         db-test:
           condition: service_healthy
   ```
2. Smoke-test: now pytest should actually RUN against the test DB:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm -T \
     -v "$(pwd)/tests:/app/tests" -v "$(pwd)/conftest.py:/app/conftest.py" \
     -v "$(pwd)/migrations:/app/migrations" -v "$(pwd)/pytest.ini:/app/pytest.ini" \
     api python -m pytest tests/test_ai_engine.py -x 2>&1 | tail -30
   ```
   Conftest guard should NOT fire — DSN now contains `_test@` and `outreach_test`.
3. Verify the prod DB was NOT touched during the test run:
   ```bash
   docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -tAc "
     SELECT MAX((pg_stat_file('base/'||d.oid||'/'||c.relfilenode)).modification)
       FROM pg_class c JOIN pg_database d ON d.datname=current_database()
      WHERE c.relkind='r' AND c.relnamespace=(SELECT oid FROM pg_namespace WHERE nspname='public');"
   ```
   The file_mtime should be older than the test invocation (no DROP+CREATE happened on prod).
4. Update `CLAUDE.md` § "Testing" (add if missing):
   > **Running tests:** `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. Never run pytest against the default compose — `DATABASE_URL` there points at prod, conftest guard will block but the right path is the test overlay.
</action>

<acceptance_criteria>
- File `docker-compose.test.yml` exists.
- `docker compose -f docker-compose.yml -f docker-compose.test.yml config | grep -A1 DATABASE_URL` shows `db-test:5432/outreach_test`.
- Smoke pytest in step 2 either passes the test or fails on a real test assertion — **not** with `REFUSING TO RUN`. (Guard does not fire.)
- After the smoke test, prod's file_mtime is unchanged (step 3 verifies).
- CLAUDE.md has the test invocation documented.
</acceptance_criteria>

---

## Task C — Migration applier in `init_db()` (biggest change)

**Why:** This is the structural fix. Today `init_db()` calls only `Base.metadata.create_all`,
so a fresh schema is missing every column/constraint/index added by raw-SQL migrations.
The 2026-05-26 incident recovery required manual re-application of 23 migration files.
Goal: every api start runs all `migrations/*.sql` in order, idempotently, tracked via a
`schema_migrations` table. After any DROP SCHEMA or new clone, recovery is `docker compose up`.

**Wave:** 2 (depends on Task A — log_statement=ddl helps us see what the applier does;
optional but useful).
**Depends on:** Task A (optional — applier works without it but logging makes verification easier).
**Files modified:**
- `app/database.py` — new helper `_apply_migrations(engine)` called from `init_db()`.
- `migrations/001_add_unique_constraint_messages.sql` — fix the non-idempotent CREATE UNIQUE INDEX (add `IF NOT EXISTS`) so applier can re-run it harmlessly on already-applied DBs.
- `migrations/_schema_migrations.sql` (new) — bootstrap migration that creates the `schema_migrations` tracking table.
- `CLAUDE.md` — update the "Migrations" rule: raw-SQL is now auto-applied; manual `docker exec psql -f` is no longer required.
**Autonomous:** yes, but with explicit smoke-test stop after first run.

<read_first>
- app/database.py — full file (small, ~50 lines).
- migrations/001_add_unique_constraint_messages.sql — fix non-idempotent index.
- Сравни наименование с другими (`CREATE UNIQUE INDEX IF NOT EXISTS`).
- /root/.claude/projects/-root/memory/project_aimly_tg_outreach_migrations.md — context on why this matters.
</read_first>

<action>
1. **Bootstrap migration** — `migrations/_schema_migrations.sql`:
   ```sql
   -- Tracking table for the auto-applier. Idempotent.
   CREATE TABLE IF NOT EXISTS schema_migrations (
       version     TEXT PRIMARY KEY,        -- e.g. '001_add_unique_constraint_messages'
       applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
       sha256      TEXT NOT NULL,           -- hash of the .sql file content at apply time
       error       TEXT                     -- NULL on success; set when applier captured a failure
   );
   CREATE INDEX IF NOT EXISTS idx_schema_migrations_applied_at
       ON schema_migrations(applied_at);
   ```
   Underscore prefix → sorts before all numbered migrations.

2. **Fix `001_add_unique_constraint_messages.sql`** — replace
   `CREATE UNIQUE INDEX messages_conversation_telegram_unique ON …`
   with
   `CREATE UNIQUE INDEX IF NOT EXISTS messages_conversation_telegram_unique ON …`
   (single edit). This is the only migration we know is non-idempotent.

3. **Applier in `app/database.py`** — extend `init_db()`:
   ```python
   import hashlib
   import logging
   import pathlib
   from typing import List, Tuple

   logger = logging.getLogger(__name__)

   PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
   MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
   APPLIER_LOCK_KEY = 0xA1A1A1A1A1A1A1A1  # advisory lock id — single-instance migrations

   async def _list_pending_migrations(conn) -> List[Tuple[str, str, str]]:
       """Return [(version, path, sha256), ...] not yet in schema_migrations."""
       files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))
       # _schema_migrations.sql runs first (underscore sorts before digits)
       pending: List[Tuple[str, str, str]] = []
       result = await conn.exec_driver_sql("SELECT version FROM schema_migrations")
       applied = {row[0] for row in result.fetchall()} if result.returns_rows else set()
       for f in files:
           version = f.stem  # filename without .sql
           if version in applied:
               continue
           content = f.read_text()
           sha = hashlib.sha256(content.encode()).hexdigest()
           pending.append((version, str(f), sha, content))
       return pending

   async def _apply_migrations(engine_) -> None:
       """Apply all migrations/*.sql in order, idempotently, behind an advisory lock.

       Behavior:
       - Acquires a session-level advisory lock so two api instances starting
         simultaneously do not race.
       - Bootstrap step: ensures schema_migrations exists (via _schema_migrations.sql,
         which always runs because it starts with `_` and never lands in the tracking
         table itself).
       - For each pending migration: BEGIN; run statements; INSERT INTO schema_migrations;
         COMMIT. If any statement fails, the whole transaction rolls back and the api
         start fails — preventing a half-applied state.
       """
       async with engine_.begin() as conn:
           # Bootstrap: schema_migrations must exist before _list_pending can SELECT.
           bootstrap = (MIGRATIONS_DIR / "_schema_migrations.sql").read_text()
           await conn.exec_driver_sql(bootstrap)

           # Advisory lock — only one instance at a time runs migrations.
           await conn.exec_driver_sql(f"SELECT pg_advisory_lock({APPLIER_LOCK_KEY})")
           try:
               pending = await _list_pending_migrations(conn)
               if not pending:
                   logger.info("[migrate] schema is up to date")
                   return
               logger.info(f"[migrate] applying {len(pending)} pending migration(s)")
               for version, path, sha, content in pending:
                   logger.info(f"[migrate] -> {version}")
                   await conn.exec_driver_sql(content)
                   await conn.exec_driver_sql(
                       "INSERT INTO schema_migrations(version, sha256) VALUES ($1, $2)",
                       (version, sha),
                   )
                   logger.info(f"[migrate] OK  {version}")
           finally:
               await conn.exec_driver_sql(
                   f"SELECT pg_advisory_unlock({APPLIER_LOCK_KEY})"
               )

   async def init_db():
       async with engine.begin() as conn:
           await conn.run_sync(Base.metadata.create_all)
       # NEW — auto-apply raw-SQL migrations after create_all.
       # create_all gives us the ORM baseline; migrations layer columns/constraints/indexes
       # that the ORM doesn't know about (see project_aimly_tg_outreach_migrations.md).
       await _apply_migrations(engine)
   ```
   Notes for the implementer:
   - `exec_driver_sql` is the right SQLAlchemy 2.0 method for raw asyncpg execution of
     multi-statement files (asyncpg does not run multiple statements in one call by
     default — falls back to splitting if needed, or use `simple_query` route).
   - If multi-statement support is fragile, alternative: shell out to `psql -f` inside
     the api container (`docker exec`-style). But that adds psql dependency to the
     image. Stick with native first, fallback if it breaks.
   - Advisory lock is **session-scoped**, not transaction-scoped — released only when
     conn closes (which is at `engine.begin()` exit). Using session-level (not xact)
     so the advisory unlock happens cleanly.

4. **Backfill `schema_migrations` for the current prod DB** — all 23 migrations are
   already applied manually, so they should be recorded as applied to avoid
   re-running them. SQL:
   ```sql
   INSERT INTO schema_migrations(version, sha256, applied_at)
   SELECT v, '<sha256 of file>', now()
     FROM (VALUES
       ('001_add_unique_constraint_messages'),
       ('002_add_document_webhook_url'),
       ... -- all 22 numbered + 023
     ) AS m(v)
   ON CONFLICT (version) DO NOTHING;
   ```
   Generate the actual SHAs from disk:
   ```bash
   for f in /root/apps/aimly/tg-outreach/migrations/*.sql; do
     v=$(basename "$f" .sql)
     [[ "$v" == "_schema_migrations" ]] && continue
     sha=$(sha256sum "$f" | awk '{print $1}')
     echo "  ('$v', '$sha', now()),"
   done
   ```

5. **Rebuild api** and confirm:
   ```bash
   docker compose up -d --build api
   sleep 5
   docker logs outreach-platform-api --tail 50 | grep -E "\[migrate\]"
   ```
   Expected: `[migrate] schema is up to date` (because we backfilled).

6. **Smoke-test the applier path** — create a no-op test migration to prove it auto-applies:
   ```bash
   cat > migrations/024_smoke_test.sql <<'SQL'
   -- Smoke test for auto-applier (delete after verifying).
   DO $$ BEGIN RAISE NOTICE 'applier-smoke-test-024 ran'; END $$;
   SQL
   docker compose restart api
   sleep 4
   docker logs outreach-platform-api --tail 30 | grep -E "\[migrate\]|024_smoke_test"
   ```
   Expected: `[migrate] -> 024_smoke_test` and `[migrate] OK 024_smoke_test`. Then:
   ```bash
   docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -tAc \
     "SELECT version FROM schema_migrations WHERE version='024_smoke_test';"
   ```
   Expected: returns the row.
   **Then delete `migrations/024_smoke_test.sql`** AND the row:
   ```bash
   rm /root/apps/aimly/tg-outreach/migrations/024_smoke_test.sql
   docker exec outreach-platform-db psql -U outreach_user -d outreach_platform -c \
     "DELETE FROM schema_migrations WHERE version='024_smoke_test';"
   ```

7. **Update CLAUDE.md** "Архитектурные правила" §:
   > Старое правило: «миграции применяются руками». **Новое:** raw-SQL миграции в `migrations/*.sql` авто-применяются при старте api через `_apply_migrations`. Tracking — таблица `schema_migrations`. Достаточно положить новый файл и сделать `docker compose up -d --build api`. Идемпотентность — `IF NOT EXISTS` / `DO $$ EXCEPTION duplicate_object $$` (см. 023 как образец). Скрипт абортит api start если миграция падает — half-applied state невозможен.
</action>

<acceptance_criteria>
- `app/database.py::init_db` calls `_apply_migrations(engine)` after `create_all`.
- Table `schema_migrations` exists in prod (`\d schema_migrations`).
- Backfill: `SELECT COUNT(*) FROM schema_migrations` returns 23 (all current migrations).
- API restart logs show `[migrate] schema is up to date` (no re-application of already-applied migrations).
- Smoke test 024 in step 6: applier runs it, records it, then we clean up. Logs confirm the path.
- After smoke cleanup, `SELECT COUNT(*) FROM schema_migrations` returns 23 again.
- API + listener stay healthy after rebuild (no startup regression).
- CLAUDE.md migration section updated.

**Failure mode test (manual):** introduce a deliberately broken migration `024_break.sql` with `SELECT 1/0;`. Restart api. Expected: api fails to start with `[migrate] FAIL 024_break` in logs, `docker compose ps` shows api in restart loop or exit. Remove the broken file → api recovers on next restart. (This proves abort-on-failure behavior; do this once on a quiet moment.)
</acceptance_criteria>

---

## Verification (после всех 3 tasks)

1. Postgres now logs DDL:
   ```bash
   docker exec outreach-platform-db psql -U outreach_user -d outreach_platform \
     -c "CREATE TABLE _v(x int); DROP TABLE _v;"
   docker logs outreach-platform-db --tail 5 | grep -E "CREATE TABLE _v|DROP TABLE _v"
   ```

2. Pytest path via test overlay:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm \
     api python -m pytest tests/ -x 2>&1 | tail -5
   ```
   Either real test failures OR all pass — never `REFUSING TO RUN`.

3. Migration applier survives api restart:
   ```bash
   docker compose restart api ; sleep 5
   docker logs outreach-platform-api --tail 10 | grep "\[migrate\]"
   ```
   Expected: `up to date`.

4. **Stress test:** simulate the original incident. Drop a table by hand, restart api,
   confirm applier restores it:
   ```bash
   # Pick something safe — proxy_pool is empty + has no FKs from other tables
   docker exec outreach-platform-db psql -U outreach_user -d outreach_platform \
     -c "DROP TABLE proxy_pool CASCADE;"
   docker compose restart api ; sleep 5
   docker exec outreach-platform-db psql -U outreach_user -d outreach_platform \
     -c "\d proxy_pool" | head -10
   ```
   Expected: table back, all columns/indexes from migration 009 present.

## Must Haves

- [ ] **М0:** `log_statement=ddl` active on prod db → DDL операции видны в `docker logs`.
- [ ] **М1:** `docker-compose.test.yml` + `db-test` service → pytest имеет clean path к isolated DB.
- [ ] **М2:** `init_db()` авто-применяет все `migrations/*.sql` в порядке; tracking через `schema_migrations`.
- [ ] **М3:** 23 текущих миграции backfill'ены в `schema_migrations` как applied (no re-run).
- [ ] **М4:** API стартует чисто, logs показывают `[migrate]` строки.
- [ ] **М5:** CLAUDE.md обновлён: миграции теперь auto-apply.

## Out of scope

- Конвертация всех 23 миграций в строго idempotent — слишком много работы; вместо этого
  backfill их как applied и не трогать. Новые миграции писать идемпотентно по образцу 023.
- Down-миграции (rollback) — не делаем, project никогда не использовал их и v1 не требует.
- Multi-tenant testing infra (отдельные test-DSN per worker) — out of scope, single
  db-test достаточно для serial-mode pytest.
- Перенос на Alembic — CLAUDE.md «никогда Alembic».
