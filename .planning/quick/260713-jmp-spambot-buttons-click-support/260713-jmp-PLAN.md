---
phase: quick-260713-jmp
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/063_messages_buttons.sql
  - tests/conftest.py
  - app/services/listener.py
  - app/services/telegram.py
  - app/routers/conversations.py
  - app/schemas/__init__.py
  - tests/test_spambot_buttons.py
  - frontend/src/types/api.ts
  - frontend/src/components/SpambotChatPanel.tsx
autonomous: true
requirements: [260713-jmp]
must_haves:
  truths:
    - "Inbound @SpamBot messages carrying an inline/reply keyboard persist their button layout (messages.buttons is non-NULL); plain-text replies keep buttons NULL"
    - "GET /conversations/{id}/messages returns each message's buttons field (2D array of {text}) so the panel can render it"
    - "POST /conversations/{id}/messages/{message_id}/click {row,col} clicks that button on the sender's OWN Telethon session and returns success"
    - "A button click's SpamBot reply arrives via the existing unchanged listener path (no special response handling)"
    - "_handle_antispam_signal restriction parsing is byte-for-byte unchanged"
    - "SpambotChatPanel renders clickable buttons under a bubble, disables them while a click is in-flight, invalidates the messages query on success, toast.error on ApiError"
  artifacts:
    - path: "migrations/063_messages_buttons.sql"
      provides: "messages.buttons JSONB column (idempotent ADD COLUMN IF NOT EXISTS)"
      contains: "buttons"
    - path: "app/services/telegram.py"
      provides: "click_message_button_by_telegram_id(...) — get_client → resolve peer → get_messages(ids) → .click(row,col)"
      contains: "click_message_button_by_telegram_id"
    - path: "app/routers/conversations.py"
      provides: "POST /{conversation_id}/messages/{message_id}/click endpoint"
      contains: "/messages/{message_id}/click"
    - path: "frontend/src/components/SpambotChatPanel.tsx"
      provides: "button grid rendering + click mutation"
      contains: "buttons"
  key_links:
    - from: "app/services/listener.py::_persist_spambot_message"
      to: "messages.buttons column"
      via: "serialize event.message.reply_markup into INSERT"
      pattern: "reply_markup"
    - from: "app/routers/conversations.py::click endpoint"
      to: "telegram_service.click_message_button_by_telegram_id"
      via: "resolved sender session"
      pattern: "click_message_button_by_telegram_id"
    - from: "frontend/src/components/SpambotChatPanel.tsx"
      to: "POST /conversations/{id}/messages/{message_id}/click"
      via: "useMutation"
      pattern: "/click"
---

<objective>
Follow-up to quick task 260713-hiw. The "Text to SpamBot" per-sender chat panel currently drops any button layout @SpamBot sends: `listener._persist_spambot_message` stores only `event.text`, never `event.message.reply_markup`. This task:
1. Persists the button layout on inbound @SpamBot messages (new `messages.buttons` JSONB column).
2. Returns `buttons` from `GET /conversations/{id}/messages` and renders clickable buttons in `SpambotChatPanel`.
3. Adds a backend endpoint that performs the actual Telegram "click" on the sender's own Telethon session (`message.click(row, col)`, which transparently handles both inline callback-query buttons and reply-keyboard text-buttons).

Purpose: managers can drive @SpamBot's button-menu flows (appeal, status, unfreeze) from the panel instead of only sending free text.
Output: 1 migration, listener capture, a TelegramService click method, a workspace-scoped click endpoint, schema + tests, and the frontend button UI.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/quick/260713-jmp-spambot-buttons-click-support/260713-jmp-CONTEXT.md
@.planning/quick/260713-hiw-add-text-to-spambot-button-on-account-pa/260713-hiw-SUMMARY.md

<constraints>
- CLAUDE.md: async everywhere, no time.sleep/print/sync requests; migrations = idempotent raw SQL in migrations/NNN_name.sql (auto-applied at api start, fail-fast); tests ONLY via `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`.
- Do NOT touch queue rate limits, working hours, or FloodWait retry logic.
- Do NOT modify `_handle_antispam_signal` (restriction/antispam text parsing). The click endpoint clicks a button; SpamBot's reply flows back through the UNCHANGED listener antispam path exactly like today.
- Parallel-agent memory: another agent may be editing this checkout. Never `git add -A`; stage only the files this plan touches.
</constraints>

