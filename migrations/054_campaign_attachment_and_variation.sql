-- Phase 24: campaign first-message file attachment (D-01/D-02/D-04) + variation flag (D-13).
-- Idempotent + fail-fast-safe: ADD COLUMN IF NOT EXISTS / CREATE TABLE IF NOT EXISTS /
-- ALTER SET DEFAULT (all re-runnable on drift — see CLAUDE.md auto-applier rules).

-- D-13: per-campaign invisible text-variation toggle, default ON — retro-enables
-- ALL existing campaigns (incl. running; no backfill needed, the DEFAULT does it).
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS variation_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE campaigns ALTER COLUMN variation_enabled SET DEFAULT true;   -- drift guard, idempotent

-- D-01/D-02/D-04: 1-1 attachment blob table (blob kept OUT of SELECT campaigns —
-- worker/endpoint query it by campaign_id directly). campaign_id UNIQUE + CASCADE
-- => exactly one attachment per campaign. Modeled on csv_imports (DB-blob, pg_dump).
CREATE TABLE IF NOT EXISTS campaign_attachments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id uuid NOT NULL UNIQUE REFERENCES campaigns(id) ON DELETE CASCADE,
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    file_data bytea NOT NULL,
    file_name varchar(255) NOT NULL,
    content_type varchar(100),
    size_bytes bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_campaign_attachments_workspace ON campaign_attachments(workspace_id);
