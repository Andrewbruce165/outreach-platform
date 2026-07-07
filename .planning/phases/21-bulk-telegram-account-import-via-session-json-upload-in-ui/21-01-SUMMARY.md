---
phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui
plan: 01
subsystem: database
tags: [postgres, sqlalchemy, migration, telethon, pytest, tdd, account-import]

# Dependency graph
requires:
  - phase: 20-account-profile-management
    provides: "migration/ORM server_default pattern (mig 049 profile_field_changed_at) + Sender profile columns to append after"
provides:
  - "migrations/051_account_import.sql — 2 nullable senders columns (client_fingerprint JSONB, twofa_password_enc TEXT) + 3 idempotent import tables (account_import_stagings/jobs/items)"
  - "ORM mirrors in app/models: Sender +2 cols, AccountImportStaging/AccountImportJob/AccountImportItem with server_default on every NOT NULL col + dual uuid/gen_random_uuid ids"
  - "Wave-0 RED test scaffold: tests/test_account_import.py (6 tests, IMPT-01/03/04/05/06/07) + tests/test_account_import_worker.py (IMPT-02), collecting-clean, RED on missing symbols"
  - "conftest fixtures: build_vendor_sqlite_session (synthetic offline session) + stub_import_telethon (no-network connect/get_me stub)"
