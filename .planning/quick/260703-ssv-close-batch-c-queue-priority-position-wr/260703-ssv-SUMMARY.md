---
phase: quick-260703-ssv
plan: 01
subsystem: queue
tags: [postgres, sqlalchemy, message-queue, priority, rate-limiting, migrations, head-of-line-blocking]

# Dependency graph
requires:
  - phase: 04-campaign-lifecycle
    provides: per-campaign scheduling + campaign_enqueue raw INSERT into message_queue
  - phase: 13-even-pacing
    provides: _process_next_for_sender pacing pre-query the long-pause branch sits ahead of
provides:
  - "message_queue.priority/attempts/as_draft DB defaults + NULL backfill (migration 047)"
  - "priority-aware NULL-safe _queue_position matching the pick order priority DESC, created_at ASC"
  - "durable non-blocking per-sender long-pause via senders.long_pause_until (migration 048)"
  - "long-pause double-fire guard (no re-trigger while a pause is active)"
affects: [queue-worker, campaign-enqueue, pool-management, any future queue/ETA work]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ORM default= vs server_default= drift fix (mig 040/042 lineage) applied to message_queue"
    - "durable DB-marker replaces inline asyncio.sleep for head-of-line-safe pausing (warmup-bug lineage)"

key-files:
  created:
    - migrations/047_message_queue_priority_default.sql
    - migrations/048_sender_long_pause_until.sql
    - tests/test_queue_position.py
    - tests/test_queue_long_pause.py
  modified:
    - app/models/__init__.py
    - app/services/campaign_enqueue.py
    - app/services/queue.py

key-decisions:
  - "long-pause double-fire guard reads long_pause_until > NOW() in SQL (DB clock, consistent with the _tick filter) rather than a Python now() comparison"
  - "campaign_enqueue names priority explicitly (defence-in-depth) even though migration 047 now defaults it"
  - "no SET NOT NULL on priority/attempts/as_draft (out of scope per FIXPLAN — defaults + backfill only)"

patterns-established:
  - "Durable per-sender pause marker: _tick excludes long_pause_until > NOW(); survives restart, no in-memory state"
  - "NULL-safe queue ordering: COALESCE(priority,0) on both sides of every comparison"

requirements-completed: [WR-02, WR-03, WR-04]

# Metrics
duration: 11min
completed: 2026-07-03
---

# Phase quick-260703-ssv: Close Batch C + Batch D (queue priority default/position + head-of-line long-pause) Summary

**message_queue.priority/attempts/as_draft get DB defaults + NULL backfill (mig 047), _queue_position is rewritten priority-aware and NULL-safe, and the 3-10 min long-pause becomes a durable non-blocking senders.long_pause_until marker (mig 048) instead of an inline asyncio.sleep that stalled every sender in every workspace.**

## Performance

- **Duration:** ~11 min
- **Started:** 2026-07-03T20:55:01Z
- **Completed:** 2026-07-03T21:05:39Z
- **Tasks:** 5
- **Files modified:** 7 (4 created, 3 modified)

## Accomplishments
- WR-02: `message_queue.priority`, `attempts`, `as_draft` now have DB defaults (0/0/false) + existing NULLs backfilled; ORM `server_default` added so a fresh/recovered DB matches; `campaign_enqueue` INSERT names `priority` explicitly. Prod NULL-priority rows: **0**.
- WR-03: `_queue_position` rewritten to COALESCE(priority,0) on both sides with an explicit "ahead" predicate (higher priority OR same-priority earlier `created_at`), fixing the inverted + NULL-blind tuple comparison. Matches the worker pick order `priority DESC, created_at ASC`.
- WR-04: inline `asyncio.sleep(long_pause)` replaced by a durable `senders.long_pause_until` marker. `_tick` excludes paused senders (restart-durable, re-read every tick); `_get_long_pause_seconds` gains a guard that returns None while a pause is active (kills the modulo double-fire); other senders keep sending.
- Empirical constants (4/20/150, LONG_PAUSE_EVERY 12-25, LONG_PAUSE 180-600s, pause_every modulo) verified byte-for-byte unchanged — mechanism-only change.
- Deployed to prod (api + listener rebuilt); migrations 047 + 048 applied cleanly by the auto-applier; live sanity checks pass.

## Task Commits

Each task was committed atomically:

