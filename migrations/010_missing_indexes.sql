-- migrations/010_missing_indexes.sql
-- Add missing indexes identified during code review.
-- All statements are idempotent (IF NOT EXISTS).

BEGIN;

-- message_queue: main worker query filters by (sender_id, status, scheduled_at).
-- Partial index on active statuses keeps it small and fast.
CREATE INDEX IF NOT EXISTS idx_message_queue_sender_status_scheduled
    ON message_queue(sender_id, status, scheduled_at)
    WHERE status IN ('pending', 'processing');

-- messages_log: frequently queried by sender_id for history and stats.
CREATE INDEX IF NOT EXISTS idx_messages_log_sender_id
    ON messages_log(sender_id);

-- conversations: filtered by status and ai_enabled in list endpoint.
CREATE INDEX IF NOT EXISTS idx_conversations_sender_status
    ON conversations(sender_id, status);

CREATE INDEX IF NOT EXISTS idx_conversations_ai_enabled
    ON conversations(ai_enabled);

-- warmup_sessions: queried by both participants.
CREATE INDEX IF NOT EXISTS idx_warmup_sessions_sender_a
    ON warmup_sessions(sender_a_id);

CREATE INDEX IF NOT EXISTS idx_warmup_sessions_sender_b
    ON warmup_sessions(sender_b_id);

-- proxy_pool: lookup "which proxy is assigned to this sender".
-- Partial index skips unassigned rows.
CREATE INDEX IF NOT EXISTS idx_proxy_pool_assigned_sender
    ON proxy_pool(assigned_to_sender_id)
    WHERE assigned_to_sender_id IS NOT NULL;

COMMIT;
