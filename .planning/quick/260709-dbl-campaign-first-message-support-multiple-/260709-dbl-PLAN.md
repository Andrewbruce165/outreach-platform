---
phase: quick-260709-dbl
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/060_campaign_attachments_multiple.sql
  - app/models/__init__.py
  - app/routers/campaigns.py
  - app/schemas/__init__.py
  - app/services/telegram.py
  - app/services/queue.py
  - tests/test_campaign_attachment.py
  - tests/test_queue_file_opener.py
  - tests/test_send_file_blob.py
  - lovable-handoff/openapi.json
autonomous: true
requirements: [MULTIFILE-01]
must_haves:
  truths:
    - "A campaign can hold MORE THAN ONE first-message attachment (ordered)"
    - "Uploading files replaces the campaign's attachment set (upsert, replace-all)"
    - "An existing single-file campaign keeps working unchanged (backwards compatible)"
    - "The opener send delivers ALL attachments to the recipient as one grouped album, caption on the first"
    - "Original filenames are preserved for every attached file"
    - "Duplicating a campaign copies ALL its attachments, not just the first"
    - "Number of attachments is capped and over-cap uploads are rejected"
  artifacts:
    - path: "migrations/060_campaign_attachments_multiple.sql"
      provides: "Drop 1-1 UNIQUE(campaign_id) + add position column for ordering"
      contains: "campaign_attachments"
    - path: "app/services/telegram.py"
      provides: "Album send path (list of files) preserving per-file filenames"
      contains: "send_file"
    - path: "app/services/queue.py"
      provides: "Load ALL attachments by campaign_id ordered by position, send album when >1"
      contains: "campaign_attachments"
  key_links:
    - from: "app/routers/campaigns.py::upload_attachment"
      to: "campaign_attachments (N rows)"
      via: "delete-then-insert all uploaded files with position"
      pattern: "position"
    - from: "app/services/queue.py"
      to: "telegram_service.send_file (attachments list)"
      via: "load all attachment rows ORDER BY position"
      pattern: "ORDER BY position"
---

<objective>
Extend the campaign first-message attachment (Phase 24) from a single file to MULTIPLE files.
Today `campaign_attachments.campaign_id` is UNIQUE (exactly one blob per campaign) and the send
worker loads it with `.first()`. This plan makes the table 1-to-N (ordered), lets the upload
endpoint accept several files at once, and delivers them all as one grouped Telegram album on the
campaign opener — with original filenames preserved and full backwards compatibility for existing
single-file campaigns.

Purpose: users want to attach several files (e.g. a deck + price list + photo) to the opener.
Output: 1-N attachment model, multi-file upload API, album delivery, updated duplicate/tests/openapi.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
</execution_context>

<context>
@.planning/STATE.md
@./CLAUDE.md

<interfaces>
<!-- Current single-file model (Phase 24, migration 054). Executor extends these. -->

migrations/054_campaign_attachment_and_variation.sql — creates:
  campaign_attachments(
    id uuid PK default gen_random_uuid(),
    campaign_id uuid NOT NULL UNIQUE REFERENCES campaigns(id) ON DELETE CASCADE,  -- <-- 1-1 today
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    file_data bytea NOT NULL,
    file_name varchar(255) NOT NULL,
    content_type varchar(100),
    size_bytes bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
  )

app/models/__init__.py::CampaignAttachment — ORM mirror; campaign_id has unique=True.
  NOTE: create_all wins over migrations in init_db → any new column MUST also be added to the ORM
  with a server_default (same drift rule cited in the model docstring), else raw-SQL INSERTs that
  omit it hit NotNullViolation on the fresh-DB/recovery path.

