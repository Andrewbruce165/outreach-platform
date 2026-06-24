---
phase: 09-cold-contact-failover
plan: 01
subsystem: queue/failover (test scaffold)
tags: [tdd, wave-0, red-tests, failover, pytest, conftest]
requires:
  - tests/test_rebalance.py (pattern source — import-inside-body, _pending_counts/_cca_sender_for)
  - tests/conftest.py::test_queue_item_factory + test_running_campaign_factory
  - migrations/017_phase5.sql (messages table: conversation_id/direction/message_text/sent_by)
  - migrations/028_sender_restriction.sql (senders.restriction_status/restricted_until)
provides:
  - tests/test_failover.py (9 RED tests, FAIL-01..08 contract locked as executable)
  - tests/conftest.py::test_queue_item_factory with_message flag (empty vs engaged conversation)
affects:
  - 09-02 (implementation graded red→green against this scaffold)
tech-stack:
  added: []
  patterns:
    - import-inside-test-body (collect-only clean while target module absent)
    - transaction-neutral helper called with explicit async_db_session
key-files:
  created:
    - tests/test_failover.py
  modified:
    - tests/conftest.py
decisions:
  - "with_message flag chosen over a separate messages-insert fixture: keeps the empty-conversation (D-05 movable) and engaged (FAIL-05 not-movable) cases producible from the SAME factory call site"
  - "messages row anchored by conversation_id via RETURNING id (messages has no recipient_phone — migration 017)"
  - "tests pass async_db_session explicitly (transaction-neutral path) so the in-session move is visible to assertions"
metrics:
  duration: ~25min
  tasks: 2
  files: 2
  completed: 2026-06-24
---

# Phase 9 Plan 01: Cold-Contact Failover Test Scaffold Summary

Wave-0 RED scaffold that locks the `failover_cold_backlog` contract as 9 fully-asserting pytest tests (FAIL-01..08) plus the one conftest fixture extension (`with_message`) needed to distinguish an empty conversation (D-05, movable) from an engaged has-message conversation (FAIL-05, not movable) — collects clean, RED only because `app.services.failover` does not yet exist.

## What Was Built

### Task 1 — `test_queue_item_factory` gains `with_message` (commit e22715e)
- Added optional keyword `with_message: bool = False` to `test_queue_item_factory` in `tests/conftest.py`.
- When `with_conversation=True` AND `with_message=True`, the factory now inserts one `inbound` row into the `messages` table (columns from migration 017: `conversation_id`, `direction`, `message_text`, `sent_by`, `created_at`), anchored to the conversation via `RETURNING id`.
- Default `with_message=False` leaves behavior unchanged: `with_conversation=True` still produces an EMPTY conversation (zero messages) — the D-05 movable-cold case stays reproducible exactly as before.
- Diff is additions only inside the one fixture; `test_running_campaign_factory` and all other fixtures untouched.

### Task 2 — `tests/test_failover.py` RED scaffold (commit 3a889e7)
- 9 fully-asserting tests, each importing `from app.services.failover import failover_cold_backlog` INSIDE its body (mirrors `test_rebalance.py:51`) so `--collect-only` stays clean while the module is absent.
- `_pending_counts` and `_cca_sender_for` copied verbatim from `test_rebalance.py:26-41`.
- Local freeze-state helpers (`_freeze_sender`, `_pause_pending`) mirror the real freeze paths: flag `restriction_status` + push pending `scheduled_at +24h`, so moved rows must be reset to `NOW()` (Pitfall 2) and the frozen sender is excluded as a receiver (Pitfall 1/3).

| Test | Requirement | Asserts |
|------|-------------|---------|
| `test_failover_spreads_to_healthy_pool` | FAIL-01 | 4 cold rows leave frozen, spread across >=2 healthy receivers, return == 4 |
| `test_failover_excludes_frozen_as_receiver` | FAIL-01 / D-09 / Pitfall 1 | no moved row's new sender is the frozen sender; stale CCA repointed |
| `test_failover_skips_engaged` | FAIL-03 | sent/processing/engaged stay on frozen; only the cold row moves |
| `test_failover_moves_empty_conversation` | FAIL-03 / D-05 | empty conversation (0 messages) IS moved |
| `test_failover_cca_in_sync` | FAIL-04 | queue.sender_id == CCA.sender_id; scheduled_at reset to <= NOW() |
| `test_failover_leaves_engaged` | FAIL-05 | has-message contact stays on frozen sender, moved == 0 |
| `test_failover_idempotent` | FAIL-06 | 2nd call moves 0, distribution unchanged |
| `test_failover_no_receiver_keeps_paused` | FAIL-07 / D-13 | sender_count==1 → moved 0, paused scheduled_at unchanged |
| `test_failover_logs_count_no_pii` | FAIL-08 | log carries moved COUNT + frozen sender UUID; recipient phone NEVER in caplog |

FAIL-02 (the three freeze call sites actually invoking failover) is intentionally NOT scaffolded here — it lives in 09-02 alongside the call-site edits.

## Verification

All commands run via the mandatory test-overlay (`docker-compose.yml` + `docker-compose.test.yml`), DATABASE_URL → ephemeral `outreach_test` (tmpfs). A worktree-only third overlay renamed the static `container_name`s and `--no-deps` kept the run off the live prod `db` (the prod stack was up the whole time and was never touched). The ephemeral stack was torn down (`down -v`) after the run.

- `pytest --collect-only -q tests/test_failover.py` → **9 tests collected, 0 import errors**.
- `pytest tests/test_failover.py -x` → **FAILED with `ModuleNotFoundError: No module named 'app.services.failover'`** at the import line — RED for the right reason (fixtures/freeze setup ran fine; only the module is missing).
- `pytest --collect-only -q` (full suite) → **701 tests collected, 0 errors** (692 prior + 9 new; conftest change introduced no regression).
- `grep -c 'def test_failover' tests/test_failover.py` → 9; no `pytest.skip` / bare `pass`.
- `git diff --stat app/` → empty (no production code modified).

## Deviations from Plan

None — plan executed exactly as written.

(Process note, not a code deviation: the prod-named `db`/`api`/`listener` containers from the live stack collide with the worktree's static `container_name`s, so the test-overlay was run with an extra worktree-only overlay file [renaming container_names] plus `docker compose ... up -d db-test` then `run --rm --no-deps api pytest`. This is a test-harness invocation detail in the isolated worktree only; the canonical command from the main tree — `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` — is unchanged and is what the plan's `<verify>` blocks specify. No conftest guard was weakened; DATABASE_URL stayed on `outreach_test`.)

## Known Stubs

None that block the plan goal. `tests/test_failover.py` is intentionally RED (the target module `app.services.failover` is created in 09-02) — that is the designed Wave-0 state, not a stub to be wired here.

## Self-Check: PASSED

- FOUND: tests/test_failover.py
- FOUND: tests/conftest.py (with_message flag present, grep confirmed)
- FOUND commit e22715e (Task 1)
- FOUND commit 3a889e7 (Task 2)
