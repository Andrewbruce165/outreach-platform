---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
plan: 04
type: execute
wave: 2
depends_on: ["23-01"]
files_modified:
  - app/services/listener.py
autonomous: true
requirements: [INBM-04]
must_haves:
  truths:
    - "An incoming photo/video/voice/document from the contact writes a messages row with the concrete message_type + file_name/mime_type/size_bytes, WITHOUT downloading the bytes (D-15)"
    - "Voice messages are STILL transcribed and fed to the AI answerer (message_type='voice' AND transcription kept in message_text) — no regression"
    - "The AI-dispatch path for photo/video/document is unchanged (label still built), only the persisted row is enriched"
    - "Duplicate telegram_message_id is idempotent (ON CONFLICT DO NOTHING via the existing unique constraint)"
  artifacts:
    - path: "app/services/listener.py"
      provides: "save_message() accepts message_type + file_name/mime_type/size_bytes; incoming media branch classifies type + extracts pre-download metadata"
      contains: "message_type"
  key_links:
    - from: "handle_incoming_message media branch"
      to: "save_message(...)"
      via: "message.file.name/.mime_type/.size read pre-download, passed as new params"
      pattern: "message_type"
---

<objective>
Teach the listener to persist incoming media (from the contact) as a real file bubble:
extend `save_message()` to accept `message_type` + media metadata, and extend the incoming
media branch of `handle_incoming_message` to classify the concrete type
(photo/video/voice/document) and read `message.file.{name,mime_type,size}` PRE-download —
without fetching the bytes (D-15/D-16 lazy download comes later via the endpoint).

Purpose: Turn the current text-label-only storage (`[📎 Документ: file.pdf]`) into a typed,
metadata-bearing row that the inbox UI renders as a file bubble and the download endpoint
(23-05) can lazily fetch. Must NOT regress voice→AI transcription or the photo/video/document
AI-dispatch label. This file (listener.py) is untouched by the endpoint plans, so it runs in
Wave 2 parallel with 23-03. Deploy touches the listener container.
Output: enriched save_message + media branch in listener.py.
Addresses: INBM-04 (D-15).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-CONTEXT.md
@.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-RESEARCH.md
@app/services/listener.py

<interfaces>
<!-- Current save_message (listener.py:531-572) — INSERT writes only 5 columns -->
async def save_message(self, conversation_id, direction, message_text, sent_by, telegram_message_id) -> bool:
    INSERT INTO messages (conversation_id, direction, message_text, sent_by, telegram_message_id)
    VALUES (:conv_id, :direction, :msg_text, :sent_by, :msg_id)
    # IntegrityError on messages_conversation_telegram_unique → return False (duplicate)

<!-- Current media branch (listener.py:859-916) already classifies for the LABEL -->
elif event.message.photo or event.message.video or event.message.document:
    if event.message.photo:   file_name=f"photo_{event.id}.jpg"; file_type="image/jpeg"; emoji="📷"; media_type="Фото"
    elif event.message.video: file_name=...; file_type=video.mime_type or "video/mp4"; emoji="🎥"
    else: doc=event.message.document; file_name from attrs; file_type=doc.mime_type; emoji="📎"
    document_info = f"[{emoji} {media_type}: {file_name}]"
    caption = event.message.message or ""
    message_text = f"{document_info}\n{caption}".strip() if caption else document_info

<!-- Voice branch (listener.py:810-844) — transcribes to message_text, fed to AI -->
voice_media = event.message.voice
message_text = f"[🎤 Голосовое]: {transcribed_text}"   # is_voice=True; must remain AI-dispatched

