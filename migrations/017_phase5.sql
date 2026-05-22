-- migrations/017_phase5.sql
-- Phase 5: Inbox & Analytics
-- - Extends conversations.status CHECK to include 'bot_ignored' (D-07)
-- - Creates llm_calls audit table (D-09)
-- - Adds 3 composite indexes on conversations for real-time analytics (C-04)
-- - Defensive CREATE TABLE IF NOT EXISTS for `messages` (table predates
--   migration 012 but its DDL was lost when the repo was forked from
--   internal telegram-api; idempotent guard keeps fresh DBs bootable for
--   Phase 5 inbox endpoints + tests).
--
-- Idempotent (IF NOT EXISTS / DROP CONSTRAINT IF EXISTS).

BEGIN;

-- 0. Defensive `messages` table create (DDL lost from initial brownfield commit).
--    NB: production DBs already have this table — IF NOT EXISTS makes the
--    statement a no-op there. Fresh test DBs need it before INBX-02 inbox
--    history endpoints can return rows.
CREATE TABLE IF NOT EXISTS messages (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id          UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    conversation_id       UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction             VARCHAR(20) NOT NULL,    -- 'inbound' | 'outbound'
    message_text          TEXT NOT NULL,
    sent_by               VARCHAR(20) NOT NULL,    -- 'contact' | 'ai' | 'human'
    telegram_message_id   BIGINT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT messages_conversation_telegram_unique
        UNIQUE (conversation_id, telegram_message_id)
);

-- Indexes referenced by migration 001 — make sure they exist on a fresh DB.
CREATE INDEX IF NOT EXISTS idx_messages_telegram_message_id
    ON messages(telegram_message_id)
    WHERE telegram_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON messages(conversation_id, created_at DESC);

-- 1. Extend conversations.status CHECK constraint to include 'bot_ignored' (Phase 5 D-07).
ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active','manual','paused','lead','handoff','finished','bot_ignored'));

-- 2. llm_calls table (Phase 5 D-09 — OpenAI chat.completions audit log).
CREATE TABLE IF NOT EXISTS llm_calls (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    conversation_id   UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    campaign_id       UUID REFERENCES campaigns(id) ON DELETE SET NULL,
    agent_id          UUID REFERENCES ai_contexts(id) ON DELETE SET NULL,
    sender_id         UUID REFERENCES senders(id) ON DELETE SET NULL,
    model             VARCHAR(50) NOT NULL,
    prompt            JSONB NOT NULL,
    response_text     TEXT,
    tool_calls        JSONB,
    prompt_tokens     INT,
    completion_tokens INT,
    total_tokens      INT,
    latency_ms        INT,
    error             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_workspace_created
    ON llm_calls(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_calls_conversation_created
    ON llm_calls(conversation_id, created_at DESC);

-- 3. Composite indexes for real-time analytics queries (Phase 5 C-04).
CREATE INDEX IF NOT EXISTS idx_conversations_workspace_campaign_status
    ON conversations(workspace_id, campaign_id, status)
    WHERE campaign_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversations_workspace_agent_status
    ON conversations(workspace_id, ai_context_id, status)
    WHERE ai_context_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversations_workspace_sender_status
    ON conversations(workspace_id, sender_id, status);

COMMIT;
