---
phase: 09-cold-contact-failover
plan: 02
subsystem: api
tags: [failover, queue, postgres, sender-pool, freeze, telethon, rebalance]

# Dependency graph
requires:
  - phase: 09-01
    provides: tests/test_failover.py (9 RED unit stubs) + conftest with_message flag
  - phase: 08-pool-management-and-even-distribution
    provides: rebalance.py concurrency template + _COLD_PENDING_PREDICATE + _pick_least_loaded
  - phase: 07-unified-freeze-policy
    provides: the three soft-restriction freeze paths (PEER_FLOOD / ACCOUNT_FROZEN / antispam)
provides:
  - app/services/failover.py::failover_cold_backlog — per-row even-spread reassignment off a frozen sender
  - failover wired inline into all three freeze paths (queue.py x2, listener.py x1)
  - 3 FAIL-02 call-site integration tests + 9 unit tests (all GREEN)
affects: [09-03, pool-visibility, sender-pool-resilience]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dual session-ownership helper: db=None opens+commits own session; db passed = transaction-neutral (caller commits)"
    - "Per-row even spread via _pick_least_loaded called per claimed row (NOT a single set-based move)"
    - "scheduled_at=NOW() divergence: failover sheds the +24h freeze pause so moved rows are sendable immediately"

key-files:
  created:
    - app/services/failover.py
  modified:
    - app/services/queue.py
    - app/services/listener.py
    - tests/test_failover.py

key-decisions:
  - "Selection via _pick_least_loaded over a restriction_status='none' candidate set — NEVER rotation.get_or_assign_sender (its stale-CCA short-circuit ignores restriction_status and would return the just-frozen sender — Pitfall 1)"
  - "Cold predicate widens rebalance's: item_type='message', never-sent guard IN ('sent','processing'), and has-message JOIN so an EMPTY conversation is still cold (D-05)"
  - "Moved rows reset scheduled_at=NOW() (Pitfall 2 / EDIT 1) — the +24h freeze pause must not travel with the row"
  - "queue.py callers pass db=None (own committed session, after the restriction flag is already committed); listener antispam passes the session (pause+flag+failover in ONE commit, flag written BEFORE failover per Pitfall 3)"
  - "No migration (FAIL-09) — pure reassignment over existing tables"

patterns-established:
  - "Failover is additive at every freeze site: restriction flag persisted/visible FIRST, then failover_cold_backlog — never touching rate-limiter constants, FloodWait retry, or the +24h pause"
  - "Worker-safe claim: FOR UPDATE OF mq SKIP LOCKED + status='pending' makes a second call move 0 (idempotent)"

requirements-completed: [FAIL-01, FAIL-02, FAIL-03, FAIL-04, FAIL-05, FAIL-06, FAIL-07, FAIL-08, FAIL-09]

# Metrics
duration: 35min
completed: 2026-06-24
---

# Phase 9 Plan 02: Cold-Contact Failover Service and Call Sites Summary

**`failover_cold_backlog` reassigns a frozen sender's cold-pending backlog onto healthy pool senders (per-row even spread, scheduled_at=NOW), wired inline into all three freeze paths — closing the b7cc7d06 24h-stall gap while engaged dialogs stay put.**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-06-24T09:08:00Z
- **Completed:** 2026-06-24T09:43:00Z
- **Tasks:** 3
- **Files modified:** 4 (1 created, 3 modified)

## Accomplishments
- New `app/services/failover.py::failover_cold_backlog(frozen_sender_id, db=None)` mirroring rebalance.py's concurrency discipline, with per-row even spread and lock-step queue+CCA updates resetting `scheduled_at=NOW()`.
- All three freeze paths now call it inline after the restriction flag is persisted: queue.py PEER_FLOOD + ACCOUNT_FROZEN (db=None, own session) and listener.py `_handle_antispam_signal` (session passed, single-commit transaction-neutral).
- 12/12 `tests/test_failover.py` green — the 9 unit tests from 09-01 plus 3 new FAIL-02 call-site integration tests that drive the REAL freeze paths (mocked Telegram for the queue worker; direct handler call for antispam).
- Verified FAIL-09: no migration added; rate-limiter constants (4/20/150), FloodWait retry, and the +24h pause logic untouched (additive call only).

## Task Commits

Each task was committed atomically:

1. **Task 1: Create app/services/failover.py::failover_cold_backlog** - `9644a4d` (feat)
2. **Task 2: Wire failover into the three freeze call sites** - `b06b34b` (feat)
3. **Task 3: Add FAIL-02 call-site integration tests** - `ae54017` (test)

_Task 1 was a TDD GREEN task against the 9 RED unit stubs delivered by 09-01; the RED commit lives in 09-01, so this plan has a single feat commit for the implementation._

