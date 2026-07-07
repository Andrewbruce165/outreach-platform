-- 051: Bulk Telegram Account Import via session-JSON upload (Phase 21 — Plan 21-01).
--
-- Next free migration number is 051 (latest on disk was 050_lower_new_dialog_cap.sql).
-- Auto-applied at api start by app/database.py::_apply_migrations in lexical order; this
-- file MUST be idempotent (ADD COLUMN / CREATE TABLE / CREATE INDEX ... IF NOT EXISTS) —
-- the applier re-runs it on any schema drift and the api fail-fasts (does not start) if a
-- migration raises. No BEGIN/COMMIT wrapper — each statement is atomic.
--
-- Two new senders columns (both NULLABLE => NULL = today's behaviour, so no server_default
-- required on the migration; they ARE mirrored on the ORM):
--   client_fingerprint  JSONB  NULL  — per-account device fingerprint (IMPT-04). NULL =>
--                                       make_telegram_client falls back to the global
--                                       _CLIENT_FINGERPRINT (D-02, no regression to the 13 senders).
--   twofa_password_enc  TEXT   NULL  — Fernet ciphertext of the account's 2FA password (IMPT-05),
--                                       same encryption as session_string. NULL => no stored password.
ALTER TABLE senders ADD COLUMN IF NOT EXISTS client_fingerprint JSONB NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS twofa_password_enc  TEXT  NULL;  -- Fernet ciphertext, same as session_string

-- account_import_stagings — ZIP preview blob with a TTL (mirrors csv_imports).
-- summary carries the matched/unpaired/malformed preview result computed at upload time.
-- NOT NULL columns carry a DEFAULT so create_all-built (test/fresh) schema INSERTs that omit
-- them do not hit NotNullViolation (memory project-orm-default-vs-server-default-drift).
CREATE TABLE IF NOT EXISTS account_import_stagings (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  zip_data     BYTEA NOT NULL,
  summary      JSONB NOT NULL DEFAULT '{}'::jsonb,   -- matched/unpaired/malformed preview result
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ais_workspace ON account_import_stagings(workspace_id);

-- account_import_jobs — one row per confirmed batch.
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

-- account_import_items — one row per file pair; carries its own session bytes + parsed JSON
-- so the worker never re-unzips. session_blob is NULLed by the worker on terminal status.
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
