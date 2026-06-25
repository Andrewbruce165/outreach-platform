---
phase: 12-per-campaign-daily-new-dialog-limit-max-new-dialogs-per-day
plan: 02
subsystem: queue
tags: [queue, rate-limit, new-dialog-cap, candidate-select, integration-test]
requires:
  - campaigns.max_new_dialogs_per_day column (plan 12-01, migration 033 + ORM)
  - QueueWorker._process_next_for_sender candidate SELECT (Phase 4, queue.py)
provides:
  - Per-(sender,campaign) new-dialog cap enforced in the candidate SELECT WHERE clause
  - tests/test_queue_new_dialog_limit.py (4 integration tests via test-overlay)
affects:
  - 12-03 (API exposes/enforces ge=1/le=100 bounds on the same column the worker reads)
tech-stack:
  added: []
  patterns:
    - "SQL-side enforcement (WHERE predicate) — single source of truth, no Python-side cap logic"
    - "correlated EXISTS (follow-up classifier) OR correlated COUNT(DISTINCT) < cap (new-dialog gate)"
    - "raw INSERT naming finished_at to seed in-window sent rows (factory omits finished_at)"
key-files:
  created:
    - tests/test_queue_new_dialog_limit.py
  modified:
    - app/services/queue.py
decisions:
  - "D-07: cap enforced in _process_next_for_sender (per-item candidate filter), NOT _check_rate_limits (per-tick gate that would block follow-ups too)"
  - "D-06/D-08: follow-up = recipient_phone with ANY prior status='sent' in THIS campaign (no time bound); always eligible regardless of cap"
  - "D-09: empirical 4/20/150 + MAX_NEW_CONTACTS_PER_HOUR=15 and the whole _check_rate_limits body byte-for-byte unchanged (git diff scoped to _process_next_for_sender only)"
  - "D-01/D-02/D-05: new dialog excluded once COUNT(DISTINCT recipient_phone) of sent rows in (sender,campaign) over trailing 24h >= max_new_dialogs_per_day"
metrics:
  duration: ~5min
  tasks: 2
  files: 2
  completed: 2026-06-25
---

# Phase 12 Plan 02: Per-Campaign New-Dialog Cap in the Queue Worker Summary

Enforced the per-(sender,campaign) daily new-dialog cap inside the candidate
`SELECT` of `QueueWorker._process_next_for_sender`: new-dialog items (no prior
`status='sent'` to that `recipient_phone` in the campaign) are excluded once the
`(sender,campaign)` has opened `>= campaigns.max_new_dialogs_per_day` unique new
dialogs in the trailing 24h, while follow-up / re-contact items stay eligible —
`_check_rate_limits` and the empirical constants are untouched.

## What Was Built

### Task 1 — New-dialog filter in `_process_next_for_sender` (`app/services/queue.py`)
- Added a single `AND ( ... )` predicate to the existing candidate SELECT (the
  one that JOINs `campaigns`, `ORDER BY priority/created_at LIMIT 8
  FOR UPDATE OF mq SKIP LOCKED`):
  - **Follow-up branch** — `EXISTS (SELECT 1 FROM message_queue prior WHERE
    prior.campaign_id = mq.campaign_id AND prior.recipient_phone =
    mq.recipient_phone AND prior.status = 'sent')` — never blocked (D-06/D-08).
  - **New-dialog branch** — `(SELECT COUNT(DISTINCT opened.recipient_phone) ...
    WHERE opened.sender_id = mq.sender_id AND opened.campaign_id =
    mq.campaign_id AND opened.status='sent' AND opened.finished_at >= NOW() -
    INTERVAL '24 hours') < c.max_new_dialogs_per_day` (D-01/D-02/D-05).
- Added the prescribed comment above the SELECT documenting NDLG-02 / D-07/D-08/D-09.
- `_check_rate_limits` (lines 363+) and the module constants
  (`MAX_NEW_CONTACTS_PER_HOUR = 15`, MIN/MAX_SEND_INTERVAL, LONG_PAUSE_*, 4/20/150)
  are byte-for-byte unchanged — `git diff app/services/queue.py` is two pure
  insertion hunks, both inside `_process_next_for_sender`.
- `LIMIT 8 / FOR UPDATE OF mq SKIP LOCKED` preserved; the Python working-window /
  stop_date post-filter loop is unchanged (excluded new dialogs simply never
  enter the candidate set).
- Commit: `28f6329`

### Task 2 — Integration test (`tests/test_queue_new_dialog_limit.py`)
- Drives the **real** `_process_next_for_sender` (mocks `_check_rate_limits`→True
  to isolate the cap, `_get_long_pause_seconds`→None, `_send_item`→capture id).
