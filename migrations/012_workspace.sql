-- migrations/012_workspace.sql
-- Phase 1: multi-tenant foundation
-- Creates workspaces, user_workspaces, workspace_api_keys + adds workspace_id FK
-- to all 11 tenant-scoped tables in a single transaction (D-02).
-- БД должна быть пустой (D-01). Все операторы идемпотентны (IF NOT EXISTS / IF EXISTS).

BEGIN;

-- ── 1. Root tenant table ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS workspaces (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 2. user_workspaces (many-to-many; D-10 — НЕ ставим uniqueness on supabase_user_id) ─
CREATE TABLE IF NOT EXISTS user_workspaces (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supabase_user_id    TEXT NOT NULL,
    workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    role                VARCHAR(20) NOT NULL DEFAULT 'owner',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT user_workspaces_role_check
        CHECK (role IN ('owner', 'admin', 'member'))
);

CREATE INDEX IF NOT EXISTS idx_user_workspaces_supabase_user_id
    ON user_workspaces(supabase_user_id);

CREATE INDEX IF NOT EXISTS idx_user_workspaces_workspace_id
    ON user_workspaces(workspace_id);

-- ── 3. workspace_api_keys ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS workspace_api_keys (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    prefix        VARCHAR(12) NOT NULL,
    bcrypt_hash   TEXT NOT NULL,
    name          VARCHAR(50) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);

-- Partial index: только активные ключи участвуют в lookup (C-02 resolved)
CREATE INDEX IF NOT EXISTS idx_workspace_api_keys_prefix_active
    ON workspace_api_keys(prefix)
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_workspace_api_keys_workspace_id
    ON workspace_api_keys(workspace_id);

-- ── 4. ALTER tenant-scoped tables: add workspace_id FK ──────────────────────
-- D-03: все 11 таблиц (включая proxy_pool и warmup_*) получают workspace_id NOT NULL.

ALTER TABLE senders
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_senders_workspace
    ON senders(workspace_id);

ALTER TABLE messages_log
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_messages_log_workspace
    ON messages_log(workspace_id);

ALTER TABLE contacts_cache
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_contacts_cache_workspace
    ON contacts_cache(workspace_id);

ALTER TABLE ai_contexts
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_ai_contexts_workspace
    ON ai_contexts(workspace_id);

ALTER TABLE message_queue
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_message_queue_workspace
    ON message_queue(workspace_id);

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_conversations_workspace
    ON conversations(workspace_id);

ALTER TABLE warmup_pool
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_warmup_pool_workspace
    ON warmup_pool(workspace_id);

ALTER TABLE warmup_sessions
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_warmup_sessions_workspace
    ON warmup_sessions(workspace_id);

ALTER TABLE warmup_messages
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_warmup_messages_workspace
    ON warmup_messages(workspace_id);

ALTER TABLE proxy_pool
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_proxy_pool_workspace
    ON proxy_pool(workspace_id);

ALTER TABLE context_contact_assignments
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_context_contact_assignments_workspace
    ON context_contact_assignments(workspace_id);

COMMIT;
