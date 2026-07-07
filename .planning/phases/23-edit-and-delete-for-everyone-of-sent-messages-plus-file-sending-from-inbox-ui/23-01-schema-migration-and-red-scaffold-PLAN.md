---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/053_phase23_messages_media.sql
  - tests/conftest.py
  - tests/test_phase23_inbox_mutations.py
  - app/schemas/__init__.py
autonomous: true
requirements: [INBM-08]
must_haves:
  truths:
    - "Prod (migration) and fresh/test DB (conftest applies 053) end up with identical messages schema — no UndefinedColumn in inbox integration tests, no NotNullViolation on raw INSERT that omits message_type"
    - "Existing messages rows keep working — message_type backfills to 'text', message_text stays populated (only its NOT NULL is relaxed)"
    - "A messages row can now be inserted with message_text=NULL (file bubble) and a non-text message_type"
    - "Wave-0 RED test file exists and collects (import-inside-body) so Wave-2 tasks turn it green"
  artifacts:
    - path: "migrations/053_phase23_messages_media.sql"
      provides: "messages gains message_type (NOT NULL DEFAULT 'text' + CHECK), file_name/mime_type/size_bytes (nullable), edited_at (nullable); message_text DROP NOT NULL"
      contains: "ADD COLUMN IF NOT EXISTS message_type"
    - path: "tests/conftest.py"
      provides: "exists-guarded apply of migration 053 in the hardcoded migration list"
      contains: "053_phase23_messages_media.sql"
    - path: "tests/test_phase23_inbox_mutations.py"
      provides: "RED scaffold for edit/delete/send-file/incoming-media/download/schema clusters (INBM-01..07)"
      min_lines: 80
    - path: "app/schemas/__init__.py"
      provides: "MessageResponse extended (message_text Optional + message_type/file_name/mime_type/size_bytes/edited_at) + EditMessageRequest + SendFileFromUIResponse"
      contains: "class EditMessageRequest"
  key_links:
    - from: "tests/conftest.py migration list"
      to: "migrations/053_phase23_messages_media.sql"
      via: "exists-guarded read_text().execute() (mirror the 045/046 blocks)"
      pattern: "053_phase23_messages_media"
    - from: "app/schemas/__init__.py MessageResponse"
      to: "migrations/053_phase23_messages_media.sql columns"
      via: "field names must match SELECT columns exactly (message_type, file_name, mime_type, size_bytes, edited_at)"
      pattern: "message_type|edited_at"
---

<objective>
Lay the schema + test foundation for Phase 23. Extend the raw-SQL `messages` table
(migration 053) with a `message_type`, media metadata, an `edited_at` marker, and relax
`message_text` NOT NULL — so file bubbles and edited-markers are representable. Teach the
test harness (`tests/conftest.py`) to apply 053, extend `MessageResponse` + add the new
request/response schemas, and drop the Wave-0 RED test scaffold for every capability in
the phase.

Purpose: This is the hard Wave-0 dependency. `messages` has **no ORM model** (raw-SQL only,
created by migration 017), so the migration DDL is the SOLE source of column defaults, and
`tests/conftest.py` applies a **hardcoded migration list (NOT a glob)** — without an explicit
053 apply, every downstream inbox test hits `UndefinedColumn`.
Output: migration 053, conftest 053-apply, extended schemas, RED test file.
Addresses: INBM-08 (D-20, D-21); RED scaffold for INBM-01..07.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-CONTEXT.md
@.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-RESEARCH.md
@.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-VALIDATION.md
@CLAUDE.md

<interfaces>
<!-- Current messages DDL (migrations/017_phase5.sql:19-31) — raw-SQL, NO ORM model -->
CREATE TABLE IF NOT EXISTS messages (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id          UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    conversation_id       UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction             VARCHAR(20) NOT NULL,    -- 'inbound' | 'outbound'
    message_text          TEXT NOT NULL,           -- <-- being relaxed to NULLABLE
    sent_by               VARCHAR(20) NOT NULL,    -- 'contact' | 'ai' | 'human'
    telegram_message_id   BIGINT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT messages_conversation_telegram_unique
        UNIQUE (conversation_id, telegram_message_id)
);

<!-- Current MessageResponse (app/schemas/__init__.py:1084) -->
class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    direction: str
    message_text: str                 # <-- becomes Optional[str]
    sent_by: str
    telegram_message_id: Optional[int] = None
    created_at: datetime

<!-- conftest exists-guard pattern to mirror (tests/conftest.py:230-239) -->
_mig_045 = PROJECT_ROOT / "migrations" / "045_follow_up.sql"
if _mig_045.exists():
    await asyncpg_conn.execute(_mig_045.read_text())
