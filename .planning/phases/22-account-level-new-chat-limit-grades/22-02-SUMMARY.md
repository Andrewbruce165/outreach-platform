---
phase: 22-account-level-new-chat-limit-grades
plan: 02
subsystem: api
tags: [fastapi, postgres, background-worker, grade-ladder, warmup-settings-pattern, multitenancy]

# Dependency graph
requires:
  - phase: 22-01
    provides: sender_grade_settings table (mig 058), senders grade columns (mig 056), app/services/grade_ladder.py resolver
provides:
  - GET/PUT /api/v1/sender-grade-settings — per-workspace configurable 3-level ladder with green-corridor warnings (D-16)
  - GradeProgressionWorker — hourly sweep auto-advancing sender grade levels per the workspace ladder (D-14), frozen at level 3 (D-17)
affects: [22-03, 22-04, 22-05, warmup-budget, queue-rewrite, sender-api]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Settings API cloned from warmup GET/PUT /settings (resolve-defaults on read, idempotent ON CONFLICT upsert on write, workspace-scoped via auth_dep)"
    - "Green-corridor soft warnings via schemas.WarningItem (200 + warnings[]), Pydantic Field bounds as the hard cap — mirrors senders.py rate limits"
    - "Background sweep worker cloned from WarmupWorker asyncio lifecycle (start/stop/_run), single set-based UPDATE per tick"
    - "Per-workspace ladder read into SQL via LEFT JOIN + COALESCE to code-defaults (no code constant)"

key-files:
  created:
    - app/routers/grade_settings.py
    - app/services/grade_progression.py
    - tests/test_grade_settings.py
    - tests/test_grade_progression.py
  modified:
    - app/main.py

key-decisions:
  - "One level per tick (eventual catch-up): Postgres NOW() is the transaction-start timestamp, so a just-advanced sender's level_updated_at freezes at NOW() and its delta becomes 0 — it cannot double-step within a tick. A single set-based UPDATE per hourly tick is therefore both correct and simplest (RESEARCH Pattern 3)."
  - "Step-days floor surfaced through WarningItem.recommended_max slot (schema has no min-field) so the UI still shows the green-corridor edge for too-short steps."
  - "Progression tests run the imported _SWEEP_SQL directly against the isolated async_db_session (not the worker's own AsyncSessionLocal) — keeps seed+sweep+assert in one rolled-back transaction, no cross-connection visibility issues or committed global side-effects."

patterns-established:
  - "Grade settings API is the canonical template for future per-workspace numeric-ladder settings"

requirements-completed: [D-16, D-14, D-17]

# Metrics
duration: 12min
completed: 2026-07-08
---

# Phase 22 Plan 02: Grade Ladder Settings API + Auto-Progression Worker Summary

**Per-workspace GET/PUT /sender-grade-settings (warmup-settings clone, green-corridor warnings, cross-tenant scoped) plus an hourly GradeProgressionWorker that auto-advances sender grade levels per the workspace ladder and freezes them at level 3.**

## Performance

- **Duration:** 12 min
- **Started:** 2026-07-08T17:34:28Z
- **Completed:** 2026-07-08T17:48:00Z
- **Tasks:** 3
- **Files modified:** 5 (4 created, 1 modified)

## Accomplishments
- `GET /api/v1/sender-grade-settings` resolves the workspace ladder to code-defaults 5/30, 9/30, 13 when no row exists (D-16), scoped by `ctx.workspace_id`.
- `PUT /api/v1/sender-grade-settings` idempotently upserts the fixed 3-level ladder (ON CONFLICT), returns green-corridor `warnings[]` for out-of-recommended values but 200; Pydantic `le=100`/`le=365` bounds are the hard cap (422).
- `GradeProgressionWorker` runs a single set-based UPDATE hourly that advances `current_level += 1` and resets `level_updated_at` once `NOW() - level_updated_at >= step_days(current_level)` — step-days read from the per-workspace `sender_grade_settings` (COALESCE to 30 default), never a code constant.
- `s.current_level < 3` guard makes level 3 permanent (D-17).
- 10 integration tests green via test-overlay (defaults, custom round-trip, green-corridor warning, hard-cap 422, cross-tenant isolation, due-advance, in-window hold, level-2→3, stop-at-3, ladder-driven step-days).

