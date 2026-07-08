---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
plan: 06
subsystem: handoff
status: complete
tags: [handoff, openapi, error-codes, live-smoke, inbox]
requires:
  - "23-03 edit/delete endpoints (no-takeover)"
  - "23-05 send-file + download endpoints"
provides:
  - "lovable-handoff/openapi.json regenerated with the 4 Phase-23 inbox endpoints + schemas"
  - "lovable-handoff/error-codes.md D-17 inbox error-code registry"
  - "23-UI-SPEC.md design contract (photo/video inline view is primary, not download-to-disk)"
  - "aimly-tg-outreach inbox.tsx: typed message bubbles, edit/delete row-actions, send-file composer, inline photo/video viewer + lightbox"
affects:
  - "sibling repo AGS-Venture-Lab/aimly-tg-outreach (UI regen input + direct implementation)"
tech-stack:
  added: []
  patterns:
    - "openapi.json regenerated from the running backend via scripts/export-handoff.sh (never hand-edited)"
    - "Telethon photo/mime detection keys off the file PATH's extension, not any separately-passed name — temp files for outbound uploads must keep the original suffix"
    - "Telethon send_file has no file_name kwarg; use attributes=[DocumentAttributeFilename(...)] to set the displayed name"
key-files:
  created: []
  modified:
    - "lovable-handoff/openapi.json — 4 new paths + EditMessageRequest/SendFileFromUIResponse + extended MessageResponse"
    - "lovable-handoff/error-codes.md — Phase-23 D-17 inbox error-code section + FILE_TOO_LARGE note update"
    - "lovable-handoff/types/api.ts — regenerated from the new spec"
    - "app/routers/conversations.py — _spool_upload_with_cap keeps the upload's extension (tg-outreach 62a4664)"
    - "app/services/telegram.py — send_file_by_telegram_id uses DocumentAttributeFilename instead of the no-op file_name kwarg (tg-outreach 62a4664)"
    - "23-UI-SPEC.md — Surface 4/C2 revised: inline view is the required primary behaviour for photo/video, not a nice-to-have (tg-outreach d82c6c6)"
    - "aimly-tg-outreach src/routes/_authenticated/inbox.tsx — inline photo/video viewer + shared blob cache + lightbox (aimly-tg-outreach da3c0db, e424f53)"
decisions:
  - "Download endpoint documented at /messages/{message_id}/download (the implemented path from 23-05), not the plan's stale /file draft"
  - "FILE_TOO_LARGE kept as a single row (same 413 code) covering both campaign-attachment and inbox send-file; UI string updated to 'File is larger than 50 MB.'"
  - "Photo/video bubbles (both directions) fetch bytes only on explicit tap (D-16 preserved — no auto/background fetch per message in a thread); the tap's RESULT changed from download-to-disk to inline render, cached per message_id for the session"
  - "Freshly-sent outbound photo/video renders from the local File object immediately (zero network round-trip), registered against the message_id returned by POST /send-file"
metrics:
  duration: ~7min (Task 1) + live-smoke session with 2 backend bugs + 1 frontend gap found and fixed
  completed: 2026-07-08
---

# Phase 23 Plan 06: Handoff Regen + Live Smoke Summary

Regenerated the Lovable frontend handoff (openapi.json + error-codes.md) from the rebuilt
backend, then a human live-smoked all four inbox capabilities against a real Telegram account
and two real backend bugs plus one frontend UX gap were found and fixed live during the session.

## Status

**COMPLETE.** Both tasks done. Human confirmed edit, delete-for-everyone, and send-file (photo)
work correctly against a real Telegram account and a real inbox UI (the sibling repo's
`inbox.tsx` was implemented against 23-UI-SPEC.md during this session, not left for a later
Lovable regen cycle, since the live-smoke immediately surfaced gaps in it).

Not explicitly exercised: caption >1024-char overflow, sending a `.pdf` document, and the
optional >50 MB `FILE_TOO_LARGE` case. User elected to close the checkpoint without these
(none touch the code paths that were just fixed); flagged here as an untested-but-low-risk
follow-up rather than a blocking gap.

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

## Task 2 — Live-smoke all four inbox operations (DONE)

Deployed both services (`docker compose up -d --build api listener`), then the human live-smoked:

