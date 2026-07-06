---
phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/051_account_import.sql
  - app/models/__init__.py
  - tests/test_account_import.py
  - tests/test_account_import_worker.py
  - tests/conftest.py
autonomous: true
requirements: [IMPT-08]
must_haves:
  truths:
    - "Fresh/test DB (create_all) and prod (migration) end up with identical account-import schema — no NotNullViolation on raw INSERT"
    - "senders gains client_fingerprint (JSONB, nullable) + twofa_password_enc (TEXT, nullable) with no regression to existing 13 senders"
    - "Wave-0 RED test files exist and collect (import-inside-body) so downstream tasks turn them green"
    - "Telethon connect/get_me is stubbed in tests — no network, no leaked auth_key"
  artifacts:
    - path: "migrations/051_account_import.sql"
      provides: "2 senders columns + account_import_stagings/jobs/items tables, idempotent"
      contains: "ADD COLUMN IF NOT EXISTS client_fingerprint"
    - path: "app/models/__init__.py"
      provides: "Sender 2 new columns + AccountImportStaging/AccountImportJob/AccountImportItem ORM classes"
      contains: "twofa_password_enc"
    - path: "tests/test_account_import.py"
      provides: "RED scaffold for IMPT-01/03/04/05/06/07"
      min_lines: 60
    - path: "tests/test_account_import_worker.py"
      provides: "RED scaffold for IMPT-02"
      min_lines: 30
  key_links:
    - from: "app/models/__init__.py Sender"
      to: "migrations/051_account_import.sql"
      via: "column names + server_default must match exactly"
      pattern: "client_fingerprint|twofa_password_enc"
---

<objective>
Lay the Phase 21 schema + test foundation. Add the two new `senders` columns (per-account client fingerprint + encrypted 2FA) and the three account-import tables (staging blob, job, per-file item) as an idempotent migration mirrored on the ORM, then drop a Wave-0 RED test scaffold that every later task turns green.

Purpose: Everything downstream (fingerprint seam, preview, import routine, worker) reads/writes these columns and tables — they must exist first, and the test/fresh-DB schema (built by `create_all` from the ORM, NOT the migration) must match prod exactly or raw INSERTs hit NotNullViolation (memory `project-orm-default-vs-server-default-drift`).
Output: `migrations/051_account_import.sql`, ORM mirrors, and two collecting-but-RED test files with a stubbed-Telethon fixture.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-CONTEXT.md
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-RESEARCH.md
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-VALIDATION.md

<interfaces>
<!-- Extracted from the running codebase — executor should use these directly. -->

Reference migration (Phase 20, the pattern to copy): migrations/049_account_profile.sql
Reference table with BYTEA + expires_at TTL staging: app/models/__init__.py CsvImport (line ~588)
Reference status-row model (pending/processing/indexed/failed): app/models/__init__.py KbDocument (line ~776) — note `id` uses BOTH default=uuid.uuid4 AND server_default=text("gen_random_uuid()") because create_all wins over the migration in init_db.

Current Sender ORM (app/models/__init__.py line ~74) — new columns go right after profile_field_changed_at (line ~138):
```python
class Sender(Base):
    __tablename__ = "senders"
    ...
    profile_field_changed_at = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # <-- ADD the two Phase 21 columns here
```

Migration list is auto-applied at api start (app/database.py::_apply_migrations) in lexical order; latest on disk is `050_lower_new_dialog_cap.sql`, so the next free number is 051. Every migration MUST be idempotent (`ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`).

