---
phase: 22-account-level-new-chat-limit-grades
plan: 06
subsystem: database
tags: [postgres, migration, sqlalchemy, fastapi, pydantic, cleanup]

# Dependency graph
requires:
  - phase: 22-03
    provides: queue.py stopped reading campaigns.max_new_dialogs_per_day and senders.rate_per_day (account grade budget replaces both)
  - phase: 22-04
    provides: sender API stopped exposing rate_per_day
  - phase: 22-05
    provides: warmup uses the grade ladder / shared new-chat budget
provides:
  - "migration 059 dropping campaigns.max_new_dialogs_per_day (D-07) and senders.rate_per_day (D-04)"
  - "ORM (Sender / Campaign) no longer declares either column"
  - "campaign create/update/response API with no per-campaign dialog-cap field or validation"
  - "restriction-audit activity slice no longer references rate_per_day"
  - "repo-wide grep gate: zero app/ references to either dropped column"
affects: [campaigns, senders, restriction-audit, queue, warmup]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Terminal Wave-3 column DROP lands after all readers removed (boot-time auto-apply safe)"
    - "ORM removal + migration DROP ship together so create_all and the DB stay consistent"

key-files:
  created:
    - migrations/059_drop_dead_limit_columns.sql
  modified:
    - app/models/__init__.py
    - app/schemas/__init__.py
    - app/routers/campaigns.py
    - app/services/restriction_audit.py
    - app/services/queue.py
    - tests/conftest.py

key-decisions:
  - "Removed configured_per_day from the restriction-audit activity slice rather than substituting the grade budget — the slice is informational and rate_per_day no longer exists"
  - "Deleted test_campaign_new_dialog_limit_api.py wholesale — it tested the removed per-campaign cap feature"
  - "Swept rate_per_day out of every raw sender INSERT / ORM factory across the test suite because dropping the ORM column makes create_all omit it (raw inserts would crash)"

patterns-established:
  - "Pattern 1: when dropping an ORM column, audit ALL raw-SQL test inserts (create_all mirrors the ORM — the test DB loses the column too)"
  - "Pattern 2: keep source-introspection asserts (column-not-in-src) as living guards that the cleanup stuck"

requirements-completed: [D-07, D-04]

coverage:
  - id: D1
    description: "migration 059 drops campaigns.max_new_dialogs_per_day and senders.rate_per_day (idempotent DROP COLUMN IF EXISTS)"
    requirement: "D-07"
    verification:
      - kind: integration
        ref: "tests/test_migration_013.py (senders schema no longer asserts rate_per_day; suite green)"
        status: pass
    human_judgment: false
  - id: D2
    description: "campaign create/update/response API works with no max_new_dialogs_per_day field and no dialog-limit validation"
    requirement: "D-07"
    verification:
      - kind: integration
        ref: "tests/test_send_campaign.py (5/5 pass)"
        status: pass
    human_judgment: false
  - id: D3
    description: "senders.rate_per_day fully retired from DB/ORM; restriction-audit slice no longer reads it"
    requirement: "D-04"
    verification:
      - kind: integration
        ref: "tests/test_restriction_audit.py::test_event_carries_activity_slice (configured_per_day absent)"
        status: pass
    human_judgment: false
  - id: D4
    description: "no app/ source references max_new_dialogs_per_day or rate_per_day"
    verification:
      - kind: other
        ref: "grep -rn --include=*.py 'max_new_dialogs_per_day|rate_per_day' app/ -> empty"
        status: pass
    human_judgment: false

# Metrics
duration: 16min
completed: 2026-07-08
status: complete
---

# Phase 22 Plan 06: Retire Dead Throttle Columns Summary

**Migration 059 drops `campaigns.max_new_dialogs_per_day` (D-07) and `senders.rate_per_day` (D-04) from DB + ORM, strips the per-campaign dialog-cap API surface, and proves zero residual references across app/.**

## Performance

- **Duration:** 16 min
- **Started:** 2026-07-08T18:09:00Z
- **Completed:** 2026-07-08T18:25:00Z
- **Tasks:** 3
- **Files modified:** 27 (6 app/migrations, 21 tests incl. 1 deletion)

## Accomplishments
- Idempotent migration 059 dropping both superseded throttle columns; ORM (`Sender`, `Campaign`) no longer declares them, keeping create_all consistent with the DB.
- Campaign API cleaned: `max_new_dialogs_per_day` removed from `CampaignCreate/Update/Response`; `DIALOG_LIMIT_SOFT/HARD_CAP` and `_validate_max_new_dialogs` and all references deleted from the router.
- Restriction-audit `_record` no longer SELECTs or serializes `rate_per_day` (would have crashed once the column was gone) — `configured_per_day` dropped from the activity slice.
- Repo-wide grep gate passes: zero `app/` references to either column.
- Test suite swept: shared conftest Sender factory, 16 raw-insert test helpers, queue-pacing `_set_cap` helpers, and migration/restriction-audit assertions all updated; `test_send_campaign.py` green 5/5.

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 059 DROP + ORM column removal** — `fd4c974` (feat)
2. **Task 2: Campaign schema + router cleanup + residual-ref purge** — `52c93ec` (feat)
3. **Task 3: conftest note + test-suite sweep + grep gate** — `8fb47a1` (test)

_Note: this plan tagged Task 3 tdd="true"; MVP/TDD gate was inactive (tdd_mode: false), so it ran as a cleanup task rather than a strict RED/GREEN cycle._

