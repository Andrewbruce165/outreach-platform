---
phase: 260704-bty-batch-h
plan: 01
subsystem: api
tags: [dead-code, routers, cleanup, fastapi, queue, proxy_pool]

# Dependency graph
requires:
  - phase: (none)
    provides: (standalone dead-code removal)
provides:
  - Removed unimportable dead router modules app/routers/queue.py + app/routers/proxy_pool.py
affects: [future greps/refactors of app/routers, Batch G deploy]

# Tech tracking
tech-stack:
  added: []
  patterns: []

key-files:
  created: []
  modified:
    - app/routers/queue.py (deleted)
    - app/routers/proxy_pool.py (deleted)

key-decisions:
  - "Deleted both dead router files verbatim per plan — no salvage; both imported the non-existent app.routers.auth and were never mounted"

patterns-established: []

requirements-completed: [WR-01]

# Metrics
duration: 41min
completed: 2026-07-04
---

# Phase 260704-bty Batch H: Remove Dead Router Files Summary

**Deleted two unimportable, unmounted dead router modules (`app/routers/queue.py`, `app/routers/proxy_pool.py`) that both imported the non-existent `app.routers.auth` — pure dead-code cleanup, no behavior/DB change.**

## Performance

- **Duration:** 41 min (dominated by repeated full-suite test runs to prove the pre-existing cascade is not caused by the deletion)
- **Started:** 2026-07-04T08:33:12Z
- **Completed:** 2026-07-04T09:14:41Z
- **Tasks:** 2
- **Files modified:** 2 (both deleted)

## Accomplishments
- Removed `app/routers/queue.py` (155 lines) and `app/routers/proxy_pool.py` (272 lines) — 427 lines of dead code gone.
- Confirmed via grep that no module in `app/` or `tests/` imports either router, and that `app/main.py` never mounted them (only `app.services.queue`, the live service, is referenced).
- Verified `import app.main` still succeeds (IMPORT_OK) and the queue/send/rotation test areas remain green (60/60 targeted).
- Left the live `app/services/queue.py`, the `ProxyPool` ORM model, and the `proxy_pool` DB table untouched (explicit non-goals).

## Task Commits

1. **Task 1: Re-confirm no importers, then delete the two dead router files** - `a4aebaf` (refactor)
2. **Task 2: Verify app imports cleanly and test-overlay suite passes** - no code change (verification only; folded into Task 1's commit)

**Plan metadata / docs:** committed separately (this SUMMARY + PLAN + deferred-items).

## Files Created/Modified
- `app/routers/queue.py` - DELETED (dead, unimportable: `from app.routers.auth import verify_api_key`; auth.py does not exist; never mounted).
- `app/routers/proxy_pool.py` - DELETED (same dead/unimportable pattern; the ORM model + DB table are separate and untouched).

## Decisions Made
None - followed plan as specified. Both files removed verbatim; no salvage since they were unimportable and unmounted.

## Deviations from Plan

None - plan executed exactly as written. Both dead router files were deleted; all non-goals (`app/services/queue.py`, `ProxyPool` model, `proxy_pool` table, conftest/test `"proxy_pool"` string references) were left untouched.

## Issues Encountered

**1. Worktree isolation vs shared checkout (harness).** Initial edits/tests accidentally ran against the shared checkout `/root/apps/aimly/tg-outreach`; the harness requires work in the isolated worktree. Restored the shared checkout to pristine (only the two router files; left the parallel-agent STATE.md untouched) and redid the deletion + verification inside the worktree, where the commit now lives.

**2. Compose container-name collision + missing env in worktree.** Running the test overlay from the worktree tried to create the base `db` service (hardcoded `container_name: outreach-platform-db`), colliding with the running shared container. Worked around by starting only `db-test` and running pytest with `--no-deps`. The worktree also lacked `.env` (base compose interpolates `${TELEGRAM_API_ID}` etc.), so a gitignored copy of the shared `.env` was placed in the worktree for compose interpolation. Neither affects the committed change.

**3. Full-suite failures are PRE-EXISTING (out of scope).** See `deferred-items.md`. The authoritative in-worktree full run is **939 passed / 1 failed / 1 skipped**; the single failure `tests/test_warmup_worker.py::test_restricted_sender_excluded` is a RED scaffold for an unimplemented feature ("restriction clause not added yet (WARM-14)"), fails in isolation, and is unrelated to router deletion. Runs against the newer shared-checkout base additionally show the documented `test_phase5_migration_017` pooled-connection cascade — also pre-existing (reproduced identically with the two files RESTORED) and out of scope.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Batch H complete. Per task constraints, NOT deployed — deploy will happen together with Batch G afterward.
- `app/routers/` no longer contains the two broken-import trip hazards for future greps/refactors.

## Self-Check: PASSED

- GONE: `app/routers/queue.py`
- GONE: `app/routers/proxy_pool.py`
- PRESENT (kept): `app/services/queue.py`
- Deletion commit `a4aebaf` exists (2 files, 427 deletions).
- SUMMARY.md, deferred-items.md, PLAN.md all present in the quick-task dir.

---
*Phase: 260704-bty-batch-h*
*Completed: 2026-07-04*