Test stubbing precedent (tests/test_onboarding.py lines ~40-109): a mock Telethon client is built with `client.connect = AsyncMock(...)`, `client.is_user_authorized = AsyncMock(...)`, `client.get_me = AsyncMock(return_value=me)`, and injected via `monkeypatch.setattr(<router_module>, "make_telegram_client", _factory)`. A VALID empty-auth-key StringSession is produced with `StringSession()` (no arg) then `.save()`.
KB worker test precedent: tests/test_kb_ingest_worker.py monkeypatches `app.services.kb_ingest.embed_texts`; mirror this style for the account-import worker test.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Migration 051 + ORM mirrors (2 senders columns + 3 import tables)</name>
  <read_first>
    - migrations/049_account_profile.sql (the idempotent-migration pattern to copy verbatim)
    - app/models/__init__.py lines 74-143 (Sender class — where the 2 new columns land)
    - app/models/__init__.py lines 588-602 (CsvImport — BYTEA + expires_at staging pattern)
    - app/models/__init__.py lines 776-798 (KbDocument — status-row model + dual id default pattern)
  </read_first>
  <action>
    Create `migrations/051_account_import.sql` (idempotent, auto-applied). Content:

    1. Two nullable senders columns (NULL = today's behavior, so no server_default needed on nullable cols but they MUST be mirrored on the ORM):
    ```sql
    ALTER TABLE senders ADD COLUMN IF NOT EXISTS client_fingerprint JSONB NULL;
    ALTER TABLE senders ADD COLUMN IF NOT EXISTS twofa_password_enc  TEXT  NULL;  -- Fernet ciphertext, same as session_string
    ```

    2. `account_import_stagings` — ZIP preview blob with TTL (mirror csv_imports):
    ```sql
    CREATE TABLE IF NOT EXISTS account_import_stagings (
      id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      zip_data     BYTEA NOT NULL,
      summary      JSONB NOT NULL DEFAULT '{}'::jsonb,   -- matched/unpaired/malformed preview result
      created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      expires_at   TIMESTAMPTZ NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_ais_workspace ON account_import_stagings(workspace_id);
    ```

    3. `account_import_jobs` — one row per confirmed batch:
    ```sql
    CREATE TABLE IF NOT EXISTS account_import_jobs (
      id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      staging_id   UUID NULL REFERENCES account_import_stagings(id) ON DELETE SET NULL,
      role         VARCHAR(20) NOT NULL DEFAULT 'sender',   -- 'sender' | 'checker' (D-16)
      status       VARCHAR(20) NOT NULL DEFAULT 'running',  -- 'running' | 'done'
      total        INTEGER NOT NULL DEFAULT 0,
      processed    INTEGER NOT NULL DEFAULT 0,
      created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_aij_workspace ON account_import_jobs(workspace_id);
    ```

    4. `account_import_items` — one row per file pair, carries its own session bytes + parsed JSON so the worker never re-unzips:
    ```sql
    CREATE TABLE IF NOT EXISTS account_import_items (
      id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      job_id        UUID NOT NULL REFERENCES account_import_jobs(id) ON DELETE CASCADE,
      workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      basename      VARCHAR(64) NOT NULL,                   -- '+18646884306' pairing key / phone fallback
      session_blob  BYTEA NULL,                             -- vendor .session bytes; worker NULLs it on terminal status
      vendor_json   JSONB NOT NULL DEFAULT '{}'::jsonb,     -- parsed vendor JSON (incl. twoFA/proxy/fingerprint)
      status        VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending|processing|ok|failed
      result        VARCHAR(30) NULL,                       -- imported|already_connected|auth_failed|convert_failed|...
      reason        TEXT NULL,
      sender_id     UUID NULL REFERENCES senders(id) ON DELETE SET NULL,
      created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_aii_job ON account_import_items(job_id);
    CREATE INDEX IF NOT EXISTS idx_aii_pending ON account_import_items(status) WHERE status = 'pending';
    ```

    Then mirror ALL of this in `app/models/__init__.py`:
    - Add to `Sender` (right after `profile_field_changed_at`, ~line 138):
      ```python
      # Phase 21 (IMPT-04/05, mig 051): per-account import extras. NULL fingerprint =>
      # make_telegram_client falls back to the global _CLIENT_FINGERPRINT (D-02, no regression).
      # NULL twofa => no stored password. Both NULLABLE => no server_default required.
      client_fingerprint = Column(JSONB, nullable=True)
      twofa_password_enc = Column(Text, nullable=True)  # Fernet ciphertext (D-05/D-07); never logged/returned
      ```
    - Add three new model classes (near CsvImport / KbDocument). Every NOT NULL column MUST carry a matching `server_default`, and every `id` uses BOTH `default=uuid.uuid4` AND `server_default=text("gen_random_uuid()")` (create_all wins over the migration DEFAULT — same reason as KbDocument):
      ```python
      class AccountImportStaging(Base):
          __tablename__ = "account_import_stagings"
          id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
          workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
          zip_data = Column(LargeBinary, nullable=False)
          summary = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
          created_at = Column(DateTime(timezone=True), server_default=func.now())
          expires_at = Column(DateTime(timezone=True), nullable=False)

      class AccountImportJob(Base):
          __tablename__ = "account_import_jobs"
          id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
          workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
          staging_id = Column(UUID(as_uuid=True), ForeignKey("account_import_stagings.id", ondelete="SET NULL"), nullable=True)
          role = Column(String(20), nullable=False, server_default='sender')
          status = Column(String(20), nullable=False, server_default='running')
          total = Column(Integer, nullable=False, server_default='0')
          processed = Column(Integer, nullable=False, server_default='0')
          created_at = Column(DateTime(timezone=True), server_default=func.now())
          updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

      class AccountImportItem(Base):
          __tablename__ = "account_import_items"
          id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
          job_id = Column(UUID(as_uuid=True), ForeignKey("account_import_jobs.id", ondelete="CASCADE"), nullable=False)
          workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
          basename = Column(String(64), nullable=False)
          session_blob = Column(LargeBinary, nullable=True)
          vendor_json = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
          status = Column(String(20), nullable=False, server_default='pending')
          result = Column(String(30), nullable=True)
          reason = Column(Text, nullable=True)
          sender_id = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="SET NULL"), nullable=True)
          created_at = Column(DateTime(timezone=True), server_default=func.now())
          updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
      ```
    Confirm `Integer`, `LargeBinary`, `Text`, `JSONB`, `text`, `func`, `String`, `DateTime`, `UUID`, `ForeignKey` are already imported at the top of models/__init__.py (they are — used by existing classes); add none that are missing only.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py --collect-only -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "ADD COLUMN IF NOT EXISTS" migrations/051_account_import.sql` returns 2
    - `grep -c "CREATE TABLE IF NOT EXISTS" migrations/051_account_import.sql` returns 3
    - `grep -q "client_fingerprint = Column(JSONB, nullable=True)" app/models/__init__.py` succeeds
    - `grep -q "twofa_password_enc = Column(Text, nullable=True)" app/models/__init__.py` succeeds
    - `grep -q "class AccountImportItem(Base)" app/models/__init__.py` succeeds
    - `grep -c "server_default=text(\"gen_random_uuid()\")" app/models/__init__.py` increases by 3 (one per new table)
    - Test-overlay collection command exits 0 (schema built by create_all with the new tables, no ORM import error)
  </acceptance_criteria>
  <done>Migration 051 exists (idempotent, 2 columns + 3 tables), ORM mirrors all 5 additions with server_default on NOT NULL cols and gen_random_uuid() ids, and the test-overlay builds the schema without error.</done>
</task>

<task type="auto">
  <name>Task 2: Wave-0 RED test scaffold (2 files + stubbed-Telethon fixture)</name>
  <read_first>
    - tests/test_onboarding.py lines 40-135 (how a mock Telethon client + valid StringSession are built and monkeypatched)
    - tests/test_kb_ingest_worker.py (worker-test structure + monkeypatch of the module-level dependency)
    - .planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-VALIDATION.md (Wave 0 Requirements + Per-Task Verification Map)
    - .planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-RESEARCH.md (§Validation Architecture → Phase Requirements → Test Map — the 7 named tests)
  </read_first>
  <action>
    Create two RED test files whose bodies `import` the not-yet-existing production symbols INSIDE the test function (deferred import) so `--collect-only` stays clean while the assertions fail until the code lands. Mirror the onboarding stub style (no real Telegram network).

    In `tests/conftest.py`, add a reusable fixture `stub_import_telethon(monkeypatch)` (or a plain helper) that:
    - builds a mock client with `connect = AsyncMock(return_value=None)`, `is_user_authorized = AsyncMock(return_value=True)`, `get_me = AsyncMock(return_value=SimpleNamespace(id=<int>, username=<str>, first_name=<str>, phone=<str>))`, `disconnect = AsyncMock()`, `is_connected = MagicMock(return_value=True)`, and a `.session` whose `.save()` returns a valid empty-auth-key StringSession (`StringSession().save()`).
    - Also add a `build_vendor_sqlite_session(tmp_path)` helper that constructs a SYNTHETIC `SQLiteSession` file on disk with a fake dc_id + 256-byte auth_key via `telethon.sessions.SQLiteSession` + `set_dc`/`auth_key` assignment (so the offline-conversion test does NOT depend on the gitignored live sample). Never read/commit the real scratchpad `.session`.

    `tests/test_account_import.py` — RED tests (deferred-import bodies), one per IMPT req in the research Test Map:
    - `test_sqlite_to_stringsession_offline` — IMPT-03: builds a synthetic vendor SQLiteSession, asserts the conversion helper (`from app.services.account_import import sqlite_to_string_session`) returns a `1A`-prefixed StringSession that round-trips to the same auth_key, with no network.
    - `test_fingerprint_override_and_strict_fallback` — IMPT-04: asserts `make_telegram_client(session, fingerprint=None)` yields kwargs byte-identical to today's `_CLIENT_FINGERPRINT` and keeps `lang_pack='tdesktop'`; a non-None fingerprint dict overrides device/version/locale but still forces `lang_pack='tdesktop'`.
    - `test_preview_pairing` — IMPT-01: builds an in-memory ZIP (matched pair + orphan .json + orphan .session + malformed .json), asserts `unpack_and_pair` returns correct matched/unpaired/malformed lists.
    - `test_twofa_encrypted_at_rest` — IMPT-05: asserts a stored `twofa_password_enc` decrypts back to the plaintext and that the value is never present in the API response/log surface (assert the stored column ciphertext != plaintext).
    - `test_dedup_skip_and_proxy` — IMPT-06: second import of the same tg_id → item result `already_connected`, existing sender's session_string unchanged; JSON proxy honored else pool.
    - `test_partial_success_and_start_state` — IMPT-07: a batch with one broken pair → that item `failed`, the rest `ok`; an imported sender row has `lifecycle_status='active'` + `restriction_status='none'`.

    `tests/test_account_import_worker.py` — RED test (IMPT-02):
    - `test_worker_drives_items_and_status` — create a job + N pending items directly, monkeypatch the per-account import routine (module ref, e.g. `app.services.account_import.import_one_account`) to a deterministic stub, run one worker tick per item, assert items go pending→processing→ok/failed and the status endpoint / job row reports processed/total. Mirror `test_kb_ingest_worker.py` claim/commit structure.

    Every test that touches production symbols must import them inside the function body so collection passes now and the tests fail (RED) until later tasks implement the symbols. Add a short module docstring in each file naming the IMPT reqs it covers.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py tests/test_account_import_worker.py --collect-only -q</automated>
  </verify>
  <acceptance_criteria>
    - `grep -c "def test_" tests/test_account_import.py` returns 6 (the six named tests)
    - `grep -q "def test_worker_drives_items_and_status" tests/test_account_import_worker.py` succeeds
    - `grep -q "StringSession()" tests/conftest.py` succeeds (synthetic-session builder present)
    - `grep -q "build_vendor_sqlite_session" tests/conftest.py` succeeds
    - `--collect-only` exits 0 (no collection errors; deferred imports keep it clean)
    - Running the files (not collect-only) shows the new tests FAILING/ERRORing (RED) — they reference symbols that do not exist yet
    - No test performs a real Telegram connect (grep the two files: no un-mocked `TelegramClient(` construction that connects to the network)
  </acceptance_criteria>
  <done>Two test files + conftest fixtures exist, collect cleanly, and are RED (fail on missing production symbols). Telethon connect/get_me is stubbed; the offline-conversion test uses a synthetic session, never the live sample.</done>
</task>

</tasks>

<verification>
- `migrations/051_account_import.sql` is idempotent (all statements `IF NOT EXISTS`) and re-running the api container applies it once (schema_migrations records it).
- Test-overlay `--collect-only` on both files exits 0.
- Full suite still collects with 0 errors (new ORM classes don't break metadata).
</verification>

<success_criteria>
- Migration 051 + ORM mirrors deliver the 2 senders columns + 3 import tables with matching server_defaults.
- Wave-0 test files exist, collect, and are RED — ready for downstream tasks to turn green.
- No Telegram network in any test; no live auth_key touched.
</success_criteria>

<output>
After completion, create `.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-01-SUMMARY.md`
</output>
