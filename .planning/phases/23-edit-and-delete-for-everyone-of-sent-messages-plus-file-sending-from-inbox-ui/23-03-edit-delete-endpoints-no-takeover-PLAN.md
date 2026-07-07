---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
plan: 03
type: execute
wave: 2
depends_on: ["23-01", "23-02"]
files_modified:
  - app/routers/conversations.py
autonomous: true
requirements: [INBM-01, INBM-02, INBM-06, INBM-07]
must_haves:
  truths:
    - "Manager edits an outbound TEXT message → Telegram edit succeeds → message_text updated + edited_at set; conversation status/ai_enabled/queue UNCHANGED (no takeover, D-08)"
    - "Manager deletes an outbound message → revoke succeeds → the messages row is hard-DELETEd; list preview recomputes from the next message; no takeover (D-04)"
    - "Editing/deleting an INBOUND or contact message → 404 (D-01/D-05, outbound + sent_by IN ('ai','human') gate)"
    - "Cross-workspace or wrong-conversation message_id → 404, Telethon never called (D-19 silent isolation)"
    - "GET /messages now returns message_type/file_name/mime_type/size_bytes/edited_at for every row"
  artifacts:
    - path: "app/routers/conversations.py"
      provides: "_raise_inbox_message_error helper + _load_message_for_mutation gate + PATCH edit endpoint + DELETE revoke endpoint + widened GET /messages SELECT"
      contains: "PATCH"
  key_links:
    - from: "PATCH/DELETE endpoints"
      to: "telegram_service.edit_message_by_telegram_id / delete_message_by_telegram_id"
      via: "Telethon op FIRST, then UPDATE/DELETE messages row (inverted ordering, no takeover)"
      pattern: "edit_message_by_telegram_id|delete_message_by_telegram_id"
    - from: "message-id gate"
      to: "messages JOIN conversations JOIN senders"
      via: "WHERE m.id=:mid AND m.conversation_id=:cid AND c.workspace_id=:wid AND m.direction='outbound'"
      pattern: "direction = 'outbound'"
---

<objective>
Add the two "edit the past, no takeover" inbox endpoints to `app/routers/conversations.py`:
`PATCH /{id}/messages/{message_id}` (edit outbound text) and
`DELETE /{id}/messages/{message_id}` (delete-for-everyone / revoke + hard-delete row).
Add a Phase-23 Telethon-error mapping helper, a shared message-id+workspace gate, and widen
the `GET /{id}/messages` SELECT to return the new media/edited columns.

Purpose: These two operations are the behavioural counterweight to send-file — they mutate
PAST messages and MUST NOT auto-takeover (D-04/D-08). They invert the send ordering: Telethon
op FIRST, then the DB write. This plan owns conversations.py in Wave 2; the send-file/download
endpoints (23-05) touch the same file and therefore run after this in Wave 3.
Output: 2 endpoints + 1 error helper + 1 gate helper + widened SELECT, all in conversations.py.
Addresses: INBM-01 (delete), INBM-02 (edit), INBM-06 (error codes), INBM-07 (REST+workspace gate).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-CONTEXT.md
@.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-RESEARCH.md
@app/routers/conversations.py

<interfaces>
<!-- Existing workspace gate (conversations.py:67-85) -->
async def _load_conversation_or_404(db, ctx, conversation_id) -> dict:  # cross-ws → 404

<!-- Existing GET /messages SELECT to WIDEN (conversations.py:257-266) -->
SELECT m.id, m.conversation_id, m.direction, m.message_text,
       m.sent_by, m.telegram_message_id, m.created_at
FROM messages m JOIN conversations c ON c.id = m.conversation_id
WHERE c.id = :cid AND c.workspace_id = :wid ORDER BY m.created_at ASC LIMIT :limit OFFSET :offset

<!-- Service methods from plan 23-02 (return structured dicts) -->
telegram_service.edit_message_by_telegram_id(sender_slug, sender_id, encrypted_session,
    telegram_id, telegram_message_id, new_text, proxy=None, fingerprint=None) -> dict
telegram_service.delete_message_by_telegram_id(sender_slug, sender_id, encrypted_session,
    telegram_id, telegram_message_id, proxy=None, fingerprint=None) -> dict

<!-- Error-mapping template to clone (senders.py:315-380 _raise_profile_telegram_error) -->
blob = f"{type(e).__name__} {e}".upper(); for needle,code,msg in table: if needle in blob: raise HTTPException(...)

