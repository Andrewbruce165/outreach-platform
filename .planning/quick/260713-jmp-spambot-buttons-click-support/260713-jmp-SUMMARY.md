---
phase: quick-260713-jmp
plan: 01
status: complete
subsystem: spambot-chat-panel
tags: [spambot, telethon, listener, inbox, frontend, migration]
requirements: [260713-jmp]
dependency-graph:
  requires:
    - "260713-hiw: per-sender @SpamBot chat panel (_persist_spambot_message, status='spambot' conversation, SpambotChatPanel)"
  provides:
    - "messages.buttons JSONB column (persisted @SpamBot inline/reply-keyboard layout)"
    - "POST /conversations/{id}/messages/{message_id}/click — Telethon message.click(row,col) on the sender's own session"
    - "MessageResponse.buttons in GET /conversations/{id}/messages"
    - "SpambotChatPanel button grid + click mutation"
  affects:
    - "app/services/listener.py::_persist_spambot_message (additive; antispam path untouched)"
tech-stack:
  added: []
  patterns:
    - "Reuse of edit/delete_message_by_telegram_id skeleton (get_client -> _resolve_peer_by_telegram_id -> op -> disconnect) for the new click method"
    - "Idempotent raw-SQL migration + matching conftest _mig_063 block (messages has no ORM model)"
key-files:
  created:
    - migrations/063_messages_buttons.sql
    - tests/test_spambot_buttons.py
  modified:
    - tests/conftest.py
    - app/services/listener.py
    - app/services/telegram.py
    - app/routers/conversations.py
    - app/schemas/__init__.py
    - frontend/src/types/api.ts
    - frontend/src/components/SpambotChatPanel.tsx
decisions:
  - "buttons stored as minimal 2D {text} array matching Telethon row/col indexing — no button-type stored (Telethon .click handles the type)"
  - "click endpoint gates on an INBOUND workspace-scoped message (NOT _load_message_for_mutation, which requires outbound); foreign/missing -> opaque 404 MESSAGE_NOT_FOUND"
  - "no takeover / queue-cancel / status flip on click — a button press is not a manual message"
metrics:
  duration: ~25min
  tasks: 2
  files: 9
  completed: 2026-07-13
---

# Quick Task 260713-jmp: Capture and Render @SpamBot Buttons Summary

Inbound @SpamBot messages carrying an inline/reply keyboard now persist their button layout (`messages.buttons` JSONB, a 2D `{text}` array); the "Text to SpamBot" panel renders those buttons and a new workspace-scoped endpoint performs the actual Telegram click on the sender's own Telethon session.

## What Was Built

### Task 1 — Backend (commit e1319c1)
- **Migration 063** (`migrations/063_messages_buttons.sql`): idempotent `ALTER TABLE messages ADD COLUMN IF NOT EXISTS buttons JSONB` + matching `_mig_063` block in `tests/conftest.py` (messages is raw-SQL with no ORM model, so create_all never builds the column).
- **listener capture**: `_persist_spambot_message` now serializes `event.message.reply_markup` via a new module-level `serialize_reply_markup()` helper (rows -> cols -> `{"text": ...}`, `None` on empty/parse-error, never raises) and writes it into the inbound INSERT as `CAST(:buttons AS JSONB)`. `import json` added. `_handle_antispam_signal` byte-for-byte unchanged (verified via `git diff`).
- **TelegramService.click_message_button_by_telegram_id**: mirrors the edit/delete skeleton — `get_client` -> `_resolve_peer_by_telegram_id` -> `get_messages(ids=...)` -> `msg.click(row, col)`; FloodWait/frozen/generic mapped to structured error dicts; always `disconnect_client` in finally.
- **Schema**: `MessageResponse.buttons: Optional[list]`, new `ClickButtonRequest`/`ClickButtonResponse`.
- **Endpoint**: `POST /conversations/{id}/messages/{message_id}/click` (workspace + active/ok-sender gate on an inbound message; opaque 404; no takeover).
- **GET /messages** SELECT widened to return `m.buttons`.
- **Tests** (`tests/test_spambot_buttons.py`, 5 cases): button capture, plain-text NULL, GET returns buttons, cross-tenant 404, mocked click success.

### Task 2 — Frontend (commit ffa6f16)
- `types/api.ts`: `MessageResponse.buttons?: ({ text: string })[][] | null`.
- `SpambotChatPanel.tsx`: click `useMutation` posting to the new endpoint, per-message in-flight tracking (via `clickMut.variables.messageId`), row/col button grid rendered below each bubble, buttons disabled with a `Loader2` spinner while in-flight, `invalidateQueries(["messages", conversationId])` on success, `toast.error` on `ApiError`.

## Verification
- Backend targeted suite: `8 passed` via test-overlay (`test_spambot_buttons.py` + `test_spambot_conversation.py`).
- Frontend: `tsc --noEmit` (via `oven/bun:1`) reports only the 3 pre-existing `/login` route errors in `__root.tsx` / `_authenticated.tsx` / `settings.tsx` (out of scope) — zero new errors in touched files.
- `git diff app/services/listener.py` confirms no edits to `_handle_antispam_signal`.
- Queue rate limits / working hours / FloodWait retry untouched.

## Deviations from Plan
None functional. Test-run environment note: the worktree lacks the prod `.env` and uses a distinct compose project name, so the documented `docker compose ... run --rm api pytest` was invoked with `--env-file /root/apps/aimly/tg-outreach/.env -p tg-outreach` to reuse the already-running prod db container (tests still target the ephemeral `db-test`, prod DB untouched). Frontend typecheck ran inside `oven/bun:1` because `bun`/`bunx` are not installed on the host (frontend builds via that Docker image per CLAUDE.md).

## Known Stubs
None.

## Self-Check: PASSED
- Files: migrations/063_messages_buttons.sql, tests/test_spambot_buttons.py, app/services/telegram.py, app/routers/conversations.py — all FOUND.
- Commits e1319c1, ffa6f16 — both FOUND.