<migration_number>
Verified live at plan time: `migrations/` max is a DUPLICATE `062_` (062_conversations_status_spambot.sql from 260713-hiw + 062_sender_proxy_switch_pending.sql from a concurrent task — collision still unresolved). Next free sequential number is **063**. Re-run `ls migrations/ | sort | tail -6` at execution start and confirm no `063_*` already landed; if it did, bump to the next free number and update every reference below + the conftest block.
</migration_number>

<interfaces>
Backend patterns to reuse verbatim (do NOT re-explore the codebase):

listener._persist_spambot_message (app/services/listener.py ~1416-1494) — the ONLY method to extend. It already receives the Telethon `event`; `event.message.reply_markup` is available in the same scope. The inbound INSERT is at ~1475-1486:
```python
await session.execute(text("""
    INSERT INTO messages
        (workspace_id, conversation_id, direction, message_text,
         sent_by, telegram_message_id)
    VALUES (:wid, :cid, 'inbound', :txt, 'contact', :tmid)
    ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING
"""), {...})
```
Both antispam call-sites (listener.py ~824 and ~853) already invoke `_persist_spambot_message` — no call-signature change needed.

TelegramService method pattern (app/services/telegram.py) — mirror edit_message_by_telegram_id (~1800-1874) / delete_message_by_telegram_id (~1876):
```python
client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)
peer = await self._resolve_peer_by_telegram_id(client, telegram_id)   # cache → get_dialogs(200) → retry (helper at ~1787)
# ... op ...
finally: await self.disconnect_client(client)
```
FloodWaitError → {"code":"FLOOD_WAIT","retry_after":e.seconds}; is_frozen_error(e) → {"code":"ACCOUNT_FROZEN"}. Return {"success": True/False, "error": {...}} dicts.

conversations.py error mapping — `_raise_inbox_message_error(result)` (~124) already maps FLOOD_WAIT→429, ACCOUNT_FROZEN→409, unknown→502. Add any new codes to `_INBOX_ERROR_STATUS` (~109) if needed.

MessageResponse (app/schemas/__init__.py ~1179) — all Phase-23 media fields are `Optional`/defaulted so the model constructs even when the SELECT omits them. Add `buttons: Optional[list] = None` the same way.

get_messages SELECT (conversations.py ~408-418) — widen to add `m.buttons`.

Frontend: types/api.ts MessageResponse (~4216) is hand-maintained (openapi-typescript present but NO codegen script) — manually add the `buttons` field. SpambotChatPanel.tsx (full file, 165 lines) uses `queryKey:["messages", conversationId]`, `api(...)`, `ApiError`, `toast.error`, shadcn — reuse those.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Backend — capture buttons, click endpoint, migration + tests</name>
  <files>migrations/063_messages_buttons.sql, tests/conftest.py, app/services/listener.py, app/services/telegram.py, app/routers/conversations.py, app/schemas/__init__.py, tests/test_spambot_buttons.py</files>
  <behavior>
    - _persist_spambot_message with an inbound event whose message.reply_markup has 2 rows → messages.buttons = [[{"text":"A"},{"text":"B"}],[{"text":"C"}]] (row/col matches Telethon indexing).
    - _persist_spambot_message with reply_markup=None (plain text) → messages.buttons IS NULL (no behavior change).
    - GET /conversations/{id}/messages returns each row's `buttons` field.
    - POST /conversations/{id}/messages/{message_id}/click for a message in a workspace foreign conversation → 404 MESSAGE_NOT_FOUND (tenant isolation, no existence leak).
    - Click on a valid spambot message → calls telegram_service.click_message_button_by_telegram_id with the resolved sender session + (row,col); a mocked success returns {"success": true}.
    - _handle_antispam_signal remains byte-for-byte unchanged (diff shows no edits to that method).
  </behavior>
  <action>
