---
phase: 14
plan: 02
subsystem: contact-resolution
tags: [worker, selection-gate, rate-limit, durable-cap, brownfield, wave-2]
requires:
  - "Phase 10 senders.restriction_status / restricted_until / lifecycle_status"
  - "14-01 Settings.contact_check_burst_cap / contact_check_daily_cap knobs"
  - "14-01 Wave-0 RED scaffold (selection-skip / mobile-first / cap / rotation tests)"
  - "ContactCheckWorker._tick claim-queue (Phase 2)"
provides:
  - "Restriction/lifecycle selection gate in _tick JOIN LATERAL (spam_limited/paused checker never selected)"
  - "Mobile-first claim ordering (+79… drained before landlines)"
  - "Burst-cap (effective claim LIMIT = contact_check_burst_cap)"
  - "Durable per-checker daily-cap (COUNT over contacts_cache writes today)"
  - "Durable cooldown gate (restricted_until <= NOW())"
affects:
  - "Wave 3 (14-03): confidence/source write, suspect rollback, importContacts fallback — still RED, untouched here"
tech-stack:
  added: []
  patterns:
    - "Correlated subquery daily-cap counted from a durable DB source (contacts_cache), never an in-memory counter (Pitfall 5)"
    - "Selection-time exclusion (JOIN LATERAL WHERE) so a degraded checker never reaches _apply_results"
    - "Env-knob as ceiling only: CONTACT_CHECK_BATCH_SIZE can lower but never uncap past burst_cap"
key-files:
  created: []
  modified:
    - "app/services/contact_check_worker.py (selection gate + mobile-first + burst/daily/cooldown cap)"
decisions:
  - "CONTACT_CHECK_BATCH_SIZE became an OPTIONAL override (None when unset); effective claim LIMIT defaults to settings.contact_check_burst_cap and an explicit env value is min()'d with burst_cap so it can only LOWER the cap"
  - "Daily-cap is a correlated COUNT(*) over contacts_cache (cc.sender_id = senders.id AND cc.updated_at >= date_trunc('day', now())) inside the JOIN LATERAL — durable across api restart, no process-local state"
  - "Cooldown handled in selection (restricted_until <= NOW()) in addition to the restriction_status='none' gate, so a checker resting on a future cooldown is skipped even if its status was cleared early"
metrics:
  tasks: 2
  files: 1
  commits: 2
  completed: 2026-06-26
---

# Phase 14 Plan 02: Selection Gate + Volume Guards Summary

Closed the root-cause hole (RESV-05/D-11) and added the volume guards (RESV-02/D-10) + mobile-first ordering (RESV-04/D-08) to the existing `ContactCheckWorker._tick()` claim-queue — without rebuilding it. A degraded checker flagged `spam_limited`/`paused`/cooling-down is now excluded at selection time, so the semantic restriction flag actually stops the worker (no more nuking `auth_status`), and no single checker can over-resolve into a shadow-ban.

## What Was Built

### Task 1 — RESV-05 selection gate + RESV-04 mobile-first ordering
Extended the `_tick()` JOIN LATERAL (`contact_check_worker.py`) with two new disqualifiers alongside the existing `role='checker' AND auth_status='ok'`:
- `AND restriction_status = 'none'` — a `spam_limited`/`frozen` checker is never picked.
- `AND lifecycle_status <> 'paused'` — a paused checker is never picked.

