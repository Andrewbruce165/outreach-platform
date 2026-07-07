---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 05
subsystem: api
tags: [postgres, message-queue, campaign-enqueue, file-attachment, rerender]

# Dependency graph
requires:
  - phase: 24-02 (data-model-migration-schemas)
    provides: campaign_attachments blob table, MessageQueue.item_type/caption columns, QueueItemType enum, migration 054
provides:
  - Enqueue worker emits ONE item_type='file' row per contact (caption=rendered opener) when a campaign has an attachment (D-05); still one send / one new-dialog (D-18, limits unchanged)
  - Attachment presence resolved ONCE per campaign per tick (single SELECT 1 FROM campaign_attachments), not per contact
  - rerender_pending_queue re-renders caption + message_text of pending item_type='file' rows on template edit (D-17), preserving in-flight safety
  - Enqueue snapshot stays clean text — variation is deferred to send time (D-14, Plan 24-06)
affects: [24-06 (send-time file dispatch + variation), campaign-send-path]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-campaign-per-tick attachment presence resolution (single SELECT, item_type computed once before the contacts loop)"
    - "caption mirrors message_text for file rows so inbox/log stay readable; message rows keep caption NULL via a CASE in the rerender UPDATE"

key-files:
  created: []
  modified:
    - app/services/campaign_enqueue.py
    - tests/test_campaign_enqueue_worker.py
    - tests/test_rerender_pending_queue.py

key-decisions:
  - "caption is the source of truth for the file caption; message_text mirrors the clean opener (D-05) — both set to the same rendered text at enqueue"
  - "file_url is NOT set at enqueue — the blob lives in campaign_attachments and the send worker loads it by campaign_id (RESEARCH §4)"
  - "Reproduced current-main INSERT structure (INSERT ... SELECT ... WHERE EXISTS + rowcount guard, WR-09) in the stale worktree so the orchestrator cherry-pick applies cleanly"

patterns-established:
  - "Rerender caption CASE: SET caption = CASE WHEN item_type='file' THEN :txt ELSE caption END — message rows never gain a caption"

requirements-completed: [D-05, D-17, D-18]

# Metrics
duration: 12min
completed: 2026-07-07
---

# Phase 24 Plan 05: Enqueue File Opener + Rerender Summary

**Enqueue worker emits a single file-opener queue row (item_type='file', caption=rendered opener) per contact when a campaign has an attachment, counting as one send/one new-dialog; rerender_pending_queue now propagates template edits to pending file-row captions — enqueue snapshot stays clean text (variation deferred to send).**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-07-07T15:39:00Z
- **Completed:** 2026-07-07T15:51:00Z
- **Tasks:** 2 (both TDD)
- **Files modified:** 3

## Accomplishments
- Attachment-aware enqueue: campaign with a `campaign_attachments` row → one `item_type='file'` row per contact, `caption == message_text == rendered opener`, `status='pending'` (D-05/D-18). One row per contact preserved — rate limits and new-dialog cap unchanged.
- Attachment presence resolved ONCE per campaign per tick (`SELECT 1 FROM campaign_attachments WHERE campaign_id=`), not per contact.
- Campaign WITHOUT an attachment enqueues `item_type='message'`, `caption NULL` — no behavior change.
- `rerender_pending_queue` widened to `item_type IN ('message','file')`; UPDATE sets `message_text` always and `caption` only for file rows via a CASE, preserving the per-row `WHERE id=:id AND status='pending'` in-flight guard (D-17).

## Task Commits

Each task committed atomically (TDD RED → GREEN):

1. **Task 1 RED: failing test for file-opener enqueue** - `a205c22` (test)
2. **Task 1 GREEN: enqueue file-opener row when campaign has attachment** - `437ec61` (feat)
3. **Task 2 RED: failing test for rerender of pending file-row captions** - `08abe52` (test)
4. **Task 2 GREEN: rerender pending file-row captions on template edit** - `ac3b313` (feat)

_Plan metadata commit (SUMMARY/STATE/ROADMAP) is handled by the execute-phase orchestrator after cherry-pick._

## Files Created/Modified
- `app/services/campaign_enqueue.py` - `_tick_one_campaign`: per-campaign `has_attachment`/`item_type` resolution + `item_type`/`caption` parametrized INSERT; `rerender_pending_queue`: widened SELECT + caption CASE in UPDATE
- `tests/test_campaign_enqueue_worker.py` - tests: file campaign → item_type='file' + caption==message_text per contact; message campaign → item_type='message' + caption NULL
- `tests/test_rerender_pending_queue.py` - test: template edit re-renders pending file-row caption AND message_text; message rows keep caption NULL; in-flight (non-pending) file row untouched

## Decisions Made
- **caption == message_text for file rows** (D-05): caption is the source of truth for the Telegram file caption; message_text mirrors the clean opener so inbox/log stay readable. Both bound to the same `rendered` value at enqueue.
- **file_url not set at enqueue**: the blob lives in `campaign_attachments`; the send worker (24-06) loads it by `campaign_id` — avoids duplicating the blob into the queue row.
- **INSERT structure matched current main**: the worktree base predates Phase 24 (stale, phase-19), so its `_tick_one_campaign` still used the old `VALUES(...)` INSERT. I reproduced main's `INSERT ... SELECT ... WHERE EXISTS` + `rowcount == 1` guard (WR-09) and layered my `item_type`/`caption` changes on top, so the orchestrator's cherry-pick onto real main applies cleanly.

## Deviations from Plan

None - plan executed exactly as written. Both tasks followed the plan's `<action>` blocks and acceptance criteria.

## Issues Encountered
- **Stale worktree base (phase-19).** The parallel executor's worktree branched from a commit predating all Phase 24 wave-1 artifacts — migration 054, the `CampaignAttachment` model, and the `campaign_attachments` table were absent, so the tests could not run as-is. Resolution: added migration 054 + a conftest 054-apply block **locally as verification-only scaffolding** (never committed — reverted after the run), so the ephemeral test DB had `campaign_attachments`. Only the three task files are committed; the orchestrator cherry-picks them onto real main where those dependencies already exist.
- **Docker container-name conflict.** The base compose `db` service uses a fixed `container_name` (`outreach-platform-db`) that collides with the running prod DB. Ran `docker compose ... up -d db-test` then `run --rm --no-deps api pytest` (with `--env-file` pointing at the main repo's `.env`, absent from the worktree) to isolate the ephemeral test DB.

## Verification
- `pytest tests/test_campaign_enqueue_worker.py` → **16 passed**
- `pytest tests/test_rerender_pending_queue.py tests/test_campaign_enqueue_worker.py` → **25 passed**
- Acceptance greps all match: `:item_type` + `caption` in INSERT, `SELECT 1 FROM campaign_attachments`, `item_type IN ('message','file')`, `CASE WHEN item_type = 'file'`.
- Empirical queue intervals / rate-limit constants untouched (CLAUDE.md guard) — only INSERT columns and the rerender filter changed.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Ready for Plan 24-06 (send-time file dispatch + invisible variation): the queue now carries `item_type='file'` rows with a clean-text caption; the send worker loads the blob from `campaign_attachments` by `campaign_id` and applies variation (D-14) at send time.
- No blockers.

## Self-Check: PASSED

All committed files exist; all four task commit hashes (`a205c22`, `437ec61`, `08abe52`, `ac3b313`) present in git history.

---
*Phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation*
*Completed: 2026-07-07*
