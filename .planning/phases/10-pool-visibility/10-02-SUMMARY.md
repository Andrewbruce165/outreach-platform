---
phase: 10-pool-visibility
plan: 02
subsystem: restriction-audit
tags: [wave-2, restriction-audit, event-log, hlth-01, hlth-02, d-01-gate, d-03]
dependency_graph:
  requires:
    - "10-01 (RED test contract — tests/test_restriction_audit.py)"
  provides:
    - "migrations/030_sender_restriction_events.sql (append-only event table)"
    - "app/services/restriction_audit.py::record_restriction_event (dual-mode helper + D-01 gate)"
    - "SenderRestrictionEvent ORM model (for the Wave 3 history endpoint)"
    - "5 account write-points + recipient_privacy write-point emit events in-TX"
  affects:
    - "10-03-pool-health-and-history-endpoint (reads sender_restriction_events via GET /senders/{slug}/restriction-events; turns test_history_endpoint GREEN)"
tech_stack:
  added: []
  patterns:
    - "dual-mode db=None session helper (mirror failover.py:87-114) — same-TX audit write"
    - "idempotent raw-SQL migration (mirror 028) — CREATE TABLE/INDEX IF NOT EXISTS + DROP+ADD CONSTRAINT"
    - "D-01 forward-shift gate inside the helper (extension recorded only on >old+1min)"
    - "id server_default gen_random_uuid() so raw text() INSERT + create_all both get a PK"
key_files:
  created:
    - migrations/030_sender_restriction_events.sql
    - app/services/restriction_audit.py
  modified:
    - app/models/__init__.py
    - app/services/queue.py
    - app/services/listener.py
    - tests/test_sender_restriction.py
decisions:
  - "OQ#1: HARD FloodWait writes an informational flood_wait event (no restriction_status change, pool_health unaffected)"
  - "OQ#2: listener antispam path uses source='antispam_signal' (free-form VARCHAR, no CHECK — not a D-02 violation)"
  - "OQ#3: PRIVACY_RESTRICTED is mandatory this phase (category='recipient_privacy'); PRIVACY_PREMIUM_REQUIRED stays out of scope"
  - "B-1: reconcile old_until read INSIDE the per-sender transaction (not from the stale batch SELECT)"
  - "D-01 gate lives in the helper (per the Wave-1 test contract — test calls record_restriction_event directly), with a defence-in-depth read in listener.py"
metrics:
  duration: ~25min
  completed: 2026-06-24
  tasks: 3
  files: 6
---

# Phase 10 Plan 02: Event-Log & Write-Points Summary

Durable append-only restriction event-log (HLTH-01) with a write-time activity-slice + proxy snapshot (HLTH-02), wired into all five account-level restriction state-change points plus the mandatory recipient-privacy write-point, each event landing in the SAME transaction as the `senders.restriction_status` UPDATE via a dual-mode helper that mirrors `failover_cold_backlog`.

## What Was Built

