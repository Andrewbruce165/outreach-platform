---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
plan: 02
type: execute
wave: 1
depends_on: []
files_modified:
  - app/services/telegram.py
autonomous: true
requirements: [INBM-01, INBM-02, INBM-03, INBM-05, INBM-06]
must_haves:
  truths:
    - "Four new mockable async methods exist on TelegramService, each returning a structured dict, each opening a client-per-op and ALWAYS disconnecting in finally"
    - "Peer is resolved by telegram_id via the proven cache→get_dialogs(200)→retry ladder, shared by all four methods"
    - "send_file uses force_document=False (auto-media) — photos arrive as photos, not documents (D-11)"
    - "delete passes revoke=True and never treats a stale/own-message no-op as an error"
    - "Telethon errors map to structured {code} dicts reusing the existing send_message mapping (FLOOD_WAIT/ACCOUNT_FROZEN/USER_IS_BLOCKED/RECIPIENT_NOT_IN_TELEGRAM)"
  artifacts:
    - path: "app/services/telegram.py"
      provides: "_resolve_peer_by_telegram_id helper + edit_message_by_telegram_id + delete_message_by_telegram_id + send_file_by_telegram_id + download_media_by_telegram_id"
      contains: "async def send_file_by_telegram_id"
  key_links:
    - from: "app/services/telegram.py new methods"
      to: "get_client / disconnect_client"
      via: "client-per-op skeleton cloned from send_message_by_telegram_id (fingerprint threaded through)"
      pattern: "disconnect_client"
    - from: "send_file_by_telegram_id"
      to: "client.send_file"
      via: "force_document=False + CAPTION_LIMIT overflow follow-up"
      pattern: "force_document=False"
---

<objective>
Add the four Telethon service methods this phase needs to `app/services/telegram.py`, each
cloning the proven `send_message_by_telegram_id` client-per-op skeleton and sharing one
peer-resolve helper: edit, delete-revoke, send-file (auto-media + caption overflow), and
lazy media download. Each method is a single mockable async method returning a structured
dict — the router logic (gates, ordering, DB writes) is fully testable against these mocks.

Purpose: Isolate all Telethon-facing code in one file so the endpoint plans (23-03, 23-05)
consume stable method signatures with no Telethon knowledge. This plan has NO DB/schema
dependency, so it runs in Wave 1 parallel with 23-01.
Output: 5 additions to telegram.py (1 helper + 4 methods).
Addresses: INBM-01 (delete), INBM-02 (edit), INBM-03 (send-file), INBM-05 (download), INBM-06 (error mapping).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-CONTEXT.md
@.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-RESEARCH.md
@CLAUDE.md

<interfaces>
<!-- Proven skeleton to clone — send_message_by_telegram_id (telegram.py:1082-1142) -->
async def send_message_by_telegram_id(self, sender_slug, sender_id, encrypted_session,
                                      telegram_id, message, proxy=None, fingerprint=None) -> dict:
    client = None
    try:
        client = await self.get_client(sender_slug, sender_id, encrypted_session, proxy=proxy, fingerprint=fingerprint)
        try:
            peer = await client.get_input_entity(telegram_id)
        except ValueError:
            await client.get_dialogs(limit=200)          # warms access_hash
            peer = await client.get_input_entity(telegram_id)
        sent = await client.send_message(peer, message)
        return {"success": True, "telegram_message_id": sent.id}
    except FloodWaitError as e:
        return {"success": False, "error": f"Rate limited. Retry after {e.seconds} seconds"}
    except Exception as e:
        logger.error(f"Error sending message by telegram_id: {e}")
        return {"success": False, "error": str(e)}
    finally:
        if client:
            await self.disconnect_client(client)

<!-- Existing send_message error-mapping block to COPY (telegram.py:838-902) -->
except FloodWaitError as e:        -> {"code":"FLOOD_WAIT","message":..., "retry_after": e.seconds}
except PeerFloodError:             -> (queue path) restriction; for inbox map to a structured code
except UserIsBlockedError:         -> {"code":"USER_IS_BLOCKED", ...}
except Exception as e:
    if is_frozen_error(e):         -> {"code":"ACCOUNT_FROZEN", ...}
    if "USER_IS_BLOCKED" in str(e):-> {"code":"USER_IS_BLOCKED", ...}
<!-- RECIPIENT_NOT_IN_TELEGRAM is already used (telegram.py:794) -->

<!-- Existing caption-overflow block to COPY (telegram.py:960-987) -->
CAPTION_LIMIT = 1024
file_caption = None; overflow_text = None
if caption:
    if len(caption) <= CAPTION_LIMIT: file_caption = caption
    else: overflow_text = caption
sent = await client.send_file(peer, tmp_path, caption=file_caption, file_name=file_name, force_document=True)  # inbox uses force_document=False
if overflow_text: await client.send_message(peer, overflow_text)