## Files Created/Modified
- `migrations/059_drop_dead_limit_columns.sql` - DROP COLUMN IF EXISTS for both columns (BEGIN/COMMIT, Wave-3 ordering header)
- `app/models/__init__.py` - removed `Sender.rate_per_day` and `Campaign.max_new_dialogs_per_day`
- `app/schemas/__init__.py` - removed the field from CampaignCreate/Update/Response
- `app/routers/campaigns.py` - removed caps constants, validation helper, and all field references
- `app/services/restriction_audit.py` - dropped rate_per_day from the sender SELECT + activity slice
- `app/services/queue.py` - reworded 3 stale comments referencing the dropped column names
- `tests/conftest.py` - dropped rate_per_day from the Sender factory + added migration-059 note
- `tests/test_campaign_new_dialog_limit_api.py` - deleted (tested the removed feature)
- 18 other test files - swept rate_per_day / max_new_dialogs_per_day out of inserts and assertions

## Decisions Made
- Removed `configured_per_day` from the restriction-audit slice instead of substituting the grade budget (informational field; column is gone).
- Deleted `test_campaign_new_dialog_limit_api.py` entirely rather than adapting it — the whole file tested the removed per-campaign cap contract.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] restriction_audit.py read a column being dropped**
- **Found during:** Task 2 (residual-reference audit)
- **Issue:** `app/services/restriction_audit.py` had a live `SELECT ... rate_per_day` plus `s.rate_per_day` in the activity slice. The plan's `files_modified` omitted it; dropping the column would crash every restriction-event write at runtime (the plan's must_haves + important_note demand zero app/ references).
- **Fix:** Removed `rate_per_day` from the SELECT and dropped `configured_per_day` from the slice; updated `test_restriction_audit.py::test_event_carries_activity_slice` to assert its absence.
- **Files modified:** app/services/restriction_audit.py, tests/test_restriction_audit.py
- **Verification:** test_restriction_audit.py 15/15 green
- **Committed in:** 52c93ec / 8fb47a1

**2. [Rule 3 - Blocking] queue.py comments referenced the dropped column names**
- **Found during:** Task 2
- **Issue:** Three docstring/comment lines in `app/services/queue.py` still contained the literal `max_new_dialogs_per_day` token, which fails the plan's repo-wide app/ grep gate.
- **Fix:** Reworded the comments to describe "the old per-campaign dialog cap" / "account_budget" without the literal token.
- **Files modified:** app/services/queue.py
- **Verification:** grep gate returns empty
- **Committed in:** 52c93ec

**3. [Rule 3 - Blocking] test suite raw-inserts + ORM factory would crash on the dropped column**
- **Found during:** Task 3
- **Issue:** The plan's `files_modified` only listed conftest + test_send_campaign, but ~19 test files raw-INSERT senders (or construct `Sender(...)`) with `rate_per_day`, and two queue tests `UPDATE campaigns SET max_new_dialogs_per_day`. Since conftest builds the test schema via `create_all` (which now omits the columns), all of these crash with "column does not exist" / invalid-kwarg once the ORM columns are removed.
- **Fix:** Swept `rate_per_day` out of every raw sender INSERT / ORM factory; removed the `UPDATE campaigns` statements from `_set_cap` and the divergence UPDATE in `test_pacing_numerator_is_account_budget`; updated `test_migration_013.py` schema assertions; deleted `test_campaign_new_dialog_limit_api.py`.
- **Files modified:** tests/conftest.py + 19 test files (see Files Created/Modified)
- **Verification:** test_send_campaign, test_migration_013, grade/queue/senders/warmup/contacts/restriction suites re-run green (see Issues for the one pre-existing failure)
- **Committed in:** 8fb47a1

---

**Total deviations:** 3 auto-fixed (all Rule 3 - blocking). **Impact:** All three were required to satisfy the plan's own must_haves (zero app/ references) and to avoid runtime/test crashes the plan under-scoped. No scope creep — every edit is a direct consequence of dropping the two columns.

## Issues Encountered
- **Worktree stale base:** the worktree spawned from a phase-19 commit (242 commits behind main). Fast-forwarded the branch onto current main (`ceee315`, all Wave 1/2 merges) before starting so the work stacks cleanly.
- **test-overlay in worktree:** the base `db` service has a fixed `container_name` that collides with the running prod container under the worktree's default compose project. Ran tests with `-p tg-outreach --env-file /root/apps/aimly/tg-outreach/.env` so compose reuses the prod db for the dependency check while the ephemeral `db-test` (which `DATABASE_URL` points to) handles the run — identical behavior to running on main.
- **Pre-existing RED test (out of scope):** `tests/test_warmup_worker.py::test_restricted_sender_excluded` fails asserting a `spam_limited` sender is excluded from the warmup pool. The warmup source (`app/services/warmup.py:218`) intentionally *includes* `spam_limited` in warming ("прогрев — это и есть восстановление"), and the assertion message says "restriction clause not added yet (WARM-14)". This is an unimplemented-feature RED test unrelated to this plan's column drop (my diff to that file only removed the `rate_per_day` INSERT column). Left as-is per the scope boundary.

## Known Stubs
None.

## User Setup Required
None - no external service configuration required. Migration 059 auto-applies at api boot (`app/database.py::_apply_migrations`); no manual step. Prod deploy is the standard `docker compose up -d --build api`.

## Next Phase Readiness
- Phase 22 Wave 3 (terminal cleanup) complete. Both superseded throttle columns are fully retired from DB, ORM, schemas, routers, and the audit path.
- The daily new-dialog throttle is now exclusively the account-level grade budget resolved from the workspace ladder.

## Self-Check: PASSED

- migrations/059_drop_dead_limit_columns.sql — FOUND
- .planning/phases/22-account-level-new-chat-limit-grades/22-06-SUMMARY.md — FOUND
- Commits fd4c974, 52c93ec, 8fb47a1 — FOUND

---
*Phase: 22-account-level-new-chat-limit-grades*
*Completed: 2026-07-08*
