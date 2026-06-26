---
phase: 13-even-pacing-across-sending-window-smooth-new-dialog-distribu
plan: 01
subsystem: testing
tags: [pytest, asyncio, postgres, queue-worker, pacing, tdd, red-scaffold, introspection]

# Dependency graph
requires:
  - phase: 12-per-campaign-daily-new-dialog-limit
    provides: "_process_next_for_sender new-dialog cap predicate + test helpers (_insert_pending_item, _seed_sent_dialog, _set_cap, _item_status, _run_worker_capturing_picked)"
provides:
  - "tests/test_queue_even_pacing.py — Wave-0 RED scaffold of 7 tests mapped 1:1 to PACE-01..07"
  - "Executable contract for the expected-by-now pacing model (window-start counter, structural interval floor, jitter, follow-up bypass, PROTECTED-constant guards)"
  - "_assert_pacing_predicate_wired introspection guard ensuring 13-02 binds :expected_now / :window_start_utc (not string-interpolated)"
affects: [13-02-pacing-implementation, queue-worker, even-pacing]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Deferred in-body imports for not-yet-existing symbols keep --collect-only clean while tests are genuinely RED"
    - "Source-introspection guard (inspect.getsource) to make behavioural integration tests RED for the RIGHT reason instead of coincidentally passing on the Phase 12 cap"

key-files:
  created:
    - tests/test_queue_even_pacing.py
  modified: []

key-decisions:
  - "Added _assert_pacing_predicate_wired() guard so the four behavioural integration tests (PACE-03/04/05/07) are genuinely RED against pre-13-02 code — without it they passed coincidentally (Phase 12 cap blocking, or the no-predicate path picking)"
  - "Phase 12 helpers copied verbatim (no behavioural edits); pace-numerator helper _count_since_window_start_sent added to assert the two-counter divergence (window-start vs trailing-24h)"

patterns-established:
  - "Wave-0 RED scaffold: collect-only clean + every test fails inside the body on a not-yet-implemented symbol/predicate, none skipped"

requirements-completed: [PACE-01, PACE-02, PACE-03, PACE-04, PACE-05, PACE-06, PACE-07]

# Metrics
duration: 6min
completed: 2026-06-26
---

# Phase 13 Plan 01: Even-Pacing RED Test Scaffold Summary

**Wave-0 `tests/test_queue_even_pacing.py` — 7 RED tests (PACE-01..07) encoding the expected-by-now pacing contract via the Phase 12 test analog, with deferred imports + source-introspection guards so collection is clean and every test is genuinely red against pre-13-02 code.**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-06-26T09:49:05Z
- **Completed:** 2026-06-26T09:55:03Z
- **Tasks:** 2
- **Files modified:** 1 (created)

## Accomplishments
- New `tests/test_queue_even_pacing.py` with 7 tests mapped 1:1 to PACE-01..07 (RESEARCH §Validation Architecture PACE→Test Map).
- Phase 12 helpers (`_insert_pending_item`, `_seed_sent_dialog`, `_set_cap`, `_item_status`, `_run_worker_capturing_picked`) reused verbatim so the file mirrors the proven Phase 12 pattern.
- Unit tests: PROTECTED-constant guard (8 empirical constants asserted unchanged) + injectable-`now` elapsed-fraction math across start/mid/pre/post/boundary/degenerate cases.
- Integration tests: expected-by-now gate, window-start vs trailing-24h counter divergence, structural interval floor (no crash), catch-up no-burst, follow-up bypass — each with `FOR UPDATE OF mq SKIP LOCKED` / `LIMIT 8` / `random.uniform` / `_check_rate_limits` introspection guards from the threat model.
- All 7 collect cleanly (0 errors) and are genuinely RED; pre-existing queue tests (`test_queue_new_dialog_limit.py`, `test_queue_per_campaign_hours.py`) remain GREEN (16 passed).

## Task Commits

Each task was committed atomically:

1. **Task 1: Scaffold unit tests (PACE-01, PACE-02)** - `924c19e` (test)
2. **Task 2: Integration RED tests (PACE-03..07)** - `0dcb535` (test)