affects: [21-02-fingerprint-seam-and-2fa-autofill, 21-03-preview-unzip-pair-stage, 21-04-per-account-import-routine, 21-05-async-job-confirm-worker-status, 21-06-frontend-and-handoff]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Nullable-new-column additive migration (NULL = today's behaviour, no server_default needed on nullable cols)"
    - "Wave-0 RED scaffold with deferred in-body imports (collect-clean, fail-on-missing-symbol) — sets downstream contracts"
    - "Synthetic Telethon SQLiteSession fixture (fake dc + 256-byte auth_key) so offline SQLite→StringSession conversion runs with no network and never touches the live sample"

key-files:
  created:
    - migrations/051_account_import.sql
    - tests/test_account_import.py
    - tests/test_account_import_worker.py
  modified:
    - app/models/__init__.py
    - tests/conftest.py

key-decisions:
  - "Both new senders columns NULLABLE (client_fingerprint, twofa_password_enc): NULL = today's exact behaviour (global _CLIENT_FINGERPRINT fallback / no stored 2FA) so no regression to the 13 existing senders and no server_default required on the migration"
  - "All 3 import-table id columns carry BOTH default=uuid.uuid4 AND server_default=text('gen_random_uuid()') because create_all (test/fresh DB) builds the schema from the ORM and wins over the migration DEFAULT — every NOT NULL col carries a matching server_default to avoid NotNullViolation on raw INSERT (KbDocument precedent, memory project-orm-default-vs-server-default-drift)"
  - "account_import_items carries its own session_blob (BYTEA) + vendor_json so the worker never re-unzips; worker NULLs session_blob on terminal status"
  - "RED scaffold sets downstream symbol contracts: sqlite_to_string_session(bytes)->str, unpack_and_pair(zip_bytes)->{matched,unpaired,malformed}, encrypt_twofa(str)->str, import_one_account(db, item)->result_str, AccountImportWorker._tick(); worker must call import_one_account as a module attribute for monkeypatch to bite"

patterns-established:
  - "Preview/staging TTL blob table mirrors CsvImport (BYTEA + expires_at)"
  - "Async import worker modeled on KnowledgeIngestWorker (claim pending → processing → ok/failed, job.processed/total, status done)"

requirements-completed: [IMPT-08]

# Metrics
duration: 11min
completed: 2026-07-07
---

# Phase 21 Plan 01: Schema Foundation and Test Scaffold Summary

**Idempotent migration 051 + ORM mirrors add per-account fingerprint (JSONB) & Fernet-2FA (TEXT) columns to `senders` plus 3 account-import tables, backed by a 7-test Wave-0 RED scaffold with a no-network Telethon stub and synthetic-session fixture.**

## Performance

- **Duration:** 11 min
- **Started:** 2026-07-07T07:01:04Z
- **Completed:** 2026-07-07T07:12:59Z
- **Tasks:** 2
- **Files modified:** 5 (3 created, 2 modified)

## Accomplishments

- `migrations/051_account_import.sql`: 2 nullable `senders` columns + `account_import_stagings`/`account_import_jobs`/`account_import_items` — all `ADD COLUMN/CREATE TABLE/CREATE INDEX ... IF NOT EXISTS` (re-applies once, records in `schema_migrations`, fail-fast on drift).
- ORM mirror in `app/models/__init__.py`: `Sender.client_fingerprint`/`twofa_password_enc` (nullable) + three model classes with `server_default` on every NOT NULL column and dual `uuid.uuid4`/`gen_random_uuid()` ids so `create_all` (test/fresh DB) matches prod exactly — verified via a runtime DB test (no NotNullViolation).
- Wave-0 RED scaffold: 6 tests in `tests/test_account_import.py` (IMPT-01/03/04/05/06/07) + `test_worker_drives_items_and_status` (IMPT-02) — all collect clean and are RED on the not-yet-existing symbols.
- Reusable no-network fixtures in `conftest.py`: `build_vendor_sqlite_session` (synthetic fake-dc + 256-byte auth_key session, never the live sample) and `stub_import_telethon` (stubbed connect/get_me/is_user_authorized + valid empty-auth-key StringSession).

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 051 + ORM mirrors (2 senders columns + 3 import tables)** — `774933c` (feat)
2. **Task 2: Wave-0 RED test scaffold (2 files + stubbed-Telethon fixture)** — `be31ddf` (test)

**Plan metadata:** _(this commit)_ (docs: complete plan)

## Files Created/Modified

- `migrations/051_account_import.sql` — idempotent: 2 nullable `senders` cols + 3 import tables + 4 indexes.
- `app/models/__init__.py` — `Sender` +2 nullable cols; `AccountImportStaging`/`AccountImportJob`/`AccountImportItem` ORM classes.
- `tests/test_account_import.py` — 6 RED tests (offline SQLite→StringSession, fingerprint override/strict fallback, ZIP pairing, 2FA-at-rest, dedup+proxy, partial-success+start-state).
- `tests/test_account_import_worker.py` — IMPT-02 worker-drive + job status RED test.
- `tests/conftest.py` — `import pytest`; `build_vendor_sqlite_session` + `stub_import_telethon` fixtures.

## Decisions Made

- Nullable new columns (NULL = current behaviour) → zero-regression additive migration; no `server_default` needed on the migration for nullable cols, but the ORM mirrors them so `create_all` builds an identical fresh/test schema.
- Every NOT NULL import-table column carries a `server_default`; every id uses BOTH `default=uuid.uuid4` and `server_default=text("gen_random_uuid()")` (KbDocument precedent) — the RED worker test inserts items via raw SQL omitting defaulted columns and must not hit NotNullViolation.
- RED scaffold intentionally fixes downstream symbol contracts (module `app.services.account_import` with `sqlite_to_string_session`/`unpack_and_pair`/`encrypt_twofa`/`import_one_account`, and `app.services.account_import_worker.AccountImportWorker`). `import_one_account` must be called as a module attribute by the worker so the monkeypatch in the worker test bites (kb_ingest precedent). Downstream tasks may adjust a single import line if they pick a different symbol name.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None. `--collect-only` on the plan's Task-1 verify command initially reported "file not found" because that command references `tests/test_account_import.py` (created by Task 2, not Task 1) — the real Task-1 intent (ORM/migration schema builds without error) was verified instead via a full-suite collection (1046→1053 tests, 0 errors) plus a runtime DB test (`test_workspace_router.py`, 8 passed) that exercises `create_all` + migration apply with the new tables.

## Authentication Gates

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Schema + RED scaffold ready. **21-02** (fingerprint seam + 2FA autofill) turns `test_fingerprint_override_and_strict_fallback` green by adding the `fingerprint=` param to `make_telegram_client` (currently RED via `TypeError` on the unexpected kwarg).
- Migration 051 is NOT yet applied to prod (auto-applies on next `docker compose up -d --build api`); test DB gets the tables from ORM `create_all`.
- Note: migration 051 is intentionally NOT added to the `conftest._build_outreach_schema` hardcoded migration list — the 3 tables + 2 senders cols come from ORM `create_all` (with `server_default`), so the test DB needs no SQL-only bits from 051 (the 4 indexes are perf-only, not correctness).

---
*Phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui*
*Completed: 2026-07-07*

## Self-Check: PASSED

- All 4 created files exist on disk; both modified files contain their new symbols.
- Task commits `774933c` (feat) and `be31ddf` (test) present in git log.
