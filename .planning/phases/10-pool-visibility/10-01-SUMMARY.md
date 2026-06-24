---
phase: 10-pool-visibility
plan: 01
subsystem: testing
tags: [tdd, wave-0, restriction-audit, pool-health, scaffold]
dependency_graph:
  requires: []
  provides:
    - "tests/test_restriction_audit.py (10 RED stubs — HLTH-01/02, D-03, HLTH-03)"
    - "tests/test_pool_health.py (2 RED stubs — POOLV-01/02)"
  affects:
    - "10-02-event-log-and-write-points (must turn restriction-audit tests GREEN)"
    - "10-03-pool-health-and-history-endpoint (must turn pool_health + history tests GREEN)"
tech_stack:
  added: []
  patterns:
    - "import-inside-body RED stub (collect-only clean, runtime RED) — per test_failover.py/test_rebalance.py"
    - "per-test distinct JWT sub bound to test_workspace (user_workspaces UNIQUE guard, migration 023)"
key_files:
  created:
    - tests/test_restriction_audit.py
    - tests/test_pool_health.py
  modified: []
decisions: []
metrics:
  duration: ~20min
  completed: 2026-06-24
  tasks: 2
  files: 2
---

# Phase 10 Plan 01: Test Scaffold Summary

Wave-0 RED test scaffold for Phase 10 — two test files (12 fully-asserting stubs) that encode the restriction-audit event-log (HLTH-01/02 + D-03 + HLTH-03) and pool-visibility (POOLV-01/02) contracts before any implementation exists; both files collect cleanly and fail RED at runtime, the expected pre-implementation state.

## What Was Built

### Task 1 — `tests/test_restriction_audit.py` (commit `5dd25b7`)
10 fully-asserting tests, one per VALIDATION.md Per-Task row, each importing `app.services.restriction_audit.record_restriction_event` **inside the test body** so `--collect-only` is clean while runtime fails with `ModuleNotFoundError` (the helper lands in Wave 2):

- `test_peer_flood_writes_event` (HLTH-01a) — PEER_FLOOD → one `spam_limited`/`queue_error` event row.
- `test_reconcile_cleared_writes_event` (HLTH-01b) — reconcile free → `cleared`/`spambot_reconcile`, `restricted_until` NULL.
- `test_reconcile_no_shift_no_event` (HLTH-01c / D-01) — same release date → **no** `extension` event (kills the 37/day noise).
- `test_reconcile_shift_writes_extension` (HLTH-01d / D-01) — forward shift → exactly one `extension` event.
- `test_events_append_only` (HLTH-01e) — spam_limited + cleared → both rows survive (append-only).
- `test_event_carries_activity_slice` (HLTH-02a) — `activity_slice.sends_1h == N`, `rate` echoes sender limits (4/20/150).
- `test_event_carries_proxy_snapshot` (HLTH-02b) — `proxy` JSONB column equals `senders.proxy`.
- `test_slice_windows_sent_only` (HLTH-02c) — counts only `message_type='sent'`, 1h vs 24h windowing correct (failed + >1h excluded from sends_1h).
- `test_recipient_privacy_separate_category` (D-03) — `category='recipient_privacy'`, `restriction_status` stays `none`, excluded by `WHERE category='restriction'`.
- `test_history_endpoint` (HLTH-03) — `GET /senders/{slug}/restriction-events` newest-first + foreign-workspace sender returns 404 (cross-tenant-leak guard baked in per threat model).

### Task 2 — `tests/test_pool_health.py` (commit `a9f4db5`)
2 fully-asserting tests exercised through the real `GET /api/v1/campaigns/{id}` → `_campaign_to_response`:

- `test_pool_health_states` (POOLV-01) — 3-state arithmetic: all-active `{3,0,3,None}` → one frozen `{2,1,3,T}` → all frozen `{0,3,3,MIN}`. Numeric contract only; badge derived on frontend (no `badge_state` field).
- `test_attached_senders_enriched` (POOLV-02) — frozen sender carries `restriction_status='spam_limited'` + matching `restricted_until`; active sender carries `none`/`None`.

## Verification

| Check | Result |
|-------|--------|
| `pytest test_restriction_audit.py --collect-only` (test-overlay) | exit 0, 10 tests, no collection errors |
| `pytest test_pool_health.py --collect-only` (test-overlay) | exit 0, 2 tests |
| Combined `--collect-only` | 12 tests collected in 0.14s |
| Combined run (test-overlay) | 12 failed (RED) — `ModuleNotFoundError` (restriction_audit) + `KeyError: 'pool_health'` |
| `grep -c "from app.services.restriction_audit import"` | 10 (import inside each test body) |
| `grep -q "test_running_campaign_factory" test_pool_health.py` | yes |

Both files RED at runtime confirms a genuine TDD scaffold (not vacuous `assert True`). Feedback latency < 3s, well under the 90s budget.

## Deviations from Plan

None — plan executed exactly as written. All tests run via test-overlay (`docker-compose.test.yml`, ephemeral `db-test` in tmpfs) per CLAUDE.md; no bare `pytest`, no `down -v`.

## Known Stubs

The two test files are intentional Wave-0 RED stubs (the whole purpose of this plan): they assert against `app.services.restriction_audit.record_restriction_event`, the `sender_restriction_events` table, the `pool_health` response key, and `attached_senders[].restriction_status`/`restricted_until` — none of which exist yet. They are resolved by Plan 10-02 (helper + migration 030 + write-points) and Plan 10-03 (history endpoint + response enrichment). This is the documented TDD contract, not an incomplete deliverable.

## Self-Check: PASSED
- FOUND: tests/test_restriction_audit.py
- FOUND: tests/test_pool_health.py
- FOUND: commit 5dd25b7
- FOUND: commit a9f4db5
