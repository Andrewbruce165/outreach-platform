---
phase: quick-260706-c1p
plan: 01
subsystem: api
tags: [rebalance, failover, campaign-enqueue, cold-backlog, pool-health, postgres, sqlalchemy]

# Dependency graph
requires:
  - phase: 08-pool-management-and-even-distribution
    provides: rebalance_on_attach even-split + eligible-pool filter + BATCH_CAP
  - phase: 09-cold-contact-failover
    provides: failover_cold_backlog (cold-pending predicate, per-row least-loaded spread, scheduled_at=NOW() reset, best-effort)
  - phase: 10-pool-health-and-restriction-audit
    provides: pool_health aggregate on CampaignResponse (_compute_pool_health)
provides:
  - "rebalance_on_attach full-evacuation branch: moves ALL cold-pending rows off ineligible senders onto the eligible pool with scheduled_at=NOW(), independent of fair-share (P>=1 recipient sufficient)"
  - "CampaignEnqueueWorker._sweep_stranded_cold_backlog: periodic re-run of failover for every ineligible sender still holding cold-pending backlog in a running campaign"
  - "pool_health.has_backup soft advisory (active >= 2)"
affects: [pool-management, campaign-lifecycle, failover, frontend-pool-badge]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Full evacuation (not fair-share) of ineligible-donor rows: claim under FOR UPDATE OF mq SKIP LOCKED + status guard, reset scheduled_at=NOW(), keep CCA in lock-step"
    - "Continuous invariant enforcement via periodic sweep at the start of the enqueue tick, wrapped so a sweep failure never aborts enqueue"
    - "Negated eligible-pool predicate to discover EVERY ineligible reason (not just spam_limited)"

key-files:
  created: []
  modified:
    - app/services/rebalance.py
    - app/services/campaign_enqueue.py
    - app/routers/campaigns.py
    - app/schemas/__init__.py
    - tests/test_rebalance.py
    - tests/test_campaign_enqueue_worker.py
    - tests/test_pool_health.py

key-decisions:
  - "Evacuation runs BEFORE fair-share and is independent of P; fair-share extracted verbatim into a nested _fair_share_backfill() so evacuation + backfill never double-move (return evacuated + backfill)"
  - "Sweep uses failover_cold_backlog(db=None) per sender (own session, own commit) — isolated from the enqueue transaction; runs at existing tick interval, no new worker/interval/schema"
  - "has_backup is a SOFT advisory only (locked 2026-07-06): NO blocking on attach/detach/start"

patterns-established:
  - "Ineligible-donor evacuation predicate: cold-pending AND sender_id NOT IN eligible pool → move + scheduled_at=NOW()"
  - "Sweep discovery: negation of the canonical eligible-pool filter to catch all ineligible reasons"

requirements-completed: [EVAC-01, EVAC-02, EVAC-03]

# Metrics
duration: ~20min
completed: 2026-07-06
---

# Quick Task 260706-c1p: Evacuate Cold-Pending Backlog Off Ineligible Senders Summary

**Cold-pending queue rows can no longer strand on a restricted/frozen sender while the campaign pool has an eligible sender — enforced at attach time (rebalance full-evacuation with scheduled_at reset) AND continuously (periodic enqueue-tick sweep), plus a soft pool_health.has_backup advisory.**

## Performance

- **Duration:** ~20 min (incl. Docker image builds)
- **Started:** 2026-07-06T09:44:00Z
- **Completed:** 2026-07-06T09:58:00Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments
- **EVAC-01/02:** `rebalance_on_attach` now fully evacuates every cold-pending row sitting on an ineligible (not-in-pool) sender onto the eligible pool, resetting `scheduled_at = NOW()` — closing the P<2 no-op, partial-share, and inherited-+24h-pause root causes. A single-frozen-sender campaign with a freshly-attached healthy sender moves 100% of its backlog.
- **EVAC-03:** `CampaignEnqueueWorker._sweep_stranded_cold_backlog` runs at the start of every enqueue tick, discovers every ineligible sender still holding cold-pending backlog in a running campaign (negated eligible-pool predicate → catches all ineligible reasons), and re-runs `failover_cold_backlog` per sender. This closes the "failover only fires inline at freeze, never re-runs" gap for the attach-before-freeze case.
- **Soft advisory:** `pool_health.has_backup = (active >= 2)` exposed on `CampaignResponse` for a frontend "no backup sender" nudge — no behavioural change to attach/detach/start.
- Both evacuation paths are idempotent (a repeat invocation moves 0 rows).

## Task Commits

Each task committed atomically (code only):

1. **Task 1: rebalance_on_attach full evacuation (EVAC-01/02)** — `600aa5d` (feat)
2. **Task 2: periodic sweep (EVAC-03)** — `9a53cbf` (feat)
3. **Task 3: pool_health.has_backup soft advisory** — `3acbfc0` (feat)

_TDD tasks (1 & 2): RED confirmed before implementation, then GREEN in the same task commit._

