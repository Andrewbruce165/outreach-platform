---
phase: quick-260706-fw7
plan: 01
subsystem: infra
tags: [config, spambot, restriction, queue, peer-flood, reconcile]

# Dependency graph
requires:
  - phase: 07-unified-freeze-policy
    provides: PEER_FLOOD soft-restriction path (spam_limited flag + 24h queue pause)
  - phase: 10-account-health-visibility
    provides: record_restriction_event audit log + restriction reconcile tick
provides:
  - "restriction_recheck_interval_seconds default flipped 6h -> 1h"
  - "Regression tests locking the 1h recheck window + matching audit recheck_at"
affects: [restriction-reconcile, spambot-recheck, account-uptime]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Source-shape guard tests via inspect.getsource(QueueWorker) for the PEER_FLOOD branch"

key-files:
  created: []
  modified:
    - app/config.py
    - tests/test_sender_restriction.py

key-decisions:
  - "Shrink post-spam-restriction @SpamBot recheck window 6h -> 1h so accounts freed after ~1h resume within the 60-75 min reconcile window instead of idling for 5 wasted hours"
  - "Description string corrected: check_spambot DOES parse a quoted release date (parse_spambot_limit_until), which takes precedence over the fixed interval"

patterns-established:
  - "Config-default + source-shape assertion pattern: flip a single knob, then pin the consuming code path (queue.py PEER_FLOOD) with inspect.getsource substring guards"

requirements-completed: [QUICK-260706-fw7]

# Metrics
duration: 5min
completed: 2026-07-06
---

# Phase quick-260706-fw7 Plan 01: SpamBot Recheck Window 6h → 1h Summary

**`restriction_recheck_interval_seconds` default flipped `6*60*60 → 1*60*60` so a PEER_FLOOD-restricted account stamps `senders.restricted_until = now+1h` (audit event carrying the same recheck_at), letting the 15-min reconcile sweep re-poll @SpamBot and resume it inside a 60–75 min window instead of holding it the full synthetic 6h.**

## Performance

- **Duration:** 5 min
- **Started:** 2026-07-06T11:31:38Z
- **Completed:** 2026-07-06T11:36:44Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Flipped the config default `6 * 60 * 60 → 1 * 60 * 60` (`app/config.py`) — the effective recheck delay after a spam-limit hit, since the `RESTRICTION_RECHECK_INTERVAL` env alias is unset anywhere.
- Corrected the now-stale description: dropped "check_spambot does not expose limit_until, so this is a fixed delay" (false since SpamBot's quoted release date is parsed via `parse_spambot_limit_until` and takes precedence).
- Added 2 regression tests: one pins the 1h knob value (and asserts the 15-min sweep knob is untouched), one is a source-shape guard on the `QueueWorker` PEER_FLOOD branch (recheck_at derived from the knob, stamped on `senders.restricted_until`, same recheck_at handed to `record_restriction_event`, 24h queue pause independent).

## Task Commits

Each task was committed atomically:

1. **Task 1: Flip restriction recheck default 6h → 1h + fix description** - `2bf7db4` (feat)
2. **Task 2: Regression test — PEER_FLOOD stamps +1h recheck with matching audit event** - `76bc543` (test)

**Plan metadata:** committed separately (docs: complete plan) with SUMMARY.md + STATE.md.

## Files Created/Modified
- `app/config.py` - `restriction_recheck_interval_seconds` default `6*60*60 → 1*60*60`; description rewritten to state "recheck via SpamBot after 1h" + SpamBot quoted-release-date precedence. `restriction_reconcile_interval_seconds` (15-min sweep) left at `15 * 60`.
- `tests/test_sender_restriction.py` - Added section 5 with `test_recheck_interval_default_is_one_hour` and `test_peer_flood_sets_one_hour_recheck_with_matching_audit`.

## Decisions Made
- None beyond the plan — followed the plan's two surgical changes and the two prescribed tests verbatim.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- **Test-overlay container conflict from the worktree.** Running the documented `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` from the isolated worktree failed twice: (1) the base `api` service `depends_on: db` (fixed `container_name: outreach-platform-db`) collided with the already-running prod db because the worktree's compose project name (`agent-…`) differs from prod's (`tg-outreach`); (2) once the project name was aligned, `${TELEGRAM_API_ID}` and friends interpolated to empty strings because the worktree has no `.env`, tripping Pydantic `int` validation. Resolved without any prod risk by running `docker compose -p tg-outreach --env-file /root/apps/aimly/tg-outreach/.env …` — this reuses the healthy prod db container (identical config, not recreated) and supplies only compose-level `${...}` interpolation values. The overlay's literal `DATABASE_URL` (→ `db-test`) still wins for the api container, so tests ran against the ephemeral tmpfs `db-test`, never prod. Prod `api`/`listener`/`db` containers remained up and untouched; the ephemeral `db-test` container was removed afterward (never `down`/`down -v`).

## Verification
- `grep` confirms `app/config.py:136` reads `default=1 * 60 * 60`; description carries "takes precedence over this interval"; `restriction_reconcile_interval_seconds` still `default=15 * 60`.
- `tests/test_sender_restriction.py` — **13 passed** via the test-overlay (11 pre-existing incl. the SpamBot quoted-release-date precedence test `test_restriction_tick_uses_spambot_release_date`, + 2 new fw7 tests).
- Broader recommended run `test_sender_restriction.py + test_restriction_audit.py` — **28 passed**.

## User Setup Required
None - no external service configuration required.

**Deploy is a MANUAL post-merge step for the user (NOT performed by this executor):**
`cd /root/apps/aimly/tg-outreach && docker compose up -d --build api listener`.

## Next Phase Readiness
- Config change and tests are green and committed. Ready for merge + manual deploy.
- No blockers.

## Self-Check: PASSED

- FOUND: `app/config.py` (default=1 * 60 * 60; precedence wording; 15-min sweep intact)
- FOUND: `tests/test_sender_restriction.py`
- FOUND: `.planning/quick/260706-fw7-spambot-6-1/260706-fw7-SUMMARY.md`
- FOUND commit `2bf7db4` (Task 1, feat)
- FOUND commit `76bc543` (Task 2, test)

---
*Phase: quick-260706-fw7*
*Completed: 2026-07-06*
