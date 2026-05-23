-- migrations/018_phase5_1.sql
-- Phase 05.1: Lovable UI v1 (aimly) — backend schema gap closure
-- Adds: telemetry_events table; widens ai_contexts (12 cols) and campaigns (4 cols).
-- Idempotent (IF NOT EXISTS / DROP CONSTRAINT IF EXISTS). Wrapped in BEGIN; COMMIT;.
-- Empirical rate limits 4/20/150 untouched (CLAUDE.md guard).
--
-- NB on auto_pause_triggers: migration 015 (Phase 3) DROPPED this column. The
-- ADD COLUMN IF NOT EXISTS below RECREATES it as nullable TEXT[]; on fresh DBs
-- this is a real ADD, on prod DBs that somehow kept the column it is a no-op.

BEGIN;

-- 1. Telemetry events (UI-SPEC §9; 15-event whitelist enforced at router-level, not DDL).
CREATE TABLE IF NOT EXISTS telemetry_events (
    event_id      UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id       TEXT,
    event         VARCHAR(80) NOT NULL,
    props         JSONB NOT NULL DEFAULT '{}'::jsonb,
    client_ts     TIMESTAMPTZ,
    server_ts     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_telemetry_workspace_event_server
    ON telemetry_events(workspace_id, event, server_ts DESC);

-- 2. Agent (ai_contexts) v2 columns (UI-SPEC §5.8 — 11 new cols + auto_pause_triggers
--    revival, all nullable/defaulted).
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS who_is_agent TEXT;
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS company_knowledge TEXT;
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS knowledge_base TEXT;
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS voice_baseline VARCHAR(20);
ALTER TABLE ai_contexts DROP CONSTRAINT IF EXISTS ai_contexts_voice_baseline_check;
ALTER TABLE ai_contexts ADD CONSTRAINT ai_contexts_voice_baseline_check
    CHECK (voice_baseline IS NULL OR voice_baseline IN ('Professional','Friendly','Playful'));
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS tone JSONB
    DEFAULT '{"formal": 0, "warm": 0, "brief": 0}'::jsonb;
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS max_message_length INT DEFAULT 280;
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS mirror_language BOOLEAN DEFAULT TRUE;
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS allow_emoji BOOLEAN DEFAULT FALSE;
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS banlist TEXT[];
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS qa_pairs JSONB;
-- auto_pause_triggers: dropped by migration 015 (Phase 3) — RECREATED here as
-- nullable TEXT[]. On fresh DBs this is a real ADD; if a prod DB has not yet
-- applied 015, IF NOT EXISTS still keeps this a safe no-op.
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS auto_pause_triggers TEXT[];
ALTER TABLE ai_contexts ADD COLUMN IF NOT EXISTS auto_pause_scope VARCHAR(20) DEFAULT 'conversation';
ALTER TABLE ai_contexts DROP CONSTRAINT IF EXISTS ai_contexts_auto_pause_scope_check;
ALTER TABLE ai_contexts ADD CONSTRAINT ai_contexts_auto_pause_scope_check
    CHECK (auto_pause_scope IN ('conversation','contact','campaign'));

-- 3. Campaign v2 columns (UI-SPEC §5.5 step 2 + step 6).
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS audience_hints TEXT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS primary_goal VARCHAR(20);
ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS campaigns_primary_goal_check;
ALTER TABLE campaigns ADD CONSTRAINT campaigns_primary_goal_check
    CHECK (primary_goal IS NULL OR primary_goal IN ('book_meeting','qualify','click','engage'));
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS success_criteria TEXT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS webhook_url TEXT;

-- Backfill unified webhook_url from one of 3 split URLs (Pitfall 6: keep 3 legacy cols).
-- On already-migrated DBs with webhook_url already populated this UPDATE is a no-op.
UPDATE campaigns SET webhook_url = COALESCE(
    webhook_url, lead_webhook_url, handoff_webhook_url, finish_webhook_url
) WHERE webhook_url IS NULL
  AND (lead_webhook_url IS NOT NULL OR handoff_webhook_url IS NOT NULL OR finish_webhook_url IS NOT NULL);
-- Do NOT drop lead_/handoff_/finish_webhook_url here — Phase 4 tests rely on them.

COMMIT;
