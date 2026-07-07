---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 06
subsystem: api
tags: [queue-worker, telethon, variation, campaign-attachments, invisible-unicode, inbox-media, postgres]

# Dependency graph
requires:
  - phase: 24-01
    provides: app/services/variation.py::vary (pure invisible-variation module)
  - phase: 24-02
    provides: CampaignAttachment model + campaigns.variation_enabled (migration 054)
  - phase: 24-03
    provides: telegram.send_file(file_bytes=, force_document=) blob source + auto-media + overflow
provides:
  - Send-time variation gate in the queue worker (vary() on a LOCAL copy of the opener text/caption, DB never mutated)
  - Campaign file-opener blob delivery (load blob by campaign_id → send_file(file_bytes, force_document=False) with varied caption)
  - Media-typed inbox messages row for file openers (message_type + file_name/mime_type/size_bytes)
  - Migration 055 — messages media columns (bridges the never-executed Phase 23 mig 053)
affects: [24-07, phase-23-edit-delete-file-sending, inbox-media-rendering]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Variation applied to a local copy at send time only — DB (message_queue + messages + messages_log) stays byte-clean (D-14)"
    - "Feature flag read at send time (SELECT campaigns.variation_enabled) so toggling reaches already-pending rows"
    - "result['media'] side-channel carries file-opener media metadata from the send branch into _upsert_conversation's inbox INSERT"
    - "Extension→auto-media classification mirrors telegram.send_file force_document=False (photo/video/document, never voice)"

key-files:
  created:
    - migrations/055_messages_media_columns.sql
    - tests/test_queue_variation.py
    - tests/test_queue_file_opener.py
  modified:
    - app/services/queue.py
    - tests/conftest.py

key-decisions:
  - "vary() is applied to text_to_send / caption_to_send local vars only; item.message_text/caption and the messages/messages_log rows are never written varied (D-14)"
  - "variation gate = campaign_id set AND not follow-up AND variation_enabled=true, read via the existing per-item campaign SELECT (D-12)"
  - "file openers with campaign_id + file_url NULL load the blob from campaign_attachments and send as auto-media (force_document=False); missing-attachment/legacy file_url falls back to the URL path (no crash)"
  - "Added migration 055 to bridge the Phase 23 mig-053 gap — Phase 23 was never executed, so messages media columns were absent; idempotent so a future Phase 23 migration coexists"

patterns-established:
  - "Pattern: send-time feature flags read from campaigns, applied to a throwaway local copy — persistence path untouched"
  - "Pattern: idempotent bridge migration + exists-guarded conftest apply for a raw-SQL table (messages has no ORM model)"

requirements-completed: [D-05, D-06, D-08, D-12, D-14, D-16]

# Metrics
duration: ~30min
completed: 2026-07-07
---

# Phase 24 Plan 06: Worker Variation & Blob Delivery Summary

**Send-time invisible variation on a clean-DB local copy of the opener (gated on campaign + not-followup + flag) plus campaign file-opener blob delivery as auto-media with a media-typed inbox row.**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-07-07T15:45Z
- **Completed:** 2026-07-07T16:00Z
- **Tasks:** 2 (both TDD)
- **Files modified:** 5 (2 created tests, 1 migration, queue.py, conftest.py)

## Accomplishments
- Variation gate wired into the queue worker: `vary()` runs per send on a local copy of the opener text AND caption, strictly gated (campaign + not-followup + variation_enabled), fresh per send (D-12/D-16); `message_queue.message_text/caption`, the `messages` inbox row and `messages_log` all stay byte-clean (D-14).
- Campaign file-opener blob delivery: the file branch loads the blob from `campaign_attachments` by `campaign_id` and calls `send_file(file_bytes=…, force_document=False, caption=<varied>)` (D-05/D-06/D-08); defensive URL fallback preserved (no crash).
- Inbox fidelity: file-opener `messages` rows carry the concrete `message_type` (photo/video/document from the attachment extension) + `file_name`/`mime_type`/`size_bytes`, so the inbox renders a media bubble; text openers keep `message_type='text'`.

## Task Commits

Each task was committed atomically (TDD RED→GREEN):

1. **Task 1 (RED): variation gate tests** - `0a8abac` (test)
2. **Task 1 (GREEN): send-time variation on local copy** - `6816cb3` (feat)
3. **Task 2 (RED): file-opener tests + migration 055 bridge** - `9704ed3` (test)
4. **Task 2 (GREEN): blob auto-media delivery + media-typed inbox row** - `e6034b0` (feat)

**Plan metadata:** (this SUMMARY commit — docs)

