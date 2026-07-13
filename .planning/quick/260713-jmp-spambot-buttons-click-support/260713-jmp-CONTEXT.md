# Quick Task 260713-jmp: Capture and render SpamBot inline/reply-keyboard buttons in the Text to SpamBot chat panel - Context

**Gathered:** 2026-07-13
**Status:** Ready for planning

<domain>
## Task Boundary

Follow-up to quick task 260713-hiw ("Text to SpamBot" per-sender chat panel). Currently the panel only shows/sends plain text — if @SpamBot's reply carries an inline keyboard or a custom reply keyboard (buttons), that button layout is silently dropped: `listener._persist_spambot_message` only stores `event.text`, never `event.message.reply_markup`. This task adds: (1) capturing and persisting the button layout on inbound @SpamBot messages, (2) rendering clickable buttons under the message bubble in `SpambotChatPanel`, (3) a backend endpoint that performs the actual Telegram "click" (Telethon `message.click(row, col)`, which transparently handles both callback-query inline buttons and reply-keyboard text-buttons) using that specific sender's own Telethon session.

</domain>

<decisions>
## Implementation Decisions

### Storage
- Add a new nullable `messages.buttons` JSONB column (migration, next sequential number after whatever is committed on main at execution time — check `migrations/` before picking the number, there was a numbering collision with a concurrent task on 062, verify current max first).
- Serialize `event.message.reply_markup` into a small JSON structure the frontend can render directly, e.g. a 2D array of `{text: string}` per row/col, matching Telethon's own row/col addressing (`message.click(row, col)` uses these same indices) — so the click endpoint can address a button purely by `(row, col)` without re-parsing Telegram's raw reply_markup format.
- If `reply_markup` is absent (plain text message, the common case), `buttons` stays NULL — no behavior change from what 260713-hiw already does.

### Click endpoint
- New endpoint, e.g. `POST /conversations/{conversation_id}/messages/{message_id}/click` with body `{row: int, col: int}`.
- Workspace-scoped (same auth pattern as other conversation endpoints — `_load_conversation_or_404` or equivalent).
- Implementation: resolve the sender + Telethon client for this conversation (same `get_client`/`send_message_by_telegram_id` machinery used elsewhere), fetch the live Telegram message via `client.get_messages(spambot_entity, ids=telegram_message_id)`, call `.click(row, col)` on it. This single Telethon call transparently handles BOTH inline-keyboard buttons (fires a callback query) and custom reply-keyboard buttons (sends the button's label text as a normal message) — no need to branch on button type.
- After a successful click, whatever @SpamBot sends back arrives through the normal listener path (`_persist_spambot_message`) exactly like any other inbound message — no special handling needed for the response.
- Restriction/antispam text parsing (`_handle_antispam_signal`) must remain completely untouched by this task, same rule as 260713-hiw — a button click can trigger a real restriction-status-bearing reply from SpamBot, and that must still flow through the existing (unmodified) parsing path.

### Frontend
- `SpambotChatPanel.tsx`: when a message has non-null `buttons`, render them as a grid of small buttons below that message bubble (rows = outer array, cols = inner array), each dispatching the click mutation with its own `(row, col)`.
- While a click is in-flight, disable that message's buttons (avoid double-click / racing two clicks) and show a lightweight pending indicator; on success, invalidate the messages query (same `queryKey: ["messages", conversation_id]` pattern already used for send) so the bot's response appears via the existing 10s poll / immediate refetch.
- On `ApiError` from the click endpoint, `toast.error` — same error-handling convention as the send composer.

### Claude's Discretion
- Exact JSON shape for `buttons` (e.g. whether to also store the button's Telegram-side "type", url-button vs text-button vs callback-button) — keep it minimal (label text is enough to render and to know which (row,col) to click); Telethon's `.click()` handles the type distinction internally, we don't need to replicate that logic in our own storage.
- Whether to disable/hide buttons on a message once any button in it has been clicked (Telegram inline keyboards commonly become stale/one-shot after use) — reasonable default: leave them visible/clickable (SpamBot's flows are typically idempotent single-choice menus you might need to reopen), don't over-engineer a "used" state unless it's trivial.
- Migration number — pick the correct next sequential number by checking the actual `migrations/` directory state at execution time (there was a recent 062 collision with a concurrent task; that may or may not be resolved/renumbered by the time this executes).

</decisions>

<specifics>
## Specific Ideas

No additional UI mockups — buttons should look like simple, small, secondary-style buttons (reuse existing shadcn Button styling already used elsewhere in the panel), laid out in the same row/col grid Telegram itself uses.

</specifics>

<canonical_refs>
## Canonical References

- `app/services/listener.py` — `_persist_spambot_message` (~1416-1494, from quick task 260713-hiw) — extend to capture `event.message.reply_markup`. Wired into both antispam branches (~813-826 bot-flag, ~838-857 keyword-backup).
- `app/routers/senders.py` — `POST /senders/{slug}/spambot-conversation` (from 260713-hiw) — auth/pattern reference, not modified by this task.
- `app/routers/conversations.py` — existing send endpoint (`POST /{conversation_id}/send`) and message-fetch (`GET /{conversation_id}/messages`) as the patterns for the new click endpoint's auth/response shape.
- `frontend/src/components/SpambotChatPanel.tsx` (from 260713-hiw) — message list + composer, extend to render buttons.
- `messages` table (raw SQL, no ORM model) — current columns confirmed via `\d messages`: id, workspace_id, conversation_id, direction, message_text, sent_by, telegram_message_id, created_at, message_type, file_name, mime_type, size_bytes, edited_at. New `buttons JSONB` column needs a migration (raw SQL, idempotent `ADD COLUMN IF NOT EXISTS`) since there's no ORM model for this table (per project pattern: raw-SQL migrations required, ORM create_all won't build this column).
- `tests/conftest.py` — mirrors migration gaps not covered by ORM create_all (see the existing 045/046/060/061/062 blocks) — the new migration for `buttons` needs a matching conftest block or new tests will fail with "column does not exist", exactly like the pattern hit twice already in 260713-hiw and its predecessor lead_pending task.

</canonical_refs>
