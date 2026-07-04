---
phase: 20-account-profile-management
plan: 01
subsystem: database
tags: [postgres, jsonb, sqlalchemy, pydantic, migrations, telethon, testing, red-scaffold]

# Dependency graph
requires:
  - phase: 02-tg-accounts-contacts
    provides: "senders table + Sender ORM + onboarding verify-code finalize path"
  - phase: 01-workspace-foundation
    provides: "workspace scoping + auth_dep + JWT test fixtures"
provides:
  - "5 cached-profile columns on senders (mig 049): tg_username, tg_bio, tg_photo, tg_photo_mime, profile_field_changed_at"
  - "Sender ORM mirror with server_default on the NOT NULL JSONB (create_all-safe)"
  - "Phase-20 Pydantic request/response schemas (ProfileUpdate, ProfileWarningItem, ProfileUpdateResponse, UsernameCheckResponse, TwoFAPasswordUpdate, RecoveryEmailStart, RecoveryEmailConfirm)"
  - "SenderResponse profile fields (tg_username/tg_bio/has_photo/profile_field_changed_at)"
  - "Wave-0 RED test scaffold naming PROF-01..08 + D-08/D-09 (tests/test_account_profile.py + onboarding PROF-08)"
affects: [20-02, 20-03, 20-04, 20-05, account-profile-identity, account-profile-photo, account-profile-2fa, account-profile-frontend]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "server_default on NOT NULL JSONB columns to survive create_all schema drift (mig 040/042 precedent)"
    - "Per-field cooldown STATE stored as JSONB {field: iso8601} (profile_field_changed_at) — not an audit log"
    - "Wave-0 RED scaffold with deferred in-body imports so --collect-only stays clean while behaviour is RED"
    - "ProfileWarningItem (code/message/severity) kept DISTINCT from the rate-limit WarningItem (field/value/recommended_max)"

key-files:
  created:
    - migrations/049_account_profile.sql
    - tests/test_account_profile.py
  modified:
    - app/models/__init__.py
    - app/schemas/__init__.py
    - tests/test_onboarding.py

key-decisions:
  - "Migration renumbered 047 -> 049 (047/048 slots consumed by quick-260703-ssv after the plan was authored)"
  - "profile_field_changed_at is per-field cooldown STATE (JSONB), NOT NULL DEFAULT '{}'::jsonb, mirrored with server_default on the ORM"
  - "has_photo is a bool on the row; photo bytes served via a separate authenticated endpoint (D-11) — not embedded in the list"

patterns-established:
  - "Pattern: cached Telegram profile columns (NULL = not yet cached) live on senders, refreshed on resync/finalize"
  - "Pattern: RED-scaffold test names are a contract downstream plans turn GREEN"

requirements-completed: [PROF-01]

# Metrics
duration: 10min
completed: 2026-07-04
---

# Phase 20 Plan 01: Foundation Schema and Test Scaffold Summary

**Cached-profile columns on senders (mig 049 + ORM with server_default JSONB), all Phase-20 Pydantic profile schemas, and a Wave-0 RED test scaffold pinning PROF-01..08 + D-08/D-09 (PROF-01 columns GREEN, the rest RED).**

## Performance

- **Duration:** 10 min
- **Started:** 2026-07-04T08:33:43Z
- **Completed:** 2026-07-04T08:43:45Z
- **Tasks:** 3
- **Files modified:** 5 (2 created, 3 modified)

## Accomplishments
- Migration 049 adds the 5 cached-profile columns idempotently (`ADD COLUMN IF NOT EXISTS`); `profile_field_changed_at` is `JSONB NOT NULL DEFAULT '{}'::jsonb`.
- `Sender` ORM mirrors the 5 columns; the NOT NULL JSONB carries `server_default=text("'{}'::jsonb")` so `create_all` (test/fresh DB) builds the same DB default — a raw INSERT omitting it succeeds (verified GREEN).
- 7 new Pydantic schemas + 4 profile fields on `SenderResponse`; `ProfileWarningItem` (D-09) kept strictly separate from the pre-existing rate-limit `WarningItem` (D-14, byte-identical, untouched).
- `tests/test_account_profile.py` lands with 9 tests naming PROF-01..08 + D-08/D-09; `test_profile_columns_defaults` GREEN, the other 8 RED on missing endpoints. `tests/test_onboarding.py` gains `test_finalize_caches_profile` (RED, PROF-08) + a `me_username` mock hook.

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 049 + Sender ORM columns** - `210cdb8` (feat)
2. **Task 2: Pydantic schemas + SenderResponse profile fields** - `70b23d6` (feat)
3. **Task 3: Wave-0 RED test scaffold** - `87081c5` (test)

**Plan metadata:** (final docs commit — this SUMMARY + STATE + ROADMAP)

