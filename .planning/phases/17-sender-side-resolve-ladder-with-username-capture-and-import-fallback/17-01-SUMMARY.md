---
phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
plan: 01
subsystem: testing
tags: [pytest, telethon-mock, red-scaffold, resolve-ladder, restriction-audit]

# Dependency graph
requires:
  - phase: 14-reliable-contact-resolution
    provides: "mock_telethon_client fixture (conftest:986), contacts.tg_probe_state/tg_confidence/tg_resolved_by (mig 034), resolve_phone_with_fallback, _apply_results suspect/clean finalization"
  - phase: 10-pool-visibility
    provides: "record_restriction_event helper (restriction_audit.py:48), sender_restriction_events table (mig 030/031), free-form event_type"
provides:
  - "12 RED/contract tests across 4 test files covering SRLD-01..08 — concrete `pytest -k` targets that flip GREEN as 17-02/17-03/17-04 implement"
  - "_resolved_users(username=...) / _resolved / _imported / _raises mock helpers for the resolve-ladder tests"
  - "Executable specs for: checker username capture, confidence-gated cache reads (both checker + sender), sender 3-tier resolve ladder, import gate, lazy import (no DeleteContacts on sender), stale-username fall-through, durable block capture + block-rate"
affects: [17-02-checker-username-capture, 17-03-sender-resolve-ladder, 17-04-block-capture-and-docs]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Deferred in-body imports keep --collect-only clean while the body fails RED at run time (Wave-0 scaffold convention, lifted from Phase 8/13/14/16)"
    - "client.calls request-type introspection asserts the resolve LADDER shape (which Telethon RPCs fire, in order) rather than mocking return values only"
    - "Callable mock response (_raises) raises a Telethon error inside the mock client so the stale-username fall-through can be exercised without a live session"

key-files:
  created: []
  modified:
    - tests/test_checker.py
    - tests/test_send.py
    - tests/test_contact_check_worker.py
    - tests/test_restriction_audit.py

key-decisions:
  - "DB-session fixture for the cache-gate test: test_checker.py uses `async_db_session` (which COMMITs) because _lookup_cache / _get_cached_contact open their OWN AsyncSessionLocal() sessions — uncommitted rows are invisible to them."
  - "SRLD-07 confidence signal lives on the matching contacts row (tg_probe_state/tg_confidence), not on contacts_cache (which has NO source column) — matches D-12 research recommendation (gate the READ against contacts.* columns, no schema change)."
  - "SRLD-01 split into two tests (ResolvePhone path + ImportContacts fallback path) — the action spec requires both capture sites; 12 tests total vs the plan's stated 11."

patterns-established:
  - "Wave-0 RED scaffold: append SRLD tests to existing Phase-10/14 test files, mirror their fixtures, deferred in-body imports, full suite stays at 0 collection errors."

requirements-completed: [SRLD-01, SRLD-02, SRLD-03, SRLD-04, SRLD-05, SRLD-06, SRLD-07, SRLD-08]

# Metrics
duration: 14min
completed: 2026-06-30
---

# Phase 17 Plan 01: Test Scaffold Summary

**12 Wave-0 RED/contract tests (10 RED, 2 GREEN contracts) locking the sender-side resolve ladder, checker username capture, confidence-gated cache reads, and durable block capture as executable specs — full suite collects 852 tests, 0 errors.**

## Performance

- **Duration:** ~14 min
- **Started:** 2026-06-30T16:12Z
- **Completed:** 2026-06-30T16:24Z
- **Tasks:** 3
- **Files modified:** 4 (all under tests/)

## Accomplishments

