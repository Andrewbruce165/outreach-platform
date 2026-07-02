-- migrations/044_llm_settings.sql
-- Per-workspace LLM provider/model/knobs + encrypted BYO API key (Phase 18).
--
-- D-01: setting scope is workspace-level (PK = workspace_id, one row per workspace;
--       no per-agent override this phase — the entire workspace uses one provider/model).
-- D-02: absence of a row = platform default — platform OPENAI_API_KEY +
--       settings.openai_model. Nothing breaks for existing (row-less) workspaces;
--       mirrors the warmup_settings (038) default-absent pattern.
-- D-04: api_key stored Fernet-encrypted (reuse app/services/encryption.py); only
--       api_key_prefix (prefix+last4) is ever returned to the UI, never the full key,
--       never written to logs.
-- D-05/D-06: api_key_status tracks validity ('unset'|'valid'|'invalid'); runtime
--       key-level errors flip it to 'invalid' and fall back to the platform default.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + DO $$ EXCEPTION duplicate_object $$ CHECK
-- guards + ADD COLUMN IF NOT EXISTS. Auto-applied via app/database.py::_apply_migrations
-- (lexical order, advisory lock). Fail-fast: api does NOT start if this migration raises.
--
-- NOTE: provider/api_key_status use VARCHAR+CHECK, NOT a PG enum — ALTER TYPE ADD VALUE
-- cannot run inside a transaction (same reason campaigns.status is VARCHAR+CHECK).

BEGIN;

CREATE TABLE IF NOT EXISTS llm_settings (
    workspace_id        UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    provider            TEXT NOT NULL DEFAULT 'openai',
    model               TEXT,
    api_key_encrypted   TEXT,
    api_key_prefix      TEXT,
    api_key_status      TEXT NOT NULL DEFAULT 'unset',
    temperature         DOUBLE PRECISION,
    reasoning_effort    TEXT,
    max_tokens          INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$ BEGIN
  ALTER TABLE llm_settings ADD CONSTRAINT llm_settings_provider_chk
    CHECK (provider IN ('openai','anthropic'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE llm_settings ADD CONSTRAINT llm_settings_key_status_chk
    CHECK (api_key_status IN ('unset','valid','invalid'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- D-07: llm_logger records provider + key_source per call. Nullable, no backfill.
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS provider   TEXT;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS key_source TEXT;

COMMIT;