## Files Created/Modified
- `migrations/049_account_profile.sql` - idempotent ADD COLUMN IF NOT EXISTS for the 5 profile columns (renamed from 047 due to slot collision).
- `app/models/__init__.py` - `Sender` gains tg_username/tg_bio/tg_photo/tg_photo_mime + profile_field_changed_at (server_default JSONB).
- `app/schemas/__init__.py` - `EmailStr` import; SenderResponse profile fields; 7 new profile schemas.
- `tests/test_account_profile.py` - Wave-0 RED scaffold (9 tests, deferred in-body imports).
- `tests/test_onboarding.py` - `_make_mock_client(me_username=...)` + `test_finalize_caches_profile` (RED, PROF-08).

## Decisions Made
- **Migration renumbered 047 → 049.** The plan assumed slot 047 was free, but quick-task 260703-ssv landed `047_message_queue_priority_default.sql` and `048_sender_long_pause_until.sql` after the plan was authored. Using 047 would have created a two-file numbering collision. 049 is the next free slot and respects the `NNN_short_name.sql` convention + the filename-keyed auto-applier.
- **`profile_field_changed_at` = per-field cooldown STATE (JSONB), not an audit log.** `{"username": iso8601, ...}`; server_default `'{}'::jsonb` on both the migration and ORM so it never NULL-violates a raw INSERT.
- **`ProfileWarningItem` is a new schema, not a reuse of `WarningItem`.** The rate-limit `WarningItem` (field/value/recommended_max, D-14) is numeric-shaped; the profile advisory (code/message/severity, D-09) is text-shaped. Kept separate and left the existing schema byte-identical.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Migration slot 047 already taken → renumbered to 049**
- **Found during:** Task 1 (Migration 047 + Sender ORM columns)
- **Issue:** The plan mandated `migrations/047_account_profile.sql` ("next free number is 047 after 046"), but `migrations/047_message_queue_priority_default.sql` and `migrations/048_sender_long_pause_until.sql` already exist (created by quick-task 260703-ssv after the plan was written). Creating a second `047_*` file is a numbering collision that violates the project's `NNN_short_name.sql` convention.
- **Fix:** Created `migrations/049_account_profile.sql` (next genuinely-free slot) with the identical body the plan specified. A rationale comment documents the renumber. The ORM/schema/test contents are otherwise exactly as the plan wrote them.
- **Files modified:** migrations/049_account_profile.sql (instead of 047)
- **Verification:** `test_profile_columns_defaults` GREEN (columns + server_default fire); full suite collects 981 tests / 0 errors; migration is idempotent `ADD COLUMN IF NOT EXISTS`.
- **Committed in:** `210cdb8` (Task 1 commit)

**Note for the verifier / downstream plans:** the profile columns live in **migration 049**, not 047. The plan's must-have artifact path `migrations/047_account_profile.sql` should be read as `migrations/049_account_profile.sql`. Every truth (columns exist after the migration applies, raw INSERT omitting profile_field_changed_at succeeds, RED scaffold GREEN/RED split) holds — only the number changed.

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Renumber only; no semantic change to the schema, ORM, schemas, or tests. No scope creep.

## Issues Encountered
- Repo has concurrent uncommitted work from a parallel agent (quick-tasks 260704-bty / 260704-buc — deleted `app/routers/proxy_pool.py` + `app/routers/queue.py`, modified STATE.md). Verified those deletions have no dangling references (`app.main` imports cleanly, full suite collects 981/0). Followed the parallel-agent commit rule: staged only my own files per task, never `git add -A`; left the other agent's changes untouched.

## Known Stubs
- **`SenderResponse.has_photo` defaults to `False`** (schema default). This is intentional for Wave 0 — the enrichment that computes `has_photo` from `tg_photo IS NOT NULL` lands with the photo endpoints in a downstream Wave-20 plan (test_photo / test_photo_serve_auth are the RED targets that turn it GREEN). Not a blocker for this plan's goal (schema/columns/scaffold foundation).
- **`profile_field_changed_at` default `{}`** on the response is likewise wired to the real column value by the profile endpoints in downstream plans. The column itself is fully live now.

## User Setup Required
None - no external service configuration required. Migration 049 auto-applies on the next `docker compose up -d --build api` (fail-fast if it raises). Not yet deployed to prod.

## Next Phase Readiness
- Downstream Wave-1..4 plans (identity, photo, 2FA, frontend) have their data contract (columns + ORM), their binding schemas, and a named RED test file to turn GREEN.
- Migration 049 is idempotent and will apply cleanly on api restart; the columns already exist in the test schema via create_all.
- No blockers. Concurrent parallel-agent work is isolated and does not touch Phase-20 files.

---
*Phase: 20-account-profile-management*
*Completed: 2026-07-04*

## Self-Check: PASSED
- All created/modified files present (migration 049, test_account_profile.py, models, schemas, test_onboarding.py, SUMMARY).
- All task commits present (210cdb8, 70b23d6, 87081c5).
