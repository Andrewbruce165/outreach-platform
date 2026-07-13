# Quick Task 260713-hiw: Add 'Text to SpamBot' button on account page opening a side panel to chat with @SpamBot from that specific sender account - Context

**Gathered:** 2026-07-13
**Status:** Ready for planning

<domain>
## Task Boundary

On the account (sender) detail page, add a "Text to SpamBot" button. Clicking it opens a side panel (Sheet/slide-over) where the user can send/receive messages with Telegram's official @SpamBot (id `178220800`), impersonating that specific sender account. This lets managers manually check/negotiate account restriction status through a live chat, instead of only the existing one-shot automated `check_spambot` probe.

</domain>

<decisions>
## Implementation Decisions

### Visibility
- SpamBot conversation must NOT appear in the normal Inbox conversation list.
- Give it a dedicated `status` value (e.g. `'spambot'`) distinct from `'active'`/`'manual'`/`'telegram_service'`/`'bot_ignored'` so existing Inbox list queries (which filter/display by status) exclude it by default.
- Only reachable via the new side panel launched from the account page.

### Antispam parsing during live chat
- Keep existing antispam-text parsing/restriction_status updates active even while the "Text to SpamBot" panel is open and the user is manually chatting.
- Do NOT special-case or suppress `_handle_antispam_signal` behavior for this flow — a real restriction message from SpamBot during a manual chat should still update `sender.restriction_status`/`restricted_until` exactly like it does today.
- In addition to existing antispam signal handling, the SpamBot message must ALSO be persisted to `conversations`/`messages` (new behavior) so it renders in the side panel — mirror the pattern used for `_handle_telegram_service_message` (persists AND is excluded from AI dispatch via `ai_enabled=false`).

### Conversation lifecycle
- Get-or-create a conversation per sender with `contact_telegram_id=178220800` (SpamBot's real Telegram user id), `ai_enabled=false`, dedicated status.
- New backend endpoint to get-or-create this conversation for a given sender slug (mirrors the existing `GET /senders/{slug}/spambot-check` pattern for auth/ownership checks).
- Sending messages reuses the EXISTING `POST /conversations/{id}/send` endpoint — no new send mechanism needed.

### Frontend
- Button lives in `frontend/src/routes/_authenticated/accounts.tsx`, in the per-account actions area near the existing "Check Spam Bot" button (`spambotMut`, ~line 611-624).
- Side panel is a slimmed-down variant of the `Thread` component pattern from `inbox.tsx` (message list + composer) — does NOT need AI-trace tabs, tags, or the full `RightPane` — just messages + send box, scoped to the one conversation.

### Claude's Discretion
- Exact `status` enum value name for the SpamBot conversation (e.g. `'spambot'`) — pick one that fits the existing CHECK constraint / migration pattern used for `conversations.status`.
- Whether the side panel is a shadcn `Sheet` or similar — follow existing component conventions in the frontend.
- Polling/refresh strategy for incoming SpamBot replies while panel is open (e.g. reuse whatever polling/query invalidation the Inbox Thread already uses).

</decisions>

<specifics>
## Specific Ideas

No additional specific UI mockups — reuse existing Inbox `Thread` visual style for consistency, just in a side-panel/sheet instead of the 3-pane full page layout.

</specifics>

<canonical_refs>
## Canonical References

- `app/services/telegram.py` — `check_spambot()` (413-458), `classify_spambot_text()` (114-132), `mark_spambot_selfcheck`/`is_spambot_selfcheck` (334-344), `send_message_by_telegram_id` (1207+), `resolve_contact`/`_resolve_username` (626-777)
- `app/services/listener.py` — `ANTISPAM_BOT_IDS` (119), main incoming handler antispam branch (812-821), `_handle_antispam_signal` (1114-1235), `_handle_telegram_service_message` (1319+) as the pattern to mirror for persistence, `get_or_create_conversation` (473-561), AI-dispatch gate (1042)
- `app/routers/senders.py` — `GET /senders/{slug}/spambot-check` (938-1079) as the auth/ownership pattern for the new endpoint
- `app/routers/conversations.py` — existing send/disable-ai/enable-ai endpoints (236-1307), reused as-is
- `frontend/src/routes/_authenticated/accounts.tsx` — `SenderCard` (from 399), actions menu with `spambotMut` (611-624), `SpambotResultModal` (939-991)
- `frontend/src/routes/_authenticated/inbox.tsx` — `Thread` component (1222+) as the chat-panel template, `MessageBubble` (2037+)

</canonical_refs>
