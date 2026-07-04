---
phase: 260704-buq
plan: 01
subsystem: api
tags: [campaigns, queue, enqueue-worker, message_queue, campaign_contact_assignments, lifecycle, postgres]

# Dependency graph
requires:
  - phase: 04-campaign-model
    provides: CampaignEnqueueWorker, queue dispatcher (_fail_item / _process_next_for_sender / _tick), campaigns router + CampaignResponse
  - phase: 08-pool-management
    provides: _check_sender_lock + attach_sender + pool_health aggregate
  - phase: 10-pool-visibility
    provides: _compute_pool_health, restriction_status/auth_status/lifecycle_status columns
provides:
  - WR-09 status-gated enqueue INSERT (no zombie pending on finished campaigns)
  - IN-11 per-campaign try/except isolation in the enqueue tick
  - WR-12a cold-terminal-fail CCA release (contact re-eligible)
  - WR-12b POST /campaigns/{id}/requeue-failed + failed_count on CampaignResponse
  - IN-05 attach-only sender-lock check (only_sender_id)
  - IN-06 duplicate_campaign IntegrityError 409 backstop
  - IN-07 past_stop_date status='pending' guard + failure callback
  - IN-10 pool_health.active excludes session_expired / lifecycle-paused senders
  - IN-12 dispatcher messages logged sent_by='ai'
affects: [campaigns, queue, inbox-attribution, pool-health]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Status-gated INSERT ... SELECT ... WHERE EXISTS re-asserts campaign state at insert time (no read-time snapshot trust)"
    - "Per-item worker loop isolation: try/except + rollback + continue so one bad row/campaign never starves the rest"
    - "Extracted _fail_past_stop_date_items helper shared by both stop_date fail sites (DRY + directly testable)"

key-files:
  created:
    - tests/test_queue_lifecycle_fixes.py
    - tests/test_campaign_lifecycle_fixes.py
  modified:
    - app/services/campaign_enqueue.py
    - app/services/queue.py
    - app/routers/campaigns.py
    - app/schemas/__init__.py
    - tests/test_campaign_enqueue_worker.py

key-decisions:
  - "Deployed prod api by building the image from a git-archive of HEAD (committed state) rather than the live working tree, because the prod Dockerfile bakes source (no volume mount) and parallel agents had uncommitted edits mid-flight — this shipped only committed code."
  - "Extracted _fail_past_stop_date_items helper rather than inlining the guard+callback twice (both stop_date fail sites now share one tested code path)."

patterns-established:
  - "WHERE EXISTS (SELECT 1 FROM campaigns WHERE id=:cid AND status='running') gate on worker inserts"
  - "read-time COUNT(*) computed field (failed_count) instead of a stored column / migration"

requirements-completed: [WR-09, WR-12, IN-05, IN-06, IN-07, IN-10, IN-11, IN-12]

# Metrics
duration: 47min
completed: 2026-07-04
---

# Phase 260704-buq Plan 01: Batch E Campaign Lifecycle Fixes Summary

**Eight campaign-lifecycle correctness fixes across the enqueue worker, queue dispatcher and campaigns router — status-gated enqueue (no zombie pending), cold-fail contact release + requeue-failed endpoint, attach-scoped sender lock, duplicate 409 backstop, stop-date guard+callback, truthful pool_health.active, and correct AI-vs-human attribution — deployed to prod and live-verified.**

## Performance

- **Duration:** ~47 min
- **Started:** 2026-07-04T08:39:42Z
- **Completed:** 2026-07-04T09:26Z
- **Tasks:** 4
- **Files modified:** 4 source + 3 test (2 new)

