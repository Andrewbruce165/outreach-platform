---
phase: 22-account-level-new-chat-limit-grades
plan: 04
subsystem: api
tags: [fastapi, pydantic, senders, grade-ladder, rate-limits, workspace-scoped]

# Dependency graph
requires:
  - phase: 22-01
    provides: sender grade columns (current_level, level_updated_at), grade_ladder resolver, sender_grade_settings table
provides:
  - rate_per_day fully removed from the sender API surface (schemas, caps, validation, response, create/update writes)
  - SenderResponse carries current_level, level_updated_at, remaining_daily_budget
  - GradeOverrideRequest schema (ge=1, le=3)
  - PATCH /senders/{slug}/grade — workspace-scoped manual grade override that resets the progression timer
  - list endpoint computes per-sender remaining_daily_budget from the grade ladder
affects: [22-06, warmup, queue, frontend-sender-card]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Grade budget is derived (grade_ladder.load_ladder + budget_for_level), never a stored per-sender field"
    - "Manual grade override writes the SAME two fields as auto-progression (current_level + level_updated_at=NOW()); no separate frozen flag"
    - "Workspace ownership on the grade PATCH via _load_sender_by_slug (404 on non-owned), bind-params-only UPDATE"

key-files:
  created: []
  modified:
    - app/schemas/__init__.py
    - app/routers/senders.py
    - tests/test_senders.py

key-decisions:
  - "Used existing _load_sender_by_slug as the workspace-ownership gate instead of the plan's referenced _assert_workspace_owns_sender (which does not exist in this codebase); T-22-10 mitigation fully satisfied"
  - "remaining_daily_budget = grade budget for current_level minus COUNT(DISTINCT recipient_phone) sent in trailing 24h, clamped at 0; computed only on the list endpoint (None elsewhere, same convention as sent_today)"
  - "rate_per_day left untouched in the ORM (dropped in 22-06); only the API stopped reading/writing it"

patterns-established:
  - "Derived daily budget replaces the old per-sender rate_per_day cap end-to-end on the API"

requirements-completed: [D-04, D-12, D-15]

# Metrics
duration: ~18min
completed: 2026-07-08
---

# Phase 22 Plan 04: Sender API grade surface + rate_per_day removal Summary

**Removed rate_per_day from the entire sender API surface (D-04), surfaced the account grade + a grade-driven remaining daily new-chat budget on SenderResponse (D-12), and added a workspace-scoped PATCH /senders/{slug}/grade that resets the progression timer (D-15).**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-07-08T17:33Z (approx)
- **Completed:** 2026-07-08T17:52Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments
- `rate_per_day` / `per_day` gone from `RateLimits`, `SenderCreate`, `SenderUpdate`, `RATE_HARD_CAP`, `RATE_SOFT_CAP`, `_validate_rate_limits`, `_sender_to_response`, and the create/update write paths — per-minute/per-hour untouched.
- `SenderResponse` now exposes `current_level`, `level_updated_at`, and `remaining_daily_budget`; the list endpoint computes the remaining budget per sender via `grade_ladder.load_ladder` + `budget_for_level` minus trailing-24h distinct new dialogs (clamped ≥0).
- New `GradeOverrideRequest` (`ge=1, le=3`) + `PATCH /senders/{slug}/grade`: workspace-scoped, bind-params-only `UPDATE senders SET current_level, level_updated_at = NOW()` — identical write to auto-progression, resets the timer, no frozen flag.
- `tests/test_senders.py` extended with rate-removal, grade-exposure (get + list), override happy-path (level + timer reset), out-of-range (422), and cross-tenant (404) cases; two pre-existing rate tests updated to the D-04 contract. Full file: 27 passed.

## Task Commits

Each task was committed atomically:

1. **Task 1: Remove rate_per_day from schemas + sender router surface (D-04)** - `f901987` (feat)
2. **Task 2: SenderResponse grade fields + PATCH /senders/{slug}/grade override (D-12/D-15)** - `2b24214` (feat)
3. **Task 3: Extend test_senders.py — rate removal + grade fields + override** - `e8ba3c9` (test)

