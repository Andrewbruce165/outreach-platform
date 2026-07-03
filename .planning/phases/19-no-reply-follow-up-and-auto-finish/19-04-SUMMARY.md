---
phase: 19-no-reply-follow-up-and-auto-finish
plan: 04
subsystem: api
tags: [follow-up, auto-finish, asyncio-worker, webhook, state-machine, postgres]

# Dependency graph
requires:
  - phase: 19-01
    provides: "conversations.status='no_reply' + pings_sent column, campaign follow-up columns (migration 045)"
  - phase: 19-02
    provides: "ai_engine.generate_followup_ping (provider-routed, tool-free ping generator) + campaign follow-up API fields"
  - phase: 19-03
    provides: "listener no_reply->active revert + queue pre-send D-17 follow-up guards"
provides:
  - "FollowUpWorker — timer-driven auto-finish-first / ping-else state machine"
  - "follow_up_tick_seconds config knob (default 300)"
  - "lifespan registration of follow_up_worker"
  - "no_reply reason marker on the auto-finish finish webhook (D-09)"
affects: [phase-19-05, follow-up-ui, campaign-lifecycle]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Background worker (asyncio tick + FOR UPDATE OF c SKIP LOCKED) modeled on CampaignEnqueueWorker"
    - "Auto-finish-first / ping-else evaluation anchored to last outbound message (derived lazily)"

key-files:
  created:
    - "app/services/follow_up.py"
  modified:
    - "app/config.py"
    - "app/main.py"
    - "app/services/webhook_notify.py"
    - "tests/conftest.py"

key-decisions:
  - "last_outbound_at derived lazily per tick via MAX(messages.created_at WHERE direction='outbound') — no stored column (RESEARCH Pattern 2)"
  - "Auto-finish evaluated BEFORE ping (D-08 whichever-comes-first); UPDATE guarded WHERE status IN ('active','no_reply') to avoid clobbering a reverted dialog"
  - "no_reply marker is carried on the existing 'finish' webhook via reason='no_reply' — no new payload field (additive, n8n-safe)"
  - "public tick() method (not _tick) so unit tests call it directly; _run loops tick()"
  - "D-16 pause semantics use wall-clock — no pause-duration compensation for v1 (RESEARCH Open Question 1), flagged in a comment"

patterns-established:
  - "FollowUpWorker: one AsyncSessionLocal per tick, per-conversation try/except with rollback so one bad row never kills the tick"
  - "Double-enqueue guard: skip when a pending ping row already exists for (campaign_id, recipient_phone, sender_id)"

requirements-completed: [NORP-04, NORP-06, NORP-09, NORP-10, NORP-11, NORP-12]

# Metrics
duration: ~90min
completed: 2026-07-03
---

# Phase 19 Plan 04: FollowUpWorker (No Reply Follow-Up + Auto-Finish) Summary

**FollowUpWorker drives the full no_reply→ping→auto-finish state machine — an asyncio tick that sweeps running follow-up-enabled campaigns, auto-finishes silent dialogs (finish webhook reason='no_reply') or enqueues a provider-generated ping to the owning sender, anchored to the last outbound message.**

## Performance

- **Duration:** ~90 min (dominated by full-suite test-isolation debugging)
- **Started:** 2026-07-03T08:37:56Z
- **Completed:** 2026-07-03T09:00:00Z (approx)
- **Tasks:** 3
- **Files modified:** 5 (1 created, 4 modified)

## Accomplishments
- `FollowUpWorker` (`app/services/follow_up.py`): eligible SELECT gates `running AND follow_up_enabled` campaigns + `active/no_reply` conversations (D-06/D-16) with `FOR UPDATE OF c SKIP LOCKED`; last-outbound anchor derived lazily (D-04/D-10).
- Auto-finish-first branch (D-08): flip `finished`, cancel pending pings, fire finish webhook `reason='no_reply'` (D-09); satisfies D-15 (toggle-enable past-threshold finishes immediately, no ping).
- Ping branch (D-02): flip `active→no_reply`, generate via `generate_followup_ping`, enqueue `kind='followup'` to the owning sender (D-13/D-14), increment `pings_sent`; skips restricted sender (D-14) + double-enqueue guard.
- `follow_up_tick_seconds` config knob (default 300) and lifespan register/stop of `follow_up_worker`.
- `no_reply` reason marker documented on the finish webhook path.

## Task Commits

1. **Task 1: config knob + no_reply webhook marker** - `c27b4fa` (feat)
2. **Task 2: FollowUpWorker tick state machine + test infra** - `b3e9063` (feat)
3. **Task 3: register FollowUpWorker in the lifespan** - `baf00fe` (feat)