This is the exact hole that let `sender-8428118140` keep returning false `not_registered` results: the old selection filtered only `auth_status`, so a correct `spam_limited` flag did nothing. Now the flag (written by Plan 03's probe) stops the worker at selection — the degraded checker never even reaches `_apply_results`.

Replaced `ORDER BY c.created_at ASC` with `ORDER BY (c.phone LIKE '+79%') DESC, c.created_at ASC` so mobiles (~50% live) drain before landlines (RESV-04/D-08). The `FOR UPDATE OF c SKIP LOCKED`, the 5-minute `tg_checked_at` claim window, and the no-`'processing'`-status invariant are preserved verbatim. Module docstring updated to describe the gate.

### Task 2 — RESV-02 burst-cap + durable daily-cap + cooldown gate
- **Burst-cap:** the per-batch claim `LIMIT` now defaults to `settings.contact_check_burst_cap` (30). The legacy `CONTACT_CHECK_BATCH_SIZE` env became an optional override (sentinel `None` when unset) that is `min()`'d with `burst_cap` — it can lower the cap but never uncap the worker past the ~45–50 empirical throttle onset.
- **Durable daily-cap:** added a correlated subquery in the JOIN LATERAL — `(SELECT COUNT(*) FROM contacts_cache cc WHERE cc.sender_id = senders.id AND cc.updated_at >= date_trunc('day', now())) < :daily_cap` (bound from `settings.contact_check_daily_cap`). Counted from a durable DB source (today's `contacts_cache` writes), so a container restart cannot reset the counter (Pitfall 5 — never an in-memory dict).
- **Cooldown gate:** added `AND (restricted_until IS NULL OR restricted_until <= NOW())` so a checker resting on a future `restricted_until` is skipped even if its `restriction_status` was cleared early. This is what turns the N=1 `test_rotation_n1_pauses` stub GREEN — a single resting checker → `_tick` processes nothing, no false `not_registered` (D-04).

`queue.py` send constants were NOT touched (CLAUDE.md guard — `CONTACT_CHECK_*` is a separate knob set).

## Verification Results

All commands run ONLY via the test-overlay under a dedicated compose project (`wt14_02`) with the ephemeral `db-test` and `api --no-deps`, so the running prod `outreach-platform-db` was never (re)created or touched. Torn down with `down` (never `down -v`).

- Task 1 — `test_selection_skips_restricted`, `test_selection_skips_paused`, `test_mobile_first_order`: **3 passed**.
- Task 2 — `test_checker_cap.py` (`test_burst_cap`, `test_daily_cap_durable`), `test_checker_pool.py::test_rotation_n1_pauses`: **3 passed**.
- Full suite: **762 passed, 6 failed, 1 skipped** (161s). Passing count rose from the 756 Plan-01 baseline to 762 — the 6 tests this plan owns flipped RED→GREEN with no regression.
- The 6 remaining failures are the intentional Wave-3 (Plan 14-03) RED stubs and carry no overlap with this plan's scope: `test_import_fallback_and_cleanup`, `test_rotation_picks_eligible` (needs `select_eligible_checkers`, a Wave-3 helper), the 3 `test_checker_probe` tests, and `test_confidence_written`.

Acceptance-criteria greps confirmed: selection SQL contains `restriction_status = 'none'`, `lifecycle_status <> 'paused'`, `ORDER BY (c.phone LIKE '+79%') DESC`, `date_trunc('day', now())` over `contacts_cache` filtered by `sender_id`, and `restricted_until IS NULL OR restricted_until <= NOW()`; `FOR UPDATE OF c SKIP LOCKED` + the `tg_checked_at` claim window are unchanged; no `'processing'` tg_status value introduced (the two `'processing'` matches are in comments documenting the CHECK constraint); no in-memory daily counter.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Worktree had no `.env` for compose interpolation + no running `db-test`**
- **Found during:** RED baseline run (Task 1 setup).
- **Issue:** The base `docker-compose.yml` needs `.env` for `${VAR}` interpolation (gitignored, absent in a fresh worktree) and the test `api` `depends_on: db-test`; `--no-deps` (required to avoid recreating prod `db`) skips bringing `db-test` up, so the first run failed with `socket.gaierror: Temporary failure in name resolution` (db-test host unresolved).
- **Fix:** Copied the gitignored prod `.env` into the worktree purely for compose interpolation (the overlay forces `DATABASE_URL=…/outreach_test`, and `.env` is gitignored — never committed), and brought up only the ephemeral `db-test` service before running `api --no-deps`. Same operational pattern as Plan 14-01.
- **Files modified:** none committed (`.env` gitignored).
- **Commit:** n/a (operational).

### Scope notes (not deviations)
- The plan's `__init__` instruction said "set `self.batch_size = settings.contact_check_burst_cap` (keep CONTACT_CHECK_BATCH_SIZE as a back-compat override if present, but the effective cap must be ≤ burst_cap)". The legacy module default was `5`, which is `< burst_cap (30)` and would have kept `test_burst_cap` RED (it asserts `total_resolved == cap`). Implemented exactly as specified: `CONTACT_CHECK_BATCH_SIZE` is now an optional env override (default → `burst_cap`), and an explicit value is `min()`'d with `burst_cap`. This satisfies both the "back-compat override" and "effective cap ≤ burst_cap" clauses and makes the cap the active per-batch budget.

## Known Stubs
None introduced by this plan. The 6 still-RED tests are pre-existing Wave-0 scaffold owned by Plan 14-03 (confidence/source write, suspect rollback, importContacts fallback, `select_eligible_checkers` rotation helper) — out of scope here and tracked in 14-VALIDATION.md.

## Commits
- `c364d90` feat(14-02): RESV-05 restriction/lifecycle selection gate + RESV-04 mobile-first order
- `19f2ae7` feat(14-02): RESV-02 burst-cap + durable daily-cap + cooldown gate

## Self-Check: PASSED
- app/services/contact_check_worker.py — FOUND (355 lines, > min_lines 312)
- Commits c364d90, 19f2ae7 — FOUND
- Selection SQL contains `restriction_status = 'none'` + `lifecycle_status <> 'paused'` + `date_trunc('day', now())` + `restricted_until <= NOW()` + `ORDER BY (c.phone LIKE '+79%') DESC` — OK
- queue.py untouched — OK
- 6 target tests GREEN; 762/756 baseline up, no regression — OK
