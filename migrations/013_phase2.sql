-- migrations/013_phase2.sql
-- Phase 2: TG Accounts & Contacts foundation
-- Adds: folders, contacts, onboarding_sessions, csv_imports tables
-- Modifies: senders (+ lifecycle_status, + rate_per_min/hour/day, + role CHECK; - is_active)
-- БД должна быть пустой (Phase 1 D-01). Все операторы идемпотентны (IF NOT EXISTS / IF EXISTS).

BEGIN;

-- ── 1. folders (D-05) ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS folders (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name         VARCHAR(100) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT folders_workspace_name_unique UNIQUE (workspace_id, name)
);

CREATE INDEX IF NOT EXISTS idx_folders_workspace ON folders(workspace_id);

-- ── 2. contacts (D-01) ──────────────────────────────────────────────────────
-- NB: НЕ ПУТАТЬ с contacts_cache (per-sender Telegram-resolve cache из Phase 0).
CREATE TABLE IF NOT EXISTS contacts (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    folder_id             UUID NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    phone                 VARCHAR(20),
    username              VARCHAR(50),
    full_name             VARCHAR(200),
    source                VARCHAR(100),
    custom                JSONB NOT NULL DEFAULT '{}',
    tg_status             VARCHAR(20) NOT NULL DEFAULT 'pending',
    tg_telegram_id        BIGINT,
    tg_username_resolved  VARCHAR(50),
    tg_error              TEXT,
    tg_checked_at         TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT contacts_tg_status_check
        CHECK (tg_status IN ('pending', 'registered', 'not_registered', 'error', 'unchecked')),
    CONSTRAINT contacts_phone_or_username_check
        CHECK (phone IS NOT NULL OR username IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_contacts_workspace ON contacts(workspace_id);
CREATE INDEX IF NOT EXISTS idx_contacts_folder    ON contacts(folder_id);
CREATE INDEX IF NOT EXISTS idx_contacts_tg_status
    ON contacts(tg_status)
    WHERE tg_status = 'pending';

-- Partial UNIQUE: (workspace_id, phone) и (workspace_id, username) — только когда поле NOT NULL.
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_workspace_phone_unique
    ON contacts(workspace_id, phone)
    WHERE phone IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_workspace_username_unique
    ON contacts(workspace_id, username)
    WHERE username IS NOT NULL;

-- ── 3. onboarding_sessions (D-16) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS onboarding_sessions (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id             UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    phone                    VARCHAR(20) NOT NULL,
    phone_code_hash          TEXT NOT NULL,
    encrypted_session_string TEXT NOT NULL,
    role                     VARCHAR(20) NOT NULL DEFAULT 'sender',
    proxy                    JSONB,
    status                   VARCHAR(20) NOT NULL,
    expires_at               TIMESTAMPTZ NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT onboarding_sessions_role_check
        CHECK (role IN ('sender', 'checker')),
    CONSTRAINT onboarding_sessions_status_check
        CHECK (status IN ('code_sent', 'awaiting_2fa', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_workspace
    ON onboarding_sessions(workspace_id);
CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_expires_at
    ON onboarding_sessions(expires_at);

-- ── 4. csv_imports (C-02, RESEARCH Option B — DB-blob) ──────────────────────
CREATE TABLE IF NOT EXISTS csv_imports (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    file_data          BYTEA NOT NULL,
    columns            JSONB NOT NULL,
    suggested_mapping  JSONB NOT NULL,
    encoding           VARCHAR(20),
    delimiter          VARCHAR(5),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at         TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '30 minutes')
);

CREATE INDEX IF NOT EXISTS idx_csv_imports_workspace  ON csv_imports(workspace_id);
CREATE INDEX IF NOT EXISTS idx_csv_imports_expires_at ON csv_imports(expires_at);

-- ── 5. senders extension (D-11, D-13, D-21) ─────────────────────────────────
-- lifecycle_status: новое поле, заменяет is_active.
ALTER TABLE senders ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(20) NOT NULL DEFAULT 'active';
ALTER TABLE senders DROP CONSTRAINT IF EXISTS senders_lifecycle_status_check;
ALTER TABLE senders ADD CONSTRAINT senders_lifecycle_status_check
    CHECK (lifecycle_status IN ('active', 'warmup', 'paused'));

-- rate limits per-sender (D-13). Defaults = эмпирический "зелёный коридор".
ALTER TABLE senders ADD COLUMN IF NOT EXISTS rate_per_min  INT NOT NULL DEFAULT 4;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS rate_per_hour INT NOT NULL DEFAULT 20;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS rate_per_day  INT NOT NULL DEFAULT 150;

-- role CHECK constraint (D-21 / CONTEXT specifics).
ALTER TABLE senders DROP CONSTRAINT IF EXISTS senders_role_check;
ALTER TABLE senders ADD CONSTRAINT senders_role_check
    CHECK (role IN ('sender', 'checker'));

-- Drop legacy boolean is_active (D-11). Все usages переписаны на lifecycle_status + auth_status.
ALTER TABLE senders DROP COLUMN IF EXISTS is_active;

COMMIT;