- **SRLD-01 (×2) + SRLD-07-checker** in `tests/test_checker.py`: username capture from ResolvePhone AND from the ImportContacts fallback (both RED — checker drops `user.username`); a suspect-source `is_registered=false` cache row is NOT served by `_lookup_cache` (RED), with a clean+high-confidence negative control that IS served.
- **SRLD-03/04/05/06/07-sender** in `tests/test_send.py`: drive `TelegramService().resolve_contact` against `mock_telethon_client`, asserting on `client.calls` — sender's own ResolvePhone is removed (RED: still fires), import gated on `tg_status='registered'` (RED), no DeleteContacts on the sender (RED), stale captured username falls through to import (RED), cross-sender suspect false does not short-circuit (RED).
- **SRLD-02 (GREEN contract)** in `tests/test_contact_check_worker.py`: drives `_apply_results` directly with a username in the results — pins that the captured username persists to `contacts.tg_username_resolved` and NEVER clobbers the CSV `contacts.username` (Pitfall 5). GREEN today because the worker SQL already writes it (worker:875); flips fully live once 17-02 makes the checker emit the username.
- **SRLD-08 (×3)** in `tests/test_restriction_audit.py` + `tests/test_send.py`: `event_type='blocked'` inserts with no CHECK violation (GREEN — proves free-form event_type, no migration); `UserIsBlockedError` on send surfaces as `code='USER_IS_BLOCKED'` (RED — currently SEND_FAILED); per-sender block-rate aggregate `sender_block_rate(...)` (RED — ImportError, helper not built).

## `-k` selectors per requirement (continuation targets)

| Req | File | `-k` selector |
|-----|------|---------------|
| SRLD-01 | test_checker.py | `username_capture` (2 tests: resolve_phone + import_fallback) |
| SRLD-02 | test_contact_check_worker.py | `captured_username` |
| SRLD-03 | test_send.py | `resolve_ladder` |
| SRLD-04 | test_send.py | `import_gate` |
| SRLD-05 | test_send.py | `lazy_import` |
| SRLD-06 | test_send.py | `stale_username_fallthrough` |
| SRLD-07 | test_checker.py + test_send.py | `confidence_gated_cache` (checker_read + sender_read) |
| SRLD-08 | test_restriction_audit.py + test_send.py | `blocked or user_blocked or block_rate` |

Quick all-new run: `pytest -k "username_capture or confidence_gated_cache or resolve_ladder or import_gate or lazy_import or stale_username_fallthrough or captured_username or blocked or user_blocked or block_rate"` → 10 failed, 2 passed.

## Mock helpers added

- `tests/test_checker.py::_resolved_users(*, telegram_id, username=None)` — ResolvedPeer-like with `.users[0].username` (SRLD-01 capture).
- `tests/test_send.py::_user_obj / _resolved / _imported / _raises` — Telethon `User`/`ResolvedPeer`/`ImportedContacts` shapes (id/access_hash/username) + a callable response that raises a Telethon error inside the mock client (drives the SRLD-06 stale-username fall-through). `_seed_contact(...)` COMMITs a folder+contact for the ladder tests.

## Task Commits

1. **Task 1: checker username capture + confidence-gated cache read** — `928389a` (test)
2. **Task 2: sender resolve ladder** — `2f1192e` (test)
3. **Task 3: username persistence + block capture** — `bc2a9df` (test)

## Files Created/Modified

- `tests/test_checker.py` — +3 tests (SRLD-01 ×2, SRLD-07-checker) + `_resolved_users` helper + `text` import
- `tests/test_send.py` — +6 tests (SRLD-03/04/05/06/07-sender, SRLD-08 send-path) + mock helpers + `text` import
- `tests/test_contact_check_worker.py` — +1 test (SRLD-02 persistence contract)
- `tests/test_restriction_audit.py` — +2 tests (SRLD-08 blocked-insert GREEN, block-rate RED)

## Decisions Made

