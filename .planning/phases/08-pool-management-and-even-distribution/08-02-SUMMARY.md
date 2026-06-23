---
phase: 08-pool-management-and-even-distribution
plan: 02
subsystem: backend-services
tags: [pool-management, rebalance, even-distribution, tdd, wave-2, concurrency]
requires:
  - tests/test_rebalance.py (Plan 08-01 RED harness — POOL-07/08/08b)
  - test_queue_item_factory fixture (Plan 08-01, conftest.py)
  - message_queue / campaign_contact_assignments / conversations / campaign_senders schema (migration 016)
provides:
  - app/services/rebalance.py::rebalance_on_attach(campaign_id, new_sender_id, db) -> int
  - module-level BATCH_CAP=500 and _COLD_PENDING_PREDICATE (reusable cold-pending filter)
affects:
  - Plan 08-03 (attach endpoint) — calls rebalance_on_attach when campaign.status == 'running'
tech-stack:
  added: []
  patterns:
    - "campaign-scoped even-split (NOT rotation._pick_least_loaded which is global-scope single-pick)"
    - "FOR UPDATE OF mq SKIP LOCKED + status='pending' donor claim mirroring queue.py:313 worker discipline"
    - "single-transaction queue.sender_id + CCA.sender_id reassign keyed on recipient_phone (lock-step invariant)"
key-files:
  created:
    - app/services/rebalance.py
  modified: []
decisions:
  - "BATCH_CAP=500 single pass (D-09 discretion) — cheap UPDATE, v1 scale; total/P>cap needs a follow-up pass (out of v1 scope)"
  - "±1 even-split guaranteed for the NEWLY-ATTACHED sender only — pre-existing donors not re-evened against each other (D-08 narrowed scope)"
  - "_pick_least_loaded deliberately NOT reused (global cross-campaign count); fresh campaign-scoped set-based pass written instead"
  - "No migration — 016 covers all schema (campaign_senders, CCA UNIQUE idx, message_queue composite index)"
metrics:
  duration: 8min
  tasks: 1
  files: 1
  completed: 2026-06-23
---

# Phase 8 Plan 02: Rebalance Service Summary

Implemented `app/services/rebalance.py::rebalance_on_attach` — the one genuinely-new piece of Phase 8 logic — a campaign-scoped even-split that back-fills a newly-attached sender from overloaded senders' un-sent cold-pending backlog, keeping `campaign_contact_assignments` in lock-step with `message_queue` and never racing the queue worker. All 3 RED tests from Plan 08-01 (POOL-07/08/08b) are now GREEN.

## What Was Built

### `rebalance_on_attach(campaign_id, new_sender_id, db) -> int`
A single set-based pass (no `_pick_least_loaded` loop, which is global-scope and single-pick):

1. **Eligible pool** — `campaign_senders JOIN senders JOIN campaigns`, candidate filter copied verbatim from `rotation.py:113-123` (`lifecycle_status='active' AND auth_status='ok' AND role='sender' AND restriction_status='none'`) with `s.workspace_id = c.workspace_id` (closes cross-workspace IDOR, threat T1). If the new sender is not in the eligible pool, or `P < 2` → return 0.
2. **Campaign-scoped cold-pending load per sender** via the `_COLD_PENDING_PREDICATE` (`status='pending'`, `NOT EXISTS sent`, `NOT EXISTS conversations`, keyed on `recipient_phone`). `total == 0` → return 0.
3. **Floor target** `total // P`; `need = target - load[new]`; `need <= 0` → return 0 (idempotent). `need = min(need, BATCH_CAP=500)`.
4. **Donor claim** — rows from senders with load > target, `ORDER BY donor-load DESC, scheduled_at DESC`, `LIMIT :need`, with `FOR UPDATE OF mq SKIP LOCKED` + `status='pending'` (mirrors worker discipline at `queue.py:313`, prevents racing the worker — threat T2).
5. **Reassign in one transaction** — `UPDATE message_queue SET sender_id` AND `UPDATE campaign_contact_assignments SET sender_id` keyed on `recipient_phone`/`contact_phone` (CCA lock-step, Pitfall 3). Single `await db.commit()`.
6. **Log moved-row COUNT only** — never recipient phones / payloads (CLAUDE.md, threat T4). Return n.

Scope (intentional v1 limits per D-08): the ±1-of-total/P guarantee is for the **newly-attached sender only**; pre-existing donors are not re-evened against each other, and `total/P > BATCH_CAP` would need a follow-up pass. Both are out of v1 scope (matches the narrowed POOL-07 assertion in Plan 01).

## Verification Results

- `pytest tests/test_rebalance.py -x` (overlay): **3 passed** (POOL-07/08/08b GREEN; was 1 RED `ModuleNotFoundError` before).
- Acceptance grep-gates — all pass:
  - `grep "SKIP LOCKED"` → match (line 167).
  - `grep "status = 'pending'\|status='pending'"` → match (line 51).
  - `grep "_pick_least_loaded"` → only a comment explaining it is NOT reused (no import/call).
  - both `message_queue` and `campaign_contact_assignments` UPDATE'd.
  - `git status --porcelain migrations/` → empty (no migration added).

All commands ran through the mandatory test-overlay (`docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`), never the bare invocation (CLAUDE.md / 2026-05-26 DROP SCHEMA guard).

## Deviations from Plan

None — plan executed exactly as written. `rotation.py` and `queue.py` untouched; no rate-limit/`scheduled_at` semantics changed; no migration added.

## Deferred Issues

**Pre-existing full-suite failures (NOT caused by this plan).** The Plan's verification step calls for a full-suite run on wave-merge. The full suite reports `69 failed / 593 passed / 1 skipped / 20 errors`. These were **confirmed pre-existing**: with `app/services/rebalance.py` removed from the tree the same 20 setup-time asyncpg errors reproduce in `tests/test_migration_014.py` and `tests/test_onboarding_reauth.py` (sqlalchemy/asyncpg `_prepare_and_execute` path — an infra/fixture-level issue, not application logic). The only file 08-02 added is the brand-new `rebalance.py`, which nothing outside `tests/test_rebalance.py` imports (grep-confirmed). Logged to `.planning/phases/08-pool-management-and-even-distribution/deferred-items.md` for a separate investigation; out of scope for the rebalance plan.

## Known Stubs

None. `rebalance.py` is fully wired and exercised by the GREEN POOL-07/08/08b tests. (It is not yet *called* by an endpoint — that is Plan 08-03's attach endpoint, which is the documented consumer per the plan's signature note.)

## Self-Check: PASSED

- `app/services/rebalance.py` exists.
- Task commit `93ab819` present in git history.