<!-- Telethon File wrapper (research §Don't Hand-Roll): message.file.name / .mime_type / .size
     available WITHOUT downloading. NEVER read message.file.id (deprecated). -->

<!-- message_type value set (locked by planner in 23-01): text | photo | video | voice | document -->
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Extend save_message() with message_type + media metadata params</name>
  <read_first>
    - app/services/listener.py (save_message:531-572 — the shared INSERT + IntegrityError dedup)
    - migrations/053_phase23_messages_media.sql (new columns — from plan 23-01)
  </read_first>
  <action>
    In `app/services/listener.py`, change `save_message` signature to accept the new
    optional media params (default None so all existing text call-sites keep working — the
    DB `DEFAULT 'text'` fills message_type when not passed):
    ```python
    async def save_message(self, conversation_id, direction, message_text, sent_by,
                           telegram_message_id, message_type: str = "text",
                           file_name: str | None = None, mime_type: str | None = None,
                           size_bytes: int | None = None) -> bool:
    ```
    Update the INSERT to include the new columns:
    ```sql
    INSERT INTO messages (conversation_id, direction, message_text, sent_by,
                          telegram_message_id, message_type, file_name, mime_type, size_bytes)
    VALUES (:conv_id, :direction, :msg_text, :sent_by, :msg_id,
            :message_type, :file_name, :mime_type, :size_bytes)
    ```
    Keep the existing `IntegrityError` → `return False` duplicate handling (the
    `messages_conversation_telegram_unique` constraint still guards idempotency). Do NOT
    change any existing call-site's positional args — the new params are keyword-optional.
    `message_text` may now be None (column relaxed in 053) — the INSERT already binds it.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase23_inbox_mutations.py -k incoming_media -x</automated>
  </verify>
  <acceptance_criteria>
    - `save_message` signature includes `message_type: str = "text"`, `file_name`, `mime_type`, `size_bytes` (all keyword-optional).
    - The INSERT statement lists `message_type, file_name, mime_type, size_bytes`.
    - Existing text call-sites (text/voice/outgoing) are unchanged (still valid — new params default).
    - IntegrityError dedup (`return False`) preserved.
  </acceptance_criteria>
  <done>save_message accepts + persists message_type/file_name/mime_type/size_bytes with safe defaults; dedup preserved.</done>
</task>

<task type="auto">
  <name>Task 2: Classify incoming media type + extract pre-download metadata in handle_incoming_message</name>
  <read_first>
    - app/services/listener.py (voice branch:810-844; media branch:859-916; save_message call site + AI-dispatch:960-987)
    - .planning/phases/23-.../23-RESEARCH.md (Pitfall 5 — preserve voice→AI + label; Code Example 4 classification)
  </read_first>
  <action>
    In `handle_incoming_message` (`app/services/listener.py`):

    (a) Compute a concrete `message_type` and pull metadata from `event.message.file`
    (available WITHOUT download). In the media branch (859-916) and voice branch (810-844):
    ```python
    m = event.message
    if m.photo:      _mtype = "photo"
    elif m.video:    _mtype = "video"
    elif m.voice:    _mtype = "voice"
    elif m.document: _mtype = "document"
    else:            _mtype = "text"
    _f = m.file  # telethon File wrapper; None for plain text
    _file_name = (_f.name if _f else None)
    _mime_type = (_f.mime_type if _f else None)
    _size_bytes = (_f.size if _f else None)
    ```
    NEVER read `_f.id` (deprecated/unreliable — Pitfall 6). Do NOT call `download_media` for
    photo/video/document here (D-15 — bytes are lazy). (Voice already downloads a temp file
    for transcription — that path is unchanged; you only ADD the type/metadata capture.)

    (b) Thread these into EVERY `save_message(...)` call for incoming messages — pass
    `message_type=_mtype, file_name=_file_name, mime_type=_mime_type, size_bytes=_size_bytes`.
    For plain text, `_mtype='text'` and metadata None (identical to today).

    (c) PRESERVE behaviour:
      - Voice: keep building `message_text = "[🎤 Голосовое]: <transcript>"` AND set
        `message_type='voice'`; keep `is_voice=True` so the AI-dispatch (line 976) still
        strips the label and feeds the transcript to the answerer. Do NOT early-return
        before the AI-dispatch block for voice.
      - Photo/video/document: keep the `document_info` label + `message_text` composition
        (label + caption) exactly as-is; only add the new metadata params to save_message.
      - The AI-dispatch check (`conv["ai_enabled"] and conv["status"]=="active"`) is untouched.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase23_inbox_mutations.py -k incoming_media -x</automated>
  </verify>
  <acceptance_criteria>
    - `handle_incoming_message` computes a `message_type` of `photo`/`video`/`voice`/`document`/`text` and reads `.file.name`/`.file.mime_type`/`.file.size`.
    - `listener.py` does NOT reference `.file.id`.
    - Every incoming `save_message(` call passes `message_type=` (grep confirms).
    - The media branch does NOT call `download_media` for photo/video/document (only the pre-existing voice transcription download remains).
    - Voice path still sets `is_voice=True` and feeds the transcript to the AI (line ~976 unchanged).
    - `-k incoming_media` tests green (typed row + metadata; voice still transcribed; idempotent duplicate).
  </acceptance_criteria>
  <done>Incoming media classified + metadata captured pre-download and persisted; voice transcription + AI dispatch intact; no bytes fetched for photo/video/document; idempotent.</done>
</task>

</tasks>

<verification>
- `pytest tests/test_phase23_inbox_mutations.py -k incoming_media -x` → green.
- grep: no `.file.id` in listener.py; every incoming `save_message(` carries `message_type=`.
- Voice AI-dispatch line (~976) unchanged (transcript still reaches the answerer).
- Deploy note: rebuild BOTH `api` and `listener` containers (`docker compose up -d --build api listener`).
</verification>

<success_criteria>
- Incoming photo/video/voice/document persisted with concrete message_type + name/mime/size, no bytes downloaded.
- Voice remains transcribed + AI-dispatched; photo/video/document label unchanged.
- Duplicate telegram_message_id idempotent.
</success_criteria>

<output>
After completion, create `.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-04-SUMMARY.md`.
</output>