## Accomplishments
- **WR-09 + IN-11** (enqueue worker): the per-contact INSERT now re-asserts `campaigns.status='running'` via `INSERT ... SELECT ... WHERE EXISTS`, so a campaign flipped to done/stopped between the tick snapshot and the commit gets 0 queue rows; the per-campaign body is wrapped in try/except+rollback+continue so one campaign raising no longer starves the rest of the tick.
- **WR-12a + IN-07 + IN-12** (queue dispatcher): a cold terminal fail (no prior 'sent' for the campaign+phone) deletes the sticky CCA in the same transaction so the contact is eligible again; the past_stop_date fail path is guarded with `AND status='pending'` (never clobbers a concurrent cancel) and fires a per-item `status='failed'` callback; dispatcher-written outbound messages are logged `sent_by='ai'`.
- **WR-12b + IN-05 + IN-06 + IN-10** (campaigns router + schema): new `POST /campaigns/{id}/requeue-failed` re-pends failed rows and returns `{requeued_count}`, plus a read-time `failed_count` on `CampaignResponse`; `attach_sender` narrows the lock check to the newly-attached sender; `duplicate_campaign` translates a unique-index IntegrityError into 409; `pool_health.active` now requires `auth_status='ok' AND lifecycle_status='active'` in addition to `restriction_status='none'`.
- **Deployed** the api container (rebuilt, listener untouched per plan) — clean boot, migration 049 applied, all workers started, no traceback. `requeue-failed` + `failed_count` + `requeued_count` confirmed on the live openapi.json.

## Task Commits

1. **Task 1: Enqueue-worker lifecycle safety (WR-09 + IN-11)** — `6947393` (fix, tdd)
2. **Task 2: Queue dispatcher fixes (WR-12a/IN-07/IN-12)** — `fbf75e6` (fix, tdd)
3. **Task 3: Campaigns router + schema (WR-12b/IN-05/06/10)** — `88bf741` (fix, tdd)
4. **Task 4: full suite + deploy + WR-09 remediation + live verify** — build/ops/verify (no source edits); plan+summary metadata commit follows.

## Files Created/Modified
- `app/services/campaign_enqueue.py` — WR-09 status-gated INSERT; IN-11 per-campaign try/except in `_tick`.
- `app/services/queue.py` — WR-12a CCA release in `_fail_item`; IN-07 `_fail_past_stop_date_items` helper (status guard + callback) wired into both fail sites; IN-12 `sent_by='ai'`.
- `app/routers/campaigns.py` — WR-12b requeue-failed endpoint + failed_count compute; IN-05 `only_sender_id`; IN-06 duplicate IntegrityError 409; IN-10 active predicate.
- `app/schemas/__init__.py` — `failed_count: int = 0` on `CampaignResponse`.
- `tests/test_campaign_enqueue_worker.py` — +2 tests (WR-09 no-op, IN-11 continue-on-error).
- `tests/test_queue_lifecycle_fixes.py` — NEW, 7 tests (WR-12a cold/warm/null-campaign, IN-07 guard + callback ×2, IN-12).
- `tests/test_campaign_lifecycle_fixes.py` — NEW, 7 tests (WR-12b requeue + 404 + failed_count, IN-05 ok + 409, IN-06 409, IN-10 active).

## Test Results
- Task-targeted test-overlay runs all GREEN: `test_campaign_enqueue_worker.py` 16 passed; `test_queue_lifecycle_fixes.py` + `test_queue_workspace_id.py` 11 passed; `test_campaign_lifecycle_fixes.py` + `test_campaign_router.py` 31 passed.
- Cross-section combined run (my 3 files + the full-suite "failing" cluster files) — **71 passed** in one invocation, proving those files are healthy.

