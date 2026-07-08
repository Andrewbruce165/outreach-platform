---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
verified: 2026-07-08T11:00:51Z
status: passed
score: 9/9 must-haves verified (INBM-01..09)
---

# Phase 23: Edit and delete-for-everyone of sent messages plus file sending from inbox UI — Verification Report

**Phase Goal:** Из inbox UI можно удалить отправленное сообщение (у обеих сторон), отредактировать уже отправленное сообщение и отправить файл контакту; плюс входящие файлы ОТ контакта отображаются как file-бабблы с ленивым скачиванием.
**Verified:** 2026-07-08T11:00:51Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Manager can delete a sent message for everyone from inbox | ✓ VERIFIED | `DELETE /api/v1/conversations/{id}/messages/{message_id}` (conversations.py:483) → `delete_message_by_telegram_id` (`revoke=True`, telegram.py:1802) → hard `DELETE FROM messages`; frontend `Trash2` row-action wired (inbox.tsx:2063-2081); live-smoked and confirmed by human ("DELETE for everyone — confirmed working, both-sides revoke") |
| 2 | Manager can edit an already-sent text message | ✓ VERIFIED | `PATCH /api/v1/conversations/{id}/messages/{message_id}` (conversations.py:427) → `edit_message_by_telegram_id` (telegram.py:1703) → `UPDATE messages SET message_text, edited_at=NOW()`; frontend `Pencil` row-action gated `isTextType` (inbox.tsx:2042-2062), "(edited)" marker rendered (inbox.tsx:2023/2116); live-smoked "EDIT — confirmed working" |
| 3 | Neither edit nor delete triggers auto-takeover (no `ai_enabled`/`status`/queue mutation) | ✓ VERIFIED | Both endpoints only touch the `messages` row; no `conversations`/`message_queue` write in either handler (grep confirms no `ai_enabled`/`status=` assignment in edit_message/delete_message bodies) |
| 4 | Manager can send a file to a contact from inbox, arriving as a real photo/video (not a renamed document) | ✓ VERIFIED | `POST /{id}/send-file` (conversations.py:528) → `_spool_upload_with_cap` (50MB streamed guard) → `send_file_by_telegram_id` (`force_document=False`, `DocumentAttributeFilename`, telegram.py:1835-1885); mkstemp preserves upload extension (conversations.py:208, fix commit 62a4664); live-smoked, bug found+fixed, then "confirmed working after the fix" |
| 5 | Sending a file is a NEW outbound → DOES auto-takeover (status='manual', ai_enabled=false, pending queue cancelled) | ✓ VERIFIED | `test_send_file_takeover_and_persists_row` XPASS; code path mirrors `/send` ordering (gate → takeover UPDATE + queue-cancel → commit → Telethon → typed INSERT) |
| 6 | Incoming files from the contact are recorded as typed file rows (not generic text) | ✓ VERIFIED | Listener `handle_incoming_message` classifies `message_type` (photo/video/voice/document) + reads `file.{name,mime_type,size}` pre-download (listener.py:882-989), threaded into `save_message(message_type=..., file_name=..., ...)`; voice transcription path preserved |
| 7 | Incoming files display as file-bubbles in the UI with lazy (on-demand) download | ✓ VERIFIED | `GET /{id}/messages/{message_id}/download` (conversations.py:673) fetches bytes from Telegram on demand via `download_media_by_telegram_id` (peer+message_id, never `file.id`); frontend `MessageBubble` renders typed bubbles (photo/video/voice/document) with tap-to-fetch (`handleViewMedia`, inbox.tsx:1719-1734) and inline lightbox; live-smoked "INCOMING media + view — confirmed working" (required a frontend fix, see gap note below) |
| 8 | Structured, user-facing error codes for all new failure modes | ✓ VERIFIED | `_INBOX_ERROR_STATUS`/`_raise_inbox_message_error` (conversations.py:106-143) maps `MESSAGE_EDIT_TOO_OLD`(409)/`MESSAGE_NOT_EDITABLE`(422)/`MESSAGE_NOT_FOUND`(404)/`DELETE_FAILED`(502)/`FILE_TOO_LARGE`(413)/`MEDIA_UNAVAILABLE`(410)/`TELEGRAM_OP_FAILED`(502) + reused codes; documented in `lovable-handoff/error-codes.md` |
| 9 | Frontend handoff kept in sync (openapi.json, error-codes.md) so the sibling UI repo can consume the new endpoints | ✓ VERIFIED | `lovable-handoff/openapi.json` regenerated via `export-handoff.sh` (not hand-edited) — confirmed contains all 4 new paths + `EditMessageRequest`/`SendFileFromUIResponse`/extended `MessageResponse`; sibling repo `aimly-tg-outreach` implements against it, pushed to `origin/main` (commits `da3c0db`, `e424f53`) |