<!-- get_client signature (telegram.py:304) accepts proxy + fingerprint (Phase 21 IMPT-04) -->
async def get_client(self, sender_slug, sender_id, encrypted_session, proxy=None, fingerprint=None)
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Shared peer-resolve helper + edit_message_by_telegram_id + delete_message_by_telegram_id</name>
  <read_first>
    - app/services/telegram.py (send_message_by_telegram_id:1082-1142 skeleton; peer-resolve ladder:1110-1120; is_frozen_error:50; imports of FloodWaitError/PeerFloodError/UserIsBlockedError:23-28; get_client:304; disconnect_client:363)
    - .planning/phases/23-.../23-RESEARCH.md (Pitfall 1 edit-error classes; Pitfall 4 delete no-op; Code Example 1 + 2)
  </read_first>
  <action>
    Add to `TelegramService` in `app/services/telegram.py`, next to `send_message_by_telegram_id`:

    (a) Shared peer helper (extract the exact ladder):
    ```python
    async def _resolve_peer_by_telegram_id(self, client, telegram_id: int):
        """cache → get_dialogs(200) → retry (telegram.py:1110-1120 cold-cache fix)."""
        try:
            return await client.get_input_entity(telegram_id)
        except ValueError:
            await client.get_dialogs(limit=200)
            return await client.get_input_entity(telegram_id)
    ```

    (b) `edit_message_by_telegram_id(self, sender_slug, sender_id, encrypted_session,
        telegram_id, telegram_message_id, new_text, proxy=None, fingerprint=None) -> dict`
    — client-per-op; `await client.edit_message(peer, telegram_message_id, new_text)`;
    return `{"success": True}`. DO NOT pre-gate the edit window. Catch and map (import the
    error classes from `telethon.errors`):
      - `MessageNotModifiedError` → return `{"success": True, "no_op": True}` (idempotent;
        mirrors how set_username treats UsernameNotModifiedError).
      - `MessageEditTimeExpiredError` → `{"success": False, "error": {"code": "MESSAGE_EDIT_TOO_OLD", "message": "Сообщение слишком старое для редактирования"}}`.
      - `MessageAuthorRequiredError`, `MessageIdInvalidError` → `{"success": False, "error": {"code": "MESSAGE_NOT_EDITABLE", "message": "Это сообщение нельзя изменить"}}`.
      - `FloodWaitError` → `{"success": False, "error": {"code": "FLOOD_WAIT", "message": ..., "retry_after": e.seconds}}`.
      - generic `Exception` with `is_frozen_error(e)` → `ACCOUNT_FROZEN`; else `{"success": False, "error": {"code": "MESSAGE_NOT_EDITABLE", "message": str(e)}}` (defensive).
    ALWAYS `disconnect_client` in `finally`.

    (c) `delete_message_by_telegram_id(self, sender_slug, sender_id, encrypted_session,
        telegram_id, telegram_message_id, proxy=None, fingerprint=None) -> dict`
    — client-per-op; resolve peer; `await client.delete_messages(peer, [telegram_message_id], revoke=True)`.
    Deleting an already-gone/own message in a private chat is a SILENT no-op (never raises) —
    so treat the call reaching completion as success: return `{"success": True}`.
    Reserve failure for connection/flood/frozen/session errors:
      - `FloodWaitError` → `FLOOD_WAIT`; `is_frozen_error(e)` → `ACCOUNT_FROZEN`;
      - any other `Exception` → `{"success": False, "error": {"code": "DELETE_FAILED", "message": str(e)}}`.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api python -c "from app.services.telegram import telegram_service; assert hasattr(telegram_service,'edit_message_by_telegram_id') and hasattr(telegram_service,'delete_message_by_telegram_id') and hasattr(telegram_service,'_resolve_peer_by_telegram_id'); print('ok')"</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/telegram.py` contains `async def _resolve_peer_by_telegram_id`, `async def edit_message_by_telegram_id`, `async def delete_message_by_telegram_id`.
    - edit method references `MessageEditTimeExpiredError`, `MessageNotModifiedError`, `MessageAuthorRequiredError`, `MessageIdInvalidError` and maps to codes `MESSAGE_EDIT_TOO_OLD` / `MESSAGE_NOT_EDITABLE`.
    - delete method passes `revoke=True` (`grep "revoke=True"` matches) and returns success without requiring a Telethon exception.
    - Both methods contain `disconnect_client` inside a `finally`.
    - Verify command prints `ok`.
  </acceptance_criteria>
  <done>Shared peer helper + edit + delete methods exist; edit maps 4 error classes + NotModified→success; delete uses revoke=True + no-op-is-success; both disconnect in finally.</done>
</task>

<task type="auto">
  <name>Task 2: send_file_by_telegram_id (auto-media + caption overflow) + download_media_by_telegram_id</name>
  <read_first>
    - app/services/telegram.py (send_file:906-1080 — caption-overflow block:960-987 + error mapping:1001-1080; send_message error mapping:838-902; send_message_by_telegram_id skeleton)
    - .planning/phases/23-.../23-RESEARCH.md (Pitfall 3 send_file differences; Pitfall 6 download by message_id; Code Example 3 + 5)
  </read_first>
  <action>
    Add to `TelegramService` in `app/services/telegram.py`:

    (a) `send_file_by_telegram_id(self, sender_slug, sender_id, encrypted_session,
        telegram_id, tmp_path, file_name=None, caption=None, proxy=None, fingerprint=None) -> dict`
    — client-per-op; resolve peer via `_resolve_peer_by_telegram_id`.
    Copy the caption-overflow block VERBATIM from `send_file` (CAPTION_LIMIT=1024). Then:
    ```python
    sent = await client.send_file(peer, tmp_path, caption=file_caption,
                                  file_name=file_name, force_document=False)   # D-11 auto-media
    if overflow_text:
        try: await client.send_message(peer, overflow_text)
        except Exception as e: logger.warning(f"File sent but overflow text failed: {e}")
    return {"success": True, "telegram_message_id": sent.id}
    ```
    Error mapping — COPY the send_message mapping (telegram.py:838-902):
    `FloodWaitError`→FLOOD_WAIT(+retry_after); `PeerFloodError`→a structured code (reuse the
    existing send_message treatment); `UserIsBlockedError`→USER_IS_BLOCKED;
    `is_frozen_error(e)`→ACCOUNT_FROZEN; `"USER_IS_BLOCKED" in str(e)`→USER_IS_BLOCKED;
    unresolved-peer `ValueError` after the retry → `{"success": False, "error": {"code": "RECIPIENT_NOT_IN_TELEGRAM", ...}}`.
    Do NOT unlink tmp_path here — the router owns the temp file lifecycle (D-14). Do NOT
    download from a URL (this is the multipart/temp-file path, not the queue send_file).

    (b) `download_media_by_telegram_id(self, sender_slug, sender_id, encrypted_session,
        telegram_id, telegram_message_id, proxy=None, fingerprint=None) -> dict`
    — client-per-op; resolve peer:
    ```python
    msg = await client.get_messages(peer, ids=telegram_message_id)   # single Message | None
    if not msg or not msg.media:
        return {"success": False, "error": {"code": "MEDIA_UNAVAILABLE",
                "message": "Файл больше недоступен в Telegram"}}
    data = await msg.download_media(file=bytes)                      # in-memory bytes
    return {"success": True, "data": data,
            "mime": (msg.file.mime_type if msg.file else "application/octet-stream"),
            "name": (msg.file.name if msg.file else "file")}
    ```
    NEVER use `msg.file.id` (deprecated, unreliable for user accounts — Pitfall 6). On
    FloodWait/frozen/other exception return `{"success": False, "error": {"code": "DOWNLOAD_FAILED", "message": str(e)}}`.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api python -c "from app.services.telegram import telegram_service; assert hasattr(telegram_service,'send_file_by_telegram_id') and hasattr(telegram_service,'download_media_by_telegram_id'); print('ok')"</automated>
  </verify>
  <acceptance_criteria>
    - `app/services/telegram.py` contains `async def send_file_by_telegram_id` and `async def download_media_by_telegram_id`.
    - send_file method contains `force_document=False` and `CAPTION_LIMIT` overflow follow-up.
    - send_file method does NOT contain `httpx` download and does NOT `os.unlink(tmp_path)`.
    - download method contains `get_messages(` with `ids=`, `download_media(file=bytes)`, and returns code `MEDIA_UNAVAILABLE` when msg/media missing.
    - download method does NOT reference `.file.id`.
    - Both methods disconnect in `finally`. Verify command prints `ok`.
  </acceptance_criteria>
  <done>send_file (force_document=False + overflow + full error map, no URL, router owns temp) and download (get_messages ids→download_media(bytes), MEDIA_UNAVAILABLE, no file.id) exist and disconnect in finally.</done>
</task>

</tasks>

<verification>
- `python -c "from app.services.telegram import telegram_service"` with hasattr checks on all four methods + helper → `ok`.
- No new runtime dependency added (telethon 1.42.0 already pinned).
- `grep "force_document=False" app/services/telegram.py` matches (inbox auto-media).
</verification>

<success_criteria>
- Four methods + one shared helper added; each client-per-op with disconnect in finally.
- edit maps the exact Telethon error classes; delete uses revoke=True + no-op-is-success.
- send_file is auto-media + overflow; download is by (peer, message_id) not file.id.
- All error returns are structured `{success, error:{code,message}}` dicts.
</success_criteria>

<output>
After completion, create `.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-02-SUMMARY.md`.
</output>
