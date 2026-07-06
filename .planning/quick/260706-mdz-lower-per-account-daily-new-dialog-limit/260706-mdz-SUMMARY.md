---
phase: quick-260706-mdz
plan: 01
subsystem: api
tags: [campaigns, rate-limit, anti-spam, migration, pydantic, openapi]

# Dependency graph
requires:
  - phase: 12-new-dialog-limit
    provides: max_new_dialogs_per_day column (migration 033), green-corridor validation, queue enforcement
provides:
  - Per-account daily new-dialog cap lowered 50 → 10 across DB default, all existing rows, and API guard rails
  - Green corridor tightened: soft-warn >10 (recommend 10), hard-reject >30 (422)
affects: [campaigns, queue-pacing, lovable-frontend]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ORM server_default kept in lock-step with raw-SQL migration to avoid create_all drift (memory: ORM default= vs server_default= drift)"
    - "Idempotent guarded UPDATE (WHERE = old-default) preserves manually-set values while migrating default-value rows"

key-files:
  created:
    - migrations/050_lower_new_dialog_cap.sql
  modified:
    - app/models/__init__.py
    - app/schemas/__init__.py
    - app/routers/campaigns.py
    - tests/test_campaign_new_dialog_limit_api.py
    - lovable-handoff/openapi.json
    - lovable-handoff/types/api.ts

key-decisions:
  - "D-1: default AND all 6 existing prod campaigns lowered 50 → 10 (guarded UPDATE preserves any non-50 manual value)"
  - "D-2: green corridor soft 50→10, hard 100→30 (Pydantic le, router constants, descriptions all synced)"
  - "D-3: services/queue.py untouched — per-(sender,campaign) cap + Phase-13 even pacing read the value at query time and adapt automatically"

patterns-established:
  - "Parameter/guardrail changes cascade through five sync points: migration, ORM server_default, three Pydantic schemas, router constants — kept identical to prevent drift"

requirements-completed: [QUICK-MDZ-NDLG]

# Metrics
duration: ~5min
completed: 2026-07-06
---

# Quick 260706-mdz: Lower Per-Account Daily New-Dialog Limit Summary

**Per-account daily new-dialog cap lowered 50 → 10 stack-wide (DB default + all 6 prod campaigns + green corridor soft 10 / hard 30), queue logic untouched and self-adapting.**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-07-06T16:12:19Z
- **Completed:** 2026-07-06T16:17:02Z
- **Tasks:** 3
- **Files modified:** 6 (1 created, 5 modified)

## Accomplishments
- Migration 050 changes the DB default to 10 and updates every existing row still at the old default 50 (all 6 prod campaigns); auto-applied on api start, verified recorded in `schema_migrations` and every campaign row now reads 10.
- Green corridor tightened everywhere in sync: ORM `server_default="10"`, Pydantic `default=10, le=30` on Create/Update/Response, router `DIALOG_LIMIT_SOFT_CAP=10` / `DIALOG_LIMIT_HARD_CAP=30`, with all stale 50/100 descriptions and docstrings rewritten.
- API test corridor updated (default 10, soft-warn recommends 10, 422 above 30); 17/17 targeted tests green via test-overlay (queue tests unchanged, they bypass Pydantic via raw UPDATE).
- Lovable handoff regenerated from the running api: `openapi.json` campaign field now `maximum: 30`, `default: 10`, "Green corridor <=10 ... hard cap 30".

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 050 + sync ORM/Pydantic/router to new corridor** - `2565035` (feat)
2. **Task 2: Update API test to new corridor, suite green via test-overlay** - `27b5cda` (test)
3. **Task 3: Deploy (migration auto-applies), verify DB state, regenerate Lovable handoff** - `e04a35f` (chore)

_Plan metadata (SUMMARY/STATE) committed separately by the orchestrator._

## Files Created/Modified
- `migrations/050_lower_new_dialog_cap.sql` - Idempotent `SET DEFAULT 10` + guarded `UPDATE ... WHERE = 50`
- `app/models/__init__.py` - Campaign `server_default="50"` → `"10"` + comment block rewrite
- `app/schemas/__init__.py` - CampaignCreate/Update/Response: default 50→10, le 100→30, description rewrite
- `app/routers/campaigns.py` - `DIALOG_LIMIT_SOFT_CAP=10`, `DIALOG_LIMIT_HARD_CAP=30`, docstring/comment rewrite
- `tests/test_campaign_new_dialog_limit_api.py` - Assertions retargeted to 10/30 corridor (test renamed `test_create_default_is_10`)
- `lovable-handoff/openapi.json`, `lovable-handoff/types/api.ts` - Regenerated via `scripts/export-handoff.sh` (no hand-edit)

## Decisions Made
None new - executed the three locked user decisions (D-1/D-2/D-3) exactly as specified in the plan.

## Deviations from Plan

None - plan executed exactly as written.

Two verification-command discrepancies were noted but required no code change:
- The plan's Task-3 step-2 command queried `schema_migrations.filename`; the prod table's column is `version`. The applier log already confirmed `[migrate] OK 050_lower_new_dialog_cap`, and the record was confirmed via `SELECT version, applied_at ... WHERE version LIKE '050%'`.
- Two `"maximum": 100` entries remain in `openapi.json` — they are pagination `limit` query params (`in: query`, default 50), unrelated to `max_new_dialogs_per_day`. Correctly left as-is.

## Issues Encountered
- Pre-existing Postgres collation-version warning (`2.41` vs OS `2.36`) prints on every `psql` call. Unrelated to this task, does not affect queries; left untouched.
- Orphan container `tg-outreach-db-test-1` (ephemeral test DB from the Task-2 test-overlay run) triggered a benign compose warning during api rebuild. Left in place — removing it via `down -v` is forbidden (would wipe the prod volume); `run --rm` cleans it up on next use.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Deployed and verified: migration 050 recorded, column default = 10, all 6 campaigns = 10, corridor guard rails active (11–30 warn / >30 reject 422).
- Queue worker (`services/queue.py`) unchanged — running campaigns will naturally throttle to the new cap on the next tick. Sender rate limits (4/20/150) and 20–55s intervals untouched.
- Lovable frontend should pick up the new bounds/description from the regenerated `openapi.json` + `types/api.ts` on next generation.

## Self-Check: PASSED

All created/modified files present on disk; all three task commits (`2565035`, `27b5cda`, `e04a35f`) present in git history.

---
*Phase: quick-260706-mdz*
*Completed: 2026-07-06*