- `test_new_dialog_blocked_when_cap_reached`: cap=2, 2 distinct sent dialogs
  seeded; a 3rd never-contacted new-dialog item is NOT picked, stays `pending`.
- `test_followup_eligible_when_cap_reached`: cap reached; a re-contact item to a
  phone with a prior sent IS selected (transitions to `processing`) — D-06/D-08.
- `test_new_dialog_allowed_under_cap`: 1 sent dialog (under cap=2) → fresh new
  dialog IS selected.
- `test_check_rate_limits_untouched`: `MAX_NEW_CONTACTS_PER_HOUR == 15` and
  `inspect.getsource(_check_rate_limits)` has no `max_new_dialogs_per_day` (D-09).
- Sent rows seeded via raw INSERT naming `finished_at` (the conftest
  `test_queue_item_factory` omits `finished_at`, so factory rows would leave the
  24h COUNT at 0 and the cap would never fire). Each cap test asserts the
  in-window distinct-sent COUNT equals the seeded count before exercising the worker.
- The cap value is set with a post-create `UPDATE campaigns SET
  max_new_dialogs_per_day=2` — `test_campaign_factory` does not accept the column
  as a kwarg (its INSERT column list is fixed; the column defaults to 50).
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api
  pytest tests/test_queue_new_dialog_limit.py` → **4 passed**.
- Commit: `dbbd3d7`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `_send_item` receives a UUID, test compared against a str**
- **Found during:** Task 2 (first test run — `test_followup_eligible_when_cap_reached` failed on `'…' in [UUID('…')]`).
- **Issue:** the captured `item_id` from the SELECT is a `uuid.UUID`; the inserted ids are `str`, so the `in` membership check failed despite the follow-up being correctly selected.
- **Fix:** `captured["picked"].append(str(item_id))` in the test's fake `_send_item`.
- **Files modified:** tests/test_queue_new_dialog_limit.py
- **Commit:** dbbd3d7

**2. [Rule 3 - Blocking] Cap value cannot be passed as a factory kwarg**
- **Found during:** Task 2 (the planned `test_running_campaign_factory(..., max_new_dialogs_per_day=2)` would raise `TypeError` — the conftest factory's parameter list / INSERT columns are fixed and do not include this column).
- **Fix:** create the running campaign normally (column defaults to 50 via migration 033 / ORM `server_default`), then `UPDATE campaigns SET max_new_dialogs_per_day=2 WHERE id=:cid` via a `_set_cap` helper before seeding.
- **Files modified:** tests/test_queue_new_dialog_limit.py
- **Commit:** dbbd3d7

### Note on the test-overlay run location
The test-overlay mounts `./tests` and `./app` from the **main** repo dir
(`/root/apps/aimly/tg-outreach`), not the worktree. To execute the suite the two
changed files were temporarily copied into the main checkout for the run, then
the main checkout was restored to a clean state (`git checkout -- queue.py`,
`rm tests/test_queue_new_dialog_limit.py`). The committed source of truth lives
in the worktree only; the main checkout was left clean.

## Pre-existing (out-of-scope) test failures
`tests/test_queue_per_campaign_hours.py` has 5 failures
(`test_queue_skips_done_campaign_items`, `…skips_past_stop_date`,
`…skips_before_start_date`, `…respects_per_campaign_working_hours`,
`…respects_work_days_mask`). Verified these fail **identically against the
unmodified `queue.py`** (HEAD `5688b52`), so they are pre-existing and unrelated
to this change. Not fixed (scope boundary); the plan note flagged ~50 pre-existing
failures on main.

## Verification
- `grep -c max_new_dialogs_per_day app/services/queue.py` = 2 (predicate + comment).
- `grep -c "INTERVAL '24 hours'" app/services/queue.py` = 1 (new occurrence, inside `_process_next_for_sender`; `_check_rate_limits` uses Python-timedelta params, so baseline was 0).
- `grep -c "FOR UPDATE OF mq SKIP LOCKED"` = 1 (unchanged).
- `MAX_NEW_CONTACTS_PER_HOUR = 15` intact; `git diff` touches only `_process_next_for_sender`.
- `python -c "import ast; ast.parse(...)"` exit 0 for both files.
- `grep -c "def test_" tests/test_queue_new_dialog_limit.py` = 4; `finished_at` ≥ 1; `max_new_dialogs_per_day` ≥ 1; follow-up reference ≥ 1.
- test-overlay: 4 passed.

## Self-Check: PASSED

- FOUND: app/services/queue.py (max_new_dialogs_per_day predicate present)
- FOUND: tests/test_queue_new_dialog_limit.py
- FOUND commit 28f6329 (Task 1)
- FOUND commit dbbd3d7 (Task 2)
