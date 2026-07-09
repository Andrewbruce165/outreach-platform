---
phase: quick-260709-dbl
plan: 01
subsystem: campaigns / telegram-delivery
tags: [campaign-attachments, multi-file, album, telegram, migration]
requires:
  - campaign_attachments table (Phase 24, migration 054)
  - telegram_service.send_file blob path (Phase 24, commit 3859ce0)
provides:
  - campaign_attachments 1-to-N (ordered position column, migration 060)
  - multi-file upload endpoint (files[]/attachments[], replace-all, cap 10)
  - CampaignResponse.attachment_count
  - grouped Telegram album delivery on the campaign opener
affects:
  - app/routers/campaigns.py (upload_attachment, duplicate_campaign, _campaign_to_response)
  - app/services/telegram.py (send_file album path)
  - app/services/queue.py (file-opener branch)
tech-stack:
  added: []
  patterns:
    - "album via one client.send_file(peer, [paths]) — Telethon groups a list"
    - "per-file temp subdir preserves original basename (no DocumentAttributeFilename for albums)"
    - "replace-all upsert with position ordering"
key-files:
  created:
    - migrations/060_campaign_attachments_multiple.sql
  modified:
    - app/models/__init__.py
    - app/routers/campaigns.py
    - app/schemas/__init__.py
    - app/services/telegram.py
    - app/services/queue.py
    - tests/test_campaign_attachment.py
    - tests/test_send_file_blob.py
    - tests/test_queue_file_opener.py
    - tests/conftest.py
    - lovable-handoff/openapi.json
decisions:
  - "Album files each get their own temp subdir so Telethon derives the exact original filename from the basename — no DocumentAttributeFilename needed for the multi-file path, and no cross-file name collisions."
  - "Single-attachment send path kept byte-for-byte (commit 3859ce0 DocumentAttributeFilename fix preserved); the album branch runs ONLY when >1 file."
  - "Inbox media bubble records the PRIMARY (first) attachment for a multi-file opener (v1 limitation) — all files ARE delivered to the recipient."
  - "attachment_count computed via COUNT on campaign_attachments; has_attachment = attachment_count > 0 (blobs stay off SELECT campaigns, Pitfall 7)."
metrics:
  duration: ~16min
  tasks: 3
  files: 10
  completed: 2026-07-09
---

# Quick 260709-dbl: Campaign First-Message — Multiple File Attachments Summary

Extended the campaign first-message attachment from a single file to MULTIPLE ordered files: `campaign_attachments` is now 1-to-N (migration 060 drops the `UNIQUE(campaign_id)` and adds a `position` column), the upload endpoint accepts a `files[]`/`attachments[]` list (replace-all, capped at 10), and the opener delivers every attachment as one grouped Telegram album with original filenames preserved — with full backwards compatibility for existing single-file campaigns and the legacy single-field upload path.

## What was built

- **Task 1 — 1-to-N model.** Migration `060_campaign_attachments_multiple.sql` (idempotent): `DROP CONSTRAINT IF EXISTS campaign_attachments_campaign_id_key`, `ADD COLUMN IF NOT EXISTS position integer NOT NULL DEFAULT 0`, composite index `(campaign_id, position)`. ORM `CampaignAttachment` lost `unique=True` and gained `position` (server_default `0`). `duplicate_campaign` now copies ALL attachment rows ordered by position, not just the first. Conftest applies migration 060 for test-DB/prod index parity.
- **Task 2 — Multi-file upload.** `upload_attachment` accepts `files: list[UploadFile]` / `attachments: list[UploadFile]` (album) plus the legacy `file`/`attachment` single fields. `MAX_ATTACHMENTS = 10` → 400 `TOO_MANY_ATTACHMENTS`; per-file 50 MB ceiling unchanged → 413 `FILE_TOO_LARGE` (both validated before any write). Replace-all upsert writes rows with `position=index`. Response gains `count` + `attachments[]` while still echoing the first file at top level for back-compat. `CampaignResponse.attachment_count` added (computed via COUNT). `openapi.json` documents the multi-file request/response + `attachment_count`.
- **Task 3 — Album delivery.** `telegram_service.send_file` gained an optional `attachments: list[dict]` param: each file is written into its own temp subdir (basename = original filename), then sent as one grouped `client.send_file(peer, [paths])`; the album's message id comes from the first returned message; the parent temp dir is cleaned up in `finally`. The single-blob/URL path is byte-for-byte unchanged. The queue file-opener loads ALL attachment rows `ORDER BY position, created_at`: 0 rows → legacy URL fallback, 1 row → existing single-blob send (lowest risk), >1 rows → album. The inbox media bubble records the primary (first) file.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `_insert_attachment` test helper used `ON CONFLICT (campaign_id)`**
- **Found during:** Task 3
- **Issue:** `tests/test_queue_file_opener.py::_insert_attachment` upserted via `ON CONFLICT (campaign_id)`, which requires the UNIQUE constraint that migration 060 drops — the existing queue tests would break once the constraint was gone.
- **Fix:** Reworked the helper to `DELETE`-then-`INSERT` (with a `replace`/`position` flag) so it supports both single-file replace semantics and multi-file album building.
- **Files modified:** tests/test_queue_file_opener.py
- **Commit:** ff48da8

**2. [Rule 3 - Blocking] conftest migration-060 parity block (out of plan file list)**
- **Found during:** Task 1
- **Issue:** The test DB's UNIQUE constraint came from the ORM `unique=True` (create_all), not migration 054 (a no-op `CREATE TABLE IF NOT EXISTS`). The repo pattern applies each new migration in conftest for prod parity; without a 060 block the composite `(campaign_id, position)` index path would not be exercised in tests.
- **Fix:** Added an exists-guarded `_mig_060` apply block mirroring the existing 054/055/056 blocks.
- **Files modified:** tests/conftest.py
- **Commit:** 3512361

### Test-run note (not a deviation)

This per-agent worktree has no `.env` and `docker-compose.yml` gives the prod `db` service a fixed `container_name` (conflicts with the running prod DB). Tests were run by starting only `db-test` and invoking `run --rm --no-deps api` with `--env-file /root/apps/aimly/tg-outreach/.env`. TDD RED and GREEN were committed separately per task, but verified with a single overlay run per task (build cost) rather than a dedicated RED docker run.

## Verification

Targeted overlay runs (all green):
- `tests/test_campaign_attachment.py` — 21 passed (Task 1+2)
- `tests/test_send_file_blob.py tests/test_queue_file_opener.py` — 11 passed (Task 3)
- All three together — 32 passed
- Regression: `tests/test_campaign_router.py tests/test_phase5_1_campaign_v2_router.py tests/test_campaign_draft_optional.py` — 34 passed
- `openapi.json` validated as parseable JSON.

## Known Stubs

None.

## Deferred Issues

None.

## Self-Check: PASSED

All created/modified files present; all 6 per-task commits (4ecba9d, 3512361, 846ea61, ff1bf85, ff48da8, 4f1241f) exist in git history.