app/routers/campaigns.py:
  - MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024  (per-file ceiling → 413 FILE_TOO_LARGE)
  - POST /{campaign_id}/attachment  (upload_attachment): single UploadFile via `file`|`attachment`
    alias, delete-then-insert ONE row, returns {campaign_id, file_name, size_bytes, content_type}
  - DELETE /{campaign_id}/attachment: deletes all rows for campaign → 204 (idempotent)
  - duplicate_campaign (~line 1039): copies attachment via `.first()` (ONE row) into the copy
  - _load_campaign(db, ctx, campaign_id) → workspace-scoped load (cross-ws → 404 CAMPAIGN_NOT_FOUND)

app/schemas/__init__.py (~line 964): CampaignResponse has `has_attachment: bool = False` (computed
  by router via EXISTS on campaign_attachments — NOT a column).

app/services/queue.py (~line 937-992): file-opener branch. Loads ONE attachment:
    "SELECT file_data, file_name, content_type, size_bytes FROM campaign_attachments WHERE campaign_id=:cid" .first()
  then telegram_service.send_file(file_bytes=att.file_data, file_name=att.file_name,
  caption=caption_to_send, force_document=False, ...). Also sets result["media"] for the Phase-23
  inbox media bubble from the single attachment's extension.

app/services/telegram.py::send_file(client, phone, recipient_name, file_url=None, file_name=None,
  caption=None, sender_id, workspace_id, file_bytes=None, force_document=True) -> dict
  - Single blob today: writes ONE NamedTemporaryFile, sends via client.send_file with an explicit
    DocumentAttributeFilename(file_name) (Telethon's random temp basename would otherwise lose the
    real filename — this is the commit 3859ce0 fix; do NOT regress it).
  - Caption >1024 chars → sent as a separate follow-up text message (D-07).
  - Error handling block: FloodWait / PeerFlood / frozen / UserIsBlocked / privacy — reuse it.

app/services/campaign_enqueue.py (~line 345): `has_attachment` EXISTS probe → item_type='file'.
  This EXISTS probe is already 1-N safe (just presence) — leave it unchanged.
</interfaces>
</context>

<constraints>
- DB migration: raw SQL, idempotent, next free number = **060** (max existing is 059). Use
  `ALTER TABLE ... DROP CONSTRAINT IF EXISTS`, `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`.
- Backwards compatibility is mandatory: existing single-attachment campaigns must send unchanged, and
  the single-file upload path (one `file`/`attachment` field) must keep returning 200 with one row.
- Do NOT touch rate-limit intervals or FloodWait/PeerFlood retry logic (CLAUDE.md guard).
- The single-file send path in telegram.py (DocumentAttributeFilename fix, commit 3859ce0) must stay
  byte-for-byte for the 1-file case. Route through the new album branch ONLY when >1 file.
- Cap attachments per campaign at MAX_ATTACHMENTS = 10; over-cap upload → 400 TOO_MANY_ATTACHMENTS.
  Per-file size ceiling MAX_ATTACHMENT_BYTES (50 MB) is unchanged and applies to each file.
- Tests run ONLY via test-overlay:
  `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`
  NEVER `docker compose run --rm api pytest` (conftest guard / prod DROP risk).
- Frontend (Lovable, separate repo) is OUT of scope — but document the contract change in
  lovable-handoff/openapi.json so the multi-file shape is explicit for regeneration.
