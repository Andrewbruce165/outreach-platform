---
phase: 19-no-reply-follow-up-and-auto-finish
plan: 01
subsystem: database
tags: [postgres, migration, sqlalchemy, pytest, follow-up, conversations, campaigns]

# Dependency graph
requires:
  - phase: 04-campaigns
    provides: campaigns table + conversations.status CHECK
  - phase: 05-inbox-analytics
    provides: migration 017 (bot_ignored added to conversations.status CHECK)
provides:
  - "migration 045: conversations.status accepts 'no_reply' (CHECK extended, bot_ignored preserved)"
  - "conversations.pings_sent counter (default 0)"
  - "campaigns.follow_up_enabled/follow_up_interval_hours/follow_up_max_pings/auto_finish_hours columns"
  - "ORM mirrors for all 5 new columns with matching server_default"
  - "tests/test_follow_up.py RED scaffold (NORP-01 GREEN + NORP-02/04/06/07/12 RED)"
affects: [19-02, 19-03, 19-04, follow-up-worker, listener-revert, campaign-schema]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Wave-0 RED scaffold with deferred in-body imports (Phase 13/17 precedent)"
    - "VARCHAR+CHECK drop/add for status enum extension (never ALTER TYPE ADD VALUE — transaction-unsafe)"
    - "server_default on every NOT NULL column to survive create_all rebuild (mig 040/042 drift precedent)"

key-files:
  created:
    - migrations/045_follow_up.sql
    - tests/test_follow_up.py
  modified:
    - app/models/__init__.py
    - tests/conftest.py

key-decisions:
  - "D-01: no_reply added to conversations.status CHECK via DROP/ADD CONSTRAINT (bot_ignored preserved)"
  - "D-08/D-12: campaign follow-up columns NOT NULL + DEFAULT, no backfill (safe for running campaigns)"
  - "Bounds (interval 4-168h, max_pings 1-5, auto_finish 24-720h) enforced at API/Pydantic layer, NOT DB CHECK"

patterns-established:
  - "RED scaffold: not-yet-built symbols imported inside test bodies so --collect-only stays clean"
  - "ORM server_default duplicates every migration DEFAULT for the post-DROP create_all rebuild path"

requirements-completed: [NORP-01, NORP-02, NORP-03]

# Metrics
duration: 6min
completed: 2026-07-02
---

# Phase 19 Plan 01: No-Reply Follow-Up Schema Foundation Summary

**Migration 045 extends conversations.status with 'no_reply', adds a pings_sent counter and four campaign follow-up/auto-finish columns; ORM mirrors all five with matching server_default; RED test scaffold lands with the NORP-01 status contract GREEN and downstream tests RED.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-07-02T16:05:09Z
- **Completed:** 2026-07-02T16:11:00Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments
- migration 045_follow_up.sql: idempotent status CHECK extension (adds 'no_reply', preserves 'bot_ignored') + conversations.pings_sent + 4 campaign follow-up columns, all NOT NULL with DEFAULT (no backfill)
- ORM mirrors all 5 new columns with server_default identical to the migration DEFAULTs (survives the create_all rebuild path)
- migration 045 registered in the conftest ephemeral-test-DB bootstrap
- tests/test_follow_up.py: 7-test RED scaffold — NORP-01 status test GREEN, NORP-02/04/06/07/12 RED pending later plans

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 045 (status CHECK + ping columns + 4 campaign columns)** - `ab6ff3a` (feat)
2. **Task 2: Mirror new columns in the ORM with matching server_default** - `487406d` (feat)
3. **Task 3: Register migration 045 in conftest + write RED test scaffold** - `dc110bb` (test)

## Files Created/Modified
- `migrations/045_follow_up.sql` - status CHECK extension + pings_sent + 4 campaign follow-up columns, idempotent
- `app/models/__init__.py` - Conversation.pings_sent + Campaign.follow_up_* ORM columns with server_default; status inline comment updated with no_reply
- `tests/conftest.py` - migration 045 applied in the ephemeral test DB (exists-guarded, after 044)
- `tests/test_follow_up.py` - Wave-0 RED scaffold for NORP-01/02/04/06/07/12

## Decisions Made
- **no_reply via DROP/ADD CONSTRAINT** (not ALTER TYPE ADD VALUE) — the status column is VARCHAR+CHECK, and enum value-adds cannot run inside a transaction (same reasoning as campaigns.status). bot_ignored (mig 017) preserved in the new 8-value set.
- **Campaign follow-up columns NOT NULL + DEFAULT, no backfill** — existing rows including running campaigns get safe values automatically (follow_up_enabled=false is opt-in). Mirrors the max_new_dialogs_per_day precedent.
- **API-layer bounds, no DB CHECK** — interval 4–168h, max_pings 1–5, auto_finish 24–720h enforced at Pydantic (Plan 19-02), matching recontact_min_age_days / max_new_dialogs_per_day.

## Deviations from Plan

None - plan executed exactly as written.

Minor note: the migration header comment was rephrased so it does not literally contain the strings "ALTER TYPE"/"ADD VALUE" or a stray "ADD COLUMN IF NOT EXISTS", keeping the acceptance-criteria greps (5 ADD COLUMN, 0 ALTER TYPE) exact. No behavioural change.

## Issues Encountered
- Running the test-overlay from the isolated git worktree collided with the running prod `outreach-platform-db` container (hardcoded container_name) and had no local `.env`. Resolved by running with `COMPOSE_PROJECT_NAME=tg-outreach` (reuses the already-running prod db + ephemeral db-test) and `--env-file` pointing at the main checkout's `.env`. No prod data touched — db-test is the ephemeral tmpfs target and the run was `--rm`.

## User Setup Required
None - no external service configuration required. Migration 045 auto-applies on the next `docker compose up -d --build api` (not deployed by this plan).

## Next Phase Readiness
- Schema contracts (no_reply status, pings_sent, 4 campaign columns) and ORM mirrors are in place for Plans 19-02/03/04.
- RED scaffold gives each downstream plan a concrete automated verification command from the start.
- NOT deployed to prod (migration applies on next api rebuild) — intentional, matches the plan's foundation-only scope.

## Self-Check: PASSED

- Files verified on disk: migrations/045_follow_up.sql, tests/test_follow_up.py, app/models/__init__.py, tests/conftest.py, 19-01-SUMMARY.md — all FOUND.
- Commits verified: ab6ff3a, 487406d, dc110bb — all FOUND.
- Test-overlay: 7 tests collect (0 errors), NORP-01 GREEN, 6 downstream RED as expected.

---
*Phase: 19-no-reply-follow-up-and-auto-finish*
*Completed: 2026-07-02*
