---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
plan: 03
subsystem: api
tags: [fastapi, telethon, inbox, conversations, edit-message, delete-message, workspace-isolation]

# Dependency graph
requires:
  - phase: 23-01
    provides: EditMessageRequest schema + MessageResponse media/edit fields + migration 053/055 messages media columns
  - phase: 23-02
    provides: telegram_service.edit_message_by_telegram_id / delete_message_by_telegram_id (structured-dict returns) + _resolve_peer_by_telegram_id
provides:
  - "PATCH /api/v1/conversations/{id}/messages/{message_id} — edit outbound text for everyone, no takeover"
  - "DELETE /api/v1/conversations/{id}/messages/{message_id} — delete-for-everyone (revoke) + hard-delete row, no takeover"
  - "_raise_inbox_message_error — service error-dict → HTTP status mapper (D-17 codes)"
  - "_load_message_for_mutation — outbound + sent_by + workspace + optional text gate, opaque 404 (D-19)"
  - "GET /messages now returns message_type/file_name/mime_type/size_bytes/edited_at"
affects: [23-05 send-file/download endpoints (same file, Wave 3), frontend inbox message actions]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Inverted ordering for past-message mutation: Telethon op FIRST, then DB write (opposite of send-takeover)"
    - "Opaque 404 silent tenant isolation: cross-ws / inbound / contact / non-text all collapse to MESSAGE_NOT_FOUND"

key-files:
  created: []
  modified:
    - app/routers/conversations.py

key-decisions:
  - "Edit/delete invert send ordering (Telethon first, DB second) so a Telegram failure never leaves an orphan DB mutation"
  - "Neither endpoint touches conversations.status/ai_enabled/paused_reason/message_queue — past-edit is NOT a takeover (D-04/D-08)"
  - "DELETE_FAILED reserved for real connection/flood/frozen failures; stale/own-message revoke is a silent Telegram success (Pitfall 4)"

patterns-established:
  - "Pattern: _load_message_for_mutation single-SELECT gate joining messages→conversations→senders, require_type_text toggle for edit"
  - "Pattern: _raise_inbox_message_error central D-17 code→status table with unknown→502 TELEGRAM_OP_FAILED fallback"

requirements-completed: [INBM-01, INBM-02, INBM-06, INBM-07]

# Metrics
duration: ~15min
completed: 2026-07-08
---

# Phase 23 Plan 03: Edit & Delete Endpoints (No Takeover) Summary

**PATCH/DELETE inbox message endpoints that invert send ordering (Telethon first, then DB), edit outbound text or delete-for-everyone WITHOUT taking over the dialog, behind an opaque workspace+outbound gate, plus a widened GET /messages SELECT.**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-07-08
- **Tasks:** 3
- **Files modified:** 1 (`app/routers/conversations.py`)

## Accomplishments

- `_raise_inbox_message_error` helper: maps the D-17 structured service error codes (MESSAGE_EDIT_TOO_OLD→409, MESSAGE_NOT_EDITABLE→422, DELETE_FAILED→502, FILE_TOO_LARGE→413, NO_TELEGRAM_ID→400, RECIPIENT_NOT_IN_TELEGRAM→422, FLOOD_WAIT→429 with retry_after, ACCOUNT_FROZEN→409, USER_IS_BLOCKED→409, MEDIA_UNAVAILABLE→410, DOWNLOAD_FAILED→502) to HTTPException; unknown → 502 TELEGRAM_OP_FAILED.
- `_load_message_for_mutation` gate: single SELECT joining messages→conversations→senders, filtered on `m.direction='outbound' AND m.sent_by IN ('ai','human') AND c.workspace_id=:wid`, with an optional `m.message_type='text'` clause for edit. Any non-match (cross-ws, inbound, contact-sent, wrong-conversation, non-text) → opaque `MESSAGE_NOT_FOUND` 404 (D-19).
- `PATCH /{id}/messages/{message_id}`: text-only gate → Telethon `edit_message_by_telegram_id` (with sender_id + fingerprint) → on success `UPDATE messages SET message_text, edited_at=NOW()` → return re-SELECTed MessageResponse. No conversations/queue writes (D-08).
- `DELETE /{id}/messages/{message_id}` (204): outbound gate → Telethon `delete_message_by_telegram_id` (revoke default) → on success `DELETE FROM messages`. No conversations/queue writes (D-04); list-preview LATERAL auto-recomputes (D-03).
- Widened `GET /{id}/messages` SELECT to return `message_type, file_name, mime_type, size_bytes, edited_at`.

