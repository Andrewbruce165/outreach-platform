---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
plan: 05
type: execute
wave: 3
depends_on: ["23-03"]
files_modified:
  - app/routers/conversations.py
autonomous: true
requirements: [INBM-03, INBM-05, INBM-07]
must_haves:
  truths:
    - "Manager sends a file from inbox → conversation flips to takeover (status='manual', ai_enabled=false, paused_reason set) + pending queue for recipient_phone cancelled + a messages row recorded with the detected message_type (D-12)"
    - "A file >50 MB is rejected with 413 FILE_TOO_LARGE and Telethon is never called; the temp file is streamed with an early abort and always cleaned up (D-10/D-14)"
    - "Contact without contact_telegram_id → 400 NO_TELEGRAM_ID; inactive/dead sender → 404 (D-14 gates from /send)"
    - "Manager downloads an incoming file on demand → bytes streamed from Telegram with correct mime + Content-Disposition; deleted-on-Telegram → 410 MEDIA_UNAVAILABLE (D-16)"
    - "Bytes are never persisted to the DB; the temp upload file is unlinked in finally"
  artifacts:
    - path: "app/routers/conversations.py"
      provides: "_spool_upload_with_cap helper + POST /{id}/send-file (multipart, takeover) + GET /{id}/messages/{message_id}/file (lazy download)"
      contains: "send-file"
  key_links:
    - from: "POST /{id}/send-file"
      to: "telegram_service.send_file_by_telegram_id"
      via: "takeover UPDATE + queue-cancel + commit BEFORE Telethon (mirror POST /send), INSERT messages after success"
      pattern: "send_file_by_telegram_id"
    - from: "GET /{id}/messages/{message_id}/file"
      to: "telegram_service.download_media_by_telegram_id"
      via: "Response(content=data, media_type=mime, Content-Disposition) — mirror PROF-07 photo GET"
      pattern: "Content-Disposition"
---

<objective>
Add the two "new outbound / lazy fetch" inbox endpoints to `app/routers/conversations.py`:
`POST /{id}/send-file` (multipart upload → temp file → auto-media send, WITH auto-takeover)
and `GET /{id}/messages/{message_id}/file` (lazy on-demand download of an incoming file,
streamed straight from Telegram). Add a streaming 50 MB size-guard helper.

Purpose: Send-file is a NEW outbound → it MUST auto-takeover exactly like `POST /send`
(D-12), the behavioural opposite of edit/delete. Download is the lazy counterpart to the
listener's metadata-only incoming rows (D-16). Both touch conversations.py, so this plan is
sequenced AFTER 23-03 (Wave 3) to avoid a file conflict.
Output: 2 endpoints + 1 upload-cap helper in conversations.py.
Addresses: INBM-03 (send-file), INBM-05 (download), INBM-07 (REST + workspace gate).
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
<!-- POST /send takeover ordering to MIRROR (conversations.py:393-503) -->
# 1. SELECT c.contact_telegram_id, s.id AS sender_id, s.slug, s.session_string, s.proxy, s.client_fingerprint
#    FROM conversations c JOIN senders s ON c.sender_id=s.id
#    WHERE c.id=:cid AND c.workspace_id=:wid AND s.lifecycle_status='active' AND s.auth_status='ok'
#    → 404 if none; contact_telegram_id IS NULL → 400 NO_TELEGRAM_ID
# 2. UPDATE conversations SET ai_enabled=false, status='manual', paused_at=NOW(),
#    paused_reason='Manager sent file via UI', updated_at=NOW()
# 3. UPDATE message_queue SET status='failed', error_message='Conversation taken over manually',
#    finished_at=NOW() WHERE workspace_id=:wid AND recipient_phone=(SELECT contact_phone ...) AND status='pending'
# 4. await db.commit()
# 5. Telethon send OUTSIDE txn
# 6. INSERT INTO messages (... message_type ...) ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING

<!-- Service methods from plan 23-02 -->
telegram_service.send_file_by_telegram_id(sender_slug, sender_id, encrypted_session,
    telegram_id, tmp_path, file_name=None, caption=None, proxy=None, fingerprint=None) -> dict  # {"success","telegram_message_id"}
telegram_service.download_media_by_telegram_id(sender_slug, sender_id, encrypted_session,
    telegram_id, telegram_message_id, proxy=None, fingerprint=None) -> dict  # {"success","data","mime","name"} | {"error":{"code":"MEDIA_UNAVAILABLE"}}

<!-- Helpers from plan 23-03 (same file) -->
_raise_inbox_message_error(result)                       # maps codes → HTTPException
_load_message_for_mutation(db, ctx, cid, mid)            # but download needs an INBOUND gate — see Task 2

<!-- PROF-07 byte-serving pattern (senders.py:1300) -->
return Response(content=sender.tg_photo, media_type=sender.tg_photo_mime or "image/jpeg")

