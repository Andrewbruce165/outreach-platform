---
phase: quick-260703-rm3
plan: 01
subsystem: checker
tags: [telethon, floodwait, contacts-api, resolve, session-auth, worker, resilience]

# Dependency graph
requires:
  - phase: quick-260703-j25
    provides: Batch A — live-only probe_control, live-only _is_throttle_signal, NULL cache provenance
  - phase: 14-reliable-contact-resolution
    provides: checker pool, health-probe, suspect-rollback, checker_rest_until (mig 035), checker_trip_count (mig 036)
  - phase: 17-sender-side-resolve-ladder
    provides: username capture, import fallback, confidence-gated cache
provides:
  - FloodWait inline cap (60s) so one checker cannot freeze the single-coroutine ContactCheckWorker
  - dead/unauthorized/banned checker session classification (auth_status flip BY ID + SessionAuthError)
  - empty-ImportContacts address-book cleanup via DeleteByPhonesRequest (both cleanups in finally)
  - empty-control-set inline degrade is REST-ONLY (never permanent spam_limited)
  - recovery early-return on empty control sample (never aborts other checkers mid-loop)
  - invalid_phone → tg_status='error' finalization (previously-unreachable branch)
  - deterministic LATERAL checker rotation (ORDER BY checker_rest_until NULLS FIRST, id)
affects: [checker, contact-resolution, worker-hardening]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Mirror TelegramService.get_client auth classification in CheckerService._get_client, but UPDATE by sender id (WR-14 — slug not globally unique)"
    - "Inline resource caps (FloodWait 60s) with durable degrade for long waits, not blocking sleeps"
    - "Cleanup in finally guarded by a completion flag so a flood/failed op fires no extra API call"

key-files:
  created:
    - tests/test_checker_resilience_batch_b.py
    - .planning/quick/260703-rm3-b-wr-05-wr-06-wr-08-wr-07-in-02-in-03/deferred-items.md
  modified:
    - app/services/checker.py
    - app/services/contact_check_worker.py

key-decisions:
  - "WR-08 empty control-set → REST-ONLY degrade (checker_rest_until), NOT spam_limited — a spam_limited flag would be unrecoverable without a control set to re-probe from (FIXPLAN open decision #4)"
  - "WR-05 caps only the inline sleep + returns a partial batch (flood_wait_hit=True); the FloodWait retry/reschedule contract and empirical rate constants are untouched"
  - "WR-06 flags auth_status BY sender id, not slug (WR-14 evidence); probe_control call site left with sender_id=None so the probe never writes"

patterns-established:
  - "Auth-gate a dead checker via the LATERAL selection gate (auth_status='ok'); back off transient failures via checker_rest_until in the _tick except branch"

requirements-completed: [WR-05, WR-06, WR-07, WR-08, IN-02, IN-03]

# Metrics
duration: 29min
completed: 2026-07-03
---

# Quick 260703-rm3 (Batch B): Checker + Worker Resilience Summary

**Six checker/worker resilience fixes (no schema change): FloodWait capped inline at 60s, dead-session auth classification by id, empty-import DeleteByPhones cleanup in a finally, empty-control-set rest-only degrade, invalid_phone→error finalization, and deterministic NULLS-FIRST checker rotation.**

## Performance

- **Duration:** 29 min
- **Started:** 2026-07-03T20:07:40Z
- **Completed:** 2026-07-03T20:36:56Z
- **Tasks:** 4
- **Files modified:** 2 source + 1 new test file + 1 deferred-items note

## Accomplishments

- **WR-05:** `_FLOOD_WAIT_INLINE_CAP=60` applied in all three inline FloodWait handlers (`_check_phones_locked`, `_check_usernames_locked`, `probe_control`) — a multi-hour raised FloodWait can no longer freeze the single-coroutine worker; it returns a partial batch (`flood_wait_hit=True`) and the checker is parked by the durable degrade path.
- **WR-06:** `CheckerService._get_client` now classifies auth/ban/unauthorized exactly like `TelegramService.get_client`, flipping `senders.auth_status` **by id** (new `_flag_checker_auth`) + raising `SessionAuthError`; the `_tick` LATERAL gate (`auth_status='ok'`) then excludes the dead checker, closing the 5s hot loop. Both `_tick` batch except branches also back off via `_rest_checker` for transient/frozen failures.
- **WR-07:** the ImportContacts fallback moves both cleanups into a `finally` guarded on `import_completed`; an empty import now removes the saved phone via `DeleteByPhonesRequest` (the shadow-ban accelerator the old code left uncleaned), and a flood/failed import fires no extra contacts-API call.
- **WR-08:** `_recover_checkers` computes the control sample once before the loop and early-returns with a WARNING on an empty sample (never aborting the remaining checkers mid-loop); an inline throttle signal with an empty `_CONTROL_SET` degrades **rest-only** (`checker_rest_until` + ERROR log) instead of the permanent-because-unrecoverable `spam_limited`.
- **IN-02:** `PhoneNumberInvalidError` now carries `{"error": "invalid_phone"}` through `check_phones` (skipping the cache write); `_apply_results` finalizes such a contact as `tg_status='error'` — the previously-unreachable error branch now fires.
- **IN-03:** the `_tick` JOIN-LATERAL subquery orders by `checker_rest_until NULLS FIRST, id` for deterministic rotation.

## Task Commits

1. **Task 1: checker resolve-path resilience (WR-05, WR-07, IN-02)** — `cd44d47` (fix)
2. **Task 2: checker dead-session classification (WR-06 checker side)** — `cf1ca6b` (fix)
3. **Task 3: worker resilience (WR-08, IN-03, WR-06 _tick backoff)** — `9fc08ce` (fix)
4. **Task 4: IN-02 consumer test + deferred-items** — `193e654` (test)