_mig_046 = PROJECT_ROOT / "migrations" / "046_telegram_service_status.sql"
if _mig_046.exists():
    await asyncpg_conn.execute(_mig_046.read_text())

<!-- test mock seam (tests/test_phase5_inbox_send_takeover.py:55-60) -->
send_mock = AsyncMock(return_value={"success": True, "telegram_message_id": 123})
monkeypatch.setattr("app.services.telegram.telegram_service.send_message_by_telegram_id", send_mock)
<!-- Factories available: test_sender_factory, test_conversation_factory, test_campaign_factory,
     async_client, valid_supabase_jwt, _bind. test_conversation_factory does NOT create messages
     rows — seed messages inline per-test via raw SQL. -->
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Migration 053 — extend messages table (idempotent)</name>
  <read_first>
    - migrations/017_phase5.sql (current messages DDL + unique constraint — lines 19-38)
    - migrations/052_sender_tg_premium.sql (latest committed migration — confirm 053 is next free slot)
    - CLAUDE.md (§Миграции + §Auto-applier — raw SQL, idempotent, fail-fast, server_default)
  </read_first>
  <action>
    Create `migrations/053_phase23_messages_media.sql`. Must be idempotent (auto-applier
    re-runs on any drift; fail-fast if it errors). Use EXACTLY these statements:

    ```sql
    -- Phase 23 (D-20/D-21): extend messages for edit/delete/file-send + incoming media.
    -- messages has NO ORM model (raw-SQL only, created by 017) — the DB DEFAULT below is the
    -- SOLE source of message_type's default. Do NOT add a Message ORM model (would re-introduce
    -- the ORM default= vs server_default= drift the codebase fought in migs 040/042).

    -- 1. message_type: NOT NULL DEFAULT 'text' backfills every existing row to 'text'.
    ALTER TABLE messages
        ADD COLUMN IF NOT EXISTS message_type VARCHAR(20) NOT NULL DEFAULT 'text';

    -- 2. CHECK constraint (idempotent via duplicate_object guard — ADD CONSTRAINT has no IF NOT EXISTS).
    --    Value set locked by planner (research OQ1): text | photo | video | voice | document.
    --    NO generic 'file' — the listener's voice branch maps to 'voice'.
    DO $$ BEGIN
        ALTER TABLE messages
            ADD CONSTRAINT messages_message_type_check
            CHECK (message_type IN ('text','photo','video','voice','document'));
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$;

    -- 3. Media metadata (nullable — only set for file/media bubbles).
    ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_name  VARCHAR(255);
    ALTER TABLE messages ADD COLUMN IF NOT EXISTS mime_type  VARCHAR(255);
    ALTER TABLE messages ADD COLUMN IF NOT EXISTS size_bytes BIGINT;

    -- 4. Edit marker (D-07). NULL = never edited.
    ALTER TABLE messages ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ;

    -- 5. Relax message_text NOT NULL for file bubbles without text (D-20).
    --    Idempotent: DROP NOT NULL on an already-nullable column is a harmless no-op.
    --    Safe: every existing INSERT path (send, listener, warmup) always writes text.
    ALTER TABLE messages ALTER COLUMN message_text DROP NOT NULL;
    ```

    NO `deleted_at` column (delete is hard-delete per D-03). Do NOT touch the unique
    constraint or indexes.
  </action>
  <verify>
    <automated>grep -q "ADD COLUMN IF NOT EXISTS message_type VARCHAR(20) NOT NULL DEFAULT 'text'" migrations/053_phase23_messages_media.sql && grep -q "message_text DROP NOT NULL" migrations/053_phase23_messages_media.sql && grep -Eq "CHECK \(message_type IN" migrations/053_phase23_messages_media.sql && ! grep -q deleted_at migrations/053_phase23_messages_media.sql && echo migration-053-structure-ok</automated>
  </verify>
  <acceptance_criteria>
    - File `migrations/053_phase23_messages_media.sql` exists.
    - Contains `ADD COLUMN IF NOT EXISTS message_type VARCHAR(20) NOT NULL DEFAULT 'text'`.
    - Contains `CHECK (message_type IN ('text','photo','video','voice','document'))` inside a `DO $$ ... EXCEPTION WHEN duplicate_object` block.
    - Contains `ALTER COLUMN message_text DROP NOT NULL`.
    - Contains `ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ`, `file_name`, `mime_type`, `size_bytes`.
    - Does NOT contain the string `deleted_at`.
    - Does NOT add a `Message` ORM class anywhere.
  </acceptance_criteria>
  <done>Migration 053 exists, idempotent, matches D-20/D-21; message_type value set = text|photo|video|voice|document; no deleted_at; no ORM model.</done>
