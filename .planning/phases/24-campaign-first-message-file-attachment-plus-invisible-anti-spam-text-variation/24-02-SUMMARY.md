---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 02
subsystem: database
tags: [postgres, sqlalchemy, migration, bytea, pydantic, orm-drift]

# Dependency graph
requires:
  - phase: 04-campaigns
    provides: campaigns table + CampaignCreate/Update/Response schemas
  - phase: 02-tg-accounts-contacts
    provides: CsvImport DB-blob precedent (file_data BYTEA)
provides:
  - campaign_attachments 1-1 BYTEA-blob table (UNIQUE campaign_id, ON DELETE CASCADE)
  - campaigns.variation_enabled boolean NOT NULL DEFAULT true
  - CampaignAttachment ORM model (drift-guarded: id + size_bytes both default & server_default)
  - variation_enabled on CampaignCreate/Update/Response + computed has_attachment on Response
  - conftest migration-054 exists-guard for test/prod schema parity
affects: [24-03-send-file-blob-source-automedia, 24-04-attachment-endpoint-and-duplicate, 24-05-enqueue-file-opener-and-rerender, 24-06-worker-variation-and-blob-delivery]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "1-1 BYTEA blob in a side table (mirror of CsvImport) — blob kept OUT of every SELECT campaigns, no ORM relationship (Pitfall 7)"
    - "ORM-drift guard: every new NOT NULL column sets BOTH server_default AND an ORM default so raw INSERTs never NotNullViolation under create_all"

key-files:
  created:
    - migrations/054_campaign_attachment_and_variation.sql
    - tests/test_campaign_attachment.py
  modified:
    - app/models/__init__.py
    - app/schemas/__init__.py
    - tests/conftest.py

key-decisions:
  - "No Campaign.relationship to CampaignAttachment — worker/endpoint query by campaign_id directly, keeping the 50 MB blob off every campaign SELECT (Pitfall 7)"
  - "has_attachment is a computed Response field (router EXISTS), NOT a campaigns column"
  - "variation_enabled DEFAULT true retro-enables ALL existing campaigns via the DB default, no backfill"

patterns-established:
  - "server_default + ORM default on id (gen_random_uuid) and size_bytes (0) so create_all builds the same schema the migration does"
  - "conftest applies SQL-only migration bits (SET DEFAULT/UNIQUE/index) exists-guarded for test↔prod parity"

requirements-completed: [D-01, D-02, D-04, D-13]

# Metrics
duration: ~13min
completed: 2026-07-07
---

# Phase 24 Plan 02: Data Model, Migration, Schemas Summary

**campaign_attachments 1-1 BYTEA-blob table (UNIQUE campaign_id, CASCADE) + campaigns.variation_enabled NOT NULL DEFAULT true, mirrored in the ORM with the mandatory server_default drift guard, exposed on the campaign schemas, and covered by RED-first drift/idempotency tests.**

## Performance

- **Duration:** ~13 min
- **Started:** 2026-07-07T15:12:00Z
- **Completed:** 2026-07-07T15:26:00Z
- **Tasks:** 2
- **Files modified:** 5 (2 created, 3 modified)

## Accomplishments
- Migration 054: idempotent `campaign_attachments` table (BYTEA blob, UNIQUE campaign_id + ON DELETE CASCADE, workspace index) + `campaigns.variation_enabled` boolean NOT NULL DEFAULT true with a `SET DEFAULT` drift guard.
- `CampaignAttachment` ORM model mirroring `CsvImport`, with id (`default=uuid.uuid4` + `server_default=gen_random_uuid()`) and size_bytes (`default=0` + `server_default="0"`) — a raw INSERT omitting those columns never NotNullViolations under create_all.
- `variation_enabled` added to `CampaignCreate`/`CampaignUpdate`/`CampaignResponse`; `has_attachment` computed field added to `CampaignResponse`.
- conftest exists-guarded apply of migration 054 so the test DB carries the SQL-only bits (SET DEFAULT / UNIQUE / index) that create_all does not build.
- 4 GREEN tests: variation defaults true, raw INSERT omitting defaults OK, campaign_id UNIQUE enforced, migration 054 idempotent (applies twice cleanly).