<!-- FastAPI multipart: from fastapi import UploadFile, File, Form ; python-multipart already installed -->
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: _spool_upload_with_cap helper + POST /{id}/send-file (multipart, auto-takeover)</name>
  <read_first>
    - app/routers/conversations.py (POST /send:393-503 — the takeover ordering to mirror EXACTLY; imports at top of file)
    - .planning/phases/23-.../23-RESEARCH.md (Code Example 6 streaming cap; Pitfall 7 Content-Length spoofable; Pattern 2 send-file takeover)
    - .planning/phases/23-.../23-CONTEXT.md (D-09..D-14 — multipart, 50MB, auto-media, takeover, gates)
  </read_first>
  <action>
    In `app/routers/conversations.py`:

    (a) Add module-level imports if missing: `import os, tempfile, asyncio` and
    `from fastapi import UploadFile, File, Form`.

    (b) Add the streaming size-guard helper (Code Example 6):
    ```python
    MAX_FILE_BYTES = 50 * 1024 * 1024   # ~50 MB (D-10)
    async def _spool_upload_with_cap(upload: UploadFile) -> tuple[str, int]:
        fd, tmp_path = tempfile.mkstemp()
        total = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise HTTPException(status_code=413,
                            detail={"code": "FILE_TOO_LARGE", "message": "Файл больше 50 МБ"})
                    await asyncio.to_thread(out.write, chunk)
            return tmp_path, total
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
    ```
    Do NOT trust `Content-Length`; do NOT `await file.read()` the whole upload into RAM.

    (c) Add the endpoint (mirror POST /send takeover ordering EXACTLY, D-12):
    ```python
    @router.post("/{conversation_id}/send-file", response_model=SendFileFromUIResponse)
    async def send_file_from_ui(conversation_id: UUID,
                                file: UploadFile = File(...),
                                caption: Optional[str] = Form(None),
                                ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    ```
    D-22 alias tolerance: `caption` is a BRAND-NEW multipart field with no legacy/Lovable
    naming precedent (unlike `message`/`message_text`), so NO Form alias is needed — this
    rationale is documented here to close the D-22 compliance check. If Lovable later ships a
    different field name, accept it via a second `Form(None)` alias param then.
    INFO: the persisted `message_type` (step 8) is a BEST-EFFORT label derived from the
    browser-supplied `file.content_type`; actual Telegram rendering is governed by
    `force_document=False` (Telethon auto-detect), so any label/render mismatch is cosmetic only.
    Ordering:
      1. Same load+gate SELECT as POST /send (workspace + `s.lifecycle_status='active'` +
         `s.auth_status='ok'`; also select `s.client_fingerprint`). 404 if none.
         `contact_telegram_id IS NULL` → 400 `NO_TELEGRAM_ID`.
      2. `tmp_path, size = await _spool_upload_with_cap(file)` (streams; 413 on overflow —
         BEFORE any takeover/Telethon so an oversize upload changes nothing).
      3. Auto-takeover UPDATE conversations (`ai_enabled=false, status='manual',
         paused_at=NOW(), paused_reason='Manager sent file via UI', updated_at=NOW()`).
      4. Cancel pending queue for the recipient_phone (identical to POST /send step 3).
      5. `await db.commit()`.
      6. Telethon OUTSIDE txn (in a try/finally that `os.unlink(tmp_path)`):
         `result = await telegram_service.send_file_by_telegram_id(sender_slug=..., sender_id=str(...), encrypted_session=..., telegram_id=..., tmp_path=tmp_path, file_name=file.filename, caption=caption, proxy=..., fingerprint=row.client_fingerprint)`.
      7. `if not result.get("success"): _raise_inbox_message_error(result)`.
      8. Detect message_type from the uploaded file mime (`file.content_type`): image/* →
         'photo', video/* → 'video', else 'document' (matches Telethon auto-media). INSERT a
         messages row: `INSERT INTO messages (id, workspace_id, conversation_id, direction,
         message_text, sent_by, telegram_message_id, message_type, file_name, mime_type, size_bytes)
         VALUES (:id, :wid, :cid, 'outbound', :cap, 'human', :tg_mid, :mtype, :fname, :mime, :size)
         ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING` (message_text = caption or NULL).
      9. `await db.commit()`; `os.unlink(tmp_path)` in finally. Return `SendFileFromUIResponse(success=True, message_id=..., telegram_message_id=..., message_type=mtype)`.
    Byte payload NEVER written to the DB (D-14).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase23_inbox_mutations.py -k send_file tests/test_phase5_inbox_send_takeover.py -x</automated>
  </verify>
  <acceptance_criteria>
    - `conversations.py` contains `@router.post("/{conversation_id}/send-file"` with `UploadFile = File(...)` and `caption: Optional[str] = Form(None)`.
    - Contains `_spool_upload_with_cap` with `MAX_FILE_BYTES = 50 * 1024 * 1024` and a 413 `FILE_TOO_LARGE` raise.
    - Action documents the D-22 rationale that `caption` needs no Form alias (brand-new field, no legacy naming).
    - Handler performs the takeover UPDATE (`status = 'manual'`, `ai_enabled = false`, `paused_reason = 'Manager sent file via UI'`) and the pending-queue cancel BEFORE the Telethon call.
    - Handler INSERTs a messages row carrying `message_type` and calls `send_file_by_telegram_id`.
    - `os.unlink(tmp_path)` present in a finally; no byte payload written to `messages`.
    - >50MB → 413 (Telethon not called); no contact_telegram_id → 400; inactive sender → 404; phase-5 send tests still green.
  </acceptance_criteria>
  <done>send-file endpoint: streamed 50MB guard → takeover (status/ai/queue) → Telethon auto-media → typed messages row → temp cleanup; gates + FILE_TOO_LARGE enforced; send-file tests green.</done>
</task>

<task type="auto">
  <name>Task 2: GET /{id}/messages/{message_id}/file — lazy on-demand download</name>
  <read_first>
    - app/routers/conversations.py (helpers from 23-03; GET /messages workspace gate:247-255)
    - app/routers/senders.py (serve_sender_photo:1282-1300 — Response(content=..., media_type=...) byte-serving)
    - .planning/phases/23-.../23-RESEARCH.md (Code Example 5; Pitfall 6 download by message_id; OQ3 disposition default attachment)
  </read_first>
  <action>
    In `app/routers/conversations.py` add:
    ```python
    @router.get("/{conversation_id}/messages/{message_id}/file")
    async def download_message_file(conversation_id: UUID, message_id: UUID,
                                    disposition: str = Query("attachment"),
                                    ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    ```
    Logic:
      1. Gate on the message belonging to this conversation+workspace and carrying media.
         Use a SELECT (do NOT reuse `_load_message_for_mutation`'s outbound-only gate — an
         incoming file is `direction='inbound'`). SELECT joining messages→conversations→senders:
         `WHERE m.id=:mid AND m.conversation_id=:cid AND c.workspace_id=:wid
          AND m.message_type IN ('photo','video','voice','document')`.
         Also select `m.telegram_message_id, m.file_name, m.mime_type, c.contact_telegram_id,
         s.slug, s.id, s.session_string, s.proxy, s.client_fingerprint`. 0 rows → 404
         `{"code": "MESSAGE_NOT_FOUND"}`. `telegram_message_id IS NULL` → 404 (nothing to fetch).
      2. `result = await telegram_service.download_media_by_telegram_id(sender_slug=..., sender_id=str(...), encrypted_session=..., telegram_id=row.contact_telegram_id, telegram_message_id=row.telegram_message_id, proxy=..., fingerprint=row.client_fingerprint)`.
      3. `if not result.get("success"): _raise_inbox_message_error(result)` (MEDIA_UNAVAILABLE → 410).
      4. Return bytes (mirror PROF-07):
         ```python
         name = row.file_name or result.get("name") or "file"
         mime = row.mime_type or result.get("mime") or "application/octet-stream"
         disp = "inline" if disposition == "inline" else "attachment"
         return Response(content=result["data"], media_type=mime,
                         headers={"Content-Disposition": f'{disp}; filename="{name}"'})
         ```
    NEVER persist the downloaded bytes (D-16). `?disposition=inline` optional (OQ3), default attachment.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase23_inbox_mutations.py -k download -x</automated>
  </verify>
  <acceptance_criteria>
    - `conversations.py` contains `@router.get("/{conversation_id}/messages/{message_id}/file"`.
    - Gate SELECT includes `c.workspace_id = :wid` and `m.message_type IN ('photo','video','voice','document')` (does NOT require outbound).
    - Handler calls `telegram_service.download_media_by_telegram_id` and returns a `Response(content=..., media_type=..., headers={"Content-Disposition"...})`.
    - MEDIA_UNAVAILABLE → 410; cross-ws / non-media / missing telegram_message_id → 404.
    - No `INSERT`/`UPDATE` of the downloaded bytes anywhere in the handler.
    - `?disposition=inline` toggles the Content-Disposition; default is `attachment`.
    - `-k download` tests green.
  </acceptance_criteria>
  <done>Lazy download endpoint: inbound-media gate → Telethon fetch → Response bytes with mime + Content-Disposition (attachment default, inline opt); 410 on gone media; bytes never persisted; download tests green.</done>
</task>

</tasks>

<verification>
- `pytest tests/test_phase23_inbox_mutations.py -k "send_file or download" -x` → green.
- `pytest tests/test_phase5_inbox_send_takeover.py` → green (send/takeover regression guard).
- `pytest tests/test_phase23_inbox_mutations.py tests/test_phase5_inbox*.py` (wave-merge sample) → green.
- grep: `_spool_upload_with_cap` present; `os.unlink` in send-file finally; download handler has no INSERT/UPDATE.
</verification>

<success_criteria>
- send-file: streamed 50MB cap, auto-takeover (status/ai_enabled/queue), auto-media, typed messages row, temp cleanup, bytes never in DB.
- download: workspace+media gated, streams bytes with correct headers, 410 on gone media, no persistence.
- All four Phase-23 endpoints now registered under /api/v1/conversations.
</success_criteria>

<output>
After completion, create `.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-05-SUMMARY.md`.
</output>
