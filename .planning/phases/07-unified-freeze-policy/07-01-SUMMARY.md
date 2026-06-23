---
phase: 07-unified-freeze-policy
plan: 01
subsystem: api
tags: [telegram, antispam, rate-limiting, listener, rotation, postgres, freeze-policy]

# Dependency graph
requires:
  - phase: 260619-frz (quick task)
    provides: "senders.restriction_status / restricted_until columns (migration 028), PEER_FLOOD/ACCOUNT_FROZEN soft-restriction writers in queue.py, reconcile sweep in listener.py, pre-send skip at queue.py:401"
  - phase: 260622-gxt (quick task)
    provides: "SpamBot self-check in-memory registry + is_spambot_selfcheck early-return guard in _handle_antispam_signal"
provides:
  - "Antispam signal now PAUSES the sender (auto-resumable) instead of terminally failing the queue"
  - "Antispam signal flags sender restriction_status='spam_limited' + restricted_until (mirrors PEER_FLOOD)"
  - "Antispam signal no longer disables ai_enabled — replies in established dialogues keep flowing"
  - "Rotation excludes restriction_status != 'none' senders from cold-contact assignment"
  - "Regression coverage: cancel-path freeze contract + rotation restricted-sender skip + FRZ-05 worker skip"
affects: [08-pool-management, 09-cold-contact-failover, 10-pool-visibility]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Unified soft-restriction write: antispam path converged onto the queue.py PEER_FLOOD pattern (pause pending +24h, flag spam_limited, separate AsyncSessionLocal + single commit)"
    - "Frozen-precedence guard: spam_limited writes carry AND restriction_status <> 'frozen' so a soft signal cannot downgrade a hard freeze"

key-files:
  created: []
  modified:
    - app/services/listener.py
    - app/services/rotation.py
    - tests/test_spambot_selfcheck.py
    - tests/test_rotation_campaign.py

key-decisions:
  - "Pause scoped to status='pending' ONLY (dropped the old 'processing' clause) to match PEER_FLOOD and avoid the in-flight lost-update race"
  - "senders UPDATE guarded with AND restriction_status <> 'frozen' to preserve frozen-precedence (threat #3)"
  - "NO migration added — migration 028 already ships restriction_status / restricted_until"

patterns-established:
  - "Antispam handler = exact mirror of the queue.py soft-restriction writer; the listener and queue now share one freeze contract"
  - "self-check is_spambot_selfcheck early-return stays the FIRST statement to prevent the reconcile reflag loop"

requirements-completed: [FRZ-01, FRZ-02, FRZ-03, FRZ-04, FRZ-05]

# Metrics
duration: 14min
completed: 2026-06-23
---

# Phase 07 Plan 01: Unified Freeze Policy Summary

**Antispam signals now pause-and-flag the sender (auto-resumable via the reconcile sweep) instead of terminally killing the queue, stop silencing AI in established dialogues, and restricted senders are excluded from cold-contact rotation.**

## Performance

- **Duration:** ~14 min
- **Started:** 2026-06-23T08:56Z
- **Completed:** 2026-06-23T09:10Z
- **Tasks:** 3
- **Files modified:** 4 (2 source, 2 test)

## Accomplishments

- Rewrote `_handle_antispam_signal` to mirror the working PEER_FLOOD soft-restriction branch: pending queue items are paused (`scheduled_at` +24h, status stays `'pending'`) so the existing reconcile sweep auto-resumes them — fixing the b7cc7d06 incident where 37 contacts were terminally `failed` on a single antispam event with no recovery (FRZ-01, FRZ-02).
- Deleted the `ai_enabled = false` block — an antispam soft-limit no longer silences replies in established conversations (Telegram does not block them) (FRZ-03).
- Added a one-line rotation candidate filter (`AND s.restriction_status = 'none'`) so a `spam_limited` sender — which stays `active`/`auth-ok` by design — is never handed a new cold contact (FRZ-04).
- Locked the new behaviour: flipped the cancel-path test to the freeze contract, added a rotation restricted-sender regression, and captured the already-passing FRZ-05 worker pre-send skip.

## Task Commits

Each task was committed atomically:

