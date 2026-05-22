-- migrations/016_phase4.sql
-- Phase 4: Campaigns — модель + связи + DROP context_contact_assignments
-- Idempotent (IF NOT EXISTS / IF EXISTS / DROP CONSTRAINT IF EXISTS).
--
-- Overrides from 04-01 AUDIT.md:
--   Q1: message_queue.campaign_id is NULLable + ON DELETE SET NULL
--       (overrides CONTEXT.md D-16 "NOT NULL"). Allows hard delete of `done`
--       campaigns while keeping queue history.
--   Q6: campaigns.status is VARCHAR(20) + CHECK constraint (overrides D-04
--       "SQLEnum") — PostgreSQL `ALTER TYPE ADD VALUE` cannot run inside a
--       transaction block; CHECK constraint can be dropped/re-added idempotently.

BEGIN;

-- ── 1. campaigns table (D-01..D-15, +Q6 VARCHAR+CHECK) ───────────────────────
CREATE TABLE IF NOT EXISTS campaigns (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    agent_id             UUID NOT NULL REFERENCES ai_contexts(id) ON DELETE RESTRICT,
    folder_id            UUID NOT NULL REFERENCES folders(id) ON DELETE RESTRICT,
    name                 VARCHAR(150) NOT NULL,
    description          TEXT,
    status               VARCHAR(20) NOT NULL DEFAULT 'draft',
    timezone             TEXT NOT NULL DEFAULT 'Europe/Moscow',
    work_hour_start      INT NOT NULL DEFAULT 9,
    work_hour_end        INT NOT NULL DEFAULT 20,
    work_days_mask       INT NOT NULL DEFAULT 31,
    start_date           TIMESTAMPTZ,
    stop_date            TIMESTAMPTZ,
    message_template     TEXT NOT NULL DEFAULT '',
    lead_webhook_url     TEXT,
    handoff_webhook_url  TEXT,
    finish_webhook_url   TEXT,
    lead_trigger_hint    TEXT,
    handoff_trigger_hint TEXT,
    finish_trigger_hint  TEXT,
    tools                JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT campaigns_status_check
        CHECK (status IN ('draft','running','paused','done')),
    CONSTRAINT campaigns_work_hours_check
        CHECK (work_hour_start >= 0 AND work_hour_end <= 24 AND work_hour_start < work_hour_end),
    CONSTRAINT campaigns_work_days_check
        CHECK (work_days_mask BETWEEN 1 AND 127)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_workspace_name
    ON campaigns(workspace_id, name);
CREATE INDEX IF NOT EXISTS idx_campaigns_status_running
    ON campaigns(workspace_id, status) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_campaigns_agent_id ON campaigns(agent_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_folder_id ON campaigns(folder_id);

-- ── 2. campaign_senders through-table (D-03) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_senders (
    campaign_id  UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    sender_id    UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campaign_id, sender_id)
);
CREATE INDEX IF NOT EXISTS idx_campaign_senders_sender
    ON campaign_senders(sender_id);
CREATE INDEX IF NOT EXISTS idx_campaign_senders_workspace
    ON campaign_senders(workspace_id);

-- ── 3. campaign_contact_assignments (D-06) — replaces context_contact_assignments
DROP TABLE IF EXISTS context_contact_assignments;

CREATE TABLE IF NOT EXISTS campaign_contact_assignments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    campaign_id   UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    contact_phone VARCHAR(20) NOT NULL,
    sender_id     UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cca_campaign_phone
    ON campaign_contact_assignments(campaign_id, contact_phone);
CREATE INDEX IF NOT EXISTS idx_cca_sender
    ON campaign_contact_assignments(sender_id);
CREATE INDEX IF NOT EXISTS idx_cca_workspace
    ON campaign_contact_assignments(workspace_id);

-- ── 4. conversations.campaign_id (D-05) + status CHECK extension (C-13) ──────
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS campaign_id UUID
        REFERENCES campaigns(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_conversations_campaign_id
    ON conversations(campaign_id) WHERE campaign_id IS NOT NULL;

-- Extend conversations.status CHECK constraint — drop old + add extended.
-- Phase 3 не вводила CHECK constraint на conversations.status — он создаётся
-- здесь "с нуля", но DROP CONSTRAINT IF EXISTS защищает от двойной идемпотентности.
ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active','manual','paused','lead','handoff','finished'));

-- ── 5. message_queue.campaign_id (Q1 override: NULLable + SET NULL) ──────────
ALTER TABLE message_queue
    ADD COLUMN IF NOT EXISTS campaign_id UUID
        REFERENCES campaigns(id) ON DELETE SET NULL;

-- Composite index для эффективных queue-tick'ов (per-campaign filter).
-- C-06: WHERE campaign_id IS NOT NULL — partial для меньшего размера.
CREATE INDEX IF NOT EXISTS idx_message_queue_workspace_campaign_status_scheduled
    ON message_queue(workspace_id, campaign_id, status, scheduled_at)
    WHERE campaign_id IS NOT NULL;

COMMIT;
