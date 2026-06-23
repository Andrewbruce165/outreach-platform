---
phase: 08-pool-management-and-even-distribution
plan: 01
subsystem: backend-tests
tags: [testing, pytest, pool-management, wave-0, red-stubs]
requires:
  - tests/conftest.py existing factories (test_workspace, test_sender_factory, test_campaign_factory, test_running_campaign_factory, attach_sender_to_campaign)
  - message_queue / campaign_contact_assignments / conversations schema (migration 016)
provides:
  - test_queue_item_factory fixture (seeds message_queue + sticky CCA + optional conversation)
  - 10 named RED test slots mapping 1:1 to POOL-01..08b
affects:
  - Plan 08-02 (rebalance_on_attach) — test_rebalance.py is its acceptance harness
  - Plan 08-03 (attach/detach endpoints) — test_pool_endpoints.py is its acceptance harness
tech-stack:
  added: []
  patterns:
    - "raw-SQL fixture mirroring attach_sender_to_campaign (text() INSERT + commit)"
    - "import-inside-test-body to keep --collect-only clean for a not-yet-existing module"
key-files:
  created:
    - tests/test_pool_endpoints.py
    - tests/test_rebalance.py
  modified:
    - tests/conftest.py
decisions:
  - "Wave-0 tests are fully-asserting (no pass/xfail) so later waves verify against real RED→GREEN transitions"
  - "test_queue_item_factory keeps CCA in lock-step with the queue row via sticky upsert (rotation.py:150-163 shape) — the exact invariant rebalance asserts"
  - "rebalance import done inside each test body so collection succeeds while the module is RED (ModuleNotFoundError at run time)"
metrics:
  duration: 9min
  tasks: 3
  files: 3
  completed: 2026-06-23
---

# Phase 8 Plan 01: Test Scaffold Summary

Created the Wave-0 test scaffolding for Phase 8 pool management: a `test_queue_item_factory` conftest fixture plus 10 fully-asserting RED tests (7 attach/detach + 3 rebalance) that give every POOL-01..08b behavior an automated slot before implementation lands.

## What Was Built

### Task 1 — `test_queue_item_factory` (tests/conftest.py)
An async fixture seeding a `message_queue` row keyed on `recipient_phone` (workspace_id from `test_workspace`, `scheduled_at=NOW()`, `item_type='message'`). Options:
- `with_cca=True` (default): upserts a matching `campaign_contact_assignments(campaign_id, contact_phone)` row pointing at the same sender (`ON CONFLICT DO UPDATE`), mirroring the sticky assignment in `rotation.py:150-163` — keeps CCA in lock-step with the queue row.
- `with_conversation=True`: inserts a `conversations` row for the recipient so a contact can be marked "engaged" (used by POOL-06b / POOL-08b).

Mirrors the established `attach_sender_to_campaign` raw-`text()` + `commit()` shape. No existing fixture or the conftest DB-guard (lines 46-77) was touched.

### Task 2 — tests/test_pool_endpoints.py (POOL-01..06b)
7 fully-asserting integration tests against `POST /api/v1/campaigns/{id}/senders` and `DELETE /.../senders/{sid}`:
`test_attach_adds_sender` (200 + row), `test_attach_locked_sender_409` (409 SENDER_LOCK_CONFLICT with `conflicts:[{sender_id,campaign_id,campaign_name}]`), `test_attach_foreign_sender_404` (404 SENDER_NOT_FOUND, no data leak), `test_detach_removes_sender` (200 + row gone), `test_detach_last_running_409` (409 MIN_POOL_GUARD), `test_detach_cold_pending_409` (409 DETACH_BLOCKED_PENDING), `test_detach_engaged_only_ok` (200, D-05). All fail RED with 404 (endpoints not implemented).

### Task 3 — tests/test_rebalance.py (POOL-07/08/08b)
3 fully-asserting tests against `rebalance_on_attach(campaign_id, new_sender_id, db)`:
`test_rebalance_evens_cold_pending` (P=2 skewed 6→0 backlog; new sender's bucket within ±1 of total/P + asserts moved queue-row sender matches its CCA sender), `test_rebalance_idempotent` (second call returns 0 moved, distribution unchanged), `test_rebalance_skips_non_cold` (sent/processing/engaged rows never moved). Imports are inside each test body, so `--collect-only` is clean while run-time is RED (`ModuleNotFoundError: app.services.rebalance`).

## Verification Results

- `pytest --collect-only -q` (overlay): **683 tests collected, 0 collection errors** (was 673; +7 +3).
- `pytest tests/test_pool_endpoints.py -q`: **7 failed RED** (HTTP 404 — endpoints absent). The cold-pending/engaged tests reached their assertions, proving `test_queue_item_factory` inserts message_queue + CCA + conversations rows without column errors.
- `pytest tests/test_rebalance.py -q`: **3 failed RED** (ModuleNotFoundError on `app.services.rebalance`).

All commands ran through the mandatory test-overlay (`docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`), never the bare invocation (CLAUDE.md / 2026-05-26 DROP SCHEMA guard).

## Requirement → Test Map (contract)

| Req | Test |
|-----|------|
| POOL-01 | test_attach_adds_sender |
| POOL-02 | test_attach_locked_sender_409 |
| POOL-03 | test_attach_foreign_sender_404 |
| POOL-04 | test_detach_removes_sender |
| POOL-05 | test_detach_last_running_409 |
| POOL-06 | test_detach_cold_pending_409 |
| POOL-06b | test_detach_engaged_only_ok |
| POOL-07 | test_rebalance_evens_cold_pending |
| POOL-08 | test_rebalance_idempotent |
| POOL-08b | test_rebalance_skips_non_cold |

## Deviations from Plan

None — plan executed exactly as written. The fixture column names were confirmed against `app/models/__init__.py::MessageQueue`/`Conversation` and migration 016 before writing INSERTs (no guesses); `message_queue.status` accepts a string literal cast to its PG enum (mirrors existing `test_campaign_router.py` inserts).

## Known Stubs

These are intentional Wave-0 RED stubs, by design — the tests assert real behavior that lands in later plans:
- `tests/test_pool_endpoints.py` — fails RED until Plan 08-03 implements the attach/detach endpoints.
- `tests/test_rebalance.py` — fails RED until Plan 08-02 creates `app/services/rebalance.py`.

These are NOT data/UI stubs; no placeholder data flows anywhere. They are the feedback-sampling harness for Phase 8 per 08-VALIDATION.md.

## Self-Check: PASSED

All 3 created/modified files exist; all 3 task commits (f6f6e4b, 89b69f3, f70a70a) present in git history.
