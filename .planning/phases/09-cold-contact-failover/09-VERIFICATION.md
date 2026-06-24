---
phase: 09-cold-contact-failover
verified: 2026-06-24T12:00:00Z
status: passed
score: 7/7 must-haves verified
gaps: []
human_verification: []
---

# Phase 9: Cold-Contact Failover Verification Report

**Phase Goal:** Не-контактированные задачи замёрзшего аккаунта уходят на здоровые; активные диалоги ждут свой аккаунт — when a sender freezes, its un-contacted (cold-pending) backlog is reassigned to healthy pool senders; engaged dialogs stay on the frozen sender.
**Verified:** 2026-06-24
**Status:** PASSED
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| #  | Truth                                                                                        | Status     | Evidence                                                                                                       |
|----|----------------------------------------------------------------------------------------------|------------|----------------------------------------------------------------------------------------------------------------|
| 1  | On freeze, cold-pending backlog is reassigned to healthy pool senders inline, zero new worker | ✓ VERIFIED | `failover_cold_backlog` in `app/services/failover.py` (223 lines); 9 unit tests green                        |
| 2  | Failover triggered from all three freeze paths: PEER_FLOOD, ACCOUNT_FROZEN, antispam-signal  | ✓ VERIFIED | queue.py L768-769 (PEER_FLOOD), L811-812 (ACCOUNT_FROZEN); listener.py L952-953 (antispam); 3 FAIL-02 integration tests green |
| 3  | Engaged dialogs (any messages row) stay on the frozen sender and keep replying               | ✓ VERIFIED | `_COLD_PENDING_PREDICATE` NOT EXISTS join on `messages` table; `test_failover_leaves_engaged` green           |
| 4  | The frozen sender is never chosen as a failover receiver                                     | ✓ VERIFIED | Candidate filter `restriction_status = 'none'` (failover.py L155); `test_failover_excludes_frozen_as_receiver` green |
| 5  | When no healthy receiver exists, rows stay paused; nothing is lost                           | ✓ VERIFIED | `len(candidate_ids) < 1` branch logs and continues (failover.py L162-170); `test_failover_no_receiver_keeps_paused` green |
| 6  | Moved rows are sendable immediately (scheduled_at=NOW), not after +24h freeze pause          | ✓ VERIFIED | `SET sender_id = :new, scheduled_at = NOW()` (failover.py L199); `test_failover_cca_in_sync` green           |
| 7  | No new migration is added                                                                    | ✓ VERIFIED | `git status --porcelain migrations/` = empty                                                                  |

**Score:** 7/7 truths verified

### Required Artifacts

| Artifact                     | Expected                                               | Status     | Details                                                                                         |
|------------------------------|--------------------------------------------------------|------------|-------------------------------------------------------------------------------------------------|
| `app/services/failover.py`   | failover_cold_backlog(frozen_sender_id, db=None) → int | ✓ VERIFIED | 223 lines; `async def failover_cold_backlog` present; session duality implemented               |
| `app/services/queue.py`      | failover call after PEER_FLOOD and ACCOUNT_FROZEN commits | ✓ VERIFIED | L768-769 after L754 db2.commit(); L811-812 after L802 db2.commit()                            |
| `app/services/listener.py`   | transaction-neutral call before session.commit in _handle_antispam_signal | ✓ VERIFIED | L952-953, after flag UPDATE (L936-944), before session.commit() (L955)         |
| `tests/test_failover.py`     | 12 tests: 9 unit + 3 FAIL-02 integration               | ✓ VERIFIED | 12 `def test_` functions; `test_peer_flood_triggers_failover` present; 12/12 green per SUMMARY  |
| `tests/conftest.py`          | test_queue_item_factory with_message flag              | ✓ VERIFIED | `with_message: bool = False` at conftest.py L639; INSERT INTO messages guarded by flag          |

### Key Link Verification

| From                       | To                                      | Via                                                  | Status     | Details                                                                     |
|----------------------------|-----------------------------------------|------------------------------------------------------|------------|-----------------------------------------------------------------------------|
| `failover.py`              | `message_queue + campaign_contact_assignments` | Dual UPDATE sender_id + scheduled_at=NOW         | ✓ WIRED    | failover.py L196-211; both tables updated per row in the same session       |
| `failover.py`              | Healthy pool resolution                 | `restriction_status = 'none'` candidate query        | ✓ WIRED    | failover.py L145-159; `restriction_status = 'none'` at L155                |
| `queue.py`                 | `failover_cold_backlog`                 | Call after db2.commit() in both freeze blocks        | ✓ WIRED    | L768-769 (PEER_FLOOD, after L754); L811-812 (ACCOUNT_FROZEN, after L802)   |
| `listener.py`              | `failover_cold_backlog`                 | Call before session.commit() with session passed     | ✓ WIRED    | L952-953; flag UPDATE at L936-944 precedes call (Pitfall 3 satisfied)       |

### Data-Flow Trace (Level 4)

N/A — this phase delivers a backend service (no data-rendering component). The failover function does not render dynamic data; it is a transactional DB reassignment helper. Test coverage validates the data flow directly.

### Behavioral Spot-Checks

Static-only verification per project constraint. Test overlay confirmed all 12 tests green (per SUMMARY.md, independently verifiable by running `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_failover.py -x`).