</task>

<task type="auto">
  <name>Task 2: conftest 053-apply + Wave-0 RED test scaffold</name>
  <read_first>
    - tests/conftest.py (migration list lines 156-186 + exists-guard blocks 199-239 — mirror the 045/046 pattern; and factory defs test_sender_factory:513 / test_conversation_factory:911)
    - tests/test_phase5_inbox_send_takeover.py (mock seam + async_client usage — the template for these tests)
    - .planning/phases/23-.../23-VALIDATION.md (Phase capability → test map — the six clusters to stub)
  </read_first>
  <action>
    (a) In `tests/conftest.py`, immediately AFTER the `_mig_046` exists-guard block
    (~line 239), add an identical exists-guarded apply for 053:

    ```python
    # 053: Phase 23 messages media/edit extension. messages has NO ORM model, so
    # create_all never builds these columns — the hardcoded list does NOT glob
    # (RESEARCH Pitfall 2). Exists-guard keeps this green until migrations/053 lands.
    _mig_053 = PROJECT_ROOT / "migrations" / "053_phase23_messages_media.sql"
    if _mig_053.exists():
        await asyncpg_conn.execute(_mig_053.read_text())
    ```

    (b) Create `tests/test_phase23_inbox_mutations.py` with `pytestmark = pytest.mark.asyncio`
    and RED stubs covering the six clusters from 23-VALIDATION.md. Use import-inside-body /
    endpoint-call style so the file COLLECTS clean while the endpoints/service methods do not
    yet exist (tests fail RED, not collection-error). Include at minimum:
      - `test_schema_new_columns_present` — INSERT a `messages` row with `message_text=NULL`
        and `message_type='photo'` via raw SQL through `async_db_session`; assert it succeeds
        and that a row inserted WITHOUT message_type defaults to `'text'`. (Expected GREEN
        once BOTH migration 053 and this file exist — this task's verify runs it.)
      - `test_messages_select_includes_media_fields` — seed an outbound `messages` row with
        `message_type='photo'` + file_name/mime_type/size_bytes, GET
        `/conversations/{id}/messages`, assert response items carry `message_type='photo'` +
        the media fields (RED until 23-03 widens the GET /messages SELECT).
      - `test_save_message_persists_media_fields` — call the listener's `save_message(...,
        message_type='photo', file_name=..., mime_type=..., size_bytes=...)` and assert the
        row persists those columns (RED until 23-04 extends save_message).
      - `test_edit_*` (success updates message_text + edited_at; MESSAGE_EDIT_TOO_OLD;
        MessageNotModifiedError→success no-op; editing inbound→404; cross-ws→404)
      - `test_delete_*` (row DELETEd; revoke=True; no takeover; inbound→404; cross-ws→404)
      - `test_send_file_*` (takeover flips status='manual'/ai_enabled=false + pending queue
        failed; new messages row with message_type; >50MB→413 FILE_TOO_LARGE; no
        contact_telegram_id→400 NO_TELEGRAM_ID; inactive sender→404)
      - `test_incoming_media_*` (listener save_message writes message_type + name/mime/size;
        voice still transcribed; idempotent on duplicate telegram_message_id)
      - `test_download_*` (Response bytes + mime + Content-Disposition; mock returns None →
        404/410 MEDIA_UNAVAILABLE; cross-ws→404)
    Mock the FOUR new TelegramService methods at the boundary using the phase-5 seam with
    `raising=False` (methods land in plan 23-02):
    `monkeypatch.setattr("app.services.telegram.telegram_service.edit_message_by_telegram_id", AsyncMock(...), raising=False)`.
    Endpoint stubs may be marked `pytest.mark.xfail(reason="Wave 2")` OR left plain-RED —
    your discretion, but the file MUST collect with 0 errors.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase23_inbox_mutations.py --collect-only -q && docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase23_inbox_mutations.py -k schema -x</automated>
  </verify>
  <acceptance_criteria>
    - `tests/conftest.py` contains the string `053_phase23_messages_media.sql` inside an exists-guarded `_mig_053` block.
    - `tests/test_phase23_inbox_mutations.py` exists, contains `pytestmark = pytest.mark.asyncio`, and `--collect-only` exits 0 (no collection errors).
    - File contains test functions matching `test_schema`, `test_messages_select`, `test_save_message_persists`, `test_edit`, `test_delete`, `test_send_file`, `test_incoming_media`, `test_download`.
    - `monkeypatch.setattr` calls for the four new methods use `raising=False`.
    - `test_schema_new_columns_present` passes (validates migration 053 after Task 1).
  </acceptance_criteria>
  <done>conftest applies 053 (exists-guarded); RED scaffold collects clean; schema test green; edit/delete/send-file/incoming/download tests present and RED.</done>
</task>

<task type="auto">
  <name>Task 3: Extend MessageResponse + add EditMessageRequest / SendFileFromUIResponse schemas</name>
  <read_first>
    - app/schemas/__init__.py (MessageResponse:1084, SendMessageFromUIRequest:1101 with AliasChoices, SendMessageFromUIResponse:1118)
    - .planning/phases/23-.../23-CONTEXT.md (D-07 edited flag, D-13 caption, D-22 alias tolerance)
  </read_first>
  <action>
    In `app/schemas/__init__.py`:

    (a) Extend `MessageResponse` — make `message_text` optional and add the new columns
    (all optional/defaulted so the existing `GET /messages` SELECT, which does not yet
    return them, still constructs the model; plan 23-03 widens the SELECT):
    ```python
    class MessageResponse(BaseModel):
        model_config = ConfigDict(from_attributes=True)
        id: UUID
        conversation_id: UUID
        direction: str
        message_text: Optional[str] = None      # nullable for file bubbles (D-20)
        sent_by: str
        telegram_message_id: Optional[int] = None
        message_type: str = "text"              # text|photo|video|voice|document
        file_name: Optional[str] = None
        mime_type: Optional[str] = None
        size_bytes: Optional[int] = None
        edited_at: Optional[datetime] = None    # (изменено) marker (D-07)
        created_at: datetime
    ```

    (b) Add `EditMessageRequest` (D-06/D-22 — tolerate Lovable field aliases exactly like
    SendMessageFromUIRequest):
    ```python
    class EditMessageRequest(BaseModel):
        """PATCH /conversations/{id}/messages/{message_id} body (D-06/D-07)."""
        message: str = Field(
            ..., min_length=1, max_length=4096,
            validation_alias=AliasChoices("message", "message_text", "text"),
        )
    ```

    (c) Add `SendFileFromUIResponse` (mirror SendMessageFromUIResponse shape):
    ```python
    class SendFileFromUIResponse(BaseModel):
        """POST /conversations/{id}/send-file response (D-12)."""
        success: bool
        message_id: Optional[UUID] = None
        telegram_message_id: Optional[int] = None
        message_type: Optional[str] = None
        error: Optional[str] = None
    ```
    (The send-file endpoint uses multipart Form/File params, so no request BODY model is
    needed — caption + file arrive as form fields.)
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api python -c "from app.schemas import MessageResponse, EditMessageRequest, SendFileFromUIResponse; MessageResponse(id='00000000-0000-0000-0000-000000000000', conversation_id='00000000-0000-0000-0000-000000000000', direction='inbound', sent_by='contact', message_type='photo', created_at='2026-07-07T00:00:00Z'); print('ok')"</automated>
  </verify>
  <acceptance_criteria>
    - `app/schemas/__init__.py` contains `class EditMessageRequest` and `class SendFileFromUIResponse`.
    - `MessageResponse.message_text` is `Optional[str] = None`.
    - `MessageResponse` contains fields `message_type`, `file_name`, `mime_type`, `size_bytes`, `edited_at`.
    - `EditMessageRequest.message` uses `validation_alias=AliasChoices(...)` including `"message_text"`.
    - The verify command prints `ok` (MessageResponse constructs with message_text omitted).
  </acceptance_criteria>
  <done>MessageResponse carries media fields + edited_at with message_text optional; EditMessageRequest (alias-tolerant) and SendFileFromUIResponse exist and import cleanly.</done>
</task>

</tasks>

<verification>
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase23_inbox_mutations.py -k schema -x` → green (migration columns present, NULL text accepted, default 'text').
- `--collect-only` on the new test file → 0 errors.
- Schema import smoke prints `ok`.
</verification>

<success_criteria>
- Migration 053 idempotent, matches D-20/D-21, no deleted_at, no ORM model.
- conftest applies 053 via exists-guard (mirrors 045/046).
- MessageResponse + EditMessageRequest + SendFileFromUIResponse present.
- RED scaffold collects clean; downstream waves turn it green.
</success_criteria>

<output>
After completion, create `.planning/phases/23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui/23-01-SUMMARY.md`.
</output>
