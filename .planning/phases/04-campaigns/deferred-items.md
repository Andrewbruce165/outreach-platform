# Phase 4 — Deferred Items

## Logged by Plan 04-02 (Wave 2)

### app/services/rotation.py references DROPPED context_contact_assignments

**File:** `app/services/rotation.py:59,89,122,138`
**Issue:** Migration 016 drops `context_contact_assignments`. After this migration applies,
`rotation.get_or_assign_sender()` will fail at runtime because it queries the dropped table.

**Why deferred:** 04-04 Plan (Wave 3) owns the rotation.py rewrite per AUDIT TODO #6
(change signature `context_id` → `campaign_id`, source pool from `campaign_senders` instead
of global workspace senders). Rewriting rotation.py here would conflict with parallel work
in 04-03 (queue.py per-campaign hours) and overlap with 04-04 scope.

**Test impact:** None in 04-02 — Plan 04-02 tests do NOT invoke rotation. Send.py code path
that uses rotation will fail at runtime but is out of scope for 04-02.

**Action:** 04-04 must include rotation.py rewrite as a Task. (Already in AUDIT.md TODO #6.)