**Plan metadata:** see final docs commit.

## Files Created/Modified
- `tests/test_queue_even_pacing.py` - 7 RED tests for PACE-01..07; copied Phase 12 helpers + new `_count_since_window_start_sent` and `_assert_pacing_predicate_wired` helpers.

## Decisions Made
- **Introspection guard for genuine RED (deviation, see below).** The four behavioural integration tests passed against the current code for the wrong reason (the Phase 12 cap coincidentally blocked, or the no-predicate path coincidentally picked). Added `_assert_pacing_predicate_wired()` asserting the SELECT binds `:expected_now` / `:window_start_utc` — present in PACE-03/04/05/07 — so they fail now (predicate absent) and only pass once 13-02 wires the bound predicate. This satisfies the plan/`<critical_test_rule>` requirement that all 7 are genuinely RED, not skipped, and aligns with the threat model's "bound, not interpolated" requirement.
- Phase 12 helpers copied byte-for-byte (no behavioural edits) per the plan's `<interfaces>`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Behavioural integration tests were not genuinely RED**
- **Found during:** Task 2 (PACE-03..07)
- **Issue:** With only behavioural assertions, `test_pacing_gate`, `test_pace_counter_window_start`, `test_interval_floor`, and `test_followup_bypasses_pacing` PASSED against the current pre-13-02 code — the Phase 12 cap (or the no-predicate single-item-per-call path) produced the same outcome. The plan requires all 7 tests genuinely RED (reach not-yet-implemented behaviour, none passing/skipped).
- **Fix:** Added module-level `_assert_pacing_predicate_wired()` (asserts `expected_now` and `window_start_utc` appear in `_process_next_for_sender` source) and called it at the start of those four tests. This mirrors the plan's own threat-model requirement that 13-02 BIND `:expected_now` / `:window_start_utc` rather than interpolate. `test_catchup_no_burst` was already RED on its deferred `PACE_JITTER_*` import.
- **Files modified:** tests/test_queue_even_pacing.py
- **Verification:** `pytest tests/test_queue_even_pacing.py -q` → 7 failed (all RED, 0 passed, 0 skipped); `--collect-only` → 7 collected, 0 errors; pre-existing queue tests → 16 passed.
- **Committed in:** 0dcb535 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug — test not genuinely RED).
**Impact on plan:** Necessary to satisfy the plan's acceptance criteria and `<critical_test_rule>` (genuine RED). No scope creep — the guard only encodes the already-specified 13-02 bind-param contract.

## Issues Encountered
None beyond the deviation above.

## Known Stubs
None — this is a test-only scaffold. The not-yet-implemented production symbols (`_window_elapsed_fraction`, `PACE_JITTER_LOW/HIGH`, the pacing predicate) are intentionally absent; they are implemented in plan 13-02, which is what turns these RED tests GREEN. This is the correct Wave-0 state, not a stub.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- 13-02 has a concrete `<automated>` target for every PACE requirement from the start (Nyquist rule satisfied).
- 13-02 implementation must: append `PACE_JITTER_LOW/HIGH` to the rate-config block; add `_window_elapsed_fraction(*, campaign_tz, work_hour_start, work_hour_end, now=None) -> (datetime, float)`; add the expected-by-now subquery beside the Phase 12 cap predicate in `_process_next_for_sender`, binding `:window_start_utc` / `:expected_now` and applying a `random.uniform(PACE_JITTER_LOW, PACE_JITTER_HIGH)` jitter, while preserving `FOR UPDATE OF mq SKIP LOCKED` and `LIMIT 8`.

## Self-Check: PASSED
- FOUND: tests/test_queue_even_pacing.py
- FOUND: .planning/phases/13-.../13-01-SUMMARY.md
- FOUND commit 924c19e (Task 1)
- FOUND commit 0dcb535 (Task 2)

---
*Phase: 13-even-pacing-across-sending-window-smooth-new-dialog-distribu*
*Completed: 2026-06-26*