1. **EDIT** — confirmed working (real chat text changes).
2. **DELETE for everyone** — confirmed working (both-sides revoke).
3. **SEND-FILE** — first attempt surfaced a real bug (below); confirmed working after the fix,
   including the inline-preview UX added during the session.
4. **INCOMING media + view** — confirmed working after the frontend inline-viewer was implemented
   (previously the deployed UI had no typed-bubble rendering at all — everything showed as a
   generic file row, on both inbound and outbound).

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

**2. [Rule 2 — Bug found live] send-file sent images as a renamed generic document, not a photo**
- **Found during:** Task 2 live-smoke, first send-file attempt (`image.png` arrived as a
  document with a random filename).
- **Root cause (verified against the installed Telethon in the `api` container):**
  - `_spool_upload_with_cap` created the temp file via bare `tempfile.mkstemp()` with no
    extension. Telethon's `utils.is_image()` / `mimetypes.guess_type()` key off the file PATH's
    extension, not any separately-passed name — an extension-less temp path always classifies as
    a generic document regardless of `force_document=False`.
  - `send_file_by_telegram_id` passed `file_name=file_name` to `client.send_file(...)`, but
    Telethon's `send_file` has **no such parameter** — silently absorbed by `**kwargs` and does
    nothing. The displayed filename fell back to the temp file's random basename.
- **Fix (tg-outreach commit `62a4664`):** `mkstemp(suffix=...)` now preserves the upload's
  original extension (fixes photo/mime detection); `send_file_by_telegram_id` now passes an
  explicit `attributes=[DocumentAttributeFilename(file_name)]` (fixes the displayed name for
  non-image documents). Verified live: photo now arrives inline, correctly named.
- **Files modified:** `app/routers/conversations.py`, `app/services/telegram.py`.

**3. [Rule 2 — Gap found live] deployed inbox UI had no Phase-23 media rendering at all**
- **Found during:** Task 2 live-smoke — incoming and outgoing photos both rendered as a plain
  "file" row with no way to view the image short of downloading it, contradicting D-11/D-16's
  intent and the (at-the-time-draft) UI-SPEC.
- **Root cause:** The sibling frontend repo's deployed `inbox.tsx` predates 23-UI-SPEC.md and had
  no `message_type` branching. Separately, the freshly-written UI-SPEC itself had made inline
  photo preview a "nice-to-have" with download-to-disk as the default (Surface 4) — coding to it
  as originally drafted would have reproduced the same complaint after a Lovable regen.
- **Fix:**
  1. Revised `23-UI-SPEC.md` (tg-outreach `d82c6c6`) — inline view is now the **required primary
     behaviour** for `photo`/`video` (tap-to-fetch-once, cached per `message_id`, D-16's
     no-auto-fetch guarantee preserved); `document`/`voice` keep download-to-disk.
  2. Implemented directly in `aimly-tg-outreach/src/routes/_authenticated/inbox.tsx` (commits
     `da3c0db`, `e424f53`) rather than waiting for a Lovable regen cycle: tap-to-view photo/video
     bubbles with a shared blob-URL cache, immediate local preview for freshly-sent outbound
     media (zero round-trip), a photo lightbox (click-to-zoom, closes on backdrop/X/Escape), and
     parity Download buttons on outbound document/voice bubbles (previously inbound-only).
  3. Pushed to `origin/main` on user confirmation (Cloudflare git-integration auto-builds).
- **Files modified:** `aimly-tg-outreach/src/routes/_authenticated/inbox.tsx` (frontend repo).

## Self-Check: PASSED

- FOUND: lovable-handoff/openapi.json
- FOUND: lovable-handoff/error-codes.md
- FOUND: lovable-handoff/types/api.ts
- FOUND: .planning/phases/23-.../23-06-SUMMARY.md
- FOUND: .planning/phases/23-.../23-UI-SPEC.md (status: approved)
- FOUND commits: 9a35227 (handoff), 62a4664 (backend send-file fix), d82c6c6 (UI-SPEC revision)
- FOUND commits (sibling repo aimly-tg-outreach): da3c0db, e424f53
- Human live-smoke: **approved** — edit / delete / send-file / incoming-view all confirmed working