## Files Created/Modified
- `app/services/follow_up.py` - FollowUpWorker class + module-level singleton (created)
- `app/config.py` - `follow_up_tick_seconds` Field (default 300, FOLLOW_UP_TICK_SECONDS)
- `app/main.py` - import + `follow_up_worker.start()` / `await follow_up_worker.stop()` in lifespan
- `app/services/webhook_notify.py` - documented `reason='no_reply'` as the auto-finish marker
- `tests/conftest.py` - `test_campaign_factory` follow-up fields; `test_conversation_factory` teardown of committed `no_reply` rows

## Decisions Made
See key-decisions frontmatter. Notably: last-outbound derived lazily (no new column); auto-finish before ping with a status-guarded UPDATE; the no_reply marker reuses the existing `reason` field.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Extended `test_campaign_factory` with follow-up fields**
- **Found during:** Task 2 (running the NORP tests)
- **Issue:** The merged RED tests pass `follow_up_enabled` / `follow_up_interval_hours` / `follow_up_max_pings` / `auto_finish_hours` (and `finish_webhook_url`/`webhook_url`) through `test_running_campaign_factory` → `test_campaign_factory`, which did not accept them → `TypeError`, so the four worker tests could not run.
- **Fix:** Added the five follow-up kwargs and their INSERT columns to `test_campaign_factory`.
- **Files modified:** tests/conftest.py
- **Verification:** `tests/test_follow_up.py` 12/12 green.
- **Committed in:** `b3e9063`

**2. [Rule 1 - Bug] Fixed shared-DB connection-pool poisoning from a committed `no_reply` conversation**
- **Found during:** Task 3 (full-suite run)
- **Issue:** `test_no_reply_status_allowed` (19-01) commits a `status='no_reply'` conversation into the shared, non-rolled-back test DB. Later, `test_phase5_migration_017::test_migration_017_idempotent_double_apply` re-applies migration 017's OLD `conversations_status_check` (which predates `no_reply` from migration 045) → `CheckViolationError`; the aborted transaction poisoned a pooled connection → cascade of "cannot use Connection.transaction() in a manually started transaction" (71 failed + 80 errors). This is a Phase-19 test-hygiene defect that only surfaces in the full suite (targeted `pytest tests/test_follow_up.py` stays green, which is why 19-01/02/03 didn't catch it).
- **Fix:** Added a teardown to `test_conversation_factory` that deletes ONLY its committed `no_reply` rows (plus their dependent `messages`). Scoped to `no_reply` on purpose — deleting all factory conversations would remove the leftover `active/finished` rows that count-based tests (`test_recontact`, `test_campaign_enqueue_worker`) rely on for re-contact dedup, which was verified to break them (+1/+2 enqueue miscounts).
- **Files modified:** tests/conftest.py
- **Verification:** `test_follow_up.py + test_phase5_migration_017.py` green; `test_campaign_enqueue_worker.py + test_recontact.py` green; full suite 939 passed / 1 skipped / 1 pre-existing WARM-14 failure.
- **Committed in:** `b3e9063`

---

**Total deviations:** 2 auto-fixed (1 blocking test-infra, 1 test-isolation bug)
**Impact on plan:** Both were required to run the plan's own verification (targeted + full suite). No production-code scope creep — the worker/config/lifespan/webhook changes match the plan exactly; the only extra edits are in `tests/conftest.py`.

## Issues Encountered
- **Stale worktree:** the execution worktree was behind main and lacked the 19-01/02/03 dependency work (generate_followup_ping, migration 045, listener/queue guards). Resolved by `git merge main` before starting.
- **Container-name / env for test-overlay:** running from the worktree conflicted with the running prod `outreach-platform-db` container and lacked `.env`. Resolved by running the overlay under `-p tg-outreach --env-file /root/apps/aimly/tg-outreach/.env` so the already-running prod db is reused and only the ephemeral `db-test` is created (tests hit `db-test` only, prod db untouched).

## Deferred Issues
- `tests/test_warmup_worker.py::test_restricted_sender_excluded` fails on the clean baseline (main, no Phase-19 changes) too — the known WARM-14 out-of-scope failure (project memory). Logged in `deferred-items.md`. Full suite otherwise GREEN.

## Known Stubs
None — no stubbed data paths introduced. `last_outbound_at` advances naturally when a ping/reply writes an outbound `messages` row; the worker does not fake it.

## User Setup Required
None - no external service configuration required. Optional env knob `FOLLOW_UP_TICK_SECONDS` (default 300).

## Next Phase Readiness
- Backend follow-up/auto-finish state machine complete and lifespan-registered. Phase 19 backend NORP tests green.
- **Deploy note:** both api AND listener must be rebuilt on deploy (`docker compose up -d --build api listener`) — the api hosts FollowUpWorker; the listener carries the Plan 19-03 cancel-on-reply hook.
- 19-05 (frontend/UI for follow-up settings) can proceed.

---
*Phase: 19-no-reply-follow-up-and-auto-finish*
*Completed: 2026-07-03*

## Self-Check: PASSED
All created/modified files verified present; all three task commits (c27b4fa, b3e9063, baf00fe) exist in git history.