## Task Commits

1. **Task 1: error helper + mutation gate + widened GET /messages SELECT** — `a549ee6` (feat)
2. **Task 2: PATCH edit endpoint (no takeover)** — `d73053b` (feat)
3. **Task 3: DELETE revoke endpoint (no takeover)** — `b7a54be` (feat)

## Files Created/Modified

- `app/routers/conversations.py` — added `_INBOX_ERROR_STATUS` table + `_raise_inbox_message_error` + `_load_message_for_mutation` helpers, widened GET /messages SELECT, added PATCH edit + DELETE revoke endpoints. Imported `EditMessageRequest`.

## Decisions Made

- Followed plan as specified: inverted ordering, no-takeover, opaque-404 gate, D-17 error mapping.

## Deviations from Plan

None — plan executed exactly as written for the owned file (`app/routers/conversations.py`).

## Issues Encountered

**STALE BASE (critical, orchestrator action required).** This executor ran in a worktree whose merge-base with `main` is `92bd54b` — **Phase 19**, five phases behind `main`'s HEAD `a491c0d` (Phase 24). Consequently the worktree does **NOT** contain any of this plan's Wave-1 prerequisites:

- migration 053/055 (messages `message_type`/`file_name`/`mime_type`/`size_bytes`/`edited_at` columns) — absent
- `EditMessageRequest` schema + `MessageResponse` media fields (plan 23-01) — absent
- `telegram_service.edit_message_by_telegram_id` / `delete_message_by_telegram_id` (plan 23-02) — absent
- `tests/test_phase23_inbox_mutations.py` — absent
- (Note: the worktree's `send_message_by_telegram_id` also has an older signature without `sender_id`/`fingerprint`.)

Per the parallel-executor stale-base protocol, the plan was implemented **fully against the PLAN's interfaces** (i.e. `main`'s post-Wave-1 reality — calls pass `sender_id=str(row.sender_id)` and `fingerprint=row.client_fingerprint`, imports `EditMessageRequest`, and the SELECTs reference the media columns). Edit anchors were chosen to be identical between the worktree and `main` (the `_load_conversation_or_404` return block, the un-widened GET /messages SELECT, the schema import block, and the `get_messages` return block) so the three commits cherry-pick cleanly onto `main`.

**Verification could not be run in this worktree** — the test file and DB columns do not exist here. Only `python3 -m py_compile app/routers/conversations.py` was run (passed) after each task. The plan's pytest verify commands (`-k messages_select`, `-k edit`, `-k delete`, and the phase-5 no-regression run) must be run on `main` after the orchestrator cherry-picks commits `a549ee6`, `d73053b`, `b7a54be`.

**No .planning/STATE.md / ROADMAP.md / REQUIREMENTS.md commits were made** — the worktree copies are at Phase 19 and behind `main`; the orchestrator should apply those updates on `main` directly.

## Next Phase Readiness

- Wave 3 (plan 23-05, send-file + download endpoints) touches the same `conversations.py` — the `_raise_inbox_message_error` helper (with FILE_TOO_LARGE/MEDIA_UNAVAILABLE/DOWNLOAD_FAILED codes already mapped) and the `_load_message_for_mutation` gate are ready for reuse.
- **Blocker for the orchestrator:** cherry-pick the three feat commits onto `main` (which has 23-01/23-02), then run the plan's pytest verification there before marking 23-03 verified.

## Self-Check: PASSED

- `app/routers/conversations.py` — FOUND
- `23-03-SUMMARY.md` — FOUND
- Commits `a549ee6`, `d73053b`, `b7a54be`, `2f93f7f` — all FOUND
- Note: pytest verification deferred to `main` (stale-base worktree lacks prerequisites; see Issues Encountered).

---
*Phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui*
*Completed: 2026-07-08*
