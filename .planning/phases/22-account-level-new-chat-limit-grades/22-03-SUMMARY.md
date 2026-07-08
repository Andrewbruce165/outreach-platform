---
phase: 22-account-level-new-chat-limit-grades
plan: 03
subsystem: api
tags: [queue, rate-limiting, grade-ladder, postgres, sqlalchemy, pacing]

# Dependency graph
requires:
  - phase: 22-01
    provides: sender grade columns (current_level), sender_grade_settings table, grade_ladder resolver (load_ladder/budget_for_level)
provides:
  - Sender-wide daily new-dialog cap driven by the account grade budget (not per-campaign)
  - Sender-wide follow-up dedup — a phone contacted in ANY campaign is a known peer
  - Pacing numerator switched from campaigns.max_new_dialogs_per_day to the account budget
  - Removal of the daily-message cap (rate_per_day) from _check_rate_limits (D-04)
affects: [22-04, 22-05, 22-06, warmup budget share, campaign priority reserve]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-tick account budget resolved once in Python via grade_ladder (mirrors expected_now bind pattern)"
    - "Sender-wide predicate keying (prior.sender_id = mq.sender_id) replaces per-campaign keying"

key-files:
  created: []
  modified:
    - app/services/queue.py
    - tests/test_queue_new_dialog_limit.py
    - tests/test_queue_even_pacing.py
    - tests/test_send.py

key-decisions:
  - "Cap RHS and pace numerator both read the account grade budget; campaigns.max_new_dialogs_per_day is no longer read (dropped in 22-06)"
  - "Follow-up dedup is sender-wide across all campaigns (D-13); budget is spent once sender-wide (D-01/D-06)"
  - "Daily-message cap removed from the backend gate; per-minute/per-hour/unique-contacts/interval-fatigue floors untouched (D-04)"
  - "Test _set_cap helper now drives the workspace grade budget (level1_chats_per_day) as the authoritative cap source"

patterns-established:
  - "Divergent-value test: set account budget and the legacy campaign column to different values to prove which one the code reads"
  - "23h-ago seed technique isolates the trailing-24h cap from the today-window pace numerator"

requirements-completed: [D-01, D-05, D-06, D-13, D-03, D-04]

# Metrics
duration: 20min
completed: 2026-07-08
---

# Phase 22 Plan 03: Account-Budget Sender-Wide New-Dialog Gate Summary

**The daily new-dialog gate moved from a per-campaign cap keyed on `campaigns.max_new_dialogs_per_day` to a single sender-wide budget resolved from the workspace grade ladder, and the legacy daily-message cap was removed from `_check_rate_limits`.**

## Performance

- **Duration:** 20 min
- **Started:** 2026-07-08T17:33Z
- **Completed:** 2026-07-08T17:53Z
- **Tasks:** 3 completed
- **Files modified:** 4 (1 source, 3 test)

## Accomplishments
- Rewrote `_process_next_for_sender`: budget resolved once per tick via `grade_ladder.load_ladder`/`budget_for_level` from the sender's `current_level` + `workspace_id`; the three pick-SELECT subqueries (EXISTS follow-up, cap COUNT, pace COUNT) are now sender-wide, dropping every `campaign_id` filter; the cap RHS is the `:account_budget` bind and `expected_now` uses the account budget as its numerator.
- Removed the `rate_per_day` daily-message cap from `_check_rate_limits` (SELECT column, `max_per_day` binding, the trailing-24h gate, and the now-dead `one_day_ago`), leaving the empirical per-minute/per-hour/unique-contacts and interval-fatigue floors intact.
- Extended queue/pacing/rate tests: sender-wide cross-campaign follow-up, shared budget across two campaigns (blocks at 3+2=5, allows under total), pacing numerator = account budget (divergent 10-vs-1000 proof), and daily-cap removal (160/24h not blocked; per-hour still gates).

## Task Commits

Each task was committed atomically:

1. **Task 1: Account-budget + sender-wide rewrite of _process_next_for_sender** - `6731a14` (feat)
2. **Task 2: Remove rate_per_day gate from _check_rate_limits** - `dc58980` (feat)
3. **Task 3: Extend queue/pacing/rate tests for account-wide behavior** - `d1f128e` (test)

_Note: Task 1 was tdd-tagged; the queue rewrite and the coupled `_set_cap` helper change ship together because the existing per-campaign cap tests must stay green under the new budget source._

## Files Created/Modified
- `app/services/queue.py` - Sender-wide account-budget new-dialog cap + pacing; daily-message cap removed from `_check_rate_limits`.
- `tests/test_queue_new_dialog_limit.py` - `_set_cap` now sets the workspace grade budget; added sender-wide dedup + shared-budget-across-campaigns tests.
- `tests/test_queue_even_pacing.py` - `_set_cap` updated identically; added pacing-numerator-is-account-budget behavioral + source guard tests.
- `tests/test_send.py` - Added daily-cap-removal rate test (per-hour still gates).

## Decisions Made
- **`_set_cap` drives the account budget:** the existing cap tests set `campaigns.max_new_dialogs_per_day`, which the rewrite no longer reads. The helper now upserts `sender_grade_settings.level1_chats_per_day` for the campaign's workspace (test senders default to `current_level=1`), keeping the legacy column write as harmless documentation. This is the minimal change that keeps the inherited per-campaign tests meaningful under sender-wide semantics.
- **`if camp_row is None: return` moved inside the pre-query `async with`:** the budget resolution (`load_ladder`) reuses that session, so the early return stays inside the context manager (session closes cleanly).

## Deviations from Plan

None - plan executed exactly as written. The `_set_cap` helper update is an explicit part of Task 1's contract (its verify runs the existing cap tests, which require the budget to come from the grade settings), not an out-of-scope change.

## Issues Encountered
- **Parallel-worktree docker collision:** `docker compose run api` from the worktree tried to create the prod-named `outreach-platform-db` container (hardcoded `container_name`), conflicting with the running prod db. Resolved by starting only the unnamed `db-test` service (`up -d db-test`) and running the api with `--no-deps`, so no prod-named container is created. The worktree also lacked the git-ignored `.env` needed for `${TELEGRAM_API_ID}` interpolation — copied from the main checkout for the test run and removed afterward. Ephemeral `db-test` torn down after the run (never `down -v` — that would wipe the prod volume).

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- The queue enforcement core is account-budget-driven; `campaigns.max_new_dialogs_per_day` still physically exists (safe intermediate state) and is dropped in 22-06.
- 22-04 (sender API) and 22-05 (warmup budget) can rely on `grade_ladder` + `current_level` being the single budget source in the queue path.
- No blockers.

## Self-Check: PASSED

---
*Phase: 22-account-level-new-chat-limit-grades*
*Completed: 2026-07-08*