**Score:** 9/9 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `migrations/053_phase23_messages_media.sql` | message_type+CHECK, file_name/mime_type/size_bytes, edited_at, message_text NULLABLE, no deleted_at | ✓ VERIFIED | Present, idempotent, matches D-20/D-21 exactly; confirmed applied on prod DB (`\d messages` shows all columns + CHECK constraint live) |
| `app/schemas/__init__.py` (MessageResponse/EditMessageRequest/SendFileFromUIResponse) | Extended response + new request/response schemas | ✓ VERIFIED | All three present, `EditMessageRequest.message` uses `AliasChoices("message","message_text","text")` (D-22) |
| `app/services/telegram.py` (4 inbox methods + peer helper) | edit/delete/send_file/download_media by telegram_id | ✓ VERIFIED | All 5 present; correct exception→code mapping inside `edit_message_by_telegram_id` verified by reading source (lines 1703-1777) |
| `app/routers/conversations.py` (PATCH/DELETE/POST send-file/GET download) | 4 REST endpoints, workspace-gated | ✓ VERIFIED | All 4 registered (confirmed via live route introspection on the running prod container); `_load_message_for_mutation` single-SELECT gate enforces outbound+sent_by+workspace (+text for edit) |
| `app/services/listener.py` (incoming media classification) | message_type + metadata captured pre-download | ✓ VERIFIED | `save_message()` extended with 4 keyword-optional params; classifier computed once before the shared incoming `save_message` call |
| `lovable-handoff/openapi.json` + `error-codes.md` | Handoff artifacts regenerated | ✓ VERIFIED | Regenerated from running backend; all 4 paths + schemas + D-17 codes present |
| `aimly-tg-outreach/src/routes/_authenticated/inbox.tsx` (sibling repo) | Edit/delete row-actions, send-file composer, typed media bubbles, lazy fetch, lightbox | ✓ VERIFIED | All present and wired (see Key Link table); pushed to `origin/main` (matches local HEAD `e424f53`) |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `PATCH /messages/{id}` router | `telegram_service.edit_message_by_telegram_id` | direct await call, structured-dict result check | ✓ WIRED | conversations.py:452-463 |
| `DELETE /messages/{id}` router | `telegram_service.delete_message_by_telegram_id` | direct await call, `revoke=True` default | ✓ WIRED | conversations.py service call confirmed |
| `POST /send-file` router | `telegram_service.send_file_by_telegram_id` + `_spool_upload_with_cap` | multipart→temp→Telethon, takeover ordering mirrors `/send` | ✓ WIRED | conversations.py:528-626 |
| `GET /messages/{id}/download` router | `telegram_service.download_media_by_telegram_id` | peer+telegram_message_id lookup, streamed `Response` | ✓ WIRED | conversations.py:673-739 |
| listener `handle_incoming_message` | `save_message(message_type=..., file_name=..., ...)` | media classifier feeds the shared incoming save_message call | ✓ WIRED | listener.py:975-989 |
| frontend `editMut`/`deleteMut`/`sendFileMut` | backend PATCH/DELETE/POST send-file | `api()` fetch wrapper, method+URL match backend routes exactly | ✓ WIRED | inbox.tsx:1034 (PATCH), 1067 (DELETE), 1087-1100 (send-file) |
| frontend `MessageBubble` row-actions | `isOutbound`/`isTextType` gates | edit button only for outbound+text; delete for outbound only | ✓ WIRED | inbox.tsx:2042 (`isTextType &&`), 2026 (`isOutbound && hovered`) |
| frontend `handleViewMedia` | `GET /messages/{id}/download?disposition=inline` | tap-to-fetch, cached blob URL, lightbox on photo | ✓ WIRED | inbox.tsx:1719-1734, 1868 |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|---------------------|--------|
| `MessageBubble` (`m.message_type`/`m.edited_at`/media fields) | `messages` from `GET /messages` | Widened SELECT (`m.message_type, m.file_name, m.mime_type, m.size_bytes, m.edited_at`) added in 23-03 | Yes — confirmed live on prod (schema + route present, human live-smoke saw real typed bubbles) | ✓ FLOWING |
| `sendFileMut` response (`message_id`/`message_type`) | POST /send-file response body | Real Telethon send + typed `messages` INSERT (not a static stub) | Yes | ✓ FLOWING |
| Download blob (`fetchMessageMedia`) | `GET /messages/{id}/download` | Real Telegram `get_messages`+`download_media` round-trip, never cached bytes in DB (D-16 preserved) | Yes | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Phase-23 test suite (edit/delete/send-file/incoming-media/download) | `pytest tests/test_phase23_inbox_mutations.py -q` (test-overlay) | 2 passed, 5 xfailed (pre-existing test-fixture artifacts, see Gaps note), 15 xpassed, **0 failed** | ✓ PASS |
| Regression: phase-5 inbox/takeover suite | `pytest tests/test_phase5_inbox_send_takeover.py tests/test_phase5_inbox.py tests/test_phase5_inbox_manager_mode.py -q` | 45 passed, 0 failed | ✓ PASS |
| Live route registration on running prod API container | `docker exec outreach-platform-api python -c "...router.routes..."` | All 4 new routes present | ✓ PASS |
| Prod DB schema matches migration 053/055 | `\d messages` on `outreach-platform-db` | `message_type`+CHECK, `file_name`/`mime_type`/`size_bytes`, `edited_at` all present with correct defaults | ✓ PASS |
| Human live-smoke (real Telegram account, real inbox UI) | Manual: edit / delete-for-everyone / send-file(photo) / incoming-media view | All 4 confirmed working (2 real bugs found and fixed during the session — see below) | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| INBM-01 | 23-02, 23-03 | Delete-for-everyone, no takeover | ✓ SATISFIED | DELETE endpoint + revoke + hard-delete verified; live-smoked |
| INBM-02 | 23-02, 23-03 | Edit sent text, no takeover | ✓ SATISFIED | PATCH endpoint + edited_at marker verified; live-smoked |
| INBM-03 | 23-02, 23-05 | Send file from inbox, auto-takeover | ✓ SATISFIED | POST /send-file + takeover ordering verified; live-smoked (post-fix) |
| INBM-04 | 23-04 | Incoming media recording (listener) | ✓ SATISFIED | save_message media params + classifier verified; test XPASS |
| INBM-05 | 23-02, 23-05 | Lazy media download | ✓ SATISFIED | GET .../download endpoint verified (path is `/download`, not the original `/file` draft — documented drift, no functional gap); live-smoked |
| INBM-06 | 23-02, 23-03 | Structured error codes | ✓ SATISFIED | `_INBOX_ERROR_STATUS` table + error-codes.md registry verified |
| INBM-07 | 23-03, 23-05 | REST-by-message_id API + workspace gate | ✓ SATISFIED | `_load_message_for_mutation` opaque-404 gate verified by code inspection |
| INBM-08 | 23-01 | `messages` schema extension | ✓ SATISFIED | Migration 053 verified applied on prod, idempotent, no ORM model added |
| INBM-09 | 23-06 | Frontend handoff regen | ✓ SATISFIED | openapi.json/error-codes.md regenerated + sibling repo UI implemented and pushed |