<!-- Schemas from plan 23-01 -->
EditMessageRequest(message: str)  # AliasChoices("message","message_text","text")
MessageResponse(... message_type, file_name, mime_type, size_bytes, edited_at ...)
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: _raise_inbox_message_error helper + _load_message_for_mutation gate + widened GET /messages SELECT</name>
  <read_first>
    - app/routers/conversations.py (_load_conversation_or_404:67-85; GET /messages:235-277; send handler:393-503 for the SELECT/gate style)
    - app/routers/senders.py (_raise_profile_telegram_error:315-380 — the mapping template)
    - .planning/phases/23-.../23-CONTEXT.md (D-17 error codes; D-19 workspace/message-id gate)
  </read_first>
  <action>
    In `app/routers/conversations.py`:

    (a) Add a module-level `_raise_inbox_message_error(e_or_dict)` helper that accepts the
    structured dict returned by the service methods (`{"success": False, "error": {"code": ..., "message": ...}}`)
    and raises an `HTTPException` with `detail={"code", "message"}` and an HTTP status derived
    from the code. Map codes → status:
      - `MESSAGE_EDIT_TOO_OLD` → 409
      - `MESSAGE_NOT_EDITABLE` → 422
      - `DELETE_FAILED` → 502
      - `FILE_TOO_LARGE` → 413
      - `NO_TELEGRAM_ID` → 400
      - `RECIPIENT_NOT_IN_TELEGRAM` → 422
      - `FLOOD_WAIT` → 429 (pass through `retry_after` if present)
      - `ACCOUNT_FROZEN` → 409
      - `USER_IS_BLOCKED` → 409
      - `MEDIA_UNAVAILABLE` → 410
      - `DOWNLOAD_FAILED` → 502
      - unknown → 502 `{"code": "TELEGRAM_OP_FAILED"}`.

    (b) Add an async gate `_load_message_for_mutation(db, ctx, conversation_id, message_id,
    *, require_type_text=False)` returning the joined row or raising 404. Single SELECT:
    ```sql
    SELECT m.id AS message_id, m.telegram_message_id, m.direction, m.sent_by, m.message_type,
           c.contact_telegram_id, c.contact_phone,
           s.id AS sender_id, s.slug AS sender_slug, s.session_string, s.proxy, s.client_fingerprint
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    JOIN senders s ON s.id = c.sender_id
    WHERE m.id = :mid AND m.conversation_id = :cid AND c.workspace_id = :wid
      AND m.direction = 'outbound' AND m.sent_by IN ('ai','human')
    ```
    If `require_type_text` also require `AND m.message_type = 'text'` (edit is text-only, D-05).
    0 rows → `HTTPException(404, {"code": "MESSAGE_NOT_FOUND", "message": "Message not found"})`.
    Cross-workspace, inbound, contact-sent, wrong-conversation, or (for edit) non-text all
    collapse to the same opaque 404 (D-19 silent isolation).

    (c) Widen the `GET /{id}/messages` SELECT (conversations.py:257-266) to also select
    `m.message_type, m.file_name, m.mime_type, m.size_bytes, m.edited_at` so
    `MessageResponse(**dict(r._mapping))` populates the new fields. Also widen the
    single-message endpoints if any expose message rows (leave list/detail conversation
    previews untouched — the LATERAL preview already recomputes correctly, D-03).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase23_inbox_mutations.py -k messages_select tests/test_phase5_inbox_send_takeover.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `app/routers/conversations.py` contains `def _raise_inbox_message_error` and `async def _load_message_for_mutation`.
    - The gate SELECT contains `m.direction = 'outbound'` and `m.sent_by IN ('ai','human')` and `c.workspace_id = :wid`.
    - `require_type_text` branch adds `m.message_type = 'text'`.
    - GET /messages SELECT now contains `m.message_type` and `m.edited_at`.
    - `_raise_inbox_message_error` maps `MESSAGE_EDIT_TOO_OLD`→409 and `MESSAGE_NOT_EDITABLE`→422.
    - Existing phase-5 inbox send tests still pass (no regression).
  </acceptance_criteria>
  <done>Error helper + workspace/message-id gate (outbound + sent_by + optional text) + widened GET /messages SELECT in place; phase-5 tests green.</done>
</task>

