# Backend error codes → UI strings (en-US)

Backend uses HTTPException with `detail: {code, message}` envelope (Phase 1 D-04 / Phase 4 / Phase 5 pattern). Frontend maps `code` → friendly English; never displays raw `message` (often English internal-speak).

| Backend code | HTTP | UI string | Notes |
|---|---|---|---|
| `TOKEN_EXPIRED` | 401 | "Your session expired. Sign in again." | Auto-redirect to /login |
| `TOKEN_INVALID` | 401 | "Your session expired. Sign in again." | Same handling as TOKEN_EXPIRED |
| `WORKSPACE_NOT_FOUND` | 404 | "Workspace not found" | Should not occur post-login |
| `CAMPAIGN_NOT_FOUND` | 404 | "Campaign not found" | Cross-workspace also returns this (silent isolation per Phase 1 D-04) |
| `SENDER_NOT_FOUND` | 404 | "Account not found" | UI calls them "accounts", not "senders" |
| `AGENT_NOT_FOUND` | 404 | "Agent not found" | |
| `FOLDER_NOT_FOUND` | 404 | "Folder not found" | |
| `CONVERSATION_NOT_FOUND` | 404 | "Conversation not found" | |
| `INVALID_TRANSITION` | 409 | "Can't change from {from} to {to}" | Surface `from`/`to` from detail |
| `SENDER_LOCK_CONFLICT` | 409 | "Sender {name} is locked by campaign {other}. Stop that campaign to free the sender." | UI-SPEC §8.3 |
| `NO_SENDERS_ATTACHED` | 422 | "Attach at least one account before launching" | Builder step 3 validation hint |
| `UNKNOWN_EVENT` | 400 | "Internal: unknown telemetry event" | Debug-only; should not reach user — log to console.warn |
| `ID_REQUIRED` | 422 | "Missing id parameter" | Used by /analytics/funnel + /analytics/llm |
| `INVALID_PHONE` | 422 | "Phone number is invalid. Use +1 415 555 2810 format." | Onboarding step 1 |
| `CHECKER_ROLE_CONFLICT` | 409 | "This account checks contacts. Adding it to a campaign (or switching it to the checker pool while a campaign runs) can get it blocked for both jobs. Add it anyway?" | Two call-sites: **(a)** `POST /campaigns/{id}/senders` — the account is `role='checker'`; **(b)** `PATCH /senders/{slug}` flips an in-running-campaign sender to `role='checker'`. Resolve by pausing/finishing the campaign, or re-send the same request with `force: true`. `detail.sender_id` identifies the account. |
| `FILE_TOO_LARGE` | 413 | "File is larger than 50 MB." | 50 MB cap — applies to **both** campaign attachment upload and inbox `POST /conversations/{id}/send-file`. Surface the limit before retry; nothing is sent |
| (generic 5xx) | 5xx | "Server is unreachable. Retry." | Sonner toast with Retry action |

Reference: when in doubt, fall back to UI-SPEC §8.3 copywriting contract.

## Inbox message operations (Phase 23 / D-17)

The four inbox mutation endpoints — edit (`PATCH /conversations/{id}/messages/{message_id}`),
delete-for-everyone (`DELETE …/messages/{message_id}`), send-file
(`POST /conversations/{id}/send-file`), and lazy download
(`GET …/messages/{message_id}/download`) — return the same `{code, message}` envelope.
Frontend renders a Sonner toast and rolls back the optimistic UI (D-17). HTTP statuses below
mirror `_raise_inbox_message_error` / `_INBOX_ERROR_STATUS` in `app/routers/conversations.py`.

| Backend code | HTTP | UI string | Notes |
|---|---|---|---|
| `MESSAGE_EDIT_TOO_OLD` | 409 | "This message is too old to edit." | Telegram `MessageEditTimeExpiredError` — the edit window is **server/Telegram-controlled**; the exact window is not client-configurable |
| `MESSAGE_NOT_EDITABLE` | 422 | "This message can't be edited." | Non-text or otherwise non-editable message (e.g. media without editable text) |
| `MESSAGE_NOT_FOUND` | 404 | "Message not found" | Cross-workspace / inbound / non-existent message all return this — silent tenant isolation, no existence leak |
| `DELETE_FAILED` | 502 | "Couldn't delete the message. Try again." | Real connection/flood/frozen failure during revoke (not a validation error) |
| `MEDIA_UNAVAILABLE` | 410 | "This file is no longer available in Telegram." | Incoming file's bytes are gone from Telegram at download time |
| `DOWNLOAD_FAILED` | 502 | "Couldn't download the file. Try again." | Transient failure fetching the incoming file's bytes |
| `TELEGRAM_OP_FAILED` | 502 | "Telegram operation failed. Try again." | Generic fallback — any unmapped Telethon error collapses to this |

**Reused codes** (already documented above) also apply to these endpoints: `NO_TELEGRAM_ID` (400),
`RECIPIENT_NOT_IN_TELEGRAM` (422), `FLOOD_WAIT` (429, carries `retry_after`), `ACCOUNT_FROZEN` (409),
`USER_IS_BLOCKED` (409), and `FILE_TOO_LARGE` (413, send-file 50 MB cap).

**Field-alias tolerance (D-22):** the multipart `send-file` form and `EditMessageRequest` body
accept Lovable field aliases — `message` / `message_text` / `text` all map to the message text —
so a Lovable-generated form that posts any of those names is accepted without a spec mismatch.

## Advisory warnings (non-blocking) — `CampaignResponse.attach_warnings[]`

`POST /campaigns/{id}/senders` returns **200** even when it wants to warn the user. The
warnings ride in `attach_warnings[]` (empty `[]` on every other endpoint and on a clean
attach). Each item is `{code, sender_id, message, event_type?, restricted_until?, last_event_at?}`.
Render them as an **amber banner** (not an error toast) — the attach already succeeded.

| Warning code | Meaning | Suggested UI |
|---|---|---|
| `RECENT_RESTRICTION` | The attached account hit a restriction event (`event_type`, e.g. `spam_limited`/`frozen`) in the last 7 days — the "green corridor" pre-flight. Attaching may re-trigger anti-spam. | Amber banner: "This account was restricted recently ({event_type}). Verify it via @SpamBot before sending." Surface `last_event_at`/`restricted_until` if present. |
| `CHECKER_FORCE_ATTACHED` | A `role='checker'` account was force-attached (`force: true`) as a campaign sender — it will leave the contact-check pool once it sends. | Amber banner confirming the override: "Checker account added as a sender — it will stop checking contacts once it sends." |

Note: live inline @SpamBot pre-flight on attach is a documented follow-up; the manual
`GET /senders/{slug}/spambot-check` remains the on-demand "green corridor" verification.