No orphaned requirements found — all 9 IDs in REQUIREMENTS.md §Phase 23 map to a plan and are checked `[x]`.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `tests/test_phase23_inbox_mutations.py` | `test_edit_too_old_returns_error`, `test_edit_not_modified_is_success_noop` | Test mocks the *entire* `edit_message_by_telegram_id` method with `side_effect=<raw Telethon exception>`, bypassing the method's own internal try/except mapping (which lives inside the mocked-out function) — the raw exception propagates to an unhandled 500 rather than exercising `_raise_inbox_message_error`. Statically `xfail`-marked, so it doesn't fail the suite. | ℹ️ Info | Not a production bug — verified by reading `edit_message_by_telegram_id` source: the real (unmocked) implementation catches `MessageEditTimeExpiredError`→`MESSAGE_EDIT_TOO_OLD` and `MessageNotModifiedError`→success no-op correctly (telegram.py:1729-1747). The test as written can never fire in prod since the real method never raises past its own except-block. No fix needed for goal achievement; flagged for future test-hygiene cleanup. |
| `tests/test_phase23_inbox_mutations.py` | `test_edit_cross_workspace_404`, `test_delete_cross_workspace_404`, `test_download_cross_workspace_404` | Pre-existing test-fixture bug (documented in `deferred-items.md`): `test_conversation_factory()` without an explicit `workspace_id` defaults to the SAME workspace as the JWT user, so these "cross-workspace" tests never actually cross tenants. Statically `xfail`-marked. | ℹ️ Info | Impact confined to test coverage of the isolation gate, not the gate itself — the `WHERE c.workspace_id = :wid` filter is identical (and correct) across all four endpoints by code inspection. Documented and deferred by the phase's own executor; not a functional gap. |
| REQUIREMENTS.md | INBM-05 line | Requirement text says `GET .../messages/{message_id}/file` but the implemented (and openapi-documented) route is `/download` | ℹ️ Info | Documentation drift only — the actual code, tests, openapi.json, and error-codes.md all consistently use `/download`. No functional impact. |