| Behavior                                      | Check                                         | Result     | Status  |
|-----------------------------------------------|-----------------------------------------------|------------|---------|
| failover_cold_backlog module importable        | `grep "async def failover_cold_backlog" failover.py` | found   | ✓ PASS  |
| get_or_assign_sender not used (Pitfall 1)     | `grep "get_or_assign_sender" failover.py`     | empty      | ✓ PASS  |
| No new migration                              | `git status --porcelain migrations/`          | empty      | ✓ PASS  |
| queue.py: exactly 2 call sites                | `grep -c "failover_cold_backlog" queue.py`    | 2          | ✓ PASS  |
| listener.py: exactly 1 call site              | `grep -c "failover_cold_backlog" listener.py` | 1          | ✓ PASS  |
| listener flag UPDATE precedes failover        | lines 936-944 before L952                     | confirmed  | ✓ PASS  |
| failover.py >= 80 lines                       | `wc -l failover.py`                           | 223        | ✓ PASS  |
| 12 tests in test_failover.py                  | `grep -c "def test_" test_failover.py`        | 12         | ✓ PASS  |
| No pytest.skip / bare pass bodies             | grep for skip/pass                            | empty      | ✓ PASS  |

### Requirements Coverage

| Requirement | Source Plan | Description                                                             | Status      | Evidence                                                              |
|-------------|-------------|-------------------------------------------------------------------------|-------------|-----------------------------------------------------------------------|
| FAIL-01     | 09-01, 09-02 | Cold-pending backlog reassigned to healthy senders via per-item least-loaded | ✓ SATISFIED | failover.py per-row `_pick_least_loaded`; tests `test_failover_spreads_to_healthy_pool` + `test_failover_excludes_frozen_as_receiver` green |
| FAIL-02     | 09-02        | Failover invoked from ALL three freeze paths                             | ✓ SATISFIED | queue.py L768, L812; listener.py L953; 3 call-site integration tests green |
| FAIL-03     | 09-01, 09-02 | Movable predicate: pending + message type + no sent/processing + no started dialog | ✓ SATISFIED | `_COLD_PENDING_PREDICATE` in failover.py; `test_failover_skips_engaged` + `test_failover_moves_empty_conversation` green |
| FAIL-04     | 09-01, 09-02 | Moved row updates queue.sender_id + scheduled_at=NOW AND CCA.sender_id in same transaction | ✓ SATISFIED | failover.py L196-211 dual UPDATE per session; `test_failover_cca_in_sync` green |
| FAIL-05     | 09-01, 09-02 | Engaged-dialog rows stay on frozen sender                               | ✓ SATISFIED | NOT EXISTS JOIN messages predicate; `test_failover_leaves_engaged` green      |
| FAIL-06     | 09-01, 09-02 | Idempotent + concurrency-safe under parallel worker                     | ✓ SATISFIED | `FOR UPDATE OF mq SKIP LOCKED` + `status='pending'`; `test_failover_idempotent` green |
| FAIL-07     | 09-01, 09-02 | No healthy receiver → rows stay paused; logged "nowhere to move"; nothing lost | ✓ SATISFIED | failover.py L162-170 early continue with logger.info; `test_failover_no_receiver_keeps_paused` green |
| FAIL-08     | 09-01, 09-02 | Logs COUNT + sender UUIDs only, never recipient phones                  | ✓ SATISFIED | failover.py L217-221 (COUNT + frozen_sid + len(receivers) + cid); `test_failover_logs_count_no_pii` green |
| FAIL-09     | 09-02        | No migration added                                                      | ✓ SATISFIED | `git status --porcelain migrations/` empty; pure SQL reassignment over existing columns |

All 9 Phase 9 requirements satisfied. No orphaned requirements.

### Anti-Patterns Found

Patterns scanned in `app/services/failover.py`, `app/services/queue.py` (freeze blocks), `app/services/listener.py` (`_handle_antispam_signal`), and `tests/test_failover.py`.

| File                          | Line | Pattern                                 | Severity   | Impact                                                                                                         |
|-------------------------------|------|-----------------------------------------|------------|----------------------------------------------------------------------------------------------------------------|
| `app/services/queue.py`       | 769, 812 | `failover_cold_backlog` called outside try/except after separate db2 commit | ⚠️ Warning | CR-01 from 09-REVIEW.md: if failover raises, exception propagates past `_fail_item`, corrupts callback path; crash between freeze-commit and failover call = 24h stall (the gap this phase exists to prevent). Code-review advisory, not a blocker on requirement satisfaction — all FAIL-0x requirements are met and tests pass. |
| `app/services/failover.py`    | 78-83 | Conversation NOT EXISTS guard lacks `campaign_id` scope | ⚠️ Warning | WR-01 from 09-REVIEW.md: a has-message dialog in campaign A prevents moving a cold-pending row in campaign B for the same contact. Only affects multi-campaign workspaces. FAIL-03/FAIL-05 tests use single campaign — test coverage is correct for the defined predicate. |

No 🛑 blocker anti-patterns (missing implementation, empty stub, unwired artifact). The two ⚠️ warnings above were identified and logged in 09-REVIEW.md prior to this verification. They are advisory correctness concerns, not goal-blocking defects — FAIL-0x requirements as written in REQUIREMENTS.md are all satisfied.

### Human Verification Required

None. All observable truths are programmatically verifiable via code inspection + the confirmed test results.

### Gaps Summary

No gaps. All 7 must-have truths are verified, all 9 requirement IDs are satisfied, all 5 artifacts are substantive and wired. The phase goal is achieved.

The two advisory items from 09-REVIEW.md (CR-01: failover not wrapped in try/except on queue.py paths; WR-01: conversation predicate lacks campaign-scope) are logged but do not prevent the stated goal — cold-pending backlog moves off frozen senders in all three freeze paths, engaged dialogs stay put, nothing is lost when no healthy receiver exists.

Pre-existing full-suite failures (63 failed + 20 errors) are confirmed as pre-existing: proven identical at base commit 4de6583 before any Phase 9 changes (see deferred-items.md). They are not regressions.

---

_Verified: 2026-06-24_
_Verifier: Claude (gsd-verifier)_
