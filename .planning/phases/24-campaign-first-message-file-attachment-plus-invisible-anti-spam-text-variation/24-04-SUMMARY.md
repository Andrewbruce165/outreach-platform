---
phase: 24-campaign-first-message-file-attachment-plus-invisible-anti-spam-text-variation
plan: 04
subsystem: api
tags: [fastapi, multipart, upload, bytea, campaigns, workspace-isolation]
status: complete

# Dependency graph
requires:
  - phase: 24-02-data-model-migration-schemas
    provides: CampaignAttachment ORM + campaigns.variation_enabled + variation_enabled/has_attachment schema fields
  - phase: 04-campaigns
    provides: campaigns router (_load_campaign, _campaign_to_response, create/patch/duplicate)
provides:
  - POST /api/v1/campaigns/{id}/attachment (multipart upload, 50 MB guard, alias file|attachment, upsert)
  - DELETE /api/v1/campaigns/{id}/attachment (204, idempotent)
  - variation_enabled wired through create_campaign + _campaign_to_response
  - has_attachment computed on CampaignResponse (EXISTS probe)
  - duplicate_campaign copies BOTH variation_enabled flag AND the attachment blob
affects: [24-05-enqueue-file-opener-and-rerender, 24-06-worker-variation-and-blob-delivery, frontend-campaign-editor]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Multipart upload precedent from account_import.py: UploadFile = File(...) + await read() + len guard -> 413"
    - "Alias-tolerant multipart (file|attachment) via two File(default=None) params + `upload = file or attachment` (Lovable field-name variance, D-19)"
    - "Blob upsert = delete-then-insert one row per campaign (D-01 exactly-one)"
    - "Attachment copy in duplicate happens in the SAME transaction as new_c (flush -> copy -> commit)"

key-files:
  created: []
  modified:
    - app/routers/campaigns.py
    - tests/test_campaign_attachment.py

key-decisions:
  - "Bytes stream straight to the BYTEA column (no temp file) — inherits D-02 temp-file-free contract"
  - "DELETE is idempotent (delete-where is a no-op when no row) — still 204 with no attachment present"
  - "PATCH needed NO code change: variation_enabled is already in CampaignUpdate, so the existing exclude_unset setattr loop round-trips it"
  - "has_attachment computed via EXISTS on campaign_attachments — the 50 MB blob never rides a SELECT campaigns (Pitfall 7)"

patterns-established:
  - "Sub-resource attachment endpoints are workspace-scoped through _load_campaign (cross-workspace -> CAMPAIGN_NOT_FOUND 404); the row also carries workspace_id"

metrics:
  duration_minutes: 10
  completed: 2026-07-07
  tasks: 2
  files_changed: 2
  commits: 4
  tests_added: 11
---

# Phase 24 Plan 04: Attachment Endpoint & Duplicate Summary

Exposed the campaign first-message attachment lifecycle over the API: a multipart upload endpoint (50 MB `FILE_TOO_LARGE` guard, alias-tolerant to `file`/`attachment`, one-blob-per-campaign upsert), a matching idempotent DELETE, `variation_enabled` wired through create/response, a computed `has_attachment`, and `duplicate_campaign` now copying both the variation flag and the attachment blob so duplicates are send-ready (D-01/D-03/D-13/D-19/D-20).

## What was built

### Task 1 — upload + delete endpoints
- `MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024` module constant.
- `POST /api/v1/campaigns/{campaign_id}/attachment` — multipart, alias-tolerant (`file` or `attachment`; neither → 422 `FILE_REQUIRED`). Over 50 MB → 413 `FILE_TOO_LARGE`. Loads the campaign workspace-scoped (cross-workspace → 404), then delete-then-insert exactly one `campaign_attachments` row (bytes straight to BYTEA, no temp file). Returns `{campaign_id, file_name, size_bytes, content_type}`.
- `DELETE /api/v1/campaigns/{campaign_id}/attachment` — 204, idempotent (no-op when no blob).

### Task 2 — variation wiring + duplicate copy
- `create_campaign`: `variation_enabled=payload.variation_enabled`.
- `_campaign_to_response`: adds `variation_enabled` + computes `has_attachment` via an `EXISTS` probe on `campaign_attachments` (blob stays off the SELECT).
- `duplicate_campaign`: copies `variation_enabled=src.variation_enabled` and, in the same transaction after flush, copies the source attachment blob into a NEW `campaign_attachments` row for the copy.
- PATCH: no change needed — `variation_enabled` already flows through the existing `exclude_unset` setattr loop.

## Tests
`tests/test_campaign_attachment.py` extended with 11 endpoint/wiring tests (upload stores one blob, re-upload replaces, alias field, missing field → 422, >50 MB → 413, DELETE 204, idempotent DELETE, cross-workspace 404, has_attachment reflects blob, variation round-trip through PATCH, duplicate copies flag+blob). Full file: **15 passed**. Regression: `tests/test_campaign_router.py` **24 passed**.

## Deviations from Plan

None functionally. One test-authoring correction: the `duplicate_campaign` endpoint returns **201 Created** (not 200); the duplicate test assertion was fixed to expect 201 before the blob-copy assertions ran. No production-code deviation.

## Notes for the orchestrator (parallel executor)
- This worktree was branched from a stale base (Phase 19). At startup the branch was fast-forwarded (`git reset --hard`) to the real wave-1 base `a19ab85` (wave-1 complete) so the 24-02 artifacts (CampaignAttachment model, variation_enabled, migration 054, base test file) were present and verification could run. My four commits sit cleanly on top of `a19ab85` and cherry-pick/merge onto real main without conflict.
- STATE.md / ROADMAP.md intentionally NOT updated (parallel wave-2 executor — left to orchestrator reconciliation).
- `.env` was copied into the worktree (gitignored) so the docker test-overlay could interpolate settings; not committed.

## Self-Check: PASSED