## Files Created/Modified
- `app/schemas/__init__.py` - dropped per_day from RateLimits/SenderCreate/SenderUpdate; added current_level/level_updated_at/remaining_daily_budget to SenderResponse; added GradeOverrideRequest.
- `app/routers/senders.py` - dropped rate_per_day from caps/validation/response/create/update; imported grade_ladder helpers; list endpoint computes remaining_daily_budget; added override_sender_grade route.
- `tests/test_senders.py` - 6 new Phase 22 tests + 2 updated pre-existing rate tests.

## Decisions Made
- **Ownership gate:** the plan referenced `_assert_workspace_owns_sender`, which is not defined in this codebase. The actual workspace-ownership pattern is `_load_sender_by_slug` (workspace-scoped SELECT, 404 on non-owned). Used it — the T-22-10 elevation-of-privilege mitigation (tenant cannot re-grade a sender it does not own) is fully satisfied and proven by the cross-tenant test.
- **remaining_daily_budget semantics:** grade budget for `current_level` minus distinct new dialogs opened in the trailing 24h, clamped at 0; list-endpoint-only (None on single-sender paths).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Updated two pre-existing rate tests to the D-04 contract**
- **Found during:** Task 3 (test run)
- **Issue:** `test_create_sender_defaults_rate_limits` asserted `per_day: 150` in `rate_limits`, and `test_patch_rate_limit_just_at_hard_cap_ok` asserted 3 warnings — both assume the removed `rate_per_day`.
- **Fix:** Updated the defaults assertion to `{per_minute: 4, per_hour: 20}` and the warning count to 2, matching the new D-04 contract.
- **Files modified:** tests/test_senders.py
- **Verification:** `pytest tests/test_senders.py` → 27 passed.
- **Committed in:** `e8ba3c9` (Task 3 commit)

**2. [Rule 3 - Blocking] Substituted the non-existent `_assert_workspace_owns_sender` helper**
- **Found during:** Task 2 (grade route implementation)
- **Issue:** The plan named `_assert_workspace_owns_sender` as the ownership gate; no such symbol exists in the repo.
- **Fix:** Used the established `_load_sender_by_slug` workspace-scoped loader (404 on non-owned) — same security guarantee.
- **Files modified:** app/routers/senders.py
- **Verification:** cross-tenant override test returns 404 and the target sender stays level 1.
- **Committed in:** `2b24214` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 blocking).
**Impact on plan:** No scope change; both are test/API-fidelity adjustments to match the real codebase. No scope creep.

## Issues Encountered
- **Worktree base predated Wave 1:** the worktree branched from a phase-19 commit, missing the merged 22-01 grade foundation. Merged current `main` into the worktree branch before starting so grade columns / grade_ladder / migrations were present.
- **Test-overlay container-name conflict under parallel worktree execution:** `docker compose run api` pulls in the prod `db` service (fixed `container_name: outreach-platform-db`), which conflicts with the running prod stack when invoked under a per-worktree project name. Resolved with a throwaway overlay (`depends_on: !override db-test`) + unique project name `tg22_04_iso` + `--env-file` pointing at the prod `.env` for `${...}` interpolation. Ephemeral stack torn down with `down -v` on the isolated project name only; prod `outreach-platform-db` verified still up.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- 22-06 will drop the `senders.rate_per_day` ORM column and finalize the Campaign daily-dialog schema; this plan already removed all API reads/writes so the column is dead weight.
- Frontend sender card can now render `current_level`, `level_updated_at`, and `remaining_daily_budget`, and call `PATCH /senders/{slug}/grade`.

## Self-Check: PASSED

- All modified files present on disk (senders.py, schemas/__init__.py, test_senders.py, 22-04-SUMMARY.md).
- All task commits present in git (f901987, 2b24214, e8ba3c9).
- Full `tests/test_senders.py`: 27 passed. Targeted `-k "rate or grade or override"`: 10 passed.
- `grep -c rate_per_day app/routers/senders.py` → 0.

---
*Phase: 22-account-level-new-chat-limit-grades*
*Completed: 2026-07-08*