- **DB-session fixture for the cache-gate test:** `async_db_session` (committing). `_lookup_cache` (checker) and `_get_cached_contact` (sender) open their own `AsyncSessionLocal()` sessions, so seed rows MUST be committed to be visible — `async_db_session` commits, the test-DB rollback only covers in-session work.
- **SRLD-07 confidence signal source:** the matching `contacts` row's `tg_probe_state`/`tg_confidence` (Phase 14), NOT a `contacts_cache` column (it has none) — aligns with the D-12 research recommendation to gate the READ against existing `contacts.*` columns (no schema change in Phase 17 just for the gate).
- **SRLD-02 is a GREEN persistence contract, not RED:** by design (plan: "GREEN-able by 17-02"). Driving `_apply_results` directly with a username already exercises the existing worker UPDATE (worker:875), pinning the CSV-vs-resolve provenance separation before 17-02 makes the checker emit the username.

## Deviations from Plan

**1. [Rule 1 - Bug] SRLD-07-checker test seed slug exceeded VARCHAR(50)**
- **Found during:** Task 1 (confidence_gated_cache_checker_read)
- **Issue:** `slug=f"srld07-checker-{uuid4()}"` is 49+ chars with a full UUID → `StringDataRightTruncationError` on the senders INSERT — the test failed on setup, not on the intended assertion (wrong-reason RED).
- **Fix:** Shortened to `srld07-chk-{uuid4().hex[:8]}`; re-ran → now fails on the intended `assert suspect_hit is None` (correct RED).
- **Files modified:** tests/test_checker.py
- **Verification:** `pytest -k confidence_gated_cache` → AssertionError on the gate assertion (not a DB error).
- **Committed in:** 928389a (Task 1 commit)

**2. [Rule 1 - Bug] never-awaited-coroutine warning on the UserIsBlocked test mock**
- **Found during:** Task 3 (user_blocked_records_event)
- **Issue:** `send_message`'s `finally: disconnect_client(client)` calls `client.is_connected()` synchronously; on the AsyncMock that returns an un-awaited coroutine → RuntimeWarning (harmless, but noisy).
- **Fix:** Set `client.is_connected = MagicMock(return_value=False)` so the disconnect guard is a clean no-op.
- **Files modified:** tests/test_send.py
- **Verification:** Re-ran the test — clean RED, no warning.
- **Committed in:** bc2a9df (Task 3 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1 — test-setup bugs producing wrong-reason RED / noisy warnings).
**Impact on plan:** No scope change; both fixes ensure each RED test fails on its intended assertion. No production `app/` code was touched (verified — `git diff --name-only` for this plan lists only `tests/`).

## Issues Encountered

- The benign `Failed to save contact cache: ForeignKeyViolationError` log line in the SRLD-03 ladder test (the random `sender_id` is not a real sender, and `_save_contact_cache` swallows the error) is expected noise — it does not affect the assertion, which only inspects `client.calls`. Left as-is to keep the test simple; 17-03 will make ResolvePhone disappear from that path entirely.

## User Setup Required

None — test-only plan, no external service configuration.

## Next Phase Readiness

- 17-02 (checker username capture + gated read) has `pytest -k "username_capture or confidence_gated_cache"` targets in test_checker.py.
- 17-03 (sender resolve ladder) has `pytest -k "resolve_ladder or import_gate or lazy_import or stale_username_fallthrough or confidence_gated_cache"` in test_send.py.
- 17-04 (block capture + docs) has `pytest -k "user_blocked or block_rate"` (send.py) + `blocked` (restriction_audit.py). The block-rate test imports `app.services.restriction_audit.sender_block_rate` — 17-04 must add that helper.
- Full suite collects 852 tests / 0 errors; 10 new RED + 2 new GREEN; baseline otherwise unchanged.

## Self-Check: PASSED

- `17-01-SUMMARY.md` — FOUND
- Commits `928389a`, `2f1192e`, `bc2a9df` — all FOUND
- Scope: all 3 task commits touch `tests/` only — `git diff --name-only 9e3c571..bc2a9df -- app/ migrations/` is empty (no production code modified, per the test-only plan constraint)
- Full suite collects 852 tests, 0 collection errors

---
*Phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback*
*Completed: 2026-06-30*