1. **Migration** `migrations/063_messages_buttons.sql` (confirm 063 is free first — see <migration_number>). Header comment block like 061; body:
   ```sql
   BEGIN;
   ALTER TABLE messages ADD COLUMN IF NOT EXISTS buttons JSONB;
   COMMIT;
   ```
   Idempotent, auto-applier-safe.

2. **conftest block** in `tests/conftest.py` — add a `_mig_063` block RIGHT AFTER the `_mig_062` block (~line 325), mirroring the existing 060/061/062 pattern exactly (hardcoded filename, `if _mig_063.exists(): await asyncpg_conn.execute(_mig_063.read_text())`, with a comment: `messages` is raw-SQL (mig 017), NO ORM model, so create_all never builds `buttons` — apply the migration here or button tests fail with "column buttons does not exist"). This is the gap that bit the last two tasks — do NOT skip it.

3. **listener capture** — in `_persist_spambot_message` (app/services/listener.py ~1441-1486): before the inbound INSERT, serialize `event.message.reply_markup` into a 2D array. Write a small local helper (module-level fn or inline): iterate `reply_markup.rows`, for each row iterate `row.buttons`, emit `{"text": getattr(b, "text", "") or ""}` per button; result is `list[list[dict]]`, or `None` if reply_markup is falsy or has no rows. Wrap in try/except → on any parse error set buttons=None and log (never crash the loop / never raise into the antispam path). Add `buttons` to the INSERT column list + VALUES as `CAST(:buttons AS JSONB)` and bind `json.dumps(buttons) if buttons else None` (add `import json` if not already imported at top of listener.py). Do NOT touch `_handle_antispam_signal` or its call-sites.

4. **TelegramService.click_message_button_by_telegram_id** in app/services/telegram.py, mirroring edit_message_by_telegram_id: signature `(self, sender_slug, sender_id, encrypted_session, telegram_id, telegram_message_id, row, col, proxy=None, fingerprint=None) -> dict`. Body: get_client → `peer = await self._resolve_peer_by_telegram_id(client, telegram_id)` → `msg = await client.get_messages(peer, ids=telegram_message_id)` → if msg is None return {"success": False, "error": {"code":"MESSAGE_NOT_FOUND","message":"..."}} → `await msg.click(row, col)` → return {"success": True}. Catch FloodWaitError → FLOOD_WAIT+retry_after; `is_frozen_error(e)` → ACCOUNT_FROZEN; generic Exception → {"code":"TELEGRAM_OP_FAILED","message": str(e)}. `finally: await self.disconnect_client(client)`. `.click(row,col)` transparently handles inline (callback) AND reply-keyboard (text) buttons — do NOT branch on type.

5. **Schema** app/schemas/__init__.py: add `buttons: Optional[list] = None` to `MessageResponse` (~1194, alongside the media fields). Add a new `class ClickButtonRequest(BaseModel): row: int = Field(..., ge=0); col: int = Field(..., ge=0)` and a `class ClickButtonResponse(BaseModel): success: bool`.

6. **Endpoint** in app/routers/conversations.py: `POST /{conversation_id}/messages/{message_id}/click` under `auth_dep`. Import the new schemas + add to the existing schema import block. Load the target message with a workspace-scoped query joining messages→conversations→senders (the button-bearing message is INBOUND from @SpamBot, so do NOT reuse `_load_message_for_mutation` which filters `direction='outbound'`): SELECT `m.telegram_message_id, c.contact_telegram_id, s.slug, s.id AS sender_id, s.session_string, s.proxy, s.client_fingerprint` WHERE `m.id=:mid AND m.conversation_id=:cid AND c.workspace_id=:wid` (gate sender `lifecycle_status='active' AND auth_status='ok'`); None → 404 MESSAGE_NOT_FOUND. Call `telegram_service.click_message_button_by_telegram_id(...)` with body.row/col; on `not result.get("success")` → `_raise_inbox_message_error(result)`; return `ClickButtonResponse(success=True)`. Do NOT do any takeover / queue-cancel / status flip — a button click is not a manual message. Add any new error codes to `_INBOX_ERROR_STATUS` if you introduced them (MESSAGE_NOT_FOUND already maps via 404 in-endpoint; TELEGRAM_OP_FAILED already collapses to 502).