</constraints>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Make campaign_attachments 1-to-N (migration 060 + ORM + duplicate-copy-all)</name>
  <files>migrations/060_campaign_attachments_multiple.sql, app/models/__init__.py, app/routers/campaigns.py, tests/test_campaign_attachment.py</files>
  <behavior>
    - A raw INSERT of a SECOND campaign_attachments row for the SAME campaign now SUCCEEDS
      (the UNIQUE(campaign_id) constraint is gone) — invert the existing
      test_attachment_campaign_id_unique into test_attachment_allows_multiple_rows.
    - A raw INSERT omitting `position` reads back position == 0 (server_default fired) — drift guard,
      same shape as the existing test_attachment_raw_insert_omitting_defaults.
    - migration 060 is idempotent: applying its DDL twice raises nothing (mirror the existing
      test_migration_054_idempotent, add test_migration_060_idempotent).
    - duplicate_campaign copies ALL attachment rows (not just the first): a source with 2 attachments
      → the copy has 2 rows with the same bytes/filenames/positions (extend
      test_duplicate_copies_flag_and_blob to upload 2 files and assert count==2 on the copy).
  </behavior>
  <action>
    1. Create migrations/060_campaign_attachments_multiple.sql (idempotent):
       - `ALTER TABLE campaign_attachments DROP CONSTRAINT IF EXISTS campaign_attachments_campaign_id_key;`
         (the auto-named UNIQUE constraint from the `campaign_id ... UNIQUE` column in mig 054).
       - `ALTER TABLE campaign_attachments ADD COLUMN IF NOT EXISTS position integer NOT NULL DEFAULT 0;`
       - `ALTER TABLE campaign_attachments ALTER COLUMN position SET DEFAULT 0;` (drift guard).
       - `CREATE INDEX IF NOT EXISTS idx_campaign_attachments_campaign_pos ON campaign_attachments(campaign_id, position);`
       - Header comment explaining Phase-24 1-1 → 1-N and CLAUDE.md idempotency rules.
    2. app/models/__init__.py::CampaignAttachment:
       - Remove `unique=True` from the `campaign_id` Column (fresh-DB/create_all path must not
         recreate the UNIQUE constraint the migration drops).
       - Add `position = Column(Integer, nullable=False, default=0, server_default=text("0"))`
         (Integer/text already imported in this module). Update the docstring to note 1-N ordering.
    3. app/routers/campaigns.py duplicate_campaign (~line 1039): replace the single `.first()` copy
       with a loop over ALL source rows ordered by position, inserting a fresh CampaignAttachment per
       row (carry file_data/file_name/content_type/size_bytes/position). Keep it in the SAME
       transaction as new_c (existing all-or-nothing behaviour).
    4. tests/test_campaign_attachment.py: rewrite test_attachment_campaign_id_unique →
       test_attachment_allows_multiple_rows (two inserts succeed, count==2); add
       test_migration_060_idempotent; extend test_attachment_raw_insert_omitting_defaults to assert
       position default; extend test_duplicate_copies_flag_and_blob to two files.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_attachment.py -q</automated>
  </verify>
  <done>Two attachments per campaign persist and survive duplicate; migration 060 idempotent; position server_default fires; single-file model tests still green.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Multi-file upload endpoint + attachment_count in response + openapi doc</name>
  <files>app/routers/campaigns.py, app/schemas/__init__.py, tests/test_campaign_attachment.py, lovable-handoff/openapi.json</files>
  <behavior>
    - POST /{campaign_id}/attachment with a `files` list of 3 → 3 rows, positions 0/1/2, response
      lists all 3 (count==3). (test_upload_multiple_files_stores_all)
    - POST with a SINGLE `file` field still works: 200, exactly 1 row (existing
      test_upload_attachment_stores_one_blob must stay green — do not break it).
    - A second POST REPLACES the whole set (delete-then-insert all): upload 2, then upload 1 →
      exactly 1 row remains. (extend test_upload_replaces_existing_blob or add
      test_upload_replaces_whole_set)
    - Over-cap: uploading MAX_ATTACHMENTS+1 files → 400 TOO_MANY_ATTACHMENTS, 0 rows written.
      (test_upload_too_many_attachments_400)
    - Any single file over MAX_ATTACHMENT_BYTES → 413 FILE_TOO_LARGE, 0 rows written (unchanged).
    - GET campaign → `attachment_count` reflects the number of attachments (0 → N).
  </behavior>
  <action>
    1. app/routers/campaigns.py: add `MAX_ATTACHMENTS = 10` next to MAX_ATTACHMENT_BYTES.
    2. Rework upload_attachment to accept multiple files while staying alias-tolerant:
       - Signature: `files: list[UploadFile] = File(default=None)`,
         `attachments: list[UploadFile] = File(default=None)`,
         plus keep `file: UploadFile = File(default=None)` and
         `attachment: UploadFile = File(default=None)` for the legacy single-field callers.
       - Build one ordered `uploads` list = (files or []) + (attachments or []) + any single
         file/attachment (append the singles last only if the lists are empty, to avoid dupes).
         If empty → 422 FILE_REQUIRED (unchanged code/message).
       - If `len(uploads) > MAX_ATTACHMENTS` → 400 {"code":"TOO_MANY_ATTACHMENTS",
         "message": f"Max {MAX_ATTACHMENTS} files"}.
       - Read each upload; if any `len(raw) > MAX_ATTACHMENT_BYTES` → 413 FILE_TOO_LARGE (unchanged),
         BEFORE writing any row.
       - _load_campaign (workspace scope) as today.
       - Replace-all upsert: `DELETE FROM campaign_attachments WHERE campaign_id=c.id`, then add one
         CampaignAttachment per upload with `position=index` (enumerate), file_name=(upload.filename
         or "file"), content_type, size_bytes. Commit.
       - Response: `{"campaign_id": str(c.id), "count": N, "attachments": [ {file_name, size_bytes,
         content_type, position} ... ]}`. For backwards compat also echo the FIRST file's
         `file_name`/`size_bytes`/`content_type` at top level (so existing single-file assertions and
         the current Lovable client keep reading the old shape).
    3. app/schemas/__init__.py CampaignResponse (~line 964): add `attachment_count: int = 0`
       (keep `has_attachment` for compat). In campaigns.py where has_attachment is computed
       (~line 305 `_campaign_to_response`), also compute
       `attachment_count = SELECT COUNT(*) FROM campaign_attachments WHERE campaign_id=:cid` and pass
       it into the response (has_attachment stays = attachment_count > 0).
    4. lovable-handoff/openapi.json: update the POST /campaigns/{campaign_id}/attachment requestBody
       to a multipart schema accepting an array of files (`files`/`attachments` array + legacy
       `file`/`attachment`) and the new response shape (count + attachments[]); add `attachment_count`
       to the campaign response schema. Keep it a minimal, valid edit (this file is the frontend
       contract handoff — do not regenerate the whole spec).
    5. tests/test_campaign_attachment.py: add the behavior tests above; keep all existing single-file
       endpoint tests passing.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_campaign_attachment.py -q</automated>
  </verify>
  <done>Multi-file upload stores ordered rows, single-file path unchanged, over-cap rejected, attachment_count surfaces in GET; openapi documents the new contract.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Album delivery — send all attachments on the opener (telegram.py + queue.py)</name>
  <files>app/services/telegram.py, app/services/queue.py, tests/test_send_file_blob.py, tests/test_queue_file_opener.py</files>
  <behavior>
    - telegram_service.send_file called with an `attachments` list of 2 blobs → Telethon
      `client.send_file` is awaited ONCE with a LIST of 2 file paths, force_document passthrough, and
      each temp path's basename equals the original filename (filenames preserved). Caption goes on
      the album. (test_album_multiple_files_sent)
    - send_file with a SINGLE blob (no `attachments` list) is byte-for-byte the existing path:
      one temp file + DocumentAttributeFilename (existing test_blob_source_auto_media stays green).
    - queue file-opener with 2 attachment rows loads BOTH (ORDER BY position) and delivers them as an
      album; with 1 attachment row it uses the existing single-blob send_file call unchanged.
      (extend test_queue_file_opener.py with test_file_opener_multiple_attachments_album; keep the
      single-attachment tests green.)
  </behavior>
  <action>
    1. app/services/telegram.py::send_file: add optional param
       `attachments: Optional[list[dict]] = None` (each dict: {"file_bytes": bytes,
       "file_name": str, "content_type": Optional[str]}).
       - When `attachments` is truthy: build the media as an ALBUM. Create ONE parent temp dir
         (tempfile.mkdtemp()); for each attachment write its bytes to
         `os.path.join(parent, f"{i}", sanitized_basename)` (own subdir per file so Telethon derives
         the EXACT original filename from the basename — no DocumentAttributeFilename needed, no
         cross-file collisions). sanitized_basename = os.path.basename(file_name) or "file".
         Then a single `sent = await client.send_file(peer, [path0, path1, ...], caption=file_caption,
         force_document=force_document)` (Telethon groups a list into an album; caption lands on the
         first item). `sent` may be a list → take the first element's `.id` for message_id. Reuse the
         SAME caption-overflow (>1024 → follow-up message) and the SAME error-handling block
         (FloodWait/PeerFlood/frozen/UserIsBlocked/privacy) — do NOT duplicate a second try/except;
         keep one method, one error path. Clean up the parent temp dir in `finally`
         (shutil.rmtree, ignore_errors=True).
       - When `attachments` is None/empty: the existing single-blob / file_url path runs UNCHANGED
         (do not alter the DocumentAttributeFilename single-file logic).
       - Note in a comment: Telegram may split mixed media types (photo+doc) into more than one
         grouped message; Telethon handles that internally — acceptable for v1.
    2. app/services/queue.py file-opener branch (~line 943-992): replace the single `.first()` load
       with loading ALL rows:
       `SELECT file_data, file_name, content_type, size_bytes FROM campaign_attachments
        WHERE campaign_id=:cid ORDER BY position, created_at`.
       - 0 rows → existing legacy file_url fallback (unchanged).
       - Exactly 1 row → existing single send_file(file_bytes=..., file_name=..., force_document=False)
         call UNCHANGED (lowest risk; preserves the commit-3859ce0 path).
       - >1 rows → call send_file(..., attachments=[{file_bytes, file_name, content_type} for each],
         caption=caption_to_send, force_document=False). For the Phase-23 inbox media bubble, set
         result["media"] from the FIRST attachment (document the v1 limitation in a comment: the inbox
         records the primary file; all files ARE delivered to the recipient).
    3. tests/test_send_file_blob.py: add test_album_multiple_files_sent (mock client.send_file, assert
       awaited once with a list arg of len 2, basenames preserved, force_document passthrough).
    4. tests/test_queue_file_opener.py: add test_file_opener_multiple_attachments_album (insert 2
       attachment rows + a file queue item → the mocked send_file receives attachments list len 2);
       keep the existing single-attachment/text/fallback tests green.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_send_file_blob.py tests/test_queue_file_opener.py -q</automated>
  </verify>
  <done>Opener delivers all attachments as an album with filenames preserved; single-attachment and text-opener paths unchanged; both test files green.</done>