## Files Created/Modified

- `app/services/checker.py` — `_FLOOD_WAIT_INLINE_CAP` + capped sleeps (WR-05); `_flag_checker_auth` + rewritten `_get_client` auth classification + threaded sender_id/slug at both resolve call sites (WR-06); `DeleteByPhonesRequest` import + import-fallback cleanup in a `finally` (WR-07); `invalid_phone` tag + cache-skip + error threading (IN-02).
- `app/services/contact_check_worker.py` — LATERAL `ORDER BY checker_rest_until NULLS FIRST, id` (IN-03); `_recover_checkers` sample-before-loop + empty-sample WARN early-return, `_maybe_degrade_on_signal` empty-control-set rest-only branch, `_rest_checker(seconds=)` override (WR-08); `_rest_checker` backoff in both `_tick` except branches (WR-06).
- `tests/test_checker_resilience_batch_b.py` — 14 tests covering WR-05/WR-06/WR-07/WR-08/IN-02/IN-03.
- `.planning/quick/260703-rm3-b-.../deferred-items.md` — documents the pre-existing full-suite cascade.

## Decisions Made

- Followed the plan as specified. Empty-control-set degrade is rest-only (FIXPLAN open decision #4 recommendation), and WR-06 keys auth flips on sender id (WR-14 evidence).

## Deviations from Plan

**None functionally** — all six findings implemented exactly per the REVIEW/FIXPLAN. Two minor test-authoring adjustments (Rule 1, correctness of the test itself, not the product):

**1. [Rule 1 - Bug] Test stubs `decrypt_session` to `""`, not a fake string**
- **Found during:** Task 2 (WR-06 tests)
- **Issue:** `StringSession("decrypted-stub")` raises `ValueError: Not a valid string`; the plan's suggested stub decrypt value is not a valid StringSession payload.
- **Fix:** stub `decrypt_session` to return `""` (a valid empty StringSession) so `_get_client` reaches the auth-classification path under test.
- **Files modified:** tests/test_checker_resilience_batch_b.py
- **Committed in:** cf1ca6b

**2. [Rule 1 - Bug] Empty-control-set test patches the MODULE global `_CONTROL_SET`**
- **Found during:** Task 3 (WR-08 rest-only degrade test)
- **Issue:** the plan's snippet `monkeypatch.setattr(w, "_CONTROL_SET", [])` targets the worker instance, but `_maybe_degrade_on_signal` reads the MODULE global `_CONTROL_SET` — an instance attr would not take effect.
- **Fix:** `monkeypatch.setattr("app.services.contact_check_worker._CONTROL_SET", [])`.
- **Files modified:** tests/test_checker_resilience_batch_b.py
- **Committed in:** 9fc08ce

---

**Total deviations:** 2 test-authoring corrections (both Rule 1). **Impact:** none on product behaviour; both were necessary for the tests to exercise the intended code paths. No scope creep.

## Issues Encountered

- **Full-suite run shows a large pre-existing cascade (71 failed / 80 errors), NOT introduced by Batch B.** A baseline run at main `08d567d` (pre-Batch-B) reproduced it identically — baseline 798 passed / 71 failed / 80 errors vs Batch B 812 passed / 71 failed / 80 errors, i.e. a delta of exactly **+14 passed** (the 14 new Batch B tests) with an unchanged 71 failed + 80 errors. The checker suite is 100% green in both runs. Root cause is the documented `test_phase5_migration_017` pooled-connection poisoning cascade (migration-017 constraint reapply choking on rows committed by earlier factory tests over the long full run). Logged to `deferred-items.md`; out of scope per SCOPE BOUNDARY. Targeted verification is fully green: `test_checker_resilience_batch_b.py` + `test_checker.py` + `test_checker_probe.py` + `test_checker_probe_burn.py` = **50 passed**.
- Test-overlay `run` conflicted with the running prod `db` container (fixed `container_name: outreach-platform-db`) in this worktree; worked around by bringing up only ephemeral `db-test` and running pytest with `--no-deps` (prod DB never touched; `outreach_test` DSN confirmed via the conftest guard passing setup).

## User Setup Required

None — no external service configuration required. **Deploy is orchestrated separately AFTER merge** (per plan `<output>`): rebuild BOTH containers `docker compose up -d --build api listener` (checker runs in the api container's ContactCheckWorker; the listener shares the module), then live-verify. If the checker pool is fully parked (0 pending), live verification is deferred until a healthy checker is re-authed — not a merge blocker.

## Next Phase Readiness

- Batch B complete; the contact-checking pipeline no longer freezes on a long FloodWait, no longer hot-loops on a dead checker, no longer leaks saved phones on empty imports, no longer permanently kills a checker when the control set is missing, no longer swallows invalid numbers, and selects checkers deterministically.
- Remaining review batches (C queue priority, D head-of-line, E campaign lifecycle, F /send hardening, G identity/rotation, H dead code) are independent follow-ups.
- Pre-existing test-isolation cascade is a candidate for a dedicated "test isolation hardening" task (see deferred-items.md).

## Self-Check: PASSED

- Files verified on disk: `app/services/checker.py`, `app/services/contact_check_worker.py`, `tests/test_checker_resilience_batch_b.py`, `260703-rm3-SUMMARY.md`, `deferred-items.md`.
- Commits verified in history: `cd44d47`, `cf1ca6b`, `9fc08ce`, `193e654`.
- Plan `<verification>` greps all present; no new migration (`git status migrations/` clean).
- Targeted verification green: 50 passed (Batch B + full checker suite).

---
*Phase: quick-260703-rm3*
*Completed: 2026-07-03*
