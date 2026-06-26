---
phase: 13-even-pacing-across-sending-window-smooth-new-dialog-distribu
plan: 02
subsystem: queue-worker
tags: [queue-worker, pacing, even-pacing, postgres, zoneinfo, jitter, tdd-green, single-file]

# Dependency graph
requires:
  - phase: 13-even-pacing (plan 01)
    provides: "tests/test_queue_even_pacing.py — 7 RED tests (PACE-01..07) + _assert_pacing_predicate_wired introspection guard"
  - phase: 12-per-campaign-daily-new-dialog-limit
    provides: "_process_next_for_sender candidate SELECT + new-dialog cap predicate (the shape this plan extends)"
provides:
  - "Even-pacing: new cold dialogs released evenly across the campaign's daily window via an expected-by-now predicate (D-05) counted from today's window start (D-06)"
  - "PACE_JITTER_LOW/HIGH constants + _window_elapsed_fraction pure helper + the pacing subquery in _process_next_for_sender (binds :window_start_utc / :expected_now)"
affects: [queue-worker, sending-pace, phase-14]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Python-computed window math (zoneinfo/DST) bound into SQL as a single :expected_now numeric + :window_start_utc timestamp — keeps the tz source of truth in tested Python (Phase 4 D-15 idiom)"
    - "Correlated COUNT(DISTINCT recipient_phone) pacing subquery ANDed beside the Phase 12 cap inside the new-dialog branch — no CTE, preserves FOR UPDATE OF mq SKIP LOCKED"
    - "Structural interval clamp (no numeric max()): the PROTECTED base 20-55s gate stays the floor, the pacing predicate is the ceiling"

key-files:
  created:
    - .planning/phases/13-even-pacing-across-sending-window-smooth-new-dialog-distribu/13-02-SUMMARY.md
  modified:
    - app/services/queue.py
    - tests/test_queue_new_dialog_limit.py

key-decisions:
  - "expected_now = max_new_dialogs_per_day * elapsed_fraction * random.uniform(PACE_JITTER_LOW, PACE_JITTER_HIGH), compared raw (no floor/ceil) — jitter blurs the boundary, PG compares int < numeric fine (RESEARCH OQ#2)"
  - "Pre-query targets the campaign of the next eligible pending item (priority DESC, created_at ASC LIMIT 1); conservative defaults (window_start=now, expected=0) on no-campaign so nothing is paced through"
  - "Fixed the pre-existing Phase 12 test_new_dialog_allowed_under_cap to seed the prior dialog 23h ago (before today's UTC-midnight window start) so it stays a pure cap test decoupled from pacing — assertion unchanged (two-counter divergence, D-06)"

requirements-completed: [PACE-01, PACE-02, PACE-03, PACE-04, PACE-05, PACE-06, PACE-07]

# Metrics
duration: 8min
completed: 2026-06-26
---

# Phase 13 Plan 02: Even-Pacing Implementation (GREEN) Summary

**Implemented even pacing entirely inside `app/services/queue.py`: an "expected-by-now" predicate (`count_since_window_start < max_new_dialogs_per_day × elapsed_fraction × jitter`) gates new cold dialogs across the campaign's daily window, beside the untouched Phase 12 cap and follow-up bypass — turning the 13-01 RED tests GREEN with the full suite staying green (756 passed).**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-06-26T09:57:41Z
- **Completed:** 2026-06-26T10:06Z
- **Tasks:** 3
- **Files modified:** 2 (queue.py + one Phase 12 regression-fix in tests)