7. **Tests** `tests/test_spambot_buttons.py`: cover the <behavior> cases. Mock `telegram_service.click_message_button_by_telegram_id` for the endpoint success/isolation tests (no live Telethon). For the listener-capture test, build a fake event with a `reply_markup` object exposing `.rows[].buttons[].text` and call `_persist_spambot_message`, then assert the persisted `buttons` JSON. Follow the workspace-fixture patterns already in `tests/test_spambot_conversation.py`.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_spambot_buttons.py tests/test_spambot_conversation.py -x</automated>
  </verify>
  <done>New tests pass; `git diff app/services/listener.py` shows `_handle_antispam_signal` unchanged; migration 063 present and idempotent; buttons persist for keyboard messages and stay NULL for plain text.</done>
</task>

<task type="auto">
  <name>Task 2: Frontend — render buttons + click mutation in SpambotChatPanel</name>
  <files>frontend/src/types/api.ts, frontend/src/components/SpambotChatPanel.tsx</files>
  <action>
1. **Type** frontend/src/types/api.ts — in the `MessageResponse` object (~4216, after `edited_at`), add `buttons?: ({ text: string })[][] | null;` matching the backend JSON shape (rows → cols → {text}).

2. **SpambotChatPanel.tsx**:
   - Add a click mutation: `useMutation` posting `POST /api/v1/conversations/${conversationId}/messages/${messageId}/click` with body `{ row, col }`. Track in-flight per message (e.g. `const [clicking, setClicking] = useState<string | null>(null)` keyed by message id, or use the mutation's `variables`/`isPending` with the target message id). On success: `qc.invalidateQueries({ queryKey: ["messages", conversationId] })` (same key the send mutation uses). On error: `toast.error(e instanceof ApiError ? e.message : "Не удалось нажать кнопку")`.
   - In the message map (~112-126), when `m.buttons` is a non-empty array, render a button grid BELOW the bubble: outer array = rows (`flex flex-col gap-1`), inner array = cols (`flex gap-1`), each a small secondary shadcn-style button (reuse the existing inline button classes in this file — `rounded-md border px-2 py-1 text-xs` secondary look) with label `b.text`, `disabled` while that message's click is in-flight, `onClick` → `clickMut.mutate({ messageId: m.id, row, col })`. Show a small `Loader2` spinner on the in-flight message's buttons.
   - Leave clicked buttons visible/clickable afterward (per CONTEXT discretion — no "used" state).
  </action>
  <verify>
    <automated>cd frontend && bunx tsc --noEmit 2>&1 | grep -E "SpambotChatPanel|types/api" || echo "no new type errors in touched files"</automated>
  </verify>
  <done>`tsc --noEmit` reports zero NEW errors in SpambotChatPanel.tsx / types/api.ts (the 3 pre-existing /login route errors in __root.tsx/_authenticated.tsx/settings.tsx are out of scope); buttons render as a row/col grid, disable in-flight, invalidate the messages query on success, toast.error on failure.</done>
</task>

</tasks>

<verification>
- Backend targeted suite green via the test-overlay (Task 1 verify).
- `git diff` on listener.py touches only `_persist_spambot_message` (+ maybe an `import json`), NOT `_handle_antispam_signal`.
- Migration 063 is idempotent (`ADD COLUMN IF NOT EXISTS`) and has a matching conftest `_mig_063` block.
- Frontend touched files compile with no new tsc errors.
- Queue rate limits / working hours / FloodWait retry untouched.
</verification>

<success_criteria>
- Inbound @SpamBot keyboard messages persist `messages.buttons` (2D `{text}` array); plain text stays NULL.
- `GET /conversations/{id}/messages` returns `buttons`.
- `POST /conversations/{id}/messages/{message_id}/click {row,col}` clicks on the sender's own session and returns success; SpamBot's reply arrives via the unchanged listener path.
- Panel renders clickable buttons, disables them in-flight, invalidates messages on success, toasts on error.
- No changes to restriction/antispam parsing or empirical queue constants.
</success_criteria>

<output>
After completion, create `.planning/quick/260713-jmp-spambot-buttons-click-support/260713-jmp-SUMMARY.md`
</output>