<task type="auto">
  <name>Task 2: PATCH /{id}/messages/{message_id} — edit outbound text (no takeover)</name>
  <read_first>
    - app/routers/conversations.py (send handler:393-503 for gate/commit shape; the helpers added in Task 1)
    - .planning/phases/23-.../23-RESEARCH.md (Pattern 2 edit/delete INVERTED ordering; Pitfall 1)
    - app/schemas/__init__.py (EditMessageRequest from plan 23-01)
  </read_first>
  <action>
    Add to `app/routers/conversations.py`:
    ```python
    @router.patch("/{conversation_id}/messages/{message_id}", response_model=MessageResponse)
    async def edit_message(conversation_id: UUID, message_id: UUID, payload: EditMessageRequest,
                           ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    ```
    Ordering (INVERTED vs send — D-08 NO takeover):
      1. `row = await _load_message_for_mutation(db, ctx, conversation_id, message_id, require_type_text=True)` (404 if not an editable outbound text msg).
      2. Telethon op OUTSIDE any txn:
         `result = await telegram_service.edit_message_by_telegram_id(sender_slug=row.sender_slug, sender_id=str(row.sender_id), encrypted_session=row.session_string, telegram_id=row.contact_telegram_id, telegram_message_id=row.telegram_message_id, new_text=payload.message, proxy=row.proxy, fingerprint=row.client_fingerprint)`.
      3. `if not result.get("success"): _raise_inbox_message_error(result)`.
      4. On success (incl. `no_op`): `UPDATE messages SET message_text=:txt, edited_at=NOW() WHERE id=:mid` then `await db.commit()`.
      5. Return the updated row (re-SELECT it as a `MessageResponse`, or build from the known values + edited_at).
    DO NOT touch conversations.status / ai_enabled / paused_reason / message_queue — this is
    a past-edit, not a takeover.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase23_inbox_mutations.py -k edit -x</automated>
  </verify>
  <acceptance_criteria>
    - `conversations.py` contains a `@router.patch("/{conversation_id}/messages/{message_id}"` route.
    - Handler calls `telegram_service.edit_message_by_telegram_id` BEFORE any UPDATE.
    - On success it runs `UPDATE messages SET message_text` and sets `edited_at = NOW()`.
    - Handler contains NO `UPDATE conversations` and NO `message_queue` write.
    - Editing an inbound/contact/non-text message → 404; cross-ws → 404 (Telethon not called); `MESSAGE_EDIT_TOO_OLD` → 409; `MessageNotModifiedError` path → success.
    - `-k edit` tests green.
  </acceptance_criteria>
  <done>Edit endpoint: Telethon-first then message_text+edited_at UPDATE, no takeover, text-only gate, error codes mapped; edit tests green.</done>
</task>

<task type="auto">
  <name>Task 3: DELETE /{id}/messages/{message_id} — delete-for-everyone (revoke + hard delete, no takeover)</name>
  <read_first>
    - app/routers/conversations.py (the helpers from Task 1; delete_conversation:538-545 for the 204 style)
    - .planning/phases/23-.../23-RESEARCH.md (Pattern 2 inverted ordering; Pitfall 4 delete silent no-op → DB row is source of truth)
    - .planning/phases/23-.../23-CONTEXT.md (D-01 outbound-only; D-03 hard delete; D-04 no takeover)
  </read_first>
  <action>
    Add to `app/routers/conversations.py`:
    ```python
    @router.delete("/{conversation_id}/messages/{message_id}", status_code=204)
    async def delete_message(conversation_id: UUID, message_id: UUID,
                             ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    ```
    Ordering (INVERTED — D-04 NO takeover):
      1. `row = await _load_message_for_mutation(db, ctx, conversation_id, message_id)` (no text-type requirement — any outbound message is deletable; 404 otherwise).
      2. Telethon op OUTSIDE any txn:
         `result = await telegram_service.delete_message_by_telegram_id(sender_slug=row.sender_slug, sender_id=str(row.sender_id), encrypted_session=row.session_string, telegram_id=row.contact_telegram_id, telegram_message_id=row.telegram_message_id, proxy=row.proxy, fingerprint=row.client_fingerprint)`.
      3. `if not result.get("success"): _raise_inbox_message_error(result)` (DELETE_FAILED only for connection/flood/frozen — a stale/own-message revoke is a silent success per Pitfall 4).
      4. On success: `DELETE FROM messages WHERE id=:mid` then `await db.commit()`.
      5. Return 204 (no body). The list-preview LATERAL subquery auto-recomputes last_message (D-03).
    DO NOT touch conversations.status / ai_enabled / message_queue.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase23_inbox_mutations.py -k delete -x</automated>
  </verify>
  <acceptance_criteria>
    - `conversations.py` contains a `@router.delete("/{conversation_id}/messages/{message_id}"` route with `status_code=204`.
    - Handler calls `telegram_service.delete_message_by_telegram_id` BEFORE the DB delete.
    - On success it runs `DELETE FROM messages WHERE id`.
    - Handler contains NO `UPDATE conversations` and NO `message_queue` write.
    - Deleting inbound → 404; cross-ws → 404 (Telethon not called); connection error → 502 `DELETE_FAILED`.
    - `-k delete` tests green (row gone, status/ai_enabled unchanged).
  </acceptance_criteria>
  <done>Delete endpoint: revoke-first then hard DELETE row, no takeover, outbound-only gate, DELETE_FAILED reserved for real failures; delete tests green.</done>
</task>

</tasks>

<verification>
- `pytest tests/test_phase23_inbox_mutations.py -k "edit or delete" -x` → green.
- `pytest tests/test_phase5_inbox_send_takeover.py` → still green (no regression on send/takeover).
- grep confirms neither PATCH nor DELETE handler writes `conversations` status/ai_enabled or `message_queue`.
</verification>

<success_criteria>
- Edit + delete endpoints registered, workspace + message-id gated (404 opaque isolation).
- Both invert send ordering (Telethon first, then DB), and NEITHER takes over the dialog.
- Edit is text-only + sets edited_at; delete hard-removes the row; errors mapped to D-17 codes.
</success_criteria>

<output>
After completion, create `.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-03-SUMMARY.md`.
</output>