</task>

</tasks>

<verification>
Full targeted suite for the touched areas (run via test-overlay):

```
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest \
  tests/test_campaign_attachment.py tests/test_send_file_blob.py tests/test_queue_file_opener.py -q
```

Manual/contract checks:
- Single-file upload (one `file` field) still returns 200 + old top-level fields (back-compat).
- Multi-file upload of 3 files → GET campaign shows attachment_count==3, has_attachment true.
- Duplicate a 2-file campaign → copy has 2 attachments.
</verification>

<success_criteria>
- campaign_attachments is 1-to-N (UNIQUE dropped, position column, migration 060 idempotent).
- Upload endpoint accepts multiple files (replace-all), caps at 10, rejects over-cap (400) and
  oversized files (413); single-file callers unchanged (200, old response shape preserved).
- Opener delivers ALL attachments as one grouped album with original filenames preserved.
- Existing single-attachment campaigns send exactly as before (no regression on commit-3859ce0 path).
- Duplicate copies every attachment; attachment_count surfaces in the campaign response.
- openapi.json documents the multi-file request/response contract.
- Rate-limit / FloodWait logic untouched.
</success_criteria>

<output>
After completion, create `.planning/quick/260709-dbl-campaign-first-message-support-multiple-/260709-dbl-SUMMARY.md`
</output>
