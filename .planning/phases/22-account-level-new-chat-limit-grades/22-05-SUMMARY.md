---
phase: 22-account-level-new-chat-limit-grades
plan: 05
subsystem: warmup
tags: [warmup, grade-ladder, new-chat-budget, shared-budget, outreach-reserve]
requires:
  - "app/services/grade_ladder.py (load_ladder, budget_for_level) — Wave 1 (22-01)"
  - "migrations/057_sender_first_contacts.sql — Wave 1 (22-01)"
  - "migrations/056_sender_grade_columns.sql (senders.current_level) — Wave 1 (22-01)"
provides:
  - "WarmupWorker._remaining_new_chat_budget — trailing-24h shared new-chat budget with outreach reserve"
  - "WarmupWorker._pick_initiator — older/more-warmed account ordering for new pairs"
  - "new-pair-only warmup budget charge + sender_first_contacts registry insert"
affects:
  - app/services/warmup.py
tech-stack:
  patterns:
    - "shared trailing-24h new-chat budget window (matches queue new-dialog cap, D-03)"
    - "outreach-priority reserve: budget = account_budget - spent_24h - pending_cold_openers (D-09)"
key-files:
  created:
    - .planning/phases/22-account-level-new-chat-limit-grades/deferred-items.md
  modified:
    - app/services/warmup.py
    - tests/test_warmup_worker.py
decisions:
  - "Committed Task 1 + Task 2 in one warmup.py commit — both are interdependent edits to _create_new_sessions / WarmupWorker and inseparable in a single file (the NEW-pair branch calls the reserve helper)."
  - "current_level defaults to 1 when NULL → smallest (level-1) new-chat budget, the safest allowance for an unbackfilled account."
metrics:
  duration: ~15m
  completed: 2026-07-08
status: complete
---

# Phase 22 Plan 05: Warmup Shared New-Chat Budget (New-Pair + Outreach Reserve) Summary

Warmup now spends the shared account new-chat grade budget only for genuinely
NEW sender pairs, behind an outreach-priority reserve, so warmup competes for
whatever daily new-chat budget outreach does not consume — and never starves it.

## What was built

- **New-pair classification (D-08):** `_create_new_sessions` loads the
  `sender_first_contacts` registry once per tick into a `set[frozenset]` (same
  shape as `active_pairs`). A pair already present → KNOWN → session created free
  (unchanged behaviour). A pair absent → NEW → only opened when the initiator has
  remaining budget, and on creation the canonical `(LEAST,GREATEST)` pair is
  inserted `ON CONFLICT DO NOTHING` (matching migration 057's PK invariant), so
  every future repeat of that pair is free.
- **Initiator ordering (D-08):** `_pick_initiator` selects the older/more-warmed
  account (greater `enrolled_days`, tie-break earlier `enrolled_at`) and reorders
  the pair so the initiator is `sender_a` — `_process_session`'s NEW-session
  branch (`last_sender_id IS NULL`) then makes `sender_a` write first.
- **Outreach-priority reserve on the trailing-24h shared budget (D-09/D-03):**
  `_remaining_new_chat_budget(db, sender_id, workspace_id, current_level)` returns
  `max(0, account_budget - spent - pending)` where `account_budget` comes from the
  workspace ladder (`load_ladder`/`budget_for_level`, code-default 5/9/13), `spent`
  is the sender-wide DISTINCT recipient count sent in the trailing-24h
  (`finished_at >= NOW() - INTERVAL '24 hours'` — the SAME window the 22-03 queue
  cap uses, RESEARCH Pitfall 4), and `pending` is the DISTINCT cold openers still
  queued for this sender (campaign message with no prior sent to that phone),
  bounded to `account_budget` so a large backlog zeroes warmup without driving the
  arithmetic negative.
- **`_get_active_pool`** now also selects `s.current_level` (default 1) so the
  new-pair budget check has the initiator's grade level with no extra round-trip.

## Tests (tests/test_warmup_worker.py)

- `-k pair`: NEW pair charges initiator + inserts registry row + initiator is
  `sender_a`; KNOWN pair warms free with no second registry row (even at zero
  budget); backfilled pair (migration-057 canonical backfill re-run) classified
  KNOWN, not re-charged.
- `-k reserve`: 5 pending cold openers (level-1 budget = 5) starve the new pair
  (no session, no registry row); 4 pending frees it; trailing-24h spent window
  proven (send 30h ago ignored, send 2h ago spends 1).
- Result: `pytest tests/test_warmup_worker.py -k "pair or reserve"` → **6 passed**.
  Full file: **9 passed, 1 failed** (pre-existing WARM-14 RED guard — see Deviations).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] UUID cast on the registry INSERT (and test helpers)**
- **Found during:** Task 1 test run
- **Issue:** `LEAST(:a, :b)` with text-typed binds hit `column "sender_a_id" is of
  type uuid but expression is of type text`; the `:a::uuid` fix then broke on
  SQLAlchemy `text()` colon-bind parsing (`syntax error at or near ":"`).
- **Fix:** used `LEAST(CAST(:a AS uuid), CAST(:b AS uuid))` / `GREATEST(...)` in
  both `app/services/warmup.py` and the test helpers.
- **Files modified:** app/services/warmup.py, tests/test_warmup_worker.py
- **Commit:** 02abeb5 (impl), 197aa25 (tests)

**2. [Rule 3 - Blocking] Test helper missing NOT-NULL `item_type`**
- **Found during:** Task 3 test run
- **Issue:** `message_queue.item_type` is an ORM `default=` (no DB server_default,
  the known ORM-default drift), so a raw INSERT omitting it → NotNullViolation.
- **Fix:** the `_queue_row` test helper now inserts `item_type = 'message'`.
- **Commit:** 197aa25

### Deferred (out of scope)

**`test_restricted_sender_excluded` (WARM-14) is RED — pre-existing, not 22-05.**
It asserts `spam_limited` senders are excluded from `_get_active_pool`, but the
shipped implementation intentionally *includes* them (Phase 15 D-14: warmup is
trust-recovery for spam-limited accounts). Plan 22-05 only added `s.current_level`
to the SELECT — the restriction clause was untouched. Logged to
`deferred-items.md`.

## Infra note (not a code change)

The test-overlay's `api` service inherits a fixed `container_name` prod `db`
dependency; from a worktree (separate compose project) `run --rm api` collides
with the running prod `outreach-platform-db`. Ran tests via
`up -d db-test` + `run --rm --no-deps api pytest` and copied the gitignored `.env`
into the worktree for compose variable interpolation. Ephemeral `db-test` +
volume removed after; prod DB untouched.

## Known Stubs

None.

## Verification

- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --no-deps api pytest tests/test_warmup_worker.py -k "pair or reserve" -x` → 6 passed.
- `grep "INTERVAL '24 hours'"` present in BOTH `app/services/warmup.py` and
  `app/services/queue.py` — the two workers agree on the trailing-24h spent window.

## Self-Check: PASSED

- Files: app/services/warmup.py, tests/test_warmup_worker.py, 22-05-SUMMARY.md,
  deferred-items.md — all present.
- Commits: 02abeb5 (feat), 197aa25 (test) — both in git log.
- Both warmup.py and queue.py contain the trailing-24h window.