No blocker or warning-severity anti-patterns found in the phase's delivered code.

### Human Verification Required

None outstanding — Task 2 of plan 23-06 already performed live human verification against a real Telegram account and a real deployed inbox UI, confirming all four capabilities (edit, delete-for-everyone, send-file, incoming-media view) work correctly, including two real bugs found and fixed during that session (backend send-file photo/filename bug, frontend media-rendering gap). Two edge cases were explicitly deferred as untested-but-low-risk by the human at checkpoint close (not blocking):
- Caption >1024-char overflow (code path exists per D-13, mirrors the existing `send_file()` queue pattern, not independently smoke-tested this session).
- Sending a `.pdf` document and the >50MB `FILE_TOO_LARGE` case (code paths exist and are unit-tested — `test_send_file_too_large_returns_413` XPASS — just not live-smoked with a real account).

### Gaps Summary

No gaps blocking goal achievement. All 9 requirement IDs (INBM-01..09) are implemented, wired end-to-end (backend routes → Telethon service methods → DB → frontend mutations/rendering), deployed to production (verified live on the running API container and prod DB schema), and confirmed by both automated tests (test-overlay, 0 failures) and human live-smoke against a real Telegram account. The two live-smoke bugs found during 23-06 (send-file photo/filename mishandling, missing frontend media rendering) were fixed within the same session and are present in the current codebase (commits `62a4664`, `da3c0db`, `e424f53`) — not open gaps. The two `xfail`-masked test clusters are documented, understood, non-blocking test-fixture/test-mock artifacts, not production defects.

---

*Verified: 2026-07-08T11:00:51Z*
*Verifier: Claude (gsd-verifier)*