## WR-09 Prod Remediation
- Ran the one-off `UPDATE message_queue SET status='cancelled' ... WHERE status='pending' AND campaign_id IN (SELECT id FROM campaigns WHERE status NOT IN ('running','paused'))` via `psql -c`.
- **Result: UPDATE 0** — no zombie pending rows currently exist (the FIXPLAN's single 2026-07-03 row is already gone; the now-deployed WR-09 gate prevents new ones). The statement is idempotent.

## Deploy Confirmation
- api container rebuilt and recreated from a **git-archive of HEAD** image (see Deviations); listener NOT rebuilt (does not import the changed modules — per plan).
- Boot log clean: `[migrate] OK 049_account_profile`, `Database initialized`, `CampaignEnqueueWorker started`, `Queue worker started`, `Application startup complete`, no traceback.
- Live openapi.json (`http://127.0.0.1:8005/openapi.json`, HTTP 200): `requeue-failed`, `failed_count`, `requeued_count` all present.
- `CampaignEnqueueWorker` running with no per-campaign error spam; no new tracebacks/errors post-deploy.

## Decisions Made
- **Deploy from committed HEAD, not the working tree.** The prod api `Dockerfile` bakes source (no volume mount), and parallel agents (Phase 20 + batches G/H) had uncommitted edits in the shared repo. Building `docker compose up --build` would have shipped their unfinalized work. Built `tg-outreach-api:latest` from `git archive HEAD | tar -x` (read-only on git, non-disruptive to the parallel agents) and recreated the container with `docker compose up -d --no-build api`, guaranteeing only committed code shipped.
- **Extracted `_fail_past_stop_date_items`** shared by `_tick` and `_process_next_for_sender` instead of inlining the guard+callback twice — single tested code path.

## Deviations from Plan

### Auto-fixed / judgment adjustments

**1. [Rule 3 - Blocking] Deploy built from HEAD archive instead of `docker compose up -d --build api`**
- **Found during:** Task 4 (deploy)
- **Issue:** The prod api Dockerfile bakes source with no volume mount; the shared working tree contained parallel agents' uncommitted, in-flight edits (`onboarding.py`, `senders.py`, `telegram.py`, `schemas/__init__.py`). The plan's `--build` command would have baked that unfinalized work into the prod image.
- **Fix:** `git archive HEAD | tar -x` into a temp context, `docker build -t tg-outreach-api:latest`, then `docker compose up -d --no-build api`. Same net effect as the plan's command (my committed fixes go live) but shipping only committed code. Non-disruptive to the parallel agents (no stash / no working-tree mutation).
- **Verification:** Boot log clean; migration 049 applied; openapi.json exposes the new endpoint+field; `grep` confirmed my fixes present in the archived tree (WHERE EXISTS ×2, requeue-failed ×3).
- **Committed in:** N/A (build/ops step, no source change).

---

**Total deviations:** 1 (deploy method adjusted for parallel-agent safety). No scope creep; all eight source fixes implemented exactly per spec.

## Issues Encountered
- **Full test-overlay suite not cleanly green due to shared-infrastructure contention (NOT a code defect).** Repeated full-suite runs failed with `sqlalchemy` schema errors (`relation "warmup_sessions" does not exist`, InternalError/ProgrammingError) clustered entirely in files I did not touch (`test_send`, `test_sender_lock`, `test_rotation_campaign`, `test_restriction_audit`, `test_senders`, `test_spambot_selfcheck`, `test_warmup_isolation`). Root cause: parallel agents (batches G/H) run their own test-overlay against the single shared `tg-outreach-db-test-1` container, and their `DROP SCHEMA public CASCADE` in `conftest._setup_database` clobbers my run's schema mid-suite. Proven conclusively: those exact "failing" files ALL PASS when run together with my files in a single invocation (71 passed) — a file cannot pass and fail on its own code; the variable is a competing `DROP SCHEMA`. My changes are green.
- Pre-existing DB collation-version warning (`collation version 2.41 vs 2.36`) surfaces on `psql` — OS libc mismatch, unrelated to this task, not addressed.

## User Setup Required
None — no external service configuration required.

## Next Phase Readiness
- All Batch E findings (WR-09, WR-12a, WR-12b, IN-05, IN-06, IN-07, IN-10, IN-11, IN-12) closed, tested, deployed, and live-verified.
- Note for reviewers: the shared `tg-outreach-db-test-1` container is a contention point when multiple agents run test-overlays simultaneously; a full clean-suite confirmation should be re-run once parallel work quiesces.

---
*Phase: 260704-buq*
*Completed: 2026-07-04*

## Self-Check: PASSED
- All 7 source/test files + PLAN.md + SUMMARY.md present on disk.
- Task commits `6947393`, `fbf75e6`, `88bf741` all present in git history.