## Files Created/Modified
- `app/services/rebalance.py` — Added Step 1b full-evacuation branch (claim cold-pending rows on senders NOT in the eligible pool, move + `scheduled_at=NOW()` + CCA lock-step); extracted fair-share Steps 2-5 into nested `_fair_share_backfill()`; imports `_pick_least_loaded` from rotation.
- `app/services/campaign_enqueue.py` — Added `_sweep_stranded_cold_backlog()` method + call at start of `_tick` wrapped in try/except.
- `app/routers/campaigns.py` — `_compute_pool_health` derives `has_backup = active >= 2` (no extra query).
- `app/schemas/__init__.py` — Added `PoolHealth.has_backup: bool = False`.
- `tests/test_rebalance.py` — 3 new evacuation tests + copied `_freeze_sender`/`_pause_pending`/`_scheduled_at` helpers.
- `tests/test_campaign_enqueue_worker.py` — 2 new sweep tests + local helpers.
- `tests/test_pool_health.py` — Updated exact-dict assertion to include `has_backup`; added transition assertions for states (b)/(c).

## Decisions Made
- **Nested-function refactor over inline edits:** kept the Phase-8 fair-share logic verbatim inside `_fair_share_backfill()` and returned `evacuated + await _fair_share_backfill()`, so no `return 0` statements in the original code needed touching and idempotency is preserved by construction.
- **Sweep placement:** at the very start of `_tick`, before the campaign loop's `AsyncSessionLocal()`; uses its own session for discovery and `failover_cold_backlog(db=None)` per sender (own commit). No new interval/worker/schema.
- **has_backup semantics:** derived from the already-computed `active` count (`>= 2`), not a new query; advisory-only per the locked 2026-07-06 decision.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added `has_backup` to the PoolHealth schema**
- **Found during:** Task 3 (pool_health advisory)
- **Issue:** The plan listed only `app/routers/campaigns.py` for Task 3, but `PoolHealth` is a Pydantic model with explicit fields — a new field must be declared on the model or it is dropped from the serialized response, so the advisory would never reach the frontend.
- **Fix:** Added `has_backup: bool = False` to `PoolHealth` in `app/schemas/__init__.py`.
- **Files modified:** app/schemas/__init__.py
- **Verification:** `tests/test_pool_health.py` GREEN — `has_backup` now present in the API response dict.
- **Committed in:** `3acbfc0` (Task 3 commit)

**2. [Rule 1 - Bug] Updated exact-dict assertion in test_pool_health.py**
- **Found during:** Task 3 (pool_health advisory)
- **Issue:** `test_pool_health_states` asserted `pool_health` via exact dict equality; adding `has_backup` would break that assertion (the field is not in the plan's Task-3 files list).
- **Fix:** Added `"has_backup": True` to the state-(a) expected dict and added `has_backup` transition assertions to states (b) and (c).
- **Files modified:** tests/test_pool_health.py
- **Verification:** `tests/test_pool_health.py tests/test_pool_endpoints.py` GREEN (10 passed).
- **Committed in:** `3acbfc0` (Task 3 commit)

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 test correction) — both confined to Task 3 and necessary to ship the advisory field while keeping pool tests GREEN. No scope creep; no behavioural change to attach/detach/start.
**Impact on plan:** Minimal — additive field + test alignment only.

## Issues Encountered
- **Test-overlay container-name conflict in the worktree:** running `docker compose ... run --rm api pytest` from the per-agent worktree tried to recreate the prod `db` (fixed `container_name: outreach-platform-db`, project `tg-outreach`), which conflicted with the live prod DB container. Resolved by starting the ephemeral `db-test` in the worktree's own compose project and running pytest with `--no-deps` (so the prod `db` dependency is never started/recreated), plus `--env-file /root/apps/aimly/tg-outreach/.env` so compose interpolation could satisfy Settings (worktree has no `.env`). The test overlay still overrode `DATABASE_URL` to `db-test`, so the prod DB was never touched.

## Known Stubs
None — all changes are wired end-to-end (evacuation and sweep move real rows; `has_backup` is computed from the live pool aggregate).

## User Setup Required
None — no external service configuration, no migration, no env change.

## Next Phase Readiness
- Invariant now enforced at both attach time and continuously; safe to deploy with `docker compose up -d --build api` (listener unaffected, but a rebuild of both is harmless).
- The frontend can consume `pool_health.has_backup` to render the no-backup nudge (openapi/types regeneration is a separate frontend-handoff step, not part of this backend task).

---
*Phase: quick-260706-c1p*
*Completed: 2026-07-06*

## Self-Check: PASSED

- All 7 modified files present on disk; SUMMARY.md present.
- All 3 task commits present in git history (`600aa5d`, `9a53cbf`, `3acbfc0`).
- Targeted verification: 54 passed (test_rebalance, test_failover, test_campaign_enqueue_worker, test_pool_health, test_pool_endpoints, test_campaign_lifecycle_fixes) via the test-overlay.