1. **Task 1: Rewrite `_handle_antispam_signal` to pause+flag** - `89fd9ab` (feat)
2. **Task 2: Exclude restricted senders from rotation candidate filter** - `acca40c` (feat)
3. **Task 3: Flip cancel-path test + rotation regression + FRZ-05 assert** - `46a45f6` (test)

**Plan metadata:** (final docs commit — this SUMMARY + STATE + ROADMAP)

## Files Created/Modified

- `app/services/listener.py` — `_handle_antispam_signal` rewritten: self-check early-return preserved first; `ai_enabled=false` block deleted; terminal-fail replaced by PEER_FLOOD pause+flag mirror (`spam_limited` + `restricted_until`, `<> 'frozen'` guard); docstring + closing log updated.
- `app/services/rotation.py` — `get_or_assign_sender` candidate WHERE clause gains `AND s.restriction_status = 'none'`.
- `tests/test_spambot_selfcheck.py` — `test_antispam_guard_cancels_when_no_selfcheck` renamed to `test_antispam_guard_pauses_and_flags_when_no_selfcheck`; asserts `pending` + `scheduled_at > NOW()` + `ai_enabled is True` + `restriction_status == 'spam_limited'`.
- `tests/test_rotation_campaign.py` — new `test_rotation_skips_restricted_senders` (clone of the inactive-sender test, swapping the disqualifier to `restriction_status='spam_limited'`).

## Decisions Made

- **NO migration added.** Migration 028 (quick task 260619-frz) already ships `restriction_status` / `restricted_until`. `migrations/` is unchanged by this plan (verified clean before and after).
- **Pause scoped to `status='pending'` only.** Dropped the old `status IN ('pending','processing')` clause — matches the PEER_FLOOD reference exactly and avoids the in-flight lost-update race against a message that is mid-send.
- **`AND restriction_status <> 'frozen'` guard on the senders UPDATE.** A soft antispam signal must not downgrade a sender that is already hard-`frozen` (threat #3, frozen-precedence). Benign either way (frozen accounts are not sending) but the explicit guard removes the race.
- Empirical 24h `pause_until` (`timedelta(hours=24)`) and the 4/20/150 rate constants were copied verbatim / left untouched per CLAUDE.md hard rules.

## Deviations from Plan

None - plan executed exactly as written. No Rule 1-4 deviations were needed; the antispam handler rewrite, rotation filter, and tests all matched the plan and pattern map.

## Issues Encountered

**Full-suite pre-existing failures (out of scope).** Running the complete suite reports 62 failed / 590 passed / 20 errors, but every failing file is unrelated to this plan's change set (e.g. `test_migration_014`, `test_onboarding_reauth`, `test_phase5_inbox`, `test_queue_per_campaign_hours`, `test_warmup_*`). Root causes sampled: asyncpg `cannot insert multiple commands into a prepared statement` at fixture setup, and `conversations_status_check` CHECK-constraint violations in the ephemeral test DB — both test-infrastructure / schema-drift issues, not product regressions. They reproduce independently of the antispam/rotation change and were logged to `.planning/phases/07-unified-freeze-policy/deferred-items.md` per the executor scope boundary; NOT fixed under this plan.

The three files this plan targets pass **21/21** in isolation under the test-overlay:
`tests/test_spambot_selfcheck.py tests/test_rotation_campaign.py tests/test_sender_restriction.py`.

## Known Stubs

None. No hardcoded empty values, placeholder text, or unwired data sources introduced.

## User Setup Required

None - no external service configuration required. After deploy, rebuild the listener so the rewritten handler takes effect (`docker compose up -d --build listener`) and the api if rotation is exercised there (`docker compose up -d --build api`).

## Next Phase Readiness

- The listener and queue now share one freeze contract — Phase 08 (pool management & even distribution) and Phase 09 (cold-contact failover) can rely on `restriction_status` being the single source of truth for "this sender is paused/limited", both at rotation (cold) and pre-send (queue) gates.
- Open carry-over (documented, unchanged from 260622-gxt): the self-check in-memory registry covers the listener process (reconcile sweep) but NOT the manual `/spambot-check` API endpoint in the api process. Not a blocker for this phase.

## Self-Check: PASSED

All declared files exist (4 modified + SUMMARY) and all 3 task commits (`89fd9ab`, `acca40c`, `46a45f6`) are present in history.

---
*Phase: 07-unified-freeze-policy*
*Completed: 2026-06-23*