1. **Task 1: Batch C schema — priority/attempts/as_draft defaults + backfill + explicit enqueue (WR-02)** — `95ef8c8` (fix)
2. **Task 2: Batch C — priority-aware NULL-safe _queue_position (WR-03)** — `d073e05` (test, RED) → `2e7eee4` (fix, GREEN)
3. **Task 3: Batch D — durable non-blocking long-pause mechanism (WR-04)** — `f5cf2a7` (fix)
4. **Task 4: Batch D — non-blocking / restart-durable / no-double-trigger tests (WR-04)** — `44981ff` (test)
5. **Task 5: Deploy to prod + live sanity check** — ops (no code); see verification below

**Plan metadata:** (final docs commit)

## Files Created/Modified
- `migrations/047_message_queue_priority_default.sql` - ALTER COLUMN SET DEFAULT + UPDATE NULL backfill for priority/attempts/as_draft (idempotent)
- `migrations/048_sender_long_pause_until.sql` - `ADD COLUMN IF NOT EXISTS senders.long_pause_until TIMESTAMPTZ` (idempotent)
- `app/models/__init__.py` - `server_default` on MessageQueue.priority/attempts/as_draft; new `Sender.long_pause_until` column
- `app/services/campaign_enqueue.py` - raw INSERT names `priority` (bound to 0)
- `app/services/queue.py` - rewritten `_queue_position`; `_tick` SELECT JOINs senders + excludes `long_pause_until > NOW()`; `_get_long_pause_seconds` double-fire guard; `_process_next_for_sender` persists durable marker + returns (no inline sleep)
- `tests/test_queue_position.py` - 3 tests: mixed/NULL priority positions, 1-based, NULL-as-zero vs positive
- `tests/test_queue_long_pause.py` - 4 tests: durable marker + no long sleep, unpaused sender stays eligible, restart-durable exclusion/re-inclusion, no double-trigger while paused

## Decisions Made
- Long-pause guard evaluates `long_pause_until > NOW()` in SQL (DB clock) so it is consistent with the `_tick` eligibility filter and immune to app/DB clock skew.
- `campaign_enqueue` names `priority` explicitly for defence-in-depth even though migration 047 now supplies the default.
- No `SET NOT NULL` added (out of scope per FIXPLAN — defaults + backfill only; ORM path always supplies a Python value).

## Deviations from Plan

None - plan executed exactly as written. (The plan's TDD flow was honored for Task 2: a RED commit `d073e05` proving the inverted/NULL-blind impl failed, then a GREEN commit `2e7eee4`.)

## Issues Encountered
None. The `test_phase5_migration_017` pooled-connection cascade noted in the plan is a pre-existing full-suite artifact and was avoided by running the targeted test files individually. All 34 targeted tests across the six touched files pass together with no new failures. The recurring postgres collation-version WARNING in psql output is pre-existing DB noise (collation 2.41 vs OS 2.36), unrelated to this work.

## Live Sanity Check (Task 5)
- Backup taken pre-deploy: `/root/backups/tg-outreach/outreach_20260703_210432.sql.gz` (1.7M).
- api startup log: `[migrate] OK 047_message_queue_priority_default`, `[migrate] OK 048_sender_long_pause_until`, `Application startup complete`.
- `schema_migrations` contains `047_message_queue_priority_default` + `048_sender_long_pause_until` (COUNT = 2).
- `message_queue` NULL-priority rows: **0** (backfill applied).
- Column defaults: `priority=0`, `attempts=0`, `as_draft=false`.
- `senders.long_pause_until` present, type `timestamp with time zone`.
- Queue worker started (`Queue worker started`, `no stuck jobs found`); no errors/tracebacks; no inline long-pause block.

## Known Stubs
None.

## Next Phase Readiness
- Batch C (WR-02, WR-03) and Batch D (WR-04) from `.planning/reviews/260703-checker-campaigns-FIXPLAN.md` are closed and live in prod.
- No blockers. Phase 20 parallel work was untouched (only this plan's files were staged per-task).

## Self-Check: PASSED

All created files present (migrations 047/048, tests test_queue_position.py + test_queue_long_pause.py, SUMMARY). All task commits present: 95ef8c8, d073e05, 2e7eee4, f5cf2a7, 44981ff.

---
*Phase: quick-260703-ssv*
*Completed: 2026-07-03*