## Files Created/Modified
- `app/services/queue.py` — import `vary` + `os`; read `variation_enabled` in the per-item campaign SELECT; compute `apply_var` gate + `text_to_send`/`caption_to_send` local copies; file branch loads blob + `send_file(file_bytes, force_document=False)` + extension classification + `result['media']`; `_upsert_conversation` writes a media-typed inbox INSERT when `result['media']` present.
- `migrations/055_messages_media_columns.sql` — idempotent ADD COLUMN IF NOT EXISTS for `message_type`/`file_name`/`mime_type`/`size_bytes` + guarded CHECK constraint (bridges Phase 23 mig 053).
- `tests/conftest.py` — exists-guarded apply of migration 055 (messages is a raw-SQL table, not ORM create_all).
- `tests/test_queue_variation.py` — VAR-FLAG/VAR-SCOPE + D-14 clean-DB + D-16 freshness (5 tests).
- `tests/test_queue_file_opener.py` — ATT-DELIVER/INBOX-MEDIA/FALLBACK (4 tests).

## Decisions Made
- Kept the `messages_log` write reading `item.message_text` (untouched) — variation is never persisted anywhere (D-14).
- File-opener media metadata is passed to `_upsert_conversation` via `result['media']` rather than re-querying, keeping the INSERT column choice a pure presence check.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added migration 055 to create the messages media columns**
- **Found during:** Task 2 (media-typed inbox row)
- **Issue:** The plan assumes Phase 23 migration 053 added `messages.message_type/file_name/mime_type/size_bytes` ("Phase 24 depends_on Phase 23 → columns guaranteed present"). In reality **Phase 23 was never executed** — migrations jump 052 → 054, there is no mig 053, no `Message` ORM model, and the `messages` table (raw migration 017) lacks all four columns. Task 2's media INSERT would fail with `column "message_type" does not exist`.
- **Fix:** Added `migrations/055_messages_media_columns.sql` (idempotent ADD COLUMN IF NOT EXISTS + guarded CHECK) bridging the Phase 23 mig-053 gap, and wired an exists-guarded apply into `tests/conftest.py` (the messages table is raw-SQL, not ORM create_all). Named 055 (not 053) to avoid colliding with a future Phase 23 migration; both are idempotent so they coexist.
- **Files modified:** migrations/055_messages_media_columns.sql, tests/conftest.py
- **Verification:** Task 2 tests GREEN (photo/video/document classification + non-null media metadata); text-opener + URL-fallback rows correctly stay `message_type='text'`.
- **Committed in:** `9704ed3` (Task 2 RED/scaffold commit)

---

**Total deviations:** 1 auto-fixed (1 blocking dependency gap)
**Impact on plan:** The bridge migration is required for correctness of the inbox-fidelity requirement and does not expand scope beyond the four columns Plan 24-06 consumes. If Phase 23 is later executed it can add the same columns idempotently without conflict.

## Issues Encountered
- **Stale worktree base:** the parallel-executor worktree was branched from pre-Phase-24 `origin/main` (commit 92bd54b), missing all wave-1 artifacts and the newer `get_client` signature. Fast-forwarded the per-agent branch to local `main` (a19ab85, which contains 24-01/24-02/24-03) so edits target real content and tests run. My four commits are children of `main`, trivially applicable.
- **Docker container-name conflict:** the worktree compose project could not recreate the prod-named `outreach-platform-db` container. Ran the ephemeral `db-test` with `up -d` and executed pytest via `run --rm --no-deps api` (with `--env-file` pointing at the prod `.env` for variable substitution; DATABASE_URL is still overridden to `outreach_test` by the test overlay, so prod DB was never touched). Ephemeral `db-test` container removed after the run.

## Verification
- `pytest tests/test_queue_variation.py tests/test_queue_file_opener.py` → 9 passed.
- Regression: `tests/test_send_campaign.py tests/test_queue_new_dialog_limit.py tests/test_send_file_blob.py` → all GREEN (23 total passed together).
- `grep -P '[\x{200b}\x{200c}\x{200d}\x{2060}]' app/services/queue.py` → no invisible glyphs (variation lives in the module).

## User Setup Required
None — no external service configuration required. (Migration 055 auto-applies on api restart via `_apply_migrations`.)

## Next Phase Readiness
- 24-07 (handoff & live smoke) can now exercise the full send path: varied clean-DB openers + blob file-openers rendered as media bubbles.
- Note for orchestrator: STATE.md / ROADMAP.md were intentionally NOT updated here (parallel executor) — reconcile after all wave agents. Migration 055 bridges Phase 23's un-executed mig 053; if Phase 23 is later planned, it should reuse/skip these columns idempotently.

## Self-Check: PASSED

All created/modified files exist on disk and all four task commits (`0a8abac`, `6816cb3`, `9704ed3`, `e6034b0`) are present in git history.

---
*Phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation*
*Completed: 2026-07-07*