### Task 1 — migration 030 + ORM model (commit `3d6a747`)
- `migrations/030_sender_restriction_events.sql`: idempotent append-only table `sender_restriction_events` (id/workspace_id/sender_id FK ON DELETE CASCADE, category, event_type, source, restricted_until, raw_text, activity_slice JSONB, proxy JSONB, created_at) + 2 indexes (`idx_sre_sender_created`, `idx_sre_workspace_category`) + `sre_category_chk` CHECK via the 028 drop+recreate idiom. `source` left intentionally free-form (no CHECK) to admit `antispam_signal` (OQ#2).
- `SenderRestrictionEvent` ORM model mirroring `MessageLog` column style, for the Wave 3 history-endpoint reads.

### Task 2 — `app/services/restriction_audit.py` (commit `cbfaaa7`)
- `record_restriction_event(...)` dual-mode dispatcher (`db=None` opens+commits own session; `db` passed → transaction-neutral, caller commits) copied from `failover.py:87-114`.
- `_record` reads the sender row, computes `activity_slice` from `messages_log` (sends_1h/24h via `COUNT(*) FILTER message_type='sent'`, unique_contacts via `COUNT(DISTINCT recipient_phone) FILTER`, rate object with configured-vs-actual) for restriction-category events; `recipient_privacy` rows skip the slice and never touch senders.
- Fix discovered during RED→GREEN: the table needs `id server_default gen_random_uuid()` so a raw `text()` INSERT (no ORM default) and `create_all` (test overlay) both populate the PK.

### Task 3 — wire 5 write-points + D-01 gate (commit `19f5c72`)
- **queue.py** (additive only, 0 deletions to existing logic): PEER_FLOOD → `spam_limited`/`queue_error` and ACCOUNT_FROZEN → `frozen`/`queue_error` events inside the existing `db2` block before commit; HARD FloodWait → informational `flood_wait` event (no status change, OQ#1); **PRIVACY_RESTRICTED** → `privacy_restricted`/`recipient_privacy` on the live send-loop `db` session, never flipping `restriction_status` (D-03).
- **listener.py**: antispam path → `spam_limited`/`antispam_signal` in the existing `session`; reconcile `free`→`cleared`, `suspended`→`banned`, else→gated `extension`. `old_until` read with a per-sender `SELECT restricted_until` INSIDE the reconcile transaction (B-1 atomicity).
- **restriction_audit.py**: the D-01 forward-shift gate was moved INTO the helper (the Wave-1 test `test_reconcile_no_shift_no_event` calls the helper directly) — an `extension` is recorded only when the passed `restricted_until > current + 1 min`. The listener-side `old_until` read remains as defence-in-depth and to satisfy B-1.

### Test adaptation (commit `78c0426`)
- `tests/test_sender_restriction.py` `_FakeResult`/`_FakeSession` mocks extended (`scalar_one_or_none`/`scalar_one`/`one`, sender-row + messages_log slice serving) so the mocked reconcile tick now exercises `record_restriction_event` — restored to 10/10 GREEN.

## Verification

| Check | Result |
|-------|--------|
| `pytest tests/test_restriction_audit.py` (test-overlay) | 9 passed, 1 RED (`test_history_endpoint` = HLTH-03 / Wave 3) |
| Task 2 5 tests (slice/proxy/windows/privacy/append-only) | 5/5 GREEN |
| Task 3 5 tests (peer_flood/cleared/no-shift/shift/privacy) | 5/5 GREEN |
| `tests/test_sender_restriction.py` | 10/10 GREEN |
| `grep -Eq "record_restriction_event\(.*db=db2" app/services/queue.py` | OK |
| `grep -q "old_until" app/services/listener.py` | OK |
| `grep -q "FROM messages_log" ... "message_type = 'sent'"` restriction_audit.py | OK |
| `git diff app/services/queue.py` empirical constants / 24h pause | additions only (0 deletions) — UNTOUCHED |
| ORM import under test-overlay | `SenderRestrictionEvent.__tablename__ == 'sender_restriction_events'` |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] D-01 extension gate placed in the helper, not only the call-site**
- **Found during:** Task 3 (test `test_reconcile_no_shift_no_event` FAILED — an extension row was written on a no-shift call).
- **Issue:** The Wave-1 contract calls `record_restriction_event(event_type="extension", ...)` DIRECTLY (not through the listener), so a call-site-only gate cannot satisfy it.
- **Fix:** The helper now reads the sender's current `restricted_until` and suppresses the `extension` INSERT unless the passed value moves forward by > 1 minute. The listener-side intra-tx `old_until` read is kept as defence-in-depth (and to satisfy acceptance B-1).
- **Files modified:** app/services/restriction_audit.py
- **Commit:** 19f5c72

**2. [Rule 1 - Bug] `id` needed a server_default for raw-SQL INSERT**
- **Found during:** Task 2 (NotNullViolation on `id` — the migration's `gen_random_uuid()` DEFAULT isn't present when the test overlay builds the table via `create_all`, and a raw `text()` INSERT doesn't apply the ORM `default`).
- **Fix:** Added `server_default=text("gen_random_uuid()")` to `SenderRestrictionEvent.id` so both `create_all` and the migration give the column a DB-level default.
- **Files modified:** app/models/__init__.py
- **Commit:** cbfaaa7

**3. [Rule 1 - Bug] reconcile test mock had to evolve with the new helper call**
- **Found during:** full-suite run (4 `test_sender_restriction` reconcile tests + `test_listener_reconcile` errored on `_FakeResult` lacking `scalar_one_or_none`).
- **Fix:** Extended the test fakes to serve the new intra-tx `old_until` read and the helper's sender-row + slice SELECTs. In scope because Plan 10-02 legitimately changed the reconcile tick these mocks target.
- **Files modified:** tests/test_sender_restriction.py
- **Commit:** 78c0426

## Deferred Issues

The full suite has a wide set of PRE-EXISTING failures (asyncpg "multiple commands in a prepared statement" on multi-statement migration-file tests; phase5 inbox/bot-filter infra; onboarding/warmup) that are NOT regressions from this plan — none touch restriction-event code. They are logged in `.planning/phases/10-pool-visibility/deferred-items.md` for the verifier. Also pre-existing: `test_listener_reconcile.py::test_get_active_senders_query_shape` greps source for `is_active` and matches an unrelated comment (function untouched by 10-02).

## Known Stubs

None. The history endpoint (`test_history_endpoint`, HLTH-03) is intentionally Wave 3 (Plan 10-03), not a stub of this plan.

## Self-Check: PASSED
- FOUND: migrations/030_sender_restriction_events.sql
- FOUND: app/services/restriction_audit.py
- FOUND: commit 3d6a747
- FOUND: commit cbfaaa7
- FOUND: commit 19f5c72
- FOUND: commit 78c0426