## Accomplishments
- `PACE_JITTER_LOW = 0.75` / `PACE_JITTER_HIGH = 1.25` appended to the rate-config block (D-08) — no PROTECTED constant touched.
- `_window_elapsed_fraction(*, campaign_tz, work_hour_start, work_hour_end, now=None) -> (datetime, float)` pure helper next to `_campaign_in_working_window`: raw window width (D-01), `window_start_utc` floor (D-06), clamped `[0,1]`, injectable `now`, zero-width and invalid-tz safe (no crash).
- Pre-query in `_process_next_for_sender` fetches the next eligible item's campaign window and computes `expected_now` in Python (jitter applied fresh each call, D-08).
- Pacing subquery ANDed beside the Phase 12 cap inside the new-dialog branch, counting `status='sent'` since `:window_start_utc` (D-06, distinct from the cap's trailing-24h, Pitfall 1). `:window_start_utc` / `:expected_now` passed strictly as binds.
- Follow-up `EXISTS prior sent` branch unchanged → follow-ups bypass pacing entirely (D-07/D-10); `_check_rate_limits` byte-identical (D-07/D-09).
- `LIMIT 8`, `FOR UPDATE OF mq SKIP LOCKED`, and the Python working-window post-filter preserved; no CTE, no numeric `max(target, base)` clamp (Pattern 3 — structural floor).
- All 7 PACE-01..07 GREEN; full suite GREEN (756 passed, 1 skipped).

## Task Commits

Each task committed atomically (staging only this plan's files):

1. **Task 1: PACE_JITTER constants + _window_elapsed_fraction helper (PACE-01, PACE-02)** - `c70b5c5` (feat)
2. **Task 2: expected-by-now pacing predicate in the candidate SELECT (PACE-03..07) + Phase 12 regression fix** - `8dd407c` (feat)
3. **Task 3: full-suite regression** - no code change required beyond Tasks 1-2 (verification only; committed within Task 2)

**Plan metadata:** see final docs commit.

## Files Created/Modified
- `app/services/queue.py` — PACE_JITTER constants, `_window_elapsed_fraction` helper, the pre-query + pacing subquery + binds in `_process_next_for_sender`. The ONLY `app/` source changed (D-09: no migration/model/router/openapi/UI).
- `tests/test_queue_new_dialog_limit.py` — `test_new_dialog_allowed_under_cap` seeding changed (prior dialog 23h ago instead of NOW) so it remains a pure Phase 12 cap test, decoupled from the new Phase 13 pace gate. Cap assertion unchanged.

## Decisions Made
- **Raw `count < expected_now` comparison (no floor/ceil)** — RESEARCH OQ#2: jitter already blurs the eligibility boundary; PostgreSQL compares `int < numeric` directly.
- **Single pre-query targets the next-eligible item's campaign** — a sender's queued items belong to its attached campaign in practice; if a sender ever spans multiple running campaigns the pace uses that item's campaign window (per-(sender,campaign) isolation already holds from Phase 12). Documented in the source comment.
- **Conservative no-campaign defaults** (`window_start=now`, `expected=0`) → if nothing eligible, the pre-query returns early; if a campaign resolves to expected≈0 (e.g. zero-width window / 0 limit), no new dialog is picked, no crash (D-03).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Phase 12 `test_new_dialog_allowed_under_cap` regressed against the new pacing gate**
- **Found during:** Task 3 (full-suite regression; surfaced earlier in the Task 2 Phase-12 regression run).
- **Issue:** The test seeded 1 prior dialog with `finished_at=NOW()` (inside today's window) and asserted a 2nd new dialog is selectable under `cap=2`. With pacing added, `expected_now = 2 × elapsed_fraction × jitter` and `pace_count = 1`; at small wall-clock fractions `1 < expected_now` is false → the new dialog is (correctly) paced out, so the test failed. This is genuine, correct pacing behaviour, not a `queue.py` bug — and weakening pacing would break the new PACE tests.
- **Fix:** Re-seeded the prior dialog 23h ago (always before today's UTC-midnight window start for a full-day window) so it still counts toward the trailing-24h cap (`_count_in_window_sent == 1`, under cap=2) but NOT toward the window-start pace numerator (pace_count=0). The fresh dialog is then pace-eligible at any fraction. This exactly mirrors the two-counter divergence the new PACE-04 test exercises, and isolates the cap as the only variable — the test's stated intent. Phase 12 cap assertion is unchanged.
- **Files modified:** `tests/test_queue_new_dialog_limit.py`
- **Verification:** `pytest tests/test_queue_new_dialog_limit.py tests/test_queue_per_campaign_hours.py tests/test_queue_even_pacing.py` → 23 passed; full suite → 756 passed, 1 skipped.
- **Committed in:** `8dd407c` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 test-isolation fix forced by correct new pacing behaviour).
**Impact on plan:** No scope creep — the fix decouples a pre-existing cap test from the orthogonal pace gate without altering its Phase 12 assertion; it does NOT weaken any new PACE test and changes no production behaviour.

## Issues Encountered
None beyond the deviation above.

## Known Stubs
None — all logic is wired and exercised. No new config surface (D-09): no DB columns, API fields, UI, or openapi regen; even pacing is always-on and auto-derived from the campaign window + `max_new_dialogs_per_day`.

## User Setup Required
None — no external service configuration. The change is live in the queue worker on the next `docker compose up -d --build api && docker compose up -d --build listener` deploy.

## Self-Check: PASSED
- FOUND: app/services/queue.py (PACE_JITTER_LOW/HIGH + _window_elapsed_fraction + :window_start_utc/:expected_now binds)
- FOUND: tests/test_queue_even_pacing.py (7 tests GREEN)
- FOUND: commit c70b5c5 (Task 1)
- FOUND: commit 8dd407c (Task 2)
- FOUND: full suite GREEN (756 passed, 1 skipped)
- CONFIRMED: only app/services/queue.py changed under app/ across the two task commits (D-09)
- CONFIRMED: _check_rate_limits and PROTECTED constants byte-identical (PACE-01/PACE-07 introspection guards pass)

---
*Phase: 13-even-pacing-across-sending-window-smooth-new-dialog-distribu*
*Completed: 2026-06-26*
