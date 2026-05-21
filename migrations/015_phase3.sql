-- migrations/015_phase3.sql
-- Phase 3: Agents (AI Templates) — cleanup ai_contexts schema
-- Drops 6 deprecated columns (Campaign-concern — moved to Phase 4)
-- Drops senders.ai_context_id (agent no longer tied to sender — D-04)
-- Adds UNIQUE (workspace_id, name) for duplicate-protection (D-02)
-- БД чистая (Phase 1 D-01) — no backfill needed.
-- All operators idempotent (IF EXISTS / IF NOT EXISTS).

BEGIN;

-- ── 1. ai_contexts: drop deprecated columns (D-01) ──────────────────────────
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS auto_pause_triggers;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS webhook_functions;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS document_webhook_url;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS max_message_length;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS response_delay_seconds;
ALTER TABLE ai_contexts DROP COLUMN IF EXISTS is_active;

-- ── 2. senders: drop ai_context_id (D-04) ───────────────────────────────────
ALTER TABLE senders DROP COLUMN IF EXISTS ai_context_id;

-- ── 3. ai_contexts: UNIQUE (workspace_id, name) for duplicate-protection (D-02) ──
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_contexts_workspace_name
    ON ai_contexts(workspace_id, name);

COMMIT;
