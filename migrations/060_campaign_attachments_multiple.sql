-- 260709-dbl: campaign first-message attachment 1-1 → 1-N (multiple ordered files).
-- Phase 24 (migration 054) modeled campaign_attachments as exactly one blob per
-- campaign (campaign_id UNIQUE). Users now want several files (deck + price list +
-- photo) on the opener, delivered as one grouped Telegram album. This migration
-- drops the 1-1 UNIQUE constraint and adds an ordering `position` column so a
-- campaign can hold N attachments.
--
-- Idempotent + fail-fast-safe (see CLAUDE.md auto-applier rules): DROP CONSTRAINT
-- IF EXISTS / ADD COLUMN IF NOT EXISTS / ALTER SET DEFAULT / CREATE INDEX IF NOT
-- EXISTS — every statement re-runnable on drift.

-- Drop the auto-named UNIQUE constraint created by the `campaign_id ... UNIQUE`
-- column in migration 054 (Postgres names an inline column UNIQUE constraint
-- <table>_<column>_key). After this, several rows may share a campaign_id.
ALTER TABLE campaign_attachments DROP CONSTRAINT IF EXISTS campaign_attachments_campaign_id_key;

-- Ordering column: album delivery + duplicate-copy preserve attachment order.
ALTER TABLE campaign_attachments ADD COLUMN IF NOT EXISTS position integer NOT NULL DEFAULT 0;
ALTER TABLE campaign_attachments ALTER COLUMN position SET DEFAULT 0;   -- drift guard, idempotent

-- Fast ordered load by campaign (worker: ORDER BY position).
CREATE INDEX IF NOT EXISTS idx_campaign_attachments_campaign_pos
    ON campaign_attachments(campaign_id, position);