## Files Created/Modified
- `app/services/failover.py` (created) - `failover_cold_backlog` + `_failover` core + `_COLD_PENDING_PREDICATE`. Resolves the healthy pool per campaign (`restriction_status='none'`), claims cold-pending rows under `FOR UPDATE OF mq SKIP LOCKED`, picks a receiver per row via `_pick_least_loaded`, moves queue+CCA in lock-step with `scheduled_at=NOW()`, logs COUNT/UUID-only.
- `app/services/queue.py` (modified) - two additive `await failover_cold_backlog(sender.id)` calls, one after the PEER_FLOOD `db2.commit()` and one after the ACCOUNT_FROZEN `db2.commit()`.
- `app/services/listener.py` (modified) - one additive `await failover_cold_backlog(sender_id, session)` inside `_handle_antispam_signal` after the `spam_limited` flag UPDATE and before `session.commit()`.
- `tests/test_failover.py` (modified) - appended 3 FAIL-02 integration tests + a `_FakeTelegram` stand-in and seed helpers.

## Decisions Made
- **Selection never uses `rotation.get_or_assign_sender`** (Pitfall 1): its stale-CCA short-circuit checks only `lifecycle_status='active' AND auth_status='ok'` (ignores `restriction_status`) and would hand the backlog back to the just-frozen sender. Selection is `_pick_least_loaded` over a `restriction_status='none'` candidate set.
- **Cold predicate widened from rebalance's** (D-06): added `item_type='message'`, widened the never-sent guard to `IN ('sent','processing')`, and replaced the "any conversation" guard with a has-message JOIN so an EMPTY conversation is still cold and movable (D-05) while a has-message dialog stays on the frozen sender (FAIL-05).
- **`scheduled_at=NOW()` on every moved row** (Pitfall 2 / EDIT 1): sheds the +24h freeze pause so the healthy receiver can send immediately.
- **Per-caller session ownership:** queue.py callers commit the restriction flag first, then call with `db=None` (helper owns its session); the listener antispam path passes its session so pause+flag+failover land in one atomic commit with the flag written first (Pitfall 3 — candidate filter must see `restriction_status != 'none'`).

## Deviations from Plan

None - plan executed exactly as written. (One in-test assertion refinement during Task 3: the PEER_FLOOD/ACCOUNT_FROZEN trigger row is failed→retried back to `pending` on the frozen sender by `_fail_item`, which is correct existing behaviour — so the integration-test assertions were scoped to the cold backlog phones rather than an aggregate per-sender pending count. This is a test-authoring detail, not a code deviation.)

## Issues Encountered

- **Worktree container-name collision (environmental):** the prod compose stack pins static `container_name` (outreach-platform-db/api/listener) which collide with the LIVE prod containers when the test-overlay runs from inside the worktree. Resolved with an executor-only `docker-compose.wt.yml` overlay (renamed container_names + repointed `api.depends_on` at `db-test` only) and an isolated `-p wt_a66bbcc2_test` project name. The ephemeral stack was torn down with `stop`/`rm` (NEVER `down -v`); live prod containers verified untouched and the overlay file removed (never committed).
- **Settings env vars in worktree:** the worktree has no `.env`, so compose interpolated `TELEGRAM_API_ID` etc. to empty strings and `conftest`'s `os.environ.setdefault` could not override them → pydantic ValidationError. Resolved by passing the required Settings env vars via `-e` on the `run` command (test-overlay already overrides DATABASE_URL to `outreach_test`).

## Pre-existing Full-Suite Failures (NOT a regression)

The full suite reports **63 failed + 20 errors** (620 passed). This is **pre-existing and not caused by this plan** — proven by running the full suite at the clean base commit `4de6583` with `tests/test_failover.py` excluded: identical `63 failed, 608 passed, 20 errors`. This plan added exactly +12 passing tests and changed zero pre-existing failures; none of the failing files were touched here. Root causes are schema/API-contract drift in send/onboarding/migration_014/phase5 tests. Logged to `.planning/phases/09-cold-contact-failover/deferred-items.md` for a separate triage. The 09-02 acceptance gate (all of `tests/test_failover.py` green) is met.

## Known Stubs

None. The failover service is fully wired into all three live freeze paths; no placeholder data or unwired components.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `failover_cold_backlog` is live in all three freeze paths; cold backlog no longer stalls 24h on a frozen sender.
- Ready for 09-03 / pool-visibility. No blockers introduced.
- Recommendation for the verifier: confirm the 63-failed/20-error baseline (it predates Phase 09) rather than attributing it to 09-02.

---
*Phase: 09-cold-contact-failover*
*Completed: 2026-06-24*

## Self-Check: PASSED

- FOUND: app/services/failover.py
- FOUND: .planning/phases/09-cold-contact-failover/09-02-SUMMARY.md
- FOUND: .planning/phases/09-cold-contact-failover/deferred-items.md
- FOUND commits: 9644a4d (Task 1), b06b34b (Task 2), ae54017 (Task 3), b2fc5c6 (docs)