## Task Commits

Each task was committed atomically:

1. **Task 1: /sender-grade-settings GET/PUT router + green-corridor validation** - `8c46335` (feat)
2. **Task 2: Grade auto-progression sweep worker** - `49289f9` (feat)
3. **Task 3: Ladder + progression integration tests** - `7ee2526` (test)

**Plan metadata:** (this commit) `docs(22-02): complete grade ladder settings + progression plan`

## Files Created/Modified
- `app/routers/grade_settings.py` - GET/PUT /sender-grade-settings, GradeLadderUpdate body, _validate_ladder green-corridor warnings, _shape response, workspace-scoped upsert.
- `app/services/grade_progression.py` - GradeProgressionWorker (hourly), _SWEEP_SQL set-based ladder-driven UPDATE with D-17 stop-at-3.
- `tests/test_grade_settings.py` - defaults/round-trip/warning/hard-cap/cross-tenant.
- `tests/test_grade_progression.py` - advance/hold/stop-at-3/ladder-driven-step-days.
- `app/main.py` - register grade_settings.router; wire grade_progression_worker into lifespan start/stop + import.

## Decisions Made
- **One level per tick (eventual catch-up):** transaction-frozen `NOW()` means a just-advanced sender cannot double-step in the same tick, so a single UPDATE per hourly tick is correct and simplest (documented in the module docstring). Chose this over a loop-until-rowcount-0 because the loop is a no-op given the frozen timestamp.
- **Step-days floor via `recommended_max`:** `WarningItem` has no min-field, so a too-short step surfaces its recommended floor in the `recommended_max` slot to keep the corridor edge visible in the UI.
- **Tests exercise `_SWEEP_SQL` directly** against the isolated session rather than the worker's own session — deterministic, fully rolled back, no committed pollution.

## Deviations from Plan
None - plan executed exactly as written. All four target files created and main.py wired per the acceptance criteria.

## Issues Encountered
- **Stale worktree base:** the executor worktree spawned from commit 92bd54b (pre-Wave-1), missing migrations 056/057/058, grade_ladder.py, and the SenderGradeSettings ORM model. Since HEAD was a clean ancestor of main with no local commits, resolved via `git merge --ff-only main` before any task work (per the "Worktree executor stale base" guidance — no naive merge, no spurious deletions).
- **Test-overlay parallel-execution collisions:** the base `db` service has a fixed `container_name: outreach-platform-db` that collides with the running prod db (and sibling agents) when pulled in via merged `depends_on`. Resolved by pre-starting only the ephemeral `db-test` under an isolated project name (`gradetest_a0348363`) and running pytest with `--no-deps`, so the prod `db` service was never touched. The worktree also lacks a `.env` (gitignored, not copied), so compose interpolated blank vars → Settings validation failed; passed `--env-file /root/apps/aimly/tg-outreach/.env` from the main checkout. Ephemeral db-test (tmpfs, no prod volume) was stopped/removed after the run; no `down -v` used.

## User Setup Required
None - no external service configuration required. The grade ladder auto-resolves to code-defaults for every workspace with no row; the progression worker starts automatically with the api.

## Next Phase Readiness
- Ladder API and auto-progression are live and green; sibling Wave-2 plans (22-03 queue, 22-04 sender API, 22-05 warmup budget) can consume `grade_ladder.load_ladder` and the `current_level` column, which this plan advances over time.
- No blockers.

## Self-Check: PASSED

- Files: `app/routers/grade_settings.py`, `app/services/grade_progression.py`, `tests/test_grade_settings.py`, `tests/test_grade_progression.py`, `app/main.py` — all present.
- Commits: `8c46335`, `49289f9`, `7ee2526` — all found in git history.
- Tests: 10 passed via test-overlay.

---
*Phase: 22-account-level-new-chat-limit-grades*
*Completed: 2026-07-08*
