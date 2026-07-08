---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
plan: 06
subsystem: handoff
status: paused-at-checkpoint
tags: [handoff, openapi, error-codes, live-smoke, inbox]
requires:
  - "23-03 edit/delete endpoints (no-takeover)"
  - "23-05 send-file + download endpoints"
provides:
  - "lovable-handoff/openapi.json regenerated with the 4 Phase-23 inbox endpoints + schemas"
  - "lovable-handoff/error-codes.md D-17 inbox error-code registry"
affects:
  - "sibling repo AGS-Venture-Lab/aimly-tg-outreach (UI regen input)"
tech-stack:
  added: []
  patterns:
    - "openapi.json regenerated from the running backend via scripts/export-handoff.sh (never hand-edited)"
key-files:
  created: []
  modified:
    - "lovable-handoff/openapi.json — 4 new paths + EditMessageRequest/SendFileFromUIResponse + extended MessageResponse"
    - "lovable-handoff/error-codes.md — Phase-23 D-17 inbox error-code section + FILE_TOO_LARGE note update"
    - "lovable-handoff/types/api.ts — regenerated from the new spec"
decisions:
  - "Download endpoint documented at /messages/{message_id}/download (the implemented path from 23-05), not the plan's stale /file draft"
  - "FILE_TOO_LARGE kept as a single row (same 413 code) covering both campaign-attachment and inbox send-file; UI string updated to 'File is larger than 50 MB.'"
metrics:
  duration: ~7min (Task 1 only; Task 2 pending human live-smoke)
  completed: 2026-07-08
---

# Phase 23 Plan 06: Handoff Regen + Live Smoke Summary

Regenerated the Lovable frontend handoff (openapi.json + error-codes.md) from the rebuilt
backend so the sibling UI repo can build the four new inbox capabilities; the human live-smoke
of those capabilities against a real Telegram account is the remaining blocking checkpoint.

## Status

**PAUSED at Task 2 (blocking human-verify checkpoint).** Task 1 is complete and committed.
Task 2 requires a human to live-smoke edit / delete-for-everyone / send-file / incoming-download
against a real Telegram account — it cannot be auto-verified (server-controlled Telethon behaviour
behind the test mocks). Plan counter is **not** advanced and INBM-09 is **not** marked complete
until the human signs off.

## Task 1 — Regenerate openapi.json + document D-17 error codes (DONE, commit 9a35227)

- Rebuilt the `api` container (`docker compose up -d --build api`) so the four new routes/schemas
  are live, then ran `bash scripts/export-handoff.sh` — openapi.json + types/api.ts regenerated
  from the running backend (not hand-edited). `info.title` = "Outreach Platform API" (sanity check
  passed; UI-SPEC drift check: 39/39 endpoints present).
- Confirmed openapi.json `paths` now include:
  - `POST /api/v1/conversations/{conversation_id}/send-file`
  - `GET  /api/v1/conversations/{conversation_id}/messages/{message_id}/download`
  - `PATCH` **and** `DELETE` on `/api/v1/conversations/{conversation_id}/messages/{message_id}`
  - `components.schemas` include `EditMessageRequest` + `SendFileFromUIResponse`; `MessageResponse`
    carries `message_type`, `file_name`, `mime_type`, `size_bytes`, `edited_at`.
- Updated `error-codes.md` with a Phase-23 D-17 inbox section documenting every code with its
  HTTP status (mirroring `_INBOX_ERROR_STATUS`/`_raise_inbox_message_error`) + a UI string:
  `MESSAGE_EDIT_TOO_OLD` (409), `MESSAGE_NOT_EDITABLE` (422), `MESSAGE_NOT_FOUND` (404),
  `DELETE_FAILED` (502), `MEDIA_UNAVAILABLE` (410), `DOWNLOAD_FAILED` (502),
  `TELEGRAM_OP_FAILED` (502), plus reused-code note (`NO_TELEGRAM_ID`, `RECIPIENT_NOT_IN_TELEGRAM`,
  `FLOOD_WAIT`, `ACCOUNT_FROZEN`, `USER_IS_BLOCKED`, `FILE_TOO_LARGE`) and the D-22 field-alias
  tolerance note (`message`/`message_text`/`text`).

## Task 2 — Live-smoke all four inbox operations (PENDING — blocking human-verify)

Not started autonomously by design. Requires deploy of BOTH `api` and `listener`
(`docker compose up -d --build api listener` — listener was touched in 23-04) and a human to
exercise edit / delete-for-everyone / send-file (photo + >1024-char caption overflow + document) /
incoming photo+document download, plus optional >50 MB `FILE_TOO_LARGE`. Deviations (e.g. actual
edit-time window) to be recorded here on sign-off.

## Deviations from Plan

**1. [Rule 1 — Spec drift] Download endpoint is `/download`, not the plan's `/file` draft**
- **Found during:** Task 1 verification.
- **Issue:** The plan's automated verify command asserts `/messages/{message_id}/file`, but plan
  23-05 intentionally implemented the route at `/messages/{message_id}/download` to match the
  authoritative committed test (`tests/test_phase23_inbox_mutations.py`). openapi.json is
  regenerated from the real backend, so it correctly contains `/download`.
- **Fix:** Adapted the verification to assert `/download`; documented in error-codes.md as
  `/download`. No backend change and no hand-edit of the spec (the spec reflects the real code).
- **Files modified:** none beyond the regenerated handoff artifacts.

## Self-Check: PASSED

- FOUND: lovable-handoff/openapi.json
- FOUND: lovable-handoff/error-codes.md
- FOUND: lovable-handoff/types/api.ts
- FOUND: .planning/phases/23-.../23-06-SUMMARY.md
- FOUND commit: 9a35227

(Task 2 human live-smoke still pending — plan not yet closed.)
