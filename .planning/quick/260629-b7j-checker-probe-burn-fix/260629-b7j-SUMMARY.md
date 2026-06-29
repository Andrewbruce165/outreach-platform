---
phase: quick-260629-b7j
plan: 01
subsystem: contact-resolution / checker-pool
tags: [checker, probe, throttle, backoff, phase-14]
requires:
  - "Phase 14 14-05 inline throttle finalization (_is_throttle_signal / _maybe_degrade_on_signal)"
  - "Phase 14 14-07 benign post-batch rest (senders.checker_rest_until, migration 035)"
provides:
  - "Rest-aware + budget-gated + interval-throttled control-probe (_probe_cycle)"
  - "Escalating per-checker backoff (senders.checker_trip_count, migration 036)"
  - "Two config knobs: contact_check_probe_interval_seconds, contact_check_max_backoff_seconds"
affects:
  - "app/services/contact_check_worker.py (probe + degrade + recover paths)"
tech-stack:
  added: []
  patterns:
    - "In-memory per-checker throttle state on the singleton (mirrors _consecutive_misses)"
    - "SQL selection gate mirroring (probe WHERE copies the _tick LATERAL eligibility)"
    - "Durable counter column for backoff history surviving api restart"
key-files:
  created:
    - migrations/036_checker_probe_burn.sql
    - tests/test_checker_probe_burn.py
  modified:
    - app/config.py
    - app/services/contact_check_worker.py
    - app/models/__init__.py
decisions:
  - "checker_trip_count added to the Sender ORM model (mirrors 14-07 checker_rest_until) so create_all/tests see the column — conftest applies a hardcoded migration list that stops at 031 and does NOT glob 036."
metrics:
  duration: "~12 min"
  completed: "2026-06-29"
  tasks: 3
  files: 5
requirements: [PROBE-01, PROBE-02, PROBE-03, PROBE-04]
---

# quick-260629-b7j: Checker probe-burn fix Summary

Stopped the contact-check health-probe (`_probe_cycle`) from burning the checker pool: it now honors the 14-07 post-batch rest, is gated by the per-checker daily budget, fires at most once per `contact_check_probe_interval_seconds` (default 15min) instead of every ~5s poll tick, and a repeatedly-tripping checker backs off exponentially (capped at 6h) instead of auto-recovering every fixed ~15min only to re-trip. Finalization rules (14-05 suspect-rollback) are unchanged — this is purely a throughput/longevity fix layered on top.

## What changed

### Task 1 — Migration 036 + two config knobs (commit 13f19b4)
- `migrations/036_checker_probe_burn.sql`: idempotent `ALTER TABLE senders ADD COLUMN IF NOT EXISTS checker_trip_count INTEGER NOT NULL DEFAULT 0`. Header documents it as the per-checker consecutive trip counter (distinct from `restriction_status`/`restricted_until`; persists trip history across api restarts so backoff survives a redeploy; not a restriction in itself).
- `app/config.py`: `contact_check_probe_interval_seconds` (default 900s) and `contact_check_max_backoff_seconds` (default 6h), both env-overridable.

### Task 2 — TDD: rest/budget/interval-gated probe + escalating backoff (commits ecfd274 RED, 4b2d022 GREEN)
RED test module `tests/test_checker_probe_burn.py` written first (8 tests), then implementation:
- **PROBE-01 + PROBE-03** — `_probe_cycle` WHERE clause now includes the two predicates already used by the `_tick` LATERAL: `(checker_rest_until IS NULL OR checker_rest_until <= NOW())` and the durable `daily_cap` subquery (today's `contacts_cache` writes). A resting or over-budget checker is no longer probed.
- **PROBE-02** — new in-memory `self._last_probe_at: dict[str, datetime]`; `_probe_cycle` skips any checker probed within `contact_check_probe_interval_seconds` and stamps the timestamp when it does probe.
- **PROBE-04** — `_flag_checker_degraded` bumps `checker_trip_count` (`UPDATE ... RETURNING` in the same TX as the event row + status update) and sets `restricted_until = NOW() + min(base_cooldown * 2^(trip-1), max_backoff)`.
- **PROBE-04 reset** — `_recover_checkers` clean branch also sets `checker_trip_count = 0`.
- Sender ORM model gains `checker_trip_count` so `create_all`/tests have the column (see Deviations).

### Task 3 — Full regression (commit 865b880)
Full suite via test-overlay: **786 passed, 1 skipped, 0 failures**. One test (`test_probe_skips_resting_checker`) was scoped to assert the specific checker was not probed (rather than zero probes globally), because `_probe_cycle` selects across all workspaces and leaked-but-eligible checkers from other tests otherwise flip a global assertion.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added `checker_trip_count` to the Sender ORM model**
- **Found during:** Task 2 GREEN (escalation test raised `UndefinedColumnError: column "checker_trip_count" does not exist`).
- **Issue:** The test DB schema is built by `conftest._setup_database` via ORM `create_all` plus a HARDCODED migration list that stops at 031 and does NOT glob — so migration 036 never applies in tests. The 14-07 `checker_rest_until` column works in tests only because it is also declared on the Sender ORM model.
- **Fix:** Added `checker_trip_count = Column(Integer, nullable=False, server_default='0')` to the Sender model, exactly mirroring how 035's `checker_rest_until` is declared. Migration 036 remains the durable prod-DB path; the ORM column is the test/`create_all` path.
- **Files modified:** `app/models/__init__.py`
- **Commit:** 4b2d022 (with the worker impl)

**2. [Rule 1 - Test correctness] Scoped the rest-gate assertion to the target checker**
- **Found during:** Task 3 (full-suite run; passed in isolation, failed in the full suite).
- **Issue:** `_probe_cycle` selects checkers across ALL workspaces; committed checkers leaked by earlier tests made a global `assert_not_awaited()` fail.
- **Fix:** Assert the specific `checker_id` is absent from the probed ids instead of asserting zero probes globally.
- **Files modified:** `tests/test_checker_probe_burn.py`
- **Commit:** 865b880

## §8 Safety invariants — preserved
- `_apply_results` / `_maybe_degrade_on_signal` / `_is_throttle_signal` untouched; suspect `not_registered` → `pending` rollback intact (regression test `test_suspect_batch_still_rolls_back_not_registered` + the full `tests/test_checker_probe.py` pass).
- The 49 control rows are not touched by any change.
- `queue.py` send rate-limits and working-hours untouched.
- Migration 036 is idempotent (`ADD COLUMN IF NOT EXISTS`).
- All tests run ONLY via the test-overlay (ephemeral tmpfs db-test); never `down -v`.

## Verification
- `pytest tests/test_checker_probe_burn.py tests/test_checker_probe.py` → 21 passed.
- Full suite → 786 passed, 1 skipped.
- Probe load per cycling checker drops from ~4,267 batches/day toward ≤ ~96/day (15min interval) — the dominant burn contributor removed.

## Commits
- 13f19b4 — feat: migration 036 + config knobs
- ecfd274 — test: RED tests
- 4b2d022 — feat: probe gate + escalating backoff (impl)
- 865b880 — test: scope rest-gate assertion

## Self-Check: PASSED