## Task Commits

1. **Task 1: Migration 054 + ORM mirror (CampaignAttachment + variation_enabled)** - `b2c3998` (feat)
2. **Task 2: Pydantic schema fields + conftest 054 guard + RED drift tests** - `25055ec` (test)

## Files Created/Modified
- `migrations/054_campaign_attachment_and_variation.sql` - idempotent attachment table + variation flag
- `app/models/__init__.py` - CampaignAttachment class + Campaign.variation_enabled column
- `app/schemas/__init__.py` - variation_enabled on Create/Update/Response + has_attachment on Response
- `tests/conftest.py` - exists-guarded apply of migration 054
- `tests/test_campaign_attachment.py` - drift + idempotency tests (4 tests, GREEN)

## Decisions Made
- Added a 4th test (`test_attachment_campaign_id_unique`) beyond the 3 the plan named, to directly assert the D-01 UNIQUE(campaign_id) invariant (a one-attachment-per-campaign guarantee). Additive, no scope change.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Test-run environment isolation in a parallel worktree**
- **Found during:** Task 1/Task 2 verification
- **Issue:** The base `docker-compose.yml` `db` service has a fixed `container_name: outreach-platform-db` that collides with the running prod container when compose runs from the worktree's own project; and the worktree has no `.env`, so compose injects empty `TELEGRAM_API_ID=` which defeats conftest's `os.environ.setdefault`, crashing `Settings()`.
- **Fix:** Ran the ephemeral `db-test` in an isolated compose project (`-p ae2f77_test`) and executed the api one-off with `--no-deps` (so the prod `db` is never created) plus explicit `-e TELEGRAM_API_ID=1 ...` matching conftest's test defaults. Prod db/api/listener confirmed untouched; ephemeral project torn down with `down -v` (its own empty volume only).
- **Files modified:** none (test-invocation only)
- **Verification:** `pytest tests/test_campaign_attachment.py` → 4 passed; prod containers still running afterward.
- **Committed in:** n/a (no code change)

**2. [Rule 1 - Bug] Idempotency test split broke on ';' inside a SQL comment**
- **Found during:** Task 2
- **Issue:** The first `test_migration_054_idempotent` split the migration on `;`, but a `--` comment line ("...running; no backfill...") contains a semicolon, so the split produced a fragment that Postgres tried to execute as SQL → `syntax error at or near "no"`.
- **Fix:** Strip `--` comment lines FIRST, then split the remaining DDL on `;`.
- **Files modified:** tests/test_campaign_attachment.py
- **Verification:** All 4 tests GREEN.
- **Committed in:** `25055ec` (Task 2 commit)

---

**Total deviations:** 2 (1 blocking test-env, 1 test bug). Both confined to test execution/authoring — production code and migration unaffected.
**Impact on plan:** No scope creep. Migration/ORM/schema deliverables match the plan interfaces exactly.

## Issues Encountered
- **Stale worktree base:** this parallel-executor worktree branched from `92bd54b` (Phase-19-era) which predates migrations 046–053 and the phase 24 planning dir. Execution was faithful to the plan (migration numbered `054` as specified, so it merges cleanly onto main where 046–053 exist). The two code commits (`b2c3998`, `25055ec`) are self-contained and cherry-pickable onto main; the orchestrator reconciles STATE.md/ROADMAP.md/SUMMARY placement after the wave merges.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Schema foundation for both features is in place: 24-03 (send-file blob source), 24-04 (attachment endpoint + duplicate), 24-05 (enqueue file opener + rerender), and 24-06 (worker variation + blob delivery) can now query `campaign_attachments` by `campaign_id` and read `campaigns.variation_enabled`.
- Migration 054 will apply on the next prod `docker compose up -d --build api` (auto-applier).

## Self-Check: PASSED

---
*Phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation*
*Completed: 2026-07-07*
